"""Geometric pagination and actual DOCX images; no model, API or Office."""
from copy import deepcopy
from io import BytesIO
import hashlib

import pytest
from docx import Document
from docx.shared import Mm
from PIL import Image,ImageDraw

from packages.domain.planning_word_diagrams import detail_windows,append_diagram
from packages.domain.store import InvalidInput


def check(value,message='Word diagram acceptance failed'):
    if not value:raise AssertionError(message)


def fixture():
    graph={'id':'G01','nodes':[{'id':'N1'},{'id':'N2'}],'edges':[{'id':'E1'}]}
    geometry={'version':1,'width_px':1000,'height_px':1600,'minimum_font_px':18,
        'text_complete':True,'content_boxes':[
        {'kind':'NODE','id':'N1','x':20,'y':30,'width':250,'height':150},
        {'kind':'NODE','id':'N2','x':740,'y':1400,'width':250,'height':150},
        {'kind':'EDGE_LABEL','id':'E1','x':430,'y':750,'width':200,'height':200}]}
    return graph,geometry


def test_detail_plan_proves_every_item_complete_at_nine_points_without_tiny_fit():
    graph,geometry=fixture();plan=detail_windows(geometry,graph,1000,1600,450,600)
    check(plan['minimum_font_pt']==9);check(plan['points_per_pixel']==.5)
    covered=set()
    for window in plan['windows']:
        left,top,right,bottom=window['rect'];check((right-left)*.5<=450);check((bottom-top)*.5<=600)
        check((right-left)*(bottom-top)<=2_000_000)
        for item in window['complete_items']:
            box=next(b for b in geometry['content_boxes']if (b['kind'],b['id'])==(item['kind'],item['id']))
            check(left<=box['x']and top<=box['y']and right>=box['x']+box['width']and bottom>=box['y']+box['height'])
            covered.add((item['kind'],item['id']))
    check(covered=={('NODE','N1'),('NODE','N2'),('EDGE_LABEL','E1')})


@pytest.mark.parametrize('fault',['missing','foreign','duplicate','outside','nan','too-wide','wrong-png'])
def test_invalid_or_unpageable_geometry_rejects_without_shrinking(fault):
    graph,geometry=fixture()
    if fault=='missing':geometry['content_boxes'].pop()
    if fault=='foreign':geometry['content_boxes'][0]['id']='other'
    if fault=='duplicate':geometry['content_boxes'][1]=deepcopy(geometry['content_boxes'][0])
    if fault=='outside':geometry['content_boxes'][0]['x']=-1
    if fault=='nan':geometry['content_boxes'][0]['x']=float('nan')
    if fault=='too-wide':geometry['content_boxes'][0].update(x=0,width=950)
    if fault=='wrong-png':geometry['width_px']=999
    with pytest.raises(InvalidInput):detail_windows(geometry,graph,1000,1600,450,600)


def test_unknown_legacy_geometry_is_not_claimed_readable():
    graph,_=fixture();check(detail_windows(None,graph,1000,1600,450,600)=={'status':'NOT_VERIFIED_MISSING_GEOMETRY','windows':[]})


def test_500_node_synthetic_geometry_has_exact_999_item_coverage_without_extra_execution():
    graph={'nodes':[{'id':f'N{i}'}for i in range(500)],'edges':[{'id':f'E{i}'}for i in range(499)]}
    boxes=[]
    for kind,items in [('NODE',graph['nodes']),('EDGE_LABEL',graph['edges'])]:
        for i,item in enumerate(items):boxes.append({'kind':kind,'id':item['id'],'x':10+(i%10)*90,
            'y':10+(i//10)*90+(30 if kind=='EDGE_LABEL'else 0),'width':70,'height':20})
    geometry={'version':1,'width_px':1000,'height_px':5000,'minimum_font_px':18,'text_complete':True,'content_boxes':boxes}
    plan=detail_windows(geometry,graph,1000,5000,450,600)
    covered={(item['kind'],item['id'])for w in plan['windows']for item in w['complete_items']}
    check(len(covered)==999);check(plan['complete_item_count']==999);check(len(plan['windows'])<=64)


def test_overview_and_original_resolution_viewports_are_embedded_without_section_changes():
    graph,logical=fixture();geometry={**logical,'width_px':500,'height_px':800,'minimum_font_px':9,
        'logical_width_px':1000,'logical_height_px':1600,'logical_minimum_font_px':18,
        'logical_content_boxes':deepcopy(logical['content_boxes']),'png_scale':.5}
    image=Image.new('RGB',(500,800),'white');raw=BytesIO();image.save(raw,format='PNG');raw=raw.getvalue()
    diagram={'graph':graph,'render_geometry':geometry,'png_sha256':hashlib.sha256(raw).hexdigest()}
    document=Document();document.sections[0].left_margin=Mm(26);document.sections[0].right_margin=Mm(24)
    before=document.sections[0]._sectPr.xml;calls=[]
    # This callback is an explicit original raster fixture. Production's frozen
    # geometry/font/hash checker is separately tested by the diagram adapter.
    def original_view(value,viewport):
        check(value is diagram);calls.append(deepcopy(viewport))
        tile=Image.new('RGB',(viewport['width'],viewport['height']),'white')
        ImageDraw.Draw(tile).text((10,10),'ORIGINAL LOGICAL VIEW',(0,0,0));out=BytesIO();tile.save(out,format='PNG');return out.getvalue()
    audit=[];plan=append_diagram(document,diagram,raw,{'width':500,'height':800},audit=audit,viewport_renderer=original_view)
    check(document.sections[0]._sectPr.xml==before);check(len(document.sections)==1)
    check(len(calls)==len(plan['windows'])>0);check(len(document.inline_shapes)==len(calls)+1)
    for shape,viewport in zip(list(document.inline_shapes)[1:],calls):
        check(abs(shape.width/12700/viewport['width']-.5)<.00001)
    out=BytesIO();document.save(out);reopened=Document(BytesIO(out.getvalue()))
    check(len(reopened.inline_shapes)==len(calls)+1);check(audit[0]['minimum_font_pt']==9)
    check(audit[0]['graph_id']=='G01');check('NOT_RUN'in audit[0]['status'])


def test_detail_renderer_returning_wrong_dimensions_is_not_embedded_as_success():
    graph,geometry=fixture();raw=BytesIO();Image.new('RGB',(1000,1600),'white').save(raw,format='PNG');raw=raw.getvalue()
    diagram={'graph':graph,'render_geometry':geometry,'png_sha256':hashlib.sha256(raw).hexdigest()}
    def wrong_size(*args):out=BytesIO();Image.new('RGB',(1,1),'white').save(out,format='PNG');return out.getvalue()
    with pytest.raises(InvalidInput,match='changed the logical viewport dimensions'):
        append_diagram(Document(),diagram,raw,{'width':1000,'height':1600},viewport_renderer=wrong_size)
