"""Bounded, observational conversation metadata. No transcript or execution owner is loaded."""
import base64
from collections import Counter
from datetime import datetime, timezone
import heapq
import json
import os
from pathlib import Path
import re
from typing import Literal

from fastapi import HTTPException, Query
from packages.domain.discovery_schema import RevisionRef
from packages.domain.planning_schema import WorkspaceRef
from packages.domain.tasks import digest
from packages.domain.store import Conflict, NotFound
from apps.api.conversation_organization import organization_state, register_conversation_organization
from packages.domain.conversation_threads import ConversationThreads

KINDS = ('native', 'original', 'discovery')
READ_LIMIT = 200
BYTE_LIMIT = 16 * 1024 * 1024
RECORD_LIMIT = 2 * 1024 * 1024
ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


def _title(value):
    if not isinstance(value, str):
        return None
    value = ' '.join(value.split()).strip()
    return value[:200] or None


def _time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Missing timestamp zone')
    delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def _iso(value):
    return datetime.fromtimestamp(value / 1_000_000_000, timezone.utc).isoformat()


def _ref(value):
    return None if value is None else RevisionRef.model_validate(value).model_dump()


def _workspace(value):
    return None if value is None else WorkspaceRef.model_validate(value).model_dump()


def _cursor(value, filters):
    if value is None:
        return None
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,2048}', value):
            raise ValueError()
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        decoded = json.loads(raw)
        if (set(decoded) != {'v', 'filters', 'after'} or decoded['v'] != 1 or
                decoded['filters'] != filters or not isinstance(decoded['after'], list) or len(decoded['after']) != 3):
            raise ValueError()
        stamp, rank, ident = decoded['after']
        if (type(stamp) is not int or not -(2 ** 63) <= stamp < 2 ** 63 or type(rank) is not int or
                not 0 <= rank < len(KINDS) or not isinstance(ident, str) or len(ident) > 250 or
                not re.fullmatch(r'[A-Za-z0-9_/-]+', ident)):
            raise ValueError()
        return stamp, rank, ident
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise HTTPException(422, 'Conversation cursor is invalid or belongs to another filter.') from None


def _next_cursor(after, filters):
    data = json.dumps({'v': 1, 'filters': filters, 'after': after}, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class ConversationDirectory:
    def __init__(self, app):
        self.app = app
        self.threads = ConversationThreads(app.state.store)

    def _candidates(self, filters, errors, headers):
        state = self.app.state
        try:
            for ident, source, stamp in self.threads.listing_sources():
                headers['organization'] += 1
                if source is None:
                    errors['organization', 'METADATA_UNAVAILABLE'] += 1
                    continue
                kind = source['kind']
                if kind == 'conversation':
                    if filters['kind'] not in ('all', 'native'):
                        continue
                    try:
                        header = self.threads.metadata_header(ident)
                    except (Conflict, NotFound, KeyError, ValueError, TypeError):
                        errors['organization', 'METADATA_UNAVAILABLE'] += 1
                        continue
                    size = len(json.dumps(header, ensure_ascii=False).encode())
                    yield (_time(stamp), KINDS.index('native'), 'conversation/' + ident), ('conversation', header, size)
                    continue
                if filters['kind'] not in ('all', kind): continue
                try:
                    candidate, observed_stamp = self._source_candidate(source)
                except (Conflict, NotFound, KeyError, ValueError, TypeError, OSError):
                    errors['organization', 'METADATA_UNAVAILABLE'] += 1
                    continue
                size = candidate[-1] * (2 if candidate[0] == 'native' else 1)
                yield (_time(stamp), KINDS.index(kind), 'organized/' + ident.replace(':', '/')), ('organized', candidate, observed_stamp, ident, size)
        except (Conflict, ValueError, KeyError):
            errors['organization', 'METADATA_UNAVAILABLE'] += 1
        if filters['kind'] in ('all', 'native'):
            source = getattr(state, 'native_messages', None)
            try:
                root = source.ws._path('native-messages')
                if root.is_symlink():
                    raise ValueError()
                if root.exists():
                    with os.scandir(root) as entries:
                        for entry in entries:
                            if not entry.name.endswith('.json') or not ID.fullmatch(entry.name[:-5]):
                                continue
                            headers['native'] += 1
                            try:
                                stat = entry.stat(follow_symlinks=False)
                                yield (stat.st_mtime_ns, 0, entry.name[:-5]), ('native', Path(entry.path), stat.st_size)
                            except OSError:
                                errors['native', 'METADATA_UNAVAILABLE'] += 1
            except (AttributeError, OSError, ValueError):
                errors['native', 'SOURCE_UNAVAILABLE'] += 1
        if filters['kind'] in ('all', 'original'):
            owner = getattr(state, 'agent_sessions', None)
            try:
                root = Path(owner.data_dir) / 'agent-sessions'
                if root.is_symlink():
                    raise ValueError()
                if root.exists():
                    with os.scandir(root) as projects:
                        for project in projects:
                            if (not ID.fullmatch(project.name) or project.is_symlink() or not project.is_dir()):
                                continue
                            with os.scandir(project.path) as sessions:
                                for session in sessions:
                                    if not re.fullmatch(r'[a-f0-9]{32}', session.name) or session.is_symlink() or not session.is_dir():
                                        continue
                                    headers['original'] += 1
                                    manifest, saved = Path(session.path) / 'manifest.json', Path(session.path) / 'state.json'
                                    try:
                                        a, b = manifest.stat(follow_symlinks=False), saved.stat(follow_symlinks=False)
                                        yield (max(a.st_mtime_ns, b.st_mtime_ns), 1, project.name + '/' + session.name), ('original', project.name, session.name, manifest, saved, a.st_size + b.st_size)
                                    except OSError:
                                        errors['original', 'METADATA_UNAVAILABLE'] += 1
            except (AttributeError, OSError, ValueError):
                errors['original', 'SOURCE_UNAVAILABLE'] += 1
        if filters['kind'] in ('all', 'discovery'):
            try:
                with state.store.connect() as connection:
                    where, args = '', []
                    rows = connection.execute('SELECT s.id,s.project_id,s.current_revision,v.sha256,v.created_at,'
                        'length(CAST(v.payload AS BLOB)) AS byte_length FROM discovery_sessions s '
                        'JOIN discovery_versions v ON v.session_id=s.id AND v.revision=s.current_revision' + where, args)
                    for row in rows:
                        headers['discovery'] += 1
                        try:
                            if not ID.fullmatch(row['id']) or not ID.fullmatch(row['project_id']):
                                raise ValueError()
                            yield (_time(row['created_at']), 2, row['project_id'] + '/' + row['id']), ('discovery', dict(row), row['byte_length'])
                        except (TypeError, ValueError):
                            errors['discovery', 'METADATA_UNAVAILABLE'] += 1
            except Exception:
                errors['discovery', 'SOURCE_UNAVAILABLE'] += 1

    def _item(self, candidate, sort_key):
        state, kind = self.app.state, candidate[0]
        if kind == 'conversation':
            return candidate[1]
        if kind == 'organized':
            source = candidate[1]
            actual = self._item(source, (candidate[2], KINDS.index(source[0]), ''))
            if actual['id'] != candidate[3]:
                raise Conflict('The organized source no longer belongs to this conversation')
            return actual
        if kind == 'native':
            from apps.api.native_messages import question_project
            saved, request = state.native_messages._ide_record(candidate[1])
            pid, raw_pid = question_project(request), request['project_id']
            cid = request.get('conversation_id')
            if not isinstance(cid, str) or not ID.fullmatch(cid):
                raise ValueError('No original discussion identity')
            file = request.get('file_source_ref')
            workspace = {'id': file['workspace_id'], 'revision': file['binding_revision'],
                'binding_sha256': file['binding_sha256']} if file else None
            origin = 'CURRENT_HOST' if saved['generation'] == state.native_messages.generation else 'HISTORICAL_READONLY'
            source = {'kind': kind, 'project_id': pid, 'request_project_id': raw_pid,
                'conversation_id': cid, 'last_operation_id': request['operation_id'],
                'request_sha256': saved['request_sha256'], 'record_origin': origin,
                'provider_profile_ref': _ref(request.get('provider_profile_ref')), 'workspace_ref': _workspace(workspace)}
            identity, title = [pid, cid], None
            readonly = origin != 'CURRENT_HOST' or pid != raw_pid or request.get('native_review') is not None
        elif kind == 'original':
            _, pid, sid, manifest_path, state_path, _ = candidate
            if manifest_path.is_symlink() or state_path.is_symlink():
                raise ValueError('Linked metadata source')
            manifest, reference = state.agent_sessions._manifest(pid, sid)
            if manifest.get('native_message_admission') or manifest.get('native_review_admission'):
                return None  # An execution owner is part of its native chat, not a second user conversation.
            saved = state.agent_sessions._state(pid, sid)
            generation = saved.get('generation')
            if type(generation) is not int or generation < 0:
                raise ValueError('Invalid recorded generation')
            request = manifest['request']
            source = {'kind': kind, 'project_id': pid, 'session_ref': reference,
                'generation': generation, 'provider_profile_ref': _ref(request.get('provider_profile_ref')),
                'workspace_ref': _workspace(request.get('workspace_ref'))}
            identity, title = [pid, sid], _title(manifest.get('title', request.get('title')))
            readonly = saved.get('owner_status') != 'READY'
        else:
            row = candidate[1]
            pid, sid, revision = row['project_id'], row['id'], row['current_revision']
            with state.store.connect() as connection:
                actual = connection.execute('SELECT payload,sha256 FROM discovery_versions WHERE session_id=? AND revision=?', (sid, revision)).fetchone()
            payload = state.discovery._checked(actual)
            if (payload['id'], payload['project_id'], payload['revision']) != (sid, pid, revision) or actual['sha256'] != row['sha256']:
                raise ValueError('Object metadata basis changed')
            source = {'kind': kind, 'project_id': pid, 'object_ref': {'id': sid, 'revision': revision, 'sha256': row['sha256']},
                'workspace_ref': _workspace(payload.get('workspace_ref'))}
            identity, title = [pid, sid], _title(payload.get('candidate', {}).get('title'))
            readonly = False
        if pid is not None and not ID.fullmatch(pid):
            raise ValueError('Invalid original project')
        ident = kind + ':' + digest(identity)
        organized = self.threads.overlay(source)
        if kind == 'original' and request.get('conversation_ref') is not None and organized is None:
            return None  # A failed/pending owner binding is not a second user chat.
        if organized is not None:
            ident, pid = organized['id'], organized['project_id']
            if self.threads.has_creation(ident):
                return None if organized['deleted'] else self.threads.metadata_header(ident)
            with state.store.connect() as connection:
                same_execution_project = (self.threads._execution_project_matches(connection, pid, source['project_id'])
                    if kind == 'original' else source['project_id'] == pid)
            readonly = readonly or not same_execution_project
            title = organized['display_title'] or title
        with state.store.connect() as connection:
            project = connection.execute('SELECT name,description FROM projects WHERE id=?', (pid,)).fetchone() if pid else None
        default_chat = bool(organized is None and kind == 'original' and source['workspace_ref'] is None
            and project is not None and project['description'] in {
                'eeas.original-agent-session.internal-project.v1', 'EEAS_INTERNAL_DESKTOP_RUNTIME_V1'})
        if default_chat:
            pid, project = None, None
        return {'id': ident, 'kind': kind, 'project_id': pid,
            'project_name': _title(project['name']) if project else None, 'title': title,
            'updated_at': max(_iso(sort_key[0]), organized['updated_at']) if organized else _iso(sort_key[0]), 'read_only': readonly, 'source': source,
            'organization': {'revision': organized['revision'], 'archived': organized['archived']} if organized else organization_state(state.store, ident),
            **({'project_assignment': 'DEFAULT_CHATS'} if default_chat else {}),
            **({'conversation_ref': {'id': ident, 'revision': organized['revision']},
                'conversation_deleted': organized['deleted'], 'conversation_origin': organized['origin']} if organized is not None else {})}

    def _source_candidate(self, source):
        kind, pid = source['kind'], source['project_id']
        if pid is not None and (not isinstance(pid, str) or not ID.fullmatch(pid)):
            raise ValueError()
        if kind == 'native':
            operation = source['last_operation_id']
            if not isinstance(operation, str) or not ID.fullmatch(operation):
                raise ValueError()
            path = self.app.state.native_messages.path(operation)
            if path.is_symlink():
                raise ValueError()
            info = path.stat()
            if info.st_size > RECORD_LIMIT:
                raise ValueError()
            candidate, stamp = ('native', path, info.st_size), info.st_mtime_ns
        elif kind == 'original':
            sid = source['session_ref']['id']
            if pid is None or not isinstance(sid, str) or not re.fullmatch(r'[a-f0-9]{32}', sid):
                raise ValueError()
            root = Path(self.app.state.agent_sessions.data_dir) / 'agent-sessions' / pid / sid
            if any(part.is_symlink() for part in (root, root.parent, root.parent.parent)):
                raise ValueError()
            manifest, saved = root / 'manifest.json', root / 'state.json'
            if manifest.is_symlink() or saved.is_symlink():
                raise ValueError()
            a, b = manifest.stat(), saved.stat()
            if a.st_size + b.st_size > RECORD_LIMIT * 2:
                raise ValueError()
            candidate, stamp = ('original', pid, sid, manifest, saved, a.st_size + b.st_size), max(a.st_mtime_ns, b.st_mtime_ns)
        elif kind == 'discovery':
            sid = source['object_ref']['id']
            if pid is None or not isinstance(sid, str) or not ID.fullmatch(sid):
                raise ValueError()
            with self.app.state.store.connect() as connection:
                row = connection.execute('SELECT s.id,s.project_id,s.current_revision,v.sha256,v.created_at,'
                    'length(CAST(v.payload AS BLOB)) AS byte_length FROM discovery_sessions s '
                    'JOIN discovery_versions v ON v.session_id=s.id AND v.revision=s.current_revision '
                    'WHERE s.id=? AND s.project_id=?', (sid, pid)).fetchone()
            if row is None or row['byte_length'] > RECORD_LIMIT:
                raise ValueError()
            candidate, stamp = ('discovery', dict(row), row['byte_length']), _time(row['created_at'])
        else:
            raise ValueError()
        return candidate, stamp

    def resolve(self, source):
        """Read one exact legacy binding, without scanning a directory or connecting an owner."""
        try:
            if source.get('kind') == 'conversation':
                return self.threads.metadata_header(source['conversation_ref']['id'])
            candidate, stamp = self._source_candidate(source)
            item = self._item(candidate, (stamp, KINDS.index(candidate[0]), ''))
            if item is None:
                raise Conflict('Internal execution owners are not separate user conversations')
            return item
        except (KeyError, TypeError, ValueError, OSError, AttributeError, Conflict, NotFound):
            raise Conflict('This conversation reference is unavailable; refresh before organizing it') from None

    def read(self, *, limit=50, cursor=None, kind='all', scope='all', project_id=None, state='active'):
        if scope == 'project':
            if not isinstance(project_id, str) or not ID.fullmatch(project_id):
                raise HTTPException(422, 'An explicit project is required for this filter.')
        elif project_id is not None:
            raise HTTPException(422, 'Project filtering must be explicit.')
        filters = {'kind': kind, 'scope': scope, 'project_id': project_id}
        if state != 'active':
            filters['state'] = state
        after = _cursor(cursor, filters)
        errors, headers, items = Counter(), Counter(), {}
        candidates = heapq.nlargest(READ_LIMIT + 1,
            (row for row in self._candidates(filters, errors, headers) if after is None or row[0] < after), key=lambda row: row[0])
        bytes_budgeted, read, position, last, budget_hit = 0, 0, 0, None, False
        for sort_key, candidate in candidates[:READ_LIMIT]:
            size = candidate[-1] * (2 if candidate[0] == 'native' else 1)
            if size <= RECORD_LIMIT * 2 and bytes_budgeted + size > BYTE_LIMIT:
                budget_hit = True
                break
            position += 1
            last = sort_key
            if size > RECORD_LIMIT * 2:
                errors[candidate[0], 'RECORD_TOO_LARGE'] += 1
                continue
            bytes_budgeted += size
            read += 1
            try:
                item = self._item(candidate, sort_key)
                if item is None or item.get('conversation_deleted'):
                    continue
                if state != 'all' and item['organization']['archived'] != (state == 'archived'):
                    continue
                if scope == 'project' and item['project_id'] != project_id or scope == 'unbound' and item['project_id'] is not None:
                    continue
                if item['id'] not in items:
                    items[item['id']] = item
                if len(items) >= limit:
                    break
            except Exception:
                errors[candidate[0], 'RECORDED_METADATA_UNAVAILABLE'] += 1
        more = budget_hit or position < len(candidates)
        partial = more or bool(errors)
        return {'version': 1, 'organization_supported': True, 'items': list(items.values()), 'next_cursor': _next_cursor(last, filters) if more and last else None,
            'coverage': {'consistency': 'LIVE_OBSERVATIONAL_KEYSET', 'order': 'OBSERVED_UPDATED_AT_DESC_KIND_ID',
                'complete': not partial, 'partial': partial, 'has_more': more,
                'candidates_read': read, 'candidate_limit': READ_LIMIT, 'metadata_headers_observed': dict(headers),
                'bytes_budgeted': bytes_budgeted, 'byte_limit': BYTE_LIMIT, 'byte_basis': 'PRE_READ_RECORD_SIZE_CONSERVATIVE',
                'errors': [{'source': key[0], 'code': key[1], 'count': count} for key, count in sorted(errors.items())],
                'model_calls': 0}, 'execution_authorized': False}


def register_conversation_directory(app):
    reader = ConversationDirectory(app)
    app.state.conversation_directory = reader
    app.state.conversation_threads = reader.threads
    from apps.api.conversation_creation import register_conversation_creation
    register_conversation_creation(app, reader)
    register_conversation_organization(app, reader)
    from apps.api.conversation_history import register_conversation_history
    register_conversation_history(app, reader.threads)
    from apps.api.conversation_runtime import ConversationRuntime
    app.state.conversation_runtime = ConversationRuntime(app, reader.threads, app.state.conversation_history)
    app.state.agent_sessions.conversations = app.state.conversation_runtime
    from apps.api.canonical_conversations import register_canonical_conversations
    register_canonical_conversations(app)

    @app.get('/api/conversations')
    def listing(limit: int = Query(default=50, ge=1, le=100), cursor: str | None = Query(default=None, max_length=2048),
                kind: Literal['all', 'native', 'original', 'discovery'] = 'all',
                scope: Literal['all', 'project', 'unbound'] = 'all', project_id: str | None = None,
                state: Literal['active', 'archived', 'all'] = 'active'):
        return reader.read(limit=limit, cursor=cursor, kind=kind, scope=scope, project_id=project_id, state=state)
