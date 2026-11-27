"""AUTHORED_NOT_RUN: real source policy/context SQL/native revision metadata.

Original task ACK and fixed MCP replies are explicit UNIT boundaries inherited
from the source fixtures. No runtime/native/semantic qualification is claimed.
"""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from packages.agent_sessions.experience_source_policy import ExperienceSourcePolicy
from packages.agent_sessions.experience_candidates import ExperienceCandidates
from packages.agent_sessions.work_requirements import WorkRequirementScope,_root
from packages.domain.context_graph import (ContextGraph,ScopeChange,SourceRef,AssociationChange,SelectionInput,ForgetInput)
from packages.domain.experience_provenance import read as provenance
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from apps.api.context_graph import ApplicationContextSources
from test_experience_candidates import experience,proposal,propose
from test_experience_activation import promotion,promote,rollback_packet


def decision(s,action='WITHDRAW',operation='source_decision',revision=0,ref=None):
    return {'operation_id':operation,'candidate_ref':ref or s['candidate']['record_ref'],'expected_revision':revision,
            'action':action,'confirmed':True,'statement':'明确停止未来使用，保留原文件和历史证据。'}
def policy(s):return ExperienceSourcePolicy(s['candidates'])
def decide(s,body):return policy(s).decision(s['pid'],s['sid'],body)


def test_empty_registered_original_authority_is_active_without_source_body_or_sdk_reads(experience,monkeypatch):
    s=experience;c=propose(s);monkeypatch.setattr(s['sessions'].workspaces,'get',lambda *_:pytest.fail('No live workspace read'))
    monkeypatch.setattr(s['sessions'].workspaces,'_version',lambda *_a,**_k:pytest.fail('No saved file body read'))
    result=policy(s).status(s['pid'],s['sid'],c['record_ref']);assert result['availability']=='ACTIVE'
    assert result['original_files_deleted'] is False and result['physical_erasure_claimed'] is False


def test_explicit_withdrawal_blocks_new_promotions_but_keeps_old_native_snapshot(promotion):
    s=promotion;active=promote(s);before=s['caps']._configuration('mcp',active['observation']['current_configuration_ref'])
    r=decide(s,decision(s));assert r['current']['availability']=='WITHDRAWN'
    with pytest.raises(Conflict):s['action'].prepare(s['pid'],s['sid'],s['prepare'])
    assert s['caps']._configuration('mcp',active['observation']['current_configuration_ref'])==before
    assert s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']=={}


def test_native_binding_and_tool_picker_exclude_withdrawn_source_without_automatic_disable_mutation(promotion):
    s=promotion;promote(s);before=s['caps']._index();old=s['caps'].native_binding_snapshot();decide(s,decision(s));new=s['caps'].native_binding_snapshot()
    assert old['sha256']!=new['sha256'] and new['value']['native_config_fragment']['mcp']=={}
    assert s['caps']._index()==before
    directory=s['caps'].tool_catalog();server=next(row for row in directory['mcp_servers'] if row['id']=='learned-tool')
    assert server['availability']=='DISABLED' and server['tools']==[] and server['experience_source']['availability']=='WITHDRAWN'


def test_source_restore_requires_exact_revision_and_never_reuses_prewithdrawal_binding_hash(promotion):
    s=promotion;promote(s);original=s['caps'].native_binding_snapshot()['sha256'];decide(s,decision(s))
    with pytest.raises(Conflict):decide(s,decision(s,'RESTORE','wrong_basis',0))
    result=decide(s,decision(s,'RESTORE','explicit_restore',1));assert result['current']['availability']=='ACTIVE'
    assert s['caps'].native_binding_snapshot()['sha256']!=original


def test_forgotten_candidate_is_irreversible_and_public_content_is_withheld(promotion):
    s=promotion;promote(s);r=decide(s,decision(s,'FORGET'));assert r['current']['forgotten'] is True
    with pytest.raises(Conflict):decide(s,decision(s,'RESTORE','restore_forgotten',1))
    raw=s['candidates'].candidate(s['pid'],s['sid'],s['candidate']['record_ref']);shown=policy(s).public(s['pid'],s['sid'],raw)
    assert shown['content_withheld'] is True and 'request' not in shown and 'configuration' not in shown and 'sources' not in shown
    assert s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']=={}


def test_disabling_forgotten_candidate_preserves_provenance_and_cannot_be_reenabled(promotion):
    s=promotion;active=promote(s);decide(s,decision(s,'FORGET'))
    b=rollback_packet(s,active);disabled=s['action'].rollback(s['pid'],s['sid'],b);ref=disabled['observation']['current_configuration_ref']
    origin=provenance(s['caps'],'mcp',ref);assert origin['source']['candidate_ref']==s['candidate']['record_ref']
    with pytest.raises(Conflict):s['caps'].set_enabled('mcp','learned-tool',{'operation_id':'bypass_forget','expected_revision':ref['revision'],'enabled':True})
    assert s['caps']._index()['mcp']['learned-tool']==ref


def test_disabled_unused_experience_does_not_change_runtime_binding_on_withdrawal(promotion):
    s=promotion;active=promote(s);s['action'].rollback(s['pid'],s['sid'],rollback_packet(s,active));before=s['caps'].native_binding_snapshot()['sha256']
    decide(s,decision(s));assert s['caps'].native_binding_snapshot()['sha256']==before


def test_known_source_decision_nonce_does_not_reapply_old_state_over_newer_restore(promotion):
    s=promotion;b=decision(s);first=decide(s,b);decide(s,decision(s,'RESTORE','restore_once',1));again=decide(s,b)
    assert again['decision']==first['decision'] and again['current']['availability']=='ACTIVE' and again['current']['revision']==2
    with pytest.raises(Conflict):decide(s,{**b,'statement':'changed original decision'})


def test_negative_policy_commits_before_auxiliary_history_failure_and_exact_nonce_repairs_it(promotion,monkeypatch):
    s=promotion;b=decision(s,'FORGET');write=s['sessions']._write;failed=False
    def fail_auxiliary(path,value,**kwargs):
        nonlocal failed
        if 'source-policy' in str(path) and '/records/' in str(path) and not failed:
            failed=True;raise OSError('UNIT history write unavailable')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['sessions'],'_write',fail_auxiliary)
    with pytest.raises(OSError):decide(s,b)
    state=policy(s).status(s['pid'],s['sid'],s['candidate']['record_ref']);assert state['forgotten'] and state['pending_finalization']
    monkeypatch.setattr(s['sessions'],'_write',write);r=decide(s,b);assert r['current']['forgotten'] and r['current']['pending_finalization'] is False


def test_pending_positive_restore_stays_blocked_until_its_exact_history_is_finished(promotion,monkeypatch):
    s=promotion;decide(s,decision(s));b=decision(s,'RESTORE','restore_pending',1);write=s['sessions']._write
    def fail_auxiliary(path,value,**kwargs):
        if 'source-policy' in str(path) and '/records/' in str(path):raise OSError('UNIT restore journal unavailable')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['sessions'],'_write',fail_auxiliary)
    with pytest.raises(OSError):decide(s,b)
    assert policy(s).status(s['pid'],s['sid'],s['candidate']['record_ref'])['availability']=='SOURCE_DECISION_PENDING'
    monkeypatch.setattr(s['sessions'],'_write',write);assert decide(s,b)['current']['availability']=='ACTIVE'


def test_tampered_thin_authority_cannot_override_its_indexed_proof_hash(promotion):
    s=promotion;p=policy(s);path=p._authority_path(s['pid'],s['sid'],s['candidate']['record_ref']);value=s['candidates']._read(path)
    value['proof']['origin_ids']=['f'*64];value['proof_sha256']=digest(value['proof']);s['sessions']._write(path,value,replace=True)
    with pytest.raises(Conflict):p.status(s['pid'],s['sid'],s['candidate']['record_ref'])
    assert s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']=={}


def test_policy_head_cannot_claim_active_when_current_immutable_decision_says_forgotten(promotion):
    s=promotion;p=policy(s);decide(s,decision(s,'FORGET'));path=p._path(s['pid'],s['sid'],s['candidate']['record_ref'],'head.json');head=s['candidates']._read(path);head['state']='ACTIVE';s['sessions']._write(path,head,replace=True)
    with pytest.raises(Conflict):p.status(s['pid'],s['sid'],s['candidate']['record_ref'])


def contextual_source(s):
    adapter=ApplicationContextSources(SimpleNamespace(state=SimpleNamespace(store=s['store'])))
    graph=ContextGraph(s['store'],adapter.read,lambda _: {'project_id':s['pid']})
    graph.save_scope('experience_scope',ScopeChange(operation_id='experience_scope',expected_revision=0,kind='PROJECT',project_id=s['pid'],label='Source task'))
    doc=s['store'].save_document(s['pid'],'original source.md',b'Original human requirement; preserve user edits.','UPLOAD')
    ref=SourceRef(kind='DOCUMENT',project_id=s['pid'],object_id=doc['id'],revision=doc['revision'],sha256=doc['sha256'])
    edge=graph.associate(AssociationChange(operation_id='associate_source',scope_id='experience_scope',source=ref,expected_revision=0,state='ACTIVE'))
    selected=graph.select(SelectionInput(operation_id='select_source',scope_id='experience_scope'))
    s['sessions'].context_authority.graph=graph;manifest,_=s['sessions']._manifest(s['pid'],s['sid'])
    s['sessions'].context_authority.track(manifest,{'session_id':'ses_original'},selection_id=selected['selection_id'],project_id=s['pid'],conversation_id=None)
    return graph,doc,edge


def test_existing_context_forget_blocks_derived_candidate_and_keeps_original_document(experience):
    s=experience;graph,doc,edge=contextual_source(s);c=propose(s)
    graph.forget(ForgetInput(operation_id='forget_original_context',origin_id=edge['origin_id'],scope_id='experience_scope'))
    state=policy(s).status(s['pid'],s['sid'],c['record_ref']);assert state['forgotten'] and state['availability']=='FORGOTTEN'
    assert s['store'].document(s['pid'],doc['id'])['raw_bytes']==b'Original human requirement; preserve user edits.'


def test_later_context_scope_withdrawal_invalidates_source_without_source_body_rereads(experience,monkeypatch):
    s=experience;graph,doc,edge=contextual_source(s);c=propose(s);source=SourceRef(kind='DOCUMENT',project_id=s['pid'],object_id=doc['id'],revision=doc['revision'],sha256=doc['sha256'])
    graph.associate(AssociationChange(operation_id='exclude_original_context',scope_id='experience_scope',source=source,expected_revision=1,state='EXCLUDED'))
    monkeypatch.setattr(graph,'source_reader',lambda *_:pytest.fail('Authority check is metadata only'))
    assert policy(s).status(s['pid'],s['sid'],c['record_ref'])['availability']=='SOURCE_WITHDRAWN_OR_UNAVAILABLE'


def test_unrelated_graph_epoch_change_does_not_invalidate_frozen_source(promotion):
    s=promotion;before=policy(s).status(s['pid'],s['sid'],s['candidate']['record_ref'])
    graph=s['sessions'].context_authority.graph
    graph.save_scope('unrelated_scope',ScopeChange(operation_id='unrelated_change',expected_revision=0,kind='PROJECT',project_id=s['pid'],label='Unrelated scope'))
    assert policy(s).status(s['pid'],s['sid'],s['candidate']['record_ref'])==before


def test_native_probe_cannot_start_withdrawn_derived_tool(promotion):
    s=promotion;active=promote(s);decide(s,decision(s))
    with pytest.raises(Conflict):s['caps']._current_mcp(active['observation']['current_configuration_ref'])


def test_public_validation_result_has_opaque_raw_result_hash_for_float_safe_promotion(promotion):
    s=promotion;shown=policy(s).public(s['pid'],s['sid'],s['validation'])
    assert shown['result_sha256']==digest(s['validation']) and 'source_policy' in shown


def test_public_rollback_preview_withholds_derived_configuration_after_forget(promotion):
    s=promotion;active=promote(s);decide(s,decision(s,'FORGET'))
    from test_experience_activation import rollback_prepare
    raw=s['action'].prepare_rollback(s['pid'],s['sid'],rollback_prepare(s,active));shown=policy(s).public(s['pid'],s['sid'],raw)
    assert shown['content_withheld'] and 'proposed_configuration' not in shown and 'previous_configuration' not in shown
    assert shown['rollback_review_sha256']==raw['rollback_review_sha256']


def test_derived_candidate_inherits_prior_capability_source_withdrawal_and_forgetting(promotion):
    s=promotion;promote(s);sessions=s['sessions'];sid='d'*32;manifest={'id':sid,'project_id':s['pid'],
        'request':{'workspace_ref':s['scope']['workspace_ref']},'capability_configuration_refs':s['caps']._index()}
    sessions._write(sessions._path(s['pid'],sid,'manifest.json'),manifest);ref={'id':sid,'revision':1,'sha256':digest(manifest)}
    sessions._write(sessions._path(s['pid'],sid,'state.json'),{'generation':1,'owner_status':'DISCONNECTED'})
    request={'session_ref':ref,'generation':1,'raw_text':'Explicit subsequent task using the prior capability'}
    sessions._claim(s['pid'],sid,'descendant_work','MESSAGE',request)
    sessions._finish(s['pid'],sid,'descendant_work','UNKNOWN_OUTCOME',generation=1,original_session_id='ses_descendant',native_message_id='msg_descendant')
    sessions.context_authority.begin(manifest,{'session_id':'ses_descendant'},loaded_existing=False)
    scope={'session_ref':ref,'generation':1,'workspace_ref':s['scope']['workspace_ref'],'root_operation_id':'descendant_work'}
    root=_root(sessions,s['pid'],sid,WorkRequirementScope(**scope))
    other={**s,'sid':sid,'scope':scope,'root':root,'candidates':ExperienceCandidates(sessions)}
    child=propose(other,proposal(other,'descendant_candidate'))
    decide(s,decision(s));state=policy(other).status(s['pid'],sid,child['record_ref']);assert state['availability']=='SOURCE_WITHDRAWN_OR_UNAVAILABLE'
    decide(s,decision(s,'FORGET','forget_parent',1));state=policy(other).status(s['pid'],sid,child['record_ref']);assert state['forgotten'] is True


def test_missing_source_authority_is_not_treated_as_an_active_legacy_grant(promotion):
    s=promotion;path=policy(s)._authority_path(s['pid'],s['sid'],s['candidate']['record_ref']);path.unlink()
    assert policy(s).status(s['pid'],s['sid'],s['candidate']['record_ref'])['availability']=='LEGACY_SOURCE_AUTHORITY_UNVERIFIED'
    with pytest.raises(Conflict):s['action'].prepare(s['pid'],s['sid'],s['prepare'])


def test_public_original_operation_and_saved_validation_withhold_content_after_forget(promotion):
    s=promotion;active=promote(s);decide(s,decision(s,'FORGET'))
    shown=policy(s).public(s['pid'],s['sid'],s['action'].inspect(s['pid'],s['sid'],active['operation_id'],active['request_sha256']))
    assert shown['content_withheld'] and 'native_operation' not in shown.get('observation',{})
    shown=policy(s).public(s['pid'],s['sid'],s['validation']);assert shown['content_withheld'] and 'cases' not in shown
