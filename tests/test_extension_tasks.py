import json
import time

from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from test_documents import project
from test_extensions import archive
from test_tasks import inputs
from test_worker import submitted, until


def import_and_activate(client, version):
    candidate = client.post('/api/extensions', files={'file': (f'original-v{version}.zip', archive(version), 'application/zip')})
    assert candidate.status_code == 201, candidate.text
    sid = candidate.json()['id']
    assert client.post(f'/api/extensions/{sid}/qualify').json()['loaded']
    review = client.post(f'/api/extensions/{sid}/review').json()
    approved = client.post(f"/api/extension-reviews/{review['id']}/approve", json={
        'action_hash': review['action_hash'], 'permissions': review['action']['permissions']})
    assert approved.status_code == 200, approved.text
    return sid


def test_running_task_freezes_v1_while_v2_is_activated_then_calls_v2(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    v1 = import_and_activate(client, 1)
    assert client.post(f'/api/projects/{pid}/extension-binding', json={'snapshot_id': v1}).status_code == 200
    first_task, first = submitted(client, pid, {**body, 'mock_behavior': 'wait_for_cancel', 'timeout_seconds': 30,
                                                'approval_required': True}, 'pinned-v1')
    frozen_v1 = first_task['spec']['stack_lock_hash']
    approval = client.get(f'/api/projects/{pid}/approvals').json()['approvals'][0]
    service = TaskService(tmp_path / 'eeas.sqlite3', ROOT)
    worker = Worker(service, tmp_path)
    assert client.post(f"/api/projects/{pid}/approvals/{approval['id']}/decision", json={
        'action_hash': approval['action_hash'], 'approve': True}).status_code == 200
    assert worker.start()
    try:
        deadline = time.monotonic() + 8
        while not service.run(pid, first['id'])['runner_job_id'] and time.monotonic() < deadline:
            time.sleep(.03)
        assert service.run(pid, first['id'])['runner_job_id']
        v2 = import_and_activate(client, 2)
        assert client.post(f'/api/projects/{pid}/extension-binding', json={'snapshot_id': v2}).status_code == 200
        assert service.task(pid, first_task['id'])['review_status'] == 'CURRENT'
        assert service.run(pid, first['id'])['stack_lock_hash'] == frozen_v1
        second_task, second = submitted(client, pid, {**body, 'goal': 'new-v2-task'}, 'pinned-v2')
        assert second_task['spec']['stack_lock_hash'] != frozen_v1
        client.post(f"/api/projects/{pid}/runs/{first['id']}/cancel")
        assert until(service, pid, first['id'], {'CANCELLED'})['execution_status'] == 'CANCELLED'
        result = until(service, pid, second['id'], {'RETURNED', 'FAILED'})
        assert result['execution_status'] == 'RETURNED', result
        old_call = next(e['payload'] for e in service.events(pid, first['id']) if e['payload']['type'] == 'extension_result')
        new_call = next(e['payload'] for e in service.events(pid, second['id']) if e['payload']['type'] == 'extension_result')
        assert old_call['snapshot_id'] == v1 and old_call['result'] == 'v1|' + body['goal']
        assert new_call['snapshot_id'] == v2 and new_call['result'] == {'format': 'v2', 'text': 'new-v2-task', 'length': 11}
        evidence = client.post(f"/api/projects/{pid}/runs/{second['id']}/verify").json()
        assert evidence['payload']['result'] == 'PASSED'
        assert evidence['payload']['stack_lock_hash'] == second_task['spec']['stack_lock_hash']
        # Stack composition stays in the immutable frozen task, never in mutable current pointers.
        actual = json.loads(service.run(pid, second['id'])['frozen_inputs'])['spec']
        assert actual['extension_lock']['snapshot_id'] == v2
    finally:
        worker.stop()


def test_changed_pinned_snapshot_invalidates_task_instead_of_loading_new_bytes(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    v1 = import_and_activate(client, 1)
    client.post(f'/api/projects/{pid}/extension-binding', json={'snapshot_id': v1})
    task = client.post(f'/api/projects/{pid}/tasks', json=body).json()
    (tmp_path / 'extensions' / v1 / 'plugin.py').write_text("raise RuntimeError('tampered')")
    assert client.get(f"/api/projects/{pid}/tasks/{task['id']}").json()['review_status'] == 'NEEDS_REVIEW'
    response = client.post(f"/api/projects/{pid}/tasks/{task['id']}/runs", json={'revision': 1, 'operation_id': 'changed-stack'})
    assert response.status_code == 409
    assert not (tmp_path / 'extension-journal').joinpath('BAD').exists()
