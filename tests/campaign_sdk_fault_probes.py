"""Explicit SDK fault probes; original source import is separate from synthetic wire faults."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

from campaign_probes import ROOT, ingest, load_binding
from campaign_fault_probes import FaultEvidence
from scripts import public_campaign as campaign

PLAN = ROOT / "tests/campaign_sdk_fault_plan.json"
APPROVED_FILES = {
    "fault-sdk-route": ("tests/test_sdk_settings.py", "tests/test_sdk_settings_fd.py", "tests/test_sdk_settings_windows_contract.py"),
    "fault-sdk-budget": ("tests/test_sdk_budget_float.py", "tests/test_sdk_budget_decimal.py", "tests/test_sdk_cost_optional.py"),
    "fault-sdk-session": ("tests/test_claude_lifecycle.py", "tests/test_claude_native_transport.py", "tests/test_claude_sessions.py", "tests/test_sdk_query_scope.py"),
    "fault-sdk-images": ("tests/test_sdk_images.py", "tests/test_sdk_image_policy_race.py"),
    "fault-search": ("tests/test_sdk_search_mcp.py", "tests/test_search_mcp_boundaries.py"),
    "fault-approval-runner": ("tests/test_approvals.py", "tests/test_worker.py", "tests/test_recovery.py", "tests/test_native_runner.py", "tests/test_repair_approvals.py"),
    "fault-offline-restore": ("tests/test_release_bundle.py", "tests/test_sdk_release_contract.py", "tests/test_offline.py"),
}


def fixed_sdk_suite(case_id):
    if case_id not in APPROVED_FILES:
        raise ValueError("no approved SDK fault suite for this case")
    plan = json.loads(PLAN.read_bytes())
    if plan.get("schema") != "eeas.campaign-sdk-fault-plan.v1" or set(plan.get("cases", {})) != set(APPROVED_FILES):
        raise ValueError("unsupported explicit SDK fault plan")
    suite = plan["cases"][case_id]
    if (tuple(item["path"] for item in suite["files"]) != APPROVED_FILES[case_id]
            or type(suite["expected_tests"]) is not int or suite["expected_tests"] <= 0
            or any(type(item["collected_tests"]) is not int or item["collected_tests"] <= 0 for item in suite["files"])
            or sum(item["collected_tests"] for item in suite["files"]) != suite["expected_tests"]):
        raise ValueError("SDK fault plan requires fixed whole files and exact reviewed counts")
    for item in suite["files"]:
        path = ROOT / item["path"]
        if (not isinstance(item["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
                or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]):
            raise ValueError("original SDK fault assertions changed; explicit suite review required")
    return suite


def source_references(captured):
    project = captured["path"].parent
    references = [Path(os.environ["EEAS_CAMPAIGN_BINDING"]), captured["path"], project / "source.json", project / "license.raw", PLAN]
    source = captured["source"]
    if "provenance" in source:
        record = source["provenance"]
        if record.get("schema") != campaign.PROVENANCE_SCHEMA or record.get("path") != "provenance/sidecar.json":
            raise ValueError("unsupported original provenance binding")
        sidecar = campaign.provenance_path(project, record["path"])
        raw = campaign.provenance_read(sidecar, campaign.PROVENANCE_MAX_BYTES)
        if (campaign.digest(raw) != record["sha256"]
                or captured["binding"].get("provenance_path") != sidecar.relative_to(ROOT).as_posix()):
            raise ValueError("original provenance differs from source/binding")
        mapping, _ = campaign.validate_provenance(raw, captured["raw"], sidecar.parent,
                                                origin=source["origin"], revision=source["revision"])
        references += [sidecar, *[campaign.provenance_path(sidecar.parent, item["path"]) for item in mapping["selected"]]]
    if captured["binding"]["case_id"] == "fault-offline-restore":
        from campaign_offline_inputs import dependency_references
        references += dependency_references()
    return references


def save_raw(evidence, name, raw):
    with (evidence.root / name).open("xb") as stream:
        stream.write(raw)
    if raw:
        evidence.receipts.append({"path": name, "sha256": campaign.digest(raw)})
    # Empty raw logs remain actual files; the nonempty descriptor can be a checked receipt.
    evidence.save(name + ".receipt.json", {"byte_length": len(raw), "sha256": campaign.digest(raw)})


def execute_sdk_suite(case_id, captured, evidence):
    suite = fixed_sdk_suite(case_id)
    references = source_references(captured)
    before = {path.relative_to(ROOT).as_posix(): campaign.digest(path.read_bytes()) for path in references}
    binding = references[0]
    command = [sys.executable, "-m", "pytest", *[item["path"] for item in suite["files"]],
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Actual valid bound source ingestion precedes the frozen original whole " + case_id + " suite; "
                "original synthetic settings/SDK wire only, never company/native Windows/upstream qualification"]
    for path in references:
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-original-sdk-fault-" + uuid.uuid4().hex, "--", *command]
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(("ANTHROPIC_", "CLAUDE_", "EEAS_MODEL", "EEAS_LIVE", "EEAS_SEARCH", "EEAS_CLAUDE")):
            environment.pop(name, None)
    actual = subprocess.run(launcher, cwd=ROOT, env=environment, capture_output=True, check=False)
    save_raw(evidence, "child-launcher-stdout.txt", actual.stdout)
    save_raw(evidence, "child-launcher-stderr.txt", actual.stderr)
    matches = re.findall(r"^Evidence: (reports/[^\r\n]+)$", actual.stdout.decode("utf-8", "replace"), re.M)
    if len(matches) != 1:
        evidence.save("actual-sdk-fault-suite.json", {"case_id": case_id, "command": command,
                      "actual_exit_code": actual.returncode, "validation_error": "missing unambiguous actual record_slice receipt"})
        raise AssertionError("SDK fault suite did not produce one actual record_slice receipt; raw launcher output retained")
    child = campaign.contained(ROOT, matches[0])
    copied = evidence.root / "original-sdk-fault-suite"
    # Copy before validation: failed suites, missing JUnit or skip/count mismatch must retain original evidence.
    shutil.copytree(child, copied)
    empty = []
    for path in sorted(copied.rglob("*")):
        if path.is_file():
            item = {"path": path.relative_to(evidence.root).as_posix(), "sha256": campaign.digest(path.read_bytes())}
            if path.stat().st_size:
                evidence.receipts.append(item)
            else:
                empty.append({**item, "byte_length": 0})
    report = {"case_id": case_id, "original_report": matches[0], "command": command,
              "actual_exit_code": actual.returncode, "expected_tests": suite["expected_tests"],
              "project_material_source_kind": captured["source"]["source_kind"],
              "fault_input_source_kind": "synthetic", "runtime_kind": "mock", "real_model_calls": 0,
              "scope": suite["scope"], "limits": suite["limits"], "preserved_empty_raw_files": empty,
              "subprocess_returned": True}
    try:
        checked = campaign.checked_evidence(ROOT, child, binding, captured["source"], command,
                                            actual.returncode, observation_required=False)
        report["actual_counts"] = checked["test_counts"]
        expected_refs = {path.relative_to(ROOT).as_posix(): campaign.digest(path.read_bytes()) for path in references}
        if before != expected_refs or any(checked["frozen_input_sha256"].get(name) != sha for name, sha in before.items()):
            raise ValueError("source/sidecar/selected originals/plan changed or were not actually frozen")
        fixed_sdk_suite(case_id)
        current = load_binding(case_id)
        source_references(current)
        if current["binding_raw"] != captured["binding_raw"] or current["raw"] != captured["raw"]:
            raise ValueError("original binding/source changed during SDK fault suite")
        if actual.returncode != 0 or checked["test_counts"] != {"executed": suite["expected_tests"], "failed": 0, "skipped": 0}:
            raise ValueError("SDK fault suite failed or whole-file execution count differs")
    except (ValueError, KeyError, OSError) as error:
        report["validation_error"] = str(error)
        evidence.save("actual-sdk-fault-suite.json", report)
        raise AssertionError("SDK fault suite rejected; original logs/JUnit/freeze retained: " + str(error)) from error
    evidence.save("actual-sdk-fault-suite.json", report)


def run_sdk_boundary(client, case_id):
    fixed_sdk_suite(case_id)
    captured = load_binding(case_id)
    source_references(captured)
    evidence = FaultEvidence(captured)
    ingest(client, captured, evidence)
    execute_sdk_suite(case_id, captured, evidence)
    with client.app.state.store.connect() as connection:
        executions = connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0]
    assert executions == 0, "Source-first outer application must not start a model execution"
    evidence.save("actual-outer-runtime.json", {"agent_executions": executions, "real_model_calls": 0,
                                               "inner_scope": "exited subprocess with installed SDK/original local wire fixtures"})
    evidence.observe()


def test_fault_sdk_route(client):
    run_sdk_boundary(client, "fault-sdk-route")


def test_fault_sdk_budget(client):
    run_sdk_boundary(client, "fault-sdk-budget")


def test_fault_sdk_session(client):
    run_sdk_boundary(client, "fault-sdk-session")


def test_fault_sdk_images(client):
    run_sdk_boundary(client, "fault-sdk-images")


def test_fault_search(client):
    run_sdk_boundary(client, "fault-search")


def test_fault_approval_runner(client):
    run_sdk_boundary(client, "fault-approval-runner")


def test_fault_offline_restore(client):
    from campaign_offline_inputs import run_offline_restore
    run_offline_restore(client)
