"""AUTHORED_NOT_RUN: real private TCP/threads, UNIT callback/process bodies.

No model, native SDK process, App or Windows qualification. Fake resource faults
exercise close orchestration separately from actual bridge lifetime checks.
"""
import asyncio
from concurrent.futures import Future as ThreadFuture
from io import BytesIO
import json
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from apps.api.owner_shutdown import OwnerShutdown
from packages.adapters.opencode_desktop import _ExecutionBroker,OpenCodeDesktopTransport
from packages.adapters.opencode_native_message import NativeMessageBridge
from packages.adapters.opencode_native_review import ReviewBridge
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_experience_candidates import experience


def bridge_for(kind,callback):
    loop=asyncio.get_running_loop()
    if kind=='ordinary':return NativeMessageBridge(callback,loop),'/v1/call',{},{}
    if kind=='review':return ReviewBridge(callback,loop),'/v1/page',{},{}
    request={'budget':{'timeout_seconds':30}};row={'id':'execution','project_id':'project',
        'request_hash':digest(request),'execution_status':'RUNNING','cancel_requested':False}
    context={'loop':loop};owner=SimpleNamespace(active={row['id']:context},inspect=lambda *args:row)
    async def invoke(*args):return await callback('/invoke',args[-1])
    owner._invoke=invoke;owner.method_descriptors=lambda *args:{'methods':[]}
    context['scope']={'methods_sha256':digest(owner.method_descriptors(request))}
    bridge=_ExecutionBroker(owner,row,request,context).start();context['broker']=bridge
    return (bridge,'/invoke',{'X-EEAS-Execution':row['id'],'X-EEAS-Request-SHA256':row['request_hash']},
        {'method':'unit','arguments':{}})


def call(bridge,route,headers,body):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request=urllib.request.Request(bridge.base_url+route,data=json.dumps(body).encode(),
        headers={'Authorization':'Bearer '+bridge.key,**headers})
    try:
        with opener.open(request,timeout=10) as response:return response.status,response.read()
    except urllib.error.HTTPError as error:
        with error:return error.code,error.read()


@pytest.mark.parametrize('kind',['ordinary','review','desktop'])
def test_all_original_bridge_routes_join_actual_inflight_request_and_callback(kind):
    async def run():
        entered=asyncio.Event()
        async def callback(*args):entered.set();await asyncio.Event().wait()
        bridge,route,headers,body=bridge_for(kind,callback)
        pending=asyncio.create_task(asyncio.to_thread(call,bridge,route,headers,body))
        try:
            await asyncio.wait_for(entered.wait(),3)
            threads=bridge.owned_threads;tasks=bridge.owned_tasks
            assert any(isinstance(value,ThreadFuture) for value in tasks)
            assert any(isinstance(value,asyncio.Task) for value in tasks)
            assert all(not thread.daemon for thread in threads)
            await asyncio.to_thread(bridge.close)
            assert (await pending)[0]==409 and bridge.close_confirmed
            assert all(not thread.is_alive() for thread in threads) and all(task.done() for task in tasks)
            assert not any(bridge.resources().values())
        finally:
            if not bridge.close_confirmed:await asyncio.to_thread(bridge.close)
            await asyncio.gather(pending,return_exceptions=True)
    asyncio.run(run())


def test_desktop_exact_headers_and_review_routes_do_not_start_wrong_callback():
    async def run():
        seen=[]
        async def callback(*args):seen.append(args);return {}
        desktop,route,headers,body=bridge_for('desktop',callback)
        review,review_route,_,_=bridge_for('review',callback)
        try:
            assert (await asyncio.to_thread(call,desktop,route,{**headers,'X-EEAS-Execution':'other'},body))[0]==403
            assert (await asyncio.to_thread(call,review,'/invoke',{},{}))[0]==403
            assert seen==[]
            assert (await asyncio.to_thread(call,review,review_route,{},{}))[0]==200
            assert len(seen)==1
        finally:await asyncio.gather(asyncio.to_thread(desktop.close),asyncio.to_thread(review.close))
    asyncio.run(run())


def test_cancelled_running_worker_cannot_be_reported_closed_and_exact_later_join_recovers():
    async def run():
        bridge=NativeMessageBridge(lambda *args:None,asyncio.get_running_loop())
        entered=threading.Event();release=threading.Event()
        def work():entered.set();assert release.wait(10);return 'original result'
        task=asyncio.create_task(bridge.run_sync(work))
        try:
            assert await asyncio.to_thread(entered.wait,2);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            captured=bridge.owned_threads
            with pytest.raises(Conflict):await asyncio.to_thread(bridge.close)
            assert not bridge.close_confirmed and bridge.resources()['held_worker_slots']==1
            assert any(thread.is_alive() for thread in captured)
            release.set();await asyncio.to_thread(bridge.close)
            assert bridge.close_confirmed and all(not thread.is_alive() for thread in captured)
        finally:
            release.set()
            if not bridge.close_confirmed:await asyncio.to_thread(bridge.close)
    asyncio.run(run())


def test_cancelled_queued_workers_never_run_and_keep_queue_slots_until_drained():
    async def run():
        bridge=NativeMessageBridge(lambda *args:None,asyncio.get_running_loop())
        release=threading.Event();both=threading.Event();lock=threading.Lock();entered=[];unexpected=[]
        def block():
            with lock:
                entered.append(True)
                if len(entered)==2:both.set()
            assert release.wait(5)
        running=[asyncio.create_task(bridge.run_sync(block)) for _ in range(2)]
        queued=[]
        try:
            assert await asyncio.to_thread(both.wait,2)
            queued=[asyncio.create_task(bridge.run_sync(lambda:unexpected.append(True))) for _ in range(2)]
            await asyncio.sleep(0)
            assert bridge.resources()['held_worker_slots']==4
            for task in queued:task.cancel()
            await asyncio.gather(*queued,return_exceptions=True)
            assert bridge.resources()['held_worker_slots']==4
            with pytest.raises(Conflict):await bridge.run_sync(lambda:unexpected.append(True))
            release.set();await asyncio.gather(*running)
            await asyncio.to_thread(bridge.close)
            assert unexpected==[] and bridge.close_confirmed and not any(bridge.resources().values())
        finally:
            release.set();await asyncio.gather(*running,*queued,return_exceptions=True)
            if not bridge.close_confirmed:await asyncio.to_thread(bridge.close)
    asyncio.run(run())


def test_shutdown_capture_retains_desktop_review_and_cleanup_handles():
    async def run():
        async def callback(*args):return {}
        desktop,_,_,_=bridge_for('desktop',callback);review,_,_,_=bridge_for('review',callback)
        cleanup=asyncio.create_task(asyncio.Event().wait())
        context={'broker':desktop,'native_review':{'bridge':review},'cleanup_task':cleanup}
        contexts,processes,tasks,threads={},{},{},{}
        try:
            OwnerShutdown._capture([SimpleNamespace(active={'unit':context})],contexts,processes,tasks,threads)
            assert desktop.thread in threads.values() and review.thread in threads.values() and cleanup in tasks.values()
        finally:
            cleanup.cancel();await asyncio.gather(cleanup,return_exceptions=True)
            await asyncio.gather(asyncio.to_thread(desktop.close),asyncio.to_thread(review.close))
    asyncio.run(run())


class Closing:
    """UNIT owned-resource fault; not a native process or relay."""
    def __init__(self,fail=False):self.fail=fail;self.called=0;self.close_confirmed=False
    def close(self):
        self.called+=1
        if self.fail:raise Conflict('UNIT callback still running')
        self.close_confirmed=True


class Client:
    def __init__(self):self.closed=False
    async def aclose(self):self.closed=True


class Process:
    def __init__(self):self.returncode=None;self.terminated=False
    def terminate(self):self.terminated=True;self.returncode=0
    async def wait(self):return self.returncode


def test_desktop_close_fault_still_closes_other_handles_and_keeps_original_context():
    async def run():
        owner=OpenCodeDesktopTransport.__new__(OpenCodeDesktopTransport);broker=Closing(True);relay=Closing()
        client=Client();process=Process();stream=BytesIO();events=[]
        context={'loop':asyncio.get_running_loop(),'broker':broker,'relay':relay,'client':client,'process':process,'logs':[stream]}
        row={'id':'unit-execution'};owner.active={row['id']:context}
        async def flush(context):pass
        owner._flush_events=flush;owner._event=lambda ident,value:events.append(value)
        with pytest.raises(Conflict):await owner._dispose_context('project',row,context)
        assert owner.active[row['id']] is context and not broker.close_confirmed
        assert relay.close_confirmed and client.closed and process.terminated and stream.closed
        assert events[-1]['type']=='desktop_owned_cleanup_unconfirmed'
        broker.fail=False;await owner._dispose_context('project',row,context)
        assert row['id'] not in owner.active and events[-1]['bridge_close_confirmed'] is True
    asyncio.run(run())


def test_native_dispose_fault_still_closes_exact_process_and_relay_but_retains_owner(experience):
    s=experience;owner=s['sessions'];broker=Closing(True);wire=Closing();client=Client();process=Process();stream=BytesIO()
    context={'generation':1,'session_id':'ses_original','directory':str(s['folder']),
        'process':process,'client':client,'native_review':{'bridge':broker},'wire':wire,'logs':[stream]}
    context['owner_binding']={k:context[k] for k in ('generation','session_id','directory','process','client')}
    owner.active[s['sid']]=context;owner._update(s['pid'],s['sid'],original_session_id='ses_original',active_operation_id=None)
    before=owner.operation(s['pid'],s['sid'],s['scope']['root_operation_id'])
    with pytest.raises(Conflict):asyncio.run(owner._dispose(s['pid'],s['sid'],context,status='DISCONNECTED'))
    assert owner.active[s['sid']] is context and process.terminated and wire.close_confirmed and client.closed and stream.closed
    assert owner._state(s['pid'],s['sid'])['owner_status']=='BLOCKED_RESOURCE_STOP_UNCONFIRMED'
    assert owner.operation(s['pid'],s['sid'],s['scope']['root_operation_id'])==before
