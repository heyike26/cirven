"""Thin hierarchy references; original conversations and owners never move."""
import json
import re

from packages.domain.context_read_budget import context_read_scope
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest

MAX_CONVERSATION_LINKS = 4096
MAX_CONVERSATION_SCOPES = 32


class ContextConversations:
    def capture_target(self, project_id, conversation_id, *, selected_scope_id=None):
        """Collection follows the exact discussion scope, independently of AUTO/OFF retrieval."""
        self._conversation(project_id, conversation_id)
        with self.store.connect() as db:
            routing = self._routing(db, project_id, conversation_id)
            target = selected_scope_id or routing['scope_id']
            if target is None:
                return {'capture_status': 'SKIPPED_NO_UNIQUE_SCOPE', 'routing_status': routing['status']}
            scope = self._scope(db, target)
            if scope['project_id'] != project_id:
                return {'capture_status': 'SKIPPED_FOREIGN_SCOPE'}
            policy, _, _ = self._retrieval_policy(db, target, conversation_id)
            if not policy['capture'] or policy['archive_mode'] == 'MANUAL':
                return {'capture_status': 'SKIPPED_POLICY', 'scope_id': target}
            return {'capture_status': 'READY', 'scope_id': target, 'archive_mode': policy['archive_mode']}

    def automatic_context(self, request, text, *, focus_file_keys=()):
        """One deterministic, optional selection for an original admitted input.

        Caller verifies the original input hash. This index keeps only hashes,
        chosen references and omissions, never another copy of the question.
        """
        from packages.domain.context_graph import SelectionInput, text_hash
        if text_hash(text) != request.text_sha256:
            raise Conflict('Automatic context query differs from its original input')
        expected = digest({'kind': 'automatic_context', 'body': request.model_dump()})
        try:
            previous = self.operation(request.operation_id)
        except NotFound:
            previous = None
        if previous is not None:
            if previous['operation_kind'] != 'automatic_context' or previous['request_sha256'] != expected:
                raise Conflict('Automatic selection operation belongs to another exact input')
            return previous
        with self.store.connect() as db:
            # Preserve legacy/no-feature behavior without allocating an empty
            # immutable receipt for every ordinary message in every project.
            configured = db.execute('SELECT 1 FROM context_scopes WHERE project_id IS ? LIMIT 1',
                (request.project_id,)).fetchone()
            if not configured:
                return {'context_selection_id': None, 'selection_status': 'NO_CONFIGURED_CONTEXT', 'model_calls': 0}
        words = re.findall(r'[a-z0-9_]{2,40}|[\u3400-\u9fff]{2,40}', text[:4000].casefold())
        terms = []
        for word in words:
            candidates = ([word] + [word[i:i + 2] for i in range(len(word) - 1)]
                          if re.fullmatch(r'[\u3400-\u9fff]+', word) else [word])
            for term in candidates:
                if term not in terms:
                    terms.append(term)
                if len(terms) >= 24:
                    break
            if len(terms) >= 24:
                break
        def apply(db):
            self._conversation(request.project_id, request.conversation_id)
            routing = self._routing(db, request.project_id, request.conversation_id)
            preferences = routing['settings']
            if preferences['retrieval_mode'] != 'AUTO' or routing['scope_id'] is None:
                return {'context_selection_id': None, 'selection_status': preferences['retrieval_mode']
                    if preferences['retrieval_mode'] != 'AUTO' else routing['status'], 'model_calls': 0}
            basis = {'scope_id': routing['scope_id'], 'project_id': request.project_id,
                'status': routing['status'], 'link_basis': routing['link_basis']}
            focus = focus_file_keys() if callable(focus_file_keys) else focus_file_keys
            selection = self._select_in_transaction(db, SelectionInput(operation_id=request.operation_id,
                scope_id=routing['scope_id'], conversation_id=request.conversation_id,
                char_budget=16000, include_global=preferences['include_global']), automatic_terms=tuple(terms),
                focus_file_keys=tuple(focus)[:4], routing_basis=basis)
            return {**selection, 'context_selection_id': selection['selection_id'] if selection['selected'] else None,
                'selection_status': 'SELECTED' if selection['selected'] else 'EMPTY',
                'query_role': 'BOUNDED_LEXICAL_TITLE_MATCH_REVIEWED_SCOPE_NOTES_AND_EXACT_FILE_FOCUS_NOT_SEMANTIC_SEARCH'}
        try:
            return self._mutate('automatic_context', request, apply)
        except (Conflict, NotFound):
            # Optional retrieval does not consume a model attempt or block the
            # original task when its local index is full or concurrently changed.
            return {'context_selection_id': None, 'selection_status': 'NOT_SELECTED',
                'reason': 'CONTEXT_INDEX_CAPACITY_OR_SCOPE_CHANGED', 'model_calls': 0}

    @staticmethod
    def _conversation_preferences(db, cid):
        row = db.execute('SELECT * FROM context_conversation_preferences WHERE conversation_id=?', (cid,)).fetchone() if cid else None
        if row is None:
            return {'conversation_id': cid, 'preferred_scope_id': None, 'retrieval_mode': 'AUTO',
                'include_global': False, 'policy': {}, 'revision': 0}
        value = dict(row)
        value['policy'] = json.loads(value.pop('policy_json'))
        value['include_global'] = bool(value['include_global'])
        return value

    def _checked_conversation(self, reference):
        value = self.conversation_reader(reference.id)
        if value['conversation_ref'] != reference.model_dump():
            raise Conflict('Conversation organization changed; refresh before associating it')
        return value

    def _store_conversation_preferences(self, db, cid, previous, *, scope_id, mode, include_global, policy, stamp):
        if not previous['revision']:
            revoking = mode == 'OFF' or policy.get('retrieve') is False or policy.get('capture') is False
            self._bound(db, 'context_conversation_preferences', 4096 if revoking else 2048)
        revision = previous['revision'] + 1
        db.execute('INSERT INTO context_conversation_preferences VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(conversation_id) DO UPDATE SET preferred_scope_id=excluded.preferred_scope_id,'
            'retrieval_mode=excluded.retrieval_mode,include_global=excluded.include_global,policy_json=excluded.policy_json,'
            'revision=excluded.revision,updated_at=excluded.updated_at',
            (cid, scope_id, mode, int(include_global), canonical(policy), revision, stamp))
        return revision

    def link_conversation(self, request):
        from packages.domain.context_graph import stamp
        def apply(db):
            scope = self._scope(db, request.scope_id)
            cid = request.conversation_ref.id
            ident = digest([request.scope_id, cid])
            previous = db.execute('SELECT * FROM context_conversation_links WHERE id=?', (ident,)).fetchone()
            if (previous['revision'] if previous else 0) != request.expected_revision:
                raise Conflict('Discussion association changed; preserve the newer decision')
            if request.state in {'ACTIVE', 'CANDIDATE'} or previous is None:
                conversation = self._checked_conversation(request.conversation_ref)
                if conversation['project_id'] != scope['project_id'] and not request.allow_reference_to_other_project:
                    raise Conflict('A reference into another project needs the explicit reference-only choice')
            if previous is None:
                self._bound(db, 'context_conversation_links', MAX_CONVERSATION_LINKS)
                count = db.execute('SELECT count(*) FROM context_conversation_links WHERE conversation_id=?', (cid,)).fetchone()[0]
                if count >= MAX_CONVERSATION_SCOPES:
                    raise Conflict('Discussion scope-reference capacity reached; existing references remain')
            now, revision = stamp(), request.expected_revision + 1
            db.execute('INSERT INTO context_conversation_links VALUES (?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET conversation_ref_json=excluded.conversation_ref_json,state=excluded.state,'
                'manual_locked=excluded.manual_locked,revision=excluded.revision,updated_at=excluded.updated_at,navigation_only=excluded.navigation_only',
                (ident, request.scope_id, cid, canonical(request.conversation_ref.model_dump()), request.state,
                 int(request.manual_locked), revision, now, int(request.navigation_only)))
            preferred_revision = None
            if request.preference_expected_revision is not None:
                if request.state != 'ACTIVE' or not request.manual_locked or request.navigation_only:
                    raise Conflict('Choosing the discussion context requires an explicit active association')
                if self._checked_conversation(request.conversation_ref)['project_id'] != scope['project_id']:
                    raise Conflict('A reference into another project cannot retarget this discussion context')
                preferences = self._conversation_preferences(db, cid)
                if preferences['revision'] != request.preference_expected_revision:
                    raise Conflict('Discussion context preference changed; no association was committed')
                preferred_revision = self._store_conversation_preferences(db, cid, preferences,
                    scope_id=request.scope_id, mode=preferences['retrieval_mode'], include_global=preferences['include_global'],
                    policy=preferences['policy'], stamp=now)
            return {'conversation_link_id': ident, 'revision': revision, 'state': request.state,
                'navigation_only': request.navigation_only,
                'preferences_revision': preferred_revision, 'files_moved': False, 'owner_changed': False,
                'reference_only': True, 'execution_authorized': False}
        return self._mutate('conversation_link', request, apply)

    def conversation_preferences(self, request):
        from packages.domain.context_graph import stamp
        def apply(db):
            current = self._checked_conversation(request.conversation_ref)
            cid = request.conversation_ref.id
            previous = self._conversation_preferences(db, cid)
            if previous['revision'] != request.expected_revision:
                raise Conflict('Discussion context settings changed; preserve the current settings')
            if request.preferred_scope_id is not None:
                scope = self._scope(db, request.preferred_scope_id)
                if scope['project_id'] != current['project_id']:
                    raise Conflict('Preferred scope belongs to another current project')
                link = db.execute('SELECT state,navigation_only FROM context_conversation_links WHERE scope_id=? AND conversation_id=?',
                    (scope['id'], cid)).fetchone()
                if link is not None and link['navigation_only']:
                    raise Conflict('A navigation-only reference cannot choose the discussion context')
                if scope['kind'] in {'TOPIC', 'TASK'} and (link is None or link['state'] != 'ACTIVE'):
                    raise Conflict('Associate this discussion with the topic/task before using it as context')
                if link is not None and link['state'] in {'REJECTED', 'RETRACTED'}:
                    raise Conflict('Restore the reviewed scope association before selecting it')
            policy = {key: getattr(request, key) for key in ('capture', 'retrieve', 'char_budget') if getattr(request, key) is not None}
            revision = self._store_conversation_preferences(db, cid, previous, scope_id=request.preferred_scope_id,
                mode=request.retrieval_mode, include_global=request.include_global, policy=policy, stamp=stamp())
            return {'conversation_id': cid, 'revision': revision, 'execution_authorized': False, 'global_settings_changed': False}
        return self._mutate('conversation_preferences', request, apply)

    def _routing(self, db, project_id, cid):
        settings = self._conversation_preferences(db, cid)
        links = [dict(row) for row in db.execute('SELECT l.*,s.project_id,s.kind FROM context_conversation_links l '
            'JOIN context_scopes s ON s.id=l.scope_id WHERE l.conversation_id=? ORDER BY l.id LIMIT 33', (cid,))] if cid else []
        if len(links) > MAX_CONVERSATION_SCOPES:
            raise Conflict('Discussion scope-reference index exceeds its capacity')
        available = [row for row in links if row['project_id'] == project_id and not row['navigation_only']]
        active = [row for row in available if row['state'] == 'ACTIVE']
        preferred = settings['preferred_scope_id']
        chosen, status, basis = None, 'NO_SCOPE', []
        if preferred:
            scope = self._scope(db, preferred)
            if scope['project_id'] == project_id:
                edge = next((row for row in available if row['scope_id'] == preferred), None)
                navigation_edge = next((row for row in links if row['scope_id'] == preferred and row['navigation_only']), None)
                if navigation_edge or edge and edge['state'] != 'ACTIVE' or scope['kind'] in {'TOPIC', 'TASK'} and edge is None:
                    status = 'PREFERRED_SCOPE_INACTIVE'
                else:
                    chosen, status, basis = preferred, 'PREFERRED', [edge] if edge else []
                return {'scope_id': chosen, 'status': status, 'settings': settings,
                    'link_basis': [{'id': row['id'], 'revision': row['revision']} for row in basis]}
        if active:
            parents = {row['scope_id']: {scope['id'] for scope in self._ancestry(db, row['scope_id'])[1:]} for row in active}
            leaves = [row for row in active if not any(row['scope_id'] in chain for chain in parents.values())]
            if len(leaves) == 1:
                chosen, status, basis = leaves[0]['scope_id'], 'UNIQUE_ASSOCIATION', [leaves[0]]
            else:
                status = 'AMBIGUOUS_ASSOCIATIONS'
        else:
            denied = {row['scope_id'] for row in available if row['state'] in {'REJECTED', 'RETRACTED'}}
            roots = [row['id'] for row in db.execute('SELECT id FROM context_scopes WHERE project_id IS ? '
                'AND kind=? ORDER BY id LIMIT 513', (project_id, 'PROJECT' if project_id is not None else 'GLOBAL')) if row['id'] not in denied]
            if len(roots) == 1:
                chosen, status = roots[0], 'PROJECT_DEFAULT' if project_id is not None else 'GLOBAL_DEFAULT'
            elif len(roots) > 1:
                status = 'AMBIGUOUS_ROOTS'
        return {'scope_id': chosen, 'status': status, 'settings': settings,
            'link_basis': [{'id': row['id'], 'revision': row['revision']} for row in basis]}

    def _retrieval_policy(self, db, scope_id, cid):
        policy, basis = self._policy(db, scope_id)
        preferences = self._conversation_preferences(db, cid)
        # Temporary discussion settings can narrow existing retrieval. Explicit
        # parent exclusions and disabled retrieval cannot be widened here.
        for key in ('capture', 'retrieve'):
            if preferences['policy'].get(key) is False:
                policy[key], basis[key] = False, 'CONVERSATION'
        if preferences['retrieval_mode'] == 'OFF':
            policy['retrieve'], basis['retrieve'] = False, 'CONVERSATION'
        if preferences['policy'].get('char_budget') is not None:
            if preferences['policy']['char_budget'] < policy['char_budget']:
                policy['char_budget'], basis['char_budget'] = preferences['policy']['char_budget'], 'CONVERSATION'
        return policy, basis, {'conversation_id': cid, 'revision': preferences['revision']}

    @context_read_scope()
    def conversation_context(self, cid):
        current = self.conversation_reader(cid)
        with self.store.connect() as db:
            routing = self._routing(db, current['project_id'], cid)
            links = [dict(row) for row in db.execute('SELECT l.*,s.label,s.project_id,s.kind FROM context_conversation_links l '
                'JOIN context_scopes s ON s.id=l.scope_id WHERE l.conversation_id=? ORDER BY l.id LIMIT 33', (cid,))]
        for row in links:
            row['recorded_conversation_ref'] = json.loads(row.pop('conversation_ref_json'))
            row['current_project_matches'] = row['project_id'] == current['project_id']
        return {'conversation_ref': current['conversation_ref'], 'project_id': current['project_id'],
                'links': links, 'routing': routing, 'execution_authorized': False, 'model_calls': 0}

    @context_read_scope()
    def scope_conversations(self, scope_id, *, offset=0, limit=50):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 50:
            raise Conflict('Discussion reference page is outside its bounded range')
        with self.store.connect() as db:
            scope = self._scope(db, scope_id)
            rows = db.execute('SELECT * FROM context_conversation_links WHERE scope_id=? ORDER BY id LIMIT ? OFFSET ?',
                (scope_id, limit + 1, offset)).fetchall()
            children = [dict(row) for row in db.execute('SELECT id,label,kind,parent_id FROM context_scopes '
                'WHERE parent_id=? ORDER BY label,id LIMIT 512', (scope_id,))]
        items = []
        for row in rows[:limit]:
            value = dict(row)
            value['recorded_conversation_ref'] = json.loads(value.pop('conversation_ref_json'))
            value['discussion'] = self._discussion_projection(value['conversation_id'])
            value['current_project_matches'] = bool(value['discussion'] and value['discussion'].get('available')
                and value['discussion'].get('project_id') == scope['project_id'])
            items.append(value)
        return {'scope_id': scope_id, 'children': children, 'items': items,
                'next_offset': offset + limit if len(rows) > limit else None, 'model_calls': 0, 'execution_authorized': False}

    def _validate_context_routing(self, db, selection):
        cid = selection['conversation_id']
        preferences = self._conversation_preferences(db, cid)
        basis = selection.get('conversation_preferences_basis')
        if basis is not None and basis['revision'] != preferences['revision']:
            raise Conflict('Discussion context preference changed; select again')
        if preferences['retrieval_mode'] == 'OFF' and selection['selected']:
            raise Conflict('Discussion context retrieval was disabled')
        for ref in (selection.get('routing_basis') or {}).get('link_basis', []):
            edge = db.execute('SELECT * FROM context_conversation_links WHERE id=?', (ref['id'],)).fetchone()
            if edge is None or edge['state'] != 'ACTIVE' or edge['revision'] != ref['revision']:
                raise Conflict('Selected discussion-to-scope association was changed or withdrawn')
