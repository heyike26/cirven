"""Same-origin, exact-session reads from an actually active native planning owner."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import stat
from urllib.parse import urlsplit

from packages.adapters.opencode import decode_event, redact_native
from packages.adapters.opencode_profile import CLI_SHA, VERSION
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest


@dataclass(frozen=True)
class NativeGatewayResponse:
    status_code: int
    media_type: str
    body: bytes


class OpenCodeNativeGateway:
    """No native server URL, Basic password or permission mutation reaches a browser.

    The transport remains the sole process owner. This gateway cannot start,
    restart, keep alive, enlarge, approve, or turn a planning session into chat.
    Existing native POSTs and budget admission remain explicit EEAS operations.
    """

    def __init__(self, service, planning_transport, workspace_authority, *, prefix="/api/agent-shell/native"):
        if not prefix.startswith("/") or "?" in prefix or "#" in prefix or "//" in prefix:
            raise ValueError("Native gateway prefix must be a same-origin local route")
        self.service = service; self.transport = planning_transport
        self.workspaces = workspace_authority; self.prefix = prefix.rstrip("/")

    def _bound(self, project_id, execution_id, workspace_ref=None):
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM agent_executions WHERE id=? AND project_id=?", (execution_id, project_id)).fetchone()
            binding = connection.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None: raise NotFound("Native owner is outside this project")
        if row["runtime"] != "opencode-planning-http" or row["sdk_version"] != VERSION or not binding:
            raise Conflict("Only the qualified planning runtime's active read scope is supported")
        scope = json.loads(binding["scope"]); request = json.loads(row["request"])
        if (digest(scope) != binding["scope_hash"] or digest(request) != row["request_hash"] or
                scope.get("input_hash") != row["request_hash"] or scope.get("runtime") != row["runtime"] or
                scope.get("cli_sha256") not in CLI_SHA.values()):
            raise Conflict("Native owner binary/request/scope binding changed")
        ref = scope.get("workspace_ref")
        if not isinstance(ref, dict) or request.get("workspace_ref") != ref:
            raise Conflict("Native owner has no authoritative workspace binding")
        if workspace_ref is not None and workspace_ref != ref:
            raise Conflict("Selected workspace differs from the native owner")
        bound = self.workspaces.get(project_id, ref["id"], ref["revision"])
        if (bound["binding_sha256"] != ref["binding_sha256"] or
                digest(bound["root"]) != scope.get("workspace_root_sha256") or
                bound["root_identity"] != scope.get("workspace_root_identity")):
            raise Conflict("Native owner's selected actual folder changed")
        if (scope.get("profile_ref") != request.get("provider_profile_ref") or
                scope.get("budget") != request.get("budget") or
                scope.get("source_receipt_sha256") != request.get("source_receipt", {}).get("sha256")):
            raise Conflict("Native owner profile/source/budget binding changed")
        return dict(row), dict(binding), scope, bound

    def _active(self, row, binding):
        if row["execution_status"] not in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
            return None, "NATIVE_EXECUTION_FINISHED"
        if datetime.fromisoformat(binding["expires_at"]) <= datetime.now(timezone.utc):
            return None, "NATIVE_OWNER_SCOPE_EXPIRED"
        if getattr(self.transport, "closed", False):
            return None, "NATIVE_CONTROLLER_CLOSED"
        context = self.transport.active.get(row["id"])
        if not context: return None, "NATIVE_OWNER_NOT_CONNECTED"
        if context.get("closing"): return None, "NATIVE_OWNER_CLOSING"
        process = context.get("process")
        if not process: return None, "NATIVE_PROCESS_NOT_STARTED"
        if process.returncode is not None: return None, "NATIVE_PROCESS_STOPPED"
        if not context.get("client") or not context.get("loop") or not context["loop"].is_running():
            return None, "NATIVE_OWNER_NOT_CONNECTED"
        if not row["session_id"] or context.get("session_id") != row["session_id"]:
            return None, "NATIVE_SESSION_NOT_ESTABLISHED"
        return context, None

    @staticmethod
    def _private_directory(row, context):
        # The selected user folder is separately frozen by _bound. The native
        # planning process opens only this execution's own isolated directory.
        recorded = row["workspace"]
        if not isinstance(recorded, str) or str(context.get("workspace")) != recorded:
            raise Conflict("Native owner's private workspace differs from its execution record")
        workspace = Path(recorded)
        directory = workspace / "runtime-project"
        if not workspace.is_absolute() or str(workspace) != recorded:
            raise Conflict("Native owner's private workspace is not an exact absolute directory")
        try:
            # Do not resolve a link and then authorize its destination. Every
            # existing component must be a real directory, including ancestors.
            for component in (directory, *directory.parents):
                info = component.lstat()
                if (not stat.S_ISDIR(info.st_mode) or
                        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    raise Conflict("Native owner's private directory contains a link or unsupported component")
            if directory.resolve(strict=True) != directory:
                raise Conflict("Native owner's private directory is not canonical")
        except (OSError, ValueError) as error:
            raise Conflict("Native owner's private directory is unavailable") from error
        if context["client"].params.get_list("directory") != [str(directory)]:
            raise Conflict("Native owner's private client directory differs from its execution runtime-project")
        return directory

    def describe(self, project_id, execution_id=None, *, workspace_ref=None):
        unavailable = {"status": "UNAVAILABLE", "server_url": None,
            "protocol": "OPENCODE_OPENAPI_V1_19_0_READONLY", "reason": "NO_ACTIVE_FROZEN_NATIVE_EXECUTION",
            "long_lived_conversation": False, "native_mutations_allowed": False,
            "original_app_interface": "DISABLED_REQUIRES_LONG_LIVED_SESSION_OWNER"}
        self.service.project(project_id)
        if execution_id is None:
            with self.service.connect() as connection:
                candidates = connection.execute("SELECT id FROM agent_executions WHERE project_id=? AND runtime=? ORDER BY created_at DESC",
                    (project_id, "opencode-planning-http")).fetchall()
            execution_id = next((r["id"] for r in candidates if r["id"] in self.transport.active), None)
            if execution_id is None: return unavailable
        row, binding, scope, _bound = self._bound(project_id, execution_id, workspace_ref)
        context, reason = self._active(row, binding)
        details = {"execution_id": row["id"], "session_id": row["session_id"], "workspace_ref": scope["workspace_ref"],
            "profile_ref": scope["profile_ref"], "model": scope["model"], "budget": scope["budget"]}
        if context is None: return {**unavailable, **details, "reason": reason}
        self._private_directory(row, context)
        return {**unavailable, **details, "status": "PARTIAL_READONLY_EXACT_SESSION", "reason": "ACTIVE_PLANNING_OWNER_ONLY",
            "server_url": self.prefix + "/" + row["id"],
            "read_paths": ["/session/" + row["session_id"], "/session/" + row["session_id"] + "/message", "/event"],
            "structured_message_get": "ORIGINAL_V1_19_0_ENCODER_400", "native_event_replay": False}

    def authorize(self, project_id, execution_id, method, path, *, workspace_ref=None, query=None):
        # Exact literal native paths only; callers cannot supply directory/origin/server/header overrides.
        if method != "GET" or query:
            raise Conflict("Native mutations and caller query overrides require a separate admitted EEAS action")
        if (not isinstance(path, str) or not path.startswith("/") or any(char in path for char in "%\\?#") or
                any(ord(char) <= 32 for char in path) or urlsplit(path).netloc):
            raise Conflict("Native path is not an exact local scoped path")
        row, binding, scope, _bound = self._bound(project_id, execution_id, workspace_ref)
        allowed = {"/session/" + str(row["session_id"]), "/session/" + str(row["session_id"]) + "/message", "/event"}
        if not row["session_id"] or path not in allowed:
            raise Conflict("Native read is outside the exact recorded session")
        context, reason = self._active(row, binding)
        if context is None: raise Conflict("Native read unavailable: " + reason)
        self._private_directory(row, context)
        return row, context

    @staticmethod
    def _redact(value, context):
        private = list(context.get("secrets", []))
        # Basic credentials encoded in an echoed error are private too.
        private += [base64.b64encode(("eeas:" + secret).encode()).decode() for secret in private if secret]
        def clean(item):
            if isinstance(item, dict):
                return {redact_native(key, private): clean(child) for key, child in item.items()}
            if isinstance(item, list): return [clean(child) for child in item]
            return redact_native(item, private)
        return clean(value)

    async def read(self, project_id, execution_id, path, *, workspace_ref=None, query=None):
        if path == "/event": raise Conflict("Use the bounded native event stream for SSE")
        row, context = self.authorize(project_id, execution_id, "GET", path, workspace_ref=workspace_ref, query=query)
        if context["loop"] is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.read(project_id, execution_id, path,
                workspace_ref=workspace_ref, query=query), context["loop"])
            return await asyncio.wrap_future(future)
        try:
            response = await context["client"].get(path)
        except Exception:
            # Cleanup can close the client while a browser read is in flight.
            # Report the revoked scope, not a successful stale response.
            self.authorize(project_id, execution_id, "GET", path, workspace_ref=workspace_ref, query=query)
            raise
        self.authorize(project_id, execution_id, "GET", path, workspace_ref=workspace_ref, query=query)
        if 300 <= response.status_code < 400:
            raise Conflict("Native redirect cannot leave the exact process scope")
        if len(response.content) > 4 * 1024 * 1024:
            raise Conflict("Native response exceeded its read bound")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if media_type != "application/json": raise Conflict("Native gateway only exposes scoped JSON reads")
        value = json.loads(response.content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite native JSON")))
        public = self._redact(value, context)
        # No upstream response headers, Authorization, Basic password,
        # redirect or Set-Cookie is returned for a browser to replay.
        return NativeGatewayResponse(response.status_code, "application/json", canonical(public).encode())

    async def stream(self, project_id, execution_id, *, workspace_ref=None, query=None):
        row, context = self.authorize(project_id, execution_id, "GET", "/event", workspace_ref=workspace_ref, query=query)
        if context["loop"] is not asyncio.get_running_loop():
            raise Conflict("Native event subscription must run on the actual owner's event loop")
        session_id = row["session_id"]
        async with context["client"].stream("GET", "/event", timeout=None) as response:
            if response.status_code != 200: raise Conflict("Original native event subscription was denied")
            lines, size = [], 0
            async for line in response.aiter_lines():
                if context.get("closing") or context["process"].returncode is not None: break
                if line.startswith("data:"):
                    lines.append(line[5:].lstrip()); size += len(line.encode())
                    if size > 1024 * 1024: raise Conflict("Native event exceeds the bounded protocol size")
                elif not line and lines:
                    native = decode_event("\n".join(lines).encode()); lines, size = [], 0
                    if native["type"] != "server.connected" and native.get("properties", {}).get("sessionID") != session_id:
                        continue
                    public = self._redact(native, context)
                    yield ("data: " + canonical(public) + "\n\n").encode()
