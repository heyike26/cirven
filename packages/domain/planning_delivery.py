"""B-chain coordinator over existing intake, original OpenCode and delivery services.

No alternate model loop or manual-candidate shortcut is provided. Draft reports
remain useful before answers; publishing an execution harness requires the exact
current confirmed proposal. All selected-folder outputs are new versioned files.
"""
import os
import json
from pathlib import Path
import re
import uuid
import stat
from datetime import datetime, timezone
from copy import deepcopy
from contextlib import nullcontext, ExitStack
from contextvars import ContextVar
from typing import Literal

from pydantic import Field, model_validator

from packages.domain.discovery_schema import DiscoveryCreate, DiscoveryAdvance, Phase, RevisionRef
from packages.domain.intake import canonical, digest
from packages.domain.local_materials import LocalMaterialsInput
from packages.domain.local_workspaces import FileSource, MAX_FILE_BYTES, relative, sha
from packages.domain.paths import resolve_data_path
from packages.domain.harness_manual import ManualChangesConflict
from packages.domain.planning_reports import PlanningReportInput
from packages.domain.planning_schema import WorkspaceRef, PlanningBudget, HarnessExportInput
from packages.domain.questions import Id, Hash, RequestModel, stamp
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.taskbooks import TaskBookService


class NativeDiscoveryInput(RequestModel):
    message: str = Field(min_length=1, max_length=16000)
    provider_profile_ref: dict
    budget: PlanningBudget = Field(default_factory=PlanningBudget)
    target_phase: Phase | None = None


class DeliveryIntake(RequestModel):
    operation_id: Id
    title: str = Field(min_length=1, max_length=200)
    entry_mode: Literal['PROBLEM', 'REQUIREMENTS', 'TASK_FILE']
    statement: str = Field(min_length=1, max_length=16000)
    workspace_ref: WorkspaceRef
    material_sources: list[FileSource] = Field(default_factory=list, max_length=18)
    task_document_ref: FileSource | None = None
    native_advance: NativeDiscoveryInput | None = None

    @model_validator(mode='after')
    def exact_entry(self):
        refs = [digest(r.model_dump()) for r in self.material_sources]
        if len(set(refs)) != len(refs):
            raise ValueError('Material sources must be distinct exact FileSources')
        if len({(r.workspace_id, r.path) for r in self.material_sources}) != len(self.material_sources):
            raise ValueError('Select one exact version per real material path')
        if self.entry_mode == 'TASK_FILE':
            if self.task_document_ref is None or digest(self.task_document_ref.model_dump()) not in refs:
                raise ValueError('Task-file entry needs its exact document among selected material sources')
        elif self.task_document_ref is not None:
            raise ValueError('Task document reference is specific to TASK_FILE entry')
        return self


class DeliveryStage(RequestModel):
    operation_id: Id
    session_ref: RevisionRef
    template_ref: FileSource | None = None
    experience_ref: RevisionRef | None = None
    graph_ids: list[Id] = Field(default_factory=list, max_length=20)
    workspace_ref: WorkspaceRef | None = None
    directory: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode='after')
    def destination(self):
        if (self.workspace_ref is None) != (self.directory is None):
            raise ValueError('Stage folder export needs both workspace and exact directory')
        if self.directory:
            relative(self.directory)
        if len(set(self.graph_ids)) != len(self.graph_ids):
            raise ValueError('Graph selection must be distinct')
        return self


class DeliveryExport(RequestModel):
    operation_id: Id
    proposal_ref: RevisionRef
    workspace_ref: WorkspaceRef
    directory: str = Field(min_length=1, max_length=300)
    template_ref: FileSource | None = None
    experience_ref: RevisionRef | None = None

    @model_validator(mode='after')
    def destination(self):
        relative(self.directory)
        return self


class DeliveryExportResume(RequestModel):
    request_sha256: Hash


_HELD_EXPORT_LOCK=ContextVar('cirven_exact_delivery_resume_lock',default=None)
MAX_EXPORT_RESUMES=20
MAX_STAGE_HISTORY_DIRECTORIES=256
MAX_STAGE_HISTORY_ENTRIES=64
MAX_STAGE_HISTORY_METADATA_FILES=1024
MAX_STAGE_HISTORY_METADATA_BYTES=8*1024*1024
_KNOWN_EXPORT_FAILURES={'Conflict','InvalidInput','NotFound','ManualChangesConflict','WindowsFileBusy','LocalMaterialBusy'}


class PlanningDelivery:
    def __init__(self, store, workspaces, materials, discovery, proposals, reports, diagrams, exports):
        self.store, self.ws, self.materials = store, workspaces, materials
        self.discovery, self.proposals, self.reports = discovery, proposals, reports
        self.diagrams, self.exports = diagrams, exports
        self.epoch = uuid.uuid4().hex

    def _path(self, pid, op, *parts):
        self.store.project(pid)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', op):
            raise InvalidInput('Unsafe delivery operation identity')
        return resolve_data_path('/'.join(('planning-delivery', pid, digest(op), *parts)), self.ws.data_dir)

    def _read(self, path):
        wrapper = self.ws._json(path)
        payload = wrapper.get('payload')
        if not isinstance(payload, dict) or digest(payload) != wrapper.get('sha256'):
            raise Conflict('Delivery record integrity failed')
        return payload

    def _put(self, path, payload, *, atomic=False):
        record = {'payload': payload, 'sha256': digest(payload)}
        if len((canonical(record) + '\n').encode('utf-8')) > 4 * 1024 * 1024:
            raise InvalidInput('Delivery record exceeds 4 MiB; explicitly split the scope. Original service records are preserved')
        if atomic:self.exports.manual.publish_export(path, record)
        else:self.ws._write(path, record)

    def _claim(self, pid, body, kind):
        folder = self._path(pid, body.operation_id)
        with self.materials._lock(folder / 'lock'):
            claim = folder / 'request.json'
            request = {'kind': kind, 'input': body.model_dump()}
            hashed = digest(request)
            if claim.exists():
                old = self._read(claim)
                if old['request_sha256'] != hashed:
                    raise Conflict('IDEMPOTENCY_CONFLICT: delivery operation binds another request')
                if (folder / 'result.json').exists():
                    return folder, self._present(pid, self._read(folder / 'result.json'))
                if kind == 'INTAKE':
                    return folder, self.operation(pid, body.operation_id)
            else:
                self._put(claim, {**request, 'request_sha256': hashed, 'owner_epoch': self.epoch, 'created_at': stamp()},atomic=kind=='EXPORT')
        return folder, None

    def _step(self, folder, name, value):
        value = {k: v for k, v in value.items() if k not in {'replayed', 'file_checks'}}
        target = folder / (name + '.json')
        if target.exists():
            if self._read(target) != value:
                raise Conflict('Saved delivery step differs from its original result')
        else:
            self._put(target, value,atomic=self._read(folder/'request.json')['kind']=='EXPORT')

    def _finish(self, pid, folder, result):
        self._step(folder, 'result', result)
        return self._present(pid, result)

    def _present(self, pid, saved):
        """Keep the historical receipt immutable and inspect present bytes separately."""
        checks = []
        for item in [*saved.get('project_files', []), *saved.get('harness', {}).get('files', [])]:
            ref = item.get('file_source_ref') or item.get('source_ref')
            try:
                if ref:
                    check = self.ws.check_saved_sources(pid, [FileSource.model_validate(ref)])[0]
                    checks.append({'path': item['path'], **check})
                else:
                    # Older harness files carry an exact path/hash rather than a
                    # FileSource. Inspect only through its selected binding.
                    binding = self.ws.get(pid, saved['harness']['workspace_ref']['id'],
                        saved['harness']['workspace_ref']['revision'])
                    raw = self.ws._saved_raw(binding, item['path'], missing=True)
                    checks.append({'path': item['path'], 'status': 'MISSING' if raw is None else
                        'CURRENT' if sha(raw) == item['sha256'] else 'STALE',
                        'live_sha256': sha(raw) if raw is not None else None})
            except (OSError, Conflict, InvalidInput, NotFound, ValueError, KeyError) as error:
                checks.append({'path': item['path'], 'status': 'UNKNOWN', 'reason': type(error).__name__})
        for item in saved.get('artifacts', []):
            try:
                raw = Path(item['path']).read_bytes()
                checks.append({'path': item['path'], 'status': 'CURRENT' if sha(raw) == item['sha256'] else 'STALE',
                    'live_sha256': sha(raw)})
            except OSError as error:
                checks.append({'path': item['path'], 'status': 'UNKNOWN', 'reason': type(error).__name__})
        basis_current, answer_view_current, template_current = True, True, True
        try:
            if saved.get('proposal_ref'):
                ref = saved['proposal_ref']; view = self.proposals.get(pid, ref['id'], ref['revision'])
                basis_current = view['current_revision'] == ref['revision'] and view['review_status'] == 'CURRENT'
            elif saved.get('session_ref') or saved.get('session', {}).get('payload'):
                ref = saved.get('session_ref') or self._ref(saved['session'])
                view = self.discovery.get(pid, ref['id'], ref['revision'])
                basis_current = view['current_revision'] == ref['revision'] and view['source_current']
            if saved.get('report'):
                answer_view_current = digest(view['answers']) == saved['report']['answer_view_sha256']
                asset = next(a for a in saved['artifacts'] if a['kind'] == 'source_snapshot')
                raw = Path(asset['path']).read_bytes()
                if sha(raw) != asset['sha256']:
                    raise Conflict('Historical report source bytes changed')
                template = json.loads(raw).get('template_ref')
                if template:
                    template_current = self.ws.check_saved_sources(pid, [FileSource.model_validate(template)])[0]['status'] == 'CURRENT'
        except (OSError, Conflict, InvalidInput, NotFound, ValueError, KeyError, StopIteration):
            basis_current = False
        return {**saved, 'historical_receipt_preserved': True, 'file_checks': checks,
            'basis_current': basis_current, 'answer_view_current': answer_view_current,
            'template_source_current': template_current,
            'current_review_status': 'NO_QUALIFIED_RESULT' if saved['status'] in {'FAILED', 'UNKNOWN_OUTCOME'} else 'CHANGED_OUTPUTS' if
                any(c['status'] != 'CURRENT' for c in checks) else 'SOURCE_OR_VERSION_CHANGED' if
                not basis_current else 'TEMPLATE_SOURCE_CHANGED' if not template_current else
                'ANSWER_VIEW_CHANGED' if not answer_view_current else 'CURRENT'}

    def _failed(self, folder, error,*,atomic=False):
        self._put(folder / ('failure-' + uuid.uuid4().hex + '.json'), {
            'status': 'FAILED_OR_PARTIAL', 'reason': type(error).__name__,
            'message': str(error)[:2000], 'created_at': stamp(),
            'old_files_and_steps_preserved': True, 'automatic_model_replay': False},atomic=atomic)

    @staticmethod
    def _ref(value):
        return {k: value[k] for k in ('id', 'revision', 'sha256')}

    def _session(self, pid, ref):
        value = self.discovery.get(pid, ref.id, ref.revision)
        if self._ref(value) != ref.model_dump() or value['current_revision'] != ref.revision:
            raise Conflict('Stage needs the exact inspected current discovery version')
        return value

    async def intake(self, pid, body):
        folder, replay = self._claim(pid, body, 'INTAKE')
        if replay:
            return replay
        try:
            workspace = body.workspace_ref.model_dump()
            if not self.proposals._workspace_current(pid, workspace):
                raise Conflict('Selected main workspace binding changed')
            groups = {}
            for ref in body.material_sources:
                if ref.project_id != pid:
                    raise InvalidInput('Selected material belongs to another project')
                groups.setdefault(ref.workspace_id, []).append(ref)
            registered = []
            for wid, refs in groups.items():
                result = self.materials.materialize(pid, wid, LocalMaterialsInput(
                    operation_id='delivery-materials-' + digest([pid, body.operation_id, wid])[:40],
                    paths=[r.path for r in refs], expected_sources=refs))
                self._step(folder, 'materials-' + wid[:16], result)
                if result['status'] != 'SAVED' or any(c['status'] != 'CURRENT' for c in result['source_checks']):
                    raise Conflict('Selected materials have failures or drift; review before native discovery')
                registered.extend(result['materials'])
            create_op = 'delivery-create-' + digest([pid, body.operation_id])[:40]
            session = self.discovery.create(pid, DiscoveryCreate(
                operation_id=create_op, title=body.title, entry_mode=body.entry_mode,
                statement=body.statement, materials=[m['document_ref'] for m in registered],
                workspace_ref=body.workspace_ref))
            self._step(folder, 'session-created', session)
            if session.get('status') == 'FAILED':
                raise Conflict('Discovery session creation failed; original service failure is retained')
            native_op = None
            if body.native_advance:
                native_op = 'delivery-discovery-' + digest([pid, body.operation_id])[:40]
                session = await self.discovery.advance(pid, session['id'], DiscoveryAdvance(
                    operation_id=native_op, expected_revision=session['revision'],
                    workspace_ref=body.workspace_ref, **body.native_advance.model_dump()))
                self._step(folder, 'native-discovery', session)
            sid = session.get('id')
            status = 'DISCOVERY_DRAFT' if 'payload' in session and body.native_advance else 'AWAITING_NATIVE_DISCOVERY' if 'payload' in session else session.get('status', 'FAILED')
            result = {'schema_version': 'eeas.planning-delivery-intake.v1', 'operation_id': body.operation_id,
                'entry_mode': body.entry_mode, 'status': status, 'materials': registered,
                'task_document_ref': body.task_document_ref.model_dump() if body.task_document_ref else None,
                'session': session, 'native_operation_id': native_op, 'implementation_started': False,
                'execution_authorized': False, 'semantic_verification': 'HUMAN_REVIEW_REQUIRED',
                'resume_urls': {'session': f'/api/projects/{pid}/discovery/sessions/{sid}' if sid else None,
                    'answers': f'/api/projects/{pid}/discovery/sessions/{sid}/answers' if sid else None,
                    'confirm': f'/api/projects/{pid}/discovery/sessions/{sid}/confirm' if sid else None,
                    'stage': f'/api/projects/{pid}/planning-delivery/sessions/{sid}/stage' if sid else None}}
            return self._finish(pid, folder, result)
        except Exception as error:
            self._failed(folder, error)
            raise

    def _report_assets(self, pid, ident, report):
        assets = []
        for kind, key in [('docx', 'artifact'), ('pending_markdown', 'pending'),
                          ('pending_json', 'pending_json'), ('source_snapshot', 'source_snapshot')]:
            raw, item = self.reports.download(pid, ident, report['artifact']['id'], kind,
                source_kind=report['source_kind'])
            path = self.reports._path(pid, ident, 'artifacts', report['artifact']['id'], item['stored_name'])
            assets.append({'kind': kind, **report[key], 'path': str(path), 'raw_bytes': raw})
        return assets

    def _diagrams(self, pid, session_ref, graph_ids):
        artifacts, assets = [], []
        for gid in graph_ids:
            value = self.diagrams.generate(pid, session_ref['id'], gid, session_ref['revision'])
            if value['session_ref'] != session_ref:
                raise Conflict('Diagram differs from the selected discovery source version')
            artifacts.append(value)
            for kind in ('svg', 'png', 'json'):
                item = value['files'][kind]
                path = self.diagrams._path(pid, value['artifact_ref']['id'], item['stored_name'])
                raw = path.read_bytes()
                if sha(raw) != item['sha256']:
                    raise Conflict('Diagram artifact bytes changed')
                assets.append({'kind': 'diagram_' + kind, 'filename': 'diagram-' + gid + '.' + kind,
                    'path': str(path), 'sha256': item['sha256'], 'byte_length': len(raw),
                    'media_type': item['media_type'], 'raw_bytes': raw,
                    'download_url': f'/api/projects/{pid}/discovery/sessions/{session_ref["id"]}/diagrams/{gid}?revision={session_ref["revision"]}&format={kind}'})
        return artifacts, assets

    @staticmethod
    def _public(assets):
        return [{k: v for k, v in item.items() if k != 'raw_bytes'} for item in assets]

    def _destination(self, pid, ref, directory):
        binding = self.ws.get(pid, ref.id, ref.revision)
        if binding['binding_sha256'] != ref.binding_sha256:
            raise Conflict('Delivery output workspace binding changed')
        grant = directory + '/'
        if not any(s == '*' or (s.endswith('/') and grant.startswith(s)) for s in binding['allowed_write_paths']):
            raise InvalidInput('Delivery output directory is outside the explicit selected-folder write grant')
        return binding

    def _new_bytes(self, pid, binding, path, raw):
        """Exclusive creation: an existing/user-edited output is never replaced."""
        relative(path)
        if len(raw) > MAX_FILE_BYTES:
            raise InvalidInput('Delivery file exceeds the exact 16 MiB workspace bound')
        with self.materials._lock(self.ws._data(pid, binding['id'], 'delivery-path-locks', digest(path))):
            existing = self.ws._raw(binding, path, missing=True)
            if existing is not None:
                if sha(existing) != sha(raw):
                    raise Conflict('OUTPUT_CHANGED: existing output bytes differ; user file was not overwritten')
                outcome = 'EXISTING_IDENTICAL'
            else:
                native = self.ws._native_handles()
                if native is None:
                    if os.name != 'posix':
                        raise InvalidInput('Exclusive binary writer is unavailable on this platform')
                    with self.ws._parent(binding, path) as (parent, name):
                        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                        try:
                            with os.fdopen(fd, 'wb', closefd=False) as stream:
                                stream.write(raw); stream.flush(); os.fsync(fd)
                            os.fsync(parent)
                        finally:
                            os.close(fd)
                else:
                    # Reuse the existing verified relative-handle chain and Win32
                    # CREATE_NEW. This branch still requires native Windows qualification.
                    from packages.adapters.windows_workspace_handles import CREATE_NEW, GENERIC_WRITE, READ_CONTROL
                    with native.open_parent(binding, path, private_check=self.ws._private, _writable=True) as (parent, name):
                        handle = native.api.open_relative(parent.native_handle, name, GENERIC_WRITE | READ_CONTROL | 0x80, 0, CREATE_NEW, through=True)
                        try:
                            native._check(handle, __import__('ntpath').join(parent.path, name), directory=False)
                            offset = 0
                            while offset < len(raw):
                                written = native.api.write(handle, raw[offset:])
                                if not 0 < written <= len(raw) - offset:
                                    raise Conflict('Exclusive binary write made no bounded progress')
                                offset += written
                            native.api.flush(handle)
                        finally:
                            native.api.close(handle)
                outcome = 'WRITTEN'
            actual = self.ws._raw(binding, path)
            if actual != raw:
                raise Conflict('Delivery output changed before byte verification; no completion manifest is qualified')
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                version = self.ws._observe(binding, path, actual, origin='PLANNING_DELIVERY_EXPORT')
            return {'path': path, 'native_path': str(Path(binding['root']) / path), 'sha256': sha(actual),
                'byte_length': len(actual), 'file_source_ref': self.ws._ref(version), 'outcome': outcome}

    def _copy_assets(self, pid, binding, directory, assets, *, names=None):
        self.exports._directories(binding, directory)
        files = []
        for index, item in enumerate(assets):
            name = names[index] if names else item['filename']
            files.append(self._new_bytes(pid, binding, directory + '/' + name, item['raw_bytes']))
        return files

    def stage(self, pid, sid, body):
        if sid != body.session_ref.id:
            raise InvalidInput('Stage route differs from exact session reference')
        folder, replay = self._claim(pid, body, 'STAGE')
        if replay:
            return replay
        with self.materials._lock(folder / 'stage-lock'):
            if (folder / 'result.json').exists():
                return self._present(pid, self._read(folder / 'result.json'))
            try:
                session = self._session(pid, body.session_ref)
                graph_ids = body.graph_ids or [g['id'] for g in session['payload']['candidate']['graphs']]
                graphs, graph_assets = self._diagrams(pid, body.session_ref.model_dump(), graph_ids)
                report = self.reports.generate_discovery(pid, sid, PlanningReportInput(
                    operation_id='delivery-stage-' + digest([pid, body.operation_id])[:40],
                    expected_revision=body.session_ref.revision, template_ref=body.template_ref,
                    experience_ref=body.experience_ref, diagram_refs=[g['artifact_ref'] for g in graphs]))
                self._step(folder, 'report', report)
                assets = self._report_assets(pid, sid, report) + graph_assets
                files = []
                if body.workspace_ref:
                    binding = self._destination(pid, body.workspace_ref, body.directory)
                    directory = body.directory + '/stages/' + sid + '/r' + str(session['revision']) + '-' + report['artifact']['id'][:16]
                    files = self._copy_assets(pid, binding, directory, assets)
                result = {'schema_version': 'eeas.planning-stage-delivery.v1', 'operation_id': body.operation_id,
                    'status': 'STAGE_REPORT', 'session_ref': body.session_ref.model_dump(),
                    'source_current_at_build': session['source_current'], 'report': report, 'diagrams': graphs,
                    'pending_count': session['readiness']['pending_count'], 'blocking_count': session['readiness']['blocking_count'],
                    'artifacts': self._public(assets), 'project_files': files,
                    'requires_answers_before_stage_report': False, 'silence_is_approval': False,
                    'execution_authorized': False, 'docx_visual_validation': 'NOT_RUN',
                    'native_word_validation': 'NOT_RUN', 'semantic_verification': 'HUMAN_REVIEW_REQUIRED'}
                return self._finish(pid, folder, result)
            except Exception as error:
                self._failed(folder, error)
                raise

    def export(self, pid, ident, body):
        self._owns_export_lock(pid,body)  # Validate an internal capability even on completed replay.
        if ident != body.proposal_ref.id:
            raise InvalidInput('Export route differs from exact proposal reference')
        original=self._path(pid,body.operation_id)
        if (original/'request.json').exists() and (original/'result.json').exists():
            claim=self._read(original/'request.json')
            if claim['request_sha256']!=digest({'kind':'EXPORT','input':body.model_dump()}):
                raise Conflict('IDEMPOTENCY_CONFLICT: delivery operation binds another request')
            return self._present(pid,self._read(original/'result.json'))
        self._check_export_resume_route(pid,body,original)
        budget=self.exports.manual.read_budget()
        with self.exports.manual.proof_scope(pid,body.workspace_ref.model_dump(),budget):
            return self._export(pid,ident,body,read_budget=budget)

    def _export(self, pid, ident, body, *, read_budget):
        from packages.domain.planning_coverage import require_clear as require_claim_coverage
        if ident != body.proposal_ref.id:
            raise InvalidInput('Export route differs from exact proposal reference')
        # Replay a completed original operation before the new manual gate. A
        # read-only peek must not claim an op or create an output/artifact folder.
        original=self._path(pid,body.operation_id)
        harness_body=HarnessExportInput(operation_id='delivery-harness-' + digest([pid,body.operation_id])[:40],
            expected_revision=body.proposal_ref.revision,proposal_sha256=body.proposal_ref.sha256,
            workspace_ref=body.workspace_ref,directory=body.directory)
        own_pending=None
        if (original/'request.json').exists():
            claim=self._read(original/'request.json')
            expected=digest({'kind':'EXPORT','input':body.model_dump()})
            if claim['request_sha256']!=expected:
                raise Conflict('IDEMPOTENCY_CONFLICT: delivery operation binds another request')
            if (original/'result.json').exists():return self._present(pid,self._read(original/'result.json'))
            # Only the exact verified original outer request can present this
            # derived inner capability. _exports still validates its claim.
            own_pending={'id':digest(harness_body.operation_id),'request_sha256':digest(
                {'proposal_id':ident,**harness_body.model_dump(exclude={'operation_id'})})}
        elif (original/'result.json').exists():raise Conflict('Delivery result has no original request')
        proposal=self.proposals.get(pid,ident,body.proposal_ref.revision)
        require_claim_coverage(proposal)
        if (self._ref(proposal)!=body.proposal_ref.model_dump() or proposal['current_revision']!=proposal['revision'] or
                proposal['payload']['status']!='CONFIRMED' or proposal['review_status']!='CURRENT'):
            raise Conflict('Complete delivery needs the exact current confirmed proposal and unchanged sources')
        self._destination(pid,body.workspace_ref,body.directory)
        try:
            self.exports.manual.require_clear(pid,ident,body.workspace_ref.model_dump(),target=body.proposal_ref.model_dump(),read_budget=read_budget,_active_export=own_pending)
        except ManualChangesConflict as error:
            # Only this exact, unclaimed request can be removed from a newly
            # prepared UI index. Later gates may already have partial outputs.
            if not (original/'request.json').exists() and not (original/'result.json').exists():
                error.detail['delivery_attempt']={'phase':'PRECLAIM','operation_id':body.operation_id,
                    'project_id':pid,'proposal_ref':body.proposal_ref.model_dump(),
                    'workspace_ref':body.workspace_ref.model_dump()}
            raise
        folder, replay = self._claim(pid, body, 'EXPORT')
        if replay:
            return replay
        lock=nullcontext() if self._owns_export_lock(pid,body) else self.materials._lock(folder/'export-lock')
        with lock:
            if (folder / 'result.json').exists():
                return self._present(pid, self._read(folder / 'result.json'))
            self._check_export_resume_route(pid,body,folder)
            try:
                proposal = self.proposals.get(pid, ident, body.proposal_ref.revision)
                require_claim_coverage(proposal)
                if (self._ref(proposal) != body.proposal_ref.model_dump() or proposal['current_revision'] != proposal['revision'] or
                        proposal['payload']['status'] != 'CONFIRMED' or proposal['review_status'] != 'CURRENT'):
                    raise Conflict('Complete delivery needs the exact current confirmed proposal and unchanged sources')
                binding = self._destination(pid, body.workspace_ref, body.directory)
                self.exports.manual.require_clear(pid,ident,body.workspace_ref.model_dump(),target=body.proposal_ref.model_dump(),read_budget=read_budget,_active_export=own_pending)
                discovery_ref = proposal['payload'].get('discovery_ref')
                graphs, graph_assets = [], []
                if discovery_ref:
                    session = self.discovery.get(pid, discovery_ref['id'], discovery_ref['revision'])
                    if self._ref(session) != discovery_ref:
                        raise Conflict('Confirmed proposal requirements source differs from immutable discovery basis')
                    graphs, graph_assets = self._diagrams(pid, discovery_ref, session['payload']['accepted_graph_ids'])
                report = self.reports.generate(pid, ident, PlanningReportInput(
                    operation_id='delivery-report-' + digest([pid, body.operation_id])[:40],
                    expected_revision=proposal['revision'], template_ref=body.template_ref,
                    experience_ref=body.experience_ref, diagram_refs=[g['artifact_ref'] for g in graphs]))
                self._step(folder, 'report', report)
                book = self.proposals.taskbook(pid, ident, proposal['revision'])
                if 'taskbook_id' not in book:
                    raise Conflict('Taskbook capacity requires explicit split; the complete network remains preserved')
                taskbook = TaskBookService(self.store).get(pid, book['taskbook_id'], book['revision'])
                taskgraph = {'schema_version': 'eeas.interactive-task-graph.v1', 'proposal_ref': body.proposal_ref.model_dump(),
                    'discovery_ref': discovery_ref, 'candidate_sha256': digest(proposal['payload']['candidate']),
                    'source_receipt_sha256': proposal['payload']['source_receipt']['sha256'],
                    'nodes': proposal['flow']['nodes'], 'edges': proposal['flow']['edges'],
                    'edit_url': f'/api/projects/{pid}/planning/proposals/{ident}',
                    'execution_authorized': False, 'semantic_verification': 'HUMAN_REVIEW_REQUIRED'}
                if proposal.get('coverage') is not None:
                    taskgraph.update(requirement_coverage_ref={'filename':'accepted-items.json','snapshot_sha256':proposal['coverage']['snapshot_sha256']},
                        coverage_sha256=proposal['coverage']['snapshot_sha256'])
                harness = self.exports.export(pid, ident, harness_body,read_budget=read_budget)
                # Lift existing MD-export descriptors into genuine observed
                # FileSources for the common native artifact-open broker.
                observed = []
                for item in harness['files']:
                    value = self.ws.read_saved_snapshot(pid, binding['id'], item['path'])
                    if value['source_ref']['sha256'] != item['sha256'] or not value['current']:
                        raise Conflict('Harness bytes changed before source binding')
                    observed.append({**item, 'file_source_ref': value['source_ref'],
                        'native_path': str(Path(binding['root']) / item['path'])})
                harness = {**harness, 'files': observed}
                self._step(folder, 'harness', harness)
                assets = self._report_assets(pid, ident, report) + graph_assets
                tag = report['artifact']['id'][:16]
                names = ['taskbook-' + tag + '.docx', 'report-pending-' + tag + '.md',
                    'report-pending-' + tag + '.json', 'report-source-' + tag + '.json']
                names += [item['filename'] for item in graph_assets]
                files = self._copy_assets(pid, binding, harness['version_directory'], assets, names=names)
                for name, value in [('interactive-task-graph.json', taskgraph), ('taskbook-source.json', taskbook)]:
                    files.append(self._new_bytes(pid, binding, harness['version_directory'] + '/' + name, (canonical(value) + '\n').encode()))
                # A full-delivery marker is distinct from the existing MD-harness
                # manifest and is committed only after every artifact is checked.
                if not self.proposals._current(pid, proposal['payload']):
                    raise Conflict('Sources changed during delivery; partial outputs remain uncommitted')
                self.reports._check_template(pid, body.template_ref)
                self.exports.manual.require_clear(pid,ident,body.workspace_ref.model_dump(),target=body.proposal_ref.model_dump(),read_budget=read_budget,_active_export=own_pending)
                for item in [*harness['files'], *files]:
                    if sha(self.ws._raw(binding, item['path'])) != item['sha256']:
                        raise Conflict('A project delivery file changed; full-delivery manifest is not qualified')
                manifest = {'schema_version': 'eeas.complete-planning-delivery.v1',
                    'proposal_ref': body.proposal_ref.model_dump(), 'discovery_ref': discovery_ref,
                    'source_receipt_sha256': proposal['payload']['source_receipt']['sha256'],
                    'candidate_sha256': taskgraph['candidate_sha256'], 'report': report,
                    'taskbook_ref': book, 'taskbook_status': 'DRAFT_DERIVATIVE_OF_CONFIRMED_PLAN',
                    'diagrams': graphs, 'harness_files': harness['files'], 'additional_files': files,
                    'execution_authorized': False, 'native_word_validation': 'NOT_RUN',
                    'docx_visual_validation': 'NOT_RUN', 'semantic_verification': 'HUMAN_REVIEW_REQUIRED'}
                if proposal.get('coverage') is not None:manifest['coverage_sha256']=proposal['coverage']['snapshot_sha256']
                marker = self._new_bytes(pid, binding, harness['version_directory'] + '/delivery-manifest-' + tag + '.json',
                    (canonical(manifest) + '\n').encode())
                result = {'schema_version': 'eeas.planning-delivery-result.v1', 'operation_id': body.operation_id,
                    'status': 'EXPORTED_CONFIRMED_PLAN_DERIVATIVES', 'proposal_ref': body.proposal_ref.model_dump(),
                    'discovery_ref': discovery_ref, 'version_directory': harness['version_directory'],
                    'report': report, 'taskbook_ref': book, 'taskgraph': taskgraph,
                    'harness': harness, 'diagrams': graphs, 'artifacts': self._public(assets),
                    'project_files': [*files, marker], 'manifest': marker,
                    'execution_started': False, 'execution_authorized': False,
                    'semantic_verification': 'HUMAN_REVIEW_REQUIRED'}
                if proposal.get('coverage') is not None:result['coverage_sha256']=proposal['coverage']['snapshot_sha256']
                if read_budget['charged_bytes'] or read_budget['metadata']['charged_bytes']:result['manual_read_receipt']=deepcopy(read_budget)
                return self._finish(pid, folder, result)
            except Exception as error:
                try:self._failed(folder,error,atomic=True)
                except Exception:pass  # A failed diagnostic write cannot replace the original export failure.
                raise

    def _check_export_resume_route(self,pid,body,folder):
        # Recheck both before preflight and after actually acquiring export-lock.
        # A legacy waiter must not bypass a continuation started while it waited.
        if not self._owns_export_lock(pid,body) and resolve_data_path(folder/'resume-attempts',self.ws.data_dir).exists():
            raise Conflict('The original delivery has an explicit continuation audit; read its receipt or use its verified continuation action')

    def _saved_export(self, op, claim, expected_sha=None):
        if claim.get('kind')!='EXPORT':raise Conflict('Only the exact original failed EXPORT can continue')
        try:body=DeliveryExport.model_validate(claim.get('input'))
        except (ValueError,TypeError):raise Conflict('The saved original export body cannot be verified')from None
        hashed=digest({'kind':'EXPORT','input':body.model_dump()})
        if (body.operation_id!=op or claim.get('request_sha256')!=hashed or
                expected_sha is not None and expected_sha!=hashed):
            raise Conflict('IDEMPOTENCY_CONFLICT: continuation does not match the complete original request')
        return body,hashed

    def _owns_export_lock(self,pid,body):
        held=_HELD_EXPORT_LOCK.get()
        if held is None:return False
        key=(self,pid,body.operation_id,digest({'kind':'EXPORT','input':body.model_dump()}))
        if held!=key:raise Conflict('Original export lock capability belongs to another exact request')
        return True

    def _resume_attempts(self,folder,op,hashed):
        root=resolve_data_path(folder/'resume-attempts',self.ws.data_dir)
        if not root.exists():return []
        entries=sorted(root.iterdir())
        if len(entries)>MAX_EXPORT_RESUMES:raise Conflict('Continuation audit capacity exceeded')
        found=[]
        for number,path in enumerate(entries,1):
            if path.is_symlink() or not path.is_dir() or path.name!=f'{number:04d}':
                raise Conflict('Continuation audit sequence is invalid')
            request_path=resolve_data_path(path/'request.json',self.ws.data_dir)
            result_path=resolve_data_path(path/'result.json',self.ws.data_dir)
            if not request_path.exists():return [*found,{'status':'INTERRUPTED_REVIEW_REQUIRED'}]
            request=self._read(request_path)
            if (request.get('schema')!='cirven.delivery-export-resume.v1' or request.get('attempt')!=number or
                    request.get('operation_id')!=op or request.get('request_sha256')!=hashed):
                raise Conflict('Continuation audit does not match the original operation')
            if not result_path.exists():
                if number!=len(entries):raise Conflict('An unknown continuation precedes another attempt')
                found.append({'status':'INTERRUPTED_REVIEW_REQUIRED'});continue
            result=self._read(result_path)
            if (result.get('attempt')!=number or result.get('operation_id')!=op or result.get('request_sha256')!=hashed or
                    result.get('status')not in {'FAILED_OR_PARTIAL','COMPLETED','INTERRUPTED_REVIEW_REQUIRED'}):
                raise Conflict('Continuation result integrity differs')
            found.append(result)
        return found

    def _resume_eligibility(self,folder,op,claim):
        body,hashed=self._saved_export(op,claim)
        failures=[self._read(path)for path in folder.glob('failure-*.json')]
        if not failures or any(row.get('status')!='FAILED_OR_PARTIAL' or row.get('automatic_model_replay')is not False or
                row.get('reason')not in _KNOWN_EXPORT_FAILURES or str(row.get('message','')).startswith(('UNKNOWN:','UNKNOWN_OUTCOME:')) for row in failures):
            raise Conflict('The original export is not a definite failed or partial operation')
        attempts=self._resume_attempts(folder,op,hashed)
        if attempts and attempts[-1]['status']!='FAILED_OR_PARTIAL':
            raise Conflict('The latest continuation is unknown or completed; inspect without replay')
        if len(attempts)>=MAX_EXPORT_RESUMES:raise Conflict('Explicit continuation capacity reached; preserve the original operation')
        return body,hashed,len(attempts)

    def resume_export(self,pid,op,body):
        folder=self._path(pid,op)
        if not(folder/'request.json').exists():raise NotFound('Original delivery operation does not exist')
        self._saved_export(op,self._read(folder/'request.json'),body.request_sha256)
        if(folder/'result.json').exists():raise Conflict('The original delivery already has a result; read it without replay')
        # No creation, waiting, alternate lock or implicit thread reentrancy.
        with ExitStack() as lifetime:
            try:lifetime.enter_context(self.materials._lock(folder/'export-lock',blocking=False,create=False))
            except (NotFound,OSError):raise Conflict('The original export lock cannot be verified; inspect without replay')from None
            if(folder/'result.json').exists():raise Conflict('The original delivery already has a result; read it without replay')
            claim=self._read(folder/'request.json')
            original,hashed,count=self._resume_eligibility(folder,op,claim)
            self._saved_export(op,claim,body.request_sha256)
            attempt=count+1;target=resolve_data_path(folder/'resume-attempts'/f'{attempt:04d}',self.ws.data_dir)
            target.mkdir(parents=True,exist_ok=False)
            binding={'schema':'cirven.delivery-export-resume.v1','attempt':attempt,'operation_id':op,
                'request_sha256':hashed,'owner_epoch':self.epoch,'created_at':stamp()}
            self._put(target/'request.json',binding,atomic=True)
            token=_HELD_EXPORT_LOCK.set((self,pid,op,hashed))
            try:
                value=self.export(pid,original.proposal_ref.id,original)
            except Exception as error:
                known=isinstance(error,(Conflict,InvalidInput,NotFound)) and getattr(error,'code',None)not in {'UNKNOWN','UNKNOWN_OUTCOME'} and not str(error).startswith(('UNKNOWN:','UNKNOWN_OUTCOME:'))
                status='FAILED_OR_PARTIAL' if known else'INTERRUPTED_REVIEW_REQUIRED'
                try:self._put(target/'result.json',{'attempt':attempt,'operation_id':op,'request_sha256':hashed,
                    'status':status,'reason':type(error).__name__,'created_at':stamp()},atomic=True)
                except Exception:pass  # Failed audit writes leave an unknown attempt; preserve the original error.
                raise
            else:
                self._put(target/'result.json',{'attempt':attempt,'operation_id':op,'request_sha256':hashed,
                    'status':'COMPLETED','created_at':stamp()},atomic=True)
                return value
            finally:_HELD_EXPORT_LOCK.reset(token)

    def _stage_request(self, pid, op, claim):
        """Validate an original stage claim, including its immutable source scope."""
        try:
            data = claim['input']
            body = DeliveryStage.model_validate(data)
            if (claim['kind'] != 'STAGE' or body.operation_id != op or
                    digest({'kind': 'STAGE', 'input': data}) != claim['request_sha256']):
                raise ValueError
            with self.store.connect() as connection:
                source = self.discovery._version(connection, pid, body.session_ref.id,
                                                 body.session_ref.revision)
            if self._ref(source) != body.session_ref.model_dump():
                raise ValueError
        except (ValueError, KeyError, TypeError, NotFound):
            raise Conflict('Original stage request identity or source is invalid') from None
        return body

    def session_operations(self, pid, sid, revision, sha256):
        """Discover original STAGE keys without hashing outputs or replaying work.

        The request pins the current conversation context. Entries retain their
        own immutable revision; current file checks belong to operation(), once
        the client chooses/recovers that original key. No index is rebuilt here.
        """
        try:
            expected = RevisionRef(id=sid, revision=revision, sha256=sha256).model_dump()
        except ValueError:
            raise InvalidInput('Invalid stage history context') from None

        def session(ref=None):
            with self.store.connect() as connection:
                value = self.discovery._version(connection, pid, sid if ref is None else ref['id'],
                                                None if ref is None else ref['revision'])
            return self._ref(value)

        if session() != expected:
            raise Conflict('Stage history context changed; read the current conversation')
        self.store.project(pid)
        root = resolve_data_path('/'.join(('planning-delivery', pid)), self.ws.data_dir)
        budget = {'files': 0, 'bytes': 0}

        def record(path):
            try:
                checked = resolve_data_path(str(path), self.ws.data_dir)
                info = checked.lstat()
                if (not stat.S_ISREG(info.st_mode) or
                        getattr(info, 'st_file_attributes', 0) & 0x400):
                    raise Conflict('Stage history record is not an ordinary file')
                remaining = MAX_STAGE_HISTORY_METADATA_BYTES - budget['bytes']
                bound = min(4 * 1024 * 1024, remaining)
                identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                          value.st_mtime_ns, value.st_ctime_ns)
                with checked.open('rb') as stream:
                    before = os.fstat(stream.fileno())
                    if identity(info) != identity(before):
                        raise Conflict('Stage history record changed before its read')
                    raw = stream.read(bound + 1)
                    after = os.fstat(stream.fileno())
                budget['bytes'] += len(raw)
                if len(raw) > bound:
                    raise Conflict('Stage history metadata byte capacity exceeded')
                if (identity(before) != identity(after) or len(raw) != after.st_size or
                        identity(after) != identity(checked.lstat())):
                    raise Conflict('Stage history record changed during its read')
                wrapper = json.loads(raw)
                payload = wrapper.get('payload') if isinstance(wrapper, dict) else None
                if not isinstance(payload, dict) or digest(payload) != wrapper.get('sha256'):
                    raise Conflict('Stage history record integrity failed')
                return payload
            except ValueError:
                raise Conflict('Stage history record integrity failed') from None
            except (OSError, InvalidInput):
                raise Conflict('Stage history ledger cannot be read safely') from None

        folders = []
        if root.exists():
            with os.scandir(root) as directory:
                for item in directory:
                    if len(folders) >= MAX_STAGE_HISTORY_DIRECTORIES:
                        raise Conflict('Stage history project directory capacity exceeded')
                    info = item.stat(follow_symlinks=False)
                    if (not stat.S_ISDIR(info.st_mode) or item.is_symlink() or
                            getattr(info, 'st_file_attributes', 0) & 0x400 or
                            not re.fullmatch('[a-f0-9]{64}', item.name)):
                        raise Conflict('Stage history ledger contains an unsafe directory')
                    folders.append(root / item.name)

        entries = []
        for folder in sorted(folders):
            budget['files'] += 1
            if budget['files'] > MAX_STAGE_HISTORY_METADATA_FILES:
                raise Conflict('Stage history metadata file capacity exceeded')
            claim = record(folder / 'request.json')
            data = claim.get('input')
            kind, key = claim.get('kind'), data.get('operation_id') if isinstance(data, dict) else None
            if (kind not in {'STAGE', 'EXPORT', 'INTAKE'} or not isinstance(key, str) or
                    not re.fullmatch('[A-Za-z0-9_-]{1,100}', key) or folder.name != digest(key) or
                    digest({'kind': kind, 'input': data}) != claim.get('request_sha256')):
                raise Conflict('Stage history request does not match its original identity')
            if kind != 'STAGE':
                continue
            original = self._stage_request(pid, key, claim).session_ref.model_dump()
            try:
                created = claim['created_at']
                if not isinstance(created, str) or len(created) > 64:
                    raise ValueError
                moment = datetime.fromisoformat(created.replace('Z', '+00:00'))
                if moment.tzinfo is None or not isinstance(claim.get('owner_epoch'), str):
                    raise ValueError
            except (ValueError, KeyError, NotFound):
                raise Conflict('Stage history request has an invalid source or timestamp') from None
            if original['id'] != sid:
                continue
            if len(entries) >= MAX_STAGE_HISTORY_ENTRIES:
                raise Conflict('Stage history session entry capacity exceeded')
            failure = False
            with os.scandir(folder) as directory:
                for item in directory:
                    # Bound enumeration as well as bytes, including names not read.
                    budget['files'] += 1
                    if budget['files'] > MAX_STAGE_HISTORY_METADATA_FILES:
                        raise Conflict('Stage history metadata file capacity exceeded')
                    if item.name.startswith('failure-') and item.name.endswith('.json'):
                        record(folder / item.name)
                        failure = True
            result_path = folder / 'result.json'
            if os.path.lexists(result_path):
                saved = record(result_path)
                status = saved.get('status')
                if (saved.get('operation_id') != key or
                        saved.get('session_ref') != original or
                        saved.get('project_id', pid) != pid or
                        not isinstance(status, str) or not 1 <= len(status) <= 100 or
                        (status == 'STAGE_REPORT' and
                         saved.get('schema_version') != 'eeas.planning-stage-delivery.v1')):
                    raise Conflict('Stage history result differs from its original request')
            else:
                status = ('FAILED_OR_PARTIAL' if failure else 'PROCESSING' if
                          claim['owner_epoch'] == self.epoch else 'INTERRUPTED_REVIEW_REQUIRED')
            entries.append((moment.astimezone(timezone.utc), {
                'operation_id': key, 'session_ref': original, 'created_at': created,
                'status': status, 'basis_current': None, 'current_review_status': None}))
        if session() != expected:
            raise Conflict('Stage history context changed during the read')
        return {'project_id': pid, 'session_ref': expected,
                'entries': [row for _, row in sorted(entries, key=lambda item:
                    (item[0], item[1]['operation_id']))], 'complete': True}

    def operation(self, pid, op):
        folder = self._path(pid, op)
        if not (folder / 'request.json').exists():
            raise NotFound('Planning delivery operation does not exist in this project')
        claim = self._read(folder / 'request.json')
        if (folder / 'result.json').exists():
            return self._present(pid, self._read(folder / 'result.json'))
        original_stage = self._stage_request(pid, op, claim) if claim['kind'] == 'STAGE' else None
        steps = {p.stem: self._read(p) for p in folder.glob('*.json')
            if p.name != 'request.json' and not p.name.startswith('failure-')}
        failures = [self._read(p) for p in sorted(folder.glob('failure-*.json'))]
        status='FAILED_OR_PARTIAL' if failures else 'PROCESSING' if claim['owner_epoch']==self.epoch else'INTERRUPTED_REVIEW_REQUIRED'
        value={'operation_id':op,'status':status,'kind':claim['kind'],'steps':steps,'failures':failures,'automatic_model_replay':False}
        if original_stage is not None:
            value.update(project_id=pid, session_ref=original_stage.session_ref.model_dump())
        if claim['kind']=='EXPORT':
            # This is a read-only observation. Missing locks are never created.
            try:
                original,hashed=self._saved_export(op,claim)
                value.update(project_id=pid,proposal_ref=original.proposal_ref.model_dump(),request_sha256=hashed)
                with self.materials._lock(folder/'export-lock',blocking=False,create=False):
                    if(folder/'result.json').exists():return self._present(pid,self._read(folder/'result.json'))
                    if failures:
                        self._resume_eligibility(folder,op,self._read(folder/'request.json'))
                        value['export_resume']={'action':'RESUME_EXPORT','operation_id':op,'project_id':pid,
                            'request_sha256':hashed,'proposal_ref':original.proposal_ref.model_dump()}
            except Conflict as error:
                value['status']='PROCESSING' if getattr(error,'code',None)=='BUSY' else'INTERRUPTED_REVIEW_REQUIRED'
            except (InvalidInput,NotFound,OSError,ValueError,TypeError,KeyError):
                value['status']='INTERRUPTED_REVIEW_REQUIRED'
        return value
