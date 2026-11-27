"""Actual DOCX renderer/template tests, deliberately without HTTP or model calls."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm,Pt

from packages.domain.planning_docx import render_docx
from packages.domain.planning_schema import PlanningCandidate
from packages.domain.store import InvalidInput


def check(value, message='Word document acceptance failed'):
    if not value: raise AssertionError(message)


def snapshot(summary='## 小节一\n\n完整正文42。\n\n## 小节二\n\n- 第一项\n- 第二项'):
    candidate=PlanningCandidate.model_validate({'title':'原创QA计划','summary':summary,
        'goals':['保留原内容'],'constraints':['原件不可覆盖'],'risks':[],'assumptions':[],
        'nodes':[{'id':'T01','title':'核对交付','description':'逐项检查原输入。','inputs':['原输入'],
                  'outputs':['新副本'],'acceptance':['可审阅'],'origin':'MODEL_SUGGESTION'}], 'questions':[]}).model_dump()
    payload={'candidate':candidate,'status':'DRAFT','source_receipt':{'documents':[],'coverage':[],'sha256':'a'*64}}
    ref={'id':'proposal_fixture','revision':1,'sha256':'b'*64}
    proposal={**ref,'payload':payload,'review_status':'CURRENT','flow':{'nodes':[{'id':'T01','planning_status':'DRAFT'}]},'answers':[]}
    return {'source_kind':'PROPOSAL','proposal':proposal,'project_id':'project_fixture',
        'pending':{'proposal_ref':ref,'questions':[],'pending_count':0,'sha256':'c'*64},
        'pending_markdown':'没有登记问题。','template_ref':None,'experience':None,'discovery':None,'diagrams':[]}


def template(slots=('{{ title }}','{{ summary }}','{{ report_sections }}'), *, cell=False, split=False):
    document=Document();document.styles['Normal'].font.name='Cambria';document.styles['Normal'].font.size=Pt(12)
    document.sections[0].left_margin=Mm(26);document.sections[0].right_margin=Mm(24)
    document.sections[0].header.paragraphs[0].text='原始页眉 · QA'
    document.sections[0].footer.paragraphs[0].text='原始页脚 · QA'
    fixed=document.add_paragraph('固定文字不得删除');fixed.runs[0].italic=True
    for text in slots:
        p=document.add_table(rows=1,cols=1).cell(0,0).paragraphs[0]if cell else document.add_paragraph()
        p.paragraph_format.space_after=Pt(7)
        if split and text=='{{ summary }}':
            for part in ['{{ sum','mary }}']:p.add_run(part).font.size=Pt(12)
        else:p.add_run(text).font.size=Pt(12)
    out=BytesIO();document.save(out);return out.getvalue()


def body_text(document):
    return '\n'.join(
        ''.join(e.text or ''for e in p.iter(qn('w:t')))for p in document.element.body.iter(qn('w:p')))


@pytest.mark.parametrize(('status','review'), [('DRAFT','CURRENT'),('DRAFT','NEEDS_REVIEW'),('CONFIRMED','NEEDS_REVIEW')])
def test_template_front_keeps_critical_status_without_displacing_user_title(status,review):
    source=snapshot();source['proposal']['payload']['status']=status;source['proposal']['review_status']=review
    before=deepcopy(source);original=template()
    raw,_=render_docx(source,original);document=Document(BytesIO(raw))
    paragraphs=[p.text for p in document.paragraphs if p.text]
    prefix=paragraphs[:paragraphs.index('固定文字不得删除')]
    check(len(prefix)==2,'Only critical current state and authorization scope precede the user template')
    check(('（DRAFT）'if status=='DRAFT'else'（CONFIRMED）')in prefix[0])
    check(('来源已变化，需复核'if review=='NEEDS_REVIEW'else'来源版本一致')in prefix[0])
    check('执行授权或独立验收已经通过'in prefix[1],'Confirmation must never imply execution authority')
    trace=body_text(document).split('\n可追溯附录\n',1)[1]
    for value in ['计划版本 1','任务 1 个；前置依赖 0 条。','登记问题 0 项；当前未决 0 项。','确认范围：']:
        check(value in trace,'Complete structured state remains in the existing appendix: '+value)
    check(source==before)
    check(body_text(document).count('原创QA计划')==1)
    with ZipFile(BytesIO(original))as a,ZipFile(BytesIO(raw))as b:
        for part in ['word/header1.xml','word/footer1.xml']:check(a.read(part)==b.read(part),part)


def test_actual_selected_guide_template_has_compact_state_and_unchanged_originals(tmp_path):
    source_path=os.environ.get('CIRVEN_WORD_FRONTMATTER_SNAPSHOT')
    template_path=os.environ.get('CIRVEN_WORD_FRONTMATTER_TEMPLATE')
    if not source_path or not template_path:pytest.skip('Exact original guide snapshot/template not supplied')
    source_raw=Path(source_path).read_bytes();template_raw=Path(template_path).read_bytes()
    check(hashlib.sha256(source_raw).hexdigest()=='ae7ee61e45dfb198414482b74251c612b98a6165065dc3f960d28cf8a72419e4')
    check(hashlib.sha256(template_raw).hexdigest()=='2ae94f3f3e07d39d7f0a2b31ab84716069024b5c60faa04e3f7646b6277c1cc2')
    source=json.loads(source_raw);before=deepcopy(source);candidate=source['discovery']['payload']['candidate']
    raw,audit=render_docx(source,template_raw);document=Document(BytesIO(raw));text=body_text(document)
    first_fixed=next(p.text for p in Document(BytesIO(template_raw)).paragraphs if p.text.strip()and'{{'not in p.text)
    paragraphs=[p.text for p in document.paragraphs if p.text]
    prefix=paragraphs[:paragraphs.index(first_fixed)]
    (tmp_path/'same-guide-template-frontmatter.docx').write_bytes(raw)
    (tmp_path/'CONTENT_RECEIPT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    check(len(prefix)==2,'Actual user template title must follow two critical state paragraphs, not six')
    check('DRAFT'in prefix[0]and'来源版本一致'in prefix[0])
    check('执行授权或独立验收已经通过'in prefix[1])
    check(source==before and text.count(candidate['title'])==1)
    summary=next(x for x in audit['content_coverage']if x['source_key']=='candidate.summary')
    check(summary['source_sha256']==hashlib.sha256(candidate['summary'].encode()).hexdigest())
    trace=text.split('\n可追溯附录\n',1)[1]
    check('需求发现版本 2'in trace and '登记问题 3 项；当前未决 3 项。'in trace and '确认范围：'in trace)
    for q in source['pending']['questions']:check(q['text']in text)
    with ZipFile(BytesIO(template_raw))as a,ZipFile(BytesIO(raw))as b:
        for part in ['word/header1.xml','word/footer1.xml']:
            if part in a.namelist():check(a.read(part)==b.read(part),part)


@pytest.mark.parametrize('text', ['需', '保留原始输入和人工补充，异常恢复后明确下一步。' * 35 + ' {"原文":"用户给出的JSON也须保留"}'], ids=['short','long-with-literal-json'])
def test_discovery_claims_remain_body_text_under_short_identifiable_headings(text):
    source=snapshot(); source['discovery']={'payload':{'candidate':{
        'phase':'DEFINE','summary':'保留完整生成时说明。','claims':[
            {'id':'R01','category':'REQUIREMENT','text':text,'origin':'USER_STATEMENT',
             'acceptance':['原输入完整且可继续处理。'],'source_anchors':[]}],
        'competitors':[],'next_actions':[]}}}
    before=deepcopy(source)
    raw,_=render_docx(source); document=Document(BytesIO(raw))
    matching=[p for p in document.paragraphs if p.text==text]
    check(source==before, 'Presentation must not rewrite the frozen source')
    check(len(matching)==1, 'The complete claim, including literal JSON, remains once')
    check(not matching[0].style.name.startswith('Heading'), 'Claim prose is readable body text at any length')
    headings=[p.text for p in document.paragraphs if p.style.name.startswith('Heading')]
    check('需求 · R01' in headings, 'Headings retain a short category and actual item identity')
    check('原输入完整且可继续处理。' in body_text(document), 'Acceptance is preserved separately')


@pytest.mark.parametrize('slot',['report_sections','report_body'])
def test_title_summary_have_one_owner_and_template_content_is_preserved(slot):
    source=snapshot();before=deepcopy(source);original=template(('{{ title }}','{{ summary }}','{{ '+slot+' }}'),split=True)
    raw,receipt=render_docx(source,original);document=Document(BytesIO(raw));text=body_text(document)
    check(source==before);check(text.count('原创QA计划')==1);check(text.count('小节一')==1)
    check(text.count('完整正文42。')==1);check(text.count('小节二')==1)
    check('## 'not in text);check('固定文字不得删除'in text)
    check('新副本'in text and '逐项检查原输入。'in text and '可审阅'in text)
    check(document.styles['Normal'].font.name=='Cambria');check(document.styles['Normal'].font.size==Pt(12))
    original_section=Document(BytesIO(original)).sections[0]
    check(document.sections[0].left_margin==original_section.left_margin)
    check(document.sections[0].right_margin==original_section.right_margin)
    check(document.sections[0].header.paragraphs[0].text=='原始页眉 · QA')
    check(document.sections[0].footer.paragraphs[0].text=='原始页脚 · QA')
    check(next(p for p in document.paragraphs if p.text=='固定文字不得删除').runs[0].italic)
    check(len([c for c in receipt['content_coverage']if c['source_key']=='candidate.summary'])==1)
    check(receipt['content_coverage'][0]['source_sha256']==hashlib.sha256(source['proposal']['payload']['candidate']['summary'].encode()).hexdigest())
    with ZipFile(BytesIO(original))as a,ZipFile(BytesIO(raw))as b:
        for part in ['word/header1.xml','word/footer1.xml']:check(a.read(part)==b.read(part),part)


def test_summary_only_template_appends_remaining_report_without_repeating_summary():
    raw,_=render_docx(snapshot(),template(('{{ summary }}',)));document=Document(BytesIO(raw));text=body_text(document)
    check(text.count('完整正文42。')==1);check(text.count('原创QA计划')==1)
    check('核对交付'in text)


def test_no_summary_slot_and_builtin_keep_history_in_exact_source_snapshot():
    for raw_template in [None,template(('{{ report_sections }}',)),template(())]:
        raw,receipt=render_docx(snapshot(),raw_template);document=Document(BytesIO(raw));text=body_text(document)
        check(text.count('完整正文42。')==0);check('## 'not in text)
        check(receipt['content_coverage']==[] and text.count('原创QA计划')==1)
        check(len(receipt['historical_records'])==1)
        records={r['source_snapshot_pointer']:r for r in receipt['historical_records']}
        summary=snapshot()['proposal']['payload']['candidate']['summary']
        check(records['/proposal/payload/candidate/summary']['sha256']==hashlib.sha256(summary.encode()).hexdigest())
        check(records['/proposal/payload/candidate/summary']['byte_length']==len(summary.encode()))
        check('生成来源快照'in text)


def test_paragraphs_and_lists_inside_table_summary_slot_are_supported():
    original=template(('{{ summary }}','{{ report_sections }}'),cell=True)
    raw,_=render_docx(snapshot(),original);document=Document(BytesIO(raw));text=body_text(document)
    check(text.count('完整正文42。')==1);check('第一项'in document.tables[0].cell(0,0).text)
    for tc in document.element.body.iter(qn('w:tc')):check(tc[-1].tag==qn('w:p'))


@pytest.mark.parametrize('slot',['summary','report_body','report_sections'])
def test_mixed_inline_block_slot_is_explicitly_rejected_without_changing_template(slot):
    original=template(('前文 {{ '+slot+' }} 后文',));before=hashlib.sha256(original).hexdigest()
    with pytest.raises(InvalidInput,match='own paragraph'):render_docx(snapshot(),original)
    check(hashlib.sha256(original).hexdigest()==before)


def test_duplicate_summary_slot_is_explicitly_rejected_not_silently_deduplicated():
    with pytest.raises(InvalidInput,match='exactly once'):
        render_docx(snapshot(),template(('{{ summary }}','{{ summary }}','{{ report_sections }}')))


@pytest.mark.parametrize('tag',['bookmarkStart','commentRangeStart','fldSimple','sectPr','ins'])
def test_block_marker_with_annotated_or_complex_content_is_rejected(tag):
    document=Document(BytesIO(template()));p=next(p for p in document.paragraphs if p.text=='{{ summary }}')
    if tag=='sectPr':p._p.get_or_add_pPr().append(OxmlElement('w:'+tag))
    else:p._p.append(OxmlElement('w:'+tag))
    out=BytesIO();document.save(out)
    with pytest.raises(InvalidInput,match='field, section, annotation or revision'):
        render_docx(snapshot(),out.getvalue())


def test_repeated_header_title_does_not_steal_body_ownership():
    document=Document(BytesIO(template(('{{ report_sections }}',))))
    document.sections[0].header.paragraphs[0].text='页眉 {{ title }}';out=BytesIO();document.save(out)
    raw,_=render_docx(snapshot(),out.getvalue());document=Document(BytesIO(raw))
    check(body_text(document).count('原创QA计划')==1)
    check(document.sections[0].header.paragraphs[0].text=='页眉 原创QA计划')


def test_literal_template_looking_text_and_repeated_natural_prose_are_not_deleted():
    source=snapshot('## 原始标题\n\n用户 <真实> & 内容 {{ literal }}\n\n重复内容。\n\n重复内容。')
    raw,_=render_docx(source,template());document=Document(BytesIO(raw));text=body_text(document)
    check('用户 <真实> & 内容 {{ literal }}'in text);check(text.count('重复内容。')==2)


def test_summary_heading_matching_report_section_cannot_drop_content_during_profile_ordering():
    source=snapshot('前文仍须完整保留。\n\n## 1  目标与工作边界\n\n小节说明。\n\n## 2  任务网络与分解\n\n末尾仍须完整保留。')
    source['experience']={'approved_rules':[{'section_key':'TASKS','heading':'任务章节'},
        {'section_key':'OVERVIEW','heading':'总览章节'}],'instructions':[]}
    raw,_=render_docx(source,template(('{{ summary }}','{{ report_sections }}')));text=body_text(Document(BytesIO(raw)))
    for expected in ['前文仍须完整保留。','小节说明。','末尾仍须完整保留。','保留原内容','原件不可覆盖','逐项检查原输入。']:
        check(expected in text,expected)


def test_comments_outside_replaced_slots_are_preserved():
    document=Document(BytesIO(template()));document.add_comment(document.paragraphs[0].runs[0],text='原批注保持',author='QA')
    out=BytesIO();document.save(out);before=out.getvalue();raw,_=render_docx(snapshot(),before)
    with ZipFile(BytesIO(before))as a,ZipFile(BytesIO(raw))as b:check(a.read('word/comments.xml')==b.read('word/comments.xml'))
    check('固定文字不得删除'in body_text(Document(BytesIO(raw))))


def test_exact_original_qa_r3_and_template_regression(tmp_path):
    path=os.environ.get('CIRVEN_WORD_QA_RECEIPT');template_path=os.environ.get('CIRVEN_WORD_QA_TEMPLATE')
    if not path or not template_path:pytest.skip('Exact original QA input not supplied; portable renderer cases still run')
    source=json.loads(Path(path).read_bytes())['value']['source_snapshot'];before=deepcopy(source)
    raw_template=Path(template_path).read_bytes()
    check(hashlib.sha256(raw_template).hexdigest()=='180bc35e504b8ba59a6f3dd6a4a46be09637d3d716abdcf745ee22f89bd36e52')
    raw,receipt=render_docx(source,raw_template);document=Document(BytesIO(raw));text=body_text(document)
    candidate=source['proposal']['payload']['candidate'];check(source==before)
    check(text.count(candidate['title'])==1);check('## 一、工作范围与交付边界'not in text)
    if 'summary'in receipt['body_content_slots']:
        for original_line in candidate['summary'].splitlines():
            if original_line.strip():check(original_line.removeprefix('## ') in text,original_line)
        for heading in ['一、工作范围与交付边界','二、证据、决定与建议分层','七、制作、审阅与验收']:
            check(sum(p.text==heading for p in document.paragraphs)==1,heading)
    else:
        history=next(r for r in receipt['historical_records']if r['source_snapshot_pointer']=='/proposal/payload/candidate/summary')
        check(history['sha256']==hashlib.sha256(candidate['summary'].encode()).hexdigest())
    for node in candidate['nodes']:
        for value in [node['title'],node['description'],*node['inputs'],*node['outputs'],*node['acceptance']]:check(value in text,value)
    for q in source['pending']['questions']:
        check(q['text']in text)
        if q.get('resolution')and q['resolution'].get('answer_text'):check(q['resolution']['answer_text']in text)
    check({q['id']for q in source['pending']['questions']if q['status']=='AWAITING_ANSWER'}=={'Q12','Q13'})
    check(len(candidate['nodes'])==17)
    with ZipFile(BytesIO(raw_template))as a,ZipFile(BytesIO(raw))as b:
        for n in ['word/header1.xml','word/footer1.xml']:check(a.read(n)==b.read(n),n)
    (tmp_path/'same-r3-new-word-probe.docx').write_bytes(raw)
    (tmp_path/'CONTENT_RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')


def test_original_qa_with_actual_frozen_elk_views_is_readable_geometry_not_a_new_artifact(tmp_path):
    sample_path=os.environ.get('CIRVEN_WORD_ELK_SAMPLE');path=os.environ.get('CIRVEN_WORD_QA_RECEIPT');template_path=os.environ.get('CIRVEN_WORD_QA_TEMPLATE')
    if not sample_path or not path or not template_path:pytest.skip('Actual frozen ELK/QA sample not supplied')
    sample=json.loads(Path(sample_path).read_bytes());source=json.loads(Path(path).read_bytes())['value']['source_snapshot'];before=deepcopy(source)
    check(sample['graph']==source['diagrams'][0]['graph']);check(sample['new_artifact_registered']is False)
    derived={**source,'diagrams':[{k:sample[k]for k in ('graph','model_sha256','png_sha256','png_base64','render_geometry','render_source')}]}
    raw,receipt=render_docx(derived,Path(template_path).read_bytes());check(source==before)
    result=receipt['diagram_readability'][0]
    expected={('NODE',n['id'])for n in sample['graph']['nodes']}|{('EDGE_LABEL',e['id'])for e in sample['graph']['edges']}
    document=Document(BytesIO(raw))
    if result.get('projection')=='CIRVEN_WORD_READING_PROJECTION_V1':
        check(result['minimum_font_pt']>=9.5 and result['windows']==[])
        check(result['complete_item_count']==len(expected) and len(document.inline_shapes)==1)
        check(result['source_png_sha256']==sample['png_sha256'])
    else:
        check(result['minimum_font_pt']==9)
        covered={(i['kind'],i['id'])for w in result['windows']for i in w['complete_items']};check(covered==expected)
        check(len(document.inline_shapes)==1+len(result['windows']))
        check(result['windows']);check(all(w['minimum_font_pt']>=9 for w in result['windows']))
    check(body_text(document).count(source['proposal']['payload']['candidate']['title'])==1)
    (tmp_path/'same-r3-new-elk-DIAGNOSTIC-NOT-PUBLISHED.docx').write_bytes(raw)
    (tmp_path/'CONTENT_RECEIPT.json').write_text(json.dumps({'qualification':'D_ONLY_DIAGNOSTIC_NOT_A_NEW_REGISTERED_REPORT',
        'original_source_unchanged':True,'graph_sample_sha256':hashlib.sha256(Path(sample_path).read_bytes()).hexdigest(),
        'render_receipt':receipt},ensure_ascii=False,indent=2)+'\n')


@pytest.mark.parametrize(('status','review'), [('DRAFT','CURRENT'),('CONFIRMED','CURRENT'),('CONFIRMED','NEEDS_REVIEW')])
def test_current_state_precedes_generated_originals_and_exact_technical_trace(status,review):
    source=snapshot('生成时尚未接受。\n\n原始JSON {"business":"必须保留"}，schema_version=旧值。')
    primary=source['proposal'];primary['payload']['status']=status;primary['review_status']=review
    candidate=primary['payload']['candidate'];candidate['title']='原任务标题（生成时待审查）'
    source['schema_version']=7
    anchor={'document_id':'dd21a737-52cc-4903-ab01-9707c1709490','revision':2,'chunk_index':3,
            'char_start':10,'char_end':25,'quote_sha256':'e'*64}
    candidate['nodes'][0]['source_anchors']=[anchor]
    receipt=primary['payload']['source_receipt']
    receipt['documents']=[{'document_id':anchor['document_id'],'revision':2,'filename':'工单现场资料.md',
        'raw_sha256':'d'*64,'parsed_sha256':'f'*64}]
    receipt['coverage']=[{'document_id':anchor['document_id'],'text_coverage':'FULL_PARSED_TEXT',
                         'omitted_chunk_indexes':[],'warnings':[]}]
    question={'id':'Q01','text':'语言仍请用户选择。','status':'ANSWERED_PENDING_ACCEPTANCE',
              'reason':'原始选择未决。','affects_node_ids':['T01'],'blocking':False,
              'requested_materials':[],'answers':[{'text':'已有回答原文，不视为接受。'}],
              'resolution':None,'source_anchors':[anchor]}
    source['pending'].update(questions=[question],pending_count=1)
    before=deepcopy(source)
    raw,audit=render_docx(source);document=Document(BytesIO(raw));text=body_text(document)
    front=text[:text.index('\n可追溯附录\n')]
    check(source==before,'Presentation must not mutate status, answers, anchors or source text')
    check(front.startswith('项目任务书\n主题（题名原文）：'+candidate['title']+'\n本版状态：'),'Business identity and current structured state precede the report body')
    expected_status='已确认计划（CONFIRMED）' if status=='CONFIRMED' else '待确认草稿（DRAFT）'
    check(expected_status in front,'The actual structured confirmation state is visible')
    check(('来源已变化，需复核' if review=='NEEDS_REVIEW' else '来源版本一致')in front)
    check('当前未决事项 1 项'in front and '已有回答原文，不视为接受。'in front)
    check('计划或需求确认不代表工具权限、执行授权或独立验收已经通过。'in front)
    for technical in [anchor['document_id'],'b'*64,'d'*64,'schema_version','{"business"']:
        check(technical not in front,'Generated business front must not expose trace or original JSON: '+technical)
    check('工单现场资料.md · 修订2'in front,'Business prose keeps a readable exact-revision source cue')
    check(text.count(candidate['title'])==1)
    check(text.count('原始JSON {"business":"必须保留"}，schema_version=旧值。')==0)
    trace=text[text.index('\n可追溯附录\n'):]
    check('工单现场资料.md'in trace and '片段 3 · 字符 10–25'in trace,'Readable source/revision/excerpt location stays in Word')
    for technical in [anchor['document_id'],'b'*64,'d'*64,'f'*64,'e'*64,'schema_version']:
        check(technical not in trace,'Machine identities remain in the unchanged source snapshot, not the default Word appendix: '+technical)
    check(audit['content_coverage']==[])
    history=next(r for r in audit['historical_records']if r['source_snapshot_pointer']=='/proposal/payload/candidate/summary')
    check(history['sha256']==hashlib.sha256(candidate['summary'].encode()).hexdigest())
    check(history['byte_length']==len(candidate['summary'].encode()))


def test_explicit_template_slots_keep_original_location_and_exact_requested_reference_values():
    source=snapshot('用户指定原文 {"raw":"不是系统协议"}。')
    before=deepcopy(source)
    original=template(('{{ title }}','{{ summary }}','{{ proposal_ref }}','{{ source_receipt }}','{{ report_sections }}'))
    raw,audit=render_docx(source,original);document=Document(BytesIO(raw));text=body_text(document)
    check(source==before)
    check(text.startswith('本版状态：待确认草稿（DRAFT） · 计划版本 1'),'Template cannot conceal the current state')
    check(text.count(source['proposal']['payload']['candidate']['title'])==1)
    check(text.count('用户指定原文 {"raw":"不是系统协议"}。')==1)
    check(text.index('生成时说明（原文；用户模板指定位置）')<text.index('用户指定原文'))
    from packages.domain.local_workspaces import canonical
    check(canonical(source['pending']['proposal_ref'])in text,'An explicitly requested ref slot keeps its exact value')
    check(canonical(source['proposal']['payload']['source_receipt'])in text,'An explicitly requested receipt slot keeps its exact value')
    check('计划版本'in text[text.index('\n可追溯附录\n'):] and '第1版'in text[text.index('\n可追溯附录\n'):])
    check(len([c for c in audit['content_coverage']if c['source_key']=='candidate.summary'])==1)


def test_discovery_report_keeps_generated_summary_in_originals_after_current_confirmation():
    source=snapshot()
    primary={'id':'discovery_fixture','revision':4,'sha256':'d'*64,
             'readiness':{'review_status':'CURRENT'},'answers':[],
             'payload':{'status':'CONFIRMED','source_receipt':deepcopy(source['proposal']['payload']['source_receipt']),
                        'resolutions':[],'candidate':{'title':'需求定义（产生时未确认）',
                        'summary':'生成时记载：需求尚未接受。 {"input":"原文"}',
                        'phase':'DEFINE','claims':[],'competitors':[],'next_actions':[{'action':'重新接受旧Q05。','reason':'生成时建议。','method':'原文审查。','expected_artifact':'生成时产物。'}]}}}
    source.update(source_kind='DISCOVERY',discovery=primary)
    source.pop('proposal')
    source['pending']['session_ref']={k:primary[k]for k in ('id','revision','sha256')}
    source['pending'].pop('proposal_ref')
    before=deepcopy(source)
    raw,audit=render_docx(source);text=body_text(Document(BytesIO(raw)))
    check(source==before)
    check(text.startswith('需求发现报告\n主题（题名原文）：'+primary['payload']['candidate']['title']+'\n本版状态：已确认需求发现（CONFIRMED） · 需求发现版本 4'))
    check('需求尚未接受'not in text and text.count('生成时记载：需求尚未接受。 {"input":"原文"}')==0)
    check(text.count(primary['payload']['candidate']['title'])==1)
    check('生成时建议（原文）：重新接受旧Q05。'in text,'A historical suggested action must not appear as the current next action')
    check(Document(BytesIO(raw)).core_properties.subject=='已确认需求发现（CONFIRMED）','Visible and metadata status use the same source-kind authority')
    check(audit['content_coverage']==[] and len(audit['historical_records'])==1)


@pytest.mark.parametrize(('source_kind','display'),[
    ('discovery-confirmed-baseline:fixed-session:3:fixed-hash','已确认要求基线'),
    ('discovery-user-statements:fixed-session','用户陈述记录'),
    ('local-workspace:fixed-source','原始文件-dd21a737-52cc-4903-ab01-9707c1709490.md'),
])
def test_source_role_labels_hide_only_internal_synthetic_names_and_preserve_user_names(source_kind,display):
    source=snapshot()
    filename='原始文件-dd21a737-52cc-4903-ab01-9707c1709490.md'
    row={'document_id':'dd21a737-52cc-4903-ab01-9707c1709490','revision':2,'filename':filename,
         'source':source_kind,'raw_sha256':'d'*64,'parsed_sha256':'e'*64}
    receipt=source['proposal']['payload']['source_receipt']
    receipt['documents']=[row]
    receipt['coverage']=[{'document_id':row['document_id'],'text_coverage':'FULL_PARSED_TEXT',
                         'omitted_chunk_indexes':[],'warnings':[]}]
    source['proposal']['payload']['candidate']['nodes'][0]['source_anchors']=[
        {'document_id':row['document_id'],'revision':2,'chunk_index':0,'char_start':0,'char_end':10}]
    before=deepcopy(source)
    raw,_=render_docx(source);text=body_text(Document(BytesIO(raw)))
    front=text[:text.index('\n可追溯附录\n')]
    check(source==before)
    check(display in front,'The visible source name must follow its explicit provenance type')
    if source_kind.startswith('discovery-'):
        check(filename not in front,'Internal synthetic filenames belong to technical trace')
    else:
        check(filename in front,'A real user filename must never be shortened by UUID regex')
    trace=text[text.index('\n可追溯附录\n'):]
    if source_kind.startswith('discovery-'):
        check(display in trace and filename not in trace,'Synthetic identities are external; Word keeps the readable source role')
    else:
        check(filename in trace,'The real user filename remains visible and unchanged')
