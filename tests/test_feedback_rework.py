"""Original MD/JSON fixtures through the real persistent API and local files.

No model is invoked. Classification is supplied as a source-bound proposal.
Candidate merge3 was examined at https://github.com/breezy-team/merge3 : its
GPL-2.0 line merges neither validate object scope nor establish file authority.
This slice reuses stdlib JSONDecoder and existing LocalWorkspaces CAS instead.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import httpx
import pytest

from apps.api.feedback_rework import register_feedback_rework_routes
from packages.domain.feedback_rework import FeedbackRework, json_value_span
from packages.domain.store import InvalidInput


@pytest.fixture
def feedback_client(client):
    if not hasattr(client.app.state, 'feedback_rework'):
        register_feedback_rework_routes(client.app, client.app.state.store)
    return client


def ok(response, codes=(200, 201)):
    if response.status_code not in codes:
        raise AssertionError(f'{response.status_code}: {response.text}')
    return response.json()


def setup(client, tmp_path):
    pid = ok(client.post('/api/projects', json={'name': '原创局部返工夹具'}))['id']
    root = tmp_path.parent / (tmp_path.name + '_native_files')
    root.mkdir()
    (root / 'task.json').write_text('{\n  "requirement": "基准要求",\n  "human": "原文",\n  "accepted": true\n}\n', encoding='utf-8')
    (root / 'independent.md').write_text('# 已确认独立任务\n保留独立结论。\n', encoding='utf-8')
    (root / 'harness.md').write_text('# 任务入口\n要求：独特的旧要求。\n人工注释：原始值。\n', encoding='utf-8')
    wid = ok(client.post(f'/api/projects/{pid}/local-workspaces', json={
        'idempotency_key': 'workspace', 'root': str(root),
        'allowed_write_paths': ['task.json', 'independent.md', 'harness.md']}))['id']
    material = ok(client.post(f'/api/projects/{pid}/documents', files={'file': ('input.md', '原创需求和三个任务。'.encode())}, data={'source': 'original-feedback-fixture'}))
    proposal = ok(client.post(f'/api/projects/{pid}/planning/proposals', json={
        'operation_id': 'real-proposal', 'prompt': '根据原创材料建立可核对任务', 'mode': 'manual',
        'materials': [{'document_id': material['id'], 'revision': material['revision'], 'sha256': material['sha256']}],
        'candidate': {'title': '真实三节点计划', 'summary': '输入变化只影响所声明的两项任务。',
            'nodes': [{'id': 'input', 'title': '要求核对', 'description': '核对输入要求',
                    'outputs': ['要求记录'], 'acceptance': ['对应原始输入与明确范围']},
                {'id': 'dependent', 'title': '下游产物', 'description': '使用已核对要求', 'depends_on': ['input'],
                    'outputs': ['下游结果'], 'acceptance': ['采用已核对要求并附检查记录']},
                {'id': 'independent', 'title': '独立归档', 'description': '保持已确认的独立结论',
                    'outputs': ['独立归档'], 'acceptance': ['原始独立内容保持一致']}]}}))
    proposal = ok(client.post(f'/api/projects/{pid}/planning/proposals/{proposal["id"]}/confirm', json={
        'operation_id': 'confirm', 'expected_revision': proposal['revision'], 'proposal_sha256': proposal['sha256'],
        'user_confirmed': True}))
    return pid, wid, root, proposal


def source(client, pid, wid, path):
    return ok(client.get(f'/api/projects/{pid}/local-workspaces/{wid}/file', params={'path': path}))['source_ref']


def proposal_ref(proposal):
    return {'id': proposal['id'], 'revision': proposal['revision'], 'sha256': proposal['sha256']}


def bind(client, pid, wid, path, proposal, locator=None, op=None, **extra):
    return ok(client.post(f'/api/projects/{pid}/feedback-rework/bindings', json={
        'operation_id': op or 'bind-' + path.replace('.', '-'), 'file_source_ref': source(client, pid, wid, path),
        'locator': locator or {'kind': 'FILE'}, 'proposal_ref': proposal_ref(proposal), **extra}))


def make_plan(client, pid, wid, proposal, bindings, replacements, *, kind='CONSTRAINT', conditions=None, op='plan'):
    feedback = ok(client.post(f'/api/projects/{pid}/feedback-rework/feedback', json={
        'operation_id': 'feedback-' + op, 'binding_ref': bindings[0]['binding_ref'],
        'comment': '修订所选要求，并保留人工修改和独立任务。', 'mode': 'DRAFT'}))
    task_bindings = {'input': bindings[0], 'independent': bindings[-1]}
    contracts = []
    for task, binding in task_bindings.items():
        contracts.append({'task_id': task, 'inputs': [{
            'binding_ref': binding['binding_ref'], 'input_value_sha256': binding['payload']['selected_value_sha256'],
            'condition': (conditions or {}).get(task, 'VALUE_CHANGED'),
            'invalidating_kinds': ['DATA', 'CONSTRAINT'], 'verification_note': '原创夹具已核对输入和独立范围；语义待审阅。',
            'verification_refs': [binding['binding_ref']]}]})
    body = {'operation_id': op, 'feedback_ref': feedback['feedback_ref'], 'proposal_ref': proposal_ref(proposal),
        'classification': {'kind': kind, 'origin': 'USER_STATEMENT', 'rationale': '来自本次明确的对象反馈，仍是提案。',
            'evidence_refs': [bindings[0]['binding_ref']]},
        'patches': [{'binding_ref': binding['binding_ref'], 'proposed_value': value} for binding, value in zip(bindings, replacements)],
        'write_scope': list({(b['payload']['file_source_ref']['workspace_id'], b['payload']['file_source_ref']['path']):
            {'workspace_id': b['payload']['file_source_ref']['workspace_id'], 'path': b['payload']['file_source_ref']['path']}
            for b in bindings[:len(replacements)]}.values()), 'contracts': contracts}
    value = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans', json=body))
    return value, body, feedback


def apply_body(client, pid, plan, op='apply', rebase=False):
    scopes = plan['payload']['write_scope']
    return {'operation_id': op, 'plan_ref': plan['plan_ref'], 'write_scope': scopes,
        'expected_current_sources': [source(client, pid, s['workspace_id'], s['path']) for s in scopes], 'allow_rebase': rebase}


def test_durable_exact_feedback_draft_without_native_authority(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'},
        native_context_ref={'window_ref': 'context-supplied-by-host', 'frame_ref': 'not-a-file-grant'}, task_ids=['input'])
    if binding['payload']['selected_value'] != '基准要求':
        raise AssertionError('Selected native object does not match actual bytes')
    body = {'operation_id': 'draft', 'binding_ref': binding['binding_ref'], 'comment': '改变这条要求', 'mode': 'DRAFT'}
    draft = ok(client.post(f'/api/projects/{pid}/feedback-rework/feedback', json=body))
    if draft['native_requested'] or draft['payload']['dispatch_status'] != 'NOT_DISPATCHED':
        raise AssertionError('Persisting feedback must not simulate model dispatch')
    if binding['payload']['native_context_grants_file_authority']:
        raise AssertionError('Window/frame reference must not grant file access')
    restored = FeedbackRework(client.app.state.store, client.app.state.local_workspaces, client.app.state.planning_proposals)
    if restored.get(pid, draft['feedback_ref']['id'])['payload'] != draft['payload']:
        raise AssertionError('Feedback did not survive service recreation')
    if ok(client.post(f'/api/projects/{pid}/feedback-rework/feedback', json=body)) != draft:
        raise AssertionError('Exact retry must replay immutable feedback')
    body['comment'] = 'another request'
    if client.post(f'/api/projects/{pid}/feedback-rework/feedback', json=body).status_code != 409:
        raise AssertionError('Operation identity must bind its original payload')
    other = ok(client.post('/api/projects', json={'name': '另一项目'}))['id']
    if client.get(f'/api/projects/{other}/feedback-rework/bindings/{binding["binding_ref"]["id"]}').status_code != 404:
        raise AssertionError('Cross-project binding leaked')
    with client.app.state.store.connect() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE feedback_rework_records SET payload='{}'")
    if root.joinpath('task.json').read_text(encoding='utf-8').count('基准要求') != 1:
        raise AssertionError('Feedback recording changed the original file')


def test_json_rebase_preserves_human_bytes_confirmation_and_independent_branch(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
    independent = bind(client, pid, wid, 'independent.md', proposal)
    plan, request, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['新增要求'])
    statuses = {r['task_id']: r['status'] for r in plan['payload']['impact']['tasks']}
    if statuses != {'input': 'AFFECTED', 'dependent': 'AFFECTED', 'independent': 'RETAINED_BY_DECLARED_CONDITION'}:
        raise AssertionError(statuses)
    human = root.joinpath('task.json').read_text(encoding='utf-8').replace('"human": "原文"', '"human" :  "用户手改😀"')
    root.joinpath('task.json').write_text(human, encoding='utf-8')
    independent_before = root.joinpath('independent.md').read_bytes()
    applied = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply',
        json=apply_body(client, pid, plan, rebase=True)))
    if root.joinpath('task.json').read_text(encoding='utf-8') != human.replace('基准要求', '新增要求'):
        raise AssertionError('Unrelated manually edited JSON bytes were not retained')
    if root.joinpath('independent.md').read_bytes() != independent_before:
        raise AssertionError('Independent branch changed')
    current = ok(client.get(f'/api/projects/{pid}/planning/proposals/{proposal["id"]}'))
    if current['sha256'] != proposal['sha256'] or current['payload']['confirmation'] != proposal['payload']['confirmation']:
        raise AssertionError('Original confirmation or exact plan revision was lost')
    if applied['payload']['modified_file_count'] != 1 or plan['payload']['impact']['baseline_all_tasks'] != 3 or plan['payload']['impact']['selected_rework_tasks'] != 2:
        raise AssertionError('Scoped fixture comparison counts differ')
    if plan['payload']['classification_status'] != 'PROPOSAL_NOT_SEMANTICALLY_VERIFIED':
        raise AssertionError('Classification was promoted without semantic verification')
    if not Path(applied['artifact']['path']).is_file():
        raise AssertionError('Checking receipt is not a real artifact')
    downloaded = client.get(applied['artifact']['download_url'])
    if downloaded.status_code != 200 or hashlib.sha256(downloaded.content).hexdigest() != applied['artifact']['sha256']:
        raise AssertionError('Downloaded checking receipt is not the frozen artifact')
    historical = client.app.state.local_workspaces.read(pid, wid, 'task.json', 1)
    if historical['source_ref'] != binding['payload']['file_source_ref'] or '基准要求' not in historical['content']:
        raise AssertionError('Original file version was overwritten')
    if ok(client.post(f'/api/projects/{pid}/feedback-rework/plans', json=request)) != plan:
        raise AssertionError('Plan replay changed after applying its patch')
    print('FEEDBACK_REWORK_CHECK_RECEIPT=' + json.dumps({
        'scope': 'ORIGINAL_MD_JSON_API_FIXTURE_NOT_PRODUCT_INNOVATION_VALIDATION',
        'plan_ref': plan['plan_ref'], 'receipt_ref': applied['receipt_ref'],
        'baseline_all_tasks': plan['payload']['impact']['baseline_all_tasks'],
        'known_dependency_baseline_tasks': plan['payload']['impact']['known_dependency_baseline_tasks'],
        'selected_rework_tasks': plan['payload']['impact']['selected_rework_tasks'],
        'modified_file_count': applied['payload']['modified_file_count'],
        'independent_bytes_preserved': True, 'manual_bytes_preserved': True,
        'confirmation_sha256_preserved': current['sha256'],
        'old_file_source_ref': historical['source_ref'], 'new_file_source_ref': applied['payload']['outcomes'][0]['source_ref'],
        'checking_receipt_sha256': applied['artifact']['sha256'], 'checking_receipt': applied['payload']},
        ensure_ascii=False, sort_keys=True))


def test_same_target_human_change_conflicts_but_independent_file_applies(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
    harness = bind(client, pid, wid, 'harness.md', proposal, {'kind': 'TEXT_RANGE', 'char_start': 11, 'char_end': 18,
        'quote_sha256': hashlib.sha256(root.joinpath('harness.md').read_text(encoding='utf-8')[11:18].encode()).hexdigest()})
    plan, _, _ = make_plan(client, pid, wid, proposal, [binding, harness], ['建议要求', '替换文本'])
    human = root.joinpath('task.json').read_text(encoding='utf-8').replace('基准要求', '用户最新要求')
    root.joinpath('task.json').write_text(human, encoding='utf-8')
    apply = apply_body(client, pid, plan, rebase=True)
    result = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=apply), (207,))
    if result['status'] != 'PARTIAL' or root.joinpath('task.json').read_text(encoding='utf-8') != human:
        raise AssertionError('Conflicting human target was overwritten')
    if '替换文本' not in root.joinpath('harness.md').read_text(encoding='utf-8'):
        raise AssertionError('Independent scoped file did not continue')
    if 'TARGET_CHANGED' not in result['payload']['outcomes'][0]['reason']:
        raise AssertionError('Conflict has no specific cause')
    if ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=apply), (207,)) != result:
        raise AssertionError('Partial outcome must replay rather than rewrite files')


def test_text_rebase_preserves_external_prefix_and_refuses_duplicate_quote(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    text = root.joinpath('harness.md').read_text(encoding='utf-8')
    quote = '独特的旧要求'
    start = text.index(quote)
    binding = bind(client, pid, wid, 'harness.md', proposal, {'kind': 'TEXT_RANGE', 'char_start': start,
        'char_end': start + len(quote), 'quote_sha256': hashlib.sha256(quote.encode()).hexdigest()})
    independent = bind(client, pid, wid, 'independent.md', proposal)
    plan, _, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['新的验收要求'])
    human = '用户增加的前言😀\n' + text.replace('原始值', '人工最新注释')
    root.joinpath('harness.md').write_text(human, encoding='utf-8')
    result = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply',
        json=apply_body(client, pid, plan, rebase=True)))
    if result['status'] != 'APPLIED' or root.joinpath('harness.md').read_text(encoding='utf-8') != human.replace(quote, '新的验收要求'):
        raise AssertionError('Text rebase did not retain other human edits')
    root.joinpath('harness.md').write_text(text + quote, encoding='utf-8')
    second = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply',
        json=apply_body(client, pid, plan, op='ambiguous', rebase=True)), (409,))
    if 'AMBIGUOUS' not in second['payload']['outcomes'][0]['reason']:
        raise AssertionError('Duplicate quotation was merged without exact identity')


def test_stale_current_cas_and_extra_write_scope_are_refused(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
    independent = bind(client, pid, wid, 'independent.md', proposal)
    plan, _, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['建议更新'])
    body = apply_body(client, pid, plan, rebase=True)
    manual = root.joinpath('task.json').read_text(encoding='utf-8').replace('原文', '并发人工编辑')
    root.joinpath('task.json').write_text(manual, encoding='utf-8')
    result = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=body), (409,))
    if 'SOURCE_DRIFT' not in result['payload']['outcomes'][0]['reason'] or root.joinpath('task.json').read_text(encoding='utf-8') != manual:
        raise AssertionError('A stale inspected version overwrote current manual bytes')
    bad = apply_body(client, pid, plan, op='wide', rebase=True)
    bad['write_scope'].append({'workspace_id': wid, 'path': 'independent.md'})
    if client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=bad).status_code != 422:
        raise AssertionError('Extra write path was accepted')


def test_expression_conditions_and_unknown_dependencies_remain_proposals(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, _, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
    independent = bind(client, pid, wid, 'independent.md', proposal)
    expression, _, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['表达修订'], kind='EXPRESSION')
    impact = expression['payload']['impact']
    if impact['selected_rework_tasks'] != 0 or impact['known_dependency_baseline_tasks'] != 2 or impact['pending_assessment_tasks'] != 1:
        raise AssertionError('Kind exclusion or absent contract was promoted to semantic certainty')
    if not expression['payload']['unknown_impact']:
        raise AssertionError('Undeclared impact was hidden')
    unknown, _, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['未知变化'], kind='UNKNOWN', op='unknown-plan')
    if unknown['payload']['impact']['counts']['UNKNOWN'] != 2:
        raise AssertionError('Unknown classification failed to propagate in the actual DAG')


@pytest.mark.parametrize('text,pointer', [('{"x":1,"x":2}', '/x'), ('{"x":NaN}', '/x'), ('{"x":1}', '/missing'), ('[1,2]', '/01'), ('{"x":1}', '/x~2')])
def test_json_locator_rejects_ambiguous_or_invalid_structure(text, pointer):
    with pytest.raises(InvalidInput):
        json_value_span(text, pointer)


def test_multiple_same_file_json_patches_preserve_unselected_bytes(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    a = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'}, op='a')
    b = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/accepted'}, op='b')
    before = root.joinpath('task.json').read_text(encoding='utf-8')
    plan, _, _ = make_plan(client, pid, wid, proposal, [a, b], ['新要求', False])
    result = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=apply_body(client, pid, plan)))
    if result['status'] != 'APPLIED' or root.joinpath('task.json').read_text(encoding='utf-8') != before.replace('基准要求', '新要求').replace('true', 'false'):
        raise AssertionError('Multiple exact-value splices failed or reserialized unrelated bytes')


def test_apply_recovery_after_file_write_before_receipt_commit(feedback_client, tmp_path, monkeypatch):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
    independent = bind(client, pid, wid, 'independent.md', proposal)
    plan, _, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['恢复后要求'])
    body = apply_body(client, pid, plan)
    service = client.app.state.feedback_rework
    original_save = service._save
    def crash(pid, body, kind, payload):
        if kind == 'APPLY':
            raise RuntimeError('original injected post-write interruption')
        return original_save(pid, body, kind, payload)
    monkeypatch.setattr(service, '_save', crash)
    with pytest.raises(RuntimeError, match='post-write'):
        client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=body)
    if '恢复后要求' not in root.joinpath('task.json').read_text(encoding='utf-8'):
        raise AssertionError('Interruption was not after a real file write')
    inspection = ok(client.get(f'/api/projects/{pid}/feedback-rework/operations/{body["operation_id"]}'))
    if inspection['status'] != 'PROCESSING' or inspection['file_operations'][0]['operation']['status'] != 'APPLIED':
        raise AssertionError('Interrupted operation cannot be inspected against the actual file ledger')
    monkeypatch.setattr(service, '_save', original_save)
    recovered = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=body))
    if not recovered['payload']['outcomes'][0]['recovered'] or len(client.app.state.local_workspaces.history(pid, wid, 'task.json')) != 2:
        raise AssertionError('Interrupted apply was rewritten instead of recovered')


def test_write_boundary_detects_interleaved_manual_edit(feedback_client, tmp_path, monkeypatch):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
    independent = bind(client, pid, wid, 'independent.md', proposal)
    plan, _, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['不能覆盖人工修改'])
    body = apply_body(client, pid, plan)
    service = client.app.state.feedback_rework
    original = service.workspaces.edit
    latest = root.joinpath('task.json').read_text(encoding='utf-8').replace('原文', '写入前人工并发变化')
    def interleave(pid, wid, request):
        root.joinpath('task.json').write_text(latest, encoding='utf-8')
        return original(pid, wid, request)
    monkeypatch.setattr(service.workspaces, 'edit', interleave)
    result = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=body), (409,))
    if result['status'] != 'CONFLICT' or root.joinpath('task.json').read_text(encoding='utf-8') != latest:
        raise AssertionError('Interleaved manual write before existing CAS was overwritten')


def test_post_replace_failure_is_unknown_and_does_not_claim_no_write(feedback_client, tmp_path, monkeypatch):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
    independent = bind(client, pid, wid, 'independent.md', proposal)
    plan, _, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['写后状态未知'])
    body = apply_body(client, pid, plan)
    service = client.app.state.feedback_rework
    original = service.workspaces._replace
    def fail_after_write(binding, path, raw):
        original(binding, path, raw)
        raise OSError('original injected failure after actual replace')
    monkeypatch.setattr(service.workspaces, '_replace', fail_after_write)
    result = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=body), (409,))
    outcome = result['payload']['outcomes'][0]
    if result['status'] != 'UNKNOWN_OUTCOME' or outcome['file_written_by_this_step'] is not None:
        raise AssertionError('Post-write exception was incorrectly certified as no effect')
    if '写后状态未知' not in root.joinpath('task.json').read_text(encoding='utf-8'):
        raise AssertionError('Original failure was not after actual replacement')
    if ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=body), (409,)) != result:
        raise AssertionError('Unknown outcome was repeated instead of preserving its receipt')


def test_file_version_condition_covers_other_pointer_in_same_file(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, _, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'}, op='requirement')
    same_file = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/human'}, op='other-pointer')
    plan, _, _ = make_plan(client, pid, wid, proposal, [binding, same_file], ['文件版本变化'],
        conditions={'independent': 'ANY_FILE_VERSION_CHANGED'})
    if plan['payload']['impact']['counts']['AFFECTED'] != 3:
        raise AssertionError('Whole-file-version condition missed a different selected object in that file')


def test_current_consumed_input_change_cannot_be_retained_as_valid(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
    independent = bind(client, pid, wid, 'independent.md', proposal)
    root.joinpath('independent.md').write_text('用户在计划生成前已改变这项独立输入。\n', encoding='utf-8')
    plan, _, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['本次要求变化'])
    statuses = {r['task_id']: r['status'] for r in plan['payload']['impact']['tasks']}
    if statuses['independent'] != 'AFFECTED':
        raise AssertionError('A changed consumed input was still presented as conditionally retained')
    observation = plan['payload']['input_observations'][independent['binding_ref']['id']]
    if observation['current_source_ref']['revision'] != 2 or observation['value_sha256'] == independent['payload']['selected_value_sha256']:
        raise AssertionError('Input invalidation has no actual current version/field observation')


def test_object_alias_and_multiple_conditions_do_not_hide_known_impact(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, _, proposal = setup(client, tmp_path)
    binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'}, op='original-object')
    alias = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'}, op='same-object-alias')
    independent = bind(client, pid, wid, 'independent.md', proposal)
    _, body, _ = make_plan(client, pid, wid, proposal, [binding, independent], ['需要返工'])
    body['operation_id'] = 'alias-plan'
    item = body['contracts'][0]['inputs'][0]
    item['binding_ref'] = alias['binding_ref']
    item['verification_refs'] = [alias['binding_ref']]
    body['contracts'][0]['inputs'].append({'binding_ref': independent['binding_ref'],
        'input_value_sha256': independent['payload']['selected_value_sha256'], 'condition': 'MANUAL_REVIEW',
        'verification_note': '另一个输入尚待人工评估', 'verification_refs': [independent['binding_ref']]})
    plan = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans', json=body))
    statuses = {r['task_id']: r['status'] for r in plan['payload']['impact']['tasks']}
    if statuses['input'] != 'AFFECTED' or statuses['dependent'] != 'AFFECTED':
        raise AssertionError('Another binding ID or unknown condition hid a known changed object')


def test_overlapping_patch_scopes_rejected_before_write(feedback_client, tmp_path):
    client = feedback_client
    pid, wid, root, proposal = setup(client, tmp_path)
    a = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'}, op='a')
    b = bind(client, pid, wid, 'task.json', proposal, {'kind': 'FILE'}, op='b')
    before = root.joinpath('task.json').read_bytes()
    feedback = ok(client.post(f'/api/projects/{pid}/feedback-rework/feedback', json={
        'operation_id': 'feedback', 'binding_ref': a['binding_ref'], 'comment': '明确单个对象反馈'}))
    response = client.post(f'/api/projects/{pid}/feedback-rework/plans', json={
        'operation_id': 'overlap', 'feedback_ref': feedback['feedback_ref'], 'proposal_ref': proposal_ref(proposal),
        'classification': {'kind': 'CONSTRAINT', 'origin': 'USER_STATEMENT', 'rationale': '尚待核对', 'evidence_refs': [a['binding_ref']]},
        'patches': [{'binding_ref': a['binding_ref'], 'proposed_value': '局部'}, {'binding_ref': b['binding_ref'], 'proposed_value': '{}'}],
        'write_scope': [{'workspace_id': wid, 'path': 'task.json'}]})
    if response.status_code != 422 or root.joinpath('task.json').read_bytes() != before:
        raise AssertionError('Overlapping whole-file/object patch was allowed')


def test_real_loopback_api_restart_retains_feedback_and_artifact(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    data = tmp_path / 'data'
    origin = f'http://127.0.0.1:{port}'
    launch = ('import os; from pathlib import Path; import uvicorn; '
        'from apps.api.main import create_app; from apps.api.config import Settings; '
        'from apps.api.feedback_rework import register_feedback_rework_routes; '
        'app=create_app(Settings(Path(os.environ["REWORK_TEST_DATA"]),port=int(os.environ["REWORK_TEST_PORT"]))); '
        'register_feedback_rework_routes(app,app.state.store) if not hasattr(app.state,"feedback_rework") else None; '
        'uvicorn.run(app,host="127.0.0.1",port=int(os.environ["REWORK_TEST_PORT"]),log_level="error")')
    env = {**os.environ, 'REWORK_TEST_DATA': str(data), 'REWORK_TEST_PORT': str(port)}
    command = [sys.executable, *(['-O'] if not __debug__ else []), '-c', launch]
    process = None
    startup_logs = Path(os.environ.get('EEAS_R3_EVIDENCE', tmp_path)) / 'legacy-loopback-startup'
    startup_logs.mkdir(parents=True, exist_ok=True)
    streams = []
    def start():
        number = len(streams) // 2
        stdout = (startup_logs / f'{number}-stdout.txt').open('w')
        stderr = (startup_logs / f'{number}-stderr.txt').open('w')
        streams.extend([stdout, stderr])
        process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1], env=env,
            stdout=stdout, stderr=stderr)
        started = time.monotonic()
        with httpx.Client(trust_env=False) as health:
            while time.monotonic() - started < 30:
                if process.poll() is not None:
                    raise AssertionError('Real API server exited; inspect preserved startup logs')
                try:
                    if health.get(origin + '/health', timeout=.2).status_code == 200:
                        (startup_logs / f'{number}-ready.json').write_text(json.dumps({
                            'actual_owned_pid': process.pid, 'startup_seconds': time.monotonic() - started,
                            'ordinary_mutation_replayed': False}))
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
        process.terminate()
        process.wait(timeout=5)
        raise AssertionError('Real API server did not become ready')
    try:
        process = start()
        with httpx.Client(base_url=origin, trust_env=False) as client:
            bootstrap = ok(client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}))
            client.headers.update({'Origin': origin, 'X-EEAS-CSRF': bootstrap['csrf_token']})
            pid, wid, _, proposal = setup(client, tmp_path)
            binding = bind(client, pid, wid, 'task.json', proposal, {'kind': 'JSON_POINTER', 'pointer': '/requirement'})
            independent = bind(client, pid, wid, 'independent.md', proposal)
            plan, _, feedback = make_plan(client, pid, wid, proposal, [binding, independent], ['真实API修订'])
        process.terminate()
        process.wait(timeout=5)
        process = start()
        with httpx.Client(base_url=origin, trust_env=False) as client:
            bootstrap = ok(client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}))
            client.headers.update({'Origin': origin, 'X-EEAS-CSRF': bootstrap['csrf_token']})
            reloaded = ok(client.get(f'/api/projects/{pid}/feedback-rework/feedback/{feedback["feedback_ref"]["id"]}'))
            if reloaded['payload'] != feedback['payload']:
                raise AssertionError('Real process restart lost feedback')
            applied = ok(client.post(f'/api/projects/{pid}/feedback-rework/plans/{plan["plan_ref"]["id"]}/apply', json=apply_body(client, pid, plan)))
            artifact = client.get(applied['artifact']['download_url'])
            if artifact.status_code != 200 or hashlib.sha256(artifact.content).hexdigest() != applied['artifact']['sha256']:
                raise AssertionError('Real loopback API artifact is not persistent and hash-bound')
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        for stream in streams:
            stream.close()
