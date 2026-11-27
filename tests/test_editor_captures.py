"""AUTHORED_NOT_RUN. Actual Store/observer/cohort API; no IDE, model or OS effects."""
from copy import deepcopy
import time

from fastapi.testclient import TestClient
import pytest

from apps.api.agent_collaboration import register_agent_collaboration_routes
from packages.agent_sessions.editor_captures import EditorCaptureRequest
from packages.agent_sessions.editor_observers import EditorObserverCommand, EditorObservers, observer_request
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_session_full_diffs import coding
from test_editor_observers import publish, observed, address
from test_session_diffs import app_for


def request(scene, *, operation='capture_one', permission=None):
    return EditorCaptureRequest(operation_id=operation,session_ref=scene['ref'],generation=1,
        workspace_ref=scene['workspace'],editor_client_id='requesting_client',permission_ref=permission)


def capture(scene, *, operation='capture_one', permission=None):
    body=request(scene,operation=operation,permission=permission)
    return body,scene['value'].editor_captures.request(scene['pid'],scene['ident'],body)


def state(scene,body,*,dispatch=False):
    return scene['value'].editor_captures.status(scene['pid'],scene['ident'],body.operation_id,
        client=body.editor_client_id,include_dispatch=dispatch)


def respond(scene, ref, *, window='one', operation='response_one', dirty=False, snapshot=None):
    value=deepcopy(snapshot) if snapshot else observed(scene,dirty=dirty)
    value['capture_ref']=ref
    return publish(scene,window=window,operation=operation,snapshot=value)


def test_same_original_capture_collects_each_registered_window_and_never_grants_permission(coding):
    s=coding;publish(s,window='one');publish(s,window='two',operation='register_two')
    body,original=capture(s);waiting=state(s,body,dispatch=True)
    assert waiting['state']=='WAITING' and waiting['waiting_windows']==2
    assert all(set(v)=={'observer_id','editor_client_id','editor_window_id','project_id','capture_ref','expires_at_ms'} for v in waiting['dispatch_requests'])
    respond(s,original['capture_ref']);assert state(s,body)['state']=='WAITING'
    respond(s,original['capture_ref'],window='two',operation='response_two')
    ready=state(s,body);assert ready['state']=='READY' and ready['ready_windows']==2
    assert ready['permission_granted'] is False and ready['workspace_writes']==ready['model_calls']==ready['native_posts']==0
    assert ready['status_scope']=='CURRENT_METADATA_SNAPSHOT_NOT_EXECUTION_LEASE'
    assert s['value'].editor_captures.operation(s['pid'],s['ident'],body.operation_id,client=body.editor_client_id)==original


def test_missing_dirty_and_unavailable_members_are_explicit_current_states_without_replay(coding):
    s=coding;publish(s,window='one');body,original=capture(s)
    assert state(s,body)['state']=='WAITING'
    respond(s,original['capture_ref'],dirty=True);assert state(s,body)['state']=='DIRTY'
    partial={'captured_at_epoch_ms':int(time.time()*1000),'availability':'UNAVAILABLE','root_identity':None,
        'documents':[],'documents_complete':False,'unavailable_reason':'EDITOR_UNAVAILABLE','permission_ref':None}
    respond(s,original['capture_ref'],operation='unavailable',snapshot=partial);assert state(s,body)['state']=='UNAVAILABLE'
    assert s['value'].editor_captures.request(s['pid'],s['ident'],body)==original


def test_new_or_retired_window_invalidates_the_frozen_membership(coding):
    s=coding;publish(s,window='one');body,original=capture(s)
    publish(s,window='new_window',operation='new_member')
    respond(s,original['capture_ref']);assert state(s,body)['state']=='MEMBERSHIP_CHANGED'


def test_exact_permission_purpose_can_be_observed_without_transferring_its_reply_authority(coding):
    s=coding;native={'id':'per_cohort','sessionID':'ses_root','permission':'edit','patterns':['a.py']}
    ref={'id':native['id'],'generation':1,'sha256':digest(native)}
    value=s['value'];saved=value._state(s['pid'],s['ident']);saved['pending_permissions']=[{'request_ref':ref,'native_request':native}]
    value._write(value._path(s['pid'],s['ident'],'state.json'),saved,replace=True)
    publish(s,window='one');publish(s,window='two',operation='second')
    body,original=capture(s,permission=ref);respond(s,original['capture_ref']);respond(s,original['capture_ref'],window='two',operation='second_response')
    value.editor_observers.assert_permission(s['manifest'],ref)
    with pytest.raises(Conflict):value.editor_observers.assert_permission(s['manifest'],{**ref,'sha256':'f'*64})
    saved['pending_permissions']=[];value._write(value._path(s['pid'],s['ident'],'state.json'),saved,replace=True)
    assert state(s,body)['state']=='TARGET_CHANGED'


def test_capture_ref_cannot_be_used_by_an_unregistered_or_different_window(coding):
    s=coding;publish(s,window='one');body,original=capture(s)
    with pytest.raises(Conflict,match='registered observer'):respond(s,original['capture_ref'],window='unregistered')
    publish(s,window='other',operation='other_registration')
    with pytest.raises(Conflict,match='frozen capture membership'):respond(s,original['capture_ref'],window='other',operation='wrong_member')
    assert state(s,body)['state']=='MEMBERSHIP_CHANGED'


def test_snapshot_before_request_and_expired_or_restarted_requests_do_not_create_fresh_evidence(coding,monkeypatch):
    from packages.agent_sessions import editor_captures as module
    s=coding;publish(s,window='one');old=observed(s);old['captured_at_epoch_ms']-=100
    body,original=capture(s)
    with pytest.raises(Conflict,match='must follow'):respond(s,original['capture_ref'],snapshot=old)
    clock=module.time.time
    with monkeypatch.context() as m:
        m.setattr(module.time,'time',lambda:clock()+20)
        assert state(s,body)['state']=='EXPIRED'
    s['value'].editor_observers=EditorObservers(s['value']);assert state(s,body)['state']=='RESTARTED'
    assert s['value'].editor_captures.operation(s['pid'],s['ident'],body.operation_id,client=body.editor_client_id)==original


def test_capture_immutable_input_and_requester_identity_are_preserved_after_newer_observations(coding):
    s=coding;publish(s,window='one');body,original=capture(s);respond(s,original['capture_ref'])
    assert s['value'].editor_captures.request(s['pid'],s['ident'],body)==original
    with pytest.raises(Conflict):s['value'].editor_captures.request(s['pid'],s['ident'],body.model_copy(update={'editor_client_id':'forged'}))
    with pytest.raises(Conflict):s['value'].editor_captures.operation(s['pid'],s['ident'],body.operation_id,client='another')


def test_dirty_event_after_collective_ready_revokes_current_readiness(coding):
    s=coding;publish(s,window='one');body,original=capture(s);respond(s,original['capture_ref'])
    assert state(s,body)['state']=='READY';publish(s,window='one',operation='new_dirty',dirty=True)
    assert state(s,body)['state']=='WAITING'  # A changed fingerprint requires a new capture for the original purpose.
    with pytest.raises(Conflict):s['value']._editor_assert(s['manifest'])


def test_metadata_only_capture_capacity_retains_original_unknowns_and_never_starts_execution(coding,monkeypatch):
    from packages.agent_sessions import editor_captures as module
    s=coding;publish(s,window='one');body,original=capture(s);monkeypatch.setattr(module,'MAX_COHORTS',1)
    with pytest.raises(Conflict,match='CAPACITY'):capture(s,operation='not_accepted')
    assert s['value'].editor_captures.operation(s['pid'],s['ident'],body.operation_id,client=body.editor_client_id)==original
    assert original['permission_granted'] is False


def test_legacy_observer_hash_does_not_gain_an_absent_capture_field(coding):
    s=coding;body,_=publish(s,window='one');legacy=body.model_dump();legacy['observation'].pop('capture_ref')
    assert observer_request(body)==legacy


def test_api_immutable_intent_is_distinct_from_live_status_and_private_dispatch_is_optional(coding):
    s=coding;publish(s,window='one');app=app_for(s['value']);register_agent_collaboration_routes(app,service=s['value']);client=TestClient(app)
    base=f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}/editor-capture-cohorts";body=request(s)
    original=client.post(base,json=body.model_dump());assert original.status_code==200
    status=client.get(base+'/capture_one/status',params={'editor_client_id':'requesting_client'})
    assert status.status_code==200 and status.json()['state']=='WAITING' and 'dispatch_requests' not in status.json()
    assert client.get(base+'/capture_one',params={'editor_client_id':'requesting_client'}).json()==original.json()
    assert client.get(base+'/capture_one',params={'editor_client_id':'forged'}).status_code==409


def test_overlapping_cross_project_window_only_reports_its_own_scope_without_inheriting_permission(coding):
    from packages.domain.local_workspaces import WorkspaceInput
    s=coding;value=s['value'];pid=value.store.create_project('Other observation scope','UNIT same physical root')['id']
    binding=value.workspaces.bind(pid,WorkspaceInput(idempotency_key='other-observed-root',root=str(s['folder']),allowed_write_paths=['a.py']))
    workspace={k:binding[k] for k in ('id','revision','binding_sha256')};sid='d'*32
    manifest=deepcopy(s['manifest']);manifest.update(id=sid,project_id=pid,directory_identity=binding['root_identity']);manifest['request'].update(workspace_ref=workspace,allowed_effects=[])
    ref={'id':sid,'revision':1,'sha256':digest(manifest)}
    value._write(value._path(pid,sid,'manifest.json'),manifest)
    value._write(value._path(pid,sid,'state.json'),{'generation':1,'original_session_id':'ses_other','messages':[],
        'owner_status':'DISCONNECTED','native_status':'idle','active_operation_id':None})
    other={**s,'pid':pid,'ident':sid,'binding':binding,'workspace':workspace,'ref':ref,'manifest':manifest}
    publish(s,window='one');publish(other,window='other',operation='other_project_observer')
    native={'id':'per_cross_project_capture','sessionID':'ses_root','permission':'edit','patterns':['a.py']};permission={'id':native['id'],'generation':1,'sha256':digest(native)}
    target=value._state(s['pid'],s['ident']);target['pending_permissions']=[{'request_ref':permission,'native_request':native}]
    value._write(value._path(s['pid'],s['ident'],'state.json'),target,replace=True)
    body,original=capture(s,permission=permission);respond(s,original['capture_ref']);_,own=respond(other,original['capture_ref'],window='other',operation='other_capture')
    assert state(s,body)['state']=='READY';value.editor_observers.assert_permission(s['manifest'],permission)
    assert own['observer']['project_id']==pid and own['observer']['workspace_ref']==workspace
    assert value._manifest(pid,sid)[0]['request']['allowed_effects']==[]
    assert 'permission_ref' not in own['observer'] and own['permission_granted'] is False



def test_concurrent_purposes_retain_independent_unchanged_document_evidence(coding):
    s=coding;publish(s,window='one');a,first=capture(s);b,second=capture(s,operation='concurrent_capture')
    respond(s,first['capture_ref'],operation='answer_first');respond(s,second['capture_ref'],operation='answer_second')
    assert state(s,a)['state']==state(s,b)['state']=='READY'
    publish(s,window='one',operation='same_document_facts')
    assert state(s,a)['state']==state(s,b)['state']=='READY'
    publish(s,window='one',operation='changed_document_facts',dirty=True)
    assert state(s,a)['state']==state(s,b)['state']=='WAITING'
    respond(s,first['capture_ref'],operation='changed_first',dirty=True);respond(s,second['capture_ref'],operation='changed_second',dirty=True)
    assert state(s,a)['state']==state(s,b)['state']=='DIRTY'


def test_new_same_document_observation_does_not_refresh_an_old_purpose_time(coding,monkeypatch):
    from packages.agent_sessions import editor_captures as module
    s=coding;publish(s,window='one');body,original=capture(s);respond(s,original['capture_ref']);clock=module.time.time
    with monkeypatch.context() as m:
        m.setattr(module.time,'time',lambda:clock()+6)
        publish(s,window='one',operation='later_same_facts')
        assert state(s,body)['state']=='STALE'
