"""Real saved sources and private callbacks; no model or original SDK launch.

Only the host observation supplies an omitted contract's immutable source
identity.  It cannot supply business dependency or acceptance authority.
"""
import copy

import pytest

from packages.domain.feedback_rework import TaskContract
from packages.domain.store import Conflict, InvalidInput
from test_native_messages_quote_scope_source71 import private_scene, resolve_args, preview_args
from test_native_rework_producer import producer_scene, export
from test_native_rework import call, check, feedback, ref, source


def mapped(s):
    f = feedback(s, path=s['path'], selection={'kind': 'EXACT_QUOTE', 'quote': 'A 原已确认要求'})
    result = s['native'].resolve_native_feedback(resolve_args(s, f), s['binding'])
    check(result['status'] == 'MAPPED')
    return result


def arguments(mapping, contracts='missing'):
    body = {'mapping_ref': mapping['mapping_ref'], 'classification_kind': 'CONSTRAINT',
        'rationale': '仅提议已选择原句的新要求；未声明其他任务的依赖或独立性。',
        'proposed_value': 'A 本次限定提议要求'}
    if contracts != 'missing':
        body['contracts'] = contracts
    return body


@pytest.mark.parametrize('contracts', ['missing', []], ids=['missing', 'empty'])
def test_missing_or_empty_contracts_propose_only_conservative_mapped_source(private_scene, contracts):
    s = private_scene; m = mapped(s); body = arguments(m, contracts); original_body = copy.deepcopy(body)
    plan = s['native'].preview_native_rework(body, s['binding'])
    check(body == original_body, 'Adapter must not mutate original tool arguments')
    check(plan['status'] == 'PROPOSED' and plan['payload']['write_authorized'] is False)
    check(plan['payload']['proposal_ref'] == ref(s['proposal']))
    engine = plan['payload']['engine_plan']['payload']; rows = engine['contracts']
    check(len(rows) == 1 and rows[0]['task_id'] == 'a', 'Never infer a contract for other tasks')
    i = rows[0]['inputs'][0]
    check(i['binding_ref'] == m['payload']['binding_ref'])
    check(i['input_value_sha256'] == m['payload']['selected_value_sha256'])
    check(i['verification_refs'] == [m['payload']['binding_ref']])
    check(i['condition'] == 'MANUAL_REVIEW')
    check(set(i['invalidating_kinds']) == {'EXPRESSION', 'DATA', 'CONSTRAINT', 'UNKNOWN'})
    check('Business dependencies, independence, semantic correctness and acceptance are unconfirmed' in i['verification_note'])
    check({t['task_id']: t['status'] for t in engine['impact']['tasks']} == {'a': 'UNKNOWN', 'b': 'UNKNOWN'})
    check('apply_native_rework' not in s['native'].transport.methods)
    actual = call(s, 'GET', '/native-rework/plans/' + plan['record_ref']['id'])
    check(actual['record_ref'] == plan['record_ref'])
    current = call(s, 'GET', '/planning/proposals/' + s['proposal']['id'])
    check(ref(current) == ref(s['proposal']))


@pytest.mark.parametrize('bad', ['binding', 'hash', 'missing-task'],
                         ids=['foreign-binding', 'wrong-value-hash', 'missing-mapped-task'])
def test_nonempty_explicit_contracts_remain_strict(private_scene, bad):
    s = private_scene; m = mapped(s); body = preview_args(m)
    if bad == 'binding':
        body['contracts'][0]['inputs'][0]['binding_ref']['sha256'] = '0' * 64
    elif bad == 'hash':
        body['contracts'][0]['inputs'][0]['input_value_sha256'] = '0' * 64
    else:
        body['contracts'][0]['task_id'] = 'b'
    before = copy.deepcopy(body)
    with pytest.raises((Conflict, InvalidInput)):
        s['native'].preview_native_rework(body, s['binding'])
    check(body == before)
    check(s['native'].rework.listing(s['pid'], 'PLAN') == [])


@pytest.mark.parametrize('contracts', [None, False, {}], ids=['null', 'false', 'object'])
def test_invalid_contract_types_are_not_repaired(private_scene, contracts):
    s = private_scene; m = mapped(s); body = arguments(m, contracts)
    with pytest.raises(ValueError):
        s['native'].preview_native_rework(body, s['binding'])
    check(s['native'].rework.listing(s['pid'], 'PLAN') == [])


def test_existing_explicit_contract_is_not_replaced(private_scene):
    s = private_scene; m = mapped(s); body = preview_args(m)
    before = copy.deepcopy(body['contracts'])
    expected = [TaskContract.model_validate(row).model_dump() for row in before]
    plan = s['native'].preview_native_rework(body, s['binding'])
    actual = plan['payload']['engine_plan']['payload']['contracts']
    check(actual == expected)
    item = actual[0]['inputs'][0]
    check(item['condition'] == 'VALUE_CHANGED')
    for key in ('binding_ref', 'input_value_sha256', 'verification_note', 'verification_refs'):
        check(item[key] == before[0]['inputs'][0][key])
    check(body['contracts'] == before)
    check(plan['payload']['write_authorized'] is False)


def test_unresolved_mapping_and_quote_widening_are_not_defaulted(private_scene):
    s = private_scene
    # A second real export makes the producer relationship genuinely ambiguous.
    export(s, 'second-distinct-export')
    f = feedback(s, path=s['path'], selection={'kind': 'EXACT_QUOTE', 'quote': 'A 原已确认要求'})
    m = s['native'].resolve_native_feedback(resolve_args(s, f), s['binding'])
    check(m['status'] == 'NEEDS_CLARIFICATION')
    with pytest.raises(Conflict):
        s['native'].preview_native_rework(arguments(m), s['binding'])
    with pytest.raises(Conflict):
        s['native'].resolve_native_feedback(resolve_args(s, f, {'kind': 'FILE'}), s['binding'])
    check(s['native'].rework.listing(s['pid'], 'PLAN') == [])


def test_default_preview_does_not_bypass_changed_human_target_conflict(private_scene):
    s = private_scene; m = mapped(s)
    plan = s['native'].preview_native_rework(arguments(m), s['binding'])
    check((s['root'] / s['path']).read_bytes() == s['original'], 'Preview itself must never write')
    current = source(s, s['path'])
    human = s['original'].decode().replace('A 原已确认要求', 'A 人工已修改要求') + '\n人工句外保留🙂\n'
    call(s, 'POST', '/local-workspaces/' + s['wid'] + '/edits', {
        'operation_id': 'explicit-human-edit-after-preview', 'path': s['path'],
        'expected_revision': current['revision'], 'expected_sha256': current['sha256'], 'content': human})
    # The fixture's final invariant now protects this explicit human save.
    s['original'] = human.encode()
    result = call(s, 'POST', '/native-rework/plans/' + plan['record_ref']['id'] + '/apply', {
        'operation_id': 'explicit-apply-stale-target', 'plan_ref': plan['record_ref'],
        'expected_current_sources': [source(s, s['path'])], 'allow_rebase': True}, codes=(409,))
    check(result['status'] == 'CONFLICT')
    check(result['payload']['engine_receipt']['payload']['modified_file_count'] == 0)
    check((s['root'] / s['path']).read_bytes() == human.encode())
    check((s['root'] / s['b_path']).read_bytes() == s['independent'])


def test_descriptor_and_context_explain_nonsemantic_default(private_scene):
    s = private_scene
    descriptor = s['native'].transport.methods['preview_native_rework'][0]
    check(descriptor['readonly'] is True and descriptor['effects'] == [])
    check('MANUAL_REVIEW' in descriptor['input_schema']['properties']['contracts']['description'])
    context = s['native'].read_rework_context({}, s['binding'])
    policy = context['default_preview_contract_policy']
    check(policy['mapped_tasks_only'] is True and policy['condition'] == 'MANUAL_REVIEW')
    check(policy['business_dependencies_confirmed'] is False and policy['other_task_impact'] == 'UNKNOWN')


def test_original_text_tool_proposal_survives_host_default_contract_serialization(private_scene):
    # AUTHORED_NOT_RUN batch59: same real private callback, no SDK/model/native GUI.
    s=private_scene;m=mapped(s);body=arguments(m);body.pop('proposed_value')
    body['proposed_value_text']='9007199254740993 与原句替换文本'
    before=copy.deepcopy(body);plan=s['native'].preview_native_rework(body,s['binding'])
    check(body==before)
    check(plan['payload']['proposal_text']['text']==body['proposed_value_text'])
    check(plan['payload']['proposal_text']['kind']=='UTF8_TEXT')
    check(plan['payload']['proposed_value']==body['proposed_value_text'])
    check(plan['payload']['engine_plan']['payload']['contracts'][0]['inputs'][0]['condition']=='MANUAL_REVIEW')
    check(plan['payload']['write_authorized'] is False)


def test_tool_descriptor_advertises_exclusive_original_text_without_new_write_authority(private_scene):
    # AUTHORED_NOT_RUN batch59.
    descriptor=private_scene['native'].transport.methods['preview_native_rework'][0]
    schema=descriptor['input_schema']
    check(schema['properties']['proposed_value_text']['type']=='string')
    check(len(schema['oneOf'])==2)
    check('proposed_value_text' not in schema['required'] and 'proposed_value' not in schema['required'])
    check('never both' in schema['properties']['proposed_value_text']['description'])
    check(descriptor['readonly'] is True and descriptor['effects']==[])
