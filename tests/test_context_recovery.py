"""AUTHORED_NOT_RUN: real recovery/canonical locks, Store, aliases and journals.

Only original stop/session observation is an explicit UNIT boundary. No SDK,
model, process termination, Windows, IDE, draft erasure or native acceptance.
"""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from apps.api.canonical_conversations import CanonicalConversations
from apps.api.context_recovery import ContextRecovery, ContextRestart
from apps.api.conversation_creation import ConversationCreation, CreateConversation
from packages.agent_sessions.service import AgentSessions
from packages.domain.conversation_threads import ConversationThreads
from packages.domain.store import Store, Conflict
from packages.domain.tasks import digest


@pytest.fixture
def recovery(tmp_path):
    store=Store(tmp_path/'data/eeas.sqlite3');pid=store.create_project('Recovery','UNIT stop boundary')['id']
    threads=ConversationThreads(store);creation=ConversationCreation(store,threads)
    original=creation.create(CreateConversation(operation_id='old_chat',project_id=pid))
    ref=original['conversation_ref'];sid='a'*32
    manifest={'id':sid,'project_id':pid,'request':{'conversation_ref':ref,
        'provider_profile_ref':{'id':'profile','revision':1,'sha256':'b'*64}}}
    owner=object.__new__(AgentSessions);owner.store=store;owner.data_dir=tmp_path/'data';owner.active={}
    owner._notify_observer=lambda:None
    owner._write(owner._path(pid,sid,'manifest.json'),manifest)
    issue={'code':'SOURCE_CONTEXT_CHANGED','generation':1,'operation_id':'old_task',
        'scope':'FUTURE_HOST_ADMISSIONS_AND_API_RELAY','original_history_retained':True}
    owner._write(owner._path(pid,sid,'state.json'),{'generation':1,'original_session_id':'ses_original',
        'owner_status':'READY','native_status':'busy','context_authority_issue':issue,'active_operation_id':'old_task',
        'process_identity':{'unit':'EXPLICIT_FAKE_PROCESS'},'controller_identity':{'unit':'EXPLICIT_FAKE_CONTROLLER'}})
    _,session_ref=owner._manifest(pid,sid);threads.bind_original(manifest,ref)
    owner._claim(pid,sid,'old_task','MESSAGE',{'raw_text':'ORIGINAL_PRIVATE_TASK'})
    owner._finish(pid,sid,'old_task','DISPATCHING');owner.active[sid]={'unit':'NO_SDK'}
    calls={'stop':0};mode={'kind':'CONFIRMED'}

    def get(p,s):
        state=owner._state(p,s)
        return {**state,'project_id':p,'session_ref':owner._manifest(p,s)[1],
            'connected_owner':s in owner.active,'provider_profile_ref':manifest['request']['provider_profile_ref']}

    async def disconnect(p,s):
        calls['stop']+=1
        if mode['kind']=='UNKNOWN':
            owner._update(p,s,owner_status='BLOCKED_PROCESS_STOP_UNCONFIRMED')
        else:
            owner._finish(p,s,'old_task','UNKNOWN_OUTCOME',reason='UNIT_STOP_NO_REPLAY')
            owner.active.pop(s,None)
            owner._update(p,s,owner_status='DISCONNECTED',controller_identity=None,
                process_identity={'unit':'STILL_ALIVE'} if mode['kind']=='FALSE_POSITIVE' else None)
        return get(p,s)

    owner.get=get;owner.disconnect=disconnect
    app=SimpleNamespace(state=SimpleNamespace(agent_sessions=owner,conversation_threads=threads,
        conversation_runtime=SimpleNamespace(),conversation_creation=creation))
    canonical=CanonicalConversations(app);value=ContextRecovery(app,canonical)
    body={'operation_id':'recovery_original','session_ref':session_ref,'generation':1,
        'conversation_ref':ref,'expected_issue':issue}
    return SimpleNamespace(value=value,owner=owner,pid=pid,sid=sid,body=body,creation=creation,
        threads=threads,calls=calls,mode=mode,manifest=manifest,root=tmp_path)


def run(s,body=None):return asyncio.run(s.value.restart(s.pid,s.sid,body or s.body))
def count(s):
    with s.owner.store.connect() as db:return db.execute('SELECT count(*) FROM conversation_creations').fetchone()[0]


def test_exact_stop_then_one_empty_metadata_chat_and_no_old_history_or_unknown_replay(recovery):
    s=recovery;before=s.owner._path(s.pid,s.sid,'manifest.json').read_bytes()
    value=run(s)
    assert value['phase']=='COMPLETE' and s.calls['stop']==1 and count(s)==2
    receipt=value['creation_receipt'];scope=s.threads.view(receipt['conversation_ref']['id'])
    assert scope['conversation_ref'] != s.body['conversation_ref']
    [route] = scope['sources']
    assert route['original_source'] is None
    assert route['identity'] == {'kind':'native', **receipt['next_message']}
    assert route['id'] not in {source['id'] for source in s.threads.view(s.body['conversation_ref']['id'])['sources']}
    assert receipt['messages_created']==receipt['model_calls']==0 and receipt['execution_started'] is False
    assert value['source_data_copied'] is value['drafts_transferred'] is value['automatic_post_replay'] is False
    assert s.owner.operation(s.pid,s.sid,'old_task')['status']=='UNKNOWN_OUTCOME'
    assert s.owner._path(s.pid,s.sid,'manifest.json').read_bytes()==before
    assert run(s)==value and s.calls['stop']==1 and count(s)==2


def test_lost_create_ack_is_read_only_inspection_then_same_key_metadata_continuation(recovery,monkeypatch):
    s=recovery;real=s.creation.create;calls=[]
    def failed_once(body):
        calls.append(body.operation_id)
        if len(calls)==1:raise OSError('UNIT before metadata commit')
        return real(body)
    monkeypatch.setattr(s.creation,'create',failed_once)
    with pytest.raises(OSError):run(s)
    assert s.calls['stop']==1 and count(s)==1
    before={p:p.read_bytes() for p in s.root.rglob('*.json')}
    view=s.value.operation(s.pid,s.sid,s.body['operation_id'])
    assert view['phase']=='STOP_CONFIRMED' and count(s)==1
    assert {p:p.read_bytes() for p in s.root.rglob('*.json')}==before
    assert run(s)['phase']=='COMPLETE' and s.calls['stop']==1 and calls[0]==calls[1]


def test_lost_response_after_metadata_commit_returns_same_creation_without_another_stop(recovery,monkeypatch):
    s=recovery;real=s.creation.create
    def commit_then_lost(body):
        real(body);raise OSError('UNIT lost ack after commit')
    monkeypatch.setattr(s.creation,'create',commit_then_lost)
    with pytest.raises(OSError):run(s)
    observed=s.value.operation(s.pid,s.sid,s.body['operation_id'])
    assert observed['phase']=='COMPLETE' and count(s)==2
    assert run(s)==observed and s.calls['stop']==1


@pytest.mark.parametrize('kind',['UNKNOWN','FALSE_POSITIVE'])
def test_uncertain_or_false_positive_stop_never_creates_or_repeats_stop(recovery,kind):
    s=recovery;s.mode['kind']=kind
    result=run(s);assert result['phase']=='STOP_PENDING' and result['creation_receipt'] is None
    assert count(s)==1 and s.calls['stop']==1
    assert run(s)['phase']=='STOP_PENDING' and s.calls['stop']==1 and count(s)==1
    assert s.value.operation(s.pid,s.sid,s.body['operation_id'])['phase']=='STOP_PENDING'


@pytest.mark.parametrize('change',['generation','issue','conversation'])
def test_stale_or_foreign_admission_stops_nothing(recovery,change):
    s=recovery;body=deepcopy(s.body)
    if change=='generation':body['generation']=2
    elif change=='issue':body['expected_issue']['code']='SOURCE_CONTEXT_LEGACY'
    else:body['conversation_ref']=s.creation.create(CreateConversation(operation_id='foreign',project_id=s.pid))['conversation_ref']
    before=count(s)
    with pytest.raises(Conflict):run(s,body)
    assert s.calls['stop']==0 and count(s)==before


def test_second_window_key_cannot_allocate_another_chat_for_same_native_context(recovery):
    s=recovery;first=run(s);other={**s.body,'operation_id':'other_window'}
    with pytest.raises(Conflict,match='another original recovery key'):run(s,other)
    assert s.value.operation(s.pid,s.sid,s.body['operation_id'])==first and count(s)==2


def test_changed_replay_input_never_rebinds_original_recovery(recovery):
    s=recovery;run(s);changed=deepcopy(s.body);changed['expected_issue']['code']='SOURCE_CONTEXT_UNAVAILABLE'
    with pytest.raises(Conflict,match='IDEMPOTENCY_CONFLICT'):run(s,changed)
    assert s.calls['stop']==1 and count(s)==2


def test_missing_stop_claim_can_be_explicitly_retried_without_replaying_an_existing_claim(recovery,monkeypatch):
    s=recovery;real=s.owner.controlled_disconnect;attempts=[]
    async def fail_before_claim(*args):
        attempts.append(1)
        if len(attempts)==1:raise OSError('UNIT interrupted before original stop claim')
        return await real(*args)
    monkeypatch.setattr(s.owner,'controlled_disconnect',fail_before_claim)
    with pytest.raises(OSError):run(s)
    pending=s.value.operation(s.pid,s.sid,s.body['operation_id'])
    assert pending['phase']=='STOP_PENDING' and pending['stop_status']=='NOT_FOUND'
    assert s.calls['stop']==0 and count(s)==1
    assert run(s)['phase']=='COMPLETE' and s.calls['stop']==1
    assert run(s)['phase']=='COMPLETE' and len(attempts)==2


def test_private_native_page_owner_cannot_use_public_recovery(recovery):
    s=recovery;s.manifest['native_message_admission']={'unit':'PRIVATE_OWNER'}
    s.owner._write(s.owner._path(s.pid,s.sid,'manifest.json'),s.manifest,replace=True)
    body={**s.body,'session_ref':s.owner._manifest(s.pid,s.sid)[1]}
    with pytest.raises(Conflict,match='native-page'):run(s,body)
    assert s.calls['stop']==0 and count(s)==1


# AUTHORED_NOT_RUN batch29: metadata preparation and cross-surface discovery.
def preparation(s):return s.value.prepare(s.pid,s.sid,{'session_ref':s.body['session_ref'],'generation':1})


def test_preparation_reads_the_exact_canonical_alias_without_claims_or_process_actions(recovery):
    s=recovery;before={p:p.read_bytes() for p in s.root.rglob('*.json')}
    value=preparation(s)
    basis={k:value[k] for k in ['kind','scope','conversation_ref','expected_issue','target_project_id','existing_recovery']}
    assert value['preparation_sha256']==digest(basis) and value['conversation_ref']==s.body['conversation_ref']
    assert value['existing_recovery'] is None and value['read_only'] is True
    assert value['model_calls']==value['native_posts']==value['new_conversations']==0
    assert {p:p.read_bytes() for p in s.root.rglob('*.json')}==before and s.calls['stop']==0 and count(s)==1
    assert 'ORIGINAL_PRIVATE_TASK' not in json.dumps(value)


def test_another_surface_discovers_the_existing_original_request_instead_of_allocating_a_key(recovery):
    s=recovery;original=run(s);before=count(s);value=preparation(s)
    existing=value['existing_recovery'];assert existing['request']==s.body
    assert existing['request_sha256']==digest(s.body) and existing['target_project_id']==s.pid
    checked=s.value.operation(s.pid,s.sid,existing['request']['operation_id'])
    assert checked==original and s.calls['stop']==1 and count(s)==before


def test_unconfirmed_stop_is_discovered_read_only_and_never_becomes_a_new_context(recovery):
    s=recovery;s.mode['kind']='UNKNOWN';run(s)
    existing=preparation(s)['existing_recovery']
    result=s.value.operation(s.pid,s.sid,existing['request']['operation_id'])
    assert result['phase']=='STOP_PENDING' and result['creation_receipt'] is None
    assert count(s)==1 and s.calls['stop']==1


@pytest.mark.parametrize('kind',['generation','issue','alias'])
def test_preparation_refuses_a_stale_generation_missing_issue_or_missing_alias(recovery,kind):
    s=recovery
    if kind=='generation':s.owner._update(s.pid,s.sid,generation=2)
    elif kind=='issue':s.owner._update(s.pid,s.sid,context_authority_issue=None)
    else:
        with s.owner.store.connect() as db:db.execute('DELETE FROM conversation_source_aliases WHERE kind=?',('original',))
    with pytest.raises(Conflict):preparation(s)
    assert s.calls['stop']==0 and count(s)==1


def test_preparation_cannot_adopt_a_recovery_claim_with_a_different_request_hash(recovery):
    s=recovery;run(s)
    token=digest([s.pid,s.body['session_ref'],1,'ses_original'])
    slot=s.value._path(s.pid,s.sid,['context',token]);value=s.owner._read(slot)
    s.owner._write(slot,{**value,'request_sha256':'f'*64},replace=True)
    with pytest.raises(Conflict,match='original recovery claim differs'):preparation(s)
    assert count(s)==2 and s.calls['stop']==1


def test_preparation_route_is_read_only_with_typed_scope_and_no_implicit_sdk_load(recovery):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from apps.api.context_recovery import register_context_recovery
    s=recovery;app=FastAPI();app.state.conversation_creation=s.creation
    register_context_recovery(app,s.value.canonical)
    @app.exception_handler(Conflict)
    def conflict(_request,error):return JSONResponse(status_code=409,content={'error':str(error)})
    root=f'/api/projects/{s.pid}/agent-sessions/{s.sid}/context-recovery/prepare'
    with TestClient(app) as client:
        body={'session_ref':s.body['session_ref'],'generation':1}
        response=client.post(root,json=body);assert response.status_code==200
        assert response.json()['read_only'] is True
        assert client.post(root,json={**body,'raw_text':'MUST_NOT_ACCEPT_TEXT'}).status_code==422
        assert client.post(root,json={**body,'generation':2}).status_code==409
    assert s.calls['stop']==0 and count(s)==1
