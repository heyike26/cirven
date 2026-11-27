"""Exact original operation queries use private metadata only, without recovery."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.template_profiles import register_template_profile_routes
from packages.domain.local_workspaces import canonical, digest, sha
from packages.domain.template_profiles import ProfileImport
from packages.domain.store import InvalidInput
from test_template_profile_imports import (
    scene, api, import_body, importing, review_import, ref, _pending_scene,
)


KEYS = {'operation_hash', 'request_sha256', 'method', 'status', 'profile_id',
        'result_profile_ref', 'recovery'}


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink(): result[name] = {'link': os.readlink(path)}
        elif path.is_file(): result[name] = {'bytes': path.stat().st_size, 'sha256': sha(path.read_bytes())}
        elif path.is_dir(): result[name] = {'directory': True}
    return result


def request_record(e, pid, operation):
    service = e['app'].state.template_profiles
    return service._read(service._path(pid, 'operations', digest(operation), 'request.json'))


def query(e, monkeypatch, pid, operation, request_sha256, expected=200):
    """Record every GET's names/bytes and deny all writers/live-source seams."""
    service = e['app'].state.template_profiles; ws = e['app'].state.local_workspaces
    before = inventory(e['data'])

    def forbidden(*args, **kwargs):
        raise AssertionError('Operation GET called a writer, source reader, or recovery seam')

    with monkeypatch.context() as patch:
        for name in ('_write', '_publish_import_record', '_operation', '_import_operation',
                     '_import_snapshot', 'get', 'create', 'review', 'import_reviewed'):
            patch.setattr(service, name, forbidden)
        for name in ('read', 'read_saved_snapshot', '_raw', '_observe', 'check_sources'):
            if hasattr(ws, name): patch.setattr(ws, name, forbidden)
        value = api(e, 'GET', f'/api/projects/{pid}/template-profiles/operations/{operation}'
                    f'?request_sha256={request_sha256}', expected=expected)
    after = inventory(e['data'])
    check = {'operation_hash': digest(operation), 'status': expected,
             'before': before, 'after': after, 'identical': before == after}
    e.setdefault('query_inventories', []).append(check)
    if os.environ.get('EEAS_EVIDENCE_DIR'):
        folder = Path(os.environ['EEAS_EVIDENCE_DIR']) / 'query-inventories' / e['b']
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{len(e["query_inventories"]):03d}.json').write_text(canonical(check) + '\n')
    assert before == after, 'Read-only GET changed persisted state'
    if expected == 200:
        assert set(value) == KEYS
        assert value['operation_hash'] == digest(operation)
        assert value['request_sha256'] == request_sha256
    return value


def test_completed_queries_preserve_exact_historical_refs_without_sources(scene, monkeypatch):
    e = scene
    draft = importing(e, import_body(e, 'query-import'))
    reviewed = review_import(e, draft, 'query-review')
    latest = review_import(e, reviewed, 'query-review-next')
    assert latest['revision'] == 3
    cases = [(e['a'], 'learn-source', None),
             (e['a'], 'learn-selected-rules', ref(e['source_draft'])),
             (e['a'], 'source-review', ref(e['approved'])),
             (e['b'], 'query-import', ref(draft)),
             (e['b'], 'query-review', ref(reviewed)),
             (e['b'], 'query-review-next', ref(latest))]
    # Deleted original report cannot turn this private metadata lookup into a
    # live source observation or import replay. Existing immutable refs remain.
    (e['reference'] / '旧项目写法样例.docx').unlink()
    for pid, operation, expected_ref in cases:
        record = request_record(e, pid, operation)
        value = query(e, monkeypatch, pid, operation, record['sha256'])
        assert value['status'] == 'COMPLETED' and value['recovery'] is None
        assert value['method'] == record['input']['method']
        if expected_ref is None:
            result = e['app'].state.template_profiles._read(
                e['app'].state.template_profiles._path(pid, 'operations', digest(operation), 'result.json'))
            expected_ref = e['app'].state.template_profiles._core(pid, result['id'], result['revision'])['profile_ref']
        assert value['result_profile_ref'] == expected_ref
        assert value['profile_id'] == expected_ref['id']
    # Recreate the actual API/service against the same disk, with no transfer of
    # the original profile service's object state or route closure.
    restarted = FastAPI(); restarted.state.local_workspaces = e['app'].state.local_workspaces
    register_template_profile_routes(restarted, e['store'], e['data'])
    with TestClient(restarted) as client:
        fresh = {**e, 'app': restarted, 'client': client}
        again = query(fresh, monkeypatch, e['b'], 'query-review', request_record(e, e['b'], 'query-review')['sha256'])
    assert again['result_profile_ref'] == ref(reviewed) and again['result_profile_ref'] != ref(latest)


def partial_stage(service, monkeypatch, phase):
    original = service._write; failed = []

    def interrupt(path, payload, replace=False):
        if not failed and path.name.startswith('.pending-import-' + phase + '-'):
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = (canonical({'payload': payload, 'sha256': digest(payload)}) + '\n').encode()[:31]
            with path.open('xb') as stream: stream.write(raw); stream.flush()
            failed.append(path)
            raise OSError(28, 'QA partial original operation metadata')
        return original(path, payload, replace)

    monkeypatch.setattr(service, '_write', interrupt)
    return original, failed


def test_missing_intent_pending_and_advanced_head_never_claim_completed(scene, monkeypatch):
    e = scene; service = e['app'].state.template_profiles
    absent = query(e, monkeypatch, e['b'], 'never-recorded', '0' * 64)
    assert absent == {'operation_hash': digest('never-recorded'), 'request_sha256': '0' * 64,
        'method': None, 'status': 'NOT_FOUND', 'profile_id': None, 'result_profile_ref': None, 'recovery': None}
    body = import_body(e, 'interrupted-intent')
    request_sha = digest({'method': 'IMPORT_REVIEWED', **ProfileImport.model_validate(body).model_dump(exclude={'operation_id'})})
    original, failed = partial_stage(service, monkeypatch, 'request.json')
    with pytest.raises(OSError, match='QA partial'): service.import_reviewed(e['b'], body)
    monkeypatch.setattr(service, '_write', original)
    failed_bytes = failed[0].read_bytes(); assert len(failed_bytes) == 31
    intent = query(e, monkeypatch, e['b'], body['operation_id'], request_sha)
    assert intent['status'] == 'INTENT_ONLY' and intent['method'] is intent['profile_id'] is None
    assert intent['result_profile_ref'] is None and intent['recovery'] == 'ORIGINAL_OPERATION_ONLY'
    query(e, monkeypatch, e['b'], body['operation_id'], 'f' * 64, 409)

    service, root, operation, usable, failed_head = _pending_scene(e, monkeypatch)
    pending = query(e, monkeypatch, e['b'], 'explicit-import', request_record(e, e['b'], 'explicit-import')['sha256'])
    assert pending['status'] == 'PENDING' and pending['method'] == 'IMPORT_REVIEWED'
    assert pending['profile_id'] == root.name and pending['result_profile_ref'] is None
    assert not (root / 'head.json').exists()

    # A real review reached immutable v2 and advanced head, but its original
    # result publication failed. GET must still say PENDING, not infer success.
    draft = importing(e, {**import_body(e, 'review-result-import'), 'name': '待查审阅结果'})
    review_body = {'operation_id': 'interrupted-review-result', 'expected_revision': 1,
        'accepted_rule_ids': ['tasks'], 'reason': '原创测试，不代表业务审批。'}
    original, failed_result = partial_stage(service, monkeypatch, 'result.json')
    with pytest.raises(OSError, match='QA partial'): service.review(e['b'], draft['id'], review_body)
    monkeypatch.setattr(service, '_write', original)
    current = service._core(e['b'], draft['id']); assert current['revision'] == 2
    record = request_record(e, e['b'], review_body['operation_id'])
    pending_review = query(e, monkeypatch, e['b'], review_body['operation_id'], record['sha256'])
    assert pending_review['status'] == 'PENDING' and pending_review['result_profile_ref'] is None
    assert pending_review['method'] == 'REVIEW' and pending_review['profile_id'] == draft['id']
    # Only an explicit original POST outside the query can finish recovery.
    recovered = api(e, 'POST', e['broot'] + '/' + draft['id'] + '/review', review_body)
    completed = query(e, monkeypatch, e['b'], review_body['operation_id'], record['sha256'])
    assert completed['status'] == 'COMPLETED' and completed['result_profile_ref'] == ref(recovered)
    assert failed[0].read_bytes() == failed_bytes and len(failed_head.read_bytes()) == len(failed_result[0].read_bytes()) == 31

    # A request may be structurally valid yet rejected after persisting its
    # original key. A lost 422 response must remain explicitly retryable, not
    # become a GET integrity error or a fabricated completed/rejected result.
    duplicate = importing(e, {**import_body(e, 'duplicate-import'), 'name': '章节冲突原请求'})
    edits = deepcopy(duplicate['draft_rules'][:2])
    for rule in edits: rule.update(section_key='TASKS', heading='重复任务映射')
    rejected_body = {'operation_id': 'rejected-review-response-lost', 'expected_revision': 1,
        'accepted_rule_ids': ['overview', 'tasks'], 'edits': edits, 'reason': '原响应丢失的冲突审阅测试。'}
    with pytest.raises(InvalidInput, match='only one approved mapping'):
        service.review(e['b'], duplicate['id'], rejected_body)
    rejected_record = request_record(e, e['b'], rejected_body['operation_id'])
    unknown = query(e, monkeypatch, e['b'], rejected_body['operation_id'], rejected_record['sha256'])
    assert unknown['status'] == 'PENDING' and unknown['result_profile_ref'] is None
    api(e, 'POST', e['broot'] + '/' + duplicate['id'] + '/review', rejected_body, 422)
    after_rejection = query(e, monkeypatch, e['b'], rejected_body['operation_id'], rejected_record['sha256'])
    assert after_rejection == unknown


def rewrite(path, payload):
    path.write_text(canonical({'payload': payload, 'sha256': digest(payload)}) + '\n')


def test_queries_reject_corrupt_foreign_or_mismatched_metadata_without_writes(scene, monkeypatch, tmp_path):
    e = scene; service = e['app'].state.template_profiles
    draft = importing(e, import_body(e, 'tamper-import'))
    reviewed = review_import(e, draft, 'tamper-review')
    latest = review_import(e, reviewed, 'tamper-review-next')
    operation = service._path(e['b'], 'operations', digest('tamper-import'))
    record = request_record(e, e['b'], 'tamper-import'); request_sha = record['sha256']
    request_path = operation / 'request.json'; result_path = operation / 'result.json'
    counters = tmp_path / 'rejected-records'; counters.mkdir()

    def corrupt(path, content, op='tamper-import', request_hash=request_sha):
        old = path.read_bytes(); path.write_bytes(content)
        (counters / f'{len(list(counters.iterdir())):02d}-{path.name}').write_bytes(content)
        try: query(e, monkeypatch, e['b'], op, request_hash, 409)
        finally: path.write_bytes(old)

    corrupt(request_path, b'{"payload":')
    changed = deepcopy(record); changed['sha256'] = 'a' * 64
    corrupt(request_path, (canonical({'payload': changed, 'sha256': digest(changed)}) + '\n').encode())
    changed = deepcopy(record); changed['input']['unknown_authority'] = True
    changed['sha256'] = digest(changed['input'])
    corrupt(request_path, (canonical({'payload': changed, 'sha256': digest(changed)}) + '\n').encode(), request_hash=changed['sha256'])
    for bad_result in ({'id': draft['id'], 'revision': True}, {'id': draft['id'], 'revision': 2},
                       {'id': e['source_draft']['id'], 'revision': 1},
                       {'id': draft['id'], 'revision': 1, 'unbound': 'field'}):
        corrupt(result_path, (canonical({'payload': bad_result, 'sha256': digest(bad_result)}) + '\n').encode())
    claim = operation / 'claims' / request_sha
    claim.rename(claim.with_name('f' * 64))
    try: query(e, monkeypatch, e['b'], 'tamper-import', request_sha, 409)
    finally: claim.with_name('f' * 64).rename(claim)
    extra = operation / 'claims' / ('e' * 64); extra.mkdir()
    try: query(e, monkeypatch, e['b'], 'tamper-import', request_sha, 409)
    finally: extra.rmdir()

    # The historical reviewed version no longer owns current head, so a valid
    # wrapper alone must not bypass reconstruction of its frozen decisions.
    version = service._path(e['b'], draft['id'], 'versions', '2.json')
    payload = service._read(version); payload['review']['reason'] = '篡改旧审阅理由'
    review_sha = request_record(e, e['b'], 'tamper-review')['sha256']
    corrupt(version, (canonical({'payload': payload, 'sha256': digest(payload)}) + '\n').encode(), 'tamper-review', review_sha)
    assert latest['revision'] == 3

    # Copying valid metadata from A is not authority in B. A's source scope and
    # result identity cannot be transplanted even with valid outer hashes.
    source_op = service._path(e['a'], 'operations', digest('learn-selected-rules'))
    target_op = service._path(e['b'], 'operations', digest('learn-selected-rules')); target_op.mkdir()
    for name in ('request.json', 'result.json'): (target_op / name).write_bytes((source_op / name).read_bytes())
    query(e, monkeypatch, e['b'], 'learn-selected-rules', request_record(e, e['a'], 'learn-selected-rules')['sha256'], 409)
    # Knowing another project's operation key does not enumerate its metadata.
    foreign = query(e, monkeypatch, e['b'], 'source-review', request_record(e, e['a'], 'source-review')['sha256'])
    assert foreign['status'] == 'NOT_FOUND' and foreign['method'] is None
    query(e, monkeypatch, e['b'], 'tamper-import', 'd' * 64, 409)


def test_operation_query_validates_original_key_and_required_frozen_hash(scene, monkeypatch):
    e = scene
    for operation, suffix in [('learn-source', ''), ('learn-source', '?request_sha256=' + 'A' * 64),
                              ('learn-source', '?request_sha256=' + '0' * 63),
                              ('bad.dot', '?request_sha256=' + '0' * 64),
                              ('x' * 101, '?request_sha256=' + '0' * 64)]:
        before = inventory(e['data'])
        api(e, 'GET', e['aroot'] + '/operations/' + operation + suffix, expected=422)
        assert inventory(e['data']) == before
    # Invalid private leftovers never become a NOT_FOUND or guessed method.
    service = e['app'].state.template_profiles
    directory = service._path(e['b'], 'operations', digest('unclaimed-empty')); directory.mkdir(parents=True)
    query(e, monkeypatch, e['b'], 'unclaimed-empty', '0' * 64, 409)
    query(e, monkeypatch, 'unknown-project', 'learn-source', '0' * 64, 404)
