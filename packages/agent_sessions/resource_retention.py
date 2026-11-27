"""Exact native-message attribution for retained original event bytes.

The original event stays authoritative and is always preserved first. These
bounded SQLite locations carry no text, prompts, credentials or output copies.
Unresolved attribution is pending, never silently charged to the current head.
"""
from contextlib import contextmanager
import asyncio,json,re
from packages.domain.store import Conflict
from packages.domain.tasks import canonical,digest
from packages.domain.task_resource_budget import TaskResourceBudget,task_resource_scope,current_task_resource_budget
from packages.domain.context_read_budget import context_read_scope

MAX_MESSAGES=4096
MAX_TOTAL_MESSAGES=32768
MAX_CHILDREN=256
MAX_TOTAL_CHILDREN=2048
_NATIVE=re.compile(r'^ses_[A-Za-z0-9_-]{1,100}$')
_MESSAGE=re.compile(r'^msg_[A-Za-z0-9_-]{1,100}$')


def _resource(budget):return canonical({'authority':budget.authority,'limits':budget.limits})


def _budget(owner,value):
    if not isinstance(value,str) or len(value.encode())>16384:raise Conflict('Original output resource metadata exceeds its bound')
    raw=json.loads(value)
    if not isinstance(raw,dict) or set(raw)!={'authority','limits'}:raise Conflict('Original output resource authority changed')
    return TaskResourceBudget(owner.workspaces,raw['authority'],limits=raw['limits'])


def _row(db,pid,sid,generation,native,mid):
    return db.execute('SELECT * FROM original_resource_messages WHERE project_id=? AND session_id=? AND generation=? AND native_session_id=? AND message_id=?',
        (pid,sid,generation,native,mid)).fetchone()


def _gap(db,pid,sid,seq,reason):
    db.execute('INSERT INTO original_resource_gaps VALUES (?,?,?,?) ON CONFLICT(project_id,session_id) DO UPDATE SET first_event_seq=min(first_event_seq,excluded.first_event_seq)',(pid,sid,seq,reason))


def _capacity(db,pid,sid,table,session_limit,total_limit):
    # Only fixed internal table names are allowed by the two callers.
    local=db.execute('SELECT count(*) FROM '+table+' WHERE project_id=? AND session_id=?',(pid,sid)).fetchone()[0]
    total=db.execute('SELECT count(*) FROM '+table).fetchone()[0]
    if local>=session_limit or total>=total_limit:raise Conflict('Original output attribution metadata capacity reached')


def require_confirmed(owner,db,pid,sid):
    if (owner.active.get(sid,{}).get('resource_retention_unconfirmed')
            or owner._state(pid,sid).get('resource_retention_unconfirmed')
            or db.execute('SELECT 1 FROM original_resource_gaps WHERE project_id=? AND session_id=?',(pid,sid)).fetchone()
            or db.execute('SELECT 1 FROM original_resource_messages WHERE project_id=? AND session_id=? AND pending_bytes>0 LIMIT 1',(pid,sid)).fetchone()):
        raise Conflict('Original output accounting is unresolved; preserve received evidence before new generation')


def _save(db,row):
    db.execute('INSERT INTO original_resource_messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,session_id,generation,native_session_id,message_id) DO UPDATE SET role=excluded.role,parent_user_message_id=excluded.parent_user_message_id,resource_json=excluded.resource_json,pending_bytes=excluded.pending_bytes,accounting_unconfirmed=excluded.accounting_unconfirmed,last_event_seq=excluded.last_event_seq,last_event_sha256=excluded.last_event_sha256',
        tuple(row[k] for k in ['project_id','session_id','session_sha256','generation','native_session_id','message_id','role','parent_user_message_id','resource_json','pending_bytes','accounting_unconfirmed','last_event_seq','last_event_sha256']))


def _new(db,pid,sid,reference,generation,native,mid):
    if type(generation)is not int or generation<1 or not isinstance(native,str) or not _NATIVE.fullmatch(native) or not isinstance(mid,str) or not _MESSAGE.fullmatch(mid):
        raise Conflict('Exact original native message identity is required')
    old=_row(db,pid,sid,generation,native,mid)
    if old is not None:
        if old['session_sha256']!=reference['sha256']:raise Conflict('Original retained message owner changed')
        return dict(old)
    _capacity(db,pid,sid,'original_resource_messages',MAX_MESSAGES,MAX_TOTAL_MESSAGES)
    return {'project_id':pid,'session_id':sid,'session_sha256':reference['sha256'],'generation':generation,'native_session_id':native,'message_id':mid,
        'role':'unresolved','parent_user_message_id':None,'resource_json':None,'pending_bytes':0,'accounting_unconfirmed':0,'last_event_seq':0,'last_event_sha256':None}


def _settle(owner,db,row,resource):
    if row['resource_json'] is not None and row['resource_json']!=resource:raise Conflict('Original message cannot change its work resource authority')
    row['resource_json']=resource
    if row['pending_bytes'] and not row['accounting_unconfirmed']:
        budget=_budget(owner,resource)
        if budget.authority['project_id']!=row['project_id']:raise Conflict('Original retained output resource belongs to another project')
        budget.allocate()
        if budget.retain_received(row['pending_bytes']) is None:
            row['accounting_unconfirmed']=1
            _gap(db,row['project_id'],row['session_id'],row['last_event_seq'],'OUTPUT_ACCOUNTING_WRITE_UNCONFIRMED')
        else:row['pending_bytes']=0
    _save(db,row)


def _flush_dependants(owner,db,pid,sid,reference,generation,native,user,resource):
    rows=db.execute("SELECT * FROM original_resource_messages WHERE project_id=? AND session_id=? AND generation=? AND native_session_id=? AND parent_user_message_id=? AND role='assistant' AND accounting_unconfirmed=0 AND (resource_json IS NULL OR pending_bytes>0)",(pid,sid,generation,native,user)).fetchall()
    for row in rows:
        _settle(owner,db,dict(row),resource)
        _reconcile_task_parts(owner,db,pid,sid,reference,generation,native,row['message_id'],resource)


def bind_user(owner,db,manifest,generation,native,mid,budget):
    pid,sid=manifest['project_id'],manifest['id'];_,reference=owner._manifest(pid,sid)
    if digest(manifest)!=reference['sha256']:raise Conflict('Original work owner changed before output binding')
    if budget.authority['project_id']!=pid:raise Conflict('Original user resource belongs to another project')
    row=_new(db,pid,sid,reference,generation,native,mid)
    if row['role'] not in {'unresolved','user'}:raise Conflict('Original user message was observed with another role')
    row['role']='user';resource=_resource(budget);_settle(owner,db,row,resource)
    _flush_dependants(owner,db,pid,sid,reference,generation,native,mid,resource)


@contextmanager
def dispatch_scope(owner,manifest,context,operation):
    from .task_resources import for_operation
    pid,sid=manifest['project_id'],manifest['id'];budget=for_operation(owner,manifest,operation);budget.allocate()
    if operation.get('generation')!=context['generation'] or owner.active.get(sid) is not context:raise Conflict('Original dispatch resource generation changed')
    with owner.store.connect() as db:
        db.execute('BEGIN IMMEDIATE');require_confirmed(owner,db,pid,sid)
        bind_user(owner,db,manifest,context['generation'],operation['original_session_id'],operation['native_message_id'],budget)
    with task_resource_scope(budget),context_read_scope():yield budget


def admit_model_user(owner,db,manifest,body):
    pid,sid=manifest['project_id'],manifest['id'];require_confirmed(owner,db,pid,sid)
    _,reference=owner._manifest(pid,sid)
    budget=current_task_resource_budget()
    if budget is None:raise Conflict('Original model work resource scope is unavailable')
    row=_row(db,pid,sid,body.generation,body.native_session_id,body.user_message_id)
    if row is None or row['resource_json'] is None:
        child=db.execute('SELECT * FROM original_resource_children WHERE project_id=? AND session_id=? AND generation=? AND native_session_id=?',(pid,sid,body.generation,body.native_session_id)).fetchone()
        if child is None or child['session_sha256']!=reference['sha256'] or child['resource_json']!=_resource(budget):raise Conflict('Exact original model user work attribution is unavailable')
        bind_user(owner,db,manifest,body.generation,body.native_session_id,body.user_message_id,budget)
        row=_row(db,pid,sid,body.generation,body.native_session_id,body.user_message_id)
    if row['session_sha256']!=reference['sha256'] or row['role']!='user' or row['resource_json']!=_resource(budget):raise Conflict('Original model user belongs to another work resource allowance')


async def wait_model_user(owner,manifest,context,body,operation,*,timeout_seconds=5):
    """Await already flowing original event attribution; never bind or replay.

    A native child can reach its model hook before its parent task metadata
    reaches the durable SSE index. The existing strict admission still runs
    after this bounded metadata wait.
    """
    if not 0<timeout_seconds<=5:raise ValueError('Original attribution wait exceeds its bound')
    pid,sid=manifest['project_id'],manifest['id'];_,reference=owner._manifest(pid,sid)
    budget=current_task_resource_budget()
    if budget is None:raise Conflict('Original model work resource scope is unavailable')
    resource=_resource(budget);changed=context.setdefault('resource_attribution_changed',asyncio.Event())
    loop=asyncio.get_running_loop();deadline=loop.time()+timeout_seconds
    for _ in range(64):
        changed.clear();state=owner._state(pid,sid)
        if (owner.active.get(sid) is not context or context.get('closing')
                or state.get('generation')!=body.generation or state.get('active_operation_id')!=operation):
            raise Conflict('Original model attribution owner/work changed while awaiting events')
        with owner.store.connect() as db:
            row=_row(db,pid,sid,body.generation,body.native_session_id,body.user_message_id)
            if row is not None and row['resource_json'] is not None:
                if row['session_sha256']!=reference['sha256'] or row['role']!='user' or row['resource_json']!=resource:
                    raise Conflict('Original model user belongs to another work resource allowance')
                return
            child=db.execute('SELECT * FROM original_resource_children WHERE project_id=? AND session_id=? AND generation=? AND native_session_id=?',
                (pid,sid,body.generation,body.native_session_id)).fetchone()
            if child is not None:
                if child['session_sha256']!=reference['sha256'] or child['resource_json']!=resource:
                    raise Conflict('Original model child belongs to another work resource allowance')
                return
        remaining=deadline-loop.time()
        if remaining<=0:break
        try:await asyncio.wait_for(changed.wait(),timeout=remaining)
        except asyncio.TimeoutError:break
    raise Conflict('Exact original model user work attribution did not arrive within its bound')


def _child(owner,db,pid,sid,reference,generation,parent,mid,part,resource):
    state=part.get('state') or {};metadata=state.get('metadata') or {}
    child=metadata.get('sessionId')
    if (part.get('type')!='tool' or part.get('tool')!='task' or not isinstance(child,str) or not _NATIVE.fullmatch(child)
            or metadata.get('parentSessionId',parent)!=parent or child==parent):return
    old=db.execute('SELECT * FROM original_resource_children WHERE project_id=? AND session_id=? AND generation=? AND native_session_id=?',(pid,sid,generation,child)).fetchone()
    if old is not None:
        if old['session_sha256']!=reference['sha256'] or old['resource_json']!=resource:raise Conflict('Original child session has ambiguous work resource provenance')
    else:
        _capacity(db,pid,sid,'original_resource_children',MAX_CHILDREN,MAX_TOTAL_CHILDREN)
        db.execute('INSERT INTO original_resource_children VALUES (?,?,?,?,?,?,?,?,?)',(pid,sid,reference['sha256'],generation,child,resource,parent,mid,digest(part)))
    # Only original user identities already observed on this child are linked.
    # An unobserved role/parent is never inferred from the current active work.
    for row in db.execute("SELECT * FROM original_resource_messages WHERE project_id=? AND session_id=? AND generation=? AND native_session_id=? AND role='user' AND resource_json IS NULL",(pid,sid,generation,child)).fetchall():
        _settle(owner,db,dict(row),resource)
        _flush_dependants(owner,db,pid,sid,reference,generation,child,row['message_id'],resource)


def _reconcile_task_parts(owner,db,pid,sid,reference,generation,native,mid,resource):
    """Parts may precede their assistant info; inspect exact existing locations."""
    slots=db.execute("SELECT * FROM original_message_locations WHERE project_id=? AND session_id=? AND generation=? AND native_session_id=? AND message_id=? AND kind='OTHER' LIMIT 257",(pid,sid,generation,native,mid)).fetchall()
    if len(slots)>256:raise Conflict('Original deferred task metadata exceeds its bounded locations')
    for slot in slots:
        event=owner._read(owner._path(pid,sid,'events',f"{slot['event_seq']:010d}.json"))
        part=event.get('payload',{}).get('native_payload',{}).get('properties',{}).get('part',{})
        if digest(event)!=slot['event_sha256'] or digest(part)!=slot['value_sha256']:raise Conflict('Original deferred task event reference changed')
        if part.get('sessionID')!=native or part.get('messageID')!=mid:raise Conflict('Original deferred task message location changed')
        _child(owner,db,pid,sid,reference,generation,native,mid,part,resource)


def _observe(owner,db,pid,sid,event):
    if event['type']!='ORIGINAL_EVENT':return 'NOT_APPLICABLE'
    payload=event['payload'];native=payload.get('native_payload',{});kind=native.get('type');props=native.get('properties',{})
    if kind not in {'message.updated','message.removed','message.part.updated','message.part.removed','message.part.delta'}:return 'NOT_APPLICABLE'
    info=props.get('info',{}) if kind=='message.updated' else {};part=props.get('part',{}) if kind=='message.part.updated' else {}
    session=props.get('sessionID') or info.get('sessionID') or part.get('sessionID');mid=info.get('id') or part.get('messageID') or props.get('messageID')
    manifest,reference=owner._manifest(pid,sid);generation=payload.get('generation');row=_new(db,pid,sid,reference,generation,session,mid)
    if row['last_event_seq']>=event['seq']:
        if row['last_event_seq']==event['seq'] and row['last_event_sha256']!=digest(event):raise Conflict('Original retained event sequence collision')
        return 'ALREADY_ACCOUNTED_OR_PENDING'
    if info:
        role=info.get('role')
        if role not in {'user','assistant'} or row['role'] not in {'unresolved',role}:raise Conflict('Original retained message role changed')
        row['role']=role
        if role=='assistant':
            parent=info.get('parentID')
            if not isinstance(parent,str) or not _MESSAGE.fullmatch(parent) or row['parent_user_message_id'] not in {None,parent}:raise Conflict('Original assistant parent changed')
            row['parent_user_message_id']=parent
    had_resource=row['resource_json'] is not None;resource=row['resource_json']
    if resource is None and row['role']=='assistant':
        parent=_row(db,pid,sid,generation,session,row['parent_user_message_id'])
        if parent is not None and parent['role']=='user':resource=parent['resource_json']
    elif resource is None and row['role']=='user':
        child=db.execute('SELECT * FROM original_resource_children WHERE project_id=? AND session_id=? AND generation=? AND native_session_id=?',(pid,sid,generation,session)).fetchone()
        if child is not None:
            if child['session_sha256']!=reference['sha256']:raise Conflict('Original child output owner changed')
            resource=child['resource_json']
    amount=len(canonical(event).encode())
    if row['pending_bytes']+amount>=2**53:raise Conflict('Original pending output byte counter capacity reached')
    row.update(pending_bytes=row['pending_bytes']+amount,last_event_seq=event['seq'],last_event_sha256=digest(event))
    if resource is None:_save(db,row);return 'PENDING_ORIGINAL_MESSAGE_ATTRIBUTION'
    _settle(owner,db,row,resource)
    if row['role']=='user':_flush_dependants(owner,db,pid,sid,reference,generation,session,mid,resource)
    if part and row['role']=='assistant':_child(owner,db,pid,sid,reference,generation,session,mid,part,resource)
    elif info and row['role']=='assistant' and not had_resource:_reconcile_task_parts(owner,db,pid,sid,reference,generation,session,mid,resource)
    return 'OUTPUT_ATTRIBUTION_UNCONFIRMED' if row['pending_bytes'] else 'ORIGINAL_WORK_RETAINED_EVENT_BYTES'


def observe(owner,db,pid,sid,event):
    try:return _observe(owner,db,pid,sid,event)
    except Exception as error:
        # Original bytes have already been saved. Preserve that event even if
        # this derived quota bookkeeping cannot be committed.
        context=owner.active.get(sid)
        if context is not None:context['resource_retention_unconfirmed']=True
        try:_gap(db,pid,sid,event['seq'],type(error).__name__)
        except Exception:pass
        return 'OUTPUT_ATTRIBUTION_UNCONFIRMED'


def summary(owner,pid,sid):
    with owner.store.connect() as db:
        rows=db.execute('SELECT count(*),coalesce(sum(pending_bytes),0) FROM original_resource_messages WHERE project_id=? AND session_id=?',(pid,sid)).fetchone()
        gap=db.execute('SELECT first_event_seq,reason FROM original_resource_gaps WHERE project_id=? AND session_id=?',(pid,sid)).fetchone()
    return {'kind':'ORIGINAL_RETAINED_EVENT_ATTRIBUTION','message_locations':rows[0],'pending_bytes':rows[1],
        'gap':dict(gap) if gap is not None else None,'read_only':True,'copied_message_bodies':0,
        'coverage':'NEWLY_BOUND_WORK_ONLY_NOT_RETROACTIVE','physical_disk_bytes':'NOT_MEASURED'}
