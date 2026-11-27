"""Real SQLite + original document services; no external model invocation.

Feature batch tests are authored with the implementation and run in the
concentrated debug stage. They do not qualify the native desktop or IDE.
"""
from types import SimpleNamespace

import pytest

from apps.api.context_graph import ApplicationContextSources
from packages.domain.context_graph import (AssociationChange, ContextGraph, ForgetInput,
    NoteChange, ScopeChange, SelectionInput, SourceRef)
from packages.domain.store import Conflict, InvalidInput, Store


@pytest.fixture
def context_scene(tmp_path):
    store = Store(tmp_path / 'context.sqlite')
    project = store.create_project('真实原资料', '')['id']
    other = store.create_project('不可串入的项目', '')['id']
    adapter = ApplicationContextSources(SimpleNamespace(state=SimpleNamespace(store=store)))
    graph = ContextGraph(store, adapter.read, lambda _: {'project_id': project})
    graph.save_scope('project', ScopeChange(operation_id='scope_project', expected_revision=0,
        kind='PROJECT', project_id=project, label='Project'))
    graph.save_scope('topic', ScopeChange(operation_id='scope_topic', expected_revision=0,
        kind='TOPIC', project_id=project, parent_id='project', label='Topic'))
    document = store.save_document(project, '人工约束.md', b'Keep manual edits. Shipping fee uses started half-kilogram blocks.', 'UPLOAD')
    source = SourceRef(kind='DOCUMENT', project_id=project, object_id=document['id'],
                       revision=document['revision'], sha256=document['sha256'])
    return graph, store, project, other, document, source


def link(graph, source, scope='project', **fields):
    return graph.associate(AssociationChange(operation_id=fields.pop('operation_id', 'link_' + scope),
        expected_revision=fields.pop('expected_revision', 0), scope_id=scope, source=source, **fields))


def selection(graph, scope='topic', operation_id='select', **fields):
    return graph.select(SelectionInput(operation_id=operation_id, scope_id=scope, **fields))


def test_source_update_invalidates_old_context_without_touching_manual_bytes(context_scene):
    graph, store, project, _, document, source = context_scene
    edge = link(graph, source)
    selected = selection(graph)
    old = graph.materialize(selected['selection_id'], project)
    assert old['items'][0]['text'].startswith('Keep manual edits.')
    raw = b'Keep manual edits. USER CORRECTION: weight must be a positive integer.'
    store.save_document(project, '人工约束.md', raw, 'UPLOAD', document_id=document['id'], expected_revision=1)
    with pytest.raises(Conflict):
        graph.materialize(selected['selection_id'], project)
    fresh = selection(graph, operation_id='select_after_change')
    assert fresh['selected'] == []
    assert fresh['omitted'] == [{'origin_id': edge['origin_id'], 'reason': 'SOURCE_UNAVAILABLE_OR_CHANGED'}]
    assert store.document(project, document['id'])['raw_bytes'] == raw
    assert store.document(project, document['id'], 1)['sha256'] == source.sha256


def test_verified_admitted_scope_survives_navigation_move_but_not_new_exclusions(context_scene):
    graph, _, project, other, _, source = context_scene
    link(graph, source)
    cid = 'native:' + 'a' * 64
    placement = {'project_id': project}
    graph.conversation_reader = lambda _: placement
    chosen = selection(graph, operation_id='original_scope_selection', conversation_id=cid)
    frozen = {'project_id': project, 'conversation_ref': {'id': cid, 'revision': 1}}
    placement['project_id'] = other
    with pytest.raises(Conflict):
        graph.materialize(chosen['selection_id'], project, conversation_id=cid)
    admitted = graph.materialize(chosen['selection_id'], project, conversation_id=cid,
                                 frozen_conversation_scope=frozen)
    assert admitted['items'][0]['text'].startswith('Keep manual edits.')
    with pytest.raises(Conflict):
        graph.materialize(chosen['selection_id'], project, conversation_id=cid,
            frozen_conversation_scope={'project_id': project,
                'conversation_ref': {'id': 'native:' + 'b' * 64, 'revision': 1}})
    link(graph, source, 'topic', operation_id='withdraw_after_move', state='EXCLUDED')
    with pytest.raises(Conflict):
        graph.materialize(chosen['selection_id'], project, conversation_id=cid,
                          frozen_conversation_scope=frozen)


def test_new_exclusion_invalidates_frozen_selection_and_cas_protects_manual_choice(context_scene):
    graph, _, project, _, _, source = context_scene
    link(graph, source)
    selected = selection(graph)
    edge = link(graph, source, 'topic', state='EXCLUDED')
    with pytest.raises(Conflict):
        graph.materialize(selected['selection_id'], project)
    assert selection(graph, operation_id='select_excluded')['selected'] == []
    with pytest.raises(Conflict):
        link(graph, source, 'topic', operation_id='stale_auto', state='ACTIVE')
    same = link(graph, source, 'topic', operation_id='link_topic', state='EXCLUDED')
    assert same == edge
    link(graph, source, 'topic', operation_id='user_restore', expected_revision=1, state='ACTIVE')
    assert selection(graph, operation_id='select_restored')['characters'] > 0


def test_reviewed_learning_and_recursive_forget_preserve_original_sources(context_scene):
    graph, store, project, _, document, source = context_scene
    origin = link(graph, source)['origin_id']
    note = graph.note(NoteChange(operation_id='note', scope_id='topic',
        title='计费约束', text='Use the reviewed business table; preserve manual notes.', basis=[origin]))
    assert note['state'] == 'CANDIDATE'
    assert len(selection(graph)['selected']) == 1
    note_ref = SourceRef(kind='NOTE', project_id=project, object_id=note['origin_id'])
    link(graph, note_ref, 'topic', operation_id='approve_note', expected_revision=1)
    second = graph.note(NoteChange(operation_id='derived', scope_id='topic', title='来源核验',
        text='A derived source must become unavailable when its basis is forgotten.', basis=[note['origin_id']]))
    link(graph, SourceRef(kind='NOTE', project_id=project, object_id=second['origin_id']),
         'topic', operation_id='approve_derived', expected_revision=1)
    selected = selection(graph, operation_id='all_notes')
    assert len(selected['selected']) == 3
    forgotten = graph.forget(ForgetInput(operation_id='forget_source', origin_id=origin, scope_id='topic'))
    assert set(forgotten['forgotten_origins']) == {origin, note['origin_id'], second['origin_id']}
    with pytest.raises(Conflict):
        graph.materialize(selected['selection_id'], project)
    assert selection(graph, operation_id='after_forget')['selected'] == []
    assert store.document(project, document['id'])['sha256'] == document['sha256']
    with store.connect() as db:
        assert {row[0] for row in db.execute('SELECT text_content FROM context_notes')} == {''}
        assert not any('Use the reviewed business table' in row[0] for row in db.execute('SELECT result_json FROM context_operations'))
    link(graph, source, operation_id='explicit_source_reselect', expected_revision=2)
    assert len(selection(graph, operation_id='explicitly_restored_source')['selected']) == 1
    with pytest.raises(Conflict):
        graph.preview(note['origin_id'], project)  # No resurrection of forgotten derived text.


def test_scope_and_project_guards_budget_and_restart_recover_original_receipts(context_scene):
    graph, store, project, other, _, source = context_scene
    edge = link(graph, source)
    chosen = selection(graph, char_budget=3)
    assert chosen['characters'] == 0 and chosen['omitted'][0]['reason'] == 'BUDGET'
    with pytest.raises((Conflict, InvalidInput)):
        graph.save_scope('project', ScopeChange(operation_id='cycle', expected_revision=1,
            project_id=project, kind='PROJECT', parent_id='topic', label='Cycle'))
    graph.save_scope('foreign', ScopeChange(operation_id='foreign_scope', expected_revision=0,
        project_id=other, kind='PROJECT', label='Other'))
    with pytest.raises(Conflict):
        link(graph, source, 'foreign', operation_id='no_cross_project')
    with pytest.raises(Conflict):
        graph.materialize(selection(graph, operation_id='proper')['selection_id'], other)
    restarted_store = Store(store.database)
    restored = ContextGraph(restarted_store, graph.source_reader, graph.conversation_reader)
    assert restored.operation('link_project') == edge
    with pytest.raises(Conflict):
        link(restored, source, 'topic', operation_id='link_project')
    assert restored.scope('topic')['effective_policy']['retrieve'] is True


def test_incremental_proposals_obey_manual_review_auto_and_never_override_exclusions(context_scene):
    graph, _, project, _, _, source = context_scene
    first = graph.propose(AssociationChange(operation_id='incremental', expected_revision=0,
        scope_id='topic', source=source))
    assert first['state'] == 'CANDIDATE' and first['model_calls'] == 0
    assert selection(graph)['selected'] == []
    graph.save_scope('topic', ScopeChange(operation_id='allow_auto', expected_revision=1,
        project_id=project, parent_id='project', kind='TOPIC', label='Topic', policy={'archive_mode':'AUTO'}))
    # Changing a policy never silently approves an existing candidate.
    retained = graph.propose(AssociationChange(operation_id='incremental_again', expected_revision=0,
        scope_id='topic', source=source))
    assert retained['proposal_status'] == 'EXISTING_DECISION_RETAINED' and retained['state'] == 'CANDIDATE'
    link(graph, source, 'project', operation_id='manual_root_exclusion', state='EXCLUDED')
    graph.save_scope('task', ScopeChange(operation_id='subtask', expected_revision=0,
        project_id=project, parent_id='topic', kind='TASK', label='Implementation'))
    denied = graph.propose(AssociationChange(operation_id='auto_no_override', expected_revision=0,
        scope_id='task', source=source))
    assert denied['proposal_status'] == 'SKIPPED_MANUAL_DECISION'
    graph.save_scope('task', ScopeChange(operation_id='manual_only', expected_revision=1,
        project_id=project, parent_id='topic', kind='TASK', label='Implementation', policy={'archive_mode':'MANUAL'}))
    manual = graph.propose(AssociationChange(operation_id='manual_skip', expected_revision=0,
        scope_id='task', source=source))
    assert manual['proposal_status'] == 'SKIPPED_POLICY' and manual['source_read'] is False


def test_exact_object_related_lookup_covers_ranges_without_merging_same_filename(context_scene):
    graph, store, project, _, _, source = context_scene
    first = link(graph, source)
    part = source.model_copy(update={'offset': 19, 'length': 20})
    second = link(graph, part, 'topic', operation_id='second_range')
    related = graph.related_source(part)
    assert {row['id'] for row in related['associations']} == {first['association_id'], second['association_id']}
    other = store.save_document(project, '人工约束.md', b'A different document, deliberately with the same filename.', 'UPLOAD')
    other_ref = SourceRef(kind='DOCUMENT', project_id=project, object_id=other['id'],
        revision=other['revision'], sha256=other['sha256'])
    assert graph.related_source(other_ref)['associations'] == []


def test_storage_budget_rejects_capture_atomically_but_still_allows_revocation_and_forget(context_scene, monkeypatch):
    from packages.domain import context_graph as module
    graph, store, project, _, document, source = context_scene
    origin = link(graph, source)['origin_id']
    selected = selection(graph)
    monkeypatch.setattr(module, 'MAX_STORAGE_BYTES', 1)
    with pytest.raises(Conflict, match='storage budget'):
        graph.note(NoteChange(operation_id='over_capacity', scope_id='topic', title='Too large', text='No partial candidate.'))
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM context_notes').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM context_operations WHERE operation_id="over_capacity"').fetchone()[0] == 0
    link(graph, source, 'topic', operation_id='exclude_at_capacity', state='EXCLUDED')
    with pytest.raises(Conflict):
        graph.materialize(selected['selection_id'], project)
    result = graph.forget(ForgetInput(operation_id='forget_at_capacity', origin_id=origin, scope_id='topic'))
    assert result['forgotten_origins'] == [origin]
    assert store.document(project, document['id'])['sha256'] == document['sha256']
