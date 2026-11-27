"""Real ASGI/SQLite/signed sources and native admission; no model/OS qualification.

API state foundations only. The end-user Move/Delete/continuation UI and
non-native history projections remain separate acceptance requirements.
"""
from copy import deepcopy
import json

from packages.domain.tasks import digest
from packages.domain.store import Conflict
import pytest
from test_conversation_directory import api, check, native_record, project, discovery, unit_session


def organization(client, row, action, operation, **extra):
    return client.post('/api/conversations/' + row['id'] + '/organization', json={
        'operation_id': operation, 'action': action, 'expected_revision': row['organization']['revision'],
        'source': row['source'], **extra})


def item(client, ident, state='all'):
    response = client.get('/api/conversations?state=' + state)
    check(response.status_code == 200, response.text)
    return next(row for row in response.json()['items'] if row['id'] == ident)


def scope(client, ident):
    result = client.get('/api/conversations/' + ident + '/scope')
    check(result.status_code == 200, result.text)
    return result.json()


def send(client, scope, operation, text, **extra):
    return client.post('/api/desktop-host/native-messages', json={
        'operation_id': operation, 'raw_text': text, 'provider_profile_ref': None,
        'conversation_ref': scope['conversation_ref'], **scope['next_message'], **extra})


def test_moved_message_reads_frozen_target_project_metadata_without_old_grants_or_file_reads(api):
    a = api.post('/api/projects', json={'name': 'Source A', 'description': 'OLD_A_CONTEXT'}).json()['id']
    b = api.post('/api/projects', json={'name': 'Target B', 'description': '目标项目说明' * 1300}).json()['id']
    native_record(api, 'context_old_a', pid=a, cid='context_move')
    row = api.get('/api/conversations').json()['items'][0]
    check(organization(api, row, 'move', 'context_to_b', target_project_id=b).status_code == 200)
    current = scope(api, row['id']); metadata = current['project_context']
    check(metadata['snapshot']['project_id'] == b and metadata['snapshot']['name'] == 'Target B')
    check(metadata['snapshot']['description_truncated'] and len(metadata['snapshot']['description'].encode()) <= 16384)
    check(metadata['snapshot_sha256'] == digest(metadata['snapshot']) and not metadata['file_contents_read'])
    check(not metadata['file_write_authorized'] and not metadata['automatic_input_authorized'])
    check(send(api, current, 'context_new_b', 'Use the selected project context').status_code == 200)
    native = api.app.state.native_messages
    bound = {'request': {'operation_id': 'context_new_b'}}
    frozen = native.read_project_context({}, bound)
    check(frozen['snapshot'] == metadata['snapshot'] and frozen['conversation_ref'] == current['conversation_ref'])
    # Current project changes and a second move never rewrite this admitted task.
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE projects SET description=? WHERE id=?', ('LATER_B_NOT_FOR_OLD_TASK', b))
    check(scope(api, row['id'])['project_context']['snapshot']['description'] == 'LATER_B_NOT_FOR_OLD_TASK')
    check(organization(api, item(api, row['id']), 'move', 'context_to_chats', target_project_id=None).status_code == 200)
    check(native.read_project_context({}, bound) == frozen)
    chats = scope(api, row['id'])
    check(chats['project_context']['snapshot']['kind'] == 'CHATS' and chats['project_context']['snapshot']['project_id'] is None)
    check(send(api, chats, 'context_new_chats', 'Continue in Chats').status_code == 200)
    check(native.read_project_context({}, {'request': {'operation_id': 'context_new_chats'}})['snapshot']['kind'] == 'CHATS')
    with pytest.raises(Conflict):
        native.read_project_context({}, {'request': {'operation_id': 'context_old_a'}})
    record = native.ws._read(native.path('context_new_b'))
    record['conversation_scope']['project_context']['snapshot']['description'] = 'INJECTED_LATER_CONTEXT'
    native.ws._write(native.path('context_new_b'), record)
    with pytest.raises(Conflict):
        native.read_project_context({}, bound)


def test_observation_does_not_create_entities_and_legacy_archive_promotes_lazily(api):
    native_record(api, 'observe_legacy', pid=None, cid='legacy_chat')
    row = api.get('/api/conversations').json()['items'][0]
    with api.app.state.store.connect() as connection:
        check(connection.execute('SELECT count(*) FROM conversation_threads').fetchone()[0] == 0)
    before = api.app.state.native_messages.path('observe_legacy').read_bytes()
    response = organization(api, row, 'archive', 'archive_promote')
    check(response.status_code == 200, response.text)
    current = item(api, row['id']); check(current['id'] == row['id'] and current['source'] == row['source'])
    check(current['organization'] == {'revision': 1, 'archived': True})
    ref = scope(api, row['id']); check(ref['next_message'] == {'project_id': None, 'conversation_id': 'legacy_chat'})
    check(ref['conversation_ref'] == {'id': row['id'], 'revision': 1})
    check(not ref['execution_started'] and not ref['files_moved'] and not ref['file_write_authorized'])
    check(api.app.state.native_messages.path('observe_legacy').read_bytes() == before)


def test_move_every_legacy_kind_changes_organization_preserves_old_bytes_and_default_chat_filters(api):
    a, b = project(api, '同名项目'), project(api, '同名项目')
    native_record(api, 'move_native', pid=a, cid='move_discussion')
    original, opid, sid, _ = unit_session(api, guided=False)
    discovery(api, a)
    rows = api.get('/api/conversations').json()['items']; check(len(rows) == 3)
    saved = {path: path.read_bytes() for path in [api.app.state.native_messages.path('move_native'),
        original._path(opid, sid, 'manifest.json'), original._path(opid, sid, 'state.json')]}
    for index, row in enumerate(rows):
        result = organization(api, row, 'move', 'move_all_' + str(index), target_project_id=b)
        check(result.status_code == 200, result.text)
        receipt = result.json(); check(receipt['project_id'] == b and receipt['version'] == 2)
        check(not receipt['existing_execution_scope_changed'] and not receipt['execution_stop_requested'])
        current = item(api, row['id'])
        check(current['project_id'] == b and current['source'] == row['source'])
        check(scope(api, row['id'])['next_message']['project_id'] == b)
    target = api.get('/api/conversations?scope=project&project_id=' + b).json()['items']; check(len(target) == 3)
    check(not api.get('/api/conversations?scope=project&project_id=' + a).json()['items'])
    for index, row in enumerate(target):
        result = organization(api, row, 'move', 'move_to_chats_' + str(index), target_project_id=None)
        check(result.status_code == 200, result.text)
    unbound = api.get('/api/conversations?scope=unbound').json()['items']; check(len(unbound) == 3)
    check(all(row['project_id'] is None and row['source']['project_id'] is not None for row in unbound))
    check(all(path.read_bytes() == raw for path, raw in saved.items()))


def test_native_move_back_preserves_identity_and_history_and_does_not_merge_same_named_discussions(api):
    a, b = project(api), project(api)
    native_record(api, 'a_old', pid=a, cid='same_cid')
    native_record(api, 'b_unrelated', pid=b, cid='same_cid')
    rows = api.get('/api/conversations').json()['items']; original = next(row for row in rows if row['project_id'] == a)
    check(organization(api, original, 'move', 'a_to_b', target_project_id=b).status_code == 200)
    first = scope(api, original['id']); check(first['next_message']['conversation_id'] != 'same_cid')
    result = send(api, first, 'after_move', 'Continue after moving; do not borrow old file permissions.')
    check(result.status_code == 200 and result.json()['status'] == 'NOT_CONFIGURED', result.text)
    current = item(api, original['id']); check(current['source']['project_id'] == b)
    check(current['source']['last_operation_id'] == 'after_move')
    native = api.app.state.native_messages
    source = native.ws._read(native.path('after_move')); check(source['request']['project_id'] == b)
    check(source['conversation_scope']['conversation_ref'] == first['conversation_ref'])
    history = native.read_history({}, {'request': {'operation_id': 'after_move'}})
    check([row['operation_id'] for row in history['messages']] == ['a_old'], history)
    check(history['automatic_input_authorized'] is False)
    # A stale original-side send is refused; the immutable prior operation remains inspectable.
    stale = api.post('/api/desktop-host/native-messages', json={'operation_id': 'wrong_after_move',
        'raw_text': 'Never dispatch here', 'project_id': a, 'conversation_id': 'same_cid'})
    check(stale.status_code == 409, stale.text)
    check(not native.path('wrong_after_move').exists())
    check(organization(api, current, 'move', 'a_back', target_project_id=a).status_code == 200)
    back = scope(api, original['id']); check(back['next_message'] == {'project_id': a, 'conversation_id': 'same_cid'})
    check(send(api, first, 'stale_revision', 'Old revision').status_code == 409)
    next_reply = send(api, back, 'after_back', 'Both earlier turns belong to this same conversation.')
    check(next_reply.status_code == 200, next_reply.text)
    history = native.read_history({}, {'request': {'operation_id': 'after_back'}})
    check({row['operation_id'] for row in history['messages']} == {'a_old', 'after_move'}, history)
    check(len(api.get('/api/conversations').json()['items']) == 2)


def test_move_idempotency_and_revision_conflict_and_invalid_target_are_transactional(api):
    a, b = project(api), project(api); native_record(api, 'recover_move', pid=a, cid='recover_move')
    row = api.get('/api/conversations').json()['items'][0]
    first = organization(api, row, 'move', 'same_move', target_project_id=b)
    check(first.status_code == 200, first.text)
    check(organization(api, row, 'move', 'same_move', target_project_id=b).json() == first.json())
    check(organization(api, row, 'move', 'same_move', target_project_id=None).status_code == 409)
    check(organization(api, row, 'archive', 'other_window').status_code == 409)
    check(api.get('/api/conversation-organization/operations/same_move').json() == first.json())
    before = scope(api, row['id'])
    check(organization(api, item(api, row['id']), 'move', 'invalid_project', target_project_id='missing').status_code == 404)
    check(scope(api, row['id']) == before)
    with api.app.state.store.connect() as connection:
        check(connection.execute('SELECT count(*) FROM conversation_organization_operations').fetchone()[0] == 1)


def test_delete_is_irreversible_conversation_removal_and_retains_separate_task_records_and_files(api):
    pid = project(api); native = api.app.state.native_messages
    native_record(api, 'delete_chat', pid=pid, cid='delete_chat')
    row = api.get('/api/conversations').json()['items'][0]
    saved = native.path('delete_chat').read_bytes()
    rejected = organization(api, row, 'delete', 'unconfirmed_delete')
    check(rejected.status_code == 422)
    result = organization(api, row, 'delete', 'confirmed_delete', delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES')
    check(result.status_code == 200, result.text)
    receipt = result.json();check(receipt['conversation_removed'] and receipt['execution_records_retained'] and receipt['project_files_retained'])
    check(not receipt['execution_stop_requested'])
    check(not api.get('/api/conversations?state=all').json()['items'])
    check(api.get('/api/conversations/' + row['id'] + '/scope').status_code == 404)
    check(native.path('delete_chat').read_bytes() == saved)
    check(organization(api, row, 'delete', 'confirmed_delete', delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES').json() == receipt)
    stale = deepcopy(row);stale['organization'] = {'revision': 1, 'archived': False}
    check(organization(api, stale, 'archive', 'cannot_recreate').status_code == 409)
    check(api.post('/api/desktop-host/native-messages', json={'operation_id': 'post_deleted',
        'raw_text': 'Must not recreate this conversation', 'project_id': pid, 'conversation_id': 'delete_chat'}).status_code == 404)
    # Finishing an already admitted old task does not recreate a deleted chat.
    native_record(api, 'old_task_late', pid=pid, cid='delete_chat')
    check(not api.get('/api/conversations?state=all').json()['items'])


def test_request_hash_compatibility_and_explicit_scope_validation(api):
    from apps.api.native_messages import NativeQuestionInput
    from apps.api.conversation_organization import OrganizationChange
    old = {'operation_id': 'old', 'raw_text': 'Literal user JSON: {"keep":true}', 'conversation_id': 'same'}
    body = NativeQuestionInput.model_validate(old).request_dict();check('conversation_ref' not in body)
    check(NativeQuestionInput.model_validate({**old, 'conversation_ref': None}).request_dict() == body)
    native_record(api, 'input_validation', pid=None, cid='input_validation');row=api.get('/api/conversations').json()['items'][0]
    base={'operation_id':'bad','expected_revision':0,'source':row['source']}
    for extra in [{'action':'move'}, {'action':'archive','target_project_id':None},
        {'action':'move','target_project_id':False}, {'action':'move','target_project_id':'../other'},
        {'action':'delete','delete_scope':'DELETE_ALL_PROJECT_FILES'}, {'action':'archive','delete_scope':None}]:
        check(api.post('/api/conversations/'+row['id']+'/organization',json={**base,**extra}).status_code==422)
    check(OrganizationChange.model_validate({**base,'action':'archive'}).request_dict()=={**base,'action':'archive'})


def test_aged_move_stays_visible_with_real_candidate_limits_and_source_alias_corruption_fails_closed(api):
    import os
    import time
    from apps.api.conversation_directory import READ_LIMIT
    from packages.domain.tasks import canonical
    a,b=project(api,'原项目'),project(api,'目标项目')
    native=api.app.state.native_messages;native_record(api,'aged_origin',pid=a,cid='aged_discussion')
    path=native.path('aged_origin');raw=path.read_bytes();os.utime(path,(100,100))
    row=api.get('/api/conversations').json()['items'][0]
    moved=organization(api,row,'move','aged_move',target_project_id=b);check(moved.status_code==200,moved.text)
    # Actual malformed records are intentionally older than the explicit move
    # but newer than its original execution file. No internal limit is mocked.
    stamp=time.time()-3600
    for index in range(READ_LIMIT+1):
        stale=native.path('unavailable_header_'+str(index));stale.write_text('{}');os.utime(stale,(stamp,stamp))
    listing=api.get('/api/conversations?scope=project&project_id='+b).json()
    actual=next(value for value in listing['items'] if value['id']==row['id'])
    check(actual['read_only'] and actual['source']['project_id']==a and actual['project_id']==b)
    check(actual['conversation_origin']=={'kind':'native','project_id':a,'conversation_id':'aged_discussion'})
    check(listing['coverage']['partial'] and listing['coverage']['candidates_read']<=READ_LIMIT and listing['coverage']['model_calls']==0)
    check(path.read_bytes()==raw and path.stat().st_mtime==100,'The source timestamp and content must remain unchanged')
    with api.app.state.store.connect() as connection:
        alias=dict(connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?',(row['id'],)).fetchone())
        bad=json.loads(alias['identity_json']);bad['project_id']=b
        connection.execute('UPDATE conversation_source_aliases SET identity_json=? WHERE alias_id=?',(canonical(bad),row['id']))
    check(api.get('/api/conversations/'+row['id']+'/scope').status_code==409)
    with api.app.state.store.connect() as connection:
        source=json.loads(alias['source_json']);source['project_id']=b
        connection.execute('UPDATE conversation_source_aliases SET identity_json=?,source_json=?,source_sha256=? WHERE alias_id=?',
            (alias['identity_json'],canonical(source),digest(source),row['id']))
    check(api.get('/api/conversations/'+row['id']+'/scope').status_code==409,'Even a matching payload hash cannot transplant an alias source identity')
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE conversation_source_aliases SET identity_json=?,source_json=?,source_sha256=? WHERE alias_id=?',
            (alias['identity_json'],alias['source_json'],alias['source_sha256'],row['id']))
    check(scope(api,row['id'])['next_message']['project_id']==b)


def test_managed_metadata_budget_counts_actual_source_bytes_and_a_bad_alias_does_not_hide_other_sources(api):
    from packages.domain.tasks import canonical
    pid=project(api);native=api.app.state.native_messages
    native_record(api,'budget_source_a',pid=pid,cid='budget_a')
    native_record(api,'budget_source_b',pid=pid,cid='budget_b')
    rows=api.get('/api/conversations').json()['items']
    for index,row in enumerate(rows):check(organization(api,row,'archive','budget_archive_'+str(index)).status_code==200)
    row=rows[0];record=native.ws._read(native.path(row['source']['last_operation_id']))
    record['history_padding']='observable host fixture metadata '*6000
    native.ws._write(native.path(row['source']['last_operation_id']),record)
    owner=api.app.state.conversation_directory
    errors=__import__('collections').Counter();headers=__import__('collections').Counter()
    candidates=list(owner._candidates({'kind':'all','scope':'all','project_id':None},errors,headers))
    planned=next(candidate for _,candidate in candidates if candidate[0]=='organized' and candidate[3]==row['id'])
    actual_bytes=native.path(row['source']['last_operation_id']).stat().st_size
    check(planned[-1]==actual_bytes*2 and actual_bytes>100000,'A small stored ref cannot stand in for the actual read body budget')
    with api.app.state.store.connect() as connection:
        old=connection.execute('SELECT identity_json FROM conversation_source_aliases WHERE alias_id=?',(row['id'],)).fetchone()[0]
        malformed=json.loads(old);malformed['conversation_id']='unrelated'
        connection.execute('UPDATE conversation_source_aliases SET identity_json=? WHERE alias_id=?',(canonical(malformed),row['id']))
    listing=api.get('/api/conversations?state=all').json()
    check(len(listing['items'])==1 and listing['items'][0]['id']==rows[1]['id'])
    check(listing['coverage']['partial'] and any(value['source']=='organization' for value in listing['coverage']['errors']))
