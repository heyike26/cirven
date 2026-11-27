"""Actual pinned CLI; only inference is a fixture. No internal agent substitute."""
import asyncio
import time
from copy import deepcopy

import pytest

from apps.api.canonical_conversations import CanonicalConversations, ConversationMessage
from apps.api.conversation_creation import ConversationCreation, CreateConversation
from apps.api.conversation_history import ConversationHistory
from packages.agent_sessions.models import OpenConversation
from packages.domain.store import Conflict
from test_agent_sessions import scene
from test_public_conversation_runtime_cli import attach, opened, returned
from apps.api.conversation_organization import OrganizationChange
from native_capability_task_source64_fixture import CapabilityScene, CapabilityWire, require, text_of


def test_actual_original_terminal_401_is_known_failure_and_queries_never_repost(scene):
    wire = CapabilityWire([{'_http_error': 401}]).start()

    async def run():
        h, directory, organization, service = setup(scene, wire)
        try:
            view, _ = await opened(h, h.pid, 'open_auth_rejection')
            sid = view['session_ref']['id']
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='auth_rejection_to_chats', action='move',
                expected_revision=0, source=row['source'], target_project_id=None))
            scope = directory.threads.view(row['id'])
            await service.send(row['id'], entry(scope, h, 'only_auth_send', 'Synthetic original auth rejection'))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                op = h.sessions.operation(h.pid, sid, 'only_auth_send')
                if op['status'] in {'FAILED', 'UNKNOWN_OUTCOME', 'RETURNED'}:
                    break
                await asyncio.sleep(.05)
            assert op['status'] == 'FAILED'
            before = deepcopy(op)
            for _ in range(3):
                result = service.operation(row['id'], 'only_auth_send')
                assert result['status'] == 'FAILED'
                assert result['failure']['code'] == 'MODEL_AUTH_REQUIRED'
                assert result['response']['source'] == 'SAVED_ORIGINAL_AUTH_REJECTION'
                assert result['automatic_post_replay'] is False and result['model_calls_by_query'] == 0
                assert 'Synthetic model authentication rejection' not in str(result['response'])
            assert h.sessions.operation(h.pid, sid, 'only_auth_send') == before
            rows = ConversationHistory(h.native.app, threads=directory.threads).collect(directory.threads.view(row['id']))[0]
            failures = [r for r in rows if r.get('failure_code') == 'MODEL_AUTH_REQUIRED']
            assert len(failures) == 1 and failures[0]['status'] == 'FAILED'
            assert failures[0]['operation_id'] == 'only_auth_send'
            assert failures[0]['native_message_id'] in before['native_assistant_ids']
            assert len(wire.requests) == 1
        finally:
            await h.close('canonical-auth-rejection')

    try:
        asyncio.run(run())
    finally:
        wire.close()


def setup(scene, wire):
    h = CapabilityScene(scene, wire)
    directory, organization = attach(h)
    state = h.native.app.state
    state.conversation_threads = directory.threads
    state.conversation_runtime = h.sessions.conversations
    service = CanonicalConversations(h.native.app)
    state.canonical_conversations = service
    return h, directory, organization, service


def entry(scope, h, key, text, **extras):
    return ConversationMessage(operation_id=key, conversation_ref=scope['conversation_ref'],
        provider_profile_ref=h.profile, raw_text=text, **extras)


def test_expired_original_resumes_exact_owner_without_replaying_failure_or_expanding_permissions(scene):
    wire = CapabilityWire([{'_http_error': 401}, 'ONLY_THE_NEW_MESSAGE_RETURNED']).start()

    async def run():
        h, directory, organization, service = setup(scene, wire)
        try:
            view, declared = await opened(h, h.pid, 'open_before_expiry')
            sid, native_sid = view['session_ref']['id'], view['original_session_id']
            manifest = deepcopy(h.sessions._manifest(h.pid, sid)[0])
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='expired_owner_to_chats', action='move',
                expected_revision=0, source=row['source'], target_project_id=None))
            scope = directory.threads.view(row['id'])
            await service.send(row['id'], entry(scope, h, 'old_failed_input', 'Keep this original failed input'))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                failed = h.sessions.operation(h.pid, sid, 'old_failed_input')
                if failed['status'] in {'FAILED', 'UNKNOWN_OUTCOME', 'RETURNED'}:
                    break
                await asyncio.sleep(.05)
            assert failed['status'] == 'FAILED' and len(wire.requests) == 1
            saved_failure = deepcopy(failed)
            await h.sessions.disconnect(h.pid, sid, status='EXPIRED')
            expired = h.sessions.get(h.pid, sid)
            assert expired['owner_status'] == 'EXPIRED' and expired['active_operation_id'] is None
            assert expired['configuration_current'] and not expired['connected_owner']
            body = {**declared, 'project_id': None, 'operation_id': 'resume_for_explicit_new_input',
                    'conversation_ref': directory.threads.view(row['id'])['conversation_ref'],
                    'ttl_seconds': 600, 'max_output_tokens': 4096}
            resumed = await service.open(body)
            assert resumed['session_ref'] == view['session_ref']
            assert resumed['original_session_id'] == native_sid
            assert resumed['generation'] == view['generation'] + 1
            assert resumed['connected_owner'] and resumed['observation_is_current']
            assert h.sessions._manifest(h.pid, sid)[0] == manifest
            assert manifest['request']['allowed_effects'] == []
            assert manifest['request']['workspace_ref'] is None
            for _ in range(2):
                receipt = service.open_operation(body['operation_id'], None)
                assert receipt['session']['session_ref'] == view['session_ref']
                assert receipt['owner_started_by_query'] is False and receipt['model_calls_by_query'] == 0
            assert len(wire.requests) == 1
            await service.send(row['id'], entry(directory.threads.view(row['id']), h,
                                               'only_new_input', 'This is a distinct explicit new input'))
            await returned(h, h.pid, sid, 'only_new_input')
            result = service.operation(row['id'], 'only_new_input')
            assert result['response']['text'] == 'ONLY_THE_NEW_MESSAGE_RETURNED'
            assert result['original_source']['session_ref'] == view['session_ref']
            assert h.sessions.operation(h.pid, sid, 'old_failed_input') == saved_failure
            assert len(wire.requests) == 2
        finally:
            await h.close('expired-original-new-message')

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_existing_original_moves_to_chats_then_other_project_and_both_entries_keep_one_owner_and_budget(scene):
    wire = CapabilityWire(['FIRST_SHARED_REPLY', 'SECOND_SHARED_REPLY']).start()

    async def run():
        h, directory, organization, service = setup(scene, wire)
        try:
            view, _ = await opened(h, h.pid, 'open_shared_original')
            sid = view['session_ref']['id']
            process = h.sessions.active[sid]['process']
            manifest_before = deepcopy(h.sessions._manifest(h.pid, sid)[0])
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='to_chats', action='move',
                expected_revision=0, source=row['source'], target_project_id=None))
            scope = directory.threads.view(row['id'])
            assert scope['project_id'] is None
            with pytest.raises(Conflict):
                directory.threads.original_scope(h.pid, sid, scope['conversation_ref'])
            first = entry(scope, h, 'from_main', 'First shared input {"literal":true}')
            response = await service.send(row['id'], first)
            assert response['original_source']['project_id'] == h.pid
            await returned(h, h.pid, sid, 'from_main')
            assert service.operation(row['id'], 'from_main')['response']['text'] == 'FIRST_SHARED_REPLY'
            operation = h.sessions.operation(h.pid, sid, 'from_main')
            assert service.original_admission(manifest_before, operation)['source']['session_ref'] == view['session_ref']
            corrupted = deepcopy(operation); corrupted['request_sha256'] = '0' * 64
            with pytest.raises(Conflict):
                service.original_admission(manifest_before, corrupted)
            target = scene['store'].create_project('Different organization', 'TARGET_ORG_CONTEXT_ONLY')['id']
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='to_target', action='move',
                expected_revision=1, source=row['source'], target_project_id=target))
            moved = directory.threads.view(row['id'])
            assert moved['project_id'] == target
            second = entry(moved, h, 'from_quick', 'Second shared input')
            await service.send(row['id'], second)
            await returned(h, h.pid, sid, 'from_quick')
            final = service.operation(row['id'], 'from_quick')
            assert final['response']['text'] == 'SECOND_SHARED_REPLY'
            assert final['original_source']['session_ref'] == view['session_ref']
            assert h.sessions.active[sid]['process'] is process and process.returncode is None
            assert h.sessions._manifest(h.pid, sid)[0] == manifest_before
            assert len(wire.requests) == 2 and 'TARGET_ORG_CONTEXT_ONLY' in text_of(wire.requests[1])
            assert h.sessions._state(h.pid, sid)['model_calls_reserved'] == 2
            assert scene['actual'].read_bytes() == scene['original']
            assert moved['file_write_authorized'] is False
        finally:
            await h.close('canonical-shared-owner')

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_busy_status_and_exact_stop_use_original_actor_without_an_extra_model_or_stop_replay(scene):
    wire = CapabilityWire([{'hold': 'root_model', 'reply': 'HELD_ORIGINAL_REPLY'}]).start()

    async def run():
        h, directory, organization, service = setup(scene, wire)
        try:
            view, _ = await opened(h, h.pid, 'open_busy_shared')
            sid = view['session_ref']['id']
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='enroll_busy_chats', action='move',
                expected_revision=0, source=row['source'], target_project_id=None))
            scope = directory.threads.view(row['id'])
            await service.send(row['id'], entry(scope, h, 'held_parent', 'Keep the original task active'))
            deadline = asyncio.get_running_loop().time() + 15
            while not wire.requests:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(.05)
            status = await service.send(row['id'], entry(scope, h, 'busy_status', '进度'))
            assert status['operation']['kind'] == 'GUIDANCE'
            assert status['operation']['role'] == 'LOCAL_STATUS' and not status['can_cancel']
            assert len(wire.requests) == 1
            cancelled = await service.cancel(row['id'], 'held_parent')
            key = cancelled['cancel']['operation_id']
            stop = h.sessions.operation(h.pid, sid, key)
            assert stop['request']['expected_active_operation_id'] == 'held_parent'
            assert stop['request']['generation'] == view['generation']
            assert stop['original_abort_acknowledged'] is True
            path = service._path('messages', [row['id'], 'held_parent'])
            frozen = path.read_bytes()
            original = h.sessions.interrupt
            async def no_replay(*args, **kwargs):
                raise AssertionError('Saved cancellation must only inspect the original receipt')
            h.sessions.interrupt = no_replay
            try:
                for _ in range(3):
                    observed = await service.cancel(row['id'], 'held_parent')
                    assert observed['cancel']['operation_id'] == key
                    assert observed['model_calls_by_query'] == 0
            finally:
                h.sessions.interrupt = original
            assert path.read_bytes() == frozen and len(wire.requests) == 1
        finally:
            await h.close('canonical-busy-stop')

    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_two_first_connections_share_one_actor_and_lost_entry_ack_is_read_without_another_model_post(scene):
    wire = CapabilityWire(['ONE_COMMITTED_REPLY']).start()

    async def run():
        h, directory, _, service = setup(scene, wire)
        try:
            created = ConversationCreation(scene['store'], directory.threads).create(
                CreateConversation(operation_id='new_canonical_chat', project_id=None))
            cid = created['conversation_ref']['id']
            scope = directory.threads.view(cid)
            base = dict(project_id=None, conversation_ref=scope['conversation_ref'], provider_profile_ref=h.profile,
                        capability_binding_sha256=h.sessions.configuration()['capability_binding_sha256'])
            one, two = await asyncio.gather(service.open(OpenConversation(operation_id='main_connect', **base)),
                                            service.open(OpenConversation(operation_id='quick_connect', **base)))
            assert one['session_ref'] == two['session_ref'] and one['project_id'] == two['project_id']
            pid, sid = one['project_id'], one['session_ref']['id']
            assert len(h.sessions.active) == 1 and len(wire.requests) == 0
            process = h.sessions.active[sid]['process']
            h.contexts[sid] = h.sessions.active[sid]
            h.identities[sid] = h.sessions._state(pid, sid)['process_identity']
            assert service.open_operation('quick_connect')['session']['session_ref'] == one['session_ref']
            record_path = service._path('connections', [None, 'quick_connect'])
            record_bytes = record_path.read_bytes()
            reader = CanonicalConversations(h.native.app)
            for _ in range(3):
                observed = reader.open_operation('quick_connect')
                assert not observed['owner_started_by_query'] and observed['model_calls_by_query'] == 0
            assert record_path.read_bytes() == record_bytes and not wire.requests
            save, failed = service._save, False

            def fail_entry_ack(path, value, **options):
                nonlocal failed
                if value['kind'] == 'MESSAGE' and value['phase'] == 'OBSERVED' and not failed:
                    failed = True
                    raise OSError('Injected entry acknowledgement persistence failure after real original POST')
                return save(path, value, **options)

            service._save = fail_entry_ack
            body = entry(directory.threads.view(cid), h, 'lost_entry_ack', 'Real input sent exactly once')
            with pytest.raises(OSError):
                await service.send(cid, body)
            service._save = save
            await returned(h, pid, sid, 'lost_entry_ack')
            path = service._path('messages', [cid, 'lost_entry_ack'])
            before = path.read_bytes()
            final = reader.operation(cid, 'lost_entry_ack')
            assert final['status'] == 'RETURNED' and final['response']['text'] == 'ONE_COMMITTED_REPLY'
            assert path.read_bytes() == before
            duplicate = await service.send(cid, body)
            assert duplicate['operation']['id'] == 'lost_entry_ack'
            assert len(wire.requests) == 1 and h.sessions.active[sid]['process'] is process
            with pytest.raises(Conflict):
                await service.send(cid, body.model_copy(update={'raw_text': 'Changed input under same key'}))
            assert len(wire.requests) == 1
        finally:
            await h.close('canonical-concurrent-open')

    try:
        asyncio.run(run())
    finally:
        wire.close()
