"""Scoped desktop tasks through the unmodified original OpenCode loop.

Prepare records an immutable QUEUED request. Start is separate from message
interpretation/authorization. This module contains transport and tool admission,
not a model loop, language classifier or native qualification shortcut.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import importlib.metadata
import json
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
import time
import uuid

import httpx
from packages.adapters.model_transport import open_async_model_transport, private_model_redactions
from packages.adapters.model_relay_lifecycle import ModelRelayLifecycle
from packages.adapters.private_tool_bridge import PrivateToolBridge
from jsonschema import Draft202012Validator
from mcp.types import CallToolResult

from packages.adapters.opencode import OpenCodeBridge, TERMINAL, _constant, redact_native
from packages.adapters.opencode_planning import OpenCodePlanningTransport, _bounded_json
from packages.adapters.opencode_profile import COMMIT, VERSION
from packages.adapters.subscription_runtime import (is_subscription, native_provider_config,
    subscription_budget_summary, subscription_environment)
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest, now

AGENT = "eeas-desktop-task"
MCP_NAME = "eeas_desktop"
MCP_TOOLS = {"eeas_desktop_methods", "eeas_desktop_invoke"}
REF_KEYS = {"id", "revision", "sha256"}
SCOPE_KEYS = {"surface_ref", "input_epoch", "task_ref", "target_ref", "context_ref",
              "frame_ref", "authorization_ref", "user_message_ref"}


def reference(value):
    if (not isinstance(value, dict) or set(value) != REF_KEYS or
            not isinstance(value["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value["id"]) or
            type(value["revision"]) is not int or value["revision"] < 1 or
            not isinstance(value["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])):
        raise Conflict("An exact versioned source reference is required")
    return value


class _DesktopRelay:
    """Counted provider wire with this execution's exact model/tool whitelist."""
    def __init__(self, profile, budget, allowed):
        self.profile, self.budget, self.allowed = profile, budget, set(allowed)
        from packages.domain.task_resource_budget import current_task_resource_budget
        self.task_resource_budget=current_task_resource_budget()
        self.key = secrets.token_hex(24)
        self.deadline = time.monotonic() + budget["timeout_seconds"]
        self.lock = threading.Lock(); self.records = []; self.calls = 0; self.closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *_args): pass
            def reply(self, status, reason):
                raw = canonical({"error": {"message": reason, "code": reason}}).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.send_header("Connection", "close")
                self.end_headers(); self.wfile.write(raw); self.close_connection = True
            def do_GET(self): self.reply(403, "DESKTOP_PROVIDER_ROUTE_DENIED")
            def do_POST(self):
                if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer " + owner.key:
                    self.reply(403, "DESKTOP_PROVIDER_ROUTE_DENIED"); return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4 * 1024 * 1024: raise ValueError()
                    body = json.loads(self.rfile.read(size), parse_constant=_constant)
                    names = [item["function"]["name"] for item in body.get("tools", [])]
                    if (body.get("model") != profile.model_id or body.get("stream") is not True or
                            not names or len(names) != len(set(names)) or not set(names) <= owner.allowed or
                            type(body.get("max_tokens")) is not int or
                            not 1 <= body["max_tokens"] <= budget["max_output_tokens"]):
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    self.reply(400, "DESKTOP_PROVIDER_MODEL_OR_TOOLS_DENIED"); return
                with owner.lock:
                    if owner.closed or time.monotonic() >= owner.deadline:
                        record = {"upstream": False, "code": "DESKTOP_DEADLINE_EXHAUSTED", "status": 408}
                    elif owner.calls >= budget["max_calls"]:
                        record = {"upstream": False, "code": "DESKTOP_RPC_ALLOWANCE_EXHAUSTED", "status": 403}
                    else:
                        from packages.adapters.task_resources import reserve_model
                        try:reserve_model(owner.task_resource_budget,size)
                        except Exception:
                            self.reply(403,"SHARED_TASK_RESOURCE_ALLOWANCE_DENIED");return
                        owner.calls += 1
                        record = {"upstream": True, "call": owner.calls, "request_sha256": digest(body)}
                    owner.records.append(record)
                if not record["upstream"]: self.reply(record["status"], record["code"]); return
                record['upstream'] = False
                try:
                    owner._io.run(self.forward, body, record)
                except asyncio.CancelledError:
                    record.update(error_type='UPSTREAM_CANCELLED_LOCAL_CLOSE',local_cancel_requested=True,remote_outcome='UNKNOWN')
                    self.close_connection = True
                except Exception as error:
                    record['error_type'] = type(error).__name__
                    self.close_connection = True

            async def forward(self, body, record):
                with owner.lock:
                    if owner.closed:
                        record['upstream'] = False
                        raise ValueError('Relay sealed before forwarding')
                async with open_async_model_transport(profile,timeout_cap_seconds=owner.deadline-time.monotonic()) as client:
                    with owner.lock:
                        if owner.closed: raise ValueError('Relay sealed before forwarding')
                        record['upstream'] = True
                    async with client.stream("POST", profile.provider_url + "/chat/completions", json=body) as response:
                        record["status"] = response.status_code
                        if 300 <= response.status_code < 400:
                            self.reply(502, "DESKTOP_PROVIDER_REDIRECT_DENIED"); return
                        self.send_response(response.status_code)
                        self.send_header("Content-Type", response.headers.get("content-type", "application/octet-stream"))
                        self.send_header("Connection", "close"); self.end_headers(); self.close_connection = True
                        sha, count = hashlib.sha256(), 0
                        async for chunk in response.aiter_bytes():
                            count += len(chunk)
                            if count > 4 * 1024 * 1024 or time.monotonic() >= owner.deadline: break
                            sha.update(chunk); self.wfile.write(chunk); self.wfile.flush()
                        record["response_sha256"] = sha.hexdigest(); record["response_bytes"] = count
        self._io = ModelRelayLifecycle(Handler)
        self.server, self.thread = self._io.server, self._io.thread

    @property
    def base_url(self): return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
    @property
    def owned_threads(self): return self._io.owned_threads
    @property
    def owned_tasks(self): return self._io.owned_tasks
    @property
    def close_confirmed(self): return self._io.close_confirmed
    def start(self): self._io.start(); return self
    def close(self):
        self.seal()
        self._io.close()
    def seal(self):
        with self.lock:self.closed=True


class _ExecutionBroker(PrivateToolBridge):
    """Exact desktop execution routes, tracked requests and callback workers."""
    def __init__(self, owner, row, request, context):
        self.owner,self.row,self.request,self.context=owner,row,request,context
        async def callback(route,body):
            current=owner.inspect(row['project_id'],row['id'])
            if (owner.active.get(row['id']) is not context or context.get('closing')
                    or current['execution_status']!='RUNNING' or current['cancel_requested']
                    or current['request_hash']!=row['request_hash'] or digest(request)!=row['request_hash']
                    or digest(owner.method_descriptors(request))!=context['scope']['methods_sha256']):
                raise Conflict('Original desktop bridge owner is not active')
            if route=='/methods' and body=={}:return owner.method_descriptors(request)
            if route=='/invoke' and set(body)=={'method','arguments'}:
                return await owner._invoke(row,request,context,body)
            raise Conflict('Desktop bridge route or body denied')
        def headers(value):
            return (value.get('X-EEAS-Execution')==row['id']
                and value.get('X-EEAS-Request-SHA256')==row['request_hash'])
        super().__init__(callback,context['loop'],routes={'/methods','/invoke'},label='desktop',
            request_bytes=256*1024,timeout=request['budget']['timeout_seconds'],header_check=headers)
    @property
    def url(self):return self.base_url
    def start(self):return self


class OpenCodeDesktopTransport(OpenCodePlanningTransport):
    """Prepare → bind authority → start, all around the original native loop."""
    runtime = "opencode-desktop-http"

    def __init__(self, service, data_dir, *, provider_registry, message_reader,
                 context_reader=None, context_checker=None, authorization_reader=None,
                 source_checker=None, grant_checker=None, capability_reader=None,
                 queue_revoker=None, foreground_checker=None, task_context_checker=None, mcp_script=None):
        if not callable(message_reader): raise ValueError("Actual stored user messages are required")
        self.provider_registry, self.message_reader = provider_registry, message_reader
        self.context_reader, self.context_checker = context_reader, context_checker
        self.authorization_reader, self.source_checker = authorization_reader, source_checker
        self.grant_checker, self.capability_reader, self.queue_revoker = grant_checker, capability_reader, queue_revoker
        self.foreground_checker = foreground_checker
        self.task_context_checker = task_context_checker
        self.mcp_script = Path(mcp_script or Path(__file__).parents[1] / "desktop/mcp_server.py").resolve()
        if not self.mcp_script.is_file(): raise ValueError("The packaged original-SDK MCP bridge is missing")
        self.methods = {}; self.closed = False; self._owners = {}; self._owner_requests = {}; self._stopping = set()
        self._prepare_lock = threading.RLock()
        OpenCodeBridge.__init__(self, service, data_dir)
        self.register_method({"id": "read_message", "route": "LOCAL_RECORD", "readonly": True,
            "effects": [], "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "coverage": "EXACT_STORED_USER_MESSAGE", "qualification": "IMPLEMENTED",
            "cost": {"latency": "LOCAL_READ", "tokens": "BOUNDED_ORIGINAL_MESSAGE", "measured": False},
            "execution": {"can_background": True, "background_qualification": "QUALIFIED_LOCAL_READONLY",
                "requires_foreground_input": False, "focus_effect": "NONE"}},
            lambda arguments, binding: self.message_reader(binding["request"]["desktop_intent"]["user_message_ref"]))
        self.register_method({"id": "read_context", "route": "OBSERVATION", "readonly": True,
            "effects": [], "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "coverage": "EXACT_BOUND_CONTEXT_NOT_WHOLE_PAGE", "qualification": "IMPLEMENTED",
            "cost": {"latency": "LOCAL_READ", "tokens": "BOUNDED_CONTEXT", "measured": False},
            "execution": {"can_background": True, "background_qualification": "QUALIFIED_LOCAL_READONLY",
                "requires_foreground_input": False, "focus_effect": "NONE"}}, self._read_context)

    def register_method(self, descriptor, callback):
        required = {"id", "route", "readonly", "effects", "input_schema", "coverage", "qualification", "cost"}
        if (not isinstance(descriptor, dict) or set(descriptor) not in (required, required | {"execution"}) or
                not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(descriptor["id"])) or
                type(descriptor["readonly"]) is not bool or not callable(callback) or
                not isinstance(descriptor["effects"], list) or
                (descriptor["readonly"] and descriptor["effects"]) or
                (not descriptor["readonly"] and not descriptor["effects"])):
            raise ValueError("A typed host-owned method descriptor is required")
        _bounded_json(descriptor, 65536); Draft202012Validator.check_schema(descriptor["input_schema"])
        if descriptor["input_schema"].get("type") != "object": raise ValueError("Method input must be an object")
        if descriptor["id"] in self.methods: raise Conflict("A method version is already registered")
        descriptor = copy.deepcopy(descriptor)
        descriptor.setdefault("execution", {"can_background": False, "background_qualification": "UNQUALIFIED",
            "requires_foreground_input": True, "focus_effect": "UNKNOWN"})
        execution = descriptor["execution"]
        if (not isinstance(execution, dict) or set(execution) != {"can_background", "background_qualification", "requires_foreground_input", "focus_effect"} or
                type(execution["can_background"]) is not bool or type(execution["requires_foreground_input"]) is not bool or
                execution["focus_effect"] not in {"NONE", "POSSIBLE", "REQUIRED", "UNKNOWN"} or
                execution["background_qualification"] not in {"QUALIFIED", "QUALIFIED_LOCAL_READONLY", "UNQUALIFIED", "UNAVAILABLE"}):
            raise ValueError("Explicit background and foreground side-effect metadata is required")
        self.methods[descriptor["id"]] = (copy.deepcopy(descriptor), callback)

    def _read_context(self, arguments, binding):
        ref = binding["request"]["desktop_intent"]["context_ref"]
        if ref is None: return {"status": "UNAVAILABLE", "reason": "CURRENT_CONTEXT_NOT_BOUND", "input_authorized": False}
        return self.context_reader(ref)

    def _internal_project(self):
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT id FROM projects WHERE description=? ORDER BY created_at,id LIMIT 1",
                ("EEAS_INTERNAL_DESKTOP_RUNTIME_V1",)).fetchone()
            if row: return row["id"]
            pid = str(uuid.uuid4())
            connection.execute("INSERT INTO projects VALUES (?,?,?,?)",
                (pid, "本机工作上下文", "EEAS_INTERNAL_DESKTOP_RUNTIME_V1", now()))
            return pid

    def _validate(self, request):
        keys = {"project_id", "operation_id", "prompt", "mode", "desktop_intent", "provider_profile_ref",
                "budget", "output_schema", "method_ids"}
        if not isinstance(request, dict) or set(request) != keys | ({"desktop_scope", "desktop_intent_scope"} if request.get("mode") == "ACTION" else set()):
            raise ValueError("Desktop request fields or scope are invalid")
        if request["mode"] not in {"INTENT", "OBSERVE", "ACTION"}: raise ValueError("Unknown desktop mode")
        if not isinstance(request["prompt"], str) or not 1 <= len(request["prompt"]) <= 16000:
            raise ValueError("The exact bounded user message is required")
        if not isinstance(request["operation_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request["operation_id"]):
            raise ValueError("An operation identity is required")
        intent = request["desktop_intent"]
        if not isinstance(intent, dict) or set(intent) != {"user_message_ref", "context_ref"}: raise ValueError("Actual message/context refs are required")
        reference(intent["user_message_ref"])
        if intent["context_ref"] is not None: reference(intent["context_ref"])
        budget = request["budget"]
        if not isinstance(budget, dict) or set(budget) != {"max_calls", "timeout_seconds", "max_output_tokens", "max_tool_calls"}:
            raise ValueError("Explicit desktop budgets are required")
        for key, limit in {"max_calls": 32, "timeout_seconds": 600, "max_output_tokens": 32768, "max_tool_calls": 100}.items():
            if type(budget[key]) is not int or not 1 <= budget[key] <= limit: raise ValueError("Invalid fixed desktop budget")
        ids = request["method_ids"]
        if (not isinstance(ids, list) or len(ids) > 32 or any(not isinstance(x, str) for x in ids) or
                len(set(ids)) != len(ids) or any(x not in self.methods for x in ids)):
            raise ValueError("Unknown or duplicate method scope")
        if request["mode"] != "ACTION" and any(not self.methods[x][0]["readonly"] for x in ids):
            raise Conflict("Readonly desktop tasks cannot enable mutation methods")
        schema = request["output_schema"]; Draft202012Validator.check_schema(schema)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("A bounded object output schema is required")
        def refs(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (not isinstance(item, str) or not item.startswith("#/")):
                        raise ValueError("Output schema references must remain local")
                    refs(item)
            elif isinstance(value, list):
                for item in value: refs(item)
        refs(schema)
        _bounded_json(request, 512 * 1024)

    def _source(self, request):
        message = self.message_reader(request["desktop_intent"]["user_message_ref"])
        if (not isinstance(message, dict) or message.get("raw_text") != request["prompt"] or
                message.get("raw_text_sha256") != hashlib.sha256(request["prompt"].encode()).hexdigest() or
                message.get("context_ref") != request["desktop_intent"]["context_ref"]):
            raise Conflict("Exact original user message or context changed")
        ref = request["desktop_intent"]["context_ref"]
        if ref is not None:
            checker = self.task_context_checker if request["mode"] == "ACTION" and callable(self.task_context_checker) else self.context_checker
            if not callable(self.context_reader) or not callable(checker):
                raise Conflict("Actual current context authority is unavailable")
            context = self.context_reader(ref)
            if not isinstance(context, dict) or context.get("context_ref") != ref or checker(context) is not True:
                raise Conflict("Current context is stale or unverified")
        if request["mode"] == "ACTION":
            extended = request["desktop_intent_scope"]
            if (not isinstance(extended, dict) or set(extended) != SCOPE_KEYS or
                    extended["user_message_ref"] != request["desktop_intent"]["user_message_ref"] or
                    extended["context_ref"] != ref or not callable(self.authorization_reader)):
                raise Conflict("Action source envelope is incomplete")
            if self.authorization_reader(extended["authorization_ref"]) != {
                    "desktop_scope": request["desktop_scope"], "desktop_intent_scope": extended}:
                raise Conflict("Action envelope does not match actual message authorization")
        return message

    def _scope(self, project_id, request, *, verify_executable=True):
        self._validate(request); self.service.project(project_id); self._source(request)
        resolver=self.provider_registry.resolve if verify_executable else getattr(self.provider_registry,"resolve_metadata",self.provider_registry.resolve)
        profile = resolver(request["provider_profile_ref"])
        return {"runtime": self.runtime, "cli_version": VERSION, "upstream_commit": COMMIT,
            "profile_fingerprint": profile.fingerprint, "message_ref": request["desktop_intent"]["user_message_ref"],
            **({'authorization_epoch':profile.authorization_epoch} if is_subscription(profile) else {}),
            "context_ref": request["desktop_intent"]["context_ref"], "mode": request["mode"],
            "methods_sha256": digest(self.method_descriptors(request)), "budget": request["budget"],
            "input_hash": digest(request), "strategy": "ADAPTIVE_NO_FIXED_ROUTE_PRIORITY",
            "transport_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "mcp_bridge_sha256": hashlib.sha256(self.mcp_script.read_bytes()).hexdigest(),
            "mcp_sdk_version": importlib.metadata.version("mcp")}

    def _frozen_scope(self, row):
        with self.service.connect() as connection:
            found = connection.execute("SELECT scope,scope_hash FROM agent_scopes WHERE execution_id=?", (row["id"],)).fetchone()
        if not found: raise Conflict("Original execution scope is missing")
        value = json.loads(found["scope"])
        if digest(value) != found["scope_hash"]: raise Conflict("Original execution scope integrity failed")
        return value

    def method_descriptors(self, request):
        return {"strategy": "ADAPTIVE", "work_preference": "QUALIFIED_BACKGROUND_NO_AUTOMATIC_FOCUS",
            "methods": [copy.deepcopy(self.methods[x][0]) for x in request["method_ids"]],
            "scope": request["mode"], "native_input_qualification": "NOT_INFERRED_FROM_REGISTRATION"}

    def prepare(self, request):
        if self.closed: raise Conflict("Desktop transport is closed")
        request = copy.deepcopy(request)
        request.setdefault("project_id", None)
        if request["project_id"] is None: request["project_id"] = self._internal_project()
        with self._prepare_lock:
            scope = self._scope(request["project_id"], request)
            runtime = self.data_dir / "desktop-runtime"; runtime.mkdir(parents=True, exist_ok=True)
            workspace = runtime / uuid.uuid4().hex; workspace.mkdir(mode=0o700)
            try: row, created = OpenCodeBridge._record(self, request["project_id"], request["operation_id"], request, scope, workspace)
            except Exception: workspace.rmdir(); raise
            if not created: workspace.rmdir()
            else: self._event(row["id"], {"type": "desktop_prepared", "mode": request["mode"],
                "request_sha256": row["request_hash"], "native_started": False, "input_authorized": False})
            return row

    def _action_admission(self, row, request, grant_ref):
        receipt = self.capability_reader() if callable(self.capability_reader) else {}
        if (not isinstance(receipt, dict) or receipt.get("desktop_input_routes_qualified") is not True or
                receipt.get("owned_queue_drain_qualified") is not True or
                not callable(self.grant_checker) or not callable(self.source_checker) or not callable(self.queue_revoker)):
            raise Conflict("Actual native desktop routes are not qualified")
        extended = request["desktop_intent_scope"]
        if self.source_checker(row, extended) is not True or self.grant_checker(row, extended, grant_ref) is not True:
            raise Conflict("Exact action grant or desktop sources changed")

    async def start(self, project_id, execution_id, *, grant_ref=None):
        if self.closed: raise Conflict("Desktop transport is closed")
        row = self.inspect(project_id, execution_id); request = json.loads(row["request"])
        if digest(request) != row["request_hash"]: raise Conflict("Frozen desktop request changed")
        if row["execution_status"] in TERMINAL: return row
        scope = self._scope(project_id, request)
        if scope != self._frozen_scope(row): raise Conflict("Prepared source/profile/toolchain scope changed")
        if request["mode"] == "ACTION": self._action_admission(row, request, grant_ref)
        key = (project_id, row["operation_id"])
        if key in self._owners: return await asyncio.shield(self._owners[key])
        if row["execution_status"] != "QUEUED": raise Conflict("No authoritative queued desktop owner")
        owner = asyncio.create_task(self._run(project_id, row, request, scope, grant_ref))
        self._owners[key] = owner; self._owner_requests[key] = row["request_hash"]
        try: return await asyncio.shield(owner)
        except asyncio.CancelledError:
            # As in planning.submit, cancellation of the observer must not
            # cancel the owned cleanup a second time or discard its handles.
            # Explicit user abort still goes through request_cancel.
            self._cancel_owner(owner)
            await self._wait_owner(owner)
            raise
        finally:
            self._owners.pop(key, None); self._owner_requests.pop(key, None); self._stopping.discard(owner)

    async def submit(self, project_id, operation_id, request):
        selected = dict(request, project_id=project_id, operation_id=operation_id)
        row = self.prepare(selected)
        if selected["mode"] == "ACTION": return row
        return await self.start(row["project_id"], row["id"])

    from packages.adapters.task_resources import callback_scope as _callback_resource_scope
    @_callback_resource_scope
    async def _invoke(self, row, request, context, body):
        call_id = uuid.uuid4().hex
        try:
            current = self.inspect(row["project_id"], row["id"])
            if context.get("closing") or current["cancel_requested"] or current["execution_status"] != "RUNNING":
                raise Conflict("This original execution is no longer dispatchable")
            if digest(json.loads(current["request"])) != row["request_hash"]: raise Conflict("Execution request changed")
            if digest(self.method_descriptors(request)) != context["scope"]["methods_sha256"]:
                raise Conflict("Execution method descriptors changed")
            if context["tool_attempts"] >= request["budget"]["max_tool_calls"]:
                raise Conflict("Tool budget exhausted")
            context["tool_attempts"] += 1
            self._source(request)
            method = body["method"]
            if method not in request["method_ids"] or not isinstance(body["arguments"], dict):
                raise Conflict("Unknown method or scope")
            descriptor, callback = self.methods[method]
            Draft202012Validator(descriptor["input_schema"]).validate(body["arguments"])
            if not descriptor["readonly"]:
                if descriptor["qualification"] != "QUALIFIED":
                    raise Conflict("This mutation method has not been qualified")
                self._action_admission(current, request, context.get("grant_ref"))
            execution = descriptor["execution"]
            requires_handoff = (execution["requires_foreground_input"] or execution["focus_effect"] != "NONE" or
                not execution["can_background"] or execution["background_qualification"] not in {"QUALIFIED", "QUALIFIED_LOCAL_READONLY"})
            if requires_handoff and (not callable(self.foreground_checker) or
                    self.foreground_checker(current, descriptor, context.get("grant_ref")) is not True):
                self._event(row["id"], {"type": "desktop_tool_waiting_foreground", "call_id": call_id,
                    "method": method, "background_methods_remain_available": True})
                raise Conflict("NEEDS_FOREGROUND_HANDOFF: this route is not qualified for quiet background work")
            context["tool_calls"] += 1
            self._event(row["id"], {"type": "desktop_tool_started", "call_id": call_id, "method": method,
                "arguments_sha256": digest(body["arguments"]), "readonly": descriptor["readonly"]})
            binding = {"request": copy.deepcopy(request),
                "execution_ref": {"project_id": row["project_id"], "execution_id": row["id"],
                    "request_sha256": row["request_hash"]}, "grant_ref": copy.deepcopy(context.get("grant_ref")),
                "opencode_session_id": context.get("session_id")}
            if inspect.iscoroutinefunction(callback):
                value = await callback(copy.deepcopy(body["arguments"]), binding)
            else:
                value = await context["broker"].run_sync(callback, copy.deepcopy(body["arguments"]), binding)
            if isinstance(value, CallToolResult): value = value.model_dump(exclude_none=True)
            _bounded_json(value, 3 * 1024 * 1024)
            receipt = {"type": "desktop_tool_completed", "call_id": call_id, "method": method,
                "result_sha256": digest(value), "readonly": descriptor["readonly"], "execution_id": row["id"],
                "request_sha256": row["request_hash"], "verification": "TOOL_RESULT_NOT_TASK_ACCEPTANCE"}
            self._event(row["id"], receipt)
            if isinstance(value, dict) and "content" in value:
                result = CallToolResult.model_validate(value).model_dump(exclude_none=True)
                if any(item["type"] == "image" for item in result["content"]):
                    if not self.provider_registry.resolve(request["provider_profile_ref"]).supports_images:
                        raise Conflict("The selected model does not declare image input support")
                result["content"].append({"type": "text", "text": canonical({"receipt": receipt})})
                return result
            return {"content": [{"type": "text", "text": canonical({"result": value, "receipt": receipt})}]}
        except Exception as error:
            self._event(row["id"], {"type": "desktop_tool_rejected", "call_id": call_id,
                "error_type": type(error).__name__, "reason": str(error)[:300]})
            return {"isError": True, "content": [{"type": "text", "text": str(error)[:300]}]}

    async def _run(self, project_id, row, request, scope, grant_ref):
        context = {"workspace": Path(row["workspace"]), "loop": asyncio.get_running_loop(),
            "connected": asyncio.Event(), "done": asyncio.Event(), "seen": {}, "busy": False,
            "abort_acknowledged": False, "idle_observed": False, "event_queue": asyncio.Queue(maxsize=32),
            "tool_calls": 0, "tool_attempts": 0, "grant_ref": grant_ref, "scope": scope}
        self.active[row["id"]] = context
        try:
            await asyncio.wait_for(self._drive(project_id, row, request, scope, context), request["budget"]["timeout_seconds"])
        except asyncio.CancelledError:
            await self._flush_events(context); OpenCodeBridge._terminal(self, row["id"], "UNKNOWN_OUTCOME")
            raise
        except Exception as error:
            await self._flush_events(context)
            self._event(row["id"], {"type": "desktop_transport_error", "error_type": type(error).__name__,
                "reason": str(error)[:300], "outcome": "UNKNOWN_OUTCOME"})
            OpenCodeBridge._terminal(self, row["id"], "UNKNOWN_OUTCOME")
        finally:
            cleanup=asyncio.create_task(self._dispose_context(project_id,row,context))
            context['cleanup_task']=cleanup
            await self._wait_owner(cleanup)
        return self.inspect(project_id, row["id"])

    async def _dispose_context(self,project_id,row,context):
        """Attempt all captured resources; retain this context if any stay unknown."""
        loop=context['loop']
        if loop is not asyncio.get_running_loop():
            if not loop.is_running():raise Conflict('Original desktop cleanup loop is unavailable')
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
                self._dispose_context(project_id,row,context),loop))
        context['closing']=True;errors=[]
        try:await self._flush_events(context)
        except Exception as error:errors.append(error)
        tasks=[context[name] for name in ('listener','writer') if context.get(name)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        meter=context.get('subscription_meter')
        if meter is not None:
            try:await meter.close()
            except Exception as error:errors.append(error)
        resources={id(value):value for value in (context.get('broker'),context.get('relay')) if value is not None}
        if resources:
            results=await asyncio.gather(*(asyncio.to_thread(value.close) for value in resources.values()),return_exceptions=True)
            errors.extend(value for value in results if isinstance(value,BaseException))
        if context.get('client'):
            try:await context['client'].aclose()
            except Exception as error:errors.append(error)
        process=context.get('process')
        if process and process.returncode is None:
            try:
                try:process.terminate()
                except ProcessLookupError:pass
                try:await asyncio.wait_for(process.wait(),3)
                except asyncio.TimeoutError:
                    try:process.kill()
                    except ProcessLookupError:pass
                    await asyncio.wait_for(process.wait(),3)
            except Exception as error:errors.append(error)
        for output in context.get('logs',[]):
            try:output.close()
            except Exception as error:errors.append(error)
        broker=context.get('broker')
        received=context.get('received_writer')
        if received is not None and not received.close_confirmed:
            errors.append(Conflict('Received native events remain unsaved or writer is active'))
        if context.get('event_recovery_unowned') or context.get('event_recovery_metadata_unconfirmed'):
            errors.append(Conflict('Original received event recovery metadata remains unconfirmed'))
        if broker is not None and not broker.close_confirmed:
            errors.append(Conflict('Desktop tool bridge resources remain unconfirmed'))
        if meter is not None and not meter.close_confirmed:
            errors.append(Conflict('Desktop subscription meter resources remain unconfirmed'))
        relay=context.get('relay')
        if relay is not None and not relay.close_confirmed:
            errors.append(Conflict('Desktop API relay resources remain unconfirmed'))
        if process and process.returncode is None:
            errors.append(Conflict('Desktop owned process remains alive'))
        if errors:
            context['cleanup_error_types']=[type(error).__name__ for error in errors]
            self._event(row['id'],{'type':'desktop_owned_cleanup_unconfirmed',
                'error_types':context['cleanup_error_types'],'original_handles_retained':True,
                'automatic_post_replay':False,'business_result_preserved':True})
            raise errors[0]
        self._event(row['id'],{'type':'desktop_owned_process_stopped',
            'returncode':process.returncode if process else None,'external_actions_rolled_back':False,
            'bridge_close_confirmed':broker.close_confirmed if broker else None,
            'subscription_meter_close_confirmed':meter.close_confirmed if meter else None,
            'subscription_meter_cleanup_error':context.get('subscription_meter_cleanup_error')})
        if self.active.get(row['id']) is context:self.active.pop(row['id'])


    def _config(self, profile, request, relay, broker):
        allowed = {"*": "deny", "StructuredOutput": "allow"}
        if request["method_ids"]: allowed.update({name: "allow" for name in MCP_TOOLS})
        mcp = {} if not request["method_ids"] else {MCP_NAME: {"type": "local",
            "command": [sys.executable, "-B", str(self.mcp_script)], "enabled": True, "timeout": 120000,
            "environment": {"EEAS_DESKTOP_BRIDGE_URL": broker.url, "EEAS_DESKTOP_BRIDGE_TOKEN": broker.key,
                "EEAS_DESKTOP_EXECUTION_ID": broker.row["id"], "EEAS_DESKTOP_REQUEST_SHA256": broker.row["request_hash"]}}}
        return {"autoupdate": False, "share": "disabled", "snapshot": False, "formatter": False, "lsp": False,
            "plugin": [], "permission": allowed, "mcp": mcp, "enabled_providers": [profile.provider_id],
            "model": profile.provider_id + "/" + profile.model_id,
            **(native_provider_config(profile, request["budget"]["max_output_tokens"]) if is_subscription(profile) else {
            "provider": {profile.provider_id: {"npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": relay.base_url, "apiKey": relay.key, "timeout": request["budget"]["timeout_seconds"] * 1000},
                "models": {profile.model_id: {"tool_call": True, "reasoning": False, "attachment": profile.supports_images,
                    "limit": {"context": 131072, "output": request["budget"]["max_output_tokens"]},
                    "modalities": {"input": ["text", "image"] if profile.supports_images else ["text"], "output": ["text"]}}}}}}),
            "agent": {AGENT: {"mode": "primary", "steps": request["budget"]["max_calls"],
                "permission": allowed, "model": profile.provider_id + "/" + profile.model_id}}}

    from packages.adapters.task_resources import execution_scope as _execution_resource_scope
    @_execution_resource_scope
    async def _drive(self, project_id, row, request, scope, context):
        profile = self.provider_registry.resolve(request["provider_profile_ref"])
        if self._scope(project_id, request) != scope or self._frozen_scope(row) != scope:
            raise Conflict("Desktop source/profile changed")
        workspace = context["workspace"]; home = workspace / "home"; home.mkdir(); (home / "tmp").mkdir()
        cwd = workspace / "cwd"; cwd.mkdir()
        allowed = {"StructuredOutput"} | (MCP_TOOLS if request["method_ids"] else set())
        native_subscription = is_subscription(profile)
        relay = None if native_subscription else _DesktopRelay(profile, request["budget"], allowed).start()
        context["relay"] = relay
        broker = _ExecutionBroker(self, row, request, context).start(); context["broker"] = broker
        config_value = self._config(profile, request, relay, broker)
        config = home / "config" / "opencode" / "opencode.json"; config.parent.mkdir(parents=True)
        password = secrets.token_hex(24)
        context["secrets"] = [*private_model_redactions(profile), *([relay.key] if relay else []), broker.key, password]
        if native_subscription:
            from packages.adapters.subscription_execution_meter import SubscriptionExecutionMeter
            meter=SubscriptionExecutionMeter(self,row,request,scope,context,profile,cwd)
            config_value['plugin']=[meter.entry]
        config.write_text(canonical(config_value)); config.chmod(0o600)
        environment = self._environment(home, config, password)
        if native_subscription:
            environment = subscription_environment(environment, profile)
            environment = context["subscription_meter"].environment(environment)
        with socket.socket() as probe: probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        context["logs"] = [open(workspace / name, "wb") for name in ("server-stdout.bin", "server-stderr.bin")]
        with self.service.connect() as connection:
            connection.execute("UPDATE agent_executions SET execution_status='RUNNING' WHERE id=?", (row["id"],))
        from packages.adapters.opencode_startup_guard import reject_unreviewed_project_origins
        reject_unreviewed_project_origins(cwd)
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(str(profile.executable), *([] if context.get("subscription_meter") else ["--pure"]), "serve",
            "--hostname", "127.0.0.1", "--port", str(port), cwd=cwd,
            env=environment, stdout=context["logs"][0], stderr=context["logs"][1]))
        try: context["process"] = await asyncio.shield(spawn)
        except asyncio.CancelledError: context["process"] = await asyncio.shield(spawn); raise
        context["client"] = httpx.AsyncClient(base_url="http://127.0.0.1:" + str(port),
            auth=("eeas", password), params={"directory": str(cwd)}, trust_env=False, follow_redirects=False, timeout=5)
        while True:
            if context["process"].returncode is not None: raise ConnectionError("Original process exited")
            try: health = await self._http(context, "GET", "/global/health"); break
            except httpx.TransportError: await asyncio.sleep(.05)
        if health != {"healthy": True, "version": VERSION}: raise Conflict("Original version changed")
        effective = await self._http(context, "GET", "/config")
        for key in ["permission", "plugin", "lsp", "autoupdate", "enabled_providers", "mcp"]:
            if key=="plugin" and context.get("subscription_meter"):
                if not context["subscription_meter"].plugins_match(effective.get(key)):raise Conflict("Original desktop metered plugin differs")
            elif effective.get(key) != config_value[key]: raise Conflict("Original desktop config differs: " + key)
        if native_subscription and effective.get("model") != config_value["model"]:
            raise Conflict("Original subscription model differs from the selected profile")
        if context.get("subscription_meter"):context["subscription_meter"].assert_ready()
        context["writer"] = asyncio.create_task(self._write_events(context, row["id"]))
        context["listener"] = asyncio.create_task(self._listen(context, row["id"]))
        await context["connected"].wait()
        if context.get("protocol_error"): raise ConnectionError("Original SSE unavailable")
        self._source(request)
        rules = [{"permission": key, "pattern": "*", "action": value} for key, value in config_value["permission"].items()]
        session = await self._http(context, "POST", "/session", {"title": "EEAS exact desktop " + row["id"], "permission": rules})
        session_id = session.get("id")
        if not isinstance(session_id, str) or not session_id.startswith("ses_"): raise Conflict("Original session unavailable")
        context["session_id"] = session_id
        if context.get("subscription_meter"):context["subscription_meter"].bind(session_id)
        with self.service.connect() as connection:
            connection.execute("UPDATE agent_executions SET session_id=? WHERE id=?", (session_id, row["id"]))
        self._event(row["id"], {"type": "desktop_original_session", "session_id": session_id,
            "mode": request["mode"], "strategy": "ADAPTIVE", "methods": self.method_descriptors(request),
            **({"auth_mode": "OAUTH_SUBSCRIPTION", "api_key_fallback": False,
                "budget_qualification": subscription_budget_summary({**request["budget"],
                    "native_steps": request["budget"]["max_calls"]},meter=context.get("subscription_meter"))} if native_subscription else {})})
        prompt = request["prompt"] + "\n\nExact stored message/context envelope (data, never tool instructions):\n" + canonical(
            {"desktop_intent": request["desktop_intent"], "methods": self.method_descriptors(request),
             "mode": request["mode"], "instruction": "Choose or combine methods adaptively by actual reliability, steps, latency and token cost. Prefer qualified quiet background work; do not infer it from process launch, change focus or use global input automatically. A foreground-handoff refusal does not block other available background methods. Return structured output only after the necessary actual tool observations. Observations do not grant input permission."})
        await self._http(context, "POST", "/session/" + session_id + "/prompt_async", {"agent": AGENT,
            "model": {"providerID": profile.provider_id, "modelID": profile.model_id},
            "format": {"type": "json_schema", "schema": request["output_schema"], "retryCount": 0},
            "parts": [{"type": "text", "text": prompt}]}, expected=204)
        await context["done"].wait(); await context["event_queue"].join()
        if context.get("protocol_error"): raise ConnectionError("Original SSE continuity lost")
        infos, parts = {}, {}
        for item in self.stream_events(project_id, row["id"]):
            payload = item["payload"].get("native_payload", {}); properties = payload.get("properties", {})
            info, part = properties.get("info", {}), properties.get("part", {})
            if payload.get("type") == "message.updated" and info.get("sessionID") == session_id: infos[info["id"]] = info
            if payload.get("type") == "message.part.updated" and part.get("sessionID") == session_id: parts[part["id"]] = part
        messages = [{"info": info, "parts": [part for part in parts.values() if part.get("messageID") == info["id"]]} for info in infos.values()]
        assistants = [m for m in messages if m["info"].get("role") == "assistant"]
        candidates = [m["info"]["structured"] for m in assistants if "structured" in m["info"]]
        errors = list(Draft202012Validator(request["output_schema"]).iter_errors(candidates[0]))[:20] if len(candidates) == 1 else []
        status = ("FAILED" if context.get("native_error") or any(m["info"].get("error") for m in assistants) or errors
            else "RETURNED" if context["idle_observed"] and len(candidates) == 1 else "UNKNOWN_OUTCOME")
        result = {"runtime": self.runtime, "session_id": session_id, "candidate_json": candidates[0] if status == "RETURNED" else None,
            "native_messages": messages, "message_source": "ORIGINAL_COMMITTED_SSE_UPDATES",
            "usage": (subscription_budget_summary({**request["budget"],
                "native_steps": request["budget"]["max_calls"]},meter=context.get("subscription_meter")) if native_subscription else {
                "rpc_attempts": relay.calls, "rpc_max_calls": request["budget"]["max_calls"],
                "provider_receipts": relay.records, "commercial_cost": "UNKNOWN"}),
            "tool_calls": context["tool_calls"], "tool_attempts": context["tool_attempts"],
            "verification_status": "NOT_RUN", "task_completed": False,
            "native_input_qualification": "NOT_PROMOTED_BY_TRANSPORT"}
        public = redact_native(result, context["secrets"])
        from packages.adapters.task_resources import retain
        retain(context.get("task_resource_budget"),len(canonical(public).encode()))
        raw = canonical(public).encode(); artifact = workspace / "desktop-native-result.json"; artifact.write_bytes(raw); artifact.chmod(0o600)
        public["raw_artifact_ref"] = {"execution_id": row["id"], "filename": artifact.name,
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        await self._flush_events(context); OpenCodeBridge._terminal(self, row["id"], status, public)
        self._observe_subscription_response(project_id,row,request,scope,context,profile,messages,prompt)

    async def request_cancel(self, project_id, execution_id):
        row = self.inspect(project_id, execution_id)
        if row["execution_status"] in TERMINAL: return row
        request = json.loads(row["request"]); context = self.active.get(execution_id)
        if context and context["loop"] is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
                self.request_cancel(project_id, execution_id), context["loop"]))
        if row["execution_status"] == "QUEUED" and (project_id, row["operation_id"]) not in self._owners:
            with self.service.connect() as connection:
                connection.execute("UPDATE agent_executions SET cancel_requested=1 WHERE id=?", (execution_id,))
            OpenCodeBridge._terminal(self, execution_id, "CANCELLED")
            self._event(execution_id, {"type": "desktop_queued_cancelled", "native_started": False})
            return self.inspect(project_id, execution_id)
        queue_confirmed = request["mode"] != "ACTION"
        if context: context["closing"] = True
        if not queue_confirmed and callable(self.queue_revoker):
            try:
                if inspect.iscoroutinefunction(self.queue_revoker): value = await self.queue_revoker(row)
                else: value = await asyncio.to_thread(self.queue_revoker, row)
                queue_confirmed = value is True or (isinstance(value, dict) and value.get("confirmed") is True)
            except Exception as error:
                self._event(execution_id, {"type": "desktop_queue_revocation_unconfirmed", "error_type": type(error).__name__})
        result = await OpenCodePlanningTransport.request_cancel(self, project_id, execution_id)
        if not queue_confirmed:
            OpenCodeBridge._terminal(self, execution_id, "UNKNOWN_OUTCOME")
            result = self.inspect(project_id, execution_id)
        self._event(execution_id, {"type": "desktop_cancel_receipt", "queue_revoked_confirmed": queue_confirmed,
            "outcome": result["execution_status"], "external_actions_rolled_back": False})
        return result

    def describe_capabilities(self):
        return {"adapter": self.runtime, "version": VERSION, "source_commit": COMMIT,
            "native_loop": "ORIGINAL_OPENCODE", "strategy": "ADAPTIVE",
            "prepare_start_separated": True, "zero_material_entry": True,
            "native_input": "UNAVAILABLE_UNTIL_ACTUAL_HOST_QUALIFICATION",
            "os_network_sandbox": "NOT_QUALIFIED", "commercial_cost": "UNKNOWN"}

    async def close(self):
        """Revoke this controller's actual owners; never adopt a stored process."""
        self.closed = True
        owners = list(self._owners.items())
        close_errors=[]
        owned_keys={key for key,_owner in owners}
        detached=[]
        for ident,context in list(self.active.items()):
            context['closing']=True
            with self.service.connect() as connection:
                row=connection.execute('SELECT * FROM agent_executions WHERE id=?',(ident,)).fetchone()
            if row is None:
                close_errors.append(Conflict('Original desktop cleanup record is unavailable'));continue
            if (row['project_id'],row['operation_id']) not in owned_keys:detached.append((dict(row),context))
        cancel=[]
        for key, _owner in owners:
            with self.service.connect() as connection:
                row = connection.execute("SELECT id FROM agent_executions WHERE project_id=? AND operation_id=?", key).fetchone()
            if row:cancel.append(self.request_cancel(key[0],row['id']))
        if cancel:
            results=await asyncio.gather(*cancel,return_exceptions=True)
            close_errors.extend(value for value in results if isinstance(value,BaseException))
        results=await asyncio.gather(*(self._wait_owner(owner) for _,owner in owners),return_exceptions=True)
        close_errors.extend(value for value in results if isinstance(value,BaseException))
        for row,context in detached:
            if self.active.get(row['id']) is not context:continue
            cleanup=context.get('cleanup_task')
            if cleanup is None or cleanup.done():
                cleanup=asyncio.create_task(self._dispose_context(row['project_id'],row,context))
                context['cleanup_task']=cleanup
            try:await self._wait_owner(cleanup)
            except BaseException as error:close_errors.append(error)
        if close_errors:raise close_errors[0]
