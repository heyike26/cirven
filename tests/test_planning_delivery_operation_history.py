"""Read original API-created stage ledgers without a renderer index or replay."""
import hashlib
import inspect
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app
from packages.domain import planning_delivery as module
from packages.domain.discovery_schema import DiscoveryEdit
from packages.domain.intake import canonical, digest
from packages.domain.planning_delivery import DeliveryStage, PlanningDelivery


def reference(session):
    return {key: session[key] for key in ('id', 'revision', 'sha256')}


def create_session(client, name='同名阶段任务'):
    project = client.post('/api/projects', json={'name': name}).json()
    pid = project['id']
    response = client.post(f'/api/projects/{pid}/discovery/sessions', json={
        'operation_id': 'source-created', 'title': name, 'entry_mode': 'PROBLEM',
        'statement': '读取已保存阶段结果，不重新生成或请求模型。'})
    assert response.status_code == 201, response.text
    return pid, response.json()


def saved_stage(service, pid, session, key='stage-from-api', *, status='STAGE_REPORT'):
    """Only immutable reader fixtures, created via original claim/record writer.

    No stage generator, model, runtime or user output is invoked in this suite.
    """
    body = DeliveryStage(operation_id=key, session_ref=reference(session))
    folder, replay = service._claim(pid, body, 'STAGE')
    assert replay is None
    if status is not None:
        snapshot = folder / 'fixture-source.json'
        snapshot.write_bytes(b'{}')
        service._put(folder / 'result.json', {
            'schema_version': 'eeas.planning-stage-delivery.v1',
            'operation_id': key, 'status': status, 'session_ref': reference(session),
            'project_files': [], 'artifacts': [{
                'kind': 'source_snapshot', 'path': str(snapshot),
                'sha256': hashlib.sha256(b'{}').hexdigest()}],
            'report': {'answer_view_sha256': digest(session['answers'])}})
    return folder


def listing(client, pid, session, **query):
    return client.get(f'/api/projects/{pid}/planning-delivery/sessions/{session["id"]}/operations',
                      params={'revision': session['revision'], 'sha256': session['sha256'], **query})


def ledger_bytes(service):
    root = service.ws.data_dir / 'planning-delivery'
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


def test_api_stage_survives_indexless_restart_and_history_context(client):
    pid, first = create_session(client)
    service = client.app.state.planning_delivery
    saved_stage(service, pid, first, 'first-api-stage')
    candidate = dict(first['payload']['candidate'], summary='当前第二版；第一版产物仍须可读。')
    second = service.discovery.edit(pid, first['id'], DiscoveryEdit(
        operation_id='source-edited', expected_revision=1,
        candidate=candidate, change_reason='明确第二版上下文'))
    saved_stage(service, pid, second, 'second-api-stage')
    # Recreate the actual service against the same records, not a renderer cache.
    restarted = PlanningDelivery(service.store, service.ws, service.materials,
        service.discovery, service.proposals, service.reports, service.diagrams, service.exports)
    before = ledger_bytes(service)
    value = restarted.session_operations(pid, first['id'], second['revision'], second['sha256'])
    assert value['project_id'] == pid and value['session_ref'] == reference(second)
    assert value['complete'] is True
    assert {row['operation_id']: row['session_ref'] for row in value['entries']} == {
        'first-api-stage': reference(first), 'second-api-stage': reference(second)}
    rows = {row['operation_id']: row for row in value['entries']}
    assert all(row['basis_current'] is None and row['current_review_status'] is None for row in rows.values())
    assert restarted.operation(pid, 'first-api-stage')['basis_current'] is False
    assert restarted.operation(pid, 'second-api-stage')['current_review_status'] == 'CURRENT'
    response = listing(client, pid, second)
    assert response.status_code == 200, response.text
    assert response.json() == value
    settings = Settings(service.ws.data_dir, port=8765)
    with TestClient(create_app(settings), base_url=settings.origin) as fresh:
        bootstrap = fresh.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
        fresh.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': bootstrap.json()['csrf_token']})
        assert listing(fresh, pid, second).json() == value
    assert ledger_bytes(service) == before
    with service.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0


def test_list_requires_current_context_and_exact_project(client):
    pid, first = create_session(client)
    service = client.app.state.planning_delivery
    saved_stage(service, pid, first)
    second = service.discovery.edit(pid, first['id'], DiscoveryEdit(
        operation_id='context-edit', expected_revision=1,
        candidate=first['payload']['candidate'], change_reason='上下文版本前进'))
    other, foreign = create_session(client)
    assert listing(client, pid, first).status_code == 409
    assert listing(client, pid, second, sha256='f' * 64).status_code == 409
    assert listing(client, other, second).status_code == 404
    assert listing(client, pid, foreign).status_code == 404
    for query in ({'revision': 0}, {'sha256': 'invalid'}):
        assert listing(client, pid, second, **query).status_code == 422


def test_other_session_and_non_stage_records_do_not_become_this_card(client):
    pid, session = create_session(client)
    response = client.post(f'/api/projects/{pid}/discovery/sessions', json={
        'operation_id': 'another-source', 'title': session['payload']['candidate']['title'],
        'entry_mode': 'PROBLEM', 'statement': '同项目同名但不同来源。'})
    other = response.json()
    service = client.app.state.planning_delivery
    saved_stage(service, pid, other, 'not-selected')
    service._claim(pid, DeliveryStage(operation_id='not-a-stage', session_ref=reference(session)), 'EXPORT')
    response = listing(client, pid, session)
    assert response.status_code == 200 and response.json()['entries'] == []


def test_unknown_pending_and_failed_originals_are_not_discarded_or_replayed(client):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    saved_stage(service, pid, session, 'unknown-original', status='UNKNOWN_OUTCOME')
    saved_stage(service, pid, session, 'pending-original', status=None)
    failed = saved_stage(service, pid, session, 'failed-original', status=None)
    service._failed(failed, ValueError('fixture definite failure'))
    before = ledger_bytes(service)
    response = listing(client, pid, session)
    assert response.status_code == 200, response.text
    rows = {item['operation_id']: item for item in response.json()['entries']}
    assert rows['unknown-original']['status'] == 'UNKNOWN_OUTCOME'
    assert rows['unknown-original']['current_review_status'] is None
    assert rows['pending-original']['status'] == 'PROCESSING'
    assert rows['pending-original']['basis_current'] is None
    assert rows['failed-original']['status'] == 'FAILED_OR_PARTIAL'
    assert rows['failed-original']['current_review_status'] is None
    assert all(row['session_ref'] == reference(session) for row in rows.values())
    assert ledger_bytes(service) == before


@pytest.mark.parametrize('damage', ['wrapper_digest', 'request_digest', 'folder_digest',
                                    'result_scope', 'foreign_project_claim', 'missing_claim'])
def test_polluted_ledger_fails_explicitly_instead_of_omitting_a_record(client, damage):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    folder = saved_stage(service, pid, session)
    claim_path = folder / 'request.json'
    if damage == 'wrapper_digest':
        value = json.loads(claim_path.read_bytes()); value['sha256'] = '0' * 64
        claim_path.write_text(canonical(value))
    elif damage == 'request_digest':
        value = service._read(claim_path); value['request_sha256'] = '0' * 64
        claim_path.write_text(canonical({'payload': value, 'sha256': digest(value)}))
    elif damage == 'folder_digest':
        folder.rename(folder.with_name('a' * 64))
    elif damage == 'result_scope':
        path = folder / 'result.json'; value = service._read(path)
        value['session_ref'] = {**reference(session), 'sha256': '0' * 64}
        path.write_text(canonical({'payload': value, 'sha256': digest(value)}))
    elif damage == 'foreign_project_claim':
        _, foreign = create_session(client)
        value = service._read(claim_path); value['input']['session_ref'] = reference(foreign)
        value['request_sha256'] = digest({'kind': value['kind'], 'input': value['input']})
        claim_path.write_text(canonical({'payload': value, 'sha256': digest(value)}))
    else:
        claim_path.unlink()
    before = ledger_bytes(service)
    response = listing(client, pid, session)
    assert response.status_code == 409, response.text
    assert ledger_bytes(service) == before


@pytest.mark.parametrize('bound', ['records', 'entries', 'bytes', 'files'])
def test_capacity_is_explicit_error_without_partial_or_guessed_latest(client, monkeypatch, bound):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    saved_stage(service, pid, session, 'one')
    saved_stage(service, pid, session, 'two')
    name = {'records': 'MAX_STAGE_HISTORY_DIRECTORIES', 'entries': 'MAX_STAGE_HISTORY_ENTRIES',
            'bytes': 'MAX_STAGE_HISTORY_METADATA_BYTES',
            'files': 'MAX_STAGE_HISTORY_METADATA_FILES'}[bound]
    monkeypatch.setattr(module, name, 1)
    before = ledger_bytes(service)
    response = listing(client, pid, session)
    assert response.status_code == 409, response.text
    assert 'capacity' in response.json()['error']['message'].lower()
    assert ledger_bytes(service) == before


def test_empty_read_does_not_create_ledger_or_claim(client):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    root = service.ws.data_dir / 'planning-delivery'
    assert not root.exists()
    response = listing(client, pid, session)
    assert response.status_code == 200, response.text
    assert response.json() == {'project_id': pid, 'session_ref': reference(session),
                               'entries': [], 'complete': True}
    assert not root.exists()


def test_symlink_ledger_or_claim_is_not_followed(client, tmp_path):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    folder = saved_stage(service, pid, session)
    external = tmp_path / 'external'; folder.rename(external)
    folder.symlink_to(external, target_is_directory=True)
    response = listing(client, pid, session)
    assert response.status_code in (409, 422), response.text
    assert (external / 'request.json').is_file()


def test_listing_never_hashes_outputs_or_invokes_generation(client, monkeypatch):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    saved_stage(service, pid, session)
    def forbidden(*args, **kwargs):
        raise AssertionError('metadata history must not inspect files or generate work')
    for name in ('operation', '_present', 'stage', '_put', '_claim'):
        monkeypatch.setattr(service, name, forbidden)
    response = listing(client, pid, session)
    assert response.status_code == 200, response.text
    assert response.json()['entries'][0]['basis_current'] is None
    assert response.json()['entries'][0]['current_review_status'] is None


def test_context_change_during_history_read_refuses_partial_response(client, monkeypatch):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    saved_stage(service, pid, session)
    original = Path.lstat
    changed = False
    def concurrent_edit(path, *args, **kwargs):
        nonlocal changed
        result = original(path, *args, **kwargs)
        if not changed and path.name == 'request.json' and inspect.currentframe().f_back.f_code.co_name == 'record':
            changed = True
            service.discovery.edit(pid, session['id'], DiscoveryEdit(
                operation_id='concurrent-context-change', expected_revision=1,
                candidate=session['payload']['candidate'], change_reason='读取期间切到新版本'))
        return result
    monkeypatch.setattr(Path, 'lstat', concurrent_edit)
    response = listing(client, pid, session)
    assert response.status_code == 409, response.text
    assert 'context changed' in response.json()['error']['message'].lower()


def test_incomplete_stage_get_keeps_original_session_for_local_key_validation(client):
    pid, selected = create_session(client)
    service = client.app.state.planning_delivery
    other = client.post(f'/api/projects/{pid}/discovery/sessions', json={
        'operation_id': 'same-project-other-session', 'title': '同名阶段任务',
        'entry_mode': 'PROBLEM', 'statement': '本地索引误指另一会话时不能冒当前结果。'}).json()
    saved_stage(service, pid, other, 'another-session-pending', status=None)
    before = ledger_bytes(service)
    response = client.get(f'/api/projects/{pid}/planning-delivery/operations/another-session-pending')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['status'] == 'PROCESSING' and value['project_id'] == pid
    assert value['session_ref'] == reference(other)
    assert value['session_ref']['id'] != selected['id']
    assert ledger_bytes(service) == before


@pytest.mark.parametrize('damage', ['key', 'request_digest', 'source'])
def test_incomplete_get_rejects_broken_claim_scope(client, damage):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    folder = saved_stage(service, pid, session, status=None)
    path = folder / 'request.json'; value = service._read(path)
    if damage == 'key':
        value['input']['operation_id'] = 'another-key'
    elif damage == 'source':
        value['input']['session_ref']['sha256'] = '0' * 64
    value['request_sha256'] = ('0' * 64 if damage == 'request_digest' else
                               digest({'kind': value['kind'], 'input': value['input']}))
    path.write_text(canonical({'payload': value, 'sha256': digest(value)}))
    response = client.get(f'/api/projects/{pid}/planning-delivery/operations/stage-from-api')
    assert response.status_code == 409, response.text


def test_metadata_growth_after_stat_cannot_evade_actual_read_budget(client, monkeypatch):
    pid, session = create_session(client)
    service = client.app.state.planning_delivery
    folder = saved_stage(service, pid, session)
    request = folder / 'request.json'
    initial = request.stat().st_size
    total = initial + (folder / 'result.json').stat().st_size
    monkeypatch.setattr(module, 'MAX_STAGE_HISTORY_METADATA_BYTES', total + 20)
    real = Path.lstat
    grown = False
    def stale_size(path, *args, **kwargs):
        nonlocal grown
        result = real(path, *args, **kwargs)
        if path == request and not grown and inspect.currentframe().f_back.f_code.co_name == 'record':
            grown = True
            with request.open('ab') as stream:
                stream.write(b' ' * 1000)
        return result
    monkeypatch.setattr(Path, 'lstat', stale_size)
    response = listing(client, pid, session)
    assert grown is True
    assert response.status_code == 409, response.text


def test_joint_real_stage_http_directory_and_operation_fixture(client, tmp_path, tmp_path_factory):
    """One isolated zero-model stage, then the two real GETs consumed by UI.

    Four assets without a graph keep this a protocol fixture, not B/Word/native
    acceptance. Output is optional outside the test's isolated D directory.
    """
    import os
    pid, session = create_session(client)
    folder = tmp_path_factory.mktemp('joint-selected-project')
    bound = client.post(f'/api/projects/{pid}/local-workspaces', json={
        'idempotency_key': 'joint-selected-project',
        'root': str(folder), 'allowed_write_paths': ['outputs/']})
    assert bound.status_code == 201, bound.text
    workspace = bound.json()
    stage = client.post(f'/api/projects/{pid}/planning-delivery/sessions/{session["id"]}/stage', json={
        'operation_id': 'joint-original-stage', 'session_ref': reference(session),
        'workspace_ref': {key: workspace[key] for key in ('id', 'revision', 'binding_sha256')},
        'directory': 'outputs/reader-fixture', 'graph_ids': []})
    assert stage.status_code == 200, stage.text
    assert stage.json()['status'] == 'STAGE_REPORT'
    assert len(stage.json()['project_files']) == 4
    directory = listing(client, pid, session)
    assert directory.status_code == 200, directory.text
    operation = client.get(f'/api/projects/{pid}/planning-delivery/operations/joint-original-stage')
    assert operation.status_code == 200, operation.text
    assert operation.json()['current_review_status'] == 'CURRENT'
    assert operation.json()['operation_id'] == directory.json()['entries'][0]['operation_id']
    assert operation.json()['session_ref'] == directory.json()['session_ref'] == reference(session)
    target = Path(os.environ.get('EEAS_STAGE_HISTORY_JOINT_DIR', str(tmp_path / 'joint-fixture')))
    target.mkdir(exist_ok=False)
    captures = {}
    for name, response in [('DIRECTORY', directory), ('OPERATION', operation)]:
        path = target / (name + '.response.raw.json'); path.write_bytes(response.content)
        captures[name.lower()] = {'path': str(path), 'bytes': len(response.content),
            'sha256': hashlib.sha256(response.content).hexdigest(), 'http_status': response.status_code}
    metadata = {'scope': 'ISOLATED_ASGI_REAL_STAGE_ZERO_MODEL_FOUR_ASSETS_NO_NATIVE_OR_USER_OUTPUT',
        'project_id': pid, 'session_ref': reference(session), 'responses': captures,
        'fixture_stage_POSTs': 1, 'fixture_directory_GETs': 1, 'fixture_operation_GETs': 1,
        'fixture_setup_POSTs': {'project': 1, 'session': 1, 'workspace': 1},
        'actual_App_API_calls': 0, 'model_calls': 0, 'native_or_B_seven_file_qualification': False}
    (target / 'JOINT_HTTP_FIXTURE.r1.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    service = client.app.state.planning_delivery
    with service.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0
