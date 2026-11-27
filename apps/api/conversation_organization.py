"""Recoverable conversation organization; never rewrites execution identities or files."""
from datetime import datetime, timezone
import json
import re

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr, field_validator, model_validator
from typing import Literal

from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest

CONVERSATION_ID = re.compile(r'^(native|original|discovery):[a-f0-9]{64}$')


class ConversationSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: dict[str, JsonValue]

    @field_validator('source')
    @classmethod
    def bounded_source(cls, value):
        if len(json.dumps(value, ensure_ascii=False).encode()) > 8192:
            raise ValueError('Conversation reference is too large')
        return value


class OrganizationChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    action: Literal['archive', 'restore', 'move', 'delete']
    expected_revision: StrictInt = Field(ge=0)
    source: dict[str, JsonValue]
    target_project_id: StrictStr | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    delete_scope: Literal['CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES'] | None = None

    @model_validator(mode='after')
    def explicit_scope(self):
        if (self.action == 'move') != ('target_project_id' in self.model_fields_set):
            raise ValueError('Only move selects an explicit target project or default Chats')
        if self.action == 'delete' and self.delete_scope is None:
            raise ValueError('Confirm the exact conversation deletion scope')
        if self.action != 'delete' and 'delete_scope' in self.model_fields_set:
            raise ValueError('Deletion scope belongs only to deleting a conversation')
        return self

    def request_dict(self):
        body = self.model_dump(exclude={'target_project_id', 'delete_scope'})
        if self.action == 'move':
            body['target_project_id'] = self.target_project_id
        if self.action == 'delete':
            body['delete_scope'] = self.delete_scope
        return body

    @field_validator('source')
    @classmethod
    def bounded_source(cls, value):
        if len(json.dumps(value, ensure_ascii=False).encode()) > 8192:
            raise ValueError('Conversation reference is too large')
        return value


def organization_state(store, conversation_id):
    with store.connect() as connection:
        row = connection.execute('SELECT revision,archived FROM conversation_organization WHERE conversation_id=?',
                                 (conversation_id,)).fetchone()
    return {'revision': row['revision'], 'archived': bool(row['archived'])} if row else {'revision': 0, 'archived': False}


class ConversationOrganization:
    def __init__(self, store, resolve, threads):
        self.store, self.resolve, self.threads = store, resolve, threads

    def select(self, conversation_id, request):
        """Enroll an exact old source, without changing its organization.

        Repeating selection reads the current head. It never restores an archive,
        reconnects an owner, starts a model, or authorizes a message or file.
        """
        if not CONVERSATION_ID.fullmatch(conversation_id):
            raise NotFound('Unknown conversation')
        current = self.resolve(request.source)
        if current['id'] != conversation_id or digest(current['source']) != digest(request.source):
            raise Conflict('Conversation source changed; refresh before opening it')
        stamp = datetime.now(timezone.utc).isoformat()
        source_stamp = current.get('updated_at') or stamp
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self.threads.enroll(connection, current, created_at=source_stamp)
            if row['revision'] == 0:
                connection.execute('INSERT INTO conversation_organization '
                    '(conversation_id,revision,archived,updated_at) VALUES (?,1,0,?)',
                    (conversation_id, source_stamp))
        return {'version': 1, **self.threads.view(conversation_id)}

    @staticmethod
    def _result(row):
        try:
            value = json.loads(row['result_json'])
            if digest(value) != row['result_sha256']:
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise Conflict('Conversation organization receipt integrity changed') from None

    def operation(self, operation_id):
        with self.store.connect() as connection:
            row = connection.execute('SELECT * FROM conversation_organization_operations WHERE operation_id=?',
                                     (operation_id,)).fetchone()
        if row is None:
            raise NotFound('This conversation organization operation has no committed receipt')
        return self._result(row)

    def change(self, conversation_id, request):
        if not CONVERSATION_ID.fullmatch(conversation_id):
            raise NotFound('Unknown conversation identity')
        body = request.request_dict()
        checksum = digest({'conversation_id': conversation_id, **body})
        # Recover an already committed operation even if the original owner is now offline.
        with self.store.connect() as connection:
            previous = connection.execute('SELECT * FROM conversation_organization_operations WHERE operation_id=?',
                                          (request.operation_id,)).fetchone()
        if previous is not None:
            if previous['request_sha256'] != checksum:
                raise Conflict('Conversation operation is bound to another input')
            return self._result(previous)
        current = self.resolve(request.source)
        if current['id'] != conversation_id or digest(current['source']) != digest(request.source):
            raise Conflict('Conversation reference changed; refresh the list before organizing it')
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute('SELECT * FROM conversation_organization_operations WHERE operation_id=?',
                                          (request.operation_id,)).fetchone()
            if previous is not None:
                if previous['request_sha256'] != checksum:
                    raise Conflict('Conversation operation is bound to another input')
                return self._result(previous)
            row = connection.execute('SELECT revision,archived FROM conversation_organization WHERE conversation_id=?',
                                     (conversation_id,)).fetchone()
            revision, archived = (row['revision'], bool(row['archived'])) if row else (0, False)
            if revision != request.expected_revision:
                raise Conflict('Conversation organization changed in another window; refresh first')
            target = request.action == 'archive' if request.action in {'archive', 'restore'} else archived
            if request.action in {'archive', 'restore'} and target == archived:
                raise Conflict('Conversation already has this organization state; refresh first')
            updated = datetime.now(timezone.utc).isoformat()
            thread = self.threads.enroll(connection, current, created_at=updated)
            project_id = thread['project_id']
            if request.action == 'move':
                self.threads.move(connection, conversation_id, request.target_project_id, updated_at=updated)
                project_id = request.target_project_id
            elif request.action == 'delete':
                self.threads.delete(connection, conversation_id, updated_at=updated)
            result = {'version': 1, 'operation_id': request.operation_id, 'request_sha256': checksum,
                      'conversation_id': conversation_id, 'action': request.action,
                      'organization': {'revision': revision + 1, 'archived': target},
                      'project_id': project_id, 'execution_authorized': False,
                      'transcript_changed': False, 'files_changed': False, 'updated_at': updated}
            if request.action in {'move', 'delete'}:
                result.update(version=2, source_project_id=current['source']['project_id'],
                    conversation_removed=request.action == 'delete',
                    execution_records_retained=True, project_files_retained=True,
                    existing_execution_scope_changed=False, execution_stop_requested=False)
            connection.execute('INSERT INTO conversation_organization VALUES (?,?,?,?) '
                               'ON CONFLICT(conversation_id) DO UPDATE SET revision=excluded.revision,'
                               'archived=excluded.archived,updated_at=excluded.updated_at',
                               (conversation_id, revision + 1, int(target), updated))
            connection.execute('INSERT INTO conversation_organization_operations VALUES (?,?,?,?)',
                               (request.operation_id, checksum, json.dumps(result, ensure_ascii=False), digest(result)))
        return result


def register_conversation_organization(app, directory):
    owner = ConversationOrganization(app.state.store, directory.resolve, directory.threads)
    app.state.conversation_organization = owner

    def current_scope(value):
        # Selecting history and reading scope must use the same persisted
        # owner authority. Historical aliases retain their original order.
        return {**value, 'current_owner': app.state.canonical_conversations.current_owner(value)}

    @app.post('/api/conversations/{conversation_id}/selection')
    def select(conversation_id: str, request: ConversationSelection):
        return current_scope(owner.select(conversation_id, request))

    @app.post('/api/conversations/{conversation_id}/organization')
    def change(conversation_id: str, request: OrganizationChange):
        return owner.change(conversation_id, request)

    @app.get('/api/conversation-organization/operations/{operation_id}')
    def operation(operation_id: str):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise NotFound('Unknown conversation organization operation')
        return owner.operation(operation_id)

    @app.get('/api/conversations/{conversation_id}/scope')
    def scope(conversation_id: str):
        return current_scope({'version': 1, **directory.threads.view(conversation_id)})
