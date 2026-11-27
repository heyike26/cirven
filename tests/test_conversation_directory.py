"""Real local ASGI/signed-record/SQLite metadata reads; no model or OS interaction."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from apps.api.config import Settings
from packages.domain.tasks import digest
from test_native_file_continuity_api import app_client, HOST_TOKEN
from test_supervised_navigation_api import native_record, discovery, project
from test_supervisor_context_projection import unit_session


def check(value, message='conversation directory requirement failed'):
    if not value:
        raise AssertionError(message)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', HOST_TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
    with app_client(Settings(tmp_path / 'data', worker_enabled=False)) as client:
        settings = client.get('/api/supervisor').json()['settings']
        result = client.post('/api/supervisor/settings', json={'enabled': False,
            'notifications_enabled': False, 'interval_seconds': 30, 'expected_revision': settings['revision']})
        check(result.status_code == 200, result.text)
        yield client


def forbid(*_args, **_kwargs):
    raise AssertionError('Directory must not connect an owner, read a transcript API or request a model')


def test_all_directory_uses_real_metadata_and_never_exports_prompts_transcripts_or_execution(api, monkeypatch):
    pid = project(api, '同一台电脑的工程')
    record = native_record(api, pid=pid, cid='native_history')
    record['request']['raw_text'] = 'PRIVATE_NATIVE_WIRE_AND_USER_BODY'
    record['request_sha256'] = digest(record['request'])
    api.app.state.native_messages.ws._write(api.app.state.native_messages.path('native_op'), record)
    native_record(api, 'unbound_op', pid=None, cid='unbound_history')
    owner, original_pid, sid, state = unit_session(api, guided=False)
    state['messages'] = [{'PRIVATE_ORIGINAL_TRANSCRIPT': 'must not leave this record'}]
    owner._write(owner._path(original_pid, sid, 'state.json'), state, replace=True)
    _, session = discovery(api, pid)
    monkeypatch.setattr(owner, 'get', forbid)
    monkeypatch.setattr(owner, 'inspect', forbid)
    monkeypatch.setattr(owner, 'list', forbid)
    monkeypatch.setattr(api.app.state.native_messages, 'get', forbid)
    monkeypatch.setattr(api.app.state.native_messages, 'ide_conversations', forbid)
    monkeypatch.setattr(api.app.state.discovery, 'get', forbid)
    monkeypatch.setattr(api.app.state.discovery, 'list', forbid)
    result = api.get('/api/conversations')
    check(result.status_code == 200, result.text)
    body = result.json(); check(body['version'] == 1 and body['execution_authorized'] is False)
    check({row['kind'] for row in body['items']} == {'native', 'original', 'discovery'})
    check(len(body['items']) == 4 and body['coverage']['complete'] is True)
    check(body['coverage']['model_calls'] == 0 and body['coverage']['candidates_read'] <= 200)
    check(body['coverage']['bytes_budgeted'] <= body['coverage']['byte_limit'])
    wire = json.dumps(body, ensure_ascii=False)
    for secret in ('PRIVATE_NATIVE_WIRE_AND_USER_BODY', 'PRIVATE_ORIGINAL_TRANSCRIPT', 'payload', 'messages', 'raw_text'):
        check(secret not in wire, secret)
    native = next(row for row in body['items'] if row['kind'] == 'native' and row['project_id'] == pid)
    check(native['title'] is None and native['source']['conversation_id'] == 'native_history')
    check(native['source']['last_operation_id'] == 'native_op' and native['source']['request_sha256'] == record['request_sha256'])
    check(next(row for row in body['items'] if row['kind'] == 'original')['source']['session_ref']['id'] == sid)
    check(next(row for row in body['items'] if row['kind'] == 'discovery')['source']['object_ref']['sha256'] == session['sha256'])


def test_recent_global_pages_find_older_sources_and_keep_stable_conversation_identity(api):
    pid = project(api)
    paths = []
    for index in range(7):
        native_record(api, 'op_' + str(index), pid=pid, cid='conversation_' + str(index))
        paths.append(api.app.state.native_messages.path('op_' + str(index)))
    native_record(api, 'duplicate_turn', pid=pid, cid='conversation_6')
    paths.append(api.app.state.native_messages.path('duplicate_turn'))
    owner, original_pid, sid, _ = unit_session(api, guided=False)
    _, session = discovery(api, pid)
    future = 1_900_000_000_000_000_000
    for index, path in enumerate(paths):
        os.utime(path, ns=(future + index * 1_000_000_000, future + index * 1_000_000_000))
    original_state = owner._path(original_pid, sid, 'state.json')
    os.utime(original_state, ns=(future - 1_000_000_000, future - 1_000_000_000))
    cursor, collected, pages = None, {}, []
    for _ in range(12):
        response = api.get('/api/conversations', params={'limit': 2, **({'cursor': cursor} if cursor else {})})
        check(response.status_code == 200, response.text); value = response.json(); pages.append(value)
        dates = [datetime.fromisoformat(item['updated_at']) for item in value['items']]
        check(dates == sorted(dates, reverse=True))
        for item in value['items']:
            previous = collected.get(item['id'])
            if previous is None or item['updated_at'] > previous['updated_at']:
                collected[item['id']] = item
        cursor = value['next_cursor']
        if cursor is None:
            break
    check(cursor is None and len(pages) > 1)
    check(pages[0]['items'][0]['source']['last_operation_id'] == 'duplicate_turn', 'Real observed update time must outrank opaque operation filenames')
    check(pages[0]['coverage']['partial'] and pages[0]['coverage']['has_more'])
    check(len(collected) == 9, 'Seven native discussions plus original and discovery remain reachable')
    check(any(row['kind'] == 'original' for row in collected.values()))
    check(any(row['kind'] == 'discovery' and row['source']['object_ref']['id'] == session['id'] for row in collected.values()))
    check(next(row for row in collected.values() if row['kind'] == 'native' and row['source']['conversation_id'] == 'conversation_6')['source']['last_operation_id'] == 'duplicate_turn')


def test_explicit_filters_bad_cursors_and_unbound_scope_never_borrow_current_project(api):
    pid = project(api); native_record(api, 'bound', pid=pid, cid='shared_name'); native_record(api, 'unbound', pid=None, cid='shared_name')
    _, _ = discovery(api, pid)
    bound = api.get('/api/conversations', params={'scope': 'project', 'project_id': pid}).json()
    check(bound['items'] and all(row['project_id'] == pid for row in bound['items']))
    unbound = api.get('/api/conversations', params={'scope': 'unbound'}).json()
    check(len(unbound['items']) == 1 and unbound['items'][0]['source']['last_operation_id'] == 'unbound')
    original = api.get('/api/conversations', params={'kind': 'discovery'}).json()
    check(original['items'] and all(row['kind'] == 'discovery' for row in original['items']))
    for params in ({'scope': 'project'}, {'project_id': pid}, {'limit': 101}, {'kind': 'execution'}, {'cursor': 'not.valid'}, {'cursor': base64.urlsafe_b64encode(b'{"v":2}').rstrip(b'=').decode()}):
        check(api.get('/api/conversations', params=params).status_code == 422)
    first = api.get('/api/conversations', params={'limit': 1}).json(); check(first['next_cursor'])
    check(api.get('/api/conversations', params={'kind': 'native', 'cursor': first['next_cursor']}).status_code == 422)


def test_bad_signed_record_is_reported_as_partial_without_hiding_other_history_or_repairing_files(api):
    pid = project(api); native_record(api, 'good', pid=pid); native_record(api, 'broken', pid=pid)
    path = api.app.state.native_messages.path('broken'); path.write_text('{"value":{"private":"INVALID"},"sha256":"wrong"}', encoding='utf-8'); before = path.read_bytes()
    response = api.get('/api/conversations')
    check(response.status_code == 200, response.text); value = response.json()
    check(len(value['items']) == 1 and value['items'][0]['source']['last_operation_id'] == 'good')
    check(value['coverage']['partial'] is True and value['coverage']['complete'] is False)
    check(value['coverage']['errors'] == [{'source': 'native', 'code': 'RECORDED_METADATA_UNAVAILABLE', 'count': 1}])
    check(path.read_bytes() == before and 'INVALID' not in json.dumps(value))


def test_record_byte_budget_yields_a_real_cursor_and_older_rows_remain_reachable(api):
    pid = project(api)
    source = api.app.state.native_messages
    for index in range(9):
        op = 'large_' + str(index)
        record = native_record(api, op, pid=pid, cid='large_discussion_' + str(index))
        record['saved_observation_padding'] = 'X' * 1_000_000
        source.ws._write(source.path(op), record)
    result = api.get('/api/conversations', params={'limit': 100})
    check(result.status_code == 200, result.text); first = result.json()
    check(first['coverage']['bytes_budgeted'] <= 16 * 1024 * 1024)
    check(first['coverage']['has_more'] and first['next_cursor'] and len(first['items']) < 9)
    check(len(result.content) < 32768 and 'saved_observation_padding' not in result.text)
    second = api.get('/api/conversations', params={'limit': 100, 'cursor': first['next_cursor']})
    check(second.status_code == 200, second.text); last = second.json()
    ids = {item['id'] for item in first['items'] + last['items']}
    check(len(ids) == 9 and last['next_cursor'] is None)
    check(last['coverage']['complete'] and last['coverage']['model_calls'] == 0)
