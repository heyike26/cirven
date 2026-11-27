"""Actual cold official MCP activation; bounded original readonly observations."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import site
import sys
import time

import pytest

from packages.domain.store import InvalidInput
from test_agent_sessions import ROOT, ModelWire, control, owner, retain, scene, tool, until


def test_actual_cold_official_mcp_over_ten_seconds_load_call_and_exact_cleanup(scene):
    script = scene["data"] / "real-cold-mcp.py"
    script.write_bytes((ROOT / "tests/agent_sessions_mcp_cold_fixture.py").read_bytes())
    executable = Path(sys.executable).resolve()
    receipts = scene["data"] / "real-cold-receipts"
    receipts.mkdir()
    configured = scene["manager"].configure_mcp({"operation_id": "configure-real-cold", "id": "cold-server", "expected_revision": 0,
        "configuration": {"type": "local", "command": [str(executable), str(script), "--site-packages", site.getsitepackages()[0],
            "--receipt-dir", str(receipts), "--server-id", "cold-server", "--tool-name", "read_lines", "--startup-delay", "11"],
            "files_sha256": {str(executable): hashlib.sha256(executable.read_bytes()).hexdigest(),
                             str(script): hashlib.sha256(script.read_bytes()).hexdigest()}, "enabled": True, "timeout": 60000}})
    wire = ModelWire([tool("cold-server_read_lines", {"path": "real.md"}), "ACTUAL_COLD_ORIGINAL_MCP_RETURNED"]).start()
    async def check():
        value, request = owner(scene, wire)
        request.update(allowed_effects=["PROCESS_START"], mcp_tool_allowlist=["cold-server_read_lines"])
        try:
            start = time.monotonic()
            opened = await value.open(scene["pid"], request)
            open_elapsed = time.monotonic() - start
            ident = opened["session_ref"]["id"]
            actual = opened["capability_load"]["mcp"]["cold-server"]
            assert open_elapsed > 10 and actual["loaded_in_original_session"] and actual["config_ref"] == configured["config_ref"]
            # No mutation command may ask _http for a larger timeout, including
            # a caller accidentally passing the newly derived startup deadline.
            context = value.active[ident]
            for path in ["/session/" + opened["original_session_id"] + "/prompt_async", "/permission/per_not_live/reply"]:
                with pytest.raises(InvalidInput):
                    await value._http(context, "POST", path, {}, timeout=70)
            assert not wire.requests
            await value.send(scene["pid"], ident, {**control(opened, "cold-tool-call"), "raw_text": "Read the real file with the actual slow-started MCP"})
            pending = await until(value, scene, ident, lambda v: bool(v["pending_permissions"]))
            item = pending["pending_permissions"][0]
            assert item["native_request"]["permission"] == "cold-server_read_lines"
            await value.reply(scene["pid"], ident, item["request_ref"]["id"],
                {**control(opened, "cold-mcp-once"), "request_ref": item["request_ref"], "reply": "once"}, kind="PERMISSION")
            final = await until(value, scene, ident, lambda v: v["active_operation_id"] is None)
            read = json.loads((receipts / "calls.jsonl").read_text())
            assert read["sha256"] == hashlib.sha256(scene["original"]).hexdigest() and len(wire.requests) == 2
            starts = [json.loads(p.read_bytes()) for p in receipts.glob("startup-*.json")]
            assert starts and all(item["actual_activation_elapsed_seconds"] > 10 and item["ppid"] == final["process_identity"]["pid"] for item in starts)
            stopped = await value.controlled_disconnect(scene["pid"], ident, control(opened, "cold-exact-stop"))
            assert not stopped["connected_owner"] and stopped["last_control_operation"]["exact_owned_process_stop_confirmed"]
            assert context["process"].returncode is not None and scene["actual"].read_bytes() == scene["original"]
            retain("actual-cold-mcp-load-call-cleanup", scene, value, wire, final)
            if os.environ.get("EEAS_AGENT_SESSIONS_EVIDENCE"):
                target = Path(os.environ["EEAS_AGENT_SESSIONS_EVIDENCE"])
                target.mkdir(parents=True, exist_ok=True)
                (target / "cold-actual-activation.json").write_text(json.dumps({"open_elapsed_seconds": open_elapsed,
                    "actual_official_mcp_processes": starts, "selected_mcp_timeout_ms": 60000,
                    "derived_readonly_startup_deadline_seconds": 70, "ordinary_client_timeout_seconds": context["client"].timeout.read,
                    "post_extended_deadline_rejected_before_dispatch": True, "cleanup_receipt": stopped["last_control_operation"]}))
        finally:
            await value.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()
