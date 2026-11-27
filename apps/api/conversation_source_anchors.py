"""Exact history references into original operations, revisions and SDK events.

This module stores no text and starts no owner, model, process or native action.
"""
import base64
import hashlib
import json

from packages.domain.conversation_source_models import ConversationSourceAnchor
from packages.domain.context_read_budget import context_read_scope, current_context_read_budget
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest


def text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class ConversationSourceAnchors:
    def _source_budget(self):
        from apps.api.conversation_history import ReadBudget
        allowance = current_context_read_budget()
        if allowance is None:
            return ReadBudget()
        key = ('exact-original-history', id(self))
        if key not in allowance.cache:
            allowance.cache[key] = ReadBudget()
        return allowance.cache[key]

    def _anchor_alias(self, cid, alias_id):
        # Navigation deletion does not erase the original data or retarget an
        # admitted quote. New chat admissions still use threads.view; explicit
        # source forgetting/revocation remains enforced by the context graph.
        with self.app.state.store.connect() as db:
            self.threads._entity(db, cid)
            row = db.execute('SELECT * FROM conversation_source_aliases WHERE conversation_id=? AND alias_id=?',
                (cid, alias_id)).fetchone()
        if row is None:
            raise Conflict('Original message source is no longer in this discussion')
        identity, original = self.threads.checked_alias(row)
        return {'id': row['alias_id'], 'identity': identity, 'original_source': original}

    def _source_project(self, original_pid):
        # Keep the actual execution PID in the anchor. Only the existing
        # global-conversation data namespace is normalized here; file refs are not.
        with self.app.state.store.connect() as db:
            return None if self.threads._execution_project_matches(db, None, original_pid) else original_pid

    def _original_manifest(self, source, budget):
        identity, original = source['identity'], source['original_source']
        if identity['kind'] != 'original' or original is None:
            raise Conflict('An exact original session source is required')
        owner = self.app.state.agent_sessions
        manifest = budget.file(owner._path(identity['project_id'], identity['session_id'], 'manifest.json'))
        ref = {'id': identity['session_id'], 'revision': 1, 'sha256': digest(manifest)}
        if (ref != original['session_ref'] or manifest.get('project_id') != identity['project_id']
                or manifest.get('id') != identity['session_id'] or manifest.get('native_message_admission')
                or manifest.get('native_review_admission')):
            raise Conflict('Original message source manifest differs')
        return owner, ref

    @staticmethod
    def _event_payload(owner, pid, sid, ref, budget):
        event = budget.file(owner._path(pid, sid, 'events', f"{ref.seq:010d}.json"))
        if (event.get('seq') != ref.seq or digest(event) != ref.sha256 or event.get('type') != 'ORIGINAL_EVENT'
                or event.get('original_native_replay') is not False):
            raise Conflict('Original message event identity differs')
        return event['payload']['native_payload']

    def _message_locations(self, source, native_sid, mid, *, part_id=None):
        identity = source['identity']
        with self.app.state.store.connect() as db:
            rows = db.execute('SELECT * FROM original_message_locations WHERE project_id=? AND session_id=? '
                'AND native_session_id=? AND message_id=? AND '
                '((? IS NULL AND kind IN ("INFO","TEXT","DIRTY","MESSAGE_REMOVED")) OR slot_id="@info" OR slot_id=?) '
                'ORDER BY event_seq LIMIT 66',
                (identity['project_id'], identity['session_id'], native_sid, mid,part_id,part_id)).fetchall()
        if len(rows) > 65:
            raise Conflict('Original message part-location budget exceeded')
        return [dict(row) for row in rows]

    def _read_original_message(self, source, anchor, budget):
        if anchor.role != 'assistant':
            raise Conflict('Use the original user operation, not a raw decorated SDK input')
        owner, ref = self._original_manifest(source, budget)
        if anchor.session_ref.model_dump() != ref:
            raise Conflict('Original message session reference differs')
        if anchor.source_sha256 != digest({'info': anchor.info_event.model_dump(), 'parts': [part.model_dump() for part in anchor.part_events]}):
            raise Conflict('Original message event-set fingerprint differs')
        pid, sid = source['identity']['project_id'], ref['id']
        if anchor.kind == 'ORIGINAL_MESSAGE':
            with self.app.state.store.connect() as db:
                if db.execute('SELECT 1 FROM original_message_location_gaps WHERE project_id=? AND session_id=?',
                        (pid,sid)).fetchone():
                    raise Conflict('Original message-location coverage has a gap; use verified event parts')
        locations = self._message_locations(source, anchor.original_session_id, anchor.native_message_id,
            part_id=anchor.part_events[0].part_id if anchor.kind=='ORIGINAL_PART' else None)
        info_slot = next((row for row in locations if row['slot_id'] == '@info'), None)
        if info_slot is None or info_slot['kind'] != 'INFO':
            raise Conflict('Original message info is missing or removed')
        info_event = self._event_payload(owner, pid, sid, anchor.info_event, budget)
        info = info_event.get('properties', {}).get('info', {})
        if (info_event.get('type') != 'message.updated' or info.get('sessionID') != anchor.original_session_id
                or info.get('id') != anchor.native_message_id or info.get('role') != anchor.role):
            raise Conflict('Original message info belongs to another session, message or role')
        # Cost/timestamp updates do not replace a quote. Removal, role or parent
        # changes do; load the current info only when its immutable event differs.
        if info_slot['event_seq'] != anchor.info_event.seq or info_slot['event_sha256'] != anchor.info_event.sha256:
            from packages.domain.conversation_source_models import MessageEventRef
            current = self._event_payload(owner, pid, sid, MessageEventRef(seq=info_slot['event_seq'], sha256=info_slot['event_sha256']), budget)
            current_info = current.get('properties', {}).get('info', {})
            if (digest(current_info) != info_slot['value_sha256']
                    or any(current_info.get(key) != info.get(key) for key in ('id','sessionID','role','parentID'))):
                raise Conflict('Original message identity or parent changed')
        selected = {part.part_id for part in anchor.part_events}
        current_text = {row['slot_id'] for row in locations if row['kind'] in {'TEXT','DIRTY'}}
        if anchor.kind == 'ORIGINAL_MESSAGE' and current_text != selected:
            raise Conflict('Original message text-part membership changed')
        texts = []
        for part in anchor.part_events:
            current = next((row for row in locations if row['slot_id'] == part.part_id), None)
            if current is None or current['kind'] != 'TEXT':
                raise Conflict('Original text part is removed or its latest update is incomplete')
            event = self._event_payload(owner, pid, sid, part, budget)
            value = event.get('properties', {}).get('part', {})
            if (event.get('type') != 'message.part.updated' or value.get('type') != 'text'
                    or value.get('sessionID') != anchor.original_session_id or value.get('messageID') != anchor.native_message_id
                    or value.get('id') != part.part_id or not isinstance(value.get('text'), str)
                    or digest(value) != current['value_sha256']):
                raise Conflict('Original text part changed or belongs to another message')
            texts.append(value['text'])
        key = anchor.native_message_id + ('/part/'+anchor.part_events[0].part_id if anchor.kind == 'ORIGINAL_PART' else '')
        if anchor.message_key != key:
            raise Conflict('Original message key differs')
        text = '\n\n'.join(texts)
        if len(text) > 1000000:
            raise Conflict('Original quote exceeds the bounded message size')
        return text

    def _read_native_anchor(self, source, anchor, budget):
        from apps.api.native_messages import NativeQuestionInput, question_project
        record = budget.file(self.native.path(anchor.operation_id), native=True)
        body = NativeQuestionInput.model_validate(record['request']).request_dict()
        identity = source['identity']
        if (body != record['request'] or digest(body) != record['request_sha256']
                or body['operation_id'] != anchor.operation_id or question_project(body) != identity['project_id']
                or body.get('conversation_id') != identity['conversation_id']):
            raise Conflict('Native quote belongs to another original input')
        if anchor.kind == 'NATIVE_GUIDANCE':
            owner = record.get('owner') or {}
            if (owner.get('session_ref') != anchor.session_ref.model_dump()
                    or record.get('native_operation_id') != anchor.original_operation_id
                    or owner.get('generation') != anchor.generation):
                raise Conflict('Original guidance parent owner differs')
            service, pid, sid = self.app.state.agent_sessions, owner['project_id'], anchor.session_ref.id
            manifest = budget.file(service._path(pid, sid, 'manifest.json'))
            if digest(manifest) != anchor.session_ref.sha256 or manifest.get('project_id') != pid:
                raise Conflict('Original guidance manifest differs')
            guide = budget.file(service._path(pid, sid, 'operations', digest(anchor.guidance_operation_id)+'.json'))
            request = guide['request']
            if (guide.get('kind') != 'GUIDANCE' or guide.get('id') != anchor.guidance_operation_id
                    or guide.get('session_ref') != anchor.session_ref.model_dump() or guide.get('generation') != anchor.generation
                    or guide.get('original_session_id') != anchor.original_session_id
                    or guide.get('original_active_operation_id') != anchor.original_operation_id
                    or request.get('expected_active_operation_id') != anchor.original_operation_id
                    or digest(request) != guide.get('request_sha256') or guide.get('request_sha256') != anchor.source_sha256
                    or anchor.message_key != anchor.operation_id+'/guide/'+anchor.guidance_operation_id):
                raise Conflict('Original guidance scope or request differs')
            return request['raw_text']
        if anchor.source_sha256 != record['request_sha256'] or anchor.message_key != anchor.operation_id:
            raise Conflict('Native quote request differs')
        if anchor.kind == 'NATIVE_USER':
            return body['raw_text']
        answer, _, _ = self._native_answer(record, budget)
        if not answer:
            raise Conflict('Original committed native answer is unavailable')
        return answer

    def _discovery_payload(self, source, ref, budget):
        identity = source['identity']
        if ref.id != identity['session_id']:
            raise Conflict('Discovery quote belongs to another session')
        with self.app.state.store.connect() as db:
            row = db.execute('SELECT v.sha256 FROM discovery_versions v JOIN discovery_sessions s ON s.id=v.session_id '
                'WHERE v.session_id=? AND v.revision=? AND s.project_id=?', (ref.id, ref.revision, identity['project_id'])).fetchone()
            payload = budget.sql_json(db, 'discovery_versions', 'payload', 'session_id=? AND revision=?', (ref.id, ref.revision))
        if row is None or row['sha256'] != ref.sha256 or digest(payload) != ref.sha256:
            raise Conflict('Original discovery revision differs')
        return payload

    def _read_anchor_text(self, source, anchor, budget):
        if source['identity']['project_id'] != anchor.original_project_id:
            raise Conflict('Original source project identity differs')
        if anchor.kind.startswith('NATIVE_'):
            if source['identity']['kind'] != 'native':
                raise Conflict('A native quote needs its original native source')
            return self._read_native_anchor(source, anchor, budget)
        if anchor.kind in {'ORIGINAL_MESSAGE','ORIGINAL_PART'}:
            return self._read_original_message(source, anchor, budget)
        if anchor.kind == 'ORIGINAL_USER':
            owner, ref = self._original_manifest(source, budget)
            if ref != anchor.session_ref.model_dump() or anchor.message_key != anchor.operation_id:
                raise Conflict('Original user quote session or operation differs')
            pid, sid = source['identity']['project_id'], ref['id']
            operation = budget.file(owner._path(pid, sid, 'operations', digest(anchor.operation_id)+'.json'))
            request = operation['request']
            if (operation.get('id') != anchor.operation_id or operation.get('kind') not in {'MESSAGE','GUIDANCE'}
                    or operation.get('session_ref') != ref or request.get('session_ref') != ref
                    or digest(request) != operation.get('request_sha256') or anchor.source_sha256 != operation['request_sha256']):
                raise Conflict('Original user operation request differs')
            return request['raw_text']
        if source['identity']['kind'] != 'discovery':
            raise Conflict('A discovery quote needs its exact original source')
        payload = self._discovery_payload(source, anchor.object_ref, budget)
        if anchor.kind == 'DISCOVERY_REVIEW':
            if anchor.message_key != 'review-'+str(anchor.object_ref.revision) or anchor.source_sha256 != anchor.object_ref.sha256:
                raise Conflict('Discovery review revision key differs')
            return payload['candidate']['summary']
        if anchor.kind == 'DISCOVERY_STATEMENT':
            index = anchor.history_index
            if anchor.message_key != 'user-'+str(index):
                raise Conflict('Discovery history key differs')
            item = payload.get('history', [])[index]
            with self.app.state.store.connect() as db:
                head = db.execute('SELECT current_revision FROM discovery_sessions WHERE id=?', (anchor.object_ref.id,)).fetchone()[0]
                if head != anchor.object_ref.revision:
                    latest = budget.sql_json(db, 'discovery_versions', 'payload', 'session_id=? AND revision=?', (anchor.object_ref.id, head))
                    if digest(latest.get('history', [])[index]) != anchor.source_sha256:
                        raise Conflict('Original discovery history item changed')
            if digest(item) != anchor.source_sha256 or item.get('kind') not in {'USER_STATEMENT','USER_MESSAGE','USER_FEEDBACK'}:
                raise Conflict('Original discovery statement differs')
            return json.loads(item['text'])['comment'] if item['kind'] == 'USER_FEEDBACK' else item['text']
        with self.app.state.store.connect() as db:
            row = db.execute('SELECT sha256,session_revision FROM discovery_answers WHERE id=? AND session_id=?',
                (anchor.answer_id, anchor.object_ref.id)).fetchone()
            answer = budget.sql_json(db, 'discovery_answers', 'payload', 'id=? AND session_id=?', (anchor.answer_id, anchor.object_ref.id), maximum=128*1024)
        if (row is None or row['sha256'] != anchor.source_sha256 or row['session_revision'] != anchor.object_ref.revision
                or digest(answer) != anchor.source_sha256 or anchor.message_key != anchor.answer_id):
            raise Conflict('Original discovery answer differs')
        return answer['text']

    @context_read_scope()
    def read_source_anchor(self, reference):
        anchor = reference.source_anchor
        source = self._anchor_alias(reference.conversation_id, anchor.source_alias_id)
        if (self._source_project(anchor.original_project_id) != reference.project_id
                or digest([source['id'], anchor.message_key, anchor.role]) != reference.message_id):
            raise Conflict('Quoted source belongs to another original project or message')
        try:
            text = self._read_anchor_text(source, anchor, self._source_budget())
        except (IndexError, KeyError, TypeError, ValueError):
            raise Conflict('Original message location is unavailable or malformed') from None
        if not isinstance(text, str) or text_sha(text) != reference.sha256:
            raise Conflict('Original message text changed or its exact source is incomplete')
        return {'text': text, 'title': anchor.role+': '+text[:100]}

    def _make_source_anchor(self, source, row, budget):
        identity, role = source['identity'], row['role']
        common = {'source_alias_id': source['id'], 'original_project_id': identity['project_id'],
            'role': role, 'source_sha256': row['source_sha256']}
        if identity['kind'] == 'native':
            if row.get('native_parent_operation_id'):
                parent = row['native_parent_operation_id']
                record = budget.file(self.native.path(parent), native=True)
                owner = record['owner']
                service = self.app.state.agent_sessions
                guide = budget.file(service._path(owner['project_id'], owner['session_ref']['id'], 'operations', digest(row['operation_id'])+'.json'))
                return ConversationSourceAnchor(**common, kind='NATIVE_GUIDANCE', operation_id=parent,
                    guidance_operation_id=row['operation_id'], original_operation_id=record['native_operation_id'],
                    session_ref=owner['session_ref'], generation=owner['generation'], original_session_id=guide['original_session_id'],
                    message_key=parent+'/guide/'+row['operation_id'])
            return ConversationSourceAnchor(**common, kind='NATIVE_USER' if role == 'user' else 'NATIVE_ASSISTANT',
                operation_id=row['operation_id'], message_key=row['operation_id'])
        if identity['kind'] == 'original':
            owner, ref = self._original_manifest(source, budget)
            if (role == 'user' and row.get('operation_id')
                    and digest([source['id'], row['operation_id'], role]) == row['id']):
                return ConversationSourceAnchor(**common, kind='ORIGINAL_USER', operation_id=row['operation_id'],
                    session_ref=ref, message_key=row['operation_id'])
            if role != 'assistant':
                raise Conflict('This raw SDK user message has no exact original user-operation source')
            state = budget.file(owner._path(identity['project_id'], identity['session_id'], 'state.json'))
            messages = [message for message in state.get('messages', []) if message.get('info', {}).get('id') == row.get('native_message_id')]
            if len(messages) != 1:
                raise Conflict('Exact original message is outside its current observation window; use its event excerpts')
            message = messages[0]
            mid, native_sid = message['info']['id'], message['info']['sessionID']
            locations = self._message_locations(source, native_sid, mid)
            info = next((item for item in locations if item['slot_id'] == '@info' and item['kind'] == 'INFO'), None)
            if info is None:
                raise Conflict('Original message event location was not recorded')
            texts = [part for part in message.get('parts', []) if part.get('type') == 'text']
            if not texts or len(texts) > 64 or any(not isinstance(part.get('text'), str) for part in texts):
                raise Conflict('Original text parts are incomplete or outside the quote budget')
            parts = []
            for part in texts:
                item = next((item for item in locations if item['slot_id'] == part['id'] and item['kind'] == 'TEXT'), None)
                if item is None or item['value_sha256'] != digest(part):
                    raise Conflict('Observed original text does not match its immutable event source')
                parts.append({'part_id': part['id'], 'seq': item['event_seq'], 'sha256': item['event_sha256']})
            evidence = {'info': {'seq': info['event_seq'], 'sha256': info['event_sha256']}, 'parts': parts}
            common['source_sha256'] = digest(evidence)
            return ConversationSourceAnchor(**common, kind='ORIGINAL_MESSAGE', session_ref=ref,
                original_session_id=native_sid, native_message_id=mid, message_key=mid,
                info_event=evidence['info'], part_events=parts)
        ref = row['object_ref']
        if row.get('discovery_review') is not None:
            return ConversationSourceAnchor(**common, kind='DISCOVERY_REVIEW', object_ref=ref,
                message_key='review-'+str(ref['revision']))
        if row.get('status') == 'USER_ANSWER_NOT_AUTOMATIC_CONFIRMATION':
            answer_id = row['turn_id'].rsplit('/', 1)[-1]
            return ConversationSourceAnchor(**common, kind='DISCOVERY_ANSWER', object_ref=ref,
                answer_id=answer_id, message_key=answer_id)
        key = row['turn_id'].rsplit('/', 1)[-1]
        if not key.startswith('user-') or not key[5:].isdecimal():
            raise Conflict('Exact original discovery history item is unavailable')
        return ConversationSourceAnchor(**common, kind='DISCOVERY_STATEMENT', object_ref=ref,
            history_index=int(key[5:]), message_key=key)

    @context_read_scope()
    def prepare_message_source(self, cid, message_id, *, cursor=None, offset=0, length=4000, expected_sha256=None):
        from packages.domain.context_graph import SourceRef
        page = self.read(cid, limit=100, cursor=cursor)
        row = next((item for item in page['messages'] if item['id'] == message_id), None)
        if row is None or row['text_truncated']:
            raise Conflict('Select an exact complete message from this original history window')
        if expected_sha256 is not None and row['text_sha256'] != expected_sha256:
            raise Conflict('The selected original message changed; refresh its source before quoting')
        source = self._anchor_alias(cid, row['source']['alias_id'])
        anchor = self._make_source_anchor(source, row, self._source_budget())
        ref = SourceRef(kind='CONVERSATION_MESSAGE', project_id=self._source_project(anchor.original_project_id),
            conversation_id=cid, message_id=message_id, sha256=row['text_sha256'], source_anchor=anchor,
            offset=offset, length=length)
        resolved = self.read_source_anchor(ref)
        text = resolved['text'][offset:offset+length]
        if not text:
            raise Conflict('Selected original message range is empty')
        return {'source_ref': ref.model_dump(), 'title': resolved['title'], 'text': text,
            'authority': 'ORIGINAL_MESSAGE_DATA_NO_OWNER_OR_FILE_GRANT', 'model_calls': 0}

    @context_read_scope()
    def event_excerpts(self, cid, alias_id, *, cursor=None, limit=20):
        """Browse exact original assistant text parts beyond the saved 200 window.

        Excerpts do not pretend to reconstruct an entire answer or a transcript.
        Stable rowid pagination excludes later inserted locations; changed parts
        are read at their current verified version and selections pin those bytes.
        """
        from packages.domain.context_graph import SourceRef
        from packages.domain.conversation_source_models import MessageEventRef
        if type(limit) is not int or not 1 <= limit <= 20:
            raise Conflict('Original event-excerpt page limit is invalid')
        source, budget = self._anchor_alias(cid, alias_id), self._source_budget()
        owner, ref = self._original_manifest(source, budget)
        pid, sid = source['identity']['project_id'], ref['id']
        state = budget.file(owner._path(pid, sid, 'state.json'))
        native = state.get('original_session_id')
        if not isinstance(native, str):
            return {'items': [], 'next_cursor': None, 'partial': True, 'reason': 'NO_ORIGINAL_SESSION', 'model_calls': 0}
        before, ceiling = None, None
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                if (set(token) != {'cid','alias','native','before','ceiling'} or token['cid'] != cid or token['alias'] != alias_id
                        or token['native'] != native or any(type(token[key]) is not int or not 0 < token[key] <= 2**63-1 for key in ('before','ceiling'))):
                    raise ValueError()
                before, ceiling = token['before'], token['ceiling']
            except (TypeError, ValueError, KeyError):
                raise Conflict('Original event excerpt cursor differs') from None
        with self.app.state.store.connect() as db:
            if ceiling is None:
                ceiling = db.execute('SELECT coalesce(max(rowid),0) FROM original_message_locations WHERE project_id=? AND session_id=? AND native_session_id=?',
                    (pid, sid, native)).fetchone()[0]
            rows = db.execute('SELECT rowid AS location_rowid,* FROM original_message_locations WHERE project_id=? AND session_id=? '
                'AND native_session_id=? AND slot_id!="@info" AND kind="TEXT" AND rowid<=? AND (? IS NULL OR rowid<?) '
                'ORDER BY rowid DESC LIMIT ?', (pid,sid,native,ceiling,before,before,limit+1)).fetchall()
            recorded_gap = db.execute('SELECT reason FROM original_message_location_gaps WHERE project_id=? AND session_id=?',
                (pid,sid)).fetchone()
        items, omitted = [], 0
        for row in rows[:limit]:
            try:
                locations = self._message_locations(source, native, row['message_id'], part_id=row['slot_id'])
                info = next((entry for entry in locations if entry['slot_id'] == '@info' and entry['kind'] == 'INFO'), None)
                if info is None:
                    raise Conflict('Original info location missing')
                info_ref = MessageEventRef(seq=info['event_seq'], sha256=info['event_sha256'])
                event = self._event_payload(owner, pid, sid, info_ref, budget)
                if event.get('properties', {}).get('info', {}).get('role') != 'assistant':
                    continue  # Raw SDK user inputs may contain host context envelopes.
                part = {'part_id': row['slot_id'], 'seq': row['event_seq'], 'sha256': row['event_sha256']}
                evidence = {'info': info_ref.model_dump(), 'parts': [part]}
                key = row['message_id']+'/part/'+row['slot_id']
                anchor = ConversationSourceAnchor(kind='ORIGINAL_PART', source_alias_id=alias_id,
                    original_project_id=pid, role='assistant', message_key=key, source_sha256=digest(evidence),
                    session_ref=ref, original_session_id=native, native_message_id=row['message_id'],
                    info_event=info_ref, part_events=[part])
                text = self._read_original_message(source, anchor, budget)
                source_ref = SourceRef(kind='CONVERSATION_MESSAGE', project_id=self._source_project(pid),
                    conversation_id=cid, message_id=digest([alias_id,key,'assistant']), sha256=text_sha(text),
                    source_anchor=anchor)
                items.append({'id': source_ref.message_id, 'role': 'assistant', 'text': text[:4000],
                    'text_truncated': len(text)>4000, 'text_original_length': len(text), 'source_ref': source_ref.model_dump(),
                    'source_role': 'ORIGINAL_EVENT_TEXT_PART_NOT_COMPLETE_ANSWER_OR_TRANSCRIPT'})
            except (Conflict, NotFound, OSError, ValueError, KeyError, TypeError):
                omitted += 1
        token = {'cid':cid,'alias':alias_id,'native':native,'before':rows[limit-1]['location_rowid'],'ceiling':ceiling} if len(rows)>limit else None
        return {'items': items, 'next_cursor': base64.urlsafe_b64encode(json.dumps(token,separators=(',',':')).encode()).decode().rstrip('=') if token else None,
            'partial': bool(omitted) or recorded_gap is not None, 'omitted': omitted,
            'index_gap': recorded_gap['reason'] if recorded_gap else None, 'model_calls': 0, 'execution_authorized': False,
            'coverage': 'INDEXED_ORIGINAL_ASSISTANT_TEXT_PARTS_NO_NEW_OWNER_OR_MESSAGE_GET'}

    def scan_event_locations(self, db, cid, alias_id, *, cursor=None):
        """Explicit bounded backfill of metadata for pre-index event journals.

        Caller uses the existing context operation journal and writer transaction.
        Raw original events stay unchanged; an unread budget-limited event is the
        next page's first event, never silently skipped.
        """
        from packages.agent_sessions.message_locations import index_message_event, record_message_gap
        from apps.api.conversation_history import ReadBudgetLimit
        source, budget = self._anchor_alias(cid, alias_id), self._source_budget()
        owner, reference = self._original_manifest(source, budget)
        pid, sid = source['identity']['project_id'], reference['id']
        state = budget.file(owner._path(pid, sid, 'state.json'))
        native, ceiling = state.get('original_session_id'), state.get('last_event_seq')
        if not isinstance(native, str) or type(ceiling) is not int or ceiling < 0:
            raise Conflict('Original event journal header is unavailable')
        before = ceiling
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                if (set(value) != {'cid','alias','ref','native','before','ceiling'} or value['cid'] != cid or value['alias'] != alias_id
                        or value['ref'] != reference or value['native'] != native
                        or any(type(value[key]) is not int or not 0 <= value[key] <= ceiling for key in ('before','ceiling'))
                        or value['before'] > value['ceiling']):
                    raise ValueError()
                before, ceiling = value['before'], value['ceiling']
            except (ValueError, TypeError, KeyError):
                raise Conflict('Original event-location scan cursor differs') from None
        scanned, missing, statuses = 0, 0, {}
        while before > 0 and scanned < 200:
            try:
                event = budget.file(owner._path(pid,sid,'events',f'{before:010d}.json'))
                if (event.get('seq') != before or event.get('original_native_replay') is not False
                        or not isinstance(event.get('payload'), dict)):
                    raise Conflict('Original event journal record differs')
                status = index_message_event(db, pid, sid, event)
                statuses[status] = statuses.get(status,0)+1
            except ReadBudgetLimit:
                statuses['READ_BUDGET_REACHED'] = 1
                break
            except (Conflict, NotFound, OSError, ValueError, KeyError, TypeError):
                missing += 1
                record_message_gap(db, pid, sid, before, 'MISSING_OR_INVALID_ORIGINAL_EVENT')
            scanned += 1
            before -= 1
        value = {'cid':cid,'alias':alias_id,'ref':reference,'native':native,'before':before,'ceiling':ceiling} if before else None
        return {'next_cursor':base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).decode().rstrip('=') if value else None,
            'events_scanned':scanned,'missing_events':missing,'statuses':statuses,'event_ceiling':ceiling,
            'model_calls':0,'raw_events_changed':False,'transcript_copied':False,'execution_authorized':False}
