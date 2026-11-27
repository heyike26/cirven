"""Append closure/admission/real shared-worker protocol checks, not Windows QA."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.native_append import (NotepadAppendTasks, register_native_append_routes,
    AppendPrepareInput, AppendApproveInput)
from apps.api.native_tasks import register_native_task_routes
from packages.adapters.native_append import (NotepadQualificationReader, REQUIRED_SOURCES, SCOPE,
    ROUTE_CHECKS, HANDOFF_CHECKS, text_sha, utf16_length, Win32ClassicNotepadInspector)
from packages.domain.local_workspaces import canonical, digest
from packages.domain.store import Conflict
from test_native_tasks import scene as original_scene, OriginalLoopProtocolCounter, REF
from test_native_collaboration import TOKEN


class InspectorCounter:
    def __init__(self, scene):
        self.scene = scene
        self.reads = 0
        self.foreground = scene.source.window['window_id']
        self.caret_delta = 0
        self.modifiers_released = True
        self.edit_id = 902
        self.before_read = None

    def read(self, window):
        self.reads += 1
        if self.before_read: self.before_read(self.reads)
        text = self.scene.buffer
        length = utf16_length(text)
        return {**copy.deepcopy(self.scene.source.window), 'window_class': 'Notepad',
            'edit_window_id': self.edit_id, 'edit_class': 'Edit', 'text': text,
            'text_sha256': text_sha(text), 'utf16_length': length,
            'selection_start': length + self.caret_delta, 'selection_end': length + self.caret_delta,
            'focused_edit': True, 'gui_idle': True, 'foreground_window_id': self.foreground,
            'modifiers_released': self.modifiers_released,
            'executable_path': r'C:\Windows\System32\notepad.exe', 'executable_sha256': 'd' * 64,
            'os': {'major': 10, 'minor': 0, 'build': 19045, 'ubr': 100, 'architecture': 'AMD64'},
            'same_user': True, 'same_session': True, 'same_integrity': True,
            'source': 'PROTOCOL_COUNTER_NOT_WINDOWS'}


def qualification_fixture(root, snapshot):
    source, runtime, evidence = [root / name for name in ('source', 'runtime', 'evidence')]
    for folder in (source, runtime, evidence): folder.mkdir(parents=True)
    def write(folder, name, raw):
        path = folder / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        return {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}
    sources = [write(source, name, ('protocol-source-' + name).encode()) for name in sorted(REQUIRED_SOURCES)]
    installed = write(runtime, 'runtime/site-packages/cua_driver/_native.py', b'protocol-sdk-source')
    component = {'schema_version': 'eeas.cua-shell-component.v1', 'wheels': [
        {'name': 'cua-driver', 'version': '0.35.2', 'engine_files': {'cua_driver/_native.py': installed['sha256']}}]}
    dependencies = {'type': 'NATIVE_APPEND_SDK_DEPENDENCY_CLOSURE', 'version': 1, 'files': [installed]}
    files = [installed | {'role': 'sdk_engine'},
        write(runtime, 'component.json', canonical(component).encode()) | {'role': 'sdk_component_manifest'},
        write(runtime, 'sdk-dependencies.json', canonical(dependencies).encode()) | {'role': 'sdk_dependency_manifest'}]
    support = {'os': snapshot['os'], 'executable_sha256': snapshot['executable_sha256']}
    refs = {}
    for name, kind, checks in [('route', 'NATIVE_APPEND_ROUTE_EVIDENCE', ROUTE_CHECKS),
                               ('handoff', 'NATIVE_APPEND_HANDOFF_EVIDENCE', HANDOFF_CHECKS)]:
        receipt = {'type': kind, 'status': 'ACTUAL_WINDOWS_INDEPENDENTLY_VERIFIED', 'scope': SCOPE,
            'support': support, 'checks': {key: True for key in checks},
            'sources_sha256': digest(sources), 'runtime_sha256': digest(files),
            'test_scope': 'PROTOCOL_FIXTURE_NOT_ACTUAL_QUALIFICATION'}
        refs[name] = write(evidence, name + '.json', canonical(receipt).encode())
    pack = {'type': 'CLASSIC_NOTEPAD_APPEND_QUALIFICATION', 'version': 1, 'scope': SCOPE,
        'os': snapshot['os'], 'executable': {'path': snapshot['executable_path'],
            'sha256': snapshot['executable_sha256'], 'window_class': 'Notepad', 'edit_class': 'Edit'},
        'limits': {'max_payload_utf16_units': 100, 'max_buffer_utf16_units': 65536},
        'sdk_site_packages': 'runtime/site-packages',
        'source_files': sources, 'runtime_files': files,
        'route_evidence': refs['route'], 'handoff_evidence': refs['handoff']}
    ref = write(runtime, 'toolchain/append-qualification.json', canonical(pack).encode())
    policy_ref = {'path': str(runtime / ref['path']), 'sha256': ref['sha256']}
    reader = NotepadQualificationReader(source_root=source, runtime_root=runtime,
        evidence_root=evidence, policy_ref=policy_ref)
    return SimpleNamespace(reader=reader, source=source, runtime=runtime, evidence=evidence, pack=pack, policy_ref=policy_ref)


@pytest.fixture
def scene(original_scene, tmp_path):
    data = original_scene
    asyncio.run(data.service.close())
    data.inspector = InspectorCounter(data)
    data.closure = qualification_fixture(tmp_path / 'closure', data.inspector.read(data.source.window))
    data.factory.root = data.closure.runtime
    data.factory.manifest_ref = {'path': str(data.closure.runtime / 'component.json'),
        'sha256': next(value['sha256'] for value in data.closure.pack['runtime_files'] if value['role'] == 'sdk_component_manifest')}
    data.transport = OriginalLoopProtocolCounter(); data.transport.scene = data
    data.gestures = {}
    def activate(ticket):
        data.inspector.foreground = ticket['target']['window_id']
        return {'status': 'ACTIVATED', 'operation_id': ticket['operation_id'],
            'approval_ref': ticket['approval_ref'], 'target_identity_sha256': ticket['target']['identity_sha256'],
            'foreground_window_id': ticket['target']['window_id'],
            'source': 'ACTUAL_MAIN_USER_GESTURE_WIN32_HANDOFF', 'evidence_ref': REF}
    data.activate = activate
    data.service = NotepadAppendTasks(data.app, companion=data.companion, inspector=data.inspector,
        qualification_reader=data.closure.reader, runtime_root=data.closure.runtime,
        gesture_reader=lambda ref: copy.deepcopy(data.gestures[ref['id']]), trusted_activator=activate,
        transport=data.transport)
    data.transport.service = data.service
    data.companion.cua.target_authority = data.service.target_authority
    data.companion.cua.control_guard = data.service.control_guard
    data.qualification.update(delivery='FOREGROUND', focus_effect='REQUIRED')
    raw = '在当前记事本末尾追加新增中文🙂'
    data.prepare = {'operation_id': 'approved-append', 'context_ref': REF, 'raw_text': raw,
        'provider_profile_ref': REF, 'payload_source': {'kind': 'RAW_INSTRUCTION',
            'span': {'start': raw.index('新增'), 'end': len(raw), 'quote': '新增中文🙂'}}}
    return data


def prepare_approve(scene):
    result = scene.service.prepare_append(scene.prepare, TOKEN)
    preview = scene.service._append_read('previews', result['preview_ref'])
    gesture = {'type': 'MAIN_NATIVE_APPEND_USER_GESTURE', 'operation_id': scene.prepare['operation_id'],
        'preview_ref': result['preview_ref'], 'host_scope': preview['host_scope'], 'surface_ref': preview['surface_ref'],
        'input_epoch': preview['input_epoch'], 'intent_epoch': preview['intent_epoch'],
        'user_activation': True, 'expires_unix_ms': int(scene.service.clock() * 1000) + 59000}
    ref = {'id': 'actual-main-counter-gesture', 'revision': 1, 'sha256': digest(gesture)}
    scene.gestures[ref['id']] = gesture
    approve = {'operation_id': scene.prepare['operation_id'], 'preview_ref': result['preview_ref'],
        'expected_input_epoch': preview['input_epoch'], 'expected_intent_epoch': preview['intent_epoch'],
        'main_gesture_ref': ref}
    scene.approve = approve
    return scene.service.approve_append(approve, TOKEN)


async def complete(scene):
    approved = prepare_approve(scene)
    result = await scene.service.start_append({k: approved[k] for k in ('preview_ref', 'approval_ref')}
        | {'operation_id': scene.prepare['operation_id']}, TOKEN)
    task = scene.service.tasks.get(scene.prepare['operation_id'])
    if task: await task
    return scene.service.append_get(scene.prepare['operation_id'])


def test_approved_const_original_task_shared_worker_unknown_sdk_independent_goal(scene):
    result = asyncio.run(complete(scene))
    assert result['task_status'] == 'FINISHED', result
    assert result['task_completed'], result
    assert len(scene.native_calls) == 1 and scene.buffer == '原创 seed新增中文🙂'
    assert scene.transport.input_calls[0]['sdk_receipt']['sdk_effect'] == 'UNVERIFIABLE'
    assert scene.factory.sessions[0].closed is False and scene.factory.sessions[-1].closed is True
    assert scene.authority.get()['input']['enabled'] is False
    method = next(v[0] for k, v in scene.transport.methods.items() if k.startswith('native_append_input_'))
    assert method['input_schema']['properties']['action']['properties']['text'] == {'const': '新增中文🙂'}
    assert [call['mode'] for call in scene.transport.calls] == ['INTENT', 'ACTION']
    assert scene.service.intents.get()['active_grant_ref'] is None


def test_not_qualified_and_no_inspector_cannot_start_model_or_sdk(scene):
    scene.closure.reader.policy_ref = None
    result = scene.service.prepare_append(scene.prepare, TOKEN)
    assert result['state'] == 'NOT_ADAPTED'
    assert scene.transport.calls == [] and scene.native_calls == []
    with pytest.raises(Exception): asyncio.run(scene.service.submit(scene.request, TOKEN))
    assert scene.transport.calls == []


@pytest.mark.parametrize('part', ['source', 'runtime', 'evidence', 'os', 'exe', 'security'])
def test_actual_closure_support_drift_rejects_before_any_model(scene, part):
    if part == 'source': (scene.closure.source / sorted(REQUIRED_SOURCES)[0]).write_bytes(b'drift')
    elif part == 'runtime': (scene.closure.runtime / 'runtime/site-packages/cua_driver/_native.py').write_bytes(b'drift')
    elif part == 'evidence': (scene.closure.evidence / 'route.json').write_text('{}')
    else:
        original = scene.inspector.read
        def read(window):
            value = original(window)
            if part == 'os': value['os']['ubr'] += 1
            if part == 'exe': value['executable_sha256'] = 'e' * 64
            if part == 'security': value['same_user'] = False
            return value
        scene.inspector.read = read
    result = scene.service.prepare_append(scene.prepare, TOKEN)
    assert result['state'] == 'NOT_ADAPTED'
    assert not scene.native_calls and not scene.transport.calls


def test_missing_handoff_never_admits_original_execution(scene):
    approved = prepare_approve(scene)
    scene.service.trusted_activator = None
    result = asyncio.run(scene.service.start_append({'operation_id': scene.prepare['operation_id'],
        'preview_ref': approved['preview_ref'], 'approval_ref': approved['approval_ref']}, TOKEN))
    assert result['state'] == 'HANDOFF_REQUIRED' and not result['task_completed']
    assert scene.transport.calls == [] and scene.native_calls == []


@pytest.mark.parametrize('field', ['preview', 'epoch', 'gesture', 'payload'])
def test_forged_approval_or_changed_payload_cannot_dispatch(scene, field):
    approved = prepare_approve(scene)
    if field == 'preview':
        with pytest.raises(Conflict): scene.service.approve_append(scene.approve | {'preview_ref': REF}, TOKEN)
    elif field == 'epoch':
        with pytest.raises(Conflict): scene.service.approve_append(scene.approve | {'expected_intent_epoch': 999}, TOKEN)
    elif field == 'gesture':
        with pytest.raises(Conflict): scene.service.approve_append(scene.approve | {'main_gesture_ref': REF}, TOKEN)
    else:
        changed = copy.deepcopy(scene.prepare); changed['payload_source']['span']['quote'] = '另外文字'
        with pytest.raises(Conflict): scene.service.prepare_append(changed, TOKEN)
    assert scene.native_calls == [] and scene.transport.calls == []


@pytest.mark.parametrize('reason', ['buffer', 'caret', 'modifier', 'foreground', 'child'])
def test_same_worker_rechecks_queued_state_before_sdk(scene, reason):
    old_act = scene.companion.cua.act
    def act(*args, **kwargs):
        # Runs after RPC early admission but before the real shared SDK worker.
        if reason == 'buffer': scene.buffer += '人工修改'
        if reason == 'caret': scene.inspector.caret_delta = -1
        if reason == 'modifier': scene.inspector.modifiers_released = False
        if reason == 'foreground': scene.inspector.foreground = 77
        if reason == 'child': scene.inspector.edit_id += 1
        return old_act(*args, **kwargs)
    scene.companion.cua.act = act
    result = asyncio.run(complete(scene))
    assert result['task_status'] == 'UNKNOWN_OUTCOME', result
    assert not result['task_completed'] and scene.native_calls == []
    assert scene.service.intents.get()['active_grant_ref'] is None


def test_model_different_text_rejected_even_with_approved_permission(scene):
    old_input = scene.service.input
    def input(arguments, binding):
        changed = copy.deepcopy(arguments); changed['action']['text'] = '模型更换文字'
        return old_input(changed, binding)
    scene.service.input = input
    result = asyncio.run(complete(scene))
    assert result['task_status'] == 'UNKNOWN_OUTCOME' and not scene.native_calls


def test_unknown_result_never_retries_or_claims_goal_without_actual_buffer_change(scene):
    scene.change_buffer = False
    result = asyncio.run(complete(scene))
    assert result['task_status'] == 'FINISHED' and not result['task_completed']
    assert len(scene.native_calls) == 1
    again = asyncio.run(scene.service.start_append({'operation_id': scene.prepare['operation_id'],
        'preview_ref': result['preview_ref'], 'approval_ref': result['approval_ref']}, TOKEN))
    assert again['task_status'] == 'FINISHED' and len(scene.native_calls) == 1


def test_restart_approval_and_source_cannot_reauthorize(scene):
    approved = prepare_approve(scene)
    asyncio.run(scene.service.close())
    restarted = NotepadAppendTasks(scene.app, companion=scene.companion, inspector=scene.inspector,
        qualification_reader=scene.closure.reader, runtime_root=scene.closure.runtime,
        transport=OriginalLoopProtocolCounter())
    try:
        assert restarted.append_get(scene.prepare['operation_id'])['state'] == 'HISTORICAL_READONLY'
        with pytest.raises(Conflict, match='Historical'):
            asyncio.run(restarted.start_append({'operation_id': scene.prepare['operation_id'],
                'preview_ref': approved['preview_ref'], 'approval_ref': approved['approval_ref']}, TOKEN))
        assert restarted.transport.calls == [] and scene.native_calls == []
    finally: asyncio.run(restarted.close())


def test_http_private_header_strict_dto_and_one_service(scene):
    @scene.app.exception_handler(Conflict)
    async def conflict(_, error): return JSONResponse({'detail': str(error)}, status_code=409)
    register_native_task_routes(scene.app, companion=scene.companion, service=scene.service)
    register_native_append_routes(scene.app, service=scene.service)
    with TestClient(scene.app) as client:
        assert client.get('/api/desktop-host/native-append/get').status_code == 409
        headers = {'X-EEAS-Desktop-Host': TOKEN}
        assert client.post('/api/desktop-host/native-append/prepare', headers=headers,
            json=scene.prepare | {'qualified': True}).status_code == 422
        response = client.post('/api/desktop-host/native-append/prepare', headers=headers, json=scene.prepare)
        assert response.status_code == 200 and response.json()['state'] == 'PREPARED'
        assert client.get('/api/desktop-host/native-tasks/get', headers=headers).status_code == 200
        with pytest.raises(Conflict): register_native_append_routes(scene.app, service=object())


@pytest.mark.parametrize('payload', ['新增\n', '新增\t', '\x00', 'x' * 101])
def test_unqualified_control_and_oversized_payload_not_offered(scene, payload):
    scene.prepare['raw_text'] = '追加' + payload
    scene.prepare['payload_source']['span'] = {'start': 2, 'end': len(scene.prepare['raw_text']), 'quote': payload}
    with pytest.raises((Conflict, ValueError)): scene.service.prepare_append(scene.prepare, TOKEN)
    assert scene.native_calls == [] and scene.transport.calls == []


def test_win32_inspector_nonwindows_is_explicit_not_adapted(scene):
    import os
    if os.name != 'nt':
        with pytest.raises(Conflict, match='ACTUAL_WINDOWS'):
            Win32ClassicNotepadInspector(scene.source)


def test_source_drift_after_rpc_admission_is_rechecked_in_worker(scene):
    old_act = scene.companion.cua.act
    def act(*args, **kwargs):
        (scene.closure.source / 'apps/api/native_tasks.py').write_bytes(b'late-source-drift')
        return old_act(*args, **kwargs)
    scene.companion.cua.act = act
    result = asyncio.run(complete(scene))
    assert result['task_status'] == 'UNKNOWN_OUTCOME' and not scene.native_calls


def test_alternate_controller_runtime_is_not_adapted(scene):
    scene.factory.root = scene.closure.source
    result = scene.service.prepare_append(scene.prepare, TOKEN)
    assert result['state'] == 'NOT_ADAPTED' and 'controller' in result['reason']
    assert not scene.transport.calls and not scene.native_calls


def test_installed_engine_missing_from_component_manifest_closure_rejects(scene):
    pack = copy.deepcopy(scene.closure.pack)
    pack['runtime_files'][0]['path'] = 'unrelated.py'
    (scene.closure.runtime / 'unrelated.py').write_bytes(b'protocol-sdk-source')
    path = Path(scene.closure.policy_ref['path']); raw = canonical(pack).encode(); path.write_bytes(raw)
    scene.closure.reader.policy_ref = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
    result = scene.service.prepare_append(scene.prepare, TOKEN)
    assert result['state'] == 'NOT_ADAPTED' and not scene.transport.calls and not scene.native_calls


def test_symlink_runtime_policy_directory_cannot_write_outside_selected_root(scene, tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir()
    folder = scene.closure.runtime / 'toolchain' / 'native-append-policies'
    folder.symlink_to(outside, target_is_directory=True)
    result = asyncio.run(complete(scene))
    assert result['task_status'] == 'UNKNOWN_OUTCOME' and not scene.native_calls
    assert list(outside.iterdir()) == []


def test_unstarted_takeover_revokes_approval_but_preserves_preview(scene):
    approved = prepare_approve(scene)
    epoch = scene.service.intents.state['epoch']
    asyncio.run(scene.service.revoke({'operation_id': 'takeover', 'expected_intent_epoch': epoch}, TOKEN))
    state = scene.service.append_get(scene.prepare['operation_id'])
    assert state['state'] == 'REVOKED' and state['payload_text'] == '新增中文🙂'
    with pytest.raises(Conflict):
        asyncio.run(scene.service.start_append({'operation_id': scene.prepare['operation_id'],
            'preview_ref': approved['preview_ref'], 'approval_ref': approved['approval_ref']}, TOKEN))
    assert not scene.transport.calls and not scene.native_calls


def test_missing_model_configuration_does_not_activate_or_dispatch(scene):
    scene.prepare['provider_profile_ref'] = None
    approved = prepare_approve(scene)
    called = []
    scene.service.trusted_activator = lambda ticket: called.append(ticket)
    state = asyncio.run(scene.service.start_append({'operation_id': scene.prepare['operation_id'],
        'preview_ref': approved['preview_ref'], 'approval_ref': approved['approval_ref']}, TOKEN))
    assert state['state'] == 'NOT_CONFIGURED' and called == []
    assert scene.transport.calls == [] and scene.native_calls == []


def test_classic_readonly_abi_reports_utf16_end_and_final_caret_without_input(tmp_path):
    """Actual Python/ctypes ABI logic with message counters; no OS qualification."""
    import ctypes as ct
    exe = tmp_path / 'notepad.exe'; exe.write_bytes(b'only-read-executable-fixture')
    text = '原创🙂'; length = utf16_length(text)
    window = {'status': 'WINDOW_OBSERVED', 'window_id': 100, 'pid': 200, 'process_creation': 300,
        'os_session': 1, 'window_thread': 10, 'window_class': 'Notepad', 'identity_sha256': 'a' * 64,
        'bounds': {'x': 0, 'y': 0, 'width': 100, 'height': 100}}
    child = {'pid': 200, 'class': 'Edit'}
    class Gui(ct.Structure):
        _fields_ = [('cbSize', ct.c_uint32), ('flags', ct.c_uint32), ('hwndFocus', ct.c_void_p),
            ('hwndCapture', ct.c_void_p), ('hwndMenuOwner', ct.c_void_p), ('hwndMoveSize', ct.c_void_p)]
    obj = Win32ClassicNotepadInspector.__new__(Win32ClassicNotepadInspector)
    obj.max_units = 65536; obj.Gui = Gui
    obj.source = SimpleNamespace(inspect=lambda _: copy.deepcopy(window), ancestor=lambda *_: 100,
        process_executable=lambda _: str(exe), foreground=lambda: 100)
    def bind(*_):
        def session(pid, output): output._obj.value = 1; return 1
        return session
    obj.api = SimpleNamespace(window=lambda _: copy.deepcopy(child), kernel=object(), _bind=bind)
    obj.find = lambda root, after, *_: 101 if not after else 0
    obj.enabled = obj.visible = lambda _: 1
    obj.style = lambda *_: 0
    obj.key = lambda _: 0
    obj._security = lambda _: ['same-user-sid', 'same-integrity-sid']
    obj._os = lambda: {'major': 10, 'minor': 0, 'build': 19045, 'ubr': 100, 'architecture': 'AMD64'}
    messages = []
    def message(hwnd, kind, wp=0, lp=0):
        messages.append(kind)
        if kind == 0x000E: return length
        if kind == 0x000D:
            pointer = ct.cast(lp, ct.POINTER(ct.c_wchar))
            for index, char in enumerate(text): pointer[index] = char
            pointer[len(text)] = '\0'
            return length
        if kind == 0x00B0:
            ct.cast(wp, ct.POINTER(ct.c_uint32))[0] = length
            ct.cast(lp, ct.POINTER(ct.c_uint32))[0] = length
            return 0
        raise AssertionError('Unexpected mutable/native message')
    obj._message = message
    def gui(_, output):
        output._obj.hwndFocus = 101; output._obj.flags = 1  # GUI_CARETBLINKING is allowed.
        return 1
    obj.gui = gui
    snapshot = obj.read(window)
    assert snapshot['text'] == text and snapshot['utf16_length'] == 4
    assert snapshot['selection_start'] == snapshot['selection_end'] == 4
    assert snapshot['gui_idle'] and snapshot['same_user'] and snapshot['same_session']
    assert messages.count(0x00B0) == 2 and messages.count(0x000D) == 2
    assert set(messages) == {0x000E, 0x000D, 0x00B0}
