"""Real ASGI + original SQLite sources; authored for concentrated debug."""
from test_conversation_directory import api


def test_global_defaults_and_readonly_fragment_routes_preserve_originals_without_model(api):
    a = api.post('/api/projects', json={'name': 'Source project'}).json()['id']
    b = api.post('/api/projects', json={'name': 'Target project'}).json()['id']
    scope = api.put('/api/context/scopes/target', json={'operation_id': 'scope', 'expected_revision': 0,
        'kind': 'PROJECT', 'project_id': b, 'label': 'Target'})
    assert scope.status_code == 200, scope.text
    initial = api.get('/api/context/defaults').json()
    updated = api.put('/api/context/defaults', json={'operation_id': 'defaults', 'expected_revision': initial['revision'],
        'policy': {'char_budget': 1000}})
    assert updated.status_code == 200 and updated.json()['source_contents_shared'] is False
    store = api.app.state.store
    original = b'SELECTED_FRAGMENT\nUNSELECTED_PRIVATE_DETAILS'
    document = store.save_document(a, 'exact.md', original, 'UPLOAD')
    ref = {'kind': 'DOCUMENT', 'project_id': a, 'object_id': document['id'],
        'revision': document['revision'], 'sha256': document['sha256'], 'offset': 0, 'length': 17}
    result = api.post('/api/context/read-grants/preview', json={'scope_id': 'target', 'source': ref})
    assert result.status_code == 200, result.text
    preview = result.json()
    assert preview['text'] == 'SELECTED_FRAGMENT'
    body = {'operation_id': 'grant', 'scope_id': 'target', 'source': preview['source_ref'],
        'expected_revision': preview['expected_revision'], 'expected_grant_revision': preview['expected_grant_revision'],
        'review_sha256': preview['review_sha256'], 'state': 'ACTIVE'}
    grant = api.post('/api/context/read-grants', json=body)
    assert grant.status_code == 200, grant.text
    assert api.post('/api/context/read-grants', json=body).json() == grant.json()
    listing = api.get('/api/context/scopes/target/read-grants').json()
    assert listing['items'][0]['source_project_id'] == a and listing['source_contents_read'] is False
    selected = api.post('/api/context/selections', json={'operation_id': 'select', 'scope_id': 'target'}).json()
    material = api.get('/api/context/selections/'+selected['selection_id'], params={'project_id': b})
    assert material.status_code == 200, material.text
    assert material.json()['items'][0]['text'] == 'SELECTED_FRAGMENT'
    assert material.json()['items'][0]['source_ref']['project_id'] == a
    assert store.document(a, document['id'])['raw_bytes'] == original
    receipt = api.get('/api/context/operations/grant').json()
    assert 'UNSELECTED_PRIVATE_DETAILS' not in str(receipt) and receipt['execution_authorized'] is False
    off = api.put('/api/context/defaults', json={'operation_id': 'disable', 'expected_revision': 1, 'policy': {'retrieve': False}})
    assert off.status_code == 200
    assert api.get('/api/context/selections/'+selected['selection_id'], params={'project_id': b}).status_code == 409
