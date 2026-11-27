"""Real Store proposals, selected local templates, Word bytes and retry boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import socket
from threading import Event,Thread
import time
from unittest.mock import patch
import zipfile

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt,RGBColor
from fastapi.testclient import TestClient
import httpx
import pytest
import uvicorn

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.local_workspaces import register_local_workspace_routes
from apps.api.planning_reports import register_planning_report_routes
from packages.domain.local_workspaces import canonical,digest,sha
from packages.domain.planning_docx import render_docx
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_reports import PlanningReports,PlanningReportInput
from packages.domain.planning_schema import ProposalInput,ProposalAnswer,ProposalConfirm,ProposalEdit
from packages.domain.store import Conflict,InvalidInput


def candidate():
    return {'title':'科研与本地控制模块规划','summary':'综合两份原始资料。尚需明确连接约束。',
        'goals':['保存精确原件','交付经过检查的结果'],'constraints':['用户原件不可覆盖'],
        'risks':['接口资料不完整'],'assumptions':['样例预算尚待确认'],
        'nodes':[{'id':'study','title':'核对研究问题','description':'阅读原始资料并比较约束。',
                  'inputs':['原始研究说明'],'outputs':['分析说明'],'acceptance':['每项结论可查来源'],
                  'risks':['样本范围不明'],'capabilities':['source-review']},
                 {'id':'module','title':'实现本地模块','description':'按已确认连接方式实现。','depends_on':['study'],
                  'inputs':['已确认方案'],'outputs':['可检查模块'],'acceptance':['真实输入反例通过']}],
        'questions':[{'id':'connection','text':'本地模块使用什么连接方式？','reason':'材料未给出接口信息。',
                      'category':'MISSING_INPUT','blocking':True,'affects_node_ids':['module'],
                      'requested_materials':['接口说明和测试条件']} ]}


def app_for(settings):
    app=create_app(settings)
    register_local_workspace_routes(app,app.state.store,settings.data_dir)
    proposal=PlanningProposals(app.state.store,Path(__file__).resolve().parents[1],workspaces=app.state.local_workspaces)
    app.state.planning_proposals=proposal
    register_planning_report_routes(app,app.state.store,settings.data_dir)
    return app


@pytest.fixture
def word(tmp_path):
    actual=tmp_path/'实际项目与模板 空格';actual.mkdir()
    (actual/'research.md').write_bytes('# 原创研究说明\r\n需求和约束。\r\n'.encode())
    (actual/'module.txt').write_bytes('REQ: 用户指定本地模块。\r\nACCEPT: 检查原始文件。\r\n'.encode())
    settings=Settings(tmp_path/'controller');app=app_for(settings)
    with TestClient(app,base_url=settings.origin) as client:
        csrf=client.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'}).json()['csrf_token']
        client.headers.update({'Origin':settings.origin,'X-EEAS-CSRF':csrf})
        pid=client.post('/api/projects',json={'name':'真实规划与Word报告'}).json()['id']
        binding=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'folder','root':str(actual),'allowed_write_paths':['*']}).json()
        local=f'/api/projects/{pid}/local-workspaces/{binding["id"]}'
        material=client.post(local+'/materials',json={'operation_id':'selected-materials','paths':['research.md','module.txt']})
        assert material.status_code==201,material.text
        refs=[m['document_ref'] for m in material.json()['materials']]
        body=ProposalInput.model_validate({'operation_id':'original-plan','prompt':'按实际资料拆解并澄清缺口',
            'materials':refs,'mode':'manual','candidate':candidate(),
            'workspace_ref':{k:binding[k] for k in ('id','revision','binding_sha256')}})
        value=asyncio.run(app.state.planning_proposals.create(pid,body));assert value['payload']['status']=='DRAFT',value
        base=f'/api/projects/{pid}/planning/proposals/{value["id"]}/reports'
        yield client,actual,pid,value,base,local,settings


@pytest.fixture(autouse=True)
def actual_artifact_receipt(request,word):
    yield
    if not os.environ.get('EEAS_EVIDENCE_DIR'):return
    import shutil
    client,_,pid,value,_,_,settings=word
    source=settings.data_dir/'planning-reports'/pid/value['id']
    if source.exists():
        target=Path(os.environ['EEAS_EVIDENCE_DIR'])/'actual-artifacts'/request.node.name.replace('/','_').replace('\\','_')
        shutil.copytree(source,target)


def body(key='report-original',revision=1,template=None):
    value={'operation_id':key,'expected_revision':revision,'format':'docx'}
    if template:value['template_ref']=template
    return value


def export(word,request=None):
    client,_,_,_,base,_,_=word
    response=client.post(base,json=request or body());assert response.status_code in {200,201},response.text
    return response.json()


def text(raw):
    # Read the whole visible body, including generated source/trace tables.
    document=Document(BytesIO(raw))
    return '\n'.join(''.join(e.text or '' for e in p.iter(qn('w:t')))
        for p in document.element.body.iter(qn('w:p')))


def local_template(word,raw):
    client,actual,_,_,_,local,_=word
    (actual/'用户正式模板.docx').write_bytes(raw)
    value=client.get(local+'/file',params={'path':'用户正式模板.docx'});assert value.status_code==200,value.text
    return value.json()['source_ref']


def template(slots=None):
    document=Document();normal=document.styles['Normal'];normal.font.name='Cambria';normal.font.size=Pt(12)
    normal.font.color.rgb=RGBColor.from_string('304060')
    document.sections[0].header.paragraphs[0].text='USER TEMPLATE HEADER 原始页眉'
    document.sections[0].footer.paragraphs[0].text='USER ORIGINAL FOOTER 原始页脚'
    document.add_paragraph('用户保留的正式版式').runs[0].bold=True
    table=document.add_table(rows=1,cols=2);table.cell(0,0).text='原始表格';table.cell(0,1).text='原件不可丢失'
    if slots:
        for slot in slots:document.add_paragraph(slot)
    out=BytesIO();document.save(out);return out.getvalue()


def answer(word,key='answer-original',text='采用UART，预算待补充。'):
    client,_,pid,value,_,_,_=word
    return client.app.state.planning_proposals.answer(pid,value['id'],ProposalAnswer(operation_id=key,
        expected_revision=1,question_id='connection',text=text))


def test_draft_real_docx_pending_and_frozen_download_after_late_answer(word):
    client,_,_,proposal,_,_,_=word;value=export(word)
    assert value['status']=='DRAFT' and not value['replayed'] and value['proposal_ref']['sha256']==proposal['sha256']
    downloaded=client.get(value['artifact']['download_url']);assert downloaded.status_code==200
    raw=downloaded.content;assert sha(raw)==value['artifact']['sha256'] and len(raw)==value['artifact']['byte_length']
    assert downloaded.headers['content-type'].split(';')[0]==value['artifact']['media_type']
    assert text(raw).count('科研与本地控制模块规划')==1 and 'DRAFT' in text(raw)
    assert all(t in text(raw) for t in ['每项结论可查来源','真实输入反例通过','接口说明和测试条件','尚未接受答案或假设'])
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert archive.testzip() is None and b'PAGE' in archive.read('word/footer1.xml')
    pending=client.get(value['pending_json']['download_url']).json()
    assert pending['questions'][0]['status']=='AWAITING_ANSWER' and pending['pending_count']==1
    answer(word)
    replay=export(word);assert replay['replayed'] and replay['artifact']==value['artifact']
    assert client.get(replay['artifact']['download_url']).content==raw
    assert client.get(value['pending_json']['download_url']).json()==pending
    assert b'AWAITING_ANSWER' in client.get(value['pending']['download_url']).content


def test_answered_but_not_accepted_new_snapshot_and_explicit_confirmed_revision(word):
    first=export(word);saved_answer=answer(word);second=export(word,body('report-with-answer'))
    client,_,pid,value,_,_,_=word
    assert second['status']=='DRAFT' and first['artifact']['id']!=second['artifact']['id']
    pending=client.get(second['pending_json']['download_url']).json()
    assert pending['questions'][0]['status']=='ANSWERED_PENDING_ACCEPTANCE' and pending['pending_count']==1
    confirm=ProposalConfirm.model_validate({'operation_id':'accept-plan','expected_revision':1,'proposal_sha256':value['sha256'],
        'user_confirmed':True,'resolutions':[{'question_id':'connection','mode':'ANSWER_ACCEPTED','answer_id':saved_answer['answer_id'],'reason':'本次用户明确接受'}]})
    accepted=client.app.state.planning_proposals.confirm(pid,value['id'],confirm)
    final=export(word,body('formal',accepted['revision']));assert final['status']=='CONFIRMED'
    pending_final=client.get(final['pending_json']['download_url']).json()
    assert pending_final['questions'][0]['status']=='RESOLVED' and pending_final['pending_count']==0
    assert '采用UART' in text(client.get(final['artifact']['download_url']).content)
    assert first['proposal_ref']['revision']==1 and final['proposal_ref']['revision']==2
    assert client.get(first['artifact']['download_url']).status_code==200


def test_user_plain_local_docx_preserves_format_header_footer_and_full_append(word):
    raw=template();ref=local_template(word,raw);value=export(word,body('template',template=ref))
    client,actual,_,_,_,_,_=word;result=client.get(value['artifact']['download_url']).content
    document=Document(BytesIO(result))
    assert document.styles['Normal'].font.name=='Cambria' and document.styles['Normal'].font.size==Pt(12)
    assert document.sections[0].header.paragraphs[0].text=='USER TEMPLATE HEADER 原始页眉'
    assert document.sections[0].footer.paragraphs[0].text=='USER ORIGINAL FOOTER 原始页脚'
    assert document.tables[0].cell(0,0).text=='原始表格'
    manual=[p for p in document.paragraphs if p.text=='用户保留的正式版式']
    assert len(manual)==1 and manual[0].runs[0].bold and '任务网络与分解' in text(result)
    assert (actual/'用户正式模板.docx').read_bytes()==raw
    source=client.get(value['source_snapshot']['download_url']).json();assert source['template_ref']==ref


def test_named_slots_preserve_format_xml_escaping_and_complete_network(word):
    ref=local_template(word,template(['{{ title }}','{{ summary }}','{{ report_body }}']))
    client,_,pid,value,_,_,_=word;new=candidate();new['summary']='用户 <真实> & 内容 {{ literal }}'
    revised=client.app.state.planning_proposals.edit(pid,value['id'],ProposalEdit.model_validate({'operation_id':'edit-for-xml',
        'expected_revision':1,'candidate':new,'change_reason':'补充有XML字符的真实内容'}))
    result=export(word,body('slots',revised['revision'],ref));raw=client.get(result['artifact']['download_url']).content
    assert new['summary'] in text(raw) and '{{ report_body }}' not in text(raw)
    assert '真实输入反例通过' in text(raw) and 'USER TEMPLATE HEADER' in Document(BytesIO(raw)).sections[0].header.paragraphs[0].text
    assert 'research.md' in text(raw) and '科研与本地控制模块规划' in text(raw)
    source_part=client.get(result['source_snapshot']['download_url']);assert source_part.status_code==200
    assert sha(source_part.content)==result['source_snapshot']['sha256']
    source=source_part.json()
    assert source['proposal']['sha256']==revised['sha256'] and digest(source['proposal']['payload'])==revised['sha256']
    assert source['proposal']['payload']['candidate']==revised['payload']['candidate']
    assert source['pending']['proposal_ref']==result['proposal_ref']
    with zipfile.ZipFile(BytesIO(raw)) as archive:assert b'&lt;' in archive.read('word/document.xml')


@pytest.mark.parametrize('slot',['{{ unknown }}','{{ title.__class__ }}','{% for x in range(999) %}','{{ title'])
def test_template_code_unknown_or_incomplete_slots_fail_without_success(word,slot):
    ref=local_template(word,template([slot]));client,_,_,_,base,_,settings=word
    result=client.post(base,json=body('unsafe-template',template=ref));assert result.status_code==422,result.text
    operation=next((settings.data_dir/'planning-reports').glob('**/operations/unsafe-template'))
    assert not (operation/'result.json').exists() and len(list(operation.glob('*-failure.json')))==1


@pytest.mark.parametrize('issue',['external','macro','escape','duplicates','invalidzip'])
def test_unsafe_ooxml_templates_rejected_before_renderer(word,issue):
    original=template()
    if issue=='invalidzip':raw=b'PK\x03\x04not-real-zip'
    else:
        out=BytesIO()
        with zipfile.ZipFile(BytesIO(original)) as incoming,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
            for name in incoming.namelist():
                raw_part=incoming.read(name)
                if issue=='external' and name=='word/_rels/document.xml.rels':
                    raw_part=raw_part.replace(b'</Relationships>',b'<Relationship Id="outside" Type="evil" Target="file:///outside" TargetMode="External"/></Relationships>')
                target.writestr(name,raw_part)
            if issue=='macro':target.writestr('word/vbaProject.bin',b'macro')
            if issue=='escape':target.writestr('../escape.xml',b'<outside/>')
            if issue=='duplicates':target.writestr('word/DOCUMENT.xml',b'<fake/>')
        raw=out.getvalue()
    ref=local_template(word,raw);client,_,_,_,base,_,_=word
    response=client.post(base,json=body('unsafe-ooxml',template=ref));assert response.status_code==422,response.text


def test_template_drift_cross_project_forged_hash_and_escape_are_rejected(word):
    ref=local_template(word,template());client,actual,_,_,base,_,_=word
    for key,value in [('sha256','0'*64),('binding_sha256','1'*64),('project_id','outside-project'),('path','../outside.docx')]:
        forged={**ref,key:value};response=client.post(base,json=body('forged-'+key,template=forged))
        assert response.status_code in {404,409,422},response.text
    (actual/'用户正式模板.docx').write_bytes(template(['{{ summary }}']))
    response=client.post(base,json=body('drift-template',template=ref));assert response.status_code==409,response.text


def test_saved_template_report_replays_after_original_changes_and_restart(word):
    ref=local_template(word,template());request=body('saved-local-template',template=ref);value=export(word,request)
    client,actual,pid,proposal,_,_,settings=word;(actual/'用户正式模板.docx').write_bytes(b'changed external original')
    restarted=app_for(settings).state.planning_reports
    replay=restarted.generate(pid,proposal['id'],PlanningReportInput.model_validate(request))
    assert replay['replayed'] and replay['artifact']==value['artifact']
    assert restarted.download(pid,proposal['id'],value['artifact']['id'])[0]==client.get(value['artifact']['download_url']).content


def test_source_drift_report_explicitly_keeps_review_status_and_original_sources(word):
    client,actual,_,_,_,_,_=word;(actual/'research.md').write_bytes(b'external new source')
    value=export(word);assert value['review_status']=='NEEDS_REVIEW' and value['status']=='DRAFT'
    snap=client.get(value['source_snapshot']['download_url']).json()
    assert '# 原创研究说明' in snap['proposal']['payload']['source_receipt']['documents'][0]['text_or_selected_chunks']
    assert '来源已变化，需复核' in text(client.get(value['artifact']['download_url']).content)
    assert snap['proposal']['review_status']=='NEEDS_REVIEW'
    assert digest(snap)==value['artifact']['id']


def test_concurrent_same_source_operation_generates_once_and_changed_input_conflicts(word):
    client,_,pid,value,base,_,_=word;service=client.app.state.planning_reports;calls=[]
    def actual(snapshot,template_raw):calls.append(snapshot);return render_docx(snapshot,template_raw)
    with patch('packages.domain.planning_reports.render_docx',side_effect=actual):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(lambda _:service.generate(pid,value['id'],PlanningReportInput.model_validate(body())),range(5)))
    assert len(calls)==1 and len({v['artifact']['id'] for v in results})==1
    assert sum(not r['replayed'] for r in results)==1
    assert client.post(base,json=body(revision=2)).status_code==409
    equivalent=export(word,body('same-source-different-operation'));assert equivalent['replayed'] and equivalent['artifact']==results[0]['artifact']


def test_generator_failure_is_retained_and_retry_does_not_report_fake_success(word):
    client,_,pid,value,_,_,settings=word;service=client.app.state.planning_reports;request=PlanningReportInput.model_validate(body())
    with patch('packages.domain.planning_reports.render_docx',side_effect=InvalidInput('original deterministic render failure')):
        with pytest.raises(InvalidInput,match='original deterministic'):service.generate(pid,value['id'],request)
    op=settings.data_dir/'planning-reports'/pid/value['id']/'operations'/request.operation_id
    failed=list(op.glob('*-failure.json'));assert len(failed)==1 and not (op/'result.json').exists()
    assert 'original deterministic render failure' in failed[0].read_text()
    saved=service.generate(pid,value['id'],request);assert saved['artifact']['byte_length']>0
    assert failed[0].exists() and len(list(op.glob('*-success.json')))==1


@pytest.mark.parametrize('change',['template','source'])
def test_live_template_or_source_change_during_render_does_not_register_false_current(word,change):
    client,actual,pid,value,_,_,settings=word
    request=PlanningReportInput.model_validate(body('changing-render',template=local_template(word,template()) if change=='template' else None))
    def changing(snapshot,raw):
        result=render_docx(snapshot,raw)
        (actual/('用户正式模板.docx' if change=='template' else 'research.md')).write_bytes(b'externally changed during rendering')
        return result
    with patch('packages.domain.planning_reports.render_docx',side_effect=changing):
        with pytest.raises(Conflict):client.app.state.planning_reports.generate(pid,value['id'],request)
    operation=settings.data_dir/'planning-reports'/pid/value['id']/'operations'/request.operation_id
    assert not (operation/'result.json').exists() and len(list(operation.glob('*-failure.json')))==1


def test_content_address_still_binds_snapshot_if_manifest_checksum_is_rewritten(word):
    value=export(word);client,_,pid,proposal,_,_,settings=word
    directory=settings.data_dir/'planning-reports'/pid/proposal['id']/'artifacts'/value['artifact']['id']
    snapshot=json.loads((directory/'source.json').read_bytes());snapshot['proposal']['answers'].append({'text':'forged answer'})
    raw=(canonical(snapshot)+'\n').encode();(directory/'source.json').write_bytes(raw)
    wrapper=json.loads((directory/'manifest.json').read_bytes());manifest=wrapper['payload']
    manifest['files']['source_snapshot'].update(sha256=sha(raw),byte_length=len(raw));manifest['answer_view_sha256']=digest(snapshot['proposal']['answers'])
    (directory/'manifest.json').write_text(canonical({'payload':manifest,'sha256':digest(manifest)}))
    assert client.get(value['artifact']['download_url']).status_code==409


@pytest.mark.parametrize('part',['report.docx','pending.md','pending.json','source.json','manifest.json'])
def test_real_artifact_corruption_prevents_replay_and_download(word,part):
    value=export(word);client,_,pid,proposal,base,_,settings=word
    path=settings.data_dir/'planning-reports'/pid/proposal['id']/'artifacts'/value['artifact']['id']/part
    path.write_bytes(b'corrupted original artifact')
    assert client.post(base,json=body()).status_code==409
    assert client.get(value['artifact']['download_url']).status_code==409


def test_exact_current_revision_cross_project_and_csrf_boundaries(word):
    value=export(word);client,_,pid,proposal,base,_,_=word
    assert client.post(base,json=body('wrong-revision',2)).status_code==404
    edited=client.app.state.planning_proposals.edit(pid,proposal['id'],ProposalEdit.model_validate({'operation_id':'next-revision',
        'expected_revision':1,'candidate':candidate(),'change_reason':'新的用户纠正'}))
    assert client.post(base,json=body('obsolete-revision',1)).status_code==409
    assert export(word,body('new-revision',edited['revision']))['proposal_ref']['revision']==2
    other=client.post('/api/projects',json={'name':'另一项目'}).json()['id']
    assert client.get(value['artifact']['download_url'].replace('/'+pid+'/','/'+other+'/')).status_code==404
    assert client.post(base.replace('/'+pid+'/','/'+other+'/'),json=body('cross-project')).status_code==404
    # The fixture already owns the App lifespan. A second lifespan would
    # incorrectly bind its existing supervisor to a different event loop.
    outsider=TestClient(client.app,base_url='http://127.0.0.1:8765')
    try:
        assert not outsider.cookies
        assert outsider.post(base,json=body('no-session')).status_code==401
    finally:
        outsider.close()


def test_large_network_keeps_every_node_and_never_truncates_at_taskbook_limit(word):
    client,_,pid,value,_,_,_=word;plan=candidate();plan['nodes']=[];plan['questions']=[]
    for i in range(125):
        plan['nodes'].append({'id':'node-'+str(i),'title':'独立节点'+str(i),'description':'完整保留第'+str(i)+'项',
            'outputs':['产物'+str(i)],'acceptance':['检查'+str(i)],'depends_on':['node-'+str(i-1)] if i else []})
    revised=client.app.state.planning_proposals.edit(pid,value['id'],ProposalEdit.model_validate({'operation_id':'large-plan',
        'expected_revision':1,'candidate':plan,'change_reason':'完整大任务网络'}))
    result=export(word,body('large-report',revised['revision']));content=text(client.get(result['artifact']['download_url']).content)
    assert all('完整保留第'+str(i)+'项' in content for i in range(125))


def test_real_loopback_disconnect_after_post_generation_retries_saved_bytes_once(word):
    client,_,pid,proposal,base,_,_=word;app=client.app;entered=Event();released=Event();calls=[]
    def actual(snapshot,template_raw):
        calls.append(snapshot);entered.set();assert released.wait(15)
        return render_docx(snapshot,template_raw)
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    # The original same-origin middleware is configured to settings.origin.
    # HTTP Host/Origin use that exact authority; the TCP target may be any loopback port.
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',lifespan='off'))
    thread=Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and time.monotonic()<deadline:time.sleep(.02)
    assert server.started
    cookie='; '.join(k+'='+v for k,v in client.cookies.items());csrf=client.headers['X-EEAS-CSRF']
    request=json.dumps(body()).encode()
    wire=(f'POST {base} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nOrigin: http://127.0.0.1:8765\r\n'
          f'Cookie: {cookie}\r\nX-EEAS-CSRF: {csrf}\r\nContent-Type: application/json\r\nContent-Length: {len(request)}\r\nConnection: close\r\n\r\n').encode()+request
    try:
        with patch('packages.domain.planning_reports.render_docx',side_effect=actual):
            sock=socket.create_connection(('127.0.0.1',port));sock.sendall(wire)
            assert entered.wait(10);sock.close();released.set()
            with httpx.Client(base_url=origin,trust_env=False,headers={'Host':'127.0.0.1:8765',
                'Origin':'http://127.0.0.1:8765','Cookie':cookie,'X-EEAS-CSRF':csrf}) as http:
                retry=http.post(base,json=body());assert retry.status_code==200,retry.text
                saved=retry.json();assert saved['replayed']
                raw=http.get(saved['artifact']['download_url']).content;assert sha(raw)==saved['artifact']['sha256']
            assert len(calls)==1
    finally:
        released.set();server.should_exit=True;thread.join(10)
