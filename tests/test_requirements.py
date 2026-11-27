import copy
import json
import sqlite3

import pytest
from packages.domain.store import Store
from test_documents import project, upload


def proposal(client, pid):
    doc = upload(client, pid, b"REQ: create a small file\nASSUME: local only\nQUESTION: confirm filename\nACCEPT: file contents match\n").json()
    response = client.post(f"/api/projects/{pid}/planning/extract", json={"document_id": doc["id"], "revision": 1})
    assert response.json()["mode"] == "mock"
    assert not response.json()["model_verified"]
    return doc, response.json()["proposal"]


def test_confirm_rev_and_old_planning_task_invalidation(client, tmp_path):
    pid = project(client)
    doc, body = proposal(client, pid)
    assert not body["confirmed"]
    draft = client.post(f"/api/projects/{pid}/requirements", json=body).json()
    assert client.post(f"/api/projects/{pid}/requirements/{draft['id']}/plan", json={"revision": 1}).status_code == 422
    body["confirmed"] = True
    confirmed = client.put(f"/api/projects/{pid}/requirements/{draft['id']}?expected_revision=1", json=body).json()
    assert confirmed["revision"] == 2
    assert confirmed["source_status"] == "CURRENT"
    plan = client.post(f"/api/projects/{pid}/requirements/{draft['id']}/plan", json={"revision": 2}).json()["tasks"]
    assert len(plan) == 1
    assert plan[0]["review_status"] == "DRAFT"
    assert json.loads(plan[0]["acceptance"])[0]["requirement_ids"] == ["R1"]
    assert len(client.post(f"/api/projects/{pid}/requirements/{draft['id']}/plan", json={"revision": 2}).json()["tasks"]) == 1
    old = client.get(f"/api/projects/{pid}/requirements/{draft['id']}?revision=2").json()
    body["requirements"][0]["text"] = "change the file contents"
    new = client.put(f"/api/projects/{pid}/requirements/{draft['id']}?expected_revision=2", json=body).json()
    assert new["sha256"] != old["sha256"]
    assert client.get(f"/api/projects/{pid}/requirements/{draft['id']}?revision=2").json()["payload"] == old["payload"]
    tasks = client.get(f"/api/projects/{pid}/planning/tasks").json()["tasks"]
    assert tasks[0]["review_status"] == "NEEDS_REVIEW"
    assert tasks[0]["requirement_hash"] == old["sha256"]
    assert client.post(f"/api/projects/{pid}/requirements/{draft['id']}/plan", json={"revision": 2}).status_code == 409
    with Store(tmp_path / "eeas.sqlite3").connect() as connection, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE requirement_versions SET payload='tampered'")


@pytest.mark.parametrize("action", ["revise", "delete"])
def test_source_revision_and_deletion_make_task_and_reference_stale(client, action):
    pid = project(client)
    doc, body = proposal(client, pid)
    body["confirmed"] = True
    req = client.post(f"/api/projects/{pid}/requirements", json=body).json()
    client.post(f"/api/projects/{pid}/requirements/{req['id']}/plan", json={"revision": 1})
    if action == "revise":
        client.put(f"/api/projects/{pid}/documents/{doc['id']}", files={"file": ("original.md", b"changed source")}, data={"expected_revision": 1})
    else:
        client.delete(f"/api/projects/{pid}/documents/{doc['id']}")
    stale = client.get(f"/api/projects/{pid}/requirements/{req['id']}").json()
    assert stale["source_status"] == "STALE"
    assert client.get(f"/api/projects/{pid}/planning/tasks").json()["tasks"][0]["review_status"] == "NEEDS_REVIEW"
    assert client.post(f"/api/projects/{pid}/requirements/{req['id']}/plan", json={"revision": 1}).status_code == 409


def test_bad_source_cross_project_and_acceptance_maps_are_rejected(client):
    pid, other = project(client, "A"), project(client, "B")
    doc, body = proposal(client, pid)
    assert client.post(f"/api/projects/{other}/requirements", json=body).status_code == 422
    damaged = copy.deepcopy(body)
    damaged["requirements"][0]["source_refs"][0]["sha256"] = "0" * 64
    assert client.post(f"/api/projects/{pid}/requirements", json=damaged).status_code == 422
    damaged = copy.deepcopy(body)
    damaged["acceptance"][0]["requirement_ids"] = ["missing"]
    assert client.post(f"/api/projects/{pid}/requirements", json=damaged).status_code == 422
    req = client.post(f"/api/projects/{pid}/requirements", json=body).json()
    assert client.get(f"/api/projects/{other}/requirements/{req['id']}").status_code == 404
    assert client.put(f"/api/projects/{pid}/requirements/{req['id']}?expected_revision=9", json=body).status_code == 409


def test_manual_unsourced_is_explicit_and_live_remains_disconnected(client):
    pid = project(client)
    doc, body = proposal(client, pid)
    body["requirements"][0]["source_refs"] = []
    body["origin"] = "manual"
    row = client.post(f"/api/projects/{pid}/requirements", json=body).json()
    assert row["source_status"] == "UNSOURCED"
    assert not row["payload"]["confirmed"]
    response = client.post(f"/api/projects/{pid}/planning/extract", json={"document_id": doc["id"], "revision": 1, "mode": "live"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AGENT_NOT_CONNECTED"
