"""Original synthetic contracts run all explicit nodes with real isolated Chromium/API."""
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


def execute(source, node):
    command = [sys.executable, "-m", "pytest", "tests/campaign_browser_probes.py::" + node,
               "-q", "-ra", "-p", "scripts.public_campaign"]
    binding = source["binding_path"]
    references = [binding, *[source["project"] / name for name in ("source.raw", "source.json", "license.raw")]]
    if source["source"].get("provenance"):
        sidecar = source["project"] / source["source"]["provenance"]["path"]
        references += [sidecar, *(sidecar.parent / item["path"] for item in json.loads(sidecar.read_bytes())["selected"])]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Actual frozen original synthetic source browser/API/SQLite/Mock Runner/Verifier; "
                "no public-project, company-model, Windows or OS-sandbox qualification"]
    for path in references:
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-browser-probe-" + uuid.uuid4().hex, "--", *command]
    result = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(binding)},
                            capture_output=True, check=False)
    reports = [line.removeprefix("Evidence: ") for line in result.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(reports) == 1, result.stdout.decode() + result.stderr.decode()
    report = ROOT / reports[0]
    frozen = json.loads((report / "freeze.json").read_bytes())["code_sha256"]
    changed = [name for name, digest in frozen.items() if campaign.digest((ROOT / name).read_bytes()) != digest]
    assert not changed, changed
    (report / "source-after.json").write_bytes(campaign.encode({"frozen_count": len(frozen), "changed": changed}))
    if os.environ.get("EEAS_EVIDENCE_DIR"):
        marker = Path(os.environ["EEAS_EVIDENCE_DIR"]) / (source["case_id"] + "-" + binding.parent.name + "-actual-child.json")
        with marker.open("xb") as output:
            output.write(campaign.encode({"report": reports[0], "command": command, "exit_code": result.returncode}))
    return result, report, command


@pytest.mark.parametrize("case_id", ["source-reader", "ui-workspace", "ui-settings"])
def test_explicit_source_bound_browser_node_has_real_receipts(bound_source, case_id):
    # A fresh deliberately long ORIGINAL fixture, never padding a captured public source.
    raw = (f"# 原创浏览器契约 {case_id}\r\n" + "完整原创参数与边界；" * 7400 +
           f"\r\n原文唯一尾行 Straße 😀 {case_id}\n").encode()
    assert len(raw.decode()) >= 70000
    bound_source["prepare"](case_id, raw)
    result, report, command = execute(bound_source, "test_" + case_id.replace("-", "_"))
    assert result.returncode == 0, (report / "stdout.txt").read_text() + (report / "stderr.txt").read_text()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"],
                                         command, result.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    assert (report / "actual-original.bin").read_bytes() == raw
    observation = json.loads((report / "campaign-observation.json").read_bytes())
    assert observation["source_kind"] == "synthetic" and observation["model_calls"] == 0
    assert observation["company_model"] == observation["upstream_engineering"] == "NOT_RUN"
    assert observation["runtime_kind"] == ("mock" if case_id == "ui-workspace" else "none")
    database = json.loads((report / "actual-sqlite-rows.json").read_bytes())
    assert database["document_versions"][0]["sha256"] == campaign.digest(raw)
    assert not database["agent_executions"] and not database["task_native_reviews"]
    assert (report / "actual-store.sqlite3").read_bytes().startswith(b"SQLite format 3\x00")
    assert json.loads((report / "actual-ui-api-receipts.json").read_bytes())
    if case_id == "ui-workspace":
        consumed = json.loads((report / "actual-source-consumption.json").read_bytes())
        assert campaign.digest(raw).encode() in (report / "actual-source-candidate.bin").read_bytes()
        assert consumed["source_lines"][-1]["content"].encode() in (report / "actual-source-candidate.bin").read_bytes()
        assert len(database["runner_jobs"]) == len(database["task_runs"]) == 1


def test_browser_nodes_are_explicit_and_not_default_discovery():
    path = ROOT / "tests/campaign_browser_probes.py"
    spec = importlib.util.spec_from_file_location("explicit_campaign_browser", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {name for name in vars(module) if name.startswith("test_")} == {
        "test_source_reader", "test_ui_workspace", "test_ui_settings"}
    assert not path.name.startswith("test_")
