"""Use the installed native SDK; mock only its external JSON wire transport."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import anyio
from claude_agent_sdk import ClaudeSDKClient, Transport
import pytest

from test_claude_bridge import claude_bridge_runtime, setup
from test_documents import project
from test_requirements import proposal


class OriginalJSONTransport(Transport):
    def __init__(self, mode="success", structured_output=None, session_id="original-native-wire-session", total_cost_usd=None):
        self.mode = mode
        self.session_id = session_id
        self.total_cost_usd = total_cost_usd
        self.structured_output = structured_output if structured_output is not None else {"original": True}
        self.send, self.receive = anyio.create_memory_object_stream(20)
        self.writes = []
        self.ready = False
        self.closed = False
        self.prompt_received = asyncio.Event()
        self.connect_started = asyncio.Event()
        self.connect_allowed = asyncio.Event()

    async def connect(self):
        self.connect_started.set()
        if self.mode == "pause_connect":
            await self.connect_allowed.wait()
        if self.mode == "connect_error":
            raise ConnectionError("original connection never established")
        self.ready = True

    async def write(self, data):
        for line in data.splitlines():
            message = json.loads(line)
            self.writes.append(message)
            if message["type"] == "control_request":
                await self.send.send({"type": "control_response", "response": {
                    "subtype": "success", "request_id": message["request_id"], "response": {}}})
                if message["request"]["subtype"] == "initialize":
                    self.initialization = message["request"]
                    await self.send.send({"type": "system", "subtype": "init", "session_id": self.session_id})
                elif message["request"]["subtype"] == "interrupt" and self.mode in {"cancel", "cancel_completed"}:
                    await self.result("aborted_tools" if self.mode == "cancel" else "completed", is_error=self.mode == "cancel")
            elif message["type"] == "user":
                self.prompt_received.set()
                if self.mode in {"success", "close_error"}:
                    await self.send.send({"type": "assistant", "message": {
                        "model": "original-mocked-transport", "content": [{"type": "text", "text": "original reply"}]}})
                    await self.result()
                elif self.mode == "lost":
                    await self.send.aclose()
                elif self.mode == "hook":
                    callback_id = self.initialization["hooks"]["PreToolUse"][0]["hookCallbackIds"][0]
                    await self.send.send({"type": "control_request", "request_id": "original-hook-request", "request": {
                        "subtype": "hook_callback", "callback_id": callback_id, "tool_use_id": "original-tool",
                        "input": {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "../outside"}}}})
            elif message["type"] == "control_response" and self.mode == "hook":
                self.hook_reply = message["response"]
                await self.result()

    async def result(self, terminal_reason="completed", is_error=False):
        await self.send.send({"type": "result", "subtype": "success", "duration_ms": 1,
            "duration_api_ms": 1, "is_error": is_error, "num_turns": 1,
            "session_id": self.session_id, "terminal_reason": terminal_reason, "total_cost_usd": self.total_cost_usd,
            "result": "original result", "structured_output": self.structured_output})

    async def read_messages(self):
        async with self.receive:
            async for message in self.receive:
                yield message

    async def close(self):
        self.closed = True
        self.ready = False
        await self.send.aclose()
        if self.mode == "close_error":
            raise ConnectionError("original stop confirmation lost")

    def is_ready(self):
        return self.ready

    async def end_input(self):
        pass


def native_options(bridge, row, request):
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "original-unused-test-key",
                                "EEAS_MODEL_ID": "original-transport-fixture",
                                "EEAS_LIVE_MAX_COST_USD": "0.01"}):
        return bridge.options(Path(row["workspace"]), request, row["id"])


@pytest.mark.parametrize("mode,expected", [("success", "RETURNED"), ("lost", "UNKNOWN_OUTCOME"), ("timeout", "UNKNOWN_OUTCOME")])
def test_installed_native_sdk_controls_events_session_cost_and_disconnect(client, tmp_path, mode, expected):
    pid, bridge, row, request = setup(client, tmp_path, timeout_seconds=1 if mode == "timeout" else 10)
    transport = OriginalJSONTransport(mode)
    model = ClaudeSDKClient(options=native_options(bridge, row, request), transport=transport)
    result = asyncio.run(bridge.consume(pid, row["id"], model, request))
    assert result["execution_status"] == expected
    assert result["verification_status"] == "NOT_RUN"
    assert result["session_id"] == "original-native-wire-session"
    assert transport.closed and model._query is None
    assert sum(message["type"] == "user" for message in transport.writes) == 1
    if mode == "success":
        data = json.loads(result["result"])
        assert data["usage"] is None and data["total_cost_usd"] is None
        assert data["terminal_reason"] == "completed"
        assert bridge.stream_events(pid, row["id"])[-1]["payload"]["type"] == "ResultMessage"
    assert not bridge.export_result(pid, row["id"])["live_evidence"]


def test_installed_sdk_interrupt_is_a_request_until_native_aborted_result(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path, timeout_seconds=10)
    transport = OriginalJSONTransport("cancel")
    model = ClaudeSDKClient(options=native_options(bridge, row, request), transport=transport)

    async def run():
        execution = asyncio.create_task(bridge.consume(pid, row["id"], model, request))
        await transport.prompt_received.wait()
        requested = await bridge.request_cancel(pid, row["id"])
        assert requested["execution_status"] in {"CANCEL_REQUESTED", "CANCELLED"}
        return await execution

    result = asyncio.run(run())
    assert result["execution_status"] == "CANCELLED"
    assert json.loads(result["result"])["terminal_reason"] == "aborted_tools"
    assert transport.closed
    asyncio.run(bridge.request_cancel(pid, row["id"]))
    assert sum(message.get("request", {}).get("subtype") == "interrupt" for message in transport.writes) == 1


def test_installed_sdk_invokes_mandatory_native_hook_before_a_read(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path, timeout_seconds=10)
    transport = OriginalJSONTransport("hook")
    model = ClaudeSDKClient(options=native_options(bridge, row, request), transport=transport)
    result = asyncio.run(bridge.consume(pid, row["id"], model, request))
    assert result["execution_status"] == "RETURNED"
    decision = transport.hook_reply["response"]["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    events = bridge.stream_events(pid, row["id"])
    assert any(e["payload"]["type"] == "tool_policy" and e["payload"]["decision"] == "deny" for e in events)


@pytest.mark.parametrize("mode,expected", [("connect_error", "FAILED"), ("close_error", "UNKNOWN_OUTCOME")])
def test_native_failures_distinguish_before_query_from_unconfirmed_disconnect(client, tmp_path, mode, expected):
    pid, bridge, row, request = setup(client, tmp_path, timeout_seconds=10)
    transport = OriginalJSONTransport(mode)
    model = ClaudeSDKClient(options=native_options(bridge, row, request), transport=transport)
    result = asyncio.run(bridge.consume(pid, row["id"], model, request))
    assert result["execution_status"] == expected
    if mode == "connect_error":
        assert not any(message["type"] == "user" for message in transport.writes)
    else:
        assert json.loads(result["result"])["result"] == "original result"
        assert bridge.stream_events(pid, row["id"])[-1]["payload"]["type"] == "disconnect_unconfirmed"


def test_native_cancel_during_connect_never_submits_the_user_prompt(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path, timeout_seconds=10)
    transport = OriginalJSONTransport("pause_connect")
    model = ClaudeSDKClient(options=native_options(bridge, row, request), transport=transport)

    async def run():
        execution = asyncio.create_task(bridge.consume(pid, row["id"], model, request))
        await transport.connect_started.wait()
        await bridge.request_cancel(pid, row["id"])
        transport.connect_allowed.set()
        return await execution

    result = asyncio.run(run())
    assert result["execution_status"] == "CANCELLED"
    assert transport.closed and not any(message["type"] == "user" for message in transport.writes)


def test_completed_result_after_cancel_request_is_not_a_confirmed_cancellation(client, tmp_path):
    pid, bridge, row, request = setup(client, tmp_path, timeout_seconds=10)
    transport = OriginalJSONTransport("cancel_completed")
    model = ClaudeSDKClient(options=native_options(bridge, row, request), transport=transport)

    async def run():
        execution = asyncio.create_task(bridge.consume(pid, row["id"], model, request))
        await transport.prompt_received.wait()
        await bridge.request_cancel(pid, row["id"])
        return await execution

    result = asyncio.run(run())
    assert result["cancel_requested"] and result["execution_status"] == "RETURNED"
    assert json.loads(result["result"])["terminal_reason"] == "completed"


@pytest.mark.parametrize("damaged_reference", [False, True])
def test_real_api_native_sdk_proposals_require_exact_source_and_human_confirmation(client, damaged_reference):
    pid = project(client)
    document, body = proposal(client, pid)
    body["confirmed"] = True  # A model cannot approve its own suggestion.
    body["origin"] = "manual"
    if damaged_reference:
        body["requirements"][0]["source_refs"][0]["sha256"] = "0" * 64
    transport = OriginalJSONTransport(structured_output=body)

    def installed_sdk(options):
        return ClaudeSDKClient(options=options, transport=transport)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "original-unused-test-key",
                                "EEAS_MODEL_ID": "original-transport-fixture", "EEAS_LIVE_MAX_COST_USD": "0.01"}), \
            patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed_sdk):
        response = client.post(f"/api/projects/{pid}/planning/extract",
                               json={"document_id": document["id"], "revision": 1, "mode": "live"})
    assert response.status_code == (422 if damaged_reference else 200)
    if not damaged_reference:
        answer = response.json()
        assert not answer["proposal"]["confirmed"] and answer["proposal"]["origin"] == "live_suggestion"
        assert not answer["model_verified"] and answer["approval"] == "HUMAN_CONFIRMATION_REQUIRED"
        execution = client.get(f"/api/projects/{pid}/agent-executions/{answer['execution_id']}").json()
        assert execution["execution_status"] == "RETURNED" and execution["sdk_version"] == "0.2.165"
        assert execution["verification_status"] == "NOT_RUN"
        exported = client.app.state.bridge.export_result(pid, execution["id"])
        assert not exported["live_evidence"] and exported["native_result_recorded"]
        assert exported["events"][-1]["payload"]["type"] == "workspace_diff"
        assert exported["events"][-1]["payload"]["changed_paths"] == []
        again = client.post(f"/api/projects/{pid}/agent-executions/{execution['id']}/cancel")
        assert again.json()["execution_status"] == "RETURNED"
    assert client.get(f"/api/projects/{pid}/requirements").json()["requirements"] == []
    assert transport.closed
