"""Actual ASGI, existing file services and native admission; native identity is injected metadata.

Authored for concentrated debug. No provider, model, Word, SDK or GUI action.
"""
from pathlib import Path
from test_context_navigation_api import linked
from test_conversation_directory import api
from apps.api.native_messages import NativeQuestionInput
from packages.domain.tasks import digest


def test_page_related_route_exposes_only_bound_registered_relationships(linked, monkeypatch):
    s = linked
    proof = {'native_path': str(s['path']), 'saved': False, 'identity_sha256': 'a'*64, 'window_lifetime_sha256': 'b'*64}
    state = s['api'].app.state
    monkeypatch.setattr(state.desktop_context_rpc, 'frozen_document_identity', lambda _ref: proof)
    monkeypatch.setattr(state.native_context_links, 'same_file', lambda left,right: Path(left).samefile(right))
    reference = {'id': 'f'*32, 'revision': 1, 'sha256': 'e'*64}
    result = s['api'].post('/api/context/native-pages/related', json={'context_ref': reference})
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['context_ref'] == reference and value['status'] == 'MATCHED'
    assert value['items'][0]['file_key'] == s['source']['file_identity_sha256']
    assert value['items'][0]['relationships'][0]['discussion']['conversation_ref']['id'] == s['cid']
    assert str(s['path'].parent) not in result.text and 'native_path' not in result.text
    assert value['automatic_selection'] is False and value['saved_bytes_equal_page'] is False


def test_native_workspace_input_creates_one_candidate_with_frozen_hash_and_no_provider_dispatch(linked):
    s = linked
    state, client = s['api'].app.state, s['api']
    path = s['path'].with_name('new-input.md')
    path.write_text('EXPLICIT_NATIVE_FILE_INPUT_PRESERVE_BYTES')
    base = state.local_workspaces.get(s['pid'], s['target']['source_ref']['workspace_id'])
    source = state.local_workspaces.read_saved_snapshot(s['pid'], base['id'], path.name)['source_ref']
    scope = client.get('/api/conversations/'+s['cid']+'/scope').json()
    body = {'operation_id': 'native_file_capture', 'raw_text': 'Continue with this selected saved file',
        'provider_profile_ref': None, 'conversation_ref': scope['conversation_ref'],
        **scope['next_message'], 'file_source_ref': source}
    admitted = client.post('/api/desktop-host/native-messages', json=body)
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()['status'] == 'NOT_CONFIGURED'
    native = state.native_messages
    record = native.ws._read(native.path(body['operation_id']), authority=False)
    assert record['request'] == NativeQuestionInput.model_validate(body).request_dict()
    assert record['request_sha256'] == digest(record['request'])
    assert record['context_file_proposal']['proposal_status'] == 'ADDED'
    assert record['context_file_proposal']['state'] == 'CANDIDATE'
    assert record['execution'] is None and record.get('owner') is None
    cid, oid = s['cid'], record['context_file_proposal']['origin_id']
    initial = state.context_graph.associations('project', conversation_id=cid)
    assert len([row for row in initial['items'] if row['origin_id'] == oid]) == 1
    replayed = client.post('/api/desktop-host/native-messages', json=body)
    assert replayed.status_code == 200 and replayed.json()['status'] == 'NOT_CONFIGURED'
    assert replayed.json()['operation_id'] == body['operation_id']
    assert state.context_graph.associations('project', conversation_id=cid) == initial
    assert path.read_text() == 'EXPLICIT_NATIVE_FILE_INPUT_PRESERVE_BYTES'
