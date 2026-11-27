import json
import uuid

from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import now


class RepairCoordinator:
    """Durable bounded refinement; the single Worker still owns all execution."""
    def __init__(self, service, verifier):
        self.service, self.verifier = service, verifier

    def request(self, project_id, run_id, max_repairs):
        run = self.service.run(project_id, run_id)
        self.verifier._current(run)
        budget = json.loads(run["frozen_inputs"])["spec"]["budget"]["max_repairs"]
        if not 1 <= max_repairs <= min(3, budget):
            raise InvalidInput("Repair count exceeds the frozen budget (at most 3)")
        if run["verification_status"] != "FAILED":
            raise InvalidInput("Repair requires independent failed verification evidence")
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT * FROM repair_sessions WHERE initial_run_id=? ORDER BY created_at LIMIT 1", (run_id,)).fetchone()
            if old:
                if old["max_repairs"] != max_repairs:
                    raise Conflict("Repair request is already bound to another budget")
                return dict(old)
            session_id = str(uuid.uuid4())
            connection.execute("INSERT INTO repair_sessions VALUES (?,?,?,?,?,?,?,0,'RUNNING',?,NULL)",
                (session_id, project_id, run["task_id"], run["task_revision"], run_id, run_id, max_repairs, now()))
        return self.inspect(project_id, session_id)

    def inspect(self, project_id, session_id):
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM repair_sessions WHERE id=? AND project_id=?", (session_id, project_id)).fetchone()
        if not row:
            raise NotFound("Repair session does not exist in this project")
        return dict(row)

    def tick(self):
        with self.service.connect() as connection:
            sessions = [dict(r) for r in connection.execute("SELECT * FROM repair_sessions WHERE status='RUNNING' ORDER BY created_at")]
        for session in sessions:
            run = self.service.run(session["project_id"], session["current_run_id"])
            if run["execution_status"] in {"QUEUED", "AWAITING_APPROVAL", "RUNNING", "CANCEL_REQUESTED"}:
                continue
            if run["execution_status"] in {"UNKNOWN_OUTCOME", "INTERRUPTED", "CANCELLED"}:
                self.finish(session, "BLOCKED")
                continue
            try:
                evidence = self.verifier.verify(session["project_id"], run["id"])
            except (Conflict, InvalidInput):
                self.finish(session, "BLOCKED")
                continue
            if evidence["association_status"] != "CURRENT":
                self.finish(session, "BLOCKED")
            elif evidence["payload"]["result"] == "PASSED":
                self.finish(session, "COMPLETE")
            elif session["repairs_started"] >= session["max_repairs"]:
                self.finish(session, "EXHAUSTED")
            else:
                ordinal = session["repairs_started"] + 1
                new_run = self.service.start_run(session["project_id"], session["task_id"], session["task_revision"], f"repair_{session['id']}_{ordinal}")
                with self.service.connect() as connection:
                    connection.execute("UPDATE repair_sessions SET current_run_id=?,repairs_started=? WHERE id=? AND status='RUNNING'",
                                       (new_run["id"], ordinal, session["id"]))

    def finish(self, session, status):
        with self.service.connect() as connection:
            connection.execute("UPDATE repair_sessions SET status=?,ended_at=? WHERE id=?", (status, now(), session["id"]))
