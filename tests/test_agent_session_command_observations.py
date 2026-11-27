"""Operation association on real captured command records, no new execution API."""
from copy import deepcopy

import pytest

from packages.agent_sessions.command_observations import project_command_observations, required_parent_ids
from packages.domain.store import Conflict
from test_opencode_command_observation import command_scene, exercise, observation, history


def operation(s, *, kind='MESSAGE', generation=1, native='msg_user', ident='original_message', **changes):
    value = {'id': ident, 'kind': kind, 'session_ref': s['ref'], 'generation': generation,
             'original_session_id': 'ses_command', 'native_message_id': native, 'status': 'RETURNED',
             'request': {'session_ref': s['ref'], 'generation': generation, 'raw_text': '修改并验证'}}
    if kind == 'GUIDANCE': value['original_active_operation_id'] = 'original_root_task'
    return {**value, **changes}


def actual(s, **options):
    packet = exercise(s, **options)
    observed = observation(s, packet)
    entries, reasons = history(s, observed)
    assert entries and not reasons
    return observed, entries


def test_command_result_belongs_to_exact_original_operation_and_is_not_whole_task_acceptance(command_scene):
    s = command_scene
    view, rows = actual(s, exit=7)
    result = project_command_observations(view, rows, [operation(s)])
    assert required_parent_ids(rows) == ['msg_user']
    command = result['commands'][0]
    assert command['operation_id'] == command['root_operation_id'] == 'original_message'
    assert command['exit_code'] == 7 and command['status'] == 'EXITED'
    assert command['qualification'] == 'ORIGINAL_COMMAND_OBSERVATION_ONLY'
    assert result['coverage']['whole_task_acceptance'] == 'NOT_ASSESSED'
    assert result['model_calls'] == 0 and result['automatic_replay'] is False
    assert result == project_command_observations(view, rows, [operation(s)])


def test_guidance_preserves_its_own_original_message_and_root_task(command_scene):
    s = command_scene
    view, rows = actual(s)
    result = project_command_observations(view, rows, [operation(s, kind='GUIDANCE', ident='guidance_one')])
    assert result['commands'][0]['operation_id'] == 'guidance_one'
    assert result['commands'][0]['root_operation_id'] == 'original_root_task'


@pytest.mark.parametrize('change', ['generation', 'native', 'session', 'request', 'duplicate', 'missing'])
def test_foreign_late_or_ambiguous_operation_is_not_adopted(command_scene, change):
    s = command_scene
    view, rows = actual(s)
    ops = [operation(s)]
    if change == 'generation': ops[0]['generation'] = 2
    elif change == 'native': ops[0]['original_session_id'] = 'ses_other'
    elif change == 'session': ops[0]['session_ref'] = {**s['ref'], 'sha256': 'f' * 64}
    elif change == 'request': ops[0]['request']['generation'] = 2
    elif change == 'duplicate': ops += [operation(s, ident='another')]
    elif change == 'missing': ops = []
    result = project_command_observations(view, rows, ops)
    assert not result['commands'] and result['coverage']['reason_codes']


def test_cross_scope_collision_and_operation_cap_fail_closed(command_scene):
    s = command_scene
    view, rows = actual(s)
    foreign = deepcopy(rows)
    foreign[0]['source_scope']['workspace_ref']['binding_sha256'] = 'f' * 64
    with pytest.raises(Conflict): project_command_observations(view, foreign, [operation(s)])
    collision = deepcopy(rows[0])
    collision['exit_code'] = 22
    with pytest.raises(Conflict): project_command_observations(view, rows + [collision], [operation(s)])
    with pytest.raises(Conflict): project_command_observations(view, rows, [operation(s)] * 129)


def test_empty_or_old_owner_history_has_explicit_readonly_coverage(command_scene):
    s = command_scene
    packet = exercise(s)
    view = observation(s, packet)
    result = project_command_observations(view, [], [], reason_codes=['NO_ADMITTED_COMMAND_OBSERVER'])
    assert not result['commands'] and result['read_only']
    assert 'NO_ADMITTED_COMMAND_OBSERVER' in result['coverage']['reason_codes']
