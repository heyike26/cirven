"""Stable user conversations with immutable execution-source aliases.

Only organization metadata is written here. A native route is a future message
address, never proof that an execution owner, project file or grant was moved.
"""
from datetime import datetime, timezone
from dataclasses import dataclass
import json
import re

from pydantic import Field, StrictInt
from packages.domain.questions import RequestModel
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest

IDENT = re.compile(r'^(native|original|discovery):[a-f0-9]{64}$')
MAX_SOURCES = 64


class ConversationRef(RequestModel):
    id: str = Field(pattern=r'^(native|original|discovery):[a-f0-9]{64}$')
    revision: StrictInt = Field(ge=1)


@dataclass(frozen=True)
class ConfigurationAdoptionBinding:
    """Internal verified acknowledgement, never a public request/grant field."""
    original_reference: dict
    conversation_reference: dict
    project_id: str | None
    execution_project_id: str
    request_sha256: str
    operation_id: str
    authority_sha256: str


def source_identity(source):
    kind, pid = source['kind'], source['project_id']
    if kind == 'native':
        identity = {'kind': kind, 'project_id': pid, 'conversation_id': source['conversation_id']}
        key = [pid, source['conversation_id']]
    elif kind == 'original':
        identity = {'kind': kind, 'project_id': pid, 'session_id': source['session_ref']['id']}
        key = [pid, source['session_ref']['id']]
    elif kind == 'discovery':
        identity = {'kind': kind, 'project_id': pid, 'session_id': source['object_ref']['id']}
        key = [pid, source['object_ref']['id']]
    else:
        raise Conflict('Unknown conversation source kind')
    return kind + ':' + digest(key), identity


class ConversationThreads:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _entity(connection, ident):
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            raise NotFound('Unknown conversation')
        row = connection.execute('SELECT t.*,coalesce(o.revision,0) AS revision,'
            'coalesce(o.archived,0) AS archived,o.updated_at AS organization_updated_at FROM conversation_threads t '
            'LEFT JOIN conversation_organization o USING(conversation_id) WHERE t.conversation_id=?', (ident,)).fetchone()
        if row is None:
            raise NotFound('Unknown organized conversation')
        return row

    @staticmethod
    def _alias(connection, source):
        alias, identity = source_identity(source)
        row = connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?', (alias,)).fetchone()
        if row is not None and canonical(identity) != row['identity_json']:
            raise Conflict('Conversation source alias integrity differs')
        return row

    def overlay(self, source):
        """Observational; listing existing legacy data never enrolls it."""
        with self.store.connect() as connection:
            alias = self._alias(connection, source)
            if alias is None:
                return None
            row = self._entity(connection, alias['conversation_id'])
            origin = connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?',
                                        (row['conversation_id'],)).fetchone()
            origin_identity, _ = self.checked_alias(origin)
            stamps = [value for value in (row['activity_at'], row['organization_updated_at'], row['created_at']) if value]
            return {'id': row['conversation_id'], 'project_id': row['project_id'],
                'revision': row['revision'], 'archived': bool(row['archived']),
                'deleted': row['deleted_at'] is not None, 'display_title': row['display_title'],
                'origin': origin_identity, 'updated_at': max(stamps)}

    def enroll(self, connection, current, *, created_at):
        """Caller holds the same transaction as its organization CAS/receipt."""
        if current['source']['kind'] == 'conversation':
            row = self._entity(connection, current['id'])
            created = connection.execute('SELECT 1 FROM conversation_creations WHERE conversation_id=?', (current['id'],)).fetchone()
            if created is None or row['deleted_at'] is not None or current['source']['conversation_ref'] != {
                    'id': row['conversation_id'], 'revision': row['revision']}:
                raise Conflict('Conversation metadata changed before selection')
            return row
        alias_id, identity = source_identity(current['source'])
        alias = self._alias(connection, current['source'])
        if alias is not None:
            if alias['conversation_id'] != current['id']:
                raise Conflict('Conversation already belongs to a different stable identity')
            row = self._entity(connection, alias['conversation_id'])
            if row['deleted_at'] is not None:
                raise Conflict('This conversation was deleted and cannot be restored')
            self.observe_listing(connection, row['conversation_id'], alias_id,
                                 current['source'], updated_at=created_at)
            return row
        if current['id'] != alias_id:
            raise Conflict('A new conversation must bind its exact original source identity')
        if current['project_id'] is not None and connection.execute('SELECT id FROM projects WHERE id=?', (current['project_id'],)).fetchone() is None:
            raise Conflict('The original project is unavailable; existing conversation data is preserved')
        connection.execute('INSERT INTO conversation_threads '
            '(conversation_id,initial_kind,project_id,created_at,deleted_at,display_title,activity_at) VALUES (?,?,?,?,NULL,?,?)',
            (alias_id, current['kind'], current['project_id'], created_at, current.get('title'), created_at))
        raw = canonical(current['source'])
        connection.execute('INSERT INTO conversation_source_aliases VALUES (?,?,?,?,?,?,?)',
            (alias_id, alias_id, identity['kind'], canonical(identity), raw, digest(current['source']), created_at))
        self.reserve_route(connection, alias_id, current['project_id'], created_at=created_at,
            preferred_cid=identity.get('conversation_id'))
        self.observe_listing(connection, alias_id, alias_id, current['source'], updated_at=created_at)
        return self._entity(connection, alias_id)

    @staticmethod
    def observe_listing(connection, ident, alias_id, source, *, updated_at):
        """Remember an explicitly validated head without rewriting its frozen alias."""
        raw = canonical(source)
        if len(raw.encode()) > 8192:
            raise Conflict('Conversation listing observation is too large')
        connection.execute('INSERT INTO conversation_listing_observations VALUES (?,?,?,?,?) '
            'ON CONFLICT(conversation_id) DO UPDATE SET alias_id=excluded.alias_id,'
            'source_json=excluded.source_json,source_sha256=excluded.source_sha256,'
            'updated_at=excluded.updated_at', (ident, alias_id, raw, digest(source), updated_at))

    def reserve_route(self, connection, ident, project_id, *, created_at, preferred_cid=None):
        project_key = project_id or ''
        old = connection.execute('SELECT * FROM conversation_native_routes WHERE conversation_id=? AND project_key=?',
                                 (ident, project_key)).fetchone()
        if old is not None:
            return dict(old)
        count = connection.execute('SELECT count(*) FROM conversation_source_aliases WHERE conversation_id=?',
                                   (ident,)).fetchone()[0]
        cid = preferred_cid or 'cv_' + digest([ident, project_id])[:48]
        alias_id, identity = source_identity({'kind': 'native', 'project_id': project_id, 'conversation_id': cid})
        existing = connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?', (alias_id,)).fetchone()
        if existing is not None and existing['conversation_id'] != ident:
            raise Conflict('Target message address already belongs to another conversation')
        if existing is None:
            if count >= MAX_SOURCES:
                raise Conflict('Conversation source capacity reached; existing discussion is preserved')
            connection.execute('INSERT INTO conversation_source_aliases VALUES (?,?,?,?,NULL,NULL,?)',
                (alias_id, ident, 'native', canonical(identity), created_at))
        connection.execute('INSERT INTO conversation_native_routes VALUES (?,?,?,?,?)',
            (ident, project_key, project_id, cid, alias_id))
        return {'conversation_id': ident, 'project_key': project_key, 'project_id': project_id,
                'native_conversation_id': cid, 'alias_id': alias_id}

    def move(self, connection, ident, project_id, *, updated_at):
        if project_id is not None and connection.execute('SELECT id FROM projects WHERE id=?', (project_id,)).fetchone() is None:
            raise NotFound('Target project is unavailable')
        row = self._entity(connection, ident)
        if row['deleted_at'] is not None:
            raise Conflict('Deleted conversations cannot be moved')
        if row['project_id'] == project_id:
            raise Conflict('Conversation already belongs to this project')
        self.reserve_route(connection, ident, project_id, created_at=updated_at)
        connection.execute('UPDATE conversation_threads SET project_id=?,activity_at=? WHERE conversation_id=?', (project_id, updated_at, ident))

    def delete(self, connection, ident, *, updated_at):
        row = self._entity(connection, ident)
        if row['deleted_at'] is not None:
            raise Conflict('This conversation is already deleted')
        # Source aliases are tombstoned through the owner. Their reserved keys
        # prevent old receipts or delayed results from resurrecting the chat.
        # Task/execution records and project files have independent lifetimes.
        connection.execute('UPDATE conversation_threads SET deleted_at=? WHERE conversation_id=?', (updated_at, ident))

    @staticmethod
    def checked_alias(item):
        try:
            if item is None:
                raise ValueError()
            if len(item['identity_json'].encode()) > 2048 or item['source_json'] and len(item['source_json'].encode()) > 8192:
                raise ValueError()
            identity = json.loads(item['identity_json'])
            kind, pid = identity['kind'], identity['project_id']
            if kind not in {'native', 'original', 'discovery'} or (pid is not None and
                    (not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', pid))):
                raise ValueError()
            key = 'conversation_id' if kind == 'native' else 'session_id'
            if set(identity) != {'kind', 'project_id', key} or not isinstance(identity[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identity[key]):
                raise ValueError()
            if item['alias_id'] != kind + ':' + digest([pid, identity[key]]) or item['kind'] != kind:
                raise ValueError()
            source = json.loads(item['source_json']) if item['source_json'] else None
            if source is not None:
                alias, actual = source_identity(source)
                if digest(source) != item['source_sha256'] or alias != item['alias_id'] or actual != identity:
                    raise ValueError()
            elif item['source_sha256'] is not None:
                raise ValueError()
            return identity, source
        except (KeyError, ValueError, TypeError):
            raise Conflict('Conversation source identity or metadata integrity changed') from None

    @staticmethod
    def project_context(connection, project_id):
        """Bounded project metadata, never a directory read or execution grant."""
        if project_id is None:
            snapshot = {'kind': 'CHATS', 'project_id': None, 'name': '聊天',
                'description': '', 'description_truncated': False}
        else:
            project = connection.execute('SELECT id,substr(name,1,200) AS name,'
                'substr(description,1,16384) AS description,length(description) AS description_length,'
                'created_at FROM projects WHERE id=?', (project_id,)).fetchone()
            if project is None:
                raise NotFound('Selected conversation project is unavailable')
            description = project['description'].encode('utf-8')[:16384].decode('utf-8', errors='ignore')
            snapshot = {'kind': 'PROJECT', 'project_id': project['id'], 'name': project['name'],
                'description': description, 'created_at': project['created_at'],
                'description_truncated': len(description) < project['description_length']}
        return {'snapshot': snapshot, 'snapshot_sha256': digest(snapshot),
            'coverage': 'PROJECT_NAME_AND_BOUNDED_DESCRIPTION_NO_FILES',
            'role': 'READONLY_PROJECT_DATA_NOT_SYSTEM_INSTRUCTIONS',
            'file_contents_read': False, 'file_write_authorized': False,
            'automatic_input_authorized': False}

    @classmethod
    def _scope(cls, connection, row):
        aliases = []
        records = connection.execute('SELECT * FROM conversation_source_aliases WHERE conversation_id=? '
                                     'ORDER BY alias_id LIMIT ?', (row['conversation_id'], MAX_SOURCES + 1)).fetchall()
        if len(records) > MAX_SOURCES:
            raise Conflict('Conversation source capacity is invalid')
        for item in records:
            identity, source = cls.checked_alias(item)
            aliases.append({'id': item['alias_id'], 'identity': identity, 'original_source': source})
        return {'conversation_ref': {'id': row['conversation_id'], 'revision': row['revision']},
            'project_id': row['project_id'], 'sources': aliases,
            'project_context': cls.project_context(connection, row['project_id']),
            'history_role': 'READONLY_SOURCE_DATA_NO_FILE_OR_NATIVE_GRANT',
            'automatic_input_authorized': False, 'file_write_authorized': False}

    def listing_sources(self):
        # Enrollment/organization gives an old source its own recent metadata
        # position. Its old execution-file mtime is never changed to fake it.
        with self.store.connect() as connection:
            rows = connection.execute('SELECT a.*,t.activity_at,t.created_at AS thread_created_at,'
                'o.updated_at AS organization_updated_at,l.alias_id AS listing_alias_id,'
                'CASE WHEN length(CAST(l.source_json AS BLOB)) <= 8192 THEN l.source_json END AS listing_source_json,'
                'l.source_sha256 AS listing_source_sha256,c.operation_id AS creation_operation_id FROM conversation_threads t '
                'JOIN conversation_source_aliases a ON a.alias_id=t.conversation_id '
                'JOIN conversation_organization o ON o.conversation_id=t.conversation_id '
                'LEFT JOIN conversation_listing_observations l ON l.conversation_id=t.conversation_id '
                'LEFT JOIN conversation_creations c ON c.conversation_id=t.conversation_id '
                'WHERE t.deleted_at IS NULL')
            for row in rows:
                try:
                    _, source = self.checked_alias(row)
                    if row['creation_operation_id'] is not None:
                        source = self.metadata_header(row['conversation_id'])['source']
                        stamp = max(value for value in (row['activity_at'], row['organization_updated_at'], row['thread_created_at']) if value)
                        yield row['conversation_id'], source, stamp
                        continue
                    if row['listing_alias_id'] is not None:
                        observed = json.loads(row['listing_source_json'])
                        alias_id, identity = source_identity(observed)
                        alias = self._alias(connection, observed)
                        if (digest(observed) != row['listing_source_sha256'] or
                                alias_id != row['listing_alias_id'] or alias is None or
                                alias['conversation_id'] != row['conversation_id']):
                            raise Conflict('Conversation listing observation integrity differs')
                        actual, _ = self.checked_alias(alias)
                        if actual != identity:
                            raise Conflict('Conversation listing observation identity differs')
                        source = observed
                except (Conflict, NotFound, ValueError, TypeError, KeyError):
                    source = None
                stamp = max(value for value in (row['activity_at'], row['organization_updated_at'], row['thread_created_at']) if value)
                yield row['conversation_id'], source, stamp

    def has_creation(self, ident):
        with self.store.connect() as connection:
            return connection.execute('SELECT 1 FROM conversation_creations WHERE conversation_id=?', (ident,)).fetchone() is not None

    def metadata_header(self, ident):
        """Logical conversation metadata, explicitly not an execution source."""
        with self.store.connect() as connection:
            row = self._entity(connection, ident)
            if row['deleted_at'] is not None:
                raise NotFound('This conversation has been deleted')
            created = connection.execute('SELECT * FROM conversation_creations WHERE conversation_id=?', (ident,)).fetchone()
            if created is None:
                raise NotFound('This conversation uses its original legacy source')
            try:
                if len(created['result_json'].encode()) > 8192:
                    raise ValueError()
                result = json.loads(created['result_json'])
                if (digest(result) != created['result_sha256'] or result['conversation_ref']['id'] != ident or
                        result['request_sha256'] != created['request_sha256'] or result['operation_id'] != created['operation_id']):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise Conflict('Conversation creation metadata integrity changed') from None
            origin = connection.execute('SELECT * FROM conversation_source_aliases WHERE alias_id=?', (ident,)).fetchone()
            identity, _ = self.checked_alias(origin)
            if origin['conversation_id'] != ident or row['revision'] < 1:
                raise Conflict('Conversation metadata identity changed')
            project = connection.execute('SELECT substr(name,1,500) AS name FROM projects WHERE id=?', (row['project_id'],)).fetchone() if row['project_id'] else None
            source = {'kind': 'conversation', 'project_id': row['project_id'], 'workspace_ref': None,
                'conversation_ref': {'id': ident, 'revision': row['revision']},
                'metadata_role': 'CONVERSATION_METADATA_NO_EXECUTION_OR_FILE_GRANT'}
            return {'id': ident, 'kind': 'native', 'project_id': row['project_id'],
                'project_name': project['name'] if project else None, 'title': row['display_title'],
                'updated_at': max(value for value in (row['activity_at'], row['organization_updated_at'], row['created_at']) if value),
                'read_only': False, 'source': source, 'organization': {'revision': row['revision'], 'archived': bool(row['archived'])},
                'conversation_ref': source['conversation_ref'], 'conversation_origin': identity}

    def unused_creation(self, ident):
        with self.store.connect() as connection:
            if connection.execute('SELECT 1 FROM conversation_creations WHERE conversation_id=?', (ident,)).fetchone() is None:
                return False
            return (connection.execute('SELECT 1 FROM conversation_message_admissions WHERE conversation_id=? LIMIT 1', (ident,)).fetchone() is None
                and connection.execute("SELECT 1 FROM conversation_source_aliases WHERE conversation_id=? AND (kind!='native' OR source_json IS NOT NULL) LIMIT 1", (ident,)).fetchone() is None)

    def view(self, ident):
        with self.store.connect() as connection:
            row = self._entity(connection, ident)
            if row['deleted_at'] is not None:
                raise NotFound('This conversation has been deleted')
            route = connection.execute('SELECT * FROM conversation_native_routes WHERE conversation_id=? AND project_key=?',
                                       (ident, row['project_id'] or '')).fetchone()
            if route is None:
                raise Conflict('Conversation has no exact next-message route')
            return {**self._scope(connection, row), 'archived': bool(row['archived']), 'title': row['display_title'],
                **({'metadata_source': self.metadata_header(ident)['source']} if self.has_creation(ident) else {}),
                'next_message': {'project_id': route['project_id'], 'conversation_id': route['native_conversation_id']},
                'execution_started': False, 'files_moved': False}

    def admission_scope(self, body):
        """Freeze the selected scope. Existing admitted tasks never call this again."""
        reference = body.get('conversation_ref')
        pid = body.get('project_id') or (body.get('file_source_ref') or {}).get('project_id') or (body.get('file_link_ref') or {}).get('project_id')
        cid = body.get('conversation_id')
        with self.store.connect() as connection:
            alias = self._alias(connection, {'kind': 'native', 'project_id': pid, 'conversation_id': cid}) if cid else None
            if reference is None and alias is None:
                return None
            if reference is not None:
                reference = ConversationRef.model_validate(reference).model_dump()
                row = self._entity(connection, reference['id'])
                if row['revision'] != reference['revision']:
                    raise Conflict('Conversation organization changed; refresh before sending')
                if alias is None or alias['conversation_id'] != reference['id']:
                    raise Conflict('The next message does not use this exact conversation route')
            else:
                row = self._entity(connection, alias['conversation_id'])
            if row['deleted_at'] is not None:
                raise NotFound('Deleted conversations cannot receive new messages')
            if row['project_id'] != pid:
                raise Conflict('Conversation moved; select its current project before sending')
            route = connection.execute('SELECT * FROM conversation_native_routes WHERE conversation_id=? AND project_key=?',
                                       (row['conversation_id'], pid or '')).fetchone()
            if route is None or route['native_conversation_id'] != cid:
                raise Conflict('Conversation message route differs')
            return self._scope(connection, row)

    @staticmethod
    def _execution_project_matches(connection, logical, execution):
        if logical == execution:
            return True
        if logical is not None:
            return False
        project = connection.execute('SELECT description FROM projects WHERE id=?', (execution,)).fetchone()
        return project is not None and project['description'] in {
            'eeas.original-agent-session.internal-project.v1', 'EEAS_INTERNAL_DESKTOP_RUNTIME_V1'}

    def execution_source_matches(self, connection, ident, logical, execution, source=None):
        if self._execution_project_matches(connection,logical,execution):
            return True
        if not source or source.get('project_id')!=execution:
            return False
        alias=self._alias(connection,{'kind':'original',**source})
        if alias is None or alias['conversation_id']!=ident:
            return False
        _,original=self.checked_alias(alias)
        return original is not None and original.get('kind')=='original' and original.get('session_ref')==source.get('session_ref')

    def bind_original(self, manifest, reference, *, execution_source=None, adoption_binding=None):
        """Register a new public SDK owner; never move its workspace or grants."""
        reference = ConversationRef.model_validate(reference).model_dump()
        if manifest.get('native_review_admission') or manifest.get('native_message_admission'):
            raise Conflict('A private execution owner cannot become a public conversation source')
        adopted=isinstance(adoption_binding,ConfigurationAdoptionBinding)
        if adoption_binding is not None and (not adopted or adoption_binding.original_reference!=manifest['request'].get('conversation_ref')
                or adoption_binding.conversation_reference!=reference or adoption_binding.original_reference['id']!=reference['id']
                or adoption_binding.execution_project_id!=manifest['project_id'] or adoption_binding.request_sha256!=digest(manifest['request'])
                or not re.fullmatch(r'[a-f0-9]{64}',adoption_binding.authority_sha256)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',adoption_binding.operation_id)):
            raise Conflict('The internal adopted binding differs from the exact original execution manifest')
        if manifest['request'].get('conversation_ref') != reference and not adopted:
            raise Conflict('Original owner was not opened for this exact conversation')
        pid, sid = manifest['project_id'], manifest['id']
        source = {'kind': 'original', 'project_id': pid, 'session_ref': {'id': sid, 'revision': 1, 'sha256': digest(manifest)},
            'generation': 0, 'provider_profile_ref': manifest['request']['provider_profile_ref'],
            'workspace_ref': manifest['request'].get('workspace_ref')}
        alias_id, identity = source_identity(source)
        raw = canonical(source)
        if len(raw.encode()) > 8192:
            raise Conflict('Original conversation source exceeds its metadata bound')
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = self._alias(connection, source)
            if existing is not None:
                if existing['conversation_id'] != reference['id'] or existing['source_sha256'] != digest(source):
                    raise Conflict('Original owner already belongs to another conversation')
                return  # Exact open replay is a read; it does not re-start an owner.
            row = self._entity(connection, reference['id'])
            if row['deleted_at'] is not None or row['revision'] != reference['revision']:
                raise Conflict('Conversation changed before the original owner was registered')
            if adopted and row['project_id']!=adoption_binding.project_id:
                raise Conflict('The adopted organization changed before source registration')
            if not adopted and not self.execution_source_matches(connection,reference['id'],row['project_id'],pid,execution_source):
                raise Conflict('New original owner does not use the selected conversation project')
            if connection.execute('SELECT count(*) FROM conversation_source_aliases WHERE conversation_id=?',
                                  (reference['id'],)).fetchone()[0] >= MAX_SOURCES:
                raise Conflict('Conversation source capacity reached; no owner was started')
            connection.execute('INSERT INTO conversation_source_aliases VALUES (?,?,?,?,?,?,?)',
                (alias_id, reference['id'], 'original', canonical(identity), raw, digest(source),
                 datetime.now(timezone.utc).isoformat()))

    def original_scope(self, pid, sid, reference=None, *, canonical_admission=None):
        """New MESSAGE admission; existing-task guidance uses its frozen scope."""
        source = {'kind': 'original', 'project_id': pid, 'session_ref': {'id': sid}}
        with self.store.connect() as connection:
            alias = self._alias(connection, source)
            if alias is None:
                if reference is not None:
                    raise Conflict('Original owner has no registered conversation source')
                return None
            row = self._entity(connection, alias['conversation_id'])
            if reference is not None:
                reference = ConversationRef.model_validate(reference).model_dump()
                if reference != {'id': row['conversation_id'], 'revision': row['revision']}:
                    raise Conflict('Conversation organization changed; refresh before starting a task')
            if row['deleted_at'] is not None:
                raise NotFound('Deleted conversations cannot receive new tasks')
            if not self._execution_project_matches(connection, row['project_id'], pid):
                # A verified canonical entry may continue the exact original
                # execution while supplying new organization as readonly data.
                # Legacy/raw sends keep the old guard. No workspace, native
                # action or target-project file permission is inherited here.
                _, checked = self.checked_alias(alias)
                admitted = canonical_admission
                if (not isinstance(admitted, dict) or reference is None
                        or admitted.get('conversation_ref') != reference
                        or admitted.get('policy') != 'READONLY_ORGANIZATION_CONTEXT_EXISTING_EXECUTION_PERMISSIONS_UNCHANGED'
                        or admitted.get('source', {}).get('project_id') != pid
                        or admitted.get('source', {}).get('session_ref') != checked['session_ref']):
                    raise Conflict('Conversation moved; new tasks must use its current project')
            return self._scope(connection, row)

    def note_message(self, scope, source, operation_id, request_sha256, *, created_at, user_text=None):
        """Idempotent organization/activity receipt; never a model execution receipt."""
        if scope is None:
            return None
        alias_id, _ = source_identity(source)
        key = digest([alias_id, operation_id])
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT * FROM conversation_message_admissions WHERE event_key=?', (key,)).fetchone()
            if old is not None:
                if old['request_sha256'] != request_sha256 or old['conversation_id'] != scope['conversation_ref']['id']:
                    raise Conflict('Message admission already belongs to another input')
                if digest(json.loads(old['scope_json'])) != old['scope_sha256']:
                    raise Conflict('Frozen message admission integrity changed')
                return json.loads(old['organization_json'])
            alias = self._alias(connection, source)
            if alias is None or alias['conversation_id'] != scope['conversation_ref']['id']:
                raise Conflict('Message source does not belong to this conversation')
            row = self._entity(connection, alias['conversation_id'])
            # A task already admitted before a later move/archive keeps its
            # original scope; it cannot undo that later organization change.
            revision, archived = row['revision'], bool(row['archived'])
            if (row['deleted_at'] is None and archived and revision == scope['conversation_ref']['revision']
                    and row['project_id'] == scope['project_id']):
                revision += 1; archived = False
                connection.execute('UPDATE conversation_organization SET revision=?,archived=0,updated_at=? WHERE conversation_id=?',
                                   (revision, created_at, row['conversation_id']))
            connection.execute('UPDATE conversation_threads SET activity_at=max(coalesce(activity_at,created_at),?) WHERE conversation_id=?',
                               (created_at, row['conversation_id']))
            if (isinstance(user_text, str) and not row['display_title'] and connection.execute(
                    'SELECT 1 FROM conversation_creations WHERE conversation_id=?',
                    (row['conversation_id'],)).fetchone() is not None):
                title = next((line.strip()[:100] for line in user_text.splitlines() if line.strip()), '')
                if title:
                    connection.execute('UPDATE conversation_threads SET display_title=? WHERE conversation_id=?',
                                       (title, row['conversation_id']))
            organization = {'revision': revision, 'archived': archived}
            connection.execute('INSERT INTO conversation_message_admissions VALUES (?,?,?,?,?,?,?,?,?)',
                (key, row['conversation_id'], alias_id, operation_id, request_sha256, canonical(scope), digest(scope),
                 canonical(organization), created_at))
            return organization
