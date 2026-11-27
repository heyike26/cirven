"""Resolve a resource identity from original server records, never model IDs."""
from contextlib import contextmanager
from contextvars import Context
from functools import wraps
from packages.domain.task_resource_budget import TaskResourceBudget,task_resource_scope,current_task_resource_budget
from packages.domain.context_read_budget import context_read_scope
from packages.domain.store import Conflict
from packages.domain.tasks import digest


def for_operation(owner,manifest,operation,*,_seen=None,_cache=None):
    pid,sid=manifest['project_id'],manifest['id'];_,reference=owner._manifest(pid,sid)
    if digest(manifest)!=reference['sha256']:raise Conflict('Task resource owner manifest changed')
    seen=set(_seen or ());key=(pid,sid,operation['id'])
    if key in seen or len(seen)>=32:raise Conflict('Task resource original work ancestry exceeds its proof bound')
    seen.add(key)
    cache={} if _cache is None else _cache
    if key in cache:
        checksum,budget=cache[key]
        if checksum!=digest(operation):raise Conflict('Task resource original operation changed during resolution')
        return budget
    def resolved(budget):
        cache[key]=(digest(operation),budget)
        return budget
    if manifest.get('native_review_admission'):
        admission=owner.native_reviews.validate_admission(manifest['native_review_admission'],manifest['request'])
        root_id=admission.get('task_root_operation_id')
        if root_id is None:
            parent=operation.get('original_active_operation_id') or operation['id']
            mapping=owner.native_reviews.read('operations',digest([sid,parent]))
            if mapping['session_ref']!=reference or mapping['generation']!=operation['request']['generation']:
                raise Conflict('Task resource native review operation owner changed')
            record=owner.native_reviews._record(mapping['review_ref']['id'])
            if record['review_ref']!=mapping['review_ref']:
                raise Conflict('Task resource native review original reference changed')
            root_id=record['request']['operation_id']
        else:
            record=owner.native_reviews._record(root_id)
        if (record['request'].get('project_id')!=admission['project_id']
                or record['request']['conversation_id']!=admission['conversation_id']):
            raise Conflict('Task resource native review belongs to another discussion')
        authority={'project_id':pid,'kind':'NATIVE_REVIEW_WORK','root_operation_id':root_id,'request_sha256':record['request_sha256']}
    elif manifest.get('native_message_admission'):
        admission=owner.native_messages.validate_admission(manifest['native_message_admission'],manifest['request'])
        record=owner.native_messages._record(admission['parent_operation_id'])
        authority={'project_id':pid,'kind':'NATIVE_MESSAGE_WORK','root_operation_id':admission['parent_operation_id'],'request_sha256':record['request_sha256']}
    elif manifest.get('experience_validation_admission'):
        admission,intent=owner.experience_validation._admission(manifest)
        source_sid=admission['source_session_id'];candidate=owner.experience_validation.candidates.candidate(pid,source_sid,intent['request']['candidate_ref'])
        source_manifest,reference=owner._manifest(pid,source_sid);root_id=candidate['request']['root_operation_id'];record=owner.operation(pid,source_sid,root_id)
        authority={'project_id':pid,'kind':'ORIGINAL_WORK','session_ref':reference,'root_operation_id':root_id,'request_sha256':record['request_sha256']}
    else:
        visited=set();root=operation
        for _ in range(8):
            if (root['id'] in visited or root.get('session_ref')!=reference or digest(root.get('request'))!=root.get('request_sha256')):
                raise Conflict('Task resource original operation lineage changed')
            visited.add(root['id']);parent=root.get('original_active_operation_id')
            if root.get('kind') in {'GUIDANCE','CHILD_GUIDANCE'} and parent:
                root=owner.operation(pid,sid,parent);continue
            selected=root['request'].get('original_work_ref')
            if selected is not None:
                from .work_requirements import WorkRequirementScope,_root
                basis=_root(owner,pid,sid,WorkRequirementScope(session_ref=reference,generation=root['request']['generation'],workspace_ref=manifest['request']['workspace_ref'],root_operation_id=selected['root_operation_id']))
                if basis['sha256']!=selected['root_sha256']:raise Conflict('Task resource selected original work differs')
                root=owner.operation(pid,sid,selected['root_operation_id'])
                if root['request'].get('original_work_ref') is not None:raise Conflict('Task resource work root must be the original work')
            break
        else:raise Conflict('Task resource operation ancestry exceeds its bound')
        authority={'project_id':pid,'kind':'ORIGINAL_WORK','session_ref':reference,'root_operation_id':root['id'],'request_sha256':root['request_sha256']}
        budget=(for_operation(owner,manifest,root,_seen=seen,_cache=cache) if root['id']!=operation['id']
            else TaskResourceBudget(owner.workspaces,authority))
        runtime=getattr(owner,'conversations',None)
        prior=runtime.resource_origin(manifest,operation) if runtime is not None else None
        if prior is not None:
            previous,parent,inherited=prior
            if root['id']!=operation['id'] and budget.id!=inherited.id:
                raise Conflict('Selected work and canonical reply name different resource roots')
            return resolved(inherited)
        return resolved(budget)
    return resolved(TaskResourceBudget(owner.workspaces,authority))


@contextmanager
def claimed_read_scope(owner,manifest,operation_id):
    """Account child dispatch preflight before any native message GET.

    The durable claimed body, current server parent and immutable lineage are
    the authority. A submitted parent ID alone cannot borrow its allowance.
    """
    pid,sid=manifest['project_id'],manifest['id']
    operation=owner.operation(pid,sid,operation_id)
    _,reference=owner._manifest(pid,sid)
    request=operation.get('request',{})
    state=owner._state(pid,sid)
    if (operation.get('status')!='RECORDED_NOT_DISPATCHED'
            or operation.get('kind') not in {'CHILD_MESSAGE','CHILD_GUIDANCE'}
            or operation.get('session_ref')!=reference or request.get('session_ref')!=reference
            or digest(request)!=operation.get('request_sha256')
            or state.get('generation')!=request.get('generation')):
        raise Conflict('Original child resource preflight claim changed')
    parent=request.get('expected_active_operation_id')
    if operation['kind']=='CHILD_GUIDANCE':
        if not parent or state.get('active_operation_id')!=parent:
            raise Conflict('Original child resource preflight parent changed')
        original=owner.operation(pid,sid,parent)
        if (original.get('kind') not in {'MESSAGE','CHILD_MESSAGE'} or original.get('status')!='ACCEPTED'
                or original.get('generation')!=request['generation']
                or original.get('session_ref')!=reference):
            raise Conflict('Original child resource preflight requires its acknowledged parent')
        operation={**operation,'original_active_operation_id':parent}
    elif parent is not None or state.get('active_operation_id'):
        raise Conflict('Idle child resource preflight cannot replace active work')
    budget=for_operation(owner,manifest,operation)
    budget.allocate()
    with task_resource_scope(budget),context_read_scope():yield budget


@contextmanager
def for_active(owner,pid,sid,*,expected_context=None):
    manifest,_=owner._manifest(pid,sid);state=owner._state(pid,sid);context=owner.active.get(sid)
    if not state.get('active_operation_id') or context is None or expected_context is not None and context is not expected_context:
        raise Conflict('Original resource accounting owner/work changed')
    operation=owner.operation(pid,sid,state['active_operation_id']);budget=for_operation(owner,manifest,operation);budget.allocate()
    with task_resource_scope(budget),context_read_scope():yield budget


def model_scope(method):
    @wraps(method)
    def wrapped(owner,pid,sid,request_sha,*,expected_context=None,input_bytes=0):
        with for_active(owner,pid,sid,expected_context=expected_context):return method(owner,pid,sid,request_sha,expected_context=expected_context,input_bytes=input_bytes)
    return wrapped


def native_callback_scope(routes):
    """Re-enter the exact active owner after a private bridge thread hop."""
    allowed=frozenset(routes)
    def decorate(method):
        @wraps(method)
        async def wrapped(service,manifest,context,route,value):
            owner=service.sessions;pid,sid=manifest['project_id'],manifest['id']
            if (route not in allowed or not isinstance(value,dict)
                    or owner.active.get(sid) is not context or context.get('closing')
                    or type(value.get('generation')) is not int
                    or value['generation']!=context['generation']):
                raise Conflict('Original tool callback owner or route changed')
            def identity():
                # Identity-only control records must not debit an unrelated
                # caller's ambient source meter before we can reject it.
                _,reference=owner._manifest(pid,sid);state=owner._state(pid,sid)
                active=state.get('active_operation_id')
                if (value.get('owner_ref')!=reference or state.get('generation')!=context['generation']
                        or not active):
                    raise Conflict('Original tool callback has no exact active work')
                operation=owner.operation(pid,sid,active)
                if operation.get('status') not in {'ACCEPTED','DISPATCHING'}:
                    raise Conflict('Original tool callback work is not dispatchable')
                return for_operation(owner,manifest,operation)
            budget=Context().run(identity)
            with task_resource_scope(budget),context_read_scope():
                budget.allocate()
                return await method(service,manifest,context,route,value)
        return wrapped
    return decorate


def native_scope(method):
    @wraps(method)
    async def wrapped(owner,manifest,context,body,*,http_hop):
        pid,sid=manifest['project_id'],manifest['id']
        _,reference=owner._manifest(pid,sid)
        if (body.owner_ref.project_id!=pid or body.owner_ref.session_ref.model_dump()!=reference or body.generation!=context['generation']
                or body.root_native_session_id!=context.get('session_id') or owner.active.get(sid) is not context):raise Conflict('MODEL_AUTHORITY_OWNER_CHANGED')
        with for_active(owner,pid,sid,expected_context=context):return await method(owner,manifest,context,body,http_hop=http_hop)
    return wrapped


def reserve_model(input_bytes=0):
    budget=current_task_resource_budget()
    if budget is not None:budget.consume(model_attempts=1,source_bytes=input_bytes,source_reads=int(input_bytes>0))
