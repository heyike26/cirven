"""AUTHORED_NOT_RUN: real adapter execution rows/scopes/ledger and callback.

Only bridge transport, live process and application source/profile checker are
UNIT boundaries. No socket, account contents, SDK/model, browser or Windows.
"""
import asyncio
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import uuid
import pytest
from packages.domain.store import Store,Conflict
from packages.domain.tasks import digest,canonical,now
from packages.adapters import subscription_execution_meter as module
from packages.adapters.subscription_runtime import subscription_budget_summary


@pytest.fixture(params=['opencode-planning-http','opencode-desktop-http'])
def metered(tmp_path,monkeypatch,request):
    store=Store(tmp_path/'db.sqlite3');pid=store.create_project('Adapter meter','UNIT authority')['id'];ident=str(uuid.uuid4());root=tmp_path/'runtime';root.mkdir();cwd=root/'cwd';cwd.mkdir()
    req={'project_id':pid,'operation_id':'original','budget':{'max_calls':2,'max_output_tokens':128,'timeout_seconds':60}}
    scope={'UNIT_FROZEN_SOURCE':'exact','input_hash':digest(req)}
    with store.connect() as c:
        c.execute("INSERT INTO agent_executions(id,project_id,operation_id,request_hash,runtime,workspace,execution_status,verification_status,session_id,model_profile,sdk_version,request,created_at,cancel_requested) VALUES (?,?,?,?,?,?,'RUNNING','NOT_RUN','ses_original','{}','1.19.0',?,?,0)",(ident,pid,'original',digest(req),request.param,str(root),canonical(req),now()))
        c.execute('INSERT INTO agent_scopes VALUES (?,?,?,?,?,?,?,?,?,?)',(ident,canonical(scope),digest(scope),(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(),ident,None,'0',None,None,ident))
    context={'workspace':root,'process':SimpleNamespace(returncode=None),'session_id':'ses_original','done':asyncio.Event()};calls=[]
    def current(project,body,*,verify_executable):assert project==pid and body==req and verify_executable is False;calls.append('METADATA_CURRENT_SCOPE');return deepcopy(scope)
    adapter=SimpleNamespace(service=store,runtime=request.param,active={ident:context},_scope=current)
    class Bridge:
        base_url='http://127.0.0.1:1';key='a'*64
        def __init__(self,callback,loop):self.callback=callback
        def close(self):pass
    monkeypatch.setattr(module,'NativeMessageBridge',Bridge)
    with store.connect() as c:row=dict(c.execute('SELECT * FROM agent_executions WHERE id=?',(ident,)).fetchone())
    async def create():return module.SubscriptionExecutionMeter(adapter,row,req,scope,context,SimpleNamespace(model_id='UNIT_MODEL',auth_mode='OAUTH_SUBSCRIPTION',provider_id='openai'),cwd)
    meter=asyncio.run(create());meter.bind('ses_original')
    body={'schema':'eeas.adapter-model-authority-call.v1','owner_ref':meter.owner_ref,'generation':1,'root_native_session_id':'ses_original','native_session_id':'ses_original','user_message_id':'msg_original','agent':'planner','step':1}
    return {'meter':meter,'store':store,'pid':pid,'id':ident,'context':context,'request':req,'scope':scope,'adapter':adapter,'body':body,'calls':calls}


def call(s,body):return asyncio.run(s['meter']._callback('/v1/call',body))
def plan(s):return call(s,s['body'])
def hop(s,p,n=1,**changes):return {**s['body'],'schema':'eeas.adapter-model-authority-http-hop.v1','hop':n,'request_sha256':digest({'UNIT':n}),'plan_sha256':p['plan_sha256'],**changes}


def test_original_adapter_row_has_separate_execution_identity_and_bounded_attempts(metered):
    s=metered;p=plan(s);assert 'execution_ref' in s['body']['owner_ref'] and 'session_ref' not in s['body']['owner_ref']
    assert call(s,hop(s,p))['reserved']==1 and call(s,hop(s,p,2))['reserved']==2
    with pytest.raises(Conflict,match='ALLOWANCE_EXHAUSTED'):call(s,hop(s,p,3))
    summary=subscription_budget_summary(s['request']['budget'],meter=s['meter'])
    assert summary['http_reservations']==2 and summary['rpc_attempts'] is None and summary['hard_rpc_cap']=='NOT_QUALIFIED'


@pytest.mark.parametrize('change',['owner','scope','cancel','process','context','done'])
def test_changed_original_execution_cannot_borrow_another_request_or_source(metered,change):
    s=metered;p=plan(s);before=s['meter'].path.read_bytes();body=hop(s,p)
    if change=='owner':body['owner_ref']={**body['owner_ref'],'project_id':'other'}
    elif change=='scope':s['scope']['UNIT_FROZEN_SOURCE']='newer'
    elif change=='cancel':
        with s['store'].connect() as c:c.execute('UPDATE agent_executions SET cancel_requested=1 WHERE id=?',(s['id'],))
    elif change=='process':s['context']['process'].returncode=0
    elif change=='context':s['adapter'].active[s['id']]={**s['context']}
    else:s['context']['done'].set()
    with pytest.raises(Conflict):call(s,body)
    assert s['meter'].path.read_bytes()==before


def test_saved_unknown_hop_is_not_released_twice_or_refunded(metered):
    s=metered;p=plan(s);body=hop(s,p);call(s,body);before=s['meter'].path.read_bytes()
    with pytest.raises(Conflict,match='ALREADY_RESERVED'):call(s,body)
    assert s['meter'].path.read_bytes()==before


def test_bridge_close_seals_future_admission_but_retains_exact_ledger(metered):
    s=metered;p=plan(s);before=s['meter'].path.read_bytes();assert asyncio.run(s['meter'].close())
    assert s['meter'].close_confirmed
    with pytest.raises(Conflict):call(s,hop(s,p))
    assert s['meter'].path.read_bytes()==before


def test_private_meter_environment_admits_only_the_pinned_plugin_and_no_ambient_scope(metered):
    s=metered;m=s['meter'];env=m.environment({'OPENCODE_PURE':'true','OPENCODE_DISABLE_EXTERNAL_SKILLS':'true'})
    assert env['OPENCODE_PURE']=='false' and env['OPENCODE_DISABLE_EXTERNAL_SKILLS']=='true'
    assert m.plugins_match([m.entry]) and m.plugins_match([[m.entry,{}]]) and not m.plugins_match([m.entry,'unreviewed-plugin'])
    assert 'account' not in canonical(s['meter']._read())


def test_unconfirmed_bridge_shutdown_never_reports_verified_cleanup(metered,monkeypatch):
    s=metered;m=s['meter'];p=plan(s);before=m.path.read_bytes()
    def unconfirmed():raise OSError('UNIT owned bridge close unconfirmed')
    monkeypatch.setattr(m.bridge,'close',unconfirmed)
    assert asyncio.run(m.close()) is False and m.closed and not m.close_confirmed
    assert s['context']['subscription_meter_cleanup_error']=='OSError'
    with pytest.raises(Conflict):call(s,hop(s,p))
    assert m.path.read_bytes()==before
