"""Readonly OpenCode HTTP pilot. The original CLI owns its loop; EEAS owns scope and evidence."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import time
import uuid

import httpx

from packages.adapters.claude_sdk import ClaudeBridge, LiveBlocked
from packages.adapters.opencode_profile import VERSION, COMMIT, ProfileError, resolve_profile
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now, source_hashes

PROVIDER = "eeas-opencode-mock-wire"
AGENT = "eeas-readonly-review"
TERMINAL = {"RETURNED", "FAILED", "CANCELLED", "UNKNOWN_OUTCOME"}


def _constant(_value):
    raise ValueError("Native JSON contains a non-finite numeric value")


def decode_event(raw):
    if len(raw) > 1024 * 1024:
        raise ValueError("Native event exceeds the bounded protocol size")
    value = json.loads(raw, parse_constant=_constant)
    if not isinstance(value, dict) or not isinstance(value.get("type"), str) or not isinstance(value.get("properties", {}), dict):
        raise ValueError("Native event has no valid type/properties")
    return value


def redact_native(value, secrets_to_remove):
    if isinstance(value, str):
        for secret in secrets_to_remove:
            if secret: value = value.replace(secret, "[REDACTED_RUNTIME_CREDENTIAL]")
        return value
    if isinstance(value, dict): return {key: redact_native(child, secrets_to_remove) for key, child in value.items()}
    if isinstance(value, list): return [redact_native(child, secrets_to_remove) for child in value]
    return value


class OpenCodeBridge:
    runtime = "opencode-http"
    sdk_version = VERSION  # Compatibility column: for this runtime it identifies the original CLI.

    def __init__(self, service, data_dir):
        self.service = service
        self.data_dir = Path(data_dir)
        self.active = {}
        # No native SSE replay or persisted owner connection is promised after a controller restart.
        with service.connect() as connection:
            lost = connection.execute("SELECT id FROM agent_executions WHERE runtime=? AND execution_status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')", (self.runtime,)).fetchall()
            for row in lost:
                connection.execute("UPDATE agent_executions SET execution_status='UNKNOWN_OUTCOME',ended_at=? WHERE id=?", (now(), row["id"]))
        for row in lost:
            self._event(row["id"], {"type": "opencode_owner_lost", "outcome": "UNKNOWN_OUTCOME", "replayed": False})

    @staticmethod
    def native_parameters_schema():
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @staticmethod
    def _validate_request(request):
        if not isinstance(request, dict): raise ValueError("Native request must be an object")
        if request.get("native", {}) != {}:
            raise ValueError("OpenCode native overrides are outside this readonly pilot policy")
        if request.get("image_refs"):
            raise ValueError("OpenCode image input has not been qualified")
        ClaudeBridge._validate_request(request)
        if not request.get("task_binding"):
            raise ValueError("OpenCode review requires an exact frozen TaskRun binding")
        if request.get("output_format"):
            raise ValueError("OpenCode structured planning is not implemented in this review pilot")

    def preflight(self, needs_tools, *, search_enabled=False):
        reasons = []
        try:
            resolve_profile()
        except ProfileError as error:
            reasons.append(error.reason)
        if needs_tools:
            reasons.append("OS_ISOLATION_NOT_QUALIFIED")
        if search_enabled:
            reasons.append("OPENCODE_SEARCH_NOT_QUALIFIED")
        return reasons

    def describe_capabilities(self):
        try:
            route = resolve_profile().public()
        except ProfileError as error:
            route = {"status": "BLOCKED", "reason": error.reason, "credential_present": False}
        return {"adapter": self.runtime, "version": VERSION, "source_commit": COMMIT,
                "route_configuration": route, "authentication_verified": False, "developer_login_used": False,
                "live_verification": "BLOCKED", "native_parameters_schema": self.native_parameters_schema(),
                "native_api": {"submit": "READONLY_MOCK_WIRE_PILOT", "stream_events": "LOCAL_PERSISTED_CURSOR_NO_NATIVE_REPLAY",
                               "interrupt": "ABORT_REQUEST_REQUIRES_OBSERVED_OUTCOME"},
                "session_control": {name: "UNSUPPORTED" for name in ("resume", "fork", "steer")},
                "image_input": {"enabled_for_attempt": False, "compatibility": "NOT_VALIDATED"},
                "search_mcp": {"enabled_for_attempt": False, "compatibility": "NOT_VALIDATED"},
                "planning_blockers": ["OPENCODE_STRUCTURED_PLANNING_UNSUPPORTED"],
                "code_execution_blockers": self.preflight(True),
                "readonly_review_blockers": self.preflight(False),
                "budget": {"commercial_cost": "UNKNOWN", "live_hard_usd_limit": "UNSUPPORTED_LIVE_BLOCKED",
                           "native_steps": "FINAL_TEXT_ITERATION_BOUND_NOT_HARD_RPC_OR_FEE_CAP"}}

    def _scope(self, project_id, request):
        self._validate_request(request)
        self.service.project(project_id)
        try:
            profile = resolve_profile()
        except ProfileError as error:
            raise LiveBlocked([error.reason]) from None
        binding = request["task_binding"]
        run = self.service.run(project_id, binding["run_id"])
        task = self.service.task(project_id, binding["task_id"], binding["task_revision"])
        hashes = source_hashes(self.service.source_root)
        physical_stack = hashlib.sha256((self.service.source_root / "profiles/stack.lock.json").read_bytes()).hexdigest()
        if (any(binding[name] != run[name] for name in ("task_id", "task_revision", "spec_hash", "stack_lock_hash")) or
                task["review_status"] != "CURRENT" or task["revision"] != task["current_revision"] or
                task["sha256"] != binding["spec_hash"] or task["spec"]["source_hash"] != digest(hashes) or
                task["spec"].get("base_stack_hash", task["spec"]["stack_lock_hash"]) != physical_stack or
                not self.service.stack_current(task["spec"])):
            raise Conflict("OpenCode Task/source/stack binding is stale or changed")
        self.service._requirements_current(project_id, task["spec"]["requirement_refs"])
        expected_docs = [{k: ref[k] for k in ("document_id", "revision", "sha256")} for ref in task["spec"]["input_refs"]]
        if request.get("document_refs", []) != expected_docs:
            raise Conflict("OpenCode document set does not match the frozen Task")
        for ref in expected_docs:
            doc = self.service.document(project_id, ref["document_id"], ref["revision"])
            if doc["sha256"] != ref["sha256"] or doc["current_revision"] != ref["revision"]:
                raise Conflict("OpenCode document bytes or revision changed")
        budget = task["spec"]["budget"]
        if request["max_turns"] > budget["max_steps"] or request["timeout_seconds"] > budget["timeout_seconds"]:
            raise Conflict("OpenCode review cannot enlarge the approved Task budget")
        return {"runtime": self.runtime, "cli_version": VERSION, "cli_sha256": profile.executable_sha256,
                "upstream_commit": COMMIT, "profile_hash": profile.fingerprint,
                "credential_fingerprint": profile.credential_sha256, "source_hash": digest(hashes), "stack_hash": physical_stack,
                "permissions_hash": digest({"needs_tools": False, "policy": "OPENCODE_DENY_ALL_READONLY_V1"}),
                "input_hash": digest(request), "root_budget_usd": None, "cost_kind": "MOCK_WIRE_UNKNOWN_COMMERCIAL_COST",
                "native_steps": min(request["max_turns"], 1), "rpc_cap_enforcement": "NOT_QUALIFIED_LIVE_BLOCKED"}

    def inspect_record(self, project_id, execution_id):
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM agent_executions WHERE id=? AND project_id=?", (execution_id, project_id)).fetchone()
        if row is None:
            raise NotFound("Native execution does not exist in this project")
        return dict(row)

    def inspect(self, project_id, execution_id):
        row = self.inspect_record(project_id, execution_id)
        if row["runtime"] != self.runtime:
            raise Conflict("This execution belongs to another native runtime")
        received=self.active.get(execution_id,{}).get('received_writer')
        if received is not None:
            recovery=received.recovery();row['received_event_recovery']=recovery
            if recovery['archives'] or row.get('result') and recovery['pending_in_memory']:
                result=json.loads(row['result']) if row.get('result') else {}
                row['result']=canonical({**result,'received_event_recovery':recovery})
        return row

    def inspect_execution_project(self, execution_id):
        with self.service.connect() as connection:
            row = connection.execute("SELECT project_id FROM agent_executions WHERE id=?", (execution_id,)).fetchone()
        if row is None:
            raise NotFound("Native execution does not exist")
        return row[0]

    def _event(self, execution_id, payload):
        self._event_batch(execution_id, [payload])

    def _event_batch(self, execution_id, payloads):
        from packages.adapters.task_resources import retain_execution_output
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            retain_execution_output(self,connection,execution_id,sum(len(canonical(p).encode()) for p in payloads))
            seq = connection.execute("SELECT coalesce(max(event_seq),0)+1 FROM agent_events WHERE execution_id=?", (execution_id,)).fetchone()[0]
            connection.executemany("INSERT INTO agent_events VALUES (?,?,?,?)",
                [(execution_id, seq + offset, canonical(payload), now()) for offset, payload in enumerate(payloads)])

    async def _write_events(self, context, execution_id):
        from packages.adapters.received_event_writer import ReceivedEventWriter
        try:writer=ReceivedEventWriter(self,context,execution_id)
        except Exception as error:
            context['protocol_error']='EVENT_WRITER_'+type(error).__name__
            context['done'].set();context['connected'].set()
            raise
        context['received_writer']=writer
        await writer.run()

    async def _flush_events(self, context):
        # Stop receiving first: this barrier covers every already-received original frame,
        # including metadata arriving during the final message GET. No native replay is invented.
        received=context.get('received_writer')
        if received is not None:
            await received.flush()
            if received.archives or received.has_pending_data:
                try:
                    with self.service.connect() as connection:
                        row=connection.execute('SELECT result FROM agent_executions WHERE id=?',(received.ident,)).fetchone()
                        if row is None:raise Conflict('Original received event execution is missing')
                        result=json.loads(row[0]) if row[0] else {}
                        result['received_event_recovery']=received.recovery()
                        connection.execute('UPDATE agent_executions SET result=? WHERE id=?',(canonical(result),received.ident))
                    context.pop('event_recovery_metadata_unconfirmed',None)
                except Exception:context['event_recovery_metadata_unconfirmed']=True
            return
        listener = context.get("listener")
        if listener:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        if context.get("writer"):
            if context.get("protocol_error", "").startswith("EVENT_WRITER_"):
                context['event_recovery_unowned']=True
                return  # Keep the queue/context while allowing other owned cleanup.
            await context["event_queue"].join()

    def stream_events(self, project_id, execution_id, after=0):
        self.inspect(project_id, execution_id)
        with self.service.connect() as connection:
            return [{"event_seq": row["event_seq"], "payload": json.loads(row["payload"]), "created_at": row["created_at"]}
                    for row in connection.execute("SELECT * FROM agent_events WHERE execution_id=? AND event_seq>? ORDER BY event_seq", (execution_id, after))]

    def export_result(self, project_id, execution_id):
        row = self.inspect(project_id, execution_id)
        from packages.adapters.received_event_writer import export_received_recovery
        return {"execution": row, "events": self.stream_events(project_id, execution_id), "verification_status": "NOT_RUN",
                'received_event_recovery':export_received_recovery(self,row),
                "native_result_recorded": bool(row["result"]), "live_evidence": False, "live_verification": "BLOCKED",
                "limitations": ["Original CLI/provider wire pilot; company, commercial-budget and native Windows qualification remain unvalidated",
                                "Native review does not complete FormalRun or independent verification; abort does not roll back external actions"]}

    def _record(self, project_id, operation_id, request, scope, workspace):
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT * FROM agent_executions WHERE project_id=? AND operation_id=?", (project_id, operation_id)).fetchone()
            if old:
                if old["runtime"] != self.runtime or old["request_hash"] != digest(request):
                    raise Conflict("Native operation is frozen to another runtime or request")
                return dict(old), False
            reference = str(uuid.uuid4())
            connection.execute("INSERT INTO agent_executions VALUES (?,?,?,?,?,?,'QUEUED','NOT_RUN',NULL,?,?,?,NULL,?,NULL,0)",
                (reference, project_id, operation_id, digest(request), self.runtime, str(workspace),
                 "ORIGINAL_MOCK_WIRE_ONLY", VERSION, canonical(request), now()))
            expires = (datetime.now(timezone.utc) + timedelta(seconds=request.get("session_ttl_seconds", 600))).isoformat()
            connection.execute("INSERT INTO agent_scopes VALUES (?,?,?,?,?,?,?,?,?,?)",
                (reference, canonical(scope), digest(scope), expires, reference, None, "0", None, None, reference))
            recorded = dict(connection.execute("SELECT * FROM agent_executions WHERE id=?", (reference,)).fetchone())
            from packages.adapters.task_resources import bind_created_execution
            bind_created_execution(self,connection,recorded)
            return recorded, True

    def _terminal(self, execution_id, status, result=None):
        with self.service.connect() as connection:
            received=self.active.get(execution_id,{}).get('received_writer')
            if received is not None and (received.archives or received.has_pending_data):
                if result is None:
                    previous=connection.execute('SELECT result FROM agent_executions WHERE id=?',(execution_id,)).fetchone()
                    result=json.loads(previous[0]) if previous and previous[0] else {}
                result={**result,'received_event_recovery':received.recovery()}
            connection.execute("UPDATE agent_executions SET execution_status=?,result=?,ended_at=? WHERE id=?", (status, canonical(result) if result else None, now(), execution_id))

    def _config(self, profile):
        return {"autoupdate": False, "share": "disabled", "snapshot": False, "formatter": False, "lsp": False, "plugin": [],
                "permission": {"*": "deny"}, "enabled_providers": [PROVIDER], "model": PROVIDER + "/" + profile.model_id,
                "provider": {PROVIDER: {"npm": "@ai-sdk/openai-compatible", "name": "EEAS original Mock transport — not company",
                    "options": {"baseURL": profile.provider_url, "apiKey": profile.api_key, "timeout": 15000, "headerTimeout": 15000, "chunkTimeout": 15000},
                    "models": {profile.model_id: {"tool_call": False, "reasoning": False, "attachment": False,
                        "limit": {"context": 131072, "output": 512}, "modalities": {"input": ["text"], "output": ["text"]}}}}},
                "agent": {AGENT: {"mode": "primary", "model": PROVIDER + "/" + profile.model_id, "steps": 1, "permission": {"*": "deny"}}}}

    @staticmethod
    def _environment(home, config, password):
        environment = {name: os.environ[name] for name in ("PATH", "LANG", "SystemRoot", "WINDIR", "COMSPEC") if name in os.environ}
        environment.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "roaming"), "LOCALAPPDATA": str(home / "local"),
            "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"), "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_STATE_HOME": str(home / "state"), "TMPDIR": str(home / "tmp"), "TEMP": str(home / "tmp"), "TMP": str(home / "tmp"),
            "OPENCODE_CONFIG": str(config), "OPENCODE_SERVER_USERNAME": "eeas", "OPENCODE_SERVER_PASSWORD": password,
            "OPENCODE_PURE": "true", "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true",
            "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "true",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true", "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true", "OPENCODE_DISABLE_CLAUDE_CODE": "true", "OPENCODE_DISABLE_EMBEDDED_WEB_UI": "true",
            "OPENCODE_DISABLE_FFF": "true", "OPENCODE_AUTO_SHARE": "false", "OPENCODE_CLIENT": "eeas",
            "npm_config_offline": "true", "npm_config_ignore_scripts": "true", "npm_config_audit": "false", "npm_config_fund": "false",
            "npm_config_cache": str(home / "npm-cache")})
        from packages.adapters.opencode_support import support_environment
        return support_environment(environment)

    async def _http(self, context, method, path, body=None, expected=200):
        sequence = context.get("http_request_sequence", 0) + 1
        context["http_request_sequence"] = sequence
        pending = context.setdefault("http_inflight", {})
        started = time.monotonic()
        detail = {"request_id": sequence, "method": method, "path": path}
        context["http_inflight_count"] = context.get("http_inflight_count", 0) + 1
        # Diagnostics must remain bounded even if several owned control reads overlap.
        if len(pending) < 16: pending[sequence] = detail
        context["http_phase"] = method + " " + path
        try:
            with (context["workspace"] / "http-starts.jsonl").open("a", encoding="utf-8") as out:
                out.write(canonical({**detail, "started_at": now()}) + "\n")
            response = await context["client"].request(method, path, json=body)
            raw = response.content
            observed = {**detail, "status": response.status_code,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
            context["http_last_response"] = observed
            with (context["workspace"] / "http-responses.jsonl").open("a", encoding="utf-8") as out:
                # Config/Authorization/provider secrets are never added to diagnostics.
                out.write(canonical({**observed, "body": None if path == "/config" else raw.decode("utf-8", errors="replace")}) + "\n")
            if response.status_code != expected or len(raw) > 4 * 1024 * 1024:
                raise ValueError("Unexpected or oversized native HTTP response")
            return None if not raw else json.loads(raw, parse_constant=_constant)
        except BaseException as error:
            context["http_last_error"] = {**detail, "error_type": type(error).__name__}
            raise
        finally:
            pending.pop(sequence, None)
            context["http_inflight_count"] -= 1
            remaining = next(iter(pending.values()), None)
            context["http_phase"] = (remaining["method"] + " " + remaining["path"] if remaining else
                                     "HTTP requests in flight (details bounded)" if context["http_inflight_count"] else None)

    async def _listen(self, context, execution_id):
        try:
            async with context["client"].stream("GET", "/event", timeout=None) as response:
                if response.status_code != 200:
                    raise ValueError("Native event subscription was not accepted")
                lines, size = [], 0
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        lines.append(line[5:].lstrip()); size += len(line.encode())
                        if size > 1024 * 1024: raise ValueError("Native SSE frame exceeded limit")
                    elif line == "" and lines:
                        raw = "\n".join(lines).encode(); lines, size = [], 0
                        context['pending_native_frame']=raw
                        with (context["workspace"] / "native-events.jsonl").open("ab") as out:
                            out.write(raw + b"\n")
                        event = decode_event(raw)
                        identity, sha = event.get("id"), hashlib.sha256(raw).hexdigest()
                        if identity is not None:
                            if not isinstance(identity, str): raise ValueError("Invalid native event ID")
                            if identity in context["seen"]:
                                if context["seen"][identity] != sha: raise ValueError("Native event ID collided with different bytes")
                                context.pop('pending_native_frame',None)
                                continue
                            context["seen"][identity] = sha
                        public = redact_native(event, context["secrets"])
                        payload={"type": "OpenCodeEvent", "native_type": event["type"], "native_payload": public,
                            "native_payload_sha256": sha, "native_replay": False, "credential_redacted": public != event}
                        context['pending_received_event']=payload
                        await context["event_queue"].put(payload)
                        context.pop('pending_received_event',None);context.pop('pending_native_frame',None)
                        if event["type"] == "server.connected": context["connected"].set()
                        properties = event.get("properties", {})
                        if properties.get("sessionID") != context.get("session_id"): continue
                        if event["type"] == "session.error":
                            context["native_error"] = event
                        if event["type"] == "session.status":
                            status = properties.get("status", {}).get("type")
                            if status == "busy": context["busy"] = True
                            elif status == "idle" and context["busy"]:
                                context["idle_observed"] = True; context["done"].set()
            if not context.get("closing"):
                raise ConnectionError("Native SSE ended without controller shutdown")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            context["protocol_error"] = type(error).__name__
            context["done"].set(); context["connected"].set()

    async def submit(self, project_id, operation_id, request):
        self._validate_request(request)
        blockers = self.preflight(request["needs_tools"], search_enabled=request.get("search_enabled", False))
        if blockers: raise LiveBlocked(blockers)
        scope = self._scope(project_id, request)
        # Outside harness/Git ancestors: no personal rules, native config or source checkout discovery.
        workspace = Path(tempfile.mkdtemp(prefix="eeas-opencode-readonly-"))
        try:
            row, created = self._record(project_id, operation_id, request, scope, workspace)
        except Exception:
            workspace.rmdir()
            raise
        if not created:
            workspace.rmdir(); return row
        context = {"workspace": workspace, "loop": asyncio.get_running_loop(), "connected": asyncio.Event(), "done": asyncio.Event(),
                   "seen": {}, "busy": False, "abort_acknowledged": False, "idle_observed": False,
                   "event_queue": asyncio.Queue(maxsize=32)}
        self.active[row["id"]] = context
        try:
            await asyncio.wait_for(self._drive(project_id, row, request, scope, context), timeout=request["timeout_seconds"])
        except asyncio.CancelledError:
            await self._flush_events(context)
            self._event(row["id"], {"type": "opencode_controller_stopped", "outcome": "UNKNOWN_OUTCOME"})
            self._terminal(row["id"], "UNKNOWN_OUTCOME")
            raise
        except Exception as error:
            await self._flush_events(context)
            self._event(row["id"], {"type": "opencode_transport_error", "error_type": type(error).__name__, "outcome": "UNKNOWN_OUTCOME",
                "observed": {"http_phase": context.get("http_phase"),
                             "http_inflight_count": context.get("http_inflight_count", 0),
                             "http_inflight": list(context.get("http_inflight", {}).values())[:16],
                             "last_http_response": context.get("http_last_response"),
                             "last_http_error": context.get("http_last_error"),
                             "deadline_seconds": request["timeout_seconds"],
                             "busy": context["busy"], "idle": context["idle_observed"],
                             "done": context["done"].is_set(), "protocol_error": context.get("protocol_error")}})
            self._terminal(row["id"], "UNKNOWN_OUTCOME")
        finally:
            context["closing"] = True
            await self._flush_events(context)
            if context.get("writer"):
                context["writer"].cancel()
                await asyncio.gather(context["writer"], return_exceptions=True)
            if "client" in context: await context["client"].aclose()
            process = context.get("process")
            if process and process.returncode is None:
                process.terminate()
                try: await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    process.kill(); await process.wait()
            if context.get("subscription_meter") and not context["subscription_meter"].close_confirmed:
                await context["subscription_meter"].close()
            self._event(row["id"], {"type": "opencode_process_stopped", "returncode": process.returncode if process else None,
                                    "external_actions_rolled_back": False,
                                    "subscription_meter_close_confirmed": getattr(context.get("subscription_meter"),"close_confirmed",None),
                                    "subscription_meter_cleanup_error": context.get("subscription_meter_cleanup_error")})
            for stream in context.get("logs", []): stream.close()
            received=context.get('received_writer')
            if (not context.get('event_recovery_unowned') and not context.get('event_recovery_metadata_unconfirmed')
                    and (received is None or received.close_confirmed)):
                self.active.pop(row["id"], None)
        return self.inspect(project_id, row["id"])

    from packages.adapters.task_resources import execution_scope as _execution_resource_scope
    @_execution_resource_scope
    async def _drive(self, project_id, row, request, scope, context):
        profile = resolve_profile()
        if self._scope(project_id, request) != scope: raise Conflict("Native scope changed before process dispatch")
        if self.inspect(project_id, row["id"])["cancel_requested"]:
            self._terminal(row["id"], "CANCELLED"); return
        workspace = context["workspace"]
        home, project = workspace / "home", workspace / "project"
        home.mkdir(); project.mkdir(); (home / "tmp").mkdir()
        config = home / "config" / "opencode" / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text(canonical(self._config(profile)), encoding="utf-8"); config.chmod(0o600)
        password = secrets.token_hex(24)
        context["secrets"] = [profile.api_key, password]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        logs = [open(workspace / name, "wb") for name in ("server-stdout.bin", "server-stderr.bin")]
        context["logs"] = logs
        with self.service.connect() as connection:
            connection.execute("UPDATE agent_executions SET execution_status='RUNNING' WHERE id=?", (row["id"],))
        from packages.adapters.opencode_startup_guard import reject_unreviewed_project_origins
        reject_unreviewed_project_origins(project)
        context["process"] = await asyncio.create_subprocess_exec(str(profile.executable), "--pure", "serve", "--hostname", "127.0.0.1", "--port", str(port),
            cwd=project, env=self._environment(home, config, password), stdout=logs[0], stderr=logs[1])
        context["client"] = httpx.AsyncClient(base_url="http://127.0.0.1:" + str(port), auth=("eeas", password),
            headers={"x-opencode-directory": str(project)}, trust_env=False, timeout=5)
        while True:
            if context["process"].returncode is not None: raise ConnectionError("Native server exited before readiness")
            try:
                health = await self._http(context, "GET", "/global/health")
                break
            except httpx.TransportError:
                await asyncio.sleep(.05)  # Bounded by Task timeout; retry readiness GET only.
        if health != {"healthy": True, "version": VERSION}: raise Conflict("Native version differs from frozen CLI")
        effective = await self._http(context, "GET", "/config")
        if (effective.get("permission") != {"*": "deny"} or effective.get("plugin") != [] or effective.get("lsp") is not False or
                effective.get("autoupdate") is not False or effective.get("enabled_providers") != [PROVIDER] or
                effective.get("provider", {}).get(PROVIDER, {}).get("options", {}).get("baseURL") != profile.provider_url):
            raise Conflict("Native configuration differs from approved readonly profile")
        self._event(row["id"], {"type": "opencode_native_health", "health": health, "effective_permission": effective["permission"],
                               "profile_hash": profile.fingerprint, "transport_kind": "MOCK_WIRE_DECLARED"})
        context["writer"] = asyncio.create_task(self._write_events(context, row["id"]))
        context["listener"] = asyncio.create_task(self._listen(context, row["id"]))
        await context["connected"].wait()
        if context.get("protocol_error"): raise ConnectionError("Native SSE connection failed")
        if self._scope(project_id, request) != scope: raise Conflict("Frozen Task/native profile changed before session creation")
        session = await self._http(context, "POST", "/session", {"title": "EEAS frozen readonly review " + row["id"],
            "permission": [{"permission": "*", "pattern": "*", "action": "deny"}]})
        session_id = session.get("id")
        if not isinstance(session_id, str) or not session_id.startswith("ses_"): raise ValueError("Native session ID not established")
        context["session_id"] = session_id
        with self.service.connect() as connection:
            connection.execute("UPDATE agent_executions SET session_id=? WHERE id=?", (session_id, row["id"]))
        self._event(row["id"], {"type": "opencode_session_created", "session_id": session_id, "task_binding": request["task_binding"]})
        if self.inspect(project_id, row["id"])["cancel_requested"]:
            self._terminal(row["id"], "CANCELLED"); return
        # Session title is explicit, so there is no auxiliary model title request. Never retry POSTs.
        await self._http(context, "POST", "/session/" + session_id + "/prompt_async", {"agent": AGENT,
            "model": {"providerID": PROVIDER, "modelID": profile.model_id}, "parts": [{"type": "text", "text": request["prompt"]}]}, expected=204)
        await context["done"].wait()
        await context["event_queue"].join()
        if context.get("protocol_error"): raise ConnectionError("Native SSE continuity lost")
        messages = await self._http(context, "GET", "/session/" + session_id + "/message")
        if not isinstance(messages, list) or any(m.get("info", {}).get("sessionID") != session_id for m in messages):
            raise ValueError("Native messages belong to another session")
        assistants = [m for m in messages if m.get("info", {}).get("role") == "assistant"]
        if context.get("abort_pending"):
            await context["abort_pending"].wait()
        aborted = any(m["info"].get("error", {}).get("name") == "MessageAbortedError" for m in assistants)
        if context["abort_acknowledged"] and context["idle_observed"] and aborted:
            status = "CANCELLED"
        elif context.get("native_error") or any(m["info"].get("error") for m in assistants):
            status = "FAILED"
        elif context["idle_observed"] and any(m["info"].get("time", {}).get("completed") and m["info"].get("finish") == "stop" for m in assistants):
            status = "RETURNED"
        else:
            status = "UNKNOWN_OUTCOME"
        result = {"runtime": self.runtime, "session_id": session_id, "native_messages": messages,
                  "native_error": context.get("native_error"), "idle_observed": context["idle_observed"],
                  "abort_acknowledged": context["abort_acknowledged"], "commercial_cost_usd": None,
                  "cost_kind": "UNKNOWN_COMMERCIAL_MOCK_WIRE_ONLY", "verification_status": "NOT_RUN"}
        (workspace / "native-result.json").write_text(canonical(result), encoding="utf-8")
        result["native_result_sha256"] = hashlib.sha256((workspace / "native-result.json").read_bytes()).hexdigest()
        public = redact_native(result, context["secrets"])
        public["credential_redacted"] = public != result
        await self._flush_events(context)
        self._terminal(row["id"], status, public)

    async def request_cancel(self, project_id, execution_id):
        row = self.inspect(project_id, execution_id)
        if row["execution_status"] in TERMINAL or row["cancel_requested"]: return row
        context = self.active.get(execution_id)
        if context and context["loop"] is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.request_cancel(project_id, execution_id), context["loop"])
            return await asyncio.wrap_future(future)
        with self.service.connect() as connection:
            connection.execute("UPDATE agent_executions SET cancel_requested=1,execution_status='CANCEL_REQUESTED' WHERE id=? AND execution_status IN ('QUEUED','RUNNING')", (execution_id,))
        self._event(execution_id, {"type": "cancel_requested", "outcome": "CANCEL_REQUESTED", "rollback": False})
        if not context:
            self._terminal(execution_id, "UNKNOWN_OUTCOME")
        elif context.get("session_id"):
            context["abort_pending"] = asyncio.Event()
            try:
                acknowledged = await self._http(context, "POST", "/session/" + context["session_id"] + "/abort")
                context["abort_acknowledged"] = acknowledged is True
                self._event(execution_id, {"type": "opencode_abort_response", "acknowledged": acknowledged is True, "outcome": "CANCEL_REQUESTED"})
            except Exception as error:
                context["protocol_error"] = type(error).__name__; context["done"].set()
                self._event(execution_id, {"type": "opencode_abort_unconfirmed", "outcome": "UNKNOWN_OUTCOME"})
            finally:
                context["abort_pending"].set()
        return self.inspect(project_id, execution_id)

    def control_preflight(self, project_id, execution_id, kind, *, expected_session_id):
        row = self.inspect(project_id, execution_id)
        if row["session_id"] != expected_session_id: raise Conflict("Native session identity does not match")
        raise LiveBlocked(["OPENCODE_SESSION_CONTROL_UNSUPPORTED"])

    async def extract(self, *args, **kwargs):
        raise LiveBlocked(["OPENCODE_STRUCTURED_PLANNING_UNSUPPORTED"])
