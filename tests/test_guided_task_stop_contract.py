"""UNIT termination proof guards; native execution is tested separately."""
import asyncio
from copy import deepcopy

import pytest

from packages.agent_sessions.guidance import SessionGuidance
from test_session_guidance import UnitOwner, native_messages


def setup(tmp_path):
    value = UnitOwner(tmp_path/'owner')
    value.guidance = SessionGuidance(value)
    context = value.context
    context.update(known_sessions={'ses_unit_root'}, native_commands_inflight=0,
                   native_command_epoch=1, observed_command_epoch=1, closing=False)
    value._finish(value.pid, value.sid, 'first', 'ACCEPTED', native_dispatch_acknowledged=True)
    value._claim(value.pid, value.sid, 'guide', 'GUIDANCE', {'raw_text': 'Keep the same task'})
    value._finish(value.pid, value.sid, 'guide', 'OBSERVED', native_message_id='msg_guide',
        generation=3, original_session_id='ses_unit_root', native_dispatch_acknowledged=True,
        original_active_operation_id='first')
    value._claim(value.pid, value.sid, 'stop', 'INTERRUPT', {'expected_active_operation_id': 'first'})
    value._finish(value.pid, value.sid, 'stop', 'RETURNED', generation=3,
        original_abort_acknowledged=True, target_operation_id='first')
    value._update(value.pid, value.sid, native_status='idle', original_session_id='ses_unit_root',
        guidance_cohort_operation_id='first', guidance_operation_ids=['guide'])
    barrier = {'operation_id': 'stop', 'generation': 3, 'target_operation_id': 'first', 'http_finished': True}
    context['interrupt_barrier'] = barrier
    messages = native_messages(value.operation(value.pid, value.sid, 'first'), error={'name': 'MessageAbortedError'})
    messages += native_messages(value.operation(value.pid, value.sid, 'guide'))
    options = dict(statuses={}, quiescent=True, barrier=barrier, can_release_interrupt=True)
    return value, messages, options


def test_unit_complete_exact_stop_proof_cancels_unanswered_guidance_without_model_or_replay(tmp_path):
    value, messages, options = setup(tmp_path)
    value.guidance._publish(value.context, value.pid, value.sid,
        value.operation(value.pid, value.sid, 'guide'))
    value.context['guidance_observation_cache'] = {'signature': 'old observed guidance'}
    assert value.guidance.public_snapshot(value.pid, value.sid)[0]['status'] == 'OBSERVED'
    asyncio.run(value._settle(value.pid, value.sid, value.context, messages, **options))
    parent = value.operation(value.pid, value.sid, 'first')
    assert parent['status'] == 'CANCELLED'
    assert value.operation(value.pid, value.sid, 'guide')['status'] == 'CANCELLED'
    assert value._state(value.pid, value.sid)['active_operation_id'] is None
    assert parent['termination_evidence']['external_actions_rolled_back'] is False
    assert value.http_calls == []
    assert value.guidance.public_snapshot(value.pid, value.sid)[0]['status'] == 'CANCELLED'
    assert 'guidance_observation_cache' not in value.context


@pytest.mark.parametrize('missing', ['ack', 'stop_unknown', 'quiescent', 'abort_finished', 'wrong_target',
    'wrong_generation', 'busy_root', 'busy_child', 'inflight', 'command_epoch', 'foreign_user',
    'changed_text', 'unfinished_assistant', 'unfinished_tool', 'not_aborted', 'guidance_unknown', 'parent_unknown'])
def test_unit_incomplete_or_unknown_proof_never_closes_the_task(tmp_path, missing):
    value, messages, options = setup(tmp_path)
    context = value.context
    if missing == 'ack': value._finish(value.pid, value.sid, 'stop', 'RETURNED', original_abort_acknowledged=False)
    elif missing == 'stop_unknown': value._finish(value.pid, value.sid, 'stop', 'UNKNOWN_OUTCOME')
    elif missing == 'quiescent': options['quiescent'] = False
    elif missing == 'abort_finished': context['interrupt_barrier']['http_finished'] = False
    elif missing == 'wrong_target': context['interrupt_barrier']['target_operation_id'] = 'another'
    elif missing == 'wrong_generation': context['interrupt_barrier']['generation'] = 4
    elif missing == 'busy_root': value._update(value.pid, value.sid, native_status='busy')
    elif missing == 'busy_child': context['known_sessions'].add('child'); options['statuses']['child'] = {'type': 'busy'}
    elif missing == 'inflight': context['native_commands_inflight'] = 1
    elif missing == 'command_epoch': context['native_command_epoch'] = 2
    elif missing == 'foreign_user': messages.append({'info': {'id': 'other', 'role': 'user', 'sessionID': 'ses_unit_root'}, 'parts': []})
    elif missing == 'changed_text': messages[-1]['parts'][0]['text'] = 'Different user content'
    elif missing == 'unfinished_assistant': messages[1]['info']['time'] = {}
    elif missing == 'unfinished_tool': messages[1]['parts'] = [{'type': 'tool', 'state': {'status': 'running'}}]
    elif missing == 'not_aborted': messages[1]['info']['error'] = {'name': 'OtherError'}
    elif missing == 'guidance_unknown': value._finish(value.pid, value.sid, 'guide', 'UNKNOWN_OUTCOME')
    elif missing == 'parent_unknown': value._finish(value.pid, value.sid, 'first', 'UNKNOWN_OUTCOME')
    parent = deepcopy(value.operation(value.pid, value.sid, 'first'))
    asyncio.run(value._settle(value.pid, value.sid, context, messages, **options))
    assert value.operation(value.pid, value.sid, 'first') == parent
    assert value._state(value.pid, value.sid)['active_operation_id'] == 'first'


def test_unit_a_completed_latest_guidance_reply_is_not_relabelled_by_an_older_aborted_parent(tmp_path):
    value, messages, options = setup(tmp_path)
    messages += native_messages(value.operation(value.pid, value.sid, 'guide'), finish='stop')[1:]
    messages[-1]['info']['id'] = 'latest_real_response'
    asyncio.run(value._settle(value.pid, value.sid, value.context, messages, **options))
    assert value.operation(value.pid, value.sid, 'first')['status'] == 'RETURNED'


def test_unit_projection_write_failure_recovers_by_reading_the_same_stop_without_rewriting_its_result(tmp_path):
    value, messages, options = setup(tmp_path)
    original_update = value._update
    fail = [True]

    def update(pid, sid, **changes):
        if changes.get('active_operation_id', 'unchanged') is None and fail[0]:
            fail[0] = False
            raise OSError('UNIT one-off state projection persistence failure')
        return original_update(pid, sid, **changes)

    value._update = update
    with pytest.raises(OSError):
        asyncio.run(value._settle(value.pid, value.sid, value.context, messages, **options))
    parent = deepcopy(value.operation(value.pid, value.sid, 'first'))
    assert parent['status'] == 'CANCELLED'
    # An in-memory projection may also be lost during a failed state write.
    value.context.pop('guidance_public_summaries', None)
    asyncio.run(value._settle(value.pid, value.sid, value.context, messages, **options))
    assert value._state(value.pid, value.sid)['active_operation_id'] is None
    assert value.operation(value.pid, value.sid, 'first') == parent
    assert value.http_calls == []
    assert value.guidance.public_snapshot(value.pid, value.sid)[0]['status'] == 'CANCELLED'
