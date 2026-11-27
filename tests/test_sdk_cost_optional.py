"""Installed SDK's optional fee remains unknown without an extra empty event."""
import asyncio
import json
from unittest.mock import patch

import pytest

from packages.adapters.claude_sdk import LiveBlocked
from packages.domain.tasks import digest
from test_claude_sessions import FIXTURE_ENV, NativeFactory, context


def configured(monkeypatch):
    for name, value in FIXTURE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


@pytest.mark.parametrize("is_error", [False, True])
def test_optional_none_keeps_native_stream_null_cost_and_both_controls_blocked(client, tmp_path, monkeypatch, is_error):
    configured(monkeypatch)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([None], error=is_error)
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        row = asyncio.run(bridge.submit(pid, "optional-absent-fee", request))
        original = bridge.export_result(pid, row["id"])
        events = original["events"]
        assert [event["payload"]["type"] for event in events] == [
            "SystemMessage", "AssistantMessage", "ResultMessage", "session_checkpoint", "workspace_diff"]
        assert [event["event_seq"] for event in events] == [1, 2, 3, 4, 5]
        data = json.loads(row["result"])
        assert data["total_cost_usd"] is None and data["budget"]["reported_new_cost_usd"] is None
        assert data["budget"]["within_estimate_limit"] is None
        native_result = next(event["payload"]["native"] for event in events if event["payload"]["type"] == "ResultMessage")
        assert native_result["total_cost_usd"] is None
        for kind in ("resume", "fork"):
            with pytest.raises(LiveBlocked, match="SESSION_COST_UNKNOWN"):
                asyncio.run(getattr(bridge, kind)(pid, row["id"], "absent-fee-" + kind, "New original instruction",
                                                  expected_session_id=row["session_id"]))
        assert bridge.export_result(pid, row["id"]) == original
    assert row["execution_status"] == ("FAILED" if is_error else "RETURNED")
    assert not original["live_evidence"] and original["verification_status"] == "NOT_RUN"
    assert len(factory.transports) == 1 and factory.transports[0].closed
    with bridge.service.connect() as database:
        raw = json.loads(database.execute("SELECT result FROM agent_executions WHERE id=?", (row["id"],)).fetchone()[0])
        assert raw["total_cost_usd"] is None
        assert database.execute("SELECT count(*) FROM agent_controls").fetchone()[0] == 0


@pytest.mark.parametrize("invalid", ["1e-999999999", float("nan"), float("inf"), float("-inf"), -0.001])
def test_explicit_invalid_fee_preserves_diagnostic_hash_type_and_unknown_control_guard(client, tmp_path, monkeypatch, invalid):
    configured(monkeypatch)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([invalid])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        row = asyncio.run(bridge.submit(pid, "explicit-invalid-fee", request))
        exported = bridge.export_result(pid, row["id"])
        diagnostic = [event["payload"] for event in exported["events"] if event["payload"]["type"] == "sdk_cost_unknown"]
        assert len(diagnostic) == 1
        assert diagnostic[0]["native_value_type"] == type(invalid).__name__
        assert diagnostic[0]["native_value_sha256"] == digest(bridge._sanitize(invalid))
        assert diagnostic[0]["reported_cost_usd"] is None
        data = json.loads(row["result"])
        assert data["total_cost_usd"] is None and data["budget"]["within_estimate_limit"] is None
        json.dumps(exported, allow_nan=False)
        for kind in ("resume", "fork"):
            with pytest.raises(LiveBlocked, match="SESSION_COST_UNKNOWN"):
                asyncio.run(getattr(bridge, kind)(pid, row["id"], "invalid-fee-" + kind, "Original followup",
                                                  expected_session_id=row["session_id"]))
    assert len(factory.transports) == 1 and factory.transports[0].closed
