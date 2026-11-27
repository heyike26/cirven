"""Actual Pillow/OOXML detail boundaries; no Office or model qualification."""
from io import BytesIO
import base64
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from PIL import Image, ImageDraw
import pytest

from packages.domain import planning_diagrams as producer
from packages.domain.local_workspaces import digest
from packages.domain.planning_docx import _table, _write_supplement
from packages.domain.planning_word_diagrams import append_diagram

ROOT = Path(producer.__file__).resolve().parents[2]


def check(value, message):
    if not value:
        raise AssertionError(message)


@pytest.fixture
def legacy_projection_fallback(monkeypatch):
    # Cropped-window assertions belong to the explicitly unavailable projection
    # path; successful complete reading projections have no detail windows.
    from packages.domain import planning_word_projection
    monkeypatch.setattr(planning_word_projection,'build_reading_projection',
        lambda *args,**kwargs:{'status':'NOT_SINGLE_PAGE_READABLE','reason':'LOCAL_LAYOUT_UNAVAILABLE'})


@pytest.fixture(scope='module')
def original_b():
    graph = json.loads((ROOT/'tests/fixtures/diagram_original_b_g01.json').read_bytes())['graph']
    font, font_raw = producer._font(ROOT)
    svg, png, metadata = producer.render_graph(graph, font, source_root=ROOT, with_geometry=True)
    return graph, font, svg, png, metadata, {
        'graph': graph, 'model_sha256': digest(graph), 'png_sha256': hashlib.sha256(png).hexdigest(),
        'render_geometry': metadata['render_geometry'],
        'render_source': {'layout': metadata['layout'], 'layout_engine': metadata['layout_engine'],
            'renderer_sha256': hashlib.sha256(Path(producer.__file__).read_bytes()).hexdigest(),
            'font_sha256': hashlib.sha256(font_raw).hexdigest(), 'font_face_index': font.index}}


@pytest.mark.parametrize('rect', [(0, 33, 918, 1323), (43, 246, 961, 1380)])
def test_real_detail_draws_only_complete_objects_and_no_repeated_title_or_id(original_b, monkeypatch, rect):
    graph, font, _, _, metadata, diagram = original_b
    left, top, right, bottom = rect
    viewport = {'x': left, 'y': top, 'width': right-left, 'height': bottom-top}
    contained = lambda b: left <= b['x'] and top <= b['y'] and right >= b['x']+b['width'] and bottom >= b['y']+b['height']
    boxes = metadata['render_geometry']['logical_content_boxes']
    complete = {(b['kind'], b['id']) for b in boxes if contained(b)}
    check(complete, 'A detail must retain complete objects alongside clipped neighbors')
    partial = [b for b in boxes if not contained(b) and b['x'] < right and b['x']+b['width'] > left and b['y'] < bottom and b['y']+b['height'] > top]
    check(partial, 'Actual native-page case must contain partial duplicate objects')
    painted_text = []
    original_text = ImageDraw.ImageDraw.text
    def record_actual_text(self, xy, value, *args, **kwargs):
        painted_text.append(value)
        return original_text(self, xy, value, *args, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', record_actual_text)
    svg, png = producer.render_graph(graph, font, source_root=ROOT,
        _layout_result=(metadata['layout'], metadata['layout_engine']), _viewport=viewport)
    root = ET.fromstring(svg); visible = set()
    for group in root:
        if group.get('data-node-id'):
            visible.add(('NODE', group.get('data-node-id')))
            check(any(n.tag.endswith('rect') for n in group), 'Complete node retains its box')
        if group.get('data-edge-id'):
            has_text = any(n.tag.endswith('text') for n in group)
            has_box = any(n.tag.endswith('rect') for n in group)
            check(has_text == has_box, 'Label text and background share complete containment')
            if has_text:
                visible.add(('EDGE_LABEL', group.get('data-edge-id')))
    check(visible == complete, 'Partial node/label boxes and text must not be painted')
    check(not any(n.tag.endswith('text') for n in root), 'Detail does not repaint graph title/footer')
    check(painted_text == [n.text or '' for n in root.iter() if n.tag.endswith('text')], 'Actual PNG and SVG emit the same complete text')
    with Image.open(BytesIO(png)) as image:
        check(image.size == (viewport['width'], viewport['height']), 'Original detail scale/dimensions preserved')
        check(any(color == (96,117,140) for _,color in image.getcolors(image.width*image.height)), 'Existing relationship routes remain drawn')
    check(producer.render_diagram_viewport(diagram, viewport, source_root=ROOT) == png, 'Public frozen-renderer path uses the same detail policy')
    from packages.domain.store import InvalidInput
    with pytest.raises(InvalidInput, match='outside its bounded logical image'):
        producer.render_diagram_viewport(diagram, {**viewport, 'height': metadata['render_geometry']['logical_height_px'] - top + 1}, source_root=ROOT)


def test_actual_detail_pages_keep_all_eighteen_items_and_short_captions(original_b):
    graph, _, svg, png, metadata, diagram = original_b
    document = Document(); section = document.sections[0]
    section.page_width=Pt(595); section.page_height=Pt(842)
    section.left_margin=section.right_margin=Pt(68)
    section.top_margin=Pt(62); section.bottom_margin=Pt(63)
    audit=[]; plan=append_diagram(document, diagram, png,
        {'width':metadata['render_geometry']['width_px'], 'height':metadata['render_geometry']['height_px']}, audit=audit)
    expected={('NODE', n['id']) for n in graph['nodes']}|{('EDGE_LABEL', e['id']) for e in graph['edges']}
    covered={(n['kind'], n['id']) for w in plan['windows'] for n in w['complete_items']}
    check(covered == expected and len(covered) == 18, 'Every original item still has a complete readable detail')
    check(all(w['minimum_font_pt'] >= 9 and len(w['rect']) == 4 for w in audit[0]['windows']), 'Scale/rect remain in audit')
    captions=[p.text for p in document.paragraphs if p.text.startswith('图 1 · 详图')]
    check(captions == [f'图 1 · 详图 {i}/{len(plan["windows"])}' for i in range(1,len(plan['windows'])+1)], 'Captions have only figure/detail numbers')
    check(len(document.inline_shapes) == len(plan['windows'])+1, 'Full overview remains alongside readable details')
    root=ET.fromstring(svg)
    check(len([n for n in root if n.get('data-node-id')]) == 9, 'Overview still contains every node')
    check(len([n for n in root if n.get('data-edge-id')]) == 9, 'Overview still contains every relation')


def test_repeating_table_header_keeps_with_first_body_row_after_docx_reload():
    document=Document(); _table(document, ['关系编号', '关系全文'], [['E01', '正文仍按正常分页']], [.2,.8])
    output=BytesIO(); document.save(output); restored=Document(BytesIO(output.getvalue()))
    table=restored.tables[0]
    check(table.rows[0]._tr.trPr.find(qn('w:tblHeader')) is not None, 'Header still repeats')
    check(all(p.paragraph_format.keep_with_next is True for c in table.rows[0].cells for p in c.paragraphs), 'Header paragraphs cannot orphan before their body row')
    check(all(p.paragraph_format.keep_with_next is not True for c in table.rows[1].cells for p in c.paragraphs), 'Body rows do not become one unbreakable table')


def test_short_and_oversized_records_keep_full_text_without_chaining_the_table():
    long_text='\n'.join(f'完整原文 {index}：保留输入和已保存的人工修改。' for index in range(600))
    document=Document()
    _table(document, ['记录','完整内容'], [['短来源标签 SHA256','a'*64],['跨多页记录',long_text]], [.28,.72])
    output=BytesIO();document.save(output);restored=Document(BytesIO(output.getvalue()))
    table=restored.tables[0]
    check(table.cell(1,0).text=='短来源标签 SHA256', 'Short record labels stay complete')
    check(table.cell(2,1).text==long_text, 'Oversized records are never cut to fit a page')
    for row in table.rows:
        check(row._tr.trPr.find(qn('w:cantSplit')) is not None, 'Records request row continuity from Word')
    check(all(p.paragraph_format.keep_with_next is not True for row in table.rows[1:] for c in row.cells for p in c.paragraphs), 'Rows do not chain the entire table together')


def test_legacy_figure_introduction_stays_connected_to_its_overview(original_b,legacy_projection_fallback):
    graph, _, _, png, _, diagram=original_b
    document=Document()
    _write_supplement(document, {'diagrams':[{**diagram,'png_base64':base64.b64encode(png).decode('ascii')}]})
    output=BytesIO();document.save(output);restored=Document(BytesIO(output.getvalue()))
    paragraphs=restored.paragraphs
    index=next(i for i,p in enumerate(paragraphs) if p.text=='图 1  '+graph['title'])
    check(paragraphs[index+1].paragraph_format.keep_with_next is True, 'The figure explanation cannot orphan the heading from the image')
    check(paragraphs[index+2].text=='图 1 · 概览', 'The original overview follows the explanation')
    check(paragraphs[index+2].paragraph_format.keep_with_next is True, 'The overview caption stays with the image')
    check(paragraphs[index+3]._p.find('.//'+qn('w:drawing')) is not None, 'The original image is preserved after its caption')


def test_legacy_graph_tables_index_all_complete_objects_and_cross_tile_endpoints(original_b,legacy_projection_fallback):
    graph, _, _, png, metadata, diagram = original_b
    diagram={**diagram,'png_base64':base64.b64encode(png).decode('ascii')}
    document=Document();audit=[]
    _write_supplement(document,{'diagrams':[diagram]},diagram_audit=audit)
    check(len(audit)==1,'Actual graph is rendered and audited once')
    windows=audit[0]['windows']
    boxes=metadata['render_geometry'].get('logical_content_boxes',metadata['render_geometry']['content_boxes'])
    complete={}
    for box in boxes:
        pages=[]
        for index,window in enumerate(windows,1):
            l,t,r,b=window['rect']
            if l<=box['x']and t<=box['y']and r>=box['x']+box['width']and b>=box['y']+box['height']:
                pages.append(index)
        complete[(box['kind'],box['id'])]=pages
    check(any(len(pages)>1 for pages in complete.values()),'Actual geometry exercises complete repeated objects')
    node_table,edge_table=document.tables[-2:]
    check([c.text for c in node_table.rows[0].cells]==['节点编号','名称与说明','需求依据','完整详图号'],'Node table exposes the actual complete-view index')
    check([c.text for c in edge_table.rows[0].cells]==['关系编号','起点 → 终点','关系','完整对象详图号'],'Relation table exposes label and endpoint view indexes')
    pages=lambda key:'、'.join(map(str,complete[key]))or'未核完整位置'
    for row,node in zip(node_table.rows[1:],graph['nodes']):
        check(row.cells[0].text==node['id']and row.cells[3].text==pages(('NODE',node['id'])),
              'Node index must include all actual containing views, not only newly-covered items')
    for row,edge in zip(edge_table.rows[1:],graph['edges']):
        expected='文字：'+pages(('EDGE_LABEL',edge['id']))+'；起点：'+pages(('NODE',edge['source']))+'；终点：'+pages(('NODE',edge['target']))
        check(row.cells[0].text==edge['id']and row.cells[2].text==edge['relation']and row.cells[3].text==expected,
              'Original relation and its endpoint locations must remain tied to the actual graph')
    check(len(document.inline_shapes)==len(windows)+1,'Index must not add or replace frozen images')
    check(any('线段触及边界不表示关系结束'in p.text for p in document.paragraphs),'Clipped lines are explicitly explained')
