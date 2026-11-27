"""Actual HTTP/SQLite/signed observations; synthetic saved SDK messages only.

No model, SDK response, original process, Windows or semantic qualification.
"""
from copy import deepcopy
import json

import pytest

from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_conversation_directory import api, check, native_record, project, discovery, unit_session, forbid
from test_conversation_threads import organization, scope, send, item


def history(api, ident, **params):
    result = api.get('/api/conversations/' + ident + '/history', params=params)
    check(result.status_code == 200, result.text)
    return result.json()


@pytest.mark.parametrize('kind', ['native', 'original', 'discovery'])
def test_move_keeps_stored_user_and_answer_sources_in_readonly_api_and_model_history(api, monkeypatch, kind):
    native = api.app.state.native_messages
    if kind == 'native':
        a = project(api); native_record(api, 'history_old', pid=a, cid='history_source')
        expected = '原始用户问题 history_old'
    elif kind == 'original':
        _, a, _, _ = unit_session(api, guided=True)
        expected = '只读核对原任务'
    else:
        a, _ = discovery(api)
        expected = '形成带来源的任务计划'
    b = project(api)
    row = next(row for row in api.get('/api/conversations').json()['items'] if row['kind'] == kind)
    check(organization(api, row, 'move', 'move_history', target_project_id=b).status_code == 200)
    selected = scope(api, row['id'])
    check(send(api, selected, 'after_history_move', '新项目中的下一条').status_code == 200)
    paths = [p for base in (native.ws._path('native-messages'), api.app.state.settings.data_dir / 'agent-sessions')
             for p in base.rglob('*.json') if p.is_file()]
    frozen = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    monkeypatch.setattr(native, 'get', forbid)
    monkeypatch.setattr(native.transport, 'inspect', forbid)
    monkeypatch.setattr(api.app.state.agent_sessions, 'inspect', forbid)
    monkeypatch.setattr(api.app.state.agent_sessions, 'get', forbid)
    view = history(api, row['id'])
    check(view['project_id'] == b and view['conversation_ref'] == selected['conversation_ref'])
    original_rows = [message for message in view['messages'] if message['source']['kind'] == kind and message['source']['project_id'] == a]
    check(any(message['role'] == 'user' and expected in message['text'] for message in original_rows), view)
    check(all(not view[key] for key in ('execution_authorized', 'automatic_input_authorized', 'file_write_authorized')))
    model = native.read_history({}, {'request': {'operation_id': 'after_history_move'}})
    check(any(expected in message['user_text'] and message['source_kind'] == kind for message in model['messages']), model)
    check('NOT_YET_PROJECTED' not in json.dumps(model) and model['source_coverage']['model_calls'] == 0)
    if kind == 'native':
        check(model['messages'][0]['answer'] == '原问题的精确回答 history_old')
    elif kind == 'original':
        check('FINAL_PART_A' in json.dumps(model) and 'FINAL_PART_B' in json.dumps(model))
        check('UNRELATED_RESPONSE' not in json.dumps(model))
    check(all((p.read_bytes(), p.stat().st_mtime_ns) == value for p, value in frozen.items()))


def seed(native, pid, op, cid, text):
    # Saved-record fixture only: pagination does not test admission. Actual
    # HTTP admission is exercised above and by the original-CLI cases.
    from apps.api.native_messages import NativeQuestionInput
    body = NativeQuestionInput.model_validate({'operation_id': op, 'project_id': pid, 'conversation_id': cid,
        'raw_text': text, 'provider_profile_ref': None}).request_dict()
    record = {'request': body, 'request_sha256': digest(body), 'generation': native.generation,
        'status': 'NOT_CONFIGURED', 'execution': None}
    native.ws._write(native.path(op), record)
    return record


def test_history_pages_cross_real_native_candidate_window_without_gaps_and_reject_changed_basis(api):
    pid = project(api); native = api.app.state.native_messages
    seed(native, pid, 'aged_original', 'aged_chat', 'The selected discussion is older than 260 unrelated turns')
    for index in range(260):
        seed(native, pid, 'page_' + str(index), 'paged_chat', 'Saved turn ' + str(index))
    directory = api.app.state.conversation_directory
    aged = directory.resolve({'kind': 'native', 'project_id': pid, 'last_operation_id': 'aged_original'})
    check(organization(api, aged, 'archive', 'promote_aged_chat').status_code == 200)
    check(any(message['operation_id'] == 'aged_original' for message in history(api, aged['id'])['messages']))
    check(send(api, scope(api, aged['id']), 'aged_current', 'Continue the old selected discussion').status_code == 200)
    model = native.read_history({}, {'request': {'operation_id': 'aged_current'}})
    check(any(message['operation_id'] == 'aged_original' for message in model['messages']))
    row = directory.resolve({'kind': 'native', 'project_id': pid, 'last_operation_id': 'page_259'})
    check(organization(api, row, 'archive', 'promote_paged_chat').status_code == 200)
    cursor, seen = None, []
    for _ in range(10):
        view = history(api, row['id'], limit=100, **({'cursor': cursor} if cursor else {}))
        seen.extend(message['operation_id'] for message in view['messages'])
        check(view['coverage']['source_bytes_read'] <= view['coverage']['source_byte_limit'])
        cursor = view['next_cursor']
        if not cursor:
            break
    check(cursor is None and len(seen) == len(set(seen)) == 260)
    first = history(api, row['id'], limit=2)
    check(first['next_cursor'])
    seed(native, pid, 'page_new', 'paged_chat', 'A newly saved user turn')
    changed = api.get('/api/conversations/' + row['id'] + '/history', params={'cursor': first['next_cursor']})
    check(changed.status_code == 409, changed.text)


def test_actual_byte_budget_continuation_never_skips_the_first_unread_record(api):
    pid = project(api); native = api.app.state.native_messages
    for index in range(13):
        op = 'large_' + str(index)
        record = seed(native, pid, op, 'large_saved_chat', 'Keep turn ' + str(index))
        record['saved_fixture_padding'] = 'x' * (1536 * 1024)
        native.ws._write(native.path(op), record)
    row = api.get('/api/conversations').json()['items'][0]
    check(organization(api, row, 'archive', 'promote_large').status_code == 200)
    cursor, seen, bounded = None, [], False
    for _ in range(6):
        page = history(api, row['id'], limit=100, **({'cursor': cursor} if cursor else {}))
        bounded = bounded or any(error['code'] == 'SHARED_READ_BUDGET_REACHED' for error in page['coverage']['errors'])
        check(page['coverage']['source_bytes_read'] <= 16 * 1024 * 1024)
        seen.extend(message['operation_id'] for message in page['messages'])
        cursor = page['next_cursor']
        if not cursor:
            break
    check(bounded and not cursor and len(set(seen)) == len(seen) == 13, seen)


def test_bad_original_source_does_not_hide_healthy_later_native_turn_or_write_old_state(api):
    owner, a, sid, _ = unit_session(api, guided=False); b = project(api)
    row = next(row for row in api.get('/api/conversations').json()['items'] if row['kind'] == 'original')
    check(organization(api, row, 'move', 'bad_source_move', target_project_id=b).status_code == 200)
    selected = scope(api, row['id'])
    check(send(api, selected, 'healthy_earlier', 'A healthy saved user requirement').status_code == 200)
    check(send(api, selected, 'healthy_current', 'Current user question').status_code == 200)
    bad_path = owner._path(a, sid, 'state.json')
    wrapper = json.loads(bad_path.read_bytes()); wrapper['value']['messages'] = []; bad_path.write_text(json.dumps(wrapper))
    frozen = bad_path.read_bytes()
    view = history(api, row['id'])
    check(any(message['text'] == 'A healthy saved user requirement' for message in view['messages']))
    check(view['coverage']['source_partial'] and not view['coverage']['complete'])
    model = api.app.state.native_messages.read_history({}, {'request': {'operation_id': 'healthy_current'}})
    check(any(message['user_text'] == 'A healthy saved user requirement' for message in model['messages']))
    check(bad_path.read_bytes() == frozen)


def test_native_answer_reuses_exact_final_guidance_cohort_and_never_an_unrelated_or_old_reply(api):
    owner, pid, sid, state = unit_session(api, guided=True)
    native = api.app.state.native_messages
    record = native_record(api, 'cohort_native', pid=pid, cid='exact_cohort')
    reference = owner._manifest(pid, sid)[1]
    record['request']['raw_text'] = '只读核对原任务'
    record.update(request_sha256=digest(record['request']), execution=None,
        owner={'project_id': pid, 'session_ref': reference, 'generation': 1}, native_operation_id='root-task')
    native.ws._write(native.path('cohort_native'), record)
    row = next(row for row in api.get('/api/conversations').json()['items'] if row['kind'] == 'native')
    check(organization(api, row, 'archive', 'promote_cohort').status_code == 200)
    initial = history(api, row['id'])
    check({message['text'] for message in initial['messages'] if message.get('user_input_kind') == 'ORIGINAL_TASK_GUIDANCE'} == {'指导1', '指导2', '指导3'})
    answer = next(message for message in initial['messages'] if message['role'] == 'assistant')
    check(answer['text'] == 'FINAL_PART_A\n\nFINAL_PART_B' and 'OLD_FIRST_RESPONSE' not in answer['text'])
    state['messages'] = [message for message in state['messages'] if message['info']['id'] != 'msg_final_b']
    owner._write(owner._path(pid, sid, 'state.json'), state, replace=True)
    partial = history(api, row['id'])
    answer = next(message for message in partial['messages'] if message['role'] == 'assistant')
    check(answer['text'] == 'FINAL_PART_A' and partial['coverage']['source_partial'])
    record['request']['raw_text'] = 'A different user task must not borrow this response'
    record['request_sha256'] = digest(record['request']); native.ws._write(native.path('cohort_native'), record)
    refused = history(api, row['id'])
    check(not any(message['role'] == 'assistant' for message in refused['messages']))


def test_internal_execution_owner_is_not_a_duplicate_user_conversation_or_organization_target(api):
    owner, pid, sid, _ = unit_session(api, guided=False)
    native_record(api, 'only_public_chat', pid=pid, cid='public_chat')
    original = next(row for row in api.get('/api/conversations').json()['items'] if row['kind'] == 'original')
    manifest = owner._read(owner._path(pid, sid, 'manifest.json'))
    manifest['native_message_admission'] = {'id': 'internal-parent', 'revision': 1, 'sha256': 'a' * 64}
    owner._write(owner._path(pid, sid, 'manifest.json'), manifest, replace=True)
    result = api.get('/api/conversations').json()
    check([row['kind'] for row in result['items']] == ['native'] and not result['coverage']['errors'], result)
    with pytest.raises(Conflict):
        api.app.state.conversation_directory.resolve(original['source'])


def test_discovery_revisions_keep_old_reviews_exact_user_answers_and_feedback_comment_projection(api):
    pid, opened = discovery(api); sid = opened['id']
    candidate = deepcopy(opened['payload']['candidate'])
    candidate['summary'] = 'SECOND_STORED_REVIEW'
    candidate['questions'] = [{'id': 'q_original', 'text': 'Original question', 'reason': 'Keep its exact version',
                               'category': 'MISSING_INPUT', 'blocking': True}]
    second = api.put(f'/api/projects/{pid}/discovery/sessions/{sid}', json={
        'operation_id': 'second_review', 'expected_revision': 1, 'candidate': candidate, 'change_reason': 'A stored revision'})
    check(second.status_code == 200, second.text)
    answer_text = 'My answer includes literal user JSON {"keep":true}'
    answer = api.post(f'/api/projects/{pid}/discovery/sessions/{sid}/answers', json={
        'operation_id': 'actual_answer', 'expected_revision': 2, 'question_id': 'q_original', 'text': answer_text})
    check(answer.status_code == 200, answer.text)
    candidate['summary'] = 'THIRD_STORED_REVIEW'
    candidate['questions'][0]['text'] = 'Later question with the same ID'
    third = api.put(f'/api/projects/{pid}/discovery/sessions/{sid}', json={
        'operation_id': 'third_review', 'expected_revision': 2, 'candidate': candidate, 'change_reason': 'A later stored revision'})
    check(third.status_code == 200, third.text)
    # Fixture of the exact USER_FEEDBACK shape produced by discovery.advance.
    payload = deepcopy(third.json()['payload']); payload['revision'] = 4
    payload['history'].append({'kind': 'USER_FEEDBACK', 'created_at': '2026-10-19T12:00:00+00:00',
        'text': json.dumps({'comment': 'Only this human comment belongs in the chat', 'INTERNAL_RAW_ENVELOPE': 'hidden'})})
    with api.app.state.store.connect() as connection:
        api.app.state.discovery._append(connection, pid, sid, payload)
    row = next(row for row in api.get('/api/conversations').json()['items'] if row['kind'] == 'discovery')
    check(organization(api, row, 'archive', 'promote_review_versions').status_code == 200)
    view = history(api, row['id'])
    reviews = [message for message in view['messages'] if message['role'] == 'assistant']
    check({message['object_ref']['revision'] for message in reviews} == {1, 2, 3, 4})
    check(any(message['text'] == 'SECOND_STORED_REVIEW' for message in reviews))
    actual_answer = next(message for message in view['messages'] if message['text'] == answer_text)
    check(actual_answer['object_ref']['revision'] == 2 and actual_answer['status'] == 'USER_ANSWER_NOT_AUTOMATIC_CONFIRMATION')
    check(any(message['text'] == 'Only this human comment belongs in the chat' for message in view['messages']))
    check('INTERNAL_RAW_ENVELOPE' not in json.dumps(view) and not view['coverage']['source_partial'], view)


def test_malformed_answer_objects_do_not_become_internal_json_chat_prose(api):
    pid = project(api); record = native_record(api, 'invalid_answer_object', pid=pid, cid='typed_answer')
    row = api.get('/api/conversations').json()['items'][0]
    check(organization(api, row, 'archive', 'promote_typed_answer').status_code == 200)
    result = {'candidate_json': {'text': {'INTERNAL_RAW_ANSWER': 'must not be displayed'}, 'questions': []}}
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE agent_executions SET result=? WHERE id=?', (json.dumps(result), record['execution']['id']))
    view = history(api, row['id'])
    check(any(message['role'] == 'user' for message in view['messages']))
    check(not any(message['role'] == 'assistant' for message in view['messages']))
    check('INTERNAL_RAW_ANSWER' not in json.dumps(view) and view['coverage']['source_partial'])
    # A native ordinary parent with no publication may retain SDK text in its
    # execution record. Its protocol marker prevents any raw fallback.
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE agent_executions SET result=? WHERE id=?',
            (json.dumps({'candidate_json': {'text': 'UNPUBLISHED_INTERNAL_SDK_JSON', 'questions': []}}), record['execution']['id']))
    record['native_message_ref'] = {'id': 'invalid_answer_object', 'revision': 1, 'sha256': 'a' * 64}
    api.app.state.native_messages.ws._write(api.app.state.native_messages.path('invalid_answer_object'), record)
    unpublished = history(api, row['id'])
    check('UNPUBLISHED_INTERNAL_SDK_JSON' not in json.dumps(unpublished) and unpublished['coverage']['source_partial'])


def test_original_history_read_cannot_race_our_atomic_observation_replacement(api, monkeypatch):
    """Force a writer exactly between the reader's stat and open."""
    import os
    import threading

    owner, pid, sid, state = unit_session(api, guided=False)
    row = next(row for row in api.get('/api/conversations').json()['items'] if row['kind'] == 'original')
    check(organization(api, row, 'archive', 'promote_observation_guard').status_code == 200)
    path = owner._path(pid, sid, 'state.json')
    replacement = deepcopy(state)
    replacement['updated_at'] = '2030-01-01T00:00:00+00:00'
    started, finished = threading.Event(), threading.Event()
    failures, workers = [], []
    original_open = os.open

    def replace():
        started.set()
        try:
            owner._write(path, replacement, replace=True)
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    def open_during_observation(candidate, *args, **kwargs):
        if str(candidate) == str(path) and not workers:
            worker = threading.Thread(target=replace)
            workers.append(worker)
            worker.start()
            check(started.wait(2), 'Concurrent observation did not start')
            check(not finished.wait(.15), 'Writer replaced an original record during its bounded read')
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(os, 'open', open_during_observation)
    try:
        view = history(api, row['id'])
    finally:
        for worker in workers:
            worker.join(5)
    check(len(workers) == 1 and finished.is_set() and not failures, failures)
    check(not view['coverage']['source_partial'], view['coverage'])
    check(any(message['role'] == 'user' for message in view['messages']), view)
    check(owner._state(pid, sid)['updated_at'] == replacement['updated_at'])
