"""Actual ASGI/SQLite empty chats and their ordinary organization, without an execution."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

from test_conversation_directory import api, check, project, native_record, forbid
from test_native_file_continuity_api import app_client


def create(client, key='create_chat', pid=None, title=None):
    return client.post('/api/conversations', json={'operation_id': key, 'project_id': pid, 'title': title})


def organize(client, row, action, key, **extra):
    return client.post('/api/conversations/' + row['id'] + '/organization', json={
        'operation_id': key, 'action': action, 'source': row['source'],
        'expected_revision': row['organization']['revision'], **extra})


def test_new_chat_is_only_metadata_and_empty_history_reads_no_file_or_owner(api, monkeypatch):
    monkeypatch.setattr(api.app.state.agent_sessions, 'open', forbid)
    monkeypatch.setattr(api.app.state.conversation_history, '_native_rows', forbid)
    response = create(api); check(response.status_code == 200, response.text)
    receipt = response.json(); ident = receipt['conversation_ref']['id']
    check(receipt['model_calls'] == receipt['messages_created'] == 0 and not receipt['execution_started'])
    rows = api.get('/api/conversations').json()['items']; check(len(rows) == 1)
    row = rows[0]; check(row['id'] == ident and row['project_id'] is None)
    check(row['source']['kind'] == 'conversation' and 'last_operation_id' not in row['source'])
    selected = api.post('/api/conversations/' + ident + '/selection', json={'source': row['source']})
    check(selected.status_code == 200, selected.text)
    scope = selected.json(); check(scope['sources'][0]['original_source'] is None)
    check(scope['metadata_source'] == row['source'] and scope['next_message'] == receipt['next_message'])
    history = api.get('/api/conversations/' + ident + '/history').json()
    check(history['messages'] == [] and history['coverage']['source_bytes_read'] == 0)
    check(history['coverage']['source_reads'] == 0 and history['coverage']['model_calls'] == 0)
    check(create(api).json() == receipt)
    check(api.get('/api/conversation-creations/create_chat').json() == receipt)
    with api.app.state.store.connect() as connection:
        check(connection.execute('SELECT count(*) FROM conversation_threads').fetchone()[0] == 1)
        check(connection.execute('SELECT count(*) FROM conversation_source_aliases WHERE source_json IS NOT NULL').fetchone()[0] == 0)
        check(connection.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0)


def test_empty_chat_organization_move_archive_restore_delete_keeps_its_identity(api):
    pid = project(api, '目标工程'); receipt = create(api).json(); ident = receipt['conversation_ref']['id']
    row = api.get('/api/conversations').json()['items'][0]
    moved = organize(api, row, 'move', 'move_empty', target_project_id=pid)
    check(moved.status_code == 200, moved.text)
    check(api.post('/api/conversations/' + ident + '/selection', json={'source': row['source']}).status_code == 409)
    row = api.get('/api/conversations').json()['items'][0]
    check(row['id'] == ident and row['project_id'] == pid and row['source']['project_id'] == pid)
    check(organize(api, row, 'archive', 'archive_empty').status_code == 200)
    check(api.get('/api/conversations').json()['items'] == [])
    row = api.get('/api/conversations?state=archived').json()['items'][0]
    check(organize(api, row, 'restore', 'restore_empty').status_code == 200)
    row = api.get('/api/conversations').json()['items'][0]
    check(organize(api, row, 'delete', 'delete_empty', delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES').status_code == 200)
    check(api.get('/api/conversations').json()['items'] == [])
    check(api.get('/api/conversations/' + ident + '/scope').status_code == 404)
    # Recovering a creation reads the original result; it cannot resurrect deletion.
    check(api.get('/api/conversation-creations/create_chat').json() == receipt)
    check(create(api).json() == receipt)
    check(api.get('/api/conversations').json()['items'] == [])


def test_creation_key_cannot_change_target_or_title_and_same_named_user_project_is_not_internal(api):
    pid = project(api, '原生只读执行记录')
    first = create(api, pid=pid, title='中文 😀'); check(first.status_code == 200)
    check(create(api, pid=None, title='中文 😀').status_code == 409)
    check(create(api, pid=pid, title='different').status_code == 409)
    internal = project(api, 'Private storage')
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE projects SET description=? WHERE id=?', ('EEAS_INTERNAL_DESKTOP_RUNTIME_V1', internal))
    check(create(api, key='internal', pid=internal).status_code == 404)
    check(create(api, key='missing', pid='missing').status_code == 404)
    for body in [{'operation_id':'invalid'}, {'operation_id':'invalid','project_id':False},
                 {'operation_id':'invalid','project_id':None,'title':1},
                 {'operation_id':'invalid','project_id':None,'execution_started':True}]:
        check(api.post('/api/conversations', json=body).status_code == 422)


def test_creation_recovery_is_hash_checked_and_one_bad_metadata_header_does_not_hide_legacy(api):
    native_record(api, 'legacy_safe', pid=None, cid='legacy_safe')
    receipt = create(api).json(); ident = receipt['conversation_ref']['id']
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE conversation_creations SET result_sha256=? WHERE conversation_id=?', ('0' * 64, ident))
    check(api.get('/api/conversation-creations/create_chat').status_code == 409)
    check(api.get('/api/conversations/' + ident + '/scope').status_code == 409)
    page = api.get('/api/conversations').json()
    check(page['coverage']['partial'])
    check(any(row['source'].get('conversation_id') == 'legacy_safe' for row in page['items']))
    check(all(row['id'] != ident for row in page['items']))


def test_metadata_source_cannot_forge_project_head_or_file_permission(api):
    create(api); row = api.get('/api/conversations').json()['items'][0]
    for mutate in [lambda source: source.update(project_id='foreign'),
                   lambda source: source['conversation_ref'].update(revision=9),
                   lambda source: source.update(file_write_authorized=True)]:
        forged = copy.deepcopy(row); mutate(forged['source'])
        check(organize(api, forged, 'archive', 'forged').status_code == 409)
    check(api.get('/api/conversations').json()['items'][0]['organization']['revision'] == 1)


def test_parallel_retry_of_original_create_key_commits_one_empty_chat(api):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create(api, key='same_original_key'), range(8)))
    check(all(result.status_code == 200 for result in results))
    check(all(result.json() == results[0].json() for result in results))
    with api.app.state.store.connect() as connection:
        check(connection.execute('SELECT count(*) FROM conversation_creations').fetchone()[0] == 1)
        check(connection.execute('SELECT count(*) FROM conversation_threads').fetchone()[0] == 1)
        check(connection.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0)


def test_new_application_recovers_empty_chat_and_creation_receipt_without_starting_owner(api, monkeypatch):
    receipt = create(api, title='可重启的空会话').json()
    settings = api.app.state.settings
    with app_client(settings) as restarted:
        monkeypatch.setattr(restarted.app.state.agent_sessions, 'open', forbid)
        monkeypatch.setattr(restarted.app.state.conversation_history, '_native_rows', forbid)
        check(restarted.get('/api/conversation-creations/create_chat').json() == receipt)
        rows = restarted.get('/api/conversations').json()['items']
        check(len(rows) == 1 and rows[0]['title'] == '可重启的空会话')
        ident = receipt['conversation_ref']['id']
        check(restarted.get('/api/conversations/' + ident + '/scope').status_code == 200)
        check(restarted.get('/api/conversations/' + ident + '/history').json()['messages'] == [])
