"""Actual original read/edit/bash loop; only the external model is a fixture.

The command is genuine stdlib unittest, not a configured exit-code producer.
This scene does not qualify Windows UI, general model reasoning or delivery.
Root runs it with a fresh D-drive --basetemp; all original evidence stays there.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

from packages.agent_sessions import AgentSessions
from packages.domain.local_workspaces import WorkspaceInput
from packages.domain.tasks import canonical
from test_agent_sessions import ModelWire, control, owner, scene, tool


MANUAL_NOTE = "USER_MANUAL_NOTE: cents are integers; retain this explanation."
OLD_LINE = "    return 500 + (extra // 500) * 200"
NEW_LINE = "    return 500 + ((extra + 499) // 500) * 200"
BROKEN = f'''# {MANUAL_NOTE}
def shipping_fee_cents(weight_g):
    if type(weight_g) is not int:
        raise TypeError("weight must be integer grams")
    if weight_g <= 0:
        raise ValueError("weight must be positive")
    if weight_g <= 1000:
        return 500
    extra = weight_g - 1000
{OLD_LINE}
'''
REPAIRED = BROKEN.replace(OLD_LINE, NEW_LINE)
CHECKS = '''import unittest
from calculator import shipping_fee_cents


class ShippingFeeTests(unittest.TestCase):
    # USER_TEST_NOTE: explicit business table, never computed by the subject.
    def test_started_half_kilogram_blocks(self):
        cases = [(1, 500), (999, 500), (1000, 500), (1001, 700),
                 (1499, 700), (1500, 700), (1501, 900), (1999, 900),
                 (2000, 900), (2001, 1100)]
        for weight, expected in cases:
            with self.subTest(weight=weight):
                self.assertEqual(shipping_fee_cents(weight), expected)

    def test_nonpositive_weights_are_rejected(self):
        for weight in (0, -1):
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                shipping_fee_cents(weight)

    def test_noninteger_weights_are_rejected(self):
        for weight in (True, 1.5, "1001", None):
            with self.subTest(weight=weight), self.assertRaises(TypeError):
                shipping_fee_cents(weight)
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(directory, name, value):
    path = directory / name
    with path.open("x", encoding="utf-8") as output:
        output.write(canonical(value) + "\n")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}


def tool_results(body):
    # The external wire must receive the real original tool results. Authored
    # assistant prose or the user's request is never accepted as tool evidence.
    return canonical([m for m in body["messages"] if m.get("role") == "tool"])


class CausalReplies:
    """Gate each next tool on the prior original result and real source bytes.

    ModelWire appends the actual request before indexing this sequence. A gate
    failure ends the fixture model turn, records the failure, and emits no next
    tool. It never repairs files or fabricates command output/exit metadata.
    """
    def __init__(self, wire, folder, command, evidence):
        self.wire, self.folder = wire, folder
        self.evidence, self.events, self.failures = evidence, [], []
        self.replies = [
            tool("read", {"filePath": str(folder / "calculator.py")}),
            tool("read", {"filePath": str(folder / "test_calculator.py")}),
            tool("bash", {"command": command, "workdir": str(folder),
                          "description": "Run actual shipping fee regression before repair", "timeout": 30000}),
            tool("edit", {"filePath": str(folder / "calculator.py"),
                          "oldString": OLD_LINE, "newString": NEW_LINE, "replaceAll": False}),
            tool("bash", {"command": command, "workdir": str(folder),
                          "description": "Rerun identical shipping fee regression after repair", "timeout": 30000}),
            "ORIGINAL_ENGINEERING_UNittest_CHAIN_RETURNED",
        ]

    def __len__(self):
        return len(self.replies)

    def __getitem__(self, number):
        body = self.wire.requests[number]["body"]
        results = tool_results(body)
        event = {"request_index": number, "tool_results_sha256": sha(results.encode()),
                 "implementation_sha256": sha((self.folder / "calculator.py").read_bytes()),
                 "test_sha256": sha((self.folder / "test_calculator.py").read_bytes())}
        try:
            assert (self.folder / "test_calculator.py").read_text() == CHECKS
            expected = BROKEN if number <= 3 else REPAIRED
            assert (self.folder / "calculator.py").read_text() == expected
            if number == 1:
                assert MANUAL_NOTE in results and OLD_LINE.strip() in results
            elif number == 2:
                assert "test_started_half_kilogram_blocks" in results and "(2001, 1100)" in results
            elif number == 3:
                assert "FAILED (failures=" in results and "AssertionError" in results
                assert "weight=1001" in results and "500 != 700" in results
                # Persist the genuine failing tool output before any edit is
                # even offered by the model fixture.
                save(self.evidence, "ORIGINAL_FAILED_MODEL_TOOL_RESULTS.r1.json",
                     [m for m in body["messages"] if m.get("role") == "tool"])
            elif number == 4:
                # Pinned packages/opencode/src/tool/edit.ts returns this text;
                # the replacement belongs to metadata/saved-file diff, not to
                # the model-visible tool body. Real repaired bytes are checked
                # above and the original immutable diff is checked below.
                assert "Edit applied successfully." in results
            elif number == 5:
                assert "Ran 3 tests" in results and "OK" in results
                assert self.wire.requests[number - 1]["body"] != body
            event["gate"] = "PRIOR_ORIGINAL_RESULT_AND_REAL_SOURCE_MATCH"
            self.events.append(event)
            save(self.evidence, f"MODEL_CAUSAL_GATE_{number:02d}.r1.json", event)
            return self.replies[number]
        except Exception as error:
            event.update(gate="FAILED_NO_NEXT_TOOL", error=repr(error))
            self.events.append(event)
            self.failures.append(event)
            save(self.evidence, f"MODEL_CAUSAL_GATE_{number:02d}.FAILED.r1.json", event)
            return "CAUSAL_FIXTURE_GATE_FAILED_NO_FURTHER_TOOL"


def test_actual_original_read_fail_edit_retest_and_saved_history(scene, tmp_path, monkeypatch):
    folder = tmp_path / "original shipping project"
    folder.mkdir()
    implementation, tests = folder / "calculator.py", folder / "test_calculator.py"
    # These are the only harness writes to project source, before original open.
    implementation.write_text(BROKEN, encoding="utf-8")
    tests.write_text(CHECKS, encoding="utf-8")
    bound = scene["workspaces"].bind(scene["pid"], WorkspaceInput(
        idempotency_key="original-shipping-engineering", root=str(folder),
        allowed_write_paths=["calculator.py"]))
    selected = {**scene, "folder": folder, "bound": bound}
    evidence = tmp_path / "engineering-chain-evidence"
    evidence.mkdir()
    python = Path(sys.executable).resolve()
    command = shlex.quote(str(python)) + " -B -m unittest -v test_calculator"
    inputs = {"qualification": "ACTUAL_ORIGINAL_CLI_TOOLS_AND_UNITTEST_ONLY_EXTERNAL_MODEL_FIXTURE",
              "implementation_before_sha256": sha(BROKEN.encode()),
              "implementation_expected_after_sha256": sha(REPAIRED.encode()),
              "readonly_test_sha256": sha(CHECKS.encode()), "manual_note": MANUAL_NOTE,
              "command": command, "python": {"path": str(python), "sha256": sha(python.read_bytes())},
              "workspace": bound, "initial_project_files": sorted(p.name for p in folder.iterdir()),
              "paid_model_calls": 0, "Windows_native": "NOT_RUN", "whole_engineering_delivery": "NOT_ASSESSED"}
    save(evidence, "INPUTS.r1.json", inputs)
    wire = ModelWire([])
    gated = CausalReplies(wire, folder, command, evidence)
    wire.replies = gated
    wire.start()

    async def run():
        value, request = owner(selected, wire)
        request.update(operation_id="open-original-engineering", allowed_effects=["WORKSPACE_WRITE", "BACKGROUND_COMMAND"],
                       command_allowlist=[command], ttl_seconds=240, max_calls=8, max_output_tokens=2048)
        ident, latest, original_process = None, None, None
        permissions = []
        try:
            opened = await value.open(scene["pid"], request)
            save(evidence, "ORIGINAL_OPENED.r1.json", opened)
            ident = opened["session_ref"]["id"]
            original_process = value.active[ident]["process"]
            context = value.active[ident]
            assert context.get("change_review") and context.get("command_observation")
            assert context["command_observation"]["scope"]["source_paths"] == ["calculator.py"]
            assert opened["file_writes"] == "EXPLICIT_WORKSPACE_WRITE_ASK_WITH_CHANGE_REVIEW"
            body = {**control(opened, "original-shipping-repair"), "agent": "build", "timeout_seconds": 180,
                    "raw_text": "Read calculator.py and test_calculator.py. Run the approved regression. "
                    "Fix only the implementation's started-500g rounding defect after observing the genuine failure; "
                    "preserve all comments and tests, rerun the identical command and report the actual outcome."}
            accepted = await value.send(scene["pid"], ident, body)
            save(evidence, "ORIGINAL_SEND_ADMISSION.r1.json", accepted)
            assert accepted["status"] == "ACCEPTED"
            deadline = asyncio.get_running_loop().time() + 180
            handled = set()
            while True:
                latest = await value.inspect(scene["pid"], ident)
                assert latest["original_session_id"] == opened["original_session_id"]
                assert latest["session_ref"] == opened["session_ref"] and latest["generation"] == opened["generation"]
                assert len(wire.requests) <= 8
                assert not latest["pending_questions"], "This bounded engineering fixture never asks a model question"
                for pending in latest["pending_permissions"]:
                    reference = pending["request_ref"]
                    if reference["id"] in handled:
                        continue
                    native = pending["native_request"]
                    action = native["permission"]
                    assert action in {"read", "edit", "bash"} and "once" in pending["allowed_choices"]
                    parts = [p for m in latest["messages"] for p in m["parts"]
                             if p.get("type") == "tool" and p.get("callID") == native["tool"]["callID"]]
                    assert len(parts) == 1
                    original_input = parts[0]["state"]["input"]
                    if action == "bash":
                        assert original_input["command"] == command and native["patterns"] == [command]
                    else:
                        target = Path(original_input["filePath"])
                        assert target in ({implementation} if action == "edit" else {implementation, tests})
                        if action == "edit":
                            assert implementation.read_text() == BROKEN and tests.read_text() == CHECKS
                            assert not gated.failures and any(event["request_index"] == 3 for event in gated.events)
                            assert pending["allowed_choices"] == ["once", "reject"]
                    number = len(permissions) + 1
                    save(evidence, f"ORIGINAL_PERMISSION_{number:02d}.r1.json", pending)
                    reply = await value.reply(scene["pid"], ident, reference["id"],
                        {**control(opened, f"engineering-permission-{number}"), "request_ref": reference, "reply": "once"},
                        kind="PERMISSION")
                    save(evidence, f"ORIGINAL_PERMISSION_REPLY_{number:02d}.r1.json", reply)
                    assert reply["original_request_processed"]
                    permissions.append({"action": action, "request_ref": reference})
                    handled.add(reference["id"])
                if latest["active_operation_id"] is None:
                    break
                assert asyncio.get_running_loop().time() < deadline, "Original engineering operation timed out; no replay"
                await asyncio.sleep(.05)

            save(evidence, "ORIGINAL_RETURNED.r1.json", latest)
            operation = value.operation(scene["pid"], ident, body["operation_id"])
            save(evidence, "ORIGINAL_OPERATION.r1.json", operation)
            assert operation["status"] == "RETURNED"
            assert not gated.failures and len(wire.requests) == 6 and len(gated.events) == 6
            assert [event["request_index"] for event in gated.events] == list(range(6))
            assert implementation.read_text() == REPAIRED and tests.read_text() == CHECKS
            assert MANUAL_NOTE in implementation.read_text()
            assert scene["actual"].read_bytes() == scene["original"]
            assert sorted(p.name for p in folder.iterdir()) == ["calculator.py", "test_calculator.py"]
            tools = [p for m in latest["messages"] for p in m["parts"] if p.get("type") == "tool"]
            assert [p["tool"] for p in tools] == ["read", "read", "bash", "edit", "bash"]
            assert all(p["state"]["status"] == "completed" for p in tools)
            shells = [p for p in tools if p["tool"] == "bash"]
            assert [p["state"]["metadata"]["exit"] for p in shells] == [1, 0]
            assert all(type(p["state"]["metadata"]["exit"]) is int for p in shells)
            assert "FAILED (failures=" in shells[0]["state"]["output"]
            assert "Ran 3 tests" in shells[1]["state"]["output"] and "OK" in shells[1]["state"]["output"]
            (evidence / "ORIGINAL_UNITTEST_FAILED.merged.txt").write_text(shells[0]["state"]["output"], encoding="utf-8")
            (evidence / "ORIGINAL_UNITTEST_PASSED.merged.txt").write_text(shells[1]["state"]["output"], encoding="utf-8")
            query = {"session_sha256": opened["session_ref"]["sha256"], "generation": opened["generation"],
                     "workspace_ref": request["workspace_ref"]}
            commands = value.command_observations(scene["pid"], ident, **query)
            save(evidence, "ORIGINAL_COMMAND_OBSERVATIONS.r1.json", commands)
            assert commands["read_only"] and commands["model_calls"] == 0 and not commands["automatic_replay"]
            assert not commands["coverage"]["reason_codes"]
            rows = commands["commands"]
            assert len(rows) == 2 and [row["exit_code"] for row in rows] == [1, 0]
            for row, part, expected_source in zip(rows, shells, (BROKEN, REPAIRED)):
                assert row["operation_id"] == row["root_operation_id"] == body["operation_id"]
                assert row["native_message_id"] == operation["native_message_id"]
                assert row["call_id"] == part["callID"] and row["part_id"] == part["id"]
                assert row["command"] == command and row["status"] == "EXITED"
                assert row["source_generation"] == opened["generation"] and row["source_status"] == "UNCHANGED"
                assert len(row["sources"]) == 1 and row["sources"][0]["path"] == "calculator.py"
                assert row["sources"][0]["before"]["sha256"] == sha(expected_source.encode())
                assert row["sources"][0]["after"]["sha256"] == sha(expected_source.encode())
                assert row["output"]["text"] == part["state"]["output"] and not row["output"]["truncated"]
                assert row["output"]["sha256"] == sha(row["output"]["text"].encode())
            assert rows[0]["record_ref"] != rows[1]["record_ref"] and shells[0]["callID"] != shells[1]["callID"]
            directory = value.diffs(scene["pid"], ident, **query)
            save(evidence, "ORIGINAL_DIFF_CATALOG.r1.json", directory)
            assert len(directory["files"]) == 1, canonical(directory)
            change = directory["files"][0]
            assert change["path"] == "calculator.py" and change["source"] == "AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS"
            assert change["call_id"] == tools[3]["callID"] and change["content_scope"] == "FULL_FILE"
            detail = value.diff_file(scene["pid"], ident, **query,
                                     snapshot_ref=directory["snapshot_ref"], diff_ref=change["diff_ref"])
            save(evidence, "ORIGINAL_DIFF_FULL_FILE.r1.json", detail)
            assert detail["before"] == BROKEN and detail["after"] == REPAIRED
            assert detail["before_sha256"] == rows[0]["sources"][0]["before"]["sha256"]
            assert detail["after_sha256"] == rows[1]["sources"][0]["before"]["sha256"]
            assert [line for line in detail["before"].splitlines() if line not in detail["after"].splitlines()] == [OLD_LINE]
            assert [line for line in detail["after"].splitlines() if line not in detail["before"].splitlines()] == [NEW_LINE]
            return {"opened": opened, "query": query, "commands": commands, "diff": detail,
                    "directory": directory, "diff_ref": change["diff_ref"], "permissions": permissions}
        except BaseException as error:
            save(evidence, "ORIGINAL_SCENE_FAILURE.r1.json", {"error": repr(error), "last_observation": latest,
                 "model_requests": wire.requests, "gates": gated.events})
            raise
        finally:
            try:
                await value.close()
            finally:
                save(evidence, "ORIGINAL_OWNER_CLOSED.r1.json", {"active_owners": list(value.active),
                     "actual_returncode": original_process.returncode if original_process else None,
                     "state": value.get(scene["pid"], ident) if ident else None})
                assert not value.active
                if original_process is not None:
                    assert original_process.returncode is not None

    try:
        result = asyncio.run(run())
        # Saved history must work cold without a provider, owner or model.
        cold = AgentSessions(scene["store"], scene["data"], provider_registry=None,
                             workspaces=scene["workspaces"], capabilities=None)
        async def no_owner(*args, **kwargs):
            raise AssertionError("Saved engineering history cannot start/contact an original owner or model")
        monkeypatch.setattr(cold, "_http", no_owner)
        monkeypatch.setattr(cold, "_start", no_owner)
        monkeypatch.setattr(cold, "inspect", no_owner)
        count = len(wire.requests)
        state_path = cold._path(scene["pid"], result["opened"]["session_ref"]["id"], "state.json")
        state_before = state_path.read_bytes()
        ident = result["opened"]["session_ref"]["id"]
        saved = cold.command_observations(scene["pid"], ident, **result["query"])
        assert saved == result["commands"]
        assert cold.diff_file(scene["pid"], ident, **result["query"], snapshot_ref=result["directory"]["snapshot_ref"],
                              diff_ref=result["diff_ref"]) == result["diff"]
        assert len(wire.requests) == count == 6 and not cold.active and state_path.read_bytes() == state_before
        summary = {"status": "SCOPED_ACTUAL_ORIGINAL_READ_UNITTEST_RED_EDIT_UNITTEST_GREEN_AND_COLD_HISTORY_PASS",
                   "session_ref": result["opened"]["session_ref"], "generation": result["opened"]["generation"],
                   "original_session_id": result["opened"]["original_session_id"], "commands": [r["record_ref"] for r in saved["commands"]],
                   "diff_ref": result["diff_ref"], "model_fixture_rpc": count, "paid_remote_model_calls": 0,
                   "test_sha256_unchanged": sha(tests.read_bytes()) == inputs["readonly_test_sha256"],
                   "implementation_only_write_grant": ["calculator.py"], "manual_note_preserved": MANUAL_NOTE in implementation.read_text(),
                   "cold_history_owner_or_model_calls": 0, "original_runtime_logs_retained": str(scene["data"]),
                   "evidence_directory": str(evidence), "Windows_native": "NOT_RUN", "delivery_maintenance": "NOT_ASSESSED"}
        save(evidence, "RESULT.r1.json", summary)
        if os.environ.get("EEAS_AGENT_SESSIONS_EVIDENCE"):
            destination = Path(os.environ["EEAS_AGENT_SESSIONS_EVIDENCE"])
            destination.mkdir(parents=True, exist_ok=True)
            save(destination, "original-engineering-chain.r1.json", summary)
    finally:
        save(evidence, "ORIGINAL_MODEL_WIRE_REQUESTS.r1.json", {"requests": wire.requests, "causal_gates": gated.events,
                                                               "failures": gated.failures, "paid_model_calls": 0})
        wire.close()
