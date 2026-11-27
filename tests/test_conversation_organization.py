"""Actual ASGI/SQLite/signed metadata organization; no model or OS access."""
import copy
import json
from pathlib import Path
import sqlite3
import pytest
from packages.domain.tasks import digest

from test_conversation_directory import api, check, native_record, project, discovery, unit_session, forbid


def test_existing_schema_upgrade_preserves_projects_and_is_repeatable(tmp_path):
    database = tmp_path / 'previous.sqlite3'
    migrations = Path(__file__).parents[1] / 'packages/domain/migrations'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)')
        for path in sorted(migrations.glob('*.sql')):
            if path.name >= '026_':
                break
            connection.executescript(path.read_text())
            connection.execute('INSERT INTO schema_migrations VALUES (?)', (path.name,))
        connection.execute('INSERT INTO projects VALUES (?,?,?,?)',
                           ('existing_project', '保留的项目', '保留用户资料', '2026-10-19T00:00:00Z'))
    from packages.domain.store import Store
    from apps.api.conversation_organization import organization_state
    for _ in range(2):
        store = Store(database)
        with store.connect() as connection:
            project_row = dict(connection.execute('SELECT * FROM projects WHERE id=?', ('existing_project',)).fetchone())
            check(project_row['name'] == '保留的项目' and project_row['description'] == '保留用户资料')
            check(connection.execute('SELECT count(*) FROM conversation_organization').fetchone()[0] == 0)
            check(connection.execute('SELECT count(*) FROM schema_migrations WHERE version=?',
                                     ('026_conversation_organization.sql',)).fetchone()[0] == 1)
            check(connection.execute('SELECT count(*) FROM schema_migrations WHERE version=?',
                                     ('030_conversation_listing_observations.sql',)).fetchone()[0] == 1)
        check(organization_state(store, 'native:' + 'a'*64) == {'revision': 0, 'archived': False})


def change(client, row, action, operation, revision=None):
    return client.post('/api/conversations/' + row['id'] + '/organization', json={
        'operation_id': operation, 'action': action,
        'expected_revision': row['organization']['revision'] if revision is None else revision,
        'source': row['source']})


def test_archive_restore_all_kinds_preserves_identity_sources_files_and_running_owners(api, monkeypatch):
    pid = project(api); native_record(api, 'archive_native', pid=None, cid='free_chat')
    owner, original_pid, sid, state = unit_session(api, guided=False)
    discovery(api, pid)
    rows = api.get('/api/conversations').json()['items']; check(len(rows) == 3)
    original = owner._path(original_pid, sid, 'state.json'); saved = original.read_bytes()
    native = api.app.state.native_messages.path('archive_native'); native_saved = native.read_bytes()
    monkeypatch.setattr(owner, 'get', forbid)
    monkeypatch.setattr(api.app.state.discovery, 'get', forbid)
    for index, row in enumerate(rows):
        response = change(api, row, 'archive', f'archive_{index}')
        check(response.status_code == 200, response.text); receipt = response.json()
        check(receipt['organization'] == {'revision': 1, 'archived': True})
        check(receipt['conversation_id'] == row['id'] and receipt['project_id'] == row['project_id'])
        check(not receipt['execution_authorized'] and not receipt['transcript_changed'] and not receipt['files_changed'])
    check(not api.get('/api/conversations').json()['items'])
    archived = api.get('/api/conversations?state=archived').json()['items']; check(len(archived) == 3)
    for index, row in enumerate(archived):
        response = change(api, row, 'restore', f'restore_{index}')
        check(response.status_code == 200, response.text)
    restored = api.get('/api/conversations').json()['items']; check(len(restored) == 3)
    for previous in rows:
        actual = next(row for row in restored if row['id'] == previous['id'])
        check(actual['source'] == previous['source'] and actual['project_id'] == previous['project_id'])
        check(actual['organization'] == {'revision': 2, 'archived': False})
    check(original.read_bytes() == saved and native.read_bytes() == native_saved)


def test_archive_applies_to_whole_discussion_after_new_turn_and_survives_new_directory_instance(api):
    native_record(api, 'first_turn', pid=None, cid='same_discussion')
    row = api.get('/api/conversations').json()['items'][0]
    check(change(api, row, 'archive', 'archive_whole').status_code == 200)
    with api.app.state.store.connect() as connection:
        original_alias = dict(connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?',
                                                (row['id'],)).fetchone())
    native_record(api, 'new_turn', pid=None, cid='same_discussion')
    check(not api.get('/api/conversations').json()['items'])
    from apps.api.conversation_directory import ConversationDirectory
    owner = ConversationDirectory(api.app)
    archived = owner.read(state='archived')['items']; check(len(archived) == 1)
    check(archived[0]['id'] == row['id'] and archived[0]['source']['last_operation_id'] == 'new_turn')
    check(change(api, archived[0], 'restore', 'restore_whole').status_code == 200)
    check(api.get('/api/conversations').json()['items'][0]['source']['last_operation_id'] == 'new_turn')
    # A current listing head must not rewrite the immutable execution source.
    with api.app.state.store.connect() as connection:
        check(dict(connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?',
                                      (row['id'],)).fetchone()) == original_alias)
        observation = connection.execute('SELECT * FROM conversation_listing_observations WHERE conversation_id=?',
                                         (row['id'],)).fetchone()
        check(json.loads(observation['source_json'])['last_operation_id'] == 'new_turn')
    again = ConversationDirectory(api.app).read()['items'][0]
    check(again['source']['last_operation_id'] == 'new_turn')


def test_same_key_recovery_conflicting_windows_and_unavailable_source_do_not_repeat_or_grant(api):
    native_record(api, 'recovery_turn', pid=None, cid='recover')
    row = api.get('/api/conversations').json()['items'][0]
    first = change(api, row, 'archive', 'recover_archive'); check(first.status_code == 200, first.text)
    check(change(api, row, 'archive', 'recover_archive').json() == first.json())
    check(change(api, row, 'restore', 'recover_archive', 1).status_code == 409)
    check(change(api, row, 'archive', 'other_window').status_code == 409)
    api.app.state.native_messages.path('recovery_turn').unlink()
    check(change(api, row, 'archive', 'recover_archive').json() == first.json(), 'Committed receipt does not require an online owner')
    result = api.get('/api/conversation-organization/operations/recover_archive')
    check(result.status_code == 200 and result.json() == first.json())
    check(api.get('/api/conversation-organization/operations/unknown').status_code == 404)
    check(change(api, row, 'restore', 'missing_reference', 1).status_code == 409)
    with api.app.state.store.connect() as connection:
        count = connection.execute('SELECT count(*) FROM conversation_organization_operations').fetchone()[0]
    check(count == 1)


def test_listing_observation_corruption_cannot_retarget_a_chat_or_hide_other_valid_sources(api):
    native_record(api, 'listing_a', pid=None, cid='listing_a')
    native_record(api, 'listing_b', pid=None, cid='listing_b')
    rows = api.get('/api/conversations').json()['items']
    a = next(row for row in rows if row['source']['conversation_id'] == 'listing_a')
    b = next(row for row in rows if row['source']['conversation_id'] == 'listing_b')
    check(change(api, a, 'archive', 'listing_archive').status_code == 200)
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE conversation_listing_observations SET source_json=?,source_sha256=? '
            'WHERE conversation_id=?', (json.dumps(b['source']), digest(b['source']), a['id']))
    result = api.get('/api/conversations?state=all').json()
    check(result['coverage']['partial'])
    check(any(error['source'] == 'organization' for error in result['coverage']['errors']))
    actual_b = next(row for row in result['items'] if row['id'] == b['id'])
    check(actual_b['source'] == b['source'] and not actual_b['organization']['archived'])
    check(all(row['source']['conversation_id'] == 'listing_a' for row in result['items'] if row['id'] == a['id']))


def test_listing_observation_size_limit_fails_transactionally_without_rewriting_frozen_alias(api):
    native_record(api, 'listing_bounded', pid=None, cid='listing_bounded')
    row = api.get('/api/conversations').json()['items'][0]
    check(change(api, row, 'archive', 'bounded_archive').status_code == 200)
    with api.app.state.store.connect() as connection:
        before = dict(connection.execute('SELECT * FROM conversation_listing_observations').fetchone())
        alias = dict(connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?', (row['id'],)).fetchone())
    with pytest.raises(sqlite3.IntegrityError):
        with api.app.state.store.connect() as connection:
            connection.execute('UPDATE conversation_listing_observations SET source_json=?', ('x' * 8193,))
    with api.app.state.store.connect() as connection:
        check(dict(connection.execute('SELECT * FROM conversation_listing_observations').fetchone()) == before)
        check(dict(connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?', (row['id'],)).fetchone()) == alias)


def test_forged_source_wrong_target_stale_basis_and_invalid_input_never_modify_organization(api):
    pid = project(api); native_record(api, 'safe_source', pid=pid, cid='safe')
    native_record(api, 'other_source', pid=None, cid='other')
    rows = api.get('/api/conversations').json()['items']; a = next(r for r in rows if r['project_id'] == pid)
    b = next(r for r in rows if r['project_id'] is None)
    wrong = copy.deepcopy(a); wrong['source'] = b['source']
    check(change(api, wrong, 'archive', 'wrong_target').status_code == 409)
    for key, value in [('project_id', None), ('request_sha256', '0'*64), ('last_operation_id', '../safe_source')]:
        forged = copy.deepcopy(a); forged['source'][key] = value
        check(change(api, forged, 'archive', 'forged_' + key).status_code == 409)
    target = '/api/conversations/' + a['id'] + '/organization'
    for body in [
        {'operation_id':'bad','action':'delete','expected_revision':0,'source':a['source']},
        {'operation_id':'bad','action':'archive','expected_revision':True,'source':a['source']},
        {'operation_id':'bad','action':'archive','expected_revision':0,'source':a['source'],'permission':'allow'},
        {'operation_id':'bad','action':'archive','expected_revision':0,'source':{'padding':'x'*9000}},
    ]:
        check(api.post(target, json=body).status_code == 422)
    check(all(row['organization']['revision'] == 0 for row in api.get('/api/conversations').json()['items']))


def test_archived_filters_have_separate_cursors_and_commit_receipt_corruption_is_reported(api):
    for index in range(4):
        native_record(api, 'paged_' + str(index), pid=None, cid='discussion_' + str(index))
    rows = api.get('/api/conversations').json()['items']
    for index, row in enumerate(rows[:2]):
        check(change(api, row, 'archive', 'paged_archive_' + str(index)).status_code == 200)
    first = api.get('/api/conversations?state=archived&limit=1').json()
    check(first['next_cursor'] and len(first['items']) == 1)
    check(api.get('/api/conversations', params={'cursor': first['next_cursor']}).status_code == 422)
    second = api.get('/api/conversations', params={'state':'archived','limit':1,'cursor':first['next_cursor']}).json()
    check(second['items'] and second['items'][0]['id'] != first['items'][0]['id'])
    check(len(api.get('/api/conversations?state=all').json()['items']) == 4)
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE conversation_organization_operations SET result_json=? WHERE operation_id=?',
                           (json.dumps({'forged': True}), 'paged_archive_0'))
    check(api.get('/api/conversation-organization/operations/paged_archive_0').status_code == 409)
