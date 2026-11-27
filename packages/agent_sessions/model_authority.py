"""Native request-plan admission within the original owner, with no model call."""
import asyncio
from pathlib import Path
from typing import Literal

from pydantic import Field

from packages.agent_sessions.models import SessionRef
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict


class ModelOwner(RequestModel):
    project_id: Id
    session_ref: SessionRef


class ModelAuthorityCall(RequestModel):
    schema_tag: Literal['eeas.model-authority-call.v1'] = Field(alias='schema')
    owner_ref: ModelOwner
    generation: int = Field(ge=1,strict=True)
    root_native_session_id: str = Field(pattern=r'^ses_[A-Za-z0-9_-]{1,100}$')
    native_session_id: str = Field(pattern=r'^ses_[A-Za-z0-9_-]{1,100}$')
    user_message_id: str = Field(pattern=r'^msg_[A-Za-z0-9_-]{1,100}$')
    agent: Id
    step: int = Field(ge=1,le=1048576,strict=True)


async def lineage(owner,manifest,context,native):
    root=context['session_id'];known=context['model_authority']['known_sessions']
    if native==root or native in known:return
    current=native;seen=set()
    for _ in range(8):
        if current==root:break
        if current in seen:raise Conflict('MODEL_AUTHORITY_CHILD_CYCLE')
        seen.add(current)
        value=await owner._http(context,'GET','/session/'+current)
        parent=value.get('parentID')
        if (value.get('id')!=current or value.get('directory')!=context['directory']
                or not isinstance(parent,str) or not parent.startswith('ses_')
                or not 5<=len(parent)<=104 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in parent)):
            raise Conflict('MODEL_AUTHORITY_CHILD_SCOPE_CHANGED')
        current=parent
    else:
        if current!=root:raise Conflict('MODEL_AUTHORITY_CHILD_DEPTH')
    if len(known|seen)>256:raise Conflict('MODEL_AUTHORITY_CHILD_CAPACITY')
    known.update(seen)


async def admit(owner,manifest,expected_context,value):
    return await _admit(owner,manifest,expected_context,ModelAuthorityCall.model_validate(value),http_hop=False)


async def admit_http(owner,manifest,expected_context,value):
    from .subscription_http_calls import SubscriptionHTTPCall
    return await _admit(owner,manifest,expected_context,SubscriptionHTTPCall.model_validate(value),http_hop=True)


from .task_resources import native_scope


@native_scope
async def _admit(owner,manifest,expected_context,body,*,http_hop):
    from .service import TERMINAL
    pid,sid=manifest['project_id'],manifest['id']
    _,reference=owner._manifest(pid,sid)
    if (body.owner_ref.project_id!=pid or body.owner_ref.session_ref.model_dump()!=reference
            or body.generation!=expected_context['generation'] or body.root_native_session_id!=expected_context.get('session_id')):
        raise Conflict('MODEL_AUTHORITY_OWNER_CHANGED')
    async with expected_context['model_authority']['lock']:
        _,current=owner._context(pid,sid,reference,body.generation,intent='OBSERVE')
        if current is not expected_context:raise Conflict('MODEL_AUTHORITY_ORIGINAL_CONTEXT_CHANGED')
        operation=owner._state(pid,sid).get('active_operation_id')
        if not operation or owner.operation(pid,sid,operation)['status'] in TERMINAL:
            raise Conflict('MODEL_AUTHORITY_NO_ACTIVE_ORIGINAL_WORK')
        owner.context_authority.check(manifest,current)
        await lineage(owner,manifest,current,body.native_session_id)
        from .resource_retention import wait_model_user
        await wait_model_user(owner,manifest,current,body,operation)
        owner.context_authority.check(manifest,current)
        await owner.editor_captures.broker.admit(manifest,generation=body.generation,operation_id=operation,kind='MODEL_REQUEST_PLAN')
        _,checked=owner._context(pid,sid,reference,body.generation,intent='EXECUTE')
        if (checked is not expected_context or owner._state(pid,sid).get('active_operation_id')!=operation
                or owner.operation(pid,sid,operation)['status'] in TERMINAL):
            raise Conflict('MODEL_AUTHORITY_ORIGINAL_OPERATION_CHANGED')
        with owner.store.connect() as source_boundary:
            source_boundary.execute('BEGIN IMMEDIATE')
            if not owner._configuration_current(manifest):
                raise Conflict('EXPERIENCE_SOURCE_CHANGED: source/configuration changed before native request-plan admission')
            state=owner._state(pid,sid)
            if (owner.active.get(sid) is not expected_context or expected_context.get('closing')
                    or state.get('generation')!=body.generation or state.get('active_operation_id')!=operation
                    or owner.operation(pid,sid,operation)['status'] in TERMINAL):
                raise Conflict('MODEL_AUTHORITY_ORIGINAL_OPERATION_CHANGED')
            from .resource_retention import admit_model_user
            admit_model_user(owner,source_boundary,manifest,body)
            measured={}
            if manifest.get('provider_auth_mode')=='OAUTH_SUBSCRIPTION':
                from . import subscription_http_budget as budget
                if http_hop:measured=budget.reserve(owner,manifest,body,operation)
                else:measured={'plan_sha256':budget.plan(owner,manifest,body,operation)}
            elif http_hop:raise Conflict('SUBSCRIPTION_HTTP_EXACT_OWNER_REQUIRED')
        return {'schema':'eeas.model-authority-http-hop-receipt.v1' if http_hop else 'eeas.model-authority-receipt.v1',
            **measured,**({'hop':body.hop,'request_sha256':body.request_sha256} if http_hop else {}),
            'admitted':True,'step':body.step,
            'generation':body.generation,'root_native_session_id':body.root_native_session_id,
            'native_session_id':body.native_session_id,'user_message_id':body.user_message_id,
            'model_calls_reserved':0}


def start(owner,manifest,context,runtime,config):
    from packages.adapters.opencode_native_message import NativeMessageBridge
    from packages.adapters.opencode_model_authority import prepare
    async def callback(_route,value):
        if isinstance(value,dict) and value.get('schema')=='eeas.model-authority-http-hop.v1':return await admit_http(owner,manifest,context,value)
        return await admit(owner,manifest,context,value)
    bridge=NativeMessageBridge(callback,asyncio.get_running_loop())
    context.setdefault('secrets',[]).append(bridge.key)
    # Register exact handles before any subsequent preparation can fail.
    value={'bridge':bridge,'lock':asyncio.Lock(),'known_sessions':set()};context['model_authority']=value
    _,reference=owner._manifest(manifest['project_id'],manifest['id'])
    meter=None
    if manifest.get('provider_auth_mode')=='OAUTH_SUBSCRIPTION':
        from . import subscription_http_budget as budget
        with owner.store.connect() as writer:
            writer.execute('BEGIN IMMEDIATE');budget.initialize(owner,manifest)
        meter={'model_id':context['profile'].model_id,'transport':'ORIGINAL_CODEX_HTTP_FALLBACK'}
    value['prepared']=prepare(Path(__file__).resolve().parents[2],runtime,subscription_http=meter,owner_ref={'project_id':manifest['project_id'],'session_ref':reference},
        generation=context['generation'],directory=context['directory'],bridge=bridge)
    config['plugin'].append(Path(value['prepared']['entry']).as_uri())
    return value
