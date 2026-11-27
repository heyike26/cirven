"""Readonly public open recovery against real ASGI/SQLite and saved owner records."""
from packages.domain.tasks import digest
from test_conversation_directory import api, check, project, unit_session, forbid


def receipt(owner, pid, sid, key='open_original'):
    manifest = owner._manifest(pid, sid)[0]
    path = owner.data_dir / 'agent-sessions' / 'open-operations' / (digest([pid, key]) + '.json')
    owner._write(path, {'id': sid, 'request_sha256': digest(manifest['request'])})
    return path, manifest


def query(client, key='open_original', pid=None):
    return client.get('/api/agent-sessions/open-operations/' + key,
                      params={'project_id': pid} if pid is not None else {})


def test_missing_open_query_creates_no_internal_project_or_runtime(api, monkeypatch):
    owner = api.app.state.agent_sessions
    monkeypatch.setattr(owner, 'internal_project', forbid)
    monkeypatch.setattr(owner, 'open', forbid)
    monkeypatch.setattr(owner, '_start', forbid)
    with api.app.state.store.connect() as connection:
        before = connection.execute('SELECT count(*) FROM projects').fetchone()[0]
    check(query(api).status_code == 404)
    check(query(api, pid='absent').status_code == 404)
    with api.app.state.store.connect() as connection:
        check(connection.execute('SELECT count(*) FROM projects').fetchone()[0] == before)


def test_exact_public_open_query_never_inspects_starts_or_changes_saved_sources(api, monkeypatch):
    owner, pid, sid, _ = unit_session(api, guided=False)
    path, manifest = receipt(owner, pid, sid)
    paths = [path, owner._path(pid, sid, 'manifest.json'), owner._path(pid, sid, 'state.json')]
    before = [p.read_bytes() for p in paths]
    for name in ['open', 'inspect', '_start', 'internal_project']:
        monkeypatch.setattr(owner, name, forbid)
    response = query(api, pid=pid); check(response.status_code == 200, response.text)
    row = response.json()
    check(row['operation_id'] == 'open_original' and row['request_sha256'] == digest(manifest['request']))
    check(row['session']['session_ref']['id'] == sid and row['project_id'] == pid)
    check(not row['owner_started_by_query'] and row['model_calls_by_query'] == 0)
    check(not row['source_changed_by_query'] and not row['automatic_post_replay'])
    check(not row['session']['connected_owner'] and before == [p.read_bytes() for p in paths])
    check(query(api, pid='foreign').status_code == 404)


def test_default_chat_lookup_uses_exact_storage_marker_and_does_not_create_it(api, monkeypatch):
    owner, pid, sid, _ = unit_session(api, guided=False)
    receipt(owner, pid, sid)
    project(api, 'EEAS conversation')
    check(query(api).status_code == 404)
    with api.app.state.store.connect() as connection:
        connection.execute('UPDATE projects SET description=? WHERE id=?',
            ('eeas.original-agent-session.internal-project.v1', pid))
    monkeypatch.setattr(owner, 'internal_project', forbid)
    check(query(api).json()['session']['session_ref']['id'] == sid)


def test_corrupt_open_receipt_fails_closed_before_owner_read(api, monkeypatch):
    owner, pid, sid, _ = unit_session(api, guided=False)
    path, _ = receipt(owner, pid, sid)
    owner._write(path, {'id': sid, 'request_sha256': '0' * 64}, replace=True)
    monkeypatch.setattr(owner, 'get', forbid)
    check(query(api, pid=pid).status_code == 409)


def test_private_native_open_cannot_be_adopted_as_public_chat_connection(api, monkeypatch):
    owner, pid, sid, _ = unit_session(api, guided=False)
    _, manifest = receipt(owner, pid, sid)
    manifest['native_message_admission'] = {'separate_private_scope': True}
    owner._write(owner._path(pid, sid, 'manifest.json'), manifest, replace=True)
    monkeypatch.setattr(owner, 'get', forbid)
    check(query(api, pid=pid).status_code == 409)


def test_incomplete_registration_is_observed_without_repair_or_start(api, monkeypatch):
    owner, pid, sid, _ = unit_session(api, guided=False)
    created = api.post('/api/conversations', json={'operation_id': 'new_chat', 'project_id': pid}).json()
    manifest = owner._manifest(pid, sid)[0]
    manifest['request']['conversation_ref'] = created['conversation_ref']
    owner._write(owner._path(pid, sid, 'manifest.json'), manifest, replace=True)
    receipt(owner, pid, sid)
    monkeypatch.setattr(owner.conversations, 'bind_open', forbid)
    monkeypatch.setattr(owner, '_start', forbid)
    response = query(api, pid=pid); check(response.status_code == 200, response.text)
    check(response.json()['conversation_ref'] == created['conversation_ref'])
    check(response.json()['conversation_bound'] is False)
    with api.app.state.store.connect() as connection:
        check(connection.execute("SELECT count(*) FROM conversation_source_aliases WHERE kind='original'").fetchone()[0] == 0)
