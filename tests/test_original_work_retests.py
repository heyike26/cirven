"""AUTHORED_NOT_RUN. Real Store, immutable reports, ledger, HTTP and SEND claim.

Original owner/native POST/model/command execution are explicit UNIT boundaries.
No test here qualifies actual OpenCode, Windows, target coverage or acceptance.
"""
from copy import deepcopy
import asyncio
import json
import sqlite3
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import digest,canonical
from packages.agent_sessions.models import SendMessage,operation_request
from packages.agent_sessions.work_retests import (WorkRetestPrepare,WorkRetestSubmit,WorkRetestInspect,WorkRetestContinue,
    prepare_work_retest,submit_work_retest,inspect_work_retest,continue_work_retest,admit_dispatch,resolved_command_roots)
from packages.agent_sessions.command_observations import project_command_observations
from test_command_observations_routes import make_routed
from test_opencode_command_observation import command_scene
from test_original_work_test_cases import mapped,packet,save,inspect as case_view
from test_original_work_requirements import revise
from test_session_diffs import app_for

COMMAND='python -m pytest'


@pytest.fixture
def routed(command_scene):return make_routed(command_scene,commands=[COMMAND,'python other.py'])


@pytest.fixture
def retest(mapped):
    s=mapped;s['mapping']=save(s,packet(s));state=s['service']._state(s['pid'],s['ident'])
    state.update(owner_status='READY',active_operation_id=None,native_status='idle')
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    v=case_view(s);s['prepare']={**s['input'],'root_sha256':s['root_sha256'],
        'expected_case_revision':v['revision'],'expected_requirement_revision':v['requirement_revision'],
        'mappings':[{'operation_id':s['mapping']['operation_id'],'request_sha256':s['mapping']['request_sha256']}],
        'command':COMMAND,'timeout_seconds':300}
    return s


def prepare(s,**changes):return prepare_work_retest(s['service'],s['pid'],s['ident'],{**s['prepare'],**changes})
def submit_packet(s,operation='retest_original'):
    p=prepare(s);return {**s['prepare'],'operation_id':operation,'preparation_sha256':p['preparation_sha256']}
def lookup(s,p,**changes):return inspect_work_retest(s['service'],s['pid'],s['ident'],{
    **s['input'],'operation_id':p['operation_id'],'request_sha256':digest(WorkRetestSubmit.model_validate(p).model_dump()),**changes})


def native_boundary(s,monkeypatch,*,after_claim=None):
    calls=[];owner=s['service']
    async def send(pid,sid,body):
        value=SendMessage.model_validate(body);record,created=owner._claim(pid,sid,value.operation_id,'MESSAGE',operation_request(value))
        calls.append({'body':value.model_dump(),'created':created})
        if not created:return record
        with owner.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');admit_dispatch(owner,connection,pid,sid,owner._manifest(pid,sid)[0],value,owner._state(pid,sid))
        if after_claim:
            try:after_claim()
            except BaseException:
                owner._finish(pid,sid,value.operation_id,'UNKNOWN_OUTCOME',generation=value.generation,
                    original_session_id='ses_command',native_message_id='msg_retest',native_post_attempted=True)
                raise
        return owner._finish(pid,sid,value.operation_id,'ACCEPTED',generation=value.generation,
            original_session_id='ses_command',native_message_id='msg_retest',native_dispatch_acknowledged=True)
    monkeypatch.setattr(owner,'send',send);return calls


def test_prepare_uses_real_active_mappings_and_report_but_never_dispatches_or_changes_old_files(retest):
    s=retest;before=s['root_path'].read_bytes();p=prepare(s)
    assert p['ready'] and p['preparation_sha256']==digest(p['basis'])
    assert p['basis']['selected'][0]['case_ref']==s['mapping']['proof']['case_ref']
    assert p['allowed_commands']==[COMMAND,'python other.py']
    assert p['model_calls']==p['commands_started']==p['source_files_written']==0
    assert p['target_case_execution']=='NOT_PROVEN' and s['root_path'].read_bytes()==before


def test_no_command_selection_returns_choices_and_no_implicit_reuse_or_path_to_command(retest):
    p=prepare(retest,command=None);assert not p['ready'] and 'EXACT_COMMAND_SELECTION_REQUIRED' in p['reason_codes']
    assert p['basis']['command'] is None and p['dispatch_preview'] is None
    assert not prepare(retest,command='python unapproved.py')['ready']


def test_prompt_preview_preserves_exact_authorized_command_and_does_not_derive_report_self_stated_file(retest):
    p=prepare(retest);text=p['dispatch_preview']['raw_text'];assert COMMAND in text
    assert '../../' not in text and 'report-self-stated' not in text
    assert p['basis']['permission_scope']=='EXISTING_CONFIRMED_SESSION_SCOPE_NOT_NARROWED'


def test_selected_requirement_change_blocks_but_unselected_change_keeps_old_reference(retest):
    s=retest;old=s['bundle'];current=revise(s,old,lambda v:v['requirements'][1].update(text='Unselected branch changed'))
    p=prepare(s);assert p['ready'] and p['basis']['selected'][0]['requirement_ref']['revision']==1
    assert p['requirement_observations'][0]['requirement_status']=='SELECTED_UNCHANGED_NEWER_BUNDLE'
    revise(s,current,lambda v:v['acceptance'][0].update(text='Selected criterion changed'))
    p=prepare(s);assert not p['ready'] and 'SELECTED_REQUIREMENTS_NEED_REVIEW' in p['reason_codes']


def test_either_head_or_removed_mapping_rejects_preparation(retest):
    s=retest
    for change in [{'expected_case_revision':0},{'expected_requirement_revision':0},
            {'mappings':[{'operation_id':'missing','request_sha256':'a'*64}]}]:
        with pytest.raises(Conflict):prepare(s,**change)


def test_busy_disconnected_and_different_native_owner_cannot_become_ready(retest):
    s=retest;path=s['service']._path(s['pid'],s['ident'],'state.json');original=s['service']._state(s['pid'],s['ident'])
    for change in [{'active_operation_id':'another'},{'owner_status':'DISCONNECTED'},{'original_session_id':'ses_another'}]:
        s['service']._write(path,{**original,**change},replace=True);assert not prepare(s)['ready']


def test_durable_intent_precedes_original_send_claim_and_repeat_submit_never_replays(retest,monkeypatch):
    s=retest;p=submit_packet(s);calls=native_boundary(s,monkeypatch)
    first=asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p));second=asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    assert len(calls)==1 and first['original_dispatch']['native_dispatch_acknowledged']
    assert second['original_dispatch']==first['original_dispatch']
    assert s['service'].operation(s['pid'],s['ident'],p['operation_id'])['work_retest']['root_operation_id']==s['input']['root_operation_id']
    assert first['case_correspondence']=='EXPLICIT_NEW_CASE_MAPPING_REQUIRED'


def test_lost_send_ack_retains_immutable_intent_and_original_key_is_readonly(retest,monkeypatch):
    s=retest;p=submit_packet(s);calls=native_boundary(s,monkeypatch,after_claim=lambda:(_ for _ in ()).throw(TimeoutError('UNIT lost native ACK')))
    with pytest.raises(TimeoutError):asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    v=lookup(s,p);assert v['original_dispatch']['status']=='UNKNOWN_OUTCOME'
    asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p));assert len(calls)==1


def test_intent_saved_before_send_failure_is_not_replayed_without_explicit_continue(retest,monkeypatch):
    s=retest;p=submit_packet(s);calls=[]
    async def fail(*args):calls.append(args);raise ConnectionError('UNIT before original claim')
    monkeypatch.setattr(s['service'],'send',fail)
    with pytest.raises(ConnectionError):asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    assert lookup(s,p)['status']=='INTENT_RECORDED_ORIGINAL_NOT_FOUND'
    asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p));assert len(calls)==1
    resumed=native_boundary(s,monkeypatch);q={**s['input'],'operation_id':p['operation_id'],
        'request_sha256':digest(WorkRetestSubmit.model_validate(p).model_dump()),'preparation_sha256':p['preparation_sha256']}
    asyncio.run(continue_work_retest(s['service'],s['pid'],s['ident'],q));asyncio.run(continue_work_retest(s['service'],s['pid'],s['ident'],q));assert len(resumed)==1


def test_changed_preparation_or_key_input_cannot_send(retest,monkeypatch):
    s=retest;calls=native_boundary(s,monkeypatch);p=submit_packet(s);p['preparation_sha256']='0'*64
    with pytest.raises(Conflict):asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    assert not calls


def test_reserved_original_send_key_rejects_different_body_and_keeps_prior_intent(retest,monkeypatch):
    s=retest;p=submit_packet(s)
    async def fail(*args):raise ConnectionError('UNIT intent only')
    monkeypatch.setattr(s['service'],'send',fail)
    with pytest.raises(ConnectionError):asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    request=operation_request(SendMessage(operation_id=p['operation_id'],session_ref=s['ref'],generation=1,raw_text='different'))
    with pytest.raises(Conflict):s['service']._claim(s['pid'],s['ident'],p['operation_id'],'MESSAGE',request)
    assert lookup(s,p)['original_dispatch'] is None


def test_immutable_intent_and_bounded_capacity_do_not_delete_unknown_evidence(retest,monkeypatch):
    s=retest;p=submit_packet(s);native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    for sql in ['DELETE FROM original_work_retest_intents','UPDATE original_work_retest_intents SET request_sha256=\'bad\'']:
        with pytest.raises(sqlite3.IntegrityError):
            with s['service'].store.connect() as c:c.execute(sql)
    from packages.agent_sessions import work_retests as module
    monkeypatch.setattr(module,'MAX_INTENTS',1)
    with pytest.raises(Conflict):asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],submit_packet(s,'next')))
    assert lookup(s,p)['original_dispatch'] is not None


def test_command_projection_reuses_original_work_root_only_after_immutable_sql_binding(retest,monkeypatch):
    s=retest;p=submit_packet(s);native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    op=s['service'].operation(s['pid'],s['ident'],p['operation_id']);roots=resolved_command_roots(s['service'],s['pid'],s['ident'],[op]);assert roots=={p['operation_id']:s['input']['root_operation_id']}
    catalog=s['service'].command_observations(s['pid'],s['ident'],session_sha256=s['ref']['sha256'],generation=1,workspace_ref=s['workspace_ref']);entry=deepcopy(catalog['commands'][0]);entry['parent_native_message_id']='msg_retest';entry['native_message_id']='msg_retest';entry['command']=COMMAND
    observed={**catalog['scope'],'original_session_id':'ses_command'}
    verified=project_command_observations(observed,[entry],[op],trusted_work_roots=roots)
    assert verified['commands'][0]['root_operation_id']==s['input']['root_operation_id'] and verified['commands'][0]['operation_id']==p['operation_id']
    refused=project_command_observations(observed,[entry],[op]);assert not refused['commands'] and 'RETEST_WORK_ROOT_NOT_VERIFIED' in refused['coverage']['reason_codes']


def test_false_retest_metadata_cannot_reparent_a_native_message(retest,monkeypatch):
    s=retest;p=submit_packet(s);native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    op=s['service'].operation(s['pid'],s['ident'],p['operation_id']);op['work_retest']['root_operation_id']='another_root'
    assert resolved_command_roots(s['service'],s['pid'],s['ident'],[op])=={}


def test_native_dispatch_and_new_command_evidence_are_distinct_from_target_case_acceptance(retest,monkeypatch):
    s=retest;p=submit_packet(s);native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    v=lookup(s,p,include_command_evidence=True);assert v['command_evidence']['status']=='NO_MATCHED_COMMAND_OBSERVED'
    assert v['target_case_execution']=='NOT_PROVEN' and v['whole_task_acceptance']=='NOT_ASSESSED'


def test_http_readonly_prepare_and_exact_lookup_have_strict_schemas(retest):
    s=retest;client=TestClient(app_for(s['service']));base='/api/projects/'+s['pid']+'/agent-sessions/'+s['ident']+'/work-retests'
    response=client.post(base+'/prepare',json=s['prepare']);assert response.status_code==200 and response.json()['read_only']
    bad=client.post(base+'/prepare',json={**s['prepare'],'run':True});assert bad.status_code==422
    absent=client.post(base+'/operations/inspect',json={**s['input'],'operation_id':'absent','request_sha256':'0'*64});assert absent.status_code==404


def test_literal_commands_duplicate_targets_and_fake_coverage_fields_are_rejected(retest):
    s=retest
    for bad in [{**s['prepare'],'command':'python *.py'},{**s['prepare'],'command':'python\nother'},
            {**s['prepare'],'mappings':s['prepare']['mappings']*2},{**s['prepare'],'coverage':'PASSED'}]:
        with pytest.raises(ValidationError):WorkRetestPrepare.model_validate(bad)


def test_unselected_newer_requirement_version_does_not_invalidate_the_same_frozen_retest(retest,monkeypatch):
    s=retest;p=submit_packet(s);before=prepare(s);revise(s,s['bundle'],lambda v:v['requirements'][1].update(text='Unselected-only change'))
    after=prepare(s);assert after['preparation_sha256']==before['preparation_sha256']
    assert after['basis']['selected'][0]['requirement_status']=='ELIGIBLE'
    assert after['basis']['selected'][0]['requirement_ref']==before['basis']['selected'][0]['requirement_ref']
    calls=native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p));assert len(calls)==1


def test_running_guidance_claim_and_command_projection_keep_verified_retest_work_root(retest,monkeypatch):
    from packages.agent_sessions.guidance import GuidanceInput
    from packages.agent_sessions.work_retests import admit_guidance
    s=retest;p=submit_packet(s);native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    parent=s['service'].operation(s['pid'],s['ident'],p['operation_id'])
    body=GuidanceInput(operation_id='guide_retest',session_ref=s['ref'],generation=1,raw_text='Retain my manual edits',intent='STEER',expected_active_operation_id=p['operation_id'])
    guide,_=s['service']._claim(s['pid'],s['ident'],body.operation_id,'GUIDANCE',operation_request(body))
    assert guide['work_retest']==parent['work_retest']
    with s['service'].store.connect() as connection:admit_guidance(s['service'],connection,s['pid'],s['ident'],parent,body)
    guide=s['service']._finish(s['pid'],s['ident'],body.operation_id,'ACCEPTED',generation=1,original_session_id='ses_command',native_message_id='msg_guide',original_active_operation_id=p['operation_id'])
    roots=resolved_command_roots(s['service'],s['pid'],s['ident'],[guide]);assert roots=={guide['id']:s['input']['root_operation_id']}
    catalog=s['service'].command_observations(s['pid'],s['ident'],session_sha256=s['ref']['sha256'],generation=1,workspace_ref=s['workspace_ref']);entry=deepcopy(catalog['commands'][0]);entry['parent_native_message_id']='msg_guide'
    projected=project_command_observations({**catalog['scope'],'original_session_id':'ses_command'},[entry],[guide],trusted_work_roots=roots)['commands'][0]
    assert projected['operation_id']==guide['id'] and projected['work_retest_operation_id']==p['operation_id'] and projected['root_operation_id']==s['input']['root_operation_id']


def test_guidance_cannot_steal_retest_message_key_or_inherit_another_generation(retest,monkeypatch):
    from packages.agent_sessions.guidance import GuidanceInput
    s=retest;p=submit_packet(s);native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    body=GuidanceInput(operation_id=p['operation_id'],session_ref=s['ref'],generation=1,raw_text='wrong kind',expected_active_operation_id=p['operation_id'])
    with pytest.raises(Conflict):s['service']._claim(s['pid'],s['ident'],body.operation_id,'GUIDANCE',operation_request(body))
    body=GuidanceInput(operation_id='new_generation_guide',session_ref=s['ref'],generation=2,raw_text='new owner',expected_active_operation_id=p['operation_id'])
    with pytest.raises(Conflict):s['service']._claim(s['pid'],s['ident'],body.operation_id,'GUIDANCE',operation_request(body))


def test_bad_guidance_parent_or_native_owner_cannot_reparent_command_history(retest,monkeypatch):
    from packages.agent_sessions.guidance import GuidanceInput
    s=retest;p=submit_packet(s);native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    body=GuidanceInput(operation_id='guide_retest',session_ref=s['ref'],generation=1,raw_text='keep evidence',expected_active_operation_id=p['operation_id'])
    s['service']._claim(s['pid'],s['ident'],body.operation_id,'GUIDANCE',operation_request(body))
    guide=s['service']._finish(s['pid'],s['ident'],body.operation_id,'ACCEPTED',generation=1,original_session_id='ses_command',native_message_id='msg_guide',original_active_operation_id=p['operation_id'])
    for change in [{'original_active_operation_id':'another'},{'original_session_id':'ses_another'}]:
        assert resolved_command_roots(s['service'],s['pid'],s['ident'],[{**guide,**change}])=={}


def test_retest_inspection_includes_verified_running_guidance_commands_without_promoting_coverage(retest,monkeypatch):
    s=retest;p=submit_packet(s);native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    catalog=s['service'].command_observations(s['pid'],s['ident'],session_sha256=s['ref']['sha256'],generation=1,workspace_ref=s['workspace_ref']);command=deepcopy(catalog['commands'][0]);command.update(operation_id='guide_retest',root_operation_id=s['input']['root_operation_id'],work_retest_operation_id=p['operation_id'],native_message_id='msg_guide',command=COMMAND)
    monkeypatch.setattr(s['service'],'command_observations',lambda *a,**k:{**catalog,'commands':[command]})
    v=lookup(s,p,include_command_evidence=True);assert v['command_evidence']['commands'][0]['operation_id']=='guide_retest'
    assert v['command_evidence']['commands'][0]['matches_requested_command'] is True and v['target_case_execution']=='NOT_PROVEN'


def foreign_retest(s):
    # Actual immutable foreign data grant/link/case mapping; native execution
    # remains the existing labelled UNIT boundary of this file.
    from test_original_work_requirement_sources import foreign,review_packet,allow,associate
    original,b=foreign(s);p=review_packet(s,original,b);_,grant=allow(s,p)
    s['link']=associate(s,b,grant);s['bundle']=b;s['mapping']=save(s,packet(s,'foreign_case'))
    current=case_view(s)
    s['prepare'].update(expected_case_revision=current['revision'],
        expected_requirement_revision=current['requirement_revision'],
        mappings=[{'operation_id':s['mapping']['operation_id'],'request_sha256':s['mapping']['request_sha256']}])
    return p,grant


def test_foreign_source_revocation_propagates_case_status_and_blocks_prepared_retest(retest,monkeypatch):
    from test_original_work_requirement_sources import revoke
    s=retest;source_packet,grant=foreign_retest(s);p=submit_packet(s)
    assert prepare(s)['ready'] and prepare(s)['basis']['scope']['workspace_ref']==s['workspace_ref']
    revoke(s,source_packet,grant)
    row=next(r for r in case_view(s)['rows'] if r['operation']['operation_id']==s['mapping']['operation_id'])
    assert row['requirement_status']=='UNAVAILABLE' and row['operation']==s['mapping']
    observed=prepare(s);assert not observed['ready'] and 'SELECTED_REQUIREMENTS_NEED_REVIEW' in observed['reason_codes']
    calls=native_boundary(s,monkeypatch)
    with pytest.raises(Conflict):asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    assert not calls


def test_revocation_between_intent_and_native_dispatch_preserves_original_intent_without_a_post(retest,monkeypatch):
    from test_original_work_requirement_sources import revoke
    s=retest;source_packet,grant=foreign_retest(s);p=submit_packet(s);calls=[]
    async def send(pid,sid,raw):
        revoke(s,source_packet,grant)
        body=SendMessage.model_validate(raw);s['service']._claim(pid,sid,body.operation_id,'MESSAGE',operation_request(body))
        with s['service'].store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            admit_dispatch(s['service'],c,pid,sid,s['service']._manifest(pid,sid)[0],body,s['service']._state(pid,sid))
        calls.append(raw)
        pytest.fail('A revoked source must not reach the native POST boundary')
    monkeypatch.setattr(s['service'],'send',send)
    with pytest.raises(Conflict):asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    assert not calls and lookup(s,p)['original_request']==WorkRetestSubmit.model_validate(p).model_dump()


def test_running_guidance_rechecks_foreign_source_authority_without_rewriting_parent(retest,monkeypatch):
    from test_original_work_requirement_sources import revoke
    from packages.agent_sessions.guidance import GuidanceInput
    from packages.agent_sessions.work_retests import admit_guidance
    s=retest;source_packet,grant=foreign_retest(s);p=submit_packet(s)
    native_boundary(s,monkeypatch);asyncio.run(submit_work_retest(s['service'],s['pid'],s['ident'],p))
    parent=s['service'].operation(s['pid'],s['ident'],p['operation_id'])
    body=GuidanceInput(operation_id='foreign_guide',session_ref=s['ref'],generation=1,
        raw_text='Keep manual edits',expected_active_operation_id=p['operation_id'])
    s['service']._claim(s['pid'],s['ident'],body.operation_id,'GUIDANCE',operation_request(body))
    revoke(s,source_packet,grant)
    with s['service'].store.connect() as c:
        with pytest.raises(Conflict):admit_guidance(s['service'],c,s['pid'],s['ident'],parent,body)
    assert s['service'].operation(s['pid'],s['ident'],p['operation_id'])==parent
