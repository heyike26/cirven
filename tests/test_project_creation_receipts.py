"""Actual project/SQLite transactions and folder binding recovery; no GUI claim."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

from fastapi.testclient import TestClient
import pytest

from apps.api.main import create_app
from apps.api.config import Settings
from packages.domain.store import Store, Conflict


def check(value, message='project receipt acceptance failed'):
    if not value:
        raise AssertionError(message)


def test_project_create_is_atomic_under_duplicate_races_and_recovers_exact_receipt_after_restart(tmp_path):
    db = tmp_path/'projects.sqlite'
    store = Store(db)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: store.create_project('原创项目😀', '', operation_id='create-once'), range(8)))
    check(len({item['id'] for item in results}) == 1)
    check(len(store.list_projects()) == 1)
    reopened = Store(db)
    check(reopened.project_operation('create-once')['project'] == results[0])
    with pytest.raises(Conflict):
        reopened.create_project('不同项目', '', operation_id='create-once')
    check(len(reopened.list_projects()) == 1)
    with reopened.connect() as connection:
        connection.execute("UPDATE project_operations SET request_sha256=?", ('f'*64,))
    with pytest.raises(Conflict):
        reopened.project_operation('create-once')


def test_real_api_unknown_project_and_workspace_binding_reads_never_create_or_replay(tmp_path):
    settings = Settings(data_dir=tmp_path/'data', port=8787, worker_enabled=False)
    app = create_app(settings)
    folder = tmp_path/'working';folder.mkdir()
    with TestClient(app, base_url='http://127.0.0.1:8787') as client:
        token = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap':'1'}).json()['csrf_token']
        client.headers.update({'Origin':settings.origin,'X-EEAS-CSRF':token})
        check(client.get('/api/project-operations/not-observed').status_code == 404)
        body = {'name':'新目录工程', 'description':'', 'operation_id':'folder-project'}
        first = client.post('/api/projects', json=body)
        check(first.status_code == 201, first.text)
        original = first.json();pid = original['id']
        recovered = client.get('/api/project-operations/folder-project').json()
        check(recovered['project'] == original)
        expected = hashlib.sha256(json.dumps({'name':body['name'],'description':''}, ensure_ascii=False,
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        check(recovered['request_sha256'] == expected)
        check(client.post('/api/projects',json=body).json() == original)
        check(client.post('/api/projects',json={**body,'name':'另一个工程'}).status_code == 409)
        root = f'/api/projects/{pid}'
        check(client.get(root+'/local-workspace-operations/not-bound').status_code == 404)
        binding = client.post(root+'/local-workspaces',json={'idempotency_key':'bind-folder', 'root':str(folder),
            'allowed_write_paths':[]})
        check(binding.status_code == 201, binding.text)
        check(client.get(root+'/local-workspace-operations/bind-folder').json()['workspace'] == binding.json())
        foreign = client.post('/api/projects',json={'name':'其他工程'}).json()['id']
        check(client.get(f'/api/projects/{foreign}/local-workspace-operations/bind-folder').status_code == 404)
        check(len(client.get('/api/projects').json()['projects']) == 2)
    with TestClient(create_app(settings),base_url=settings.origin) as client:
        token=client.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'}).json()['csrf_token']
        client.headers.update({'Origin':settings.origin,'X-EEAS-CSRF':token})
        check(client.get('/api/project-operations/folder-project').json()['project'] == original)
        check(client.get(root+'/local-workspace-operations/bind-folder').json()['workspace'] == binding.json())
