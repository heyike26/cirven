"""Durable admission/owned startup; real stores/controller, controlled OS boundary.

No Windows, original CLI, real model, or end-to-end UI qualification is claimed.
"""
import asyncio

import pytest

from packages.domain.store import Conflict
from test_native_message_sessions52 import scene, finish, clock, check


@pytest.fixture(autouse=True)
def admission_clock(clock, monkeypatch):
    import packages.agent_sessions.native_message as ordinary_module
    monkeypatch.setattr(ordinary_module, 'datetime', clock)


def body(f, ident):
    return {**f.body_original, 'operation_id': ident}


def test_ack_is_durable_before_cold_start_and_duplicate_never_starts_twice(tmp_path, clock, monkeypatch):
    async def run():
        f = await scene(tmp_path)
        c = f.coordinator
        entered, release = asyncio.Event(), asyncio.Event()
        opens = []

        async def cold_open(*args, **kwargs):
            opens.append((args, kwargs)); entered.set(); await release.wait()
            raise Conflict('CONTROLLED_COLD_START_FAILURE')

        monkeypatch.setattr(f.owner, 'open', cold_open)
        try:
            request = body(f, 'fast-durable-admission')
            ack = await asyncio.wait_for(c.submit(request, 'b' * 64, deferred=True), 2)
            task = c.startups[request['operation_id']]
            check(ack['status'] == 'PROCESSING' and ack['native_message_phase'] == 'STARTING')
            check(ack['control_capabilities'] == {'can_guide': False, 'can_status': False, 'can_cancel': True})
            check(c.validate_admission(ack['native_message_ref'])['body_sha256'] == ack['request_sha256'])
            check(task in c.owned_tasks and c.resources()['startup_tasks'] == 1)
            await asyncio.wait_for(entered.wait(), 2)
            duplicate = await c.submit(request, 'b' * 64, deferred=True)
            check(duplicate['native_message_ref'] == ack['native_message_ref'] and len(opens) == 1)
            with pytest.raises(Conflict):
                await c.submit({**request, 'raw_text': 'A different input'}, 'b' * 64, deferred=True)
            release.set(); await task
            check(f.native.get(request['operation_id'])['status'] == 'FAILED')
            check(f.sdk.post_count('/prompt_async') == 0)
        finally:
            release.set()
            await c.close(); await finish(f)
    asyncio.run(run())


def test_stop_before_start_is_known_not_dispatched_and_repeated_stop_is_readonly(tmp_path, clock, monkeypatch):
    async def run():
        f = await scene(tmp_path); c = f.coordinator; opens = []

        async def forbidden_open(*args, **kwargs):
            opens.append(args)
            raise AssertionError('Cancelled queued admission must not open an SDK owner')

        monkeypatch.setattr(f.owner, 'open', forbidden_open)
        try:
            request = body(f, 'cancel-before-start')
            await c.submit(request, 'b' * 64, deferred=True)
            task = c.startups[request['operation_id']]
            stopped = await c.cancel(request['operation_id'])
            check(stopped['native_message_phase'] == 'STOP_REQUESTED')
            check(not stopped['control_capabilities']['can_cancel'])
            await task
            final = f.native.get(request['operation_id'])
            check(final['status'] == 'CANCELLED' and not final['execution_started'])
            check(final['cancel_result']['status'] == 'RETURNED')
            check(await c.cancel(request['operation_id']) == final)
            check(not opens and f.sdk.post_count('/prompt_async') == 0)
        finally:
            await c.close(); await finish(f)
    asyncio.run(run())


def test_stop_during_cold_start_returns_promptly_and_never_sends(tmp_path, clock, monkeypatch):
    async def run():
        f = await scene(tmp_path); c = f.coordinator
        entered, release = asyncio.Event(), asyncio.Event(); sends = []

        async def cold_start(pid, sid, **kwargs):
            entered.set(); await release.wait()
            return f.owner.get(pid, sid)

        async def forbidden_send(*args, **kwargs):
            sends.append(args)
            raise AssertionError('Stop requested during startup must prevent prompt dispatch')

        monkeypatch.setattr(f.owner, '_start', cold_start)
        monkeypatch.setattr(f.owner, 'send', forbidden_send)
        try:
            request = body(f, 'cancel-cold-start')
            await c.submit(request, 'b' * 64, deferred=True)
            task = c.startups[request['operation_id']]
            await asyncio.wait_for(entered.wait(), 3)
            ack = await asyncio.wait_for(c.cancel(request['operation_id']), 1)
            check(ack['status'] == 'PROCESSING' and ack['native_message_phase'] == 'STOP_REQUESTED')
            release.set(); await asyncio.gather(task, return_exceptions=True)
            final = f.native.get(request['operation_id'])
            check(final['status'] == 'CANCELLED' and final['owner'] is not None)
            check(not sends and f.sdk.post_count('/prompt_async') == 0)
        finally:
            release.set(); await c.close(); await finish(f)
    asyncio.run(run())


def test_queued_admissions_share_owner_limit_and_are_joined_at_close(tmp_path, clock, monkeypatch):
    async def run():
        f = await scene(tmp_path); c = f.coordinator
        entered = asyncio.Event()

        async def cold_open(*args, **kwargs):
            entered.set(); await asyncio.Event().wait()

        monkeypatch.setattr(f.owner, 'open', cold_open)
        try:
            for i in range(7):
                ack = await c.submit(body(f, 'queued-' + str(i)), 'b' * 64, deferred=True)
                check(ack['status'] == 'PROCESSING')
            refused = await c.submit(body(f, 'over-limit'), 'b' * 64, deferred=True)
            check(refused['status'] == 'FAILED' and 'over-limit' not in c.startups)
            tasks = list(c.startups.values())
            check(c.resources()['startup_tasks'] == 7)
            await asyncio.wait_for(entered.wait(), 2)
            await c.close()
            check(all(t.done() for t in tasks) and not c.owned_tasks and not c.owned_bridges)
            check(all(f.native.get('queued-' + str(i))['status'] == 'CANCELLED' for i in range(7)))
            check(f.sdk.post_count('/prompt_async') == 0)
        finally:
            await c.close(); await finish(f)
    asyncio.run(run())


def test_queued_start_consumes_original_deadline_without_open_or_renewal(tmp_path, clock, monkeypatch):
    async def run():
        f = await scene(tmp_path); c = f.coordinator
        entered, release = asyncio.Event(), asyncio.Event(); opens = []

        async def cold_open(*args, **kwargs):
            opens.append(args); entered.set(); await release.wait()
            raise Conflict('CONTROLLED_COLD_START_FAILURE')

        monkeypatch.setattr(f.owner, 'open', cold_open)
        try:
            for name in ('first-cold', 'expired-in-queue'):
                await c.submit(body(f, name), 'b' * 64, deferred=True)
            tasks = list(c.startups.values())
            admission = c.read('admissions', 'expired-in-queue')
            await asyncio.wait_for(entered.wait(), 2)
            clock.advance(181); release.set(); await asyncio.gather(*tasks)
            final = f.native.get('expired-in-queue')
            check(final['status'] == 'FAILED' and 'expired while waiting' in final['error']['message'])
            check(c.read('admissions', 'expired-in-queue') == admission and len(opens) == 1)
            check(f.sdk.post_count('/prompt_async') == 0)
        finally:
            release.set(); await c.close(); await finish(f)
    asyncio.run(run())


@pytest.mark.parametrize(('fault', 'expected_evidence', 'known_no_process'), [
    ('before_spawn', 'NOT_ATTEMPTED', True),
    ('matched_os_refusal', 'OS_REFUSED', True),
    ('unidentified_exception', 'ATTEMPTED', False),
    ('unrelated_os_refusal', 'ATTEMPTED', False),
])
def test_failed_startup_cleanup_retains_actual_relay_until_normal_close_rejoins(
        tmp_path, clock, monkeypatch, fault, expected_evidence, known_no_process):
    import errno
    from types import SimpleNamespace
    from apps.api.owner_shutdown import OwnerShutdown
    from packages.agent_sessions.service import ProviderWire

    async def run():
        f = await scene(tmp_path)
        c = f.coordinator
        await f.owner.disconnect(f.pid, f.sid)
        original_close = ProviderWire.close
        retained = []
        spawn_calls = []

        async def failed_spawn(executable, *args, **kwargs):
            spawn_calls.append(executable)
            if fault == 'matched_os_refusal':
                raise FileNotFoundError(errno.ENOENT, 'original executable missing', executable)
            if fault == 'unrelated_os_refusal':
                raise FileNotFoundError(errno.ENOENT, 'unrelated path missing', str(tmp_path / 'other'))
            raise OSError('launch outcome unavailable')

        def failed_cleanup(wire):
            retained.append(wire)
            raise Conflict('CONTROLLED_RELAY_CLOSE_UNCONFIRMED')

        if fault == 'before_spawn':
            def failed_config(*args):
                raise ValueError('preparation failed before spawn')
            monkeypatch.setattr(f.owner, '_config', failed_config)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', failed_spawn)
        monkeypatch.setattr(ProviderWire, 'close', failed_cleanup)
        try:
            operation = 'failed-startup-cleanup'
            ack = await c.submit(body(f, operation), 'b' * 64, deferred=True)
            assert ack['status'] == 'PROCESSING'
            await c.startups[operation]
            record = c._record(operation)
            sid = record['owner']['session_ref']['id']
            assert record['status'] == 'FAILED'
            assert len(spawn_calls) == (0 if fault == 'before_spawn' else 1)
            assert retained and retained[0].server.fileno() >= 0
            assert f.owner.active[sid]['startup_cleanup_error'] == 'Conflict'
            assert f.owner.active[sid]['process_start_evidence'] == expected_evidence

            monkeypatch.setattr(ProviderWire, 'close', original_close)
            shutdown = OwnerShutdown(SimpleNamespace(state=SimpleNamespace(agent_sessions=f.owner)), f.data)
            receipt = await shutdown.close()
            owner = next(row for row in receipt['outcomes'] if row['owner_kind'] == 'AGENT_SESSIONS')
            assert receipt['status'] == owner['status'] == ('CLOSED' if known_no_process else 'CLOSE_UNKNOWN')
            assert owner['confirmed_no_process_count'] == int(known_no_process)
            assert owner['unobserved_owner_count'] == int(not known_no_process)
            assert owner['captured_process_count'] == owner['pending_thread_count'] == 0
            assert retained[0].server.fileno() == -1 and retained[0].close_confirmed
            assert sid not in f.owner.active and f.sdk.post_count('/prompt_async') == 0
            await c.close()
            assert not c.owned_bridges and not c.owned_tasks
        finally:
            monkeypatch.setattr(ProviderWire, 'close', original_close)
            for wire in retained:
                await asyncio.to_thread(original_close, wire)
            await c.close()
            await finish(f)
    asyncio.run(run())


def test_actual_http_post_ack_and_original_get_do_not_wait_for_cold_sdk(tmp_path, clock, monkeypatch):
    async def run():
        import httpx
        from fastapi import FastAPI
        from apps.api.native_messages import register_native_message_routes
        from packages.agent_sessions.native_message import NativeMessageSessions
        f = await scene(tmp_path); app = FastAPI()
        app.state.store = f.store; app.state.settings = f.native.app.state.settings
        app.state.provider_settings = f.native.app.state.provider_settings
        app.state.native_capabilities = f.caps
        native = register_native_message_routes(app, authority=f.native.ws, context_rpc=f.native.context)
        native.transport.provider_registry = f.registry
        coordinator = NativeMessageSessions(native, f.owner); native.ordinary = coordinator
        entered, release = asyncio.Event(), asyncio.Event(); opens = []

        async def cold_open(*args, **kwargs):
            opens.append(args); entered.set(); await release.wait()
            raise Conflict('CONTROLLED_COLD_START_FAILURE')

        monkeypatch.setattr(f.owner, 'open', cold_open)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local',
                    headers={'X-EEAS-Desktop-Host': 'b' * 64}) as client:
                request = body(f, 'http-fast-admission')
                response = await asyncio.wait_for(client.post('/api/desktop-host/native-messages', json=request), 2)
                check(response.status_code == 200, response.text)
                ack = response.json(); check(ack['status'] == 'PROCESSING')
                task = coordinator.startups[request['operation_id']]
                await asyncio.wait_for(entered.wait(), 2)
                original = await client.get('/api/desktop-host/native-messages/' + request['operation_id'])
                check(original.status_code == 200 and original.json()['native_message_ref'] == ack['native_message_ref'])
                check(original.json()['request_sha256'] == ack['request_sha256'])
                again = await client.post('/api/desktop-host/native-messages', json=request)
                check(again.status_code == 200 and len(opens) == 1)
                check(f.sdk.post_count('/prompt_async') == 0)
                release.set(); await task
        finally:
            release.set(); await native.close(); await f.coordinator.close(); await finish(f)
    asyncio.run(run())


def test_stop_racing_prompt_ack_is_retained_then_interrupts_the_exact_original_owner(tmp_path, clock, monkeypatch):
    async def run():
        f = await scene(tmp_path); c = f.coordinator
        parent = f.body_original['operation_id']; admission = c.read('admissions', parent)
        f.sdk.hold_prompt = True

        async def already_ready(pid, *args, **kwargs):
            return f.owner.get(pid, f.sid)

        monkeypatch.setattr(f.owner, 'open', already_ready)
        task = asyncio.create_task(c._start_admitted(parent, admission['request'], f.admission_ref))
        c.startups[parent] = task
        try:
            await asyncio.wait_for(f.sdk.prompt_started.wait(), 15)
            stopped = await asyncio.wait_for(c.cancel(parent), 1)
            check(stopped['native_message_phase'] == 'STOP_REQUESTED')
            check(f.sdk.post_count('/abort') == 0, 'Prompt admission is not yet acknowledged')
            f.sdk.prompt_release.set(); await asyncio.wait_for(task, 15)
            check(f.sdk.post_count('/prompt_async') == 1 and f.sdk.post_count('/abort') == 1)
            record = c._record(parent)
            check(record['cancel_result']['status'] == 'RETURNED')
            check(record['cancel_result']['request']['expected_active_operation_id'] == record['native_operation_id'])
            check(record['cancel_result']['generation'] == f.context['generation'])
        finally:
            f.sdk.prompt_release.set()
            if not task.done():task.cancel(); await asyncio.gather(task, return_exceptions=True)
            c.startups.pop(parent, None)
            await c.close(); await finish(f)
    asyncio.run(run())


def test_answer_committed_before_dispatch_returns_keeps_terminal_phase(tmp_path, clock, monkeypatch):
    async def run():
        import json
        f = await scene(tmp_path); c = f.coordinator
        parent = f.body_original['operation_id']; admission = c.read('admissions', parent)
        original_send = f.owner.send

        async def already_ready(pid, *args, **kwargs):
            return f.owner.get(pid, f.sid)

        async def fast_original_answer(*args, **kwargs):
            receipt = await original_send(*args, **kwargs)
            cohort = c._record(parent)['ordinary_cohort']
            f.sdk.terminal(cohort['native_user_message_id'], error=False, identifier='msg_fast_final')
            f.sdk.messages[-1]['parts'] = [{'type': 'text', 'text': json.dumps({'text': 'Original fast answer', 'questions': []})}]
            await f.inspect(); await c._owner_changed(f.pid, f.sid)
            check(c._record(parent)['status'] == 'RETURNED')
            return receipt

        monkeypatch.setattr(f.owner, 'open', already_ready)
        monkeypatch.setattr(f.owner, 'send', fast_original_answer)
        task = asyncio.create_task(c._start_admitted(parent, admission['request'], f.admission_ref))
        c.startups[parent] = task
        try:
            await asyncio.wait_for(task, 20)
            final = f.native.get(parent)
            check(final['status'] == 'RETURNED' and final['native_message_phase'] == 'DONE')
            check(final['text'] == 'Original fast answer' and f.sdk.post_count('/prompt_async') == 1)
        finally:
            if not task.done():task.cancel(); await asyncio.gather(task, return_exceptions=True)
            c.startups.pop(parent, None)
            await c.close(); await finish(f)
    asyncio.run(run())


def test_native_stop_during_actual_service_startup_joins_bridge_and_relay_without_model(tmp_path, clock, monkeypatch):
    async def run():
        f = await scene(tmp_path); c = f.coordinator; entered = asyncio.Event()

        async def waiting_os_spawn(*args, **kwargs):
            entered.set(); await asyncio.Event().wait()

        monkeypatch.setattr(asyncio, 'create_subprocess_exec', waiting_os_spawn)
        try:
            request = body(f, 'stop-actual-startup')
            await c.submit(request, 'b' * 64, deferred=True)
            task = c.startups[request['operation_id']]
            await asyncio.wait_for(entered.wait(), 20)
            owner = c._record(request['operation_id'])['owner']
            new_bridges = [b for key, _, b in c._bridge_entries() if key[1] == owner['session_ref']['id']]
            check(len(new_bridges) == 1)
            threads = new_bridges[0].owned_threads
            stopped = await asyncio.wait_for(c.cancel(request['operation_id']), 1)
            check(stopped['native_message_phase'] == 'STOP_REQUESTED')
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 15)
            final = f.native.get(request['operation_id'])
            check(final['status'] == 'CANCELLED' and final['cancel_result']['prompt_not_dispatched'])
            check(new_bridges[0].close_confirmed and all(not t.is_alive() for t in threads))
            check(owner['session_ref']['id'] not in f.owner.active and f.sdk.post_count('/prompt_async') == 0)
            check(all(key[1] != owner['session_ref']['id'] for key, _, _ in c._bridge_entries()))
        finally:
            await c.close(); await finish(f)
    asyncio.run(run())


@pytest.mark.parametrize('new_owner_active', [True, False])
def test_old_cleanup_finishes_only_its_own_handles_after_generation_changes(tmp_path, new_owner_active):
    from threading import Event
    from test_agent_sessions_deadline_recovery import Scene

    async def run():
        f = Scene(tmp_path)
        entered, release = Event(), Event()
        calls = []

        class Bridge:
            def close(self):
                calls.append('old bridge')
                if len(calls) == 1:
                    entered.set()
                    assert release.wait(8)
                    raise Conflict('old bridge has not joined')

        f.context['native_message'] = {'bridge': Bridge()}
        stale = asyncio.create_task(f.owner._dispose(f.pid, f.sid, f.context, status='DISCONNECTED'))
        try:
            assert await asyncio.to_thread(entered.wait, 8)
            # Represent a later native owner at the external process boundary.
            # The old close is already running and holds only its captured handles.
            successor = {'generation': 4}
            if new_owner_active:
                f.owner.active[f.sid] = successor
            else:
                f.owner.active.pop(f.sid)
            f.owner._update(f.pid, f.sid, generation=4, original_session_id='ses_later',
                            owner_status='READY' if new_owner_active else 'DISCONNECTED')
            expected = f.state()
            release.set()
            with pytest.raises(Conflict, match='old bridge has not joined'):
                await stale
            assert f.owner.active.get(f.sid) is (successor if new_owner_active else None)
            assert f.state() == expected and len(f.owner._detached_cleanup) == 1
            if new_owner_active:
                await f.owner._dispose(f.pid, f.sid, f.context, status='DISCONNECTED')
                assert f.owner.active[f.sid] is successor
            else:
                # Normal application shutdown retries an older retained handle even
                # when the newer session no longer has a local process to disconnect.
                await f.owner.close()
            assert not f.owner._detached_cleanup and f.state() == expected
            assert calls == ['old bridge','old bridge'] and f.context['process'].terminated == 1
        finally:
            release.set()
            await asyncio.gather(stale, return_exceptions=True)
            await f.close()

    asyncio.run(run())
