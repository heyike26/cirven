"""Real Store/private API and original owner GETs; no live model or Windows."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import httpx

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.native_capabilities import register_native_capability_routes
from packages.agent_sessions.models import OpenSession
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Store, Conflict, InvalidInput, NotFound
from packages.domain.tasks import digest
from test_agent_sessions import scene, owner, ModelWire
from test_native_capabilities import local_mcp

TOKEN = 'a' * 64
ROOT = '/api/desktop-host/capabilities'


def api(manager, tmp_path):
    app = FastAPI()
    for error, status in [(Conflict, 409), (InvalidInput, 422), (NotFound, 404)]:
        def handler(code):
            async def result(_request: Request, value):
                return JSONResponse(status_code=code, content={'error': str(value)})
            return result
        app.add_exception_handler(error, handler(status))
    register_native_capability_routes(app, manager.store, manager.data_dir,
        authority=NativeCollaborationAuthority(tmp_path / 'host', token=TOKEN, owner_pid=123, platform='posix'), service=manager)
    return app


def manager_at(tmp_path):
    return NativeCapabilities(Store(tmp_path / 'store.sqlite'), tmp_path)


def configure(manager, tmp_path):
    config, _ = local_mcp(tmp_path)
    saved = manager.configure_mcp({'id': 'review-local', 'operation_id': 'configure', 'configuration': config})
    return saved['config_ref'], config


def selection(catalog):
    server = catalog['mcp_servers'][0]
    tool = server['tools'][0]
    return {'server_id': server['id'], 'config_ref': server['config_ref'], 'probe_ref': server['probe_ref'],
            'tool_id': tool['id'], 'name': tool['name'], 'input_schema_sha256': tool['input_schema_sha256']}


def files(root, *, authority_only=False):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file() and path.suffix != '.sqlite'
            and (not authority_only or 'runtime' not in path.relative_to(root).parts)}


def test_private_exact_mcp_read_and_original_operation_survive_restart_without_mutation(tmp_path):
    manager = manager_at(tmp_path)
    ref, config = configure(manager, tmp_path)
    manager = NativeCapabilities(manager.store, tmp_path)
    before = files(tmp_path / 'native-capabilities')
    with TestClient(api(manager, tmp_path)) as client:
        path = ROOT + '/mcp/review-local'
        params = {'revision': ref['revision'], 'sha256': ref['sha256']}
        assert client.get(path, params=params).status_code == 409
        result = client.get(path, params=params, headers={'X-EEAS-Desktop-Host': TOKEN})
        assert result.status_code == 200 and result.json()['configuration'] == config
        assert result.json()['config_ref'] == ref and result.json()['current_configuration'] is True
        bad = client.get(path, params={**params, 'sha256': '0' * 64}, headers={'X-EEAS-Desktop-Host': TOKEN})
        assert bad.status_code == 409
        operation = client.get(ROOT + '/operations/configure', headers={'X-EEAS-Desktop-Host': TOKEN}).json()
        assert operation['operation_id'] == 'configure' and operation['receipt']['config_ref'] == ref
        assert operation['configuration'] == config and operation['kind'] == 'mcp'
        assert operation['input_sha256'] == digest({'kind': 'mcp', 'id': ref['id'], 'expected_revision': 0, 'configuration': config})
        assert client.get(ROOT + '/operations/missing', headers={'X-EEAS-Desktop-Host': TOKEN}).status_code == 404
    assert files(tmp_path / 'native-capabilities') == before


def test_real_official_probe_to_catalog_to_exact_new_session_ask_policy(tmp_path):
    manager = manager_at(tmp_path)
    ref, _ = configure(manager, tmp_path)
    asyncio.run(manager.probe_mcp(ref['id'], {'config_ref': ref, 'allowed_effects': ['PROCESS_START']}))
    before = files(tmp_path / 'native-capabilities')
    with TestClient(api(manager, tmp_path)) as client:
        response = client.get(ROOT + '/tools', headers={'X-EEAS-Desktop-Host': TOKEN})
        assert response.status_code == 200
        catalog = response.json()
        assert catalog['owner']['status'] == 'NOT_SELECTED' and catalog['builtin_tools'] == []
        item = selection(catalog)
        assert item['tool_id'] == 'review-local_local_echo'
        assert catalog['read_only'] and not catalog['effects_applied'] and catalog['model_calls'] == 0
    revived = NativeCapabilities(manager.store, tmp_path)
    revived.validate_tool_selection([item], [item['tool_id']])
    from packages.agent_sessions import AgentSessions
    sessions = AgentSessions(manager.store, tmp_path, provider_registry=None, workspaces=None, capabilities=revived)
    request = OpenSession.model_validate({'operation_id': 'explicit-create-only',
        'provider_profile_ref': {'id': 'provider', 'revision': 1, 'sha256': 'a'*64},
        'capability_binding_sha256': catalog['capability_binding_sha256'],
        'allowed_effects': ['PROCESS_START'], 'mcp_tool_allowlist': [item['tool_id']], 'mcp_tool_selection': [item]})
    sessions._admit_open(request, revived.native_binding_snapshot())
    assert sessions._permissions(request.model_dump())[item['tool_id']] == 'ask'
    assert sessions._permissions(request.model_dump())['*'] == 'deny'
    assert sessions.active == {} and files(tmp_path / 'native-capabilities') == before
    # Selecting a discovered tool never silently adds process-start authority.
    with pytest.raises(Conflict, match='PROCESS_START'):
        sessions._admit_open(request.model_copy(update={'allowed_effects': []}), revived.native_binding_snapshot())


def test_changed_configuration_probe_and_exact_selection_cannot_be_rebound(tmp_path):
    manager = manager_at(tmp_path)
    ref, _ = configure(manager, tmp_path)
    asyncio.run(manager.probe_mcp(ref['id'], {'config_ref': ref, 'allowed_effects': ['PROCESS_START']}))
    item = selection(manager.tool_catalog())
    for key, value in [('tool_id', 'review-local_wrong'), ('input_schema_sha256', '0'*64), ('server_id', 'other')]:
        changed = {**item, key: value}
        with pytest.raises(Conflict):
            manager.validate_tool_selection([changed], [changed['tool_id']])
    with pytest.raises(Conflict):
        manager.validate_tool_selection([item], [])
    # A new probe of the same configuration is a different discovery receipt.
    asyncio.run(manager.probe_mcp(ref['id'], {'config_ref': ref, 'allowed_effects': ['PROCESS_START']}))
    with pytest.raises(Conflict, match='discovery changed'):
        manager.validate_tool_selection([item], [item['tool_id']])
    manager.set_enabled('mcp', ref['id'], {'operation_id': 'disable', 'expected_revision': 1, 'enabled': False})
    assert manager.tool_catalog()['mcp_servers'][0]['availability'] == 'DISABLED'
    with pytest.raises(Conflict):
        manager.mcp_content(ref)
    with pytest.raises(Conflict):
        manager.validate_tool_selection([item], [item['tool_id']])


def test_sanitized_original_names_collision_is_visible_and_not_selectable(tmp_path):
    manager = manager_at(tmp_path)
    ref, _ = configure(manager, tmp_path)
    # Explicit original-probe ledger boundary fixture, not a claimed live server.
    manager._record_probe({'id': 'b'*32, 'config_ref': ref, 'status': 'INITIALIZED_AND_LISTED',
        'initialized': True, 'list_tools_succeeded': True, 'tools': [
            {'name': 'review.file', 'input_schema_sha256': 'a'*64},
            {'name': 'review file', 'input_schema_sha256': 'b'*64}]})
    server = manager.tool_catalog()['mcp_servers'][0]
    assert server['availability'] == 'COLLISION' and server['tools'] == []
    with pytest.raises(Conflict):
        manager.validate_tool_selection([{'server_id': ref['id'], 'config_ref': ref, 'probe_ref': server['probe_ref'],
            'tool_id': 'review-local_review_file', 'name': 'review.file', 'input_schema_sha256': 'a'*64}], ['review-local_review_file'])


def test_unknown_operation_proves_exact_old_and_new_configuration_not_latest(tmp_path):
    manager = manager_at(tmp_path)
    ref, config = configure(manager, tmp_path)
    enabled = manager.set_enabled('mcp', ref['id'], {'operation_id': 'disable', 'expected_revision': 1, 'enabled': False})
    manager.set_enabled('mcp', ref['id'], {'operation_id': 'enable-later', 'expected_revision': 2, 'enabled': True})
    proof = manager.operation('disable')
    assert proof['receipt'] == enabled and proof['previous_config_ref'] == ref
    assert proof['previous_configuration'] == config
    assert proof['configuration'] == {**config, 'enabled': False}
    assert proof['receipt']['config_ref']['revision'] == 2


def test_actual_original_owner_registry_is_read_only_and_expiry_never_restarts(scene):
    wire = ModelWire([]).start()
    sessions, request = owner(scene, wire)
    async def check():
        current = await sessions.open(scene['pid'], request)
        scope = {'project_id': scene['pid'], 'session_ref': current['session_ref'], 'generation': current['generation']}
        # The real original HTTP server appends its own GET access log. The
        # controller's immutable manifest/state/operations must remain unchanged.
        before = files(scene['data'] / 'agent-sessions', authority_only=True)
        try:
            result = await sessions.tool_catalog(scope)
            assert result['owner']['status'] == 'CURRENT', result
            assert result['owner']['scope'] == scope and result['builtin_tools']
            by_id = {item['id']: item for item in result['builtin_tools']}
            assert by_id['read']['permission'] == 'ask' and by_id['question']['permission'] == 'allow'
            assert all(item['selectable_for_new_session'] is False for item in by_id.values())
            app = api(scene['manager'], scene['data'])
            app.state.agent_sessions = sessions
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://original-owner-fixture') as client:
                routed = await client.get(ROOT + '/tools', params={'scope': json.dumps(scope)}, headers={'X-EEAS-Desktop-Host': TOKEN})
                assert routed.status_code == 200 and routed.json()['owner']['status'] == 'CURRENT'
                assert routed.json()['owner']['scope'] == scope
                rejected = await client.get(ROOT + '/tools', params={'scope': json.dumps({**scope, 'generation': current['generation']+1})}, headers={'X-EEAS-Desktop-Host': TOKEN})
                assert rejected.status_code == 409
            with pytest.raises(Conflict):
                await sessions.tool_catalog({**scope, 'generation': current['generation'] + 1})
            with pytest.raises(Conflict):
                await sessions.tool_catalog({**scope, 'session_ref': {**current['session_ref'], 'sha256': '0'*64}})
            assert files(scene['data'] / 'agent-sessions', authority_only=True) == before
            context = sessions.active[current['session_ref']['id']]
            process = context['process']
            context['expires_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            expired = await sessions.tool_catalog(scope)
            assert expired['owner']['status'] == 'STALE' and expired['owner']['reason'] == 'OWNER_EXPIRED'
            assert expired['builtin_tools'] == [] and context['process'] is process
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://original-owner-fixture') as client:
                routed = await client.get(ROOT + '/tools', params={'scope': json.dumps(scope)}, headers={'X-EEAS-Desktop-Host': TOKEN})
                assert routed.status_code == 200 and routed.json()['owner']['status'] == 'STALE'
            assert wire.requests == []
        finally:
            await sessions.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()


def test_bad_owner_scope_and_unavailable_owner_do_not_create_runtime(tmp_path):
    manager = manager_at(tmp_path)
    with TestClient(api(manager, tmp_path)) as client:
        for value in ['{}', '{"project_id":"x","session_ref":{},"generation":1}', '[1]', '{"generation":1,"generation":2}']:
            result = client.get(ROOT + '/tools', params={'scope': value}, headers={'X-EEAS-Desktop-Host': TOKEN})
            assert result.status_code == 409
        assert not (tmp_path / 'agent-sessions').exists()
