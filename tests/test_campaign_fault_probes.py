"""Synthetic regression of explicit boundary audit runners, not public source certification."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

import campaign_fault_probes as probes
from scripts import public_campaign as campaign
from test_campaign_probes import bound_source


def test_only_reviewed_whole_file_suites_keep_original_assertion_bytes():
    for case_id, expected in [("fault-documents", 35), ("fault-extension", 18)]:
        actual = probes.fixed_suite(case_id)
        assert actual["expected_tests"] == expected
        assert sum(i["collected_tests"] for i in actual["files"]) == expected
    with pytest.raises(ValueError, match="approved suite"):
        probes.fixed_suite("requirements")


@pytest.mark.parametrize("case_id,node,expected", [
    ("fault-documents", "test_fault_documents", 35),
    ("fault-extension", "test_fault_extension", 18),
])
def test_bound_valid_bytes_then_actual_original_fault_assertions(bound_source, case_id, node, expected):
    bound_source["prepare"](case_id)
    root = probes.ROOT
    binding = bound_source["binding_path"]
    command = [sys.executable, "-m", "pytest", "tests/campaign_fault_probes.py::" + node,
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Original synthetic audit regression: real bound bytes ingested before all fixed fault tests; "
                "exact original assertions/count/JUnit/freeze; no public/project/company qualification"]
    for path in [binding, *[bound_source["project"] / n for n in ["source.raw", "source.json", "license.raw"]], probes.PLAN]:
        launcher += ["--reference", path.relative_to(root).as_posix()]
    launcher += ["E11-fault-probe-regression-" + uuid.uuid4().hex, "--", *command]
    actual = subprocess.run(launcher, cwd=root, env=dict(os.environ), capture_output=True, check=False)
    reports = [line.removeprefix("Evidence: ") for line in actual.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(reports) == 1
    evidence = root / reports[0]
    assert actual.returncode == 0, "See actual outer/child logs at " + str(evidence)
    checked = campaign.checked_evidence(root, evidence, binding, bound_source["source"], command,
                                         actual.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    assert (evidence / "actual-original.bin").read_bytes() == (bound_source["project"] / "source.raw").read_bytes()
    fault = json.loads((evidence / "actual-fault-suite.json").read_bytes())
    assert fault["actual_counts"] == {"executed": expected, "failed": 0, "skipped": 0}
    assert fault["project_material_source_kind"] == "synthetic" and fault["fault_input_source_kind"] == "synthetic"
    observation = json.loads((evidence / "campaign-observation.json").read_bytes())
    assert observation["source_kind"] == "synthetic" and observation["model_calls"] == 0
    assert observation["runtime_kind"] == "mock" and observation["fault_input_source_kind"] == "synthetic"
    assert fault["preserved_empty_raw_files"]
    assert all((evidence / item["path"]).read_bytes() == b"" for item in fault["preserved_empty_raw_files"])
    # Generic original fault invariants are separate from the project workflow.
    assert "not project-specific workflow" in fault["limits"]
