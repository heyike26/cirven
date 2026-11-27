"""Native subscription dispatch configuration and real original no-auth failure.

The no-auth test supplies a deliberately unconnected profile directly to this
transport boundary. It does not qualify account registration or real Pro access.
The original CLI, message store and session pipeline remain real.
"""
import asyncio
import json
import os
from types import SimpleNamespace

from packages.adapters.opencode_account import SubscriptionProviderProfile
from packages.adapters.opencode_profile import CLI_SHA
from packages.adapters.subscription_runtime import subscription_budget_summary
from packages.domain.tasks import canonical
from test_opencode_desktop_transport import CLI, OriginalDesktopWire, scene


def selected(tmp_path):
    account = tmp_path / "owned-account-native-data"
    account.mkdir()
    return SubscriptionProviderProfile("subscription-test", 1, "a" * 64, CLI,
        CLI_SHA[os.name], "gpt-5.4", {"id": "owned-account", "revision": 1, "sha256": "b" * 64}, account)


def test_native_config_preserves_mcp_permissions_and_uses_builtin_provider_without_api_relay(tmp_path):
    wire = OriginalDesktopWire().start()
    try:
        native, request, _store, _intents = scene(tmp_path, wire)
        profile = selected(tmp_path)
        broker = SimpleNamespace(url="http://127.0.0.1:1", key="own-mcp-key", row={"id": "owned", "request_hash": "c" * 64})
        config = native._config(profile, request, None, broker)
        assert config["model"] == "openai/gpt-5.4"
        assert config["enabled_providers"] == ["openai"]
        assert set(config["provider"]["openai"]) == {"models"}
        assert config["permission"]["*"] == "deny"
        assert config["permission"]["StructuredOutput"] == "allow"
        assert config["mcp"]["eeas_desktop"]["environment"]["EEAS_DESKTOP_BRIDGE_TOKEN"] == broker.key
        assert config["agent"]["eeas-desktop-task"]["steps"] == request["budget"]["max_calls"]
        assert "baseURL" not in canonical(config) and "apiKey" not in canonical(config)
        usage = subscription_budget_summary(request["budget"])
        assert usage["rpc_attempts"] is None and usage["hard_rpc_cap"] == "NOT_QUALIFIED"
        assert wire.requests == []
    finally:
        wire.close()


def test_actual_original_no_auth_error_has_no_company_api_fallback_or_native_tool_effect(tmp_path, monkeypatch):
    wire = OriginalDesktopWire().start()
    try:
        native, request, _store, _intents = scene(tmp_path, wire, mode="INTENT", methods=[])
        profile = selected(tmp_path)
        reference = profile.reference
        request["provider_profile_ref"] = reference
        request["budget"]["timeout_seconds"] = 60
        # This is a transport-boundary negative test, intentionally bypassing
        # account admission to prove that original unauthenticated inference
        # fails instead of finding the old API wire or an ambient API key.
        native.provider_registry = SimpleNamespace(resolve=lambda ref: profile if ref == reference else None)
        monkeypatch.setenv("OPENAI_API_KEY", "ambient-api-key-must-not-be-used")
        async def run():
            prepared = native.prepare(request)
            result = await native.start(prepared["project_id"], prepared["id"])
            assert native.active == {}
            return result
        result = asyncio.run(run())
        assert result["session_id"] and result["session_id"].startswith("ses_")
        assert result["execution_status"] == "FAILED", canonical(result)
        parsed = json.loads(result["result"])
        assert parsed["candidate_json"] is None and parsed["tool_calls"] == 0
        assert parsed["usage"]["rpc_attempts"] is None
        assert parsed["usage"]["hard_rpc_cap"] == "NOT_QUALIFIED"
        assert any(item["info"].get("error") for item in parsed["native_messages"])
        assert wire.requests == []
        assert not (profile.account_data_home / "opencode" / "auth.json").exists()
        destination = os.environ.get("EEAS_SUBSCRIPTION_DESKTOP_EVIDENCE")
        if destination:
            from pathlib import Path
            evidence = Path(destination); evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "no-auth-native-result.json").write_text(canonical({
                "status": result["execution_status"], "session_id": result["session_id"],
                "result": parsed, "company_wire_calls": len(wire.requests),
                "qualification": "REAL_ORIGINAL_CLI_NO_AUTH_NEGATIVE_ONLY", "pro_inference": "NOT_RUN"}))
    finally:
        wire.close()
