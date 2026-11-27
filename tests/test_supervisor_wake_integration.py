"""Actual app/HTTP/SQLite/owner commits; no model, native GUI or fake owners."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from packages.domain.tasks import digest, now
from test_native_messages import application as native_application, TOKEN


def check(value, message="wake integration acceptance failed"):
    if not value: raise AssertionError(message)


def connect(client):
    response = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
    check(response.status_code == 200)
    client.headers.update({'Origin': 'http://127.0.0.1:8787', 'X-EEAS-CSRF': response.json()['csrf_token']})


def until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline: raise AssertionError('actual API waiter did not register')
        time.sleep(.005)


def durable_terminal_session(service, pid, ident, operation):
    """Original test records only; deliberately no live process/active context."""
    service._write(service._path(pid, ident, 'manifest.json'),
                   {'id': ident, 'project_id': pid, 'request': {}, 'title': '原创接线夹具'})
    service._write(service._path(pid, ident, 'state.json'), {
        'generation': 1, 'active_operation_id': None, 'native_status': 'idle',
        'pending_questions': [], 'pending_permissions': [], 'observation_at': now(), 'messages': []})
    service._write(service._path(pid, ident, 'operations', digest(operation) + '.json'), {
        'id': operation, 'kind': 'MESSAGE', 'status': 'FAILED',
        'operation_ref': {'id': operation, 'request_sha256': 'a' * 64},
        'request': {'generation': 1, 'raw_text': '原创数据，未运行模型'}})


def test_real_changes_long_get_wakes_after_owner_commit_rereads_scope_and_close_releases_watch(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / 'app', port=8787, worker_enabled=False))
    owner, supervisor = app.state.agent_sessions, app.state.global_supervisor
    project = app.state.store.create_project('工程甲', '')['id']
    other = app.state.store.create_project('工程乙', '')['id']
    durable_terminal_session(owner, project, 'a' * 32, 'owned-op')
    durable_terminal_session(owner, other, 'b' * 32, 'other-op')
    supervisor.update_settings(enabled=True, interval_seconds=3600, expected_revision=1)
    with TestClient(app, base_url='http://127.0.0.1:8787') as client:
        connect(client)
        before = client.post('/api/supervisor/check')
        check(before.status_code == 200, before.text)
        before = before.json()
        check(before['settings']['interval_seconds'] == 3600 and before['model_calls'] == 0)
        check(len(before['tasks']) == 2 and not owner.active)
        cursor = before['change_sequence']
        with ThreadPoolExecutor(1) as pool:
            response = pool.submit(client.get, f'/api/supervisor/changes?after={cursor}&timeout=5')
            until(lambda: bool(supervisor._change_waiters))
            began = time.monotonic()
            # This real owner method writes its own durable operation first,
            # then invokes the configured dirty callback. No direct wake call.
            owner._finish(project, 'a' * 32, 'owned-op', 'RETURNED')
            changed = response.result(timeout=6)
            check(changed.status_code == 200 and changed.json()['change_sequence'] > cursor)
            check(time.monotonic() - began < 5, 'must not wait for the configured 3600s fallback')
            current = client.get('/api/supervisor').json()
            tasks = {item['key']['project_id']: item for item in current['tasks']}
            check(tasks[project]['source_status'] == 'RETURNED')
            check(tasks[project]['classification'] == 'EXECUTION_RETURNED')
            check(tasks[project]['validation_status'] == 'NOT_RUN')
            check(tasks[other]['source_status'] == 'FAILED' and tasks[other]['classification'] == 'EXECUTION_ERROR')
            check(current['source_coverage']['model_calls'] == 0 and not owner.active)
            cursor = current['change_sequence']
            response = pool.submit(client.get, f'/api/supervisor/changes?after={cursor}&timeout=25')
            until(lambda: bool(supervisor._change_waiters))
            client.portal.call(supervisor.close)
            closed = response.result(timeout=4)
            check(closed.status_code == 200 and closed.json()['closed'] is True)
            check(closed.json()['change_sequence'] > cursor and not supervisor._change_waiters)
    check(owner.closed and not app.state.supervisor_collector._reads)


def test_observer_wake_failure_does_not_change_real_committed_mutation_response(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path / 'app', port=8787, worker_enabled=False))
    calls = []
    def failure(reason, source_key=None):
        calls.append(reason)
        raise RuntimeError('original observer fault fixture')
    with TestClient(app, base_url='http://127.0.0.1:8787') as client:
        connect(client)
        monkeypatch.setattr(app.state.global_supervisor, 'wake', failure)
        response = client.post('/api/projects', json={'name': '已写入工程', 'description': '提醒故障不改变结果'})
        check(response.status_code == 201, response.text)
        actual = client.get('/api/projects/' + response.json()['id'])
        check(actual.status_code == 200 and actual.json()['name'] == '已写入工程')
        check(calls == ['OWNER_CHANGED'])


@pytest.mark.parametrize('status', ['RETURNED', 'FAILED', 'MISSING_EXECUTION'])
def test_native_actual_terminal_transport_finally_retains_result_and_removes_task_on_wake_failure(tmp_path, status):
    app, service, _profile = native_application(tmp_path)
    store = app.state.store
    pid = store.create_project('原页面反馈', '')['id']
    calls = []
    def failure(reason):
        calls.append(reason)
        raise RuntimeError('observer fault only')
    app.state.global_supervisor = SimpleNamespace(wake=failure)
    eid = 'actual-terminal-record'
    if status != 'MISSING_EXECUTION':
        # The genuine transport inspects a genuine durable terminal execution;
        # its terminal guard returns without starting CLI/model or reading auth.
        request = {}
        with store.connect() as connection:
            connection.execute('''INSERT INTO agent_executions
                (id,project_id,operation_id,request_hash,runtime,workspace,execution_status,verification_status,
                 session_id,model_profile,sdk_version,request,result,created_at,ended_at,cancel_requested)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (eid, pid, 'native-finally', digest(request), service.transport.runtime, 'fixture-unused',
                 status, 'NOT_RUN', None, 'unused-no-model', '1.19.0', '{}', None, now(), now(), 0))
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        response = client.post('/api/desktop-host/native-messages',
                               json={'operation_id': 'native-finally', 'raw_text': '原创关闭清理夹具'})
        check(response.status_code == 200 and response.json()['status'] == 'NOT_CONFIGURED')
        async def exercise():
            # Retain the actual running Task in the same owner map used by submit.
            # Storage/transport/Task are real; only wake is a deliberate fault.
            task = asyncio.create_task(service.run('native-finally', {'project_id': pid, 'id': eid}))
            service.tasks['native-finally'] = task
            await task
            check(task.done() and task.exception() is None and not service.tasks)
            actual = service.ws._read(service.path('native-finally'), authority=True)
            check(actual['status'] == ('UNKNOWN_OUTCOME' if status == 'MISSING_EXECUTION' else status))
            check(not service.transport.active and not service.transport._owners)
        client.portal.call(exercise)
        check(calls == ['OWNER_CHANGED'])
    check(service.closed and not service.tasks)
