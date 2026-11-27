"""AUTHORED_NOT_RUN: actual Store/manifest/binding/API and process reads.

No native editor, file write command, original CLI or model is exercised here.
Process-ending cases explicitly label their injected OS observation boundary.
"""
from copy import deepcopy
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from apps.api.agent_collaboration import register_agent_collaboration_routes
from packages.agent_sessions.editor_observers import (EditorObserverAddress, EditorObserverCommand,
    EditorObservation, EditorObservers)
from packages.domain.editor_handoff_scope import assert_handoff_free
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest
from test_session_full_diffs import coding
from test_session_diffs import app_for


def address(scene, *, window='original_window', action='OBSERVE', client='paired_client'):
    return EditorObserverAddress(session_ref=scene['ref'],generation=1,workspace_ref=scene['workspace'],
        editor_client_id=client,editor_window_id=window,editor_process_id=os.getpid(),
        editor_parent_process_id=os.getppid(),action=action)


def observed(scene, *, dirty=False, permission=None):
    return {'captured_at_epoch_ms':int(time.time()*1000),'availability':'OBSERVED','root_identity':{k:str(v) for k,v in scene['binding']['root_identity'].items()},
        'documents':[{'path':'a.py','document_version':7,'content_sha256':digest('UNIT_EDITOR_TEXT_METADATA_ONLY'),
            'is_dirty':dirty}],'documents_complete':True,'unavailable_reason':None,'permission_ref':permission}


def publish(scene, *, operation='observe_1', dirty=False, window='original_window', permission=None,
            action='OBSERVE', snapshot=None):
    service=scene['value']; target=address(scene,window=window,action=action)
    prepared=service.editor_observers.prepare(scene['pid'],scene['ident'],target)
    body=EditorObserverCommand(**target.model_dump(),operation_id=operation,expected_revision=prepared['revision'],
        preparation_sha256=prepared['preparation_sha256'],observation=snapshot or observed(scene,dirty=dirty,permission=permission),
        user_approved=action=='RETIRE')
    return body,service.editor_observers.command(scene['pid'],scene['ident'],body)


def guard(scene, *, root=None):
    with scene['value'].store.connect() as db:
        assert_handoff_free(db,root or scene['binding']['root'],identity=scene['binding']['root_identity'])


def test_dirty_local_document_blocks_shared_writer_then_clean_observation_releases_only_its_guard(coding):
    s=coding; original={p:p.read_bytes() for p in s['folder'].iterdir() if p.is_file()}
    _,result=publish(s,dirty=True)
    assert result['permission_granted'] is False and result['workspace_writes']==0
    assert result['observer']['dirty_count']==1
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):guard(s)
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):s['value']._editor_assert(s['manifest'])
    publish(s,operation='observe_saved',dirty=False);guard(s)
    assert {p:p.read_bytes() for p in original}==original


def test_observation_receipt_is_immutable_and_idempotent_after_newer_head_without_model_or_file_actions(coding):
    s=coding;body,first=publish(s);publish(s,operation='observe_newer',dirty=True)
    replay=s['value'].editor_observers.command(s['pid'],s['ident'],body)
    read=s['value'].editor_observers.operation(s['pid'],s['ident'],body.operation_id,client=body.editor_client_id)
    assert replay==read==first and read['observer']['revision']==1
    assert read['observation_time_scope']=='AT_ORIGINAL_COMMAND_ONLY'
    assert s['value'].editor_observers.inspect(s['pid'],first['observer']['id'],client=body.editor_client_id)['dirty_count']==1
    with pytest.raises(Conflict):s['value'].editor_observers.command(s['pid'],s['ident'],body.model_copy(update={'user_approved':True}))
    with pytest.raises(Conflict):s['value'].editor_observers.operation(s['pid'],s['ident'],body.operation_id,client='another_client')


def test_every_overlapping_paired_window_retains_its_guard_and_other_namespaces_are_unaffected(coding):
    s=coding;publish(s,window='one');publish(s,operation='second_window',window='two',dirty=True)
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):guard(s)
    publish(s,operation='one_again',window='one')
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):guard(s,root=str(s['folder']/'child'))
    with s['value'].store.connect() as db:
        assert_handoff_free(db,str(s['folder'].parent/'unrelated'),identity={'device':-1,'inode':-1})
    publish(s,operation='two_saved',window='two');guard(s)


def test_stale_clean_observation_and_backend_restart_cannot_imply_current_clean_editor(coding,monkeypatch):
    from packages.domain import editor_observer_scope as scope
    s=coding;body,first=publish(s);clock=scope.time.time
    with monkeypatch.context() as m:
        m.setattr(scope.time,'time',lambda:clock()+60)
        with pytest.raises(Conflict,match='EDITOR_OBSERVATION_REQUIRED'):guard(s)
        assert s['value'].editor_observers.inspect(s['pid'],first['observer']['id'],client='paired_client')['current'] is False
    s['value'].editor_observers=EditorObservers(s['value'])
    with pytest.raises(Conflict,match='EDITOR_OBSERVATION_REQUIRED'):guard(s)
    publish(s,operation='fresh_after_restart');guard(s)
    assert s['value'].editor_observers.operation(s['pid'],s['ident'],body.operation_id,client='paired_client')==first


def test_edit_permission_requires_its_exact_current_ref_in_each_window_not_an_earlier_clean_capture(coding):
    s=coding;native={'id':'per_exact','sessionID':'ses_root','permission':'edit','patterns':['a.py']}
    reference={'id':native['id'],'generation':1,'sha256':digest(native)}
    state=s['value']._state(s['pid'],s['ident']);state['pending_permissions']=[{'kind':'PERMISSION','request_ref':reference,'native_request':native}]
    s['value']._write(s['value']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    publish(s)
    with pytest.raises(Conflict,match='EDITOR_PERMISSION_OBSERVATION_REQUIRED'):
        s['value'].editor_observers.assert_permission(s['manifest'],reference)
    publish(s,operation='exact_pending_capture',permission=reference)
    s['value'].editor_observers.assert_permission(s['manifest'],reference)
    changed={**reference,'sha256':'e'*64}
    with pytest.raises(Conflict,match='no longer pending'):publish(s,operation='wrong_pending',permission=changed)
    with pytest.raises(Conflict,match='EDITOR_PERMISSION_OBSERVATION_REQUIRED'):
        s['value'].editor_observers.assert_permission(s['manifest'],changed)


def test_unavailable_observation_keeps_guard_and_explicit_retirement_can_preserve_a_missing_namespace(coding):
    s=coding;publish(s,dirty=True)
    retained=Path(str(s['folder'])+'-retained');s['folder'].rename(retained);s['folder'].mkdir()
    replacement=s['folder']/'a.py';replacement.write_text('REPLACEMENT_DO_NOT_TOUCH')
    snapshot={'captured_at_epoch_ms':int(time.time()*1000),'availability':'UNAVAILABLE','root_identity':None,'documents':[],
        'documents_complete':True,'unavailable_reason':'NAMESPACE_CHANGED','permission_ref':None}
    _,unknown=publish(s,operation='observe_missing',snapshot=snapshot)
    with pytest.raises(Conflict,match='EDITOR_OBSERVATION_REQUIRED'):guard(s)
    _,retired=publish(s,operation='retire_explicit',action='RETIRE',snapshot=snapshot)
    assert retired['observer']['phase']=='RETIRED' and retired['workspace_writes']==0
    assert unknown['observer']['phase']=='ACTIVE' and replacement.read_text()=='REPLACEMENT_DO_NOT_TOUCH'
    assert retained.is_dir()
    guard(s)


def test_uncaptured_or_partial_editor_state_never_claims_clean_and_retirement_rejects_dirty(coding):
    s=coding;snapshot={'captured_at_epoch_ms':int(time.time()*1000),'availability':'UNAVAILABLE','root_identity':None,'documents':[],
        'documents_complete':False,'unavailable_reason':'DOCUMENT_LIMIT','permission_ref':None}
    publish(s,snapshot=snapshot)
    with pytest.raises(Conflict,match='EDITOR_OBSERVATION_REQUIRED'):guard(s)
    with pytest.raises(Conflict,match='current clean IDE observation'):publish(s,operation='retire_partial',action='RETIRE',snapshot=snapshot)
    with pytest.raises(Conflict,match='current clean IDE observation'):publish(s,operation='retire_dirty',action='RETIRE',dirty=True)
    with pytest.raises(ValueError):EditorObservation.model_validate({**snapshot,'documents_complete':True})


def test_observer_capacity_preserves_receipts_blocks_writes_and_reserves_explicit_retirement(coding,monkeypatch):
    from packages.agent_sessions import editor_observers as module
    from packages.domain import editor_observer_scope as scope
    s=coding;body,first=publish(s)
    monkeypatch.setattr(module,'MAX_OBSERVATIONS',1);monkeypatch.setattr(scope,'MAX_OBSERVATIONS',1)
    with pytest.raises(Conflict,match='CAPACITY'):guard(s)
    with pytest.raises(Conflict,match='CAPACITY'):publish(s,operation='capacity_no_update',dirty=True)
    _,result=publish(s,operation='retirement_capacity',action='RETIRE')
    assert result['observer']['phase']=='RETIRED';guard(s)
    assert s['value'].editor_observers.operation(s['pid'],s['ident'],body.operation_id,client='paired_client')==first


def test_old_metadata_producer_identity_must_end_before_a_new_window_explicitly_retires_it(coding,monkeypatch):
    from packages.agent_sessions import editor_observers as module
    s=coding;publish(s);real=module.process_identity(os.getpid(),os.getppid());new={**real,'birth':str(float(real['birth'])+1)}
    # Labelled process observation UNIT boundary; no process is stopped/started.
    monkeypatch.setattr(module,'process_identity',lambda *args:new);monkeypatch.setattr(module,'process_ended',lambda identity:False)
    with pytest.raises(Conflict,match='EDITOR_ORIGINAL_PROCESS_UNCONFIRMED'):publish(s,operation='live_parent_no_takeover',action='RETIRE')
    monkeypatch.setattr(module,'process_ended',lambda identity:True)
    with pytest.raises(Conflict,match='EDITOR_ORIGINAL_PROCESS_UNCONFIRMED'):publish(s,operation='no_observe_takeover')
    _,retired=publish(s,operation='old_ended_explicit_retire',action='RETIRE')
    assert retired['observer']['phase']=='RETIRED'


def test_api_original_actor_receipt_and_readonly_prepare_preserve_workspace_bytes(coding):
    s=coding;app=app_for(s['value']);register_agent_collaboration_routes(app,service=s['value'])
    client=TestClient(app);base=f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}"
    target=address(s);prepared=client.post(base+'/editor-observers/prepare',json=target.model_dump())
    assert prepared.status_code==200 and prepared.json()['read_only'] is True
    body=EditorObserverCommand(**target.model_dump(),operation_id='api_observe',expected_revision=0,
        preparation_sha256=prepared.json()['preparation_sha256'],observation=observed(s,dirty=True))
    result=client.post(base+'/editor-observer-actions',json=body.model_dump());assert result.status_code==200
    read=client.get(base+'/editor-observer-actions/api_observe',params={'editor_client_id':'paired_client'})
    assert read.json()==result.json() and result.json()['permission_granted'] is False
    assert client.get(base+'/editor-observer-actions/api_observe',params={'editor_client_id':'other'}).status_code==409
    assert s['folder'].is_dir()


def test_dirty_observation_reaches_real_direct_edit_start_and_inner_send_reservation(coding,monkeypatch):
    import asyncio
    from packages.domain.local_workspaces import FileEditInput
    from packages.agent_sessions.models import SendMessage
    s=coding;service=s['value'];path=s['folder']/'a.py';before=path.read_bytes()
    selected=service.workspaces.read(s['pid'],s['workspace']['id'],'a.py')['source_ref']
    publish(s,dirty=True)
    # Explicit UNIT admission-feed boundary so this case continues to exercise
    # the independent synchronous guard after an attempted preflight.
    async def capture_boundary(*args,**kwargs):return None
    monkeypatch.setattr(service.editor_captures.broker,'admit',capture_boundary)
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):
        service.workspaces.edit(s['pid'],s['workspace']['id'],FileEditInput(operation_id='dirty_direct_no_write',
            path='a.py',expected_revision=selected['revision'],expected_sha256=selected['sha256'],content='MUST_NOT_WRITE'))
    async def no_start(*args,**kwargs):raise AssertionError('No native owner can start through the dirty guard')
    monkeypatch.setattr(service,'_start_impl',no_start)
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):asyncio.run(service._start(s['pid'],s['ident']))
    async def run():
        # Original transport ownership is an explicit UNIT boundary. Real send's
        # inner DB transaction must independently reject before native dispatch.
        context={'lock':asyncio.Lock()};monkeypatch.setattr(service,'_context',lambda *a,**k:(s['manifest'],context))
        async def no_http(*args,**kwargs):raise AssertionError('No POST allowed after dirty observation')
        monkeypatch.setattr(service,'_http',no_http)
        with pytest.raises(Conflict,match='DIRTY_EDITOR'):
            await service.send(s['pid'],s['ident'],SendMessage(operation_id='dirty_send_no_dispatch',session_ref=s['ref'],generation=1,raw_text='MUST_NOT_DISPATCH'))
    asyncio.run(run());assert path.read_bytes()==before


def test_dirty_guard_never_prevents_rejecting_the_exact_original_permission(coding,monkeypatch):
    import asyncio
    s=coding;service=s['value'];publish(s,dirty=True);native={'id':'per_dirty_reject','sessionID':'ses_root','permission':'edit','patterns':['a.py']}
    ref={'id':native['id'],'generation':1,'sha256':digest(native)}
    service._claim(s['pid'],s['ident'],'original_root','MESSAGE',{'UNIT_TRANSPORT_BOUNDARY':True})
    service._finish(s['pid'],s['ident'],'original_root','ACCEPTED',generation=1,original_session_id='ses_root')
    state=service._state(s['pid'],s['ident']);state['active_operation_id']='original_root';service._write(service._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    calls=[]
    async def run():
        context={'lock':asyncio.Lock(),'known_sessions':{'ses_root'},'session_id':'ses_root','generation':1}
        # Labelled ownership/HTTP UNIT boundaries; real reply validates its
        # original request/ref/parent, claims a receipt and emits only reject.
        def original_context(*args,**kwargs):
            if kwargs.get('intent')!='STOP':service._editor_assert(s['manifest'])
            return s['manifest'],context
        async def http(ctx,method,path,payload=None):
            calls.append((method,path,payload))
            if method=='GET' and path=='/permission':return [native]
            assert method=='POST' and path=='/permission/per_dirty_reject/reply' and payload=={'reply':'reject'}
            return True
        monkeypatch.setattr(service,'_context',original_context);monkeypatch.setattr(service,'_http',http)
        with pytest.raises(Conflict,match='DIRTY_EDITOR'):
            await service.reply(s['pid'],s['ident'],ref['id'],{'operation_id':'cannot_allow_dirty','session_ref':s['ref'],'generation':1,'request_ref':ref,'reply':'once'},kind='PERMISSION')
        result=await service.reply(s['pid'],s['ident'],ref['id'],{'operation_id':'can_reject_dirty','session_ref':s['ref'],'generation':1,'request_ref':ref,'reply':'reject'},kind='PERMISSION')
        assert result['status']=='RETURNED' and result['original_request_processed'] is True
    asyncio.run(run());assert [row[0] for row in calls]==['GET','POST']



def test_delayed_clean_snapshot_cannot_refresh_an_existing_dirty_guard(coding):
    s=coding;publish(s,dirty=True);snapshot=observed(s);snapshot['captured_at_epoch_ms']-=30000
    with pytest.raises(Conflict,match='delayed clean state'):publish(s,operation='late_clean_must_not_replace',snapshot=snapshot)
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):guard(s)



def test_metadata_only_retirement_does_not_require_a_shared_parent_to_exit(monkeypatch):
    from packages.agent_sessions import editor_observers as module
    identity={'pid':1234,'parent_pid':1233,'birth':'1700000000.0','parent_birth':'1699999900.0'}
    # Explicit UNIT OS observation. No real process is started or stopped.
    def absent_extension(pid):
        assert pid==identity['pid']
        raise module.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(module.psutil,'Process',absent_extension)
    assert module.process_ended(identity) is True



def test_current_observer_scope_is_readonly_metadata_and_cannot_authorize_a_new_generation(coding):
    s=coding;_,first=publish(s);value=s['value'];saved=value._state(s['pid'],s['ident']);saved['generation']=2
    value._write(value._path(s['pid'],s['ident'],'state.json'),saved,replace=True)
    current=value.editor_observers.inspect(s['pid'],first['observer']['id'],client='paired_client')
    assert current['current_scope']['generation']==2
    assert current['current_scope_is_execution_authorization'] is False
    assert current['workspace_writes']==current['model_calls']==current['native_posts']==0
