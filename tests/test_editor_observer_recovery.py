"""AUTHORED_NOT_RUN: real Store/manifest/API; OS ending explicitly UNIT injected.

No CLI, IDE/Windows, native mutation or model qualification is performed here.
"""
import time

import pytest
from fastapi.testclient import TestClient

from apps.api.agent_collaboration import register_agent_collaboration_routes
from packages.agent_sessions.editor_observer_recovery import (EditorObserverRecovery,
    EditorObserverRecoveryAddress, EditorObserverRecoveryCommand)
from packages.domain.editor_observer_scope import decode_observer
from packages.domain.store import Conflict
from test_editor_observers import address, observed, publish, guard
from test_session_full_diffs import coding
from test_session_diffs import app_for


def recovery(scene, ident, operation='recover_exact', *, snapshot=None, consent=True):
    owner=EditorObserverRecovery(scene['value'].editor_observers)
    target=address(scene,client='new_pair',window='new_window').model_dump()
    target.pop('action');target['observer_id']=ident
    target=EditorObserverRecoveryAddress.model_validate(target)
    prepared=owner.prepare(scene['pid'],scene['ident'],target)
    body=EditorObserverRecoveryCommand(**target.model_dump(),operation_id=operation,
        expected_revision=prepared['revision'],preparation_sha256=prepared['preparation_sha256'],
        observation=snapshot or observed(scene),user_preserved_original=consent)
    return owner,target,prepared,body


def read(scene, ident):
    with scene['value'].store.connect() as db:
        return decode_observer(db.execute('SELECT * FROM agent_editor_observers WHERE id=?',(ident,)).fetchone())


def test_new_pair_same_process_explicit_recovery_retains_original_dirty_snapshot_and_receipt(coding):
    s=coding;_,old=publish(s,dirty=True);ident=old['observer']['id'];before=read(s,ident)
    owner,target,prepared,body=recovery(s,ident)
    assert prepared['original_process_state']=='SAME_PROCESS'
    result=owner.command(s['pid'],s['ident'],body)
    assert result['phase']=='RETIRED' and result['original_evidence_retained']
    after=read(s,ident)
    assert after['editor_client_id']==before['editor_client_id']
    assert after['observation']==before['observation'] and after['origin_scope']==before['origin_scope']
    assert s['value'].editor_observers.operation(s['pid'],s['ident'],'observe_1',client='paired_client')==old
    assert owner.command(s['pid'],s['ident'],body)==result
    assert owner.operation(s['pid'],s['ident'],body.operation_id,client='new_pair')==result
    assert owner.prepare(s['pid'],s['ident'],target)['phase']=='RETIRED'
    guard(s)


def test_different_original_process_must_end_before_new_pair_can_recover(coding,monkeypatch):
    from packages.agent_sessions import editor_observer_recovery as module
    s=coding;_,old=publish(s);ident=old['observer']['id'];original=read(s,ident)['process_identity']
    # Labelled process observation boundary; no process is stopped or started.
    monkeypatch.setattr(module,'process_identity',lambda *a:{**original,'birth':'1800000001.0'})
    monkeypatch.setattr(module,'process_ended',lambda _:False)
    with pytest.raises(Conflict,match='ORIGINAL_PROCESS_UNCONFIRMED'):recovery(s,ident)
    monkeypatch.setattr(module,'process_ended',lambda _:True)
    owner,_,prepared,body=recovery(s,ident)
    assert prepared['original_process_state']=='ENDED'
    assert owner.command(s['pid'],s['ident'],body)['phase']=='RETIRED'


def test_dirty_missing_consent_stale_capture_and_wrong_revision_keep_original_guard(coding):
    s=coding;_,old=publish(s,dirty=True);ident=old['observer']['id']
    owner,_,_,body=recovery(s,ident,snapshot=observed(s,dirty=True))
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):owner.command(s['pid'],s['ident'],body)
    for update in [{'user_preserved_original':False},{'expected_revision':body.expected_revision+1},
            {'observation':{**observed(s),'captured_at_epoch_ms':int(time.time()*1000)-4000}}]:
        clean=body.model_copy(update={'observation':body.observation.model_validate(observed(s))})
        changed=EditorObserverRecoveryCommand.model_validate({**clean.model_dump(),**update})
        with pytest.raises(Conflict):owner.command(s['pid'],s['ident'],changed)
    assert read(s,ident)['phase']=='ACTIVE'
    with pytest.raises(Conflict,match='DIRTY_EDITOR'):guard(s)


def test_recovery_cannot_borrow_capture_or_permission_and_rejects_changed_request_hash(coding):
    s=coding;_,old=publish(s);ident=old['observer']['id'];owner,_,_,body=recovery(s,ident)
    changed=body.model_dump();changed['observation']['capture_ref']='e'*64
    with pytest.raises(Conflict):owner.command(s['pid'],s['ident'],changed)
    result=owner.command(s['pid'],s['ident'],body)
    changed=body.model_copy(update={'editor_window_id':'another_window'})
    with pytest.raises(Conflict):owner.command(s['pid'],s['ident'],changed)
    with pytest.raises(Conflict):owner.operation(s['pid'],s['ident'],result['id'],client='another_pair')


def test_read_preparation_can_discover_same_session_generation_without_execution_authority(coding):
    s=coding;_,old=publish(s);ident=old['observer']['id'];owner,target,_,_=recovery(s,ident)
    state=s['value']._state(s['pid'],s['ident']);state['generation']=2
    s['value']._write(s['value']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    prepared=owner.prepare(s['pid'],s['ident'],target)
    assert prepared['scope']['generation']==2 and prepared['current_scope_is_execution_authorization'] is False
    # The stale generation still cannot be submitted as a command.
    body=EditorObserverRecoveryCommand(**target.model_dump(),operation_id='stale_recovery',
        expected_revision=prepared['revision'],preparation_sha256=prepared['preparation_sha256'],
        observation=observed(s),user_preserved_original=True)
    with pytest.raises(Conflict,match='generation'):owner.command(s['pid'],s['ident'],body)


def test_api_recovery_uses_its_original_actor_and_preserves_file_bytes(coding):
    s=coding;_,old=publish(s);owner,_,_,body=recovery(s,old['observer']['id'])
    original={p:p.read_bytes() for p in s['folder'].iterdir() if p.is_file()}
    app=app_for(s['value']);register_agent_collaboration_routes(app,service=s['value'])
    path=f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}/editor-observer-recoveries"
    with TestClient(app) as client:
        result=client.post(path,json=body.model_dump());assert result.status_code==200
        assert client.get(path+'/'+body.operation_id,params={'editor_client_id':'new_pair'}).json()==result.json()
    assert {p:p.read_bytes() for p in original}==original
