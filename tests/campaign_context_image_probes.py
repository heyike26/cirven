"""Bound long-source text and selected image reach installed SDK, without vision claims."""
import base64
import json
from pathlib import Path

from campaign_native_probes import fixture_route, prepared, proposal
from campaign_pdf_probes import make_pdf
from campaign_probes import sha
from packages.adapters import claude_sdk
from test_claude_sessions import NativeFactory
from test_task_sdk_api import claude_review_runtime, until_review


def test_source_context_images(client, monkeypatch):
    captured, evidence, pid, doc, base, quote, parts, context = prepared(client, 'source-context')
    assert len(captured['text']) >= 70000, 'Actual long source required; no public source padding'
    full = client.post(base + '/context', json={'revision': 1})
    complete = len(captured['text']) <= 120000 and len(captured['raw']) <= 220000
    assert full.status_code == (200 if complete else 422), full.text
    if complete:
        assert full.json()['coverage'] == 'COMPLETE' and full.json()['truncated'] is False
        assert ''.join(p['content'] for p in full.json()['chunks']) == captured['text']
    else:
        assert 'No text was truncated' in full.json()['error']['message']
    assert context['included_chunks'] and not context['truncated']
    last = parts[-1]['index']
    tail = client.post(base + '/context', json={'revision': 1, 'chunk_indexes': [last]})
    assert tail.status_code == 200 and tail.json()['included_chunks'] == [last]
    assert tail.json()['chunks'][0]['content'] == parts[-1]['content']
    denials = []
    for body in ({'revision': 1, 'byte_budget': 1}, {'revision': 1, 'char_budget': 1},
                 {'revision': 1, 'chunk_indexes': [last, last]}, {'revision': 1, 'chunk_indexes': [len(parts)]}):
        denied = client.post(base + '/context', json=body)
        assert denied.status_code == 422
        denials.append({'request': body, 'response': denied.json()})

    selected = [{'line_start': quote['line_start'], 'text': quote['quote'].expandtabs(4), 'raw_text': quote['raw_line'],
                 'verified_original_source_line': quote.get('verified_source_line')}]
    pdf, png, derivation = make_pdf(selected)
    uploaded = client.post(f'/api/projects/{pid}/documents', files={'file': ('context-source-derived.pdf', pdf)},
        data={'source': 'derived:context-original-body:parent=' + sha(captured['raw'])})
    assert uploaded.status_code == 201, uploaded.text
    image_doc = uploaded.json();image_base = f'/api/projects/{pid}/documents/{image_doc["id"]}'
    image_row = client.get(image_base, params={'revision': 1}).json()
    asset = client.get(image_base + '/assets', params={'revision': 1}).json()['assets'][0]
    pixels = client.get(image_base + '/assets/' + asset['asset_id'], params={'revision': 1})
    assert pixels.status_code == 200 and sha(pixels.content) == asset['sha256']
    assert image_doc['sha256'] == sha(pdf)
    requirement = proposal(doc, quote)
    requirement['confirmed'] = True  # Explicit manual fixture action; never automatic model confirmation.
    pdf_page = image_row['outline']['pages'][0]
    requirement['requirements'][0]['source_refs'].append({'document_id': image_doc['id'], 'revision': 1,
        'sha256': image_doc['sha256'], 'line_start': pdf_page['line_start'] + 1, 'line_end': pdf_page['line_start'] + 1})
    confirmed = client.post(f'/api/projects/{pid}/requirements', json=requirement)
    assert confirmed.status_code == 201, confirmed.text
    req = confirmed.json()
    task_response = client.post(f'/api/projects/{pid}/tasks', json={'goal': 'Original readonly context/image fixture: ' + quote['quote'],
        'requirement_refs': [{'requirement_id': req['id'], 'revision': req['revision'], 'sha256': req['sha256']}]})
    assert task_response.status_code == 201, task_response.text
    task = task_response.json()
    route = f'/api/projects/{pid}/tasks/{task["id"]}/runs'
    started = client.post(route, json={'revision': 1, 'operation_id': 'context-image-formal'})
    assert started.status_code == 202, started.text
    run = started.json();run_route = route + '/' + run['id']
    scope_response = client.get(run_route + '/native-review-scope')
    assert scope_response.status_code == 200, scope_response.text
    scope = scope_response.json()
    frozen = json.loads(run['frozen_inputs'])['documents']
    assert {d['document_id']: d['sha256'] for d in frozen} == {doc['id']: doc['sha256'], image_doc['id']: image_doc['sha256']}
    ref = {'document_id': image_doc['id'], 'revision': 1, 'asset_id': asset['asset_id'], 'sha256': asset['sha256']}
    body = {'operation_id': 'context-image-native', 'task_binding': scope['task_binding'], 'input_hash': scope['input_hash'],
        'source_hash': scope['source_hash'], 'include_images': True, 'image_selections': [ref],
        'chunk_selections': [{'document_id': doc['id'], 'revision': 1, 'chunk_indexes': context['included_chunks']}],
        'needs_tools': False, 'native': {}}
    factory = NativeFactory([0.002])
    monkeypatch.setattr(claude_sdk, 'ClaudeSDKClient', factory)
    fixture_route(monkeypatch, True)
    monkeypatch.setenv('EEAS_MODEL_VISION_ENABLED', '0')
    no_vision = client.post(run_route + '/native-reviews', json=body)
    assert no_vision.status_code == 503 and 'MODEL_VISION_NOT_ENABLED' in no_vision.json()['error']['reasons']
    assert not factory.transports
    monkeypatch.setenv('EEAS_MODEL_VISION_ENABLED', '1')  # Original fixture profile capability, never company qualification.
    invalid_images = []
    for changed in ({**body, 'image_selections': [{**ref, 'sha256': '0' * 64}]},
                    {**body, 'image_selections': [ref, ref]},
                    {**body, 'include_images': False}):
        denied = client.post(run_route + '/native-reviews', json=changed)
        assert denied.status_code == 422, denied.text
        invalid_images.append(denied.json())
    assert not factory.transports
    with client.app.state.store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM task_native_reviews').fetchone()[0] == 0
    submitted = client.post(run_route + '/native-reviews', json=body)
    assert submitted.status_code == 202, submitted.text
    final = until_review(client, pid, submitted.json()['admission_id'], {'RETURNED'})
    user = next(r for r in factory.transports[0].writes if r['type'] == 'user')
    images = [p for p in user['message']['content'] if p['type'] == 'image']
    assert len(images) == 1 and base64.b64decode(images[0]['source']['data']) == pixels.content
    request = json.loads(final['native_execution']['request'])
    assert request['image_refs'] == [ref] and final['verification_status'] == 'NOT_RUN'
    assert client.get(f'/api/projects/{pid}/runs/{run["id"]}').json() == run
    with client.app.state.store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM runner_jobs').fetchone()[0] == 0
    evidence.save('actual-context-source-derived.pdf', pdf)
    evidence.save('actual-context-source-preview.png', png)
    evidence.save('actual-context-image-asset.bin', pixels.content)
    evidence.save('actual-long-image-context.json', {'parent_raw_sha256': sha(captured['raw']),
        'full_context': {'status': full.status_code, 'result': full.json()}, 'selected_context': context,
        'selected_tail': tail.json(), 'budget_denials': denials, 'selected_original': quote,
        'source_derivation': derivation, 'derived_pdf_sha256': sha(pdf), 'derived_document': image_row,
        'asset_ref': ref, 'formal_run': run, 'frozen_scope': scope, 'request': request,
        'no_vision': no_vision.json(), 'invalid_images': invalid_images, 'native_review': final,
        'sdk_writes': factory.transports[0].writes, 'model_vision': 'NOT_VALIDATED', 'ocr': 'NOT_EXECUTED',
        'company_model_calls': 0, 'converter_sha256': sha(Path(__file__).read_bytes())})
    evidence.observe()
