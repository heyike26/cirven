"""Explicit format fidelity gates over real bound-source imports.

The initial delegate retains the old converter/import paths so the independent
regression can expose missing fidelity evidence before implementation changes.
Original headings are document outline data, never engineering topology.
"""
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import campaign_format_probes as formats
from campaign_probes import Evidence, load_binding, sha

V = "http://schemas.microsoft.com/office/visio/2012/main"
W = formats.W
R = formats.R
A = formats.A


def artifact(evidence, name):
    return json.loads((evidence.root / name).read_bytes())


def check_docx(evidence):
    trace = artifact(evidence, "derived-format-trace.json")
    parsed = artifact(evidence, "actual-format-document.json")
    assets = artifact(evidence, "actual-format-assets.json")
    raw = (evidence.root / "derived-original.bin").read_bytes()
    assert sha(raw) == trace["derived_sha256"] == parsed["sha256"]
    nodes = parsed["graph"]["nodes"]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        rows = document.findall("./{" + W + "}body/{" + W + "}tbl/{" + W + "}tr")
        assert len(rows) == len(trace["selected_source"])
        for row_index, (row, original) in enumerate(zip(rows, trace["selected_source"]), 1):
            cells = row.findall("{" + W + "}tc")
            expected = [f"source line {original['line_start']} chars {original['char_start']}:{original['char_end']}", original["text"]]
            assert len(cells) == 2
            for cell_index, (cell, text) in enumerate(zip(cells, expected), 1):
                assert ''.join(item.text or '' for item in cell.iter("{" + W + "}t")) == text
                path = f"/document[1]/body[1]/tbl[1]/tr[{row_index}]/tc[{cell_index}]"
                actual = next(node for node in nodes if node["source"]["part"] == "word/document.xml" and node["source"]["node_id"] == path)
                assert actual["kind"] == "table-cell"
                child = next(node for node in nodes if node["source"]["node_id"] == path + "/p[1]")
                assert child["parent_id"] == actual["id"] and child["text"] == text
                assert text in '\n'.join(parsed["content"].splitlines()[child["line_start"]-1:child["line_end"]])
        picture_paragraph = len(trace["selected_source"]) + 1
        body = document.find("{" + W + "}body")
        paragraph = body.findall("{" + W + "}p")[picture_paragraph-1]
        blip = paragraph.find(".//{" + A + "}blip")
        assert blip is not None and blip.attrib["{" + R + "}embed"] == "sourceImage"
        relationships = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        image_relation = next(item for item in relationships if item.attrib["Id"] == "sourceImage")
        assert image_relation.attrib["Type"] == R + "/image" and image_relation.attrib["Target"] == "media/source.png"
        image = archive.read("word/media/source.png")
    assert len(assets) == 1 and assets[0]["member_path"] == "word/media/source.png"
    assert sha(image) == assets[0]["sha256"] == sha((evidence.root / "actual-asset-0.bin").read_bytes())
    assert assets[0]["source_anchors"] == [{"part": "word/document.xml", "node_id": f"/document[1]/body[1]/p[{picture_paragraph}]",
        "scope": "docx:word/document.xml", "anchor_kind": "xml-local-path"}]
    # This must be the saved actual public context response, not a constructed receipt.
    context = artifact(evidence, "actual-format-context.json")
    assert context["document_id"] == parsed["id"] and context["revision"] == 1 and context["sha256"] == sha(raw)
    assert context["coverage"] == "COMPLETE" and context["truncated"] is False
    assert ''.join(chunk["content"] for chunk in context["chunks"]) == parsed["content"]
    content_sha = sha(parsed["content"].encode())
    manifest = {key: parsed[key] for key in ("format", "parser_version", "outline", "graph", "warnings")}
    manifest.update(content_sha256=content_sha, assets=assets)
    expected_sha = sha(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    assert context["parse_receipt"] == {"content_sha256": content_sha, "parse_manifest_sha256": expected_sha}


def check_diagram(evidence, suffix):
    trace = artifact(evidence, "derived-format-trace.json")
    outline = trace["source_outline"]
    parsed = artifact(evidence, "actual-format-document.json")
    raw = (evidence.root / "derived-original.bin").read_bytes()
    nodes = parsed["graph"]["nodes"]
    title_nodes = {original["id"]: next(node for node in nodes if node["source"]["node_id"] ==
        (str(original["line_start"]) if suffix == "vsdx" else original["id"])) for original in outline}
    expected = {(title_nodes[n["parent_id"]]["id"], title_nodes[n["id"]]["id"]) for n in outline if n["parent_id"]}
    assert expected, "Fidelity material must contain actual original heading parent-child relations"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if suffix == "vsdx":
            page = ET.fromstring(archive.read("visio/pages/page1.xml"))
            connects = page.findall("./{" + V + "}Connects/{" + V + "}Connect")
            assert len(connects) == 2 * len(expected), "Actual Visio Connects BeginX/EndX are missing"
            by_connector = {}
            for connection in connects:
                endpoints = by_connector.setdefault(connection.attrib["FromSheet"], {})
                assert connection.attrib["FromCell"] not in endpoints
                endpoints[connection.attrib["FromCell"]] = connection.attrib["ToSheet"]
            physical = {(value["BeginX"], value["EndX"]) for value in by_connector.values()}
            assert physical == {(str(n["line_start"]), str(child["line_start"])) for child in outline if child["parent_id"] for n in outline if n["id"] == child["parent_id"]}
            links = [edge for edge in parsed["graph"]["edges"] if edge["kind"] == "connector"]
            assert {(edge["source"], edge["target"]) for edge in links} == expected
            assert len(links) == len(expected) and len(nodes) <= 64
            assert len(parsed["outline"]["pages"]) == 1 and parsed["outline"]["pages"][0]["source"]["part"] == "visio/pages/page1.xml"
            assert all(edge["origin"]["part"] == "visio/pages/page1.xml" for edge in links)
            attachments = [edge for edge in parsed["graph"]["edges"] if edge["kind"] == "attachment"]
            assert len(attachments) == len(connects)
            assert all(edge["source"] and edge["target"] and edge["origin"]["node_id"].startswith("Connect:") for edge in attachments)
        else:
            sheets = json.loads(archive.read("content.json"))
            roots = [node for node in outline if not node["parent_id"]]
            assert [sheet["rootTopic"]["id"] for sheet in sheets] == [node["id"] for node in roots]
            relationships = [relation for sheet in sheets for relation in sheet.get("relationships", [])]
            assert len(relationships) == len(expected), "Actual XMind relationships are missing"
            assert {(relation["end1Id"], relation["end2Id"]) for relation in relationships} == {(n["parent_id"], n["id"]) for n in outline if n["parent_id"]}
            links = [edge for edge in parsed["graph"]["edges"] if edge["kind"] == "relationship"]
            assert {(edge["source"], edge["target"]) for edge in links} == expected and len(links) == len(expected)
            assert all(edge["origin"]["part"] == "content.json" for edge in links)
        assert {(edge["source"], edge["target"]) for edge in parsed["graph"]["edges"] if edge["kind"] == "hierarchy"} == expected
    assert all(node["text"] == original["title"] for identifier, node in title_nodes.items() for original in outline if original["id"] == identifier)


def check_raster(evidence):
    png = artifact(evidence, "actual-format-assets.json")[0]
    jpeg = artifact(evidence, "jpeg-actual-format-assets.json")[0]
    assert png["media_type"] == "image/png" and jpeg["media_type"] == "image/jpeg"
    for prefix, asset in (("", png), ("jpeg-", jpeg)):
        preview = artifact(evidence, prefix + "actual-preview-response.json")
        assert preview["status_code"] == 200 and preview["content_type"] == asset["media_type"]
        assert preview["sha256"] == asset["sha256"] == sha((evidence.root / (prefix + "actual-asset-0.bin")).read_bytes())
        assert preview["etag"] == '"' + asset["sha256"] + '"' and preview["nosniff"] == "nosniff"
        pixels = formats.Image.open(io.BytesIO((evidence.root / (prefix + "actual-asset-0.bin")).read_bytes()))
        assert pixels.size == (asset["width"], asset["height"]) == (png["width"], png["height"])
        parsed = artifact(evidence, prefix + "actual-format-document.json")
        assert "IMAGE_TEXT_NOT_EXTRACTED" in parsed["warnings"] and "MODEL_VISION_NOT_VALIDATED" in parsed["warnings"]
        assert not asset["source_anchors"]


def record_context(client, parsed, evidence):
    base = f"/api/projects/{parsed['project_id']}/documents/{parsed['id']}"
    request = {"revision": 1}
    response = client.post(base + "/context", json=request)
    evidence.save("actual-format-context-response.json", {"route": base + "/context", "request": request,
        "status_code": response.status_code, "response": response.json()})
    assert response.status_code == 200, response.text
    evidence.save("actual-format-context.json", response.json())


def record_preview(client, parsed, assets, evidence, prefix=""):
    assert len(assets) == 1
    base = f"/api/projects/{parsed['project_id']}/documents/{parsed['id']}"
    response = client.get(base + f"/assets/{assets[0]['asset_id']}", params={"revision": 1})
    evidence.save(prefix + "actual-preview-response.json", {"status_code": response.status_code,
        "content_type": response.headers.get("content-type"), "sha256": sha(response.content),
        "etag": response.headers.get("etag"), "nosniff": response.headers.get("x-content-type-options"),
        "route": base + f"/assets/{assets[0]['asset_id']}", "revision": 1})


def finish(evidence, details):
    evidence.save("actual-format-fidelity.json", {"schema": "eeas.derived-format-fidelity.v1", **details,
        "scope": "Explicit selected original document material; not whole-project engineering/topology, native desktop app or model/OCR qualification"})
    evidence.observe()


def test_source_docx_fidelity(client):
    captured = load_binding("source-docx")
    evidence = Evidence(captured)
    selected = formats.selections(captured, evidence)
    image, rendering = formats.render(selected)
    raw = formats.word(selected, image)
    evidence.save("derived-format-trace.json", formats.derived_trace(captured, selected, rendering, raw, "docx"))
    parsed, assets = formats.import_derived(client, captured, evidence, raw, "derived-source.docx")
    assert parsed["format"] == "docx"
    for index, original in enumerate(selected, 1):
        node = next(node for node in parsed["graph"]["nodes"] if node["source"]["node_id"] == f"/document[1]/body[1]/p[{index}]")
        assert node["text"] == original["text"]
        assert original["text"] in "\n".join(parsed["content"].splitlines()[node["line_start"]-1:node["line_end"]])
    record_context(client, parsed, evidence)
    check_docx(evidence)
    finish(evidence, {"format": "docx", "table_rows": len(selected), "table_cells": 2*len(selected),
        "drawing_source_anchors": assets[0]["source_anchors"], "actual_parse_receipt": artifact(evidence, "actual-format-context.json")["parse_receipt"]})


def test_source_raster_fidelity(client):
    captured = load_binding("source-raster")
    evidence = Evidence(captured)
    selected = formats.selections(captured, evidence)
    raw, rendering = formats.render(selected)
    trace = formats.derived_trace(captured, selected, rendering, raw, "png")
    parsed, assets = formats.import_derived(client, captured, evidence, raw, "derived-source.png")
    record_preview(client, parsed, assets, evidence)
    output = io.BytesIO()
    formats.Image.open(io.BytesIO(raw)).convert("RGB").save(output, format="JPEG", quality=95)
    jpeg = output.getvalue()
    jpeg_parsed, jpeg_assets = formats.import_derived(client, captured, evidence, jpeg, "derived-source.jpg", prefix="jpeg-")
    record_preview(client, jpeg_parsed, jpeg_assets, evidence, prefix="jpeg-")
    trace["additional_derived_formats"] = [{"format": "jpeg", "sha256": sha(jpeg), "quality": 95,
        "transform": "Explicit lossy JPEG encoding of the faithful selected-character PNG; no pixel-identical or OCR claim"}]
    evidence.save("derived-format-trace.json", trace)
    check_raster(evidence)
    finish(evidence, {"formats": ["png", "jpeg"], "asset_sha256": [assets[0]["sha256"], jpeg_assets[0]["sha256"]],
        "warnings": {"png": parsed["warnings"], "jpeg": jpeg_parsed["warnings"]}, "ocr_executed": False})


def fidelity_diagram(client, suffix):
    captured = load_binding("source-" + suffix)
    evidence = Evidence(captured)
    outline = formats.source_outline(captured, evidence)
    raw = formats.outline_archive(outline, suffix, include_hierarchy_links=True)
    trace = formats.derived_trace(captured, [], {"rendered": False, "geometry": "not claimed"}, raw, suffix)
    trace.update(converter="eeas.derived-document-outline.fidelity.v1", source_outline=outline,
        graph_semantics="Original document heading hierarchy; explicit derived links repeat only original parent-child relations, not engineering topology",
        outline_source_files=sorted({node["original_source"]["path"] for node in outline if "original_source" in node}),
        outline_coverage="EXPLICIT_ORIGINAL_FILE_SELECTION" if formats.os.environ.get("EEAS_CAMPAIGN_OUTLINE_FILES") else "ALL_FROZEN_ORIGINAL_BODIES",
        outline_file_selection=formats.os.environ.get("EEAS_CAMPAIGN_OUTLINE_FILES"), selected_complete_source=False,
        links=[{"parent": node["parent_id"], "child": node["id"], "semantic": "derived original-heading parent-child"} for node in outline if node["parent_id"]])
    evidence.save("derived-format-trace.json", trace)
    parsed, assets = formats.import_derived(client, captured, evidence, raw, "derived-outline." + suffix)
    assert not assets
    for original in outline:
        assert captured["text"][original["char_start"]:original["char_end"]] == original["title"]
    check_diagram(evidence, suffix)
    finish(evidence, {"format": suffix, "heading_count": len(outline), "actual_diagram_node_count": len(parsed["graph"]["nodes"]),
        "original_hierarchy_count": len(trace["links"]), "actual_edge_kinds": [edge["kind"] for edge in parsed["graph"]["edges"]],
        "original_heading_map": outline, "actual_graph": parsed["graph"], "actual_sheets_or_pages": parsed["outline"]})


def test_source_vsdx_fidelity(client):
    fidelity_diagram(client, "vsdx")


def test_source_xmind_fidelity(client):
    fidelity_diagram(client, "xmind")
