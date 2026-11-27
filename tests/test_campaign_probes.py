"""Original probe regression, never public-project certification or model evidence."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from scripts import public_campaign as campaign

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("explicit_source_probes", ROOT / "tests/campaign_probes.py")
probes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probes)


@pytest.fixture
def bound_source(monkeypatch):
    target = ROOT / "var" / ("original-probe-" + uuid.uuid4().hex)
    project = target / "projects/original"
    project.mkdir(parents=True)
    case = {"path": target, "project": project}

    def prepare(case_id, raw=b"# Original\r\nREQ: preserve original bytes\r\nOriginal source tail\n"):
        source = {"schema": "eeas.public-project-source.v1", "project_id": "original",
                  "source_kind": "synthetic", "origin": None, "origin_key": None,
                  "revision": "original-fixture-v1", "license_id": "Original-test-fixture",
                  "original_filename": "original.md", "license_filename": "LICENSE",
                  "raw_sha256": campaign.digest(raw), "license_sha256": campaign.digest(b"Original EEAS fixture license\n"),
                  "provenance_status": "DECLARED_NOT_INDEPENDENTLY_VERIFIED"}
        (project / "source.raw").write_bytes(raw)
        (project / "license.raw").write_bytes(b"Original EEAS fixture license\n")
        (project / "source.json").write_bytes(campaign.encode(source))
        binding = {"schema": "eeas.campaign-binding.v1", "case_id": case_id,
                   "project": source, "source_json_sha256": campaign.digest((project / "source.json").read_bytes()),
                   "raw_path": (project / "source.raw").relative_to(ROOT).as_posix()}
        (target / "binding.json").write_bytes(campaign.encode(binding))
        monkeypatch.setenv("EEAS_CAMPAIGN_BINDING", str(target / "binding.json"))
        case.update(binding=binding, source=source, binding_path=target / "binding.json", case_id=case_id)
        return case

    case["prepare"] = prepare
    try:
        yield case
    finally:
        shutil.rmtree(target)


def execute_probe(bound_source, node):
    binding = bound_source["binding_path"]
    command = [sys.executable, "-m", "pytest", "tests/campaign_probes.py::" + node, "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Actual explicit source probe consumes frozen original synthetic bytes through real EEAS API/SQLite; no public-project or origin certification, network/model call, GUI or source padding"]
    for path in (binding, *[bound_source["project"] / name for name in ("source.raw", "source.json", "license.raw")]):
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-source-probe-" + uuid.uuid4().hex, "--", *command]
    result = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(binding)},
                            capture_output=True, check=False)
    lines = result.stdout.decode().splitlines()
    target = ROOT / next(line.removeprefix("Evidence: ") for line in lines if line.startswith("Evidence: "))
    return result, target, command


def test_missing_binding_never_falls_back_to_fixture_or_source(monkeypatch):
    monkeypatch.delenv("EEAS_CAMPAIGN_BINDING", raising=False)
    with pytest.raises(ValueError, match="required"):
        probes.load_binding("source-text")


@pytest.mark.parametrize("mutation", ["case", "raw", "license", "metadata", "symlink"])
def test_wrong_case_or_changed_captured_bytes_fail_closed(bound_source, mutation):
    bound_source["prepare"]("source-text")
    project = bound_source["project"]
    expected = "source-history" if mutation == "case" else "source-text"
    if mutation in {"raw", "license", "metadata"}:
        name = {"raw": "source.raw", "license": "license.raw", "metadata": "source.json"}[mutation]
        (project / name).write_bytes(b"original mutation")
    elif mutation == "symlink":
        (project / "license.raw").unlink()
        (project / "license.raw").symlink_to(project / "source.raw")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        probes.load_binding(expected)


def test_short_source_context_is_actual_failed_case_no_padding_or_observation(bound_source):
    bound_source["prepare"]("source-context")
    result, evidence, command = execute_probe(bound_source, "test_source_context")
    assert result.returncode == 1
    assert "no synthetic padding" in (evidence / "stdout.txt").read_text()
    assert not (evidence / "campaign-observation.json").exists()
    checked = campaign.checked_evidence(ROOT, evidence, bound_source["binding_path"], bound_source["source"],
                                         command, result.returncode, observation_required=False)
    assert checked["test_counts"] == {"executed": 1, "failed": 1, "skipped": 0}


@pytest.mark.parametrize("case_id,node,raw", [
    ("source-text", "test_source_text", "# 原创\r\nUnicode Straße 源行\r\n实际末尾约束\n".encode()),
    ("source-history", "test_source_history", b"# Original upstream-like fixture\nDo not replace prior bytes\n"),
    ("source-context", "test_source_context", ("工程采集边界条件\n" * 8000 + "任务书实际末尾约束\n").encode()),
    ("source-context", "test_source_context", ("工程采集边界条件\n" * 10000 + "超默认预算实际末尾约束\n").encode()),
], ids=["text-crlf-unicode", "history-original", "context-72k-complete", "context-90k-budget-rejected"])
def test_explicit_probes_process_actual_bound_bytes_and_emit_checked_artifacts(bound_source, case_id, node, raw):
    bound_source["prepare"](case_id, raw)
    result, evidence, command = execute_probe(bound_source, node)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    checked = campaign.checked_evidence(ROOT, evidence, bound_source["binding_path"], bound_source["source"],
                                         command, result.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    observation = json.loads((evidence / "campaign-observation.json").read_bytes())
    assert observation["source_kind"] == "synthetic" and observation["runtime_kind"] == "none"
    assert observation["origin_verification"] == "DECLARED_NOT_INDEPENDENTLY_VERIFIED"
    assert (evidence / "actual-original.bin").read_bytes() == raw
    assert observation["source_sha256"] == hashlib.sha256(raw).hexdigest()
    if case_id == "source-context":
        full = json.loads((evidence / "actual-full-context.json").read_bytes())
        assert full["status"] == (200 if len(raw) <= 220000 else 422)
        selected = json.loads((evidence / "actual-selected-context.json").read_bytes())
        assert selected["coverage"] == "PARTIAL_EXPLICIT_SELECTION"
        assert "末尾约束" in selected["chunks"][0]["content"] and selected["truncated"] is False
