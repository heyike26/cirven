"""AUTHORED_NOT_RUN. Real canonical journals/Store; original actor I/O is UNIT metadata."""
import asyncio
from copy import deepcopy
import pytest
from test_canonical_provider_generation import metadata_service
from test_conversation_configuration import configured,packet
from test_configuration_continuation import continuation


@pytest.fixture
def replaced(configured):
    h=configured;original=h.service.sessions.open
    async def fail(*_args,**_kwargs):raise ConnectionError('UNIT failed before native open')
    h.service.sessions.open=fail;h.old=packet(h,'old_not_started')
    with pytest.raises(ConnectionError):asyncio.run(h.configuration.configure(h.old))
    h.service.sessions.open=original
    asyncio.run(h.configuration.configure(packet(h,'first_current')))
    asyncio.run(h.configuration.configure(packet(h,'new_current',max_calls=13)))
    h.old_path=h.service._path('connections',[None,h.old['request']['operation_id']])
    h.query=lambda:h.service.open_operation(h.old['request']['operation_id'],None)
    return h


def test_current_replacement_adds_readonly_routing_evidence_without_changing_old_failed_result(replaced):
    h=replaced;before=deepcopy(h.calls);row=h.service._read(h.old_path);r=h.query()
    assert r['status']=='FAILED_NOT_STARTED' and r['configuration_disposition']['kind']=='SUPERSEDED_CONFIGURATION'
    assert r['configuration_disposition']['current']['operation_id']=='new_current'
    assert r['configuration_disposition']['old_execution_outcome_changed'] is False
    assert h.calls==before and h.service._read(h.old_path)==row


def test_original_unknown_remains_unknown_when_current_configuration_is_known(replaced):
    h=replaced;row=h.service._read(h.old_path);row.pop('error_type',None);row['phase']='UNKNOWN';h.service._save(h.old_path,row,replace=True)
    result=h.query();assert result['status']=='UNKNOWN_OUTCOME' and result['configuration_disposition']['old_execution_outcome_changed'] is False
    assert h.service._read(h.old_path)['phase']=='UNKNOWN'


def test_explicit_continue_of_superseded_config_is_observation_only(replaced):
    h=replaced;before=deepcopy(h.calls);row=h.service._read(h.old_path)
    result=asyncio.run(h.configuration.continue_configuration(h.scope['conversation_ref']['id'],continuation(h.old)))
    assert result['configuration_disposition']['kind']=='SUPERSEDED_CONFIGURATION' and h.calls==before
    assert h.service._read(h.old_path)==row


def test_pending_replacement_does_not_claim_a_bound_current_replacement(replaced):
    h=replaced;path=h.service._path('current-owners',h.scope['conversation_ref']['id']);head=h.service._read(path);head['phase']='PREPARING';h.service._save(path,head,replace=True)
    r=h.query();assert r['status']=='FAILED_NOT_STARTED' and 'configuration_disposition' not in r


def test_current_operation_is_never_disposed_as_superseded(replaced):
    h=replaced;r=h.service.open_operation('new_current',None)
    assert r['conversation_bound'] and 'configuration_disposition' not in r
