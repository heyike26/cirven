"""Scoped file/object feedback and checkable local rework, without a model loop.

Classification and dependency conditions are supplied proposals, not semantic
certification. JSON value splices use Python's JSON decoder and preserve bytes
outside each selected value. Text rebases are deliberately conservative. Real
file writes reuse LocalWorkspaces' immutable versions and durable CAS receipts.
"""
import json
import re
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from packages.domain.intake import canonical, digest
from packages.domain.local_workspaces import FileEditInput, FileSource, MAX_EDIT_BYTES, relative
from packages.domain.paths import resolve_data_path
from packages.domain.planning_schema import PlanningContextRef
from packages.domain.questions import Hash, Id, raw_hash, stamp
from packages.domain.store import Conflict, InvalidInput, NotFound


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Ref(Model):
    id: Id
    revision: int = Field(default=1, ge=1, strict=True)
    sha256: Hash


class FileLocator(Model):
    kind: Literal['FILE']


class JSONLocator(Model):
    kind: Literal['JSON_POINTER']
    pointer: str = Field(min_length=1, max_length=2048, pattern=r'^/')


class TextLocator(Model):
    kind: Literal['TEXT_RANGE']
    char_start: int = Field(ge=0, strict=True)
    char_end: int = Field(gt=0, strict=True)
    quote_sha256: Hash


Locator = Annotated[FileLocator | JSONLocator | TextLocator, Field(discriminator='kind')]
ChangeKind = Literal['EXPRESSION', 'DATA', 'CONSTRAINT', 'UNKNOWN']


class BindingInput(Model):
    operation_id: Id
    file_source_ref: FileSource
    locator: Locator
    proposal_ref: PlanningContextRef | None = None
    task_ids: list[Id] = Field(default_factory=list, max_length=2000)
    native_context_ref: dict | None = None

    @model_validator(mode='after')
    def context_scope(self):
        if self.task_ids and self.proposal_ref is None:
            raise ValueError('Task binding needs an exact planning proposal reference')
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError('Task IDs must be unique')
        if len(canonical(self.model_dump()).encode()) > 2 * 1024 * 1024:
            raise ValueError('Binding exceeds 2 MiB')
        return self


class FeedbackInput(Model):
    operation_id: Id
    binding_ref: Ref
    comment: str = Field(min_length=1, max_length=16000)
    mode: Literal['DRAFT', 'SUBMITTED'] = 'DRAFT'
    workbench_feedback_ref: Ref | None = None


class Scope(Model):
    workspace_id: Hash
    path: str

    @model_validator(mode='after')
    def exact_path(self):
        relative(self.path)
        return self


class Classification(Model):
    kind: ChangeKind
    origin: Literal['USER_STATEMENT', 'OPENCODE_PROPOSAL']
    rationale: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[Ref] = Field(min_length=1, max_length=64)


class Patch(Model):
    binding_ref: Ref
    proposed_value: JsonValue


class InputCondition(Model):
    binding_ref: Ref
    input_value_sha256: Hash
    condition: Literal['VALUE_CHANGED', 'ANY_FILE_VERSION_CHANGED', 'MANUAL_REVIEW']
    invalidating_kinds: list[ChangeKind] = Field(default_factory=lambda: ['EXPRESSION', 'DATA', 'CONSTRAINT', 'UNKNOWN'], min_length=1, max_length=4)
    verification_note: str = Field(min_length=1, max_length=4000)
    verification_refs: list[Ref] = Field(min_length=1, max_length=16)


class TaskContract(Model):
    task_id: Id
    inputs: list[InputCondition] = Field(min_length=1, max_length=64)


class PlanInput(Model):
    operation_id: Id
    feedback_ref: Ref
    proposal_ref: PlanningContextRef
    classification: Classification
    patches: list[Patch] = Field(min_length=1, max_length=64)
    write_scope: list[Scope] = Field(min_length=1, max_length=64)
    contracts: list[TaskContract] = Field(default_factory=list, max_length=2000)

    @model_validator(mode='after')
    def bounded_unique(self):
        if len({p.binding_ref.id for p in self.patches}) != len(self.patches):
            raise ValueError('Patch bindings must be unique')
        if len({c.task_id for c in self.contracts}) != len(self.contracts):
            raise ValueError('Task contracts must be unique')
        if len({(s.workspace_id, s.path) for s in self.write_scope}) != len(self.write_scope):
            raise ValueError('Write paths must be unique')
        if len(canonical(self.model_dump()).encode()) > 2 * 1024 * 1024:
            raise ValueError('Rework proposal exceeds 2 MiB')
        return self


class ApplyInput(Model):
    operation_id: Id
    plan_ref: Ref
    write_scope: list[Scope] = Field(min_length=1, max_length=64)
    expected_current_sources: list[FileSource] = Field(min_length=1, max_length=64)
    allow_rebase: bool = Field(default=False, strict=True)


def _parts(pointer):
    parts = pointer.split('/')[1:]
    if len(parts) > 64 or any(re.search(r'~(?![01])', p) for p in parts):
        raise InvalidInput('JSON pointer must use RFC 6901 escapes within 64 levels')
    return [p.replace('~1', '/').replace('~0', '~') for p in parts]


def _json(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise InvalidInput('Duplicate JSON keys make object feedback ambiguous')
            value[key] = item
        return value
    try:
        value = json.loads(text, object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        canonical(value)
        return value
    except (ValueError, TypeError, RecursionError):
        raise InvalidInput('Object locator requires bounded, finite JSON') from None


def json_value_span(text, pointer):
    """Find one existing value with stdlib decoding, without reserializing a file."""
    _json(text)
    decoder = json.JSONDecoder()
    spaces = re.compile(r'\s*')
    def skip(position):
        return spaces.match(text, position).end()
    def locate(position, parts):
        position = skip(position)
        value, end = decoder.raw_decode(text, position)
        if not parts:
            return value, position, end
        wanted, rest = parts[0], parts[1:]
        if isinstance(value, dict):
            cursor = skip(position + 1)
            while text[cursor] != '}':
                key, cursor = decoder.raw_decode(text, cursor)
                cursor = skip(cursor)
                cursor = skip(cursor + 1)  # The complete JSON parse checked ':' above.
                if key == wanted:
                    return locate(cursor, rest)
                _, cursor = decoder.raw_decode(text, cursor)
                cursor = skip(cursor)
                if text[cursor] == ',':
                    cursor = skip(cursor + 1)
        elif isinstance(value, list) and wanted.isascii() and wanted.isdecimal() and str(int(wanted)) == wanted:
            cursor = skip(position + 1)
            for index in range(len(value)):
                if index == int(wanted):
                    return locate(cursor, rest)
                _, cursor = decoder.raw_decode(text, cursor)
                cursor = skip(cursor)
                if text[cursor] == ',':
                    cursor = skip(cursor + 1)
        raise InvalidInput('JSON pointer does not name an existing value')
    try:
        return locate(0, _parts(pointer))
    except (IndexError, ValueError, RecursionError):
        raise InvalidInput('JSON pointer cannot be resolved safely') from None


def selected(text, locator):
    if locator['kind'] == 'FILE':
        return text
    if locator['kind'] == 'JSON_POINTER':
        return json_value_span(text, locator['pointer'])[0]
    start, end = locator['char_start'], locator['char_end']
    if not 0 <= start < end <= len(text) or raw_hash(text[start:end]) != locator['quote_sha256']:
        raise InvalidInput('Text range/quotation differs from this exact file revision')
    return text[start:end]


def merge_scoped(base, current, locator, before, proposed, *, allow_rebase):
    """Three-way comparison at the selected object; no fuzzy/semantic merging."""
    if base != current and not allow_rebase:
        raise Conflict('BASE_VERSION_CHANGED: explicit rebase to inspected current bytes is required')
    kind = locator['kind']
    if kind == 'JSON_POINTER':
        value, start, end = json_value_span(current, locator['pointer'])
        if digest(value) not in {digest(before), digest(proposed)}:
            raise Conflict('TARGET_CHANGED: current JSON value conflicts with the proposed value')
        return current[:start] + canonical(proposed) + current[end:]
    if not isinstance(proposed, str) or '\x00' in proposed:
        raise InvalidInput('Text and file patches require a UTF-8 string without NUL')
    if kind == 'FILE':
        if base != current and current != proposed:
            raise Conflict('TARGET_CHANGED: whole-file patch cannot retain concurrent changes')
        return proposed
    start, end = locator['char_start'], locator['char_end']
    if base != current:
        if base.count(before) != 1 or current.count(before) != 1:
            raise Conflict('TARGET_CHANGED_OR_AMBIGUOUS: selected quotation is no longer uniquely unchanged')
        start = current.index(before)
        end = start + len(before)
    if current[start:end] != before:
        raise Conflict('TARGET_CHANGED: selected text was modified')
    return current[:start] + proposed + current[end:]


class FeedbackRework:
    def __init__(self, store, workspaces, planning, *, workbench=None):
        self.store, self.workspaces, self.planning = store, workspaces, planning
        self.workbench = workbench

    @staticmethod
    def _view(row):
        payload = json.loads(row['payload'])
        if digest(payload) != row['sha256']:
            raise Conflict('Feedback/rework record integrity failed')
        return {'record_ref': {'id': row['id'], 'revision': 1, 'sha256': row['sha256']},
            'kind': row['kind'], 'payload': payload, 'created_at': row['created_at']}

    def get(self, pid, ident, kind=None):
        self.store.project(pid)
        with self.store.connect() as c:
            row = c.execute('SELECT * FROM feedback_rework_records WHERE id=? AND project_id=?', (ident, pid)).fetchone()
        if row is None or (kind and row['kind'] != kind):
            raise NotFound('Feedback/rework object does not exist in this project')
        return self._view(row)

    def resolve(self, pid, ref, kind):
        value = self.get(pid, ref['id'], kind)
        if value['record_ref'] != ref:
            raise Conflict('Feedback/rework reference differs from immutable record')
        return value['payload']

    @staticmethod
    def _replay(c, pid, op, request_hash, kind):
        row = c.execute('SELECT * FROM feedback_rework_operations WHERE project_id=? AND operation_id=?', (pid, op)).fetchone()
        if row is None:
            return None
        if row['request_sha256'] != request_hash or row['kind'] != kind:
            raise Conflict('IDEMPOTENCY_CONFLICT: operation binds another request')
        if row['status'] == 'PROCESSING':
            return {'status': 'PROCESSING', 'operation_id': op,
                'message': 'Inspect or resume this exact operation; file writes may have occurred.'}
        result = json.loads(row['result'])
        if digest(result) != row['result_sha256']:
            raise Conflict('Feedback/rework operation receipt integrity failed')
        return result

    def _old(self, pid, body, kind):
        self.store.project(pid)
        request_hash = digest(body.model_dump())
        with self.store.connect() as c:
            value = self._replay(c, pid, body.operation_id, request_hash, kind)
        return request_hash, value

    def _save(self, pid, body, kind, payload):
        request_hash = digest(body.model_dump())
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            replay = self._replay(c, pid, body.operation_id, request_hash, kind)
            if replay and replay.get('status') != 'PROCESSING':
                return replay
            ident = uuid.uuid4().hex
            payload = {'schema_version': 'eeas.feedback-rework.v1', 'id': ident, 'project_id': pid,
                'created_at': stamp(), **payload}
            hashed = digest(payload)
            c.execute('INSERT INTO feedback_rework_records VALUES (?,?,?,?,?,?)',
                (ident, pid, kind, canonical(payload), hashed, payload['created_at']))
            key = {'BINDING': 'binding_ref', 'FEEDBACK': 'feedback_ref', 'PLAN': 'plan_ref', 'APPLY': 'receipt_ref'}[kind]
            result = {key: {'id': ident, 'revision': 1, 'sha256': hashed}, 'payload': payload,
                'status': payload.get('status', 'RECORDED'), 'native_requested': False}
            if replay:
                c.execute('UPDATE feedback_rework_operations SET status=?,record_id=?,result=?,result_sha256=? WHERE project_id=? AND operation_id=?',
                    ('COMPLETED', ident, canonical(result), digest(result), pid, body.operation_id))
            else:
                c.execute('INSERT INTO feedback_rework_operations VALUES (?,?,?,?,?,?,?,?)',
                    (pid, body.operation_id, kind, request_hash, 'COMPLETED', ident, canonical(result), digest(result)))
        return result

    def _file(self, pid, ref):
        if ref['project_id'] != pid:
            raise InvalidInput('FileSource belongs to another project')
        read = self.workspaces.read(pid, ref['workspace_id'], ref['path'], ref['revision'])
        if read['source_ref'] != ref:
            raise Conflict('FileSource binding/version/SHA does not match frozen bytes')
        if read['encoding'] != 'utf-8' or read['byte_length'] > MAX_EDIT_BYTES:
            raise InvalidInput('Rework supports exact UTF-8 text files up to 1 MiB')
        return read

    def _proposal(self, pid, ref, *, current=True):
        value = self.planning.get(pid, ref['id'], ref['revision'])
        if value['sha256'] != ref['sha256'] or (current and value['current_revision'] != ref['revision']):
            raise Conflict('Planning proposal changed; inspect the current version')
        return value

    def _observation(self, pid, binding):
        ref = binding['file_source_ref']
        try:
            current = self.workspaces.read(pid, ref['workspace_id'], ref['path'])
            if current['encoding'] != 'utf-8' or current['byte_length'] > MAX_EDIT_BYTES:
                return {'status': 'UNKNOWN', 'reason': 'Current input is outside the qualified text bound'}
            locator = binding['locator']
            if locator['kind'] == 'TEXT_RANGE' and current['source_ref'] != ref:
                quote = binding['selected_value']
                base = self._file(pid, ref)['content']
                if base.count(quote) != 1 or current['content'].count(quote) != 1:
                    return {'status': 'UNKNOWN', 'current_source_ref': current['source_ref'],
                        'reason': 'Changed text input cannot be located uniquely'}
                value = quote
            else:
                value = selected(current['content'], locator)
            return {'status': 'OBSERVED', 'current_source_ref': current['source_ref'],
                'value_sha256': digest(value), 'observed_at': stamp()}
        except (Conflict, InvalidInput, NotFound, OSError) as error:
            return {'status': 'UNKNOWN', 'reason': str(error)}

    def bind(self, pid, body):
        _, old = self._old(pid, body, 'BINDING')
        if old:
            return old
        read = self._file(pid, body.file_source_ref.model_dump())
        if not read['current']:
            raise Conflict('Feedback binding needs an inspected current file revision')
        if body.proposal_ref:
            proposal = self._proposal(pid, body.proposal_ref.model_dump())
            if not set(body.task_ids) <= {n['id'] for n in proposal['payload']['candidate']['nodes']}:
                raise InvalidInput('Task binding names missing planning nodes')
        locator = body.locator.model_dump()
        value = selected(read['content'], locator)
        return self._save(pid, body, 'BINDING', {**body.model_dump(exclude={'operation_id'}),
            'selected_value': value, 'selected_value_sha256': digest(value),
            'native_context_status': 'ATTACHED_NOT_QUALIFIED' if body.native_context_ref else 'NOT_ATTACHED',
            'native_context_grants_file_authority': False})

    def bind_historical(self, pid, body):
        """Bind verified snapshot bytes to confirmed tasks without changing their baseline.

        This entry admits a past immutable FileSource; subsequent merge and CAS
        still compare against an independently inspected current source.
        """
        if body.proposal_ref is None or not body.task_ids:
            raise InvalidInput('Historical binding needs an exact confirmed proposal and task IDs')
        proposal = self._proposal(pid, body.proposal_ref.model_dump())
        if proposal['payload']['status'] != 'CONFIRMED':
            raise Conflict('Historical task binding requires a confirmed proposal')
        tasks = {n['id'] for n in proposal['payload']['candidate']['nodes'] if n['kind'] == 'TASK'}
        if not set(body.task_ids) <= tasks:
            raise InvalidInput('Historical binding must name actual confirmed task nodes')
        _, old = self._old(pid, body, 'BINDING')
        if old:
            return old
        read = self._file(pid, body.file_source_ref.model_dump())
        locator = body.locator.model_dump()
        value = selected(read['content'], locator)
        return self._save(pid, body, 'BINDING', {**body.model_dump(exclude={'operation_id'}),
            'selected_value': value, 'selected_value_sha256': digest(value),
            'binding_policy': 'VERIFIED_IMMUTABLE_SNAPSHOT',
            'source_status_at_binding': 'CURRENT' if read['current'] else 'STALE',
            'native_context_status': 'ATTACHED_NOT_QUALIFIED' if body.native_context_ref else 'NOT_ATTACHED',
            'native_context_grants_file_authority': False})

    def feedback(self, pid, body):
        _, old = self._old(pid, body, 'FEEDBACK')
        if old:
            return old
        binding = self.resolve(pid, body.binding_ref.model_dump(), 'BINDING')
        read = self._file(pid, binding['file_source_ref'])  # Historical context is never silently rebound.
        if body.workbench_feedback_ref:
            if self.workbench is None:
                raise Conflict('Linked workbench feedback authority is unavailable')
            value = self.workbench.get(pid, body.workbench_feedback_ref.id)
            if value['feedback_ref'] != body.workbench_feedback_ref.model_dump():
                raise Conflict('Linked workbench feedback reference changed')
        return self._save(pid, body, 'FEEDBACK', {**body.model_dump(exclude={'operation_id'}),
            'status': body.mode, 'dispatch_status': 'NOT_DISPATCHED',
            'source_status_at_record': 'CURRENT' if read['current'] else 'STALE_REVIEW_REQUIRED',
            'binding_file_source_ref': binding['file_source_ref']})

    @staticmethod
    def _scope(scopes):
        return {(s['workspace_id'], s['path']) for s in scopes}

    def plan(self, pid, body):
        _, old = self._old(pid, body, 'PLAN')
        if old:
            return old
        feedback = self.resolve(pid, body.feedback_ref.model_dump(), 'FEEDBACK')
        proposal = self._proposal(pid, body.proposal_ref.model_dump())
        nodes = proposal['payload']['candidate']['nodes']
        ids = {n['id'] for n in nodes}
        if not {c.task_id for c in body.contracts} <= ids:
            raise InvalidInput('Contracts must name real nodes in the exact planning proposal')
        bindings = {}
        refs = [feedback['binding_ref'], *[p.binding_ref.model_dump() for p in body.patches],
            *[r.model_dump() for r in body.classification.evidence_refs],
            *[i.binding_ref.model_dump() for c in body.contracts for i in c.inputs],
            *[r.model_dump() for c in body.contracts for i in c.inputs for r in i.verification_refs]]
        for ref in refs:
            binding = self.resolve(pid, ref, 'BINDING')
            self._file(pid, binding['file_source_ref'])
            if binding['proposal_ref'] and binding['proposal_ref'] != body.proposal_ref.model_dump():
                raise InvalidInput('Object binding belongs to a different planning proposal version')
            bindings[ref['id']] = binding
        paths = set()
        for patch in body.patches:
            binding = bindings[patch.binding_ref.id]
            source = binding['file_source_ref']
            paths.add((source['workspace_id'], source['path']))
            if binding['locator']['kind'] != 'JSON_POINTER' and not isinstance(patch.proposed_value, str):
                raise InvalidInput('Text/file patch requires a string')
            if len(canonical(patch.proposed_value).encode()) > MAX_EDIT_BYTES:
                raise InvalidInput('Patch value exceeds the local edit bound')
        if paths != self._scope([s.model_dump() for s in body.write_scope]):
            raise InvalidInput('Write scope must exactly name patched files; no extra paths')
        self._nonoverlap(body.patches, bindings)
        input_ids = {i.binding_ref.id for c in body.contracts for i in c.inputs}
        observations = {ident: self._observation(pid, bindings[ident]) for ident in input_ids}
        impact = self._impact(body, bindings, nodes, observations)
        return self._save(pid, body, 'PLAN', {**body.model_dump(exclude={'operation_id'}),
            'status': 'PROPOSED', 'classification_status': 'PROPOSAL_NOT_SEMANTICALLY_VERIFIED',
            'classification_provenance_status': 'CALLER_DECLARED_NOT_RUNTIME_ATTESTED',
            'confirmation_preservation': {'proposal_sha256': proposal['sha256'],
                'status': proposal['payload']['status'], 'confirmation': proposal['payload'].get('confirmation'),
                'behavior': 'Original confirmed proposal is immutable; affected validity is a separate review overlay.'},
            'impact': impact, 'unknown_impact': ['Undeclared dependencies and classification correctness are not proven.'],
            'input_observations': observations,
            'model_calls': 0, 'execution_started': False})

    @staticmethod
    def _nonoverlap(patches, bindings):
        groups = {}
        for patch in patches:
            binding = bindings[patch.binding_ref.id]
            source = binding['file_source_ref']
            groups.setdefault((source['workspace_id'], source['path']), []).append(binding)
        for values in groups.values():
            if len({digest(v['file_source_ref']) for v in values}) != 1:
                raise InvalidInput('Patches to one file need the same baseline FileSource')
            for index, a in enumerate(values):
                for b in values[index+1:]:
                    x, y = a['locator'], b['locator']
                    if x['kind'] != y['kind'] or x['kind'] == 'FILE':
                        raise InvalidInput('Overlapping or mixed locator patches are refused')
                    if x['kind'] == 'TEXT_RANGE':
                        overlap = max(x['char_start'], y['char_start']) < min(x['char_end'], y['char_end'])
                    else:
                        p, q = _parts(x['pointer']), _parts(y['pointer'])
                        overlap = p[:len(q)] == q or q[:len(p)] == p
                    if overlap:
                        raise InvalidInput('Patch object scopes overlap')

    @staticmethod
    def _impact(body, bindings, nodes, observations):
        changed = {p.binding_ref.id: p for p in body.patches}
        def object_key(binding):
            source = binding['file_source_ref']
            locator = binding['locator']
            identity = _parts(locator['pointer']) if locator['kind'] == 'JSON_POINTER' else locator
            return source['workspace_id'], source['path'], locator['kind'], digest(identity)
        changed_objects = {object_key(bindings[p.binding_ref.id]): p for p in body.patches}
        changed_files = {(bindings[p.binding_ref.id]['file_source_ref']['workspace_id'],
            bindings[p.binding_ref.id]['file_source_ref']['path']) for p in body.patches}
        contracts = {c.task_id: c for c in body.contracts}
        states = {}
        for node in nodes:
            reasons = []
            contract = contracts.get(node['id'])
            state = 'RETAINED_BY_DECLARED_CONDITION' if contract else 'UNKNOWN'
            if contract:
                for item in contract.inputs:
                    binding = bindings[item.binding_ref.id]
                    if item.input_value_sha256 != binding['selected_value_sha256']:
                        raise InvalidInput('Task input fingerprint differs from its exact selected object')
                    patch = changed.get(item.binding_ref.id) or changed_objects.get(object_key(binding))
                    source = binding['file_source_ref']
                    file_changed = (source['workspace_id'], source['path']) in changed_files
                    observed = observations[item.binding_ref.id]
                    if item.condition == 'MANUAL_REVIEW':
                        if state != 'AFFECTED':
                            state = 'UNKNOWN'
                        reasons.append('Input requires explicit manual assessment')
                    elif observed['status'] == 'UNKNOWN':
                        if state != 'AFFECTED':
                            state = 'UNKNOWN'
                        reasons.append('Current input cannot be verified: ' + observed['reason'])
                    elif ((item.condition == 'VALUE_CHANGED' and observed['value_sha256'] != item.input_value_sha256) or
                            (item.condition == 'ANY_FILE_VERSION_CHANGED' and observed['current_source_ref'] != source)):
                        state = 'AFFECTED'
                        reasons.append('Observed current input/version differs from the consumed validation basis')
                    elif (patch or (file_changed and item.condition == 'ANY_FILE_VERSION_CHANGED')) and body.classification.kind == 'UNKNOWN':
                        if state != 'AFFECTED':
                            state = 'UNKNOWN'
                        reasons.append('Change classification remains unknown')
                    elif body.classification.kind in item.invalidating_kinds:
                        if (file_changed and item.condition == 'ANY_FILE_VERSION_CHANGED') or (patch and digest(patch.proposed_value) != item.input_value_sha256):
                            state = 'AFFECTED'
                            reasons.append('Declared input/version validity condition fails for the proposed change')
                    else:
                        reasons.append('Declared input condition is unchanged or excludes this proposed change kind')
            else:
                reasons.append('No input contract supplied; absence of a link does not establish independence')
            states[node['id']] = {'task_id': node['id'], 'status': state, 'reasons': reasons,
                'previous_confirmation_preserved': True}
        # Follow the real planning DAG, without changing its stored approval.
        pending = {n['id']: n for n in nodes}
        while pending:
            ready = [n for n in pending.values() if not set(n['depends_on']) & pending.keys()]
            if not ready:
                raise Conflict('Stored planning dependencies are not acyclic')
            for node in ready:
                upstream = {states[d]['status'] for d in node['depends_on']}
                if 'AFFECTED' in upstream:
                    states[node['id']]['status'] = 'AFFECTED'
                    states[node['id']]['reasons'].append('A declared upstream planning task requires review')
                elif 'UNKNOWN' in upstream and states[node['id']]['status'] != 'AFFECTED':
                    states[node['id']]['status'] = 'UNKNOWN'
                    states[node['id']]['reasons'].append('An upstream impact remains unknown')
                pending.pop(node['id'])
        rows = list(states.values())
        counts = {state: sum(r['status'] == state for r in rows) for state in ('AFFECTED', 'UNKNOWN', 'RETAINED_BY_DECLARED_CONDITION')}
        baseline = {c.task_id for c in body.contracts if any(
            (bindings[i.binding_ref.id]['file_source_ref']['workspace_id'],
             bindings[i.binding_ref.id]['file_source_ref']['path']) in changed_files for i in c.inputs)}
        while True:
            expanded = baseline | {n['id'] for n in nodes if set(n['depends_on']) & baseline}
            if expanded == baseline:
                break
            baseline = expanded
        return {'tasks': rows, 'counts': counts, 'baseline_all_tasks': len(nodes),
            'known_dependency_baseline_tasks': len(baseline),
            'known_dependency_baseline_rule': 'Patched file input links plus real planning dependency closure, without kind/value conditions.',
            'selected_rework_tasks': counts['AFFECTED'], 'pending_assessment_tasks': counts['UNKNOWN'],
            'comparison_scope': 'Declared original-fixture contracts; no semantic or product innovation superiority claim.'}

    def apply(self, pid, body):
        request_hash, old = self._old(pid, body, 'APPLY')
        if old and old.get('status') != 'PROCESSING':
            return old
        plan = self.resolve(pid, body.plan_ref.model_dump(), 'PLAN')
        proposed_scope = self._scope(plan['write_scope'])
        supplied_scope = self._scope([s.model_dump() for s in body.write_scope])
        sources = {(s.workspace_id, s.path): s.model_dump() for s in body.expected_current_sources}
        if (supplied_scope != proposed_scope or set(sources) != proposed_scope or
                len(sources) != len(body.expected_current_sources) or len(supplied_scope) != len(body.write_scope)):
            raise InvalidInput('Apply needs the exact planned write paths and one inspected current FileSource per path')
        self._proposal(pid, plan['proposal_ref'])
        groups = {}
        for patch in plan['patches']:
            binding = self.resolve(pid, patch['binding_ref'], 'BINDING')
            source = binding['file_source_ref']
            groups.setdefault((source['workspace_id'], source['path']), []).append((binding, patch))
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            replay = self._replay(c, pid, body.operation_id, request_hash, 'APPLY')
            if replay and replay.get('status') != 'PROCESSING':
                return replay
            if replay is None:
                c.execute('INSERT INTO feedback_rework_operations VALUES (?,?,?,?,?,?,?,?)',
                    (pid, body.operation_id, 'APPLY', request_hash, 'PROCESSING', body.plan_ref.id, None, None))
        outcomes = []
        for key, patches in groups.items():
            wid, path = key
            local_op = 'rework-' + digest([pid, body.operation_id, wid, path])[:56]
            outcome = {'workspace_id': wid, 'path': path, 'local_operation_id': local_op,
                'base_file_source_ref': patches[0][0]['file_source_ref'], 'expected_current_source_ref': sources[key],
                'patch_binding_ids': [b['id'] for b, _ in patches]}
            try:
                try:
                    prior = self.workspaces.operation(pid, wid, local_op)
                except NotFound:
                    prior = None
                if prior and prior['status'] == 'APPLIED':
                    check = self.workspaces.check_sources(pid, [FileSource.model_validate(prior['result']['source_ref'])])[0]
                    outcomes.append({**outcome, **prior['result'], 'status': 'APPLIED', 'recovered': True,
                        'result_source_status_at_receipt': check['status'],
                        'current': check['status'] == 'CURRENT'})
                    continue
                if prior and prior['status'] == 'UNKNOWN_OUTCOME':
                    raise Conflict('UNKNOWN_OUTCOME: interrupted local edit requires inspection')
                current = self._file(pid, sources[key])
                if not current['current']:
                    raise Conflict('SOURCE_DRIFT: inspected current FileSource is no longer current')
                base = self._file(pid, patches[0][0]['file_source_ref'])['content']
                merged = current['content']
                # Descending ranges preserve same-baseline text offsets.
                patches = sorted(patches, key=lambda value: value[0]['locator'].get('char_start', 0), reverse=True)
                for binding, patch in patches:
                    merge_base = base if base != current['content'] else merged
                    merged = merge_scoped(merge_base, merged, binding['locator'], binding['selected_value'],
                        patch['proposed_value'], allow_rebase=body.allow_rebase)
                if len(merged.encode('utf-8')) > MAX_EDIT_BYTES:
                    raise InvalidInput('Merged file exceeds the local edit limit')
                if merged == current['content']:
                    outcomes.append({**outcome, 'status': 'UNCHANGED', 'source_ref': sources[key]})
                    continue
                saved = self.workspaces.edit(pid, wid, FileEditInput(operation_id=local_op, path=path,
                    expected_revision=sources[key]['revision'], expected_sha256=sources[key]['sha256'], content=merged))
                outcomes.append({**outcome, 'status': 'APPLIED', **saved,
                    'outside_selected_object_bytes_preserved': True,
                    'rebase_used': base != current['content']})
            except (Conflict, InvalidInput, NotFound, OSError) as error:
                uncertain = 'UNKNOWN_OUTCOME' in str(error)
                try:
                    inspection = self.workspaces.operation(pid, wid, local_op)
                    uncertain = uncertain or inspection['status'] == 'UNKNOWN_OUTCOME'
                except (NotFound, Conflict, InvalidInput, OSError):
                    inspection = None
                outcomes.append({**outcome, 'status': 'UNKNOWN_OUTCOME' if uncertain else 'CONFLICT',
                    'reason': str(error), 'local_operation_inspection': inspection,
                    'file_written_by_this_step': None if uncertain else False})
        count = sum(o['status'] == 'APPLIED' for o in outcomes)
        conflicts = sum(o['status'] in {'CONFLICT', 'UNKNOWN_OUTCOME'} for o in outcomes)
        uncertain_count = sum(o['status'] == 'UNKNOWN_OUTCOME' for o in outcomes)
        status = 'PARTIAL' if count and conflicts else 'UNKNOWN_OUTCOME' if uncertain_count else 'CONFLICT' if conflicts else 'APPLIED'
        return self._save(pid, body, 'APPLY', {'status': status, 'plan_ref': body.plan_ref.model_dump(),
            'write_scope': plan['write_scope'], 'outcomes': outcomes, 'modified_file_count': count,
            'unknown_outcome_file_count': uncertain_count,
            'baseline_all_scoped_files': len(groups), 'impact': plan['impact'],
            'verification_status': 'INCOMPLETE_WRITE_OUTCOME_SEMANTIC_REVALIDATION_PENDING' if uncertain_count else
                'PARTIAL_OR_CONFLICTING_WRITES_SEMANTIC_REVALIDATION_PENDING' if conflicts else
                'STRUCTURAL_WRITE_CHECKED_SEMANTIC_REVALIDATION_PENDING',
            'confirmation_preservation': plan['confirmation_preservation'],
            'unknown_impact': plan['unknown_impact'],
            'concurrency_boundary': 'Inspected versions and existing LocalWorkspaces CAS; native-editor cross-process compare/replace race qualification is separate.',
            'semantic_correctness_confirmed': False, 'model_calls': 0})

    def listing(self, pid, kind, binding_id=None):
        self.store.project(pid)
        with self.store.connect() as c:
            rows = c.execute('SELECT * FROM feedback_rework_records WHERE project_id=? AND kind=? ORDER BY rowid', (pid, kind)).fetchall()
        values = [self._view(row) for row in rows]
        return [v for v in values if not binding_id or v['payload'].get('binding_ref', {}).get('id') == binding_id]

    def operation(self, pid, op):
        self.store.project(pid)
        with self.store.connect() as c:
            row = c.execute('SELECT * FROM feedback_rework_operations WHERE project_id=? AND operation_id=?', (pid, op)).fetchone()
        if row is None:
            raise NotFound('Feedback/rework operation does not exist in this project')
        if row['status'] == 'COMPLETED':
            result = json.loads(row['result'])
            if digest(result) != row['result_sha256']:
                raise Conflict('Feedback/rework receipt integrity failed')
            return result
        value = {'status': 'PROCESSING', 'operation_id': op, 'request_sha256': row['request_sha256'],
            'file_operations': [], 'message': 'Inspect actual file operations; interrupted writes are not automatically repeated.'}
        if row['kind'] == 'APPLY' and row['record_id']:
            plan = self.get(pid, row['record_id'], 'PLAN')
            value['plan_ref'] = plan['record_ref']
            for scope in plan['payload']['write_scope']:
                local_op = 'rework-' + digest([pid, op, scope['workspace_id'], scope['path']])[:56]
                try:
                    status = self.workspaces.operation(pid, scope['workspace_id'], local_op)
                except NotFound:
                    status = {'status': 'NOT_STARTED'}
                value['file_operations'].append({**scope, 'operation': status})
        return value

    def artifact(self, pid, ident):
        value = self.get(pid, ident)
        if value['kind'] not in {'PLAN', 'APPLY'}:
            raise InvalidInput('Only rework plans and checking receipts have report artifacts')
        payload = value['payload']
        lines = ['# 局部返工检查回执' if value['kind'] == 'APPLY' else '# 局部返工提案', '',
            f'记录：{ident} / {value["record_ref"]["sha256"]}',
            f'状态：{payload["status"]}', '',
            '语义分类和有效条件仍是提案；本回执不替代语义、原生软件或 Windows 资格验收。', '',
            '```json', json.dumps(payload, ensure_ascii=False, indent=2), '```', '']
        raw = '\n'.join(lines).encode('utf-8')
        path = resolve_data_path(f'feedback-rework/{pid}/artifacts/{ident}.md', self.workspaces.data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('xb') as stream:
                stream.write(raw)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise Conflict('Rework artifact bytes differ from the immutable ledger')
        return {'artifact_id': ident, 'path': str(path), 'sha256': raw_hash(raw.decode()),
            'media_type': 'text/markdown', 'byte_length': len(raw), 'record_ref': value['record_ref'],
            'download_url': f'/api/projects/{pid}/feedback-rework/records/{ident}/artifact'}
