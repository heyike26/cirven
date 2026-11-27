"""Pinned original CLI/SQLite; only external inference and OS pixels are fixtures."""
import asyncio
from copy import deepcopy

import pytest

from apps.api.canonical_conversations import ConversationContinuation
from apps.api.conversation_creation import ConversationCreation, CreateConversation
from apps.api.conversation_organization import OrganizationChange
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_agent_sessions import scene
from test_canonical_conversations_cli import setup, entry
from test_public_conversation_runtime_cli import opened, returned
from native_capability_task_source64_fixture import CapabilityWire


def test_simultaneous_inputs_continue_one_actor_and_unknown_followup_ack_never_replays(scene):
    wire = CapabilityWire([{'hold': 'first', 'reply': 'FIRST_REPLY'}, 'GUIDE_REPLY', 'FOLLOWUP_REPLY']).start()

    async def run():
        h, directory, organization, service = setup(scene, wire)
        try:
            view, _ = await opened(h, h.pid, 'open_continuation')
            sid = view['session_ref']['id']; process = h.sessions.active[sid]['process']
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='enroll_continuation', action='move',
                expected_revision=0, source=row['source'], target_project_id=None))
            scope = directory.threads.view(row['id']); cid = row['id']
            manifest = deepcopy(h.sessions._manifest(h.pid, sid)[0])
            one, two = await asyncio.gather(
                service.send(cid, entry(scope, h, 'simultaneous_main', 'First real user input')),
                service.send(cid, entry(scope, h, 'simultaneous_quick', 'Preserve my manual changes and include this requirement')))
            assert {one['operation']['kind'], two['operation']['kind']} == {'MESSAGE', 'GUIDANCE'}
            parent = one if one['operation']['kind'] == 'MESSAGE' else two
            parent_id = parent['operation_id']; reference = parent['canonical_message_ref']
            deadline = asyncio.get_running_loop().time() + 15
            while not wire.requests:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(.04)
            status = await service.continuation(cid, parent_id, ConversationContinuation(operation_id='local_progress',
                expected_message_ref=reference, raw_text='进度'))
            assert status['operation']['role'] == 'LOCAL_STATUS'
            assert status['response']['source'] == 'ORIGINAL_LOCAL_STATUS' and len(wire.requests) == 1
            with pytest.raises(Conflict):
                await service.continuation(cid, parent_id, ConversationContinuation(operation_id='wrong_basis',
                    expected_message_ref={**reference, 'sha256': '0' * 64}, raw_text='Wrong original parent'))
            assert len(wire.requests) == 1
            wire.release('first')
            await returned(h, h.pid, sid, parent_id)
            assert len(wire.requests) == 2
            idle = await service.continuation(cid, parent_id, ConversationContinuation(operation_id='idle_progress',
                expected_message_ref=reference, raw_text='进度'))
            assert idle['operation']['role'] == 'LOCAL_STATUS' and len(wire.requests) == 2
            assert idle['operation']['local_status']['active_operation_id'] is None
            save, injected = service._save, False
            def lost_ack(path, value, **options):
                nonlocal injected
                if value['kind'] == 'MESSAGE' and value['request']['operation_id'] == 'after_reply' and value['phase'] == 'OBSERVED' and not injected:
                    injected = True
                    raise OSError('Lost continuation entry ACK after original native POST')
                return save(path, value, **options)
            service._save = lost_ack
            body = ConversationContinuation(operation_id='after_reply', expected_message_ref=reference,
                raw_text='Continue in the same discussion 😀 {"literal":true}')
            with pytest.raises(OSError):
                await service.continuation(cid, parent_id, body)
            service._save = save
            await returned(h, h.pid, sid, 'after_reply')
            observed = service.operation(cid, 'after_reply')
            assert observed['operation']['kind'] == 'MESSAGE' and observed['response']['text'] == 'FOLLOWUP_REPLY'
            before = service._path('messages', [cid, 'after_reply']).read_bytes()
            for _ in range(3):
                duplicate = await service.continuation(cid, parent_id, body)
                assert duplicate['canonical_message_ref'] == observed['canonical_message_ref']
            assert service._path('messages', [cid, 'after_reply']).read_bytes() == before
            assert len(wire.requests) == 3 and h.sessions._state(h.pid, sid)['model_calls_reserved'] == 3
            assert len(h.sessions.active) == 1 and h.sessions.active[sid]['process'] is process
            assert h.sessions._manifest(h.pid, sid)[0] == manifest
            assert scene['actual'].read_bytes() == scene['original']
        finally:
            await h.close('canonical-continuation')
    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_unknown_screenshot_origin_is_rejected_before_a_model_actor_or_message_entry_is_created(scene):
    wire = CapabilityWire([]).start()
    async def run():
        h, directory, _, service = setup(scene, wire)
        try:
            created = ConversationCreation(scene['store'], directory.threads).create(
                CreateConversation(operation_id='unverified_input_chat', project_id=None))
            cid = created['conversation_ref']['id']; scope = directory.threads.view(cid)
            snapshot = h.pages.pages[digest(h.pages.first)]
            snapshot['observation']['coverage']['pixel_source'] = 'UNKNOWN'
            screenshot = {'kind': 'SCREENSHOT', 'context_ref': h.pages.first, 'label': 'Keep this original input',
                'regions': [], 'image_sha256': snapshot['preview']['image_ref']['sha256']}
            with pytest.raises(Conflict):
                await service.send(cid, entry(scope, h, 'unverified_frame', 'Explain this screenshot', input_attachments=[screenshot]))
            assert not h.sessions.active and not wire.requests
            assert not service._path('messages', [cid, 'unverified_frame']).exists()
            assert snapshot['preview']['status'] == 'AVAILABLE'
        finally:
            await h.close('canonical-unverified-frame')
    try:
        asyncio.run(run())
    finally:
        wire.close()
