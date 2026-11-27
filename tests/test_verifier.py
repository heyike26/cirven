import json
from pathlib import Path
import time

import pytest
from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from packages.verifier.service import Verifier
from packages.verifier.repair import RepairCoordinator
from test_documents import project
from test_tasks import inputs
from test_worker import submitted, until


def tools(client, tmp_path, behavior="success"):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, {**body, "mock_behavior": behavior}, "verification-original")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    verifier = Verifier(service, tmp_path)
    repair = RepairCoordinator(service, verifier)
    worker = Worker(service, tmp_path)
    worker.repair_coordinator = repair
    worker.verifier = verifier
    worker.start()
    until(service, pid, run["id"], {"RETURNED", "FAILED"})
    return pid, task, run, service, verifier, repair, worker


def test_independent_real_tests_correct_candidate_and_integrity_check(client, tmp_path):
    pid, task, run, service, verifier, repair, worker = tools(client, tmp_path)
    try:
        evidence = verifier.verify(pid, run["id"])
        data = evidence["payload"]
        assert data["result"] == "PASSED"
        assert data["exit_code"] == 0
        assert data["test_report"]["tests_run"] == 1
        assert data["test_report"]["skipped"] == 0
        assert Path(data["stderr_path"]).read_text()
        assert data["runtime_kind"] == "mock" and data["source_kind"] == "synthetic"
        assert service.run(pid, run["id"])["verification_status"] == "PASSED"
        (tmp_path / run["workspace"] / "candidate.txt").write_text("wrong candidate")
        assert verifier.evidence(pid, data["evidence_id"])["association_status"] == "INTEGRITY_ERROR"
        assert client.get(f"/api/projects/{pid}/runs/{run['id']}/artifacts/candidate.txt").status_code == 422
    finally:
        worker.stop()


def test_approved_test_tamper_is_rejected_without_running_it(client, tmp_path):
    pid, task, run, service, verifier, repair, worker = tools(client, tmp_path)
    try:
        with service.connect() as connection:
            baseline = connection.execute("SELECT * FROM verification_baselines WHERE run_id=?", (run["id"],)).fetchone()
        Path(baseline["baseline_path"]).write_text("raise SystemExit(0)\n")
        evidence = verifier.verify(pid, run["id"])
        assert evidence["payload"]["result"] == "FAILED"
        assert evidence["payload"]["error_type"] == "INTEGRITY_ERROR"
        assert evidence["payload"]["command"] is None
        assert evidence["association_status"] == "INTEGRITY_ERROR"
    finally:
        worker.stop()


@pytest.mark.parametrize("behavior,expected", [("repairable", "COMPLETE"), ("persistent_failure", "EXHAUSTED")])
def test_original_fails_correct_fix_passes_or_stops_after_three_preserving_every_attempt(client, tmp_path, behavior, expected):
    pid, task, run, service, verifier, repair, worker = tools(client, tmp_path, behavior)
    try:
        first = verifier.verify(pid, run["id"])
        assert first["payload"]["result"] == "FAILED"
        assert first["payload"]["exit_code"] != 0
        session = repair.request(pid, run["id"], 3)
        # Four independent attempts plus four protected baseline processes and
        # durable controller writes. Their execution budgets remain unchanged.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = repair.inspect(pid, session["id"])
            if current["status"] != "RUNNING":
                break
            time.sleep(0.05)
        assert current["status"] == expected, current
        runs = service.runs(pid, task["id"])
        assert len(runs) == (2 if expected == "COMPLETE" else 4)
        assert runs[0]["verification_status"] == "FAILED"
        assert len({r["id"] for r in runs}) == len(runs)
        assert len({r["spec_hash"] for r in runs}) == 1
        assert len({r["workspace"] for r in runs}) == len(runs)
        with service.connect() as connection:
            records = [json.loads(r[0]) for r in connection.execute("SELECT payload FROM verification_evidence ORDER BY created_at")]
        assert len(records) == len(runs)
        assert all(Path(r["stderr_path"]).is_file() for r in records)
    finally:
        worker.stop()


def test_actual_verified_predecessor_dispatches_successor_and_old_evidence_cannot_satisfy_new_revision(client, tmp_path):
    pid, task, run, service, verifier, repair, worker = tools(client, tmp_path)
    try:
        body = {"goal": "successor original", "requirement_refs": task["spec"]["requirement_refs"], "dependencies": [task["id"]]}
        child, child_run = submitted(client, pid, body, "successor")
        assert service.run(pid, child_run["id"])["execution_status"] == "QUEUED"
        evidence = verifier.verify(pid, run["id"])
        until(service, pid, child_run["id"], {"RETURNED"})
        old = evidence["payload"]
        changed = {"goal": "changed goal", "requirement_refs": task["spec"]["requirement_refs"]}
        client.put(f"/api/projects/{pid}/tasks/{task['id']}?expected_revision=1", json=changed)
        assert verifier.evidence(pid, old["evidence_id"])["association_status"] == "STALE_EVIDENCE"
        assert service.run(pid, run["id"])["task_revision"] == 1
    finally:
        worker.stop()
