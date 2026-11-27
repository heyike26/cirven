"""Source52 resource-barrier amendment: actual objects, startup failure, no CLI/model."""
import asyncio
from concurrent.futures import Future
import threading
import urllib.request
import urllib.error
import pytest
from packages.adapters import opencode_native_message as adapter
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_native_message_sessions52 import scene,finish,clock,check


def test_actual_worker_thread_future_objects_capture_then_join(tmp_path):
    async def run():
        entered=threading.Event();release=threading.Event();actual=[];closed=[]
        async def callback(*_args):return {}
        b=adapter.NativeMessageBridge(callback,asyncio.get_running_loop(),on_closed=lambda:closed.append(True))
        def work():actual.append(threading.current_thread());entered.set();release.wait(5);return 'done'
        task=asyncio.create_task(b.run_sync(work));await asyncio.to_thread(entered.wait,2)
        threads=b.owned_threads;futures=b.owned_tasks
        check(b.thread in threads and actual[0]in threads and actual[0].is_alive())
        check(any(isinstance(f,Future)and not f.done()for f in futures) and b.resources()['callback_threads_alive']==1)
        release.set();check(await task=='done');await asyncio.to_thread(b.close)
        check(all(not t.is_alive()for t in threads)and all(f.done()for f in futures)and b.close_confirmed and closed==[True])
        await asyncio.to_thread(b.close);check(closed==[True])
    asyncio.run(run())


def test_actual_http_callback_tasks_and_futures_capture_then_cancel_join(tmp_path):
    async def run():
        started=asyncio.Event()
        async def callback(*_args):started.set();await asyncio.Event().wait()
        b=adapter.NativeMessageBridge(callback,asyncio.get_running_loop())
        def call():
            req=urllib.request.Request(b.base_url+'/v1/call',data=b'{}',headers={'Authorization':'Bearer '+b.key})
            try:urllib.request.urlopen(req,timeout=8)
            except urllib.error.HTTPError as error:return error.code
        pending=asyncio.create_task(asyncio.to_thread(call));await asyncio.wait_for(started.wait(),3)
        threads=b.owned_threads;tasks=b.owned_tasks
        check(any(isinstance(t,asyncio.Task)for t in tasks)and any(isinstance(t,Future)for t in tasks))
        await asyncio.to_thread(b.close);check(await pending==409)
        check(all(not t.is_alive()for t in threads)and all(t.done()for t in tasks)and not any(b.resources().values()))
    asyncio.run(run())


def test_prepare_filesystem_failure_and_unknown_close_remain_owned_until_actual_join(tmp_path,clock,monkeypatch):
    async def run():
        f=await scene(tmp_path);c=f.coordinator;original=adapter.NativeMessageBridge.close
        # The seeded SDK fixture is external; isolate the genuine pre-active startup stage.
        active=f.owner.active.pop(f.sid);candidate={k:v for k,v in f.context.items()if k!='native_message'}
        bad=tmp_path/'not-a-directory';bad.write_text('scope parent deliberately cannot be a directory')
        def unknown_close(_bridge):raise Conflict('INJECTED_OS_CLOSE_UNCONFIRMED')
        monkeypatch.setattr(adapter.NativeMessageBridge,'close',unknown_close)
        try:
            with pytest.raises(Conflict,match='INJECTED_OS_CLOSE_UNCONFIRMED'):
                c.prepare_owner(f.manifest52,candidate,bad,{'plugin':[]})
            check(len(c.owned_bridges)==1 and c.resources()['owners'][0]['registered_context_live']is False)
            b=c.owned_bridges[0];threads=c.owned_threads
            check(b.thread in threads and b.thread.is_alive()and not b.close_confirmed)
            monkeypatch.setattr(adapter.NativeMessageBridge,'close',original)
            f.owner.active[f.sid]=active
            await c.close()
            check(not c.owned_bridges and not c.owned_threads and not c.owned_tasks and all(not t.is_alive()for t in threads))
        finally:
            monkeypatch.setattr(adapter.NativeMessageBridge,'close',original)
            if f.sid not in f.owner.active and not active.get('closing'):f.owner.active[f.sid]=active
            for b in c.owned_bridges:await asyncio.to_thread(b.close)
            await finish(f)
    asyncio.run(run())


def test_queued_owner_changed_callback_cannot_start_after_close(tmp_path,clock):
    async def run():
        f=await scene(tmp_path);c=f.coordinator
        try:
            c.owner_changed(f.pid,f.sid)
            await c.close();await asyncio.sleep(.03)
            check(not c.tasks and not c.path('observation-errors',digest([f.pid,f.sid])).exists())
            check(not c.resources()['owners'])
        finally:await finish(f)
    asyncio.run(run())


def test_close_tracks_actual_inflight_startup_task_and_no_late_prompt(tmp_path,clock,monkeypatch):
    async def run():
        import packages.agent_sessions.native_message as ordinary_module
        monkeypatch.setattr(ordinary_module,'datetime',clock)
        f=await scene(tmp_path);c=f.coordinator;started=asyncio.Event()
        async def owned_external_spawn(*_args,**_kwargs):
            started.set();await asyncio.Event().wait()
        monkeypatch.setattr(asyncio,'create_subprocess_exec',owned_external_spawn)
        task=asyncio.create_task(c.submit({**f.body_original,'operation_id':'close-during-owned-startup'},'b'*64))
        try:
            await asyncio.wait_for(started.wait(),20)
            check(task in c.owned_tasks and c.resources()['request_tasks']==1 and c.owned_bridges)
            await c.close()
            check(task.done()and task.cancelled()and not c.owned_tasks and not c.owned_bridges)
            check(f.sdk.post_count('/prompt_async')==0)
            with pytest.raises(Conflict):await c.submit({**f.body_original,'operation_id':'no-late-start'},'b'*64)
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
            for b in c.owned_bridges:await asyncio.to_thread(b.close)
            await finish(f)
    asyncio.run(run())
