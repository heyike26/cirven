"""Real approval/Runner/Verifier transitions for an approval-gated repair."""
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from conftest import ROOT
from packages.domain.approvals import Approvals
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from packages.verifier.repair import RepairCoordinator
from packages.verifier.service import Verifier
from test_documents import project
from test_tasks import inputs
from test_worker import submitted, until


def checkpoint(service, verifier, repair, pid, session_id, mode, stage):
    """Preserve actual durable facts and process receipts, including before FAIL."""
    if not os.environ.get("EEAS_EVIDENCE_DIR"):
        return
    target = Path(os.environ["EEAS_EVIDENCE_DIR"]) / "actual-repair-approvals" / mode / stage
    target.mkdir(parents=True, exist_ok=False)
    session = repair.inspect(pid, session_id)
    runs = service.runs(pid, session["task_id"])
    with service.connect() as connection:
        jobs = [dict(row) for row in connection.execute("SELECT * FROM runner_jobs ORDER BY started_at")]
        proofs = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM verification_evidence ORDER BY created_at")]
    facts = {"repair_session": session, "runs": runs, "approvals": Approvals(service).list(pid),
             "runner_jobs": jobs, "verification_evidence": proofs,
             "events": {run["id"]: service.events(pid, run["id"]) for run in runs},
             "runtime_kind": "mock", "source_kind": "synthetic"}
    with (target / "facts.json").open("x", encoding="utf-8") as stream:
        json.dump(facts, stream, ensure_ascii=False, indent=2)
    for run in runs:
        paths = {"candidate.txt": service.database.parent / run["workspace"] / "candidate.txt"}
        job = next((job for job in jobs if job["run_id"] == run["id"]), None)
        if job:
            paths.update({"runner-" + name: Path(job["journal_path"]) / name
                          for name in ("request.json", "receipt.json")})
            paths.update({"runner-stdout.txt": Path(job["stdout_path"]),
                          "runner-stderr.txt": Path(job["stderr_path"])})
        proof = next((proof for proof in proofs if proof["run_id"] == run["id"]), None)
        if proof:
            paths.update({"verifier-" + name + ".txt": Path(proof[name + "_path"])
                          for name in ("stdout", "stderr") if proof.get(name + "_path")})
        for name, path in paths.items():
            if path.is_file():
                directory = target / run["id"]
                directory.mkdir(exist_ok=True)
                shutil.copyfile(path, directory / name)


def no_execution(service, pid, run_id, data_dir):
    run = service.run(pid, run_id)
    assert run["runner_job_id"] is None
    assert run["started_at"] is None
    assert not (data_dir / run["workspace"]).exists()
    assert not any(event["payload"]["type"] in {"running", "tool_result"}
                   for event in service.events(pid, run_id))
    with service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM runner_jobs WHERE run_id=?", (run_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("decision", ["approve", "reject", "cancel"])
def test_repair_waits_for_new_approval_then_completes_or_blocks_preserving_failure(client, tmp_path, decision):
    pid = project(client)
    _, _, body = inputs(client, pid)
    task, original = submitted(client, pid, {**body, "approval_required": True,
                                            "mock_behavior": "repairable"}, "approval-gated-original")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    approvals = Approvals(service)
    verifier = Verifier(service, tmp_path)
    repair = RepairCoordinator(service, verifier)
    first_approval = approvals.list(pid)[0]
    route = f"/api/projects/{pid}/approvals/{first_approval['id']}/decision"
    original_decision = {"action_hash": first_approval["action_hash"], "approve": True}
    assert client.post(route, json=original_decision).status_code == 200
    # A fresh real Worker executes tools; explicit ticks isolate the coordinator
    # decision and avoid relying on a test driver to auto-approve any attempt.
    worker = Worker(service, tmp_path)
    worker.verifier = verifier
    assert worker.repair_coordinator is None
    assert worker.start()
    try:
        until(service, pid, original["id"], {"RETURNED"})
        first_evidence = verifier.verify(pid, original["id"])
        assert first_evidence["association_status"] == "CURRENT"
        assert first_evidence["payload"]["result"] == "FAILED"
        assert first_evidence["payload"]["exit_code"] != 0
        candidate = tmp_path / original["workspace"] / "candidate.txt"
        failed_candidate_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        assert approvals.inspect(pid, first_approval["id"])["status"] == "CONSUMED"
        session = repair.request(pid, original["id"], 1)
        repair.tick()
        pending = repair.inspect(pid, session["id"])
        second = service.run(pid, pending["current_run_id"])
        assert pending["status"] == "RUNNING"
        assert pending["repairs_started"] == 1
        assert second["attempt_id"] == 2
        assert second["execution_status"] == "AWAITING_APPROVAL"
        second_approval = next(approval for approval in approvals.list(pid) if approval["run_id"] == second["id"])
        assert second_approval["status"] == "PENDING"
        assert second_approval["id"] != first_approval["id"]
        assert second_approval["action_hash"] != first_approval["action_hash"]
        assert second["spec_hash"] == original["spec_hash"] == task["sha256"]
        checkpoint(service, verifier, repair, pid, session["id"], decision, "new-approval-pending")
        for tick in range(4):
            repair.tick()
            current = repair.inspect(pid, session["id"])
            checkpoint(service, verifier, repair, pid, session["id"], decision, f"waiting-tick-{tick}")
            assert current["status"] == "RUNNING", current
            assert current["repairs_started"] == 1
            assert current["current_run_id"] == second["id"]
            assert service.run(pid, second["id"])["execution_status"] == "AWAITING_APPROVAL"
            no_execution(service, pid, second["id"], tmp_path)
            worker.stop_event.wait(0.12)
        # Old approval retry remains idempotent for its old run only.
        assert client.post(route, json=original_decision).json()["status"] == "CONSUMED"
        new_route = f"/api/projects/{pid}/approvals/{second_approval['id']}/decision"
        assert client.post(new_route, json=original_decision).status_code == 409
        assert approvals.inspect(pid, second_approval["id"])["status"] == "PENDING"
        no_execution(service, pid, second["id"], tmp_path)
        if decision == "cancel":
            assert client.post(f"/api/projects/{pid}/runs/{second['id']}/cancel").status_code == 200
        else:
            response = client.post(new_route, json={"action_hash": second_approval["action_hash"],
                                                   "approve": decision == "approve"})
            assert response.status_code == 200
        if decision == "approve":
            until(service, pid, second["id"], {"RETURNED"})
            repair.tick()
            assert repair.inspect(pid, session["id"])["status"] == "COMPLETE"
            assert service.run(pid, second["id"])["verification_status"] == "PASSED"
            assert approvals.inspect(pid, second_approval["id"])["status"] == "CONSUMED"
        else:
            repair.tick()
            assert repair.inspect(pid, session["id"])["status"] == "BLOCKED"
            assert service.run(pid, second["id"])["execution_status"] == "CANCELLED"
            no_execution(service, pid, second["id"], tmp_path)
        final = repair.inspect(pid, session["id"])
        assert final["repairs_started"] == 1
        assert final["max_repairs"] == 1
        assert len(service.runs(pid, task["id"])) == 2
        assert service.run(pid, original["id"])["verification_status"] == "FAILED"
        assert verifier.evidence(pid, first_evidence["payload"]["evidence_id"]) == first_evidence
        assert hashlib.sha256(candidate.read_bytes()).hexdigest() == failed_candidate_hash
        checkpoint(service, verifier, repair, pid, session["id"], decision, "final")
    finally:
        worker.stop()
