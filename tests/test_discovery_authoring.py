"""Actual local templates, reviewed writing rules and frozen discovery diagrams/reports."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import base64
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

from docx import Document
from docx.shared import Pt
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from apps.api.local_workspaces import register_local_workspace_routes
from apps.api.template_profiles import register_template_profile_routes
from apps.api.discovery_diagrams import register_discovery_diagram_routes
from apps.api.planning_reports import register_planning_report_routes
from packages.domain.discovery import DiscoveryWorkbench
from packages.domain.discovery_schema import DiscoveryCreate,DiscoveryEdit,DiscoveryAnswer
from packages.domain.local_workspaces import WorkspaceInput,FileSource,digest,sha,canonical
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_reports import PlanningReports,PlanningReportInput
from packages.domain.planning_docx import normalize_template,template_info
from packages.domain.planning_diagrams import PlanningDiagrams
from packages.domain.template_profiles import TemplateProfiles,ProfileCreate,ProfileReview
from packages.domain.tasks import TaskService
from packages.domain.store import Conflict,InvalidInput,NotFound

ROOT=Path(__file__).resolve().parents[1]


def candidate():
    c={'title':'专业引导与真实图报告','phase':'SPECIFY','summary':'保留实际需求、来源和待确认项。',
       'claims':[{'id':'req','text':'可检查需求与图的实际来源。','category':'REQUIREMENT','origin':'MODEL_SUGGESTION',
                  'source_anchors':[],'acceptance':['每份报告保留精确版本和待确认项。']}],
       'questions':[{'id':'scope','text':'哪些实际材料需要核对？','reason':'范围影响来源覆盖。','category':'DECISION',
                     'blocking':True,'affects_claim_ids':['req'],'requested_materials':['首批实际文件'],'source_anchors':[]}],
       'competitors':[{'id':'manual','name':'人工审核','alternative_type':'MANUAL','summary':'当前替代办法。',
                       'observations':[{'dimension':'效果','text':'尚未实测，保留未知。','status':'UNKNOWN','source_anchors':[]}]}],
       'graphs':[],'next_actions':[]}
    for kind in ['ARCHITECTURE','FLOW','MINDMAP']:
        c['graphs'].append({'id':kind.lower(),'kind':kind,'title':'数据与审阅 '+kind,
            'nodes':[{'id':'input','label':'真实输入 <文件> & 样例','description':'保留原件。','claim_ids':['req'],'source_anchors':[],'origin':'MODEL_SUGGESTION'},
                     {'id':'review','label':'审阅与决议','description':'答案仍待接受。','claim_ids':['req'],'source_anchors':[],'origin':'MODEL_SUGGESTION'}],
            'edges':[{'id':'read','source':'input','target':'review','relation':'读取与核对','claim_ids':['req'],'source_anchors':[],'origin':'MODEL_SUGGESTION'}]})
    c['graphs'][0]['edges'].append({'id':'feedback','source':'review','target':'input','relation':'真实反馈回路',
                                   'claim_ids':['req'],'source_anchors':[],'origin':'MODEL_SUGGESTION'})
    return c


def word_bytes(*,structured=False):
    d=Document();d.styles['Normal'].font.name='Cambria';d.styles['Normal'].font.size=Pt(12)
    d.sections[0].header.paragraphs[0].text='用户模板原始页眉';d.sections[0].footer.paragraphs[0].text='用户原始页脚'
    d.add_heading('参考报告：仅形式经验',1)
    d.add_paragraph('旧项目性能为98765，不是新项目事实。')
    t=d.add_table(rows=1,cols=2);t.cell(0,0).text='用户表格';t.cell(0,1).text='必须保留'
    if structured:d.add_paragraph('{{ report_sections }}')
    out=BytesIO();d.save(out);return out.getvalue()


def change_zip(raw,transform,extra=None):
    out=BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as src,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():dst.writestr(name,transform(name,src.read(name)))
        if extra:dst.writestr(*extra)
    return out.getvalue()


def dotx(raw):
    return change_zip(raw,lambda name,part:part.replace(b'wordprocessingml.document.main+xml',b'wordprocessingml.template.main+xml')
                      if name=='[Content_Types].xml' else part)


@pytest.fixture
def authoring(tmp_path,request):
    data=tmp_path/'controller';store=TaskService(data/'eeas.sqlite3',ROOT);pid=store.create_project('原创模板/图/写作经验','')['id']
    actual=tmp_path/'用户选择的 实际目录';actual.mkdir()
    app=FastAPI();app.state.store=store
    for error,code in [(Conflict,409),(InvalidInput,422),(NotFound,404)]:
        async def handler(request:Request,exc:Exception,code=code):return JSONResponse({'error':str(exc)},status_code=code)
        app.add_exception_handler(error,handler)
    register_local_workspace_routes(app,store,data)
    workspaces=app.state.local_workspaces
    binding=workspaces.bind(pid,WorkspaceInput(idempotency_key='actualfolder',root=str(actual),allowed_write_paths=['*']))
    proposals=PlanningProposals(store,ROOT,workspaces=workspaces);discovery=DiscoveryWorkbench(store,proposals)
    app.state.planning_proposals=proposals;app.state.discovery=discovery
    session=discovery.create(pid,DiscoveryCreate(operation_id='start',title='从实际痛点起步',entry_mode='PROBLEM',statement='减少资料不清造成的返工。'))
    assert session['payload']['execution']['model_calls']==0
    session=discovery.edit(pid,session['id'],DiscoveryEdit(operation_id='edit',expected_revision=1,candidate=candidate(),change_reason='用户提出拟议要求与关系'))
    register_template_profile_routes(app,store,data)
    register_discovery_diagram_routes(app,store,data,source_root=ROOT)
    register_planning_report_routes(app,store,data)
    with TestClient(app) as client:
        env={'store':store,'pid':pid,'root':actual,'data':data,'app':app,'client':client,'binding':binding,'ws':workspaces,
             'session':session,'discovery':discovery,'profiles':app.state.template_profiles,'diagrams':app.state.planning_diagrams}
        yield env
    if os.environ.get('EEAS_EVIDENCE_DIR'):
        dest=Path(os.environ['EEAS_EVIDENCE_DIR'])/'authoring-artifacts'/request.node.name.replace('/','_')
        for folder in ['planning-reports','planning-diagrams','template-profiles']:
            if (data/folder).exists():shutil.copytree(data/folder,dest/folder)


def source(env,name,raw):
    (env['root']/name).write_bytes(raw)
    return env['ws'].read(env['pid'],env['binding']['id'],name)['source_ref']


def profile(env,raw=None,name='参考写法.docx',role='WRITING_EXAMPLE'):
    ref=source(env,name,raw or word_bytes())
    value=env['profiles'].create(env['pid'],ProfileCreate(operation_id='profile',name='参考写作经验',
        sources=[{'role':role,'file_source_ref':ref}]))
    return ref,value


def reviewed(env):
    ref,value=profile(env)
    rule=value['draft_rules'][0];edited={**rule,'section_key':'TASKS','heading':'用户要求的详细任务分解',
        'instruction':'采用用户标题样式；旧报告数字和结论不能转作新项目事实。'}
    result=env['profiles'].review(env['pid'],value['id'],ProfileReview(operation_id='review',expected_revision=1,
        accepted_rule_ids=[rule['id']],edits=[edited],reason='用户审阅并只接受表达形式'))
    return ref,value,result


def report(env,**fields):
    base=f"/api/projects/{env['pid']}/discovery/sessions/{env['session']['id']}/reports"
    response=env['client'].post(base,json={'operation_id':'report','expected_revision':env['session']['revision'],'format':'docx',**fields})
    assert response.status_code in {200,201},response.text
    return response.json()


def test_profile_is_draft_review_is_explicit_and_reusable_output_contains_no_example_facts(authoring):
    e=authoring;ref,draft,approved=reviewed(e)
    with pytest.raises(Conflict,match='reviewed'):e['profiles'].instructions(e['pid'],draft['profile_ref'])
    assert approved['revision']==2 and approved['status']=='REVIEWED'
    data=e['profiles'].instructions(e['pid'],approved['profile_ref'])
    assert data['usage']=='EXPERIENCE_ONLY_NOT_PROJECT_FACTS' and '98765' not in canonical(data)
    assert '98765' in canonical(draft['payload']['observations'])
    assert e['profiles'].get(e['pid'],draft['id'],1)['status']=='DRAFT'
    restart=TemplateProfiles(e['store'],e['data'],e['ws'])
    assert restart.instructions(e['pid'],approved['profile_ref'])==data


@pytest.mark.parametrize('fault',['unknown-rule','stale-revision','forged-anchor','unknown-style','draft-ref'])
def test_profile_review_rejects_wrong_exact_rules_revision_or_source(authoring,fault):
    e=authoring;_,draft=profile(e);rule=draft['draft_rules'][0]
    body={'operation_id':'review','expected_revision':1,'accepted_rule_ids':[rule['id']],'reason':'实际审阅'}
    if fault=='unknown-rule':body['accepted_rule_ids']=['fake-rule']
    if fault=='stale-revision':body['expected_revision']=99
    if fault in {'forged-anchor','unknown-style'}:
        edited=deepcopy(rule)
        if fault=='forged-anchor':edited['source_anchors'][0]['quote_sha256']='0'*64
        else:edited['style_id']='UnobservedStyle'
        body['edits']=[edited]
    if fault=='draft-ref':
        with pytest.raises(Conflict):e['profiles'].instructions(e['pid'],draft['profile_ref'])
        return
    with pytest.raises((InvalidInput,Conflict,NotFound)):e['profiles'].review(e['pid'],draft['id'],ProfileReview.model_validate(body))
    assert e['profiles'].get(e['pid'],draft['id'])['revision']==1


def test_profile_concurrent_create_review_cas_replay_and_original_drift(authoring):
    e=authoring;ref,draft=profile(e)
    body=ProfileReview(operation_id='review',expected_revision=1,accepted_rule_ids=[draft['draft_rules'][0]['id']],reason='明确接受')
    with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(lambda _:e['profiles'].review(e['pid'],draft['id'],body),range(2)))
    assert values[0]['profile_ref']==values[1]['profile_ref'] and sum(v['replayed'] for v in values)==1
    with pytest.raises(Conflict):e['profiles'].review(e['pid'],draft['id'],body.model_copy(update={'operation_id':'stale'}))
    (e['root']/ref['path']).write_bytes(b'changed')
    assert e['profiles'].get(e['pid'],draft['id'])['source_checks'][0]['status']!='CURRENT'
    assert e['profiles'].instructions(e['pid'],values[0]['profile_ref'])['profile_ref']==values[0]['profile_ref']


@pytest.mark.parametrize('fault',['macro','external-template','ole','escape','code'])
def test_template_execution_is_rejected_without_false_success(authoring,fault):
    e=authoring;raw=word_bytes(structured=True)
    if fault=='macro':raw=change_zip(raw,lambda n,p:p,('word/vbaProject.bin',b'code'))
    if fault=='ole':raw=change_zip(raw,lambda n,p:p,('word/embeddings/ole.bin',b'code'))
    if fault=='escape':raw=change_zip(raw,lambda n,p:p,('../outside.xml',b'<outside/>'))
    if fault=='external-template':raw=change_zip(raw,lambda n,p:p.replace(b'</Relationships>',b'<Relationship Id="evil" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" Target="https://example.com/evil.dotm" TargetMode="External"/></Relationships>') if n=='word/_rels/document.xml.rels' else p)
    if fault=='code':raw=change_zip(raw,lambda n,p:p.replace(b'{{ report_sections }}',b'{% for x in range(999) %}') if n=='word/document.xml' else p)
    ref=source(e,'恶意模板.docx',raw)
    response=e['client'].post(f"/api/projects/{e['pid']}/discovery/sessions/{e['session']['id']}/reports",
        json={'operation_id':'unsafe','expected_revision':2,'template_ref':ref})
    assert response.status_code==422,response.text
    assert not list((e['data']/'planning-reports').glob('**/result.json'))
    assert list((e['data']/'planning-reports').glob('**/*-failure.json'))


def test_dotx_normalization_preserves_original_all_parts_and_structured_report_tables(authoring):
    e=authoring;raw=dotx(word_bytes(structured=True));ref=source(e,'用户任务书.dotx',raw)
    normalized,receipt=normalize_template(raw)
    assert receipt['source_type']=='DOTX' and normalize_template(raw)[0]==normalized
    with zipfile.ZipFile(BytesIO(raw)) as original,zipfile.ZipFile(BytesIO(normalized)) as current:
        assert set(original.namelist())==set(current.namelist())
        assert all(original.read(n)==current.read(n) for n in original.namelist() if n!='[Content_Types].xml')
    value=report(e,template_ref=ref);content=e['client'].get(value['artifact']['download_url']).content
    document=Document(BytesIO(content))
    assert len(document.tables)>1 and '98765' in '\n'.join(p.text for p in document.paragraphs)
    assert '用户模板原始页眉' in document.sections[0].header.paragraphs[0].text
    assert '用户原始页脚' in document.sections[0].footer.paragraphs[0].text
    assert (e['root']/ref['path']).read_bytes()==raw
    assert value['status']=='DRAFT' and value['source_kind']=='DISCOVERY' and value['proposal_ref'] is None
    with e['store'].connect() as c:assert c.execute('SELECT count(*) FROM planning_proposals').fetchone()[0]==0
    snapshot=e['client'].get(value['source_snapshot']['download_url']).json()
    assert snapshot['template_ref']==ref and snapshot['discovery']['sha256']==e['session']['sha256']


@pytest.mark.parametrize('kind',['architecture','flow','mindmap'])
def test_real_svg_png_model_are_same_scope_and_survive_restart(authoring,kind):
    e=authoring;s=e['session'];result=e['diagrams'].generate(e['pid'],s['id'],kind,2)
    for format in ['svg','png','json']:
        raw,item,again=e['diagrams'].download(e['pid'],s['id'],kind,2,format)
        assert sha(raw)==item['sha256'] and result['artifact_ref']==again['artifact_ref']
        if format=='svg':
            parsed=ET.fromstring(raw);assert len([n for n in parsed.iter() if n.get('data-node-id')])==2
            assert b'&lt;' in raw and b'&amp;' in raw
        if format=='png':
            with Image.open(BytesIO(raw)) as image:assert image.format=='PNG' and image.width*image.height<=16_000_000
        if format=='json':assert json.loads(raw)['session_ref']['sha256']==s['sha256']
    restart=PlanningDiagrams(e['store'],e['data'],e['discovery'].get,source_root=ROOT)
    assert restart.read_artifact(e['pid'],result['artifact_ref'])['png_bytes']==e['diagrams'].read_artifact(e['pid'],result['artifact_ref'])['png_bytes']


def test_graph_concurrent_generation_and_version_refs_are_distinct(authoring):
    e=authoring;s=e['session']
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:e['diagrams'].generate(e['pid'],s['id'],'architecture',2),range(2)))
    assert results[0]==results[1] and len(list((e['data']/'planning-diagrams'/e['pid']).glob('*/manifest.json')))==1
    c=candidate();c['graphs'][0]['nodes'][0]['label']='实际修订的输入'
    updated=e['discovery'].edit(e['pid'],s['id'],DiscoveryEdit(operation_id='new',expected_revision=2,candidate=c,change_reason='用户修订图示'))
    newest=e['diagrams'].generate(e['pid'],s['id'],'architecture',updated['revision'])
    assert newest['artifact_ref']!=results[0]['artifact_ref']
    old=e['diagrams'].read_artifact(e['pid'],results[0]['artifact_ref']);assert old['graph']['nodes'][0]['label'].startswith('真实输入')


@pytest.mark.parametrize('fault',['png','model','manifest','rewritten-checksum'])
def test_saved_graph_corruption_is_not_accepted(authoring,fault):
    e=authoring;v=e['diagrams'].generate(e['pid'],e['session']['id'],'architecture',2);root=e['data']/'planning-diagrams'/e['pid']/v['artifact_ref']['id']
    file={'png':'diagram.png','model':'model.json','manifest':'manifest.json','rewritten-checksum':'diagram.png'}[fault]
    (root/file).write_bytes(b'changed')
    if fault=='rewritten-checksum':
        wrapper=json.loads((root/'manifest.json').read_bytes());m=wrapper['payload'];m['files']['png'].update(sha256=sha(b'changed'),byte_length=7)
        (root/'manifest.json').write_text(canonical({'payload':m,'sha256':digest(m)}))
    with pytest.raises(Conflict):e['diagrams'].read_artifact(e['pid'],v['artifact_ref'])


def test_report_freezes_reviewed_style_real_graph_pending_and_late_answer(authoring):
    e=authoring;_,_,p=reviewed(e);graph=e['diagrams'].generate(e['pid'],e['session']['id'],'architecture',2)
    template=source(e,'正式结构.docx',word_bytes(structured=True))
    value=report(e,template_ref=template,experience_ref=p['profile_ref'],diagram_refs=[graph['artifact_ref']])
    raw=e['client'].get(value['artifact']['download_url']).content;document=Document(BytesIO(raw))
    assert len(document.inline_shapes)==1 and len(document.tables)>=4
    assert '用户要求的详细任务分解' in '\n'.join(p.text for p in document.paragraphs)
    source_diagram=e['diagrams'].read_artifact(e['pid'],graph['artifact_ref'])
    snapshot=e['client'].get(value['source_snapshot']['download_url']).json()
    frozen,=snapshot['diagrams']
    assert frozen['artifact_ref']==graph['artifact_ref']
    assert base64.b64decode(frozen['png_base64'],validate=True)==source_diagram['png_bytes']
    assert sha(source_diagram['png_bytes'])==frozen['png_sha256']==source_diagram['png_sha256']
    manifest=e['app'].state.planning_reports._artifact(e['pid'],e['session']['id'],value['artifact']['id'])
    reading,=manifest['render_receipt']['diagram_readability']
    assert reading['projection']=='CIRVEN_WORD_READING_PROJECTION_V1'
    assert reading['source_png_sha256']==frozen['png_sha256']
    assert reading['graph_sha256']==digest(frozen['graph'])==source_diagram['model_sha256']
    blip=document.inline_shapes[0]._inline.graphic.graphicData.pic.blipFill.blip
    part=document.part.related_parts[blip.embed]
    assert part.content_type=='image/png' and sha(part.blob)==reading['png_sha256']
    e['discovery'].answer(e['pid'],e['session']['id'],DiscoveryAnswer(operation_id='late',expected_revision=2,question_id='scope',text='已回答但尚未接受。'))
    repeat=report(e,template_ref=template,experience_ref=p['profile_ref'],diagram_refs=[graph['artifact_ref']])
    assert repeat['replayed'] and repeat['artifact']==value['artifact']
    assert e['client'].get(value['pending_json']['download_url']).json()['questions'][0]['status']=='AWAITING_ANSWER'
    next_report=report(e,operation_id='later',template_ref=template,experience_ref=p['profile_ref'])
    assert e['client'].get(next_report['pending_json']['download_url']).json()['questions'][0]['status']=='ANSWERED_PENDING_ACCEPTANCE'
    assert e['client'].get(value['artifact']['download_url']).content==raw


@pytest.mark.parametrize('fault',['cross-project','forged-ref','wrong-session','old-session','duplicate-ref'])
def test_report_rejects_diagrams_outside_exact_source(authoring,fault):
    e=authoring;v=e['diagrams'].generate(e['pid'],e['session']['id'],'architecture',2);refs=[deepcopy(v['artifact_ref'])]
    if fault=='forged-ref':refs[0]['sha256']='0'*64
    if fault=='duplicate-ref':refs*=2
    if fault in {'wrong-session','old-session'}:
        if fault=='wrong-session':new=e['discovery'].create(e['pid'],DiscoveryCreate(operation_id='other',title='另一来源',entry_mode='PROBLEM',statement='独立问题。'))
        else:new=e['discovery'].edit(e['pid'],e['session']['id'],DiscoveryEdit(operation_id='new',expected_revision=2,candidate=candidate(),change_reason='新修订'))
        e['session']=new
    pid=e['store'].create_project('另项目','')['id'] if fault=='cross-project' else e['pid']
    r=e['client'].post(f"/api/projects/{pid}/discovery/sessions/{e['session']['id']}/reports",json={'operation_id':'bad','expected_revision':e['session']['revision'],'diagram_refs':refs})
    assert r.status_code in {404,409,422},r.text


def test_actual_api_metadata_bytes_and_download_kind_boundary(authoring):
    e=authoring;base=f"/api/projects/{e['pid']}/discovery/sessions/{e['session']['id']}/diagrams/architecture"
    metadata=e['client'].get(base+'/artifact?revision=2');assert metadata.status_code==200,metadata.text
    value=metadata.json()
    for file in value['files'].values():
        r=e['client'].get(file['download_url']);assert r.status_code==200
        assert len(r.content)==file['byte_length'] and sha(r.content)==file['sha256']
        assert json.loads(r.headers['X-EEAS-Artifact-Ref'])==value['artifact_ref']
    result=report(e)
    wrong=result['artifact']['download_url'].replace('/discovery/sessions/','/planning/proposals/')
    assert e['client'].get(wrong).status_code==404


def test_word_report_replay_after_restart_and_changed_template(authoring):
    e=authoring;ref=source(e,'模板.dotx',dotx(word_bytes(structured=True)));value=report(e,template_ref=ref)
    request=PlanningReportInput(operation_id='report',expected_revision=2,template_ref=ref)
    (e['root']/ref['path']).write_bytes(b'drift')
    restarted=PlanningReports(e['store'],e['data'],e['app'].state.planning_proposals,e['ws'],discovery_reader=e['discovery'].get)
    again=restarted.generate_discovery(e['pid'],e['session']['id'],request)
    assert again['replayed'] and again['artifact']==value['artifact']


def test_report_generation_failure_stays_failed_and_retry_saves_real_bytes(authoring,monkeypatch):
    e=authoring;import packages.domain.planning_reports as module
    original=module.render_docx
    def failure(*args):raise RuntimeError('original renderer failure')
    monkeypatch.setattr(module,'render_docx',failure)
    base=f"/api/projects/{e['pid']}/discovery/sessions/{e['session']['id']}/reports"
    with pytest.raises(RuntimeError,match='original renderer failure'):e['client'].post(base,json={'operation_id':'report','expected_revision':2})
    assert list((e['data']/'planning-reports').glob('**/*-failure.json')) and not list((e['data']/'planning-reports').glob('**/result.json'))
    monkeypatch.setattr(module,'render_docx',original);value=report(e)
    assert e['client'].get(value['artifact']['download_url']).content.startswith(b'PK')


def test_reviewed_real_chapter_mapping_fills_existing_template_without_append_or_slots(authoring):
    e=authoring;d=Document();d.sections[0].header.paragraphs[0].text='正式任务书页眉'
    d.sections[0].footer.paragraphs[0].text='正式任务书页脚'
    d.add_paragraph('用户封面与固定说明')
    for heading in ['目标与范围','详细任务','待确认问题','资料来源']:
        d.add_heading(heading,1);d.add_paragraph('旧项目98765，必须被当前项目正文替换。')
    out=BytesIO();d.save(out);raw=out.getvalue();ref,draft=profile(e,raw=raw,name='真正用户任务书.docx',role='TEMPLATE')
    assert {r.get('section_key') for r in draft['draft_rules']}=={'OVERVIEW','TASKS','QUESTIONS','SOURCES'}
    approved=e['profiles'].review(e['pid'],draft['id'],ProfileReview(operation_id='review',expected_revision=1,
        accepted_rule_ids=[r['id'] for r in draft['draft_rules']],reason='用户逐项确认四个真实章节映射'))
    value=report(e,template_ref=ref,experience_ref=approved['profile_ref'])
    content=e['client'].get(value['artifact']['download_url']).content;result=Document(BytesIO(content))
    body='\n'.join(p.text for p in result.paragraphs)
    assert '旧项目98765' not in body and '用户封面与固定说明' in body
    assert all(body.count(h)==1 for h in ['目标与范围','详细任务','待确认问题','资料来源'])
    assert '实际材料' in body and len(result.tables)>=2
    assert result.sections[0].header.paragraphs[0].text=='正式任务书页眉' and result.sections[0].footer.paragraphs[0].text=='正式任务书页脚'
    assert (e['root']/ref['path']).read_bytes()==raw
    directory=e['data']/'planning-reports'/e['pid']/e['session']['id']/'artifacts'/value['artifact']['id']
    manifest=json.loads((directory/'manifest.json').read_bytes())['payload']
    assert manifest['render_receipt']['template_mode']=='PROFILE_SECTION_MAPPING'


@pytest.mark.parametrize('filename,raw',[('经验.md','# 概要\n先给结论，再给证据。'.encode()),('经验.txt','先给结论，再给证据。'.encode())])
def test_real_utf8_text_writing_examples_are_data_and_need_review(authoring,filename,raw):
    e=authoring;_,p=profile(e,raw=raw,name=filename)
    assert p['status']=='DRAFT' and p['approved_rules']==[] and p['payload']['observations'][0]['format'] in {'md','txt'}


def test_reference_hyperlink_is_read_as_data_but_cannot_be_rendered_as_external_template(authoring):
    e=authoring;raw=change_zip(word_bytes(),lambda n,p:p.replace(b'</Relationships>',b'<Relationship Id="reference" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/quoted-source" TargetMode="External"/></Relationships>') if n=='word/_rels/document.xml.rels' else p)
    _,p=profile(e,raw=raw);assert p['status']=='DRAFT'
    with pytest.raises(InvalidInput,match='External'):template_info(raw)


def test_active_word_field_rejected_before_any_native_execution(authoring):
    e=authoring;raw=change_zip(word_bytes(),lambda n,p:p.replace(b'</w:body>',b'<w:p><w:r><w:instrText>DDEAUTO malicious-command</w:instrText></w:r></w:p></w:body>') if n=='word/document.xml' else p)
    with pytest.raises(InvalidInput,match='field instructions'):template_info(raw)


def test_template_expression_in_xml_attribute_is_not_run(authoring):
    raw=change_zip(word_bytes(),lambda n,p:p.replace(b'<w:body>',b'<w:body hidden="{{ 7 * 7 }}">') if n=='word/document.xml' else p)
    with pytest.raises(InvalidInput,match='outside paragraph text'):template_info(raw)


def test_missing_explicit_font_fails_without_system_fallback_or_download(authoring):
    e=authoring;service=PlanningDiagrams(e['store'],e['data'],e['discovery'].get,font_path=e['root']/'unconfigured.ttf')
    with pytest.raises(InvalidInput,match='CJK'):service.generate(e['pid'],e['session']['id'],'architecture',2)
    assert not (e['data']/'planning-diagrams').exists()


@pytest.mark.parametrize('fault',['endpoint','unbased','unknown-claim','node-edge-id','xml-control','wrong-scope','sha'])
def test_renderer_revalidates_callback_graph_and_scope(authoring,fault):
    e=authoring;value=deepcopy(e['session']);graph=value['payload']['candidate']['graphs'][0]
    if fault=='endpoint':graph['edges'][0]['target']='missing'
    if fault=='unbased':graph['nodes'][0]['claim_ids']=[]
    if fault=='unknown-claim':graph['nodes'][0]['claim_ids']=['missing']
    if fault=='node-edge-id':graph['edges'][0]['id']=graph['nodes'][0]['id']
    if fault=='xml-control':graph['nodes'][0]['label']='invalid\x01control'
    if fault=='wrong-scope':value['project_id']='another-project'
    value['sha256']='0'*64 if fault=='sha' else digest(value['payload'])
    # Explicitly a hostile callback fixture; actual normal tests use real DiscoveryWorkbench.
    service=PlanningDiagrams(e['store'],e['data'],lambda *args:value,source_root=ROOT)
    with pytest.raises((InvalidInput,Conflict)):service.generate(e['pid'],value['id'],'architecture',2)


def test_large_graph_keeps_every_node_and_edge_with_bounded_real_png(authoring):
    e=authoring;c=candidate();g=c['graphs'][0]
    g['nodes']=[{'id':f'n{i}','label':'真实节点 '+str(i),'description':'完整说明','claim_ids':['req'],'source_anchors':[],'origin':'MODEL_SUGGESTION'} for i in range(500)]
    g['edges']=[{'id':f'e{i}','source':f'n{i}','target':f'n{i+1}','relation':'依赖','claim_ids':['req'],'source_anchors':[],'origin':'MODEL_SUGGESTION'} for i in range(499)]
    value=e['discovery'].edit(e['pid'],e['session']['id'],DiscoveryEdit(operation_id='large',expected_revision=2,candidate=c,change_reason='真实大图模型'))
    result=e['diagrams'].generate(e['pid'],value['id'],'architecture',3);item=e['diagrams'].read_artifact(e['pid'],result['artifact_ref'])
    assert len(item['graph']['nodes'])==500 and len(item['graph']['edges'])==499
    with Image.open(BytesIO(item['png_bytes'])) as image:assert image.width*image.height<=16_000_000
    svg,_,_=e['diagrams'].download(e['pid'],value['id'],'architecture',3,'svg')
    parsed=ET.fromstring(svg);assert sum(n.get('data-node-id') is not None for n in parsed.iter())==500
