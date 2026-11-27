"""Explicit source-bound workflow probes, outside default pytest discovery.

These exercise EEAS using captured source as DATA and platform-owned original
Mock candidates. They do not execute upstream code, call a model, certify the
source/license, qualify an OS sandbox, or establish Windows compatibility.
Run one bound node through record_slice and the public_campaign pytest plugin.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote

import pytest

from campaign_probes import Evidence, ROOT, ingest, load_binding, sha
from packages.domain.tasks import TaskService, digest
from packages.runner.worker import Worker
from packages.runner.supervisor import matching_process
from scripts.public_campaign import PROVENANCE_MAX_BYTES, provenance_path, provenance_read, validate_provenance
from test_worker import until


def source_bodies(captured, evidence):
    """Resolve declared frozen provenance, never generated composite wrappers."""
    declared = captured["source"].get("provenance")
    composite = captured["text"].startswith("EEAS PUBLIC AUDIT MATERIAL\n")
    if not declared:
        if composite:
            raise ValueError("Composite source requires frozen original-body provenance; generated wrapper is not original content")
        return None
    path = provenance_path(captured["path"].parent, declared["path"])
    relative = path.relative_to(ROOT).as_posix()
    if captured["binding"].get("provenance_path") != relative:
        raise ValueError("Provenance path differs from the frozen case binding")
    freeze = json.loads((evidence.root / "freeze.json").read_bytes())["code_sha256"]
    raw = provenance_read(path, PROVENANCE_MAX_BYTES)
    if sha(raw) != declared["sha256"] or freeze.get(relative) != sha(raw):
        raise ValueError("Original-body provenance is changed or was not frozen before execution")
    sidecar, originals = validate_provenance(raw, captured["raw"], path.parent,
        origin=captured["source"]["origin"], revision=captured["source"]["revision"])
    for index, item in enumerate(sidecar["selected"]):
        original_path = provenance_path(path.parent, item["path"])
        original_raw = originals[item["path"]]
        assert freeze.get(original_path.relative_to(ROOT).as_posix()) == sha(original_raw) == item["sha256"]
        evidence.save(f"actual-original-body-{index}.bin", original_raw)
    evidence.save("actual-original-body-provenance.json", sidecar)
    return sidecar["selected"]


def source_lines(captured, evidence):
    """Return bounded first/last whole original lines with explicit coordinates.

    A converter-added terminal LF is excluded; the original EOF line must still
    equal one complete original splitlines(keepends=True) entry, never a fragment.
    """
    bodies = source_bodies(captured, evidence)
    candidates, byte_start, char_start = [], 0, 0
    for number, document_line in enumerate(captured["text"].splitlines(keepends=True), 1):
        content = document_line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
        mapped = None if bodies is None else next((item for item in bodies
            if item["body"]["line_start"] <= number <= item["body"]["line_end"]
            and item["body"]["char_start"] <= char_start
            and char_start + len(content) <= item["body"]["char_end_exclusive"]), None)
        if content.strip() and len(content) <= 1200 and (bodies is None or mapped is not None):
            physical = document_line
            if mapped:
                physical = document_line[:mapped["body"]["char_end_exclusive"] - char_start]
                original_number = mapped["original"]["line_start"] + number - mapped["body"]["line_start"]
                original_text = captured["text"][mapped["body"]["char_start"]:mapped["body"]["char_end_exclusive"]]
                assert physical == original_text.splitlines(keepends=True)[original_number - 1]
            raw = physical.encode()
            selected = {"line": number, "content": content, "physical_line": physical,
                        "char_start": char_start, "char_end": char_start + len(physical),
                        "byte_start": byte_start, "byte_end": byte_start + len(raw), "sha256": sha(raw)}
            if mapped:
                original_char = mapped["original"]["char_start"] + char_start - mapped["body"]["char_start"]
                original_byte = mapped["original"]["byte_start"] + byte_start - mapped["body"]["byte_start"]
                selected["original_source"] = {"path": mapped["source_path"], "sha256": mapped["sha256"],
                    "source_url": mapped["source_url"], "archive_member": mapped["archive_member"],
                    "line": original_number, "char_start": original_char, "char_end": original_char + len(physical),
                    "byte_start": original_byte, "byte_end": original_byte + len(raw),
                    "generated_terminal_separator_excluded": physical != document_line}
            if bodies is None or any(char.isalnum() for char in content):
                candidates.append(selected)
        byte_start += len(document_line.encode())
        char_start += len(document_line)
    assert byte_start == len(captured["raw"]) and char_start == len(captured["text"])
    assert candidates, "no whole source line fits the bounded manual fixture; no padding or truncation"
    selected = [candidates[0]] + ([candidates[-1]] if candidates[-1] != candidates[0] else [])
    for line in selected:
        assert captured["raw"][line["byte_start"]:line["byte_end"]] == line["physical_line"].encode()
    return selected


class Workflow:
    def __init__(self, client, tmp_path, case_id):
        self.client, self.tmp_path = client, tmp_path
        self.captured = load_binding(case_id)
        self.evidence = Evidence(self.captured)
        self.calls, self.persisted = [], False
        self.service = client.app.state.store
        self.worker = client.app.state.worker
        self.pid, self.doc, self.doc_route = ingest(client, self.captured, self.evidence)
        self.base = f"/api/projects/{self.pid}"
        self.lines = source_lines(self.captured, self.evidence)
        self.goal = "\n".join(["ORIGINAL_MOCK: source-bound EEAS workflow; upstream engineering NOT_RUN",
                               "parentRawSHA=" + sha(self.captured["raw"]),
                               "sourceProject=" + self.captured["source"]["project_id"],
                               *[f"sourceLine[{line['line']}]={line['content']}" for line in self.lines]])
        assert len(self.goal) <= 4000
        self.payload = {
            "schema_version": "eeas.requirements.v1", "title": "Original Mock source-bound workflow",
            "requirements": [{"id": f"R{i + 1}", "text": line["content"], "source_refs": [{
                "document_id": self.doc["id"], "revision": self.doc["revision"], "sha256": self.doc["sha256"],
                "line_start": line["line"], "line_end": line["line"]}]} for i, line in enumerate(self.lines)],
            "assumptions": ["Manual original test candidate; source text is data, never executable code"],
            "questions": ["Upstream semantic completion, origin/license and company models remain unverified"],
            "acceptance": [{"id": "A1", "text": "Original protected candidate-equals-goal test only",
                            "requirement_ids": [f"R{i + 1}" for i in range(len(self.lines))]}],
            "confirmed": False, "origin": "manual"}
        self.evidence.save("actual-source-consumption.json", {
            "parentRawSHA": sha(self.captured["raw"]), "document_id": self.doc["id"], "revision": 1,
            "source_lines": self.lines, "original_mock_goal": self.goal,
            "physical_line_count": len(self.captured["text"].splitlines()),
            "selection_policy": "First and last nonblank whole physical lines at most 1200 characters each",
            "meaning": "Explicit bounded dataflow fixture, not full-source semantic requirements or upstream acceptance",
            "upstream_engineering": "NOT_RUN", "company_model": "NOT_RUN", "candidate_source_kind": "synthetic"})

    def api(self, method, suffix, expected=200, **kwargs):
        route = suffix if suffix.startswith("/api/") else self.base + suffix
        response = self.client.request(method, route, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {"byte_length": len(response.content), "sha256": sha(response.content)}
        self.calls.append({"method": method, "route": route, "params": kwargs.get("params"),
                           "request": kwargs.get("json"), "status": response.status_code, "response": data})
        assert response.status_code == expected, response.text
        return data

    def confirmed(self):
        body = copy.deepcopy(self.payload)
        draft = self.api("POST", "/requirements", 201, json=body)
        assert draft["payload"]["confirmed"] is False and draft["source_status"] == "CURRENT"
        body["confirmed"] = True  # Explicit human fixture action, never inferred from model output.
        row = self.api("PUT", f"/requirements/{draft['id']}?expected_revision=1", json=body)
        assert row["revision"] == 2 and row["payload"]["confirmed"] is True
        return row

    def task_body(self, requirement, **changes):
        return {"goal": self.goal, "requirement_refs": [{"requirement_id": requirement["id"],
                "revision": requirement["revision"], "sha256": requirement["sha256"]}],
                "allowed_write_paths": ["原创 来源候选.txt"], "timeout_seconds": 10,
                "max_steps": 4, "max_repairs": 1, **changes}

    def task(self, requirement, **changes):
        body = self.task_body(requirement, **changes)
        return self.api("POST", "/tasks", 201, json=body), body

    def submit(self, task, operation):
        run = self.api("POST", f"/tasks/{task['id']}/runs", 202,
                       json={"revision": task["revision"], "operation_id": operation})
        frozen = json.loads(run["frozen_inputs"])
        assert frozen["spec"] == task["spec"] and run["spec_hash"] == task["sha256"]
        assert run["stack_lock_hash"] == task["spec"]["stack_lock_hash"]
        assert run["verification_status"] == "NOT_RUN"
        assert len(frozen["documents"]) == 1
        doc = frozen["documents"][0]
        assert doc["document_id"] == self.doc["id"] and doc["revision"] == 1
        assert doc["sha256"] == sha(self.captured["raw"])
        assert base64.b64decode(doc["raw_base64"]) == self.captured["raw"]
        return run

    def approve(self, run, approve=True):
        rows = self.api("GET", "/approvals")["approvals"]
        approval = next(row for row in rows if row["run_id"] == run["id"])
        assert approval["action_hash"] == digest(approval["action"])
        assert approval["action"]["input_hash"] == sha(run["frozen_inputs"].encode())
        assert approval["action"]["write_paths"] == json.loads(run["frozen_inputs"])["spec"]["allowed_write_paths"]
        self.api("POST", f"/approvals/{approval['id']}/decision",
                 json={"action_hash": approval["action_hash"], "approve": approve})
        return approval

    def start(self):
        if self.worker.stop_event.is_set():
            # Restart means a fresh real controller object, just as a process
            # restart does; never clear/reuse a terminated Worker's stop token.
            assert self.worker.thread is None or not self.worker.thread.is_alive()
            self.worker = Worker(self.service, self.tmp_path)
            self.worker.verifier = self.client.app.state.verifier
            self.worker.repair_coordinator = self.client.app.state.repair_coordinator
        assert self.worker.start()

    def revise_source(self):
        # Deliberately original test mutation, never an alleged upstream revision.
        text = self.captured["text"]
        changed = (("!" if text[0] != "!" else "?") + text[1:]).encode()
        assert changed != self.captured["raw"] and len(changed) <= len(self.captured["raw"])
        revised = self.api("PUT", self.doc_route, files={"file": (self.captured["name"], changed)},
                           data={"expected_revision": 1})
        assert revised["revision"] == 2 and revised["sha256"] == sha(changed)
        self.evidence.save("actual-original-fixture-source-revision.json", {
            "parentRawSHA": sha(self.captured["raw"]), "raw_base64": base64.b64encode(changed).decode(),
            "sha256": sha(changed), "meaning": "Original controlled test mutation, not an upstream revision"})
        return revised

    def terminal(self, run, state="RETURNED"):
        result = until(self.service, self.pid, run["id"], {state})
        self.api("GET", f"/runs/{run['id']}")
        return result

    def process_receipt(self, run):
        actual = self.service.run(self.pid, run["id"])
        inspected = self.api("GET", f"/runs/{run['id']}/runner")
        assert inspected["state"] == "FINISHED" and inspected["replay_allowed"] is False
        with self.service.connect() as connection:
            job = dict(connection.execute("SELECT * FROM runner_jobs WHERE id=?", (actual["runner_job_id"],)).fetchone())
        assert job["run_id"] == run["id"] and job["pid"] > 0 and job["child_created"] > 0
        assert job["supervisor_pid"] > 0 and job["supervisor_created"] > 0 and job["status"] == "FINISHED"
        journal = Path(job["journal_path"])
        request_raw, receipt_raw = (journal / "request.json").read_bytes(), (journal / "receipt.json").read_bytes()
        request, receipt = json.loads(request_raw), json.loads(receipt_raw)
        assert sha(request_raw) == job["request_hash"] == receipt["request_hash"]
        assert request["run_id"] == receipt["run_id"] == run["id"]
        assert digest(request["argv"]) == job["command_hash"]
        assert request["timeout_seconds"] == json.loads(run["frozen_inputs"])["spec"]["budget"]["timeout_seconds"]
        assert receipt["child"] == {"pid": job["pid"], "create_time": job["child_created"]}
        assert receipt["exit_code"] == job["exit_code"] == inspected["exit_code"]
        for name in ("request.json", "receipt.json", f"event-{receipt['event_seq']:04d}.json"):
            self.evidence.save(f"runner-{run['id']}-{name}", (journal / name).read_bytes())
        self.evidence.save(f"runner-{run['id']}-sqlite.json", job)
        return job, receipt

    def verify(self, run, result="PASSED"):
        proof = self.api("POST", f"/runs/{run['id']}/verify")
        data = proof["payload"]
        assert data["run_id"] == run["id"] and data["result"] == result
        assert data["runtime_kind"] == "mock" and data["source_kind"] == "synthetic"
        assert data["test_report"]["tests_run"] == 1 and data["test_report"]["skipped"] == 0
        assert (data["exit_code"] == 0) == (result == "PASSED")
        spec = json.loads(run["frozen_inputs"])["spec"]
        assert data["command"] and data["acceptance_hash"] == spec["acceptance_hash"]
        assert data["source_hash"] == spec["source_hash"] and data["stack_lock_hash"] == run["stack_lock_hash"]
        assert data["task_revision"] == run["task_revision"]
        assert data["baseline_hash"] == spec["verification_profile"]["baseline_hash"]
        for field in ("stdout_path", "stderr_path"):
            raw = Path(data[field]).read_bytes()
            self.evidence.save(f"verifier-{run['id']}-{field}.json", {
                "raw_base64": base64.b64encode(raw).decode(), "byte_length": len(raw), "sha256": sha(raw)})
        self.evidence.save(f"verifier-{run['id']}.json", proof)
        return proof

    def persist(self):
        if self.persisted:
            return
        self.worker.stop()
        with self.service.connect() as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            rows = {name: [{key: ({"sqlite_type": "BLOB", "raw_base64": base64.b64encode(value).decode(),
                                  "byte_length": len(value), "sha256": sha(value)} if isinstance(value, bytes) else value)
                           for key, value in dict(row).items()}
                          for row in connection.execute('SELECT * FROM "' + name + '"')]
                    for name in names if not name.startswith("sqlite_")}
            path = self.tmp_path / "workflow-export.sqlite3"
            with sqlite3.connect(path) as exported:
                connection.backup(exported)
        self.evidence.save("actual-api-receipts.json", self.calls)
        self.evidence.save("actual-sqlite-rows.json", rows)
        self.evidence.save("actual-store.sqlite3", path.read_bytes())
        self.persisted = True

    def finish(self):
        self.persist()
        with self.service.connect() as connection:
            jobs = connection.execute("SELECT count(*) FROM runner_jobs").fetchone()[0]
            assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
        value = {"schema": "eeas.campaign-observation.v1", "case_id": self.captured["binding"]["case_id"],
                 "binding_sha256": sha(self.captured["binding_raw"]), "source_sha256": sha(self.captured["raw"]),
                 "source_kind": self.captured["source"]["source_kind"], "runtime_kind": "mock" if jobs else "none",
                 "model_calls": 0, "original_fixture_runner_jobs": jobs,
                 "candidate_source_kind": "synthetic", "origin_verification": "DECLARED_NOT_INDEPENDENTLY_VERIFIED",
                 "upstream_engineering": "NOT_RUN", "company_model": "NOT_RUN", "windows_native": "NOT_VALIDATED",
                 "os_sandbox": "NOT_VALIDATED", "actual_receipts": self.evidence.receipts}
        self.evidence.save("campaign-observation.json", value)


@pytest.fixture
def workflow(request, client, tmp_path):
    probe = Workflow.__new__(Workflow)
    try:
        Workflow.__init__(probe, client, tmp_path, request.node.name.removeprefix("test_").replace("_", "-"))
        yield probe
    finally:
        if hasattr(probe, "service"):
            probe.persist()  # Failed input/line assertions also retain actual SQLite, never a success observation.


def test_requirements(workflow):
    w = workflow
    foreign = w.api("POST", "/api/projects", 201, json={"name": "Foreign source boundary"})["id"]
    w.api("POST", f"/api/projects/{foreign}/requirements", 422, json=w.payload)
    for field, value in (("sha256", "0" * 64), ("line_end", len(w.captured["text"].splitlines()) + 1)):
        bad = copy.deepcopy(w.payload)
        bad["requirements"][0]["source_refs"][0][field] = value
        w.api("POST", "/requirements", 422, json=bad)
    bad = copy.deepcopy(w.payload)
    bad["acceptance"][0]["requirement_ids"] = ["missing"]
    w.api("POST", "/requirements", 422, json=bad)
    row = w.confirmed()
    assert w.api("GET", "/requirements")["requirements"][0] == row
    assert w.api("GET", f"/requirements/{row['id']}?revision=1")["payload"] == w.payload
    w.api("GET", f"/api/projects/{foreign}/requirements/{row['id']}", 404)
    w.api("PUT", f"/requirements/{row['id']}?expected_revision=1", 409, json=row["payload"])
    changed = copy.deepcopy(row["payload"])
    changed["requirements"][0]["text"] = "Original Mock revision: " + w.lines[0]["content"]
    revised = w.api("PUT", f"/requirements/{row['id']}?expected_revision=2", json=changed)
    assert revised["revision"] == 3 and revised["sha256"] != row["sha256"]
    assert w.api("GET", f"/requirements/{row['id']}?revision=2") == {**row, "current_revision": 3}
    w.revise_source()
    assert w.api("GET", f"/requirements/{row['id']}")["source_status"] == "STALE"
    w.api("POST", f"/requirements/{row['id']}/plan", 409, json={"revision": 3})
    assert w.api("GET", w.doc_route, params={"revision": 1})["content_sha256"] == sha(w.captured["raw"])
    with w.service.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE requirement_versions SET payload='tamper'")
    w.finish()


def test_planning(workflow):
    w = workflow
    draft = w.api("POST", "/requirements", 201, json=w.payload)
    route = f"/requirements/{draft['id']}/plan"
    w.api("POST", route, 422, json={"revision": 1})
    body = {**w.payload, "confirmed": True}
    req = w.api("PUT", f"/requirements/{draft['id']}?expected_revision=1", json=body)
    plan = w.api("POST", route, json={"revision": 2})["tasks"]
    repeated = w.api("POST", route, json={"revision": 2})["tasks"]
    assert plan == repeated and len(plan) == len(w.lines)
    for item in plan:
        assert item["requirement_hash"] == req["sha256"] and item["requirement_revision"] == 2
        assert item["review_status"] == "DRAFT"
        assert item["goal"] in [line["content"] for line in w.lines]
        assert sha(item["acceptance"].encode()) == item["acceptance_hash"]
    first, body = w.task(req)
    child, _ = w.task(req, dependencies=[first["id"]])
    assert child["spec"]["budget"]["max_steps"] == 4 and child["spec"]["budget"]["max_repairs"] == 1
    assert child["spec"]["dependencies"] == [{"task_id": first["id"], "task_revision": 1, "required_verification": "PASSED"}]
    w.api("PUT", f"/tasks/{first['id']}?expected_revision=1", 422, json={**body, "dependencies": [child["id"]]})
    foreign = w.api("POST", "/api/projects", 201, json={"name": "Foreign dependency"})["id"]
    w.api("POST", f"/api/projects/{foreign}/tasks", 404, json=body)
    foreign_doc = w.api("POST", f"/api/projects/{foreign}/documents", 201,
                        files={"file": (w.captured["name"], w.captured["raw"])},
                        data={"source": "Original cross-project dependency fixture; same captured bytes"})
    assert foreign_doc["sha256"] == w.doc["sha256"]
    foreign_payload = copy.deepcopy(req["payload"])
    for item in foreign_payload["requirements"]:
        item["source_refs"][0]["document_id"] = foreign_doc["id"]
    foreign_req = w.api("POST", f"/api/projects/{foreign}/requirements", 201, json=foreign_payload)
    foreign_body = w.task_body(foreign_req, dependencies=[first["id"]])
    w.api("POST", f"/api/projects/{foreign}/tasks", 422, json=foreign_body)
    changed = copy.deepcopy(req["payload"])
    changed["requirements"][0]["text"] = "Original Mock revised plan: " + w.lines[0]["content"]
    w.api("PUT", f"/requirements/{req['id']}?expected_revision=2", json=changed)
    assert all(item["review_status"] == "NEEDS_REVIEW" for item in w.api("GET", "/planning/tasks")["tasks"])
    w.api("POST", route, 409, json={"revision": 2})
    assert w.api("GET", f"/tasks/{first['id']}?revision=1")["spec"] == first["spec"]
    w.finish()


def test_tasks(workflow):
    w = workflow
    req = w.confirmed()
    task, body = w.task(req)
    run = w.submit(task, "source-task")
    repeated = w.api("POST", f"/tasks/{task['id']}/runs", 202, json={"revision": 1, "operation_id": "source-task"})
    assert repeated == run
    other, _ = w.task(req)
    w.api("POST", f"/tasks/{other['id']}/runs", 409, json={"revision": 1, "operation_id": "source-task"})
    for path in ("../escape.txt", "CON.txt", "stdout.txt", "frozen-inputs.json/overwrite"):
        w.api("POST", "/tasks", 422, json={**body, "allowed_write_paths": [path]})
    revised = w.api("PUT", f"/tasks/{task['id']}?expected_revision=1", json={**body, "goal": w.goal + "\nOriginal Mock task revision 2"})
    assert revised["revision"] == 2 and revised["sha256"] != task["sha256"]
    assert w.api("GET", f"/tasks/{task['id']}?revision=1")["spec"] == task["spec"]
    assert w.api("GET", f"/runs/{run['id']}")["frozen_inputs"] == run["frozen_inputs"]
    assert w.api("GET", f"/tasks/{task['id']}/runs")["runs"] == [run]
    reopened = TaskService(w.tmp_path / "eeas.sqlite3", ROOT)
    assert reopened.run(w.pid, run["id"])["frozen_inputs"] == run["frozen_inputs"]
    with w.service.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE task_runs SET frozen_inputs='tamper'")
    changed = copy.deepcopy(req["payload"])
    changed["questions"].append("Original Mock stale-scope check")
    w.api("PUT", f"/requirements/{req['id']}?expected_revision=2", json=changed)
    w.api("POST", f"/tasks/{task['id']}/runs", 409, json={"revision": 2, "operation_id": "stale-source-task"})
    w.api("POST", f"/runs/{run['id']}/cancel")
    w.finish()


def test_approvals(workflow):
    w = workflow
    req = w.confirmed()
    task, body = w.task(req, approval_required=True)
    run = w.submit(task, "approved-source")
    assert run["execution_status"] == "AWAITING_APPROVAL" and not (w.tmp_path / run["workspace"]).exists()
    pending = w.api("GET", "/approvals")["approvals"][0]
    route = f"/approvals/{pending['id']}/decision"
    w.api("POST", route, 409, json={"action_hash": "0" * 64, "approve": True})
    foreign = w.api("POST", "/api/projects", 201, json={"name": "Foreign approval"})["id"]
    w.api("POST", f"/api/projects/{foreign}{route}", 404, json={"action_hash": pending["action_hash"], "approve": True})
    w.approve(run)
    w.api("POST", route, json={"action_hash": pending["action_hash"], "approve": True})
    w.start()
    returned = w.terminal(run)
    assert returned["verification_status"] == "NOT_RUN"
    w.process_receipt(run)
    assert w.api("POST", route, json={"action_hash": pending["action_hash"], "approve": True})["status"] == "CONSUMED"
    w.worker.stop()
    rejected = w.submit(task, "rejected-source")
    w.approve(rejected, approve=False)
    assert w.api("GET", f"/runs/{rejected['id']}")["execution_status"] == "CANCELLED"
    expired_task, _ = w.task(req, approval_required=True, approval_ttl_seconds=1)
    expired = w.submit(expired_task, "expired-source")
    approval = next(a for a in w.api("GET", "/approvals")["approvals"] if a["run_id"] == expired["id"])
    deadline = time.monotonic() + 3
    while datetime.now(timezone.utc) < datetime.fromisoformat(approval["expires_at"]):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    w.api("POST", f"/approvals/{approval['id']}/decision", 409, json={"action_hash": approval["action_hash"], "approve": True})
    stale = w.submit(task, "stale-permission-source")
    stale_approval = next(a for a in w.api("GET", "/approvals")["approvals"] if a["run_id"] == stale["id"])
    w.api("PUT", f"/tasks/{task['id']}?expected_revision=1", json={**body, "allowed_write_paths": ["another-scope.txt"]})
    w.api("POST", f"/approvals/{stale_approval['id']}/decision", 409,
          json={"action_hash": stale_approval["action_hash"], "approve": True})
    w.api("POST", route, 409, json={"action_hash": pending["action_hash"], "approve": True})
    revised = w.api("GET", f"/tasks/{task['id']}")
    new_scope = w.submit(revised, "new-permission-source")
    scoped = next(a for a in w.api("GET", "/approvals")["approvals"] if a["run_id"] == new_scope["id"])
    assert scoped["action_hash"] != pending["action_hash"] and scoped["status"] == "PENDING"
    assert scoped["action"]["write_paths"] == ["another-scope.txt"]
    w.revise_source()
    w.api("POST", f"/approvals/{scoped['id']}/decision", 409,
          json={"action_hash": scoped["action_hash"], "approve": True})
    assert not (w.tmp_path / new_scope["workspace"]).exists()
    assert all(not (w.tmp_path / r["workspace"]).exists() for r in (rejected, expired, stale))
    with w.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM runner_jobs").fetchone()[0] == 1
    w.finish()


def test_runner(workflow):
    w = workflow
    req = w.confirmed()
    task, _ = w.task(req, approval_required=True)
    run = w.submit(task, "runner-success")
    w.approve(run)
    w.start()
    second = Worker(w.service, w.tmp_path)
    assert not second.start()
    returned = w.terminal(run)
    assert returned["verification_status"] == "NOT_RUN"
    assert (w.tmp_path / run["workspace"] / task["spec"]["allowed_write_paths"][0]).read_bytes() == (w.goal + "\n").encode()
    w.process_receipt(run)
    events = w.api("GET", f"/runs/{run['id']}/events")["events"]
    assert [e["event_seq"] for e in events] == list(range(1, len(events) + 1))
    assert any(e["payload"]["type"] == "tool_result" for e in events)
    timed_task, _ = w.task(req, mock_behavior="wait_for_cancel", timeout_seconds=1)
    timed = w.submit(timed_task, "runner-timeout")
    assert w.terminal(timed, "FAILED")["error_type"] == "TIME_LIMIT"
    w.process_receipt(timed)
    cancelled_task, _ = w.task(req, mock_behavior="wait_for_cancel")
    cancelled = w.submit(cancelled_task, "runner-cancel")
    deadline = time.monotonic() + 20
    while True:
        live = w.api("GET", f"/runs/{cancelled['id']}/runner")
        if live["state"] == "LIVE" and live["child_alive"]:
            break
        assert time.monotonic() < deadline, live
        time.sleep(0.03)
    w.api("POST", f"/runs/{cancelled['id']}/cancel")
    stopped = w.terminal(cancelled, "CANCELLED")
    assert stopped["error_type"] == "CANCELLED"
    w.process_receipt(cancelled)
    assert w.api("GET", f"/runs/{cancelled['id']}/runner")["child_alive"] is False
    w.finish()


def test_verification(workflow):
    w = workflow
    req = w.confirmed()
    task, _ = w.task(req)
    run = w.submit(task, "verified-source")
    w.start()
    assert w.terminal(run)["verification_status"] == "NOT_RUN"
    w.process_receipt(run)
    proof = w.verify(run)
    assert w.api("POST", f"/runs/{run['id']}/verify") == proof
    artifact = w.tmp_path / run["workspace"] / task["spec"]["allowed_write_paths"][0]
    artifact.write_bytes(b"Original test-only corruption; not upstream data")
    assert w.api("GET", f"/evidence/{proof['payload']['evidence_id']}")["association_status"] == "INTEGRITY_ERROR"
    artifact.unlink()
    assert w.api("GET", f"/evidence/{proof['payload']['evidence_id']}")["association_status"] == "INTEGRITY_ERROR"
    # A second genuine run preserves the first proof while demonstrating stale lineage.
    next_run = w.submit(task, "verified-source-next")
    w.terminal(next_run)
    next_proof = w.verify(next_run)
    tampered_run = w.submit(task, "protected-baseline-tamper")
    w.terminal(tampered_run)
    with w.service.connect() as connection:
        baseline = dict(connection.execute("SELECT * FROM verification_baselines WHERE run_id=?", (tampered_run["id"],)).fetchone())
    approved_copy = Path(baseline["baseline_path"])
    assert sha(approved_copy.read_bytes()) == task["spec"]["verification_profile"]["baseline_hash"]
    approved_copy.write_bytes(b"# Original test-only malicious skip/success replacement\nraise SystemExit(0)\n")
    denied = w.api("POST", f"/runs/{tampered_run['id']}/verify")
    assert denied["payload"]["result"] == "FAILED" and denied["payload"]["error_type"] == "INTEGRITY_ERROR"
    assert denied["payload"]["command"] is None and denied["payload"]["test_report"] is None
    assert denied["association_status"] == "INTEGRITY_ERROR"
    assert sha((ROOT / "packages/verifier/original_baseline.py").read_bytes()) == task["spec"]["verification_profile"]["baseline_hash"]
    w.evidence.save("actual-protected-baseline-denial.json", denied)
    body = w.task_body(req, goal=w.goal + "\nOriginal Mock revised acceptance scope")
    w.api("PUT", f"/tasks/{task['id']}?expected_revision=1", json=body)
    assert w.api("GET", f"/evidence/{next_proof['payload']['evidence_id']}")["association_status"] == "STALE_EVIDENCE"
    w.api("POST", f"/runs/{next_run['id']}/verify", 409)
    assert w.service.run(w.pid, run["id"])["verification_status"] == "PASSED"
    w.finish()


def test_repair(workflow):
    w = workflow
    req = w.confirmed()
    w.start()
    for behavior, expected, count in (("repairable", "COMPLETE", 2), ("persistent_failure", "EXHAUSTED", 3)):
        task, _ = w.task(req, mock_behavior=behavior, max_repairs=2)
        run = w.submit(task, "repair-" + behavior)
        w.terminal(run)
        w.api("POST", f"/runs/{run['id']}/repair", 422, json={"max_repairs": 1})
        first = w.verify(run, "FAILED")
        original_proof_raw = json.dumps(first, ensure_ascii=False, sort_keys=True).encode()
        w.api("POST", f"/runs/{run['id']}/repair", 422, json={"max_repairs": 3})
        session = w.api("POST", f"/runs/{run['id']}/repair", 202, json={"max_repairs": 2})
        repeated = w.api("POST", f"/runs/{run['id']}/repair", 202, json={"max_repairs": 2})
        assert repeated["id"] == session["id"]
        w.api("POST", f"/runs/{run['id']}/repair", 409, json={"max_repairs": 1})
        deadline = time.monotonic() + 60
        while True:
            current = w.api("GET", f"/repair-sessions/{session['id']}")
            if current["status"] != "RUNNING":
                break
            assert time.monotonic() < deadline, current
            time.sleep(0.05)
        assert current["status"] == expected and current["repairs_started"] == count - 1
        runs = w.api("GET", f"/tasks/{task['id']}/runs")["runs"]
        assert len(runs) == count and len({r["id"] for r in runs}) == count
        assert len({r["workspace"] for r in runs}) == count and {r["spec_hash"] for r in runs} == {run["spec_hash"]}
        assert runs[0]["verification_status"] == "FAILED"
        assert json.dumps(w.api("GET", f"/evidence/{first['payload']['evidence_id']}"), ensure_ascii=False, sort_keys=True).encode() == original_proof_raw
        for attempt in runs:
            frozen = json.loads(attempt["frozen_inputs"])
            assert frozen["documents"][0]["sha256"] == sha(w.captured["raw"])
            assert frozen["spec"]["budget"]["max_repairs"] == 2 and frozen["spec"]["budget"]["max_steps"] == 4
            w.process_receipt(attempt)
        with w.service.connect() as connection:
            assert connection.execute("SELECT count(*) FROM verification_evidence WHERE run_id IN (SELECT id FROM task_runs WHERE task_id=?)", (task["id"],)).fetchone()[0] == count
    # Each repair attempt is a distinct action, not permission inherited from
    # the first approved run. Two actual controller ticks are deterministic;
    # they must leave the new run awaiting an explicit new human decision.
    w.worker.stop()
    guarded_task, _ = w.task(req, mock_behavior="repairable", max_repairs=1, approval_required=True)
    initial = w.submit(guarded_task, "repair-new-approval")
    initial_approval = w.approve(initial)
    w.start()
    w.terminal(initial)
    failed = w.verify(initial, "FAILED")
    w.worker.stop()
    guarded = w.api("POST", f"/runs/{initial['id']}/repair", 202, json={"max_repairs": 1})
    coordinator = w.client.app.state.repair_coordinator
    coordinator.tick()
    pending_runs = w.api("GET", f"/tasks/{guarded_task['id']}/runs")["runs"]
    assert len(pending_runs) == 2
    pending = pending_runs[-1]
    assert pending["execution_status"] == "AWAITING_APPROVAL" and pending["runner_job_id"] is None
    assert not (w.tmp_path / pending["workspace"]).exists()
    coordinator.tick()
    status = w.api("GET", f"/repair-sessions/{guarded['id']}")
    w.evidence.save("actual-repair-approval-controller-ticks.json", {
        "ticks": 2, "session": status, "pending_run": pending,
        "meaning": "Actual existing RepairCoordinator ticks; no replacement coordinator or new Agent loop"})
    assert status["status"] == "RUNNING", "A repair awaiting its new exact approval must remain pending"
    next_approval = w.approve(pending)
    assert next_approval["id"] != initial_approval["id"]
    assert next_approval["action_hash"] != initial_approval["action_hash"]
    w.start()
    deadline = time.monotonic() + 30
    while True:
        current = w.api("GET", f"/repair-sessions/{guarded['id']}")
        if current["status"] != "RUNNING":
            break
        assert time.monotonic() < deadline, current
        time.sleep(0.05)
    assert current["status"] == "COMPLETE" and current["repairs_started"] == 1
    assert w.api("GET", f"/evidence/{failed['payload']['evidence_id']}") == failed
    w.process_receipt(initial)
    w.process_receipt(pending)
    # A step limit and a frozen zero repair budget are separate from model cost.
    limited_task, _ = w.task(req, max_steps=1, max_repairs=0)
    limited = w.submit(limited_task, "repair-zero-budget")
    stopped = w.terminal(limited, "FAILED")
    assert stopped["error_type"] == "STEP_LIMIT" and stopped["runner_job_id"] is None
    w.verify(limited, "FAILED")
    w.api("POST", f"/runs/{limited['id']}/repair", 422, json={"max_repairs": 1})
    assert len(w.api("GET", f"/tasks/{limited_task['id']}/runs")["runs"]) == 1
    w.finish()


def test_results(workflow):
    w = workflow
    req = w.confirmed()
    task, _ = w.task(req)
    run = w.submit(task, "results-source")
    w.start()
    w.terminal(run)
    w.process_receipt(run)
    assert w.api("GET", f"/runs/{run['id']}/results")["evidence"] is None
    proof = w.verify(run)
    summary = w.api("GET", f"/runs/{run['id']}/results")
    name = task["spec"]["allowed_write_paths"][0]
    expected = (w.goal + "\n").encode()
    assert summary["usage"] == {"tokens": None, "cost": None}
    assert summary["artifact_hash"] == {name: sha(expected)}
    assert summary["task_revision"] == 1 and summary["budget"] == task["spec"]["budget"]
    assert summary["evidence"]["association_status"] == "CURRENT"
    artifact_route = f"/runs/{run['id']}/artifacts/{quote(name)}"
    response = w.client.get(w.base + artifact_route)
    assert response.status_code == 200 and response.content == expected
    w.evidence.save("actual-source-candidate.bin", response.content)
    assert w.api("GET", f"/evidence/{proof['payload']['evidence_id']}/download") == proof
    for stream in ("stdout", "stderr"):
        log = w.api("GET", f"/runs/{run['id']}/logs/{stream}")
        raw = (w.tmp_path / run["workspace"] / (stream + ".txt")).read_bytes()
        assert log["sha256"] == sha(raw) and log["bytes"] == len(raw) and log["snapshot"] is True
        w.evidence.save(f"actual-runner-{stream}.json", {
            "raw_base64": base64.b64encode(raw).decode(), "byte_length": len(raw), "sha256": sha(raw)})
    foreign = w.api("POST", "/api/projects", 201, json={"name": "Foreign results"})["id"]
    w.api("GET", f"/api/projects/{foreign}/runs/{run['id']}/results", 404)
    w.api("GET", f"/api/projects/{foreign}/evidence/{proof['payload']['evidence_id']}/download", 404)
    w.api("GET", f"/api/projects/{foreign}{artifact_route}", 404)
    w.api("GET", f"/runs/{run['id']}/artifacts/../frozen-inputs.json", 404)
    w.api("GET", f"/runs/{run['id']}/artifacts/frozen-inputs.json", 404)
    (w.tmp_path / run["workspace"] / name).write_bytes(b"Original test-only artifact tamper")
    assert w.api("GET", f"/runs/{run['id']}/results")["evidence"]["association_status"] == "INTEGRITY_ERROR"
    w.api("GET", artifact_route, 422)
    w.finish()


def test_project_boundary(workflow):
    w = workflow
    owner = w.api("GET", "")
    foreign = w.api("POST", "/api/projects", 201,
                    json={"name": "Foreign source-bound project", "description": "Original boundary fixture"})
    projects = w.api("GET", "/api/projects")["projects"]
    assert {p["id"] for p in projects} == {w.pid, foreign["id"]}
    assert TaskService(w.tmp_path / "eeas.sqlite3", ROOT).project(w.pid) == owner
    assert TaskService(w.tmp_path / "eeas.sqlite3", ROOT).project(foreign["id"]) == foreign
    req = w.confirmed()
    task, body = w.task(req, approval_required=True)
    run = w.submit(task, "isolated-source-boundary")
    approval = next(a for a in w.api("GET", "/approvals")["approvals"] if a["run_id"] == run["id"])
    other = f"/api/projects/{foreign['id']}"
    for route in (f"/documents/{w.doc['id']}", f"/documents/{w.doc['id']}/download?revision=1",
                  f"/requirements/{req['id']}", f"/tasks/{task['id']}", f"/runs/{run['id']}",
                  f"/runs/{run['id']}/events", f"/runs/{run['id']}/runner",
                  f"/tasks/{task['id']}/runs/{run['id']}/native-review-scope"):
        w.api("GET", other + route, 404)
    w.api("POST", other + "/requirements", 422, json=req["payload"])
    w.api("POST", other + "/tasks", 404, json=body)
    w.api("POST", other + f"/tasks/{task['id']}/runs", 404,
          json={"revision": 1, "operation_id": "foreign-run"})
    w.api("POST", other + f"/approvals/{approval['id']}/decision", 404,
          json={"action_hash": approval["action_hash"], "approve": True})
    w.api("POST", other + f"/runs/{run['id']}/cancel", 404)
    assert w.api("GET", f"/runs/{run['id']}")["execution_status"] == "AWAITING_APPROVAL"
    w.approve(run)
    w.start()
    w.terminal(run)
    w.process_receipt(run)
    proof = w.verify(run)
    for route in (f"/runs/{run['id']}/results", f"/runs/{run['id']}/logs/stdout",
                  f"/runs/{run['id']}/artifacts/{quote(task['spec']['allowed_write_paths'][0])}",
                  f"/evidence/{proof['payload']['evidence_id']}", f"/evidence/{proof['payload']['evidence_id']}/download"):
        w.api("GET", other + route, 404)
    w.api("POST", other + f"/runs/{run['id']}/verify", 404)
    assert w.api("GET", other + "/documents")["documents"] == []
    assert w.api("GET", other + "/tasks")["tasks"] == []
    assert w.api("GET", other + "/approvals")["approvals"] == []
    w.finish()


def test_recovery(workflow):
    from test_recovery import crash, wait_for, runner_fact, clean_runner

    w = workflow
    req = w.confirmed()
    for window in ("result-lost", "during-execution"):
        if (w.tmp_path / "fault-ready").exists():
            (w.tmp_path / "fault-ready").unlink()
        task, _ = w.task(req, mock_behavior="wait_for_cancel" if window == "during-execution" else "success")
        run = w.submit(task, "recovery-" + window)
        output = w.tmp_path / ("controller-" + window + ".txt")
        with output.open("xb") as stream:
            controller = subprocess.Popen([sys.executable, str(ROOT / "tests/fixtures/worker_fault.py"),
                                           str(w.tmp_path), window], cwd=ROOT, stdout=stream, stderr=stream)
            try:
                if window == "result-lost":
                    wait_for(lambda: (w.tmp_path / "fault-ready").exists())
                    fact = runner_fact(w.service, w.tmp_path, w.pid, run["id"])
                    assert fact["state"] == "FINISHED"
                else:
                    fact = wait_for(lambda: (f if f.get("phase") == "RUNNING" else None)
                                    if (f := runner_fact(w.service, w.tmp_path, w.pid, run["id"])) else None)
                    assert matching_process(fact["receipt"]["child"])
                before = w.api("GET", f"/runs/{run['id']}")
                assert before["execution_status"] == "RUNNING"
                original_job = before["runner_job_id"]
                w.evidence.save("actual-before-controller-crash-" + window + ".json", {
                    "controller_pid": controller.pid, "runner_fact": fact, "run": before})
                crash(controller)
                if window == "during-execution":
                    assert matching_process(fact["receipt"]["child"]), "Detached child must survive controller loss"
                w.start()
                if window == "during-execution":
                    w.api("POST", f"/runs/{run['id']}/cancel")
                terminal = w.terminal(run, "CANCELLED" if window == "during-execution" else "RETURNED")
                assert terminal["runner_job_id"] == original_job and terminal["verification_status"] != "PASSED"
                w.process_receipt(run)
                events = w.api("GET", f"/runs/{run['id']}/events")["events"]
                assert sum(e["payload"]["type"] == "runner_recovered" for e in events) == 1
                assert sum(e["payload"]["type"] == "tool_result" for e in events) == 1
                with w.service.connect() as connection:
                    assert connection.execute("SELECT count(*) FROM runner_jobs WHERE run_id=?", (run["id"],)).fetchone()[0] == 1
                    assert connection.execute("SELECT count(*) FROM task_runs WHERE task_id=?", (task["id"],)).fetchone()[0] == 1
                if window == "result-lost":
                    assert (w.tmp_path / terminal["workspace"] / task["spec"]["allowed_write_paths"][0]).read_bytes() == (w.goal + "\n").encode()
                    w.verify(run)
            finally:
                crash(controller)
                w.worker.stop()
                clean_runner(w.service, w.tmp_path, w.pid, run["id"])
        raw = output.read_bytes()
        w.evidence.save("actual-controller-output-" + window + ".json", {
            "raw_base64": base64.b64encode(raw).decode(), "sha256": sha(raw), "exit_code": controller.returncode})
    # A third real result deliberately loses valid identity association. It
    # must be UNKNOWN, never replayed or used to kill an unrelated live child.
    (w.tmp_path / "fault-ready").unlink(missing_ok=True)
    unknown_task, _ = w.task(req)
    unknown = w.submit(unknown_task, "recovery-identity-mismatch")
    controller = subprocess.Popen([sys.executable, str(ROOT / "tests/fixtures/worker_fault.py"),
                                   str(w.tmp_path), "result-lost"], cwd=ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    unrelated = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(lambda: (w.tmp_path / "fault-ready").exists())
        crash(controller)
        original = w.service.run(w.pid, unknown["id"])
        w.process_receipt(unknown)  # Retain the original actual receipt before the controlled corruption.
        with w.service.connect() as connection:
            job = dict(connection.execute("SELECT * FROM runner_jobs WHERE run_id=?", (unknown["id"],)).fetchone())
            connection.execute("UPDATE runner_jobs SET supervisor_pid=?,supervisor_created=? WHERE id=?",
                               (unrelated.pid, job["supervisor_created"] + 6000, job["id"]))
        assert unrelated.poll() is None
        denied = w.api("GET", f"/runs/{unknown['id']}/runner")
        assert denied["state"] == "UNKNOWN_OUTCOME" and denied["replay_allowed"] is False
        w.evidence.save("actual-recovery-identity-corruption.json", {
            "original_job": job, "original_run": original, "unrelated_owned_pid": unrelated.pid,
            "controlled_wrong_created_at": job["supervisor_created"] + 6000, "inspection": denied})
        w.start()
        result = w.terminal(unknown, "UNKNOWN_OUTCOME")
        assert result["verification_status"] == "BLOCKED" and result["runner_job_id"] == job["id"]
        assert unrelated.poll() is None, "Unknown identity must not authorize killing another process"
        with w.service.connect() as connection:
            assert connection.execute("SELECT count(*) FROM runner_jobs WHERE run_id=?", (unknown["id"],)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM task_runs WHERE task_id=?", (unknown_task["id"],)).fetchone()[0] == 1
        assert not any(e["payload"]["type"] == "runner_recovered" for e in w.service.events(w.pid, unknown["id"]))
    finally:
        crash(controller)
        w.worker.stop()
        if unrelated.poll() is None:
            unrelated.terminate()
        unrelated.wait(timeout=5)
    w.finish()


def test_extensions(workflow):
    from test_extensions import archive

    w = workflow
    req = w.confirmed()
    snapshots = []
    for version in (1, 2):
        raw = archive(version)
        w.evidence.save(f"actual-original-formatter-v{version}.zip", raw)
        row = w.api("POST", "/api/extensions", 201, files={"file": (f"original-v{version}.zip", raw)})
        assert row["supported"] and not row["qualified"] and not row["loaded"]
        same = w.api("POST", "/api/extensions", 201, files={"file": (f"original-v{version}.zip", raw)})
        assert same["id"] == row["id"]
        qualified = w.api("POST", f"/api/extensions/{row['id']}/qualify")
        assert qualified["qualified"] and qualified["loaded"]
        review = w.api("POST", f"/api/extensions/{row['id']}/review")
        if version == 2:
            assert review["action"]["new_permissions"] == ["text.length"]
            w.api("POST", f"/api/extension-reviews/{review['id']}/approve", 409,
                  json={"action_hash": review["action_hash"], "permissions": ["text.format"]})
        w.api("POST", f"/api/extension-reviews/{review['id']}/approve",
              json={"action_hash": review["action_hash"], "permissions": review["action"]["permissions"]})
        snapshots.append((row, review))
        if version == 1:
            pin = w.api("POST", "/extension-binding", json={"snapshot_id": row["id"]})
            task, _ = w.task(req)
            historical = w.submit(task, "extension-v1-source")
    v1, v2 = snapshots[0][0], snapshots[1][0]
    assert w.api("GET", "/extension-binding")["binding"] == pin
    assert json.loads(historical["frozen_inputs"])["spec"]["extension_lock"]["snapshot_id"] == v1["id"]
    w.start()
    w.terminal(historical)
    w.process_receipt(historical)
    w.verify(historical)
    events = w.api("GET", f"/runs/{historical['id']}/events")["events"]
    call = next(e["payload"] for e in events if e["payload"]["type"] == "extension_result")
    assert call["snapshot_id"] == v1["id"] and call["result"] == "v1|" + w.goal
    w.api("POST", "/extension-binding", json={"snapshot_id": v2["id"]})
    current_task, _ = w.task(req)
    current = w.submit(current_task, "extension-v2-source")
    w.terminal(current)
    w.process_receipt(current)
    w.verify(current)
    assert current["stack_lock_hash"] != historical["stack_lock_hash"]
    events = w.api("GET", f"/runs/{current['id']}/events")["events"]
    call = next(e["payload"] for e in events if e["payload"]["type"] == "extension_result")
    assert call["snapshot_id"] == v2["id"] and call["result"] == {"format": "v2", "text": w.goal, "length": len(w.goal)}
    rollback = w.api("POST", f"/api/extensions/{v1['id']}/review")
    w.api("POST", f"/api/extension-reviews/{rollback['id']}/approve",
          json={"action_hash": rollback["action_hash"], "permissions": rollback["action"]["permissions"]})
    old_review = snapshots[1][1]
    w.api("POST", f"/api/extension-reviews/{old_review['id']}/approve",
          json={"action_hash": old_review["action_hash"], "permissions": old_review["action"]["permissions"]})
    listing = w.api("GET", "/api/extensions")
    assert listing["sdk_plugin_live"] == "NOT_VALIDATED"
    assert next(r for r in listing["extensions"] if r["id"] == v1["id"])["channel"] == "STABLE"
    assert w.api("GET", "/extension-binding")["binding"]["lock"]["snapshot_id"] == v2["id"]
    assert w.api("GET", f"/runs/{historical['id']}")["frozen_inputs"] == historical["frozen_inputs"]
    w.finish()
