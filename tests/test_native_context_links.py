"""Real file registry, bytes and SQLite; injected path comparison is NOT Windows evidence."""
from types import SimpleNamespace
import pytest

from apps.api.native_context_links import NativeContextLinks
from apps.api.context_graph import capture_context_file_event
from packages.domain.context_graph import AssociationChange, ScopeChange
from packages.domain.context_conversation_models import ConversationLinkChange, ConversationSettingsChange
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_context_file_relations import relation_scene, register
from test_context_navigation import discussion


REF = {'id': 'f'*32, 'revision': 1, 'sha256': 'e'*64}


def app_for(s, proof):
    return SimpleNamespace(state=SimpleNamespace(store=s.store, context_graph=s.graph,
        file_continuity=s.files, desktop_context_rpc=SimpleNamespace(frozen_document_identity=lambda reference: proof)))


def test_page_links_use_exact_registered_file_and_discussion_without_title_or_navigation_changes(relation_scene):
    s = relation_scene
    path = s.root/'人工稿.md'
    path.write_text('USER_MANUAL_WORK')
    source, _ = register(s, '人工稿.md', 'native_page_source')
    threads, cid = discussion(s)
    s.graph.associate(AssociationChange(operation_id='link_discussion', scope_id='work', expected_revision=0,
        source=source, conversation_id=cid))
    before = threads.view(cid)
    proof = {'native_path': str(path), 'saved': False, 'identity_sha256': 'a'*64, 'window_lifetime_sha256': 'b'*64}
    calls = []
    owner = NativeContextLinks(app_for(s, proof), same_file=lambda left,right: calls.append((left,right)) or left == right)
    result = owner.related(REF)
    assert result['status'] == 'MATCHED' and result['unsaved_changes_observed'] is True
    assert result['saved_bytes_equal_page'] is False and result['automatic_selection'] is False
    assert result['execution_authorized'] is False and result['file_write_authorized'] is False
    row = next(item for item in result['items'][0]['relationships'] if item['conversation_id'] == cid)
    assert row['discussion']['conversation_ref'] == before['conversation_ref']
    assert threads.view(cid) == before and path.read_text() == 'USER_MANUAL_WORK'
    assert calls == [(str(path), str(path))]
    assert str(s.root) not in str(result)


def test_no_document_identity_or_physical_match_never_falls_back_to_a_filename(relation_scene):
    s = relation_scene
    (s.root/'same.md').write_text('registered user file')
    register(s, 'same.md', 'same_filename')
    owner = NativeContextLinks(app_for(s, None), same_file=lambda *_: pytest.fail('No proof must not inspect a registered file'))
    assert owner.related(REF)['status'] == 'UNAVAILABLE'
    proof = {'native_path': str(s.root/'same.md'), 'saved': True, 'identity_sha256': 'a'*64, 'window_lifetime_sha256': 'b'*64}
    result = NativeContextLinks(app_for(s, proof), same_file=lambda *_: False).related(REF)
    assert result['status'] == 'NO_REGISTERED_MATCH' and result['items'] == []


def test_capture_policy_and_retrieval_mode_are_independent_and_all_input_namespaces_keep_manual_decisions(relation_scene):
    s = relation_scene
    (s.root/'shared.md').write_text('PRESERVE_THIS_INPUT_FILE')
    source, _ = register(s, 'shared.md', 'explicit_input')
    threads, cid = discussion(s)
    reference = threads.view(cid)['conversation_ref']
    s.graph.link_conversation(ConversationLinkChange(operation_id='adopt_capture_scope', scope_id='work',
        expected_revision=0, conversation_ref=reference, preference_expected_revision=0))
    s.graph.conversation_preferences(ConversationSettingsChange(operation_id='pause_retrieval',
        conversation_ref=reference, expected_revision=1, preferred_scope_id='work', retrieval_mode='OFF'))
    observed = s.files.observe(source.link_ref.model_dump())
    app = app_for(s, None)
    body = {'operation_id': 'file_input', 'file_link_ref': source.link_ref.model_dump()}
    first = capture_context_file_event(app, body, project_id=s.pid, conversation_id=cid,
        namespace='ORIGINAL_INPUT', input_sha256=digest(body), observed_file=observed)
    assert first['proposal_status'] == 'ADDED' and first['state'] == 'CANDIDATE'
    assert capture_context_file_event(app, body, project_id=s.pid, conversation_id=cid,
        namespace='ORIGINAL_INPUT', input_sha256=digest(body), observed_file=observed) == first
    second = capture_context_file_event(app, body, project_id=s.pid, conversation_id=cid,
        namespace='NATIVE_INPUT', input_sha256=digest(body), observed_file=observed)
    assert second['proposal_status'] == 'EXISTING_DECISION_RETAINED'
    s.graph.associate(AssociationChange(operation_id='reject_input', scope_id='work', expected_revision=1,
        source=source, conversation_id=cid, state='REJECTED'))
    body['operation_id'] = 'quick_input'
    kept = capture_context_file_event(app, body, project_id=s.pid, conversation_id=cid,
        namespace='NATIVE_INPUT', input_sha256=digest(body), observed_file=observed)
    assert kept['state'] == 'REJECTED' and kept['revision'] == 2
    assert (s.root/'shared.md').read_text() == 'PRESERVE_THIS_INPUT_FILE'


def test_disabled_capture_skips_file_observation_and_ambiguous_project_roots_are_not_guessed(relation_scene, monkeypatch):
    s = relation_scene
    (s.root/'selected.md').write_text('UNCHANGED')
    source, _ = register(s, 'selected.md', 'capture_skip')
    app = app_for(s, None)
    body = {'operation_id': 'without_scope', 'file_link_ref': source.link_ref.model_dump()}
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Skipped capture must not freeze or observe another copy')
    monkeypatch.setattr(s.files, 'observe', forbidden)
    assert capture_context_file_event(app, body, project_id=s.pid, conversation_id=None,
        namespace='NATIVE_INPUT', input_sha256=digest(body))['proposal_status'] == 'SKIPPED_NO_UNIQUE_SCOPE'
    s.graph.save_scope('work', ScopeChange(operation_id='disable_capture', expected_revision=1,
        kind='PROJECT', project_id=s.pid, label='Work', policy={'capture': False}))
    assert capture_context_file_event(app, body, project_id=s.pid, conversation_id=None,
        namespace='NATIVE_INPUT', input_sha256=digest(body), selected_scope_id='work')['proposal_status'] == 'SKIPPED_POLICY'


def test_capture_rejects_another_frozen_file_even_in_the_same_project(relation_scene):
    from packages.domain.conversation_file_input import freeze_conversation_file
    s = relation_scene
    for name in ('wanted.md', 'other.md'):
        (s.root/name).write_text(name+' ORIGINAL_USER_DATA')
    wanted, _ = register(s, 'wanted.md', 'wanted_source')
    other, _ = register(s, 'other.md', 'wrong_source')
    selected = {'kind': 'LINKED_FILE', 'link_ref': wanted.link_ref.model_dump()}
    wrong = freeze_conversation_file(s.files, s.pid, {'kind': 'LINKED_FILE', 'link_ref': other.link_ref.model_dump()})
    body = {'operation_id': 'same_project_wrong_input', 'file_input': selected}
    result = capture_context_file_event(app_for(s, None), body, project_id=s.pid, conversation_id=None,
        namespace='ORIGINAL_INPUT', input_sha256=digest(body), selected_scope_id='work', frozen_file=wrong)
    assert result['proposal_status'] == 'NOT_CAPTURED'
    assert s.graph.associations('work')['items'] == []
