"""Original editable-deck and template fixtures; no Office, GUI, model or network."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches, Pt

from packages.domain.pptx_export import (
    BACKEND_VERSION, ReportDeck, ReportSection, PptxExportError, export_report_deck,
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ref(**changes):
    # The original bytes are retained in the test receipt; no imaginary public project.
    value = {"kind": "document", "project_id": "original-project", "document_id": "minutes",
             "revision": 2, "sha256": sha("原创会议纪要\n已确认接口与验收，不含凭据。".encode()),
             "line_start": 1, "line_end": 2}
    value.update(changes)
    return value


def report():
    artifact = {"kind": "artifact", "project_id": "original-project", "artifact_id": "test-receipt",
                "revision": 1, "sha256": sha(b'original controlled test receipt; not a company run')}
    return {"schema_version": "eeas.report-deck.v1", "title": "原创项目：结果归档",
            "subtitle": "可编辑文字与真实来源边界", "source_refs": [ref()],
            "sections": [{"title": "接口与验证", "paragraphs": ["参数按修订二冻结。", "失败也需保留。"],
                          "bullets": ["没有调用模型", "尚未执行 Windows/Office"],
                          "source_refs": [artifact]}]}


def approved(value):
    return value.get("source_refs", []) + [item for section in value["sections"] for item in section.get("source_refs", [])]


def members(raw):
    with zipfile.ZipFile(BytesIO(raw)) as source:
        return {item.filename: source.read(item) for item in source.infolist()}


def save(deck):
    buffer = BytesIO(); deck.save(buffer)
    return buffer.getvalue()


def rewrite(raw, *, extra=(), edit=None):
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as original, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for item in original.infolist():
            blob = original.read(item)
            target.writestr(item, edit(item.filename, blob) if edit else blob)
        for name, blob in extra:
            target.writestr(name, blob)
    return output.getvalue()


def template(*, notes=True, missing=False, outside=False, split=False, external=False):
    deck = Presentation()
    deck.slide_width = Inches(12); deck.slide_height = Inches(7)
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    title = slide.shapes.add_textbox(Inches(0.5), Inches(0.4), Inches(10), Inches(1))
    paragraph = title.text_frame.paragraphs[0]
    run = paragraph.add_run(); run.text = "保留前缀：{{EEAS_TITLE}}：保留后缀"
    run.font.name = "Arial"; run.font.size = Pt(27); run.font.bold = True
    run.font.color.rgb = RGBColor(10, 35, 65)
    if split:
        run.text = "{{EEAS_"; paragraph.add_run().text = "TITLE}}"
    title.text_frame.add_paragraph().text = "不应被替换的原模板第二段。"
    subtitle = slide.shapes.add_textbox(Inches(.5), Inches(1.5), Inches(10), Inches(.6))
    subtitle.text = "{{EEAS_SUBTITLE}}"
    heading = slide.shapes.add_textbox(Inches(.5), Inches(2.5), Inches(10), Inches(.6))
    heading.text = "{{EEAS_SECTION_1_TITLE}}"
    body = slide.shapes.add_textbox(Inches(11 if outside else .5), Inches(3.5), Inches(10), Inches(2))
    body.text = "没有报告字段" if missing else "{{EEAS_SECTION_1_BODY}}"
    body.text_frame.paragraphs[0].runs[0].font.italic = True
    shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(10.6), Inches(.5), Inches(1), Inches(1))
    shape.name = "原装饰形状"; shape.fill.solid(); shape.fill.fore_color.rgb = RGBColor(120, 30, 40)
    image = BytesIO(); Image.new("RGB", (4, 3), (100, 150, 220)).save(image, format="PNG")
    slide.shapes.add_picture(BytesIO(image.getvalue()), Inches(10.6), Inches(2), width=Inches(1))
    if notes:
        note = slide.notes_slide.notes_text_frame
        note.text = "用户原始备注必须保留。"
        note.paragraphs[0].runs[0].font.bold = True
    if external:
        paragraph.runs[0].hyperlink.address = "https://invalid.example/never-fetch?token=fixture-not-secret"
    raw = save(deck)
    def add_type(name, blob):
        if name == "[Content_Types].xml":
            root = ET.fromstring(blob)
            ET.SubElement(root, "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                          PartName="/custom/original.xml", ContentType="application/xml")
            return ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return blob
    return rewrite(raw, edit=add_type, extra=[("custom/original.xml", b'<original><detail>inert untouched custom part</detail></original>')])


class DeckExportContract(unittest.TestCase):
    def setUp(self):
        self.value = report()
        self.record("original-minutes.txt", "原创会议纪要\n已确认接口与验收，不含凭据。".encode())
        self.record("original-test-receipt.txt", b'original controlled test receipt; not a company run')

    def record(self, name, raw, *, value=None, result=None):
        target = os.environ.get("EEAS_EVIDENCE_DIR")
        if not target:
            return
        folder = Path(target) / "fixtures" / self._testMethodName
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        with path.open("xb") as stream: stream.write(raw)
        entry = {"path": path.relative_to(Path(target)).as_posix(), "sha256": sha(raw),
                 "byte_length": len(raw), "source_kind": "synthetic"}
        if value is not None:
            entry["report"] = value
        if result is not None:
            entry["source_manifest"] = result.source_manifest; entry["audit"] = result.audit
        with (folder / "index.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def export(self, *, value=None, raw=None):
        value = value or self.value
        if raw is not None:
            self.record("original-template.pptx", raw)
        self.record("input.json", json.dumps(value, ensure_ascii=False).encode(), value=value)
        result = export_report_deck(value, project_id="original-project", validated_source_refs=approved(value), template_bytes=raw)
        self.record("output.pptx", result.raw_bytes, result=result)
        return result

    def test_report_generates_editable_text_and_source_notes_with_exact_hash(self):
        original = deepcopy(self.value)
        result = self.export()
        deck = Presentation(BytesIO(result.raw_bytes))
        self.assertEqual(self.value, original)
        self.assertEqual(len(deck.slides), 2)
        self.assertEqual(deck.slides[0].shapes.title.text, self.value["title"])
        body = deck.slides[1].placeholders[1].text
        self.assertIn("参数按修订二冻结。", body)
        self.assertIn("• 尚未执行 Windows/Office", body)
        deck.slides[1].shapes.title.text = "重新编辑"
        self.assertEqual(Presentation(BytesIO(save(deck))).slides[1].shapes.title.text, "重新编辑")
        self.assertEqual(result.sha256, sha(result.raw_bytes))
        canonical = json.dumps(self.value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self.assertEqual(result.source_manifest["input_sha256"], sha(canonical))
        self.assertIsNone(result.source_manifest["template_sha256"])
        self.assertEqual(result.source_manifest["backend"]["version"], BACKEND_VERSION)
        self.assertEqual(result.audit["native_office"], "NOT_RUN")
        for slide in Presentation(BytesIO(result.raw_bytes)).slides:
            note = slide.notes_slide.notes_text_frame.text
            self.assertIn(ref()["sha256"], note)
            self.assertIn('"revision":2', note)
            self.assertIn('"artifact_id":"test-receipt"', note)
            self.assertIn("CALLER_VALIDATED_SET_EXACT_MATCH_ONLY", note)
            self.assertIn("DECK_REFS_NOT_AUTHENTICATED_SLIDE_QUOTES", note)
        xml = members(result.raw_bytes)["ppt/slides/slide2.xml"]
        self.assertIn("失败也需保留。".encode(), xml)

    def test_actual_template_keeps_ids_styles_layout_parts_unknown_content_and_notes(self):
        raw = template()
        before = Presentation(BytesIO(raw))
        slide = before.slides[0]
        ids = [shape.shape_id for shape in slide.shapes]
        title_properties = etree.tostring(slide.shapes[0].text_frame.paragraphs[0].runs[0]._r.rPr, method="c14n")
        decoration = etree.tostring(slide.shapes[4].element, method="c14n")
        old_note = etree.tostring(slide.notes_slide.notes_text_frame.paragraphs[0]._p, method="c14n")
        template_hash = sha(raw)
        result = self.export(raw=raw)
        after = Presentation(BytesIO(result.raw_bytes)); out = after.slides[0]
        self.assertEqual(sha(raw), template_hash)
        self.assertEqual(result.source_manifest["template_sha256"], template_hash)
        self.assertEqual(out.slide_id, slide.slide_id)
        self.assertEqual([shape.shape_id for shape in out.shapes], ids)
        self.assertEqual(out.slide_layout.part.partname, slide.slide_layout.part.partname)
        self.assertEqual((after.slide_width, after.slide_height), (before.slide_width, before.slide_height))
        self.assertEqual(etree.tostring(out.shapes[0].text_frame.paragraphs[0].runs[0]._r.rPr, method="c14n"), title_properties)
        self.assertEqual(etree.tostring(out.shapes[4].element, method="c14n"), decoration)
        self.assertEqual(etree.tostring(out.notes_slide.notes_text_frame.paragraphs[0]._p, method="c14n"), old_note)
        self.assertEqual(out.shapes[0].text_frame.paragraphs[1].text, "不应被替换的原模板第二段。")
        self.assertIn("保留前缀：原创项目：结果归档：保留后缀", out.shapes[0].text)
        self.assertIn("• 没有调用模型", out.shapes[3].text)
        self.assertIn("用户原始备注必须保留。", out.notes_slide.notes_text_frame.text)
        self.assertIn("EEAS export provenance", out.notes_slide.notes_text_frame.text)
        before_parts, after_parts = members(raw), members(result.raw_bytes)
        self.assertTrue(before_parts.keys() <= after_parts.keys())
        allowed = {"ppt/slides/slide1.xml", "ppt/notesSlides/notesSlide1.xml"}
        self.assertEqual({name for name in before_parts if before_parts[name] != after_parts[name]}, allowed)
        for name in before_parts.keys() - allowed:
            self.assertEqual(after_parts[name], before_parts[name], name)
        self.assertTrue(any(item["member_path"] == "custom/original.xml" for item in result.audit["preserved_parts"]))
        # Explicit DrawingML breaks keep template run formatting on all body segments.
        self.assertIn(b"<a:br", after_parts["ppt/slides/slide1.xml"])
        self.assertTrue(all(run.font.italic for run in out.shapes[3].text_frame.paragraphs[0].runs))

    def test_template_without_notes_adds_only_provenance_parts_and_keeps_original_parts(self):
        raw = template(notes=False)
        result = self.export(raw=raw)
        old, new = members(raw), members(result.raw_bytes)
        self.assertEqual(new["custom/original.xml"], old["custom/original.xml"])
        self.assertEqual(new["ppt/slideLayouts/slideLayout7.xml"], old["ppt/slideLayouts/slideLayout7.xml"])
        self.assertTrue(any(item["member_path"].startswith("ppt/notesMasters/") for item in result.audit["added_parts"]))
        self.assertIn("EEAS export provenance", Presentation(BytesIO(result.raw_bytes)).slides[0].notes_slide.notes_text_frame.text)
        for name in {item["member_path"] for item in result.audit["preserved_parts"]}:
            self.assertEqual(old[name], new[name])

    def test_dataclass_payload_and_crlf_have_separate_input_and_normalized_hashes(self):
        section = ReportSection("标题", paragraphs=["第一行\r\n第二行"], source_refs=[ref()])
        value = ReportDeck("项目", [section])
        result = export_report_deck(value, project_id="original-project", validated_source_refs=[ref()])
        self.record("output.pptx", result.raw_bytes, result=result)
        self.assertNotEqual(result.source_manifest["input_sha256"], result.source_manifest["normalized_input_sha256"])
        # python-pptx's public shape.text uses VT for a DrawingML soft break.
        self.assertIn("第一行\n第二行", Presentation(BytesIO(result.raw_bytes)).slides[1].placeholders[1].text.replace("\v", "\n"))
        self.assertIn(b"<a:br", members(result.raw_bytes)["ppt/slides/slide2.xml"])
        self.assertEqual(section.paragraphs, ["第一行\r\n第二行"])

    def test_unvalidated_ref_revision_hash_kind_range_and_cross_project_fail_before_backend(self):
        changes = [{"revision": 3}, {"sha256": "a" * 64}, {"line_end": 3}, {"project_id": "another-project"},
                   {"revision": True}, {"sha256": "A" * 64}, {"line_start": 0}, {"endpoint": "must-not-pass"}]
        original = deepcopy(self.value)
        for change in changes:
            value = deepcopy(self.value); value["source_refs"] = [ref(**change)]
            with self.subTest(change=change), patch("pptx.Presentation", side_effect=AssertionError("backend must not run")) as backend:
                with self.assertRaises(PptxExportError):
                    export_report_deck(value, project_id="original-project", validated_source_refs=approved(original))
                backend.assert_not_called()
        self.assertEqual(self.value, original)

    def test_tables_charts_media_unknown_schema_and_unsourced_are_not_silently_exported(self):
        for field_name in ("table", "tables", "chart", "charts", "media", "images"):
            value = deepcopy(self.value); value["sections"][0][field_name] = []
            with self.subTest(field_name=field_name), self.assertRaisesRegex(PptxExportError, "unsupported fields"):
                export_report_deck(value, project_id="original-project", validated_source_refs=approved(self.value))
        for value in ({**self.value, "schema_version": "future"},
                      {"title": "无来源", "sections": [{"title": "章节", "paragraphs": ["内容"]}]}):
            with self.subTest(value=value), self.assertRaises(PptxExportError):
                export_report_deck(value, project_id="original-project", validated_source_refs=approved(self.value))

    def test_missing_outside_or_split_template_fields_reject_without_changing_input(self):
        for options, message in (({"missing": True}, "missing required"), ({"outside": True}, "outside"),
                                 ({"split": True}, "split across")):
            raw = template(**options); digest = sha(raw); value = deepcopy(self.value)
            self.record("rejected-" + next(iter(options)) + ".pptx", raw)
            with self.subTest(options=options), self.assertRaisesRegex(PptxExportError, message):
                export_report_deck(value, project_id="original-project", validated_source_refs=approved(value), template_bytes=raw)
            self.assertEqual(sha(raw), digest); self.assertEqual(value, self.value)

    def test_external_template_link_is_rejected_not_fetched(self):
        raw = template(external=True)
        self.record("rejected-external.pptx", raw)
        with patch("urllib.request.urlopen", side_effect=AssertionError("never network")) as network:
            with self.assertRaisesRegex(PptxExportError, "External template"):
                export_report_deck(self.value, project_id="original-project", validated_source_refs=approved(self.value), template_bytes=raw)
            network.assert_not_called()

    def test_unsafe_paths_windows_alias_symlink_dtd_macro_are_rejected_before_pptx_load(self):
        raw = template()
        symlink = zipfile.ZipInfo("custom/link"); symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [("traversal", rewrite(raw, extra=[("../outside.xml", b"<x/>")])),
                 ("alias", rewrite(raw, extra=[("PPT/SLIDES/slide1.xml", b"<x/>")])),
                 ("symlink", rewrite(raw, extra=[(symlink, b"elsewhere")])),
                 ("dtd", rewrite(raw, extra=[("custom/entity.xml", b'<!DOCTYPE x [<!ENTITY injected SYSTEM "file:///never">]><x/>')])),
                 ("macro", rewrite(raw, extra=[("ppt/vbaProject.bin", b"inert macro fixture")]))]
        for name, rejected in cases:
            self.record("rejected-" + name + ".pptx", rejected)
            with self.subTest(name=name), patch("pptx.Presentation", side_effect=AssertionError("preflight required")) as backend:
                with self.assertRaises(PptxExportError):
                    export_report_deck(self.value, project_id="original-project", validated_source_refs=approved(self.value), template_bytes=rejected)
                backend.assert_not_called()

    def test_text_count_byte_and_reserved_symbol_bounds_are_explicit(self):
        inputs = []
        for edits in ({"paragraphs": ["x"] * 25}, {"paragraphs": ["x" * 2001]},
                      {"paragraphs": ["x" * 2000] * 4}, {"paragraphs": ["{{EEAS_TITLE}}"]}):
            value = deepcopy(self.value); value["sections"][0].update(edits); inputs.append(value)
        inputs.append({**self.value, "title": "x" * (1024 * 1024)})
        for index, value in enumerate(inputs):
            with self.subTest(index=index), self.assertRaises(PptxExportError):
                export_report_deck(value, project_id="original-project", validated_source_refs=approved(self.value))

    def test_compression_bomb_and_non_deflate_methods_reject_before_backend(self):
        original = template()
        rejected = rewrite(original, extra=[("custom/repeated.bin", b"x" * (1024 * 1024 + 1))])
        cases = [("compression-ratio", rejected)]
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=method) as target:
                target.writestr("[Content_Types].xml", b"<Types/>")
            cases.append(("method-" + str(method), buffer.getvalue()))
        for name, raw in cases:
            self.record("rejected-" + name + ".pptx", raw)
            with self.subTest(name=name), patch("pptx.Presentation", side_effect=AssertionError("no unsafe load")) as backend:
                with self.assertRaises(PptxExportError):
                    export_report_deck(self.value, project_id="original-project", validated_source_refs=approved(self.value), template_bytes=raw)
                backend.assert_not_called()

    def test_backend_version_is_pinned_without_importing_other_office_dispatchers(self):
        with patch("packages.domain.pptx_export.version", return_value="future"), patch("pptx.Presentation") as backend:
            with self.assertRaisesRegex(PptxExportError, "fixed python-pptx 1.0.2"):
                export_report_deck(self.value, project_id="original-project", validated_source_refs=approved(self.value))
            backend.assert_not_called()

    def test_notes_placeholder_absence_cannot_destroy_old_notes(self):
        raw = template()
        def remove(name, blob):
            if name != "ppt/notesSlides/notesSlide1.xml": return blob
            root = ET.fromstring(blob)
            ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
            for shape in list(root.find("p:cSld/p:spTree", ns)):
                placeholder = shape.find("p:nvSpPr/p:nvPr/p:ph", ns)
                if placeholder is not None and placeholder.get("type") == "body":
                    root.find("p:cSld/p:spTree", ns).remove(shape)
            return ET.tostring(root, encoding="utf-8", xml_declaration=True)
        raw = rewrite(raw, edit=remove); digest = sha(raw)
        self.record("rejected-notes.pptx", raw)
        with self.assertRaisesRegex(PptxExportError, "no editable notes placeholder"):
            export_report_deck(self.value, project_id="original-project", validated_source_refs=approved(self.value), template_bytes=raw)
        self.assertEqual(sha(raw), digest)


if __name__ == "__main__":
    unittest.main()
