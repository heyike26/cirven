"""AUTHORED_NOT_RUN: immutable original packet recovery, not a second archive.

Inherited model/native/ACK/observer fixtures are explicit UNIT boundaries.
"""
import pytest
from fastapi.testclient import TestClient
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_original_work_deliveries import delivery,prepare,create_packet,create,lookup
from test_original_work_test_cases import mapped
from test_command_observations_routes import routed
from test_opencode_command_observation import command_scene
from test_session_diffs import app_for


def incomplete(s,monkeypatch):
    p=create_packet(s,prepare(s))
    def fail(*args,**kwargs):raise OSError('UNIT archive interruption')
    monkeypatch.setattr(s['deliveries'],'_archive',fail)
    with pytest.raises(OSError):create(s,p)
    return p


def test_readonly_recovery_returns_the_exact_original_packet_and_frozen_selection(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch);q=lookup(s,p)
    v=s['deliveries'].recovery(s['pid'],s['ident'],q)
    assert v['original_request']==p and v['query_sha256']==digest(q)
    assert v['preview']['files'][0]['source_ref']==s['release_source']
    assert v['preview_sha256']==digest(v['preview']) and v['outcome']['kind']=='ORIGINAL_WORK_DELIVERY_PENDING'
    assert v['artifact_files_written']==v['source_files_written']==v['commands_started']==0


def test_recovery_never_reads_current_file_bytes_or_rebuilds_archive(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch);s['release_path'].unlink()
    def forbidden(*args,**kwargs):raise AssertionError('Recovery must not read or rebuild current source')
    monkeypatch.setattr(s['deliveries'],'_saved',forbidden)
    monkeypatch.setattr(s['deliveries'],'_archive',forbidden)
    assert s['deliveries'].recovery(s['pid'],s['ident'],lookup(s,p))['original_request']==p


def test_recovery_at_new_generation_preserves_old_original_request_generation(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch);state=s['service']._state(s['pid'],s['ident']);state['generation']=2
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    q={**lookup(s,p),'generation':2};v=s['deliveries'].recovery(s['pid'],s['ident'],q)
    assert v['original_request']['generation']==1 and v['query_sha256']==digest(q)


def test_completed_original_recovery_keeps_the_same_saved_receipt(delivery):
    s=delivery;p=create_packet(s,prepare(s));receipt=create(s,p)
    v=s['deliveries'].recovery(s['pid'],s['ident'],lookup(s,p))
    assert v['outcome']==receipt and v['original_request']==p and v['artifact_files_written']==0
    assert v['preview']['warnings']==receipt['warnings']


def test_wrong_original_request_sha_cannot_recover_another_packet(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch)
    with pytest.raises(Conflict):s['deliveries'].recovery(s['pid'],s['ident'],{**lookup(s,p),'request_sha256':'0'*64})


def test_original_unknown_retention_is_preserved_with_the_recovered_packet(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch);q=lookup(s,p)
    retained=s['deliveries'].retain(s['pid'],s['ident'],{**q,'statement':'Keep unique incomplete evidence'})
    v=s['deliveries'].recovery(s['pid'],s['ident'],q)
    assert v['outcome']['retention']==retained['retention'] and v['original_request']==p


def test_recovery_does_not_create_new_version_receipt_or_intent(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch);service=s['service']
    def counts():
        with service.store.connect() as c:return tuple(c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['original_work_delivery_intents','original_work_delivery_receipts','original_work_delivery_retentions'])
    before=counts();s['deliveries'].recovery(s['pid'],s['ident'],lookup(s,p))
    assert counts()==before==(1,0,0)


def test_missing_dispatch_ack_does_not_hide_pending_metadata_recovery(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch)
    s['service']._finish(s['pid'],s['ident'],s['input']['root_operation_id'],'UNKNOWN_OUTCOME',native_dispatch_acknowledged=False)
    assert s['deliveries'].recovery(s['pid'],s['ident'],lookup(s,p))['original_request']==p
    with pytest.raises(Conflict):s['deliveries'].continue_original(s['pid'],s['ident'],lookup(s,p))


def test_recovery_projects_no_report_body_or_full_private_manifest(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch);v=s['deliveries'].recovery(s['pid'],s['ident'],lookup(s,p))
    assert set(v['preview'])=={'version_label','notes','parent','files','file_bytes','warnings'}
    assert 'manifest' not in v and 'raw_bytes' not in v and v['read_only'] and v['metadata_only']


def test_http_recovery_route_is_a_read_of_the_same_original_intent(delivery,monkeypatch):
    s=delivery;p=incomplete(s,monkeypatch);client=TestClient(app_for(s['service']))
    base=f'/api/projects/{s["pid"]}/agent-sessions/{s["ident"]}/work-deliveries'
    response=client.post(base+'/operations/recovery',json=lookup(s,p))
    assert response.status_code==200 and response.json()['original_request']==p
