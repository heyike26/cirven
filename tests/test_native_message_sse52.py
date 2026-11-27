"""Ordinary committed SSE regression: controlled SDK/stream, no CLI/model."""
import asyncio
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import httpx
import pytest
from packages.agent_sessions.service import AgentSessions
from packages.domain.store import Conflict
from packages.domain.tasks import canonical
from test_agent_sessions_deadline_recovery import Scene,clock,check,until
from test_native_message_kernel52 import ordinary,guide


def enable(s):
    ordinary(s)
    s.context['native_message']={'prepared':{},'bridge':SimpleNamespace(close=lambda:None)}
    s.context['connected']=asyncio.Event();s.context['connected'].set()
    s.context['ordinary_message_snapshot']={'generation':s.context['generation'],'session_id':s.sdk.native,'infos':{},'parts':{},'last_event_seq':0}


def commit(s,kind,**props):
    event={'type':kind,'properties':{'sessionID':s.sdk.native,**props}}
    raw=canonical(event).encode()
    seq=s.owner._event(s.pid,s.sid,'ORIGINAL_EVENT',{'native_payload':event,'native_payload_sha256':hashlib.sha256(raw).hexdigest(),'generation':s.context['generation']})
    s.owner._ordinary_message_event(s.context,event,seq)
    return seq


def user(s):
    item=deepcopy(s.sdk.messages[-1]);commit(s,'message.updated',info=item['info'])
    for index,part in enumerate(item['parts']):
        commit(s,'message.part.updated',part={**part,'id':'prt_user'+str(index)+item['info']['id'][4:],'messageID':item['info']['id'],'sessionID':s.sdk.native})
    return item['info']['id']


def assistant(s,parent,identifier,*,done=False):
    info={'id':identifier,'role':'assistant','sessionID':s.sdk.native,'parentID':parent,'time':{'created':1}}
    if done:info.update(time={'created':1,'completed':2},finish='stop',structured={'text':'Exact final','questions':[]})
    commit(s,'message.updated',info=info)
    return info


def deny_message_list(s):
    base=s.sdk.handle;attempts=[]
    async def handler(request):
        if request.method=='GET'and request.url.path=='/session/'+s.sdk.native+'/message':
            attempts.append(request.url.path);return httpx.Response(400,json={'error':'Expected OutputFormatJsonSchema'})
        return await base(request)
    return handler,attempts


def test_ordinary_json_schema_list400_is_not_called_but_nonordinary_original_error_is_preserved(tmp_path,clock):
    async def run():
        for mode in ['ordinary','original']:
            s=Scene(tmp_path/mode)
            try:
                if mode=='ordinary':enable(s)
                await s.send()
                if mode=='ordinary':user(s)
                handler,attempts=deny_message_list(s)
                await s.client.aclose();s.client=httpx.AsyncClient(base_url='http://sdk-unit.invalid',transport=httpx.MockTransport(handler),trust_env=False)
                s.context['client']=s.client
                await s.inspect()
                if mode=='ordinary':
                    check(not attempts and not s.state()['observation_error'])
                    check(s.state()['message_source']=='ORIGINAL_COMMITTED_SSE_UPDATES'and s.operation()['status']=='ACCEPTED')
                    check(s.state()['messages'][0]['info']['id']==s.operation()['native_message_id'])
                else:
                    check(len(attempts)==1 and s.state()['native_messages_error']['http_status']==400)
                    check(s.operation()['status']=='UNKNOWN_OUTCOME')
            finally:await s.close()
    asyncio.run(run())


def test_old_final_cannot_settle_latest_guidance_and_exact_tool_part_remains_in_sse(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);enable(s)
        try:
            await s.send();initial=user(s)
            await guide(s,'guidance52','Keep the original goal; also clarify the evidence.');latest=user(s)
            assistant(s,initial,'msg_old',done=True);s.sdk.statuses[s.sdk.native]={'type':'idle'}
            await s.inspect();check(s.operation()['status']=='ACCEPTED')
            info=assistant(s,latest,'msg_latest')
            part={'id':'prt_tool','sessionID':s.sdk.native,'messageID':info['id'],'type':'tool','tool':'eeas_native_message_call','callID':'call_exact52','state':{'status':'running','input':{'method_id':'read_context','arguments_json':'{}'}}}
            commit(s,'message.part.updated',part=part);await s.inspect()
            observed=next(m for m in s.state()['messages']if m['info']['id']=='msg_latest')
            check(observed['info']['parentID']==latest and observed['parts']==[part])
            part['state']['status']='completed';commit(s,'message.part.updated',part=part)
            assistant(s,latest,'msg_latest',done=True);await s.inspect()
            check(s.operation()['status']=='RETURNED'and s.sdk.post_count('/prompt_async')==2)
        finally:await s.close()
    asyncio.run(run())


def test_delta_removal_child_exclusion_and_snapshot_copy_isolation(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);enable(s)
        try:
            await s.send();uid=user(s);assistant(s,uid,'msg_a')
            part={'id':'prt_text','messageID':'msg_a','sessionID':s.sdk.native,'type':'text','text':'Begin'}
            commit(s,'message.part.updated',part=part)
            commit(s,'message.part.delta',partID='prt_text',messageID='msg_a',field='text',delta=' **exact**')
            first=s.owner._ordinary_messages(s.context);check(first[-1]['parts'][0]['text']=='Begin **exact**')
            first[-1]['info']['parentID']='msg_forged';check(s.owner._ordinary_messages(s.context)[-1]['info']['parentID']==uid)
            commit(s,'message.updated',sessionID='ses_child',info={'id':'msg_child','sessionID':'ses_child','role':'user'})
            check(len(s.owner._ordinary_messages(s.context))==2)
            commit(s,'message.part.removed',messageID='msg_a',partID='prt_text');check(not s.owner._ordinary_messages(s.context)[-1]['parts'])
            commit(s,'message.removed',messageID='msg_a');check(len(s.owner._ordinary_messages(s.context))==1)
        finally:await s.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault',['stream','generation','session','uncommitted','overflow','crosspart'])
def test_lost_or_inexact_sse_never_becomes_complete_observation_or_replays(fault,tmp_path,clock):
    async def run():
        s=Scene(tmp_path);enable(s)
        try:
            await s.send();uid=user(s)
            if fault=='stream':s.context['stream_error']='OriginalStreamLost'
            elif fault=='generation':s.context['ordinary_message_snapshot']['generation']+=1
            elif fault=='session':s.context['ordinary_message_snapshot']['session_id']='ses_foreign'
            elif fault=='uncommitted':
                with pytest.raises(Conflict):s.owner._ordinary_message_event(s.context,{'type':'message.updated','properties':{'sessionID':s.sdk.native,'info':{'id':'msg_fake','role':'user','sessionID':s.sdk.native}}},None)
                s.context['stream_error']='UncommittedEvent'
            elif fault=='overflow':
                with pytest.raises(Conflict):commit(s,'message.part.updated',part={'id':'prt_huge','messageID':uid,'sessionID':s.sdk.native,'type':'text','text':'x'*(4*1024*1024)})
            else:
                with pytest.raises(Conflict):commit(s,'message.part.updated',part={'id':'prt_cross','messageID':uid,'sessionID':'ses_foreign','type':'text','text':'foreign'})
            await s.inspect();check(s.operation()['status']=='UNKNOWN_OUTCOME'and s.sdk.post_count('/prompt_async')==1)
        finally:await s.close()
    asyncio.run(run())


class Stream(httpx.AsyncByteStream):
    def __init__(self):self.queue=asyncio.Queue()
    async def __aiter__(self):
        while True:yield await self.queue.get()
    async def aclose(self):pass
    async def emit(self,value):await self.queue.put(('data: '+canonical(value)+'\n\n').encode())


def test_real_listener_commits_event_before_snapshot_and_keeps_exact_scope(tmp_path,clock):
    async def run():
        s=Scene(tmp_path);enable(s);s.context['connected'].clear();stream=Stream();listener=None
        base=s.sdk.handle
        async def handle(request):
            if request.url.path=='/event':return httpx.Response(200,stream=stream)
            return await base(request)
        await s.client.aclose();s.client=httpx.AsyncClient(base_url='http://sdk-unit.invalid',transport=httpx.MockTransport(handle),trust_env=False);s.context['client']=s.client
        try:
            listener=asyncio.create_task(s.owner._listen(s.pid,s.sid,s.context))
            await stream.emit({'type':'server.connected','properties':{}});await asyncio.wait_for(s.context['connected'].wait(),2)
            info={'id':'msg_committed','sessionID':s.sdk.native,'role':'user','format':{'type':'json_schema','schema':{'type':'object'},'retryCount':0}}
            await stream.emit({'type':'message.updated','properties':{'sessionID':s.sdk.native,'info':info}})
            await until(lambda:bool(s.context['ordinary_message_snapshot']['infos']))
            snapshot=s.owner._ordinary_messages(s.context);seq=s.context['ordinary_message_snapshot']['last_event_seq']
            event=s.owner.events(s.pid,s.sid,after=seq-1,limit=1)['events'][0]
            check(snapshot[0]['info']==info and event['seq']==seq)
            check(event['payload']['generation']==s.context['generation']and event['payload']['native_payload']['properties']['info']==info)
        finally:
            if listener:listener.cancel();await asyncio.gather(listener,return_exceptions=True)
            await s.close()
    asyncio.run(run())
