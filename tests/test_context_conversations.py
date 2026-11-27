"""Real context DB/documents and conversation metadata. Authored, not yet run.

No model, original owner startup or native/IDE application is exercised here.
"""
from dataclasses import dataclass

import pytest

from apps.api.conversation_creation import ConversationCreation, CreateConversation
from packages.domain.conversation_threads import ConversationThreads
from packages.domain.context_conversation_models import (ConversationLinkChange,
    ConversationSettingsChange, AutomaticContextInput)
from packages.domain.context_graph import AssociationChange, NoteChange, ScopeChange, SelectionInput, SourceRef, text_hash
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest
from test_context_graph import context_scene, link


@dataclass
class Scene:
    graph: object
    store: object
    pid: str
    other: str
    threads: object
    ref: dict
    note: dict
    source: object


@pytest.fixture
def conversation_scene(context_scene):
    graph, store, pid, other, _, source = context_scene
    origin = link(graph, source)['origin_id']
    note = graph.note(NoteChange(operation_id='reviewed_rule', scope_id='project', title='Adopted constraint',
        text='PRESERVE_REVIEWED_CONSTRAINT_AND_MANUAL_EDITS', basis=[origin]))
    graph.associate(AssociationChange(operation_id='approve_reviewed_rule', scope_id='project', expected_revision=1,
        source=SourceRef(kind='NOTE', project_id=pid, object_id=note['origin_id'])))
    threads = ConversationThreads(store)
    ref = ConversationCreation(store, threads).create(CreateConversation(operation_id='create_discussion',
        project_id=pid, title='Actual metadata discussion'))['conversation_ref']
    graph.conversation_reader = threads.view
    return Scene(graph, store, pid, other, threads, ref, note, source)


def choose(s, key, text='Continue the current implementation'):
    request = AutomaticContextInput(operation_id=key, project_id=s.pid, conversation_id=s.ref['id'],
        input_sha256=digest({'id': key, 'raw_text': text}), text_sha256=text_hash(text))
    return s.graph.automatic_context(request, text), request


def associate(s, scope, key, expected=0, state='ACTIVE', preference=None):
    return s.graph.link_conversation(ConversationLinkChange(operation_id=key, scope_id=scope,
        conversation_ref=s.ref, expected_revision=expected, state=state,
        preference_expected_revision=preference))


def test_hierarchical_discussion_references_and_preference_leave_original_identity_and_sources_unchanged(conversation_scene):
    s = conversation_scene
    before = s.threads.view(s.ref['id'])
    s.graph.save_scope('task', ScopeChange(operation_id='task_scope', expected_revision=0,
        project_id=s.pid, parent_id='topic', kind='TASK', label='Implementation subtask'))
    first = associate(s, 'topic', 'topic_reference')
    selected = associate(s, 'task', 'task_reference', preference=0)
    state = s.graph.conversation_context(s.ref['id'])
    assert state['routing']['scope_id'] == 'task' and state['routing']['status'] == 'PREFERRED'
    assert selected['preferences_revision'] == 1
    assert selected['owner_changed'] is False and selected['files_moved'] is False
    assert s.threads.view(s.ref['id']) == before
    assert {item['id'] for item in s.graph.scope_conversations('topic')['children']} == {'task'}
    assert s.graph.scope_conversations('topic')['items'][0]['discussion']['conversation_ref'] == s.ref
    assert s.store.document(s.pid, s.source.object_id)['sha256'] == s.source.sha256
    assert first['conversation_link_id'] != selected['conversation_link_id']


def test_preference_cas_failure_rolls_back_new_link_and_preserves_original_operation_receipts(conversation_scene):
    s = conversation_scene
    s.graph.conversation_preferences(ConversationSettingsChange(operation_id='settings_first',
        conversation_ref=s.ref, expected_revision=0, retrieval_mode='MANUAL'))
    with pytest.raises(Conflict):
        associate(s, 'topic', 'stale_atomic_link', preference=0)
    assert s.graph.conversation_context(s.ref['id'])['links'] == []
    with pytest.raises(NotFound):
        s.graph.operation('stale_atomic_link')
    request = ConversationLinkChange(operation_id='exact_atomic_link', scope_id='topic',
        conversation_ref=s.ref, expected_revision=0, preference_expected_revision=1)
    first = s.graph.link_conversation(request)
    assert s.graph.link_conversation(request) == first
    with pytest.raises(Conflict):
        s.graph.link_conversation(request.model_copy(update={'state': 'RETRACTED'}))
    assert s.graph.conversation_context(s.ref['id'])['routing']['settings']['retrieval_mode'] == 'MANUAL'


def test_automatic_selection_uses_adopted_scope_notes_without_copying_question_or_waking_a_model(conversation_scene):
    s = conversation_scene
    text = 'UNIQUE_PRIVATE_QUESTION_NOT_TO_COPY_INTO_CONTEXT_INDEX'
    selected, request = choose(s, 'automatic_note', text)
    assert selected['selection_status'] == 'SELECTED'
    assert [item['origin_id'] for item in selected['selected']] == [s.note['origin_id']]
    assert selected['selection_mode'] == 'AUTO' and selected['model_calls'] == 0
    materialized = s.graph.materialize(selected['context_selection_id'], s.pid, conversation_id=s.ref['id'])
    assert materialized['items'][0]['text'] == 'PRESERVE_REVIEWED_CONSTRAINT_AND_MANUAL_EDITS'
    with s.store.connect() as db:
        assert not any(text in row[0] for row in db.execute('SELECT result_json FROM context_operations'))
        assert not any(text in row[0] for row in db.execute('SELECT selection_json FROM context_selections'))
    assert s.graph.automatic_context(request, text) == selected
    with pytest.raises(Conflict):
        s.graph.automatic_context(request, text + ' CHANGED')
    with pytest.raises(Conflict):
        s.graph.automatic_context(request.model_copy(update={'input_sha256': 'f' * 64}), text)


def test_ambiguous_sibling_scopes_need_an_explicit_preference_and_withdrawal_invalidates_frozen_selection(conversation_scene):
    s = conversation_scene
    s.graph.save_scope('sibling', ScopeChange(operation_id='sibling_scope', expected_revision=0,
        project_id=s.pid, parent_id='project', kind='TOPIC', label='Other topic'))
    first = associate(s, 'topic', 'first_topic')
    associate(s, 'sibling', 'other_topic')
    ambiguous, _ = choose(s, 'ambiguous_selection')
    assert ambiguous['context_selection_id'] is None and ambiguous['selection_status'] == 'AMBIGUOUS_ASSOCIATIONS'
    preferred = associate(s, 'topic', 'explicit_preference', expected=first['revision'], preference=0)
    selected, _ = choose(s, 'scoped_selection')
    assert selected['scope_id'] == 'topic' and selected['selected']
    associate(s, 'topic', 'withdraw_preferred', expected=preferred['revision'], state='RETRACTED')
    with pytest.raises(Conflict):
        s.graph.materialize(selected['context_selection_id'], s.pid, conversation_id=s.ref['id'])
    after, _ = choose(s, 'after_withdrawal')
    assert after['selection_status'] == 'PREFERRED_SCOPE_INACTIVE' and after['context_selection_id'] is None


def test_discussion_off_and_manual_modes_apply_to_new_inputs_and_exact_previous_choices(conversation_scene, monkeypatch):
    s = conversation_scene
    manual = s.graph.select(SelectionInput(operation_id='manual_choice', scope_id='project', conversation_id=s.ref['id']))
    s.graph.conversation_preferences(ConversationSettingsChange(operation_id='pause', conversation_ref=s.ref,
        expected_revision=0, retrieval_mode='OFF'))
    with pytest.raises(Conflict):
        s.graph.materialize(manual['selection_id'], s.pid, conversation_id=s.ref['id'])
    original = s.graph.source_reader
    monkeypatch.setattr(s.graph, 'source_reader', lambda *_: pytest.fail('Paused retrieval must not read source bodies'))
    paused, _ = choose(s, 'paused_input')
    assert paused['selection_status'] == 'OFF' and paused['context_selection_id'] is None
    s.graph.conversation_preferences(ConversationSettingsChange(operation_id='manual_mode', conversation_ref=s.ref,
        expected_revision=1, retrieval_mode='MANUAL'))
    not_automatic, _ = choose(s, 'manual_mode_input')
    assert not_automatic['selection_status'] == 'MANUAL'
    monkeypatch.setattr(s.graph, 'source_reader', original)
    explicit = s.graph.select(SelectionInput(operation_id='new_explicit_choice', scope_id='project', conversation_id=s.ref['id']))
    assert explicit['selected'] and explicit['conversation_preferences_basis']['revision'] == 2


def test_moved_navigation_preserves_old_admitted_scope_but_withdrawal_remains_available(conversation_scene):
    s = conversation_scene
    edge = associate(s, 'topic', 'before_move', preference=0)
    selected, _ = choose(s, 'old_admitted_context')
    frozen = s.threads.view(s.ref['id'])
    with s.store.connect() as db:
        s.threads.move(db, s.ref['id'], s.other, updated_at='2026-11-02T12:00:00+00:00')
        db.execute('UPDATE conversation_organization SET revision=revision+1 WHERE conversation_id=?', (s.ref['id'],))
    state = s.graph.conversation_context(s.ref['id'])
    assert state['links'][0]['current_project_matches'] is False and state['routing']['scope_id'] is None
    with pytest.raises(Conflict):
        s.graph.materialize(selected['context_selection_id'], s.pid, conversation_id=s.ref['id'])
    assert s.graph.materialize(selected['context_selection_id'], s.pid, conversation_id=s.ref['id'],
                               frozen_conversation_scope=frozen)['items']
    associate(s, 'topic', 'withdraw_after_move', expected=edge['revision'], state='RETRACTED')
    with pytest.raises(Conflict):
        s.graph.materialize(selected['context_selection_id'], s.pid, conversation_id=s.ref['id'],
                            frozen_conversation_scope=frozen)


def test_cross_project_metadata_reference_does_not_move_discussion_or_expand_automatic_context(conversation_scene):
    s = conversation_scene
    s.graph.save_scope('other_project', ScopeChange(operation_id='other_scope', expected_revision=0,
        project_id=s.other, kind='PROJECT', label='Another project'))
    request = ConversationLinkChange(operation_id='foreign_reference', scope_id='other_project',
        conversation_ref=s.ref, expected_revision=0)
    with pytest.raises(Conflict):
        s.graph.link_conversation(request)
    reference = s.graph.link_conversation(request.model_copy(update={'allow_reference_to_other_project': True}))
    assert reference['reference_only'] is True and reference['owner_changed'] is False
    assert s.threads.view(s.ref['id'])['project_id'] == s.pid
    assert s.graph.scope_conversations('other_project')['items'][0]['current_project_matches'] is False
    chosen, _ = choose(s, 'no_foreign_context')
    assert chosen['scope_id'] == 'project' and chosen['project_id'] == s.pid
    with pytest.raises(Conflict):
        s.graph.link_conversation(request.model_copy(update={'operation_id': 'foreign_preference',
            'expected_revision': 1, 'allow_reference_to_other_project': True, 'preference_expected_revision': 0}))
    assert s.graph.conversation_context(s.ref['id'])['routing']['settings']['preferred_scope_id'] is None


def test_omission_receipt_is_bounded_and_historical_metadata_stays_readable_after_policy_change(conversation_scene):
    s = conversation_scene
    for index in range(140):
        document = s.store.save_document(s.pid, f'unmatched-source-{index}.md', b'Unrelated original material.', 'UPLOAD')
        source = SourceRef(kind='DOCUMENT', project_id=s.pid, object_id=document['id'], revision=1, sha256=document['sha256'])
        s.graph.associate(AssociationChange(operation_id='unmatched_' + str(index), scope_id='project', expected_revision=0, source=source))
    selected, _ = choose(s, 'saved_selection')
    s.graph.conversation_preferences(ConversationSettingsChange(operation_id='after_selection_off',
        conversation_ref=s.ref, expected_revision=0, retrieval_mode='OFF'))
    receipt = s.graph.selection_receipt(selected['context_selection_id'], s.pid)
    assert receipt['historical_receipt'] is True and receipt['current_sources_verified'] is False
    assert receipt['origin_labels'][s.note['origin_id']] == 'Adopted constraint'
    assert len(receipt['omitted']) == 128 and receipt['omitted_truncated'] is True
    assert sum(receipt['omission_counts'].values()) > 128 and receipt['selection_id'] == selected['context_selection_id']
    with pytest.raises(NotFound):
        s.graph.selection_receipt(selected['context_selection_id'], s.other)
