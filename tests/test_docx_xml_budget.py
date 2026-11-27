"""Independent OOXML, JSON and semantic budgets; no model or document writes."""
import hashlib
import io
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace

import pytest

from packages.domain.diagram_import import DiagramImportError, DiagramLimits, _Archive, _Result
from packages.domain.docx_import import DocxImportError, parse_docx
from test_diagram_import import archive
from test_docx_import import NS, paragraph, rels


def minimal_parts(paragraphs=1):
    return [('[Content_Types].xml', '<Types><Override ContentType="application/xml"/></Types>'),
            ('_rels/.rels', rels([('main', 'officeDocument', 'word/document.xml', '')])),
            ('word/document.xml', '<w:document '+NS+'><w:body>'
             + ''.join(paragraph('独立正文 '+str(i)) for i in range(paragraphs))
             + '</w:body></w:document>')]


def test_actual_22436_node_docx_keeps_original_title_and_source():
    source = os.environ.get('EEAS_DOCX_XML_SAMPLE')
    if not source:
        pytest.skip('Exact original DOCX is supplied from D; private document is not a repository fixture')
    raw = Path(source).read_bytes()
    assert len(raw) == 164523
    assert hashlib.sha256(raw).hexdigest() == '524c26c025bfdbdf13153a616d05774122b69b4ff11c3818e39f5680c406bec7'
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        nodes = sum(sum(1 for _event, _node in ET.iterparse(io.BytesIO(z.read(n)), events=('start',)))
                    for n in z.namelist() if n.endswith(('.xml', '.rels')))
    assert nodes == 22436
    filename = 'Cirven首版本地用户指引与验收任务书：待人审发现草稿-draft.docx'
    parsed = parse_docx(filename, raw)
    title = 'Cirven首版本地用户指引与验收任务书：待人审发现草稿'
    assert parsed.format == 'docx' and title in parsed.content
    originals = [n for n in parsed.graph['nodes'] if title in n['text']]
    assert originals and all(n['source']['part'] == 'word/document.xml' for n in originals)
    assert all(n['source']['anchor_kind'] == 'xml-local-path' for n in originals)
    assert len(parsed.graph['nodes']) < 20000


def test_style_xml_budget_is_cumulative_and_has_exact_boundary():
    parts = minimal_parts()
    base_nodes = sum(sum(1 for _ in ET.fromstring(data).iter()) for _name, data in parts)
    # Unused formatting is still fully validated, not omitted to pass a sample.
    styles = '<styles>'+'<s/>'*(65536-base_nodes-1)+'</styles>'
    raw = archive(parts+[('word/styles.xml', styles)])
    assert parse_docx('boundary.docx', raw).format == 'docx'
    with pytest.raises(DocxImportError, match='XML.*node limit'):
        parse_docx('over.docx', archive(parts+[('word/styles.xml', styles), ('word/unused.xml', '<extra/>')]))


def test_xml_budget_is_not_reset_per_style_member():
    styles = '<styles>'+'<s/>'*32768+'</styles>'
    with pytest.raises(DocxImportError, match='XML.*node limit'):
        parse_docx('two-styles.docx', archive(minimal_parts()+[
            ('word/styles.xml', styles), ('word/stylesWithEffects.xml', styles)]))


def test_separate_xml_budget_cannot_expand_json_budget():
    limits = replace(DiagramLimits(), max_nodes=5)
    # Six XML nodes fit the independent XML budget; JSON has its own five-unit cap.
    parsed = _Archive(archive([('styles.xml', '<s><a/><b/><c/><d/><e/></s>'), ('x.json', '[[],[]]')]),
                      limits, xml_node_limit=8)
    assert parsed.xml_nodes == 6 and parsed.json_nodes == 5
    with pytest.raises(DiagramImportError, match='JSON.*node limit'):
        _Archive(archive([('styles.xml', '<s/>'), ('x.json', '[[],[],[]]')]), limits, xml_node_limit=8)


def test_diagram_default_still_combines_xml_and_json_at_original_cap():
    limits = replace(DiagramLimits(), max_nodes=5)
    # Each part individually fits five units; the legacy shared diagram total does not.
    with pytest.raises(DiagramImportError, match='JSON.*node limit'):
        _Archive(archive([('a.xml', '<r><a/><b/></r>'), ('a.json', '[[],[]]')]), limits)
    with pytest.raises(DiagramImportError, match='XML.*node limit'):
        _Archive(archive([('a.json', '[[],[]]'), ('a.xml', '<r><a/><b/></r>')]), limits)


def test_semantic_graph_nodes_have_an_independent_limit():
    limits = replace(DiagramLimits(), max_nodes=2)
    value = _Result(_Archive(archive([('safe.xml', '<root/>')]), limits))
    value.node('scope', 'one', 'safe.xml', 'one')
    value.node('scope', 'two', 'safe.xml', 'two')
    with pytest.raises(DiagramImportError, match='Semantic graph node limit'):
        value.node('scope', 'three', 'safe.xml', 'three')


def test_docx_semantic_limit_remains_independent_of_larger_xml_budget():
    with pytest.raises(DocxImportError, match='Semantic graph node limit'):
        parse_docx('graph.docx', archive(minimal_parts(paragraphs=2)), limits=replace(DiagramLimits(), max_nodes=2))


@pytest.mark.parametrize('part', ['<!DOCTYPE styles><styles/>', '<s>'*65+'</s>'*65])
def test_unused_style_xml_still_rejects_dtd_and_excess_depth(part):
    with pytest.raises(DocxImportError, match='DTD/entity|XML depth'):
        parse_docx('unsafe.docx', archive(minimal_parts()+[('word/styles.xml', part)]))
