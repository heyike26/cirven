"""AUTHORED_NOT_RUN: actual immutable NativeCapabilities/Store/CAS/HTTP control.

Qualification is explicitly UNIT fixed-MCP protocol output, not original SDK,
model, Windows or general safety. Promotion/rollback start no external process.
"""
import asyncio
from copy import deepcopy
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from packages.agent_sessions.experience_activation import (ExperienceActivation,ExperiencePromotePrepare,
    ExperiencePromote,ExperienceRollbackPrepare,ExperienceRollback)
from packages.agent_sessions.experience_tool_validation import ExperienceToolValidation
from packages.domain.store import Conflict,InvalidInput,NotFound
from packages.domain.tasks import digest
from test_experience_candidates import experience
from test_experience_tool_validation import tool_candidate,protocol
from test_session_diffs import app_for


@pytest.fixture
def promotion(experience,monkeypatch):
    s=experience;c,r,b=tool_candidate(s);v=ExperienceToolValidation(s['candidates']);calls,_=protocol(monkeypatch,v)
    result=asyncio.run(v.validate(s['pid'],s['sid'],b));assert result['status']=='PASS'
    action=ExperienceActivation(s['candidates']);prepare={'candidate_ref':c['record_ref'],'review_ref':r['record_ref'],'validation_kind':'MCP_TOOL',
        'validation_operation_id':b['operation_id'],'validation_request_sha256':digest(b),'validation_result_sha256':digest(result)}
    s.update(action=action,prepare=prepare,candidate=c,review=r,validation=result,calls=calls)
    return s


def preview(s):return s['action'].prepare(s['pid'],s['sid'],s['prepare'])
def packet(s,operation='activate',**changes):return {**s['prepare'],'operation_id':operation,'promotion_review_sha256':preview(s)['promotion_review_sha256'],'promotion_confirmed':True,**changes}
def promote(s,body=None):return s['action'].promote(s['pid'],s['sid'],body or packet(s))
def rollback_prepare(s,activation,mode='DISABLE'):
    return {'activation_operation_id':activation['operation_id'],'activation_request_sha256':activation['request_sha256'],
        'expected_current_configuration_ref':activation['observation']['native_operation']['receipt']['config_ref'],'mode':mode}
def rollback_packet(s,activation,mode='DISABLE',operation='rollback'):
    body=rollback_prepare(s,activation,mode);view=s['action'].prepare_rollback(s['pid'],s['sid'],body)
    return {**body,'operation_id':operation,'rollback_review_sha256':view['rollback_review_sha256'],'rollback_confirmed':True}


def test_readonly_promotion_preview_binds_exact_review_result_and_existing_configuration(promotion):
    s=promotion;before=s['caps']._index();view=preview(s);assert view['read_only'] is True and view['model_calls']==view['processes_started']==0
    assert view['basis']['validation_result_sha256']==digest(s['validation']);assert view['previous_configuration'] is None
    assert view['promotion_review_sha256']==digest(view['basis']) and s['caps']._index()==before
    assert not s['caps']._path('experience-claims').exists()


def test_validated_candidate_promotes_through_native_immutable_save_and_closes_its_claim(promotion):
    s=promotion;r=promote(s);native=r['observation']['native_operation'];assert r['observation']['status']=='APPLIED_CURRENT'
    assert native['configuration']['enabled'] is True and native['kind']=='mcp'
    ref=native['receipt']['config_ref'];assert s['caps']._index()['mcp']['learned-tool']==ref
    assert s['caps'].experience_claim('mcp','learned-tool')['state']=='CLOSED'
    assert r['local_result']['permission_granted'] is False and len(s['calls'])==2


def test_same_activation_number_is_readonly_and_does_not_create_another_native_revision(promotion):
    s=promotion;b=packet(s);r=promote(s,b);ref=r['observation']['current_configuration_ref'];again=promote(s,b)
    assert again['observation']['current_configuration_ref']==ref
    assert len(list(s['caps']._path('mcp','learned-tool').glob('*.json')))==1
    with pytest.raises(Conflict):promote(s,{**b,'promotion_review_sha256':'f'*64})


@pytest.mark.parametrize('change',[{'validation_result_sha256':'f'*64},{'validation_request_sha256':'e'*64},{'review_ref':{'id':'another','revision':1,'sha256':'d'*64}}])
def test_wrong_validation_hash_or_review_cannot_prepare_or_enable(promotion,change):
    s=promotion
    with pytest.raises((Conflict,NotFound)):s['action'].prepare(s['pid'],s['sid'],{**s['prepare'],**change})
    assert s['caps']._index()['mcp']=={}


def test_failed_case_cannot_be_relabelled_as_a_passing_candidate(promotion):
    s=promotion;v=deepcopy(s['validation']);v['cases'][0]['result']['content'][0]['text']='not the fixed expected output';v['cases'][0]['result_sha256']=digest(v['cases'][0]['result'])
    validator=ExperienceToolValidation(s['candidates']);s['sessions']._write(validator._path(s['pid'],s['sid'],v['operation_id'],'result.json'),v,replace=True)
    with pytest.raises(Conflict):s['action'].prepare(s['pid'],s['sid'],{**s['prepare'],'validation_result_sha256':digest(v)})
    assert s['caps']._index()['mcp']=={}


def test_preview_change_preserves_new_manual_native_configuration(promotion):
    s=promotion;b=packet(s);saved=s['caps'].configure_mcp({'operation_id':'manual','id':'learned-tool','expected_revision':0,'configuration':{'type':'remote','url':'https://manual.example.invalid/mcp','enabled':False,'timeout':1000}})
    with pytest.raises(Conflict):promote(s,b)
    assert s['caps']._index()['mcp']['learned-tool']==saved['config_ref']


def test_new_activation_requires_explicit_confirmation(promotion):
    s=promotion
    with pytest.raises(InvalidInput):promote(s,packet(s,promotion_confirmed=False))
    assert s['caps']._index()['mcp']=={}


def test_disable_uses_new_immutable_native_revision_and_keeps_original_candidate(promotion):
    s=promotion;r=promote(s);b=rollback_packet(s,r);done=s['action'].rollback(s['pid'],s['sid'],b)
    native=done['observation']['native_operation'];assert native['configuration']['enabled'] is False
    assert native['receipt']['config_ref']['revision']==2
    assert s['candidates'].candidate(s['pid'],s['sid'],s['candidate']['record_ref'])['record_ref']==s['candidate']['record_ref']
    assert s['caps']._configuration('mcp',r['observation']['current_configuration_ref'])['configuration']['enabled'] is True


def test_restore_previous_configuration_restores_exact_bytes_without_moving_revision_back(promotion):
    s=promotion;prior={'type':'remote','url':'https://prior.example.invalid/mcp','enabled':False,'timeout':4000}
    old=s['caps'].configure_mcp({'operation_id':'old_manual','id':'learned-tool','expected_revision':0,'configuration':prior})
    r=promote(s);b=rollback_packet(s,r,'RESTORE_PREVIOUS');done=s['action'].rollback(s['pid'],s['sid'],b)
    assert done['observation']['native_operation']['configuration']==prior
    assert done['observation']['current_configuration_ref']['revision']==3
    assert s['caps']._configuration('mcp',old['config_ref'])['configuration']==prior


def test_newer_user_configuration_is_not_overwritten_by_old_rollback(promotion):
    s=promotion;r=promote(s);b=rollback_packet(s,r)
    latest=s['caps'].configure_mcp({'operation_id':'later_manual','id':'learned-tool','expected_revision':1,'configuration':{'type':'remote','url':'https://newer.example.invalid/mcp','enabled':True,'timeout':2000}})
    with pytest.raises(Conflict):s['action'].rollback(s['pid'],s['sid'],b)
    assert s['caps']._index()['mcp']['learned-tool']==latest['config_ref']


def test_restore_without_a_previous_configuration_offers_disable_instead_of_deleting(promotion):
    s=promotion;r=promote(s)
    with pytest.raises(Conflict):s['action'].prepare_rollback(s['pid'],s['sid'],rollback_prepare(s,r,'RESTORE_PREVIOUS'))
    assert s['caps']._index()['mcp']['learned-tool']==r['observation']['current_configuration_ref']


def test_unknown_local_finalization_keeps_claim_and_readonly_inspection_then_finishes_without_native_resave(promotion,monkeypatch):
    s=promotion;b=packet(s);finish=s['action']._finalize
    def lose(*_):raise OSError('UNIT local receipt unavailable')
    monkeypatch.setattr(s['action'],'_finalize',lose)
    with pytest.raises(OSError):promote(s,b)
    r=s['action'].inspect(s['pid'],s['sid'],b['operation_id'],digest(b));assert r['observation']['status']=='APPLIED_CURRENT' and r['local_finalization_needed']
    with pytest.raises(Conflict):s['caps'].set_enabled('mcp','learned-tool',{'operation_id':'unrelated','expected_revision':1,'enabled':False})
    monkeypatch.setattr(s['action'],'_finalize',finish);monkeypatch.setattr(s['caps'],'_save',lambda *_a,**_k:pytest.fail('Known receipt only needs local finalization'))
    done=s['action'].recover(s['pid'],s['sid'],{'operation_id':b['operation_id'],'request_sha256':digest(b),'mode':'FINALIZE_LOCAL_RECORD','recovery_confirmed':True})
    assert done['local_finalization_needed'] is False and s['caps'].experience_claim('mcp','learned-tool')['state']=='CLOSED'


def test_partial_native_pointer_save_can_complete_only_its_exact_already_saved_metadata(promotion,monkeypatch):
    s=promotion;b=packet(s);write=s['caps']._write;failed=False
    def fail_pointer(path,value,**kwargs):
        nonlocal failed
        if path==s['caps']._path('index.json') and not failed:
            failed=True;raise OSError('UNIT pointer write failed')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['caps'],'_write',fail_pointer)
    with pytest.raises(OSError):promote(s,b)
    r=s['action'].inspect(s['pid'],s['sid'],b['operation_id'],digest(b));assert r['observation']['status']=='SAVED_POINTER_UNCONFIRMED'
    monkeypatch.setattr(s['caps'],'_write',write)
    done=s['action'].recover(s['pid'],s['sid'],{'operation_id':b['operation_id'],'request_sha256':digest(b),'mode':'COMPLETE_EXACT_SAVED_METADATA','recovery_confirmed':True})
    assert done['observation']['status']=='APPLIED_CURRENT' and done['observation']['current_configuration_ref']['revision']==1


def test_an_unproven_native_save_is_not_replayed_even_by_explicit_complete_request(promotion,monkeypatch):
    s=promotion;b=packet(s)
    def stop_before_save(*_a,**_k):raise OSError('UNIT before native save')
    monkeypatch.setattr(s['caps'],'_save',stop_before_save)
    with pytest.raises(OSError):promote(s,b)
    r=s['action'].inspect(s['pid'],s['sid'],b['operation_id'],digest(b));assert r['observation']['status']=='UNKNOWN_NO_SAVED_NATIVE_RECEIPT'
    with pytest.raises(Conflict):s['action'].recover(s['pid'],s['sid'],{'operation_id':b['operation_id'],'request_sha256':digest(b),'mode':'COMPLETE_EXACT_SAVED_METADATA','recovery_confirmed':True})
    assert s['caps']._index()['mcp']=={}


def test_another_source_nonce_cannot_replace_pending_capability_claim(promotion,monkeypatch):
    s=promotion;b=packet(s);monkeypatch.setattr(s['caps'],'_save',lambda *_a,**_k:(_ for _ in ()).throw(OSError('UNIT pending')))
    with pytest.raises(OSError):promote(s,b)
    with pytest.raises(Conflict):promote(s,packet(s,'another_activation'))
    assert s['caps'].experience_claim('mcp','LEARNED-TOOL')['operation_id']==b['operation_id']


def test_corrupt_claim_cannot_be_treated_as_empty_or_replaced(promotion):
    s=promotion;path=s['action']._claim_path('mcp','learned-tool');s['caps']._write(path,{'state':'CLOSED'});before=path.read_bytes()
    with pytest.raises(Conflict):promote(s)
    assert path.read_bytes()==before and s['caps']._index()['mcp']=={}


def test_repeated_historical_finalize_does_not_clear_a_newer_claim(promotion):
    s=promotion;first=promote(s);original_claim=s['caps'].experience_claim('mcp','learned-tool');path=s['action']._claim_path('mcp','learned-tool')
    newer={**original_claim,'state':'ACTIVE','operation_id':'newer','native_operation_id':'newer_native'};newer.pop('closed_at',None);s['caps']._write(path,newer,replace=True)
    done=s['action'].recover(s['pid'],s['sid'],{'operation_id':first['operation_id'],'request_sha256':first['request_sha256'],'mode':'FINALIZE_LOCAL_RECORD','recovery_confirmed':True})
    assert s['caps'].experience_claim('mcp','learned-tool')==newer


def test_existing_native_save_without_experience_claim_keeps_its_original_operation_protocol(experience):
    s=experience;r=s['caps'].configure_mcp({'operation_id':'ordinary','id':'ordinary','expected_revision':0,'configuration':{'type':'remote','url':'https://ordinary.example.invalid/mcp','enabled':False,'timeout':1000}})
    assert s['caps'].operation('ordinary')['receipt']==r


def test_http_promotion_and_disable_return_native_metadata_only(promotion):
    s=promotion;client=TestClient(app_for(s['sessions']));root=f"/api/projects/{s['pid']}/agent-sessions/{s['sid']}/experience";b=packet(s)
    p=client.post(root+'/promotion',json=b);assert p.status_code==200;r=p.json();assert r['processes_started']==r['model_calls']==0
    back=rollback_packet(s,r);d=client.post(root+'/rollback',json=back);assert d.status_code==200
    assert d.json()['observation']['native_operation']['configuration']['enabled'] is False
