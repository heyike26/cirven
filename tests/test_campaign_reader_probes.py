"""Contract tests execute explicit reader probes with original sources and real receipts."""
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
from test_campaign_workflow_provenance import mapped
from test_public_campaign_provenance import inputs

ROOT = Path(__file__).resolve().parents[1]


def execute(source, node):
    binding = source["binding_path"]
    command = [sys.executable, "-m", "pytest", "tests/campaign_reader_probes.py::" + node,
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Bound original reader/revision-search through actual API/SQLite, exact Unicode/CRLF bytes, "
                "all historical and current search assertions; synthetic only, no padding or corporate runtime"]
    refs = [binding, *[source["project"] / name for name in ("source.raw", "source.json", "license.raw")]]
    if "provenance" in source["source"]:
        sidecar = source["project"] / source["source"]["provenance"]["path"]
        refs.append(sidecar)
        refs.extend(sidecar.parent / item["path"] for item in json.loads(sidecar.read_bytes())["selected"])
    for path in refs:
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-reader-probe-" + uuid.uuid4().hex, "--", *command]
    actual = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(binding)},
                            capture_output=True, check=False)
    reports = [line.removeprefix("Evidence: ") for line in actual.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(reports) == 1, actual.stdout.decode() + actual.stderr.decode()
    report = ROOT / reports[0]
    frozen = json.loads((report / "freeze.json").read_bytes())["code_sha256"]
    changed = [p for p, digest in frozen.items() if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != digest]
    assert not changed, changed
    (report / "source-after.json").write_bytes(campaign.encode({"frozen_count": len(frozen), "changed": changed}))
    if os.environ.get("EEAS_EVIDENCE_DIR"):
        target = Path(os.environ["EEAS_EVIDENCE_DIR"]) / (source["case_id"] + "-" + binding.parent.name + "-actual-child.json")
        target.write_bytes(campaign.encode({"report": reports[0], "command": command, "exit_code": actual.returncode}))
    return actual, report, command


@pytest.mark.parametrize("case,node,raw", [
    ("source-reader", "test_source_reader", "# 原创\r\nUnicode Straße 😀 坐标\r\n原始尾部\n".encode()),
    ("source-reader", "test_source_reader", ("甲😀" * 36000 + "尾ß检索").encode()),
    ("source-history", "test_source_history_search", "# Original revision search\r\n原文唯一查询 Straße 😀 preserves upstream bytes\r\n".encode()),
], ids=["reader-crlf-unicode", "reader-72k-one-line", "history-current-vs-pinned"])
def test_reader_and_search_use_actual_source_without_loss(bound_source, case, node, raw):
    bound_source["prepare"](case, raw)
    actual, report, command = execute(bound_source, node)
    assert actual.returncode == 0, actual.stdout.decode() + actual.stderr.decode()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command,
                                         actual.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    assert (report / "actual-original.bin").read_bytes() == raw
    if case == "source-reader":
        pages = json.loads((report / "actual-reader-windows.json").read_bytes())
        assert "".join(p["content"] for p in pages) == raw.decode()
    else:
        data = json.loads((report / "actual-history-search.json").read_bytes())
        assert data["current_pages"][0]["hits"] == [] and data["historical_pages"][0]["hits"]


def test_mapped_history_queries_original_body_not_generated_wrapper(bound_source, inputs):
    mapped(bound_source, inputs)
    bound_source["case_id"] = bound_source["binding"]["case_id"] = "source-history"
    bound_source["binding_path"].write_bytes(campaign.encode(bound_source["binding"]))
    actual, report, command = execute(bound_source, "test_source_history_search")
    assert actual.returncode == 0, actual.stdout.decode()
    data = json.loads((report / "actual-history-search.json").read_bytes())
    assert "Generated" not in data["query"]
    assert all(line.get("original_source") for line in data["selected_original_lines"])
    assert campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command,
                                     actual.returncode, observation_required=True)["test_counts"]["failed"] == 0


def test_no_binding_fails_before_api_use(monkeypatch, client):
    monkeypatch.delenv("EEAS_CAMPAIGN_BINDING", raising=False)
    spec = importlib.util.spec_from_file_location("reader_probes", ROOT / "tests/campaign_reader_probes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="required"):
        module.test_source_reader(client)
    assert client.get("/api/projects").json()["projects"] == []


def test_text_search_reaches_last_original_body_line_not_composite_footer(bound_source, inputs):
    mapped(bound_source, inputs)
    bound_source["case_id"] = bound_source["binding"]["case_id"] = "source-text"
    bound_source["binding_path"].write_bytes(campaign.encode(bound_source["binding"]))
    actual, report, command = execute(bound_source, "test_source_text_original_tail")
    assert actual.returncode == 0, actual.stdout.decode()
    selected = json.loads((report / "actual-original-tail-source.json").read_bytes())
    assert selected["content"] == "真实末尾" and selected["physical_line"] == "真实末尾"
    assert selected["original_source"]["generated_terminal_separator_excluded"] is True
    pages = json.loads((report / "actual-original-tail-search-pages.json").read_bytes())
    assert any(hit["line_start"] == selected["line"] for page in pages for hit in page["hits"])
    assert campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command,
                                     actual.returncode, observation_required=True)["test_counts"]["failed"] == 0
