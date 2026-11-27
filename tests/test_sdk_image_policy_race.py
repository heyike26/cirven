"""Original model wire changes process policy during real SDK connect."""
import asyncio
import json
from unittest.mock import patch

from claude_agent_sdk import ClaudeSDKClient
import pytest

from packages.adapters.claude_sdk import digest
from test_claude_sessions import SessionWire
from test_sdk_images import environment, image_context


@pytest.mark.parametrize("kind", ["submit", "resume", "fork"])
@pytest.mark.parametrize("variable,value", [("EEAS_MODEL_MAX_IMAGES", "5"),
                                            ("EEAS_MODEL_MAX_IMAGE_PAYLOAD_BYTES", str(5 * 1024 * 1024))])
def test_connect_policy_change_never_sends_user_images_under_new_scope(client, tmp_path, monkeypatch, kind, variable, value):
    environment(monkeypatch)
    pid, bridge, request, *_ = image_context(client, tmp_path)
    wires, changed = [], []

    class ConnectPolicyWire(SessionWire):
        async def connect(self):
            await super().connect()
            monkeypatch.setenv(variable, value)
            changed.append(True)

    def installed(options):
        index = len(wires)
        should_change = kind == "submit" or index == 1
        wire = (ConnectPolicyWire if should_change else SessionWire)(options, 0.002)
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)

    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed):
        parent = asyncio.run(bridge.submit(pid, "image-policy-parent", request))
        if kind == "submit": result = parent
        else:
            assert parent["execution_status"] == "RETURNED"
            old_events = bridge.stream_events(pid, parent["id"])
            result = asyncio.run(getattr(bridge, kind)(pid, parent["id"], "image-policy-continuation", "Original continuation",
                                                     expected_session_id=parent["session_id"]))
            assert bridge.inspect(pid, parent["id"]) == parent
            assert bridge.stream_events(pid, parent["id"]) == old_events
    assert changed == [True] and wires[-1].closed
    assert result["execution_status"] == "FAILED" and result["result"] is None
    assert not [message for message in wires[-1].writes if message["type"] == "user"]
    with bridge.service.connect() as connection:
        frozen = dict(connection.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (result["id"],)).fetchone())
    frozen_scope = json.loads(frozen["scope"])
    assert digest(frozen_scope) == frozen["scope_hash"]
    assert frozen_scope["image_input_hash"] != bridge._scope(pid, json.loads(result["request"]))["image_input_hash"]
