"""Real metadata/service rejection and cleanup boundaries; no CLI/HTTP/model.

The independent pinned-CLI suite separately qualifies actual SDK processes.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest
from packages.agent_sessions.models import SendMessage, operation_request
from packages.agent_sessions.guidance import GuidanceInput
from packages.agent_sessions.native_review import NativeReviews
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest
from test_native_capability_task_metadata_source64 import manager, permission_scene, check, context


def ready(f):
    pid, sid = f.manifest['project_id'], f.manifest['id']
    ref = f.sessions._manifest(pid, sid)[1]
    f.context.update(native_review_user_turns=8, native_review={'kind':'CAPABILITY_TASK'})
    f.sessions.active[sid] = f.context
    return pid, sid, ref


@pytest.mark.parametrize('guidance', [False, True])
def test_real_eight_turn_preclaim_guard_persists_failed_receipt_without_context_or_post(permission_scene, monkeypatch, guidance):
    f=permission_scene; pid,sid,ref=ready(f)
    def no_context(*args, **kwargs): raise AssertionError('Rejected pre-claim input entered original SDK context')
    monkeypatch.setattr(f.sessions, '_context', no_context)
    body={'operation_id':'ninth-guidance' if guidance else 'ninth-message', 'session_ref':ref,
          'generation':1,'raw_text':'Original ninth instruction'}
    if guidance: body.update(intent='STEER',expected_active_operation_id='native-original-parent')
    async def run():
        with pytest.raises(Conflict, match='eight explicit user turns'):
            if guidance: await f.sessions.guidance.submit(pid,sid,body)
            else: await f.sessions.send(pid,sid,body)
    asyncio.run(run())
    result=f.sessions.operation(pid,sid,body['operation_id'])
    check(result['status']=='FAILED' and result['native_post_attempted'] is False)
    check(result['rejection_phase']=='BEFORE_ORIGINAL_CLAIM' and result['execution_started'] is False)
    check(result['request']['raw_text']==body['raw_text'] and result['generation']==1)
    check(f.context['native_review_user_turns']==8 and not f.reviews._task(f.root)['turns'])
    before=deepcopy(result);asyncio.run(run())
    check(f.sessions.operation(pid,sid,body['operation_id'])==before)
    # A new service instance sees the exact durable failure, without dispatch.
    other=type(f.sessions)(f.sessions.store,f.sessions.data_dir,provider_registry=None,workspaces=None,capabilities=f.sessions.capabilities)
    check(other.operation(pid,sid,body['operation_id'])==before)


def test_preclaim_rejection_does_not_overwrite_existing_dispatched_receipt(permission_scene):
    f=permission_scene;pid,sid,ref=ready(f)
    body=SendMessage.model_validate({'operation_id':'existing-message','session_ref':ref,'generation':1,'raw_text':'Original existing message'})
    f.sessions._claim(pid,sid,body.operation_id,'MESSAGE',operation_request(body))
    f.sessions._finish(pid,sid,body.operation_id,'DISPATCHING',native_post_attempted=True,native_message_id='msg_existing')
    before=f.sessions.operation(pid,sid,body.operation_id)
    async def run():
        with pytest.raises(Conflict,match='eight explicit user turns'):await f.sessions.send(pid,sid,body)
    asyncio.run(run())
    check(f.sessions.operation(pid,sid,body.operation_id)==before)
    check('rejection_phase' not in before and before['native_post_attempted'] is True)


def channel(f, active=True):
    pid,sid,ref=ready(f);key=f.reviews._channel_key(f.reviews._record(f.root)['request'])
    admission=f.reviews.read('admissions',f.manifest['native_review_admission']['id'])
    owner={'project_id':pid,'session_ref':ref,'generation':1,'admission_ref':f.manifest['native_review_admission']}
    record=f.reviews._record(f.root)
    record.update(owner=owner,channel_key=key,status='PROCESSING',execution_started=False,dispatch_state='ACCEPTED',review_phase='RUNNING')
    f.reviews.ws._write(f.reviews.native.path(f.root),record)
    ch={'key':key,'owner':owner,'active':f.root if active else None,'queue':[] if active else [f.root]}
    f.reviews.write('channels',key,ch)
    return pid,sid,ref,key,record,ch


def test_real_advance_ninth_turn_is_terminal_and_channel_not_stranded(permission_scene, monkeypatch):
    f=permission_scene;pid,sid,ref,key,record,ch=channel(f,active=False)
    source,_=context()
    def frozen_review(body):
        check(body['context_ref'] == source['context_ref'])
        return {'original':'fixture'}
    f.reviews.native.review_context = frozen_review
    source['observation_sha256']=digest({'original':'fixture'})
    frozen=f.reviews._frozen(record['review_ref']);frozen.update(capability_context=source,
       capability_scope=f.reviews.read('admissions',f.manifest['native_review_admission']['id'])['capability_scope'])
    frozen['request'].update(raw_text='Original ninth queued question',context_ref=source['context_ref'],provider_profile_ref={'id':'provider','revision':1,'sha256':'e'*64})
    record['request']=deepcopy(frozen['request']);record['request_sha256']=digest(record['request'])
    record['review_ref']={'id':f.root,'revision':1,'sha256':digest(frozen)}
    f.reviews.write('reviews',f.root,frozen);f.reviews.ws._write(f.reviews.native.path(f.root),record)
    f.sessions._write(f.sessions._path(pid,sid,'state.json'),{'active_operation_id':None},replace=True)
    monkeypatch.setattr(f.reviews,'_owner_live',lambda owner:True)
    asyncio.run(f.reviews._advance(key))
    record=f.reviews._record(f.root);original=f.sessions.operation(pid,sid,record['native_operation_id'])
    check(original['status']=='FAILED' and original['rejection_phase']=='BEFORE_ORIGINAL_CLAIM')
    check(record['status']=='FAILED' and record['execution_started'] is False and record['dispatch_state']=='NOT_DISPATCHED')
    check(f.reviews.read('channels',key)['active'] is None)
    f.reviews.owner_stopped(pid,sid,f.context)
    check(f.reviews._task(f.root)['blocked'] is False)


def test_missing_original_receipt_is_unknown_and_actual_dispose_releases_registry(permission_scene):
    f=permission_scene;pid,sid,ref,key,record,ch=channel(f)
    f.context.update(process=None,client=None)
    state={'active_operation_id':record['native_operation_id'],'generation':1,'original_session_id':f.context['session_id'],'last_event_seq':0}
    f.sessions._write(f.sessions._path(pid,sid,'state.json'),state,replace=True)
    asyncio.run(f.sessions._dispose(pid,sid,f.context,status='DISCONNECTED'))
    check(sid not in f.sessions.active)
    saved=f.reviews._record(f.root)
    check(saved['status']=='UNKNOWN_OUTCOME' and saved['capability_unknown'] is True)
    check(saved['error']['code']=='REVIEW_ORIGINAL_RECEIPT_UNAVAILABLE')
    check(f.reviews._task(f.root,live=False)['blocked'] is True)
    with pytest.raises(NotFound):f.sessions.operation(pid,sid,record['native_operation_id'])


def test_advance_missing_active_receipt_does_not_claim_no_dispatch(permission_scene):
    f=permission_scene;pid,sid,ref,key,record,ch=channel(f)
    asyncio.run(f.reviews._advance(key))
    saved=f.reviews._record(f.root)
    check(saved['status']=='UNKNOWN_OUTCOME' and saved['dispatch_state']=='UNKNOWN_OUTCOME')
    check(f.reviews.read('channels',key)['active']==f.root)
    with pytest.raises(NotFound):f.sessions.operation(pid,sid,record['native_operation_id'])


@pytest.mark.parametrize('entered',[False,True])
def test_control_distinguishes_local_guard_from_missing_after_original_call(permission_scene,monkeypatch,entered):
    f=permission_scene;pid,sid,ref,key,record,ch=channel(f)
    monkeypatch.setattr(f.reviews,'project',lambda record:{'review':{'can_guide':entered}})
    async def missing(*args,**kwargs):raise NotFound('Simulated loss after original call boundary; no receipt is fabricated')
    monkeypatch.setattr(f.sessions.guidance,'submit',missing)
    result=asyncio.run(f.reviews.control(f.root,{'operation_id':'original-guidance-control',
        'expected_review_ref':record['review_ref'],'raw_text':'Original supplemental instruction'},kind='GUIDANCE'))
    check(result['status']==('UNKNOWN_OUTCOME' if entered else 'FAILED_NOT_DISPATCHED'))
    check(f.reviews._task(f.root,live=False)['blocked'] is entered)
    check(f.reviews.control_get(f.root,result['operation_id'])['status']==result['status'])
