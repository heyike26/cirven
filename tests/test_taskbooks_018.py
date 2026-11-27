"""Additional API/SQLite concurrency, migration and source-drift boundaries."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sqlite3

import pytest

from packages.domain.store import Conflict
from packages.domain.taskbooks import TaskBookInput, TaskBookService
from test_project_intake import project, upload, make
from test_project_questions import no_runtime
from test_taskbooks import book, draft


def prepared(client):
    pid = project(client)
    doc, ref = upload(client, pid, '原创来源；不是模型结论。')
    intake = make(client, pid, [ref]).json()
    return pid, doc, draft(intake)


def test_taskbook_api_cas_concurrency_and_restart_replay(client, tmp_path):
    pid, _, body = prepared(client)
    first = book(client, pid, body)
    service = client.app.state.taskbooks
    variants = []
    for i in range(6):
        value = {**body, 'expected_revision': 1, 'idempotency_key': f'parallel-{i}', 'title': f'并发修订 {i}'}
        variants.append(TaskBookInput.model_validate(value))
    def edit(value):
        try:
            return service.save(pid, value, first['id'])
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(edit, variants))
    assert sum(r is not None for r in results) == 1
    assert service.get(pid, first['id'])['revision'] == 2
    restarted = TaskBookService(type(service.store)(tmp_path/'eeas.sqlite3', service.store.source_root))
    assert restarted.get(pid, first['id']) == service.get(pid, first['id'])
    with service.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_taskbook_versions').fetchone()[0] == 2
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}


def test_concurrent_idempotent_create_and_changed_request_are_atomic(client):
    pid, _, body = prepared(client)
    service = client.app.state.taskbooks
    model = TaskBookInput.model_validate(body)
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: service.save(pid, model), range(5)))
    assert all(value == results[0] for value in results)
    with pytest.raises(Conflict, match='IDEMPOTENCY_CONFLICT'):
        service.save(pid, TaskBookInput.model_validate({**body, 'title': '同键不同内容'}))
    with service.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_taskbooks').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM project_taskbook_operations').fetchone()[0] == 1


def test_source_change_marks_old_book_and_rejects_new_edit_without_mutating_history(client):
    pid, doc, body = prepared(client)
    first = book(client, pid, body)
    changed = client.put(f'/api/projects/{pid}/documents/{doc["id"]}',
        files={'file': ('新修订.md', '新来源。'.encode())}, data={'expected_revision': '1'})
    assert changed.status_code == 200
    old = client.get(f'/api/projects/{pid}/taskbooks/{first["id"]}').json()
    assert old['review_status'] == 'NEEDS_REVIEW' and old['payload'] == first['payload']
    value = {**body, 'expected_revision': 1, 'idempotency_key': 'stale-edit'}
    assert client.put(f'/api/projects/{pid}/taskbooks/{first["id"]}', json=value).status_code == 409
    assert client.post(f'/api/projects/{pid}/taskbooks', json=body).json() == first
    with client.app.state.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_taskbook_versions').fetchone()[0] == 1


def test_binary_template_missing_mapping_duplicate_sections_and_forged_origin_reject(client):
    pid, _, body = prepared(client)
    value = copy.deepcopy(body)
    value['sections'][1]['section_id'] = value['sections'][0]['section_id']
    assert client.post(f'/api/projects/{pid}/taskbooks', json=value).status_code == 422
    value = copy.deepcopy(body); value['sections'][0]['entries'][0]['origin'] = 'MODEL_APPROVED_FACT'
    assert client.post(f'/api/projects/{pid}/taskbooks', json=value).status_code == 422
    template = client.post(f'/api/projects/{pid}/documents', files={'file': ('malformed.md', b'{{taskbook:one}} {{taskbook:bad.dot}}')}).json()
    value = copy.deepcopy(body)
    value['template_ref'] = {'document_id': template['id'], 'revision': 1, 'sha256': template['sha256']}
    value['template_mapping'] = [{'slot_id': 'one', 'section_ids': [s['section_id'] for s in value['sections']]}]
    assert client.post(f'/api/projects/{pid}/taskbooks', json=value).status_code == 422
    assert client.get('/api/taskbook-template').json()['component']['version'] == 'v1.1.3'


def test_explicit_assumptions_missing_sections_and_no_automatic_clear_status(client):
    pid, _, body = prepared(client)
    section = body['sections'][-1]
    section['entries'] = [{'entry_id': 'accepted-original', 'text': '用户接受此假设；不当作原文事实。',
        'origin': 'ACCEPTED_ASSUMPTION', 'acceptance_reason': '用户本次明确确认这个假设', 'source_anchors': []}]
    value = book(client, pid, body)
    assert value['payload']['clarity_status'] == 'UNASSESSED'
    assert not value['payload']['implementation_started']
    assert 'scope' in value['payload']['missing_sections']
    assert value['payload']['sections'][-1]['entries'][0]['acceptance_reason']
    invalid = copy.deepcopy(body); del invalid['sections'][-1]['entries'][0]['acceptance_reason']
    assert client.post(f'/api/projects/{pid}/taskbooks', json=invalid).status_code == 422


def test_schema17_database_real_migration_retains_original_rows_and_immutability(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = tmp_path/'old17.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)')
        for path in sorted((root/'packages/domain/migrations').glob('*.sql')):
            if path.name[:3] > '017':
                continue
            connection.executescript(path.read_text())
            connection.execute('INSERT INTO schema_migrations VALUES (?)', (path.name,))
        connection.execute('INSERT INTO projects VALUES (?,?,?,?)', ('original-project', '原schema17项目', '原记录不改写', '2026-10-12'))
        original = connection.execute('SELECT * FROM projects').fetchall()
    from packages.domain.tasks import TaskService
    upgraded = TaskService(database, root)
    with upgraded.connect() as connection:
        assert [tuple(r) for r in connection.execute('SELECT * FROM projects')] == original
        assert connection.execute('SELECT count(*) FROM schema_migrations WHERE version LIKE "018_%"').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM project_taskbooks').fetchone()[0] == 0
    reopened = TaskService(database, root)
    assert reopened.project('original-project')['description'] == '原记录不改写'


def test_taskbook_versions_and_operations_cannot_update_or_delete(client):
    pid, _, body = prepared(client); first = book(client, pid, body)
    for statement in ('UPDATE project_taskbook_versions SET sha256="forged"',
                      'DELETE FROM project_taskbook_versions', 'DELETE FROM project_taskbook_operations',
                      'UPDATE project_taskbook_operations SET sha256="forged"'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            with client.app.state.store.connect() as connection:
                connection.execute(statement)
    assert client.get(f'/api/projects/{pid}/taskbooks/{first["id"]}').json() == first
