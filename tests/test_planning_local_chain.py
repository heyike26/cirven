"""Local files -> actual source-bound plan -> questions -> project prompt bytes."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from packages.domain.provider_settings import ProviderInput
from packages.planning.client import call_planning_tool
from test_planning_proposals import candidate,confirm_body
from test_project_intake import project


def prepare(client,tmp_path,*,grant=True):
    actual=tmp_path.parent/(tmp_path.name+' 工程目录 中文');actual.mkdir()
    (actual/'资料.md').write_bytes('# 项目资料\r\n需要可验证原型。\r\n接口未说明。\r\n'.encode())
    pid=project(client)
    bound=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'folder',
        'root':str(actual),'allowed_write_paths':['eeas-harness/'] if grant else []})
    assert bound.status_code==201,bound.text
    binding=bound.json()
    materials=client.post(f'/api/projects/{pid}/local-workspaces/{binding["id"]}/materials',json={
        'operation_id':'read-selected','paths':['资料.md']})
    assert materials.status_code==201,materials.text
    ref={k:binding[k] for k in ['id','revision','binding_sha256']}
    body={'operation_id':'decompose','prompt':'理解资料并规划','mode':'manual','workspace_ref':ref,
        'materials':[m['document_ref'] for m in materials.json()['materials']], 'candidate':candidate()}
    response=client.post(f'/api/projects/{pid}/planning/proposals',json=body)
    assert response.status_code==201,response.text
    return pid,actual,binding,response.json()


def approve(client,pid,value):
    base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    body=confirm_body(value,[{'question_id':'interface','mode':'ASSUMPTION_ACCEPTED',
        'assumption':'接口按UART设计，真实规格仍须实测复核。','reason':'用户明确接受作为当前计划假设'}])
    result=client.post(base+'/confirm',json=body)
    assert result.status_code==200,result.text
    return result.json()


def test_real_folder_drift_without_material_refresh_marks_plan_and_keeps_pending(client,tmp_path):
    pid,actual,_,value=prepare(client,tmp_path)
    source=value['payload']['source_receipt']['documents'][0]
    assert source['local_file_source_ref']['path']=='资料.md'
    (actual/'资料.md').write_text('外部用户改变资料，数据库尚未刷新。')
    base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    old=client.get(base).json();assert old['review_status']=='NEEDS_REVIEW'
    assert old['payload']==value['payload']
    assert client.get(base+'/pending/markdown').status_code==200
    assert client.post(base+'/confirm',json=confirm_body(value)).status_code==409


def test_confirmed_harness_writes_checked_version_directory_preserving_source(client,tmp_path):
    pid,actual,binding,value=prepare(client,tmp_path);original=(actual/'资料.md').read_bytes()
    accepted=approve(client,pid,value)
    base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    body={'operation_id':'write-prompts','expected_revision':accepted['revision'],
        'proposal_sha256':accepted['sha256'],'workspace_ref':{k:binding[k] for k in ['id','revision','binding_sha256']},'directory':'eeas-harness'}
    result=client.post(base+'/harness/export',json=body);assert result.status_code==200,result.text
    saved=result.json();assert saved['status']=='EXPORTED_CONFIRMED_PLAN' and not saved['execution_started']
    for file in saved['files']:
        raw=(actual/file['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==file['sha256']
    directory=actual/saved['version_directory']
    assert len(list(directory.glob('task-*.md')))==3
    assert (directory/'manifest.json').exists() and (directory/'pending-confirmation.md').exists()
    assert 'UART' in (directory/'task-design.md').read_text()
    assert (actual/'资料.md').read_bytes()==original
    replay=client.post(base+'/harness/export',json=body).json();assert replay['replayed']
    assert all(f['current'] for f in replay['file_checks'])
    (directory/'task-design.md').write_text('用户手工变更，不能覆盖。')
    replay=client.post(base+'/harness/export',json=body).json();assert not all(f['current'] for f in replay['file_checks'])
    assert (directory/'task-design.md').read_text()=='用户手工变更，不能覆盖。'


def test_export_rejects_draft_and_missing_grant_without_creating_output(client,tmp_path):
    pid,actual,binding,value=prepare(client,tmp_path,grant=False)
    body={'operation_id':'export','expected_revision':value['revision'],'proposal_sha256':value['sha256'],
        'workspace_ref':{k:binding[k] for k in ['id','revision','binding_sha256']},'directory':'eeas-harness'}
    base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    assert client.post(base+'/harness/export',json=body).status_code==422
    assert not (actual/'eeas-harness').exists()


def test_original_mcp_sdk_client_calls_real_project_stdio_process(client,tmp_path,monkeypatch):
    from packages.planning import client as planning_client
    commands=[];actual_stdio=planning_client.stdio_client
    def observe_stdio(parameters):
        commands.append([parameters.command,*parameters.args]);return actual_stdio(parameters)
    monkeypatch.setattr(planning_client,'stdio_client',observe_stdio)
    pid,_,_,value=prepare(client,tmp_path)
    source=client.app.state.store.source_root
    data=client.app.state.settings.data_dir
    result=asyncio.run(call_planning_tool(source,data,pid,'pending_questions',{'proposal_id':value['id']}))
    assert not result.is_error
    dumped=result.model_dump_json()
    assert 'interface' in dumped and 'AWAITING_ANSWER' in dumped
    other=project(client)
    wrong=asyncio.run(call_planning_tool(source,data,other,'pending_questions',{'proposal_id':value['id']}))
    assert wrong.is_error
    assert len(commands)==2 and all(command[1:4]==['-B','-m','packages.planning.mcp_server'] for command in commands)
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM task_runs').fetchone()[0]==0


def test_api_toolkit_reports_structure_gaps_without_inventing_verification(client):
    value=candidate();value['nodes'][0]['acceptance']=[]
    result=client.post('/api/planning/tools/acceptance-coverage',json={'candidate':value})
    assert result.status_code==200 and 'acceptance' in result.json()['nodes'][0]['missing']
    assert not result.json()['verified_execution']
    templates=client.get('/api/planning/tools/templates').json()
    assert templates['component']['version']=='v1.1.3' and not templates['runtime_hooks_executed']


def test_custom_provider_is_versioned_private_and_never_exposes_key(client):
    body={'operation_id':'provider-1','id':'private-model','provider_url':'http://127.0.0.1:12345/v1',
          'model_id':'original-local-model','api_key':'original-fixture-credential'}
    result=client.post('/api/planning/providers',json=body);assert result.status_code==201,result.text
    assert body['api_key'] not in result.text and body['api_key'] not in client.get('/api/planning/providers').text
    first=result.json();assert first['profile_ref']['revision']==1 and first['model_access']=='NOT_VALIDATED'
    assert client.post('/api/planning/providers',json=body).json()==first
    changed={**body,'operation_id':'provider-2','expected_revision':1,'model_id':'changed-model','api_key':'changed-original-credential'}
    second=client.post('/api/planning/providers',json=changed);assert second.status_code==201
    service=client.app.state.provider_settings
    old=service.configuration('private-model',1)
    assert service.secret(old['secret_ref'])==body['api_key'] and old['model_id']==body['model_id']
    assert client.post('/api/planning/providers',json={**body,'operation_id':'stale'}).status_code==409
    if __import__('os').name=='posix':
        assert (service.path('secrets',old['secret_ref'][7:]).stat().st_mode & 0o777)==0o600


@pytest.mark.parametrize('url',['https://user:password@example.invalid/v1','http://127.0.0.1/v1?key=x','file:///etc/v1'])
def test_provider_endpoint_rejects_implicit_credentials_and_non_http(client,url):
    result=client.post('/api/planning/providers',json={'operation_id':'bad','id':'bad','provider_url':url,
        'model_id':'a','api_key':'original'})
    assert result.status_code==422


def test_actual_original_cli_through_product_provider_materials_proposal_api(client,tmp_path):
    from test_opencode_planning import OriginalPlanningWire,KEY,MODEL
    wire=OriginalPlanningWire().start()
    try:
        actual=tmp_path.parent/(tmp_path.name+' 原生规划项目');actual.mkdir()
        raw=b'REQ: preserve immutable source hashes\nREQ: ask for missing target environment\n'
        (actual/'original.md').write_bytes(raw)
        pid=project(client)
        bound=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'native-folder','root':str(actual)})
        assert bound.status_code==201,bound.text
        binding=bound.json()
        materials=client.post(f'/api/projects/{pid}/local-workspaces/{binding["id"]}/materials',json={
            'operation_id':'native-source','paths':['original.md']}).json()
        profile=client.post('/api/planning/providers',json={'operation_id':'native-profile','id':'original-native',
            'provider_url':wire.base_url,'model_id':MODEL,'api_key':KEY})
        assert profile.status_code==201,profile.text
        body={'operation_id':'original-product-native','prompt':'拆解实际文件，提出所需资料；不执行任务。',
            'materials':[m['document_ref'] for m in materials['materials']],
            'workspace_ref':{k:binding[k] for k in ['id','revision','binding_sha256']},
            'provider_profile_ref':profile.json()['profile_ref'],
            'budget':{'max_calls':1,'timeout_seconds':45,'max_output_tokens':4096}}
        result=client.post(f'/api/projects/{pid}/planning/proposals',json=body)
        assert result.status_code==201,result.text
        value=result.json();execution=value['payload']['execution']
        assert execution['status']=='RETURNED' and execution['session_id'].startswith('ses_')
        assert len(value['flow']['nodes'])==3 and value['payload']['candidate']['questions'][0]['blocking']
        assert len(wire.requests)==execution['usage']['rpc_attempts']==1
        assert (actual/'original.md').read_bytes()==raw and not value['payload']['implementation_started']
        pending=client.get(f'/api/projects/{pid}/planning/proposals/{value["id"]}/pending').json()
        assert pending['pending_count']==1
        status=client.get(f'/api/projects/{pid}/planning/operations/original-product-native').json()
        assert status['native_execution']['id']==execution['execution_id'] and status['events']
        assert KEY not in json.dumps(value)
        assert client.post(f'/api/projects/{pid}/planning/proposals',json=body).json()==value and len(wire.requests)==1
    finally:wire.close()


def test_real_plan_word_and_pending_snapshot_remain_after_later_answer(client,tmp_path):
    from zipfile import ZipFile
    from io import BytesIO
    pid,_,_,value=prepare(client,tmp_path)
    base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    body={'operation_id':'draft-report','expected_revision':1,'format':'docx'}
    report=client.post(base+'/reports',json=body)
    assert report.status_code==201,report.text
    saved=report.json();assert saved['status']=='DRAFT'
    word=client.get(saved['artifact']['download_url']);assert word.status_code==200
    assert hashlib.sha256(word.content).hexdigest()==saved['artifact']['sha256']
    with ZipFile(BytesIO(word.content)) as archive:
        xml=archive.read('word/document.xml').decode()
        assert all(text in xml for text in ['明确接口与方案','实现独立原型','整理原始资料','设备使用什么接口'])
    pending=client.get(saved['pending']['download_url']);assert b'AWAITING_ANSWER' in pending.content
    answer=client.post(base+'/answers',json={'operation_id':'late-answer','expected_revision':1,
        'question_id':'interface','text':'UART，现在补充接口资料。'})
    assert answer.status_code==200
    assert client.get(base+'/pending').json()['questions'][0]['status']=='ANSWERED_PENDING_ACCEPTANCE'
    assert client.get(saved['pending']['download_url']).content==pending.content
    assert client.post(base+'/reports',json=body).json()['replayed']
    assert client.get(saved['artifact']['download_url']).content==word.content


def test_computer_template_from_separate_readonly_folder_preserves_format(client,tmp_path):
    from docx import Document
    from io import BytesIO
    pid,_,binding,value=prepare(client,tmp_path)
    template_folder=tmp_path.parent/(tmp_path.name+' 单独模板');template_folder.mkdir()
    document=Document();document.sections[0].header.paragraphs[0].text='原模板页眉'
    document.add_paragraph('用户模板原始内容');document.save(template_folder/'公司格式.docx')
    original=(template_folder/'公司格式.docx').read_bytes()
    bound=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'template-only',
        'root':str(template_folder),'allowed_write_paths':[]}).json()
    source=client.get(f'/api/projects/{pid}/local-workspaces/{bound["id"]}/file',params={'path':'公司格式.docx'}).json()['source_ref']
    assert source['workspace_id']!=binding['id']
    result=client.post(f'/api/projects/{pid}/planning/proposals/{value["id"]}/reports',json={
        'operation_id':'user-template-report','expected_revision':1,'format':'docx','template_ref':source})
    assert result.status_code==201,result.text
    raw=client.get(result.json()['artifact']['download_url']).content
    parsed=Document(BytesIO(raw))
    assert '原模板页眉' in '\n'.join(p.text for p in parsed.sections[0].header.paragraphs)
    texts='\n'.join(p.text for p in parsed.paragraphs)
    assert '用户模板原始内容' in texts and '明确接口与方案' in texts
    assert (template_folder/'公司格式.docx').read_bytes()==original
    assert client.app.state.planning_proposals.get(pid,value['id'])['payload']['workspace_ref']['id']==binding['id']
