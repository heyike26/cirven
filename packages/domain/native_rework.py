"""Persist native feedback lineage and reuse the existing scoped repair engine.

Source equality establishes provenance, not semantic correctness. A prior
confirmed object mapping or one exact confirmed task anchor can continue
without another mapping decision; ambiguous relationships remain pending.
"""
from typing import Literal

from pydantic import Field, JsonValue, model_validator, model_serializer, field_validator

from packages.domain.discovery_schema import RevisionRef
from packages.domain.feedback_rework import (
    ApplyInput, BindingInput, Classification, FeedbackInput, Locator, Patch,
    PlanInput, Scope, TaskContract, _parts, _json, json_value_span, selected,
)
from packages.domain.local_materials import LocalMaterials
from packages.domain.local_workspaces import FileSource, MAX_EDIT_BYTES, canonical, digest, sha
from packages.domain.paths import resolve_data_path
from packages.domain.planning_schema import PlanningContextRef
from packages.domain.questions import Id, RequestModel, stamp
from packages.domain.store import Conflict, InvalidInput, NotFound


class MappingInput(RequestModel):
    operation_id: Id
    native_feedback_ref: RevisionRef
    proposal_ref: PlanningContextRef
    locator: Locator | None = None
    task_ids: list[Id] = Field(default_factory=list, max_length=2000)
    user_confirmed: bool = Field(default=False, strict=True)
    mapping_context_ref: RevisionRef | None = None
    discovery_ref: RevisionRef | None = None
    requirement_ids: list[Id] = Field(default_factory=list, max_length=2000)

    @model_validator(mode='after')
    def explicit_scope(self):
        if len(set(self.task_ids)) != len(self.task_ids) or len(set(self.requirement_ids)) != len(self.requirement_ids):
            raise ValueError('Mapped identities must be unique')
        if self.requirement_ids and self.discovery_ref is None:
            raise ValueError('Requirements need an exact discovery reference')
        if self.user_confirmed and not self.task_ids:
            raise ValueError('An explicit mapping decision needs selected task IDs')
        return self


class NativePlanInput(RequestModel):
    operation_id: Id
    mapping_ref: RevisionRef
    classification_kind: Literal['EXPRESSION', 'DATA', 'CONSTRAINT', 'UNKNOWN']
    classification_origin: Literal['USER_STATEMENT', 'OPENCODE_PROPOSAL']
    rationale: str = Field(min_length=1, max_length=4000)
    proposed_value: JsonValue = None
    proposed_value_text: str | None = Field(default=None, max_length=MAX_EDIT_BYTES)
    contracts: list[TaskContract] = Field(default_factory=list, max_length=2000)

    @field_validator('*')
    @classmethod
    def original_utf8(cls, value, info):
        if info.field_name != 'proposed_value_text':
            return RequestModel.original_utf8(value)
        if value is not None:
            try: raw = value.encode('utf-8')
            except UnicodeError as error: raise ValueError('Proposal text must be valid UTF-8') from error
            if len(raw) > MAX_EDIT_BYTES or '\x00' in value:
                raise ValueError('Proposal text exceeds its UTF-8 limit or contains NUL')
        return value

    @model_validator(mode='after')
    def one_proposal_representation(self):
        numeric = 'proposed_value' in self.model_fields_set
        original = 'proposed_value_text' in self.model_fields_set
        if numeric == original or original and self.proposed_value_text is None:
            raise ValueError('Provide exactly one proposed value or its original text')
        return self

    @model_serializer(mode='wrap')
    def original_request_shape(self, handler):
        value = handler(self)
        if 'proposed_value_text' in self.model_fields_set:
            value.pop('proposed_value', None)
        else:
            # Old PLAN operation bodies, including explicit JSON null, retain
            # exactly their original field set and request SHA.
            value.pop('proposed_value_text', None)
        return value

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        schema['properties']['proposed_value_text'] = {
            'type': 'string', 'maxLength': MAX_EDIT_BYTES, 'title': 'Proposed Value Text'}
        schema['oneOf'] = [
            {'required': ['proposed_value'], 'not': {'required': ['proposed_value_text']}},
            {'required': ['proposed_value_text'], 'not': {'required': ['proposed_value']}},
        ]
        return schema


class NativeApplyInput(RequestModel):
    operation_id: Id
    plan_ref: RevisionRef
    expected_current_sources: list[FileSource] = Field(min_length=1, max_length=64)
    allow_rebase: bool = Field(default=False, strict=True)


class NativeContextInput(RequestModel):
    file_source_ref: FileSource


class NativeRework:
    def __init__(self, store, workspaces, native_feedback, rework, planning, discovery=None):
        self.store, self.ws, self.native, self.rework = store, workspaces, native_feedback, rework
        self.planning, self.discovery = planning, discovery
        self.materials = LocalMaterials(workspaces)

    def _path(self, pid, *parts):
        self.store.project(pid)
        return resolve_data_path('/'.join(('native-rework', pid, *parts)), self.ws.data_dir)

    def _read(self, path):
        wrapped = self.ws._json(path)
        value = wrapped.get('payload')
        if not isinstance(value, dict) or digest(value) != wrapped.get('sha256'):
            raise Conflict('Native rework record integrity failed')
        return value

    def _put(self, path, payload):
        record = {'payload': payload, 'sha256': digest(payload)}
        if len(canonical(record).encode()) > 2 * 1024 * 1024:
            raise InvalidInput('Native rework record exceeds 2 MiB')
        if path.exists():
            if self._read(path) != payload:
                raise Conflict('Immutable native rework record changed')
            return
        self.ws._write(path, record)

    def _run(self, pid, body, kind, build):
        folder = self._path(pid, 'operations', digest(body.operation_id))
        with self.materials._lock(folder / 'lock'):
            request = {'project_id': pid, 'operation_id': body.operation_id, 'kind': kind,
                'input': body.model_dump()}
            request_sha = digest(request)
            fresh = not (folder / 'request.json').exists()
            if not fresh and self._read(folder / 'request.json')['request_sha256'] != request_sha:
                raise Conflict('IDEMPOTENCY_CONFLICT: native rework operation binds another input')
            if fresh:
                self._put(folder / 'request.json', {**request, 'request_sha256': request_sha, 'created_at': stamp()})
            if (folder / 'result.json').exists():
                return self.get(pid, self._read(folder / 'result.json')['record_id'])
            payload = build(fresh)
            if payload.get('status') == 'INCOMPLETE_REVIEW_REQUIRED':
                return payload
            ident = digest(['native-rework', pid, body.operation_id])
            value = {'schema_version': 'eeas.native-rework.v1', 'id': ident, 'revision': 1,
                'project_id': pid, 'kind': kind, 'created_at': stamp(),
                'request_sha256': request_sha, **payload}
            self._put(self._path(pid, 'records', ident + '.json'), value)
            self._put(folder / 'result.json', {'record_id': ident})
            return self.get(pid, ident)

    def _resolve(self, pid, ref, kind):
        value = self.get(pid, ref['id'])
        if value['record_ref'] != ref or value['kind'] != kind:
            raise Conflict('Native rework reference differs from its immutable record')
        return value['payload']

    def _proposal(self, pid, ref):
        value = self.planning.get(pid, ref['id'], ref['revision'])
        if value['sha256'] != ref['sha256'] or value['current_revision'] != ref['revision']:
            raise Conflict('Mapping requires the exact current task proposal version')
        if value['payload']['status'] != 'CONFIRMED':
            raise Conflict('Mapping requires actual confirmed task nodes')
        return value

    @staticmethod
    def _file_identity(ref):
        return tuple(ref[k] for k in ('project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path'))

    @staticmethod
    def _object_matches(a, b, a_value, b_value):
        if a['kind'] != b['kind']:
            return False
        if a['kind'] == 'JSON_POINTER':
            return _parts(a['pointer']) == _parts(b['pointer'])
        if a['kind'] == 'FILE':
            return True
        # The caller also checks a verified value lineage and unique quotes
        # in both snapshots; character offsets alone never identify a moved span.
        return True

    def _known_values(self, pid, mapping):
        values = {mapping['selected_value_sha256']}
        for value in self.listing(pid, 'APPLY'):
            applied = value['payload']
            plan = self._resolve(pid, applied['plan_ref'], 'PLAN')
            if plan['mapping_ref']['id'] != mapping['id']:
                continue
            engine = applied['engine_receipt']['payload']
            for outcome in engine['outcomes']:
                if outcome['status'] in {'APPLIED', 'UNCHANGED'} and outcome.get('source_ref'):
                    # The actual engine receipt proves the scoped before/after write;
                    # the proposed value is not admitted from an unfinished plan.
                    self.rework._file(pid, outcome['source_ref'])
                    values.add(digest(plan['proposed_value']))
        return values

    def _prior(self, pid, body, locator, source, value):
        rows = self.listing(pid, 'MAPPING')
        if body.mapping_context_ref:
            rows = [self.get(pid, body.mapping_context_ref.id)]
            if rows[0]['record_ref'] != body.mapping_context_ref.model_dump():
                raise Conflict('Explicit mapping context reference changed')
        candidates = []
        for row in rows:
            p = row['payload']
            if (p.get('status') != 'MAPPED' or p['proposal_ref'] != body.proposal_ref.model_dump() or
                    self._file_identity(p['baseline_file_source_ref']) != self._file_identity(source)):
                continue
            previous = self.rework.resolve(pid, p['binding_ref'], 'BINDING')
            if not self._object_matches(p['locator'], locator, previous['selected_value'], value):
                continue
            if digest(value) not in self._known_values(pid, p):
                continue
            if locator['kind'] == 'TEXT_RANGE':
                before = self.rework._file(pid, p['baseline_file_source_ref'])['content']
                after = self.rework._file(pid, source)['content']
                if before.count(previous['selected_value']) != 1 or after.count(value) != 1:
                    continue
            candidates.append({'task_ids': p['task_ids'], 'mapping_ref': row['record_ref'],
                'discovery_ref': p.get('discovery_ref'), 'requirement_ids': p.get('requirement_ids', [])})
        return candidates

    @staticmethod
    def _anchor_tasks(proposal, source, locator, text):
        if locator['kind'] == 'FILE':
            span = None
        elif locator['kind'] == 'JSON_POINTER':
            _, start, end = json_value_span(text, locator['pointer'])
            span = (start, end)
        else:
            span = (locator['char_start'], locator['char_end'])
        documents = {d['document_id']: d for d in proposal['payload']['source_receipt']['documents']
            if d.get('local_file_source_ref') == source}
        tasks = []
        for n in proposal['payload']['candidate']['nodes']:
            if n['kind'] != 'TASK':
                continue
            for anchor in n['source_anchors']:
                doc = documents.get(anchor['document_id'])
                if doc is None or anchor['revision'] != doc['revision'] or anchor['raw_sha256'] != source['sha256']:
                    continue
                if span is None or span == (anchor['char_start'], anchor['char_end']):
                    tasks.append(n['id'])
                    break
        return sorted(set(tasks))

    def _export_producers(self, pid, proposal, source, text):
        """Join completed, selected-workspace exports to exact harness metadata.

        A prompt name alone establishes no relationship. The actual feedback
        snapshot must match a completed producer's bytes and the current
        confirmed harness. Human edits after that snapshot remain the repair
        engine's three-way comparison responsibility.
        """
        workspace_ref = {'id': source['workspace_id'], 'revision': source['binding_revision'],
            'binding_sha256': source['binding_sha256']}
        proposal_ref = {k: proposal[k] for k in ('id', 'revision', 'sha256')}
        records, unreadable = [], []
        folder = self.ws._data(pid, source['workspace_id'], 'planning-exports')
        for path in sorted(folder.glob('*/result.json')):
            try:
                result = self.ws._json(path)
            except (Conflict, OSError) as error:
                unreadable.append({'operation_digest': path.parent.name, 'reason': str(error)})
                continue
            if result.get('workspace_ref') != workspace_ref:
                continue
            files = result.get('files', [])
            if not isinstance(files, list):
                unreadable.append({'operation_digest': path.parent.name, 'reason': 'Invalid export file records'})
                continue
            matches = [f for f in files if isinstance(f, dict) and f.get('path') == source['path']]
            if not matches:
                continue
            evidence = {'operation_digest': path.parent.name, 'workspace_ref': workspace_ref,
                'proposal_ref': result.get('proposal_ref'), 'result_payload_sha256': digest(result),
                'file': matches[0]}
            reason = None
            try:
                claim = self.ws._json(path.parent / 'request.json')
                request = claim['request']
                evidence['request_sha256'] = claim['request_sha256']
                if digest(request) != claim['request_sha256'] or request.get('workspace_ref') != workspace_ref:
                    raise Conflict('Export request integrity or workspace binding differs')
                if (result.get('status') != 'EXPORTED_CONFIRMED_PLAN' or result.get('execution_started') is not False or
                        result.get('proposal_ref') != proposal_ref or request.get('proposal_id') != proposal_ref['id'] or
                        request.get('expected_revision') != proposal_ref['revision'] or
                        request.get('proposal_sha256') != proposal_ref['sha256']):
                    raise Conflict('Producer differs from the exact current confirmed proposal')
                expected_directory = request['directory'] + '/' + proposal_ref['id'] + '/v' + str(
                    proposal_ref['revision']) + '-' + proposal_ref['sha256'][:16]
                if len(matches) != 1 or result.get('version_directory') != expected_directory:
                    raise Conflict('Export output identity is ambiguous or changed')
                if matches[0].get('outcome') not in {'WRITTEN', 'EXISTING_IDENTICAL'}:
                    raise Conflict('Producer does not record a completed output')
                evidence['version_directory'] = expected_directory
            except (Conflict, KeyError, TypeError, OSError) as error:
                reason = str(error)
            records.append({'evidence': evidence, 'reason': reason})
        check = {'status': 'NO_TASK_PRODUCER', 'path_record_count': len(records),
            'unreadable_records': unreadable, 'candidates': []}
        if unreadable:
            return {**check, 'status': 'UNVERIFIED_EXPORT_RECORD'}
        if not records:
            return check
        if len(records) != 1:
            return {**check, 'status': 'MULTIPLE_PRODUCERS',
                'candidates': [r['evidence'] for r in records]}
        record = records[0]
        check['candidates'] = [record['evidence']]
        if record['reason'] or proposal['review_status'] != 'CURRENT':
            return {**check, 'status': 'PRODUCER_VERSION_OR_SOURCE_DRIFT',
                'reason': record['reason'] or 'Confirmed proposal source needs review'}
        harness = self.planning.harness(pid, proposal_ref['id'], proposal_ref['revision'])
        if harness['proposal_ref'] != proposal_ref or harness['status'] != 'CONFIRMED_PLAN' or harness['review_status'] != 'CURRENT':
            return {**check, 'status': 'PRODUCER_VERSION_OR_SOURCE_DRIFT'}
        tasks = {n['id'] for n in proposal['payload']['candidate']['nodes'] if n['kind'] == 'TASK'}
        evidence = record['evidence']
        prompts = [p for p in harness['prompts'] if p['node_id'] in tasks and
            evidence['version_directory'] + '/' + p['filename'] == source['path']]
        if len(prompts) != 1:
            return check
        prompt = prompts[0]
        raw = text.encode('utf-8')
        if (sha(prompt['content'].encode('utf-8')) != prompt['sha256'] or
                prompt['sha256'] != evidence['file'].get('sha256') or sha(raw) != source['sha256'] or
                source['sha256'] != prompt['sha256'] or len(raw) != evidence['file'].get('byte_length')):
            return {**check, 'status': 'PRODUCER_CONTENT_DRIFT'}
        evidence.update({'node_id': prompt['node_id'], 'prompt_filename': prompt['filename'],
            'prompt_sha256': prompt['sha256'], 'source_receipt_sha256': harness['source_receipt_sha256']})
        return {**check, 'status': 'VERIFIED_UNIQUE_TASK_PRODUCER', 'task_ids': [prompt['node_id']]}

    def _requirements(self, pid, proposal, ref, ids):
        if ref is None:
            return
        if self.discovery is None or proposal['payload'].get('discovery_ref') != ref:
            raise Conflict('Requirements mapping needs the proposal exact discovery basis')
        value = self.discovery.get(pid, ref['id'], ref['revision'])
        if value['sha256'] != ref['sha256'] or value['payload']['status'] != 'CONFIRMED':
            raise Conflict('Requirements mapping needs a confirmed discovery version')
        current = self.discovery.get(pid, ref['id'])
        fields = ('candidate', 'accepted_claim_ids', 'accepted_graph_ids', 'resolutions', 'confirmation', 'workspace_ref')
        if current['payload']['status'] != 'CONFIRMED' or any(
                current['payload'].get(k) != value['payload'].get(k) for k in fields):
            raise Conflict('Confirmed requirements changed; review the mapping before another write')
        accepted = set(value['payload']['accepted_claim_ids'])
        claims = {c['id']: c for c in value['payload']['candidate']['claims']}
        if not set(ids) <= accepted or any(claims[i]['category'] not in {'REQUIREMENT', 'CONSTRAINT'} for i in ids):
            raise InvalidInput('Requirement IDs must be actual accepted requirements or constraints')

    def map(self, pid, body):
        def build(_fresh):
            feedback = self.native.get(pid, body.native_feedback_ref.id)
            if feedback['feedback_ref'] != body.native_feedback_ref.model_dump():
                raise Conflict('Mapping needs the exact persistent native feedback reference')
            proposal = self._proposal(pid, body.proposal_ref.model_dump())
            source = feedback['payload']['baseline_file_source_ref']
            self.native._baseline(pid, FileSource.model_validate(source))
            base = {'native_feedback_ref': body.native_feedback_ref.model_dump(),
                'proposal_ref': body.proposal_ref.model_dump(), 'baseline_file_source_ref': source,
                'native_context_grants_authority': False, 'write_authorized': False,
                'semantic_correctness_confirmed': False, 'proposal_review_status_at_mapping': proposal['review_status']}
            if feedback['mutation_status'] == 'NOT_ADAPTED':
                return {**base, 'status': 'NOT_ADAPTED', 'reason': 'Binary object mutation has no qualified adapter'}
            locator = body.locator.model_dump() if body.locator else None
            selection = feedback['payload']['file_selection']
            if locator is None and feedback['payload']['selection_verification'] == 'TEXT_RANGE_HASH_VERIFIED':
                locator = selection
            if locator is None:
                return {**base, 'status': 'NEEDS_CLARIFICATION', 'reason': 'Select a precise object locator; a screen description cannot locate a file object', 'candidate_task_ids': []}
            read = self.rework._file(pid, source)
            value = selected(read['content'], locator)
            tasks = {n['id'] for n in proposal['payload']['candidate']['nodes'] if n['kind'] == 'TASK'}
            if not set(body.task_ids) <= tasks:
                raise InvalidInput('Mapping names task IDs outside this confirmed proposal')
            priors = self._prior(pid, body, locator, source, value)
            relations = {(tuple(sorted(p['task_ids'])), digest(p.get('discovery_ref')), tuple(sorted(p['requirement_ids']))) for p in priors}
            anchors = self._anchor_tasks(proposal, source, locator, read['content'])
            basis, prior_ref, producer = None, None, None
            ref, requirements, chosen = body.discovery_ref.model_dump() if body.discovery_ref else None, body.requirement_ids, body.task_ids
            if body.user_confirmed:
                basis = 'USER_CONFIRMED_OBJECT_MAPPING'
            elif len(relations) == 1:
                prior = priors[0]
                if chosen and set(chosen) != set(prior['task_ids']):
                    raise Conflict('Requested tasks differ from the existing confirmed object mapping')
                if (ref is not None and ref != prior['discovery_ref']) or (
                        requirements and set(requirements) != set(prior['requirement_ids'])):
                    raise Conflict('Requested requirements differ from the existing confirmed object mapping')
                chosen, ref, requirements = prior['task_ids'], prior['discovery_ref'], prior['requirement_ids']
                prior_ref, basis = prior['mapping_ref'], 'REUSED_CONFIRMED_OBJECT_MAPPING'
            elif not relations and len(anchors) == 1 and (not chosen or chosen == anchors):
                if not requirements:
                    chosen, basis = anchors, 'EXACT_CONFIRMED_TASK_SOURCE_ANCHOR'
            elif not relations and not anchors and not requirements:
                producer = self._export_producers(pid, proposal, source, read['content'])
                if producer['status'] == 'VERIFIED_UNIQUE_TASK_PRODUCER' and (
                        not chosen or chosen == producer['task_ids']):
                    chosen, basis = producer['task_ids'], 'EXACT_CONFIRMED_TASK_EXPORT_PRODUCER'
            producer_fields = {'producer_check': producer} if producer is not None else {}
            if basis is None:
                return {**base, **producer_fields, 'locator': locator, 'status': 'NEEDS_CLARIFICATION',
                    'reason': 'No unique verified task mapping; choose the exact tasks and confirm this relationship',
                    'candidate_task_ids': sorted(set(anchors + [t for p in priors for t in p['task_ids']]))}
            self._requirements(pid, proposal, ref, requirements)
            binding = self.rework.bind_historical(pid, BindingInput(
                operation_id='nr-bind-' + digest([pid, body.operation_id])[:56],
                file_source_ref=FileSource.model_validate(source), locator=locator,
                proposal_ref=body.proposal_ref, task_ids=chosen,
                native_context_ref=feedback['payload']['screen_annotations']['context_ref']))
            return {**base, **producer_fields, 'status': 'MAPPED', 'locator': locator, 'task_ids': chosen,
                'discovery_ref': ref, 'requirement_ids': requirements, 'binding_ref': binding['binding_ref'],
                'selected_value_sha256': digest(value), 'mapping_basis': basis, 'reused_mapping_ref': prior_ref,
                'historical_source_preserved': True, 'repeated_mapping_confirmation_required': False}
        return self._run(pid, body, 'MAPPING', build)

    def plan(self, pid, body):
        def build(_fresh):
            mapping = self._resolve(pid, body.mapping_ref.model_dump(), 'MAPPING')
            if mapping['status'] != 'MAPPED':
                raise Conflict('Resolve the pending mapping before preparing a scoped change')
            proposal = self._proposal(pid, mapping['proposal_ref'])
            self._requirements(pid, proposal, mapping.get('discovery_ref'), mapping['requirement_ids'])
            binding = self.rework.resolve(pid, mapping['binding_ref'], 'BINDING')
            proposed, original = body.proposed_value, None
            if 'proposed_value_text' in body.model_fields_set:
                raw = body.proposed_value_text
                proposed = _json(raw) if binding['locator']['kind'] == 'JSON_POINTER' else raw
                try: encoded = canonical(proposed).encode('utf-8')
                except (ValueError, UnicodeError) as error:
                    raise InvalidInput('The proposal must be finite valid UTF-8 JSON or text') from error
                if len(encoded) > MAX_EDIT_BYTES:
                    raise InvalidInput('The proposed value exceeds the existing edit limit')
                original = {'kind': 'JSON_VALUE' if binding['locator']['kind'] == 'JSON_POINTER' else 'UTF8_TEXT',
                    'text': raw, 'sha256': sha(raw.encode('utf-8'))}
            declared = {c.task_id: c for c in body.contracts}
            for task in mapping['task_ids']:
                if task not in declared or not any(i.binding_ref.model_dump() == mapping['binding_ref'] for i in declared[task].inputs):
                    raise InvalidInput('Mapped tasks need source-version contracts referencing this exact selected binding')
            feedback = self.native.get(pid, mapping['native_feedback_ref']['id'])
            linked = self.rework.feedback(pid, FeedbackInput(
                operation_id='nr-feedback-' + digest([pid, body.operation_id])[:52],
                binding_ref=mapping['binding_ref'], comment=feedback['payload']['comment'], mode=feedback['payload']['mode']))
            scope = Scope(workspace_id=binding['file_source_ref']['workspace_id'], path=binding['file_source_ref']['path'])
            result = self.rework.plan(pid, PlanInput(
                operation_id='nr-plan-' + digest([pid, body.operation_id])[:56],
                feedback_ref=linked['feedback_ref'], proposal_ref=mapping['proposal_ref'],
                classification=Classification(kind=body.classification_kind, origin=body.classification_origin,
                    rationale=body.rationale, evidence_refs=[mapping['binding_ref']]),
                patches=[Patch(binding_ref=mapping['binding_ref'], proposed_value=proposed)],
                write_scope=[scope], contracts=body.contracts))
            return {'status': 'PROPOSED', 'mapping_ref': body.mapping_ref.model_dump(),
                'native_feedback_ref': mapping['native_feedback_ref'], 'proposal_ref': mapping['proposal_ref'],
                'proposed_value': proposed, **({'proposal_text': original} if original is not None else {}),
                'engine_plan_ref': result['plan_ref'],
                'engine_plan': result, 'write_scope': [scope.model_dump()],
                'classification_status': 'PROPOSAL_NOT_SEMANTICALLY_VERIFIED', 'write_authorized': False}
        return self._run(pid, body, 'PLAN', build)

    def apply(self, pid, ident, body):
        if ident != body.plan_ref.id:
            raise InvalidInput('Route plan ID differs from its exact reference')
        def build(fresh):
            plan = self._resolve(pid, body.plan_ref.model_dump(), 'PLAN')
            proposal = self._proposal(pid, plan['proposal_ref'])
            mapping = self._resolve(pid, plan['mapping_ref'], 'MAPPING')
            self._requirements(pid, proposal, mapping.get('discovery_ref'), mapping['requirement_ids'])
            engine_op = 'nr-apply-' + digest([pid, body.operation_id])[:55]
            if not fresh:
                try:
                    result = self.rework.operation(pid, engine_op)
                except NotFound:
                    result = None
                if result is None or result.get('status') == 'PROCESSING':
                    return {'status': 'INCOMPLETE_REVIEW_REQUIRED', 'operation_id': body.operation_id,
                        'engine_operation': result, 'automatic_retry': False,
                        'message': 'Inspect this exact operation; interrupted writes are not automatically repeated'}
            else:
                result = self.rework.apply(pid, ApplyInput(operation_id=engine_op,
                    plan_ref=plan['engine_plan_ref'], write_scope=plan['write_scope'],
                    expected_current_sources=body.expected_current_sources, allow_rebase=body.allow_rebase))
            return {'status': result['status'], 'plan_ref': body.plan_ref.model_dump(),
                'native_feedback_ref': plan['native_feedback_ref'], 'proposal_ref': plan['proposal_ref'],
                'engine_receipt': result, 'semantic_correctness_confirmed': False,
                'automatic_retry': False}
        return self._run(pid, body, 'APPLY', build)

    def get(self, pid, ident):
        path = self._path(pid, 'records', ident + '.json')
        if not path.exists():
            raise NotFound('Native rework record does not exist in this project')
        value = self._read(path)
        if value['project_id'] != pid or value['id'] != ident:
            raise Conflict('Native rework record scope changed')
        ref = {'id': ident, 'revision': 1, 'sha256': digest(value)}
        key = {'MAPPING': 'mapping_ref', 'PLAN': 'plan_ref', 'APPLY': 'receipt_ref'}[value['kind']]
        result = {'record_ref': ref, key: ref, 'kind': value['kind'], 'status': value['status'], 'payload': value}
        try:
            proposal = self._proposal(pid, value['proposal_ref'])
            mapping = value
            if value['kind'] == 'PLAN':
                mapping = self._resolve(pid, value['mapping_ref'], 'MAPPING')
            elif value['kind'] == 'APPLY':
                plan = self._resolve(pid, value['plan_ref'], 'PLAN')
                mapping = self._resolve(pid, plan['mapping_ref'], 'MAPPING')
            self._requirements(pid, proposal, mapping.get('discovery_ref'), mapping.get('requirement_ids', []))
            result['current_basis_check'] = {'status': 'CURRENT_TASK_REQUIREMENT_BASIS',
                'source_review_status': proposal['review_status'], 'semantic_correctness_confirmed': False}
        except (Conflict, InvalidInput, NotFound, OSError) as error:
            result['current_basis_check'] = {'status': 'STALE_REVIEW_REQUIRED', 'reason': str(error),
                'semantic_correctness_confirmed': False}
        if value['kind'] in {'PLAN', 'APPLY'}:
            engine_ref = value['engine_plan_ref'] if value['kind'] == 'PLAN' else value['engine_receipt']['receipt_ref']
            result['artifact'] = self.rework.artifact(pid, engine_ref['id'])
        return result

    def listing(self, pid, kind='MAPPING'):
        return [self.get(pid, p.stem) for p in sorted(self._path(pid, 'records').glob('*.json'))
            if self._read(p)['kind'] == kind]

    def original_input(self, pid, op):
        """Read an immutable saved request and its exact saved basis only.

        This does not call get/current-basis, source readers, the repair engine,
        a native owner, or an execution/permission endpoint.
        """
        import re
        from packages.domain.context_read_budget import context_read_scope
        if not isinstance(op, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', op):
            raise InvalidInput('Invalid native rework original operation ID')
        with context_read_scope():
            folder = self._path(pid, 'operations', digest(op))
            if not (folder / 'request.json').exists():
                raise NotFound('Native rework original request is not available')
            request = self._read(folder / 'request.json')
            basis = {key: request.get(key) for key in ('project_id', 'operation_id', 'kind', 'input')}
            body = basis['input']
            if (basis['project_id'] != pid or basis['operation_id'] != op or
                    basis['kind'] not in {'MAPPING', 'PLAN', 'APPLY'} or not isinstance(body, dict) or
                    body.get('operation_id') != op or digest(basis) != request.get('request_sha256')):
                raise Conflict('Native rework original request identity changed')
            metadata_reads = 1

            def saved_ref(value):
                try:
                    return RevisionRef.model_validate(value).model_dump()
                except (ValueError, TypeError):
                    raise Conflict('Native rework original input reference is invalid') from None

            def saved(ref, kind):
                nonlocal metadata_reads
                ref = saved_ref(ref)
                value = self._read(self._path(pid, 'records', ref['id'] + '.json'))
                metadata_reads += 1
                if (value.get('project_id') != pid or value.get('id') != ref['id'] or
                        value.get('revision') != ref['revision'] or value.get('kind') != kind or digest(value) != ref['sha256']):
                    raise Conflict('Native rework saved input basis changed')
                return value

            if basis['kind'] == 'MAPPING':
                protocol = 'MAPPING'
                scope = {'basis_ref': saved_ref(body.get('proposal_ref')),
                    'feedback_ref': saved_ref(body.get('native_feedback_ref'))}
            elif basis['kind'] == 'PLAN' and 'mapping_ref' in body:
                protocol = 'ORDINARY_PREVIEW'
                mapping = saved(body['mapping_ref'], 'MAPPING')
                scope = {'basis_ref': body['mapping_ref'], 'feedback_ref': mapping.get('native_feedback_ref')}
            else:
                plan = saved(body.get('plan_ref'), 'PLAN')
                protocol = 'APPLY' if basis['kind'] == 'APPLY' else 'CONFLICT_PREVIEW'
                scope = {'basis_ref': body['plan_ref'] if basis['kind'] == 'APPLY' else plan.get('mapping_ref'),
                    'feedback_ref': plan.get('native_feedback_ref')}
            saved_ref(scope['basis_ref'])
            saved_ref(scope['feedback_ref'])
            return {'kind': 'NATIVE_REWORK_ORIGINAL_INPUT', 'version': 1,
                'project_id': pid, 'operation_id': op, 'operation_kind': basis['kind'],
                'protocol': protocol, 'input_scope': scope, 'input_json': canonical(body),
                'input_sha256': digest(body), 'request_sha256': request['request_sha256'],
                'read_only': True, 'source_files_written': 0, 'model_calls': 0,
                'execution_started': False, 'write_authorized': False,
                'saved_metadata_records_read': metadata_reads,
                'input_encoding': 'EXACT_SAVED_CANONICAL_JSON_NOT_RECONSTRUCTED_CALLER_LITERAL'}

    def operation(self, pid, op):
        folder = self._path(pid, 'operations', digest(op))
        if not (folder / 'request.json').exists():
            raise NotFound('Native rework operation does not exist in this project')
        if (folder / 'result.json').exists():
            return self.get(pid, self._read(folder / 'result.json')['record_id'])
        request = self._read(folder / 'request.json')
        inspection = None
        if request['kind'] == 'APPLY':
            try:
                inspection = self.rework.operation(pid, 'nr-apply-' + digest([pid, op])[:55])
            except NotFound:
                pass
        return {'status': 'INCOMPLETE_REVIEW_REQUIRED', 'operation_id': op,
            'request_sha256': request['request_sha256'], 'engine_operation': inspection,
            'automatic_retry': False}

    def context(self, pid, source):
        """Read only the selected real file's project and exact binding lineage."""
        source = FileSource.model_validate(source)
        self.native._baseline(pid, source)
        identity = self._file_identity(source.model_dump())
        feedbacks = []
        for path in sorted(self.native._path(pid, 'records').glob('*.json')):
            recorded = self.native._feedback(pid, path.stem)
            if self._file_identity(recorded['baseline_file_source_ref']) == identity:
                feedbacks.append(self.native.get(pid, path.stem))
        mappings = [v for v in self.listing(pid) if
            self._file_identity(v['payload']['baseline_file_source_ref']) == identity]
        proposals = []
        linked = {v['payload']['proposal_ref']['id'] for v in mappings}
        for value in self.planning.list(pid):
            if value['payload']['status'] != 'CONFIRMED':
                continue
            local = [d.get('local_file_source_ref') for d in value['payload']['source_receipt']['documents']]
            known = value['id'] in linked or any(s and self._file_identity(s) == identity for s in local)
            proposals.append({'proposal_ref': {k: value[k] for k in ('id', 'revision', 'sha256')},
                'title': value['payload']['candidate']['title'], 'review_status': value['review_status'],
                'relationship': 'RECORDED_FILE_LINEAGE_REQUIRES_OBJECT_CHECK' if known else 'NO_VERIFIED_FILE_MAPPING',
                'task_ids': [n['id'] for n in value['payload']['candidate']['nodes'] if n['kind'] == 'TASK']})
        return {'status': 'READ_ONLY_CONTEXT', 'file_source_ref': source.model_dump(),
            'feedback': feedbacks, 'mappings': mappings, 'confirmed_proposals': proposals,
            'file_write_authorized': False, 'automatic_task_selection': False}
