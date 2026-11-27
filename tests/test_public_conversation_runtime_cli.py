"""Real fixed Linux OpenCode; only model replies and saved pixels are fixtures."""
import asyncio
from copy import deepcopy

import pytest

from apps.api.conversation_directory import ConversationDirectory
from apps.api.conversation_history import ConversationHistory
from apps.api.conversation_organization import ConversationOrganization, OrganizationChange
from apps.api.conversation_runtime import ConversationRuntime
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest
from test_agent_sessions import scene
from test_native_capability_task_source64 import stable_actual_python_and_fixed_plugin_inputs
from native_capability_task_source64_fixture import CapabilityScene, CapabilityWire, evidence, image_urls, require, text_of


def attach(h):
    state = h.native.app.state
    state.agent_sessions, state.native_messages = h.sessions, h.native
    directory = ConversationDirectory(h.native.app)
    state.conversation_threads = directory.threads
    history = ConversationHistory(h.native.app, threads=directory.threads)
    h.sessions.conversations = ConversationRuntime(h.native.app, directory.threads, history)
    return directory, ConversationOrganization(h.scene['store'], directory.resolve, directory.threads)


async def opened(h, pid, operation_id, reference=None):
    body = {'operation_id': operation_id, 'provider_profile_ref': h.profile,
        'capability_binding_sha256': h.caps.native_binding_snapshot()['sha256'],
        'ttl_seconds': 300, 'max_calls': 24, 'max_output_tokens': 1024}
    if reference is not None:
        body['conversation_ref'] = reference
    view = await h.sessions.open(pid, body)
    sid = view['session_ref']['id']
    h.contexts[sid] = h.sessions.active[sid]
    h.identities[sid] = h.sessions._state(pid, sid)['process_identity']
    return view, body


async def returned(h, pid, sid, operation_id):
    deadline = asyncio.get_running_loop().time() + 45
    while True:
        view = await h.sessions.inspect(pid, sid)
        if view['active_operation_id'] is None:
            require(h.sessions.operation(pid, sid, operation_id)['status'] == 'RETURNED', canonical(view))
            return view
        require(asyncio.get_running_loop().time() < deadline, 'The actual public task did not return')
        await asyncio.sleep(.08)


def message(view, operation_id, raw_text, **extras):
    return {'operation_id': operation_id, 'session_ref': view['session_ref'],
        'generation': view['generation'], 'raw_text': raw_text, 'agent': 'plan', **extras}


def screenshots(h):
    second = h.pages.add('public-second-frame', 'PUBLIC_SECOND_SAVED_FRAME')
    return [{'kind': 'SCREENSHOT', 'context_ref': ref, 'label': 'User saved screenshot ' + str(index),
        'image_sha256': h.pages.lookup(ref)['preview']['image_ref']['sha256'],
        'regions': [{'x': .25, 'y': .25, 'width': .5, 'height': .5}] if index == 0 else []}
        for index, ref in enumerate((h.pages.first, second))]


def test_move_then_two_messages_keep_full_public_agent_and_exact_user_display(scene):
    wire = CapabilityWire(['ACTUAL_PRIOR_A_REPLY', 'ACTUAL_B_FIRST_REPLY', 'ACTUAL_B_SECOND_REPLY']).start()

    async def run():
        h = CapabilityScene(scene, wire); directory, organization = attach(h)
        try:
            a, _ = await opened(h, h.pid, 'open_public_a')
            sid_a = a['session_ref']['id']
            await h.sessions.send(h.pid, sid_a, message(a, 'prior_a', 'USER_REQUIREMENT_FROM_A'))
            await returned(h, h.pid, sid_a, 'prior_a')
            await h.sessions.disconnect(h.pid, sid_a)
            old_paths = [h.sessions._path(h.pid, sid_a, name) for name in ('manifest.json', 'state.json')]
            old_bytes = {p: p.read_bytes() for p in old_paths}
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': a['session_ref']})
            target = scene['store'].create_project('Public project B', 'PUBLIC_TARGET_B_PURPOSE')['id']
            organization.change(row['id'], OrganizationChange.model_validate({'operation_id': 'public_move_to_b',
                'action': 'move', 'expected_revision': 0, 'source': row['source'], 'target_project_id': target}))
            scope = directory.threads.view(row['id'])
            b, open_body = await opened(h, target, 'open_public_b', scope['conversation_ref'])
            sid_b = b['session_ref']['id']; context = h.sessions.active[sid_b]
            manifest = h.sessions._manifest(target, sid_b)[0]; frozen_manifest = deepcopy(manifest)
            raw = 'Compare the saved frames and continue. {"literal user JSON":true}'
            body = message(b, 'public_b_first', raw, conversation_ref=scope['conversation_ref'], input_attachments=screenshots(h))
            accepted = await h.sessions.send(target, sid_b, body)
            first = await returned(h, target, sid_b, body['operation_id'])
            seen = text_of(wire.requests[1])
            require(all(text in seen for text in (raw, 'USER_REQUIREMENT_FROM_A', 'ACTUAL_PRIOR_A_REPLY', 'PUBLIC_TARGET_B_PURPOSE')),
                'Actual target public loop did not receive the selected project and exact earlier discussion')
            require(len(image_urls(wire.requests[1])) == 2, 'Both selected images must reach the actual public provider')
            users = [m for m in first['messages'] if m['info']['role'] == 'user']
            require(len(users) == 1 and [p['text'] for p in users[0]['parts']] == [raw]
                and len(users[0]['input_attachments']) == 2, 'Public transcript exposed host wire or lost authored inputs')
            actual = await h.sessions._http(context, 'GET', '/session/' + context['session_id'] + '/message')
            actual_user = next(m for m in actual if m['info']['role'] == 'user')
            require(sum(p['type'] == 'file' for p in actual_user['parts']) == 2
                and 'READONLY_CONVERSATION_SOURCE_DATA' in canonical(actual_user), 'Actual SDK payload was replaced by a mock transcript')
            await h.sessions.send(target, sid_b, message(b, 'public_b_second', 'SECOND_REAL_PUBLIC_TURN',
                conversation_ref=directory.threads.view(row['id'])['conversation_ref']))
            final = await returned(h, target, sid_b, 'public_b_second')
            require(final['original_session_id'] == b['original_session_id'] and 'ACTUAL_B_FIRST_REPLY' in text_of(wire.requests[2]),
                'A new readonly native owner replaced the original long lived public agent')
            require(manifest['request']['max_calls'] == 24 and manifest.get('native_message_admission') is None
                and h.sessions._manifest(target, sid_b)[0] == frozen_manifest, 'Public mode or exact manifest changed')
            require(all(p.read_bytes() == raw_bytes for p, raw_bytes in old_bytes.items()), 'Reading prior discussion rewrote the old task')
            listing = directory.read()
            require([item['id'] for item in listing['items']] == [row['id']], 'New execution owner became another user chat')
            replay = await h.sessions.send(target, sid_b, body)
            require(replay['status'] == 'RETURNED' and (await h.sessions.open(target, open_body))['session_ref'] == b['session_ref']
                and len(wire.requests) == 3, 'Exact recovery replay added another model call')
            # An explicit new message on a mismatched owner is known unsent and
            # must release its slot, not become an unrecoverable unknown.
            moved = organization.change(row['id'], OrganizationChange.model_validate({'operation_id': 'public_move_back_a',
                'action': 'move', 'expected_revision': scope['conversation_ref']['revision'],
                'source': row['source'], 'target_project_id': h.pid}))
            with pytest.raises(Conflict):
                await h.sessions.send(target, sid_b, message(b, 'wrong_old_owner', 'DO_NOT_DISPATCH',
                    conversation_ref=directory.threads.view(row['id'])['conversation_ref']))
            failed = h.sessions.operation(target, sid_b, 'wrong_old_owner')
            require(failed['status'] == 'FAILED' and failed['native_post_attempted'] is False
                and h.sessions._state(target, sid_b)['active_operation_id'] is None and len(wire.requests) == 3,
                'Known pre-dispatch validation failure consumed a live task or called a model')
            evidence('public-move-two-turns', {'qualification': 'ACTUAL_FIXED_LINUX_ORIGINAL_MODEL_AND_OS_FIXTURES',
                'first': first, 'final': final, 'replay': replay, 'failed': failed, 'moved': moved,
                'provider_requests': wire.requests, 'raw_sdk_messages': actual, 'old_source_bytes_unchanged': True,
                'official_mcp_selected': False, 'Windows_qualified': False})
        finally:
            evidence('public-move-two-turns-observed', {'provider_requests': wire.requests})
            await h.close('public-move-two-turns')
    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_moved_busy_public_guidance_keeps_old_task_scope_and_saved_images(scene):
    wire = CapabilityWire([{'hold': 'old-public-task', 'reply': 'OLD_PUBLIC_TASK_REPLY'}, 'PUBLIC_GUIDANCE_FINAL']).start()

    async def run():
        h = CapabilityScene(scene, wire); directory, organization = attach(h)
        try:
            opened_a, _ = await opened(h, h.pid, 'open_busy_public')
            sid = opened_a['session_ref']['id']; original_id = opened_a['original_session_id']
            await h.sessions.send(h.pid, sid, message(opened_a, 'busy_public_parent', 'OLD_A_TASK_PURPOSE'))
            deadline = asyncio.get_running_loop().time() + 30
            while not wire.requests:
                require(asyncio.get_running_loop().time() < deadline, 'Old public task never reached actual provider')
                await asyncio.sleep(.05)
            manifest = h.sessions._manifest(h.pid, sid)[0]
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': opened_a['session_ref']})
            target = scene['store'].create_project('New logical B', 'MUST_NOT_INJECT_B_INTO_OLD_GUIDANCE')['id']
            organization.change(row['id'], OrganizationChange.model_validate({'operation_id': 'move_busy_public',
                'action': 'move', 'expected_revision': 0, 'source': row['source'], 'target_project_id': target}))
            guide = message(opened_a, 'public_busy_guide', 'SUPPLEMENT_THE_SAME_OLD_A_TASK',
                expected_active_operation_id='busy_public_parent',
                conversation_ref=directory.threads.view(row['id'])['conversation_ref'], input_attachments=screenshots(h))
            guide.pop('agent')
            accepted = await h.sessions.guidance.submit(h.pid, sid, guide)
            require(accepted['status'] == 'ACCEPTED', canonical(accepted))
            wire.release('old-public-task')
            final = await returned(h, h.pid, sid, 'busy_public_parent')
            require(final['original_session_id'] == original_id and len(wire.requests) == 2,
                'Guidance restarted or replaced the original public task')
            require('SUPPLEMENT_THE_SAME_OLD_A_TASK' in text_of(wire.requests[1])
                and 'MUST_NOT_INJECT_B_INTO_OLD_GUIDANCE' not in text_of(wire.requests[1])
                and len(image_urls(wire.requests[1])) == 2, 'Guidance lost attachments or inherited another project context')
            saved = h.sessions.operation(h.pid, sid, guide['operation_id'])
            require(saved['conversation_context']['scope'] is None and saved['conversation_context']['guidance']
                and h.sessions._manifest(h.pid, sid)[0] == manifest, 'Guidance changed old workspace or permissions')
            users = [m for m in final['messages'] if m['info']['role'] == 'user']
            require(len(users) == 2 and [p['text'] for p in users[0]['parts'] if p['type'] == 'text'] == ['OLD_A_TASK_PURPOSE']
                and [p['text'] for p in users[1]['parts']] == [guide['raw_text']], 'Legacy or typed user display exposed host payload')
            replay = await h.sessions.guidance.submit(h.pid, sid, guide)
            require(replay['status'] == saved['status'] and len(wire.requests) == 2, 'Guidance recovery resent a model request')
            evidence('public-busy-guidance', {'qualification': 'ACTUAL_FIXED_LINUX_ORIGINAL_MODEL_AND_OS_FIXTURES',
                'accepted': accepted, 'final': final, 'guidance': saved, 'provider_requests': wire.requests,
                'official_mcp_selected': False, 'Windows_qualified': False})
        finally:
            evidence('public-busy-guidance-observed', {'provider_requests': wire.requests})
            await h.close('public-busy-guidance')
    try:
        asyncio.run(run())
    finally:
        wire.close()
