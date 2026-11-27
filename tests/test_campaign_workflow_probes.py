"""Synthetic contracts for explicit source-bound probes, never public qualification."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from scripts import public_campaign as campaign
from test_campaign_probes import bound_source

ROOT = Path(__file__).resolve().parents[1]


def execute_workflow(source, node):
    binding = source["binding_path"]
    command = [sys.executable, "-m", "pytest", "tests/campaign_workflow_probes.py::" + node,
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Actual source-bound original Mock workflow API/SQLite/Runner/Verifier probe; "
                "original synthetic source only, upstream engineering/company model/Windows/sandbox NOT_QUALIFIED"]
    for path in (binding, *[source["project"] / name for name in ("source.raw", "source.json", "license.raw")]):
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    declared = source["source"].get("provenance")
    if declared:
        sidecar = source["project"] / declared["path"]
        launcher += ["--reference", sidecar.relative_to(ROOT).as_posix()]
        if not source.get("omit_original_references"):
            for item in json.loads(sidecar.read_bytes())["selected"]:
                launcher += ["--reference", (sidecar.parent / item["path"]).relative_to(ROOT).as_posix()]
    launcher += ["E11-workflow-probe-" + uuid.uuid4().hex, "--", *command]
    result = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(binding)},
                            capture_output=True, check=False)
    lines = result.stdout.decode().splitlines()
    report = ROOT / next(line.removeprefix("Evidence: ") for line in lines if line.startswith("Evidence: "))
    frozen = json.loads((report / "freeze.json").read_bytes())["code_sha256"]
    after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in frozen}
    (report / "source-after.json").write_bytes(campaign.encode({"files_sha256": after, "matches": after == frozen}))
    assert after == frozen
    selected = os.environ.get("EEAS_EVIDENCE_DIR")
    if selected:
        name = source["case_id"] + "-" + binding.parent.name + "-explicit-report.json"
        with (Path(selected) / name).open("xb") as stream:
            stream.write(campaign.encode({"actual_report": report.relative_to(ROOT).as_posix(),
                                          "command": command, "exit_code": result.returncode}))
    return result, report, command


@pytest.mark.parametrize("case_id", ["requirements", "planning", "tasks", "approvals", "runner", "verification", "repair", "results"])
def test_bound_workflow_node_has_real_record_slice_receipts(bound_source, case_id):
    raw = (f"# 原创 {case_id} 项目\r\n实际规范 Straße 数据，不能当作代码执行\r\n"
           f"末尾边界 {case_id}，使用当前原始字节\n").encode()
    bound_source["prepare"](case_id, raw)
    result, report, command = execute_workflow(bound_source, "test_" + case_id)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"],
                                         command, result.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    observation = json.loads((report / "campaign-observation.json").read_bytes())
    assert observation["source_kind"] == "synthetic" and observation["model_calls"] == 0
    assert observation["upstream_engineering"] == observation["company_model"] == "NOT_RUN"
    assert observation["windows_native"] == observation["os_sandbox"] == "NOT_VALIDATED"
    assert observation["runtime_kind"] == ("none" if case_id in {"requirements", "planning", "tasks"} else "mock")
    assert (report / "actual-original.bin").read_bytes() == raw
    consumed = json.loads((report / "actual-source-consumption.json").read_bytes())
    assert consumed["parentRawSHA"] == hashlib.sha256(raw).hexdigest()
    assert consumed["source_lines"][0]["physical_line"] == raw.decode().splitlines(keepends=True)[0]
    assert consumed["source_lines"][-1]["physical_line"] == raw.decode().splitlines(keepends=True)[-1]
    assert consumed["parentRawSHA"] in consumed["original_mock_goal"]
    assert "末尾边界 " + case_id in consumed["original_mock_goal"]
    rows = json.loads((report / "actual-sqlite-rows.json").read_bytes())
    original = rows["document_versions"][0]
    assert original["content"] == raw.decode() and original["sha256"] == consumed["parentRawSHA"]
    assert original["raw_bytes"]["sqlite_type"] == "BLOB"
    assert base64.b64decode(original["raw_bytes"]["raw_base64"]) == raw
    assert original["raw_bytes"]["sha256"] == consumed["parentRawSHA"]
    assert (report / "actual-store.sqlite3").read_bytes().startswith(b"SQLite format 3\x00")
    assert json.loads((report / "actual-api-receipts.json").read_bytes())
    if case_id == "results":
        expected = (consumed["original_mock_goal"] + "\n").encode()
        assert (report / "actual-source-candidate.bin").read_bytes() == expected
        log = json.loads((report / "actual-runner-stderr.json").read_bytes())
        assert base64.b64decode(log["raw_base64"]) == b"" and log["sha256"] == hashlib.sha256(b"").hexdigest()


def test_inapplicable_source_fails_without_padding_or_success_observation(bound_source):
    bound_source["prepare"]("requirements", ("超长单物理行" * 300 + "\n").encode())
    result, report, command = execute_workflow(bound_source, "test_requirements")
    assert result.returncode == 1
    assert "no padding or truncation" in (report / "stdout.txt").read_text()
    assert (report / "actual-original.bin").read_bytes() == (bound_source["project"] / "source.raw").read_bytes()
    assert not (report / "campaign-observation.json").exists()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"],
                                         command, result.returncode, observation_required=False)
    assert checked["test_counts"] == {"executed": 1, "failed": 1, "skipped": 0}


def test_explicit_probe_module_is_not_default_discovery():
    spec = importlib.util.spec_from_file_location("explicit_workflow_probes", ROOT / "tests/campaign_workflow_probes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = {"test_" + case for case in ("requirements", "planning", "tasks", "approvals", "runner", "verification", "repair", "results",
                                           "project_boundary", "recovery", "extensions")}
    assert {name for name in vars(module) if name.startswith("test_")} == expected
    assert not (ROOT / "tests/campaign_workflow_probes.py").name.startswith("test_")
