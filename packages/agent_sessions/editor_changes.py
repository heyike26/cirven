"""Durable original-workspace handoff to a paired native editor.

No filesystem mutation occurs here. The grant covers one exact historical
before/after selection; original starts/forwards are blocked until that editor
explicitly resolves saved bytes or an untouched cancelled handoff.
"""
from contextlib import contextmanager
import hashlib
import json
import threading
import time
from typing import Literal

from pydantic import Field

from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.domain.editor_handoff_scope import same_workspace, assert_handoff_free
from .diff_reviews import DiffPreparation, DiffReference, DiffReviews
from .models import SessionControl

def editor_location(value):
    """Preserve 64-bit native directory identity across the JSON/JS boundary."""
    result=json.loads(json.dumps(value))
    for key in ('device','inode'):
        raw=result['parent_ref'][key]
        if type(raw) is not int or raw<0:raise Conflict('Original editor directory identity is invalid')
        result['parent_ref'][key]=str(raw)
    return result


def text_hash(text):
    # VS Code stores UTF-8 BOM as encoding metadata rather than document text.
    return hashlib.sha256(text.removeprefix('\ufeff').encode('utf-8')).hexdigest()


class EditorChangeRequest(DiffPreparation):
    operation_id: Id
    preparation_sha256: Hash
    direction: Literal['RESTORE_BEFORE','APPLY_AFTER']
    editor_client_id: Id
    editor_window_id: Id
    editor_nonce: Hash


class EditorBasis(RequestModel):
    document_version: int=Field(ge=1,le=2147483647,strict=True)
    content_sha256: Hash
    is_dirty: bool=Field(strict=True)


class EditorChangeControl(SessionControl):
    target_operation_id: Id
    lease_ref: DiffReference | None=None
    editor_client_id: Id
    editor_window_id: Id
    editor_nonce: Hash
    expected_revision: int=Field(ge=1,le=64,strict=True)
    action: Literal['ARM','BUFFER_CHANGED','RESOLVE_SAVED','RESOLVE_CANCELLED','RESOLVE_MANUAL_SAVED']
    editor_basis: EditorBasis | None=None
    expected_saved_sha256: Hash | None=None
    user_approved_manual_saved: bool=Field(default=False,strict=True)


class EditorChanges:
    def __init__(self,owner):
        self.owner=owner;self.reviews=DiffReviews(owner)
        self.starting={};self.start_lock=threading.RLock()

    @staticmethod
    def _decode(row):
        if row is None:raise NotFound('Original editor handoff does not exist')
        value=json.loads(row['payload_json'])
        if digest(value)!=row['payload_sha256'] or len(row['payload_json'].encode())>32768:
            raise Conflict('Original editor handoff integrity changed')
        if (value['id']!=row['id'] or value['project_id']!=row['project_id'] or value['session_id']!=row['session_id']
                or value['operation_id']!=row['operation_id'] or value['request_sha256']!=row['request_sha256']
                or value['workspace_root']!=row['workspace_root'] or value['generation']!=row['generation']):
            raise Conflict('Original editor handoff index differs from its proof')
        return {**value,'phase':row['phase'],'revision':row['revision'],'updated_at':row['updated_at']}

    def _row(self,db,pid,sid,operation):
        return db.execute('SELECT * FROM agent_editor_changes WHERE project_id=? AND session_id=? AND operation_id=?',
            (pid,sid,operation)).fetchone()

    def _active(self,db):
        rows=db.execute("SELECT * FROM agent_editor_changes WHERE phase IN ('RESERVING','READY','ARMED','BUFFER_CHANGED','UNKNOWN_OUTCOME') ORDER BY id LIMIT 17").fetchall()
        if len(rows)>16:raise Conflict('Original editor handoff active bound exceeded')
        return [self._decode(row) for row in rows]

    def assert_available(self,root,*,identity=None,connection=None,except_id=None):
        if connection is not None:
            assert_handoff_free(connection,root,identity=identity,except_id=except_id)
        else:
            with self.owner.store.connect() as db:
                assert_handoff_free(db,root,identity=identity,except_id=except_id)

    def assert_manifest(self,manifest,*,connection=None):
        ref=manifest['request'].get('workspace_ref')
        if ref:
            binding=self.owner.workspaces.get(manifest['project_id'],ref['id'],ref['revision'])
            self.assert_available(binding['root'],identity=binding['root_identity'],connection=connection)

    @contextmanager
    def starting_owner(self,pid,sid):
        manifest,_=self.owner._manifest(pid,sid)
        ref=manifest['request'].get('workspace_ref')
        binding=self.owner.workspaces.get(pid,ref['id'],ref['revision']) if ref else None
        root=binding['root'] if binding else None
        with self.start_lock:
            if root:self.assert_available(root,identity=binding['root_identity'])
            if (pid,sid) in self.starting:raise Conflict('Original owner start is already in progress')
            self.starting[(pid,sid)]=None if binding is None else {'root':root,'identity':binding['root_identity']}
        try:yield
        finally:
            with self.start_lock:self.starting.pop((pid,sid),None)

    def _no_starting(self,root,identity=None):
        with self.start_lock:
            if any(value and same_workspace(root,identity,value['root'],value['identity']) for value in self.starting.values()):
                raise Conflict('An overlapping original owner is starting; no editor handoff was granted')

    def _cached_quiet(self,pid,sid,root,identity=None):
        for key,ctx in list(self.owner.active.items()):
            if not same_workspace(root,identity,ctx['directory'],ctx.get('workspace_root_identity')):continue
            project=ctx.get('project_id') or (pid if key==sid else None)
            if project is None:raise Conflict('Original owner project identity was not registered')
            state=self.owner._state(project,key)
            if (state.get('active_operation_id') or state.get('native_status')!='idle'
                    or state.get('pending_questions') or state.get('pending_permissions')
                    or ctx.get('closing') or ctx.get('native_commands_inflight') or ctx.get('interrupt_barrier')):
                raise Conflict('Resolve overlapping original work before reserving an editor handoff')

    async def _quiescent(self,pid,sid,root,identity=None):
        self._no_starting(root,identity)
        active=list(self.owner.active.items())
        if len(active)>64:raise Conflict('Original owner observation bound exceeded')
        selected=[(key,ctx) for key,ctx in active if same_workspace(root,identity,ctx['directory'],ctx.get('workspace_root_identity'))]
        if len(selected)>16:raise Conflict('Overlapping original owner bound exceeded')
        deadline=time.monotonic()+6
        for key,ctx in selected:
            project=ctx.get('project_id') or (pid if key==sid else None)
            if project is None:raise Conflict('Original owner project identity was not registered')
            state=self.owner._state(project,key)
            if (ctx.get('closing') or not ctx.get('process') or ctx['process'].returncode is not None
                    or state.get('generation')!=ctx['generation'] or state.get('active_operation_id')
                    or state.get('pending_questions') or state.get('pending_permissions')
                    or ctx.get('native_commands_inflight') or ctx.get('interrupt_barrier')):
                raise Conflict('Resolve overlapping original work before handing a file to the editor')
            async with ctx['lock']:
                for route in ('/session/status','/question','/permission'):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise Conflict('Original owner idle observation deadline exceeded')
                    result=await self.owner._http(ctx,'GET',route,timeout=min(1.5,remaining))
                    if route=='/session/status':
                        if not isinstance(result,dict) or any(not isinstance(v,dict) or v.get('type')!='idle' for v in result.values()):
                            raise Conflict('An original session is still running or has unknown status')
                    elif not isinstance(result,list) or result:
                        raise Conflict('An original session still has pending questions or permissions')
                latest=self.owner._state(project,key)
                if (self.owner.active.get(key) is not ctx or ctx.get('closing')
                        or latest.get('generation')!=ctx['generation'] or latest.get('active_operation_id')
                        or ctx.get('native_commands_inflight')):
                    raise Conflict('Original owner changed during editor handoff observation')
        self._no_starting(root,identity)

    @staticmethod
    def _lease_ref(value):
        grant=value.get('grant')
        return None if grant is None else {'id':value['id'],'revision':1,'sha256':digest(grant)}

    @staticmethod
    def _public(value):
        # Root paths remain private; Main resolves the exact FileSource to a URI.
        return {'kind':'EDITOR_CHANGE','id':value['operation_id'],'session_ref':value['request']['session_ref'],
            'generation':value['generation'],'request_sha256':value['request_sha256'],
            'lease_ref':EditorChanges._lease_ref(value),'phase':value['phase'],'revision':value['revision'],
            'grant':value.get('grant'),'last_editor_observation':value.get('last_editor_observation'),
            'observed_saved':value.get('observed_saved'),'automatic_replay':False,
            'workspace_writes':0,'model_calls':0,'native_posts':0,'independent_verification':'NOT_RUN',
            'native_editor_effect':'CLIENT_REPORTED_NOT_INDEPENDENTLY_VERIFIED','first_reservation_reply':False,
            'editor_client_id':value['request']['editor_client_id'],'editor_window_id':value['request']['editor_window_id']}

    def inspect(self,pid,sid,operation,*,client=None):
        self.owner._manifest(pid,sid)
        with self.owner.store.connect() as db:value=self._decode(self._row(db,pid,sid,operation))
        if client is not None and value['request']['editor_client_id']!=client:
            raise Conflict('Editor handoff belongs to another paired client')
        return self._public(value)

    def _save(self,db,value,phase,revision):
        payload={k:v for k,v in value.items() if k not in {'phase','revision','updated_at'}}
        if len(canonical(payload).encode())>32768:raise Conflict('Original editor handoff metadata exceeds its bound')
        db.execute('UPDATE agent_editor_changes SET phase=?,revision=?,payload_json=?,payload_sha256=?,updated_at=? WHERE id=? AND revision=?',
            (phase,revision,canonical(payload),digest(payload),now(),value['id'],value['revision']))
        if db.execute('SELECT changes()').fetchone()[0]!=1:raise Conflict('Original editor handoff revision changed')
        return {**payload,'phase':phase,'revision':revision,'updated_at':now()}

    async def reserve(self,pid,sid,body):
        body=body if isinstance(body,EditorChangeRequest) else EditorChangeRequest.model_validate(body)
        manifest,reference,state,binding=self.reviews._scope(pid,sid,body)
        if self.owner.closed or manifest.get('native_review_admission') or manifest.get('native_message_admission'):
            raise Conflict('Original public workspace authority is required for this editor handoff')
        if 'WORKSPACE_WRITE' not in manifest['request'].get('allowed_effects',[]):
            raise Conflict('Historical viewing does not grant workspace editing')
        request=body.model_dump();old,created=self.owner._claim(pid,sid,body.operation_id,'EDITOR_CHANGE',request)
        if not created:
            return self.inspect(pid,sid,body.operation_id,client=body.editor_client_id)
        inserted=False;attempted_insert=False
        try:
            selection=DiffPreparation.model_validate(self.reviews._selection(body))
            prepared=await self.reviews.prepare(pid,sid,selection)
            if prepared['preparation_sha256']!=body.preparation_sha256:
                raise Conflict('Original saved-byte editor preparation changed')
            detail=prepared['diff'];target='before' if body.direction=='RESTORE_BEFORE' else 'after'
            source='after' if target=='before' else 'before'
            if not prepared['matches_'+source]:raise Conflict('Saved bytes differ from the exact opposite historical version; manual content is retained')
            scopes=binding['allowed_write_paths'];path=detail['path']
            if not any(p=='*' or p==path or p.endswith('/') and path.startswith(p) for p in scopes):
                raise Conflict('The historical file is outside the original current write grant')
            ident=digest({'project_id':pid,'session_id':sid,'operation_id':body.operation_id})
            value={'id':ident,'project_id':pid,'session_id':sid,'operation_id':body.operation_id,
                'generation':body.generation,'workspace_root':binding['root'],'root_identity':binding['root_identity'],'request':request,
                'request_sha256':digest(request),'grant':None,'created_at':now()}
            with self.owner.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');self._no_starting(binding['root'],binding['root_identity']);self.assert_available(binding['root'],identity=binding['root_identity'],connection=db)
                if len(self._active(db))>=16:raise Conflict('Original editor handoff concurrency bound is full')
                count=db.execute('SELECT COUNT(*) FROM agent_editor_changes').fetchone()[0]
                if count>=2048:raise Conflict('Original editor handoff history is full; preserve and manage existing evidence')
                self._cached_quiet(pid,sid,binding['root'],binding['root_identity']);attempted_insert=True
                db.execute('INSERT INTO agent_editor_changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident,pid,sid,body.operation_id,body.generation,body.workspace_ref.id,binding['root'],digest(request),
                     'RESERVING',1,canonical(value),digest(value),now()))
            inserted=True
            await self._quiescent(pid,sid,binding['root'],binding['root_identity'])
            _,_,current,binding=self.reviews._scope(pid,sid,body)
            if current.get('active_operation_id') or self.reviews._disk(binding,path)!=prepared['current_saved']:
                raise Conflict('Original owner or saved bytes changed before editor grant')
            existing=prepared['current_saved']['exists'];target_exists=detail['status']!=('added' if target=='before' else 'deleted')
            source_ref=None;location=None
            if existing:
                registered=self.owner.workspaces.read(pid,body.workspace_ref.id,path,max_bytes=524288)
                if not registered['current'] or registered['source_ref']['sha256']!=prepared['current_saved']['sha256']:
                    raise Conflict('Registered editor source differs from the prepared saved bytes')
                source_ref=registered['source_ref']
                location=editor_location(self.owner.workspaces.file_location(pid,body.workspace_ref.id,path,
                    binding_revision=body.workspace_ref.revision,binding_sha256=body.workspace_ref.binding_sha256))
            else:
                location=editor_location(self.owner.workspaces.file_slot_location(pid,body.workspace_ref.id,path,
                    binding_revision=body.workspace_ref.revision,binding_sha256=body.workspace_ref.binding_sha256))
            before_text=detail[source];after_text=detail[target]
            grant={'scope':prepared['scope'],'selection':selection.model_dump(),'direction':body.direction,
                'editor_client_id':body.editor_client_id,'editor_window_id':body.editor_window_id,'editor_nonce':body.editor_nonce,
                'path':path,'source_ref':source_ref,'file_location':location,'saved_basis':prepared['current_saved'],
                'target_exists':target_exists,'target_sha256':detail[target+'_sha256'] if target_exists else None,
                'source_editor_sha256':text_hash(before_text),'target_editor_sha256':text_hash(after_text),
                'source_has_bom':before_text.startswith('\ufeff'),'target_has_bom':after_text.startswith('\ufeff'),
                'mode':'TEXT_REPLACE' if existing and target_exists else 'FILE_CREATE' if target_exists else 'FILE_DELETE'}
            with self.owner.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');value=self._decode(self._row(db,pid,sid,body.operation_id))
                if value['phase']!='RESERVING':raise Conflict('Original editor handoff changed before grant')
                self._no_starting(binding['root'],binding['root_identity']);self.reviews._scope(pid,sid,body)
                if self.reviews._disk(binding,path)!=prepared['current_saved']:raise Conflict('Saved editor basis changed before grant publication')
                value['grant']=grant;value=self._save(db,value,'READY',value['revision']+1)
            result=self._public(value)
            self.owner._finish(pid,sid,body.operation_id,'ACCEPTED',generation=body.generation,
                editor_lease_ref=result['lease_ref'],workspace_writes=0,model_calls=0,native_posts=0)
            return {**result,'first_reservation_reply':True}
        except Exception as error:
            # No native edit was permitted while RESERVING. A published READY
            # grant is held on acknowledgement loss, never released by timeout.
            phase='FAILED'
            if inserted or attempted_insert:
                with self.owner.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE');row=self._row(db,pid,sid,body.operation_id)
                    value=self._decode(row) if row is not None else None
                    if value is None:pass
                    elif value['phase'] in {'RESERVING','READY'}:
                        phase='FAILED' if value['phase']=='RESERVING' else 'UNKNOWN_OUTCOME'
                        value['error_type']=type(error).__name__;self._save(db,value,phase,value['revision']+1)
                    else:phase=value['phase']
            self.owner._finish(pid,sid,body.operation_id,'FAILED' if phase=='FAILED' else 'CANCELLED' if phase=='CANCELLED' else 'UNKNOWN_OUTCOME',
                generation=body.generation,reason=type(error).__name__,workspace_writes=0,model_calls=0,native_posts=0)
            raise

    def control(self,pid,sid,body):
        body=body if isinstance(body,EditorChangeControl) else EditorChangeControl.model_validate(body)
        self.owner._manifest(pid,sid,body.session_ref.model_dump())
        old,created=self.owner._claim(pid,sid,body.operation_id,'EDITOR_CHANGE_CONTROL',body.model_dump())
        if not created:return self.control_operation(pid,sid,body.operation_id,client=body.editor_client_id)
        published=False
        try:
            with self.owner.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');v=self._decode(self._row(db,pid,sid,body.target_operation_id));grant=v['grant']
                expected_ref=None if body.lease_ref is None else body.lease_ref.model_dump()
                if (self._lease_ref(v)!=expected_ref or v['revision']!=body.expected_revision
                        or v['request']['session_ref']!=body.session_ref.model_dump() or v['generation']!=body.generation
                        or any(v['request'][k]!=getattr(body,k) for k in ('editor_client_id','editor_window_id','editor_nonce'))):
                    raise Conflict('Original editor handoff, actor or revision changed')
                if v.get('file_lifecycle_intent',{}).get('file_effect')=='NOT_YET_OBSERVED':
                    raise Conflict('Original file lifecycle intent requires its exact action or explicit file-state recovery')
                basis=body.editor_basis
                if grant is None:
                    if v['phase']!='RESERVING' or body.action!='RESOLVE_CANCELLED' or basis is not None:
                        raise Conflict('An ungranted handoff only permits explicit cancellation')
                    observed=None;phase='CANCELLED'
                else:
                    binding=self.owner.workspaces.get(pid,grant['scope']['workspace_ref']['id'],grant['scope']['workspace_ref']['revision'])
                    if binding['binding_sha256']!=grant['scope']['workspace_ref']['binding_sha256'] or binding['root']!=v['workspace_root']:
                        raise Conflict('Original editor workspace binding changed')
                    observed=self.reviews._disk(binding,grant['path']);basis=body.editor_basis
                    if body.action=='ARM':
                        location=editor_location(self.owner.workspaces.file_location(pid,binding['id'],grant['path'],
                            binding_revision=binding['revision'],binding_sha256=binding['binding_sha256']))
                        if location!=grant['file_location']:
                            raise Conflict('Original editor parent directory or stored filename changed before native intent')
                        if (v['phase']!='READY' or observed!=grant['saved_basis'] or basis is None or basis.is_dirty
                                or basis.content_sha256!=grant['source_editor_sha256'] or grant['mode']!='TEXT_REPLACE'):
                            raise Conflict('Exact clean editor and saved basis are required before this text-edit intent')
                        phase='ARMED';v['armed_editor_basis']=basis.model_dump()
                    elif body.action=='BUFFER_CHANGED':
                        if (v['phase']!='ARMED' or basis is None or basis.content_sha256!=grant['target_editor_sha256']
                                or basis.document_version<=v['armed_editor_basis']['document_version']):
                            raise Conflict('The original native editor result was not observed')
                        phase='BUFFER_CHANGED'
                    elif body.action=='RESOLVE_SAVED':
                        location=editor_location(self.owner.workspaces.file_location(pid,binding['id'],grant['path'],
                            binding_revision=binding['revision'],binding_sha256=binding['binding_sha256']))
                        if location!=grant['file_location']:
                            raise Conflict('Original editor parent changed; inspect the saved manual result instead')
                        if (v['phase'] not in {'ARMED','BUFFER_CHANGED','UNKNOWN_OUTCOME'} or basis is None or basis.is_dirty
                                or not observed['exists'] or observed['sha256']!=grant['target_sha256']
                                or basis.content_sha256!=grant['target_editor_sha256']):
                            raise Conflict('Exact target saved bytes and a clean editor are required to resolve this handoff')
                        phase='SAVED'
                    elif body.action=='RESOLVE_CANCELLED':
                        if v['phase']=='READY' or v['phase']=='UNKNOWN_OUTCOME' and not v.get('armed_editor_basis'):
                            if observed!=grant['saved_basis']:raise Conflict('Saved basis changed before cancellation')
                        elif (v['phase'] not in {'ARMED','BUFFER_CHANGED','UNKNOWN_OUTCOME'} or observed!=grant['saved_basis']
                                or basis is None or basis.is_dirty or basis.content_sha256!=grant['source_editor_sha256']):
                            raise Conflict('Cancellation requires an untouched or explicitly restored clean editor')
                        phase='CANCELLED'
                    else:
                        if (v['phase'] not in {'READY','ARMED','BUFFER_CHANGED','UNKNOWN_OUTCOME'} or not body.user_approved_manual_saved
                                or basis is None or basis.is_dirty or not observed['exists'] or observed['sha256']!=body.expected_saved_sha256):
                            raise Conflict('Explicit current manual saved-byte resolution is required')
                        saved=self.owner.workspaces._raw(binding,grant['path'],max_bytes=524288).decode('utf-8')
                        if text_hash(saved)!=basis.content_sha256:raise Conflict('The clean editor does not match the manual saved text')
                        phase='MANUAL_SAVED'
                v['last_editor_observation']=None if basis is None else basis.model_dump();v['observed_saved']=observed
                controls=v.get('control_receipts',[])
                if len(controls)>=8:raise Conflict('Original editor control receipt bound exceeded')
                controls.append({'operation_id':body.operation_id,'request_sha256':digest(body.model_dump()),
                    'phase':phase,'revision':v['revision']+1,'last_editor_observation':v['last_editor_observation'],
                    'observed_saved':observed})
                v['control_receipts']=controls;v['last_control_operation_id']=body.operation_id
                published=True
                value=self._save(db,v,phase,v['revision']+1)
            result=self._public(value)
            receipt=self.owner._finish(pid,sid,body.operation_id,'RETURNED',generation=body.generation,
                editor_change=result,workspace_writes=0,model_calls=0,native_posts=0)
            return {**receipt,'first_control_reply':True}
        except Exception as error:
            self.owner._finish(pid,sid,body.operation_id,'UNKNOWN_OUTCOME' if published else 'FAILED',generation=body.generation,reason=type(error).__name__,workspace_writes=0)
            raise


    def control_operation(self,pid,sid,operation,*,client=None):
        old=self.owner.operation(pid,sid,operation)
        if old['kind']!='EDITOR_CHANGE_CONTROL':raise Conflict('The original operation is not an editor control')
        body=EditorChangeControl.model_validate(old['request'])
        if digest(body.model_dump())!=old['request_sha256'] or client is not None and body.editor_client_id!=client:
            raise Conflict('Original editor control request or paired actor changed')
        if old['status'] in {'RECORDED_NOT_DISPATCHED','UNKNOWN_OUTCOME'}:
            with self.owner.store.connect() as db:v=self._decode(self._row(db,pid,sid,body.target_operation_id))
            matches=[r for r in v.get('control_receipts',[]) if r['operation_id']==operation and r['request_sha256']==old['request_sha256']]
            if len(matches)==1:
                result=self._public({**v,**{k:matches[0][k] for k in ('phase','revision','last_editor_observation','observed_saved')}})
                old=self.owner._finish(pid,sid,operation,'RETURNED',generation=body.generation,editor_change=result,
                    workspace_writes=0,model_calls=0,native_posts=0,recovered_editor_metadata=True)
        return {**old,'first_control_reply':False}
