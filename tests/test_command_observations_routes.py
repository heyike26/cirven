"""Real observer files + durable operations + HTTP; no owner or model on GET.

The original metadata in the first scenes is an explicit test boundary. The
last scene uses the pinned original CLI with only its external model replaced.
Neither case qualifies a Windows engineering task or whole-task acceptance.
"""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from packages.adapters.opencode_command_observation import prepare, bind, record_admission
from packages.agent_sessions import AgentSessions
from packages.domain.local_workspaces import WorkspaceInput
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest
from test_agent_sessions import scene, owner, ModelWire, tool, control, until
from test_opencode_command_observation import command_scene, exercise, observation
from test_session_diffs import app_for, query


def make_routed(command_scene,*,commands=None):
    s = command_scene
    from packages.domain.local_workspaces import LocalWorkspaces
    from packages.domain.store import Store
    store = Store(s['data'] / 'eeas.sqlite3')
    service = AgentSessions(store, s['data'], provider_registry=None,
        workspaces=LocalWorkspaces(store, s['data']), capabilities=None)
    ident = s['ref']['id']
    workspace = {key: s['workspace'][key] for key in ('id', 'revision', 'binding_sha256')}
    manifest = {'id': ident, 'project_id': s['pid'], 'directory_identity': s['workspace']['root_identity'],
        'request': {'workspace_ref': workspace, 'allowed_effects': ['BACKGROUND_COMMAND']}}
    if commands is not None:manifest['request']['command_allowlist']=list(commands)
    s['ref'] = {'id': ident, 'revision': 1, 'sha256': digest(manifest)}
    service._write(service._path(s['pid'], ident, 'manifest.json'), manifest)
    s['runtime'] = service._path(s['pid'], ident, 'runtime')
    s['admission'] = bind(prepare(s['runtime'], project_id=s['pid'], session_ref=s['ref'], generation=1,
        workspace=s['workspace'], source_paths=['a.py']), 'ses_command')
    record_admission(s['runtime'], s['admission'])
    s.update(service=service, ident=ident, workspace_ref=workspace)
    return s


@pytest.fixture
def routed(command_scene):
    return make_routed(command_scene)


def persist(s, packet):
    observed = observation(s, packet)
    # A completed command remains readable while the original task continues.
    state = {**observed, 'owner_status': 'DISCONNECTED', 'active_operation_id': 'still_active', 'native_status': 'busy'}
    s['service']._write(s['service']._path(s['pid'], s['ident'], 'state.json'), state, replace=True)
    operation = {'id': 'original_command', 'kind': 'MESSAGE', 'status': 'ACCEPTED',
        'session_ref': s['ref'], 'generation': 1, 'original_session_id': 'ses_command', 'native_message_id': 'msg_user',
        'request': {'session_ref': s['ref'], 'generation': 1, 'raw_text': 'Original selected command'}}
    p = s['service']._path(s['pid'], s['ident'], 'operations', digest(operation['id']) + '.json')
    s['service']._write(p, operation)
    expected = {'session_sha256': s['ref']['sha256'], 'generation': 1, 'workspace_ref': s['workspace_ref']}
    return state, p, expected


def test_get_is_persisted_readonly_with_exact_scope_and_survives_restart(routed, monkeypatch):
    s = routed
    packet = exercise(s, exit=7)
    state, operation_path, expected = persist(s, packet)
    (s['folder'] / 'a.py').write_text('LATER_MANUAL_EDIT_MUST_SURVIVE\n')
    current_read = Path.read_bytes
    def no_current_source(path):
        if path == s['folder'] / 'a.py':
            raise AssertionError('Command history must not reread the current source')
        return current_read(path)
    monkeypatch.setattr(Path, 'read_bytes', no_current_source)
    async def no_native(*args, **kwargs):
        raise AssertionError('A history GET must not contact an owner or model')
    monkeypatch.setattr(s['service'], '_http', no_native)
    original_state = s['service']._path(s['pid'], s['ident'], 'state.json').read_bytes()
    with TestClient(app_for(s['service'])) as client:
        url = f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}/command-observations"
        result = client.get(url, params=query(expected))
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload['commands'][0]['operation_id'] == 'original_command'
        assert payload['commands'][0]['exit_code'] == 7
        assert payload['commands'][0]['source_status'] == 'UNCHANGED'
        assert payload['coverage']['whole_task_acceptance'] == 'NOT_ASSESSED'
        assert payload['model_calls'] == 0 and payload['automatic_replay'] is False
        assert client.get(url, params=query(expected)).json() == payload
        for bad in ({'generation': 2}, {'session_sha256': 'f' * 64}, {'binding_sha256': 'f' * 64}):
            assert client.get(url, params={**query(expected), **bad}).status_code == 409
        assert client.get(url, params={**query(expected), 'generation': '-1'}).status_code == 422
    assert s['service']._path(s['pid'], s['ident'], 'state.json').read_bytes() == original_state
    reopened = AgentSessions(s['service'].store, s['data'], provider_registry=None,
        workspaces=s['service'].workspaces, capabilities=None)
    assert reopened.command_observations(s['pid'], s['ident'], **expected) == payload
    # A corrupted operation cannot be adopted, and is not reconstructed by a GET.
    operation_path.write_text('{}')
    unavailable = reopened.command_observations(s['pid'], s['ident'], **expected)
    assert not unavailable['commands'] and 'COMMAND_OPERATION_INDEX_UNAVAILABLE' in unavailable['coverage']['reason_codes']


def test_generation_change_during_history_read_is_rejected(routed, monkeypatch):
    s = routed
    _, _, expected = persist(s, exercise(s))
    original = s['service']._state
    reads = 0
    def changed(*args):
        nonlocal reads
        reads += 1
        value = original(*args)
        return {**value, 'generation': 2} if reads > 1 else value
    monkeypatch.setattr(s['service'], '_state', changed)
    with pytest.raises(Conflict, match='generation changed'):
        s['service'].command_observations(s['pid'], s['ident'], **expected)


def test_windows_scandir_metadata_does_not_hide_saved_command_history(routed, monkeypatch):
    s = routed
    _, operation_path, expected = persist(s, exercise(s, exit=7))
    original_operation = operation_path.read_bytes()
    state_path = s['service']._path(s['pid'], s['ident'], 'state.json')
    original_state = state_path.read_bytes()
    original_scandir = os.scandir

    class WindowsCachedEntry:
        def __init__(self, entry):
            self.entry, self.name, self.path = entry, entry.name, entry.path

        def stat(self, *, follow_symlinks):
            value = self.entry.stat(follow_symlinks=follow_symlinks)
            # Python 3.10 on Windows leaves these DirEntry cache fields unset.
            return SimpleNamespace(st_mode=value.st_mode, st_size=value.st_size,
                                   st_nlink=0, st_ino=0, st_dev=0)

    class WindowsCachedScan:
        def __enter__(self):
            self.inner = original_scandir(operation_path.parent)
            return (WindowsCachedEntry(entry) for entry in self.inner)

        def __exit__(self, *_):
            self.inner.close()

    def cached_scandir(path):
        if Path(path) == operation_path.parent:
            return WindowsCachedScan()
        return original_scandir(path)

    async def no_owner_or_model(*args, **kwargs):
        raise AssertionError('Reading saved command history must not contact an owner or model')

    monkeypatch.setattr(os, 'scandir', cached_scandir)
    monkeypatch.setattr(s['service'], '_http', no_owner_or_model)
    with TestClient(app_for(s['service'])) as client:
        url = f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}/command-observations"
        response = client.get(url, params=query(expected))
        assert response.status_code == 200, response.text
        commands = response.json()['commands']
        assert len(commands) == 1, response.text
        assert commands[0]['operation_id'] == 'original_command'
        assert commands[0]['exit_code'] == 7
        assert response.json()['model_calls'] == 0
        # Reading real link metadata must retain the independent-file guard.
        retained_link = operation_path.with_name('retained-own-hardlink')
        os.link(operation_path, retained_link)
        try:
            denied = client.get(url, params=query(expected)).json()
            assert not denied['commands']
            assert 'COMMAND_OPERATION_INDEX_UNAVAILABLE' in denied['coverage']['reason_codes']
        finally:
            retained_link.unlink()
    assert operation_path.read_bytes() == original_operation
    assert state_path.read_bytes() == original_state


@pytest.mark.parametrize('basis', ['directory', 'secret_source', 'secret_root'])
def test_original_cli_directory_grant_and_real_permission_command_feed_readonly_history(scene, basis):
    command = 'printf EEAS_ORIGINAL_OBSERVER_INTEGRATION'
    from test_agent_sessions import KEY
    folder = scene['folder'] / (KEY if basis == 'secret_root' else 'original-code')
    folder.mkdir()
    (folder / 'src').mkdir()
    paths = ['src/'] if basis != 'secret_source' else [KEY + '.py']
    if basis == 'secret_source':
        (folder / paths[0]).write_text('USER_MANUAL_MUST_SURVIVE\n')
    scene['bound'] = scene['workspaces'].bind(scene['pid'], WorkspaceInput(
        idempotency_key='original-directory-grant', root=str(folder), allowed_write_paths=paths))
    wire = ModelWire([tool('bash', {'command': command, 'description': 'Emit the actual integration marker'}),
                      'ORIGINAL_OBSERVER_COMPLETE']).start()
    async def check():
        value, request = owner(scene, wire)
        request.update(command_allowlist=[command], allowed_effects=['BACKGROUND_COMMAND'])
        try:
            opened = await value.open(scene['pid'], request)
            ident = opened['session_ref']['id']
            await value.send(scene['pid'], ident, {**control(opened, 'observed-command'), 'raw_text': 'Run the selected original command'})
            pending = await until(value, scene, ident, lambda view: bool(view['pending_permissions']))
            permission = pending['pending_permissions'][0]
            await value.reply(scene['pid'], ident, permission['request_ref']['id'],
                {**control(opened, 'allow-command-once'), 'request_ref': permission['request_ref'], 'reply': 'once'}, kind='PERMISSION')
            final = await until(value, scene, ident, lambda view: view['active_operation_id'] is None)
            assert len(wire.requests) == 2
            expected = {'session_sha256': opened['session_ref']['sha256'], 'generation': opened['generation'],
                        'workspace_ref': request['workspace_ref']}
            result = value.command_observations(scene['pid'], ident, **expected)
            assert len(result['commands']) == 1, canonical(result)
            observed = result['commands'][0]
            assert observed['operation_id'] == observed['root_operation_id'] == 'observed-command'
            assert observed['exit_code'] == 0 and observed['command'] == command
            assert 'EEAS_ORIGINAL_OBSERVER_INTEGRATION' in observed['output']['text']
            assert observed['source_status'] == 'UNAVAILABLE' and observed['sources'] == []
            assert observed['workdir'] is None
            assert KEY not in canonical(result)
            assert len(wire.requests) == 2
            assert scene['actual'].read_bytes() == scene['original']
            if basis == 'directory':
                await value.close()
                # Real bounded slot reservations, without manufacturing 15
                # model tasks or repeatedly executing the original command.
                history = value._path(scene['pid'], ident, 'runtime', 'command-observation')
                for generation in range(2, 17):
                    (history / f'generation-{generation}').mkdir()
                value, _ = owner(scene, wire)
                loaded = await value.load(scene['pid'], ident, {
                    'operation_id': 'resume-after-observer-capacity', 'session_ref': opened['session_ref']})
                resumed = loaded['session']
                assert resumed['owner_status'] == 'READY'
                assert resumed['generation'] == opened['generation'] + 1
                assert resumed['original_session_id'] == opened['original_session_id']
                after = value.command_observations(scene['pid'], ident,
                    **{**expected, 'generation': resumed['generation']})
                assert after['commands'][0]['record_ref'] == observed['record_ref']
                assert after['commands'][0]['operation_id'] == 'observed-command'
                assert 'COMMAND_GENERATION_CAPACITY_REACHED' in after['coverage']['reason_codes']
                assert len(wire.requests) == 2
        finally:
            await value.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()
