"""Actual pinned CLI/official stdio SDK; only the external model is replaced."""
import asyncio
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

ROOT = Path(os.environ.get("EEAS_ROOT_SOURCE", str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(ROOT))
SOURCE = Path(os.environ.get("EEAS_DESKTOP_TRANSPORT_SOURCE",
    Path(__file__).parents[2] / "packages/adapters/opencode_desktop.py"))
if not SOURCE.is_file(): SOURCE = Path(__file__).parents[3] / "packages/adapters/opencode_desktop.py"
spec = importlib.util.spec_from_file_location("packages.adapters.opencode_desktop", SOURCE)
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.domain.desktop_intents import DesktopIntents, DesktopIntentCandidate
from packages.domain.store import Store, Conflict
from packages.domain.tasks import canonical, digest
from packages.domain.workspace_desktop import WorkspaceDesktop

Transport = module.OpenCodeDesktopTransport
KEY = "desktop-original-wire-only"
MODEL = "desktop-original-wire-v1"
TOKEN = "a" * 64
CLI = ROOT / "var/opencode-ui-offline-guard/opencode"


class OriginalDesktopWire:
    def __init__(self, behavior="tool"):
        self.behavior = behavior; self.requests = []; self.receipts = []; self.requested = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append({"body": body, "auth_matches": self.headers.get("Authorization") == "Bearer " + KEY})
                owner.requested.set()
                if owner.behavior == "wait":
                    time.sleep(4)
                names = [t["function"]["name"] for t in body["tools"]]
                tool_messages = [m for m in body["messages"] if m["role"] == "tool"]
                if owner.behavior in {"intent", "wait"}:
                    output = {"intent": "OBSERVE", "goal": "Read current page", "instruction_span": None,
                              "allowed_effects": [], "question": None}
                    name, arguments = "StructuredOutput", output
                elif not tool_messages or (owner.behavior == "budget" and len(owner.requests) == 2):
                    name = next(x for x in names if x.endswith("_invoke"))
                    arguments = {"method": "missing" if owner.behavior == "wrong-method" else "read_message", "arguments": {}}
                else:
                    content = tool_messages[-1]["content"]
                    if isinstance(content, list): content = "\n".join(str(x.get("text", "")) for x in content)
                    if owner.behavior in {"wrong-method", "budget"}:
                        output = {"observed_sha256": "0" * 64, "text": "Rejected unknown method"}
                    else:
                        value = json.loads(content)
                        owner.receipts.append(value)
                        output = {"observed_sha256": value["result"]["raw_text_sha256"], "text": value["result"]["raw_text"]}
                    name, arguments = "StructuredOutput", output
                chunks = [
                    {"id": "chatcmpl-desktop", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                     "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0,
                       "id": "call_" + str(len(owner.requests)), "type": "function",
                       "function": {"name": name, "arguments": canonical(arguments)}}]}, "finish_reason": None}]},
                    {"id": "chatcmpl-desktop", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                     "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60}}]
                raw = ("".join("data: " + canonical(x) + "\n\n" for x in chunks) + "data: [DONE]\n\n").encode()
                self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw))); self.end_headers()
                try: self.wfile.write(raw); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError): pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    @property
    def url(self): return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
    def start(self): self.thread.start(); return self
    def close(self): self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


def scene(tmp_path, wire, *, mode="OBSERVE", methods=None, text="  当前页面是什么？🙂\r\n"):
    assert CLI.is_file() and hashlib.sha256(CLI.read_bytes()).hexdigest() == CLI_SHA[os.name]
    store = Store(tmp_path / "db.sqlite3")
    intents = DesktopIntents(WorkspaceDesktop(tmp_path / "ws", token=TOKEN, owner_pid=123, platform="posix"))
    message = intents.record_message({"operation_id": "actual-message", "raw_text": text}, host_token=TOKEN)
    profile = {"schema": "eeas.opencode-provider.v1", "id": "desktop-wire", "revision": 1,
        "version": VERSION, "source_commit": COMMIT, "executable": str(CLI), "executable_sha256": CLI_SHA[os.name],
        "provider_id": "eeas-desktop-wire", "protocol": "OPENAI_COMPATIBLE_CHAT_COMPLETIONS",
        "provider_url": wire.url, "model_id": MODEL, "secret_ref": "secret:desktop-wire", "transport_kind": "MOCK_WIRE"}
    registry = OpenCodeProviderRegistry(lambda *_: copy.deepcopy(profile), lambda *_: KEY)
    native = Transport(store, tmp_path, provider_registry=registry, message_reader=intents.message)
    schema = {"type": "object", "properties": {"observed_sha256": {"type": "string"}, "text": {"type": "string"}},
              "required": ["observed_sha256", "text"], "additionalProperties": False}
    request = {"project_id": None, "operation_id": "desktop-operation", "prompt": text,
        "mode": mode, "desktop_intent": {"user_message_ref": message["message_ref"], "context_ref": None},
        "provider_profile_ref": {"id": profile["id"], "revision": 1, "sha256": digest(profile)},
        "budget": {"max_calls": 3, "timeout_seconds": 30, "max_output_tokens": 2048, "max_tool_calls": 3},
        "output_schema": DesktopIntentCandidate.model_json_schema() if mode == "INTENT" else schema,
        "method_ids": ["read_message"] if methods is None else methods}
    return native, request, store, intents


def evidence(name, native, row, wire):
    destination = os.environ.get("EEAS_DESKTOP_TRANSPORT_EVIDENCE")
    if not destination: return
    directory = Path(destination) / name; directory.mkdir(parents=True, exist_ok=True)
    (directory / "receipt.json").write_text(canonical({"row": row, "events": native.stream_events(row["project_id"], row["id"]),
        "wire_requests": wire.requests, "wire_tool_receipts": wire.receipts,
        "qualification": "ACTUAL_ORIGINAL_CLI_OFFICIAL_STDIO_MCP_EXTERNAL_MODEL_FIXTURE_ONLY",
        "native_input": "NOT_RUN", "paid_model_calls": 0}).replace(KEY, "<fixture-key-redacted>"))
    for filename in ["native-events.jsonl", "http-starts.jsonl", "http-responses.jsonl",
                     "server-stdout.bin", "server-stderr.bin", "desktop-native-result.json"]:
        path = Path(row["workspace"]) / filename
        if path.is_file(): (directory / filename).write_bytes(path.read_bytes().replace(KEY.encode(), b"<fixture-key-redacted>"))


def test_desktop_owned_mcp_starts_cache_free_before_the_fixed_bridge_without_dispatching_a_model(tmp_path):
    from types import SimpleNamespace
    native,request,_store,_intents=scene(tmp_path,SimpleNamespace(url='http://127.0.0.1:9/v1'))
    try:
        profile=native.provider_registry.resolve(request['provider_profile_ref'])
        config=native._config(profile,request,SimpleNamespace(base_url='http://127.0.0.1:9/v1',key='fixture-relay'),
            SimpleNamespace(url='http://127.0.0.1:9',key='fixture-broker',row={'id':'unstarted-own-operation','request_hash':'a'*64}))
        command=config['mcp'][module.MCP_NAME]['command']
        assert command==[sys.executable,'-B',str(native.mcp_script)]
        assert native.active=={}
    finally:
        asyncio.run(native.close())


def test_original_cli_executes_official_mcp_read_of_exact_real_message_without_material_or_business_project(tmp_path):
    wire = OriginalDesktopWire().start()
    native, request, store, intents = scene(tmp_path, wire)
    try:
        row = native.prepare(request)
        assert row["execution_status"] == "QUEUED" and wire.requests == []
        assert row["request_hash"] == digest(json.loads(row["request"]))
        replay = native.prepare(request); assert replay["id"] == row["id"]
        result = asyncio.run(native.start(row["project_id"], row["id"])); evidence("actual-read-message", native, result, wire)
        assert result["execution_status"] == "RETURNED", canonical(result)
        parsed = json.loads(result["result"])
        assert parsed["tool_calls"] == 1 and parsed["candidate_json"]["text"] == request["prompt"]
        assert parsed["candidate_json"]["observed_sha256"] == hashlib.sha256(request["prompt"].encode()).hexdigest()
        assert len(wire.requests) == 2 and all(x["auth_matches"] for x in wire.requests)
        assert wire.receipts[0]["receipt"]["execution_id"] == row["id"]
        assert wire.receipts[0]["receipt"]["request_sha256"] == row["request_hash"]
        assert not parsed["task_completed"] and not intents.get()["capabilities"]["single_task_input_ready"]
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM task_runs").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
        again = asyncio.run(native.start(row["project_id"], row["id"]))
        assert again["id"] == row["id"] and len(wire.requests) == 2
        assert any(x["payload"]["type"] == "desktop_tool_completed" for x in native.stream_events(row["project_id"], row["id"]))
    finally:
        asyncio.run(native.close()); wire.close()


def test_original_zero_material_intent_can_be_interpreted_by_actual_domain_without_native_grant(tmp_path):
    wire = OriginalDesktopWire("intent").start(); native, request, store, intents = scene(tmp_path, wire, mode="INTENT", methods=[])
    try:
        row = native.prepare(request); row = asyncio.run(native.start(row["project_id"], row["id"]))
        evidence("actual-intent", native, row, wire)
        assert row["execution_status"] == "RETURNED", canonical(row)
        intents.transport = native
        ref = {"project_id": row["project_id"], "execution_id": row["id"], "request_sha256": row["request_hash"]}
        interpreted = intents.interpret("original-interpret", request["desktop_intent"]["user_message_ref"], ref, host_token=TOKEN)
        accepted = intents.authorize("original-observe", interpreted["interpretation_ref"], host_token=TOKEN)
        assert interpreted["intent"] == accepted["status"] == "OBSERVE"
        assert accepted["owner"] == "USER" and not accepted["mutation_authority"]
        assert len(wire.requests) == 1
    finally: asyncio.run(native.close()); wire.close()


def test_queued_cancel_never_dispatches_original_loop(tmp_path):
    wire = OriginalDesktopWire().start(); native, request, _, _ = scene(tmp_path, wire)
    try:
        row = native.prepare(request)
        cancelled = asyncio.run(native.request_cancel(row["project_id"], row["id"]))
        assert cancelled["execution_status"] == "CANCELLED" and cancelled["cancel_requested"]
        assert asyncio.run(native.start(row["project_id"], row["id"]))["execution_status"] == "CANCELLED"
        assert wire.requests == []
    finally: asyncio.run(native.close()); wire.close()


def test_actual_original_running_prompt_cancel_preserves_receipt_and_no_native_input(tmp_path):
    wire = OriginalDesktopWire("wait").start(); native, request, _, _ = scene(tmp_path, wire, mode="INTENT", methods=[])
    async def run():
        try:
            row = native.prepare(request)
            task = asyncio.create_task(native.start(row["project_id"], row["id"]))
            # Wait within the existing task budget for actual upstream entry;
            # a fixed 9s also included variable original CLI cold startup.
            deadline = time.monotonic() + request["budget"]["timeout_seconds"]
            while not wire.requested.is_set() and not task.done() and time.monotonic() < deadline:
                await asyncio.sleep(.03)
            assert wire.requested.is_set()
            cancelled = await native.request_cancel(row["project_id"], row["id"])
            await asyncio.gather(task, return_exceptions=True)
            evidence("actual-running-cancel", native, cancelled, wire)
            assert cancelled["execution_status"] == "CANCELLED" and cancelled["session_id"].startswith("ses_")
            assert len(wire.requests) == 1 and not native.active
            assert any(x["payload"]["type"] == "opencode_abort_response" for x in native.stream_events(row["project_id"], row["id"]))
        finally:
            await native.close()
    try: asyncio.run(run())
    finally: wire.close()


def test_repeated_cancel_of_caller_waits_for_actual_owned_cli_and_relay_cleanup(tmp_path):
    wire = OriginalDesktopWire("wait").start()
    native, request, _, _ = scene(tmp_path, wire, mode="INTENT", methods=[])
    entered, release = threading.Event(), threading.Event()

    async def run():
        try:
            row = native.prepare(request)
            task = asyncio.create_task(native.start(row["project_id"], row["id"]))
            deadline = time.monotonic() + request["budget"]["timeout_seconds"]
            while not wire.requested.is_set() and not task.done() and time.monotonic() < deadline:
                await asyncio.sleep(.03)
            assert wire.requested.is_set()
            context = native.active[row["id"]]
            process, relay = context["process"], context["relay"]
            close = relay.close

            def held_close():
                entered.set()
                release.wait()
                close()

            relay.close = held_close
            assert task.cancel()
            assert await asyncio.to_thread(entered.wait, 5)
            assert task.cancel()
            await asyncio.sleep(.05)
            assert not task.done() and native.active[row["id"]] is context
            assert native._owners and process.returncode is None
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            assert task.cancelled() and not native.active and not native._owners
            assert process.returncode is not None and relay.close_confirmed
            assert not relay.owned_tasks and not any(t.is_alive() for t in relay.owned_threads)
            assert len(wire.requests) == 1
            assert native.inspect(row["project_id"], row["id"])["execution_status"] == "UNKNOWN_OUTCOME"
        finally:
            release.set()
            await native.close()

    try:
        asyncio.run(run())
    finally:
        release.set()
        wire.close()


def test_actual_mcp_unknown_method_is_rejected_without_callback(tmp_path):
    wire = OriginalDesktopWire("wrong-method").start(); native, request, _, _ = scene(tmp_path, wire)
    try:
        row = native.prepare(request); row = asyncio.run(native.start(row["project_id"], row["id"]))
        evidence("actual-unknown-method", native, row, wire)
        events = native.stream_events(row["project_id"], row["id"])
        assert any(x["payload"]["type"] == "desktop_tool_rejected" and x["payload"]["reason"] == "Unknown method or scope" for x in events)
        assert not any(x["payload"]["type"] == "desktop_tool_completed" for x in events)
    finally: asyncio.run(native.close()); wire.close()


@pytest.mark.parametrize("change", ["unknown-method", "message-sha", "changed-text", "external-ref", "unbounded"])
def test_invalid_scope_refused_before_model_or_native(tmp_path, change):
    wire = OriginalDesktopWire().start(); native, request, store, _ = scene(tmp_path, wire)
    try:
        if change == "unknown-method": request["method_ids"] = ["raw_powershell"]
        elif change == "message-sha": request["desktop_intent"]["user_message_ref"]["sha256"] = "0" * 64
        elif change == "changed-text": request["prompt"] = request["prompt"].strip()
        elif change == "external-ref": request["output_schema"]["properties"]["text"] = {"$ref": "https://example.org/schema"}
        else: request["budget"]["max_calls"] = 999
        with pytest.raises((Conflict, ValueError)): native.prepare(request)
        assert wire.requests == []
        with store.connect() as db: assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
    finally: asyncio.run(native.close()); wire.close()


def test_restart_preserves_original_unknown_outcome_and_never_resumes(tmp_path):
    wire = OriginalDesktopWire().start(); native, request, _, _ = scene(tmp_path, wire)
    try:
        row = native.prepare(request)
        restored, _, _, _ = scene(tmp_path, wire)
        current = restored.inspect(row["project_id"], row["id"])
        assert current["execution_status"] == "UNKNOWN_OUTCOME"
        assert asyncio.run(restored.start(row["project_id"], row["id"]))["execution_status"] == "UNKNOWN_OUTCOME"
        assert wire.requests == []
        asyncio.run(restored.close())
    finally: asyncio.run(native.close()); wire.close()


def test_real_message_record_tamper_between_prepare_and_start_refuses_before_original_dispatch(tmp_path):
    wire = OriginalDesktopWire().start(); native, request, _, intents = scene(tmp_path, wire)
    try:
        row = native.prepare(request)
        path = intents._path("messages", request["desktop_intent"]["user_message_ref"]["id"] + ".json")
        payload = json.loads(path.read_bytes()); payload["payload"]["raw_text"] = "different actual bytes"
        path.write_text(canonical(payload))
        with pytest.raises(Conflict, match="integrity"): asyncio.run(native.start(row["project_id"], row["id"]))
        assert wire.requests == [] and not native.active
    finally: asyncio.run(native.close()); wire.close()


def test_default_native_qualification_false_refuses_action_before_grant_callback_or_model(tmp_path):
    wire = OriginalDesktopWire().start(); native, request, _, _ = scene(tmp_path, wire)
    try:
        row = native.prepare(request)
        with pytest.raises(Conflict, match="not qualified"): native._action_admission(row, {}, None)
        assert wire.requests == [] and not native.active
        assert native.describe_capabilities()["native_input"] == "UNAVAILABLE_UNTIL_ACTUAL_HOST_QUALIFICATION"
    finally: asyncio.run(native.close()); wire.close()


def test_original_loop_tool_attempt_budget_blocks_second_actual_method(tmp_path):
    wire = OriginalDesktopWire("budget").start(); native, request, _, _ = scene(tmp_path, wire)
    try:
        request["budget"]["max_tool_calls"] = 1
        request["budget"]["max_calls"] = 4
        row = native.prepare(request); row = asyncio.run(native.start(row["project_id"], row["id"]))
        evidence("actual-tool-budget", native, row, wire)
        events = native.stream_events(row["project_id"], row["id"])
        assert len([x for x in events if x["payload"]["type"] == "desktop_tool_completed"]) == 1
        assert any(x["payload"].get("reason") == "Tool budget exhausted" for x in events)
        assert json.loads(row["result"])["tool_calls"] == 1 and len(wire.requests) == 3
    finally: asyncio.run(native.close()); wire.close()


def test_concurrent_observers_share_one_original_execution_and_method_scope_cannot_be_mutated_through_public_view(tmp_path):
    wire = OriginalDesktopWire().start(); native, request, _, _ = scene(tmp_path, wire)
    async def run():
        row = native.prepare(request)
        descriptors = native.method_descriptors(json.loads(row["request"]))
        descriptors["methods"][0]["readonly"] = False
        assert native.method_descriptors(json.loads(row["request"]))["methods"][0]["readonly"] is True
        results = await asyncio.gather(native.start(row["project_id"], row["id"]),
                                       native.start(row["project_id"], row["id"]))
        evidence("actual-concurrent", native, results[0], wire)
        assert results[0]["id"] == results[1]["id"] == row["id"]
        assert all(x["execution_status"] == "RETURNED" for x in results)
        assert len(wire.requests) == 2
        await native.close()
    try: asyncio.run(run())
    finally: wire.close()


def test_execution_private_bridge_refuses_wrong_sha_or_execution_while_actual_loop_is_running(tmp_path):
    wire = OriginalDesktopWire("wait").start(); native, request, _, _ = scene(tmp_path, wire, mode="INTENT")
    async def run():
        import httpx
        row = native.prepare(request); task = asyncio.create_task(native.start(row["project_id"], row["id"]))
        for _ in range(400):
            if wire.requested.is_set(): break
            await asyncio.sleep(.025)
        assert wire.requested.is_set()
        context = native.active[row["id"]]; broker = context["broker"]
        headers = {"Authorization": "Bearer " + broker.key, "X-EEAS-Execution": row["id"],
                   "X-EEAS-Request-SHA256": row["request_hash"]}
        async with httpx.AsyncClient(trust_env=False) as client:
            for changed in [{"X-EEAS-Execution": "other-execution"}, {"X-EEAS-Request-SHA256": "0" * 64}]:
                response = await client.post(broker.url + "/invoke", headers=headers | changed,
                                            json={"method": "read_message", "arguments": {}})
                assert response.status_code == 403
        assert context["tool_calls"] == 0
        await native.request_cancel(row["project_id"], row["id"]); await asyncio.gather(task, return_exceptions=True)
        await native.close()
    try: asyncio.run(run())
    finally: wire.close()


def test_private_credential_drift_after_prepare_cannot_silently_change_original_dispatch(tmp_path):
    wire = OriginalDesktopWire().start(); native, request, _, _ = scene(tmp_path, wire)
    try:
        row = native.prepare(request)
        native.provider_registry.secret_resolver = lambda *_: "changed-private-credential"
        with pytest.raises(Conflict, match="scope changed"): asyncio.run(native.start(row["project_id"], row["id"]))
        assert wire.requests == [] and not native.active
    finally: asyncio.run(native.close()); wire.close()


def test_packaged_stdio_bridge_source_drift_is_refused_after_prepare(tmp_path):
    wire = OriginalDesktopWire().start(); native, request, _, _ = scene(tmp_path, wire)
    try:
        bridge = tmp_path / "selected-bridge.py"; bridge.write_bytes(native.mcp_script.read_bytes()); native.mcp_script = bridge
        row = native.prepare(request); bridge.write_bytes(bridge.read_bytes() + b"\n# actual later drift\n")
        with pytest.raises(Conflict, match="scope changed"): asyncio.run(native.start(row["project_id"], row["id"]))
        assert wire.requests == [] and not native.active
    finally: asyncio.run(native.close()); wire.close()


def test_method_execution_metadata_does_not_infer_background_from_cli_or_process_label(tmp_path):
    wire = OriginalDesktopWire().start(); native, request, _, _ = scene(tmp_path, wire)
    try:
        descriptor = copy.deepcopy(native.methods["read_message"][0])
        descriptor.update(id="unverified_cli", route="CLI"); descriptor.pop("execution")
        native.register_method(descriptor, lambda *_: {"not_invoked": True})
        request["method_ids"] = ["unverified_cli"]
        execution = native.method_descriptors(request)["methods"][0]["execution"]
        assert not execution["can_background"] and execution["requires_foreground_input"]
        assert execution["focus_effect"] == "UNKNOWN" and execution["background_qualification"] == "UNQUALIFIED"
        actual = native.methods["read_message"][0]["execution"]
        assert actual["can_background"] and actual["focus_effect"] == "NONE"
        assert actual["background_qualification"] == "QUALIFIED_LOCAL_READONLY"
    finally: asyncio.run(native.close()); wire.close()
