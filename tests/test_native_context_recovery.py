"""AUTHORED_NOT_RUN: signed native records, real Store/Threads/Core journals.

Only process observation/termination is a UNIT boundary. No SDK, model,
Windows, browser, actual capability admission or user database qualification.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from apps.api.canonical_conversations import CanonicalConversations
from apps.api.conversation_creation import ConversationCreation
from apps.api.native_context_recovery import NativeContextRecovery
from apps.api.native_messages import NativeMessages
from packages.agent_sessions.native_message import NativeMessageSessions
from packages.agent_sessions.native_review import NativeReviews
from packages.agent_sessions.service import AgentSessions
from packages.domain.conversation_threads import ConversationThreads, source_identity
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Store, Conflict, NotFound
from packages.domain.tasks import digest, now


@pytest.fixture
def recovery(tmp_path):
    store=Store(tmp_path/'data/eeas.sqlite3')
    pid=store.create_project('Original project','UNIT user project')['id']
    execution_pid=store.create_project('Private owner','UNIT execution project')['id']
    threads=ConversationThreads(store);creation=ConversationCreation(store,threads)
    source={'kind':'native','project_id':pid,'conversation_id':'chat_original'}
    ident,_=source_identity(source)
    with store.connect() as db:
        threads.enroll(db,{'id':ident,'kind':'native','source':source,'project_id':pid,'title':'Original'},created_at=now())
    ws=object.__new__(NativeCollaborationAuthority)
    ws.data_dir=tmp_path/'data';ws.enabled=True;ws.token='a'*64
    owner=object.__new__(AgentSessions);owner.store=store;owner.data_dir=tmp_path/'data';owner.active={}
    owner._notify_observer=lambda:None
    app=SimpleNamespace(state=SimpleNamespace(store=store,agent_sessions=owner,
        conversation_threads=threads,conversation_creation=creation,conversation_runtime=SimpleNamespace()))
    app.state.canonical_conversations=CanonicalConversations(app)
    native=object.__new__(NativeMessages);native.app=app;native.ws=ws;native.generation='host_original'
    native.ordinary=object.__new__(NativeMessageSessions);native.ordinary.ws=ws
    native.reviews=object.__new__(NativeReviews);native.reviews.ws=ws
    question={'operation_id':'native_parent','project_id':pid,'conversation_id':'chat_original',
        'raw_text':'KEEP_ORIGINAL_PRIVATE_INPUT','native_review':None}
    admission={'parent_operation_id':question['operation_id'],'body':deepcopy(question),
        'body_sha256':digest(question),'source_host_generation':native.generation}
    ref={'id':'native_parent','revision':1,'sha256':digest(admission)}
    native.ordinary.write('admissions',ref['id'],admission)
    sid='b'*32;manifest={'id':sid,'project_id':execution_pid,'request':{},'native_message_admission':ref}
    owner._write(owner._path(execution_pid,sid,'manifest.json'),manifest)
    session_ref=owner._manifest(execution_pid,sid)[1]
    issue={'code':'SOURCE_CONTEXT_CHANGED','generation':1,'operation_id':'old_task',
        'scope':'FUTURE_HOST_ADMISSIONS_AND_API_RELAY','original_history_retained':True}
    owner._write(owner._path(execution_pid,sid,'state.json'),{'generation':1,'original_session_id':'ses_original',
        'context_authority_issue':issue,'active_operation_id':'old_task','owner_status':'READY',
        'process_identity':{'unit':'NO_ACTUAL_PROCESS'},'controller_identity':{'unit':'NO_ACTUAL_CONTROLLER'}})
    record={'request':question,'request_sha256':digest(question),'generation':native.generation,
        'owner':{'project_id':execution_pid,'session_ref':session_ref,'generation':1},
        'native_message_ref':ref,'native_operation_id':'old_task','status':'UNKNOWN_OUTCOME'}
    ws._write(native.path(question['operation_id']),record)
    owner._claim(execution_pid,sid,'old_task','MESSAGE',{'raw_text':question['raw_text']})
    owner._finish(execution_pid,sid,'old_task','DISPATCHING');owner.active[sid]={'unit':'NO_SDK'}
    calls={'stop':0};mode={'kind':'CONFIRMED'}
    def get(p,s):return {**owner._state(p,s),'project_id':p,'session_ref':owner._manifest(p,s)[1]}
    async def disconnect(p,s):
        calls['stop']+=1
        if mode['kind']=='UNKNOWN':owner._update(p,s,owner_status='BLOCKED_PROCESS_STOP_UNCONFIRMED')
        else:
            owner._finish(p,s,'old_task','UNKNOWN_OUTCOME',reason='UNIT_STOP_NO_TASK_REPLAY')
            owner.active.pop(s,None)
            owner._update(p,s,owner_status='DISCONNECTED',controller_identity=None,
                process_identity={'unit':'STILL_ALIVE'} if mode['kind']=='FALSE_POSITIVE' else None)
        return get(p,s)
    owner.get=get;owner.disconnect=disconnect
    value=NativeContextRecovery(native)
    return SimpleNamespace(value=value,native=native,owner=owner,store=store,threads=threads,
        creation=creation,pid=pid,execution_pid=execution_pid,sid=sid,ref=ref,parent='native_parent',
        ident=ident,record=record,manifest=manifest,ws=ws,calls=calls,mode=mode)


def packet(s):
    p=s.value.prepare(s.parent,{'expected_native_ref':s.ref})
    return {'operation_id':p['operation_id'],'expected_native_ref':s.ref,'preparation_sha256':p['preparation_sha256']}


def run(s,body):return asyncio.run(s.value.restart(s.parent,body))


def count(s):
    with s.store.connect() as db:return db.execute('SELECT count(*) FROM conversation_creations').fetchone()[0]


def test_preparation_is_readonly_no_current_capability_readmission_or_private_owner_exposure(recovery):
    s=recovery;before=s.native.path(s.parent).read_bytes()
    s.native.ordinary.validate_admission=lambda *_:pytest.fail('Revoked sources must not block stopping')
    p=s.value.prepare(s.parent,{'expected_native_ref':s.ref})
    assert p['read_only'] and p['model_calls']==p['native_posts']==p['new_conversations']==0
    assert p['target']['project_id']==s.pid and p['target_label']=='Original project'
    assert s.execution_pid not in str(p) and s.sid not in str(p)
    assert s.native.path(s.parent).read_bytes()==before and s.calls['stop']==count(s)==0


def test_exact_owned_stop_creates_one_empty_chat_without_mutating_original_input(recovery):
    s=recovery;body=packet(s);original=s.native.path(s.parent).read_bytes()
    manifest=s.owner._path(s.execution_pid,s.sid,'manifest.json').read_bytes()
    r=run(s,body);assert r['phase']=='COMPLETE' and s.calls['stop']==count(s)==1
    receipt=r['creation_receipt'];scope=s.threads.view(receipt['conversation_ref']['id'])
    # A new chat owns a fresh message-route alias, never an inherited executor.
    identity={'kind':'native','project_id':s.pid,'conversation_id':receipt['next_message']['conversation_id']}
    assert scope['sources']==[{'id':receipt['conversation_ref']['id'],'identity':identity,'original_source':None}]
    assert receipt['conversation_ref']['id']!=s.ident and identity['conversation_id']!='chat_original'
    assert scope['automatic_input_authorized'] is scope['file_write_authorized'] is False
    assert receipt['messages_created']==receipt['model_calls']==0
    assert r['drafts_transferred'] is r['source_data_copied'] is r['execution_started'] is False
    assert s.owner.operation(s.execution_pid,s.sid,'old_task')['status']=='UNKNOWN_OUTCOME'
    assert s.native.path(s.parent).read_bytes()==original
    assert s.owner._path(s.execution_pid,s.sid,'manifest.json').read_bytes()==manifest
    assert run(s,body)==r and s.calls['stop']==count(s)==1


def test_unknown_stop_is_never_repeated_by_original_query_or_explicit_continuation(recovery):
    s=recovery;s.mode['kind']='UNKNOWN';body=packet(s)
    assert run(s,body)['phase']=='STOP_PENDING'
    assert s.value.operation(s.parent,body['operation_id'])['stop_status']=='UNKNOWN_OUTCOME'
    assert run(s,body)['phase']=='STOP_PENDING' and s.calls['stop']==1 and count(s)==0


def test_lost_metadata_creation_reply_is_resolved_by_readonly_original_key(recovery,monkeypatch):
    s=recovery;body=packet(s);real=s.creation.create
    def lost(request):real(request);raise ConnectionError('UNIT committed blank creation, reply lost')
    monkeypatch.setattr(s.creation,'create',lost)
    with pytest.raises(ConnectionError):run(s,body)
    assert s.value.operation(s.parent,body['operation_id'])['phase']=='COMPLETE'
    monkeypatch.setattr(s.creation,'create',real)
    assert run(s,body)['phase']=='COMPLETE' and s.calls['stop']==count(s)==1


def test_host_change_before_stop_cannot_create_new_authority(recovery):
    s=recovery;body=packet(s);s.native.generation='replacement_host'
    with pytest.raises(Conflict):run(s,body)
    assert s.calls['stop']==count(s)==0


def test_mismatched_ref_and_unrelated_active_parent_cannot_stop(recovery):
    s=recovery
    with pytest.raises(Conflict):s.value.prepare(s.parent,{'expected_native_ref':{**s.ref,'sha256':'f'*64}})
    s.owner._update(s.execution_pid,s.sid,active_operation_id='another_private_turn')
    with pytest.raises(Conflict):packet(s)
    assert s.calls['stop']==count(s)==0


def test_review_uses_immutable_channel_owner_even_when_current_capability_revoked(recovery):
    s=recovery;question={**s.record['request'],'native_review':{'kind':'UNIT_REVOKED_CAPABILITY'}}
    frozen={'request':question};ref={**s.ref,'sha256':digest(frozen)}
    s.native.reviews.write('reviews',ref['id'],frozen)
    admission={'source_host_generation':s.native.generation};root={'id':'review_owner','revision':1,'sha256':digest(admission)}
    s.native.reviews.write('admissions',root['id'],admission)
    manifest={**s.manifest};manifest.pop('native_message_admission');manifest['native_review_admission']=root
    s.owner._write(s.owner._path(s.execution_pid,s.sid,'manifest.json'),manifest,replace=True)
    owner={**s.record['owner'],'session_ref':s.owner._manifest(s.execution_pid,s.sid)[1]}
    s.native.reviews.write('channels','unit_channel',{'owner':{**owner,'admission_ref':root}})
    record={**s.record,'request':question,'request_sha256':digest(question),'owner':owner,'review_ref':ref,'channel_key':'unit_channel'}
    record.pop('native_message_ref');s.ws._write(s.native.path(s.parent),record);s.ref=ref
    s.native.reviews.validate_admission=lambda *_:pytest.fail('Do not readmit revoked capabilities to stop')
    assert run(s,packet(s))['phase']=='COMPLETE' and s.calls['stop']==1


def test_project_move_after_confirmed_stop_does_not_silently_change_new_chat_target(recovery,monkeypatch):
    s=recovery;body=packet(s);real=s.creation.create
    monkeypatch.setattr(s.creation,'create',lambda *_:(_ for _ in ()).throw(ConnectionError('UNIT before insert')))
    with pytest.raises(ConnectionError):run(s,body)
    assert s.value.operation(s.parent,body['operation_id'])['phase']=='STOP_CONFIRMED'
    destination=s.store.create_project('Moved later','UNIT')['id']
    with s.store.connect() as db:db.execute('UPDATE conversation_threads SET project_id=? WHERE conversation_id=?',(destination,s.ident))
    monkeypatch.setattr(s.creation,'create',real)
    result=run(s,body);assert result['creation_receipt']['project_id']==s.pid and s.calls['stop']==1


def test_returned_stop_with_remaining_process_is_not_proof_for_new_chat(recovery):
    s=recovery;s.mode['kind']='FALSE_POSITIVE';result=run(s,packet(s))
    assert result['phase']=='STOP_PENDING' and result['stop_status']=='RETURNED' and count(s)==0


def test_missing_original_record_is_not_found_but_corruption_is_conflict(recovery):
    s=recovery;body=packet(s)
    with pytest.raises(NotFound):s.value.operation(s.parent,body['operation_id'])
    run(s,body);p=s.value._path(body['operation_id']);p.write_text('{}')
    with pytest.raises(Conflict):s.value.operation(s.parent,body['operation_id'])


def test_context_projection_preserves_original_outcome_and_disables_new_guidance_or_process(recovery):
    s=recovery;result={'status':'UNKNOWN_OUTCOME','control_capabilities':{'can_guide':True,'can_status':True},
        'review':{'can_guide':True,'process_permission_current':True}}
    projected=s.value.project(s.record,result)
    assert projected['status']=='UNKNOWN_OUTCOME' and projected['control_capabilities']['can_status']
    assert projected['control_capabilities']['can_guide'] is projected['review']['process_permission_current'] is False
    assert projected['native_context_recovery']['expected_native_ref']==s.ref and s.calls['stop']==0


def test_concurrent_same_confirmation_uses_one_original_stop_and_one_blank(recovery):
    s=recovery;body=packet(s)
    async def both():return await asyncio.gather(s.value.restart(s.parent,body),s.value.restart(s.parent,body))
    a,b=asyncio.run(both());assert a==b and s.calls['stop']==count(s)==1


def test_native_id_first_becoming_known_does_not_fork_recovery_key(recovery):
    s=recovery;s.owner._update(s.execution_pid,s.sid,original_session_id=None);a=packet(s)
    s.owner._update(s.execution_pid,s.sid,original_session_id='ses_first_known');b=packet(s)
    assert a['operation_id']==b['operation_id'] and a['preparation_sha256']!=b['preparation_sha256']


def test_stale_preparation_after_source_issue_change_is_rejected_before_stop(recovery):
    s=recovery;body=packet(s);issue=deepcopy(s.owner._state(s.execution_pid,s.sid)['context_authority_issue'])
    issue['code']='SOURCE_CONTEXT_UNAVAILABLE';s.owner._update(s.execution_pid,s.sid,context_authority_issue=issue)
    with pytest.raises(Conflict):run(s,body)
    assert s.calls['stop']==count(s)==0
