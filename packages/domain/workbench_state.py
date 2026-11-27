"""Bounded presentation drafts in the existing SQLite Store, never business state.

Exact baseline keys isolate versions and scopes. A single transaction commits a
snapshot, CAS head and immutable operation receipt. No worker or model is used.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain.local_workspaces import FileSource, relative
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.template_profiles import ProfileImport, WritingRule
from packages.domain.planning_schema import ManualCaptureInput, ManualCaptureRef, ManualDecision, ManualReview, PlanningCandidate
from packages.domain.workbench_feedback import Selection, TextSelection

MAX_BYTES = 2 * 1024 * 1024
MAX_STRING_BYTES = 1024 * 1024
MAX_DEPTH = 16
MAX_NODES = 50000
MAX_ITEMS = 4096
MAX_DRAFT_PAGE_BYTES = 4 * 1024 * 1024
POLLUTION = {'__proto__', 'prototype', 'constructor'}
KINDS = Literal['workspace', 'local-file', 'taskbook', 'harness', 'proposal',
                'discovery', 'report', 'competition', 'experience']
FIELDS = {
    'workspace': {},
    'local-file': {'text': str, 'sourceRef': 'source'},
    'taskbook': {'sections': list},
    'proposal': {'candidate': dict, 'answers': 'answers', 'resolutions': 'decisions',
        'changeReason': str, 'templatePath': str, 'templateRef': 'source', 'templateName': str,
        'harnessDirectory': str, 'destinationId': str, 'manualGate': 'manual-gate',
        'manualCapture': 'manual-capture', 'manualReview': 'manual-review', 'manualSave': 'manual-save',
        'requirementCoverage': 'claim-coverage', 'coverageOperation': 'coverage-operation'},
    'discovery': {'candidate': dict, 'answers': 'answers', 'decisions': 'decisions',
        'accepted': 'ids', 'graphsAccepted': 'ids', 'reason': str, 'selectedClaim': str,
        'experienceId': str, 'experienceRef': 'writing-selection', 'templatePath': str, 'templateRef': 'source', 'templateName': str,
        'reportGraphs': 'ids', 'destinationId': str, 'stageDirectory': str, 'saveToDirectory': bool,
        'planWaitSeconds': 'plan-wait'},
    'harness': {'reason': str, 'comment': str},
    'report': {'comment': str},
    'competition': {'url': str, 'title': str, 'notes': str, 'comment': str},
    'experience': {'name': str, 'rules': list, 'comment': str, 'experienceForm': 'experience-form',
        'importOperation': 'writing-import', 'reviewDraft': 'writing-review'},
}
for _renderer in ('discovery', 'proposal', 'report'):
    FIELDS[_renderer].update({'graphFeedback': 'graph-feedback', 'textFeedback': 'text-feedback'})
FIELDS['local-file']['textFeedback'] = 'text-feedback'
GRAPH_VIEW = {'zoom', 'pan', 'positions', 'selected', 'mode', 'search', 'viewport'}
VIEW = GRAPH_VIEW | {'scroll', 'tab', 'expanded', 'hidden', 'editing', 'active_key', 'panes', 'graphs'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _text(value, maximum=MAX_STRING_BYTES, *, nonempty=False):
    if type(value) is not str or '\x00' in value or (nonempty and not value.strip()):
        raise ValueError('Expected bounded Unicode text without NUL')
    try:
        length = len(value.encode('utf-8'))
    except UnicodeError:
        raise ValueError('Invalid Unicode surrogate is not supported') from None
    if length > maximum:
        raise ValueError('Text exceeds the presentation limit; no draft was truncated')


def _bounded(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop(); count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise ValueError('Presentation nesting or item budget exceeded; no draft was removed')
        if type(item) is dict:
            if len(item) > MAX_ITEMS: raise ValueError('Presentation object has too many items')
            for key, child in item.items():
                _text(key, 1024, nonempty=True)
                if key in POLLUTION: raise ValueError('Prototype pollution property is not presentation data')
                stack.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > MAX_ITEMS: raise ValueError('Presentation array has too many items')
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            _text(item)
        elif type(item) in (int, float):
            if abs(item) > 9007199254740991 or not math.isfinite(item):
                raise ValueError('Presentation numbers must be finite and safe for the client')
        elif item is not None and type(item) is not bool:
            raise ValueError('Only JSON presentation data is supported')
    if len(canonical(value).encode('utf-8')) > MAX_BYTES:
        raise ValueError('Presentation exceeds 2 MiB; no draft was truncated or removed')


class WorkbenchKey(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    project_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    workspace_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    binding_revision: int | None = Field(default=None, ge=1, le=2147483647)
    binding_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    kind: KINDS
    object_id: str = Field(min_length=1, max_length=1024)
    revision: int = Field(ge=1, le=2147483647)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def exact_scope(self):
        values = (self.workspace_id, self.binding_revision, self.binding_sha256)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError('Workspace binding ID, revision and SHA must be supplied together')
        _text(self.object_id, 4096, nonempty=True)
        if self.session_id is not None: _text(self.session_id, 800, nonempty=True)
        if any(ord(char) < 32 or ord(char) == 127 for value in (self.object_id, self.session_id or '') for char in value):
            raise ValueError('Object and session identities must not contain control characters')
        if self.kind == 'workspace' and (not self.workspace_id or self.object_id != self.workspace_id or
                self.revision != self.binding_revision or self.sha256 != self.binding_sha256):
            raise ValueError('Workspace navigation needs its actual complete binding as the object basis')
        return self

    def data(self):
        return self.model_dump(exclude_none=True)


def _ids(value):
    if type(value) is not list or len(value) > 2000:
        raise ValueError('Expected at most 2000 presentation selections')
    for item in value: _text(item, 1024, nonempty=True)


def _fields(key, value):
    allowed = FIELDS[key.kind]
    if type(value) is not dict or set(value) - set(allowed):
        raise ValueError('Fields outside this renderer presentation whitelist are not allowed')
    for name, item in value.items():
        kind = allowed[name]
        if kind == 'source':
            if item is None: continue
            source = FileSource.model_validate(item)
            relative(source.path)
            if source.project_id != key.project_id:
                raise ValueError('Attached SourceRef must stay in its original project')
            # Validate complete data, preserve the actual supplied reference; do
            # not look up/read a file, rebind it, or confer mutation authority.
        elif kind in {'writing-selection', 'writing-import', 'writing-review', 'experience-form'}:
            _writing_draft(key, kind, item)
        elif kind in {'manual-gate', 'manual-capture', 'manual-review', 'manual-save'}:
            _manual_draft(key, kind, item)
        elif kind == 'claim-coverage':
            _claim_coverage(item)
        elif kind == 'coverage-operation':
            _coverage_operation(key, item)
        elif kind == 'plan-wait':
            if type(item) is not int or item not in {120, 300, 600}:
                raise ValueError('Planning wait preference must be 120, 300 or 600 integer seconds')
        elif kind in {'graph-feedback', 'text-feedback'}:
            _feedback_drafts(item, graph=kind == 'graph-feedback')
        elif kind == 'ids': _ids(item)
        elif kind == 'answers':
            if type(item) is not dict or any(type(v) is not str for v in item.values()):
                raise ValueError('Answer drafts must map question IDs to text')
        elif kind == 'decisions':
            if type(item) is not dict: raise ValueError('Decision selections must be a presentation map')
            for decision in item.values():
                if type(decision) is not dict or set(decision) - {'answerId', 'assumption', 'reason'} or any(
                        type(v) is not str for v in decision.values()):
                    raise ValueError('Decision drafts contain only answer, assumption and reason text')
        elif type(item) is not kind:
            raise ValueError('Renderer presentation field has the wrong JSON type')


    if key.kind == 'proposal':
        captured, reviewing, saving = value.get('manualCapture'), value.get('manualReview'), value.get('manualSave')
        if captured and captured['state'] == 'COMPLETED' and reviewing and captured['capture_ref'] != reviewing['change_ref']:
            raise ValueError('Manual choices belong to another capture')
        if saving and saving['state'] in ('PREPARED', 'UNKNOWN') and reviewing and saving['manual_review']['change_ref'] != reviewing['change_ref']:
            raise ValueError('Original manual save must retain the same capture')

def _claim_coverage(value):
    if value is None:return
    from copy import deepcopy
    from packages.domain.planning_coverage import CoverageEdit
    if type(value) is not dict:raise ValueError('Coverage draft must be typed')
    checked=deepcopy(value)
    exclusions=checked.get('user_exclusions')
    if type(exclusions) is not list:raise ValueError('Coverage exclusions must be a list')
    for row in exclusions:
        if type(row) is not dict:raise ValueError('Coverage exclusion must be typed')
        reason=row.get('reason');_text(reason,16000)
        if len(reason)>4000:raise ValueError('Exclusion draft reason is too long')
        # Preserve unfinished user text; this only validates presentation data.
        # The business CoverageEdit still rejects blank exclusions on dispatch.
        if not reason.strip():row['reason']='unfinished local reason'
    CoverageEdit.model_validate(checked,strict=True)


def _coverage_operation(key,value):
    if value is None:return
    required={'kind','operation_id','project_id','proposal_ref','request_sha256','candidate_sha256',
        'coverage_draft_sha256','coverage_sha256','resolutions_sha256','state','result_ref'}
    if type(value) is not dict or set(value)!=required or len(canonical(value).encode())>8192:
        raise ValueError('Original coverage operation must be bounded typed metadata')
    if value['kind'] not in ('SAVE','CONFIRM') or value['state'] not in ('PREPARED','UNKNOWN','REJECTED','COMPLETED'):
        raise ValueError('Unknown coverage operation')
    if value['project_id']!=key.project_id:raise ValueError('Coverage operation belongs to another project')
    _writing_id(value['operation_id']);_manual_target_ref(key,value['proposal_ref'])
    for name in ('request_sha256','candidate_sha256'):_manual_sha(value[name])
    for name in ('coverage_draft_sha256','coverage_sha256','resolutions_sha256'):
        if value[name] is not None:_manual_sha(value[name])
    if value['kind']=='SAVE':
        if value['coverage_draft_sha256'] is None or value['coverage_sha256'] is not None or value['resolutions_sha256'] is not None:
            raise ValueError('Original coverage save needs its exact draft digest')
    elif value['coverage_sha256'] is None or value['resolutions_sha256'] is None or value['coverage_draft_sha256'] is None:
        raise ValueError('Original confirmation needs its coverage and decisions digests')
    if value['result_ref'] is not None:
        _writing_ref(value['result_ref'])
        if value['result_ref']['id']!=key.object_id or value['result_ref']['revision']!=key.revision+1:
            raise ValueError('Coverage result belongs to another version')
    if (value['state']=='COMPLETED')!=(value['result_ref'] is not None):raise ValueError('Completed coverage operation needs its original exact result')


def _manual_sha(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Manual draft needs an exact lowercase SHA')


def _manual_target_ref(key, value):
    _writing_ref(value)
    if value != {'id': key.object_id, 'revision': key.revision, 'sha256': key.sha256}:
        raise ValueError('Manual draft must retain its original proposal basis')


def _manual_workspace(value):
    if type(value) is not dict or set(value) != {'id', 'revision', 'binding_sha256'}:
        raise ValueError('Manual draft needs an exact workspace reference')
    _manual_sha(value['id']); _manual_sha(value['binding_sha256'])
    if type(value['revision']) is not int or not 1 <= value['revision'] <= 2147483647:
        raise ValueError('Manual workspace revision must be a positive integer')


def _manual_coverage(key, value):
    """Persist the bounded producer diagnostics exactly, never as write authority.

    Keep the original five-field form compatible. Extended actual gate metadata
    is closed and typed; no unknown reason, omitted coverage or byte ledger is
    silently dropped to make a draft appear saved or a gate appear clear.
    """
    required = {'complete', 'total_registered_files', 'checked_files', 'scanned_export_count', 'unverified'}
    paths = {'checked_paths', 'unchecked_paths', 'unchecked_path_count',
             'unlisted_checked_path_count', 'unlisted_unchecked_path_count'}
    optional = paths | {'raw_bytes_verified', 'read_receipt', 'uncovered_changed_paths', 'limit', 'pending_exports'}
    if type(value) is not dict or not required <= set(value) <= required | optional or type(value['complete']) is not bool:
        raise ValueError('Manual coverage must be explicit bounded metadata')

    def count(item, maximum=1000000):
        if type(item) is not int or not 0 <= item <= maximum:
            raise ValueError('Invalid bounded manual coverage count')

    def path_list(items, maximum):
        if type(items) is not list or len(items) > maximum:
            raise ValueError('Manual coverage path budget exceeded')
        for path in items:
            _text(path, 4096, nonempty=True); relative(path)
        if len(set(items)) != len(items): raise ValueError('Duplicate manual coverage paths')

    for name in ('total_registered_files', 'checked_files', 'scanned_export_count'):
        count(value[name])
    if value['checked_files'] > value['total_registered_files']:
        raise ValueError('Checked files exceed registered coverage')
    if type(value['unverified']) is not list or len(value['unverified']) > 200:
        raise ValueError('Manual unverified metadata exceeds budget')
    for item in value['unverified']:
        if type(item) is not dict or not {'reason'} <= set(item) <= {'path', 'reason', 'message'}:
            raise ValueError('Unknown unverified metadata')
        _text(item['reason'], 800, nonempty=True)
        if 'path' in item: _text(item['path'], 4096, nonempty=True); relative(item['path'])
        if 'message' in item: _text(item['message'], 4000, nonempty=True)
    if 'uncovered_changed_paths' in value: path_list(value['uncovered_changed_paths'], 32)
    if set(value) & paths:
        if not paths <= set(value): raise ValueError('Checked and unchecked coverage must remain complete')
        for name in ('checked_paths', 'unchecked_paths'): path_list(value[name], 256)
        for name in paths - {'checked_paths', 'unchecked_paths'}: count(value[name])
        if (set(value['checked_paths']) & set(value['unchecked_paths']) or
                len(value['checked_paths']) + value['unlisted_checked_path_count'] != value['checked_files'] or
                len(value['unchecked_paths']) + value['unlisted_unchecked_path_count'] != value['unchecked_path_count'] or
                value['checked_files'] + value['unchecked_path_count'] != value['total_registered_files']):
            raise ValueError('Manual coverage path counts disagree')
    raw_limit, metadata_limit = 8*1024*1024, 16*1024*1024
    if 'raw_bytes_verified' in value: count(value['raw_bytes_verified'], raw_limit)
    if 'read_receipt' in value:
        receipt = value['read_receipt']
        if (type(receipt) is not dict or set(receipt) != {'limit_bytes', 'charged_bytes', 'known_raw_bytes',
                'failed_read_upper_bound', 'scope', 'reads', 'metadata', 'combined_input_upper_bound_bytes'} or
                type(receipt['limit_bytes']) is not int or receipt['limit_bytes'] != raw_limit or
                type(receipt['combined_input_upper_bound_bytes']) is not int or
                receipt['combined_input_upper_bound_bytes'] != raw_limit + metadata_limit or
                receipt['scope'] != 'MANUAL_REGISTERED_HARNESS_INPUT_PROOF_NOT_GENERATION_OUTPUT_IO'):
            raise ValueError('Unknown manual raw input receipt')
        for name in ('charged_bytes', 'known_raw_bytes', 'failed_read_upper_bound'): count(receipt[name], raw_limit)
        kinds = {'SAVED_READ', 'CAPTURE_LIVE_READ', 'CAPTURE_HEAD_READ', 'FROZEN_BASIS_READ',
                 'FAILED_SAVED_READ_RESERVED', 'FAILED_LIVE_HEAD_READ_RESERVED', 'FAILED_BASIS_READ_RESERVED'}
        if type(receipt['reads']) is not list or len(receipt['reads']) > MAX_ITEMS:
            raise ValueError('Manual raw receipt exceeds presentation budget')
        known, failed = 0, 0
        for item in receipt['reads']:
            if (type(item) is not dict or set(item) != {'path', 'kind', 'bytes', 'exact'} or
                    type(item['kind']) is not str or item['kind'] not in kinds or type(item['exact']) is not bool):
                raise ValueError('Unknown manual raw read diagnostic')
            _text(item['path'], 4096, nonempty=True); relative(item['path']); count(item['bytes'], raw_limit)
            if item['exact']: known += item['bytes']
            else: failed += item['bytes']
        if (known != receipt['known_raw_bytes'] or failed != receipt['failed_read_upper_bound'] or
                known + failed != receipt['charged_bytes']): raise ValueError('Manual raw ledger totals disagree')
        metadata = receipt['metadata']
        if (type(metadata) is not dict or set(metadata) != {'limit_bytes', 'charged_bytes', 'failed_read_upper_bound', 'reads'} or
                type(metadata['limit_bytes']) is not int or metadata['limit_bytes'] != metadata_limit or
                type(metadata['reads']) is not list or len(metadata['reads']) > MAX_ITEMS):
            raise ValueError('Unknown manual metadata receipt')
        for name in ('charged_bytes', 'failed_read_upper_bound'): count(metadata[name], metadata_limit)
        charged, failed = 0, 0
        for item in metadata['reads']:
            if type(item) is not dict or set(item) != {'record', 'bytes', 'exact'} or type(item['exact']) is not bool:
                raise ValueError('Unknown manual metadata read diagnostic')
            _text(item['record'], 1024, nonempty=True); count(item['bytes'], metadata_limit)
            charged += item['bytes']
            if not item['exact']: failed += item['bytes']
        if charged != metadata['charged_bytes'] or failed != metadata['failed_read_upper_bound']:
            raise ValueError('Manual metadata ledger totals disagree')
        if value.get('raw_bytes_verified', 0) > receipt['known_raw_bytes']:
            raise ValueError('Verified raw bytes exceed the actual read receipt')
    if 'limit' in value:
        limit = value['limit']
        if (type(limit) is not dict or set(limit) != {'kind', 'max', 'observed_at_least'} or
                limit['kind'] != 'CHANGED_PROMPT_FILES' or type(limit['max']) is not int or limit['max'] != 32 or
                type(limit['observed_at_least']) is not int or not 33 <= limit['observed_at_least'] <= 1000000):
            raise ValueError('Unknown manual changed-file limit')
    if 'pending_exports' in value:
        pending = value['pending_exports']
        if type(pending) is not list or len(pending) > 200:
            raise ValueError('Pending export presentation budget exceeded')
        operations = set()
        for item in pending:
            if (type(item) is not dict or set(item) != {'operation_hash', 'request_sha256', 'proposal_ref', 'recovery', 'status'} or
                    item['recovery'] != 'ORIGINAL_OPERATION_ONLY' or item['status'] != 'PENDING_EXPORT_RESULT'):
                raise ValueError('Unknown pending export diagnostic')
            _manual_sha(item['operation_hash']); _manual_sha(item['request_sha256']); _writing_ref(item['proposal_ref'])
            if (item['operation_hash'] in operations or item['proposal_ref']['id'] != key.object_id or
                    item['proposal_ref']['revision'] > key.revision): raise ValueError('Pending export has foreign or duplicate scope')
            operations.add(item['operation_hash'])
    if value['complete'] and (value['checked_files'] != value['total_registered_files'] or value['unverified'] or
            value.get('unchecked_path_count', 0) or value.get('uncovered_changed_paths') or
            value.get('pending_exports') or 'limit' in value):
        raise ValueError('Incomplete or unknown coverage cannot be stored as clear')


def _manual_draft(key, kind, value):
    if value is None: return
    if type(value) is not dict: raise ValueError('Manual draft must be a typed object')
    if kind == 'manual-gate':
        if set(value) != {'code', 'target_proposal_ref', 'workspace_ref', 'status', 'files', 'coverage', 'predecessor_export_refs'}:
            raise ValueError('Unknown manual gate presentation field')
        if value['code'] not in ('MANUAL_REVIEW_REQUIRED', 'SOURCE_DRIFT', 'UNVERIFIED') or value['status'] not in ('CLEAR', 'REVIEW_REQUIRED', 'STALE', 'UNVERIFIED'):
            raise ValueError('Unknown manual gate state')
        _manual_target_ref(key, value['target_proposal_ref'])
        if value['workspace_ref'] is None:
            if value['code'] != 'SOURCE_DRIFT' or value['files']: raise ValueError('Only drift metadata may omit the workspace')
        else: _manual_workspace(value['workspace_ref'])
        if type(value['files']) is not list or len(value['files']) > 32: raise ValueError('Manual draft exceeds 32 chosen files')
        from packages.domain.planning_schema import ManualFileRef, ManualExportRef
        for item in value['files']:
            if type(item) is not dict or set(item) != {'export_ref', 'path', 'basis_sha256', 'edited_sha256', 'node_id', 'kind', 'status'}:
                raise ValueError('Unknown registered manual file field')
            ManualFileRef.model_validate({k: item[k] for k in ('export_ref', 'path', 'basis_sha256', 'edited_sha256')}, strict=True)
            if item['node_id'] is not None: _writing_id(item['node_id'])
            if item['kind'] not in ('PROMPT', 'OTHER') or item['status'] != 'MODIFIED': raise ValueError('Unknown registered change kind')
        _manual_coverage(key, value['coverage'])
        if type(value['predecessor_export_refs']) is not list or len(value['predecessor_export_refs']) > 200: raise ValueError('Predecessor metadata exceeds budget')
        for item in value['predecessor_export_refs']: ManualExportRef.model_validate(item, strict=True)
    elif kind == 'manual-capture':
        if set(value) != {'request', 'request_sha256', 'operation_hash', 'state', 'capture_ref'} or value['state'] not in ('PREPARED', 'UNKNOWN', 'REJECTED', 'COMPLETED'):
            raise ValueError('Unknown original manual capture state')
        body = ManualCaptureInput.model_validate(value['request'], strict=True)
        _manual_target_ref(key, body.target_proposal_ref.model_dump()); _manual_workspace(body.workspace_ref.model_dump())
        if value['request_sha256'] != sha(canonical(body.model_dump(exclude={'operation_id'})).encode()) or value['operation_hash'] != sha(canonical(body.operation_id).encode()):
            raise ValueError('Original capture request/SHA differs')
        if value['capture_ref'] is not None: ManualCaptureRef.model_validate(value['capture_ref'], strict=True)
        if (value['state'] == 'COMPLETED') != (value['capture_ref'] is not None): raise ValueError('Completed capture needs its exact result reference')
    elif kind == 'manual-review':
        if set(value) != {'change_ref', 'choices', 'applied'}: raise ValueError('Unknown manual review draft fields')
        ManualCaptureRef.model_validate(value['change_ref'], strict=True)
        if type(value['choices']) is not list or len(value['choices']) > 200 or len({i.get('hunk_id') for i in value['choices'] if type(i) is dict}) != len(value['choices']):
            raise ValueError('At most 200 unique hunk choices are allowed')
        for item in value['choices']:
            if type(item) is not dict or set(item) != {'hunk_id', 'hunk_sha256', 'node_id', 'field', 'edit_action', 'item_index', 'mode', 'text', 'reason'}: raise ValueError('Unknown manual choice fields')
            _manual_sha(item['hunk_id']); _manual_sha(item['hunk_sha256']); _writing_id(item['node_id'], empty=True)
            if item['field'] not in ('', 'description', 'inputs', 'outputs', 'acceptance', 'risks') or item['edit_action'] not in ('', 'ADD', 'REPLACE', 'REMOVE') or item['mode'] not in ('', 'KEEP', 'REPLACE', 'DISCARD'):
                raise ValueError('Unknown manual draft choice')
            if type(item['item_index']) is not str or not re.fullmatch('[0-9]{0,5}', item['item_index']): raise ValueError('Manual list locator must be explicit')
            _text(item['text'], 96000); _text(item['reason'], 16000)
            if len(item['text']) > 24000 or len(item['reason']) > 4000: raise ValueError('Manual choice exceeds text budget')
        if type(value['applied']) is not list or len(value['applied']) > 200: raise ValueError('Too many applied draft decisions')
        for item in value['applied']: ManualDecision.model_validate(item, strict=True)
    else:
        required = {'operation_id', 'expected_revision', 'candidate', 'change_reason', 'manual_review', 'request_sha256', 'state', 'result_ref'}
        if set(value)-{'requirement_coverage'} != required or value['state'] not in ('PREPARED', 'UNKNOWN', 'REJECTED', 'COMPLETED'): raise ValueError('Unknown original manual save state')
        _writing_id(value['operation_id'])
        if type(value['expected_revision']) is not int or value['expected_revision'] != key.revision: raise ValueError('Original manual save revision differs')
        candidate = PlanningCandidate.model_validate(value['candidate'], strict=True)
        review = ManualReview.model_validate(value['manual_review'], strict=True); _text(value['change_reason'], 16000, nonempty=True)
        if len(value['change_reason']) > 4000: raise ValueError('Manual save reason too long')
        body = {'proposal_id': key.object_id, 'expected_revision': key.revision, 'candidate': candidate.model_dump(), 'change_reason': value['change_reason'], 'manual_review': review.model_dump()}
        if 'requirement_coverage' in value:
            if value['requirement_coverage'] is None:raise ValueError('Original manual save must omit unused coverage')
            _claim_coverage(value['requirement_coverage'])
            body['requirement_coverage']=value['requirement_coverage']
        if value['request_sha256'] != sha(canonical(body).encode()): raise ValueError('Original manual save request/SHA differs')
        if value['result_ref'] is not None:
            _writing_ref(value['result_ref'])
            if value['result_ref']['id'] != key.object_id or value['result_ref']['revision'] != key.revision + 1: raise ValueError('Manual save result has foreign scope')
        if (value['state'] == 'COMPLETED') != (value['result_ref'] is not None): raise ValueError('Completed manual save needs its exact result reference')


def _writing_id(value, *, empty=False):
    if type(value) is not str or not (empty and value == '') and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Writing draft needs an exact bounded identity')


def _writing_ref(value):
    if type(value) is not dict or set(value) != {'id', 'revision', 'sha256'}:
        raise ValueError('Writing draft needs a complete exact revision reference')
    _writing_id(value['id'])
    if type(value['revision']) is not int or not 1 <= value['revision'] <= 9007199254740991:
        raise ValueError('Writing revision must be a positive safe integer')
    if type(value['sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', value['sha256']):
        raise ValueError('Writing revision SHA is invalid')


def _writing_ids(value):
    if type(value) is not list or len(value) > 100 or any(type(item) is not str for item in value) or len(set(value)) != len(value):
        raise ValueError('Writing selections need at most 100 unique rule IDs')
    for ident in value: _writing_id(ident)


def _writing_draft(key, kind, value):
    if value is None and kind != 'experience-form': return
    if type(value) is not dict: raise ValueError('Writing presentation draft must be an object')
    if kind == 'experience-form':
        if set(value) != {'name', 'sources', 'path', 'role', 'source_project_id', 'source_profile_ref', 'import_name', 'selected_rule_ids'}:
            raise ValueError('Unexpected experience form fields')
        _text(value['name'], 800); _text(value['import_name'], 800); _text(value['path'], 4000)
        if len(value['name']) > 200 or len(value['import_name']) > 200 or len(value['path']) > 1000:
            raise ValueError('Writing form text is too long')
        if value['role'] not in ('TEMPLATE', 'WRITING_EXAMPLE'): raise ValueError('Unknown writing source role')
        _writing_id(value['source_project_id'], empty=True); _writing_ids(value['selected_rule_ids'])
        if value['source_profile_ref'] is not None:
            _writing_ref(value['source_profile_ref'])
            if not value['source_project_id']: raise ValueError('Imported source reference needs its project')
        elif value['selected_rule_ids']: raise ValueError('Selected writing rules need their source reference')
        if type(value['sources']) is not list or len(value['sources']) > 8: raise ValueError('At most eight writing source drafts')
        for source in value['sources']:
            if type(source) is not dict or set(source) != {'role', 'file_source_ref'} or source['role'] not in ('TEMPLATE', 'WRITING_EXAMPLE'):
                raise ValueError('Writing source draft has invalid fields')
            parsed = FileSource.model_validate(source['file_source_ref'], strict=True)
            relative(parsed.path)
            if parsed.project_id != key.project_id: raise ValueError('Writing file source belongs to another project')
        return
    if kind == 'writing-selection':
        if set(value) != {'project_id', 'profile_ref', 'name'} or value['project_id'] != key.project_id:
            raise ValueError('Writing selection belongs to another project')
        _writing_ref(value['profile_ref']); _text(value['name'], 800)
        if len(value['name']) > 200: raise ValueError('Writing selection name is too long')
        return
    if kind == 'writing-import':
        if set(value) != {'target_project_id', 'request', 'profile_id', 'operation_hash', 'request_sha256', 'result_profile_ref', 'state'} or value['target_project_id'] != key.project_id:
            raise ValueError('Writing import draft belongs to another target project')
        if value['state'] not in ('PREPARED', 'UNKNOWN', 'SUCCEEDED', 'REJECTED'): raise ValueError('Unknown writing import state')
        request = value['request']
        if type(request) is not dict: raise ValueError('Writing import needs its original request')
        _writing_ref(request.get('source_profile_ref'))
        parsed = ProfileImport.model_validate(request, strict=True)
        frozen = {'method': 'IMPORT_REVIEWED', **parsed.model_dump(exclude={'operation_id'})}
        expected = {'profile_id': sha(canonical({'project_id': key.project_id, 'request': frozen}).encode()),
                    'operation_hash': sha(canonical(parsed.operation_id).encode()),
                    'request_sha256': sha(canonical(frozen).encode())}
        if any(value[name] != digest for name, digest in expected.items()):
            raise ValueError('Writing import recovery identity differs from its frozen request')
        if value['state'] == 'SUCCEEDED':
            _writing_ref(value['result_profile_ref'])
            if value['result_profile_ref']['id'] != value['profile_id']: raise ValueError('Import result differs from its original target')
        elif value['result_profile_ref'] is not None: raise ValueError('Unfinished import cannot carry a result reference')
        return
    if set(value) != {'project_id', 'profile_ref', 'selected_rule_ids', 'edits', 'reason', 'operation_id', 'request_sha256', 'result_profile_ref', 'state'} or value['project_id'] != key.project_id:
        raise ValueError('Writing review draft belongs to another project')
    _writing_ref(value['profile_ref']); _writing_ids(value['selected_rule_ids']); _text(value['reason'], 16000)
    if len(value['reason']) > 4000: raise ValueError('Writing review reason is too long')
    if value['state'] not in ('EDITING', 'PREPARED', 'UNKNOWN', 'COMPLETED'): raise ValueError('Unknown writing review state')
    if value['state'] == 'EDITING':
        if value['operation_id'] is not None or value['request_sha256'] is not None or value['result_profile_ref'] is not None: raise ValueError('Edited review cannot reuse a frozen operation')
    else:
        _writing_id(value['operation_id'])
        if not value['selected_rule_ids'] or not value['reason'].strip(): raise ValueError('Frozen review needs explicit selected rules and reason')
    if type(value['edits']) is not list or len(value['edits']) > 100: raise ValueError('At most 100 edited writing rules')
    seen = set(); normalized = []
    for rule in value['edits']:
        if type(rule) is not dict: raise ValueError('Edited writing rule must be an object')
        parsed_rule = dict(rule)
        # Incomplete text is a draft, not a granted or submitted writing rule.
        if value['state'] == 'EDITING' or rule.get('id') not in value['selected_rule_ids']:
            _text(rule.get('instruction'), 16000)
            if len(rule['instruction']) > 4000: raise ValueError('Writing instruction draft is too long')
            if not rule['instruction']: parsed_rule['instruction'] = 'draft'
            if rule.get('heading') == '': parsed_rule['heading'] = 'draft'
            if rule.get('section_key') is not None and parsed_rule.get('heading') is None: parsed_rule['heading'] = 'draft'
        parsed = WritingRule.model_validate(parsed_rule, strict=True)
        if parsed.id in seen: raise ValueError('Writing review edits must be unique')
        seen.add(parsed.id)
        if parsed.id in value['selected_rule_ids']: normalized.append(parsed.model_dump())
    if value['state'] != 'EDITING':
        # Match ProfileReview.model_dump exactly, including explicit null fields.
        frozen = {'method': 'REVIEW', 'profile_id': value['profile_ref']['id'],
                  'expected_revision': value['profile_ref']['revision'],
                  'accepted_rule_ids': value['selected_rule_ids'], 'edits': normalized, 'reason': value['reason']}
        if value['request_sha256'] != sha(canonical(frozen).encode()): raise ValueError('Review request hash differs from its original fields')
    if value['state'] == 'COMPLETED':
        _writing_ref(value['result_profile_ref'])
        if (value['result_profile_ref']['id'] != value['profile_ref']['id'] or
                value['result_profile_ref']['revision'] <= value['profile_ref']['revision']):
            raise ValueError('Review result must be a newer revision of the original profile')
    elif value['result_profile_ref'] is not None: raise ValueError('Unfinished review cannot carry a result reference')
    # Stored selections and requests are presentation only, never review authority.


def _feedback_drafts(value, *, graph):
    if type(value) is not dict or len(value) > 64:
        raise ValueError('At most 64 unfinished feedback selections are supported')
    for ident, item in value.items():
        _text(ident, 1024, nonempty=True)
        required = {'comment', 'tool', 'selection'} if graph else {'comment', 'selection'}
        if type(item) is not dict or set(item) != required:
            raise ValueError('Feedback drafts contain selection/comment only, never delivery results')
        _text(item['comment'], 32000)
        if len(item['comment']) > 8000: raise ValueError('Feedback comment exceeds 8000 characters')
        selected = item['selection']
        if graph:
            if type(item['tool']) is not str or item['tool'] not in {'COMMENT', 'RECTANGLE', 'FREEHAND'}:
                raise ValueError('Unknown graph annotation tool')
            if type(selected) is not dict or set(selected) - {'graph_id', 'node_ids', 'normalized_points', 'viewport'}:
                raise ValueError('Graph feedback contains only its actual graph selection')
            if not {'graph_id', 'node_ids', 'normalized_points'} <= set(selected) or selected['graph_id'] != ident:
                raise ValueError('Graph draft must retain its exact graph ID')
            parsed = Selection.model_validate(selected, strict=True)
            if len(parsed.normalized_points) > 512:
                raise ValueError('Unfinished graph annotation exceeds 512 points; no points were truncated')
            # In-progress gestures may have zero/one point. Submission semantics
            # remain the existing business feedback validator's responsibility.
        else:
            if type(selected) is not dict or selected.get('offset_unit') != 'UNICODE_CODEPOINT':
                raise ValueError('Text draft needs explicit Unicode codepoint offsets')
            parsed = TextSelection.model_validate(selected, strict=True)
            if parsed.locator.kind not in {'CANDIDATE_FIELD', 'REPORT_PARAGRAPH', 'OCR_LINE'}:
                raise ValueError('Text draft locator is not supported by this renderer')
            if sha(parsed.text.encode('utf-8')) != parsed.quote_sha256:
                raise ValueError('Text draft quote SHA does not match its selected text')
            if parsed.locator.char_end - parsed.locator.char_start != len(parsed.text):
                raise ValueError('Text draft range must match its exact Unicode codepoint quote')


def _number(value, *, positive=False):
    if type(value) not in (int, float) or abs(value) > 10_000_000 or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('View coordinates must be bounded finite numbers')


def _point(value):
    if type(value) is not dict or set(value) != {'x', 'y'}:
        raise ValueError('View point needs exactly x and y')
    _number(value['x']); _number(value['y'])


def _view(key, value, *, graph=False):
    if type(value) is not dict or set(value) - (GRAPH_VIEW if graph else VIEW):
        raise ValueError('Unknown view field; business status is not presentation state')
    for name, item in value.items():
        if name == 'zoom':
            _number(item, positive=True)
            if item > 100: raise ValueError('Graph zoom exceeds 100')
        elif name in {'pan', 'scroll'}: _point(item)
        elif name == 'viewport':
            if not graph or type(item) is not dict or set(item) != {'width', 'height'}:
                raise ValueError('Graph viewport needs exactly width and height')
            for size in item.values():
                _number(size, positive=True)
                if size > 100000: raise ValueError('Graph viewport exceeds the presentation limit')
        elif name == 'positions':
            if type(item) is not dict or len(item) > 2000: raise ValueError('At most 2000 graph positions are supported')
            for ident, point in item.items(): _text(ident, 1024, nonempty=True); _point(point)
        elif name in {'selected', 'expanded'}: _ids(item)
        elif name in {'mode', 'search', 'tab'}: _text(item, 4096)
        elif name in {'hidden', 'editing'}:
            if type(item) is not bool: raise ValueError('Presentation visibility must be a boolean')
        elif name == 'active_key':
            if item is None: continue
            target = WorkbenchKey.model_validate(item)
            if target.project_id != key.project_id: raise ValueError('Navigation target belongs to another project')
        elif name == 'graphs':
            if type(item) is not dict or len(item) > 64: raise ValueError('At most 64 graph views are supported')
            for ident, graph_view in item.items(): _text(ident, 1024, nonempty=True); _view(key, graph_view, graph=True)
        elif name == 'panes':
            if type(item) is not dict or set(item) - {'files_width', 'agent_width', 'files_hidden', 'agent_hidden', 'workbench_hidden'}:
                raise ValueError('Unknown pane presentation field')
            for pane, setting in item.items():
                if pane.endswith('_width'):
                    _number(setting, positive=True)
                    if setting > 10000: raise ValueError('Pane width exceeds the presentation limit')
                elif type(setting) is not bool: raise ValueError('Pane visibility must be a boolean')


class WorkbenchLoadInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    key: WorkbenchKey


class WorkbenchSaveInput(WorkbenchLoadInput):
    expected_revision: int = Field(ge=0, le=2147483646)
    operation_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    fields: dict[str, Any]
    view: dict[str, Any]
    dirty: bool

    @model_validator(mode='after')
    def presentation_only(self):
        validate_presentation(self.key, self.fields, self.view)
        return self


def validate_presentation(key, fields, view):
    # Walk before recursive encoding so deeply nested/cyclic direct-call inputs
    # also fail within the budget, without recursion or discarded old drafts.
    _bounded({'key': key.data(), 'fields': fields, 'view': view})
    _fields(key, fields); _view(key, view)


def _integrity():
    return Conflict('WORKBENCH_STATE_INTEGRITY: stored presentation is unavailable; original drafts were preserved')


class WorkbenchState:
    def __init__(self, store, data_dir, *, workspaces=None):
        self.store, self.data_dir, self.workspaces = store, data_dir, workspaces

    @staticmethod
    def _route(pid, key):
        if pid != key.project_id: raise InvalidInput('Route project differs from exact presentation key')

    def _new_binding(self, pid, key):
        if key.workspace_id is None: return
        if self.workspaces is None: raise Conflict('Workspace registry is unavailable; no presentation was saved')
        bound = self.workspaces.get(pid, key.workspace_id, key.binding_revision)
        if bound['binding_sha256'] != key.binding_sha256:
            raise Conflict('WORKSPACE_BINDING_CONFLICT: presentation must keep its exact registered binding')

    @staticmethod
    def _identity(key):
        raw = canonical(key.data())
        return raw, sha(raw.encode('utf-8'))

    @staticmethod
    def _head(db, pid, ident):
        return db.execute('SELECT * FROM workbench_state_heads WHERE project_id=? AND key_sha256=?', (pid, ident)).fetchone()

    @staticmethod
    def _json(raw, digest, *, maximum=MAX_BYTES + 65536):
        try:
            if type(raw) is not str or len(raw.encode('utf-8')) > maximum or sha(raw.encode('utf-8')) != digest:
                raise ValueError
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if canonical(value) != raw: raise ValueError
            return value
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise _integrity() from None

    @classmethod
    def _head_key(cls, head):
        value = cls._json(head['key_json'], head['key_sha256'], maximum=16384)
        try:
            key = WorkbenchKey.model_validate(value)
            if key.project_id != head['project_id'] or key.data() != value: raise ValueError
            return key
        except (ValueError, TypeError): raise _integrity() from None

    @staticmethod
    def _state_payload(value, pid):
        try:
            if type(value) is not dict or set(value) != {'schema_version', 'key', 'state_revision', 'fields', 'view', 'dirty', 'updated_at'}:
                raise ValueError
            key = WorkbenchKey.model_validate(value['key'])
            if value['schema_version'] != 1 or type(value['schema_version']) is not int or key.project_id != pid:
                raise ValueError
            if type(value['state_revision']) is not int or value['state_revision'] < 1 or type(value['dirty']) is not bool:
                raise ValueError
            _text(value['updated_at'], 100, nonempty=True)
            validate_presentation(key, value['fields'], value['view'])
            return key
        except (ValueError, TypeError, KeyError): raise _integrity() from None

    @classmethod
    def _read_state(cls, db, head):
        key = cls._head_key(head)
        row = db.execute('SELECT * FROM workbench_state_versions WHERE project_id=? AND key_sha256=? AND state_revision=?',
                         (head['project_id'], head['key_sha256'], head['current_revision'])).fetchone()
        if row is None: raise _integrity()
        value = cls._json(row['payload'], row['sha256'])
        parsed = cls._state_payload(value, head['project_id'])
        if (parsed.data() != key.data() or value['state_revision'] != row['state_revision'] or
                value['dirty'] != bool(row['dirty']) or value['updated_at'] != row['updated_at']): raise _integrity()
        return {'status': 'SAVED', **value, 'sha256': row['sha256']}

    @classmethod
    def _receipt(cls, row, pid, *, replayed):
        value = cls._json(row['result'], row['result_sha256'])
        try:
            extra = {'status', 'sha256', 'operation_id', 'operation_status', 'request_sha256'}
            if type(value) is not dict: raise ValueError
            core = {k: v for k, v in value.items() if k not in extra}
            cls._state_payload(core, pid)
            if (value['status'] != 'SAVED' or value['operation_status'] != 'COMPLETED' or
                    value['operation_id'] != row['operation_id'] or value['request_sha256'] != row['request_sha256'] or
                    sha(canonical(core).encode('utf-8')) != value['sha256']): raise ValueError
            return {**value, 'replayed': replayed}
        except (ValueError, KeyError, TypeError): raise _integrity() from None

    def load(self, pid, body):
        key = body.key; self._route(pid, key); self.store.project(pid)
        raw, ident = self._identity(key)
        with self.store.connect() as db:
            head = self._head(db, pid, ident)
            if head is not None:
                if head['key_json'] != raw: raise _integrity()
                # An immutable saved key witnesses its historical binding. No
                # current folder access or replacement-binding substitution.
                return self._read_state(db, head)
        self._new_binding(pid, key)
        return {'status': 'EMPTY', 'schema_version': 1, 'key': key.data(), 'state_revision': 0,
                'fields': {}, 'view': {}, 'dirty': False, 'sha256': None, 'updated_at': None}

    def save(self, pid, body):
        key = body.key; self._route(pid, key); self.store.project(pid)
        raw, ident = self._identity(key)
        request_sha = sha(canonical({**body.model_dump(exclude_none=True), 'key': key.data()}).encode('utf-8'))
        # Check the original receipt before consulting a possibly unavailable
        # live binding. An observed receipt never restarts a write/authorization.
        with self.store.connect() as db:
            old = db.execute('SELECT * FROM workbench_state_operations WHERE project_id=? AND operation_id=?',
                             (pid, body.operation_id)).fetchone()
            if old is not None:
                if old['request_sha256'] != request_sha: raise Conflict('IDEMPOTENCY_CONFLICT: operation already has different input')
                return self._receipt(old, pid, replayed=True)
            known = self._head(db, pid, ident)
            if known is not None: self._read_state(db, known)
        if known is None: self._new_binding(pid, key)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM workbench_state_operations WHERE project_id=? AND operation_id=?',
                             (pid, body.operation_id)).fetchone()
            if old is not None:
                if old['request_sha256'] != request_sha: raise Conflict('IDEMPOTENCY_CONFLICT: operation already has different input')
                return self._receipt(old, pid, replayed=True)
            head = self._head(db, pid, ident)
            if head is not None: self._read_state(db, head)
            actual = head['current_revision'] if head is not None else 0
            if actual != body.expected_revision: raise Conflict('WORKBENCH_STATE_CAS_CONFLICT: reload the original key; local edits were not overwritten')
            revision = actual + 1; stamp = datetime.now(timezone.utc).isoformat()
            value = {'schema_version': 1, 'key': key.data(), 'state_revision': revision,
                     'fields': body.fields, 'view': body.view, 'dirty': body.dirty, 'updated_at': stamp}
            payload = canonical(value); digest = sha(payload.encode('utf-8'))
            if head is None:
                db.execute('INSERT INTO workbench_state_heads VALUES (?,?,?,?)', (pid, ident, raw, revision))
            else:
                db.execute('UPDATE workbench_state_heads SET current_revision=? WHERE project_id=? AND key_sha256=?', (revision, pid, ident))
            db.execute('INSERT INTO workbench_state_versions VALUES (?,?,?,?,?,?,?)',
                       (pid, ident, revision, payload, digest, int(body.dirty), stamp))
            result = {'status': 'SAVED', **value, 'sha256': digest, 'operation_id': body.operation_id,
                      'operation_status': 'COMPLETED', 'request_sha256': request_sha}
            encoded = canonical(result)
            db.execute('INSERT INTO workbench_state_operations VALUES (?,?,?,?,?)',
                       (pid, body.operation_id, request_sha, encoded, sha(encoded.encode('utf-8'))))
            return {**result, 'replayed': False}

    def operation(self, pid, operation_id):
        self.store.project(pid)
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 100 or any(
                not (c.isascii() and (c.isalnum() or c in '_-')) for c in operation_id):
            raise InvalidInput('Operation ID must be a bounded safe identity')
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM workbench_state_operations WHERE project_id=? AND operation_id=?', (pid, operation_id)).fetchone()
            if row is None: raise NotFound('Workbench operation has no observed committed receipt; keep the pending draft')
            return self._receipt(row, pid, replayed=True)

    def drafts(self, pid, *, offset=0, limit=50):
        self.store.project(pid)
        if type(offset) is not int or not 0 <= offset <= 1_000_000 or type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput('Draft page needs offset 0..1000000 and limit 1..100')
        with self.store.connect() as db:
            heads = db.execute('''SELECT h.* FROM workbench_state_heads h
                LEFT JOIN workbench_state_versions v ON v.project_id=h.project_id AND v.key_sha256=h.key_sha256 AND v.state_revision=h.current_revision
                WHERE h.project_id=? AND (v.dirty=1 OR v.state_revision IS NULL)
                ORDER BY h.key_sha256 LIMIT ? OFFSET ?''', (pid, limit + 1, offset)).fetchall()
            values, unavailable, consumed, byte_count = [], [], 0, 0
            for head in heads[:limit]:
                try:
                    value = self._read_state(db, head)
                    size = len(canonical(value).encode('utf-8'))
                    if consumed and byte_count + size > MAX_DRAFT_PAGE_BYTES: break
                    values.append(value); byte_count += size
                except Conflict:
                    unavailable.append({'key_sha256': head['key_sha256'], 'state_revision': head['current_revision'], 'status': 'INTEGRITY_ERROR'})
                consumed += 1
            has_more = len(heads) > consumed
            return {'drafts': values, 'unavailable': unavailable, 'has_more': has_more,
                    'next_offset': offset + consumed if has_more else None}
