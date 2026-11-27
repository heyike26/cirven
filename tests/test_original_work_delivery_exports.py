"""AUTHORED_NOT_RUN: actual original delivery/SQL/files/HTTP; model/ACK UNIT.

All original fixture tool/observer work is deferred with the rest of debug.
Injected Win32 output counters do not qualify a native Save dialog or kernel.
"""
from copy import deepcopy
import hashlib
import sqlite3
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from packages.agent_sessions.work_delivery_exports import WorkDeliveryExports
from packages.adapters.delivery_export_files import DeliveryExportFiles
from packages.adapters.windows_workspace_handles import WindowsWorkspaceHandles
from packages.domain.store import Conflict,NotFound
from packages.domain.local_workspaces import digest
from apps.api.native_delivery_exports import register_native_delivery_export_routes
from test_original_work_deliveries import delivery,create,download
from test_original_work_test_cases import mapped
from test_command_observations_routes import routed
from test_opencode_command_observation import command_scene
from test_session_diffs import app_for
from test_windows_workspace_handles import OriginalHandleCounter


@pytest.fixture
def exported(delivery):
    s=delivery;receipt=create(s);s['package_receipt']=receipt
    s['export_request']={**s['input'],'operation_id':'native_export_1','root_sha256':s['root_sha256'],
        'delivery_operation_id':receipt['operation_id'],'delivery_request_sha256':receipt['request_sha256'],
        'manifest_sha256':receipt['manifest_sha256'],'artifact_sha256':receipt['artifact']['sha256'],
        'artifact_byte_length':receipt['artifact']['byte_length']}
    s['exports']=WorkDeliveryExports(s['service']);return s


def query(s,**changes):return {**s['input'],'operation_id':s['export_request']['operation_id'],
    'request_sha256':digest(s['export_request']),**changes}
def begin(s):return s['exports'].begin(s['pid'],s['ident'],s['export_request'],client_id='unit_paired_ide')
def save(s,path):return s['exports'].save(s['pid'],s['ident'],{**query(s),'destination':str(path)})
def inspect(s,**changes):return s['exports'].operation(s['pid'],s['ident'],{**query(s),'observe_output':True,**changes})


def test_begin_claims_original_export_once_without_reading_archive_or_creating_file(exported,monkeypatch):
    s=exported
    monkeypatch.setattr(s['exports'].deliveries,'download',lambda *a:(_ for _ in ()).throw(AssertionError('begin must be metadata only')))
    first=begin(s);second=begin(s)
    assert first['new_intent_claimed'] is True and first['native_dialog_allowed'] is True
    assert second['new_intent_claimed'] is False and second['native_dialog_allowed'] is False
    assert first['original_request']==s['export_request'] and first['current_call_target_writes']==0


def test_native_save_export_reuses_exact_package_and_keeps_original_deliveries_unchanged(exported,tmp_path):
    s=exported;before=download(s,s['package_receipt']);begin(s);target=tmp_path/'delivery.zip';r=save(s,target)
    assert r['status']=='SAVED' and target.read_bytes()==before
    assert r['events']['RESULT']['sha256']==hashlib.sha256(before).hexdigest()
    assert r['new_delivery_created'] is False and r['source_files_written']==r['model_calls']==r['commands_started']==0
    with s['service'].store.connect() as db:assert db.execute('SELECT count(*) FROM original_work_delivery_receipts').fetchone()[0]==1


def test_existing_selected_filename_is_preserved_and_original_not_written_is_durable(exported,tmp_path):
    s=exported;begin(s);target=tmp_path/'manual.zip';target.write_bytes(b'manual unique content');r=save(s,target)
    assert r['status']=='NOT_WRITTEN' and r['events']['RESULT']['effect']=='EXISTING_FILE_UNTOUCHED'
    assert target.read_bytes()==b'manual unique content' and r['current_call_target_writes']==0
    assert inspect(s)['status']=='NOT_WRITTEN'


def test_completed_original_save_is_not_written_again_or_redirected_to_another_name(exported,tmp_path):
    s=exported;begin(s);target=tmp_path/'first.zip';first=save(s,target);before=target.stat().st_mtime_ns
    assert save(s,target)['events']==first['events'] and target.stat().st_mtime_ns==before
    other=tmp_path/'different.zip'
    with pytest.raises(Conflict):save(s,other)
    assert not other.exists()


def test_original_export_request_checksum_conflict_never_creates_a_second_intent(exported):
    s=exported;begin(s);different={**s['export_request'],'artifact_sha256':'0'*64}
    with pytest.raises(Conflict):s['exports'].begin(s['pid'],s['ident'],different,client_id='unit_paired_ide')
    with pytest.raises(Conflict):inspect(s,request_sha256='0'*64)


def test_cancelled_native_dialog_has_no_destination_and_cannot_later_write(exported,tmp_path):
    s=exported;begin(s);cancel=s['exports'].cancel(s['pid'],s['ident'],{**query(s),'reason':'USER_CANCELLED'})
    assert cancel['status']=='CANCELLED' and 'DESTINATION' not in cancel['events']
    target=tmp_path/'late.zip';assert save(s,target)['status']=='CANCELLED' and not target.exists()


def test_creation_journal_failure_preserves_empty_output_and_never_retries_write(exported,tmp_path,monkeypatch):
    s=exported;begin(s);original=s['exports']._append
    def fail(pid,sid,op,kind,value):
        if kind=='CREATED':raise OSError('UNIT creation receipt failed')
        return original(pid,sid,op,kind,value)
    monkeypatch.setattr(s['exports'],'_append',fail);target=tmp_path/'unknown.zip';r=save(s,target)
    assert r['status']=='UNKNOWN' and target.exists() and target.read_bytes()==b''
    assert 'CREATED' not in r['events'];assert save(s,target)['status']=='UNKNOWN'
    assert inspect(s)['current_observation']['status']=='NO_ORIGINAL_CREATION_PROOF'


def test_receipt_failure_keeps_complete_output_but_readonly_inspect_does_not_forge_saved_result(exported,tmp_path,monkeypatch):
    s=exported;begin(s);original=s['exports']._append
    def fail(pid,sid,op,kind,value):
        if kind=='RESULT' and value['status']=='SAVED':raise OSError('UNIT result commit failed')
        return original(pid,sid,op,kind,value)
    monkeypatch.setattr(s['exports'],'_append',fail);target=tmp_path/'complete unknown.zip';r=save(s,target)
    assert r['status']=='UNKNOWN' and hashlib.sha256(target.read_bytes()).hexdigest()==s['export_request']['artifact_sha256']
    observed=inspect(s);assert observed['current_observation']['status']=='MATCHING_CAPTURED_OUTPUT'
    assert observed['status']=='UNKNOWN' and 'RECOVERY' not in observed['events']
    assert observed['current_call_target_writes']==0


def test_exact_windows_birth_proof_allows_explicit_metadata_recovery_and_retains_original_unknown(exported,monkeypatch):
    s=exported;api=OriginalHandleCounter();s['exports'].files=DeliveryExportFiles(native=WindowsWorkspaceHandles(api));begin(s)
    original=s['exports']._append
    def fail(pid,sid,op,kind,value):
        if kind=='RESULT' and value['status']=='SAVED':raise OSError('UNIT ACK lost')
        return original(pid,sid,op,kind,value)
    monkeypatch.setattr(s['exports'],'_append',fail);r=save(s,'C:/selected/out.zip');assert r['status']=='UNKNOWN'
    writes=len([c for c in api.calls if c[0]=='write']);recovered=s['exports'].reconcile(s['pid'],s['ident'],query(s))
    assert recovered['status']=='SAVED' and recovered['events']['RESULT']['status']=='UNKNOWN'
    assert recovered['events']['RECOVERY']['metadata_recovery_only'] is True
    assert len([c for c in api.calls if c[0]=='write'])==writes and recovered['current_call_target_writes']==0


def test_changed_file_identity_blocks_metadata_recovery_and_preserves_external_bytes(exported,monkeypatch):
    s=exported;api=OriginalHandleCounter();s['exports'].files=DeliveryExportFiles(native=WindowsWorkspaceHandles(api));begin(s)
    original=s['exports']._append
    def fail(pid,sid,op,kind,value):
        if kind=='RESULT' and value['status']=='SAVED':raise OSError('UNIT ACK lost')
        return original(pid,sid,op,kind,value)
    monkeypatch.setattr(s['exports'],'_append',fail);save(s,'C:/selected/out.zip')
    node=api.nodes['c:\\selected\\out.zip'];node['creation']+=1;before=bytes(node['raw'])
    with pytest.raises(Conflict):s['exports'].reconcile(s['pid'],s['ident'],query(s))
    assert node['raw']==before and not any(c[0]=='delete' for c in api.calls)


def test_new_generation_can_read_original_export_but_cannot_reuse_old_save_authority(exported,tmp_path):
    s=exported;begin(s);state=s['service']._state(s['pid'],s['ident']);state['generation']=2
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    v=inspect(s,generation=2);assert v['original_request']['generation']==1 and v['scope']['generation']==2
    target=tmp_path/'must not exist.zip'
    with pytest.raises(Conflict):s['exports'].save(s['pid'],s['ident'],{**query(s,generation=2),'destination':str(target)})
    assert not target.exists()


def test_archive_integrity_failure_before_creation_is_not_written_and_original_zip_is_not_rebuilt(exported,tmp_path,monkeypatch):
    s=exported;begin(s)
    def broken(*a):raise Conflict('UNIT private archive integrity mismatch')
    monkeypatch.setattr(s['exports'].deliveries,'download',broken);target=tmp_path/'must not exist.zip';r=save(s,target)
    assert r['status']=='NOT_WRITTEN' and r['events']['RESULT']['effect']=='NOT_INVOKED' and not target.exists()


def test_rejected_native_filename_preserves_the_exact_original_choice_without_target_creation(exported,tmp_path):
    s=exported;begin(s);target=tmp_path/'original rejected choice.txt';r=save(s,target)
    assert r['status']=='NOT_WRITTEN' and r['events']['CHOICE']['destination']==str(target)
    assert 'DESTINATION' not in r['events'] and not target.exists()


def test_interrupted_after_choice_never_reopens_or_resaves_and_can_cancel_metadata_only(exported,tmp_path,monkeypatch):
    s=exported;begin(s);target=tmp_path/'interrupted choice.zip'
    class OriginalProcessStopped(BaseException):pass
    def stop(*a):raise OriginalProcessStopped()
    monkeypatch.setattr(s['exports'].files,'capture',stop)
    with pytest.raises(OriginalProcessStopped):save(s,target)
    first=inspect(s);assert first['status']=='DESTINATION_CHOICE_RECORDED' and first['events']['CHOICE']['destination']==str(target)
    assert save(s,target)['events']==first['events']
    result=s['exports'].cancel(s['pid'],s['ident'],{**query(s),'reason':'REQUEST_ABORTED'})
    assert result['status']=='CANCELLED' and not target.exists()


def test_native_export_journal_and_events_are_immutable(exported,tmp_path):
    s=exported;begin(s);save(s,tmp_path/'saved.zip')
    for table in ['original_delivery_export_intents','original_delivery_export_events']:
        for sql in [f'DELETE FROM {table}',f'UPDATE {table} SET operation_id=operation_id']:
            with pytest.raises(sqlite3.IntegrityError),s['service'].store.connect() as db:db.execute(sql)


def test_readonly_export_history_discovers_original_keys_without_reading_any_target(exported,monkeypatch):
    s=exported;begin(s)
    def forbidden(*a):raise AssertionError('History cannot read output or copy archive bytes')
    monkeypatch.setattr(s['exports'].files,'observe',forbidden)
    monkeypatch.setattr(s['exports'].deliveries,'download',forbidden)
    v=s['exports'].listing(s['pid'],s['ident'],{**s['input'],'offset':0})
    assert v['entries'][0]['operation_id']=='native_export_1' and v['entries'][0]['request_sha256']==digest(s['export_request'])
    assert v['read_only'] is True and v['new_delivery_created'] is False


def test_http_native_export_routes_require_current_desktop_host_header(exported):
    s=exported;app=app_for(s['service'])
    class Authority:
        def check_host(self,token,*,require_native):
            assert require_native is False
            if token!='unit-main-only':raise HTTPException(status_code=403,detail='UNIT trusted Main required')
    register_native_delivery_export_routes(app,authority=Authority())
    path=f"/api/desktop-host/projects/{s['pid']}/agent-sessions/{s['ident']}/delivery-exports"
    with TestClient(app) as client:
        packet={'request':s['export_request'],'origin_client_id':'unit_paired_ide'}
        assert client.post(path+'/begin',json=packet).status_code==403
        first=client.post(path+'/begin',json=packet,headers={'X-EEAS-Desktop-Host':'unit-main-only'})
        assert first.status_code==200 and first.json()['new_intent_claimed'] is True
        read=client.post(path+'/operations/inspect',json=query(s),headers={'X-EEAS-Desktop-Host':'unit-main-only'})
        assert read.status_code==200 and read.json()['current_call_target_writes']==0
