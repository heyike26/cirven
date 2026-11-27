"""T1 final text contract: actual kernel, controlled SDK/SSE and loopback wire."""
import asyncio
from copy import deepcopy
import http.client
from types import SimpleNamespace
import time
from urllib.parse import urlsplit
import pytest
from packages.agent_sessions import service as kernel
from packages.agent_sessions.wire import ProviderWire
from packages.domain.tasks import canonical
from test_agent_sessions_deadline_recovery import Scene,clock,check
from test_native_message_kernel52 import ordinary,guide
from test_native_message_sse52 import enable,user,commit
from test_provider_wire_shutdown import Gateway


def text_mode(s):
    hook=ordinary(s);hook.output_format=lambda _manifest:None
    return hook


def test_none_format_is_absent_from_initial_and_both_guidance_payloads(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);text_mode(s)
        try:
            await s.send();deadline=s.operation()['deadline'];await guide(s,'g1','原文 **保持**；补证据');await guide(s,'g2','再核这条结论。')
            bodies=[p['body']for p in s.sdk.posts if p['path'].endswith('/prompt_async')]
            check(len(bodies)==3 and all('format'not in p for p in bodies))
            check([p['parts'][0]['text']for p in bodies]==['Read owned.txt only after my answer.','原文 **保持**；补证据','再核这条结论。'])
            check(s.operation()['deadline']==deadline and deadline<=s.context['expires_at']and not s.operation()['user_guidance_extended_deadline'])
        finally:await s.close()
    asyncio.run(run())


def test_actual_start_selects_only_private_tool_and_real_wire_denies_builtin_structured(tmp_path,clock,monkeypatch):
    captured={}
    class BeforeProcess(Exception):pass
    class WireFacet:
        def __init__(self,_profile,**options):captured.update(options);raise BeforeProcess()
    async def start():
        s=Scene(tmp_path);hook=text_mode(s);hook.binding=lambda _manifest,binding,_directory:deepcopy(binding)
        s.owner.active.pop(s.sid)  # Controlled fixture has no process; exercise a fresh startup, not a live-owner bypass.
        try:
            with monkeypatch.context()as m:
                m.setattr(kernel,'ProviderWire',WireFacet)
                with pytest.raises(BeforeProcess):await s.owner._start(s.pid,s.sid)
            check(captured['allowed_tool_names']=={'eeas_native_message_call'})
        finally:await s.close()
    asyncio.run(start())
    gateway=Gateway();calls=[]
    profile=SimpleNamespace(model_id='t1-local-wire',api_key='synthetic-only',provider_url=f'http://127.0.0.1:{gateway.server.server_port}/v1',transport_policy=None)
    wire=ProviderWire(profile,admit=lambda sha:calls.append(sha)or str(len(calls)),complete=lambda _:None,
        max_output_tokens=4096,allowed_tool_names=captured['allowed_tool_names'],absolute_deadline=time.time()+180).start()
    def request(name):
        base=urlsplit(wire.base_url);client=http.client.HTTPConnection(base.hostname,base.port,timeout=5)
        body={'model':profile.model_id,'stream':True,'max_tokens':4096,'messages':[{'role':'user','content':'Original fixture task'}],
            'tools':[{'type':'function','function':{'name':name,'parameters':{'type':'object'}}}]}
        try:
            client.request('POST','/v1/chat/completions',canonical(body),{'Authorization':'Bearer '+wire.key,'Content-Type':'application/json'})
            response=client.getresponse();status=response.status;response.read();return status
        finally:client.close()
    try:
        for name in ['StructuredOutput','question','read','skill']:check(request(name)==403)
        check(not calls and not gateway.requests)
        check(request('eeas_native_message_call')==200 and len(calls)==len(gateway.requests)==1)
    finally:
        wire.close();gateway.close();check(wire.close_confirmed and not any(t.is_alive()for t in wire.owned_threads))


def test_old_text_stop_does_not_complete_new_guidance_latest_text_stop_settles(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);enable(s);s.owner.native_messages.output_format=lambda _:None
        try:
            await s.send();initial=user(s);await guide(s,'new-requirement','保原目标，另补来源。');latest=user(s)
            def text_final(parent,mid,part_id,text):
                commit(s,'message.updated',info={'id':mid,'role':'assistant','sessionID':s.sdk.native,'parentID':parent,'time':{'created':1,'completed':2},'finish':'stop'})
                commit(s,'message.part.updated',part={'id':part_id,'type':'text','sessionID':s.sdk.native,'messageID':mid,'text':canonical({'text':text,'questions':[]})})
            text_final(initial,'msg_old_text','prt_old_text','Old answer must not close new task')
            s.sdk.statuses[s.sdk.native]={'type':'idle'};await s.inspect()
            check(s.operation()['status']=='ACCEPTED'and s.state()['active_operation_id']=='parent')
            text_final(latest,'msg_latest_text','prt_latest_text','Original latest text answer')
            await s.inspect();check(s.operation()['status']=='RETURNED'and s.state()['active_operation_id']is None)
            last=next(m for m in s.state()['messages']if m['info']['id']=='msg_latest_text')
            check(last['info']['parentID']==latest and last['info']['finish']=='stop'and 'structured'not in last['info'])
            check(all('format'not in p['body']for p in s.sdk.posts if p['path'].endswith('/prompt_async')))
            check(s.sdk.post_count('/prompt_async')==2 and s.state()['message_source']=='ORIGINAL_COMMITTED_SSE_UPDATES')
        finally:await s.close()
    asyncio.run(run())
