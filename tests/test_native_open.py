"""Real file authority/report bytes; resolving never launches a native application."""
import asyncio
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from packages.domain.planning_schema import ProposalInput

TOKEN = 'a' * 64
ROOT = '/api/desktop-host/native-open'


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', '123')
    settings = Settings(tmp_path / 'controller')
    app = create_app(settings)
    folder = tmp_path / '用户原件'; folder.mkdir()
    (folder / '材料.md').write_text('现有需求，需要按实际输入核对。', encoding='utf-8')
    with TestClient(app, base_url=settings.origin) as client:
        csrf = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}).json()['csrf_token']
        client.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': csrf, 'X-EEAS-Desktop-Host': TOKEN})
        pid = client.post('/api/projects', json={'name': '原生打开验证'}).json()['id']
        binding = client.post(f'/api/projects/{pid}/local-workspaces', json={
            'idempotency_key': 'folder', 'root': str(folder), 'allowed_write_paths': ['*']}).json()
        base = f'/api/projects/{pid}/local-workspaces/{binding["id"]}'
        source = client.get(base + '/file', params={'path': '材料.md'}).json()['source_ref']
        yield client, pid, folder, base, source, settings


def test_exact_file_version_is_resolved_and_human_drift_rejected(scene):
    client, pid, folder, _, source, _ = scene
    body = {'project_id': pid, 'file_source_ref': source}
    resolved = client.post(ROOT + '/file', json=body)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['native_path'] == str(folder / '材料.md')
    assert resolved.json()['active_document_identity_verified'] is False
    (folder / '材料.md').write_text('人工已修改，不可以默默换成新版本。', encoding='utf-8')
    assert client.post(ROOT + '/file', json=body).status_code == 409


def report(scene):
    client, pid, _, base, _, _ = scene
    material = client.post(base + '/materials', json={'operation_id': 'material', 'paths': ['材料.md']}).json()
    body = ProposalInput.model_validate({'operation_id': 'plan', 'mode': 'manual', 'prompt': '核对资料，拆解任务',
        'materials': [material['materials'][0]['document_ref']],
        'candidate': {'title': '可核对计划', 'summary': '按已有资料拆解', 'nodes': [
            {'id': 'review', 'title': '资料核对', 'description': '核对资料与缺口', 'inputs': ['已有材料'],
                'outputs': ['核对结果'], 'acceptance': ['来源可核对']}]}})
    value = asyncio.run(client.app.state.planning_proposals.create(pid, body))
    response = client.post(f'/api/projects/{pid}/planning/proposals/{value["id"]}/reports',
        json={'operation_id': 'word', 'expected_revision': 1})
    assert response.status_code in {200, 201}, response.text
    return response.json()['artifact']


def test_report_working_copy_preserves_immutable_source_and_subsequent_human_edits(scene):
    client, pid, _, _, _, _ = scene
    artifact = report(scene)
    body = {'project_id': pid, 'artifact_id': artifact['id'], 'sha256': artifact['sha256'],
        'download_url': artifact['download_url']}
    original = client.get(artifact['download_url']).content
    first = client.post(ROOT + '/report', json=body)
    assert first.status_code == 200, first.text
    path = Path(first.json()['native_path'])
    assert first.json()['created'] is True and path.read_bytes() == original
    # Exact current working-copy bytes survive another click; never recopy over them.
    path.write_bytes(original + b'human edit marker')
    again = client.post(ROOT + '/report', json=body)
    assert again.status_code == 200, again.text
    assert again.json()['working_copy_modified'] is True
    assert again.json()['created'] is False
    assert path.read_bytes().endswith(b'human edit marker')
    assert client.get(artifact['download_url']).content == original
    assert sha256(original).hexdigest() == artifact['sha256']


@pytest.mark.parametrize('url', ['https://example.com/a.docx', 'file:///D:/secret.docx',
    '/api/projects/another/planning/proposals/id/reports/' + 'b' * 64 + '/download'])
def test_report_links_cannot_select_arbitrary_network_files_or_another_project(scene, url):
    client, pid, *_ = scene
    response = client.post(ROOT + '/report', json={'project_id': pid, 'artifact_id': 'b' * 64,
        'sha256': 'c' * 64, 'download_url': url})
    assert response.status_code == 409


def test_private_native_open_requires_host_authority(scene):
    client, pid, _, _, source, _ = scene
    client.headers.pop('X-EEAS-Desktop-Host')
    assert client.post(ROOT + '/file', json={'project_id': pid, 'file_source_ref': source}).status_code == 409
