"""Exact quotes reuse the real exported-task mapping and scoped rework services.

All task statements and proposed replacements are explicit fixture user inputs;
no model, original CLI or native GUI is invoked.
"""
import pytest

from test_native_rework import call, check, feedback, ref, source
from test_native_rework_producer import export, producer_scene, task_path


@pytest.mark.parametrize('target_changed', [False, True])
def test_quote_maps_actual_task_and_scoped_apply_preserves_human_or_conflicts(producer_scene, target_changed):
    s = producer_scene; exported = export(s); path = task_path(s, exported)
    b_path = task_path(s, exported, 'b'); independent = (s['root'] / b_path).read_bytes()
    original = (s['root'] / path).read_text(); quote = 'A 原已确认要求'
    f = feedback(s, path=path, selection={'kind': 'EXACT_QUOTE', 'quote': quote})
    check(f['payload']['requested_selection'] == {'kind': 'EXACT_QUOTE', 'quote': quote})
    m = call(s, 'POST', '/native-rework/mappings', {'operation_id': 'quote-map',
        'native_feedback_ref': f['feedback_ref'], 'proposal_ref': ref(s['proposal'])})
    check(m['status'] == 'MAPPED' and m['payload']['task_ids'] == ['a'])
    check(m['payload']['mapping_basis'] == 'EXACT_CONFIRMED_TASK_EXPORT_PRODUCER')
    check(m['payload']['locator'] == f['payload']['file_selection'])
    check(not m['payload']['write_authorized'])
    binding = m['payload']['binding_ref']
    preview = call(s, 'POST', '/native-rework/plans', {'operation_id': 'quote-preview',
        'mapping_ref': m['mapping_ref'], 'classification_kind': 'CONSTRAINT', 'classification_origin': 'USER_STATEMENT',
        'rationale': '只提议修改用户选定原句，保留句外人工文字', 'proposed_value': 'A 本次明确修订要求',
        'contracts': [{'task_id': 'a', 'inputs': [{'binding_ref': binding,
            'input_value_sha256': m['payload']['selected_value_sha256'], 'condition': 'VALUE_CHANGED',
            'verification_note': '实际导出记录关联 A，不推断独立 B 已验收', 'verification_refs': [binding]}]}]})
    check(preview['status'] == 'PROPOSED' and not preview['payload']['write_authorized'])
    check((s['root'] / path).read_text() == original)
    human = '人写前言😀\r\n' + original + '\n人工后记必须保留。\n'
    if target_changed:
        human = human.replace(quote, '人工已经重写目标句')
    (s['root'] / path).write_bytes(human.encode('utf-8'))
    before = source(s, path)
    body = {'operation_id': 'quote-apply', 'plan_ref': preview['plan_ref'],
        'expected_current_sources': [before], 'allow_rebase': True}
    result = call(s, 'POST', '/native-rework/plans/' + preview['plan_ref']['id'] + '/apply', body,
        codes=(409,) if target_changed else (200,))
    if target_changed:
        check(result['status'] == 'CONFLICT', str(result))
        check((s['root'] / path).read_bytes() == human.encode('utf-8'))
    else:
        check(result['status'] == 'APPLIED')
        check((s['root'] / path).read_bytes() == human.replace(quote, 'A 本次明确修订要求').encode('utf-8'))
        replay = call(s, 'POST', '/native-rework/plans/' + preview['plan_ref']['id'] + '/apply', body, codes=(200,))
        check(replay == result)
    check((s['root'] / b_path).read_bytes() == independent)
    check(call(s, 'GET', '/planning/proposals/' + s['proposal']['id'])['sha256'] == s['proposal']['sha256'])


def test_missing_and_ambiguous_quote_do_not_infer_whole_export_or_mapping(producer_scene):
    s = producer_scene; exported = export(s); path = task_path(s, exported)
    original = (s['root'] / path).read_bytes()
    # Both queries target an actual registered task file, which would otherwise
    # have a verified FILE producer. An unresolved quote must not select it.
    for op, quote, expected in [('missing', '确实不存在的选句', 'QUOTE_NOT_FOUND'),
                                ('ambiguous', 'A', 'QUOTE_AMBIGUOUS')]:
        f = feedback(s, op='quote-' + op, path=path, selection={'kind': 'EXACT_QUOTE', 'quote': quote})
        check(f['payload']['selection_verification'] == expected and f['payload']['file_selection'] is None)
        mapped = call(s, 'POST', '/native-rework/mappings', {'operation_id': 'map-' + op,
            'native_feedback_ref': f['feedback_ref'], 'proposal_ref': ref(s['proposal']),
            'task_ids': ['a'], 'user_confirmed': True})
        check(mapped['status'] == 'NEEDS_CLARIFICATION' and not mapped['payload']['write_authorized'])
    check((s['root'] / path).read_bytes() == original)
