"""AUTHORED_NOT_RUN: real Store/cohorts/CAS and ASGI fixture edits.

Native editor observation is the labelled UNIT metadata producer. No running
Main/IDE, Windows, CLI, provider or model is exercised by these cases.
"""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from apps.api.local_workspaces import register_local_workspace_routes
from packages.agent_sessions.workspace_editor_captures import WorkspaceCaptureRequest
from packages.domain.local_workspaces import FileEditInput
from packages.domain.editor_capture_scope import capture_for_permission
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_session_full_diffs import coding
from test_session_diffs import app_for
from test_editor_observers import publish, observed
from test_editor_captures import respond, capture


def body(s,key='edit_original'):
    return FileEditInput(operation_id=key,path='a.py',expected_revision=1,
        expected_sha256='a'*64,content='EDIT_PAYLOAD_PRIVATE')


def request(s,operation='workspace_collect'):
    edit=body(s)
    return WorkspaceCaptureRequest(operation_id=operation,workspace_ref=s['workspace'],editor_client_id='workspace_actor',
        edit_operation_id=edit.operation_id,edit_request_sha256=digest(edit.model_dump(exclude={'operation_id'})))


def count(s):
    with s['value'].store.connect() as db:return db.execute('SELECT COUNT(*) FROM workspace_editor_capture_cohorts').fetchone()[0]


def test_workspace_purpose_has_exact_edit_digest_and_no_synthetic_session_or_execution_authority(coding):
    s=coding;publish(s,window='one');service=s['value'].editor_captures.workspace;r=request(s)
    receipt=service.request(s['pid'],s['workspace']['id'],r)
    assert receipt['kind']=='WORKSPACE_EDITOR_CAPTURE_COHORT'
    assert 'session_ref' not in receipt['scope'] and 'generation' not in receipt['scope']
    assert receipt['scope']['edit_operation_id']=='edit_original' and receipt['scope']['edit_request_sha256']==r.edit_request_sha256
    assert receipt['permission_granted'] is False and receipt['workspace_writes']==receipt['model_calls']==receipt['native_posts']==0
    with s['value'].store.connect() as db:
        row=db.execute('SELECT * FROM workspace_editor_capture_cohorts').fetchone()
        assert 'session_id' not in row.keys() and row['workspace_id']==s['workspace']['id']
        assert 'EDIT_PAYLOAD_PRIVATE' not in row['payload_json']
        assert db.execute('SELECT COUNT(*) FROM agent_editor_capture_cohorts').fetchone()[0]==0
    respond(s,receipt['capture_ref'])
    assert service.status(s['pid'],s['workspace']['id'],r.operation_id,client=r.editor_client_id)['state']=='READY'
    assert service.operation(s['pid'],s['workspace']['id'],r.operation_id,client=r.editor_client_id)==receipt
    assert not s['value'].active


def test_workspace_capture_cannot_borrow_pending_permission_and_changed_edit_hash_is_rejected(coding):
    s=coding;publish(s,window='one');service=s['value'].editor_captures.workspace;r=request(s)
    receipt=service.request(s['pid'],s['workspace']['id'],r)
    with pytest.raises(Conflict,match='IDEMPOTENCY_CONFLICT'):
        service.request(s['pid'],s['workspace']['id'],r.model_copy(update={'edit_request_sha256':'b'*64}))
    permission={'id':'per_original','generation':1,'sha256':'c'*64}
    state=s['value']._state(s['pid'],s['ident']);state['pending_permissions']=[{'request_ref':permission,'native_request':{'permission':'edit'}}]
    s['value']._write(s['value']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    with pytest.raises(Conflict,match='cannot borrow'):
        respond(s,receipt['capture_ref'],snapshot=observed(s,permission=permission))
    with s['value'].store.connect() as db:
        assert not capture_for_permission(db,receipt['capture_ref'],s['binding']['root'],s['binding']['root_identity'],permission)


def test_original_and_workspace_captures_share_one_retained_record_and_active_budget(coding,monkeypatch):
    from packages.agent_sessions import editor_captures as module
    s=coding;publish(s,window='one');monkeypatch.setattr(module,'MAX_COHORTS',1);capture(s)
    with pytest.raises(Conflict,match='CAPACITY'):
        s['value'].editor_captures.workspace.request(s['pid'],s['workspace']['id'],request(s))
    assert count(s)==0


def test_real_direct_edit_api_waits_for_metadata_then_uses_original_file_cas_once(coding):
    s=coding;owner=s['value'];initial=owner.workspaces.read(s['pid'],s['workspace']['id'],'a.py')['source_ref']
    edit=body(s).model_copy(update={'expected_revision':initial['revision'],'expected_sha256':initial['sha256']})
    publish(s,window='one',dirty=True);broker=owner.editor_captures.broker;before=(s['folder']/'a.py').read_bytes()
    app=app_for(owner);register_local_workspace_routes(app,owner.store,owner.data_dir)
    path=f"/api/projects/{s['pid']}/local-workspaces/{s['workspace']['id']}/edits"
    async def run():
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://unit') as client:
            task=asyncio.create_task(client.post(path,json=edit.model_dump()))
            frame=json.loads(await asyncio.wait_for(anext(stream),1))
            assert (s['folder']/'a.py').read_bytes()==before
            respond(s,frame['rows'][0]['capture_ref'])
            result=await asyncio.wait_for(task,2);assert result.status_code==200
            assert (s['folder']/'a.py').read_text()=='EDIT_PAYLOAD_PRIVATE'
            second=await client.post(path,json=edit.model_dump());assert second.status_code==200
            assert second.json()['replayed'] is True and count(s)==1
        await stream.aclose()
    asyncio.run(run())


def test_dirty_capture_stops_before_any_direct_edit_claim_or_file_mutation(coding):
    s=coding;owner=s['value'];publish(s,window='one',dirty=True);broker=owner.editor_captures.broker
    before=(s['folder']/'a.py').read_bytes()
    async def run():
        lease=broker.reserve();stream=lease.lines();await anext(stream)
        task=asyncio.create_task(broker.admit_workspace(s['pid'],s['workspace']['id'],body(s)))
        rows=json.loads(await asyncio.wait_for(anext(stream),1))['rows'];respond(s,rows[0]['capture_ref'],dirty=True)
        with pytest.raises(Conflict,match='CAPTURE_DIRTY'):await asyncio.wait_for(task,1)
        await stream.aclose()
    asyncio.run(run());assert (s['folder']/'a.py').read_bytes()==before
    assert not owner.workspaces._data(s['pid'],s['workspace']['id'],'operations',digest(body(s).operation_id)).exists()


def test_existing_unknown_edit_intent_is_preserved_without_a_new_capture_or_write_attempt(coding):
    s=coding;owner=s['value'];edit=body(s);directory=owner.workspaces._data(s['pid'],s['workspace']['id'],'operations',digest(edit.operation_id))
    owner.workspaces._write(directory/'request.json',{'request':edit.model_dump(exclude={'operation_id'}),'request_sha256':digest(edit.model_dump(exclude={'operation_id'}))})
    owner.workspaces._write(directory/'write-begun.json',{'UNIT_PRIOR_UNKNOWN':True})
    publish(s,window='one',dirty=True)
    assert asyncio.run(owner.editor_captures.broker.admit_workspace(s['pid'],s['workspace']['id'],edit)) is None
    assert count(s)==0 and (directory/'write-begun.json').exists()


def test_workspace_replacement_invalidates_current_purpose_without_reading_replacement_file_contents(coding):
    s=coding;publish(s,window='one');service=s['value'].editor_captures.workspace;r=request(s)
    receipt=service.request(s['pid'],s['workspace']['id'],r)
    old=s['folder'].with_name('retained_original');s['folder'].rename(old);s['folder'].mkdir();new=s['folder']/'a.py';new.write_text('REPLACEMENT_MUST_REMAIN')
    state=service.status(s['pid'],s['workspace']['id'],r.operation_id,client=r.editor_client_id)
    assert state['state']=='TARGET_CHANGED';assert new.read_text()=='REPLACEMENT_MUST_REMAIN'
    assert service.operation(s['pid'],s['workspace']['id'],r.operation_id,client=r.editor_client_id)==receipt
