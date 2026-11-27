"""Real HTTP/Store metadata reads; no SDK, model or native UI qualification."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from packages.domain.conversation_threads import ConversationThreads, source_identity
from packages.domain.store import NotFound
from test_native_conversations_source79 import scene, send, TOKEN


def name_discussion(store, pid, cid, title):
    threads = ConversationThreads(store)
    source = {'kind': 'native', 'project_id': pid, 'conversation_id': cid}
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        threads.enroll(connection, {'id': source_identity(source)[0], 'source': source,
            'kind': 'native', 'project_id': pid, 'title': title}, created_at=datetime.now(timezone.utc).isoformat())
    return threads


def test_exact_existing_metadata_title_reaches_both_directory_and_discussion_without_execution_change(tmp_path):
    app, native, a, _ = scene(tmp_path)
    with TestClient(app, headers={'X-EEAS-Desktop-Host': TOKEN}) as client:
        send(client, a, 'same-id', 'original-title-request', 'This exact input must remain unchanged 😀')
        before = native.path('original-title-request').read_bytes()
        app.state.conversation_threads = name_discussion(app.state.store, a, 'same-id', '交付验收 · 保留人工修改 😀')
        directory = client.get('/api/desktop-host/native-conversations', params={'project_id': a}).json()
        value = client.get('/api/desktop-host/native-conversations/same-id', params={'project_id': a}).json()
        assert directory['conversations'][0]['title'] == value['title'] == '交付验收 · 保留人工修改 😀'
        assert value['project_id'] == a and value['conversation_id'] == 'same-id'
        assert value['latest_provider_profile_ref'] is None and value['messages'][0]['status'] == 'NOT_CONFIGURED'
        assert native.path('original-title-request').read_bytes() == before
        assert not native.transport.active and not native.tasks


def test_same_legacy_cid_in_another_project_cannot_supply_the_title_or_change_routing(tmp_path):
    app, native, a, b = scene(tmp_path)
    with TestClient(app, headers={'X-EEAS-Desktop-Host': TOKEN}) as client:
        send(client, a, 'same-id', 'a-title', 'A original')
        send(client, b, 'same-id', 'b-title', 'B original')
        app.state.conversation_threads = name_discussion(app.state.store, a, 'same-id', 'A 的交付')
        name_discussion(app.state.store, b, 'same-id', 'B 的审阅')
        a_result = client.get('/api/desktop-host/native-conversations/same-id', params={'project_id': a}).json()
        b_result = client.get('/api/desktop-host/native-conversations/same-id', params={'project_id': b}).json()
        assert a_result['title'] == 'A 的交付' and b_result['title'] == 'B 的审阅'
        assert [m['operation_id'] for m in a_result['messages']] == ['a-title']
        assert [m['operation_id'] for m in b_result['messages']] == ['b-title']
        assert not native.transport.active and not native.tasks


def test_title_read_without_metadata_never_enrolls_or_names_a_conversation_from_raw_instructions(tmp_path):
    app, native, a, _ = scene(tmp_path)
    with TestClient(app, headers={'X-EEAS-Desktop-Host': TOKEN}) as client:
        send(client, a, 'old-unorganized', 'old-title', '{"internal-looking": "still exact original input"}')
        app.state.conversation_threads = ConversationThreads(app.state.store)
        before = native.path('old-title').read_bytes()
        value = client.get('/api/desktop-host/native-conversations/old-unorganized', params={'project_id': a}).json()
        assert value.get('title') is None
        with app.state.store.connect() as connection:
            assert connection.execute('SELECT count(*) FROM conversation_threads').fetchone()[0] == 0
        assert native.path('old-title').read_bytes() == before


def test_naming_after_organization_move_never_rewrites_the_original_execution_project(tmp_path):
    app, native, a, b = scene(tmp_path)
    with TestClient(app, headers={'X-EEAS-Desktop-Host': TOKEN}) as client:
        send(client, a, 'old-source', 'before-move', 'Original execution remains in project A')
        threads = name_discussion(app.state.store, a, 'old-source', '已命名的历史讨论')
        app.state.conversation_threads = threads
        source = {'kind': 'native', 'project_id': a, 'conversation_id': 'old-source'}
        before = native.path('before-move').read_bytes()
        with app.state.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            threads.move(connection, source_identity(source)[0], b, updated_at=datetime.now(timezone.utc).isoformat())
        value = client.get('/api/desktop-host/native-conversations/old-source', params={'project_id': a}).json()
        assert value['title'] == '已命名的历史讨论' and value['project_id'] == a
        assert value['messages'][0]['project_id'] == a and value['messages'][0]['operation_id'] == 'before-move'
        # The small route fixture intentionally omits the product's global
        # NotFound-to-HTTP handler; its domain rejection still must be exact.
        with pytest.raises(NotFound, match='Native discussion does not exist'):
            client.get('/api/desktop-host/native-conversations/old-source', params={'project_id': b})
        assert native.path('before-move').read_bytes() == before
