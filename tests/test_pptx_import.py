"""Original public-free Office fixtures; source binding and inert conversion contract."""
from __future__ import annotations
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
import zlib

from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

from packages.domain.diagram_import import DiagramLimits
from packages.domain.pptx_import import parse_pptx, PptxImportError, VENDOR, UPSTREAM_COMMIT


def image_bytes(fmt="PNG"):
    target = BytesIO()
    Image.new("RGB", (3, 2), (32, 120, 210)).save(target, format=fmt)
    return target.getvalue()


def fixture(*, long=False, chart=False, external=False):
    deck = Presentation()
    first = deck.slides.add_slide(deck.slide_layouts[6])
    group = first.shapes.add_group_shape()
    group.shapes.add_textbox(Inches(0), Inches(0), Inches(2), Inches(1)).text = "嵌套来源起点"
    second_group = group.shapes.add_group_shape()
    second_group.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1)).text = "第二层内容"
    text = first.shapes.add_textbox(Inches(0), Inches(3), Inches(5), Inches(1))
    text.text = "会议明确的接口，后续仍待用户答复。"
    if external:
        text.text_frame.paragraphs[0].runs[0].hyperlink.address = "https://invalid.example/private?token=never-expose"
    table = first.shapes.add_table(2, 2, Inches(0), Inches(4), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "列名"
    table.cell(0, 1).text = "值"
    table.cell(1, 0).text = "协议"
    table.cell(1, 1).text = "十六进制|需转义"
    first.shapes.add_picture(BytesIO(image_bytes()), Inches(4), Inches(0), width=Inches(1))
    first.notes_slide.notes_text_frame.text = "会议备注，不是模型新生成。"
    if chart:
        data = CategoryChartData(); data.categories = ["甲", "乙"]; data.add_series("量", [2, 5])
        first.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(4), Inches(2), Inches(2), Inches(2), data)
    second = deck.slides.add_slide(deck.slide_layouts[6])
    second.shapes.add_textbox(Inches(0), Inches(0), Inches(5), Inches(5)).text = (
        "原创会议设计条目：" * 9000 + "资料真实末尾：不要漏掉本行" if long else "另一页的同名不同节点")
    second.shapes.add_picture(BytesIO(image_bytes("JPEG")), Inches(5), Inches(0), width=Inches(1))
    result = BytesIO(); deck.save(result)
    raw = result.getvalue()
    # Preserve the exact runtime fixture bytes, not a later regenerated ZIP timestamp.
    report = os.environ.get('EEAS_EVIDENCE_DIR')
    if report:
        digest = hashlib.sha256(raw).hexdigest()
        folder = Path(__file__).resolve().parents[1] / 'var/pptx-fixtures' / Path(report).name
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / (digest + '.pptx')
        if not file.exists():
            with file.open('xb') as stream: stream.write(raw)
        with (Path(report) / 'raw-fixtures.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'raw_path':str(file),'sha256':digest,'byte_length':len(raw),
                'source_kind':'synthetic','long':long,'chart':chart,'external':external})+'\n')
    return raw


def rewrite(raw, *, edit=None, extra=None, compression=zipfile.ZIP_DEFLATED):
    result = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as source, zipfile.ZipFile(result, "w", compression=compression) as target:
        for item in source.infolist():
            payload = source.read(item)
            if edit is not None:
                payload = edit(item.filename, payload)
            target.writestr(item.filename, payload)
        for name, payload in (extra or []):
            target.writestr(name, payload)
    return result.getvalue()


class PptxContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = fixture()

    def test_multi_slides_group_table_notes_media_and_exact_lines(self):
        result = parse_pptx(self.raw)
        self.assertEqual(result.format, "pptx")
        self.assertEqual(len(result.outline["slides"]), 2)
        for text in ["嵌套来源起点", "第二层内容", "会议明确的接口", "协议", "会议备注", "另一页"]:
            self.assertIn(text, result.content)
        nodes = result.graph["nodes"]
        self.assertEqual(len({n["node_id"] for n in nodes}), len(nodes))
        self.assertGreaterEqual(sum(n["kind"] == "group" for n in nodes), 2)
        self.assertEqual(sum(n["kind"] == "table_cell" for n in nodes), 4)
        self.assertTrue(any(n["kind"] == "notes_paragraph" for n in nodes))
        # Repeated Office IDs are page-local; their graph IDs must remain different.
        shapes = [n for n in nodes if n.get("shape_id") == "2"]
        self.assertEqual(len(shapes), 2)
        self.assertNotEqual(shapes[0]["node_id"], shapes[1]["node_id"])
        exact = next(n for n in nodes if n["text"] == "第二层内容")
        lines = result.content.splitlines()
        self.assertEqual(exact["line_anchor_scope"], "TEXT_MATCH")
        self.assertIn(exact["text"], "\n".join(lines[exact["line_start"]-1:exact["line_end"]]))
        self.assertTrue(exact["part_path"].startswith("ppt/slides/"))
        self.assertIn("/grpSp[1]/grpSp[1]/sp[1]", exact["node_path"])
        self.assertEqual({a["media_type"] for a in result.assets}, {"image/png", "image/jpeg"})
        self.assertEqual(len(result.outline["asset_sources"]), 2)
        with zipfile.ZipFile(BytesIO(self.raw)) as original:
            for asset in result.assets:
                self.assertEqual(asset["raw_bytes"], original.read(asset["member_path"]))
                self.assertEqual(asset["sha256"], hashlib.sha256(asset["raw_bytes"]).hexdigest())
                if asset["member_path"] in {s["asset_member_path"] for s in result.outline["asset_sources"]}:
                    self.assertEqual((asset["width"], asset["height"]), (3, 2))
                else:
                    self.assertEqual(asset["member_path"], "docProps/thumbnail.jpeg")
                    self.assertTrue(any(w == "RASTER_MEDIA_WITHOUT_PARSED_SLIDE_REFERENCE: " + asset["member_path"] for w in result.warnings))

    def test_over_70k_characters_keeps_real_tail_and_source_revision(self):
        raw = fixture(long=True)
        source = {"document_id": "doc-original", "revision": 7, "raw_sha256": hashlib.sha256(raw).hexdigest()}
        result = parse_pptx(raw, source_context=source)
        self.assertGreater(len(result.content), 70000)
        self.assertIn("资料真实末尾：不要漏掉本行", result.content)
        node = next(n for n in result.graph["nodes"] if n["text"].endswith("资料真实末尾：不要漏掉本行"))
        self.assertEqual(node["document_id"], "doc-original")
        self.assertEqual(node["revision"], 7)
        self.assertIn("资料真实末尾", result.content.splitlines()[node["line_end"]-1])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "actual-70k.pptx"; path.write_bytes(raw)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["raw_sha256"])

    def test_revision_mismatch_rejected_before_converter_side_effect(self):
        for binding in [None, {"document_id":"d","revision":True,"raw_sha256":hashlib.sha256(self.raw).hexdigest()},
                        {"document_id":"d","revision":1,"raw_sha256":"0"*64}]:
            if binding is None: continue
            with self.subTest(binding=binding), patch("packages.domain.pptx_import._converter", side_effect=AssertionError("must not import")):
                with self.assertRaisesRegex(PptxImportError, "revision/hash"):
                    parse_pptx(self.raw, source_context=binding)

    def test_actual_upstream_converter_is_called_without_callbacks(self):
        from packages.adapters.office_vendor.markitdown_v018.converters._pptx_converter import PptxConverter
        calls = []
        original = PptxConverter.convert
        def observe(converter, stream, info, **kwargs):
            calls.append((info.extension, kwargs)); return original(converter, stream, info, **kwargs)
        with patch.object(PptxConverter, "convert", observe):
            result = parse_pptx(self.raw)
        self.assertEqual(calls, [(".pptx", {})])
        self.assertEqual(result.outline["converter"]["commit"], UPSTREAM_COMMIT)

    def test_narrow_import_never_imports_dispatcher_or_heavy_packages(self):
        command = """import importlib.abc, sys, json
class Reject(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'markitdown','magika','torch','numpy','onnxruntime','openai'}:
   raise AssertionError('Forbidden import '+fullname)
sys.meta_path.insert(0, Reject())
from packages.domain.pptx_import import _converter
converter, info, sha = _converter()
print(json.dumps({'converter':type(converter).__name__,'forbidden_loaded':[x for x in sys.modules if x.split('.')[0] in {'markitdown','magika','torch','numpy','onnxruntime','openai'}]}))
"""
        run = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["forbidden_loaded"], [])

    def test_external_link_not_fetched_or_disclosed_and_no_caption(self):
        from packages.adapters.office_vendor.markitdown_v018.converters import _pptx_converter
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
             patch.object(_pptx_converter, "llm_caption", side_effect=AssertionError("caption forbidden")), \
             patch("subprocess.Popen", side_effect=AssertionError("Office process forbidden")):
            result = parse_pptx(fixture(external=True))
        self.assertTrue(any(w.startswith("EXTERNAL_RELATIONSHIP_NOT_FETCHED") for w in result.warnings))
        self.assertNotIn("never-expose", result.content + json.dumps(result.outline) + json.dumps(result.graph) + json.dumps(result.warnings))

    def test_chart_preserves_cached_original_values_and_warns_no_render(self):
        result = parse_pptx(fixture(chart=True))
        self.assertTrue(any(w.startswith("CHART_CACHED_DATA_ONLY") for w in result.warnings))
        nodes = [n for n in result.graph["nodes"] if n["kind"] == "chart_cached_value"]
        self.assertTrue({"甲", "乙", "2", "5"}.issubset({n["text"] for n in nodes}))
        self.assertTrue(all(n["part_path"].startswith("ppt/charts/") for n in nodes))
        self.assertIn("|", result.content)

    def test_unrendered_ole_timing_are_explicit_and_not_executed(self):
        def edit(name, raw):
            if name == "ppt/slides/slide1.xml":
                root = ET.fromstring(raw); ET.SubElement(root, "{http://schemas.openxmlformats.org/presentationml/2006/main}timing")
                return ET.tostring(root)
            return raw
        result = parse_pptx(rewrite(self.raw, edit=edit))
        self.assertTrue(any("EFFECT_NOT_EXECUTED: ppt/slides/slide1.xml" in w for w in result.warnings))

    def test_marker_collision_never_invents_exact_page_line_anchor(self):
        deck=Presentation(); slide=deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(0,0,1000,1000).text="<!-- Slide number: 1 -->\n真实输入"
        raw=BytesIO();deck.save(raw)
        result=parse_pptx(raw.getvalue())
        self.assertTrue(any(w.startswith("SLIDE_LINE_MAPPING_AMBIGUOUS") for w in result.warnings))
        self.assertTrue(all(n["line_anchor_scope"] == "DOCUMENT" for n in result.graph["nodes"]))

    def assert_preflight_rejects(self, raw, regex=None, **kwargs):
        with patch("packages.domain.pptx_import._converter", side_effect=AssertionError("unsafe input reached converter")):
            with self.assertRaisesRegex(PptxImportError, regex or ".+"):
                parse_pptx(raw, **kwargs)

    def test_archive_traversal_windows_alias_symlink_compression_and_dtd(self):
        attacks = [rewrite(self.raw, extra=[("../escape.txt", b"x")]),
                   rewrite(self.raw, extra=[("PPT/SLIDES/slide1.xml", b"<x/>")]),
                   rewrite(self.raw, extra=[("ppt/slides/CON.txt", b"x")]),
                   rewrite(self.raw, compression=zipfile.ZIP_BZIP2),
                   rewrite(self.raw, edit=lambda n,b: b'<!DOCTYPE x [<!ENTITY a "boom">]><x>&a;</x>' if n=="ppt/slides/slide1.xml" else b)]
        link=zipfile.ZipInfo("ppt/link");link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        attacks.append(rewrite(self.raw, extra=[(link,b"../../outside")]))
        for number,raw in enumerate(attacks):
            with self.subTest(attack=number):self.assert_preflight_rejects(raw)

    def test_ratio_depth_node_and_expansion_bounds(self):
        self.assert_preflight_rejects(rewrite(self.raw,extra=[("ppt/large.bin", b"x"*(2*1024*1024))]), "ratio")
        for bounds in [DiagramLimits(max_archive_bytes=1),DiagramLimits(max_member_bytes=10),
                       DiagramLimits(max_expanded_bytes=1024),DiagramLimits(max_nodes=2),DiagramLimits(max_depth=2),
                       DiagramLimits(max_content_bytes=100)]:
            with self.subTest(bounds=bounds):self.assert_preflight_rejects(self.raw, limits=bounds)

    def test_duplicate_shape_and_dangling_relationship_early_fail(self):
        def duplicate(name,raw):
            if name=="ppt/slides/slide1.xml":
                root=ET.fromstring(raw)
                ids=[i for i in root.iter() if i.tag.endswith('}cNvPr')]
                ids[-1].set('id',ids[1].get('id'));return ET.tostring(root)
            return raw
        self.assert_preflight_rejects(rewrite(self.raw,edit=duplicate), "duplicate source shape")
        self.assert_preflight_rejects(rewrite(self.raw,edit=lambda n,b:b.replace(b'../media/image1.png',b'../media/missing.png') if n=='ppt/slides/_rels/slide1.xml.rels' else b), "target part is missing")

    def test_invalid_image_fails_before_upstream(self):
        self.assert_preflight_rejects(rewrite(self.raw, edit=lambda n,b:b'\x89PNG\r\n\x1a\ninvalid' if n.endswith('.png') else b), "corrupt")

    def test_dimensions_fail_before_actual_decode_without_large_allocation(self):
        def oversized(name, raw):
            if not name.endswith('.png'): return raw
            header=struct.pack('>II',5000,5000)+raw[24:29]
            return raw[:16]+header+struct.pack('>I',zlib.crc32(b'IHDR'+header))+raw[33:]
        with patch('packages.domain.document_import.image_metadata',side_effect=AssertionError('decode must not start')):
            self.assert_preflight_rejects(rewrite(self.raw,edit=oversized),'pixel limit')

    def test_all_image_dimensions_counted_before_first_actual_decode(self):
        raw=image_bytes();header=struct.pack('>II',400,400)+raw[24:29]
        bounded_header=raw[:16]+header+struct.pack('>I',zlib.crc32(b'IHDR'+header))+raw[33:]
        deck=rewrite(self.raw,extra=[('ppt/media/pending1.png',bounded_header),('ppt/media/pending2.png',bounded_header)])
        with patch('packages.domain.document_import.image_metadata',side_effect=AssertionError('decode must not start')):
            self.assert_preflight_rejects(deck,'cumulative decoded',limits=DiagramLimits(max_expanded_bytes=1024*1024))

    def test_unsupported_media_warning_and_legacy_macro_rejection(self):
        self.assert_preflight_rejects(self.raw, filename="legacy.ppt")
        self.assert_preflight_rejects(rewrite(self.raw,extra=[("ppt/vbaProject.bin",b"inert")]),"Macro")
        self.assert_preflight_rejects(rewrite(self.raw,edit=lambda n,b:b.replace(b'presentationml.presentation.main+xml',b'presentation.macroEnabled.main+xml') if n=='[Content_Types].xml' else b),"Macro")
        raw=rewrite(self.raw,extra=[("ppt/media/unrendered.svg",b'<svg xmlns="http://www.w3.org/2000/svg"/>')],edit=lambda n,b:b.replace(b'</Types>',b'<Default Extension="svg" ContentType="image/svg+xml"/></Types>') if n=='[Content_Types].xml' else b)
        result=parse_pptx(raw)
        self.assertTrue(any(w=="UNSUPPORTED_IMAGE_NOT_RENDERED: ppt/media/unrendered.svg" for w in result.warnings))

    def test_original_upstream_files_and_version_guard(self):
        source=json.loads((VENDOR/'SOURCE.json').read_text())
        self.assertEqual(source['commit'],UPSTREAM_COMMIT)
        for item in source['files']:
            raw=(VENDOR/item['path']).read_bytes()
            self.assertEqual((len(raw),hashlib.sha256(raw).hexdigest()),(item['byte_length'],item['sha256']))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest=dict(source,commit='0'*40);(root/'SOURCE.json').write_text(json.dumps(manifest))
            with patch('packages.domain.pptx_import.VENDOR',root):
                with self.assertRaisesRegex(PptxImportError,"source version"):
                    parse_pptx(self.raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
