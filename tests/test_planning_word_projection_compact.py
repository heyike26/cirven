"""Complete actual eight-node/twelve-relation reading view, no native Word claim.

The graph is the frozen real Source38 draft; optional D evidence supplies its
actual source PNG. Portable runs use only a declared synthetic source raster.
"""
from copy import deepcopy
import base64
import hashlib
from io import BytesIO
import json
import math
import os
import unicodedata
from pathlib import Path

from PIL import Image, ImageChops
import pytest

from packages.domain import planning_word_projection as projection
from packages.domain.intake import canonical
from packages.domain.store import InvalidInput

R=Path(__file__).resolve().parents[1]
ACTUAL_GRAPH=json.loads(r'''{"edges":[{"claim_ids":["draft-work-model"],"id":"e1","origin":"MODEL_SUGGESTION","relation":"核来源并仅补实际缺口","source":"materials","source_anchors":[],"target":"draft"},{"claim_ids":["round-boundary","draft-work-model"],"id":"e2","origin":"MODEL_SUGGESTION","relation":"后续另行提交确认","source":"draft","source_anchors":[],"target":"review"},{"claim_ids":["round-boundary","draft-work-model"],"id":"e3","origin":"MODEL_SUGGESTION","relation":"仅在后续明确操作完成规划/导出后","source":"review","source_anchors":[],"target":"artifact"},{"claim_ids":["draft-work-model","coverage-warning"],"id":"e4","origin":"MODEL_SUGGESTION","relation":"回答包含实际支持路线的真实产物时","source":"native","source_anchors":[],"target":"artifact"},{"claim_ids":["draft-work-model"],"id":"e5","origin":"MODEL_SUGGESTION","relation":"打开使用后继续反馈","source":"artifact","source_anchors":[],"target":"feedback"},{"claim_ids":["draft-acceptance-package"],"id":"e6","origin":"MODEL_SUGGESTION","relation":"版本、差异及必要授权满足后","source":"feedback","source_anchors":[],"target":"check"},{"claim_ids":["draft-work-model"],"id":"e7","origin":"MODEL_SUGGESTION","relation":"仍需返工时回到同任务反馈","source":"check","source_anchors":[],"target":"feedback"},{"claim_ids":["draft-work-model","draft-acceptance-package"],"id":"e8","origin":"MODEL_SUGGESTION","relation":"影响要求或依据时只重开相关部分","source":"feedback","source_anchors":[],"target":"draft"},{"claim_ids":["recovery-resource-standard","draft-work-model"],"id":"e9","origin":"MODEL_SUGGESTION","relation":"结果未知、冲突或执行中断","source":"check","source_anchors":[],"target":"recover"},{"claim_ids":["draft-work-model"],"id":"e10","origin":"MODEL_SUGGESTION","relation":"切换、关闭或草稿恢复需要","source":"draft","source_anchors":[],"target":"recover"},{"claim_ids":["draft-work-model","recovery-resource-standard"],"id":"e11","origin":"MODEL_SUGGESTION","relation":"原操作状态核明且可安全继续时","source":"recover","source_anchors":[],"target":"feedback"},{"claim_ids":["draft-work-model","round-boundary"],"id":"e12","origin":"MODEL_SUGGESTION","relation":"恢复文稿上下文，仍保留未确认身份","source":"recover","source_anchors":[],"target":"draft"}],"id":"draft-user-workflow","kind":"FLOW","nodes":[{"claim_ids":["draft-work-model","authorized-direction"],"description":"保留当前工作；精确绑定对象、任务和版本，歧义才澄清。","id":"native","label":"原生页面提问/批注","origin":"MODEL_SUGGESTION","source_anchors":[]},{"claim_ids":["draft-work-model","latest-scope"],"description":"依据最新范围核来源和实际缺口，不固定套发现问卷。","id":"materials","label":"本地资料/已有任务书","origin":"MODEL_SUGGESTION","source_anchors":[]},{"claim_ids":["draft-work-model","round-boundary"],"description":"缺答案仍保留独立产物；本轮到此供人审。","id":"draft","label":"阶段文稿、结构图及待确认项","origin":"MODEL_SUGGESTION","source_anchors":[]},{"claim_ids":["draft-work-model","round-boundary"],"description":"要求确认与方案确认分开，不能以沉默代确认。","id":"review","label":"后续分层确认","origin":"MODEL_SUGGESTION","source_anchors":[]},{"claim_ids":["draft-work-model","draft-acceptance-package"],"description":"工程路径需后续明确操作完成规划/导出；原生路径仅列实际支持的交付物。","id":"artifact","label":"真实产物原生打开","origin":"MODEL_SUGGESTION","source_anchors":[]},{"claim_ids":["draft-work-model","coverage-warning"],"description":"检查保存后版本、权限及手改差异，必要时预览/授权。","id":"feedback","label":"同任务反馈与版本核对","origin":"MODEL_SUGGESTION","source_anchors":[]},{"claim_ids":["draft-acceptance-package","coverage-warning"],"description":"仅适用于实际支持且获得相应授权的路线；逐维记录证据。","id":"check","label":"限定修改与原软件检查","origin":"MODEL_SUGGESTION","source_anchors":[]},{"claim_ids":["draft-work-model","recovery-resource-standard"],"description":"不重放未知动作；恢复不等于批准，不能自动跨越确认。","id":"recover","label":"异常/未知：保草稿并核原操作","origin":"MODEL_SUGGESTION","source_anchors":[]}],"title":"首版目标工作链与恢复入口（建议模型，非实现证明）"}''')
ACTUAL_GRAPH_SHA='c311c91c343cb6d1c8fbcb4ee34e113649cb03b9fecf5ed0feed7d1dd8c8b76b'


def check(value,message='Complete Word view acceptance failed'):
    if not value:raise AssertionError(message)


@pytest.fixture(scope='module')
def actual_diagram():
    path=os.environ.get('CIRVEN_PROJECTION_ACTUAL_DIAGRAM_PATH')
    if path:
        diagram=json.loads(Path(path).read_bytes())
        check(diagram['graph']==ACTUAL_GRAPH and diagram['model_sha256']==ACTUAL_GRAPH_SHA)
        check(hashlib.sha256(base64.b64decode(diagram['png_base64'])).hexdigest()==diagram['png_sha256'])
        return diagram
    output=BytesIO();Image.new('RGB',(8,8),'white').save(output,format='PNG');raw=output.getvalue()
    return {'graph':deepcopy(ACTUAL_GRAPH),'model_sha256':ACTUAL_GRAPH_SHA,
            'png_base64':base64.b64encode(raw).decode(),'png_sha256':hashlib.sha256(raw).hexdigest(),
            'render_source':{'synthetic_portable_raster_only':True}}


@pytest.fixture(scope='module')
def rendered_actual(actual_diagram):
    before=deepcopy(actual_diagram)
    real_layout,real_draw=projection.layout_graph,projection.ImageDraw.Draw
    calls,texts,lines,arrows=[],[],[],[]
    def capture_layout(*args):
        value=real_layout(*args)
        calls.append({'input':deepcopy(args[:2]),'value':value,'before':deepcopy(value)})
        return value
    def capture_draw(image):
        draw=real_draw(image)
        class Recorder:
            def __getattr__(self,name):return getattr(draw,name)
            def text(self,point,text,**kwargs):
                texts.append((point,text,kwargs['font'].getbbox(text,anchor='lt')))
                return draw.text(point,text,**kwargs)
            def line(self,points,**kwargs):
                lines.append(deepcopy(points));return draw.line(points,**kwargs)
            def polygon(self,points,**kwargs):
                arrows.append(deepcopy(points));return draw.polygon(points,**kwargs)
        return Recorder()
    projection.layout_graph,projection.ImageDraw.Draw=capture_layout,capture_draw
    try:result=projection.build_reading_projection(actual_diagram,464.9,602.85,source_root=R)
    finally:projection.layout_graph,projection.ImageDraw.Draw=real_layout,real_draw
    check(actual_diagram==before)
    return result,calls,texts,lines,arrows


def test_real_eight_node_twelve_edge_graph_fits_actual_template_without_semantic_or_source_changes(actual_diagram,rendered_actual):
    result,calls,_,_,_=rendered_actual
    check(result['status']=='GEOMETRY_CHECKED_NATIVE_WORD_NOT_RUN',str(result))
    check(result['minimum_font_pt']>=9.5)
    check(result['audit']['graph_sha256']==ACTUAL_GRAPH_SHA)
    check(result['audit']['node_count']==8 and result['audit']['edge_count']==12)
    check(result['audit']['all_routes_complete'] and result['audit']['all_labels_complete'])
    check(result['audit']['source_png_sha256']==actual_diagram['png_sha256'])
    check(len(calls)==2 and [a['node_wrap_px']for a in result['audit']['layout_attempts']]==[216,112])
    check([a['edge_wrap_px']for a in result['audit']['layout_attempts']]==[90,72])
    check(result['audit']['layout_attempts'][0]['minimum_font_pt']<9.5)
    check(all(call['value']==call['before'] for call in calls),'An existing computed layout cannot be changed in place')
    path=os.environ.get('CIRVEN_PROJECTION_EVIDENCE_DIR')
    if path:
        folder=Path(path);folder.mkdir()
        with(folder/'complete-actual-reading-view.png').open('xb')as f:f.write(result['png_bytes'])
        with(folder/'RESULT.actual.r1.json').open('x')as f:json.dump({k:v for k,v in result.items()if k!='png_bytes'},f,ensure_ascii=False,indent=2);f.write('\n')


def test_all_twenty_actual_labels_glyphs_and_twelve_complete_routes_are_painted_inside_sixteen_pixel_margin(rendered_actual):
    result,calls,texts,lines,arrows=rendered_actual
    audit=result['audit'];transform=audit['canvas_transform'];layout=calls[-1]['value'][0]
    dx,dy=transform['offset_x_px'],transform['offset_y_px']
    nodes={n['id']:n for n in layout['nodes']};edges={e['id']:e for e in layout['edges']}
    expected=[(e['relation'],edges[e['id']]['labels'][0],projection.EDGE_PAD)for e in sorted(ACTUAL_GRAPH['edges'],key=lambda n:n['id'])]
    expected += [(n['label'],nodes[n['id']],projection.NODE_PAD)for n in sorted(ACTUAL_GRAPH['nodes'],key=lambda n:n['id'])]
    cursor=0
    for label,box,pad in expected:
        actual=''
        while len(actual)<len(label):
            (x,y),row,(left,top,right,bottom)=texts[cursor];cursor+=1;actual+=row
            check(not row or row[0]not in projection.FORBIDDEN_WRAP_START,
                  'The actual diagram must not introduce a closing mark or slash at a wrapped line start')
            check(box['x']+dx+pad-.01<=x+left<=x+right<=box['x']+dx+box['width']-pad+.01)
            check(box['y']+dy+pad-.01<=y+top<=y+bottom<=box['y']+dy+box['height']-pad+.01)
        check(actual==label,'Every original character must reach the real text drawing calls in order')
    check(cursor==len(texts) and len(expected)==20 and len(lines)==len(arrows)==12)
    def boundary(point,box):
        x,y=point;l,t=box['x']+dx,box['y']+dy;r,b=l+box['width'],t+box['height']
        return l-.01<=x<=r+.01 and t-.01<=y<=b+.01 and min(abs(x-l),abs(x-r),abs(y-t),abs(y-b))<=.01
    for edge,painted,arrow in zip(sorted(ACTUAL_GRAPH['edges'],key=lambda e:e['id']),lines,arrows):
        check(boundary(painted[0],nodes[edge['source']]) and boundary(painted[-1],nodes[edge['target']]))
        check(arrow[0]==painted[-1])
        for x,y in [*painted,*arrow]:check(16<=x<=result['width_px']-16 and 16<=y<=result['height_px']-16)
    image=Image.open(BytesIO(result['png_bytes'])).convert('RGB')
    bounds=ImageChops.difference(image,Image.new('RGB',image.size,'white')).getbbox()
    check(bounds is not None and min(bounds[:2])>=16 and bounds[2]<=image.width-16 and bounds[3]<=image.height-16,
          'The raster, including an integer last pixel, needs sixteen actual blank pixels on all sides')
    check(image.size==(result['width_px'],result['height_px']))
    check(transform['all_glyph_ink_inside_label_boxes'] and transform['complete_node_label_count']==20 and transform['complete_route_count']==12)
    check(transform['original_layout_sha256']==hashlib.sha256(canonical(layout).encode()).hexdigest())
    for k in ('physical_width_pt','physical_height_pt'):check(abs(result[k]*20-round(result[k]*20))<1e-6)
    check(result['physical_width_pt']<=464.9 and result['physical_height_pt']<=602.85)


def test_both_candidate_failures_are_bounded_and_no_image_is_allocated_for_a_tiny_page(actual_diagram,monkeypatch):
    real=projection.layout_graph;calls=[]
    def layout(*args):calls.append(1);return real(*args)
    def forbidden(*args,**kwargs):raise AssertionError('An unreadable candidate cannot allocate a PNG canvas')
    monkeypatch.setattr(projection,'layout_graph',layout);monkeypatch.setattr(projection.Image,'new',forbidden)
    result=projection.build_reading_projection(actual_diagram,100,100,source_root=R)
    check(result['reason']=='COMPLETE_LAYOUT_BELOW_TARGET_9_5PT'and 'png_bytes'not in result and len(calls)==2)
    check(len(result['layout_attempts'])==2 and all(v['minimum_font_pt']<9.5 for v in result['layout_attempts']))


def test_pixel_budget_is_preserved_before_image_allocation(actual_diagram,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('The image budget must precede allocation')
    monkeypatch.setattr(projection,'MAX_PIXELS',1);monkeypatch.setattr(projection.Image,'new',forbidden)
    result=projection.build_reading_projection(actual_diagram,464.9,602.85,source_root=R)
    check(result['reason']=='BOUNDED_IMAGE_LIMIT'and 'png_bytes'not in result)
    check(len(result['layout_attempts'])==1)


@pytest.mark.parametrize('limit,expected',[('MAX_NODES','BOUNDED_GRAPH_LIMIT'),('MAX_EDGES','BOUNDED_GRAPH_LIMIT'),('MAX_TEXT','BOUNDED_TEXT_LIMIT')])
def test_existing_graph_and_text_caps_stop_before_any_layout(actual_diagram,monkeypatch,limit,expected):
    monkeypatch.setattr(projection,limit,1)
    def forbidden(*args):raise AssertionError('The original cap must not dispatch any layout')
    monkeypatch.setattr(projection,'layout_graph',forbidden)
    result=projection.build_reading_projection(actual_diagram,464.9,602.85,source_root=R)
    check(result['reason']==expected and 'png_bytes'not in result)


@pytest.mark.parametrize('text,width', [('原生页面提问/批注',112),('结果未知、冲突或执行中断',72),
                                       ('汉、字',18),('A\u0301、B',18),('不改原文。保持字符！',72)])
def test_local_word_wrap_keeps_complete_character_order_without_a_generated_closing_mark_start(text,width):
    face,_=projection._font(R);face=face.font_variant(size=projection.FONT_PX)
    rows=projection._word_lines(text,width,face)
    check(''.join(rows)==text)
    check(all(not row or row[0]not in projection.FORBIDDEN_WRAP_START for row in rows))
    check(all(not row or not unicodedata.combining(row[0]) for row in rows))


def test_explicit_source_line_start_is_retained_rather_than_rewritten():
    face,_=projection._font(R);face=face.font_variant(size=projection.FONT_PX)
    check(projection._word_lines('原文\n/明确另起一行',1000,face)==['原文','/明确另起一行'])
