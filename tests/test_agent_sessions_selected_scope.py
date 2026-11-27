"""Real capability records/projection checks; no CLI, MCP process or model.

Probe records below are explicit metadata fixtures, not observations of a live
server.  The separate Source63 original-CLI suite qualifies actual startup.
"""
from copy import deepcopy
import hashlib

import pytest

from packages.agent_sessions import AgentSessions
from packages.agent_sessions.capability_scope import current_scope, freeze_scope
from packages.agent_sessions.models import OpenSession
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Conflict, InvalidInput, Store
from packages.domain.tasks import digest


@pytest.fixture
def capabilities(tmp_path):
    store = Store(tmp_path / 'records.sqlite')
    manager = NativeCapabilities(store, tmp_path)
    return manager


def configured(manager, ident, *, tool_name='inspect'):
    # An original inert text file suffices for hash/configuration validation;
    # no test executes it or presents it as a qualified executable.
    command = manager.data_dir / (ident + '-never-executed.txt')
    command.write_text('Source63 original inert command fixture: ' + ident)
    result = manager.configure_mcp({'operation_id': 'configure-' + ident, 'id': ident,
        'configuration': {'type': 'local', 'enabled': True, 'timeout': 1000,
            'command': [str(command)],
            'files_sha256': {str(command): hashlib.sha256(command.read_bytes()).hexdigest()}}})
    ref = result['config_ref']
    receipt = {'id': hashlib.md5(ident.encode()).hexdigest(), 'config_ref': ref,
        'status': 'INITIALIZED_AND_LISTED', 'initialized': True, 'list_tools_succeeded': True,
        'tools': [{'name': tool_name, 'input_schema_sha256': digest({'type': 'object'})}]}
    manager._record_probe(receipt)
    item = {'server_id': ident, 'config_ref': ref,
        'probe_ref': {'id': receipt['id'], 'sha256': digest(receipt)},
        'tool_id': ident + '_' + tool_name, 'name': tool_name,
        'input_schema_sha256': receipt['tools'][0]['input_schema_sha256']}
    return item, command


def request(manager, items=None, *, names=None, effects=None):
    return OpenSession.model_validate({'operation_id': 'new-explicit-owner',
        'provider_profile_ref': {'id': 'not-resolved-by-this-metadata-test', 'revision': 1, 'sha256': 'a' * 64},
        'capability_binding_sha256': manager.native_binding_snapshot(validate_mcp_ids=set())['sha256'],
        'mcp_tool_allowlist': names if names is not None else [item['tool_id'] for item in items or []],
        'mcp_tool_selection': items,
        'allowed_effects': ['PROCESS_START'] if effects is None else effects}).model_dump(exclude={'operation_id'})


def manifest(manager, body):
    binding, scope = freeze_scope(manager, body, manager.native_binding_snapshot(validate_mcp_ids=set()))
    return {'request': body, 'capability_scope': scope,
            'capability_configuration_refs': deepcopy(binding['value']['configuration_refs'])}, binding


def records(manager):
    root = manager.data_dir / 'native-capabilities'
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_structured_projection_ignores_unselected_collision_broken_command_and_later_revision(capabilities):
    manager = capabilities
    selected, _ = configured(manager, 'chosen')
    unused, bad = configured(manager, 'chosen_other')
    body = request(manager, [selected])
    bad.unlink()
    with pytest.raises((Conflict, InvalidInput)):
        manager.native_binding_snapshot()  # The legacy strict mode is preserved.
    before = records(manager)
    value, binding = manifest(manager, body)
    service = AgentSessions(manager.store, manager.data_dir, provider_registry=None,
                            workspaces=None, capabilities=manager)
    service._admit_open(OpenSession.model_validate({**body, 'operation_id': 'admit'}), binding)
    assert set(binding['value']['native_config_fragment']['mcp']) == {'chosen'}
    assert set(value['capability_configuration_refs']['mcp']) == {'chosen'}
    assert current_scope(manager, value)['sha256'] == binding['sha256']
    assert records(manager) == before
    assert service.active == {}
    manager.set_enabled('mcp', unused['server_id'], {'operation_id': 'unselected-revision',
        'expected_revision': unused['config_ref']['revision'], 'enabled': False})
    restarted = NativeCapabilities(manager.store, manager.data_dir)
    assert current_scope(restarted, value)['sha256'] == binding['sha256']
    assert restarted.native_binding_snapshot(validate_mcp_ids=set())['sha256'] != body['capability_binding_sha256']


def test_empty_selection_needs_no_process_grant_or_mcp_files(capabilities):
    manager = capabilities
    _, bad = configured(manager, 'unused')
    bad.unlink()
    for items in (None, []):
        body = request(manager, items, effects=[])
        value, binding = manifest(manager, body)
        service = AgentSessions(manager.store, manager.data_dir, provider_registry=None,
                                workspaces=None, capabilities=manager)
        service._admit_open(OpenSession.model_validate({**body, 'operation_id': 'empty'}), binding)
        assert binding['value']['native_config_fragment']['mcp'] == {}
        assert current_scope(manager, value)['value']['configuration_refs']['mcp'] == {}
        assert service.active == {}


def test_global_metadata_is_checked_before_projection(capabilities):
    manager = capabilities
    chosen, _ = configured(manager, 'chosen')
    body = request(manager, [chosen])
    configured(manager, 'later-unselected')
    with pytest.raises(Conflict, match='metadata changed'):
        freeze_scope(manager, body, manager.native_binding_snapshot(validate_mcp_ids=set()))


def test_legacy_unique_allowlist_is_supported_and_ambiguous_source_is_not_guessed(capabilities):
    manager = capabilities
    chosen, _ = configured(manager, 'chosen')
    body = request(manager, names=[chosen['tool_id']])
    value, binding = manifest(manager, body)
    assert current_scope(manager, value)['sha256'] == binding['sha256']
    configured(manager, 'chosen_other')
    ambiguous = request(manager, names=['chosen_other_inspect'])
    with pytest.raises(Conflict, match='SOURCE_NOT_UNAMBIGUOUS'):
        manifest(manager, ambiguous)
    # An unrelated later prefix cannot retroactively enlarge a frozen owner.
    assert current_scope(manager, value)['sha256'] == binding['sha256']


@pytest.mark.parametrize('fault', ['command', 'configuration', 'probe', 'missing_probe'])
def test_selected_bytes_configuration_and_discovery_still_fail_closed(capabilities, fault):
    manager = capabilities
    chosen, command = configured(manager, 'chosen')
    value, _ = manifest(manager, request(manager, [chosen]))
    if fault == 'command':
        command.write_text('Changed after explicit selection')
    elif fault == 'configuration':
        manager.set_enabled('mcp', 'chosen', {'operation_id': 'selected-revision',
            'expected_revision': 1, 'enabled': False})
    elif fault == 'probe':
        path = manager._path('mcp-probes', chosen['probe_ref']['id'] + '.json')
        receipt = manager._read(path)
        receipt['tools'][0]['input_schema_sha256'] = 'f' * 64
        manager._write(path, receipt, replace=True)
    else:
        manager._path('mcp-probes', chosen['probe_ref']['id'] + '.json').unlink()
    with pytest.raises((Conflict, InvalidInput)):
        current_scope(NativeCapabilities(manager.store, manager.data_dir), value)


def test_new_probe_same_configuration_does_not_replace_frozen_owner_discovery(capabilities):
    manager = capabilities
    chosen, _ = configured(manager, 'chosen')
    body = request(manager, [chosen])
    value, binding = manifest(manager, body)
    receipt = manager._last_probe('chosen', chosen['config_ref'])
    receipt.pop('current_configuration')
    receipt['id'] = 'e' * 32
    manager._record_probe(receipt)
    assert current_scope(NativeCapabilities(manager.store, manager.data_dir), value)['sha256'] == binding['sha256']
    # A new owner must still use the latest exact discovery selected by its UI.
    service = AgentSessions(manager.store, manager.data_dir, provider_registry=None,
                            workspaces=None, capabilities=manager)
    with pytest.raises(Conflict, match='discovery changed'):
        service._admit_open(OpenSession.model_validate({**body, 'operation_id': 'new-owner'}), binding)


def test_restart_cannot_transplant_immutable_probe_from_another_configuration(capabilities):
    manager = capabilities
    chosen, _ = configured(manager, 'chosen')
    other, _ = configured(manager, 'other')
    value, _ = manifest(manager, request(manager, [chosen]))
    value['request']['mcp_tool_selection'][0]['probe_ref'] = other['probe_ref']
    value['capability_scope']['selection_sha256'] = digest({key: value['request'][key]
        for key in ('mcp_tool_allowlist', 'mcp_tool_selection')})
    with pytest.raises(Conflict, match='discovery changed'):
        current_scope(NativeCapabilities(manager.store, manager.data_dir), value)


def test_skill_semantics_and_private_admission_are_not_widened(capabilities):
    manager = capabilities
    manager.import_skill({'operation_id': 'skill', 'id': 'original-skill',
        'content': '---\nname: original-skill\ndescription: Source63 original\n---\nRead only.\n', 'enabled': True})
    value, binding = manifest(manager, request(manager, effects=[]))
    assert set(binding['value']['configuration_refs']['skills']) == {'original-skill'}
    for kind in ('native_review_admission', 'native_message_admission'):
        with pytest.raises(Conflict, match='generic owner'):
            current_scope(manager, {**value, kind: {'id': 'never-adopted'}})
    manager.set_enabled('skills', 'original-skill', {'operation_id': 'disable-skill',
        'expected_revision': 1, 'enabled': False})
    with pytest.raises(Conflict, match='Skill configuration changed'):
        current_scope(manager, value)


@pytest.mark.parametrize('fault', ['extra', 'bool_revision', 'duplicate', 'extra_tool', 'hash'])
def test_projection_metadata_tampering_is_rejected_without_records_written(capabilities, fault):
    manager = capabilities
    chosen, _ = configured(manager, 'chosen')
    value, _ = manifest(manager, request(manager, [chosen]))
    before = records(manager)
    if fault == 'extra': value['capability_scope']['unknown'] = True
    elif fault == 'bool_revision': value['capability_configuration_refs']['mcp']['chosen']['revision'] = True
    elif fault == 'duplicate': value['capability_scope']['selected_server_ids'].append('chosen')
    elif fault == 'extra_tool': value['request']['mcp_tool_allowlist'].append('chosen_unselected')
    else: value['capability_scope']['binding_sha256'] = '0' * 64
    with pytest.raises(Conflict):
        current_scope(manager, value)
    assert records(manager) == before
