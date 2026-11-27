"""AUTHORED_NOT_RUN. Real SQLite metadata/journal; SDK actor I/O is UNIT only."""
import asyncio
from copy import deepcopy
import pytest
from apps.api.configuration_adoptions import ConfigurationAdoptions
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_configuration_candidates import candidate
from test_canonical_provider_generation import metadata_service
from test_conversation_configuration import configured


@pytest.fixture
def adopted(candidate):
    h=candidate;c=h.service;store=c.threads.store;cid=h.scope['conversation_ref']['id']
    with store.connect() as db:
        db.execute('INSERT INTO conversation_threads (conversation_id,initial_kind,project_id,created_at,deleted_at) VALUES (?,?,?,?,?)',(cid,'original',None,'UNIT',None))
    h.destination=store.create_project('Moved conversation','UNIT organization only')['id']
    h.original_row=c._read(h.path);h.original_manifest=deepcopy(h.manifest)
    # The SDK receipt retains the old frozen reference while metadata moves.
    original=c.sessions.open_operation
    def receipt(key,pid=None):
        result=original(key,pid);result['conversation_ref']=deepcopy(h.original_row['conversation_ref']);return result
    c.sessions.open_operation=receipt
    h.scope.update(project_id=h.destination,conversation_ref={'id':cid,'revision':2})
    h.adoptions=ConfigurationAdoptions(c);c.configuration_adoptions=h.adoptions
    h.preparation={'original':h.adoptions.reference(h.original_row),'conversation_ref':deepcopy(h.scope['conversation_ref']),'project_id':h.destination}
    return h


def command(h,key='adopt_original'):
    view=h.adoptions.prepare(h.preparation)
    body={'operation_id':key,'preparation':deepcopy(view['preparation']),'preparation_sha256':view['preparation_sha256']}
    return {**body,'confirmation_sha256':digest(body)}


def test_prepare_observes_only_without_load_bind_sdk_inspect_or_metadata_write(adopted):
    h=adopted;before=deepcopy(h.calls);a=h.adoptions.prepare(h.preparation);b=h.adoptions.prepare(h.preparation)
    assert a==b and a['adoption_allowed'] and a['read_only'] and a['model_calls']==0
    assert h.calls==before and h.service._read(h.path)==h.original_row
    with h.service.threads.store.connect() as db:assert db.execute('SELECT count(*) FROM configuration_adoptions').fetchone()[0]==0


def test_explicit_acknowledgement_changes_organization_authority_only(adopted):
    h=adopted;before=deepcopy(h.calls);body=command(h);r=asyncio.run(h.adoptions.adopt(body))
    assert r['accepted'] and r['current'] and not r['owner_started'] and not r['permission_granted']
    assert h.calls==before and h.manifest==h.original_manifest and h.service._read(h.path)==h.original_row
    assert h.service.configuration_scope(h.original_row)['project_id']==h.destination


def test_repeat_original_ack_is_idempotent_even_after_another_move(adopted):
    h=adopted;body=command(h);asyncio.run(h.adoptions.adopt(body));h.scope['conversation_ref']['revision']=3
    before=deepcopy(h.calls);a=asyncio.run(h.adoptions.adopt(body));b=h.adoptions.operation(h.scope['conversation_ref']['id'],body['operation_id'])
    assert a==b and a['accepted'] and not a['current'] and h.calls==before
    with pytest.raises(Conflict):h.service.configuration_scope(h.original_row)


def test_next_explicit_adoption_retains_both_immutable_operations(adopted):
    h=adopted;first=command(h);asyncio.run(h.adoptions.adopt(first));h.scope['conversation_ref']['revision']=3
    h.preparation['conversation_ref']=deepcopy(h.scope['conversation_ref']);second=command(h,'adopt_again')
    assert asyncio.run(h.adoptions.adopt(second))['current']
    assert not h.adoptions.operation(h.scope['conversation_ref']['id'],first['operation_id'])['current']
    with h.service.threads.store.connect() as db:assert db.execute('SELECT count(*) FROM configuration_adoptions').fetchone()[0]==2


def test_candidate_initialization_progress_invalidates_prepared_confirmation(adopted):
    h=adopted;body=command(h);h.actor.update(generation=1,original_session_id='ses_known',owner_status='READY',native_status='idle',connected_owner=True,observation_is_current=True)
    with pytest.raises(Conflict):asyncio.run(h.adoptions.adopt(body))
    assert h.calls['load']==0 and h.calls['send']==0


def test_move_after_review_rejects_new_ack_before_any_owner_activity(adopted):
    h=adopted;body=command(h);h.scope['conversation_ref']['revision']=3;before=deepcopy(h.calls)
    with pytest.raises(Conflict):asyncio.run(h.adoptions.adopt(body))
    assert h.calls==before


def test_same_metadata_key_cannot_acknowledge_another_body(adopted):
    h=adopted;body=command(h);asyncio.run(h.adoptions.adopt(body));other=deepcopy(body);other['preparation_sha256']='f'*64
    other['confirmation_sha256']=digest({k:v for k,v in other.items() if k!='confirmation_sha256'})
    with pytest.raises(Conflict,match='IDEMPOTENCY_CONFLICT'):asyncio.run(h.adoptions.adopt(other))


def test_native_create_unknown_cannot_be_adopted_as_safe_to_continue(adopted):
    h=adopted;h.actor.update(generation=1,owner_status='BLOCKED');view=h.adoptions.prepare(h.preparation)
    assert not view['adoption_allowed'];before=deepcopy(h.calls)
    with pytest.raises(Conflict):asyncio.run(h.adoptions.adopt(command(h)))
    assert h.calls==before


def test_other_configuration_head_is_never_overwritten_by_adoption(adopted):
    h=adopted;body=command(h);calls=deepcopy(h.calls)
    request=deepcopy(h.original_row['request']);request['operation_id']='another_current'
    head={'kind':'PROVIDER_GENERATION','phase':'PREPARING','provider_generation':2,'request':request,'body_sha256':digest(request)}
    path=h.service._path('current-owners',h.scope['conversation_ref']['id'])
    h.service._save(path,head,replace=path.exists());before=path.read_bytes()
    p=h.adoptions.prepare(h.preparation);assert not p['adoption_allowed'] and p['reason']=='OTHER_CONFIGURATION'
    with pytest.raises(Conflict):asyncio.run(h.adoptions.adopt(body))
    assert path.read_bytes()==before and h.calls==calls


def test_wrong_original_confirmation_cannot_borrow_acknowledgement(adopted):
    h=adopted;p=deepcopy(h.preparation);p['original']['confirmation_sha256']='e'*64
    with pytest.raises(Conflict):h.adoptions.prepare(p)


def test_readonly_receipt_after_current_candidate_changes_does_not_rewrite_old_ack(adopted):
    h=adopted;body=command(h);asyncio.run(h.adoptions.adopt(body));before=deepcopy(h.calls)
    h.actor.update(generation=1,original_session_id='ses_saved',owner_status='READY',native_status='idle')
    assert h.adoptions.operation(h.scope['conversation_ref']['id'],body['operation_id'])['accepted']
    assert h.calls==before and h.manifest==h.original_manifest


def test_explicit_original_candidate_continue_keeps_original_request_manifest_and_actor(adopted):
    h=adopted;asyncio.run(h.adoptions.adopt(command(h)));source=deepcopy(h.actor['session_ref']);result=h.continue_candidate()
    assert result['conversation_bound'] and result['session']['session_ref']==source
    assert h.calls['load']==1 and h.calls['open']==1 and h.calls['send']==0
    assert h.manifest==h.original_manifest and h.service._read(h.path)['request']==h.original_row['request']
