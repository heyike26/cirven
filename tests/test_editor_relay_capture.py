"""AUTHORED_NOT_RUN: real relay counter/Store/broker and worker-thread bridge.

Native owner/config and editor observation are explicit UNIT boundaries. No
ProviderWire server, SDK/CLI, external model, IDE or Windows is run here.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from packages.domain.store import Conflict
from test_session_full_diffs import coding
from test_editor_observers import publish
from test_editor_captures import respond


# The broker has its own eight-second admission deadline. Test observation
# must allow its durable intent and capture receipt to reach a slow volume.
OBSERVATION_TIMEOUT = 10


def owner_context(s,monkeypatch,loop,*,seconds=30):
    owner=s['value'];manifest=deepcopy(s['manifest']);manifest['request']['max_calls']=4
    owner._write(owner._path(s['pid'],s['ident'],'manifest.json'),manifest,replace=True)
    s['manifest']=manifest;s['ref']=owner._manifest(s['pid'],s['ident'])[1]
    monkeypatch.setattr(owner,'_configuration_current',lambda _:True)  # UNIT provider configuration.
    owner._claim(s['pid'],s['ident'],'model_root','MESSAGE',{'UNIT_NATIVE_OWNER_BOUNDARY':True})
    owner._finish(s['pid'],s['ident'],'model_root','ACCEPTED',generation=1,original_session_id='ses_root')
    owner._update(s['pid'],s['ident'],owner_status='READY',active_operation_id='model_root',model_calls_reserved=0)
    context={'project_id':s['pid'],'generation':1,'loop':loop,'closing':False,
        'process':SimpleNamespace(returncode=None),
        'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()}
    owner.active[s['ident']]=context
    return context


def reserved(s):return s['value']._state(s['pid'],s['ident'])['model_calls_reserved']


def test_original_worker_waits_outside_sql_then_reserves_one_existing_model_call_after_clean_capture(coding,monkeypatch):
    s=coding;owner=s['value'];broker=owner.editor_captures.broker
    async def run():
        context=owner_context(s,monkeypatch,asyncio.get_running_loop());publish(s,window='one',dirty=True)
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        task=asyncio.create_task(asyncio.to_thread(owner._reserve_rpc,s['pid'],s['ident'],'f'*64,expected_context=context))
        frame=json.loads(await asyncio.wait_for(anext(stream),OBSERVATION_TIMEOUT));assert reserved(s)==0
        respond(s,frame['rows'][0]['capture_ref']);receipt=await asyncio.wait_for(task,OBSERVATION_TIMEOUT)
        stored=owner._read(owner._path(s['pid'],s['ident'],'rpc',receipt+'.json'))
        assert reserved(s)==1 and stored['request_sha256']=='f'*64 and stored['operation_id']=='model_root'
        with owner.store.connect() as db:
            value=json.loads(db.execute('SELECT payload_json FROM agent_editor_capture_cohorts').fetchone()[0])
            assert value['original_action']=={'kind':'MODEL_RELAY','operation_id':'model_root','request_sha256':'f'*64}
        await stream.aclose()
    asyncio.run(run())


def test_synchronous_call_on_original_event_loop_rejects_stale_capture_instead_of_deadlocking(coding,monkeypatch):
    s=coding;owner=s['value']
    async def run():
        context=owner_context(s,monkeypatch,asyncio.get_running_loop());publish(s,window='one',dirty=True)
        with pytest.raises(Conflict,match='never block'):
            owner._reserve_rpc(s['pid'],s['ident'],'f'*64,expected_context=context)
        assert reserved(s)==0
    asyncio.run(run())


def test_old_wire_cannot_charge_a_replacement_owner_even_at_the_same_generation(coding,monkeypatch):
    s=coding;owner=s['value']
    async def run():
        old=owner_context(s,monkeypatch,asyncio.get_running_loop());owner.active[s['ident']]={**old}
        with pytest.raises(Conflict,match='Original resource accounting owner/work changed'):
            await asyncio.to_thread(owner._reserve_rpc,s['pid'],s['ident'],'f'*64,expected_context=old)
        assert reserved(s)==0
        assert not list(owner._path(s['pid'],s['ident'],'rpc').glob('*.json'))
    asyncio.run(run())


def test_original_operation_ending_wakes_metadata_wait_and_prevents_any_model_reservation(coding,monkeypatch):
    s=coding;owner=s['value'];broker=owner.editor_captures.broker
    async def run():
        context=owner_context(s,monkeypatch,asyncio.get_running_loop());publish(s,window='one',dirty=True)
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        task=asyncio.create_task(asyncio.to_thread(owner._reserve_rpc,s['pid'],s['ident'],'f'*64,expected_context=context))
        await asyncio.wait_for(anext(stream),OBSERVATION_TIMEOUT)
        owner._update(s['pid'],s['ident'],active_operation_id=None)
        with pytest.raises(Conflict,match='TARGET_CHANGED'):await asyncio.wait_for(task,OBSERVATION_TIMEOUT)
        assert reserved(s)==0 and not broker.waiters
        await stream.aclose()
    asyncio.run(run())


def test_relay_authority_deadline_cancels_only_its_metadata_waiter_and_preserves_original_task_and_intent(coding,monkeypatch):
    s=coding;owner=s['value'];broker=owner.editor_captures.broker
    async def run():
        context=owner_context(s,monkeypatch,asyncio.get_running_loop())
        publish(s,window='one',dirty=True)
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        # Exercise expiry during a dispatched capture. Registering the observer
        # performs durable writes before the timed operation starts.
        context['expires_at']=(datetime.now(timezone.utc)+timedelta(seconds=3)).isoformat()
        task=asyncio.create_task(asyncio.to_thread(owner._reserve_rpc,s['pid'],s['ident'],'f'*64,expected_context=context))
        frame=json.loads(await asyncio.wait_for(anext(stream),4))
        assert frame['type']=='editor-capture-batch' and frame['rows']
        with pytest.raises(Conflict,match='deadline reached'):await asyncio.wait_for(task,5)
        assert datetime.now(timezone.utc)>=datetime.fromisoformat(context['expires_at'])
        for _ in range(10):await asyncio.sleep(0)
        assert reserved(s)==0 and owner.active[s['ident']] is context
        assert owner._state(s['pid'],s['ident'])['active_operation_id']=='model_root'
        with owner.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM agent_editor_capture_cohorts').fetchone()[0]==1
        assert not broker.waiters
        await stream.aclose()
    asyncio.run(run())


def test_ended_original_process_cannot_reserve_a_paid_request_from_its_old_wire(coding,monkeypatch):
    s=coding;owner=s['value']
    async def run():
        context=owner_context(s,monkeypatch,asyncio.get_running_loop());context['process'].returncode=0
        with pytest.raises(Conflict,match='process ended'):
            await asyncio.to_thread(owner._reserve_rpc,s['pid'],s['ident'],'f'*64,expected_context=context)
        assert reserved(s)==0
    asyncio.run(run())
