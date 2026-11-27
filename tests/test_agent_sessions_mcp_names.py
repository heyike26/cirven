"""MCP namespace admission with actual original CLI and official MCP servers."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import site
import sys

import pytest

from packages.agent_sessions.mcp_names import original_mcp_identifier
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest
from test_agent_sessions import ROOT, ModelWire, control, owner, retain, scene, tool, until


def configure(scene, ident, tool_name, *, enabled=True):
    script = scene["data"] / (ident + "-actual-mcp.py")
    script.write_bytes((ROOT / "tests/agent_sessions_mcp_namespace_fixture.py").read_bytes())
    executable = Path(sys.executable).resolve()
    receipts = scene["data"] / (ident + "-receipts")
    receipts.mkdir()
    saved = scene["manager"].configure_mcp({"operation_id": "configure-" + ident, "id": ident, "expected_revision": 0,
        "configuration": {"type": "local", "command": [str(executable), "-B", str(script), "--site-packages", site.getsitepackages()[0],
            "--receipt-dir", str(receipts), "--server-id", ident, "--tool-name", tool_name],
            "files_sha256": {str(executable): hashlib.sha256(executable.read_bytes()).hexdigest(),
                             str(script): hashlib.sha256(script.read_bytes()).hexdigest()}, "enabled": enabled, "timeout": 10000}})
    return saved, receipts


def test_two_actual_hyphen_and_underscore_mcp_servers_load_call_and_exact_source_attribution(scene):
    first, first_receipts = configure(scene, "fixture-local", "local_line_count")
    second, second_receipts = configure(scene, "fixture_local", "local_line_count")
    disabled, disabled_receipts = configure(scene, "fixture", "local_local_line_count", enabled=False)
    names = ["fixture-local_local_line_count", "fixture_local_local_line_count"]
    assert original_mcp_identifier("fixture-local") == "fixture-local"
    wire = ModelWire([tool(names[0], {"path": "real.md"}), tool(names[1], {"path": "real.md"}), "BOTH_ACTUAL_MCP_SOURCES_RETURNED"]).start()

    async def check():
        value, request = owner(scene, wire)
        request.update(allowed_effects=["PROCESS_START"], mcp_tool_allowlist=names)
        try:
            opened = await value.open(scene["pid"], request)
            ident = opened["session_ref"]["id"]
            for server_id, saved in [("fixture-local", first), ("fixture_local", second)]:
                item = opened["capability_load"]["mcp"][server_id]
                assert item["loaded_in_original_session"] and item["config_ref"] == saved["config_ref"]
                assert item["native_tool_prefix"] == server_id + "_" and not item["invoked"]
            await value.send(scene["pid"], ident, {**control(opened, "actual-two-mcp-names"), "raw_text": "Read the same real file via both actual admitted MCP sources"})
            for number, name in enumerate(names):
                pending = await until(value, scene, ident, lambda v: any(p["native_request"]["permission"] == name for p in v["pending_permissions"]))
                permission = next(p for p in pending["pending_permissions"] if p["native_request"]["permission"] == name)
                if number == 1:
                    assert pending["capability_load"]["mcp"]["fixture-local"]["invoked"]
                    assert not pending["capability_load"]["mcp"]["fixture_local"]["invoked"]
                await value.reply(scene["pid"], ident, permission["request_ref"]["id"],
                    {**control(opened, "actual-mcp-name-reply-" + str(number)), "request_ref": permission["request_ref"], "reply": "once"}, kind="PERMISSION")
            final = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            advertised = {entry["function"]["name"] for entry in wire.requests[0]["body"]["tools"]}
            assert set(names) <= advertised and len(wire.requests) == 3
            for server_id, receipts in [("fixture-local", first_receipts), ("fixture_local", second_receipts)]:
                actual = json.loads((receipts / "calls.jsonl").read_text())
                assert actual["server_id"] == server_id and actual["sha256"] == hashlib.sha256(scene["original"]).hexdigest()
                assert actual["lines"] == 2 and final["capability_load"]["mcp"][server_id]["invoked"]
                starts = [json.loads(p.read_bytes()) for p in receipts.glob("startup-*.json")]
                assert starts and all(p["ppid"] == final["process_identity"]["pid"] for p in starts)
            invocations = [final["capability_load"]["mcp"][server_id]["last_actual_invocation"]["native_part_id"] for server_id in ["fixture-local", "fixture_local"]]
            assert len(set(invocations)) == 2 and scene["actual"].read_bytes() == scene["original"]
            inactive = final["capability_load"]["mcp"].get("fixture")
            assert inactive is None or (inactive["config_ref"] == disabled["config_ref"]
                and not inactive["configured_in_original"] and not inactive["invoked"]
                and not inactive["loaded_in_original_session"])
            assert not list(disabled_receipts.iterdir())
            retain("hyphen-underscore-actual-source-separation", scene, value, wire, final)
        finally:
            await value.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_two_real_mcp_configurations_with_underscore_namespace_overlap_reject_before_process_start(scene):
    first, first_receipts = configure(scene, "fixture", "local_line_count")
    second, second_receipts = configure(scene, "fixture_local", "line_count")
    # Actual MCP configurations declare different tools that share the pinned
    # original compound name. Preflight rejects potential namespace ambiguity;
    # it does not launch another MCP probe to manufacture a catalog receipt.
    names = [original_mcp_identifier("fixture") + "_" + original_mcp_identifier("local_line_count"),
             original_mcp_identifier("fixture_local") + "_" + original_mcp_identifier("line_count")]
    assert names[0] == names[1]
    wire = ModelWire([]).start()
    async def check():
        value, request = owner(scene, wire)
        request.update(allowed_effects=["PROCESS_START"], mcp_tool_allowlist=[names[0]])
        try:
            with pytest.raises(Conflict, match="ORIGINAL_MCP_TOOL_SOURCE_NOT_UNAMBIGUOUS"):
                await value.open(scene["pid"], request)
            assert not value.active and not wire.requests
            assert not list(first_receipts.iterdir()) and not list(second_receipts.iterdir())
            assert scene["actual"].read_bytes() == scene["original"]
            if os.environ.get("EEAS_AGENT_SESSIONS_EVIDENCE"):
                target = Path(os.environ["EEAS_AGENT_SESSIONS_EVIDENCE"])
                target.mkdir(parents=True, exist_ok=True)
                (target / "actual-mcp-namespace-startup-rejection.json").write_text(canonical({
                    "configuration_refs": [first["config_ref"], second["config_ref"]], "compound_tool_names": names,
                    "startup_markers": [], "actual_owner_process_count": len(value.active), "model_rpc_count": len(wire.requests),
                    "admission": "CONSERVATIVE_SERVER_PREFIX_NAMESPACE_CHECK_BEFORE_ORIGINAL_STARTUP",
                    "actual_unflattened_original_catalog": "NOT_EXPOSED_NOT_PROBED", "paid_model_calls": 0}))
        finally:
            await value.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()
