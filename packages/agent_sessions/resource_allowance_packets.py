"""Immutable private original requests, retained before allowance mutation.

These packets make a lost browser journal discoverable through the original
work. Presence proves request receipt, never that an allowance was applied.
"""
from pathlib import Path
import re,stat
from packages.domain.native_capabilities import NativeCapabilities,_json
from packages.adapters.opencode_native_review import regular
from packages.domain.store import Conflict
from packages.domain.tasks import digest,now
from packages.domain.task_resource_budget import MAX_ADJUSTMENTS
from .resource_allowance import AllowanceChange

SCHEMA='eeas.original-work-allowance-packet.v1'
MAX_PACKET_BYTES=16384


def _path(budget,operation_id):return budget.path.parent/'allowance-packets'/(digest(operation_id)+'.json')


def _read(budget,path):
    envelope=_json(regular(path,MAX_PACKET_BYTES,private=True))
    if not isinstance(envelope,dict) or set(envelope)!={'value','sha256'} or digest(envelope['value'])!=envelope['sha256']:raise Conflict('Original allowance packet integrity changed')
    value=envelope['value']
    if (not isinstance(value,dict) or set(value)!={'schema','resource_id','request','request_sha256','created_at'}
            or value['schema']!=SCHEMA or value['resource_id']!=budget.id or not isinstance(value['created_at'],str)):
        raise Conflict('Original allowance packet scope changed')
    request=AllowanceChange.model_validate(value['request']).model_dump()
    if (request!=value['request'] or digest(request)!=value['request_sha256'] or request['expected_resource_ref']['id']!=budget.id
            or path.name!=_path(budget,request['operation_id']).name):raise Conflict('Original allowance packet request changed')
    return value


def _entries(budget):
    folder=budget.path.parent/'allowance-packets'
    if not folder.exists():return []
    info=folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or folder.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:raise Conflict('Original allowance packet directory changed')
    entries=[]
    # Do not materialize an unbounded directory listing or omit unknown files.
    for path in folder.iterdir():
        if len(entries)>=MAX_ADJUSTMENTS or not re.fullmatch(r'[a-f0-9]{64}\.json',path.name):raise Conflict('Original allowance packet capacity or member differs')
        entries.append(path)
    return sorted(entries)


def retain(budget,request):
    body=AllowanceChange.model_validate(request).model_dump();sha=digest(body)
    if body['expected_resource_ref']['id']!=budget.id:raise Conflict('Allowance packet belongs to another original work')
    with budget._lock(create=False):
        budget._read();path=_path(budget,body['operation_id'])
        if path.exists():
            value=_read(budget,path)
            if value['request']!=body or value['request_sha256']!=sha:raise Conflict('Original allowance packet ID already binds another request')
            return value
        entries=_entries(budget)
        for old in entries:_read(budget,old)
        if len(entries)>=MAX_ADJUSTMENTS:raise Conflict('Original allowance packet capacity reached; prior requests retained')
        value={'schema':SCHEMA,'resource_id':budget.id,'request':body,'request_sha256':sha,'created_at':now()}
        NativeCapabilities._write(path,value)
        if _read(budget,path)!=value:raise Conflict('Original allowance packet save is unconfirmed')
        return value


def page(owner,pid,sid,session_sha256,work_operation_id):
    from .task_resources import for_operation
    manifest,reference=owner._manifest(pid,sid)
    if reference['sha256']!=session_sha256:raise Conflict('Original allowance packet owner changed')
    budget=for_operation(owner,manifest,owner.operation(pid,sid,work_operation_id))
    with budget._lock(create=False):
        state=budget._read();values=sorted([_read(budget,path) for path in _entries(budget)],key=lambda v:(v['created_at'],v['request']['operation_id']))
    rows=[];generation=owner._state(pid,sid)['generation'];operations={r['operation_id']:r for r in state.get('adjustments',[])}
    for value in values:
        request=value['request']
        if request['session_ref']!=reference:continue
        record=operations.get(request['operation_id'])
        observation=budget._adjustment_result(record,state) if record is not None else {'kind':'ORIGINAL_WORK_RESOURCE_ALLOWANCE_OPERATION','resource_id':budget.id,'operation_id':request['operation_id'],'status':'NOT_OBSERVED','reason':'ABSENCE_IS_NOT_PROOF_OF_NOT_APPLIED','automatic_replay_allowed':False}
        observation.update(project_id=pid,session_ref=reference,observed_generation=generation,work_operation_id=work_operation_id,read_only=True,metadata_writes=0,model_calls=0)
        rows.append({'packet_ref':{'id':digest([budget.id,request['operation_id']]),'revision':1,'sha256':digest(value)},
            'request':request,'request_sha256':value['request_sha256'],'observation':observation})
    return {'kind':'ORIGINAL_WORK_ALLOWANCE_PACKET_PAGE','project_id':pid,'session_ref':reference,'resource_id':budget.id,
        'work_operation_id':work_operation_id,'packets':rows,'legacy_operations_without_packet':len(set(operations)-{v['request']['operation_id'] for v in values}),'capacity':MAX_ADJUSTMENTS,'read_only':True,'metadata_writes':0,'model_calls':0,
        'presence_is_application_proof':False,'automatic_replay_allowed':False}
