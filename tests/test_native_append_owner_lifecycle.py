"""Real owner/receipt/worker lifecycle seams; protocol fixtures are not Win QA."""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from apps.api.native_append_composition import register_bound_native_append
from apps.api.native_tasks import NativeTasks, register_native_task_routes
from apps.api.native_runtime import NativeCollaborationRuntime, register_native_collaboration_routes
from packages.domain.local_workspaces import digest
from packages.domain.store import Conflict, Store
from test_native_append import scene as append_scene, complete, original_scene
from test_native_collaboration import TOKEN, BIND
from test_native_collaboration_runtime import assembly


def next_context(scene):
    prior = copy.deepcopy(scene.context)
    reference = {'id': 'fresh-second-observation', 'revision': 1,
                 'sha256': digest({'prior': prior, 'actual_text': scene.buffer})}
    observed = {**prior, 'context_ref': reference}
    # The existing bound lookup closes over this registry object. Publish a new
    # observation there, preserving the authority's immutable cached first one.
    scene.context.clear()
    scene.context.update(observed)
    return reference


def test_completed_append_allows_second_fresh_context_without_reusing_old_approval(append_scene):
    scene = append_scene
    original_input = scene.service.input
    def model_input(arguments, binding):
        # The external-model fixture's literal input-once id was designed for
        # one message. Give its second tool request its own immutable key.
        return original_input({**arguments,
            'operation_id': 'model-input-' + digest(binding['request']['operation_id'])[:32]}, binding)
    scene.service.input = model_input
    first = asyncio.run(complete(scene))
    assert first['task_completed'] and first['task_status'] == 'FINISHED'
    assert scene.service.intents.state['active_grant'] is None
    assert scene.service.intents.state['unresolved_execution_refs'] == []
    assert scene.service.intents.state['cancellation']['confirmed']
    assert scene.service.intents.state['cancellation']['queued_inputs']['status'] == 'DRAINED'
    reference = next_context(scene)
    scene.prepare = {**scene.prepare, 'operation_id': 'second-approved-append', 'context_ref': reference}
    prepared = scene.service.prepare_append(scene.prepare, TOKEN)
    assert prepared['state'] == 'PREPARED', prepared
    assert len(scene.native_calls) == 1 and len(scene.transport.calls) == 2
    second = asyncio.run(complete(scene))
    assert second['task_completed'] and second['preview_ref'] != first['preview_ref'], second
    assert scene.buffer == '原创 seed新增中文🙂新增中文🙂'
    assert len(scene.native_calls) == 2 and len(scene.factory.sessions) == 3
    assert not scene.factory.sessions[0].closed and all(session.closed for session in scene.factory.sessions[1:])


def runtime_task_owner(tmp_path):
    app, runtime, _source, factory = assembly(tmp_path)
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    app.state.provider_settings = SimpleNamespace(configuration=lambda *_: None, secret=lambda *_: None)
    app.state.store = Store(tmp_path / 'owner.sqlite3')
    service = NativeTasks(app, companion=runtime)
    register_native_task_routes(app, companion=runtime, service=service)
    runtime.task_service = service
    runtime.bind(BIND)
    return app, runtime, service, factory


def test_actual_close_rpc_awaits_single_task_owner_before_readonly_controller(tmp_path):
    app, runtime, service, factory = runtime_task_owner(tmp_path)
    events = []
    original_task_close, original_runtime_close = service.close, runtime.close
    async def task_close():
        assert not runtime.closed and not factory.session.closed
        events.append('task-close-start')
        result = await original_task_close()
        assert not factory.session.closed
        events.append('task-close-finished')
        return result
    def runtime_close():
        events.append('readonly-controller-close')
        return original_runtime_close()
    service.close, runtime.close = task_close, runtime_close
    with TestClient(app) as client:
        response = client.post('/api/desktop-host/collaboration/close', headers={'X-EEAS-Desktop-Host': TOKEN})
        assert response.status_code == 200 and response.json()['status'] == 'CLOSED'
    assert events == ['task-close-start', 'task-close-finished', 'readonly-controller-close']
    assert service.closed and runtime.closed and factory.session.closed


def test_duplicate_composition_refuses_before_constructing_new_intent_generation(original_scene):
    scene = original_scene
    scene.companion.runtime = None
    scene.app.state.native_tasks = scene.service
    scene.companion.task_service = scene.service
    path = scene.service.intents._path('head.json')
    before = path.read_bytes()
    with pytest.raises(Conflict, match='ONE_ORIGINAL_NATIVE_TASK_SERVICE_REQUIRED'):
        register_bound_native_append(scene.app, companion=scene.companion,
            source_root=Path(__file__).resolve().parents[1], session_cookie_checker=lambda _: True)
    assert path.read_bytes() == before
    assert scene.app.state.native_tasks is scene.service and scene.companion.task_service is scene.service


def test_unknown_queue_drain_retains_pin_after_model_terminal(append_scene):
    scene = append_scene
    scene.service.intents.queue_revoker = lambda _grant: {'status': 'UNKNOWN_OUTCOME', 'discarded_count': None}
    result = asyncio.run(complete(scene))
    assert result['task_status'] == 'FINISHED'
    assert scene.service.intents.state['active_grant'] is None
    assert scene.service.intents.state['cancellation']['confirmed'] is False
    record = scene.service._read(scene.prepare['operation_id'])
    assert record['task_session_close']['status'] == 'CLOSED'
    assert record['qualification_pin_release']['status'] == 'RETAINED'
    assert scene.service.authority.qualification_pin['context_ref'] == scene.prepare['context_ref']
    scene.prepare = {**scene.prepare, 'operation_id': 'after-unknown-drain', 'context_ref': next_context(scene)}
    assert scene.service.prepare_append(scene.prepare, TOKEN)['state'] == 'NOT_ADAPTED'
    assert len(scene.native_calls) == 1
    closed = asyncio.run(scene.service.close())
    assert closed['status'] == 'CLOSE_UNKNOWN' and closed['qualification_pin_retained']


def test_task_session_close_failure_is_durable_unknown_not_forgotten(append_scene):
    scene = append_scene
    def failed_close(_reference): raise OSError('owned SDK close result unavailable')
    scene.companion.cua.close_session = failed_close
    result = asyncio.run(complete(scene))
    assert result['task_status'] == 'FINISHED'
    record = scene.service._read(scene.prepare['operation_id'])
    assert record['task_session_close']['status'] == 'CLOSE_UNKNOWN'
    assert record['qualification_pin_release']['status'] == 'RETAINED'
    assert scene.service.authority.qualification_pin is not None
    assert scene.prepare['operation_id'] in scene.service.sessions
    first = asyncio.run(scene.service.close())
    assert first['status'] == 'CLOSE_UNKNOWN' and first['readonly_controller_closed'] is False
    assert asyncio.run(scene.service.close()) == first
    assert not scene.factory.sessions[0].closed


@pytest.mark.parametrize('changed', ['active_context', 'generation', 'pin_context', 'same_context_pin_digest'])
def test_successful_close_does_not_release_another_context_or_generation_pin(append_scene, changed):
    scene = append_scene
    close = scene.companion.cua.close_session
    original_pin = {}
    def closing(reference):
        result = close(reference)
        original_pin.update(copy.deepcopy(scene.service.authority.qualification_pin))
        other = {**scene.prepare['context_ref'], 'id': 'another-actual-context'}
        if changed == 'active_context': scene.service.active_context = other
        elif changed == 'pin_context': scene.service.authority.qualification_pin['context_ref'] = other
        elif changed == 'same_context_pin_digest': scene.service.authority.qualification_pin['receipt_sha256'] = 'e' * 64
        else: scene.service.intents.generation = 'another-owner-generation'
        return result
    scene.companion.cua.close_session = closing
    asyncio.run(complete(scene))
    record = scene.service._read(scene.prepare['operation_id'])
    assert record['task_session_close']['status'] == 'CLOSED'
    assert record['qualification_pin_release']['status'] == 'RETAINED'
    assert scene.service.authority.qualification_pin is not None
    if changed not in {'pin_context', 'same_context_pin_digest'}:
        assert scene.service.authority.qualification_pin == original_pin


def test_unknown_task_result_retains_pin_even_when_owned_session_closed(original_scene):
    from test_native_tasks import complete as complete_task
    scene = original_scene
    scene.transport.route_changed = True
    result = asyncio.run(complete_task(scene))
    assert result['status'] == 'UNKNOWN_OUTCOME'
    assert result['task_session_close']['status'] == 'CLOSED'
    assert result['qualification_pin_release']['status'] == 'RETAINED'
    assert scene.service.authority.qualification_pin is not None
    assert scene.native_calls == [] and not scene.factory.sessions[0].closed
    before = len(scene.transport.calls)
    with pytest.raises(Conflict, match='qualification fence remains unresolved'):
        asyncio.run(scene.service.submit({**scene.request, 'operation_id': 'after-unknown-task'}, TOKEN))
    assert len(scene.transport.calls) == before and len(scene.factory.sessions) == 2


def test_concurrent_and_repeated_owner_close_share_one_original_transport_cleanup(original_scene):
    scene = original_scene
    original = scene.transport.close
    calls = []
    async def closing():
        calls.append('original-transport-close')
        await asyncio.sleep(0)
        await original()
    scene.transport.close = closing
    async def exercise():
        first, second = await asyncio.gather(scene.service.close(), scene.service.close())
        assert first == second and first['status'] == 'CLOSED'
        first['status'] = 'CALLER_MUTATION'
        assert (await scene.service.close())['status'] == 'CLOSED'
    asyncio.run(exercise())
    assert calls == ['original-transport-close']
    assert not scene.factory.sessions[0].closed


def test_cancelled_close_caller_does_not_abandon_owned_original_cleanup(original_scene):
    scene = original_scene
    original = scene.transport.close
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def closing():
            calls.append('owned-cleanup')
            started.set()
            await release.wait()
            await original()
        scene.transport.close = closing
        caller = asyncio.create_task(scene.service.close())
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError): await caller
        assert scene.service.closed and not scene.transport.closed
        assert not scene.factory.sessions[0].closed
        release.set()
        receipt = await scene.service.close()
        assert receipt['status'] == 'CLOSED' and calls == ['owned-cleanup']
    asyncio.run(exercise())


def test_failed_terminal_receipt_write_cannot_clear_pin_or_claim_owner_closed(append_scene):
    scene = append_scene
    original = scene.service.authority._write
    def writing(path, value):
        if value.get('task_session_close'):
            raise OSError('terminal receipt could not be persisted')
        return original(path, value)
    scene.service.authority._write = writing
    asyncio.run(complete(scene))
    assert scene.service.authority.qualification_pin is not None
    assert len(scene.native_calls) == 1 and scene.factory.sessions[-1].closed
    receipt = asyncio.run(scene.service.close())
    assert receipt['status'] == 'CLOSE_UNKNOWN'
    assert receipt['outcomes']['task_sessions'][scene.prepare['operation_id']]['bookkeeping_error_type'] == 'OSError'
    assert scene.service.authority.qualification_pin is not None


def test_original_transport_close_failure_is_retained_and_never_resigned_closed(original_scene):
    scene = original_scene
    calls = []
    async def failed():
        calls.append('failed-transport-close')
        raise OSError('original owner termination result unavailable')
    scene.transport.close = failed
    first = asyncio.run(scene.service.close())
    assert first['status'] == 'CLOSE_UNKNOWN'
    assert first['outcomes']['original_transport'] == {'status': 'CLOSE_UNKNOWN', 'error_type': 'OSError'}
    assert asyncio.run(scene.service.close()) == first
    assert calls == ['failed-transport-close'] and not scene.factory.sessions[0].closed


def test_http_close_preserves_task_failure_and_still_revokes_readonly_resources(tmp_path):
    app, runtime, service, factory = runtime_task_owner(tmp_path)
    async def failed_transport(): raise OSError('owned original transport cleanup unknown')
    service.transport.close = failed_transport
    with TestClient(app) as client:
        first = client.post('/api/desktop-host/collaboration/close', headers={'X-EEAS-Desktop-Host': TOKEN}).json()
        second = client.post('/api/desktop-host/collaboration/close', headers={'X-EEAS-Desktop-Host': TOKEN}).json()
    assert first == second and first['status'] == 'CLOSE_UNKNOWN'
    assert first['outcomes']['tasks']['status'] == 'CLOSE_UNKNOWN'
    assert first['outcomes']['sdk']['status'] == 'CLOSED'
    assert service.closed and runtime.closed and factory.session.closed


def test_actual_http_close_cancels_inflight_original_intent_before_same_readonly_worker(original_scene):
    import httpx
    scene = original_scene
    runtime = NativeCollaborationRuntime(scene.app.state.settings.data_dir,
        authority=scene.authority, platform='OWNER_LIFECYCLE_PROTOCOL_NOT_WINDOWS')
    runtime.source, runtime.cua = scene.source, scene.companion.cua
    runtime.task_service = scene.service
    scene.service.companion = runtime
    register_native_collaboration_routes(scene.app, scene.app.state.store,
        scene.app.state.settings.data_dir, service=runtime)
    register_native_task_routes(scene.app, companion=runtime, service=scene.service)
    async def exercise():
        scene.transport.intent_wait = asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=scene.app), base_url='http://fixture') as client:
            admitted = await client.post('/api/desktop-host/native-tasks/submit', json=scene.request,
                headers={'X-EEAS-Desktop-Host': TOKEN})
            assert admitted.status_code == 200 and admitted.json()['status'] == 'INTERPRETING'
            await asyncio.sleep(0)
            result = await client.post('/api/desktop-host/collaboration/close', headers={'X-EEAS-Desktop-Host': TOKEN})
            assert result.status_code == 200 and result.json()['status'] == 'CLOSED', result.json()
            assert result.json()['outcomes']['tasks']['outcomes']['revocation']['readonly_intent_cancellation_status'] == 'CONFIRMED'
    asyncio.run(exercise())
    assert scene.transport.calls[0]['mode'] == 'INTENT' and len(scene.transport.calls) == 1
    assert next(iter(scene.transport.rows.values()))['execution_status'] == 'CANCELLED'
    assert scene.factory.sessions[0].closed and scene.native_calls == []
    assert runtime.closed and scene.service.closed and all(task.done() for task in scene.service.tasks.values())


def test_http_task_close_exception_cannot_skip_controller_cleanup_or_claim_closed(tmp_path):
    app, runtime, service, factory = runtime_task_owner(tmp_path)
    original = service.close
    async def failed(): raise OSError('task owner receipt unavailable')
    service.close = failed
    with TestClient(app) as client:
        response = client.post('/api/desktop-host/collaboration/close', headers={'X-EEAS-Desktop-Host': TOKEN}).json()
    assert response['status'] == 'CLOSE_UNKNOWN'
    assert response['outcomes']['tasks'] == {'status': 'CLOSE_UNKNOWN', 'error_type': 'OSError'}
    assert runtime.closed and factory.session.closed
    service.close = original
    asyncio.run(service.close())


@pytest.mark.parametrize('slot', ['app_broker', 'companion_owner'])
def test_partial_duplicate_mount_never_constructs_or_rewrites_receipts(original_scene, slot):
    scene = original_scene
    scene.companion.runtime = None
    if slot == 'app_broker': scene.app.state.native_append_host = object()
    else: scene.companion.task_service = scene.service
    before = scene.service.intents._path('head.json').read_bytes()
    with pytest.raises(Conflict, match='ONE_ORIGINAL_NATIVE_TASK_SERVICE_REQUIRED'):
        register_bound_native_append(scene.app, companion=scene.companion,
            source_root=Path(__file__).resolve().parents[1], session_cookie_checker=lambda _: True)
    assert scene.service.intents._path('head.json').read_bytes() == before
