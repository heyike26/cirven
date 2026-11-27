from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
from conftest import ROOT
from packages.domain.approvals import Approvals
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from test_documents import project
from test_tasks import inputs
from test_worker import submitted, until


def prepared(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, {**body, "approval_required": True}, "approved-original")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    approval = client.get(f"/api/projects/{pid}/approvals").json()["approvals"][0]
    return pid, task, run, service, approval


def test_approval_survives_reopen_repeated_approve_and_single_real_effect(client, tmp_path):
    pid, task, run, service, approval = prepared(client, tmp_path)
    assert run["execution_status"] == "AWAITING_APPROVAL"
    assert approval["action"]["task_revision"] == 1
    assert approval["action"]["write_paths"] == ["candidate.txt"]
    assert Approvals(TaskService(tmp_path / "eeas.sqlite3", ROOT)).inspect(pid, approval["id"]) == approval
    route = f"/api/projects/{pid}/approvals/{approval['id']}/decision"
    decision = {"action_hash": approval["action_hash"], "approve": True}
    assert client.post(route, json=decision).status_code == 200
    assert client.post(route, json=decision).status_code == 200
    worker = Worker(service, tmp_path)
    worker.start()
    try:
        until(service, pid, run["id"], {"RETURNED"})
        assert client.post(route, json=decision).json()["status"] == "CONSUMED"
        with service.connect() as connection:
            assert connection.execute("SELECT count(*) FROM runner_jobs WHERE run_id=?", (run["id"],)).fetchone()[0] == 1
    finally:
        worker.stop()


def test_wrong_digest_expiry_and_old_revision_cannot_be_approved(client, tmp_path):
    pid, task, run, service, approval = prepared(client, tmp_path)
    route = f"/api/projects/{pid}/approvals/{approval['id']}/decision"
    assert client.post(route, json={"action_hash": "0" * 64, "approve": True}).status_code == 409
    with service.connect() as connection:
        connection.execute("UPDATE approvals SET expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), approval["id"]))
    response = client.post(route, json={"action_hash": approval["action_hash"], "approve": True})
    assert response.status_code == 409
    assert "EXPIRED" in response.json()["error"]["message"]
    assert not (tmp_path / run["workspace"]).exists()
    with service.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE approvals SET action_hash='changed'")


def test_reject_or_cancel_waiting_approval_has_no_process_effect(client, tmp_path):
    pid, task, run, service, approval = prepared(client, tmp_path)
    route = f"/api/projects/{pid}/approvals/{approval['id']}/decision"
    assert client.post(route, json={"action_hash": approval["action_hash"], "approve": False}).status_code == 200
    assert service.run(pid, run["id"])["execution_status"] == "CANCELLED"
    assert client.post(route, json={"action_hash": approval["action_hash"], "approve": True}).status_code == 409
    assert not (tmp_path / run["workspace"]).exists()


def test_changed_requirement_invalidates_pending_approval(client, tmp_path):
    pid, task, run, service, approval = prepared(client, tmp_path)
    ref = task["spec"]["requirement_refs"][0]
    payload = client.get(f"/api/projects/{pid}/requirements/{ref['requirement_id']}").json()["payload"]
    payload["requirements"][0]["text"] = "changed requirement"
    client.put(f"/api/projects/{pid}/requirements/{ref['requirement_id']}?expected_revision=1", json=payload)
    route = f"/api/projects/{pid}/approvals/{approval['id']}/decision"
    assert client.post(route, json={"action_hash": approval["action_hash"], "approve": True}).status_code == 409
