"""Source52 real Store/controller boundaries; SDK/OS facets only, no CLI/model qualification."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.request
import threading
import pytest

from apps.api.native_messages import NativeMessages
from packages.adapters import opencode_native_message as adapter
from packages.agent_sessions.native_message import NativeMessageSessions
from packages.agent_sessions.models import OpenSession
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.provider_settings import ProviderSettings
from packages.domain.store import Conflict
from packages.domain.tasks import digest, now
from test_agent_sessions_deadline_recovery import Scene, clock
from test_native_collaboration import HostLifecycleCounter
from test_native_review import FrozenPixels,TOKEN,ROOT,check


async def scene(directory):
    f=Scene(directory)
    authority=NativeCollaborationAuthority(f.data,token=TOKEN,owner_pid=123,native=HostLifecycleCounter(),platform='posix')
    app=SimpleNamespace(state=SimpleNamespace(store=f.store,settings=SimpleNamespace(data_dir=f.data),
        native_capabilities=f.caps,provider_settings=ProviderSettings(f.store,f.data,ROOT)))
    n=NativeMessages(app,authority,FrozenPixels());n.transport.provider_registry=f.registry
    c=NativeMessageSessions(n,f.owner);n.ordinary=c;c.loop=asyncio.get_running_loop()
    body={'operation_id':'ordinary-parent','project_id':f.pid,'conversation_id':'owned-discussion','provider_profile_ref':f.profile_ref,
        'context_ref':n.context.context_ref,'raw_text':'Read this frozen page only.','file_source_ref':None,'file_link_ref':None}
    record,request=n._prepare_ordinary(body,TOKEN)
    descriptors=n.transport.method_descriptors(request)
    a={'schema':'eeas.native-message-admission.v1','parent_operation_id':body['operation_id'],'body':record['request'],
       'body_sha256':record['request_sha256'],'request':request,'request_sha256':digest(request),'provider_profile_ref':f.profile_ref,
       'method_descriptors':descriptors,'method_descriptors_sha256':digest(descriptors),'output_schema':request['output_schema'],
       'source_host_generation':n.generation,'absolute_deadline':(datetime.now(timezone.utc)+timedelta(seconds=180)).isoformat()}
    ref={'id':body['operation_id'],'revision':1,'sha256':digest(a)};c.write('admissions',ref['id'],a)
    record.update(native_message_ref=ref,native_message_phase='RUNNING',native_operation_id='ordinary-send-'+digest(body['operation_id']),
        owner=None,user_turns=0,tool_attempts=0);n.ws._write(n.path(body['operation_id']),record)
    directory=f.owner._path(f.pid,f.sid,'runtime-project');directory.mkdir();_,identity=f.works._root(str(directory))
    m=f.manifest;m.update(native_message_admission=ref,directory_identity=identity,workspace_kind='APP_INTERNAL_READONLY')
    m['request']=OpenSession.model_validate({'operation_id':'seeded-external-owner','provider_profile_ref':f.profile_ref,
        'capability_binding_sha256':f.caps.native_binding_snapshot()['sha256'],'max_calls':8,'max_output_tokens':4096,'ttl_seconds':180}).model_dump(exclude={'operation_id'})
    f.owner._write(f.owner._path(f.pid,f.sid,'manifest.json'),m,replace=True);f.ref=f.owner._manifest(f.pid,f.sid)[1]
    c.owner_created(m,f.ref)
    record=c._record(body['operation_id']);record['owner']['generation']=3;n.ws._write(n.path(body['operation_id']),record)
    f.context.update(directory=str(directory),expires_at=a['absolute_deadline'],native_message_absolute_deadline=a['absolute_deadline']);f.sdk.directory=str(directory)
    async def cb(route,value):return await c._bridge_call(m,f.context,route,value)
    b=adapter.NativeMessageBridge(cb,asyncio.get_running_loop());f.context['native_message']={'bridge':b}
    f.native,f.coordinator,f.body_original,f.admission_ref,f.manifest52=n,c,body,ref,m
    # This fixture replaces the external SDK facets. Production ordinary
    # observations now use committed SSE updates, never a message GET fallback.
    f.context['connected']=asyncio.Event();f.context['connected'].set()
    f.context['ordinary_message_snapshot']={'generation':f.context['generation'],
        'session_id':f.context['session_id'],'infos':{},'parts':{},'last_event_seq':0}
    original_inspect=f.inspect
    async def inspect_with_sdk_events():
        for message in f.sdk.messages:
            mid=message['info']['id']
            events=[{'type':'message.updated','properties':{'sessionID':f.context['session_id'],'info':deepcopy(message['info'])}}]
            for index,part in enumerate(message.get('parts',[])):
                value={**deepcopy(part),'id':part.get('id','prt_'+digest([mid,index])[:24]),
                    'sessionID':f.context['session_id'],'messageID':mid}
                events.append({'type':'message.part.updated','properties':{'sessionID':f.context['session_id'],'part':value}})
            for event in events:
                seq=f.owner._event(f.pid,f.sid,'ORIGINAL_EVENT',{'native_payload':event,'generation':f.context['generation']})
                f.owner._ordinary_message_event(f.context,event,seq)
        return await original_inspect()
    f.inspect=inspect_with_sdk_events
    return f


async def finish(f):
    await f.owner.close();await f.close()


async def initial(f):
    r=f.coordinator._record(f.body_original['operation_id'])
    return await f.owner.send(f.pid,f.sid,f.body(r['native_operation_id'],raw_text=f.body_original['raw_text'],timeout_seconds=180))


def test_actual_initial_two_guidance_latest_cohort_local_status_and_no_get_dispatch(tmp_path,clock):
    async def run():
        f=await scene(tmp_path)
        try:
            sent=await initial(f);c=f.coordinator;n=f.native;parent=f.body_original['operation_id'];deadline=c.deadline(f.manifest52)
            check(f.sdk.post_count('/prompt_async')==1 and c._record(parent)['user_turns']==1)
            original=c._record(parent)['ordinary_cohort'];check(original['native_user_message_id']==sent['native_message_id'])
            await c.submit(f.body_original,TOKEN);check(f.sdk.post_count('/prompt_async')==1)
            status=await c.control(parent,{'operation_id':'status-zero','expected_native_message_ref':f.admission_ref,'raw_text':'现在进度如何？'})
            check(status['original_operation']['role']=='LOCAL_STATUS' and status['original_operation']['model_calls']==0)
            check(f.sdk.post_count('/prompt_async')==1 and c._record(parent)['ordinary_cohort']==original)
            for i in [1,2]:
                result=await c.control(parent,{'operation_id':'guide-'+str(i),'expected_native_message_ref':f.admission_ref,'raw_text':'Keep original goal; extra requirement '+str(i)})
                check(result['status']=='ACCEPTED')
                before=f.sdk.post_count('/prompt_async');again=await c.control(parent,{'operation_id':'guide-'+str(i),'expected_native_message_ref':f.admission_ref,'raw_text':'Keep original goal; extra requirement '+str(i)})
                check(again==result and f.sdk.post_count('/prompt_async')==before)
            latest=c._record(parent)['ordinary_cohort'];check(latest['sequence']==3 and latest['native_user_message_id']!=original['native_user_message_id'])
            f.sdk.terminal(original['native_user_message_id'],error=False,identifier='msg_old_final')
            await f.inspect();await c._owner_changed(f.pid,f.sid)
            check(c._record(parent)['status']=='PROCESSING','Old assistant must not finish latest cohort')
            f.sdk.terminal(latest['native_user_message_id'],error=False,identifier='msg_latest_final')
            f.sdk.messages[-1]['parts']=[{'type':'text','text':json.dumps({'text':'Complete latest answer','questions':[]})}]
            await f.inspect();await c._owner_changed(f.pid,f.sid)
            result=n.get(parent)
            check(result['status']=='RETURNED' and result['text']=='Complete latest answer')
            check(result['native_message_controls'][0]['role']=='LOCAL_STATUS' and result['native_message_controls'][1]['raw_text'].startswith('Keep original'))
            check(c.deadline(f.manifest52)==deadline and f.context['expires_at']==deadline and f.sdk.post_count('/prompt_async')==3)
            for _ in range(3):n.get(parent)
            check(f.sdk.post_count('/prompt_async')==3)
        finally:await finish(f)
    asyncio.run(run())


def test_frozen_ref_cross_owner_generation_unknown_control_never_reposts(tmp_path,clock):
    async def run():
        f=await scene(tmp_path)
        try:
            await initial(f);c=f.coordinator;parent=f.body_original['operation_id']
            with pytest.raises(Conflict):c.prepare_owner(f.manifest52,{**f.context,'generation':4},tmp_path/'forbidden-reopen',{'plugin':[]})
            with pytest.raises(Conflict):await c.control(parent,{'operation_id':'badref','expected_native_message_ref':{**f.admission_ref,'sha256':'0'*64},'raw_text':'New requirement'})
            f.sdk.fail_prompt=True
            body={'operation_id':'unknown-guide','expected_native_message_ref':f.admission_ref,'raw_text':'Keep original; new exact requirement'}
            result=await c.control(parent,body);check(result['status']=='UNKNOWN_OUTCOME')
            posts=f.sdk.post_count('/prompt_async')
            check((await c.control(parent,body))['status']=='UNKNOWN_OUTCOME' and f.sdk.post_count('/prompt_async')==posts)
            with pytest.raises(Conflict):await c.control(parent,{**body,'raw_text':'Different requirement'})
            rejected=await c.control(parent,{**body,'operation_id':'new-after-unknown','raw_text':'A distinct new requirement'})
            check(rejected['status']=='FAILED_NOT_DISPATCHED' and f.sdk.post_count('/prompt_async')==posts)
            f.native.generation='new-host'
            projected=f.native.get(parent);check(projected['record_origin']=='HISTORICAL_READONLY' and not any(projected['control_capabilities'].values()))
            check(f.sdk.post_count('/prompt_async')==posts)
        finally:await finish(f)
    asyncio.run(run())


def add_call(f,index,method='read_context',arguments=None,parent_user=None):
    c=f.coordinator;r=c._record(f.body_original['operation_id']);cohort=r['ordinary_cohort'];mid='msg_tool_'+str(index);call='call_'+str(index)
    args={'method_id':method,'arguments_json':json.dumps(arguments or {})}
    f.sdk.messages.append({'info':{'id':mid,'role':'assistant','sessionID':f.sdk.native,'parentID':parent_user or cohort['native_user_message_id'],'time':{'created':1}},
        'parts':[{'id':'prt_'+str(index),'type':'tool','messageID':mid,'sessionID':f.sdk.native,'callID':call,'tool':adapter.TOOL_NAME,'state':{'status':'running','input':args}}]})
    return {'args':args,'ctx':{'sessionID':f.sdk.native,'messageID':mid,'callID':call},'owner_ref':f.ref,'generation':3}


def test_original_tool_parent_input_status_and_eight_attempts_across_guidance(tmp_path,clock):
    async def run():
        f=await scene(tmp_path)
        try:
            await initial(f);c=f.coordinator;parent=f.body_original['operation_id'];original=c._record(parent)['ordinary_cohort']
            first=add_call(f,1);out=await c._bridge_call(f.manifest52,f.context,'/v1/call',first)
            check('observation'in out['output']);check(await c._bridge_call(f.manifest52,f.context,'/v1/call',first)==out)
            check(c._record(parent)['tool_attempts']==1)
            bad={**first,'args':{**first['args'],'arguments_json':'{"changed":true}'}}
            with pytest.raises(Conflict):await c._bridge_call(f.manifest52,f.context,'/v1/call',bad)
            await c.control(parent,{'operation_id':'fresh-steer','expected_native_message_ref':f.admission_ref,'raw_text':'Keep original; narrower requirement'})
            old=add_call(f,2,parent_user=original['native_user_message_id'])
            with pytest.raises(Conflict):await c._bridge_call(f.manifest52,f.context,'/v1/call',old)
            for j in range(3,10):await c._bridge_call(f.manifest52,f.context,'/v1/call',add_call(f,j))
            check(c._record(parent)['tool_attempts']==8)
            with pytest.raises(Conflict):await c._bridge_call(f.manifest52,f.context,'/v1/call',add_call(f,10))
            check(c._record(parent)['tool_attempts']==8)
            part=f.sdk.messages[-1]['parts'][0];part['state']['status']='completed'
            with pytest.raises(Conflict):await c._bridge_call(f.manifest52,f.context,'/v1/call',add_call(f,11,method='not-admitted'))
        finally:await finish(f)
    asyncio.run(run())


def test_descriptor_tamper_and_absolute_deadline_do_not_renew_on_guidance(tmp_path,clock,monkeypatch):
    async def run():
        f=await scene(tmp_path)
        try:
            await initial(f);c=f.coordinator;parent=f.body_original['operation_id'];first=c._record(parent)['ordinary_cohort'];deadline=c.deadline(f.manifest52)
            descriptor,callback=f.native.transport.methods['read_context'];descriptor['coverage']='tampered'
            with pytest.raises(Conflict):c._current(f.manifest52)
            local=await c.control(parent,{'operation_id':'status-descriptor-drift','expected_native_message_ref':f.admission_ref,'raw_text':'进度'})
            check(local['original_operation']['role']=='LOCAL_STATUS' and f.sdk.post_count('/prompt_async')==1)
            descriptor['coverage']='EXACT_REGISTERED_CONTEXT_OBSERVATIONS_ONLY'
            # Restore the exact frozen descriptor rather than guessing implementation metadata.
            frozen=c.validate_admission(f.admission_ref)['method_descriptors']['methods']
            f.native.transport.methods['read_context']=(next(x for x in frozen if x['id']=='read_context'),callback)
            import packages.agent_sessions.native_message as ordinary_module
            clock.instant=datetime.fromisoformat(deadline)+timedelta(seconds=1)
            monkeypatch.setattr(ordinary_module,'datetime',clock)
            expired=await c.control(parent,{'operation_id':'late','expected_native_message_ref':f.admission_ref,'raw_text':'One new requirement'})
            check(expired['status']=='FAILED' or expired['status']=='FAILED_NOT_DISPATCHED')
            check(f.sdk.post_count('/prompt_async')==1 and c.deadline(f.manifest52)==deadline)
        finally:await finish(f)
    asyncio.run(run())


def test_broker_rejects_wrong_token_tracks_worker_and_joins(tmp_path):
    async def run():
        entered=threading.Event();release=threading.Event()
        async def callback(route,value):return {'ok':True}
        b=adapter.NativeMessageBridge(callback,asyncio.get_running_loop())
        def request():
            req=urllib.request.Request(b.base_url+'/v1/call',data=b'{}',headers={'Authorization':'Bearer wrong','Content-Type':'application/json'})
            try:urllib.request.urlopen(req,timeout=3)
            except urllib.error.HTTPError as e:return e.code
        check(await asyncio.to_thread(request)==403)
        def work():entered.set();release.wait(8);return 'done'
        task=asyncio.create_task(b.run_sync(work));await asyncio.to_thread(entered.wait,2)
        check(b.resources()['callback_futures']==1)
        release.set();check(await task=='done')
        await asyncio.to_thread(b.close)
        check(b.resources()=={'server_thread_alive':False,'request_threads':0,'callback_tasks':0,'callback_futures':0,
                              'broker_futures':0,'callback_threads_alive':0,'held_worker_slots':0,'worker_cleanup_unconfirmed':False})
    asyncio.run(run())


def test_eight_user_turns_shared_with_guidance_but_status_not_a_turn(tmp_path,clock):
    async def run():
        f=await scene(tmp_path)
        try:
            await initial(f);c=f.coordinator;parent=f.body_original['operation_id']
            for j in range(7):
                r=await c.control(parent,{'operation_id':'turn-'+str(j),'expected_native_message_ref':f.admission_ref,'raw_text':'Same task new detail '+str(j)})
                check(r['status']=='ACCEPTED')
            check(c._record(parent)['user_turns']==8 and f.sdk.post_count('/prompt_async')==8)
            status=await c.control(parent,{'operation_id':'eighth-status','expected_native_message_ref':f.admission_ref,'raw_text':'status'})
            check(status['original_operation']['role']=='LOCAL_STATUS' and f.sdk.post_count('/prompt_async')==8)
            ninth=await c.control(parent,{'operation_id':'turn-nine','expected_native_message_ref':f.admission_ref,'raw_text':'New ninth requirement'})
            check(ninth['status']=='FAILED_NOT_DISPATCHED' and c._record(parent)['user_turns']==8 and f.sdk.post_count('/prompt_async')==8)
        finally:await finish(f)
    asyncio.run(run())


def test_broker_does_not_claim_closed_while_sync_callback_runs(tmp_path):
    async def run():
        entered=threading.Event();release=threading.Event()
        b=adapter.NativeMessageBridge(lambda *_:None,asyncio.get_running_loop())
        def work():entered.set();release.wait(12);return 'complete'
        task=asyncio.create_task(b.run_sync(work));await asyncio.to_thread(entered.wait,2)
        try:
            with pytest.raises(Conflict):await asyncio.to_thread(b.close)
            check(b.resources()['callback_futures']==1)
        finally:release.set()
        check(await task=='complete');await asyncio.to_thread(b.close)
        check(not b.resources()['server_thread_alive'] and b.resources()['callback_futures']==0)
    asyncio.run(run())


def test_real_node_plugin_handshake_and_authenticated_broker_call(tmp_path,clock):
    async def run():
        import os,subprocess
        f=await scene(tmp_path)
        process=None
        try:
            await initial(f);call=add_call(f,91)
            prepared=adapter.prepare(ROOT,f.owner._path(f.pid,f.sid,'runtime'),owner_ref=f.ref,generation=3,
                directory=f.context['directory'],bridge=f.context['native_message']['bridge'],
                methods=f.coordinator.validate_admission(f.admission_ref)['method_descriptors']['methods'])
            env=dict(os.environ,EEAS_NATIVE_MESSAGE_CONFIG=prepared['config_path'])
            cmd=[str(ROOT/'.node-tools/node_modules/node/bin/node'),str(ROOT/'tests/test_native_message_plugin52.mjs'),prepared['entry'],f.context['directory']]
            process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            ready=await asyncio.wait_for(asyncio.to_thread(process.stdout.readline),timeout=8)
            check(ready.strip()=='PLUGIN_READY' and adapter.ready(prepared))
            prepared=adapter.bind(prepared,f.sdk.native)
            process.stdin.write(json.dumps({'args':call['args'],'ctx':call['ctx']})+'\n');process.stdin.flush()
            stdout,stderr=await asyncio.wait_for(asyncio.to_thread(process.communicate),timeout=15)
            check(process.returncode==0 and not stderr)
            result=json.loads(stdout);check(result['ok'] and 'observation'in result['result']['output'])
            check(f.coordinator._record(f.body_original['operation_id'])['tool_attempts']==1 and f.sdk.post_count('/prompt_async')==1)
        finally:
            if process and process.poll()is None:process.kill();process.wait(timeout=3)
            await finish(f)
    asyncio.run(run())


def test_broker_cancels_actual_request_callback_and_joins_request_thread(tmp_path):
    async def run():
        started=asyncio.Event()
        async def callback(route,value):
            started.set();await asyncio.Event().wait()
        b=adapter.NativeMessageBridge(callback,asyncio.get_running_loop())
        def request():
            req=urllib.request.Request(b.base_url+'/v1/call',data=b'{}',headers={'Authorization':'Bearer '+b.key,'Content-Type':'application/json'})
            try:urllib.request.urlopen(req,timeout=8)
            except urllib.error.HTTPError as error:return error.code
        pending=asyncio.create_task(asyncio.to_thread(request))
        await asyncio.wait_for(started.wait(),3)
        check(b.resources()['callback_tasks']==1 and b.resources()['request_threads']==1)
        await asyncio.to_thread(b.close)
        check(await pending==409)
        check(not any(b.resources().values()))
    asyncio.run(run())


def test_plugin_manifest_member_tamper_and_symlink_refused(tmp_path):
    import shutil
    target=tmp_path/'source'/adapter.PLUGIN_PATH;target.mkdir(parents=True)
    for name in [*adapter.PLUGIN_FILES,'manifest.json']:
        shutil.copyfile(ROOT/adapter.PLUGIN_PATH/name,target/name)
    (target/'index.mjs').write_text('tampered pinned entry')
    with pytest.raises(Conflict):adapter.verify_bundle(tmp_path/'source')
    (target/'index.mjs').unlink();(target/'index.mjs').symlink_to(ROOT/adapter.PLUGIN_PATH/'index.mjs')
    with pytest.raises(Conflict):adapter.verify_bundle(tmp_path/'source')
