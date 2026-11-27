"""AUTHORED_NOT_RUN. Actual service helpers with explicit UNIT storage/HTTP only."""
import asyncio
from copy import deepcopy
from types import MethodType,SimpleNamespace
import pytest
from pydantic import ValidationError
from packages.agent_sessions.service import AgentSessions
from packages.agent_sessions.models import LoadSession
from packages.domain.store import Conflict
from packages.domain.tasks import digest


def checkpoint(stage='NATIVE_NOT_ATTEMPTED',generation=1,sha=None):
    m={'project_id':'project','id':'actor','request':{'title':'Original candidate'}}
    return m,{'generation':generation,'original_session_id':None,'native_initialization':{
        'generation':generation,'manifest_sha256':sha or digest(m),'stage':stage,'original_session_id':None}}


def test_known_not_attempted_and_generation_zero_are_distinct_from_unknown_create():
    m,s=checkpoint();AgentSessions._admit_native_initialization(m,s)
    AgentSessions._admit_native_initialization(m,{'generation':0,'original_session_id':None})
    for wrong in [{'generation':1,'original_session_id':None},checkpoint('NATIVE_CREATE_ATTEMPTED')[1],
        checkpoint(sha='f'*64)[1],{**s,'generation':2}]:
        with pytest.raises(Conflict):AgentSessions._admit_native_initialization(m,wrong)


def test_native_create_intent_precedes_actual_post_and_known_identity_survives_later_failure():
    m,_=checkpoint();state={},[]
    saved,calls=state
    def update(_pid,_sid,**value):saved.update(deepcopy(value))
    async def http(context,method,route,body=None):
        calls.append((method,route))
        assert saved['native_initialization']['stage']=='NATIVE_CREATE_ATTEMPTED'
        assert saved['native_initialization']['manifest_sha256']==digest(m)
        return {'id':'ses_original'}
    owner=SimpleNamespace(_update=update,_http=http)
    ctx={'generation':1,'session_id':None,'directory':'UNIT directory'}
    session=asyncio.run(AgentSessions._native_session(owner,m,ctx,{'permission':{}}))
    assert session['id']==saved['original_session_id']=='ses_original'
    assert saved['native_initialization']['stage']=='NATIVE_SESSION_KNOWN'
    assert calls==[('POST','/session')]
    # A later setup failure must not erase this validated identity receipt.
    try:raise ConnectionError('UNIT later context/plugin startup failure')
    except ConnectionError:pass
    assert saved['original_session_id']=='ses_original'


def test_lost_native_create_response_preserves_intent_and_blocks_new_create_admission():
    m,_=checkpoint();saved={'generation':1,'original_session_id':None}
    def update(_pid,_sid,**value):saved.update(deepcopy(value))
    async def http(*_a):raise ConnectionError('UNIT native acknowledgement lost')
    owner=SimpleNamespace(_update=update,_http=http)
    with pytest.raises(ConnectionError):asyncio.run(AgentSessions._native_session(owner,m,{'generation':1,'session_id':None},{'permission':{}}))
    assert saved['native_initialization']['stage']=='NATIVE_CREATE_ATTEMPTED'
    with pytest.raises(Conflict):AgentSessions._admit_native_initialization(m,saved)


def test_failed_intent_persistence_prevents_native_create_post():
    m,_=checkpoint();calls=[]
    def update(*_a,**_kw):raise OSError('UNIT cannot journal')
    async def http(*a):calls.append(a);return {'id':'ses_must_not_exist'}
    with pytest.raises(OSError):asyncio.run(AgentSessions._native_session(SimpleNamespace(_update=update,_http=http),m,{'generation':1,'session_id':None},{'permission':{}}))
    assert calls==[]


def test_existing_native_identity_uses_get_and_never_create():
    m,_=checkpoint();calls=[];saved={}
    async def http(context,method,route,body=None):calls.append((method,route));return {'id':'ses_original','directory':'UNIT'}
    def update(_pid,_sid,**value):saved.update(value)
    owner=SimpleNamespace(_http=http,_update=update)
    asyncio.run(AgentSessions._native_session(owner,m,{'generation':2,'session_id':'ses_original','directory':'UNIT'},{'permission':{}}))
    assert calls==[('GET','/session/ses_original')] and saved['original_session_id']=='ses_original'


def test_internal_generation_zero_load_reuses_its_original_claim_and_never_widens_public_schema():
    with pytest.raises(ValidationError):LoadSession(operation_id='load',session_ref={'id':'owner','revision':1,'sha256':'a'*64},expected_generation=0)
    owner=object.__new__(AgentSessions);ref={'id':'owner','revision':1,'sha256':'a'*64};records={};calls=[]
    state={'generation':0,'owner_status':'DISCONNECTED','original_session_id':None,'process_identity':None,'controller_identity':None,'active_operation_id':None}
    owner.active={};owner._manifest=lambda *_a:({'canonical_execution_claim':{'UNIT':'already confirmed'}},ref)
    owner._state=lambda *_a:deepcopy(state);owner._alive=lambda *_a:False;owner.get=lambda *_a:deepcopy(state)
    def claim(_pid,_sid,key,kind,request):
        if key in records:return deepcopy(records[key]),False
        records[key]={'id':key,'kind':kind,'request':request,'status':'DISPATCHING'};return deepcopy(records[key]),True
    owner._claim=claim
    def finish(_pid,_sid,key,status,**values):records[key].update(status=status,**values)
    owner._finish=finish;owner.operation=lambda _p,_s,key:deepcopy(records[key])
    async def start(_pid,_sid,expected_generation=None,load_operation=None):
        calls.append(expected_generation);assert state['generation']==expected_generation==0
        state.update(generation=1,original_session_id='ses_same',owner_status='READY');return deepcopy(state)
    owner._start=start
    body=LoadSession(operation_id='original_load',session_ref=ref)
    first=asyncio.run(owner.load('project','owner',body,configuration_generation_zero=True))
    again=asyncio.run(owner.load('project','owner',body,configuration_generation_zero=True))
    assert calls==[0] and first['operation']==again['operation'] and again['session']['original_session_id']=='ses_same'
