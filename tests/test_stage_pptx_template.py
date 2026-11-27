from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt
import pytest

from packages.dynamic_workflow.stage_template_export import describe_template, export_stage_template
from packages.dynamic_workflow.stage_templates import parse_stage_template, template_issues
from test_stage_workflow_runs import ROOT, action, define, node, ok, scope, start


def deck(kind='template'):
    ppt = Presentation()
    page = ppt.slides.add_slide(ppt.slide_layouts[6])
    box = page.shapes.add_textbox(Inches(.5), Inches(.5), Inches(6), Inches(1))
    text = box.text_frame.paragraphs[0]
    text.add_run().text = '项目：'
    if kind == 'static':
        text.add_run().text = '固定排版'
    else:
        run = text.add_run(); run.text = '{{名'; run.font.bold = True; run.font.size = Pt(22)
        if kind == 'split-by-break':
            text.add_line_break()
        text.add_run().text = '称}} / '
        run = text.add_run(); run.text = '{{版本}}'; run.font.italic = True
    if kind == 'template':
        table = page.shapes.add_table(2, 2, Inches(.5), Inches(2), Inches(6), Inches(2)).table
        table.cell(0, 0).text = '编号'; table.cell(0, 1).text = '原排版'
        paragraph = table.cell(1, 0).text_frame.paragraphs[0]
        r = paragraph.add_run(); r.text = '{{编'; r.font.underline = True
        paragraph.add_run().text = '号}}'
        table.cell(1, 1).text = '保留静态说明'
        image = BytesIO(); Image.new('RGB', (8, 8), 'blue').save(image, 'PNG')
        page.shapes.add_picture(image, Inches(7), Inches(2))
        other = ppt.slides.add_slide(ppt.slide_layouts[1])
        other.shapes.title.text = '未填写页'
    out = BytesIO(); ppt.save(out)
    return out.getvalue()


def parsed(raw):
    return describe_template(parse_stage_template('工程汇报.pptx', raw), raw)


def artifact(template):
    values = {('名称', '版本'): ['中文项目', 'v2'], ('编号',): ['OR-1\n检查'], ('内容',): []}
    return {'summary': '审核文稿', 'items': [{'id':'X','title':'确认','description':'完成'}],
            'template_output':'中文项目 v2\nOR-1\n检查', 'template_edits': [
                {'target':t['target'],'values':values[tuple(t['columns'])]}
                for t in template['edit_targets'] if t['required']]}


def parts(raw):
    with ZipFile(BytesIO(raw)) as z:
        return {part: z.read(part) for part in z.namelist()}


def test_marked_slide_and_table_preserve_runs_untouched_pages_media_and_package():
    input_bytes = deck()
    template = parsed(input_bytes); approved = artifact(template)
    assert template['export_extension'] == '.pptx'
    assert [x['columns'] for x in template['edit_targets'] if x['required']] == [['名称','版本'],['编号']]
    blank, = [x for x in template['edit_targets'] if not x['required']]
    assert blank['target'].startswith('slide:2:') and not blank['repeatable']
    assert template_issues(template, approved['template_output'], approved['template_edits']) == []
    name, media, raw = export_stage_template(template, approved, input_bytes)
    assert name == '工程汇报-已审核.pptx' and media.endswith('presentationml.presentation')
    old, new = parts(input_bytes), parts(raw)
    assert old.keys() == new.keys()
    assert {k for k in old if old[k] != new[k]} == {'ppt/slides/slide1.xml'}
    ppt = Presentation(BytesIO(raw))
    para = ppt.slides[0].shapes[0].text_frame.paragraphs[0]
    assert para.text == '项目：中文项目 / v2'
    assert any(r.text == '中文项目' and r.font.bold and r.font.size == Pt(22) for r in para.runs)
    assert any(r.text == 'v2' and r.font.italic for r in para.runs)
    table = ppt.slides[0].shapes[1].table
    assert table.cell(1, 0).text == 'OR-1\v检查'
    assert [r.font.underline for r in table.cell(1, 0).text_frame.paragraphs[0].runs if r.text] == [True, True]
    assert table.cell(1, 1).text == '保留静态说明'
    assert ppt.slides[1].shapes.title.text == '未填写页'
    assert export_stage_template(template, approved, input_bytes)[2] == raw
    with_blank = deepcopy(approved)
    with_blank['template_edits'].append({'target':blank['target'],'values':['保留布局的正文']})
    filled = Presentation(BytesIO(export_stage_template(template, with_blank, input_bytes)[2]))
    assert filled.slides[1].placeholders[1].text == '保留布局的正文'


@pytest.mark.parametrize('kind', ['static', 'split-by-break'])
def test_layout_without_reliable_fields_keeps_text_fallback(kind):
    raw = deck(kind)
    template = parsed(raw)
    assert template['export_extension'] == '.txt' and not template.get('edit_targets')
    assert '纯文本' in template['warnings'][-1]
    assert export_stage_template(template, {'template_output':'审核后正文'}, raw)[2].decode() == '审核后正文'
    historical = parse_stage_template('旧版汇报.pptx', raw)
    assert export_stage_template(historical, {'template_output':'历史正文'}, None)[2].decode() == '历史正文'


def test_download_requires_exact_approved_stage_and_rejects_unfillable_input(client):
    owner=scope(client);define(client,owner,[node('brief')]);run=start(client,owner)
    raw = deck()
    template=ok(client.post(ROOT+'/template',data=owner,files={'file':('工程汇报.pptx',raw)}))
    result = artifact(template)
    for invalid in ['\x00', 'x\n' * 1025]:
        edits = deepcopy(result['template_edits']); edits[0]['values'][0] = invalid
        assert template_issues(template, result['template_output'], edits)
    assert template_issues(template, result['template_output'], result['template_edits'][:-1])
    assert template_issues(template, result['template_output'], result['template_edits'] + result['template_edits'][:1])
    client.app.state.stage_workflows.executor=lambda ctx:{'artifact':deepcopy(result)}
    run=ok(action(client,owner,run,'gen','execute',{'template':template}))
    assert run['stages'][0]['status']=='review'
    path=ROOT+'/runs/'+run['id']+'/stages/brief/template-export'
    query={**owner,'stage_session_id':run['stages'][0]['stage_session_id'],'approval_sha256':'0'*64}
    assert client.get(path,params=query).status_code==404
    run=ok(action(client,owner,run,'approve','approve'))
    query['approval_sha256']=run['stages'][0]['approval_sha256']
    response=client.get(path,params=query)
    assert response.status_code==200 and response.headers['content-type'].endswith('presentationml.presentation')
    assert Presentation(BytesIO(response.content)).slides[0].shapes[0].text == '项目：中文项目 / v2'
    assert client.get(path,params={**query,'format':'text'}).content.decode()==result['template_output']
    assert client.get(path,params={**query,'approval_sha256':'1'*64}).status_code==404
    assert client.get(path,params={**query,'project_id':scope(client,'other')['project_id']}).status_code==404


def test_table_header_identifies_an_empty_cell_and_unnamed_cells_remain_static():
    ppt = Presentation()
    slide = ppt.slides.add_slide(ppt.slide_layouts[6])
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(3)).table
    table.cell(0, 0).text = '结果'
    data = BytesIO(); ppt.save(data); raw = data.getvalue()
    template = parsed(raw)
    target, = template['edit_targets']
    assert target['columns'] == ['结果'] and not target['required']
    result = {'template_output':'已验收', 'template_edits':[{'target':target['target'], 'values':['已验收']}]}
    written = Presentation(BytesIO(export_stage_template(template, result, raw)[2])).slides[0].shapes[0].table
    assert [[written.cell(r,c).text for c in (0,1)] for r in (0,1)] == [['结果',''],['已验收','']]


def test_old_slide_run_with_text_only_template_and_no_stored_package(client):
    owner=scope(client);define(client,owner,[node('brief')]);run=start(client,owner)
    manager = client.app.state.stage_workflows
    prior = parse_stage_template('历史标题.pptx', deck('static'))
    prior['export_extension'] = '.txt'
    template = manager.save_template(owner['project_id'], owner['conversation_id'], prior)
    manager.executor = lambda ctx: {'artifact': {'summary':'历史结果', 'items':[{'id':'X','title':'结果','description':'审核通过'}],
                                                'template_output':'旧运行已经审核的内容'}}
    run=ok(action(client,owner,run,'gen','execute',{'template':template}))
    run=ok(action(client,owner,run,'approve','approve'));stage=run['stages'][0]
    path=ROOT+'/runs/'+run['id']+'/stages/brief/template-export'
    query={**owner,'stage_session_id':stage['stage_session_id'],'approval_sha256':stage['approval_sha256']}
    for query_format in ({}, {'format':'text'}):
        response=client.get(path,params={**query,**query_format})
        assert response.status_code==200 and response.content.decode()=='旧运行已经审核的内容'
        assert response.headers['content-type'].startswith('text/plain')
