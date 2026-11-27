"""AUTHORED_NOT_RUN: real Store, immutable versions, original observer and ZIP.

The inherited observer fixture runs a bounded local Node hook when eventually
executed; native owner/model/Windows and whole-task acceptance are not qualified.
No product method here builds, deploys, extracts or restores an application.
"""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import sqlite3
from zipfile import ZipFile
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from packages.agent_sessions.work_deliveries import (WorkDeliveries,WorkDeliveryPrepare,WorkDeliveryCreate,
    WorkDeliveryLookup,archive_name,MAX_PAYLOAD)
from packages.domain.local_workspaces import FileSource
from packages.domain.store import Conflict,InvalidInput
from packages.domain.tasks import digest
from test_original_work_test_cases import mapped,packet,save
from test_command_observations_routes import routed
from test_opencode_command_observation import command_scene
from test_session_diffs import app_for


@pytest.fixture
def delivery(mapped):
    s=mapped;s['mapping']=save(s,packet(s))
    # Explicit synthetic ACK; preallocated native IDs alone are insufficient.
    s['service']._finish(s['pid'],s['ident'],s['input']['root_operation_id'],
        'ACCEPTED',native_dispatch_acknowledged=True)
    path=s['folder']/'release.py';path.write_text('print("original delivered code")\n')
    ws=s['service'].workspaces
    selected=ws.read_saved_snapshot(s['pid'],s['workspace']['id'],'release.py')['source_ref']
    s['delivery_query']={**s['input'],'root_sha256':s['root_sha256'],'expected_revision':0,
        'version_label':'0.1 local','notes':'明确选择已保存文件与原失败证据；未声明目标验收通过。',
        'files':[selected],'include_evidence':True}
    s['deliveries']=WorkDeliveries(s['service']);s['release_path']=path;s['release_source']=selected
    return s


def prepare(s,**changes):return s['deliveries'].prepare(s['pid'],s['ident'],{**s['delivery_query'],**changes})
def create_packet(s,review,op='delivery_1',**changes):
    return {**s['delivery_query'],'operation_id':op,'review_sha256':review['review_sha256'],
        'review_confirmed':True,'known_gaps_acknowledged':True,**changes}
def create(s,body=None):
    return s['deliveries'].create(s['pid'],s['ident'],body or create_packet(s,prepare(s)))
def lookup(s,body):return {**s['input'],'operation_id':body['operation_id'],'request_sha256':digest(body)}
def download(s,r):return s['deliveries'].download(s['pid'],s['ident'],s['input']['root_operation_id'],r['operation_id'],r['request_sha256'])[0]
def archive_path(s,r):return s['deliveries']._path(s['pid'],s['ident'],r['operation_id'],*r['artifact']['path'].split('/'))


def test_prepare_reads_only_explicit_saved_files_and_does_not_claim_or_create_an_archive(delivery):
    s=delivery;before=s['release_path'].read_bytes();v=prepare(s)
    assert v['read_only'] is True and v['artifact_files_written']==v['source_files_written']==v['model_calls']==0
    assert v['files'][0]['source_ref']==s['release_source'] and v['files'][0]['archive_path']=='files/release.py'
    assert v['review_sha256']==digest(v['basis']) and s['release_path'].read_bytes()==before
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]==0
    assert not s['service']._path(s['pid'],s['ident'],'work-deliveries').exists()


def test_complete_bundle_contains_exact_saved_bytes_manifest_and_original_report(delivery):
    s=delivery;before=s['release_path'].read_bytes();r=create(s);raw=download(s,r)
    assert hashlib.sha256(raw).hexdigest()==r['artifact']['sha256']
    with ZipFile(BytesIO(raw)) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert digest(manifest)==r['manifest_sha256']
        assert z.read('files/release.py')==before
        assert all(z.getinfo(n).date_time==(1980,1,1,0,0,0) for n in z.namelist())
        for report in manifest['evidence']['reports'].values():
            assert report['status']=='SAVED_REPORT'
            assert hashlib.sha256(z.read(report['archive_path'])).hexdigest()==report['raw_sha256']
    assert s['release_path'].read_bytes()==before


def test_failed_nonzero_original_evidence_is_preserved_without_task_or_target_acceptance(delivery):
    s=delivery;r=create(s)
    with ZipFile(BytesIO(download(s,r))) as z:m=json.loads(z.read('manifest.json'))
    reports=list(m['evidence']['reports'].values())
    assert reports and reports[0]['command_exit_code']==7
    assert reports[0]['summary']['outcome']!='PASSED'
    assert 'REPORT_OR_COMMAND_NEEDS_REVIEW' in m['warnings']
    for v in [r,m]:
        assert v['whole_task_acceptance']=='NOT_ASSESSED' and v['target_environment_verified'] is False
        assert v['build_performed'] is v['deployment_performed'] is v['restore_performed'] is False


def test_review_and_known_gap_consent_are_required_before_durable_intent(delivery):
    s=delivery;v=prepare(s);p=create_packet(s,v)
    with pytest.raises(ValidationError):WorkDeliveryCreate.model_validate({**p,'review_confirmed':False})
    with pytest.raises(InvalidInput):create(s,{**p,'known_gaps_acknowledged':False})
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]==0


def test_selected_file_changed_after_review_rejects_before_intent_without_overwriting_it(delivery):
    s=delivery;p=create_packet(s,prepare(s));s['release_path'].write_text('USER_MANUAL_NEW\n')
    with pytest.raises(Conflict):create(s,p)
    assert s['release_path'].read_text()=='USER_MANUAL_NEW\n'
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]==0


def test_same_original_request_after_later_file_changes_returns_only_original_bundle(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));r=create(s,p);original=download(s,r)
    s['release_path'].write_text('Later manual content\n')
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(AssertionError('No package replay')))
    assert create(s,p)==r and download(s,r)==original


def test_same_original_operation_with_different_notes_cannot_replace_its_package(delivery):
    s=delivery;p=create_packet(s,prepare(s));r=create(s,p)
    with pytest.raises(Conflict):create(s,{**p,'notes':'A different original request'})
    assert s['deliveries'].operation(s['pid'],s['ident'],lookup(s,p))==r


def test_successive_delivery_versions_keep_an_exact_immutable_parent_chain(delivery):
    s=delivery;first=create(s);q={**s['delivery_query'],'expected_revision':1,'version_label':'0.2 local'}
    v=s['deliveries'].prepare(s['pid'],s['ident'],q)
    second=create(s,{**q,'operation_id':'delivery_2','review_sha256':v['review_sha256'],
        'review_confirmed':True,'known_gaps_acknowledged':True})
    assert second['revision']==2 and second['parent']=={k:first[k] for k in ['operation_id','request_sha256','manifest_sha256','revision']}
    assert download(s,first)!=download(s,second)


def test_stale_expected_version_cannot_create_another_intent(delivery):
    s=delivery;p=create_packet(s,prepare(s));create(s,p)
    with pytest.raises(Conflict):create(s,{**p,'operation_id':'other_operation'})
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]==1


def test_archive_failure_leaves_durable_original_intent_and_explicit_same_key_continuation(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));method=s['deliveries']._archive
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(OSError('UNIT interrupted archive')))
    with pytest.raises(OSError):create(s,p)
    value=s['deliveries'].operation(s['pid'],s['ident'],lookup(s,p))
    assert value['status']=='INCOMPLETE_REVIEW_REQUIRED' and value['automatic_retry'] is False
    assert create(s,p)['kind']=='ORIGINAL_WORK_DELIVERY_PENDING'
    monkeypatch.setattr(s['deliveries'],'_archive',method)
    r=s['deliveries'].continue_original(s['pid'],s['ident'],lookup(s,p))
    assert r['artifact']['path']=='attempts/02/package.zip' and r['request_sha256']==digest(p)
    assert s['deliveries']._path(s['pid'],s['ident'],p['operation_id'],'attempts','01','request.json').is_file()


def test_partial_archive_is_retained_and_does_not_get_extracted_or_overwritten(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));method=s['deliveries']._archive
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(OSError('UNIT interrupted')))
    with pytest.raises(OSError):create(s,p)
    partial=s['deliveries']._path(s['pid'],s['ident'],p['operation_id'],'attempts','01','package.zip');partial.write_bytes(b'PK-partial-unique-evidence')
    monkeypatch.setattr(s['deliveries'],'_archive',method)
    r=s['deliveries'].continue_original(s['pid'],s['ident'],lookup(s,p))
    assert partial.read_bytes()==b'PK-partial-unique-evidence' and r['artifact']['path']=='attempts/02/package.zip'


def test_complete_archive_before_receipt_commit_is_recovered_without_rebuilding(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));method=s['deliveries']._head;count=0
    def before_commit(*args):
        nonlocal count
        count+=1
        if count==3:raise OSError('UNIT crash before receipt commit')
        return method(*args)
    monkeypatch.setattr(s['deliveries'],'_head',before_commit)
    with pytest.raises(OSError):create(s,p)
    monkeypatch.setattr(s['deliveries'],'_head',method)
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(AssertionError('Already complete')))
    r=s['deliveries'].continue_original(s['pid'],s['ident'],lookup(s,p))
    assert r['artifact']['path']=='attempts/01/package.zip' and download(s,r)


def test_retained_unknown_remains_listed_and_does_not_claim_cancellation_or_rollback(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));method=s['deliveries']._archive
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(OSError('UNIT failed')))
    with pytest.raises(OSError):create(s,p)
    retained=s['deliveries'].retain(s['pid'],s['ident'],{**lookup(s,p),'statement':'保留原结果，再准备另一份交付'})
    assert retained['retention']['status']=='UNKNOWN_RETAINED_NOT_CANCELLED_OR_ROLLED_BACK'
    listing=s['deliveries'].listing(s['pid'],s['ident'],{**s['input'],'offset':0})
    assert listing['pending']==[] and listing['retained'][0]['operation_id']==p['operation_id']
    with pytest.raises(Conflict):s['deliveries'].continue_original(s['pid'],s['ident'],lookup(s,p))
    monkeypatch.setattr(s['deliveries'],'_archive',method)
    r=create(s,create_packet(s,prepare(s),'after_retention'))
    assert r['revision']==1 and r['operation_id']!=p['operation_id']


def test_retention_statement_cannot_silently_change_for_the_same_original_outcome(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(OSError('UNIT failed')))
    with pytest.raises(OSError):create(s,p)
    q={**lookup(s,p),'statement':'First explicit retained outcome'}
    a=s['deliveries'].retain(s['pid'],s['ident'],q)
    assert s['deliveries'].retain(s['pid'],s['ident'],q)==a
    with pytest.raises(Conflict):s['deliveries'].retain(s['pid'],s['ident'],{**q,'statement':'Other statement'})


def test_immutable_intent_receipt_and_retention_sql_rows_reject_update_and_delete(delivery,monkeypatch):
    s=delivery;r=create(s)
    for table in ['original_work_delivery_intents','original_work_delivery_receipts']:
        for action in [f'UPDATE {table} SET operation_id=operation_id',f'DELETE FROM {table}']:
            with s['service'].store.connect() as c:
                with pytest.raises(sqlite3.IntegrityError):c.execute(action)
    definition=s['service'].store
    with definition.connect() as c:assert c.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'original_delivery_retention_no_%'").fetchone()[0]==2


def test_archive_download_detects_tampering_without_touching_source_or_original_receipt(delivery):
    s=delivery;r=create(s);before=s['release_path'].read_bytes();archive_path(s,r).write_bytes(b'TAMPERED')
    with pytest.raises(Conflict):download(s,r)
    assert s['release_path'].read_bytes()==before
    assert s['deliveries'].operation(s['pid'],s['ident'],{**s['input'],'operation_id':r['operation_id'],'request_sha256':r['request_sha256']})==r


def test_historical_download_does_not_need_current_source_files_or_an_editor(delivery,monkeypatch):
    s=delivery;r=create(s);raw=download(s,r);s['release_path'].unlink()
    monkeypatch.setattr(s['deliveries'].ws,'_saved_raw',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('No live read')))
    assert download(s,r)==raw and r['atomic_workspace_snapshot'] is False


def test_archive_names_reject_traversal_reserved_windows_names_and_case_collisions(delivery):
    s=delivery
    for name in ['../user','/absolute','a\\b','x:stream','CON.txt','dir/LPT1','x.','x ','COM¹']:
        with pytest.raises((InvalidInput,ValueError)):archive_name(name)
    first={**s['release_source'],'path':'same.py'};second={**first,'path':'SAME.py'}
    with pytest.raises(ValidationError):WorkDeliveryPrepare.model_validate({**s['delivery_query'],'files':[first,second]})


def test_a_delivery_cannot_read_files_from_another_project_or_binding(delivery):
    s=delivery;q=deepcopy(s['delivery_query']);q['files'][0]['project_id']='foreign'
    with pytest.raises(InvalidInput):s['deliveries'].prepare(s['pid'],s['ident'],q)
    q['files'][0]['project_id']=s['pid'];q['files'][0]['binding_revision']+=1
    with pytest.raises(ValidationError):WorkDeliveryPrepare.model_validate(q)


def test_evidence_not_included_is_explicit_and_never_becomes_test_success(delivery):
    s=delivery;q={**s['delivery_query'],'include_evidence':False};v=s['deliveries'].prepare(s['pid'],s['ident'],q)
    r=create(s,{**q,'operation_id':'files_only','review_sha256':v['review_sha256'],'review_confirmed':True,'known_gaps_acknowledged':True})
    with ZipFile(BytesIO(download(s,r))) as z:m=json.loads(z.read('manifest.json'))
    assert 'EVIDENCE_CONTENT_NOT_INCLUDED' in m['warnings'] and m['evidence']['reports']=={}
    assert all(r['selected_snapshot'] is None for r in m['evidence']['requirements'])
    assert m['whole_task_acceptance']=='NOT_ASSESSED'


def test_file_count_and_aggregate_byte_limits_do_not_start_a_package(delivery):
    s=delivery
    files=[{**s['release_source'],'path':'file_'+str(i)} for i in range(65)]
    with pytest.raises(ValidationError):WorkDeliveryPrepare.model_validate({**s['delivery_query'],'files':files})
    selected=[]
    for name in ['large_a.bin','large_b.bin']:
        (s['folder']/name).write_bytes(b'x'*(MAX_PAYLOAD//2+1))
        selected.append(s['service'].workspaces.read_saved_snapshot(s['pid'],s['workspace']['id'],name)['source_ref'])
    with pytest.raises(Conflict):prepare(s,files=selected)
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]==0


def test_http_prepare_create_inspect_and_download_use_the_same_original_receipt(delivery):
    s=delivery;client=TestClient(app_for(s['service']));base=f'/api/projects/{s["pid"]}/agent-sessions/{s["ident"]}/work-deliveries'
    review=client.post(base+'/prepare',json=s['delivery_query']);assert review.status_code==200
    p=create_packet(s,review.json());result=client.post(base+'/packages',json=p);assert result.status_code==201
    r=result.json();assert client.post(base+'/operations/inspect',json=lookup(s,p)).json()==r
    binary=client.get(r['download_url']);assert binary.status_code==200 and binary.headers['content-type']=='application/zip'
    assert hashlib.sha256(binary.content).hexdigest()==r['artifact']['sha256']


def test_download_cannot_switch_root_or_original_request_hash(delivery):
    s=delivery;r=create(s)
    with pytest.raises(Conflict):s['deliveries'].download(s['pid'],s['ident'],'other_root',r['operation_id'],r['request_sha256'])
    with pytest.raises(Conflict):s['deliveries'].download(s['pid'],s['ident'],s['input']['root_operation_id'],r['operation_id'],'0'*64)


def test_complete_orphan_archive_can_finish_in_a_new_generation_without_new_source_reads(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));head=s['deliveries']._head;calls=0
    def crash(*args):
        nonlocal calls
        calls+=1
        if calls==3:raise OSError('UNIT before receipt')
        return head(*args)
    monkeypatch.setattr(s['deliveries'],'_head',crash)
    with pytest.raises(OSError):create(s,p)
    monkeypatch.setattr(s['deliveries'],'_head',head)
    state=s['service']._state(s['pid'],s['ident']);state['generation']=2
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(AssertionError('No source rebuild')))
    r=s['deliveries'].continue_original(s['pid'],s['ident'],{**lookup(s,p),'generation':2})
    assert r['request_sha256']==digest(p) and r['artifact']['path']=='attempts/01/package.zip'


def test_frozen_report_and_source_bytes_can_package_after_owner_change_and_source_deletion(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));method=s['deliveries']._archive
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(OSError('UNIT before package')))
    with pytest.raises(OSError):create(s,p)
    state=s['service']._state(s['pid'],s['ident']);state['generation']=2
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    s['release_path'].unlink()
    monkeypatch.setattr(s['deliveries'],'_archive',method)
    monkeypatch.setattr(s['service'],'command_observations',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('No new owner report read')))
    r=s['deliveries'].continue_original(s['pid'],s['ident'],{**lookup(s,p),'generation':2})
    with ZipFile(BytesIO(download(s,r))) as z:
        assert z.read('files/release.py')==b'print("original delivered code")\n'
        assert any(name.startswith('evidence/reports/') for name in z.namelist())
    assert r['whole_task_acceptance']=='NOT_ASSESSED'


def test_report_bytes_are_content_addressed_once_across_delivery_versions(delivery):
    s=delivery;create(s);q={**s['delivery_query'],'expected_revision':1,'version_label':'next'}
    v=s['deliveries'].prepare(s['pid'],s['ident'],q)
    create(s,{**q,'operation_id':'next_with_same_report','review_sha256':v['review_sha256'],
        'review_confirmed':True,'known_gaps_acknowledged':True})
    with s['service'].store.connect() as c:
        assert c.execute('SELECT count(*) FROM original_work_delivery_report_blobs').fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError):c.execute('DELETE FROM original_work_delivery_report_blobs')


def test_explicit_saved_revision_packaging_does_not_enumerate_its_entire_version_history(delivery,monkeypatch):
    s=delivery;binding=s['service'].workspaces.binding_record(s['pid'],s['workspace']['id'])
    version_dir=s['service'].workspaces._versions(binding,'release.py');path_type=type(version_dir);original=path_type.iterdir
    def bounded(path):
        if path==version_dir:raise AssertionError('Do not scan unrelated file versions for an exact reference')
        return original(path)
    monkeypatch.setattr(path_type,'iterdir',bounded)
    r=create(s);assert download(s,r)


def test_shared_private_delivery_capacity_is_checked_before_any_new_intent_or_archive(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s))
    def full(*args):raise Conflict('UNIT reserved delivery budget reached')
    monkeypatch.setattr(s['deliveries'],'_capacity',full)
    with pytest.raises(Conflict):create(s,p)
    with s['service'].store.connect() as c:
        assert c.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM original_work_delivery_report_blobs').fetchone()[0]==0


def test_continuation_uses_frozen_readme_even_if_exporter_copy_changes_after_intent(delivery,monkeypatch):
    from packages.agent_sessions import work_deliveries as module
    s=delivery;p=create_packet(s,prepare(s));method=s['deliveries']._archive;original_readme=module.README
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(OSError('UNIT before ZIP')))
    with pytest.raises(OSError):create(s,p)
    monkeypatch.setattr(module,'README',b'NEW_FORMATTER_COPY_NOT_PART_OF_THE_ORIGINAL_REVIEW')
    monkeypatch.setattr(s['deliveries'],'_archive',method)
    r=s['deliveries'].continue_original(s['pid'],s['ident'],lookup(s,p))
    with ZipFile(BytesIO(download(s,r))) as z:assert z.read('READ_ME.txt')==original_readme
