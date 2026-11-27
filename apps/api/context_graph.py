"""Context references reuse the original source services and same-origin auth."""
from fastapi import Query
from hashlib import sha256

from packages.domain.context_graph import (AssociationChange, ContextGraph, ForgetInput,
    NoteChange, ScopeChange, SelectionInput, SourceRef, SourceReadChange, SourceReadPreview, text_hash)
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import digest
from packages.domain.context_read_budget import context_read_scope, current_context_read_budget, ContextReadLimit
from packages.domain.context_file_models import FolderRef, FolderSelection, FolderRuleChange
from packages.domain.conversation_file_input import ConversationFileLink, ConversationFileInput, freeze_conversation_file
from packages.domain.local_workspaces import FileSource
from packages.domain.questions import RequestModel, Id
from packages.domain.context_conversation_models import (ConversationLinkChange,
    ConversationSettingsChange, AutomaticContextInput)
from pydantic import Field
from packages.domain.context_policy_models import GlobalPolicyChange
import sqlite3


class FileSourceSelection(RequestModel):
    snapshot_ref: ConversationFileLink
    link_ref: ConversationFileLink
    offset: int = Field(default=0, strict=True, ge=0, le=10_000_000)
    length: int = Field(default=4000, strict=True, ge=1, le=16000)


class SavedFileSelection(RequestModel):
    link_ref: ConversationFileLink


class OpenContextFile(RequestModel):
    source: SourceRef


class AssociationBasis(RequestModel):
    scope_id: Id
    source: SourceRef
    conversation_id: str | None = Field(default=None, pattern=r'^(native|original|discovery):[a-f0-9]{64}$')


def capture_context_conversation_event(app, body, *, project_id, conversation_id, namespace,
        input_sha256, selected_scope_id=None):
    """One discussion reference candidate, never a candidate for every message body."""
    graph = getattr(app.state, 'context_graph', None)
    if graph is None or conversation_id is None:
        return None
    try:
        target = graph.capture_target(project_id, conversation_id, selected_scope_id=selected_scope_id)
        if target['capture_status'] != 'READY':
            return {'proposal_status': target['capture_status'], 'model_calls': 0}
        current = graph.conversation_context(conversation_id)
        old = next((row for row in current['links'] if row['scope_id'] == target['scope_id']), None)
        if old is not None:
            return {'proposal_status': 'EXISTING_DECISION_RETAINED', 'state': old['state'],
                'revision': old['revision'], 'model_calls': 0}
        from packages.domain.context_conversation_models import ConversationLinkChange
        result = graph.link_conversation(ConversationLinkChange(operation_id=digest(['context-discussion-event',
            namespace, project_id, conversation_id, body['operation_id'], input_sha256]),
            scope_id=target['scope_id'], conversation_ref=current['conversation_ref'], expected_revision=0,
            state='ACTIVE' if target['archive_mode'] == 'AUTO' else 'CANDIDATE', manual_locked=False))
        return {**result, 'proposal_status': 'ADDED', 'model_calls': 0}
    except (Conflict, InvalidInput, NotFound, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return {'proposal_status': 'NOT_CAPTURED', 'reason': 'DISCUSSION_OR_SCOPE_CHANGED', 'model_calls': 0}


@context_read_scope()
def capture_context_file_event(app, body, *, project_id, conversation_id, namespace,
        input_sha256, selected_scope_id=None, frozen_file=None, observed_file=None):
    """Same incremental source event for original/native/Quick; no model, owner or scan."""
    graph = getattr(app.state, 'context_graph', None)
    if graph is None:
        return None
    selected = body.get('file_input')
    if selected is None:
        if body.get('file_link_ref') is not None:
            selected = {'kind': 'LINKED_FILE', 'link_ref': body['file_link_ref']}
        elif body.get('file_source_ref') is not None:
            selected = {'kind': 'WORKSPACE_FILE', 'source_ref': body['file_source_ref']}
    if selected is None:
        return None
    try:
        target = graph.capture_target(project_id, conversation_id, selected_scope_id=selected_scope_id)
        if target['capture_status'] != 'READY':
            return {'proposal_status': target['capture_status'], 'model_calls': 0}
        source_project = (selected.get('link_ref') or selected.get('source_ref'))['project_id']
        if source_project != project_id:
            return {'proposal_status': 'SKIPPED_FOREIGN_SOURCE', 'model_calls': 0}
        if frozen_file is not None:
            if (selected['kind'] == 'LINKED_FILE' and frozen_file['link_ref'] != selected['link_ref']
                    or selected['kind'] == 'WORKSPACE_FILE' and frozen_file.get('file_source_ref') != selected['source_ref']):
                raise Conflict('Captured frozen file differs from the original explicit input')
            snapshot, link = frozen_file['snapshot_ref'], frozen_file['link_ref']
        elif observed_file is not None and selected['kind'] == 'LINKED_FILE':
            snapshot, link = observed_file['snapshot_ref'], selected['link_ref']
            if observed_file['link_ref'] != link:
                raise Conflict('Captured file observation belongs to another exact link')
        else:
            saved = freeze_conversation_file(app.state.file_continuity, project_id, selected)
            snapshot, link = saved['snapshot_ref'], saved['link_ref']
        return graph.propose(AssociationChange(operation_id=digest(['context-input-file-event', namespace,
            project_id, conversation_id, body['operation_id'], input_sha256]), expected_revision=0,
            scope_id=target['scope_id'], conversation_id=conversation_id, source=graph.file_source(snapshot, link)))
    except (Conflict, InvalidInput, NotFound, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return {'proposal_status': 'NOT_CAPTURED', 'reason': 'INDEX_UNAVAILABLE_OR_SOURCE_CHANGED', 'model_calls': 0}


def automatic_input_context(app, body, *, project_id, conversation_id, namespace, input_sha256=None):
    """Original host boundary: no new model/loop, no mutation of input hashes."""
    graph = getattr(app.state, 'context_graph', None)
    if graph is None:
        return {'context_selection_id': None, 'selection_status': 'UNAVAILABLE', 'model_calls': 0}
    original = input_sha256 or digest(body)
    request = AutomaticContextInput(operation_id=digest(['automatic-context', namespace, project_id,
        conversation_id, body['operation_id'], original]), project_id=project_id,
        conversation_id=conversation_id, input_sha256=original, text_sha256=text_hash(body['raw_text']))
    def focus():
        files = app.state.file_continuity
        link = body.get('file_link_ref')
        source = body.get('file_source_ref')
        selected = body.get('file_input')
        if selected:
            link = selected.get('link_ref') if selected['kind'] == 'LINKED_FILE' else None
            source = selected.get('source_ref') if selected['kind'] == 'WORKSPACE_FILE' else None
        if link is not None:
            return [files.context_file_location(link)['file_key']]
        if source is not None:
            ref = FileSource.model_validate(source)
            checks = files.ws.check_saved_sources(ref.project_id, [ref])
            if checks[0]['status'] != 'CURRENT':
                raise Conflict('Exact selected file changed during automatic context lookup')
            return [files.ws.file_location(ref.project_id, ref.workspace_id, ref.path,
                binding_revision=ref.binding_revision, binding_sha256=ref.binding_sha256)['file_key']]
        return []
    return {**graph.automatic_context(request, body['raw_text'], focus_file_keys=focus),
            'project_id': project_id, 'conversation_id': conversation_id}


def context_selection_summary(value):
    return {'selection_id': value.get('context_selection_id'),
        'selection_receipt_id': value.get('selection_id'), 'mode': 'AUTO',
        'project_id': value.get('project_id'), 'conversation_id': value.get('conversation_id'),
        'status': value['selection_status'], 'scope_id': value.get('scope_id'),
        'selected_count': len(value.get('selected', [])),
        'omitted_count': sum(value['omission_counts'].values()) if 'omission_counts' in value else len(value.get('omitted', [])),
        'characters': value.get('characters', 0), 'model_calls': 0,
        **({'reason': value['reason']} if value.get('reason') else {})}


class ApplicationContextSources:
    def __init__(self, app):
        self.app = app

    @context_read_scope()
    def target_location(self, target):
        selected = ConversationFileInput.model_validate(target)
        files = self.app.state.file_continuity
        if selected.kind == 'WORKSPACE_FILE':
            source = selected.source_ref
            current = files.ws.check_saved_sources(source.project_id, [source])[0]
            if current['status'] != 'CURRENT':
                raise Conflict('Selected saved file changed; refresh it before locating discussions')
            location = files.ws.file_location(source.project_id, source.workspace_id, source.path,
                binding_revision=source.binding_revision, binding_sha256=source.binding_sha256)
            return location, current['live_sha256']
        reference = selected.link_ref.model_dump()
        current = files.resolve_open(reference)
        return self.file_locator(reference), current['current_sha256']

    @context_read_scope()
    def file_snapshot_saved_sha256(self, locator):
        budget = current_context_read_budget()
        key = ('file-receipt', locator['link_ref']['id'], locator['object_id'], locator['sha256'])
        if key not in budget.cache:
            snapshot = {'project_id': locator['project_id'], 'id': locator['object_id'],
                        'revision': locator['revision'], 'sha256': locator['sha256']}
            budget.cache[key] = self.app.state.file_continuity.context_snapshot_receipt(
                locator['link_ref'], snapshot)['saved_sha256']
        return budget.cache[key]

    @context_read_scope()
    def file_locator(self, reference):
        link = ConversationFileLink.model_validate(reference).model_dump()
        return self.app.state.file_continuity.context_file_location(link)

    @context_read_scope()
    def registered_file_for_open(self, reference):
        return self.app.state.file_continuity.resolve_open(reference)

    @context_read_scope()
    def folder_reference(self, selection):
        selected = FolderSelection.model_validate(selection)
        workspaces = getattr(self.app.state, 'local_workspaces', None) or self.app.state.file_continuity.ws
        value = workspaces.folder_reference(selected.project_id, selected.workspace_id,
            '/'.join(selected.segments), binding_revision=selected.binding_revision,
            binding_sha256=selected.binding_sha256)
        return FolderRef.model_validate(value).model_dump()

    @context_read_scope()
    def read(self, ref: SourceRef):
        state = self.app.state
        allowance = current_context_read_budget()
        cache = allowance.cache
        if ref.project_id is not None:
            state.store.project(ref.project_id)
        if ref.kind == 'DOCUMENT':
            key = ('document', ref.project_id, ref.object_id, ref.revision, ref.sha256)
            with state.store.connect() as db:
                sizes = db.execute('SELECT d.current_revision,length(v.raw_bytes) AS raw_size,'
                    'length(cast(v.content AS blob)) AS text_size,'
                    'coalesce(length(cast(p.outline AS blob)),0)+coalesce(length(cast(p.graph AS blob)),0)'
                    '+coalesce(length(cast(p.warnings AS blob)),0) AS metadata_size '
                    'FROM documents d JOIN document_versions v ON v.document_id=d.id '
                    'LEFT JOIN document_parses p ON p.document_id=v.document_id AND p.revision=v.revision '
                    'WHERE d.id=? AND d.project_id=? AND d.deleted_at IS NULL AND v.revision=?',
                    (ref.object_id, ref.project_id, ref.revision)).fetchone()
            if sizes is None or sizes['current_revision'] != ref.revision:
                raise Conflict('Document source revision is no longer current')
            if key not in cache:
                allowance.charge(sizes['raw_size'] + sizes['text_size'] + sizes['metadata_size'])
                document = state.store.document(ref.project_id, ref.object_id, ref.revision)
                if (document['sha256'] != ref.sha256 or document['current_revision'] != ref.revision
                        or sha256(document['raw_bytes']).hexdigest() != ref.sha256
                        or document.get('content_sha256') != text_hash(document['content'])):
                    raise Conflict('Document source bytes, parsed text or revision integrity differs')
                cache[key] = {'content': document['content'], 'title': document['filename']}
            text = cache[key]['content'][ref.offset:ref.offset + ref.length]
            title = cache[key]['title']
        elif ref.kind == 'FILE_SNAPSHOT':
            location = self.file_locator(ref.link_ref.model_dump())
            if location['file_key'] != ref.file_identity_sha256:
                raise Conflict('Selected saved-file parent or logical filename identity changed')
            snapshot = {'project_id': ref.project_id, 'id': ref.object_id, 'revision': ref.revision, 'sha256': ref.sha256}
            key = ('file', ref.link_ref.id, ref.link_ref.sha256, ref.object_id, ref.sha256)
            if key not in cache:
                current = state.file_continuity.context_snapshot(snapshot, link_ref=ref.link_ref.model_dump(),
                    offset=ref.offset, limit=ref.length, include_source_text=True)
                cache[key] = {'content': current['source_text_content'], 'title': current['filename']}
            text, title = cache[key]['content'][ref.offset:ref.offset + ref.length], cache[key]['title']
        elif ref.kind == 'CONVERSATION_MESSAGE':
            if ref.source_anchor is not None:
                key = ('anchored-message', ref.conversation_id, digest(ref.source_anchor.model_dump()))
                if key not in cache:
                    cache[key] = state.conversation_history.read_source_anchor(ref)
                full = cache[key]
                text = full['text'][ref.offset:ref.offset+ref.length]
                if not text:
                    raise Conflict('Selected original message range is empty')
                return {'text': text, 'title': full['title'], 'fingerprint': digest({'source': ref.model_dump(), 'text_sha256': text_hash(text)})}
            key = ('history', ref.conversation_id, ref.cursor)
            if key not in cache:
                cache[key] = state.conversation_history.read(ref.conversation_id, limit=100, cursor=ref.cursor)
            history = cache[key]
            message = next((row for row in history['messages'] if row['id'] == ref.message_id), None)
            if message is None:
                raise NotFound('Exact message is outside this bounded history window; select its original source')
            if message['text_sha256'] != ref.sha256 or message['text_truncated']:
                raise Conflict('Conversation message content differs or is incomplete')
            actual_project = message['source'].get('project_id')
            with state.store.connect() as db:
                if not state.conversation_threads._execution_project_matches(db, ref.project_id, actual_project):
                    raise Conflict('Exact message belongs to another source project')
            text = message['text'][ref.offset:ref.offset + ref.length]
            title = message['role'] + ': ' + message['text'][:100]
        elif ref.kind == 'COMMAND_OBSERVATION':
            owner = ref.command_owner
            key = ('commands', ref.project_id, owner.session_id, owner.session_sha256,
                   owner.workspace_id, owner.binding_revision, owner.binding_sha256)
            if key not in cache:
                # The public command reader below verifies the immutable owner
                # and workspace. Loading provider/capability inventories merely
                # to obtain its saved generation would add unrelated I/O.
                current = state.agent_sessions._state(ref.project_id, owner.session_id)
                cache[key] = state.agent_sessions.command_observations(ref.project_id, owner.session_id,
                    session_sha256=owner.session_sha256, generation=current['generation'],
                    workspace_ref={'id': owner.workspace_id, 'revision': owner.binding_revision,
                                   'binding_sha256': owner.binding_sha256})
            catalog = cache[key]
            record_ref = {'id': ref.object_id, 'revision': ref.revision, 'sha256': ref.sha256}
            matched = [row for row in catalog['commands'] if row['record_ref'] == record_ref
                       and row['source_generation'] == owner.generation]
            if len(matched) != 1:
                if allowance.remaining <= 1 or allowance.reads >= allowance.read_limit:
                    raise ContextReadLimit('Original command source read budget reached')
                raise NotFound('Exact original command observation is outside the verified history window')
            command = matched[0]
            # A command observation can support a reviewed lesson. It never
            # certifies all tests, the current tree, or whole-task acceptance.
            text = ('Historical command observation; not current validation or whole-task acceptance.\n'
                + 'Command: ' + command['command'] + '\nStatus: ' + command['status']
                + '\nExit code: ' + str(command.get('exit_code'))
                + '\nOutput: ' + (command.get('output') or {}).get('text', ''))
            text = text[ref.offset:ref.offset + ref.length]
            title = command['command'][:160]
        else:
            raise Conflict('Notes are resolved through the local reviewed note store')
        if not text:
            raise Conflict('Selected context range is empty')
        return {'text': text, 'title': title, 'fingerprint': digest({'source': ref.model_dump(), 'text_sha256': text_hash(text)})}


def register_context_graph(app):
    sources = ApplicationContextSources(app)
    graph = ContextGraph(app.state.store, sources.read, app.state.conversation_threads.view)
    app.state.context_graph = graph
    authority=getattr(getattr(app.state,'agent_sessions',None),'context_authority',None)
    if authority is not None:authority.graph=graph
    from apps.api.native_context_links import register_native_context_links
    register_native_context_links(app)

    @app.get('/api/context/defaults')
    def defaults():
        return graph.global_defaults()

    @app.put('/api/context/defaults')
    def save_defaults(body: GlobalPolicyChange):
        return graph.save_global_defaults(body)

    @app.post('/api/context/read-grants/preview')
    def preview_read_grant(body: SourceReadPreview):
        return graph.preview_read_grant(body)

    @app.post('/api/context/read-grants')
    def change_read_grant(body: SourceReadChange):
        return graph.change_read_grant(body)

    @app.get('/api/context/scopes/{scope_id}/read-grants')
    def read_grants(scope_id: str, conversation_id: str | None = Query(None, pattern=r'^(native|original|discovery):[a-f0-9]{64}$'),
                    offset: int = Query(0, ge=0, le=8192)):
        return graph.read_grants(scope_id, conversation_id=conversation_id, offset=offset)

    @app.get('/api/context/source-catalog')
    def source_catalog(project_id: str | None = Query(None, pattern=r'^[A-Za-z0-9_-]{1,100}$'), offset: int = Query(0, ge=0, le=4096)):
        return graph.source_catalog(project_id, offset=offset)

    @app.get('/api/context/scopes')
    def scopes(project_id: str | None = Query(default=None, pattern=r'^[A-Za-z0-9_-]{1,100}$')):
        return {'scopes': graph.scopes(project_id)}

    @app.get('/api/context/scopes/{scope_id}')
    def scope(scope_id: str):
        return graph.scope(scope_id)

    @app.get('/api/context/scopes/{scope_id}/conversations')
    def scope_conversations(scope_id: str, offset: int = Query(0, ge=0, le=4096), limit: int = Query(50, ge=1, le=50)):
        return graph.scope_conversations(scope_id, offset=offset, limit=limit)

    @app.get('/api/context/conversations/{conversation_id}/settings')
    def conversation_context(conversation_id: str):
        return graph.conversation_context(conversation_id)

    @app.post('/api/context/conversation-links')
    def link_conversation(body: ConversationLinkChange):
        return graph.link_conversation(body)

    @app.post('/api/context/conversation-settings')
    def conversation_preferences(body: ConversationSettingsChange):
        return graph.conversation_preferences(body)

    @app.put('/api/context/scopes/{scope_id}')
    def save_scope(scope_id: str, body: ScopeChange):
        import re
        from packages.domain.store import InvalidInput
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', scope_id):
            raise InvalidInput('Invalid context scope identity')
        return graph.save_scope(scope_id, body)

    @app.post('/api/context/associations')
    def associate(body: AssociationChange):
        return graph.associate(body)

    @app.post('/api/context/associations/basis')
    def association_basis(body: AssociationBasis):
        return graph.association_basis(body.scope_id, body.source, body.conversation_id)

    @app.post('/api/context/folders/resolve')
    def resolve_folder(body: FolderSelection):
        return {'folder_ref': sources.folder_reference(body.model_dump()), 'execution_authorized': False}

    @app.post('/api/context/folder-rules')
    def folder_rule(body: FolderRuleChange):
        return graph.folder_rule(body)

    @app.post('/api/context/files/resolve')
    @context_read_scope()
    def resolve_file(body: FileSourceSelection):
        source = graph.file_source(body.snapshot_ref.model_dump(), body.link_ref.model_dump(),
                                   offset=body.offset, length=body.length)
        value = sources.read(source)
        return {'source_ref': source.model_dump(), 'title': value['title'], 'text': value['text'], 'execution_authorized': False}

    @app.post('/api/context/files/observe')
    @context_read_scope()
    def observe_file(body: SavedFileSelection):
        # Original idempotent saved-byte observer, outside the context write
        # transaction. It never saves the user's document or activates memory.
        observed = app.state.file_continuity.observe(body.link_ref.model_dump())
        source = graph.file_source(observed['snapshot_ref'], observed['link_ref'])
        value = sources.read(source)
        return {'source_ref': source.model_dump(), 'title': value['title'], 'text': value['text'],
                'unsaved_changes': 'NOT_OBSERVED', 'execution_authorized': False}

    @app.post('/api/context/files/related')
    @context_read_scope()
    def related_file(body: ConversationFileInput, offset: int = Query(0, ge=0, le=9216),
                     limit: int = Query(50, ge=1, le=50)):
        location, saved_sha = sources.target_location(body.model_dump())
        return graph.file_relationships(location, saved_sha256=saved_sha, offset=offset, limit=limit)

    @app.post('/api/context/files/prepare-source')
    @context_read_scope()
    def prepare_source(body: ConversationFileInput):
        selected = body.model_dump()
        pid = (body.source_ref if body.kind == 'WORKSPACE_FILE' else body.link_ref).project_id
        frozen = freeze_conversation_file(app.state.file_continuity, pid, selected)
        source = graph.file_source(frozen['snapshot_ref'], frozen['link_ref'])
        value = sources.read(source)
        return {'source_ref': source.model_dump(), 'title': value['title'], 'text': value['text'],
                'execution_authorized': False, 'file_write_authorized': False}

    @app.post('/api/context/files/open-target')
    @context_read_scope()
    def open_target(body: OpenContextFile):
        source = body.source
        current = graph.resolve_original_file(source)
        return {'source_ref': source.model_dump(), 'file_link_ref': source.link_ref.model_dump(),
                'file_key': current['context_file_key'], 'filename': current['filename'],
                'current_saved_sha256': current['current_sha256'], 'model_calls': 0,
                'file_write_authorized': False, 'native_open_requested': False}

    @app.get('/api/context/conversations/{conversation_id}/files')
    def conversation_files(conversation_id: str, scope_id: str | None = None,
                           offset: int = Query(0, ge=0, le=12288), limit: int = Query(50, ge=1, le=50)):
        return graph.conversation_files(conversation_id, scope_id=scope_id, offset=offset, limit=limit)

    @app.get('/api/context/scopes/{scope_id}/folder-rules')
    def folder_rules(scope_id: str, conversation_id: str | None = None,
                     offset: int = Query(0, ge=0, le=1024), limit: int = Query(50, ge=1, le=50)):
        return graph.folder_rules(scope_id, conversation_id=conversation_id, offset=offset, limit=limit)

    @app.get('/api/context/scopes/{scope_id}/inherited-files')
    def inherited_files(scope_id: str, conversation_id: str | None = None,
                        offset: int = Query(0, ge=0, le=4096), limit: int = Query(50, ge=1, le=50)):
        return graph.inherited_files(scope_id, conversation_id=conversation_id, offset=offset, limit=limit)

    @app.post('/api/context/proposals')
    def propose(body: AssociationChange):
        return graph.propose(body)

    @app.get('/api/context/scopes/{scope_id}/associations')
    def associations(scope_id: str, conversation_id: str | None = None,
                     offset: int = Query(0, ge=0, le=8192), limit: int = Query(50, ge=1, le=100)):
        return graph.associations(scope_id, conversation_id=conversation_id, offset=offset, limit=limit)

    @app.get('/api/context/origins/{origin_id}/related')
    def related(origin_id: str, project_id: str | None = Query(default=None, pattern=r'^[A-Za-z0-9_-]{1,100}$')):
        return graph.related(origin_id, project_id)

    @app.post('/api/context/related')
    def related_source(body: SourceRef):
        return graph.related_source(body)

    @app.post('/api/context/notes')
    def note(body: NoteChange):
        return graph.note(body)

    @app.get('/api/context/origins/{origin_id}')
    def preview(origin_id: str, project_id: str | None = None):
        return graph.preview(origin_id, project_id)

    @app.post('/api/context/selections')
    def select(body: SelectionInput):
        return graph.select(body)

    @app.get('/api/context/selections/{selection_id}')
    def selection(selection_id: str, project_id: str | None = None, conversation_id: str | None = None):
        return graph.materialize(selection_id, project_id, conversation_id=conversation_id)

    @app.get('/api/context/selections/{selection_id}/receipt')
    def selection_receipt(selection_id: str, project_id: str | None = Query(default=None, pattern=r'^[A-Za-z0-9_-]{1,100}$')):
        return graph.selection_receipt(selection_id, project_id)

    @app.post('/api/context/forget')
    def forget(body: ForgetInput):
        return graph.forget(body)

    @app.get('/api/context/operations/{operation_id}')
    def operation(operation_id: str):
        return graph.operation(operation_id)
