"""Native max_budget_usd is a float; positive Decimal must not become zero."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
import pytest

from packages.adapters.claude_sdk import LiveBlocked
from packages.adapters.settings_profile import resolve_route
from test_claude_sessions import FIXTURE_ENV, NativeFactory, context


@pytest.mark.parametrize("amount", ["1e-10000", "1e-9999"])
def test_decimal_underflow_is_blocked_before_any_native_execution(client, tmp_path, monkeypatch, amount):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", amount)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    assert Decimal(amount) > 0 and ClaudeAgentOptions(max_budget_usd=float(Decimal(amount))).max_budget_usd == 0.0
    pid, bridge, request = context(client, tmp_path)
    assert bridge.preflight(False) == ["LIVE_BUDGET_NOT_CONFIGURED"]
    with pytest.raises(LiveBlocked, match="LIVE_BUDGET_NOT_CONFIGURED"):
        asyncio.run(bridge.submit(pid, "never-zero-native-budget", request))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


@pytest.mark.parametrize("amount", ["0.01", "1e-308", "100"])
def test_valid_finite_native_float_budget_preserves_original_decimal(amount):
    profile = resolve_route({"EEAS_LIVE_MAX_COST_USD": amount})
    assert Decimal(profile.budget_usd) == Decimal(amount)
    assert 0 < float(profile.budget_usd) <= 100


def test_positive_decimal_remainder_cannot_reset_native_budget_to_zero(client, tmp_path, monkeypatch):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", "6e-324")
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([5e-324, 5e-324])
    assert float(Decimal("6e-324") - Decimal("5e-324")) == 0.0
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "native-subnormal-root", request))
        assert parent["execution_status"] == "RETURNED" and factory.transports[0].options.max_budget_usd > 0
        with pytest.raises(LiveBlocked, match="SESSION_BUDGET_NOT_REPRESENTABLE"):
            asyncio.run(bridge.resume(pid, parent["id"], "never-zero-remainder", "original followup", expected_session_id=parent["session_id"]))
    assert len(factory.transports) == 1
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 1


def cli_budget(options):
    bundled = SubprocessCLITransport(prompt="", options=options)._find_bundled_cli()
    assert bundled is not None
    command = SubprocessCLITransport(prompt="", options=replace(options, cli_path=bundled))._build_command()
    return Decimal(command[command.index("--max-budget-usd") + 1])


@pytest.mark.parametrize("amount", ["8e-324", "0.01", "0.001", "0.000001", "0.0099999999999999999999999999999"])
def test_actual_native_cli_budget_text_never_rounds_up_authorization(client, tmp_path, monkeypatch, amount):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", amount)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.0])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "native-cli-budget-text", request))
    assert parent["execution_status"] == "RETURNED"
    approved = Decimal(amount)
    assert 0 < cli_budget(factory.transports[0].options) <= approved
    if amount in {"0.01", "0.001", "0.000001"}: assert cli_budget(factory.transports[0].options) == approved
    with bridge.service.connect() as connection:
        assert Decimal(connection.execute("SELECT root_budget_usd FROM agent_scopes").fetchone()[0]) == approved


def test_rounding_down_to_zero_is_blocked_before_root_call(client, tmp_path, monkeypatch):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", "4e-324")
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    assert bridge.preflight(False) == ["LIVE_BUDGET_NOT_CONFIGURED"]
    with pytest.raises(LiveBlocked): asyncio.run(bridge.submit(pid, "never-round-up-to-minimum", request))


def test_resume_native_cli_text_never_enlarges_exact_remaining_decimal(client, tmp_path, monkeypatch):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", "0.03")
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    cost = 0.012345678901234567
    factory = NativeFactory([cost, 0.013])
    remaining = Decimal("0.03") - Decimal(str(cost))
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "native-exact-parent", request))
        child = asyncio.run(bridge.resume(pid, parent["id"], "native-exact-remainder", "Original continuation", expected_session_id=parent["session_id"]))
    assert child["execution_status"] == "RETURNED"
    assert 0 < cli_budget(factory.transports[1].options) <= remaining
    with bridge.service.connect() as connection:
        assert Decimal(connection.execute("SELECT call_budget_usd FROM agent_scopes WHERE execution_id=?", (child["id"],)).fetchone()[0]) == remaining
