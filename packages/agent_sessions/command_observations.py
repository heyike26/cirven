"""Read-only command facts matched to the original user operation.

An integer exit is a command observation, never a product acceptance decision.
No database, files, native API, process or model is touched by this projection.
"""
from copy import deepcopy

from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest

MAX_COMMANDS = 128
MAX_OPERATIONS = 128
MAX_OPERATION_BYTES = 8 * 1024 * 1024
SCOPE_KEYS = ('project_id', 'session_ref', 'generation', 'workspace_ref')


def required_parent_ids(entries):
    """Exact original user IDs needed by the bounded operation-index reader."""
    if not isinstance(entries, list) or len(entries) > MAX_COMMANDS:
        raise Conflict('Command observation count exceeds its projection bound')
    return sorted({row['parent_native_message_id'] for row in entries})


def project_command_observations(observed, entries, operations, *, reason_codes=(), trusted_work_roots=None):
    scope = {key: deepcopy(observed[key]) for key in SCOPE_KEYS}
    if (type(scope['generation']) is not int or scope['generation'] < 0 or
            not isinstance(entries, list) or len(entries) > MAX_COMMANDS):
        raise Conflict('Original command projection scope is invalid')
    reasons = set(reason_codes)
    parents = required_parent_ids(entries)
    index, count, size = {}, 0, 0
    for operation in operations:
        count += 1
        if count > MAX_OPERATIONS or not isinstance(operation, dict):
            raise Conflict('Original command operation coverage exceeds its bound')
        size += len(canonical(operation).encode())
        if size > MAX_OPERATION_BYTES:
            raise Conflict('Original command operation coverage exceeds its byte bound')
        if operation.get('native_message_id') in parents:
            index.setdefault(operation['native_message_id'], []).append(operation)
    commands, seen = [], {}
    for entry in entries:
        source = entry.get('source_scope')
        if (not isinstance(source, dict) or any(source.get(k) != scope[k] for k in ('project_id', 'session_ref', 'workspace_ref')) or
                type(entry.get('source_generation')) is not int or not 1 <= entry['source_generation'] <= scope['generation'] or
                source.get('generation') != entry['source_generation'] or
                entry.get('native_session_id') != observed.get('original_session_id')):
            raise Conflict('Original command observation belongs to another scope')
        candidates = index.get(entry.get('parent_native_message_id'), [])
        if len(candidates) != 1:
            reasons.add('COMMAND_OPERATION_NOT_UNIQUELY_ASSOCIATED')
            continue
        operation = candidates[0]
        request = operation.get('request')
        if (operation.get('kind') not in ('MESSAGE', 'GUIDANCE') or not isinstance(request, dict) or
                operation.get('session_ref') != source['session_ref'] or
                type(operation.get('generation')) is not int or type(request.get('generation')) is not int or
                operation.get('generation') != entry['source_generation'] or
                operation.get('original_session_id') != entry['native_session_id'] or
                request.get('session_ref') != source['session_ref'] or
                request.get('generation') != entry['source_generation'] or not isinstance(operation.get('id'), str)):
            reasons.add('COMMAND_OPERATION_SCOPE_UNAVAILABLE')
            continue
        if operation.get('work_retest') is not None and operation['id'] not in (trusted_work_roots or {}):
            reasons.add('RETEST_WORK_ROOT_NOT_VERIFIED')
            continue
        root = (trusted_work_roots or {}).get(operation['id'],
            operation['id'] if operation['kind']=='MESSAGE' else operation.get('original_active_operation_id'))
        if not isinstance(root, str) or not root:
            reasons.add('COMMAND_ROOT_OPERATION_UNAVAILABLE')
            continue
        key = (entry['source_generation'], entry['native_session_id'], entry['call_id'])
        if key in seen:
            if seen[key] != digest(entry):
                raise Conflict('Immutable original command identity collided')
            continue
        seen[key] = digest(entry)
        command = {**deepcopy(entry), 'operation_id': operation['id'], 'root_operation_id': root,
                   'native_message_id': entry['parent_native_message_id']}
        if operation.get('work_retest') is not None:
            command['work_retest_operation_id']=operation['work_retest']['intent_ref']['operation_id']
        commands.append(command)
    if not entries:
        reasons.add('NO_MATCHED_ORIGINAL_COMMAND_RECORDS')
    value = {'schema': 'eeas.original-command-observations.v1', 'scope': scope, 'commands': commands,
             'coverage': {'basis_scope': 'DECLARED_FILES_ONLY', 'all_dependencies_captured': False,
                          'reason_codes': sorted(reasons), 'max_commands': MAX_COMMANDS,
                          'maximum_messages_observed': 200, 'independent_runner': False,
                          'whole_task_acceptance': 'NOT_ASSESSED'},
             'read_only': True, 'model_calls': 0, 'automatic_replay': False}
    return {**value, 'snapshot_ref': {'id': digest(scope), 'revision': 1, 'sha256': digest(value)}}
