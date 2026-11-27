"""M0 policy/fault probes plus real private HTTP and durable file receipts.

Native counters below cannot qualify Win32, capture pixels or desktop input.
They deliberately expose only host property operations and metadata reads.
"""
import asyncio
import base64
import copy
from io import BytesIO
import json
import os
from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from apps.api.desktop_context import DesktopContextRPC, register_desktop_context_routes
from packages.adapters.cua_desktop_input import (
    CuaDesktopInput, CuaInputError, _NativeSession, _readonly_manifest,
)
from packages.adapters.desktop_current_context import DesktopCurrentContext
from packages.domain.local_workspaces import digest
from packages.domain.native_collaboration import (
    NativeCollaborationAuthority, NativeCollaborationBindInput, Win32OwnedHostWindows,
    _adapter_digest,
)
from packages.domain.store import Conflict, InvalidInput

TOKEN = 'a' * 64
BUDGET = {'max_actions': 0, 'timeout_ms': 1000, 'max_frame_age_ms': 5000,
          'ttl_seconds': 300, 'allow_foreground': False}
BIND = {'main_window': {'hwnd_decimal': '77', 'owner_pid': 123},
        'owned_windows': {}, 'presentation': 'NATIVE_COMPANION'}
BODY = {'operation_id': 'current-page-one', 'intent': '解释当前原生页面：中文🙂',
        'need': {'metadata': True, 'accessibility': True, 'visual': False, 'ocr': False}}


class HostLifecycleCounter:
    """Fault seam, without any native layout, focus or user-window operation."""
    def __init__(self):
        self.owner = {'pid': 123, 'process_creation': 10101, 'os_session': 1}
        self.markers = {}
        self.mark_calls, self.clear_calls, self.validate_hook = [], [], None

    def process_identity(self, pid):
        if pid != self.owner['pid']:
            raise Conflict('counter owner changed')
        return copy.deepcopy(self.owner)

    def mark_window(self, hwnd, owner):
        if owner != self.owner or hwnd not in {77, 78}:
            raise Conflict('counter refuses unrelated window')
        self.mark_calls.append(hwnd)
        marker = {'window_id': hwnd, **owner, 'window_thread': 91, 'window_class': 'OwnHostCounter',
                  'marker_name': 'EEAS-NativeCollaboration-counter-' + str(len(self.mark_calls)),
                  'marker_value': len(self.mark_calls)}
        self.markers[hwnd] = copy.deepcopy(marker)
        return marker

    def validate(self, marker):
        if self.validate_hook:
            self.validate_hook()
        if self.markers.get(marker['window_id']) != marker or self.owner['process_creation'] != marker['process_creation']:
            raise Conflict('counter HWND was destroyed or reused')
        return copy.deepcopy(marker)

    def clear_marker(self, marker):
        self.validate(marker)
        self.clear_calls.append(marker['window_id'])
        self.markers.pop(marker['window_id'])


class CurrentWindowCounter:
    def __init__(self):
        identity = {'window_id': 901, 'pid': 456, 'process_creation': 20202,
                    'os_session': 1, 'window_class': '原生页面Counter', 'window_thread': 92}
        self.window = {**identity, 'identity_sha256': _adapter_digest(identity),
            'status': 'WINDOW_OBSERVED', 'title': '自建原始夹具', 'visible': True, 'minimized': False,
            'dpi': 144, 'bounds': {'x': 120, 'y': 80, 'width': 7, 'height': 5},
            'source': 'FAULT_PROBE_NOT_NATIVE_CAPTURE', 'page_content_observed': False}
        self.host_foreground, self.closed = False, False

    def sample(self):
        return {'status': 'EXCLUDED_EEAS_WINDOW'} if self.host_foreground else copy.deepcopy(self.window)

    def inspect(self, _hwnd):
        if self.closed:
            raise Conflict('counter target destroyed')
        return copy.deepcopy(self.window)

    def close(self):
        self.closed = True
        return {'status': 'CLOSED'}


class ReadSessionCounter:
    def __init__(self, source):
        self.source, self.calls, self.closed, self.block = source, [], False, None
        self.text = '原创只读正文：中文内容'
        stream = BytesIO()
        Image.new('RGB', (7, 5), '#d8e1e9').save(stream, 'PNG')
        self.png = stream.getvalue()

    async def call_tool(self, name, raw):
        args = json.loads(raw)
        self.calls.append((name, args, threading.get_ident()))
        if self.block:
            await self.block()
        data = {'pid': self.source.window['pid'], 'window_id': self.source.window['window_id'],
                'tree_markdown': self.text, 'elements_complete': True,
                'capture_id': 'counter-frame-not-native-qualification'}
        return {'is_error': False, 'degraded': False, 'structured_json': json.dumps(data),
                'images': [{'mime_type': 'image/png', 'data_base64': base64.b64encode(self.png).decode()}]
                    if args['include_screenshot'] else []}

    async def action(self, *_args):
        raise AssertionError('Readonly authority must never call a native action')

    def close(self):
        self.closed = True


class SessionFactoryCounter:
    def __init__(self, source):
        self.session, self.bindings = ReadSessionCounter(source), []

    async def open(self, binding):
        self.bindings.append(copy.deepcopy(binding))
        return self.session

    async def shutdown(self):
        self.session.close()


def host(tmp_path, *, native=None, token=TOKEN):
    native = native or HostLifecycleCounter()
    authority = NativeCollaborationAuthority(tmp_path, token=token, owner_pid=123,
        native=native, platform='POLICY_FAULT_PROBE')
    return authority, native


@pytest.fixture
def scene(tmp_path):
    authority, native = host(tmp_path)
    authority.bind(BIND)
    source = CurrentWindowCounter()
    authority.set_window_source(source)
    factory = SessionFactoryCounter(source)
    cua = CuaDesktopInput(data_dir=tmp_path / 'original-cua-receipts', session_factory=factory,
        target_authority=authority.target_authority, observation_guard=authority.observation_guard,
        control_guard=authority.dispatch_guard, platform='POLICY_FAULT_PROBE')
    binding = authority.observation_session_binding(session_label='readonly-current-page', budget=BUDGET)
    receipt = cua.open_session(binding)
    authority.set_observation_session(receipt)
    context = DesktopCurrentContext(window_source=source, context_authority=authority.context_authority,
        cua_adapter=cua, binding_reader=authority.binding_reader, observation_guard=authority.observation_guard)
    yield authority, native, source, factory, cua, context, binding, receipt
    context.close()
    cua.shutdown()
    authority.close()


def test_host_lifetime_is_required_before_binding_or_authenticated_read(tmp_path):
    authority, native = host(tmp_path)
    for token in (None, '', 'a' * 63, 'b' * 64):
        with pytest.raises(Conflict):
            authority.check_host(token)
    native.owner['process_creation'] += 1
    with pytest.raises(Conflict, match='lifetime'):
        authority.check_host(TOKEN)
    with pytest.raises(Conflict):
        authority.bind(BIND)
    assert native.mark_calls == []


def test_owned_binding_and_presentation_never_authorize_input_or_reflow(tmp_path):
    authority, native = host(tmp_path)
    active = authority.bind(BIND)
    assert native.mark_calls == [77]
    assert authority.bind(BIND) == active
    mode = authority.set_presentation('PROJECT_WORKBENCH')
    assert mode['input'] == active['input']
    assert mode['surface_ref'] == active['surface_ref']
    assert not mode['input']['enabled']
    assert not mode['native_input_qualified']
    assert not mode['appbar_reserved']
    assert not mode['desktop_geometry_mutated']
    assert mode['verified_agent_dispatch_blocked']
    with pytest.raises(Conflict):
        with authority.dispatch_guard(active['surface_ref'], 1):
            raise AssertionError('No dispatch admitted')


@pytest.mark.parametrize('mutate', [
    lambda b: b['main_window'].update(owner_pid=999),
    lambda b: b.update(owned_windows={'second': {'hwnd_decimal': '77', 'owner_pid': 123}}),
    lambda b: b.update(owned_windows={'second': {'hwnd_decimal': '99', 'owner_pid': 123}}),
    lambda b: b.update(helper_windows={}),
])
def test_arbitrary_renderer_or_foreign_windows_cannot_be_adopted(tmp_path, mutate):
    authority, native = host(tmp_path)
    body = copy.deepcopy(BIND); mutate(body)
    with pytest.raises((ValueError, Conflict)):
        authority.bind(body)
    assert not native.markers


def test_actual_owned_window_destruction_and_hwnd_reuse_invalidate_scope(tmp_path):
    authority, native = host(tmp_path)
    active = authority.bind(BIND)
    native.markers[77]['marker_value'] += 1
    assert authority.get()['status'] == 'HOST_LOST'
    assert not authority.check_current(active['surface_ref'], 1)
    with pytest.raises(Conflict):
        with authority.observation_guard(active['surface_ref'], 1):
            raise AssertionError
    closed = authority.close()
    assert native.clear_calls == []
    assert closed['cleanup'][0]['status'] == 'NOT_REMOVED_LIFETIME_CHANGED_OR_UNKNOWN'
    assert not closed['user_windows_modified']


def test_read_guard_rechecks_lifetime_after_await_or_other_work(tmp_path):
    authority, native = host(tmp_path)
    active = authority.bind(BIND)
    with pytest.raises(Conflict, match='during observation'):
        with authority.observation_guard(active['surface_ref'], 1):
            native.markers.clear()


def test_restart_retains_original_receipts_but_never_adopts_old_authority(tmp_path):
    authority, native = host(tmp_path)
    active = authority.bind(BIND)
    historical = {path: path.read_bytes() for path in authority._path('sessions').glob('*.json')}
    restarted, _ = host(tmp_path, native=native, token='b' * 64)
    assert restarted.get()['status'] == 'NEEDS_REBIND'
    assert not restarted.check_current(active['surface_ref'], 1)
    new = restarted.bind(BIND)
    assert new['session_ref'] != active['session_ref']
    assert new['surface_ref'] != active['surface_ref']
    assert all(path.read_bytes() == raw for path, raw in historical.items())
    for path in historical:
        with pytest.raises(Conflict):
            restarted._read(path, authority=True)


def test_private_receipt_tamper_paths_and_symlinks_fail_closed(tmp_path):
    authority, _ = host(tmp_path)
    path = authority._path('test.json')
    authority._write(path, {'value': '真实记录'})
    assert authority._read(path, authority=True) == {'value': '真实记录'}
    wrapper = json.loads(path.read_bytes()); wrapper['payload']['value'] = '篡改'
    path.write_text(json.dumps(wrapper))
    with pytest.raises(Conflict):
        authority._read(path, authority=True)
    with pytest.raises((ValueError, InvalidInput)):
        authority._write(tmp_path / 'outside.json', {})
    target = tmp_path / 'outside'; target.mkdir()
    authority._path('linked').symlink_to(target, target_is_directory=True)
    with pytest.raises(InvalidInput):
        authority._path('linked', 'test.json')


def test_runtime_has_no_import_or_constructor_driver_and_uses_only_two_win32_libraries(tmp_path):
    class Function:
        def __call__(self, *_args):
            raise AssertionError('Constructing ABI bindings must not call the OS')
    class Library:
        def __init__(self):
            self.functions = {}
        def __getattr__(self, name):
            return self.functions.setdefault(name, Function())
    user, kernel = Library(), Library()
    Win32OwnedHostWindows(libraries={'user32': user, 'kernel32': kernel})
    assert set(user.functions) == {'IsWindow', 'GetAncestor', 'GetWindowThreadProcessId',
        'GetClassNameW', 'SetPropW', 'GetPropW', 'RemovePropW'}
    assert not {'SetWindowPos', 'SetParent', 'SetForegroundWindow', 'SetWindowRgn', 'SHAppBarMessage'} & set(user.functions)
    authority = NativeCollaborationAuthority(tmp_path, token=TOKEN, owner_pid=123, platform='posix')
    assert authority.get()['status'] == 'UNBOUND'
    assert not authority.get()['native_input_qualified']


@pytest.mark.parametrize('change', [
    {'execution_ref': {'project_id': 'p', 'execution_id': 'e', 'request_sha256': 'a' * 64}},
    {'runtime_role': 'unrestricted'}, {'role': 'READONLY_OBSERVATION'},
    {'budget': dict(BUDGET, allow_foreground=True)}, {'budget': dict(BUDGET, max_actions=1)},
    {'budget': dict(BUDGET, max_actions=False)},
])
def test_readonly_binding_cannot_acquire_task_or_foreground_authority(scene, change):
    *_rest, binding, _receipt = scene
    with pytest.raises(ValueError):
        CuaDesktopInput._binding({**binding, **change})


def test_readonly_session_is_bound_to_exact_host_run_not_an_arbitrary_sdk_ref(scene):
    authority, _native, _source, _factory, _cua, _context, _binding, receipt = scene
    fake = copy.deepcopy(receipt)
    fake['observation_run_ref']['sha256'] = 'f' * 64
    with pytest.raises(Conflict):
        authority.set_observation_session(fake)
    assert authority.binding_reader()['sdk_session_ref'] == receipt['session_ref']


def test_readonly_adapter_and_native_wrapper_reject_mutation_before_native_call(scene):
    authority, _native, source, factory, cua, context, _binding, receipt = scene
    observed = context.get_context(intent='解释即可，不要修改', need=BODY['need'])
    frame = cua.capture(receipt['session_ref'], observed['window_ref'], observed['surface_ref'], 1)
    for action in ({'kind': 'TYPE_TEXT', 'text': '禁止写入'},
                   {'kind': 'PRESS_KEY', 'key': 'ENTER'},
                   {'kind': 'CLICK', 'x': 1, 'y': 1, 'delivery_mode': 'background'}):
        with pytest.raises(CuaInputError, match='READONLY_OBSERVATION_CANNOT_MUTATE'):
            cua.act(receipt['session_ref'], observed['window_ref'], frame['frame_ref'], action,
                lease_ref=observed['surface_ref'], surface_ref=observed['surface_ref'],
                input_epoch=1, control_epoch=1, operation_id='readonly-never-act')
    wrapper = _NativeSession(object(), factory.session, readonly=True)
    with pytest.raises(CuaInputError, match='CANNOT_MUTATE'):
        asyncio.run(wrapper.action('type_text', {'text': '不可写'}))
    with pytest.raises(CuaInputError, match='WINDOW_ONLY'):
        asyncio.run(wrapper.call_tool('get_desktop_state', '{}'))
    assert all(name == 'get_window_state' for name, *_args in factory.session.calls)
    source.closed = True
    with pytest.raises(Conflict):
        authority.target_authority(observed['window_ref'], observed['surface_ref'], 1)


@pytest.mark.parametrize('text', [
    'version: 3\nexpires_after: 5m\nidle_timeout: 5m\nallow:\n  tools: [get_window_state, click]\n',
    'version: 3\nexpires_after: 5m\nidle_timeout: 5m\nallow:\n  tools: [get_window_state]\nallow:\n  tools: [click]\n',
])
def test_original_readonly_manifest_rejects_broad_tools_and_duplicate_keys(tmp_path, text):
    policy = tmp_path / 'original-policy.yaml'; policy.write_text(text)
    with pytest.raises(CuaInputError):
        _readonly_manifest(policy)


def test_original_readonly_manifest_validates_frozen_native_tool_ceiling(tmp_path):
    policy = tmp_path / 'original-policy.yaml'
    policy.write_text('version: 3\nexpires_after: 5m\nidle_timeout: 5m\nallow:\n  tools: [get_window_state]\n')
    assert _readonly_manifest(policy) == policy


def test_current_page_real_http_hashes_chinese_reads_fresh_and_preserves_snapshot(scene, tmp_path):
    authority, _native, source, factory, cua, context, _binding, _receipt = scene
    app = FastAPI(); app.state.native_collaboration = authority
    @app.exception_handler(Conflict)
    async def conflict(_request, error):
        return JSONResponse({'error': str(error)}, status_code=409)
    rpc = register_desktop_context_routes(app, None, tmp_path, context=context)
    headers = {'X-EEAS-Desktop-Host': TOKEN}
    with TestClient(app) as client:
        assert client.post('/api/desktop-host/context/observe', json=BODY).status_code == 409
        response = client.post('/api/desktop-host/context/observe', json=BODY, headers=headers)
        assert response.status_code == 201
        data = response.json()
        assert data['current']
        observed = data['observation']
        ref = observed['context_ref']
        original = context.get(ref)
        assert digest({key: value for key, value in original.items() if key != 'context_ref'}) == ref['sha256']
        assert observed['observations']['accessibility']['metadata']['tree_markdown'] == factory.session.text
        count = len(factory.session.calls)
        source.host_foreground = True
        replay = client.post('/api/desktop-host/context/observe', json=BODY, headers=headers).json()
        assert replay['replayed'] and len(factory.session.calls) == count
        factory.session.text = '用户刚修改的新正文'
        newer = client.post('/api/desktop-host/context/observe', json={**BODY, 'operation_id': 'fresh-two'}, headers=headers).json()
        assert newer['observation']['context_ref'] != ref
        assert newer['observation']['observations']['accessibility']['metadata']['tree_markdown'] == factory.session.text
        assert context.get(ref) == original
        assert rpc.status()['runtime_role'] == 'READONLY_OBSERVATION'
        assert not rpc.status()['native_capture_qualified']
        authority.close()
        assert rpc.status()['status'] == 'UNAVAILABLE'
        denied = client.post('/api/desktop-host/context/observe', json={**BODY, 'operation_id': 'closed-three'}, headers=headers)
        assert denied.status_code == 201
        assert denied.json()['observation']['status'] == 'FAILED'
        assert not denied.json()['current']


def test_preview_proves_exact_bytes_and_never_promotes_unknown_pixel_source(scene, tmp_path):
    authority, _native, _source, factory, cua, context, _binding, _receipt = scene
    def reader(context_ref, frame_ref):
        value = context.get(context_ref)
        frame = value['observations']['visual']
        assert frame['frame_ref'] == frame_ref
        raw = (cua.data / frame['image_ref']['path']).read_bytes()
        return {'bytes': raw, 'media_type': 'image/png', 'sha256': frame['image_ref']['sha256'],
                'width': frame['width'], 'height': frame['height']}
    rpc = DesktopContextRPC(authority, context=context, exact_frame_image_reader=reader)
    result = rpc.observe({**BODY, 'need': {**BODY['need'], 'visual': True}})
    observed = result['observation']
    assert observed['status'] == 'NEEDS_CLARIFICATION'
    assert observed['coverage']['pixel_source'] == 'UNKNOWN'
    assert not observed['observations']['visual']['visual_coordinate_input_authorized']
    assert result['preview']['status'] == 'AVAILABLE'
    assert result['preview']['pixel_source'] == 'UNKNOWN'
    assert result['preview']['qualification_scope'] == 'EXACT_IMAGE_BYTES_ONLY_NOT_TARGET_PIXEL_QUALIFICATION'
    reference = {key: result['preview']['image_ref'][key] for key in ('id', 'revision', 'sha256')}
    assert rpc.image(reference) == factory.session.png


def test_rpc_restarted_processing_is_unknown_and_does_not_repeat_read(scene):
    authority, _native, _source, factory, _cua, context, _binding, _receipt = scene
    rpc = DesktopContextRPC(authority, context=context)
    body = __import__('apps.api.desktop_context', fromlist=['DesktopObserveInput']).DesktopObserveInput.model_validate(BODY).model_dump()
    record = {'request': body, 'request_sha256': digest(body), 'generation': 'old-generation', 'state': 'PROCESSING'}
    authority._write(rpc._path('operations', body['operation_id'] + '.json'), record)
    result = rpc.observe(body)
    assert result['replayed'] and result['observation']['status'] == 'UNKNOWN'
    assert not factory.session.calls
    assert authority._read(rpc._path('operations', body['operation_id'] + '.json'), authority=True)['state'] == 'UNKNOWN'


def test_unknown_or_boolean_pixel_source_is_never_a_coordinate_authorization(scene):
    authority, _native, _source, _factory, cua, context, _binding, _receipt = scene
    observed = context.get_context(intent='当前页是什么', need=BODY['need'])
    target = authority.target_authority(observed['window_ref'], observed['surface_ref'], 1)
    assert cua._pixel_proof(True, b'original', target) is None
    assert cua._pixel_proof(None, b'original', target) is None
    with pytest.raises(CuaInputError):
        cua._pixel_proof({'status': 'VERIFIED_TARGET_PIXELS'}, b'original', target)
