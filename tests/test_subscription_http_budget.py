"""AUTHORED_NOT_RUN: real metadata Store/authority/ledger, UNIT SDK boundary only.

No auth content, account login, SDK process, model, network or native qualification.
"""
import asyncio
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import pytest
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.agent_sessions.model_authority import ModelAuthorityCall,admit,admit_http
from packages.agent_sessions.subscription_http_calls import SubscriptionHTTPCall
from packages.agent_sessions import subscription_http_budget as budget
from test_session_full_diffs import coding
from test_model_authority import setup


@pytest.fixture
def measured(coding):
    s=coding;owner=s['value'];s['manifest']=deepcopy(s['manifest']);s['manifest']['provider_auth_mode']='OAUTH_SUBSCRIPTION';s['manifest']['request']['max_calls']=3
    owner._write(owner._path(s['pid'],s['ident'],'manifest.json'),s['manifest'],replace=True);s['ref']=owner._manifest(s['pid'],s['ident'])[1]
    _,_,ctx,_,_,calls,body=setup(s)
    with owner.store.connect() as writer:writer.execute('BEGIN IMMEDIATE');budget.initialize(owner,s['manifest'])
    s.update(context=ctx,body=body,calls=calls);return s


def plan(s,body=None):return asyncio.run(admit(s['value'],s['manifest'],s['context'],body or s['body']))
def hop(s,reservation,ordinal=1,**changes):return {**s['body'],'schema':'eeas.model-authority-http-hop.v1','hop':ordinal,'plan_sha256':reservation['plan_sha256'],'request_sha256':digest({'UNIT_BODY':ordinal}),**changes}
def reserve(s,body):return asyncio.run(admit_http(s['value'],s['manifest'],s['context'],body))
def current(s):return budget.read(s['value'],s['manifest'])


def test_each_original_runtime_retry_consumes_one_shared_reservation(measured):
    s=measured;p=plan(s)
    for n in range(1,4):assert reserve(s,hop(s,p,n))['reserved']==n
    with pytest.raises(Conflict,match='ALLOWANCE_EXHAUSTED'):reserve(s,hop(s,p,4))
    assert len(current(s)['reservations'])==3 and s['value']._state(s['pid'],s['ident']).get('model_calls_reserved') is None


def test_same_hop_cannot_replay_after_unknown_host_response(measured):
    s=measured;p=plan(s);body=hop(s,p);reserve(s,body);before=deepcopy(current(s))
    with pytest.raises(Conflict,match='ALREADY_RESERVED'):reserve(s,body)
    assert current(s)==before


def test_native_child_shares_parent_allowance_without_another_execution_loop(measured):
    s=measured;p=plan(s);reserve(s,hop(s,p));owner=s['value'];ctx=s['context']
    # Native lineage and work provenance are separate proofs. Persist the
    # original parent task events instead of treating a cached ID as both.
    for event in [
        {'type':'message.updated','properties':{'info':{'id':'msg_parent','role':'assistant','parentID':'msg_original','sessionID':ctx['session_id']}}},
        {'type':'message.part.updated','properties':{'part':{'id':'prt_task','type':'tool','tool':'task','messageID':'msg_parent','sessionID':ctx['session_id'],
            'state':{'status':'running','metadata':{'sessionId':'ses_child','parentSessionId':ctx['session_id']}}}}}]:
        owner._event(s['pid'],s['ident'],'ORIGINAL_EVENT',{'generation':1,'native_payload':event})
    async def native(_ctx,method,route):
        assert method=='GET' and route=='/session/ses_child'
        s['calls']['native_get'].append(route)
        return {'id':'ses_child','parentID':ctx['session_id'],'directory':ctx['directory']}
    owner._http=native
    child={**s['body'],'native_session_id':'ses_child','user_message_id':'msg_child','step':2};c=plan(s,child)
    reserve(s,hop(s,c,2,native_session_id='ses_child',user_message_id='msg_child',step=2));assert len(current(s)['reservations'])==2
    assert len({r['operation_id'] for r in current(s)['reservations']})==1
    assert s['calls']['native_get']==['/session/ses_child']


def test_reconnect_generation_preserves_earlier_reservations(measured):
    from packages.agent_sessions.resource_retention import dispatch_scope
    s=measured;p=plan(s);reserve(s,hop(s,p));owner=s['value'];first=deepcopy(current(s)['reservations'][0])
    # A reconnect cannot borrow an old generation's message binding. Model a
    # newly claimed message and the same pre-dispatch binding as the send path.
    owner._finish(s['pid'],s['ident'],'unit_task','UNKNOWN_OUTCOME')
    owner._update(s['pid'],s['ident'],generation=2,active_operation_id='unit_reconnect')
    s['context']['generation']=2;s['body']={**s['body'],'generation':2,'user_message_id':'msg_reconnect'}
    owner._claim(s['pid'],s['ident'],'unit_reconnect','MESSAGE',{'session_ref':s['ref'],'generation':2,'raw_text':'Continue after reconnect'})
    operation=owner._finish(s['pid'],s['ident'],'unit_reconnect','DISPATCHING',generation=2,
        original_session_id=s['context']['session_id'],native_message_id='msg_reconnect')
    with dispatch_scope(owner,s['manifest'],s['context'],operation):pass
    p=plan(s);reserve(s,hop(s,p))
    assert [r['call']['generation'] for r in current(s)['reservations']]==[1,2]
    assert current(s)['reservations'][0]==first
    assert [r['operation_id'] for r in current(s)['reservations']]==['unit_task','unit_reconnect']
    assert budget.initialize(owner,s['manifest'])==current(s)


@pytest.mark.parametrize('change',[{'plan_sha256':'f'*64},{'step':2},{'native_session_id':'ses_child'},{'user_message_id':'msg_other'}])
def test_broadened_plan_cannot_release_an_http_forward(measured,change):
    s=measured;p=plan(s);s['context']['model_authority']['known_sessions'].add('ses_child')
    with pytest.raises(Conflict):reserve(s,hop(s,p,**change))
    assert current(s)['reservations']==[]


def test_late_source_retraction_blocks_http_after_prior_plan_was_admitted(measured):
    s=measured;p=plan(s)
    async def dirty(*_a,**_k):raise Conflict('EDITOR_CAPTURE_DIRTY: UNIT newer buffer')
    s['value'].editor_captures.broker.admit=dirty
    with pytest.raises(Conflict,match='EDITOR_CAPTURE_DIRTY'):reserve(s,hop(s,p))
    assert current(s)['reservations']==[]


def test_pre_forward_persistence_failure_never_returns_an_admission(measured,monkeypatch):
    s=measured;p=plan(s);write=s['value']._write
    def fail(path,value,**kwargs):
        if path.name=='subscription-http-budget.json':raise OSError('UNIT quota failure')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['value'],'_write',fail)
    with pytest.raises(OSError):reserve(s,hop(s,p))
    assert current(s)['reservations']==[]


def test_saved_reservation_survives_ambiguous_write_acknowledgement(measured,monkeypatch):
    s=measured;p=plan(s);write=s['value']._write
    def lost_ack(path,value,**kwargs):
        write(path,value,**kwargs)
        if path.name=='subscription-http-budget.json':raise OSError('UNIT saved but acknowledgement lost')
    monkeypatch.setattr(s['value'],'_write',lost_ack)
    with pytest.raises(OSError):reserve(s,hop(s,p))
    assert len(current(s)['reservations'])==1
    monkeypatch.setattr(s['value'],'_write',write)
    with pytest.raises(Conflict,match='ALREADY_RESERVED'):reserve(s,hop(s,p))


def test_duplicate_plan_and_missing_or_corrupt_ledger_never_reset_the_allowance(measured):
    s=measured;plan(s)
    with pytest.raises(Conflict,match='ORIGINAL_PLAN_RESERVED'):plan(s)
    path=budget._path(s['value'],s['manifest']);original=path.read_bytes();path.write_bytes(b'not a valid ledger')
    with pytest.raises(Conflict):budget.initialize(s['value'],s['manifest'])
    assert path.read_bytes()==b'not a valid ledger';path.write_bytes(original)
    s['value']._update(s['pid'],s['ident'],native_turns_started=1);path.unlink()
    with pytest.raises(Conflict,match='LEGACY_UNMETERED_OWNER'):budget.initialize(s['value'],s['manifest'])
    assert not path.exists()


def test_metadata_budget_view_does_not_start_owner_rewrite_or_read_source_body(measured,monkeypatch):
    s=measured;p=plan(s);reserve(s,hop(s,p));path=budget._path(s['value'],s['manifest']);before=path.read_bytes()
    monkeypatch.setattr(s['value'],'open',lambda *_a,**_k:pytest.fail('No owner start'))
    monkeypatch.setattr(s['value'],'_write',lambda *_a,**_k:pytest.fail('No metadata query writes'))
    value=budget.public(s['value'],s['pid'],s['ident'],s['ref']['sha256'])
    assert value['reservations']==1 and value['read_only'] and value['model_calls']==value['metadata_writes']==0
    assert value['commercial_usage']=='NOT_MEASURED' and 'plans' not in value and path.read_bytes()==before
    with pytest.raises(Conflict):budget.public(s['value'],s['pid'],s['ident'],'f'*64)


def test_parallel_transport_reservations_share_one_writer_budget(measured):
    s=measured;p=plan(s);owner=s['value']
    def attempt(n):
        body=SubscriptionHTTPCall.model_validate(hop(s,p,n))
        try:
            with owner.store.connect() as writer:writer.execute('BEGIN IMMEDIATE');return budget.reserve(owner,s['manifest'],body,'unit_task')['reserved']
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(attempt,range(1,9)))
    assert sorted(x for x in results if x is not None)==[1,2,3] and len(current(s)['reservations'])==3


def test_final_writer_rechecks_original_operation_before_durable_http_reservation(measured,monkeypatch):
    s=measured;p=plan(s);owner=s['value'];check=owner._configuration_current
    def changed(manifest,**kwargs):
        result=check(manifest,**kwargs);state=owner._state(s['pid'],s['ident']);state['active_operation_id']='newer_original_work'
        owner._write(owner._path(s['pid'],s['ident'],'state.json'),state,replace=True);return result
    monkeypatch.setattr(owner,'_configuration_current',changed)
    with pytest.raises(Conflict,match='ORIGINAL_OPERATION_CHANGED'):reserve(s,hop(s,p))
    assert current(s)['reservations']==[]


def test_exhaustion_keeps_readonly_lookup_and_negative_control_metadata_available(measured):
    s=measured;p=plan(s)
    for n in range(1,4):reserve(s,hop(s,p,n))
    owner=s['value'];receipt,created=owner._claim(s['pid'],s['ident'],'explicit_stop','INTERRUPT',{'session_ref':s['ref'],'generation':1,'operation_id':'explicit_stop'})
    assert created and receipt['status']=='RECORDED_NOT_DISPATCHED'
    value=budget.public(owner,s['pid'],s['ident'],s['ref']['sha256'])
    assert value['reservations']==value['max_calls']==3 and value['model_calls']==0


def test_ledger_byte_capacity_refuses_new_plan_before_overwriting_original(measured,monkeypatch):
    from packages.domain.tasks import canonical
    s=measured;plan(s);value=current(s);path=budget._path(s['value'],s['manifest']);original=path.read_bytes()
    monkeypatch.setattr(budget,'MAX_BYTES',len(canonical(value).encode()))
    with pytest.raises(Conflict,match='LEDGER_CAPACITY'):plan(s,{**s['body'],'step':2})
    assert path.read_bytes()==original and current(s)==value
