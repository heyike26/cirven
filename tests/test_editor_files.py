"""AUTHORED_NOT_RUN: real Store/files/ledger with a labelled OS boundary.

The optional plugin captures actual immutable bytes. Only its original tool
and the Windows OS mutation boundary are controlled here; no Windows/native
IDE, model or general file-lifecycle qualification is claimed by this suite.
"""
import asyncio
from copy import deepcopy
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from apps.api.agent_collaboration import register_agent_collaboration_routes
from packages.agent_sessions.editor_changes import EditorChangeRequest, text_hash
from packages.agent_sessions.editor_files import EditorFileAddress, EditorFileCommand
from packages.agent_sessions.diff_reviews import DiffPreparation
from packages.adapters.windows_workspace_handles import file_metadata_sha256
from packages.domain.store import Conflict, InvalidInput, NotFound
from test_session_full_diffs import coding, Producer, saved
from test_session_diffs import app_for
from test_editor_changes import begin, control


async def added_handoff(scene, direction):
    producer = Producer(scene, tool='write', target='new.py')
    try: producer.finish()
    finally: producer.finish(False)
    service = scene['value']; query = saved(scene, [producer.message()])
    directory = service.diffs(scene['pid'], scene['ident'], **query)
    row = next(row for row in directory['files'] if row['path'] == 'new.py' and row['content_scope'] == 'FULL_FILE')
    selection = DiffPreparation(session_ref=scene['ref'], generation=1, workspace_ref=scene['workspace'],
        snapshot_ref=directory['snapshot_ref'], diff_ref=row['diff_ref'])
    if direction == 'APPLY_AFTER': (scene['folder']/'new.py').unlink()  # UNIT explicit user deletion before a fresh grant.
    prepared = await service.editor_changes.reviews.prepare(scene['pid'], scene['ident'], selection)
    request = EditorChangeRequest(**selection.model_dump(), operation_id='file_handoff',
        preparation_sha256=prepared['preparation_sha256'], direction=direction,
        editor_client_id='paired_client', editor_window_id='original_window', editor_nonce='a'*64)
    return request, await service.editor_changes.reserve(scene['pid'], scene['ident'], request)


def address(request, lease, action):
    return EditorFileAddress(session_ref=request.session_ref, generation=request.generation,
        target_operation_id=request.operation_id, lease_ref=lease['lease_ref'],
        editor_client_id=request.editor_client_id, editor_window_id=request.editor_window_id,
        editor_nonce=request.editor_nonce, expected_revision=lease['revision'], action=action)


def command(scene, request, lease, action, operation, *, documents=(), settled=True):
    target = address(request, lease, action)
    prepared = scene['value'].editor_files.prepare(scene['pid'], scene['ident'], target)
    return EditorFileCommand(**target.model_dump(), operation_id=operation,
        preparation_sha256=prepared['preparation_sha256'], user_approved=True,
        editor_observation={'documents': list(documents), 'native_commands_settled': settled})


class LabelledMutationBoundary:
    """UNIT OS stand-in only. Real Windows handle exclusion has separate tests."""
    def __init__(self, scene, request): self.scene=scene; self.request=request; self.calls=[]; self.fail_after=False; self.metadata={}
    def capture(self,filename):
        stat=filename.stat()
        material=self.metadata.get(str(filename)) or {'schema':'eeas.ntfs-file-metadata.v1','attributes':32,
            'creation':str(stat.st_ctime_ns),'access':str(stat.st_atime_ns),'write':str(stat.st_mtime_ns),'security_sddl':'UNIT_OWNER_GROUP_DACL_NOT_WINDOWS'}
        return {'metadata':deepcopy(material),'sha256':file_metadata_sha256(material),
            'identity':{'device':str(stat.st_dev),'inode':str(stat.st_ino)}}
    def check(self, binding, path, expected_parent):
        s=self.scene; service=s['value']
        lease=service.editor_changes.inspect(s['pid'],s['ident'],self.request.operation_id,client=self.request.editor_client_id)
        assert lease['phase']=='ARMED'  # Durable SQL intent precedes this OS boundary.
        info=(Path(binding['root'])/path).parent.stat()
        assert str(info.st_dev)==expected_parent['device'] and str(info.st_ino)==expected_parent['inode']
        original=service.operation(s['pid'],s['ident'],service.editor_files.inflight[(s['pid'],s['ident'],self.request.operation_id)])
        assert original['status']=='DISPATCHING'
    def compare_delete(self,binding,path,*,expected_raw,expected_parent,private_check,
            expected_metadata_sha256=None,expected_identity=None,persist_recovery=None):
        self.check(binding,path,expected_parent);filename=Path(binding['root'])/path
        assert filename.read_bytes()==expected_raw
        reference=persist_recovery(self.capture(filename));self.calls.append('delete');filename.unlink()
        if self.fail_after:raise RuntimeError('UNIT lost exclusive OS result after file effect')
        return {'kind':'UNIT_OS_BOUNDARY_NOT_WINDOWS_KERNEL','external_writers_and_delete_excluded':False,'metadata_recovery_ref':reference}
    def compare_write(self,binding,path,raw,*,expected_raw,expected_parent,private_check,restore_metadata=None,record_created=None):
        self.check(binding,path,expected_parent);assert expected_raw is None
        fd=os.open(Path(binding['root'])/path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:os.write(fd,raw);os.fsync(fd)
        finally:os.close(fd)
        self.calls.append('create')
        if restore_metadata is not None:self.metadata[str(Path(binding['root'])/path)]=deepcopy(restore_metadata)
        reference=record_created(self.capture(Path(binding['root'])/path))
        if self.fail_after:raise RuntimeError('UNIT lost exclusive OS result after file effect')
        return {'kind':'UNIT_OS_BOUNDARY_NOT_WINDOWS_KERNEL','created':True,'created_metadata_ref':reference,'metadata_restored':restore_metadata is not None}


@pytest.mark.parametrize('direction', ['RESTORE_BEFORE', 'APPLY_AFTER'])
def test_file_commit_and_exact_undo_hold_handoff_until_separate_editor_confirmation(coding, monkeypatch, direction):
    async def run():
        s=coding;service=s['value'];request,lease=await added_handoff(s,direction)
        path=s['folder']/'new.py';original=path.read_bytes() if path.exists() else None
        boundary=LabelledMutationBoundary(s,request);monkeypatch.setattr(service.editor_files,'_mutation_adapter',lambda:boundary)
        body=command(s,request,lease,'COMMIT','commit_original_file')
        result=await service.editor_files.command(s['pid'],s['ident'],body)
        assert result['file_result']['status']=='APPLIED' and result['file_result']['workspace_writes']==1
        assert path.exists()==(direction=='APPLY_AFTER')
        current=result['editor_change'];assert current['phase']=='BUFFER_CHANGED'
        with pytest.raises(Conflict):service.editor_changes.assert_available(str(s['folder']))
        undo=command(s,request,current,'UNDO','undo_original_file')
        restored=await service.editor_files.command(s['pid'],s['ident'],undo)
        assert restored['file_result']['status']=='UNDONE' and restored['editor_change']['phase']=='BUFFER_CHANGED'
        assert (path.read_bytes() if path.exists() else None)==original
        with pytest.raises(Conflict):service.editor_changes.assert_available(str(s['folder']))
        confirm=command(s,request,restored['editor_change'],'CONFIRM','confirm_original_undo')
        closed=await service.editor_files.command(s['pid'],s['ident'],confirm)
        assert closed['editor_change']['phase']=='CANCELLED' and closed['file_result']['workspace_writes']==0
        service.editor_changes.assert_available(str(s['folder']))
        assert len(boundary.calls)==2
        previous=await service.editor_files.command(s['pid'],s['ident'],body)
        assert previous==result and len(boundary.calls)==2
    asyncio.run(run())


def test_dirty_editor_and_stale_preparation_refuse_before_os_boundary(coding,monkeypatch):
    async def run():
        s=coding;service=s['value'];request,lease=await added_handoff(s,'RESTORE_BEFORE')
        boundary=LabelledMutationBoundary(s,request);monkeypatch.setattr(service.editor_files,'_mutation_adapter',lambda:boundary)
        dirty={'document_version':2,'content_sha256':text_hash('MANUAL_UNSAVED'),'is_dirty':True}
        body=command(s,request,lease,'COMMIT','dirty_refused',documents=[dirty])
        with pytest.raises(Conflict):await service.editor_files.command(s['pid'],s['ident'],body)
        fresh=command(s,request,lease,'COMMIT','stale_refused');path=s['folder']/'new.py';path.write_bytes(b'USER_LATER_SAVED\n')
        with pytest.raises(Conflict):await service.editor_files.command(s['pid'],s['ident'],fresh)
        assert boundary.calls==[] and path.read_bytes()==b'USER_LATER_SAVED\n'
    asyncio.run(run())


def test_unknown_os_result_is_not_inferred_or_retried_and_manual_resolution_keeps_original_unknown(coding,monkeypatch):
    async def run():
        s=coding;service=s['value'];request,lease=await added_handoff(s,'RESTORE_BEFORE')
        boundary=LabelledMutationBoundary(s,request);boundary.fail_after=True
        monkeypatch.setattr(service.editor_files,'_mutation_adapter',lambda:boundary)
        body=command(s,request,lease,'COMMIT','unknown_delete')
        with pytest.raises(RuntimeError):await service.editor_files.command(s['pid'],s['ident'],body)
        assert not (s['folder']/'new.py').exists()
        original=service.editor_files.operation(s['pid'],s['ident'],body.operation_id,client=request.editor_client_id)
        assert original['status']=='UNKNOWN_OUTCOME'
        assert await service.editor_files.command(s['pid'],s['ident'],body)==original and boundary.calls==['delete']
        current=service.editor_changes.inspect(s['pid'],s['ident'],request.operation_id,client=request.editor_client_id)
        with pytest.raises(Conflict):
            service.editor_changes.control(s['pid'],s['ident'],control(s,request,current,'RESOLVE_CANCELLED',operation='old_path_must_not_release'))
        unresolved=command(s,request,current,'MANUAL_RESOLVE','native_unsettled',settled=False)
        with pytest.raises(Conflict):await service.editor_files.command(s['pid'],s['ident'],unresolved)
        chosen=command(s,request,current,'MANUAL_RESOLVE','keep_explicit_file_state')
        closed=await service.editor_files.command(s['pid'],s['ident'],chosen)
        assert closed['editor_change']['phase']=='MANUAL_SAVED' and closed['file_result']['workspace_writes']==0
        assert service.operation(s['pid'],s['ident'],'unknown_delete')['status']=='UNKNOWN_OUTCOME'
        assert boundary.calls==['delete']
    asyncio.run(run())


def test_lost_action_receipt_recovers_its_exact_result_after_newer_confirmation_without_file_replay(coding,monkeypatch):
    async def run():
        s=coding;service=s['value'];request,lease=await added_handoff(s,'RESTORE_BEFORE')
        boundary=LabelledMutationBoundary(s,request);monkeypatch.setattr(service.editor_files,'_mutation_adapter',lambda:boundary)
        finish=service._finish;failed=False
        def lost(pid,sid,operation,status,**fields):
            nonlocal failed
            if operation=='lost_ack' and status=='RETURNED' and not failed:
                failed=True;raise RuntimeError('UNIT lost action receipt after durable result publication')
            return finish(pid,sid,operation,status,**fields)
        monkeypatch.setattr(service,'_finish',lost)
        body=command(s,request,lease,'COMMIT','lost_ack')
        with pytest.raises(RuntimeError):await service.editor_files.command(s['pid'],s['ident'],body)
        current=service.editor_changes.inspect(s['pid'],s['ident'],request.operation_id,client=request.editor_client_id)
        await service.editor_files.command(s['pid'],s['ident'],command(s,request,current,'CONFIRM','newer_confirm'))
        recovered=service.editor_files.operation(s['pid'],s['ident'],'lost_ack',client=request.editor_client_id)
        assert recovered['status']=='RETURNED' and recovered['file_result']['status']=='APPLIED'
        assert recovered['editor_change']['phase']=='BUFFER_CHANGED' and recovered['editor_change']['revision']==lease['revision']+2
        assert recovered['recovered_original_metadata'] is True and boundary.calls==['delete']
    asyncio.run(run())


def test_recovery_material_is_private_scoped_and_required_before_undo_recreation(coding,monkeypatch):
    async def run():
        s=coding;service=s['value'];request,lease=await added_handoff(s,'RESTORE_BEFORE')
        boundary=LabelledMutationBoundary(s,request);monkeypatch.setattr(service.editor_files,'_mutation_adapter',lambda:boundary)
        result=await service.editor_files.command(s['pid'],s['ident'],command(s,request,lease,'COMMIT','delete_with_metadata'))
        ref=result['file_result']['metadata_ref'];path=service._path(s['pid'],s['ident'],'editor-file-materials',ref['id']+'.json')
        private=service._read(path)
        assert private['purpose']=='DELETED_PREIMAGE' and private['metadata']['security_sddl']=='UNIT_OWNER_GROUP_DACL_NOT_WINDOWS'
        assert 'UNIT_OWNER_GROUP_DACL_NOT_WINDOWS' not in str(result)
        current=result['editor_change'];before_calls=list(boundary.calls)
        path.write_text('{}')  # UNIT corruption in this test's private fixture only.
        with pytest.raises(Conflict):command(s,request,current,'UNDO','must_not_guess_permissions')
        assert boundary.calls==before_calls and not (s['folder']/'new.py').exists()
    asyncio.run(run())


def test_failed_metadata_record_keeps_file_and_original_unknown_without_native_replay(coding,monkeypatch):
    async def run():
        s=coding;service=s['value'];request,lease=await added_handoff(s,'RESTORE_BEFORE')
        boundary=LabelledMutationBoundary(s,request);monkeypatch.setattr(service.editor_files,'_mutation_adapter',lambda:boundary)
        write=service._write
        def refused(path,value,**kwargs):
            if path.parent.name=='editor-file-materials':raise RuntimeError('UNIT metadata storage failure before deletion')
            return write(path,value,**kwargs)
        monkeypatch.setattr(service,'_write',refused);body=command(s,request,lease,'COMMIT','metadata_failed')
        original=(s['folder']/'new.py').read_bytes()
        with pytest.raises(RuntimeError):await service.editor_files.command(s['pid'],s['ident'],body)
        assert (s['folder']/'new.py').read_bytes()==original and boundary.calls==[]
        old=await service.editor_files.command(s['pid'],s['ident'],body)
        assert old['status']=='UNKNOWN_OUTCOME' and boundary.calls==[]
    asyncio.run(run())


def test_user_permission_change_after_creation_blocks_undo_before_deleting_current_file(coding,monkeypatch):
    async def run():
        s=coding;service=s['value'];request,lease=await added_handoff(s,'APPLY_AFTER')
        boundary=LabelledMutationBoundary(s,request);monkeypatch.setattr(service.editor_files,'_mutation_adapter',lambda:boundary)
        class MetadataProbe:
            def metadata_regular(self,binding,path,**options):return boundary.capture(Path(binding['root'])/path)
        monkeypatch.setattr(service.editor_files,'_metadata_adapter',lambda:MetadataProbe())
        result=await service.editor_files.command(s['pid'],s['ident'],command(s,request,lease,'COMMIT','created_endpoint'))
        filename=s['folder']/'new.py';original=filename.read_bytes()
        captured=boundary.capture(filename);boundary.metadata[str(filename)]={**captured['metadata'],'security_sddl':'UNIT_USER_MANUAL_DACL'}
        with pytest.raises(Conflict):command(s,request,result['editor_change'],'UNDO','must_keep_new_permissions')
        assert filename.read_bytes()==original and boundary.calls==['create']
    asyncio.run(run())


@pytest.mark.parametrize('replace_root',[False,True])
def test_missing_or_replaced_root_can_only_release_original_handoff_metadata_without_file_access(coding,replace_root):
    async def run():
        s=coding;service=s['value'];request,lease=await begin(s)
        old=s['folder'].with_name('ORIGINAL_RETAINED_ROOT');s['folder'].rename(old)
        if replace_root:
            s['folder'].mkdir();(s['folder']/'a.py').write_bytes(b'REPLACEMENT_ROOT_MUST_NOT_BE_TOUCHED')
        target=address(request,lease,'MANUAL_RESOLVE');p=service.editor_files.prepare(s['pid'],s['ident'],target)
        assert p['snapshot']['availability']=='UNAVAILABLE' and p['snapshot']['saved'] is None
        with pytest.raises((Conflict,InvalidInput,NotFound)):service.workspaces.get(s['pid'],s['workspace']['id'])
        body=EditorFileCommand(**target.model_dump(),operation_id='release_original_missing_root',
            preparation_sha256=p['preparation_sha256'],user_approved=True,
            editor_observation={'documents':[],'native_commands_settled':True})
        result=await service.editor_files.command(s['pid'],s['ident'],body)
        assert result['file_result']['workspace_writes']==0 and result['editor_change']['phase']=='MANUAL_SAVED'
        assert (old/'a.py').read_bytes()==b'keep\nnew\ntail\n'
        if replace_root:assert (s['folder']/'a.py').read_bytes()==b'REPLACEMENT_ROOT_MUST_NOT_BE_TOUCHED'
    asyncio.run(run())


def test_prepare_and_manual_file_resolution_asgi_keep_original_actor_and_request(coding):
    s=coding;service=s['value'];request,lease=asyncio.run(begin(s));app=app_for(service)
    register_agent_collaboration_routes(app,service=service)
    route=f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}"
    body=command(s,request,lease,'MANUAL_RESOLVE','manual_asgi')
    with TestClient(app) as client:
        prepared=client.post(route+'/editor-files/prepare',json=address(request,lease,'MANUAL_RESOLVE').model_dump())
        assert prepared.status_code==200
        result=client.post(route+'/editor-file-actions',json=body.model_dump());assert result.status_code==200,result.text
        original=client.get(route+'/editor-file-actions/manual_asgi',params={'editor_client_id':request.editor_client_id})
        assert original.status_code==200 and original.json()==result.json()
        foreign=client.get(route+'/editor-file-actions/manual_asgi',params={'editor_client_id':'other_client'})
        assert foreign.status_code==409
    assert (s['folder']/'a.py').read_bytes()==b'keep\nnew\ntail\n'
