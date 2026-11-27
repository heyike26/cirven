"""Real graph/Store integration, authored NOT_RUN for concentrated debug."""
from types import SimpleNamespace

import pytest

from apps.api.context_graph import capture_context_conversation_event
from packages.domain.context_graph import AssociationChange, SourceRef
from packages.domain.context_conversation_models import ConversationLinkChange, ConversationSettingsChange
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_context_graph import context_scene
from test_context_conversations import conversation_scene


def test_legacy_source_and_nested_request_identity_omit_new_optional_anchor(conversation_scene):
    source = conversation_scene.source
    expected = {key: value for key, value in source.model_dump().items() if key != 'source_anchor'}
    assert 'source_anchor' not in source.model_dump()
    assert SourceRef.model_validate({**expected, 'source_anchor':None}).model_dump() == expected
    request = AssociationChange(operation_id='legacy_association', scope_id='project',
        expected_revision=0, source=source)
    assert request.model_dump()['source'] == expected


def test_navigation_entry_does_not_route_context_and_cannot_be_preferred_atomically(conversation_scene):
    s=conversation_scene
    before=s.threads.view(s.ref['id'])
    request=ConversationLinkChange(operation_id='navigation_only',scope_id='topic',conversation_ref=s.ref,
        expected_revision=0,navigation_only=True)
    saved=s.graph.link_conversation(request)
    view=s.graph.conversation_context(s.ref['id'])
    assert view['routing']['scope_id']=='project'
    assert view['links'][0]['navigation_only']==1
    assert s.threads.view(s.ref['id'])==before
    with pytest.raises(Conflict):
        s.graph.conversation_preferences(ConversationSettingsChange(operation_id='invalid_preference',
            conversation_ref=s.ref,expected_revision=0,preferred_scope_id='topic'))
    with pytest.raises(Conflict):
        s.graph.link_conversation(request.model_copy(update={'operation_id':'invalid_atomic_navigation',
            'expected_revision':saved['revision'],'preference_expected_revision':0}))
    assert s.graph.conversation_context(s.ref['id'])['links'][0]['revision']==saved['revision']
    converted=s.graph.link_conversation(request.model_copy(update={'operation_id':'explicit_use_context',
        'expected_revision':saved['revision'],'navigation_only':False,'preference_expected_revision':0}))
    assert converted['navigation_only'] is False
    assert s.graph.conversation_context(s.ref['id'])['routing']['scope_id']=='topic'


def test_default_navigation_flag_does_not_change_legacy_operation_hash(conversation_scene):
    s=conversation_scene
    request=ConversationLinkChange(operation_id='legacy_link',scope_id='topic',conversation_ref=s.ref,expected_revision=0)
    legacy=request.model_dump();legacy.pop('navigation_only')
    result=s.graph.link_conversation(request)
    assert result['request_sha256']==digest({'kind':'conversation_link','body':legacy})
    assert s.graph.link_conversation(request)==result


def test_incremental_input_proposes_one_discussion_and_keeps_manual_rejection(conversation_scene):
    s=conversation_scene
    app=SimpleNamespace(state=SimpleNamespace(context_graph=s.graph))
    def capture(operation, selected=None):
        return capture_context_conversation_event(app,{'operation_id':operation},project_id=s.pid,
            conversation_id=s.ref['id'],namespace='unit_original_input',input_sha256=digest(operation),selected_scope_id=selected)
    first=capture('first_input')
    assert first['proposal_status']=='ADDED' and first['state']=='CANDIDATE'
    with s.store.connect() as db:
        count=db.execute('SELECT count(*) FROM context_operations').fetchone()[0]
        origins=db.execute('SELECT count(*) FROM context_origins').fetchone()[0]
    assert capture('second_input')['proposal_status']=='EXISTING_DECISION_RETAINED'
    with s.store.connect() as db:
        assert db.execute('SELECT count(*) FROM context_operations').fetchone()[0]==count
        assert db.execute('SELECT count(*) FROM context_origins').fetchone()[0]==origins
    s.graph.link_conversation(ConversationLinkChange(operation_id='reject_candidate',scope_id='project',
        conversation_ref=s.ref,expected_revision=first['revision'],state='REJECTED'))
    assert capture('later_input')['proposal_status']=='SKIPPED_NO_UNIQUE_SCOPE'
    retained=capture('selected_scope_input','project')
    assert retained['proposal_status']=='EXISTING_DECISION_RETAINED' and retained['state']=='REJECTED'
