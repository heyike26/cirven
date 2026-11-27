"""Fresh controller over actual original CLI storage; only inference is a wire fixture."""
import asyncio
import json

import pytest
from copy import deepcopy

from apps.api.canonical_conversations import ConversationContinuation
from apps.api.conversation_organization import OrganizationChange
from packages.domain.tasks import digest
from packages.domain.store import Conflict, InvalidInput, NotFound
from test_agent_sessions import scene
from test_canonical_conversations_cli import setup, entry
from test_public_conversation_runtime_cli import opened, returned
from native_capability_task_source64_fixture import CapabilityWire


def test_cold_local_progress_does_not_start_owner_and_explicit_new_input_resumes_same_original(scene):
    wire = CapabilityWire(['BEFORE_RESTART_REPLY', 'AFTER_RESTART_REPLY']).start()

    async def run():
        first = second = None
        evidence = {'model_calls_paid': 0, 'native_windows': False}
        try:
            first, directory, organization, service = setup(scene, wire)
            view, _ = await opened(first, first.pid, 'cold_open_original')
            sid = view['session_ref']['id']
            original_process = first.sessions.active[sid]['process']
            row = directory.resolve({'kind': 'original', 'project_id': first.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='cold_enroll', action='move',
                expected_revision=0, source=row['source'], target_project_id=None))
            scope = directory.threads.view(row['id']); cid = row['id']
            result = await service.send(cid, entry(scope, first, 'before_cold', 'First user input before normal shutdown'))
            await returned(first, first.pid, sid, 'before_cold')
            parent = service.operation(cid, 'before_cold'); reference = parent['canonical_message_ref']
            original_operation = digest(first.sessions.operation(first.pid, sid, 'before_cold'))
            manifest = deepcopy(first.sessions._manifest(first.pid, sid)[0])
            original_native_session = first.sessions.get(first.pid, sid)['original_session_id']
            await first.close('cold-original-before')
            assert original_process.returncode is not None
            second, directory2, organization2, restored = setup(scene, wire)
            assert second.sessions.active == {}
            for _ in range(3):
                observed = restored.operation(cid, 'before_cold')
                assert observed['operation']['status'] == 'RETURNED'
            assert len(wire.requests) == 1 and second.sessions.active == {}
            evidence['initial_cold_can_continue'] = observed['can_continue']
            progress = await restored.continuation(cid, 'before_cold', ConversationContinuation(operation_id='cold_progress',
                expected_message_ref=reference, raw_text='进度'))
            assert progress['operation']['role'] == 'LOCAL_STATUS'
            assert progress['operation']['local_status']['coverage'] == 'LAST_SAVED_OWNER_AND_OPERATION_NOT_CURRENT'
            assert progress['operation']['local_status']['observation_is_current'] is False
            assert progress['operation']['native_post_count'] == 0
            assert second.sessions.active == {} and len(wire.requests) == 1
            assert progress['can_continue'] is True
            body = ConversationContinuation(operation_id='after_cold', expected_message_ref=reference,
                raw_text='Continue this same discussion after restart; preserve the original file 😀')
            save = restored._save
            lost_ack = False
            def lose_ack(path, value, **options):
                nonlocal lost_ack
                if not lost_ack and value.get('phase') == 'OBSERVED' and value['request']['operation_id'] == 'after_cold':
                    lost_ack = True
                    raise OSError('Lost cold continuation ACK after original dispatch')
                return save(path, value, **options)
            restored._save = lose_ack
            with pytest.raises(OSError):
                await restored.continuation(cid, 'before_cold', body)
            restored._save = save
            await returned(second, second.pid, sid, 'after_cold')
            completed = restored.operation(cid, 'after_cold')
            assert completed['response']['text'] == 'AFTER_RESTART_REPLY'
            assert completed['operation']['kind'] == 'MESSAGE'
            assert completed['original_source']['session_ref'] == view['session_ref']
            assert completed['original_source']['generation'] == view['generation'] + 1
            assert second.sessions.get(second.pid, sid)['original_session_id'] == original_native_session
            assert second.sessions._manifest(second.pid, sid)[0] == manifest
            assert digest(second.sessions.operation(second.pid, sid, 'before_cold')) == original_operation
            assert second.sessions._state(second.pid, sid)['model_calls_reserved'] == 2
            for _ in range(3):
                duplicate = await restored.continuation(cid, 'before_cold', body)
                assert duplicate['canonical_message_ref'] == completed['canonical_message_ref']
            assert len(wire.requests) == 2 and len(second.sessions.active) == 1
            await second.sessions.disconnect(second.pid, sid)
            generation = second.sessions.get(second.pid, sid)['generation']
            config = deepcopy(second.config)
            second.config['model_id'] = 'changed_without_confirmation'
            assert restored.operation(cid, 'after_cold')['can_continue'] is False
            with pytest.raises((Conflict, InvalidInput)):
                await restored.continuation(cid, 'after_cold', ConversationContinuation(operation_id='cold_changed_config',
                    expected_message_ref=completed['canonical_message_ref'], raw_text='Do not start the changed configuration'))
            assert second.sessions.active == {} and len(wire.requests) == 2
            assert second.sessions.get(second.pid, sid)['generation'] == generation
            second.config = config
            selected = directory2.resolve({'kind': 'original', 'project_id': second.pid, 'session_ref': view['session_ref']})
            target = scene['store'].create_project('Cold move target', 'organization only')['id']
            current = directory2.threads.view(cid)
            organization2.change(cid, OrganizationChange(operation_id='cold_move', action='move',
                expected_revision=current['conversation_ref']['revision'], source=selected['source'], target_project_id=target))
            with pytest.raises(Conflict):
                await restored.continuation(cid, 'after_cold', ConversationContinuation(operation_id='cold_moved_parent',
                    expected_message_ref=completed['canonical_message_ref'], raw_text='Old parent scope must not start an owner'))
            current = directory2.threads.view(cid)
            organization2.change(cid, OrganizationChange(operation_id='cold_archive', action='archive',
                expected_revision=current['conversation_ref']['revision'], source=selected['source']))
            assert restored.operation(cid, 'after_cold')['can_continue'] is False
            current = directory2.threads.view(cid)
            organization2.change(cid, OrganizationChange(operation_id='cold_delete', action='delete',
                expected_revision=current['conversation_ref']['revision'], source=selected['source'],
                delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES'))
            with pytest.raises(NotFound):
                await restored.continuation(cid, 'after_cold', ConversationContinuation(operation_id='cold_deleted_parent',
                    expected_message_ref=completed['canonical_message_ref'], raw_text='Deleted discussion must not revive'))
            assert second.sessions.active == {} and len(wire.requests) == 2
            assert second.sessions.get(second.pid, sid)['generation'] == generation
            assert scene['actual'].read_bytes() == scene['original']
            evidence.update(status='PASS',original_generation=view['generation'],resumed_generation=completed['original_source']['generation'],
                same_native_session=True,actual_model_requests=len(wire.requests),manifest_unchanged=True,old_operation_unchanged=True)
        finally:
            if second is not None:
                await second.close('cold-original-after')
            if first is not None and not first.sessions.closed:
                await first.close('cold-original-cleanup')
            (scene['data'] / 'cold-continuation-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    try:
        asyncio.run(run())
    finally:
        wire.close()
