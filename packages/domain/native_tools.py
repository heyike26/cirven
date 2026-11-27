"""Read-only tool choices derived from immutable configuration/probe evidence.

These choices seed an explicit new conversation. They neither mutate an owner
nor grant execution; the existing exact allowlist still compiles to ``ask``.
"""
import copy
import re

from pydantic import Field, field_validator

from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import canonical, digest


class ToolConfigurationRef(RequestModel):
    id: Id
    revision: int = Field(ge=1, strict=True)
    sha256: Hash


class ToolProbeRef(RequestModel):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    sha256: Hash


class MCPToolSelection(RequestModel):
    server_id: Id
    config_ref: ToolConfigurationRef
    probe_ref: ToolProbeRef
    tool_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,200}$')
    name: str = Field(min_length=1, max_length=256)
    input_schema_sha256: Hash

    @field_validator('name')
    @classmethod
    def literal_name(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError('A tool name must be a literal single-line name')
        return value


class ToolSessionRef(ToolConfigurationRef):
    revision: int = Field(default=1, ge=1, le=1, strict=True)


class ToolOwnerScope(RequestModel):
    project_id: Id
    session_ref: ToolSessionRef
    generation: int = Field(ge=0, strict=True)


def mcp_content(service, ref):
    configuration = service._current_mcp(ref)
    intact = True
    if configuration['type'] == 'local':
        from .native_capabilities import _command_files
        try:
            _command_files(configuration)
        except (Conflict, InvalidInput, OSError, ValueError):
            intact = False
    return {'config_ref': copy.deepcopy(ref), 'configuration': copy.deepcopy(configuration),
            'current_configuration': True, 'integrity': 'CURRENT' if intact else 'INTEGRITY_ERROR'}


def tool_catalog(service, *, server_ids=None):
    from packages.agent_sessions.mcp_names import original_mcp_identifier
    from .native_capabilities import _command_files
    binding = service.native_binding_snapshot(validate_mcp_ids=set())
    refs = binding['value']['configuration_refs']
    if len(refs['mcp']) > 128:
        raise Conflict('Tool catalogue exceeds its bounded server directory')
    if server_ids is not None and (type(server_ids) is not set or
            not all(isinstance(ident, str) for ident in server_ids) or
            not server_ids <= refs['mcp'].keys()):
        raise Conflict('Selected MCP directory names an unknown server')
    selected = refs['mcp'] if server_ids is None else {ident: refs['mcp'][ident] for ident in server_ids}
    enabled = binding['value']['native_config_fragment']['mcp']
    prefixes = {ident: original_mcp_identifier(ident) + '_' for ident in selected if ident in enabled}
    conflicts = set()
    for ident, prefix in prefixes.items():
        for other, other_prefix in prefixes.items():
            if ident != other and (prefix.startswith(other_prefix) or other_prefix.startswith(prefix)):
                conflicts.update((ident, other))
    servers, total = [], 0
    for ident, ref in sorted(selected.items()):
        configuration = service._configuration('mcp', ref)['configuration']
        probe = service._last_probe(ident, ref)
        proof = None if probe is None else {key: value for key, value in probe.items() if key != 'current_configuration'}
        probe_ref = None if proof is None else {'id': proof['id'], 'sha256': digest(proof)}
        availability = 'NOT_PROBED'
        source_policy=binding['value'].get('experience_source_states',{}).get('mcp',{}).get(ident)
        control=binding['value'].get('experience_control_states',{}).get('mcp',{}).get(ident)
        source_blocked=(source_policy is not None and source_policy.get('availability')!='ACTIVE') or (control is not None and control['effective_state']=='PAUSED')
        if source_blocked:availability='DISABLED'
        elif not configuration['enabled']:
            availability = 'DISABLED'
        elif ident in conflicts:
            availability = 'COLLISION'
        elif probe is not None:
            availability = ('STALE' if not probe['current_configuration'] else
                            'AVAILABLE' if probe.get('status') == 'INITIALIZED_AND_LISTED' and
                            probe.get('initialized') is True and probe.get('list_tools_succeeded') is True else 'FAILED')
        if configuration['type'] == 'local' and not source_blocked:
            try:
                _command_files(configuration)
            except (Conflict, InvalidInput, OSError, ValueError):
                availability = 'INTEGRITY_ERROR'
        tools = []
        if availability == 'AVAILABLE':
            seen = set()
            for item in probe.get('tools', []):
                name = item.get('name')
                schema = item.get('input_schema_sha256')
                if (not isinstance(name, str) or not 1 <= len(name) <= 256 or any(ord(c) < 32 for c in name)
                        or not isinstance(schema, str) or not re.fullmatch(r'[a-f0-9]{64}', schema)):
                    raise Conflict('Stored tool discovery is incomplete')
                tool_id = original_mcp_identifier(ident) + '_' + original_mcp_identifier(name)
                if tool_id in seen:
                    availability = 'COLLISION'; tools = []; break
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', tool_id):
                    availability = 'TOO_LARGE'; tools = []; break
                seen.add(tool_id)
                tools.append({'id': tool_id, 'name': name, 'input_schema_sha256': schema, 'selectable': True})
            total += len(tools)
            if total > 1024:
                raise Conflict('Tool catalogue exceeds its bounded tool directory')
        servers.append({'id': ident, 'config_ref': copy.deepcopy(ref), 'enabled': configuration['enabled'],
                        'experience_source':source_policy,'capability_control':control,
                        'availability': availability, 'probe_ref': probe_ref, 'tools': tools,
                        'source': 'PRIVATE_MCP_PROBE_NOT_ORIGINAL_OWNER'})
    result = {'schema': 'eeas.native-tools.v1', 'capability_binding_sha256': binding['sha256'],
              'configuration_refs': copy.deepcopy(refs), 'mcp_servers': servers,
              'owner': {'scope': None, 'status': 'NOT_SELECTED'}, 'builtin_tools': [],
              'read_only': True, 'model_calls': 0, 'effects_applied': False}
    if len(canonical(result).encode('utf-8')) > 524288:
        raise Conflict('Tool catalogue exceeds its bounded response')
    return result


def validate_selection(service, selected, allowlist):
    """An optional structured picker narrows the existing explicit allowlist."""
    if selected is None:  # Legacy hand-entered exact allowlists remain supported.
        return
    items = [MCPToolSelection.model_validate(item).model_dump() for item in selected]
    names = [item['tool_id'] for item in items]
    if len(items) > 64 or len(names) != len(set(names)) or set(names) != set(allowlist):
        raise Conflict('Selected tools differ from the explicit new conversation allowlist')
    catalogue = tool_catalog(service, server_ids={item['server_id'] for item in items})
    servers = {server['id']: server for server in catalogue['mcp_servers']}
    for item in items:
        server = servers.get(item['server_id'])
        if (not server or server['availability'] != 'AVAILABLE' or server['config_ref'] != item['config_ref']
                or server['probe_ref'] != item['probe_ref']):
            raise Conflict('Selected MCP discovery changed; refresh before explicitly creating a conversation')
        if not any(tool['id'] == item['tool_id'] and tool['name'] == item['name'] and
                   tool['input_schema_sha256'] == item['input_schema_sha256'] for tool in server['tools']):
            raise Conflict('Selected tool differs from its exact discovered source')
