"""Reviewed, revocable source fragments without changing original ownership."""
import json
from packages.domain.context_read_budget import context_read_scope
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest


class ContextSourceGrants:
    def _read_reference(self, db, reference, *, seen=None, blocked=(), budget=None, target_scope_id=None):
        from packages.domain.context_graph import text_hash
        if reference.kind not in {'NOTE', 'NOTE_FRAGMENT'}:
            return self.source_reader(reference)
        base = self._origin(db, reference.object_id)
        if base['kind'] != 'NOTE' or base['project_id'] != reference.project_id:
            raise Conflict('Reviewed note belongs to another original source')
        value = self._read(db, base['id'], seen=seen, blocked=blocked, budget=budget, target_scope_id=target_scope_id)
        if reference.kind == 'NOTE':
            return value
        if value['fingerprint'] != reference.sha256:
            raise Conflict('Reviewed note fingerprint changed')
        text = value['text'][reference.offset:reference.offset + reference.length]
        if not text:
            raise Conflict('Reviewed note range is empty')
        return {'text': text, 'title': value['title'], 'fingerprint': digest({
            'source': reference.model_dump(), 'text_sha256': text_hash(text)})}

    def _read_grant(self, db, scope_id, origin_id, cid):
        row = db.execute('SELECT * FROM context_read_grants WHERE scope_id=? AND origin_id=? '
                         'AND conversation_id IS ?', (scope_id, origin_id, cid)).fetchone()
        return dict(row) if row else None

    def _validate_read_grant(self, db, reference, *, origin_id=None):
        row = db.execute('SELECT * FROM context_read_grants WHERE id=?', (reference['id'],)).fetchone()
        if (row is None or row['revision'] != reference['revision'] or row['state'] != 'ACTIVE'
                or origin_id is not None and row['origin_id'] != origin_id):
            raise Conflict('A reviewed read-only source grant changed or was withdrawn')
        origin = self._origin(db, row['origin_id'])
        if origin['fingerprint'] != row['source_fingerprint']:
            raise Conflict('Read-only source no longer matches the reviewed fingerprint')
        return dict(row)

    def _validate_note_grants(self, db, origin_id, *, target_scope_id=None):
        origin = self._origin(db, origin_id)
        for row in db.execute('SELECT * FROM context_note_grants WHERE note_origin_id=?', (origin_id,)):
            grant = self._validate_read_grant(db, {'id': row['grant_id'], 'revision': row['grant_revision']},
                                            origin_id=row['dependency_origin_id'])
            anchor = self._scope(db, row['note_scope_id'])
            if (anchor['project_id'] != origin['project_id'] or grant['conversation_id'] is not None
                    or grant['scope_id'] not in {node['id'] for node in self._ancestry(db, anchor['id'])}):
                raise Conflict('The derived note no longer belongs to its reviewed source grant scope')
            if target_scope_id is not None:
                target = self._scope(db, target_scope_id)
                if (target['project_id'] == origin['project_id']
                        and grant['scope_id'] not in {node['id'] for node in self._ancestry(db, target_scope_id)}):
                    raise Conflict('Review a read-only grant before using a derived note outside its source scope')

    def _prepare_read_grant(self, db, request):
        from packages.domain.context_graph import SourceRef
        scope = self._scope(db, request.scope_id)
        self._conversation(scope['project_id'], request.conversation_id)
        source = request.source
        if source.project_id == scope['project_id']:
            raise Conflict('Use an ordinary source association within its original project')
        if source.kind == 'NOTE':
            origin = self._origin(db, source.object_id)
            source = SourceRef(kind='NOTE_FRAGMENT', project_id=source.project_id,
                object_id=source.object_id, sha256=origin['fingerprint'], offset=source.offset, length=source.length)
        value = self._read_reference(db, source)
        oid = digest(source.model_dump())
        registered = db.execute('SELECT forgotten FROM context_origins WHERE id=?', (oid,)).fetchone()
        if registered is not None and registered['forgotten']:
            raise Conflict('The original indexed source was forgotten; review it in its original project first')
        aid = digest([request.scope_id, oid, request.conversation_id])
        edge = db.execute('SELECT revision FROM context_associations WHERE id=?', (aid,)).fetchone()
        grant = self._read_grant(db, request.scope_id, oid, request.conversation_id)
        exclusions = self._file_exclusions(db, [row['id'] for row in self._ancestry(db, request.scope_id)],
            request.conversation_id, source.file_identity_sha256) if source.kind == 'FILE_SNAPSHOT' else []
        restore = sorted(({'id': row['id'], 'revision': row['revision']} for row in exclusions), key=lambda row: row['id'])
        basis = {'scope_id': scope['id'], 'scope_revision': scope['revision'],
            'conversation_id': request.conversation_id, 'source_ref': source.model_dump(),
            'fingerprint': value['fingerprint'], 'association_revision': edge['revision'] if edge else 0,
            'grant_revision': grant['revision'] if grant else 0, 'file_exclusions': restore}
        return {**value, 'source_ref': source.model_dump(), 'origin_id': oid,
            'association_id': aid, 'expected_revision': basis['association_revision'],
            'expected_grant_revision': basis['grant_revision'], 'restore_file_exclusions': restore,
            'review_sha256': digest(basis), 'source_project_id': source.project_id,
            'scope_id': scope['id'], 'conversation_id': request.conversation_id,
            'authority': 'READONLY_SOURCE_DATA_NO_EXECUTION_GRANT', 'model_calls': 0}

    @context_read_scope()
    def preview_read_grant(self, request):
        with self.store.connect() as db:
            return self._prepare_read_grant(db, request)

    def change_read_grant(self, request):
        from packages.domain.context_graph import stamp, MAX_ORIGINS, MAX_ASSOCIATIONS
        def apply(db):
            scope = self._scope(db, request.scope_id)
            if scope['project_id'] == request.source.project_id:
                raise Conflict('This source belongs to the selected project; use its ordinary association')
            oid, locator = digest(request.source.model_dump()), canonical(request.source.model_dump())
            aid = digest([request.scope_id, oid, request.conversation_id])
            old = self._read_grant(db, request.scope_id, oid, request.conversation_id)
            edge = db.execute('SELECT * FROM context_associations WHERE id=?', (aid,)).fetchone()
            if ((old['revision'] if old else 0) != request.expected_grant_revision
                    or (edge['revision'] if edge else 0) != request.expected_revision):
                raise Conflict('The read-only source decision changed; preserve the newer review')
            withdrawing = request.state in {'RETRACTED', 'REJECTED', 'EXCLUDED'}
            origin = db.execute('SELECT * FROM context_origins WHERE id=?', (oid,)).fetchone()
            now = stamp()
            if withdrawing:
                if (old is None or origin is None or origin['locator_json'] != locator
                        or old['review_sha256'] != request.review_sha256):
                    raise Conflict('Withdraw only the exact previously reviewed source grant')
                fingerprint = old['source_fingerprint']
            else:
                preview = self._prepare_read_grant(db, request)
                if preview['review_sha256'] != request.review_sha256:
                    raise Conflict('Reviewed source or target scope changed; preview the exact fragment again')
                fingerprint = preview['fingerprint']
                if origin is None:
                    self._bound(db, 'context_origins', MAX_ORIGINS)
                    db.execute('INSERT INTO context_origins VALUES (?,?,?,?,?,?,0,?)',
                        (oid, request.source.project_id, request.source.kind, locator, fingerprint, preview['title'][:160], now))
                elif origin['locator_json'] != locator or origin['fingerprint'] != fingerprint:
                    raise Conflict('Original source identity or fingerprint changed')
                if request.source.kind == 'NOTE_FRAGMENT':
                    db.execute('INSERT OR IGNORE INTO context_fragment_bases VALUES (?,?)', (oid, request.source.object_id))
                if request.source.kind == 'FILE_SNAPSHOT':
                    self._pin_origin_file(db, oid, request.source)
            if old is None:
                self._bound(db, 'context_read_grants', 8192)
            if edge is None:
                self._bound(db, 'context_associations', MAX_ASSOCIATIONS)
            if request.source.kind == 'FILE_SNAPSHOT':
                if request.state == 'ACTIVE':
                    self._restore_file_exclusions(db, request, request.source.file_identity_sha256, now)
                elif request.state == 'EXCLUDED':
                    self._exclude_file(db, request, aid, request.source.file_identity_sha256, now)
            gid = digest(['read-grant', request.scope_id, oid, request.conversation_id])
            revision, grant_revision = request.expected_revision + 1, request.expected_grant_revision + 1
            db.execute('INSERT INTO context_read_grants VALUES (?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET source_fingerprint=excluded.source_fingerprint,state=excluded.state,'
                'revision=excluded.revision,review_sha256=excluded.review_sha256,updated_at=excluded.updated_at',
                (gid, request.scope_id, request.conversation_id, oid, fingerprint, request.state,
                 grant_revision, request.review_sha256, now))
            db.execute('INSERT INTO context_associations VALUES (?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET state=excluded.state,manual_locked=1,revision=excluded.revision,updated_at=excluded.updated_at',
                (aid, request.scope_id, oid, request.conversation_id, request.state, 1, revision, now))
            return {'grant_id': gid, 'grant_revision': grant_revision, 'association_id': aid,
                'origin_id': oid, 'revision': revision, 'state': request.state,
                'execution_authorized': False, 'original_project_changed': False, 'files_changed': False}
        return self._mutate('read_grant', request, apply)

    def read_grants(self, scope_id, *, conversation_id=None, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise Conflict('Read-only source page is outside its bounded range')
        with self.store.connect() as db:
            scope = self._scope(db, scope_id)
            self._conversation(scope['project_id'], conversation_id)
            rows = db.execute('SELECT g.*,o.title,o.kind,o.locator_json,o.project_id AS source_project_id,'
                'o.forgotten,a.revision AS association_revision FROM context_read_grants g '
                'JOIN context_origins o ON o.id=g.origin_id JOIN context_associations a '
                'ON a.scope_id=g.scope_id AND a.origin_id=g.origin_id AND a.conversation_id IS g.conversation_id '
                'WHERE g.scope_id=? AND (? IS NULL OR g.conversation_id IS NULL OR g.conversation_id=?) '
                'ORDER BY g.id LIMIT ? OFFSET ?', (scope_id, conversation_id, conversation_id, limit + 1, offset)).fetchall()
        items = []
        for row in rows[:limit]:
            item = dict(row)
            item['source_ref'] = json.loads(item.pop('locator_json'))
            items.append(item)
        return {'items': items, 'next_offset': offset + limit if len(rows) > limit else None,
                'source_contents_read': False, 'execution_authorized': False}

    def source_catalog(self, project_id, *, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise Conflict('Source catalog page is outside its bounded range')
        if project_id is not None:
            self.store.project(project_id)
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM context_origins WHERE project_id IS ? AND forgotten=0 '
                'ORDER BY id LIMIT ? OFFSET ?', (project_id, limit + 1, offset)).fetchall()
        items = []
        for row in rows[:limit]:
            source = ({'kind': 'NOTE', 'project_id': project_id, 'object_id': row['id']}
                      if row['kind'] == 'NOTE' else json.loads(row['locator_json']))
            items.append({'origin_id': row['id'], 'title': row['title'], 'kind': row['kind'], 'source_ref': source})
        return {'items': items, 'next_offset': offset + limit if len(rows) > limit else None,
                'source_contents_read': False, 'execution_authorized': False}
