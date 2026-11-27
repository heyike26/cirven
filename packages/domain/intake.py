"""Immutable material snapshots and prepared planning context; no model calls."""
from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator
from packages.domain.document_chunks import chunks
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.planning.spec_kit import load_templates, POLICY

MAX_CONTEXT_CHARACTERS = 240_000
Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
ChunkIndex = Annotated[int, Field(ge=0, strict=True)]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


class IntakeMaterial(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1, strict=True)
    sha256: Hash
    kind: Literal['MEETING_MINUTES', 'DESIGN_DOCUMENT', 'NOTES', 'OTHER'] = 'OTHER'
    chunk_indexes: list[ChunkIndex] | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode='after')
    def unique_selection(self):
        if self.chunk_indexes is not None:
            if len(set(self.chunk_indexes)) != len(self.chunk_indexes):
                raise ValueError('Chunk indexes must be unique')
            self.chunk_indexes.sort()
        return self


class IntakeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    idempotency_key: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    expected_revision: int = Field(default=0, ge=0, strict=True)
    materials: list[IntakeMaterial] = Field(min_length=1, max_length=20)
    purpose: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def unique_materials(self):
        if len({m.document_id for m in self.materials}) != len(self.materials):
            raise ValueError('Document IDs must be unique within one source snapshot')
        return self


class IntakeService:
    def __init__(self, store, source_root):
        self.store, self.source_root = store, source_root

    @staticmethod
    def _version(connection, project_id, intake_id, revision=None):
        parent = connection.execute('SELECT * FROM project_intakes WHERE id=? AND project_id=?',
                                    (intake_id, project_id)).fetchone()
        if parent is None:
            raise NotFound('Intake does not exist in this project')
        revision = parent['current_revision'] if revision is None else revision
        row = connection.execute('SELECT * FROM project_intake_versions WHERE intake_id=? AND revision=?',
                                 (intake_id, revision)).fetchone()
        if row is None:
            raise NotFound('Intake revision does not exist')
        payload = json.loads(row['payload'])
        if digest(payload) != row['sha256']:
            raise Conflict('Stored intake snapshot integrity check failed')
        return {'id': intake_id, 'project_id': project_id, 'revision': revision,
                'current_revision': parent['current_revision'], 'sha256': row['sha256'],
                'created_at': row['created_at'], 'snapshot': payload}

    def get(self, project_id, intake_id, revision=None):
        with self.store.connect() as connection:
            return self._version(connection, project_id, intake_id, revision)

    def save(self, project_id, body: IntakeInput, intake_id=None):
        self.store.project(project_id)
        if intake_id is None and body.expected_revision != 0:
            raise Conflict('New intake expected_revision must be zero')
        if intake_id is not None and body.expected_revision < 1:
            raise Conflict('Intake update requires its current revision')
        request_hash = digest({'intake_id': intake_id, **body.model_dump(exclude={'idempotency_key'})})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            operation = connection.execute('SELECT * FROM project_intake_operations WHERE project_id=? AND idempotency_key=?',
                                           (project_id, body.idempotency_key)).fetchone()
            if operation:
                if operation['payload_sha256'] != request_hash:
                    raise Conflict('IDEMPOTENCY_CONFLICT: key already used with different payload')
                receipt = self._version(connection, project_id, operation['intake_id'], operation['revision'])
                # Replay the operation-era response. GET remains the live head.
                receipt['current_revision'] = operation['revision']
                return receipt
            if intake_id is not None:
                old = self._version(connection, project_id, intake_id)
                if old['current_revision'] != body.expected_revision:
                    raise Conflict('Intake changed; reload its current revision')
                revision = body.expected_revision + 1
            else:
                intake_id, revision = str(uuid.uuid4()), 1
            materials, total = [], 0
            for ref in body.materials:
                row = connection.execute('''SELECT d.filename,d.current_revision,v.* FROM documents d
                    JOIN document_versions v ON v.document_id=d.id
                    WHERE d.id=? AND d.project_id=? AND d.deleted_at IS NULL AND v.revision=?''',
                    (ref.document_id, project_id, ref.revision)).fetchone()
                if row is None:
                    raise NotFound('Source document revision does not exist in this project')
                if ref.sha256 != row['sha256'] or hashlib.sha256(row['raw_bytes']).hexdigest() != ref.sha256:
                    raise Conflict('Source document raw SHA does not match its immutable bytes')
                parsed = self.store._parse_metadata(connection, ref.document_id, ref.revision)
                content_hash = hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
                if parsed.get('content_sha256', content_hash) != content_hash:
                    raise Conflict('Source derived text integrity check failed')
                windows = chunks(row['content'])
                indexes = list(range(len(windows))) if ref.chunk_indexes is None else ref.chunk_indexes
                if not indexes or any(i >= len(windows) for i in indexes):
                    raise InvalidInput('Select existing nonempty document chunks')
                selected = [windows[i] for i in indexes]
                count = sum(len(w['content']) for w in selected)
                total += count
                if total > MAX_CONTEXT_CHARACTERS:
                    raise InvalidInput('Selected text exceeds 240000 characters; choose explicit chunks. No text was truncated')
                assets = self.store._document_asset_metadata(connection, ref.document_id, ref.revision,
                                                              parsed.get('outline', {}))
                receipt = self.store._document_parse_receipt(parsed, content_hash, assets)
                complete_text = len(selected) == len(windows)
                warnings = parsed.get('warnings', [])
                materials.append({'document_id': ref.document_id, 'revision': ref.revision,
                    'raw_sha256': ref.sha256, 'filename_at_snapshot': row['filename'], 'kind_hint': ref.kind,
                    'current_revision_at_snapshot': row['current_revision'], 'source': row['source'],
                    **receipt,
                    'format': parsed.get('format'), 'warnings': warnings,
                    'assets': assets, 'asset_contents_sent': False, 'visual_understanding': 'UNASSESSED',
                    'source_characters': len(row['content']), 'selected_characters': count,
                    'text_coverage': 'FULL_PARSED_TEXT' if complete_text else 'SELECTED_CHUNKS',
                    'original_modal_coverage': 'UNASSESSED',
                    'omitted_chunk_indexes': [w['index'] for w in windows if w['index'] not in indexes],
                    'chunks': selected})
            snapshot = {'schema_version': 'eeas.intake-source-snapshot.v1', 'purpose': body.purpose,
                        'materials': materials, 'selected_characters': total,
                        'clarity_status': 'UNASSESSED', 'implementation_started': False}
            stamp = datetime.now(timezone.utc).isoformat()
            if revision == 1:
                connection.execute('INSERT INTO project_intakes VALUES (?,?,?)', (intake_id, project_id, revision))
            else:
                connection.execute('UPDATE project_intakes SET current_revision=? WHERE id=?', (revision, intake_id))
            snapshot_hash = digest(snapshot)
            connection.execute('INSERT INTO project_intake_versions VALUES (?,?,?,?,?)',
                               (intake_id, revision, canonical(snapshot), snapshot_hash, stamp))
            connection.execute('INSERT INTO project_intake_operations VALUES (?,?,?,?,?)',
                               (project_id, body.idempotency_key, request_hash, intake_id, revision))
            connection.execute('''INSERT INTO project_intake_events
                (project_id,intake_id,revision,kind,snapshot_sha256,created_at) VALUES (?,?,?,?,?,?)''',
                (project_id, intake_id, revision, 'SOURCE_SNAPSHOT_CREATED', snapshot_hash, stamp))
            return self._version(connection, project_id, intake_id, revision)

    def events(self, project_id, after=0, limit=100):
        self.store.project(project_id)
        with self.store.connect() as connection:
            return [dict(r) for r in connection.execute('''SELECT * FROM project_intake_events
                WHERE project_id=? AND seq>? ORDER BY seq LIMIT ?''', (project_id, after, limit))]

    def context(self, project_id, intake_id, revision=None):
        intake = self.get(project_id, intake_id, revision)
        try:
            templates = load_templates(self.source_root)
        except (OSError, ValueError, KeyError) as e:
            raise Conflict('Planning component is missing or changed; restore the pinned offline source') from e
        materials = intake['snapshot']['materials']
        result = {'schema_version': 'eeas.prepared-planning-context.v1',
            'intake_id': intake_id, 'revision': intake['revision'], 'source_snapshot_sha256': intake['sha256'],
            'component': {k: templates['manifest'][k] for k in ('name', 'version', 'commit', 'integration')},
            'component_manifest_sha256': templates['manifest_sha256'],
            'instructions': POLICY, 'specification_template': templates['contents']['spec-template.md'],
            'coverage_taxonomy': templates['contents']['clarity-taxonomy.txt'],
            'purpose': intake['snapshot']['purpose'], 'materials': materials,
            'clarity_status': 'UNASSESSED', 'model_calls': 0, 'implementation_started': False,
            'material_status': [{'document_id': m['document_id'], 'revision': m['revision'],
                'text_coverage': m['text_coverage'], 'original_modal_coverage': m['original_modal_coverage']}
                for m in materials]}
        result['context_sha256'] = digest(result)
        return result
