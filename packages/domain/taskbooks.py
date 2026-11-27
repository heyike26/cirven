"""Source-bound editable taskbooks; templates are data, never commands."""
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.domain.decisions import IntakeRef, intake_current
from packages.domain.intake import IntakeService, canonical, digest, MAX_CONTEXT_CHARACTERS
from packages.domain.questions import Hash, Id, QuestionAnchor, QuestionService, raw_hash, stamp
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.planning.spec_kit import load_templates, POLICY

KINDS = ('GOAL', 'SCOPE', 'DELIVERABLES', 'ENVIRONMENT', 'INTERFACES', 'DATA', 'DEPENDENCIES',
         'CONSTRAINTS', 'ACCEPTANCE', 'NOTES', 'RISKS', 'OPEN_QUESTIONS', 'ASSUMPTIONS')
HEADINGS = ('目标', '范围', '交付物', '运行环境', '接口', '数据', '依赖', '约束', '验收',
            '注意事项', '风险', '待确认问题', '假设')
MAX_REVISION = 2**63-1


class BookModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('*')
    @classmethod
    def utf8_text(cls, value):
        if isinstance(value, str):
            try:
                value.encode('utf-8')
            except UnicodeError as exc:
                raise ValueError('Text must be valid UTF-8') from exc
            if not value.strip() or '\x00' in value:
                raise ValueError('Text must be nonblank and contain no null bytes')
        return value


class Entry(BookModel):
    entry_id: Id
    text: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARACTERS)
    origin: Literal['SOURCE_FACT', 'USER_STATEMENT', 'MODEL_SUGGESTION', 'ASSUMPTION', 'ACCEPTED_ASSUMPTION']
    source_anchors: list[QuestionAnchor] = Field(default_factory=list, max_length=256)
    acceptance_reason: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode='after')
    def explicit_basis(self):
        if self.origin == 'SOURCE_FACT' and not self.source_anchors:
            raise ValueError('A source fact requires exact source anchors')
        if (self.origin == 'ACCEPTED_ASSUMPTION') != (self.acceptance_reason is not None):
            raise ValueError('An accepted assumption needs its explicit acceptance reason')
        keys = [(a.document_id, a.revision, a.char_start, a.char_end) for a in self.source_anchors]
        if len(set(keys)) != len(keys):
            raise ValueError('Source anchors must be unique')
        return self


class Section(BookModel):
    section_id: Id
    kind: Literal['GOAL', 'SCOPE', 'DELIVERABLES', 'ENVIRONMENT', 'INTERFACES', 'DATA', 'DEPENDENCIES',
                  'CONSTRAINTS', 'ACCEPTANCE', 'NOTES', 'RISKS', 'OPEN_QUESTIONS', 'ASSUMPTIONS']
    heading: str = Field(min_length=1, max_length=200)
    entries: list[Entry] = Field(default_factory=list, max_length=100)


class TemplateRef(BookModel):
    document_id: Id
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    sha256: Hash


class Slot(BookModel):
    slot_id: Id
    section_ids: list[Id] = Field(min_length=1, max_length=13)


class ProviderProfileRef(BookModel):
    profile_id: Id
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    sha256: Hash
    auth_kind: Literal['API_KEY', 'OAUTH_SUBSCRIPTION', 'LOCAL_MODEL']


class TaskBookInput(BookModel):
    idempotency_key: Id
    expected_revision: int = Field(default=0, ge=0, lt=MAX_REVISION, strict=True)
    intake_ref: IntakeRef
    title: str = Field(min_length=1, max_length=200)
    sections: list[Section] = Field(min_length=13, max_length=13)
    template_ref: TemplateRef | None = None
    template_mapping: list[Slot] = Field(default_factory=list, max_length=13)
    provider_profile_ref: ProviderProfileRef | None = None

    @model_validator(mode='after')
    def unique_structure(self):
        if set(s.kind for s in self.sections) != set(KINDS):
            raise ValueError('Every taskbook section kind must appear exactly once')
        if len({s.section_id for s in self.sections}) != 13:
            raise ValueError('Section IDs must be unique')
        entries = [e for s in self.sections for e in s.entries]
        if len({e.entry_id for e in entries}) != len(entries):
            raise ValueError('Entry IDs must be unique across sections')
        if sum(len(e.text) for e in entries) > MAX_CONTEXT_CHARACTERS:
            raise ValueError('Taskbook text exceeds 240000 characters; no text was truncated')
        if self.template_ref is None and self.template_mapping:
            raise ValueError('Template mapping requires an exact template document')
        return self


class TaskBookService:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _version(connection, project_id, book_id, revision=None):
        head = connection.execute('SELECT * FROM project_taskbooks WHERE id=? AND project_id=?',
                                  (book_id, project_id)).fetchone()
        if not head:
            raise NotFound('Taskbook does not exist in this project')
        revision = head['current_revision'] if revision is None else revision
        row = connection.execute('SELECT * FROM project_taskbook_versions WHERE taskbook_id=? AND revision=?',
                                 (book_id, revision)).fetchone()
        if not row:
            raise NotFound('Taskbook revision does not exist')
        payload = json.loads(row['payload'])
        if digest(payload) != row['sha256'] or payload['project_id'] != project_id or payload['taskbook_id'] != book_id or payload['revision'] != revision:
            raise Conflict('Stored taskbook integrity check failed')
        ref = payload['intake_ref']
        intake = IntakeService._version(connection, project_id, ref['intake_id'], ref['revision'])
        current = intake['sha256'] == ref['sha256'] and QuestionService._intake_current(connection, project_id, intake)
        if payload['template_ref']:
            template = payload['template_ref']
            actual = connection.execute('''SELECT d.current_revision,v.sha256 FROM documents d
                JOIN document_versions v ON v.document_id=d.id AND v.revision=d.current_revision
                WHERE d.id=? AND d.project_id=? AND d.deleted_at IS NULL''',
                (template['document_id'], project_id)).fetchone()
            current = current and actual is not None and actual['current_revision'] == template['revision'] and actual['sha256'] == template['sha256']
        return {'id': book_id, 'project_id': project_id, 'revision': revision,
                'current_revision': head['current_revision'], 'sha256': row['sha256'],
                'created_at': row['created_at'], 'payload': payload,
                'review_status': 'CURRENT' if current else 'NEEDS_REVIEW'}

    def get(self, project_id, book_id, revision=None):
        with self.store.connect() as connection:
            return self._version(connection, project_id, book_id, revision)

    def list(self, project_id):
        self.store.project(project_id)
        with self.store.connect() as connection:
            ids = [r[0] for r in connection.execute('SELECT id FROM project_taskbooks WHERE project_id=? ORDER BY id', (project_id,))]
            return [self._version(connection, project_id, book_id) for book_id in ids]

    def template(self):
        try:
            template = load_templates(self.store.source_root)
        except (OSError, ValueError, KeyError) as exc:
            raise Conflict('Pinned Spec Kit source is missing or changed') from exc
        return {'schema_version': 'eeas.taskbook-template.v1',
                'component': {k: template['manifest'][k] for k in ('name', 'version', 'commit', 'integration')},
                'component_manifest_sha256': template['manifest_sha256'],
                'upstream_templates': {name: template['contents'][name] for name in ('spec-template.md', 'plan-template.md', 'tasks-template.md')},
                'instructions': POLICY,
                'sections': [{'section_id': kind.lower(), 'kind': kind, 'heading': heading, 'entries': []}
                             for kind, heading in zip(KINDS, HEADINGS)]}

    @staticmethod
    def _anchors(intake, entry):
        result = []
        for anchor in entry.source_anchors:
            material = next((m for m in intake['snapshot']['materials'] if
                             (m['document_id'], m['revision']) == (anchor.document_id, anchor.revision)), None)
            if not material:
                raise InvalidInput('Anchor is outside the selected intake source')
            chunk = next((c for c in material['chunks'] if c['index'] == anchor.chunk_index), None)
            if not chunk:
                raise InvalidInput('Anchor chunk was not selected')
            if material['raw_sha256'] != anchor.raw_sha256 or chunk['sha256'] != anchor.chunk_sha256 or raw_hash(chunk['content']) != anchor.chunk_sha256:
                raise Conflict('Anchor source SHA changed')
            if not chunk['char_start'] <= anchor.char_start < anchor.char_end <= chunk['char_end']:
                raise InvalidInput('Anchor range is outside the selected chunk')
            quote = chunk['content'][anchor.char_start-chunk['char_start']:anchor.char_end-chunk['char_start']]
            if raw_hash(quote) != anchor.quote_sha256:
                raise Conflict('Anchor quote SHA changed')
            result.append({**anchor.model_dump(), 'quote': quote})
        if entry.origin == 'SOURCE_FACT' and entry.text != ''.join(a['quote'] for a in result):
            raise InvalidInput('A source fact must equal its ordered exact quotes; paraphrases are suggestions or user statements')
        return result

    @staticmethod
    def _current_template(connection, project_id, ref):
        row = connection.execute('''SELECT d.filename,d.current_revision,v.* FROM documents d JOIN document_versions v
            ON v.document_id=d.id AND v.revision=? WHERE d.id=? AND d.project_id=? AND d.deleted_at IS NULL''',
            (ref.revision, ref.document_id, project_id)).fetchone()
        if not row:
            raise NotFound('Template document does not exist in this project')
        if row['current_revision'] != ref.revision or row['sha256'] != ref.sha256 or hashlib.sha256(row['raw_bytes']).hexdigest() != ref.sha256:
            raise Conflict('Template revision or original bytes changed')
        # This slice supports Markdown templates; extracting a DOCX is not layout preservation.
        if PurePosixPath(row['filename']).suffix.lower() not in {'.md', '.txt'} or not row['raw_bytes'] or not row['content']:
            raise InvalidInput('Only nonempty Markdown/TXT taskbook templates are supported')
        return row['raw_bytes'].decode('utf-8-sig')

    @staticmethod
    def _render(title, sections, template, mapping):
        rendered = {}
        for section in sections:
            lines = ['## '+section['heading']+'\n\n']
            if not section['entries']:
                lines.append('待共同确认；尚无依据。\n')
            for entry in section['entries']:
                lines.extend(['['+entry['origin']+']\n\n', entry['text'], '\n\n'])
            rendered[section['section_id']] = ''.join(lines)
        if template is None:
            prefix = '# '+title+'\n\n'
            segments = [(s['section_id'], rendered[s['section_id']]) for s in sections]
            markdown = prefix+''.join(text for _, text in segments)
            position = len(prefix)
            indexes = []
            for sid, text in segments:
                indexes.append((sid, position, position+len(text))); position += len(text)
        else:
            slots = list(re.finditer(r'\{\{taskbook:([A-Za-z0-9_-]{1,100})\}\}', template))
            ids = [m.group(1) for m in slots]
            if not slots or len(set(ids)) != len(ids) or len({m.slot_id for m in mapping}) != len(mapping) or set(ids) != {m.slot_id for m in mapping}:
                raise InvalidInput('Every unique template slot needs exactly one mapping')
            mapped = [sid for slot in mapping for sid in slot.section_ids]
            if len(mapped) != len(sections) or set(mapped) != set(rendered):
                raise InvalidInput('Every section must map exactly once to a template slot')
            by_slot = {slot.slot_id: slot.section_ids for slot in mapping}
            parts, indexes, end, position = [], [], 0, 0
            for slot in slots:
                literal = template[end:slot.start()]; parts.append(literal); position += len(literal)
                for sid in by_slot[slot.group(1)]:
                    text = rendered[sid]; parts.append(text)
                    indexes.append((sid, position, position+len(text))); position += len(text)
                end = slot.end()
            parts.append(template[end:]); markdown = ''.join(parts)
            if '{{taskbook:' in markdown:
                raise InvalidInput('Template contains an unsupported taskbook slot')
        by_id = {s['section_id']: s for s in sections}
        index = [{'section_id': sid, 'kind': by_id[sid]['kind'], 'heading': by_id[sid]['heading'],
                  'char_start': start, 'char_end': end, 'sha256': raw_hash(markdown[start:end])}
                 for sid, start, end in indexes]
        return markdown, index

    def save(self, project_id, body: TaskBookInput, book_id=None, caller_id=None):
        self.store.project(project_id)
        if bool(book_id) != bool(body.expected_revision):
            raise Conflict('Taskbook edits require the exact current revision; new books require zero')
        request_body = body.model_dump(exclude={'idempotency_key'})
        if body.provider_profile_ref is None:
            request_body.pop('provider_profile_ref')
        request_hash = digest({'taskbook_id': book_id, 'body': request_body, 'caller_id': caller_id})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = connection.execute('SELECT * FROM project_taskbook_operations WHERE project_id=? AND idempotency_key=?',
                                        (project_id, body.idempotency_key)).fetchone()
            if replay:
                if replay['payload_sha256'] != request_hash:
                    raise Conflict('IDEMPOTENCY_CONFLICT: taskbook key is bound to another request')
                result = json.loads(replay['result'])
                if digest(result) != replay['sha256']:
                    raise Conflict('Taskbook operation integrity check failed')
                return result
            if book_id:
                old = self._version(connection, project_id, book_id)
                if old['current_revision'] != body.expected_revision:
                    raise Conflict('Taskbook changed; reload its current revision')
            else:
                book_id = str(uuid.uuid4())
            revision = body.expected_revision+1
            intake = intake_current(connection, project_id, body.intake_ref.model_dump())
            pinned = self.template()
            template = None
            if body.template_ref:
                try:
                    template = self._current_template(connection, project_id, body.template_ref)
                except UnicodeError as exc:
                    raise InvalidInput('Only UTF-8 Markdown/TXT templates are supported; binary templates need a separate adapter') from exc
            sections = []
            for s in body.sections:
                entries = [{**e.model_dump(), 'source_anchors': self._anchors(intake, e),
                            'text_sha256': raw_hash(e.text), 'recorded_by': {'kind': 'LOCAL_SESSION', 'id': caller_id}}
                           for e in s.entries]
                sections.append({**s.model_dump(exclude={'entries'}), 'entries': entries,
                                 'status': 'RECORDED' if entries else 'MISSING'})
            markdown, index = self._render(body.title, sections, template, body.template_mapping)
            sources = [{k: m[k] for k in ('document_id', 'revision', 'raw_sha256', 'text_coverage', 'original_modal_coverage')}
                       for m in intake['snapshot']['materials']]
            payload = {'schema_version': 'eeas.taskbook.v1', 'taskbook_id': book_id, 'project_id': project_id,
                       'revision': revision, 'title': body.title, 'intake_ref': body.intake_ref.model_dump(),
                       'source_refs': sources, 'template_ref': body.template_ref.model_dump() if body.template_ref else None,
                       'template_mapping': [s.model_dump() for s in body.template_mapping],
                       'planning_component': pinned['component'], 'component_manifest_sha256': pinned['component_manifest_sha256'],
                       'provider_profile_ref': body.provider_profile_ref.model_dump() if body.provider_profile_ref else None,
                       'provider_binding_status': 'DECLARED_NOT_AUTHENTICATED' if body.provider_profile_ref else 'UNSELECTED',
                       'sections': sections, 'section_index': index, 'markdown': markdown,
                       'markdown_sha256': raw_hash(markdown), 'missing_sections': [s['section_id'] for s in sections if not s['entries']],
                       'clarity_status': 'UNASSESSED', 'model_calls': 0, 'implementation_started': False}
            created = stamp()
            if revision == 1:
                connection.execute('INSERT INTO project_taskbooks VALUES (?,?,?)', (book_id, project_id, revision))
            else:
                connection.execute('UPDATE project_taskbooks SET current_revision=? WHERE id=? AND current_revision=?',
                                   (revision, book_id, body.expected_revision))
            connection.execute('INSERT INTO project_taskbook_versions VALUES (?,?,?,?,?)',
                               (book_id, revision, canonical(payload), digest(payload), created))
            result = self._version(connection, project_id, book_id, revision)
            connection.execute('INSERT INTO project_taskbook_operations VALUES (?,?,?,?,?)',
                               (project_id, body.idempotency_key, request_hash, canonical(result), digest(result)))
            return result
