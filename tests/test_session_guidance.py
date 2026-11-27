"""Guidance evidence: real storage units plus actual pinned CLI/wire tests.

UnitOwner substitutes only the native owner/HTTP source and is labelled UNIT.
The final tests use unmodified pinned OpenCode; only its external model is a wire
fixture.  Neither scope qualifies Windows, Pro inference, or model quality.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.agent_sessions.guidance import SessionGuidance
from packages.agent_sessions.service import AgentSessions
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Store, Conflict, InvalidInput
from packages.domain.tasks import canonical
from test_agent_sessions import ModelWire, control, owner, scene, until


def check(condition, message="guidance acceptance failed"):
    if not condition:
        raise AssertionError(message)


class UnitOwner(AgentSessions):
    """UNIT source facade; real SQLite, immutable manifest and operation files."""
    def __init__(self, directory):
        # Guidance and child prompts charge the same original work budget as the
        # service. The fixture supplies the native HTTP responses and owns no provider.
        directory = Path(directory)
        store = Store(directory / "unit.sqlite")
        super().__init__(store, directory, provider_registry=None,
                         workspaces=LocalWorkspaces(store, directory),
                         capabilities=NativeCapabilities(store, directory))
        self.pid = self.store.create_project("原创指导测试", "UNIT native source")['id']
        self.sid = "e" * 32
        self.profile = SimpleNamespace(provider_id="provider", model_id="model")
        self.context = {"lock": asyncio.Lock(), "project_id": self.pid, "session_id": "ses_unit_root", "generation": 3,
                        "profile": self.profile, "expires_at": "2076-01-01T00:00:00+00:00"}
        self.active = {self.sid: self.context}
        self.http_calls, self.http_error, self.available = [], None, True
        manifest = {"id": self.sid, "project_id": self.pid, "request": {"max_calls": 40}}
        self._write(self._path(self.pid, self.sid, "manifest.json"), manifest)
        self.ref = self._manifest(self.pid, self.sid)[1]
        self._write(self._path(self.pid, self.sid, "state.json"),
                    {"generation": 3, "owner_status": "READY", "native_status": "busy", "last_event_seq": 0,
                     "active_operation_id": "first", "model_calls_reserved": 1})
        self._claim(self.pid, self.sid, "first", "MESSAGE", {"raw_text": "first", "agent": "plan"})
        self._finish(self.pid, self.sid, "first", "ACCEPTED", native_message_id="msg_first", generation=3,
                     original_session_id="ses_unit_root")

    def _context(self, pid, sid, ref=None, generation=None, **_kwargs):
        manifest, _ = self._manifest(pid, sid, ref)
        if not self.available or generation != self.context["generation"]:
            raise Conflict("UNIT exact owner unavailable/generation changed")
        return manifest, self.context

    async def _http(self, context, method, path, body=None, **options):
        self.http_calls.append({"method": method, "path": path, "body": deepcopy(body), "options": options})
        if self.http_error:
            raise self.http_error
        return None

    def body(self, key="guide", text="不要修改原件，只补充说明", intent="AUTO", **changes):
        return {"operation_id": key, "session_ref": self.ref, "generation": 3, "raw_text": text,
                "intent": intent, "expected_active_operation_id": "first", **changes}


def native_messages(receipt, *, text=None, finish=None, error=None, session="ses_unit_root"):
    ident = receipt["native_message_id"]
    result = [{"info": {"id": ident, "role": "user", "sessionID": session},
               "parts": [{"type": "text", "text": text if text is not None else receipt["request"]["raw_text"]}]}]
    if finish is not None or error is not None:
        info = {"id": "msg_response", "role": "assistant", "sessionID": session,
                "parentID": ident, "time": {"completed": 1}, "finish": finish}
        if error:
            info["error"] = error
        result.append({"info": info, "parts": []})
    return result


def test_unit_exact_status_is_local_and_requirement_with_progress_is_steer(tmp_path):
    async def run():
        value = UnitOwner(tmp_path / "owner")
        guidance = SessionGuidance(value)
        original = value.operation(value.pid, value.sid, "first")
        result = await guidance.submit(value.pid, value.sid, value.body("status", " 现在进度如何？ "))
        check(result["status"] == "RETURNED" and result["role"] == "LOCAL_STATUS")
        check(result["model_calls"] == 0 and not value.http_calls)
        check(value._state(value.pid, value.sid)["active_operation_id"] == "first")
        check(value.operation(value.pid, value.sid, "first") == original, "local status changed original task/deadline")
        result = await guidance.submit(value.pid, value.sid, value.body("instruction", "把进度显示改成百分比"))
        check(result["intent_resolution"] == "AUTO_AS_STEER" and result["status"] == "ACCEPTED")
        check(len(value.http_calls) == 1 and value.http_calls[0]["body"]["agent"] == "plan")
        with pytest.raises(InvalidInput):
            await guidance.submit(value.pid, value.sid, value.body("bad_status", "进度栏需要重新设计", "STATUS"))
        check(len(value.http_calls) == 1)
    asyncio.run(run())


def test_unit_exact_scope_duplicate_unknown_and_restart_never_repost(tmp_path):
    async def run():
        value = UnitOwner(tmp_path / "owner")
        guidance = SessionGuidance(value)
        value.http_error = TimeoutError("UNIT unknown transport")
        body = value.body()
        with pytest.raises(TimeoutError):
            await guidance.submit(value.pid, value.sid, body)
        record = value.operation(value.pid, value.sid, "guide")
        check(record["status"] == "UNKNOWN_OUTCOME" and record["native_message_id"])
        check(value._state(value.pid, value.sid)["active_operation_id"] == "first")
        check(await SessionGuidance(value).submit(value.pid, value.sid, body) == record)
        # Reopen actual SQLite/files with no native owner; recovery is read-only.
        reopened = UnitOwner.__new__(UnitOwner)
        reopened.__dict__ = dict(value.__dict__)
        reopened.store = Store(value.data_dir / "unit.sqlite")
        reopened.active = {}
        check(await SessionGuidance(reopened).submit(value.pid, value.sid, body) == record)
        check(len(value.http_calls) == 1)
        with pytest.raises(Conflict):
            await guidance.submit(value.pid, value.sid, value.body(text="different input"))
        value.http_error = None
        evidence = await guidance.observe(value.pid, value.sid, value.context, native_messages(record))
        check(value.operation(value.pid, value.sid, "guide")["status"] == "OBSERVED")
        check(evidence["pending_operation_ids"] == ["guide"] and not evidence["unobserved_operation_ids"])
        check(len(value.http_calls) == 1)
        for key, changes in [("stale", {"generation": 2}), ("wrong_active", {"expected_active_operation_id": "other"})]:
            with pytest.raises(Conflict):
                await guidance.submit(value.pid, value.sid, value.body(key, **changes))
        value.available = False
        with pytest.raises(Conflict):
            await guidance.submit(value.pid, value.sid, value.body("revoked", "status"))
        check(len(value.http_calls) == 1)
    asyncio.run(run())


@pytest.mark.parametrize("finish,error,status", [("stop", None, "RETURNED"),
    (None, {"name": "MessageAbortedError"}, "CANCELLED"), (None, {"name": "APIError"}, "FAILED")])
def test_unit_observation_requires_exact_text_parent_and_terminal_evidence(tmp_path, finish, error, status):
    async def run():
        value = UnitOwner(tmp_path / "owner")
        guidance = SessionGuidance(value)
        record = await guidance.submit(value.pid, value.sid, value.body())
        evidence = await guidance.observe(value.pid, value.sid, value.context, native_messages(record, text="different"))
        check(evidence["unobserved_operation_ids"] == ["guide"])
        messages = native_messages(record)
        messages.append({"info": {"id": "msg_foreign_parent", "sessionID": "ses_unit_root", "role": "assistant",
                                  "parentID": "msg_first", "finish": "stop", "time": {"completed": 1}}, "parts": []})
        evidence = await guidance.observe(value.pid, value.sid, value.context, messages)
        check(value.operation(value.pid, value.sid, "guide")["status"] == "OBSERVED")
        check(not evidence["finals"])
        with pytest.raises(Conflict):
            await guidance.observe(value.pid, value.sid, value.context, native_messages(record, session="ses_foreign"))
        evidence = await guidance.observe(value.pid, value.sid, value.context, native_messages(record, finish=finish, error=error))
        check(evidence["finals"][0]["status"] == status and not evidence["pending_operation_ids"])
        check(evidence["latest_guidance_native_message_id"] == record["native_message_id"])
        check(value.operation(value.pid, value.sid, "guide")["semantic_acceptance"] is False)
        before = value.operation(value.pid, value.sid, "guide")
        await guidance.observe(value.pid, value.sid, value.context, native_messages(record, finish=finish, error=error))
        check(value.operation(value.pid, value.sid, "guide") == before, "unchanged poll rewrote terminal receipt")
        check(len(value.http_calls) == 1)
    asyncio.run(run())


def test_unit_bounded_cohort_multiple_guidance_and_old_epoch_is_not_adopted(tmp_path):
    async def run():
        value = UnitOwner(tmp_path / "owner")
        guidance = SessionGuidance(value)
        records = [await guidance.submit(value.pid, value.sid, value.body("guide_" + str(i))) for i in range(32)]
        with pytest.raises(Conflict):
            await guidance.submit(value.pid, value.sid, value.body("guide_over"))
        evidence = await guidance.observe(value.pid, value.sid, value.context, native_messages(records[-1], finish="stop"))
        check(evidence["latest_guidance_native_message_id"] == records[-1]["native_message_id"])
        check(len(evidence["pending_operation_ids"]) == 31 and len(evidence["unobserved_operation_ids"]) == 31)
        check(len(evidence["finals"]) == 1 and len(value.http_calls) == 32)
        value.context["generation"] = 4
        value._update(value.pid, value.sid, generation=4)
        evidence = await guidance.observe(value.pid, value.sid, value.context, native_messages(records[-1], finish="stop"))
        check(evidence["latest_guidance_native_message_id"] is None and not evidence["finals"])
    asyncio.run(run())


def test_unit_three_guidance_cohort_return_and_cache_preserve_unobserved(tmp_path, monkeypatch):
    async def run():
        value = UnitOwner(tmp_path / "owner")
        guidance = SessionGuidance(value)
        records = [await guidance.submit(value.pid, value.sid, value.body("guide_" + str(i))) for i in range(3)]
        value._update(value.pid, value.sid, native_status="idle")
        # First guidance is outside this actual supplied window: never close it by inference.
        messages = native_messages(records[1]) + native_messages(records[2], finish="stop")
        evidence = await guidance.observe(value.pid, value.sid, value.context, messages)
        check(evidence["pending_operation_ids"] == ["guide_0"] and evidence["unobserved_operation_ids"] == ["guide_0"])
        middle = value.operation(value.pid, value.sid, "guide_1")
        check(middle["status"] == "COHORT_RETURNED" and middle["individual_response_observed"] is False)
        check(middle["linked_latest_operation_id"] == "guide_2" and "native_assistant_ids" not in middle)
        public = guidance.public_snapshot(value.pid, value.sid)
        check(len(public) == 3 and all("raw_text" not in canonical(item) for item in public))
        check(not guidance.public_snapshot("foreign", value.sid))
        public[0]["status"] = "MUTATED_COPY"
        check(all(item["status"] != "MUTATED_COPY" for item in guidance.public_snapshot(value.pid, value.sid)))
        original_operation = value.operation
        reads = []
        def counted(*args):
            reads.append(args[-1])
            return original_operation(*args)
        monkeypatch.setattr(value, "operation", counted)
        for _ in range(4):
            check(await guidance.observe(value.pid, value.sid, value.context, messages) == evidence)
        check(not reads, "unchanged observation reread each guidance receipt")
        # Late native user evidence changes the signature and closes only that observed record.
        finished = await guidance.observe(value.pid, value.sid, value.context, native_messages(records[0]) + messages)
        check(not finished["pending_operation_ids"] and original_operation(value.pid, value.sid, "guide_0")["status"] == "COHORT_RETURNED")
        check(reads)
    asyncio.run(run())


def test_unit_guidance_deadline_capped_at_owner_ttl_and_status_does_not_extend(tmp_path):
    async def run():
        value = UnitOwner(tmp_path / "owner")
        guidance = SessionGuidance(value)
        expires = datetime.now(timezone.utc) + timedelta(seconds=25)
        value.context["expires_at"] = expires.isoformat()
        old_deadline = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        value._finish(value.pid, value.sid, "first", "ACCEPTED", deadline=old_deadline)
        result = await guidance.submit(value.pid, value.sid, value.body())
        parent = value.operation(value.pid, value.sid, "first")
        check(result["parent_previous_deadline"] == old_deadline and result["parent_deadline"] == expires.isoformat())
        check(parent["deadline"] == expires.isoformat() and parent["user_guidance_extended_deadline"] is True)
        await guidance.submit(value.pid, value.sid, value.body("query", "status"))
        check(value.operation(value.pid, value.sid, "first") == parent)
    asyncio.run(run())


def test_actual_busy_original_prompt_accepts_guidance_next_native_turn_and_status_is_zero_model(scene):
    wire = ModelWire(["HOLD", "GUIDANCE_NATIVE_NEXT_TURN"]).start()
    # Reuse the existing bounded external fixture gate; never extend the owner budget.
    from test_session_guidance_api import HeldModelGate
    wire.release = HeldModelGate()
    async def run():
        value, request = owner(scene, wire)
        guidance = SessionGuidance(value)
        try:
            opened = await value.open(scene["pid"], request)
            sid = opened["session_ref"]["id"]
            await value.send(scene["pid"], sid, {**control(opened, "first"), "raw_text": "先评估资料，不得改动原件", "agent": "plan"})
            deadline = asyncio.get_running_loop().time() + 10
            while not wire.received.is_set() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(.03)
            check(wire.received.is_set(), "actual first model request was not held")
            active = await value.inspect(scene["pid"], sid)
            check(active["active_operation_id"] == "first")
            status = await guidance.submit(scene["pid"], sid, {**control(opened, "status"), "raw_text": "现在进度如何？",
                "intent": "AUTO", "expected_active_operation_id": "first"})
            check(status["model_calls"] == 0 and len(wire.requests) == 1)
            body = {**control(opened, "guidance"), "raw_text": "补充要求：所有结论需要列出证据；继续保持原件字节不变",
                    "intent": "STEER", "expected_active_operation_id": "first"}
            accepted = await guidance.submit(scene["pid"], sid, body)
            check(accepted["status"] == "ACCEPTED" and len(wire.requests) == 1)
            check(await guidance.submit(scene["pid"], sid, body) == accepted)
            # Observe exact native persistence while the original model is still held.
            ready = await until(value, scene, sid, lambda view: any(item["info"].get("id") == accepted["native_message_id"]
                                for item in view["messages"]))
            observed = await guidance.observe(scene["pid"], sid, value.active[sid], ready["messages"])
            check(observed["pending_operation_ids"] == ["guidance"])
            check(not wire.release.timed_out, "external fixture auto-released before explicit release")
            check(value._state(scene["pid"], sid)["active_operation_id"] == "first" and len(wire.requests) == 1)
            wire.release.set()
            final = await until(value, scene, sid, lambda view: view["native_status"] == "idle" and
                                any(item["info"].get("parentID") == accepted["native_message_id"] and
                                    item["info"].get("time", {}).get("completed") for item in view["messages"]))
            cohort = await guidance.observe(scene["pid"], sid, value.active[sid], final["messages"])
            check(cohort["finals"][0]["status"] == "RETURNED" and not cohort["pending_operation_ids"])
            check(len(wire.requests) == 2 and body["raw_text"] in canonical(wire.requests[1]["body"]["messages"]))
            last_user = next(item for item in reversed(wire.requests[1]["body"]["messages"]) if item["role"] == "user")
            check(body["raw_text"] in canonical(last_user), "new guidance did not reach actual next native LLM input")
            check(scene["actual"].read_bytes() == scene["original"])
            target = os.environ.get("EEAS_SESSION_GUIDANCE_EVIDENCE")
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                Path(target, "actual-guidance.json").write_text(canonical({"qualification": "ACTUAL_PINNED_CLI_EXTERNAL_MODEL_WIRE",
                    "cohort": cohort, "guidance": value.operation(scene["pid"], sid, "guidance"),
                    "model_rpc_count": len(wire.requests), "paid_model_calls": 0,
                    "same_native_session": final["original_session_id"] == opened["original_session_id"],
                    "file_preserved": True, "windows": "NOT_RUN", "pro_inference": "NOT_RUN"}))
        finally:
            wire.release.set()
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()
