import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil
import pytest

from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.fixture import TrustedFixtureRunner
from packages.runner.supervisor import atomic_json, matching_process
from packages.runner.worker import Worker
from test_documents import project
from test_tasks import inputs
from test_worker import submitted, until


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("Fault-injection synchronization timed out")


def controller(directory, window):
    return subprocess.Popen([sys.executable, str(ROOT / "tests/fixtures/worker_fault.py"),
                             str(directory), window], cwd=ROOT)


def crash(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=4)


def runner_fact(service, directory, pid, rid):
    return TrustedFixtureRunner(service, directory).inspect(service.run(pid, rid))


def clean_runner(service, directory, pid, rid):
    # Only send a request to the exact supervisor, never kill an unverified PID.
    run = service.run(pid, rid)
    fact = runner_fact(service, directory, pid, rid)
    job = fact.get("job")
    if job:
        atomic_json(Path(job["journal_path"]) / "cancel.json",
                    {"job_id": job["id"], "request_hash": job["request_hash"], "reason": "INTERRUPTED"})


@pytest.mark.parametrize("window", ["before-claim", "during-execution", "result-lost"])
def test_real_controller_crash_inspects_or_observes_without_replay(client, tmp_path, window):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    if window == "during-execution":
        body = {**body, "mock_behavior": "wait_for_cancel", "timeout_seconds": 60}
    task, run = submitted(client, pid, body, "crash-" + window)
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    original = controller(tmp_path, window)
    recovered = Worker(service, tmp_path)
    try:
        if window == "during-execution":
            fact = wait_for(lambda: (f if f.get("phase") == "RUNNING" else None)
                            if (f := runner_fact(service, tmp_path, pid, run["id"])) else None)
            child = fact["receipt"]["child"]
            assert matching_process(child)
        else:
            wait_for(lambda: (tmp_path / "fault-ready").exists())
        crash(original)
        if window == "during-execution":
            assert matching_process(child), "Detached Runner must survive controller termination"
        elif window == "result-lost":
            assert runner_fact(service, tmp_path, pid, run["id"])["state"] == "FINISHED"
            assert service.run(pid, run["id"])["execution_status"] == "RUNNING"
        assert recovered.start()
        if window == "during-execution":
            service.cancel(pid, run["id"])
        result = until(service, pid, run["id"], {"CANCELLED", "RETURNED", "FAILED", "UNKNOWN_OUTCOME"})
        assert result["execution_status"] == ("CANCELLED" if window == "during-execution" else "RETURNED"), result
        assert result["verification_status"] != "PASSED"
        with service.connect() as connection:
            assert connection.execute("SELECT count(*) FROM runner_jobs WHERE run_id=?", (run["id"],)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM task_runs WHERE task_id=?", (task["id"],)).fetchone()[0] == 1
        events = service.events(pid, run["id"])
        assert sum(e["payload"]["type"] == "tool_result" for e in events) == 1
        assert sum(e["payload"]["type"] == "runner_recovered" for e in events) == (window != "before-claim")
        if window != "during-execution":
            assert (tmp_path / result["workspace"] / "candidate.txt").read_text() == body["goal"] + "\n"
    finally:
        crash(original)
        recovered.stop()
        clean_runner(service, tmp_path, pid, run["id"])


def test_missing_result_or_reused_identity_blocks_without_killing_other_process(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, {**body, "mock_behavior": "wait_for_cancel", "timeout_seconds": 60}, "identity")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    original = controller(tmp_path, "during-execution")
    unrelated = psutil.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
    recovered = Worker(service, tmp_path)
    fact = None
    try:
        fact = wait_for(lambda: (f if f.get("phase") == "RUNNING" else None)
                        if (f := runner_fact(service, tmp_path, pid, run["id"])) else None)
        crash(original)
        with service.connect() as connection:
            # Same numeric PID is insufficient: deliberately supply a wrong birth time.
            connection.execute("UPDATE runner_jobs SET supervisor_pid=?,supervisor_created=? WHERE id=?",
                               (unrelated.pid, unrelated.create_time() - 1, fact["job"]["id"]))
        child_task, queued = submitted(client, pid, body, "must-stay-queued")
        assert recovered.start()
        result = until(service, pid, run["id"], {"UNKNOWN_OUTCOME"})
        assert result["verification_status"] == "BLOCKED"
        assert service.run(pid, queued["id"])["execution_status"] == "QUEUED"
        assert unrelated.poll() is None
        assert client.post(f"/api/projects/{pid}/runs/{run['id']}/verify").status_code == 422
        assert runner_fact(service, tmp_path, pid, run["id"])["reason"] == "RUNNER_EVIDENCE_INVALID"
    finally:
        crash(original)
        recovered.stop()
        if fact:
            atomic_json(Path(fact["job"]["journal_path"]) / "cancel.json",
                        {"job_id": fact["job"]["id"], "request_hash": fact["job"]["request_hash"], "reason": "INTERRUPTED"})
            wait_for(lambda: not matching_process(fact["receipt"]["supervisor"]))
        unrelated.kill()
        unrelated.wait(timeout=4)


def test_finished_result_tampering_is_unknown_and_not_replayed(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, body, "tampered-result")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    original = controller(tmp_path, "result-lost")
    recovered = Worker(service, tmp_path)
    try:
        wait_for(lambda: (tmp_path / "fault-ready").exists())
        crash(original)
        fact = runner_fact(service, tmp_path, pid, run["id"])
        assert fact["state"] == "FINISHED"
        Path(fact["job"]["stdout_path"]).write_bytes(b"changed-after-run")
        assert recovered.start()
        result = until(service, pid, run["id"], {"UNKNOWN_OUTCOME"})
        assert result["verification_status"] == "BLOCKED"
        with service.connect() as connection:
            assert connection.execute("SELECT count(*) FROM runner_jobs WHERE run_id=?", (run["id"],)).fetchone()[0] == 1
    finally:
        crash(original)
        recovered.stop()


def test_supervisor_lost_with_live_child_is_unknown_even_after_cancel(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, {**body, "mock_behavior": "wait_for_cancel", "timeout_seconds": 60}, "lost-supervisor")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    original = controller(tmp_path, "during-execution")
    recovered = Worker(service, tmp_path)
    child = None
    try:
        fact = wait_for(lambda: (f if f.get("phase") == "RUNNING" else None)
                        if (f := runner_fact(service, tmp_path, pid, run["id"])) else None)
        child = fact["receipt"]["child"]
        crash(original)
        supervisor = matching_process(fact["receipt"]["supervisor"])
        assert supervisor
        supervisor.kill()
        wait_for(lambda: not matching_process(fact["receipt"]["supervisor"]))
        assert matching_process(child)
        service.cancel(pid, run["id"])
        assert recovered.start()
        result = until(service, pid, run["id"], {"UNKNOWN_OUTCOME"})
        assert result["verification_status"] == "BLOCKED"
        assert matching_process(child), "An unknown stop cannot be declared CANCELLED"
    finally:
        crash(original)
        recovered.stop()
        target = matching_process(child)
        if target:
            target.kill()


def test_runner_inspection_api_reports_actual_exit_and_scopes_project(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, body, "inspect-api")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    worker = Worker(service, tmp_path)
    assert worker.start()
    try:
        until(service, pid, run["id"], {"RETURNED"})
        route = f"/api/projects/{pid}/runs/{run['id']}/runner"
        fact = client.get(route).json()
        assert fact["state"] == "FINISHED"
        assert fact["exit_code"] == 0
        assert fact["child"]["pid"] > 0
        assert not fact["replay_allowed"]
        other = project(client, "other-original-project")
        assert client.get(f"/api/projects/{other}/runs/{run['id']}/runner").status_code == 404
    finally:
        worker.stop()


def test_detached_timeout_survives_controller_crash(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, {**body, "mock_behavior": "wait_for_cancel", "timeout_seconds": 2}, "independent-timeout")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    original = controller(tmp_path, "during-execution")
    recovered = Worker(service, tmp_path)
    try:
        wait_for(lambda: (f if f.get("phase") == "RUNNING" else None)
                 if (f := runner_fact(service, tmp_path, pid, run["id"])) else None)
        crash(original)
        fact = wait_for(lambda: (f if f["state"] == "FINISHED" else None)
                        if (f := runner_fact(service, tmp_path, pid, run["id"])) else None)
        assert fact["receipt"]["error_type"] == "TIME_LIMIT"
        assert recovered.start()
        result = until(service, pid, run["id"], {"FAILED"})
        assert result["error_type"] == "TIME_LIMIT"
        assert result["verification_status"] == "NOT_RUN"
    finally:
        crash(original)
        recovered.stop()
        clean_runner(service, tmp_path, pid, run["id"])


def test_supervisor_caps_actual_output_without_controller(tmp_path):
    journal, workspace = tmp_path / "journal", tmp_path / "workspace"
    journal.mkdir()
    workspace.mkdir()
    request = {"job_id": "original-output-job", "run_id": "original-output-run",
               "argv": [sys.executable, "-I", "-c", "import sys,time; sys.stdout.write('x'*1048576); sys.stdout.flush(); time.sleep(5)"],
               "workspace": str(workspace), "stdout_path": str(workspace / "stdout.txt"),
               "stderr_path": str(workspace / "stderr.txt"), "timeout_seconds": 7, "output_limit_bytes": 65536}
    atomic_json(journal / "request.json", request)
    process = subprocess.run([sys.executable, "-I", str(ROOT / "packages/runner/supervisor.py"),
                              str(journal / "request.json")], timeout=10, check=False)
    receipt = json.loads((journal / "receipt.json").read_bytes())
    assert process.returncode == 0
    assert receipt["phase"] == "FINISHED"
    assert receipt["error_type"] == "OUTPUT_LIMIT"
    assert isinstance(receipt["exit_code"], int)
    assert sum(path.stat().st_size for path in workspace.iterdir()) <= 65536
