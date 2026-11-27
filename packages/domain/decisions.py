"""Explicit local-user information decisions, scoped to one existing TaskSpec."""
import json
from typing import Literal
import uuid

from pydantic import Field, model_validator

from packages.domain.intake import IntakeService, canonical, digest
from packages.domain.questions import (AffectedTask, Hash, Id, QuestionService, RequestModel,
                                      checked, raw_hash, stamp)
from packages.domain.store import Conflict, InvalidInput, NotFound

MAX_REVISION = 2**63-1


class IntakeRef(RequestModel):
    intake_id: Id
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    sha256: Hash


class QuestionRef(RequestModel):
    question_id: Id
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    sha256: Hash


class AnswerRef(RequestModel):
    answer_id: Id
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    sha256: Hash


class ReviewedScope(RequestModel):
    question_id: Id
    question_revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    question_sha256: Hash
    task_id: Id
    task_revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    spec_hash: Hash


class ResolutionInput(RequestModel):
    idempotency_key: Id
    intake_ref: IntakeRef
    question_ref: QuestionRef
    task_ref: AffectedTask
    reviewed_scope_refs: list[ReviewedScope] = Field(min_length=1, max_length=200)
    mode: Literal['ANSWER_ACCEPTED', 'ASSUMPTION_ACCEPTED']
    answer_refs: list[AnswerRef] = Field(default_factory=list, max_length=20)
    assumption: str | None = Field(default=None, min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode='after')
    def explicit_basis(self):
        if self.mode == 'ANSWER_ACCEPTED' and (not self.answer_refs or self.assumption is not None):
            raise ValueError('Answer acceptance needs exact answers and no implicit assumption')
        if self.mode == 'ASSUMPTION_ACCEPTED' and (self.assumption is None or self.answer_refs):
            raise ValueError('Assumption acceptance needs explicit original text')
        if len({a.answer_id for a in self.answer_refs}) != len(self.answer_refs):
            raise ValueError('Answer references must be unique')
        return self


class ResolutionRef(RequestModel):
    resolution_id: Id
    sha256: Hash


class AcceptedAssumption(RequestModel):
    resolution_id: Id
    assumption_sha256: Hash


class StartDecisionInput(RequestModel):
    idempotency_key: Id
    intent: Literal['USER_READY_OVERRIDE']
    user_ready: bool = Field(strict=True)
    intake_ref: IntakeRef
    task_ref: AffectedTask
    scope_reason: str = Field(min_length=1, max_length=4000)
    resolution_refs: list[ResolutionRef] = Field(default_factory=list, max_length=100)
    accepted_assumptions: list[AcceptedAssumption] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def explicit_ready(self):
        if self.user_ready is not True:
            raise ValueError('User readiness must be explicitly true')
        for name in ('resolution_refs', 'accepted_assumptions'):
            refs = getattr(self, name)
            if len({r.resolution_id for r in refs}) != len(refs):
                raise ValueError('Decision references must be unique')
        return self


def scope_history(connection, project_id, question_id, task_id):
    rows = connection.execute('''SELECT r.*,v.payload,v.sha256 FROM project_question_task_refs r
        JOIN project_question_versions v ON v.question_id=r.question_id AND v.revision=r.question_revision
        JOIN project_questions q ON q.id=r.question_id
        WHERE q.project_id=? AND r.question_id=? AND r.task_id=? ORDER BY r.question_revision''',
        (project_id, question_id, task_id)).fetchall()
    result = []
    for row in rows:
        payload = checked(row)
        ref = {'task_id': row['task_id'], 'revision': row['task_revision'], 'spec_hash': row['spec_hash']}
        if ref not in payload['affected_task_refs'] or payload['project_id'] != project_id:
            raise Conflict('Question history scope integrity check failed')
        result.append({'question_id': question_id, 'question_revision': row['question_revision'],
            'question_sha256': row['sha256'], 'task_id': task_id,
            'task_revision': row['task_revision'], 'spec_hash': row['spec_hash']})
    return result


def exact_set(items):
    values = [canonical(item) for item in items]
    if len(set(values)) != len(values):
        raise Conflict('Duplicate scope references are not accepted')
    return set(values)


def intake_current(connection, project_id, ref):
    intake = IntakeService._version(connection, project_id, ref['intake_id'], ref['revision'])
    if intake['sha256'] != ref['sha256'] or not QuestionService._intake_current(connection, project_id, intake):
        raise Conflict('INTAKE_SOURCE_STALE: a new explicit scope decision is required')
    return intake


def task_identity_current(connection, project_id, ref):
    row = connection.execute('''SELECT t.current_revision,v.sha256,v.spec FROM tasks t
        JOIN task_versions v ON v.task_id=t.id AND v.revision=?
        WHERE t.id=? AND t.project_id=?''', (ref['revision'], ref['task_id'], project_id)).fetchone()
    if not row or row['current_revision'] != ref['revision'] or row['sha256'] != ref['spec_hash']:
        raise Conflict('Task scope changed; explicitly review the new version')
    spec = json.loads(row['spec'])
    if digest(spec) != ref['spec_hash']:
        raise Conflict('Stored TaskSpec integrity check failed')
    return spec


def task_sources_in_intake(spec, intake):
    selected = {(m['document_id'], m['revision'], m['raw_sha256']) for m in intake['snapshot']['materials']}
    required = {(r['document_id'], r['revision'], r['sha256']) for r in spec['input_refs']}
    if not required or not required <= selected:
        raise Conflict('Task sources are not covered by this exact intake snapshot')


def resolution_valid(connection, project_id, row, ref):
    payload = checked(row)
    if (payload['project_id'] != project_id or payload['resolution_id'] != row['id']
            or payload['task_ref'] != ref or row['task_id'] != ref['task_id']
            or row['task_revision'] != ref['revision'] or row['spec_hash'] != ref['spec_hash']):
        raise Conflict('Resolution is bound to a different task scope')
    intake = intake_current(connection, project_id, payload['intake_ref'])
    spec = task_identity_current(connection, project_id, ref)
    task_sources_in_intake(spec, intake)
    qr = payload['question_ref']
    q = QuestionService._question(connection, project_id, qr['question_id'], qr['revision'])
    if q['sha256'] != qr['sha256'] or q['current_revision'] != qr['revision']:
        raise Conflict('Question changed after information resolution')
    if (q['payload']['intake_id'], q['payload']['intake_revision'], q['payload']['intake_sha256']) != (
            payload['intake_ref']['intake_id'], payload['intake_ref']['revision'], payload['intake_ref']['sha256']):
        raise Conflict('Resolution and question have different source snapshots')
    history = scope_history(connection, project_id, qr['question_id'], ref['task_id'])
    if not history or exact_set(history) != exact_set(payload['reviewed_scope_refs']):
        raise Conflict('SCOPE_HISTORY_REVIEW_REQUIRED: question scope history changed')
    latest = connection.execute('''SELECT max(seq) FROM project_question_resolutions WHERE project_id=?
        AND question_id=? AND task_id=? AND task_revision=? AND spec_hash=?''',
        (project_id, qr['question_id'], ref['task_id'], ref['revision'], ref['spec_hash'])).fetchone()[0]
    if latest != row['seq']:
        raise Conflict('A newer explicit resolution replaced this scope')
    for ar in payload['answer_refs']:
        a = QuestionService._answer(connection, project_id, qr['question_id'], ar['answer_id'], ar['revision'])
        ap = a['payload']
        if (a['sha256'] != ar['sha256'] or a['current_revision'] != ar['revision']
                or ap['question_revision'] != qr['revision'] or ap['question_sha256'] != qr['sha256']
                or ap['application_status'] != 'RECORDED' or ap['intake_sha256'] != payload['intake_ref']['sha256']):
            raise Conflict('Answer changed or is late; explicit current-scope review is required')
    return payload


def decision_valid(connection, project_id, row, ref, blockers):
    payload = checked(row)
    if (payload['project_id'] != project_id or payload['decision_id'] != row['id'] or payload['task_ref'] != ref
            or row['task_id'] != ref['task_id'] or row['task_revision'] != ref['revision'] or row['spec_hash'] != ref['spec_hash']):
        raise Conflict('Start decision is bound to a different task scope')
    intake = intake_current(connection, project_id, payload['intake_ref'])
    spec = task_identity_current(connection, project_id, ref)
    task_sources_in_intake(spec, intake)
    latest = connection.execute('''SELECT max(seq) FROM project_start_decisions WHERE project_id=?
        AND task_id=? AND task_revision=? AND spec_hash=?''', (project_id, ref['task_id'], ref['revision'], ref['spec_hash'])).fetchone()[0]
    if latest != row['seq']:
        raise Conflict('A newer explicit start decision replaced this scope')
    by_question = {}
    assumptions = []
    for rr in payload['resolution_refs']:
        res = connection.execute('SELECT * FROM project_question_resolutions WHERE id=? AND project_id=?',
                                 (rr['resolution_id'], project_id)).fetchone()
        if not res or res['sha256'] != rr['sha256']:
            raise Conflict('Start resolution reference changed or is outside this project')
        rp = resolution_valid(connection, project_id, res, ref)
        if rp['intake_ref'] != payload['intake_ref'] or rp['question_ref']['question_id'] in by_question:
            raise Conflict('Resolution set has mixed or duplicate scopes')
        by_question[rp['question_ref']['question_id']] = rp
        if rp['mode'] == 'ASSUMPTION_ACCEPTED':
            assumptions.append({'resolution_id': res['id'], 'assumption_sha256': rp['assumption_sha256']})
    if set(by_question) != {b['question_id'] for b in blockers}:
        raise Conflict('UNRESOLVED_INFORMATION: every blocking question for this task needs its own resolution')
    if exact_set(assumptions) != exact_set(payload['accepted_assumptions']):
        raise Conflict('User must explicitly accept each exact assumption for this start scope')
    return payload


def apply_start_decisions(connection, project_id, gate):
    """No TaskRun mutation. Current scoped decision changes information eligibility."""
    ref = {'task_id': gate['task_id'], 'revision': gate['task_revision'], 'spec_hash': gate['spec_hash']}
    rows = connection.execute('''SELECT * FROM project_start_decisions WHERE project_id=? AND task_id=?
        AND task_revision=? AND spec_hash=? ORDER BY seq DESC LIMIT 1''',
        (project_id, ref['task_id'], ref['revision'], ref['spec_hash'])).fetchall()
    qualified = None
    for row in rows:
        try:
            decision_valid(connection, project_id, row, ref, gate['blockers'])
        except (Conflict, NotFound):
            continue
        qualified = {'decision_id': row['id'], 'sha256': row['sha256']}
    if qualified:
        return {**gate, 'status': 'READY', 'resolved_blockers': gate['blockers'], 'blockers': [],
            'start_decision': qualified, 'resolution_implemented': True,
            'readiness_scope': 'EXISTING_TASK_SPEC_ONLY', 'workflow_preparation_status': 'TASKBOOK_HARNESS_NOT_IMPLEMENTED'}
    return {**gate, 'resolution_implemented': True,
            'readiness_scope': 'EXISTING_TASK_SPEC_ONLY', 'workflow_preparation_status': 'TASKBOOK_HARNESS_NOT_IMPLEMENTED'}


class DecisionService:
    def __init__(self, store):
        self.store = store

    def _task(self, project_id, ref):
        task = self.store.task(project_id, ref['task_id'], ref['revision'])
        if task['current_revision'] != ref['revision'] or task['sha256'] != ref['spec_hash'] or task['review_status'] != 'CURRENT':
            raise Conflict('Task/code/profile/source changed; review its current exact scope')
        return task

    @staticmethod
    def _row(connection, table, project_id, id):
        row = connection.execute('SELECT * FROM '+table+' WHERE id=? AND project_id=?', (id, project_id)).fetchone()
        if row is None:
            raise NotFound('Decision does not exist in this project')
        checked(row)
        return row

    @staticmethod
    def _receipt(row, association='CURRENT'):
        return {'id': row['id'], 'project_id': row['project_id'], 'revision': 1,
                'sha256': row['sha256'], 'payload': checked(row), 'current_association': association, 'seq': row['seq']}

    def resolve(self, project_id, body: ResolutionInput, caller_id):
        self.store.project(project_id)
        caller = QuestionService.caller(caller_id)
        request_hash = digest({'kind': 'resolution', 'body': body.model_dump(), 'caller': caller})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = QuestionService._replay(connection, project_id, body.idempotency_key, request_hash)
            if replay is not None:
                return replay
            intake_ref, task_ref, qr = body.intake_ref.model_dump(), body.task_ref.model_dump(), body.question_ref.model_dump()
            intake = intake_current(connection, project_id, intake_ref)
            task = self._task(project_id, task_ref); task_sources_in_intake(task['spec'], intake)
            q = QuestionService._question(connection, project_id, qr['question_id'], qr['revision'])
            if q['sha256'] != qr['sha256'] or q['current_revision'] != qr['revision']:
                raise Conflict('Resolution requires the current exact question')
            if (q['payload']['intake_id'], q['payload']['intake_revision'], q['payload']['intake_sha256']) != (
                    intake_ref['intake_id'], intake_ref['revision'], intake_ref['sha256']):
                raise Conflict('Question and resolution have different source snapshots')
            history = scope_history(connection, project_id, qr['question_id'], task_ref['task_id'])
            reviewed = [r.model_dump() for r in body.reviewed_scope_refs]
            if not history or exact_set(history) != exact_set(reviewed):
                raise Conflict('SCOPE_HISTORY_REVIEW_REQUIRED: review every exact prior question/task association')
            answer_refs = [a.model_dump() for a in body.answer_refs]
            for ar in answer_refs:
                a = QuestionService._answer(connection, project_id, qr['question_id'], ar['answer_id'], ar['revision'])
                ap = a['payload']
                if (a['current_revision'] != ar['revision'] or a['sha256'] != ar['sha256']
                        or ap['question_revision'] != qr['revision'] or ap['question_sha256'] != qr['sha256']
                        or ap['application_status'] != 'RECORDED' or ap['intake_sha256'] != intake_ref['sha256']):
                    raise Conflict('Late/changed answer cannot be silently applied to this scope')
            id = str(uuid.uuid4()); created = stamp()
            payload = {'schema_version': 'eeas.question-resolution.v1', 'resolution_id': id, 'revision': 1,
                'project_id': project_id, 'intake_ref': intake_ref, 'question_ref': qr, 'task_ref': task_ref,
                'reviewed_scope_refs': reviewed, 'mode': body.mode, 'answer_refs': answer_refs,
                'assumption': body.assumption, 'assumption_sha256': raw_hash(body.assumption) if body.assumption is not None else None,
                'reason': body.reason, 'caller': caller, 'created_at': created, 'execution_authorized': False,
                'method': 'LOCAL_USER_RESOLUTION', 'model_calls': 0}
            connection.execute('''INSERT INTO project_question_resolutions
                (id,project_id,question_id,task_id,task_revision,spec_hash,payload,sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                (id, project_id, qr['question_id'], task_ref['task_id'], task_ref['revision'], task_ref['spec_hash'], canonical(payload), digest(payload), created))
            row = self._row(connection, 'project_question_resolutions', project_id, id)
            result = self._receipt(row)
            QuestionService._event(connection, project_id, qr['question_id'], 'QUESTION_RESOLUTION_RECORDED',
                                   {'resolution_id': id, 'sha256': row['sha256'], 'task_ref': task_ref})
            QuestionService._operation(connection, project_id, body.idempotency_key, request_hash, result)
            return result

    def start(self, project_id, body: StartDecisionInput, caller_id):
        self.store.project(project_id)
        caller = QuestionService.caller(caller_id)
        request_hash = digest({'kind': 'start-decision', 'body': body.model_dump(), 'caller': caller})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = QuestionService._replay(connection, project_id, body.idempotency_key, request_hash)
            if replay is not None:
                return replay
            intake_ref, task_ref = body.intake_ref.model_dump(), body.task_ref.model_dump()
            intake = intake_current(connection, project_id, intake_ref)
            task = self._task(project_id, task_ref); task_sources_in_intake(task['spec'], intake)
            from packages.domain.questions import knowledge_gate
            raw_gate = knowledge_gate(connection, project_id, task_ref['task_id'], task_ref['revision'], task_ref['spec_hash'], check_decisions=False)
            id = str(uuid.uuid4()); created = stamp()
            payload = {'schema_version': 'eeas.user-start-decision.v1', 'decision_id': id, 'revision': 1,
                'project_id': project_id, 'intake_ref': intake_ref, 'task_ref': task_ref, 'intent': body.intent,
                'user_ready': body.user_ready, 'scope_reason': body.scope_reason,
                'resolution_refs': [r.model_dump() for r in body.resolution_refs],
                'accepted_assumptions': [a.model_dump() for a in body.accepted_assumptions],
                'method': 'LOCAL_USER_OVERRIDE', 'caller': caller, 'created_at': created, 'model_calls': 0,
                'readiness_scope': 'EXISTING_TASK_SPEC_ONLY', 'workflow_preparation_status': 'TASKBOOK_HARNESS_NOT_IMPLEMENTED',
                'coverage_at_decision': [{'document_id': m['document_id'], 'revision': m['revision'],
                    'text_coverage': m['text_coverage'], 'original_modal_coverage': m['original_modal_coverage']} for m in intake['snapshot']['materials']],
                'budget_sha256': digest(task['spec']['budget']), 'execution_approval_granted': False}
            connection.execute('''INSERT INTO project_start_decisions
                (id,project_id,task_id,task_revision,spec_hash,payload,sha256,created_at) VALUES (?,?,?,?,?,?,?,?)''',
                (id, project_id, task_ref['task_id'], task_ref['revision'], task_ref['spec_hash'], canonical(payload), digest(payload), created))
            row = self._row(connection, 'project_start_decisions', project_id, id)
            decision_valid(connection, project_id, row, task_ref, raw_gate['blockers'])
            result = self._receipt(row)
            for b in raw_gate['blockers']:
                QuestionService._event(connection, project_id, b['question_id'], 'USER_START_DECISION_RECORDED',
                                       {'decision_id': id, 'sha256': row['sha256'], 'task_ref': task_ref})
            QuestionService._operation(connection, project_id, body.idempotency_key, request_hash, result)
            return result

    def get_resolution(self, project_id, id):
        with self.store.connect() as connection:
            row = self._row(connection, 'project_question_resolutions', project_id, id)
            payload = checked(row); association = 'CURRENT'
            try:
                self._task(project_id, payload['task_ref'])
                resolution_valid(connection, project_id, row, payload['task_ref'])
            except (Conflict, NotFound):
                association = 'REVIEW_REQUIRED'
            return self._receipt(row, association)

    def get_start(self, project_id, id):
        with self.store.connect() as connection:
            row = self._row(connection, 'project_start_decisions', project_id, id)
            payload = checked(row); association = 'CURRENT'
            try:
                self._task(project_id, payload['task_ref'])
                from packages.domain.questions import knowledge_gate
                ref = payload['task_ref']
                gate = knowledge_gate(connection, project_id, ref['task_id'], ref['revision'], ref['spec_hash'], check_decisions=False)
                decision_valid(connection, project_id, row, ref, gate['blockers'])
            except (Conflict, NotFound):
                association = 'REVIEW_REQUIRED'
            return self._receipt(row, association)
