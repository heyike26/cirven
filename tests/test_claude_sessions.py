"""Persistent native session contracts; all prices/transcripts are original fixtures."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import time
import uuid
from unittest.mock import patch

from claude_agent_sdk import ClaudeSDKClient, project_key_for_directory
import pytest

from packages.adapters.claude_sdk import ClaudeBridge, LiveBlocked
from packages.domain.store import Conflict
from packages.domain.tasks import TaskService
from conftest import ROOT
from test_documents import project, upload
from test_claude_native_transport import OriginalJSONTransport


FIXTURE_ENV = {"ANTHROPIC_API_KEY": "original-unused-session-key", "EEAS_MODEL_ID": "original-session-model",
               "EEAS_LIVE_MAX_COST_USD": "0.01", "EEAS_MODEL_PROFILE_ID": "original-session-profile"}


class SessionWire(OriginalJSONTransport):
    def __init__(self, options, cost, mode="success", error=False):
        session = options.resume if options.resume and not options.fork_session else str(uuid.uuid4())
        super().__init__(mode=mode, session_id=session, total_cost_usd=cost)
        self.options, self.error = options, error

    async def transcript(self, entry):
        # Explicit native-transport fixture: the CLI's private JSONL and its
        # decoded mirror frame are separate outputs. A mirror alone cannot
        # qualify the production continuation checkpoint.
        path = Path(self.options.env["CLAUDE_CONFIG_DIR"]) / "projects" / project_key_for_directory(self.options.cwd) / (self.session_id + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        seed = []
        if not path.exists() and self.options.fork_session and self.options.resume:
            seed_path = path.with_name(self.options.resume + '.jsonl')
            seed = [json.loads(line) for line in seed_path.read_text().splitlines()]
        with path.open('ab') as stream:
            for item in [*seed, entry]:
                stream.write((json.dumps(item, ensure_ascii=False) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)
        await self.send.send({"type": "transcript_mirror", "filePath": str(path), "entries": [*seed, entry]})

    async def write(self, data):
        for line in data.splitlines():
            message = json.loads(line)
            if message["type"] == "user" and self.mode == "success":
                await self.transcript({"type": "user", "uuid": str(uuid.uuid4()), "sessionId": self.session_id,
                     "message": message["message"], "timestamp": "2026-10-12T00:00:00Z"})
        await super().write(data)

    async def result(self, terminal_reason="completed", is_error=False):
        await super().result(terminal_reason, is_error or self.error)


class NativeFactory:
    def __init__(self, costs, modes=None, error=False):
        self.costs, self.modes, self.error = costs, modes or [], error
        self.transports = []

    def __call__(self, options):
        index = len(self.transports)
        transport = SessionWire(options, self.costs[index], self.modes[index] if index < len(self.modes) else "success", self.error)
        self.transports.append(transport)
        return ClaudeSDKClient(options=options, transport=transport)


class QueuedWire(SessionWire):
    """Native wire fixture queues two user messages and emits ordered results."""
    def __init__(self, options, mode="success"):
        super().__init__(options, 0.002)
        self.queue_mode = mode
        self.prompts = []

    async def write(self, data):
        messages = [json.loads(line) for line in data.splitlines()]
        for message in messages:
            if message["type"] == "user":
                self.prompts.append(message)
                self.prompt_received.set()
                if len(self.prompts) == 2:
                    if self.queue_mode == "lost":
                        await self.send.aclose()
                        return
                    for index, submitted in enumerate(self.prompts):
                        await self.transcript({"type": "user", "uuid": str(uuid.uuid4()), "sessionId": self.session_id, "message": submitted["message"]})
                        self.total_cost_usd = 0.002 if index == 0 else (0.001 if self.queue_mode == "cost_reset" else 0.003)
                        await self.result()
                self.writes.append(message)
            else:
                await super().write(json.dumps(message) + "\n")


def context(client, tmp_path, **overrides):
    pid = project(client)
    bridge = ClaudeBridge(TaskService(tmp_path / "eeas.sqlite3", ROOT), tmp_path)
    request = {"prompt": "Explain this original source", "needs_tools": False, "max_turns": 3,
               "timeout_seconds": 30, "source_files": {"src/original.txt": "原创事实\n"}, **overrides}
    return pid, bridge, request


@pytest.mark.parametrize("kind", ["resume", "fork"])
def test_native_session_continuations_freeze_parent_checkpoint_and_remaining_budget(client, tmp_path, kind):
    pid, bridge, request = context(client, tmp_path, native={"effort": "low", "thinking": {"type": "disabled"}, "include_hook_events": True})
    factory = NativeFactory([0.002, 0.004])
    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "original-root", request))
        old_events = bridge.stream_events(pid, parent["id"])
        checkpoint = bridge._checkpoint(pid, parent)
        old_bytes = Path(checkpoint["transcript_path"]).read_bytes()
        child = asyncio.run(getattr(bridge, kind)(pid, parent["id"], "original-followup", "A new original question",
                                               expected_session_id=parent["session_id"]))
        repeated = asyncio.run(getattr(bridge, kind)(pid, parent["id"], "original-followup", "A new original question",
                                                  expected_session_id=parent["session_id"]))
    assert parent["execution_status"] == child["execution_status"] == "RETURNED"
    assert parent["id"] != child["id"] and child["id"] == repeated["id"]
    assert child["workspace"] != parent["workspace"] and len(factory.transports) == 2
    options = factory.transports[1].options
    assert options.resume == parent["session_id"] and options.fork_session == (kind == "fork")
    assert options.max_budget_usd == 0.008 and options.effort == "low" and options.thinking == {"type": "disabled"}
    assert options.include_hook_events and options.permission_mode == "default" and options.skills == []
    assert Path(checkpoint["transcript_path"]).read_bytes() == old_bytes
    assert bridge.inspect(pid, parent["id"]) == parent and bridge.stream_events(pid, parent["id"]) == old_events
    with bridge.service.connect() as connection:
        frozen = dict(connection.execute("SELECT * FROM agent_controls WHERE execution_id=?", (child["id"],)).fetchone())
        budget = dict(connection.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (child["id"],)).fetchone())
        assert frozen["checkpoint_hash"] == hashlib.sha256(old_bytes).hexdigest()
        assert budget["prior_spent_usd"] == "0.002" and budget["call_budget_usd"] == "0.008"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE agent_scopes SET scope='changed'")
    assert not bridge.export_result(pid, child["id"])["live_evidence"]


@pytest.mark.parametrize("cost", [None, 0.01])
def test_unknown_or_exhausted_cost_never_starts_a_new_native_client(client, tmp_path, cost):
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([cost])
    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "original-root", request))
        with pytest.raises(LiveBlocked, match="SESSION_COST_UNKNOWN|SESSION_BUDGET_EXHAUSTED"):
            asyncio.run(bridge.resume(pid, parent["id"], "original-blocked", "Original followup", expected_session_id=parent["session_id"]))
    assert len(factory.transports) == 1 and json.loads(parent["result"])["total_cost_usd"] == cost


def test_failed_native_result_can_be_explicitly_forked_without_overwriting_failure(client, tmp_path):
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002, 0.004], error=True)
    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "original-failure", request))
        original = bridge.export_result(pid, parent["id"])
        child = asyncio.run(bridge.fork(pid, parent["id"], "original-new-fork", "Explain the original failure", expected_session_id=parent["session_id"]))
    assert parent["execution_status"] == child["execution_status"] == "FAILED"
    assert bridge.export_result(pid, parent["id"]) == original


@pytest.mark.parametrize("mode,expected", [("success", "RETURNED"), ("lost", "UNKNOWN_OUTCOME"), ("cost_reset", "UNKNOWN_OUTCOME")])
def test_active_native_query_followup_is_persistent_and_uses_one_client_budget(client, tmp_path, mode, expected):
    pid, bridge, request = context(client, tmp_path)
    transports = []

    def installed_sdk(options):
        wire = QueuedWire(options, mode)
        transports.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)

    async def run():
        execution = asyncio.create_task(bridge.submit(pid, "original-active", request))
        while not transports:
            await asyncio.sleep(0)
        await transports[0].prompt_received.wait()
        for _ in range(100):
            with bridge.service.connect() as connection:
                parent = dict(connection.execute("SELECT * FROM agent_executions WHERE operation_id='original-active'").fetchone())
            if parent["session_id"]:
                break
            await asyncio.sleep(0.01)
        assert parent["session_id"]
        child = await bridge.steer(pid, parent["id"], "original-steer", "Original followup", expected_session_id=parent["session_id"])
        repeated = await bridge.steer(pid, parent["id"], "original-steer", "Original followup", expected_session_id=parent["session_id"])
        assert repeated["id"] == child["id"]
        final_parent = await execution
        return final_parent, bridge.inspect(pid, child["id"])

    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed_sdk):
        parent, child = asyncio.run(run())
    assert child["execution_status"] == expected
    assert parent["execution_status"] == ("RETURNED" if mode == "cost_reset" else expected)
    assert len(transports) == 1 and len(transports[0].prompts) == 2
    assert transports[0].options.max_budget_usd == 0.01 and transports[0].closed
    assert child["verification_status"] == "NOT_RUN"
    if mode == "success":
        assert json.loads(child["result"])["total_cost_usd"] == 0.003
        checkpoint = bridge._checkpoint(pid, child)
        assert checkpoint["session_id"] == parent["session_id"]
        with bridge.service.connect() as connection:
            assert bridge._lineage_budget(pid, child["id"], connection)["spent_usd"] == "0.003"
    elif mode == "cost_reset":
        assert json.loads(child["result"])["budget"]["scope_verified"] is False
        with bridge.service.connect() as connection, pytest.raises(LiveBlocked):
            bridge._lineage_budget(pid, child["id"], connection)


@pytest.mark.parametrize("change", ["model", "gateway", "sdk", "source", "workspace", "transcript", "document", "expired"])
def test_scope_or_checkpoint_change_blocks_resume_before_native_query(client, tmp_path, change):
    pid, bridge, request = context(client, tmp_path, session_ttl_seconds=1 if change == "expired" else 600)
    if change == "source":
        source_root = tmp_path / "original-source"
        (source_root / "packages").mkdir(parents=True)
        (source_root / "profiles").mkdir()
        (source_root / "packages/original.py").write_text("original = 1\n")
        (source_root / "profiles/stack.lock.json").write_text('{"original":1}')
        bridge.service.source_root = source_root
    if change == "document":
        document = upload(client, pid, b"original source").json()
        request["document_refs"] = [{"document_id": document["id"], "revision": 1, "sha256": document["sha256"]}]
    factory = NativeFactory([0.002])
    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "original-root", request))
        if change == "model": os.environ["EEAS_MODEL_ID"] = "changed-model"
        elif change == "gateway": os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:1/changed"
        elif change == "sdk": bridge.sdk_version = "original-unqualified-version"
        elif change == "source": (source_root / "packages/original.py").write_text("original = 2\n")
        elif change == "workspace": (Path(parent["workspace"]) / "src/original.txt").write_text("changed artifact")
        elif change == "transcript":
            checkpoint = bridge._checkpoint(pid, parent)
            with Path(checkpoint["transcript_path"]).open("ab") as stream: stream.write(b'{}\n')
        elif change == "document":
            client.put(f"/api/projects/{pid}/documents/{document['id']}", files={"file": ("original.md", b"changed source")}, data={"expected_revision": 1})
        elif change == "expired": time.sleep(1.05)
        with pytest.raises(Conflict):
            asyncio.run(bridge.resume(pid, parent["id"], "original-stale", "Original followup", expected_session_id=parent["session_id"]))
    assert len(factory.transports) == 1 and bridge.inspect(pid, parent["id"]) == parent


@pytest.mark.parametrize("native", [{"permission_mode": "bypassPermissions"}, {"env": {"SECRET": "original"}},
    {"tools": ["Bash"]}, {"mcp_servers": {}}, {"resume": "original"}, {"plugins": []},
    {"thinking": {"type": "enabled", "budget_tokens": 8193}}, {"effort": "unapproved"}])
def test_unapproved_native_parameters_never_persist_or_connect(client, tmp_path, native):
    pid, bridge, request = context(client, tmp_path, native=native)
    with patch.dict(os.environ, FIXTURE_ENV), pytest.raises(ValueError):
        asyncio.run(bridge.submit(pid, "original-invalid-options", request))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


def test_native_session_mirrors_relocate_without_changing_historical_paths_or_hashes(client, tmp_path):
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002, 0.004])
    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "original-root", request))
        original_checkpoint = bridge._checkpoint(pid, parent)
        restored = tmp_path.parent / (tmp_path.name + "-restored")
        restored.mkdir()
        with bridge.service.connect() as connection, sqlite3.connect(restored / "eeas.sqlite3") as destination:
            connection.backup(destination)
        shutil.copytree(tmp_path / "agent-workspaces", restored / "agent-workspaces",
                        ignore=shutil.ignore_patterns(".eeas-sdk-state"))
        shutil.copytree(tmp_path / "agent-transcripts", restored / "agent-transcripts")
        shutil.copytree(tmp_path / "task-resource-budgets", restored / "task-resource-budgets")
        # A continuation checkpoint also needs the original native JSONL. Copy
        # that exact private artifact, without copying unrelated SDK accounts.
        native_relative=Path(parent['workspace']).relative_to(tmp_path)/'.eeas-sdk-state'/'projects'/project_key_for_directory(parent['workspace'])/(parent['session_id']+'.jsonl')
        native_copy=restored/native_relative;native_copy.parent.mkdir(parents=True)
        shutil.copy2(tmp_path/native_relative,native_copy)
        (restored / "restore-provenance.json").write_text(json.dumps({"schema_version": "eeas.restore-provenance.v1",
            "allowed_source_data_dirs": [str(tmp_path)]}), encoding="utf-8")
        relocated = ClaudeBridge(TaskService(restored / "eeas.sqlite3", ROOT), restored)
        assert relocated._checkpoint(pid, parent) == original_checkpoint
        native_original=native_copy.read_bytes()
        native_copy.write_bytes(native_original+b'{"type":"manual","text":"keep restored newer version"}\n')
        with pytest.raises(Conflict):relocated._checkpoint(pid,parent)
        native_copy.write_bytes(native_original)
        child = asyncio.run(relocated.resume(pid, parent["id"], "original-relocated-followup", "Original question",
                                              expected_session_id=parent["session_id"]))
        assert child["execution_status"] == "RETURNED" and Path(child["workspace"]).is_relative_to(restored)
        assert relocated.inspect(pid, parent["id"])["workspace"] == parent["workspace"]
        with relocated.service.connect() as connection:
            frozen = dict(connection.execute("SELECT * FROM agent_checkpoints WHERE execution_id=?", (parent["id"],)).fetchone())
        assert frozen == original_checkpoint


def test_native_budget_overrun_is_reported_as_failed_and_blocks_new_session_calls(client, tmp_path):
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.02])
    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "original-overrun", request))
        assert parent["execution_status"] == "FAILED"
        budget = json.loads(parent["result"])["budget"]
        assert budget["within_estimate_limit"] is False and budget["reported_new_cost_usd"] == "0.02"
        with pytest.raises(LiveBlocked, match="SESSION_BUDGET_EXHAUSTED"):
            asyncio.run(bridge.fork(pid, parent["id"], "original-blocked-fork", "Original followup", expected_session_id=parent["session_id"]))
    assert len(factory.transports) == 1


def test_cancelled_native_session_is_not_resumed_and_foreign_or_missing_head_is_rejected(client, tmp_path):
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002], modes=["cancel"])

    async def run():
        execution = asyncio.create_task(bridge.submit(pid, "original-cancel", request))
        while not factory.transports:
            await asyncio.sleep(0)
        await factory.transports[0].prompt_received.wait()
        with bridge.service.connect() as connection:
            row = dict(connection.execute("SELECT * FROM agent_executions WHERE operation_id='original-cancel'").fetchone())
        await bridge.request_cancel(pid, row["id"])
        return await execution

    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(run())
        assert parent["execution_status"] == "CANCELLED"
        with pytest.raises(Conflict, match="cancelled"):
            asyncio.run(bridge.resume(pid, parent["id"], "original-no-resume", "Original question", expected_session_id=parent["session_id"]))
        with pytest.raises(Conflict, match="Expected session"):
            asyncio.run(bridge.fork(pid, parent["id"], "original-no-fork", "Original question", expected_session_id="not-current"))
        with pytest.raises(Conflict, match="active native owner"):
            asyncio.run(bridge.steer(pid, parent["id"], "original-no-steer", "Original question", expected_session_id=parent["session_id"]))
    assert len(factory.transports) == 1


@pytest.mark.parametrize("command", ["/clear", "/reset", "/new"])
def test_native_budget_reset_commands_are_rejected_before_any_execution(client, tmp_path, command):
    pid, bridge, request = context(client, tmp_path, prompt=command)
    with patch.dict(os.environ, FIXTURE_ENV), pytest.raises(ValueError, match="slash commands"):
        asyncio.run(bridge.submit(pid, "original-budget-reset", request))
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


def test_control_preflight_checks_existing_session_without_side_effect_and_stale_scope_blocks_native_query(client, tmp_path):
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002])
    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "original-root", request))
        with bridge.service.connect() as connection:
            before = connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0]
        admitted = bridge.control_preflight(pid, parent["id"], "resume", expected_session_id=parent["session_id"])
        assert admitted["budget"]["remaining_usd"] == "0.008"
        with bridge.service.connect() as connection:
            assert connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == before
        os.environ["EEAS_MODEL_ID"] = "original-changed-profile"
        with pytest.raises(Conflict, match="scope changed"):
            bridge.control_preflight(pid, parent["id"], "fork", expected_session_id=parent["session_id"])
    assert len(factory.transports) == 1


def test_accepted_native_request_rechecks_scope_before_connect(client, tmp_path):
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002])
    workspace = tmp_path / "agent-workspaces" / str(uuid.uuid4())
    workspace.mkdir(parents=True)
    with patch.dict(os.environ, FIXTURE_ENV):
        row, created = bridge._record(pid, "original-queued", request, workspace, "claude-agent-sdk")
        options = bridge.options(workspace, request, row["id"])
        model = factory(options)
        os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:1/original-changed-profile"
        with pytest.raises(Conflict, match="scope changed"):
            asyncio.run(bridge.consume(pid, row["id"], model, request))
    assert not factory.transports[0].ready and factory.transports[0].writes == []
    assert bridge.inspect(pid, row["id"])["execution_status"] == "QUEUED"
