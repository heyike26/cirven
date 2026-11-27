"""AUTHORED_NOT_RUN: real Store/files/ledger with SDK/editor boundaries injected.

The native editor is not executed. Explicit test writes emulate a user's save;
the handoff service itself never changes workspace bytes or invokes a model.
"""
import asyncio

import pytest

from packages.agent_sessions import AgentSessions
from packages.agent_sessions.editor_changes import EditorChangeRequest, EditorChangeControl, text_hash
from packages.agent_sessions.models import SendMessage
from packages.domain.editor_handoff_scope import overlaps, same_workspace
from packages.domain.local_workspaces import FileEditInput
from packages.domain.store import Conflict
from test_session_full_diffs import coding
from test_diff_reviews import capture


async def begin(scene,operation='editor_one'):
    selection=capture(scene);service=scene['value'];p=await service.editor_changes.reviews.prepare(scene['pid'],scene['ident'],selection)
    body=EditorChangeRequest(**selection.model_dump(),operation_id=operation,preparation_sha256=p['preparation_sha256'],
        direction='RESTORE_BEFORE',editor_client_id='paired_client',editor_window_id='window_one',editor_nonce='a'*64)
    return body,await service.editor_changes.reserve(scene['pid'],scene['ident'],body)


def control(scene,request,lease,action,*,operation='control_one',basis=None,**changes):
    body={'operation_id':operation,'session_ref':request.session_ref.model_dump(),'generation':request.generation,
        'target_operation_id':request.operation_id,'lease_ref':lease['lease_ref'],'editor_client_id':request.editor_client_id,
        'editor_window_id':request.editor_window_id,'editor_nonce':request.editor_nonce,'expected_revision':lease['revision'],
        'action':action,'editor_basis':basis,**changes}
    return EditorChangeControl.model_validate(body)


def test_exact_handoff_blocks_real_writer_and_original_start_until_saved_resolution(coding,monkeypatch):
    s=coding;service=s['value'];request,lease=asyncio.run(begin(s));path=s['folder']/'a.py';before=path.read_bytes()
    assert lease['phase']=='READY' and lease['first_reservation_reply'] is True and lease['grant']['mode']=='TEXT_REPLACE'
    assert 'target_text' not in lease['grant'] and 'source_text' not in lease['grant']
    with pytest.raises(Conflict,match='EDITOR_CHANGE_PENDING'):
        service.workspaces.edit(s['pid'],s['workspace']['id'],FileEditInput(operation_id='blocked_direct',path='a.py',
            expected_revision=lease['grant']['source_ref']['revision'],expected_sha256=lease['grant']['saved_basis']['sha256'],content='MUST_NOT_WRITE'))
    async def unexpected(*args,**kwargs):raise AssertionError('No native owner start while the real durable gate is active')
    monkeypatch.setattr(service,'_start_impl',unexpected)
    with pytest.raises(Conflict,match='EDITOR_CHANGE_PENDING'):asyncio.run(service._start(s['pid'],s['ident']))
    assert path.read_bytes()==before
    dirty={'document_version':10,'content_sha256':lease['grant']['source_editor_sha256'],'is_dirty':True}
    with pytest.raises(Conflict):service.editor_changes.control(s['pid'],s['ident'],control(s,request,lease,'ARM',basis=dirty))
    armed_request=control(s,request,lease,'ARM',operation='arm_real',basis={**dirty,'is_dirty':False})
    armed=service.editor_changes.control(s['pid'],s['ident'],armed_request)
    assert armed['first_control_reply'] is True
    assert service.editor_changes.control(s['pid'],s['ident'],armed_request)['first_control_reply'] is False
    lease=armed['editor_change'];assert lease['phase']=='ARMED'
    changed={'document_version':11,'content_sha256':lease['grant']['target_editor_sha256'],'is_dirty':True}
    lease=service.editor_changes.control(s['pid'],s['ident'],control(s,request,lease,'BUFFER_CHANGED',operation='buffer_observed',basis=changed))['editor_change']
    with pytest.raises(Conflict):service.editor_changes.assert_available(str(s['folder']))
    # UNIT editor-boundary save, not a backend write or native qualification.
    path.write_bytes(b'keep\nold\ntail\n')
    closed=service.editor_changes.control(s['pid'],s['ident'],control(s,request,lease,'RESOLVE_SAVED',operation='save_observed',basis={**changed,'is_dirty':False}))['editor_change']
    assert closed['phase']=='SAVED' and closed['workspace_writes']==0
    service.editor_changes.assert_available(str(s['folder']))


def test_root_send_inner_reservation_rechecks_the_durable_gate(coding,monkeypatch):
    s=coding;service=s['value'];asyncio.run(begin(s));calls=[]
    async def run():
        context={'lock':asyncio.Lock()}
        # Ownership is a UNIT boundary here; real send's DB-side writer gate is
        # intentionally exercised, rather than asserting an unconnected error.
        monkeypatch.setattr(service,'_context',lambda *a,**k:(s['manifest'],context))
        async def no_post(*a,**k):calls.append((a,k));raise AssertionError('No native dispatch after editor gate')
        monkeypatch.setattr(service,'_http',no_post)
        with pytest.raises(Conflict,match='EDITOR_CHANGE_PENDING'):
            await service.send(s['pid'],s['ident'],SendMessage(operation_id='blocked_send',session_ref=s['ref'],generation=1,raw_text='MUST_NOT_DISPATCH'))
    asyncio.run(run());assert calls==[]


def test_lost_reservation_reply_is_held_across_service_restart_without_auto_grant_or_file_write(coding,monkeypatch):
    s=coding;service=s['value'];selection=capture(s)
    async def run():
        p=await service.editor_changes.reviews.prepare(s['pid'],s['ident'],selection)
        body=EditorChangeRequest(**selection.model_dump(),operation_id='lost_grant',preparation_sha256=p['preparation_sha256'],direction='RESTORE_BEFORE',editor_client_id='paired_client',editor_window_id='window_one',editor_nonce='a'*64)
        original=service._finish;failed=False
        def finish(pid,sid,operation,status,**kwargs):
            nonlocal failed
            if status=='ACCEPTED' and not failed:failed=True;raise RuntimeError('UNIT reservation receipt lost after SQL READY')
            return original(pid,sid,operation,status,**kwargs)
        monkeypatch.setattr(service,'_finish',finish)
        with pytest.raises(RuntimeError):await service.editor_changes.reserve(s['pid'],s['ident'],body)
        restored=AgentSessions(service.store,service.data_dir,provider_registry=service.providers,workspaces=service.workspaces,capabilities=service.capabilities)
        lease=restored.editor_changes.inspect(s['pid'],s['ident'],body.operation_id,client='paired_client')
        assert lease['phase']=='UNKNOWN_OUTCOME' and lease['first_reservation_reply'] is False
        with pytest.raises(Conflict):restored.editor_changes.assert_available(str(s['folder']))
        assert (await restored.editor_changes.reserve(s['pid'],s['ident'],body))['first_reservation_reply'] is False
        with pytest.raises(Conflict):restored.editor_changes.inspect(s['pid'],s['ident'],body.operation_id,client='another_client')
        cancelled=restored.editor_changes.control(s['pid'],s['ident'],control(s,body,lease,'RESOLVE_CANCELLED',operation='cancel_unarmed'))
        assert cancelled['editor_change']['phase']=='CANCELLED'
        restored.editor_changes.assert_available(str(s['folder']))
    asyncio.run(run());assert (s['folder']/'a.py').read_bytes()==b'keep\nnew\ntail\n'


def test_actor_revision_manual_save_and_postpublication_control_recovery_are_exact(coding,monkeypatch):
    s=coding;service=s['value'];request,lease=asyncio.run(begin(s))
    with pytest.raises(Conflict):service.editor_changes.control(s['pid'],s['ident'],control(s,request,lease,'RESOLVE_CANCELLED',operation='wrong_actor',editor_nonce='b'*64))
    assert service.editor_changes.inspect(s['pid'],s['ident'],request.operation_id)['phase']=='READY'
    armed=service.editor_changes.control(s['pid'],s['ident'],control(s,request,lease,'ARM',operation='arm_once',basis={'document_version':1,'content_sha256':lease['grant']['source_editor_sha256'],'is_dirty':False}))['editor_change']
    path=s['folder']/'a.py';path.write_bytes(b'USER_CHOSEN_MANUAL_SAVE\n')
    with pytest.raises(Conflict):service.editor_changes.control(s['pid'],s['ident'],control(s,request,armed,'RESOLVE_SAVED',operation='not_target',basis={'document_version':3,'content_sha256':text_hash('USER_CHOSEN_MANUAL_SAVE\n'),'is_dirty':False}))
    original=service._finish;failed=False
    def finish(pid,sid,operation,status,**kwargs):
        nonlocal failed
        if status=='RETURNED' and not failed:failed=True;raise RuntimeError('UNIT control receipt lost after SQL manual resolution')
        return original(pid,sid,operation,status,**kwargs)
    monkeypatch.setattr(service,'_finish',finish)
    import hashlib
    body=control(s,request,armed,'RESOLVE_MANUAL_SAVED',operation='manual_resolution',basis={'document_version':3,'content_sha256':text_hash('USER_CHOSEN_MANUAL_SAVE\n'),'is_dirty':False},expected_saved_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),user_approved_manual_saved=True)
    with pytest.raises(RuntimeError):service.editor_changes.control(s['pid'],s['ident'],body)
    recovered=service.editor_changes.control_operation(s['pid'],s['ident'],'manual_resolution',client='paired_client')
    assert recovered['status']=='RETURNED' and recovered['first_control_reply'] is False
    assert recovered['editor_change']['phase']=='MANUAL_SAVED' and path.read_bytes()==b'USER_CHOSEN_MANUAL_SAVE\n'
    service.editor_changes.assert_available(str(s['folder']))


def test_overlap_uses_segments_case_and_physical_identity_not_name_prefix():
    assert overlaps('C:\\Work\\Project','c:\\work\\project\\sub')
    assert not overlaps('C:\\Work\\Project','C:\\Work\\Project2')
    assert not overlaps('/work/project','/work/project2')
    assert same_workspace('/mount/a',{'device':1,'inode':7},'/other/b',{'device':1,'inode':7})
    assert not same_workspace('/mount/a',{'device':1,'inode':7},'/other/b',{'device':2,'inode':7})


def test_explicit_cancel_of_an_ungranted_reservation_cannot_publish_a_late_grant(coding,monkeypatch):
    s=coding;service=s['value'];selection=capture(s)
    async def run():
        prepared=await service.editor_changes.reviews.prepare(s['pid'],s['ident'],selection)
        request=EditorChangeRequest(**selection.model_dump(),operation_id='cancel_before_grant',preparation_sha256=prepared['preparation_sha256'],direction='RESTORE_BEFORE',editor_client_id='paired_client',editor_window_id='window_one',editor_nonce='a'*64)
        entered=asyncio.Event();release=asyncio.Event()
        async def observe(*args,**kwargs):entered.set();await release.wait()
        monkeypatch.setattr(service.editor_changes,'_quiescent',observe)
        task=asyncio.create_task(service.editor_changes.reserve(s['pid'],s['ident'],request))
        await entered.wait();lease=service.editor_changes.inspect(s['pid'],s['ident'],request.operation_id,client='paired_client')
        assert lease['phase']=='RESERVING' and lease['lease_ref'] is None
        cancelled=service.editor_changes.control(s['pid'],s['ident'],control(s,request,lease,'RESOLVE_CANCELLED',operation='cancel_ungranted'))
        assert cancelled['editor_change']['phase']=='CANCELLED'
        release.set()
        with pytest.raises(Conflict):await task
        assert service.editor_changes.inspect(s['pid'],s['ident'],request.operation_id)['grant'] is None
        service.editor_changes.assert_available(str(s['folder']))
    asyncio.run(run());assert (s['folder']/'a.py').read_bytes()==b'keep\nnew\ntail\n'


def test_editor_directory_identity_keeps_exact_large_integers_without_changing_original_metadata():
    from packages.agent_sessions.editor_changes import editor_location
    value={'parent_ref':{'device':2**53+17,'inode':2**63+41},'name':'a.ts','path':'src/a.ts'}
    result=editor_location(value)
    assert result['parent_ref']=={'device':str(2**53+17),'inode':str(2**63+41)}
    assert type(value['parent_ref']['inode']) is int and value['parent_ref']['inode']==2**63+41
