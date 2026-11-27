"""Original native-OAuth HTTP wire + real Task/SQLite guards, not real Pro qualification."""
import asyncio
import importlib.util
import os
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
import sqlite3
from urllib.parse import urlencode

import httpx
import pytest

from conftest import ROOT
from packages.adapters.opencode import OpenCodeBridge
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_subscription import (
    NATIVE_METHODS, PLUGIN_SHA256, NativeAuthTransport, OpenCodeSubscriptionAuth,
    SubscriptionProfile, prepare_owned_auth_home,
)
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest
from test_documents import project
from test_tasks import inputs


def profile_value(pid):
    return {"schema": "eeas.opencode-codex-subscription.v1", "version": VERSION, "source_commit": COMMIT,
            "plugin_sha256": PLUGIN_SHA256, "provider": "openai", "auth_mode": "native_codex_oauth",
            "project_id": pid, "profile_id": "original-subscription-profile", "revision": 1,
            "model_id": "gpt-6-sol", "owner_id": "original-native-owner", "secret_ref": "native-auth:original-owned-home",
            "cli_sha256": CLI_SHA["posix"], "login_method": "browser"}


@pytest.fixture
def task_scene(client):
    pid = project(client, "原创订阅授权范围")
    other = project(client, "原创另一项目")
    doc, req, body = inputs(client, pid)
    response = client.post(f"/api/projects/{pid}/tasks", json=body); assert response.status_code == 201, response.text
    task = response.json()
    response = client.post(f"/api/projects/{pid}/tasks/{task['id']}/runs", json={"revision": task["revision"], "operation_id": "original-subscription-parent"})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["execution_status"] == "QUEUED" and run["verification_status"] == "NOT_RUN"
    return {"pid": pid, "other": other, "doc": doc, "req": req, "task": task, "run": run,
            "service": client.app.state.store, "body": body, "client": client}


class OriginalOAuthWire:
    """No OAuth token endpoint or inference is called. This is only a native-API transport fixture."""
    def __init__(self):
        self.requests = []; self.shifted = False; self.callback_reply = True; self.authorize_status = 200
        self.delay = False; self.login_url = None

    async def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.requests.append({"method": request.method, "path": request.url.path, "body": body,
                              "directory": request.url.params.get("directory")})
        if self.delay: await asyncio.sleep(.01)
        if request.method == "GET" and request.url.path == "/provider/auth":
            methods = [{"type": "oauth", "label": NATIVE_METHODS["browser"]},
                       {"type": "oauth", "label": NATIVE_METHODS["headless"]},
                       {"type": "api", "label": "Manually enter API Key"}]
            if self.shifted: methods.reverse()
            return httpx.Response(200, json={"openai": methods})
        if request.url.path.endswith("/oauth/authorize"):
            url = self.login_url or "https://auth.openai.com/oauth/authorize?" + urlencode({
                "client_id": "app_EMoamEEZ73f0CkXaXp7hrann", "state": "S" * 43,
                "code_challenge": "C" * 43, "code_challenge_method": "S256", "response_type": "code",
                "redirect_uri": "http://localhost:1455/auth/callback", "scope": "openid profile email offline_access"})
            return httpx.Response(self.authorize_status, json={"url": url, "method": "auto", "instructions": "Complete original mock authorization"})
        if request.url.path.endswith("/oauth/callback"): return httpx.Response(200, json=self.callback_reply)
        if request.url.path == "/provider": return httpx.Response(200, json={"all": [], "default": {}, "connected": ["openai"]})
        raise AssertionError("Unexpected native mutation or model route: " + request.method + " " + request.url.path)


async def coordinator(scene, wire=None, process=None):
    selected = SubscriptionProfile.parse(profile_value(scene["pid"]))
    wire = wire or OriginalOAuthWire()
    client = httpx.AsyncClient(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(wire),
                               trust_env=False, follow_redirects=False, timeout=1)
    transport = NativeAuthTransport(client, selected, process)
    return OpenCodeSubscriptionAuth(scene["service"], selected, transport), wire, client


async def connected(scene):
    auth, wire, client = await coordinator(scene)
    pending = await auth.begin(scene["pid"], auth.profile.fingerprint)
    result = await auth.complete(scene["pid"], auth.profile.fingerprint, pending["attempt_id"])
    assert result["status"] == "AUTHENTICATED_TRANSPORT_ONLY"
    return auth, wire, client


def test_original_captured_openapi_native_provider_auth_contract():
    path = ROOT / "reports/E04-opencode-headless/20261013/empty-home-cold-config-first/response-06.bin"
    raw = path.read_bytes(); api = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == "04b16616693498e2b92af87408d9df3ea4ce60176d8416d36dc6c5bcf8d3bfed"
    assert api["openapi"] == "3.1.0"
    for path, method, operation in [("/provider/auth", "get", "provider.auth"),
                                   ("/provider/{providerID}/oauth/authorize", "post", "provider.oauth.authorize"),
                                   ("/provider/{providerID}/oauth/callback", "post", "provider.oauth.callback")]:
        assert api["paths"][path][method]["operationId"] == operation
    assert api["paths"]["/provider/{providerID}/oauth/callback"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["type"] == "boolean"


@pytest.mark.parametrize("change", ["api_key", "base_url", "provider", "auth_mode", "cli", "plugin", "secret", "model", "revision", "method"])
def test_subscription_profile_never_accepts_api_key_fallback_or_unknown_identity(change):
    value = profile_value("original-project")
    if change in {"api_key", "base_url"}: value[change] = "original-rejected-value"
    else:
        key = {"cli": "cli_sha256", "plugin": "plugin_sha256", "secret": "secret_ref", "model": "model_id", "method": "login_method"}.get(change, change)
        value[key] = "gpt-5.5-pro" if change == "model" else 0 if change == "revision" else "original-rejected-value"
    with pytest.raises(ValueError): SubscriptionProfile.parse(value)


def test_fresh_native_home_uses_original_config_and_does_not_inherit_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "original-ambient-key-must-not-be-copied")
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "original-ambient-auth-must-not-be-copied")
    profile = SubscriptionProfile.parse(profile_value("original-project"))
    owned = prepare_owned_auth_home(tmp_path / "owned", profile, "original-native-private-basic-password")
    config = json.loads(owned["config"].read_text()); env = owned["environment"]
    assert config == profile.native_config() and config["permission"] == {"*": "deny"}
    assert config["enabled_providers"] == ["openai"] and "provider" not in config
    assert "OPENAI_API_KEY" not in env and "OPENCODE_AUTH_CONTENT" not in env
    assert env["HOME"] == str(owned["home"]) and env["XDG_DATA_HOME"] == str(owned["home"] / "data")
    assert not list(owned["home"].rglob("auth.json"))
    with pytest.raises(FileExistsError): prepare_owned_auth_home(tmp_path / "owned", profile, "another-password")


def test_native_method_and_callback_transport_are_real_requests_but_not_pro_qualification(task_scene):
    async def check():
        auth, wire, client = await connected(task_scene)
        try:
            report = auth.inspect(task_scene["pid"], auth.profile.fingerprint)
            assert report["authentication_verified"] is False and report["subscription_eligibility"] == "NOT_VALIDATED"
            assert report["live_inference"] == "BLOCKED" and report["api_key_fallback"] is False
            assert [item["path"] for item in wire.requests] == ["/provider/auth", "/provider/openai/oauth/authorize", "/provider/auth", "/provider/openai/oauth/callback", "/provider"]
            assert [item["body"] for item in wire.requests if item["method"] == "POST"] == [{"method": 0}, {"method": 0}]
            with pytest.raises(Conflict): await auth.complete(task_scene["pid"], auth.profile.fingerprint, report["attempt_id"])
            assert len([item for item in wire.requests if item["path"].endswith("/callback")]) == 1
        finally: await client.aclose()
    asyncio.run(check())


def test_concurrent_login_and_callback_do_not_duplicate_native_mutations(task_scene):
    async def check():
        wire = OriginalOAuthWire(); wire.delay = True
        auth, _, client = await coordinator(task_scene, wire)
        try:
            starts = await asyncio.gather(*(auth.begin(task_scene["pid"], auth.profile.fingerprint) for _ in range(2)), return_exceptions=True)
            assert sum(isinstance(item, Conflict) for item in starts) == 1
            admitted = next(item for item in starts if isinstance(item, dict))
            replies = await asyncio.gather(*(auth.complete(task_scene["pid"], auth.profile.fingerprint, admitted["attempt_id"]) for _ in range(2)), return_exceptions=True)
            assert sum(isinstance(item, Conflict) for item in replies) == 1
            assert sum(item["path"].endswith("/authorize") for item in wire.requests) == 1
            assert sum(item["path"].endswith("/callback") for item in wire.requests) == 1
        finally: await client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize("fault", ["methods-shifted", "callback-false", "external-login", "native-429"])
def test_native_auth_failure_is_retained_without_retry_or_api_fallback(task_scene, fault):
    async def check():
        auth, wire, client = await coordinator(task_scene)
        try:
            if fault == "external-login": wire.login_url = "https://external.invalid/oauth/authorize"
            if fault == "native-429": wire.authorize_status = 429
            if fault in {"external-login", "native-429"}:
                with pytest.raises(Conflict): await auth.begin(task_scene["pid"], auth.profile.fingerprint)
            else:
                pending = await auth.begin(task_scene["pid"], auth.profile.fingerprint)
                wire.shifted = fault == "methods-shifted"; wire.callback_reply = fault != "callback-false"
                with pytest.raises(Conflict): await auth.complete(task_scene["pid"], auth.profile.fingerprint, pending["attempt_id"])
            assert auth.attempt["status"] == "UNKNOWN_OUTCOME"
            with pytest.raises(Conflict): await auth.begin(task_scene["pid"], auth.profile.fingerprint)
            assert all(item["body"] is None or item["body"] == {"method": 0} for item in wire.requests)
        finally: await client.aclose()
    asyncio.run(check())


def test_cancel_without_actual_process_confirmation_stays_requested(task_scene):
    async def check():
        auth, wire, client = await coordinator(task_scene)
        try:
            pending = await auth.begin(task_scene["pid"], auth.profile.fingerprint)
            result = await auth.cancel(task_scene["pid"], auth.profile.fingerprint, pending["attempt_id"])
            assert result["status"] == "CANCEL_REQUESTED" and not auth.transport.closed
            with pytest.raises(Conflict): await auth.complete(task_scene["pid"], auth.profile.fingerprint, pending["attempt_id"])
            assert not any(item["path"].endswith("/callback") for item in wire.requests)
        finally: await client.aclose()
    asyncio.run(check())


def test_expired_login_stops_its_actual_owned_original_python_process(task_scene):
    async def check():
        # An actual harmless owned-process lifecycle counter. It is not a native OpenCode OAuth qualification.
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")
        auth, wire, client = await coordinator(task_scene, process=process)
        try:
            pending = await auth.begin(task_scene["pid"], auth.profile.fingerprint)
            auth.attempt["deadline"] = time.monotonic() - 1
            with pytest.raises(Conflict, match="expired"): await auth.complete(task_scene["pid"], auth.profile.fingerprint, pending["attempt_id"])
            assert auth.attempt["status"] == "EXPIRED" and process.returncode is not None and auth.transport.closed
            assert not any(item["path"].endswith("/callback") for item in wire.requests)
        finally:
            if process.returncode is None: process.terminate(); await process.wait()
            await client.aclose()
    asyncio.run(check())


def test_each_run_freezes_real_task_profile_budget_and_refuses_scope_drift(task_scene):
    async def check():
        auth, wire, client = await connected(task_scene)
        try:
            pid, rid, ph = task_scene["pid"], task_scene["run"]["id"], auth.profile.fingerprint
            grant = auth.freeze_run(pid, ph, rid, max_calls=1, timeout_seconds=10)
            assert grant["model_id"] == auth.profile.model_id and grant["live_inference"] == "BLOCKED"
            assert auth.freeze_run(pid, ph, rid, max_calls=1, timeout_seconds=10) == grant
            with pytest.raises(Conflict): auth.freeze_run(pid, ph, rid, max_calls=2, timeout_seconds=10)
            with pytest.raises(Conflict): auth.freeze_run(task_scene["other"], ph, rid, max_calls=1, timeout_seconds=10)
            with pytest.raises(Conflict): auth.freeze_run(pid, "0" * 64, rid, max_calls=1, timeout_seconds=10)
            budget = task_scene["task"]["spec"]["budget"]
            with pytest.raises(Conflict): auth.freeze_run(pid, ph, rid, max_calls=budget["max_steps"] + 1, timeout_seconds=10)
            response = task_scene["client"].put(f"/api/projects/{pid}/tasks/{task_scene['task']['id']}?expected_revision=1", json={**task_scene["body"], "goal": "original changed goal"})
            assert response.status_code == 200
            with pytest.raises(Conflict, match="stale"): auth.freeze_run(pid, ph, rid, max_calls=1, timeout_seconds=10)
            assert not any("prompt" in item["path"] or "responses" in item["path"] for item in wire.requests)
        finally: await client.aclose()
    asyncio.run(check())


def test_exact_recorded_sqlite_session_and_scope_are_required(task_scene, tmp_path):
    async def check():
        auth, wire, client = await connected(task_scene)
        try:
            pid, rid, ph = task_scene["pid"], task_scene["run"]["id"], auth.profile.fingerprint
            grant = auth.freeze_run(pid, ph, rid, max_calls=1, timeout_seconds=10)
            frozen = {key: value for key, value in grant.items() if key not in {"binding_hash", "live_inference"}}
            request = {"task_binding": {"run_id": rid}, "original_sqlite_guard_fixture": True}
            scope = {"profile_hash": ph, "subscription_binding_hash": digest(frozen), "input_hash": digest(request), "cli_sha256": auth.profile.cli_sha256}
            bridge = OpenCodeBridge(task_scene["service"], tmp_path)
            row, created = bridge._record(pid, "original-subscription-session-guard", request, scope, tmp_path / "not-a-native-execution")
            assert created
            # This literal is a guard fixture only; no original native session is claimed or qualified.
            session = "original-guard-session-not-native"
            with task_scene["service"].connect() as db: db.execute("UPDATE agent_executions SET session_id=? WHERE id=?", (session, row["id"]))
            assert auth.authorize_session_read(pid, ph, rid, row["id"], "GET", "/session/" + session)["native_mutation_allowed"] is False
            for method, path in [("POST", "/session/" + session + "/prompt_async"), ("GET", "/session/other"), ("GET", "/config"), ("POST", "/permission/fixture/reply")]:
                with pytest.raises(Conflict): auth.authorize_session_read(pid, ph, rid, row["id"], method, path)
            with task_scene["service"].connect() as db, pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute("UPDATE agent_scopes SET scope_hash=? WHERE execution_id=?", ("0" * 64, row["id"]))
            assert auth.authorize_session_read(pid, ph, rid, row["id"], "GET", "/session/" + session)["native_mutation_allowed"] is False
        finally: await client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize("status,body", [(403, {"error": {"code": "subscription_sharing_user_not_eligible"}}),
                                       (429, {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}),
                                       (503, {"error": {"code": "subscription_sharing_usage_unavailable"}}),
                                       (401, {"detail": "original admission denied"})])
def test_usage_denial_stops_new_grants_with_unknown_reset_and_no_key_fallback(task_scene, status, body):
    async def check():
        auth, wire, client = await connected(task_scene)
        try:
            pid, ph = task_scene["pid"], auth.profile.fingerprint
            result = auth.record_usage_failure(pid, ph, status, body, "original-request-id")
            assert result["blocker"]["http_status"] == status and result["blocker"]["reset_time"] == "UNKNOWN"
            assert result["blocker"]["api_key_fallback"] is False and result["blocker"]["retry_automatically"] is False
            with pytest.raises(Conflict): auth.freeze_run(pid, ph, task_scene["run"]["id"], max_calls=1, timeout_seconds=10)
            assert not any(item["path"].startswith("/auth/") or "responses" in item["path"] for item in wire.requests)
        finally: await client.aclose()
    asyncio.run(check())


def test_native_oauth_transport_rejects_external_origin_redirects_and_non_auth_api():
    async def check():
        profile = SubscriptionProfile.parse(profile_value("original-project"))
        for origin, redirects in [("https://external.invalid", False), ("http://127.0.0.1:9", True)]:
            async with httpx.AsyncClient(base_url=origin, transport=httpx.MockTransport(OriginalOAuthWire()), follow_redirects=redirects) as client:
                with pytest.raises(ValueError): NativeAuthTransport(client, profile)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(OriginalOAuthWire()), follow_redirects=False) as client:
            transport = NativeAuthTransport(client, profile)
            for method, path in [("PUT", "/auth/openai"), ("POST", "/session"), ("GET", "/config"), ("POST", "/permission/fixture/reply")]:
                with pytest.raises(Conflict): await transport.request(method, path, {"apiKey": "original-forbidden-fixture"})
    asyncio.run(check())


def test_native_method_index_is_selected_by_actual_oauth_label_not_hardcoded(task_scene):
    async def check():
        wire = OriginalOAuthWire(); wire.shifted = True
        auth, _, client = await coordinator(task_scene, wire)
        try:
            pending = await auth.begin(task_scene["pid"], auth.profile.fingerprint)
            result = await auth.complete(task_scene["pid"], auth.profile.fingerprint, pending["attempt_id"])
            assert result["status"] == "AUTHENTICATED_TRANSPORT_ONLY"
            assert [request["body"] for request in wire.requests if request["method"] == "POST"] == [{"method": 2}, {"method": 2}]
        finally: await client.aclose()
    asyncio.run(check())


def test_original_headless_method_is_delegated_to_native_provider(task_scene):
    async def check():
        wire = OriginalOAuthWire(); wire.login_url = "https://auth.openai.com/codex/device"
        profile = SubscriptionProfile.parse({**profile_value(task_scene["pid"]), "login_method": "headless"})
        client = httpx.AsyncClient(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(wire), follow_redirects=False)
        auth = OpenCodeSubscriptionAuth(task_scene["service"], profile, NativeAuthTransport(client, profile))
        try:
            pending = await auth.begin(task_scene["pid"], profile.fingerprint)
            assert pending["login_url"] == "https://auth.openai.com/codex/device"
            result = await auth.complete(task_scene["pid"], profile.fingerprint, pending["attempt_id"])
            assert result["status"] == "AUTHENTICATED_TRANSPORT_ONLY"
            assert [request["body"] for request in wire.requests if request["method"] == "POST"] == [{"method": 1}, {"method": 1}]
        finally: await client.aclose()
    asyncio.run(check())


@pytest.fixture
def actual_workspace_authority(task_scene, tmp_path, tmp_path_factory):
    """Use the sibling author's actual LocalWorkspaces implementation, never an invented folder graph."""
    selected = ROOT / "packages/domain/local_workspaces.py"
    if not selected.is_file():
        value = os.environ.get("EEAS_LOCAL_WORKSPACE_AUTHORITY_SOURCE")
        assert value, "Explicit actual LocalWorkspaces source is required until its slice is integrated"
        selected = Path(value)
    assert selected.is_absolute() and selected.is_file()
    raw = selected.read_bytes()
    name = "original_local_workspace_authority_for_subscription_tests"
    spec = importlib.util.spec_from_file_location(name, selected)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    folder = tmp_path_factory.mktemp("original-external-workspace") / "原创实际目录 空格"
    folder.mkdir(); (folder / "original-source.txt").write_text("Original selected workspace bytes\n")
    authority = module.LocalWorkspaces(task_scene["service"], tmp_path)
    binding = authority.bind(task_scene["pid"], module.WorkspaceInput(idempotency_key="original-auth-folder", root=str(folder), allowed_write_paths=[]))
    assert binding["root"] == str(folder) and binding["allowed_write_paths"] == []
    yield authority, binding, folder
    assert selected.read_bytes() == raw, "Sibling workspace authority source changed during auth test"


def test_actual_selected_external_folder_is_native_auth_directory_and_keeps_private_home(task_scene, actual_workspace_authority, tmp_path):
    async def check():
        authority, binding, folder = actual_workspace_authority
        auth, wire, client = await coordinator(task_scene)
        auth.workspaces = authority
        try:
            bound = auth.bind_workspace(task_scene["pid"], auth.profile.fingerprint, binding["id"])
            assert bound["workspace_root"] == str(folder) and bound["workspace_binding_sha256"] == binding["binding_sha256"]
            owned = prepare_owned_auth_home(tmp_path / "native-auth-home", auth.profile, "original-owned-private-password", workspaces=authority, workspace_id=binding["id"])
            assert owned["project"] == folder and owned["home"] != folder
            assert (folder / "original-source.txt").read_text() == "Original selected workspace bytes\n"
            pending = await auth.begin(task_scene["pid"], auth.profile.fingerprint)
            await auth.complete(task_scene["pid"], auth.profile.fingerprint, pending["attempt_id"])
            assert all(request["directory"] == str(folder) for request in wire.requests)
            grant = auth.freeze_run(task_scene["pid"], auth.profile.fingerprint, task_scene["run"]["id"], max_calls=1, timeout_seconds=10)
            assert grant["workspace_binding"] == bound
            with pytest.raises(Conflict): auth.bind_workspace(task_scene["pid"], auth.profile.fingerprint, binding["id"])
        finally: await client.aclose()
    asyncio.run(check())


def test_actual_workspace_conversation_session_scope_needs_no_internal_task_binding(task_scene, actual_workspace_authority, tmp_path):
    async def check():
        authority, binding, folder = actual_workspace_authority
        auth, wire, client = await coordinator(task_scene); auth.workspaces = authority
        try:
            pid, ph = task_scene["pid"], auth.profile.fingerprint
            selected = auth.bind_workspace(pid, ph, binding["id"])
            pending = await auth.begin(pid, ph); await auth.complete(pid, ph, pending["attempt_id"])
            request = {"max_turns": 1, "timeout_seconds": 10, "original_conversation_scope_guard_fixture": True}
            scope = {"input_hash": digest(request), "profile_hash": ph, "cli_sha256": auth.profile.cli_sha256,
                     "workspace_binding": selected, "auth_attempt_id": pending["attempt_id"],
                     "budget": {"max_calls": 1, "timeout_seconds": 10}}
            bridge = OpenCodeBridge(task_scene["service"], tmp_path)
            row, _ = bridge._record(pid, "original-conversation-auth-scope", request, scope, tmp_path / "original-conversation-guard-only")
            session = "original-conversation-guard-not-actual-native"
            with task_scene["service"].connect() as db: db.execute("UPDATE agent_executions SET session_id=? WHERE id=?", (session, row["id"]))
            result = auth.authorize_workspace_session_read(pid, ph, row["id"], "GET", "/session/" + session + "/message")
            assert result["workspace_binding"] == selected and result["live_inference"] == "BLOCKED"
            assert "task_binding" not in request
            for method, path in [("GET", "/session/other"), ("POST", "/session/" + session + "/prompt_async"), ("GET", "/config")]:
                with pytest.raises(Conflict): auth.authorize_workspace_session_read(pid, ph, row["id"], method, path)
            with pytest.raises(Conflict): auth.authorize_workspace_session_read(task_scene["other"], ph, row["id"], "GET", "/session/" + session)
        finally: await client.aclose()
    asyncio.run(check())


def test_actual_folder_identity_drift_is_rejected_before_native_auth_requests(task_scene, actual_workspace_authority):
    async def check():
        authority, binding, folder = actual_workspace_authority
        auth, wire, client = await coordinator(task_scene); auth.workspaces = authority
        try:
            auth.bind_workspace(task_scene["pid"], auth.profile.fingerprint, binding["id"])
            with pytest.raises(NotFound): auth.bind_workspace(task_scene["pid"], auth.profile.fingerprint, "0" * 64)
            folder.rename(folder.with_name(folder.name + "-original-old")); folder.mkdir()
            with pytest.raises(Conflict, match="ROOT_DRIFT"): await auth.begin(task_scene["pid"], auth.profile.fingerprint)
            assert wire.requests == []
        finally: await client.aclose()
    asyncio.run(check())
