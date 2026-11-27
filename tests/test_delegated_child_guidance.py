"""AUTHORED_NOT_RUN. Real Store/records with a labelled native HTTP fixture.

These cases exercise original root/child identity and settlement boundaries;
they do not qualify an actual CLI, model, Windows or VS Code extension host.
"""
import asyncio
from copy import deepcopy

import pytest

from packages.domain.store import Conflict
from test_child_controls import ChildUnit
from test_agent_sessions import ModelWire, tool, owner, control, until, scene
from packages.agent_sessions.child_observations import child_reference


class DelegatedUnit(ChildUnit):
    def __init__(self, directory):
        super().__init__(directory)
        self._finish(self.pid, self.sid, 'first', 'ACCEPTED', native_dispatch_acknowledged=True,
            deadline='2070-01-01T00:00:00+00:00')
        self._update(self.pid, self.sid, active_operation_id='first', active_child_operation=None)
        native = self.context['session_id']
        self.root_messages = [
            {'info': {'id': 'msg_first', 'sessionID': native, 'role': 'user'}, 'parts': []},
            {'info': {'id': 'msg_root_task', 'sessionID': native, 'role': 'assistant', 'parentID': 'msg_first'},
             'parts': [{'id': 'prt_task', 'messageID': 'msg_root_task', 'sessionID': native,
                'type': 'tool', 'tool': 'task', 'callID': 'call_task',
                'state': {'status': 'running', 'input': {'subagent_type': 'explore', 'prompt': 'Original task'},
                    'metadata': {'sessionId': self.child['id'], 'parentSessionId': native}}}]}]
        self.statuses = {native: {'type': 'busy'}, self.child['id']: {'type': 'busy'}}

    async def _http(self, context, method, path, body=None, **options):
        if method == 'GET' and path == '/session/'+self.context['session_id']+'/message':
            self.http_calls.append({'method': method, 'path': path, 'body': None, 'options': options})
            return deepcopy(self.root_messages)
        return await super()._http(context, method, path, body, **options)

    async def observe_delegated(self, *, barrier=None, quiescent=True):
        delegated = self.child_controls.delegated
        messages = await delegated.messages(self.pid, self.sid, self.context, self.statuses)
        return delegated.observe(self.pid, self.sid, self.context, messages, self.statuses,
            quiescent=quiescent, barrier=barrier, can_release_interrupt=bool(barrier))


def test_running_root_guidance_keeps_original_barrier_and_waits_for_latest_exact_child_result(tmp_path):
    async def run():
        s = DelegatedUnit(tmp_path)
        body = s.child_body('guide_one', 'first', 'Correct the original child constraint')
        first = await s.child_controls.submit(s.pid, s.sid, body)
        assert first['kind'] == 'CHILD_GUIDANCE' and first['delegated_parent'] is True
        assert first['delegation_basis']['part_id'] == 'prt_task'
        assert s._state(s.pid, s.sid)['active_operation_id'] == 'first'
        assert not s._state(s.pid, s.sid).get('active_child_operation')
        s.answer(first)
        latest = await s.child_controls.submit(s.pid, s.sid, s.child_body('guide_two', 'first', 'Use the final correction'))
        s.statuses = {}
        assert await s.observe_delegated() is True  # Old response cannot settle new guidance.
        s.answer(latest)
        assert await s.observe_delegated(quiescent=False) is True
        assert await s.observe_delegated() is False
        assert s.operation(s.pid, s.sid, 'guide_one')['status'] == 'COHORT_RETURNED'
        assert s.operation(s.pid, s.sid, 'guide_two')['status'] == 'RETURNED'
        assert s.operation(s.pid, s.sid, 'first')['status'] == 'ACCEPTED'  # Root result is still observed separately.
        assert s._state(s.pid, s.sid)['active_operation_id'] == 'first'
        assert [row['path'] for row in s.http_calls if row['method'] == 'POST'] == ['/session/'+s.child['id']+'/prompt_async']*2
        await s.child_controls.submit(s.pid, s.sid, body)
        assert len([row for row in s.http_calls if row['method'] == 'POST']) == 2
    asyncio.run(run())


@pytest.mark.parametrize('changed', ['sibling', 'completed', 'old_parent', 'other_agent', 'background', 'duplicate'])
def test_guidance_requires_exact_current_foreground_task_and_configuration_before_post(tmp_path, changed):
    async def run():
        s = DelegatedUnit(tmp_path)
        message = s.root_messages[-1]
        part = message['parts'][0]
        if changed == 'sibling': part['state']['metadata']['sessionId'] = 'ses_other'
        elif changed == 'completed': part['state']['status'] = 'completed'
        elif changed == 'old_parent': message['info']['parentID'] = 'msg_not_this_root_input'
        elif changed == 'other_agent': part['state']['input']['subagent_type'] = 'build'
        elif changed == 'background': part['state']['metadata']['background'] = True
        else: message['parts'].append(deepcopy(part))
        with pytest.raises(Conflict):
            await s.child_controls.submit(s.pid, s.sid, s.child_body('bad_guide', 'first'))
        assert not [row for row in s.http_calls if row['method'] == 'POST']
        assert s._state(s.pid, s.sid)['active_operation_id'] == 'first'
    asyncio.run(run())


def test_lost_delegated_ack_holds_root_and_original_inspection_never_reposts(tmp_path):
    async def run():
        s = DelegatedUnit(tmp_path)
        body = s.child_body('guide_unknown', 'first')
        s.post_error = True
        with pytest.raises(Conflict): await s.child_controls.submit(s.pid, s.sid, body)
        original = s.operation(s.pid, s.sid, body['operation_id'])
        assert original['status'] == 'UNKNOWN_OUTCOME'
        s.answer(original)
        s.statuses = {}
        assert await s.observe_delegated() is True
        assert await s.child_controls.submit(s.pid, s.sid, body) == original
        assert len([row for row in s.http_calls if row['method'] == 'POST']) == 1
        with pytest.raises(Conflict):
            await s.child_controls.submit(s.pid, s.sid, s.child_body('new_after_unknown', 'first'))
        assert len([row for row in s.http_calls if row['method'] == 'POST']) == 1
        assert s._state(s.pid, s.sid)['active_operation_id'] == 'first'
    asyncio.run(run())


def test_foreign_child_answer_or_pending_control_cannot_release_original_root(tmp_path):
    async def run():
        s = DelegatedUnit(tmp_path)
        receipt = await s.child_controls.submit(s.pid, s.sid, s.child_body('exact', 'first'))
        s.answer(receipt)
        s.statuses = {}
        s._update(s.pid, s.sid, pending_permissions=[{'request_ref': {'id': 'still_pending'}}])
        assert await s.observe_delegated() is True
        s._update(s.pid, s.sid, pending_permissions=[])
        s.messages[-1]['info']['parentID'] = 'msg_foreign'
        assert await s.observe_delegated() is True
        assert s.operation(s.pid, s.sid, receipt['id'])['status'] == 'ACCEPTED'
    asyncio.run(run())


def test_exact_stop_can_release_root_wait_without_relabelling_unknown_guidance(tmp_path):
    async def run():
        s = DelegatedUnit(tmp_path)
        s.post_error = True
        with pytest.raises(Conflict):
            await s.child_controls.submit(s.pid, s.sid, s.child_body('unknown', 'first'))
        s.statuses = {}
        barrier = {'operation_id': 'stop_root', 'target_operation_id': 'first', 'generation': 3}
        s.context['interrupt_barrier'] = barrier
        s._claim(s.pid, s.sid, 'stop_root', 'STOP', {})
        s._finish(s.pid, s.sid, 'stop_root', 'RETURNED', generation=3,
            target_native_session_id=s.context['session_id'], original_abort_acknowledged=True)
        assert await s.observe_delegated(barrier=barrier) is False
        assert s.operation(s.pid, s.sid, 'unknown')['status'] == 'UNKNOWN_OUTCOME'
        assert s._state(s.pid, s.sid)['delegated_child_observation']['automatic_replay'] is False
    asyncio.run(run())


@pytest.mark.parametrize('changed', [None, 'sibling_question', 'parallel_root_tool'])
def test_human_pause_requires_current_exact_blocked_child_calls_without_parallel_work(tmp_path, changed):
    async def run():
        s = DelegatedUnit(tmp_path)
        receipt = await s.child_controls.submit(s.pid, s.sid, s.child_body('guided_question', 'first'))
        native = s.child['id']
        message = {'info': {'id': 'msg_question', 'sessionID': native, 'role': 'assistant', 'parentID': receipt['native_message_id']},
            'parts': [{'id': 'prt_question', 'messageID': 'msg_question', 'sessionID': native,
                'type': 'tool', 'tool': 'question', 'callID': 'call_question', 'state': {'status': 'running'}}]}
        s.messages.append(message)
        request = {'request_ref': {'id': 'question_ref'}, 'native_request': {'sessionID': native,
            'tool': {'messageID': 'msg_question', 'callID': 'call_question'}}}
        if changed == 'sibling_question':request['native_request']['sessionID'] = 'ses_other'
        if changed == 'parallel_root_tool':
            s.root_messages[-1]['parts'].append({'id': 'prt_unblocked', 'sessionID': s.context['session_id'],
                'messageID': 'msg_root_task', 'type': 'tool', 'tool': 'bash', 'callID': 'call_unblocked', 'state': {'status': 'running'}})
        s._update(s.pid, s.sid, pending_questions=[request])
        observed = {native: deepcopy(s.messages)}
        helper = s.child_controls.delegated
        handled = helper.human_wait(s.pid, s.sid, s.context, s.root_messages, observed, s.statuses, quiescent=True)
        parent = s.operation(s.pid, s.sid, 'first')
        if changed:
            assert not handled and parent.get('human_wait', {}).get('status') != 'WAITING_FOR_USER'
        else:
            assert handled and parent['human_wait']['coverage'] == 'EXACT_DELEGATED_FOREGROUND_CHILD_REQUESTS'
            assert parent['human_wait']['owner_ttl_extended'] is False
            first = parent['human_wait']
            helper.human_wait(s.pid, s.sid, s.context, s.root_messages, observed, s.statuses, quiescent=True)
            assert s.operation(s.pid, s.sid, 'first')['human_wait'] == first
            s._update(s.pid, s.sid, pending_questions=[])
            helper.human_wait(s.pid, s.sid, s.context, s.root_messages, observed, s.statuses, quiescent=True)
            assert s.operation(s.pid, s.sid, 'first')['human_wait']['status'] == 'RUNNING'
    asyncio.run(run())


def test_actual_pinned_cli_steers_running_foreground_child_without_replacing_original_root(scene):
    """Unmodified CLI; only external model output is a bounded local wire."""
    wire = ModelWire([tool('task', {'description': 'Read original source', 'prompt': 'Review without writing',
        'subagent_type': 'explore'}), 'HOLD', 'GUIDED_CHILD_FINAL', 'ROOT_AFTER_CHILD_GUIDANCE']).start()
    async def run():
        service, request = owner(scene, wire)
        try:
            opened = await service.open(scene['pid'], request)
            sid = opened['session_ref']['id']
            await service.send(scene['pid'], sid, {**control(opened, 'root_running'), 'raw_text': 'Delegate the review to explore'})
            waiting = await until(service, scene, sid, lambda row: bool(row['pending_permissions']))
            permission = waiting['pending_permissions'][0]
            await service.reply(scene['pid'], sid, permission['request_ref']['id'],
                {**control(opened, 'allow_child'), 'request_ref': permission['request_ref'], 'reply': 'once'}, kind='PERMISSION')
            running = await until(service, scene, sid, lambda row: bool(row.get('child_guidance_targets')) and len(wire.requests) == 2)
            child = running['children'][0]
            original = service.active[sid]['process']
            receipt = await service.child_controls.submit(scene['pid'], sid,
                {**control(opened, 'steer_running_child'), 'child_ref': child_reference(running, child),
                 'raw_text': 'Use corrected constraint GUIDANCE_BOUND_TO_THIS_CHILD', 'expected_active_operation_id': 'root_running'})
            assert receipt['delegated_parent'] is True and receipt['original_session_id'] == child['id']
            assert service._state(scene['pid'], sid)['active_operation_id'] == 'root_running'
            wire.release.set()
            settled = await until(service, scene, sid, lambda row: row['active_operation_id'] is None)
            assert service.active[sid]['process'] is original and len(wire.requests) == 4
            assert service.operation(scene['pid'], sid, 'steer_running_child')['status'] == 'RETURNED'
            root = service.operation(scene['pid'], sid, 'root_running')
            assert root['status'] == 'RETURNED' and root['semantic_acceptance'] is False
            assert root['delegated_child_guidance_ids'] == ['steer_running_child']
            assert any('ROOT_AFTER_CHILD_GUIDANCE' in str(message) for message in settled['messages'])
            assert scene['actual'].read_bytes() == scene['original']
        finally:
            wire.release.set()
            await service.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()
