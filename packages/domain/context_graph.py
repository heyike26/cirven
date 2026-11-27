"""Thin, local, source-pinned context index. Never owns files or executions.

Selections persist references and omission reasons, never a second transcript.
All model-facing content is re-read from the original service. A note is data,
not an instruction, permission, installed skill, or proof of execution.
"""
from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator, model_serializer

from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.conversation_file_input import ConversationFileLink
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest
from packages.domain.context_read_budget import context_read_scope, current_context_read_budget, ContextReadLimit
from packages.domain.context_file_models import FileExclusionRef
from packages.domain.context_file_relations import ContextFileRelations
from packages.domain.context_navigation import ContextNavigation
from packages.domain.context_conversations import ContextConversations
from packages.domain.context_policy_models import DEFAULT_POLICY, Policy
from packages.domain.context_policy_defaults import ContextPolicyDefaults
from packages.domain.context_source_grants import ContextSourceGrants
from packages.domain.conversation_source_models import ConversationSourceAnchor

Revision = Annotated[int, Field(strict=True, ge=0)]
ConversationId = Annotated[str, Field(pattern=r'^(native|original|discovery):[a-f0-9]{64}$')]
MAX_ORIGINS = 4096
MAX_ASSOCIATIONS = 8192
MAX_SCOPES = 512
MAX_DEPTH = 12
MAX_STORAGE_BYTES = 64 * 1024 * 1024


def stamp():
    return datetime.now(timezone.utc).isoformat()


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class ScopeChange(RequestModel):
    operation_id: Id
    expected_revision: Revision
    project_id: Id | None = None
    parent_id: Id | None = None
    kind: Literal['GLOBAL', 'PROJECT', 'TOPIC', 'TASK']
    label: str = Field(min_length=1, max_length=160)
    policy: Policy = Field(default_factory=Policy)


class CommandContextOwner(RequestModel):
    session_id: Id
    session_sha256: Hash
    generation: StrictInt = Field(ge=1)
    workspace_id: Hash
    binding_revision: StrictInt = Field(ge=1)
    binding_sha256: Hash


class SourceRef(RequestModel):
    kind: Literal['DOCUMENT', 'FILE_SNAPSHOT', 'CONVERSATION_MESSAGE', 'COMMAND_OBSERVATION', 'NOTE', 'NOTE_FRAGMENT']
    project_id: Id | None = None
    object_id: Id | None = None
    revision: StrictInt | None = Field(default=None, ge=1)
    sha256: Hash | None = None
    conversation_id: ConversationId | None = None
    message_id: Hash | None = None
    offset: StrictInt = Field(default=0, ge=0, le=10_000_000)
    length: StrictInt = Field(default=4000, ge=1, le=16000)
    command_owner: CommandContextOwner | None = None
    cursor: str | None = Field(default=None, max_length=2048)
    link_ref: ConversationFileLink | None = None
    file_identity_sha256: Hash | None = None
    source_anchor: ConversationSourceAnchor | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_identity(self, handler):
        value = handler(self)
        if self.source_anchor is None:
            value.pop('source_anchor', None)
        return value

    @model_validator(mode='after')
    def exact_source(self):
        if self.kind in ('DOCUMENT', 'FILE_SNAPSHOT'):
            if not all((self.project_id, self.object_id, self.revision, self.sha256)):
                raise ValueError('Select an exact project, object, revision and SHA')
            if self.kind == 'FILE_SNAPSHOT' and (self.link_ref is None or self.link_ref.project_id != self.project_id or not self.file_identity_sha256):
                raise ValueError('Select the exact registered file link for this snapshot')
        elif self.kind == 'CONVERSATION_MESSAGE':
            if not all((self.conversation_id, self.message_id, self.sha256)):
                raise ValueError('Select an exact conversation message and text SHA')
        elif self.kind == 'COMMAND_OBSERVATION':
            if not all((self.project_id, self.object_id, self.revision, self.sha256, self.command_owner)):
                raise ValueError('Select an exact original command record, owner and workspace')
        elif not self.object_id:
            raise ValueError('Select an existing note identity')
        if self.kind == 'NOTE_FRAGMENT' and not self.sha256:
            raise ValueError('Select the exact reviewed note fingerprint')
        irrelevant = {
            'DOCUMENT': (self.conversation_id, self.message_id, self.command_owner, self.cursor),
            'FILE_SNAPSHOT': (self.conversation_id, self.message_id, self.command_owner, self.cursor),
            'CONVERSATION_MESSAGE': (self.object_id, self.revision, self.command_owner),
            'COMMAND_OBSERVATION': (self.conversation_id, self.message_id, self.cursor),
            'NOTE': (self.revision, self.sha256, self.conversation_id, self.message_id, self.command_owner, self.cursor),
            'NOTE_FRAGMENT': (self.revision, self.conversation_id, self.message_id, self.command_owner, self.cursor),
        }[self.kind]
        if any(value is not None for value in irrelevant):
            raise ValueError('Source fields belong to another source kind')
        if self.kind != 'FILE_SNAPSHOT' and self.link_ref is not None:
            raise ValueError('File link belongs only to a saved-file snapshot')
        if self.kind != 'FILE_SNAPSHOT' and self.file_identity_sha256 is not None:
            raise ValueError('File identity belongs only to a saved-file snapshot')
        if self.source_anchor is not None and (self.kind != 'CONVERSATION_MESSAGE' or self.cursor is not None):
            raise ValueError('Original message anchors cannot be combined with another source kind or a page cursor')
        return self


class AssociationChange(RequestModel):
    operation_id: Id
    expected_revision: Revision
    scope_id: Id
    source: SourceRef
    conversation_id: ConversationId | None = None
    state: Literal['CANDIDATE', 'ACTIVE', 'REJECTED', 'RETRACTED', 'EXCLUDED'] = 'ACTIVE'
    manual_locked: StrictBool = True
    restore_file_exclusions: list[FileExclusionRef] = Field(default_factory=list, max_length=128)

    @model_validator(mode='after')
    def explicit_restoration(self):
        if len({item.id for item in self.restore_file_exclusions}) != len(self.restore_file_exclusions):
            raise ValueError('File exclusion references must be unique')
        if self.restore_file_exclusions and (self.source.kind != 'FILE_SNAPSHOT' or self.state != 'ACTIVE' or not self.manual_locked):
            raise ValueError('Restoration requires an explicit active manual file association')
        return self


class NoteChange(RequestModel):
    operation_id: Id
    scope_id: Id
    text: str = Field(min_length=1, max_length=8000)
    title: str = Field(min_length=1, max_length=160)
    basis: list[Hash] = Field(default_factory=list, max_length=32)
    basis_grants: list['NoteGrantRef'] = Field(default_factory=list, max_length=32)


class NoteGrantRef(RequestModel):
    origin_id: Hash
    id: Hash
    revision: StrictInt = Field(ge=1)


NoteChange.model_rebuild()


class SourceReadPreview(RequestModel):
    scope_id: Id
    source: SourceRef
    conversation_id: ConversationId | None = None


class SourceReadChange(AssociationChange):
    expected_grant_revision: Revision
    review_sha256: Hash

    @model_validator(mode='after')
    def human_review(self):
        if not self.manual_locked or self.source.kind == 'NOTE':
            raise ValueError('Read-only sharing requires explicit review of the exact source fragment')
        return self


class SelectionInput(RequestModel):
    operation_id: Id
    scope_id: Id
    conversation_id: ConversationId | None = None
    char_budget: StrictInt = Field(default=8000, ge=0, le=16000)
    limit: StrictInt = Field(default=12, ge=1, le=32)
    query: str | None = Field(default=None, max_length=200)
    include_global: StrictBool = False


class ForgetInput(RequestModel):
    operation_id: Id
    origin_id: Hash
    scope_id: Id


class ContextGraph(ContextFileRelations, ContextNavigation, ContextConversations, ContextPolicyDefaults, ContextSourceGrants):
    def __init__(self, store, source_reader, conversation_reader):
        self.store, self.source_reader, self.conversation_reader = store, source_reader, conversation_reader

    def file_source(self, snapshot_ref, link_ref, *, offset=0, length=4000):
        adapter = getattr(self.source_reader, '__self__', None)
        if adapter is None or not hasattr(adapter, 'file_locator'):
            raise Conflict('Exact file location adapter is unavailable')
        link = ConversationFileLink.model_validate(link_ref).model_dump()
        if snapshot_ref['project_id'] != link['project_id']:
            raise Conflict('Snapshot and link belong to different projects')
        location = adapter.file_locator(link)
        return SourceRef(kind='FILE_SNAPSHOT', project_id=link['project_id'], link_ref=link,
            object_id=snapshot_ref['id'], revision=snapshot_ref['revision'], sha256=snapshot_ref['sha256'],
            file_identity_sha256=location['file_key'], offset=offset, length=length)

    @staticmethod
    def _scope(db, ident):
        row = db.execute('SELECT * FROM context_scopes WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise NotFound('Context scope does not exist')
        return dict(row)

    @staticmethod
    def _receipt(row):
        result = json.loads(row['result_json'])
        if digest(result) != row['result_sha256']:
            raise Conflict('Context operation receipt integrity differs')
        return result

    def operation(self, operation_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM context_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None:
            raise NotFound('No committed context operation; inspect before retrying the same operation')
        return self._receipt(row)

    @context_read_scope()
    def _mutate(self, kind, request, apply):
        body = request.model_dump()
        # Empty optional grant references preserve pre-upgrade note identities.
        if kind == 'note' and not body.get('basis_grants'):
            body.pop('basis_grants', None)
        if kind == 'conversation_link' and body.get('navigation_only') is False:
            body.pop('navigation_only', None)
        request_hash = digest({'kind': kind, 'body': body})
        revoking = (kind == 'forget' or kind in {'association', 'read_grant'} and request.state in {'EXCLUDED', 'RETRACTED', 'REJECTED'}
                    or kind == 'folder_rule' and request.state in {'RETRACTED', 'REJECTED'}
                    or kind == 'conversation_link' and request.state in {'RETRACTED', 'REJECTED'}
                    or kind == 'conversation_preferences' and (request.retrieval_mode == 'OFF' or request.retrieve is False or request.capture is False)
                    or (kind.startswith('scope:') or kind == 'global_defaults') and (request.policy.retrieve is False or request.policy.capture is False or request.policy.char_budget == 0))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM context_operations WHERE operation_id=?', (request.operation_id,)).fetchone()
            if old:
                if old['request_sha256'] != request_hash:
                    raise Conflict('Context operation is already bound to another request')
                return self._receipt(old)
            self._bound(db, 'context_operations', 32768 if kind == 'forget' else 24576 if revoking else 16384)
            result = {'version': 1, 'operation_id': request.operation_id, 'operation_kind': kind,
                      'request_sha256': request_hash, **apply(db)}
            db.execute('UPDATE context_authority_epoch SET revision=revision+1 WHERE singleton=1')
            if kind != 'forget':
                # Charge both frozen-selection and receipt copies, all request
                # data which may be owned here, plus row/index overhead. This
                # is a conservative logical quota, not a disk-free-space claim.
                cost = len(canonical(request.model_dump()).encode()) + 2 * len(canonical(result).encode()) + 2048
                reserved = db.execute('SELECT reserved_bytes FROM context_storage_budget WHERE singleton=1').fetchone()[0]
                limit = MAX_STORAGE_BYTES + (16 * 1024 * 1024 if revoking else 0)
                if reserved + cost > limit:
                    raise Conflict('Local context storage budget reached; originals remain untouched')
                db.execute('UPDATE context_storage_budget SET reserved_bytes=? WHERE singleton=1', (reserved + cost,))
            # No user text is copied to immutable receipts; forgetting can scrub
            # the only owned note body without rewriting execution history.
            db.execute('INSERT INTO context_operations VALUES (?,?,?,?)',
                       (request.operation_id, request_hash, canonical(result), digest(result)))
            if kind == 'forget':
                # Forgetting remains available at capacity. Recount only on
                # this explicit operation; SQLite can reuse freed pages later.
                tables = {
                    'context_scopes': ('id','project_id','parent_id','kind','label','policy_json','updated_at'),
                    'context_origins': ('id','project_id','kind','locator_json','fingerprint','title','created_at'),
                    'context_notes': ('origin_id','text_content','basis_json'),
                    'context_associations': ('id','scope_id','origin_id','conversation_id','state','updated_at'),
                    'context_operations': ('operation_id','request_sha256','result_json','result_sha256'),
                    'context_selections': ('id','scope_id','conversation_id','selection_json','selection_sha256','created_at'),
                    'context_origin_files': ('origin_id','file_key','locator_json','locator_sha256'),
                    'context_folder_rules': ('id','scope_id','conversation_id','folder_json','folder_sha256','range_kind','state','updated_at'),
                    'context_file_exclusions': ('id','scope_id','conversation_id','file_key','state','association_id','updated_at'),
                    'context_conversation_links': ('id','scope_id','conversation_id','conversation_ref_json','state','updated_at'),
                    'context_conversation_preferences': ('conversation_id','preferred_scope_id','retrieval_mode','policy_json','updated_at'),
                    'context_policy_defaults': ('policy_json','updated_at'),
                    'context_read_grants': ('id','scope_id','conversation_id','origin_id','source_fingerprint','state','review_sha256','updated_at'),
                    'context_note_grants': ('note_origin_id','dependency_origin_id','grant_id','note_scope_id'),
                    'context_fragment_bases': ('origin_id','base_origin_id'),
                }
                total = 0
                for table, columns in tables.items():
                    expression = '+'.join('coalesce(length(cast(' + name + ' AS blob)),0)' for name in columns)
                    total += db.execute(f'SELECT coalesce(sum({expression}+512),0) FROM {table}').fetchone()[0]
                db.execute('UPDATE context_storage_budget SET reserved_bytes=? WHERE singleton=1', (total,))
            return result

    @staticmethod
    def _bound(db, table, maximum):
        # Identifiers below are internal constants, never request input.
        if db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] >= maximum:
            raise Conflict('Local context index capacity reached; narrow the capture scope')

    def _ancestry(self, db, ident):
        result, seen = [], set()
        while ident is not None:
            if ident in seen or len(result) >= MAX_DEPTH:
                raise Conflict('Context ancestry contains a cycle or exceeds the depth budget')
            seen.add(ident)
            row = self._scope(db, ident)
            result.append(row)
            ident = row['parent_id']
        return result

    def _policy(self, db, ident):
        effective, basis = dict(DEFAULT_POLICY), {key: 'DEFAULT' for key in DEFAULT_POLICY}
        defaults = self._global_defaults(db)
        for key, value in defaults['policy'].items():
            effective[key], basis[key] = value, 'GLOBAL_DEFAULTS'
        for scope in reversed(self._ancestry(db, ident)):
            for key, value in json.loads(scope['policy_json']).items():
                effective[key], basis[key] = value, scope['id']
        return effective, basis

    def scope(self, ident):
        with self.store.connect() as db:
            row = self._scope(db, ident)
            effective, basis = self._policy(db, ident)
        row['policy'] = json.loads(row.pop('policy_json'))
        return {**row, 'effective_policy': effective, 'policy_basis': basis}

    def scopes(self, project_id=None):
        if project_id is not None:
            self.store.project(project_id)
        with self.store.connect() as db:
            rows = db.execute('SELECT id FROM context_scopes WHERE project_id IS ? ORDER BY id LIMIT ?',
                              (project_id, MAX_SCOPES)).fetchall()
        return [self.scope(row['id']) for row in rows]

    def save_scope(self, ident, request):
        def apply(db):
            old = db.execute('SELECT * FROM context_scopes WHERE id=?', (ident,)).fetchone()
            if (old['revision'] if old else 0) != request.expected_revision:
                raise Conflict('Context scope changed; refresh before applying')
            if old and (old['project_id'], old['kind']) != (request.project_id, request.kind):
                raise Conflict('Scope identity cannot migrate projects or change kind')
            if request.project_id:
                self.store.project(request.project_id)
            if request.kind == 'GLOBAL' and (request.parent_id or request.project_id):
                raise InvalidInput('Global scope has no parent or project')
            if request.kind == 'PROJECT' and (not request.project_id or request.parent_id):
                raise InvalidInput('Project scope requires its real project and no implicit global parent')
            if request.kind in ('TOPIC', 'TASK'):
                if not request.parent_id:
                    raise InvalidInput('Topic/task needs a parent scope')
                parents = self._ancestry(db, request.parent_id)
                if any(p['id'] == ident for p in parents) or len(parents) >= MAX_DEPTH:
                    raise Conflict('Context parent would create a cycle or exceed depth')
                if any(p['project_id'] != request.project_id for p in parents):
                    raise Conflict('Context parent belongs to a different project')
            if not old:
                self._bound(db, 'context_scopes', MAX_SCOPES)
            revision = request.expected_revision + 1
            db.execute('INSERT INTO context_scopes VALUES (?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id,label=excluded.label,'
                       'revision=excluded.revision,policy_json=excluded.policy_json,updated_at=excluded.updated_at',
                       (ident, request.project_id, request.parent_id, request.kind, request.label, revision,
                        canonical(request.policy.model_dump(exclude_none=True)), stamp()))
            return {'scope_id': ident, 'revision': revision}
        return self._mutate('scope:' + ident, request, apply)

    def _conversation(self, project_id, ident):
        if ident is not None:
            scope = self.conversation_reader(ident)
            if scope['project_id'] != project_id or scope.get('deleted_at'):
                raise Conflict('Conversation belongs to another project or was removed')

    @staticmethod
    def _origin(db, ident):
        row = db.execute('SELECT * FROM context_origins WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise NotFound('Context origin does not exist')
        if row['forgotten']:
            raise Conflict('Context origin was explicitly forgotten')
        try:
            intact = digest(json.loads(row['locator_json'])) == row['id']
        except (ValueError, TypeError):
            intact = False
        if not intact:
            raise Conflict('Context origin identity integrity differs')
        return dict(row)

    def _read(self, db, ident, seen=None, *, blocked=(), budget=None, target_scope_id=None):
        seen = set() if seen is None else set(seen)
        budget = [64] if budget is None else budget
        budget[0] -= 1
        if budget[0] < 0 or ident in blocked:
            raise Conflict('Context dependency is excluded or its read budget was reached')
        if ident in seen or len(seen) >= 8:
            raise Conflict('Derived context dependency budget exceeded')
        seen.add(ident)
        row = self._origin(db, ident)
        locator = json.loads(row['locator_json'])
        if row['kind'] == 'NOTE':
            note = db.execute('SELECT * FROM context_notes WHERE origin_id=?', (ident,)).fetchone()
            allowance = current_context_read_budget()
            if allowance is not None and note is not None:
                key = ('note', ident, row['fingerprint'])
                if key not in allowance.cache:
                    allowance.charge(len(note['text_content'].encode()) + len(note['basis_json'].encode()))
                    allowance.cache[key] = True
            if note is None or text_hash(note['text_content']) != row['fingerprint']:
                raise Conflict('Context note integrity differs')
            self._validate_note_grants(db, ident, target_scope_id=target_scope_id)
            for dependency in json.loads(note['basis_json']):
                self._read(db, dependency, seen, blocked=blocked, budget=budget, target_scope_id=target_scope_id)
            return {'text': note['text_content'], 'fingerprint': row['fingerprint'], 'title': row['title']}
        if row['kind'] == 'NOTE_FRAGMENT':
            value = self._read_reference(db, SourceRef.model_validate(locator), seen=seen, blocked=blocked, budget=budget,
                                         target_scope_id=target_scope_id)
            if value['fingerprint'] != row['fingerprint']:
                raise Conflict('Reviewed note fragment changed')
            return value
        try:
            value = self.source_reader(SourceRef.model_validate(locator))
        except (InvalidInput, OSError, ValueError, TypeError, KeyError):
            raise Conflict('Context source is no longer verifiable') from None
        if value['fingerprint'] != row['fingerprint']:
            raise Conflict('Context source changed; review its current version')
        return value

    def associate(self, request):
        def apply(db):
            scope = self._scope(db, request.scope_id)
            if request.source.project_id != scope['project_id']:
                raise Conflict('Cross-project sources require a reviewed read-only grant change')
            withdrawing = request.state in {'EXCLUDED', 'RETRACTED', 'REJECTED'}
            if not withdrawing:
                self._conversation(scope['project_id'], request.conversation_id)
            if request.source.kind == 'NOTE':
                origin = self._origin(db, request.source.object_id)
                if origin['project_id'] != scope['project_id']:
                    raise Conflict('Note belongs to another project')
                oid = origin['id']
                if not withdrawing:
                    self._read(db, oid, target_scope_id=request.scope_id)
            else:
                locator = request.source.model_dump()
                oid = digest(locator)
                existing = db.execute('SELECT * FROM context_origins WHERE id=?', (oid,)).fetchone()
                if withdrawing and not existing:
                    raise Conflict('Withdraw only an already registered exact source')
                if existing and existing['forgotten'] and (request.state != 'ACTIVE' or not request.manual_locked):
                    raise Conflict('Restoring a forgotten source requires an explicit manual association')
                source = None if withdrawing else self._read_reference(db, request.source, target_scope_id=request.scope_id)
                if not existing:
                    self._bound(db, 'context_origins', MAX_ORIGINS)
                    db.execute('INSERT INTO context_origins VALUES (?,?,?,?,?,?,0,?)',
                        (oid, scope['project_id'], request.source.kind, canonical(locator),
                         source['fingerprint'], source['title'][:160], stamp()))
                elif existing['forgotten']:
                    # This API is the user's explicit manual action. Old edges
                    # and forgotten derived note bodies remain withdrawn.
                    db.execute('UPDATE context_origins SET forgotten=0,title=?,fingerprint=? WHERE id=?',
                               (source['title'][:160], source['fingerprint'], oid))
                if request.source.kind == 'NOTE_FRAGMENT' and not withdrawing:
                    db.execute('INSERT OR IGNORE INTO context_fragment_bases VALUES (?,?)', (oid, request.source.object_id))
            file_key = None
            if request.source.kind == 'FILE_SNAPSHOT':
                if withdrawing:
                    location = self._origin_location(db, oid)
                    if location is None or location['file_key'] != request.source.file_identity_sha256:
                        raise Conflict('Registered file identity is unavailable')
                    file_key = location['file_key']
                else:
                    file_key = self._pin_origin_file(db, oid, request.source)
            aid = digest([request.scope_id, oid, request.conversation_id])
            old = db.execute('SELECT * FROM context_associations WHERE id=?', (aid,)).fetchone()
            if (old['revision'] if old else 0) != request.expected_revision:
                raise Conflict('Context association changed; preserve the newer manual decision')
            if not old:
                self._bound(db, 'context_associations', MAX_ASSOCIATIONS)
            now = stamp()
            if file_key and request.state == 'ACTIVE':
                if not request.manual_locked and self._file_exclusions(db,
                        [row['id'] for row in self._ancestry(db, request.scope_id)], request.conversation_id, file_key):
                    raise Conflict('Manual file exclusion requires explicit restoration')
                self._restore_file_exclusions(db, request, file_key, now)
            revision = request.expected_revision + 1
            db.execute('INSERT INTO context_associations VALUES (?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET state=excluded.state,manual_locked=excluded.manual_locked,'
                'revision=excluded.revision,updated_at=excluded.updated_at',
                (aid, request.scope_id, oid, request.conversation_id, request.state,
                 int(request.manual_locked), revision, now))
            if file_key and request.state == 'EXCLUDED':
                self._exclude_file(db, request, aid, file_key, now)
            return {'association_id': aid, 'origin_id': oid, 'revision': revision, 'state': request.state,
                    'execution_authorized': False, 'files_changed': False}
        return self._mutate('association', request, apply)

    def propose(self, request):
        """Incremental exact-source event, never semantic guessing or a scan.

        Automatic proposals can add a new edge. They cannot edit an existing
        edge, clear manual exclusions, or turn a rejected basis back on.
        """
        def apply(db):
            scope = self._scope(db, request.scope_id)
            if request.source.project_id != scope['project_id']:
                raise Conflict('Proposed source belongs to another project')
            self._conversation(scope['project_id'], request.conversation_id)
            policy, _, _ = self._retrieval_policy(db, request.scope_id, request.conversation_id)
            if not policy['capture'] or policy['archive_mode'] == 'MANUAL':
                return {'proposal_status': 'SKIPPED_POLICY', 'source_read': False, 'model_calls': 0}
            if request.source.kind == 'NOTE':
                oid = request.source.object_id
                origin = self._origin(db, oid)
                if origin['project_id'] != scope['project_id']:
                    raise Conflict('Proposed note belongs to another project')
            else:
                locator = request.source.model_dump()
                oid = digest(locator)
                origin = db.execute('SELECT * FROM context_origins WHERE id=?', (oid,)).fetchone()
                if origin and origin['forgotten']:
                    return {'proposal_status': 'SKIPPED_FORGOTTEN', 'origin_id': oid, 'model_calls': 0}
            aid = digest([request.scope_id, oid, request.conversation_id])
            old = db.execute('SELECT * FROM context_associations WHERE id=?', (aid,)).fetchone()
            if old:
                return {'proposal_status': 'EXISTING_DECISION_RETAINED', 'association_id': aid,
                        'revision': old['revision'], 'state': old['state'], 'model_calls': 0}
            scope_ids = [row['id'] for row in self._ancestry(db, request.scope_id)]
            if request.source.kind == 'FILE_SNAPSHOT' and self._file_exclusions(
                    db, scope_ids, request.conversation_id, request.source.file_identity_sha256):
                return {'proposal_status': 'SKIPPED_MANUAL_FILE_EXCLUSION', 'origin_id': oid, 'model_calls': 0}
            placeholders = ','.join('?' for _ in scope_ids)
            rejected = db.execute(f'SELECT 1 FROM context_associations WHERE scope_id IN ({placeholders}) '
                'AND origin_id=? AND state IN ("EXCLUDED","REJECTED") AND manual_locked=1 '
                'AND (conversation_id IS NULL OR conversation_id IS ?) LIMIT 1',
                (*scope_ids, oid, request.conversation_id)).fetchone()
            if rejected:
                return {'proposal_status': 'SKIPPED_MANUAL_DECISION', 'origin_id': oid, 'model_calls': 0}
            if request.source.kind == 'NOTE':
                self._read(db, oid, target_scope_id=request.scope_id)
            else:
                source = self._read_reference(db, request.source, target_scope_id=request.scope_id)
                if not origin:
                    self._bound(db, 'context_origins', MAX_ORIGINS)
                    db.execute('INSERT INTO context_origins VALUES (?,?,?,?,?,?,0,?)',
                        (oid, scope['project_id'], request.source.kind, canonical(locator),
                         source['fingerprint'], source['title'][:160], stamp()))
                self._pin_origin_file(db, oid, request.source)
                if request.source.kind == 'NOTE_FRAGMENT':
                    db.execute('INSERT OR IGNORE INTO context_fragment_bases VALUES (?,?)', (oid, request.source.object_id))
            self._bound(db, 'context_associations', MAX_ASSOCIATIONS)
            state = 'ACTIVE' if policy['archive_mode'] == 'AUTO' else 'CANDIDATE'
            db.execute('INSERT INTO context_associations VALUES (?,?,?,?,?,?,?,?)',
                (aid, request.scope_id, oid, request.conversation_id, state, 0, 1, stamp()))
            return {'proposal_status': 'ADDED', 'association_id': aid, 'origin_id': oid,
                    'revision': 1, 'state': state, 'model_calls': 0, 'execution_authorized': False}
        return self._mutate('proposal', request, apply)

    def note(self, request):
        def apply(db):
            scope = self._scope(db, request.scope_id)
            policy, _ = self._policy(db, request.scope_id)
            if not policy['capture']:
                raise Conflict('Memory capture is disabled in this scope')
            if len(set(request.basis)) != len(request.basis):
                raise InvalidInput('Memory dependencies must be unique')
            grants = {ref.origin_id: ref for ref in request.basis_grants}
            if len(grants) != len(request.basis_grants) or set(grants) - set(request.basis):
                raise InvalidInput('Note grant references must match unique explicit dependencies')
            scope_ids = {row['id'] for row in self._ancestry(db, request.scope_id)}
            for dependency in request.basis:
                if self._origin(db, dependency)['project_id'] != scope['project_id']:
                    reference = grants.get(dependency)
                    if reference is None:
                        raise Conflict('Foreign memory dependency requires its exact reviewed source grant')
                    grant = self._validate_read_grant(db, reference.model_dump(), origin_id=dependency)
                    if grant['scope_id'] not in scope_ids or grant['conversation_id'] is not None:
                        raise Conflict('Discussion-only or unrelated grants cannot authorize a scope-wide note')
                elif dependency in grants:
                    raise InvalidInput('Local note dependencies do not require a foreign source grant')
                self._read(db, dependency, target_scope_id=request.scope_id)
            locator = {'kind': 'NOTE', 'project_id': scope['project_id'], 'operation_id': request.operation_id}
            oid = digest(locator)
            self._bound(db, 'context_origins', MAX_ORIGINS)
            self._bound(db, 'context_associations', MAX_ASSOCIATIONS)
            db.execute('INSERT INTO context_origins VALUES (?,?,?,?,?,?,0,?)',
                (oid, scope['project_id'], 'NOTE', canonical(locator), text_hash(request.text), request.title, stamp()))
            db.execute('INSERT INTO context_notes VALUES (?,?,?)', (oid, request.text, canonical(request.basis)))
            for dependency, ref in grants.items():
                db.execute('INSERT INTO context_note_grants VALUES (?,?,?,?,?)', (oid, dependency, ref.id, ref.revision, request.scope_id))
            # New learnings stay candidates until the user reviews them. An
            # explicit manually entered preference can be activated with the
            # same association API; capture never implies activation.
            aid = digest([request.scope_id, oid, None])
            db.execute('INSERT INTO context_associations VALUES (?,?,?,?,?,?,?,?)',
                       (aid, request.scope_id, oid, None, 'CANDIDATE', 0, 1, stamp()))
            return {'origin_id': oid, 'association_id': aid, 'revision': 1, 'state': 'CANDIDATE'}
        return self._mutate('note', request, apply)

    def associations(self, scope_id, *, conversation_id=None, offset=0, limit=50):
        with self.store.connect() as db:
            scope = self._scope(db, scope_id)
            self._conversation(scope['project_id'], conversation_id)
            rows = db.execute('SELECT a.*,o.title,o.kind,o.forgotten,o.locator_json,o.project_id AS source_project_id FROM context_associations a '
                'JOIN context_origins o ON o.id=a.origin_id WHERE a.scope_id=? '
                'AND (? IS NULL OR a.conversation_id IS NULL OR a.conversation_id=?) ORDER BY a.id LIMIT ? OFFSET ?',
                (scope_id, conversation_id, conversation_id, limit + 1, offset)).fetchall()
            scope_ids = [row['id'] for row in self._ancestry(db, scope_id)]
            exclusions_by_conversation = {cid: self._file_exclusions(db, scope_ids, cid)
                                          for cid in {row['conversation_id'] for row in rows}}
        items = []
        for row in rows[:limit]:
            item = dict(row)
            locator = json.loads(item.pop('locator_json'))
            item['source_ref'] = ({'kind': 'NOTE', 'project_id': item['source_project_id'],
                                   'object_id': item['origin_id']} if item['kind'] == 'NOTE' else locator)
            item['relation'] = 'DIRECT'
            item['file_exclusions'] = [{'id': entry['id'], 'revision': entry['revision'],
                'scope_id': entry['scope_id'], 'conversation_id': entry['conversation_id']}
                for entry in exclusions_by_conversation[item['conversation_id']]
                if entry['file_key'] == locator.get('file_identity_sha256')]
            items.append(item)
        return {'items': items, 'next_offset': offset + limit if len(rows) > limit else None}

    def association_basis(self, scope_id, source, conversation_id=None):
        """Exact metadata basis for an explicit link, never a mutation/retry."""
        with self.store.connect() as db:
            scope = self._scope(db, scope_id)
            if source.project_id != scope['project_id']:
                raise Conflict('Source and selected association scope differ')
            self._conversation(scope['project_id'], conversation_id)
            oid = source.object_id if source.kind == 'NOTE' else digest(source.model_dump())
            aid = digest([scope_id, oid, conversation_id])
            edge = db.execute('SELECT * FROM context_associations WHERE id=?', (aid,)).fetchone()
            ids = [row['id'] for row in self._ancestry(db, scope_id)]
            exclusions = (self._file_exclusions(db, ids, conversation_id, source.file_identity_sha256)
                          if source.kind == 'FILE_SNAPSHOT' else [])
        return {'id': aid, 'revision': edge['revision'] if edge else 0,
            'state': edge['state'] if edge else None,
            'file_exclusions': [{'id': row['id'], 'revision': row['revision']} for row in exclusions],
            'execution_authorized': False}

    @context_read_scope()
    def preview(self, origin_id, project_id):
        with self.store.connect() as db:
            if self._origin(db, origin_id)['project_id'] != project_id:
                raise NotFound('Origin is not in the selected project')
            source = self._read(db, origin_id)
        return {'origin_id': origin_id, **source, 'authority': 'SOURCE_DATA_NOT_A_PERMISSION_OR_SKILL'}

    def related(self, origin_id, project_id):
        with self.store.connect() as db:
            origin = self._origin(db, origin_id)
            if origin['project_id'] != project_id:
                raise NotFound('Origin is not in the selected project')
            rows = db.execute('SELECT a.id,a.scope_id,a.conversation_id,a.revision,a.state '
                'FROM context_associations a WHERE a.origin_id=? AND a.state="ACTIVE" ORDER BY a.id LIMIT 100',
                (origin_id,)).fetchall()
        return {'origin_id': origin_id, 'associations': [dict(row) for row in rows]}

    @context_read_scope()
    def related_source(self, source):
        """Find exact object relationships across indexed ranges, no basename
        matching and no rereading or indexing the user's directory tree.
        """
        if source.kind == 'NOTE':
            return self.related(source.object_id, source.project_id)
        self.source_reader(source)
        with self.store.connect() as db:
            rows = db.execute('SELECT id,locator_json FROM context_origins '
                'WHERE project_id IS ? AND kind=? AND forgotten=0 ORDER BY id LIMIT ?',
                (source.project_id, source.kind, MAX_ORIGINS)).fetchall()
            identity_fields = ('kind', 'project_id', 'object_id', 'revision', 'sha256', 'conversation_id', 'message_id', 'command_owner', 'link_ref')
            expected = source.model_dump()
            matching = [row['id'] for row in rows if all(
                json.loads(row['locator_json']).get(key) == expected.get(key) for key in identity_fields)]
            associations = []
            for origin_id in matching:
                for row in db.execute('SELECT a.id,a.scope_id,s.label AS scope_label,a.conversation_id,a.revision '
                    'FROM context_associations a JOIN context_scopes s ON s.id=a.scope_id '
                    'WHERE a.origin_id=? AND a.state="ACTIVE" ORDER BY a.id LIMIT 101', (origin_id,)):
                    if len(associations) >= 100:
                        return {'associations': associations, 'truncated': True, 'source_verified': True}
                    associations.append(dict(row))
        return {'associations': associations, 'truncated': False, 'source_verified': True}

    def select(self, request):
        return self._mutate('select', request, lambda db: self._select_in_transaction(db, request))

    def _select_in_transaction(self, db, request, *, automatic_terms=None, focus_file_keys=(), routing_basis=None):
        scope = self._scope(db, request.scope_id)
        self._conversation(scope['project_id'], request.conversation_id)
        policy, policy_basis, conversation_policy = self._retrieval_policy(db, request.scope_id, request.conversation_id)
        ancestry = self._ancestry(db, request.scope_id)
        scope_ids = [row['id'] for row in ancestry]
        if request.include_global and scope['project_id'] is not None:
            scope_ids += [row['id'] for row in db.execute(
                'SELECT id FROM context_scopes WHERE kind="GLOBAL" ORDER BY id LIMIT ?', (MAX_SCOPES,))]
        placeholders = ','.join('?' for _ in scope_ids)
        rows = db.execute(f'SELECT a.*,o.kind,o.title,o.project_id AS source_project_id,f.file_key FROM context_associations a '
            f'JOIN context_origins o ON o.id=a.origin_id LEFT JOIN context_origin_files f ON f.origin_id=o.id '
            f'WHERE a.scope_id IN ({placeholders}) '
            'AND (a.conversation_id IS NULL OR a.conversation_id IS ?) '
            'AND a.state IN ("ACTIVE","EXCLUDED","REJECTED") ORDER BY a.id LIMIT ?',
            (*scope_ids, request.conversation_id, MAX_ASSOCIATIONS)).fetchall()
        rows = [dict(row) for row in rows]
        rows += self._inherited_candidates(db, scope_ids, request.conversation_id)
        # A manual exclusion anywhere in the applicable scope chain beats
        # an ancestor/global active edge, independent of ordering.
        excluded = {row['origin_id'] for row in rows if row['state'] == 'EXCLUDED'}
        rejected = {row['origin_id'] for row in rows if row['state'] == 'REJECTED' and row.get('manual_locked')}
        excluded.update(rejected)
        excluded.update(self._excluded_file_origins(db, scope_ids, request.conversation_id))
        chosen, omitted, seen, used = [], [], set(), 0
        omission_counts = {}
        read_budget = [64]
        budget = min(request.char_budget, policy['char_budget']) if policy['retrieve'] else 0
        def order(row):
            ordinary = (scope_ids.index(row['scope_id']), row.get('relation') == 'INHERITED', row['id'])
            return ((row['kind'] not in {'NOTE','NOTE_FRAGMENT'}, row.get('file_key') not in focus_file_keys, *ordinary)
                    if automatic_terms is not None else ordinary)
        for row in sorted(rows, key=order):
            oid = row['origin_id']
            if oid in seen:
                continue
            seen.add(oid)
            reason = None
            row_policy, _ = self._policy(db, row['scope_id'])
            grant = None
            if row.get('relation') != 'INHERITED' and row['source_project_id'] != self._scope(db, row['scope_id'])['project_id']:
                grant = self._read_grant(db, row['scope_id'], oid, row['conversation_id'])
                if grant is None or grant['state'] != 'ACTIVE':
                    reason = 'READ_GRANT_INACTIVE'
            if oid in excluded:
                reason = 'MANUALLY_REJECTED' if oid in rejected else 'MANUALLY_EXCLUDED'
            elif not policy['retrieve'] or not row_policy['retrieve']:
                reason = 'RETRIEVAL_DISABLED'
            elif request.query and request.query.casefold() not in row['title'].casefold():
                reason = 'QUERY_NOT_MATCHED'
            elif automatic_terms is not None and not (row['kind'] in {'NOTE','NOTE_FRAGMENT'}
                    or request.conversation_id is not None and row.get('conversation_id') == request.conversation_id
                    or row.get('file_key') in focus_file_keys
                    or any(term in row['title'].casefold() for term in automatic_terms)):
                reason = 'QUERY_NOT_MATCHED'
            elif used >= budget or len(chosen) >= request.limit:
                reason = 'BUDGET'
            elif read_budget[0] <= 0:
                reason = 'SOURCE_READ_BUDGET'
            if reason is None:
                try:
                    if grant is not None:
                        self._validate_read_grant(db, {'id': grant['id'], 'revision': grant['revision']}, origin_id=oid)
                    value = self._read(db, oid, blocked=excluded, budget=read_budget,
                                       target_scope_id=None if grant is not None else request.scope_id)
                except ContextReadLimit:
                    reason = 'SOURCE_BYTE_BUDGET'
                except (Conflict, NotFound):
                    reason = 'SOURCE_UNAVAILABLE_OR_CHANGED'
                else:
                    length = len(value['text'])
                    if used + length > budget or len(chosen) >= request.limit:
                        reason = 'BUDGET'
                    else:
                        inherited = row.get('relation') == 'INHERITED'
                        chosen.append({'origin_id': oid, 'association_id': None if inherited else row['id'],
                            'association_revision': None if inherited else row['revision'],
                            'relation': 'INHERITED' if inherited else 'DIRECT',
                            'folder_rule_refs': row.get('folder_rule_refs', []), 'fingerprint': value['fingerprint'],
                            'source_project_id': row.get('source_project_id', self._scope(db, row['scope_id'])['project_id']),
                            'source_kind': row['kind'],
                            **({'read_grant_ref': {'id': grant['id'], 'revision': grant['revision']}} if grant else {}),
                            'characters': length, 'scope_id': row['scope_id']})
                        used += length
            if reason:
                omission_counts[reason] = omission_counts.get(reason, 0) + 1
                if len(omitted) < 128:
                    omitted.append({'origin_id': oid, 'reason': reason})
        selection = {'scope_id': request.scope_id, 'conversation_id': request.conversation_id,
            'project_id': scope['project_id'], 'scope_basis': [{'id': s['id'], 'revision': s['revision']} for s in ancestry],
            'policy': policy, 'policy_basis': policy_basis, 'selected': chosen, 'omitted': omitted,
            'omission_counts': omission_counts, 'omitted_truncated': sum(omission_counts.values()) > len(omitted),
            'applicable_scopes': [{'id': sid, 'revision': self._scope(db, sid)['revision']} for sid in scope_ids],
            'characters': used, 'char_budget': budget, 'model_calls': 0,
            'authority': 'READONLY_SOURCE_DATA_NO_EXECUTION_GRANT',
            'selection_mode': 'AUTO' if automatic_terms is not None else 'MANUAL',
            'conversation_preferences_basis': conversation_policy,
            **({'routing_basis': routing_basis} if routing_basis is not None else {})}
        allowance = current_context_read_budget()
        if allowance is not None:
            selection['source_read_budget'] = allowance.summary()
        sid = digest(selection)
        if db.execute('SELECT 1 FROM context_selections WHERE id=?', (sid,)).fetchone() is None:
            self._bound(db, 'context_selections', 4096)
        db.execute('INSERT OR IGNORE INTO context_selections VALUES (?,?,?,?,?,?)',
            (sid, request.scope_id, request.conversation_id, canonical(selection), digest(selection), stamp()))
        return {'selection_id': sid, **selection}

    def selection_receipt(self, selection_id, project_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM context_selections WHERE id=?', (selection_id,)).fetchone()
        if row is None:
            raise NotFound('Context selection receipt does not exist')
        value = json.loads(row['selection_json'])
        if digest(value) != selection_id or digest(value) != row['selection_sha256']:
            raise Conflict('Context selection receipt integrity differs')
        if value['project_id'] != project_id:
            raise NotFound('Selection receipt belongs to another source project')
        ids = list(dict.fromkeys(item['origin_id'] for item in value['selected'] + value['omitted']))
        labels = {}
        if ids:
            with self.store.connect() as db:
                labels = {row['id']: row['title'] for row in db.execute(
                    'SELECT id,title FROM context_origins WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)}
        return {'selection_id': selection_id, **value, 'historical_receipt': True,
            'current_sources_verified': False, 'execution_authorized': False,
            'origin_labels': labels, 'label_scope': 'CURRENT_INDEX_METADATA_NOT_FROZEN_ORIGINAL_TEXT'}

    def authority_epoch(self):
        with self.store.connect() as db:
            row=db.execute('SELECT revision FROM context_authority_epoch WHERE singleton=1').fetchone()
        if row is None or type(row[0]) is not int or row[0]<1:
            raise Conflict('Current context authority epoch is unavailable')
        return row[0]

    def _authorize_origin(self,db,ident,*,blocked,budget,target_scope_id,seen=()):
        """Current registered metadata/withdrawal only, not saved-byte freshness."""
        if budget[0]<=0 or ident in blocked or ident in seen or len(seen)>=8:
            raise Conflict('Context authority dependency is excluded or exceeds its bound')
        budget[0]-=1;row=self._origin(db,ident);seen=(*seen,ident)
        if row['kind']=='NOTE':
            self._validate_note_grants(db,ident,target_scope_id=target_scope_id)
            note=db.execute('SELECT basis_json FROM context_notes WHERE origin_id=?',(ident,)).fetchone()
            if note is None:raise Conflict('Context note authority basis is unavailable')
            for dependency in json.loads(note['basis_json']):
                self._authorize_origin(db,dependency,blocked=blocked,budget=budget,target_scope_id=target_scope_id,seen=seen)
        elif row['kind']=='NOTE_FRAGMENT':
            locator=SourceRef.model_validate(json.loads(row['locator_json']))
            base=self._origin(db,locator.object_id)
            if base['kind']!='NOTE' or base['project_id']!=locator.project_id or base['fingerprint']!=locator.sha256:
                raise Conflict('Reviewed note authority differs from its original basis')
            self._authorize_origin(db,base['id'],blocked=blocked,budget=budget,target_scope_id=target_scope_id,seen=seen)
        elif row['kind']=='DOCUMENT':
            locator=SourceRef.model_validate(json.loads(row['locator_json']))
            document=db.execute('SELECT d.current_revision,v.sha256 FROM documents d JOIN document_versions v ON v.document_id=d.id AND v.revision=? WHERE d.id=? AND d.project_id=? AND d.deleted_at IS NULL',
                (locator.revision,locator.object_id,locator.project_id)).fetchone()
            if document is None or document['current_revision']!=locator.revision or document['sha256']!=locator.sha256:
                raise Conflict('Registered document source revision changed after admission')
        return row

    def authorize_selection(self,selection_id,project_id,*,conversation_id=None,frozen_conversation_scope=None):
        return self.materialize(selection_id,project_id,conversation_id=conversation_id,
            frozen_conversation_scope=frozen_conversation_scope,_authorization_only=True)

    @context_read_scope()
    def materialize(self, selection_id, project_id, *, conversation_id=None, frozen_conversation_scope=None,_authorization_only=False):
        """Resolve for a new input, or for an already verified host admission.

        The latter is an internal Python boundary, never a public route flag.
        Callers must verify their immutable operation/admission digest first.
        Moving/deleting navigation does not retarget an existing task's grant.
        Current source versions, user exclusions and retrieval policy still win.
        """
        with self.store.connect() as db:
            if _authorization_only:db.execute('BEGIN')
            row = db.execute('SELECT * FROM context_selections WHERE id=?', (selection_id,)).fetchone()
            if row is None:
                raise NotFound('Context selection does not exist')
            selection = json.loads(row['selection_json'])
            if digest(selection) != selection_id or digest(selection) != row['selection_sha256']:
                raise Conflict('Context selection integrity differs')
            if selection['project_id'] != project_id or selection['conversation_id'] != conversation_id:
                raise Conflict('Context selection belongs to another project or conversation')
            if frozen_conversation_scope is None:
                self._conversation(project_id, conversation_id)
            else:
                from packages.domain.conversation_threads import ConversationRef
                reference = ConversationRef.model_validate(frozen_conversation_scope['conversation_ref'])
                if (reference.id != conversation_id or frozen_conversation_scope['project_id'] != project_id):
                    raise Conflict('Frozen context admission belongs to another conversation or project')
            for scope in selection['applicable_scopes']:
                if self._scope(db, scope['id'])['revision'] != scope['revision'] and not _authorization_only:
                    raise Conflict('Context policy or hierarchy changed; select again')
            if _authorization_only:
                ancestry=self._ancestry(db,selection['scope_id'])
                if ([s['id'] for s in ancestry]!=[s['id'] for s in selection['scope_basis']]
                        or ancestry[0]['project_id']!=selection['project_id']):
                    raise Conflict('Admitted context hierarchy or project changed')
            effective, _, _ = self._retrieval_policy(db, selection['scope_id'], conversation_id)
            if not _authorization_only:self._validate_context_routing(db, selection)
            if not effective['retrieve'] and selection['selected']:
                raise Conflict('Context retrieval was disabled after selection')
            if selection['characters'] > effective['char_budget']:
                raise Conflict('Context character budget was narrowed after selection; select again')
            items = []
            read_budget = [64]
            ids = [scope['id'] for scope in selection['applicable_scopes']]
            placeholders = ','.join('?' for _ in ids)
            # A newly added child/conversation exclusion must invalidate a
            # frozen selection even when its old active edge did not change.
            blocked = {edge['origin_id'] for edge in db.execute(
                f'SELECT origin_id FROM context_associations WHERE scope_id IN ({placeholders}) '
                'AND (state="EXCLUDED" OR state="REJECTED" AND manual_locked=1) '
                'AND (conversation_id IS NULL OR conversation_id IS ?)',
                (*ids, conversation_id))}
            blocked.update(self._excluded_file_origins(db, ids, conversation_id))
            for item in selection['selected']:
                item_policy, _, _ = self._retrieval_policy(db, item['scope_id'], conversation_id)
                if not item_policy['retrieve']:
                    raise Conflict('A selected source scope disabled retrieval after selection')
                if item.get('read_grant_ref') is not None:
                    self._validate_read_grant(db, item['read_grant_ref'], origin_id=item['origin_id'])
                if item.get('relation') == 'INHERITED':
                    self._validate_inherited(db, item, ids, conversation_id)
                else:
                    edge = db.execute('SELECT * FROM context_associations WHERE id=?', (item['association_id'],)).fetchone()
                    if not edge or edge['revision'] != item['association_revision'] or edge['state'] != 'ACTIVE':
                        raise Conflict('A selected association was withdrawn or changed')
                if _authorization_only:
                    source=self._authorize_origin(db,item['origin_id'],blocked=blocked,budget=read_budget,
                        target_scope_id=None if item.get('read_grant_ref') else selection['scope_id'])
                else:
                    source = self._read(db, item['origin_id'], blocked=blocked, budget=read_budget,
                                        target_scope_id=None if item.get('read_grant_ref') else selection['scope_id'])
                if source['fingerprint'] != item['fingerprint']:
                    raise Conflict('A selected source changed before dispatch')
                if _authorization_only:continue
                origin = self._origin(db, item['origin_id'])
                source_ref = ({'kind': 'NOTE', 'project_id': origin['project_id'], 'object_id': origin['id']}
                              if origin['kind'] == 'NOTE' else json.loads(origin['locator_json']))
                items.append({**item, 'title': source['title'], 'text': source['text'],
                    'source_project_id': origin['project_id'], 'source_ref': source_ref})
            if _authorization_only:
                epoch=db.execute('SELECT revision FROM context_authority_epoch WHERE singleton=1').fetchone()
                if epoch is None:raise Conflict('Context authority epoch is unavailable')
                return {'kind':'CONTEXT_SELECTION_AUTHORITY','selection_id':selection_id,'authority_epoch':epoch[0],
                    'selected_count':len(selection['selected']),'source_bodies_read':False,
                    'scope':'CURRENT_REGISTERED_AUTHORITY_NOT_SOURCE_BYTE_OR_MODEL_USE_PROOF','execution_authorized':False}
            return {'selection_id': selection_id, 'scope_id': selection['scope_id'],
                    'items': items, 'characters': selection['characters'],
                    'authority': selection['authority']}

    def forget(self, request):
        def apply(db):
            scope = self._scope(db, request.scope_id)
            origin = self._origin(db, request.origin_id)
            if origin['project_id'] != scope['project_id']:
                raise Conflict('Forget scope belongs to another project')
            forgotten = {request.origin_id}
            notes = db.execute('SELECT origin_id,basis_json FROM context_notes LIMIT ?', (MAX_ORIGINS,)).fetchall()
            fragments = db.execute('SELECT * FROM context_fragment_bases LIMIT ?', (MAX_ORIGINS,)).fetchall()
            while True:
                derived = {row['origin_id'] for row in notes if forgotten.intersection(json.loads(row['basis_json']))}
                derived.update(row['origin_id'] for row in fragments if row['base_origin_id'] in forgotten)
                if derived <= forgotten:
                    break
                forgotten.update(derived)
            for oid in sorted(forgotten):
                db.execute('UPDATE context_origins SET forgotten=1,title="Forgotten source" WHERE id=?', (oid,))
                db.execute('UPDATE context_notes SET text_content="",basis_json="[]" WHERE origin_id=?', (oid,))
                db.execute('DELETE FROM context_origin_files WHERE origin_id=?', (oid,))
                db.execute('UPDATE context_associations SET state="RETRACTED",manual_locked=1,'
                           'revision=revision+1,updated_at=? WHERE origin_id=?', (stamp(), oid))
                db.execute('UPDATE context_read_grants SET state="RETRACTED",revision=revision+1,updated_at=? WHERE origin_id=?',
                           (stamp(), oid))
            return {'forgotten_origins': sorted(forgotten), 'original_files_deleted': False,
                    'original_conversations_deleted': False, 'physical_erasure_claimed': False}
        return self._mutate('forget', request, apply)
