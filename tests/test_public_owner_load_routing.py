"""AUTHORED_NOT_RUN. Real Threads/SQLite alias; UNIT saved-routing and broker boundaries."""
import asyncio
from copy import deepcopy
import sqlite3
from types import SimpleNamespace
import pytest
from apps.api.conversation_runtime import ConversationRuntime
from packages.agent_sessions.service import AgentSessions
from packages.domain.tasks import canonical,digest
from packages.domain.store import Conflict,NotFound
from test_canonical_provider_generation import metadata_service
from test_conversation_owner_selection import owners


@pytest.fixture
def routing(owners):
    h=owners;source=deepcopy(h.choices[0]);pid=source['project_id'];sid=source['session_ref']['id'];cid=h.cid
    manifest={'project_id':pid,'id':sid,'request':{'conversation_ref':{'id':cid,'revision':1},'workspace_ref':None}}
    ref={'id':sid,'revision':1,'sha256':digest(manifest)};source['session_ref']=ref
    with h.store.connect() as db:
        alias=h.service.threads._alias(db,{'kind':'original','project_id':pid,'session_ref':{'id':sid}})
        _,saved=h.service.threads.checked_alias(alias);saved['session_ref']=ref
        db.execute('UPDATE conversation_source_aliases SET source_json=?,source_sha256=? WHERE alias_id=?',(canonical(saved),digest(saved),alias['alias_id']))
    h.views[sid]['session_ref']=ref
    h.current={'status':'SELECTED','original_source':source};h.service.current_owner=lambda _scope:deepcopy(h.current)
    app=SimpleNamespace(state=SimpleNamespace(agent_sessions=h.service.sessions,native_messages=SimpleNamespace(),canonical_conversations=h.service))
    h.runtime=ConversationRuntime(app,h.service.threads,None);h.manifest=manifest;h.source=source
    return h


def test_same_actor_remains_resumable_after_organization_move_and_rename(routing):
    h=routing;pid=h.store.create_project('Moved conversation','UNIT')['id']
    with h.store.connect() as db:h.service.threads.move(db,h.cid,pid,updated_at='UNIT_MOVE')
    before=deepcopy(h.calls);h.runtime.admit_load(h.manifest,{})
    assert h.calls==before and h.manifest['request']['conversation_ref']['revision']==1
    assert h.service.threads.view(h.cid)['project_id']==pid


def test_retired_actor_cannot_resume_as_the_selected_replacement(routing):
    h=routing;h.current['original_source']=deepcopy(h.choices[1])
    with pytest.raises(Conflict,match='retired or replaced'):h.runtime.admit_load(h.manifest,{})


def test_pending_configuration_blocks_raw_old_actor_load(routing):
    h=routing;h.current={'status':'PENDING'}
    with pytest.raises(Conflict,match='pending configuration'):h.runtime.admit_load(h.manifest,{})


def test_unselected_multiple_legacy_actors_do_not_guess_a_load_target(routing):
    h=routing;h.current={'status':'LEGACY'}
    with pytest.raises(Conflict,match='Choose the current'):h.runtime.admit_load(h.manifest,{})


def test_deleted_conversation_cannot_restart_its_old_actor(routing):
    h=routing
    with h.store.connect() as db:db.execute('UPDATE conversation_threads SET deleted_at=? WHERE conversation_id=?',('UNIT_DELETED',h.cid))
    with pytest.raises(NotFound):h.runtime.admit_load(h.manifest,{})


def test_wrong_exact_manifest_cannot_borrow_registered_alias(routing):
    h=routing;other=deepcopy(h.manifest);other['request']['workspace_ref']={'id':'wrong'}
    with pytest.raises(Conflict,match='manifest differs'):h.runtime.admit_load(other,{})


def test_unregistered_legacy_actor_does_not_acquire_conversation_authority(routing):
    h=routing;legacy={'id':'unregistered','project_id':h.source['project_id'],'request':{'conversation_ref':None}}
    h.runtime.admit_load(legacy,{})
    assert h.current['original_source']==h.source


def test_generation_start_rechecks_routing_after_broker_wait_before_any_native_start(tmp_path):
    assert not str(tmp_path.resolve()).startswith('/mnt/d/')
    owner=object.__new__(AgentSessions);manifest={'project_id':'project','id':'actor','request':{}}
    state={'generation':1,'owner_status':'DISCONNECTED','original_session_id':'ses_existing','process_identity':None,'controller_identity':None}
    owner.closed=False;owner.active={};owner._identity={};owner._manifest=lambda *_args:(deepcopy(manifest),{})
    owner._state=lambda *_args:deepcopy(state);owner._alive=lambda *_args:False
    owner._path=lambda *_args:tmp_path/'untouched-runtime';owner._check_scope=lambda _m:(None,None,tmp_path)
    owner._preflight_private=lambda *_args:None;calls=[];routing={'allowed':True}
    def admit(_manifest,_op):
        calls.append(routing['allowed'])
        if not routing['allowed']:raise Conflict('UNIT actor retired while broker waited')
    owner.conversations=SimpleNamespace(admit_load=admit);owner.operation=lambda *_args:{'id':'same_load'}
    async def broker(*_args,**_kwargs):routing['allowed']=False
    owner.editor_captures=SimpleNamespace(broker=SimpleNamespace(admit=broker));owner.editor_changes=None
    db=sqlite3.connect(':memory:');owner.store=SimpleNamespace(connect=lambda:db)
    with pytest.raises(Conflict,match='retired while broker'):
        asyncio.run(owner._start('project','actor',expected_generation=1,load_operation='same_load'))
    assert calls==[True,False] and state['generation']==1 and not (tmp_path/'untouched-runtime').exists()
    db.close()


def test_single_legacy_alias_is_an_exact_target_not_a_project_guess(routing):
    h=routing;h.current={'status':'LEGACY'}
    with h.store.connect() as db:
        alias=h.service.threads._alias(db,{'kind':'original','project_id':h.choices[1]['project_id'],'session_ref':{'id':h.choices[1]['session_ref']['id']}})
        old=db.execute('SELECT * FROM conversation_native_routes WHERE conversation_id=?',(h.cid,)).fetchone()
        db.execute('DELETE FROM conversation_native_routes WHERE conversation_id=?',(h.cid,))
        db.execute('DELETE FROM conversation_source_aliases WHERE alias_id=?',(alias['alias_id'],))
        # Keep the same future message address while binding it to a real
        # native route alias, independent of the remaining original executor.
        h.service.threads.reserve_route(db,h.cid,None,created_at='UNIT_SINGLE_ALIAS',preferred_cid=old['native_conversation_id'])
    scope=h.service.threads.view(h.cid)
    originals=[row['original_source']for row in scope['sources']if (row.get('original_source')or{}).get('kind')=='original']
    assert len(originals)==1 and originals[0]['session_ref']==h.source['session_ref']
    assert scope['next_message']['conversation_id']==old['native_conversation_id']
    before=deepcopy(h.calls)
    h.runtime.admit_load(h.manifest,{})
    assert h.calls==before


def test_missing_next_message_route_refuses_load_without_owner_activity(routing):
    h=routing;before=deepcopy(h.calls)
    with h.store.connect()as db:db.execute('DELETE FROM conversation_native_routes WHERE conversation_id=?',(h.cid,))
    with pytest.raises(Conflict,match='no exact next-message route'):h.runtime.admit_load(h.manifest,{})
    assert h.calls==before


def test_rejected_historical_load_does_not_relabel_its_original_pending_task():
    from packages.agent_sessions.models import LoadSession
    owner=object.__new__(AgentSessions);ref={'id':'actor','revision':1,'sha256':'a'*64}
    manifest={'id':'actor','project_id':'project','request':{}};state={'generation':1,'owner_status':'DISCONNECTED','original_session_id':'ses_old','active_operation_id':'original_task','process_identity':None,'controller_identity':None}
    records={'original_task':{'id':'original_task','status':'DISPATCHING'}};owner.active={};owner._alive=lambda _p:False
    owner._manifest=lambda *_a:(manifest,ref);owner._state=lambda *_a:deepcopy(state);owner.get=lambda *_a:deepcopy(state)
    def claim(_p,_s,key,kind,request):
        records[key]={'id':key,'kind':kind,'request':request,'status':'DISPATCHING'};return deepcopy(records[key]),True
    owner._claim=claim;owner.operation=lambda _p,_s,key:deepcopy(records[key])
    owner._finish=lambda _p,_s,key,status,**values:records[key].update(status=status,**values)
    def reject(_m,_op):raise Conflict('UNIT retired actor')
    owner.conversations=SimpleNamespace(admit_load=reject)
    with pytest.raises(Conflict,match='retired actor'):
        asyncio.run(owner.load('project','actor',LoadSession(operation_id='rejected_load',session_ref=ref,expected_generation=1)))
    assert records['original_task']['status']=='DISPATCHING' and records['rejected_load']['status']=='FAILED'
