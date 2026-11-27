"""Read an explicitly selected original operation's software resource receipt."""
import sqlite3
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import now
from .task_resources import for_operation
from .resource_retention import summary


def public(owner,pid,sid,session_sha256,operation_id=None):
    manifest,reference=owner._manifest(pid,sid)
    if reference['sha256']!=session_sha256:raise Conflict('RESOURCE_BUDGET_ORIGINAL_OWNER_CHANGED')
    state=owner._state(pid,sid)
    result={'kind':'ORIGINAL_WORK_RESOURCE_BUDGET','project_id':pid,'session_ref':reference,'generation':state['generation'],
        'operation_id':operation_id,'observed_at':now(),'read_only':True,'metadata_writes':0,'model_calls':0,
        'scope':'EXACT_SELECTED_ORIGINAL_WORK','usage':None,'retained_event_attribution':None}
    try:
        attribution=summary(owner,pid,sid)
        attribution['accounting_unconfirmed']=bool(attribution['gap'] or state.get('resource_retention_unconfirmed') or owner.active.get(sid,{}).get('resource_retention_unconfirmed'))
        result['retained_event_attribution']=attribution
    except (Conflict,OSError,ValueError,TypeError,KeyError,sqlite3.Error):return {**result,'status':'UNAVAILABLE','reason':'ATTRIBUTION_UNAVAILABLE'}
    if operation_id is None:return {**result,'status':'NO_WORK_SELECTED','reason':'EXPLICIT_WORK_SELECTION_REQUIRED'}
    try:
        operation=owner.operation(pid,sid,operation_id);budget=for_operation(owner,manifest,operation)
        if not budget.path.exists():
            if budget.marker.exists() or budget.gap.exists():return {**result,'status':'UNAVAILABLE','reason':'ORIGINAL_LEDGER_MISSING'}
            return {**result,'status':'NOT_ALLOCATED','reason':'NO_NEW_ALLOCATION_OR_RETROACTIVE_USAGE'}
        result['usage']=budget.inspect()
    except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError):return {**result,'status':'UNAVAILABLE','reason':'ORIGINAL_WORK_USAGE_UNAVAILABLE'}
    # Read requests never allocate, refund, reconcile unknown state or dispatch.
    return {**result,'status':'AVAILABLE','reason':None}
