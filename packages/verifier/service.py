from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid

from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, now
from packages.domain.paths import resolve_data_path


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Verifier:
    def __init__(self, service, data_dir):
        self.service = service
        self.data_dir = data_dir.resolve()

    def _current(self, run):
        task = self.service.task(run["project_id"], run["task_id"], run["task_revision"])
        if task["current_revision"] != run["task_revision"] or task["review_status"] != "CURRENT" or task["sha256"] != run["spec_hash"]:
            raise Conflict("STALE_EVIDENCE: task, requirement, source or stack version changed")
        return task

    def artifacts(self, run):
        spec = json.loads(run["frozen_inputs"])["spec"]
        workspace = resolve_data_path(run["workspace"], self.data_dir)
        if not workspace.is_relative_to(self.data_dir):
            raise InvalidInput("Artifact workspace escaped controller data")
        artifacts = {}
        for name in spec["expected_artifacts"]:
            path = workspace / name
            if path.is_symlink() or not path.resolve().is_relative_to(workspace):
                raise InvalidInput("INTEGRITY_ERROR: artifact path escaped the workspace")
            artifacts[name] = file_hash(path) if path.is_file() else None
        return artifacts

    def verify(self, project_id, run_id):
        try:
            return self._verify(project_id, run_id)
        except (OSError, InvalidInput, ValueError, KeyError):
            with self.service.connect() as connection:
                changed = connection.execute("UPDATE task_runs SET verification_status='BLOCKED',error_type='VERIFIER_ERROR' WHERE id=? AND project_id=? AND verification_status='RUNNING'",
                                             (run_id, project_id))
                if changed.rowcount:
                    self.service._event(connection, run_id, "verification-error-" + str(uuid.uuid4()),
                                        {"type": "verification_blocked", "reason": "platform_or_integrity_error"})
            raise

    def _verify(self, project_id, run_id):
        run = self.service.run(project_id, run_id)
        task = self._current(run)
        if run["execution_status"] not in {"RETURNED", "FAILED"}:
            raise InvalidInput("Only a terminal candidate or failed execution can enter verification")
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            baseline = connection.execute("SELECT * FROM verification_baselines WHERE run_id=?", (run_id,)).fetchone()
            previous = connection.execute("SELECT id FROM verification_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
            if previous:
                return self.evidence(project_id, previous["id"])
            state = connection.execute("SELECT verification_status FROM task_runs WHERE id=?", (run_id,)).fetchone()[0]
            if state == "RUNNING":
                raise Conflict("Independent verification is already running")
            connection.execute("UPDATE task_runs SET verification_status='RUNNING' WHERE id=?", (run_id,))
        if not baseline:
            raise InvalidInput("Approved pre-run baseline is missing")
        receipt_dir = self.data_dir / "verification" / run_id / str(uuid.uuid4())
        receipt_dir.mkdir(parents=True, exist_ok=False)
        stdout, stderr, report = receipt_dir / "stdout.txt", receipt_dir / "stderr.txt", receipt_dir / "tests.json"
        started = now()
        result, error_type, exit_code, command = "FAILED", None, None, None
        code_path = resolve_data_path(baseline["baseline_path"], self.data_dir)
        expected_path = resolve_data_path(baseline["expected_path"], self.data_dir)
        artifacts = self.artifacts(run)
        if not code_path.is_file() or not expected_path.is_file() or file_hash(code_path) != baseline["baseline_hash"] or file_hash(expected_path) != baseline["expected_hash"]:
            error_type = "INTEGRITY_ERROR"
            tests = None
        else:
            command = [sys.executable, "-I", str(code_path), str(expected_path), str(self.data_dir / run["workspace"]), str(report)]
            env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
            with stdout.open("xb") as output, stderr.open("xb") as errors:
                try:
                    process = subprocess.run(command, cwd=receipt_dir, env=env, stdin=subprocess.DEVNULL,
                                             stdout=output, stderr=errors, timeout=10, check=False)
                    exit_code = process.returncode
                except subprocess.TimeoutExpired:
                    error_type = "TEST_TIMEOUT"
            tests = json.loads(report.read_text()) if report.is_file() else None
            required = json.loads(baseline["test_ids"])
            if tests and tests["test_ids"] == required and tests["tests_run"] == len(required) and tests["skipped"] == 0 and tests["successful"] and exit_code == 0:
                result = "PASSED"
            else:
                error_type = error_type or "MANDATORY_TEST_FAILED"
        # Evidence names exact files and hashes; model-authored verdict files are never consumed.
        evidence_id = str(uuid.uuid4())
        payload = {"schema_version": "eeas.evidence.v1", "evidence_id": evidence_id, "run_id": run_id,
                   "attempt_id": run["attempt_id"], "task_revision": run["task_revision"],
                   "test_id": json.loads(baseline["test_ids"]), "acceptance_hash": task["spec"]["acceptance_hash"],
                   "source_hash": task["spec"]["source_hash"], "commit": task["spec"]["base_commit"],
                   "artifact_hash": artifacts, "stack_lock_hash": run["stack_lock_hash"],
                   "runner": "controller-original-python-baseline", "environment": "Windows" if os.name == "nt" else "WSL2/Linux",
                   "source_kind": "synthetic", "runtime_kind": "mock", "started_at": started, "ended_at": now(),
                   "command": command, "exit_code": exit_code, "stdout_path": str(stdout) if stdout.exists() else None,
                   "stderr_path": str(stderr) if stderr.exists() else None, "test_report": tests,
                   "baseline_hash": baseline["baseline_hash"], "expected_hash": baseline["expected_hash"],
                   "result": result, "error_type": error_type,
                   "limitations": ["Original fixture functional validation only", "OS isolation NOT_VALIDATED",
                                   "No live model or business engineering qualification"]}
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO verification_evidence VALUES (?,?,?,?,?,?)", (evidence_id, project_id, run_id, canonical(payload), result, now()))
            connection.execute("UPDATE task_runs SET verification_status=? WHERE id=?", (result, run_id))
            self.service._event(connection, run_id, "verification-terminal", {"type": "verification", "result": result,
                "evidence_id": evidence_id, "error_type": error_type, "source_kind": "synthetic"})
        return self.evidence(project_id, evidence_id)

    def evidence(self, project_id, evidence_id):
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM verification_evidence WHERE id=? AND project_id=?", (evidence_id, project_id)).fetchone()
        if not row:
            raise NotFound("Evidence does not exist in this project")
        payload = json.loads(row["payload"])
        run = self.service.run(project_id, row["run_id"])
        try:
            self._current(run)
        except Conflict:
            association = "STALE_EVIDENCE"
        else:
            association = "CURRENT" if self.artifacts(run) == payload["artifact_hash"] else "INTEGRITY_ERROR"
            with self.service.connect() as connection:
                baseline = connection.execute("SELECT * FROM verification_baselines WHERE run_id=?", (run["id"],)).fetchone()
            code_path = resolve_data_path(baseline["baseline_path"], self.data_dir) if baseline else None
            expected_path = resolve_data_path(baseline["expected_path"], self.data_dir) if baseline else None
            if not baseline or not code_path.is_file() or not expected_path.is_file() or file_hash(code_path) != payload["baseline_hash"] or file_hash(expected_path) != payload["expected_hash"]:
                association = "INTEGRITY_ERROR"
        return {"payload": payload, "association_status": association}

    def fetch_artifact(self, project_id, run_id, filename):
        run = self.service.run(project_id, run_id)
        if filename not in json.loads(run["frozen_inputs"])["spec"]["expected_artifacts"]:
            raise NotFound("Artifact is not in the approved output set")
        self.artifacts(run)
        with self.service.connect() as connection:
            proof = connection.execute("SELECT id FROM verification_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        if proof and self.evidence(project_id, proof["id"])["association_status"] == "INTEGRITY_ERROR":
            raise InvalidInput("INTEGRITY_ERROR: candidate bytes differ from the recorded evidence")
        path = self.data_dir / run["workspace"] / filename
        if not path.is_file():
            raise NotFound("Artifact is missing")
        return path
