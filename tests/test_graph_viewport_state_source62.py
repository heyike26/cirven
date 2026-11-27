"""Actual WorkbenchState HTTP/SQLite: optional graph viewport, old records, restart."""
from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app


def check(value, note='graph viewport state acceptance failed'):
    if not value:
        raise AssertionError(note)


def client_at(directory):
    settings = Settings(directory)
    app = create_app(settings)
    client = TestClient(app, base_url=settings.origin)
    bootstrap = client.get('/api/bootstrap', headers={'x-eeas-bootstrap': '1'})
    client.headers.update({'Origin': settings.origin, 'x-eeas-csrf': bootstrap.json()['csrf_token']})
    return app.state.store, client


def scene(directory):
    store, client = client_at(directory)
    pid = store.create_project('原创62图视图', '无模型、无业务执行')['id']
    key = {'project_id': pid, 'kind': 'proposal', 'object_id': 'original-graph', 'revision': 3, 'sha256': 'a' * 64}
    view = {'zoom': 1.25, 'pan': {'x': -24.5, 'y': 19}, 'positions': {'A': {'x': 140, 'y': 72}}, 'selected': ['A'], 'mode': 'flow-down', 'search': '中文任务'}
    body = {'key': key, 'operation_id': 'old-six-field-view', 'expected_revision': 0, 'fields': {'changeReason': '尚未提交的原创说明'}, 'view': {'graphs': {'task-network:readable-v2': view}}, 'dirty': True}
    return store, client, pid, key, body


def test_old_six_fields_and_new_viewport_survive_restart_exact_key_and_original_receipt(tmp_path):
    data = tmp_path / 'data'
    store, client, pid, key, body = scene(data)
    path = f'/api/projects/{pid}/workbench-state'
    old = client.post(path + '/save', json=body)
    check(old.status_code == 200, old.text)
    newer = deepcopy(body)
    newer.update(operation_id='new-sized-view', expected_revision=1)
    newer['view']['graphs']['task-network:readable-v2']['viewport'] = {'width': 1298.5, 'height': 640}
    saved = client.post(path + '/save', json=newer)
    check(saved.status_code == 200, saved.text)
    current = saved.json()
    client.close()
    store, restarted = client_at(data)
    try:
        loaded = restarted.post(path + '/load', json={'key': key})
        check(loaded.status_code == 200 and loaded.json()['view'] == newer['view'])
        check(loaded.json()['fields'] == body['fields'])
        receipt = restarted.get(path + '/operations/old-six-field-view')
        check(receipt.status_code == 200 and receipt.json()['view'] == body['view'])
        check('viewport' not in receipt.json()['view']['graphs']['task-network:readable-v2'])
        check(restarted.post(path + '/save', json=newer).json()['sha256'] == current['sha256'])
        new_key = {**key, 'revision': 4, 'sha256': 'b' * 64}
        empty = restarted.post(path + '/load', json={'key': new_key})
        check(empty.status_code == 200 and empty.json()['status'] == 'EMPTY' and empty.json()['view'] == {})
        with store.connect() as db:
            check(db.execute('SELECT count(*) FROM task_runs').fetchone()[0] == 0)
        (tmp_path / 'STATE_RECEIPT.json').write_text(json.dumps({'old': old.json(), 'saved': current, 'restarted': loaded.json(), 'old_operation': receipt.json(), 'new_scope': empty.json()}, ensure_ascii=False, indent=2))
    finally:
        restarted.close()


def test_graph_viewport_bad_types_extra_keys_and_numeric_limits_reject_without_advancing_state(tmp_path):
    store, client, pid, key, body = scene(tmp_path / 'data')
    path = f'/api/projects/{pid}/workbench-state'
    try:
        check(client.post(path + '/save', json=body).status_code == 200)
        invalid = [None, {}, {'width': 684}, {'width': 684, 'height': 480, 'extra': 1}, {'width': True, 'height': 480}, {'width': 684, 'height': '480'}, {'width': 0, 'height': 480}, {'width': -1, 'height': 480}, {'width': 100000.1, 'height': 480}, {'width': 684, 'height': False}]
        outcomes = []
        for index, viewport in enumerate(invalid):
            request = deepcopy(body)
            request.update(operation_id=f'invalid-{index}', expected_revision=1)
            request['view']['graphs']['task-network:readable-v2']['viewport'] = viewport
            response = client.post(path + '/save', json=request)
            check(response.status_code == 422, response.text)
            outcomes.append(response.json())
        for constant in ['NaN', 'Infinity', '-Infinity']:
            request = deepcopy(body)
            request.update(operation_id='invalid-constant', expected_revision=1)
            request['view']['graphs']['task-network:readable-v2']['viewport'] = {'width': '__NUMBER__', 'height': 480}
            raw = json.dumps(request).replace('"__NUMBER__"', constant)
            response = client.post(path + '/save', content=raw, headers={'content-type': 'application/json'})
            check(response.status_code == 422)
        request = deepcopy(body)
        request.update(operation_id='root-viewport', expected_revision=1)
        request['view']['viewport'] = {'width': 684, 'height': 480}
        check(client.post(path + '/save', json=request).status_code == 422)
        actual = client.post(path + '/load', json={'key': key}).json()
        check(actual['state_revision'] == 1 and actual['view'] == body['view'])
        with store.connect() as db:
            check(db.execute('SELECT count(*) FROM workbench_state_operations').fetchone()[0] == 1)
        (tmp_path / 'REJECTION_RECEIPT.json').write_text(json.dumps({'invalid': outcomes, 'unchanged': actual}, ensure_ascii=False, indent=2))
    finally:
        client.close()
