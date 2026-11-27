"""AUTHORED_NOT_RUN: bounded XML parser and real saved command journals.

The observer's fixture substitutes the SDK tool boundary. These are not real
pytest executions, independent target acceptance, native Windows or UI proof.
All eventual pytest temporaries must stay in the internal workspace.
"""
from copy import deepcopy
import hashlib

import pytest

from packages.agent_sessions.test_reports import CommandTestReportInput,parse_junit
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_opencode_command_observation import command_scene,exercise,observation
from test_command_observations_routes import routed,persist

PASS=b'<testsuite name="suite" tests="1" failures="0" errors="0" skipped="0"><testcase name="one" classname="test_math" time="0.01"/></testsuite>'


def test_junit_counts_cases_without_using_command_names_or_exit_codes():
    r=parse_junit(PASS);assert r['summary']['outcome']=='PASSED' and r['summary']['total']==1
    assert r['cases'][0]['suite']=='suite' and r['cases'][0]['status']=='PASSED'


def test_failures_errors_and_skips_are_separate_report_facts():
    r=parse_junit(b'<testsuites tests="4"><testsuite name="suite"><testcase name="pass"/><testcase name="fail"><failure message="wrong">expected 2</failure></testcase><testcase name="err"><error>setup</error></testcase><testcase name="skip"><skipped type="pytest.xfail"/></testcase></testsuite></testsuites>')
    assert r['summary']['counts']=={'PASSED':1,'FAILED':1,'ERROR':1,'SKIPPED':1,'UNKNOWN':0}
    assert r['summary']['outcome']=='FAILED' and r['cases'][1]['detail']=='expected 2'


def test_nested_suites_and_duplicate_names_keep_distinct_case_ordinals():
    r=parse_junit(b'<testsuites tests="2"><testsuite name="outer" tests="2"><testcase name="same"/><testsuite name="inner" tests="1"><testcase name="same"/></testsuite></testsuite></testsuites>')
    assert [c['ordinal'] for c in r['cases']]==[0,1]
    assert [c['suite'] for c in r['cases']]==['outer','inner'] and not r['summary']['integrity_issues']


def test_empty_all_skipped_and_partially_skipped_never_become_an_all_pass_badge():
    assert parse_junit(b'<testsuite tests="0"/>')['summary']['outcome']=='NO_TESTS'
    assert parse_junit(b'<testsuite><testcase name="skip"><skipped/></testcase></testsuite>')['summary']['outcome']=='SKIPPED'
    assert parse_junit(b'<testsuite><testcase name="pass"/><testcase name="skip"><skipped/></testcase></testsuite>')['summary']['outcome']=='PASSED_WITH_SKIPS'


def test_declared_totals_are_checked_against_actual_cases():
    r=parse_junit(b'<testsuite tests="5" failures="0"><testcase name="one"/></testsuite>')
    assert r['summary']['outcome']=='INCONSISTENT' and r['summary']['total']==1
    assert 'DECLARED_CASE_COUNTS_DIFFER' in r['summary']['integrity_issues']


def test_ambiguous_case_markers_are_unknown_and_preserve_the_report_conflict():
    r=parse_junit(b'<testsuite><testcase name="ambiguous"><failure>assert</failure><error>teardown</error></testcase></testsuite>')
    assert r['cases'][0]['status']=='UNKNOWN' and r['summary']['outcome']=='INCONSISTENT'


def test_custom_entities_and_dtd_cannot_read_external_files_or_expand():
    for raw in [b'<!DOCTYPE testsuite SYSTEM "file:///etc/passwd"><testsuite/>',b'<!DOCTYPE testsuite [<!ENTITY a "boom">]><testsuite>&a;</testsuite>',b'<!ENTITY x SYSTEM "https://example.invalid/">']:
        with pytest.raises(Conflict):parse_junit(raw)


def test_malformed_namespaced_non_utf8_or_unsupported_xml_is_not_a_zero_test_success():
    for raw in [b'<testsuite>',b'<project><testcase/></project>',b'<j:testsuite xmlns:j="urn:junit"/>','<testsuite/>'.encode('utf-16'),b'<?xml version="1.0" encoding="ISO-8859-1"?><testsuite/>']:
        with pytest.raises(Conflict):parse_junit(raw)


def test_depth_byte_case_and_node_bounds_fail_before_partial_pass_is_published():
    for raw in [b'<testsuite>'+b'<testsuite>'*20+b'</testsuite>'*20+b'</testsuite>',b' '*(262144+1),b'<testsuite>'+b'<testcase name="a"/>'*2001+b'</testsuite>',b'<testsuite><properties>'+b'<property/>'*8200+b'</properties></testsuite>',b'<testsuites>'+b'<testsuite/>'*129+b'</testsuites>']:
        with pytest.raises(Conflict):parse_junit(raw)


def test_report_duration_is_bounded_metadata_and_failure_details_are_truncated():
    raw=('<testsuite><testcase name="long" time="NaN"><failure message="'+('x'*500)+'">'+('y'*2000)+'</failure></testcase></testsuite>').encode()
    r=parse_junit(raw);assert r['cases'][0]['duration_seconds'] is None
    assert r['cases'][0]['text_truncated'] and len(r['cases'][0]['detail'])==512
    assert r['summary']['metadata_warnings']==['INVALID_CASE_DURATION']


def request(s,entry):
    after=entry['sources'][0]['after']
    return {'session_ref':s['ref'],'generation':1,'workspace_ref':s['workspace_ref'],
        'command_ref':entry['record_ref'],'command_generation':1,'call_id':entry['call_id'],
        'report_path':'a.py','report_sha256':after['sha256'],'offset':0,'limit':20,'outcome':'ALL'}


def report_scene(s,**options):
    packet=exercise(s,changeDuring=PASS.decode(),exit=0,**options)
    state,op,expected=persist(s,packet)
    catalog=s['service'].command_observations(s['pid'],s['ident'],**expected)
    return packet,state,op,expected,request(s,catalog['commands'][0])


def test_report_reader_uses_immutable_after_bytes_and_preserves_later_manual_changes(routed,monkeypatch):
    s=routed;_,_,_,_,body=report_scene(s)
    (s['folder']/'a.py').write_text('LATER_MANUAL_CONTENT_KEEP\n')
    import packages.adapters.opencode_command_observation as adapter
    real=adapter._regular
    def no_current(path,*args):
        assert path!=s['folder']/'a.py','Report reader must not reread today\'s workspace file'
        return real(path,*args)
    monkeypatch.setattr(adapter,'_regular',no_current)
    r=s['service'].command_test_report(s['pid'],s['ident'],body)
    assert r['summary']['outcome']=='PASSED' and r['association']=='CHANGED_BYTES'
    assert r['writer_attribution']=='NOT_PROVEN' and r['whole_task_acceptance']=='NOT_ASSESSED'
    assert r['commands_started']==r['model_calls']==r['source_files_written']==0
    assert (s['folder']/'a.py').read_text()=='LATER_MANUAL_CONTENT_KEEP\n'


def test_a_report_with_no_new_bytes_keeps_generation_unproven(routed):
    s=routed;(s['folder']/'a.py').write_bytes(PASS)
    packet=exercise(s,exit=0);_,_,expected=persist(s,packet)
    entry=s['service'].command_observations(s['pid'],s['ident'],**expected)['commands'][0]
    r=s['service'].command_test_report(s['pid'],s['ident'],request(s,entry))
    assert r['association']=='UNCHANGED_BYTES' and 'NEW_REPORT_GENERATION_NOT_PROVEN' in r['reason_codes']
    assert r['current_workspace_verified'] is r['target_environment_verified'] is False


def test_unknown_original_command_outcome_is_not_replaced_by_passing_report_data(routed):
    s=routed;packet=exercise(s,changeDuring=PASS.decode(),nullExit=True)
    _,_,expected=persist(s,packet);entry=s['service'].command_observations(s['pid'],s['ident'],**expected)['commands'][0]
    r=s['service'].command_test_report(s['pid'],s['ident'],request(s,entry))
    assert r['summary']['outcome']=='PASSED' and r['command_status']=='UNKNOWN_OUTCOME'
    assert 'COMMAND_OUTCOME_UNKNOWN' in r['reason_codes'] and r['command_exit_code'] is None


def test_wrong_command_hash_generation_or_report_path_cannot_select_another_blob(routed):
    s=routed;_,_,_,_,body=report_scene(s)
    variants=[{'command_ref':{**body['command_ref'],'sha256':'f'*64}}, {'command_generation':2}, {'report_path':'another.xml'}, {'report_sha256':'e'*64}]
    for update in variants:
        with pytest.raises(Conflict):s['service'].command_test_report(s['pid'],s['ident'],{**body,**update})


def test_paging_filtering_and_repeated_reads_keep_the_same_report_ref_and_original_journal(routed):
    s=routed;xml='<testsuite>'+''.join('<testcase name="case_'+str(i)+'">'+('<failure>failed</failure>' if i%2 else '')+'</testcase>' for i in range(45))+'</testsuite>'
    report=s['folder']/'fixture-report.xml';report.write_text(xml)
    packet=exercise(s,changeDuringFile=str(report),exit=1);assert len(packet['args']['command'])<=1024
    _,op,expected=persist(s,packet)
    entry=s['service'].command_observations(s['pid'],s['ident'],**expected)['commands'][0];body=request(s,entry);before=op.read_bytes()
    first=s['service'].command_test_report(s['pid'],s['ident'],body)
    second=s['service'].command_test_report(s['pid'],s['ident'],{**body,'offset':20})
    problems=s['service'].command_test_report(s['pid'],s['ident'],{**body,'outcome':'PROBLEMS'})
    assert first['report_ref']==second['report_ref']==problems['report_ref']
    assert first['page']['next_offset']==20 and second['cases'][0]['ordinal']==20
    assert problems['page']['matching']==22 and all(c['status']=='FAILED' for c in problems['cases'])
    assert op.read_bytes()==before and first['request_sha256']==digest(CommandTestReportInput.model_validate(body).model_dump())


def test_specific_report_read_does_not_read_unrelated_command_records(routed,monkeypatch):
    s=routed;first=exercise(s,callID='selected',changeDuring=PASS.decode(),exit=0)
    second=exercise(s,callID='unrelated',changeDuring='NOT_A_TEST_REPORT',exit=7)
    state,_,expected=persist(s,first);state['messages']+=observation(s,second,message='msg_unrelated')['messages']
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    entries=s['service'].command_observations(s['pid'],s['ident'],**expected)['commands']
    selected=next(row for row in entries if row['call_id']=='selected')
    other=digest({'native_session_id':'ses_command','call_id':'unrelated'})
    import packages.adapters.opencode_command_observation as adapter
    real=adapter._regular
    def only_selected(path,*args):
        assert other not in str(path),'Specific report read must not inspect unrelated command records'
        return real(path,*args)
    monkeypatch.setattr(adapter,'_regular',only_selected)
    assert s['service'].command_test_report(s['pid'],s['ident'],request(s,selected))['summary']['outcome']=='PASSED'


def test_readonly_report_http_route_rejects_expanded_input_without_starting_an_owner(routed,monkeypatch):
    from fastapi.testclient import TestClient
    from test_session_diffs import app_for
    s=routed;_,_,_,_,body=report_scene(s)
    async def no_execution(*_args,**_kwargs):pytest.fail('Report inspection must not start or send')
    monkeypatch.setattr(s['service'],'open',no_execution);monkeypatch.setattr(s['service'],'send',no_execution)
    with TestClient(app_for(s['service'])) as client:
        route='/api/projects/'+s['pid']+'/agent-sessions/'+s['ident']+'/command-test-reports/inspect'
        result=client.post(route,json=body);assert result.status_code==200,result.text
        assert result.json()['read_only'] is True and result.json()['commands_started']==0
        assert client.post(route,json={**body,'commands_started':True}).status_code==422


def test_same_historical_report_ref_survives_newer_owner_observation_generation(routed):
    s=routed;_,state,_,_,body=report_scene(s)
    original=s['service'].command_test_report(s['pid'],s['ident'],body)
    state['generation']=2;s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    later=s['service'].command_test_report(s['pid'],s['ident'],{**body,'generation':2})
    assert original['report_ref']==later['report_ref']
    assert later['basis']['scope']['generation']==1 and later['observation_scope']['generation']==2
