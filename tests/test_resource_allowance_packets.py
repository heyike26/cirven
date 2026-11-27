"""AUTHORED_NOT_RUN: immutable server packets before quota mutation, real roots."""
import pytest
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.domain.task_resource_budget import TaskResourceBudget
from packages.domain.native_capabilities import NativeCapabilities
from packages.agent_sessions.task_resources import for_operation
from packages.agent_sessions.resource_allowance import change,lookup
from packages.agent_sessions import resource_allowance_packets as packets
from test_experience_candidates import experience


def prepared(s,key='explicit_change',work=None):
    o=s['sessions'];manifest,reference=o._manifest(s['pid'],s['sid']);work=work or s['scope']['root_operation_id'];budget=for_operation(o,manifest,o.operation(s['pid'],s['sid'],work));budget.allocate()
    body={'operation_id':key,'session_ref':reference,'generation':o._state(s['pid'],s['sid'])['generation'],'work_operation_id':work,'expected_resource_ref':budget.inspect()['resource_ref'],'limits':{**budget.inspect()['limits'],'model_attempts':256},'note':'保留原请求 😀'}
    return budget,body


def page(s,work=None):
    _,ref=s['sessions']._manifest(s['pid'],s['sid'])
    return packets.page(s['sessions'],s['pid'],s['sid'],ref['sha256'],work or s['scope']['root_operation_id'])


def test_packet_save_failure_prevents_quota_mutation_and_preserves_original_ledger(experience,monkeypatch):
    s=experience;b,body=prepared(s);before=b.path.read_bytes();write=NativeCapabilities._write
    def failed(path,value,**kwargs):
        if path.parent.name=='allowance-packets':raise OSError('UNIT original packet save failed')
        return write(path,value,**kwargs)
    monkeypatch.setattr(NativeCapabilities,'_write',failed)
    with pytest.raises(OSError):change(s['sessions'],s['pid'],s['sid'],body)
    assert b.path.read_bytes()==before and page(s)['packets']==[]


def test_saved_packet_and_unknown_application_survive_business_sql_rollback(experience,monkeypatch):
    s=experience;b,body=prepared(s);before=b.path.read_bytes()
    def failed(*args,**kwargs):raise OSError('UNIT after original packet, before adjustment')
    monkeypatch.setattr(TaskResourceBudget,'adjust',failed)
    with pytest.raises(OSError):change(s['sessions'],s['pid'],s['sid'],body)
    result=page(s);row=result['packets'][0]
    assert row['request']==body and row['request_sha256']==digest(body) and row['observation']['status']=='NOT_OBSERVED'
    assert row['observation']['automatic_replay_allowed'] is False and b.path.read_bytes()==before


def test_lost_mutation_ack_is_discoverable_from_server_after_browser_journal_loss_and_new_generation(experience,monkeypatch):
    s=experience;b,body=prepared(s);save=TaskResourceBudget._save
    def lost(budget,value,**kwargs):
        save(budget,value,**kwargs)
        if value.get('adjustments'):raise OSError('UNIT adjustment saved but ACK lost')
    monkeypatch.setattr(TaskResourceBudget,'_save',lost)
    with pytest.raises(OSError):change(s['sessions'],s['pid'],s['sid'],body)
    s['sessions']._update(s['pid'],s['sid'],generation=2);before=b.path.read_bytes();row=page(s)['packets'][0]
    assert row['request']==body and row['observation']['status']=='APPLIED' and row['observation']['observed_generation']==2
    assert row['observation']['request_sha256']==digest(body) and b.path.read_bytes()==before and b.inspect()['allowance_adjustments']==1


def test_packet_rebinding_or_corruption_never_rewrites_original_request_or_grants_new_limits(experience):
    s=experience;b,body=prepared(s);change(s['sessions'],s['pid'],s['sid'],body);path=packets._path(b,body['operation_id']);original=path.read_bytes()
    with pytest.raises(Conflict):change(s['sessions'],s['pid'],s['sid'],{**body,'note':'Different request'})
    assert path.read_bytes()==original
    value=packets._read(b,path);value['request']['limits']['model_attempts']=512;NativeCapabilities._write(path,value,replace=True);corrupt=path.read_bytes();before=b.path.read_bytes()
    with pytest.raises(Conflict):page(s)
    _,next_body=prepared(s,key='new_after_corruption')
    with pytest.raises(Conflict):change(s['sessions'],s['pid'],s['sid'],next_body)
    assert path.read_bytes()==corrupt and b.path.read_bytes()==before


def test_packet_history_is_shared_across_exact_followups_of_the_same_original_work(experience):
    s=experience;o=s['sessions'];reference=s['scope']['session_ref'];request={'session_ref':reference,'generation':1,'raw_text':'Continue exact original work','original_work_ref':{'root_operation_id':s['scope']['root_operation_id'],'root_sha256':s['root']['sha256']}}
    o._claim(s['pid'],s['sid'],'followup','MESSAGE',request)
    b,body=prepared(s,work='followup');change(o,s['pid'],s['sid'],body);result=page(s)
    assert result['resource_id']==b.id and result['work_operation_id']==s['scope']['root_operation_id']
    assert result['packets'][0]['request']['work_operation_id']=='followup' and result['packets'][0]['observation']['status']=='APPLIED'


def test_legacy_adjustments_without_request_packet_are_counted_without_inventing_inputs(experience):
    s=experience;b,body=prepared(s);b.adjust(body['operation_id'],digest(body),body['expected_resource_ref'],body['limits'],note=body['note']);result=page(s)
    assert result['packets']==[] and result['legacy_operations_without_packet']==1 and result['presence_is_application_proof'] is False
