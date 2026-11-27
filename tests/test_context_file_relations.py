"""Real local files/SQLite; authored for the later concentrated debug phase.

No model, native application, directory watcher or filesystem search fixture.
"""
from types import SimpleNamespace

import pytest

from apps.api.context_graph import ApplicationContextSources
from packages.domain.context_file_models import FileExclusionRef, FolderRuleChange
from packages.domain.context_graph import (AssociationChange, ContextGraph, NoteChange,
    ScopeChange, SelectionInput, SourceRef)
from packages.domain.file_continuity import FileContinuity
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput
from packages.domain.store import Store, Conflict


@pytest.fixture
def relation_scene(tmp_path):
    data, root = tmp_path / 'private', tmp_path / '真实用户目录'
    data.mkdir()
    root.mkdir()
    store = Store(data / 'state.sqlite')
    pid = store.create_project('Context file relationships', '')['id']
    workspaces = LocalWorkspaces(store, data)
    bound = workspaces.bind(pid, WorkspaceInput(idempotency_key='selected', root=str(root)))
    files = FileContinuity(store, data, workspaces)
    adapter = ApplicationContextSources(SimpleNamespace(state=SimpleNamespace(
        store=store, file_continuity=files, local_workspaces=workspaces)))
    graph = ContextGraph(store, adapter.read, lambda _: {'project_id': pid})
    for sid in ('catalog', 'work'):
        graph.save_scope(sid, ScopeChange(operation_id='scope_' + sid, expected_revision=0,
            project_id=pid, kind='PROJECT', label=sid))
    return SimpleNamespace(root=root, store=store, pid=pid, ws=workspaces,
        bound=bound, files=files, adapter=adapter, graph=graph)


def register(s, path, key):
    source = s.ws.read_saved_snapshot(s.pid, s.bound['id'], path)['source_ref']
    link = s.files.register_workspace(s.pid, source,
        {'kind': 'USER_SELECTED_CONVERSATION_FILE', 'source_id': key})['link_ref']
    observed = s.files.observe(link)
    ref = s.graph.file_source(observed['snapshot_ref'], link)
    edge = s.graph.associate(AssociationChange(operation_id='catalog_' + key,
        expected_revision=0, scope_id='catalog', source=ref))
    return ref, edge


def folder(s, path):
    return s.ws.folder_reference(s.pid, s.bound['id'], path, binding_revision=1,
                                binding_sha256=s.bound['binding_sha256'])


def choose(s, key):
    return s.graph.select(SelectionInput(operation_id=key, scope_id='work'))


def test_directory_inheritance_is_registered_only_and_not_a_string_prefix(relation_scene, monkeypatch):
    s = relation_scene
    for name in ('docs', 'docs/nested', 'docs-old'):
        (s.root / name).mkdir(parents=True, exist_ok=True)
    refs = {}
    for key, path in [('direct', 'docs/相同.md'), ('child', 'docs/nested/相同.md'), ('sibling', 'docs-old/相同.md')]:
        (s.root / path).write_text('Exact registered source ' + key)
        refs[key] = register(s, path, key)[1]['origin_id']
    (s.root / 'docs/unregistered.md').write_text('Never selected or registered')
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Directory inheritance must not enumerate user files')
    monkeypatch.setattr(s.ws, 'tree', forbidden)
    rule = FolderRuleChange(operation_id='current_dir', scope_id='work', expected_revision=0,
        folder_ref=folder(s, 'docs'), range='CURRENT_DIRECTORY')
    receipt = s.graph.folder_rule(rule)
    first = choose(s, 'select_current')
    assert {item['origin_id'] for item in first['selected']} == {refs['direct']}
    assert first['selected'][0]['relation'] == 'INHERITED'
    assert first['selected'][0]['association_id'] is None
    assert s.graph.materialize(first['selection_id'], s.pid)['items'][0]['text'].endswith('direct')
    s.graph.folder_rule(rule.model_copy(update={'operation_id': 'descendants',
        'expected_revision': receipt['revision'], 'range': 'INCLUDING_DESCENDANTS'}))
    with pytest.raises(Conflict):
        s.graph.materialize(first['selection_id'], s.pid)
    second = choose(s, 'select_descendants')
    assert {item['origin_id'] for item in second['selected']} == {refs['direct'], refs['child']}
    listed = s.graph.inherited_files('work')
    assert listed['coverage'] == 'REGISTERED_FILE_SOURCES_ONLY_NO_FILESYSTEM_SCAN'
    assert {item['origin_id'] for item in listed['items']} == {refs['direct'], refs['child']}


def test_exclusion_survives_save_new_alias_and_derived_note_until_exact_restore(relation_scene):
    s = relation_scene
    path = s.root / 'manual.md'
    path.write_text('User manually saved original source')
    source, first = register(s, 'manual.md', 'first_alias')
    s.graph.folder_rule(FolderRuleChange(operation_id='root_rule', scope_id='work', expected_revision=0,
        folder_ref=folder(s, ''), range='INCLUDING_DESCENDANTS'))
    chosen = choose(s, 'before_exclusion')
    note = s.graph.note(NoteChange(operation_id='derived_note', scope_id='work', title='Derived',
        text='Must be blocked when its real file basis is excluded', basis=[first['origin_id']]))
    s.graph.associate(AssociationChange(operation_id='approve_note', scope_id='work', expected_revision=1,
        source=SourceRef(kind='NOTE', project_id=s.pid, object_id=note['origin_id'])))
    # Ordinary atomic save changes the inode, but preserves the logical file in
    # the same pinned parent. Withdrawing the OLD source must remain possible.
    replacement = s.root / 'save.tmp'
    replacement.write_text('USER CORRECTION: newer bytes, same logical file')
    replacement.replace(path)
    s.graph.associate(AssociationChange(operation_id='exclude_old', scope_id='work',
        expected_revision=0, source=source, state='EXCLUDED'))
    current, second = register(s, 'manual.md', 'second_alias')
    assert current.file_identity_sha256 == source.file_identity_sha256
    proposal = s.graph.propose(AssociationChange(operation_id='new_version_proposal', scope_id='work',
        expected_revision=0, source=current))
    assert proposal['proposal_status'] == 'SKIPPED_MANUAL_FILE_EXCLUSION'
    assert choose(s, 'after_exclusion')['selected'] == []
    with pytest.raises(Conflict):
        s.graph.materialize(chosen['selection_id'], s.pid)
    refs = next(item for item in s.graph.inherited_files('work')['items']
                if item['origin_id'] == second['origin_id'])['file_exclusions']
    assert refs
    request = AssociationChange(operation_id='restore_without_basis', scope_id='work', expected_revision=0, source=current)
    with pytest.raises(Conflict):
        s.graph.associate(request)
    # Omitting or guessing an exclusion revision does not clear the decision.
    with pytest.raises(Conflict):
        s.graph.associate(request.model_copy(update={'operation_id': 'bad_restore',
            'restore_file_exclusions': [FileExclusionRef(id=refs[0]['id'], revision=refs[0]['revision'] + 1)]}))
    restored = s.graph.associate(AssociationChange(operation_id='reviewed_restore', scope_id='work',
        expected_revision=0, source=current,
        restore_file_exclusions=[{'id': row['id'], 'revision': row['revision']} for row in refs]))
    final = choose(s, 'after_restore')
    assert any(row['origin_id'] == restored['origin_id'] for row in final['selected'])
    assert path.read_text().startswith('USER CORRECTION:')
    assert s.files.read_snapshot_bytes(source.link_ref.model_dump(), {
        'project_id': s.pid, 'id': source.object_id, 'revision': source.revision,
        'sha256': source.sha256})['raw'] == b'User manually saved original source'


def test_replaced_directory_cannot_retarget_existing_rule_or_frozen_source(relation_scene):
    s = relation_scene
    (s.root / 'docs').mkdir()
    (s.root / 'docs/manual.md').write_text('Original directory must remain recoverable')
    register(s, 'docs/manual.md', 'before_move')
    rule = FolderRuleChange(operation_id='directory_rule', scope_id='work', expected_revision=0,
        folder_ref=folder(s, 'docs'), range='INCLUDING_DESCENDANTS')
    s.graph.folder_rule(rule)
    chosen = choose(s, 'before_directory_move')
    (s.root / 'docs').rename(s.root / 'preserved-original')
    (s.root / 'docs').mkdir()
    (s.root / 'docs/manual.md').write_text('Different file in replacement directory')
    assert choose(s, 'after_directory_replacement')['selected'] == []
    with pytest.raises(Conflict):
        s.graph.materialize(chosen['selection_id'], s.pid)
    assert s.graph.folder_rules('work')['items'][0]['source_current'] is False
    revoked = s.graph.folder_rule(rule.model_copy(update={
        'operation_id': 'withdraw_stale_rule', 'expected_revision': 1, 'state': 'RETRACTED'}))
    assert revoked['state'] == 'RETRACTED'
    assert (s.root / 'preserved-original/manual.md').read_text().startswith('Original directory')


def test_same_file_ranges_share_the_full_source_read_within_one_selection(relation_scene, monkeypatch):
    s = relation_scene
    (s.root / 'manual.md').write_text('First part. Second part. Third part.')
    source, _ = register(s, 'manual.md', 'ranges')
    for index, offset in enumerate((0, 12)):
        part = source.model_copy(update={'offset': offset, 'length': 10})
        s.graph.associate(AssociationChange(operation_id='range_' + str(index), scope_id='work', expected_revision=0, source=part))
    original, calls = s.files.context_snapshot, []
    def observed(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(s.files, 'context_snapshot', observed)
    result = choose(s, 'shared_file_body')
    assert len(result['selected']) == 2 and len(calls) == 1
    assert result['source_read_budget']['physical_disk_io'] == 'NOT_MEASURED'
    assert result['source_read_budget']['charged_bytes'] > 0
