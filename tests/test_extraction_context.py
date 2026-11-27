"""Real API, stored sources and installed SDK; only external model wire is synthetic."""
import base64
import copy
import json

from claude_agent_sdk import ClaudeSDKClient
import pytest

from test_claude_native_transport import OriginalJSONTransport
from test_documents import project, upload
from test_image_documents import original_image
from test_sdk_images import environment


@pytest.fixture(autouse=True)
def selected_runtime(monkeypatch):
    # The live extraction cases supply an installed Claude SDK transport fixture.
    monkeypatch.setenv("EEAS_NATIVE_RUNTIME", "claude-agent-sdk")


def bundle(document, first, last=None):
    return {"schema_version": "eeas.requirements.v1", "title": "原创来源建议",
            "requirements": [{"id": "R1", "text": "保留尾端条件", "source_refs": [{
                "document_id": document["id"], "revision": document["revision"],
                "sha256": document["sha256"], "line_start": first, "line_end": last or first}]}],
            "acceptance": [{"id": "A1", "text": "可核对原始来源", "requirement_ids": ["R1"]}],
            "confirmed": True, "origin": "manual"}


def installed_wire(monkeypatch, output, enabled=False):
    environment(monkeypatch, enabled)
    transports = []

    def factory(options):
        wire = OriginalJSONTransport(structured_output=copy.deepcopy(output), total_cost_usd=0.002)
        transports.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)

    monkeypatch.setattr("packages.adapters.claude_sdk.ClaudeSDKClient", factory)
    return transports


def extract(client, pid, document, **options):
    return client.post(f"/api/projects/{pid}/planning/extract", json={
        "document_id": document["id"], "revision": document["revision"], **options})


def context(client, pid, document):
    response = client.post(f"/api/projects/{pid}/documents/{document['id']}/context", json={"revision": 1})
    assert response.status_code == 200, response.text
    return response.json()


def test_mock_selected_tail_keeps_absolute_lines_and_full_original(client):
    pid = project(client)
    text = "工程需求采集与边界条件\n" * 6000 + "REQ: preserve tail\nACCEPT: original source matches\n"
    assert len(text) > 70000
    doc = upload(client, pid, text.encode()).json()
    last = context(client, pid, doc)["total_chunks"] - 1
    response = extract(client, pid, doc, chunk_indexes=[last])
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["coverage"] == "PARTIAL_EXPLICIT_SELECTION"
    assert answer["proposal"]["requirements"][0]["source_refs"][0]["line_start"] == 6001
    assert not answer["proposal"]["confirmed"] and not answer["model_verified"]
    assert client.get(f"/api/projects/{pid}/documents/{doc['id']}").json()["content"] == text
    assert client.get(f"/api/projects/{pid}/requirements").json()["requirements"] == []


def test_mock_cannot_turn_a_selected_midline_fragment_into_a_requirement(client):
    pid = project(client)
    text = "x" * 4000 + "REQ: fake new line\nACCEPT: original source\n"
    doc = upload(client, pid, text.encode()).json()
    parts = context(client, pid, doc)["chunks"]
    assert parts[1]["content"].startswith("REQ:") and parts[1]["line_start"] == 1
    assert extract(client, pid, doc, chunk_indexes=[1]).status_code == 422


def test_native_full_70000_chinese_and_selected_tail_have_separate_idempotent_scope(client, monkeypatch):
    pid = project(client)
    text = "工程需求采集与边界条件\n" * 6000 + "任务书尾端条件\n"
    doc = upload(client, pid, text.encode()).json()
    assert len(text) > 70000
    wires = installed_wire(monkeypatch, bundle(doc, 6001))
    full = extract(client, pid, doc, mode="live")
    assert full.status_code == 200, full.text
    answer = full.json()
    assert answer["coverage"] == "COMPLETE" and not answer["proposal"]["confirmed"]
    assert answer["proposal"]["origin"] == "live_suggestion" and not answer["model_verified"]
    full_context = context(client, pid, doc)
    execution = client.get(f"/api/projects/{pid}/agent-executions/{answer['execution_id']}").json()
    request = json.loads(execution["request"])
    assert "任务书尾端条件" in request["prompt"] and len(request["prompt"].encode()) < 256 * 1024
    supplied = json.loads(request["prompt"].split("\n", 1)[1])
    assert "".join(c["content"] for c in supplied["context"]["chunks"]) == text
    assert "raw_bytes" not in supplied and "graph" not in supplied
    assert execution["verification_status"] == "NOT_RUN"
    repeated = extract(client, pid, doc, mode="live").json()
    assert repeated["execution_id"] == answer["execution_id"] and len(wires) == 1
    selected = extract(client, pid, doc, mode="live", chunk_indexes=[full_context["total_chunks"] - 1])
    assert selected.status_code == 200, selected.text
    assert selected.json()["execution_id"] != answer["execution_id"] and len(wires) == 2
    assert selected.json()["coverage"] == "PARTIAL_EXPLICIT_SELECTION"
    assert selected.json()["context_sha256"] != answer["context_sha256"]
    assert client.get(f"/api/projects/{pid}/requirements").json()["requirements"] == []


@pytest.mark.parametrize("citation", ["omitted", "gap", "partial_line"])
def test_native_cannot_cite_omitted_or_incomplete_physical_lines(client, monkeypatch, citation):
    pid = project(client)
    text = "source line\n" * 1000 if citation != "partial_line" else "x" * 9000 + "\n"
    doc = upload(client, pid, text.encode()).json()
    parts = context(client, pid, doc)["chunks"]
    indexes = [len(parts) - 1] if citation != "gap" else [0, len(parts) - 1]
    first = 1
    last = 1000 if citation == "gap" else first
    wires = installed_wire(monkeypatch, bundle(doc, first, last))
    response = extract(client, pid, doc, mode="live", chunk_indexes=indexes)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MODEL_PROPOSAL_INVALID"
    assert len(wires) == 1 and wires[0].closed
    assert client.get(f"/api/projects/{pid}/requirements").json()["requirements"] == []


def test_oversized_extraction_rejects_without_truncation_or_native_side_effects(client, monkeypatch):
    pid = project(client)
    text = "工程需求采集与边界条件\n" * 10000 + "最终关键条件\n"
    doc = upload(client, pid, text.encode()).json()
    wires = installed_wire(monkeypatch, bundle(doc, 10001))
    denied = extract(client, pid, doc, mode="live")
    assert denied.status_code == 422 and "No text was truncated" in denied.json()["error"]["message"]
    assert not wires
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
    chunks = client.get(f"/api/projects/{pid}/documents/{doc['id']}/chunks").json()
    admitted = extract(client, pid, doc, mode="live", chunk_indexes=[chunks["total_chunks"] - 1])
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["coverage"] == "PARTIAL_EXPLICIT_SELECTION"
    assert client.get(f"/api/projects/{pid}/documents/{doc['id']}").json()["content"] == text


def test_explicit_image_extraction_uses_original_asset_in_native_sdk_block(client, monkeypatch):
    pid = project(client)
    raw = original_image()
    doc = upload(client, pid, raw, "原创图片.png").json()
    asset = client.get(f"/api/projects/{pid}/documents/{doc['id']}/assets").json()["assets"][0]
    wires = installed_wire(monkeypatch, bundle(doc, 1), enabled=True)
    response = extract(client, pid, doc, mode="live", include_images=True, image_asset_ids=[asset["asset_id"]])
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["image_refs"] == [{"document_id": doc["id"], "revision": 1,
                                    "asset_id": asset["asset_id"], "sha256": asset["sha256"]}]
    user = [message for message in wires[0].writes if message["type"] == "user"]
    assert len(user) == 1
    blocks = user[0]["message"]["content"]
    assert blocks[0]["type"] == "text" and blocks[1]["type"] == "image"
    assert base64.b64decode(blocks[1]["source"]["data"], validate=True) == raw
    assert not answer["proposal"]["confirmed"] and not answer["model_verified"]


@pytest.mark.parametrize("case", ["not_explicit", "mock", "missing", "duplicate", "empty", "disabled", "boolean"])
def test_image_selection_errors_have_no_native_side_effects(client, monkeypatch, case):
    pid = project(client)
    doc = upload(client, pid, original_image(), "original.png").json()
    asset = client.get(f"/api/projects/{pid}/documents/{doc['id']}/assets").json()["assets"][0]
    wires = installed_wire(monkeypatch, bundle(doc, 1), enabled=case != "disabled")
    options = {"mode": "live", "include_images": True, "image_asset_ids": [asset["asset_id"]]}
    if case == "not_explicit": options["include_images"] = False
    if case == "mock": options["mode"] = "mock"
    if case == "missing": options["image_asset_ids"] = ["not-from-document"]
    if case == "duplicate": options["image_asset_ids"] *= 2
    if case == "empty": options["image_asset_ids"] = []
    if case == "boolean": options["include_images"] = 1
    response = extract(client, pid, doc, **options)
    assert response.status_code == (503 if case == "disabled" else 422), response.text
    assert not wires
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
