from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import hashlib
import platform

import psutil

from packages.domain.tasks import canonical, digest, now
from packages.adapters.mock_agent import MockAgent
from packages.domain.store import InvalidInput
from packages.runner.supervisor import atomic_json, encoded, identity, matching_process
from packages.domain.paths import resolve_data_path

# This is original platform-owned code. User data is passed as argv, never executed.
SCRIPT = """import json,pathlib,sys,time
mode,content,paths=sys.argv[1],sys.argv[2],json.loads(sys.argv[3])
if mode == 'wait_for_cancel': time.sleep(60)
if mode == 'failure':
    print('original-fixture-failure')
    raise SystemExit(7)
for name in paths:
    p=pathlib.Path(name)
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(content,encoding='utf-8')
print('original-candidate-written')
"""


class TrustedFixtureRunner:
    def __init__(self, service, data_dir):
        self.service = service
        self.data_dir = data_dir.resolve()

    @staticmethod
    def capabilities():
        return {"profile": "WindowsNativePython" if os.name == "nt" else ("WSL2Python" if "microsoft" in platform.release().lower() else "LinuxPython"),
                "python": platform.python_version(), "executable": sys.executable,
                "shell": False, "encoding": "utf-8", "process_identity": "pid+create_time",
                "scope": "trusted_original_fixtures", "timeout": True, "cancel_process_tree": True,
                "output_limit_bytes": 65536, "sandbox": "NOT_VALIDATED",
                "native_windows_qualification": "NOT_VALIDATED", "device_write": "DISABLED"}

    @staticmethod
    def command(run):
        spec = json.loads(run["frozen_inputs"])["spec"]
        proposal = MockAgent().tool_proposal(run)
        return [sys.executable, "-I", "-c", SCRIPT, proposal["behavior"],
                proposal["candidate_text"], canonical(spec["allowed_write_paths"])]

    def execute(self, run, stop_event):
        frozen = json.loads(run["frozen_inputs"])
        spec = frozen["spec"]
        workspace = self.data_dir / run["workspace"]
        if workspace.is_symlink() or not workspace.resolve().is_relative_to(self.data_dir):
            raise ValueError("Workspace escapes the task data directory")
        workspace.mkdir(parents=True, exist_ok=False)
        # Inputs are controller snapshots; this does not constitute an OS sandbox.
        (workspace / "frozen-inputs.json").write_text(canonical(frozen), encoding="utf-8")
        argv = self.command(run)
        job_id = str(uuid.uuid4())
        stdout_path, stderr_path = workspace / "stdout.txt", workspace / "stderr.txt"
        journal = self.data_dir / "runner-journal" / job_id
        journal.mkdir(parents=True, exist_ok=False)
        request = {"job_id": job_id, "run_id": run["id"], "argv": argv,
                   "workspace": str(workspace), "stdout_path": str(stdout_path),
                   "stderr_path": str(stderr_path), "timeout_seconds": spec["budget"]["timeout_seconds"],
                   "output_limit_bytes": 65536}
        request_path = journal / "request.json"
        with request_path.open("xb") as stream:
            stream.write(encoded(request))
            stream.flush()
            os.fsync(stream.fileno())
        request_hash = hashlib.sha256(request_path.read_bytes()).hexdigest()
        with self.service.connect() as connection:
            # Persist the intent BEFORE spawning. A crash in an ambiguous launch
            # window must never cause a second launch of this run.
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT execution_status FROM task_runs WHERE id=?", (run["id"],)).fetchone()[0]
            if state != "RUNNING" or stop_event.is_set():
                return {"runner_job_id": None, "exit_code": None, "error_type": "CANCELLED",
                        "workspace": str(workspace), "stdout_bytes": 0, "stderr_bytes": 0,
                        "sandbox": "NOT_VALIDATED"}
            connection.execute("""INSERT INTO runner_jobs
                (id,run_id,command_hash,workspace,status,started_at,stdout_path,stderr_path,journal_path,request_hash)
                VALUES (?,?,?,?,'STARTING',?,?,?,?,?)""",
                (job_id, run["id"], digest(argv), str(workspace), now(), str(stdout_path), str(stderr_path),
                 str(journal), request_hash))
            connection.execute("UPDATE task_runs SET runner_job_id=? WHERE id=?", (job_id, run["id"]))
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        env["PYTHONIOENCODING"] = "utf-8"
        kwargs = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        with self.service.connect() as connection:
            # Cancellation serializes with the actual spawn, after durable intent.
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT execution_status FROM task_runs WHERE id=?", (run["id"],)).fetchone()[0]
            if state != "RUNNING" or stop_event.is_set():
                atomic_json(journal / "abort.json", {"job_id": job_id, "run_id": run["id"],
                            "request_hash": request_hash, "launched": False, "error_type": "CANCELLED"})
                connection.execute("UPDATE runner_jobs SET status='FINISHED',ended_at=?,error_type='CANCELLED' WHERE id=?", (now(), job_id))
                return {"runner_job_id": job_id, "exit_code": None, "error_type": "CANCELLED",
                        "workspace": str(workspace), "stdout_bytes": 0, "stderr_bytes": 0,
                        "sandbox": "NOT_VALIDATED"}
            process = psutil.Popen([sys.executable, "-I", str(Path(__file__).with_name("supervisor.py")), str(request_path)],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True, **kwargs)
            supervisor = identity(process)
            connection.execute("UPDATE runner_jobs SET supervisor_pid=?,supervisor_created=? WHERE id=?",
                               (supervisor["pid"], supervisor["create_time"], job_id))
        try:
            return self.observe({**run, "runner_job_id": job_id}, stop_event)
        finally:
            if process.poll() is not None:
                process.wait()

    def inspect(self, run):
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM runner_jobs WHERE id=? AND run_id=?",
                                     (run.get("runner_job_id"), run["id"])).fetchone()
        if not row or not row["journal_path"]:
            return {"state": "UNKNOWN_OUTCOME", "reason": "NO_DURABLE_RUNNER_IDENTITY"}
        job = dict(row)
        try:
            journal = resolve_data_path(job["journal_path"], self.data_dir)
            if journal.is_symlink() or not journal.resolve().is_relative_to(self.data_dir / "runner-journal"):
                raise ValueError("Invalid journal path")
            request_path = journal / "request.json"
            if request_path.is_symlink():
                raise ValueError("Invalid request path")
            raw = request_path.read_bytes()
            request = json.loads(raw)
            if hashlib.sha256(raw).hexdigest() != job["request_hash"] or request["job_id"] != job["id"] or request["run_id"] != run["id"]:
                raise ValueError("Runner request changed")
            if digest(request["argv"]) != job["command_hash"] or any(request[key] != job[key] for key in ("workspace", "stdout_path", "stderr_path")):
                raise ValueError("Runner request scope changed")
            receipt_path = journal / "receipt.json"
            supervisor = {"pid": job["supervisor_pid"], "create_time": job["supervisor_created"]}
            abort_path = journal / "abort.json"
            if abort_path.exists():
                abort = json.loads(abort_path.read_bytes())
                if abort_path.is_symlink() or abort != {"job_id": job["id"], "run_id": run["id"],
                    "request_hash": job["request_hash"], "launched": False, "error_type": "CANCELLED"} or receipt_path.exists() or job["supervisor_pid"]:
                    raise ValueError("Conflicting launch facts")
                return {"state": "FINISHED", "job": job, "receipt": {"exit_code": None,
                        "error_type": "CANCELLED", "outputs": {}}}
            if not receipt_path.exists():
                if matching_process(supervisor):
                    return {"state": "LIVE", "phase": "STARTING", "job": job}
                return {"state": "UNKNOWN_OUTCOME", "reason": "NO_RESULT_RECEIPT", "job": job}
            if receipt_path.is_symlink():
                raise ValueError("Invalid receipt path")
            receipt = json.loads(receipt_path.read_bytes())
            if receipt["request_hash"] != job["request_hash"] or receipt["job_id"] != job["id"] or receipt["run_id"] != run["id"]:
                raise ValueError("Receipt scope changed")
            event = journal / f"event-{receipt['event_seq']:04d}.json"
            if event.is_symlink() or json.loads(event.read_bytes()) != receipt:
                raise ValueError("Receipt is not backed by its durable journal event")
            if job["supervisor_pid"] and receipt["supervisor"] != supervisor:
                raise ValueError("Supervisor identity changed")
            if receipt["phase"] == "FINISHED":
                if not isinstance(receipt["exit_code"], int):
                    raise ValueError("Missing actual exit code")
                for key in ("stdout_path", "stderr_path"):
                    path = resolve_data_path(job[key], self.data_dir)
                    if path.is_symlink() or not path.resolve().is_relative_to(resolve_data_path(job["workspace"], self.data_dir)):
                        raise ValueError("Output path changed")
                    raw = path.read_bytes()
                    if receipt["outputs"][path.name] != {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}:
                        raise ValueError("Runner output changed")
                return {"state": "FINISHED", "receipt": receipt, "job": job}
            if receipt["phase"] in {"STARTING", "RUNNING"} and matching_process(receipt["supervisor"]):
                return {"state": "LIVE", "phase": receipt["phase"], "receipt": receipt, "job": job}
            return {"state": "UNKNOWN_OUTCOME", "reason": "RUNNER_LOST_OR_STOP_UNCONFIRMED", "job": job}
        except (OSError, ValueError, KeyError, TypeError, InvalidInput):
            return {"state": "UNKNOWN_OUTCOME", "reason": "RUNNER_EVIDENCE_INVALID", "job": job}

    def observe(self, run, stop_event):
        heartbeat = time.monotonic()
        recorded_child = None
        requested_reason = None
        while True:
            fact = self.inspect(run)
            if fact["state"] != "LIVE":
                break
            job = fact["job"]
            current = self.service.run(run["project_id"], run["id"], include_inputs=False)
            reason = "CANCELLED" if current["execution_status"] == "CANCEL_REQUESTED" else (
                "INTERRUPTED" if stop_event.is_set() else None)
            if reason and reason != requested_reason:
                atomic_json(resolve_data_path(job["journal_path"], self.data_dir) / "cancel.json",
                            {"job_id": job["id"], "request_hash": job["request_hash"], "reason": reason})
                requested_reason = reason
            receipt = fact.get("receipt") or {}
            child = receipt.get("child") or {}
            if (child and child != recorded_child) or time.monotonic() - heartbeat >= 1:
                with self.service.connect() as connection:
                    connection.execute("UPDATE runner_jobs SET status='RUNNING',pid=?,child_created=? WHERE id=?",
                                       (child.get("pid"), child.get("create_time"), job["id"]))
                    connection.execute("UPDATE task_runs SET heartbeat_at=? WHERE id=? AND worker_id=?",
                                       (now(), run["id"], run["worker_id"]))
                heartbeat = time.monotonic()
                recorded_child = child
            time.sleep(0.03)
        job = fact.get("job")
        receipt = fact.get("receipt") or {}
        unknown = fact["state"] != "FINISHED"
        child = receipt.get("child") or {}
        if job:
            with self.service.connect() as connection:
                connection.execute("UPDATE runner_jobs SET status=?,pid=COALESCE(?,pid),child_created=COALESCE(?,child_created),ended_at=?,exit_code=?,error_type=? WHERE id=?",
                    ("UNKNOWN_OUTCOME" if unknown else "FINISHED", child.get("pid"), child.get("create_time"),
                     now(), receipt.get("exit_code"), fact.get("reason") if unknown else receipt.get("error_type"), job["id"]))
        outputs = receipt.get("outputs") or {}
        return {"runner_job_id": run.get("runner_job_id"), "exit_code": receipt.get("exit_code"),
                "error_type": fact.get("reason", "UNKNOWN_OUTCOME") if unknown else receipt.get("error_type"),
                "outcome_unknown": unknown, "workspace": job["workspace"] if job else str(self.data_dir / run["workspace"]),
                "stdout_bytes": outputs.get("stdout.txt", {}).get("bytes", 0),
                "stderr_bytes": outputs.get("stderr.txt", {}).get("bytes", 0), "sandbox": "NOT_VALIDATED"}
