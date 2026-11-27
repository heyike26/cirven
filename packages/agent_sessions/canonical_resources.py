"""Original canonical reply receipts bind accounting, never execution grants.

All reads use the existing integrity checked journals. No index backfill,
allocation, model call, old-owner startup or permission adoption happens here.
"""
from copy import deepcopy

from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.domain.task_resource_budget import TaskResourceBudget

MAX_ANCESTORS = 32


def resource_value(budget):
    return {'resource_id':budget.id,'authority':deepcopy(budget.authority),'limits':deepcopy(budget.limits)}


def replacement_path(service, target, original, conversation_id):
    """Prove an explicitly configured replacement of this exact old owner.

    A conversation move, owner selection or matching title is insufficient.
    Execution-project changes retain their existing separate resource boundary.
    """
    if target['project_id'] != original['project_id']:
        raise Conflict('Cross execution-project work accounting requires an explicit resource transfer')
    current = deepcopy(target)
    seen = set()
    for _ in range(MAX_ANCESTORS):
        key = (current['project_id'], current['session_ref']['id'])
        if key in seen:
            raise Conflict('Original work replacement ancestry contains a cycle')
        seen.add(key)
        manifest, reference = service.sessions._manifest(*key)
        if reference != current['session_ref']:
            raise Conflict('Original work replacement manifest changed')
        if (current['project_id'] == original['project_id']
                and reference == original['session_ref']):
            if current['generation'] < original['generation']:
                raise Conflict('Original work replacement predates the selected message')
            return
        claim = manifest.get('canonical_execution_claim')
        if claim is None or manifest['request'].get('conversation_ref', {}).get('id') != conversation_id:
            raise Conflict('Current owner has no confirmed replacement of the selected original work')
        proof = service.runtime._claimed_configuration(manifest['project_id'],
            manifest['request']['conversation_ref'], claim,
            {**manifest['request'], 'operation_id': claim['operation_id']}, require_closed=True)
        row = service._read(service._path('connections', [claim['project_id'], claim['operation_id']]))
        published = row.get('source') or {}
        if (row.get('phase') != 'BOUND' or published.get('project_id') != current['project_id']
                or published.get('session_ref') != reference
                or type(published.get('generation')) is not int
                or not 1 <= published['generation'] <= current['generation']):
            raise Conflict('Original work replacement was not published to this owner')
        current = proof['authority']['original_source']
        if current is None:
            raise Conflict('An empty configured owner cannot adopt unrelated work accounting')
    raise Conflict('Original work replacement ancestry exceeds its proof bound')


def origin(service, manifest, operation):
    """Return the exact prior operation for an explicit canonical reply only."""
    request = operation.get('request', {})
    reference = request.get('conversation_ref')
    if reference is None:
        return None
    path = service._path('messages', [reference['id'], operation['id']])
    if not path.exists():
        return None  # Direct/legacy operations do not acquire a guessed parent.
    row = service._read(path)
    service.original_admission(manifest, operation)
    parent_ref = row['request'].get('parent_message_ref')
    lineage = row.get('work_resource_lineage')
    if lineage is None:
        # Existing accepted operations may already have their own consumed
        # ledger. Never reinterpret or silently merge that historical debit.
        return None
    if (not isinstance(lineage, dict) or set(lineage)!={'version','parent_message_ref','resource'}
            or lineage['parent_message_ref']!=parent_ref or lineage['version']!=1
            or type(lineage.get('version')) is not int or parent_ref is None):
        raise Conflict('Original work resource lineage differs from its canonical reply')
    if (not isinstance(parent_ref, dict) or set(parent_ref) != {'id', 'revision', 'sha256'}
            or parent_ref['revision'] != 1 or parent_ref['id'] == operation['id']):
        raise Conflict('Original work reply reference differs')
    parent = service._read(service._path('messages', [reference['id'], parent_ref['id']]))
    source = parent.get('source')
    if (parent.get('body_sha256') != parent_ref['sha256'] or source is None
            or parent.get('phase') not in {'DISPATCHING', 'OBSERVED', 'UNKNOWN'}
            or parent['request']['conversation_ref']['id'] != reference['id']
            or parent['request']['operation_id'] != parent_ref['id']):
        raise Conflict('Original work reply does not name its saved canonical parent')
    previous, saved_ref = service.sessions._manifest(source['project_id'], source['session_ref']['id'])
    prior = service.sessions.operation(source['project_id'], source['session_ref']['id'], parent_ref['id'])
    if (saved_ref != source['session_ref'] or prior.get('session_ref') != saved_ref
            or prior.get('kind') not in {'MESSAGE', 'GUIDANCE'}
            or prior.get('generation') != source['generation']
            or prior.get('request_sha256') != parent.get('dispatch_request_sha256')
            or digest(prior.get('request')) != prior.get('request_sha256')):
        raise Conflict('Original work reply parent operation differs')
    if prior.get('role') == 'LOCAL_STATUS':
        from .work_context import _status_ref
        if (_status_ref(prior) is None and not (
                isinstance(parent.get('work_resource_lineage'),dict)
                and parent['work_resource_lineage'].get('version')==1
                and parent['work_resource_lineage'].get('parent_message_ref')==parent['request'].get('parent_message_ref')
                and parent['request'].get('parent_message_ref') is not None
                and prior.get('status') == 'RETURNED' and prior.get('model_calls') == 0
                and prior.get('native_post_count') == 0 and prior.get('semantic_acceptance') is False)):
            raise Conflict('Original local work status has no verified root')
    elif prior.get('native_dispatch_acknowledged') is not True:
        raise Conflict('Original work reply parent has no acknowledged dispatch')
    target = row.get('source')
    if target is None:
        raise Conflict('Original work reply has no recorded destination')
    replacement_path(service, target, source, reference['id'])
    resource=lineage['resource']
    if not isinstance(resource,dict) or set(resource)!={'resource_id','authority','limits'}:
        raise Conflict('Original work resource anchor is malformed')
    budget=TaskResourceBudget(service.sessions.workspaces,resource['authority'],limits=resource['limits'])
    if resource_value(budget)!=resource:
        raise Conflict('Original work resource anchor differs from its ledger identity')
    authority=budget.authority
    if set(authority)!={'project_id','kind','session_ref','root_operation_id','request_sha256'} or authority['kind']!='ORIGINAL_WORK':
        raise Conflict('Canonical work resource anchor is not an original public work')
    root_manifest,root_ref=service.sessions._manifest(authority['project_id'],authority['session_ref']['id'])
    root=service.sessions.operation(authority['project_id'],root_manifest['id'],authority['root_operation_id'])
    if (root_ref!=authority['session_ref'] or root.get('session_ref')!=root_ref
            or root.get('kind')!='MESSAGE' or root.get('request_sha256')!=authority['request_sha256']
            or digest(root.get('request'))!=authority['request_sha256']
            or authority['project_id']!=target['project_id']):
        raise Conflict('Original work resource anchor no longer names its original input')
    parent_lineage=parent.get('work_resource_lineage')
    if parent_lineage is not None:
        if (not isinstance(parent_lineage,dict) or set(parent_lineage)!={'version','parent_message_ref','resource'}
                or type(parent_lineage['version']) is not int or parent_lineage['version']!=1
                or parent_lineage['parent_message_ref']!=parent['request'].get('parent_message_ref')
                or parent_lineage['resource']!=resource):
            raise Conflict('Canonical reply cannot replace its acknowledged original resource anchor')
    else:
        from .task_resources import for_operation
        if resource_value(for_operation(service.sessions,previous,prior))!=resource:
            raise Conflict('Canonical reply resource differs from its legacy parent ledger')
    return previous, prior, budget


def parent_snapshot(runtime, manifest, operation):
    prior = runtime.resource_origin(manifest, operation)
    if prior is None:
        return None
    previous, record, _budget = prior
    return {'project_id': previous['project_id'],
        'session_ref': record['session_ref'], 'operation_id': record['id'],
        'request_sha256': record['request_sha256'],
        'input_excerpt': record['request']['raw_text'][:480],
        'input_sha256': digest(record['request']['raw_text']),
        'read_only': True, 'permission_granted': False, 'automatic_replay': False}


def saved_status(runtime, manifest, operation):
    """A model-free progress reply keeps the original referenced work visible."""
    snapshot = parent_snapshot(runtime, manifest, operation)
    if snapshot is None:
        return None
    record = runtime.sessions.operation(snapshot['project_id'],
        snapshot['session_ref']['id'], snapshot['operation_id'])
    local = record.get('local_status', {}) if record.get('role') == 'LOCAL_STATUS' else {}
    status = local.get('original_work', local.get('canonical_work', {})).get('status', record['status'])
    return {**snapshot, 'status': status, 'model_calls': 0, 'native_posts': 0,
        'coverage': 'LAST_SAVED_REFERENCED_OPERATION_NOT_CURRENT_OR_SEMANTIC_ACCEPTANCE'}
