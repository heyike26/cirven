"""Invoke exactly the MCP connector selected for this workflow node.

A connector is user-installed code or an explicitly registered HTTPS service.
The SDK never gets credentials, ambient configuration or roots from the model
session. Local process environment isolation is not an OS sandbox.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import tempfile

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, PaginatedRequestParams

from packages.adapters.mcp_resources import MCPSourceTransport
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.dynamic_workflow.kernel_settings import home
from packages.dynamic_workflow.stage_assertions import (
    action_digest, pointer_tokens, readback_descriptor_ok, readback_in_window, resolve_case_action, sample_time,
)
from packages.dynamic_workflow.stage_workspace import _operation_records, _record, _scope, ledger

MAX_RESULT = 2 * 1024 * 1024  # accommodates modest image observations
MAX_DIRECTORY = 256 * 1024
MAX_ARGUMENTS = 64 * 1024
MAX_CALLS = 48


def _encoded(value, limit):
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError("连接器字段必须是有效 JSON") from exc
    if len(data) > limit:
        raise ValueError("连接器输入或响应超过节点限制")
    return data


def _digest(value):
    return hashlib.sha256(_encoded(value, MAX_RESULT)).hexdigest()


def _current(ctx, server):
    refs = ctx.get("capability_refs", {}).get("mcp", {})
    if (not isinstance(server, str) or server not in ctx.get("capabilities", {}).get("mcp", [])
            or server not in refs or refs[server].get("id") != server):
        raise ValueError("当前节点没有选用此 MCP 连接器")
    try:
        state = NativeCapabilities(None, ctx["_data_dir"]).mcp_content(refs[server])
    except (Conflict, InvalidInput, NotFound, OSError) as exc:
        raise ValueError("所选 MCP 的当前版本或登记状态已变化") from exc
    if not state["configuration"]["enabled"] or state["integrity"] != "CURRENT":
        raise ValueError("所选 MCP 已停用或文件校验失败")
    return state["configuration"]


def _reserve(ctx):
    with ledger(ctx["_data_dir"]) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS connector_counts(scope TEXT,op TEXT,count INTEGER NOT NULL,PRIMARY KEY(scope,op))")
        key = (_scope(ctx), ctx["operation_id"])
        current = db.execute("SELECT count FROM connector_counts WHERE scope=? AND op=?", key).fetchone()
        if current and current[0] >= MAX_CALLS:
            raise ValueError("本次节点执行的 MCP 调用次数已达上限")
        db.execute("INSERT INTO connector_counts VALUES(?,?,1) ON CONFLICT(scope,op) DO UPDATE SET count=count+1", key)


@asynccontextmanager
async def _streams(ctx, config):
    if config["type"] == "local":
        with tempfile.TemporaryDirectory(prefix="mcp-", dir=home(ctx["_data_dir"])) as directory:
            env = dict.fromkeys(DEFAULT_INHERITED_ENV_VARS, "")
            for key in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE"):
                if os.environ.get(key):
                    env[key] = os.environ[key]
            for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "TMPDIR", "TEMP", "TMP"):
                env[key] = directory
            env.update(USER="cirven", LOGNAME="cirven", PYTHONDONTWRITEBYTECODE="1")
            argv = config["command"]
            params = StdioServerParameters(command=argv[0], args=argv[1:], env=env, cwd=directory)
            with open(os.devnull, "w") as err:
                async with stdio_client(params, errlog=err) as streams:
                    yield streams[0], streams[1]
        return
    endpoint = httpx2.URL(config["url"])

    async def exact_endpoint(request):
        if request.url != endpoint:
            raise ValueError("MCP 请求地址已变化")

    async def original_response(response):
        if 300 <= response.status_code < 400 or any(
                entry.strip().lower() != "identity" for entry in response.headers.get_list("content-encoding")):
            raise ValueError("MCP 连接器返回重定向或不支持的响应编码")

    transport = MCPSourceTransport(httpx2.AsyncHTTPTransport(trust_env=False, retries=0), MAX_RESULT)
    async with httpx2.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
            headers={"Accept-Encoding": "identity"}, timeout=config["timeout"] / 1000,
            event_hooks={"request": [exact_endpoint], "response": [original_response]}) as http:
        async with streamable_http_client(config["url"], http_client=http, max_sse_event_size=MAX_RESULT) as streams:
            yield streams[0], streams[1]


async def _directory(client):
    listed = await client.list_tools()
    result, names = [], set()
    size = 0
    for page in range(8):
        for tool in listed.tools:
            entry = {"name": tool.name, "description": tool.description or "", "inputSchema": tool.input_schema}
            if tool.output_schema is not None:
                entry["outputSchema"] = tool.output_schema
            size += len(_encoded(entry, MAX_DIRECTORY))
            if (not tool.name or tool.name in names or len(tool.name) > 256 or any(ord(c) < 32 for c in tool.name)
                    or len(names) >= 256 or size > MAX_DIRECTORY):
                raise ValueError("MCP 工具目录无效或超过限制")
            result.append(entry)
            names.add(tool.name)
        if not listed.next_cursor:
            return result
        if page < 7:
            listed = await client.list_tools(params=PaginatedRequestParams(cursor=listed.next_cursor))
    raise ValueError("MCP 工具目录分页超过限制")


def _comparison(ctx, case_id, pointer, expected):
    if not any((case_id, pointer, expected)):
        return None
    cases = ctx.get("approved_by_kind", {}).get("cases", {}).get("items", [])
    if (not case_id or not any(c.get("id") == case_id for c in cases)
            or not isinstance(expected, str) or not expected.strip() or len(expected) > MAX_ARGUMENTS):
        raise ValueError("断言需要已审核的用例编号、JSON Pointer 和明确的预期 JSON")
    tokens = pointer_tokens(pointer)

    def unique(pairs):
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise ValueError("预期 JSON 含重复键")
        return obj

    try:
        value = json.loads(expected, object_pairs_hook=unique)
        _encoded(value, MAX_ARGUMENTS)  # excludes NaN, Infinity and excessive nesting
    except (ValueError, RecursionError) as exc:
        raise ValueError("断言预期必须是有效且有界的 JSON") from exc
    return tokens, value


def _lookup(root, tokens):
    current = root
    for name in tokens:
        if isinstance(current, dict) and name in current:
            current = current[name]
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]{0,8}", name) and int(name) < len(current):
            current = current[int(name)]
        else:
            return False, None
    return True, current


def _hardware_observation(plan, fields, has_value, dispatched_at, received_at):
    found_device, device = _lookup(fields, pointer_tokens(plan["device_id_pointer"]))
    found_time, raw_time = _lookup(fields, pointer_tokens(plan["sample_time_pointer"]))
    sampled = sample_time(raw_time)
    device_id = (device if isinstance(device,str) and len(device)<=160
                 and all(ord(char)>=32 and ord(char)!=127 for char in device) else None)
    readback = {"source":"connector_reported", "device_id":device_id,
                "sampled_at":sampled.isoformat() if sampled else None,
                "dispatched_at":dispatched_at, "received_at":received_at}
    issue = None
    if not (found_device and found_time and has_value):
        issue = "missing_readback"
    elif device_id != plan["device_id"]:
        issue = "wrong_device"
    elif sampled is None:
        issue = "invalid_sample_time"
    elif not readback_in_window(readback):
        issue = "outside_dispatch"
    return readback, issue


async def _exchange(ctx, server, tool=None, args=None, *, before_dispatch=None):
    config = _current(ctx, server)
    _reserve(ctx)
    timeout = config["timeout"] / 1000
    async def run():
        async with _streams(ctx, config) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timeout) as client:
                await client.initialize()
                directory = await _directory(client)
                if tool is None:
                    return {"server": server, "tools": directory}
                if not any(row["name"] == tool for row in directory):
                    raise ValueError("当前 MCP 没有声明此工具")
                _current(ctx, server)  # directory pagination can outlive a user revocation
                if before_dispatch is not None:
                    before_dispatch()
                observed = await client.call_tool(tool, args, read_timeout_seconds=timeout)
                received_at = datetime.now(timezone.utc).isoformat()
                if not isinstance(observed, CallToolResult):
                    raise ValueError("连接器返回了需续接的结果，无法当作已完成")
                payload = observed.model_dump(mode="json", by_alias=True, exclude_none=True)
                result = {"server": server, "tool": tool, "is_error": bool(observed.is_error), "received_at": received_at,
                          "structured_content": payload.get("structuredContent"), "content": payload.get("content", [])}
                _encoded(result, MAX_RESULT)
                return result
    try:
        result = await asyncio.wait_for(run(), timeout=timeout)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # SDK exception groups may carry output, credentials or transport details.
        # A failed/disconnected call may already have reached the original device.
        raise RuntimeError("连接器没有返回可确认的结果，请先检查原操作记录") from exc
    _current(ctx, server)
    return result


async def list_connector_tools(ctx, server: str) -> dict:
    return await _exchange(ctx, server)


async def call_connector(ctx, server: str, tool: str, arguments: dict, case_id: str = "",
                         check_pointer: str = "", expected_json: str = "") -> dict:
    if not isinstance(tool, str) or not tool or len(tool) > 256 or not isinstance(arguments, dict):
        raise ValueError("请选择明确的 MCP 工具并提供参数对象")
    arguments_sha256 = hashlib.sha256(_encoded(arguments, MAX_ARGUMENTS)).hexdigest()
    comparison = _comparison(ctx, case_id, check_pointer, expected_json)
    identity = {"server": server, "tool": tool, "arguments_sha256": arguments_sha256}
    details = dict(identity)
    kind = "connector_call"
    readback_plan = None
    if comparison is not None:
        tokens, expected = comparison
        action = resolve_case_action(ctx, case_id, method="mcp_response", server=server, tool=tool,
                                     arguments=arguments, pointer=check_pointer, expected_json=expected_json,
                                     approved_fields=("hardware_readback",))
        if "hardware_readback" in action:
            if not readback_descriptor_ok(action) or expected is None:
                raise ValueError("已审设备回读需要独立的设备编号、采样时间和非空读值")
            readback_plan = action["hardware_readback"]
        details.update(action_id=action["id"], action_sha256=action_digest(action))
        details.update(case_id=case_id, method="mcp_response", pointer=check_pointer, expected_sha256=_digest(expected))
        kind = "check"
    dispatch = None

    def record_dispatch():
        nonlocal dispatch
        from packages.dynamic_workflow.stage_tool_server import check_owner
        check_owner(ctx)
        with ledger(ctx["_data_dir"]) as db:
            db.execute("BEGIN IMMEDIATE")
            if any(record.get("status") == "unconfirmed" and all(record.get(k) == v for k, v in identity.items())
                   for record in _operation_records(db, ctx)):
                raise ValueError("本次执行已有同一项未确认的调用")
            dispatch = _record(db, ctx, kind, **details, status="unconfirmed",
                               config_ref=ctx["capability_refs"]["mcp"][server])

    result = await _exchange(ctx, server, tool, arguments, before_dispatch=record_dispatch)
    observed_fields = {"status": "connector_error" if result["is_error"] else "connector_replied"}
    if comparison is not None:
        found, observed = _lookup(result["structured_content"], tokens)
        actual = _digest(observed) if found else None
        readback_fields, issue = {}, None
        if readback_plan is not None:
            readback, issue = _hardware_observation(readback_plan, result["structured_content"],
                found and observed is not None, dispatch["recorded_at"], result["received_at"])
            if type(observed) in (bool, int, float, str):
                preview = _encoded(observed, MAX_RESULT).decode()
                readback["observed_preview"] = preview if len(preview)<=240 else preview[:239]+"…"
            readback_fields["hardware_readback"] = readback
            if issue:
                readback_fields["readback_issue"] = issue
        passed = found and actual == details["expected_sha256"] and not result["is_error"] and issue is None
        observed_fields = {"status": "passed" if passed else "failed", "observed_sha256": actual,
                           "exit_code": 0 if passed else 1, **readback_fields}
    with ledger(ctx["_data_dir"]) as db:
        result["receipt"] = _record(db, ctx, kind, **details, **observed_fields,
                                    config_ref=dispatch["config_ref"], connector_dispatch_id=dispatch["id"])
    return result


def as_tool_result(result):
    """Keep images, resource links and embedded resources as actual MCP blocks.

    Also give text-only MCP consumers the receipt/structured observations;
    serialise those only, never turn screenshot bytes into a text block.
    """
    metadata = {key: val for key, val in result.items() if key != "content"}
    content = list(result["content"])
    content.append({"type": "text", "text": _encoded(metadata, MAX_RESULT + 4096).decode()})
    return CallToolResult.model_validate({"isError": result["is_error"], "content": content,
                                          "structuredContent": metadata})
