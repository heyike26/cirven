"""Selected MCP bindings for newly admitted generic original sessions.

The UI's complete metadata binding is checked once at admission.  A new owner
then keeps only its selected MCP configuration references.  Skills retain the
existing complete-index semantics.  Old manifests and private admissions are
deliberately outside this versioned projection.
"""
from copy import deepcopy
import re

from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.native_tools import validate_selection
from packages.domain.store import Conflict
from packages.domain.tasks import digest

from .mcp_names import admitted_server_prefixes, exact_tool_owner, original_mcp_identifier


SCHEMA = 'eeas.agent-session-selected-mcp.v1'
_KEYS = {'schema', 'binding_sha256', 'selected_server_ids', 'selection_sha256'}


def _selection_sha(request):
    return digest({key: request.get(key) for key in ('mcp_tool_allowlist', 'mcp_tool_selection')})


def _server_ids(request, binding):
    names = request['mcp_tool_allowlist']
    selection = request.get('mcp_tool_selection')
    servers = binding['value']['native_config_fragment']['mcp']
    if selection is not None:
        selected_names = [item['tool_id'] for item in selection]
        if len(selected_names) != len(set(selected_names)) or set(selected_names) != set(names):
            raise Conflict('Selected tools differ from the explicit new conversation allowlist')
        ids = {item['server_id'] for item in selection}
        if not ids <= servers.keys():
            raise Conflict('A selected MCP configuration is disabled or unavailable')
    else:
        # A historical flattened name has no explicit source discriminator.
        # Do not guess a longest prefix when two configured servers can own it.
        prefixes = {ident: original_mcp_identifier(ident) + '_' for ident in servers}
        ids = {exact_tool_owner(name, prefixes) for name in names}
    admitted = admitted_server_prefixes(sorted(ids))
    for name in names:
        exact_tool_owner(name, admitted)
    return sorted(ids)


class _SelectedCapabilities:
    """Read-only index view reusing the authoritative snapshot implementation."""
    def __init__(self, service, server_ids, expected_refs, probe_refs=None):
        self.service = service
        self.server_ids = server_ids
        self.expected_refs = expected_refs
        self.probe_refs = probe_refs

    def _index(self):
        current = self.service._index()
        if not set(self.server_ids) <= current['mcp'].keys():
            raise Conflict('Selected MCP configuration is no longer current')
        projected = {**current, 'mcp': {ident: current['mcp'][ident] for ident in self.server_ids}}
        if digest(projected) != digest(self.expected_refs):
            raise Conflict('Selected MCP or existing Skill configuration changed')
        return deepcopy(projected)

    def _configuration(self, *args):
        return self.service._configuration(*args)

    def _path(self, *args):
        return self.service._path(*args)

    def _last_probe(self, ident, ref):
        if self.probe_refs is None:
            return self.service._last_probe(ident, ref)
        expected = self.probe_refs.get(ident)
        if (not isinstance(expected, dict) or set(expected) != {'id', 'sha256'}
                or not isinstance(expected.get('id'), str)
                or not re.fullmatch(r'[a-f0-9]{32}', expected['id'])
                or not isinstance(expected.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', expected['sha256'])):
            raise Conflict('Selected original MCP probe identity changed')
        receipt = self.service._read(self.service._path('mcp-probes', expected['id'] + '.json'))
        if digest(receipt) != expected['sha256'] or receipt.get('id') != expected['id']:
            raise Conflict('Selected original MCP probe bytes changed')
        return {**receipt, 'current_configuration': digest(receipt.get('config_ref')) == digest(ref)}

    def native_binding_snapshot(self, **kwargs):
        return NativeCapabilities.native_binding_snapshot(self, **kwargs)


def _snapshot(service, server_ids, refs):
    view = _SelectedCapabilities(service, server_ids, refs)
    return NativeCapabilities.native_binding_snapshot(view, validate_mcp_ids=set(server_ids))


def freeze_scope(service, request, global_binding):
    """Create a projection only after the caller verified the UI's global SHA."""
    if global_binding['sha256'] != request['capability_binding_sha256']:
        raise Conflict('Selected exact capability metadata changed')
    ids = _server_ids(request, global_binding)
    index = global_binding['value']['configuration_refs']
    refs = {**deepcopy(index), 'mcp': {ident: deepcopy(index['mcp'][ident]) for ident in ids}}
    binding = _snapshot(service, ids, refs)
    if set(binding['value']['native_config_fragment']['mcp']) != set(ids):
        raise Conflict('Selected MCP is disabled or its effective configuration changed')
    scope = {'schema': SCHEMA, 'binding_sha256': binding['sha256'], 'selected_server_ids': ids,
             'selection_sha256': _selection_sha(request)}
    return binding, scope


def current_scope(service, manifest):
    """Recheck only a previously frozen new owner; never create or migrate one."""
    scope = manifest.get('capability_scope')
    if ('native_review_admission' in manifest or 'native_message_admission' in manifest
            or not isinstance(scope, dict) or set(scope) != _KEYS or scope.get('schema') != SCHEMA
            or not isinstance(scope.get('binding_sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', scope['binding_sha256'])):
        raise Conflict('Selected capability projection is not an admitted generic owner')
    ids = scope.get('selected_server_ids')
    if (not isinstance(ids, list) or len(ids) > 64
            or not all(isinstance(ident, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident) for ident in ids)
            or ids != sorted(set(ids))):
        raise Conflict('Selected MCP projection identities changed')
    refs = manifest.get('capability_configuration_refs')
    if (not isinstance(refs, dict) or set(refs) != {'schema', 'skills', 'mcp'}
            or refs.get('schema') != 'eeas.native-capabilities.v1'
            or not isinstance(refs.get('skills'), dict) or not isinstance(refs.get('mcp'), dict)
            or set(refs['mcp']) != set(ids)):
        raise Conflict('Selected MCP projection references changed')
    request = manifest['request']
    if scope['selection_sha256'] != _selection_sha(request):
        raise Conflict('Selected MCP tools differ from their frozen admission')
    prefixes = admitted_server_prefixes(ids)
    used = {exact_tool_owner(name, prefixes) for name in request['mcp_tool_allowlist']}
    if used != set(ids):
        raise Conflict('Selected MCP projection differs from its exact tool allowlist')
    selected = request.get('mcp_tool_selection')
    if selected is not None and {item['server_id'] for item in selected} != set(ids):
        raise Conflict('Selected MCP projection differs from its structured source selection')
    binding = _snapshot(service, ids, refs)
    if (set(binding['value']['native_config_fragment']['mcp']) != set(ids)
            or binding['sha256'] != scope['binding_sha256']
            or digest(binding['value']) != scope['binding_sha256']):
        raise Conflict('Selected original capability bytes changed')
    # The same original selection validator runs on the owner's frozen index
    # view; a later unrelated MCP declaration is not part of this check.
    probes = {}
    for item in selected or []:
        ident, ref = item['server_id'], item['probe_ref']
        if ident in probes and digest(probes[ident]) != digest(ref):
            raise Conflict('Selected tools do not share their original server discovery')
        probes[ident] = ref
    validate_selection(_SelectedCapabilities(service, ids, refs, probe_refs=probes),
                       selected, request['mcp_tool_allowlist'])
    return binding
