"""Exact accepted-item links for explicitly versioned planning proposals."""
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, model_validator

from packages.domain.local_workspaces import canonical, digest
from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.store import Conflict, InvalidInput

CONTRACT = 'ACCEPTED_CLAIMS_V1'
SCHEMA = 'cirven.accepted-claim-coverage.v1'
POLICY = 'ALL_ACCEPTED_7_V1'
MAX_BYTES = 2 * 1024 * 1024


class TaskTarget(RequestModel):
    kind: Literal['TASK']
    node_id: Id
    node_sha256: Hash


class SharedTarget(RequestModel):
    kind: Literal['SHARED_CONSTRAINT']
    constraint_index: int = Field(ge=0, le=99, strict=True)
    value_sha256: Hash
    scope: Literal['ALL_TASKS']


Target = Annotated[TaskTarget | SharedTarget, Field(discriminator='kind')]


class ClaimLink(RequestModel):
    claim_id: Id
    claim_sha256: Hash
    targets: list[Target] = Field(min_length=1, max_length=16)

    @model_validator(mode='after')
    def unique(self):
        keys = {(t.kind, t.node_id if t.kind == 'TASK' else t.constraint_index) for t in self.targets}
        if len(keys) != len(self.targets):
            raise ValueError('Duplicate accepted-item target')
        return self


class UserExclusion(RequestModel):
    claim_id: Id
    claim_sha256: Hash
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode='after')
    def reason_required(self):
        if not self.reason.strip():
            raise ValueError('An exclusion needs an explicit user reason')
        return self


class CoverageEdit(RequestModel):
    expected_basis_sha256: Hash
    claim_links: list[ClaimLink] = Field(max_length=2000)
    user_exclusions: list[UserExclusion] = Field(max_length=2000)

    @model_validator(mode='after')
    def bounded(self):
        links = [r.claim_id for r in self.claim_links]
        excluded = [r.claim_id for r in self.user_exclusions]
        if len(set(links)) != len(links) or len(set(excluded)) != len(excluded) or set(links) & set(excluded):
            raise ValueError('Each accepted item is linked or explicitly excluded once')
        if sum(len(r.targets) for r in self.claim_links) > 8000 or len(self.model_dump_json().encode()) > MAX_BYTES:
            raise ValueError('Accepted-item coverage exceeds its budget; split the plan')
        return self


class ModelTaskTarget(RequestModel):
    kind: Literal['TASK']
    node_id: Id


class ModelSharedTarget(RequestModel):
    kind: Literal['SHARED_CONSTRAINT']
    constraint_index: int = Field(ge=0, le=99, strict=True)
    scope: Literal['ALL_TASKS']


class ModelClaimLink(RequestModel):
    claim_id: Id
    targets: list[Annotated[ModelTaskTarget | ModelSharedTarget, Field(discriminator='kind')]] = Field(min_length=1, max_length=16)


def bounded(value):
    if len(canonical(value).encode()) > MAX_BYTES:
        raise InvalidInput('Accepted-item coverage exceeds 2 MiB; split the plan')
    return value


def inventory(candidate):
    return {'tasks': [{'node_id': n['id'], 'title': n['title'], 'node_sha256': digest(n)}
                      for n in candidate['nodes'] if n['kind'] == 'TASK'],
            'constraints': [{'constraint_index': i, 'text': s, 'value_sha256': digest(s)}
                            for i, s in enumerate(candidate['constraints'])]}


def create(pid, discovery, candidate, model_links):
    accepted = set(discovery['payload']['accepted_claim_ids'])
    claims = [deepcopy(c) for c in discovery['payload']['candidate']['claims'] if c['id'] in accepted]
    if not claims or len(claims) != len(accepted) or len(claims) > 2000:
        raise InvalidInput('Coverage needs exact accepted items from this discovery')
    basis = {'project_id': pid, 'discovery_ref': {k: discovery[k] for k in ('id', 'revision', 'sha256')},
             'accepted_claims_sha256': digest(claims), 'policy_version': POLICY}
    snapshot = {'schema': SCHEMA, 'basis': basis, 'basis_sha256': digest(basis),
                'accepted_claims': [{'claim': c, 'sha256': digest(c)} for c in claims],
                'claim_links': [], 'user_exclusions': []}
    nodes = {n['id']: n for n in candidate['nodes']}
    for link in model_links:
        targets = []
        for target in link['targets']:
            if target['kind'] == 'TASK':
                node = nodes.get(target['node_id'])
                if not node or node['kind'] != 'TASK':
                    raise InvalidInput('Model coverage names no actual task')
                targets.append({**target, 'node_sha256': digest(node)})
            else:
                index = target['constraint_index']
                if index >= len(candidate['constraints']):
                    raise InvalidInput('Model coverage names no actual shared constraint')
                targets.append({**target, 'value_sha256': digest(candidate['constraints'][index])})
        source = next((r for r in snapshot['accepted_claims'] if r['claim']['id'] == link['claim_id']), None)
        if source is None:
            raise InvalidInput('Model coverage names an unaccepted item')
        snapshot['claim_links'].append({'claim_id': link['claim_id'], 'claim_sha256': source['sha256'], 'targets': targets})
    request = CoverageEdit.model_validate({'expected_basis_sha256': snapshot['basis_sha256'],
        'claim_links': snapshot['claim_links'], 'user_exclusions': []})
    return edit(snapshot, request, candidate)


def edit(snapshot, request, candidate):
    if request.expected_basis_sha256 != snapshot['basis_sha256']:
        raise Conflict('Accepted-item basis changed')
    claims = {r['claim']['id']: r['sha256'] for r in snapshot['accepted_claims']}
    old = {(row['claim_id'], canonical(t)) for row in snapshot['claim_links'] for t in row['targets']}
    nodes = {n['id']: n for n in candidate['nodes']}
    value = request.model_dump()
    for row in [*value['claim_links'], *value['user_exclusions']]:
        if claims.get(row['claim_id']) != row['claim_sha256']:
            raise InvalidInput('Unknown, foreign or changed accepted item')
    for row in value['claim_links']:
        for target in row['targets']:
            if target['kind'] == 'TASK':
                node = nodes.get(target['node_id'])
                current = node and node['kind'] == 'TASK' and digest(node) == target['node_sha256']
            else:
                index = target['constraint_index']
                current = index < len(candidate['constraints']) and digest(candidate['constraints'][index]) == target['value_sha256']
            if not current and (row['claim_id'], canonical(target)) not in old:
                raise InvalidInput('New coverage target must match the actual task or constraint')
    return bounded({**deepcopy(snapshot), 'claim_links': value['claim_links'], 'user_exclusions': value['user_exclusions']})


def project(snapshot, candidate, *, source_current):
    nodes = {n['id']: n for n in candidate['nodes']}
    stale, valid = [], set()
    for row in snapshot['claim_links']:
        all_valid = True
        for index, target in enumerate(row['targets']):
            if target['kind'] == 'TASK':
                node = nodes.get(target['node_id'])
                reason = 'NODE_REMOVED' if node is None or node['kind'] != 'TASK' else 'NODE_CHANGED' if digest(node) != target['node_sha256'] else None
            else:
                i = target['constraint_index']
                reason = 'CONSTRAINT_CHANGED' if i >= len(candidate['constraints']) or digest(candidate['constraints'][i]) != target['value_sha256'] else None
            if reason:
                all_valid = False
                stale.append({'claim_id': row['claim_id'], 'target_index': index, 'reason': reason})
        if all_valid:
            valid.add(row['claim_id'])
    excluded = {r['claim_id'] for r in snapshot['user_exclusions']}
    missing = [r['claim']['id'] for r in snapshot['accepted_claims'] if r['claim']['id'] not in valid | excluded]
    status = 'STALE' if not source_current else 'INCOMPLETE' if missing or stale else 'STRUCTURALLY_COMPLETE'
    return {**deepcopy(snapshot), 'snapshot_sha256': digest(snapshot), 'status': status,
            'missing_claim_ids': missing, 'stale_targets': stale, 'target_inventory': inventory(candidate),
            'semantic_verification': 'HUMAN_REVIEW_REQUIRED'}


def require_clear(view):
    coverage = view.get('coverage')
    if coverage is not None and coverage['status'] != 'STRUCTURALLY_COMPLETE':
        raise Conflict('Accepted items need current task links, shared constraints or explicit user exclusions')
    if coverage is not None and view['payload']['status'] == 'CONFIRMED':
        confirmation = view['payload']['confirmation']
        if (confirmation.get('coverage_sha256') != coverage['snapshot_sha256'] or
                confirmation.get('confirmed_exclusion_ids') != [r['claim_id'] for r in coverage['user_exclusions']]):
            raise Conflict('This coverage and its exclusions need explicit current confirmation')


def prompt_context(snapshot, sources):
    """Avoid a new accepted-text copy only with exact full-source proof.

    Receipt storage and its historical aggregate/chunk representation stay intact.
    This is input composition, never an authority or approximate text filter.
    """
    import json
    import hashlib
    claims=[r['claim'] for r in snapshot['accepted_claims']]
    ref=snapshot['basis']['discovery_ref']
    expected_source='discovery-confirmed-baseline:'+ref['id']+':'+str(ref['revision'])+':'+ref['sha256']
    prefix='# 明确接受的需求与决议\n\n```json\n';suffix='\n```\n'
    for document in sources['documents']:
        if document['source']!=expected_source:continue
        rows=[r for r in sources['coverage'] if r['document_id']==document['document_id']]
        if len(rows)!=1 or rows[0]['text_coverage']!='FULL_PARSED_TEXT' or rows[0]['omitted_chunk_indexes']:continue
        text=document['text_or_selected_chunks'];raw_sha=hashlib.sha256(text.encode('utf-8')).hexdigest()
        if document['raw_sha256']!=raw_sha or document['parsed_sha256']!=raw_sha:continue
        if not text.startswith(prefix) or not text.endswith(suffix):continue
        try:baseline=json.loads(text[len(prefix):-len(suffix)])
        except (ValueError,TypeError):continue
        if (not isinstance(baseline,dict) or baseline.get('discovery_ref')!=ref or
                baseline.get('kind')!='USER_CONFIRMED_REQUIREMENTS_NOT_EXECUTION_AUTHORIZATION' or
                baseline.get('accepted_requirements')!=claims):continue
        return {'basis':snapshot['basis'],'accepted_items':[
            {'claim_id':r['claim']['id'],'category':r['claim']['category'],'claim_sha256':r['sha256']} for r in snapshot['accepted_claims']],
            'accepted_text_source':{k:document[k] for k in ('document_id','revision','raw_sha256','parsed_sha256')},
            'source_receipt_sha256':sources['sha256']}
    return {'basis':snapshot['basis'],'accepted_items':deepcopy(snapshot['accepted_claims'])}
