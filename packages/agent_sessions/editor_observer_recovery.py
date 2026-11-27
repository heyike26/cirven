"""Explicit paired recovery of an original metadata observer, never file control.

The old actor, snapshots and unresolved receipts remain immutable evidence. A
new paired actor may retire only its own exact process lifetime or an ended
producer, with a fresh complete local capture and explicit preservation consent.
"""
import json
import time

from pydantic import Field

from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.domain.editor_observer_scope import MAX_ACTIONS, decode_observer
from .models import SessionRef, WorkspaceRef
from .editor_observers import EditorObservation, process_identity, process_ended


class EditorObserverRecoveryAddress(RequestModel):
    session_ref: SessionRef
    generation: int = Field(ge=1, strict=True)
    workspace_ref: WorkspaceRef
    observer_id: Hash
    editor_client_id: Id
    editor_window_id: Id
    editor_process_id: int = Field(ge=1, le=2147483647, strict=True)
    editor_parent_process_id: int = Field(ge=1, le=2147483647, strict=True)


class EditorObserverRecoveryCommand(EditorObserverRecoveryAddress):
    operation_id: Id
    expected_revision: int = Field(ge=1, le=2147483646, strict=True)
    preparation_sha256: Hash
    observation: EditorObservation
    user_preserved_original: bool = Field(strict=True)


class EditorObserverRecovery:
    def __init__(self, observers):
        self.observers = observers
        self.owner = observers.owner

    def _prepared(self, db, pid, sid, body, *, current=False):
        # Read preparation may discover the CURRENT generation of this same
        # immutable session. Commands must explicitly echo that exact scope.
        _, ref = self.owner._manifest(pid, sid, body.session_ref.model_dump())
        generation = self.owner._state(pid, sid)['generation']
        if not current and generation != body.generation:
            raise Conflict('Original observer recovery generation differs')
        scoped = body.model_copy(update={'generation':generation})
        scope, binding = self.observers._scope(pid, sid, scoped)
        row = db.execute('SELECT * FROM agent_editor_observers WHERE project_id=? AND id=?', (pid,body.observer_id)).fetchone()
        if row is None: raise NotFound('Original IDE observer does not exist')
        value = decode_observer(row)
        original = value.get('observation_scope') or value['origin_scope']
        if (original['session_ref'] != ref
                or value['workspace_ref'] != body.workspace_ref.model_dump()
                or value['workspace_root'] != binding['root'] or value['root_identity'] != binding['root_identity']):
            raise Conflict('Original observer recovery namespace or lifetime differs')
        runtime = db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
        if not runtime or runtime['runtime_id'] != self.observers.runtime_id:
            raise Conflict('Original observation service instance changed')
        identity = process_identity(body.editor_process_id,body.editor_parent_process_id)
        if not current and value['phase'] != 'ACTIVE':
            raise Conflict('Original observer protection has already ended')
        same_process = identity == value['process_identity']
        ended = False if same_process else process_ended(value['process_identity'])
        if not same_process and not ended and value['phase'] == 'ACTIVE':
            raise Conflict('EDITOR_ORIGINAL_PROCESS_UNCONFIRMED: preserve the still-running original producer')
        basis = {'scope':scope,'observer_id':value['id'],'revision':value['revision'],'phase':value['phase'],
            'runtime_id':self.observers.runtime_id,'editor_client_id':body.editor_client_id,
            'editor_window_id':body.editor_window_id,'process_identity':identity,
            'original_process_identity':value['process_identity'],
            'original_editor_client_id':value['editor_client_id'],
            'original_editor_window_id':value['editor_window_id'],
            'original_process_state':'SAME_PROCESS' if same_process else 'ENDED' if ended else 'OTHER_LIVE',
            'root_identity':{k:str(v) for k,v in binding['root_identity'].items()},
            'root_path_sha256':digest(binding['root'])}
        return {'kind':'EDITOR_OBSERVER_RECOVERY_PREPARATION',**basis,
            'preparation_sha256':digest(basis),'native_root':binding['root'],
            'read_only':True,'workspace_writes':0,'model_calls':0,'native_posts':0,
            'permission_granted':False,'automatic_replay':False,
            'current_scope_is_execution_authorization':False}, value, binding

    def prepare(self, pid, sid, body):
        body = EditorObserverRecoveryAddress.model_validate(body)
        with self.owner.store.connect() as db:
            prepared,_,_ = self._prepared(db,pid,sid,body,current=True)
        return prepared

    def operation(self, pid, sid, operation, *, client):
        result = self.observers.operation(pid,sid,operation,client=client)
        if result.get('kind') != 'EDITOR_OBSERVER_RECOVERY':
            raise Conflict('Original operation is not an observer recovery')
        return result

    def command(self, pid, sid, body):
        body = EditorObserverRecoveryCommand.model_validate(body)
        request = body.model_dump()
        with self.owner.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM agent_editor_observer_actions WHERE project_id=? AND session_id=? AND operation_id=?', (pid,sid,body.operation_id)).fetchone()
            if prior is not None:
                result = self.observers._receipt(prior,request=request,client=body.editor_client_id)
                if result.get('kind') != 'EDITOR_OBSERVER_RECOVERY':
                    raise Conflict('Original operation is not an observer recovery')
                return result
            prepared, original, binding = self._prepared(db,pid,sid,body)
            if body.expected_revision != prepared['revision'] or body.preparation_sha256 != prepared['preparation_sha256']:
                raise Conflict('Original observer recovery preparation changed')
            snapshot = body.observation.model_dump()
            if (not body.user_preserved_original or not snapshot['documents_complete']
                    or snapshot['permission_ref'] is not None or snapshot['capture_ref'] is not None
                    or snapshot['availability'] != 'OBSERVED' and snapshot['unavailable_reason'] != 'NAMESPACE_CHANGED'
                    or any(d['is_dirty'] for d in snapshot['documents'])):
                raise Conflict('DIRTY_EDITOR: explicitly preserve original content and capture complete clean local documents')
            if snapshot['availability'] == 'OBSERVED':
                current = self.owner.workspaces.get(pid,binding['id'],binding['revision'])
                if current['root_identity'] != binding['root_identity'] or snapshot['root_identity'] != prepared['root_identity']:
                    raise Conflict('Original observer recovery physical root changed')
                for document in snapshot['documents']:self.owner.workspaces._private(binding,document['path'])
            paths = [d['path'].casefold() if ':' in binding['root'][:3] else d['path'] for d in snapshot['documents']]
            if len(set(paths)) != len(paths):raise Conflict('Duplicate recovery document paths')
            if db.execute('SELECT COUNT(*) FROM agent_editor_observer_actions').fetchone()[0] >= MAX_ACTIONS:
                raise Conflict('EDITOR_OBSERVATION_CAPACITY: retained original receipts reached their bound')
            if not -1 <= time.time()-snapshot['captured_at_epoch_ms']/1000 <= 3:
                raise Conflict('EDITOR_OBSERVATION_REQUIRED: recapture current local documents')
            # Recheck both process lifetimes and all original preparation facts
            # after physical/document validation, immediately before the update.
            latest,_,_ = self._prepared(db,pid,sid,body)
            if latest['preparation_sha256'] != prepared['preparation_sha256']:
                raise Conflict('Original producer changed during recovery')
            if not -1 <= time.time()-snapshot['captured_at_epoch_ms']/1000 <= 3:
                raise Conflict('EDITOR_OBSERVATION_REQUIRED: recovery capture expired during validation')
            updated = now()
            value = {**original,'phase':'RETIRED','revision':original['revision']+1,'updated_at':updated,
                'recovery':{'operation_id':body.operation_id,'request_sha256':digest(request),
                    'editor_client_id':body.editor_client_id,'editor_window_id':body.editor_window_id,
                    'process_identity':prepared['process_identity'],
                    # The exact new request remains in the companion's private
                    # intent. Avoid duplicating two full document inventories in
                    # the bounded observer record; retain its original snapshot.
                    'observation_sha256':digest(snapshot),
                    'captured_at_epoch_ms':snapshot['captured_at_epoch_ms'],
                    'observed_documents':len(snapshot['documents']),
                    'user_preserved_original':True,'original_process_state':prepared['original_process_state']}}
            # Keep the original dirty/unknown observation, origin and all receipts.
            payload = canonical(value)
            if len(payload.encode()) > 65536:raise Conflict('Original recovery record exceeds its bound')
            db.execute('UPDATE agent_editor_observers SET phase=?,revision=?,payload_json=?,payload_sha256=?,updated_at=? WHERE id=? AND revision=?', ('RETIRED',value['revision'],payload,digest(value),updated,value['id'],original['revision']))
            result = {'kind':'EDITOR_OBSERVER_RECOVERY','id':body.operation_id,'scope':prepared['scope'],
                'request_sha256':digest(request),'observer_id':original['id'],
                'editor_client_id':body.editor_client_id,'editor_window_id':body.editor_window_id,
                'original_editor_client_id':original['editor_client_id'],
                'original_editor_window_id':original['editor_window_id'],
                'original_process_state':prepared['original_process_state'],
                'phase':'RETIRED','revision':value['revision'],'phase':value['phase'],'status':'RETURNED',
                'workspace_writes':0,'model_calls':0,'native_posts':0,'permission_granted':False,
                'automatic_replay':False,'atomic_editor_write_guarantee':False,
                'original_evidence_retained':True,'observation_time_scope':'AT_ORIGINAL_COMMAND_ONLY'}
            receipt = canonical(result)
            if len(receipt.encode()) > 8192:raise Conflict('Original recovery receipt exceeds its bound')
            db.execute('INSERT INTO agent_editor_observer_actions(id,project_id,session_id,operation_id,observer_id,request_sha256,payload_json,payload_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (digest({'project_id':pid,'session_id':sid,'operation_id':body.operation_id}),pid,sid,body.operation_id,original['id'],digest(request),receipt,digest(result),updated))
        self.owner.editor_captures.broker.changed()
        return result
