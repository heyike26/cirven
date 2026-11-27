"""Real discovery API/SQLite with injected terminal native-state observations.

No model, Windows process or upstream completion is simulated as qualified.
"""
import json

import pytest

from packages.domain.discovery import DiscoveryWorkbench
from packages.domain.intake import digest
from test_discovery_workbench import create


class TerminalPlanner:
    def __init__(self, store, terminal):
        self.store, self.terminal, self.calls = store, terminal, []

    async def propose(self, request):
        self.calls.append(request)
        with self.store.connect() as c:
            c.execute(
                'INSERT INTO agent_executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('native-outcome', request['project_id'], request['operation_id'],
                 digest(request), 'PROTOCOL_FIXTURE', 'private-fixture', self.terminal,
                 'NOT_RUN', 'original-session', '{}', 'fixture', json.dumps(request),
                 None, '2026-10-21', '2026-10-21', 0))
        return {'status': self.terminal}

    def inspect(self, project_id, execution_id):
        with self.store.connect() as c:
            row = c.execute('SELECT * FROM agent_executions WHERE project_id=? AND id=?',
                            (project_id, execution_id)).fetchone()
            assert row is not None
            return dict(row)

    def stream_events(self, *_args):
        return []


@pytest.mark.parametrize('native,expected', [
    ('UNKNOWN_OUTCOME', 'UNKNOWN_OUTCOME'), ('TIMED_OUT', 'UNKNOWN_OUTCOME'),
    ('INTERRUPTED', 'UNKNOWN_OUTCOME'), ('CANCELLED', 'CANCELLED'), ('FAILED', 'FAILED')])
def test_terminal_native_outcome_survives_api_replay_and_restart(client, tmp_path, native, expected):
    pid, _, _, initial = create(client)
    folder = tmp_path.parent / (tmp_path.name + ' original sources')
    folder.mkdir()
    original = folder / 'manual.md'
    original.write_text('人工原稿，禁止重发覆盖\n', encoding='utf-8')
    response = client.post(f'/api/projects/{pid}/local-workspaces', json={
        'idempotency_key': 'bind', 'root': str(folder)})
    assert response.status_code == 201, response.text
    bound = response.json()
    service = client.app.state.discovery
    planner = TerminalPlanner(client.app.state.store, native)
    service.transport = planner
    base = f'/api/projects/{pid}/discovery'
    body = {'operation_id': 'one-advance', 'expected_revision': initial['revision'],
            'message': '只读梳理，保留未确认事项。',
            'workspace_ref': {k: bound[k] for k in ['id', 'revision', 'binding_sha256']}}
    response = client.post(base + f'/sessions/{initial["id"]}/advance', json=body)
    assert response.status_code == 422, response.text
    assert response.json()['status'] == expected
    assert client.post(base + f'/sessions/{initial["id"]}/advance', json=body).json() == response.json()
    assert len(planner.calls) == 1
    client.app.state.planning_proposals.transport = planner
    restarted = DiscoveryWorkbench(client.app.state.store, client.app.state.planning_proposals)
    observed = restarted.operation(pid, 'one-advance')
    assert observed['status'] == expected
    assert observed['native_execution']['execution_status'] == native
    assert restarted.get(pid, initial['id']) == initial
    assert original.read_text(encoding='utf-8') == '人工原稿，禁止重发覆盖\n'
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM task_runs').fetchone()[0] == 0


@pytest.mark.parametrize('kind,prefix', [('ADVANCE', 'disc-'), ('PLAN', 'discplan-')])
def test_legacy_failed_operation_projects_unknown_without_rewriting_history(client, kind, prefix):
    pid, _, _, initial = create(client)
    op = 'legacy-op'
    result = {'id': initial['id'], 'operation_id': op, 'status': 'FAILED', 'reason': 'Conflict'}
    with client.app.state.store.connect() as c:
        c.execute('INSERT INTO discovery_operations VALUES (?,?,?,?,?,?,?,?)',
                  (pid, op, initial['id'], kind, 'request-pin', 'FAILED', json.dumps(result), digest(result)))
        c.execute('INSERT INTO agent_executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  ('native-outcome', pid, prefix + digest([pid, op])[:40], 'pin', 'PROTOCOL_FIXTURE',
                   'fixture', 'UNKNOWN_OUTCOME', 'NOT_RUN', 'original-session', '{}', 'fixture', '{}',
                   None, '2026-10-21', '2026-10-21', 0))
        before = tuple(c.execute('SELECT * FROM discovery_operations WHERE operation_id=?', (op,)).fetchone())
    planner = TerminalPlanner(client.app.state.store, 'UNKNOWN_OUTCOME')
    client.app.state.discovery.transport = planner
    for _ in range(2):
        observed = client.get(f'/api/projects/{pid}/discovery/operations/{op}').json()
        assert observed['status'] == 'UNKNOWN_OUTCOME'
        assert observed['result'] == result
        assert observed['native_execution']['execution_status'] == 'UNKNOWN_OUTCOME'
    other, _, _, _ = create(client)
    assert client.get(f'/api/projects/{other}/discovery/operations/{op}').status_code == 404
    assert planner.calls == []
    with client.app.state.store.connect() as c:
        assert tuple(c.execute('SELECT * FROM discovery_operations WHERE operation_id=?', (op,)).fetchone()) == before
