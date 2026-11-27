"""Explicit local allowance changes; never resets usage or grants model/tools."""
from pydantic import Field,field_validator
from packages.domain.questions import Id,Hash,RequestModel
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import digest
from .models import SessionRef
from .task_resources import for_operation


class ResourceRef(RequestModel):
    id:Hash
    revision:int=Field(ge=0,lt=2**48,strict=True)
    sha256:Hash


class ResourceLimits(RequestModel):
    source_bytes:int=Field(ge=1,lt=2**48,strict=True)
    source_reads:int=Field(ge=1,lt=2**48,strict=True)
    model_attempts:int=Field(ge=1,lt=2**48,strict=True)
    retained_output_bytes:int=Field(ge=1,lt=2**48,strict=True)


class AllowanceChange(RequestModel):
    operation_id:Id
    session_ref:SessionRef
    generation:int=Field(ge=1,strict=True)
    work_operation_id:Id
    expected_resource_ref:ResourceRef
    limits:ResourceLimits
    note:str=Field(default='',max_length=160,strict=True)

    @field_validator('*')
    @classmethod
    def original_utf8(cls,value,info):
        # An optional data note can be empty; its own type/length/UTF-8
        # validators still apply. Other fields keep the shared text policy.
        if info.field_name=='note':return value
        return RequestModel.original_utf8(value)

    @field_validator('note')
    @classmethod
    def valid_text(cls,value):
        value.encode('utf-8');return value


def change(owner,pid,sid,value):
    body=AllowanceChange.model_validate(value);manifest,reference=owner._manifest(pid,sid,body.session_ref.model_dump())
    request=body.model_dump();request_sha=digest(request)
    # Same writer ordering as model/SSE admission: SQL owner -> independent
    # resource file lock. No SDK request or allocation occurs in this path.
    with owner.store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        manifest,checked=owner._manifest(pid,sid,reference)
        if checked!=reference or owner._state(pid,sid)['generation']!=body.generation:raise Conflict('Original allowance owner generation changed')
        work=owner.operation(pid,sid,body.work_operation_id);budget=for_operation(owner,manifest,work)
        from .resource_allowance_packets import retain
        retain(budget,request)
        result=budget.adjust(body.operation_id,request_sha,body.expected_resource_ref.model_dump(),body.limits.model_dump(),note=body.note)
    return {**result,'project_id':pid,'session_ref':reference,'generation':body.generation,'work_operation_id':body.work_operation_id}


def lookup(owner,pid,sid,session_sha256,work_operation_id,operation_id):
    manifest,reference=owner._manifest(pid,sid)
    if reference['sha256']!=session_sha256:raise Conflict('Original allowance lookup owner changed')
    work=owner.operation(pid,sid,work_operation_id);budget=for_operation(owner,manifest,work)
    try:result=budget.adjustment(operation_id)
    except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError):
        result={'kind':'ORIGINAL_WORK_RESOURCE_ALLOWANCE_OPERATION','resource_id':budget.id,'operation_id':operation_id,
            'status':'UNAVAILABLE','reason':'ORIGINAL_LEDGER_UNAVAILABLE','automatic_replay_allowed':False}
    return {**result,'project_id':pid,'session_ref':reference,'observed_generation':owner._state(pid,sid)['generation'],
        'work_operation_id':work_operation_id,'read_only':True,'metadata_writes':0,'model_calls':0}
