"""Native lifecycle contracts; only external model transport is mocked."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest

from test_claude_bridge import MockSDKTransportClient, setup
from packages.adapters.claude_sdk import LiveBlocked
from packages.domain.store import Conflict


def test_cancel_before_consume_never_connects_or_queries(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    model = MockSDKTransportClient()
    asyncio.run(bridge.request_cancel(pid, row["id"]))
    reopened = type(bridge)(bridge.service, tmp_path)
    result = asyncio.run(reopened.consume(pid, row["id"], model, request))
    assert result["execution_status"] == "CANCELLED"
    assert not model.connected and not hasattr(model, "prompt")


class DisconnectedAfterQuery(MockSDKTransportClient):
    async def receive_response(self):
        raise ConnectionError("original lost transport")
        yield  # Keep the SDK's async-iterator contract.


def test_lost_transport_after_query_is_unknown_and_cannot_be_replayed(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    model = DisconnectedAfterQuery()
    result = asyncio.run(bridge.consume(pid, row["id"], model, request))
    assert result["execution_status"] == "UNKNOWN_OUTCOME"
    replay = MockSDKTransportClient()
    again = asyncio.run(bridge.consume(pid, row["id"], replay, request))
    assert again["execution_status"] == "UNKNOWN_OUTCOME"
    assert not replay.connected


class TaskGroupTransport(MockSDKTransportClient):
    async def connect(self):
        self.owner = asyncio.current_task()
        self.group = anyio.create_task_group()
        await self.group.__aenter__()
        await super().connect()

    async def disconnect(self):
        assert asyncio.current_task() is self.owner
        await self.group.__aexit__(None, None, None)
        await super().disconnect()


def test_sdk_task_group_closes_in_its_owner_without_nested_cancel_scope(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    model = TaskGroupTransport()
    result = asyncio.run(bridge.consume(pid, row["id"], model, request))
    assert model.disconnected
    assert result["execution_status"] == "RETURNED"
    data = json.loads(result["result"])
    assert data["usage"] is None and data["total_cost_usd"] is None


def test_arbitrary_isolation_provider_does_not_qualify_code_execution(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    bridge.isolation_provider = object()
    assert "OS_ISOLATION_NOT_QUALIFIED" in bridge.preflight(True)


def test_changed_frozen_request_is_rejected_before_native_connect(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    model = MockSDKTransportClient()
    with pytest.raises(Conflict, match="frozen request"):
        asyncio.run(bridge.consume(pid, row["id"], model, {**request, "prompt": "changed original input"}))
    assert not model.connected
    assert bridge.inspect(pid, row["id"])["execution_status"] == "QUEUED"


def test_unqualified_tools_cannot_bypass_submit_gate_through_consume(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    model = MockSDKTransportClient()
    with pytest.raises(LiveBlocked, match="OS_ISOLATION_NOT_QUALIFIED"):
        asyncio.run(bridge.consume(pid, row["id"], model, {**request, "needs_tools": True}))
    assert not model.connected


def test_native_options_clear_inherited_secrets_and_default_authorization(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "original-product-key", "UNRELATED_PASSWORD": "original-other-secret",
                                "AWS_ACCESS_KEY_ID": "original-aws-key", "SSH_AUTH_SOCK": "/original-private-agent",
                                "EEAS_MODEL_ID": "original-model", "EEAS_LIVE_MAX_COST_USD": "0.01"}):
        options = bridge.options(Path(row["workspace"]), request, row["id"])
    assert options.env["ANTHROPIC_API_KEY"] == "original-product-key"
    assert options.env["UNRELATED_PASSWORD"] == options.env["AWS_ACCESS_KEY_ID"] == options.env["SSH_AUTH_SOCK"] == ""
    assert options.env["HOME"] == options.env["CLAUDE_CONFIG_DIR"]
    assert options.env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert options.permission_mode == "default" and options.setting_sources == []
    assert options.strict_mcp_config and options.mcp_servers == {} and options.tools == []
    assert options.allowed_tools == [] and options.add_dirs == [] and options.plugins == []
    assert "PreToolUse" in options.hooks and options.max_budget_usd == 0.01


@pytest.mark.parametrize("files,paths", [({"../escape.py": "original"}, []),
    ({".eeas-sdk-state/config.json": "original"}, []), ({"main.py": 42}, []),
    ({}, ["C:/escape.py"]), ({}, [".eeas-sdk-state/key"]), ({}, [42])])
def test_invalid_repository_input_is_rejected_before_persisting_new_execution(client, tmp_path, files, paths):
    pid, bridge, row, request = setup(client, tmp_path)
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "original-product-key", "EEAS_MODEL_ID": "original-model",
                                "EEAS_LIVE_MAX_COST_USD": "0.01"}), pytest.raises(ValueError):
        asyncio.run(bridge.submit(pid, "invalid-original-source", {**request, "source_files": files, "allowed_write_paths": paths}))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions WHERE operation_id='invalid-original-source'").fetchone()[0] == 0
    assert not (tmp_path / "agent-workspaces").exists()


def test_model_cannot_read_its_sdk_credentials_or_escape_glob_scope(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path)
    workspace = Path(row["workspace"])
    source = workspace / "src"
    source.mkdir()
    permission = lambda name, args: asyncio.run(bridge.permission(workspace, [".eeas-sdk-state/key"], name, args, None))
    assert permission("Read", {"file_path": ".eeas-sdk-state/key"}).behavior == "deny"
    assert permission("Write", {"file_path": ".eeas-sdk-state/key"}).behavior == "deny"
    assert permission("Glob", {"path": "src", "pattern": "../../*"}).behavior == "deny"
    assert permission("Glob", {"path": "src", "pattern": "**/*.py"}).behavior == "allow"
    assert permission("Grep", {"path": str(workspace), "pattern": "key"}).behavior == "deny"
