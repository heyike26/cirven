"""Original mapped-format regression; real import, not public source certification."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

import campaign_format_probes as formats
from scripts import public_campaign as campaign
from test_campaign_probes import bound_source
from test_public_campaign_provenance import COMMIT, ORIGIN, SCHEMA, span

ROOT = Path(__file__).resolve().parents[1]


def mapped_source(bound, case, texts=None):
    texts = texts or [("docs/first.rst", "Original document\r\n=================\r\n\r\nFirst heading\r\n-------------\r\nActual original body é & value\r\n"),
             ("docs/tail.md", "Markdown original\n=================\n\n## Tail exact")]
    material = "EEAS PUBLIC AUDIT MATERIAL\n# GENERATED WRAPPER ROOT\n\n"
    selected = []
    for name, text in texts:
        material += "# GENERATED WRAPPER FOR " + name + "\n\n"
        start = len(material)
        material += text
        selected.append({"path": "original/" + name, "source_path": name,
            "archive_member": "Fixture-" + COMMIT + "/" + name, "source_url": ORIGIN + "/blob/" + COMMIT + "/" + name,
            "sha256": campaign.digest(text.encode()), "byte_length": len(text.encode()), "characters": len(text),
            "body": span(material, start, len(material)), "original": span(text, 0, len(text))})
        if not text.endswith("\n"):
            material += "\n"
    bound["prepare"](case, material.encode())
    target = bound["project"] / "provenance"
    target.mkdir()
    for (name, text), item in zip(texts, selected):
        path = target / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    regions, cursor = [], 0
    for start, end in [(x["body"]["char_start"], x["body"]["char_end_exclusive"]) for x in selected] + [(len(material), len(material))]:
        if cursor < start:
            regions.append({"kind": "GENERATED_WRAPPER", **span(material, cursor, start)})
        cursor = end
    sidecar = {"schema": SCHEMA, "parent_archive": {"sha256": "b" * 64, "origin": ORIGIN, "commit_sha": COMMIT,
        "archive_url": "https://codeload.github.com/Original/Fixture/zip/" + COMMIT},
        "material": {"sha256": campaign.digest(material.encode()), "byte_length": len(material.encode()), "characters": len(material)},
        "selected": selected, "synthetic_ranges": regions, "conversion": {"version": "original-format-fixture-v1", "input_manifest_sha256": "c" * 64},
        "source_authentication": "DECLARED_NOT_INDEPENDENTLY_VERIFIED"}
    path = target / "sidecar.json"
    path.write_bytes(campaign.encode(sidecar))
    source = bound["source"]
    source.update(origin=ORIGIN, revision=COMMIT, provenance={"schema": SCHEMA,
        "path": "provenance/sidecar.json", "sha256": campaign.digest(path.read_bytes())})
    metadata = bound["project"] / "source.json"
    metadata.write_bytes(campaign.encode(source))
    binding = bound["binding"]
    binding.update(project=source, source_json_sha256=campaign.digest(metadata.read_bytes()),
        provenance_path=path.relative_to(ROOT).as_posix())
    bound["binding_path"].write_bytes(campaign.encode(binding))
    return sidecar


def execute(bound, node):
    command = [sys.executable, "-m", "pytest", "tests/campaign_format_probes.py::" + node,
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
        "Original synthetic mapped bodies only drive actual derived format API/raw/parse/assets/graph. No generated wrapper is selected as text/heading; original EOF/char/byte/line and per-file RST/Setext/ATX hierarchy map are exact, limits remain12lines1200characters64headings; no upstream-native-format/public/model/GUI qualification"]
    refs = [bound["binding_path"], *[bound["project"] / name for name in ("source.raw", "source.json", "license.raw")]]
    if bound["source"].get("provenance"):
        refs += [p for p in (bound["project"] / "provenance").rglob("*") if p.is_file() and
                 not (bound.get("omit_original_references") and "original" in p.parts)]
    for path in refs:
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-mapped-format-probe-" + uuid.uuid4().hex, "--", *command]
    result = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(bound["binding_path"])},
                            capture_output=True, check=False)
    reports = [line.removeprefix("Evidence: ") for line in result.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(reports) == 1, result.stdout.decode() + result.stderr.decode()
    return result, ROOT / reports[0], command


@pytest.mark.parametrize("case,node", [("source-docx", "test_source_docx"), ("source-raster", "test_source_raster"),
    ("source-vsdx", "test_source_vsdx"), ("source-xmind", "test_source_xmind")])
def test_actual_mapped_formats_preserve_original_roles_and_parser_source_anchors(bound_source, case, node):
    definition = mapped_source(bound_source, case)
    result, report, command = execute(bound_source, node)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command, 0, True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    trace = json.loads((report / "derived-format-trace.json").read_bytes())
    values = trace["source_outline"] if case in {"source-vsdx", "source-xmind"} else trace["selected_source"]
    assert all("GENERATED WRAPPER" not in item.get("title", item.get("text", "")) for item in values)
    for item in values:
        source = item["original_source"]
        original = (bound_source["project"] / "provenance/original" / source["path"]).read_bytes()
        value = item["title"] if "title" in item else item["raw_text"]
        assert original[source["byte_start"]:source["byte_end"]].decode() == value
        assert original.decode()[source["char_start"]:source["char_end"]] == value
        assert source["sha256"] == campaign.digest(original)
    if case in {"source-vsdx", "source-xmind"}:
        assert [item["title"] for item in values] == ["Original document", "First heading", "Markdown original", "Tail exact"]
        assert [item["level"] for item in values] == [1, 2, 1, 2]
        assert values[0]["parent_id"] is None and values[2]["parent_id"] is None
        assert values[1]["parent_id"] == values[0]["id"] and values[3]["parent_id"] == values[2]["id"]
    else:
        assert values[-1]["raw_text"] == "## Tail exact"
        assert values[-1]["original_source"]["generated_terminal_separator_excluded"] is True


@pytest.mark.parametrize("fault", ["missing_map", "unfrozen_original", "wrapper_selection"])
def test_composite_derivative_fails_without_valid_original_selection_and_has_no_observation(bound_source, monkeypatch, fault):
    if fault == "missing_map":
        bound_source["prepare"]("source-docx", b"EEAS PUBLIC AUDIT MATERIAL\n# GENERATED ONLY\n")
    else:
        mapped_source(bound_source, "source-docx")
        if fault == "unfrozen_original":
            bound_source["omit_original_references"] = True
        else:
            monkeypatch.setenv("EEAS_CAMPAIGN_SOURCE_LINES", "1")
    result, report, command = execute(bound_source, "test_source_docx")
    assert result.returncode == 1
    assert not (report / "campaign-observation.json").exists()
    assert not (report / "derived-original.bin").exists()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command, 1, False)
    assert checked["test_counts"] == {"executed": 1, "failed": 1, "skipped": 0}


def test_rst_overline_and_single_line_setext_exact_title_coordinates():
    rst = "=========\n Document\n=========\n\nSection\n=======\n\nSubsection\n----------\n\n    Fake code\n    ---------\n"
    nodes = formats.adorned_outline(rst, "rst")
    assert [node["title"] for node in nodes] == ["Document", "Section", "Subsection"]
    assert [node["level"] for node in nodes] == [1, 2, 3]
    assert all(rst[node["char_start"]:node["char_end"]] == node["title"] for node in nodes)
    assert formats.adorned_outline("==\nLong title\n==========\n", "rst") == []
    with pytest.raises(ValueError, match="Multiline Setext"):
        formats.adorned_outline("A multiline\ntitle\n=====\n", "markdown")


def test_mapped_outline_total_limit_requires_explicit_exact_original_file_selection(bound_source, monkeypatch):
    rst = "\n".join(f"Actual original section {i}\n==========================\n" for i in range(40))
    markdown = "\n".join(f"# Actual original second file heading {i}\n" for i in range(30))
    mapped_source(bound_source, "source-vsdx", [("docs/first.rst", rst), ("docs/second.md", markdown)])
    monkeypatch.delenv("EEAS_CAMPAIGN_OUTLINE_FILES", raising=False)
    process, report, command = execute(bound_source, "test_source_vsdx")
    assert process.returncode == 1
    assert "at most64" in (report / "stdout.txt").read_text()
    assert not (report / "campaign-observation.json").exists() and not (report / "derived-original.bin").exists()
    assert campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command, 1, False)["test_counts"]["failed"] == 1
    monkeypatch.setenv("EEAS_CAMPAIGN_OUTLINE_FILES", "GENERATED_WRAPPER")
    process, report, command = execute(bound_source, "test_source_vsdx")
    assert process.returncode == 1 and "unique exact original source paths" in (report / "stdout.txt").read_text()
    assert not (report / "campaign-observation.json").exists()
    campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command, 1, False)
    monkeypatch.setenv("EEAS_CAMPAIGN_OUTLINE_FILES", "docs/first.rst")
    process, report, command = execute(bound_source, "test_source_vsdx")
    assert process.returncode == 0, process.stdout.decode() + process.stderr.decode()
    campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"], command, 0, True)
    trace = json.loads((report / "derived-format-trace.json").read_bytes())
    assert trace["outline_coverage"] == "EXPLICIT_ORIGINAL_FILE_SELECTION"
    assert trace["outline_source_files"] == ["docs/first.rst"] and len(trace["source_outline"]) == 40
    assert [node["title"] for node in trace["source_outline"]] == [f"Actual original section {i}" for i in range(40)]
