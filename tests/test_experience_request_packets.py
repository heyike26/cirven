"""AUTHORED_NOT_RUN: exact read-only packet and historical control observation.

Real local Store metadata is inherited; original source ACK/fixed MCP replies
remain explicit UNIT boundaries. No product/native/model qualification.
"""
from copy import deepcopy
import hashlib,json
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from packages.agent_sessions.experience_requests import ACTION_MODELS,prepare_experience_request
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import digest
from test_experience_candidates import experience,proposal
from test_experience_activation import promotion
from test_experience_source_policy import policy,decide,decision
from test_experience_pending import pending,control,apply,pause,complete,resume,state
from test_session_diffs import app_for


def files(s):return {str(p.relative_to(s['data'])):hashlib.sha256(p.read_bytes()).hexdigest() for p in s['data'].rglob('*') if p.is_file()}
def prepared(s,action,request):return prepare_experience_request(s['candidates'],s['pid'],s['sid'],{'action':action,'request':request})


@pytest.mark.parametrize('number',[1e-7,1e20,0.00000123])
def test_server_owned_request_digest_covers_full_numeric_review_without_javascript_rehash(promotion,number):
    s=promotion;request=deepcopy(s['review']['request']);request['operation_id']='review_numeric'
    request['cases'][0]['input']={'number':number,'unicode_key_𝄞':{'read':True}}
    value=prepared(s,'REVIEW',request)
    assert value['request']==request and value['request_sha256']==digest(request)
    assert value['read_only'] and value['metadata_writes']==value['model_calls']==value['processes_started']==0


def test_preparation_does_not_freeze_sources_create_candidate_or_call_native_service(experience,monkeypatch):
    s=experience;before=files(s)
    monkeypatch.setattr(s['candidates'],'propose',lambda *_:pytest.fail('No candidate registration'))
    monkeypatch.setattr(s['caps'],'_save',lambda *_a,**_k:pytest.fail('No native save'))
    monkeypatch.setattr(s['candidates'],'_quotes',lambda *_:pytest.fail('No quote/body reads'))
    value=prepared(s,'PROPOSE',proposal(s));assert value['permission_granted'] is False and files(s)==before


def test_preparation_rejects_unknown_action_and_extra_request_fields_without_writes(experience):
    s=experience;before=files(s)
    with pytest.raises(ValidationError):prepared(s,'RUN_COMMAND',proposal(s))
    with pytest.raises(ValidationError):prepared(s,'PROPOSE',{**proposal(s),'auto_enable':True})
    assert files(s)==before


def test_all_nine_mutating_request_models_share_one_normalization_registry():
    assert set(ACTION_MODELS)=={'PROPOSE','REVIEW','TOOL_VALIDATE','SKILL_VALIDATE','PROMOTE','ROLLBACK','SOURCE_DECISION','PENDING_CONTROL','RECOVER_CONFIGURATION'}
    assert all('operation_id' in model.model_fields for model in ACTION_MODELS.values())


def test_missing_original_owner_is_not_created_by_request_preparation(experience):
    s=experience;before=files(s)
    with pytest.raises(NotFound):prepare_experience_request(s['candidates'],s['pid'],'a'*32,{'action':'PROPOSE','request':proposal(s)})
    assert files(s)==before


def test_exact_source_decision_lookup_after_forget_withholds_statement_and_original_body(promotion,monkeypatch):
    s=promotion;body=decision(s,'FORGET');decide(s,body);before=files(s)
    monkeypatch.setattr(policy(s).__class__,'decision',lambda *_:pytest.fail('Lookup is not decision replay'))
    value=policy(s).inspect_decision(s['pid'],s['sid'],body['candidate_ref'],body['operation_id'],digest(body))
    assert value['decision_state']=='FORGOTTEN' and value['current']['forgotten'] is True
    assert value['content_withheld'] is True and 'statement' not in json.dumps(value) and 'request' not in value
    assert files(s)==before


def test_old_source_decision_lookup_never_substitutes_newer_restore(promotion):
    s=promotion;body=decision(s);decide(s,body);decide(s,decision(s,'RESTORE','restore',1))
    value=policy(s).inspect_decision(s['pid'],s['sid'],body['candidate_ref'],body['operation_id'],digest(body))
    assert value['decision_state']=='WITHDRAWN' and value['decision_ref']['revision']==1
    assert value['current']['availability']=='ACTIVE' and value['current']['revision']==2


def test_interrupted_negative_source_history_can_be_inspected_without_finalization(promotion,monkeypatch):
    s=promotion;body=decision(s);write=s['sessions']._write
    def fail(path,value,**kwargs):
        if 'source-policy' in path.parts and 'records' in path.parts:raise OSError('UNIT history write interrupted')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['sessions'],'_write',fail)
    with pytest.raises(OSError):decide(s,body)
    before=files(s);value=policy(s).inspect_decision(s['pid'],s['sid'],body['candidate_ref'],body['operation_id'],digest(body))
    assert value['local_finalization_needed'] and value['decision_state']=='WITHDRAWN' and files(s)==before


def test_known_negative_source_receipt_remains_readable_if_authority_sidecar_is_corrupt(promotion):
    s=promotion;body=decision(s,'FORGET');decide(s,body);path=policy(s)._authority_path(s['pid'],s['sid'],body['candidate_ref'])
    s['sessions']._write(path,{'invalid':'UNIT source authority corruption'},replace=True)
    value=policy(s).inspect_decision(s['pid'],s['sid'],body['candidate_ref'],body['operation_id'],digest(body))
    assert value['decision_state']=='FORGOTTEN' and value['current']['availability']=='SOURCE_METADATA_UNAVAILABLE'
    assert value['current']['forgotten'] is True


def test_missing_source_decision_and_wrong_exact_request_hash_never_replay_or_write(promotion):
    s=promotion;body=decision(s);before=files(s)
    with pytest.raises(NotFound):policy(s).inspect_decision(s['pid'],s['sid'],body['candidate_ref'],body['operation_id'],digest(body))
    assert files(s)==before
    decide(s,body);before=files(s)
    with pytest.raises(Conflict):policy(s).inspect_decision(s['pid'],s['sid'],body['candidate_ref'],body['operation_id'],'a'*64)
    assert files(s)==before


def test_exact_old_pause_lookup_reports_original_negative_decision_and_current_resume_separately(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s);resume(s,saved['observation']['current_configuration_ref']);before=files(s);body=control(s)
    value=s['pending_control'].inspect_control(s['pid'],s['sid'],'activate',digest(s['pending_packet']),body['operation_id'],digest(body),'PAUSE')
    assert value['decision_state']=='PAUSED' and value['decision_ref']['revision']==1
    assert value['capability_control']['effective_state']=='ACTIVE' and value['capability_control']['revision']==2
    assert value['configuration_finalization_needed'] is False and files(s)==before


def test_paused_completion_packet_is_not_misreported_as_a_completed_native_save(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);monkeypatch.setattr(s['caps'],'_save',lambda *_a,**_k:(_ for _ in ()).throw(OSError('UNIT native save not reached')))
    with pytest.raises(OSError):complete(s)
    body=control(s,'COMPLETE_PAUSED_METADATA','complete',1);before=files(s)
    value=s['pending_control'].inspect_control(s['pid'],s['sid'],'activate',digest(s['pending_packet']),body['operation_id'],digest(body),body['mode'])
    assert value['control_record_kind']=='COMPLETION_PACKET_RESERVED_ONLY' and value['decision_state'] is None
    assert value['observation']['status']=='UNKNOWN_NO_SAVED_NATIVE_RECEIPT' and value['configuration_finalization_needed'] is True
    assert value['capability_control']['effective_state']=='PAUSED' and files(s)==before


def test_interrupted_positive_control_lookup_does_not_finish_or_open_it(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);saved=complete(s);write=s['caps']._write
    def fail(path,value,**kwargs):
        if 'records' in path.parts and value.get('state')=='ACTIVE':raise OSError('UNIT resume history not written')
        return write(path,value,**kwargs)
    monkeypatch.setattr(s['caps'],'_write',fail)
    with pytest.raises(OSError):resume(s,saved['observation']['current_configuration_ref'])
    body=control(s,'RESUME_PAUSED','resume',1,expected_current_configuration_ref=saved['observation']['current_configuration_ref']);before=files(s)
    value=s['pending_control'].inspect_control(s['pid'],s['sid'],'activate',digest(s['pending_packet']),'resume',digest(body),body['mode'])
    assert value['local_finalization_needed'] and value['decision_state']=='ACTIVE'
    assert value['capability_control']['effective_state']=='PAUSED' and files(s)==before


def test_wrong_control_nonce_hash_or_mode_is_not_treated_as_current_control(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);pause(s);body=control(s);before=files(s)
    with pytest.raises(Conflict):s['pending_control'].inspect_control(s['pid'],s['sid'],'activate',digest(s['pending_packet']),'pause','b'*64,'PAUSE')
    with pytest.raises(Conflict):s['pending_control'].inspect_control(s['pid'],s['sid'],'activate',digest(s['pending_packet']),'pause',digest(body),'RESUME_PAUSED')
    with pytest.raises(NotFound):s['pending_control'].inspect_control(s['pid'],s['sid'],'activate',digest(s['pending_packet']),'missing',digest(body),'PAUSE')
    assert files(s)==before


def test_http_prepare_and_exact_control_reads_are_readonly_and_keep_original_identity(promotion,monkeypatch):
    s=pending(promotion,monkeypatch);client=TestClient(app_for(s['sessions']));root=f"/api/projects/{s['pid']}/agent-sessions/{s['sid']}/experience";before=files(s);body=control(s)
    result=client.post(root+'/requests/prepare',json={'action':'PENDING_CONTROL','request':body})
    assert result.status_code==200 and result.json()['request_sha256']==digest(body) and files(s)==before
    pause(s);before=files(s)
    result=client.get(root+'/pending-configuration/activate/controls/pause',params={'activation_request_sha256':digest(s['pending_packet']),'request_sha256':digest(body),'mode':'PAUSE'})
    assert result.status_code==200 and result.json()['operation_id']=='pause' and result.json()['decision_state']=='PAUSED' and files(s)==before
