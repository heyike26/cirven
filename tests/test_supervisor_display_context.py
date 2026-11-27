"""Real Store/collector/display persistence checks; no runtime or model invoked."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from apps.api.supervisor import ApplicationTaskCollector
from packages.agent_sessions.service import AgentSessions
from packages.agent_sessions.supervisor import GlobalSupervisor, TaskDisplayContext
from packages.domain.conversation_threads import ConversationThreads, source_identity
from packages.domain.store import Store
from packages.domain.tasks import digest, now


def recorded_task(store, sessions, sid, created):
    pid = store.create_project('同名项目', '')['id']
    opid = 'message-' + sid
    manifest = {'id': sid, 'project_id': pid, 'created_at': created, 'request': {}}
    sessions._write(sessions._path(pid, sid, 'manifest.json'), manifest)
    sessions._write(sessions._path(pid, sid, 'state.json'), {
        'generation': 1, 'active_operation_id': None, 'pending_questions': [],
        'pending_permissions': [], 'observation_at': now()})
    sessions._write(sessions._path(pid, sid, 'operations', digest(opid) + '.json'), {
        'id': opid, 'kind': 'MESSAGE', 'status': 'RETURNED', 'created_at': created,
        'operation_ref': {'id': opid, 'request_sha256': 'a' * 64},
        'request': {'generation': 1, 'raw_text': 'SECRET_PROMPT_MUST_NOT_LEAK'}})
    reference = sessions._manifest(pid, sid)[1]
    source = {'kind': 'original', 'project_id': pid, 'session_ref': reference, 'workspace_ref': None}
    alias, _ = source_identity(source)
    with store.connect() as connection:
        ConversationThreads(store).enroll(connection, {'id': alias, 'kind': 'original',
            'project_id': pid, 'title': '同名讨论', 'source': source}, created_at=created)
    return pid, alias


def setup_collector(tmp_path):
    store = Store(tmp_path / 'state.sqlite'); store.migrate()
    sessions = AgentSessions(store, tmp_path, provider_registry=None, workspaces=None, capabilities=None)
    sessions.inspect = lambda *_: pytest.fail('Display collection cannot inspect or load an owner')
    collector = ApplicationTaskCollector(SimpleNamespace(state=SimpleNamespace(store=store, agent_sessions=sessions)))
    return store, sessions, collector


def test_same_project_and_conversation_names_keep_distinct_real_creation_context(tmp_path):
    store, sessions, collector = setup_collector(tmp_path)
    first = '2026-10-26T02:00:00+00:00'
    second = '2026-10-26T05:00:00+00:00'
    recorded_task(store, sessions, 'a' * 32, first)
    recorded_task(store, sessions, 'b' * 32, second)
    result = collector.collect_sync()
    tasks = result['observations']
    assert len(tasks) == 2 and result['unavailable_codes'] == []
    assert {t['label'] for t in tasks} == {'同名讨论'}
    assert {t['display_context']['task_created_at'] for t in tasks} == {first, second}
    assert {t['display_context']['conversation_created_at'] for t in tasks} == {first, second}
    assert all(t['display_context']['project_label'] == '同名项目' for t in tasks)
    assert all(t['observed_at'] not in {first, second} for t in tasks)
    assert len({t['key']['project_id'] for t in tasks}) == 2
    assert 'SECRET_PROMPT_MUST_NOT_LEAK' not in json.dumps(tasks)
    assert collector.coverage['model_calls'] == 0


def test_corrupt_alias_context_is_not_trusted_and_never_changes_returned_outcome(tmp_path):
    store, sessions, collector = setup_collector(tmp_path)
    _, alias = recorded_task(store, sessions, 'c' * 32, '2026-10-26T05:00:00+00:00')
    with store.connect() as connection:
        connection.execute('UPDATE conversation_source_aliases SET source_sha256=? WHERE alias_id=?', ('0' * 64, alias))
    task = collector.collect_sync()['observations'][0]
    assert task['label'] == '同名项目'
    assert 'conversation_created_at' not in task['display_context']
    assert task['display_context']['task_created_at'] == '2026-10-26T05:00:00+00:00'
    assert task['source_status'] == 'RETURNED' and task['execution_returned'] is True


def test_public_context_refresh_persists_without_creating_an_execution_alert(tmp_path):
    store, sessions, collector = setup_collector(tmp_path)
    pid, _ = recorded_task(store, sessions, 'd' * 32, '2026-10-26T05:00:00+00:00')
    async def run():
        supervisor = GlobalSupervisor(tmp_path / 'supervision', collector)
        try:
            first = await supervisor.poll_once()
            with store.connect() as connection:
                connection.execute('UPDATE projects SET name=? WHERE id=?', ('项目改名', pid))
            second = await supervisor.poll_once()
            assert second['alerts_total'] == first['alerts_total']
            assert second['tasks'][0]['key'] == first['tasks'][0]['key']
            assert second['tasks'][0]['classification'] == 'EXECUTION_RETURNED'
            assert second['tasks'][0]['display_context']['project_label'] == '项目改名'
        finally:
            await supervisor.close()
    asyncio.run(run())


@pytest.mark.parametrize('value', [
    {'project_label': 'password=secret'}, {'workspace_label': 'bad\nname'},
    {'task_created_at': '2026-10-26T05:00:00'}, {'conversation_created_at': 'not a date'},
    {'project_id': 'not-a-display-field'},
])
def test_context_rejects_credentials_controls_naive_dates_and_identity_fields(value):
    with pytest.raises(ValueError):
        TaskDisplayContext.model_validate(value)
