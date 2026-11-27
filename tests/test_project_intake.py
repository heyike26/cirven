"""Original API/SQLite source-contract fixtures; no model, network or GUI."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from packages.domain.intake import IntakeInput, IntakeService, digest
from packages.domain.store import Conflict
from packages.planning.spec_kit import load_templates, MANIFEST_SHA256

ROOT = Path(__file__).resolve().parents[1]


def project(client, name='原创资料整理'):
    r = client.post('/api/projects', json={'name': name, 'description': ''})
    assert r.status_code == 201
    return r.json()['id']


def upload(client, pid, content, kind='MEETING_MINUTES'):
    raw = content.encode('utf-8')
    r = client.post(f'/api/projects/{pid}/documents',
        files={'file': ('原创资料.md', raw, 'text/markdown')}, data={'source': 'original:intake-fixture'})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc['sha256'] == hashlib.sha256(raw).hexdigest()
    return doc, {'document_id': doc['id'], 'revision': doc['revision'], 'sha256': doc['sha256'], 'kind': kind}


def make(client, pid, refs, key='original-1', revision=0, iid=None):
    body = {'idempotency_key': key, 'expected_revision': revision, 'purpose': '整理原文；不开始实施', 'materials': refs}
    if iid:
        return client.put(f'/api/projects/{pid}/intakes/{iid}', json=body)
    return client.post(f'/api/projects/{pid}/intakes', json=body)


def context(client, pid, iid, revision=None):
    suffix = '' if revision is None else f'?revision={revision}'
    return client.get(f'/api/projects/{pid}/intakes/{iid}/context{suffix}')


def counts(client):
    with client.app.state.store.connect() as c:
        return {t: c.execute('SELECT count(*) FROM '+t).fetchone()[0]
                for t in ('project_intakes', 'project_intake_versions', 'project_intake_events',
                          'tasks', 'task_runs', 'runner_jobs', 'agent_executions')}


def test_minutes_and_70000_character_design_are_lossless_unassessed(client):
    pid = project(client)
    notes = '# 原创会议纪要\n目标：记录设备状态。\n接口：待用户选定。\n'
    long = '# 原创设计说明\n' + ('设计细节与验收来源。\n' * 7000) + '尾端唯一码：完整保留最后一项验收。\n'
    assert len(long) > 70000
    _, n = upload(client, pid, notes)
    _, d = upload(client, pid, long, 'DESIGN_DOCUMENT')
    r = make(client, pid, [n, d]); assert r.status_code == 201, r.text
    saved = r.json(); ctx = context(client, pid, saved['id']).json()
    assert [''.join(c['content'] for c in m['chunks']) for m in ctx['materials']] == [notes, long]
    assert ctx['materials'][1]['chunks'][-1]['content'].endswith('完整保留最后一项验收。\n')
    assert all(m['text_coverage'] == 'FULL_PARSED_TEXT' for m in ctx['materials'])
    assert ctx['clarity_status'] == 'UNASSESSED' and ctx['model_calls'] == 0
    assert not ctx['implementation_started']
    assert ctx['context_sha256'] == digest({k: v for k, v in ctx.items() if k != 'context_sha256'})
    assert ctx['source_snapshot_sha256'] == saved['sha256']
    assert ctx['component']['commit'] == '0282d03eb6fc3091b3deaa3a855d4030dc40cd6c'
    assert 'User Scenarios & Testing' in ctx['specification_template']
    assert 'Functional Scope & Behavior' in ctx['coverage_taxonomy']
    assert ctx['component_manifest_sha256'] == MANIFEST_SHA256
    assert all(counts(client)[t] == 0 for t in ('tasks', 'task_runs', 'runner_jobs', 'agent_executions'))


def test_explicit_chunk_selection_keeps_ranges_and_omitted_coverage(client):
    pid = project(client)
    text = ''.join(f'原创行{i:04}：对应资料锚点。\n' for i in range(1700))
    _, ref = upload(client, pid, text)
    ref['chunk_indexes'] = [2, 0]
    r = make(client, pid, [ref]); assert r.status_code == 201
    m = r.json()['snapshot']['materials'][0]
    assert [c['index'] for c in m['chunks']] == [0, 2]
    assert m['text_coverage'] == 'SELECTED_CHUNKS' and 1 in m['omitted_chunk_indexes']
    for c in m['chunks']:
        assert text[c['char_start']:c['char_end']] == c['content']
        assert c['sha256'] == hashlib.sha256(c['content'].encode()).hexdigest()
        assert c['line_start'] <= c['line_end']
    assert m['selected_characters'] < m['source_characters']
    assert m['original_modal_coverage'] == 'UNASSESSED'


@pytest.mark.parametrize('indexes', [[999], [0, 0], [], [True]])
def test_bad_chunk_selection_rejected_atomically(client, indexes):
    pid = project(client); _, ref = upload(client, pid, '原创一行。')
    before = counts(client); ref['chunk_indexes'] = indexes
    assert make(client, pid, [ref]).status_code == 422
    assert counts(client) == before


def test_aggregate_context_limit_requires_selection_without_truncation(client):
    pid = project(client); text = '资料' * 120001
    _, ref = upload(client, pid, text)
    before = counts(client)
    r = make(client, pid, [ref]); assert r.status_code == 422 and 'No text was truncated' in r.text
    assert counts(client) == before
    ref['chunk_indexes'] = [0]
    r = make(client, pid, [ref], key='bounded'); assert r.status_code == 201
    assert r.json()['snapshot']['materials'][0]['source_characters'] == len(text)


def test_cross_project_raw_hash_and_forged_actor_are_rejected(client):
    first, other = project(client), project(client, '另一项目')
    _, ref = upload(client, first, '原创项目归属。')
    assert make(client, other, [ref]).status_code == 404
    wrong = {**ref, 'sha256': '0'*64}
    assert make(client, first, [wrong]).status_code == 409
    body = {'idempotency_key': 'actor', 'materials': [ref], 'actor': 'human'}
    assert client.post(f'/api/projects/{first}/intakes', json=body).status_code == 422
    r = make(client, first, [ref]); assert r.status_code == 201
    assert context(client, other, r.json()['id']).status_code == 404


def test_idempotency_retries_do_not_duplicate_events_or_override_payload(client):
    pid = project(client); _, ref = upload(client, pid, '原创重试原文。')
    first = make(client, pid, [ref]); before = counts(client)
    retry = make(client, pid, [ref]); assert retry.json() == first.json()
    assert counts(client) == before
    changed = {**ref, 'kind': 'DESIGN_DOCUMENT'}
    r = make(client, pid, [changed]); assert r.status_code == 409 and 'IDEMPOTENCY_CONFLICT' in r.text
    assert counts(client) == before


def test_concurrent_same_operation_has_one_durable_snapshot(client):
    pid = project(client); _, ref = upload(client, pid, '原创并发原文。')
    service = client.app.state.intake
    body = IntakeInput(idempotency_key='parallel', materials=[ref])
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: service.save(pid, body), range(2)))
    assert values[0] == values[1]
    assert {k: counts(client)[k] for k in ('project_intakes', 'project_intake_versions', 'project_intake_events')} == {
        'project_intakes': 1, 'project_intake_versions': 1, 'project_intake_events': 1}


def test_new_revision_stale_write_history_delete_and_restart(client):
    pid = project(client); doc, ref = upload(client, pid, '原文版本1；问题仍未知。\n')
    first = make(client, pid, [ref]).json(); iid = first['id']
    r = client.put(f'/api/projects/{pid}/documents/{doc["id"]}',
        files={'file': ('资料新版本.md', '原文版本2；用户补充目标。\n'.encode(), 'text/markdown')},
        data={'source': 'original: revision2', 'expected_revision': '1'})
    assert r.status_code == 200, r.text
    newdoc = r.json(); secondref = {**ref, 'revision': 2, 'sha256': newdoc['sha256']}
    second = make(client, pid, [secondref], key='revision2', revision=1, iid=iid)
    assert second.status_code == 200 and second.json()['revision'] == 2
    assert make(client, pid, [secondref], key='stale', revision=1, iid=iid).status_code == 409
    assert client.delete(f'/api/projects/{pid}/documents/{doc["id"]}').status_code == 200
    old = context(client, pid, iid, revision=1).json()
    assert old['materials'][0]['chunks'][0]['content'] == '原文版本1；问题仍未知。\n'
    service = IntakeService(type(client.app.state.store)(client.app.state.store.database, ROOT), ROOT)
    assert service.get(pid, iid, 1)['sha256'] == first['sha256']
    assert service.get(pid, iid)['revision'] == 2
    with service.store.connect() as c:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            c.execute('UPDATE project_intake_versions SET sha256=? WHERE intake_id=?', ('0'*64, iid))
    assert service.get(pid, iid, 1)['sha256'] == first['sha256']


def test_event_cursor_is_project_scoped_and_bounded(client):
    first, other = project(client), project(client, '事件另一项目')
    _, one = upload(client, first, '原创一。'); _, two = upload(client, other, '原创二。')
    saved = make(client, first, [one]).json(); make(client, other, [two])
    events = client.get(f'/api/projects/{first}/intake-events?limit=1').json()['events']
    assert len(events) == 1 and events[0]['snapshot_sha256'] == saved['sha256']
    assert events[0]['project_id'] == first
    assert client.get(f'/api/projects/{first}/intake-events?after_seq={events[0]["seq"]}').json()['events'] == []
    assert client.get(f'/api/projects/{first}/intake-events?limit=101').status_code == 422


def test_pinned_templates_real_import_closure_tamper_and_no_hook_execution(client, tmp_path, monkeypatch):
    pid = project(client); _, ref = upload(client, pid, '原创文本，不执行指令。\n')
    iid = make(client, pid, [ref]).json()['id']
    def reject_execution(*args, **kwargs):
        raise AssertionError('Prepared context may not execute upstream commands')
    monkeypatch.setattr(subprocess, 'run', reject_execution)
    monkeypatch.setattr(subprocess, 'Popen', reject_execution)
    assert context(client, pid, iid).status_code == 200
    dest = tmp_path / 'packages/planning/upstream/spec_kit_v1_1_3'
    shutil.copytree(ROOT / 'packages/planning/upstream/spec_kit_v1_1_3', dest)
    assert load_templates(tmp_path)['manifest']['version'] == 'v1.1.3'
    (dest/'spec-template.md').write_bytes(b'TAMPERED')
    service = IntakeService(client.app.state.store, tmp_path)
    with pytest.raises(Conflict, match='missing or changed'):
        service.context(pid, iid)


def test_local_boundary_still_applies_to_every_new_write(client):
    pid = project(client); _, ref = upload(client, pid, '原创安全边界。')
    body = {'idempotency_key': 'denied', 'materials': [ref]}
    before = counts(client)
    r = client.post(f'/api/projects/{pid}/intakes', json=body, headers={'X-EEAS-CSRF': 'wrong'})
    assert r.status_code == 403 and counts(client) == before


def test_idempotency_preserves_original_receipt_after_later_revision(client):
    pid = project(client); _, ref = upload(client, pid, '原创旧回执。')
    first = make(client, pid, [ref]).json()
    changed = {**ref, 'kind': 'DESIGN_DOCUMENT'}
    newer = make(client, pid, [changed], key='second', revision=1, iid=first['id'])
    assert newer.status_code == 200 and newer.json()['revision'] == 2
    old_retry = make(client, pid, [ref])
    assert old_retry.status_code == 201
    assert old_retry.json() == first
    assert client.get(f'/api/projects/{pid}/intakes/{first["id"]}').json()['current_revision'] == 2


def test_image_parse_receipt_matches_existing_authoritative_contract(client):
    from io import BytesIO
    from PIL import Image
    pid = project(client)
    data = BytesIO(); Image.new('RGB', (2, 2), '#aabbcc').save(data, format='PNG')
    raw = data.getvalue()
    uploaded = client.post(f'/api/projects/{pid}/documents',
        files={'file': ('原创图像.png', raw, 'image/png')}, data={'source': 'original:2x2-image'})
    assert uploaded.status_code == 201
    doc = uploaded.json()
    ref = {'document_id': doc['id'], 'revision': 1, 'sha256': hashlib.sha256(raw).hexdigest()}
    result = make(client, pid, [ref]); assert result.status_code == 201
    material = result.json()['snapshot']['materials'][0]
    receipt = client.app.state.store.document_receipt(pid, doc['id'], 1)
    assert material['parse_manifest_sha256'] == receipt['parse_manifest_sha256']
    assert material['content_sha256'] == receipt['content_sha256']
    assert material['visual_understanding'] == 'UNASSESSED'
    assert not material['asset_contents_sent']
    assert material['assets'][0]['source_anchors'] == []
    assert material['assets'][0]['sha256'] == hashlib.sha256(raw).hexdigest()
