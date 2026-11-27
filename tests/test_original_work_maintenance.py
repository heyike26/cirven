"""AUTHORED_NOT_RUN: actual test-owned SQL/files, UNIT original native/SDK/ACK.

No real issue reproduction, application, Windows, IDE or release qualification.
The inherited observer is an explicit local UNIT hook when eventually executed.
"""
import sqlite3
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from packages.agent_sessions.work_maintenance import (WorkMaintenance,WorkMaintenanceInspect,
    WorkMaintenanceChange,MaintenanceDeliveryRef)
from packages.agent_sessions import work_maintenance as mod,work_requirements
from packages.domain.context_read_budget import context_read_scope,ContextReadLimit
from packages.domain.tasks import digest
from packages.domain.store import Conflict,NotFound
from test_original_work_deliveries import delivery,prepare as package_review,create,create_packet
from test_original_work_test_cases import mapped
from test_command_observations_routes import routed
from test_opencode_command_observation import command_scene
from test_session_diffs import app_for


@pytest.fixture
def maintenance(delivery):
    s=delivery;s['baseline_receipt']=create(s);service=s['service']
    original=service.operation(s['pid'],s['ident'],s['input']['root_operation_id'])
    r={**original['request'],'operation_id':'maintenance_work','raw_text':'原交付后出现故障，保留已运行版本与原失败证据。'}
    service._claim(s['pid'],s['ident'],'maintenance_work','MESSAGE',r)
    service._finish(s['pid'],s['ident'],'maintenance_work','UNKNOWN_OUTCOME',
        generation=s['input']['generation'],original_session_id=original['original_session_id'],
        native_message_id='msg_maintenance_unit',native_dispatch_acknowledged=True)
    s['maintenance_input']={**s['input'],'root_operation_id':'maintenance_work'}
    s['maintenance']=WorkMaintenance(service)
    root=work_requirements._root(service,s['pid'],s['ident'],WorkMaintenanceInspect.model_validate(s['maintenance_input']))
    s['maintenance_root_sha256']=root['sha256']
    s['baseline_ref']=s['maintenance'].browse(s['pid'],s['ident'],s['maintenance_input'])['entries'][0]['reference']
    return s


def view(s,**changes):return s['maintenance'].inspect(s['pid'],s['ident'],{**s['maintenance_input'],**changes})
def details(s,section='FILES',**changes):return s['maintenance'].delivery(s['pid'],s['ident'],{
    **s['maintenance_input'],'delivery_ref':s['baseline_ref'],'section':section,**changes})
def review(s,**changes):
    q={**s['maintenance_input'],'root_sha256':s['maintenance_root_sha256'],'expected_revision':view(s)['revision'],
        'action':'ASSOCIATE','delivery_ref':s['baseline_ref'],'note':'现场尚未复现；关联原版本与原失败证据。',**changes}
    return q,s['maintenance'].prepare(s['pid'],s['ident'],q)
def packet(s,op='maintenance_association_1',**changes):
    q,r=review(s,**changes);return {**q,'operation_id':op,'review_sha256':r['review_sha256'],'review_confirmed':True}
def change(s,p=None):return s['maintenance'].change(s['pid'],s['ident'],p or packet(s))
def lookup(s,p):return {**s['maintenance_input'],'operation_id':p['operation_id'],'request_sha256':digest(p)}


def test_same_project_baseline_keeps_delivery_and_maintenance_original_roots_distinct(maintenance):
    s=maintenance;v=details(s)
    assert v['root_sha256']==s['maintenance_root_sha256']!=v['summary']['reference']['root_sha256']
    assert v['entries'][0]['record']['source_ref']==s['release_source']
    assert v['summary']['artifact']==s['baseline_receipt']['artifact']
    assert v['model_calls']==v['commands_started']==v['artifact_files_written']==0


def test_case_and_report_evidence_keep_original_nonzero_failure_not_current_success(maintenance):
    s=maintenance;c=details(s,'CASES')['entries'][0];r=details(s,'REPORTS')['entries'][0]['record']
    assert c['command_exit_code']==r['command_exit_code']==7
    assert c['report_outcome']!='PASSED' and r['summary']['outcome']!='PASSED'
    assert c['selection']['source']['report_ref']==r['report_ref']
    assert details(s)['whole_task_acceptance']=='NOT_ASSESSED' and details(s)['issue_resolution']=='NOT_ASSESSED'


def test_inspection_does_not_read_workspace_or_zip_rebuild_or_invoke_native_owner(maintenance,monkeypatch):
    s=maintenance
    def forbidden(*args,**kwargs):raise AssertionError('Maintenance reads frozen SQL metadata only')
    for name in ['download','_archive','_saved']:monkeypatch.setattr(s['maintenance'].deliveries,name,forbidden)
    monkeypatch.setattr(s['service'].workspaces,'read_saved_snapshot',forbidden)
    assert details(s,'REQUIREMENTS')['entries']
    assert details(s,'NOTES')['entries'][0]['record']['text']==s['baseline_receipt']['notes']
    assert view(s)['revision']==0


def test_frozen_baseline_reader_has_no_source_session_owner_dependency(maintenance,monkeypatch):
    s=maintenance
    monkeypatch.setattr(s['service'],'_manifest',lambda *args:(_ for _ in ()).throw(AssertionError('No old owner read')))
    with s['service'].store.connect() as db:
        summary,_=s['maintenance']._baseline(db,s['pid'],s['baseline_ref'])
    assert summary['reference']==s['baseline_ref'] and summary['counts']['REPORTS']==1


def test_confirmed_association_only_appends_metadata_and_never_resolves_original_issue(maintenance):
    s=maintenance;p=packet(s);before=s['service'].operation(s['pid'],s['ident'],'maintenance_work')
    result=change(s,p);v=view(s)
    assert result['original_request']==p and result['receipt']['revision']==1
    assert result['current_call_metadata_writes']==1 and not result['read_only']
    assert v['active'][0]['baseline']['reference']==s['baseline_ref']
    assert s['service'].operation(s['pid'],s['ident'],'maintenance_work')==before
    with s['service'].store.connect() as db:
        assert db.execute('SELECT count(*) FROM original_work_delivery_receipts').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM original_work_maintenance_operations').fetchone()[0]==1


def test_duplicate_original_key_returns_same_receipt_and_conflicting_packet_is_rejected(maintenance):
    s=maintenance;p=packet(s);first=change(s,p)
    repeat=change(s,p)
    assert repeat['receipt']==first['receipt'] and view(s)['revision']==1
    assert repeat['current_call_metadata_writes']==0 and repeat['read_only']
    with pytest.raises(Conflict):change(s,{**p,'note':'different original request'})


def test_two_prepared_decisions_use_exact_revision_cas_and_keep_first_receipt(maintenance):
    s=maintenance;a=packet(s);b={**a,'operation_id':'other_association'};change(s,a)
    with pytest.raises(Conflict):change(s,b)
    assert view(s)['revision']==1 and len(view(s)['active'])==1


def test_removal_preserves_old_association_and_never_deletes_delivery_or_source(maintenance):
    s=maintenance;change(s);before=s['release_path'].read_bytes()
    p=packet(s,'remove_baseline',action='REMOVE',note='撤回此关联，原版本与失败仍保留。');change(s,p)
    v=view(s);assert v['revision']==2 and v['active']==[]
    assert [r['action'] for r in v['history']]==['ASSOCIATE','REMOVE']
    assert s['release_path'].read_bytes()==before and s['baseline_receipt']['warnings']


def test_removal_and_old_operation_remain_readable_if_original_delivery_is_now_unavailable(maintenance,monkeypatch):
    s=maintenance;p=packet(s);change(s,p)
    monkeypatch.setattr(s['maintenance'],'_baseline',lambda *args:(_ for _ in ()).throw(NotFound('UNIT unavailable')))
    assert s['maintenance'].operation(s['pid'],s['ident'],lookup(s,p))['receipt']['revision']==1
    change(s,packet(s,'remove_after_unavailable',action='REMOVE',note='保留已记录证据，撤回关联。'))
    assert view(s)['active']==[]


@pytest.mark.parametrize('field,value',[('request_sha256','0'*64),('manifest_sha256','0'*64),
    ('receipt_sha256','0'*64),('root_sha256','0'*64),('root_operation_id','other_root'),
    ('operation_id','other_delivery'),('revision',2)])
def test_wrong_frozen_reference_never_associates_another_delivery(maintenance,field,value):
    s=maintenance
    with pytest.raises((Conflict,NotFound)):details(s,delivery_ref={**s['baseline_ref'],field:value})
    assert view(s)['revision']==0


def test_original_query_at_later_generation_preserves_original_request_and_receipt(maintenance):
    s=maintenance;p=packet(s);first=change(s,p)
    state=s['service']._state(s['pid'],s['ident']);state['generation']=2
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    q={**lookup(s,p),'generation':2};v=s['maintenance'].operation(s['pid'],s['ident'],q)
    assert v['original_request']['generation']==1 and v['scope']['generation']==2
    assert v['receipt']==first['receipt'] and v['query_sha256']==digest(q)


def test_later_delivery_revision_cannot_replace_previous_selected_baseline(maintenance):
    s=maintenance;p=packet(s);change(s,p)
    s['delivery_query']={**s['delivery_query'],'expected_revision':1,'version_label':'0.2 later'}
    newer=create(s,create_packet(s,package_review(s),op='delivery_2'))
    assert newer['revision']==2 and details(s)['summary']['reference']['revision']==1
    assert view(s)['active'][0]['baseline']['reference']==s['baseline_ref']


def test_uncompleted_delivery_is_not_offered_as_a_qualified_baseline(maintenance,monkeypatch):
    s=maintenance;s['delivery_query']={**s['delivery_query'],'expected_revision':1,'version_label':'incomplete'}
    p=create_packet(s,package_review(s),op='incomplete_delivery')
    monkeypatch.setattr(s['deliveries'],'_archive',lambda *args:(_ for _ in ()).throw(OSError('UNIT archive interruption')))
    with pytest.raises(OSError):create(s,p)
    v=s['maintenance'].browse(s['pid'],s['ident'],s['maintenance_input'])
    assert len(v['entries'])==1 and v['entries'][0]['reference']['operation_id']!=p['operation_id']


def test_maintenance_ledger_rejects_update_and_delete_without_erasing_history(maintenance):
    s=maintenance;change(s)
    for sql in ['UPDATE original_work_maintenance_operations SET revision=revision','DELETE FROM original_work_maintenance_operations']:
        with s['service'].store.connect() as db:
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)
    assert view(s)['revision']==1


def test_shared_source_budget_is_charged_before_loading_frozen_manifest(maintenance):
    s=maintenance
    with context_read_scope() as budget:
        budget.byte_limit=16
        with s['service'].store.connect() as db:
            with pytest.raises(ContextReadLimit):s['maintenance']._baseline(db,s['pid'],s['baseline_ref'])
    assert view(s)['revision']==0


def test_immutable_row_sha_corruption_fails_closed_without_repair(maintenance):
    s=maintenance;p=packet(s);change(s,p)
    # Intentional corruption of this test-owned temporary DB only.
    with s['service'].store.connect() as db:
        db.execute('DROP TRIGGER original_work_maintenance_no_update')
        db.execute('UPDATE original_work_maintenance_operations SET receipt_sha256=?',('0'*64,))
    with pytest.raises(Conflict):s['maintenance'].operation(s['pid'],s['ident'],lookup(s,p))


def test_capacity_failure_does_not_append_a_decision_or_affect_delivery(maintenance,monkeypatch):
    s=maintenance;p=packet(s);monkeypatch.setattr(mod,'MAX_OPERATIONS',0)
    with pytest.raises(Conflict):change(s,p)
    assert view(s)['revision']==0 and details(s)['summary']['reference']==s['baseline_ref']


def test_exact_request_schema_rejects_auto_permission_target_path_and_false_confirmation(maintenance):
    s=maintenance;p=packet(s)
    for extra in [{'destination':'C:/forged.zip'},{'permission_granted':True},{'review_confirmed':False}]:
        with pytest.raises(ValidationError):WorkMaintenanceChange.model_validate({**p,**extra})
    with pytest.raises(ValidationError):MaintenanceDeliveryRef.model_validate({**s['baseline_ref'],'project_id':'foreign_project'})
    assert view(s)['revision']==0


def test_stale_work_scope_or_wrong_review_sha_cannot_write_metadata(maintenance):
    s=maintenance;p=packet(s)
    for changes in [{'generation':2},{'root_sha256':'0'*64},{'review_sha256':'0'*64}]:
        with pytest.raises(Conflict):change(s,{**p,**changes})
    assert view(s)['revision']==0


def test_http_routes_keep_original_scope_and_record_only_the_reviewed_metadata(maintenance):
    s=maintenance;client=TestClient(app_for(s['service']));base=f'/api/projects/{s["pid"]}/agent-sessions/{s["ident"]}/work-maintenance'
    assert client.post(base+'/deliveries',json=s['maintenance_input']).status_code==200
    p=packet(s);r=client.post(base+'/links',json=p);assert r.status_code==200 and r.json()['issue_resolution']=='NOT_ASSESSED'
    assert client.post(base+'/operations/inspect',json=lookup(s,p)).json()['original_request']==p


def test_catalog_retains_unavailable_entry_without_hiding_other_verified_versions(maintenance,monkeypatch):
    s=maintenance;s['delivery_query']={**s['delivery_query'],'expected_revision':1,'version_label':'later verified'}
    create(s,create_packet(s,package_review(s),op='delivery_2'))
    baseline=s['maintenance']._baseline
    def selected(db,pid,ref):
        if ref.operation_id==s['baseline_ref']['operation_id']:raise Conflict('UNIT unavailable original')
        return baseline(db,pid,ref)
    monkeypatch.setattr(s['maintenance'],'_baseline',selected)
    v=s['maintenance'].browse(s['pid'],s['ident'],s['maintenance_input'])
    assert v['unavailable']==1 and len(v['entries'])==2
    assert [r['eligible'] for r in v['entries']]==[False,True]
    assert v['entries'][0]['locator']['operation_id']==s['baseline_ref']['operation_id']


def test_baseline_from_a_different_project_is_not_readable_through_current_project(maintenance):
    s=maintenance
    with s['service'].store.connect() as db:
        with pytest.raises(NotFound):s['maintenance']._baseline(db,'foreign_project',s['baseline_ref'])
    assert view(s)['revision']==0
