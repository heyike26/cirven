"""Real configuration records and official MCP initialize/list/close.

No model, original conversation, Windows, or tool execution is qualified here.
"""
import asyncio
import copy

import pytest

from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.native_tools import tool_catalog
from packages.domain.store import Conflict, InvalidInput, Store
from test_native_capabilities import local_mcp


def configured(manager, root, ident, *, probe=False):
    folder = root / ident
    folder.mkdir()
    config, script = local_mcp(folder)
    receipt = manager.configure_mcp({'operation_id': 'save-' + ident, 'id': ident,
        'configuration': config})
    if probe:
        result = asyncio.run(manager.probe_mcp(ident, {
            'config_ref': receipt['config_ref'], 'allowed_effects': ['PROCESS_START']}))
        assert result['status'] == 'INITIALIZED_AND_LISTED', result
        assert result['tools_invoked'] == 0
    return receipt['config_ref'], script


def selected(server):
    tool = server['tools'][0]
    return {'server_id': server['id'], 'config_ref': server['config_ref'],
        'probe_ref': server['probe_ref'], 'tool_id': tool['id'],
        'name': tool['name'], 'input_schema_sha256': tool['input_schema_sha256']}


def test_unselected_broken_command_is_local_error_not_missing_tool_directory(tmp_path):
    manager = NativeCapabilities(Store(tmp_path / 'store.sqlite'), tmp_path)
    configured(manager, tmp_path, 'chosen', probe=True)
    _, broken = configured(manager, tmp_path, 'other')
    before = manager.native_binding_snapshot()
    assert manager.native_binding_snapshot(validate_mcp_ids=set()) == before
    broken.write_text('raise RuntimeError("changed unselected original")\n')
    with pytest.raises(Conflict, match='file bytes changed'):
        manager.native_binding_snapshot()
    assert manager.native_binding_snapshot(validate_mcp_ids={'chosen'}) == before
    assert manager.native_binding_snapshot(validate_mcp_ids=set()) == before
    with pytest.raises(Conflict, match='file bytes changed'):
        manager.native_binding_snapshot(validate_mcp_ids={'other'})
    catalog = manager.tool_catalog()
    servers = {row['id']: row for row in catalog['mcp_servers']}
    assert servers['other']['availability'] == 'INTEGRITY_ERROR'
    assert servers['chosen']['availability'] == 'AVAILABLE'
    item = selected(servers['chosen'])
    manager.validate_tool_selection([item], [item['tool_id']])
    assert catalog['capability_binding_sha256'] == before['sha256']
    assert catalog['effects_applied'] is False and catalog['model_calls'] == 0


def test_namespace_collision_only_affects_involved_directory_entries(tmp_path):
    manager = NativeCapabilities(Store(tmp_path / 'store.sqlite'), tmp_path)
    configured(manager, tmp_path, 'alpha', probe=True)
    configured(manager, tmp_path, 'alpha_tools')
    configured(manager, tmp_path, 'chosen', probe=True)
    servers = {row['id']: row for row in manager.tool_catalog()['mcp_servers']}
    assert servers['alpha']['availability'] == servers['alpha_tools']['availability'] == 'COLLISION'
    assert servers['chosen']['availability'] == 'AVAILABLE'
    item = selected(servers['chosen'])
    manager.validate_tool_selection([item], [item['tool_id']])
    narrower = tool_catalog(manager, server_ids={'alpha'})
    assert narrower['mcp_servers'][0]['availability'] == 'AVAILABLE'
    precise = selected(narrower['mcp_servers'][0])
    manager.validate_tool_selection([precise], [precise['tool_id']])
    changed = copy.deepcopy(precise)
    changed['input_schema_sha256'] = '0' * 64
    with pytest.raises(Conflict, match='exact discovered source'):
        manager.validate_tool_selection([changed], [changed['tool_id']])


@pytest.mark.parametrize('scope', [[], {'not-configured'}, True])
def test_metadata_validation_scope_is_explicit_and_known(tmp_path, scope):
    manager = NativeCapabilities(Store(tmp_path / 'store.sqlite'), tmp_path)
    with pytest.raises(InvalidInput, match='exact configured servers'):
        manager.native_binding_snapshot(validate_mcp_ids=scope)
    with pytest.raises(Conflict, match='unknown server'):
        tool_catalog(manager, server_ids=scope)
