"""Exact historical user review, separate from tool writes and editor effects.

Preparation observes saved bytes twice. A decision changes bounded review
metadata only, never workspace bytes, a model, the SDK or another owner.
"""
import hashlib
import json
from typing import Literal

from pydantic import Field

from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.store import Conflict
from packages.domain.tasks import digest, now
from .models import SessionRef, WorkspaceRef
from .child_controls import ChildRef
from .child_observations import ChildObservations
from .diffs import file_detail, MAX_CONTENT_BYTES


class DiffReference(RequestModel):
    id: Hash
    revision: int = Field(default=1, ge=1, le=1, strict=True)
    sha256: Hash


class DiffPreparation(RequestModel):
    session_ref: SessionRef
    generation: int = Field(ge=1, strict=True)
    workspace_ref: WorkspaceRef
    snapshot_ref: DiffReference
    diff_ref: DiffReference
    child_ref: ChildRef | None = None


class DiffDecision(DiffPreparation):
    operation_id: Id
    preparation_sha256: Hash
    expected_review_revision: int = Field(ge=0, le=1024, strict=True)
    decision: Literal['ACCEPT', 'REJECT']


class DiffReviews:
    def __init__(self, owner):
        self.owner = owner

    @staticmethod
    def _selection(body):
        return {key:body.model_dump()[key] for key in DiffPreparation.model_fields}

    def _key(self, pid, body):
        # Other files entering the catalog do not erase this exact file review.
        return digest({'project_id':pid,'session_ref':body.session_ref.model_dump(),
            'generation':body.generation,'workspace_ref':body.workspace_ref.model_dump(),
            'child_ref':body.child_ref.model_dump() if body.child_ref else None,
            'diff_ref':body.diff_ref.model_dump()})

    def _record(self,pid,sid,path):
        from packages.adapters.opencode_change_review import _regular

        def unwrap(envelope):
            if (not isinstance(envelope,dict) or set(envelope)!={'value','sha256'}
                    or not isinstance(envelope['value'],dict)
                    or digest(envelope['value'])!=envelope['sha256']):
                raise Conflict('Original review metadata integrity changed')
            return envelope['value']

        try:
            record=unwrap(json.loads(_regular(path,8192)))
            # The original writer wrapped its record before calling the shared
            # writer, which added a second envelope. Verify both in place.
            if set(record)=={'value','sha256'}:
                record=unwrap(record)
            if (record.get('schema')!='eeas.diff-review.v1' or type(record.get('revision')) is not int
                    or not 1<=record['revision']<=1024 or path.name!=digest(record['operation_id'])+'.json'):
                raise Conflict('Original review metadata integrity changed')
            selection=DiffPreparation.model_validate(record['selection'])
            if selection.session_ref.id!=sid or self._key(pid,selection)!=record['key']:
                raise Conflict('Original review session or project differs')
            request={**selection.model_dump(),'operation_id':record['operation_id'],'preparation_sha256':record['preparation_sha256'],
                'expected_review_revision':record['revision']-1,'decision':record['decision']}
            checked=DiffDecision.model_validate(request)
            if digest(checked.model_dump())!=record['request_sha256']:
                raise Conflict('Original immutable review request differs from its record')
        except (ValueError,TypeError,KeyError) as error:
            raise Conflict('Original review metadata integrity changed') from error
        return record

    def _records(self,pid,sid):
        parent=self.owner._path(pid,sid,'diff-reviews')
        if not parent.exists():return []
        paths=[]
        for path in parent.iterdir():
            if len(paths)>=256:raise Conflict('Session review metadata exceeds its bounded catalog')
            paths.append(path)
        return [self._record(pid,sid,path) for path in paths]

    def _head(self,pid,sid,key):
        records=[row for row in self._records(pid,sid) if row['key']==key]
        if sorted(row['revision'] for row in records)!=list(range(1,len(records)+1)):
            raise Conflict('Original historical review revisions collide')
        return max(records,key=lambda row:row['revision']) if records else None

    def _scope(self,pid,sid,body):
        manifest,reference=self.owner._manifest(pid,sid,body.session_ref.model_dump())
        state=self.owner._state(pid,sid)
        if state['generation']!=body.generation or manifest['request']['workspace_ref']!=body.workspace_ref.model_dump():
            raise Conflict('Original review session generation or workspace changed')
        binding=self.owner.workspaces.get(pid,body.workspace_ref.id,body.workspace_ref.revision)
        if binding['binding_sha256']!=body.workspace_ref.binding_sha256 or binding['root_identity']!=manifest['directory_identity']:
            raise Conflict('Original review registered workspace changed')
        return manifest,reference,state,binding

    def _disk(self,binding,path):
        from packages.domain.context_read_budget import current_context_read_budget
        allowance=current_context_read_budget()
        def read():
            fn=lambda maximum:self.owner.workspaces._raw(binding,path,missing=True,max_bytes=maximum)
            return allowance.guarded(fn,MAX_CONTENT_BYTES) if allowance else fn(MAX_CONTENT_BYTES)
        raw=read();checked=read()
        if raw!=checked:
            raise Conflict('Saved file changed between review observations')
        return {'exists':raw is not None,'sha256':None if raw is None else hashlib.sha256(raw).hexdigest(),
            'byte_length':0 if raw is None else len(raw)}

    async def prepare(self,pid,sid,body):
        body=body if isinstance(body,DiffPreparation) else DiffPreparation.model_validate(body)
        _,reference,_,binding=self._scope(pid,sid,body)
        if body.child_ref:
            directory,content=await ChildObservations(self.owner).diffs(pid,sid,reference,body.generation,
                body.child_ref.model_dump(),body.workspace_ref.model_dump())
        else:
            directory,content=self.owner._diff_observation(pid,sid,session_sha256=reference['sha256'],
                generation=body.generation,workspace_ref=body.workspace_ref.model_dump())
        detail=file_detail(directory,content,body.snapshot_ref.model_dump(),body.diff_ref.model_dump())
        if body.child_ref:
            detail['child_ref']=body.child_ref.model_dump()
        if detail['content_scope']!='FULL_FILE' or detail['source']!='AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS':
            raise Conflict('Exact complete before/after snapshots are required for review decisions')
        disk=self._disk(binding,detail['path'])
        head=self._head(pid,sid,self._key(pid,body))
        before_exists=detail['status']!='added';after_exists=detail['status']!='deleted'
        basis={'selection':self._selection(body),'path':detail['path'],'disk':disk,
            'review_revision':head['revision'] if head else 0,
            'before_sha256':detail['before_sha256'],'after_sha256':detail['after_sha256'],
            'before_exists':before_exists,'after_exists':after_exists}
        self._scope(pid,sid,body)
        return {'kind':'DIFF_REVIEW_PREPARATION','scope':directory['scope'],
            'child_ref':body.child_ref.model_dump() if body.child_ref else None,
            'preparation_sha256':digest(basis),'review_revision':basis['review_revision'],
            'review':None if head is None else {'revision':head['revision'],'decision':head['decision'],
                'operation_id':head['operation_id'],'review_ref':{'id':head['key'],'revision':head['revision'],'sha256':digest(head)}},
            'current_saved':disk,
            'matches_before':disk['exists']==before_exists and (not before_exists or disk['sha256']==detail['before_sha256']),
            'matches_after':disk['exists']==after_exists and (not after_exists or disk['sha256']==detail['after_sha256']),
            'diff':detail,'read_only':True,'model_calls':0,'native_posts':0,'workspace_writes':0,
            'editor_dirty_state':'NOT_OBSERVED','editor_barrier':'NOT_ACQUIRED',
            'effect_scope':'HISTORICAL_USER_REVIEW_ONLY_NOT_FILE_APPLICATION_OR_ROLLBACK'}

    @staticmethod
    def _details(head):
        return {'generation':head['selection']['generation'],'review_ref':{'id':head['key'],
            'revision':head['revision'],'sha256':digest(head)},'review_decision':head['decision'],
            'review_selection':head['selection'],'preparation_sha256':head['preparation_sha256'],
            'current_saved':head['current_saved'],'model_calls':0,'native_posts':0,'workspace_writes':0,
            'effect_scope':'HISTORICAL_USER_REVIEW_ONLY_NOT_FILE_APPLICATION_OR_ROLLBACK',
            'independent_verification':'NOT_RUN','semantic_acceptance':False}

    def operation(self,pid,sid,operation_id):
        old=self.owner.operation(pid,sid,operation_id)
        if old['kind']!='DIFF_REVIEW':
            raise Conflict('The original operation is not a review decision')
        body=DiffDecision.model_validate(old['request'])
        if old['request_sha256']!=digest(body.model_dump()) or old['session_ref']!=body.session_ref.model_dump():
            raise Conflict('Original review request integrity changed')
        if old['status'] in {'RECORDED_NOT_DISPATCHED','UNKNOWN_OUTCOME'}:
            # Recover only already-published exact metadata, not a file effect,
            # a new decision or a newer same-path version.
            record_path=self.owner._path(pid,sid,'diff-reviews',digest(operation_id)+'.json')
            head=self._record(pid,sid,record_path) if record_path.exists() else None
            if head and head['key']==self._key(pid,body) and head['operation_id']==operation_id and head['request_sha256']==old['request_sha256']:
                return self.owner._finish(pid,sid,operation_id,'RETURNED',**self._details(head),recovered_review_metadata=True)
        return old

    async def decide(self,pid,sid,body):
        body=body if isinstance(body,DiffDecision) else DiffDecision.model_validate(body)
        self.owner._manifest(pid,sid,body.session_ref.model_dump())
        old,created=self.owner._claim(pid,sid,body.operation_id,'DIFF_REVIEW',body.model_dump())
        if not created:
            return self.operation(pid,sid,body.operation_id)
        published=False
        try:
            prepared=await self.prepare(pid,sid,DiffPreparation.model_validate(self._selection(body)))
            if prepared['preparation_sha256']!=body.preparation_sha256 or prepared['review_revision']!=body.expected_review_revision:
                raise Conflict('Review preparation or another user decision changed; refresh without replay')
            key=self._key(pid,body)
            with self.owner.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                _,_,_,binding=self._scope(pid,sid,body)
                if self._disk(binding,prepared['diff']['path'])!=prepared['current_saved']:
                    raise Conflict('Saved file changed before review publication')
                head=self._head(pid,sid,key)
                if (head['revision'] if head else 0)!=body.expected_review_revision:
                    raise Conflict('Another review decision already changed this exact historical file')
                parent=self.owner._path(pid,sid,'diff-reviews')
                records=self._records(pid,sid)
                if len(records)>=256 or not head and len({row['key'] for row in records})>=128:
                    raise Conflict('The bounded session review catalog is full')
                record={'schema':'eeas.diff-review.v1','key':key,'revision':body.expected_review_revision+1,
                    'operation_id':body.operation_id,'request_sha256':digest(body.model_dump()),
                    'selection':self._selection(body),'decision':body.decision,'preparation_sha256':body.preparation_sha256,
                    'current_saved':prepared['current_saved'],'created_at':now()}
                # A write may publish then lose its acknowledgement.
                published=True
                self.owner._write(parent/(digest(body.operation_id)+'.json'),record)
                # In the same serialization scope, publish the receipt without
                # opening another BEGIN IMMEDIATE through _finish.
                result={**old,'status':'RETURNED','dispatch_status':'RETURNED','updated_at':now(),**self._details(record)}
                self.owner._write(self.owner._path(pid,sid,'operations',digest(body.operation_id)+'.json'),result,replace=True)
            self.owner._notify_observer()
            return result
        except Exception as error:
            self.owner._finish(pid,sid,body.operation_id,'UNKNOWN_OUTCOME' if published else 'FAILED',
                generation=body.generation,reason=type(error).__name__,workspace_writes=0,
                model_calls=0,native_posts=0,review_publication_may_have_happened=published)
            raise
