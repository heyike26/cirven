"""Real APIs, immutable source snapshots and actual interleaved human files.

No model is invoked: task statements, mapping decisions and proposed changes
are explicit fixture user inputs. This does not qualify semantic or GUI accuracy.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.native_rework import register_native_rework_routes
from fastapi.testclient import TestClient
from packages.domain.local_workspaces import digest
from packages.domain.native_rework import NativeRework
from packages.domain.feedback_rework import BindingInput
from packages.domain.store import Conflict
from conftest import LiveServer


def check(value, message='R3 acceptance check failed'):
    if not value:
        raise AssertionError(message)


def ok(response, codes=(200, 201)):
    check(response.status_code in codes, f'{response.status_code}: {response.text}')
    return response.json()


def ref(value):
    return {k: value[k] for k in ('id', 'revision', 'sha256')}


@pytest.fixture
def scene(client, tmp_path):
    if not hasattr(client.app.state, 'native_rework'):
        register_native_rework_routes(client.app, client.app.state.store)
    pid = ok(client.post('/api/projects', json={'name': 'R3 两个独立真实任务'}))['id']
    root = tmp_path.parent / (tmp_path.name + '-actual-project')
    root.mkdir()
    text = '{\n  "tasks": {"a": {"requirement": "A 原已确认要求"}, "b": {"requirement": "B 独立已确认要求"}},\n  "human_note": "原人工备注"\n}\n'
    (root / '任务与harness.json').write_text(text)
    (root / '独立任务B.md').write_text('# B 已确认\n保持人工结论。\n')
    (root / '原页.md').write_text('# 原页\n独特 A 选文\n不动 B。\n')
    (root / 'binary.png').write_bytes(b'\x89PNG\r\n\x1a\nactual-binary-not-text')
    workspace = ok(client.post(f'/api/projects/{pid}/local-workspaces', json={
        'idempotency_key': 'actual-project', 'root': str(root),
        'allowed_write_paths': ['任务与harness.json', '原页.md']}))
    material = ok(client.post(f'/api/projects/{pid}/documents', files={
        'file': ('用户需求.md', 'A 与 B 为独立任务；所有改动应可核对。'.encode())}))
    proposal = ok(client.post(f'/api/projects/{pid}/planning/proposals', json={
        'operation_id': 'user-plan', 'prompt': '明确两个任务', 'mode': 'manual',
        'materials': [{'document_id': material['id'], 'revision': material['revision'], 'sha256': material['sha256']}],
        'candidate': {'title': '已确认独立任务', 'summary': '用户提交的两项独立任务', 'nodes': [
            {'id': 'a', 'title': '任务 A', 'description': '修订 A 的任务与 harness 要求',
                'outputs': ['A 要求记录'], 'acceptance': ['核对 A 改动和来源']},
            {'id': 'b', 'title': '任务 B', 'description': '保留独立 B 人工结论',
                'outputs': ['B 要求记录'], 'acceptance': ['B 原字节及确认保留']}]}}))
    draft = copy.deepcopy(proposal)
    proposal = ok(client.post(f'/api/projects/{pid}/planning/proposals/{proposal["id"]}/confirm', json={
        'operation_id': 'user-confirm', 'expected_revision': proposal['revision'],
        'proposal_sha256': proposal['sha256'], 'user_confirmed': True}))
    value = {'client': client, 'pid': pid, 'base': f'/api/projects/{pid}', 'root': root,
        'wid': workspace['id'], 'proposal': proposal, 'draft': draft, 'original': text,
        'store': client.app.state.store, 'data_dir': tmp_path,
        'independent': (root / '独立任务B.md').read_bytes(), 'api_receipts': []}
    yield value
    folder = Path(os.environ.get('EEAS_R3_EVIDENCE', tmp_path / 'r3-evidence')) / tmp_path.name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'receipt.json').write_text(json.dumps({'schema': 'eeas.r3.actual-api-evidence.v1',
        'api_receipts': value['api_receipts'], 'source_initial': value['original'],
        'source_final': (root / '任务与harness.json').read_text(),
        'independent_original_sha256': hashlib.sha256(value['independent']).hexdigest(),
        'independent_final_sha256': hashlib.sha256((root / '独立任务B.md').read_bytes()).hexdigest(),
        'model_calls': 0, 'user_inputs': 'EXPLICIT_ORIGINAL_FIXTURE_STATEMENTS_NOT_MOCK_SERVICES',
        'native_gui': 'NOT_RUN', 'semantic_correctness': 'NOT_QUALIFIED',
        'cross_process_editor_compare_replace_race': 'NOT_QUALIFIED'}, ensure_ascii=False, indent=2))


def call(s, method, path, body=None, codes=(200, 201)):
    result = ok(s['client'].request(method, s['base'] + path, json=body), codes)
    s['api_receipts'].append({'method': method, 'path': path,
        'input': copy.deepcopy(body), 'response': copy.deepcopy(result)})
    return result


def source(s, path='任务与harness.json'):
    return ok(s['client'].get(s['base'] + f'/local-workspaces/{s["wid"]}/file', params={'path': path}))['source_ref']


def feedback(s, op='native-feedback', path='任务与harness.json', selection=None):
    return call(s, 'POST', '/native-feedback', {'operation_id': op, 'file_source_ref': source(s, path),
        'mode': 'SUBMITTED', 'comment': '请更新 A，保留人工与独立 B。',
        'selection': selection, 'context_ref': {'window_title': '标题绝不是来源', 'id': 'caller-context'}})


def mapping(s, f, op='map', confirm=True, locator=None, **extra):
    return call(s, 'POST', '/native-rework/mappings', {'operation_id': op,
        'native_feedback_ref': f['feedback_ref'], 'proposal_ref': ref(s['proposal']),
        'locator': locator or {'kind': 'JSON_POINTER', 'pointer': '/tasks/a/requirement'},
        'task_ids': ['a'] if confirm else [], 'user_confirmed': confirm, **extra})


def prepare(s, m, op='prepare', value='A 本次提议要求', kind='CONSTRAINT', independent=True):
    selected_binding = m['payload']['binding_ref']
    contracts = [{'task_id': 'a', 'inputs': [{'binding_ref': selected_binding,
        'input_value_sha256': m['payload']['selected_value_sha256'], 'condition': 'VALUE_CHANGED',
        'invalidating_kinds': ['EXPRESSION', 'DATA', 'CONSTRAINT', 'UNKNOWN'],
        'verification_note': '用户在原创任务文件中明确映射 A 对象；语义仍待复验',
        'verification_refs': [selected_binding]}]}]
    if independent:
        b = call(s, 'POST', '/feedback-rework/bindings', {'operation_id': 'b-bind-' + op,
            'file_source_ref': source(s), 'locator': {'kind': 'JSON_POINTER', 'pointer': '/tasks/b/requirement'},
            'proposal_ref': ref(s['proposal']), 'task_ids': ['b']})
        contracts.append({'task_id': 'b', 'inputs': [{'binding_ref': b['binding_ref'],
            'input_value_sha256': b['payload']['selected_value_sha256'], 'condition': 'VALUE_CHANGED',
            'verification_note': '核对实际 B 独立对象不依赖 A，属于用户声明的有效条件',
            'verification_refs': [b['binding_ref']]}]})
    return call(s, 'POST', '/native-rework/plans', {'operation_id': op, 'mapping_ref': m['mapping_ref'],
        'classification_kind': kind, 'classification_origin': 'USER_STATEMENT',
        'rationale': '用户反馈指定本次对象改动；不是自动语义确认', 'proposed_value': value,
        'contracts': contracts})


def apply_body(s, p, op='apply', rebase=True):
    return {'operation_id': op, 'plan_ref': p['plan_ref'],
        'expected_current_sources': [source(s, p['payload']['write_scope'][0]['path'])], 'allow_rebase': rebase}


def apply(s, p, body=None, codes=(200,)):
    return call(s, 'POST', '/native-rework/plans/' + p['plan_ref']['id'] + '/apply',
        body or apply_body(s, p), codes)


def test_feedback_historical_object_rework_retains_two_branches_human_and_confirmation(scene):
    s = scene
    f = feedback(s)
    # Actual external editor saves after feedback, before a historical binding.
    human = s['original'].replace('原人工备注', '人工并发备注😀：必须保留')
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit((s['root'] / '任务与harness.json').write_text, human).result()
    m = mapping(s, f)
    check(m['status'] == 'MAPPED' and m['payload']['baseline_file_source_ref'] == f['payload']['baseline_file_source_ref'])
    binding = call(s, 'GET', '/feedback-rework/bindings/' + m['payload']['binding_ref']['id'])
    check(binding['payload']['source_status_at_binding'] == 'STALE')
    p = prepare(s, m)
    impact = p['payload']['engine_plan']['payload']['impact']
    check(impact['counts'] == {'AFFECTED': 1, 'UNKNOWN': 0, 'RETAINED_BY_DECLARED_CONDITION': 1})
    check(impact['baseline_all_tasks'] == 2 and impact['known_dependency_baseline_tasks'] == 2)
    body = apply_body(s, p)
    result = apply(s, p, body)
    check(result['status'] == 'APPLIED')
    check((s['root'] / '任务与harness.json').read_text() == human.replace('A 原已确认要求', 'A 本次提议要求'))
    check((s['root'] / '独立任务B.md').read_bytes() == s['independent'])
    check(call(s, 'GET', '/planning/proposals/' + s['proposal']['id'])['sha256'] == s['proposal']['sha256'])
    historical = ok(s['client'].get(s['base'] + f'/local-workspaces/{s["wid"]}/file',
        params={'path': '任务与harness.json', 'revision': f['payload']['baseline_file_source_ref']['revision']}))
    check(historical['content'] == s['original'])
    check(apply(s, p, body) == result, 'Exact replay must not rewrite the source')
    check(result['payload']['engine_receipt']['payload']['modified_file_count'] == 1)
    check(not result['payload']['semantic_correctness_confirmed'])
    check(call(s, 'GET', '/native-rework/operations/apply') == result)


def test_new_feedback_reuses_confirmed_mapping_after_unrelated_human_change_and_qualified_write(scene):
    s = scene
    first = mapping(s, feedback(s))
    human = s['original'].replace('原人工备注', '人工追加，任务映射不变')
    (s['root'] / '任务与harness.json').write_text(human)
    second = mapping(s, feedback(s, 'feedback-2'), op='map-2', confirm=False)
    check(second['status'] == 'MAPPED' and second['payload']['mapping_basis'] == 'REUSED_CONFIRMED_OBJECT_MAPPING')
    check(second['payload']['reused_mapping_ref'] == first['mapping_ref'])
    p = prepare(s, second)
    apply(s, p)
    third = mapping(s, feedback(s, 'feedback-3'), op='map-3', confirm=False)
    check(third['status'] == 'MAPPED', 'Actual applied lineage must continue without another mapping decision')
    check(not third['payload']['repeated_mapping_confirmation_required'])


def test_unknown_and_multiple_task_mappings_remain_durable_clarification(scene):
    s = scene
    f = feedback(s)
    pending = mapping(s, f, confirm=False)
    check(pending['status'] == 'NEEDS_CLARIFICATION')
    first = mapping(s, f, op='a-mapping')
    second = mapping(s, f, op='b-mapping', task_ids=['b'])
    pending2 = mapping(s, f, op='ambiguous', confirm=False)
    check(pending2['status'] == 'NEEDS_CLARIFICATION' and pending2['payload']['candidate_task_ids'] == ['a', 'b'])
    continued = mapping(s, f, op='explicit-context', confirm=False, mapping_context_ref=first['mapping_ref'])
    check(continued['status'] == 'MAPPED' and continued['payload']['task_ids'] == ['a'])
    check(first['mapping_ref'] != second['mapping_ref'])
    service = NativeRework(s['store'], s['client'].app.state.local_workspaces,
        s['client'].app.state.native_feedback, s['client'].app.state.feedback_rework,
        s['client'].app.state.planning_proposals, s['client'].app.state.discovery)
    check(service.get(s['pid'], pending['mapping_ref']['id']) == pending)
    check((s['root'] / '任务与harness.json').read_text() == s['original'])


def test_manual_target_change_does_not_guess_new_mapping_or_overwrite(scene):
    s = scene
    f = feedback(s)
    m = mapping(s, f)
    p = prepare(s, m)
    human = s['original'].replace('A 原已确认要求', '人工重定 A 约束')
    (s['root'] / '任务与harness.json').write_text(human)
    unmapped = mapping(s, feedback(s, 'human-new-feedback'), op='human-new-map', confirm=False)
    check(unmapped['status'] == 'NEEDS_CLARIFICATION')
    result = apply(s, p, codes=(409,))
    check(result['status'] == 'CONFLICT' and 'TARGET_CHANGED' in result['payload']['engine_receipt']['payload']['outcomes'][0]['reason'])
    check((s['root'] / '任务与harness.json').read_text() == human)


def test_human_save_after_current_inspection_is_refused_by_real_cas(scene):
    s = scene
    m = mapping(s, feedback(s))
    p = prepare(s, m)
    body = apply_body(s, p)
    human = s['original'].replace('原人工备注', '读取 current 后的新人工保存')
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit((s['root'] / '任务与harness.json').write_text, human).result()
    result = apply(s, p, body, codes=(409,))
    check('SOURCE_DRIFT' in result['payload']['engine_receipt']['payload']['outcomes'][0]['reason'])
    check((s['root'] / '任务与harness.json').read_text() == human)
    retried = apply(s, p, apply_body(s, p, op='explicit-refreshed-apply'))
    check(retried['status'] == 'APPLIED' and '读取 current 后的新人工保存' in (s['root'] / '任务与harness.json').read_text())


def test_missing_contract_unknown_classification_and_scope_injection_never_become_confirmation(scene):
    s = scene
    m = mapping(s, feedback(s))
    p = prepare(s, m, kind='UNKNOWN', independent=False)
    impact = p['payload']['engine_plan']['payload']['impact']
    check(impact['counts']['UNKNOWN'] == 2 and impact['selected_rework_tasks'] == 0)
    check(p['payload']['classification_status'] == 'PROPOSAL_NOT_SEMANTICALLY_VERIFIED')
    body = apply_body(s, p)
    body['write_scope'] = [{'workspace_id': s['wid'], 'path': '独立任务B.md'}]
    ok(s['client'].post(s['base'] + '/native-rework/plans/' + p['plan_ref']['id'] + '/apply', json=body), (422,))
    check((s['root'] / '任务与harness.json').read_text() == s['original'])


@pytest.mark.parametrize('fault', ['wrong-feedback-sha', 'wrong-proposal-sha', 'draft', 'missing-node', 'fake-historical-source', 'binary'])
def test_exact_feedback_confirmed_task_and_actual_snapshot_admission(scene, fault):
    s = scene
    f = feedback(s, path='binary.png' if fault == 'binary' else '任务与harness.json')
    body = {'operation_id': 'fault-map', 'native_feedback_ref': f['feedback_ref'],
        'proposal_ref': ref(s['proposal']), 'locator': {'kind': 'FILE'}, 'task_ids': ['a'], 'user_confirmed': True}
    expected = 409
    if fault == 'wrong-feedback-sha': body['native_feedback_ref']['sha256'] = '0' * 64
    elif fault == 'wrong-proposal-sha': body['proposal_ref']['sha256'] = '0' * 64
    elif fault == 'draft': body['proposal_ref'] = ref(s['draft'])
    elif fault == 'missing-node': body['task_ids'] = ['absent']; expected = 422
    elif fault == 'fake-historical-source':
        bad = copy.deepcopy(f['payload']['baseline_file_source_ref']); bad['sha256'] = '0' * 64
        ok(s['client'].post(s['base'] + '/feedback-rework/bindings', json={
            'operation_id': 'fake-bind', 'file_source_ref': bad, 'locator': {'kind': 'FILE'}}), (409,))
        with pytest.raises(Conflict):
            s['client'].app.state.feedback_rework.bind_historical(s['pid'], BindingInput(
                operation_id='fake-historical-bind', file_source_ref=bad,
                locator={'kind': 'FILE'}, proposal_ref=ref(s['proposal']), task_ids=['a']))
        check((s['root'] / '任务与harness.json').read_text() == s['original'])
        return
    elif fault == 'binary':
        result = call(s, 'POST', '/native-rework/mappings', body)
        check(result['status'] == 'NOT_ADAPTED' and not result['payload']['write_authorized'])
        return
    call(s, 'POST', '/native-rework/mappings', body, (expected,))
    check((s['root'] / '任务与harness.json').read_text() == s['original'])


def test_text_selection_uses_exact_source_and_preserves_prefix_human_edits(scene):
    s = scene
    text = (s['root'] / '原页.md').read_text(); quote = '独特 A 选文'; start = text.index(quote)
    f = feedback(s, path='原页.md', selection={'kind': 'TEXT_RANGE', 'char_start': start,
        'char_end': start + len(quote), 'quote_sha256': hashlib.sha256(quote.encode()).hexdigest()})
    m = call(s, 'POST', '/native-rework/mappings', {'operation_id': 'text-map',
        'native_feedback_ref': f['feedback_ref'], 'proposal_ref': ref(s['proposal']), 'task_ids': ['a'], 'user_confirmed': True})
    p = prepare(s, m, value='新的 A 选文', independent=False)
    human = '人写前言😀\n' + text
    (s['root'] / '原页.md').write_text(human)
    result = apply(s, p)
    check(result['status'] == 'APPLIED' and (s['root'] / '原页.md').read_text() == human.replace(quote, '新的 A 选文'))
    updated = (s['root'] / '原页.md').read_text(); new_quote = '新的 A 选文'; new_start = updated.index(new_quote)
    f2 = feedback(s, 'next-text-feedback', path='原页.md', selection={'kind': 'TEXT_RANGE', 'char_start': new_start,
        'char_end': new_start + len(new_quote), 'quote_sha256': hashlib.sha256(new_quote.encode()).hexdigest()})
    m2 = call(s, 'POST', '/native-rework/mappings', {'operation_id': 'next-text-map',
        'native_feedback_ref': f2['feedback_ref'], 'proposal_ref': ref(s['proposal'])})
    check(m2['status'] == 'MAPPED' and m2['payload']['mapping_basis'] == 'REUSED_CONFIRMED_OBJECT_MAPPING')


def test_exact_references_survive_full_app_restart_and_no_record_is_not_permission_to_replay(scene):
    s = scene
    m = mapping(s, feedback(s)); p = prepare(s, m); result = apply(s, p)
    config = Settings(s['data_dir'], port=8765)
    with TestClient(create_app(config), base_url=config.origin) as restored:
        bootstrap = restored.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
        restored.headers.update({'Origin': config.origin, 'X-EEAS-CSRF': bootstrap.json()['csrf_token']})
        check(ok(restored.get(s['base'] + '/native-rework/operations/apply')) == result)
        check(ok(restored.get(s['base'] + '/native-rework/mappings/' + m['mapping_ref']['id'])) == m)
        check(ok(restored.get(s['base'] + '/native-rework/plans/' + p['plan_ref']['id'])) == p)
        ok(restored.get(s['base'] + '/native-rework/operations/never-recorded'), (404,))
    bad = {'operation_id': 'map', 'native_feedback_ref': feedback(s, 'new-op-feedback')['feedback_ref'],
        'proposal_ref': ref(s['proposal']), 'locator': {'kind': 'FILE'}, 'task_ids': ['b'], 'user_confirmed': True}
    call(s, 'POST', '/native-rework/mappings', bad, (409,))


def test_interrupted_apply_journal_is_inspected_without_a_new_write(scene):
    s = scene
    m = mapping(s, feedback(s)); p = prepare(s, m); body = apply_body(s, p)
    service = s['client'].app.state.native_rework
    # A real persisted pre-dispatch journal models interruption at its actual
    # durable boundary, without mocking any service or inventing a write result.
    request = {'project_id': s['pid'], 'operation_id': body['operation_id'], 'kind': 'APPLY', 'input': body}
    folder = service._path(s['pid'], 'operations', digest(body['operation_id']))
    service._put(folder / 'request.json', {**request, 'request_sha256': digest(request), 'created_at': 'fixture-interruption-boundary'})
    observed = call(s, 'GET', '/native-rework/operations/apply')
    check(observed['status'] == 'INCOMPLETE_REVIEW_REQUIRED' and not observed['automatic_retry'])
    result = apply(s, p, body, codes=(409,))
    check(result['status'] == 'INCOMPLETE_REVIEW_REQUIRED' and not result['automatic_retry'])
    check((s['root'] / '任务与harness.json').read_text() == s['original'])


def test_unique_confirmed_local_source_anchor_auto_maps_but_two_candidates_need_clarification(scene):
    s = scene
    materialized = call(s, 'POST', f'/local-workspaces/{s["wid"]}/materials', {
        'operation_id': 'actual-anchor-material', 'paths': ['原页.md'],
        'expected_sources': [source(s, '原页.md')]})
    document = materialized['materials'][0]['document_ref']
    text = (s['root'] / '原页.md').read_text()
    candidate = copy.deepcopy(s['proposal']['payload']['candidate'])
    anchor = {'document_id': document['document_id'], 'revision': document['revision'],
        'raw_sha256': document['sha256'], 'chunk_index': 0,
        'chunk_sha256': hashlib.sha256(text.encode()).hexdigest(), 'char_start': 0,
        'char_end': len(text), 'quote_sha256': hashlib.sha256(text.encode()).hexdigest()}
    for both in (False, True):
        candidate['nodes'][0]['source_anchors'] = [anchor]
        candidate['nodes'][1]['source_anchors'] = [anchor] if both else []
        proposal = call(s, 'POST', '/planning/proposals', {'operation_id': 'anchor-plan-' + str(both),
            'prompt': '按真实本地来源核对任务', 'mode': 'manual', 'materials': [document], 'candidate': candidate})
        s['proposal'] = call(s, 'POST', '/planning/proposals/' + proposal['id'] + '/confirm', {
            'operation_id': 'anchor-confirm-' + str(both), 'expected_revision': proposal['revision'],
            'proposal_sha256': proposal['sha256'], 'user_confirmed': True})
        result = mapping(s, feedback(s, 'anchored-feedback-' + str(both), path='原页.md'), op='anchor-map-' + str(both),
            confirm=False, locator={'kind': 'FILE'})
        if both:
            check(result['status'] == 'NEEDS_CLARIFICATION' and result['payload']['candidate_task_ids'] == ['a', 'b'])
        else:
            check(result['status'] == 'MAPPED' and result['payload']['mapping_basis'] == 'EXACT_CONFIRMED_TASK_SOURCE_ANCHOR')
            check(result['payload']['task_ids'] == ['a'] and not result['payload']['write_authorized'])


def test_task_version_change_invalidates_mapping_authority_before_any_write(scene):
    s = scene
    m = mapping(s, feedback(s)); p = prepare(s, m)
    edited = copy.deepcopy(s['proposal']['payload']['candidate'])
    edited['nodes'][0]['acceptance'] = ['新的人工验收条件需要重新确认']
    call(s, 'PUT', '/planning/proposals/' + s['proposal']['id'], {
        'operation_id': 'human-plan-edit', 'expected_revision': s['proposal']['revision'],
        'candidate': edited, 'change_reason': '人工明确改变验收条件'})
    call(s, 'POST', '/native-rework/plans/' + p['plan_ref']['id'] + '/apply', apply_body(s, p), (409,))
    check((s['root'] / '任务与harness.json').read_text() == s['original'])
    check(call(s, 'GET', '/native-rework/operations/apply')['engine_operation'] is None)


def test_real_http_process_restart_reads_same_feedback_plan_receipt_and_source(scene):
    s = scene
    m = mapping(s, feedback(s)); p = prepare(s, m); applied = apply(s, p)
    final_bytes = (s['root'] / '任务与harness.json').read_bytes()
    server = LiveServer(s['data_dir'])
    observations = []
    log_root = Path(os.environ.get('EEAS_R3_EVIDENCE', s['data_dir'])) / 'r3-http-startup'
    log_root.mkdir(parents=True, exist_ok=True)
    streams = []
    def start(restart):
        stdout = (log_root / f'{restart}-stdout.txt').open('w')
        stderr = (log_root / f'{restart}-stderr.txt').open('w')
        streams.extend([stdout, stderr])
        command = [sys.executable, *(['-O'] if not __debug__ else []), '-m', 'apps.api', 'serve']
        server.process = subprocess.Popen(command,
            cwd=Path(__file__).resolve().parents[1], env=server.env, stdout=stdout, stderr=stderr)
        started = time.monotonic()
        with httpx.Client(trust_env=False) as health:
            while time.monotonic() - started < 30:
                check(server.process.poll() is None, 'Owned API exited; inspect preserved startup logs')
                try:
                    if health.get(server.origin + '/health', timeout=.2).status_code == 200:
                        (log_root / f'{restart}-ready.json').write_text(json.dumps({
                            'actual_owned_pid': server.process.pid, 'command': command,
                            'startup_seconds': time.monotonic() - started}))
                        return time.monotonic() - started
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
        raise AssertionError('Owned API health readiness exceeded bounded 30 seconds')
    try:
        for restart in range(2):
            ready_seconds = start(restart)
            with httpx.Client(base_url=server.origin, trust_env=False) as wire:
                bootstrap = wire.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
                wire.headers.update({'Origin': server.origin, 'X-EEAS-CSRF': bootstrap.json()['csrf_token']})
                value = ok(wire.get(s['base'] + '/native-rework/operations/apply'))
                check(value == applied)
                recovered = ok(wire.get(s['base'] + '/native-rework/mappings/' + m['mapping_ref']['id']))
                check(recovered == m)
                observations.append({'restart': restart, 'actual_owned_api_pid': server.process.pid,
                    'origin': server.origin, 'startup_seconds': ready_seconds,
                    'receipt_ref': value['receipt_ref'], 'mapping_ref': recovered['mapping_ref']})
            server.stop()
    finally:
        server.stop()
        for stream in streams:
            stream.close()
    check(observations[0]['actual_owned_api_pid'] != observations[1]['actual_owned_api_pid'])
    check((s['root'] / '任务与harness.json').read_bytes() == final_bytes)
    s['api_receipts'].append({'actual_http_process_restarts': observations})


def test_selected_file_context_is_project_scoped_and_route_registration_is_idempotent(scene):
    s = scene
    f = feedback(s); m = mapping(s, f)
    other = feedback(s, 'other-file-feedback', path='独立任务B.md')
    routes = len(s['client'].app.routes)
    owner = s['client'].app.state.native_rework
    check(register_native_rework_routes(s['client'].app, s['store']) is owner)
    check(len(s['client'].app.routes) == routes)
    context = call(s, 'POST', '/native-rework/context', {'file_source_ref': source(s)})
    check(context['status'] == 'READ_ONLY_CONTEXT' and not context['file_write_authorized'])
    check([v['feedback_ref'] for v in context['feedback']] == [f['feedback_ref']])
    check(other['feedback_ref'] not in [v['feedback_ref'] for v in context['feedback']])
    check([v['mapping_ref'] for v in context['mappings']] == [m['mapping_ref']])
    check(context['confirmed_proposals'][0]['proposal_ref'] == ref(s['proposal']))
    foreign = copy.deepcopy(source(s)); foreign['project_id'] = 'another-project'
    call(s, 'POST', '/native-rework/context', {'file_source_ref': foreign}, (422,))


def test_confirmed_requirement_basis_is_exact_and_human_redefinition_blocks_stale_apply(scene):
    s = scene
    discovery = call(s, 'POST', '/discovery/sessions', {'operation_id': 'requirements-entry',
        'title': '用户需求', 'entry_mode': 'REQUIREMENTS', 'statement': '任务 A 的约束需确认，任务 B 独立保留。'})
    candidate = {'title': '已知用户需求', 'phase': 'SPECIFY', 'summary': '用户指定验收',
        'claims': [{'id': 'req-a', 'text': '任务 A 支持准确反馈归位', 'category': 'REQUIREMENT',
            'origin': 'USER_STATEMENT', 'acceptance': ['准确定位源版本和对象']},
            {'id': 'req-b', 'text': '任务 B 保留已确认结论', 'category': 'REQUIREMENT',
            'origin': 'USER_STATEMENT', 'acceptance': ['原结论不丢失']}], 'questions': [], 'graphs': []}
    discovery = call(s, 'PUT', '/discovery/sessions/' + discovery['id'], {'operation_id': 'requirements-edit',
        'expected_revision': discovery['revision'], 'candidate': candidate, 'change_reason': '用户明确两项要求'})
    discovery = call(s, 'POST', '/discovery/sessions/' + discovery['id'] + '/confirm', {
        'operation_id': 'requirements-confirm', 'expected_revision': discovery['revision'],
        'session_sha256': discovery['sha256'], 'user_confirmed': True,
        'accepted_claim_ids': ['req-a', 'req-b'], 'accepted_graph_ids': []})
    material = discovery['payload']['source_materials']
    proposal = call(s, 'POST', '/planning/proposals', {'operation_id': 'requirements-task-plan',
        'prompt': '用户已确认需求的任务网络', 'mode': 'manual', 'materials': material,
        'discovery_ref': ref(discovery), 'candidate': s['proposal']['payload']['candidate']})
    s['proposal'] = call(s, 'POST', '/planning/proposals/' + proposal['id'] + '/confirm', {
        'operation_id': 'requirements-plan-confirm', 'expected_revision': proposal['revision'],
        'proposal_sha256': proposal['sha256'], 'user_confirmed': True})
    f = feedback(s)
    m = mapping(s, f, discovery_ref=ref(discovery), requirement_ids=['req-a'])
    check(m['payload']['discovery_ref'] == ref(discovery) and m['payload']['requirement_ids'] == ['req-a'])
    p = prepare(s, m)
    candidate['claims'][0]['acceptance'] = ['用户新约束需要重新确认']
    call(s, 'PUT', '/discovery/sessions/' + discovery['id'], {'operation_id': 'requirements-human-change',
        'expected_revision': discovery['revision'], 'candidate': candidate, 'change_reason': '人工改变原验收要求'})
    apply(s, p, codes=(409,))
    check((s['root'] / '任务与harness.json').read_text() == s['original'])
    historical = call(s, 'GET', '/native-rework/mappings/' + m['mapping_ref']['id'])
    check(historical['payload'] == m['payload'] and historical['mapping_ref'] == m['mapping_ref'])
    check(historical['current_basis_check']['status'] == 'STALE_REVIEW_REQUIRED')
