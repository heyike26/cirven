"""Source52 kernel contract: real service/Store and controlled external SDK/TCP.

Coordinator in these tests is an explicit contract fixture, not production
native-message/CLI qualification. No Windows, account or paid model is used.
"""
import asyncio
from copy import deepcopy
from datetime import timedelta,timezone
import http.client
import json
from types import SimpleNamespace
import time
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from packages.agent_sessions.models import OrdinaryGuidanceInput,OpenSession
from packages.agent_sessions.wire import ProviderWire
from packages.domain.store import Conflict
from packages.domain.tasks import canonical,digest
from test_agent_sessions_deadline_recovery import Scene,Clock,clock,check,until
from test_agent_session_config_continuity import bind_controlled_transport,change_capability
from test_provider_wire_shutdown import Gateway


REF={'id':'ordinary-parent52','revision':1,'sha256':'a'*64}
FORMAT={'type':'json_schema','schema':{'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False},'retryCount':0}


class CoordinatorContract:
    """External collaborator fixture. Business service methods are not patched."""
    def __init__(self,deadline):
        self.absolute=deadline;self.dispatches=[];self.created=[]
        request={'operation_id':REF['id'],'raw_text':'original ordinary fixture request'}
        self.parent={'request':request,'request_sha256':digest(request)}

    def _record(self,parent):
        if parent!=self.parent['request']['operation_id']:
            raise Conflict('Ordinary parent does not own this admission')
        return deepcopy(self.parent)
    def validate_admission(self,ref,request=None):
        if ref!=REF:raise Conflict('Exact UNIT ordinary ref changed')
        return {'absolute_deadline':self.absolute,'parent_operation_id':self.parent['request']['operation_id']}
    def deadline(self,manifest):return self.absolute
    def authorize_dispatch(self,manifest,body):self.validate_admission(manifest['native_message_admission'])
    def authorize_guidance(self,manifest,body):self.validate_admission(manifest['native_message_admission'])
    def before_dispatch(self,manifest,context,operation):
        self.validate_admission(manifest['native_message_admission'])
        check(bool(operation['native_message_id'])and operation['generation']==context['generation'])
        self.dispatches.append(deepcopy(operation))
    def output_format(self,manifest):return deepcopy(FORMAT)
    def owner_created(self,manifest,ref):self.created.append((deepcopy(manifest),ref))
    def permissions(self,manifest):return {'*':'deny','eeas_native_message_call':'allow'}
    def instructions(self,manifest):return 'Keep the original frozen target; answer briefly, then continue. ACK is not semantic adoption.'


def ordinary(s):
    deadline=(Clock.now(timezone.utc)+timedelta(seconds=180)).isoformat()
    hook=CoordinatorContract(deadline);s.owner.native_messages=hook
    s.manifest['native_message_admission']=deepcopy(REF)
    s.manifest['request'].update(ttl_seconds=180,max_calls=8,max_output_tokens=4096)
    s.owner._write(s.owner._path(s.pid,s.sid,'manifest.json'),s.manifest,replace=True)
    s.ref=s.owner._manifest(s.pid,s.sid)[1]
    s.context.update(expires_at=deadline,native_message_absolute_deadline=deadline)
    return hook


def guide(s,key,text,intent='STEER',**extra):
    return s.owner.guidance.submit(s.pid,s.sid,s.body(key,raw_text=text,intent=intent,expected_active_operation_id='parent',**extra))


def test_ordinary_guidance_body_is_exact_private_public_open_cannot_accept_admission():
    value=OrdinaryGuidanceInput.model_validate({'operation_id':'guide52','expected_native_message_ref':REF,'raw_text':'原文\n **不可改写**'})
    check(value.intent=='AUTO'and value.raw_text=='原文\n **不可改写**')
    for extra in [{'expected_review_ref':REF},{'generation':1},{'intent':'CANCEL'}]:
        with pytest.raises(ValidationError):OrdinaryGuidanceInput.model_validate({**value.model_dump(),**extra})
    with pytest.raises(ValidationError):OpenSession.model_validate({'operation_id':'x','provider_profile_ref':REF,'capability_binding_sha256':'b'*64,'native_message_admission':REF})


def test_initial_two_guidance_same_owner_raw_schema_absolute_deadline_and_status_zero_post(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);hook=ordinary(s)
        try:
            first=await s.owner.send(s.pid,s.sid,s.body('parent',raw_text='初始\n原始目标',timeout_seconds=300))
            deadline=first['deadline'];check(deadline==s.context['expires_at'])
            Clock.advance(40);one=await guide(s,'g1','别改原稿；补一个摘要。')
            Clock.advance(30);two=await guide(s,'g2','无关问题先短答，然后继续原目标。')
            before=deepcopy(s.sdk.posts);snapshot=s.operation()
            status=await guide(s,'status',' 现在进度如何？ ','AUTO')
            check(status['role']=='LOCAL_STATUS'and status['model_calls']==0 and s.sdk.posts==before)
            check(s.operation()==snapshot and s.operation()['deadline']==deadline and not s.operation()['user_guidance_extended_deadline'])
            check(len(hook.dispatches)==3 and s.state()['native_turns_started']==3)
            calls=[p['body']for p in s.sdk.posts if p['path'].endswith('/prompt_async')]
            check([p['parts'][0]['text']for p in calls]==['初始\n原始目标','别改原稿；补一个摘要。','无关问题先短答，然后继续原目标。'])
            check(all(p['format']==FORMAT for p in calls)and len({p['messageID']for p in calls})==3)
            check(all(op['original_session_id']==s.sdk.native for op in [first,one,two]))
        finally:await s.close()
    asyncio.run(run())


def test_ordinary_human_wait_never_extends_absolute180_and_monitor_expires(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);ordinary(s)
        try:
            await s.owner.send(s.pid,s.sid,s.body('parent',raw_text='Frozen original task',timeout_seconds=180))
            deadline=s.operation()['deadline'];Clock.advance(10)
            s.sdk.tool_wait(s.operation()['native_message_id'],'QUESTION');await s.inspect()
            check(s.operation()['human_wait']['status']=='WAITING_FOR_USER')
            Clock.advance(30);s.sdk.requests=[];await s.inspect()
            check(s.operation()['deadline']==deadline)
            Clock.advance(141);monitor=s.monitor();await asyncio.wait_for(monitor,2)
            check(s.state()['owner_status']=='EXPIRED'and s.context['process'].terminated==1)
            check(s.operation()['status']=='UNKNOWN_OUTCOME'and s.sdk.post_count('/prompt_async')==1)
        finally:await s.close()
    asyncio.run(run())


def test_eight_explicit_turns_shared_across_guidance_then_ninth_rejected(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);ordinary(s)
        try:
            await s.send()
            for i in range(7):await guide(s,'g'+str(i),'Explicit correction '+str(i))
            check(s.state()['native_turns_started']==8 and s.sdk.post_count('/prompt_async')==8)
            with pytest.raises(Conflict):await guide(s,'g-over','Ninth correction')
            check(s.sdk.post_count('/prompt_async')==8)
        finally:await s.close()
    asyncio.run(run())


def test_eight_api_reservations_are_parent_shared_not_guidance_reset(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);ordinary(s)
        try:
            await s.send()
            for i in range(8):s.owner._reserve_rpc(s.pid,s.sid,'unit-rpc-'+str(i))
            with pytest.raises(Conflict):s.owner._reserve_rpc(s.pid,s.sid,'ninth-rpc')
            with pytest.raises(Conflict):await guide(s,'guide-after-rpc-cap','Continue')
            check(s.state()['model_calls_reserved']==8 and s.sdk.post_count('/prompt_async')==1)
        finally:await s.close()
    asyncio.run(run())


def test_unknown_guidance_same_operation_replay_does_not_repost_or_complete_parent(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);ordinary(s)
        try:
            await s.send();s.sdk.fail_prompt=True
            with pytest.raises(Exception):await guide(s,'lost','Exact unknown correction')
            old=s.owner.operation(s.pid,s.sid,'lost');check(old['status']=='UNKNOWN_OUTCOME')
            count=s.sdk.post_count('/prompt_async');again=await guide(s,'lost','Exact unknown correction')
            check(again==old and s.sdk.post_count('/prompt_async')==count and s.operation()['status']=='ACCEPTED')
        finally:await s.close()
    asyncio.run(run())


def test_old_terminal_idle_cannot_settle_latest_guidance(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);ordinary(s)
        try:
            initial=await s.send();latest=await guide(s,'latest','Latest correction')
            s.sdk.terminal(initial['native_message_id'],error=False);await s.inspect()
            check(s.operation()['status']=='ACCEPTED'and s.state()['active_operation_id']=='parent')
            s.sdk.terminal(latest['native_message_id'],error=False,identifier='msg_latest');await s.inspect()
            check(s.operation()['status']=='RETURNED'and s.operation()['native_assistant_ids']==['msg_latest'])
        finally:await s.close()
    asyncio.run(run())


def test_future_config_change_preserves_observation_status_stop_but_not_steer(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);ordinary(s);bind_controlled_transport(s)
        try:
            await s.send();change_capability(s);before=len(s.sdk.posts)
            status=await guide(s,'status','进度','AUTO')
            check(status['role']=='LOCAL_STATUS'and len(s.sdk.posts)==before)
            with pytest.raises(Conflict):await guide(s,'no-new-authority','Continue with new requirement')
            check(len(s.sdk.posts)==before)
            stopped=await s.stop();check(stopped['status']=='RETURNED'and s.sdk.post_count('/abort')==1)
        finally:await s.close()
    asyncio.run(run())


@pytest.mark.parametrize('change',[{'generation':4},{'session_ref':{'id':'f'*32,'revision':1,'sha256':'f'*64}}])
def test_wrong_owner_or_generation_guidance_rejected_without_post(tmp_path,clock,change):
    async def run():
        s=Scene(tmp_path);ordinary(s)
        try:
            await s.send();count=len(s.sdk.posts)
            with pytest.raises(Conflict):await guide(s,'wrong','New instruction',**change)
            check(len(s.sdk.posts)==count)
        finally:await s.close()
    asyncio.run(run())


@pytest.mark.parametrize('extra',[{'workflow_ref':REF},{'allowed_effects':['WORKSPACE_WRITE']},{'native_review':True}])
def test_private_admission_mutual_exclusions_before_process(tmp_path,clock,extra):
    async def run():
        s=Scene(tmp_path);ordinary(s)
        try:
            body={'operation_id':'forbidden-open','workspace_ref':s.request['workspace_ref'],'provider_profile_ref':s.profile_ref,
                'capability_binding_sha256':s.request['capability_binding_sha256'],'ttl_seconds':180,'max_calls':8,'max_output_tokens':4096}
            kwargs={'native_message_admission':REF}
            if extra.get('native_review'):kwargs['native_review_admission']=REF
            else:body.update(extra)
            with pytest.raises(Conflict):await s.owner.open(s.pid,body,**kwargs)
            check(not s.sdk.posts)
        finally:await s.close()
    asyncio.run(run())


def test_private_open_exact_ref_idempotency_and_no_load_new_generation(tmp_path,clock,monkeypatch):
    async def run():
        s=Scene(tmp_path);hook=ordinary(s);starts=[]
        try:
            monkeypatch.setattr(s.owner.providers,'resolve',lambda ref:s.profile)
            async def external_start(pid,sid):starts.append(sid);return s.owner.get(pid,sid)
            monkeypatch.setattr(s.owner,'_start',external_start)
            body={'operation_id':'private-open','provider_profile_ref':s.profile_ref,'capability_binding_sha256':s.request['capability_binding_sha256'],'ttl_seconds':180,'max_calls':8,'max_output_tokens':4096}
            one=await s.owner.open(s.pid,body,native_message_admission=REF)
            two=await s.owner.open(s.pid,body,native_message_admission=REF)
            check(one['session_ref']==two['session_ref']and len(starts)==len(hook.created)==1)
            with pytest.raises(Conflict):await s.owner.open(s.pid,body,native_message_admission={**REF,'sha256':'b'*64})
            with pytest.raises(Conflict):await s.owner.load(s.pid,one['session_ref']['id'],{'operation_id':'no-reload','session_ref':one['session_ref']})
        finally:await s.close()
    asyncio.run(run())


def test_bridge_unconfirmed_close_never_pops_or_claims_closed(tmp_path,clock):
    class Bridge:
        fail=True
        def close(self):
            if self.fail:raise Conflict('UNIT bridge thread stop unconfirmed')
    async def run():
        s=Scene(tmp_path);ordinary(s);bridge=Bridge();s.context['native_message']={'bridge':bridge}
        try:
            with pytest.raises(Conflict):await s.owner._dispose(s.pid,s.sid,s.context,status='DISCONNECTED')
            check(s.owner.active[s.sid] is s.context and s.context['process'].terminated==1)
            check(s.state()['owner_status']=='BLOCKED_RESOURCE_STOP_UNCONFIRMED')
            bridge.fail=False;await s.owner._dispose(s.pid,s.sid,s.context,status='DISCONNECTED')
            check(s.sid not in s.owner.active and s.context['process'].terminated==1)
        finally:await s.close()
    asyncio.run(run())


def test_ordinary_config_keeps_question_and_all_other_tools_denied(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);ordinary(s)
        try:
            wire=SimpleNamespace(base_url='http://127.0.0.1:9/v1',key='UNIT_NOT_ACCOUNT')
            config=s.owner._config(s.manifest,s.profile,s.caps.native_binding_snapshot(),wire)
            check(config['permission']=={'*':'deny','eeas_native_message_call':'allow'})
            check(all(agent['steps']==8 for agent in config['agent'].values())and config['mcp']=={})
        finally:await s.close()
    asyncio.run(run())


def test_real_tcp_wire_exact_tool_names_tokens_deadline_and_eight_calls(tmp_path):
    gateway=Gateway();calls=[]
    profile=SimpleNamespace(model_id='ordinary52-wire',api_key='UNIT_NOT_ACCOUNT',provider_url=f'http://127.0.0.1:{gateway.server.server_port}/v1',transport_policy=None)
    def admit(value):
        if len(calls)>=8:raise Conflict('Eight actual fixture wire calls exhausted')
        calls.append(value);return str(len(calls))
    wire=ProviderWire(profile,admit=admit,complete=lambda _:None,max_output_tokens=4096,
        allowed_tool_names={'eeas_native_message_call','StructuredOutput'},absolute_deadline=time.time()+180).start()
    def request(names,tokens=4096):
        url=urlsplit(wire.base_url);client=http.client.HTTPConnection(url.hostname,url.port,timeout=5)
        body={'model':profile.model_id,'stream':True,'max_tokens':tokens,'messages':[{'role':'user','content':'Original UNIT wire input'}],
            'tools':[{'type':'function','function':{'name':n,'parameters':{'type':'object'}}}for n in names]}
        try:
            client.request('POST','/v1/chat/completions',body=canonical(body),headers={'Authorization':'Bearer '+wire.key,'Content-Type':'application/json'})
            result=client.getresponse();status=result.status;result.read();return status
        finally:client.close()
    try:
        for names in [[],['question'],['read'],['skill'],['eeas_native_message_call','eeas_native_message_call']]:check(request(names)==403)
        check(request(['StructuredOutput'],4097)==403 and not calls and not gateway.requests)
        for _ in range(8):check(request(['eeas_native_message_call','StructuredOutput'])==200)
        check(request(['StructuredOutput'])==403 and len(calls)==len(gateway.requests)==8)
        wire.absolute_deadline=time.time()-1
        check(request(['StructuredOutput'])==403 and len(gateway.requests)==8)
    finally:
        wire.close();gateway.close()
        check(wire.close_confirmed and not any(t.is_alive()for t in wire.owned_threads))
