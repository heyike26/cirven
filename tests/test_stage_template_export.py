from copy import deepcopy
from io import BytesIO
from zipfile import ZIP_DEFLATED,ZipFile

import pytest
from docx import Document
from lxml import etree as ET
import xlsxwriter

from packages.dynamic_workflow.stage_template_export import W,X,describe_template,export_stage_template
from packages.dynamic_workflow.stage_templates import parse_stage_template
from test_stage_workflow_runs import scope, start, action, define, node, ok, ROOT


def _as_office_template(raw, old, new):
    out=BytesIO()
    with ZipFile(BytesIO(raw)) as src,ZipFile(out,'w',compression=ZIP_DEFLATED) as dest:
        for part in src.infolist():
            data=src.read(part)
            if part.filename=='[Content_Types].xml':data=data.replace(old,new)
            dest.writestr(part,data)
    return out.getvalue()


def _word():
    doc=Document()
    doc.sections[0].header.paragraphs[0].text='固定页眉'
    doc.sections[0].footer.paragraphs[0].text='内部审核'
    doc.add_heading('需求清单',1)
    p=doc.add_paragraph()
    p.add_run('项目：')
    emphasis=p.add_run('{{项');emphasis.bold=True
    p.add_run('目}} / ')
    p.add_run('{{版本}}').italic=True
    table=doc.add_table(rows=2,cols=2);table.style='Table Grid'
    table.rows[0].cells[0].text='编号';table.rows[0].cells[1].text='内容'
    table.rows[1].cells[0].text='{{ID}}';table.rows[1].cells[1].text='需求 {{内容}}'
    out=BytesIO();doc.save(out);return out.getvalue()


def _excel():
    out=BytesIO()
    book=xlsxwriter.Workbook(out,{'in_memory':True})
    style=book.add_format({'bold':True,'bg_color':'#DDDDEE'})
    data=book.add_format({'text_wrap':True,'font_color':'#445566'})
    sheet=book.add_worksheet('需求')
    sheet.set_column('A:A',17);sheet.write_row('A1',['编号','验收'],style)
    sheet.write_row('A2',['{{ID}}','{{验收标准}}'],data)
    static=book.add_worksheet('统计');static.write('A1','现有公式');static.write_formula('A2','=1+1',None,2)
    book.close();return out.getvalue()


def parsed(name,content):
    return describe_template(parse_stage_template(name,content),content)


def _parts(raw):
    with ZipFile(BytesIO(raw)) as z:return {n:z.read(n) for n in z.namelist()}


@pytest.mark.parametrize('suffix',['docx','dotx'])
def test_word_preserves_original_parts_run_styles_and_repeated_table(suffix):
    raw=_word()
    if suffix=='dotx':raw=_as_office_template(raw,b'wordprocessingml.document.main+xml',b'wordprocessingml.template.main+xml')
    template=parsed('测试.'+suffix,raw)
    assert [s['columns'] for s in template['edit_targets']]==[['项目','版本'],['ID','内容']]
    para,row=template['edit_targets']
    result={'template_output':'需求清单\n项目：本机 / v2\n编号 内容','template_edits':[
        {'target':para['target'],'values':['本机','v2']},
        {'target':row['target'],'values':['OR-1','第一条']},
        {'target':row['target'],'values':['OR-2','第二条\n补充']}]}
    name,mime,data=export_stage_template(template,result,raw)
    assert name=='测试-已审核.docx' and 'wordprocessingml.document' in mime
    old,new=_parts(raw),_parts(data)
    assert old.keys()==new.keys()
    assert {k for k in old if old[k]!=new[k]}<=({'word/document.xml','[Content_Types].xml'} if suffix=='dotx' else {'word/document.xml'})
    saved=Document(BytesIO(data))
    assert saved.sections[0].header.paragraphs[0].text=='固定页眉'
    assert saved.sections[0].footer.paragraphs[0].text=='内部审核'
    assert saved.paragraphs[0].style.name=='Heading 1'
    assert saved.paragraphs[1].text=='项目：本机 / v2'
    assert any(r.bold and r.text=='本机' for r in saved.paragraphs[1].runs)
    assert any(r.italic and r.text=='v2' for r in saved.paragraphs[1].runs)
    assert [[c.text for c in r.cells] for r in saved.tables[0].rows]==[['编号','内容'],['OR-1','需求 第一条'],['OR-2','需求 第二条\n补充']]
    assert saved.tables[0].style.name=='Table Grid'
    assert export_stage_template(template,result,raw)[2]==data


@pytest.mark.parametrize('suffix',['xlsx','xltx'])
def test_excel_repeated_fields_preserve_styles_formulas_unedited_sheets(suffix):
    raw=_excel()
    if suffix=='xltx':raw=_as_office_template(raw,b'spreadsheetml.sheet.main+xml',b'spreadsheetml.template.main+xml')
    template=parsed('样表.'+suffix,raw);slot=template['edit_targets'][0]
    assert slot['repeatable'] and slot['columns']==['需求 · ID','需求 · 验收标准']
    artifact={'template_output':'编号 验收\nOR-1 按要求保存','template_edits':[
        {'target':slot['target'],'values':['OR-1','按要求保存']},{'target':slot['target'],'values':['OR-2','=literal_text']}]}
    name,media,data=export_stage_template(template,artifact,raw)
    assert name.endswith('.xlsx')
    old,new=_parts(raw),_parts(data)
    assert {k for k in old if old[k]!=new[k]}<=({'xl/worksheets/sheet1.xml','[Content_Types].xml'} if suffix=='xltx' else {'xl/worksheets/sheet1.xml'})
    root=ET.fromstring(new['xl/worksheets/sheet1.xml'])
    cells={c.get('r'):c for c in root.iter(X+'c')}
    assert cells['A2'].get('s')==cells['A3'].get('s') and cells['A3'].findtext(X+'is/'+X+'t')=='OR-2'
    assert cells['B3'].get('t')=='inlineStr' and cells['B3'].findtext(X+'is/'+X+'t')=='=literal_text'
    assert root.find(X+'dimension').get('ref')=='A1:B3'
    assert 'OR-2' in parse_stage_template(name,data)['text']


def test_text_export_matches_format_and_honest_fallback():
    for name,source,text in [('要求.csv','列,需求\n','列,需求\r\n一,运行\r\n'),('要求.tsv','列\t需求\n','列\t需求\n一\t运行'),
                             ('要求.md','# 目标\n内容',' # 目标\n\n成果'),('要求.json','{"goal":"current"}','{"goal":"approved"}'),
                             ('要求.other','目标与内容','已审核正文')]:
        raw=source.encode('utf-8-sig') if name.endswith('.csv') else source.encode()
        template=parsed(name,raw)
        filename,_,data=export_stage_template(template,{'template_output':text},raw)
        assert data.decode('utf-8-sig')==text
        assert filename.endswith('.txt' if name.endswith('.other') else name[name.rindex('.'):])
    template=parsed('要求.docx',_word())
    with pytest.raises(ValueError):export_stage_template(template,{'template_output':'未映射'},_word())
    with pytest.raises(ValueError):export_stage_template(template,{'template_output':'旧值','template_edits':[{'target':'bogus','values':['x']}]},_word())


def test_json_template_keeps_order_types_and_repeatable_requirements():
    raw = '{"需求":[{"名称":"示例","级别":1}],"范围":[]}'.encode('utf-16')
    template = parsed('原件.json', raw)
    output = '{"需求":[{"名称":"登录","级别":2},{"名称":"退出","级别":1}],"范围":["桌面"]}'
    name, media, data = export_stage_template(template, {'template_output': output}, raw)
    assert name.endswith('.json') and media == 'application/json' and data.decode('utf-16') == output
    for invalid in [output.replace('"级别":2', '"级别":true'), output.replace('"范围"', '"额外"'),
                    output.replace('"名称":"登录","级别":2', '"级别":2,"名称":"登录"'),
                    output.replace(',"范围"', ',"范围":[],"范围"'), '[]', 'NaN']:
        with pytest.raises(ValueError):
            export_stage_template(template, {'template_output': invalid}, raw)
    with pytest.raises(ValueError):
        parsed('损坏.json', b'{"x":1,"x":2}')


def test_template_download_requires_exact_approved_record_and_reopens_word(client):
    owner=scope(client);define(client,owner,[node('brief')]);run=start(client,owner)
    raw=_word();template=ok(client.post(ROOT+'/template',data=owner,files={'file':('需求.docx',raw)}))
    para,row=template['edit_targets']
    artifact={'summary':'阶段文稿','items':[{'id':'X','title':'结论','description':'已确认'}],
              'template_output':'需求清单\n已按要求填写。','template_edits':[{'target':para['target'],'values':['本机','v1']},{'target':row['target'],'values':['1','验收']}]}
    client.app.state.stage_workflows.executor=lambda ctx:{'artifact':deepcopy(artifact)}
    run=ok(action(client,owner,run,'generate','execute',{'template':template}))
    assert run['stages'][0]['status']=='review'
    path=ROOT+'/runs/'+run['id']+'/stages/brief/template-export'
    query={**owner,'stage_session_id':run['stages'][0]['stage_session_id'],'approval_sha256':'0'*64}
    assert client.get(path,params=query).status_code==404
    run=ok(action(client,owner,run,'release','approve'))
    query['approval_sha256']=run['stages'][0]['approval_sha256']
    result=client.get(path,params=query)
    assert result.status_code==200,result.text[:400]
    assert 'filename*=' in result.headers['content-disposition']
    assert Document(BytesIO(result.content)).tables[0].rows[1].cells[1].text=='需求 验收'
    assert client.get(path,params={**query,'project_id':scope(client,'other')['project_id']}).status_code==404
    assert client.get(path,params={**query,'format':'text'}).content.decode()=='需求清单\n已按要求填写。'
