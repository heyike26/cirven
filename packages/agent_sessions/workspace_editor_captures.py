"""Workspace-edit metadata cohorts without an invented or borrowed session.

Only an exact edit digest and registered window metadata are captured. Original
filesystem grant/CAS/unknown-write handling remain in LocalWorkspaces.edit.
"""
import uuid

from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.domain.editor_observer_scope import relevant_observers
from packages.domain.editor_capture_scope import (decode_capture, member_identity,
    MAX_CAPTURE_PAYLOAD, CAPTURE_LIFETIME_MS)
from .models import WorkspaceRef
from .editor_captures import check_capture_capacity, capture_projection


class WorkspaceCaptureRequest(RequestModel):
    operation_id: Id
    workspace_ref: WorkspaceRef
    editor_client_id: Id
    edit_operation_id: Id
    edit_request_sha256: Hash


class WorkspaceEditorCaptures:
    def __init__(self,captures):self.captures=captures;self.owner=captures.owner

    def _scope(self,pid,wid,body):
        ref=body.workspace_ref.model_dump()
        binding=self.owner.workspaces.get(pid,wid,ref['revision'])
        if ref['id']!=wid or ref['binding_sha256']!=binding['binding_sha256']:
            raise Conflict('Original workspace edit capture binding differs')
        return {'project_id':pid,'workspace_ref':ref,'edit_operation_id':body.edit_operation_id,
            'edit_request_sha256':body.edit_request_sha256},binding

    def _row(self,db,pid,wid,operation):
        return db.execute('SELECT * FROM workspace_editor_capture_cohorts WHERE project_id=? AND workspace_id=? AND operation_id=?',(pid,wid,operation)).fetchone()

    def _receipt(self,value,reference):
        result=self.captures._receipt(value,reference)
        result['kind']='WORKSPACE_EDITOR_CAPTURE_COHORT'
        return result

    def request(self,pid,wid,body):
        body=WorkspaceCaptureRequest.model_validate(body);request=body.model_dump()
        with self.owner.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=self._row(db,pid,wid,body.operation_id)
            if row is not None:
                value=decode_capture(row)
                if value['editor_client_id']!=body.editor_client_id or value['request_sha256']!=digest(request):
                    raise Conflict('IDEMPOTENCY_CONFLICT: workspace capture input differs')
                return self._receipt(value,row['capture_ref'])
            scope,binding=self._scope(pid,wid,body)
            runtime=db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
            if not runtime or runtime['runtime_id']!=self.owner.editor_observers.runtime_id:
                raise Conflict('EDITOR_CAPTURE_RESTARTED: observation service changed')
            current_ms=check_capture_capacity(db,self.owner.editor_observers.runtime_id)
            members=sorted((member_identity(v) for v in relevant_observers(db,binding['root'],identity=binding['root_identity'])),key=lambda v:v['id'])
            value={'id':digest({'project_id':pid,'workspace_id':wid,'operation_id':body.operation_id}),
                'target_kind':'WORKSPACE_EDIT','project_id':pid,'workspace_id':wid,'operation_id':body.operation_id,
                'editor_client_id':body.editor_client_id,'request_sha256':digest(request),'scope':scope,
                'workspace_root':binding['root'],'root_identity':binding['root_identity'],'permission_ref':None,
                'members':members,'nonce':uuid.uuid4().hex,'created_at_ms':current_ms,
                'expires_at_ms':current_ms+CAPTURE_LIFETIME_MS,'runtime_id':self.owner.editor_observers.runtime_id}
            payload=canonical(value)
            if len(payload.encode())>MAX_CAPTURE_PAYLOAD:raise Conflict('EDITOR_CAPTURE_CAPACITY: metadata payload exceeds its bound')
            reference=digest(value)
            db.execute('INSERT INTO workspace_editor_capture_cohorts(id,project_id,workspace_id,operation_id,editor_client_id,capture_ref,request_sha256,payload_json,payload_sha256,expires_at_ms,runtime_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (value['id'],pid,wid,body.operation_id,body.editor_client_id,reference,value['request_sha256'],payload,reference,value['expires_at_ms'],value['runtime_id'],now()))
        return self._receipt(value,reference)

    def _original(self,db,pid,wid,operation,client):
        self.owner.store.project(pid);row=self._row(db,pid,wid,operation)
        if row is None:raise NotFound('Original workspace editor capture does not exist')
        value=decode_capture(row)
        if value['editor_client_id']!=client:raise Conflict('Original workspace capture requester differs')
        return value,row['capture_ref']

    def operation(self,pid,wid,operation,*,client):
        with self.owner.store.connect() as db:value,reference=self._original(db,pid,wid,operation,client)
        return self._receipt(value,reference)

    def status(self,pid,wid,operation,*,client,include_dispatch=False):
        with self.owner.store.connect() as db:
            db.execute('BEGIN');value,reference=self._original(db,pid,wid,operation,client)
            values=relevant_observers(db,value['workspace_root'],identity=value['root_identity'])
            runtime=db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
            try:
                ref=value['scope']['workspace_ref'];current=self.owner.workspaces.get(pid,wid,ref['revision'])
                target_current=current['binding_sha256']==ref['binding_sha256'] and current['root_identity']==value['root_identity'] and current['root']==value['workspace_root']
            except (Conflict,NotFound,OSError):target_current=False
            result=capture_projection(value,reference,values,runtime_id=self.owner.editor_observers.runtime_id,
                current_runtime=runtime['runtime_id'] if runtime else None,target_current=target_current)
            if not include_dispatch:result.pop('dispatch_requests')
        return result
