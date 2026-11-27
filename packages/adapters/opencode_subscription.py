"""Project-owned native Codex OAuth control. No OAuth implementation or second agent loop.

Authentication transport success is separate from subscription/model entitlement.
Only the pinned native provider methods are called; no API-key fallback exists.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from packages.adapters.opencode import OpenCodeBridge
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, source_hashes

PLUGIN_SHA256 = "9480ee62a3de6cfd8c49651a1651f27a7381c2fa7a183a5eedff5960d284c202"
NATIVE_METHODS = {"browser": "ChatGPT Pro/Plus (browser)", "headless": "ChatGPT Pro/Plus (headless)"}
PROVIDER = "openai"


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise ValueError("Subscription binding identifier is invalid")
    return value


def model_allowed(model):
    if model in {"gpt-5.5-pro", "gpt-5.6"} or model.endswith("-pro"): return False
    if model in {"gpt-5.5", "gpt-5.3-codex-spark", "gpt-5.4", "gpt-5.4-mini", "gpt-6-sol", "gpt-6-luna"}: return True
    match = re.match(r"^gpt-(\d+)(?:\.(\d+))?", model)
    return bool(match and (int(match[1]) > 5 or int(match[1]) == 5 and int(match[2] or 0) > 4))


@dataclass(frozen=True)
class SubscriptionProfile:
    project_id: str
    profile_id: str
    revision: int
    model_id: str
    owner_id: str
    secret_ref: str
    cli_sha256: str
    login_method: str = "browser"

    def __post_init__(self):
        for value in (self.project_id, self.profile_id, self.owner_id, self.secret_ref, self.model_id): identifier(value)
        if (type(self.revision) is not int or self.revision < 1 or self.cli_sha256 not in CLI_SHA.values() or
                self.login_method not in NATIVE_METHODS or not self.secret_ref.startswith("native-auth:") or not model_allowed(self.model_id)):
            raise ValueError("Subscription profile identity/model/revision is invalid")

    @classmethod
    def parse(cls, value):
        keys = {"schema", "version", "source_commit", "plugin_sha256", "provider", "auth_mode",
                "project_id", "profile_id", "revision", "model_id", "owner_id", "secret_ref", "cli_sha256", "login_method"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("Subscription profile must use exact fields; API keys and custom endpoints are not accepted")
        if (value["schema"] != "eeas.opencode-codex-subscription.v1" or value["version"] != VERSION or
                value["source_commit"] != COMMIT or value["plugin_sha256"] != PLUGIN_SHA256 or
                value["provider"] != PROVIDER or value["auth_mode"] != "native_codex_oauth"):
            raise ValueError("Subscription provider/source identity is not approved")
        for key in ("project_id", "profile_id", "owner_id", "secret_ref", "model_id"): identifier(value[key])
        if not value["secret_ref"].startswith("native-auth:"):
            raise ValueError("Subscription credentials require an opaque native-owned secret reference")
        if type(value["revision"]) is not int or value["revision"] < 1:
            raise ValueError("Subscription profile revision is invalid")
        if value["cli_sha256"] not in CLI_SHA.values() or value["login_method"] not in NATIVE_METHODS:
            raise ValueError("Subscription CLI/method is unknown")
        if not model_allowed(value["model_id"]): raise ValueError("Model is rejected by the pinned native subscription policy")
        return cls(**{key: value[key] for key in cls.__dataclass_fields__})

    @property
    def fingerprint(self): return digest(self.__dict__)

    def public(self):
        return {**self.__dict__, "profile_hash": self.fingerprint, "provider": PROVIDER,
                "auth_mode": "native_codex_oauth", "api_key_fallback": False,
                "commercial_cost": "UNKNOWN", "subscription_eligibility": "NOT_VALIDATED", "model_access": "NOT_VALIDATED"}

    def native_config(self):
        # The original builtin provider/auth plugin owns OAuth, token renewal and inference.
        return {"autoupdate": False, "share": "disabled", "snapshot": False, "formatter": False, "lsp": False,
                "plugin": [], "permission": {"*": "deny"}, "enabled_providers": [PROVIDER],
                "model": PROVIDER + "/" + self.model_id}


def selected_workspace_binding(workspaces, project_id, workspace_id):
    if workspaces is None: raise Conflict("Selected local workspace authority is not connected")
    selected = workspaces.get(project_id, workspace_id)
    if (selected.get("id") != workspace_id or selected.get("project_id") != project_id or
            type(selected.get("revision")) is not int or selected["revision"] < 1 or
            not isinstance(selected.get("binding_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", selected["binding_sha256"]) or
            not Path(selected.get("root", "")).is_absolute() or selected.get("native_location", {}).get("directory") != selected["root"]):
        raise Conflict("Selected workspace authority returned an invalid native location")
    return {"project_id": project_id, "workspace_id": workspace_id, "workspace_revision": selected["revision"],
            "workspace_binding_sha256": selected["binding_sha256"], "workspace_root": selected["root"]}


def prepare_owned_auth_home(root, profile, password, *, workspaces=None, workspace_id=None):
    """Create a fresh native HOME; never discover or read a developer's auth.json."""
    workspace = selected_workspace_binding(workspaces, profile.project_id, workspace_id) if workspace_id is not None else None
    root = Path(root).absolute()
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents): raise ValueError("Native auth home is a symlink")
    root.mkdir(parents=True, exist_ok=False)
    home = root / "home"
    home.mkdir(); (home / "tmp").mkdir()
    if workspace is None:
        project = root / "project"; project.mkdir()
    else:
        project = Path(workspace["workspace_root"])
    config = root / "opencode.json"
    config.write_text(canonical(profile.native_config()), encoding="utf-8"); config.chmod(0o600)
    environment = OpenCodeBridge._environment(home, config, password)
    return {"home": home, "project": project, "config": config, "environment": environment,
            "secret_ref": profile.secret_ref, "workspace": workspace, "api_key_fallback": False}


class NativeAuthTransport:
    """Private native client/process seam, with a finite method/path allowlist."""
    def __init__(self, client, profile, process=None):
        origin = urlsplit(str(client.base_url))
        if (origin.scheme != "http" or origin.hostname != "127.0.0.1" or not origin.port or
                origin.username or origin.password or origin.query or origin.fragment or origin.path not in {"", "/"}):
            raise ValueError("Native OAuth transport must use a private loopback origin")
        if client.follow_redirects: raise ValueError("Native OAuth transport cannot follow redirects")
        self.client = client; self.profile_hash = profile.fingerprint; self.origin = str(client.base_url)
        self.process = process; self.closed = False
        self.workspace = None

    def bind_workspace(self, binding):
        if self.workspace and self.workspace != binding:
            raise Conflict("Native auth owner cannot switch its selected workspace binding")
        self.workspace = dict(binding)
        # The native API's directory query supports Unicode without lossy HTTP header encoding.
        self.client.headers.pop("x-opencode-directory", None)

    async def request(self, method, path, body=None):
        allowed = {("GET", "/provider/auth"), ("GET", "/provider"),
                   ("POST", "/provider/openai/oauth/authorize"), ("POST", "/provider/openai/oauth/callback"),
                   ("DELETE", "/auth/openai")}
        if (method, path) not in allowed: raise Conflict("Native subscription action is outside the exact provider auth scope")
        if self.closed or str(self.client.base_url) != self.origin or self.process is not None and self.process.returncode is not None:
            raise Conflict("Native subscription owner is closed or changed")
        if self.workspace and "x-opencode-directory" in self.client.headers:
            raise Conflict("Native auth directory changed outside its selected workspace binding")
        response = await self.client.request(method, path, json=body,
            params={"directory": self.workspace["workspace_root"]} if self.workspace else None)
        if response.status_code != 200:
            # No raw response/token/Authorization text enters exception messages or diagnostics.
            raise Conflict("Native subscription response rejected: HTTP " + str(response.status_code))
        if len(response.content) > 1024 * 1024: raise Conflict("Native subscription response exceeds byte bound")
        return response.json()

    async def close_owned(self):
        """A cancelled HTTP coroutine does not prove the native OAuth callback stopped."""
        if self.process is None: return False
        if self.process.returncode is None:
            self.process.terminate()
            try: await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill(); await asyncio.wait_for(self.process.wait(), 5)
        stopped = self.process.returncode is not None
        if stopped:
            await self.client.aclose(); self.closed = True
        return stopped


class OpenCodeSubscriptionAuth:
    def __init__(self, service, profile, transport, *, workspaces=None):
        service.project(profile.project_id)
        if transport.profile_hash != profile.fingerprint: raise Conflict("Native auth owner/profile binding changed")
        self.service = service; self.profile = profile; self.transport = transport
        self.attempt = None; self.blocker = None; self.run_bindings = {}
        self.workspaces = workspaces; self.workspace = None

    def bind_workspace(self, project_id, profile_hash, workspace_id):
        """Consume the LocalWorkspaces authority. No browser directory or duplicate filesystem implementation."""
        self._bound(project_id, profile_hash)
        if self.run_bindings or self.attempt and self.attempt["status"] in {"STARTING", "WAITING_USER", "COMPLETING", "CANCEL_REQUESTED"}:
            raise Conflict("Workspace selection cannot change a pending auth or frozen run scope")
        binding = selected_workspace_binding(self.workspaces, project_id, workspace_id)
        if self.workspace and self.workspace != binding: raise Conflict("Auth owner workspace is frozen; replacement requires a new owner")
        self.transport.bind_workspace(binding); self.workspace = dict(binding)
        return dict(binding)

    def _workspace_current(self):
        if self.workspace is None: return None
        selected = self.workspaces.get(self.profile.project_id, self.workspace["workspace_id"])
        if (selected["revision"] != self.workspace["workspace_revision"] or selected["binding_sha256"] != self.workspace["workspace_binding_sha256"] or
                selected["root"] != self.workspace["workspace_root"]):
            raise Conflict("Selected workspace binding changed after subscription scope freeze")
        return dict(self.workspace)

    def _bound(self, project_id, profile_hash):
        if project_id != self.profile.project_id or profile_hash != self.profile.fingerprint:
            raise Conflict("Subscription action is outside the exact project/profile revision")
        if self.transport.profile_hash != self.profile.fingerprint:
            raise Conflict("Native subscription owner changed")
        self.service.project(project_id)
        self._workspace_current()

    async def _method(self):
        methods = await self.transport.request("GET", "/provider/auth")
        selected = methods.get(PROVIDER, []) if isinstance(methods, dict) else []
        label = NATIVE_METHODS[self.profile.login_method]
        matches = [index for index, item in enumerate(selected) if item.get("type") == "oauth" and item.get("label") == label]
        if len(matches) != 1: raise Conflict("Exact pinned native Codex OAuth method is unavailable or ambiguous")
        return matches[0], digest(selected)

    async def begin(self, project_id, profile_hash, timeout_seconds=300):
        self._bound(project_id, profile_hash)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300: raise ValueError("OAuth wait must be bounded by five minutes")
        if self.attempt:
            raise Conflict("This native provider already has an auth attempt; replacement requires a fresh owner/profile revision")
        # Commit ownership before the first await; concurrent clicks cannot overwrite the native provider's pending map.
        attempt = {"id": secrets.token_hex(16), "status": "STARTING", "method": None, "methods_hash": None,
                   "deadline": time.monotonic() + timeout_seconds, "profile_hash": profile_hash}
        self.attempt = attempt
        try:
            index, methods_hash = await asyncio.wait_for(self._method(), timeout_seconds)
            attempt.update(method=index, methods_hash=methods_hash)
            reply = await asyncio.wait_for(self.transport.request("POST", "/provider/openai/oauth/authorize", {"method": index}),
                                           max(.001, attempt["deadline"] - time.monotonic()))
            if attempt["status"] != "STARTING": raise Conflict("Native authorization arrived after cancellation")
            if not isinstance(reply, dict) or set(reply) != {"url", "method", "instructions"} or reply["method"] != "auto":
                raise Conflict("Native Codex OAuth authorization reply is invalid")
            url = urlsplit(reply["url"]); query = parse_qs(url.query)
            expected = "/oauth/authorize" if self.profile.login_method == "browser" else "/codex/device"
            if (url.scheme != "https" or url.netloc != "auth.openai.com" or url.path != expected or url.fragment or
                    any(key in query for key in ("access_token", "refresh_token", "id_token", "id_token_hint"))):
                raise Conflict("Native OAuth returned an unapproved login destination")
            if self.profile.login_method == "browser" and (query.get("client_id") != ["app_EMoamEEZ73f0CkXaXp7hrann"] or
                    query.get("code_challenge_method") != ["S256"] or query.get("response_type") != ["code"] or
                    query.get("redirect_uri") != ["http://localhost:1455/auth/callback"] or
                    query.get("scope") != ["openid profile email offline_access"] or
                    len(query.get("state", [])) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", query["state"][0]) or
                    len(query.get("code_challenge", [])) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", query["code_challenge"][0])):
                raise Conflict("Native OAuth PKCE/client binding differs from the pinned source")
            if not isinstance(reply["instructions"], str) or len(reply["instructions"]) > 8192: raise Conflict("Native OAuth instructions are invalid")
            attempt["status"] = "WAITING_USER"
            return {"attempt_id": attempt["id"], "status": attempt["status"], "login_url": reply["url"],
                    "instructions": reply["instructions"], "profile_hash": profile_hash, "api_key_fallback": False}
        except asyncio.TimeoutError:
            await self.cancel(project_id, profile_hash, attempt["id"], expired=True)
            raise Conflict("Native OAuth authorize expired; owned stop confirmation is required") from None
        except asyncio.CancelledError:
            await self.cancel(project_id, profile_hash, attempt["id"])
            raise
        except BaseException:
            if attempt["status"] == "STARTING": attempt["status"] = "UNKNOWN_OUTCOME"
            raise

    def _attempt(self, project_id, profile_hash, attempt_id):
        self._bound(project_id, profile_hash)
        if not self.attempt or attempt_id != self.attempt["id"]: raise NotFound("OAuth attempt is outside this native owner")
        return self.attempt

    async def complete(self, project_id, profile_hash, attempt_id):
        attempt = self._attempt(project_id, profile_hash, attempt_id)
        if attempt["status"] != "WAITING_USER": raise Conflict("OAuth callback is not pending; native mutations are never automatically retried")
        if time.monotonic() >= attempt["deadline"]:
            await self.cancel(project_id, profile_hash, attempt_id, expired=True)
            raise Conflict("Native OAuth attempt expired")
        # Reserve the callback before awaiting its method check; native callback POST is never duplicated.
        attempt["status"] = "COMPLETING"
        try:
            index, methods_hash = await asyncio.wait_for(self._method(), max(.001, attempt["deadline"] - time.monotonic()))
            if index != attempt["method"] or methods_hash != attempt["methods_hash"]:
                raise Conflict("Native auth method array changed during authorization")
            reply = await asyncio.wait_for(self.transport.request("POST", "/provider/openai/oauth/callback", {"method": index}),
                                           max(.001, attempt["deadline"] - time.monotonic()))
            if attempt["status"] != "COMPLETING": raise Conflict("Native callback arrived after cancellation")
            if reply is not True: raise Conflict("Native OAuth callback did not commit")
            providers = await asyncio.wait_for(self.transport.request("GET", "/provider"), max(.001, attempt["deadline"] - time.monotonic()))
            if attempt["status"] != "COMPLETING": raise Conflict("Native provider reply arrived after cancellation")
            if not isinstance(providers, dict) or PROVIDER not in providers.get("connected", []):
                raise Conflict("Native OAuth callback did not establish the selected provider")
            attempt["status"] = "AUTHENTICATED_TRANSPORT_ONLY"
            return self.inspect(project_id, profile_hash)
        except asyncio.TimeoutError:
            await self.cancel(project_id, profile_hash, attempt_id, expired=True)
            raise Conflict("Native OAuth callback expired; owned stop confirmation is required") from None
        except asyncio.CancelledError:
            await self.cancel(project_id, profile_hash, attempt_id)
            raise
        except BaseException:
            if attempt["status"] == "COMPLETING": attempt["status"] = "UNKNOWN_OUTCOME"
            raise

    async def cancel(self, project_id, profile_hash, attempt_id, *, expired=False):
        attempt = self._attempt(project_id, profile_hash, attempt_id)
        if attempt["status"] in {"CANCELLED", "EXPIRED"}: return self.inspect(project_id, profile_hash)
        attempt["status"] = "CANCEL_REQUESTED"
        if await self.transport.close_owned(): attempt["status"] = "EXPIRED" if expired else "CANCELLED"
        return self.inspect(project_id, profile_hash)

    def inspect(self, project_id, profile_hash):
        self._bound(project_id, profile_hash)
        return {"profile": self.profile.public(), "attempt_id": self.attempt["id"] if self.attempt else None,
                "status": self.attempt["status"] if self.attempt else "NOT_STARTED", "blocker": self.blocker,
                "authentication_verified": False, "subscription_eligibility": "NOT_VALIDATED", "live_inference": "BLOCKED",
                "native_windows": "NOT_RUN", "api_key_fallback": False}

    def freeze_run(self, project_id, profile_hash, run_id, *, max_calls, timeout_seconds):
        self._bound(project_id, profile_hash)
        if not self.attempt or self.attempt["status"] != "AUTHENTICATED_TRANSPORT_ONLY" or self.blocker:
            raise Conflict("Selected subscription auth is absent, revoked or usage-blocked")
        run = self.service.run(project_id, run_id); task = self.service.task(project_id, run["task_id"], run["task_revision"])
        spec = task["spec"]; budget = spec["budget"]
        if (task["review_status"] != "CURRENT" or task["current_revision"] != run["task_revision"] or task["sha256"] != run["spec_hash"] or
                not self.service.stack_current(spec) or spec["source_hash"] != digest(source_hashes(self.service.source_root))):
            raise Conflict("Subscription task/source/stack binding is stale")
        self.service._requirements_current(project_id, spec["requirement_refs"])
        if (type(max_calls) is not int or type(timeout_seconds) is not int or not 1 <= max_calls <= budget["max_steps"] or
                not 1 <= timeout_seconds <= budget["timeout_seconds"]):
            raise Conflict("Subscription operation cannot enlarge the approved run budget")
        frozen = {"project_id": project_id, "run_id": run_id, "task_id": run["task_id"], "task_revision": run["task_revision"],
                  "spec_hash": run["spec_hash"], "stack_lock_hash": run["stack_lock_hash"], "source_hash": spec["source_hash"],
                  "profile_hash": profile_hash, "profile_revision": self.profile.revision, "model_id": self.profile.model_id,
                  "auth_attempt_id": self.attempt["id"], "secret_ref": self.profile.secret_ref,
                  "budget": {"max_calls": max_calls, "timeout_seconds": timeout_seconds, "commercial_cost": "UNKNOWN",
                             "hard_rpc_cap": "NOT_QUALIFIED", "api_key_fallback": False}}
        if self.workspace: frozen["workspace_binding"] = self._workspace_current()
        existing = self.run_bindings.get(run_id)
        if existing and existing != frozen: raise Conflict("An existing run cannot switch its frozen subscription profile or budget")
        self.run_bindings[run_id] = json.loads(canonical(frozen))
        return {**frozen, "binding_hash": digest(frozen), "live_inference": "BLOCKED"}

    def authorize_session_read(self, project_id, profile_hash, run_id, execution_id, method, path):
        self._bound(project_id, profile_hash)
        if self.blocker or not self.attempt or self.attempt["status"] != "AUTHENTICATED_TRANSPORT_ONLY":
            raise Conflict("Subscription auth/usage changed")
        frozen = self.run_bindings.get(run_id)
        if not frozen or frozen["auth_attempt_id"] != self.attempt["id"] or frozen["profile_hash"] != profile_hash:
            raise Conflict("Subscription run has no matching frozen profile/auth attempt")
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM agent_executions WHERE id=? AND project_id=?", (execution_id, project_id)).fetchone()
            scope = connection.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None: raise NotFound("Native subscription execution is outside the selected project")
        if not scope or digest(json.loads(scope["scope"])) != scope["scope_hash"]: raise Conflict("Native subscription scope changed")
        selected = json.loads(scope["scope"]); request = json.loads(row["request"])
        session = row["session_id"]
        if (row["runtime"] != "opencode-http" or row["sdk_version"] != VERSION or not session or
                selected.get("profile_hash") != profile_hash or selected.get("subscription_binding_hash") != digest(frozen) or
                selected.get("input_hash") != row["request_hash"] or digest(request) != row["request_hash"] or
                request.get("task_binding", {}).get("run_id") != run_id or selected.get("cli_sha256") != self.profile.cli_sha256):
            raise Conflict("Native subscription execution/profile/session binding changed")
        if method != "GET" or path not in {"/session/" + session, "/session/" + session + "/message"}:
            raise Conflict("Native subscription operation is outside the exact recorded session read scope")
        return {"project_id": project_id, "run_id": run_id, "execution_id": execution_id, "session_id": session,
                "profile_hash": profile_hash, "native_mutation_allowed": False}

    def authorize_workspace_session_read(self, project_id, profile_hash, execution_id, method, path):
        """Conversation session scope can use an actual selected folder before a formal TaskRun exists."""
        self._bound(project_id, profile_hash)
        workspace = self._workspace_current()
        if not workspace or self.blocker or not self.attempt or self.attempt["status"] != "AUTHENTICATED_TRANSPORT_ONLY":
            raise Conflict("Selected workspace/subscription auth scope is absent or blocked")
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM agent_executions WHERE id=? AND project_id=?", (execution_id, project_id)).fetchone()
            record = connection.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None: raise NotFound("Native conversation session is outside the selected project")
        if not record or digest(json.loads(record["scope"])) != record["scope_hash"]: raise Conflict("Native conversation scope changed")
        scope = json.loads(record["scope"]); request = json.loads(row["request"]); session = row["session_id"]
        budget = scope.get("budget", {})
        if (row["runtime"] != "opencode-http" or row["sdk_version"] != VERSION or not session or
                digest(request) != row["request_hash"] or scope.get("input_hash") != row["request_hash"] or
                scope.get("profile_hash") != profile_hash or scope.get("cli_sha256") != self.profile.cli_sha256 or
                scope.get("workspace_binding") != workspace or scope.get("auth_attempt_id") != self.attempt["id"] or
                type(budget.get("max_calls")) is not int or budget["max_calls"] < 1 or
                budget["max_calls"] != request.get("max_turns") or type(budget.get("timeout_seconds")) is not int or
                budget["timeout_seconds"] < 1 or budget["timeout_seconds"] != request.get("timeout_seconds")):
            raise Conflict("Native conversation workspace/profile/budget binding differs from its immutable scope")
        if method != "GET" or path not in {"/session/" + session, "/session/" + session + "/message"}:
            raise Conflict("Native conversation operation is outside the exact recorded session read scope")
        return {"project_id": project_id, "execution_id": execution_id, "session_id": session, "workspace_binding": workspace,
                "profile_hash": profile_hash, "native_mutation_allowed": False, "live_inference": "BLOCKED"}

    def record_usage_failure(self, project_id, profile_hash, http_status, body, request_id=None):
        self._bound(project_id, profile_hash)
        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = error.get("code") if isinstance(error, dict) else None
        if code not in {"subscription_sharing_user_not_eligible", "subscription_sharing_usage_limit_exceeded",
                        "subscription_sharing_usage_unavailable"}: code = None
        if http_status not in {401, 403, 429, 503} and code is None: raise ValueError("This is not a recognized subscription admission/usage failure")
        self.blocker = {"http_status": http_status, "code": code, "body_shape": sorted(body) if isinstance(body, dict) else type(body).__name__,
                        "body_sha256": digest(body), "request_id": request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", request_id) else None,
                        "api_key_fallback": False, "retry_automatically": False, "reset_time": "UNKNOWN"}
        return self.inspect(project_id, profile_hash)
