"""Read-only normalization for durable exact experience requests.

JSON floating-point canonicalization is server-owned. This does not freeze a
task, grant permission, reserve an operation, validate a tool, or enable code.
"""
from typing import Literal
import json
import math
from pydantic import Field,model_validator,field_validator
from packages.domain.questions import RequestModel
from packages.domain.tasks import canonical,digest
from .experience_candidates import ExperiencePropose,ExperienceReview
from .experience_tool_validation import ExperienceToolValidate
from .experience_skill_validation import ExperienceSkillValidate
from .experience_activation import ExperiencePromote,ExperienceRollback,ExperienceActivationRecover
from .experience_source_policy import ExperienceSourceDecision
from .experience_pending import ExperiencePendingControl


ACTION_MODELS={
    'PROPOSE':ExperiencePropose,'REVIEW':ExperienceReview,
    'TOOL_VALIDATE':ExperienceToolValidate,'SKILL_VALIDATE':ExperienceSkillValidate,
    'PROMOTE':ExperiencePromote,'ROLLBACK':ExperienceRollback,
    'SOURCE_DECISION':ExperienceSourceDecision,'PENDING_CONTROL':ExperiencePendingControl,
    'RECOVER_CONFIGURATION':ExperienceActivationRecover,
}

def _exact_json(raw):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('Duplicate JSON keys are not accepted')
            value[key]=item
        return value
    def invalid(value):raise ValueError('Nonfinite JSON values are not accepted')
    try:value=json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)
    except RecursionError:raise ValueError('Exact JSON nesting exceeds its bound') from None
    stack=[(value,0)];nodes=0
    while stack:
        item,depth=stack.pop();nodes+=1
        if nodes>20000 or depth>64:raise ValueError('Exact JSON exceeds its bounded structure')
        if isinstance(item,float) and not math.isfinite(item):invalid(item)
        if isinstance(item,dict):stack.extend((child,depth+1) for child in item.values())
        elif isinstance(item,list):stack.extend((child,depth+1) for child in item)
    return value


class ExperienceRequestPrepare(RequestModel):
    action:Literal['PROPOSE','REVIEW','TOOL_VALIDATE','SKILL_VALIDATE','PROMOTE','ROLLBACK',
        'SOURCE_DECISION','PENDING_CONTROL','RECOVER_CONFIGURATION']
    request:dict|None=Field(default=None,min_length=1)
    request_json:str|None=Field(default=None,max_length=262144)

    @field_validator('*')
    @classmethod
    def original_utf8(cls,value,info):
        if info.field_name=='request_json' and value is not None:
            if len(value.encode('utf-8'))>262144:raise ValueError('Exact request JSON exceeds 256KiB')
            return value
        return RequestModel.original_utf8(value)

    @model_validator(mode='after')
    def bounded(self):
        if (self.request is None)==(self.request_json is None):raise ValueError('Provide one request object or exact request JSON')
        if self.request_json is not None:
            value=_exact_json(self.request_json)
            if not isinstance(value,dict) or not value:raise ValueError('Exact request JSON must be a nonempty object')
        else:value=self.request
        if len(canonical(value).encode())>262144:raise ValueError('Experience request preparation exceeds 256KiB')
        return self


def prepare_experience_request(candidates,pid,sid,body):
    body=ExperienceRequestPrepare.model_validate(body)
    _,reference=candidates.sessions._manifest(pid,sid)
    request=ACTION_MODELS[body.action].model_validate(_exact_json(body.request_json) if body.request_json is not None else body.request).model_dump()
    # Echo only the caller's normalized input. No candidate body, context/file
    # content, authority proof, or runtime configuration is read for this echo.
    return {'kind':'EXPERIENCE_REQUEST_PREPARATION','version':1,'project_id':pid,'session_id':sid,'session_ref':reference,
        'action':body.action,'request':request,'request_json':canonical(request),'request_sha256':digest(request),
        'read_only':True,'metadata_writes':0,'model_calls':0,'processes_started':0,
        'permission_granted':False,'automatic_replay':False,'authority_checked_on_actual_command':True}
