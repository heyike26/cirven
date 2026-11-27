"""AUTHORED_NOT_RUN: actual local metadata/Store controls, UNIT MCP fixture.

No real model, SDK owner, Tool execution, Windows or product qualification.
"""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from packages.agent_sessions.experience_pending import ExperiencePending
from packages.domain import experience_controls as controls
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_experience_candidates import experience
from test_experience_activation import promotion,packet,rollback_packet
from test_experience_source_policy import decision,decide,policy
from test_session_diffs import app_for


def pending(s,monkeypatch,*,partial=False):
    body=packet(s);write=s['caps']._write;save=s['caps']._save
    if partial:
        def fail(path,value,**kwargs):
            if path==s['caps']._path('index.json'):raise OSError('UNIT saved native operation, pointer unknown')
            return write(path,value,**kwargs)
        monkeypatch.setattr(s['caps'],'_write',fail)
    else:
        monkeypatch.setattr(s['caps'],'_save',lambda *_a,**_k:(_ for _ in ()).throw(OSError('UNIT interrupted before metadata save')))
    with pytest.raises(OSError):s['action'].promote(s['pid'],s['sid'],body)
    monkeypatch.setattr(s['caps'],'_write',write);monkeypatch.setattr(s['caps'],'_save',save)
    s['pending_packet']=body;s['pending_control']=ExperiencePending(s['candidates']);return s


def control(s,mode='PAUSE',operation='pause',revision=0,**changes):
    return {'operation_id':operation,'activation_operation_id':s['pending_packet']['operation_id'],
        'activation_request_sha256':digest(s['pending_packet']),'expected_control_revision':revision,
        'mode':mode,'expected_current_configuration_ref':None,'confirmed':True,**changes}
def apply(s,body):return s['pending_control'].change(s['pid'],s['sid'],body)
def state(s):return controls.view(s['caps'],'mcp','learned-tool')
def pause(s):return apply(s,control(s))
def complete(s):return apply(s,control(s,'COMPLETE_PAUSED_METADATA','complete',1))
def resume(s,ref):return apply(s,control(s,'RESUME_PAUSED','resume',1,expected_current_configuration_ref=ref))


def test_pause_is_negative_and_does_not_save_or_execute_the_pending_configuration(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);before=s['caps']._index();calls=len(s['calls']);view=pause(s)
    assert view['capability_control']['effective_state']=='PAUSED' and s['caps']._index()==before
    assert view['observation']['status']=='UNKNOWN_NO_SAVED_NATIVE_RECEIPT' and len(s['calls'])==calls


def test_late_original_native_save_and_old_positive_recovery_cannot_bypass_pause(promotion,monkeypatch):
    s=pending(promotion,monkeypatch,partial=True);pause(s)
    intent=s['candidates'].operation(s['pid'],s['sid'],'activate',digest(s['pending_packet']))
    with pytest.raises(Conflict):s['action']._save(s['pid'],s['sid'],intent)
    with pytest.raises(Conflict):s['action'].recover(s['pid'],s['sid'],{'operation_id':'activate','request_sha256':digest(s['pending_packet']),'mode':'COMPLETE_EXACT_SAVED_METADATA','recovery_confirmed':True})
    assert state(s)['effective_state']=='PAUSED'


@pytest.mark.parametrize('partial',[False,True])
def test_exact_completion_recovers_original_metadata_but_stays_paused_and_starts_no_tool(promotion,monkeypatch,partial):
    s=pending(promotion,monkeypatch,partial=partial);pause(s);calls=len(s['calls']);result=complete(s)
    assert result['observation']['status']=='APPLIED_CURRENT' and result['capability_control']['effective_state']=='PAUSED'
    assert s['caps'].experience_claim('mcp','learned-tool')['state']=='CLOSED' and len(s['calls'])==calls
    assert s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']=={}
    assert next(r for r in s['caps'].tool_catalog()['mcp_servers'] if r['id']=='learned-tool')['availability']=='DISABLED'


def test_resume_requires_completed_metadata_and_current_exact_configuration(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s)
    with pytest.raises(Conflict):resume(s,{'id':'learned-tool','revision':1,'sha256':'a'*64})
    saved=complete(s);ref=saved['observation']['current_configuration_ref']
    with pytest.raises(Conflict):resume(s,{**ref,'sha256':'b'*64})
    result=resume(s,ref);assert result['capability_control']['effective_state']=='ACTIVE'
    assert 'learned-tool' in s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']


def test_retained_control_revision_prevents_pre_pause_binding_revival(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s);paused=s['caps'].native_binding_snapshot()['sha256']
    resume(s,saved['observation']['current_configuration_ref']);active=s['caps'].native_binding_snapshot()
    assert active['sha256']!=paused and active['value']['experience_control_states']['mcp']['learned-tool']['revision']==2


def test_source_forget_allows_inert_metadata_completion_and_disable_but_never_enabled_resume(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);decide(s,decision(s,'FORGET'));saved=complete(s)
    with pytest.raises(Conflict):resume(s,saved['observation']['current_configuration_ref'])
    disabled=s['action'].rollback(s['pid'],s['sid'],rollback_packet(s,saved));ref=disabled['observation']['current_configuration_ref']
    resumed=resume(s,ref);assert resumed['capability_control']['effective_state']=='ACTIVE'
    assert s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']=={}
    with pytest.raises(Conflict):s['caps'].set_enabled('mcp','learned-tool',{'operation_id':'bypass','expected_revision':ref['revision'],'enabled':True})


def test_negative_pause_stays_effective_after_auxiliary_history_failure(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);write=s['caps']._write
    def fail(path,value,**kwargs):
        if 'experience-controls' in path.parts and 'records' in path.parts:raise OSError('UNIT auxiliary history write failed')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['caps'],'_write',fail)
    with pytest.raises(OSError):pause(s)
    assert state(s)['effective_state']=='PAUSED' and state(s)['finalized'] is False
    monkeypatch.setattr(s['caps'],'_write',write);pause(s);assert state(s)['finalized'] is True


def test_unfinished_positive_resume_is_effectively_paused_until_exact_history_repair(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s);write=s['caps']._write
    def fail(path,value,**kwargs):
        if 'records' in path.parts and value.get('state')=='ACTIVE':raise OSError('UNIT resume history write failed')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['caps'],'_write',fail)
    with pytest.raises(OSError):resume(s,saved['observation']['current_configuration_ref'])
    assert state(s)['state']=='ACTIVE' and state(s)['effective_state']=='PAUSED'
    assert s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']=={}
    monkeypatch.setattr(s['caps'],'_write',write);resume(s,saved['observation']['current_configuration_ref']);assert state(s)['effective_state']=='ACTIVE'


def test_old_pause_nonce_never_overwrites_a_later_resume(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s);resume(s,saved['observation']['current_configuration_ref']);again=pause(s)
    assert again['capability_control']['revision']==2 and again['capability_control']['effective_state']=='ACTIVE'


def test_interrupted_resume_rechecks_later_source_forgetting_before_finalizing(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s);write=s['caps']._write
    def fail(path,value,**kwargs):
        if 'records' in path.parts and value.get('state')=='ACTIVE':raise OSError('UNIT resume interrupted')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['caps'],'_write',fail)
    with pytest.raises(OSError):resume(s,saved['observation']['current_configuration_ref'])
    monkeypatch.setattr(s['caps'],'_write',write);decide(s,decision(s,'FORGET'))
    with pytest.raises(Conflict):resume(s,saved['observation']['current_configuration_ref'])
    assert state(s)['effective_state']=='PAUSED'


def test_interrupted_resume_retains_later_manual_configuration_and_refuses_stale_finalize(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s);ref=saved['observation']['current_configuration_ref'];write=s['caps']._write
    def fail(path,value,**kwargs):
        if 'records' in path.parts and value.get('state')=='ACTIVE':raise OSError('UNIT resume interrupted')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['caps'],'_write',fail)
    with pytest.raises(OSError):resume(s,ref)
    monkeypatch.setattr(s['caps'],'_write',write)
    latest=s['caps'].set_enabled('mcp','learned-tool',{'operation_id':'manual_disable','expected_revision':ref['revision'],'enabled':False})['config_ref']
    with pytest.raises(Conflict):resume(s,ref)
    assert s['caps']._index()['mcp']['learned-tool']==latest and state(s)['effective_state']=='PAUSED'
    apply(s,control(s,'PAUSE','retain_negative_pause',2))
    apply(s,control(s,'RESUME_PAUSED','resume_disabled_latest',3,expected_current_configuration_ref=latest))
    assert state(s)['effective_state']=='ACTIVE' and s['caps'].native_binding_snapshot()['value']['native_config_fragment']['mcp']=={}


def test_same_pause_nonce_with_changed_input_conflicts(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s)
    with pytest.raises(Conflict):apply(s,control(s,expected_current_configuration_ref={'id':'learned-tool','revision':1,'sha256':'a'*64}))


def test_each_pause_binds_one_exact_local_completion_input(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);complete(s)
    with pytest.raises(Conflict):apply(s,control(s,'COMPLETE_PAUSED_METADATA','second_complete',1))
    assert state(s)['effective_state']=='PAUSED'


def test_paused_capability_cannot_be_probed_or_selected(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s);ref=saved['observation']['current_configuration_ref']
    with pytest.raises(Conflict):s['caps']._current_mcp(ref)
    row=next(r for r in s['caps'].get()['mcp'] if r['id']=='learned-tool')
    assert row['enabled'] is True and row['effective_enabled'] is False and row['probe_permitted'] is False


def test_forget_public_projection_retains_safe_pause_controls_and_withholds_source_body(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);decide(s,decision(s,'FORGET'))
    value=s['pending_control'].inspect(s['pid'],s['sid'],'activate',digest(s['pending_packet']));shown=policy(s).public(s['pid'],s['sid'],value)
    assert shown['content_withheld'] and shown['capability_control']['effective_state']=='PAUSED'
    assert 'configuration' not in shown and 'local_result' not in shown and 'native_operation' not in shown['observation']


def test_stale_control_revision_or_other_original_operation_cannot_resume(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s)
    with pytest.raises(Conflict):apply(s,control(s,'RESUME_PAUSED','stale',0,expected_current_configuration_ref=saved['observation']['current_configuration_ref']))
    with pytest.raises(Conflict):apply(s,control(s,'PAUSE','second_pause',1))


def test_pending_http_routes_share_same_authority_and_return_readonly_control(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);client=TestClient(app_for(s['sessions']));root=f"/api/projects/{s['pid']}/agent-sessions/{s['sid']}/experience/pending-configuration"
    r=client.post(root,json=control(s));assert r.status_code==200 and r.json()['capability_control']['effective_state']=='PAUSED'
    r=client.get(root+'/activate',params={'request_sha256':digest(s['pending_packet'])})
    assert r.status_code==200 and r.json()['read_only'] is True and r.json()['model_calls']==0
