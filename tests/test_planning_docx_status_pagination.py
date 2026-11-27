"""Status groups stay readable without binding unrelated report content."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree
import pytest

from packages.domain.planning_docx import render_docx
from packages.domain.planning_schema import PlanningCandidate


WARNING = '计划或需求确认不代表工具权限、执行授权或独立验收已经通过。'


def _effective(paragraph, name):
    value = getattr(paragraph.paragraph_format, name)
    style = paragraph.style
    while value is None and style is not None:
        value = getattr(style.paragraph_format, name)
        style = style.base_style
    return value


def _check_status_groups(document):
    paragraphs = document.paragraphs
    starts = [i for i, p in enumerate(paragraphs) if p.text.startswith('本版状态：')]
    assert len(starts) >= 2, 'Both the reading body and trace appendix retain current state'
    for start in starts:
        end = next(i for i in range(start, len(paragraphs)) if paragraphs[i].text == WARNING)
        group = paragraphs[start:end + 1]
        assert 2 <= len(group) <= 6
        for paragraph in group:
            assert _effective(paragraph, 'keep_together') is True, paragraph.text
        for paragraph in group[:-1]:
            assert _effective(paragraph, 'keep_with_next') is True, paragraph.text
        assert group[-1].paragraph_format.keep_with_next is False, 'The state chain ends locally'
        assert all(p.paragraph_format.page_break_before is not True for p in group)
        if start and paragraphs[start - 1].style.name.startswith('Heading'):
            assert _effective(paragraphs[start - 1], 'keep_with_next') is True
    appendix = next(i for i, p in enumerate(paragraphs) if p.text == '本版状态与确认范围')
    assert paragraphs[appendix + 1].text.startswith('本版状态：')
    assert _effective(paragraphs[appendix], 'keep_with_next') is True
    assert all(p.paragraph_format.keep_with_next is not True
               for p in paragraphs if p.text.startswith('本报告保留当前任务方案'))


def _proposal(status='DRAFT', review='CURRENT'):
    candidate = PlanningCandidate.model_validate({
        'title': '状态分页核对', 'summary': '完整原文、中文与人工补充必须保留。',
        'goals': ['保留正文'], 'constraints': [], 'risks': [], 'assumptions': [],
        'nodes': [{'id': 'T01', 'title': '核对结果', 'description': '正常阅读后继续工作。',
                   'inputs': ['原件'], 'outputs': ['新副本'], 'acceptance': ['内容完整'],
                   'origin': 'MODEL_SUGGESTION'}], 'questions': [],
    }).model_dump()
    reference = {'id': 'status_fixture', 'revision': 1, 'sha256': 'a' * 64}
    proposal = {**reference, 'payload': {'candidate': candidate, 'status': status,
                'source_receipt': {'documents': [], 'coverage': [], 'sha256': 'b' * 64}},
                'review_status': review, 'flow': {'nodes': [{'id': 'T01', 'planning_status': 'DRAFT'}]},
                'answers': []}
    return {'source_kind': 'PROPOSAL', 'proposal': proposal, 'project_id': 'status_fixture',
            'pending': {'proposal_ref': reference, 'questions': [], 'pending_count': 0,
                        'sha256': 'c' * 64}, 'pending_markdown': '没有登记问题。',
            'template_ref': None, 'experience': None, 'discovery': None, 'diagrams': []}


@pytest.mark.parametrize('with_template', [False, True], ids=['builtin-draft', 'template-confirmed-review'])
def test_status_chains_are_local_in_builtin_and_user_template_reports(with_template):
    source = _proposal('CONFIRMED', 'NEEDS_REVIEW') if with_template else _proposal()
    before = deepcopy(source)
    template = None
    if with_template:
        document = Document()
        document.add_heading('模板原题名', 0)
        document.add_paragraph('{{ title }}')
        document.add_paragraph('{{ summary }}')
        document.add_heading('使用指引、验收任务与待确认事项', 1)
        document.add_paragraph('{{ report_sections }}')
        document.add_paragraph('模板尾注保持原位。')
        out = BytesIO(); document.save(out); template = out.getvalue()
    raw, _ = render_docx(source, template)
    document = Document(BytesIO(raw))
    _check_status_groups(document)
    assert source == before
    if with_template:
        text = '\n'.join(p.text for p in document.paragraphs)
        assert '模板原题名' in text and '模板尾注保持原位。' in text


def _text_and_nonpagination_format(document):
    result = []
    for paragraph in document.element.body.iter(qn('w:p')):
        properties = deepcopy(paragraph.find(qn('w:pPr')))
        if properties is not None:
            for key in ('keepNext', 'keepLines', 'widowControl'):
                for element in properties.findall(qn('w:' + key)):
                    properties.remove(element)
            if not len(properties):
                properties = None
        result.append((tuple(t.text for t in paragraph.iter(qn('w:t'))),
                       etree.tostring(properties, method='c14n') if properties is not None else None,
                       tuple(etree.tostring(r, method='c14n') for r in paragraph.iter(qn('w:rPr')))))
    return result


def test_actual_same_b_template_keeps_state_groups_and_all_original_content(tmp_path):
    names = ('CIRVEN_WORD_STATUS_SNAPSHOT', 'CIRVEN_WORD_STATUS_TEMPLATE', 'CIRVEN_WORD_STATUS_BASELINE_DOCX')
    if not all(os.environ.get(name) for name in names):
        pytest.skip('Exact same-B snapshot, selected template and original DOCX not supplied')
    paths = [Path(os.environ[name]) for name in names]
    inputs = [path.read_bytes() for path in paths]
    expected = ('a523388f7f95a8f34da8d7d51156a9a08970bd026c85b9628d5937f535213fde',
                '2ae94f3f3e07d39d7f0a2b31ab84716069024b5c60faa04e3f7646b6277c1cc2',
                '416df4dbb46fd45385306ddc7ac80d6378ed2d33f97cb0d6c77a709c7b3bba23')
    assert tuple(hashlib.sha256(value).hexdigest() for value in inputs) == expected
    source = json.loads(inputs[0]); before = deepcopy(source)
    raw, audit = render_docx(source, inputs[1])
    document = Document(BytesIO(raw)); baseline = Document(BytesIO(inputs[2]))
    (tmp_path / 'same-b-status-chain.docx').write_bytes(raw)
    (tmp_path / 'RENDER_AUDIT.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    _check_status_groups(document)
    assert _text_and_nonpagination_format(document) == _text_and_nonpagination_format(baseline)
    assert source == before
    assert [path.read_bytes() for path in paths] == inputs
    assert len(document.inline_shapes) == len(baseline.inline_shapes) == 1
    assert len(document.tables) == len(baseline.tables)
