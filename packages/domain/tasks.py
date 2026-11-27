from __future__ import annotations
from contextlib import nullcontext
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from packages.domain.store import Store, Conflict, InvalidInput, NotFound
from packages.domain.task_schema import TaskInput


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def source_hashes(source_root):
    """Use one portable filename representation for frozen code on every OS."""
    hashes = {}
    for folder in ('apps', 'packages'):
        for path in (source_root / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                hashes[path.relative_to(source_root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


class TaskService(Store):
    def __init__(self, database, source_root):
        super().__init__(database)
        self.source_root = source_root
        self.source_hash = digest(source_hashes(source_root))
        self.stack_hash = hashlib.sha256((source_root / "profiles/stack.lock.json").read_bytes()).hexdigest()
        # Direct-file development binds runs to source_hash, without invoking Git.
        # Retain the legacy field for existing frozen TaskSpec readers.
        self.base_commit = None

    def frozen_stack(self, project_id):
        from packages.extensions.service import ExtensionRegistry
        registry = ExtensionRegistry(self, self.database.parent)
        binding = registry.binding(project_id)
        if not binding:
            return {"base_stack_hash": self.stack_hash, "stack_lock_hash": self.stack_hash, "extension_lock": None}
        snapshot = registry.describe(binding["lock"]["snapshot_id"])
        if not snapshot["qualified"]:
            raise Conflict("Pinned extension snapshot is missing, changed or no longer qualified")
        combination = {"base_stack_hash": self.stack_hash, "extension_lock": binding["lock"]}
        return {**combination, "stack_lock_hash": digest(combination)}

    def stack_current(self, spec):
        if not spec.get("extension_lock"):
            return spec["stack_lock_hash"] == self.stack_hash
        if spec.get("base_stack_hash") != self.stack_hash:
            return False
        combination = {"base_stack_hash": spec["base_stack_hash"], "extension_lock": spec["extension_lock"]}
        if digest(combination) != spec["stack_lock_hash"]:
            return False
        from packages.extensions.service import ExtensionRegistry
        try:
            snapshot = ExtensionRegistry(self, self.database.parent).describe(spec["extension_lock"]["snapshot_id"])
        except (NotFound, InvalidInput):
            return False
        return snapshot["qualified"] and snapshot["files_sha256"] == spec["extension_lock"]["files_sha256"] and snapshot["manifest"] == spec["extension_lock"]["manifest"]

    def _requirements_current(self, project_id, refs):
        rows = []
        for ref in refs:
            row = self.requirements(project_id, ref["requirement_id"], ref["revision"])
            if row["revision"] != row["current_revision"] or row["sha256"] != ref["sha256"] or row["source_status"] == "STALE":
                raise Conflict("Referenced requirements or sources are stale")
            if not row["payload"]["confirmed"]:
                raise InvalidInput("Task requirements need human confirmation")
            rows.append(row)
        return rows

    def _acyclic(self, connection, project_id, task_id, dependencies):
        heads = connection.execute("""SELECT t.id,v.spec FROM tasks t JOIN task_versions v
            ON v.task_id=t.id AND v.revision=t.current_revision WHERE t.project_id=?""", (project_id,)).fetchall()
        graph = {r["id"]: [d["task_id"] for d in json.loads(r["spec"])["dependencies"]] for r in heads}
        graph[task_id] = dependencies
        visiting, visited = set(), set()

        def visit(node):
            if node in visiting:
                raise InvalidInput("Task dependency cycle is not allowed")
            if node in visited:
                return
            visiting.add(node)
            for parent in graph.get(node, []):
                visit(parent)
            visiting.remove(node)
            visited.add(node)
        for node in graph:
            visit(node)

    def save_task(self, project_id, body, task_id=None, expected_revision=0):
        body = TaskInput.model_validate(body).model_dump()
        self.project(project_id)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            requirements = self._requirements_current(project_id, body["requirement_refs"])
            if task_id:
                head = connection.execute("SELECT * FROM tasks WHERE id=? AND project_id=?", (task_id, project_id)).fetchone()
                if not head:
                    raise NotFound("Task does not exist in this project")
                if head["current_revision"] != expected_revision:
                    raise Conflict("Task changed; reload its current revision")
                revision = expected_revision + 1
            else:
                task_id, revision = str(uuid.uuid4()), 1
            dependencies = []
            for dependency_id in body["dependencies"]:
                parent = connection.execute("SELECT * FROM tasks WHERE id=? AND project_id=?", (dependency_id, project_id)).fetchone()
                if not parent:
                    raise InvalidInput("Dependencies must exist in the same project")
                if self.task(project_id, dependency_id)["review_status"] != "CURRENT":
                    raise Conflict("A dependency refers to stale requirements or code")
                dependencies.append({"task_id": dependency_id, "task_revision": parent["current_revision"],
                                     "required_verification": "PASSED"})
            self._acyclic(connection, project_id, task_id, body["dependencies"])
            acceptance = [{"requirement_id": row["id"], "revision": row["revision"],
                           "rules": row["payload"]["acceptance"]} for row in requirements]
            baseline = self.source_root / "packages/verifier/original_baseline.py"
            fixture_profile = {"id": "original-candidate-equals-goal-v1", "source_kind": "synthetic",
                               "baseline_hash": hashlib.sha256(baseline.read_bytes()).hexdigest(),
                               "test_ids": [f"original_artifact_{i+1}" for i in range(len(body["allowed_write_paths"]))]}
            input_refs = list({(r["document_id"], r["revision"]): r for row in requirements for r in row["sources"]}.values())
            if len(input_refs) > 20:
                raise InvalidInput("A task can freeze at most 20 source documents")
            spec = {"schema_version": "eeas.task-spec.v1", "task_id": task_id, "project_id": project_id,
                    "task_revision": revision, "goal": body["goal"], "requirement_refs": body["requirement_refs"],
                    "contract_refs": [{"path": name, "sha256": hashlib.sha256((self.source_root / name).read_bytes()).hexdigest()}
                                      for name in ("contracts/task.md", "contracts/state.md")],
                    "input_refs": input_refs, "base_commit": self.base_commit, "source_hash": self.source_hash,
                    "workspace": "per-run directory assigned at transaction acceptance",
                    "allowed_write_paths": body["allowed_write_paths"], "dependencies": dependencies,
                    "required_capabilities": ["mock-agent", "trusted-original-fixture"],
                    "runtime_profile": {"adapter": "mock", "version": "1", "behavior": body["mock_behavior"]},
                    "runner_profile": {"name": "trusted-original-python", "sandbox": "NOT_VALIDATED"},
                    "budget": {"timeout_seconds": body["timeout_seconds"], "max_steps": body["max_steps"],
                               "max_repairs": body["max_repairs"], "max_tokens": None, "max_cost": None},
                    "acceptance_ref": acceptance, "verification_profile": fixture_profile,
                    "acceptance_hash": digest({"mappings": acceptance, "fixture_profile": fixture_profile}),
                    "expected_artifacts": body["allowed_write_paths"], "stack_lock_hash": self.stack_hash}
            spec["approval_required"] = body["approval_required"]
            spec.update(self.frozen_stack(project_id))
            spec["approval_ttl_seconds"] = body["approval_ttl_seconds"]
            if expected_revision:
                connection.execute("UPDATE tasks SET current_revision=? WHERE id=?", (revision, task_id))
            else:
                connection.execute("INSERT INTO tasks VALUES (?,?,?)", (task_id, project_id, revision))
            connection.execute("INSERT INTO task_versions VALUES (?,?,?,?,?)", (task_id, revision, canonical(spec), digest(spec), now()))
            for parent in dependencies:
                connection.execute("INSERT INTO task_dependencies VALUES (?,?,?,?)",
                                   (task_id, revision, parent["task_id"], parent["task_revision"]))
        return self.task(project_id, task_id, revision)

    def task(self, project_id, task_id, revision=None):
        with self.connect() as connection:
            head = connection.execute("SELECT * FROM tasks WHERE id=? AND project_id=?", (task_id, project_id)).fetchone()
            if not head:
                raise NotFound("Task does not exist in this project")
            row = connection.execute("SELECT * FROM task_versions WHERE task_id=? AND revision=?",
                                     (task_id, head["current_revision"] if revision is None else revision)).fetchone()
            if not row:
                raise NotFound("Task revision does not exist")
        spec = json.loads(row["spec"])
        review = "CURRENT"
        try:
            self._requirements_current(project_id, spec["requirement_refs"])
        except (Conflict, InvalidInput, NotFound):
            review = "NEEDS_REVIEW"
        if spec["source_hash"] != self.source_hash or not self.stack_current(spec):
            review = "NEEDS_REVIEW"
        return {"id": task_id, "project_id": project_id, "revision": row["revision"], "current_revision": head["current_revision"],
                "spec": spec, "sha256": row["sha256"], "review_status": review}

    def tasks(self, project_id):
        self.project(project_id)
        with self.connect() as connection:
            ids = [r[0] for r in connection.execute("SELECT id FROM tasks WHERE project_id=? ORDER BY id", (project_id,))]
        return [self.task(project_id, key) for key in ids]

    def start_run(self, project_id, task_id, revision, operation_id, *, transaction=None):
        request_hash = digest({"task_id": task_id, "task_revision": revision})
        with (self.connect() if transaction is None else nullcontext(transaction)) as connection:
            if transaction is None:
                connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT * FROM task_runs WHERE project_id=? AND operation_id=?", (project_id, operation_id)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise Conflict("Operation ID is already bound to different input")
                return dict(old)
            task = self.task(project_id, task_id, revision)
            if task["revision"] != task["current_revision"] or task["review_status"] != "CURRENT":
                raise Conflict("Task or requirements changed; create a reviewed revision")
            spec = task["spec"]
            requirements = self._requirements_current(project_id, spec["requirement_refs"])
            from packages.domain.questions import KnowledgeBlocked, knowledge_gate
            knowledge = knowledge_gate(connection, project_id, task_id, revision, task["sha256"])
            if knowledge["status"] != "READY":
                raise KnowledgeBlocked(knowledge)
            documents = []
            for ref in spec["input_refs"]:
                doc = self.document(project_id, ref["document_id"], ref["revision"])
                documents.append({"document_id": doc["id"], "revision": doc["revision"], "sha256": doc["sha256"],
                                  "raw_base64": base64.b64encode(doc["raw_bytes"]).decode("ascii"),
                                  "parse_receipt": self.document_receipt(project_id, doc["id"], doc["revision"])})
            run_id = str(uuid.uuid4())
            attempt = connection.execute("SELECT coalesce(max(attempt_id),0)+1 FROM task_runs WHERE task_id=? AND task_revision=?",
                                         (task_id, revision)).fetchone()[0]
            workspace = f"workspaces/{project_id}/{task_id}/{run_id}"
            frozen = {"spec": spec, "requirements": requirements, "documents": documents,
                      "verification": "NOT_RUN", "runtime": "mock", "source": "synthetic"}
            connection.execute("""INSERT INTO task_runs
                (id,project_id,task_id,task_revision,attempt_id,operation_id,request_hash,spec_hash,stack_lock_hash,
                 frozen_inputs,workspace,execution_status,verification_status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'QUEUED','NOT_RUN',?)""",
                (run_id, project_id, task_id, revision, attempt, operation_id, request_hash, task["sha256"],
                 spec["stack_lock_hash"], canonical(frozen), workspace, now()))
            # Freeze the actual approved executable baseline before publishing QUEUED.
            controller_dir = self.database.parent / "verification" / run_id
            controller_dir.mkdir(parents=True, exist_ok=False)
            baseline_path = controller_dir / "approved-baseline.py"
            baseline_path.write_bytes((self.source_root / "packages/verifier/original_baseline.py").read_bytes())
            expected_path = controller_dir / "expected.json"
            expected_path.write_text(canonical({"files": spec["expected_artifacts"], "content": spec["goal"] + "\n"}), encoding="utf-8")
            connection.execute("INSERT INTO verification_baselines VALUES (?,?,?,?,?,?)",
                (run_id, str(baseline_path), spec["verification_profile"]["baseline_hash"], str(expected_path),
                 hashlib.sha256(expected_path.read_bytes()).hexdigest(), canonical(spec["verification_profile"]["test_ids"])))
            self._event(connection, run_id, "queued", {"type": "queued", "runtime": "mock", "verification_status": "NOT_RUN"})
            if spec["approval_required"]:
                from packages.domain.approvals import Approvals
                run = dict(connection.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone())
                approval_id = Approvals.create(connection, run, spec["approval_ttl_seconds"])
                connection.execute("UPDATE task_runs SET execution_status='AWAITING_APPROVAL' WHERE id=?", (run_id,))
                self._event(connection, run_id, "awaiting-approval", {"type": "awaiting_approval", "approval_id": approval_id})
            return dict(connection.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone())

    def run(self, project_id, run_id, include_inputs=True):
        with self.connect() as connection:
            columns = "*" if include_inputs else "id,project_id,task_id,task_revision,attempt_id,operation_id,spec_hash,stack_lock_hash,workspace,execution_status,verification_status,worker_id,created_at,started_at,ended_at,error_type,runner_job_id,session_id,heartbeat_at"
            row = connection.execute(f"SELECT {columns} FROM task_runs WHERE id=? AND project_id=?", (run_id, project_id)).fetchone()
            if not row:
                raise NotFound("Run does not exist in this project")
            return dict(row)

    def runs(self, project_id, task_id):
        self.task(project_id, task_id)
        with self.connect() as connection:
            return [dict(r) for r in connection.execute("SELECT * FROM task_runs WHERE project_id=? AND task_id=? ORDER BY created_at,id", (project_id, task_id))]

    def _event(self, connection, run_id, key, payload):
        encoded, hashed = canonical(payload), digest(payload)
        old = connection.execute("SELECT event_seq,payload_hash FROM run_events WHERE run_id=? AND event_key=?", (run_id, key)).fetchone()
        if old:
            if old["payload_hash"] != hashed:
                raise Conflict("Event key is already bound to another payload")
            return old["event_seq"]
        sequence = connection.execute("SELECT coalesce(max(event_seq),0)+1 FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
        connection.execute("INSERT INTO run_events VALUES (?,?,?,?,?,?)", (run_id, sequence, key, encoded, hashed, now()))
        return sequence

    def emit_event(self, project_id, run_id, key, payload):
        self.run(project_id, run_id)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._event(connection, run_id, key, payload)

    def events(self, project_id, run_id, after=0):
        self.run(project_id, run_id)
        with self.connect() as connection:
            return [{"run_id": run_id, "event_seq": row["event_seq"], "event_key": row["event_key"],
                     "payload": json.loads(row["payload"]), "created_at": row["created_at"]}
                    for row in connection.execute("SELECT * FROM run_events WHERE run_id=? AND event_seq>? ORDER BY event_seq LIMIT 1000", (run_id, after))]

    def cancel(self, project_id, run_id):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM task_runs WHERE id=? AND project_id=?", (run_id, project_id)).fetchone()
            if not row:
                raise NotFound("Run does not exist in this project")
            if row["execution_status"] in {"QUEUED", "AWAITING_APPROVAL"}:
                connection.execute("UPDATE task_runs SET execution_status='CANCELLED',verification_status='NOT_APPLICABLE',ended_at=? WHERE id=?", (now(), run_id))
                self._event(connection, run_id, "cancelled-before-start", {"type": "cancelled", "started": False})
            elif row["execution_status"] == "RUNNING":
                connection.execute("UPDATE task_runs SET execution_status='CANCEL_REQUESTED' WHERE id=?", (run_id,))
                self._event(connection, run_id, "cancel-requested", {"type": "cancel_requested"})
        return self.run(project_id, run_id)
