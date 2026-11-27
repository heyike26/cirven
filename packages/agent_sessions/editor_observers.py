"""Paired IDE metadata observations shared by original writers.

No buffer text, save/reload/close, native command or model call. Observation
publication and its immutable receipt commit in one SQLite transaction. This
is a captured-state guard, not atomic control over future typing in VS Code.
"""
import json
from datetime import datetime, timezone
import time
import uuid
from typing import Literal

import psutil
from pydantic import Field, field_validator, model_validator

from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.domain.local_workspaces import relative
from packages.domain.editor_observer_scope import (MAX_ACTIVE, MAX_WINDOWS, MAX_OBSERVATIONS,
    MAX_ACTIONS, MAX_WINDOW_OBSERVATIONS, MAX_PAYLOAD, decode_observer, assert_observers_clean)
from .models import SessionRef, WorkspaceRef, PendingRef


class EditorRootIdentity(RequestModel):
    device: str = Field(pattern=r'^\d{1,32}$')
    inode: str = Field(pattern=r'^\d{1,32}$')


class EditorObservedDocument(RequestModel):
    path: str = Field(min_length=1, max_length=1024)
    document_version: int = Field(ge=1, le=2147483647, strict=True)
    content_sha256: Hash
    is_dirty: bool = Field(strict=True)

    @field_validator('path')
    @classmethod
    def safe_relative(cls, value):
        relative(value)
        return value


class EditorObservation(RequestModel):
    captured_at_epoch_ms: int = Field(ge=1, le=9007199254740991, strict=True)
    availability: Literal['OBSERVED','UNAVAILABLE']
    root_identity: EditorRootIdentity | None = None
    documents: list[EditorObservedDocument] = Field(default_factory=list, max_length=64)
    documents_complete: bool = Field(strict=True)
    unavailable_reason: Literal['NOT_CAPTURED','EDITOR_UNAVAILABLE','NAMESPACE_CHANGED','DOCUMENT_LIMIT'] | None = None
    permission_ref: PendingRef | None = None
    capture_ref: Hash | None = None

    @model_validator(mode='after')
    def shape(self):
        if self.availability == 'OBSERVED':
            if self.root_identity is None or self.unavailable_reason is not None or not self.documents_complete:
                raise ValueError('An observed original IDE namespace requires its exact physical identity')
        elif (self.root_identity is not None or self.unavailable_reason is None or self.permission_ref is not None
                or self.documents_complete and self.unavailable_reason != 'NAMESPACE_CHANGED'
                or self.documents and not self.documents_complete):
            raise ValueError('Unavailable namespaces cannot imply absent or clean documents; only an explicit complete local document capture may accompany namespace drift')
        if len(canonical(self.model_dump()).encode()) > 49152:
            raise ValueError('Original IDE metadata exceeds its bounded snapshot size')
        return self


class EditorObserverAddress(RequestModel):
    session_ref: SessionRef
    generation: int = Field(ge=1, strict=True)
    workspace_ref: WorkspaceRef
    editor_client_id: Id
    editor_window_id: Id
    editor_process_id: int = Field(ge=1, le=2147483647, strict=True)
    editor_parent_process_id: int = Field(ge=1, le=2147483647, strict=True)
    action: Literal['OBSERVE','RETIRE']


class EditorObserverCommand(EditorObserverAddress):
    operation_id: Id
    expected_revision: int = Field(ge=0, le=2147483646, strict=True)
    preparation_sha256: Hash
    observation: EditorObservation
    user_approved: bool = Field(default=False, strict=True)


def observer_id(pid, body):
    return digest({'project_id':pid,'workspace_id':body.workspace_ref.id,
        'editor_client_id':body.editor_client_id,'editor_window_id':body.editor_window_id})


def observer_request(body):
    value=body.model_dump()
    if value['observation'].get('capture_ref') is None:value['observation'].pop('capture_ref',None)
    return value


def process_identity(pid, parent):
    try:
        process = psutil.Process(pid); previous = psutil.Process(parent)
        if process.ppid() != parent or not process.is_running() or not previous.is_running():
            raise Conflict('EDITOR_PROCESS_UNCONFIRMED: original IDE process lineage differs')
        return {'pid':pid,'parent_pid':parent,'birth':repr(process.create_time()),'parent_birth':repr(previous.create_time())}
    except (psutil.Error, OSError) as error:
        raise Conflict('EDITOR_PROCESS_UNCONFIRMED: original IDE process could not be observed') from error


def process_ended(identity):
    # This producer publishes metadata only. Once its original extension process
    # has ended it cannot publish again; a shared VS Code parent may stay alive.
    # Native file/editor handoffs keep their separate, stricter owner barrier.
    try:
        process = psutil.Process(identity['pid'])
        return not process.is_running() or repr(process.create_time()) != identity['birth']
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError):
        return False


class EditorObservers:
    def __init__(self, owner):
        self.owner = owner; self.runtime_id = uuid.uuid4().hex
        # Restart invalidates previously clean observations without clearing them.
        with owner.store.connect() as db:
            db.execute('INSERT INTO agent_editor_observer_runtime(singleton,runtime_id) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET runtime_id=excluded.runtime_id', (self.runtime_id,))

    def _scope(self, pid, sid, body):
        manifest, ref = self.owner._manifest(pid, sid, body.session_ref.model_dump())
        state = self.owner._state(pid, sid)
        workspace = body.workspace_ref.model_dump()
        if (manifest['request'].get('workspace_ref') != workspace or state.get('generation') != body.generation
                or manifest['directory_identity'] is None):
            raise Conflict('Original IDE observation session, generation or workspace differs')
        binding = self.owner.workspaces.binding_record(pid, workspace['id'], workspace['revision'])
        if binding['binding_sha256'] != workspace['binding_sha256'] or binding['root_identity'] != manifest['directory_identity']:
            raise Conflict('Original IDE observation binding differs')
        return {'project_id':pid,'session_ref':ref,'generation':body.generation,'workspace_ref':workspace}, binding

    def _row(self, db, pid, body):
        return db.execute('SELECT * FROM agent_editor_observers WHERE id=? AND project_id=?', (observer_id(pid,body),pid)).fetchone()

    def _prepared(self, pid, sid, body, db):
        runtime = db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
        if not runtime or runtime['runtime_id'] != self.runtime_id:
            raise Conflict('EDITOR_OBSERVATION_REQUIRED: the original observation service instance changed')
        scope, binding = self._scope(pid, sid, body); row = self._row(db,pid,body)
        value = decode_observer(row) if row else None
        if value and (value['workspace_id'] != binding['id'] or value['root_identity'] != binding['root_identity']
                or value['workspace_root'] != binding['root'] or value['workspace_ref'] != body.workspace_ref.model_dump()):
            raise Conflict('Original IDE observation namespace changed')
        if value and value['phase'] == 'RETIRED':
            raise Conflict('A retired IDE window observation cannot be reactivated')
        if body.action == 'RETIRE' and value is None:
            raise NotFound('Original IDE observer does not exist')
        basis = {'scope':scope,'observer_id':observer_id(pid,body),'revision':value['revision'] if value else 0,
            'runtime_id':self.runtime_id,'action':body.action,'editor_client_id':body.editor_client_id,
            'editor_window_id':body.editor_window_id,'editor_process_id':body.editor_process_id,
            'editor_parent_process_id':body.editor_parent_process_id,
            'root_identity':{key:str(v) for key,v in binding['root_identity'].items()},
            'root_path_sha256':digest(binding['root']),
            'process_identity':process_identity(body.editor_process_id,body.editor_parent_process_id)}
        return {'kind':'EDITOR_OBSERVER_PREPARATION',**basis,'preparation_sha256':digest(basis),
            'native_root':binding['root'],'read_only':True,'workspace_writes':0,'model_calls':0,'native_posts':0,
            'automatic_replay':False}, binding, value

    def prepare(self, pid, sid, body):
        body = EditorObserverAddress.model_validate(body)
        with self.owner.store.connect() as db:
            prepared, _, _ = self._prepared(pid,sid,body,db)
        return prepared

    def _receipt(self, row, *, request=None, client=None):
        if len(row['payload_json'].encode()) > 8192:
            raise Conflict('Original IDE observation receipt exceeds its bound')
        result = json.loads(row['payload_json'])
        if (digest(result) != row['payload_sha256'] or result['id'] != row['operation_id']
                or result['request_sha256'] != row['request_sha256']
                or request is not None and row['request_sha256'] != digest(request)
                or client is not None and result['editor_client_id'] != client):
            raise Conflict('Original IDE observation receipt input differs')
        return result

    def operation(self, pid, sid, operation, *, client):
        self.owner._manifest(pid,sid)
        with self.owner.store.connect() as db:
            row = db.execute('SELECT * FROM agent_editor_observer_actions WHERE project_id=? AND session_id=? AND operation_id=?', (pid,sid,operation)).fetchone()
            if row is None: raise NotFound('Original IDE observation receipt does not exist')
            return self._receipt(row,client=client)

    def inspect(self, pid, ident, *, client):
        with self.owner.store.connect() as db:
            row = db.execute('SELECT * FROM agent_editor_observers WHERE project_id=? AND id=? AND editor_client_id=?',(pid,ident,client)).fetchone()
            if row is None: raise NotFound('Original paired IDE observer does not exist')
            value = decode_observer(row)
        result=self._public(value)
        original=value.get('observation_scope') or value['origin_scope']
        _,ref=self.owner._manifest(pid,original['session_ref']['id'],original['session_ref'])
        current=self.owner._state(pid,original['session_ref']['id'])
        result['current_scope']={'project_id':pid,'session_ref':ref,'generation':current['generation']}
        result['current_scope_is_execution_authorization']=False
        return result

    def _public(self, value):
        snapshot = value['observation']
        elapsed = time.time()-value['observed_at_epoch']
        from packages.domain.editor_observer_scope import FRESH_SECONDS
        with self.owner.store.connect() as db:
            runtime = db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
        current = bool(runtime and runtime['runtime_id'] == self.runtime_id == value['runtime_id'] and 0 <= elapsed <= FRESH_SECONDS)
        dirty = [d['path'] for d in snapshot['documents'] if d['is_dirty']]; preview = []; size = 0
        for path in dirty:
            if len(preview) >= 4 or size + len(path.encode()) > 2048: break
            preview.append(path); size += len(path.encode())
        return {'kind':'EDITOR_OBSERVER','id':value['id'],'project_id':value['project_id'],
            'workspace_ref':value['workspace_ref'],'editor_client_id':value['editor_client_id'],
            'editor_window_id':value['editor_window_id'],'phase':value['phase'],'revision':value['revision'],
            'observation_state':snapshot['availability'] if current else 'STALE',
            'dirty_paths':preview,'dirty_count':len(dirty),'dirty_paths_truncated':len(preview)!=len(dirty),
            'process_identity':value['process_identity'],
            'observed_documents':len(snapshot['documents']),'current':current,'read_only':True,
            'workspace_writes':0,'model_calls':0,'native_posts':0,'automatic_replay':False,
            'atomic_editor_write_guarantee':False,'captured_at_epoch_ms':value['observation']['captured_at_epoch_ms'],
            'observed_at':datetime.fromtimestamp(value['observed_at_epoch'],timezone.utc).isoformat(),
            'received_at':value['updated_at'],'capture_ref':snapshot.get('capture_ref')}

    def command(self, pid, sid, body):
        body = EditorObserverCommand.model_validate(body); request = observer_request(body)
        with self.owner.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM agent_editor_observer_actions WHERE project_id=? AND session_id=? AND operation_id=?',(pid,sid,body.operation_id)).fetchone()
            if prior is not None: return self._receipt(prior,request=request,client=body.editor_client_id)
            prepared, binding, original = self._prepared(pid,sid,body,db)
            if body.preparation_sha256 != prepared['preparation_sha256'] or body.expected_revision != prepared['revision']:
                raise Conflict('Original IDE observation preparation/revision changed')
            observation = body.observation.model_dump()
            if observation['capture_ref'] is None:observation.pop('capture_ref')
            elif original is None:raise Conflict('An original registered observer is required for a collective capture')
            else:
                from .editor_captures import member_matches
                capture = member_matches(db,observation['capture_ref'],original)
                if capture.get('target_kind')=='WORKSPACE_EDIT' and observation['permission_ref'] is not None:
                    raise Conflict('Workspace metadata capture cannot borrow an original permission purpose')
                if not capture['created_at_ms'] <= observation['captured_at_epoch_ms'] <= capture['expires_at_ms']:
                    raise Conflict('EDITOR_CAPTURE_INVALID: local capture must follow its exact original request')
                if capture['runtime_id'] != self.runtime_id:
                    raise Conflict('EDITOR_CAPTURE_EXPIRED: the capture service instance changed')
            age = time.time()-observation['captured_at_epoch_ms']/1000
            if not -1 <= age <= 3:
                raise Conflict('EDITOR_OBSERVATION_REQUIRED: capture original local documents again; delayed clean state is not current')
            identity = prepared['process_identity']
            if original and original['process_identity'] != identity:
                if body.action != 'RETIRE' or not process_ended(original['process_identity']):
                    raise Conflict('EDITOR_ORIGINAL_PROCESS_UNCONFIRMED: the original IDE window still owns its observation')
            if observation['availability'] == 'OBSERVED':
                current = self.owner.workspaces.get(pid,binding['id'],binding['revision'])
                if current['root_identity'] != binding['root_identity'] or observation['root_identity'] != prepared['root_identity']:
                    raise Conflict('Original observed IDE root identity differs')
                paths = [doc['path'] for doc in observation['documents']]
                folded = [p.casefold() if ':' in binding['root'][:3] else p for p in paths]
                if len(set(folded)) != len(folded): raise Conflict('Duplicate original IDE document paths')
                for path in paths: self.owner.workspaces._private(binding,path)
                permission = observation['permission_ref']
                if permission is not None:
                    state = self.owner._state(pid,sid)
                    if permission['generation'] != body.generation or not any(
                            item.get('request_ref') == permission and item.get('native_request',{}).get('permission') == 'edit'
                            for item in state.get('pending_permissions',[])):
                        raise Conflict('Original IDE permission observation is no longer pending')
            if body.action == 'RETIRE':
                if (not body.user_approved or not observation['documents_complete']
                        or observation['availability'] != 'OBSERVED' and observation['unavailable_reason'] != 'NAMESPACE_CHANGED'
                        or any(d['is_dirty'] for d in observation['documents'])):
                    raise Conflict('Explicit retirement requires a current clean IDE observation')
            elif body.user_approved:
                raise Conflict('Publishing an IDE observation must not masquerade as a user permission grant')
            total = db.execute('SELECT COUNT(*) FROM agent_editor_observer_actions').fetchone()[0]
            own = db.execute('SELECT COUNT(*) FROM agent_editor_observer_actions WHERE observer_id=?',(prepared['observer_id'],)).fetchone()[0]
            if total >= (MAX_ACTIONS if body.action == 'RETIRE' else MAX_OBSERVATIONS) or body.action != 'RETIRE' and own >= MAX_WINDOW_OBSERVATIONS:
                raise Conflict('EDITOR_OBSERVATION_CAPACITY: original observation receipts retained; no new snapshot accepted')
            if original is None:
                count = db.execute("SELECT COUNT(*) FROM agent_editor_observers WHERE phase='ACTIVE'").fetchone()[0]
                rows = db.execute('SELECT COUNT(*) FROM agent_editor_observers').fetchone()[0]
                if count >= MAX_ACTIVE or rows >= MAX_WINDOWS:
                    raise Conflict('EDITOR_OBSERVATION_CAPACITY: original IDE window bound reached')
            if not -1 <= time.time()-observation['captured_at_epoch_ms']/1000 <= 3:
                raise Conflict('EDITOR_OBSERVATION_REQUIRED: editor capture expired while checking its original namespace')
            if process_identity(body.editor_process_id,body.editor_parent_process_id) != identity:
                raise Conflict('EDITOR_PROCESS_UNCONFIRMED: original IDE process changed after its capture')
            updated = now(); value = {'id':prepared['observer_id'],'project_id':pid,'workspace_id':binding['id'],
                'workspace_ref':body.workspace_ref.model_dump(),'editor_client_id':body.editor_client_id,
                'editor_window_id':body.editor_window_id,'workspace_root':binding['root'],'root_identity':binding['root_identity'],
                'origin_scope':original['origin_scope'] if original else prepared['scope'],'observation_scope':prepared['scope'],
                'process_identity':original['process_identity'] if original else identity,'runtime_id':self.runtime_id,
                'phase':'RETIRED' if body.action == 'RETIRE' else 'ACTIVE','revision':prepared['revision']+1,
                'observation':observation,'observed_at_epoch':observation['captured_at_epoch_ms']/1000,'updated_at':updated}
            from packages.domain.editor_capture_scope import observation_fingerprint
            fingerprint=observation_fingerprint(observation)
            acknowledgements={ref:ack for ref,ack in (original or {}).get('capture_acks',{}).items() if ack.get('fingerprint')==fingerprint and 0<=int(time.time()*1000)-ack['captured_at_epoch_ms']<=12000}
            if observation.get('capture_ref'):
                acknowledgements[observation['capture_ref']]={'fingerprint':fingerprint,'captured_at_epoch_ms':observation['captured_at_epoch_ms']}
            value['capture_acks']=dict(sorted(acknowledgements.items(),key=lambda item:item[1]['captured_at_epoch_ms'])[-8:])
            payload = canonical(value)
            if len(payload.encode()) > MAX_PAYLOAD: raise Conflict('Original IDE observation record exceeds its bound')
            db.execute('INSERT INTO agent_editor_observers(id,project_id,workspace_id,editor_client_id,editor_window_id,workspace_root,phase,revision,payload_json,payload_sha256,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET phase=excluded.phase,revision=excluded.revision,payload_json=excluded.payload_json,payload_sha256=excluded.payload_sha256,updated_at=excluded.updated_at',
                (value['id'],pid,binding['id'],body.editor_client_id,body.editor_window_id,binding['root'],value['phase'],value['revision'],payload,digest(value),updated))
            result = {'kind':'EDITOR_OBSERVER_ACTION','id':body.operation_id,'session_ref':prepared['scope']['session_ref'],
                'generation':body.generation,'request_sha256':digest(request),'editor_client_id':body.editor_client_id,
                'editor_window_id':body.editor_window_id,'status':'RETURNED','action':body.action,
                'observer':self._public(value),'automatic_replay':False,'workspace_writes':0,'model_calls':0,'native_posts':0,
                'permission_granted':False,'atomic_editor_write_guarantee':False,
                'observation_time_scope':'AT_ORIGINAL_COMMAND_ONLY'}
            receipt = canonical(result)
            if len(receipt.encode()) > 8192: raise Conflict('Original IDE receipt exceeds its bound')
            db.execute('INSERT INTO agent_editor_observer_actions(id,project_id,session_id,operation_id,observer_id,request_sha256,payload_json,payload_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (digest({'project_id':pid,'session_id':sid,'operation_id':body.operation_id}),pid,sid,body.operation_id,value['id'],digest(request),receipt,digest(result),updated))
        self.owner.editor_captures.broker.changed()
        return result

    def assert_permission(self, manifest, request_ref):
        ref = manifest['request'].get('workspace_ref')
        if ref:
            binding = self.owner.workspaces.get(manifest['project_id'],ref['id'],ref['revision'])
            with self.owner.store.connect() as db:
                assert_observers_clean(db,binding['root'],identity=binding['root_identity'],permission_ref=request_ref,target={'project_id':manifest['project_id'],'session_id':manifest['id']})
