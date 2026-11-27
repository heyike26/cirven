"""Real ASGI/SQLite/immutable-record readers; saved-source fixtures, no model or OS control."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from apps.api.config import Settings
from apps.api.native_messages import NativeQuestionInput
from packages.domain.tasks import digest, now, canonical
from test_native_file_continuity_api import app_client, HOST_TOKEN
from test_project_intake import upload
from test_planning_proposals import candidate as planning_candidate
from test_supervisor_context_projection import unit_session


def check(value, message='supervised navigation requirement failed'):
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


def project(client, name='原任务导航'):
    result = client.post('/api/projects', json={'name': name})
    check(result.status_code == 201, result.text)
    return result.json()['id']


def read_detail(client, kind, object_id, operation_id, pid, *, observed=None):
    if observed is None:
        response = client.post('/api/supervisor/check')
        check(response.status_code == 200, response.text)
        observed = response.json()['tasks']
    selected = next(row for row in observed if row['key']['kind'] == kind and
        row['key']['object_id'] == object_id and row['key']['operation_id'] == operation_id and row['key']['project_id'] == pid)
    params = {k: v for k, v in selected['key'].items() if v is not None}
    response = client.get('/api/supervisor/task-detail', params=params)
    return response, selected, params


def export(name, detail, **responses):
    folder = os.environ.get('EEAS_NAVIGATION_FIXTURES')
    if folder:
        path = Path(folder); path.mkdir(parents=True, exist_ok=True)
        (path / (name + '.json')).write_text(json.dumps({'detail': detail, **responses}, ensure_ascii=False, indent=2) + '\n')


def native_record(client, op='native_op', *, pid=None, cid='native_discussion', file=None, generation=None):
    source, store = client.app.state.native_messages, client.app.state.store
    storage_pid = pid or project(client, '原生只读执行记录')
    execution = 'exec_' + op
    result = {'candidate_json': {'text': '原问题的精确回答 ' + op, 'questions': []}}
    with store.connect() as c:
        c.execute('''INSERT INTO agent_executions
          (id,project_id,operation_id,request_hash,runtime,workspace,execution_status,verification_status,
           session_id,model_profile,sdk_version,request,result,created_at,ended_at,cancel_requested)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (execution, storage_pid, 'underlying_' + op, digest({}), source.transport.runtime, 'recorded-readonly',
           'RETURNED', 'NOT_RUN', None, 'recorded-public-profile', '1.19.0', '{}', canonical(result), now(), now(), 0))
    request = NativeQuestionInput(operation_id=op, raw_text='原始用户问题 ' + op, context_ref=None,
        project_id=pid, conversation_id=cid, provider_profile_ref=None, file_source_ref=file).request_dict()
    record = {'generation': generation or source.generation, 'request': request, 'request_sha256': digest(request),
        'status': 'RETURNED', 'execution': {'project_id': storage_pid, 'id': execution, 'session_id': None}}
    source.ws._write(source.path(op), record)
    check(source.get(op).get('text') == '原问题的精确回答 ' + op)
    return record


def discovery(client, pid=None):
    pid = pid or project(client)
    response = client.post(f'/api/projects/{pid}/discovery/sessions', json={'operation_id': 'discovery_create',
        'title': '原需求讨论', 'entry_mode': 'REQUIREMENTS', 'statement': '形成带来源的任务计划', 'materials': []})
    check(response.status_code == 201, response.text)
    return pid, response.json()


def planning(client, pid=None, source_session=None):
    pid = pid or project(client)
    _, document = upload(client, pid, '原始要求：保留用户修改，只形成计划。')
    body = {'operation_id': 'planning_create', 'prompt': '形成可核验的原任务图', 'materials': [document],
        'mode': 'manual', 'candidate': planning_candidate()}
    if source_session:
        body['discovery_ref'] = {k: source_session[k] for k in ('id', 'revision', 'sha256')}
    response = client.post(f'/api/projects/{pid}/planning/proposals', json=body)
    check(response.status_code == 201, response.text)
    return pid, response.json()


@pytest.mark.parametrize('unbound', [False, True])
def test_native_navigation_and_host_only_null_directory_keep_exact_original_scope(api, unbound):
    pid = None if unbound else project(api)
    record = native_record(api, pid=pid)
    response, selected, _ = read_detail(api, 'native_message', 'native_op', 'native_op', pid)
    check(response.status_code == 200, response.text)
    value, nav = response.json(), response.json()['navigation']
    check(nav['kind'] == 'native' and nav['status'] == 'AVAILABLE')
    check(nav['task_key'] == selected['key'] and nav['source_ref'] == selected['source_ref'])
    check(nav['conversation_id'] == 'native_discussion' and nav['request_sha256'] == record['request_sha256'])
    check(value['answer_text'] == '原问题的精确回答 native_op')
    params = {} if pid is None else {'project_id': pid}
    convo = api.get('/api/desktop-host/native-conversations/native_discussion', params=params)
    operation = api.get('/api/desktop-host/native-conversations/native_discussion/operations/native_op', params=params)
    check(convo.status_code == 200, convo.text); check(operation.status_code == 200, operation.text)
    check(convo.json()['project_id'] == pid and operation.json()['project_id'] == pid)
    check(operation.json()['request_sha256'] == nav['request_sha256'])
    other = project(api, '不借用其他工程')
    check(api.get('/api/desktop-host/native-conversations/native_discussion/operations/native_op', params={'project_id': other}).status_code == 409)
    denied = api.get('/api/desktop-host/native-conversations/native_discussion', params=params,
        headers={'X-EEAS-Desktop-Host': 'wrong'})
    check(denied.status_code == 409 and denied.json()['error']['code'] == 'REVISION_CONFLICT', denied.text)
    export('native_null' if unbound else 'native_project', value, native_conversation=convo.json(),
        native_operation=operation.json(), native_message=api.get('/api/desktop-host/native-messages/native_op').json())


def test_null_directory_does_not_collect_bound_project_messages_or_borrow_a_matching_conversation(api):
    pid = project(api); native_record(api, 'bound', pid=pid); native_record(api, 'unbound', pid=None)
    global_view = api.get('/api/desktop-host/native-conversations/native_discussion').json()
    check([row['operation_id'] for row in global_view['messages']] == ['unbound'])
    check(global_view['coverage']['scope_filter'] == 'EXPLICIT_UNBOUND_ONLY')
    check(global_view['coverage']['unbound_messages_included'] is True)
    local_view = api.get('/api/desktop-host/native-conversations/native_discussion', params={'project_id': pid}).json()
    check([row['operation_id'] for row in local_view['messages']] == ['bound'])
    check(local_view['coverage']['unbound_messages_included'] is False)
    check(api.get('/api/desktop-host/native-conversations/native_discussion/operations/bound').status_code == 409)
    check(api.get('/api/desktop-host/native-conversations/native_discussion/operations/unbound', params={'project_id': pid}).status_code == 409)


def test_native_source_change_rejects_navigation_and_missing_conversation_is_not_replaced(api):
    record = native_record(api, cid=None)
    response, _, _ = read_detail(api, 'native_message', 'native_op', 'native_op', None)
    check(response.status_code == 200, response.text)
    check(response.json()['navigation']['status'] == 'UNAVAILABLE')
    check(response.json()['navigation']['conversation_id'] is None)
    record['request']['raw_text'] = '不能重绑原任务'; record['request_sha256'] = digest(record['request'])
    api.app.state.native_messages.ws._write(api.app.state.native_messages.path('native_op'), record)
    check(api.get('/api/supervisor/task-detail', params={k: v for k, v in response.json()['key'].items() if v is not None}).status_code == 409)


def test_original_navigation_binds_actual_session_operation_and_old_generation_remains_readonly(api):
    owner, pid, sid, state = unit_session(api, guided=False)
    state.update(todo=[], children=[])
    owner._write(owner._path(pid, sid, 'state.json'), state, replace=True)
    response, selected, _ = read_detail(api, 'original_session', sid, 'root-task', pid)
    check(response.status_code == 200, response.text)
    value, nav = response.json(), response.json()['navigation']
    check(nav['kind'] == 'original' and nav['session_ref'] == selected['source_ref'])
    operation = owner.operation(pid, sid, 'root-task')
    check(nav['request_sha256'] == operation['request_sha256'] and nav['generation'] == 1)
    check(nav['status'] == 'READONLY', 'recorded disconnected session is not a live execution grant')
    state['generation'] = 2; owner._write(owner._path(pid, sid, 'state.json'), state, replace=True)
    response, _, _ = read_detail(api, 'original_session', sid, 'root-task', pid)
    check(response.status_code == 200, response.text)
    check(response.json()['navigation']['generation'] == 1 and response.json()['navigation']['current_generation'] == 2)
    check(response.json()['navigation']['status'] == 'READONLY')
    export('original_old_generation', response.json(), session=owner.get(pid, sid), operation=operation)


def test_discovery_and_planning_navigation_read_exact_revisions_and_preserve_original_answers(api):
    pid, original = discovery(api)
    response, selected, _ = read_detail(api, 'discovery', original['id'], 'discovery_create', pid)
    check(response.status_code == 200, response.text)
    nav = response.json()['navigation']; check(nav['object_ref'] == {k: original[k] for k in ('id', 'revision', 'sha256')})
    check(nav['source_session_ref'] == nav['object_ref'] and nav['proposal_ref'] is None)
    export('discovery', response.json(), object=api.get(f"/api/projects/{pid}/discovery/sessions/{original['id']}", params={'revision': 1}).json())
    _, proposal = planning(api, pid)
    response, _, _ = read_detail(api, 'planning', proposal['id'], 'planning_create', pid)
    check(response.status_code == 200, response.text)
    check(response.json()['answer_text'] == proposal['payload']['candidate']['summary'])
    nav = response.json()['navigation']; check(nav['object_ref']['revision'] == 1 and nav['source_session_ref'] is None)
    export('planning', response.json(), object=api.get(f"/api/projects/{pid}/planning/proposals/{proposal['id']}", params={'revision': 1}).json())
    changed = deepcopy(proposal['payload']['candidate']); changed['title'] = '后续用户手改版本'
    edited = api.put(f"/api/projects/{pid}/planning/proposals/{proposal['id']}", json={
        'operation_id': 'later_edit', 'expected_revision': 1, 'candidate': changed, 'change_reason': '用户后来修改'})
    check(edited.status_code == 200, edited.text)
    response, _, _ = read_detail(api, 'planning', proposal['id'], 'planning_create', pid)
    check(response.status_code == 200, response.text)
    check(response.json()['navigation']['object_ref']['revision'] == 1 and response.json()['navigation']['status'] == 'READONLY')


def test_plan_wrapper_has_exact_result_session_proposal_and_source_session_without_latest(api):
    pid, session = discovery(api)
    candidate = {'title': '明确要求', 'phase': 'SPECIFY', 'summary': '完整确认原始范围',
        'claims': [{'id': 'req', 'text': '输出可核验任务网络', 'category': 'REQUIREMENT', 'origin': 'USER_STATEMENT',
                    'acceptance': ['每个任务节点包含可核对的输入、产出和验收项']}],
        'questions': [], 'competitors': [], 'graphs': []}
    root = f"/api/projects/{pid}/discovery/sessions/{session['id']}"
    edited = api.put(root, json={'operation_id': 'edit', 'expected_revision': session['revision'], 'candidate': candidate, 'change_reason': '用户明确要求'})
    check(edited.status_code == 200, edited.text); session = edited.json()
    confirmed = api.post(root + '/confirm', json={'operation_id': 'confirm', 'expected_revision': session['revision'],
        'session_sha256': session['sha256'], 'user_confirmed': True, 'accepted_claim_ids': ['req'], 'accepted_graph_ids': []})
    check(confirmed.status_code == 200, confirmed.text); basis = confirmed.json()
    _, proposal = planning(api, pid, basis); proposal_ref = {k: proposal[k] for k in ('id', 'revision', 'sha256')}
    service = api.app.state.discovery; payload = deepcopy(basis['payload']); payload.update(revision=basis['revision'] + 1, proposal_ref=proposal_ref)
    with api.app.state.store.connect() as c:
        service._append(c, pid, basis['id'], payload)
        service._claim(c, pid, basis['id'], 'recorded_plan', 'PLAN', digest({'fixture': 'recorded original PLAN result'}))
    result = {'session': service.get(pid, basis['id'], payload['revision']), 'proposal_ref': proposal_ref, 'proposal': proposal}
    with api.app.state.store.connect() as c:
        service._finish(c, pid, 'recorded_plan', result)
    response, _, _ = read_detail(api, 'discovery', basis['id'], 'recorded_plan', pid)
    check(response.status_code == 200, response.text)
    value, nav = response.json(), response.json()['navigation']; check(nav['object_ref']['revision'] == payload['revision'])
    check(nav['source_session_ref'] == nav['object_ref'] and nav['proposal_ref'] == proposal_ref)
    check(value['answer_text'] == candidate['summary'])
    export('discovery_plan', value, object=api.get(root, params={'revision': payload['revision']}).json(),
        proposal=api.get(f"/api/projects/{pid}/planning/proposals/{proposal['id']}", params={'revision': proposal['revision']}).json())
    response, _, _ = read_detail(api, 'planning', proposal['id'], 'planning_create', pid)
    check(response.status_code == 200, response.text)
    check(response.json()['navigation']['source_session_ref'] == {k: basis[k] for k in ('id', 'revision', 'sha256')})
    export('planning_with_source', response.json(), object=proposal, source_session=api.get(root, params={'revision': basis['revision']}).json())


def test_missing_recorded_result_and_changed_result_basis_never_choose_a_latest_object(api):
    pid, value = discovery(api)
    with api.app.state.store.connect() as c:
        c.execute('INSERT INTO discovery_operations VALUES (?,?,?,?,?,?,?,?)',
            (pid, 'pending', value['id'], 'REFINE', digest({'pending': True}), 'PROCESSING', None, None))
    response, _, pending_params = read_detail(api, 'discovery', value['id'], 'pending', pid)
    check(response.status_code == 200, response.text); check(response.json()['navigation']['status'] == 'UNAVAILABLE')
    check(response.json()['navigation']['object_ref'] is None)
    response, _, params = read_detail(api, 'discovery', value['id'], 'discovery_create', pid)
    check(response.status_code == 200, response.text)
    with api.app.state.store.connect() as c:
        api.app.state.discovery._finish(c, pid, 'pending', value)
    check(api.get('/api/supervisor/task-detail', params=pending_params).status_code == 409)


def test_navigation_keeps_saved_user_files_unchanged_and_marks_changed_file_readonly(api, tmp_path):
    pid = project(api); folder = tmp_path / 'files'; folder.mkdir(); target = folder / '原稿.md'; target.write_text('用户原稿', encoding='utf-8')
    binding = api.post(f'/api/projects/{pid}/local-workspaces', json={'idempotency_key': 'workspace', 'root': str(folder), 'allowed_write_paths': []})
    check(binding.status_code == 201, binding.text); wid = binding.json()['id']
    source = api.get(f'/api/projects/{pid}/local-workspaces/{wid}/file', params={'path': target.name}).json()['source_ref']
    record = native_record(api, pid=pid, file=source)
    response, _, _ = read_detail(api, 'native_message', 'native_op', 'native_op', pid)
    check(response.status_code == 200, response.text); nav = response.json()['navigation']
    check(nav['workspace_ref'] == {'id': wid, 'revision': source['binding_revision'], 'binding_sha256': source['binding_sha256']})
    target.write_text('用户手改保留', encoding='utf-8'); before = target.read_bytes()
    response, _, _ = read_detail(api, 'native_message', 'native_op', 'native_op', pid)
    check(response.status_code == 200, response.text); check(response.json()['navigation']['status'] == 'READONLY')
    check(response.json()['navigation']['file_source_ref'] == source); check(target.read_bytes() == before)
    check(api.app.state.native_messages.ws._read(api.app.state.native_messages.path('native_op'), authority=False)['request_sha256'] == record['request_sha256'])
    legacy = native_record(api, 'legacy_file', pid=None, cid='legacy_discussion', file=source)
    response, _, _ = read_detail(api, 'native_message', 'legacy_file', 'legacy_file', None)
    check(response.status_code == 200, response.text); nav = response.json()['navigation']
    check(nav['project_id'] is None and nav['request_project_id'] is None and nav['conversation_project_id'] == pid)
    check(nav['status'] == 'READONLY' and nav['reason_code'] == 'LEGACY_PROJECT_SCOPE_MISMATCH')
    message = api.get('/api/desktop-host/native-messages/legacy_file').json()
    check(message['project_id'] == pid and message['request_project_id'] is None and message['request_sha256'] == legacy['request_sha256'])
    convo = api.get('/api/desktop-host/native-conversations/legacy_discussion', params={'project_id': pid})
    check(convo.status_code == 200, convo.text)
    check(api.get('/api/desktop-host/native-conversations/legacy_discussion').status_code == 404)
    export('native_legacy_file_scope', response.json(), native_conversation=convo.json(), native_message=message)
    check(target.read_bytes() == before)
