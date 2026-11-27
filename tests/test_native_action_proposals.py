"""Real ASGI, signed local drafts and original readonly tool dispatch; no native input."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
import pytest

from apps.api.native_action_proposals import NativeAppendProposalInput
from packages.adapters.opencode_desktop import _ExecutionBroker
from packages.adapters.task_resources import record_scope
from packages.domain.tasks import canonical, digest
from packages.domain.store import Conflict
from test_native_messages import application, TOKEN
from test_native_review import FrozenPixels


def check(value, message='native action proposal requirement failed'):
    if not value:
        raise AssertionError(message)


@pytest.fixture
def action(tmp_path, request):
    app, native, profile = application(tmp_path, SimpleNamespace(server=SimpleNamespace(server_port=9)))
    native.context = FrozenPixels()
    pid = None if getattr(request,'param',False) else app.state.store.create_project('原动作提案', '')['id']
    raw = '请在当前文稿末尾追加：中文🙂'
    body = {'operation_id': 'original-parent', 'raw_text': raw, 'project_id': pid,
        'conversation_id': 'original-conversation', 'context_ref': native.context.context_ref,
        'provider_profile_ref': {'id': profile['id'], 'revision': 1, 'sha256': digest(profile)}}
    record, request = native._prepare_ordinary(body, TOKEN)
    check('propose_native_append' in request['method_ids'])
    row = native.transport.prepare(request)
    with app.state.store.connect() as c:
        c.execute("UPDATE agent_executions SET execution_status='RUNNING' WHERE id=?", (row['id'],))
    record['execution'] = {'project_id': row['project_id'], 'id': row['id'], 'session_id': None}
    native.ws._write(native.path(body['operation_id']), record)
    request = json.loads(row['request'])
    binding = {'request': request, 'execution_ref': {'project_id': row['project_id'],
        'execution_id': row['id'], 'request_sha256': row['request_hash']}}
    span = {'start': raw.index('中文'), 'end': len(raw), 'quote': '中文🙂'}
    with TestClient(app, headers={'X-EEAS-Desktop-Host': TOKEN}) as client:
        yield SimpleNamespace(app=app,native=native,body=body,record=record,request=request,row=row,binding=binding,span=span,client=client)


def returned(s):
    record=s.native.ws._read(s.native.path(s.body['operation_id']),authority=True)
    record['status']='RETURNED';s.native.ws._write(s.native.path(s.body['operation_id']),record)
    with s.app.state.store.connect() as c:
        c.execute("UPDATE agent_executions SET execution_status='RETURNED',result=? WHERE id=?",
            (canonical({'candidate_json':{'text':'已准备精确追加提案，等待用户决定。','questions':[]}}),s.row['id']))
    result=s.client.get('/api/desktop-host/native-messages/'+s.body['operation_id'])
    check(result.status_code==200,result.text)
    return result.json()


@pytest.mark.parametrize('action',[False,True],indirect=True)
def test_real_original_readonly_dispatch_returns_bound_draft_and_asgi_get_projects_without_input(action):
    s=action;methods=s.native.transport.method_descriptors(s.request)
    context={'closing':False,'tool_attempts':0,'tool_calls':0,'scope':{'methods_sha256':digest(methods)},'session_id':'recorded-original-bridge-fixture'}
    adapter=s.native.transport
    call={'method':'propose_native_append','arguments':s.span}
    with pytest.raises(Conflict,match='callback owner changed'):
        asyncio.run(adapter._invoke(s.row,s.request,context,call))
    async def dispatch():
        context['loop']=asyncio.get_running_loop()
        broker=_ExecutionBroker(adapter,s.row,s.request,context)
        context['broker']=broker
        try:
            with record_scope(adapter,s.row['project_id'],s.row) as budget:
                context['task_resource_budget']=budget
                adapter.active[s.row['id']]=context
                headers={'Authorization':'Bearer '+broker.key, 'X-EEAS-Execution':s.row['id'],
                         'X-EEAS-Request-SHA256':s.row['request_hash']}
                async with httpx.AsyncClient(trust_env=False) as http:
                    response=await http.post(broker.url+'/invoke',headers=headers,json=call)
                    response.raise_for_status()
                    return response.json()
        finally:
            adapter.active.pop(s.row['id'],None)
            await asyncio.to_thread(broker.close)
    envelope=asyncio.run(dispatch())
    check(not envelope.get('isError'),canonical(envelope));result=json.loads(envelope['content'][0]['text'])['result']
    check(result['status']=='RECORDED_DRAFT' and not result['execution_authorized'])
    pending=s.client.get('/api/desktop-host/native-messages/'+s.body['operation_id']).json()['native_action_proposals'][0]
    check(pending['status']=='READONLY' and pending['request'] is None and pending['reason_code']=='WAITING_PARENT_RESULT')
    value=returned(s);proposal=value['native_action_proposals'][0]
    check(proposal['status']=='PROPOSED' and proposal['parent_request_sha256']==digest(s.record['request']))
    check(proposal['source']['project_id']==s.body['project_id'] and proposal['source']['context_ref']==s.body['context_ref'])
    request=proposal['request'];check(request['raw_text']==s.body['raw_text'] and request['payload_source']['span']==s.span)
    check(request['operation_id']=='native-append-'+digest([s.body['operation_id'],s.record['request_sha256'],s.span])[:48])
    check(proposal['proposal_ref']['id']==proposal['proposal_ref']['sha256'])
    check(value['text']=='已准备精确追加提案，等待用户决定。' and not value['automatic_input_authorized'] and not value['changes_applied'])
    check(not s.native.ws._path('native-append').exists() and s.native.transport.active=={})
    out=os.environ.get('EEAS_NATIVE_ACTION_PRODUCER')
    if out:
        p=Path(out);p.mkdir(parents=True,exist_ok=True);(p/'native-action-producer.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def test_same_parent_concurrent_identical_proposals_are_one_immutable_record_and_other_span_is_rejected(action):
    s=action
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:s.native.action_proposals.propose(s.span,s.binding),range(4)))
    check(all(row==results[0] for row in results))
    path=s.native.action_proposals.path(s.body['operation_id']);before=path.read_bytes()
    with pytest.raises(Conflict):s.native.action_proposals.propose({'start':0,'end':1,'quote':'请'},s.binding)
    check(path.read_bytes()==before)


@pytest.mark.parametrize('change',['parent','profile','project','message','sha','historical','unknown','file_scope','quote'])
def test_bad_bound_identity_and_unknown_never_publish_or_replay_a_proposal(action,change):
    s=action;binding=deepcopy(s.binding);span=deepcopy(s.span)
    if change=='parent':binding['request']['operation_id']='another-parent'
    elif change=='profile':binding['request']['provider_profile_ref']['sha256']='f'*64
    elif change=='project':binding['request']['project_id']='another-project'
    elif change=='message':binding['request']['desktop_intent']['user_message_ref']['sha256']='f'*64
    elif change=='quote':span['quote']='模型重写'
    elif change=='sha':binding['execution_ref']['request_sha256']='f'*64
    else:
        record=deepcopy(s.record)
        if change=='historical':record['generation']='prior-host'
        elif change=='unknown':record['status']='UNKNOWN_OUTCOME'
        elif change=='file_scope':record['request']['file_link_ref']={'id':'f'*64,'revision':1,'sha256':'f'*64,'project_id':s.body['project_id']};record['request_sha256']=digest(record['request'])
        s.native.ws._write(s.native.path(s.body['operation_id']),record)
    with pytest.raises(Exception):s.native.action_proposals.propose(span,binding)
    check(not s.native.action_proposals.path(s.body['operation_id']).exists())


def test_historical_expired_unknown_and_bad_provenance_keep_original_answer_readonly(action):
    s=action;s.native.action_proposals.propose(s.span,s.binding)
    original=returned(s);path=s.native.action_proposals.path(s.body['operation_id']);saved=s.native.ws._read(path,authority=False)
    for change in ['historical','expired','unknown','sha']:
        copy=deepcopy(saved);record=s.native.ws._read(s.native.path(s.body['operation_id']),authority=False)
        if change=='historical':copy['payload']['host_generation']='previous-host'
        elif change=='expired':copy['payload']['admission']['deadline']='2000-01-01T00:00:00+00:00'
        elif change=='unknown':record['status']='UNKNOWN_OUTCOME'
        else:copy['proposal_ref']['sha256']='f'*64
        if change in {'historical','expired'}:
            sha=digest(copy['payload']);copy['proposal_ref']={'id':sha,'revision':1,'sha256':sha}
        s.native.ws._write(path,copy);s.native.ws._write(s.native.path(s.body['operation_id']),record)
        value=s.client.get('/api/desktop-host/native-messages/'+s.body['operation_id']).json()
        check(value.get('text')==original['text']);p=value['native_action_proposals'][0]
        check(p['status']=='READONLY' and p['request'] is None and not p['execution_authorized'])
        s.native.ws._write(path,saved);record['status']='RETURNED';s.native.ws._write(s.native.path(s.body['operation_id']),record)


@pytest.mark.parametrize('value',[{'start':True,'end':2,'quote':'x'},{'start':0,'end':1,'quote':'a\nb'},
    {'start':0,'end':129,'quote':'😀'*129},{'start':0,'end':1,'quote':'\ud800'},
    {'start':0,'end':1,'quote':'a','approved':True}])
def test_model_span_cannot_add_authority_controls_or_partial_unicode(value):
    with pytest.raises(Exception):NativeAppendProposalInput.model_validate(value)
