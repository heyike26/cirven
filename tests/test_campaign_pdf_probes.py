"""Actual imported source-derived PDF contracts, with font failures retained."""
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


def execute(source):
    command = [sys.executable, "-m", "pytest", "tests/campaign_pdf_probes.py::test_source_pdf",
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Actual source-derived PDF Unicode text/embedded glyph-mapped font/scan bitmap through real API, "
                "immutable parent original bytes, layout mapping, parsed pages/image pixels/anchors and no fake OCR; "
                "bounded original selection, synthetic source only, no upstream or corporate model qualification"]
    for path in (source["binding_path"], *[source["project"] / name for name in ("source.raw", "source.json", "license.raw")]):
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-pdf-probe-" + uuid.uuid4().hex, "--", *command]
    actual = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(source["binding_path"])},
                            capture_output=True, check=False)
    paths = [line.removeprefix("Evidence: ") for line in actual.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(paths) == 1, actual.stdout.decode() + actual.stderr.decode()
    report = ROOT / paths[0]
    frozen = json.loads((report / "freeze.json").read_bytes())["code_sha256"]
    assert not [p for p, digest in frozen.items() if campaign.digest((ROOT / p).read_bytes()) != digest]
    (report / "source-after.json").write_bytes(campaign.encode({"all_unchanged": True, "frozen_files": len(frozen)}))
    if os.environ.get("EEAS_EVIDENCE_DIR"):
        (Path(os.environ["EEAS_EVIDENCE_DIR"]) / (source["binding_path"].parent.name + "-actual-pdf-report.json")).write_bytes(
            campaign.encode({"actual_report": paths[0], "exit_code": actual.returncode, "command": command}))
    return actual, report, command


@pytest.mark.parametrize("raw", [
    b"# Original () [] backslash \\ text\r\nActual tail source needs correct PDF pages\r\n",
    "# Résumé café original\r\nStraße naïve source preserved\r\n".encode(),
], ids=["ascii-specials", "latin-unicode-crlf"])
def test_pdf_pages_and_scan_preserve_source_mapping_and_bitmap(bound_source, raw):
    bound_source["prepare"]("source-pdf", raw)
    actual, report, command = execute(bound_source)
    assert actual.returncode == 0, actual.stdout.decode() + actual.stderr.decode()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command,
                                         actual.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    trace = json.loads((report / "actual-pdf-derivation.json").read_bytes())
    assert trace["parent_raw_sha256"] == campaign.digest(raw)
    assert trace["conversion"]["font_cmap_coverage_checked"] is True
    assert trace["conversion"]["embedded_font_sha256"] == trace["conversion"]["font_sha256"]
    assert (report / "actual-original.bin").read_bytes() == raw
    pages = json.loads((report / "actual-pdf-document.json").read_bytes())["outline"]["pages"]
    assert [p["text_extracted"] for p in pages] == [True, True, False]


def test_unsupported_glyph_has_real_failed_receipt_without_fake_pdf_or_ocr(bound_source):
    raw = "原文字符不能被替换成方框\r\n完整实际尾部\r\n".encode()
    bound_source["prepare"]("source-pdf", raw)
    actual, report, command = execute(bound_source)
    assert actual.returncode == 1 and "refusing silent replacement" in actual.stdout.decode()
    assert (report / "actual-original.bin").read_bytes() == raw
    assert not (report / "source-derived.pdf").exists()
    assert not (report / "campaign-observation.json").exists()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command,
                                         actual.returncode, observation_required=False)
    assert checked["test_counts"] == {"executed": 1, "failed": 1, "skipped": 0}
