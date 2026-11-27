"""AUTHORED_NOT_RUN: actual candidate journal/official-client boundary.

Most protocol responses are explicit UNIT values. The final scene eventually
starts a stdlib MCP fixture through the installed official client; it is not
native Windows, arbitrary-code sandbox or original OpenCode qualification.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from mcp.types import CallToolResult,TextContent
from packages.agent_sessions.experience_candidates import ExperienceReview
from packages.agent_sessions.experience_tool_validation import ExperienceToolValidation
from packages.domain.store import Conflict,InvalidInput
from packages.domain.tasks import digest
from test_experience_candidates import experience,proposal,propose,review
import packages.agent_sessions.experience_tool_validation as implementation


def material(text,error=False,structured=None):return {'content':[{'type':'text','text':text}],'structuredContent':structured,'isError':error}
def tool_candidate(s,configuration=None):
    c=propose(s,proposal(s,proposal={'kind':'MCP_TOOL','id':'learned-tool','configuration':configuration or {'type':'remote','url':'https://example.invalid/mcp','enabled':False,'timeout':1000}}))
    cases=[{'kind':'MCP_TOOL','id':'positive','label':'normal value','purpose':'POSITIVE','tool_name':'echo','input':{'text':'ok'},'expected_result':material('ok')},
           {'kind':'MCP_TOOL','id':'negative','label':'explicit invalid input','purpose':'NEGATIVE','tool_name':'echo','input':{'bad':True},'expected_result':material('invalid',True)}]
    effect='PROCESS_START' if c['configuration']['type']=='local' else 'REMOTE_MCP_CONNECT'
    r=s['candidates'].review(s['pid'],s['sid'],review(s,c,cases=cases,max_model_calls=0,allowed_effects=[effect]))
    b={'operation_id':'validation_one','candidate_ref':c['record_ref'],'review_ref':r['record_ref'],
       'validation_confirmed':True,'allowed_effects':[effect],'process_boundary_acknowledged':True}
    return c,r,b


def protocol(monkeypatch,validation,*,wrong=False,delay=False,structured=None):
    calls=[];connections=[]
    @asynccontextmanager
    async def connect(configuration,workspace):
        connections.append(str(workspace));yield object(),object()
    class Client:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def initialize(self):return SimpleNamespace(protocol_version='UNIT')
        async def list_tools(self,**kwargs):
            tool=SimpleNamespace(name='echo',input_schema={'type':'object'},model_dump=lambda **_: {'name':'echo','input_schema':{'type':'object'}})
            return SimpleNamespace(tools=[tool],next_cursor=None)
        async def call_tool(self,name,arguments,**kwargs):
            calls.append(deepcopy(arguments))
            if delay:await asyncio.sleep(3600)
            bad=arguments.get('bad') is True
            return CallToolResult(content=[TextContent(type='text',text='wrong' if wrong else 'invalid' if bad else arguments['text'])],isError=bad,structuredContent=structured)
    monkeypatch.setattr(validation,'_connection',connect);monkeypatch.setattr(implementation,'ClientSession',Client)
    return calls,connections


def test_fixed_positive_and_negative_original_calls_are_compared_without_enabling_a_candidate(experience,monkeypatch):
    s=experience;c,r,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);calls,connections=protocol(monkeypatch,v)
    result=asyncio.run(v.validate(s['pid'],s['sid'],b));assert result['status']=='PASS' and result['qualified'] is True
    assert len(calls)==len(connections)==2 and len(set(connections))==2
    assert result['enabled'] is False and result['loaded_in_original_session'] is False and result['os_network_sandbox']=='NOT_QUALIFIED'
    assert s['caps']._index()['mcp']=={}
    assert all(case['status']=='PASS' for case in result['cases'])


def test_same_original_validation_number_never_calls_the_tool_again(experience,monkeypatch):
    s=experience;_,_,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);calls,_=protocol(monkeypatch,v)
    first=asyncio.run(v.validate(s['pid'],s['sid'],b));assert asyncio.run(v.validate(s['pid'],s['sid'],b))==first;assert len(calls)==2
    changed={**b,'allowed_effects':['PROCESS_START']}
    with pytest.raises(Conflict):asyncio.run(v.validate(s['pid'],s['sid'],changed))
    assert len(calls)==2


def test_wrong_expected_output_is_failure_and_original_failure_receipt_is_preserved(experience,monkeypatch):
    s=experience;_,_,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);calls,_=protocol(monkeypatch,v,wrong=True)
    result=asyncio.run(v.validate(s['pid'],s['sid'],b));assert result['status']=='FAIL' and result['qualified'] is False
    assert all(case['status']=='FAIL' for case in result['cases']) and len(calls)==2
    assert v.inspect(s['pid'],s['sid'],b['operation_id'],digest(b))==result


def test_structured_result_is_included_in_the_exact_fixed_output_hash(experience,monkeypatch):
    s=experience;_,_,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);protocol(monkeypatch,v,structured={'unexpected':1e-7})
    result=asyncio.run(v.validate(s['pid'],s['sid'],b));assert result['status']=='FAIL'
    assert result['cases'][0]['result']['structuredContent']=={'unexpected':1e-7}


def test_cancelled_original_call_remains_inspectable_and_cannot_be_positively_replayed(experience,monkeypatch):
    s=experience;_,_,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);calls,_=protocol(monkeypatch,v,delay=True)
    async def run():
        task=asyncio.create_task(v.validate(s['pid'],s['sid'],b))
        for _ in range(100):
            if calls:break
            await asyncio.sleep(.001)
        assert len(calls)==1;task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        before=len(calls);result=await v.validate(s['pid'],s['sid'],b);assert result['status']=='CANCELLED';assert len(calls)==before
    asyncio.run(run());assert v.inspect(s['pid'],s['sid'],b['operation_id'],digest(b))['cases'][0]['status']=='UNKNOWN'


def test_interrupted_reserved_intent_without_result_is_unknown_and_never_starts_a_process(experience,monkeypatch):
    s=experience;_,_,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);calls,_=protocol(monkeypatch,v)
    c=s['candidates'];intent={'schema':'eeas.task-experience-validation-intent.v1','kind':'VALIDATION','project_id':s['pid'],'session_id':s['sid'],
        'operation_id':b['operation_id'],'request':b,'request_sha256':digest(b),'created_at':'UNIT','dispatch_status':'RESERVED_NOT_REPLAYABLE','automatic_replay':False}
    with c._guard(s['pid'],s['sid']):c._append(s['pid'],s['sid'],c._index(s['pid'],s['sid']),intent)
    result=asyncio.run(v.validate(s['pid'],s['sid'],b));assert result['status']=='UNKNOWN_OR_IN_PROGRESS';assert calls==[]


@pytest.mark.parametrize('change',[{'validation_confirmed':False},{'process_boundary_acknowledged':False},{'allowed_effects':['PROCESS_START']}])
def test_fresh_execution_requires_exact_review_effect_and_explicit_confirmation(experience,monkeypatch,change):
    s=experience;_,_,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);calls,_=protocol(monkeypatch,v)
    with pytest.raises((Conflict,InvalidInput)):asyncio.run(v.validate(s['pid'],s['sid'],{**b,**change}))
    assert calls==[]


def test_a_review_for_another_candidate_cannot_validate_the_selected_tool(experience,monkeypatch):
    s=experience;_,r,b=tool_candidate(s);other=propose(s,proposal(s,'another_candidate'));b['candidate_ref']=other['record_ref'];v=ExperienceToolValidation(s['candidates']);calls,_=protocol(monkeypatch,v)
    with pytest.raises(Conflict):asyncio.run(v.validate(s['pid'],s['sid'],b))
    assert calls==[]


def test_changed_tool_configuration_bytes_are_not_checked_by_fixture_whitelist(experience,monkeypatch):
    # A general remote candidate follows its explicit official protocol tests;
    # ExtensionRegistry._supported is not broadened or bypassed for ZIP code.
    s=experience;_,_,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);calls,_=protocol(monkeypatch,v)
    monkeypatch.setattr(s['caps'],'configure_mcp',lambda *_:pytest.fail('Validation must not change native config'))
    result=asyncio.run(v.validate(s['pid'],s['sid'],b));assert result['status']=='PASS' and len(calls)==2


def test_installed_official_stdio_client_exercises_a_general_original_test_tool(experience):
    s=experience;executable=str(Path(sys.executable).resolve());server=str(Path(__file__).with_name('experience_tool_server_fixture.py').resolve())
    configuration={'type':'local','command':[executable,server],'files_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in [executable,server]},'enabled':False,'timeout':3000}
    _,_,b=tool_candidate(s,configuration);v=ExperienceToolValidation(s['candidates']);result=asyncio.run(v.validate(s['pid'],s['sid'],b))
    assert result['status']=='PASS';assert len(result['cases'])==2;assert all(row['call_reserved'] for row in result['cases'])
    assert result['model_calls']==0 and result['loaded_in_original_session'] is False
