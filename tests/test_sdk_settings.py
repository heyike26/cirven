"""Original company settings fixtures with installed native SDK transport contracts."""
import asyncio
import json
import os
from unittest.mock import patch

import pytest

from packages.adapters.claude_sdk import LiveBlocked
from packages.adapters.claude_http_meter import ClaudeHTTPMeter
from packages.adapters.settings_profile import ROUTE_ENV, SettingsRouteError, resolve_route
from packages.domain.store import Conflict
from test_claude_sessions import NativeFactory, context


TOKEN = "original-company-fixture-bearer-secret"
MODEL = "original-company-deployment-041"


def settings(tmp_path, **extra):
    path = tmp_path / "批准的公司 settings.json"
    body = {"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.invalid/anthropic",
                    "ANTHROPIC_AUTH_TOKEN": TOKEN, "ANTHROPIC_DEFAULT_SONNET_MODEL": MODEL,
                    "HTTP_PROXY": "http://unapproved.example.invalid", "HOME": "/unapproved/home"},
            "model": "sonnet", "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "unapproved-hook"}]}]},
            "permissions": {"defaultMode": "bypassPermissions", "allow": ["Bash(*)"]},
            "mcpServers": {"unapproved": {"command": "unapproved-command"}}, "apiKeyHelper": "unapproved-helper", **extra}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def selected_environment(monkeypatch, path):
    for name in (*ROUTE_ENV, "EEAS_MODEL_ID", "EEAS_CLAUDE_SETTINGS_PATH", "EEAS_MODEL_PROFILE_ID", "EEAS_LIVE_MAX_COST_USD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EEAS_CLAUDE_SETTINGS_PATH", str(path))
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", "0.01")


def started_meters(monkeypatch):
    actual_start = ClaudeHTTPMeter.start
    started = []

    def record(meter):
        result = actual_start(meter)
        started.append(meter)
        return result

    monkeypatch.setattr(ClaudeHTTPMeter, "start", record)
    return started


def check_routed_credential(options, meter, upstream_token):
    upstream_url = "https://gateway.example.invalid/anthropic"
    assert meter.route.environment["ANTHROPIC_AUTH_TOKEN"] == upstream_token
    assert meter.route.environment["ANTHROPIC_BASE_URL"] == upstream_url
    assert options.env["ANTHROPIC_BASE_URL"] == meter.base_url
    assert meter.base_url.startswith("http://127.0.0.1:")
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == meter.key != upstream_token
    assert options.env["ANTHROPIC_API_KEY"] == ""


def test_selected_company_settings_reach_installed_native_options_only(client, tmp_path, monkeypatch):
    meters = started_meters(monkeypatch)
    selected_environment(monkeypatch, settings(tmp_path))
    pid, bridge, request = context(client, tmp_path)
    assert bridge.preflight(False) == []
    factory = NativeFactory([0.002])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        row = asyncio.run(bridge.submit(pid, "company-settings-original", request))
    assert row["execution_status"] == "RETURNED" and len(factory.transports) == 1
    options = factory.transports[0].options
    assert options.model == MODEL and options.max_budget_usd == 0.01
    assert len(meters) == 1
    check_routed_credential(options, meters[0], TOKEN)
    assert options.settings is None and options.setting_sources == []
    assert options.tools == [] and options.permission_mode == "default" and options.mcp_servers == {} and options.skills == []
    assert options.env.get("HTTP_PROXY", "") == "" and options.env["HOME"] != "/unapproved/home"
    assert bridge.describe_capabilities()["route_configuration"]["raw_settings_forwarded"] is False
    assert not bridge.export_result(pid, row["id"])["live_evidence"]


def test_process_overrides_and_clear_values_have_public_provenance(tmp_path):
    path = settings(tmp_path)
    profile = resolve_route({"EEAS_CLAUDE_SETTINGS_PATH": str(path), "ANTHROPIC_AUTH_TOKEN": "",
                             "ANTHROPIC_API_KEY": "original-process-key", "EEAS_MODEL_ID": "original-explicit-id",
                             "EEAS_LIVE_MAX_COST_USD": "0.03"})
    assert profile.model_id == "original-explicit-id" and profile.budget_usd == "0.03"
    assert profile.environment["ANTHROPIC_AUTH_TOKEN"] == ""
    assert profile.public()["sources"]["ANTHROPIC_API_KEY"] == "PROCESS_ENVIRONMENT"
    assert profile.public()["sources"]["ANTHROPIC_BASE_URL"] == "SELECTED_SETTINGS_ENV"
    serialized = json.dumps(profile.public()) + repr(profile)
    assert TOKEN not in serialized and "original-process-key" not in serialized and str(path) not in serialized


@pytest.mark.parametrize("body", ['{"env":{"ANTHROPIC_AUTH_TOKEN":"one","ANTHROPIC_AUTH_TOKEN":"two"}}',
                                  '{"env":{"ANTHROPIC_AUTH_TOKEN":NaN}}', '{"env":[]}',
                                  '{"env":{"ANTHROPIC_AUTH_TOKEN":4}}', '{"model":4}', '{not json'])
def test_invalid_settings_emit_fixed_errors_without_raw_contents(tmp_path, body):
    path = tmp_path / "approved.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(SettingsRouteError) as caught:
        resolve_route({"EEAS_CLAUDE_SETTINGS_PATH": str(path)})
    assert body not in str(caught.value) and str(path) not in str(caught.value)


@pytest.mark.parametrize("kind", ["missing", "oversize", "symlink", "login_cache", "relative"])
def test_only_explicit_bounded_regular_settings_are_read(tmp_path, kind):
    path = tmp_path / "selected.json"
    if kind == "oversize": path.write_bytes(b"x" * (256 * 1024 + 1))
    if kind == "symlink": path.symlink_to(settings(tmp_path))
    if kind == "login_cache":
        path = tmp_path / ".credentials.json"
        path.write_text('{"env":{"ANTHROPIC_AUTH_TOKEN":"personal-login-cache"}}')
    if kind == "relative": path = __import__("pathlib").Path(".claude/settings.json")
    with pytest.raises(SettingsRouteError): resolve_route({"EEAS_CLAUDE_SETTINGS_PATH": str(path)})


@pytest.mark.parametrize("model", [None, "sonnet", "default", "deepseek 4.1flash"])
def test_model_label_or_unmapped_alias_and_settings_budget_do_not_authorize(tmp_path, model):
    body = {"env": {"ANTHROPIC_AUTH_TOKEN": TOKEN, "EEAS_LIVE_MAX_COST_USD": "1"}}
    if model is not None: body["model"] = model
    profile = resolve_route({"EEAS_CLAUDE_SETTINGS_PATH": str(settings(tmp_path, **body))})
    assert profile.model_id is None and profile.budget_usd is None


def test_no_automatic_personal_settings_discovery(tmp_path, monkeypatch):
    home = tmp_path / "personal-home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/settings.json").write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "personal-cache-token"}, "model": MODEL}))
    monkeypatch.setenv("HOME", str(home))
    profile = resolve_route({})
    assert profile.environment == {} and profile.model_id is None and not profile.public()["settings_selected"]


def test_selected_settings_cannot_authorize_code_tools(client, tmp_path, monkeypatch):
    selected_environment(monkeypatch, settings(tmp_path))
    pid, bridge, request = context(client, tmp_path, needs_tools=True)
    assert bridge.preflight(True) == ["OS_ISOLATION_NOT_QUALIFIED"]
    with pytest.raises(LiveBlocked, match="OS_ISOLATION_NOT_QUALIFIED"):
        asyncio.run(bridge.submit(pid, "settings-tools-blocked", request))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


def test_selected_company_settings_missing_endpoint_never_falls_back(client, tmp_path, monkeypatch):
    path = settings(tmp_path, env={"ANTHROPIC_AUTH_TOKEN": TOKEN, "ANTHROPIC_MODEL": MODEL})
    selected_environment(monkeypatch, path)
    pid, bridge, request = context(client, tmp_path)
    assert bridge.preflight(False) == ["COMPANY_ENDPOINT_NOT_CONFIGURED"]
    with pytest.raises(LiveBlocked, match="COMPANY_ENDPOINT_NOT_CONFIGURED"):
        asyncio.run(bridge.submit(pid, "company-no-fallback", request))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


def test_route_rotation_blocks_continuation_and_never_rewrites_parent(client, tmp_path, monkeypatch):
    path = settings(tmp_path)
    selected_environment(monkeypatch, path)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "original-company-parent", request))
        old_events = bridge.stream_events(pid, parent["id"])
        body = json.loads(path.read_bytes())
        body["env"]["ANTHROPIC_AUTH_TOKEN"] = "original-rotated-company-secret"
        path.write_text(json.dumps(body), encoding="utf-8")
        with pytest.raises(Conflict, match="scope changed"):
            asyncio.run(bridge.resume(pid, parent["id"], "company-rotated-resume", "new question", expected_session_id=parent["session_id"]))
    assert len(factory.transports) == 1 and bridge.inspect(pid, parent["id"]) == parent
    assert bridge.stream_events(pid, parent["id"]) == old_events


def test_settings_secret_is_redacted_even_after_selected_file_changes(client, tmp_path, monkeypatch):
    path = settings(tmp_path)
    selected_environment(monkeypatch, path)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        row = asyncio.run(bridge.submit(pid, "company-redaction-original", request))
    path.unlink()
    bridge._event(row["id"], {"type": "original-simulated-native-log", "text": "Bearer " + TOKEN,
                             "nested": {TOKEN: TOKEN}, "invalid_usage": {TOKEN: float("nan")}})
    exported = json.dumps(bridge.export_result(pid, row["id"])) + json.dumps(bridge.stream_events(pid, row["id"]))
    with bridge.service.connect() as connection:
        durable = " ".join(row[0] for row in connection.execute("SELECT payload FROM agent_events"))
    assert TOKEN not in exported + durable and "[REDACTED]" in exported + durable


def test_production_options_reject_route_drift_before_client_creation(client, tmp_path, monkeypatch):
    path = settings(tmp_path)
    selected_environment(monkeypatch, path)
    pid, bridge, request = context(client, tmp_path)
    workspace = tmp_path / "original-native-workspace"
    workspace.mkdir()
    row, _ = bridge._record(pid, "native-before-options", request, workspace, "claude-agent-sdk")
    body = json.loads(path.read_bytes())
    body["env"]["ANTHROPIC_BASE_URL"] = "https://other.example.invalid"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(Conflict, match="route changed"):
        bridge.options(workspace, request, row["id"])
    assert bridge.inspect(pid, row["id"])["execution_status"] == "QUEUED"
