"""Actual original owners and HTTP shutdown; only the external model is a fixture."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.owner_shutdown import OwnerShutdown, register_owner_shutdown
from apps.api.config import Settings
from apps.api.main import create_app
from conftest import ROOT
from packages.adapters.opencode_profile import VERSION
from packages.domain.tasks import canonical
from test_agent_sessions import scene as session_scene, owner, ModelWire, control, CLI, KEY, MODEL
from test_opencode_planning import scene as planning_scene, transport
from test_opencode_gateway import HeldOriginalPlanningWire
from test_native_collaboration_runtime import assembly
from test_native_collaboration import TOKEN, BIND


def record(name, receipt, **facts):
    root = os.environ.get("EEAS_OWNER_SHUTDOWN_EVIDENCE")
    if root:
        path = Path(root) / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical({"receipt": receipt, **facts, "windows": "NOT_RUN", "paid_model_calls": 0}), encoding="utf-8")


def outcome(receipt, kind):
    return next(row for row in receipt["outcomes"] if row["owner_kind"] == kind)


def test_actual_pending_planning_owner_cancelled_close_callers_share_one_receipt(planning_scene):
    wire = HeldOriginalPlanningWire().start()
    async def check():
        native, request, _ = transport(planning_scene, wire)
        app = planning_scene["client"].app
        app.state.planning_proposals.transport = native
        closer = app.state.owner_shutdown
        task = asyncio.create_task(native.propose(request))
        try:
            assert await asyncio.to_thread(wire.received.wait, 30)
            execution_id, context = next(iter(native.active.items()))
            process = context["process"]
            assert process.returncode is None
            cancelled = asyncio.create_task(closer.close())
            await asyncio.sleep(0)
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
            first, concurrent = await asyncio.gather(closer.close(), closer.close())
            assert first == concurrent == await closer.close()
            assert first["status"] == "CLOSED", first
            assert process.returncode is not None and native.active == {}
            row = outcome(first, "PLANNING")
            assert row["captured_process_count"] == row["stopped_process_count"] == 1
            assert row["pending_process_count"] == 0 and not row["blocked"]
            settled = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(settled[0], asyncio.CancelledError)
            assert native.inspect(planning_scene["pid"], execution_id)["execution_status"] == "UNKNOWN_OUTCOME"
            assert len(wire.requests) == 1 and planning_scene["file"].read_bytes() == planning_scene["original"]
            restored = OwnerShutdown(SimpleNamespace(state=SimpleNamespace()), planning_scene["tmp_path"])
            assert restored.inspect(first["shutdown_ref"]) == first
            assert restored.inspect()["status"] == "OPEN"
            assert KEY not in canonical(first) and str(planning_scene["folder"]) not in canonical(first)
            record("planning-concurrent-cancelled-caller", first, original_rpc_count=len(wire.requests),
                   original_process_returncode=process.returncode, source_unchanged=True, receipt_restored=True)
        finally:
            wire.release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    # The app supervisor was created on the TestClient portal loop.
    try: planning_scene["client"].portal.call(check)
    finally: wire.close()


def test_private_runtime_close_uses_real_pending_original_session_owner(session_scene):
    wire = ModelWire(["HOLD"]).start()
    app, runtime, _source, _factory = assembly(session_scene["data"])
    value, request = owner(session_scene, wire)
    app.state.agent_sessions = value
    closer = register_owner_shutdown(app, session_scene["data"])
    try:
        with TestClient(app) as client:
            bound = client.post("/api/desktop-host/collaboration/bind", json=BIND, headers={"X-EEAS-Desktop-Host": TOKEN})
            assert bound.status_code == 200, bound.text
            view = client.portal.call(value.open, session_scene["pid"], request)
            ident = view["session_ref"]["id"]
            client.portal.call(value.send, session_scene["pid"], ident,
                {**control(view, "held-before-private-close"), "raw_text": "Keep this real original request pending", "agent": "plan"})
            assert wire.received.wait(30)
            process = value.active[ident]["process"]
            assert process.returncode is None
            response = client.post("/api/desktop-host/collaboration/close", headers={"X-EEAS-Desktop-Host": TOKEN})
            assert response.status_code == 200, response.text
            receipt = response.json()
            assert receipt["status"] == "CLOSED", receipt
            assert process.returncode is not None and value.active == {} and runtime.closed
            assert outcome(receipt, "AGENT_SESSIONS")["stopped_process_count"] == 1
            assert outcome(receipt, "RUNTIME_SDK")["captured_thread_count"] == outcome(receipt, "RUNTIME_SDK")["stopped_thread_count"] == 1
            assert receipt["outcomes"][-1]["owner_kind"] == "RUNTIME_SDK"
            assert client.post("/api/desktop-host/collaboration/close", headers={"X-EEAS-Desktop-Host": TOKEN}).json() == receipt
            assert len(wire.requests) == 1
            assert value.operation(session_scene["pid"], ident, "held-before-private-close")["status"] == "UNKNOWN_OUTCOME"
            assert session_scene["actual"].read_bytes() == session_scene["original"]
            record("private-runtime-original-session", receipt, original_rpc_count=1,
                   original_process_returncode=process.returncode, simulated_gui_authority="POLICY_ONLY_NOT_WINDOWS")
    finally: wire.close()


@pytest.mark.skipif(os.name != "posix", reason="Own Linux SIGSTOP fault probe; not a Windows qualification")
def test_actual_original_process_blocked_after_active_pop_never_signs_closed(session_scene, monkeypatch):
    wire = ModelWire([]).start()
    stopped = []
    app = create_app(Settings(session_scene["data"], port=8765), shutdown=lambda: stopped.append(True))
    value, request = owner(session_scene, wire)
    app.state.agent_sessions = value
    request["capability_binding_sha256"] = session_scene["manager"].native_binding_snapshot()["sha256"]
    try:
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            bootstrap = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
            client.headers.update({"Origin": "http://127.0.0.1:8765", "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})
            view = client.portal.call(value.open, session_scene["pid"], request)
            ident = view["session_ref"]["id"]; process = value.active[ident]["process"]
            close_owner = value.close
            removed = []
            async def close_and_drop_registry():
                # Simulate registry loss after the real close has tried stopping its captured process.
                try:
                    return await close_owner()
                finally:
                    current = value.active.get(ident)
                    if current is not None and current.get('process') is process and current.get('closing'):
                        removed.append(value.active.pop(ident))
            monkeypatch.setattr(value, 'close', close_and_drop_registry)
            try:
                os.kill(process.pid, signal.SIGSTOP)  # This exact newly-created original owner only.
                result = client.post("/api/shutdown", json={})
                assert result.status_code == 200, result.text
                body = result.json(); receipt = body["owner_shutdown"]
                assert body["status"] == receipt["status"] == "CLOSE_UNKNOWN", body
                row = outcome(receipt, "AGENT_SESSIONS")
                assert row["captured_process_count"] == row["pending_process_count"] == 1
                assert len(removed) == 1 and process.returncode is None and value.active == {} and stopped == []
                assert value.get(session_scene["pid"], ident)["owner_status"] == "BLOCKED_PROCESS_STOP_UNCONFIRMED"
                assert client.post("/api/shutdown", json={}).json() == body
                assert client.get("/api/owner-shutdown").json() == receipt
                assert client.get("/health").status_code == 200
                denied = client.post("/api/projects", json={"name": "must not be created while draining"})
                assert denied.status_code == 409 and denied.json()["error"]["code"] == "OWNER_SHUTDOWN_IN_PROGRESS"
                assert stopped == []
                record("actual-blocked-owner-after-pop", receipt, original_registry_empty=True,
                       original_handle_still_running=True, new_post_denied=True, api_shutdown_not_called=True,
                       model_rpc_count=len(wire.requests))
            finally:
                if process.returncode is None:
                    os.kill(process.pid, signal.SIGCONT)
                    async def settle(): await asyncio.wait_for(process.wait(), 10)
                    client.portal.call(settle)
    finally: wire.close()


@pytest.mark.parametrize("fault", ["owner-state-corruption", "final-receipt-collision"])
def test_actual_storage_fault_cannot_turn_owner_cleanup_into_success(session_scene, fault):
    wire = ModelWire([]).start()
    async def check():
        value, request = owner(session_scene, wire)
        app = FastAPI(); app.state.agent_sessions = value
        closer = OwnerShutdown(app, session_scene["data"])
        view = await value.open(session_scene["pid"], request)
        ident = view["session_ref"]["id"]
        process = value.active[ident]["process"]
        state_path = value._path(session_scene["pid"], ident, "state.json")
        original = state_path.read_bytes()
        if fault == "owner-state-corruption":
            state_path.write_text('{"actual_corrupt_fixture":true}', encoding="utf-8")
        else:
            closer._path(".json").mkdir(parents=True)
        try:
            receipt = await closer.close()
            assert receipt["status"] == "CLOSE_UNKNOWN", receipt
            assert process.returncode is not None
            if fault == "owner-state-corruption":
                assert outcome(receipt, "AGENT_SESSIONS")["close_call"] == "RAISED"
            else:
                assert outcome(receipt, "RECEIPT")["error_code"] == "FINAL_RECEIPT_UNAVAILABLE"
            assert await closer.close() == receipt
            record(fault, receipt, original_process_returncode=process.returncode, model_rpc_count=0)
        finally:
            state_path.write_bytes(original)
            if ident in value.active:
                await value.disconnect(session_scene["pid"], ident)
    try: asyncio.run(check())
    finally: wire.close()


def test_admitted_start_and_writes_remain_unknown_without_replaying_actions(tmp_path):
    async def check():
        app = FastAPI()
        closer = OwnerShutdown(app, tmp_path, close_timeout_seconds=.01)
        token = closer.enter("POST", "/api/agent-sessions/open")
        receipt = await closer.close()
        assert receipt["status"] == "CLOSE_UNKNOWN"
        assert outcome(receipt, "ADMITTED_REQUESTS")["pending_request_count"] == 1
        closer.leave(token)
        assert await closer.close() == receipt
        assert closer.allowed_during_close("GET", "/api/projects/p/agent-sessions/s")
        assert closer.allowed_during_close("POST", "/api/projects/p/agent-sessions/s/disconnect")
        assert not closer.allowed_during_close("POST", "/api/projects/p/agent-sessions/s/messages")
        assert not closer.allowed_during_close("PUT", "/api/projects/p/requirements")
    asyncio.run(check())


class ActualServer:
    """Own TCP fixture with retained cold-start logs, not a replaced API/server."""
    def __init__(self, data):
        self.data = data
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); self.port = probe.getsockname()[1]
        self.origin = "http://127.0.0.1:" + str(self.port)
        self.log = (data / "actual-api-server.log").open("wb")
        self.env = {**os.environ, "EEAS_DATA_DIR": str(data), "EEAS_PORT": str(self.port), "EEAS_HOST": "127.0.0.1"}
        if sys.flags.optimize: self.env["PYTHONOPTIMIZE"] = "1"
        self.process = None

    def start(self):
        self.process = subprocess.Popen([sys.executable, "-m", "apps.api", "serve"], cwd=ROOT,
            env=self.env, stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 45  # Explicit QA readiness, not the old LiveServer assertion.
        with httpx.Client(trust_env=False) as client:
            while time.monotonic() < deadline:
                assert self.process.poll() is None, "Actual API exited before readiness; retained log"
                try:
                    if client.get(self.origin + "/health", timeout=.2).status_code == 200: return
                except httpx.HTTPError: pass
                time.sleep(.05)
        raise AssertionError("Actual API readiness deadline exceeded; retained log")

    def cleanup(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()  # Only this fixture's exact child; assertion has already failed.
            self.process.wait(timeout=10)
        self.log.close()


def test_real_tcp_shutdown_flushes_closed_receipt_exits_cleanly_and_does_not_replay(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    installed = data / "provider-settings/runtime" / VERSION / "opencode"
    installed.parent.mkdir(parents=True); shutil.copyfile(CLI, installed)
    if os.name != 'nt':
        installed.chmod(0o700)
    wire = ModelWire(["HOLD"]).start()
    server = ActualServer(data)
    try:
        server.start()
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=45) as client:
            bootstrap = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
            assert bootstrap.status_code == 200
            client.headers.update({"Origin": server.origin, "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})
            configured = client.post("/api/planning/providers", json={"operation_id": "shutdown-provider", "id": "shutdown-provider",
                "expected_revision": 0, "provider_url": wire.url, "model_id": MODEL, "api_key": KEY})
            assert configured.status_code == 201, configured.text
            configuration = client.get("/api/agent-sessions/configuration").json()
            opened = client.post("/api/agent-sessions/open", json={"operation_id": "shutdown-open", "project_id": None,
                "workspace_ref": None, "provider_profile_ref": configured.json()["profile_ref"],
                "capability_binding_sha256": configuration["capability_binding_sha256"], "ttl_seconds": 120,
                "max_calls": 4, "max_output_tokens": 1024})
            assert opened.status_code == 200, opened.text
            view = opened.json(); root = "/api/projects/" + view["project_id"] + "/agent-sessions/" + view["session_ref"]["id"]
            accepted = client.post(root + "/messages", json={**control(view, "shutdown-held-message"), "raw_text": "Hold this actual original request", "agent": "plan"})
            assert accepted.status_code == 200, accepted.text
            assert wire.received.wait(30)
            response = client.post("/api/shutdown", json={})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "stopping" and body["owner_shutdown"]["status"] == "CLOSED", body
            assert outcome(body["owner_shutdown"], "AGENT_SESSIONS")["stopped_process_count"] == 1
            assert KEY not in canonical(body) and str(data) not in canonical(body)
        assert server.process.wait(timeout=10) == 0
        assert len(wire.requests) == 1
        restored = OwnerShutdown(SimpleNamespace(state=SimpleNamespace()), data)
        assert restored.inspect(body["owner_shutdown"]["shutdown_ref"]) == body["owner_shutdown"]
        record("actual-tcp-api-exit", body["owner_shutdown"], api_exit_code=0,
               response_complete_json_before_exit=True, original_rpc_count=1, api_child_optimized_env=server.env.get("PYTHONOPTIMIZE"))
    finally:
        wire.close()
        server.cleanup()


def test_real_tcp_shutdown_drains_an_admitted_original_session_start(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    installed = data / "provider-settings/runtime" / VERSION / "opencode"
    installed.parent.mkdir(parents=True); shutil.copyfile(CLI, installed)
    if os.name != 'nt':
        installed.chmod(0o700)
    wire = ModelWire([]).start()
    server = ActualServer(data)
    try:
        server.start()
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=45) as client:
            bootstrap = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
            client.headers.update({"Origin": server.origin, "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})
            configured = client.post("/api/planning/providers", json={"operation_id": "drain-provider", "id": "drain-provider",
                "expected_revision": 0, "provider_url": wire.url, "model_id": MODEL, "api_key": KEY})
            assert configured.status_code == 201, configured.text
            configuration = client.get("/api/agent-sessions/configuration").json()
            body = {"operation_id": "drain-open", "project_id": None, "workspace_ref": None,
                "provider_profile_ref": configured.json()["profile_ref"],
                "capability_binding_sha256": configuration["capability_binding_sha256"], "ttl_seconds": 120,
                "max_calls": 4, "max_output_tokens": 1024}
            with ThreadPoolExecutor(max_workers=1) as executor:
                admitted = executor.submit(client.post, "/api/agent-sessions/open", json=body)
                deadline = time.monotonic() + 10
                observed = None
                while time.monotonic() < deadline and not admitted.done():
                    observed = client.get("/api/owner-shutdown").json()
                    if observed["pending_request_count"]:
                        break
                    time.sleep(.01)
                assert observed is not None and observed["pending_request_count"] == 1, observed
                response = client.post("/api/shutdown", json={})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["status"] == "stopping", result
                receipt = result["owner_shutdown"]
                assert receipt["status"] == "CLOSED" and receipt["admitted_request_count"] == 1
                assert receipt["pending_request_count"] == 0
                opened = admitted.result(timeout=10)
                assert opened.status_code in {200, 409}, opened.text
            assert server.process.wait(timeout=10) == 0
            assert len(wire.requests) == 0
            record("actual-tcp-inflight-start-drain", receipt, admitted_open_http_status=opened.status_code,
                   original_rpc_count=0, api_exit_code=0, observed_admitted_start=True)
    finally:
        wire.close()
        server.cleanup()
