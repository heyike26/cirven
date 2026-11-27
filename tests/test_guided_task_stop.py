"""Pinned real OpenCode loop: stopping an admitted but unanswered guidance cohort."""
import asyncio

import pytest

from packages.agent_sessions.guidance import SessionGuidance
from packages.domain.store import Conflict
from test_agent_sessions import ModelWire, control, owner, retain, scene, until
from test_session_guidance_api import HeldModelGate


@pytest.mark.parametrize('agent', ['plan', 'build', 'workflow'])
def test_actual_stop_settles_observed_guidance_without_waiting_for_its_reply(scene, agent):
    wire = ModelWire(['HOLD', 'MUST_NOT_RUN_AFTER_STOP']).start()
    wire.release = HeldModelGate()

    async def run():
        service, request = owner(scene, wire)
        try:
            opened = await service.open(scene['pid'], request)
            sid = opened['session_ref']['id']
            context = service.active[sid]
            original_process = context['process'].pid
            manifest = service._manifest(scene['pid'], sid)[0]
            goal = {**control(opened, 'guided-stop-parent'), 'agent': agent,
                    'raw_text': 'Read only; keep the original goal 中文 😀 and wait for my additional requirement'}
            await service.send(scene['pid'], sid, goal)
            assert await asyncio.to_thread(wire.received.wait, 10)
            guide = await SessionGuidance(service).submit(scene['pid'], sid,
                {**control(opened, 'guided-stop-guidance'), 'intent': 'STEER',
                 'expected_active_operation_id': goal['operation_id'],
                 'raw_text': 'Keep this same task and mode; do not change any files'})
            before = await until(service, scene, sid, lambda row: any(
                item['operation_id'] == guide['id'] and item['native_user_observed'] for item in row.get('guidance', [])))
            assert before['active_operation_id'] == goal['operation_id']
            with pytest.raises(Conflict):
                await service.interrupt(scene['pid'], sid, {**control(opened, 'wrong-stop-target'),
                    'expected_active_operation_id': 'some-other-task'})
            assert service.get(scene['pid'], sid)['active_operation_id'] == goal['operation_id']
            stop = {**control(opened, 'guided-stop-once'), 'expected_active_operation_id': goal['operation_id']}
            receipt = await service.interrupt(scene['pid'], sid, stop)
            assert receipt['original_abort_acknowledged'] and receipt['control_scope'] == 'EXACT_OPERATION'
            final = await until(service, scene, sid, lambda row: row['active_operation_id'] is None, timeout=12)
            parent = service.operation(scene['pid'], sid, goal['operation_id'])
            guidance = service.operation(scene['pid'], sid, guide['id'])
            assert parent['status'] == 'CANCELLED' and guidance['status'] == 'CANCELLED'
            assert next(item for item in final['guidance'] if item['operation_id'] == guide['id'])['status'] == 'CANCELLED'
            assert {key: parent['request'][key] for key in goal} == goal
            assert parent['semantic_acceptance'] is False
            assert guidance['native_message_id'] == guide['native_message_id']
            assert context['process'].pid == original_process and context['session_id'] == opened['original_session_id']
            assert service._manifest(scene['pid'], sid)[0] == manifest
            assert await service.interrupt(scene['pid'], sid, stop) == receipt
            assert (await service.send(scene['pid'], sid, goal))['status'] == 'CANCELLED'
            wire.release.set()
            for _ in range(3):
                await service.inspect(scene['pid'], sid)
                await asyncio.sleep(.1)
            assert len(wire.requests) == 1 and not wire.release.timed_out
            assert scene['actual'].read_bytes() == scene['original']
            retain('guided-stop-' + agent, scene, service, wire, final)
        finally:
            wire.release.set()
            await service.close()

    try:
        asyncio.run(run())
    finally:
        wire.close()
