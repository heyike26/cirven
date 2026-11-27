import json

from test_documents import project, upload
from test_diagram_documents import manual_requirement
from test_task_sdk_api import configured, until_review
from test_claude_sessions import NativeFactory


def test_70000_chinese_context_full_or_explicit_partial_with_absolute_sources(client):
    pid = project(client)
    content = "工程需求采集与边界条件\n" * 6000 + "任务书末尾关键条件\n"
    assert len(content) > 70000
    doc = upload(client, pid, content.encode()).json()
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    response = client.post(path + "/context", json={"revision": 1})
    assert response.status_code == 200, response.text
    full = response.json()
    assert full["coverage"] == "COMPLETE" and full["truncated"] is False
    assert full["total_characters"] == len(content)
    assert "".join(p["content"] for p in full["chunks"]) == content
    last = full["total_chunks"] - 1
    partial = client.post(path + "/context", json={"revision": 1, "chunk_indexes": [last]}).json()
    assert partial["coverage"] == "PARTIAL_EXPLICIT_SELECTION"
    assert partial["omitted_chunk_count"] == last
    assert partial["included_chunks"] == [last] and "末尾关键条件" in partial["chunks"][0]["content"]
    assert partial["chunks"][0]["line_start"] > 1
    assert partial["sha256"] == doc["sha256"] and partial["context_sha256"] != full["context_sha256"]
    for data in ({"revision": 1, "char_budget": 10}, {"revision": 1, "chunk_indexes": [1, 1]},
                 {"revision": 1, "chunk_indexes": [True]}, {"revision": 1, "chunk_indexes": [999]},
                 {"revision": 1, "byte_budget": 1}):
        assert client.post(path + "/context", json=data).status_code == 422
    other = project(client, "另一个项目")
    assert client.post(path.replace(pid, other) + "/context", json={"revision": 1}).status_code == 404
    assert client.put(path, files={"file": ("original.md", b"changed")}, data={"expected_revision": 1}).status_code == 200
    assert client.post(path + "/context", json={"revision": 1, "chunk_indexes": [last]}).json()["context_sha256"] == partial["context_sha256"]
    # The original failed test generated 84k characters, exceeding the declared
    # 220000 UTF-8 byte budget. Keep that boundary explicit instead of raising it.
    too_large = upload(client, pid, ("工程需求采集与边界条件\n" * 7000).encode()).json()
    denied = client.post(f"/api/projects/{pid}/documents/{too_large['id']}/context", json={"revision": 1})
    assert denied.status_code == 422 and "No text was truncated" in denied.json()["error"]["message"]


def test_native_large_document_explicit_tail_selection_real_sdk_wire_preserves_formal_run(claude_review_client, monkeypatch):
    client = claude_review_client
    pid = project(client)
    content = "原创工程采集资料必须保存事件时间并追溯\n" * 10000 + "末段必须保留最终告警条件\n"
    doc = upload(client, pid, content.encode()).json()
    req = client.post(f"/api/projects/{pid}/requirements", json=manual_requirement(doc)).json()
    task = client.post(f"/api/projects/{pid}/tasks", json={"goal": "只读审阅末段条件", "requirement_refs": [
        {"requirement_id": req["id"], "revision": 1, "sha256": req["sha256"]}]}).json()
    route = f"/api/projects/{pid}/tasks/{task['id']}/runs"
    run = client.post(route, json={"revision": 1, "operation_id": "full-source-run"}).json()
    scope = client.get(route + f"/{run['id']}/native-review-scope").json()
    body = {"operation_id": "selected-tail", "task_binding": scope["task_binding"], "input_hash": scope["input_hash"],
            "source_hash": scope["source_hash"], "needs_tools": False, "native": {}}
    factory = NativeFactory([0.002])
    configured(monkeypatch, factory)
    denied = client.post(route + f"/{run['id']}/native-reviews", json=body)
    assert denied.status_code == 422 and "No source text was truncated" in denied.json()["error"]["message"]
    assert not factory.transports
    last = scope["document_contexts"][0]["total_chunks"] - 1
    selected = {"document_id": doc["id"], "revision": 1, "chunk_indexes": [last]}
    for bad in ({**selected, "revision": 2}, {**selected, "document_id": "another-doc"}, {**selected, "chunk_indexes": [last, last]}):
        assert client.post(route + f"/{run['id']}/native-reviews", json={**body, "chunk_selections": [bad]}).status_code == 422
    admitted = client.post(route + f"/{run['id']}/native-reviews", json={**body, "chunk_selections": [selected]})
    assert admitted.status_code == 202, admitted.text
    final = until_review(client, pid, admitted.json()["admission_id"], {"RETURNED"})
    execution = client.get(f"/api/projects/{pid}/agent-executions/{final['native_execution_id']}").json()
    request = json.loads(execution["request"])
    assert "末段必须保留最终告警条件" in request["prompt"]
    assert "PARTIAL_EXPLICIT_SELECTION" in request["prompt"]
    assert len(request["prompt"].encode()) < 256 * 1024
    assert final["verification_status"] == "NOT_RUN"
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
