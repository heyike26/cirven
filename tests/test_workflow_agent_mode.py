"""Fixed original CLI/loop/tools; only the external model is substituted."""
import asyncio
import os
import pytest

from packages.agent_sessions.work_modes import WORKFLOW_POLICY_SHA256, WORKFLOW_PROMPT
from packages.domain.store import Conflict
from test_agent_sessions import scene, owner, ModelWire, control, tool, until, retain


def test_workflow_is_a_loaded_primary_mode_on_the_same_original_session(scene):
    wire = ModelWire(['PLAN_ONLY']).start()

    async def run():
        service, request = owner(scene, wire)
        command = 'echo WORKFLOW_REAL_MODE' if os.name == 'nt' else 'printf WORKFLOW_REAL_MODE'
        request.update(command_allowlist=[command], allowed_effects=['BACKGROUND_COMMAND'])
        try:
            opened = await service.open(scene['pid'], request)
            sid = opened['session_ref']['id']
            context = service.active[sid]
            assert 'workflow' in opened['available_agent_modes'], 'actual /config and /agent must confirm the mode'
            assert opened['workflow_mode_definition']['policy_sha256'] == WORKFLOW_POLICY_SHA256
            pid, native_id = context['process'].pid, context['session_id']
            manifest_before = service._manifest(scene['pid'], sid)[0]
            assert manifest_before['request']['max_calls'] == request['max_calls']
            assert not manifest_before['request'].get('workflow_ref'), 'no manual task graph prerequisite'
            await service.send(scene['pid'], sid, {**control(opened, 'plan-first'), 'raw_text': 'Review only', 'agent': 'plan'})
            await until(service, scene, sid, lambda row: row['active_operation_id'] is None)
            todos = [{'content': 'Run the exact admitted check', 'status': 'in_progress', 'priority': 'high'}]
            wire.replies.extend([tool('todowrite', {'todos': todos}),
                tool('bash', {'command': command, 'description': 'Run the admitted check'}),
                tool('todowrite', {'todos': [{**todos[0], 'status': 'completed'}]}), 'WORKFLOW_CHECKED'])
            raw = 'Continue this goal 原话 {"x":1} 😀\nCheck the admitted result.'
            body = {**control(opened, 'workflow-once'), 'raw_text': raw, 'agent': 'workflow'}
            await service.send(scene['pid'], sid, body)
            pending = await until(service, scene, sid, lambda row: bool(row['pending_permissions']))
            assert scene['actual'].read_bytes() == scene['original']
            permission = pending['pending_permissions'][0]
            assert permission['native_request']['permission'] == 'bash'
            await service.reply(scene['pid'], sid, permission['request_ref']['id'],
                {**control(opened, 'workflow-command-once'), 'request_ref': permission['request_ref'], 'reply': 'once'}, kind='PERMISSION')
            final = await until(service, scene, sid, lambda row: row['active_operation_id'] is None)
            op = service.operation(scene['pid'], sid, 'workflow-once')
            assert op['request']['agent'] == 'workflow' and op['request']['raw_text'] == raw
            assert op['work_mode_definition']['policy_sha256'] == WORKFLOW_POLICY_SHA256
            assert context['process'].pid == pid and context['session_id'] == native_id
            assert final['work_mode_message']['agent'] == 'workflow'
            assert final['work_mode_message']['native_message_id'] == op['native_message_id']
            assert final['todo'][0]['status'] == 'completed'
            parts = [p for m in final['messages'] for p in m['parts'] if p.get('type') == 'tool' and p.get('tool') == 'bash']
            assert any(p['state']['status'] == 'completed' and 'WORKFLOW_REAL_MODE' in p['state']['output'] for p in parts)
            retain('workflow-native-mode-and-todo', scene, service, wire, final)
            system_text = [message['content'] for req in wire.requests for message in req['body']['messages'] if message['role'] == 'system']
            assert any(isinstance(text, str) and WORKFLOW_PROMPT in text for text in system_text)
            count = len(wire.requests)
            assert (await service.send(scene['pid'], sid, body))['id'] == 'workflow-once'
            with pytest.raises(Conflict):
                await service.send(scene['pid'], sid, {**body, 'agent': 'build'})
            assert len(wire.requests) == count and count == 5, 'original-key replay never dispatches again'
            assert service._manifest(scene['pid'], sid)[0] == manifest_before
            assert scene['actual'].read_bytes() == scene['original']
        finally:
            await service.close()

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_workflow_and_build_share_the_existing_call_budget(scene):
    wire = ModelWire([tool('todowrite', {'todos': [{'content': 'Check the bounded goal', 'status': 'completed', 'priority': 'medium'}]}), 'BOUNDED_RESULT']).start()

    async def run():
        service, request = owner(scene, wire)
        request['max_calls'] = 2
        try:
            opened = await service.open(scene['pid'], request)
            sid = opened['session_ref']['id']
            body = {**control(opened, 'bounded-workflow'), 'raw_text': 'Use the admitted shared budget', 'agent': 'workflow'}
            await service.send(scene['pid'], sid, body)
            final = await until(service, scene, sid, lambda row: row['active_operation_id'] is None)
            assert len(wire.requests) == 2
            with pytest.raises(Conflict):
                await service.send(scene['pid'], sid, {**control(opened, 'no-extra-build-budget'), 'raw_text': 'A new mode is not a new allowance', 'agent': 'build'})
            assert len(wire.requests) == 2 and service.get(scene['pid'], sid)['work_mode_message']['agent'] == 'workflow'
            assert service.operation(scene['pid'], sid, 'no-extra-build-budget')['status'] == 'FAILED'
            retain('workflow-shared-budget', scene, service, wire, final)
        finally:
            await service.close()

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_workflow_cancel_is_the_original_abort_and_old_key_cannot_replay(scene):
    wire = ModelWire(['HOLD']).start()

    async def run():
        service, request = owner(scene, wire)
        try:
            opened = await service.open(scene['pid'], request)
            sid = opened['session_ref']['id']
            body = {**control(opened, 'workflow-held'), 'agent': 'workflow', 'raw_text': 'Keep this exact operation until cancelled'}
            await service.send(scene['pid'], sid, body)
            assert await asyncio.to_thread(wire.received.wait, 10)
            aborted = await service.interrupt(scene['pid'], sid, control(opened, 'stop-workflow-once'))
            assert aborted['original_abort_acknowledged']
            final = await until(service, scene, sid, lambda row: row['active_operation_id'] is None)
            assert service.operation(scene['pid'], sid, 'workflow-held')['status'] == 'CANCELLED'
            await service.send(scene['pid'], sid, body)
            assert len(wire.requests) == 1
            retain('workflow-original-cancel', scene, service, wire, final)
        finally:
            wire.release.set()
            await service.close()

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_workflow_question_reply_and_active_task_cannot_be_replaced_by_another_mode(scene):
    wire = ModelWire([tool('question', {'questions': [{'question': 'Which result should be checked?', 'header': 'Workflow',
        'custom': False, 'options': [{'label': 'Original result', 'description': 'Keep the same task'}]}]}), 'QUESTION_ANSWERED']).start()

    async def run():
        service, request = owner(scene, wire)
        try:
            opened = await service.open(scene['pid'], request)
            sid = opened['session_ref']['id']
            await service.send(scene['pid'], sid, {**control(opened, 'workflow-question'), 'agent': 'workflow', 'raw_text': 'Check a necessary choice'})
            pending = await until(service, scene, sid, lambda row: bool(row['pending_questions']))
            with pytest.raises(Conflict):
                await service.send(scene['pid'], sid, {**control(opened, 'must-not-replace'), 'agent': 'build', 'raw_text': 'A future mode'})
            assert service.get(scene['pid'], sid)['work_mode_message']['agent'] == 'workflow'
            q = pending['pending_questions'][0]
            await service.reply(scene['pid'], sid, q['request_ref']['id'],
                {**control(opened, 'answer-workflow-question'), 'request_ref': q['request_ref'], 'answers': [['Original result']]}, kind='QUESTION')
            final = await until(service, scene, sid, lambda row: row['active_operation_id'] is None)
            assert final['work_mode_message']['operation_id'] == 'workflow-question'
            assert len(wire.requests) == 2
            retain('workflow-native-question-freeze', scene, service, wire, final)
        finally:
            await service.close()

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_workflow_unconfirmed_definition_fails_before_model_or_budget_consumption(scene):
    wire = ModelWire(['SHOULD_NOT_BE_CALLED']).start()

    async def run():
        service, request = owner(scene, wire)
        try:
            opened = await service.open(scene['pid'], request)
            sid = opened['session_ref']['id']
            context = service.active[sid]
            assert 'workflow' in opened['available_agent_modes']
            context['workflow_mode'] = None  # Lose the native-definition observation, not the actual owner.
            assert 'workflow' not in service.get(scene['pid'], sid)['available_agent_modes']
            with pytest.raises(Conflict):
                await service.send(scene['pid'], sid, {**control(opened, 'unconfirmed-workflow'),
                    'agent': 'workflow', 'raw_text': 'Keep this exact input on admission failure'})
            final = service.get(scene['pid'], sid)
            assert final['active_operation_id'] is None and final['connected_owner']
            assert service.operation(scene['pid'], sid, 'unconfirmed-workflow')['status'] == 'FAILED'
            assert not wire.requests and scene['actual'].read_bytes() == scene['original']
            retain('workflow-unconfirmed-mode-admission', scene, service, wire, final)
        finally:
            await service.close()

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_workflow_guidance_keeps_frozen_mode_on_the_same_next_original_turn(scene):
    from packages.agent_sessions.guidance import SessionGuidance
    wire = ModelWire(['HOLD', 'WORKFLOW_GUIDANCE_CHECKED']).start()

    async def run():
        service, request = owner(scene, wire)
        guidance = SessionGuidance(service)
        try:
            opened = await service.open(scene['pid'], request)
            sid = opened['session_ref']['id']
            await service.send(scene['pid'], sid, {**control(opened, 'frozen-workflow'),
                'agent': 'workflow', 'raw_text': 'Investigate this bounded original goal'})
            assert await asyncio.to_thread(wire.received.wait, 10)
            context = service.active[sid]
            definition = context['workflow_mode']
            context['workflow_mode'] = {**definition, 'native_definition_sha256': '0' * 64}
            with pytest.raises(Conflict):
                await guidance.submit(scene['pid'], sid, {**control(opened, 'changed-policy-must-not-steer'),
                    'raw_text': 'Keep the actual frozen policy', 'intent': 'STEER', 'expected_active_operation_id': 'frozen-workflow'})
            assert len(wire.requests) == 1
            context['workflow_mode'] = definition
            body = {**control(opened, 'workflow-guidance'), 'raw_text': 'Additional requirement 中文 😀: preserve the exact input and verify the result',
                'intent': 'STEER', 'expected_active_operation_id': 'frozen-workflow'}
            accepted = await guidance.submit(scene['pid'], sid, body)
            assert accepted['status'] == 'ACCEPTED'
            assert await guidance.submit(scene['pid'], sid, body) == accepted and len(wire.requests) == 1
            wire.release.set()
            final = await until(service, scene, sid, lambda row: row['native_status'] == 'idle' and
                any(message['info'].get('parentID') == accepted['native_message_id'] and
                    message['info'].get('time', {}).get('completed') for message in row['messages']))
            assert len(wire.requests) == 2 and final['original_session_id'] == opened['original_session_id']
            assert any(message['role'] == 'system' and isinstance(message['content'], str) and WORKFLOW_PROMPT in message['content']
                for message in wire.requests[1]['body']['messages'])
            assert any(message['role'] == 'user' and body['raw_text'] in str(message['content'])
                for message in wire.requests[1]['body']['messages'])
            assert final['work_mode_message']['operation_id'] == 'frozen-workflow'
            assert scene['actual'].read_bytes() == scene['original']
            retain('workflow-frozen-mode-guidance', scene, service, wire, final)
        finally:
            wire.release.set()
            await service.close()

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_workflow_canonical_entry_uses_the_same_original_owner_and_exact_operation(scene):
    from copy import deepcopy
    from test_canonical_conversations_cli import setup, entry
    from test_public_conversation_runtime_cli import opened, returned
    from native_capability_task_source64_fixture import CapabilityWire
    from apps.api.conversation_organization import OrganizationChange
    wire = CapabilityWire(['CANONICAL_WORKFLOW_RETURNED']).start()

    async def run():
        h, directory, organization, canonical_entry = setup(scene, wire)
        try:
            view, _ = await opened(h, h.pid, 'workflow-canonical-open')
            sid = view['session_ref']['id']
            context = h.sessions.active[sid]
            process = context['process']
            manifest = deepcopy(h.sessions._manifest(h.pid, sid)[0])
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='workflow-to-chats', action='move',
                expected_revision=0, source=row['source'], target_project_id=None))
            scope = directory.threads.view(row['id'])
            raw = 'Current chat goal 中文 😀 {"literal":true}\nKeep the original operation.'
            body = entry(scope, h, 'canonical-workflow-once', raw, agent='workflow')
            response = await canonical_entry.send(row['id'], body)
            assert response['original_source']['session_ref'] == view['session_ref']
            final = await returned(h, h.pid, sid, body.operation_id)
            receipt = canonical_entry.operation(row['id'], body.operation_id)
            assert receipt['response']['text'] == 'CANONICAL_WORKFLOW_RETURNED'
            op = h.sessions.operation(h.pid, sid, body.operation_id)
            assert op['request']['raw_text'] == raw and op['request']['agent'] == 'workflow'
            assert op['work_mode_definition'] == context['workflow_mode']
            assert h.sessions.active[sid]['process'] is process and final['original_session_id'] == view['original_session_id']
            assert h.sessions._manifest(h.pid, sid)[0] == manifest
            await canonical_entry.send(row['id'], body)
            assert len(wire.requests) == 1 and scene['actual'].read_bytes() == scene['original']
            retain('workflow-canonical-shared-entry', scene, h.sessions, wire, final)
        finally:
            await h.close('workflow-canonical-entry')

    try:
        asyncio.run(run())
    finally:
        wire.close()
