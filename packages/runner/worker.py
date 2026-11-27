from __future__ import annotations
import json
import os
from pathlib import Path
import threading
import uuid
import traceback

from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import now
from packages.adapters.mock_agent import MockAgent
from packages.runner.fixture import TrustedFixtureRunner


class WorkerLock:
    def __init__(self, path):
        self.path = path
        self.stream = None

    def acquire(self):
        if self.path.is_symlink():
            raise ValueError("Worker lock must not be a symlink")
        stream = self.path.open("a+b")
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def release(self):
        if not self.stream:
            return
        if os.name == "nt":
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
        self.stream = None


class Worker:
    def __init__(self, service, data_dir):
        self.service = service
        self.data_dir = data_dir
        self.id = str(uuid.uuid4())
        self.stop_event = threading.Event()
        self.lock = WorkerLock(data_dir / "worker.lock")
        self.thread = None
        self.status = "OFFLINE"
        self.repair_coordinator = None
        self.verifier = None
        self.recoveries = []

    def start(self):
        if not self.lock.acquire():
            self.status = "OWNED_BY_OTHER_WORKER"
            return False
        # A released lock proves only that the controller is gone. Inspect
        # independent runner facts before deciding whether to continue observing.
        runner = TrustedFixtureRunner(self.service, self.data_dir)
        with self.service.connect() as connection:
            rows = connection.execute("SELECT * FROM task_runs WHERE execution_status IN ('RUNNING','CANCEL_REQUESTED','UNKNOWN_OUTCOME')").fetchall()
        facts = [(dict(row), runner.inspect(dict(row))) for row in rows]
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for run, fact in facts:
                if fact["state"] in {"LIVE", "FINISHED"}:
                    state = "CANCEL_REQUESTED" if run["execution_status"] == "CANCEL_REQUESTED" else "RUNNING"
                    connection.execute("UPDATE task_runs SET execution_status=?,worker_id=?,verification_status='NOT_RUN',error_type=NULL,heartbeat_at=? WHERE id=?",
                                       (state, self.id, now(), run["id"]))
                    self.service._event(connection, run["id"], "recovered-" + self.id,
                        {"type": "runner_recovered", "fact": fact["state"], "replayed": False})
                    self.recoveries.append({**run, "worker_id": self.id, "execution_status": state})
                else:
                    connection.execute("UPDATE task_runs SET execution_status='UNKNOWN_OUTCOME',verification_status='BLOCKED',error_type='OWNER_LOST' WHERE id=?", (run["id"],))
                    self.service._event(connection, run["id"], "owner-lost", {"type": "unknown_outcome", "reason": "Inspect runner evidence before retrying"})
            # Verification has no durable subprocess receipt yet. Preserve its
            # failed/incomplete attempt and require an explicit new verification.
            connection.execute("UPDATE task_runs SET verification_status='BLOCKED',error_type='VERIFIER_OWNER_LOST' WHERE verification_status='RUNNING'")
            connection.execute("UPDATE agent_executions SET execution_status='UNKNOWN_OUTCOME',verification_status='BLOCKED',ended_at=? WHERE execution_status IN ('RUNNING','CANCEL_REQUESTED')", (now(),))
        self.status = "ONLINE"
        self.thread = threading.Thread(target=self.loop, name="eeas-single-worker", daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=8)
            if self.thread.is_alive():
                self.status = "STOP_TIMEOUT"
                return
        self.status = "OFFLINE"
        self.lock.release()

    def claim(self):
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM task_runs WHERE execution_status='UNKNOWN_OUTCOME' LIMIT 1").fetchone():
                self.status = "BLOCKED_UNKNOWN_OUTCOME"
                return None
            rows = connection.execute("SELECT * FROM task_runs WHERE execution_status='QUEUED' ORDER BY created_at,id").fetchall()
            for row in rows:
                run = dict(row)
                try:
                    task = self.service.task(run["project_id"], run["task_id"], run["task_revision"])
                    if task["review_status"] != "CURRENT" or task["current_revision"] != run["task_revision"]:
                        raise Conflict("Task input changed before execution")
                except (Conflict, InvalidInput, NotFound):
                    connection.execute("UPDATE task_runs SET execution_status='CANCELLED',verification_status='NOT_APPLICABLE',ended_at=?,error_type='STALE_INPUT' WHERE id=?", (now(), run["id"]))
                    self.service._event(connection, run["id"], "stale-input", {"type": "cancelled", "reason": "stale_input"})
                    continue
                from packages.domain.questions import knowledge_gate
                from packages.domain.intake import digest
                knowledge = knowledge_gate(connection, run["project_id"], run["task_id"], run["task_revision"], run["spec_hash"])
                if knowledge["status"] != "READY":
                    self.service._event(connection, run["id"], "waiting-question-" + digest(knowledge),
                        {"type": "waiting_question", "knowledge": knowledge})
                    continue
                ready = True
                for parent in task["spec"]["dependencies"]:
                    previous = connection.execute("SELECT * FROM task_runs WHERE task_id=? AND task_revision=? ORDER BY attempt_id DESC LIMIT 1", (parent["task_id"], parent["task_revision"])).fetchone()
                    if previous and (previous["execution_status"] in {"FAILED", "CANCELLED", "INTERRUPTED", "UNKNOWN_OUTCOME"} or previous["verification_status"] == "FAILED"):
                        connection.execute("UPDATE task_runs SET execution_status='FAILED',verification_status='BLOCKED',ended_at=?,error_type='DEPENDENCY_FAILED' WHERE id=?", (now(), run["id"]))
                        self.service._event(connection, run["id"], "dependency-failed", {"type": "dependency_failed", "dependency_id": parent["task_id"]})
                        ready = False
                        break
                    if not previous or previous["verification_status"] != "PASSED":
                        ready = False
                        self.service._event(connection, run["id"], "waiting-" + parent["task_id"], {"type": "waiting_dependency", "dependency_id": parent["task_id"], "requires": "PASSED"})
                    elif self.verifier:
                        proof = connection.execute("SELECT id FROM verification_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (previous["id"],)).fetchone()
                        if not proof or self.verifier.evidence(run["project_id"], proof["id"])["association_status"] != "CURRENT":
                            ready = False
                            connection.execute("UPDATE task_runs SET execution_status='FAILED',verification_status='BLOCKED',ended_at=?,error_type='DEPENDENCY_EVIDENCE_INVALID' WHERE id=?", (now(), run["id"]))
                            self.service._event(connection, run["id"], "dependency-integrity", {"type": "dependency_failed", "reason": "evidence_integrity"})
                    else:
                        ready = False
                if not ready:
                    continue
                if task["spec"].get("approval_required"):
                    from packages.domain.approvals import Approvals
                    try:
                        Approvals(self.service).consume(connection, run)
                    except Conflict:
                        connection.execute("UPDATE task_runs SET execution_status='AWAITING_APPROVAL',error_type='APPROVAL_INVALID' WHERE id=?", (run["id"],))
                        self.service._event(connection, run["id"], "approval-invalid", {"type": "awaiting_approval", "reason": "approval_invalid"})
                        continue
                changed = connection.execute("UPDATE task_runs SET execution_status='RUNNING',worker_id=?,started_at=?,heartbeat_at=?,session_id=? WHERE id=? AND execution_status='QUEUED'",
                    (self.id, now(), now(), "mock:" + run["id"], run["id"]))
                if changed.rowcount != 1:
                    continue
                self.service._event(connection, run["id"], "claimed", {"type": "running", "worker_id": self.id, "runtime": "mock"})
                return dict(connection.execute("SELECT * FROM task_runs WHERE id=?", (run["id"],)).fetchone())
        return None

    def finish(self, run, status, error_type=None, payload=None):
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT execution_status FROM task_runs WHERE id=? AND worker_id=?", (run["id"], self.id)).fetchone()
            if not current or current[0] not in {"RUNNING", "CANCEL_REQUESTED"}:
                return
            # A completed child may win the cancellation race. Keep its actual
            # result rather than declaring that its already-completed writes stopped.
            verification = "NOT_APPLICABLE" if status == "CANCELLED" else (
                "BLOCKED" if status == "UNKNOWN_OUTCOME" else "NOT_RUN")
            connection.execute("UPDATE task_runs SET execution_status=?,verification_status=?,error_type=?,ended_at=?,heartbeat_at=? WHERE id=?",
                               (status, verification, error_type, now(), now(), run["id"]))
            self.service._event(connection, run["id"], "terminal", {"type": status.lower(), "verification_status": verification, "error_type": error_type, **(payload or {})})

    def execute(self, run, recovery=False):
        spec = json.loads(run["frozen_inputs"])["spec"]
        steps = 0
        try:
            if not recovery:
                for message in MockAgent().messages(run):
                    steps += 1
                    if steps > spec["budget"]["max_steps"]:
                        self.finish(run, "FAILED", "STEP_LIMIT")
                        return
                    self.service.emit_event(run["project_id"], run["id"], f"mock-step-{steps}", message)
                if spec.get("extension_lock"):
                    steps += 1
                    if steps > spec["budget"]["max_steps"]:
                        self.finish(run, "FAILED", "STEP_LIMIT")
                        return
                    from packages.extensions.service import ExtensionRegistry
                    called = ExtensionRegistry(self.service, self.data_dir).invoke(spec["extension_lock"]["snapshot_id"], spec["goal"])
                    self.service.emit_event(run["project_id"], run["id"], "extension-result",
                        {"type": "extension_result", "snapshot_id": called["snapshot_id"], "result": called["result"],
                         "runtime_kind": "none", "sdk_plugin_live": "NOT_VALIDATED"})
            runner = TrustedFixtureRunner(self.service, self.data_dir)
            result = runner.observe(run, self.stop_event) if recovery else runner.execute(run, self.stop_event)
            self.service.emit_event(run["project_id"], run["id"], "tool-result", {"type": "tool_result", **result})
            reason = result["error_type"]
            if result.get("outcome_unknown"):
                self.finish(run, "UNKNOWN_OUTCOME", reason)
            elif reason == "CANCELLED":
                self.finish(run, "CANCELLED", reason)
            elif reason == "INTERRUPTED":
                self.finish(run, "INTERRUPTED", reason)
            elif reason or result["exit_code"] != 0:
                self.finish(run, "FAILED", reason or "TOOL_EXIT_NONZERO")
            else:
                self.finish(run, "RETURNED", payload={"candidate_paths": spec["expected_artifacts"], "runtime": "mock"})
        except Exception:
            # Preserve the platform failure outside the task's writable paths, with secrets masked.
            receipt_dir = self.data_dir / "receipts" / run["id"]
            receipt_dir.mkdir(parents=True, exist_ok=True)
            trace = traceback.format_exc()
            for key, value in os.environ.items():
                if value and len(value) >= 8 and any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                    trace = trace.replace(value, "[REDACTED]")
            with (receipt_dir / "worker-error.txt").open("x", encoding="utf-8") as stream:
                stream.write(trace)
            # Any platform failure after durable launch intent may have effects.
            current = self.service.run(run["project_id"], run["id"], include_inputs=False)
            self.finish(run, "UNKNOWN_OUTCOME" if current["runner_job_id"] else "FAILED", "WORKER_ERROR")

    def loop(self):
        try:
            while not self.stop_event.is_set():
                if self.recoveries:
                    self.execute(self.recoveries.pop(0), recovery=True)
                    continue
                if self.repair_coordinator:
                    self.repair_coordinator.tick()
                run = self.claim()
                if run:
                    self.execute(run)
                else:
                    self.stop_event.wait(0.1)
        finally:
            self.status = "OFFLINE"
