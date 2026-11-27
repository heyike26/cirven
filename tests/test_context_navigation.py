"""Real metadata, registered saved files and SQLite; no model/OS execution.

Authored for the later concentrated debug phase. Native/renderer behavior has
its own boundary tests and still needs real desktop acceptance.
"""
from hashlib import sha256

import pytest

from apps.api.conversation_creation import ConversationCreation, CreateConversation
from packages.domain.conversation_threads import ConversationThreads
from packages.domain.context_file_models import FolderRuleChange
from packages.domain.context_graph import AssociationChange
from packages.domain.store import Conflict, NotFound
from test_context_file_relations import relation_scene, register, folder


def discussion(s):
    threads = ConversationThreads(s.store)
    created = ConversationCreation(s.store, threads).create(CreateConversation(
        operation_id='new_discussion', project_id=s.pid, title='Actual related discussion'))
    s.graph.conversation_reader = threads.view
    return threads, created['conversation_ref']['id']


def test_saved_update_finds_same_discussion_and_opens_original_without_certifying_old_content(relation_scene, monkeypatch):
    s = relation_scene
    path = s.root / 'manual.md'
    path.write_text('Original saved source for this discussion')
    source, _ = register(s, 'manual.md', 'discussion_source')
    threads, cid = discussion(s)
    edge = s.graph.associate(AssociationChange(operation_id='discussion_link', scope_id='work',
        conversation_id=cid, source=source, expected_revision=0))
    current = s.ws.read_saved_snapshot(s.pid, s.bound['id'], 'manual.md')['source_ref']
    location, saved = s.adapter.target_location({'kind': 'WORKSPACE_FILE', 'source_ref': current})
    initial = s.graph.file_relationships(location, saved_sha256=saved)
    own = next(row for row in initial['items'] if row['conversation_id'] == cid)
    assert own['discussion']['conversation_ref'] == threads.view(cid)['conversation_ref']
    assert own['saved_version_matches'] is True
    replacement = s.root / 'manual-save.tmp'
    replacement.write_text('LATER USER EDITS: keep this actual original file intact')
    replacement.replace(path)
    current = s.ws.read_saved_snapshot(s.pid, s.bound['id'], 'manual.md')['source_ref']
    def no_archive_body(*_args, **_kwargs):
        raise AssertionError('Relationship lookup must not load historical snapshot bytes')
    monkeypatch.setattr(s.files, 'read_snapshot_bytes', no_archive_body)
    new_location, new_saved = s.adapter.target_location({'kind': 'WORKSPACE_FILE', 'source_ref': current})
    assert new_location['file_key'] == location['file_key']
    related = s.graph.file_relationships(new_location, saved_sha256=new_saved)
    own = next(row for row in related['items'] if row['conversation_id'] == cid)
    assert own['saved_version_matches'] is False and own['discussion']['available'] is True
    assert own['origin_id'] == edge['origin_id']
    resolved = s.graph.resolve_original_file(source)
    assert resolved['file_source_ref']['sha256'] == sha256(path.read_bytes()).hexdigest()
    assert resolved['context_source_ref'] == source.model_dump()
    assert path.read_text().startswith('LATER USER EDITS:')
    with pytest.raises(Conflict):
        s.graph.preview(edge['origin_id'], s.pid)  # Navigation did not revive the old context body.


def test_moved_discussion_keeps_old_project_file_and_can_remove_only_its_direct_link(relation_scene):
    s = relation_scene
    (s.root / 'manual.md').write_text('Preserve the original project file')
    source, _ = register(s, 'manual.md', 'move_source')
    threads, cid = discussion(s)
    edge = s.graph.associate(AssociationChange(operation_id='before_move', scope_id='work',
        conversation_id=cid, source=source, expected_revision=0))
    other = s.store.create_project('New navigation project', '')['id']
    with s.store.connect() as db:
        threads.move(db, cid, other, updated_at='2026-11-02T10:00:00+00:00')
        db.execute('UPDATE conversation_organization SET revision=revision+1 WHERE conversation_id=?', (cid,))
    listing = s.graph.conversation_files(cid)
    assert listing['project_id'] == other
    assert len(listing['items']) == 1 and listing['items'][0]['source_project_id'] == s.pid
    assert listing['items'][0]['source_ref'] == source.model_dump()
    assert s.graph.resolve_original_file(source)['link_ref']['project_id'] == s.pid
    assert threads.view(cid)['project_id'] == other
    s.graph.associate(AssociationChange(operation_id='remove_after_move', scope_id='work',
        conversation_id=cid, source=source, expected_revision=edge['revision'], state='RETRACTED'))
    assert s.graph.conversation_files(cid)['items'] == []
    assert (s.root / 'manual.md').read_text() == 'Preserve the original project file'


def test_same_basename_other_directory_and_deleted_discussion_do_not_retarget_relationships(relation_scene):
    s = relation_scene
    for name in ('first', 'second'):
        (s.root / name).mkdir()
        (s.root / name / 'same.md').write_text(name)
    source, _ = register(s, 'first/same.md', 'first')
    other, _ = register(s, 'second/same.md', 'second')
    threads, cid = discussion(s)
    s.graph.associate(AssociationChange(operation_id='first_only', scope_id='work',
        conversation_id=cid, source=source, expected_revision=0))
    assert source.file_identity_sha256 != other.file_identity_sha256
    location = s.adapter.file_locator(other.link_ref.model_dump())
    result = s.graph.file_relationships(location, saved_sha256=sha256(b'second').hexdigest())
    assert not any(row['conversation_id'] == cid for row in result['items'])
    with s.store.connect() as db:
        threads.delete(db, cid, updated_at='2026-11-02T11:00:00+00:00')
    location = s.adapter.file_locator(source.link_ref.model_dump())
    result = s.graph.file_relationships(location, saved_sha256=sha256(b'first').hexdigest())
    old = next(row for row in result['items'] if row['conversation_id'] == cid)
    assert old['discussion'] == {'id': cid, 'available': False}
    with pytest.raises(NotFound):
        s.graph.conversation_files(cid)
    assert (s.root / 'first/same.md').read_text() == 'first'


def test_conversation_directory_files_are_registered_only_and_scopewide_rules_are_not_borrowed(relation_scene, monkeypatch):
    s = relation_scene
    for name in ('chosen', 'other'):
        (s.root / name).mkdir()
        (s.root / name / 'same.md').write_text(name)
    chosen, chosen_edge = register(s, 'chosen/same.md', 'chosen')
    register(s, 'other/same.md', 'other')
    (s.root / 'chosen/unregistered.md').write_text('Never indexed')
    _, cid = discussion(s)
    s.graph.folder_rule(FolderRuleChange(operation_id='scopewide_root', scope_id='work',
        expected_revision=0, folder_ref=folder(s, ''), range='INCLUDING_DESCENDANTS'))
    assert s.graph.conversation_files(cid)['items'] == []
    s.graph.folder_rule(FolderRuleChange(operation_id='discussion_directory', scope_id='work',
        conversation_id=cid, expected_revision=0, folder_ref=folder(s, 'chosen')))
    monkeypatch.setattr(s.ws, 'tree', lambda *_args, **_kwargs: pytest.fail('Must not scan user files'))
    listing = s.graph.conversation_files(cid)
    assert {row['origin_id'] for row in listing['items']} == {chosen_edge['origin_id']}
    assert listing['items'][0]['relation'] == 'INHERITED'
    source_ref = s.graph.resolve_original_file(chosen)['context_source_ref']
    assert source_ref['file_identity_sha256'] == chosen.file_identity_sha256
    s.graph.associate(AssociationChange(operation_id='exclude_inherited', scope_id='work',
        conversation_id=cid, source=chosen, expected_revision=0, state='EXCLUDED'))
    assert s.graph.conversation_files(cid)['items'][0]['file_excluded'] is True


def test_replaced_parent_and_forgotten_origin_cannot_open_another_file(relation_scene):
    from packages.domain.context_graph import ForgetInput
    s = relation_scene
    (s.root / 'docs').mkdir()
    (s.root / 'docs/manual.md').write_text('Keep the old original')
    source, edge = register(s, 'docs/manual.md', 'parent_identity')
    (s.root / 'docs').rename(s.root / 'preserved-docs')
    (s.root / 'docs').mkdir()
    (s.root / 'docs/manual.md').write_text('Same name in a different parent')
    with pytest.raises(Conflict):
        s.graph.resolve_original_file(source)
    s.graph.forget(ForgetInput(operation_id='forget_for_navigation', scope_id='catalog', origin_id=edge['origin_id']))
    with pytest.raises(Conflict):
        s.graph.resolve_original_file(source)
    assert (s.root / 'preserved-docs/manual.md').read_text() == 'Keep the old original'
    assert (s.root / 'docs/manual.md').read_text() == 'Same name in a different parent'
