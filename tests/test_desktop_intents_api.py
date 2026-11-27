"""Actual private RPC, immutable records and real original CLI interpretation.

External provider wire only is replaced. No native success/driver/session is
manufactured; default qualification remains false through all HTTP routes.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.desktop_intents import register_desktop_intent_routes
from packages.domain.desktop_intents import DesktopIntents, DesktopIntentCandidate
from packages.domain.local_workspaces import canonical, sha
from packages.domain.store import Conflict, NotFound, InvalidInput
from packages.domain.workspace_desktop import WorkspaceDesktop
from test_desktop_intents import IntentModelWire
from test_opencode_planning import scene, transport, KEY, CLI, CLI_SHA


TOKEN = 'a' * 64
HEADERS = {'X-EEAS-Desktop-Host': TOKEN}
ROOT = '/api/desktop-host/intents'
REF = {'id': 'f' * 32, 'revision': 1, 'sha256': 'b' * 64}
EXECUTION = {'project_id': 'internal-desktop', 'execution_id': 'not-adopted-execution', 'request_sha256': 'c' * 64}


def application(value, **kwargs):
    app = FastAPI(); app.state.workspace_desktop = value.ws
    @app.exception_handler(Conflict)
    async def conflict(_request, exc): return JSONResponse({'error': str(exc)}, status_code=409)
    @app.exception_handler(NotFound)
    async def missing(_request, exc): return JSONResponse({'error': str(exc)}, status_code=404)
    @app.exception_handler(InvalidInput)
    async def invalid(_request, exc): return JSONResponse({'error': str(exc)}, status_code=422)
    assert register_desktop_intent_routes(app, None, value.ws.data_dir, service=value, **kwargs) is value
    return app


@pytest.fixture
def api(tmp_path):
    ws = WorkspaceDesktop(tmp_path, token=TOKEN, owner_pid=123, platform='posix')
    value = DesktopIntents(ws)
    app = application(value)
    with TestClient(app) as client:
        yield client, value


def post(client, endpoint, body): return client.post(ROOT + endpoint, json=body, headers=HEADERS)


def test_all_private_host_endpoints_reject_missing_or_wrong_token_before_recording(api):
    client, value = api
    requests = [('/messages', {'operation_id': 'raw', 'raw_text': '打开E09'}),
        ('/interpret', {'operation_id': 'interpret', 'message_ref': REF, 'execution_ref': EXECUTION}),
        ('/authorize', {'operation_id': 'authorize', 'interpretation_ref': REF}),
        ('/bind', {'operation_id': 'bind', 'authorization_ref': REF, 'execution_ref': EXECUTION, 'expected_intent_epoch': 1}),
        ('/revoke', {'operation_id': 'take', 'expected_intent_epoch': 1}), ('/finish', {'execution_ref': EXECUTION})]
    for endpoint, body in requests:
        assert client.post(ROOT + endpoint, json=body).status_code == 409
        assert client.post(ROOT + endpoint, json=body, headers={'X-EEAS-Desktop-Host': 'b' * 64}).status_code == 409
    assert client.get(ROOT + '/get').status_code == 409
    assert client.get(ROOT + '/messages/' + REF['id'], params={'revision': 1, 'sha256': REF['sha256']}).status_code == 409
    assert not value._path('messages').exists() and value.get()['intent_epoch'] == 1


def test_http_original_message_unicode_whitespace_sha_read_replay_and_conflict(api):
    client, value = api; raw = '  打开 VSCode 左栏 E09.md。\r\n中文🙂\t'
    body = {'operation_id': 'raw-original', 'raw_text': raw, 'context_ref': None}
    created = post(client, '/messages', body)
    assert created.status_code == 201, created.text
    data = created.json(); ref = data['message_ref']
    assert data['raw_text_sha256'] == hashlib.sha256(raw.encode()).hexdigest() and not data['mutation_authority']
    observed = client.get(ROOT + '/messages/' + ref['id'], params={'revision': ref['revision'], 'sha256': ref['sha256']}, headers=HEADERS)
    assert observed.status_code == 200 and observed.json()['raw_text'] == raw
    assert observed.json()['message_ref'] == ref and observed.json()['role'] == 'user'
    repeated = post(client, '/messages', body)
    assert repeated.status_code == 200 and repeated.json()['replayed'] and repeated.json()['message_ref'] == ref
    assert post(client, '/messages', body | {'raw_text': raw.strip()}).status_code == 409
    assert client.get(ROOT + '/messages/' + ref['id'], params={'revision': 1, 'sha256': 'd' * 64}, headers=HEADERS).status_code == 409
    assert client.get(ROOT + '/messages/' + REF['id'], params={'revision': 1, 'sha256': REF['sha256']}, headers=HEADERS).status_code == 404
    assert len(list(value._path('messages').glob('*.json'))) == 1


@pytest.mark.parametrize('endpoint,body', [
    ('/messages', {'operation_id': 'x', 'raw_text': '  \r\n'}),
    ('/messages', {'operation_id': 'x', 'raw_text': True}),
    ('/messages', {'operation_id': 'x', 'raw_text': '原消息', 'origin': 'user'}),
    ('/messages', {'operation_id': 'x', 'raw_text': '原消息', 'authorized': True}),
    ('/messages', {'operation_id': '../escape', 'raw_text': '原消息'}),
    ('/interpret', {'operation_id': 'x', 'message_ref': REF | {'revision': True}, 'execution_ref': EXECUTION}),
    ('/interpret', {'operation_id': 'x', 'message_ref': REF, 'execution_ref': EXECUTION, 'candidate': {'intent': 'ACTION'}}),
    ('/authorize', {'operation_id': 'x', 'interpretation_ref': REF, 'allowed_effects': ['CLICK']}),
    ('/bind', {'operation_id': 'x', 'authorization_ref': REF, 'execution_ref': EXECUTION, 'expected_intent_epoch': True}),
    ('/revoke', {'operation_id': 'x', 'expected_intent_epoch': 0}),
    ('/revoke', {'operation_id': 'x', 'expected_intent_epoch': 1, 'kill_apps': True}),
    ('/finish', {'execution_ref': EXECUTION, 'task_completed': True}),
])
def test_malformed_or_model_self_authorizing_body_has_no_side_effect(api, endpoint, body):
    client, value = api
    response = post(client, endpoint, body)
    assert response.status_code == 422, response.text
    assert value.get()['owner'] == 'USER' and value.get()['intent_epoch'] == 1
    assert not value._path('messages').exists() and not value._path('grants').exists()


def test_registration_reuses_exact_owner_and_never_invents_transport_or_capability(tmp_path):
    app = FastAPI(); ws = WorkspaceDesktop(tmp_path, token=TOKEN, owner_pid=123, platform='posix')
    app.state.workspace_desktop = ws
    value = register_desktop_intent_routes(app, None, tmp_path)
    count = len(app.routes)
    again = register_desktop_intent_routes(app, None, tmp_path, capability_reader=lambda: {'desktop_input_routes_qualified': True})
    assert again is value and len(app.routes) == count and value.transport is None
    assert value.ws is ws and value.lock is ws.lock and not value.get()['capabilities']['single_task_input_ready']
    assert value.authorizer is None and value.result_checker is None


def test_precomposed_domain_gets_real_routes_once_and_cannot_be_replaced(tmp_path):
    ws = WorkspaceDesktop(tmp_path, token=TOKEN, owner_pid=123, platform='posix')
    value = DesktopIntents(ws); app = FastAPI()
    app.state.workspace_desktop = ws; app.state.desktop_intents = value
    @app.exception_handler(Conflict)
    async def conflict(_request, exc): return JSONResponse({'error': str(exc)}, status_code=409)
    assert register_desktop_intent_routes(app, None, tmp_path) is value
    count = len(app.routes)
    assert app.state.desktop_intents_routes_registered
    assert register_desktop_intent_routes(app, None, tmp_path) is value and len(app.routes) == count
    other_ws = WorkspaceDesktop(tmp_path / 'other-authority', token='b' * 64, owner_pid=321, platform='posix')
    replacement = DesktopIntents(other_ws)
    with pytest.raises(Conflict, match='replace'): register_desktop_intent_routes(app, None, tmp_path, service=replacement)
    with TestClient(app) as client:
        assert client.get(ROOT + '/get', headers=HEADERS).status_code == 200
        assert client.get(ROOT + '/get', headers={'X-EEAS-Desktop-Host': 'b' * 64}).status_code == 409


def test_constructor_preserves_server_callback_injection_without_native_qualification(tmp_path):
    app = FastAPI(); ws = WorkspaceDesktop(tmp_path, token=TOKEN, owner_pid=123, platform='posix')
    app.state.workspace_desktop = ws
    calls = []
    def semantic(*_args): calls.append('semantic'); return None
    def result(*_args): calls.append('result'); return {'status': 'UNKNOWN', 'reason': '未核实际结果'}
    value = register_desktop_intent_routes(app, None, tmp_path, authorizer=semantic, result_checker=result)
    assert value.authorizer is semantic and value.result_checker is result
    with TestClient(app) as client:
        message = post(client, '/messages', {'operation_id': 'callback-scope', 'raw_text': '原消息'}).json()
        assert not message['mutation_authority']
        assert post(client, '/finish', {'execution_ref': EXECUTION}).json()['status'] == 'NO_TASK_GRANT'
        assert not client.get(ROOT + '/get', headers=HEADERS).json()['capabilities']['single_task_input_ready']
    assert calls == []


def test_unqualified_bind_denies_http_409_and_replay_cannot_forge_permission(api):
    client, value = api
    body = {'operation_id': 'default-deny', 'authorization_ref': REF, 'execution_ref': EXECUTION, 'expected_intent_epoch': 1}
    first = post(client, '/bind', body)
    assert first.status_code == 409
    data = first.json()
    assert data['status'] == 'UNAVAILABLE' and data['grant_ref'] is None and data['owner'] == 'USER'
    assert not data['mutation_authority'] and data['error']['code'] == 'DESKTOP_TASK_INPUT_UNAVAILABLE'
    repeated = post(client, '/bind', body)
    assert repeated.status_code == 409 and repeated.json()['replayed'] and not repeated.json()['mutation_authority']
    assert value.get()['intent_epoch'] == 1 and not value._path('grants').exists()
    assert post(client, '/bind', body | {'expected_intent_epoch': 2}).status_code == 409


def test_revoke_cas_replay_restart_and_unrelated_finish_do_not_cancel_or_adopt(api):
    client, value = api
    first = post(client, '/revoke', {'operation_id': 'human-take', 'expected_intent_epoch': 1})
    assert first.status_code == 200 and first.json()['owner'] == 'USER' and first.json()['intent_epoch'] == 2
    assert first.json()['cancellation']['confirmed'] and first.json()['cancellation']['outcomes'] == []
    replay = post(client, '/revoke', {'operation_id': 'human-take', 'expected_intent_epoch': 1})
    assert replay.status_code == 200 and replay.json()['replayed'] and replay.json()['cancellation'] == first.json()['cancellation']
    assert post(client, '/revoke', {'operation_id': 'late-take', 'expected_intent_epoch': 1}).status_code == 409
    finish = post(client, '/finish', {'execution_ref': EXECUTION})
    assert finish.status_code == 200 and finish.json()['status'] == 'NO_TASK_GRANT'
    assert value.get()['model_calls'] == 0 and not value.get()['can_takeover']
    restarted = DesktopIntents(value.ws)
    with TestClient(application(restarted)) as replacement:
        state = replacement.get(ROOT + '/get', headers=HEADERS).json()
        assert state['owner'] == 'USER' and state['intent_epoch'] == 3 and not state['capabilities']['single_task_input_ready']
        assert post(replacement, '/revoke', {'operation_id': 'human-take', 'expected_intent_epoch': 1}).json()['replayed']
    assert post(client, '/messages', {'operation_id': 'old-main', 'raw_text': '迟到'}).status_code == 409


def test_no_public_raw_input_route_or_mutable_model_authorization_endpoint(api):
    client, value = api
    for endpoint in ['/input', '/act', '/execute', '/prepare-input']:
        assert post(client, endpoint, {'kind': 'CLICK', 'hwnd': '123', 'authorized': True}).status_code == 404
    assert post(client, '/interpret', {'operation_id': 'no-original', 'message_ref': REF, 'execution_ref': EXECUTION}).status_code == 404
    assert post(client, '/authorize', {'operation_id': 'no-original', 'interpretation_ref': REF}).status_code == 404
    assert value.get()['model_calls'] == 0 and not value._path('inputs').exists()


def test_actual_private_api_reads_only_immutable_original_loop_result_and_preserves_sources(scene, tmp_path):
    assert sha(CLI.read_bytes()) == CLI_SHA[os.name]
    wire = IntentModelWire().start(); native, request, _ = transport(scene, wire)
    ws = WorkspaceDesktop(tmp_path / 'private-api-records', token=TOKEN, owner_pid=123, platform='posix')
    value = DesktopIntents(ws, transport=native)
    cases = []; evidence = {'qualification': 'ACTUAL_PRIVATE_HTTP_AND_ORIGINAL_CLI_EXTERNAL_MODEL_WIRE_ONLY',
        'native_input': 'NOT_RUN_NO_WINDOW_DRIVER_SESSION_OR_GRANT', 'paid_model_calls': 0}
    try:
        with TestClient(application(value)) as client:
            for index, raw in enumerate(['  当前页面是什么？\r\n', '  打开 VSCode 左栏 E09.md。\r\n']):
                created = post(client, '/messages', {'operation_id': 'actual-message-' + str(index), 'raw_text': raw})
                assert created.status_code == 201, created.text
                message_ref = created.json()['message_ref']
                result = asyncio.run(native.propose(request | {'operation_id': 'private-intent-' + str(index),
                    'prompt': value.interpretation_prompt(message_ref), 'output_schema': DesktopIntentCandidate.model_json_schema()}))
                assert result['status'] == 'RETURNED' and result['usage']['rpc_attempts'] == 1
                row = native.inspect(scene['pid'], result['execution_id'])
                exec_ref = {'project_id': scene['pid'], 'execution_id': row['id'], 'request_sha256': row['request_hash']}
                body = {'operation_id': 'actual-interpret-' + str(index), 'message_ref': message_ref, 'execution_ref': exec_ref}
                interpreted = post(client, '/interpret', body)
                assert interpreted.status_code == 200 and interpreted.json()['intent'] == ('OBSERVE' if index == 0 else 'ACTION')
                assert post(client, '/interpret', body).json()['replayed']
                assert post(client, '/interpret', body | {'operation_id': 'wrong-request-' + str(index),
                    'execution_ref': exec_ref | {'request_sha256': 'e' * 64}}).status_code == 409
                authorize = post(client, '/authorize', {'operation_id': 'actual-authorize-' + str(index),
                    'interpretation_ref': interpreted.json()['interpretation_ref']})
                assert authorize.status_code == 200
                assert authorize.json()['status'] == ('OBSERVE' if index == 0 else 'NEEDS_INPUT')
                assert authorize.json()['owner'] == 'USER' and not authorize.json()['mutation_authority']
                assert authorize.json()['interaction_mode'] == ('NONE' if index == 0 else 'INLINE_QUESTION')
                assert post(client, '/finish', {'execution_ref': exec_ref}).json()['status'] == 'NO_TASK_GRANT'
                cases.append({'message': created.json(), 'interpret_request': body, 'interpreted': interpreted.json(),
                    'authorization': authorize.json(), 'native_result': result})
                if index == 1:
                    assert post(client, '/interpret', body | {'operation_id': 'wrong-original-message',
                        'message_ref': cases[0]['message']['message_ref']}).status_code == 409
            state = client.get(ROOT + '/get', headers=HEADERS).json()
            assert state['owner'] == 'USER' and not state['can_takeover'] and not state['capabilities']['single_task_input_ready']
            assert state['model_calls'] == 0 and len(wire.requests) == 2
            assert scene['file'].read_bytes() == scene['original'] and not value._path('grants').exists()
            with scene['service'].connect() as db:
                assert db.execute('SELECT count(*) FROM task_runs').fetchone()[0] == 0
    finally:
        evidence['cases'] = cases; evidence['wire_requests'] = wire.requests
        destination = os.environ.get('EEAS_DESKTOP_INTENT_API_EVIDENCE')
        if destination:
            folder = Path(destination); folder.mkdir(parents=True, exist_ok=True)
            (folder / 'private-api-original-receipt.json').write_text(canonical(evidence).replace(KEY, '<fixture-credential-redacted>'))
            for index, case in enumerate(cases):
                row = native.inspect(scene['pid'], case['interpret_request']['execution_ref']['execution_id'])
                target = folder / ('native-' + str(index)); target.mkdir(exist_ok=True)
                for name in ['http-starts.jsonl', 'http-responses.jsonl', 'native-events.jsonl', 'planning-native-result.json']:
                    path = Path(row['workspace']) / name
                    if path.is_file(): (target / name).write_bytes(path.read_bytes().replace(KEY.encode(), b'<fixture-credential-redacted>'))
        asyncio.run(native.close()); wire.close()
