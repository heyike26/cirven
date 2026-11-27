"""Fraction oracle exposes Decimal context loss in real native session budgets."""
import asyncio
import json
from decimal import Decimal, localcontext
from fractions import Fraction
from unittest.mock import patch

import pytest

from packages.adapters import budget_math
from packages.adapters.claude_sdk import LiveBlocked
from test_claude_sessions import FIXTURE_ENV, NativeFactory, context
from test_sdk_budget_float import cli_budget


@pytest.mark.parametrize("kind", ["resume", "fork"])
@pytest.mark.parametrize("approved,cost,precision", [("0.0099999999999999999999999999999", 0.0, 28),
                                                     ("0.01", 5e-324, 28),
                                                     ("0.03", 0.012345678901234567, 6)])
def test_native_continuation_preserves_exact_authorized_remaining_with_fraction_oracle(client, tmp_path, monkeypatch,
                                                                                       kind, approved, cost, precision):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", approved)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    oracle = Fraction(Decimal(approved)) - Fraction(Decimal(str(cost)))
    assert oracle > 0
    factory = NativeFactory([cost, cost])
    with localcontext() as decimal_context:
        decimal_context.prec = precision
        with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
            parent = asyncio.run(bridge.submit(pid, "exact-decimal-root", request))
            before = bridge.stream_events(pid, parent["id"])
            child = asyncio.run(getattr(bridge, kind)(pid, parent["id"], "exact-decimal-followup", "Original exact continuation",
                                                   expected_session_id=parent["session_id"]))
    assert parent["execution_status"] == child["execution_status"] == "RETURNED"
    assert Fraction(cli_budget(factory.transports[1].options)) <= oracle
    with bridge.service.connect() as connection:
        frozen = dict(connection.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (child["id"],)).fetchone())
    assert Fraction(Decimal(frozen["root_budget_usd"])) == Fraction(Decimal(approved))
    assert Fraction(Decimal(frozen["call_budget_usd"])) == oracle
    assert Fraction(Decimal(frozen["prior_spent_usd"])) == Fraction(Decimal(str(cost)))
    assert bridge.inspect(pid, parent["id"]) == parent and bridge.stream_events(pid, parent["id"]) == before


def test_result_overspend_is_not_rounded_away_by_caller_decimal_context(client, tmp_path, monkeypatch):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    reported = 0.01000004
    factory = NativeFactory([reported])
    with localcontext() as decimal_context:
        decimal_context.prec = 6
        with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
            row = asyncio.run(bridge.submit(pid, "do-not-round-away-overspend", request))
    assert row["execution_status"] == "FAILED"
    budget = json.loads(row["result"])["budget"]
    assert budget["within_estimate_limit"] is False
    assert Fraction(Decimal(budget["reported_new_cost_usd"])) == Fraction(Decimal(str(reported)))


@pytest.mark.parametrize("left,right", [("0.01", "1e-999999999"), ("0.01", "1e999999999"), ("1e2000", "1e-3000")])
def test_sparse_untrusted_exponent_rejected_before_any_context_allocation(monkeypatch, left, right):
    allocated = []
    def no_context(*args, **kwargs):
        allocated.append(True)
        raise AssertionError("Must reject before numeric allocation")
    monkeypatch.setattr(budget_math, "Context", no_context)
    with pytest.raises(ValueError, match="SDK_DECIMAL_UNSUPPORTED"):
        budget_math.exact_difference(left, right)
    assert allocated == []


def test_unsupported_source_cost_remains_null_and_cannot_resume(client, tmp_path, monkeypatch):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory(["1e-999999999"])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        row = asyncio.run(bridge.submit(pid, "unsupported-sparse-native-cost", request))
        assert row["execution_status"] == "RETURNED"
        result = json.loads(row["result"])
        assert result["total_cost_usd"] is None and result["budget"]["reported_new_cost_usd"] is None
        with pytest.raises(LiveBlocked, match="SESSION_COST_UNKNOWN"):
            asyncio.run(bridge.resume(pid, row["id"], "no-sparse-cost-resume", "Original followup", expected_session_id=row["session_id"]))
    assert len(factory.transports) == 1


def test_unsupported_approved_precision_is_blocked_before_persistence(client, tmp_path, monkeypatch):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    # Bounded4KiB input boundary, never construct an enormous coefficient.
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", "0." + "9" * budget_math.MAX_DECIMAL_SPAN)
    pid, bridge, request = context(client, tmp_path)
    assert bridge.preflight(False) == ["LIVE_BUDGET_NOT_REPRESENTABLE"]
    with pytest.raises(LiveBlocked, match="LIVE_BUDGET_NOT_REPRESENTABLE"):
        asyncio.run(bridge.submit(pid, "unsupported-approved-precision", request))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


def test_normal_finite_float_extreme_cost_is_exact_budget_violation(client, tmp_path, monkeypatch):
    for name, value in FIXTURE_ENV.items(): monkeypatch.setenv(name, value)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([1e308])
    with localcontext() as decimal_context:
        decimal_context.prec = 6
        with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
            row = asyncio.run(bridge.submit(pid, "finite-huge-source-cost", request))
    result = json.loads(row["result"])
    assert row["execution_status"] == "FAILED" and result["budget"]["within_estimate_limit"] is False
    assert Fraction(Decimal(result["budget"]["reported_new_cost_usd"])) == Fraction(Decimal("1e308"))
