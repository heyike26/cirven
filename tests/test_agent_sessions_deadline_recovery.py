"""Real service + SQLite; controlled SDK HTTP, clock and owned-process facets.

No original CLI, live API, Windows, account or model is exercised here. The
immutable Source39 real-wall-clock counterexamples live in the D receipt; these
regressions advance only the external clock, not operation records or methods.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import httpx
import pytest

from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.agent_sessions import AgentSessions
import packages.agent_sessions.service as service_module
import packages.agent_sessions.guidance as guidance_module
from packages.agent_sessions.models import OpenSession
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Store, Conflict
from packages.domain.tasks import digest, now


def check(value, message="deadline/recovery acceptance failed"):
    if not value:
        raise AssertionError(message)


class Clock(datetime):
    instant = datetime.now(timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.instant if tz is not None else cls.instant.replace(tzinfo=None)

    @classmethod
    def advance(cls, seconds):
        cls.instant += timedelta(seconds=seconds)


class OwnedProcessFacet:
    """UNIT external OS boundary; no subprocess exists or is controlled."""
    returncode = None

    def __init__(self):
        self.terminated = 0

    def terminate(self):
        self.terminated += 1
        self.returncode = 0

    async def wait(self):
        return self.returncode


class SDK:
    def __init__(self, directory):
        self.directory, self.native = str(directory), "ses_deadline_unit"
        self.posts, self.messages, self.children, self.requests = [], [], [], []
        self.statuses = {self.native: {"type": "busy"}}
        self.fail_prompt, self.hold_abort, self.hold_prompt = False, False, False
        self.abort_result, self.late_abort_pending = True, False
        self.prompt_started, self.prompt_release = asyncio.Event(), asyncio.Event()
        self.abort_started, self.abort_release = asyncio.Event(), asyncio.Event()
        self.permission_get_started, self.permission_get_release = asyncio.Event(), asyncio.Event()
        self.hold_permission_get = False

    async def handle(self, request):
        path, method = request.url.path, request.method
        body = __import__("json").loads(request.content) if request.content else None
        if method == "POST":
            self.posts.append({"path": path, "body": deepcopy(body)})
            if path == f"/session/{self.native}/prompt_async":
                # OpenCode returns persisted parts with their owning session and message.
                message_id = body["messageID"]
                parts = [{**deepcopy(part), "sessionID": self.native, "messageID": message_id}
                         for part in body["parts"]]
                self.messages.append({"info": {"id": message_id, "role": "user", "sessionID": self.native},
                                      "parts": parts})
                self.statuses[self.native] = {"type": "busy"}
                self.prompt_started.set()
                if self.hold_prompt:
                    await self.prompt_release.wait()
                if self.fail_prompt:
                    raise httpx.ReadTimeout("UNIT lost dispatch ACK", request=request)
                return httpx.Response(204)
            if path == f"/session/{self.native}/abort":
                self.abort_started.set()
                if self.hold_abort:
                    await self.abort_release.wait()
                self.requests = []
                self.statuses[self.native] = {"type": "idle"}
                if self.abort_result == "TIMEOUT":
                    self.late_abort_pending = True
                    raise httpx.ReadTimeout("UNIT original abort may execute late", request=request)
                if self.abort_result is False:
                    self.late_abort_pending = True
                return httpx.Response(200, json=self.abort_result)
            if path == "/instance/dispose":
                return httpx.Response(200, json=True)
            if path.startswith(("/permission/", "/question/")):
                self.requests = []
                for item in self.messages:
                    for part in item.get("parts", []):
                        if part.get("type") == "tool":
                            part["state"]["status"] = "completed"
                    if item["info"].get("role") == "assistant":
                        item["info"]["time"] = {"completed": 1}
                        item["info"]["finish"] = "tool-calls"
                return httpx.Response(200, json=True)
            raise AssertionError("Unexpected SDK mutation " + path)
        check(method == "GET", "Only fixed SDK GET/POST allowed")
        if path == f"/session/{self.native}":
            value = {"id": self.native, "directory": self.directory}
        elif path == f"/session/{self.native}/message":
            value = deepcopy(self.messages)
        elif path.startswith(f"/session/{self.native}/message/"):
            value = next(deepcopy(m) for m in self.messages if m['info']['id'] == path.rsplit('/', 1)[1])
        elif path == f"/session/{self.native}/todo":
            value = []
        elif path == f"/session/{self.native}/children":
            value = self.children
        elif path == "/session/status":
            value = deepcopy(self.statuses)
        elif path in ("/question", "/permission"):
            if path == "/permission" and self.hold_permission_get:
                self.permission_get_started.set()
                await self.permission_get_release.wait()
            prefix = "que_" if path == "/question" else "per_"
            value = [item for item in self.requests if item["id"].startswith(prefix)]
        else:
            raise AssertionError("Unexpected SDK GET " + path)
        return httpx.Response(200, json=value)

    def tool_wait(self, native_user, kind="PERMISSION"):
        assistant = {"info": {"id": "msg_wait", "role": "assistant", "sessionID": self.native,
                              "path": {"cwd": self.directory, "root": self.directory},
                              "parentID": native_user, "time": {"created": 1}},
                     "parts": [{"id": "prt_wait", "type": "tool", "sessionID": self.native,
                                "messageID": "msg_wait", "callID": "call_wait", "tool": "read" if kind == "PERMISSION" else "question",
                                "state": {"status": "running", "input": {"filePath": str(Path(self.directory) / 'owned.txt')} if kind == 'PERMISSION' else {}, "time": {"start": 1}}}]}
        self.messages.append(assistant)
        request = {"id": "per_wait" if kind == "PERMISSION" else "que_wait", "sessionID": self.native,
                   "tool": {"messageID": "msg_wait", "callID": "call_wait"}}
        if kind == "PERMISSION":
            request.update(permission="read", patterns=["owned.txt"], always=["owned.txt"], metadata={})
        else:
            request["questions"] = [{"question": "Use the original local fixture?", "header": "Choice",
                                     "options": [{"label": "Yes", "description": "Use it"}], "custom": False}]
        self.requests = [request]
        return assistant, request

    def terminal(self, native_user, *, error=True, identifier="msg_final"):
        for item in self.messages:
            if item["info"].get("role") == "assistant":
                item["info"]["time"] = {"completed": 1}
                for part in item.get("parts", []):
                    if part.get("type") == "tool":
                        part["state"]["status"] = "completed"
        info = {"id": identifier, "role": "assistant", "sessionID": self.native,
                "parentID": native_user, "time": {"completed": 1}, "finish": "stop"}
        if error:
            info["error"] = {"name": "MessageAbortedError", "data": {"message": "UNIT exact native stop"}}
        self.messages.append({"info": info, "parts": []})
        self.requests = []
        self.statuses[self.native] = {"type": "idle"}

    def post_count(self, suffix):
        return sum(item["path"].endswith(suffix) for item in self.posts)


class Scene:
    """No AgentSessions subclass or substituted business method."""
    def __init__(self, directory):
        self.data, self.workspace = directory / "data", directory / "workspace"
        self.workspace.mkdir(parents=True)
        (self.workspace / "owned.txt").write_text("Original local UNIT fixture; preserve these bytes.\n")
        self.original = (self.workspace / "owned.txt").read_bytes()
        self.store = Store(self.data / "store.sqlite")
        self.pid = self.store.create_project("Deadline recovery UNIT", "Controlled SDK, no model")["id"]
        self.works = LocalWorkspaces(self.store, self.data)
        bound = self.works.bind(self.pid, WorkspaceInput(idempotency_key="unit-root", root=str(self.workspace), allowed_write_paths=[]))
        self.caps = NativeCapabilities(self.store, self.data)
        binding = self.caps.native_binding_snapshot()
        cli = Path(__file__).resolve().parents[1] / "var/opencode-ui-offline-guard/opencode"
        check(cli.is_file() and not cli.is_symlink(), "Existing CLI metadata only; never execute it")
        self.config = {"schema": "eeas.opencode-provider.v1", "id": "deadline-provider", "revision": 1,
            "version": VERSION, "source_commit": COMMIT, "executable": str(cli), "executable_sha256": CLI_SHA["posix"],
            "provider_id": "eeas-deadline-unit", "protocol": "OPENAI_COMPATIBLE_CHAT_COMPLETIONS",
            "provider_url": "http://127.0.0.1:9/v1", "model_id": "unit-model", "secret_ref": "secret:unit-only", "transport_kind": "MOCK_WIRE"}
        self.registry = OpenCodeProviderRegistry(lambda *_: deepcopy(self.config), lambda _: "synthetic-unit-only")
        self.profile_ref = {"id": self.config["id"], "revision": 1, "sha256": digest(self.config)}
        self.profile = self.registry._resolve(self.profile_ref, verify_executable=False)
        self.owner = self.new_controller()
        self.sid = uuid.uuid4().hex
        self.request = OpenSession.model_validate({"operation_id": "unit-open", "workspace_ref": {k: bound[k] for k in ("id", "revision", "binding_sha256")},
            "provider_profile_ref": self.profile_ref, "capability_binding_sha256": binding["sha256"], "ttl_seconds": 600,
            "max_calls": 24, "max_output_tokens": 1024}).model_dump(exclude={"operation_id"})
        self.manifest = {"schema": "eeas.agent-session.v1", "id": self.sid, "project_id": self.pid, "created_at": now(),
            "request": self.request, "provider_fingerprint": self.profile.fingerprint, "workflow_provider_fingerprints": {},
            "provider_auth_mode": "API_KEY", "capability_configuration_refs": binding["value"]["configuration_refs"],
            "directory_identity": bound["root_identity"], "workspace_kind": "LOCAL_WORKSPACE"}
        self.owner._write(self.owner._path(self.pid, self.sid, "manifest.json"), self.manifest)
        self.ref = self.owner._manifest(self.pid, self.sid)[1]
        self.owner._write(self.owner._path(self.pid, self.sid, "state.json"), {"owner_status": "READY", "native_status": "idle",
            "original_session_id": "ses_deadline_unit", "generation": 3, "last_event_seq": 0, "model_calls_reserved": 0,
            "native_turns_started": 0, "active_operation_id": None, "messages": [], "todo": [], "children": [],
            "pending_questions": [], "pending_permissions": [], "capability_load": {"skills": [], "mcp": {}},
            "updated_at": now(), "controller_identity": None, "process_identity": None})
        self.sdk = SDK(self.workspace)
        self.client = httpx.AsyncClient(base_url="http://sdk-unit.invalid", transport=httpx.MockTransport(self.sdk.handle), timeout=10, trust_env=False)
        self.context = {"project_id":self.pid, "process": OwnedProcessFacet(), "closing": False, "loop": asyncio.get_running_loop(), "generation": 3,
            "profile": self.profile, "directory": str(self.workspace), "expires_at": (Clock.now(timezone.utc) + timedelta(seconds=600)).isoformat(),
            "lock": asyncio.Lock(), "session_id": self.sdk.native, "known_sessions": {self.sdk.native}, "client": self.client, "secrets": []}
        self.owner.active[self.sid] = self.context
        self.tasks = []

    def new_controller(self):
        return AgentSessions(self.store, self.data, provider_registry=self.registry, workspaces=self.works, capabilities=self.caps)

    def body(self, operation_id, **extra):
        return {"operation_id": operation_id, "session_ref": self.ref, "generation": self.context["generation"], **extra}

    def operation(self, key="parent"):
        return self.owner.operation(self.pid, self.sid, key)

    def state(self):
        return self.owner._state(self.pid, self.sid)

    async def send(self, key="parent"):
        return await self.owner.send(self.pid, self.sid, self.body(key, raw_text="Read owned.txt only after my answer.", timeout_seconds=10))

    async def inspect(self):
        return await self.owner.inspect(self.pid, self.sid)

    async def stop(self, key="stop"):
        return await self.owner.interrupt(self.pid, self.sid, self.body(key, expected_active_operation_id="parent"))

    def monitor(self):
        task = asyncio.create_task(self.owner._monitor(self.pid, self.sid, self.context))
        self.tasks.append(task)
        return task

    async def close(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()
        check((self.workspace / "owned.txt").read_bytes() == self.original, "UNIT original bytes changed")


@pytest.fixture
def clock(monkeypatch):
    Clock.instant = datetime.now(timezone.utc)
    for module in (service_module, guidance_module):
        monkeypatch.setattr(module, "datetime", Clock)
        monkeypatch.setattr(module, "now", lambda: Clock.now(timezone.utc).isoformat())
    async def no_process(*_args, **_kwargs):
        raise AssertionError("UNIT must never launch the original CLI or another process")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_process)
    return Clock


async def until(predicate, timeout=3):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        check(asyncio.get_running_loop().time() < deadline, "Bounded UNIT condition did not arrive")
        await asyncio.sleep(.01)


@pytest.mark.parametrize("kind", ["PERMISSION", "QUESTION"])
def test_exact_human_wait_pauses_only_execution_budget_then_reply_and_progress_keep_parent(tmp_path, clock, kind):
    async def run():
        scene = Scene(tmp_path)
        try:
            accepted = await scene.send()
            scene.sdk.tool_wait(accepted["native_message_id"], kind)
            await scene.inspect()
            waiting = scene.operation()
            check(waiting["human_wait"]["status"] == "WAITING_FOR_USER")
            expires = scene.context["expires_at"]
            clock.advance(11)
            scene.monitor()
            await asyncio.sleep(.4)
            check(scene.sdk.post_count("/abort") == 0 and scene.operation()["status"] == "ACCEPTED")
            check(scene.state()["active_operation_id"] == "parent" and scene.context["expires_at"] == expires)
            status_before = scene.operation()
            progress = await scene.owner.guidance.submit(scene.pid, scene.sid,
                scene.body("progress", raw_text="现在进度如何？", expected_active_operation_id="parent"))
            check(progress["role"] == "LOCAL_STATUS" and progress["model_calls"] == 0)
            check(scene.operation() == status_before and scene.sdk.post_count("/prompt_async") == 1)
            pending = scene.state()["pending_permissions" if kind == "PERMISSION" else "pending_questions"][0]
            reply = scene.body("reply", request_ref=pending["request_ref"], **({"reply": "once"} if kind == "PERMISSION" else {"answers": [["Yes"]]}))
            await scene.owner.reply(scene.pid, scene.sid, pending["request_ref"]["id"], reply, kind=kind)
            await scene.inspect()
            resumed = scene.operation()
            check(resumed["human_wait"]["status"] == "RUNNING" and resumed["deadline"] > Clock.now(timezone.utc).isoformat())
            check(datetime.fromisoformat(resumed["deadline"]) <= datetime.fromisoformat(expires))
            check(resumed["human_wait"]["completed_wait_seconds"] == 11)
            scene.sdk.terminal(accepted["native_message_id"], error=False)
            await scene.inspect()
            check(scene.operation()["status"] == "RETURNED" and scene.state()["active_operation_id"] is None)
            check(scene.context["expires_at"] == expires and scene.request["max_calls"] == 24 and scene.request["max_output_tokens"] == 1024)
        finally:
            await scene.close()
    asyncio.run(run())


@pytest.mark.parametrize("mismatch", ["child_request", "child_busy", "parallel_tool", "wrong_parent", "wrong_call", "duplicate_request", "part_session", "part_message"])
def test_unrelated_or_parallel_wait_never_pauses_parent_deadline(tmp_path, clock, mismatch):
    async def run():
        scene = Scene(tmp_path)
        try:
            sent = await scene.send()
            assistant, request = scene.sdk.tool_wait(sent["native_message_id"])
            if mismatch in {"child_request", "child_busy"}:
                scene.sdk.children = [{"id": "ses_child", "parentID": scene.sdk.native, "directory": str(scene.workspace)}]
                scene.sdk.statuses["ses_child"] = {"type": "busy"}
                if mismatch == "child_request":
                    request["sessionID"] = "ses_child"
            elif mismatch == "parallel_tool":
                assistant["parts"].append({"id": "prt_parallel", "type": "tool", "callID": "call_working",
                                           "sessionID": scene.sdk.native, "messageID": assistant["info"]["id"],
                                           "state": {"status": "running"}})
            elif mismatch == "wrong_parent":
                assistant["info"]["parentID"] = "msg_unrelated_user"
            elif mismatch == "wrong_call":
                request["tool"]["callID"] = "call_unrelated"
            elif mismatch == "part_session":
                assistant["parts"][0]["sessionID"] = "ses_unrelated"
            elif mismatch == "part_message":
                assistant["parts"][0]["messageID"] = "msg_unrelated"
            else:
                duplicate = deepcopy(request)
                duplicate["id"] = "per_duplicate"
                scene.sdk.requests.append(duplicate)
            await scene.inspect()
            check(scene.operation().get("human_wait", {}).get("status") != "WAITING_FOR_USER")
            if mismatch in {"part_session", "part_message"}:
                # A part carrying foreign ownership invalidates this observation before its deadline.
                check(scene.operation()["status"] == "UNKNOWN_OUTCOME")
                check(scene.operation()["reason"] == "ORIGINAL_OBSERVATION_UNAVAILABLE_NO_POST_REPLAY")
                check(scene.state()["observation_error"]["error_type"] == "Conflict")
                check(scene.sdk.post_count("/abort") == 0 and scene.sdk.post_count("/prompt_async") == 1)
            else:
                clock.advance(11)
                scene.monitor()
                await until(lambda: scene.operation()["status"] == "UNKNOWN_OUTCOME")
                check(scene.sdk.post_count("/abort") == 1 and scene.sdk.post_count("/prompt_async") == 1)
                check(scene.operation()["reason"] == "MESSAGE_DEADLINE_ABORT_REQUESTED")
        finally:
            await scene.close()
    asyncio.run(run())


async def unknown_after_deadline(scene):
    sent = await scene.send()
    Clock.advance(11)
    task = scene.monitor()
    await until(lambda: scene.operation()["status"] == "UNKNOWN_OUTCOME" and scene.sdk.abort_started.is_set()
                and not scene.context.get("native_commands_inflight", 0))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return sent


def test_acknowledged_exact_terminal_recovers_slot_with_separate_durable_evidence_unknown_unchanged(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            sent = await unknown_after_deadline(scene)
            original = scene.operation()
            await scene.inspect()  # Idle without exact terminal must remain blocked.
            check(scene.state()["active_operation_id"] == "parent" and scene.operation() == original)
            stopped = await scene.stop()
            check(stopped["original_abort_acknowledged"] and scene.context["interrupt_barrier"]["http_finished"])
            scene.sdk.terminal(sent["native_message_id"])
            recovered = await scene.inspect()
            check(recovered["active_operation_id"] is None and not scene.context.get("interrupt_barrier"))
            check(scene.operation() == original, "Historical UNKNOWN was rewritten")
            proof = scene.owner._read(scene.owner._path(scene.pid, scene.sid, "recoveries", "parent.json"))
            check(proof["original_operation_sha256"] == digest(original) and proof["stop_operation_id"] == "stop")
            check(proof["original_status"] == "UNKNOWN_OUTCOME" and proof["automatic_replay"] is False)
            await scene.inspect()
            check(scene.operation() == original and scene.state()["last_operation_recovery"]["sha256"] == digest(proof))
            restarted = scene.new_controller()
            check(restarted.operation(scene.pid, scene.sid, "parent") == original)
            check(restarted.get(scene.pid, scene.sid)["last_operation_recovery"] == recovered["last_operation_recovery"])
            next_send = await scene.send("explicit-new")
            check(next_send["status"] == "ACCEPTED" and scene.sdk.post_count("/prompt_async") == 2)
            check(await scene.owner.send(scene.pid, scene.sid, original["request"]) == original)
            check(scene.sdk.post_count("/prompt_async") == 2, "Unknown original POST was replayed")
            await scene.inspect()  # Late old terminal cannot release the new turn.
            check(scene.state()["active_operation_id"] == "explicit-new")
        finally:
            await scene.close()
    asyncio.run(run())


@pytest.mark.parametrize("insufficient", ["unacknowledged", "idle_only", "wrong_terminal", "foreign_user", "child_busy", "pending", "unfinished_tool", "unacknowledged_guidance"])
def test_unknown_requires_exact_dispatch_terminal_and_scope_evidence(tmp_path, clock, insufficient):
    async def run():
        scene = Scene(tmp_path)
        try:
            if insufficient == "unacknowledged":
                scene.sdk.fail_prompt = True
                with pytest.raises(httpx.ReadTimeout):
                    await scene.send()
                sent = scene.operation()
            else:
                sent = await unknown_after_deadline(scene)
            original = scene.operation()
            await scene.stop()
            if insufficient != "idle_only":
                scene.sdk.terminal("msg_wrong_parent" if insufficient == "wrong_terminal" else sent["native_message_id"])
            if insufficient == "foreign_user":
                scene.sdk.messages.append({"info": {"id": "msg_unowned_user", "role": "user", "sessionID": scene.sdk.native}, "parts": []})
            elif insufficient == "child_busy":
                scene.sdk.children = [{"id": "ses_child", "parentID": scene.sdk.native, "directory": str(scene.workspace)}]
                scene.sdk.statuses["ses_child"] = {"type": "busy"}
            elif insufficient == "pending":
                scene.sdk.requests = [{"id": "per_unresolved", "sessionID": scene.sdk.native, "permission": "read", "patterns": ["owned.txt"]}]
            elif insufficient == "unfinished_tool":
                scene.sdk.messages[-1]["parts"] = [{"type": "tool", "callID": "call_unresolved", "state": {"status": "running"}}]
            elif insufficient == "unacknowledged_guidance":
                # A crash/timeout may persist a GUIDANCE intent without a 204.
                body = scene.body("lost-guide", raw_text="A separately admitted UNIT guidance turn", intent="STEER", expected_active_operation_id="parent")
                scene.owner._claim(scene.pid, scene.sid, "lost-guide", "GUIDANCE", body)
                scene.owner._finish(scene.pid, scene.sid, "lost-guide", "UNKNOWN_OUTCOME", generation=3,
                    original_session_id=scene.sdk.native, original_active_operation_id="parent", native_message_id="msg_lost_guide")
                scene.owner._update(scene.pid, scene.sid, guidance_cohort_operation_id="parent", guidance_operation_ids=["lost-guide"])
            await scene.inspect()
            check(scene.operation() == original and scene.state()["active_operation_id"] == "parent")
            check(scene.context.get("interrupt_barrier"), "Insufficient proof released stop barrier")
            with pytest.raises(Conflict):
                await scene.send("must-not-dispatch")
            check(scene.sdk.post_count("/prompt_async") == 1)
            check(not scene.owner._path(scene.pid, scene.sid, "recoveries", "parent.json").exists())
        finally:
            await scene.close()
    asyncio.run(run())


def test_inflight_stop_and_crossing_idle_observation_cannot_release_unknown(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            sent = await unknown_after_deadline(scene)
            original = scene.operation()
            scene.sdk.terminal(sent["native_message_id"])
            scene.sdk.hold_abort = True
            scene.sdk.abort_started.clear()
            stopping = asyncio.create_task(scene.stop())
            scene.tasks.append(stopping)
            await scene.sdk.abort_started.wait()
            await scene.inspect()
            check(scene.state()["active_operation_id"] == "parent" and scene.context["native_commands_inflight"] == 1)
            with pytest.raises(Conflict):
                await scene.send("blocked-during-stop")
            scene.sdk.abort_release.set()
            await stopping
            await scene.inspect()
            check(scene.state()["active_operation_id"] is None and scene.operation() == original)
            check(scene.sdk.post_count("/prompt_async") == 1)
        finally:
            await scene.close()
    asyncio.run(run())


def test_hard_owner_ttl_expires_even_while_exact_human_wait_is_paused(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            sent = await scene.send()
            scene.sdk.tool_wait(sent["native_message_id"])
            await scene.inspect()
            check(scene.operation()["human_wait"]["status"] == "WAITING_FOR_USER")
            expires = scene.context["expires_at"]
            clock.advance(601)
            await scene.monitor()
            check(scene.state()["owner_status"] == "EXPIRED" and scene.context["process"].terminated == 1)
            check(scene.context["expires_at"] == expires and scene.operation()["status"] == "UNKNOWN_OUTCOME")
            check(scene.sid not in scene.owner.active and scene.sdk.post_count("/prompt_async") == 1)
            with pytest.raises(Conflict):
                await scene.send("expired-must-not-dispatch")
        finally:
            await scene.close()
    asyncio.run(run())


def test_ambiguous_dispatch_requires_explicit_owner_disconnect_preserves_unknown_and_budgets(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            scene.sdk.fail_prompt = True
            with pytest.raises(httpx.ReadTimeout):
                await scene.send()
            original = scene.operation()
            view = await scene.owner.controlled_disconnect(scene.pid, scene.sid, scene.body("disconnect-owner"))
            check(scene.context["process"].terminated == 1 and not view["connected_owner"])
            check(scene.operation() == original and scene.sid not in scene.owner.active)
            check(view["last_control_operation"]["status"] == "RETURNED")
            check(scene.state()["model_calls_reserved"] == 0 and scene.state()["native_turns_started"] == 0)
            check(scene.request["ttl_seconds"] == 600 and scene.request["max_calls"] == 24 and scene.request["max_output_tokens"] == 1024)
            check(scene.sdk.post_count("/prompt_async") == 1)
        finally:
            await scene.close()
    asyncio.run(run())


def test_late_observation_from_old_owner_cannot_replace_new_generation_or_new_task(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            sent = await scene.send()
            scene.sdk.terminal(sent["native_message_id"])
            scene.sdk.hold_permission_get = True
            observing = asyncio.create_task(scene.inspect())
            scene.tasks.append(observing)
            await scene.sdk.permission_get_started.wait()
            # Controlled owner boundary has changed while an old readonly GET
            # was in flight. No production method is replaced or invoked on a
            # guessed process; only the SDK/process facets are synthetic here.
            next_context = {**scene.context, "generation": 4, "lock": asyncio.Lock()}
            scene.owner.active[scene.sid] = next_context
            scene.owner._finish(scene.pid, scene.sid, "parent", "UNKNOWN_OUTCOME", reason="UNIT_OWNER_REPLACED")
            scene.owner._update(scene.pid, scene.sid, generation=4, active_operation_id=None, native_status="idle")
            accepted = await scene.owner.send(scene.pid, scene.sid,
                scene.body("new-owner-task", generation=4, raw_text="New owner's original operation", timeout_seconds=10))
            check(accepted["status"] == "ACCEPTED" and accepted["generation"] == 4)
            expected = scene.state()
            scene.sdk.permission_get_release.set()
            await observing
            check(scene.state() == expected, "Old readonly observation overwrote the new owner/task")
            await scene.owner._settle(scene.pid, scene.sid, scene.context, scene.sdk.messages)
            check(scene.state() == expected)
        finally:
            await scene.close()
    asyncio.run(run())


def test_loading_live_owner_is_rejected_without_losing_accepted_parent(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            await scene.send()
            original = scene.operation()
            with pytest.raises(Conflict, match="Disconnect the exact previous"):
                await scene.owner.load(scene.pid, scene.sid, {"operation_id": "not-a-disconnect", "session_ref": scene.ref})
            check(scene.operation() == original and scene.state()["active_operation_id"] == "parent")
            check(scene.sdk.post_count("/prompt_async") == 1)
        finally:
            await scene.close()
    asyncio.run(run())


def test_late_dispatch_ack_after_cancelled_owner_does_not_rewrite_unknown_or_new_task(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            scene.sdk.hold_prompt = True
            sending = asyncio.create_task(scene.send())
            scene.tasks.append(sending)
            await scene.sdk.prompt_started.wait()
            await scene.owner.controlled_disconnect(scene.pid, scene.sid, scene.body("disconnect-inflight"))
            original = scene.operation()
            check(original["status"] == "UNKNOWN_OUTCOME")
            # External owner boundary now represents a later admitted generation;
            # no new process is launched by this UNIT fixture.
            next_context = {**scene.context, "closing": False, "generation": 4, "lock": asyncio.Lock()}
            scene.owner.active[scene.sid] = next_context
            scene.owner._update(scene.pid, scene.sid, owner_status="READY", generation=4,
                                active_operation_id="new-owner-task", native_status="busy")
            expected = scene.state()
            scene.sdk.prompt_release.set()
            with pytest.raises(Conflict, match="dispatch owner changed"):
                await sending
            check(scene.operation() == original and scene.state() == expected)
        finally:
            await scene.close()
    asyncio.run(run())


def test_exact_terminal_after_deadline_abort_needs_no_additional_stop_or_native_dispatch(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            sent = await unknown_after_deadline(scene)
            original = scene.operation()
            scene.sdk.terminal(sent["native_message_id"])
            await scene.inspect()
            check(scene.state()["active_operation_id"] is None and scene.operation() == original)
            proof = scene.owner._read(scene.owner._path(scene.pid, scene.sid, "recoveries", "parent.json"))
            check(proof["stop_operation_id"] is None and proof["original_status"] == "UNKNOWN_OUTCOME")
            check(scene.sdk.post_count("/abort") == 1 and scene.sdk.post_count("/prompt_async") == 1)
        finally:
            await scene.close()
    asyncio.run(run())


def test_explicit_stop_during_exact_human_wait_settles_only_matching_cancelled_parent(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            sent = await scene.send()
            scene.sdk.tool_wait(sent["native_message_id"])
            await scene.inspect()
            check(scene.operation()["human_wait"]["status"] == "WAITING_FOR_USER")
            await scene.stop()
            await scene.inspect()
            check(scene.state()["active_operation_id"] == "parent" and scene.context.get("interrupt_barrier"))
            scene.sdk.terminal(sent["native_message_id"])
            await scene.inspect()
            check(scene.operation()["status"] == "CANCELLED" and scene.state()["active_operation_id"] is None)
            check(not scene.context.get("interrupt_barrier") and scene.sdk.post_count("/prompt_async") == 1)
        finally:
            await scene.close()
    asyncio.run(run())


def test_other_live_controller_load_cannot_poison_existing_parent_or_take_actor(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            await scene.send()
            original = scene.operation()
            # This is the actual current process/birth identity, not a fabricated
            # alive PID. A second service instance has no right to this owner.
            scene.owner._update(scene.pid, scene.sid, controller_identity=scene.owner._identity)
            other = scene.new_controller()
            with pytest.raises(Conflict, match="Disconnect the exact previous"):
                await other.load(scene.pid, scene.sid, {"operation_id": "cannot-steal-owner", "session_ref": scene.ref})
            check(scene.operation() == original and scene.state()["active_operation_id"] == "parent")
            check(scene.sdk.post_count("/prompt_async") == 1)
        finally:
            await scene.close()
    asyncio.run(run())


@pytest.mark.parametrize("abort_result", [False, "TIMEOUT"])
def test_unconfirmed_original_deadline_abort_blocks_new_turn_even_after_second_stop_ack(tmp_path, clock, abort_result):
    async def run():
        scene = Scene(tmp_path)
        try:
            scene.sdk.abort_result = abort_result
            sent = await unknown_after_deadline(scene)
            original = scene.operation()
            key = digest("parent") + ".json"
            intent = scene.owner._read(scene.owner._path(scene.pid, scene.sid, "deadline-stops", key))
            result = scene.owner._read(scene.owner._path(scene.pid, scene.sid, "deadline-stop-results", key))
            check(intent["dispatch_attempted"] is True and intent["native_abort_acknowledged"] is False)
            check(intent["parent_operation_sha256"] == digest(original) and result["intent_sha256"] == digest(intent))
            check(result["status"] == "UNKNOWN_OUTCOME" and result["native_abort_acknowledged"] is False)
            check(result["error_type"] == ("ReadTimeout" if abort_result == "TIMEOUT" else None))
            scene.sdk.terminal(sent["native_message_id"])
            await scene.inspect()
            check(scene.state()["native_status"] == "idle" and scene.state()["active_operation_id"] == "parent")
            with pytest.raises(Conflict):
                await scene.send("cannot-start-during-old-abort")
            # A different successful abort cannot acknowledge/drain the first
            # timed-out request. Its exact old intent/result stay immutable.
            scene.sdk.abort_result = True
            stopped = await scene.stop("second-explicit-stop")
            check(stopped["original_abort_acknowledged"] is True and scene.sdk.late_abort_pending)
            await scene.inspect()
            with pytest.raises(Conflict):
                await scene.send("cannot-start-after-second-ack")
            check(scene.state()["active_operation_id"] == "parent" and scene.context.get("interrupt_barrier"))
            check(scene.operation() == original and scene.sdk.post_count("/prompt_async") == 1)
            check(scene.owner._read(scene.owner._path(scene.pid, scene.sid, "deadline-stop-results", key)) == result)
            view = await scene.owner.controlled_disconnect(scene.pid, scene.sid, scene.body("disconnect-uncertain-stop"))
            check(scene.context["process"].terminated == 1 and not view["connected_owner"])
            check(scene.operation() == original, "Owner lifecycle recovery rewrote the archived UNKNOWN")
        finally:
            await scene.close()
    asyncio.run(run())


def test_same_monitor_survives_lost_stop_ack_until_hard_ttl_disposes_exact_owner(tmp_path, clock):
    async def run():
        scene = Scene(tmp_path)
        try:
            scene.sdk.abort_result = "TIMEOUT"
            await scene.send()
            expires = scene.context["expires_at"]
            clock.advance(11)
            monitor = scene.monitor()
            result_path = scene.owner._path(scene.pid, scene.sid, "deadline-stop-results", digest("parent") + ".json")
            await until(result_path.exists)
            result = scene.owner._read(result_path)
            check(result["native_abort_acknowledged"] is False and not monitor.done())
            original = scene.operation()
            check(original["status"] == "UNKNOWN_OUTCOME" and scene.context["process"].terminated == 0)
            clock.advance(590)  # Same immutable 600-second owner; no new lease/monitor.
            await asyncio.wait_for(monitor, timeout=5)
            check(scene.state()["owner_status"] == "EXPIRED" and scene.sid not in scene.owner.active)
            check(scene.context["process"].terminated == 1 and scene.context["expires_at"] == expires)
            check(scene.operation() == original and scene.sdk.post_count("/prompt_async") == 1)
        finally:
            await scene.close()
    asyncio.run(run())
