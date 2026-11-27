"""Real source/asset persistence and installed SDK; only external model wire is original."""
import asyncio
import base64
import hashlib
import json
import os
from unittest.mock import patch

from claude_agent_sdk import ClaudeSDKClient
import pytest

from packages.adapters.claude_sdk import LiveBlocked
from packages.domain.store import Conflict, NotFound
from test_claude_sessions import FIXTURE_ENV, NativeFactory, QueuedWire, context
from test_documents import project, upload
from test_image_documents import original_image


def image_context(client, tmp_path, format="PNG"):
    pid, bridge, request = context(client, tmp_path)
    raw = original_image(format)
    document = upload(client, pid, raw, "原创图.png" if format == "PNG" else "原创图.jpg").json()
    asset = bridge.service.document_assets(pid, document["id"], document["revision"])[0]
    request["document_refs"] = [{"document_id": document["id"], "revision": document["revision"], "sha256": document["sha256"]}]
    request["image_refs"] = [{"document_id": document["id"], "revision": document["revision"],
                              "asset_id": asset["asset_id"], "sha256": asset["sha256"]}]
    return pid, bridge, request, document, asset, raw


def environment(monkeypatch, enabled=True):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv("EEAS_MODEL_VISION_ENABLED", "1" if enabled else "0")
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("EEAS_MODEL_MAX_IMAGES", raising=False)
    monkeypatch.delenv("EEAS_MODEL_MAX_IMAGE_PAYLOAD_BYTES", raising=False)


def no_native_rows(bridge):
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_actual_source_images_reach_native_async_iterable_content_blocks(client, tmp_path, monkeypatch, format, mime):
    environment(monkeypatch)
    pid, bridge, request, document, asset, raw = image_context(client, tmp_path, format)
    factory = NativeFactory([0.002])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        row = asyncio.run(bridge.submit(pid, "original-vision-input", request))
    assert row["execution_status"] == "RETURNED"
    users = [message for message in factory.transports[0].writes if message["type"] == "user"]
    assert len(users) == 1 and users[0]["message"]["role"] == "user"
    content = users[0]["message"]["content"]
    assert content[0] == {"type": "text", "text": request["prompt"]}
    assert content[1]["type"] == "image" and content[1]["source"]["type"] == "base64"
    assert content[1]["source"]["media_type"] == mime
    assert base64.b64decode(content[1]["source"]["data"], validate=True) == raw
    assert hashlib.sha256(raw).hexdigest() == asset["sha256"] == document["sha256"]
    assert len(json.dumps(users[0]).encode()) < 4 * 1024 * 1024
    assert factory.transports[0].options.tools == [] and factory.transports[0].options.max_budget_usd == 0.01
    exported = bridge.export_result(pid, row["id"])
    assert not exported["live_evidence"] and exported["verification_status"] == "NOT_RUN"
    assert content[1]["source"]["data"] not in json.dumps(exported)
    assert "raw_bytes" not in row["request"]


def test_default_text_profile_never_sends_images_without_explicit_vision_attempt(client, tmp_path, monkeypatch):
    environment(monkeypatch, False)
    pid, bridge, request, *_ = image_context(client, tmp_path)
    with pytest.raises(LiveBlocked, match="MODEL_VISION_NOT_ENABLED"):
        asyncio.run(bridge.submit(pid, "vision-not-approved", request))
    no_native_rows(bridge)
    capability = bridge.describe_capabilities()["image_input"]
    assert not capability["enabled_for_attempt"] and capability["model_compatibility"] == "NOT_VALIDATED"


@pytest.mark.parametrize("mutation", ["missing_document", "wrong_document_hash", "wrong_asset_hash", "missing_asset",
                                      "duplicate", "url", "base64", "revision_bool"])
def test_unfrozen_or_arbitrary_image_inputs_never_create_native_execution(client, tmp_path, monkeypatch, mutation):
    environment(monkeypatch)
    pid, bridge, request, *_ = image_context(client, tmp_path)
    if mutation == "missing_document": request.pop("document_refs")
    if mutation == "wrong_document_hash": request["document_refs"][0]["sha256"] = "0" * 64
    if mutation == "wrong_asset_hash": request["image_refs"][0]["sha256"] = "0" * 64
    if mutation == "missing_asset": request["image_refs"][0]["asset_id"] = "f" * 64
    if mutation == "duplicate": request["image_refs"].append(dict(request["image_refs"][0]))
    if mutation == "url": request["image_refs"][0]["url"] = "https://unapproved.example.invalid/image.png"
    if mutation == "base64": request["image_refs"][0]["data"] = "dW5hcHByb3ZlZA=="
    if mutation == "revision_bool": request["image_refs"][0]["revision"] = True
    with pytest.raises((ValueError, Conflict, NotFound)):
        asyncio.run(bridge.submit(pid, "bad-image-reference", request))
    no_native_rows(bridge)


@pytest.mark.parametrize("mutation", ["cross_project", "superseded", "deleted", "blob_tamper", "metadata_tamper"])
def test_real_database_source_and_asset_integrity_guards(client, tmp_path, monkeypatch, mutation):
    environment(monkeypatch)
    pid, bridge, request, document, asset, raw = image_context(client, tmp_path)
    if mutation == "cross_project": pid = project(client, "other original tenant")
    if mutation == "superseded":
        assert client.put(f"/api/projects/{pid}/documents/{document['id']}",
                          files={"file": ("replacement.txt", b"new actual version")}, data={"expected_revision": 1}).status_code == 200
    if mutation == "deleted": assert client.delete(f"/api/projects/{pid}/documents/{document['id']}").status_code == 200
    if mutation in {"blob_tamper", "metadata_tamper"}:
        with bridge.service.connect() as connection:
            connection.execute("DROP TRIGGER document_assets_no_update")
            if mutation == "blob_tamper":
                connection.execute("UPDATE document_assets SET raw_bytes=? WHERE asset_id=?", (raw[:-1] + b"x", asset["asset_id"]))
            else:
                connection.execute("UPDATE document_assets SET width=width+1 WHERE asset_id=?", (asset["asset_id"],))
    with pytest.raises((Conflict, NotFound)):
        asyncio.run(bridge.submit(pid, "stale-image-source", request))
    no_native_rows(bridge)


@pytest.mark.parametrize("policy", ["count", "payload", "invalid_count", "invalid_payload"])
def test_count_and_entire_native_json_payload_limits_are_bounded(client, tmp_path, monkeypatch, policy):
    environment(monkeypatch)
    pid, bridge, request, document, asset, raw = image_context(client, tmp_path)
    if policy == "count":
        second = upload(client, pid, original_image("JPEG"), "second.jpg").json()
        next_asset = bridge.service.document_assets(pid, second["id"], 1)[0]
        request["document_refs"].append({"document_id": second["id"], "revision": 1, "sha256": second["sha256"]})
        request["image_refs"].append({"document_id": second["id"], "revision": 1, "asset_id": next_asset["asset_id"], "sha256": next_asset["sha256"]})
        monkeypatch.setenv("EEAS_MODEL_MAX_IMAGES", "1")
    if policy == "payload":
        monkeypatch.setenv("EEAS_MODEL_MAX_IMAGE_PAYLOAD_BYTES", "1024")
        request["prompt"] = "original visible text " * 300
    if policy == "invalid_count": monkeypatch.setenv("EEAS_MODEL_MAX_IMAGES", "100000")
    if policy == "invalid_payload": monkeypatch.setenv("EEAS_MODEL_MAX_IMAGE_PAYLOAD_BYTES", "99999999999")
    with pytest.raises((ValueError, LiveBlocked)):
        asyncio.run(bridge.submit(pid, "vision-size-policy", request))
    no_native_rows(bridge)


def test_chinese_taskbook_json_encoding_is_counted_with_images_without_truncation(client, tmp_path, monkeypatch):
    environment(monkeypatch)
    pid, bridge, request, *_ = image_context(client, tmp_path)
    request["prompt"] = "原" * 70000
    monkeypatch.setenv("EEAS_MODEL_MAX_IMAGE_PAYLOAD_BYTES", "400000")
    assert len(request["prompt"].encode("utf-8")) == 210000
    with pytest.raises(ValueError, match="native JSON payload"):
        asyncio.run(bridge.submit(pid, "chinese-json-frame-bound", request))
    assert len(request["prompt"]) == 70000
    no_native_rows(bridge)


@pytest.mark.parametrize("kind", ["resume", "fork"])
def test_native_image_continuations_keep_sources_and_shared_budget(client, tmp_path, monkeypatch, kind):
    environment(monkeypatch)
    pid, bridge, request, document, asset, raw = image_context(client, tmp_path)
    factory = NativeFactory([0.002, 0.004])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "vision-root", request))
        before = bridge.stream_events(pid, parent["id"])
        child = asyncio.run(getattr(bridge, kind)(pid, parent["id"], "vision-followup", "A new original image question",
                                               expected_session_id=parent["session_id"]))
    assert child["execution_status"] == "RETURNED" and factory.transports[1].options.max_budget_usd == 0.008
    assert json.loads(child["request"])["image_refs"] == request["image_refs"]
    assert json.loads(child["request"])["document_refs"] == request["document_refs"]
    assert bridge.stream_events(pid, parent["id"]) == before and bridge.inspect(pid, parent["id"]) == parent
    user = next(message for message in factory.transports[1].writes if message["type"] == "user")
    assert user["message"]["content"][0]["text"] == "A new original image question"
    assert base64.b64decode(user["message"]["content"][1]["source"]["data"]) == raw


@pytest.mark.parametrize("mutation", ["vision_disabled", "limits_changed", "source_changed"])
def test_image_policy_or_current_source_drift_refuses_resume_without_overwriting_parent(client, tmp_path, monkeypatch, mutation):
    environment(monkeypatch)
    pid, bridge, request, document, asset, raw = image_context(client, tmp_path)
    factory = NativeFactory([0.002])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "vision-current-root", request))
        if mutation == "vision_disabled": monkeypatch.setenv("EEAS_MODEL_VISION_ENABLED", "0")
        if mutation == "limits_changed": monkeypatch.setenv("EEAS_MODEL_MAX_IMAGES", "5")
        if mutation == "source_changed":
            assert client.put(f"/api/projects/{pid}/documents/{document['id']}", files={"file": ("new.txt", b"new text")},
                              data={"expected_revision": 1}).status_code == 200
        with pytest.raises((LiveBlocked, Conflict)):
            asyncio.run(bridge.resume(pid, parent["id"], "vision-stale-followup", "Original followup", expected_session_id=parent["session_id"]))
    assert len(factory.transports) == 1 and bridge.inspect(pid, parent["id"]) == parent


def test_active_image_followup_uses_one_real_native_owner_and_immutable_inputs(client, tmp_path, monkeypatch):
    environment(monkeypatch)
    pid, bridge, request, document, asset, raw = image_context(client, tmp_path)
    wires = []
    def installed(options):
        wire = QueuedWire(options)
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    async def run():
        active = asyncio.create_task(bridge.submit(pid, "vision-active", request))
        while not wires: await asyncio.sleep(0)
        await wires[0].prompt_received.wait()
        parent = None
        for _ in range(100):
            with bridge.service.connect() as connection:
                parent = dict(connection.execute("SELECT * FROM agent_executions WHERE operation_id='vision-active'").fetchone())
            if parent["session_id"]: break
            await asyncio.sleep(0.01)
        queued = await bridge.steer(pid, parent["id"], "vision-steer", "An original queued image question", expected_session_id=parent["session_id"])
        completed = await active
        return completed, bridge.inspect(pid, queued["id"])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed):
        parent, child = asyncio.run(run())
    assert parent["execution_status"] == child["execution_status"] == "RETURNED" and len(wires) == 1
    assert len(wires[0].prompts) == 2 and wires[0].options.max_budget_usd == 0.01
    assert all(base64.b64decode(message["message"]["content"][1]["source"]["data"]) == raw for message in wires[0].prompts)
    assert json.loads(child["request"])["image_refs"] == request["image_refs"]
