"""Actual global native-message GET and ordinary projection, no model or GUI."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from apps.api.native_messages import NativeQuestionInput
from packages.agent_sessions.native_message import NativeMessageSessions
from packages.domain.local_workspaces import digest
from test_native_messages import application, TOKEN


def check(value, message='global native inspection contract failed'):
    if not value:
        raise AssertionError(message)


def test_global_original_get_projects_exact_hash_without_project_or_model(tmp_path):
    app, native, _ = application(tmp_path)
    body = {'operation_id': 'global-original-inspect', 'raw_text': '检查这个原问题。',
        'project_id': None, 'conversation_id': 'global-discussion', 'provider_profile_ref': None}
    normalized = NativeQuestionInput.model_validate(body).request_dict()
    with TestClient(app, headers={'X-EEAS-Desktop-Host': TOKEN}) as client:
        first = client.post('/api/desktop-host/native-messages', json=body)
        check(first.status_code == 200, first.text)
        check(first.json()['status'] == 'NOT_CONFIGURED')
        path = native.path(body['operation_id'])
        before = path.read_bytes()
        saved = native.ws._read(path, authority=True)
        check(saved['request'] == normalized and saved['request_sha256'] == digest(normalized))
        inspected = client.get('/api/desktop-host/native-messages/' + body['operation_id'])
        check(inspected.status_code == 200, inspected.text)
        value = inspected.json()
        check(value['operation_id'] == body['operation_id'] and value['project_id'] is None)
        check(value['conversation_id'] == body['conversation_id'])
        check(value['request_sha256'] == saved['request_sha256'])
        check('provider_profile_ref' in value and value['provider_profile_ref'] is None)
        check(value == first.json())
        coordinator = NativeMessageSessions(native, SimpleNamespace())
        ordinary = coordinator.project(saved, value)
        check(ordinary['request_sha256'] == saved['request_sha256'])
        check('provider_profile_ref' in ordinary and ordinary['provider_profile_ref'] is None)
        check(ordinary['project_id'] is None and ordinary['conversation_id'] == body['conversation_id'])
        check(path.read_bytes() == before)
        check(native.tasks == {} and native.transport.active == {})
        denied = client.get('/api/desktop-host/native-messages/' + body['operation_id'],
            headers={'X-EEAS-Desktop-Host': 'wrong'})
        check(denied.status_code == 409 and path.read_bytes() == before)
