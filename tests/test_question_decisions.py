"""Original explicit user scope decisions; actual API/DB/fixture jobs, no model."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from test_project_intake import make, project
from test_project_questions import setup, ask, answer, no_runtime
from test_worker import until


def exact_task(task):
    return {'task_id': task['id'], 'revision': task['revision'], 'spec_hash': task['sha256']}


def resolution_body(q, task, intake, a=None, key='resolution'):
    return {'idempotency_key': key,
        'intake_ref': {'intake_id': intake['id'], 'revision': intake['revision'], 'sha256': intake['sha256']},
        'question_ref': {'question_id': q['id'], 'revision': q['revision'], 'sha256': q['sha256']},
        'task_ref': exact_task(task),
        'reviewed_scope_refs': [{'question_id': q['id'], 'question_revision': q['revision'],
            'question_sha256': q['sha256'], 'task_id': task['id'], 'task_revision': task['revision'], 'spec_hash': task['sha256']}],
        'mode': 'ANSWER_ACCEPTED' if a else 'ASSUMPTION_ACCEPTED',
        'answer_refs': [] if not a else [{'answer_id': a['id'], 'revision': a['revision'], 'sha256': a['sha256']}],
        'assumption': None if a else '用户明确接受本范围按接口版本一实施；保留未读模态限制。',
        'reason': '只解决所列问题对所列任务的影响，不代替执行批准。'}


def resolve(client, pid, body):
    r = client.post(f'/api/projects/{pid}/question-resolutions', json=body)
    assert r.status_code == 201, r.text
    return r.json()


def decision_body(task, intake, resolutions, key='user-ready'):
    return {'idempotency_key': key, 'intent': 'USER_READY_OVERRIDE', 'user_ready': True,
        'intake_ref': {'intake_id': intake['id'], 'revision': intake['revision'], 'sha256': intake['sha256']},
        'task_ref': exact_task(task), 'scope_reason': '用户认为这个既有任务范围足够明确，允许进入原执行审批。',
        'resolution_refs': [{'resolution_id': r['id'], 'sha256': r['sha256']} for r in resolutions],
        'accepted_assumptions': [{'resolution_id': r['id'], 'assumption_sha256': r['payload']['assumption_sha256']}
                                 for r in resolutions if r['payload']['mode'] == 'ASSUMPTION_ACCEPTED']}


def ready(client, pid, body):
    r = client.post(f'/api/projects/{pid}/start-decisions', json=body)
    assert r.status_code == 201, r.text
    return r.json()


def record(tmp_path, label, value, store):
    out = Path(os.environ.get('EEAS_EVIDENCE_DIR', str(tmp_path)))/label
    out.mkdir(exist_ok=False)
    with (out/'actual-api-receipts.json').open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')
    with store.connect() as source, sqlite3.connect(out/'actual-store.sqlite3') as target:
        source.backup(target)


def test_explicit_answer_resolution_readiness_original_approval_and_real_neighbor(client, tmp_path):
    pid, doc, intake, qbody, neighbor, task_body = setup(client, task=True)
    response = client.post(f'/api/projects/{pid}/tasks', json={**task_body, 'approval_required': True})
    assert response.status_code == 201, response.text
    task = response.json(); qbody['affected_task_refs'] = [exact_task(task)]
    q = ask(client, pid, qbody); _, a = answer(client, pid, q)
    route = f'/api/projects/{pid}/tasks/{task["id"]}/runs'
    assert client.post(route, json={'revision': 1, 'operation_id': 'answer-only'}).status_code == 409
    rb = resolution_body(q, task, intake, a); res = resolve(client, pid, rb)
    assert client.post(route, json={'revision': 1, 'operation_id': 'resolution-only'}).status_code == 409
    db = decision_body(task, intake, [res]); decision = ready(client, pid, db)
    assert decision['payload']['method'] == 'LOCAL_USER_OVERRIDE'
    assert decision['payload']['workflow_preparation_status'] == 'TASKBOOK_HARNESS_NOT_IMPLEMENTED'
    assert decision['payload']['model_calls'] == 0
    assert client.post(f'/api/projects/{pid}/start-decisions', json=db).json() == decision
    r = client.post(route, json={'revision': 1, 'operation_id': 'explicit-start'})
    assert r.status_code == 202, r.text
    run = r.json(); assert run['execution_status'] == 'AWAITING_APPROVAL'
    assert json.loads(run['frozen_inputs'])['spec']['budget'] == task['spec']['budget']
    neighbor_run = client.post(f'/api/projects/{pid}/tasks/{neighbor["id"]}/runs',
        json={'revision': 1, 'operation_id': 'independent-branch'}).json()
    store = client.app.state.store; worker = Worker(store, tmp_path); assert worker.start()
    try:
        result_b = until(store, pid, neighbor_run['id'], {'RETURNED', 'FAILED'})
        assert result_b['execution_status'] == 'RETURNED', result_b
        assert store.run(pid, run['id'])['execution_status'] == 'AWAITING_APPROVAL'
        assert not (tmp_path/run['workspace']).exists()
        approval = next(row for row in client.get(f'/api/projects/{pid}/approvals').json()['approvals'] if row['run_id'] == run['id'])
        r = client.post(f'/api/projects/{pid}/approvals/{approval["id"]}/decision',
            json={'action_hash': approval['action_hash'], 'approve': True})
        assert r.status_code == 200, r.text
        result_a = until(store, pid, run['id'], {'RETURNED', 'FAILED'})
        assert result_a['execution_status'] == 'RETURNED', result_a
        assert result_a['verification_status'] == result_b['verification_status'] == 'NOT_RUN'
        assert (tmp_path/result_a['workspace']/'candidate.txt').read_text() == task['spec']['goal']+'\n'
        assert no_runtime(client) == {'task_runs': 2, 'runner_jobs': 2, 'agent_executions': 0}
    finally:
        worker.stop()
    reopened = type(client.app.state.decisions)(TaskService(tmp_path/'eeas.sqlite3', ROOT))
    assert reopened.get_start(pid, decision['id'])['payload'] == decision['payload']
    assert reopened.get_resolution(pid, res['id'])['payload'] == res['payload']
    assert client.post(f'/api/projects/{pid}/question-resolutions', json=rb).json() == res
    with store.connect() as c, pytest.raises(sqlite3.IntegrityError, match='immutable'):
        c.execute('UPDATE project_start_decisions SET payload=?', ('changed',))
    record(tmp_path, 'actual-explicit-start-approved', {'source_kind': 'synthetic', 'raw_sha256': doc['sha256'],
        'resolution_request': rb, 'resolution_receipt': res, 'start_request': db, 'start_receipt': decision,
        'initial_run': run, 'approval': approval, 'actual_task_result': result_a, 'actual_neighbor_result': result_b,
        'task_events': store.events(pid, run['id']), 'company_model_calls': 0, 'native_windows': 'NOT_RUN'}, store)


@pytest.mark.parametrize('drift', ['source', 'intake', 'question', 'answer', 'task'])
def test_drift_requires_review_while_operation_receipts_and_old_runs_stay_frozen(client, tmp_path, drift):
    pid, doc, intake, qbody, task, task_body = setup(client, task=True)
    q = ask(client, pid, qbody); abody, a = answer(client, pid, q)
    rb = resolution_body(q, task, intake, a); res = resolve(client, pid, rb)
    db = decision_body(task, intake, [res]); decision = ready(client, pid, db)
    route = f'/api/projects/{pid}/tasks/{task["id"]}/runs'
    old = client.post(route, json={'revision': 1, 'operation_id': 'old-run'}).json()
    if drift == 'source':
        assert client.put(f'/api/projects/{pid}/documents/{doc["id"]}', files={'file': ('changed.md', b'Changed original source.')},
            data={'expected_revision': 1}).status_code == 200
    elif drift == 'intake':
        assert make(client, pid, [{'document_id': doc['id'], 'revision': 1, 'sha256': doc['sha256']}],
                    key='new-intake', revision=1, iid=intake['id']).status_code == 200
    elif drift == 'question':
        ask(client, pid, {**qbody, 'idempotency_key': 'q-new', 'expected_revision': 1, 'text': '新版本决定字段范围。'}, q['id'])
    elif drift == 'answer':
        assert client.put(f'/api/projects/{pid}/questions/{q["id"]}/answers/{a["id"]}',
            json={**abody, 'idempotency_key': 'a-new', 'expected_answer_revision': 1, 'text': '更正旧答复。'}).status_code == 200
    else:
        assert client.put(f'/api/projects/{pid}/tasks/{task["id"]}?expected_revision=1',
                          json={**task_body, 'goal': '新范围，不自动继承开始决定'}).status_code == 200
    current = client.get(f'/api/projects/{pid}/start-decisions/{decision["id"]}').json()
    assert current['current_association'] == 'REVIEW_REQUIRED'
    assert current['payload'] == decision['payload']
    assert client.post(f'/api/projects/{pid}/start-decisions', json=db).json() == decision
    assert client.post(f'/api/projects/{pid}/question-resolutions', json=rb).json() == res
    revision = 2 if drift == 'task' else 1
    assert client.post(route, json={'revision': revision, 'operation_id': 'must-review'}).status_code == 409
    actual_old = client.get(f'/api/projects/{pid}/runs/{old["id"]}').json()
    assert actual_old['frozen_inputs'] == old['frozen_inputs'] and actual_old['spec_hash'] == old['spec_hash']
    assert no_runtime(client) == {'task_runs': 1, 'runner_jobs': 0, 'agent_executions': 0}
    _, late = answer(client, pid, q, key='late-'+drift)
    if drift in ('source', 'intake', 'question'):
        assert late['payload']['application_status'] == 'REVIEW_REQUIRED'
    record(tmp_path, 'actual-start-drift-'+drift, {'source_kind': 'synthetic', 'drift': drift,
        'original_resolution': res, 'original_start': decision, 'current_get': current,
        'old_run': old, 'observed_old_run': actual_old, 'late_answer': late,
        'company_model_calls': 0, 'native_windows': 'NOT_RUN'}, client.app.state.store)


def test_assumptions_are_explicit_and_start_cannot_clear_another_blocking_scope(client):
    pid, _, intake, qbody, task, _ = setup(client, task=True)
    first = ask(client, pid, qbody)
    second = ask(client, pid, {**qbody, 'idempotency_key': 'second-q', 'text': '另一个必须单独决定的字段。', 'decision_field': 'other.field'})
    res1 = resolve(client, pid, resolution_body(first, task, intake))
    partial = decision_body(task, intake, [res1])
    assert client.post(f'/api/projects/{pid}/start-decisions', json=partial).status_code == 409
    res2 = resolve(client, pid, resolution_body(second, task, intake, key='second-resolution'))
    complete = decision_body(task, intake, [res1, res2])
    for changed in ({**complete, 'user_ready': False}, {**complete, 'user_ready': 1}, {**complete, 'actor': 'human'},
                    {**complete, 'max_steps': 9999}):
        assert client.post(f'/api/projects/{pid}/start-decisions', json=changed).status_code == 422
    missing_acceptance = {**complete, 'accepted_assumptions': []}
    assert client.post(f'/api/projects/{pid}/start-decisions', json=missing_acceptance).status_code == 409
    decision = ready(client, pid, complete)
    assert decision['payload']['accepted_assumptions'] == complete['accepted_assumptions']
    changed = {**complete, 'scope_reason': '同key不同scope不可覆盖'}
    assert client.post(f'/api/projects/{pid}/start-decisions', json=changed).status_code == 409
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}


def test_exact_history_review_is_required_to_move_resolution_to_new_task_revision(client):
    pid, _, intake, qbody, task, task_body = setup(client, task=True)
    q = ask(client, pid, qbody)
    revised = client.put(f'/api/projects/{pid}/tasks/{task["id"]}?expected_revision=1', json={**task_body, 'goal': '显式新的任务范围'}).json()
    rb = resolution_body(q, task, intake)
    rb['task_ref'] = exact_task(revised)
    missing = {**rb, 'reviewed_scope_refs': []}
    assert client.post(f'/api/projects/{pid}/question-resolutions', json=missing).status_code in (409, 422)
    res = resolve(client, pid, rb)
    decision = ready(client, pid, decision_body(revised, intake, [res]))
    assert decision['payload']['task_ref'] == exact_task(revised)
    assert res['payload']['reviewed_scope_refs'] == rb['reviewed_scope_refs']
    r = client.post(f'/api/projects/{pid}/tasks/{task["id"]}/runs', json={'revision': 2, 'operation_id': 'reviewed-new-scope'})
    assert r.status_code == 202, r.text
    assert r.json()['task_revision'] == 2 and r.json()['verification_status'] == 'NOT_RUN'


def test_queued_claim_rechecks_decision_without_consuming_approval_or_releasing_other_task(client, tmp_path):
    pid, _, intake, qbody, other, task_body = setup(client, task=True)
    task = client.post(f'/api/projects/{pid}/tasks', json={**task_body, 'approval_required': True}).json()
    runs = []
    for current, key in ((task, 'approved-before-question'), (other, 'other-queued')):
        r = client.post(f'/api/projects/{pid}/tasks/{current["id"]}/runs', json={'revision': 1, 'operation_id': key})
        assert r.status_code == 202, r.text
        runs.append(r.json())
    run, other_run = runs
    approval = next(a for a in client.get(f'/api/projects/{pid}/approvals').json()['approvals'] if a['run_id'] == run['id'])
    r = client.post(f'/api/projects/{pid}/approvals/{approval["id"]}/decision',
                    json={'action_hash': approval['action_hash'], 'approve': True})
    assert r.status_code == 200 and r.json()['status'] == 'APPROVED', r.text
    q = ask(client, pid, {**qbody, 'affected_task_refs': [exact_task(task)]})
    other_q = ask(client, pid, {**qbody, 'idempotency_key': 'other-question', 'decision_field': 'other.wait',
                              'affected_task_refs': [exact_task(other)]})
    store = client.app.state.store; worker = Worker(store, tmp_path)
    for _ in range(2):
        assert worker.claim() is None
    before_events = store.events(pid, run['id'])
    assert len([e for e in before_events if e['payload']['type'] == 'waiting_question']) == 1
    _, a = answer(client, pid, q)
    res = resolve(client, pid, resolution_body(q, task, intake, a))
    assert worker.claim() is None
    with store.connect() as c:
        assert c.execute('SELECT status FROM approvals WHERE id=?', (approval['id'],)).fetchone()[0] == 'APPROVED'
    for pending in runs:
        actual = store.run(pid, pending['id'])
        assert actual['execution_status'] == 'QUEUED' and actual['started_at'] is None and actual['runner_job_id'] is None
        assert actual['frozen_inputs'] == pending['frozen_inputs']
        assert not (tmp_path/pending['workspace']).exists()
    decision = ready(client, pid, decision_body(task, intake, [res]))
    knowledge = client.get(f'/api/projects/{pid}/tasks/{task["id"]}/knowledge').json()
    assert knowledge['status'] == 'READY' and knowledge['start_decision']['decision_id'] == decision['id']
    other_knowledge = client.get(f'/api/projects/{pid}/tasks/{other["id"]}/knowledge').json()
    assert other_knowledge['status'] == 'BLOCKED_ON_QUESTION'
    assert [b['question_id'] for b in other_knowledge['blockers']] == [other_q['id']]
    claimed = worker.claim(); assert claimed['id'] == run['id']
    worker.execute(claimed)
    returned = store.run(pid, run['id'])
    assert returned['execution_status'] == 'RETURNED' and returned['verification_status'] == 'NOT_RUN'
    assert returned['frozen_inputs'] == run['frozen_inputs'] and returned['spec_hash'] == run['spec_hash']
    assert worker.claim() is None and store.run(pid, other_run['id'])['execution_status'] == 'QUEUED'
    assert no_runtime(client) == {'task_runs': 2, 'runner_jobs': 1, 'agent_executions': 0}
    with store.connect() as c:
        assert c.execute('SELECT status FROM approvals WHERE id=?', (approval['id'],)).fetchone()[0] == 'CONSUMED'
    record(tmp_path, 'actual-queued-info-release', {'source_kind': 'synthetic', 'question': q,
        'resolution': res, 'start_decision': decision, 'knowledge': knowledge, 'other_knowledge': other_knowledge,
        'original_approval': approval, 'original_runs': runs, 'actual_returned': returned,
        'actual_other_run': store.run(pid, other_run['id']), 'events_before_decision': before_events,
        'events_after': store.events(pid, run['id']), 'company_model_calls': 0}, store)


def test_exact_refs_project_and_late_answer_rejections_create_no_decision(client):
    pid, _, intake, qbody, task, _ = setup(client, task=True)
    q = ask(client, pid, qbody); _, a = answer(client, pid, q)
    body = resolution_body(q, task, intake, a)
    route = f'/api/projects/{pid}/question-resolutions'
    for field in ('intake_ref', 'question_ref', 'task_ref'):
        wrong = copy.deepcopy(body)
        wrong[field]['spec_hash' if field == 'task_ref' else 'sha256'] = '0'*64
        assert client.post(route, json=wrong).status_code == 409
    wrong = copy.deepcopy(body); wrong['answer_refs'][0]['sha256'] = '0'*64
    assert client.post(route, json=wrong).status_code == 409
    for wrong in ({**body, 'actor': 'human'}, {**body, 'assumption': '隐式假设替换回答'}):
        assert client.post(route, json=wrong).status_code == 422
    cross = project(client, '其他项目不能接受这些来源')
    assert client.post(f'/api/projects/{cross}/question-resolutions', json=body).status_code == 404
    assert client.post(route, json=body, headers={'Origin': 'http://untrusted.invalid'}).status_code == 403
    revised = ask(client, pid, {**qbody, 'idempotency_key': 'current-question', 'expected_revision': 1,
                              'text': '新问题版本要求重新答复。'}, q['id'])
    _, late = answer(client, pid, q, key='late-answer')
    assert late['payload']['application_status'] == 'REVIEW_REQUIRED'
    late_body = resolution_body(revised, task, intake, late)
    late_body['reviewed_scope_refs'] = body['reviewed_scope_refs'] + late_body['reviewed_scope_refs']
    assert client.post(route, json=late_body).status_code == 409
    res = resolve(client, pid, {**late_body, 'mode': 'ASSUMPTION_ACCEPTED', 'answer_refs': [],
                               'assumption': '用户明确接受新问题当前范围的假设；旧答复仍晚到。'})
    assert client.get(f'/api/projects/{cross}/question-resolutions/{res["id"]}').status_code == 404
    db = decision_body(task, intake, [res]); wrong = copy.deepcopy(db); wrong['resolution_refs'][0]['sha256'] = '0'*64
    assert client.post(f'/api/projects/{pid}/start-decisions', json=wrong).status_code == 409
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM project_question_resolutions').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM project_start_decisions').fetchone()[0] == 0
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}
