import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

from claude_agent_sdk import SystemMessage, AssistantMessage, TextBlock, ResultMessage
import pytest

from packages.adapters.claude_sdk import ClaudeBridge, LiveBlocked
from packages.domain.tasks import TaskService
from conftest import ROOT
from test_documents import project


@pytest.fixture(autouse=True)
def claude_bridge_runtime(monkeypatch):
    monkeypatch.setenv("EEAS_NATIVE_RUNTIME", "claude-agent-sdk")


class MockSDKTransportClient:
    """Only the external model transport is mocked; database and bridge are real."""
    def __init__(self, failure=False, stalled=False):
        self.failure, self.stalled = failure, stalled
        self.connected = False
        self.disconnected = False
        self.interrupted = False

    async def connect(self): self.connected = True
    async def query(self, prompt): self.prompt = prompt
    async def interrupt(self): self.interrupted = True
    async def disconnect(self): self.disconnected = True

    async def receive_response(self):
        if self.stalled:
            await asyncio.sleep(30)
        yield SystemMessage(subtype="init", data={"session_id": "original-mocked-sdk-session"})
        yield AssistantMessage(content=[TextBlock(text="original-mocked-sdk-secret says PASS")], model="mocked-sdk-transport")
        yield ResultMessage(subtype="success" if not self.failure else "error_during_execution",
            duration_ms=1, duration_api_ms=1, is_error=self.failure, num_turns=1,
            session_id="original-mocked-sdk-session", total_cost_usd=None,
            usage=None, result="PASS", structured_output={"fixture": "original"})


def setup(client, tmp_path, timeout_seconds=1):
    pid = project(client)
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    bridge = ClaudeBridge(service, tmp_path)
    request = {"prompt": "original fixture", "needs_tools": False, "max_turns": 1, "timeout_seconds": timeout_seconds}
    workspace = tmp_path / "native-original"
    workspace.mkdir()
    row, created = bridge._record(pid, "original-sdk-operation", request, workspace, "mocked-sdk-transport")
    return pid, bridge, row, request


def test_live_preflight_never_borrows_developer_login_and_no_false_gateway_compatibility(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "EEAS_MODEL_ID": "", "EEAS_LIVE_MAX_COST_USD": ""}):
        assert "NO_CREDENTIALS" in bridge.preflight(True)
        assert "OS_ISOLATION_NOT_QUALIFIED" in bridge.preflight(True)
        with pytest.raises(LiveBlocked):
            asyncio.run(bridge.submit(pid, "live-missing", request))
        caps = client.get("/api/runtime").json()
        assert not caps["developer_login_used"]
        assert caps["intranet_target"]["api_compatibility"] == "NOT_VALIDATED"


@pytest.mark.parametrize("failure", [False, True])
def test_sdk_message_contract_real_storage_error_and_result_never_self_verifies(client, tmp_path, failure):
    pid, bridge, row, request = setup(client, tmp_path)
    model = MockSDKTransportClient(failure=failure)
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "original-mocked-sdk-secret"}):
        result = asyncio.run(bridge.consume(pid, row["id"], model, request))
    assert result["execution_status"] == ("FAILED" if failure else "RETURNED")
    assert result["verification_status"] == "NOT_RUN"
    assert result["session_id"] == "original-mocked-sdk-session"
    exported = bridge.export_result(pid, row["id"])
    assert not exported["live_evidence"]
    assert "original-mocked-sdk-secret" not in json.dumps(exported)
    assert [e["event_seq"] for e in exported["events"]] == [1, 2, 3]
    assert model.connected and model.disconnected


def test_stalled_sdk_timeout_requests_interrupt_keeps_outcome_unknown(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    model = MockSDKTransportClient(stalled=True)
    result = asyncio.run(bridge.consume(pid, row["id"], model, request))
    assert model.interrupted and model.disconnected
    assert result["execution_status"] == "UNKNOWN_OUTCOME"
    assert result["verification_status"] == "NOT_RUN"


def test_permission_paths_symlink_and_unapproved_native_capability_denied(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    workspace = tmp_path / "permission-workspace"
    workspace.mkdir()
    (workspace / "escape").symlink_to(tmp_path)
    permission = lambda name, args: asyncio.run(bridge.permission(workspace, ["candidate.py"], name, args, None))
    assert permission("Write", {"file_path": "candidate.py"}).behavior == "allow"
    assert permission("Write", {"file_path": "unapproved.py"}).behavior == "deny"
    assert permission("Read", {"file_path": "../outside"}).behavior == "deny"
    assert permission("Read", {"file_path": "escape/anything"}).behavior == "deny"
    assert permission("Bash", {"command": "arbitrary command"}).behavior == "deny"
