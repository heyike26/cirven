"""Real durable authority/Cua-worker seams with SDK/loop protocol counters.

These fixtures qualify neither native Windows input nor the original CLI loop.
The shared adapter, exact grants, frozen source records and cancellation paths
are production code; Root owns actual SDK/GUI/original-loop qualification.
"""
import asyncio
import base64
import copy
from io import BytesIO
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from apps.api.native_tasks import NativeTasks, action_schema, register_native_task_routes
from packages.adapters.cua_desktop_input import CuaDesktopInput, CuaInputError, _NativeSession, _task_manifest
from packages.domain.local_workspaces import canonical, digest, sha
from packages.domain.store import Conflict, Store
from test_native_collaboration import host, BIND, TOKEN, CurrentWindowCounter


REF = {'id': 'protocol-counter', 'revision': 1, 'sha256': 'b' * 64}


class OriginalLoopProtocolCounter:
    """Only protocol scheduling, not an original-loop qualification substitute."""
    def __init__(self):
        self.methods = {key: ({'readonly': True}, None) for key in ('read_message', 'read_context')}
        self.rows, self.calls, self.input_calls = {}, [], []
        self.service = None
        self.intent_wait = None
        self.repeat_unknown = False
        self.route_changed = False
        self.closed = False

    def register_method(self, descriptor, callback):
        self.methods[descriptor['id']] = (copy.deepcopy(descriptor), callback)

    def prepare(self, request):
        ident = 'execution-' + str(len(self.rows))
        row = {'project_id': 'protocol-only', 'id': ident, 'request': canonical(request),
            'request_hash': digest(request), 'execution_status': 'QUEUED', 'result': None}
        self.rows[ident] = row
        self.calls.append(copy.deepcopy(request))
        return copy.deepcopy(row)

    def inspect(self, project, ident):
        row = self.rows[ident]
        if row['project_id'] != project: raise Conflict('counter project mismatch')
        return copy.deepcopy(row)

    async def start(self, project, ident, grant_ref=None):
        row = self.rows[ident]; row['execution_status'] = 'RUNNING'
        request = json.loads(row['request'])
        if request['mode'] == 'INTENT':
            if self.intent_wait: await self.intent_wait.wait()
            text = request['prompt']
            result = {'candidate_json': {'intent': 'ACTION', 'goal': 'fixture append',
                'instruction_span': {'start': 0, 'end': len(text), 'quote': text},
                'allowed_effects': ['TYPE_TEXT'], 'question': None}}
        else:
            execution = self.service._execution(row)
            binding = {'request': request, 'execution_ref': execution, 'grant_ref': grant_ref}
            assert self.service.intents.check_grant(row, request['desktop_scope'], grant_ref)
            observation = await asyncio.to_thread(self.service.observe, {}, binding)
            if self.route_changed:
                self.scene.qualification['qualification_ref'] = REF | {'sha256': 'c' * 64}
            action = {'kind': 'TYPE_TEXT', 'text': '新增中文🙂',
                'delivery_mode': self.scene.qualification['delivery'].lower()}
            arguments = {'operation_id': 'input-once', 'frame_ref': observation['frame']['frame_ref'], 'action': action}
            self.input_calls.append(await asyncio.to_thread(self.service.input, arguments, binding))
            if self.repeat_unknown:
                with pytest.raises(Conflict, match='exact active'):
                    await asyncio.to_thread(self.service.input, arguments | {'operation_id': 'repeat'}, binding)
                await asyncio.to_thread(self.service.observe, {}, binding)
            result = {'candidate_json': {'text': 'SDK returned; independent goal checker required', 'questions': []}}
        row.update(execution_status='RETURNED', result=canonical(result))
        return copy.deepcopy(row)

    async def request_cancel(self, project, ident):
        self.rows[ident]['execution_status'] = 'CANCELLED'
        return self.inspect(project, ident)

    async def close(self): self.closed = True


class SdkSessionCounter:
    def __init__(self, scene): self.scene, self.closed = scene, False
    async def call_tool(self, name, raw):
        assert name == 'get_window_state'
        args = json.loads(raw); window = self.scene.source.window
        assert (args['pid'], args['window_id']) == (window['pid'], window['window_id'])
        return {'is_error': False, 'degraded': False,
            'structured_json': canonical({'pid': window['pid'], 'window_id': window['window_id'],
                'snapshot_id': 's00000001', 'elements_complete': False, 'truncated': False,
                'elements': [{'element_index': 0, 'element_token': 's00000001:0', 'enabled': True,
                    'role': 'Edit', 'value': self.scene.buffer, 'actions': ['set_value']}]}),
            'images': [{'mime_type': 'image/png', 'data_base64': self.scene.png}]}
    async def action(self, name, args):
        self.scene.native_calls.append((name, copy.deepcopy(args)))
        if self.scene.change_buffer and name == 'type_text': self.scene.buffer += args['text']
        return {'is_error': False, 'action': {'effect': self.scene.effect}, 'images': []}
    def close(self): self.closed = True


class SdkFactoryCounter:
    def __init__(self, scene): self.scene, self.sessions = scene, []
    async def open(self, binding):
        if binding.get('runtime_role') == 'TASK_INPUT':
            _task_manifest(binding['task_policy_ref']['path'])
            if self.scene.block_open:
                self.scene.open_started.set()
                while not self.scene.release_open.is_set(): await asyncio.sleep(.002)
        session = SdkSessionCounter(self.scene); self.sessions.append(session)
        return session
    async def shutdown(self): pass


@pytest.fixture
def scene(tmp_path):
    authority, native = host(tmp_path / 'host'); authority.bind(BIND)
    source = CurrentWindowCounter(); authority.set_window_source(source)
    target = authority.context_authority(source.window, authority.binding_reader())['target_ref']
    state = authority.get()
    context = {'context_ref': REF, 'window_ref': target, 'surface_ref': state['surface_ref'],
        'input_epoch': state['input']['epoch'], 'status': 'READY_OBSERVATION', 'questions': []}
    data = SimpleNamespace(authority=authority, native=native, source=source, context=context,
        native_calls=[], buffer='原创 seed', change_buffer=True, effect='UNVERIFIABLE', block_open=False,
        open_started=threading.Event(), release_open=threading.Event())
    stream = BytesIO(); Image.new('RGB', (7, 5), '#e3d2c1').save(stream, 'PNG')
    data.png = base64.b64encode(stream.getvalue()).decode()
    data.qualification = {'qualified': True, 'qualification_ref': REF | {'id': 'protocol-qualification'},
        'target_identity_sha256': source.window['identity_sha256'], 'delivery': 'BACKGROUND',
        'focus_effect': 'NONE', 'effects': ['TYPE_TEXT'], 'owned_queue_drain_qualified': True}
    app = FastAPI(); app.state.settings = SimpleNamespace(data_dir=tmp_path)
    app.state.provider_settings = SimpleNamespace(configuration=lambda *_: None, secret=lambda *_: None)
    app.state.store = Store(tmp_path / 'store.sqlite3')
    data.companion = SimpleNamespace(authority=authority, context_rpc=SimpleNamespace(lookup=lambda _: copy.deepcopy(context)), cua=None)
    data.transport = OriginalLoopProtocolCounter(); data.transport.scene = data
    def policy_writer(*_args):
        policy = {'version': 3, 'expires_after': '5m', 'idle_timeout': '5m', 'allow': {'tools': ['get_window_state', 'type_text']},
            'resources': {'apps': [{'executable': r'D:\fixture\own-notepad.exe', 'windows': 'all'}], 'desktop': {'display': False}}}
        path = tmp_path / 'task-policy.yaml'; path.write_text(canonical(policy), encoding='utf-8')
        return {'path': str(path), 'sha256': sha(path.read_bytes())}
    data.policy_writer = policy_writer
    data.app = app
    data.service = NativeTasks(app, companion=data.companion, transport=data.transport,
        qualification_reader=lambda _: copy.deepcopy(data.qualification),
        authorizer=lambda *_: {'policy_ref': REF, 'allowed_effects': ['TYPE_TEXT'], 'max_actions': 3, 'ttl_seconds': 300},
        policy_writer=policy_writer)
    data.transport.service = data.service
    factory = SdkFactoryCounter(data); data.factory = factory
    cua = CuaDesktopInput(data_dir=tmp_path / 'sdk', session_factory=factory,
        target_authority=data.service.target_authority, observation_guard=authority.observation_guard,
        control_guard=data.service.control_guard, platform='PROTOCOL_COUNTER_NOT_WINDOWS')
    data.companion.cua = cua
    readonly = authority.observation_session_binding(session_label='preserved-readonly',
        budget={'max_actions': 0, 'timeout_ms': 1000, 'max_frame_age_ms': 5000, 'ttl_seconds': 300, 'allow_foreground': False})
    data.readonly = cua.open_session(readonly)['session_ref']
    data.request = {'operation_id': 'current-page-task', 'raw_text': '在当前编辑器追加新增中文🙂',
        'context_ref': REF, 'provider_profile_ref': REF}
    yield data
    data.release_open.set()
    asyncio.run(data.service.close()); cua.shutdown(); authority.close()


async def complete(scene):
    await scene.service.submit(scene.request, TOKEN)
    task = scene.service.tasks.get(scene.request['operation_id'])
    if task: await task
    return scene.service.get(scene.request['operation_id'])


def test_exact_task_reuses_shared_worker_unknown_sdk_never_completes_or_retries(scene):
    scene.transport.repeat_unknown = True
    result = asyncio.run(complete(scene))
    assert result['status'] == 'FINISHED', result
    assert not result['task_completed'] and result['task_outcome']['task_outcome']['verification']['status'] == 'UNKNOWN'
    assert len(scene.native_calls) == 1 and scene.buffer.endswith('新增中文🙂')
    assert scene.transport.input_calls[0]['sdk_receipt']['sdk_effect'] == 'UNVERIFIABLE'
    assert result['answer']['text'].startswith('SDK returned') and not result['answer']['is_goal_verification']
    assert scene.factory.sessions[0].closed is False and scene.factory.sessions[1].closed is True
    assert scene.service.intents.get()['active_grant_ref'] is None
    assert [r['mode'] for r in scene.transport.calls] == ['INTENT', 'ACTION']
    assert scene.authority.get()['input']['enabled'] is False
    assert not scene.service.get()['automatic_input_authorized']


def test_only_fresh_trusted_user_goal_checker_can_complete_task(scene):
    scene.service.intents.result_checker = lambda row, grant: {
        'status': 'VERIFIED' if scene.buffer == '原创 seed新增中文🙂' else 'NOT_VERIFIED',
        'reason': 'Independent protocol fixture buffer, not SDK effect or model assertion', 'evidence_refs': [REF]}
    result = asyncio.run(complete(scene))
    assert result['task_completed']
    assert result['task_outcome']['task_outcome']['proof_scope'] == 'TRUSTED_USER_GOAL_CHECKER_ACTUAL_SOURCE_OBSERVATIONS'


def test_default_unqualified_has_no_model_or_task_session_and_keeps_exact_message(scene):
    scene.service.authority.qualification_reader = None
    result = asyncio.run(complete(scene))
    assert result['status'] == 'INPUT_NOT_QUALIFIED'
    assert scene.transport.calls == [] and len(scene.factory.sessions) == 1
    assert scene.service.intents.message(result['message_ref'])['raw_text'] == scene.request['raw_text']
    assert asyncio.run(scene.service.submit(scene.request, TOKEN))['message_ref'] == result['message_ref']
    with pytest.raises(Conflict): asyncio.run(scene.service.submit(scene.request | {'raw_text': 'other'}, TOKEN))


def test_missing_provider_is_not_configured_no_model_or_implicit_paid_fallback(scene):
    scene.request.pop('provider_profile_ref')
    result = asyncio.run(complete(scene))
    assert result['status'] == 'NOT_CONFIGURED'
    assert scene.transport.calls == [] and len(scene.factory.sessions) == 1


def test_foreground_requires_new_private_permission_and_never_falls_back(scene):
    scene.qualification.update(delivery='FOREGROUND', focus_effect='REQUIRED')
    result = asyncio.run(complete(scene))
    assert result['status'] == 'NEEDS_FOREGROUND_HANDOFF' and not result['task_completed']
    assert scene.native_calls == [] and len(scene.factory.sessions) == 1
    assert scene.service.intents.state['active_grant'] is None


def test_explicit_foreground_grant_freezes_delivery_and_only_task_budget(scene):
    scene.qualification.update(delivery='FOREGROUND', focus_effect='REQUIRED')
    scene.request['foreground_permission_ref'] = REF
    scene.service.foreground_checker = lambda *_: True
    result = asyncio.run(complete(scene))
    assert result['status'] == 'FINISHED', result
    assert scene.native_calls[0][1]['delivery_mode'] == 'foreground'
    assert scene.factory.sessions[0].closed is False


def test_qualification_receipt_changed_after_capture_fences_before_sdk(scene):
    scene.transport.route_changed = True
    result = asyncio.run(complete(scene))
    assert result['status'] == 'UNKNOWN_OUTCOME' and not result['task_completed']
    assert scene.native_calls == []
    assert not scene.service.capability()['desktop_input_routes_qualified']


def test_identity_not_title_or_other_foreground_is_task_target(scene):
    authority = scene.service.authority; authority.context(REF)
    scene.source.window['title'] = 'dirty * changed title'; scene.source.host_foreground = True
    assert authority.check_context(REF, scene.context['window_ref'], scene.context['surface_ref'], scene.context['input_epoch'])
    assert authority.target_authority(scene.context['window_ref'], scene.context['surface_ref'], 1)['pid'] == scene.source.window['pid']
    scene.source.window['identity_sha256'] = 'f' * 64
    assert not authority.check_context(REF, scene.context['window_ref'], scene.context['surface_ref'], 1)


def test_readonly_base_dispatch_remains_refused_new_task_inner_guard_requires_pin(scene):
    with pytest.raises(Conflict):
        with scene.authority.dispatch_guard(scene.context['surface_ref'], 1): pytest.fail('readonly mutation')
    with pytest.raises(Conflict, match='frozen route'):
        with scene.service.authority.dispatch_guard(scene.context['surface_ref'], 1): pytest.fail('unbound mutation')


def test_host_lifecycle_and_old_context_surface_never_rebind_implicitly(scene):
    scene.context['surface_ref'] = REF
    with pytest.raises(Conflict, match='another owned host'): scene.service.authority.context(REF)


def test_takeover_during_readonly_intent_prevents_later_grant_and_input(scene):
    async def exercise():
        scene.transport.intent_wait = asyncio.Event()
        await scene.service.submit(scene.request, TOKEN)
        await asyncio.sleep(0)
        revoked = await scene.service.revoke({'operation_id': 'takeover', 'expected_intent_epoch': 1}, TOKEN)
        assert revoked['readonly_intent_cancellation'][0]['status'] == 'CANCELLED'
        assert revoked['readonly_intent_cancellation_status'] == 'CONFIRMED'
        replay = await scene.service.revoke({'operation_id': 'takeover', 'expected_intent_epoch': 1}, TOKEN)
        assert replay['readonly_intent_cancellation'] == revoked['readonly_intent_cancellation']
        scene.transport.intent_wait.set()
        await scene.service.tasks[scene.request['operation_id']]
    asyncio.run(exercise())
    assert scene.service.get(scene.request['operation_id'])['status'] == 'UNKNOWN_OUTCOME'
    assert scene.native_calls == [] and len(scene.factory.sessions) == 1


def test_service_close_during_intent_cancels_owned_task_preserves_readonly(scene):
    async def exercise():
        scene.transport.intent_wait = asyncio.Event()
        await scene.service.submit(scene.request, TOKEN); await asyncio.sleep(0)
        await scene.service.close()
    asyncio.run(exercise())
    assert scene.transport.closed and all(task.done() for task in scene.service.tasks.values())
    assert not scene.factory.sessions[0].closed and scene.native_calls == []
    assert scene.service.get(scene.request['operation_id'])['status'] == 'UNKNOWN_OUTCOME'


def test_cancel_during_sdk_admission_recovers_exact_task_receipt_and_closes_only_it(scene):
    async def exercise():
        scene.block_open = True
        await scene.service.submit(scene.request, TOKEN)
        assert await asyncio.to_thread(scene.open_started.wait, 3)
        closing = asyncio.create_task(scene.service.close())
        await asyncio.sleep(.03)
        assert not closing.done()
        scene.release_open.set(); await closing
    asyncio.run(exercise())
    assert len(scene.factory.sessions) == 2 and scene.factory.sessions[1].closed
    assert not scene.factory.sessions[0].closed and scene.native_calls == []
    assert scene.service.sessions == {} and scene.service.intents.state['active_grant'] is None


def test_restart_historical_task_is_readonly_no_adoption_or_retry(scene):
    asyncio.run(complete(scene))
    ident = scene.request['operation_id']; value = scene.service._read(ident)
    value['status'] = 'RUNNING'; scene.service._save(ident, value)
    restarted = NativeTasks(scene.app, companion=scene.companion, transport=OriginalLoopProtocolCounter())
    view = restarted.get(ident)
    assert view['record_origin'] == 'HISTORICAL_READONLY' and view['status'] == 'UNKNOWN_OUTCOME'
    assert not view['automatic_retry'] and not restarted.capability()['desktop_input_routes_qualified']
    assert restarted.intents.message(view['message_ref'])['raw_text'] == scene.request['raw_text']
    assert restarted.tasks == {} and not scene.factory.sessions[0].closed
    asyncio.run(restarted.close())


def test_real_transport_source_checker_signature_and_authorization_envelope(scene):
    from packages.adapters.opencode_desktop import OpenCodeDesktopTransport
    asyncio.run(complete(scene))
    action_request = copy.deepcopy(scene.transport.calls[-1])
    real = NativeTasks(scene.app, companion=scene.companion)
    assert isinstance(real.transport, OpenCodeDesktopTransport)
    message = real.intents.record_message({key: scene.request[key] for key in ('operation_id', 'raw_text', 'context_ref')}, host_token=TOKEN)
    request = {'prompt': scene.request['raw_text'], 'mode': 'INTENT',
        'desktop_intent': {'user_message_ref': message['message_ref'], 'context_ref': REF}}
    assert real.transport._source(request)['raw_text'] == scene.request['raw_text']
    real._mutation_method(scene.qualification)
    real.transport._validate(action_request)
    assert real.transport._source(action_request)['raw_text'] == scene.request['raw_text']
    asyncio.run(real.close())


def test_actual_private_routes_register_once_no_renderer_qualification_or_token_leak(scene):
    @scene.app.exception_handler(Conflict)
    async def conflict(_request, error): return JSONResponse({'error': str(error)}, status_code=409)
    scene.service.authority.qualification_reader = None
    assert register_native_task_routes(scene.app, companion=scene.companion, service=scene.service) is scene.service
    count = len(scene.app.routes)
    assert register_native_task_routes(scene.app, companion=scene.companion) is scene.service
    assert len(scene.app.routes) == count
    with TestClient(scene.app) as client:
        root = '/api/desktop-host/native-tasks'
        assert client.get(root + '/get').status_code == 409
        headers = {'X-EEAS-Desktop-Host': TOKEN}
        result = client.post(root + '/submit', json=scene.request, headers=headers)
        assert result.status_code == 200 and result.json()['status'] == 'INPUT_NOT_QUALIFIED'
        assert TOKEN not in result.text and 'marker_name' not in result.text
        assert client.post(root + '/submit', json=scene.request | {'qualified': True}, headers=headers).status_code == 422


def test_public_action_schema_has_only_fixed_element_and_explicit_delivery():
    value = action_schema(['CLICK', 'TYPE_TEXT', 'PRESS_KEY'], 'FOREGROUND')
    assert all(row['additionalProperties'] is False for row in value['oneOf'])
    assert value['oneOf'][0]['required'] == ['kind', 'element_token', 'delivery_mode']
    assert 'x' not in value['oneOf'][0]['properties']


@pytest.mark.parametrize('patch', [
    {'resources': {'desktop': {'display': True}, 'apps': []}},
    {'resources': {'desktop': {'display': 0}, 'apps': [{'executable': '/actual.exe', 'windows': 'all'}]}},
    {'allow': {'tools': ['get_window_state', 'click', 'clipboard_write']}},
    {'expires_after': '11m'}, {'idle_timeout': '9999s'},
    {'resources': {'apps': [{'executable': '*', 'windows': 'all'}], 'desktop': {'display': False}}},
])
def test_task_manifest_rejects_unbounded_or_wrong_resource_tools_and_ttl(scene, patch):
    ref = scene.policy_writer(); path = Path(ref['path'])
    value = json.loads(path.read_bytes()); value.update(patch); path.write_text(canonical(value))
    with pytest.raises(CuaInputError): _task_manifest(path)


def test_real_policy_hash_binding_and_readonly_rejects_task_input(scene):
    qualification = scene.service.authority.qualification(REF)
    scene.service.authority.pin_qualification(REF, qualification)
    ref = scene.policy_writer()
    execution = {'project_id': 'scope', 'execution_id': 'actual', 'request_sha256': 'a' * 64}
    binding = scene.service.authority.binding(REF, execution, policy_ref=ref, max_actions=1, ttl_seconds=300)
    assert binding['task_policy_ref'] == ref and binding['runtime_role'] == 'TASK_INPUT'
    Path(ref['path']).write_text('changed')
    with pytest.raises(CuaInputError): CuaDesktopInput._binding(binding)
    with pytest.raises(CuaInputError, match='READONLY'):
        scene.companion.cua.act(scene.readonly, scene.context['window_ref'], REF,
            {'kind': 'TYPE_TEXT', 'text': 'forbidden'}, lease_ref=REF,
            surface_ref=scene.context['surface_ref'], input_epoch=1, control_epoch=1, operation_id='ro-write')


def test_public_sdk_dispatch_is_fixed_tool_and_exact_fields_no_typed_foreground_guess():
    class Receiver:
        async def call_tool(self, name, raw):
            calls.append((name, json.loads(raw))); return {'is_error': False}
    calls = []; wrapper = _NativeSession(SimpleNamespace(), Receiver())
    target = {'kind': 'window', 'pid': 12, 'window_id': 34}
    asyncio.run(wrapper.action('type_text', {'target': target, 'text': '中文🙂', 'delivery_mode': 'foreground'}))
    asyncio.run(wrapper.action('press_key', {'target': target, 'key': 'END', 'delivery_mode': 'background'}))
    assert calls == [('type_text', {'pid': 12, 'window_id': 34, 'text': '中文🙂', 'delivery_mode': 'foreground'}),
        ('press_key', {'pid': 12, 'window_id': 34, 'key': 'END', 'delivery_mode': 'background'})]
    with pytest.raises(CuaInputError):
        asyncio.run(wrapper.action('type_text', {'target': {'kind': 'desktop'}, 'text': 'x', 'delivery_mode': 'foreground'}))
