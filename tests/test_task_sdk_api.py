"""Real API/SQLite/formal input/native installed SDK; only the external JSON transport is a fixture."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

import pytest
from claude_agent_sdk import ClaudeSDKClient
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api import main
from packages.adapters import claude_sdk
from packages.domain.tasks import digest, source_hashes
from conftest import ROOT
from test_claude_sessions import FIXTURE_ENV, NativeFactory, QueuedWire
from test_documents import project
from test_extension_tasks import import_and_activate
from test_tasks import inputs


def formal(client, name="原创原生审阅", snapshot=None):
    pid = project(client, name)
    if snapshot:
        pinned = client.post(f"/api/projects/{pid}/extension-binding", json={"snapshot_id": snapshot})
        assert pinned.status_code == 200, pinned.text
    doc, req, body = inputs(client, pid)
    task = client.post(f"/api/projects/{pid}/tasks", json=body).json()
    prefix = f"/api/projects/{pid}/tasks/{task['id']}/runs"
    run = client.post(prefix, json={"revision": task["revision"], "operation_id": "original-formal-run"}).json()
    route = prefix + "/" + run["id"]
    scope = client.get(route + "/native-review-scope")
    assert scope.status_code == 200, scope.text
    scope = scope.json()
    body = {"operation_id": "original-native-review", "task_binding": scope["task_binding"],
            "input_hash": scope["input_hash"], "source_hash": scope["source_hash"], "native": {}, "needs_tools": False}
    return pid, doc, req, task, run, route, body


def copy_frozen_source(destination, spec):
    for name in ("apps", "packages", "contracts", "profiles"):
        shutil.copytree(ROOT / name, destination / name, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    # The drift tests must start with the exact source and stack the task froze.
    assert digest(source_hashes(destination)) == spec["source_hash"]
    stack_hash = hashlib.sha256((destination / "profiles/stack.lock.json").read_bytes()).hexdigest()
    assert stack_hash == spec.get("base_stack_hash", spec["stack_lock_hash"])


@pytest.fixture(autouse=True)
def claude_review_runtime(monkeypatch):
    # This suite exercises the Claude adapter, which the app selects at startup.
    monkeypatch.setenv("EEAS_NATIVE_RUNTIME", "claude-agent-sdk")


def configured(monkeypatch, factory):
    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", factory)


def until_review(client, pid, ref, states, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{pid}/native-reviews/{ref}")
        assert response.status_code == 200, response.text
        result = response.json()
        native = result["native_execution"]
        if (native and native["execution_status"] in states and
                (native["session_id"] or native["execution_status"] != "RUNNING") and
                (native["execution_status"] == "RUNNING" or result["admission_status"] == native["execution_status"])):
            return result
        if not native and result["admission_status"] in {"BLOCKED", "UNKNOWN_OUTCOME"}:
            pytest.fail(str(result))
        time.sleep(.02)
    pytest.fail("native review did not reach the requested actual state")


def test_preflight_missing_config_and_code_tools_have_no_native_side_effects(client, monkeypatch):
    pid, doc, req, task, run, route, body = formal(client)
    factory = NativeFactory([0.002])
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", factory)
    for key in ("ANTHROPIC_API_KEY", "EEAS_MODEL_ID", "EEAS_LIVE_MAX_COST_USD"):
        monkeypatch.setenv(key, "")
    denied = client.post(route + "/native-reviews", json=body)
    assert denied.status_code == 503
    assert "NO_CREDENTIALS" in denied.json()["error"]["reasons"]
    configured(monkeypatch, factory)
    denied = client.post(route + "/native-reviews", json={**body, "needs_tools": True})
    assert denied.status_code == 503 and "OS_ISOLATION_NOT_QUALIFIED" in denied.json()["error"]["reasons"]
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM task_native_reviews").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM agent_executions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 0
    assert not factory.transports
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run


def test_actual_sdk_api_binding_stream_result_and_repeated_admission_do_not_complete_formal_run(client, monkeypatch):
    pid, doc, req, task, run, route, body = formal(client)
    factory = NativeFactory([0.002])
    configured(monkeypatch, factory)
    submitted = client.post(route + "/native-reviews", json={**body, "native": {"effort": "low", "thinking": {"type": "disabled"}}})
    assert submitted.status_code == 202, submitted.text
    ref = submitted.json()["admission_id"]
    repeat = client.post(route + "/native-reviews", json={**body, "native": {"effort": "low", "thinking": {"type": "disabled"}}})
    assert repeat.status_code == 202 and repeat.json()["admission_id"] == ref
    completed = until_review(client, pid, ref, {"RETURNED"})
    native = completed["native_execution"]
    assert native["verification_status"] == completed["verification_status"] == "NOT_RUN"
    assert not completed["formal_run_changed"]
    actual = json.loads(native["request"])
    assert actual["task_binding"] == body["task_binding"]
    assert actual["document_refs"] == [{"document_id": doc["id"], "revision": doc["revision"], "sha256": doc["sha256"]}]
    options = factory.transports[0].options
    assert options.tools == [] and options.permission_mode == "default" and options.max_turns == task["spec"]["budget"]["max_steps"]
    assert options.effort == "low" and options.thinking == {"type": "disabled"}
    events = client.get(f"/api/projects/{pid}/native-reviews/{ref}/events").json()["events"]
    assert any(event["payload"]["type"] == "ResultMessage" for event in events)
    last = events[-1]["event_seq"]
    assert client.get(f"/api/projects/{pid}/native-reviews/{ref}/events?after={last}").json()["events"] == []
    exported = client.get(f"/api/projects/{pid}/native-reviews/{ref}/result").json()
    assert exported["frozen_scope"]["input_hash"] == body["input_hash"]
    assert exported["native_result"]["execution"]["id"] == native["id"]
    assert not exported["native_result"]["live_evidence"]
    assert exported["verification_status"] == "NOT_RUN"
    assert client.get(f"/api/projects/{pid}/agent-executions/{native['id']}/result").json()["execution"]["id"] == native["id"]
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
    different = client.post(route + "/native-reviews", json={**body, "operation_id": "budget-reset-root"})
    assert different.status_code == 409
    assert len(factory.transports) == 1
    with client.app.state.store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE task_native_reviews SET frozen_scope='changed'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM task_native_reviews")
        assert db.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 0
    evidence = os.environ.get("EEAS_EVIDENCE_DIR")
    if evidence:
        Path(evidence, "native-task-review.json").write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("field,value,status", [
    ("source_hash", "0" * 64, 409), ("input_hash", "0" * 64, 409),
    ("max_turns", 6, 409), ("timeout_seconds", 31, 409),
    ("native", {"env": {"UNAPPROVED": "value"}}, 422),
    ("allowed_write_paths", ["unapproved.txt"], 422),
    ("document_refs", [], 422), ("source_files", {"injected.txt": "data"}, 422),
])
def test_frozen_input_native_overrides_and_budget_changes_rejected_before_admission(client, monkeypatch, field, value, status):
    pid, doc, req, task, run, route, body = formal(client)
    factory = NativeFactory([0.002])
    configured(monkeypatch, factory)
    rejected = client.post(route + "/native-reviews", json={**body, field: value})
    assert rejected.status_code == status, rejected.text
    assert not factory.transports
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM task_native_reviews").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["task", "document", "requirement", "cross_project", "binding"])
def test_formal_revision_documents_and_project_binding_are_checked_again(client, monkeypatch, change):
    pid, doc, req, task, run, route, body = formal(client)
    factory = NativeFactory([0.002])
    configured(monkeypatch, factory)
    if change == "task":
        spec = task["spec"]
        updated = client.put(f"/api/projects/{pid}/tasks/{task['id']}?expected_revision=1", json={"goal": "changed goal", "requirement_refs": spec["requirement_refs"]})
        assert updated.status_code == 200
    elif change == "document":
        assert client.put(f"/api/projects/{pid}/documents/{doc['id']}", files={"file": ("original.md", b"changed public text")}, data={"expected_revision": 1}).status_code == 200
    elif change == "requirement":
        payload = req["payload"]
        payload["title"] = "changed original requirement"
        assert client.put(f"/api/projects/{pid}/requirements/{req['id']}?expected_revision=1", json=payload).status_code == 200
    elif change == "cross_project":
        other = project(client, "other original project")
        route = route.replace(pid, other)
    else:
        body["task_binding"]["task_revision"] += 1
    rejected = client.post(route + "/native-reviews", json=body)
    assert rejected.status_code in {404, 409}, rejected.text
    assert not factory.transports


@pytest.mark.parametrize("change", ["source", "stack"])
def test_actual_source_or_stack_bytes_changed_in_public_copy_are_refused(client, monkeypatch, tmp_path, change):
    pid, doc, req, task, run, route, body = formal(client)
    copied = tmp_path / "original-source-copy"
    copy_frozen_source(copied, task["spec"])
    target = copied / ("apps/web/index.html" if change == "source" else "profiles/stack.lock.json")
    if change == "source":
        target.write_text(target.read_text() + "\n<!-- Original source change fixture -->\n")
    else:
        payload = json.loads(target.read_text())
        payload["original_change_fixture"] = True
        target.write_text(json.dumps(payload))
    monkeypatch.setattr(main, "ROOT", copied)
    factory = NativeFactory([0.002])
    configured(monkeypatch, factory)
    with TestClient(main.create_app(Settings(client.app.state.settings.data_dir)), base_url="http://127.0.0.1:8765") as reopened:
        auth = reopened.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()
        reopened.headers.update({"Origin": "http://127.0.0.1:8765", "X-EEAS-CSRF": auth["csrf_token"]})
        denied = reopened.post(route + "/native-reviews", json=body)
        assert denied.status_code == 409, denied.text
    assert not factory.transports


@pytest.mark.parametrize("kind", ["resume", "fork"])
def test_api_native_session_continuation_inherits_frozen_input_and_remaining_budget(client, monkeypatch, kind):
    pid, doc, req, task, run, route, body = formal(client)
    factory = NativeFactory([0.002, 0.004])
    configured(monkeypatch, factory)
    admission = client.post(route + "/native-reviews", json=body).json()
    parent = until_review(client, pid, admission["admission_id"], {"RETURNED"})
    frozen_export = client.get(f"/api/projects/{pid}/native-reviews/{admission['admission_id']}/result").json()
    control = {"operation_id": "original-followup", "expected_session_id": parent["native_execution"]["session_id"], "prompt": "An original readonly followup"}
    bad = client.post(f"/api/projects/{pid}/native-reviews/{admission['admission_id']}/{kind}", json={**control, "expected_session_id": "wrong-original-session"})
    assert bad.status_code == 409
    assert client.post(f"/api/projects/{pid}/native-reviews/{admission['admission_id']}/{kind}", json={**control, "max_turns": 20}).status_code == 422
    followed = client.post(f"/api/projects/{pid}/native-reviews/{admission['admission_id']}/{kind}", json=control)
    assert followed.status_code == 202, followed.text
    child = until_review(client, pid, followed.json()["admission_id"], {"RETURNED"})
    repeated = client.post(f"/api/projects/{pid}/native-reviews/{admission['admission_id']}/{kind}", json=control)
    assert repeated.status_code == 202 and repeated.json()["admission_id"] == child["admission_id"]
    assert child["native_execution_id"] != parent["native_execution_id"]
    assert json.loads(child["native_execution"]["request"])["task_binding"] == body["task_binding"]
    assert factory.transports[1].options.max_budget_usd == .008
    assert len(factory.transports) == 2
    assert client.get(f"/api/projects/{pid}/native-reviews/{admission['admission_id']}/result").json() == frozen_export
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
    other = project(client, "original other project")
    assert client.get(f"/api/projects/{other}/native-reviews/{admission['admission_id']}").status_code == 404
    assert client.post(f"/api/projects/{other}/native-reviews/{admission['admission_id']}/{kind}", json=control).status_code == 404


def test_api_unknown_session_cost_blocks_continuation_before_new_admission(client, monkeypatch):
    pid, doc, req, task, run, route, body = formal(client)
    factory = NativeFactory([None])
    configured(monkeypatch, factory)
    submitted = client.post(route + "/native-reviews", json=body).json()
    completed = until_review(client, pid, submitted["admission_id"], {"RETURNED"})
    response = client.post(f"/api/projects/{pid}/native-reviews/{submitted['admission_id']}/resume", json={
        "operation_id": "original-unknown-cost", "expected_session_id": completed["native_execution"]["session_id"], "prompt": "Readonly followup"})
    assert response.status_code == 503 and "SESSION_COST_UNKNOWN" in response.json()["error"]["reasons"]
    assert len(factory.transports) == 1
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM task_native_reviews").fetchone()[0] == 1


@pytest.mark.parametrize("change", ["task", "document", "profile"])
def test_returned_native_export_survives_input_drift_while_new_control_is_refused(client, monkeypatch, change):
    pid, doc, req, task, run, route, body = formal(client)
    factory = NativeFactory([.002])
    configured(monkeypatch, factory)
    submitted = client.post(route + "/native-reviews", json=body).json()
    completed = until_review(client, pid, submitted["admission_id"], {"RETURNED"})
    prefix = f"/api/projects/{pid}/native-reviews/{submitted['admission_id']}"
    exported = client.get(prefix + "/result").json()
    events = client.get(prefix + "/events").json()
    if change == "task":
        response = client.put(f"/api/projects/{pid}/tasks/{task['id']}?expected_revision=1", json={
            "goal": "Original revised goal", "requirement_refs": task["spec"]["requirement_refs"]})
        assert response.status_code == 200, response.text
    elif change == "document":
        response = client.put(f"/api/projects/{pid}/documents/{doc['id']}", files={
            "file": ("original.md", b"Original changed source bytes")}, data={"expected_revision": 1})
        assert response.status_code == 200, response.text
    else:
        monkeypatch.setenv("EEAS_MODEL_ID", "original-changed-model-profile")
    denied = client.post(prefix + "/resume", json={"operation_id": "original-refused-new-control",
        "expected_session_id": completed["native_execution"]["session_id"], "prompt": "Readonly original followup"})
    assert denied.status_code == 409, denied.text
    assert client.get(prefix + "/result").json() == exported
    assert client.get(prefix + "/events").json() == events
    assert exported["frozen_scope"]["task_binding"] == body["task_binding"]
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
    assert len(factory.transports) == 1
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM task_native_reviews").fetchone()[0] == 1


def test_real_pinned_extension_task_uses_its_composed_stack_for_readonly_native_review(client, monkeypatch):
    snapshot = import_and_activate(client, 1)
    pid, doc, req, task, run, route, body = formal(client, snapshot=snapshot)
    assert task["spec"]["extension_lock"]["snapshot_id"] == snapshot
    assert task["spec"]["base_stack_hash"] != task["spec"]["stack_lock_hash"]
    factory = NativeFactory([.002])
    configured(monkeypatch, factory)
    admitted = client.post(route + "/native-reviews", json=body)
    assert admitted.status_code == 202, admitted.text
    completed = until_review(client, pid, admitted.json()["admission_id"], {"RETURNED"})
    request = json.loads(completed["native_execution"]["request"])
    assert request["task_binding"]["stack_lock_hash"] == task["spec"]["stack_lock_hash"]
    assert factory.transports[0].options.tools == []
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 0


def test_api_active_steer_is_a_distinct_bound_review_on_same_native_call(client, monkeypatch):
    pid, doc, req, task, run, route, body = formal(client)
    transports = []
    def installed(options):
        wire = QueuedWire(options)
        transports.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    configured(monkeypatch, installed)
    submitted = client.post(route + "/native-reviews", json=body).json()
    parent = until_review(client, pid, submitted["admission_id"], {"RUNNING"})
    invalid_resume = client.post(f"/api/projects/{pid}/native-reviews/{submitted['admission_id']}/resume", json={
        "operation_id": "original-invalid-resume", "expected_session_id": parent["native_execution"]["session_id"], "prompt": "Readonly followup"})
    assert invalid_resume.status_code == 409
    steered = client.post(f"/api/projects/{pid}/native-reviews/{submitted['admission_id']}/steer", json={
        "operation_id": "original-steer", "expected_session_id": parent["native_execution"]["session_id"], "prompt": "Queued original readonly followup"})
    assert steered.status_code == 202, steered.text
    child = until_review(client, pid, steered.json()["admission_id"], {"RETURNED"})
    parent = until_review(client, pid, submitted["admission_id"], {"RETURNED"})
    assert len(transports) == 1 and len(transports[0].prompts) == 2
    assert child["native_execution_id"] != parent["native_execution_id"]
    assert child["task_binding"] == parent["task_binding"]
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run


def test_api_cancel_and_shutdown_never_mark_formal_run_cancelled(client, monkeypatch, tmp_path):
    pid, doc, req, task, run, route, body = formal(client)
    factory = NativeFactory([.002], modes=["cancel"])
    configured(monkeypatch, factory)
    admitted = client.post(route + "/native-reviews", json=body).json()
    until_review(client, pid, admitted["admission_id"], {"RUNNING"})
    canceled = client.post(f"/api/projects/{pid}/native-reviews/{admitted['admission_id']}/cancel")
    assert canceled.status_code == 200
    final = until_review(client, pid, admitted["admission_id"], {"CANCELLED"})
    assert final["native_execution"]["verification_status"] == "NOT_RUN"
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run


def test_health_reports_actual_applied_migrations(client):
    health = client.get("/health").json()
    with client.app.state.store.connect() as db:
        applied = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert health["schema_migrations"] == applied
        assert {"013_task_native_reviews.sql", "014_document_parses.sql"} <= set(applied)
        latest = max(int(name.split("_", 1)[0]) for name in applied)
        assert health["schema_version"] == latest
        added = f"{latest+1:03d}_original-health-fixture.sql"
        db.execute("INSERT INTO schema_migrations VALUES (?)", (added,))
    updated = client.get("/health").json()
    assert updated["schema_version"] == latest + 1 and added in updated["schema_migrations"]


def test_real_api_shutdown_retains_unknown_native_outcome_and_untouched_formal_run(tmp_path, monkeypatch):
    factory = NativeFactory([None], modes=["timeout"])
    configured(monkeypatch, factory)
    data = tmp_path / "original-shutdown-data"
    app = main.create_app(Settings(data))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
        client.headers.update({"Origin": "http://127.0.0.1:8765", "X-EEAS-CSRF": csrf})
        pid, doc, req, task, run, route, body = formal(client)
        admission = client.post(route + "/native-reviews", json=body).json()
        actual = until_review(client, pid, admission["admission_id"], {"RUNNING"})
    with sqlite3.connect(data / "eeas.sqlite3") as db:
        row = db.execute("SELECT execution_status,verification_status FROM agent_executions WHERE id=?", (actual["native_execution_id"],)).fetchone()
        assert row == ("UNKNOWN_OUTCOME", "NOT_RUN")
        assert db.execute("SELECT admission_status FROM task_native_reviews").fetchone()[0] == "UNKNOWN_OUTCOME"
        assert db.execute("SELECT execution_status,verification_status FROM task_runs").fetchone() == ("QUEUED", "NOT_RUN")
        assert db.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 0
    assert factory.transports[0].closed
