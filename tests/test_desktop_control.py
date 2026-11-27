"""Real authority records/CAS/default USER; no simulated native success."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from pathlib import Path
import pytest
from pydantic import ValidationError
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from apps.api.desktop_control import register_desktop_control_routes
from packages.domain.desktop_control import DesktopControl,DesktopDelegateInput,DesktopTakeoverInput
from packages.domain.workspace_desktop import WorkspaceDesktop
from packages.domain.store import Conflict
from packages.domain.local_workspaces import canonical,digest,sha
from test_direct_actions import scene,provider,body,run,ORIGINAL,no_pipeline


def control(tmp_path,*,token='a'*64,**kwargs):
    ws=WorkspaceDesktop(tmp_path,token=token,owner_pid=123,platform='posix')
    return DesktopControl(ws,**kwargs)


def takeover(value,op='take',**extra):
    return asyncio.run(value.takeover({'operation_id':op,'expected_control_epoch':value.get()['control_epoch'],**extra}))


def delegation(epoch=1):
    return {'operation_id':'delegate','surface_ref':{'id':'f'*64,'revision':1,'sha256':'a'*64},
        'expected_input_epoch':0,'expected_control_epoch':epoch,
        'execution_ref':{'project_id':'internal-desktop','execution_id':'actual-execution-required','request_sha256':'b'*64},
        'user_requested_delegation':True}


def test_default_user_and_cua_import_cannot_grant_qualified_input(tmp_path):
    value=control(tmp_path);state=value.get()
    assert value.lock is value.ws.lock
    assert state['owner']=='USER' and state['status']=='USER_CONTROL' and state['control_epoch']==1
    assert state['lease_ref'] is None and state['execution_ref'] is None and not state['queued_actions_enabled']
    assert not state['capabilities']['delegation_ready'] and not state['capabilities']['desktop_input_routes_qualified']
    assert state['capabilities']['direct_file_cas_requires_desktop_lease'] is False and state['model_calls']==0
    assert not any(word in json.dumps(state).lower() for word in ['hwnd','owner_pid','marker_name'])
    claimed=control(tmp_path/'claimed-sdk-import',capability_reader=lambda:{
        'desktop_input_routes_qualified':True,'owned_queue_drain_qualified':True})
    # Declarative capability alone has no actual queue/source/transport route.
    assert claimed.get()['capabilities']['delegation_ready'] is False
    with pytest.raises(Conflict):value.delegate(delegation())
    assert value.get()['owner']=='USER'
    with pytest.raises(Conflict):
        with value.dispatch_guard({'id':'f'*64,'revision':1,'sha256':'a'*64},{'id':'f'*64,'revision':1,'sha256':'a'*64},0,1):pass


def test_takeover_even_already_user_advances_epoch_and_idempotency_does_not_revoke_twice(tmp_path):
    value=control(tmp_path);request={'operation_id':'take','expected_control_epoch':1}
    first=asyncio.run(value.takeover(request))
    assert first['owner']=='USER' and first['control_epoch']==2 and first['current'] and not first['replayed']
    assert first['cancellation']['status']=='NOT_NEEDED' and first['cancellation']['confirmed']
    assert first['cancellation']['queued_inputs']['discarded_count']==0
    assert first['model_calls']==0 and first['scene_effect']=='NO_USER_WINDOW_MUTATION_OR_APP_KILL'
    replay=asyncio.run(value.takeover(request));assert replay['replayed'] and replay['control_epoch']==2
    with pytest.raises(Conflict):asyncio.run(value.takeover(request|{'expected_control_epoch':2}))
    with pytest.raises(Conflict):value.delegate(delegation(1))
    assert value.get()['owner']=='USER'


def test_concurrent_takeover_cas_has_one_actual_record_winner(tmp_path):
    value=control(tmp_path);barrier=threading.Barrier(8)
    def actor(index):
        barrier.wait()
        try:return asyncio.run(value.takeover({'operation_id':'take-'+str(index),'expected_control_epoch':1}))
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(actor,range(8)))
    winners=[x for x in results if x is not None]
    assert len(winners)==1 and winners[0]['control_epoch']==2 and value.get()['owner']=='USER'
    assert len(list(value._path('operations').glob('*.json')))==1


def test_restart_revokes_old_lifetime_and_historical_replay_does_not_restore_old_authority(tmp_path):
    first=control(tmp_path);request={'operation_id':'take','expected_control_epoch':1}
    accepted=asyncio.run(first.takeover(request));assert accepted['current']
    restarted=DesktopControl(first.ws)
    assert restarted.get()['owner']=='USER' and restarted.get()['control_epoch']==3
    replay=asyncio.run(restarted.takeover(request))
    assert replay['replayed'] and replay['current'] is False and replay['owner']=='USER' and replay['lease_ref'] is None
    with pytest.raises(Conflict,match='lifetime'):takeover(first,'old-owner')
    changed=control(tmp_path,token='b'*64)
    assert changed.get()['owner']=='USER' and changed.get()['control_epoch']==4
    assert changed.get()['execution_ref'] is None


def test_current_host_hmac_tamper_is_rejected_not_reclassified_as_new_main(tmp_path):
    first=control(tmp_path);head=first._path('head.json');wrapper=json.loads(head.read_bytes())
    wrapper['payload']['sha256']='f'*64;wrapper['sha256']=digest(wrapper['payload'])
    head.write_text(canonical(wrapper))
    with pytest.raises(Conflict,match='integrity'):DesktopControl(first.ws)


@pytest.mark.parametrize('patch',[
    {'user_requested_delegation':False},{'user_requested_delegation':1},{'expected_control_epoch':True},
    {'expected_input_epoch':True},{'owner':'AGENT'},{'hwnd_decimal':'123'},{'lease_seconds':0},
    {'execution_ref':{'project_id':'p','execution_id':'x','request_sha256':'not-a-source-hash'}},
])
def test_model_cannot_self_authorize_change_owner_or_supply_native_window(patch):
    with pytest.raises(ValidationError):DesktopDelegateInput.model_validate(delegation()|patch)


def test_takeover_optional_scope_is_exact_pair_with_strict_epoch():
    for patch in [{'expected_control_epoch':True},{'expected_input_epoch':0},
                  {'surface_ref':delegation()['surface_ref']},{'kill_apps':True}]:
        with pytest.raises(ValidationError):DesktopTakeoverInput.model_validate({'operation_id':'take','expected_control_epoch':1}|patch)


def test_actual_http_requires_host_header_and_never_claims_agent_on_linux(tmp_path):
    value=control(tmp_path);app=FastAPI();app.state.workspace_desktop=value.ws
    register_desktop_control_routes(app,None,tmp_path,workspace=value.ws)
    @app.exception_handler(Conflict)
    async def conflict(request,exc):return JSONResponse({'error':str(exc)},status_code=409)
    with TestClient(app) as client:
        root='/api/desktop-host/control';headers={'X-EEAS-Desktop-Host':'a'*64}
        assert client.get(root+'/get').status_code==409
        state=client.get(root+'/get',headers=headers).json();assert state['owner']=='USER'
        assert client.post(root+'/delegate',json=delegation(state['control_epoch']),headers=headers).status_code==409
        request={'operation_id':'http-takeover','expected_control_epoch':state['control_epoch']}
        taken=client.post(root+'/takeover',json=request,headers=headers)
        assert taken.status_code==200 and taken.json()['owner']=='USER' and taken.json()['control_epoch']==state['control_epoch']+1
        assert client.post(root+'/takeover',json=request,headers=headers).json()['replayed']
        assert client.post(root+'/takeover',json=request|{'kill_apps':True},headers=headers).status_code==422


def test_no_desktop_lease_takeover_preserves_actual_original_cli_single_file_cas(scene,tmp_path):
    wire,profile=provider(scene,'wait')
    desktop=WorkspaceDesktop(tmp_path/'desktop-control',token='a'*64,owner_pid=123,platform='posix')
    value=DesktopControl(desktop,transport=scene['service'].transport)
    pending={}
    def original_action():
        try:pending['result']=run(scene,body(scene,profile))
        except BaseException as exc:pending['error']=exc
    worker=threading.Thread(target=original_action);worker.start()
    try:
        assert wire.entered.wait(35),'Actual original CLI did not reach the external model fixture'
        state=takeover(value,'user-keeps-desktop')
        assert state['owner']=='USER' and state['cancellation']['status']=='NOT_NEEDED'
        assert state['execution_ref'] is None and state['model_calls']==0
        with scene['client'].app.state.store.connect() as db:
            row=db.execute('SELECT * FROM agent_executions WHERE project_id=?',(scene['pid'],)).fetchone()
        assert row and not row['cancel_requested']
        wire.release.set();worker.join(timeout=45)
        assert not worker.is_alive() and 'error' not in pending,pending.get('error')
        result=pending['result'];expected=ORIGINAL.decode().replace('Firsyt','First').encode()
        assert result['status']=='APPLIED' and result['after_ref']['sha256']==sha(expected)
        assert scene['target'].read_bytes()==expected and len(wire.requests)==1
        assert value.get()['owner']=='USER' and not value.get()['queued_actions_enabled']
        no_pipeline(scene,1)
    finally:
        wire.release.set();worker.join(timeout=45)



def test_takeover_intent_is_latched_before_shared_mutex_drain_without_fake_native_action(tmp_path):
    value=control(tmp_path);entered=threading.Event();release=threading.Event()
    def existing_mutex_work():
        with value.lock:
            entered.set();release.wait(timeout=5)
    worker=threading.Thread(target=existing_mutex_work);worker.start();assert entered.wait(2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(lambda:asyncio.run(value.takeover({'operation_id':'take-before-drain','expected_control_epoch':1})))
        assert value._takeover_intent.wait(2)
        assert not future.done()
        with pytest.raises(Conflict,match='Human takeover'):
            with value.dispatch_guard({}, {},0,1):pass
        release.set();result=future.result(timeout=5)
    worker.join(timeout=3)
    assert result['owner']=='USER' and result['control_epoch']==2 and not result['queued_actions_enabled']
