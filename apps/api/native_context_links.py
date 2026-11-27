"""Original page -> registered file -> discussion metadata, without retargeting.

Raw Word paths stay here. Matching never grants a file tool or injects text.
"""
import json
import ntpath
import os
from pathlib import Path, PureWindowsPath

from packages.domain.context_graph import SourceRef
from packages.domain.context_read_budget import context_read_scope
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import digest


def _same_native_file(left, right):
    if os.name != 'nt':
        return False
    from packages.adapters.windows_workspace_handles import Win32Kernel
    api = Win32Kernel()
    handles = []
    try:
        for path in (left, right):
            handles.append(api.open(path, 0x80, 7))  # Attributes only, share read/write/delete.
        facts = [api.info(handle) for handle in handles]
        if any(value['attributes'] & (0x400 | 0x10) for value in facts):
            return False
        return ([(value['device'], value['inode']) for value in facts][0]
                == [(value['device'], value['inode']) for value in facts][1]
                and api.final_path(handles[0]) == api.final_path(handles[1]))
    finally:
        for handle in handles:
            api.close(handle)


class NativeContextLinks:
    def __init__(self, app, *, same_file=_same_native_file):
        self.app, self.same_file = app, same_file

    def _candidate_path(self, source):
        files = self.app.state.file_continuity
        item = files._link(source.link_ref.model_dump())
        if item['kind'] == 'WORKSPACE_FILE':
            ref = item['request']['file_source_ref']
            binding = files.ws.get(item['project_id'], ref['workspace_id'], ref['binding_revision'])
            if binding['binding_sha256'] != ref['binding_sha256']:
                raise Conflict('Registered native file workspace binding changed')
            return str(Path(binding['root']).joinpath(*ref['path'].split('/')))
        if item['kind'] not in {'REPORT_WORKING_COPY', 'ARTIFACT_WORKING_COPY', 'DOCX_REVISION_WORKING_COPY'}:
            raise Conflict('Registered native file kind is unsupported')
        from packages.domain.paths import resolve_data_path
        part = item['request'].get('report') or item['request']['artifact']
        parent = resolve_data_path('/'.join(('native-working-copies', item['project_id'], part['artifact_id'], part['kind'])), files.data_dir)
        return str(parent / item['filename'])

    @context_read_scope()
    def related(self, reference, *, project_id=None):
        state = self.app.state
        result = {'context_ref': reference, 'status': 'UNAVAILABLE', 'items': [], 'model_calls': 0,
            'automatic_selection': False, 'execution_authorized': False, 'file_write_authorized': False,
            'document_identity_is_current': False,
            'identity_scope': 'FROZEN_WORD_WINDOW_LOCATION_AND_CURRENT_REGISTERED_FILE_LOCATION_NOT_PAGE_BYTES',
            'saved_bytes_equal_page': False, 'coverage': 'REGISTERED_CONTEXT_FILE_ORIGINS_ONLY_NO_DISK_OR_HISTORY_SCAN'}
        rpc = getattr(state, 'desktop_context_rpc', None)
        if rpc is None:
            return result
        proof = rpc.frozen_document_identity(reference)
        if proof is None:
            return result
        native_path = proof['native_path']
        filename = PureWindowsPath(native_path).name
        graph = state.context_graph
        if project_id is not None:
            state.store.project(project_id)
        with state.store.connect() as db:
            # Bounded registered metadata only. Filter without opening a user file.
            rows = db.execute('SELECT o.id,o.locator_json,f.locator_json AS file_location_json,f.locator_sha256,f.file_key '
                'FROM context_origins o JOIN context_origin_files f ON f.origin_id=o.id '
                'WHERE o.kind="FILE_SNAPSHOT" AND o.forgotten=0 AND (? IS NULL OR o.project_id=?) '
                'ORDER BY o.id LIMIT 4097', (project_id, project_id)).fetchall()
        if len(rows) > 4096:
            return {**result, 'status': 'INDEX_LIMIT_REACHED'}
        candidates, seen = [], set()
        for row in rows:
            try:
                source = SourceRef.model_validate(json.loads(row['locator_json']))
                location = json.loads(row['file_location_json'])
            except (ValueError, TypeError):
                raise Conflict('Native page registered file metadata is invalid') from None
            if (digest(source.model_dump()) != row['id'] or digest(location) != row['locator_sha256']
                    or location.get('file_key') != row['file_key'] or row['file_key'] != source.file_identity_sha256):
                raise Conflict('Native page registered source identity differs')
            if (location.get('name') or location.get('filename') or '').casefold() != filename.casefold():
                continue
            key = (source.project_id, source.file_identity_sha256)
            if key in seen:
                continue
            seen.add(key)
            try:
                path = self._candidate_path(source)
                if (ntpath.basename(path).casefold() == filename.casefold()
                        and ntpath.normcase(ntpath.normpath(path)) == ntpath.normcase(ntpath.normpath(native_path))):
                    candidates.append((source, path))
            except (Conflict, InvalidInput, NotFound, OSError, ValueError, KeyError):
                continue
        if len(candidates) > 8:
            return {**result, 'status': 'AMBIGUOUS_REGISTRATIONS', 'candidate_count': len(candidates)}
        items, incomplete = [], False
        for source, path in candidates:
            try:
                files = state.file_continuity
                exact = files.resolve_open(source.link_ref.model_dump())
                actual = exact.get('file_source_ref')
                if actual is not None:
                    path = self._candidate_path(source)
                else:
                    path = exact['native_path']
                if not self.same_file(native_path, path):
                    continue
                location = files.context_file_location(source.link_ref.model_dump())
                if location['file_key'] != source.file_identity_sha256:
                    raise Conflict('Registered native file parent identity changed')
                relations = graph.file_relationships(location, saved_sha256=exact['current_sha256'])
                items.append({'project_id': source.project_id, 'project_name': state.store.project(source.project_id)['name'],
                    'filename': PureWindowsPath(native_path).name,
                    'file_key': location['file_key'], 'source_ref': source.model_dump(),
                    'link_ref': source.link_ref.model_dump(), 'relationships': relations['items'],
                    'next_relationship_offset': relations['next_offset'],
                    'document_location_verified': True, 'unsaved_changes_observed': not proof['saved']})
            except (Conflict, InvalidInput, NotFound, OSError, ValueError, KeyError):
                incomplete = True
        return {**result, 'status': 'PARTIAL' if incomplete else 'MATCHED' if len(items) == 1 else 'AMBIGUOUS' if items else 'NO_REGISTERED_MATCH',
            'items': items, 'document_identity_sha256': proof['identity_sha256'],
            'window_lifetime_sha256': proof['window_lifetime_sha256'], 'unsaved_changes_observed': not proof['saved']}


def register_native_context_links(app):
    from fastapi import Query
    from packages.domain.discovery_schema import RevisionRef
    from packages.domain.questions import RequestModel
    class Input(RequestModel):
        context_ref: RevisionRef
    owner = NativeContextLinks(app)
    app.state.native_context_links = owner

    @app.post('/api/context/native-pages/related')
    def related(body: Input, project_id: str | None = Query(None, pattern=r'^[A-Za-z0-9_-]{1,100}$')):
        return owner.related(body.context_ref.model_dump(), project_id=project_id)
    return owner
