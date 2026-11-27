"""Durable information questions; recording an answer grants no execution right."""
from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.domain.intake import IntakeService, canonical, digest
from packages.domain.store import Conflict, InvalidInput, NotFound

Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Id = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('*')
    @classmethod
    def original_utf8(cls, value):
        if isinstance(value, str):
            try:
                raw = value.encode('utf-8')
            except UnicodeError as exc:
                raise ValueError('Text must be valid UTF-8') from exc
            if not value.strip() or len(raw) > 65536:
                raise ValueError('Text must be nonblank and at most 64 KiB')
        return value


class QuestionAnchor(RequestModel):
    document_id: Id
    revision: int = Field(ge=1, strict=True)
    raw_sha256: Hash
    chunk_index: int = Field(ge=0, strict=True)
    chunk_sha256: Hash
    char_start: int = Field(ge=0, strict=True)
    char_end: int = Field(gt=0, strict=True)
    quote_sha256: Hash


class AffectedTask(RequestModel):
    task_id: Id
    revision: int = Field(ge=1, strict=True)
    spec_hash: Hash


class QuestionInput(RequestModel):
    idempotency_key: Id
    expected_revision: int = Field(default=0, ge=0, strict=True)
    intake_id: Id
    intake_revision: int = Field(ge=1, strict=True)
    intake_sha256: Hash
    text: str = Field(min_length=1, max_length=16000)
    priority: Literal['INPUT_REQUIRED', 'DECISION_REQUIRED', 'ADVISORY'] = 'INPUT_REQUIRED'
    decision_field: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,99}$')
    scope: str = Field(min_length=1, max_length=4000)
    source_anchors: list[QuestionAnchor] = Field(min_length=1, max_length=32)
    affected_task_refs: list[AffectedTask] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_refs(self):
        if len({r.task_id for r in self.affected_task_refs}) != len(self.affected_task_refs):
            raise ValueError('Affected tasks must be unique')
        keys = {(r.document_id, r.revision, r.char_start, r.char_end) for r in self.source_anchors}
        if len(keys) != len(self.source_anchors):
            raise ValueError('Source ranges must be unique')
        return self


class AnswerInput(RequestModel):
    idempotency_key: Id
    expected_answer_revision: int = Field(default=0, ge=0, strict=True)
    question_revision: int = Field(ge=1, strict=True)
    question_sha256: Hash
    text: str = Field(min_length=1, max_length=16000)


class QuestionCancel(RequestModel):
    idempotency_key: Id
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=4000)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def raw_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def checked(row):
    payload = json.loads(row['payload'])
    if digest(payload) != row['sha256']:
        raise Conflict('Stored question/answer integrity check failed')
    return payload


class KnowledgeBlocked(Conflict):
    def __init__(self, gate):
        self.gate = gate
        super().__init__(gate['status'] + ': explicit information resolution is required')


def knowledge_gate(connection, project_id, task_id, revision, spec_hash, *, check_decisions=True):
    """Check before new acceptance/claim in their existing write transaction.

    Answer/cancel/supersede cannot retire a blocking scope. The separate explicit
    user decision service validates current sources and reviewed scope history.
    """
    rows = connection.execute('''SELECT r.*, v.payload, v.sha256, q.current_revision
        FROM project_question_task_refs r
        JOIN project_question_versions v ON v.question_id=r.question_id AND v.revision=r.question_revision
        JOIN project_questions q ON q.id=r.question_id
        WHERE q.project_id=? AND r.task_id=? ORDER BY q.seq,r.question_revision''',
        (project_id, task_id)).fetchall()
    grouped = {}
    for row in rows:
        payload = checked(row)
        ref = {'task_id': row['task_id'], 'revision': row['task_revision'], 'spec_hash': row['spec_hash']}
        if (payload['project_id'] != project_id or payload['question_id'] != row['question_id']
                or payload['revision'] != row['question_revision'] or ref not in payload['affected_task_refs']):
            raise Conflict('Stored question task scope integrity check failed')
        if payload['priority'] == 'ADVISORY':
            continue
        grouped.setdefault(row['question_id'], []).append((row, payload))
    blockers = []
    for question_id, versions in grouped.items():
        exact = [(r, p) for r, p in versions if r['task_revision'] == revision and r['spec_hash'] == spec_hash]
        row, payload = (exact or versions)[-1]
        reason = 'UNRESOLVED_INFORMATION' if exact else 'TASK_SCOPE_CHANGED'
        if payload['state'] == 'CANCELLED' and exact:
            reason = 'CANCELLATION_IS_NOT_RESOLUTION'
        blockers.append({'question_id': question_id, 'question_revision': row['question_revision'],
            'question_sha256': row['sha256'], 'priority': payload['priority'],
            'intake_id': payload['intake_id'], 'intake_revision': payload['intake_revision'],
            'intake_sha256': payload['intake_sha256'],
            'bound_task_revision': row['task_revision'], 'bound_spec_hash': row['spec_hash'],
            'reason': reason})
    status = ('NEEDS_SCOPE_REVIEW' if any(b['reason'] == 'TASK_SCOPE_CHANGED' for b in blockers)
              else 'BLOCKED_ON_QUESTION' if blockers else 'READY')
    gate = {'status': status, 'task_id': task_id, 'task_revision': revision,
            'spec_hash': spec_hash, 'blockers': blockers, 'scope': 'UNSTARTED_DISPATCH_ONLY',
            'resolution_implemented': False}
    if check_decisions:
        from packages.domain.decisions import apply_start_decisions
        return apply_start_decisions(connection, project_id, gate)
    return gate


class QuestionService:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def caller(caller_id):
        if not isinstance(caller_id, str) or len(caller_id) != 64 or any(c not in '0123456789abcdef' for c in caller_id):
            raise InvalidInput('A trusted local session identity is required')
        return {'kind': 'LOCAL_SESSION', 'id': caller_id}

    @staticmethod
    def _question(connection, project_id, question_id, revision=None):
        parent = connection.execute('SELECT * FROM project_questions WHERE id=? AND project_id=?',
                                    (question_id, project_id)).fetchone()
        if parent is None:
            raise NotFound('Question does not exist in this project')
        revision = parent['current_revision'] if revision is None else revision
        row = connection.execute('SELECT * FROM project_question_versions WHERE question_id=? AND revision=?',
                                 (question_id, revision)).fetchone()
        if row is None:
            raise NotFound('Question revision does not exist')
        payload = checked(row)
        if payload['project_id'] != project_id or payload['question_id'] != question_id or payload['revision'] != revision:
            raise Conflict('Stored question identity check failed')
        answered = connection.execute('''SELECT 1 FROM project_answer_versions v
            JOIN project_answers a ON a.id=v.answer_id WHERE a.question_id=?
            AND json_extract(v.payload,'$.question_revision')=? LIMIT 1''', (question_id, revision)).fetchone()
        status = 'CANCELLED' if payload['state'] == 'CANCELLED' else 'ANSWER_RECORDED' if answered else 'OPEN'
        return {'id': question_id, 'project_id': project_id, 'revision': revision,
            'current_revision': parent['current_revision'], 'sha256': row['sha256'],
            'status': status, 'payload': payload, 'seq': parent['seq']}

    @staticmethod
    def _answer(connection, project_id, question_id, answer_id, revision=None):
        QuestionService._question(connection, project_id, question_id)
        parent = connection.execute('SELECT * FROM project_answers WHERE id=? AND question_id=?',
                                    (answer_id, question_id)).fetchone()
        if parent is None:
            raise NotFound('Answer does not exist for this question')
        revision = parent['current_revision'] if revision is None else revision
        row = connection.execute('SELECT * FROM project_answer_versions WHERE answer_id=? AND revision=?',
                                 (answer_id, revision)).fetchone()
        if row is None:
            raise NotFound('Answer revision does not exist')
        payload = checked(row)
        if (payload['project_id'] != project_id or payload['question_id'] != question_id
                or payload['answer_id'] != answer_id or payload['revision'] != revision):
            raise Conflict('Stored answer identity check failed')
        return {'id': answer_id, 'project_id': project_id, 'question_id': question_id,
            'revision': revision, 'current_revision': parent['current_revision'],
            'sha256': row['sha256'], 'payload': payload, 'seq': parent['seq']}

    @staticmethod
    def _replay(connection, project_id, key, request_hash):
        row = connection.execute('SELECT * FROM project_question_operations WHERE project_id=? AND idempotency_key=?',
                                 (project_id, key)).fetchone()
        if row is None:
            return None
        if row['payload_sha256'] != request_hash:
            raise Conflict('IDEMPOTENCY_CONFLICT: key is bound to different input')
        result = json.loads(row['receipt'])
        if digest(result) != row['receipt_sha256']:
            raise Conflict('Stored operation receipt integrity check failed')
        return result

    @staticmethod
    def _operation(connection, project_id, key, request_hash, result):
        connection.execute('INSERT INTO project_question_operations VALUES (?,?,?,?,?)',
            (project_id, key, request_hash, canonical(result), digest(result)))

    @staticmethod
    def _event(connection, project_id, question_id, kind, payload):
        connection.execute('''INSERT INTO project_question_events
            (project_id,question_id,kind,payload,sha256,created_at) VALUES (?,?,?,?,?,?)''',
            (project_id, question_id, kind, canonical(payload), digest(payload), stamp()))

    @staticmethod
    def _intake_current(connection, project_id, intake):
        parent = connection.execute('SELECT current_revision FROM project_intakes WHERE id=? AND project_id=?',
                                    (intake['id'], project_id)).fetchone()
        if not parent or parent['current_revision'] != intake['revision']:
            return False
        for material in intake['snapshot']['materials']:
            row = connection.execute('''SELECT d.current_revision,v.sha256 FROM documents d
                JOIN document_versions v ON v.document_id=d.id AND v.revision=d.current_revision
                WHERE d.id=? AND d.project_id=? AND d.deleted_at IS NULL''',
                (material['document_id'], project_id)).fetchone()
            if not row or row['current_revision'] != material['revision'] or row['sha256'] != material['raw_sha256']:
                return False
        return True

    def _anchors(self, connection, project_id, body):
        intake = IntakeService._version(connection, project_id, body.intake_id, body.intake_revision)
        if intake['sha256'] != body.intake_sha256:
            raise Conflict('Intake source snapshot hash changed')
        if not self._intake_current(connection, project_id, intake):
            raise Conflict('INTAKE_SOURCE_STALE: create a reviewed current source snapshot')
        anchors = []
        for anchor in body.source_anchors:
            material = next((m for m in intake['snapshot']['materials'] if m['document_id'] == anchor.document_id
                             and m['revision'] == anchor.revision), None)
            if not material:
                raise InvalidInput('Anchor is outside the selected intake source')
            chunk = next((c for c in material['chunks'] if c['index'] == anchor.chunk_index), None)
            if not chunk:
                raise InvalidInput('Anchor chunk was not selected in the source snapshot')
            if (material['raw_sha256'] != anchor.raw_sha256 or chunk['sha256'] != anchor.chunk_sha256
                    or raw_hash(chunk['content']) != anchor.chunk_sha256):
                raise Conflict('Anchor source/hash changed')
            if not chunk['char_start'] <= anchor.char_start < anchor.char_end <= chunk['char_end']:
                raise InvalidInput('Anchor range is outside its selected chunk; no text was truncated')
            start, end = anchor.char_start-chunk['char_start'], anchor.char_end-chunk['char_start']
            quote = chunk['content'][start:end]
            if raw_hash(quote) != anchor.quote_sha256:
                raise Conflict('Anchor quote hash changed')
            anchors.append({**anchor.model_dump(), 'quote': quote})
        return anchors

    @staticmethod
    def _write_refs(connection, question_id, revision, refs):
        for ref in refs:
            connection.execute('INSERT INTO project_question_task_refs VALUES (?,?,?,?,?)',
                (question_id, revision, ref['task_id'], ref['revision'], ref['spec_hash']))

    def save(self, project_id, body: QuestionInput, caller_id, question_id=None):
        self.store.project(project_id)
        caller = self.caller(caller_id)
        if bool(question_id) != bool(body.expected_revision):
            raise Conflict('Question revision requires its exact current revision')
        request_hash = digest({'kind': 'question', 'question_id': question_id, 'body': body.model_dump(), 'caller': caller})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = self._replay(connection, project_id, body.idempotency_key, request_hash)
            if replay is not None:
                return replay
            if question_id:
                old = self._question(connection, project_id, question_id)
                if old['revision'] != body.expected_revision:
                    raise Conflict('Question changed; reload its current revision')
                revision = old['revision']+1
            else:
                revision, question_id = 1, str(uuid.uuid4())
            anchors = self._anchors(connection, project_id, body)
            refs = [r.model_dump() for r in body.affected_task_refs]
            for ref in refs:
                task = self.store.task(project_id, ref['task_id'], ref['revision'])
                if task['current_revision'] != ref['revision'] or task['sha256'] != ref['spec_hash'] or task['review_status'] != 'CURRENT':
                    raise Conflict('Affected task version/hash or source is stale')
            semantic = digest({'project_id': project_id, 'intake_id': body.intake_id, 'revision': body.intake_revision,
                'sha256': body.intake_sha256, 'decision_field': body.decision_field, 'identity': ' '.join(body.text.split())})
            if connection.execute('''SELECT 1 FROM project_question_versions v JOIN project_questions q ON q.id=v.question_id
                    WHERE q.project_id=? AND v.semantic_key=? AND q.id!=? LIMIT 1''', (project_id, semantic, question_id)).fetchone():
                raise Conflict('QUESTION_ALREADY_EXISTS: revise the existing source-bound question')
            payload = {'schema_version': 'eeas.question.v1', 'project_id': project_id, 'question_id': question_id,
                'revision': revision, 'parent_revision': revision-1, 'intake_id': body.intake_id,
                'intake_revision': body.intake_revision, 'intake_sha256': body.intake_sha256,
                'text': body.text, 'text_sha256': raw_hash(body.text), 'priority': body.priority,
                'decision_field': body.decision_field, 'scope': body.scope, 'execution_scope': 'UNSTARTED_DISPATCH_ONLY',
                'source_anchors': anchors, 'affected_task_refs': refs, 'state': 'OPEN',
                'caller': caller, 'created_at': stamp()}
            if revision == 1:
                connection.execute('INSERT INTO project_questions(id,project_id,current_revision) VALUES (?,?,?)',
                                   (question_id, project_id, revision))
            else:
                connection.execute('UPDATE project_questions SET current_revision=? WHERE id=?', (revision, question_id))
            connection.execute('INSERT INTO project_question_versions VALUES (?,?,?,?,?,?)',
                (question_id, revision, canonical(payload), digest(payload), semantic, payload['created_at']))
            self._write_refs(connection, question_id, revision, refs)
            self._event(connection, project_id, question_id, 'QUESTION_RECORDED', {'revision': revision, 'sha256': digest(payload)})
            result = self._question(connection, project_id, question_id, revision)
            self._operation(connection, project_id, body.idempotency_key, request_hash, result)
            return result

    def save_answer(self, project_id, question_id, body: AnswerInput, caller_id, answer_id=None):
        self.store.project(project_id)
        caller = self.caller(caller_id)
        if bool(answer_id) != bool(body.expected_answer_revision):
            raise Conflict('Answer correction requires its exact current revision')
        request_hash = digest({'kind': 'answer', 'question_id': question_id, 'answer_id': answer_id,
                               'body': body.model_dump(), 'caller': caller})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = self._replay(connection, project_id, body.idempotency_key, request_hash)
            if replay is not None:
                return replay
            question = self._question(connection, project_id, question_id, body.question_revision)
            if question['sha256'] != body.question_sha256:
                raise Conflict('Answer question hash changed')
            if answer_id:
                old = self._answer(connection, project_id, question_id, answer_id)
                if old['revision'] != body.expected_answer_revision:
                    raise Conflict('Answer changed; reload its current revision')
                if (old['payload']['question_revision'] != body.question_revision
                        or old['payload']['question_sha256'] != body.question_sha256):
                    raise Conflict('Answer correction cannot move its original question scope')
                revision = old['revision']+1
            else:
                revision, answer_id = 1, str(uuid.uuid4())
            q = question['payload']
            intake = IntakeService._version(connection, project_id, q['intake_id'], q['intake_revision'])
            if intake['sha256'] != q['intake_sha256']:
                raise Conflict('Stored question intake scope changed')
            current = (question['current_revision'] == question['revision'] and q['state'] != 'CANCELLED'
                       and self._intake_current(connection, project_id, intake))
            payload = {'schema_version': 'eeas.answer.v1', 'project_id': project_id, 'question_id': question_id,
                'question_revision': question['revision'], 'question_sha256': question['sha256'],
                'answer_id': answer_id, 'revision': revision, 'parent_revision': revision-1,
                'intake_id': q['intake_id'], 'intake_revision': q['intake_revision'], 'intake_sha256': q['intake_sha256'],
                'text': body.text, 'text_sha256': raw_hash(body.text), 'caller': caller,
                'application_status': 'RECORDED' if current else 'REVIEW_REQUIRED', 'created_at': stamp()}
            if revision == 1:
                connection.execute('INSERT INTO project_answers(id,question_id,current_revision) VALUES (?,?,?)',
                                   (answer_id, question_id, revision))
            else:
                connection.execute('UPDATE project_answers SET current_revision=? WHERE id=?', (revision, answer_id))
            connection.execute('INSERT INTO project_answer_versions VALUES (?,?,?,?,?)',
                (answer_id, revision, canonical(payload), digest(payload), payload['created_at']))
            self._event(connection, project_id, question_id, 'ANSWER_RECORDED',
                        {'answer_id': answer_id, 'revision': revision, 'sha256': digest(payload),
                         'question_revision': question['revision'], 'application_status': payload['application_status']})
            result = self._answer(connection, project_id, question_id, answer_id, revision)
            self._operation(connection, project_id, body.idempotency_key, request_hash, result)
            return result

    def cancel(self, project_id, question_id, body: QuestionCancel, caller_id):
        self.store.project(project_id)
        caller = self.caller(caller_id)
        request_hash = digest({'kind': 'cancel', 'question_id': question_id, 'body': body.model_dump(), 'caller': caller})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = self._replay(connection, project_id, body.idempotency_key, request_hash)
            if replay is not None:
                return replay
            old = self._question(connection, project_id, question_id)
            if old['revision'] != body.expected_revision:
                raise Conflict('Question changed; reload its current revision')
            if old['status'] == 'CANCELLED':
                raise Conflict('Question is already cancelled')
            revision = old['revision']+1
            payload = {**old['payload'], 'revision': revision, 'parent_revision': old['revision'],
                'state': 'CANCELLED', 'cancel_reason': body.reason, 'caller': caller, 'created_at': stamp()}
            semantic = connection.execute('SELECT semantic_key FROM project_question_versions WHERE question_id=? AND revision=?',
                                          (question_id, old['revision'])).fetchone()[0]
            connection.execute('INSERT INTO project_question_versions VALUES (?,?,?,?,?,?)',
                (question_id, revision, canonical(payload), digest(payload), semantic, payload['created_at']))
            connection.execute('UPDATE project_questions SET current_revision=? WHERE id=?', (revision, question_id))
            self._write_refs(connection, question_id, revision, payload['affected_task_refs'])
            self._event(connection, project_id, question_id, 'QUESTION_CANCELLED', {'revision': revision, 'sha256': digest(payload)})
            result = self._question(connection, project_id, question_id, revision)
            self._operation(connection, project_id, body.idempotency_key, request_hash, result)
            return result

    def get(self, project_id, question_id, revision=None):
        with self.store.connect() as connection:
            return self._question(connection, project_id, question_id, revision)

    @staticmethod
    def _page(after, limit):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput('Cursor must be nonnegative and page limit between 1 and 100')

    def list(self, project_id, after=0, limit=100):
        self._page(after, limit)
        self.store.project(project_id)
        with self.store.connect() as connection:
            rows = connection.execute('SELECT * FROM project_questions WHERE project_id=? AND seq>? ORDER BY seq LIMIT ?',
                                      (project_id, after, limit)).fetchall()
            result = [self._question(connection, project_id, r['id']) for r in rows]
            return {'questions': result, 'next_after_seq': rows[-1]['seq'] if rows else after}

    def get_answer(self, project_id, question_id, answer_id, revision=None):
        with self.store.connect() as connection:
            return self._answer(connection, project_id, question_id, answer_id, revision)

    def answers(self, project_id, question_id, after=0, limit=100):
        self._page(after, limit)
        with self.store.connect() as connection:
            self._question(connection, project_id, question_id)
            rows = connection.execute('SELECT * FROM project_answers WHERE question_id=? AND seq>? ORDER BY seq LIMIT ?',
                                      (question_id, after, limit)).fetchall()
            return {'answers': [self._answer(connection, project_id, question_id, r['id']) for r in rows],
                    'next_after_seq': rows[-1]['seq'] if rows else after}

    def events(self, project_id, after=0, limit=100):
        self._page(after, limit)
        self.store.project(project_id)
        with self.store.connect() as connection:
            rows = connection.execute('SELECT * FROM project_question_events WHERE project_id=? AND seq>? ORDER BY seq LIMIT ?',
                                      (project_id, after, limit)).fetchall()
            return [{**dict(row), 'payload': checked(row)} for row in rows]

    def knowledge(self, project_id, task_id, revision=None):
        with self.store.connect() as connection:
            task = self.store.task(project_id, task_id, revision)
            result = knowledge_gate(connection, project_id, task_id, task['revision'], task['sha256'])
            return {**result, 'task_review_status': task['review_status'], 'current_task_revision': task['current_revision']}
