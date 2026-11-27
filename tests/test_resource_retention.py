"""AUTHORED_NOT_RUN: real original events, SQL metadata and per-work ledgers.

Controlled local records only; no SDK client/process/model/native qualification.
"""
import asyncio
import json
import pytest
from packages.domain.store import Conflict
from packages.domain.tasks import canonical,digest
from packages.domain.task_resource_budget import TaskResourceBudget,task_resource_scope
from packages.agent_sessions.task_resources import for_operation
from packages.agent_sessions.model_authority import ModelAuthorityCall
from packages.agent_sessions import resource_retention as routing
from test_experience_candidates import experience


@pytest.fixture
def retained(experience):
    s=experience;o=s['sessions'];manifest,reference=o._manifest(s['pid'],s['sid'])
    o._update(s['pid'],s['sid'],last_event_seq=0,active_operation_id=s['scope']['root_operation_id'])
    o.active[s['sid']]={'generation':1,'session_id':'ses_original'}
    original=o.operation(s['pid'],s['sid'],s['scope']['root_operation_id']);budget=for_operation(o,manifest,original);budget.allocate()
    with o.store.connect() as db:
        db.execute('BEGIN IMMEDIATE');routing.bind_user(o,db,manifest,1,'ses_original','msg_original',budget)
    return {**s,'o':o,'manifest':manifest,'reference':reference,'budget':budget}


def second(s,name='second',generation=1):
    o=s['o'];request={'session_ref':s['reference'],'generation':generation,'raw_text':'A different original work'}
    o._claim(s['pid'],s['sid'],name,'MESSAGE',request)
    op=o._finish(s['pid'],s['sid'],name,'UNKNOWN_OUTCOME',generation=generation,original_session_id='ses_original',native_message_id='msg_'+name)
    b=for_operation(o,s['manifest'],op);b.allocate()
    with o.store.connect() as db:
        db.execute('BEGIN IMMEDIATE');routing.bind_user(o,db,s['manifest'],generation,'ses_original','msg_'+name,b)
    return b


def info(mid,role='assistant',parent='msg_original',native='ses_original'):
    value={'id':mid,'role':role,'sessionID':native}
    if role=='assistant':value['parentID']=parent
    return {'type':'message.updated','properties':{'info':value}}


def part(mid,text='Retain this result',native='ses_original',task=None,ident='prt_output'):
    value={'id':ident,'messageID':mid,'sessionID':native,'type':'text','text':text}
    if task is not None:value={'id':ident,'messageID':mid,'sessionID':native,'type':'tool','tool':'task','callID':'call_original','state':{'status':'running','metadata':{'sessionId':task,'parentSessionId':native},'input':{'subagent_type':'build'}}}
    return {'type':'message.part.updated','properties':{'part':value}}


def append(s,native,generation=1):
    seq=s['o']._event(s['pid'],s['sid'],'ORIGINAL_EVENT',{'native_payload':native,'generation':generation,'native_payload_sha256':digest(native)})
    event=s['o']._read(s['o']._path(s['pid'],s['sid'],'events',f'{seq:010d}.json'))
    return event,len(canonical(event).encode())


def confirmed(s):
    with s['o'].store.connect() as db:routing.require_confirmed(s['o'],db,s['pid'],s['sid'])


def child_plan(s):
    return ModelAuthorityCall.model_validate({'schema':'eeas.model-authority-call.v1',
        'owner_ref':{'project_id':s['pid'],'session_ref':s['reference']},'generation':1,
        'root_native_session_id':'ses_original','native_session_id':'ses_child',
        'user_message_id':'msg_child','agent':'build','step':2})


def test_original_child_plan_waits_for_committed_parent_metadata_without_guessing_or_starting_work(retained):
    s=retained;ctx=s['o'].active[s['sid']];body=child_plan(s)
    async def run():
        with task_resource_scope(s['budget']):
            task=asyncio.create_task(routing.wait_model_user(s['o'],s['manifest'],ctx,body,s['scope']['root_operation_id']))
            await asyncio.sleep(0);assert not task.done()
            append(s,info('msg_parent'));append(s,part('msg_parent',task='ses_child'))
            ctx['resource_attribution_changed'].set();await asyncio.wait_for(task,2)
    asyncio.run(run())
    with s['o'].store.connect() as db:
        assert db.execute('SELECT count(*) FROM original_resource_messages WHERE message_id="msg_child"').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM original_resource_children').fetchone()[0]==1
    assert s['budget'].inspect()['used']['model_attempts']==0


def test_child_plan_never_borrows_the_current_work_when_original_metadata_names_another_work(retained):
    s=retained;second(s);append(s,info('msg_other_parent',parent='msg_second'));append(s,part('msg_other_parent',task='ses_child'))
    async def run():
        with task_resource_scope(s['budget']):
            with pytest.raises(Conflict,match='another work'):
                await routing.wait_model_user(s['o'],s['manifest'],s['o'].active[s['sid']],child_plan(s),s['scope']['root_operation_id'])
    asyncio.run(run());assert s['budget'].inspect()['used']['model_attempts']==0


@pytest.mark.parametrize('fault',['timeout','owner','operation','generation'])
def test_unproven_or_changed_child_plan_preserves_original_accounting_and_starts_nothing(retained,fault):
    s=retained;ctx=s['o'].active[s['sid']];before=s['budget'].path.read_bytes()
    async def run():
        with task_resource_scope(s['budget']):
            task=asyncio.create_task(routing.wait_model_user(s['o'],s['manifest'],ctx,child_plan(s),s['scope']['root_operation_id'],timeout_seconds=.05))
            await asyncio.sleep(0)
            if fault=='owner':s['o'].active[s['sid']]=dict(ctx)
            elif fault=='operation':s['o']._update(s['pid'],s['sid'],active_operation_id='replacement')
            elif fault=='generation':s['o']._update(s['pid'],s['sid'],generation=2)
            if fault!='timeout':ctx['resource_attribution_changed'].set()
            with pytest.raises(Conflict):await task
    asyncio.run(run());assert s['budget'].path.read_bytes()==before


def test_late_original_output_is_charged_to_exact_original_user_not_new_active_head(retained):
    s=retained;b=s['budget'];other=second(s);s['o']._update(s['pid'],s['sid'],active_operation_id='second')
    _,a=append(s,info('msg_answer'));_,z=append(s,part('msg_answer'))
    assert b.inspect()['used']['retained_output_bytes']==a+z and other.inspect()['used']['retained_output_bytes']==0
    assert routing.summary(s['o'],s['pid'],s['sid'])['pending_bytes']==0


def test_part_before_assistant_info_stays_pending_then_settles_only_from_original_parent(retained):
    s=retained;_,a=append(s,part('msg_out_of_order'))
    assert s['budget'].inspect()['used']['retained_output_bytes']==0
    assert routing.summary(s['o'],s['pid'],s['sid'])['pending_bytes']==a
    with pytest.raises(Conflict):confirmed(s)
    _,z=append(s,info('msg_out_of_order'));confirmed(s)
    assert s['budget'].inspect()['used']['retained_output_bytes']==a+z


def test_child_output_before_task_metadata_resolves_from_exact_parent_assistant(retained):
    s=retained;amount=0
    for event in [info('msg_root_answer'),info('msg_child_user',role='user',native='ses_child'),info('msg_child_answer',parent='msg_child_user',native='ses_child')]:
        _,size=append(s,event);amount+=size
    with pytest.raises(Conflict):confirmed(s)
    _,size=append(s,part('msg_root_answer',task='ses_child',ident='prt_delegate'));amount+=size
    confirmed(s)
    assert routing.summary(s['o'],s['pid'],s['sid'])['pending_bytes']==0
    assert s['budget'].inspect()['used']['retained_output_bytes']==amount


def test_task_part_before_parent_assistant_info_is_reconciled_from_original_event_locations(retained):
    s=retained
    for event in [part('msg_root_delayed',task='ses_child',ident='prt_delegate'),info('msg_child_user',role='user',native='ses_child')]:append(s,event)
    append(s,info('msg_root_delayed'));confirmed(s)
    assert routing.summary(s['o'],s['pid'],s['sid'])['pending_bytes']==0
    with s['o'].store.connect() as db:assert db.execute('SELECT count(*) FROM original_resource_children').fetchone()[0]==1


def test_old_native_user_cannot_borrow_new_original_work_model_allowance(retained):
    s=retained;other=second(s)
    call=ModelAuthorityCall.model_validate({'schema':'eeas.model-authority-call.v1','owner_ref':{'project_id':s['pid'],'session_ref':s['reference']},'generation':1,'root_native_session_id':'ses_original','native_session_id':'ses_original','user_message_id':'msg_original','agent':'build','step':1})
    with task_resource_scope(other),s['o'].store.connect() as db:
        with pytest.raises(Conflict):routing.admit_model_user(s['o'],db,s['manifest'],call)
    with task_resource_scope(s['budget']),s['o'].store.connect() as db:routing.admit_model_user(s['o'],db,s['manifest'],call)
    assert other.inspect()['used']['model_attempts']==0


def test_ambiguous_child_provenance_keeps_original_event_and_sticky_gap_across_owner_reopen(retained):
    s=retained;second(s)
    for event in [info('msg_first_answer'),part('msg_first_answer',task='ses_child',ident='prt_first'),info('msg_second_answer',parent='msg_second')]:append(s,event)
    event,_=append(s,part('msg_second_answer',task='ses_child',ident='prt_second'))
    assert s['o']._read(s['o']._path(s['pid'],s['sid'],'events',f"{event['seq']:010d}.json"))==event
    assert routing.summary(s['o'],s['pid'],s['sid'])['gap'] is not None
    s['o'].active.clear()
    with pytest.raises(Conflict):confirmed(s)


def test_metadata_capacity_preserves_arrived_evidence_and_blocks_new_model(retained,monkeypatch):
    s=retained;monkeypatch.setattr(routing,'MAX_MESSAGES',1)
    event,_=append(s,info('msg_over_capacity'))
    assert s['o']._path(s['pid'],s['sid'],'events',f"{event['seq']:010d}.json").is_file()
    with pytest.raises(Conflict):confirmed(s)
    assert s['o']._state(s['pid'],s['sid'])['resource_retention_unconfirmed']


def test_failed_resource_accounting_preserves_original_output_and_blocks_future_generation(retained,monkeypatch):
    s=retained
    def failed(*args,**kwargs):raise OSError('UNIT quota disk write failed')
    monkeypatch.setattr(TaskResourceBudget,'consume',failed)
    event,_=append(s,info('msg_accounting_failure'))
    assert s['o']._read(s['o']._path(s['pid'],s['sid'],'events',f"{event['seq']:010d}.json"))==event
    assert routing.summary(s['o'],s['pid'],s['sid'])['pending_bytes']>0
    with pytest.raises(Conflict):confirmed(s)


def test_same_committed_event_observation_does_not_charge_or_copy_again(retained):
    s=retained;event,size=append(s,info('msg_once'));before=s['budget'].path.read_bytes()
    with s['o'].store.connect() as db:
        db.execute('BEGIN IMMEDIATE');assert routing.observe(s['o'],db,s['pid'],s['sid'],event)=='ALREADY_ACCOUNTED_OR_PENDING'
        rows=db.execute('SELECT resource_json FROM original_resource_messages WHERE resource_json IS NOT NULL').fetchall()
        assert all(set(json.loads(row[0]))=={'authority','limits'} for row in rows)
    assert s['budget'].path.read_bytes()==before and s['budget'].inspect()['used']['retained_output_bytes']==size


def test_generation_collision_stays_pending_without_borrowing_old_user_binding(retained):
    s=retained;_,size=append(s,info('msg_original',role='user'),generation=2)
    assert s['budget'].inspect()['used']['retained_output_bytes']==0
    assert routing.summary(s['o'],s['pid'],s['sid'])['pending_bytes']==size
    with pytest.raises(Conflict):confirmed(s)


def test_lost_accounting_acknowledgement_is_not_automatically_charged_again(retained,monkeypatch):
    s=retained;original=TaskResourceBudget.consume;calls=[]
    def lost(budget,**units):
        calls.append(units);original(budget,**units);raise OSError('UNIT saved quota but acknowledgement lost')
    monkeypatch.setattr(TaskResourceBudget,'consume',lost)
    append(s,info('msg_unknown_charge'));before=s['budget'].path.read_bytes()
    append(s,info('msg_unknown_charge'));append(s,part('msg_unknown_charge'))
    assert len(calls)==1 and s['budget'].path.read_bytes()==before
    with pytest.raises(Conflict):confirmed(s)


def test_session_status_metadata_does_not_allocate_or_charge_output_resource(retained):
    s=retained;before=s['budget'].path.read_bytes()
    append(s,{'type':'session.status','properties':{'sessionID':'ses_original','status':{'type':'idle'}}})
    assert s['budget'].path.read_bytes()==before and routing.summary(s['o'],s['pid'],s['sid'])['pending_bytes']==0
