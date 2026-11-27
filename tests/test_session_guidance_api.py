"""Actual TCP/GUI-settings API -> pinned native loop -> in-flight guidance.

Only the external model is replaced.  The separately named monitor UNIT uses a
native source facade and cannot qualify the runtime/API.  No real credentials,
paid models, Windows UI, or company gateways are used here.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time

import httpx
import psutil
import pytest

from packages.adapters.opencode_profile import CLI_SHA, VERSION
from packages.agent_sessions.service import AgentSessions
from packages.domain.tasks import canonical
from test_agent_sessions import CLI, KEY, MODEL, ModelWire
from test_session_guidance import UnitOwner
from test_workflow_app_runtime import LoggedServer


ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = ("packages/agent_sessions/guidance.py", "packages/agent_sessions/service.py",
                "apps/api/agent_sessions.py", "apps/api/main.py", "tests/test_agent_sessions.py",
                "tests/test_workflow_app_runtime.py", "tests/test_session_guidance.py")


def check(condition, message="guidance API acceptance failed"):
    if not condition:
        raise AssertionError(message)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def source_hashes():
    return {rel: sha(ROOT / rel) for rel in DEPENDENCIES}


def same_process_alive(identity):
    try:
        process = psutil.Process(identity["pid"])
        return process.is_running() and abs(process.create_time() - identity["created"]) < .01
    except psutil.Error:
        return False


class HeldModelGate(threading.Event):
    """External fixture gate; D-drive API checks may exceed its old 20s hold."""
    def __init__(self):
        super().__init__()
        self.timed_out = False

    def wait(self, timeout=None):
        released = super().wait(90)
        self.timed_out = self.timed_out or not released
        return released


@pytest.mark.parametrize("guidance_count", [1, 3], ids=["single-inflight", "three-inflight"])
def test_actual_tcp_guidance_same_owner_local_status_cohort_and_get_recovery(tmp_path, guidance_count):
    approved = Path(os.environ.get("EEAS_TEST_APPROVED_CLI_CACHE", str(CLI)))
    check(sha(approved) == CLI_SHA[os.name], "mandatory pinned CLI differs from approved hash")
    installed = tmp_path / "data" / "provider-settings" / "runtime" / VERSION / "opencode"
    installed.parent.mkdir(parents=True)
    if approved.stat().st_dev == installed.parent.stat().st_dev:
        os.link(approved, installed)
    else:
        shutil.copy2(approved, installed)
    before = source_hashes()
    wire = ModelWire(["HOLD", "ACTUAL_GUIDED_API_RESPONSE"])
    wire.release = HeldModelGate()
    wire.start()
    server = LoggedServer(tmp_path / "data")
    if sys.flags.optimize:
        # LoggedServer uses sys.executable but not its flags: propagate -O to
        # the actual API subprocess too, instead of qualifying only this test.
        server.env["PYTHONOPTIMIZE"] = str(sys.flags.optimize)
    trace, opened, closed, final, parent, guidance_records = [], None, None, None, None, []
    disconnect_attempted, cleanup_error = False, None
    try:
        server.start()
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=60) as client:
            bootstrap = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
            check(bootstrap.status_code == 200, "actual API bootstrap failed")
            client.headers.update({"Origin": server.origin, "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})

            def call(method, path, body=None):
                response = client.request(method, path, json=body)
                trace.append({"method": method, "path": path, "status_code": response.status_code})
                check(response.is_success, f"actual API {method} {path}: {response.status_code} {response.text}")
                return response.json()

            def wait_for(predicate, message, seconds=40):
                deadline = time.monotonic() + seconds
                current = None
                while time.monotonic() < deadline:
                    current = call("GET", session)
                    if predicate(current):
                        return current
                    time.sleep(.08)
                raise AssertionError(message + ": " + canonical({key: current.get(key) for key in (
                    "owner_status", "native_status", "active_operation_id", "observation_error", "guidance")}))

            pid = call("POST", "/api/projects", {"name": "运行中直接指导", "description": "original TCP wire qualification"})["id"]
            saved = call("POST", "/api/planning/providers", {
                "operation_id": "save-guidance-fixture-provider", "id": "guidance-fixture-provider",
                "provider_url": wire.url, "model_id": MODEL, "api_key": KEY, "auth_mode": "API_KEY"})
            configuration = call("GET", "/api/agent-sessions/configuration")
            opened = call("POST", f"/api/projects/{pid}/agent-sessions/open", {
                "operation_id": "open-guidance-api-owner", "provider_profile_ref": saved["profile_ref"],
                "capability_binding_sha256": configuration["capability_binding_sha256"],
                "ttl_seconds": 180, "max_calls": 8, "max_output_tokens": 1024})
            sid = opened["session_ref"]["id"]
            session = f"/api/projects/{pid}/agent-sessions/{sid}"
            check(opened["owner_status"] == "READY" and opened["workflow_ref"] is None)
            identity = opened["process_identity"]
            check(same_process_alive(identity), "actual original CLI identity is not alive")
            control = {"session_ref": opened["session_ref"], "generation": opened["generation"]}
            first = call("POST", session + "/messages", {**control, "operation_id": "original-task",
                "raw_text": "核对当前工程资料，先描述依据，不得改写任何用户原件", "agent": "plan", "timeout_seconds": 120})
            check(first["status"] == "ACCEPTED")
            check(wire.received.wait(10), "actual external model did not reach held first request")
            active = call("GET", session)
            check(active["active_operation_id"] == "original-task" and len(wire.requests) == 1)
            parent_before_status = call("GET", session + "/operations/original-task")
            status = call("POST", session + "/guidance", {**control, "operation_id": "local-progress",
                "expected_active_operation_id": "original-task", "raw_text": "现在进度如何？", "intent": "AUTO"})
            check(status["status"] == "RETURNED" and status["role"] == "LOCAL_STATUS" and status["model_calls"] == 0)
            check(len(wire.requests) == 1, "local progress asked the model")
            check(call("GET", session + "/operations/original-task") == parent_before_status,
                  "local status mutated the original task/deadline")
            check(call("GET", session)["active_operation_id"] == "original-task")
            submitted = []
            for index in range(guidance_count):
                key = "guidance-" + str(index + 1)
                text = "补充要求" + str(index + 1) + "：每项结论都保留来源，先报告不确定事项，再给建议；不要改写原件"
                receipt = call("POST", session + "/guidance", {**control, "operation_id": key,
                    "expected_active_operation_id": "original-task", "raw_text": text, "intent": "STEER"})
                check(receipt["status"] == "ACCEPTED" and receipt["original_active_operation_id"] == "original-task")
                check(receipt["original_session_id"] == opened["original_session_id"])
                submitted.append((key, text, receipt["native_message_id"]))
            observed = wait_for(lambda view: all(any(row["info"].get("id") == native_id and
                row["info"].get("role") == "user" and text in canonical(row["parts"]) for row in view["messages"])
                for _key, text, native_id in submitted), "not all exact native user messages were observed", seconds=15)
            check(observed["active_operation_id"] == "original-task" and len(wire.requests) == 1,
                  "original held run stopped or model work began before release")
            check(not wire.release.timed_out, "external fixture gate expired before explicit release")
            check(observed["original_session_id"] == opened["original_session_id"] and observed["process_identity"] == identity)
            wire.release.set()
            final = wait_for(lambda view: view["active_operation_id"] is None, "root original task never settled after guidance")
            parent = call("GET", session + "/operations/original-task")
            check(parent["status"] == "RETURNED" and parent["continuation_guidance_ids"] == [item[0] for item in submitted])
            check(parent["response_evidence"] == "RUN_COHORT_LATEST_GUIDANCE_RESPONSE" and parent["semantic_acceptance"] is False)
            check(parent["independent_verification"] == "NOT_RUN")
            check(final["original_session_id"] == opened["original_session_id"] and final["process_identity"] == identity)
            check(len(wire.requests) == 2, "in-flight guidance started extra model requests/loop")
            last_user = next(row for row in reversed(wire.requests[1]["body"]["messages"]) if row["role"] == "user")
            check(submitted[-1][1] in canonical(last_user), "latest guidance was absent from actual next native model input")
            check(all(text in canonical(wire.requests[1]["body"]["messages"]) for _key, text, _native_id in submitted))
            for index, (key, _text, native_id) in enumerate(submitted):
                receipt = call("GET", session + "/operations/" + key)
                check(receipt["native_message_id"] == native_id and receipt["native_user_observed"] is True)
                expected = "RETURNED" if index == len(submitted) - 1 else "COHORT_RETURNED"
                check(receipt["status"] == expected)
                if expected == "COHORT_RETURNED":
                    check(receipt["individual_response_observed"] is False and receipt["linked_latest_operation_id"] == submitted[-1][0])
                guidance_records.append(receipt)
            for _ in range(3):
                latest = call("GET", session)
                public = {row["operation_id"]: row for row in latest["guidance"]}
                check(all(public[row["id"]]["status"] == row["status"] for row in guidance_records))
                check(call("GET", session + "/operations/" + submitted[-1][0]) == guidance_records[-1])
            check(len(wire.requests) == 2, "GET recovery/observation dispatched a new model request")
            disconnect_attempted = True
            closed = call("POST", session + "/disconnect", {**control, "operation_id": "close-exact-guidance-owner"})
            check(closed["owner_status"] == "DISCONNECTED" and not closed["connected_owner"])
            check(closed["last_control_operation"]["exact_owned_process_stop_confirmed"] is True)
            check(not same_process_alive(identity), "exact disconnected original CLI is still alive")
    finally:
        wire.release.set()
        if opened and closed is None:
            # Even a failing assertion must revoke only this exact test owner.
            # Never replay a disconnect whose outcome may already be unknown.
            try:
                with httpx.Client(base_url=server.origin, trust_env=False, timeout=30) as cleanup:
                    boot = cleanup.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
                    boot.raise_for_status()
                    cleanup.headers.update({"Origin": server.origin, "X-EEAS-CSRF": boot.json()["csrf_token"]})
                    if disconnect_attempted:
                        response = cleanup.get(session + "/operations/close-exact-guidance-owner")
                    else:
                        disconnect_attempted = True
                        response = cleanup.post(session + "/disconnect", json={**control, "operation_id": "close-exact-guidance-owner"})
                    response.raise_for_status()
                    if "owner_status" in response.json():
                        closed = response.json()
            except Exception as error:
                cleanup_error = type(error).__name__
        try:
            server.stop()
        finally:
            wire.close()
            after = source_hashes()
            (tmp_path / "actual-guidance-api-evidence.json").write_text(json.dumps({
                "qualification": "ACTUAL_TCP_APP_GUI_SETTINGS_API_PINNED_CLI_ONLY_EXTERNAL_MODEL_WIRE",
                "guidance_count": guidance_count, "trace": trace, "opened": opened, "closed": closed,
                "final_session": final, "parent_receipt": parent, "guidance_receipts": guidance_records,
                "synthetic_external_model_requests": wire.requests,
                "model_rpc_count": len(wire.requests), "paid_model_calls": 0,
                "source_hashes_before": before, "source_hashes_after": after,
                "changed_sources_during_test": [name for name in before if before[name] != after[name]],
                "api_process_exit_code": server.process.poll() if server.process else None,
                "api_python_optimize_requested": server.env.get("PYTHONOPTIMIZE", "0"),
                "external_model_gate_seconds": 90, "external_model_gate_timed_out": wire.release.timed_out,
                "cleanup_error_type": cleanup_error,
                "exact_native_process_alive_after_cleanup": bool(opened and same_process_alive(opened["process_identity"])),
                "external_wire_thread_joined": not wire.thread.is_alive(),
                "windows": "NOT_RUN", "pro_inference": "NOT_RUN", "company_gateway": "NOT_RUN",
                "scope": "API_AND_NATIVE_RUNTIME_NOT_DESKTOP_GUI_OR_MODEL_QUALITY"}, ensure_ascii=False, indent=2) + "\n")


@pytest.mark.parametrize('renew_before_release', [True, False])
def test_unit_monitor_rechecks_deadline_after_waiting_for_guidance_lock(tmp_path, monkeypatch, renew_before_release):
    """Real owner monitor/storage/lock; native calls explicitly UNIT substituted."""
    async def run():
        value = UnitOwner(tmp_path / "unit-owner")
        context = value.context
        context["closing"] = False
        context.update(quiescent_observation=True, observed_command_epoch=0, native_command_epoch=0,
                       native_commands_inflight=0)
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        value._finish(value.pid, value.sid, "first", "ACCEPTED", deadline=expired)
        saw_old_deadline = asyncio.Event()
        original_operation = value.operation
        def observed_operation(*args):
            receipt = original_operation(*args)
            if receipt.get("deadline") == expired:
                saw_old_deadline.set()
            return receipt
        monkeypatch.setattr(value, "operation", observed_operation)
        inspections, aborts = [], []
        async def inspected(_pid, _sid):
            inspections.append(True)
            # Let the first real monitor cycle reach its deadline/lock check.
            # The old facade closed here, so it could never exercise that race.
            if len(inspections) > 1:
                context["closing"] = True
            return {**value._state(value.pid, value.sid), 'observation_is_current': True}
        async def aborted(_pid, _sid, _context, operation):
            # Only the native abort boundary is substituted in this UNIT case.
            aborts.append(operation['id'])
            context['closing'] = True
        monkeypatch.setattr(value, "inspect", inspected)
        monkeypatch.setattr(value, '_deadline_abort', aborted)
        await context["lock"].acquire()
        task = asyncio.create_task(AgentSessions._monitor(value, value.pid, value.sid, context))
        try:
            await asyncio.wait_for(saw_old_deadline.wait(), 3)
            check(not task.done(), "monitor did not wait for the shared dispatch lock")
            renewed = (datetime.now(timezone.utc) + timedelta(seconds=40)).isoformat()
            if renew_before_release:
                value._finish(value.pid, value.sid, "first", "ACCEPTED", deadline=renewed)
            context["lock"].release()
            await asyncio.wait_for(task, 4)
            check(not value.http_calls, "monitor aborted based on stale pre-guidance deadline")
            check(aborts == ([] if renew_before_release else ['first']),
                  'renewed deadline must survive; still-expired control must reach the native abort boundary')
            check(original_operation(value.pid, value.sid, "first")["status"] == "ACCEPTED")
            check(value._state(value.pid, value.sid)["active_operation_id"] == "first")
        finally:
            if context["lock"].locked():
                context["lock"].release()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
