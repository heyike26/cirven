"""AUTHORED_NOT_RUN: Store/domain/async broker; Main/IDE boundary explicit UNIT.

No Windows editor, original CLI, live Main feed, model or file-write command.
"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from apps.api.agent_collaboration import register_agent_collaboration_routes
from apps.api.editor_capture_feed import CaptureFeedResponse
from packages.domain.store import Conflict
from test_editor_observers import publish, guard
from test_editor_captures import respond
from test_session_full_diffs import coding
from test_session_diffs import app_for


def count(s):
    with s['value'].store.connect() as db:
        return db.execute('SELECT COUNT(*) FROM agent_editor_capture_cohorts').fetchone()[0]


def test_no_registered_editor_or_fresh_valid_preflight_does_not_open_channel_or_create_duplicate_intent(coding):
    s=coding;broker=s['value'].editor_captures.broker
    async def run():
        assert await broker.admit(s['manifest'],generation=1,operation_id='first',kind='MESSAGE') is None
        publish(s)
        assert await broker.admit(s['manifest'],generation=1,operation_id='second',kind='MESSAGE') is None
    asyncio.run(run());assert count(s)==0


def test_two_window_desktop_admission_reuses_real_cohort_and_only_wakes_on_original_receipts(coding):
    s=coding;publish(s,window='one',dirty=True);publish(s,window='two',operation='register_two',dirty=True)
    broker=s['value'].editor_captures.broker;before=(s['folder']/'a.py').read_bytes()
    async def run():
        lease=broker.reserve();stream=lease.lines();assert json.loads(await anext(stream))['type']=='ready'
        task=asyncio.create_task(broker.admit(s['manifest'],generation=1,operation_id='desktop_original',kind='MESSAGE'))
        frame=json.loads(await asyncio.wait_for(anext(stream),1));rows=frame['rows'];assert len(rows)==2
        assert all(set(r)=={'observer_id','editor_client_id','editor_window_id','project_id','capture_ref','expires_at_ms'} for r in rows)
        assert 'workspace_ref' not in json.dumps(frame) and 'permission_ref' not in json.dumps(frame)
        respond(s,rows[0]['capture_ref'],window='one');respond(s,rows[0]['capture_ref'],window='two',operation='response_two')
        result=await asyncio.wait_for(task,1);assert result['state']=='READY' and result['required_windows']==2
        await stream.aclose();assert broker.feed is None and not broker.waiters
    asyncio.run(run());assert count(s)==1;guard(s);assert (s['folder']/'a.py').read_bytes()==before


def test_generation_zero_owner_start_captures_existing_window_in_its_own_original_session(coding):
    s=coding;publish(s,window='one',dirty=True);owner=s['value'];broker=owner.editor_captures.broker
    target=deepcopy(s['manifest']);target['id']='b'*32
    owner._write(owner._path(s['pid'],target['id'],'manifest.json'),target)
    owner._write(owner._path(s['pid'],target['id'],'state.json'),{'generation':0,'owner_status':'DISCONNECTED'})
    async def run():
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        task=asyncio.create_task(broker.admit(target,generation=0,operation_id=target['id'],kind='OWNER_START'))
        rows=json.loads(await asyncio.wait_for(anext(stream),1))['rows']
        respond(s,rows[0]['capture_ref']);result=await asyncio.wait_for(task,1)
        assert result['state']=='READY';assert not owner.active
        assert owner._state(s['pid'],target['id'])['generation']==0
        await stream.aclose()
    asyncio.run(run())


def test_missing_main_feed_keeps_dirty_guard_without_persisting_a_new_capture(coding):
    s=coding;publish(s,dirty=True);broker=s['value'].editor_captures.broker
    with pytest.raises(Conflict,match='CHANNEL_REQUIRED'):
        asyncio.run(broker.admit(s['manifest'],generation=1,operation_id='original',kind='MESSAGE'))
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):guard(s)
    assert count(s)==0


def test_feed_disconnect_wakes_only_its_own_wait_and_keeps_original_sql_request(coding):
    s=coding;publish(s,window='one',dirty=True);broker=s['value'].editor_captures.broker
    async def run():
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        task=asyncio.create_task(broker.admit(s['manifest'],generation=1,operation_id='original',kind='MESSAGE'))
        await asyncio.wait_for(anext(stream),1);lease.close()
        with pytest.raises(Conflict,match='CHANNEL_REQUIRED'):await asyncio.wait_for(task,1)
        await stream.aclose();assert not broker.waiters and not broker.pending
    asyncio.run(run());assert count(s)==1


def test_capture_dirty_result_fails_without_recreating_an_intent_or_changing_original_files(coding):
    s=coding;publish(s,window='one',dirty=True);broker=s['value'].editor_captures.broker
    async def run():
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        task=asyncio.create_task(broker.admit(s['manifest'],generation=1,operation_id='original',kind='MESSAGE'))
        rows=json.loads(await asyncio.wait_for(anext(stream),1))['rows'];respond(s,rows[0]['capture_ref'],dirty=True)
        with pytest.raises(Conflict,match='CAPTURE_DIRTY'):await asyncio.wait_for(task,1)
        await stream.aclose()
    asyncio.run(run());assert count(s)==1


def test_feed_is_host_authenticated_and_rejects_untyped_query_before_reserving(coding):
    s=coding;app=app_for(s['value']);register_agent_collaboration_routes(app,service=s['value'])
    def check(token):
        if token!='own_main_token':raise HTTPException(403,detail='EXACT_MAIN_REQUIRED')
    app.state.native_collaboration_runtime=SimpleNamespace(authority=SimpleNamespace(check_host=check))
    with TestClient(app) as client:
        assert client.get('/api/desktop-host/editor-capture-events').status_code==403
        assert client.get('/api/desktop-host/editor-capture-events?path=arbitrary',headers={'X-EEAS-Desktop-Host':'own_main_token'}).status_code==422
    assert s['value'].editor_captures.broker.feed is None


def test_asgi_header_failure_releases_reserved_idle_feed_without_waiting_for_any_capture(coding):
    broker=coding['value'].editor_captures.broker
    async def run():
        lease=broker.reserve();response=CaptureFeedResponse(lease)
        async def send(_):raise RuntimeError('UNIT ASGI header failure')
        async def receive():await asyncio.Event().wait()
        with pytest.raises(BaseException):
            await asyncio.wait_for(response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send),1)
        assert lease.closed and broker.feed is None
    asyncio.run(run())


def test_real_send_waits_before_execute_context_and_original_replay_does_not_repeat_capture(coding,monkeypatch):
    from packages.agent_sessions.models import SendMessage
    s=coding;publish(s,window='one',dirty=True);owner=s['value'];broker=owner.editor_captures.broker;calls=[]
    # Native execution is intentionally not entered. The real service claims,
    # waits for the real capture broker and preserves its original failure.
    def execution_boundary(*args,**kwargs):
        calls.append('EXECUTE');raise RuntimeError('UNIT native execution boundary reached')
    monkeypatch.setattr(owner,'_context',execution_boundary)
    body=SendMessage(operation_id='original_desktop_send',session_ref=s['ref'],generation=1,raw_text='KEEP_INPUT')
    async def run():
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        task=asyncio.create_task(owner.send(s['pid'],s['ident'],body))
        frame=json.loads(await asyncio.wait_for(anext(stream),1));assert calls==[]
        respond(s,frame['rows'][0]['capture_ref'])
        with pytest.raises(RuntimeError,match='UNIT native execution'):await asyncio.wait_for(task,1)
        assert calls==['EXECUTE'];assert count(s)==1
        original=owner.operation(s['pid'],s['ident'],body.operation_id);assert original['status']=='FAILED'
        assert await owner.send(s['pid'],s['ident'],body)==original
        assert calls==['EXECUTE'] and count(s)==1
        await stream.aclose()
    asyncio.run(run())
