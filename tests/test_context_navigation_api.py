"""Original ASGI/context/file services; authored, not executed this batch.

The process-lifetime fixture supplies no window, input or model. Resolving a
native target is not an OS-open request or Windows acceptance.
"""
from pathlib import Path

import pytest

from test_conversation_directory import api


@pytest.fixture
def linked(api, tmp_path):
    pid = api.post('/api/projects', json={'name': 'File-discussion API'}).json()['id']
    folder = tmp_path / 'explicit-user-folder'
    folder.mkdir()
    path = folder / 'manual.md'
    path.write_text('ORIGINAL_USER_DATA_MUST_SURVIVE')
    bound = api.post(f'/api/projects/{pid}/local-workspaces', json={
        'idempotency_key': 'original-folder', 'root': str(folder), 'allowed_write_paths': []}).json()
    source = api.get(f'/api/projects/{pid}/local-workspaces/{bound["id"]}/file?path=manual.md').json()['source_ref']
    created = api.post('/api/conversations', json={
        'operation_id': 'navigation_conversation', 'project_id': pid, 'title': 'Related conversation'})
    assert created.status_code == 200, created.text
    cid = created.json()['conversation_ref']['id']
    response = api.put('/api/context/scopes/project', json={'operation_id': 'scope', 'expected_revision': 0,
        'project_id': pid, 'parent_id': None, 'kind': 'PROJECT', 'label': 'Project sources', 'policy': {}})
    assert response.status_code == 200, response.text
    target = {'kind': 'WORKSPACE_FILE', 'source_ref': source}
    prepared = api.post('/api/context/files/prepare-source', json=target)
    assert prepared.status_code == 200, prepared.text
    context_source = prepared.json()['source_ref']
    edge = api.post('/api/context/associations', json={'operation_id': 'associate', 'expected_revision': 0,
        'scope_id': 'project', 'source': context_source, 'conversation_id': cid, 'state': 'ACTIVE'})
    assert edge.status_code == 200, edge.text
    return {'api': api, 'pid': pid, 'cid': cid, 'path': path, 'target': target, 'source': context_source}


def test_api_bidirectional_navigation_exposes_refs_without_native_paths_or_new_execution(linked):
    s = linked
    related = s['api'].post('/api/context/files/related', json=s['target'])
    assert related.status_code == 200, related.text
    assert related.json()['model_calls'] == 0 and related.json()['execution_authorized'] is False
    row = next(item for item in related.json()['items'] if item['conversation_id'] == s['cid'])
    assert row['discussion']['available'] is True
    files = s['api'].get('/api/context/conversations/' + s['cid'] + '/files')
    assert files.status_code == 200, files.text
    assert files.json()['items'][0]['source_ref'] == s['source']
    target = s['api'].post('/api/context/files/open-target', json={'source': s['source']})
    assert target.status_code == 200, target.text
    assert 'native_path' not in target.json() and target.json()['native_open_requested'] is False
    assert s['path'].read_text() == 'ORIGINAL_USER_DATA_MUST_SURVIVE'


def test_context_native_resolver_requires_host_and_returns_exact_original_identity_without_os_open(linked):
    s = linked
    denied = s['api'].post('/api/desktop-host/native-open/context-file', json={'source': s['source']},
        headers={'X-EEAS-Desktop-Host': ''})
    assert denied.status_code == 409 and denied.json()['error']['message'] == 'A trusted desktop main process is required'
    assert 'native_path' not in denied.text
    result = s['api'].post('/api/desktop-host/native-open/context-file', json={'source': s['source']})
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert receipt['status'] == 'RESOLVED' and receipt['context_source_ref'] == s['source']
    assert receipt['context_file_key'] == s['source']['file_identity_sha256']
    assert receipt['file_write_authorized'] is False and receipt['active_document_identity_verified'] is False
    assert Path(receipt['native_path']).name == s['path'].name
    assert s['path'].read_text() == 'ORIGINAL_USER_DATA_MUST_SURVIVE'


def test_navigation_requests_cannot_inject_paths_or_replace_an_indexed_file_identity(linked):
    s = linked
    forged = {**s['source'], 'file_identity_sha256': 'f' * 64}
    result = s['api'].post('/api/context/files/open-target', json={'source': forged})
    assert result.status_code in (404, 409), result.text
    arbitrary = s['api'].post('/api/desktop-host/native-open/context-file',
        json={'source': s['source'], 'native_path': str(s['path'])})
    assert arbitrary.status_code == 422
    extra_path = s['api'].post('/api/context/files/related', json={**s['target'], 'path': '../other'})
    assert extra_path.status_code == 422
    assert s['path'].read_text() == 'ORIGINAL_USER_DATA_MUST_SURVIVE'
