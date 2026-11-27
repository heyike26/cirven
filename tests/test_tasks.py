import copy
import json
import sqlite3

import pytest
from conftest import ROOT
from packages.domain.tasks import TaskService
from test_documents import project
from test_requirements import proposal


def inputs(client, pid):
    doc, body = proposal(client, pid)
    body["confirmed"] = True
    req = client.post(f"/api/projects/{pid}/requirements", json=body).json()
    task = {"goal": "original-task", "requirement_refs": [{"requirement_id": req["id"],
            "revision": req["revision"], "sha256": req["sha256"]}]}
    return doc, req, task


def test_tasks_freeze_and_idempotent_run_survive_service_reopen(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task = client.post(f"/api/projects/{pid}/tasks", json=body).json()
    assert task["spec"]["schema_version"] == "eeas.task-spec.v1"
    assert len(task["spec"]["source_hash"]) == 64
    route = f"/api/projects/{pid}/tasks/{task['id']}/runs"
    run = client.post(route, json={"revision": 1, "operation_id": "original-operation"}).json()
    repeated = client.post(route, json={"revision": 1, "operation_id": "original-operation"}).json()
    assert repeated["id"] == run["id"]
    assert run["execution_status"] == "QUEUED"
    assert run["verification_status"] == "NOT_RUN"
    frozen = json.loads(run["frozen_inputs"])
    assert frozen["documents"][0]["sha256"] == doc["sha256"]
    assert frozen["spec"]["task_revision"] == 1
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    assert service.run(pid, run["id"])["frozen_inputs"] == run["frozen_inputs"]
    assert len(service.events(pid, run["id"])) == 1
    with service.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE task_runs SET frozen_inputs='changed' WHERE id=?", (run["id"],))
    other = client.post(f"/api/projects/{pid}/tasks", json=body).json()
    assert client.post(f"/api/projects/{pid}/tasks/{other['id']}/runs",
                       json={"revision": 1, "operation_id": "original-operation"}).status_code == 409


def test_cycle_cross_project_and_stale_requirement_rejected(client):
    pid, other = project(client, "A"), project(client, "B")
    doc, req, body = inputs(client, pid)
    first = client.post(f"/api/projects/{pid}/tasks", json=body).json()
    second_body = {**body, "dependencies": [first["id"]]}
    second = client.post(f"/api/projects/{pid}/tasks", json=second_body).json()
    cyclic = {**body, "dependencies": [second["id"]]}
    assert client.put(f"/api/projects/{pid}/tasks/{first['id']}?expected_revision=1", json=cyclic).status_code == 422
    assert client.post(f"/api/projects/{other}/tasks", json=second_body).status_code in (404, 422)
    changed = copy.deepcopy(req["payload"])
    changed["requirements"][0]["text"] = "changed requirement"
    client.put(f"/api/projects/{pid}/requirements/{req['id']}?expected_revision=1", json=changed)
    assert client.get(f"/api/projects/{pid}/tasks/{first['id']}").json()["review_status"] == "NEEDS_REVIEW"
    assert client.post(f"/api/projects/{pid}/tasks/{first['id']}/runs", json={"revision": 1, "operation_id": "stale"}).status_code == 409


def test_task_revision_does_not_rewrite_existing_run_or_acceptance(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task = client.post(f"/api/projects/{pid}/tasks", json=body).json()
    run = client.post(f"/api/projects/{pid}/tasks/{task['id']}/runs", json={"revision": 1, "operation_id": "old"}).json()
    body["goal"] = "new goal"
    revised = client.put(f"/api/projects/{pid}/tasks/{task['id']}?expected_revision=1", json=body).json()
    assert revised["revision"] == 2
    assert revised["sha256"] != task["sha256"]
    assert client.get(f"/api/projects/{pid}/tasks/{task['id']}?revision=1").json()["spec"] == task["spec"]
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json()["spec_hash"] == task["sha256"]
    with TaskService(tmp_path / "eeas.sqlite3", ROOT).connect() as connection, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE task_versions SET spec='altered'")


def test_event_dedup_cursor_no_fake_pass_and_queue_cancel(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task = client.post(f"/api/projects/{pid}/tasks", json=body).json()
    run = client.post(f"/api/projects/{pid}/tasks/{task['id']}/runs", json={"revision": 1, "operation_id": "events"}).json()
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    payload = {"type": "model_text", "text": "Agent says PASS"}
    assert service.emit_event(pid, run["id"], "message-1", payload) == 2
    assert service.emit_event(pid, run["id"], "message-1", payload) == 2
    assert service.run(pid, run["id"])["verification_status"] == "NOT_RUN"
    with pytest.raises(Exception, match="already bound"):
        service.emit_event(pid, run["id"], "message-1", {"type": "different"})
    events = client.get(f"/api/projects/{pid}/runs/{run['id']}/events?after=1").json()
    assert [e["event_seq"] for e in events["events"]] == [2]
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}/events?after=2").json()["events"] == []
    assert client.post(f"/api/projects/{pid}/runs/{run['id']}/cancel").json()["execution_status"] == "CANCELLED"
    client.post(f"/api/projects/{pid}/runs/{run['id']}/cancel")
    assert [e["event_seq"] for e in service.events(pid, run["id"])] == [1, 2, 3]


@pytest.mark.parametrize("path", ["../escape.txt", "C:\\escape.txt", "/tmp/escape.txt", ".", "CON.txt", "folder/NUL", "trailing. ", "STDOUT.TXT", "frozen-inputs.json/extra"])
def test_write_paths_reject_windows_devices_and_escape(client, path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    body["allowed_write_paths"] = [path]
    assert client.post(f"/api/projects/{pid}/tasks", json=body).status_code == 422
