"""Actual HTTP/Store projections plus one actual TCP/original-CLI guidance run.

Synthetic session records below are explicitly UNIT source observations, not
runtime credentials or models. Only the final test qualifies the original CLI;
its sole replacement is an external loopback model. No Windows GUI is tested.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import time

import httpx
import pytest

from apps.api.config import Settings
from packages.adapters.opencode_profile import CLI_SHA, VERSION
from packages.domain.tasks import canonical, digest, now
from test_agent_sessions import CLI, KEY, MODEL, ModelWire
from test_native_file_continuity_api import app_client, HOST_TOKEN, MESSAGES
from test_session_guidance_api import HeldModelGate, same_process_alive, sha
from test_workflow_app_runtime import LoggedServer


def check(value, message='supervisor context projection requirement failed'):
    if not value:
        raise AssertionError(message)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', HOST_TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
    with app_client(Settings(tmp_path / 'data')) as client:
        settings = client.get('/api/supervisor').json()['settings']
        response = client.post('/api/supervisor/settings', json={
            'enabled': False, 'notifications_enabled': False, 'interval_seconds': 30,
            'expected_revision': settings['revision']})
        check(response.status_code == 200, response.text)
        yield client


def unit_session(client, *, guided=True):
    """Real immutable wrappers/Store; synthesized native observations, UNIT only."""
    response = client.post('/api/projects', json={'name': '后台结果投影单元夹具'})
    check(response.status_code == 201, response.text)
    pid, sid, native = response.json()['id'], 'a' * 32, 'ses_unit_original'
    owner = client.app.state.agent_sessions
    manifest = {'id': sid, 'project_id': pid, 'workspace_kind': 'APP_INTERNAL_READONLY',
        'request': {'workspace_ref': None, 'provider_profile_ref': {'id': 'unit-not-connected', 'revision': 1, 'sha256': 'a' * 64}}}
    owner._write(owner._path(pid, sid, 'manifest.json'), manifest)
    ref = owner._manifest(pid, sid)[1]
    request = {'operation_id': 'root-task', 'session_ref': ref, 'generation': 1,
               'raw_text': '只读核对原任务', 'agent': 'plan', 'timeout_seconds': 120}
    owner._claim(pid, sid, 'root-task', 'MESSAGE', request)

    def message(ident, parent, text, session=native):
        return {'info': {'id': ident, 'role': 'assistant', 'sessionID': session, 'parentID': parent,
                         'finish': 'stop', 'time': {'completed': 123}},
                'parts': [{'type': 'text', 'text': text, 'messageID': ident, 'sessionID': session}]}

    messages = [message('msg_old', 'msg_root', 'OLD_FIRST_RESPONSE')]
    ids = ['msg_old']
    continuations = []
    if guided:
        for i in range(1, 4):
            ident = f'guidance_{i}'
            owner._claim(pid, sid, ident, 'GUIDANCE', {'operation_id': ident, 'session_ref': ref,
                'generation': 1, 'raw_text': f'指导{i}', 'expected_active_operation_id': 'root-task'})
            owner._finish(pid, sid, ident, 'RETURNED' if i == 3 else 'COHORT_RETURNED',
                generation=1, original_session_id=native, original_active_operation_id='root-task',
                native_message_id=f'msg_guidance_{i}', native_user_observed=True,
                native_assistant_ids=['msg_final_b', 'msg_final_a'] if i == 3 else [])
            continuations.append(ident)
        messages.extend([message('msg_final_a', 'msg_guidance_3', 'FINAL_PART_A'),
                         message('msg_other', 'msg_other_user', 'UNRELATED_RESPONSE'),
                         message('msg_final_b', 'msg_guidance_3', 'FINAL_PART_B')])
        ids = ['msg_final_b', 'msg_final_a']  # Projection must retain original observed message order.
    owner._finish(pid, sid, 'root-task', 'RETURNED', generation=1, original_session_id=native,
        native_message_id='msg_root', native_assistant_ids=ids, continuation_guidance_ids=continuations,
        response_evidence='RUN_COHORT_LATEST_GUIDANCE_RESPONSE' if guided else 'ROOT_FINAL_RESPONSE',
        semantic_acceptance=False, independent_verification='NOT_RUN')
    state = {'generation': 1, 'owner_status': 'DISCONNECTED', 'native_status': 'idle', 'active_operation_id': None,
        'original_session_id': native, 'messages': messages, 'pending_questions': [], 'pending_permissions': [],
        'observation_at': now(), 'last_event_seq': 0, 'observation_error': None}
    owner._write(owner._path(pid, sid, 'state.json'), state)
    return owner, pid, sid, state


def detail(client, pid, sid, generation=1):
    observed = client.post('/api/supervisor/check')
    check(observed.status_code == 200, observed.text)
    check(observed.json()['model_calls'] == 0)
    response = client.get('/api/supervisor/task-detail', params={
        'kind': 'original_session', 'project_id': pid, 'object_id': sid,
        'operation_id': 'root-task', 'generation': generation})
    check(response.status_code == 200, response.text)
    return response.json()


def test_actual_api_unit_guidance_final_uses_exact_ids_in_observed_order_and_preserves_old_shape(api):
    owner, pid, sid, state = unit_session(api)
    value = detail(api, pid, sid)
    check(value['answer_status'] == 'AVAILABLE' and value['answer_text'] == 'FINAL_PART_A\n\nFINAL_PART_B')
    check('OLD_FIRST_RESPONSE' not in canonical(value) and 'UNRELATED_RESPONSE' not in canonical(value))
    check(all(key in value for key in ('input_text', 'answer_text', 'artifacts', 'session_ref', 'text_coverage', 'text_truncated')))
    check(not value['execution_authorized'] and value['task']['validation_status'] == 'NOT_RUN')


@pytest.mark.parametrize('change', ['missing_one', 'foreign_session', 'wrong_parent', 'foreign_part', 'one_foreign_part'])
def test_actual_api_unit_missing_or_foreign_final_never_falls_back_to_first_response(api, change):
    owner, pid, sid, state = unit_session(api)
    if change == 'missing_one':
        state['messages'] = [row for row in state['messages'] if row['info']['id'] != 'msg_final_b']
    else:
        for row in state['messages']:
            if not row['info']['id'].startswith('msg_final_'):
                continue
            if change == 'foreign_session':
                row['info']['sessionID'] = 'ses_foreign_owner'
            elif change == 'wrong_parent':
                row['info']['parentID'] = 'msg_root'
            elif change == 'foreign_part' or row['info']['id'] == 'msg_final_b':
                row['parts'][0]['sessionID'] = 'ses_foreign_owner'
    owner._write(owner._path(pid, sid, 'state.json'), state, replace=True)
    value = detail(api, pid, sid)
    partial = change in ('missing_one', 'one_foreign_part')
    check(value['answer_status'] == ('PARTIAL' if partial else 'UNAVAILABLE'))
    check(value['answer_text'] == ('FINAL_PART_A' if partial else ''))
    check('OLD_FIRST_RESPONSE' not in canonical(value) and value['answer_message'])


@pytest.mark.parametrize('change', ['wrong_cohort', 'guidance_generation', 'guidance_request_generation', 'owner_generation'])
def test_actual_api_unit_old_generation_or_wrong_guidance_owner_is_unavailable(api, change):
    owner, pid, sid, state = unit_session(api)
    if change == 'owner_generation':
        state['generation'] = 2
        owner._write(owner._path(pid, sid, 'state.json'), state, replace=True)
    elif change == 'guidance_request_generation':
        latest = owner.operation(pid, sid, 'guidance_3')
        request = {**latest['request'], 'generation': 2}
        owner._finish(pid, sid, 'guidance_3', 'RETURNED', request=request, request_sha256=digest(request))
    else:
        changes = {'original_active_operation_id': 'different-task'} if change == 'wrong_cohort' else {'generation': 2}
        owner._finish(pid, sid, 'guidance_3', 'RETURNED', **changes)
    value = detail(api, pid, sid)
    check(value['answer_status'] == 'UNAVAILABLE' and value['answer_text'] == '')


def test_actual_api_unit_ordinary_root_final_is_preserved(api):
    owner, pid, sid, state = unit_session(api, guided=False)
    value = detail(api, pid, sid)
    check(value['answer_status'] == 'AVAILABLE' and value['answer_text'] == 'OLD_FIRST_RESPONSE')


def test_real_native_http_without_execution_keeps_explicit_project_and_unbound_stays_unbound(api):
    pid = api.post('/api/projects', json={'name': '没有模型也保留原工程'}).json()['id']
    for operation, project in [('known-project', pid), ('unbound-question', None)]:
        sent = api.post(MESSAGES, json={'operation_id': operation, 'raw_text': '普通只读问题', 'project_id': project})
        check(sent.status_code == 200 and sent.json()['status'] == 'NOT_CONFIGURED', sent.text)
    checked = api.post('/api/supervisor/check')
    check(checked.status_code == 200, checked.text)
    tasks = {row['key']['operation_id']: row for row in checked.json()['tasks'] if row['key']['kind'] == 'native_message'}
    check(tasks['known-project']['key']['project_id'] == pid)
    check(tasks['unbound-question']['key']['project_id'] is None)
    for operation in tasks:
        result = api.get('/api/supervisor/task-detail', params={
            key: value for key, value in tasks[operation]['key'].items() if value is not None})
        check(result.status_code == 200 and result.json()['input_text'] == '普通只读问题', result.text)


def test_unit_native_internal_execution_or_formatted_file_metadata_never_mints_user_project(api):
    """Real Store/record readers; synthetic legacy execution/metadata, UNIT only."""
    owner = api.app.state.native_messages
    store = api.app.state.store
    internal = store.create_project('internal fixture only', 'internal-owner')['id']
    foreign = store.create_project('not admitted by file formatting', '')['id']
    for operation in ('internal-execution', 'unadmitted-file-metadata'):
        response = api.post(MESSAGES, json={'operation_id': operation, 'raw_text': '未归属普通问题'})
        check(response.status_code == 200, response.text)
        record = owner.ws._read(owner.path(operation), authority=False)
        if operation == 'internal-execution':
            with store.connect() as connection:
                connection.execute('''INSERT INTO agent_executions
                    (id,project_id,operation_id,request_hash,runtime,workspace,execution_status,verification_status,
                     session_id,model_profile,sdk_version,request,result,created_at,ended_at,cancel_requested)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    ('unit-internal-execution', internal, operation, 'a' * 64, 'UNIT_RECORDED_SOURCE', 'unit-private',
                     'FAILED', 'NOT_RUN', None, 'not-configured', VERSION, '{}', None, now(), now(), 0))
            record['execution'] = {'project_id': internal, 'id': 'unit-internal-execution'}
        else:
            record['request']['file_link_ref'] = {'project_id': foreign, 'id': 'b' * 64, 'revision': 1, 'sha256': 'c' * 64}
            record['request_sha256'] = digest(record['request'])
        owner.ws._write(owner.path(operation), record)
    checked = api.post('/api/supervisor/check')
    check(checked.status_code == 200, checked.text)
    tasks = [row for row in checked.json()['tasks'] if row['key']['kind'] == 'native_message']
    check(len(tasks) == 2 and all(row['key']['project_id'] is None for row in tasks))
    check(checked.json()['model_calls'] == 0)


def test_actual_tcp_original_three_guidances_supervisor_returns_latest_answer_without_new_model_call(tmp_path):
    approved = Path(os.environ.get('EEAS_TEST_APPROVED_CLI_CACHE', str(CLI)))
    check(sha(approved) == CLI_SHA[os.name], 'approved original CLI bytes differ')
    installed = tmp_path / 'data' / 'provider-settings' / 'runtime' / VERSION / 'opencode'
    installed.parent.mkdir(parents=True)
    if approved.stat().st_dev == installed.parent.stat().st_dev:
        os.link(approved, installed)
    else:
        shutil.copy2(approved, installed)
    wire = ModelWire(['HOLD', 'ACTUAL_FINAL_AFTER_THREE_GUIDANCES'])
    wire.release = HeldModelGate()
    wire.start()
    server = LoggedServer(tmp_path / 'data')
    if sys.flags.optimize:
        server.env['PYTHONOPTIMIZE'] = str(sys.flags.optimize)
    opened = closed = final = parent = projected = None
    disconnect_attempted = False
    cleanup_error = None
    trace = []
    try:
        server.start()
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=60) as client:
            boot = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
            check(boot.status_code == 200, boot.text)
            client.headers.update({'Origin': server.origin, 'X-EEAS-CSRF': boot.json()['csrf_token']})
            def call(method, path, body=None, params=None):
                response = client.request(method, path, json=body, params=params)
                trace.append({'method': method, 'path': path, 'status_code': response.status_code})
                check(response.is_success, response.text)
                return response.json()
            pid = call('POST', '/api/projects', {'name': '实际后台指导结果'})['id']
            saved = call('POST', '/api/planning/providers', {'operation_id': 'save-source-projection-provider',
                'id': 'source-projection-fixture', 'provider_url': wire.url, 'model_id': MODEL,
                'api_key': KEY, 'auth_mode': 'API_KEY'})
            configuration = call('GET', '/api/agent-sessions/configuration')
            opened = call('POST', f'/api/projects/{pid}/agent-sessions/open', {
                'operation_id': 'open-source-projection-owner', 'provider_profile_ref': saved['profile_ref'],
                'capability_binding_sha256': configuration['capability_binding_sha256'],
                'ttl_seconds': 180, 'max_calls': 8, 'max_output_tokens': 1024})
            session = f'/api/projects/{pid}/agent-sessions/{opened["session_ref"]["id"]}'
            control = {'session_ref': opened['session_ref'], 'generation': opened['generation']}
            first = call('POST', session + '/messages', {**control, 'operation_id': 'root-task',
                'raw_text': '先只读检查当前任务', 'agent': 'plan', 'timeout_seconds': 120})
            check(first['status'] == 'ACCEPTED' and wire.received.wait(10))
            guidance_ids = []
            for i in range(3):
                operation = 'actual-guidance-' + str(i)
                sent = call('POST', session + '/guidance', {**control, 'operation_id': operation,
                    'raw_text': '纠正要求：保留用户原件，并核对依据，第' + str(i + 1) + '点',
                    'intent': 'STEER', 'expected_active_operation_id': 'root-task'})
                check(sent['status'] == 'ACCEPTED')
                guidance_ids.append(operation)
            check(len(wire.requests) == 1 and not wire.release.timed_out)
            wire.release.set()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                final = call('GET', session)
                if final['active_operation_id'] is None:
                    break
                time.sleep(.08)
            check(final['active_operation_id'] is None)
            parent = call('GET', session + '/operations/root-task')
            check(parent['status'] == 'RETURNED' and parent['continuation_guidance_ids'] == guidance_ids)
            check(parent['semantic_acceptance'] is False and parent['independent_verification'] == 'NOT_RUN')
            checked = call('POST', '/api/supervisor/check')
            task = next(row for row in checked['tasks'] if row['key']['kind'] == 'original_session'
                and row['key']['object_id'] == opened['session_ref']['id'] and row['key']['operation_id'] == 'root-task')
            projected = call('GET', '/api/supervisor/task-detail', params=task['key'])
            check(projected['answer_status'] == 'AVAILABLE')
            check(projected['answer_text'] == 'ACTUAL_FINAL_AFTER_THREE_GUIDANCES', canonical(projected))
            check('MODEL_RELEASED_AFTER_INTERRUPT' not in projected['answer_text'])
            check(projected['task']['validation_status'] == 'NOT_RUN')
            for _ in range(2):
                reread = call('GET', '/api/supervisor/task-detail', params=task['key'])
                check(all(reread[field] == projected[field] for field in ('key', 'answer_text', 'answer_status', 'text_coverage')))
            check(len(wire.requests) == 2, 'projection or recovery launched a new model call')
            disconnect_attempted = True
            closed = call('POST', session + '/disconnect', {**control, 'operation_id': 'close-projection-owner'})
            check(closed['owner_status'] == 'DISCONNECTED' and not same_process_alive(opened['process_identity']))
    finally:
        wire.release.set()
        if opened and closed is None:
            try:
                with httpx.Client(base_url=server.origin, trust_env=False, timeout=30) as cleanup:
                    boot = cleanup.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
                    cleanup.headers.update({'Origin': server.origin, 'X-EEAS-CSRF': boot.json()['csrf_token']})
                    if not disconnect_attempted:
                        disconnect_attempted = True
                        cleanup.post(session + '/disconnect', json={**control, 'operation_id': 'close-projection-owner'})
                    else:
                        cleanup.get(session + '/operations/close-projection-owner')
            except Exception as error:
                cleanup_error = type(error).__name__
        try:
            server.stop()
        finally:
            wire.close()
            (tmp_path / 'actual-supervisor-projection-evidence.json').write_text(canonical({
                'qualification': 'ACTUAL_TCP_APP_ORIGINAL_CLI_ONLY_EXTERNAL_MODEL_FIXTURE',
                'optimized_api': bool(sys.flags.optimize), 'trace': trace, 'opened': opened, 'closed': closed,
                'parent': parent, 'projection': projected, 'model_rpc_count': len(wire.requests),
                'wire_requests': wire.requests, 'api_process_returncode': server.process.returncode,
                'cleanup_error': cleanup_error}) + '\n', encoding='utf-8')
    check(not wire.thread.is_alive() and closed is not None)
