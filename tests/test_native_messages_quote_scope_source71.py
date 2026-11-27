"""Real private callbacks and rework services under a durable host question.

No original transport is started: the real host admission persists an explicit
unconfigured question, then its exact binding invokes production callbacks.
This qualifies scope enforcement, not a model or Windows lifetime.
"""
import asyncio
import copy

import pytest

from apps.api.desktop_context import DesktopContextRPC
from apps.api.native_messages import NativeMessages
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.local_workspaces import canonical
from packages.domain.store import Conflict
from test_native_collaboration import HostLifecycleCounter
from test_native_messages import TOKEN
from test_native_rework import call, check, feedback, ref, source
from test_native_rework_producer import export, producer_scene, selection, task_path


@pytest.fixture
def private_scene(producer_scene):
    s = producer_scene
    exported = export(s); path = task_path(s, exported)
    s.update(path=path, original=(s['root'] / path).read_bytes(), b_path=task_path(s, exported, 'b'))
    s['independent'] = (s['root'] / s['b_path']).read_bytes()
    authority = NativeCollaborationAuthority(s['data_dir'] / 'quote-scope-host', token=TOKEN,
        owner_pid=123, native=HostLifecycleCounter(), platform='posix')
    native = NativeMessages(s['client'].app, authority, DesktopContextRPC(authority))
    body = {'operation_id': 'quote-scope-original-question', 'raw_text': '仅按已保存反馈准备限定预览，不应用。',
        'project_id': s['pid'], 'file_source_ref': source(s, path)}
    record, dispatch = native._prepare_ordinary(body, TOKEN)
    check(dispatch is None and record['status'] == 'NOT_CONFIGURED')
    binding = {'request': {'operation_id': body['operation_id'], 'prompt': body['raw_text']}}
    s.update(native=native, binding=binding)
    try:
        yield s
    finally:
        check((s['root'] / path).read_bytes() == s['original'])
        check((s['root'] / s['b_path']).read_bytes() == s['independent'])
        check(native.tasks == {} and native.transport.active == {})
        asyncio.run(native.close())
        with s['client'].app.state.store.connect() as db:
            check(db.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0)


def invoke(s, method, arguments, *, denied=False, binding=None):
    try:
        value = getattr(s['native'], method)(arguments, binding or s['binding'])
    except Conflict as error:
        s['api_receipts'].append({'method': 'REAL_PRIVATE_CALLBACK', 'path': method,
            'input': copy.deepcopy(arguments), 'response': {'error': str(error)}})
        check(denied, str(error))
        return str(error)
    s['api_receipts'].append({'method': 'REAL_PRIVATE_CALLBACK', 'path': method,
        'input': copy.deepcopy(arguments), 'response': copy.deepcopy(value)})
    check(not denied, 'Production private callback expanded the quote scope: ' + str(value))
    return value


def resolve_args(s, f, locator=None):
    return {'native_feedback_ref': f['feedback_ref'], 'proposal_ref': ref(s['proposal']),
        **({'locator': locator} if locator is not None else {})}


def preview_args(mapping, value='本次限定替换文本'):
    binding = mapping['payload']['binding_ref']
    return {'mapping_ref': mapping['mapping_ref'], 'classification_kind': 'CONSTRAINT',
        'rationale': '只读预览；用户尚未授权任何文件写入', 'proposed_value': value,
        'contracts': [{'task_id': 'a', 'inputs': [{'binding_ref': binding,
            'input_value_sha256': mapping['payload']['selected_value_sha256'],
            'condition': 'VALUE_CHANGED', 'verification_note': '真实导出对象，未作语义验收',
            'verification_refs': [binding]}]}]}


def human_file_mapping(s, f, op):
    return call(s, 'POST', '/native-rework/mappings', {'operation_id': op,
        'native_feedback_ref': f['feedback_ref'], 'proposal_ref': ref(s['proposal']),
        'locator': {'kind': 'FILE'}, 'user_confirmed': True, 'task_ids': ['a']})


def test_unverified_quote_cannot_resolve_file_or_preview_a_human_file_mapping(private_scene):
    s = private_scene
    for name, quote, expected in [('missing', '不存在的指定原句', 'QUOTE_NOT_FOUND'),
                                  ('multiple', 'A', 'QUOTE_AMBIGUOUS')]:
        f = feedback(s, op='feedback-' + name, path=s['path'], selection={'kind': 'EXACT_QUOTE', 'quote': quote})
        check(f['payload']['selection_verification'] == expected)
        invoke(s, 'resolve_native_feedback', resolve_args(s, f, {'kind': 'FILE'}), denied=True)
        invoke(s, 'resolve_native_feedback', resolve_args(s, f), denied=True)
        # A real explicit public user mapping remains valid; the model cannot
        # borrow that wider locator to bypass its own quote restriction.
        human = human_file_mapping(s, f, 'human-' + name)
        check(human['status'] == 'MAPPED' and human['payload']['mapping_basis'] == 'USER_CONFIRMED_OBJECT_MAPPING')
        invoke(s, 'preview_native_rework', preview_args(human), denied=True)


def test_verified_exact_or_original_range_allows_only_canonical_resolve_and_preview(private_scene):
    s = private_scene; text = s['original'].decode('utf-8'); quote = 'A 原已确认要求'
    for name, picked in [('quote', {'kind': 'EXACT_QUOTE', 'quote': quote}), ('range', selection(text, quote))]:
        f = feedback(s, op='verified-' + name, path=s['path'], selection=picked)
        canonical_range = f['payload']['file_selection']
        check(f['payload']['selection_verification'] == 'TEXT_RANGE_HASH_VERIFIED')
        for bad in [{'kind': 'FILE'}, {'kind': 'JSON_POINTER', 'pointer': '/title'}, selection(text, text)]:
            invoke(s, 'resolve_native_feedback', resolve_args(s, f, bad), denied=True)
        mapped = invoke(s, 'resolve_native_feedback', resolve_args(s, f))
        check(mapped['status'] == 'MAPPED' and mapped['payload']['locator'] == canonical_range)
        explicit = invoke(s, 'resolve_native_feedback', resolve_args(s, f, canonical_range))
        check(explicit['status'] == 'MAPPED' and explicit['payload']['locator'] == canonical_range)
        preview = invoke(s, 'preview_native_rework', preview_args(mapped))
        check(preview['status'] == 'PROPOSED' and not preview['payload']['write_authorized'])
        human = human_file_mapping(s, f, 'human-wide-' + name)
        invoke(s, 'preview_native_rework', preview_args(human), denied=True)
        # Public manual preview remains unchanged for the explicitly chosen FILE.
        manual = call(s, 'POST', '/native-rework/plans', {**preview_args(human),
            'operation_id': 'human-preview-' + name, 'classification_origin': 'USER_STATEMENT'})
        check(manual['status'] == 'PROPOSED' and not manual['payload']['write_authorized'])
    context = invoke(s, 'read_rework_context', {})
    quote_feedback = next(row for row in context['feedback'] if row['feedback_ref']['id'] ==
        next(c['response']['feedback_ref']['id'] for c in s['api_receipts']
            if c['path'] == '/native-feedback' and c['input']['operation_id'] == 'verified-quote'))
    check(quote_feedback['selection_verification'] == 'TEXT_RANGE_HASH_VERIFIED')
    check(quote_feedback['requested_selection'] == {'kind': 'EXACT_QUOTE', 'quote': quote})
    check(len(canonical(context)) <= 48000)


def test_legacy_file_preview_and_original_scope_guards_remain(private_scene):
    s = private_scene
    f = feedback(s, path=s['path'])
    check('requested_selection' not in f['payload'] and f['payload']['file_selection'] is None)
    mapped = invoke(s, 'resolve_native_feedback', resolve_args(s, f, {'kind': 'FILE'}))
    check(mapped['status'] == 'MAPPED' and mapped['payload']['locator'] == {'kind': 'FILE'})
    preview = invoke(s, 'preview_native_rework', preview_args(mapped))
    check(preview['status'] == 'PROPOSED' and not preview['payload']['write_authorized'])
    wrong_mapping = preview_args(mapped)
    wrong_mapping['mapping_ref'] = {**mapped['mapping_ref'], 'sha256': '0' * 64}
    invoke(s, 'preview_native_rework', wrong_mapping, denied=True)
    wrong_ref = resolve_args(s, f); wrong_ref['native_feedback_ref'] = {**f['feedback_ref'], 'sha256': '0' * 64}
    invoke(s, 'resolve_native_feedback', wrong_ref, denied=True)
    other = feedback(s, op='other-file', path=s['b_path'])
    invoke(s, 'resolve_native_feedback', resolve_args(s, other, {'kind': 'FILE'}), denied=True)
    stale = {'request': {**s['binding']['request'], 'prompt': '另一个原问题'}}
    invoke(s, 'resolve_native_feedback', resolve_args(s, f, {'kind': 'FILE'}), denied=True, binding=stale)
    context = invoke(s, 'read_rework_context', {})
    check(len(canonical(context)) <= 48000)
