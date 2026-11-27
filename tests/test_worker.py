import json
import time

import pytest
from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker, WorkerLock
from test_documents import project
from test_tasks import inputs


def until(service, pid, rid, states, timeout=20):
    # Observe controller startup, optional plugin qualification/call, and durable
    # terminal persistence as well as the tool. This never changes the frozen
    # tool budget, which the independent supervisor enforces.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = service.run(pid, rid)
        if run["execution_status"] in states:
            return run
        time.sleep(0.03)
    raise AssertionError(f"run did not reach {sorted(states)}; actual execution={run['execution_status']}, verification={run['verification_status']}, error={run['error_type']}, job={run['runner_job_id']}")


def submitted(client, pid, body, key):
    task = client.post(f"/api/projects/{pid}/tasks", json=body).json()
    run = client.post(f"/api/projects/{pid}/tasks/{task['id']}/runs", json={"revision": 1, "operation_id": key}).json()
    return task, run


def test_single_worker_real_process_returns_candidate_with_no_verification_pass(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, body, "real-fixture")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    worker = Worker(service, tmp_path)
    second = Worker(service, tmp_path)
    assert worker.start()
    try:
        assert not second.start()
        result = until(service, pid, run["id"], {"RETURNED", "FAILED"})
        assert result["execution_status"] == "RETURNED", result
        assert result["verification_status"] == "NOT_RUN"
        assert (tmp_path / result["workspace"] / "candidate.txt").read_text() == body["goal"] + "\n"
        with service.connect() as connection:
            job = connection.execute("SELECT * FROM runner_jobs WHERE id=?", (result["runner_job_id"],)).fetchone()
            assert job["pid"] > 0
            assert job["exit_code"] == 0
        events = service.events(pid, run["id"])
        assert [e["event_seq"] for e in events] == list(range(1, len(events)+1))
        assert any(e["payload"]["type"] == "tool_result" for e in events)
    finally:
        worker.stop()
        second.stop()


def test_failed_dependency_propagates_without_running_successor(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    parent, parent_run = submitted(client, pid, {**body, "mock_behavior": "failure"}, "failure")
    child, child_run = submitted(client, pid, {**body, "dependencies": [parent["id"]]}, "child")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    worker = Worker(service, tmp_path)
    worker.start()
    try:
        result = until(service, pid, child_run["id"], {"FAILED"})
        assert result["error_type"] == "DEPENDENCY_FAILED"
        assert result["verification_status"] == "BLOCKED"
        assert result["runner_job_id"] is None
        assert not (tmp_path / result["workspace"]).exists()
    finally:
        worker.stop()


def test_returned_parent_does_not_satisfy_passed_dependency(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    parent, parent_run = submitted(client, pid, body, "parent")
    child, child_run = submitted(client, pid, {**body, "dependencies": [parent["id"]]}, "after-validation")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    worker = Worker(service, tmp_path)
    worker.start()
    try:
        until(service, pid, parent_run["id"], {"RETURNED"})
        deadline = time.monotonic() + 5
        while not any(e["payload"]["type"] == "waiting_dependency" for e in service.events(pid, child_run["id"])) and time.monotonic() < deadline:
            time.sleep(0.03)
        assert service.run(pid, child_run["id"])["execution_status"] == "QUEUED"
        assert any(e["payload"]["type"] == "waiting_dependency" for e in service.events(pid, child_run["id"]))
    finally:
        worker.stop()


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_real_running_process_cancel_and_timeout(client, tmp_path, mode):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, {**body, "mock_behavior": "wait_for_cancel", "timeout_seconds": 1 if mode == "timeout" else 10}, mode)
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    worker = Worker(service, tmp_path)
    worker.start()
    try:
        until(service, pid, run["id"], {"RUNNING"})
        # Ensure the actual Runner process exists before cancelling.
        deadline = time.monotonic() + 4
        while not service.run(pid, run["id"])["runner_job_id"] and time.monotonic() < deadline:
            time.sleep(0.02)
        if mode == "cancel":
            client.post(f"/api/projects/{pid}/runs/{run['id']}/cancel")
        result = until(service, pid, run["id"], {"CANCELLED", "FAILED"})
        assert result["error_type"] == ("CANCELLED" if mode == "cancel" else "TIME_LIMIT")
        with service.connect() as connection:
            job = connection.execute("SELECT * FROM runner_jobs WHERE id=?", (result["runner_job_id"],)).fetchone()
            assert job["status"] == "FINISHED"
            assert job["exit_code"] is not None
    finally:
        worker.stop()
