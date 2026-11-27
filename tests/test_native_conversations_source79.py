"""Real host-authorized ASGI/Store reads; no model, transport or Windows run."""
import copy

from fastapi.testclient import TestClient
import pytest

import apps.api.native_messages as module
from packages.domain.store import Conflict, NotFound
from test_native_messages import application, TOKEN


def scene(tmp_path):
    app, native, _ = application(tmp_path)
    a = app.state.store.create_project('QA A', '')['id']
    b = app.state.store.create_project('QA B', '')['id']
    return app, native, a, b


def send(client, pid, conversation, operation, text):
    response = client.post('/api/desktop-host/native-messages', json={
        'project_id': pid, 'conversation_id': conversation,
        'operation_id': operation, 'raw_text': text})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'NOT_CONFIGURED'


def test_native_directory_and_read_share_exact_project_conversation_without_dispatch(tmp_path):
    app, native, a, b = scene(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        send(client, a, 'same-id', 'a1', '  原始 Unicode 😀\r\n')
        send(client, a, 'other-topic', 'a2', '另一讨论')
        send(client, b, 'same-id', 'b1', '不同项目不可混入')
        send(client, a, 'same-id', 'a3', '后续原消息')
        directory = client.get('/api/desktop-host/native-conversations', params={'project_id': a}).json()
        assert {v['conversation_id'] for v in directory['conversations']} == {'same-id', 'other-topic'}
        assert directory['coverage']['complete'] and not directory['coverage']['has_more']
        value = client.get('/api/desktop-host/native-conversations/same-id', params={'project_id': a}).json()
        assert [v['operation_id'] for v in value['messages']] == ['a1', 'a3']
        assert value['messages'][0]['input'] == {'raw_text': '  原始 Unicode 😀\r\n', 'origin': 'ORIGINAL_NATIVE_REQUEST'}
        assert all(v['project_id'] == a and v['conversation_id'] == 'same-id' for v in value['messages'])
        assert '不同项目' not in str(value) and '另一讨论' not in str(value)
        assert value['latest_provider_profile_ref'] is None
        assert native.transport.active == {} and native.tasks == {}
        assert all(not any(v['control_capabilities'].values()) for v in value['messages'])


def test_scoped_operation_rejects_other_conversation_and_host_authorization_is_required(tmp_path):
    app, native, a, b = scene(tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/desktop-host/native-conversations', params={'project_id': a}).status_code == 409
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        send(client, a, 'a-topic', 'exact', 'private to A')
        route = '/api/desktop-host/native-conversations/a-topic/operations/exact'
        assert client.get(route, params={'project_id': a}).json()['operation_id'] == 'exact'
        assert client.get(route, params={'project_id': b}).status_code == 409
        assert client.get(route.replace('a-topic', 'b-topic'), params={'project_id': a}).status_code == 409
        assert client.get('/api/desktop-host/native-conversations', params={'project_id': a, 'limit': 31}).status_code == 422
        assert client.get('/api/desktop-host/native-conversations/a-topic', params={'project_id': a, 'limit': 41}).status_code == 422
        assert native.transport.active == {}


def test_directory_limit_and_scan_window_are_explicit_and_cannot_create_a_discussion(tmp_path, monkeypatch):
    app, native, a, _ = scene(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        for n in range(3): send(client, a, 'topic-' + str(n), 'op-' + str(n), 'text')
        value = native.ide_conversations(a, 1)
        assert len(value['conversations']) == 1 and value['coverage']['has_more'] and not value['coverage']['complete']
        before = {p.name: p.read_bytes() for p in native.ws._path('native-messages').glob('*.json')}
        with pytest.raises(NotFound): native.ide_conversation(a, 'never-created', 20)
        monkeypatch.setattr(module, 'IDE_CANDIDATE_LIMIT', 2)
        value = native.ide_conversations(a, 20)
        assert value['coverage']['candidate_window_truncated'] and value['coverage']['has_more']
        with pytest.raises(Conflict): native.ide_conversation(a, 'never-created', 20)
        assert before == {p.name: p.read_bytes() for p in native.ws._path('native-messages').glob('*.json')}


def test_projection_uses_original_provider_ref_and_strips_execution_and_tool_data(tmp_path):
    app, native, a, _ = scene(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        send(client, a, 'topic', 'original', '{"host_prompt":"not a user quotation"}')
        saved = native.ws._read(native.path('original'), authority=False)
        profile = {'id': 'public-profile', 'revision': 2, 'sha256': 'a' * 64}
        saved['request']['provider_profile_ref'] = profile
        saved['request_sha256'] = module.digest(saved['request'])
        native.ws._write(native.path('original'), saved)
        original_get = native.get
        native.get = lambda op: {**original_get(op), 'execution': {'token': 'DO_NOT_PROJECT'},
            'events': [{'secret': 'DO_NOT_PROJECT'}], 'error': {'message': 'DO_NOT_PROJECT_DIAGNOSTIC'}, 'text': 'actual reply'}
        try:
            value = native.ide_conversation(a, 'topic', 20)
        finally:
            native.get = original_get
        assert value['latest_provider_profile_ref'] == profile
        assert value['messages'][0]['provider_profile_ref'] == profile
        assert value['messages'][0]['input']['origin'] == 'ORIGINAL_NATIVE_REQUEST'
        assert 'DO_NOT_PROJECT' not in str(value) and 'execution' not in value['messages'][0]
        changed = copy.deepcopy(saved); changed['request']['raw_text'] = 'changed without digest'
        native.ws._write(native.path('original'), changed)
        with pytest.raises(Conflict): native.ide_operation(a, 'topic', 'original')


def test_legacy_unbound_same_conversation_is_not_inferred_from_another_bound_message(tmp_path):
    app, native, a, _ = scene(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        send(client, None, 'same-topic', 'legacy-unbound', 'unbound historical private material')
        send(client, a, 'same-topic', 'bound', 'explicit project')
        value = native.ide_conversation(a, 'same-topic', 20)
        assert [v['operation_id'] for v in value['messages']] == ['bound']
        assert value['coverage']['scope_filter'] == 'EXPLICIT_PROJECT_ONLY'
        assert value['coverage']['unbound_messages_included'] is False
        assert 'unbound historical private material' not in str(value)
        with pytest.raises(Conflict): native.ide_operation(a, 'same-topic', 'legacy-unbound')


def test_profile_choice_is_not_guessed_from_mutable_record_mtime(tmp_path):
    app, native, a, _ = scene(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        profiles = [{'id': 'profile-' + str(i), 'revision': 1, 'sha256': str(i) * 64} for i in (1, 2)]
        for index, profile in enumerate(profiles):
            op = 'message-' + str(index)
            send(client, a, 'topic', op, 'actual message')
            saved = native.ws._read(native.path(op), authority=False)
            saved['request']['provider_profile_ref'] = profile
            saved['request_sha256'] = module.digest(saved['request'])
            native.ws._write(native.path(op), saved)
        value = native.ide_conversation(a, 'topic', 20)
        assert value['latest_provider_profile_ref'] is None
        assert value['coverage']['order_basis'] == 'RECORD_MODIFIED_AT'
        assert {v['provider_profile_ref']['id'] for v in value['messages']} == {'profile-1', 'profile-2'}


def test_history_byte_budget_is_explicit_and_reads_do_not_dispatch_new_model_work(tmp_path):
    app, native, a, _ = scene(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        for i in range(5): send(client, a, 'topic', 'bounded-' + str(i), 'question')
        original_get = native.get
        native.get = lambda op: {**original_get(op), 'text': '中文' * 24000}
        try:
            value = native.ide_conversation(a, 'topic', 40)
        finally:
            native.get = original_get
        assert 0 < len(value['messages']) < 5
        assert value['coverage']['has_more'] and not value['coverage']['complete']
        assert value['coverage']['returned'] == len(value['messages'])
        assert sum(len(module.canonical(v).encode()) for v in value['messages']) <= 512 * 1024
        assert native.tasks == {} and native.transport.active == {}
