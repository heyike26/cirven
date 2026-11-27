"""Same-origin exact-session gateway; one actual original CLI active-owner counter."""
import asyncio
import base64
import copy
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from packages.adapters.opencode_gateway import OpenCodeNativeGateway
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical
from test_documents import project
from test_opencode_planning import scene, transport, KEY


class HeldOriginalPlanningWire:
    """Controlled original company-protocol denial; never a model call or fake native server."""
    def __init__(self):
        self.received, self.release = threading.Event(), threading.Event(); self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append({"body": body, "auth_matches": self.headers.get("Authorization") == "Bearer " + KEY})
                owner.received.set(); owner.release.wait(30)
                raw = b'{"error":{"message":"Original protocol-only held denial","type":"invalid_request_error"}}'
                self.send_response(401); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers()
                try: self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError): pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    @property
    def base_url(self): return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
    def start(self): self.thread.start(); return self
    def close(self): self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)


def test_actual_original_owner_gateway_is_same_origin_exact_folder_session_and_expires_after_planning(scene):
    wire = HeldOriginalPlanningWire().start()
    async def check():
        native, request, _ = transport(scene, wire)
        gateway = OpenCodeNativeGateway(scene["service"], native, scene["authority"])
        assert gateway.describe(scene["pid"])["status"] == "UNAVAILABLE"
        task = asyncio.create_task(native.propose(request))
        try:
            assert await asyncio.to_thread(wire.received.wait, 30), "Actual original CLI did not reach the controlled provider"
            public = gateway.describe(scene["pid"], workspace_ref=request["workspace_ref"])
            assert public["status"] == "PARTIAL_READONLY_EXACT_SESSION", public
            execution_id, session_id = public["execution_id"], public["session_id"]
            assert public["server_url"] == "/api/agent-shell/native/" + execution_id
            assert public["long_lived_conversation"] is False and public["native_mutations_allowed"] is False
            assert public["original_app_interface"] == "DISABLED_REQUIRES_LONG_LIVED_SESSION_OWNER"
            response = await gateway.read(scene["pid"], execution_id, "/session/" + session_id, workspace_ref=request["workspace_ref"])
            body = json.loads(response.body)
            context = native.active[execution_id]
            runtime_directory = context["workspace"] / "runtime-project"
            assert response.status_code == 200 and body["id"] == session_id and body["directory"] == str(runtime_directory)
            assert runtime_directory != scene["folder"]
            original_params = context["client"].params
            for directory in (str(scene["folder"]), str(context["workspace"]), str(runtime_directory / "..")):
                context["client"].params = {"directory": directory}
                try:
                    with pytest.raises(Conflict): gateway.authorize(scene["pid"], execution_id, "GET", "/session/" + session_id)
                    with pytest.raises(Conflict): gateway.describe(scene["pid"], execution_id)
                finally: context["client"].params = original_params
            context["client"].params = [("directory", str(runtime_directory)), ("directory", str(scene["folder"]))]
            try:
                with pytest.raises(Conflict): gateway.authorize(scene["pid"], execution_id, "GET", "/session/" + session_id)
            finally: context["client"].params = original_params
            original_workspace = context["workspace"]
            context["workspace"] = scene["folder"]
            try:
                with pytest.raises(Conflict): gateway.authorize(scene["pid"], execution_id, "GET", "/session/" + session_id)
            finally: context["workspace"] = original_workspace
            context["session_id"] = "ses_other-execution"
            try:
                with pytest.raises(Conflict): gateway.authorize(scene["pid"], execution_id, "GET", "/session/" + session_id)
            finally: context["session_id"] = session_id
            # A real link at the expected literal path cannot borrow the native
            # execution's authority, even when it points to the same old folder.
            moved_directory = context["workspace"] / "runtime-project-relocated"
            runtime_directory.rename(moved_directory)
            try:
                runtime_directory.symlink_to(moved_directory, target_is_directory=True)
                with pytest.raises(Conflict): gateway.authorize(scene["pid"], execution_id, "GET", "/session/" + session_id)
            finally:
                if runtime_directory.is_symlink(): runtime_directory.unlink()
                moved_directory.rename(runtime_directory)
            after_guards = await gateway.read(scene["pid"], execution_id, "/session/" + session_id)
            assert after_guards.status_code == 200 and json.loads(after_guards.body)["id"] == session_id
            for method, path, query in [("POST", "/session", None), ("POST", "/session/" + session_id + "/prompt_async", None),
                ("GET", "/config", None), ("GET", "/provider/auth", None), ("GET", "/session/other-session", None),
                ("GET", "/session/" + session_id, {"directory": "/"})]:
                with pytest.raises(Conflict): gateway.authorize(scene["pid"], execution_id, method, path, query=query)
            other = project(scene["client"], "Original gateway other project")
            with pytest.raises(NotFound): gateway.describe(other, execution_id)
            changed = copy.deepcopy(request["workspace_ref"]); changed["binding_sha256"] = "0" * 64
            with pytest.raises(Conflict): gateway.describe(scene["pid"], execution_id, workspace_ref=changed)
            stream = gateway.stream(scene["pid"], execution_id, workspace_ref=request["workspace_ref"])
            connected = await asyncio.wait_for(anext(stream), timeout=5)
            assert json.loads(connected.decode().removeprefix("data: "))["type"] == "server.connected"
            await stream.aclose()
            assert len(wire.requests) == 1 and wire.requests[0]["auth_matches"]
            private = native.active[execution_id]["secrets"]
            encoded = canonical(public) + response.body.decode() + connected.decode()
            assert all(secret not in encoded for secret in private)
            wire.release.set(); result = await task
            assert result["status"] == "FAILED" and len(wire.requests) == 1
            ended = gateway.describe(scene["pid"], execution_id, workspace_ref=request["workspace_ref"])
            assert ended["status"] == "UNAVAILABLE" and ended["server_url"] is None and ended["reason"] == "NATIVE_EXECUTION_FINISHED"
            with pytest.raises(Conflict): await gateway.read(scene["pid"], execution_id, "/session/" + session_id)
            assert scene["file"].read_bytes() == scene["original"]
            evidence = os.environ.get("EEAS_GATEWAY_EVIDENCE_DIR")
            if evidence:
                out = Path(evidence); out.mkdir(parents=True, exist_ok=True)
                (out / "actual-native-gateway.json").write_text(canonical({"active": public, "ended": ended,
                    "native_session_response": body, "native_connected_frame": json.loads(connected.decode().removeprefix("data: ")),
                    "native_result": result, "upstream_calls": len(wire.requests), "paid_model_calls": 0,
                    "gui": "NOT_RUN", "qualification": "ACTUAL_ORIGINAL_CLI_ACTIVE_EXACT_SESSION_READS_ONLY"}))
        finally:
            wire.release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    try: asyncio.run(check())
    finally: wire.close()


def recorded_guard_scene(scene):
    wire = HeldOriginalPlanningWire()
    native, request, _ = transport(scene, wire)
    # A factual database/scope guard fixture, not an actual native process qualification.
    selected = copy.deepcopy(request); selected["timeout_seconds"] = 45; selected["needs_tools"] = False
    scope = native._scope(scene["pid"], selected)
    row, _ = native._record(scene["pid"], selected["operation_id"], selected, scope, scene["tmp_path"] / "original-gateway-record-guard-only")
    gateway = OpenCodeNativeGateway(scene["service"], native, scene["authority"])
    wire.server.server_close()
    return native, request, row, gateway


@pytest.mark.parametrize("path", ["https://external.invalid/session/x", "//external.invalid/session/x", "/session/x?directory=/", "/session/%78", "/session/x#fragment", "/session/x\\message", "/session/x\n"])
def test_gateway_rejects_remote_encoded_or_query_paths_before_native_reads(scene, path):
    native, request, row, gateway = recorded_guard_scene(scene)
    with pytest.raises(Conflict): gateway.authorize(scene["pid"], row["id"], "GET", path)
    assert native.active == {}


def test_unavailable_owner_cannot_be_advertised_as_live_or_restarted_from_get(scene):
    native, request, row, gateway = recorded_guard_scene(scene)
    status = gateway.describe(scene["pid"], row["id"])
    assert status["status"] == "UNAVAILABLE" and status["server_url"] is None and status["reason"] == "NATIVE_OWNER_NOT_CONNECTED"
    assert native.active == {}


def test_gateway_redacts_literal_and_base64_basic_values_and_secret_json_keys():
    password = "original-private-owner-password"; credential = "original-private-model-credential"
    basic = base64.b64encode(("eeas:" + password).encode()).decode()
    result = OpenCodeNativeGateway._redact({credential: {"message": "Basic " + basic, "raw": password}}, {"secrets": [password, credential]})
    encoded = canonical(result)
    assert credential not in encoded and basic not in encoded and password not in encoded


@pytest.mark.parametrize("prefix", ["https://external.invalid", "//external.invalid", "/route?server=x", "/route#fragment"])
def test_gateway_metadata_cannot_select_remote_server_url(prefix):
    with pytest.raises(ValueError): OpenCodeNativeGateway(None, None, None, prefix=prefix)
