"""Same durable native question and real feedback/rework/CAS; no model or GUI.

Only the host observation boundary is controlled. All files, projects, proposals,
feedback, mappings, plans and apply receipts use their production services.
"""
import asyncio
import copy
import hashlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from apps.api.desktop_context import DesktopContextRPC
from apps.api.native_messages import register_native_message_routes, NativeQuestionInput
from packages.domain.local_workspaces import digest
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict
from test_native_collaboration import HostLifecycleCounter
from test_native_messages import TOKEN
from test_native_rework import scene, source, mapping, prepare, ref, check


@pytest.fixture
def ide(scene):
    s = scene
    app = FastAPI()
    app.state._state.update(s['client'].app.state._state)
    app.exception_handlers.update(s['client'].app.exception_handlers)
    authority = NativeCollaborationAuthority(s['data_dir'] / 'native-ide-host', token=TOKEN,
        owner_pid=123, native=HostLifecycleCounter(), platform='posix')
    native = register_native_message_routes(app, authority=authority, context_rpc=DesktopContextRPC(authority))
    s.update(native=native, cid='original-discussion', parent='original-file-message')
    body = {'operation_id': s['parent'], 'raw_text': '仅此原文件。', 'conversation_id': s['cid'],
        'project_id': s['pid'], 'file_source_ref': source(s)}
    record, dispatch = native._prepare_ordinary(body, TOKEN)
    check(dispatch is None and record['status'] == 'NOT_CONFIGURED')
    with TestClient(app, headers={'X-EEAS-Desktop-Host': TOKEN}) as client:
        s['ide'] = client
        yield s
    check(native.tasks == {} and native.transport.active == {})
    asyncio.run(native.close())


def route(s, suffix='', parent=None, cid=None):
    return f'/api/desktop-host/native-conversations/{cid or s["cid"]}/operations/{parent or s["parent"]}' + suffix


def get(s, suffix='', **params):
    return s['ide'].get(route(s, suffix), params={'project_id': s['pid'], **params})


def post(s, suffix, body):
    return s['ide'].post(route(s, suffix), params={'project_id': s['pid']}, json=body)


def feedback_body(s, op='ide-feedback', quote='A 原已确认要求'):
    return {'operation_id': op, 'file_source_ref': source(s), 'comment': '仅改 A，保留人工 B。',
        'selection': {'kind': 'EXACT_QUOTE', 'quote': quote}}


def recorded(s, **kw):
    body = feedback_body(s, **kw); r = post(s, '/feedback', body)
    check(r.status_code == 200, r.text)
    value = r.json(); check(value['status'] == 'RECORDED')
    return body, value


def planned(s):
    _, f = recorded(s)
    public = s['client'].app.state.native_feedback.get(s['pid'], f['feedback_ref']['id'])
    m = mapping(s, public)
    p = prepare(s, m, independent=False)
    saved = s['native'].ws._read(s['native'].path(s['parent']), authority=False)
    # The fixture records the exact outputs of the real service as an original
    # tool result; no model/CLI is run or simulated as qualified inference.
    saved['rework_records'] = [{'project_id': s['pid'], 'record_ref': v['record_ref']} for v in (m, p)]
    s['native'].ws._write(s['native'].path(s['parent']), saved)
    return f, m, p


def test_file_original_current_scope_and_host_are_exact(ide):
    s = ide; original = get(s, '/file', version='ORIGINAL_SAVED')
    check(original.status_code == 200, original.text)
    v = original.json(); check(v['content'] == s['original'] and v['read_only'] and v['model_calls'] == 0)
    check(v['file_source_ref']['sha256'] == hashlib.sha256(v['content'].encode()).hexdigest())
    (s['root'] / '任务与harness.json').write_bytes(('\ufeff' + s['original'].replace('\n', '\r\n') + '😀\r\n').encode())
    current = get(s, '/file').json(); old = get(s, '/file', version='ORIGINAL_SAVED').json()
    check(current['content'].startswith('\ufeff') and current['content'].endswith('😀\r\n'))
    check(current['file_source_ref'] != old['file_source_ref'] and not old['current'])
    check(s['ide'].get(route(s, '/file', cid='different'), params={'project_id': s['pid']}).status_code == 409)
    check(s['ide'].get(route(s, '/file')).status_code == 422)
    check(s['ide'].get(route(s, '/file'), params={'project_id': s['pid']}, headers={'X-EEAS-Desktop-Host': 'wrong'}).status_code == 409)


def test_feedback_exact_get_replay_ambiguous_and_cross_parent(ide):
    s = ide; body, f = recorded(s)
    check(f['selection_verification'] == 'TEXT_RANGE_HASH_VERIFIED' and f['file_selection']['kind'] == 'TEXT_RANGE')
    check(f['request'] == body and f['request_sha256'] == digest(body))
    check(get(s, '/feedback-operations/' + body['operation_id']).json() == f)
    check(post(s, '/feedback', body).json() == f)
    changed = {**body, 'comment': '另一条要求'}
    check(post(s, '/feedback', changed).status_code == 409)
    other = {'operation_id': 'second-parent', 'raw_text': '其他原问题', 'project_id': s['pid'],
        'conversation_id': s['cid'], 'file_source_ref': source(s)}
    s['native']._prepare_ordinary(other, TOKEN)
    check(s['ide'].get(route(s, '/feedback-operations/' + body['operation_id'], parent='second-parent'), params={'project_id': s['pid']}).status_code == 409)
    _, missing = recorded(s, op='missing', quote='不在此文件')
    check(missing['selection_verification'] == 'QUOTE_NOT_FOUND' and missing['file_selection'] is None)
    _, repeated = recorded(s, op='repeated', quote='"')
    check(repeated['selection_verification'] == 'QUOTE_AMBIGUOUS' and repeated['file_selection'] is None)
    invalid = {**body, 'operation_id': 'claimed-invalid-range', 'selection': {
        'kind': 'TEXT_RANGE', 'char_start': 0, 'char_end': 1, 'quote_sha256': '0' * 64}}
    rejected = post(s, '/feedback', invalid)
    check(rejected.status_code == 409, rejected.text)
    pending = get(s, '/feedback-operations/claimed-invalid-range').json()
    check(pending['status'] == 'INCOMPLETE_REVIEW_REQUIRED' and pending['request'] == invalid
        and 'feedback_ref' not in pending)


def test_preview_is_exact_saved_fullfile_and_cannot_borrow_another_plan(ide):
    s = ide; f, m, p = planned(s)
    original = (s['root'] / '任务与harness.json').read_bytes()
    r = get(s, '/rework-preview', plan_ref=json.dumps(p['plan_ref']))
    check(r.status_code == 200, r.text)
    v = r.json(); check(v['plan_ref'] == p['plan_ref'] and v['mapping_ref'] == m['mapping_ref'] and v['native_feedback_ref'] == f['feedback_ref'])
    check(v['diff']['before'] == s['original'] and 'A 本次提议要求' in v['diff']['after'])
    check(v['can_apply'] and not v['already_applied'] and v['diff']['content_scope'] == 'FULL_FILE')
    check(original == (s['root'] / '任务与harness.json').read_bytes())
    check(get(s, '/rework-preview', plan_ref=json.dumps({**p['plan_ref'], 'sha256': '0' * 64})).status_code == 409)
    check(get(s).json()['rework_previews'][0]['record_ref'] == p['record_ref'])
    saved = s['native'].ws._read(s['native'].path(s['parent']), authority=False)
    saved['rework_records'] = []
    s['native'].ws._write(s['native'].path(s['parent']), saved)
    check(get(s, '/rework-preview', plan_ref=json.dumps(p['plan_ref'])).status_code == 409)


def test_explicit_apply_preserves_manual_edit_and_original_get_does_not_reapply(ide):
    s = ide; _, _, p = planned(s)
    path = s['root'] / '任务与harness.json'
    path.write_text(s['original'].replace('原人工备注', '人手新增😀 不覆盖'), encoding='utf-8')
    current = get(s, '/file').json()['file_source_ref']
    body = {'operation_id': 'explicit-apply', 'plan_ref': p['plan_ref'],
        'expected_current_sources': [current], 'allow_rebase': True, 'user_approved': True}
    check(post(s, '/rework-apply', {**body, 'user_approved': False}).status_code == 422)
    r = post(s, '/rework-apply', body); check(r.status_code == 200, r.text)
    value = r.json(); check(value['status'] == 'APPLIED' and len(value['outcomes']) == 1)
    final = path.read_bytes(); check('人手新增😀 不覆盖' in final.decode() and 'A 本次提议要求' in final.decode())
    check(get(s, '/rework-operations/explicit-apply').json() == value)
    check(post(s, '/rework-apply', body).json() == value)
    check(final == path.read_bytes() and (s['root'] / '独立任务B.md').read_bytes() == s['independent'])
    preview = get(s, '/rework-preview', plan_ref=json.dumps(p['plan_ref'])).json()
    check(preview['already_applied'] and not preview['can_apply'])
    check(post(s, '/rework-apply', {**body, 'operation_id': 'never-repeat'}).status_code == 409)


def test_target_edit_and_stale_expected_source_do_not_write(ide):
    s = ide; _, _, p = planned(s); old = source(s)
    path = s['root'] / '任务与harness.json'
    path.write_text(s['original'].replace('A 原已确认要求', '人工改变目标 A'), encoding='utf-8')
    human = path.read_bytes()
    body = {'operation_id': 'conflicting-apply', 'plan_ref': p['plan_ref'],
        'expected_current_sources': [old], 'allow_rebase': True, 'user_approved': True}
    check(post(s, '/rework-apply', body).status_code == 409)
    body['expected_current_sources'] = [get(s, '/file').json()['file_source_ref']]
    r = post(s, '/rework-apply', body); check(r.status_code == 200, r.text)
    check(r.json()['status'] == 'CONFLICT' and path.read_bytes() == human)
    check(get(s, '/rework-operations/conflicting-apply').json()['status'] == 'CONFLICT')


def test_typed_feedback_uses_original_loop_selection_without_rewriting_old_request(ide):
    s = ide; _, f = recorded(s); _, other = recorded(s, op='other-feedback')
    old = s['native'].path(s['parent']).read_bytes()
    body = {'operation_id': 'typed-feedback-message', 'raw_text': '按此精确反馈准备预览。',
        'project_id': s['pid'], 'conversation_id': s['cid'], 'file_source_ref': f['file_source_ref'],
        'native_feedback_ref': f['feedback_ref']}
    result = asyncio.run(s['native'].submit(body, TOKEN)); check(result['status'] == 'NOT_CONFIGURED')
    binding = {'request': {'operation_id': body['operation_id'], 'prompt': body['raw_text']}}
    context = s['native'].read_rework_context({}, binding)
    check([v['feedback_ref'] for v in context['feedback']] == [f['feedback_ref']])
    with pytest.raises(Conflict):
        s['native'].resolve_native_feedback({'native_feedback_ref': other['feedback_ref'], 'proposal_ref': ref(s['proposal'])}, binding)
    public = s['client'].app.state.native_feedback.get(s['pid'], f['feedback_ref']['id'])
    mapping(s, public, locator=f['file_selection'])
    mapped = s['native'].resolve_native_feedback({'native_feedback_ref': f['feedback_ref'],
        'proposal_ref': ref(s['proposal'])}, binding)
    check(mapped['status'] == 'MAPPED' and mapped['payload']['locator'] == f['file_selection'])
    plan = s['native'].preview_native_rework({'mapping_ref': mapped['mapping_ref'],
        'classification_kind': 'EXPRESSION', 'rationale': '仅在原loop准备限定预览。',
        'proposed_value': '新的 A 要求'}, binding)
    check(plan['status'] == 'PROPOSED')
    exact = s['native'].ide_preview(s['pid'], s['cid'], body['operation_id'], plan['plan_ref'])
    check(exact['native_feedback_ref'] == f['feedback_ref'] and exact['locator'] == f['file_selection'])
    check(s['native'].path(s['parent']).read_bytes() == old)
    invalid = {**body, 'operation_id': 'cross-topic', 'conversation_id': 'different'}
    with pytest.raises(Conflict): asyncio.run(s['native'].submit(invalid, TOKEN))
    legacy = NativeQuestionInput.model_validate({'operation_id': 'legacy', 'raw_text': '原话'}).request_dict()
    check('native_feedback_ref' not in legacy)
