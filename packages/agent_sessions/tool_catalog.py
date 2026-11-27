"""Bounded observations of an already owned original tool registry, never load."""
from datetime import datetime, timezone
import re

import httpx

from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest, now


def _permission(name, rules):
    matches = [rule for rule in rules if rule['permission'] in {'*', name}]
    if not matches:
        return 'UNKNOWN'
    last = matches[-1]
    if last['pattern'] == '*':
        return last['action']
    return 'SCOPED'


async def owner_tool_catalog(service, scope):
    pid, ref, generation = scope['project_id'], scope['session_ref'], scope['generation']
    manifest, _ = service._manifest(pid, ref['id'], ref)
    state = service._state(pid, ref['id'])
    if state['generation'] != generation:
        raise Conflict('Tool catalogue belongs to another owner generation')
    result = {'scope': scope, 'status': 'UNAVAILABLE', 'reason': 'OWNER_NOT_CONNECTED'}
    if not service._configuration_current(manifest):
        return {'owner': {**result, 'status': 'STALE', 'reason': 'CONFIGURATION_CHANGED'}, 'builtin_tools': []}
    candidate = service.active.get(ref['id'])
    if candidate and datetime.fromisoformat(candidate['expires_at']) <= datetime.now(timezone.utc):
        return {'owner': {**result, 'status': 'STALE', 'reason': 'OWNER_EXPIRED'}, 'builtin_tools': []}
    try:
        _, context = service._context(pid, ref['id'], ref, generation, verify_scope=False)
    except Conflict:
        return {'owner': result, 'builtin_tools': []}
    try:
        definitions = await service._http(context, 'GET', '/experimental/tool', params={
            'provider': context['profile'].provider_id, 'model': context['profile'].model_id}, timeout=10)
        session = await service._http(context, 'GET', '/session/' + context['session_id'], timeout=10)
        mcp = await service._http(context, 'GET', '/mcp', timeout=10)
        _, latest = service._context(pid, ref['id'], ref, generation, verify_scope=False)
        if latest is not context or session.get('id') != context['session_id']:
            raise Conflict('Original tool observation owner changed')
        if (not isinstance(definitions, list) or len(definitions) > 256 or not isinstance(mcp, dict)
                or len(mcp) > 128 or len(canonical([definitions, session.get('permission'), mcp]).encode()) > 524288):
            raise Conflict('Original tool observation exceeds its bounded directory')
        permissions = session.get('permission')
        if not isinstance(permissions, list) or len(permissions) > 256 or any(
                not isinstance(rule, dict) or not isinstance(rule.get('permission'), str) or
                not isinstance(rule.get('pattern'), str) or rule.get('action') not in {'allow', 'ask', 'deny'}
                for rule in permissions):
            raise Conflict('Original session permission observation is incomplete')
        rows, seen = [], set()
        for item in definitions:
            ident = item.get('id') if isinstance(item, dict) else None
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', ident) or ident in seen:
                raise Conflict('Original tool registry identity is ambiguous')
            seen.add(ident)
            action = 'edit' if ident in {'write', 'apply_patch', 'multiedit'} else ident
            description = item.get('description', '')
            if not isinstance(description, str):
                raise Conflict('Original tool description is malformed')
            effective = _permission(action, permissions)
            rows.append({'id': ident, 'description': service._clean(context, description[:512]),
                         'description_truncated': len(description) > 512, 'permission': effective,
                         'permission_action': action, 'agent_permissions': {
                             'build': effective, 'plan': 'deny' if action == 'edit' and
                             'WORKSPACE_WRITE' in manifest['request'].get('allowed_effects', []) else effective},
                         'input_schema_sha256': digest(item.get('parameters', {})),
                         'selectable_for_new_session': False, 'source': 'ORIGINAL_OWNER_REGISTRY'})
        config_refs = manifest['capability_configuration_refs']['mcp']
        observed_mcp = {ident: {'config_ref': config_refs[ident], 'status': value.get('status', 'UNKNOWN')}
                        for ident, value in mcp.items() if ident in config_refs and isinstance(value, dict)}
        return {'owner': {'scope': scope, 'status': 'CURRENT', 'observed_at': now(),
                          'mcp': observed_mcp, 'permission_rules': service._clean(context, permissions),
                          'permission_source': 'ORIGINAL_SESSION_RULES_NOT_A_NEW_GRANT'}, 'builtin_tools': rows}
    except (Conflict, httpx.HTTPError, ValueError, KeyError, TypeError):
        # No mutation, load, retry or invented historical registry on failure.
        return {'owner': {**result, 'reason': 'ORIGINAL_OBSERVATION_UNAVAILABLE'}, 'builtin_tools': []}
