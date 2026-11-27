"""Explicit source-bound runs of frozen original fault suites, not public workflow claims."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

from campaign_probes import Evidence, ROOT, ingest, load_binding
from scripts import public_campaign as campaign

PLAN = ROOT / "tests/campaign_fault_plan.json"


class FaultEvidence(Evidence):
    def observe(self):
        super().observe()
        path = self.root / "campaign-observation.json"
        actual = json.loads(path.read_bytes())
        actual.update(runtime_kind="mock", fault_input_source_kind="synthetic",
                      runtime_scope="original local fault fixtures; no upstream model")
        path.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fixed_suite(case_id):
    plan = json.loads(PLAN.read_bytes())
    if case_id not in {"fault-documents", "fault-extension"}:
        raise ValueError("this explicit probe has no approved suite for that case")
    suite = plan[case_id]
    for item in suite["files"]:
        name = item["path"]
        if not re.fullmatch(r"tests/test_[a-z_]+\.py", name):
            raise ValueError("fixed suite contains an unsafe or non-test selector")
        path = ROOT / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("original fault assertions changed; explicit suite review required")
    return suite


def execute_original_suite(case_id, captured, evidence):
    suite = fixed_suite(case_id)
    binding = Path(os.environ["EEAS_CAMPAIGN_BINDING"])
    command = [sys.executable, "-m", "pytest", *[i["path"] for i in suite["files"]],
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Frozen original " + case_id + " fault assertions after actual bound project text ingestion; "
                "synthetic fault inputs/SDK wire, no public workflow/upstream/company qualification"]
    for path in [binding, captured["path"], captured["path"].parent / "source.json",
                 captured["path"].parent / "license.raw", PLAN]:
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-original-fault-" + uuid.uuid4().hex, "--", *command]
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith(("ANTHROPIC_", "CLAUDE_", "EEAS_MODEL", "EEAS_LIVE", "EEAS_SEARCH", "EEAS_CLAUDE")):
            env.pop(key, None)
    actual = subprocess.run(launcher, cwd=ROOT, env=env, capture_output=True, check=False)
    evidence.save("child-launcher-stdout.txt", actual.stdout)
    # Keep nonempty receipt bytes even when the actual stderr stream is empty.
    evidence.save("child-launcher-stderr.json", {"utf8": actual.stderr.decode("utf-8", "replace"),
                                                "byte_length": len(actual.stderr)})
    matches = re.findall(r"^Evidence: (reports/[^\r\n]+)$", actual.stdout.decode("utf-8", "replace"), re.M)
    assert len(matches) == 1, "Original fault suite did not produce one actual record_slice receipt"
    child = campaign.contained(ROOT, matches[0])
    checked = campaign.checked_evidence(ROOT, child, binding, captured["source"], command,
                                        actual.returncode, observation_required=False)
    # Copy the actual child receipt, assertions, frozen inputs and artifacts;
    # never reconstruct a passing run or rewrite its original evidence.
    copied = evidence.root / "original-fault-suite"
    shutil.copytree(child, copied)
    empty_files = []
    for path in sorted(copied.rglob("*")):
        if path.is_file() and path.stat().st_size:
            evidence.receipts.append({"path": path.relative_to(evidence.root).as_posix(),
                                      "sha256": campaign.digest(path.read_bytes())})
        elif path.is_file():
            empty_files.append({"path": path.relative_to(evidence.root).as_posix(),
                                "byte_length": 0, "sha256": campaign.digest(b"")})
    evidence.save("actual-fault-suite.json", {"case_id": case_id, "original_report": matches[0],
                  "command": command, "actual_exit_code": actual.returncode,
                  "expected_tests": suite["expected_tests"], "actual_counts": checked["test_counts"],
                  "project_material_source_kind": captured["source"]["source_kind"],
                  "fault_input_source_kind": "synthetic", "runtime_kind": "mock",
                  "scope": suite["scope"], "limits": suite["limits"],
                  "preserved_empty_raw_files": empty_files})
    assert actual.returncode == 0, "Original fault suite failed; original child logs/receipt retained"
    assert checked["test_counts"] == {"executed": suite["expected_tests"], "failed": 0, "skipped": 0}


def run_boundary(client, case_id):
    fixed_suite(case_id)
    captured = load_binding(case_id)
    evidence = FaultEvidence(captured)
    ingest(client, captured, evidence)
    # The original valid bound bytes are checked before any synthetic injection.
    execute_original_suite(case_id, captured, evidence)
    evidence.observe()


def test_fault_documents(client):
    run_boundary(client, "fault-documents")


def test_fault_extension(client):
    run_boundary(client, "fault-extension")
