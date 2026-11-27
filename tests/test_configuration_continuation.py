"""AUTHORED_NOT_RUN. Exact canonical metadata journals; UNIT session I/O only.
No SDK, model, Windows, IDE, or user data is exercised by this suite.
"""
import asyncio
from copy import deepcopy
import pytest
from pydantic import ValidationError
from apps.api.conversation_configuration import ConfigurationContinuation, confirmation_value
from packages.agent_sessions.models import OpenConversation
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest
from test_canonical_provider_generation import metadata_service
from test_conversation_configuration import configured, packet


def continuation(p):
    native={k:v for k,v in p['request'].items() if k not in {'operation_id','project_id'}}
    return {'operation_id':p['request']['operation_id'],'project_id':p['request']['project_id'],
            'request_sha256':digest(native),'confirmation_sha256':p['confirmation_sha256']}


def observe(h, p):
    return h.service.open_operation(p['request']['operation_id'],p['request']['project_id'])


def install_failure(h):
    original=h.service.sessions.open
    async def unavailable(*args,**kwargs):raise ConnectionError('UNIT before native journal')
    h.service.sessions.open=unavailable
    return original


def fail_first(h,key='continue_original'):
    p=packet(h,key);original=install_failure(h)
    with pytest.raises(ConnectionError):asyncio.run(h.configuration.configure(p))
    assert observe(h,p)['status']=='FAILED_NOT_STARTED'
    return p,original


def test_explicit_same_key_continues_first_configuration_without_sending(configured):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    r=asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    assert r['conversation_bound'] and r['session']['connected_owner']
    assert h.calls=={'open':1,'send':0,'stop':0}
    row=h.service._read(h.service._path('connections',[None,p['request']['operation_id']]))
    assert row['request']==p['request'] and row['phase']=='BOUND'
    assert row['continuation_attempts'][0]['prior_error_type']=='ConnectionError'
    assert 'error_type' not in row and row['created_at']
    asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    assert h.calls['open']==1


@pytest.mark.parametrize('field,value',[('operation_id','different_key'),('project_id','different_project'),
    ('request_sha256','f'*64),('confirmation_sha256','f'*64)])
def test_changed_reference_cannot_admit_original_configuration(configured,field,value):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    b=continuation(p);b[field]=value
    with pytest.raises((Conflict,NotFound)):
        asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],b))
    assert h.calls['open']==0 and h.calls['stop']==0


def test_conversation_id_is_part_of_continuation_identity(configured):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    with pytest.raises(Conflict):asyncio.run(h.configuration.continue_configuration('native:'+'f'*64,continuation(p)))
    assert h.calls['open']==0


def test_live_original_task_is_observed_without_second_start(configured):
    h=configured;p=packet(h,'still_processing');original=h.service.sessions.open
    async def run():
        reached,resume=asyncio.Event(),asyncio.Event()
        async def delayed(*args,**kwargs):
            reached.set();await resume.wait();return await original(*args,**kwargs)
        h.service.sessions.open=delayed
        task=asyncio.create_task(h.configuration.configure(p));await reached.wait()
        try:
            r=await h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p))
            assert r['status']=='PROCESSING' and r['session'] is None and h.calls['open']==0
        finally:resume.set();await task
        assert h.calls['open']==1 and h.calls['send']==0
    asyncio.run(run())


def test_missing_failure_marker_keeps_unknown_outcome_read_only(configured):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    path=h.service._path('connections',[None,p['request']['operation_id']]);row=h.service._read(path)
    row.pop('error_type');h.service._save(path,row,replace=True)
    r=asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    assert r['status']=='UNKNOWN_OUTCOME' and h.calls['open']==0


def test_concurrent_explicit_continuations_start_one_owner(configured):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    async def run():
        return await asyncio.gather(*(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)) for _ in range(3)))
    rows=asyncio.run(run());assert all(r['conversation_bound'] for r in rows)
    assert len({r['session']['session_ref']['id'] for r in rows})==1 and h.calls['open']==1


def test_scope_revision_change_preserves_failure_and_original_body(configured):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    path=h.service._path('connections',[None,p['request']['operation_id']]);before=path.read_bytes()
    h.scope['conversation_ref']['revision']+=1
    with pytest.raises(Conflict):asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    assert path.read_bytes()==before and h.calls['open']==0


def test_native_committed_candidate_is_observed_and_not_started_again(configured):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    async def seed():return await original(h.pid,{k:v for k,v in p['request'].items() if k!='project_id'})
    owner=asyncio.run(seed());calls=h.calls['open']
    def saved(key,pid):
        assert key==p['request']['operation_id']
        return {'version':1,'operation_id':key,'request_sha256':continuation(p)['request_sha256'],
            'project_id':h.pid,'conversation_ref':p['request']['conversation_ref'],
            'conversation_bound':False,'session':deepcopy(owner),'owner_started_by_query':False,
            'model_calls_by_query':0,'source_changed_by_query':False,'automatic_post_replay':False}
    h.service.sessions.open_operation=saved
    r=asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    assert r['session']['session_ref']==owner['session_ref'] and h.calls['open']==calls


def test_failure_evidence_is_bounded_and_not_discarded(configured):
    h=configured;p,_=fail_first(h)
    for _ in range(32):
        with pytest.raises(ConnectionError):asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    with pytest.raises(Conflict,match='attempt bound'):asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    row=h.service._read(h.service._path('connections',[None,p['request']['operation_id']]))
    assert len(row['continuation_attempts'])==32 and row['error_type']=='ConnectionError'


def test_continuation_is_reference_only_without_request_or_user_message():
    assert len(ConfigurationContinuation.model_json_schema()['properties'])==4
    p={'operation_id':'original','project_id':None,'request_sha256':'a'*64,'confirmation_sha256':'b'*64}
    for extra in ['request','raw_text','original_source','allowed_effects','execution_project_id']:
        with pytest.raises(ValidationError):ConfigurationContinuation.model_validate({**p,extra:None})


def test_repeated_configure_only_observes_failure_without_implicit_start(configured):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    assert asyncio.run(h.configuration.configure(p)) is None
    assert h.calls=={'open':0,'send':0,'stop':0}
    assert observe(h,p)['status']=='FAILED_NOT_STARTED'


def test_unbound_raw_receipt_uses_confirmed_execution_project(configured):
    h=configured
    first=packet(h,'original_before_move');asyncio.run(h.configuration.configure(first))
    h.scope['project_id']=h.service.threads.store.create_project('Moved organization','UNIT')['id']
    h.scope['conversation_ref']['revision']+=1
    p=packet(h,'execution_after_move');p['request']['project_id']=h.scope['project_id']
    p['confirmation_sha256']=digest(confirmation_value(p['request'],p['preparation_sha256'],p['execution_project_id']))
    original=install_failure(h)
    with pytest.raises(ConnectionError):asyncio.run(h.configuration.configure(p))
    expected=p['execution_project_id'];assert expected==h.pid
    seen=[]
    def missing(key,pid):
        seen.append(pid);assert pid==expected;raise NotFound('UNIT no native candidate')
    h.service.sessions.open_operation=missing
    r=observe(h,p)
    assert seen==[expected] and r['status']=='FAILED_NOT_STARTED'
    assert r['organization_project_id']==h.scope['project_id'] and r['execution_project_id']==h.pid


# batch33 AUTHORED_NOT_RUN: current grants must pass before retirement admission.
def test_continuation_rechecks_current_grants_before_mutating_original_journal(configured):
    h=configured;p,original=fail_first(h);h.service.sessions.open=original
    path=h.service._path('connections',[None,p['request']['operation_id']]);before=path.read_bytes()
    def denied(*_args):raise Conflict('UNIT current capability withdrawn')
    h.service.sessions._admit_open=denied
    with pytest.raises(Conflict,match='capability withdrawn'):
        asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(p)))
    assert path.read_bytes()==before and h.calls=={'open':0,'send':0,'stop':0}
