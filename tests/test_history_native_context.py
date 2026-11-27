"""AUTHORED_NOT_RUN. Real bounded session-record reader; no App/Store/SDK.

This UNIT scope checks saved history issue projection and shared IO accounting.
It does not qualify a running private owner, API server, Windows or IDE.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from apps.api.conversation_history import ConversationHistory, ReadBudget, READ_LIMIT
from packages.agent_sessions.service import AgentSessions
from packages.domain.tasks import digest


@pytest.fixture
def saved(tmp_path):
    owner=object.__new__(AgentSessions);owner.data_dir=tmp_path
    pid='unit_private_project';sid='b'*32
    native_ref={'id':'native_parent','revision':1,'sha256':'a'*64}
    manifest={'project_id':pid,'id':sid,'native_message_admission':native_ref}
    reference={'id':sid,'revision':1,'sha256':digest(manifest)}
    issue={'code':'SOURCE_CONTEXT_CHANGED','generation':1,'operation_id':'old_task',
        'scope':'FUTURE_HOST_ADMISSIONS_AND_API_RELAY','original_history_retained':True}
    owner._write(owner._path(pid,sid,'manifest.json'),manifest)
    owner._write(owner._path(pid,sid,'state.json'),{'generation':1,'context_authority_issue':issue})
    record={'request':{'operation_id':'native_parent','raw_text':'KEEP_ORIGINAL_INPUT'},
        'generation':'old_host','status':'UNKNOWN_OUTCOME','native_message_ref':native_ref,
        'owner':{'project_id':pid,'session_ref':reference,'generation':1}}
    value=object.__new__(ConversationHistory);value.native=SimpleNamespace(generation='old_host')
    value.app=SimpleNamespace(state=SimpleNamespace(agent_sessions=owner))
    return SimpleNamespace(value=value,owner=owner,pid=pid,sid=sid,record=record,issue=issue,native_ref=native_ref)


def test_private_source_issue_is_readonly_and_does_not_expose_execution_project_or_owner(saved):
    x=saved;paths=[x.owner._path(x.pid,x.sid,name) for name in ['manifest.json','state.json']]
    before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths};budget=ReadBudget()
    result=x.value._native_context(x.record,budget)
    assert result['native_context_issue']==x.issue
    assert result['native_context_recovery']['expected_native_ref']==x.native_ref
    assert result['native_context_recovery']['model_calls']==0 and budget.reads==2
    assert x.pid not in str(result) and x.sid not in str(result)
    assert all((p.read_bytes(),p.stat().st_mtime_ns)==v for p,v in before.items())


def test_multiple_native_turns_share_one_manifest_and_state_read_budget_cache(saved):
    x=saved;budget=ReadBudget();x.value._native_context(x.record,budget)
    second=deepcopy(x.record);second['request']['operation_id']='another_review_parent'
    second.pop('native_message_ref');second['review_ref']={**x.native_ref,'id':'another_review_parent'}
    result=x.value._native_context(second,budget)
    assert result['native_context_recovery']['parent_operation_id']=='another_review_parent'
    assert result['native_context_recovery']['expected_native_ref']==second['review_ref']
    assert budget.reads==2


def test_old_host_history_is_observational_and_does_not_grant_a_new_stop(saved):
    x=saved;x.value.native.generation='replacement_host'
    result=x.value._native_context(x.record,ReadBudget())
    assert result['native_context_recovery']['record_current'] is False
    assert x.record['status']=='UNKNOWN_OUTCOME'


def test_changed_owner_generation_omits_action_and_preserves_original_record(saved):
    x=saved;before=deepcopy(x.record)
    x.owner._write(x.owner._path(x.pid,x.sid,'state.json'),{'generation':2,'context_authority_issue':{**x.issue,'generation':2}},replace=True)
    assert x.value._native_context(x.record,ReadBudget())=={} and x.record==before


def test_invalid_manifest_or_state_is_a_metadata_gap_without_rewriting_old_history(saved):
    x=saved;before=deepcopy(x.record)
    x.owner._path(x.pid,x.sid,'state.json').write_text('{}')
    assert x.value._native_context(x.record,ReadBudget())=={} and x.record==before


def test_metadata_projection_cannot_bypass_the_existing_history_read_count_bound(saved):
    x=saved;budget=ReadBudget();budget.reads=READ_LIMIT
    assert x.value._native_context(x.record,budget)=={}
    assert budget.reads==READ_LIMIT and budget.bytes==0
