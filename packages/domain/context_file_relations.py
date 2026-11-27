"""Directory inheritance and durable per-file exclusions on the context index.

Only already registered, source-pinned files can be inherited. This module does
not enumerate user directories, copy files, move owners, or expand permissions.
"""
import json

from packages.domain.context_file_models import FolderRef, FolderSelection
from packages.domain.context_read_budget import context_read_scope, current_context_read_budget
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest

MAX_FOLDER_RULES = 1024
MAX_EFFECTIVE_FOLDER_RULES = 128


class ContextFileRelations:
    def _file_adapter(self):
        adapter = getattr(self.source_reader, '__self__', None)
        if adapter is None or not hasattr(adapter, 'file_locator') or not hasattr(adapter, 'folder_reference'):
            raise Conflict('Exact file/directory source adapter is unavailable')
        return adapter

    def _pin_origin_file(self, db, origin_id, source):
        if source.kind != 'FILE_SNAPSHOT':
            return None
        location = self._file_adapter().file_locator(source.link_ref.model_dump())
        if location['file_key'] != source.file_identity_sha256:
            raise Conflict('File location differs from the selected exact source')
        previous = db.execute('SELECT * FROM context_origin_files WHERE origin_id=?', (origin_id,)).fetchone()
        if previous:
            if previous['file_key'] != location['file_key'] or digest(json.loads(previous['locator_json'])) != previous['locator_sha256']:
                raise Conflict('Indexed file location integrity differs')
        else:
            db.execute('INSERT INTO context_origin_files VALUES (?,?,?,?)',
                (origin_id, location['file_key'], canonical(location), digest(location)))
        return location['file_key']

    @staticmethod
    def _origin_location(db, origin_id):
        row = db.execute('SELECT * FROM context_origin_files WHERE origin_id=?', (origin_id,)).fetchone()
        if row is None:
            return None
        value = json.loads(row['locator_json'])
        if digest(value) != row['locator_sha256'] or value.get('file_key') != row['file_key']:
            raise Conflict('Indexed file location integrity differs')
        return value

    @staticmethod
    def _file_exclusions(db, scope_ids, conversation_id, file_key=None):
        if not scope_ids:
            return []
        slots = ','.join('?' for _ in scope_ids)
        return [dict(row) for row in db.execute(
            f'SELECT * FROM context_file_exclusions WHERE scope_id IN ({slots}) AND state="ACTIVE" '
            'AND (conversation_id IS NULL OR conversation_id IS ?) AND (? IS NULL OR file_key=?) ORDER BY id',
            (*scope_ids, conversation_id, file_key, file_key))]

    def _excluded_file_origins(self, db, scope_ids, conversation_id):
        exclusions = self._file_exclusions(db, scope_ids, conversation_id)
        keys = {row['file_key'] for row in exclusions}
        if not keys:
            return set()
        # The bounded index, never the user's file tree. Also blocks notes
        # whose exact dependencies refer to another saved version of the file.
        return {row['origin_id'] for row in db.execute('SELECT origin_id,file_key FROM context_origin_files')
                if row['file_key'] in keys}

    def _exclude_file(self, db, request, association_id, file_key, stamp):
        ident = digest([request.scope_id, request.conversation_id, file_key])
        old = db.execute('SELECT * FROM context_file_exclusions WHERE id=?', (ident,)).fetchone()
        if old and old['state'] == 'ACTIVE':
            return
        revision = old['revision'] + 1 if old else 1
        db.execute('INSERT INTO context_file_exclusions VALUES (?,?,?,?,?,?,?,?) '
            'ON CONFLICT(id) DO UPDATE SET state=excluded.state,revision=excluded.revision,'
            'association_id=excluded.association_id,updated_at=excluded.updated_at',
            (ident, request.scope_id, request.conversation_id, file_key, 'ACTIVE', revision, association_id, stamp))

    def _restore_file_exclusions(self, db, request, file_key, stamp):
        scope_ids = [row['id'] for row in self._ancestry(db, request.scope_id)]
        active = self._file_exclusions(db, scope_ids, request.conversation_id, file_key)
        expected = {item.id: item.revision for item in request.restore_file_exclusions}
        if expected != {row['id']: row['revision'] for row in active}:
            raise Conflict('Review the current manual file exclusion before restoring references')
        for row in active:
            db.execute('UPDATE context_file_exclusions SET state="RETRACTED",revision=revision+1,updated_at=? WHERE id=?',
                       (stamp, row['id']))
            # The old EXCLUDED origin edge must not shadow an explicitly
            # restored file through another range or saved-byte version.
            db.execute('UPDATE context_associations SET state="RETRACTED",revision=revision+1,updated_at=? '
                       'WHERE id=? AND state="EXCLUDED"', (stamp, row['association_id']))

    def _folder_current(self, reference):
        expected = FolderRef.model_validate(reference).model_dump()
        key = ('folder-reference', digest(expected))
        allowance = current_context_read_budget()
        if allowance is not None and key in allowance.cache:
            return allowance.cache[key]
        selected = {key: expected[key] for key in FolderSelection.model_fields}
        current = self._file_adapter().folder_reference(selected)
        valid = current == expected
        if allowance is not None:
            allowance.cache[key] = valid
        return valid

    def folder_rule(self, request):
        from packages.domain.context_graph import stamp
        def apply(db):
            scope = self._scope(db, request.scope_id)
            reference = request.folder_ref.model_dump()
            if reference['project_id'] != scope['project_id']:
                raise Conflict('Folder rule belongs to another project')
            ident = digest([request.scope_id, request.conversation_id, reference])
            previous = db.execute('SELECT * FROM context_folder_rules WHERE id=?', (ident,)).fetchone()
            if (previous['revision'] if previous else 0) != request.expected_revision:
                raise Conflict('Folder association changed; refresh before applying')
            if request.state in {'ACTIVE', 'CANDIDATE'} or previous is None:
                self._conversation(scope['project_id'], request.conversation_id)
                if not self._folder_current(reference):
                    raise Conflict('Folder identity or workspace binding changed; select the directory again')
            if previous is None:
                self._bound(db, 'context_folder_rules', MAX_FOLDER_RULES)
            revision = request.expected_revision + 1
            db.execute('INSERT INTO context_folder_rules VALUES (?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET range_kind=excluded.range_kind,state=excluded.state,'
                'revision=excluded.revision,updated_at=excluded.updated_at',
                (ident, request.scope_id, request.conversation_id, canonical(reference), digest(reference),
                 request.range, request.state, revision, stamp()))
            return {'folder_rule_id': ident, 'revision': revision, 'state': request.state,
                    'range': request.range, 'files_moved': False, 'execution_authorized': False}
        return self._mutate('folder_rule', request, apply)

    @context_read_scope()
    def folder_rules(self, scope_id, *, conversation_id=None, offset=0, limit=50):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 50:
            raise Conflict('Directory association page is outside its bounded range')
        with self.store.connect() as db:
            scope = self._scope(db, scope_id)
            self._conversation(scope['project_id'], conversation_id)
            rows = db.execute('SELECT * FROM context_folder_rules WHERE scope_id=? '
                'AND (? IS NULL OR conversation_id IS ?) ORDER BY id LIMIT ? OFFSET ?',
                (scope_id, conversation_id, conversation_id, limit + 1, offset)).fetchall()
        items = []
        for row in rows[:limit]:
            value = dict(row)
            reference = json.loads(value.pop('folder_json'))
            if digest(reference) != value.pop('folder_sha256'):
                raise Conflict('Folder association integrity differs')
            try:
                current = self._folder_current(reference)
            except (Conflict, NotFound, OSError):
                current = False
            items.append({**value, 'folder_ref': reference, 'source_current': current})
        return {'items': items, 'next_offset': offset + limit if len(rows) > limit else None}

    def _effective_folder_rows(self, db, scope_ids, conversation_id):
        slots = ','.join('?' for _ in scope_ids)
        rows = db.execute(f'SELECT * FROM context_folder_rules WHERE scope_id IN ({slots}) AND state="ACTIVE" '
            'AND (conversation_id IS NULL OR conversation_id IS ?) ORDER BY id LIMIT ?',
            (*scope_ids, conversation_id, MAX_EFFECTIVE_FOLDER_RULES + 1)).fetchall()
        if len(rows) > MAX_EFFECTIVE_FOLDER_RULES:
            raise Conflict('Too many effective folder associations; narrow the context scope')
        return [dict(row) for row in rows]

    @context_read_scope()
    def inherited_files(self, scope_id, *, conversation_id=None, offset=0, limit=50):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 50:
            raise Conflict('Inherited file page is outside its bounded range')
        with self.store.connect() as db:
            scope = self._scope(db, scope_id)
            self._conversation(scope['project_id'], conversation_id)
            ids = [row['id'] for row in self._ancestry(db, scope_id)]
            rows = self._inherited_candidates(db, ids, conversation_id)
            exclusions = self._file_exclusions(db, ids, conversation_id)
            items = []
            for row in rows[offset:offset + limit]:
                origin = self._origin(db, row['origin_id'])
                location = self._origin_location(db, origin['id'])
                edge_id = digest([scope_id, origin['id'], conversation_id])
                edge = db.execute('SELECT * FROM context_associations WHERE id=?', (edge_id,)).fetchone()
                items.append({**row, 'inherited_from_scope_id': row['scope_id'], 'scope_id': scope_id,
                    'conversation_id': conversation_id, 'revision': edge['revision'] if edge else 0,
                    'state': edge['state'] if edge else 'ACTIVE', 'manual_locked': edge['manual_locked'] if edge else 0,
                    'forgotten': 0, 'source_ref': json.loads(origin['locator_json']),
                    'file_exclusions': [{'id': entry['id'], 'revision': entry['revision'], 'scope_id': entry['scope_id'],
                        'conversation_id': entry['conversation_id']} for entry in exclusions if entry['file_key'] == location['file_key']]})
        return {'items': items, 'next_offset': offset + limit if offset + limit < len(rows) else None,
                'coverage': 'REGISTERED_FILE_SOURCES_ONLY_NO_FILESYSTEM_SCAN'}

    def _folder_matches(self, row, location):
        if location.get('kind') != 'WORKSPACE_FILE':
            return False
        folder = json.loads(row['folder_json'])
        if digest(folder) != row['folder_sha256']:
            raise Conflict('Folder association integrity differs')
        parent = location['parent_ref']
        if any(folder[key] != parent[key] for key in ('project_id', 'workspace_id', 'binding_revision', 'binding_sha256')):
            return False
        parts, actual = tuple(folder['segments']), tuple(parent['segments'])
        if row['range_kind'] == 'CURRENT_DIRECTORY':
            member = parts == actual and all(folder[key] == parent[key] for key in ('device', 'inode'))
        else:
            member = actual[:len(parts)] == parts
        if not member:
            return False
        try:
            return self._folder_current(folder)
        except (Conflict, NotFound, OSError):
            return False

    def _inherited_candidates(self, db, scope_ids, conversation_id):
        rules = self._effective_folder_rows(db, scope_ids, conversation_id)
        if not rules:
            return []
        origins = db.execute('SELECT o.id AS origin_id,o.title,o.kind FROM context_origins o '
            'JOIN context_origin_files f ON f.origin_id=o.id WHERE o.forgotten=0 AND o.kind="FILE_SNAPSHOT" '
            'ORDER BY o.id LIMIT 4096').fetchall()
        result = []
        for origin in origins:
            location = self._origin_location(db, origin['origin_id'])
            matched = [rule for rule in rules if self._folder_matches(rule, location)]
            if not matched:
                continue
            for scope_id in scope_ids:
                own = [rule for rule in matched if rule['scope_id'] == scope_id]
                if not own:
                    continue
                result.append({'id': 'inherited:' + origin['origin_id'], 'scope_id': scope_id,
                    'origin_id': origin['origin_id'], 'title': origin['title'], 'kind': origin['kind'],
                    'file_key': location['file_key'],
                    'conversation_id': conversation_id if conversation_id is not None and any(
                        rule['conversation_id'] == conversation_id for rule in own) else None,
                    'state': 'ACTIVE', 'revision': 0, 'relation': 'INHERITED',
                    'folder_rule_refs': [{'id': row['id'], 'revision': row['revision']} for row in own]})
                break
        return result

    def _validate_inherited(self, db, item, scope_ids, conversation_id):
        location = self._origin_location(db, item['origin_id'])
        if location is None:
            raise Conflict('Inherited file location is unavailable')
        refs = item.get('folder_rule_refs') or []
        if not refs:
            raise Conflict('Inherited context has no exact directory basis')
        for reference in refs:
            row = db.execute('SELECT * FROM context_folder_rules WHERE id=?', (reference['id'],)).fetchone()
            if (row is None or row['state'] != 'ACTIVE' or row['revision'] != reference['revision']
                    or row['scope_id'] not in scope_ids or row['conversation_id'] not in (None, conversation_id)
                    or not self._folder_matches(dict(row), location)):
                raise Conflict('Inherited directory association changed; select context again')
