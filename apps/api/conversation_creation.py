"""Idempotent metadata-only creation; no model, runtime, workspace or file grant."""
from datetime import datetime, timezone
import json
import re
import uuid

from pydantic import Field, StrictStr
from packages.domain.questions import RequestModel
from packages.domain.conversation_threads import source_identity
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest


class CreateConversation(RequestModel):
    operation_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    project_id: StrictStr | None = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    title: StrictStr | None = Field(default=None, max_length=120)


class ConversationCreation:
    def __init__(self, store, threads):
        self.store, self.threads = store, threads

    @staticmethod
    def checked(row):
        if row is None:
            raise NotFound('No committed conversation creation was found')
        try:
            if len(row['result_json'].encode()) > 8192:
                raise ValueError()
            value = json.loads(row['result_json'])
            if (digest(value) != row['result_sha256'] or value['operation_id'] != row['operation_id'] or
                    value['request_sha256'] != row['request_sha256'] or
                    value['conversation_ref']['id'] != row['conversation_id']):
                raise ValueError()
            return value
        except (ValueError, TypeError, KeyError):
            raise Conflict('Conversation creation receipt integrity changed') from None

    def operation(self, operation_id):
        with self.store.connect() as connection:
            return self.checked(connection.execute('SELECT * FROM conversation_creations WHERE operation_id=?',
                                                   (operation_id,)).fetchone())

    def create(self, body):
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            return self.create_in(connection, body)

    def create_in(self, connection, body):
        """Create the child and its native address in the caller's transaction."""
        request = body.model_dump()
        checksum, stamp = digest(request), datetime.now(timezone.utc).isoformat()
        old = connection.execute('SELECT * FROM conversation_creations WHERE operation_id=?',
                                 (body.operation_id,)).fetchone()
        if old is not None:
            if old['request_sha256'] != checksum:
                raise Conflict('This creation operation belongs to another input')
            return self.checked(old)
        if body.project_id is not None:
            project = connection.execute('SELECT description FROM projects WHERE id=?', (body.project_id,)).fetchone()
            if project is None or project['description'] in {
                    'eeas.original-agent-session.internal-project.v1', 'EEAS_INTERNAL_DESKTOP_RUNTIME_V1'}:
                raise NotFound('Choose an available user project or default Chats')
        cid = 'chat_' + uuid.uuid4().hex
        ident, _ = source_identity({'kind': 'native', 'project_id': body.project_id, 'conversation_id': cid})
        connection.execute('INSERT INTO conversation_threads '
            '(conversation_id,initial_kind,project_id,created_at,deleted_at,display_title,activity_at) '
            'VALUES (?,?,?,?,NULL,?,?)', (ident, 'native', body.project_id, stamp, (body.title or '').strip() or None, stamp))
        route = self.threads.reserve_route(connection, ident, body.project_id, created_at=stamp, preferred_cid=cid)
        connection.execute('INSERT INTO conversation_organization VALUES (?,1,0,?)', (ident, stamp))
        result = {'version': 1, 'operation_id': body.operation_id, 'request_sha256': checksum,
            'conversation_ref': {'id': ident, 'revision': 1}, 'project_id': body.project_id,
            'next_message': {'project_id': body.project_id, 'conversation_id': route['native_conversation_id']},
            'created_at': stamp, 'execution_started': False, 'messages_created': 0,
            'model_calls': 0, 'file_write_authorized': False, 'automatic_input_authorized': False}
        connection.execute('INSERT INTO conversation_creations VALUES (?,?,?,?,?)',
            (body.operation_id, checksum, ident, canonical(result), digest(result)))
        return result


def register_conversation_creation(app, directory):
    owner = ConversationCreation(app.state.store, directory.threads)
    app.state.conversation_creation = owner

    @app.post('/api/conversations')
    def create(body: CreateConversation):
        return owner.create(body)

    @app.get('/api/conversation-creations/{operation_id}')
    def operation(operation_id: str):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise NotFound('Unknown conversation creation operation')
        return owner.operation(operation_id)
