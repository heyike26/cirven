"""Authored for concentrated debug: real SQLite and original source services.

No browser/native/model claims. Exact grants do not create execution owners.
"""
import json
import pytest
from test_context_graph import context_scene, link, selection
from test_context_file_relations import relation_scene, register
from packages.domain.context_graph import (AssociationChange, ForgetInput, NoteChange, ScopeChange,
    SelectionInput, SourceReadChange, SourceReadPreview, SourceRef)
from packages.domain.context_policy_models import GlobalPolicyChange
from packages.domain.store import Conflict
from packages.domain.tasks import digest


def foreign_scope(graph, project, ident='other'):
    graph.save_scope(ident, ScopeChange(operation_id='scope_'+ident, expected_revision=0,
        kind='PROJECT' if project is not None else 'GLOBAL', project_id=project, label=ident))


def review(graph, source, target='other', cid=None):
    return graph.preview_read_grant(SourceReadPreview(scope_id=target, source=source, conversation_id=cid))


def grant_request(preview, key='grant', **changes):
    return SourceReadChange(operation_id=key, scope_id=preview['scope_id'], source=preview['source_ref'],
        conversation_id=preview['conversation_id'], expected_revision=preview['expected_revision'],
        expected_grant_revision=preview['expected_grant_revision'], review_sha256=preview['review_sha256'],
        restore_file_exclusions=preview['restore_file_exclusions'], **changes)


def revoke(graph, target='other', key='revoke'):
    row = graph.read_grants(target)['items'][0]
    return graph.change_read_grant(SourceReadChange(operation_id=key, scope_id=target,
        source=row['source_ref'], conversation_id=row['conversation_id'], expected_revision=row['association_revision'],
        expected_grant_revision=row['revision'], review_sha256=row['review_sha256'], state='RETRACTED'))


def test_defaults_inherit_without_sharing_global_contents_and_project_override_survives_reset(context_scene):
    graph, _, project, _, _, _ = context_scene
    foreign_scope(graph, None, 'global')
    note = graph.note(NoteChange(operation_id='global_note', scope_id='global', title='Global note', text='GLOBAL_PRIVATE'))
    graph.associate(AssociationChange(operation_id='approve_global_note', scope_id='global', expected_revision=1,
        source=SourceRef(kind='NOTE', project_id=None, object_id=note['origin_id'])))
    baseline = graph.global_defaults()
    assert baseline['revision'] == 0 and baseline['policy'] == {}
    request = GlobalPolicyChange(operation_id='defaults', expected_revision=0,
        policy={'capture': False, 'retrieve': False, 'char_budget': 42})
    saved = graph.save_global_defaults(request)
    assert graph.save_global_defaults(request) == saved
    assert saved['source_contents_shared'] is False and saved['execution_authorized'] is False
    assert graph.scope('topic')['effective_policy']['capture'] is False
    assert graph.scope('topic')['policy_basis']['retrieve'] == 'GLOBAL_DEFAULTS'
    graph.save_scope('project', ScopeChange(operation_id='override', expected_revision=1, kind='PROJECT',
        project_id=project, label='Project', policy={'retrieve': True, 'char_budget': 9000}))
    assert graph.scope('topic')['effective_policy']['retrieve'] is True
    assert graph.scope('topic')['policy_basis']['retrieve'] == 'project'
    graph.save_global_defaults(GlobalPolicyChange(operation_id='reset', expected_revision=1, policy={}))
    assert graph.scope('topic')['effective_policy']['char_budget'] == 9000
    assert graph.scope('topic')['effective_policy']['capture'] is True
    assert selection(graph)['selected'] == []
    with pytest.raises(Conflict):
        graph.save_global_defaults(GlobalPolicyChange(operation_id='stale', expected_revision=0, policy={}))


def test_narrowed_current_defaults_stop_frozen_selection_but_explicit_project_override_is_respected(context_scene):
    graph, _, project, _, _, source = context_scene
    link(graph, source)
    frozen = selection(graph)
    graph.save_global_defaults(GlobalPolicyChange(operation_id='limit', expected_revision=0, policy={'char_budget': 1}))
    with pytest.raises(Conflict, match='budget'):
        graph.materialize(frozen['selection_id'], project)
    graph.save_global_defaults(GlobalPolicyChange(operation_id='reset', expected_revision=1, policy={}))
    assert graph.materialize(frozen['selection_id'], project)['items']
    graph.save_global_defaults(GlobalPolicyChange(operation_id='off', expected_revision=2, policy={'retrieve': False}))
    with pytest.raises(Conflict, match='disabled'):
        graph.materialize(frozen['selection_id'], project)
    graph.save_scope('project', ScopeChange(operation_id='explicit', expected_revision=1, kind='PROJECT',
        project_id=project, label='Project', policy={'retrieve': True}))
    fresh = selection(graph, operation_id='explicit_selection')
    assert graph.materialize(fresh['selection_id'], project)['items']


def test_preview_is_read_only_and_grant_preserves_original_source_project_and_exact_bytes(context_scene):
    graph, store, project, other, document, source = context_scene
    foreign_scope(graph, other)
    with pytest.raises(Conflict):
        link(graph, source, 'other')
    before = store.document(project, document['id'])['raw_bytes']
    preview = review(graph, source.model_copy(update={'offset': 5, 'length': 12}))
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM context_read_grants').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM context_origins').fetchone()[0] == 0
    request = grant_request(preview)
    accepted = graph.change_read_grant(request)
    assert graph.change_read_grant(request) == accepted
    chosen = graph.select(SelectionInput(operation_id='choose_other', scope_id='other'))
    material = graph.materialize(chosen['selection_id'], other)
    assert material['items'][0]['text'] == before.decode()[5:17]
    assert material['items'][0]['source_project_id'] == project
    assert material['items'][0]['source_ref']['project_id'] == project
    assert material['items'][0]['read_grant_ref']['revision'] == 1
    assert accepted['execution_authorized'] is False and accepted['original_project_changed'] is False
    assert store.document(project, document['id'])['raw_bytes'] == before
    with store.connect() as db:
        origin = db.execute('SELECT * FROM context_origins WHERE id=?', (accepted['origin_id'],)).fetchone()
        assert origin['project_id'] == project
        assert preview['text'] not in json.dumps(graph.operation('grant'))


def test_scope_or_source_change_after_review_rejects_commit_atomically(context_scene):
    graph, store, project, other, document, source = context_scene
    foreign_scope(graph, other)
    preview = review(graph, source)
    graph.save_scope('other', ScopeChange(operation_id='rename_target', expected_revision=1,
        kind='PROJECT', project_id=other, label='A new scope revision'))
    with pytest.raises(Conflict, match='preview'):
        graph.change_read_grant(grant_request(preview, 'stale_review'))
    fresh = review(graph, source)
    store.save_document(project, document['filename'], b'Changed by the user', 'UPLOAD',
        document_id=document['id'], expected_revision=1)
    with pytest.raises(Conflict):
        graph.change_read_grant(grant_request(fresh, 'stale_source'))
    assert graph.read_grants('other')['items'] == []


def test_stale_source_can_still_be_withdrawn_and_old_selector_stays_invalid_after_reapproval(context_scene):
    graph, store, project, other, document, source = context_scene
    foreign_scope(graph, other)
    graph.change_read_grant(grant_request(review(graph, source)))
    chosen = graph.select(SelectionInput(operation_id='before_revoke', scope_id='other'))
    revoke(graph)
    with pytest.raises(Conflict):
        graph.materialize(chosen['selection_id'], other)
    graph.change_read_grant(grant_request(review(graph, source), 'approve_again'))
    with pytest.raises(Conflict):
        graph.materialize(chosen['selection_id'], other)
    store.save_document(project, document['filename'], b'New original bytes', 'UPLOAD',
        document_id=document['id'], expected_revision=1)
    current = graph.select(SelectionInput(operation_id='changed_source', scope_id='other'))
    assert current['selected'] == [] and current['omission_counts']['SOURCE_UNAVAILABLE_OR_CHANGED'] == 1
    revoke(graph, key='withdraw_stale_original')
    assert graph.read_grants('other')['items'][0]['state'] == 'RETRACTED'


def test_reviewed_note_fragment_is_bounded_and_revoke_blocks_derived_notes_without_revival(context_scene):
    graph, store, project, other, _, _ = context_scene
    foreign_scope(graph, other)
    original_text = 'PUBLIC:KEEP\nPRIVATE_UNSELECTED_DETAILS'
    note = graph.note(NoteChange(operation_id='source_note', scope_id='project', title='Original note', text=original_text))
    ref = SourceRef(kind='NOTE', project_id=project, object_id=note['origin_id'], offset=0, length=11)
    preview = review(graph, ref)
    assert preview['source_ref']['kind'] == 'NOTE_FRAGMENT' and preview['text'] == 'PUBLIC:KEEP'
    accepted = graph.change_read_grant(grant_request(preview))
    dependency = accepted['origin_id']
    with pytest.raises(Conflict):
        graph.note(NoteChange(operation_id='ungranted_basis', scope_id='other', title='Derived', text='Keep user edits', basis=[dependency]))
    derived = graph.note(NoteChange(operation_id='derived_note', scope_id='other', title='Derived', text='Keep user edits',
        basis=[dependency], basis_grants=[{'origin_id': dependency, 'id': accepted['grant_id'], 'revision': accepted['grant_revision']}]))
    graph.associate(AssociationChange(operation_id='approve_derived', scope_id='other', expected_revision=1,
        source=SourceRef(kind='NOTE', project_id=other, object_id=derived['origin_id'])))
    chosen = graph.select(SelectionInput(operation_id='with_derived', scope_id='other'))
    texts = [row['text'] for row in graph.materialize(chosen['selection_id'], other)['items']]
    assert set(texts) == {'PUBLIC:KEEP', 'Keep user edits'}
    revoke(graph)
    assert graph.select(SelectionInput(operation_id='revoked', scope_id='other'))['selected'] == []
    graph.change_read_grant(grant_request(review(graph, ref), 'reapprove_fragment'))
    renewed = graph.select(SelectionInput(operation_id='renewed', scope_id='other'))
    assert [row['origin_id'] for row in renewed['selected']] == [dependency]
    forgotten = graph.forget(ForgetInput(operation_id='forget_original_note', scope_id='project', origin_id=note['origin_id']))
    assert {note['origin_id'], dependency, derived['origin_id']} <= set(forgotten['forgotten_origins'])
    with store.connect() as db:
        assert db.execute('SELECT text_content FROM context_notes WHERE origin_id=?', (derived['origin_id'],)).fetchone()[0] == ''
    assert graph.read_grants('other')['items'][0]['state'] == 'RETRACTED'


def test_global_read_grant_requires_explicit_global_selection_and_never_changes_source_identity(context_scene):
    graph, _, project, _, _, source = context_scene
    foreign_scope(graph, None, 'global')
    graph.change_read_grant(grant_request(review(graph, source, 'global')))
    ordinary = selection(graph)
    assert ordinary['selected'] == []
    explicit = selection(graph, operation_id='explicit_global', include_global=True)
    assert graph.materialize(explicit['selection_id'], project)['items'][0]['source_project_id'] == project
    revoke(graph, target='global')
    with pytest.raises(Conflict):
        graph.materialize(explicit['selection_id'], project)


def test_legacy_note_request_hash_remains_stable_when_no_grants_are_supplied(context_scene):
    graph, _, _, _, _, _ = context_scene
    request = NoteChange(operation_id='legacy', scope_id='project', title='Manual', text='Original')
    body = request.model_dump()
    body.pop('basis_grants')
    result = graph.note(request)
    assert result['request_sha256'] == digest({'kind': 'note', 'body': body})
    assert graph.note(request) == result


def test_discussion_only_grant_does_not_leak_into_sibling_or_scope_wide_derived_note(context_scene):
    from apps.api.conversation_creation import ConversationCreation, CreateConversation
    from packages.domain.conversation_threads import ConversationThreads
    graph, store, _, other, _, source = context_scene
    foreign_scope(graph, other)
    threads = ConversationThreads(store)
    creator = ConversationCreation(store, threads)
    cid = creator.create(CreateConversation(operation_id='discussion_one', project_id=other, title='One'))['conversation_ref']['id']
    sibling = creator.create(CreateConversation(operation_id='discussion_two', project_id=other, title='Two'))['conversation_ref']['id']
    graph.conversation_reader = threads.view
    accepted = graph.change_read_grant(grant_request(review(graph, source, cid=cid)))
    assert graph.select(SelectionInput(operation_id='this_discussion', scope_id='other', conversation_id=cid))['selected']
    assert graph.select(SelectionInput(operation_id='sibling', scope_id='other', conversation_id=sibling))['selected'] == []
    assert graph.select(SelectionInput(operation_id='scope_wide', scope_id='other'))['selected'] == []
    with pytest.raises(Conflict, match='scope-wide'):
        graph.note(NoteChange(operation_id='widen_note', scope_id='other', title='Derived', text='Do not widen',
            basis=[accepted['origin_id']], basis_grants=[{'origin_id': accepted['origin_id'], 'id': accepted['grant_id'], 'revision': 1}]))


def test_shared_file_exclusion_survives_save_and_requires_current_explicit_restoration(relation_scene):
    s = relation_scene
    (s.root / 'manual.md').write_text('Original manual text')
    source, _ = register(s, 'manual.md', 'original_file')
    other = s.store.create_project('Other project', '')['id']
    foreign_scope(s.graph, other)
    s.graph.change_read_grant(grant_request(review(s.graph, source)))
    old = s.graph.select(SelectionInput(operation_id='old_file', scope_id='other'))
    row = s.graph.read_grants('other')['items'][0]
    s.graph.change_read_grant(SourceReadChange(operation_id='exclude_file', scope_id='other', source=row['source_ref'],
        expected_revision=row['association_revision'], expected_grant_revision=row['revision'],
        review_sha256=row['review_sha256'], state='EXCLUDED'))
    (s.root / 'manual.md').write_text('New manual text, preserved')
    fresh_source, _ = register(s, 'manual.md', 'new_file')
    preview = review(s.graph, fresh_source)
    assert len(preview['restore_file_exclusions']) == 1
    with pytest.raises(Conflict, match='exclusion'):
        s.graph.change_read_grant(grant_request(preview, 'unreviewed_restore').model_copy(update={'restore_file_exclusions': []}))
    accepted = s.graph.change_read_grant(grant_request(preview, 'restore_reviewed_file'))
    chosen = s.graph.select(SelectionInput(operation_id='new_file_selection', scope_id='other'))
    assert [item['origin_id'] for item in chosen['selected']] == [accepted['origin_id']]
    with pytest.raises(Conflict):
        s.graph.materialize(old['selection_id'], other)
    assert (s.root / 'manual.md').read_text() == 'New manual text, preserved'


def test_privacy_defaults_can_be_disabled_at_normal_storage_capacity(context_scene):
    from packages.domain.context_graph import MAX_STORAGE_BYTES
    graph, store, _, _, _, _ = context_scene
    with store.connect() as db:
        db.execute('UPDATE context_storage_budget SET reserved_bytes=? WHERE singleton=1', (MAX_STORAGE_BYTES,))
    result = graph.save_global_defaults(GlobalPolicyChange(operation_id='disable_at_capacity', expected_revision=0, policy={'retrieve': False}))
    assert result['defaults_revision'] == 1
    assert graph.scope('topic')['effective_policy']['retrieve'] is False


def test_automatic_selection_honors_inherited_default_above_eight_thousand_without_widening_limit(context_scene):
    from packages.domain.context_conversation_models import AutomaticContextInput
    from packages.domain.context_graph import text_hash
    graph, _, project, _, _, _ = context_scene
    graph.save_global_defaults(GlobalPolicyChange(operation_id='larger_budget', expected_revision=0, policy={'char_budget': 12000}))
    for index, size in enumerate((7000, 4000)):
        note = graph.note(NoteChange(operation_id='large_note_'+str(index), scope_id='project', title='Rule '+str(index), text='x'*size))
        graph.associate(AssociationChange(operation_id='approve_'+str(index), scope_id='project', expected_revision=1,
            source=SourceRef(kind='NOTE', project_id=project, object_id=note['origin_id'])))
    result = graph.automatic_context(AutomaticContextInput(operation_id='automatic_larger_budget', project_id=project,
        input_sha256=digest({'text': 'continue'}), text_sha256=text_hash('continue')), 'continue')
    assert result['char_budget'] == 12000 and result['characters'] == 11000
    assert len(result['selected']) == 2 and result['policy_basis']['char_budget'] == 'GLOBAL_DEFAULTS'


def test_derived_foreign_grant_cannot_be_widened_by_local_link_or_hierarchy_move(context_scene):
    graph, _, _, other, _, source = context_scene
    foreign_scope(graph, other)
    foreign_scope(graph, other, 'sibling_root')
    graph.save_scope('child', ScopeChange(operation_id='child_scope', expected_revision=0,
        kind='TOPIC', project_id=other, parent_id='other', label='Child'))
    granted = graph.change_read_grant(grant_request(review(graph, source)))
    note = graph.note(NoteChange(operation_id='bounded_note', scope_id='child', title='Bounded derived note', text='Reviewed result',
        basis=[granted['origin_id']], basis_grants=[{'origin_id': granted['origin_id'], 'id': granted['grant_id'], 'revision': 1}]))
    note_ref = SourceRef(kind='NOTE', project_id=other, object_id=note['origin_id'])
    graph.associate(AssociationChange(operation_id='adopt_bounded', scope_id='child', expected_revision=1, source=note_ref))
    with pytest.raises(Conflict, match='source scope'):
        graph.associate(AssociationChange(operation_id='widen_bounded', scope_id='sibling_root', expected_revision=0, source=note_ref))
    foreign_scope(graph, None, 'global')
    graph.change_read_grant(grant_request(review(graph, note_ref, 'global'), 'share_reviewed_note'))
    global_selection = graph.select(SelectionInput(operation_id='global_before_move', scope_id='global'))
    assert graph.materialize(global_selection['selection_id'], None)['items'][0]['text'] == 'Reviewed result'
    graph.save_scope('child', ScopeChange(operation_id='move_child', expected_revision=1, kind='TOPIC',
        project_id=other, parent_id='sibling_root', label='Child moved'))
    assert graph.select(SelectionInput(operation_id='after_move', scope_id='child'))['selected'] == []
    with pytest.raises(Conflict, match='no longer belongs'):
        graph.materialize(global_selection['selection_id'], None)
