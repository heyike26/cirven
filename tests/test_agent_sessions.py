"""Unmodified original CLI/API/loop. Only the external model is a wire fixture."""
import asyncio
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import site
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from packages.adapters.opencode import OpenCodeBridge
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.adapters.opencode_startup_guard import reject_unreviewed_project_origins
from packages.agent_sessions import AgentSessions
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Store, Conflict, InvalidInput
from packages.domain.tasks import canonical, digest
from conftest import LiveServer

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "var/opencode-ui-offline-guard/opencode"
KEY = "original-long-session-external-model-fixture"
MODEL = "long-session-wire-v1"


def tool(name, arguments):
    return {"tool": name, "arguments": arguments}


class ModelWire:
    def __init__(self, replies):
        self.replies = replies
        self.requests = []
        self.received, self.release = threading.Event(), threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                count = len(owner.requests)
                owner.requests.append({"body": body, "auth_matches": self.headers.get("Authorization") == "Bearer " + KEY})
                owner.received.set()
                if self.path != "/v1/chat/completions" or body["model"] != MODEL or not owner.requests[-1]["auth_matches"]:
                    self.send_error(400)
                    return
                reply = owner.replies[count] if count < len(owner.replies) else "UNEXPECTED_EXTRA_NATIVE_RPC"
                if reply == "HOLD":
                    owner.release.wait(20)
                    reply = "MODEL_RELEASED_AFTER_INTERRUPT"
                delta = {"role": "assistant", "content": reply} if isinstance(reply, str) else {
                    "role": "assistant", "tool_calls": [{"index": 0, "id": "call_native_" + str(count), "type": "function",
                        "function": {"name": reply["tool"], "arguments": canonical(reply["arguments"])}}]}
                finish = "stop" if isinstance(reply, str) else "tool_calls"
                chunks = [{"id": "chatcmpl-native-owner", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "chatcmpl-native-owner", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25}}]
                raw = ("".join("data: " + canonical(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


@pytest.fixture
def scene(tmp_path):
    assert CLI.is_file(), "The actual fixed original CLI is mandatory; no internal adapter mock is allowed"
    assert hashlib.sha256(CLI.read_bytes()).hexdigest() == CLI_SHA[os.name]
    data = tmp_path / "data"
    data.mkdir()
    store = Store(data / "eeas.sqlite3")
    pid = store.create_project("Original conversation test", "original local fixture")["id"]
    folder = tmp_path / "真实用户目录 空格"
    folder.mkdir()
    actual = folder / "real.md"
    actual.write_text("# Actual original input\nUSER_MANUAL_CONTENT_MUST_SURVIVE\n")
    workspaces = LocalWorkspaces(store, data)
    bound = workspaces.bind(pid, WorkspaceInput(idempotency_key="selected-original-folder", root=str(folder), allowed_write_paths=[]))
    manager = NativeCapabilities(store, data)
    return {"data": data, "store": store, "pid": pid, "folder": folder, "actual": actual,
            "original": actual.read_bytes(), "workspaces": workspaces, "bound": bound, "manager": manager}


def owner(scene, wire, *, secret=KEY):
    config = {"schema": "eeas.opencode-provider.v1", "id": "session-provider", "revision": 1,
        "version": VERSION, "source_commit": COMMIT, "executable": str(CLI), "executable_sha256": CLI_SHA[os.name],
        "provider_id": "eeas-session-provider", "protocol": "OPENAI_COMPATIBLE_CHAT_COMPLETIONS",
        "provider_url": wire.url, "model_id": MODEL, "secret_ref": "secret:session-wire", "transport_kind": "MOCK_WIRE"}
    path = scene["data"] / "actual-provider.json"
    path.write_text(canonical(config))
    registry = OpenCodeProviderRegistry(lambda *_: json.loads(path.read_bytes()), lambda _: secret)
    value = AgentSessions(scene["store"], scene["data"], provider_registry=registry,
                          workspaces=scene["workspaces"], capabilities=scene["manager"])
    bound = scene["bound"]
    request = {"operation_id": "open-original", "workspace_ref": {"id": bound["id"], "revision": 1, "binding_sha256": bound["binding_sha256"]},
        "provider_profile_ref": {"id": config["id"], "revision": 1, "sha256": digest(config)},
        "capability_binding_sha256": scene["manager"].native_binding_snapshot()["sha256"],
        "title": "Actual original conversation", "ttl_seconds": 120, "max_calls": 16, "max_output_tokens": 1024}
    return value, request


def control(view, operation_id):
    return {"operation_id": operation_id, "session_ref": view["session_ref"], "generation": view["generation"]}


async def until(value, scene, ident, predicate, *, timeout=30):
    deadline = asyncio.get_running_loop().time() + timeout
    latest = None
    while asyncio.get_running_loop().time() < deadline:
        latest = await value.inspect(scene["pid"], ident)
        if predicate(latest):
            return latest
        await asyncio.sleep(.1)
    raise AssertionError("Actual original session did not reach expected state: " + canonical({
        k: latest[k] for k in ("owner_status", "native_status", "active_operation_id", "pending_questions", "pending_permissions", "messages")}))


def retain(name, scene, value, wire, view):
    if os.environ.get("EEAS_AGENT_SESSIONS_EVIDENCE"):
        target = Path(os.environ["EEAS_AGENT_SESSIONS_EVIDENCE"])
        target.mkdir(parents=True, exist_ok=True)
        target.joinpath(name + ".json").write_text(canonical({"session": view,
            "events": value.events(scene["pid"], view["session_ref"]["id"], limit=200),
            "model_rpc_count": len(wire.requests), "paid_model_calls": 0, "qualification": "ACTUAL_ORIGINAL_CLI_ONLY_EXTERNAL_MODEL_FIXTURE",
            "native_windows": "NOT_RUN", "company_model": "NOT_VALIDATED", "user_file_preserved": scene["actual"].read_bytes() == scene["original"]}))


def test_original_long_session_two_messages_exact_history_and_reload_without_replay(scene):
    wire = ModelWire(["FIRST_ORIGINAL_REPLY", "SECOND_ORIGINAL_REPLY", "AFTER_RESTART_ORIGINAL_REPLY"]).start()

    async def check():
        value, request = owner(scene, wire)
        try:
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            native_id = opened["original_session_id"]
            assert opened["owner_status"] == "READY" and opened["long_lived_conversation"]
            assert any(a["name"] == "plan" for a in opened["original_agents"])
            for number, agent in [(1, "plan"), (2, "build")]:
                body = {**control(opened, "message-" + str(number)), "raw_text": "Original turn " + str(number), "agent": agent}
                accepted = await value.send(scene["pid"], ident, body)
                assert accepted["status"] == "ACCEPTED"
                assert await value.send(scene["pid"], ident, body) == accepted
                current = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
                assert value.operation(scene["pid"], ident, body["operation_id"])["status"] == "RETURNED"
                assert current["original_session_id"] == native_id
            assert len(wire.requests) == 2
            assert "Original turn 1" in canonical(wire.requests[1]["body"]["messages"])
            assert len([m for m in current["messages"] if m["info"]["role"] == "user"]) == 2
            await value.close()
            restarted, _ = owner(scene, wire)
            assert restarted.get(scene["pid"], ident)["owner_status"] == "DISCONNECTED"
            loaded = await restarted.load(scene["pid"], ident, {"operation_id": "load-exact", "session_ref": opened["session_ref"]})
            next_view = loaded["session"]
            assert next_view["original_session_id"] == native_id and next_view["generation"] == opened["generation"] + 1
            assert len(wire.requests) == 2 and len(next_view["messages"]) >= 4
            with pytest.raises(Conflict):
                await restarted.send(scene["pid"], ident, {**control(opened, "stale-generation"), "raw_text": "Never dispatch stale generation"})
            assert len(wire.requests) == 2
            await restarted.send(scene["pid"], ident, {**control(next_view, "message-3"), "raw_text": "Actual third turn"})
            final = await until(restarted, scene, ident, lambda v: v["active_operation_id"] is None)
            assert len(wire.requests) == 3 and "Original turn 1" in canonical(wire.requests[2]["body"]["messages"])
            assert scene["actual"].read_bytes() == scene["original"]
            assert KEY not in canonical(final) and "baseURL" not in canonical(final)
            retain("conversation-restart", scene, restarted, wire, final)
            await restarted.close()
        finally:
            await value.close()

    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_actual_native_question_exact_reply_then_real_todo(scene):
    wire = ModelWire([tool("question", {"questions": [{"question": "Which actual acceptance route?", "header": "Acceptance",
        "options": [{"label": "Local", "description": "Use the selected local fixture"}, {"label": "Other", "description": "Clarify later"}]}]}),
        tool("todowrite", {"todos": [{"content": "Verify actual selected local input", "status": "completed", "priority": "high"}]}),
        "QUESTION_AND_TODO_RETURNED"]).start()

    async def check():
        value, request = owner(scene, wire)
        try:
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            await value.send(scene["pid"], ident, {**control(opened, "question-message"), "raw_text": "Ask before deciding actual route"})
            pending = await until(value, scene, ident, lambda v: bool(v["pending_questions"]))
            question = pending["pending_questions"][0]
            bad = copy.deepcopy(question["request_ref"])
            bad["sha256"] = "0" * 64
            with pytest.raises(Conflict):
                await value.reply(scene["pid"], ident, bad["id"], {**control(opened, "stale-question-reply"), "request_ref": bad, "answers": [["Local"]]}, kind="QUESTION")
            assert len(wire.requests) == 1
            answer = await value.reply(scene["pid"], ident, question["request_ref"]["id"],
                {**control(opened, "actual-question-answer"), "request_ref": question["request_ref"], "answers": [["Local"]]}, kind="QUESTION")
            assert answer["original_request_processed"]
            final = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            assert len(wire.requests) == 3 and final["todo"][0]["status"] == "completed"
            assert "Local" in canonical(wire.requests[1]["body"]["messages"])
            assert not final["pending_questions"] and scene["actual"].read_bytes() == scene["original"]
            retain("questions-todo", scene, value, wire, final)
        finally:
            await value.close()

    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_scope_no_credentials_mcp_start_and_idempotency_do_not_dispatch(scene):
    wire = ModelWire([]).start()

    async def check():
        missing, request = owner(scene, wire, secret="")
        with pytest.raises(Conflict):
            await missing.open(scene["pid"], request)
        assert not missing.active and not wire.requests
        value, request = owner(scene, wire)
        request["workspace_ref"] = None
        request["command_allowlist"] = ["printf APPROVED_LITERAL_COMMAND"]
        with pytest.raises(Conflict):
            await value.open(scene["pid"], request)
        assert not value.active and not wire.requests
        await value.close()

    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_actual_original_task_permission_does_not_widen_then_real_child(scene):
    wire = ModelWire([tool("task", {"description": "Read only child review", "prompt": "Return a short read-only child review",
        "subagent_type": "explore"}), "ACTUAL_NATIVE_CHILD_REPLY", "PARENT_AFTER_REAL_CHILD"]).start()

    async def check():
        value, request = owner(scene, wire)
        try:
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            await value.send(scene["pid"], ident, {**control(opened, "task-message"), "raw_text": "Use one actual native explore child"})
            pending = await until(value, scene, ident, lambda v: bool(v["pending_permissions"]))
            permission = pending["pending_permissions"][0]
            assert permission["native_request"]["permission"] == "task"
            assert permission["native_request"]["patterns"] == ["explore"]
            assert "always" not in permission["allowed_choices"]
            with pytest.raises(Conflict):
                await value.reply(scene["pid"], ident, permission["request_ref"]["id"],
                    {**control(opened, "reject-wide-always"), "request_ref": permission["request_ref"], "reply": "always"}, kind="PERMISSION")
            assert len(wire.requests) == 1 and not pending["children"]
            reply = await value.reply(scene["pid"], ident, permission["request_ref"]["id"],
                {**control(opened, "allow-once-child"), "request_ref": permission["request_ref"], "reply": "once"}, kind="PERMISSION")
            assert reply["original_request_processed"]
            final = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            assert len(wire.requests) == 3 and len(final["children"]) == 1
            child = final["children"][0]
            assert child["parentID"] == opened["original_session_id"] and child["id"] != opened["original_session_id"]
            assert "ACTUAL_NATIVE_CHILD_REPLY" in canonical(wire.requests[2]["body"]["messages"])
            assert scene["actual"].read_bytes() == scene["original"]
            retain("native-task-child", scene, value, wire, final)
        finally:
            await value.close()

    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_actual_original_exact_command_requires_permission_then_executes(scene):
    command = "printf EEAS_ACTUAL_SESSION_COMMAND"
    wire = ModelWire([tool("bash", {"command": command, "description": "Print the actual original command marker"}),
                      "ORIGINAL_COMMAND_VERIFIED"]).start()

    async def check():
        value, request = owner(scene, wire)
        request.update(command_allowlist=[command], allowed_effects=["BACKGROUND_COMMAND"])
        try:
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            await value.send(scene["pid"], ident, {**control(opened, "command-message"), "raw_text": "Run the explicitly selected literal command"})
            pending = await until(value, scene, ident, lambda v: bool(v["pending_permissions"]))
            permission = pending["pending_permissions"][0]
            assert permission["native_request"]["permission"] == "bash"
            assert "once" in permission["allowed_choices"]
            await value.reply(scene["pid"], ident, permission["request_ref"]["id"],
                {**control(opened, "command-once"), "request_ref": permission["request_ref"], "reply": "once"}, kind="PERMISSION")
            final = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            parts = [part for message in final["messages"] for part in message["parts"] if part["type"] == "tool" and part["tool"] == "bash"]
            assert parts[0]["state"]["status"] == "completed" and parts[0]["state"]["metadata"]["exit"] == 0
            assert "EEAS_ACTUAL_SESSION_COMMAND" in parts[0]["state"]["output"] and len(wire.requests) == 2
            assert scene["actual"].read_bytes() == scene["original"]
            retain("actual-native-command", scene, value, wire, final)
        finally:
            await value.close()

    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_actual_imported_skill_and_official_mcp_load_invoke_then_source_change_blocks(scene):
    manager = scene["manager"]
    imported = manager.import_skill({"operation_id": "import-actual-skill", "id": "eeas-source-review", "expected_revision": 0,
        "content": (ROOT / "packages/planning/skills/source-review/SKILL.md").read_text(), "enabled": True})
    script = scene["data"] / "actual-mcp-server.py"
    script.write_bytes((ROOT / "tests/agent_sessions_mcp_fixture.py").read_bytes())
    executable = Path(sys.executable).resolve()
    receipts = scene["data"] / "actual-mcp-audit"
    receipts.mkdir()
    configured = manager.configure_mcp({"operation_id": "configure-actual-mcp", "id": "fixture", "expected_revision": 0,
        "configuration": {"type": "local", "command": [str(executable), "-B", str(script), "--site-packages", site.getsitepackages()[0],
            "--receipt-dir", str(receipts)], "files_sha256": {str(executable): hashlib.sha256(executable.read_bytes()).hexdigest(),
                str(script): hashlib.sha256(script.read_bytes()).hexdigest()}, "enabled": True, "timeout": 10000}})
    wire = ModelWire([tool("skill", {"name": "eeas-source-review"}), tool("fixture_local_line_count", {"path": "real.md"}),
                      "ACTUAL_SKILL_AND_MCP_RETURNED"]).start()

    async def check():
        value, request = owner(scene, wire)
        request['mcp_tool_allowlist'] = ['fixture_local_line_count']
        try:
            with pytest.raises(Conflict):
                await value.open(scene["pid"], request)
            assert not value.active and not wire.requests
            request.update(allowed_effects=["PROCESS_START"], mcp_tool_allowlist=["fixture_local_line_count"])
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            assert opened["capability_load"]["skills"][0]["config_ref"] == imported["config_ref"]
            assert opened["capability_load"]["skills"][0]["configured_in_original"]
            assert opened["capability_load"]["skills"][0]["loaded_in_original_session"]
            assert opened["capability_load"]["skills"][0]["discovery_receipt"]["native_path"] == "/api/skill"
            assert opened["capability_load"]["mcp"]["fixture"]["config_ref"] == configured["config_ref"]
            assert opened["capability_load"]["mcp"]["fixture"]["load_status"] == "connected"
            await value.send(scene["pid"], ident, {**control(opened, "skill-mcp-message"), "raw_text": "Use imported local-review and count the real local file"})
            pending = await until(value, scene, ident, lambda v: bool(v["pending_permissions"]))
            permission = pending["pending_permissions"][0]
            assert permission["native_request"]["permission"] == "fixture_local_line_count"
            await value.reply(scene["pid"], ident, permission["request_ref"]["id"],
                {**control(opened, "mcp-once"), "request_ref": permission["request_ref"], "reply": "once"}, kind="PERMISSION")
            final = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            observed = json.loads((receipts / "calls.jsonl").read_text())
            assert observed["sha256"] == hashlib.sha256(scene["original"]).hexdigest() and observed["lines"] == 2
            assert len(wire.requests) == 3 and scene["actual"].read_bytes() == scene["original"]
            assert final["capability_load"]["skills"][0]["invoked"] and final["capability_load"]["mcp"]["fixture"]["invoked"]
            script.write_text(script.read_text() + "\n# Actual concurrent capability-source change\n")
            with pytest.raises(Conflict):
                await value.send(scene["pid"], ident, {**control(opened, "changed-mcp-source"), "raw_text": "Must not silently run changed source"})
            assert len(wire.requests) == 3
            retain("skill-mcp-real-load-call", scene, value, wire, final)
            stopped = await value.controlled_disconnect(scene["pid"], ident, control(opened, "revoke-changed-capability-owner"))
            assert stopped["last_control_operation"]["status"] == "RETURNED" and not stopped["connected_owner"]
        finally:
            await value.close()

    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_real_main_http_and_restart_load_original_history_internal_readonly_workspace(scene):
    wire = ModelWire(["REAL_PUBLIC_API_FIRST", "REAL_PUBLIC_API_AFTER_RESTART"]).start()
    installed = scene["data"] / "provider-settings" / "runtime" / VERSION / "opencode"
    installed.parent.mkdir(parents=True)
    shutil.copy2(CLI, installed)  # The verified executable may reside on a different volume.
    server = LiveServer(scene["data"])
    if sys.flags.optimize:
        server.env["PYTHONOPTIMIZE"] = "1"

    def connect():
        client = httpx.Client(base_url=server.origin, trust_env=False, timeout=90)
        bootstrap = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
        assert bootstrap.status_code == 200
        client.headers.update({"Origin": server.origin, "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})
        return closing(client)

    def complete(client, root, operation):
        import time
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = client.get(root + "/operations/" + operation)
            assert result.status_code == 200
            if result.json()["status"] in {"RETURNED", "FAILED", "CANCELLED", "UNKNOWN_OUTCOME"}:
                return result.json()
            time.sleep(.1)
        raise AssertionError("Real public API original operation did not settle")

    try:
        server.start()
        with connect() as client:
            profile = client.post("/api/planning/providers", json={"operation_id": "actual-api-provider", "id": "actual-api-provider",
                "expected_revision": 0, "provider_url": wire.url, "model_id": MODEL, "api_key": KEY})
            assert profile.status_code == 201, profile.text
            configuration = client.get("/api/agent-sessions/configuration")
            assert configuration.status_code == 200
            opened = client.post("/api/agent-sessions/open", json={"operation_id": "actual-api-open", "project_id": None,
                "workspace_ref": None, "provider_profile_ref": profile.json()["profile_ref"],
                "capability_binding_sha256": configuration.json()["capability_binding_sha256"], "ttl_seconds": 120,
                "max_calls": 4, "max_output_tokens": 1024})
            assert opened.status_code == 200, opened.text
            view = opened.json()
            assert view["workspace_ref"] is None and view["workspace_kind"] == "APP_INTERNAL_READONLY"
            assert view["owner_status"] == "READY" and not view["native_mutations_allowed"]
            root = "/api/projects/" + view["project_id"] + "/agent-sessions/" + view["session_ref"]["id"]
            first = {**control(view, "actual-api-message"), "raw_text": "Keep this original first API turn", "agent": "plan"}
            accepted = client.post(root + "/messages", json=first)
            assert accepted.status_code == 200 and accepted.json()["status"] == "ACCEPTED", accepted.text
            assert client.post(root + "/messages", json=first).json() == accepted.json()
            assert complete(client, root, first["operation_id"])["status"] == "RETURNED"
            before = client.get(root).json()
            assert before["original_session_id"] == view["original_session_id"] and len(wire.requests) == 1
            assert any(m["info"]["role"] == "assistant" for m in before["messages"])
        server.stop()
        server.start()
        with connect() as client:
            historical = client.get(root)
            assert historical.status_code == 200 and historical.json()["owner_status"] == "DISCONNECTED"
            loaded = client.post(root + "/load", json={"operation_id": "actual-api-load", "session_ref": view["session_ref"]})
            assert loaded.status_code == 200, loaded.text
            current = loaded.json()["session"]
            assert current["original_session_id"] == view["original_session_id"] and current["generation"] == view["generation"] + 1
            assert len(wire.requests) == 1
            stale = client.post(root + "/messages", json={**control(view, "actual-api-stale"), "raw_text": "Never replay an old generation"})
            assert stale.status_code == 409 and len(wire.requests) == 1
            second = client.post(root + "/messages", json={**control(current, "actual-api-second"), "raw_text": "Continue the same original API conversation"})
            assert second.status_code == 200
            assert complete(client, root, "actual-api-second")["status"] == "RETURNED"
            final = client.get(root).json()
            assert len(wire.requests) == 2 and "Keep this original first API turn" in canonical(wire.requests[1]["body"]["messages"])
            assert KEY not in canonical(final) and "baseURL" not in canonical(final)
            events = client.get(root + "/events").json()
            assert events["events"] and not events["native_replay"]
            assert scene["actual"].read_bytes() == scene["original"]
            if os.environ.get("EEAS_AGENT_SESSIONS_EVIDENCE"):
                out = Path(os.environ["EEAS_AGENT_SESSIONS_EVIDENCE"])
                out.mkdir(parents=True, exist_ok=True)
                out.joinpath("real-public-http-restart.json").write_text(canonical({"session": final, "events": events,
                    "original_model_rpc_count": 2, "api_child_optimized_env": server.env.get("PYTHONOPTIMIZE"),
                    "paid_model_calls": 0, "windows": "NOT_RUN", "company_model": "NOT_VALIDATED"}))
    finally:
        server.stop()
        wire.close()



def test_actual_original_ancestor_plugin_process_positive_control_and_owner_rejection(scene):
    """Unmodified CLI imports a plugin which starts a real Python child;
    owner rejects those actual origins before original initialization.
    """
    plugins = scene["folder"].parent / ".opencode" / "plugins"
    plugins.mkdir(parents=True)
    marker = scene["data"] / "unapproved-plugin-child.json"
    python = str(Path(sys.executable).resolve())
    def source(target):
        code = "import os,json,pathlib; pathlib.Path(" + repr(str(target)) + ").write_text(json.dumps({'pid':os.getpid(),'ppid':os.getppid(),'actual_child':True}))"
        return "import {spawnSync} from 'node:child_process';\nspawnSync(" + canonical(python) + ", " + canonical(["-c", code]) + ", {stdio:'ignore'});\nexport default {id:'explicit-negative-origin-fixture',setup:async()=>{}};\n"
    (plugins / "real-child.js").write_text(source(marker))
    wire = ModelWire([]).start()

    async def check():
        home = scene["data"] / "positive-original-home"
        config = home / "config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        (home / "tmp").mkdir()
        config.write_text(canonical({"autoupdate": False, "snapshot": False, "lsp": False, "plugin": []}))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = OpenCodeBridge._environment(home, config, "actual-positive-fixture-password")
        logs = scene["data"] / "positive-original-stderr.bin"
        with logs.open("wb") as output:
            process = await asyncio.create_subprocess_exec(str(CLI), "--pure", "serve", "--hostname", "127.0.0.1", "--port", str(port),
                cwd=str(scene["folder"]), env=environment, stdout=output, stderr=output)
            try:
                async with httpx.AsyncClient(base_url="http://127.0.0.1:" + str(port), auth=("eeas", "actual-positive-fixture-password"), trust_env=False, timeout=10) as client:
                    deadline = asyncio.get_running_loop().time() + 20
                    while True:
                        try:
                            health = await client.get("/global/health")
                            if health.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        assert process.returncode is None and asyncio.get_running_loop().time() < deadline
                        await asyncio.sleep(.05)
                    observed = await client.get("/api/skill", params={"location[directory]": str(scene["folder"])})
                    assert observed.status_code == 200
                    deadline = asyncio.get_running_loop().time() + 8
                    while not marker.exists() and asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(.1)
                    assert marker.is_file(), "Positive control must observe a real child, not merely configuration bytes"
                    positive = json.loads(marker.read_bytes())
                    assert positive["actual_child"] and positive["ppid"] == process.pid and positive["pid"] != process.pid
                    await client.post("/instance/dispose", params={"directory": str(scene["folder"])})
            finally:
                if process.returncode is None:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 5)
        negative_marker = marker.with_name("owner-must-not-start-child.json")
        (plugins / "real-child.js").write_text(source(negative_marker))
        value, request = owner(scene, wire)
        try:
            with pytest.raises(Conflict, match="UNREVIEWED_ORIGINAL_CONFIG_OR_VCS_ANCESTOR"):
                await value.open(scene["pid"], request)
            assert not negative_marker.exists() and not value.active and not wire.requests
            if os.environ.get("EEAS_AGENT_SESSIONS_EVIDENCE"):
                target = Path(os.environ["EEAS_AGENT_SESSIONS_EVIDENCE"])
                target.mkdir(parents=True, exist_ok=True)
                (target / "original-ancestor-process-control.json").write_text(canonical({
                    "original_cli_sha256": CLI_SHA[os.name], "positive_actual_child": positive,
                    "original_parent_returncode": process.returncode, "negative_child_marker_exists": negative_marker.exists(),
                    "negative_owner_active": bool(value.active), "model_rpc_count": len(wire.requests),
                    "admission": "ACTUAL_CONFIG_ANCESTRY_PREFLIGHT", "os_sandbox": "NOT_QUALIFIED"}))
        finally:
            await value.close()

    try:
        asyncio.run(check())
    finally:
        wire.close()


@pytest.mark.parametrize("origin", ["directory", "document", "dangling-link", "lexical-link-parent"])
def test_startup_guard_unicode_actual_ancestor_and_link_sources(tmp_path, origin):
    actual = tmp_path / "真实 原生目录"
    actual.mkdir()
    reject_unreviewed_project_origins(actual)
    if origin == "directory":
        (tmp_path / ".opencode").mkdir()
    elif origin == "document":
        (tmp_path / "opencode.json").write_text("{}")
    elif origin == "dangling-link":
        (tmp_path / "opencode.jsonc").symlink_to(tmp_path / "未存在 文件")
    else:
        alias_parent = tmp_path / "别名 路径"
        alias_parent.mkdir()
        alias = alias_parent / "选择"
        alias.symlink_to(actual, target_is_directory=True)
        (alias_parent / ".opencode").mkdir()
        actual = alias
    with pytest.raises(Conflict, match="UNREVIEWED_ORIGINAL_CONFIG_OR_VCS_ANCESTOR"):
        reject_unreviewed_project_origins(actual)



def test_actual_original_interrupt_ack_no_replay_and_persistent_call_budget(scene):
    wire = ModelWire(["HOLD"]).start()
    async def check():
        value, request = owner(scene, wire)
        request["max_calls"] = 1
        restarted = None
        try:
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            body = {**control(opened, "interrupt-this-message"), "raw_text": "This actual request remains at the external wire until interrupted"}
            accepted = await value.send(scene["pid"], ident, body)
            assert await asyncio.to_thread(wire.received.wait, 10)
            interrupted = await value.interrupt(scene["pid"], ident, control(opened, "actual-abort-command"))
            assert interrupted["original_abort_acknowledged"] and not interrupted["external_actions_rolled_back"]
            wire.release.set()
            final = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            settled = value.operation(scene["pid"], ident, body["operation_id"])
            assert settled["status"] == "CANCELLED" and settled["semantic_acceptance"] is False
            assert await value.send(scene["pid"], ident, body) == settled  # Read same receipt; never re-dispatch.
            assert len(wire.requests) == 1 and final["model_calls_reserved"] == 1
            with pytest.raises(Conflict, match="exhausted"):
                await value.send(scene["pid"], ident, {**control(opened, "budget-exhausted"), "raw_text": "Must not make another model call"})
            assert value.operation(scene["pid"], ident, "budget-exhausted")["status"] == "FAILED"
            await value.close()
            restarted, _ = owner(scene, wire)
            loaded = await restarted.load(scene["pid"], ident, {"operation_id": "budget-preserving-explicit-load", "session_ref": opened["session_ref"]})
            current = loaded["session"]
            assert current["model_calls_reserved"] == 1 and current["generation"] == opened["generation"] + 1
            with pytest.raises(Conflict, match="exhausted"):
                await restarted.send(scene["pid"], ident, {**control(current, "budget-exhausted-after-load"), "raw_text": "Explicit load does not replenish the original call allowance"})
            assert len(wire.requests) == 1 and scene["actual"].read_bytes() == scene["original"]
            retain("interrupt-no-replay-persistent-budget", scene, restarted, wire, current)
        finally:
            wire.release.set()
            if restarted:
                await restarted.close()
            await value.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_actual_original_question_and_command_rejection_are_original_tool_outcomes(scene):
    command = "printf ORIGINAL_COMMAND_MUST_BE_REJECTED"
    wire = ModelWire([tool("question", {"questions": [{"question": "Accept this original decision?", "header": "Decision", "custom": False,
        "options": [{"label": "Yes", "description": "Proceed"}, {"label": "No", "description": "Stop"}]}]}),
        tool("bash", {"command": command, "description": "Rejected literal command", "timeout": 10000})]).start()
    async def check():
        value, request = owner(scene, wire)
        request.update(allowed_effects=["BACKGROUND_COMMAND"], command_allowlist=[command])
        try:
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            await value.send(scene["pid"], ident, {**control(opened, "original-question-reject-turn"), "raw_text": "Ask the original question"})
            pending = await until(value, scene, ident, lambda v: bool(v["pending_questions"]))
            question = pending["pending_questions"][0]
            with pytest.raises(InvalidInput):
                await value.reply(scene["pid"], ident, question["request_ref"]["id"],
                    {**control(opened, "invalid-single-question-answer"), "request_ref": question["request_ref"], "answers": [["Yes", "No"]]}, kind="QUESTION")
            rejected = await value.reply(scene["pid"], ident, question["request_ref"]["id"],
                {**control(opened, "actual-question-reject"), "request_ref": question["request_ref"]}, kind="QUESTION", reject=True)
            assert rejected["original_request_processed"]
            await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            assert value.operation(scene["pid"], ident, "original-question-reject-turn")["status"] == "CANCELLED"
            await value.send(scene["pid"], ident, {**control(opened, "original-command-reject-turn"), "raw_text": "Ask before the exact command"})
            pending = await until(value, scene, ident, lambda v: bool(v["pending_permissions"]))
            permission = pending["pending_permissions"][0]
            assert permission["native_request"]["permission"] == "bash"
            rejected = await value.reply(scene["pid"], ident, permission["request_ref"]["id"],
                {**control(opened, "actual-permission-reject"), "request_ref": permission["request_ref"], "reply": "reject"}, kind="PERMISSION")
            assert rejected["original_request_processed"]
            final = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            tools = [p for m in final["messages"] for p in m["parts"] if p["type"] == "tool"]
            assert all(part["state"]["status"] == "error" for part in tools)
            assert {p["tool"] for p in tools} == {"question", "bash"} and len(wire.requests) == 2
            assert value.operation(scene["pid"], ident, "original-command-reject-turn")["status"] == "CANCELLED"
            assert scene["actual"].read_bytes() == scene["original"]
            retain("original-question-permission-reject", scene, value, wire, final)
        finally:
            await value.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()



def test_actual_original_missing_session_preserves_historical_observation_and_private_origin_blocks_load(scene):
    wire = ModelWire(["ACTUAL_HISTORY_BEFORE_NATIVE_SESSION_DELETION"]).start()
    async def check():
        value, request = owner(scene, wire)
        try:
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            await value.send(scene["pid"], ident, {**control(opened, "history-before-native-error"), "raw_text": "Keep exact historical original messages"})
            observed = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            history_sha = digest(observed["messages"])
            context = value.active[ident]
            deleted = await value._http(context, "DELETE", "/session/" + opened["original_session_id"])
            assert deleted is True  # Actual original API and DB, not mocked HTTP.
            unavailable = await value.inspect(scene["pid"], ident)
            assert unavailable["owner_status"] == "OBSERVATION_UNAVAILABLE" and not unavailable["observation_is_current"]
            assert unavailable["observation_error"]["http_status"] == 404 and unavailable["native_messages_error"] is None
            assert digest(unavailable["messages"]) == history_sha and len(wire.requests) == 1
            with pytest.raises(Conflict, match="observation is unavailable"):
                await value.send(scene["pid"], ident, {**control(opened, "no-new-command-after-native-error"), "raw_text": "Must not forward on an unavailable session"})
            revoked = await value.controlled_disconnect(scene["pid"], ident, control(opened, "disconnect-after-original-error"))
            assert not revoked["connected_owner"] and revoked["last_control_operation"]["status"] == "RETURNED"
            private_plugins = value._path(scene["pid"], ident, "runtime", "home", "config", "opencode", "plugins")
            private_plugins.mkdir()
            (private_plugins / "unreviewed.js").write_text("export default {id:'unreviewed',setup:async()=>{}}")
            with pytest.raises(Conflict, match="UNREVIEWED_PRIVATE_ORIGINAL_CONFIG_SOURCE"):
                await value.load(scene["pid"], ident, {"operation_id": "refuse-unreviewed-private-source", "session_ref": opened["session_ref"]})
            assert value.operation(scene["pid"], ident, "refuse-unreviewed-private-source")["status"] == "FAILED"
            assert not value.active and len(wire.requests) == 1 and scene["actual"].read_bytes() == scene["original"]
            retain("actual-native-error-history-private-origin", scene, value, wire, unavailable)
        finally:
            await value.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()


@pytest.mark.parametrize('reporting_fails', [False, True])
def test_actual_startup_cursor_failure_always_reaps_exact_process(scene, monkeypatch, reporting_fails):
    """Actual original subprocess; inject only durable IO crash boundaries.

    No prompt is sent. The optional second IO fault denies failure reporting,
    proving that journal failure cannot skip captured-handle teardown.
    """
    wire = ModelWire([]).start()
    value, request = owner(scene, wire)
    captured = {}
    actual_event, actual_write = value._event, value._write
    event_scope = threading.local()
    triggered = []

    def failing_write(path, record, **kwargs):
        kind = getattr(event_scope, 'kind', None)
        if kind == 'ORIGINAL_SESSION_OWNER_READY' and path.name == 'state.json':
            # Require the exact newly appended READY record and its thread.
            # Background resource observations also update this state file.
            original = value._read(path.parent / 'events' / f"{record['last_event_seq']:010d}.json")
            if original['type'] == kind:
                triggered.append(kind)
                raise OSError('INJECTED_READY_CURSOR_PUBLICATION_FAILURE')
        if kind == 'ORIGINAL_OWNER_START_FAILED' and reporting_fails and path.parent.name == 'events' and record['type'] == kind:
            triggered.append(kind)
            raise PermissionError('INJECTED_FAILURE_REPORT_IO_DENIED')
        return actual_write(path, record, **kwargs)

    def at_event(pid, ident, kind, payload):
        if kind == 'ORIGINAL_SESSION_OWNER_READY' and not captured:
            context = value.active[ident]
            captured.update(context=context, process=context['process'], ident=ident)
        previous = getattr(event_scope, 'kind', None)
        event_scope.kind = kind if not triggered or kind == 'ORIGINAL_OWNER_START_FAILED' else None
        try:
            return actual_event(pid, ident, kind, payload)
        finally:
            event_scope.kind = previous

    monkeypatch.setattr(value, '_write', failing_write)
    monkeypatch.setattr(value, '_event', at_event)

    async def run():
        try:
            with pytest.raises(OSError, match='INJECTED_READY_CURSOR_PUBLICATION_FAILURE'):
                await value.open(scene['pid'], request)
            assert triggered == (['ORIGINAL_SESSION_OWNER_READY', 'ORIGINAL_OWNER_START_FAILED']
                                 if reporting_fails else ['ORIGINAL_SESSION_OWNER_READY'])
            process, ident = captured['process'], captured['ident']
            # Exact asyncio handle, not an empty state field, proves reaping.
            assert process.returncode is not None
            assert await process.wait() == process.returncode
            assert ident not in value.active
            state = value._state(scene['pid'], ident)
            assert state['owner_status'] == 'BLOCKED' and state['process_identity'] is None
            assert state['controller_identity'] is None and state['load_error'] == 'OSError'
            if reporting_fails:
                assert captured['context']['startup_reporting_error'] == 'PermissionError'
            events = value.events(scene['pid'], ident, limit=200)['events']
            assert [item['seq'] for item in events] == list(range(1, len(events) + 1))
            assert any(item['type'] == 'ORIGINAL_SESSION_OWNER_READY' for item in events)
            assert list(value._path(scene['pid'], ident, 'event-cursor-recoveries').glob('*.json'))
            ref = value._manifest(scene['pid'], ident)[1]
            # A verified stopped failed generation can be explicitly loaded;
            # its prior failure is not a permanent client dead end.
            result = await value.load(scene['pid'], ident, {'operation_id': 'explicit-after-start-failure',
                'session_ref': ref, 'expected_generation': state['generation']})
            assert result['operation']['status'] == 'RETURNED'
            assert result['session']['generation'] == state['generation'] + 1
            assert value._state(scene['pid'], ident)['load_error'] is None
            assert wire.requests == []
            assert scene['actual'].read_bytes() == scene['original']
        finally:
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_actual_explicit_generation_load_preserves_unknown_without_replaying_question(scene):
    """Actual original CLI and disk; only the external model is a local wire.

    Closing the waiting original owner supplies a real disconnected boundary.
    This is not a Windows GUI, hard-TTL timing, or live-account qualification.
    """
    wire = ModelWire([tool('question', {'questions': [{'question': 'Keep the saved manual text?',
        'header': 'Recovery', 'options': [{'label': 'Keep', 'description': 'Preserve it'}]}]}),
        'FRESH_EXPLICIT_MESSAGE_RETURNED']).start()

    async def run():
        first, request = owner(scene, wire)
        restarted = None
        try:
            opened = await first.open(scene['pid'], request)
            ident = opened['session_ref']['id']
            await first.send(scene['pid'], ident, {**control(opened, 'waiting-before-disconnect'), 'raw_text': 'Ask once; never replay this request'})
            waiting = await until(first, scene, ident, lambda s: bool(s['pending_questions']))
            old_question = copy.deepcopy(waiting['pending_questions'][0])
            await first.close()
            original = first.operation(scene['pid'], ident, 'waiting-before-disconnect')
            assert original['status'] == 'UNKNOWN_OUTCOME'
            restarted, _ = owner(scene, wire)
            body = {'operation_id': 'explicit-resume-exact-generation', 'session_ref': opened['session_ref'], 'expected_generation': opened['generation']}
            before_calls = len(wire.requests)
            with pytest.raises(Conflict):
                await restarted.load(scene['pid'], ident, {**body, 'operation_id': 'wrong-generation', 'expected_generation': opened['generation'] + 1})
            assert restarted.get(scene['pid'], ident)['generation'] == opened['generation']
            assert restarted.operation(scene['pid'], ident, 'waiting-before-disconnect') == original
            result = await restarted.load(scene['pid'], ident, body)
            restored = result['session']
            assert restored['generation'] == opened['generation'] + 1
            assert restored['original_session_id'] == opened['original_session_id']
            assert restored['active_operation_id'] is None and not restored['pending_questions']
            assert result['operation']['loaded_generation'] == restored['generation']
            assert result['operation']['request_sha256'] == digest(body)
            assert len(wire.requests) == before_calls
            assert restarted.operation(scene['pid'], ident, 'waiting-before-disconnect') == original
            # Repeating the same LOAD only reads its saved result. An old
            # question cannot grant execution or be answered in the new actor.
            assert (await restarted.load(scene['pid'], ident, body))['operation'] == result['operation']
            with pytest.raises(Conflict):
                await restarted.reply(scene['pid'], ident, old_question['request_ref']['id'],
                    {**control(restored, 'old-question-new-generation'), 'request_ref': old_question['request_ref'], 'answers': [['Keep']]}, kind='QUESTION')
            assert len(wire.requests) == before_calls
            await restarted.send(scene['pid'], ident, {**control(restored, 'fresh-explicit-message'), 'raw_text': 'This is a new explicit message; keep all manual text'})
            final = await until(restarted, scene, ident, lambda s: s['active_operation_id'] is None)
            assert restarted.operation(scene['pid'], ident, 'fresh-explicit-message')['status'] == 'RETURNED'
            assert restarted.operation(scene['pid'], ident, 'waiting-before-disconnect') == original
            assert len(wire.requests) == before_calls + 1
            assert scene['actual'].read_bytes() == scene['original']
            retain('explicit-generation-load-unknown-preserved', scene, restarted, wire, final)
        finally:
            await first.close()
            if restarted is not None:
                await restarted.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()
