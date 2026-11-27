"""Actual long-source/restart/child receipts for the Questions016 boundary."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.runner.worker import Worker
from packages.domain.store import Store
from packages.domain.intake import IntakeInput, IntakeService
from test_project_intake import project, upload, make
from test_project_questions import setup, ask, answer, no_runtime
from test_worker import until


def evidence(tmp_path, name):
    path = Path(os.environ.get('EEAS_EVIDENCE_DIR', str(tmp_path))) / name
    path.mkdir(exist_ok=False)
    return path


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def snapshot(store, path):
    with store.connect() as source, sqlite3.connect(path) as target:
        source.backup(target)


def test_selected_real_tail_chunk_restart_and_source_drift_are_evidenced(client, tmp_path):
    pid = project(client, '原创长材料问题证据')
    original = ''.join(f'原创资料行{i:05d}：接口参数尚待用户选择。\n' for i in range(5000)) + '原件最后：明确保留这个尾端决策。'
    assert 70000 < len(original) < 240000
    doc, ref = upload(client, pid, original)
    store = client.app.state.store
    chunks = client.get(f'/api/projects/{pid}/documents/{doc["id"]}/chunks?revision=1&limit=1').json()
    last = client.get(f'/api/projects/{pid}/documents/{doc["id"]}/chunks?revision=1&offset={chunks["total_chunks"]-1}&limit=1').json()['chunks'][0]
    ref['chunk_indexes'] = [last['index']]
    response = make(client, pid, [ref]); assert response.status_code == 201, response.text
    intake = response.json(); chunk = intake['snapshot']['materials'][0]['chunks'][0]
    assert intake['snapshot']['materials'][0]['text_coverage'] == 'SELECTED_CHUNKS'
    assert chunk['content'].endswith('明确保留这个尾端决策。')
    q_body = {'idempotency_key': 'tail-q', 'intake_id': intake['id'], 'intake_revision': 1,
        'intake_sha256': intake['sha256'], 'text': '明确确认原件尾端的接口决定。',
        'priority': 'INPUT_REQUIRED', 'decision_field': 'interface.tail', 'scope': '此片只记录资料与等待，不开始实施。',
        'source_anchors': [{'document_id': doc['id'], 'revision': 1, 'raw_sha256': doc['sha256'],
            'chunk_index': chunk['index'], 'chunk_sha256': chunk['sha256'],
            'char_start': chunk['char_start'], 'char_end': chunk['char_end'],
            'quote_sha256': hashlib.sha256(chunk['content'].encode()).hexdigest()}]}
    q = ask(client, pid, q_body)
    assert q['payload']['source_anchors'][0]['quote'] == original[chunk['char_start']:chunk['char_end']]
    omitted = {**q_body, 'idempotency_key': 'omitted'}
    omitted['source_anchors'] = [{**q_body['source_anchors'][0], 'chunk_index': 0}]
    denied = client.post(f'/api/projects/{pid}/questions', json=omitted)
    assert denied.status_code == 422, denied.text
    with TestClient(create_app(client.app.state.settings), base_url=client.app.state.settings.origin) as restarted:
        bootstrap = restarted.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
        restarted.headers.update({'Origin': client.app.state.settings.origin, 'X-EEAS-CSRF': bootstrap.json()['csrf_token']})
        restored = restarted.get(f'/api/projects/{pid}/questions/{q["id"]}').json()
        assert restored == q
        body, a = answer(restarted, pid, q, text='人类可迟到的真实原创答复。')
        assert a['payload']['caller'] == q['payload']['caller']
        assert a['payload']['application_status'] == 'RECORDED'
    revision = client.put(f'/api/projects/{pid}/documents/{doc["id"]}',
        files={'file': ('changed.md', b'Original changed source.\n', 'text/markdown')}, data={'expected_revision': 1})
    assert revision.status_code == 200, revision.text
    late_body, late = answer(client, pid, q, key='source-drift', text='针对旧资料录入的迟到答复，不自动改计划。')
    assert late['payload']['application_status'] == 'REVIEW_REQUIRED'
    assert late['payload']['intake_sha256'] == intake['sha256']
    assert client.get(f'/api/projects/{pid}/questions/{q["id"]}?revision=1').json()['payload'] == q['payload']
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}
    out = evidence(tmp_path, 'actual-questions-long-source')
    (out/'original.raw').write_bytes(original.encode())
    save(out/'actual-api-receipts.json', {'schema': 'eeas.original-question-observation.v1',
        'source_kind': 'synthetic', 'raw_sha256': doc['sha256'], 'raw_characters': len(original),
        'intake': intake, 'question_request': q_body, 'question_receipt': q,
        'restart_question_receipt': restored, 'answer_request': body, 'answer_receipt': a,
        'changed_source_receipt': revision.json(), 'late_request': late_body, 'late_receipt': late,
        'omitted_range_actual_http': denied.status_code, 'omitted_range_actual_response': denied.json(),
        'runtime_counts': no_runtime(client), 'company_model_calls': 0, 'native_windows': 'NOT_RUN'})
    snapshot(store, out/'actual-store.sqlite3')


def test_worker_skips_blocked_queue_and_persists_actual_neighbor_child(client, tmp_path):
    pid, doc, intake, body, blocked_task, task_body = setup(client, task=True)
    route = f'/api/projects/{pid}/tasks/{blocked_task["id"]}/runs'
    r = client.post(route, json={'revision': 1, 'operation_id': 'pending-original'})
    assert r.status_code == 202, r.text
    pending = r.json(); q = ask(client, pid, body)
    sibling = client.post(f'/api/projects/{pid}/tasks', json={**task_body, 'goal': '原创独立作业实际执行'}).json()
    r = client.post(f'/api/projects/{pid}/tasks/{sibling["id"]}/runs', json={'revision': 1, 'operation_id': 'independent-actual'})
    assert r.status_code == 202, r.text
    independent = r.json(); store = client.app.state.store; worker = Worker(store, tmp_path)
    assert worker.start()
    try:
        result = until(store, pid, independent['id'], {'RETURNED', 'FAILED'})
        assert result['execution_status'] == 'RETURNED', result
        held = store.run(pid, pending['id'])
        assert held['execution_status'] == 'QUEUED' and held['started_at'] is None
        assert held['runner_job_id'] is None and held['session_id'] is None
        assert held['frozen_inputs'] == pending['frozen_inputs']
        assert json.loads(held['frozen_inputs'])['spec']['budget'] == blocked_task['spec']['budget']
        held_events = store.events(pid, held['id'])
        assert [e['payload']['type'] for e in held_events] == ['queued', 'waiting_question']
        assert result['verification_status'] == 'NOT_RUN'
        assert not (tmp_path/held['workspace']).exists()
        candidate = (tmp_path/result['workspace']/'candidate.txt').read_bytes()
        assert candidate == '原创独立作业实际执行\n'.encode()
        with store.connect() as c:
            jobs = [dict(row) for row in c.execute('SELECT * FROM runner_jobs')]
        assert len(jobs) == 1 and jobs[0]['run_id'] == result['id'] and jobs[0]['exit_code'] == 0
        assert no_runtime(client) == {'task_runs': 2, 'runner_jobs': 1, 'agent_executions': 0}
    finally:
        worker.stop()
    out = evidence(tmp_path, 'actual-questions-independent-child')
    (out/'actual-candidate.raw').write_bytes(candidate)
    save(out/'actual-api-receipts.json', {'schema': 'eeas.original-question-observation.v1',
        'source_kind': 'synthetic', 'question': q, 'intake': intake, 'raw_sha256': doc['sha256'],
        'blocked_initial_run': pending, 'blocked_observed_run': held, 'blocked_events': held_events,
        'independent_task': sibling, 'independent_initial_run': independent,
        'independent_observed_run': result, 'independent_events': store.events(pid, result['id']),
        'actual_runner_jobs': jobs, 'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
        'runtime_counts': no_runtime(client), 'company_model_calls': 0, 'native_windows': 'NOT_RUN'})
    snapshot(store, out/'actual-store.sqlite3')


def test_real_schema15_prefix_upgrades_without_rewriting_original_source(tmp_path, migrate_through):
    # Actual original SQL prefix, not a modern DB with a fabricated hole.
    root = Path(__file__).resolve().parents[1]
    database = tmp_path/'eeas.sqlite3'
    original = '旧schema15原件。\r\n接口尚需明确答复。\r\n'.encode()
    sha = hashlib.sha256(original).hexdigest()
    created = '2026-10-13T00:00:00+00:00'
    prefix = sorted((root/'packages/domain/migrations').glob('*.sql'))[:15]
    assert prefix[-1].name == '015_project_intakes.sql'
    with sqlite3.connect(database) as c:
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)')
        for path in prefix:
            c.executescript(path.read_text())
            c.execute('INSERT INTO schema_migrations VALUES (?)', (path.name,))
        c.execute('INSERT INTO projects VALUES (?,?,?,?)', ('legacy-project', '原创旧项目', '', created))
        c.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)', ('legacy-document', 'legacy-project', 'old.md', 1, None, created))
        c.execute('INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)',
            ('legacy-document', 1, sha, original, original.decode(), 'original:schema15', created))
        before = dict(c.execute('SELECT version,1 FROM schema_migrations'))
    migrate_through(database, 16, '016_project_questions.sql')
    with sqlite3.connect(database) as db:
        mid = dict(db.execute('SELECT version,1 FROM schema_migrations'))
    assert set(mid)-set(before) == {'016_project_questions.sql'}
    store = Store(database)
    with store.connect() as c:
        after = dict(c.execute('SELECT version,1 FROM schema_migrations'))
        assert sorted(after) == [p.name for p in sorted((root/'packages/domain/migrations').glob('*.sql'))]
        assert set(before) <= set(after)
        assert c.execute('SELECT count(*) FROM project_questions').fetchone()[0] == 0
    doc = store.document('legacy-project', 'legacy-document', 1)
    assert doc['raw_bytes'] == original and doc['sha256'] == sha
    intake = IntakeService(store, root).save('legacy-project', IntakeInput(idempotency_key='legacy-intake', materials=[{
        'document_id': 'legacy-document', 'revision': 1, 'sha256': sha}]))
    assert intake['snapshot']['materials'][0]['chunks'][0]['content'].encode() == original
    reopened = Store(database)
    assert reopened.document('legacy-project', 'legacy-document', 1)['raw_bytes'] == original
    out = evidence(tmp_path, 'actual-questions-schema15-upgrade')
    (out/'original.raw').write_bytes(original)
    save(out/'actual-migration-observation.json', {'schema': 'eeas.original-question-migration-observation.v1',
        'source_kind': 'synthetic', 'prefix_original_sql_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in prefix},
        'schema_before': sorted(before), 'schema_after': sorted(after), 'original_sha256': sha,
        'actual_intake': intake, 'company_model_calls': 0, 'native_windows': 'NOT_RUN'})
    snapshot(reopened, out/'actual-store.sqlite3')


def test_machine_gate_body_matches_authoritative_state_and_frozen_refs(client, tmp_path):
    pid, _, intake, body, task, task_body = setup(client, task=True)
    q = ask(client, pid, body)
    getter = f'/api/projects/{pid}/tasks/{task["id"]}/knowledge'
    route = f'/api/projects/{pid}/tasks/{task["id"]}/runs'
    gate = client.get(getter).json()
    denied = client.post(route, json={'revision': 1, 'operation_id': 'structured-block'})
    assert denied.status_code == 409
    error = denied.json()['error']
    assert error['code'] == 'BLOCKED_ON_QUESTION'
    expected = {k: v for k, v in gate.items() if k not in ('task_review_status', 'current_task_revision')}
    assert error['knowledge'] == expected
    blocker = error['knowledge']['blockers'][0]
    assert (blocker['question_id'], blocker['question_revision'], blocker['question_sha256']) == (q['id'], 1, q['sha256'])
    assert (blocker['intake_id'], blocker['intake_revision'], blocker['intake_sha256']) == (intake['id'], 1, intake['sha256'])
    assert blocker['bound_task_revision'] == 1 and blocker['bound_spec_hash'] == task['sha256']
    revised = client.put(f'/api/projects/{pid}/tasks/{task["id"]}?expected_revision=1', json={**task_body, 'goal': '显式重审范围'}).json()
    scope = client.post(route, json={'revision': revised['revision'], 'operation_id': 'changed-scope'})
    assert scope.status_code == 409 and scope.json()['error']['code'] == 'NEEDS_SCOPE_REVIEW'
    # Existing revision conflicts retain their separate machine code.
    bad = client.post(f'/api/projects/{pid}/questions', json={**body, 'idempotency_key': 'bad-hash', 'intake_sha256': '0'*64})
    assert bad.status_code == 409 and bad.json()['error']['code'] == 'REVISION_CONFLICT'
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}
    out = evidence(tmp_path, 'actual-questions-machine-gate')
    save(out/'actual-api-receipts.json', {'schema': 'eeas.original-question-observation.v1', 'source_kind': 'synthetic',
        'question': q, 'authoritative_getter': gate, 'blocked_actual_http': denied.status_code,
        'blocked_actual_body': denied.json(), 'changed_scope_actual_http': scope.status_code,
        'changed_scope_actual_body': scope.json(), 'old_conflict_actual_body': bad.json(),
        'runtime_counts': no_runtime(client), 'company_model_calls': 0, 'native_windows': 'NOT_RUN'})
    snapshot(client.app.state.store, out/'actual-store.sqlite3')
