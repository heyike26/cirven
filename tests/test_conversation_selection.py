"""Selection is metadata enrollment; saved SDK state is UNIT evidence only."""
from copy import deepcopy

import pytest

from test_conversation_directory import api, check, forbid, unit_session, native_record, discovery, project
from test_conversation_threads import item, organization


def select(api, row):
    return api.post('/api/conversations/' + row['id'] + '/selection', json={'source': row['source']})


@pytest.mark.parametrize('marker', ['eeas.original-agent-session.internal-project.v1', 'EEAS_INTERNAL_DESKTOP_RUNTIME_V1'])
def test_default_public_chat_selects_same_identity_without_exposing_storage_project_or_connecting_owner(api, monkeypatch, marker):
    owner, pid, sid, _ = unit_session(api, guided=False)
    store = api.app.state.store
    with store.connect() as connection:
        connection.execute('UPDATE projects SET description=? WHERE id=?', (marker, pid))
    paths = list(owner._path(pid, sid).rglob('*.json'))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    monkeypatch.setattr(owner, 'get', forbid)
    monkeypatch.setattr(owner, 'inspect', forbid)
    monkeypatch.setattr(owner, 'open', forbid)
    row = api.get('/api/conversations?scope=unbound').json()['items'][0]
    check(row['project_id'] is None and row['project_name'] is None and row['source']['project_id'] == pid)
    check(row['project_assignment'] == 'DEFAULT_CHATS')
    first = select(api, row)
    check(first.status_code == 200, first.text)
    value = first.json()
    check(value['project_id'] is None and value['conversation_ref'] == {'id': row['id'], 'revision': 1})
    check(not value['archived'] and not value['execution_started'] and not value['files_moved'])
    repeated = select(api, row)
    check(repeated.status_code == 200 and repeated.json() == value, repeated.text)
    listed = item(api, row['id'])
    check(listed['project_id'] is None and listed['project_name'] is None and listed['updated_at'] == row['updated_at'])
    check({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths} == before and not owner.active)


def test_selection_retains_current_move_and_archive_and_never_restores_deleted_chat(api):
    native_record(api, 'selection_native', pid=None, cid='selection_chat')
    row = api.get('/api/conversations').json()['items'][0]
    check(select(api, row).status_code == 200)
    target = project(api, 'Selection target')
    moved = organization(api, item(api, row['id']), 'move', 'selection_move', target_project_id=target)
    check(moved.status_code == 200, moved.text)
    archived = organization(api, item(api, row['id']), 'archive', 'selection_archive')
    check(archived.status_code == 200, archived.text)
    repeated = select(api, row)
    check(repeated.status_code == 200, repeated.text)
    check(repeated.json()['project_id'] == target and repeated.json()['archived'])
    deleted = organization(api, item(api, row['id']), 'delete', 'selection_delete',
        delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES')
    check(deleted.status_code == 200, deleted.text)
    check(select(api, row).status_code == 409)
    check(api.get('/api/conversations').json()['items'] == [])


def test_selection_supports_all_stored_sources_and_rejects_changed_exact_reference(api):
    pid = project(api, 'User project')
    native_record(api, 'selection_typed_native', pid=pid, cid='selection_native_chat')
    unit_session(api, guided=False)
    discovery(api, pid)
    rows = api.get('/api/conversations').json()['items']
    check({row['kind'] for row in rows} == {'native', 'original', 'discovery'})
    for row in rows:
        response = select(api, row)
        check(response.status_code == 200, response.text)
        history = api.get('/api/conversations/' + row['id'] + '/history')
        check(history.status_code == 200, history.text)
    original = next(row for row in rows if row['kind'] == 'original')
    wrong = deepcopy(original); wrong['source']['session_ref']['sha256'] = 'f' * 64
    check(select(api, wrong).status_code == 409)
    check(api.post('/api/conversations/' + original['id'] + '/selection', json={'source': original['source'], 'restore': True}).status_code == 422)


def test_ordinary_project_with_internal_display_name_remains_a_user_project(api):
    _, pid, _, _ = unit_session(api, guided=False)
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE projects SET name=?,description=? WHERE id=?',
            ('本机工作上下文', 'Human project description', pid))
    row = api.get('/api/conversations').json()['items'][0]
    check(row['project_id'] == pid and 'project_assignment' not in row)
    response = select(api, row)
    check(response.status_code == 200 and response.json()['project_id'] == pid, response.text)
