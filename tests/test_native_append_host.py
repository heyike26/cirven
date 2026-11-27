"""Real host broker/store/transport; window ABI fixtures are protocol only.

No shadow model loop is used. No Windows process, activation, SDK/controller or
model is launched; real Windows handoff qualification remains Root-owned.
"""
import asyncio
import copy
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.native_append import NotepadAppendTasks
from apps.api.native_append_host import NativeAppendHostBroker, register_native_append_host_routes
from apps.api.native_tasks import register_native_task_routes
from packages.adapters.cua_desktop_input import CuaDesktopInput
from packages.adapters.opencode_desktop import OpenCodeDesktopTransport
from packages.domain.local_workspaces import digest
from packages.domain.store import Conflict, NotFound, Store
from test_native_collaboration import host, BIND, TOKEN, CurrentWindowCounter
from test_native_append import qualification_fixture, InspectorCounter

REF = {'id': 'only-abi-counter-context', 'revision': 1, 'sha256': 'b' * 64}
COOKIE = 'only-local-session-cookie-for-protocol-test'


class UnusedSessionFactory:
    """No mock SDK success; calling this factory is a test failure."""
    def __init__(self, closure):
        self.root = closure.runtime
        self.manifest_ref = {'path': str(closure.runtime / 'component.json'),
            'sha256': next(v['sha256'] for v in closure.pack['runtime_files'] if v['role'] == 'sdk_component_manifest')}
        self.calls = []
    async def open(self, binding):
        self.calls.append(binding)
        raise AssertionError('Host handoff must not open an SDK session')
    async def shutdown(self): pass


@pytest.fixture
def scene(tmp_path):
    authority, native = host(tmp_path / 'host'); authority.bind(BIND)
    source = CurrentWindowCounter(); authority.set_window_source(source)
    target = authority.context_authority(source.window, authority.binding_reader())['target_ref']
    state = authority.get()
    context = {'context_ref': REF, 'window_ref': target, 'surface_ref': state['surface_ref'],
        'input_epoch': state['input']['epoch'], 'status': 'READY_OBSERVATION', 'questions': []}
    data = SimpleNamespace(authority=authority, native=native, source=source, buffer='原创 seed', native_calls=[])
    data.inspector = InspectorCounter(data)
    data.closure = qualification_fixture(tmp_path / 'closure', data.inspector.read(source.window))
    app = FastAPI(); app.state.settings = SimpleNamespace(data_dir=tmp_path)
    app.state.provider_settings = SimpleNamespace(configuration=lambda *_: None, secret=lambda *_: None)
    app.state.store = Store(tmp_path / 'store.sqlite3')
    companion = SimpleNamespace(authority=authority, context_rpc=SimpleNamespace(lookup=lambda _: copy.deepcopy(context)), cua=None)
    service = NotepadAppendTasks(app, companion=companion, inspector=data.inspector,
        qualification_reader=data.closure.reader, runtime_root=data.closure.runtime)
    factory = UnusedSessionFactory(data.closure)
    cua = CuaDesktopInput(data_dir=tmp_path / 'sdk', session_factory=factory,
        target_authority=service.target_authority, observation_guard=authority.observation_guard,
        control_guard=service.control_guard, platform='BROKER_ABI_PROTOCOL_NOT_WINDOWS')
    companion.cua = cua
    data.service, data.app, data.factory, data.cua = service, app, factory, cua
    data.broker = NativeAppendHostBroker(service=service, session_cookie_checker=lambda value: value == COOKIE)
    raw = '在当前记事本末尾追加新增中文🙂'
    data.prepare = {'operation_id': 'main-reviewed-append', 'context_ref': REF, 'raw_text': raw,
        'provider_profile_ref': REF, 'payload_source': {'kind': 'RAW_INSTRUCTION',
            'span': {'start': raw.index('新增'), 'end': len(raw), 'quote': '新增中文🙂'}}}
    service.prepare_append(data.prepare, TOKEN)
    yield data
    asyncio.run(service.close()); cua.shutdown(); authority.close()


def review(scene): return scene.broker.actual_review(scene.prepare['operation_id'], TOKEN, COOKIE)


def approve(scene, *, result='APPROVE'):
    current = review(scene)
    body = {'operation_id': scene.prepare['operation_id'], 'review_ref': current['review_ref'],
        'dialog_request_id': 'main-native-dialog-one', 'result': result}
    scene.dialog_body = body
    return scene.broker.dialog_result(body, TOKEN, COOKIE)


def commit_body(scene, approved, outcome='RETURNED'):
    return {'operation_id': scene.prepare['operation_id'], 'ticket_ref': approved['ticket_ref'], 'helper_outcome': outcome}


def assert_no_execution(scene):
    assert isinstance(scene.service.transport, OpenCodeDesktopTransport)
    assert scene.service.transport.active == {} and scene.factory.calls == [] and scene.native_calls == []
    assert scene.service.tasks == {} and not scene.service._path(scene.prepare['operation_id']).exists()
    with scene.app.state.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0


def test_server_review_and_approval_bind_exact_actual_source_without_model_or_sdk(scene):
    current = review(scene)
    assert current['status'] == 'REVIEW_READY' and current['can_show_dialog']
    assert current['payload_text'] == '新增中文🙂'
    assert current['baseline']['utf16_length'] > 0
    assert current['preview_ref'] == scene.service.append_get(scene.prepare['operation_id'])['preview_ref']
    approved = approve(scene)
    assert approved['status'] == 'APPROVED_HANDOFF_REQUIRED'
    assert approved['ticket']['operation_id'] == scene.prepare['operation_id']
    assert approved['activation_target']['process_creation_decimal'] == str(scene.source.window['process_creation'])
    assert approved['activation_host']['process_creation_decimal'] == str(scene.native.owner['process_creation'])
    gesture_ref = approved['ticket']['main_gesture_ref']; gesture = scene.broker.read_gesture(gesture_ref)
    assert digest(gesture) == gesture_ref['sha256'] and gesture['user_activation'] is True
    assert approved['task_state']['permission_state'] == 'APPROVED'
    assert not review(scene)['can_show_dialog']
    assert_no_execution(scene)


@pytest.mark.parametrize('result', ['DECLINE', 'CLOSED'])
def test_native_dialog_decline_preserves_result_and_no_approval_or_reconfirm(scene, result):
    value = approve(scene, result=result)
    assert value['status'] == ('DECLINED' if result == 'DECLINE' else 'CLOSED')
    assert value['task_state']['state'] == 'APPROVAL_CANCELLED' and value['task_state']['approval_ref'] is None
    assert scene.broker.dialog_result(scene.dialog_body, TOKEN, COOKIE)['status'] == value['status']
    with pytest.raises(Conflict): scene.broker.dialog_result(scene.dialog_body | {'result': 'APPROVE'}, TOKEN, COOKIE)
    assert not review(scene)['can_show_dialog']
    assert_no_execution(scene)


@pytest.mark.parametrize('kind', ['token', 'cookie'])
def test_private_main_header_and_cookie_both_required_before_review_or_dialog(scene, kind):
    token, cookie = ('bad', COOKIE) if kind == 'token' else (TOKEN, 'bad')
    with pytest.raises(Exception): scene.broker.actual_review(scene.prepare['operation_id'], token, cookie)
    assert scene.broker._state(scene.prepare['operation_id']) is None
    assert_no_execution(scene)


@pytest.mark.parametrize('change', ['text', 'caret', 'source', 'epoch', 'birth'])
def test_changed_actual_review_cannot_be_approved(scene, change):
    current = review(scene)
    if change == 'text': scene.buffer += '人工保留'
    elif change == 'caret': scene.inspector.caret_delta = -1
    elif change == 'source': (scene.closure.source / 'apps/api/native_tasks.py').write_bytes(b'changed-source')
    elif change == 'epoch': scene.service.intents.state['epoch'] += 1
    else: scene.source.window['process_creation'] += 1
    body = {'operation_id': scene.prepare['operation_id'], 'review_ref': current['review_ref'],
        'dialog_request_id': 'main-native-dialog', 'result': 'APPROVE'}
    value = scene.broker.dialog_result(body, TOKEN, COOKIE)
    assert value['status'] == 'REVIEW_INVALIDATED' and value['approval_ref'] is None
    assert_no_execution(scene)


@pytest.mark.parametrize('change', ['foreground', 'text', 'caret', 'modifier', 'source'])
def test_main_returned_claim_cannot_replace_independent_fresh_handoff(scene, change):
    approved = approve(scene)
    if change == 'foreground': scene.inspector.foreground = 77
    elif change == 'text': scene.buffer += '交接期间人工编辑'
    elif change == 'caret': scene.inspector.caret_delta = -1
    elif change == 'modifier': scene.inspector.modifiers_released = False
    else: (scene.closure.source / 'apps/api/native_append.py').write_bytes(b'drift-after-approval')
    body = commit_body(scene, approved)
    result = scene.broker.commit_handoff(body, TOKEN, COOKIE)
    assert result['status'] == 'HANDOFF_UNVERIFIED' and not result['task_completed']
    assert result['task_state']['state'] == 'HANDOFF_REQUIRED'
    assert scene.broker.commit_handoff(body, TOKEN, COOKIE)['evidence_ref'] == result['evidence_ref']
    with pytest.raises(Conflict): scene.broker.read_handoff(approved['ticket'])
    with pytest.raises(Conflict): asyncio.run(scene.broker.start({'operation_id': scene.prepare['operation_id'], 'ticket_ref': approved['ticket_ref']}, TOKEN, COOKIE))
    assert_no_execution(scene)


@pytest.mark.parametrize('outcome,status', [('OS_REFUSED', 'OS_REFUSED'), ('UNKNOWN', 'HANDOFF_UNKNOWN')])
def test_refusal_and_unknown_are_durable_terminal_without_automatic_retry(scene, outcome, status):
    approved = approve(scene)
    result = scene.broker.commit_handoff(commit_body(scene, approved, outcome), TOKEN, COOKIE)
    assert result['status'] == status and not result['automatic_retry']
    with pytest.raises(Conflict): scene.broker.commit_handoff(commit_body(scene, approved, 'RETURNED'), TOKEN, COOKIE)
    assert not review(scene)['can_show_dialog']
    assert_no_execution(scene)


def test_linux_protocol_inspector_is_never_real_windows_success(scene):
    import os
    if os.name != 'nt':
        approved = approve(scene)
        result = scene.broker.commit_handoff(commit_body(scene, approved), TOKEN, COOKIE)
        assert result['status'] == 'HANDOFF_UNVERIFIED'
        proof = scene.service._append_read('host-handoff-evidence', result['evidence_ref'])
        assert proof['checks']['exact_identity'] and proof['checks']['exact_foreground']
        assert proof['checks']['actual_windows_inspector'] is False and proof['status'] == 'NOT_VERIFIED'
        with pytest.raises(Conflict): scene.broker.read_handoff(approved['ticket'])
        assert_no_execution(scene)


def test_ticket_epoch_expiry_mismatch_and_unknown_commit_cannot_change_input_target(scene):
    approved = approve(scene)
    with pytest.raises(Conflict): scene.broker.commit_handoff(commit_body(scene, approved) | {'ticket_ref': REF}, TOKEN, COOKIE)
    scene.service.clock = lambda: 4100000000
    expired = scene.broker.commit_handoff(commit_body(scene, approved), TOKEN, COOKIE)
    assert expired['status'] == 'EXPIRED'
    with pytest.raises(Conflict): asyncio.run(scene.broker.start({'operation_id': scene.prepare['operation_id'], 'ticket_ref': approved['ticket_ref']}, TOKEN, COOKIE))
    assert_no_execution(scene)


def test_takeover_revokes_unstarted_ticket_and_keeps_readonly_preview(scene):
    approved = approve(scene)
    asyncio.run(scene.broker.revoke({'operation_id': 'takeover', 'expected_intent_epoch': scene.service.intents.state['epoch']}, TOKEN, COOKIE))
    value = review(scene)
    assert value['status'] == 'REVOKED' and value['task_state']['payload_text'] == '新增中文🙂'
    with pytest.raises(Conflict): scene.broker.commit_handoff(commit_body(scene, approved), TOKEN, COOKIE)
    assert_no_execution(scene)


def test_host_broker_cannot_create_a_second_owner_or_replace_registered_hooks(scene):
    before = scene.service.gesture_reader
    with pytest.raises(Conflict): NativeAppendHostBroker(service=scene.service, session_cookie_checker=lambda _: True)
    assert scene.service.gesture_reader == before
    scene.app.state.native_tasks = object()
    with pytest.raises(Conflict): register_native_append_host_routes(scene.app, broker=scene.broker)
    assert_no_execution(scene)


def test_restart_with_new_token_only_returns_history_never_old_permission(scene):
    approve(scene)
    asyncio.run(scene.service.close())
    base, native = host(scene.authority.data_dir, token='c' * 64); base.bind(BIND)
    companion = SimpleNamespace(authority=base, context_rpc=SimpleNamespace(lookup=lambda _: None), cua=None)
    new = NotepadAppendTasks(scene.app, companion=companion, inspector=scene.inspector,
        qualification_reader=scene.closure.reader, runtime_root=scene.closure.runtime)
    broker = NativeAppendHostBroker(service=new, session_cookie_checker=lambda value: value == COOKIE)
    try:
        current = broker.actual_review(scene.prepare['operation_id'], 'c' * 64, COOKIE)
        assert current['status'] == 'HISTORICAL_READONLY' and not current['can_show_dialog']
        with pytest.raises(Conflict): broker.dialog_result(scene.dialog_body, 'c' * 64, COOKIE)
        assert new.tasks == {} and scene.factory.calls == []
    finally: asyncio.run(new.close()); base.close()


def test_http_strict_main_only_payload_schema_and_cookie(scene):
    @scene.app.exception_handler(Conflict)
    async def conflict(_, error): return JSONResponse({'detail': str(error)}, status_code=409)
    register_native_task_routes(scene.app, companion=scene.service.companion, service=scene.service)
    register_native_append_host_routes(scene.app, broker=scene.broker)
    with TestClient(scene.app) as client:
        route = '/api/desktop-host/native-append-host'
        headers = {'X-EEAS-Desktop-Host': TOKEN}
        assert client.get(route + '/actual-review', params={'operation_id': scene.prepare['operation_id']}, headers=headers).status_code == 401
        client.cookies.set('eeas_session', COOKIE)
        response = client.get(route + '/actual-review', params={'operation_id': scene.prepare['operation_id']}, headers=headers)
        assert response.status_code == 200 and response.json()['payload_text'] == '新增中文🙂'
        body = {'operation_id': scene.prepare['operation_id'], 'review_ref': response.json()['review_ref'],
            'dialog_request_id': 'main-dialog', 'result': 'APPROVE'}
        assert client.post(route + '/dialog-result', headers=headers, json=body | {'approved': True}).status_code == 422
        assert client.post(route + '/dialog-result', headers=headers, json=body | {'target': {'pid': 1}}).status_code == 422
        approved = client.post(route + '/dialog-result', headers=headers, json=body)
        assert approved.status_code == 200 and approved.json()['ticket_ref']
        commit = commit_body(scene, approved.json())
        assert client.post(route + '/commit-handoff', headers=headers, json=commit | {'foreground_verified': True}).status_code == 422
        assert_no_execution(scene)


def test_refresh_get_preserves_decline_without_refreezing_or_inspecting(scene):
    declined = approve(scene, result='DECLINE')
    before_reads = scene.inspector.reads
    before_files = {p: p.read_bytes() for p in scene.service._append_path('host-broker-operations', 'placeholder').parent.parent.rglob('*.json')}
    first = scene.broker.get(scene.prepare['operation_id'], TOKEN, COOKIE)
    second = scene.broker.get(scene.prepare['operation_id'], TOKEN, COOKIE)
    assert first['status'] == second['status'] == 'DECLINED'
    assert first['task_state']['state'] == 'APPROVAL_CANCELLED'
    assert first['review_ref'] == declined['review_ref'] and scene.inspector.reads == before_reads
    assert {p: p.read_bytes() for p in scene.service._append_path('host-broker-operations', 'placeholder').parent.parent.rglob('*.json')} == before_files
    assert_no_execution(scene)


def test_refresh_get_preserves_handoff_unknown_no_new_dialog(scene):
    approved = approve(scene)
    unknown = scene.broker.commit_handoff(commit_body(scene, approved, 'UNKNOWN'), TOKEN, COOKIE)
    first = scene.broker.get(scene.prepare['operation_id'], TOKEN, COOKIE)
    assert first['status'] == 'HANDOFF_UNKNOWN' and first['task_state']['state'] == 'UNKNOWN_OUTCOME'
    assert first['evidence_ref'] == unknown['evidence_ref'] and not first['can_show_dialog']
    with pytest.raises(Conflict): scene.broker.dialog_result(scene.dialog_body | {'dialog_request_id': 'new-dialog'}, TOKEN, COOKIE)
    assert_no_execution(scene)


def test_decimal_safe_activation_fields_never_round_filetime_or_hwnd(scene):
    preview = {'baseline': {'window_id': (1 << 60) + 3, 'pid': 42, 'process_creation': (1 << 60) + 7,
        'identity_sha256': 'a' * 64, 'edit_window_id': (1 << 60) + 11}}
    result = scene.broker._target(preview)
    assert result['window_id_decimal'] == str((1 << 60) + 3)
    assert result['process_creation_decimal'] == str((1 << 60) + 7)
    assert result['edit_window_id_decimal'] == str((1 << 60) + 11)
    assert_no_execution(scene)


def test_no_model_profile_cannot_open_main_native_dialog_or_issue_ticket(scene):
    body = copy.deepcopy(scene.prepare); body.update(operation_id='no-model-profile', provider_profile_ref=None)
    prepared = scene.service.prepare_append(body, TOKEN)
    response = scene.broker.actual_review(body['operation_id'], TOKEN, COOKIE)
    assert response['status'] == 'NOT_CONFIGURED' and not response['can_show_dialog']
    assert response['preview_ref'] == prepared['preview_ref']
    assert scene.broker._state(body['operation_id']) is None
    assert_no_execution(scene)


def test_public_task_state_keeps_actual_failure_reason_for_main_whitelist(scene):
    approved = approve(scene)
    scene.inspector.foreground = 77
    result = scene.broker.commit_handoff(commit_body(scene, approved), TOKEN, COOKIE)
    assert result['reason'] and result['task_state']['reason'] == result['reason']
    restored = scene.broker.get(scene.prepare['operation_id'], TOKEN, COOKIE)
    assert restored['task_state']['reason'] == result['reason']
    assert_no_execution(scene)


def test_replaced_intent_owner_cannot_keep_old_broker_authority(scene):
    from packages.domain.desktop_intents import DesktopIntents
    replacement = DesktopIntents(scene.service.authority)
    with pytest.raises(Conflict, match='lifetime'):
        review(scene)
    assert replacement.get()['active_grant_ref'] is None
    assert_no_execution(scene)
