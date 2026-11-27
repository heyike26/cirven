"""Saved-file context uses real registered bytes; public CLI test keeps its owner."""
import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from packages.domain.conversation_file_input import ConversationFileInput, freeze_conversation_file
from packages.domain.file_continuity import FileContinuity
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest
from apps.api.conversation_organization import OrganizationChange
from test_agent_sessions import scene
from test_canonical_conversations_cli import setup, entry
from test_public_conversation_runtime_cli import opened, returned
from native_capability_task_source64_fixture import CapabilityWire, text_of


def selected(scene):
    return scene['workspaces'].read_saved_snapshot(scene['pid'], scene['bound']['id'], scene['actual'].name)['source_ref']


def files(scene):
    return FileContinuity(scene['store'], scene['data'], scene['workspaces'])


def test_explicit_source_freezes_saved_bytes_and_rejects_stale_version(scene):
    service = files(scene)
    reference = selected(scene)
    request = {'kind': 'WORKSPACE_FILE', 'source_ref': reference}
    frozen = freeze_conversation_file(service, scene['pid'], request)
    assert frozen['parsed']['text_content'] == scene['actual'].read_text()
    assert frozen['sha256'] == reference['sha256'] and frozen['write_authorized'] is False
    before = deepcopy(frozen)
    scene['actual'].write_text('LATER_USER_MANUAL_SAVE\n')
    with pytest.raises(Conflict, match='version changed'):
        freeze_conversation_file(service, scene['pid'], request)
    assert frozen == before
    saved = service.read_snapshot_bytes(frozen['link_ref'], frozen['snapshot_ref'])
    assert saved['raw'] == scene['original']


def test_context_snapshot_reads_current_registered_bytes_without_new_snapshots_or_writes(scene):
    service = files(scene)
    frozen = freeze_conversation_file(service, scene['pid'], {'kind': 'WORKSPACE_FILE', 'source_ref': selected(scene)})
    roots = [service._path('links'), service._path('snapshots')]
    before = {str(path): path.read_bytes() for root in roots for path in root.rglob('*') if path.is_file()}
    with scene['store'].connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        current = service.context_snapshot(frozen['snapshot_ref'])
    assert current['text_content'] == scene['actual'].read_text()
    assert current['write_authorized'] is False
    assert {str(path): path.read_bytes() for root in roots for path in root.rglob('*') if path.is_file()} == before
    scene['actual'].write_text('USER_CHANGED_FILE_AFTER_CONTEXT_SELECTION\n')
    with pytest.raises(Conflict, match='SOURCE_DRIFT'):
        service.context_snapshot(frozen['snapshot_ref'])
    assert service.read_snapshot_bytes(frozen['link_ref'], frozen['snapshot_ref'])['raw'] == scene['original']


def test_context_graph_selects_actual_file_and_omits_it_after_manual_save(scene):
    from types import SimpleNamespace
    from apps.api.context_graph import ApplicationContextSources
    from packages.domain.context_graph import AssociationChange, ContextGraph, ScopeChange, SelectionInput, SourceRef
    service = files(scene)
    frozen = freeze_conversation_file(service, scene['pid'], {'kind': 'WORKSPACE_FILE', 'source_ref': selected(scene)})
    reader = ApplicationContextSources(SimpleNamespace(state=SimpleNamespace(store=scene['store'], file_continuity=service)))
    graph = ContextGraph(scene['store'], reader.read, lambda _: {'project_id': scene['pid']})
    graph.save_scope('files', ScopeChange(operation_id='file_scope', expected_revision=0,
        project_id=scene['pid'], kind='PROJECT', label='Files'))
    snapshot = frozen['snapshot_ref']
    graph.associate(AssociationChange(operation_id='file_association', expected_revision=0, scope_id='files',
        source=graph.file_source(snapshot, frozen['link_ref'])))
    first = graph.select(SelectionInput(operation_id='before_save', scope_id='files'))
    assert graph.materialize(first['selection_id'], scene['pid'])['items'][0]['text'] == scene['actual'].read_text()
    scene['actual'].write_text('USER_EDIT_WITHOUT_INDEX_REGENERATION\n')
    with pytest.raises(Conflict):
        graph.materialize(first['selection_id'], scene['pid'])
    assert graph.select(SelectionInput(operation_id='after_save', scope_id='files'))['selected'] == []
    assert service.read_snapshot_bytes(frozen['link_ref'], frozen['snapshot_ref'])['raw'] == scene['original']


def test_context_snapshot_never_substitutes_an_equal_byte_link_with_another_origin(scene):
    service = files(scene)
    ref = selected(scene)
    first = service.register_workspace(scene['pid'], ref, {'kind': 'EXPLICIT_FIRST_ORIGIN'})
    first_snapshot = service.observe(first['link_ref'])
    second = service.register_workspace(scene['pid'], ref, {'kind': 'EXPLICIT_SECOND_ORIGIN'})
    second_snapshot = service.observe(second['link_ref'])
    assert first['link_ref'] != second['link_ref']
    assert first_snapshot['snapshot_ref']['id'] == second_snapshot['snapshot_ref']['id']
    assert first_snapshot['snapshot_ref']['sha256'] != second_snapshot['snapshot_ref']['sha256']
    with pytest.raises(NotFound):
        service.context_snapshot(second_snapshot['snapshot_ref'], link_ref=first['link_ref'])
    exact = service.context_snapshot(second_snapshot['snapshot_ref'], link_ref=second['link_ref'])
    assert exact['snapshot_ref'] == second_snapshot['snapshot_ref']
    assert service.context_snapshot(second_snapshot['snapshot_ref'])['snapshot_ref'] == second_snapshot['snapshot_ref']


def test_explicit_link_reads_current_save_and_does_not_authorize_write(scene):
    service = files(scene)
    link = service.register_workspace(scene['pid'], selected(scene), {'kind': 'USER_OPENED_WORKSPACE_FILE'})['link_ref']
    scene['actual'].write_text('MY_LATER_MANUAL_SAVE\n')
    frozen = freeze_conversation_file(service, scene['pid'], {'kind': 'LINKED_FILE', 'link_ref': link})
    assert frozen['parsed']['text_content'] == 'MY_LATER_MANUAL_SAVE\n'
    assert frozen['unsaved_changes'] == 'NOT_OBSERVED' and frozen['write_authorized'] is False
    assert scene['workspaces'].get(scene['pid'], scene['bound']['id'])['allowed_write_paths'] == []
    with pytest.raises(Conflict, match='another execution project'):
        freeze_conversation_file(service, 'another-project', {'kind': 'LINKED_FILE', 'link_ref': link})


@pytest.mark.parametrize('patch', [{'kind': 'LINKED_FILE'}, {'kind': 'UNKNOWN'}, {'extra': 'ignored'},
    {'kind': 'LINKED_FILE', 'link_ref': {'project_id': 'p', 'id': 'a'*64, 'sha256': 'b'*64, 'revision': True}}])
def test_invalid_or_ambiguous_file_choice_is_not_silently_dropped(scene, patch):
    with pytest.raises(ValidationError):
        ConversationFileInput.model_validate({'kind': 'WORKSPACE_FILE', 'source_ref': selected(scene), **patch})


def test_missing_selected_file_keeps_old_snapshot_and_never_reads_other_files(scene):
    service = files(scene)
    frozen = freeze_conversation_file(service, scene['pid'], {'kind': 'WORKSPACE_FILE', 'source_ref': selected(scene)})
    scene['actual'].unlink()
    with pytest.raises((Conflict, NotFound, OSError, ValueError)):
        freeze_conversation_file(service, scene['pid'], {'kind': 'LINKED_FILE', 'link_ref': frozen['link_ref']})
    assert service.read_snapshot_bytes(frozen['link_ref'], frozen['snapshot_ref'])['raw'] == scene['original']


def test_actual_cli_keeps_same_public_owner_and_binds_saved_file_without_wire_in_history(scene):
    wire = CapabilityWire(['已核对选中文件的保存稿。', '已核对同一会话的更新版本。']).start()

    async def run():
        h, directory, organization, service = setup(scene, wire)
        try:
            view, _ = await opened(h, h.pid, 'file_input_open')
            sid = view['session_ref']['id']
            row = directory.resolve({'kind': 'original', 'project_id': h.pid, 'session_ref': view['session_ref']})
            organization.change(row['id'], OrganizationChange(operation_id='organize_file_chat',action='move',expected_revision=0,source=row['source'],target_project_id=None))
            scope = directory.threads.view(row['id'])
            link = h.native.app.state.file_continuity.register_workspace(h.pid, selected(scene), {'kind': 'USER_OPENED_WORKSPACE_FILE'})['link_ref']
            scene['actual'].write_text('EXACT_CURRENT_SAVED_FILE 😀\nMANUAL_CONTENT_TO_KEEP\n')
            original_manifest = deepcopy(h.sessions._manifest(h.pid, sid)[0])
            raw = '请核对输入框中这份文件的当前保存稿，不修改文件。'
            body = entry(scope, h, 'same_owner_file_input', raw, file_input={'kind': 'LINKED_FILE', 'link_ref': link})
            await service.send(row['id'], body)
            final = await returned(h, h.pid, sid, body.operation_id)
            assert len(wire.requests) == 1
            seen = text_of(wire.requests[0])
            assert 'EXACT_CURRENT_SAVED_FILE 😀' in seen and 'MANUAL_CONTENT_TO_KEEP' in seen
            assert 'READONLY_USER_SELECTED_SAVED_FILE' in seen and raw in seen
            operation = h.sessions.operation(h.pid, sid, body.operation_id)
            snapshot = deepcopy(operation['conversation_context']['file_input'])
            assert snapshot['write_authorized'] is False
            assert final['original_session_id'] == view['original_session_id']
            assert h.sessions._manifest(h.pid, sid)[0] == original_manifest
            users = [m for m in final['messages'] if m['info']['role'] == 'user']
            assert len(users) == 1 and [p['text'] for p in users[0]['parts']] == [raw]
            assert users[0]['input_file'] == {'filename': 'real.md'}
            scene['actual'].write_text('USER_CHANGED_FILE_AFTER_FROZEN_MESSAGE\n')
            for _ in range(2):
                saved = service.operation(row['id'], body.operation_id)
                assert saved['status'] == 'RETURNED'
            assert h.sessions.operation(h.pid, sid, body.operation_id)['conversation_context']['file_input'] == snapshot
            assert len(wire.requests) == 1
            history = h.sessions.conversations.history.collect(directory.threads.view(row['id']))[0]
            user = next(r for r in history if r['role'] == 'user' and r.get('operation_id') == body.operation_id)
            assert user['text'] == raw and user['input_file'] == {
                'filename': 'real.md', 'verified': True, 'openable': True}
            scope = directory.threads.view(row['id'])
            resolved = service.resolve_input_file(scope['project_id'], scope['conversation_ref'], h.pid, body.operation_id)
            assert resolved['file_source_ref'] == selected(scene)
            assert resolved['baseline_file_source_ref'] == snapshot['file_source_ref']
            assert len(wire.requests) == 1
            with pytest.raises(Conflict, match='another execution source'):
                service.resolve_input_file(scope['project_id'], scope['conversation_ref'], 'another', body.operation_id)
            outdated = {**scope['conversation_ref'], 'revision': scope['conversation_ref']['revision'] - 1}
            with pytest.raises(Conflict, match='Conversation changed'):
                service.resolve_input_file(scope['project_id'], outdated, h.pid, body.operation_id)
            original_ref = snapshot['file_source_ref']
            stale = entry(scope, h, 'stale_file_followup', '请在原会话继续核对旧选稿。',
                file_input={'kind': 'WORKSPACE_FILE', 'source_ref': original_ref})
            with pytest.raises(Conflict, match='version changed'):
                await service.send(row['id'], stale)
            assert len(wire.requests) == 1
            current_ref = selected(scene)
            followup = entry(scope, h, 'saved_file_followup', '请继续核对我刚保存的版本。',
                file_input={'kind': 'WORKSPACE_FILE', 'source_ref': current_ref})
            await service.send(row['id'], followup)
            continued = await returned(h, h.pid, sid, followup.operation_id)
            assert continued['original_session_id'] == view['original_session_id']
            assert len(wire.requests) == 2
            assert 'USER_CHANGED_FILE_AFTER_FROZEN_MESSAGE' in text_of(wire.requests[1])
            assert h.sessions.operation(h.pid, sid, followup.operation_id)['conversation_context']['file_input']['file_source_ref'] == current_ref
            assert scene['actual'].read_text() == 'USER_CHANGED_FILE_AFTER_FROZEN_MESSAGE\n'
        finally:
            await h.close('original-file-input')
    try:
        asyncio.run(run())
    finally:
        wire.close()
