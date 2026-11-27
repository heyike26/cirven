"""AUTHORED_NOT_RUN: real event SQL/private files/threads with UNIT frames.

No external model, native CLI, browser, Windows or user workspace is exercised.
Unknown local commits are observed/preserved, never repeated.
"""
import asyncio
import base64
import json
import threading
from pathlib import Path

import httpx
import pytest

from packages.adapters.received_event_writer import ReceivedEventWriter,export_received_recovery
from packages.domain.tasks import canonical
from test_execution_resource_output import output_resource,owner,created,events
from test_task_resource_budget import resource


def scene(s):
    value=owner(s)
    record=created({**s,'tmp':s['ws'].data_dir/'writer-workspace'},value)
    ident=record['row']['id']
    workspace=Path(record['row']['workspace']);workspace.mkdir(parents=True)
    context={'workspace':workspace,'event_queue':asyncio.Queue(maxsize=32),
        'done':asyncio.Event(),'connected':asyncio.Event(),'task_resource_budget':s['budget'],
        'seen':{},'secrets':[],'busy':False,'idle_observed':False}
    value.active[ident]=context
    return value,ident,context


async def start(value,ident,context):
    context['writer']=asyncio.create_task(value._write_events(context,ident))
    await asyncio.sleep(0)
    assert context.get('received_writer') is not None


def archive(s,context):
    reference=context['received_writer'].recovery()['archives'][0]
    path=s['ws'].data_dir/reference['path']
    assert path.stat().st_size==reference['bytes'] and path.stat().st_mode&0o777==0o600
    return path,json.loads(path.read_bytes())['value']


def test_normal_original_sql_path_stays_once_and_owned_worker_is_actually_joined(output_resource):
    s=output_resource
    async def run():
        value,ident,context=scene(s);await start(value,ident,context)
        payloads=[{'type':'received_one','text':'原收到内容'}, {'type':'received_two'}]
        for payload in payloads:context['event_queue'].put_nowait(payload)
        await asyncio.wait_for(context['event_queue'].join(),3)
        writer=context['received_writer'];threads=writer.owned_threads
        await value._flush_events(context)
        assert writer.close_confirmed and not writer.archives and not writer.has_pending_data
        assert all(not thread.is_alive() for thread in threads)
        assert [json.loads(raw) for raw in events(s,ident)]==payloads
    asyncio.run(run())


def test_failed_batch_and_already_queued_received_events_are_preserved_without_sql_retry(output_resource,monkeypatch):
    s=output_resource
    async def run():
        value,ident,context=scene(s);entered=threading.Event();release=threading.Event();calls=[]
        def fail(key,payloads):calls.append(payloads);entered.set();assert release.wait(3);raise OSError('UNIT original SQL write failed')
        monkeypatch.setattr(value,'_event_batch',fail)
        await start(value,ident,context)
        first={'type':'first','text':'不能丢弃'};second={'type':'second'}
        context['event_queue'].put_nowait(first)
        assert await asyncio.to_thread(entered.wait,2)
        context['event_queue'].put_nowait(second);release.set()
        await asyncio.wait_for(context['done'].wait(),3)
        await value._flush_events(context)
        path,packet=archive(s,context)
        assert [p for batch in packet['batches'] for p in batch['payloads']]==[first,second]
        assert len(calls)==1 and events(s,ident)==[] and path.exists()
        assert packet['automatic_replay'] is False and context['received_writer'].close_confirmed
        assert s['budget'].inspect()['accounting_unconfirmed']
        with s['store'].connect() as db:row=dict(db.execute('SELECT * FROM agent_executions WHERE id=?',(ident,)).fetchone())
        exported=export_received_recovery(value,row)
        assert exported['packets']==[packet] and exported['unavailable']==[] and len(calls)==1
    asyncio.run(run())


def test_cancelled_awaiter_retains_exact_running_commit_and_close_task(output_resource,monkeypatch):
    s=output_resource
    async def run():
        value,ident,context=scene(s);entered=threading.Event();release=threading.Event();original=value._event_batch;calls=[]
        def blocked(key,payloads):calls.append(True);entered.set();assert release.wait(4);return original(key,payloads)
        monkeypatch.setattr(value,'_event_batch',blocked)
        await start(value,ident,context);context['event_queue'].put_nowait({'type':'actual_original'})
        assert await asyncio.to_thread(entered.wait,2)
        context['writer'].cancel();await asyncio.gather(context['writer'],return_exceptions=True)
        received=context['received_writer']
        assert any(not future.done() for future in received.owned_tasks)
        observer=asyncio.create_task(value._flush_events(context));await asyncio.sleep(0);observer.cancel()
        await asyncio.gather(observer,return_exceptions=True)
        assert not received.close_confirmed and received.close_task is not None
        release.set();await value._flush_events(context)
        assert received.close_confirmed and len(calls)==1 and len(events(s,ident))==1
        assert not received.archives and all(not thread.is_alive() for thread in received.owned_threads)
    asyncio.run(run())


def test_committed_sql_ack_loss_is_archived_as_unknown_without_duplicate_insert_or_debit(output_resource,monkeypatch):
    s=output_resource
    async def run():
        value,ident,context=scene(s);original=value._event_batch;calls=[]
        payload={'type':'already_committed','text':'保原结果'}
        def lost(key,payloads):calls.append(True);original(key,payloads);raise OSError('UNIT commit ACK lost')
        monkeypatch.setattr(value,'_event_batch',lost)
        await start(value,ident,context);context['event_queue'].put_nowait(payload)
        await asyncio.wait_for(context['done'].wait(),3);await value._flush_events(context)
        path,packet=archive(s,context)
        assert len(calls)==len(events(s,ident))==1
        assert packet['batches'][0]['commit_status']=='UNCONFIRMED_NOT_REPLAYABLE'
        assert s['budget'].inspect()['used']['retained_output_bytes']==len(canonical(payload).encode())+path.stat().st_size
        assert s['budget'].inspect()['accounting_unconfirmed']
    asyncio.run(run())


def test_failed_archive_keeps_original_memory_and_retry_saves_same_packet_without_business_replay(output_resource,monkeypatch):
    s=output_resource
    async def run():
        value,ident,context=scene(s);calls=[]
        def fail(key,payloads):calls.append(True);raise OSError('UNIT SQL unavailable')
        monkeypatch.setattr(value,'_event_batch',fail)
        await start(value,ident,context);received=context['received_writer'];original=received._archive
        def archive_failure():received._packet();raise OSError('UNIT archive disk unavailable')
        monkeypatch.setattr(received,'_archive',archive_failure)
        context['event_queue'].put_nowait({'type':'keep_in_memory','text':'唯一已收内容'})
        await asyncio.wait_for(context['done'].wait(),3);await value._flush_events(context)
        packet_id=received.packet_id;raw=received.packet_raw
        assert raw and received.has_pending_data and not received.close_confirmed
        monkeypatch.setattr(received,'_archive',original)
        await value._flush_events(context);path,packet=archive(s,context)
        assert packet['id']==packet_id and path.read_bytes()==raw and len(calls)==1 and received.close_confirmed
    asyncio.run(run())


def test_raw_native_log_failure_preserves_exact_unparsed_frame(output_resource):
    s=output_resource
    async def run():
        value,ident,context=scene(s);(context['workspace']/'native-events.jsonl').mkdir()
        raw=b'{"type":"server.connected","properties":{}}'
        async def transport(request):return httpx.Response(200,content=b'data: '+raw+b'\n\n')
        async with httpx.AsyncClient(base_url='http://unit.invalid',transport=httpx.MockTransport(transport)) as client:
            context['client']=client;await start(value,ident,context)
            await value._listen(context,ident);assert context['pending_native_frame']==raw
            await value._flush_events(context)
        _,packet=archive(s,context)
        assert base64.b64decode(packet['private_native_frame']['base64'])==raw and events(s,ident)==[]
        assert (context['workspace']/'native-events.jsonl').is_dir()
        with s['store'].connect() as db:row=dict(db.execute('SELECT * FROM agent_executions WHERE id=?',(ident,)).fetchone())
        exported=export_received_recovery(value,row)['packets'][0]
        assert 'base64' not in exported['private_native_frame']
        assert exported['private_native_frame']['sha256']==packet['private_native_frame']['sha256']
    asyncio.run(run())


def test_backpressure_cancellation_preserves_the_frame_not_yet_put_in_full_queue(output_resource):
    s=output_resource
    async def run():
        value,ident,context=scene(s);context['event_queue']=asyncio.Queue(maxsize=1)
        context['event_queue'].put_nowait({'type':'already_queued'})
        raw=b'{"type":"server.connected","properties":{}}'
        async def transport(request):return httpx.Response(200,content=b'data: '+raw+b'\n\n')
        async with httpx.AsyncClient(base_url='http://unit.invalid',transport=httpx.MockTransport(transport)) as client:
            context['client']=client;context['received_writer']=ReceivedEventWriter(value,context,ident)
            context['listener']=asyncio.create_task(value._listen(context,ident))
            async def ready():
                while context.get('pending_received_event') is None:await asyncio.sleep(0)
            await asyncio.wait_for(ready(),2);await value._flush_events(context)
        _,packet=archive(s,context);payloads=[p for batch in packet['batches'] for p in batch['payloads']]
        assert payloads[0]['type']=='already_queued' and payloads[1]['native_type']=='server.connected'
        assert base64.b64decode(packet['private_native_frame']['base64'])==raw
        assert context['event_queue'].empty() and context['received_writer'].close_confirmed
    asyncio.run(run())
