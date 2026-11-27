"""Reference-only admission on a real planning/desktop execution authority.

Keeps the original adapter row/scope/process and frozen request. It never creates
an AgentSessions owner, reads auth content, selects a model, or retries a prompt.
"""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
from pydantic import Field,model_serializer
from packages.agent_sessions.model_authority import ModelAuthorityCall
from packages.adapters.opencode_native_message import NativeMessageBridge
from packages.adapters.opencode_native_review import regular
from packages.adapters.opencode_model_authority import prepare,bind,ready
from packages.domain.context_read_budget import context_read_scope,source_regular
from packages.domain.native_capabilities import NativeCapabilities,_json
from packages.domain.questions import Id,Hash,RequestModel
from packages.domain.store import Conflict
from packages.domain.tasks import digest,canonical,now

MAX_BYTES=262144


class ExecutionRef(RequestModel):
    id:Id
    revision:int=Field(ge=1,le=1,strict=True)
    sha256:Hash


class ExecutionOwner(RequestModel):
    project_id:Id
    execution_ref:ExecutionRef


class ExecutionPlan(ModelAuthorityCall):
    schema_tag:Literal['eeas.adapter-model-authority-call.v1']=Field(alias='schema')
    owner_ref:ExecutionOwner


class ExecutionHTTP(ExecutionPlan):
    schema_tag:Literal['eeas.adapter-model-authority-http-hop.v1']=Field(alias='schema')
    hop:int=Field(ge=1,le=1048576,strict=True)
    plan_sha256:Hash
    request_sha256:Hash
    request_bytes:int=Field(default=0,ge=0,le=4194304,strict=True)

    @model_serializer(mode='wrap')
    def preserve_legacy_hop(self,handler):
        value=handler(self)
        if 'request_bytes' not in self.model_fields_set:value.pop('request_bytes',None)
        return value



class SubscriptionExecutionMeter:
    def __init__(self,adapter,row,request,scope,context,profile,directory):
        from packages.adapters.subscription_runtime import is_subscription
        if not is_subscription(profile) or profile.provider_id!='openai':raise Conflict('ADAPTER_MODEL_ORIGINAL_SUBSCRIPTION_REQUIRED')
        self.adapter,self.context=adapter,context;self.row=deepcopy(row);self.request=deepcopy(request);self.scope=deepcopy(scope)
        self.pid,self.ident=row['project_id'],row['id'];self.lock=asyncio.Lock();self.closed=False;self.close_confirmed=False
        self.task_budget=context.get('task_resource_budget')
        self.reference={'id':self.ident,'revision':1,'sha256':digest({'id':self.ident,'request_sha256':row['request_hash'],'scope_sha256':digest(scope),'runtime':row['runtime']})}
        self.owner_ref={'project_id':self.pid,'execution_ref':self.reference};self.limit=request['budget']['max_calls']
        if row['request_hash']!=digest(request) or row['runtime']!=adapter.runtime or type(self.limit) is not int or not 1<=self.limit<=128:
            raise Conflict('ADAPTER_MODEL_EXACT_EXECUTION_REQUIRED')
        self.path=context['workspace']/'subscription-http-budget.json'
        self._save({'schema':'eeas.adapter-subscription-http-budget.v1','owner_ref':self.owner_ref,'max_calls':self.limit,'plans':[],'reservations':[]})
        self.bridge=NativeMessageBridge(self._callback,asyncio.get_running_loop())
        context['subscription_meter']=self
        context.setdefault('secrets',[]).append(self.bridge.key)
        self.prepared=prepare(Path(__file__).resolve().parents[2],context['workspace'],owner_ref=self.owner_ref,generation=1,
            directory=directory,bridge=self.bridge,subscription_http={'model_id':profile.model_id,'transport':'ORIGINAL_CODEX_HTTP_FALLBACK'},call_protocol='ADAPTER_EXECUTION_V1')

    @property
    def entry(self):return Path(self.prepared['entry']).as_uri()

    def environment(self,environment):
        return {**environment,'OPENCODE_PURE':'false','EEAS_MODEL_AUTHORITY_CONFIG':self.prepared['config_path']}

    def plugins_match(self,value):
        return value==[self.entry] or value==[[self.entry,{}]]

    def assert_ready(self):
        if not ready(self.prepared):raise Conflict('ADAPTER_MODEL_METER_NOT_INSTALLED')

    def bind(self,session_id):self.prepared=bind(self.prepared,session_id)

    def _save(self,value,*,replace=False):
        if len(canonical(value).encode())>MAX_BYTES:raise Conflict('ADAPTER_MODEL_LEDGER_CAPACITY: original records retained')
        NativeCapabilities._write(self.path,value,replace=replace)

    def _read(self):
        raw=source_regular(lambda p,n:regular(p,n,private=True),self.path,MAX_BYTES+512);outer=_json(raw)
        if (not isinstance(outer,dict) or set(outer)!={'value','sha256'} or digest(outer['value'])!=outer['sha256']):raise Conflict('ADAPTER_MODEL_LEDGER_INTEGRITY')
        value=outer['value']
        if (not isinstance(value,dict) or set(value)!={'schema','owner_ref','max_calls','plans','reservations'}
                or value['schema']!='eeas.adapter-subscription-http-budget.v1' or value['owner_ref']!=self.owner_ref
                or type(value['max_calls']) is not int or value['max_calls']!=self.limit
                or not isinstance(value['plans'],list) or len(value['plans'])>256
                or not isinstance(value['reservations'],list) or len(value['reservations'])>self.limit
                or len(canonical(value).encode())>MAX_BYTES):raise Conflict('ADAPTER_MODEL_LEDGER_CHANGED')
        plans={};hops=set()
        for row in value['plans']:
            if not isinstance(row,dict) or set(row)!={'call','sha256'}:raise Conflict('ADAPTER_MODEL_PLAN_CHANGED')
            call=ExecutionPlan.model_validate(row['call']).model_dump(by_alias=True)
            if call!=row['call'] or call['owner_ref']!=self.owner_ref or call['step'] in plans or digest(call)!=row['sha256']:
                raise Conflict('ADAPTER_MODEL_PLAN_CHANGED')
            plans[call['step']]=row
        for row in value['reservations']:
            if not isinstance(row,dict) or set(row)!={'call','reserved_at'} or not isinstance(row['reserved_at'],str):raise Conflict('ADAPTER_MODEL_HOP_CHANGED')
            call=ExecutionHTTP.model_validate(row['call']).model_dump(by_alias=True);plan=plans.get(call['step'])
            base={k:v for k,v in call.items() if k not in {'hop','plan_sha256','request_sha256','request_bytes'}};base['schema']='eeas.adapter-model-authority-call.v1'
            if call!=row['call'] or call['hop'] in hops or plan is None or base!=plan['call'] or call['plan_sha256']!=plan['sha256']:
                raise Conflict('ADAPTER_MODEL_HOP_CHANGED')
            hops.add(call['hop'])
        return value

    async def _callback(self,_route,value):
        is_http=isinstance(value,dict) and value.get('schema')=='eeas.adapter-model-authority-http-hop.v1'
        body=(ExecutionHTTP if is_http else ExecutionPlan).model_validate(value)
        if (body.owner_ref.model_dump()!=self.owner_ref or body.generation!=1 or not self.context.get('session_id')
                or body.root_native_session_id!=self.context['session_id'] or body.native_session_id!=self.context['session_id']):
            raise Conflict('ADAPTER_MODEL_ORIGINAL_EXECUTION_CHANGED')
        async with self.lock:
            from contextlib import nullcontext
            from packages.domain.task_resource_budget import task_resource_scope
            with (task_resource_scope(self.task_budget) if self.task_budget is not None else nullcontext()),context_read_scope(),self.adapter.service.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                current=connection.execute('SELECT * FROM agent_executions WHERE id=? AND project_id=?',(self.ident,self.pid)).fetchone()
                scope=connection.execute('SELECT * FROM agent_scopes WHERE execution_id=?',(self.ident,)).fetchone()
                process=self.context.get('process')
                if (self.closed or self.adapter.active.get(self.ident) is not self.context or self.context.get('closing')
                        or process is None or process.returncode is not None or self.context['done'].is_set()
                        or current is None or current['runtime']!=self.row['runtime'] or current['request_hash']!=digest(self.request)
                        or current['operation_id']!=self.row['operation_id'] or current['execution_status']!='RUNNING' or current['cancel_requested']
                        or current['session_id']!=body.native_session_id or scope is None
                        or scope['scope_hash']!=digest(self.scope) or _json(scope['scope'])!=self.scope
                        or datetime.fromisoformat(scope['expires_at'])<=datetime.now(timezone.utc)):
                    raise Conflict('ADAPTER_MODEL_ORIGINAL_EXECUTION_UNAVAILABLE')
                if self.task_budget is not None:
                    from packages.adapters.task_resources import require_confirmed
                    require_confirmed(connection,self.pid,current,self.task_budget)
                # Recheck exact application-owned sources/authorization/profile;
                # executable bytes were verified at this process's own startup.
                if self.adapter._scope(self.pid,self.request,verify_executable=False)!=self.scope:
                    raise Conflict('ADAPTER_MODEL_SOURCE_OR_PROFILE_CHANGED')
                data=self._read();call=body.model_dump(by_alias=True);extra={}
                if is_http:
                    plan=next((r for r in data['plans'] if r['call']['step']==body.step),None)
                    base={k:v for k,v in call.items() if k not in {'hop','plan_sha256','request_sha256','request_bytes'}};base['schema']='eeas.adapter-model-authority-call.v1'
                    if plan is None or plan['call']!=base or plan['sha256']!=body.plan_sha256:raise Conflict('ADAPTER_MODEL_ORIGINAL_PLAN_CHANGED')
                    if len(data['reservations'])>=self.limit:raise Conflict('ADAPTER_MODEL_ALLOWANCE_EXHAUSTED')
                    if any(r['call']['hop']==body.hop for r in data['reservations']):raise Conflict('ADAPTER_MODEL_HOP_ALREADY_RESERVED_NO_REPLAY')
                    from packages.adapters.task_resources import reserve_model
                    reserve_model(self.task_budget,body.request_bytes)
                    row={'call':call,'reserved_at':now()};data['reservations'].append(row)
                    extra={'hop':body.hop,'request_sha256':body.request_sha256,'reserved':len(data['reservations']),'limit':self.limit,'reservation_sha256':digest(row)}
                else:
                    if len(data['plans'])>=256 or any(r['call']['step']==body.step for r in data['plans']):raise Conflict('ADAPTER_MODEL_PLAN_RESERVED_OR_FULL')
                    data['plans'].append({'call':call,'sha256':digest(call)});extra={'plan_sha256':digest(call)}
                self._save(data,replace=True)
            return {'schema':'eeas.adapter-model-authority-http-hop-receipt.v1' if is_http else 'eeas.adapter-model-authority-receipt.v1',
                'admitted':True,'step':body.step,'generation':body.generation,'root_native_session_id':body.root_native_session_id,
                'native_session_id':body.native_session_id,'user_message_id':body.user_message_id,'model_calls_reserved':0,**extra}

    def summary(self):
        data=self._read()
        return {'rpc_attempts':None,'http_reservations':len(data['reservations']),'rpc_max_calls':self.limit,
            'rpc_cap_enforcement':'ORIGINAL_HTTP_PRE_FORWARD_RESERVATIONS_SOURCE_CONNECTED','hard_rpc_cap':'NOT_QUALIFIED',
            'reservation_is_actual_forward_or_provider_usage':False,'output_token_cap':'NOT_QUALIFIED_NATIVE_CODEX_PLUGIN',
            'commercial_cost':'SUBSCRIPTION_ALLOWANCE_NOT_MEASURED','provider_receipts':[]}

    async def close(self):
        self.closed=True
        try:await asyncio.to_thread(self.bridge.close)
        except Exception as error:
            self.context['subscription_meter_cleanup_error']=type(error).__name__
            return False
        self.close_confirmed=True
        return True
