"""Durable pre-forward reservations for original subscription HTTP inference.

The existing CLI/auth plugin retains credentials, refresh and model iteration.
This ledger is shared by every admitted native descendant/profile on one owner.
It measures conservative transport reservations, never commercial usage or tokens.
Call initialize under the existing owner writer barrier before starting the CLI;
call plan/reserve only under model_authority's final source writer transaction.
"""
from copy import deepcopy
from packages.domain.store import Conflict
from packages.domain.tasks import digest,now,canonical
from .model_authority import ModelAuthorityCall
from packages.adapters.opencode_native_review import regular
from packages.domain.native_capabilities import _json
from packages.domain.context_read_budget import source_regular

SCHEMA='eeas.subscription-http-budget.v1'
MAX_PLANS=256
MAX_BYTES=262144


def _path(owner,manifest):return owner._path(manifest['project_id'],manifest['id'],'subscription-http-budget.json')


def _basis(owner,manifest):
    _,ref=owner._manifest(manifest['project_id'],manifest['id'])
    if digest(manifest)!=ref['sha256']:raise Conflict('SUBSCRIPTION_HTTP_EXACT_MANIFEST_CHANGED')
    limit=manifest['request']['max_calls']
    if manifest.get('provider_auth_mode')!='OAUTH_SUBSCRIPTION' or type(limit) is not int or not 1<=limit<=128:
        raise Conflict('SUBSCRIPTION_HTTP_EXACT_OWNER_REQUIRED')
    return {'project_id':manifest['project_id'],'session_ref':ref},limit


def read(owner,manifest):
    reference,limit=_basis(owner,manifest);path=_path(owner,manifest)
    if not path.is_file() or path.stat().st_size>MAX_BYTES+512:raise Conflict('SUBSCRIPTION_HTTP_BUDGET_UNAVAILABLE')
    try:
        record=_json(source_regular(lambda p,n:regular(p,n,private=True),path,MAX_BYTES+512))
        if not isinstance(record,dict) or set(record)!={'value','sha256'} or digest(record['value'])!=record['sha256']:
            raise ValueError('Invalid subscription budget envelope')
        value=record['value']
    except (ValueError,TypeError,KeyError):raise Conflict('SUBSCRIPTION_HTTP_BUDGET_CHANGED') from None
    if (not isinstance(value,dict) or set(value)!={'schema','owner_ref','max_calls','plans','reservations'} or value['schema']!=SCHEMA
            or value['owner_ref']!=reference or value['max_calls']!=limit or type(value['max_calls']) is not int
            or not isinstance(value['plans'],list) or len(value['plans'])>MAX_PLANS
            or not isinstance(value['reservations'],list) or len(value['reservations'])>limit
            or len(canonical(value).encode())>MAX_BYTES):raise Conflict('SUBSCRIPTION_HTTP_BUDGET_CHANGED')
    plans={};identities=set()
    for row in value['plans']:
        if set(row)!={'call','operation_id','request_sha256'}:raise Conflict('SUBSCRIPTION_HTTP_PLAN_CHANGED')
        call=ModelAuthorityCall.model_validate(row['call']).model_dump(by_alias=True)
        key=(call['generation'],call['step'])
        if (call!=row['call'] or call['owner_ref']!=reference or digest(call)!=row['request_sha256'] or key in plans
                or not isinstance(row['operation_id'],str) or not row['operation_id']):raise Conflict('SUBSCRIPTION_HTTP_PLAN_CHANGED')
        plans[key]=row
    from packages.agent_sessions.subscription_http_calls import SubscriptionHTTPCall
    for row in value['reservations']:
        if set(row)!={'call','operation_id','reserved_at'}:raise Conflict('SUBSCRIPTION_HTTP_RESERVATION_CHANGED')
        call=SubscriptionHTTPCall.model_validate(row['call']).model_dump(by_alias=True)
        original=plans.get((call['generation'],call['step']));key=(call['generation'],call['hop'])
        if (call!=row['call'] or original is None or row['operation_id']!=original['operation_id'] or key in identities
                or call['plan_sha256']!=original['request_sha256'] or not isinstance(row['reserved_at'],str)):
            raise Conflict('SUBSCRIPTION_HTTP_RESERVATION_CHANGED')
        base={k:v for k,v in call.items() if k not in {'hop','plan_sha256','request_sha256','request_bytes'}};base['schema']='eeas.model-authority-call.v1'
        if base!=original['call']:raise Conflict('SUBSCRIPTION_HTTP_RESERVATION_SCOPE_CHANGED')
        identities.add(key)
    return value


def _save(owner,manifest,value,*,replace=False):
    if len(canonical(value).encode())>MAX_BYTES:
        raise Conflict('SUBSCRIPTION_HTTP_LEDGER_CAPACITY: original reservations retained')
    owner._write(_path(owner,manifest),value,replace=replace)


def initialize(owner,manifest):
    path=_path(owner,manifest)
    if path.exists():return read(owner,manifest)
    # Old unknown usage cannot be reset to a freshly empty measured allowance.
    if owner._state(manifest['project_id'],manifest['id']).get('native_turns_started',0)!=0:
        raise Conflict('SUBSCRIPTION_HTTP_LEGACY_UNMETERED_OWNER: keep history; explicitly open a new owner for a new allowance')
    reference,limit=_basis(owner,manifest)
    value={'schema':SCHEMA,'owner_ref':reference,'max_calls':limit,'plans':[],'reservations':[]}
    _save(owner,manifest,value);return value


def plan(owner,manifest,body,operation):
    value=read(owner,manifest);call=body.model_dump(by_alias=True)
    if len(value['plans'])>=MAX_PLANS or any((r['call']['generation'],r['call']['step'])==(body.generation,body.step) for r in value['plans']):
        raise Conflict('SUBSCRIPTION_HTTP_ORIGINAL_PLAN_RESERVED_OR_FULL')
    value['plans'].append({'call':call,'operation_id':operation,'request_sha256':digest(call)})
    _save(owner,manifest,value,replace=True)
    return digest(call)


def reserve(owner,manifest,body,operation):
    value=read(owner,manifest);call=body.model_dump(by_alias=True)
    original=next((r for r in value['plans'] if r['call']['generation']==body.generation and r['call']['step']==body.step),None)
    base={k:v for k,v in call.items() if k not in {'hop','plan_sha256','request_sha256','request_bytes'}};base['schema']='eeas.model-authority-call.v1'
    if original is None or original['call']!=base or original['operation_id']!=operation or original['request_sha256']!=body.plan_sha256:
        raise Conflict('SUBSCRIPTION_HTTP_ORIGINAL_PLAN_CHANGED')
    if len(value['reservations'])>=value['max_calls']:raise Conflict('SUBSCRIPTION_HTTP_ALLOWANCE_EXHAUSTED')
    if any(r['call']['generation']==body.generation and r['call']['hop']==body.hop for r in value['reservations']):
        raise Conflict('SUBSCRIPTION_HTTP_HOP_ALREADY_RESERVED_NO_REPLAY')
    from .task_resources import reserve_model
    reserve_model(body.request_bytes)
    value['reservations'].append({'call':deepcopy(call),'operation_id':operation,'reserved_at':now()})
    _save(owner,manifest,value,replace=True)
    return {'reserved':len(value['reservations']),'limit':value['max_calls'],'reservation_sha256':digest(value['reservations'][-1])}


def summary(owner,manifest):
    value=read(owner,manifest)
    return {'scope':'ORIGINAL_OWNER_ALL_NATIVE_DESCENDANTS_ALL_GENERATIONS','transport':'ORIGINAL_CODEX_HTTP_FALLBACK',
        'reservations':len(value['reservations']),'max_calls':value['max_calls'],'plans':len(value['plans']),
        'reservation_is_actual_forward_or_provider_usage':False,'refunds':False,'unknown_replay':False,
        'commercial_usage':'NOT_MEASURED','output_token_cap':'NOT_QUALIFIED_NATIVE_CODEX_PLUGIN'}


def public(owner,pid,sid,session_sha256):
    manifest,reference=owner._manifest(pid,sid)
    if reference['sha256']!=session_sha256:raise Conflict('MODEL_BUDGET_ORIGINAL_OWNER_CHANGED')
    state=owner._state(pid,sid);native=manifest.get('provider_auth_mode')=='OAUTH_SUBSCRIPTION'
    result={'kind':'ORIGINAL_MODEL_BUDGET','project_id':pid,'session_ref':reference,'generation':state['generation'],
        'counter':'SUBSCRIPTION_HTTP_RESERVATIONS' if native else 'API_RELAY_RESERVATIONS',
        'scope':'ORIGINAL_OWNER_ALL_NATIVE_DESCENDANTS_ALL_GENERATIONS','read_only':True,'metadata_writes':0,'model_calls':0,
        'max_calls':manifest['request']['max_calls'],'commercial_usage':'NOT_MEASURED',
        'output_token_cap':'NOT_QUALIFIED_NATIVE_CODEX_PLUGIN' if native else 'RELAY_REQUEST_PARAMETER_ENFORCED'}
    if native:
        try:value=summary(owner,manifest)
        except (Conflict,OSError,ValueError,TypeError,KeyError):return {**result,'status':'UNAVAILABLE','reservations':None}
        return {**result,'status':'AVAILABLE','reservations':value['reservations'],'transport':value['transport']}
    count=state.get('model_calls_reserved')
    if type(count) is not int or not 0<=count<=result['max_calls']:return {**result,'status':'UNAVAILABLE','reservations':None}
    return {**result,'status':'AVAILABLE','reservations':count}
