"""Original source-bound API/SQLite questions; actual fixture runner, no model."""
import copy
import hashlib
import json
import sqlite3

import pytest

from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from test_project_intake import project, upload, make
from test_tasks import inputs
from test_worker import until


def setup(client, task=False):
    pid = project(client, '原创异步问题')
    if task:
        doc, _, task_body = inputs(client, pid)
        ref = {'document_id': doc['id'], 'revision': doc['revision'], 'sha256': doc['sha256']}
        response = client.post(f'/api/projects/{pid}/tasks', json=task_body)
        assert response.status_code == 201, response.text
        task = response.json()
    else:
        doc, ref = upload(client, pid, '原文事实。\r\n接口待用户决定。\r\n最后一个真实约束。')
        task, task_body = None, None
    response = make(client, pid, [ref]); assert response.status_code == 201, response.text
    intake = response.json()
    chunk = intake['snapshot']['materials'][0]['chunks'][0]
    text = chunk['content']
    body = {'idempotency_key': 'create-question', 'intake_id': intake['id'],
        'intake_revision': intake['revision'], 'intake_sha256': intake['sha256'],
        'text': '  接口应使用哪个版本？\r\n原始末行不添加换行。',
        'priority': 'DECISION_REQUIRED', 'decision_field': 'interface.version',
        'scope': '仅等待引用的未开始任务；保留独立工作。',
        'source_anchors': [{'document_id': doc['id'], 'revision': doc['revision'],
            'raw_sha256': doc['sha256'], 'chunk_index': chunk['index'],
            'chunk_sha256': chunk['sha256'], 'char_start': chunk['char_start'],
            'char_end': chunk['char_end'], 'quote_sha256': hashlib.sha256(text.encode()).hexdigest()}],
        'affected_task_refs': [] if not task else [{'task_id': task['id'], 'revision': task['revision'], 'spec_hash': task['sha256']}]}
    return pid, doc, intake, body, task, task_body


def ask(client, pid, body, qid=None):
    route = f'/api/projects/{pid}/questions'
    response = client.post(route, json=body) if qid is None else client.put(route+'/'+qid, json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()


def answer(client, pid, question, key='answer', text='原始用户回答：选版本一。\r\n末行。'):
    body = {'idempotency_key': key, 'question_revision': question['revision'],
            'question_sha256': question['sha256'], 'text': text}
    response = client.post(f'/api/projects/{pid}/questions/{question["id"]}/answers', json=body)
    assert response.status_code == 201, response.text
    return body, response.json()


def no_runtime(client):
    with client.app.state.store.connect() as c:
        return {name: c.execute('SELECT count(*) FROM '+name).fetchone()[0]
                for name in ('task_runs', 'runner_jobs', 'agent_executions')}


def test_raw_question_answer_replay_restart_and_immutable_history(client, tmp_path):
    pid, doc, intake, body, _, _ = setup(client)
    q = ask(client, pid, body)
    assert q['payload']['text'].encode() == body['text'].encode()
    assert q['payload']['text_sha256'] == hashlib.sha256(body['text'].encode()).hexdigest()
    assert q['payload']['source_anchors'][0]['quote'] == intake['snapshot']['materials'][0]['chunks'][0]['content']
    assert q['payload']['caller']['kind'] == 'LOCAL_SESSION'
    caller = q['payload']['caller']['id']; assert len(caller) == 64
    assert client.cookies.get('eeas_session') not in json.dumps(q, ensure_ascii=False)
    a_body, a = answer(client, pid, q)
    assert a['payload']['text'].encode() == a_body['text'].encode()
    assert a['payload']['application_status'] == 'RECORDED'
    assert a['payload']['caller'] == q['payload']['caller']
    assert client.get(f'/api/projects/{pid}/questions/{q["id"]}').json()['status'] == 'ANSWER_RECORDED'
    assert ask(client, pid, body) == q
    assert client.post(f'/api/projects/{pid}/questions/{q["id"]}/answers', json=a_body).json() == a
    changed = {**body, 'idempotency_key': 'revise', 'expected_revision': 1, 'text': '修订问题保留原问。'}
    revised = ask(client, pid, changed, q['id']); assert revised['revision'] == 2
    assert ask(client, pid, body) == q
    service = type(client.app.state.questions)(TaskService(tmp_path/'eeas.sqlite3', ROOT))
    assert service.get(pid, q['id'], 1)['payload'] == q['payload']
    assert service.answers(pid, q['id'])['answers'][0]['payload'] == a['payload']
    for table, field in (('project_question_versions', 'payload'), ('project_answer_versions', 'payload'),
                         ('project_question_operations', 'receipt'), ('project_question_events', 'payload')):
        with service.store.connect() as c, pytest.raises(sqlite3.IntegrityError, match='immutable'):
            c.execute('UPDATE '+table+' SET '+field+'=?', ('changed',))
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}


def test_scoped_sources_anchors_tasks_and_untrusted_actor_fail_closed(client):
    pid, doc, intake, body, task, _ = setup(client, task=True)
    other = project(client, '不相关项目')
    route = f'/api/projects/{pid}/questions'
    for changed in ({**body, 'actor': 'human'}, {**body, 'priority': 'BLOCKING'},
                    {**body, 'text': '   '}, {**body, 'intake_revision': True}):
        assert client.post(route, json=changed).status_code == 422
    for field in ('intake_sha256',):
        assert client.post(route, json={**body, field: '0'*64}).status_code == 409
    for field in ('raw_sha256', 'chunk_sha256', 'quote_sha256'):
        wrong = copy.deepcopy(body); wrong['source_anchors'][0][field] = '0'*64
        assert client.post(route, json=wrong).status_code == 409
    wrong = copy.deepcopy(body); wrong['source_anchors'][0]['char_end'] += 1
    assert client.post(route, json=wrong).status_code == 422
    wrong = copy.deepcopy(body); wrong['affected_task_refs'][0]['spec_hash'] = '0'*64
    assert client.post(route, json=wrong).status_code == 409
    assert client.post(f'/api/projects/{other}/questions', json=body).status_code == 404
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM project_questions').fetchone()[0] == 0
    q = ask(client, pid, body)
    assert client.get(f'/api/projects/{other}/questions/{q["id"]}').status_code == 404
    assert client.get(f'/api/projects/{other}/questions/{q["id"]}/answers').status_code == 404
    assert client.post(f'/api/projects/{other}/questions/{q["id"]}/answers',
        json={'idempotency_key': 'cross', 'question_revision': 1, 'question_sha256': q['sha256'], 'text': '不跨项目'}).status_code == 404
    assert client.post(route, json={**body, 'text': '同key不同payload'}).status_code == 409
    assert client.post(route, json={**body, 'idempotency_key': 'duplicate-identity'}).status_code == 409
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM project_questions').fetchone()[0] == 1
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}


def test_late_answers_and_corrections_remain_review_required(client):
    pid, doc, intake, body, _, _ = setup(client)
    q = ask(client, pid, body)
    changed = {**body, 'idempotency_key': 'question-revision', 'expected_revision': 1, 'text': '新问题的精确版本。'}
    current = ask(client, pid, changed, q['id'])
    a_body, a = answer(client, pid, q, key='late')
    assert a['payload']['application_status'] == 'REVIEW_REQUIRED'
    assert a['payload']['question_revision'] == 1
    correction = {**a_body, 'idempotency_key': 'correct-answer', 'expected_answer_revision': 1, 'text': '更正原回答。'}
    r = client.put(f'/api/projects/{pid}/questions/{q["id"]}/answers/{a["id"]}', json=correction)
    assert r.status_code == 200, r.text
    assert r.json()['revision'] == 2 and r.json()['payload']['application_status'] == 'REVIEW_REQUIRED'
    assert client.get(f'/api/projects/{pid}/questions/{q["id"]}/answers/{a["id"]}?revision=1').json()['payload'] == a['payload']
    new_intake = make(client, pid, [{'document_id': doc['id'], 'revision': 1, 'sha256': doc['sha256']}],
        key='later-intake', revision=1, iid=intake['id']); assert new_intake.status_code == 200
    _, fresh_but_old_source = answer(client, pid, current, key='old-intake')
    assert fresh_but_old_source['payload']['application_status'] == 'REVIEW_REQUIRED'
    assert client.post(f'/api/projects/{pid}/questions/{q["id"]}/answers', json=a_body).json() == a
    changed_answer = {**a_body, 'text': '不能覆写相同key'}
    assert client.post(f'/api/projects/{pid}/questions/{q["id"]}/answers', json=changed_answer).status_code == 409
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}


def test_new_start_gate_has_no_effect_and_neighbor_really_runs(client, tmp_path):
    pid, doc, intake, body, task, task_body = setup(client, task=True)
    q = ask(client, pid, body)
    route = f'/api/projects/{pid}/tasks/{task["id"]}/runs'
    before = no_runtime(client)
    for _ in range(2):
        r = client.post(route, json={'revision': 1, 'operation_id': 'blocked'})
        assert r.status_code == 409 and r.json()['error']['code'] == 'BLOCKED_ON_QUESTION', r.text
    answer(client, pid, q)
    assert client.post(route, json={'revision': 1, 'operation_id': 'after-answer'}).status_code == 409
    assert no_runtime(client) == before
    assert not (tmp_path/'workspaces').exists()
    sibling = client.post(f'/api/projects/{pid}/tasks', json={**task_body, 'goal': '独立分支'}).json()
    r = client.post(f'/api/projects/{pid}/tasks/{sibling["id"]}/runs', json={'revision': 1, 'operation_id': 'independent'})
    assert r.status_code == 202, r.text
    run = r.json(); worker = Worker(client.app.state.store, tmp_path)
    assert worker.start()
    try:
        result = until(client.app.state.store, pid, run['id'], {'RETURNED', 'FAILED'})
        assert result['execution_status'] == 'RETURNED', result
        assert result['verification_status'] == 'NOT_RUN'
        assert (tmp_path/result['workspace']/'candidate.txt').read_text() == '独立分支\n'
        assert no_runtime(client) == {'task_runs': 1, 'runner_jobs': 1, 'agent_executions': 0}
    finally:
        worker.stop()


def test_queued_waiting_question_deduplicates_and_does_not_consume_approval(client, tmp_path):
    pid, doc, intake, body, task, task_body = setup(client, task=True)
    queued = client.post(f'/api/projects/{pid}/tasks/{task["id"]}/runs',
        json={'revision': 1, 'operation_id': 'old-queue'}).json()
    frozen = queued['frozen_inputs']
    q = ask(client, pid, body)
    replay = client.post(f'/api/projects/{pid}/tasks/{task["id"]}/runs',
        json={'revision': 1, 'operation_id': 'old-queue'}).json()
    assert replay == queued
    approved = client.post(f'/api/projects/{pid}/tasks', json={**task_body, 'approval_required': True}).json()
    r = client.post(f'/api/projects/{pid}/tasks/{approved["id"]}/runs', json={'revision': 1, 'operation_id': 'approved-wait'})
    assert r.status_code == 202, r.text
    waiting = r.json()
    approved_body = copy.deepcopy(body); approved_body['idempotency_key'] = 'approved-question'; approved_body['text'] = '审批不能代替信息回答。'
    approved_body['affected_task_refs'] = [{'task_id': approved['id'], 'revision': 1, 'spec_hash': approved['sha256']}]
    aq = ask(client, pid, approved_body)
    approval = client.get(f'/api/projects/{pid}/approvals').json()['approvals'][0]
    r = client.post(f'/api/projects/{pid}/approvals/{approval["id"]}/decision',
        json={'action_hash': approval['action_hash'], 'approve': True})
    assert r.status_code == 200, r.text
    worker = Worker(client.app.state.store, tmp_path)
    for _ in range(4):
        assert worker.claim() is None
    for run in (queued, waiting):
        actual = client.app.state.store.run(pid, run['id'])
        assert actual['execution_status'] == 'QUEUED'
        assert actual['started_at'] is None and actual['runner_job_id'] is None and actual['session_id'] is None
        assert not (tmp_path/actual['workspace']).exists()
        events = client.app.state.store.events(pid, run['id'])
        assert len([e for e in events if e['payload']['type'] == 'waiting_question']) == 1
    assert client.app.state.store.run(pid, queued['id'])['frozen_inputs'] == frozen
    actual_approval = next(a for a in client.get(f'/api/projects/{pid}/approvals').json()['approvals']
                          if a['id'] == approval['id'])
    assert actual_approval['status'] == 'APPROVED'
    assert no_runtime(client) == {'task_runs': 2, 'runner_jobs': 0, 'agent_executions': 0}
    answer(client, pid, aq)
    assert worker.claim() is None
    cancel = {'idempotency_key': 'cancel-question', 'expected_revision': 1, 'reason': '取消提问仍未确认实施决定。'}
    r = client.post(f'/api/projects/{pid}/questions/{q["id"]}/cancel', json=cancel)
    assert r.status_code == 200 and r.json()['status'] == 'CANCELLED'
    assert worker.claim() is None
    assert client.get(f'/api/projects/{pid}/tasks/{task["id"]}/knowledge').json()['status'] == 'BLOCKED_ON_QUESTION'


def test_advisory_nonblocking_and_new_task_revision_cannot_drop_old_dependency(client):
    pid, doc, intake, body, task, task_body = setup(client, task=True)
    advisory = {**body, 'priority': 'ADVISORY'}
    ask(client, pid, advisory)
    r = client.post(f'/api/projects/{pid}/tasks/{task["id"]}/runs', json={'revision': 1, 'operation_id': 'advisory'})
    assert r.status_code == 202, r.text
    blocked = {**body, 'idempotency_key': 'blocking', 'text': '后续版本仍需确定接口。', 'priority': 'INPUT_REQUIRED'}
    q = ask(client, pid, blocked)
    revised = client.put(f'/api/projects/{pid}/tasks/{task["id"]}?expected_revision=1',
        json={**task_body, 'goal': '显式新任务版本'}).json()
    assert revised['revision'] == 2
    gate = client.get(f'/api/projects/{pid}/tasks/{task["id"]}/knowledge?revision=2').json()
    assert gate['status'] == 'NEEDS_SCOPE_REVIEW'
    assert client.post(f'/api/projects/{pid}/tasks/{task["id"]}/runs',
        json={'revision': 2, 'operation_id': 'cannot-drop-old'}).status_code == 409
    old = client.get(f'/api/projects/{pid}/runs/{r.json()["id"]}').json()
    assert json.loads(old['frozen_inputs'])['spec']['task_revision'] == 1
    assert old['spec_hash'] == task['sha256'] and old['verification_status'] == 'NOT_RUN'


def test_question_pages_events_and_auth_boundaries(client):
    pid, _, _, body, _, _ = setup(client)
    first = ask(client, pid, body)
    second = ask(client, pid, {**body, 'idempotency_key': 'second', 'text': '第二个明确问题。'})
    page = client.get(f'/api/projects/{pid}/questions?limit=1').json()
    assert [q['id'] for q in page['questions']] == [first['id']]
    next_page = client.get(f'/api/projects/{pid}/questions?after_seq={page["next_after_seq"]}&limit=1').json()
    assert [q['id'] for q in next_page['questions']] == [second['id']]
    events = client.get(f'/api/projects/{pid}/question-events?limit=1').json()['events']
    tail = client.get(f'/api/projects/{pid}/question-events?after_seq={events[0]["seq"]}').json()['events']
    assert len(tail) == 1 and tail[0]['seq'] > events[0]['seq']
    assert client.get(f'/api/projects/{pid}/questions?limit=101').status_code == 422
    assert client.post(f'/api/projects/{pid}/questions', json={**body, 'idempotency_key': 'csrf'},
        headers={'X-EEAS-CSRF': 'bad'}).status_code == 403
    original_cookie = client.cookies.get('eeas_session'); client.cookies.clear()
    assert client.get(f'/api/projects/{pid}/questions').status_code == 401
    client.cookies.set('eeas_session', original_cookie)
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}
