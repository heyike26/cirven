"""Default business reading and unchanged, discoverable historical originals."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
import pytest

from packages.domain.local_workspaces import canonical
from packages.domain.planning_docx import render_docx,report_text
from packages.domain.planning_schema import PlanningCandidate


HISTORY='生成时尚未接受；唯一历史边界：不把资料情境称为实测。\n\n文档 77f7555b-2da5-4eeb-b4d1-13c4193f7f00，raw_sha256='+'d'*64+'；nodes[].depends_on。\n\n原文 {"business":"不可篡改"}。'


def check(value,message='Source34 reading acceptance failed'):
    if not value:raise AssertionError(message)


def source():
    candidate=PlanningCandidate.model_validate({'title':'历史标题（当时待审）','summary':HISTORY,
        'goals':['形成当前业务交接稿'],'constraints':['本次详细展开仍待方案审查。'],
        'risks':[],'assumptions':[],
        'nodes':[{'id':'T01','title':'核对工单','description':'用户业务原文 {"keep":"不能当协议清洗"}。',
                  'inputs':['原输入'],'outputs':['可审阅稿'],'acceptance':['原文完整'],'origin':'MODEL_SUGGESTION'}],
        'questions':[]}).model_dump()
    receipt={'sha256':'a'*64,'documents':[],'coverage':[]}
    ref={'id':'proposal_fixture','revision':5,'sha256':'b'*64}
    question={'id':'Q01','text':'请选择最终语言。','reason':'用户尚未选择。','status':'ANSWERED_PENDING_ACCEPTANCE',
        'blocking':False,'affects_node_ids':['T01'],'requested_materials':[],
        'answers':[{'text':'已记录回答不能冒作已接受。'}],'resolution':None,'source_anchors':[]}
    return {'source_kind':'PROPOSAL','project_id':'fixture','proposal':{**ref,
        'payload':{'candidate':candidate,'status':'CONFIRMED','source_receipt':receipt},
        'review_status':'CURRENT','flow':{'nodes':[{'id':'T01','planning_status':'ACCEPTED_PLAN'}]},'answers':[]},
        'pending':{'proposal_ref':ref,'pending_count':1,'questions':[question],'sha256':'c'*64},
        'pending_markdown':'Q01仍待明确接受。','template_ref':None,'experience':None,'discovery':None,'diagrams':[]}


def body(document):
    return '\n'.join(''.join(t.text or '' for t in p.iter(qn('w:t'))) for p in document.element.body.iter(qn('w:p')))


def selected_template(*slots,cell=False):
    document=Document();document.styles['Normal'].font.name='Cambria';document.styles['Normal'].font.size=Pt(12)
    document.sections[0].header.paragraphs[0].text='人工页眉保持'
    document.sections[0].footer.paragraphs[0].text='人工页脚保持'
    document.add_paragraph('人工开头（不要移动进来源附录）')
    for slot in slots:
        p=document.add_table(rows=1,cols=1).cell(0,0).paragraphs[0] if cell else document.add_paragraph()
        p.text='{{ '+slot+' }}'
        document.add_paragraph('人工位置：'+slot+'之后')
    document.add_paragraph('人工结束')
    out=BytesIO();document.save(out);return out.getvalue()


@pytest.mark.parametrize('template',[None,'empty','sections'])
def test_default_word_preserves_business_title_and_keeps_summary_in_exact_source_snapshot(template):
    snapshot=source();before=deepcopy(snapshot)
    selected=None if template is None else selected_template(*(['report_sections'] if template=='sections' else []))
    raw,audit=render_docx(snapshot,selected);document=Document(BytesIO(raw));text=body(document)
    check(snapshot==before,'Historical text and all source/state data are immutable')
    check(HISTORY not in text and 'raw_sha256'not in text and 'nodes[].depends_on'not in text)
    check(text.count('历史标题（当时待审）')==1 and '主题（题名原文）：历史标题（当时待审）'in text,'The role heading retains one exact business subject without treating its wording as confirmation')
    check('用户业务原文 {"keep":"不能当协议清洗"}。'in text,'Business content is never stripped by JSON patterns')
    check('本次详细展开仍待方案审查。'in text,'Confirmed label must preserve the current constraint scope')
    check('本版状态：已确认计划（CONFIRMED）'in text and '当前未决 1 项'in text)
    check('已记录回答不能冒作已接受。'in text and '尚未接受答案或假设。'in text)
    check('正文另标待审的方案，不因确认标签或导出而自动获接受。'in text)
    check('生成来源快照'in text,'The Word report provides a visible recovery cue')
    check(audit['content_coverage']==[],'Omitted history must not be falsely reported as rendered')
    check(len(audit['historical_records'])==1 and audit['historical_records'][0]['source_snapshot_pointer']=='/proposal/payload/candidate/summary')
    stored=json.loads((canonical(snapshot)+'\n').encode())
    for item in audit['historical_records']:
        value=stored
        for component in item['source_snapshot_pointer'].split('/')[1:]:value=value[component]
        check(hashlib.sha256(value.encode()).hexdigest()==item['sha256'])
        check(len(value.encode())==item['byte_length'] and item['preserved_in']=='source_snapshot')


def test_explicit_template_owns_exact_raw_values_and_original_position():
    snapshot=source();before=deepcopy(snapshot)
    original=selected_template('title','summary','proposal_ref','source_receipt','report_sections')
    raw,audit=render_docx(snapshot,original);document=Document(BytesIO(raw));text=body(document)
    check(snapshot==before)
    check(text.count('历史标题（当时待审）')==1 and '主题（题名原文）：'not in text)
    check(text.count('唯一历史边界：不把资料情境称为实测。')==1)
    check('raw_sha256='+'d'*64 in text and 'nodes[].depends_on'in text,'Explicit raw text is not sanitized')
    check(text.index('人工位置：title之后')<text.index('唯一历史边界')<text.index('人工位置：summary之后'))
    check(canonical(snapshot['pending']['proposal_ref'])in text)
    check(canonical(snapshot['proposal']['payload']['source_receipt'])in text)
    check(len(audit['content_coverage'])==1 and audit['content_coverage'][0]['coverage']=='ALL_SOURCE_LINES_ACCOUNTED')
    check(audit['content_coverage'][0]['source_sha256']==hashlib.sha256(HISTORY.encode()).hexdigest())
    check(audit['historical_records']==[],'Explicit title/summary are recorded only where rendered, not duplicated as omitted history')
    check(document.sections[0].header.paragraphs[0].text=='人工页眉保持')
    check(document.sections[0].footer.paragraphs[0].text=='人工页脚保持')
    check(document.styles['Normal'].font.name=='Cambria' and document.styles['Normal'].font.size==Pt(12))


def test_report_sections_in_table_preserve_selected_location_without_forcing_a_page_break():
    snapshot=source();original=selected_template('report_sections',cell=True)
    raw,audit=render_docx(snapshot,original);document=Document(BytesIO(raw));text=body(document)
    check('核对工单'in document.tables[0].cell(0,0).text)
    check(text.index('核对工单')<text.index('人工位置：report_sections之后'))
    appendix=next(p for p in document.tables[0].cell(0,0).paragraphs if p.text=='可追溯附录')
    check(appendix._p.getparent().tag==qn('w:tc'),'Sources stay within the selected table slot')
    for cell in document.element.body.iter(qn('w:tc')):check(cell[-1].tag==qn('w:p'))
    check(audit['content_coverage']==[])


def test_actual_user_filename_with_uuid_is_preserved_while_history_is_omitted():
    snapshot=source();name='用户文件-77f7555b-2da5-4eeb-b4d1-13c4193f7f00.json'
    receipt=snapshot['proposal']['payload']['source_receipt']
    receipt['documents']=[{'document_id':'internal_source','revision':2,'filename':name,'source':'local-workspace:fixed-source'}]
    receipt['coverage']=[{'document_id':'internal_source','text_coverage':'FULL_PARSED_TEXT','omitted_chunk_indexes':[],'warnings':[]}]
    raw,_=render_docx(snapshot);text=body(Document(BytesIO(raw)))
    check(name in text,'A user filename cannot be classified as a technical leak by UUID suffix')
    check('raw_sha256'not in text)


def test_secondary_discovery_history_has_its_own_exact_snapshot_location():
    snapshot=source();snapshot['discovery']={'revision':3,'payload':{'status':'CONFIRMED','candidate':{
        'title':'旧需求标题（当时未接受）','summary':'旧需求原文 {"keep":"独有信息"}。',
        'phase':'DEFINE','claims':[],'competitors':[],'next_actions':[]}}}
    raw,audit=render_docx(snapshot);text=body(Document(BytesIO(raw)))
    check('旧需求原文'not in text and '旧需求标题'not in text)
    records=audit['historical_records']
    check(len(records)==3 and len({r['source_snapshot_pointer'] for r in records})==3)
    check(all(r['preserved_in']=='source_snapshot' for r in records))


def test_report_text_uses_the_same_business_projection_without_rewriting_source():
    snapshot=source();before=deepcopy(snapshot);text=report_text(snapshot)
    check(snapshot==before)
    check('raw_sha256'not in text and text.count('历史标题（当时待审）')==1)
    check('用户业务原文 {"keep":"不能当协议清洗"}。'in text)
    check('本次详细展开仍待方案审查。'in text and '生成来源快照'in text)


def test_discovery_source_kind_keeps_claims_answers_and_confirmed_scope_without_promoting_history():
    snapshot=source();primary=snapshot.pop('proposal');candidate=primary['payload']['candidate']
    discovery={**{k:primary[k]for k in ('id','revision','sha256','answers')},
        'readiness':{'review_status':'CURRENT'},'payload':{'status':'CONFIRMED',
        'source_receipt':primary['payload']['source_receipt'],'resolutions':[],
        'candidate':{'title':candidate['title'],'summary':candidate['summary'],'phase':'DEFINE',
        'claims':[{'id':'R01','category':'REQUIREMENT','text':'详细方案仍须单独审查；人工原话不能丢。',
                   'origin':'USER_STATEMENT','acceptance':['以明确接受范围为准。'],'source_anchors':[]}],
        'competitors':[],'next_actions':[]}}}
    snapshot.update(source_kind='DISCOVERY',discovery=discovery)
    snapshot['pending']['session_ref']=snapshot['pending'].pop('proposal_ref')
    q=snapshot['pending']['questions'][0];q['affects_claim_ids']=['R01'];q.pop('affects_node_ids')
    before=deepcopy(snapshot);raw,audit=render_docx(snapshot);text=body(Document(BytesIO(raw)))
    check(snapshot==before)
    check('本版状态：已确认需求发现（CONFIRMED）'in text)
    check('详细方案仍须单独审查；人工原话不能丢。'in text and '已记录回答不能冒作已接受。'in text)
    check('raw_sha256'not in text and text.count('历史标题（当时待审）')==1)
    check(len(audit['historical_records'])==1 and audit['content_coverage']==[])
    check(all(r['source_snapshot_pointer'].startswith('/discovery/')for r in audit['historical_records']))


def test_reviewed_template_chapter_mapping_keeps_business_data_and_human_cover_without_history_leak():
    snapshot=source();document=Document();document.add_paragraph('人工封面必须原位保留')
    document.sections[0].header.paragraphs[0].text='人工章节页眉'
    document.add_comment(document.paragraphs[0].runs[0],text='封面人工批注',author='QA')
    template_ref={'path':'人工模板.docx','revision':2,'sha256':'f'*64}
    rules=[]
    for key,heading in [('QUESTIONS','原问题章'),('OVERVIEW','原总览章'),('SOURCES','原来源章'),('TASKS','原任务章')]:
        document.add_heading(heading,level=1);index=len(document.paragraphs)
        document.add_paragraph('已明确审阅的旧模板章内容')
        rules.append({'section_key':key,'heading':'采用'+heading,'style_id':'Heading1',
            'source_anchors':[{'part':'word/document.xml','source_index':0,'path':'/paragraph['+str(index)+']',
                               'quote_sha256':hashlib.sha256(heading.encode()).hexdigest()}]})
    snapshot.update(template_ref=template_ref,experience={'approved_rules':rules,
        'source_refs':[template_ref],'instructions':['只采用已审阅章节映射。']})
    out=BytesIO();document.save(out);original=out.getvalue();before=deepcopy(snapshot)
    raw,audit=render_docx(snapshot,original);document=Document(BytesIO(raw));text=body(document)
    check(snapshot==before and audit['template_mode']=='PROFILE_SECTION_MAPPING')
    check(text.count('人工封面必须原位保留')==1)
    check(sum(p.text=='2.1  核对工单' for p in document.paragraphs)==1,
          'The task itself occurs once; its title can recur in contextual subsection labels')
    check(text.index('采用原问题章')<text.index('采用原总览章')<text.index('采用原来源章')<text.index('采用原任务章'))
    check('本次详细展开仍待方案审查。'in text and '已记录回答不能冒作已接受。'in text)
    check('raw_sha256'not in text and text.count('历史标题（当时待审）')==1)
    check(audit['content_coverage']==[] and len(audit['historical_records'])==1)
    from zipfile import ZipFile
    with ZipFile(BytesIO(original))as a,ZipFile(BytesIO(raw))as b:
        for part in ('word/header1.xml','word/comments.xml'):check(a.read(part)==b.read(part))
