"""AUTHORED_NOT_RUN: durable allowance CAS, no usage reset or SDK execution."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.domain.task_resource_budget import TaskResourceBudget,TaskResourceLimit,MAX_ADJUSTMENTS
from packages.agent_sessions.task_resources import for_operation
from packages.agent_sessions import resource_allowance as service
from test_task_resource_budget import resource,another
from test_experience_candidates import experience
from test_session_diffs import app_for


def update(b,limits,key='adjust',reference=None):
    reference=reference or b.inspect()['resource_ref'];request={'operation_id':key,'expected_resource_ref':reference,'limits':limits,'note':''}
    return b.adjust(key,digest(request),reference,limits)


def test_adjustment_changes_effective_limits_across_reopen_without_rewriting_allocation_or_usage(resource):
    s=resource;b=s['budget'];b.consume(model_attempts=1,source_bytes=5,source_reads=1);marker=b.marker.read_bytes();before=b.inspect()['used']
    changed=update(b,{**s['limits'],'model_attempts':5,'source_bytes':80})
    assert changed['status']=='APPLIED' and not changed['usage_refunded'] and not changed['unknown_reservations_released']
    fresh=another(s);view=fresh.inspect()
    assert view['limits']['model_attempts']==5 and view['allocation_limits']==s['limits'] and view['used']==before and b.marker.read_bytes()==marker
    fresh.consume(model_attempts=4)
    with pytest.raises(TaskResourceLimit):another(s).consume(model_attempts=1)


def test_stale_snapshot_is_a_durable_rejection_without_changing_effective_limits(resource):
    s=resource;b=s['budget'];stale=b.inspect()['resource_ref'];b.consume(model_attempts=1)
    result=update(b,{**s['limits'],'model_attempts':5},reference=stale)
    assert result['status']=='REJECTED' and result['reason']=='RESOURCE_SNAPSHOT_CHANGED'
    before=b.path.read_bytes();lookup=another(s).adjustment('adjust')
    assert lookup['adjustment_ref']==result['adjustment_ref'] and lookup['status']=='REJECTED' and b.path.read_bytes()==before
    assert another(s).inspect()['limits']==s['limits']


def test_lower_limit_cannot_release_unknown_read_reservations_or_consumed_models(resource):
    s=resource;b=s['budget'];b.consume(model_attempts=2);ticket=b.reserve_read(40)
    result=update(b,{**s['limits'],'source_bytes':39})
    assert result['status']=='REJECTED' and result['reason']=='BELOW_CONSUMED_OR_RESERVED'
    assert b.inspect()['pending_read_bytes']==40 and b.inspect()['used']['model_attempts']==2
    result=update(b,{**s['limits'],'model_attempts':1},key='lower_models')
    assert result['status']=='REJECTED' and b.inspect()['pending_reads']==1
    b.finish_read(ticket,None)
    assert b.inspect()['used']['source_bytes']==40


def test_lower_soft_retention_threshold_preserves_already_received_output(resource):
    b=resource['budget'];b.retain_received(12);result=update(b,{**resource['limits'],'retained_output_bytes':8})
    assert result['status']=='APPLIED' and b.inspect()['used']['retained_output_bytes']==12 and b.inspect()['retention_threshold_exceeded']
    with pytest.raises(TaskResourceLimit):b.consume(model_attempts=1)
    b.retain_received(4);assert b.inspect()['used']['retained_output_bytes']==16


def test_more_allowance_never_clears_existing_accounting_gap(resource,monkeypatch):
    b=resource['budget'];original=b.consume
    monkeypatch.setattr(b,'consume',lambda **kwargs:(_ for _ in ()).throw(OSError('UNIT bookkeeping unavailable')))
    b.retain_received(8);gap=b.gap.read_bytes();monkeypatch.setattr(b,'consume',original)
    assert update(b,{**resource['limits'],'retained_output_bytes':128,'model_attempts':8})['status']=='APPLIED'
    assert b.gap.read_bytes()==gap and another(resource).inspect()['accounting_unconfirmed']
    with pytest.raises(TaskResourceLimit):another(resource).consume(model_attempts=1)


def test_saved_adjustment_lost_acknowledgement_has_one_original_receipt_and_no_reapply(resource,monkeypatch):
    b=resource['budget'];reference=b.inspect()['resource_ref'];limits={**resource['limits'],'model_attempts':5};sha=digest({'fixed':'original adjustment'});save=b._save
    def lost(value,**kwargs):save(value,**kwargs);raise OSError('UNIT saved but ACK lost')
    monkeypatch.setattr(b,'_save',lost)
    with pytest.raises(OSError):b.adjust('lost_ack',sha,reference,limits)
    current=another(resource);before=b.path.read_bytes();original=current.adjustment('lost_ack')
    assert original['status']=='APPLIED'
    again=current.adjust('lost_ack',sha,reference,limits)
    assert again['adjustment_ref']==original['adjustment_ref'] and b.path.read_bytes()==before and current.inspect()['allowance_adjustments']==1


def test_same_operation_cannot_be_rebound_even_if_an_internal_caller_reuses_a_hash(resource):
    b=resource['budget'];reference=b.inspect()['resource_ref'];limits={**resource['limits'],'model_attempts':5};sha='a'*64
    b.adjust('same',sha,reference,limits);before=b.path.read_bytes()
    with pytest.raises(Conflict):b.adjust('same',sha,reference,{**limits,'model_attempts':6})
    assert b.path.read_bytes()==before


def test_missing_operation_observation_never_authorizes_automatic_replay_or_writes(resource):
    b=resource['budget'];before=b.path.read_bytes();result=b.adjustment('not_observed')
    assert result['status']=='NOT_OBSERVED' and result['automatic_replay_allowed'] is False and result['metadata_writes']==0
    assert b.path.read_bytes()==before


def test_bounded_adjustment_history_retains_existing_records_and_refuses_new_entries(resource):
    b=resource['budget']
    for n in range(MAX_ADJUSTMENTS):assert update(b,resource['limits'],key='original_'+str(n))['status']=='APPLIED'
    before=b.path.read_bytes()
    with pytest.raises(TaskResourceLimit):update(b,resource['limits'],key='over_capacity')
    assert b.path.read_bytes()==before and b.adjustment('original_0')['status']=='APPLIED'


def test_rewritten_effective_limit_history_is_rejected_without_reset(resource):
    b=resource['budget'];update(b,{**resource['limits'],'model_attempts':5});value=b._read();value['adjustments'][0]['previous_limits']['model_attempts']=100;b._save(value);before=b.path.read_bytes()
    with pytest.raises(Conflict):another(resource).allocate()
    assert b.path.read_bytes()==before and b.marker.exists()


def test_exact_owner_change_and_original_lookup_survive_host_generation_change_without_new_models(experience):
    s=experience;o=s['sessions'];manifest,reference=o._manifest(s['pid'],s['sid']);b=for_operation(o,manifest,o.operation(s['pid'],s['sid'],s['scope']['root_operation_id']));b.allocate();before=b.inspect()['used']
    body={'operation_id':'allowance_user_change','session_ref':reference,'generation':1,'work_operation_id':s['scope']['root_operation_id'],'expected_resource_ref':b.inspect()['resource_ref'],'limits':{**b.limits,'model_attempts':256},'note':''}
    result=service.change(o,s['pid'],s['sid'],body)
    assert result['status']=='APPLIED' and result['request_sha256']==digest(body) and b.inspect()['used']==before
    o._update(s['pid'],s['sid'],generation=2)
    observed=service.lookup(o,s['pid'],s['sid'],reference['sha256'],body['work_operation_id'],body['operation_id'])
    assert observed['observed_generation']==2 and observed['request_sha256']==digest(body) and observed['adjustment_ref']==result['adjustment_ref']
    before=b.path.read_bytes()
    with pytest.raises(Conflict):service.change(o,s['pid'],s['sid'],{**body,'operation_id':'stale_generation'})
    assert b.path.read_bytes()==before


def test_public_allowance_lookup_with_missing_ledger_is_unavailable_without_allocation(experience):
    s=experience;o=s['sessions'];_,reference=o._manifest(s['pid'],s['sid'])
    result=service.lookup(o,s['pid'],s['sid'],reference['sha256'],s['scope']['root_operation_id'],'missing')
    assert result['status']=='UNAVAILABLE' and result['automatic_replay_allowed'] is False
    assert not (s['data']/'task-resource-budgets').exists()


@pytest.mark.parametrize('include_empty_note',[False,True])
def test_http_optional_note_roundtrip_preserves_one_exact_adjustment_and_usage(experience,include_empty_note):
    s=experience;owner=s['sessions'];manifest,reference=owner._manifest(s['pid'],s['sid'])
    work=s['scope']['root_operation_id'];budget=for_operation(owner,manifest,owner.operation(s['pid'],s['sid'],work));budget.allocate()
    before=budget.inspect()['used']
    body={'operation_id':'http_allowance','session_ref':reference,'generation':1,'work_operation_id':work,
        'expected_resource_ref':budget.inspect()['resource_ref'],'limits':{**budget.limits,'model_attempts':256}}
    if include_empty_note:body['note']=''
    expected={**body,'note':''};url=f"/api/projects/{s['pid']}/agent-sessions/{s['sid']}/resource-budget"
    with TestClient(app_for(owner),raise_server_exceptions=False)as client:
        response=client.post(url+'/adjust',json=body);assert response.status_code==200,response.text
        result=response.json();assert result['status']=='APPLIED'and result['request_sha256']==digest(expected)
        repeated=client.post(url+'/adjust',json=body);assert repeated.status_code==200,repeated.text
        assert repeated.json()['adjustment_ref']==result['adjustment_ref']
        page=client.get(url+'/adjustments',params={'work_operation_id':work,'session_sha256':reference['sha256']})
        assert page.status_code==200,page.text
        assert len(page.json()['packets'])==1 and page.json()['packets'][0]['request']==expected
    assert budget.inspect()['used']==before and budget.inspect()['allowance_adjustments']==1


@pytest.mark.parametrize('note',[0,'x'*161,'\ud800',None])
def test_invalid_note_is_rejected_before_reading_owner_or_changing_ledger(note):
    def forbidden(*args):pytest.fail('Malformed note must not reach owner or ledger')
    owner=SimpleNamespace(_manifest=forbidden)
    body={'operation_id':'bad_note','session_ref':{'id':'a'*32,'revision':1,'sha256':'b'*64},'generation':1,
        'work_operation_id':'work','expected_resource_ref':{'id':'c'*64,'revision':0,'sha256':'d'*64},
        'limits':{'source_bytes':1,'source_reads':1,'model_attempts':1,'retained_output_bytes':1},'note':note}
    with pytest.raises(ValidationError):service.change(owner,'project','a'*32,body)


@pytest.mark.parametrize('note',['  ','保留说明😀'])
def test_optional_note_roundtrip_keeps_exact_utf8_text_and_request_hash(note):
    body={'operation_id':'note_text','session_ref':{'id':'a'*32,'revision':1,'sha256':'b'*64},'generation':1,
        'work_operation_id':'work','expected_resource_ref':{'id':'c'*64,'revision':0,'sha256':'d'*64},
        'limits':{'source_bytes':1,'source_reads':1,'model_attempts':1,'retained_output_bytes':1},'note':note}
    once=service.AllowanceChange.model_validate(body).model_dump()
    twice=service.AllowanceChange.model_validate(once).model_dump()
    assert once==twice==body and digest(twice)==digest(body)
