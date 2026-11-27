"""Actual exports and immutable native feedback, without mock producers."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app
from packages.domain.local_workspaces import digest
from test_native_rework import call, check, feedback, ok, ref, source


@pytest.fixture
def producer_scene(client, tmp_path):
    pid = ok(client.post('/api/projects', json={'name': '真实 harness producer 归位'}))['id']
    root = tmp_path.parent / (tmp_path.name + '-actual-output'); root.mkdir()
    workspace = ok(client.post(f'/api/projects/{pid}/local-workspaces', json={
        'idempotency_key': 'actual-folder', 'root': str(root), 'allowed_write_paths': ['delivery/']}))
    material = ok(client.post(f'/api/projects/{pid}/documents', files={
        'file': ('用户要求.md', 'A 与 B 独立；人工内容与原版本必须保留。'.encode())}))
    candidate = {'title': '两项真实已确认任务', 'summary': '用户要求独立修改 A，保留 B。', 'nodes': [
        {'id': 'a', 'title': '真实任务 A', 'description': 'A 原已确认要求',
            'outputs': ['A 修订材料'], 'acceptance': ['A 来源与改动可核对']},
        {'id': 'b', 'title': '真实任务 B', 'description': 'B 独立已确认要求',
            'outputs': ['B 已确认材料'], 'acceptance': ['保留 B 原字节与结论']}]}
    proposal = ok(client.post(f'/api/projects/{pid}/planning/proposals', json={
        'operation_id': 'actual-user-plan', 'prompt': '拆分明确的独立任务', 'mode': 'manual',
        'materials': [{'document_id': material['id'], 'revision': material['revision'], 'sha256': material['sha256']}],
        'candidate': candidate}))
    proposal = ok(client.post(f'/api/projects/{pid}/planning/proposals/{proposal["id"]}/confirm', json={
        'operation_id': 'actual-user-confirm', 'expected_revision': proposal['revision'],
        'proposal_sha256': proposal['sha256'], 'user_confirmed': True}))
    scene = {'client': client, 'pid': pid, 'base': f'/api/projects/{pid}', 'root': root,
        'wid': workspace['id'], 'workspace_ref': {k: workspace[k] for k in ('id', 'revision', 'binding_sha256')},
        'proposal': proposal, 'data_dir': tmp_path, 'api_receipts': []}
    yield scene
    evidence = Path(os.environ.get('EEAS_R3_PRODUCER_EVIDENCE', tmp_path / 'producer-evidence')) / tmp_path.name
    evidence.mkdir(parents=True, exist_ok=True)
    export_folder = client.app.state.local_workspaces._data(pid, workspace['id'], 'planning-exports')
    records = []
    for path in sorted(export_folder.glob('*/*.json')):
        raw = path.read_bytes()
        records.append({'path': str(path.relative_to(tmp_path)), 'sha256': hashlib.sha256(raw).hexdigest(),
            'value': json.loads(raw)})
    files = [{'path': str(path.relative_to(root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'byte_length': path.stat().st_size} for path in sorted(root.rglob('*')) if path.is_file()]
    (evidence / 'receipt.json').write_text(json.dumps({'schema': 'eeas.r3.producer.actual-api-evidence.v1',
        'api_receipts': scene['api_receipts'], 'actual_export_records': records, 'actual_final_files': files,
        'model_calls': 0, 'producer_services': 'REAL_PLANNING_EXPORTS_AND_LOCAL_WORKSPACES',
        'inputs': 'EXPLICIT_ORIGINAL_FIXTURE_USER_STATEMENTS', 'windows': 'NOT_RUN',
        'semantic_correctness': 'NOT_QUALIFIED'}, ensure_ascii=False, indent=2))


def export(s, op='actual-export'):
    return call(s, 'POST', '/planning/proposals/' + s['proposal']['id'] + '/harness/export', {
        'operation_id': op, 'expected_revision': s['proposal']['revision'],
        'proposal_sha256': s['proposal']['sha256'], 'workspace_ref': s['workspace_ref'], 'directory': 'delivery'})


def task_path(s, result, task='a'):
    harness = call(s, 'GET', '/planning/proposals/' + s['proposal']['id'] + '/harness')
    prompt = next(p for p in harness['prompts'] if p['node_id'] == task)
    path = result['version_directory'] + '/' + prompt['filename']
    recorded = next(f for f in result['files'] if f['path'] == path)
    check(recorded['sha256'] == prompt['sha256'] == source(s, path)['sha256'])
    return path


def automatic(s, f, op='automatic-map', locator=None):
    return call(s, 'POST', '/native-rework/mappings', {'operation_id': op,
        'native_feedback_ref': f['feedback_ref'], 'proposal_ref': ref(s['proposal']),
        'locator': locator or {'kind': 'FILE'}, 'user_confirmed': False, 'task_ids': []})


def selection(text, quote):
    start = text.index(quote)
    return {'kind': 'TEXT_RANGE', 'char_start': start, 'char_end': start + len(quote),
        'quote_sha256': hashlib.sha256(quote.encode()).hexdigest()}


def test_exact_task_export_auto_maps_replays_and_repairs_preserving_human_and_independent_task(producer_scene):
    s = producer_scene; result = export(s); path = task_path(s, result)
    original = (s['root'] / path).read_text()
    b_path = task_path(s, result, 'b'); b_original = (s['root'] / b_path).read_bytes()
    check(export(s)['replayed'], 'Export same operation must reuse its actual completion record')
    f = feedback(s, path=path, selection=selection(original, 'A 原已确认要求'))
    m = automatic(s, f, locator=f['payload']['file_selection'])
    check(m['status'] == 'MAPPED' and m['payload']['task_ids'] == ['a'])
    check(m['payload']['mapping_basis'] == 'EXACT_CONFIRMED_TASK_EXPORT_PRODUCER')
    producer = m['payload']['producer_check']; check(producer['path_record_count'] == 1)
    evidence = producer['candidates'][0]
    check(evidence['proposal_ref'] == ref(s['proposal']) and evidence['workspace_ref'] == s['workspace_ref'])
    check(evidence['file']['path'] == path and evidence['prompt_sha256'] == f['payload']['baseline_file_source_ref']['sha256'])
    check(automatic(s, f, locator=f['payload']['file_selection']) == m)
    # A second actual producer must not change existing verified mapping reuse.
    second_export = export(s, 'actual-second-export')
    check(all(item['outcome'] == 'EXISTING_IDENTICAL' for item in second_export['files']))
    f2 = feedback(s, 'feedback-reuse', path, selection(original, 'A 原已确认要求'))
    reused = automatic(s, f2, 'reuse-existing', f2['payload']['file_selection'])
    check(reused['payload']['mapping_basis'] == 'REUSED_CONFIRMED_OBJECT_MAPPING')
    human = original + '\n人工交错补充：保留独立验收结论😀。\n'
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit((s['root'] / path).write_text, human).result()
    binding = m['payload']['binding_ref']
    plan = call(s, 'POST', '/native-rework/plans', {'operation_id': 'actual-local-preview',
        'mapping_ref': m['mapping_ref'], 'classification_kind': 'CONSTRAINT', 'classification_origin': 'USER_STATEMENT',
        'rationale': '用户要求只改已核对的 A 文本，语义仍需验收', 'proposed_value': 'A 本次真实修订要求',
        'contracts': [{'task_id': 'a', 'inputs': [{'binding_ref': binding,
            'input_value_sha256': m['payload']['selected_value_sha256'], 'condition': 'VALUE_CHANGED',
            'verification_note': '实际 producer 核对的是 A 来源对象，不推断 B 的有效条件',
            'verification_refs': [binding]}]}]})
    impact = plan['payload']['engine_plan']['payload']['impact']
    check(impact['counts'] == {'AFFECTED': 1, 'UNKNOWN': 1, 'RETAINED_BY_DECLARED_CONDITION': 0})
    applied = call(s, 'POST', '/native-rework/plans/' + plan['plan_ref']['id'] + '/apply', {
        'operation_id': 'actual-local-apply', 'plan_ref': plan['plan_ref'],
        'expected_current_sources': [source(s, path)], 'allow_rebase': True}, codes=(200,))
    check(applied['status'] == 'APPLIED')
    check((s['root'] / path).read_text() == human.replace('A 原已确认要求', 'A 本次真实修订要求'))
    check((s['root'] / b_path).read_bytes() == b_original)
    check(call(s, 'GET', '/planning/proposals/' + s['proposal']['id'])['sha256'] == s['proposal']['sha256'])
    old = ok(s['client'].get(s['base'] + f'/local-workspaces/{s["wid"]}/file', params={
        'path': path, 'revision': f['payload']['baseline_file_source_ref']['revision']}))
    check(old['content'] == original)
    # Recreate actual services against the same store, and only read the old op.
    settings = Settings(s['data_dir'], port=8765)
    with TestClient(create_app(settings), base_url=settings.origin) as restarted:
        boot = restarted.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
        restarted.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': boot.json()['csrf_token']})
        loaded = ok(restarted.get(s['base'] + '/native-rework/operations/automatic-map'))
        s['api_receipts'].append({'method': 'GET_AFTER_SERVICE_RESTART',
            'path': '/native-rework/operations/automatic-map', 'input': None, 'response': copy.deepcopy(loaded)})
        check(loaded == m)


def test_same_name_copy_and_real_non_task_export_never_guess_node(producer_scene):
    s = producer_scene; result = export(s); path = task_path(s, result)
    (s['root'] / 'task-a.md').write_bytes((s['root'] / path).read_bytes())
    unknown = automatic(s, feedback(s, 'copy-feedback', 'task-a.md'), 'copy-mapping')
    check(unknown['status'] == 'NEEDS_CLARIFICATION')
    check(unknown['payload']['producer_check']['status'] == 'NO_TASK_PRODUCER')
    index = result['version_directory'] + '/INDEX.md'
    pending = automatic(s, feedback(s, 'index-feedback', index), 'index-mapping')
    check(pending['status'] == 'NEEDS_CLARIFICATION')
    check(pending['payload']['producer_check']['status'] == 'NO_TASK_PRODUCER')
    check(not pending['payload']['write_authorized'])


def test_actual_content_and_current_proposal_version_drift_remain_clarification(producer_scene):
    s = producer_scene; result = export(s); path = task_path(s, result)
    old_source = source(s, path); original = (s['root'] / path).read_text()
    human = original.replace('A 原已确认要求', '人工更改 A，归位仍待核对')
    (s['root'] / path).write_text(human)
    pending = automatic(s, feedback(s, 'drift-feedback', path), 'content-drift-mapping')
    check(pending['status'] == 'NEEDS_CLARIFICATION')
    check(pending['payload']['producer_check']['status'] == 'PRODUCER_CONTENT_DRIFT')
    check((s['root'] / path).read_text() == human)
    previous = ok(s['client'].get(s['base'] + f'/local-workspaces/{s["wid"]}/file',
        params={'path': path, 'revision': old_source['revision']}))
    check(previous['content'] == original)
    candidate = copy.deepcopy(s['proposal']['payload']['candidate']); candidate['nodes'][0]['acceptance'] = ['人工新增验收']
    edited = call(s, 'PUT', '/planning/proposals/' + s['proposal']['id'], {'operation_id': 'actual-plan-edit',
        'expected_revision': s['proposal']['revision'], 'candidate': candidate, 'change_reason': '用户改变真实验收'})
    s['proposal'] = call(s, 'POST', '/planning/proposals/' + edited['id'] + '/confirm', {
        'operation_id': 'actual-new-confirm', 'expected_revision': edited['revision'],
        'proposal_sha256': edited['sha256'], 'user_confirmed': True})
    (s['root'] / path).write_text(original)
    stale = automatic(s, feedback(s, 'version-feedback', path), 'version-drift-mapping')
    check(stale['status'] == 'NEEDS_CLARIFICATION')
    check(stale['payload']['producer_check']['status'] == 'PRODUCER_VERSION_OR_SOURCE_DRIFT')
    check((s['root'] / path).read_text() == original)


def test_two_real_completed_producers_are_ambiguous_and_same_mapping_operation_is_immutable(producer_scene):
    s = producer_scene; result = export(s); path = task_path(s, result)
    check(export(s)['replayed'])
    second = export(s, 'distinct-actual-export')
    check(second['version_directory'] == result['version_directory'])
    f = feedback(s, path=path); pending = automatic(s, f)
    check(pending['status'] == 'NEEDS_CLARIFICATION')
    producer = pending['payload']['producer_check']
    check(producer['status'] == 'MULTIPLE_PRODUCERS' and producer['path_record_count'] == 2)
    check(len({p['operation_digest'] for p in producer['candidates']}) == 2)
    check(automatic(s, f) == pending)
    check(call(s, 'GET', '/native-rework/operations/automatic-map') == pending)
    check(pending['mapping_ref']['sha256'] == digest(pending['payload']))
