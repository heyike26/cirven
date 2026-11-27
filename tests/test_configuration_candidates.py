"""AUTHORED_NOT_RUN. Canonical journals/Store are real; actor I/O is UNIT metadata.
No native SDK/model/OS start is exercised or qualified by this suite.
"""
import asyncio
from copy import deepcopy
import pytest
from apps.api.configuration_candidates import ConfigurationCandidates
from packages.domain.store import Conflict,NotFound
from packages.agent_sessions.service import AgentSessions
from packages.domain.tasks import digest
from test_canonical_provider_generation import metadata_service
from test_conversation_configuration import configured,packet
from test_configuration_continuation import continuation


@pytest.fixture
def candidate(configured):
    h=configured;c=h.service;s=c.sessions;raw={};original_open=s.open;original_manifest=s._manifest
    h.calls.update(load=0,inspect=0,bind=0);h.mode={'load_failure':False}
    async def open_candidate(pid,body,**internal):
        view=await original_open(pid,body,**internal);sid=view['session_ref']['id'];manifest=original_manifest(pid,sid)[0]
        manifest['request']={k:v for k,v in body.items() if k!='operation_id'}
        manifest['canonical_execution_claim']=deepcopy(internal['canonical_execution_claim'])
        ref={**view['session_ref'],'sha256':digest(manifest)}
        state=h.views[sid];state.update(session_ref=ref,generation=0,owner_status='DISCONNECTED',connected_owner=False,
            observation_is_current=False,process_identity=None,controller_identity=None,original_session_id=None,
            native_turns_started=0,model_calls_reserved=0,children=[])
        h.scope['sources'][-1]['original_source'].update(session_ref=deepcopy(ref),generation=0)
        raw[body['operation_id']]={'pid':pid,'sid':sid,'request_sha256':digest(manifest['request'])}
        raise ConnectionError('UNIT committed before candidate first start')
    s._admit_native_initialization=AgentSessions._admit_native_initialization
    s.open=open_candidate
    s._manifest=lambda pid,sid:(original_manifest(pid,sid)[0],deepcopy(h.views[sid]['session_ref']))
    s._state=lambda pid,sid:deepcopy(h.views[sid])
    c._bound=lambda source,cid:any((row.get('original_source') or {}).get('kind')=='original'
        and all(row['original_source'][k]==source[k] for k in ('project_id','session_ref')) for row in h.scope['sources'])
    def opened(key,pid=None):
        if key not in raw:raise NotFound('UNIT original not committed')
        r=raw[key];view=s.get(r['pid'],r['sid'])
        return {'version':1,'operation_id':key,'request_sha256':r['request_sha256'],'project_id':r['pid'],
            'conversation_ref':h.scope['conversation_ref'],'conversation_bound':c._bound(c._source(view),h.scope['conversation_ref']['id']),
            'session':view,'owner_started_by_query':False,'model_calls_by_query':0,'source_changed_by_query':False,'automatic_post_replay':False}
    s.open_operation=opened
    def bind(manifest):h.calls['bind']+=1
    s.conversations.bind_open=bind
    async def inspect(pid,sid):h.calls['inspect']+=1;return s.get(pid,sid)
    s.inspect=inspect
    async def load(pid,sid,body,**internal):
        h.calls['load']+=1;state=h.views[sid];old_generation=state['generation']
        assert internal=={'configuration_generation_zero':old_generation==0}
        assert body.expected_generation==(old_generation if old_generation>0 else None)
        req=body.model_dump(exclude_none=True);record={'id':body.operation_id,'kind':'LOAD','session_ref':state['session_ref'],
            'request':req,'request_sha256':digest(req),'status':'FAILED' if h.mode['load_failure'] else 'RETURNED'}
        if not h.mode['load_failure']:
            state.update(generation=old_generation+1,owner_status='READY',connected_owner=True,observation_is_current=True,
                original_session_id=state['original_session_id'] or 'ses_same_candidate',native_status='idle')
            record['loaded_generation']=state['generation']
        path=s._path(pid,sid,'operations',body.operation_id+'.json');s._write(path,record)
        if h.mode['load_failure']:raise ConnectionError('UNIT failure before start')
        return {'operation':record,'session':s.get(pid,sid)}
    s.load=load
    p=packet(h,'committed_candidate')
    with pytest.raises(ConnectionError):asyncio.run(h.configuration.configure(p))
    h.p=p;h.sid=raw[p['request']['operation_id']]['sid'];h.actor=h.views[h.sid];h.manifest=s._manifest(h.pid,h.sid)[0]
    h.path=c._path('connections',[None,p['request']['operation_id']]);h.query=lambda:c.open_operation(p['request']['operation_id'],None)
    h.continue_candidate=lambda:asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    return h


def test_candidate_query_is_readonly_and_never_loads_binds_inspects_or_allocates(candidate):
    h=candidate;before=deepcopy(h.calls);a=h.query();b=h.query()
    assert a['status']==b['status']=='CANDIDATE_CLOSED' and not a['conversation_bound']
    assert h.calls==before and a['session']['generation']==0


def test_generation_zero_continue_loads_the_same_allocated_manifest_and_publishes_once(candidate):
    h=candidate;ref=deepcopy(h.actor['session_ref']);manifest=deepcopy(h.manifest);r=h.continue_candidate()
    assert r['conversation_bound'] and r['session']['session_ref']==ref and r['session']['generation']==1
    assert h.manifest==manifest and h.calls['open']==1 and h.calls['load']==1 and h.calls['send']==0
    again=h.continue_candidate();assert again['conversation_bound'] and h.calls['load']==1


def test_ready_candidate_only_publishes_metadata_without_load_or_second_open(candidate):
    h=candidate;h.actor.update(generation=1,original_session_id='ses_known',owner_status='READY',native_status='idle',connected_owner=True,observation_is_current=True)
    assert h.query()['status']=='CANDIDATE_READY';r=h.continue_candidate()
    assert r['conversation_bound'] and h.calls['open']==1 and h.calls['load']==0 and h.calls['inspect']==1


@pytest.mark.parametrize('stage',[None,'NATIVE_CREATE_ATTEMPTED','CORRUPT'])
def test_missing_or_attempted_native_create_without_known_id_is_unknown(candidate,stage):
    h=candidate;h.actor.update(generation=1,owner_status='BLOCKED')
    if stage:h.actor['native_initialization']={'generation':1,'manifest_sha256':digest(h.manifest),'stage':stage,'original_session_id':None}
    assert h.query()['status']=='UNKNOWN_OUTCOME';h.continue_candidate();assert h.calls['load']==0


def test_manifest_bound_not_attempted_checkpoint_allows_same_candidate_load(candidate):
    h=candidate;h.actor.update(generation=1,owner_status='BLOCKED',native_initialization={'generation':1,
        'manifest_sha256':digest(h.manifest),'stage':'NATIVE_NOT_ATTEMPTED','original_session_id':None})
    assert h.query()['status']=='CANDIDATE_CLOSED';r=h.continue_candidate();assert r['session']['generation']==2 and h.calls['load']==1


@pytest.mark.parametrize('change',[{'native_turns_started':1},{'model_calls_reserved':1},{'active_operation_id':'original_work'},
    {'process_identity':{'pid':12}},{'controller_identity':{'pid':13}},{'pending_permissions':[{'id':'original_permission'}]},
    {'messages':[{'info':{'role':'user'},'parts':[]}]},{'children':[{'id':'child'}]}])
def test_used_running_or_pending_original_work_is_never_restarted_as_configuration(candidate,change):
    h=candidate;h.actor.update(deepcopy(change));assert h.query()['status']=='UNKNOWN_OUTCOME';h.continue_candidate();assert h.calls['load']==0


def test_wrong_manifest_private_claim_or_changed_configuration_cannot_continue(candidate):
    h=candidate;h.manifest['canonical_execution_claim']['confirmation_sha256']='f'*64
    assert h.query()['status']=='UNKNOWN_OUTCOME';h.continue_candidate();assert h.calls['load']==0


def test_scope_move_rejects_before_bind_or_load_and_preserves_original_record(candidate):
    h=candidate;before=h.path.read_bytes();h.scope['conversation_ref']['revision']+=1
    with pytest.raises(Conflict):h.continue_candidate()
    assert h.path.read_bytes()==before and h.calls['load']==h.calls['bind']==0


def test_failed_load_attempt_is_retained_and_explicit_retry_uses_new_load_key_same_actor(candidate):
    h=candidate;h.mode['load_failure']=True
    with pytest.raises(ConnectionError):h.continue_candidate()
    first=h.service._read(h.path)['candidate_continuations'][0];assert first['phase']=='UNKNOWN'
    h.mode['load_failure']=False;r=h.continue_candidate();row=h.service._read(h.path)
    assert r['conversation_bound'] and h.calls['open']==1 and h.calls['load']==2
    assert row['candidate_continuations'][0]==first and row['candidate_continuations'][1]['load_operation_id']!=first['load_operation_id']


def test_concurrent_candidate_continuations_load_one_original_owner(candidate):
    h=candidate
    async def run():return await asyncio.gather(*(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(h.p)) for _ in range(3)))
    rows=asyncio.run(run());assert all(r['conversation_bound'] for r in rows)
    assert h.calls['open']==h.calls['load']==1 and h.calls['send']==0


def test_unknown_linked_load_can_be_published_only_if_same_next_generation_is_already_ready(candidate):
    h=candidate;c=h.service;row=c._read(h.path);ref=deepcopy(h.actor['session_ref']);step={'candidate':c._source(h.actor),
        'action':'LOAD_SAME_CANDIDATE','load_operation_id':'original_unknown_load','phase':'UNKNOWN'}
    row['candidate_continuations']=[step];c._save(h.path,row,replace=True)
    request={'operation_id':'original_unknown_load','session_ref':ref};op={'id':'original_unknown_load','kind':'LOAD',
        'session_ref':ref,'request':request,'request_sha256':digest(request),'status':'UNKNOWN_OUTCOME'}
    path=c.sessions._path(h.pid,h.sid,'operations','original_unknown_load.json');c.sessions._write(path,op)
    h.actor.update(generation=1,original_session_id='ses_known',owner_status='READY',native_status='idle',connected_owner=True,observation_is_current=True)
    assert h.query()['status']=='CANDIDATE_READY';r=h.continue_candidate();assert r['conversation_bound'] and h.calls['load']==0
    assert c.sessions._read(path)==op


def test_unknown_unlinked_load_is_not_replayed_or_ignored(candidate):
    h=candidate;c=h.service;ref=deepcopy(h.actor['session_ref']);request={'operation_id':'unrelated_load','session_ref':ref}
    c.sessions._write(c.sessions._path(h.pid,h.sid,'operations','unrelated_load.json'),{'id':'unrelated_load','kind':'LOAD',
        'session_ref':ref,'request':request,'request_sha256':digest(request),'status':'UNKNOWN_OUTCOME'})
    assert h.query()['status']=='UNKNOWN_OUTCOME';h.continue_candidate();assert h.calls['load']==0


def test_interrupted_publication_can_finish_without_another_load(candidate,monkeypatch):
    h=candidate;c=h.service;h.actor.update(generation=1,original_session_id='ses_known',owner_status='READY',native_status='idle',connected_owner=True,observation_is_current=True)
    original=c._save;head=c._path('current-owners',h.scope['conversation_ref']['id'])
    def fail_once(path,row,**kw):
        if path==head:raise OSError('UNIT publication acknowledgement lost')
        return original(path,row,**kw)
    monkeypatch.setattr(c,'_save',fail_once)
    with pytest.raises(OSError):h.continue_candidate()
    monkeypatch.setattr(c,'_save',original);r=h.continue_candidate()
    assert r['conversation_bound'] and h.calls['load']==0 and h.calls['open']==1


def test_published_init_only_load_adoption_keeps_unknown_receipt_and_does_not_block_later_idle_switch(candidate):
    h=candidate;c=h.service;row=c._read(h.path);step={'candidate':c._source(h.actor),
        'action':'LOAD_SAME_CANDIDATE','load_operation_id':'init_unknown','phase':'UNKNOWN'}
    row['candidate_continuations']=[step];c._save(h.path,row,replace=True)
    request={'operation_id':'init_unknown','session_ref':h.actor['session_ref']};op={'id':'init_unknown','kind':'LOAD',
        'session_ref':h.actor['session_ref'],'request':request,'request_sha256':digest(request),'status':'UNKNOWN_OUTCOME'}
    path=c.sessions._path(h.pid,h.sid,'operations','init_unknown.json');c.sessions._write(path,op)
    h.actor.update(generation=1,original_session_id='ses_original',owner_status='READY',native_status='idle',connected_owner=True,observation_is_current=True)
    assert h.continue_candidate()['conversation_bound'];c._known_idle_owner(h.actor,require_auth_failure=False)
    assert c.sessions._read(path)==op and h.calls['load']==0
    h.actor['original_session_id']='ses_other'
    with pytest.raises(Conflict):c._known_idle_owner(h.actor,require_auth_failure=False)
