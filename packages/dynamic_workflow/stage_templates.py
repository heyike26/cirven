"""Extract an explicitly selected template into inert, source-bound context."""
from __future__ import annotations

import csv
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import PurePath
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from packages.domain.diagram_import import DiagramLimits, _Archive, _resolve
from packages.domain.document_import import MAX_DOCUMENT_BYTES, MAX_TEXT_BYTES, SUPPORTED_SUFFIXES, parse_document

_MAX_TEMPLATE_CHARS = 180_000
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _sheet_text(element):
    # Phonetic guides (rPh) are pronunciation aids, not visible cell text.
    runs = [r.find(f"{{{_SS}}}t") for r in element.findall(f"{{{_SS}}}r")]
    direct = element.find(f"{{{_SS}}}t")
    return "".join(t.text or "" for t in ([direct] if direct is not None else runs) if t is not None)


def _spreadsheet(raw):
    archive = _Archive(raw, DiagramLimits(), xml_node_limit=65_536)
    workbook = archive.xml.get("xl/workbook.xml")
    relations = archive.xml.get("xl/_rels/workbook.xml.rels")
    if workbook is None or relations is None:
        raise ValueError("无法识别 Excel 工作簿")
    paths = {r.get("Id"): _resolve("xl/workbook.xml", r.get("Target", "")) for r in relations
             if r.get("Type", "").endswith("/worksheet") and r.get("TargetMode") != "External"}
    shared = archive.xml.get("xl/sharedStrings.xml")
    strings = [_sheet_text(si) for si in shared] if shared is not None else []
    sheets = []
    for sheet in workbook.iter(f"{{{_SS}}}sheet"):
        relation = sheet.get(f"{{{_REL}}}id")
        page = archive.xml.get(paths.get(relation))
        if page is None:
            raise ValueError("Excel 工作表关系无法解析")
        cells = []
        for cell in page.iter(f"{{{_SS}}}c"):
            address = cell.get("r")
            value = cell.findtext(f"{{{_SS}}}v", default="")
            kind = cell.get("t")
            if kind == "s":
                if not re.fullmatch(r"[0-9]{1,7}", value) or int(value) >= len(strings):
                    raise ValueError("Excel 共享字符串索引错误")
                value = strings[int(value)]
            elif kind == "inlineStr":
                inline = cell.find(f"{{{_SS}}}is")
                value = _sheet_text(inline) if inline is not None else ""
            formula = cell.findtext(f"{{{_SS}}}f")
            if value or formula:
                cells.append({"address": address, "value": value, **({"formula": formula} if formula else {}),
                              **({"style": cell.get("s")} if cell.get("s") else {})})
        merges = [m.get("ref") for m in page.iter(f"{{{_SS}}}mergeCell")]
        sheets.append({"name": sheet.get("name"), "cells": cells, "merged_cells": merges})
    if not any(s["cells"] for s in sheets):
        raise ValueError("Excel 模板没有可读取的栏目或示例")
    return json.dumps(sheets, ensure_ascii=False, separators=(",", ":"))



def _literal_label(s):
    return bool(s.strip()) and not any(mark in s for mark in ("{{", "${", "[[", "<", "…"))


def _markdown_headings(text):
    headings, fence = [], ""
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if not fence:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = ""
            continue
        match = _HEADING.match(line)
        if match and not fence:
            title = re.sub(r"\s+#+$", "", match.group(2)).strip()
            headings.append((len(match.group(1)), title))
    return headings


def _json_layout(text):
    def object_fields(pairs):
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError("JSON 模板及正文不能含重复字段")
        return fields

    def reject_constant(_):
        raise ValueError("JSON 模板及正文需要使用标准 JSON 值")

    def layout(value, depth):
        if depth > 64:
            raise ValueError("JSON 层级超出模板限制")
        if isinstance(value, dict):
            return ("object", tuple((k, layout(v, depth + 1)) for k, v in value.items()))
        if isinstance(value, list):
            elements = tuple(layout(v, depth + 1) for v in value)
            if elements and all(v == elements[0] for v in elements):
                elements = elements[:1]
            return ("array", elements)
        return type(value).__name__, ()

    try:
        # Numbers are checked for valid JSON syntax; their magnitude is irrelevant to layout.
        value = json.loads(text, object_pairs_hook=object_fields, parse_constant=reject_constant,
                           parse_int=lambda _: 0, parse_float=lambda _: 0)
        return layout(value, 0)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("模板或已填写正文不是可解析的 JSON") from exc


def _fits_json_layout(sample, filled):
    kind, expected = sample
    actual_kind, actual = filled
    if kind != actual_kind:
        return False
    if kind == "object":
        return (len(expected) == len(actual) and
                all(k == key and _fits_json_layout(v, item)
                    for (k, v), (key, item) in zip(expected, actual)))
    if kind == "array":
        if not expected:
            return True
        if len(expected) == 1:
            return bool(actual) and all(_fits_json_layout(expected[0], v) for v in actual)
        return len(expected) == len(actual) and all(_fits_json_layout(a, b) for a, b in zip(expected, actual))
    return True


def _format_rules(name, text, document_bytes):
    if name == ".json":
        _json_layout(text)
        return {"json_layout": True}
    if name in {".md", ".markdown"}:
        return {"headings": [[level, title] for level, title in _markdown_headings(text) if _literal_label(title)]}
    if name in {".csv", ".tsv"}:
        delimiter = "," if name == ".csv" else "\t"
        header = next(csv.reader(StringIO(text), delimiter=delimiter), [])
        if len(header) > 1 and all(_literal_label(c) for c in header):
            return {"columns": header, "delimiter": delimiter}
    if name in {".docx", ".dotx"}:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        labels = []
        for block in Document(BytesIO(document_bytes)).iter_inner_content():
            if isinstance(block, Paragraph):
                if re.fullmatch(r"Heading [1-9]", block.style.name, re.I) and _literal_label(block.text):
                    labels.append(block.text.strip())
            elif isinstance(block, Table) and block.rows:
                first = block.rows[0]
                if first._tr.xpath("./w:trPr/w:tblHeader"):
                    seen = set()
                    for cell in first.cells:
                        if cell._tc not in seen and _literal_label(cell.text):
                            labels.append(cell.text.strip())
                        seen.add(cell._tc)
        return {"labels": labels}
    return {}


def template_issues(template, output, edits=None):
    """Check only structure directly identifiable in the user's file."""
    edits = edits or []
    targets = template.get("edit_targets", []) if isinstance(template,dict) else []
    if not targets and edits:
        return ["所选模板没有这些可写位置"]
    if targets:
        by_id = {target["target"]:target for target in targets}
        if not edits:
            return ["请填写模板指定位置，以便生成保留原版式的成果物"]
        seen = set()
        presentation_bytes = 0
        for edit in edits:
            ident = edit["target"]
            target = by_id.get(ident)
            if not target or len(edit["values"]) != len(target["columns"]):
                return ["模板填写位置或列数与原件不一致"]
            if ident in seen and not target["repeatable"]:
                return ["此模板位置仅可填写一次"]
            if template.get('export_extension') == '.pdf':
                from packages.dynamic_workflow.stage_pdf_template import value_issue
                issue = value_issue(target, edit["values"][0])
                if issue:
                    return [issue]
            if template.get('export_extension') == '.pptx':
                from packages.dynamic_workflow.stage_pptx_template import value_issue
                from packages.dynamic_workflow.stage_template_export import MAX_FILLED
                for value in edit['values']:
                    issue = value_issue(value)
                    if issue:
                        return [issue]
                    presentation_bytes += len(value.encode('utf-8'))
                if presentation_bytes > MAX_FILLED:
                    return ['PowerPoint 模板填写内容超过大小限制']
            seen.add(ident)
        if any(t.get("required") and t["target"] not in seen for t in targets):
            return ["模板中仍有必填位置未填写"]
    if not template:
        return []
    if not isinstance(output, str) or not output.strip():
        return ["请按已提供模板生成完整正文"]
    rules = template.get("format_rules", {}) if isinstance(template, dict) else {}
    if rules.get("json_layout"):
        try:
            if not _fits_json_layout(_json_layout(template["text"]), _json_layout(output)):
                return ["JSON 正文需保留原模板的字段、顺序、层级及值类型"]
        except ValueError as exc:
            return [str(exc)]
    if rules.get("columns"):
        try:
            records = csv.reader(StringIO(output), delimiter=rules["delimiter"], strict=True)
            if next(records, []) != rules["columns"] or any(len(r) != len(rules["columns"]) for r in records if r):
                return ["模板正文的列名、列序或数据列数与所选模板不一致"]
        except csv.Error:
            return ["模板正文不是有效的分隔表格"]
    if rules.get("headings"):
        heading_iter = iter(_markdown_headings(output))
        if any(not any(current == tuple(required) for current in heading_iter) for required in rules["headings"]):
            return ["模板正文缺少原有章节，或标题层级、顺序有变化"]
    cursor = 0
    for label in rules.get("labels", []):
        at = output.find(label, cursor)
        if at < 0:
            return ["模板正文未按原顺序覆盖已标记的标题或表头"]
        cursor = at + len(label)
    return []


def parse_stage_template(filename: str, raw: bytes) -> dict:
    """Authorisation of the selected bytes belongs to the API before this call."""
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 255
            or any(c in filename for c in "/\\:\x00") or filename in {".", ".."}):
        raise ValueError("模板需要使用文件名")
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("模板为空或超过文档导入限制")
    suffix = PurePath(filename).suffix.lower()
    notices = []
    source = raw
    try:
        if suffix in {".xlsx", ".xltx"}:
            fmt, text = "spreadsheet", _spreadsheet(raw)
        elif suffix == ".dotx" or suffix in SUPPORTED_SUFFIXES - {".md", ".txt"}:
            if suffix in {".png", ".jpg", ".jpeg"}:
                raise ValueError("图片模板需要可核查的文字识别结果或提供可提取正文的文件")
            source, name = raw, filename
            if suffix == ".dotx":
                from packages.domain.planning_docx import normalize_template
                from packages.domain.store import InvalidInput
                try:
                    source, receipt = normalize_template(raw, allow_reference_hyperlinks=True)
                except InvalidInput as exc:
                    raise ValueError(str(exc)) from exc
                if receipt["source_type"] != "DOTX":
                    raise ValueError("Word 模板扩展名与内容不同")
                name = filename[:-5] + ".docx"
            parsed = parse_document(name, source)
            if parsed.format == "pdf" and not all(p["text_extracted"] for p in parsed.outline["pages"]):
                from packages.dynamic_workflow.stage_pdf_template import inspect_pdf
                targets = inspect_pdf(raw)["targets"]
                mapped_pages = {number for target in targets for number in target["pages"]}
                if any(not page["text_extracted"] and page["page_number"] not in mapped_pages
                       for page in parsed.outline["pages"]):
                    raise ValueError("PDF 模板包含未提取文字或表单字段的页面，请提供可检索文字的版本")
                parsed.content += "\n" + "\n".join(target["columns"][0] for target in targets)
            text, fmt, notices = parsed.content, parsed.format, list(parsed.warnings)
        else:
            if len(raw) > MAX_TEXT_BYTES:
                raise ValueError("文本模板超过导入大小限制")
            encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = raw.decode(encoding)
            if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
                raise ValueError("模板含二进制数据，请提供可解析的文件")
            fmt = suffix.lstrip(".") or "text"
    except (BadZipFile, ParseError, UnicodeError) as exc:
        raise ValueError("无法提取模板，请检查文件格式与完整性") from exc
    if not text.strip() or len(text) > _MAX_TEMPLATE_CHARS:
        raise ValueError("模板没有可读内容或超过单节点上下文的字符限制")
    return {"name": filename, "format": fmt, "sha256": hashlib.sha256(raw).hexdigest(),
            "text": text, "warnings": notices, "format_rules": _format_rules(suffix, text, source)}
