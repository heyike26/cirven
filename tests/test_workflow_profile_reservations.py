"""AUTHORED_NOT_RUN: real workflow callback and Store/RPC reservation guard.

ProviderWire construction/start, live SDK/editor freshness are explicit UNIT
boundaries. No socket, SDK, model, user database or Windows qualification.
"""
import asyncio
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from packages.domain.store import Conflict
from packages.agent_sessions import workflow
from test_session_full_diffs import coding
from test_model_authority import setup


@pytest.fixture
def routed(coding,monkeypatch):
    s=coding;owner=s['value'];main=SimpleNamespace(profile_id='main',fingerprint='fixture-profile');extra=SimpleNamespace(profile_id='extra',fingerprint='extra-profile')
    s['manifest']=deepcopy(s['manifest']);s['manifest']['request'].update(max_calls=2,max_output_tokens=128)
    s['manifest']['workflow_provider_fingerprints']={'main':main.fingerprint,'extra':extra.fingerprint}
    owner._write(owner._path(s['pid'],s['ident'],'manifest.json'),s['manifest'],replace=True);s['ref']=owner._manifest(s['pid'],s['ident'])[1]
    *_,body=setup(s);ctx=owner.active[s['ident']]
    ctx.update(profile=main,process=SimpleNamespace(returncode=None),expires_at=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat())
    owner._update(s['pid'],s['ident'],model_calls_reserved=0)
    monkeypatch.setattr('packages.agent_sessions.editor_relay_capture.refresh_relay_capture',lambda *_a,**_k:None)
    monkeypatch.setattr(owner,'_editor_assert',lambda *_a,**_k:None)
    monkeypatch.setattr(workflow,'workflow_scope',lambda *_a,**_k:{'profiles':{'main':main,'extra':extra}})
    captured={}
    class PreparedBoundary(Exception):pass
    class UnitWire:
        def __init__(self,profile,*,admit,complete,max_output_tokens,include_input_bytes=False):
            assert include_input_bytes is True
            captured.update(profile=profile,admit=admit,complete=complete)
        def start(self):raise PreparedBoundary('UNIT captured callback before any listener or plugin preparation')
    monkeypatch.setattr(workflow,'ProviderWire',UnitWire)
    with pytest.raises(PreparedBoundary):asyncio.run(workflow.prepare_workflow(owner,s['manifest'],ctx,{},{}))
    assert captured['profile'] is extra
    s.update(context=ctx,captured=captured);return s


def test_extra_profile_callback_reserves_on_original_root_shared_counter(routed):
    s=routed;owner=s['value'];extra=s['captured']['admit'];a=extra('a'*64)
    b=owner._reserve_rpc(s['pid'],s['ident'],'b'*64,expected_context=s['context'])
    assert a!=b and owner._state(s['pid'],s['ident'])['model_calls_reserved']==2
    with pytest.raises(Conflict,match='allowance'):extra('c'*64)
    assert owner._read(owner._path(s['pid'],s['ident'],'rpc',a+'.json'))['operation_id']=='unit_task'


@pytest.mark.parametrize('change',['same_generation_replacement','new_generation','ended_original_process','closing_original_context'])
def test_old_extra_profile_never_borrows_new_owner_or_stopped_process(routed,change):
    s=routed;owner=s['value'];original=s['context']
    if change=='same_generation_replacement':owner.active[s['ident']]={**original}
    elif change=='new_generation':
        # Separate generation protection is meaningful even if a bad caller
        # retains the same context object instead of replacing it.
        owner._update(s['pid'],s['ident'],generation=2)
    elif change=='ended_original_process':original['process'].returncode=0
    else:original['closing']=True
    before=owner._state(s['pid'],s['ident']);rpc=owner._path(s['pid'],s['ident'],'rpc');saved=list(rpc.glob('*.json')) if rpc.exists() else []
    with pytest.raises(Conflict):s['captured']['admit']('a'*64)
    assert owner._state(s['pid'],s['ident'])['model_calls_reserved']==before['model_calls_reserved']==0
    assert (list(rpc.glob('*.json')) if rpc.exists() else [])==saved


def test_old_callback_cannot_make_metadata_query_start_a_model_or_modify_new_owner(routed):
    s=routed;owner=s['value'];owner.active[s['ident']]={**s['context']};before=owner._state(s['pid'],s['ident'])
    with pytest.raises(Conflict):s['captured']['admit']('a'*64)
    # The rejected callback leaves the exact existing original operation and
    # all new-owner quota intact. No replay/second execution loop is involved.
    assert owner.operation(s['pid'],s['ident'],'unit_task')['status']=='DISPATCHING'
    assert owner._state(s['pid'],s['ident'])==before
