"""AUTHORED_NOT_RUN: original records, ledgers and callback threads only.

No App, SDK client, model, tool, socket, native window or user data is used.
Decorator UNIT callbacks stand in for native tool bodies, not native acceptance.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

import pytest

from packages.adapters.opencode_native_message import NativeMessageBridge
from packages.adapters import task_resources as adapter_resources
from packages.agent_sessions.task_resources import native_callback_scope,for_operation
from packages.domain.context_read_budget import context_read_scope,source_bytes,current_context_read_budget
from packages.domain.task_resource_budget import TaskResourceBudget,task_resource_scope,current_task_resource_budget
from packages.domain.store import Conflict
from test_task_resource_budget import resource
from test_experience_candidates import experience
from test_execution_resource_links import adapter,record


@pytest.fixture
def callback_work(experience):
    s=experience;o=s['sessions'];key=s['scope']['root_operation_id']
    o._finish(s['pid'],s['sid'],key,'ACCEPTED',generation=1)
    o._update(s['pid'],s['sid'],generation=1,active_operation_id=key)
    context={'generation':1,'session_id':'ses_original'};o.active[s['sid']]=context
    manifest,ref=o._manifest(s['pid'],s['sid']);path=s['folder']/'source.txt';path.write_bytes(b'original source')
    budget=for_operation(o,manifest,o.operation(s['pid'],s['sid'],key));budget.allocate()
    return {**s,'context':context,'manifest':manifest,'reference':ref,'source':path,'budget':budget}


def test_callback_identity_restores_real_source_budget_then_resets_all_scopes(callback_work):
    s=callback_work;calls=[]
    @native_callback_scope({'/v1/call'})
    async def read(service,manifest,context,route,value):
        calls.append(current_task_resource_budget().id)
        assert current_context_read_budget() is not None
        return await asyncio.to_thread(source_bytes,s['source'],1024)
    result=asyncio.run(read(SimpleNamespace(sessions=s['sessions']),s['manifest'],s['context'],'/v1/call',
        {'owner_ref':s['reference'],'generation':1}))
    assert result==b'original source' and calls==[s['budget'].id]
    assert s['budget'].inspect()['used']['source_bytes']==len(result)
    assert current_task_resource_budget() is None and current_context_read_budget() is None


@pytest.mark.parametrize('damage',['old_context','generation','reference','inactive','route'])
def test_stale_or_unrelated_native_callback_never_runs_source_body(callback_work,damage):
    s=callback_work;calls=[];ctx=s['context'];route='/v1/call';value={'owner_ref':s['reference'],'generation':1}
    if damage=='old_context':ctx={**ctx}
    elif damage=='generation':value['generation']=2
    elif damage=='reference':value['owner_ref']={**s['reference'],'sha256':'f'*64}
    elif damage=='inactive':s['sessions']._update(s['pid'],s['sid'],active_operation_id=None)
    else:route='/other'
    @native_callback_scope({'/v1/call'})
    async def read(*args):calls.append(True)
    before=s['budget'].path.read_bytes()
    with pytest.raises(Conflict):asyncio.run(read(SimpleNamespace(sessions=s['sessions']),s['manifest'],ctx,route,value))
    assert calls==[] and s['budget'].path.read_bytes()==before


def test_identity_lookup_does_not_charge_an_unrelated_ambient_work(callback_work):
    s=callback_work;original=s['budget']
    foreign=TaskResourceBudget(s['sessions'].workspaces,{**original.authority,'root_operation_id':'another_work'})
    foreign.allocate();before=foreign.path.read_bytes();owned_before=original.path.read_bytes()
    @native_callback_scope({'/v1/call'})
    async def forbidden(*args):raise AssertionError('An unrelated ambient work must be rejected')
    with task_resource_scope(foreign),context_read_scope():
        with pytest.raises(Conflict):asyncio.run(forbidden(SimpleNamespace(sessions=s['sessions']),s['manifest'],s['context'],
            '/v1/call',{'owner_ref':s['reference'],'generation':1}))
    assert foreign.path.read_bytes()==before and original.path.read_bytes()==owned_before


def thread_bridge():
    bridge=NativeMessageBridge.__new__(NativeMessageBridge)
    bridge.lock=threading.RLock();bridge.closed=False;bridge.workers=set();bridge.worker_gate=threading.BoundedSemaphore(4)
    bridge.worker_slots=0;bridge.worker_cleanup_unconfirmed=False
    bridge.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='UNIT-original-callback')
    return bridge


def test_reused_original_executor_thread_keeps_each_work_scope_and_leaks_neither(resource):
    s=resource;b=s['budget'];path=s['tmp']/'read.txt';path.write_bytes(b'abc')
    other=TaskResourceBudget(s['ws'],{**s['authority'],'root_operation_id':'second_work'},limits=s['limits']);other.allocate()
    bridge=thread_bridge();threads=[]
    def read():
        threads.append(threading.get_ident());assert current_context_read_budget() is not None
        return current_task_resource_budget().id,source_bytes(path,16)
    async def run():
        for budget in (b,other):
            with task_resource_scope(budget),context_read_scope():
                assert await bridge.run_sync(read)==(budget.id,b'abc')
        assert await bridge.run_sync(lambda:(current_task_resource_budget(),current_context_read_budget()))==(None,None)
    try:asyncio.run(run())
    finally:bridge.closed=True;bridge.executor.shutdown(wait=True)
    assert threads[0]==threads[1] and b.inspect()['used']['source_bytes']==other.inspect()['used']['source_bytes']==3


def test_cancelled_awaiter_retains_worker_original_scope_until_actual_completion(resource):
    s=resource;b=s['budget'];path=s['tmp']/'read.txt';path.write_bytes(b'late')
    bridge=thread_bridge();entered=threading.Event();release=threading.Event();done=threading.Event();seen=[]
    def read():
        entered.set()
        try:
            assert release.wait(3);seen.append(current_task_resource_budget().id);source_bytes(path,16)
        finally:done.set()
    async def run():
        with task_resource_scope(b),context_read_scope():
            task=asyncio.create_task(bridge.run_sync(read))
            assert await asyncio.to_thread(entered.wait,2);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        assert current_task_resource_budget() is None and any(not f.done() for f in bridge.workers)
        release.set();assert await asyncio.to_thread(done.wait,2)
    try:asyncio.run(run())
    finally:release.set();bridge.closed=True;bridge.executor.shutdown(wait=True)
    assert seen==[b.id] and b.inspect()['used']['source_bytes']==4


def test_desktop_callback_uses_persisted_execution_link_in_fresh_task_context(resource):
    s=resource;a=adapter(s);b=s['budget']
    with task_resource_scope(b),s['store'].connect() as db:
        db.execute('BEGIN IMMEDIATE');created=record(s,db);adapter_resources.bind_created_execution(a,db,created['row'])
        db.execute("UPDATE agent_executions SET execution_status='RUNNING' WHERE id=?",(created['row']['id'],))
    def inspect(pid,key):
        with s['store'].connect() as db:return dict(db.execute('SELECT * FROM agent_executions WHERE id=? AND project_id=?',(key,pid)).fetchone())
    row=inspect(s['authority']['project_id'],created['row']['id']);context={'task_resource_budget':b};a.active={row['id']:context};a.inspect=inspect
    path=s['tmp']/'read.txt';path.write_bytes(b'desktop')
    @adapter_resources.callback_scope
    async def read(adapter,row,request,context,body):return await asyncio.to_thread(source_bytes,path,16)
    assert asyncio.run(read(a,row,json.loads(row['request']),context,{}))==b'desktop'
    assert b.inspect()['used']['source_bytes']==7 and current_task_resource_budget() is None
    context['closing']=True
    with pytest.raises(Conflict):asyncio.run(read(a,row,json.loads(row['request']),context,{}))
    context['closing']=False;b.accounting_unconfirmed=True
    before=b.path.read_bytes()
    with pytest.raises(Conflict):asyncio.run(read(a,row,json.loads(row['request']),context,{}))
    assert b.path.read_bytes()==before


def test_saved_image_reader_checks_work_allowance_before_opening_source(resource,monkeypatch):
    from apps.api.desktop_context import DesktopContextRPC
    s=resource;b=s['budget'];path=s['tmp']/'saved.png';path.write_bytes(b'x'*65)
    reference={'id':'saved-image','revision':1,'sha256':'a'*64}
    context=DesktopContextRPC.__new__(DesktopContextRPC)
    context._path=lambda *parts:path
    context.ws=SimpleNamespace(_read=lambda *args,**kwargs:{'image_ref':reference})
    def forbidden(*args):raise AssertionError('No image parsing after source quota rejection')
    monkeypatch.setattr('apps.api.desktop_context.image_metadata',forbidden)
    with task_resource_scope(b),context_read_scope():
        with pytest.raises(Conflict):context.frozen_image(reference)
    assert b.inspect()['used']['source_bytes']==0
