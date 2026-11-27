"""Exact native open requests; immutable reports open as editable working copies."""
import hashlib
from pathlib import Path
import re
import threading

from fastapi import Depends, Header, Query
from pydantic import Field

from packages.domain.file_continuity import ARTIFACT_WORKING_COPY_PART, DOCX_REVISION_PART
from packages.domain.local_workspaces import FileSource, canonical
from packages.domain.paths import resolve_data_path
from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.store import Conflict
from packages.domain.context_graph import SourceRef as ContextSourceRef
from packages.domain.conversation_threads import ConversationRef


class NativeFileOpen(RequestModel):
    project_id: Id
    file_source_ref: FileSource
    delivery_operation_id: Id | None = None


class ConversationInputFileOpen(RequestModel):
    project_id: Id | None
    execution_project_id: Id
    conversation_ref: ConversationRef
    operation_id: Id


class NativeReportOpen(RequestModel):
    project_id: Id
    artifact_id: Hash
    sha256: Hash
    download_url: str = Field(min_length=1, max_length=700)


class ContinuityReference(RequestModel):
    project_id: Id
    id: Hash
    revision: int = Field(ge=1, strict=True)
    sha256: Hash


class ObserveContinuity(RequestModel):
    link_ref: ContinuityReference


class ContextFileOpen(RequestModel):
    source: ContextSourceRef


def register_native_open_routes(app, *, authority):
    lock = threading.RLock()
    def host(x_eeas_desktop_host: str | None = Header(None)):
        authority.check_host(x_eeas_desktop_host, require_native=False)

    @app.get('/api/desktop-host/file-contexts', dependencies=[Depends(host)])
    def file_contexts(project_id: Id, limit: int = Query(default=20, ge=1, le=30)):
        return app.state.file_continuity.list_candidates(project_id, limit=limit)

    @app.post('/api/desktop-host/file-contexts/observe', dependencies=[Depends(host)])
    def observe_file_context(body: ObserveContinuity):
        return app.state.file_continuity.observe(body.link_ref.model_dump())

    @app.post('/api/desktop-host/native-open/link', dependencies=[Depends(host)])
    def linked_file(body: ObserveContinuity):
        value = app.state.file_continuity.resolve_open(body.link_ref.model_dump())
        source = value.get('file_source_ref')
        if source is not None:
            selected = app.state.desktop_host.selection_saved_snapshot(body.link_ref.project_id, source)
            if selected.get('current') is not True or selected['source_ref'] != source:
                raise Conflict('Linked file changed before its current version could be opened')
            value['native_path'] = selected['native_path']
        return {'status': 'RESOLVED', 'native_path': value['native_path'],
            'file_link_ref': value['link_ref'], 'filename': value['filename'],
            'file_source_ref': source, 'current_sha256': value['current_sha256'],
            'working_copy_modified': value['working_copy_modified'],
            'active_document_identity_verified': False}

    @app.post('/api/desktop-host/native-open/context-file', dependencies=[Depends(host)])
    def context_file(body: ContextFileOpen):
        value = app.state.context_graph.resolve_original_file(body.source)
        source = value.get('file_source_ref')
        if source is not None:
            selected = app.state.desktop_host.selection_saved_snapshot(body.source.project_id, source)
            if selected.get('current') is not True or selected['source_ref'] != source:
                raise Conflict('Context file saved bytes changed before its original could be opened')
            value['native_path'] = selected['native_path']
        return {'status': 'RESOLVED', 'native_path': value['native_path'],
            'file_link_ref': value['link_ref'], 'filename': value['filename'],
            'file_source_ref': source, 'current_sha256': value['current_sha256'],
            'context_source_ref': value['context_source_ref'], 'context_file_key': value['context_file_key'],
            'active_document_identity_verified': False, 'file_write_authorized': False}

    @app.post('/api/desktop-host/native-open/conversation-file', dependencies=[Depends(host)])
    def conversation_file(body: ConversationInputFileOpen):
        return app.state.canonical_conversations.resolve_input_file(
            body.project_id, body.conversation_ref.model_dump(), body.execution_project_id, body.operation_id)

    @app.post('/api/desktop-host/native-open/file', dependencies=[Depends(host)])
    def file(body: NativeFileOpen):
        ref = body.file_source_ref.model_dump()
        selected = app.state.desktop_host.selection_saved_snapshot(body.project_id, ref)
        if selected['source_ref'] != ref or selected.get('current') is not True:
            raise Conflict('The selected file changed; refresh its exact version before opening')
        if body.delivery_operation_id is not None:
            delivery = app.state.planning_delivery.operation(body.project_id, body.delivery_operation_id)
            if delivery.get('status') not in {'STAGE_REPORT', 'EXPORTED_CONFIRMED_PLAN_DERIVATIVES'}:
                raise Conflict('Only a verified completed delivery can establish generated report provenance')
            artifact = delivery.get('report', {}).get('artifact', {})
            identity = ('kind', 'project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path')
            candidates = [item.get('file_source_ref') or item.get('source_ref')
                for item in delivery.get('project_files', [])]
            generated = [source for source in candidates if isinstance(source, dict)
                and all(source.get(key) == ref[key] for key in identity)
                and source.get('sha256') == artifact.get('sha256')]
            if len(generated) != 1 or not ref['path'].lower().endswith('.docx'):
                raise Conflict('This exact workspace file is not the original report from the given delivery')
            source_kind = 'DISCOVERY' if delivery['status'] == 'STAGE_REPORT' else 'PROPOSAL'
            basis = delivery.get('session_ref' if source_kind == 'DISCOVERY' else 'proposal_ref', {})
            linked = app.state.file_continuity.register_delivery_report(body.project_id, ref, generated[0],
                operation_id=body.delivery_operation_id, artifact_id=artifact['id'],
                source_kind=source_kind, source_id=basis['id'], source_sha256=artifact['sha256'])
        else:
            linked = app.state.file_continuity.register_workspace(body.project_id, ref,
                {'kind': 'USER_OPENED_WORKSPACE_FILE'})
        return {'status': 'RESOLVED', 'native_path': selected['native_path'],
            'file_link_ref': linked['link_ref'], 'filename': linked['filename'],
            'file_source_ref': ref, 'active_document_identity_verified': False}

    @app.post('/api/desktop-host/native-open/report', dependencies=[Depends(host)])
    def report(body: NativeReportOpen):
        revision_match = re.fullmatch(r'/api/projects/([A-Za-z0-9_-]{1,100})/docx-revisions/artifacts/'
            r'([a-f0-9]{64})/download', body.download_url)
        artifact_match = re.fullmatch(r'/api/projects/([A-Za-z0-9_-]{1,100})/report-artifacts/'
            r'([a-f0-9]{64})/download', body.download_url)
        match = re.fullmatch(r'/api/projects/([A-Za-z0-9_-]{1,100})/(planning/proposals|discovery/sessions)/'
            r'([A-Za-z0-9_-]{1,100})/reports/([a-f0-9]{64})/(download|pending/download|pending/json/download|source/download)',
            body.download_url)
        if revision_match:
            if revision_match[1] != body.project_id or revision_match[2] != body.artifact_id:
                raise Conflict('Revision link must match the exact local project and artifact')
            raw, item = app.state.file_continuity.revision_baseline(body.project_id,
                body.artifact_id, body.sha256, body.download_url)
            kind = DOCX_REVISION_PART
        elif artifact_match:
            if artifact_match[1] != body.project_id or artifact_match[2] != body.artifact_id:
                raise Conflict('Artifact link must match the exact local project and artifact')
            raw, item = app.state.file_continuity.artifact_baseline(body.project_id,
                body.artifact_id, body.sha256, body.download_url)
            kind = ARTIFACT_WORKING_COPY_PART
        else:
            if not match or match[1] != body.project_id or match[4] != body.artifact_id:
                raise Conflict('Report link must match the exact local project and artifact')
            kind = {'download': 'docx', 'pending/download': 'pending_markdown',
                'pending/json/download': 'pending_json', 'source/download': 'source_snapshot'}[match[5]]
            source_kind = 'PROPOSAL' if match[2] == 'planning/proposals' else 'DISCOVERY'
            raw, item = app.state.planning_reports.download(body.project_id, match[3], body.artifact_id,
                kind, source_kind=source_kind)
        if hashlib.sha256(raw).hexdigest() != body.sha256 or item['sha256'] != body.sha256:
            raise Conflict('Report card hash differs from the immutable artifact')
        filename = item['filename']
        if Path(filename).name != filename or '\\' in filename:
            raise Conflict('Unsafe artifact filename')
        relative = '/'.join(('native-working-copies', body.project_id, body.artifact_id, kind, filename))
        target = resolve_data_path(relative, app.state.settings.data_dir)
        with lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            created = not target.exists()
            if created:
                receipt = target.with_name(target.name + '.source.json')
                if artifact_match or revision_match:
                    receipt = resolve_data_path(receipt, app.state.settings.data_dir)
                    if receipt.exists():
                        raise Conflict('Unpaired artifact source receipt retained; inspect before creating a copy')
                with target.open('xb') as stream:
                    stream.write(raw)
                source = canonical({'project_id': body.project_id, 'artifact_id': body.artifact_id,
                    'source_sha256': body.sha256, 'kind': kind, 'download_url': body.download_url})
                with receipt.open('x' if artifact_match or revision_match else 'w', encoding='utf-8') as stream:
                    stream.write(source)
            if artifact_match or revision_match:
                try:
                    links = target.stat().st_nlink
                except OSError:
                    raise Conflict('Artifact working copy became unavailable before opening') from None
                if links != 1:
                    raise Conflict('Artifact working copy must not share storage through a hard link')
            current_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if revision_match:
            linked = app.state.file_continuity.register_revision(body.project_id, body.artifact_id,
                filename, body.sha256, body.download_url)
        elif artifact_match:
            linked = app.state.file_continuity.register_artifact(body.project_id, body.artifact_id,
                filename, body.sha256, body.download_url)
        else:
            linked = app.state.file_continuity.register_report(body.project_id, body.artifact_id,
                kind, filename, body.sha256, body.download_url, source_kind, match[3])
        return {'status': 'RESOLVED', 'native_path': str(target), 'working_copy': True,
            'file_link_ref': linked['link_ref'], 'filename': linked['filename'],
            'created': created, 'source_sha256': body.sha256, 'working_copy_sha256': current_sha,
            'working_copy_modified': current_sha != body.sha256, 'immutable_artifact_modified': False,
            'active_document_identity_verified': False}
