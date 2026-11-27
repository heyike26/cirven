from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import pytest
from packages.adapters.opencode_workflow_plugin import (prepare_workflow_plugin, verify_workflow_plugin,
    write_workflow_policy, WorkflowPluginError, MANIFEST_SHA256)


def check(value, message="workflow admission acceptance failed"):
    if not value:
        raise AssertionError(message)


def inputs():
    return {"owner_ref": {"project_id": "project", "session_ref": {"id": "conversation", "revision": 1, "sha256": "a" * 64}, "generation": 3},
            "catalog": [{"id": "route", "provider_id": "fixture-provider", "model_id": "fixture-model", "variants": [None, "high"], "read_only": True}],
            "nodes": [{"id": "review", "title": "Review", "agent_policy": {"model": {"mode": "AUTO", "value": None}, "depth": {"mode": "AUTO", "value": None}}}]}


def test_exact_bundle_private_config_and_two_field_policy_cas_with_no_secret(tmp_path):
    verified = verify_workflow_plugin()
    check(verified["manifest_sha256"] == MANIFEST_SHA256)
    prepared = prepare_workflow_plugin(tmp_path / "owner", **inputs(), sink_url="http://127.0.0.1:43112/api/dynamic-workflow/plugin-events")
    admission = prepared["admission"]
    check(prepared["config_fragment"]["plugin"][0][0].endswith("/dynamic-workflow/index.mjs"))
    raw = Path(admission["config_path"]).read_bytes()
    check(hashlib.sha256(raw).hexdigest() == admission["config_sha256"])
    check(b"apiKey" not in raw and b"Bearer" not in raw)
    check(prepare_workflow_plugin(tmp_path / "owner", **inputs(), sink_url="http://127.0.0.1:43112/api/dynamic-workflow/plugin-events") == prepared)
    nodes = inputs()["nodes"]
    nodes[0]["agent_policy"]["depth"] = {"mode": "MANUAL", "value": "high"}
    ref = write_workflow_policy(admission, nodes=nodes, plan_ref=None, expected_ref=admission["policy_ref"])
    check(ref["revision"] == 2)
    check(write_workflow_policy(admission, nodes=nodes, plan_ref=None, expected_ref=ref) == ref)
    before = Path(admission["policy_path"]).read_bytes()
    with pytest.raises(WorkflowPluginError, match="CAS_CONFLICT"):
        write_workflow_policy(admission, nodes=inputs()["nodes"], plan_ref=None, expected_ref=admission["policy_ref"])
    check(Path(admission["policy_path"]).read_bytes() == before)


def test_source_drift_symlink_foreign_owner_and_unsafe_sink_fail_without_overwrite(tmp_path):
    source = Path(verify_workflow_plugin()["plugin_root"])
    portable = tmp_path / "portable-plugin"
    portable.mkdir()
    manifest = json.loads((source / "manifest.json").read_bytes())
    for name in ["manifest.json", *[row["path"] for row in manifest["files"]]]:
        target = portable / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    verify_workflow_plugin(portable)
    with (portable / "index.mjs").open("a") as stream:
        stream.write("\n// unapproved bytes")
    with pytest.raises(WorkflowPluginError, match="SOURCE_DRIFT"):
        verify_workflow_plugin(portable)
    shutil.copyfile(source / "index.mjs", portable / "index.mjs")
    (portable / "vendor/zod/src/v3/tests/array.test.ts").unlink()
    with pytest.raises(WorkflowPluginError, match="SOURCE_MISSING"):
        verify_workflow_plugin(portable)
    link = tmp_path / "alias"
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(WorkflowPluginError, match="PATH_REJECTED"):
        verify_workflow_plugin(link)
    for url in ("http://localhost:1234/api/dynamic-workflow/plugin-events", "http://127.0.0.1:1234/run", "https://127.0.0.1:1234/api/dynamic-workflow/plugin-events"):
        with pytest.raises(WorkflowPluginError, match="SINK_REJECTED"):
            prepare_workflow_plugin(tmp_path / "bad", **inputs(), sink_url=url)
    prepared = prepare_workflow_plugin(tmp_path / "own", **inputs())
    different = inputs()
    different["owner_ref"]["generation"] += 1
    with pytest.raises(WorkflowPluginError, match="PRIVATE_SOURCE_CONFLICT"):
        prepare_workflow_plugin(tmp_path / "own", **different)


def test_plan_cycle_and_invalid_manual_or_duplicate_routes_are_rejected_before_files(tmp_path):
    bad = inputs()
    bad["nodes"][0]["agent_policy"]["model"] = {"mode": "AUTO", "value": "guessed-model"}
    with pytest.raises(WorkflowPluginError, match="POLICY_INVALID"):
        prepare_workflow_plugin(tmp_path / "bad-policy", **bad)
    bad = inputs()
    bad["catalog"].append(deepcopy(bad["catalog"][0]))
    with pytest.raises(WorkflowPluginError, match="ROUTE_INVALID"):
        prepare_workflow_plugin(tmp_path / "bad-catalog", **bad)
    with pytest.raises(WorkflowPluginError, match="DEPENDENCY_INVALID"):
        prepare_workflow_plugin(tmp_path / "bad-cycle", **inputs(), dependencies=[{"source": "review", "target": "review"}])
    check(not (tmp_path / "bad-policy").exists() and not (tmp_path / "bad-catalog").exists() and not (tmp_path / "bad-cycle").exists())
