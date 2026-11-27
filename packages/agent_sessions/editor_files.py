"""Explicit file existence changes and manual handoff recovery.

Native text WorkspaceEdit is a different path. Existence changes use the
original Windows selected-folder handle adapter, with durable intent before
the OS call, exact immutable preimages and an explicit version-checked Undo.
No file bytes or duplicate transcript are stored in these metadata receipts.
"""
import hashlib
import os
import threading
from typing import Literal

from pydantic import Field

from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import digest, now
from packages.domain.local_workspaces import relative
from packages.domain.context_read_budget import current_context_read_budget
from packages.adapters.windows_workspace_handles import validate_file_metadata, file_metadata_sha256
from .models import SessionRef
from .diff_reviews import DiffPreparation, DiffReference
from .editor_changes import EditorBasis, editor_location, text_hash


class EditorFileAddress(RequestModel):
    session_ref: SessionRef
    generation: int = Field(ge=1, strict=True)
    target_operation_id: Id
    lease_ref: DiffReference
    editor_client_id: Id
    editor_window_id: Id
    editor_nonce: Hash
    expected_revision: int = Field(ge=1, le=64, strict=True)
    action: Literal['COMMIT', 'UNDO', 'CONFIRM', 'MANUAL_RESOLVE']


class EditorFileObservation(RequestModel):
    documents: list[EditorBasis] = Field(max_length=16)
    native_commands_settled: bool = Field(strict=True)


class EditorFileCommand(EditorFileAddress):
    operation_id: Id
    preparation_sha256: Hash
    editor_observation: EditorFileObservation
    user_approved: bool = Field(strict=True)


class EditorFiles:
    def __init__(self, owner):
        self.owner = owner
        self.editor = owner.editor_changes
        self.inflight = {}; self.lock = threading.RLock()

    def _mutation_adapter(self):
        return self.owner.workspaces._native_handles()

    def _metadata_adapter(self):
        return self.owner.workspaces._native_handles()

    def _record_material(self, pid, sid, value, body, purpose, captured):
        material = validate_file_metadata(captured['metadata'])
        if file_metadata_sha256(material) != captured['sha256']:
            raise Conflict('Original NTFS recovery metadata digest differs')
        identity = captured['identity']
        if (not isinstance(identity, dict) or set(identity) != {'device', 'inode'}
                or any(not isinstance(field, str) or not field.isdecimal() or len(field)>32 for field in identity.values())):
            raise Conflict('Original NTFS recovery identity is invalid')
        ident = digest({'handoff': value['id'], 'operation_id': body.operation_id, 'purpose': purpose})
        record = {'schema': 'eeas.editor-file-metadata.v1', 'id': ident, 'purpose': purpose,
            'project_id': pid, 'session_ref': body.session_ref.model_dump(), 'generation': body.generation,
            'handoff_operation_id': value['operation_id'], 'action_operation_id': body.operation_id,
            'request_sha256': digest(body.model_dump()), 'lease_ref': self.editor._lease_ref(value),
            'metadata': material, 'metadata_sha256': captured['sha256'], 'identity': identity}
        from packages.domain.tasks import canonical
        if len(canonical(record).encode()) > 16384: raise Conflict('Original NTFS metadata recovery material exceeds its bound')
        path = self.owner._path(pid, sid, 'editor-file-materials', ident+'.json')
        self.owner._write(path, record)
        # The owned immutable file must be readable with its full proof before
        # the adapter is allowed to issue the original delete disposition.
        reference = {'id': ident, 'sha256': digest(record)}
        self._material(pid, sid, value, reference)
        return reference

    def _material(self, pid, sid, value, reference):
        from packages.adapters.opencode_change_review import _regular
        import json
        if (not isinstance(reference, dict) or set(reference) != {'id', 'sha256'}
                or any(not isinstance(field, str) or len(field)!=64 or any(c not in '0123456789abcdef' for c in field)
                    for field in reference.values())):
            raise Conflict('An exact original metadata recovery reference is required')
        path = self.owner._path(pid, sid, 'editor-file-materials', reference['id']+'.json')
        wrapper = json.loads(_regular(path, 17408)); record = wrapper.get('value')
        if (not isinstance(record, dict) or set(wrapper) != {'value', 'sha256'} or wrapper['sha256'] != reference['sha256']
                or digest(record) != reference['sha256'] or record.get('schema') != 'eeas.editor-file-metadata.v1'
                or record.get('id') != reference['id'] or record.get('project_id') != pid
                or record.get('session_ref') != value['request']['session_ref'] or record.get('generation') != value['generation']
                or record.get('handoff_operation_id') != value['operation_id'] or record.get('lease_ref') != self.editor._lease_ref(value)):
            raise Conflict('Original metadata recovery material integrity or owner differs')
        validate_file_metadata(record['metadata'])
        action = self.owner.operation(pid, sid, record['action_operation_id'])
        if (action.get('kind') != 'EDITOR_FILE_ACTION' or action.get('request_sha256') != record['request_sha256']
                or action.get('request', {}).get('target_operation_id') != value['operation_id']):
            raise Conflict('Original metadata recovery action request differs')
        if file_metadata_sha256(record['metadata']) != record['metadata_sha256']:
            raise Conflict('Original metadata recovery payload digest differs')
        return record

    def _value(self, pid, sid, body, db=None):
        self.owner._manifest(pid, sid, body.session_ref.model_dump())
        if db is None:
            with self.owner.store.connect() as connection:
                return self._value(pid, sid, body, connection)
        value = self.editor._decode(self.editor._row(db, pid, sid, body.target_operation_id))
        if (value['revision'] != body.expected_revision or self.editor._lease_ref(value) != body.lease_ref.model_dump()
                or value['generation'] != body.generation or value['request']['session_ref'] != body.session_ref.model_dump()
                or any(value['request'][key] != getattr(body, key) for key in ('editor_client_id', 'editor_window_id', 'editor_nonce'))):
            raise Conflict('Original file handoff, actor or revision changed')
        if value['phase'] not in {'READY', 'ARMED', 'BUFFER_CHANGED', 'UNKNOWN_OUTCOME'} or not value.get('grant'):
            raise Conflict('An active original granted handoff is required')
        return value

    @staticmethod
    def _same_slot(first, second):
        return bool(first and second and all(first.get(key) == second.get(key) for key in ('parent_ref', 'name', 'path')))

    def _snapshot(self, pid, value):
        grant = value['grant']; selection = grant['selection']; workspace = selection['workspace_ref']
        relative(grant['path'])
        snapshot = {'availability': 'UNAVAILABLE', 'saved': None, 'location': None,
            'editor_text_sha256': None, 'workspace_binding_sha256': workspace['binding_sha256'],
            'metadata_sha256': None, 'file_identity': None, 'metadata_recovery_version': 1}
        binding = self.owner.workspaces.binding_record(pid, workspace['id'], workspace['revision'])
        if (binding['binding_sha256'] != workspace['binding_sha256'] or binding['root'] != value['workspace_root']
                or binding['root_identity'] != value['root_identity']):
            raise Conflict('Original file handoff binding record changed')
        # Metadata above never authorizes opening a replacement root. get/_raw/
        # file_slot_location below still enforce the original physical root.
        try:
            current = self.owner.workspaces.get(pid, workspace['id'], workspace['revision'])
            location = editor_location(self.owner.workspaces.file_slot_location(pid, workspace['id'], grant['path'],
                binding_revision=workspace['revision'], binding_sha256=workspace['binding_sha256']))
            raw = self.owner.workspaces._raw(current, grant['path'], missing=True, max_bytes=524288)
            checked = self.owner.workspaces._raw(current, grant['path'], missing=True, max_bytes=524288)
            budget = current_context_read_budget()
            if budget:
                budget.charge(0 if raw is None else len(raw))
                budget.charge(0 if checked is None else len(checked))
            final = editor_location(self.owner.workspaces.file_slot_location(pid, workspace['id'], grant['path'],
                binding_revision=workspace['revision'], binding_sha256=workspace['binding_sha256']))
            if raw != checked or location != final:
                raise Conflict('Current file state changed during the original handoff observation')
            native = self._metadata_adapter()
            metadata = native.metadata_regular(current, grant['path'], private_check=self.owner.workspaces._private) if native is not None and raw is not None else None
            text = None
            if raw is not None:
                try:
                    decoded = raw.decode('utf-8')
                    if '\x00' not in decoded: text = text_hash(decoded)
                except UnicodeError: pass
            snapshot.update(availability='OBSERVED', location=location, editor_text_sha256=text,
                metadata_sha256=None if metadata is None else metadata['sha256'],
                file_identity=None if metadata is None else metadata['identity'],
                saved={'exists': raw is not None, 'sha256': None if raw is None else hashlib.sha256(raw).hexdigest(),
                    'byte_length': 0 if raw is None else len(raw)})
        except (Conflict, InvalidInput, NotFound, OSError) as error:
            snapshot['unavailable_reason'] = type(error).__name__
        return snapshot, binding

    def prepare(self, pid, sid, body):
        body = body if isinstance(body, EditorFileAddress) else EditorFileAddress.model_validate(body)
        value = self._value(pid, sid, body)
        return self._prepare(pid, sid, body, value)

    def _prepare(self, pid, sid, body, value):
        grant = value['grant']; snapshot, binding = self._snapshot(pid, value)
        if body.action != 'MANUAL_RESOLVE':
            if snapshot['availability'] != 'OBSERVED' or not self._same_slot(snapshot['location'], grant['file_location']):
                raise Conflict('The original parent or current file state is unavailable; use explicit manual recovery')
            if grant['mode'] not in {'FILE_CREATE', 'FILE_DELETE'}:
                raise Conflict('Text buffer edits use the original native editor path')
        if body.action == 'COMMIT':
            if value['phase'] != 'READY' or value.get('file_lifecycle_intent') or snapshot['saved'] != grant['saved_basis']:
                raise Conflict('A fresh untouched exact file handoff is required for commit')
        elif body.action in {'UNDO', 'CONFIRM'}:
            applied = value.get('file_lifecycle_result') or {}
            if value['phase'] != 'BUFFER_CHANGED' or applied.get('status') not in ({'APPLIED'} if body.action == 'UNDO' else {'APPLIED', 'UNDONE'}):
                raise Conflict('An exact confirmed file operation is required before Undo or confirmation')
            target = (grant['saved_basis'] if applied.get('status') == 'UNDONE' else
                {'exists': grant['target_exists'], 'sha256': grant['target_sha256']})
            if any(snapshot['saved'][key] != target[key] for key in target):
                raise Conflict('The file changed after this operation; manual content is retained')
            if body.action == 'UNDO':
                original = self._material(pid, sid, value, applied.get('metadata_ref'))
                required = 'CREATED_ENDPOINT' if snapshot['saved']['exists'] else 'DELETED_PREIMAGE'
                if original['purpose'] != required: raise Conflict('Original file endpoint recovery proof is required')
                if snapshot['saved']['exists'] and snapshot['metadata_sha256'] is not None and (snapshot['metadata_sha256'] != original['metadata_sha256']
                        or snapshot['file_identity'] != original['identity']):
                    raise Conflict('The created file permissions, timestamps or identity changed; manual state is retained')
        basis = {'scope': grant['scope'], 'lease_ref': self.editor._lease_ref(value), 'revision': value['revision'],
            'target_operation_id': value['operation_id'], 'action': body.action, 'snapshot': snapshot}
        return {'kind': 'EDITOR_FILE_PREPARATION', **basis, 'preparation_sha256': digest(basis),
            'editor_client_id': body.editor_client_id, 'editor_window_id': body.editor_window_id,
            'native_path': os.path.join(binding['root'], *grant['path'].split('/')),
            'read_only': True, 'workspace_writes': 0, 'model_calls': 0, 'native_posts': 0,
            'automatic_replay': False, 'editor_state': 'CLIENT_MUST_OBSERVE_BEFORE_EXPLICIT_COMMAND'}

    @staticmethod
    def _editor_proof(body, prepared):
        observation = body.editor_observation
        if not body.user_approved or observation.native_commands_settled is not True:
            raise Conflict('Explicit user action and settled original editor commands are required')
        if any(document.is_dirty for document in observation.documents):
            raise Conflict('Keep or save the original dirty editor before resolving this file operation')
        snapshot = prepared['snapshot']
        if body.action != 'MANUAL_RESOLVE':
            if not snapshot['saved']['exists'] and observation.documents:
                raise Conflict('An editor still owns the absent filename; native content is retained')
            if snapshot['saved']['exists'] and any(document.content_sha256 != snapshot['editor_text_sha256']
                    for document in observation.documents):
                raise Conflict('The original editor content differs from current saved bytes')

    async def _historical_bytes(self, pid, sid, value):
        grant = value['grant']
        prepared = await self.editor.reviews.prepare(pid, sid, DiffPreparation.model_validate(grant['selection']))
        detail = prepared['diff']; target = 'before' if grant['direction'] == 'RESTORE_BEFORE' else 'after'
        source = 'after' if target == 'before' else 'before'
        before = detail[source].encode('utf-8') if grant['saved_basis']['exists'] else None
        after = detail[target].encode('utf-8') if grant['target_exists'] else None
        if (detail['path'] != grant['path'] or before is not None and hashlib.sha256(before).hexdigest() != grant['saved_basis']['sha256']
                or after is not None and hashlib.sha256(after).hexdigest() != grant['target_sha256']
                or before is not None and len(before) > 524288 or after is not None and len(after) > 524288):
            raise Conflict('Original immutable file lifecycle bytes no longer match this handoff')
        return before, after

    def _publish(self, db, value, body, phase, result):
        receipts = value.get('file_lifecycle_receipts', [])
        if len(receipts) >= 8: raise Conflict('Original file lifecycle receipt bound is full')
        receipts.append({'operation_id': body.operation_id, 'request_sha256': digest(body.model_dump()),
            'phase': phase, 'revision': value['revision']+1, 'result': result})
        value['file_lifecycle_receipts'] = receipts
        return self.editor._save(db, value, phase, value['revision']+1)

    async def command(self, pid, sid, body):
        body = body if isinstance(body, EditorFileCommand) else EditorFileCommand.model_validate(body)
        self.owner._manifest(pid, sid, body.session_ref.model_dump())
        old, created = self.owner._claim(pid, sid, body.operation_id, 'EDITOR_FILE_ACTION', body.model_dump())
        if not created: return self.operation(pid, sid, body.operation_id, client=body.editor_client_id)
        mutating = body.action in {'COMMIT', 'UNDO'}; invoked = False; intent_published = False
        flight = (pid, sid, body.target_operation_id); registered = False
        try:
            with self.lock:
                if flight in self.inflight: raise Conflict('An original file command is still in flight; inspect it without taking over')
                self.inflight[flight] = body.operation_id; registered = True
            value = self._value(pid, sid, body)
            before, after = await self._historical_bytes(pid, sid, value) if mutating else (None, None)
            with self.owner.store.connect() as db:
                db.execute('BEGIN IMMEDIATE'); value = self._value(pid, sid, body, db)
                prepared = self._prepare(pid, sid, body, value)
                if prepared['preparation_sha256'] != body.preparation_sha256:
                    raise Conflict('Original file state or preparation changed')
                self._editor_proof(body, prepared)
                intent = value.get('file_lifecycle_intent') or {}
                identity = intent.get('controller_identity')
                if (body.action == 'MANUAL_RESOLVE' and intent.get('file_effect') == 'NOT_YET_OBSERVED'
                        and identity != self.owner._identity and (not identity or self.owner._alive(identity))):
                    raise Conflict('The original file controller has not ended; keep its pending operation')
                if len(value.get('file_lifecycle_receipts', [])) >= 8:
                    raise Conflict('Original file lifecycle receipt bound is full')
                if not mutating:
                    phase = ('CANCELLED' if value.get('file_lifecycle_result', {}).get('status') == 'UNDONE' else 'SAVED') if body.action == 'CONFIRM' else 'MANUAL_SAVED'
                    result = {'status': 'CONFIRMED' if body.action == 'CONFIRM' else 'MANUALLY_RESOLVED',
                        'snapshot': prepared['snapshot'], 'workspace_writes': 0,
                        'effect_scope': 'HANDOFF_RELEASE_ONLY_NO_FILE_CHANGE', 'editor_state': 'CLIENT_REPORTED'}
                    intent_published = True
                    final = self._publish(db, value, body, phase, result)
                else:
                    manifest, _ = self.owner._manifest(pid, sid, body.session_ref.model_dump())
                    if (self.owner.closed or self.owner._state(pid, sid)['generation'] != body.generation
                            or 'WORKSPACE_WRITE' not in manifest['request'].get('allowed_effects', [])):
                        raise Conflict('The original current workspace write authority is required')
                    binding = self.owner.workspaces.get(pid, value['grant']['scope']['workspace_ref']['id'],
                        value['grant']['scope']['workspace_ref']['revision'])
                    self.editor.assert_available(binding['root'], identity=binding['root_identity'], connection=db, except_id=value['id'])
                    self.editor._cached_quiet(pid, sid, binding['root'], binding['root_identity'])
                    native = self._mutation_adapter()
                    if native is None or not callable(getattr(native, 'compare_delete', None)):
                        raise Conflict('Exclusive original Windows handle file lifecycle support is required')
                    source, target = (before, after) if body.action == 'COMMIT' else (after, before)
                    value['file_lifecycle_intent'] = {'operation_id': body.operation_id, 'action': body.action,
                        'request_sha256': digest(body.model_dump()), 'preparation_sha256': body.preparation_sha256,
                        'created_at': now(), 'source_exists': source is not None, 'target_exists': target is not None,
                        'controller_identity': self.owner._identity,
                        'editor_observation': body.editor_observation.model_dump(), 'file_effect': 'NOT_YET_OBSERVED'}
                    intent_published = True
                    self.editor._save(db, value, 'ARMED', value['revision']+1)
            if mutating:
                intent_published = True
                self.owner._finish(pid, sid, body.operation_id, 'DISPATCHING', generation=body.generation,
                    original_handoff_operation_id=body.target_operation_id, file_action=body.action,
                    file_effect='UNCONFIRMED', model_calls=0, native_posts=0, automatic_file_replay=False)
                parent = value['grant']['file_location']['parent_ref']; invoked = True
                if target is None:
                    qualification = native.compare_delete(binding, value['grant']['path'], expected_raw=source,
                        expected_parent=parent, private_check=self.owner.workspaces._private,
                        expected_metadata_sha256=prepared['snapshot']['metadata_sha256'], expected_identity=prepared['snapshot']['file_identity'],
                        persist_recovery=lambda captured: self._record_material(pid, sid, value, body, 'DELETED_PREIMAGE', captured))
                elif source is None:
                    restored = None
                    if body.action == 'UNDO':
                        original = self._material(pid, sid, value, value.get('file_lifecycle_result', {}).get('metadata_ref'))
                        if original['purpose'] != 'DELETED_PREIMAGE': raise Conflict('Original deletion recovery metadata is required')
                        restored = original['metadata']
                    qualification = native.compare_write(binding, value['grant']['path'], target, expected_raw=None,
                        expected_parent=parent, private_check=self.owner.workspaces._private, restore_metadata=restored,
                        record_created=lambda captured: self._record_material(pid, sid, value, body, 'CREATED_ENDPOINT', captured))
                else:
                    raise Conflict('File lifecycle changes cannot be substituted for native text editing')
                observed, _ = self._snapshot(pid, value)
                expected_sha = None if target is None else hashlib.sha256(target).hexdigest()
                if (observed['availability'] != 'OBSERVED' or observed['saved']['exists'] != (target is not None)
                        or observed['saved']['sha256'] != expected_sha or not self._same_slot(observed['location'], value['grant']['file_location'])):
                    raise Conflict('UNKNOWN_OUTCOME: original file lifecycle namespace/result needs inspection')
                result = {'status': 'APPLIED' if body.action == 'COMMIT' else 'UNDONE', 'workspace_writes': 1,
                    'commit_qualification': qualification, 'observed_saved': observed['saved'],
                    'effect_scope': 'EXCLUSIVE_ORIGINAL_FILE_EXISTENCE_CHANGE', 'native_text_undo': False,
                    'metadata_ref': qualification.get('metadata_recovery_ref') if target is None else qualification.get('created_metadata_ref'),
                    'metadata_scope': 'ORDINARY_NTFS_ATTRIBUTES_OWNER_GROUP_DACL_CREATION_ACCESS_WRITE_NO_NAMED_STREAMS',
                    'native_qualification': 'NOT_RUN'}
                with self.owner.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = self.editor._decode(self.editor._row(db, pid, sid, body.target_operation_id))
                    if (current['phase'] != 'ARMED' or current['revision'] != body.expected_revision+1
                            or current.get('file_lifecycle_intent', {}).get('operation_id') != body.operation_id):
                        raise Conflict('Original file lifecycle intent changed before result publication')
                    current['file_lifecycle_result'] = result
                    current['file_lifecycle_intent']['file_effect'] = result['status']
                    final = self._publish(db, current, body, 'BUFFER_CHANGED', result)
            return self.owner._finish(pid, sid, body.operation_id, 'RETURNED', generation=body.generation,
                editor_change=self.editor._public(final), file_result=result, automatic_file_replay=False,
                file_effect=result['status'] if mutating else 'NO_FILE_CHANGE',
                semantic_acceptance=False, independent_verification='NOT_RUN', model_calls=0, native_posts=0)
        except BaseException as error:
            # A committed intent is never inferred to have applied from matching
            # current bytes. Only an original persisted result or explicit manual
            # state resolution can close an interrupted operation.
            self.owner._finish(pid, sid, body.operation_id, 'UNKNOWN_OUTCOME' if intent_published or invoked else 'FAILED',
                generation=body.generation, file_effect='UNCONFIRMED' if mutating and (intent_published or invoked) else 'NOT_INVOKED',
                automatic_file_replay=False, error_type=type(error).__name__, model_calls=0, native_posts=0)
            raise
        finally:
            if registered:
                with self.lock:
                    if self.inflight.get(flight) == body.operation_id: self.inflight.pop(flight)

    def operation(self, pid, sid, operation, *, client):
        old = self.owner.operation(pid, sid, operation)
        if old.get('kind') != 'EDITOR_FILE_ACTION': raise Conflict('Original operation is not a file lifecycle action')
        body = EditorFileCommand.model_validate(old['request'])
        if body.editor_client_id != client or digest(body.model_dump()) != old['request_sha256']:
            raise Conflict('Original file lifecycle request or paired client differs')
        if old['status'] in {'RECORDED_NOT_DISPATCHED', 'DISPATCHING', 'UNKNOWN_OUTCOME'}:
            with self.owner.store.connect() as db:
                value = self.editor._decode(self.editor._row(db, pid, sid, body.target_operation_id))
            rows = [row for row in value.get('file_lifecycle_receipts', [])
                if row['operation_id'] == operation and row['request_sha256'] == old['request_sha256']]
            if len(rows) == 1:
                row = rows[0]
                old = self.owner._finish(pid, sid, operation, 'RETURNED', generation=body.generation,
                    editor_change=self.editor._public({**value, 'phase': row['phase'], 'revision': row['revision']}),
                    file_result=row['result'], recovered_original_metadata=True, automatic_file_replay=False,
                    file_effect=row['result']['status'] if row['result'].get('workspace_writes') else 'NO_FILE_CHANGE',
                    semantic_acceptance=False, independent_verification='NOT_RUN', model_calls=0, native_posts=0)
        return old
