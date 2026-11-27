"""Durable server-created execution links to an exact original-work allowance.

Context inheritance alone is not provenance. New links commit with the actual
execution/scope; old unlinked executions retain their own SQL lineage authority.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from packages.domain.task_resource_budget import TaskResourceBudget,current_task_resource_budget,task_resource_scope,MAX_OUTPUT_GAPS
from packages.domain.context_read_budget import context_read_scope
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.store import Conflict
from packages.domain.tasks import canonical,digest,now

MAX_BINDINGS=16384
MAX_BINDING_BYTES=16384


def _workspaces(adapter):
    return getattr(adapter,'workspace_authority',None) or LocalWorkspaces(adapter.service,adapter.data_dir)


def _decode(raw):
    from packages.domain.native_capabilities import _json
    try:return _json(raw)
    except (ValueError,TypeError,UnicodeError) as error:
        raise Conflict('Original execution resource JSON is unreadable; original records retained') from error


def _records(db,pid,row):
    actual=db.execute('SELECT * FROM agent_executions WHERE id=? AND project_id=?',(row['id'],pid)).fetchone()
    if (actual is None or any(actual[k]!=row[k] for k in ['id','project_id','runtime','request_hash','workspace'])
            or digest(_decode(actual['request']))!=actual['request_hash']):
        raise Conflict('Original execution resource input changed')
    scope=db.execute('SELECT * FROM agent_scopes WHERE execution_id=?',(row['id'],)).fetchone()
    if scope is None or digest(_decode(scope['scope']))!=scope['scope_hash']:
        raise Conflict('Original execution resource scope changed')
    root=db.execute('SELECT * FROM agent_executions WHERE id=? AND project_id=?',(scope['root_execution_id'],pid)).fetchone()
    root_scope=db.execute('SELECT * FROM agent_scopes WHERE execution_id=?',(scope['root_execution_id'],)).fetchone()
    if (root is None or root_scope is None or root_scope['root_execution_id']!=root['id']
            or root['runtime']!=actual['runtime'] or digest(_decode(root['request']))!=root['request_hash']
            or digest(_decode(root_scope['scope']))!=root_scope['scope_hash']):
        raise Conflict('Original execution resource root changed')
    return actual,scope,root


def _own_budget(adapter,pid,root):
    authority={'project_id':pid,'kind':'ADAPTER_EXECUTION_WORK','root_execution_id':root['id'],
        'request_sha256':root['request_hash'],'runtime':root['runtime']}
    return TaskResourceBudget(_workspaces(adapter),authority)


def _encoded(budget):
    return canonical({'resource_id':budget.id,'authority':budget.authority,'limits':budget.limits})


def _linked_budget(adapter,pid,db,actual,scope):
    link=db.execute('SELECT * FROM agent_execution_resources WHERE execution_id=?',(actual['id'],)).fetchone()
    if link is None:return None
    if (any(link[k]!=v for k,v in {'project_id':pid,'root_execution_id':scope['root_execution_id'],
            'runtime':actual['runtime'],'request_sha256':actual['request_hash'],'scope_sha256':scope['scope_hash']}.items())
            or len(link['resource_json'].encode())>MAX_BINDING_BYTES):
        raise Conflict('Original execution resource link changed')
    value=_decode(link['resource_json'])
    if (not isinstance(value,dict) or set(value)!={'resource_id','authority','limits'}
            or not isinstance(value['authority'],dict) or not isinstance(value['limits'],dict)):
        raise Conflict('Original execution resource metadata changed')
    budget=TaskResourceBudget(_workspaces(adapter),value['authority'],limits=value['limits'])
    if budget.authority['project_id']!=pid or budget.id!=value['resource_id'] or _encoded(budget)!=link['resource_json']:
        raise Conflict('Original execution resource authority changed')
    return budget


def _check_inherited(adapter,pid,budget):
    inherited=current_task_resource_budget()
    if inherited is None:return budget
    if (inherited.authority['project_id']!=pid or inherited.workspaces.data_dir.resolve()!=Path(adapter.data_dir).resolve()
            or inherited.id!=budget.id or inherited.path!=budget.path or inherited.limits!=budget.limits):
        raise Conflict('Nested execution lacks an exact original-work resource link')
    return inherited


def bind_created_execution(adapter,db,row):
    """Called only inside the original SQL writer, after new execution+scope.

    No standalone commit, model/client, file allocation, counter reset or link
    update. SQL rollback removes this link along with its new execution row.
    """
    if not db.in_transaction:raise Conflict('New execution resource link requires its original SQL writer')
    pid=row['project_id'];actual,scope,root=_records(db,pid,row)
    existing=_linked_budget(adapter,pid,db,actual,scope)
    if existing is not None:return _check_inherited(adapter,pid,existing)
    if root['id']==actual['id']:
        inherited=current_task_resource_budget()
        if inherited is not None:
            if inherited.authority['project_id']!=pid or inherited.workspaces.data_dir.resolve()!=Path(adapter.data_dir).resolve():
                raise Conflict('Execution creation cannot link another project or application allowance')
            budget=inherited
        else:budget=_own_budget(adapter,pid,root)
    else:
        _,root_scope,_=_records(db,pid,root)
        budget=_linked_budget(adapter,pid,db,root,root_scope) or _own_budget(adapter,pid,root)
        _check_inherited(adapter,pid,budget)
    encoded=_encoded(budget)
    if len(encoded.encode())>MAX_BINDING_BYTES or db.execute('SELECT count(*) FROM agent_execution_resources').fetchone()[0]>=MAX_BINDINGS:
        raise Conflict('Original execution resource link capacity reached; original records retained')
    db.execute('INSERT INTO agent_execution_resources VALUES (?,?,?,?,?,?,?,?)',
        (actual['id'],pid,root['id'],actual['runtime'],actual['request_hash'],scope['scope_hash'],encoded,now()))
    return budget


def _budget_for_records(adapter,pid,db,actual,scope,root):
    budget=_linked_budget(adapter,pid,db,actual,scope)
    _,root_scope,_=_records(db,pid,root)
    original=_linked_budget(adapter,pid,db,root,root_scope) or _own_budget(adapter,pid,root)
    if budget is not None and _encoded(budget)!=_encoded(original):
        raise Conflict('Execution resource link differs from its original lineage')
    return budget or original,scope


def _resolved(adapter,pid,db,row):
    return _budget_for_records(adapter,pid,db,*_records(db,pid,row))


def resolve(adapter,pid,row,context):
    with adapter.service.connect() as db:
        db.execute('BEGIN')
        budget,_scope=_resolved(adapter,pid,db,row)
    return _check_inherited(adapter,pid,budget)


def require_confirmed(db,pid,row,budget):
    # A full gap index seals future generation globally instead of dropping
    # original evidence to make room for another attribution failure.
    if db.execute('SELECT count(*) FROM agent_execution_resource_gaps').fetchone()[0]>=MAX_OUTPUT_GAPS:
        raise Conflict('Execution output accounting gap capacity reached; preserve originals')
    scope=db.execute('SELECT root_execution_id FROM agent_scopes WHERE execution_id=?',(row['id'],)).fetchone()
    if scope is None or db.execute('SELECT 1 FROM agent_execution_resource_gaps g LEFT JOIN agent_scopes s ON s.execution_id=g.execution_id WHERE g.project_id=? AND (g.resource_id=? OR s.root_execution_id=?) LIMIT 1',
            (pid,budget.id,scope['root_execution_id'])).fetchone():
        raise Conflict('Execution output accounting is unconfirmed; preserve originals before new generation')
    if budget.accounting_unconfirmed or budget.gap.exists():
        raise Conflict('Original work output accounting remains unconfirmed')


@contextmanager
def record_scope(adapter,pid,row):
    budget=resolve(adapter,pid,row,None)
    with adapter.service.connect() as db:
        db.execute('BEGIN');require_confirmed(db,pid,row,budget)
    budget.allocate()
    with task_resource_scope(budget),context_read_scope():yield budget


def sdk_scope(method):
    @wraps(method)
    async def wrapped(adapter,pid,ident,client,request):
        row=adapter.inspect(pid,ident)
        if row['execution_status']!='QUEUED' or row['cancel_requested']:
            return await method(adapter,pid,ident,client,request)
        with record_scope(adapter,pid,row):return await method(adapter,pid,ident,client,request)
    return wrapped


def retain_execution_output(adapter,db,ident,size):
    """Charge the actual event owner; never discard received events on failure.

    Called in the event's original SQL writer. A sticky bounded SQL gap is
    committed with the original event if ledger attribution/save is uncertain.
    """
    row=db.execute('SELECT * FROM agent_executions WHERE id=?',(ident,)).fetchone()
    if row is None:raise Conflict('Original retained execution does not exist')
    pid=row['project_id'];budget=None;scope=None
    try:
        actual,scope,root=_records(db,pid,row)
        budget,_scope=_budget_for_records(adapter,pid,db,actual,scope,root)
        budget.allocate()
        if budget.retain_received(size) is not None:return True
    except Exception:
        pass
    live=getattr(adapter,'active',{});context=live.get(ident)
    if context is None and scope is not None:context=live.get(scope['native_call_execution_id'])
    captured=(context or {}).get('task_resource_budget')
    if captured is not None:captured.accounting_unconfirmed=True
    resource_id=budget.id if budget is not None else captured.id if captured is not None else None
    if db.execute('SELECT count(*) FROM agent_execution_resource_gaps').fetchone()[0]<MAX_OUTPUT_GAPS:
        db.execute('INSERT INTO agent_execution_resource_gaps VALUES (?,?,?,?,?) ON CONFLICT(execution_id) DO NOTHING',
            (ident,pid,resource_id,'OUTPUT_RESOURCE_ACCOUNTING_UNCONFIRMED',now()))
    return False


def execution_scope(method):
    @wraps(method)
    async def wrapped(adapter,pid,row,request,scope,context):
        with record_scope(adapter,pid,row) as budget:
            context['task_resource_budget']=budget
            return await method(adapter,pid,row,request,scope,context)
    return wrapped


def callback_scope(method):
    """A broker-created task cannot inherit a caller's unrelated thread scope."""
    @wraps(method)
    async def wrapped(adapter,row,request,context,body):
        if adapter.active.get(row['id']) is not context or context.get('closing'):
            raise Conflict('Original execution tool callback owner changed')
        current=adapter.inspect(row['project_id'],row['id'])
        captured=context.get('task_resource_budget')
        if (current['execution_status']!='RUNNING' or current['cancel_requested']
                or current['request_hash']!=row['request_hash'] or digest(request)!=row['request_hash']
                or captured is None or captured.accounting_unconfirmed):
            raise Conflict('Original execution tool callback has no exact active resource owner')
        with record_scope(adapter,row['project_id'],current) as budget:
            if (budget.id!=captured.id or budget.path!=captured.path or budget.limits!=captured.limits):
                raise Conflict('Original execution tool callback resource binding changed')
            return await method(adapter,row,request,context,body)
    return wrapped


def reserve_model(budget,input_bytes=0):
    if budget is not None:budget.consume(model_attempts=1,source_bytes=input_bytes,source_reads=int(input_bytes>0))


def retain(budget,size):
    if budget is not None:budget.retain_received(size)
