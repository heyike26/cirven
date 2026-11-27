"""Durable on-demand paired-window capture cohorts; never a permission grant.

Only a bounded set of registered overlapping observers is addressed. Recipients
receive their own observer ID and an opaque purpose digest, not another
project's conversation, permission, document text, credentials or native path.
"""
import json
import time
import uuid
from pydantic import Field

from packages.domain.questions import Id
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.domain.editor_observer_scope import relevant_observers, PERMISSION_FRESH_SECONDS
from packages.domain.editor_capture_scope import (decode_capture, member_identity, MAX_COHORTS,
    MAX_ACTIVE_COHORTS, MAX_CAPTURE_PAYLOAD, CAPTURE_LIFETIME_MS, capture_ack)
from .models import SessionControl, WorkspaceRef, PendingRef
from .editor_observers import process_ended


class EditorCaptureRequest(SessionControl):
    # Startup metadata can be captured before the first original owner exists.
    # This does not admit any execution/control command at generation zero.
    generation: int = Field(ge=0, strict=True)
    workspace_ref: WorkspaceRef
    editor_client_id: Id
    permission_ref: PendingRef | None = None


def member_matches(db, capture_ref, observer):
    row = db.execute('SELECT * FROM agent_editor_capture_cohorts WHERE capture_ref=?',(capture_ref,)).fetchone()
    workspace = db.execute('SELECT * FROM workspace_editor_capture_cohorts WHERE capture_ref=?',(capture_ref,)).fetchone()
    if row is not None and workspace is not None:raise Conflict('EDITOR_CAPTURE_INVALID: ambiguous capture reference')
    if row is None:row=workspace
    if row is None:raise NotFound('Original editor capture request does not exist')
    value = decode_capture(row); current_ms = int(time.time()*1000)
    if not value['created_at_ms'] <= current_ms <= value['expires_at_ms']:
        raise Conflict('EDITOR_CAPTURE_EXPIRED: the original capture request expired')
    selected = next((v for v in value['members'] if v['id'] == observer['id']),None)
    if selected is None or selected != member_identity(observer):
        raise Conflict('EDITOR_CAPTURE_MEMBER_CHANGED: this original window is outside the frozen capture membership')
    return value


def check_capture_capacity(db,runtime_id):
    total=db.execute('SELECT (SELECT COUNT(*) FROM agent_editor_capture_cohorts)+(SELECT COUNT(*) FROM workspace_editor_capture_cohorts)').fetchone()[0]
    if total>=MAX_COHORTS:raise Conflict('EDITOR_CAPTURE_CAPACITY: preserve original capture records; no new request accepted')
    current_ms=int(time.time()*1000)
    active=db.execute('SELECT (SELECT COUNT(*) FROM agent_editor_capture_cohorts WHERE runtime_id=? AND expires_at_ms>=?)+(SELECT COUNT(*) FROM workspace_editor_capture_cohorts WHERE runtime_id=? AND expires_at_ms>=?)',(runtime_id,current_ms,runtime_id,current_ms)).fetchone()[0]
    if active>=MAX_ACTIVE_COHORTS:raise Conflict('EDITOR_CAPTURE_CAPACITY: active capture request bound reached')
    return current_ms


def capture_projection(value,reference,values,*,runtime_id,current_runtime,target_current):
    """Shared observer/membership/time semantics for two distinct target kinds."""
    membership=sorted((member_identity(v) for v in values),key=lambda v:v['id'])
    current_ms=int(time.time()*1000);rows=[];dispatch=[];state='READY'
    if current_runtime!=runtime_id or value['runtime_id']!=runtime_id:state='RESTARTED'
    elif not target_current:state='TARGET_CHANGED'
    elif membership!=value['members']:state='MEMBERSHIP_CHANGED'
    elif not value['created_at_ms']<=current_ms<=value['expires_at_ms']:state='EXPIRED'
    else:
        for member in values:
            observation=member['observation'];ack=capture_ack(member,reference);age=current_ms-ack if ack is not None else None
            status=('OWNER_ENDED' if process_ended(member['process_identity']) else
                'WAITING' if ack is None else
                'UNAVAILABLE' if observation['availability']!='OBSERVED' or not observation['documents_complete'] else
                'DIRTY' if any(d['is_dirty'] for d in observation['documents']) else
                'STALE' if age<0 or age>PERMISSION_FRESH_SECONDS*1000 else 'READY')
            rows.append(status)
            if status=='WAITING':dispatch.append({'observer_id':member['id'],'editor_client_id':member['editor_client_id'],
                'editor_window_id':member['editor_window_id'],'project_id':member['project_id'],
                'capture_ref':reference,'expires_at_ms':value['expires_at_ms']})
        if any(v in {'OWNER_ENDED','UNAVAILABLE','DIRTY','STALE'} for v in rows):
            state=next(v for v in ('DIRTY','UNAVAILABLE','OWNER_ENDED','STALE') if v in rows)
        elif 'WAITING' in rows:state='WAITING'
    result={'kind':'WORKSPACE_EDITOR_CAPTURE_STATUS' if value.get('target_kind')=='WORKSPACE_EDIT' else 'EDITOR_CAPTURE_STATUS',
        'id':value['operation_id'],'capture_ref':reference,'scope':value['scope'],
        'editor_client_id':value['editor_client_id'],'request_sha256':value['request_sha256'],'state':state,
        'required_windows':len(value['members']),'ready_windows':rows.count('READY'),
        'waiting_windows':rows.count('WAITING'),'dirty_windows':rows.count('DIRTY'),
        'unavailable_windows':sum(rows.count(v) for v in ('UNAVAILABLE','OWNER_ENDED','STALE')),
        'expires_at_ms':value['expires_at_ms'],'read_only':True,'permission_granted':False,
        'automatic_replay':False,'workspace_writes':0,'model_calls':0,'native_posts':0,
        'atomic_editor_write_guarantee':False,'status_scope':'CURRENT_METADATA_SNAPSHOT_NOT_EXECUTION_LEASE'}
    result['dispatch_requests']=dispatch
    return result


class EditorCaptures:
    def __init__(self, owner):
        self.owner = owner
        from .editor_capture_broker import DesktopCaptureBroker
        self.broker = DesktopCaptureBroker(self)
        from .workspace_editor_captures import WorkspaceEditorCaptures
        self.workspace = WorkspaceEditorCaptures(self)

    def _scope(self,pid,sid,body):
        manifest, ref = self.owner._manifest(pid,sid,body.session_ref.model_dump())
        workspace = body.workspace_ref.model_dump();state = self.owner._state(pid,sid)
        if manifest['request'].get('workspace_ref') != workspace or state['generation'] != body.generation:
            raise Conflict('Original capture scope, generation or workspace changed')
        binding = self.owner.workspaces.get(pid,workspace['id'],workspace['revision'])
        if binding['binding_sha256'] != workspace['binding_sha256'] or binding['root_identity'] != manifest['directory_identity']:
            raise Conflict('Original capture workspace identity changed')
        permission = body.permission_ref.model_dump() if body.permission_ref else None
        if permission is not None and (permission['generation'] != body.generation or not any(
                p.get('request_ref') == permission and p.get('native_request',{}).get('permission') == 'edit'
                for p in state.get('pending_permissions',[]))):
            raise Conflict('The exact original edit permission is no longer pending')
        return {'project_id':pid,'session_ref':ref,'generation':body.generation,'workspace_ref':workspace},binding,permission

    def _row(self,db,pid,sid,operation):
        return db.execute('SELECT * FROM agent_editor_capture_cohorts WHERE project_id=? AND session_id=? AND operation_id=?',(pid,sid,operation)).fetchone()

    def _receipt(self,value,reference):
        return {'kind':'EDITOR_CAPTURE_COHORT','id':value['operation_id'],'capture_ref':reference,
            'scope':value['scope'],'editor_client_id':value['editor_client_id'],
            'request_sha256':value['request_sha256'],'required_windows':len(value['members']),
            'created_at_ms':value['created_at_ms'],'expires_at_ms':value['expires_at_ms'],
            'read_only':True,'automatic_replay':False,'permission_granted':False,
            'workspace_writes':0,'model_calls':0,'native_posts':0,'atomic_editor_write_guarantee':False,
            'observation_time_scope':'IMMUTABLE_CAPTURE_INTENT_NOT_CURRENT_CLEAN_STATE'}

    def request(self,pid,sid,body,*,original_action=None):
        body = EditorCaptureRequest.model_validate(body);request = body.model_dump();request_sha = digest(request)
        with self.owner.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');prior = self._row(db,pid,sid,body.operation_id)
            if prior is not None:
                value = decode_capture(prior)
                if value['request_sha256'] != request_sha or value['editor_client_id'] != body.editor_client_id:
                    raise Conflict('IDEMPOTENCY_CONFLICT: original editor capture input differs')
                if value.get('original_action') != original_action:
                    raise Conflict('IDEMPOTENCY_CONFLICT: original capture action differs')
                return self._receipt(value,prior['capture_ref'])
            runtime=db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
            if not runtime or runtime['runtime_id']!=self.owner.editor_observers.runtime_id:
                raise Conflict('EDITOR_CAPTURE_RESTARTED: the original capture service instance changed')
            scope,binding,permission = self._scope(pid,sid,body)
            current_ms = check_capture_capacity(db,self.owner.editor_observers.runtime_id)
            members = sorted((member_identity(v) for v in relevant_observers(db,binding['root'],identity=binding['root_identity'])),key=lambda v:v['id'])
            value = {'id':digest({'project_id':pid,'session_id':sid,'operation_id':body.operation_id}),
                'project_id':pid,'session_id':sid,'operation_id':body.operation_id,'editor_client_id':body.editor_client_id,
                'request_sha256':request_sha,'scope':scope,'workspace_root':binding['root'],'root_identity':binding['root_identity'],
                'permission_ref':permission,'members':members,'nonce':uuid.uuid4().hex,
                'created_at_ms':current_ms,'expires_at_ms':current_ms+CAPTURE_LIFETIME_MS,'runtime_id':self.owner.editor_observers.runtime_id}
            if original_action is not None:value['original_action']=original_action
            payload = canonical(value)
            if len(payload.encode()) > MAX_CAPTURE_PAYLOAD:raise Conflict('EDITOR_CAPTURE_CAPACITY: capture metadata exceeds its bound')
            reference = digest(value)
            db.execute('INSERT INTO agent_editor_capture_cohorts(id,project_id,session_id,operation_id,editor_client_id,capture_ref,request_sha256,payload_json,payload_sha256,expires_at_ms,runtime_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (value['id'],pid,sid,body.operation_id,body.editor_client_id,reference,request_sha,payload,reference,value['expires_at_ms'],value['runtime_id'],now()))
        return self._receipt(value,reference)

    def _original(self,db,pid,sid,operation,client):
        self.owner._manifest(pid,sid);row = self._row(db,pid,sid,operation)
        if row is None:raise NotFound('Original editor capture request does not exist')
        value = decode_capture(row)
        if value['editor_client_id'] != client:raise Conflict('Original capture requester differs')
        return value,row['capture_ref']

    def operation(self,pid,sid,operation,*,client):
        with self.owner.store.connect() as db:
            value,reference = self._original(db,pid,sid,operation,client)
        return self._receipt(value,reference)

    def status(self,pid,sid,operation,*,client,include_dispatch=False):
        with self.owner.store.connect() as db:
            # A coherent current snapshot only. It is not an execution lease.
            db.execute('BEGIN');value,reference = self._original(db,pid,sid,operation,client)
            values = relevant_observers(db,value['workspace_root'],identity=value['root_identity'])
            target = self.owner._state(pid,sid)
            permission_current = value['permission_ref'] is None or any(p.get('request_ref')==value['permission_ref'] and p.get('native_request',{}).get('permission')=='edit' for p in target.get('pending_permissions',[]))
            runtime=db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
            target_current=target['generation']==value['scope']['generation'] and permission_current
            action=value.get('original_action') or {}
            if action.get('kind')=='MODEL_RELAY':
                context=self.owner.active.get(sid)
                target_current=bool(target_current and context and not context.get('closing')
                    and context.get('generation')==value['scope']['generation'] and target.get('owner_status')=='READY'
                    and target.get('active_operation_id')==action['operation_id'])
            result=capture_projection(value,reference,values,runtime_id=self.owner.editor_observers.runtime_id,
                current_runtime=runtime['runtime_id'] if runtime else None,
                target_current=target_current)
            if not include_dispatch:result.pop('dispatch_requests')
        return result
