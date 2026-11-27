from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now


def action_for(run):
    from packages.runner.fixture import TrustedFixtureRunner
    spec = json.loads(run["frozen_inputs"])["spec"]
    return {"kind": "trusted-original-python", "run_id": run["id"], "task_id": run["task_id"],
            "task_revision": run["task_revision"], "goal": spec["goal"],
            "argv_hash": digest(TrustedFixtureRunner.command(run)),
            "input_hash": hashlib.sha256(run["frozen_inputs"].encode()).hexdigest(),
            "spec_hash": run["spec_hash"], "stack_lock_hash": run["stack_lock_hash"],
            "resource": run["workspace"], "write_paths": spec["allowed_write_paths"], "budget": spec["budget"]}


class Approvals:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def create(connection, run, ttl):
        action = action_for(run)
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        approval_id = str(uuid.uuid4())
        connection.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,'PENDING',?,NULL,NULL)",
            (approval_id, run["project_id"], run["id"], canonical(action), digest(action), action["input_hash"],
             run["spec_hash"], expires, now()))
        return approval_id

    def inspect(self, project_id, approval_id):
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id=? AND project_id=?", (approval_id, project_id)).fetchone()
        if not row:
            raise NotFound("Approval does not exist in this project")
        return {**dict(row), "action": json.loads(row["action"])}

    def list(self, project_id):
        self.service.project(project_id)
        with self.service.connect() as connection:
            ids = [r[0] for r in connection.execute("SELECT id FROM approvals WHERE project_id=? ORDER BY created_at", (project_id,))]
        return [self.inspect(project_id, key) for key in ids]

    def _valid(self, row, run):
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            raise Conflict("APPROVAL_EXPIRED")
        task = self.service.task(run["project_id"], run["task_id"], run["task_revision"])
        if task["current_revision"] != run["task_revision"] or task["review_status"] != "CURRENT":
            raise Conflict("STALE_APPROVAL: input or code version changed")
        if digest(action_for(run)) != row["action_hash"] or task["sha256"] != row["spec_hash"]:
            raise Conflict("APPROVAL_SCOPE_CHANGED")

    def decide(self, project_id, approval_id, action_hash, approve):
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM approvals WHERE id=? AND project_id=?", (approval_id, project_id)).fetchone()
            if not row:
                raise NotFound("Approval does not exist in this project")
            if row["action_hash"] != action_hash:
                raise Conflict("APPROVAL_SCOPE_CHANGED")
            run = dict(connection.execute("SELECT * FROM task_runs WHERE id=?", (row["run_id"],)).fetchone())
            self._valid(row, run)
            if row["status"] in {"APPROVED", "CONSUMED"} and approve:
                return {**dict(row), "action": json.loads(row["action"])}
            if row["status"] != "PENDING" or run["execution_status"] != "AWAITING_APPROVAL":
                raise Conflict("Approval is no longer pending")
            status = "APPROVED" if approve else "REJECTED"
            connection.execute("UPDATE approvals SET status=?,approved_at=? WHERE id=?", (status, now(), approval_id))
            connection.execute("UPDATE task_runs SET execution_status=?,verification_status=? WHERE id=?",
                ("QUEUED" if approve else "CANCELLED", "NOT_RUN" if approve else "NOT_APPLICABLE", run["id"]))
            self.service._event(connection, run["id"], "approval-decision", {"type": "approval", "decision": status, "action_hash": action_hash})
        return self.inspect(project_id, approval_id)

    def consume(self, connection, run):
        row = connection.execute("SELECT * FROM approvals WHERE run_id=?", (run["id"],)).fetchone()
        if not row or row["status"] != "APPROVED":
            raise Conflict("APPROVAL_REQUIRED")
        self._valid(row, run)
        changed = connection.execute("UPDATE approvals SET status='CONSUMED',consumed_at=? WHERE id=? AND status='APPROVED'", (now(), row["id"]))
        if changed.rowcount != 1:
            raise Conflict("Approval was already consumed")
