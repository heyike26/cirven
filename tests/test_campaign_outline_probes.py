"""Original document-outline derivatives; never engineering topology certification."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from test_campaign_probes import bound_source
from scripts import public_campaign as campaign
import campaign_format_probes as formats

ROOT = Path(__file__).resolve().parents[1]
OUTLINE = "# 原创文档根\r\nOriginal paragraph, not a node\n## 子项 & <精确>\n```md\n# Fenced fake heading\n```\n### 尾部实际标题\n# 第二根\n".encode()


def test_outline_follows_only_actual_source_heading_ranges_and_level_parentage():
    nodes = formats.source_outline({"text": OUTLINE.decode()})
    assert [n["line_start"] for n in nodes] == [1, 3, 7, 8]
    assert [n["parent_id"] for n in nodes] == [None, "line-1", "line-3", None]
    assert all(OUTLINE.decode()[n["char_start"]:n["char_end"]] == n["title"] for n in nodes)
    for text in ("No real headings\n", "# \n", "# heading\n" * 65, "<!--\n# Comment is not a heading\n-->\n"):
        with pytest.raises(ValueError):
            formats.source_outline({"text": text})


@pytest.mark.parametrize("case_id,node", [("source-vsdx", "test_source_vsdx"), ("source-xmind", "test_source_xmind")])
def test_actual_source_heading_derivatives_import_exact_nodes_edges_and_tail_anchors(bound_source, case_id, node):
    source = bound_source["prepare"](case_id, OUTLINE)
    command = [sys.executable, "-m", "pytest", "tests/campaign_format_probes.py::" + node, "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Original captured heading hierarchy only produces VSDX/XMind actual import/raw/parse/nodes/edges/tail anchors, exact original ranges, no fictional root/node/topology/images or public source certification"]
    for p in (source["binding_path"], *[source["project"] / name for name in ("source.raw", "source.json", "license.raw")]):
        launcher += ["--reference", p.relative_to(ROOT).as_posix()]
    launcher += ["E11-outline-probe-" + uuid.uuid4().hex, "--", *command]
    result = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(source["binding_path"])}, capture_output=True)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    evidence = ROOT / next(line.removeprefix("Evidence: ") for line in result.stdout.decode().splitlines() if line.startswith("Evidence: "))
    checked = campaign.checked_evidence(ROOT, evidence, source["binding_path"], source["source"], command, 0, True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    trace = json.loads((evidence / "derived-format-trace.json").read_bytes())
    assert trace["parent_raw_sha256"] == campaign.digest(OUTLINE)
    assert trace["graph_semantics"] == "ATX heading hierarchy only, not engineering topology"
    assert trace["render"]["rendered"] is False and not trace["ocr_executed"]
    assert len(json.loads((evidence / "actual-outline-source-map.json").read_bytes())) == 4
    assert json.loads((evidence / "campaign-observation.json").read_bytes())["source_kind"] == "synthetic"
