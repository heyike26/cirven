"""Independent fixed fidelity assertions; all source/transport data synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

import campaign_format_probes as formats
from scripts import public_campaign as campaign
from test_campaign_format_provenance import mapped_source
from test_campaign_probes import bound_source

ROOT = Path(__file__).resolve().parents[1]


def test_adjacent_rst_section_preserves_every_literal_original_heading_coordinate():
    original = "=======\n Root\n=======\n\nParent\n======\nChild\n-----\nLiteral original body\n"
    nodes = formats.adorned_outline(original, "rst")
    assert [node["title"] for node in nodes] == ["Root", "Parent", "Child"], "Adjacent original section silently omitted"
    assert [node["level"] for node in nodes] == [1, 2, 3]
    assert [node["line_start"] for node in nodes] == [2, 5, 7]
    assert all(original[node["char_start"]:node["char_end"]] == node["title"] for node in nodes)


@pytest.mark.parametrize("case,node", [("source-docx", "test_source_docx_fidelity"),
    ("source-raster", "test_source_raster_fidelity"), ("source-vsdx", "test_source_vsdx_fidelity"),
    ("source-xmind", "test_source_xmind_fidelity")])
def test_actual_bound_format_fidelity_node_closes_explicit_catalog_subclauses(bound_source, case, node):
    mapped_source(bound_source, case)
    command = [sys.executable, "-m", "pytest", "tests/campaign_format_fidelity_probes.py::" + node,
        "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
        "Original synthetic frozen source bodies through actual import. Strict DOCX table-cell/precise drawing/actual context parse receipt; PNG and JPEG hash/dimension/public-preview/noOCR; real Visio Connects/XMind explicit relationships and hierarchy/source coordinates. Keep12lines1200chars64actual nodes and original protected tests. Source is selected document outline, not full project/engineering/native-app/company qualification."]
    for path in (bound_source["binding_path"], *[bound_source["project"] / name for name in ("source.raw", "source.json", "license.raw")],
                 *[path for path in (bound_source["project"] / "provenance").rglob("*") if path.is_file()]):
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-format-fidelity-probe-" + uuid.uuid4().hex, "--", *command]
    result = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(bound_source["binding_path"])},
                            capture_output=True, check=False)
    reports = [line.removeprefix("Evidence: ") for line in result.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(reports) == 1, result.stdout.decode() + result.stderr.decode()
    report = ROOT / reports[0]
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command, 0, True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    observation = json.loads((report / "campaign-observation.json").read_bytes())
    assert observation["source_kind"] == "synthetic" and observation["model_calls"] == 0
