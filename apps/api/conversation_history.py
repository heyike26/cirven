"""Stored conversation projections. No owner connection, model call or write.

Logical membership selects sources; each displayed text still names its exact
execution origin. Saved observation windows are explicitly bounded, not a claim
that all original runtime history has been replicated.
"""
import base64
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import heapq
import json
import os
from pathlib import Path
import re

from fastapi import Query
from pydantic import Field
from packages.domain.questions import RequestModel, Hash, Id
from packages.domain.context_graph import SourceRef
from packages.agent_sessions.service import stored_record_guard
from packages.agent_sessions.outcomes import original_auth_rejection, AUTH_REJECTION_TEXT
from packages.domain.original_response import original_answer
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest
from packages.domain.context_read_budget import current_context_read_budget
from apps.api.conversation_source_anchors import ConversationSourceAnchors

BYTE_LIMIT = 16 * 1024 * 1024
READ_LIMIT = 512
NATIVE_WINDOW = 256
ORIGINAL_WINDOW = 200
ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


class ReadBudgetLimit(Conflict):
    pass


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate source field')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite source value')))


class ReadBudget:
    def __init__(self):
        self.bytes = 0
        self.reads = 0
        self.cache = {}
        self.errors = Counter()
        self.basis = {}
        self.stage_limit = BYTE_LIMIT

    def error(self, source, code):
        self.errors[source, code] += 1

    def reserve(self, size):
        if size < 0 or size > min(BYTE_LIMIT, self.stage_limit) - self.bytes or self.reads >= READ_LIMIT:
            raise ReadBudgetLimit('Shared history read budget reached')
        allowance = current_context_read_budget()
        if allowance is not None:
            allowance.charge(size)
        self.reads += 1

    def file(self, path, *, native=False):
        # These original records are replaced by AgentSessions on observation
        # and wire callbacks. Use its existing per-record guard; otherwise a
        # healthy replacement between stat/open looks like a missing history.
        # Native input records have their separate writer and validation.
        with nullcontext() if native else stored_record_guard():
            return self._file(path, native=native)

    def _file(self, path, *, native=False):
        path = Path(path)
        if path in self.cache:
            return self.cache[path]
        if path.is_symlink():
            raise Conflict('History source is a link')
        before = path.stat()
        maximum = 2 * 1024 * 1024 if native else 8 * 1024 * 1024
        if before.st_size > maximum:
            raise Conflict('History record is oversized')
        self.reserve(before.st_size)
        # Read exactly the observed bound; growth cannot force an unbounded
        # allocation. fstat also rejects a replacement between stat and open.
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise Conflict('History source changed before reading')
            raw = stream.read(before.st_size)
            self.bytes += len(raw)
            after = os.fstat(stream.fileno())
        if (len(raw) != before.st_size or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns):
            raise Conflict('History source changed during reading')
        wrapper = _json(raw)
        key = 'payload' if native else 'value'
        if (not isinstance(wrapper, dict) or key not in wrapper
                or digest(wrapper[key]) != wrapper.get('sha256')):
            raise Conflict('History record integrity differs')
        value = wrapper[key]
        self.cache[path] = value
        self.basis[str(path)] = hashlib.sha256(raw).hexdigest()
        return value

    def sql_json(self, connection, table, key, where, args, *, maximum=8 * 1024 * 1024):
        # All identifiers/predicates are constants chosen inside this module.
        cache_key = (table, key, where, args)
        if cache_key in self.cache:
            return self.cache[cache_key]
        size = connection.execute(f'SELECT length(cast({key} AS blob)) FROM {table} WHERE {where}', args).fetchone()
        if size is None or size[0] is None or size[0] > maximum:
            raise Conflict('Stored history payload is unavailable or oversized')
        self.reserve(size[0])
        raw = connection.execute(f'SELECT substr(cast({key} AS blob),1,?) FROM {table} WHERE {where}',
                                 (min(size[0], BYTE_LIMIT - self.bytes), *args)).fetchone()[0]
        self.bytes += len(raw)
        if len(raw) != size[0]:
            raise Conflict('Stored history payload changed while reading')
        value = _json(raw)
        self.cache[cache_key] = value
        self.basis[canonical(cache_key)] = hashlib.sha256(raw).hexdigest()
        return value


class StoredOwner:
    """The existing exact-answer selector gets only these bounded disk reads."""
    def __init__(self, owner, budget, pid, sid, reference):
        self.owner, self.budget, self.pid, self.sid, self.reference = owner, budget, pid, sid, reference

    def operation(self, pid, sid, operation_id):
        if pid != self.pid or sid != self.sid or not isinstance(operation_id, str) or not ID.fullmatch(operation_id):
            raise Conflict('Original history operation scope differs')
        value = self.budget.file(self.owner._path(pid, sid, 'operations', digest(operation_id) + '.json'))
        if (value.get('id') != operation_id or value.get('session_ref') != self.reference
                or value.get('request_sha256') != digest(value.get('request'))):
            raise Conflict('Original history operation integrity differs')
        return value


class ConversationHistory(ConversationSourceAnchors):
    def __init__(self, app, *, native=None, threads=None):
        self.app = app
        self.native = native if native is not None else app.state.native_messages
        self.threads = threads if threads is not None else getattr(app.state, 'conversation_threads', None)

    @staticmethod
    def _recent(paths, maximum, budget, source, before=None, excluded=()):
        count = 0
        def candidates():
            nonlocal count
            for path in paths:
                try:
                    if path.stem in excluded or path.is_symlink() or not path.is_file() or not ID.fullmatch(path.stem):
                        continue
                    stamp = path.stat().st_mtime_ns
                    key = (stamp, path.stem)
                    if before is not None and key >= tuple(before):
                        continue
                    count += 1
                    yield key, path
                except OSError:
                    budget.error(source, 'SOURCE_METADATA_UNAVAILABLE')
        values = heapq.nlargest(maximum, candidates(), key=lambda item: item[0])
        return values, count > maximum

    @staticmethod
    def _stamp(value, fallback):
        try:
            if isinstance(value, str):
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is not None:
                    return parsed.astimezone(timezone.utc).isoformat()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
        return datetime.fromtimestamp(fallback / 1_000_000_000, timezone.utc).isoformat()

    @staticmethod
    def _row(source, key, role, text, stamp, *, ordinal=0, **extra):
        if not isinstance(text, str):
            raise Conflict('History text is not typed source content')
        return {'id': digest([source['id'], key, role]), 'role': role, 'text': text[:64000],
            'text_truncated': len(text) > 64000, 'text_original_length': len(text),
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'observed_at': stamp,
            'source': {'alias_id': source['id'], **source['identity']},
            'history_role': 'READONLY_DATA_NO_EXECUTION_OR_FILE_GRANT',
            '_ordinal': ordinal, **extra}

    def _stored_task(self, record, budget):
        key = ('native_task', record['request']['operation_id'], record['request_sha256'])
        if key in budget.cache:
            return budget.cache[key]
        owner = record['owner']
        pid, reference = owner['project_id'], owner['session_ref']
        sid = reference['id']
        service = self.app.state.agent_sessions
        manifest = budget.file(service._path(pid, sid, 'manifest.json'))
        if (manifest.get('id') != sid or manifest.get('project_id') != pid
                or reference != {'id': sid, 'revision': 1, 'sha256': digest(manifest)}):
            raise Conflict('Stored native answer owner differs')
        state = budget.file(service._path(pid, sid, 'state.json'))
        proxy = StoredOwner(service, budget, pid, sid, reference)
        operation = proxy.operation(pid, sid, record['native_operation_id'])
        if operation.get('request', {}).get('raw_text') != record['request']['raw_text']:
            raise Conflict('Stored native answer belongs to a different user question')
        session = {**state, 'project_id': pid, 'session_ref': reference}
        result = proxy, pid, sid, session, operation, owner['generation']
        budget.cache[key] = result
        return result

    def _owner(self, record, budget):
        return original_answer(*self._stored_task(record, budget))

    def _native_guidance(self, record, source, budget):
        if not record.get('owner') or record.get('answer') is not None and record.get('user_turns', 0) <= 1:
            return []
        proxy, _, _, session, operation, generation = self._stored_task(record, budget)
        if session.get('generation') != generation or operation.get('generation') != generation:
            raise Conflict('Historical guidance owner generation differs')
        identifiers = operation.get('continuation_guidance_ids', [])
        if session.get('guidance_cohort_operation_id') == operation['id']:
            identifiers = session.get('guidance_operation_ids', [])
        if not isinstance(identifiers, list) or len(identifiers) > 32 or len(set(identifiers)) != len(identifiers):
            raise Conflict('Historical guidance window is invalid')
        rows = []
        for index, ident in enumerate(identifiers):
            guide = proxy.operation(proxy.pid, proxy.sid, ident)
            if (guide.get('kind') != 'GUIDANCE' or guide.get('generation') != generation
                    or guide.get('original_session_id') != operation.get('original_session_id')
                    or guide.get('original_active_operation_id') != operation['id']
                    or guide.get('request', {}).get('expected_active_operation_id') != operation['id']):
                raise Conflict('Historical guidance belongs to another exact task')
            rows.append(self._row(source, record['request']['operation_id'] + '/guide/' + ident,
                'user', guide['request']['raw_text'], self._stamp(guide.get('created_at'), 0), ordinal=index + 1,
                operation_id=ident, native_parent_operation_id=record['request']['operation_id'],
                turn_id=source['id'] + '/' + record['request']['operation_id'],
                source_sha256=guide['request_sha256'], status=guide['status'],
                session_ref=proxy.reference, user_input_kind='ORIGINAL_TASK_GUIDANCE'))
        return rows

    def _native_answer(self, record, budget):
        if record.get('answer') is not None:
            answer = record['answer']
            text = answer.get('text', '')
            if not isinstance(text, str):
                raise Conflict('Stored ordinary answer is not user-facing text')
            return text, record.get('status'), 'COMMITTED_ORDINARY_ANSWER'
        if record.get('native_message_ref') is not None:
            # Ordinary SDK text is an internal structured-answer envelope.
            # Publication commits record.answer; an uncommitted/failed SDK
            # return must never leak through a raw-text history fallback.
            if record.get('status') == 'RETURNED':
                budget.error('native', 'COMMITTED_ORDINARY_ANSWER_UNAVAILABLE')
            return '', record.get('status'), 'ORDINARY_ANSWER_NOT_COMMITTED'
        if record.get('owner'):
            projected = self._owner(record, budget)
            if projected['answer_status'] in {'PARTIAL', 'UNAVAILABLE'}:
                budget.error('native', 'EXACT_FINAL_ANSWER_' + projected['answer_status'])
            return projected['answer_text'], record.get('status'), projected['text_coverage']
        execution = record.get('execution')
        if execution:
            with self.app.state.store.connect() as connection:
                row = connection.execute('SELECT execution_status,result IS NOT NULL AS has_result FROM agent_executions WHERE id=? AND project_id=? AND runtime=?',
                    (execution['id'], execution['project_id'], self.native.transport.runtime)).fetchone()
                if row is None:
                    raise Conflict('Stored native execution scope differs')
                if not row['has_result']:
                    return '', row['execution_status'], 'NO_SAVED_ANSWER'
                result = budget.sql_json(connection, 'agent_executions', 'result', 'id=? AND project_id=? AND runtime=?',
                    (execution['id'], execution['project_id'], self.native.transport.runtime), maximum=2 * 1024 * 1024)
                answer = result.get('candidate_json') or {}
                text = answer.get('text', '')
                if not isinstance(text, str):
                    raise Conflict('Stored native result is not user-facing text')
                return text, row['execution_status'], 'STORED_EXECUTION_RESULT'
        return '', record.get('status'), 'NO_SAVED_ANSWER'

    def _native_context(self, record, budget):
        """Saved private-owner issue, charged to the same history read budget."""
        owner = record.get('owner')
        ref = record.get('native_message_ref') or record.get('review_ref')
        if not owner or not ref:
            return {}
        try:
            from apps.api.context_recovery import ContextIssue
            from packages.agent_sessions.models import SessionRef
            ref = SessionRef.model_validate(ref).model_dump()
            sessions = self.app.state.agent_sessions
            pid, sid = owner['project_id'], owner['session_ref']['id']
            manifest = budget.file(sessions._path(pid, sid, 'manifest.json'))
            if (manifest.get('project_id') != pid or manifest.get('id') != sid or
                    owner['session_ref'] != {'id': sid, 'revision': 1, 'sha256': digest(manifest)} or
                    not (manifest.get('native_message_admission') or manifest.get('native_review_admission'))):
                raise Conflict('History private owner manifest differs')
            state = budget.file(sessions._path(pid, sid, 'state.json'))
            if state.get('context_authority_issue') is None:
                return {}
            issue = ContextIssue.model_validate(state['context_authority_issue']).model_dump()
            if state['generation'] != owner['generation'] or issue['generation'] != owner['generation']:
                raise Conflict('History source issue belongs to another owner generation')
            return {'native_context_issue': issue, 'native_context_recovery': {
                'parent_operation_id': record['request']['operation_id'], 'expected_native_ref': deepcopy(ref),
                'record_current': record['generation'] == self.native.generation, 'read_only': True, 'model_calls': 0}}
        except (Conflict, NotFound, ValueError, TypeError, KeyError, AttributeError, OSError):
            budget.error('native', 'SOURCE_CONTEXT_OBSERVATION_UNAVAILABLE')
            return {}

    def _native_rows(self, sources, budget, *, before=None, exclude=None):
        from apps.api.native_messages import NativeQuestionInput, question_project
        aliases = {(row['identity']['project_id'], row['identity']['conversation_id']): row
                   for row in sources if row['identity']['kind'] == 'native'}
        if not aliases:
            return [], None
        pinned = {source['original_source']['last_operation_id']: source for source in sources
            if source['identity']['kind'] == 'native' and source.get('original_source') is not None}
        candidates, more = self._recent(self.native.ws._path('native-messages').glob('*.json'),
                                        NATIVE_WINDOW, budget, 'native', before, excluded=pinned)
        observed = []
        if before is None:
            for opid in pinned:
                try:
                    path = self.native.path(opid)
                    observed.append((True, (path.stat().st_mtime_ns, opid), path))
                except (OSError, Conflict):
                    budget.error(pinned[opid]['id'], 'ORIGINAL_NATIVE_SOURCE_UNAVAILABLE')
        observed.extend((False, key, path) for key, path in candidates)
        rows, last = [], None
        for is_pinned, (stamp, _), path in observed:
            previous = last
            if not is_pinned:
                last = (stamp, path.stem)
            if path.stem == exclude:
                continue
            try:
                record = budget.file(path, native=True)
                request = record['request']
                body = NativeQuestionInput.model_validate(request).request_dict()
                if digest(request) != record['request_sha256'] or self.native.path(body['operation_id']) != path:
                    raise Conflict('Stored native question integrity differs')
                if is_pinned and record['request_sha256'] != pinned[path.stem]['original_source']['request_sha256']:
                    raise Conflict('Original pinned native question bytes changed')
                source = aliases.get((question_project(body), body.get('conversation_id')))
                if source is None or body['operation_id'] == exclude:
                    continue
                observed = self._stamp(record.get('created_at'), stamp)
                scope = {'operation_id': body['operation_id'], 'request_sha256': record['request_sha256'],
                    'source_sha256': record['request_sha256'], 'status': record.get('status'),
                    'context_ref': body.get('context_ref'), 'turn_id': source['id'] + '/' + body['operation_id']}
                historical_unknown = record.get('generation') != self.native.generation and record.get('status') == 'PROCESSING'
                if historical_unknown:
                    scope['status'] = 'UNKNOWN_OUTCOME'
                scope.update(self._native_context(record, budget))
                user_row = self._row(source, body['operation_id'], 'user', body['raw_text'], observed,
                    input_attachments=body.get('input_attachments', []), native_feedback_ref=body.get('native_feedback_ref'),
                    file_source_ref=body.get('file_source_ref'), **scope)
                if record.get('reviewed_context') is not None:
                    try:
                        self.native.reviewed_selection_id(record)
                        user_row['context_summary'] = deepcopy(record['reviewed_context'])
                    except Conflict:
                        user_row['context_summary'] = {'unavailable': True}
                        budget.error(source['id'], 'ORIGINAL_CONTEXT_DECISION_UNAVAILABLE')
                rows.append(user_row)
                try:
                    rows.extend(self._native_guidance(record, source, budget))
                except (Conflict, NotFound, ValueError, TypeError, KeyError, AttributeError, OSError):
                    budget.error(source['id'], 'ORIGINAL_GUIDANCE_UNAVAILABLE')
                try:
                    answer, status, coverage = self._native_answer(record, budget)
                    if historical_unknown:
                        status = 'UNKNOWN_OUTCOME'
                    user_row['status'] = status
                    if answer:
                        rows.append(self._row(source, body['operation_id'], 'assistant', answer,
                            self._stamp(None, stamp), ordinal=10000, answer_coverage=coverage, **{**scope, 'status': status}))
                except (Conflict, NotFound, ValueError, TypeError, KeyError, AttributeError, OSError):
                    budget.error(source['id'], 'ANSWER_UNAVAILABLE')
            except ReadBudgetLimit:
                budget.error('native', 'SHARED_READ_BUDGET_REACHED')
                # The unread candidate belongs to the next window. Do not
                # advance to the end of the preselected 256 and skip it.
                return rows, list(previous) if previous else (before or [2 ** 63 - 1, 'zz'])
            except (Conflict, NotFound, ValueError, TypeError, KeyError, AttributeError, OSError):
                budget.error('native', 'QUESTION_SOURCE_UNAVAILABLE')
        return rows, list(candidates[-1][0]) if more and candidates else None

    def _original_rows(self, source, budget, exclude=None):
        identity, original = source['identity'], source['original_source']
        pid, sid = identity['project_id'], identity['session_id']
        owner = self.app.state.agent_sessions
        manifest = budget.file(owner._path(pid, sid, 'manifest.json'))
        reference = {'id': sid, 'revision': 1, 'sha256': digest(manifest)}
        if (manifest.get('project_id') != pid or manifest.get('id') != sid
                or reference != original['session_ref'] or manifest.get('native_message_admission')
                or manifest.get('native_review_admission')):
            raise Conflict('Original conversation is not this exact public source')
        state_path = owner._path(pid, sid, 'state.json')
        state = budget.file(state_path)
        stamp = state_path.stat().st_mtime_ns
        operations, more = self._recent(owner._path(pid, sid, 'operations').glob('*.json'),
                                         ORIGINAL_WINDOW, budget, source['id'])
        if more:
            budget.error(source['id'], 'ORIGINAL_OPERATION_WINDOW_TRUNCATED')
        users, rows, user_operations = {}, [], {}
        for (modified, _), path in reversed(operations):
            try:
                operation = budget.file(path)
                request = operation['request']
                if operation.get('kind') not in {'MESSAGE', 'GUIDANCE'}:
                    continue
                opid = operation['id']
                if exclude == (pid, sid, opid):
                    continue
                if (path.stem != digest(opid) or operation['session_ref'] != reference
                        or request.get('session_ref') != reference or digest(request) != operation['request_sha256']):
                    raise Conflict('Original user request scope differs')
                key = source['id'] + '/' + opid
                row = self._row(source, opid, 'user', request['raw_text'],
                    self._stamp(operation.get('created_at'), modified), operation_id=opid, turn_id=key,
                    input_attachments=request.get('input_attachments', []),
                    native_message_id=operation.get('native_message_id'),
                    source_sha256=operation['request_sha256'], status=operation['status'], session_ref=reference)
                frozen_context = operation.get('conversation_context')
                if isinstance(frozen_context, dict) and frozen_context.get('context_summary') is not None:
                    if (digest(frozen_context) == operation.get('conversation_context_sha256')
                            and frozen_context.get('original_request_sha256') == operation['request_sha256']):
                        row['context_summary'] = deepcopy(frozen_context['context_summary'])
                    else:
                        row['context_summary'] = {'unavailable': True}
                        budget.error(source['id'], 'ORIGINAL_CONTEXT_DECISION_UNAVAILABLE')
                if request.get('file_input'):
                    frozen = operation.get('conversation_context')
                    attached = frozen.get('file_input') if isinstance(frozen, dict) else None
                    if (isinstance(attached, dict) and digest(frozen) == operation.get('conversation_context_sha256')
                            and frozen.get('original_request_sha256') == operation['request_sha256']
                            and isinstance(attached.get('filename'), str)):
                        row['input_file'] = {'filename': attached['filename'], 'verified': True}
                        if (operation['kind'] == 'MESSAGE' and isinstance(request.get('conversation_ref'), dict)
                                and isinstance(attached.get('file_source_ref'), dict)
                                and isinstance(attached.get('link_ref'), dict)):
                            row['input_file']['openable'] = True
                    else:
                        row['input_file'] = {'filename': '', 'verified': False}
                rows.append(row)
                if operation.get('native_message_id'):
                    users[operation['native_message_id']] = row
                    user_operations[operation['native_message_id']] = operation
            except (Conflict, NotFound, ValueError, TypeError, KeyError, OSError):
                budget.error(source['id'], 'ORIGINAL_USER_OPERATION_UNAVAILABLE')
        messages = state.get('messages', [])
        if not isinstance(messages, list) or len(messages) > ORIGINAL_WINDOW:
            raise Conflict('Original saved message window is invalid')
        counts = Counter(message.get('info', {}).get('id') for message in messages)
        known_users = set(users) | {message.get('info', {}).get('id') for message in messages
            if message.get('info', {}).get('role') == 'user'
            and message.get('info', {}).get('sessionID') == state.get('original_session_id')}
        for index, message in enumerate(messages):
            info = message.get('info', {})
            if info.get('sessionID') != state.get('original_session_id') or info.get('role') not in {'user', 'assistant'}:
                continue
            mid, role = info['id'], info['role']
            if not isinstance(mid, str) or counts[mid] != 1:
                budget.error(source['id'], 'ORIGINAL_MESSAGE_ID_DUPLICATED_OR_INVALID')
                continue
            if role == 'user' and mid in users:
                continue
            if role == 'assistant' and info.get('parentID') not in known_users:
                budget.error(source['id'], 'ORIGINAL_RESPONSE_PARENT_UNAVAILABLE')
                continue
            parts = [part for part in message.get('parts', []) if part.get('type') == 'text' and not part.get('synthetic')]
            if any(not isinstance(part.get('text'), str) or part.get('messageID', mid) != mid
                    or part.get('sessionID', info['sessionID']) != info['sessionID'] for part in parts):
                budget.error(source['id'], 'ORIGINAL_MESSAGE_PART_SCOPE_DIFFERS')
                continue
            text = '\n\n'.join(part['text'] for part in parts)
            rejection = None
            if not text and role == 'assistant' and info.get('parentID') in user_operations:
                rejection = original_auth_rejection(user_operations[info['parentID']], messages)
                if rejection and mid == rejection['native_assistant_ids'][-1]:
                    text = AUTH_REJECTION_TEXT
                else:
                    rejection = None
            if not text:
                continue
            parent = users.get(info.get('parentID'))
            turn = parent['turn_id'] if parent else source['id'] + '/' + (info.get('parentID') or mid)
            rows.append(self._row(source, mid, role, text,
                self._stamp(info.get('time', {}).get('created'), stamp), ordinal=index + 1,
                native_message_id=mid, session_ref=reference, generation=state.get('generation'),
                operation_id=parent.get('operation_id') if parent else None, turn_id=turn,
                source_sha256=digest(message), status='FAILED' if rejection else 'STORED_MESSAGE',
                **({'failure_code': rejection['code']} if rejection else {})))
        if state.get('messages_limit') == ORIGINAL_WINDOW and len(messages) == ORIGINAL_WINDOW:
            budget.error(source['id'], 'ORIGINAL_SAVED_MESSAGE_WINDOW_MAY_BE_PARTIAL')
        return rows

    def _discovery_rows(self, source, budget):
        identity, original = source['identity'], source['original_source']
        pid, sid = identity['project_id'], identity['session_id']
        with self.app.state.store.connect() as connection:
            connection.execute('BEGIN')
            head = connection.execute('SELECT current_revision FROM discovery_sessions WHERE id=? AND project_id=?', (sid, pid)).fetchone()
            seed = connection.execute('SELECT sha256 FROM discovery_versions WHERE session_id=? AND revision=?',
                (sid, original['object_ref']['revision'])).fetchone()
            if head is None or seed is None or seed['sha256'] != original['object_ref']['sha256']:
                raise Conflict('Discovery origin revision differs')
            revision = head['current_revision']
            saved = connection.execute('SELECT sha256,created_at FROM discovery_versions WHERE session_id=? AND revision=?', (sid, revision)).fetchone()
            payload = budget.sql_json(connection, 'discovery_versions', 'payload', 'session_id=? AND revision=?', (sid, revision))
            if digest(payload) != saved['sha256'] or (payload['id'], payload['project_id'], payload['revision']) != (sid, pid, revision):
                raise Conflict('Discovery saved history integrity differs')
            reference = {'id': sid, 'revision': revision, 'sha256': saved['sha256']}
            rows = []
            for index, item in enumerate(payload.get('history', [])):
                kind = item.get('kind')
                if kind not in {'USER_STATEMENT', 'USER_MESSAGE', 'USER_FEEDBACK'}:
                    continue
                text = item['text']
                if kind == 'USER_FEEDBACK':
                    feedback = _json(text)
                    text = feedback['comment']  # Never display the internal feedback envelope as user prose.
                rows.append(self._row(source, 'user-' + str(index), 'user', text,
                    self._stamp(item.get('created_at'), 0), ordinal=index,
                    object_ref=reference, source_sha256=digest(item), turn_id=source['id'] + '/user-' + str(index),
                    status='USER_STATEMENT', user_input_kind=kind))
            answers = connection.execute('SELECT a.id,a.sha256,a.session_revision,a.question_id,v.sha256 AS revision_sha256 '
                'FROM discovery_answers a JOIN discovery_versions v ON v.session_id=a.session_id AND v.revision=a.session_revision '
                'WHERE a.session_id=? AND a.session_revision<=? ORDER BY a.rowid DESC LIMIT ?',
                (sid, revision, READ_LIMIT + 1)).fetchall()
            if len(answers) > READ_LIMIT:
                budget.error(source['id'], 'DISCOVERY_ANSWER_WINDOW_TRUNCATED')
            for saved_answer in answers[:READ_LIMIT]:
                answer = budget.sql_json(connection, 'discovery_answers', 'payload', 'id=? AND session_id=?', (saved_answer['id'], sid), maximum=128 * 1024)
                if (digest(answer) != saved_answer['sha256'] or answer['session_id'] != sid or answer['id'] != saved_answer['id']
                        or answer['session_revision'] != saved_answer['session_revision'] or answer['question_id'] != saved_answer['question_id']):
                    raise Conflict('Discovery answer integrity differs')
                rows.append(self._row(source, answer['id'], 'user', answer['text'], self._stamp(answer.get('created_at'), 0),
                    source_sha256=saved_answer['sha256'], object_ref={'id': sid, 'revision': saved_answer['session_revision'],
                        'sha256': saved_answer['revision_sha256']}, question_id=answer['question_id'],
                    turn_id=source['id'] + '/' + answer['id'], status='USER_ANSWER_NOT_AUTOMATIC_CONFIRMATION'))
            versions = connection.execute('SELECT revision,sha256,created_at FROM discovery_versions WHERE session_id=? '
                'ORDER BY revision DESC LIMIT ?', (sid, ORIGINAL_WINDOW + 1)).fetchall()
            if len(versions) > ORIGINAL_WINDOW:
                budget.error(source['id'], 'DISCOVERY_REVIEW_WINDOW_TRUNCATED')
            for version in versions[:ORIGINAL_WINDOW]:
                try:
                    previous = budget.sql_json(connection, 'discovery_versions', 'payload', 'session_id=? AND revision=?', (sid, version['revision']))
                    if (digest(previous) != version['sha256'] or
                            (previous['id'], previous['project_id'], previous['revision']) != (sid, pid, version['revision'])):
                        raise Conflict('Discovery historical review scope differs')
                    row = self._discovery_review(source, previous, version, rows)
                    if row is not None:
                        rows.append(row)
                except ReadBudgetLimit:
                    budget.error(source['id'], 'DISCOVERY_REVIEW_READ_BUDGET_REACHED')
                    break
                except (Conflict, ValueError, TypeError, KeyError):
                    budget.error(source['id'], 'DISCOVERY_HISTORICAL_REVIEW_UNAVAILABLE')
            return rows

    def _discovery_review(self, source, payload, saved, rows):
        candidate = payload.get('candidate', {})
        summary = candidate.get('summary')
        if not isinstance(summary, str) or not summary:
            return None
        stamp = self._stamp(saved['created_at'], 0)
        parents = [row for row in rows if row['role'] == 'user' and row['observed_at'] <= stamp]
        parent = max(parents, key=lambda row: (row['observed_at'], row['_ordinal'])) if parents else None
        reference = {'id': payload['id'], 'revision': payload['revision'], 'sha256': saved['sha256']}
        return self._row(source, 'review-' + str(payload['revision']), 'assistant', summary, stamp,
                    ordinal=payload['revision'], object_ref=reference, source_sha256=saved['sha256'],
                    turn_id=parent['turn_id'] if parent else source['id'] + '/review-' + str(payload['revision']),
                    status=payload.get('status'), discovery_review={
                        'claims': [{**{k: claim[k] for k in ('id', 'category', 'origin')}, 'text': claim['text'][:2000],
                            'text_truncated': len(claim['text']) > 2000} for claim in candidate.get('claims', [])[:12]],
                        'questions': [{**{k: question[k] for k in ('id', 'blocking')}, 'text': question['text'][:1000],
                            'text_truncated': len(question['text']) > 1000} for question in candidate.get('questions', [])[:12]],
                        'claims_total': len(candidate.get('claims', [])), 'questions_total': len(candidate.get('questions', [])),
                        'source_role': 'DISCOVERY_DOCUMENT_REVISION_NOT_A_CHAT_MODEL_REPLY',
                        'recorded_execution_mode': payload.get('execution', {}).get('mode', 'UNKNOWN'),
                        'coverage': 'BOUNDED_REVIEW_EXCERPT_FULL_SOURCE_AVAILABLE_BY_OBJECT_REF',
                        'semantic_verification': 'HUMAN_REVIEW_REQUIRED', 'execution_authorized': False})

    def collect(self, scope, *, before=None, exclude=None, exclude_original=None):
        budget = ReadBudget()
        budget.stage_limit = BYTE_LIMIT - 4 * 1024 * 1024
        rows, sources = [], scope['sources']
        if before is None:
            for source in sources:
                kind = source['identity']['kind']
                if kind == 'native':
                    continue
                try:
                    rows.extend(self._original_rows(source, budget, exclude_original) if kind == 'original' else self._discovery_rows(source, budget))
                except (Conflict, NotFound, ValueError, TypeError, KeyError, AttributeError, OSError):
                    budget.error(source['id'], 'STORED_SOURCE_UNAVAILABLE')
        budget.stage_limit = BYTE_LIMIT
        unused = self.threads is not None and scope.get('conversation_ref') and self.threads.unused_creation(scope['conversation_ref']['id'])
        native, next_window = ([], None) if unused else self._native_rows(sources, budget, before=before, exclude=exclude)
        rows.extend(native)
        rows.sort(key=lambda row: (row['observed_at'], row['_ordinal'], row['id']), reverse=True)
        basis = digest({'ref': scope.get('conversation_ref'), 'before': before, 'sources': budget.basis,
                        'projected_rows_sha256': digest(rows)})
        errors = [{'source': source, 'code': code, 'count': count} for (source, code), count in sorted(budget.errors.items())]
        coverage = {'consistency': 'STORED_READONLY_OBSERVATIONS_NOT_A_RUNTIME_SNAPSHOT',
            'order_basis': 'ADMISSION_TIME_ELSE_OBSERVED_MTIME_OR_SAVED_NATIVE_MESSAGE_TIME',
            'source_bytes_read': budget.bytes, 'source_byte_limit': BYTE_LIMIT, 'source_reads': budget.reads,
            'source_read_limit': READ_LIMIT, 'native_candidate_limit': NATIVE_WINDOW,
            'original_message_limit': ORIGINAL_WINDOW, 'native_window_has_more': next_window is not None,
            'source_partial': bool(errors), 'errors': errors, 'model_calls': 0, 'owner_connections_started': 0}
        return rows, basis, next_window, coverage

    def read(self, ident, *, limit=50, cursor=None):
        if self.threads is None:
            raise Conflict('Conversation history routing is unavailable')
        scope = self.threads.view(ident)
        before, offset, expected = None, 0, None
        if cursor:
            try:
                token = _json(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                if (set(token) != {'ref', 'before', 'offset', 'basis'} or token['ref'] != scope['conversation_ref']
                        or type(token['offset']) is not int or not 0 <= token['offset'] <= 10000
                        or token['basis'] is not None and not re.fullmatch(r'[a-f0-9]{64}', token['basis'])):
                    raise ValueError()
                before = token['before']
                if before is not None and (not isinstance(before, list) or len(before) != 2
                        or type(before[0]) is not int or not isinstance(before[1], str) or not ID.fullmatch(before[1])):
                    raise ValueError()
                offset, expected = token['offset'], token['basis']
            except (ValueError, TypeError, KeyError):
                raise Conflict('Conversation history cursor differs; refresh the current discussion') from None
        rows, basis, next_window, coverage = self.collect(scope, before=before)
        if expected is not None and expected != basis:
            raise Conflict('Stored conversation history changed; refresh without resending')
        selected, size = [], 0
        for row in rows[offset:offset + limit]:
            projected = {k: v for k, v in row.items() if k != '_ordinal'}
            length = len(canonical(projected).encode())
            if size + length > 512 * 1024:
                break
            selected.append(projected); size += length
        token = None
        if offset + len(selected) < len(rows):
            if not selected:
                raise Conflict('One history message exceeds the display budget; use its original source')
            token = {'ref': scope['conversation_ref'], 'before': before, 'offset': offset + len(selected), 'basis': basis}
        elif next_window is not None:
            token = {'ref': scope['conversation_ref'], 'before': next_window, 'offset': 0, 'basis': None}
        return {'version': 1, 'conversation_ref': scope['conversation_ref'], 'project_id': scope['project_id'],
            'messages': list(reversed(selected)), 'history_basis': basis,
            'next_cursor': base64.urlsafe_b64encode(canonical(token).encode()).rstrip(b'=').decode() if token else None,
            'coverage': {**coverage, 'has_more': token is not None, 'returned': len(selected),
                'message_byte_limit': 512 * 1024, 'message_bytes': size,
                'complete': token is None and not coverage['source_partial']},
            'execution_authorized': False, 'automatic_input_authorized': False, 'file_write_authorized': False}

    def for_model(self, record):
        from apps.api.native_messages import NativeQuestionInput, question_project
        body = record['request']
        NativeQuestionInput.model_validate(body)
        if digest(body) != record['request_sha256']:
            raise Conflict('Current conversation input integrity differs')
        scope = record.get('conversation_scope')
        if scope is not None and digest(scope) != record.get('conversation_scope_sha256'):
            raise Conflict('Frozen conversation history scope changed')
        if scope is None:
            identity = {'kind': 'native', 'project_id': question_project(body), 'conversation_id': body.get('conversation_id')}
            scope = {'sources': [{'id': 'native:' + digest([identity['project_id'], identity['conversation_id']]),
                                  'identity': identity, 'original_source': None}]}
        return self.for_scope(scope, project_id=question_project(body), conversation_id=body.get('conversation_id'),
            exclude_native=body['operation_id'])

    def for_scope(self, scope, *, project_id=None, conversation_id=None, exclude_native=None, exclude_original=None):
        """Same bounded view for public SDK messages; no synthetic native admission."""
        rows, _, next_window, coverage = self.collect(scope, exclude=exclude_native, exclude_original=exclude_original)
        turns = {}
        for row in reversed(rows):
            key = row['turn_id']
            turn = turns.setdefault(key, {'operation_id': row.get('operation_id'), 'source_kind': row['source']['kind'],
                'source': row['source'], 'user_text': '', 'answer': '', 'context_ref': row.get('context_ref'),
                'status': row.get('status'), 'source_sha256': row['source_sha256'],
                'scope': 'HISTORICAL_DATA_NO_NATIVE_AUTHORITY'})
            field = 'user_text' if row['role'] == 'user' else 'answer'
            turn[field] += ('\n\n' if turn[field] else '') + row['text']
            if row['role'] == 'user':
                turn.setdefault('user_source_sha256s', []).append(row['source_sha256'])
            if row.get('discovery_review'):
                turn['discovery_review'] = row['discovery_review']
            turns[key] = turns.pop(key)
        messages = list(turns.values())[-6:]
        while len(messages) > 1 and len(canonical(messages)) > 24000:
            messages.pop(0)
        if messages and len(canonical(messages)) > 24000:
            last = messages[0]
            last.update(user_text=last['user_text'][:8000], answer=last['answer'][:8000], text_truncated=True)
            last.pop('discovery_review', None)
        return {'messages': messages, 'coverage': 'LAST_SIX_WITH_24000_CHARACTER_LIMIT',
            'conversation_id': conversation_id, 'project_id': project_id,
            'candidate_window_limit': NATIVE_WINDOW, 'source_coverage': coverage,
            **({'conversation_ref': scope['conversation_ref'], 'scope_sha256': digest(scope)} if scope.get('conversation_ref') else {}),
            'automatic_input_authorized': False, 'history_not_current_page_content': True,
            'file_write_authorized': False, 'older_native_window_available': next_window is not None}


def register_conversation_history(app, threads):
    owner = ConversationHistory(app, threads=threads)
    app.state.conversation_history = owner

    class MessageSourceInput(RequestModel):
        message_id: Hash
        expected_sha256: Hash
        cursor: str | None = Field(default=None, max_length=2048)
        offset: int = Field(default=0, strict=True, ge=0, le=10000000)
        length: int = Field(default=4000, strict=True, ge=1, le=16000)

    class EventScanInput(RequestModel):
        operation_id: Id
        conversation_id: str = Field(pattern=r'^(native|original|discovery):[a-f0-9]{64}$')
        source_alias_id: str = Field(pattern=r'^(native|original|discovery):[a-f0-9]{64}$')
        cursor: str | None = Field(default=None, max_length=2048)

    @app.post('/api/context/history/scan-events')
    def scan_events(body: EventScanInput):
        return app.state.context_graph._mutate('message_locations_scan', body,
            lambda db: owner.scan_event_locations(db, body.conversation_id, body.source_alias_id, cursor=body.cursor))

    @app.post('/api/context/conversations/{conversation_id}/message-source')
    def message_source(conversation_id: str, body: MessageSourceInput):
        return owner.prepare_message_source(conversation_id, body.message_id,
            cursor=body.cursor, offset=body.offset, length=body.length, expected_sha256=body.expected_sha256)

    @app.get('/api/context/conversations/{conversation_id}/event-excerpts')
    def event_excerpts(conversation_id: str,
            source_alias_id: str = Query(pattern=r'^(native|original|discovery):[a-f0-9]{64}$'),
            cursor: str | None = Query(None, max_length=2048)):
        return owner.event_excerpts(conversation_id, source_alias_id, cursor=cursor)

    @app.post('/api/context/history/preview-source')
    def preview_source(body: SourceRef):
        if body.kind != 'CONVERSATION_MESSAGE' or body.source_anchor is None:
            raise Conflict('Select an exact original message anchor for this preview')
        value = owner.read_source_anchor(body)
        text = value['text'][body.offset:body.offset+body.length]
        if not text:
            raise Conflict('Selected original message range is empty')
        return {'source_ref': body.model_dump(), 'title': value['title'], 'text': text,
            'authority': 'ORIGINAL_MESSAGE_DATA_NO_OWNER_OR_FILE_GRANT', 'model_calls': 0}

    @app.get('/api/conversations/{conversation_id}/history')
    def history(conversation_id: str, limit: int = Query(default=50, ge=1, le=100),
                cursor: str | None = Query(default=None, max_length=2048)):
        return owner.read(conversation_id, limit=limit, cursor=cursor)
