"""AUTHORED_NOT_RUN: exact ACK source precondition and historical recovery.

The inherited original observer/model/ACK fixtures are explicit UNIT boundaries.
No case qualifies native dispatch, task success or Windows delivery.
"""
import pytest
from packages.domain.store import Conflict
from test_original_work_deliveries import (delivery,prepare,create_packet,create,lookup)
from test_original_work_test_cases import mapped
from test_command_observations_routes import routed
from test_opencode_command_observation import command_scene


def change_ack(s,value=None,*,missing=False):
    service=s['service'];op=service.operation(s['pid'],s['ident'],s['input']['root_operation_id'])
    if missing:op.pop('native_dispatch_acknowledged',None)
    else:op['native_dispatch_acknowledged']=value
    service._write(s['root_path'],op,replace=True)


@pytest.mark.parametrize('value',[False,None,0,1,'true'])
def test_preallocated_message_ids_without_exact_true_ack_cannot_prepare(delivery,monkeypatch,value):
    s=delivery;change_ack(s,value)
    def forbidden(*args,**kwargs):raise AssertionError('ACK rejection must precede saved-file byte reads')
    monkeypatch.setattr(s['deliveries'],'_saved',forbidden)
    with pytest.raises(Conflict,match='acknowledgement'):prepare(s)
    with s['service'].store.connect() as c:
        assert c.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]==0


def test_missing_legacy_ack_cannot_silently_be_treated_as_confirmed_dispatch(delivery):
    s=delivery;change_ack(s,missing=True)
    with pytest.raises(Conflict,match='acknowledgement'):prepare(s)


def test_ack_loss_after_review_cannot_claim_new_archive_intent(delivery):
    s=delivery;p=create_packet(s,prepare(s));change_ack(s,False)
    with pytest.raises(Conflict,match='acknowledgement'):create(s,p)
    with s['service'].store.connect() as c:
        assert c.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]==0


def test_saved_receipt_lookup_download_and_same_key_return_survive_ack_metadata_loss(delivery):
    s=delivery;p=create_packet(s,prepare(s));first=create(s,p);change_ack(s,missing=True)
    owner=s['deliveries'];q=lookup(s,p)
    assert owner.operation(s['pid'],s['ident'],q)==first
    assert create(s,p)==first
    assert owner.continue_original(s['pid'],s['ident'],q)==first
    raw,receipt=owner.download(s['pid'],s['ident'],s['input']['root_operation_id'],p['operation_id'],q['request_sha256'])
    assert receipt==first and len(raw)==first['artifact']['byte_length']


def test_pending_unknown_metadata_stays_readable_but_new_archive_work_needs_ack(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));owner=s['deliveries']
    def interrupted(*args,**kwargs):raise OSError('UNIT archive interruption')
    monkeypatch.setattr(owner,'_archive',interrupted)
    with pytest.raises(OSError):create(s,p)
    change_ack(s,False);q=lookup(s,p)
    assert owner.operation(s['pid'],s['ident'],q)['kind']=='ORIGINAL_WORK_DELIVERY_PENDING'
    with pytest.raises(Conflict,match='acknowledgement'):owner.continue_original(s['pid'],s['ident'],q)
    retained=owner.retain(s['pid'],s['ident'],{**q,'statement':'Keep the original incomplete evidence'})
    assert retained['retention']['status']=='UNKNOWN_RETAINED_NOT_CANCELLED_OR_ROLLED_BACK'


def test_ack_drift_during_archive_preserves_orphan_without_committing_a_receipt(delivery,monkeypatch):
    s=delivery;p=create_packet(s,prepare(s));owner=s['deliveries'];original=owner._archive
    def drift(*args,**kwargs):
        raw=original(*args,**kwargs);change_ack(s,False);return raw
    monkeypatch.setattr(owner,'_archive',drift)
    with pytest.raises(Conflict,match='acknowledgement'):create(s,p)
    assert owner.operation(s['pid'],s['ident'],lookup(s,p))['kind']=='ORIGINAL_WORK_DELIVERY_PENDING'
    assert len(list(owner._path(s['pid'],s['ident'],p['operation_id'],'attempts').glob('*/package.zip')))==1
    with s['service'].store.connect() as c:
        assert c.execute('SELECT count(*) FROM original_work_delivery_receipts').fetchone()[0]==0


def test_dispatch_ack_is_not_a_task_success_or_target_acceptance_claim(delivery):
    s=delivery;service=s['service'];service._finish(s['pid'],s['ident'],s['input']['root_operation_id'],'UNKNOWN_OUTCOME',native_dispatch_acknowledged=True)
    v=prepare(s)
    assert 'ROOT_STATUS_IS_NOT_TASK_ACCEPTANCE' in v['warnings']
    assert v['whole_task_acceptance']=='NOT_ASSESSED' and v['target_environment_verified'] is False
