"""Source34 break opportunities under long data and inherited template styling."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json

from docx import Document
from docx.oxml.ns import qn
import pytest

from packages.domain.local_workspaces import canonical
from packages.domain.planning_docx import _table,render_docx
from test_source34_report_semantics import source,selected_template,body,check


def confirmed_discovery(snapshot):
    primary=snapshot.pop('proposal');candidate=primary['payload']['candidate']
    snapshot.update(source_kind='DISCOVERY',discovery={
        **{k:primary[k]for k in ('id','revision','sha256','answers')},
        'readiness':{'review_status':'CURRENT'},'payload':{'status':'CONFIRMED',
        'source_receipt':primary['payload']['source_receipt'],'resolutions':[],
        'candidate':{'title':candidate['title'],'summary':candidate['summary'],'phase':'DEFINE',
        'claims':[{'id':'R01','category':'REQUIREMENT','text':'要求已接受；具体展开仍待审查。',
                   'origin':'USER_STATEMENT','acceptance':[],'source_anchors':[]}],
        'competitors':[],'next_actions':[]}}})
    snapshot['pending']['session_ref']=snapshot['pending'].pop('proposal_ref')
    question=snapshot['pending']['questions'][0]
    question['affects_claim_ids']=['R01'];question.pop('affects_node_ids')
    return snapshot


@pytest.mark.parametrize('source_kind',['PROPOSAL','DISCOVERY'])
def test_sixteen_thousand_character_multiline_question_is_complete_and_has_page_break_opportunities(source_kind,tmp_path):
    snapshot=source()
    if source_kind=='DISCOVERY':snapshot=confirmed_discovery(snapshot)
    text=('这段长问题必须逐字保留，不能串成整页以外的不可分段落链。\n\n'*1000)[:15999]+'终'
    check(len(text)==16000)
    snapshot['pending']['questions'][0]['text']=text
    before=deepcopy(snapshot);serialized_before=canonical(snapshot)
    raw,audit=render_docx(snapshot);document=Document(BytesIO(raw));shown=body(document)
    check(snapshot==before and canonical(snapshot)==serialized_before,'Presentation cannot rewrite long question, answers, constraints or acceptance')
    check(text in shown,'All 16,000 characters and internal blank lines remain editable body text')
    headings=[(i,p)for i,p in enumerate(document.paragraphs)if p.text.startswith('3.1  ')]
    check(len(headings)==1 and len(headings[0][1].text)<=32,'The navigation label stays short without truncating body prose')
    index=headings[0][0];status=next(i for i in range(index+1,len(document.paragraphs))if document.paragraphs[i].text=='状态 · 3.1：已记录回答，待接受')
    complete_body=document.paragraphs[index+1:status]
    check(len(complete_body)>100,'The fixture exercises a real multi-page chain rather than one short paragraph')
    check(all(p.paragraph_format.keep_with_next is False for p in complete_body[:-1]),'The long question interior must expose page-break opportunities even between blank lines; only the final short line may stay with its status')
    primary=snapshot['discovery'] if source_kind=='DISCOVERY' else snapshot['proposal']
    check(shown.count(primary['payload']['candidate']['title'])==1,'The generic report role cannot replace the business subject')
    check('正文另标待审的方案，不因确认标签或导出而自动获接受。'in shown)
    check('已记录回答不能冒作已接受。'in shown)
    check(audit['content_coverage']==[] and len(audit['historical_records'])==1)
    (tmp_path/('long-question-'+source_kind+'.docx')).write_bytes(raw)


def test_generated_table_body_overrides_normal_keep_with_next_without_restyling_human_content(tmp_path):
    document=Document();document.styles['Normal'].paragraph_format.keep_with_next=True
    document.styles['Normal'].paragraph_format.keep_together=True
    document.add_paragraph('人工段落保留模板继承')
    _table(document,['编号','完整说明'],[[str(i),'当前业务完整记录 '+str(i)]for i in range(12)],[.2,.8])
    _table(document,['来源','位置'],[['真实来源','全文另页仍可查']],[.3,.7])
    output=BytesIO();document.save(output);document=Document(BytesIO(output.getvalue()))
    check(document.styles['Normal'].paragraph_format.keep_with_next is True,'A generated table cannot change the original Normal style')
    check(document.styles['Normal'].paragraph_format.keep_together is True)
    check(document.paragraphs[0].paragraph_format.keep_with_next is None,'Human template prose keeps its original inherited formatting')
    for table in document.tables:
        check(table.rows[0]._tr.trPr.find(qn('w:tblHeader'))is not None,'Generated headers keep their repeat instruction')
        check(all(p.paragraph_format.keep_with_next is True for cell in table.rows[0].cells for p in cell.paragraphs),'Headers remain attached to their first body row')
        check(all(p.paragraph_format.keep_with_next is False for row in table.rows[1:]for cell in row.cells for p in cell.paragraphs),'Body rows need explicit false, not None inheriting true from Normal')
        check(all(p.paragraph_format.keep_together is False for row in table.rows[1:]for cell in row.cells for p in cell.paragraphs),'Long table prose overrides inherited keep-together')
    tables=document.element.body.findall(qn('w:tbl'))
    between=list(document.element.body)[list(document.element.body).index(tables[0])+1:list(document.element.body).index(tables[1])]
    check(any(block.tag==qn('w:p')for block in between),'Two generated tables retain a real separating paragraph')
    (tmp_path/'normal-keep-next-generated-tables.docx').write_bytes(output.getvalue())


def test_explicit_template_history_ownership_stays_exact_with_inherited_normal_keep_next(tmp_path):
    snapshot=source();before=deepcopy(snapshot)
    original=Document(BytesIO(selected_template('title','summary','proposal_ref','source_receipt','report_sections')))
    original.styles['Normal'].paragraph_format.keep_with_next=True
    original.add_comment(original.paragraphs[0].runs[0],text='人工模板批注不属于生成区域',author='QA')
    output=BytesIO();original.save(output);original_raw=output.getvalue()
    raw,audit=render_docx(snapshot,original_raw);document=Document(BytesIO(raw));text=body(document)
    check(snapshot==before)
    summary=snapshot['proposal']['payload']['candidate']['summary']
    check(text.count('唯一历史边界：不把资料情境称为实测。')==1)
    check(text.index('人工位置：title之后')<text.index('唯一历史边界')<text.index('人工位置：summary之后'))
    check(canonical(snapshot['pending']['proposal_ref'])in text)
    check(canonical(snapshot['proposal']['payload']['source_receipt'])in text)
    check(audit['historical_records']==[],'Explicitly rendered title/summary must not also be listed as omitted history')
    coverage=[item for item in audit['content_coverage']if item['source_key']=='candidate.summary']
    check(len(coverage)==1 and coverage[0]['source_sha256']==hashlib.sha256(summary.encode()).hexdigest())
    check('本版状态：已确认计划（CONFIRMED）'in text and '本次详细展开仍待方案审查。'in text)
    check(document.styles['Normal'].paragraph_format.keep_with_next is True)
    from zipfile import ZipFile
    with ZipFile(BytesIO(original_raw))as a,ZipFile(BytesIO(raw))as b:
        for part in ('word/header1.xml','word/footer1.xml','word/comments.xml'):check(a.read(part)==b.read(part))
    for table in document.tables:
        for row in table.rows[1:]:
            check(all(p.paragraph_format.keep_with_next is False for cell in row.cells for p in cell.paragraphs),'The full export path overrides inherited keep-next only on generated table body paragraphs')
    (tmp_path/'explicit-template-original-ownership.docx').write_bytes(raw)
