"""Actual DAG, old approval/knowledge/Verifier, recovery and migration cases."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from packages.domain.harnesses import HarnessInput, HarnessService, HarnessStart, topological_order
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from test_project_intake import project, make
from test_tasks import inputs
from test_taskbooks import draft, book, book_ref, snapshot
from test_project_questions import setup, ask, answer, no_runtime
from test_question_decisions import exact_task, resolution_body, resolve, decision_body, ready
from test_worker import until


def node(task, id='first'):
    return {'node_id': id, 'task_ref': exact_task(task), 'entry_ids': ['goal-entry'],
            'acceptance_entry_ids': ['acceptance-entry'], 'role': 'CUSTOM_IMPLEMENTER',
            'tool_ids': task['spec']['required_capabilities'],
            'expected_outputs': task['spec']['expected_artifacts'], 'checkpoint_ids': ['actual-verifier']}


def prepared(client, two=False, approval=False):
    pid = project(client, '原创通用任务链')
    doc, _, task_body = inputs(client, pid)
    task_body = {**task_body, 'approval_required': approval}
    task = client.post(f'/api/projects/{pid}/tasks', json=task_body).json()
    source_ref = {'document_id': doc['id'], 'revision': doc['revision'], 'sha256': doc['sha256']}
    intake = make(client, pid, [source_ref]).json()
    book_body = draft(intake)
    value = book(client, pid, book_body)
    nodes = [node(task)]
    second = None
    if two:
        second = client.post(f'/api/projects/{pid}/tasks', json={**task_body, 'goal': '真实后继候选',
                             'approval_required': False, 'dependencies': [task['id']]}).json()
        nodes.append(node(second, 'second'))
    body = {'idempotency_key': 'harness', 'expected_revision': 0, 'taskbook_ref': book_ref(value),
            'title': '从精确文稿生成的真实TaskSpec链', 'nodes': nodes}
    return pid, doc, intake, value, book_body, task, second, task_body, body


def harness(client, pid, body, id=None):
    route = f'/api/projects/{pid}/harnesses'
    response = client.post(route, json=body) if id is None else client.put(route+'/'+id, json=body)
    assert response.status_code == (201 if id is None else 200), response.text
    return response.json()


def start_body(value, key='user-start'):
    return {'idempotency_key': key, 'expected_revision': value['revision'], 'harness_sha256': value['sha256'],
            'intent': 'USER_START', 'user_ready': True, 'scope_reason': '用户明确启动所选具体节点；原执行批准和验收仍独立。'}


def start(client, pid, value, id='first', body=None):
    return client.post(f'/api/projects/{pid}/harnesses/{value["id"]}/nodes/{id}/runs', json=body or start_body(value))


def test_actual_taskbook_harness_dag_budget_refs_replay_revision_and_restart(client, tmp_path):
    pid, _, _, book_value, _, task, second, _, body = prepared(client, two=True)
    value = harness(client, pid, body)
    assert value['payload']['topological_order'] == ['first', 'second']
    assert value['payload']['nodes'][1]['dependency_node_ids'] == ['first']
    assert value['payload']['taskbook_ref'] == book_ref(book_value)
    for actual, original in zip(value['payload']['nodes'], [task, second]):
        assert actual['budget'] == original['spec']['budget']
        assert actual['acceptance_ref'] == original['spec']['acceptance_ref']
        assert actual['acceptance_hash'] == original['spec']['acceptance_hash']
        assert actual['expected_outputs'] == original['spec']['expected_artifacts']
        assert actual['role_application'] == 'DECLARED_TASK_MAPPING'
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}
    assert harness(client, pid, body) == value
    newer = harness(client, pid, {**body, 'idempotency_key': 'harness-edited', 'expected_revision': 1,
                                 'title': '只追加任务链修订'}, value['id'])
    assert newer['revision'] == 2
    assert harness(client, pid, body) == value
    old = client.get(f'/api/projects/{pid}/harnesses/{value["id"]}?revision=1').json()
    assert old['payload'] == value['payload'] and old['review_status'] == 'NEEDS_REVIEW'
    reopened = HarnessService(TaskService(tmp_path/'eeas.sqlite3', Path(__file__).resolve().parents[1]))
    assert reopened.get(pid, value['id'])['payload'] == newer['payload']
    snapshot(tmp_path, 'actual-taskbook-dag', {'source_kind': 'synthetic', 'taskbook': book_value,
             'tasks': [task, second], 'request': body, 'harness': value, 'current': newer}, client.app.state.store)


def test_exact_user_start_retains_real_approval_and_independent_dependency_verifier(client, tmp_path):
    pid, _, _, _, _, task, _, _, body = prepared(client, two=True, approval=True)
    value = harness(client, pid, body)
    state = client.get(f'/api/projects/{pid}/harnesses/{value["id"]}/state').json()
    assert [n['status'] for n in state['nodes']] == ['READY', 'BLOCKED_ON_DEPENDENCY']
    assert start(client, pid, value, 'second').status_code == 409
    first = start(client, pid, value); assert first.status_code == 202, first.text
    receipt = first.json(); run = receipt['run']
    assert run['execution_status'] == 'AWAITING_APPROVAL'
    assert json.loads(run['frozen_inputs'])['spec']['budget'] == task['spec']['budget']
    assert start(client, pid, value).json() == receipt
    assert start(client, pid, value, body=start_body(value, 'duplicate-active')).status_code == 409
    approval = next(a for a in client.get(f'/api/projects/{pid}/approvals').json()['approvals'] if a['run_id'] == run['id'])
    store = client.app.state.store; worker = Worker(store, tmp_path); worker.verifier = client.app.state.verifier
    assert worker.start()
    try:
        assert store.run(pid, run['id'])['execution_status'] == 'AWAITING_APPROVAL'
        result = client.post(f'/api/projects/{pid}/approvals/{approval["id"]}/decision',
                             json={'action_hash': approval['action_hash'], 'approve': True})
        assert result.status_code == 200
        returned = until(store, pid, run['id'], {'RETURNED', 'FAILED'})
        assert returned['execution_status'] == 'RETURNED' and returned['verification_status'] == 'NOT_RUN'
        assert start(client, pid, value, 'second', start_body(value, 'not-verified')).status_code == 409
        proof = client.app.state.verifier.verify(pid, run['id'])
        assert proof['payload']['result'] == 'PASSED'
        following = start(client, pid, value, 'second', start_body(value, 'verified-second'))
        assert following.status_code == 202, following.text
        last = until(store, pid, following.json()['run_id'], {'RETURNED', 'FAILED'})
        assert last['execution_status'] == 'RETURNED' and last['verification_status'] == 'NOT_RUN'
        assert (tmp_path/last['workspace']/'candidate.txt').read_text() == '真实后继候选\n'
    finally:
        worker.stop()
    assert no_runtime(client) == {'task_runs': 2, 'runner_jobs': 2, 'agent_executions': 0}
    snapshot(tmp_path, 'actual-harness-approval-verifier', {'source_kind': 'synthetic', 'request': body,
             'harness': value, 'start': receipt, 'approval': approval, 'proof': proof, 'last': last,
             'company_model_calls': 0, 'native_windows': 'NOT_RUN'}, store)


def test_harness_user_ready_never_resolves_questions_or_accepts_answers_implicitly(client):
    pid, _, intake, qbody, task, _ = setup(client, task=True)
    q = ask(client, pid, qbody)
    value = book(client, pid, draft(intake))
    h = harness(client, pid, {'idempotency_key': 'h', 'expected_revision': 0, 'taskbook_ref': book_ref(value),
                            'title': '信息等待只作用到实际节点', 'nodes': [node(task)]})
    assert start(client, pid, h).status_code == 409
    _, a = answer(client, pid, q)
    assert start(client, pid, h).status_code == 409
    resolved = resolve(client, pid, resolution_body(q, task, intake, a))
    assert start(client, pid, h).status_code == 409
    ready(client, pid, decision_body(task, intake, [resolved]))
    actual = start(client, pid, h)
    assert actual.status_code == 202, actual.text
    assert actual.json()['knowledge']['start_decision']['decision_id']
    assert no_runtime(client) == {'task_runs': 1, 'runner_jobs': 0, 'agent_executions': 0}


@pytest.mark.parametrize('fault', ['tool', 'missing-tool', 'output', 'entry', 'acceptance', 'dependency', 'duplicate', 'source'])
def test_invalid_harness_mapping_is_atomic_and_never_starts_runtime(client, fault):
    pid, _, _, _, _, _, _, _, body = prepared(client, two=True)
    body = copy.deepcopy(body)
    if fault == 'tool':body['nodes'][0]['tool_ids'] = ['arbitrary-shell']
    elif fault == 'missing-tool':body['nodes'][0]['tool_ids'] = body['nodes'][0]['tool_ids'][:1]
    elif fault == 'output':body['nodes'][0]['expected_outputs'] = ['../escape.py']
    elif fault == 'entry':body['nodes'][0]['entry_ids'] = ['missing-entry']
    elif fault == 'acceptance':body['nodes'][0]['acceptance_entry_ids'] = ['goal-entry']
    elif fault == 'dependency':body['nodes'] = [body['nodes'][1]]
    elif fault == 'duplicate':body['nodes'].append(body['nodes'][0])
    else:body['taskbook_ref']['sha256'] = '0'*64
    r = client.post(f'/api/projects/{pid}/harnesses', json=body)
    assert r.status_code in (409, 422), r.text
    with client.app.state.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_harnesses').fetchone()[0] == 0
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}


def test_old_book_task_and_cross_project_refs_do_not_rebind_an_active_run(client):
    pid, _, _, value, book_body, task, _, _, body = prepared(client)
    h = harness(client, pid, body); receipt = start(client, pid, h).json()
    before = client.get(f'/api/projects/{pid}/runs/{receipt["run_id"]}').json()
    book(client, pid, {**book_body, 'expected_revision': 1, 'idempotency_key': 'changed-book', 'title': '用户已改任务书'}, value['id'])
    assert start(client, pid, h, body=start_body(h, 'new-scope-with-old-harness')).status_code == 409
    assert client.get(f'/api/projects/{pid}/harnesses/{h["id"]}').json()['review_status'] == 'NEEDS_REVIEW'
    assert client.get(f'/api/projects/{pid}/runs/{receipt["run_id"]}').json() == before
    other = project(client, '另一项目')
    assert client.get(f'/api/projects/{other}/harnesses/{h["id"]}').status_code == 404
    assert client.post(f'/api/projects/{other}/harnesses', json=body).status_code == 404
    assert start(client, pid, h).json() == receipt
    assert json.loads(before['frozen_inputs'])['spec']['task_id'] == task['id']


def test_actual_concurrent_harness_start_is_exactly_once_and_receipt_survives_restart(client, tmp_path):
    pid, _, _, _, _, _, _, _, body = prepared(client)
    value = harness(client, pid, body)
    service = client.app.state.harnesses
    caller = value['payload']['created_by']['id']
    model = HarnessStart.model_validate(start_body(value))
    with ThreadPoolExecutor(max_workers=5) as executor:
        receipts = list(executor.map(lambda _: service.start_node(pid, value['id'], 'first', model, caller), range(5)))
    assert all(r == receipts[0] for r in receipts)
    assert no_runtime(client) == {'task_runs': 1, 'runner_jobs': 0, 'agent_executions': 0}
    restarted = HarnessService(TaskService(tmp_path/'eeas.sqlite3', Path(__file__).resolve().parents[1]))
    assert restarted.start_node(pid, value['id'], 'first', model, caller) == receipts[0]
    with pytest.raises(Conflict, match='IDEMPOTENCY_CONFLICT'):
        restarted.start_node(pid, value['id'], 'first', HarnessStart.model_validate({**start_body(value), 'scope_reason': '同键换范围'}), caller)
    with client.app.state.store.connect() as connection, pytest.raises(sqlite3.IntegrityError, match='immutable'):
        connection.execute('DELETE FROM project_harness_node_starts')


def test_graph_rejects_cycles_unknown_dependencies_and_orders_independent_nodes():
    with pytest.raises(InvalidInput, match='cycle'):
        topological_order([{'node_id': 'A', 'dependency_node_ids': ['B']}, {'node_id': 'B', 'dependency_node_ids': ['A']}])
    with pytest.raises(InvalidInput, match='inside'):
        topological_order([{'node_id': 'A', 'dependency_node_ids': ['unknown']}])
    assert topological_order([{'node_id': 'B', 'dependency_node_ids': []}, {'node_id': 'A', 'dependency_node_ids': []}]) == ['A', 'B']


def test_existing_schema18_migrates19_and_missing14_source_is_rejected_without_changing_old_rows(tmp_path, monkeypatch, migrate_through):
    root = Path(__file__).resolve().parents[1]; database = tmp_path/'old18.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)')
        for path in sorted((root/'packages/domain/migrations').glob('*.sql')):
            if path.name[:3] > '018':continue
            connection.executescript(path.read_text()); connection.execute('INSERT INTO schema_migrations VALUES (?)', (path.name,))
        connection.execute('INSERT INTO projects VALUES (?,?,?,?)', ('old-project', '原十八版', '不可覆盖旧行', '2026-10-13'))
        old = connection.execute('SELECT * FROM projects').fetchall()
    migrate_through(database, 19, '019_project_harnesses.sql')
    store = TaskService(database, root)
    with store.connect() as connection:
        assert [tuple(r) for r in connection.execute('SELECT * FROM projects')] == old
        applied = [r[0] for r in connection.execute('SELECT version FROM schema_migrations ORDER BY version')]
        assert applied == [p.name for p in sorted((root/'packages/domain/migrations').glob('*.sql'))]
        assert connection.execute('SELECT count(*) FROM project_harnesses').fetchone()[0] == 0
    subset = tmp_path/'small-migration-source'; target = subset/'migrations'; target.mkdir(parents=True)
    for path in (root/'packages/domain/migrations').glob('*.sql'):
        if not path.name.startswith('014_'):shutil.copyfile(path, target/path.name)
    import packages.domain.store as module
    monkeypatch.setattr(module, '__file__', str(subset/'store.py'))
    with pytest.raises(Conflict, match='contiguous'):
        TaskService(database, root)
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT * FROM projects').fetchall() == old
        assert [r[0] for r in connection.execute('SELECT version FROM schema_migrations ORDER BY version')] == applied


def test_subscription_provider_reference_is_only_declaration_and_large_revision_fails_before_sql(client):
    pid, _, _, _, book_body, _, _, _, _ = prepared(client)
    book_body = {**book_body, 'idempotency_key': 'subscription-profile', 'provider_profile_ref': {
        'profile_id': 'user-codex-subscription', 'revision': 1, 'sha256': hashlib.sha256(b'original-declaration').hexdigest(),
        'auth_kind': 'OAUTH_SUBSCRIPTION'}}
    value = book(client, pid, book_body)
    assert value['payload']['provider_binding_status'] == 'DECLARED_NOT_AUTHENTICATED'
    assert value['payload']['model_calls'] == 0
    assert client.get(f'/api/projects/{pid}/taskbooks/{value["id"]}?revision={2**63}').status_code == 422
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}
