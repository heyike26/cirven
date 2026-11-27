"""AUTHORED_NOT_RUN: actual Store/files/HTTP and original observer fixture.

The SDK tool/acknowledgement and test execution are synthetic UNIT boundaries.
Saved JUnit assertions and human mappings never qualify native/target coverage.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from packages.agent_sessions.work_test_cases import (WorkCaseInspect,WorkCaseMap,WorkCaseOperation,
    WorkCaseSource,WorkCaseSelection,inspect_work_test_cases,map_work_test_case,
    inspect_work_case_operation,mapping_key)
from packages.agent_sessions import work_requirements as requirements
from packages.agent_sessions.test_reports import _load_command_report,CommandTestReportInput
from packages.domain.store import Conflict,NotFound,InvalidInput
from packages.domain.tasks import canonical,digest
from test_command_observations_routes import routed,persist
from test_opencode_command_observation import command_scene,exercise
from test_command_test_reports import request as report_request
from test_original_work_requirements import bundle,ref,revise
from test_session_diffs import app_for

XML='<testsuite name="shipping" tests="3"><testcase name="same" classname="cost"/><testcase name="same" classname="cost"><failure message="rounding">expected 700</failure></testcase><testcase name="deferred"><skipped/></testcase></testsuite>'


@pytest.fixture
def mapped(routed):
    s=routed;packet=exercise(s,changeDuring=XML,exit=7)
    state,path,expected=persist(s,packet)
    operation=s['service']._read(path);operation['request']['operation_id']=operation['id']
    operation['request_sha256']=digest(operation['request'])
    s['service']._write(path,operation,replace=True)
    s['input']={'session_ref':s['ref'],'generation':1,'workspace_ref':s['workspace_ref'],'root_operation_id':operation['id']}
    b=bundle(s);root=requirements.inspect_work_requirements(s['service'],s['pid'],s['ident'],s['input'])
    p={**s['input'],'operation_id':'requirements_link','root_sha256':root['root_sha256'],
        'expected_revision':0,'action':'ASSOCIATE','requirement_ref':ref(b),
        'requirement_ids':['R1','R2'],'acceptance_ids':['A1','A2']}
    s['link']=requirements.link_work_requirements(s['service'],s['pid'],s['ident'],p)
    s['bundle']=b;s['root_sha256']=root['root_sha256'];s['root_path']=path
    entry=s['service'].command_observations(s['pid'],s['ident'],**expected)['commands'][0]
    s['report_input']=report_request(s,entry)
    s['report']=s['service'].command_test_report(s['pid'],s['ident'],s['report_input'])
    return s


def source(s,ordinal=0):
    report=s['report'];case=report['cases'][ordinal]
    return {key:s['report_input'][key] for key in ['command_ref','command_generation','call_id','report_path','report_sha256']}|{
        'report_ref':report['report_ref'],'case_ordinal':case['ordinal'],'case_sha256':digest(case)}


def inspect(s,**updates):return inspect_work_test_cases(s['service'],s['pid'],s['ident'],{**s['input'],**updates})
def packet(s,operation='mapping_one',ordinal=0,**updates):
    current=inspect(s)
    return {**s['input'],'operation_id':operation,'root_sha256':s['root_sha256'],
        'expected_revision':current['revision'],'expected_requirement_revision':current['requirement_revision'],
        'action':'MAP','mapping_key':mapping_key(s['root_sha256'],s['bundle']['id'],source(s,ordinal)),
        'selection':{'requirement_link_operation_id':s['link']['operation_id'],
            'requirement_link_request_sha256':s['link']['request_sha256'],'source':source(s,ordinal),'acceptance_ids':['A1']},**updates}

def save(s,p):return map_work_test_case(s['service'],s['pid'],s['ident'],p)
def lookup(s,receipt):return inspect_work_case_operation(s['service'],s['pid'],s['ident'],{
    **s['input'],'operation_id':receipt['operation_id'],'request_sha256':receipt['request_sha256']})


def test_mapping_binds_exact_case_and_selected_acceptance_without_changing_original_execution(mapped):
    s=mapped;before=s['root_path'].read_bytes();p=packet(s);r=save(s,p)
    assert r['request_sha256']==digest(WorkCaseMap.model_validate(p).model_dump())
    assert r['proof']['requirement_ids']==['R1'] and r['proof']['acceptance_ids']==['A1']
    assert r['proof']['case_label']['status']=='PASSED' and r['proof']['command_exit_code']==7
    assert r['coverage']=='HUMAN_DECLARED_MAPPING_NOT_MEASURED_TEST_COVERAGE'
    assert r['whole_task_acceptance']=='NOT_ASSESSED' and r['permission_granted'] is False
    assert r['commands_started']==r['model_calls']==r['source_files_written']==0 and s['root_path'].read_bytes()==before


def test_same_case_names_are_distinguished_by_immutable_report_ordinal_and_case_sha(mapped):
    s=mapped;a=save(s,packet(s,'case_zero',0));b=save(s,packet(s,'case_one',1))
    assert a['proof']['case_label']['name']==b['proof']['case_label']['name']=='same'
    assert a['mapping_key']!=b['mapping_key'] and a['proof']['case_ref']!=b['proof']['case_ref']
    assert b['proof']['case_label']['status']=='FAILED' and len(inspect(s)['rows'])==2


def test_mapping_records_failed_and_skipped_report_cases_without_promoting_them_to_pass(mapped):
    s=mapped
    for ordinal,status in [(1,'FAILED'),(2,'SKIPPED')]:
        r=save(s,packet(s,'case_'+str(ordinal),ordinal));assert r['proof']['case_label']['status']==status
        assert r['whole_task_acceptance']=='NOT_ASSESSED'
    assert all(row['new_execution_proven'] is False for row in inspect(s)['rows'])


def test_inspection_keeps_original_records_and_checks_current_case_separately(mapped):
    s=mapped;r=save(s,packet(s));view=inspect(s,case_source=source(s))
    assert view['rows'][0]['operation']==r and view['rows'][0]['report_status']=='VERIFIED_SAVED_CASE'
    assert view['rows'][0]['requirement_status']=='CURRENT'
    assert view['selected_case']['case_ref']==r['proof']['case_ref']
    assert view['selected_requirement_link']['acceptance'][0]['eligible']
    assert view['source_budget']['report_sources_reserved']==1


def test_later_manual_workspace_changes_are_never_read_or_overwritten_by_mapping(mapped,monkeypatch):
    s=mapped;path=s['folder']/'a.py';path.write_text('LATER_MANUAL_CONTENT_KEEP\n')
    from pathlib import Path
    original=Path.read_bytes
    def protected(current):
        assert current!=path,'Mapping must read only immutable after snapshot bytes'
        return original(current)
    monkeypatch.setattr(Path,'read_bytes',protected)
    async def forbidden(*_args,**_kwargs):pytest.fail('No SDK/model dispatch is permitted')
    monkeypatch.setattr(s['service'],'_http',forbidden);monkeypatch.setattr(s['service'],'send',forbidden)
    save(s,packet(s));inspect(s);assert path.read_text()=='LATER_MANUAL_CONTENT_KEEP\n'


def test_idempotent_original_key_does_not_need_report_files_again_or_move_current_head(mapped,monkeypatch):
    s=mapped;p=packet(s);r=save(s,p);later=save(s,packet(s,'later',1))
    import packages.agent_sessions.work_test_cases as cases
    monkeypatch.setattr(cases,'_load_command_report',lambda *_args:pytest.fail('Original receipt replay must not reread a report'))
    assert save(s,p)==r and lookup(s,r)==r
    # Read only raw head metadata here; current report inspection is intentionally disabled.
    with s['service'].store.connect() as c:assert c.execute('SELECT revision FROM original_work_case_heads').fetchone()[0]==later['revision']


def test_original_mapping_key_cannot_be_reused_with_different_selected_acceptance(mapped):
    s=mapped;p=packet(s);save(s,p);changed=deepcopy(p);changed['selection']['acceptance_ids']=['A2']
    with pytest.raises(Conflict):save(s,changed)
    assert inspect(s)['revision']==1


def test_concurrent_metadata_writers_only_one_wins_the_reviewed_mapping_head(mapped):
    s=mapped;a=packet(s,'one',0);b=packet(s,'two',1)
    def attempt(p):
        try:return save(s,p)['operation_id']
        except Conflict:return 'CONFLICT'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[a,b]))
    assert results.count('CONFLICT')==1 and inspect(s)['revision']==1


def test_requirement_head_change_between_review_and_save_blocks_mapping_without_partial_write(mapped):
    s=mapped;p=packet(s);b=s['bundle'];current=requirements.inspect_work_requirements(s['service'],s['pid'],s['ident'],s['input'])
    requirements.link_work_requirements(s['service'],s['pid'],s['ident'],{**s['input'],'operation_id':'change_link',
        'root_sha256':s['root_sha256'],'expected_revision':current['revision'],'action':'ASSOCIATE','requirement_ref':ref(b),
        'requirement_ids':['R1'],'acceptance_ids':['A1']})
    with pytest.raises(Conflict):save(s,p)
    assert inspect(s)['revision']==0


def test_acceptance_items_outside_original_link_and_forged_link_digest_are_rejected(mapped):
    s=mapped
    for edit in [lambda p:p['selection'].update(acceptance_ids=['MISSING']),
                 lambda p:p['selection'].update(requirement_link_request_sha256='0'*64)]:
        p=packet(s);edit(p)
        with pytest.raises(Conflict):save(s,p)
    assert inspect(s)['rows']==[]


def test_wrong_case_sha_report_ref_or_command_ref_cannot_borrow_observed_results(mapped):
    s=mapped
    for edit in [lambda v:v.update(case_sha256='0'*64),lambda v:v['report_ref'].update(sha256='0'*64),
                 lambda v:v['command_ref'].update(sha256='0'*64),lambda v:v.update(case_ordinal=1999)]:
        p=packet(s);edit(p['selection']['source'])
        with pytest.raises(Conflict):save(s,p)
    assert inspect(s)['revision']==0


def test_report_from_another_original_root_is_not_a_case_for_this_work(mapped,monkeypatch):
    s=mapped
    import packages.agent_sessions.work_test_cases as cases
    original=cases._load_command_report
    def wrong(*args):
        loaded=deepcopy(original(*args));loaded['basis']['root_operation_id']='foreign_root';return loaded
    monkeypatch.setattr(cases,'_load_command_report',wrong)
    with pytest.raises(Conflict):save(s,packet(s))
    assert inspect(s)['revision']==0


def test_only_mapped_acceptance_parent_changes_require_revalidation(mapped):
    s=mapped;r=save(s,packet(s));revise(s,s['bundle'],lambda p:p['requirements'][1].update(text='Unrelated caller update'))
    row=inspect(s)['rows'][0]
    assert row['requirement_status']=='SELECTED_UNCHANGED_NEWER_BUNDLE' and row['operation']==r
    assert row['requirement_impact']['changed_requirement_ids']==[] and row['automatic_adoption'] is False


def test_changed_mapped_requirement_keeps_old_mapping_but_blocks_new_mapping(mapped):
    s=mapped;old=save(s,packet(s));revise(s,s['bundle'],lambda p:p['requirements'][0].update(text='Use a different rounding rule'))
    row=inspect(s)['rows'][0];assert row['requirement_status']=='NEEDS_REVIEW' and row['operation']==old
    with pytest.raises(Conflict):save(s,packet(s,'new_case',1))


def test_removed_or_replaced_requirement_link_does_not_delete_its_old_case_proof(mapped):
    s=mapped;old=save(s,packet(s));current=requirements.inspect_work_requirements(s['service'],s['pid'],s['ident'],s['input'])
    requirements.link_work_requirements(s['service'],s['pid'],s['ident'],{**s['input'],'operation_id':'unlink',
        'root_sha256':s['root_sha256'],'expected_revision':current['revision'],'action':'REMOVE',
        'requirement_ref':ref(s['bundle']),'requirement_ids':[],'acceptance_ids':[]})
    row=inspect(s)['rows'][0];assert row['requirement_status']=='REQUIREMENT_LINK_CHANGED'
    assert row['operation']==old and lookup(s,old)==old


def test_unavailable_saved_report_keeps_mapping_and_unknown_source_state(mapped,monkeypatch):
    s=mapped;old=save(s,packet(s));import packages.agent_sessions.work_test_cases as cases
    monkeypatch.setattr(cases,'_load_command_report',lambda *_args:(_ for _ in ()).throw(NotFound('UNIT missing snapshot')))
    row=inspect(s)['rows'][0];assert row['report_status']=='UNAVAILABLE' and row['operation']==old
    assert lookup(s,old)==old


def test_remove_only_changes_active_metadata_and_preserves_original_operation_and_sources(mapped,monkeypatch):
    s=mapped;old=save(s,packet(s));p=packet(s,'remove',action='REMOVE',selection=None,mapping_key=old['mapping_key'])
    import packages.agent_sessions.work_test_cases as cases
    monkeypatch.setattr(cases,'_load_command_report',lambda *_args:pytest.fail('Removal must not parse or delete original report'))
    removed=save(s,p);assert removed['proof'] is None and removed['revision']==2
    assert inspect(s)['rows']==[] and lookup(s,old)==old
    assert save(s,{**p,'operation_id':'remove'})==removed


def test_original_mapping_receipts_can_be_read_after_observation_generation_advances(mapped):
    s=mapped;old=save(s,packet(s));state=s['service']._state(s['pid'],s['ident']);state['generation']=2
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    value=inspect_work_case_operation(s['service'],s['pid'],s['ident'],{**s['input'],'generation':2,
        'operation_id':old['operation_id'],'request_sha256':old['request_sha256']})
    assert value==old


def test_root_or_generation_change_during_report_read_rolls_back_new_mapping(mapped,monkeypatch):
    s=mapped;p=packet(s);import packages.agent_sessions.work_test_cases as cases
    original=cases._load_command_report
    def advance(*args):
        result=original(*args);state=s['service']._state(s['pid'],s['ident']);state['generation']=2
        s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True);return result
    monkeypatch.setattr(cases,'_load_command_report',advance)
    with pytest.raises(Conflict):save(s,p)
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_case_operations').fetchone()[0]==0


def test_sql_operation_and_head_are_one_transaction_and_operations_are_immutable(mapped):
    s=mapped;p=packet(s)
    with s['service'].store.connect() as c:c.execute("CREATE TRIGGER unit_fail_case_head BEFORE INSERT ON original_work_case_heads BEGIN SELECT RAISE(ABORT,'UNIT head failure'); END")
    with pytest.raises(sqlite3.DatabaseError):save(s,p)
    with s['service'].store.connect() as c:
        assert c.execute('SELECT count(*) FROM original_work_case_operations').fetchone()[0]==0
        c.execute('DROP TRIGGER unit_fail_case_head')
    saved=save(s,p)
    for sql in ['DELETE FROM original_work_case_operations','UPDATE original_work_case_operations SET revision=100']:
        with s['service'].store.connect() as c:
            with pytest.raises(sqlite3.DatabaseError):c.execute(sql)
    assert lookup(s,saved)==saved


def test_metadata_capacity_refuses_new_records_without_pruning_old_evidence(mapped,monkeypatch):
    s=mapped;old=save(s,packet(s));import packages.agent_sessions.work_test_cases as cases
    monkeypatch.setattr(cases,'MAX_OPERATIONS',1)
    with pytest.raises(Conflict):save(s,packet(s,'overflow',1))
    assert lookup(s,old)==old and len(inspect(s)['rows'])==1


def test_two_cases_from_one_report_share_one_snapshot_read_and_parser_result_per_inspection(mapped,monkeypatch):
    s=mapped;save(s,packet(s,'zero',0));save(s,packet(s,'one',1))
    import packages.agent_sessions.work_test_cases as cases
    original=cases._load_command_report;reads=[]
    def counted(*args):reads.append(args[3].report_path);return original(*args)
    monkeypatch.setattr(cases,'_load_command_report',counted)
    view=inspect(s,case_source=source(s,2));assert len(reads)==1 and view['source_budget']['report_sources_reserved']==1
    assert all(row['report_status']=='VERIFIED_SAVED_CASE' for row in view['rows'])


def test_report_read_budget_distinguishes_unchecked_records_from_unavailable_sources(mapped,monkeypatch):
    s=mapped;save(s,packet(s));import packages.agent_sessions.work_test_cases as cases
    monkeypatch.setattr(cases,'MAX_REPORTS',0)
    row=inspect(s)['rows'][0];assert row['report_status']=='NOT_CHECKED_BUDGET'
    assert row['operation']['proof']['case_label']['status']=='PASSED'


def test_requirement_version_read_cache_reserves_failures_and_has_no_global_lifetime(mapped):
    s=mapped;store=s['service'].store;cache={}
    with store.connect() as c:
        original=requirements._bundle(c,s['pid'],ref(s['bundle']),cache=cache)
        assert requirements._bundle(c,s['pid'],ref(s['bundle']),cache=cache) is original
        bad={'id':'absent','revision':1,'sha256':'0'*64}
        with pytest.raises(NotFound):requirements._bundle(c,s['pid'],bad,cache=cache)
        with pytest.raises(Conflict):requirements._bundle(c,s['pid'],bad,cache=cache)
        assert len(cache)==2
    assert len(inspect(s)['requirement_links'])==1


def test_same_case_replacement_keeps_original_receipt_and_requires_new_head_cas(mapped):
    s=mapped;first=save(s,packet(s));p=packet(s,'replacement');p['selection']['acceptance_ids']=['A2'];new=save(s,p)
    assert first['mapping_key']==new['mapping_key'] and new['revision']==2
    assert inspect(s)['rows'][0]['operation']==new and lookup(s,first)==first


def test_readonly_selection_uses_only_acceptance_ids_already_selected_on_the_original_link(mapped):
    s=mapped;view=inspect(s,selected_requirement_link_operation_id=s['link']['operation_id'])
    assert {row['id'] for row in view['selected_requirement_link']['acceptance']}=={'A1','A2'}
    with pytest.raises(Conflict):inspect(s,selected_requirement_link_operation_id='foreign')


def test_expanded_execution_fields_and_nonliteral_paths_are_rejected_before_mapping(mapped):
    s=mapped;p=packet(s)
    with pytest.raises(ValidationError):WorkCaseMap.model_validate({**p,'command':'run'})
    for path in ['../report.xml','/tmp/report.xml','C:/report.xml','**/*.xml']:
        bad={**source(s),'report_path':path}
        with pytest.raises((ValidationError,InvalidInput)):WorkCaseSource.model_validate(bad)
    with pytest.raises(ValidationError):WorkCaseSelection.model_validate({**p['selection'],'acceptance_ids':['A1','A1']})


def test_real_http_mapping_and_readonly_original_lookup_use_existing_service_without_provider(mapped,monkeypatch):
    s=mapped
    async def forbidden(*_args,**_kwargs):pytest.fail('No native provider or owner may be started')
    monkeypatch.setattr(s['service'],'inspect',forbidden);monkeypatch.setattr(s['service'],'send',forbidden)
    base=f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}/work-test-cases"
    with TestClient(app_for(s['service'])) as client:
        response=client.post(base+'/mappings',json=packet(s));assert response.status_code==200,response.text
        saved=response.json();view=client.post(base+'/inspect',json=s['input']);assert view.status_code==200,view.text
        query={**s['input'],'operation_id':saved['operation_id'],'request_sha256':saved['request_sha256']}
        lookup_response=client.post(base+'/operations/inspect',json=query);assert lookup_response.status_code==200,lookup_response.text
        assert lookup_response.json()==saved and saved['whole_task_acceptance']=='NOT_ASSESSED'


def test_refactored_shared_report_loader_keeps_original_public_report_hash_and_page(mapped):
    s=mapped;public=s['service'].command_test_report(s['pid'],s['ident'],s['report_input'])
    loaded=_load_command_report(s['service'],s['pid'],s['ident'],CommandTestReportInput.model_validate(s['report_input']))
    assert public['report_ref']==loaded['report_ref'] and public['basis']==loaded['basis']
    assert public['report_ref']['sha256']==digest({'basis':loaded['basis'],'parsed':loaded['parsed']})
    assert public['cases']==loaded['parsed']['cases'] and public['request_sha256']==digest(s['report_input'])


def test_focused_mapping_lookup_finds_exact_active_record_even_outside_requested_page(mapped):
    s=mapped;old=save(s,packet(s));view=inspect(s,offset=128,mapping_key=old['mapping_key'])
    assert view['rows']==[] and view['selected_mapping']==old
    assert view['query_sha256']==digest(WorkCaseInspect.model_validate({**s['input'],'offset':128,'mapping_key':old['mapping_key']}).model_dump())


def test_case_and_requirement_selection_find_existing_slot_without_guessing_by_case_name(mapped):
    s=mapped;one=save(s,packet(s,'case_zero',0));two=save(s,packet(s,'case_one',1))
    a=inspect(s,case_source=source(s,0));b=inspect(s,case_source=source(s,1))
    assert a['selected_mapping']==one and b['selected_mapping']==two


def test_absent_or_removed_focused_slot_returns_no_current_mapping_and_keeps_old_receipt(mapped):
    s=mapped;old=save(s,packet(s));removal=packet(s,'remove_focused',action='REMOVE',selection=None,mapping_key=old['mapping_key']);save(s,removal)
    assert inspect(s,mapping_key=old['mapping_key'])['selected_mapping'] is None
    assert lookup(s,old)==old
