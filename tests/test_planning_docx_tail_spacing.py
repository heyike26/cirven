"""Generated trace spacing is local; the user's closing note stays unchanged."""
from copy import deepcopy
from io import BytesIO
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from lxml import etree
import pytest

from packages.domain.planning_docx import render_docx
from test_planning_docx_content import snapshot


def _trace(document):
    paragraphs = document.paragraphs
    start = next(i for i, p in enumerate(paragraphs) if p.text == '可追溯附录')
    end = next(i for i in range(start, len(paragraphs))
               if paragraphs[i].text.startswith('生成时说明、其他来源的历史题名'))
    return paragraphs[start:end + 1]


def _check_local_spacing(document):
    trace = _trace(document)
    for paragraph in trace[1:]:
        if not paragraph.text:
            continue
        fmt = paragraph.paragraph_format
        assert fmt.space_after is not None and fmt.space_after <= Pt(3), paragraph.text
        bound = Pt(8) if paragraph.style.name.startswith('Heading') else Pt(0)
        assert fmt.space_before is not None and fmt.space_before <= bound, paragraph.text
    assert trace[-1].paragraph_format.keep_with_next is False
    assert not any(p.paragraph_format.page_break_before is True for p in trace)


@pytest.mark.parametrize('use_template', [False, True], ids=['builtin', 'user-template'])
def test_only_generated_appendix_gets_compact_spacing(use_template):
    source = snapshot(); before = deepcopy(source)
    template = None; original = None
    if use_template:
        original = Document()
        original.styles['Normal'].paragraph_format.space_after = Pt(14)
        original.add_paragraph('模板起始原文。')
        original.add_paragraph('{{ report_sections }}')
        closing = original.add_paragraph('模板结束说明：原文与格式不得变。', style='Caption')
        closing.paragraph_format.space_before = Pt(9)
        closing.paragraph_format.space_after = Pt(11)
        closing.runs[0].bold = True
        out = BytesIO(); original.save(out); template = out.getvalue()
    raw, _ = render_docx(source, template)
    document = Document(BytesIO(raw)); _check_local_spacing(document)
    assert source == before
    if original is not None:
        assert etree.tostring(document.paragraphs[-1]._p) == etree.tostring(original.paragraphs[-1]._p)
        assert document.styles['Normal'].paragraph_format.space_after == Pt(14)
        first = next(p for p in document.paragraphs if p.text == '模板起始原文。')
        assert first.paragraph_format.space_after is None
    else:
        assert document.styles['Normal'].paragraph_format.space_after == Pt(6)


def _without_trace_spacing(document, *, without_drawings=False):
    result = deepcopy(document.element)
    paragraphs = list(result.body.findall(qn('w:p')))
    texts = [''.join(t.text or '' for t in p.iter(qn('w:t'))) for p in paragraphs]
    start = texts.index('可追溯附录')
    end = next(i for i in range(start, len(texts)) if texts[i].startswith('生成时说明、其他来源的历史题名'))
    for paragraph in paragraphs[start + 1:end + 1]:
        props = paragraph.find(qn('w:pPr'))
        spacing = props.find(qn('w:spacing')) if props is not None else None
        if spacing is not None:
            for name in ('before', 'after'):
                spacing.attrib.pop(qn('w:' + name), None)
            if not spacing.attrib:
                props.remove(spacing)
    if without_drawings:
        for drawing in list(result.iter(qn('w:drawing'))):
            drawing.getparent().remove(drawing)
    return etree.tostring(result, method='c14n')


def test_actual_r12_template_tail_keeps_all_text_fonts_and_complete_diagram(tmp_path):
    names = ('CIRVEN_WORD_TAIL_SNAPSHOT', 'CIRVEN_WORD_TAIL_TEMPLATE',
             'CIRVEN_WORD_TAIL_BASELINE_DOCX', 'CIRVEN_WORD_TAIL_BASELINE_EXPORTER')
    if not all(os.environ.get(name) for name in names):
        pytest.skip('Exact R12 source, selected template and native-reviewed original DOCX not supplied')
    paths = [Path(os.environ[name]) for name in names]
    inputs = [p.read_bytes() for p in paths]
    assert tuple(hashlib.sha256(raw).hexdigest() for raw in inputs) == (
        '74805b131fad15984cfa1a1c13313ad9ed54a0e58c8b097925d58819c304e6b5',
        '2ae94f3f3e07d39d7f0a2b31ab84716069024b5c60faa04e3f7646b6277c1cc2',
        '4595a4460a4436cdaf7402fb5ea93f4e0c39df88630ae2526d37e04ba9d1207b',
        'a0916cb2a7e9e98c35837646ddb018289a32bf1b956052f2ae53672973a73044')
    source = json.loads(inputs[0]); before = deepcopy(source)
    assert len(source['diagrams']) == 1
    graph = source['diagrams'][0]['graph']
    assert (len(graph['nodes']), len(graph['edges'])) == (8, 12)
    raw, audit = render_docx(source, inputs[1])
    document = Document(BytesIO(raw)); baseline = Document(BytesIO(inputs[2]))
    spec = importlib.util.spec_from_file_location('original_tail_exporter', paths[3])
    original_exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(original_exporter)
    old_raw, _ = original_exporter.render_docx(source, inputs[1])
    same_platform = Document(BytesIO(old_raw))
    _check_local_spacing(document)
    assert _without_trace_spacing(document) == _without_trace_spacing(same_platform)
    # Native and Linux font raster metrics differ by one pixel on this graph.
    # Keep the native prose/font/format proof separate from same-platform media.
    assert _without_trace_spacing(document, without_drawings=True) == _without_trace_spacing(baseline, without_drawings=True)
    assert len(document.inline_shapes) == len(baseline.inline_shapes) == 1
    with ZipFile(BytesIO(inputs[2])) as old, ZipFile(BytesIO(raw)) as new:
        for name in old.namelist():
            if name.startswith(('word/header', 'word/footer')) or name == 'word/styles.xml':
                assert new.read(name) == old.read(name), name
    with ZipFile(BytesIO(old_raw)) as old, ZipFile(BytesIO(raw)) as new:
        for name in old.namelist():
            if name.startswith('word/media/'):
                assert new.read(name) == old.read(name), name
    assert document.paragraphs[-1].text == baseline.paragraphs[-1].text
    assert source == before and [p.read_bytes() for p in paths] == inputs
    (tmp_path / 'same-r12-tail-spacing.docx').write_bytes(raw)
    (tmp_path / 'RENDER_AUDIT.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
