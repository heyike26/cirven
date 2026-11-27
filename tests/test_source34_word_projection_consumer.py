"""The default Word consumer uses one complete derived graph or explicit legacy fallback."""
from copy import deepcopy
from io import BytesIO
import base64
import hashlib
import json
from pathlib import Path

from docx import Document
import pytest

from packages.domain import planning_diagrams as producer
from packages.domain import planning_docx as renderer
from packages.domain.local_workspaces import digest
from packages.domain.store import InvalidInput
from test_source34_report_semantics import source,body,check

ROOT=Path(renderer.__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def complete_source_diagram():
    original=json.loads((ROOT/'tests/fixtures/diagram_original_b_g01.json').read_bytes())['graph']
    graph={**deepcopy(original),'id':'G_INTERNAL_SOURCE_ONLY','title':'当前交接的完整业务关系',
           'nodes':deepcopy(original['nodes'][:2]),'edges':[deepcopy(original['edges'][0])]}
    graph['nodes'][0]['id']='INTERNAL_TASK_ALPHA';graph['nodes'][1]['id']='INTERNAL_TASK_BETA'
    graph['edges'][0].update(id='INTERNAL_EDGE_ALPHA_BETA',source='INTERNAL_TASK_ALPHA',target='INTERNAL_TASK_BETA')
    graph['nodes'][0]['description']='第一项完整业务说明；人工边界不能丢。'
    graph['nodes'][1]['description']='第二项完整业务说明；等待审查不能当作执行完成。'
    font,font_raw=producer._font(ROOT)
    _,png,metadata=producer.render_graph(graph,font,source_root=ROOT,with_geometry=True)
    return {'graph':graph,'model_sha256':digest(graph),'png_sha256':hashlib.sha256(png).hexdigest(),
        'png_base64':base64.b64encode(png).decode('ascii'),'render_geometry':metadata['render_geometry'],
        'render_source':{'layout':metadata['layout'],'layout_engine':metadata['layout_engine'],
            'renderer_sha256':hashlib.sha256(Path(producer.__file__).read_bytes()).hexdigest(),
            'font_sha256':hashlib.sha256(font_raw).hexdigest(),'font_face_index':font.index}}


def test_default_complete_projection_has_one_readable_graph_and_lossless_business_appendix(complete_source_diagram,tmp_path):
    snapshot=source();snapshot['diagrams']=[deepcopy(complete_source_diagram)];before=deepcopy(snapshot)
    raw,receipt=renderer.render_docx(snapshot);document=Document(BytesIO(raw));text=body(document)
    check(snapshot==before,'A reading projection cannot rewrite the graph, source PNG or source snapshot')
    check(len(receipt['diagram_readability'])==1)
    audit=receipt['diagram_readability'][0];graph=complete_source_diagram['graph']
    check(audit['projection']=='CIRVEN_WORD_READING_PROJECTION_V1' and audit['status']=='GEOMETRY_CHECKED_NATIVE_WORD_NOT_RUN')
    check(audit['windows']==[] and audit['complete_item_count']==len(graph['nodes'])+len(graph['edges']))
    check(audit['source_png_sha256']==complete_source_diagram['png_sha256'] and audit['graph_sha256']==digest(graph))
    check(audit['minimum_font_pt']>=9.5 and audit['figure_number']==1)
    check(audit['png_sha256']!=audit['source_png_sha256'],'Derived reading pixels stay distinct from preserved source pixels')
    check(len(document.inline_shapes)==1,'A successful complete projection must not add overview/crop pages')
    drawing_paragraph=next(p for p in document.paragraphs if p._p.xpath('.//w:drawing'))
    check(drawing_paragraph.paragraph_format.line_spacing==1.0,'An inline figure cannot inherit body leading and exceed its allocated page area')
    check(drawing_paragraph.paragraph_format.keep_with_next is True,'The figure stays with its explanatory caption')
    heading=next(p for p in document.paragraphs if p.text=='图 1  '+graph['title'])
    check(heading.paragraph_format.page_break_before is True)
    check(not any('详图' in p.text for p in document.paragraphs))
    appendix=text[text.index('\n可追溯附录\n'):]
    for node in graph['nodes']:
        check(node['description']in appendix and node['label']in appendix,'Complete descriptions remain editable and reachable by business label')
        for claim in node['claim_ids']:check(claim in appendix)
    for internal in [graph['id'],*[n['id']for n in graph['nodes']]]:check(internal not in text,'Internal graph/task identity remains outside the default prose')
    check('本次详细展开仍待方案审查。'in text and snapshot['proposal']['payload']['status']=='CONFIRMED')
    (tmp_path/'complete-reading-projection.docx').write_bytes(raw)
    (tmp_path/'complete-reading-projection-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')


@pytest.mark.parametrize('fault',['png','model'])
def test_source_identity_mismatch_is_rejected_before_projection_or_fallback(complete_source_diagram,monkeypatch,fault):
    from packages.domain import planning_word_projection
    snapshot=source();diagram=deepcopy(complete_source_diagram);snapshot['diagrams']=[diagram]
    diagram['png_sha256'if fault=='png'else'model_sha256']='0'*64
    before=deepcopy(snapshot);calls=[]
    def forbidden(*args,**kwargs):
        calls.append(1)
        raise AssertionError('Invalid source must be rejected before derived layout/fallback')
    monkeypatch.setattr(planning_word_projection,'build_reading_projection',forbidden)
    with pytest.raises(InvalidInput,match='hash mismatch'):renderer.render_docx(snapshot)
    check(not calls and snapshot==before)


def test_projection_identity_refusal_cannot_use_the_legacy_fallback(complete_source_diagram,monkeypatch):
    from packages.domain import planning_word_projection,planning_word_diagrams
    snapshot=source();snapshot['diagrams']=[deepcopy(complete_source_diagram)]
    monkeypatch.setattr(planning_word_projection,'build_reading_projection',
        lambda *args,**kwargs:{'status':'NOT_SINGLE_PAGE_READABLE','reason':'SOURCE_GRAPH_IDENTITY_MISMATCH'})
    def forbidden(*args,**kwargs):
        raise AssertionError('Identity failure cannot display a legacy graph')
    monkeypatch.setattr(planning_word_diagrams,'append_diagram',forbidden)
    with pytest.raises(InvalidInput,match='hash mismatch'):renderer.render_docx(snapshot)


def test_projection_identity_rejection_cannot_be_downgraded_to_legacy_fallback(complete_source_diagram,monkeypatch):
    from packages.domain import planning_word_projection,planning_word_diagrams
    snapshot=source();snapshot['diagrams']=[deepcopy(complete_source_diagram)];before=deepcopy(snapshot)
    monkeypatch.setattr(planning_word_projection,'build_reading_projection',lambda *args,**kwargs:{
        'status':'NOT_SINGLE_PAGE_READABLE','reason':'SOURCE_GRAPH_IDENTITY_MISMATCH'})
    calls=[]
    def forbidden(*args,**kwargs):
        calls.append(1)
        raise AssertionError('A source identity rejection cannot enter legacy rendering')
    monkeypatch.setattr(planning_word_diagrams,'append_diagram',forbidden)
    with pytest.raises(InvalidInput,match='hash mismatch'):renderer.render_docx(snapshot)
    check(not calls and snapshot==before)
