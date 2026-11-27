"""Explicit native-session campaign probe; real SDK with original model wire only."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from claude_agent_sdk import ClaudeSDKClient
import pytest

from campaign_native_probes import fixture_route, formal, options_receipt, prepared
from packages.adapters import claude_sdk
from packages.domain.tasks import digest, source_hashes
from packages.domain.store import Conflict
from test_claude_sessions import NativeFactory, QueuedWire
from test_task_sdk_api import claude_review_runtime, copy_frozen_source, until_review


def root_review(client, pid, task, initial, context, operation):
    if operation == "known":
        run = initial
    else:
        response = client.post(f"/api/projects/{pid}/tasks/{task['id']}/runs",
                               json={"revision": 1, "operation_id": "session-formal-" + operation})
        assert response.status_code == 202, response.text
        run = response.json()
    route = f"/api/projects/{pid}/tasks/{task['id']}/runs/{run['id']}"
    response = client.get(route + "/native-review-scope")
    assert response.status_code == 200, response.text
    scope = response.json()
    body = {"operation_id": "session-root-" + operation, "task_binding": scope["task_binding"],
            "source_hash": scope["source_hash"], "input_hash": scope["input_hash"],
            "chunk_selections": [{"document_id": context["document_id"], "revision": 1,
                                   "chunk_indexes": context["included_chunks"]}]}
    response = client.post(route + "/native-reviews", json=body)
    assert response.status_code == 202, response.text
    return run, response.json()["admission_id"]


def control(client, pid, review, kind, operation, quote, **overrides):
    native = review["native_execution"]
    body = {"operation_id": operation, "expected_session_id": native["session_id"],
            "prompt": "Readonly original-source followup: " + quote["quote"], **overrides}
    response = client.post(f"/api/projects/{pid}/native-reviews/{review['admission_id']}/{kind}", json=body)
    return response, body


def export(client, pid, reference):
    response = client.get(f"/api/projects/{pid}/native-reviews/{reference}/result")
    assert response.status_code == 200, response.text
    return response.json()


def counts(client):
    with client.app.state.store.connect() as db:
        return {name: db.execute("SELECT count(*) FROM " + name).fetchone()[0]
                for name in ("task_native_reviews", "agent_executions", "runner_jobs")}


def test_native_session(client, monkeypatch, tmp_path):
    captured, evidence, pid, doc, base, quote, parts, context = prepared(client, "native-session")
    task, initial, _ = formal(client, captured, evidence, pid, doc, quote)
    fixture_route(monkeypatch, True)
    # Original fixture result totals include each restored checkpoint's baseline.
    # The ledger must sum new increments, never reset authorization per client.
    factory = NativeFactory([0.002, 0.005, 0.007, 0.010])
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", factory)
    initial, ref = root_review(client, pid, task, initial, context, "known")
    parent = until_review(client, pid, ref, {"RETURNED"})
    parent_export = export(client, pid, ref)
    denied, _ = control(client, pid, parent, "resume", "wrong-current-session", quote,
                         expected_session_id="original-nonexistent-session")
    assert denied.status_code == 409 and len(factory.transports) == 1
    unknown = client.post(f"/api/projects/{pid}/native-reviews/00000000-0000-0000-0000-000000000000/resume", json={
        "operation_id": "nonexistent-execution", "expected_session_id": parent["native_execution"]["session_id"],
        "prompt": quote["quote"]})
    assert unknown.status_code == 404
    completed = [parent]
    controls = []
    for number, kind in enumerate(("resume", "fork", "resume"), 1):
        previous = completed[-1]
        response, body = control(client, pid, previous, kind, "known-followup-" + str(number), quote)
        assert response.status_code == 202, response.text
        child = until_review(client, pid, response.json()["admission_id"], {"RETURNED"})
        repeat, _ = control(client, pid, previous, kind, body["operation_id"], quote)
        assert repeat.status_code == 202 and repeat.json()["admission_id"] == child["admission_id"]
        conflict, _ = control(client, pid, previous, kind, body["operation_id"], quote, prompt="Different original followup")
        assert conflict.status_code == 409
        assert child["task_binding"] == parent["task_binding"]
        assert child["native_execution_id"] != previous["native_execution_id"]
        assert factory.transports[number].options.resume == previous["native_execution"]["session_id"]
        assert factory.transports[number].options.fork_session == (kind == "fork")
        completed.append(child)
        controls.append({"kind": kind, "body": body, "response": response.json(), "repeat": repeat.json(),
                         "conflict": conflict.json(), "completed": child})
        if number == 2:
            budget = client.app.state.bridge.control_preflight(pid, child["native_execution_id"], "resume",
                expected_session_id=child["native_execution"]["session_id"])["budget"]
            assert budget["spent_usd"] == "0.007" and budget["remaining_usd"] == "0.003"
            evidence.save("actual-known-budget-before-final-call.json", budget)
    assert [wire.options.max_budget_usd for wire in factory.transports] == [0.01, 0.008, 0.005, 0.003]
    historical, _ = control(client, pid, parent, "fork", "historical-session-head", quote)
    assert historical.status_code == 409
    exhausted, _ = control(client, pid, completed[-1], "resume", "shared-budget-exhausted", quote)
    assert exhausted.status_code == 503 and "SESSION_BUDGET_EXHAUSTED" in exhausted.json()["error"]["reasons"]
    assert counts(client) == {"task_native_reviews": 4, "agent_executions": 4, "runner_jobs": 0}
    assert export(client, pid, ref) == parent_export
    for review in completed:
        request = json.loads(review["native_execution"]["request"])
        assert request["document_refs"] == [{"document_id": doc["id"], "revision": 1, "sha256": doc["sha256"]}]
        assert not request["needs_tools"] and not request.get("search_enabled", False) and not request.get("image_refs")
        assert review["verification_status"] == "NOT_RUN" and not review["formal_run_changed"]
    evidence.save("actual-known-session-chain.json", {"parent": parent, "controls": controls,
        "bad_session": denied.json(), "historical_head": historical.json(), "exhausted": exhausted.json(),
        "options_and_writes": [{"options": options_receipt(wire), "writes": wire.writes} for wire in factory.transports]})

    # No version drift may create a new admission or alter an old result.
    before = counts(client)
    with monkeypatch.context() as changed:
        changed.setenv("EEAS_MODEL_PROFILE_ID", "original-different-profile")
        profile, _ = control(client, pid, completed[-1], "fork", "profile-drift", quote)
        assert profile.status_code == 409
    store = client.app.state.store
    copied = tmp_path / "original-copied-controller-source"
    copy_frozen_source(copied, task["spec"])
    path = copied / "packages/verifier/repair.py"
    original_code = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(b"\n# Original campaign fault: copied source bytes changed.\n")
    original_root = store.source_root
    try:
        store.source_root = copied
        source, _ = control(client, pid, completed[-1], "fork", "copied-source-drift", quote)
        assert source.status_code == 409
    finally:
        store.source_root = original_root
    assert counts(client) == before and export(client, pid, ref) == parent_export
    evidence.save("actual-version-drift-denials.json", {"profile": profile.json(), "copied_code": source.json(),
        "original_hash": task["spec"]["source_hash"], "changed_hash": digest(source_hashes(copied)), "counts": counts(client)})
    evidence.save("copied-source-before.py", original_code)
    evidence.save("copied-source-after.py", path.read_bytes())

    # Each fault scenario is a new explicit FormalRun with its own frozen root;
    # it never creates a second root for the already-bound known-budget run.
    unknown_factory = NativeFactory([None])
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", unknown_factory)
    unknown_run, unknown_ref = root_review(client, pid, task, initial, context, "unknown-cost")
    unknown_parent = until_review(client, pid, unknown_ref, {"RETURNED"})
    unknown_export = export(client, pid, unknown_ref)
    assert json.loads(unknown_parent["native_execution"]["result"])["total_cost_usd"] is None
    before = counts(client)
    unknown_denials = []
    for kind in ("resume", "fork"):
        response, _ = control(client, pid, unknown_parent, kind, "unknown-cost-" + kind, quote)
        assert response.status_code == 503 and "SESSION_COST_UNKNOWN" in response.json()["error"]["reasons"]
        unknown_denials.append(response.json())
    assert counts(client) == before and len(unknown_factory.transports) == 1
    assert export(client, pid, unknown_ref) == unknown_export
    evidence.save("actual-unknown-cost-denials.json", {"parent": unknown_export, "denials": unknown_denials, "counts": before})

    queued = []
    def installed(options):
        wire = QueuedWire(options)
        queued.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", installed)
    steer_run, steer_ref = root_review(client, pid, task, initial, context, "queued-steer")
    active = until_review(client, pid, steer_ref, {"RUNNING"})
    before = counts(client)
    not_terminal, _ = control(client, pid, active, "resume", "active-resume-denied", quote)
    assert not_terminal.status_code == 409 and counts(client) == before
    response, steer_body = control(client, pid, active, "steer", "queued-original-steer", quote)
    assert response.status_code == 202, response.text
    steered = until_review(client, pid, response.json()["admission_id"], {"RETURNED"})
    settled = until_review(client, pid, steer_ref, {"RETURNED"})
    repeat, _ = control(client, pid, active, "steer", steer_body["operation_id"], quote)
    assert repeat.status_code == 202 and repeat.json()["admission_id"] == steered["admission_id"]
    assert len(queued) == 1 and len(queued[0].prompts) == 2 and queued[0].closed
    assert steered["native_execution_id"] != settled["native_execution_id"]
    with store.connect() as db:
        scopes = [dict(row) for row in db.execute("SELECT * FROM agent_scopes WHERE execution_id IN (?,?)",
            (steered["native_execution_id"], settled["native_execution_id"]))]
    assert len({scope["native_call_execution_id"] for scope in scopes}) == 1
    evidence.save("actual-queued-steer.json", {"parent": settled, "child": steered, "repeated": repeat.json(),
        "active_resume_denied": not_terminal.json(), "scopes": scopes, "options": options_receipt(queued[0]), "writes": queued[0].writes,
        "semantics": "NATIVE_QUERY_QUEUED_FOLLOWUP_NOT_INSTANT_MANIPULATION"})

    cancelled_factory = NativeFactory([0.002], modes=["cancel"])
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", cancelled_factory)
    cancel_run, cancel_ref = root_review(client, pid, task, initial, context, "cancel")
    until_review(client, pid, cancel_ref, {"RUNNING"})
    cancelled_request = client.post(f"/api/projects/{pid}/native-reviews/{cancel_ref}/cancel")
    assert cancelled_request.status_code == 200
    cancelled = until_review(client, pid, cancel_ref, {"CANCELLED"})
    assert cancelled["native_execution"]["verification_status"] == "NOT_RUN"
    assert cancelled_factory.transports[0].closed
    assert sum(message.get("request", {}).get("subtype") == "interrupt"
               for message in cancelled_factory.transports[0].writes) == 1
    cancelled_resume, _ = control(client, pid, cancelled, "resume", "cancelled-resume-denied", quote)
    assert cancelled_resume.status_code == 409
    evidence.save("actual-native-cancellation.json", {"requested": cancelled_request.json(), "confirmed": cancelled,
        "resume_denied": cancelled_resume.json(), "writes": cancelled_factory.transports[0].writes})

    # Formal review UI has no TTL override. Exercise the public readonly Bridge
    # contract's supported one-second authorization using this same source,
    # without a formal task binding or changing any immutable scope/time value.
    expiry_factory = NativeFactory([0.002])
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", expiry_factory)
    expiry_request = {"prompt": "Original authorization-expiry boundary: " + quote["quote"],
                      "needs_tools": False, "max_turns": 1, "timeout_seconds": 30, "session_ttl_seconds": 1,
                      "source_files": json.loads(parent["native_execution"]["request"])["source_files"],
                      "document_refs": [{"document_id": doc["id"], "revision": 1, "sha256": doc["sha256"]}]}
    bridge = client.app.state.bridge
    expiry_parent = asyncio.run(bridge.submit(pid, "source-bound-expiry", expiry_request))
    assert expiry_parent["execution_status"] == "RETURNED" and expiry_parent["session_id"]
    expiry_export = bridge.export_result(pid, expiry_parent["id"])
    with store.connect() as db:
        scope = dict(db.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (expiry_parent["id"],)).fetchone())
    delay = max(0, (datetime.fromisoformat(scope["expires_at"]) - datetime.now(timezone.utc)).total_seconds()) + 0.05
    assert 0 < delay <= 1.10
    time.sleep(delay)  # Real clock; no monkeypatched datetime or scope mutation.
    before = counts(client)
    for kind in ("resume", "fork"):
        with pytest.raises(Conflict, match="authorization expired"):
            asyncio.run(getattr(bridge, kind)(pid, expiry_parent["id"], "expired-" + kind,
                quote["quote"], expected_session_id=expiry_parent["session_id"]))
    assert len(expiry_factory.transports) == 1 and expiry_factory.transports[0].closed
    assert counts(client) == before and bridge.export_result(pid, expiry_parent["id"]) == expiry_export
    with store.connect() as db:
        assert dict(db.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (expiry_parent["id"],)).fetchone()) == scope
    evidence.save("actual-real-clock-expiry.json", {"request": expiry_request, "parent": expiry_export, "frozen_scope": scope,
        "observed_at": datetime.now(timezone.utc).isoformat(), "actual_sleep_seconds": delay,
        "resume_and_fork_denied": "AUTHORIZATION_EXPIRED", "counts_before_after": before,
        "options": options_receipt(expiry_factory.transports[0]), "writes": expiry_factory.transports[0].writes})

    before = counts(client)
    changed_raw = b"Original campaign injected document revision; not a new upstream release.\n"
    changed = client.put(base, files={"file": (captured["name"], changed_raw)},
                         data={"expected_revision": 1, "source": "original-native-session-drift"})
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    document, _ = control(client, pid, completed[-1], "fork", "source-document-revision-drift", quote)
    assert document.status_code == 409 and counts(client) == before
    assert client.get(base + "/download", params={"revision": 1}).content == captured["raw"]
    assert export(client, pid, ref) == parent_export and export(client, pid, unknown_ref) == unknown_export
    for run in (initial, unknown_run, steer_run, cancel_run):
        assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
        assert run["execution_status"] == "AWAITING_APPROVAL" and run["verification_status"] == "NOT_RUN"
    assert counts(client)["runner_jobs"] == 0
    with store.connect() as db:
        records = {name: [dict(row) for row in db.execute("SELECT * FROM " + name)] for name in
                   ("task_native_reviews", "agent_scopes", "agent_controls", "agent_checkpoints")}
    evidence.save("actual-document-drift-denial.json", {"revision2": changed.json(), "denial": document.json(), "counts": counts(client)})
    evidence.save("actual-session-durable-records.json", records)
    evidence.observe()
