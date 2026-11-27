"""Authored API/admission cases; no invocation yet in this feature batch.

All providers are absent, so admission must not start a model/owner. The POSIX
host fixture proves process lifetime only, never Windows or GUI qualification.
"""
import pytest

from apps.api.native_messages import NativeQuestionInput
from packages.domain.tasks import digest
from test_conversation_directory import api


@pytest.fixture
def indexed(api):
    pid = api.post('/api/projects', json={'name': 'Context routing'}).json()['id']
    for sid, kind, parent in [('project', 'PROJECT', None), ('topic', 'TOPIC', 'project')]:
        result = api.put('/api/context/scopes/' + sid, json={'operation_id': 'scope_' + sid,
            'expected_revision': 0, 'project_id': pid, 'parent_id': parent, 'kind': kind, 'label': sid})
        assert result.status_code == 200, result.text
    created = api.post('/api/conversations', json={'operation_id': 'create', 'project_id': pid, 'title': 'Actual discussion'}).json()
    cid = created['conversation_ref']['id']
    scope = api.get('/api/conversations/' + cid + '/scope').json()
    note = api.post('/api/context/notes', json={'operation_id': 'note', 'scope_id': 'project',
        'title': 'Reviewed preference', 'text': 'PRESERVE_EXACT_USER_EDITS'}).json()
    approved = api.post('/api/context/associations', json={'operation_id': 'approve', 'scope_id': 'project',
        'expected_revision': 1, 'state': 'ACTIVE', 'source': {'kind': 'NOTE', 'project_id': pid, 'object_id': note['origin_id']}})
    assert approved.status_code == 200, approved.text
    linked = api.post('/api/context/conversation-links', json={'operation_id': 'link', 'scope_id': 'topic',
        'conversation_ref': scope['conversation_ref'], 'expected_revision': 0, 'preference_expected_revision': 0})
    assert linked.status_code == 200, linked.text
    return {'api': api, 'pid': pid, 'cid': cid, 'scope': scope}


def body(s, key, **fields):
    return {'operation_id': key, 'raw_text': 'Continue this work', 'provider_profile_ref': None,
        'conversation_ref': s['scope']['conversation_ref'], **s['scope']['next_message'], **fields}


def test_native_auto_context_has_a_frozen_decision_and_preserves_original_input_hash_without_model_dispatch(indexed):
    s = indexed
    original = body(s, 'automatic_admission')
    response = s['api'].post('/api/desktop-host/native-messages', json=original)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['status'] == 'NOT_CONFIGURED'
    assert value['context_summary']['mode'] == 'AUTO' and value['context_summary']['selected_count'] == 1
    assert value['context_summary']['scope_id'] == 'topic' and value['context_summary']['model_calls'] == 0
    native = s['api'].app.state.native_messages
    record = native.ws._read(native.path(original['operation_id']), authority=False)
    normalized = NativeQuestionInput.model_validate(original).request_dict()
    assert record['request'] == normalized and record['request_sha256'] == digest(normalized)
    assert 'context_selection_id' not in record['request']
    assert digest(record['reviewed_context']) == record['reviewed_context_sha256']
    assert record['execution'] is None and record.get('owner') is None
    assert s['api'].post('/api/desktop-host/native-messages', json=original).json()['context_summary'] == value['context_summary']


def test_manual_mode_and_pause_stop_new_automatic_context_without_destroying_original_receipts(indexed):
    s = indexed
    preferences = {'operation_id': 'manual_only', 'conversation_ref': s['scope']['conversation_ref'],
        'expected_revision': 1, 'preferred_scope_id': 'topic', 'retrieval_mode': 'MANUAL'}
    changed = s['api'].post('/api/context/conversation-settings', json=preferences)
    assert changed.status_code == 200, changed.text
    manual_mode = s['api'].post('/api/desktop-host/native-messages', json=body(s, 'manual_mode'))
    assert manual_mode.status_code == 200, manual_mode.text
    assert manual_mode.json()['context_summary']['status'] == 'MANUAL'
    assert not manual_mode.json().get('context_selection_id')
    selected = s['api'].post('/api/context/selections', json={'operation_id': 'explicit_selection',
        'scope_id': 'topic', 'conversation_id': s['cid']})
    assert selected.status_code == 200, selected.text
    sid = selected.json()['selection_id']
    explicit = s['api'].post('/api/desktop-host/native-messages', json=body(s, 'explicit_mode', context_selection_id=sid))
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()['context_summary']['mode'] == 'MANUAL'
    paused = s['api'].post('/api/context/conversation-settings', json={**preferences,
        'operation_id': 'pause', 'expected_revision': 2, 'retrieval_mode': 'OFF'})
    assert paused.status_code == 200, paused.text
    stale = s['api'].post('/api/desktop-host/native-messages', json=body(s, 'stale_explicit', context_selection_id=sid))
    assert stale.status_code == 409, stale.text
    receipt = s['api'].get('/api/context/selections/' + sid + '/receipt?project_id=' + s['pid'])
    assert receipt.status_code == 200 and receipt.json()['current_sources_verified'] is False
    assert s['api'].get('/api/context/selections/' + sid + '?project_id=' + s['pid'] + '&conversation_id=' + s['cid']).status_code == 409


def test_a_foreign_project_discussion_reference_is_metadata_only_and_cannot_become_current_context(indexed):
    s = indexed
    other = s['api'].post('/api/projects', json={'name': 'Another project'}).json()['id']
    created = s['api'].put('/api/context/scopes/other', json={'operation_id': 'other_scope', 'expected_revision': 0,
        'project_id': other, 'kind': 'PROJECT', 'label': 'Other context'})
    assert created.status_code == 200, created.text
    reference = {'operation_id': 'reference_other', 'scope_id': 'other', 'conversation_ref': s['scope']['conversation_ref'],
        'expected_revision': 0, 'allow_reference_to_other_project': True}
    result = s['api'].post('/api/context/conversation-links', json=reference)
    assert result.status_code == 200 and result.json()['reference_only'] is True
    listing = s['api'].get('/api/context/scopes/other/conversations')
    assert listing.status_code == 200 and listing.json()['items'][0]['current_project_matches'] is False
    assert s['api'].get('/api/conversations/' + s['cid'] + '/scope').json()['project_id'] == s['pid']
    rejected = s['api'].post('/api/context/conversation-links', json={**reference,
        'operation_id': 'foreign_preference', 'expected_revision': 1, 'preference_expected_revision': 1})
    assert rejected.status_code == 409
    context = s['api'].get('/api/context/conversations/' + s['cid'] + '/settings').json()
    assert context['routing']['scope_id'] == 'topic'
