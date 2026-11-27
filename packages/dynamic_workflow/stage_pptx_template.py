"""Fill the reviewed DrawingML text of an existing, preflighted PowerPoint package."""
from copy import deepcopy
import re

from lxml import etree as ET

from packages.domain.diagram_import import DiagramLimits, _Archive
from packages.domain.document_import import MAX_DOCUMENT_BYTES
from packages.domain.pptx_import import DRAWING_NS, PRESENTATION_NS, _preflight
from packages.dynamic_workflow.stage_template_export import (
    SLOT, Target, UnsupportedPresentationLayout, _xml, replace_markers,
)

A = '{' + DRAWING_NS + '}'
P = '{' + PRESENTATION_NS + '}'
TEXT_PLACEHOLDERS = {'title': '标题', 'ctrTitle': '主标题', 'subTitle': '副标题',
                     'body': '正文', 'obj': '内容'}
INVALID_XML = re.compile(r'[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')


def value_issue(value):
    if not isinstance(value, str) or INVALID_XML.search(value):
        return 'PowerPoint 字段含有不能保存的 XML 字符'
    if value.replace('\r\n', '\n').replace('\r', '\n').count('\n') > 1024:
        return '单个 PowerPoint 字段的换行超过 1024 行'
    return None


def _write_run(run, value, budget):
    node = run.find(A + 't')
    lines = value.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    properties = run.find(A + 'rPr')
    if len(lines) > 1:
        # Every continuation run must retain the author's formatting. Bound cloning
        # even when a small text field has an unusually large style/extension tree.
        style_size = len(ET.tostring(properties)) if properties is not None else 0
        budget[0] -= (len(lines) - 1) * (style_size + 160)
        if budget[0] < 0:
            raise ValueError('PowerPoint 字段展开后超过文稿大小限制')
    node.text = lines[0]
    tail = run
    for line in lines[1:]:
        line_break = ET.Element(A + 'br')
        tail.addnext(line_break)
        continuation = ET.Element(A + 'r')
        if properties is not None:
            continuation.append(deepcopy(properties))
        ET.SubElement(continuation, A + 't').text = line
        line_break.addnext(continuation)
        tail = continuation


def _editable_shapes(tree):
    for shape in tree:
        if shape.tag == P + 'grpSp':
            yield from _editable_shapes(shape)
        elif shape.tag in (P + 'sp', P + 'graphicFrame'):
            yield shape


def _paragraph_slots(p, prefix, part, budget, blank_label=None):
    # Fields, authored soft breaks and unknown objects separate ordinary text runs.
    # A marker crossing such a boundary cannot be replaced without losing content.
    groups, runs = [], []
    for child in p:
        if child.tag == A + 'r' and len(child.findall(A + 't')) == 1:
            runs.append(child)
        else:
            if runs:
                groups.append(runs)
                runs = []
    if runs:
        groups.append(runs)
    whole = ''.join(t.text or '' for t in p.iter(A + 't'))
    markers = list(SLOT.finditer(whole))
    entries = []
    for runs in groups:
        strings = [r.find(A + 't').text or '' for r in runs]
        matches = list(SLOT.finditer(''.join(strings)))
        if matches:
            entries.append((runs, strings, matches))
    if len(markers) != sum(len(matches) for _, _, matches in entries):
        raise UnsupportedPresentationLayout('部分幻灯片字段跨越了换行、自动域或其他对象，请将同一占位符放在同一段连续文字内')
    if markers:
        def fill(rows):
            values = iter(rows[0])
            for runs, original, matches in entries:
                strings = original.copy()
                replace_markers(strings, matches, [next(values) for _ in matches])
                for run, old, new in zip(runs, original, strings):
                    if new != old:
                        _write_run(run, new, budget)
        return Target(prefix, [m.group(1) for m in markers], [whole], False, True, fill, part_name=part)
    if blank_label and not whole.strip() and all(c.tag in (A + 'pPr', A + 'endParaRPr', A + 'r') for c in p):
        def fill_blank(rows):
            run = p.find(A + 'r')
            if run is None:
                run = ET.Element(A + 'r')
                end = p.find(A + 'endParaRPr')
                if end is None:
                    p.append(run)
                else:
                    run_properties = deepcopy(end)
                    run_properties.tag = A + 'rPr'
                    run.append(run_properties)
                    end.addprevious(run)
            if run.find(A + 't') is None:
                ET.SubElement(run, A + 't')
            _write_run(run, rows[0][0], budget)
        return Target(prefix, [blank_label], [''], False, False, fill_blank, part_name=part)
    return None


def presentation_slots(raw, archive):
    # Resolve slides through their actual OPC relationships and logical order,
    # including decks whose slide part names differ from slide1.xml, slide2.xml, …
    checked = _Archive(raw, DiagramLimits())
    _, slides, _, _, _ = _preflight(checked)
    budget = [MAX_DOCUMENT_BYTES]
    slots, parts = [], {}
    for slide in slides:
        part = slide['part_path']
        root = _xml(archive, part)
        tree = root.find(P + 'cSld/' + P + 'spTree')
        if tree is None:
            continue
        recognized = set()
        for index, shape in enumerate(_editable_shapes(tree), 1):
            prefix = f"slide:{slide['index']}:shape:{index}"
            bodies = []
            if shape.tag == P + 'sp':
                placeholder = shape.find(P + 'nvSpPr/' + P + 'nvPr/' + P + 'ph')
                label = (TEXT_PLACEHOLDERS.get(placeholder.get('type', 'obj'))
                         if placeholder is not None else None)
                bodies.append((shape.find(P + 'txBody'), prefix, label))
            else:
                table = shape.find(A + 'graphic/' + A + 'graphicData/' + A + 'tbl')
                if table is not None:
                    rows = table.findall(A + 'tr')
                    headers = []
                    if rows:
                        for cell in rows[0].findall(A + 'tc'):
                            text = ''.join(t.text or '' for t in cell.iter(A + 't')).strip()
                            merged = any(cell.get(k) for k in ('gridSpan', 'rowSpan', 'hMerge', 'vMerge'))
                            headers.append(text[:120] if text and not SLOT.search(text) and not merged else None)
                    for rowno, row in enumerate(rows, 1):
                        for colno, cell in enumerate(row.findall(A + 'tc'), 1):
                            if any(cell.get(k) in ('1', 'true') for k in ('hMerge', 'vMerge')):
                                continue
                            body_id = f'{prefix}:row:{rowno}:cell:{colno}'
                            label = headers[colno - 1] if rowno > 1 and colno <= len(headers) else None
                            bodies.append((cell.find(A + 'txBody'), body_id, label))
            for body, body_id, label in bodies:
                if body is None:
                    continue
                paragraphs = body.findall(A + 'p')
                for number, paragraph in enumerate(paragraphs, 1):
                    recognized.add(paragraph)
                    slot = _paragraph_slots(paragraph, f'{body_id}:paragraph:{number}', part, budget,
                                            label if len(paragraphs) == 1 else None)
                    if slot:
                        slots.append(slot)
                        parts[part] = root
        for p in tree.iter(A + 'p'):
            if p not in recognized and SLOT.search(''.join(t.text or '' for t in p.iter(A + 't'))):
                raise UnsupportedPresentationLayout('幻灯片的非常规对象中含有无法原位填写的占位符，请将字段放入文本框或表格单元格')
    return slots, parts
