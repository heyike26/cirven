"""Original derivatives test actual source traces and importer, not upstream formats."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from PIL import Image
import pytest

from test_campaign_probes import bound_source
from scripts import public_campaign as campaign
import campaign_format_probes as formats

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = b"# Original public-like source\r\nActual original line <tag> & value\nA line deliberately omitted from the selection\nLast actual tail must retain its anchor\n"


def test_source_selection_is_explicit_lossless_and_no_silent_truncation(bound_source, monkeypatch):
    bound_source["prepare"]("source-docx", ORIGINAL)
    captured = formats.load_binding("source-docx")
    selected = formats.selections(captured)
    assert [p["line_start"] for p in selected] == [1, 2, 4]
    assert all(captured["text"][p["char_start"]:p["char_end"]] == p["raw_text"] for p in selected)
    monkeypatch.setenv("EEAS_CAMPAIGN_SOURCE_LINES", "2,2")
    with pytest.raises(ValueError, match="unique"):
        formats.selections(captured)
    monkeypatch.setenv("EEAS_CAMPAIGN_SOURCE_LINES", "99")
    with pytest.raises(ValueError, match="existing"):
        formats.selections(captured)
    monkeypatch.delenv("EEAS_CAMPAIGN_SOURCE_LINES")
    captured["text"] = "x" * 1201
    with pytest.raises(ValueError, match="no truncation"):
        formats.selections(captured)


def test_bundled_font_coverage_rejects_missing_unicode_not_tofu_success():
    with pytest.raises(ValueError, match="lacks selected Unicode glyphs"):
        formats.render([{"line_start": 1, "text": "中文实际原文"}])
    png, profile = formats.render([{"line_start": 1, "text": "Actual source & original tail"}])
    image = Image.open(io.BytesIO(png))
    assert len(image.getcolors(image.width * image.height)) > 1
    assert profile["font_cmap_coverage_checked"] and profile["font_sha256"]
    assert "".join(row["text"] for row in profile["rows"]) == "Actual source & original tail"


@pytest.mark.parametrize("case_id,node", [("source-docx", "test_source_docx"), ("source-raster", "test_source_raster")])
def test_actual_derived_format_source_trace_import_raw_parse_and_assets(bound_source, case_id, node):
    source = bound_source["prepare"](case_id, ORIGINAL)
    command = [sys.executable, "-m", "pytest", "tests/campaign_format_probes.py::" + node, "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Original synthetic source produces traceable selected-line derived DOCX or PNG; actual API/SQLite/import/raw/parse/assets/anchors, never upstream supplied material or OCR/model/GUI validation"]
    for path in (source["binding_path"], *[source["project"] / n for n in ("source.raw", "source.json", "license.raw")]):
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-format-probe-" + uuid.uuid4().hex, "--", *command]
    process = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(source["binding_path"])}, capture_output=True)
    assert process.returncode == 0, process.stdout.decode() + process.stderr.decode()
    evidence = ROOT / next(line.removeprefix("Evidence: ") for line in process.stdout.decode().splitlines() if line.startswith("Evidence: "))
    checked = campaign.checked_evidence(ROOT, evidence, source["binding_path"], source["source"], command, 0, True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    trace = json.loads((evidence / "derived-format-trace.json").read_bytes())
    assert trace["parent_raw_sha256"] == campaign.digest(ORIGINAL)
    assert trace["material_kind"] == "traceable-derived-format-not-upstream-supplied" and not trace["ocr_executed"]
    assert not trace["selected_complete_source"] and [p["line_start"] for p in trace["selected_source"]] == [1, 2, 4]
    assert trace["derived_sha256"] == campaign.digest((evidence / "derived-original.bin").read_bytes())
    obs = json.loads((evidence / "campaign-observation.json").read_bytes())
    assert obs["source_kind"] == "synthetic" and obs["runtime_kind"] == "none"
