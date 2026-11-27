"""Approved original loopback MCP service; only external model wire is simulated."""
import asyncio
import json
import os
import socket
import threading
import time
import uuid
from unittest.mock import patch

from claude_agent_sdk import ClaudeSDKClient
from mcp.server import MCPServer
import uvicorn
import pytest

from packages.adapters.claude_sdk import LiveBlocked
from packages.adapters.search_mcp import NATIVE_SEARCH_TOOL
from packages.domain.store import Conflict
from test_claude_sessions import SessionWire, NativeFactory, context
from test_sdk_images import environment


def test_search_request_without_explicit_company_config_is_blocked(client, tmp_path, monkeypatch):
    environment(monkeypatch)
    monkeypatch.delenv("EEAS_SEARCH_MCP_CONFIG_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    with pytest.raises(LiveBlocked, match="SEARCH_MCP_NOT_CONFIGURED"):
        asyncio.run(bridge.submit(pid, "search-not-configured", request))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


@pytest.fixture
def local_mcp():
    server = MCPServer("original-approved-search-service")
    calls, requests, state = [], [], {}

    @server.tool(name="original_approved_search")
    async def original_search(q: str) -> dict:
        calls.append({"tool": "original_approved_search", "arguments": {"q": q}})
        if state.get("mode") == "unsupported": return {"unparsed_original_response": "Not a source lead contract"}
        snippet = "Original provided search snippet; original page was not fetched."
        if state.get("mode") == "secret": snippet += " original-search-secret"
        if state.get("mode") == "oversize": snippet = "x" * 300000
        return {"results": [{"url": "https://public-fixture.example.invalid/spec", "title": "Original source clue",
                             "snippet": snippet}]}

    @server.tool(name="fetch")
    async def forbidden_fetch(url: str) -> dict:
        calls.append({"tool": "fetch", "arguments": {"url": url}})
        return {"forbidden": True}

    app = server.streamable_http_app(json_response=True, stateless_http=True)
    async def observed_app(scope, receive, send):
        if scope["type"] == "http":
            requests.append({"path": scope["path"], "method": scope["method"], "headers": dict(scope["headers"])})
            if state.get("mode") == "redirect" and scope["path"] == "/mcp":
                from starlette.responses import RedirectResponse
                await RedirectResponse("/forbidden-page-fetch", status_code=307)(scope, receive, send)
                return
        await app(scope, receive, send)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    process = uvicorn.Server(uvicorn.Config(observed_app, log_level="critical", access_log=False, lifespan="on"))
    thread = threading.Thread(target=process.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not process.started and thread.is_alive() and time.monotonic() < deadline: time.sleep(0.01)
    assert process.started
    try: yield {"url": url, "calls": calls, "requests": requests, "state": state}
    finally:
        process.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


def search_config(tmp_path, monkeypatch, service, **extra):
    path = tmp_path / "approved-search-mcp.json"
    body = {"schema_version": "eeas.search-mcp.v1", "capability": "search_only",
            "transport": {"type": "http", "url": service["url"], "headers": {"Authorization": "Bearer original-search-secret"}},
            "upstream_tool_name": "original_approved_search", "query_argument": "q", **extra}
    path.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("EEAS_SEARCH_MCP_CONFIG_PATH", str(path))
    return path


class SearchWire(SessionWire):
    """The installed SDK serves real in-process MCP RPCs and permission hooks."""
    def __init__(self, options, cost=0.002, *, tool_name="web_search", arguments=None, hook_name=None):
        super().__init__(options, cost)
        self.tool_name = tool_name
        self.arguments = arguments or {"query": "Original public search clue"}
        self.hook_name = hook_name
        self.rpc_results = {}

    async def rpc(self, step, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if method != "notifications/initialized": message["id"] = step
        if params is not None: message["params"] = params
        await self.send.send({"type": "control_request", "request_id": "mcp-" + step,
                              "request": {"subtype": "mcp_message", "server_name": "eeas_search", "message": message}})

    async def start_rpc(self):
        await self.rpc("initialize", "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                     "clientInfo": {"name": "original-external-model-wire", "version": "1"}})

    async def write(self, data):
        for line in data.splitlines():
            message = json.loads(line)
            if message["type"] == "user":
                self.writes.append(message)
                await self.transcript({"type": "user", "uuid": str(uuid.uuid4()), "sessionId": self.session_id,
                                       "message": message["message"], "timestamp": "2026-10-13T00:00:00Z"})
                if self.hook_name is None: await self.start_rpc()
                else:
                    callback_id = self.initialization["hooks"]["PreToolUse"][0]["hookCallbackIds"][0]
                    await self.send.send({"type": "control_request", "request_id": "search-hook", "request": {
                        "subtype": "hook_callback", "callback_id": callback_id, "tool_use_id": "original-search-tool",
                        "input": {"hook_event_name": "PreToolUse", "tool_name": self.hook_name, "tool_input": self.arguments}}})
                continue
            await super().write(line + "\n")
            if message["type"] != "control_response": continue
            response = message["response"]
            identity = response.get("request_id", "")
            if identity == "search-hook":
                self.hook_reply = response["response"]
                if self.hook_reply.get("hookSpecificOutput", {}).get("permissionDecision") == "deny": await self.result()
                else: await self.start_rpc()
            elif identity.startswith("mcp-"):
                step = identity[4:]
                self.rpc_results[step] = response.get("response", {}).get("mcp_response", {})
                if step == "initialize": await self.rpc("initialized", "notifications/initialized")
                elif step == "initialized": await self.rpc("list", "tools/list")
                elif step == "list": await self.rpc("call", "tools/call", {"name": self.tool_name, "arguments": self.arguments})
                else: await self.result()


def test_real_http_mcp_search_executes_through_installed_native_sdk(client, tmp_path, monkeypatch, local_mcp):
    environment(monkeypatch)
    search_config(tmp_path, monkeypatch, local_mcp)
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    wires = []
    def installed(options):
        wire = SearchWire(options, hook_name=NATIVE_SEARCH_TOOL)
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed):
        row = asyncio.run(bridge.submit(pid, "real-native-search-only", request))
    assert row["execution_status"] == "RETURNED" and wires[0].closed
    listing = wires[0].rpc_results["list"]["result"]["tools"]
    assert [tool["name"] for tool in listing] == ["web_search"]
    result = wires[0].rpc_results["call"]["result"]
    assert not result.get("isError")
    output = json.loads(result["content"][0]["text"])
    assert output["kind"] == "SEARCH_LEAD" and not output["original_document_read"]
    assert output["leads"][0]["source_uri"] == "https://public-fixture.example.invalid/spec"
    assert not output["leads"][0]["original_document_read"]
    assert local_mcp["calls"] == [{"tool": "original_approved_search", "arguments": {"q": "Original public search clue"}}]
    assert all(item["path"] == "/mcp" for item in local_mcp["requests"])
    assert any(item["headers"].get(b"authorization") == b"Bearer original-search-secret" for item in local_mcp["requests"])
    assert wires[0].options.tools == [] and wires[0].options.allowed_tools == [NATIVE_SEARCH_TOOL]
    assert wires[0].options.setting_sources == [] and wires[0].options.strict_mcp_config
    assert not bridge.export_result(pid, row["id"])["live_evidence"]
    assert bridge.describe_capabilities()["search_mcp"]["api_compatibility"] == "NOT_VALIDATED"


def execute_search(bridge, pid, request, **wire_parameters):
    wires = []
    def installed(options):
        wire = SearchWire(options, **wire_parameters)
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed):
        row = asyncio.run(bridge.submit(pid, "original-search-matrix", request))
    assert row["execution_status"] == "RETURNED" and wires[0].closed
    return row, wires[0]


@pytest.mark.parametrize("tool_name", ["fetch", "web_fetch", "WebFetch", "Bash", "Read", "unknown"])
def test_native_registry_rejects_fetch_builtin_and_unknown_without_http(client, tmp_path, monkeypatch, local_mcp, tool_name):
    environment(monkeypatch)
    search_config(tmp_path, monkeypatch, local_mcp)
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    _, wire = execute_search(bridge, pid, request, tool_name=tool_name)
    assert wire.rpc_results["call"]["result"]["isError"]
    assert local_mcp["calls"] == [] and local_mcp["requests"] == []


@pytest.mark.parametrize("arguments", [{"query": "original", "url": "https://unapproved.example.invalid"},
                                        {"url": "https://unapproved.example.invalid"},
                                        {"query": "original", "tool_name": "fetch"},
                                        {"query": "original", "method": "GET"}, {"query": " "}, {"query": "x" * 2049}])
def test_search_schema_cannot_forward_arbitrary_fetch_parameters(client, tmp_path, monkeypatch, local_mcp, arguments):
    environment(monkeypatch)
    search_config(tmp_path, monkeypatch, local_mcp)
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    _, wire = execute_search(bridge, pid, request, arguments=arguments)
    assert wire.rpc_results["call"]["result"]["isError"]
    assert local_mcp["calls"] == [] and local_mcp["requests"] == []


@pytest.mark.parametrize("name", ["WebFetch", "WebSearch", "Read", "Bash", "mcp__eeas_search__fetch", "mcp__unknown__web_search"])
def test_actual_pretooluse_also_denies_all_nonsearch_tools(client, tmp_path, monkeypatch, local_mcp, name):
    environment(monkeypatch)
    search_config(tmp_path, monkeypatch, local_mcp)
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    _, wire = execute_search(bridge, pid, request, hook_name=name)
    assert wire.hook_reply["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert local_mcp["calls"] == [] and local_mcp["requests"] == [] and not wire.rpc_results


def test_search_configuration_does_not_enable_default_request_or_code_tools(client, tmp_path, monkeypatch, local_mcp):
    environment(monkeypatch)
    search_config(tmp_path, monkeypatch, local_mcp)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        row = asyncio.run(bridge.submit(pid, "no-implicit-search", request))
        blocked = {**request, "needs_tools": True, "search_enabled": True}
        with pytest.raises(LiveBlocked, match="OS_ISOLATION_NOT_QUALIFIED"):
            asyncio.run(bridge.submit(pid, "no-search-code-bypass", blocked))
    assert row["execution_status"] == "RETURNED" and len(factory.transports) == 1
    assert factory.transports[0].options.mcp_servers == {}
    assert local_mcp["calls"] == [] and local_mcp["requests"] == []


@pytest.mark.parametrize("mode", ["unsupported", "oversize", "redirect"])
def test_actual_upstream_format_size_and_redirect_fail_closed(client, tmp_path, monkeypatch, local_mcp, mode):
    environment(monkeypatch)
    search_config(tmp_path, monkeypatch, local_mcp)
    local_mcp["state"]["mode"] = mode
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    row, wire = execute_search(bridge, pid, request)
    assert wire.rpc_results["call"]["result"]["isError"]
    assert all(item["path"] == "/mcp" for item in local_mcp["requests"])
    assert not any(call["tool"] == "fetch" for call in local_mcp["calls"])
    events = bridge.stream_events(pid, row["id"])
    assert len([event for event in events if event["payload"]["type"] == "search_call_reserved"]) == 1
    assert not [event for event in events if event["payload"]["type"] == "search_result"]


def test_selected_mcp_secret_is_redacted_from_native_response_and_durable_exports(client, tmp_path, monkeypatch, local_mcp):
    environment(monkeypatch)
    path = search_config(tmp_path, monkeypatch, local_mcp)
    local_mcp["state"]["mode"] = "secret"
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    row, wire = execute_search(bridge, pid, request)
    path.unlink()
    public = json.dumps(wire.rpc_results["call"]) + json.dumps(bridge.stream_events(pid, row["id"])) + json.dumps(bridge.export_result(pid, row["id"]))
    with bridge.service.connect() as connection:
        stored = " ".join(event[0] for event in connection.execute("SELECT payload FROM agent_events"))
    assert "original-search-secret" not in public + stored and "[REDACTED]" in public + stored


def test_shared_search_limit_survives_new_resume_and_fork_clients(client, tmp_path, monkeypatch, local_mcp):
    environment(monkeypatch)
    search_config(tmp_path, monkeypatch, local_mcp, limits={"max_calls": 2})
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    wires = []
    def installed(options):
        wire = SearchWire(options, cost=[0.002, 0.004, 0.006][len(wires)])
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed):
        parent = asyncio.run(bridge.submit(pid, "search-root-count", request))
        child = asyncio.run(bridge.resume(pid, parent["id"], "search-resume-count", "Original continuation",
                                         expected_session_id=parent["session_id"]))
        fork = asyncio.run(bridge.fork(pid, child["id"], "search-fork-count", "Original fork",
                                      expected_session_id=child["session_id"]))
    assert all(row["execution_status"] == "RETURNED" for row in (parent, child, fork))
    assert len(local_mcp["calls"]) == 2
    assert not wires[0].rpc_results["call"]["result"].get("isError")
    assert not wires[1].rpc_results["call"]["result"].get("isError")
    assert wires[2].rpc_results["call"]["result"]["isError"]
    assert "SEARCH_MCP_CALL_LIMIT" in json.dumps(wires[2].rpc_results["call"])
    assert [wire.options.max_budget_usd for wire in wires] == [0.01, 0.008, 0.006]
    with bridge.service.connect() as connection:
        scopes = [dict(row) for row in connection.execute("SELECT * FROM agent_scopes")]
    assert len({scope["root_execution_id"] for scope in scopes}) == 1


@pytest.mark.parametrize("mutation", ["stdio", "hooks", "permissions", "fetch_tool", "url_argument", "url_credentials",
                                        "url_query", "huge_limit", "boolean_limit", "host_header", "ambient_env", "symlink"])
def test_unapproved_mcp_configurations_fail_before_native_execution(client, tmp_path, monkeypatch, local_mcp, mutation):
    environment(monkeypatch)
    path = search_config(tmp_path, monkeypatch, local_mcp)
    body = json.loads(path.read_bytes())
    if mutation == "stdio": body["transport"] = {"type": "stdio", "command": "unapproved-command"}
    if mutation == "hooks": body["hooks"] = {"PreToolUse": "unapproved-command"}
    if mutation == "permissions": body["permissions"] = {"allow": ["WebFetch", "Bash(*)"]}
    if mutation == "fetch_tool": body["upstream_tool_name"] = "fetch"
    if mutation == "url_argument": body["query_argument"] = "url"
    if mutation == "url_credentials": body["transport"]["url"] = "http://private-user:original-secret@127.0.0.1/mcp"
    if mutation == "url_query": body["transport"]["url"] += "?token=original-secret"
    if mutation == "huge_limit": body["limits"] = {"max_calls": 1000000}
    if mutation == "boolean_limit": body["limits"] = {"max_calls": True}
    if mutation == "host_header": body["transport"]["headers"] = {"Host": "unapproved.example.invalid"}
    if mutation == "ambient_env": body["transport"]["header_env"] = {"Authorization": "CLAUDE_CODE_OAUTH_TOKEN"}
    path.write_text(json.dumps(body), encoding="utf-8")
    if mutation == "symlink":
        target = tmp_path / "auth.json"
        path.rename(target)
        path.symlink_to(target)
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    with pytest.raises(LiveBlocked, match="SEARCH_MCP_INVALID_CONFIGURATION"):
        asyncio.run(bridge.submit(pid, "bad-selected-search-config", request))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
    assert local_mcp["requests"] == []


def test_explicit_process_header_override_and_config_rotation_preserve_old_scope(client, tmp_path, monkeypatch, local_mcp):
    environment(monkeypatch)
    path = search_config(tmp_path, monkeypatch, local_mcp)
    body = json.loads(path.read_bytes())
    body["transport"]["header_env"] = {"Authorization": "EEAS_SEARCH_APPROVED_AUTHORIZATION"}
    path.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("EEAS_SEARCH_APPROVED_AUTHORIZATION", "Bearer original-process-search-token")
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    row, wire = execute_search(bridge, pid, request)
    assert not wire.rpc_results["call"]["result"].get("isError")
    assert any(item["headers"].get(b"authorization") == b"Bearer original-process-search-token" for item in local_mcp["requests"])
    before = bridge.stream_events(pid, row["id"])
    monkeypatch.setenv("EEAS_SEARCH_APPROVED_AUTHORIZATION", "Bearer original-rotated-process-token")
    with pytest.raises(Conflict, match="scope changed"):
        asyncio.run(bridge.resume(pid, row["id"], "no-mcp-credential-drift-resume", "original question", expected_session_id=row["session_id"]))
    assert bridge.inspect(pid, row["id"]) == row and bridge.stream_events(pid, row["id"]) == before
    public = json.dumps(bridge.describe_capabilities()["search_mcp"]) + repr(bridge._search())
    assert "original-rotated-process-token" not in public and str(path) not in public


def test_search_profile_change_in_real_sdk_connect_prevents_any_query_or_http(client, tmp_path, monkeypatch, local_mcp):
    environment(monkeypatch)
    path = search_config(tmp_path, monkeypatch, local_mcp)
    pid, bridge, request = context(client, tmp_path)
    request["search_enabled"] = True
    wires = []
    class RotateDuringConnect(SearchWire):
        async def connect(self):
            await super().connect()
            body = json.loads(path.read_bytes())
            body["limits"] = {"max_calls": 4}
            rotated = tmp_path / "new-approved-search.json"
            rotated.write_text(json.dumps(body), encoding="utf-8")
            os.replace(rotated, path)
    def installed(options):
        wire = RotateDuringConnect(options)
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed):
        row = asyncio.run(bridge.submit(pid, "search-scope-connect-rotation", request))
    assert row["execution_status"] == "FAILED" and row["result"] is None and wires[0].closed
    assert not [message for message in wires[0].writes if message["type"] == "user"]
    assert local_mcp["requests"] == []
