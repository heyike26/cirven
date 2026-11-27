"""Actual EEAS API/SQLite/frozen Task and installed original CLI; only model HTTP is original Mock."""
import hashlib
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import time

import pytest
import httpx
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app
from conftest import ROOT
from opencode_wire_fixture import KEY, MODEL, MARKER, OriginalWire
from packages.adapters.opencode import OpenCodeBridge, decode_event
from packages.adapters.opencode_profile import CLI_SHA, VERSION, COMMIT, binary_hash
from test_task_sdk_api import copy_frozen_source, formal


def record(name, value):
    location = os.environ.get("EEAS_EVIDENCE_DIR")
    if location:
        Path(location, name + ".json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture(scope="session")
def native_cli(tmp_path_factory):
    selected = os.environ.get("EEAS_OPENCODE_TEST_CLI")
    if selected:
        path = Path(selected)
    else:
        # Explicit installed component payload; no download/npm or ambient account/cache discovery.
        archive = ROOT / "toolchain/components/opencode-v1.19.0/upstream/opencode-linux-x64.tar.gz"
        assert os.name == "posix" and archive.is_file(), "Native tests require the acquired component or EEAS_OPENCODE_TEST_CLI"
        path = tmp_path_factory.mktemp("original-opencode") / "opencode"
        with tarfile.open(archive, "r:gz") as source:
            members = [m for m in source.getmembers() if m.isfile() and m.name == "opencode"]
            assert len(members) == 1
            with source.extractfile(members[0]) as raw, path.open("xb") as out:
                while block := raw.read(1024 * 1024): out.write(block)
        path.chmod(0o755)
    assert binary_hash(path) == CLI_SHA[os.name]
    return path


def profile(monkeypatch, tmp_path, native_cli, wire):
    path = tmp_path / "original-opencode-profile.json"
    value = {"schema": "eeas.opencode-pilot-profile.v1", "version": VERSION, "source_commit": COMMIT,
        "executable": str(native_cli), "executable_sha256": CLI_SHA[os.name], "transport_kind": "MOCK_WIRE",
        "provider_url": wire.base_url, "model_id": MODEL}
    path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setenv("EEAS_NATIVE_RUNTIME", "opencode-http")
    monkeypatch.setenv("EEAS_OPENCODE_PROFILE", str(path))
    monkeypatch.setenv("EEAS_OPENCODE_API_KEY", KEY)
    return path, value


def open_client(settings):
    client = TestClient(create_app(settings), base_url=settings.origin)
    client.__enter__()
    boot = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
    client.headers.update({"Origin": settings.origin, "X-EEAS-CSRF": boot.json()["csrf_token"]})
    return client


def until(client, pid, ref, states):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{pid}/native-reviews/{ref}")
        assert response.status_code == 200, response.text
        review = response.json()
        native = review["native_execution"]
        if native and native["execution_status"] in states and review["admission_status"] == native["execution_status"]:
            return review
        time.sleep(.02)
    pytest.fail("Original native review did not settle: " + json.dumps(review))


def evidence(client, pid, ref, wire, name):
    exported = client.get(f"/api/projects/{pid}/native-reviews/{ref}/result").json()
    record(name, {"export": exported, "original_model_http": wire.requests,
        "scope": "ORIGINAL_MOCK_WIRE_NATIVE_CLI_NOT_COMPANY_OR_NATIVE_WINDOWS", "paid_or_company_calls": 0})
    return exported


def test_missing_profile_and_live_cost_route_have_no_admission(tmp_path, monkeypatch):
    monkeypatch.delenv("EEAS_NATIVE_RUNTIME", raising=False)
    monkeypatch.delenv("EEAS_OPENCODE_PROFILE", raising=False)
    client = open_client(Settings(tmp_path / "data"))
    try:
        pid, _doc, _req, _task, _run, route, body = formal(client)
        denied = client.post(route + "/native-reviews", json=body)
        assert denied.status_code == 503 and "OPENCODE_PROFILE_NOT_CONFIGURED" in denied.json()["error"]["reasons"]
        selected = tmp_path / "live-profile.json"
        selected.write_text(json.dumps({"schema": "eeas.opencode-pilot-profile.v1", "version": VERSION, "source_commit": COMMIT,
            "executable": "/not-executed", "executable_sha256": CLI_SHA[os.name], "transport_kind": "LIVE",
            "provider_url": "https://unverified.invalid/v1", "model_id": "unverified"}))
        monkeypatch.setenv("EEAS_OPENCODE_PROFILE", str(selected))
        denied = client.post(route + "/native-reviews", json=body)
        assert denied.status_code == 503 and "OPENCODE_LIVE_COST_ENFORCEMENT_NOT_QUALIFIED" in denied.json()["error"]["reasons"]
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM task_native_reviews").fetchone()[0] == 0
    finally: client.__exit__(None, None, None)


def test_actual_task_review_loop_cursor_immutable_scope_and_restart(tmp_path, monkeypatch, native_cli):
    wire = OriginalWire().start()
    selected, settings = None, Settings(tmp_path / "data")
    profile(monkeypatch, tmp_path, native_cli, wire)
    client = open_client(settings)
    try:
        pid, doc, _req, task, run, route, body = formal(client)
        response = client.post(route + "/native-reviews", json=body)
        assert response.status_code == 202, response.text
        ref = response.json()["admission_id"]
        repeated = client.post(route + "/native-reviews", json=body)
        assert repeated.status_code == 202 and repeated.json()["admission_id"] == ref
        review = until(client, pid, ref, {"RETURNED", "FAILED", "UNKNOWN_OUTCOME"})
        assert review["native_execution"]["execution_status"] == "RETURNED", review
        selected = client.app.state.bridge.selected
        assert not selected.active
        native = review["native_execution"]
        assert native["runtime"] == "opencode-http" and native["sdk_version"] == VERSION
        assert native["verification_status"] == review["verification_status"] == "NOT_RUN"
        assert not review["formal_run_changed"]
        actual_request = json.loads(native["request"])
        assert actual_request["task_binding"] == body["task_binding"]
        assert actual_request["document_refs"] == [{"document_id": doc["id"], "revision": 1, "sha256": doc["sha256"]}]
        exported = evidence(client, pid, ref, wire, "original-task-returned")
        events = exported["native_result"]["events"]
        assert [e["event_seq"] for e in events] == list(range(1, len(events) + 1))
        raw = [e["payload"]["native_payload"] for e in events if e["payload"]["type"] == "OpenCodeEvent"]
        assert any(e["type"] == "session.status" and e["properties"]["status"]["type"] == "idle" for e in raw)
        assert any(p.get("text") == MARKER for m in json.loads(native["result"])["native_messages"] for p in m["parts"])
        assert len(wire.requests) == 1 and not wire.requests[0]["body"].get("tools") and wire.requests[0]["fixture_auth_matches"]
        # Upload returns metadata. Read the actual pinned original through its document API.
        source = client.get(f"/api/projects/{pid}/documents/{doc['id']}?revision=1").json()
        texts = []
        for message in wire.requests[0]["body"]["messages"]:
            if message["role"] != "user": continue
            content = message["content"]
            if isinstance(content, str): texts.append(content)
            else: texts.extend(part["text"] for part in content if part["type"] == "text")
        assert actual_request["prompt"] in "\n".join(texts) and run["id"] in "\n".join(texts)
        captured = json.loads(actual_request["prompt"].split("\n", 1)[1].rsplit("\nSupplementary readonly review instruction:", 1)[0])
        assert source["content"] in captured["documents"].values()
        assert hashlib.sha256(source["content"].encode()).hexdigest() == doc["sha256"] == source["sha256"]
        assert client.get(f"/api/projects/{pid}/native-reviews/{ref}/events?after={events[-1]['event_seq']}").json()["events"] == []
        assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
        control = client.post(f"/api/projects/{pid}/native-reviews/{ref}/resume", json={"operation_id": "unsupported-resume",
            "expected_session_id": native["session_id"], "prompt": "No new budget or runtime"})
        assert control.status_code == 503
        assert len(wire.requests) == 1
        with client.app.state.store.connect() as db:
            scope = json.loads(db.execute("SELECT scope FROM agent_scopes WHERE execution_id=?", (native["id"],)).fetchone()[0])
            assert scope["runtime"] == "opencode-http" and scope["cli_sha256"] == CLI_SHA[os.name] and scope["root_budget_usd"] is None
            assert db.execute("SELECT count(*) FROM runner_jobs").fetchone()[0] == 0
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute("UPDATE agent_executions SET runtime='claude-agent-sdk' WHERE id=?", (native["id"],))
    finally:
        client.__exit__(None, None, None)
    reopened = open_client(settings)
    try:
        restored = reopened.get(f"/api/projects/{pid}/native-reviews/{ref}/result").json()
        assert restored["native_result"] == exported["native_result"]
        assert restored["frozen_scope"] == exported["frozen_scope"]
        assert len(wire.requests) == 1
        assert not reopened.app.state.bridge.selected.active
    finally:
        reopened.__exit__(None, None, None); wire.close()


def test_actual_native_abort_requires_observed_aborted_message(tmp_path, monkeypatch, native_cli):
    wire = OriginalWire("hold").start()
    profile(monkeypatch, tmp_path, native_cli, wire)
    client = open_client(Settings(tmp_path / "data"))
    try:
        pid, _doc, _req, _task, run, route, body = formal(client)
        response = client.post(route + "/native-reviews", json=body)
        assert response.status_code == 202, response.text
        ref = response.json()["admission_id"]
        assert wire.received.wait(20), "Original model HTTP not received"
        asked = client.post(f"/api/projects/{pid}/native-reviews/{ref}/cancel")
        assert asked.status_code == 200
        result = until(client, pid, ref, {"CANCELLED", "FAILED", "UNKNOWN_OUTCOME", "RETURNED"})
        exported = evidence(client, pid, ref, wire, "original-task-cancelled")
        assert result["native_execution"]["execution_status"] == "CANCELLED", exported
        final = json.loads(result["native_execution"]["result"])
        assert final["abort_acknowledged"] and final["idle_observed"]
        assert any(m["info"].get("error", {}).get("name") == "MessageAbortedError" for m in final["native_messages"])
        assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
        assert len(wire.requests) == 1 and not client.app.state.bridge.selected.active
    finally:
        client.__exit__(None, None, None); wire.close()


def test_actual_native_process_loss_is_unknown_and_not_replayed(tmp_path, monkeypatch, native_cli):
    wire = OriginalWire("hold").start()
    profile(monkeypatch, tmp_path, native_cli, wire)
    settings = Settings(tmp_path / "data")
    client = open_client(settings)
    try:
        pid, _doc, _req, _task, run, route, body = formal(client)
        response = client.post(route + "/native-reviews", json=body)
        assert response.status_code == 202, response.text
        ref = response.json()["admission_id"]
        assert wire.received.wait(20)
        native = client.get(f"/api/projects/{pid}/native-reviews/{ref}").json()["native_execution"]
        context = client.app.state.bridge.selected.active[native["id"]]
        context["process"].kill()  # Actual owned CLI process loss, not an HTTP/bridge mock.
        result = until(client, pid, ref, {"UNKNOWN_OUTCOME", "FAILED", "RETURNED", "CANCELLED"})
        exported = evidence(client, pid, ref, wire, "original-task-process-loss")
        assert result["native_execution"]["execution_status"] == "UNKNOWN_OUTCOME", exported
        assert not result["native_execution"]["result"]
        assert not client.app.state.bridge.selected.active
        assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
    finally: client.__exit__(None, None, None)
    reopened = open_client(settings)
    try:
        restored = reopened.get(f"/api/projects/{pid}/native-reviews/{ref}").json()
        assert restored["native_execution"]["execution_status"] == "UNKNOWN_OUTCOME"
        assert len(wire.requests) == 1 and not reopened.app.state.bridge.selected.active
    finally:
        reopened.__exit__(None, None, None); wire.close()


def test_actual_nonretryable_model_error_is_failed_without_fake_result(tmp_path, monkeypatch, native_cli):
    wire = OriginalWire("error").start()
    profile(monkeypatch, tmp_path, native_cli, wire)
    client = open_client(Settings(tmp_path / "data"))
    try:
        pid, _doc, _req, _task, run, route, body = formal(client)
        response = client.post(route + "/native-reviews", json=body)
        assert response.status_code == 202, response.text
        ref = response.json()["admission_id"]
        result = until(client, pid, ref, {"FAILED", "UNKNOWN_OUTCOME", "RETURNED"})
        exported = evidence(client, pid, ref, wire, "original-task-model-error")
        assert result["native_execution"]["execution_status"] == "FAILED", exported
        assert len(wire.requests) == 1
        final = json.loads(result["native_execution"]["result"])
        assert any(m["info"].get("error") for m in final["native_messages"])
        assert not any(p.get("text") == MARKER for m in final["native_messages"] for p in m["parts"])
        assert final["commercial_cost_usd"] is None
        assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
        assert not client.app.state.bridge.selected.active
    finally:
        client.__exit__(None, None, None); wire.close()


def test_actual_native_credential_echo_is_redacted_with_original_raw_hash_retained(tmp_path, monkeypatch, native_cli):
    wire = OriginalWire("echo_key").start()
    profile(monkeypatch, tmp_path, native_cli, wire)
    client = open_client(Settings(tmp_path / "data"))
    try:
        pid, _doc, _req, _task, run, route, body = formal(client)
        response = client.post(route + "/native-reviews", json=body)
        assert response.status_code == 202, response.text
        ref = response.json()["admission_id"]
        result = until(client, pid, ref, {"FAILED", "UNKNOWN_OUTCOME", "RETURNED"})
        exported = evidence(client, pid, ref, wire, "original-task-redaction")
        assert result["native_execution"]["execution_status"] == "RETURNED", exported
        assert KEY not in json.dumps(exported, ensure_ascii=False)
        native = result["native_execution"]
        public = json.loads(native["result"])
        assert public["credential_redacted"]
        raw = Path(native["workspace"], "native-result.json").read_bytes()
        assert KEY.encode() in raw and hashlib.sha256(raw).hexdigest() == public["native_result_sha256"]
        assert len(wire.requests) == 1
        assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
        assert not client.app.state.bridge.selected.active
    finally:
        client.__exit__(None, None, None); wire.close()


@pytest.mark.parametrize("change", ["source", "stack"])
def test_physical_source_or_stack_drift_is_rejected_before_native_dispatch(tmp_path, monkeypatch, native_cli, change):
    wire = OriginalWire().start()
    profile(monkeypatch, tmp_path, native_cli, wire)
    client = open_client(Settings(tmp_path / "data"))
    try:
        _pid, _doc, _req, _task, _run, route, body = formal(client)
        copied = tmp_path / "owned-source-copy"
        copy_frozen_source(copied, _task["spec"])
        target = copied / ("apps/web/index.html" if change == "source" else "profiles/stack.lock.json")
        target.write_bytes(target.read_bytes() + b"\n")
        client.app.state.store.source_root = copied
        denied = client.post(route + "/native-reviews", json=body)
        assert denied.status_code == 409, denied.text
        assert wire.requests == []
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM task_native_reviews").fetchone()[0] == 0
    finally:
        client.__exit__(None, None, None); wire.close()


@pytest.mark.parametrize("change", ["document", "task", "binding", "cross_project", "budget", "native", "tools", "search", "auth", "profile", "cli_sha"])
def test_changed_scope_or_unsupported_capability_rejected_before_native_side_effect(tmp_path, monkeypatch, native_cli, change):
    wire = OriginalWire().start()
    selected, value = profile(monkeypatch, tmp_path, native_cli, wire)
    client = open_client(Settings(tmp_path / "data"))
    try:
        pid, doc, _req, task, _run, route, body = formal(client)
        if change == "document":
            assert client.put(f"/api/projects/{pid}/documents/{doc['id']}", files={"file": ("original.md", b"changed original")}, data={"expected_revision": 1}).status_code == 200
        elif change == "task":
            assert client.put(f"/api/projects/{pid}/tasks/{task['id']}?expected_revision=1", json={"goal": "changed original", "requirement_refs": task["spec"]["requirement_refs"]}).status_code == 200
        elif change == "binding": body["task_binding"]["task_revision"] += 1
        elif change == "cross_project":
            from test_documents import project
            route = route.replace(pid, project(client, "other original project"))
        elif change == "budget": body["max_turns"] = task["spec"]["budget"]["max_steps"] + 1
        elif change == "native": body["native"] = {"env": {"UNAPPROVED": "value"}}
        elif change == "tools": body["needs_tools"] = True
        elif change == "search": body["search_enabled"] = True
        elif change == "auth": monkeypatch.setenv("EEAS_OPENCODE_API_KEY", "")
        elif change == "profile":
            value["transport_kind"] = "LIVE"; selected.write_text(json.dumps(value))
        else:
            value["executable_sha256"] = "0" * 64; selected.write_text(json.dumps(value))
        denied = client.post(route + "/native-reviews", json=body)
        assert denied.status_code in {404, 409, 422, 503}, denied.text
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM task_native_reviews").fetchone()[0] == 0
        assert wire.requests == [] and not client.app.state.bridge.selected.active
    finally:
        client.__exit__(None, None, None); wire.close()


def test_imported_original_event_samples_parse_without_invented_replay_or_success():
    source = ROOT / "tests/fixtures/opencode-v1.19.0"
    manifest = json.loads((source / "source-manifest.json").read_text())
    assert manifest["version"] == VERSION and manifest["source_commit"] == COMMIT
    for ref in manifest["files"]:
        raw = (source / ref["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"] and len(raw) == ref["byte_length"]
    path = source / "original-native-events.json"
    events = json.loads(path.read_text())
    assert len(events) == 70
    for event in events:
        raw = json.dumps(event, ensure_ascii=False).encode()
        assert decode_event(raw) == event
    assert any(e["type"] == "session.status" for e in events)
    for raw in (b'{"type":"native","properties":{"cost":NaN}}', b'[]', b'{"type":"native","properties":null}'):
        with pytest.raises(ValueError): decode_event(raw)


def test_parser_fifo_retains_identical_no_id_frames_and_deduplicates_only_explicit_id(client, tmp_path, monkeypatch, native_cli):
    wire = OriginalWire().start()
    profile(monkeypatch, tmp_path, native_cli, wire)
    pid, _doc, _req, _task, run, _route, body = formal(client)
    bridge = OpenCodeBridge(client.app.state.store, tmp_path)
    frozen = client.app.state.native_reviews.frozen_scope(pid, body["task_binding"]["task_id"], run["id"])
    request = client.app.state.native_reviews._request(frozen, body)
    workspace = tmp_path / "original-parser-only"; workspace.mkdir()
    row, created = bridge._record(pid, "original-parser-counterexample", request, bridge._scope(pid, request), workspace)
    assert created
    delta = {"type": "message.part.delta", "properties": {"sessionID": "original-parser-source", "delta": "same text"}}
    identified = {**delta, "id": "original-explicit-frame-id"}
    frames = [delta, delta, identified, identified]

    class OriginalFrames(httpx.AsyncByteStream):
        async def __aiter__(self):
            for event in frames:
                yield ("data: " + json.dumps(event) + "\n\n").encode()

    async def actual_parser():
        context = {"workspace": workspace, "event_queue": asyncio.Queue(maxsize=32), "secrets": [], "seen": {},
            "connected": asyncio.Event(), "done": asyncio.Event(), "busy": False, "idle_observed": False}
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=OriginalFrames()))
        async with httpx.AsyncClient(transport=transport, base_url="http://original-parser.invalid") as parser_client:
            context["client"] = parser_client
            writer = asyncio.create_task(bridge._write_events(context, row["id"]))
            await bridge._listen(context, row["id"])
            await context["event_queue"].join()
            writer.cancel(); await asyncio.gather(writer, return_exceptions=True)
        assert context["protocol_error"] == "ConnectionError"  # No EOF-as-success or fabricated native session.

    try:
        asyncio.run(actual_parser())
        events = bridge.stream_events(pid, row["id"])
        assert [e["event_seq"] for e in events] == [1, 2, 3]
        assert [e["payload"]["native_payload"] for e in events] == [delta, delta, identified]
        assert len((workspace / "native-events.jsonl").read_bytes().splitlines()) == 4
        bridge._terminal(row["id"], "UNKNOWN_OUTCOME")
        exported = bridge.export_result(pid, row["id"])
        assert exported["execution"]["session_id"] is None and not exported["native_result_recorded"]
        assert not wire.requests
        record("original-no-id-parser-counterexample", {"scope": "SCRIPTED_SSE_PARSER_AND_REAL_SQLITE_ONLY_NOT_NATIVE_CLI_QUALIFICATION",
            "received_frames": frames, "stored_events": events, "raw_frame_count": 4, "execution_status": "UNKNOWN_OUTCOME"})
    finally: wire.close()
