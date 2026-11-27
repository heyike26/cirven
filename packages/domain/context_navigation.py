"""Reference navigation, separate from model retrieval and write permission.

Only registered context relationships are inspected. A relationship may remain
useful after a saved-byte update; its old content is never certified as current.
"""
import json

from packages.domain.context_read_budget import context_read_scope
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest


class ContextNavigation:
    @context_read_scope()
    def resolve_original_file(self, source):
        """Internal original-file resolver; native paths stay in host routes."""
        if source.kind != 'FILE_SNAPSHOT':
            raise Conflict('Select an exact registered file source')
        with self.store.connect() as db:
            origin = self._origin(db, digest(source.model_dump()))
            if origin['project_id'] != source.project_id:
                raise Conflict('File navigation source belongs to another project')
        adapter = self._file_adapter()
        location = adapter.file_locator(source.link_ref.model_dump())
        if location['file_key'] != source.file_identity_sha256:
            raise Conflict('Original file parent or logical identity changed')
        current = adapter.registered_file_for_open(source.link_ref.model_dump())
        checked = adapter.file_locator(source.link_ref.model_dump())
        if checked['file_key'] != location['file_key']:
            raise Conflict('Original file location changed while resolving the request')
        return {**current, 'context_source_ref': source.model_dump(), 'context_file_key': location['file_key']}

    def _discussion_projection(self, conversation_id):
        if conversation_id is None:
            return None
        try:
            value = self.conversation_reader(conversation_id)
        except (Conflict, NotFound):
            return {'id': conversation_id, 'available': False}
        return {'id': conversation_id, 'available': True, 'conversation_ref': value['conversation_ref'],
                'project_id': value['project_id'], 'title': value.get('title'), 'archived': value.get('archived', False)}

    @context_read_scope()
    def file_relationships(self, location, *, saved_sha256, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise Conflict('Related discussion page is outside its bounded range')
        pid, key = location['project_id'], location['file_key']
        self.store.project(pid)
        with self.store.connect() as db:
            rows = db.execute('SELECT a.*,o.locator_json,s.label AS scope_label '
                'FROM context_origin_files f JOIN context_origins o ON o.id=f.origin_id '
                'JOIN context_associations a ON a.origin_id=o.id JOIN context_scopes s ON s.id=a.scope_id '
                'WHERE f.file_key=? AND o.project_id=? AND o.forgotten=0 '
                'AND a.state IN ("ACTIVE","EXCLUDED","REJECTED") ORDER BY a.id', (key, pid)).fetchall()
            result = []
            for row in rows:
                locator = json.loads(row['locator_json'])
                if digest(locator) != row['origin_id'] or locator.get('file_identity_sha256') != key:
                    raise Conflict('Related file identity integrity differs')
                result.append({'id': row['id'], 'relation': 'DIRECT', 'scope_id': row['scope_id'],
                    'scope_label': row['scope_label'], 'conversation_id': row['conversation_id'],
                    'origin_id': row['origin_id'], 'revision': row['revision'], 'state': row['state'],
                    'source_ref': locator, 'saved_version_matches': None})
            rules = db.execute('SELECT r.*,s.label AS scope_label FROM context_folder_rules r '
                'JOIN context_scopes s ON s.id=r.scope_id WHERE s.project_id=? AND r.state="ACTIVE" ORDER BY r.id LIMIT 1025',
                (pid,)).fetchall()
            if len(rules) > 1024:
                raise Conflict('Directory relationship index exceeds its bounded capacity')
            for row in rules:
                if self._folder_matches(dict(row), location):
                    result.append({'id': row['id'], 'relation': 'INHERITED', 'scope_id': row['scope_id'],
                        'scope_label': row['scope_label'], 'conversation_id': row['conversation_id'],
                        'origin_id': None, 'revision': row['revision'], 'state': row['state'],
                        'source_ref': None, 'saved_version_matches': None})
            result.sort(key=lambda row: (row['scope_id'], row['conversation_id'] or '', row['relation'], row['id']))
            page = result[offset:offset + limit]
            for row in page:
                ids = [scope['id'] for scope in self._ancestry(db, row['scope_id'])]
                row['file_excluded'] = bool(self._file_exclusions(db, ids, row['conversation_id'], key))
                if row['relation'] == 'DIRECT':
                    row['saved_version_matches'] = self._saved_version_matches(db, row['origin_id'], saved_sha256)
        # Fetch each current navigation header once, never an execution owner or
        # transcript. Moving a conversation changes its destination header only.
        discussions = {cid: self._discussion_projection(cid) for cid in {row['conversation_id'] for row in page}}
        for row in page:
            row['discussion'] = discussions[row['conversation_id']]
        return {'file_key': key, 'project_id': pid, 'items': page,
                'next_offset': offset + limit if offset + limit < len(result) else None,
                'source_verified': True, 'saved_sha256': saved_sha256,
                'coverage': 'REGISTERED_RELATIONSHIPS_NO_DISK_OR_TRANSCRIPT_SCAN', 'model_calls': 0,
                'version_check_scope': 'RECORDED_SNAPSHOT_METADATA_VS_OBSERVED_SAVED_BYTES_NOT_ARCHIVE_CONTENT_PROOF',
                'execution_authorized': False, 'file_write_authorized': False}

    def _saved_version_matches(self, db, origin_id, saved_sha256):
        # Context fingerprints describe selected parsed text; snapshot SHA is
        # a metadata digest. Compare actual saved bytes via the original reader.
        location = self._origin_location(db, origin_id)
        if location is None:
            return False
        row = db.execute('SELECT locator_json FROM context_origins WHERE id=?', (origin_id,)).fetchone()
        locator = json.loads(row['locator_json'])
        adapter = self._file_adapter()
        try:
            snapshot_sha = adapter.file_snapshot_saved_sha256(locator)
        except (Conflict, NotFound, OSError, ValueError, KeyError):
            return None
        return snapshot_sha == saved_sha256

    @context_read_scope()
    def conversation_files(self, conversation_id, *, scope_id=None, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise Conflict('Conversation file page is outside its bounded range')
        conversation = self.conversation_reader(conversation_id)
        with self.store.connect() as db:
            ids = []
            if scope_id is not None:
                scope = self._scope(db, scope_id)
                if scope['project_id'] != conversation['project_id']:
                    raise Conflict('Selected context scope belongs to another current project')
                ids = [row['id'] for row in self._ancestry(db, scope_id)]
            # Explicit conversation edges survive navigation moves. Scope-wide
            # files are included only for the explicitly selected context scope.
            sql = 'a.conversation_id=?'
            params = [conversation_id]
            if ids:
                sql += ' OR (a.conversation_id IS NULL AND a.scope_id IN (' + ','.join('?' for _ in ids) + '))'
                params += ids
            rows = db.execute('SELECT a.*,o.title,o.locator_json,s.label AS scope_label FROM context_associations a '
                'JOIN context_origins o ON o.id=a.origin_id JOIN context_scopes s ON s.id=a.scope_id '
                'WHERE o.kind="FILE_SNAPSHOT" AND o.forgotten=0 AND a.state IN ("ACTIVE","EXCLUDED") AND (' + sql + ') '
                'ORDER BY a.id LIMIT 8192', params).fetchall()
            items = []
            for row in rows:
                source = json.loads(row['locator_json'])
                if digest(source) != row['origin_id']:
                    raise Conflict('Conversation file source integrity differs')
                items.append({'id': row['id'], 'origin_id': row['origin_id'], 'title': row['title'],
                    'scope_id': row['scope_id'], 'scope_label': row['scope_label'], 'conversation_id': row['conversation_id'],
                    'relation': 'DIRECT', 'state': row['state'], 'revision': row['revision'], 'source_ref': source})
            # Reuse inheritance semantics over registered origins, including
            # conversation-specific directory links when no scope is selected.
            if not ids:
                ids = [row['scope_id'] for row in db.execute('SELECT DISTINCT scope_id FROM context_folder_rules '
                    'WHERE conversation_id=? AND state="ACTIVE" ORDER BY scope_id', (conversation_id,))]
                inherited = []
                for sid in ids:
                    candidates = self._inherited_candidates(db, [sid], conversation_id)
                    for candidate in candidates:
                        refs = candidate['folder_rule_refs']
                        owned = [ref for ref in refs if db.execute('SELECT conversation_id FROM context_folder_rules WHERE id=?',
                            (ref['id'],)).fetchone()['conversation_id'] == conversation_id]
                        if owned:
                            inherited.append({**candidate, 'folder_rule_refs': owned})
            else:
                inherited = self._inherited_candidates(db, ids, conversation_id)
            for row in inherited:
                if any(item['origin_id'] == row['origin_id'] for item in items):
                    continue
                origin = self._origin(db, row['origin_id'])
                items.append({**row, 'scope_label': self._scope(db, row['scope_id'])['label'],
                    'conversation_id': conversation_id, 'source_ref': json.loads(origin['locator_json'])})
            for item in items:
                chain = [row['id'] for row in self._ancestry(db, item['scope_id'])]
                key = item['source_ref']['file_identity_sha256']
                item['file_excluded'] = bool(self._file_exclusions(db, chain, conversation_id, key))
                item['source_project_id'] = item['source_ref']['project_id']
            items.sort(key=lambda row: (row['title'], row['id']))
        return {'conversation_ref': conversation['conversation_ref'], 'project_id': conversation['project_id'],
                'items': items[offset:offset + limit],
                'next_offset': offset + limit if offset + limit < len(items) else None,
                'model_calls': 0, 'execution_authorized': False, 'file_write_authorized': False}
