"""No credentials/model: exact saved rejection and account metadata regressions."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from packages.agent_sessions.outcomes import original_auth_rejection
from packages.adapters.opencode_account import (OpenCodeAccount, read_subscription_access,
    read_subscription_verification, record_subscription_rejection, record_subscription_response)
from packages.domain.store import Conflict
from packages.domain.tasks import canonical
from test_subscription_verification import setup, case


def failure(v, stamp=1792000001000):
    source, messages = case(v)
    source['status'] = 'FAILED'
    operation = {'id': source['operation_id'], 'kind': 'MESSAGE', 'status': 'FAILED',
        'request_sha256': source['request_sha256'],
        'generation': source['generation'], 'session_ref': source['object_ref'],
        'native_post_attempted': True, 'native_dispatch_acknowledged': True,
        'native_message_id': 'msg_unituser', 'original_session_id': source['original_session_id'],
        'native_assistant_ids': source['native_assistant_ids'],
        'deadline': datetime.fromtimestamp(source['deadline_ms']/1000, timezone.utc).isoformat(),
        'request': {'raw_text': messages[0]['parts'][0]['text']}}
    assistant = messages[1]
    assistant['parts'] = []
    assistant['info'].update(finish=None, time={'completed': stamp}, error={'name': 'APIError',
        'data': {'statusCode': 401, 'message': 'PRIVATE_ERROR_CONTENT',
                 'responseBody': 'PRIVATE_BODY', 'responseHeaders': {'set-cookie': 'PRIVATE_COOKIE'}}})
    return source, operation, messages


def test_exact_401_proof_contains_no_error_content_or_native_headers(setup):
    _, operation, messages = failure(setup)
    proof = original_auth_rejection(operation, messages)
    assert proof['code'] == 'MODEL_AUTH_REQUIRED'
    assert proof['native_user_messages'] == case(setup)[0]['native_user_messages']
    assert 'PRIVATE_' not in canonical(proof)
    assert not (setup['profile'].account_data_home/'opencode/auth.json').exists()


@pytest.mark.parametrize('corruption', [None, 'origin', 'context', 'unavailable'])
def test_saved_authored_display_requires_exact_wire_operation_context_origin(setup, corruption):
    _, op, messages = failure(setup)
    wire = 'INTERNAL_CONTEXT\n' + op['request']['raw_text']
    op['conversation_context_sha256'] = 'b'*64
    op['host_payload'] = {'text_parts': [wire], 'conversation_context_sha256': 'b'*64}
    messages[0]['conversation_input_origin'] = {'operation_id': op['id'],
        'request_sha256': op['request_sha256'], 'context_sha256': 'b'*64}
    if corruption == 'origin': messages[0]['conversation_input_origin']['operation_id'] = 'other'
    if corruption == 'context': op['host_payload']['conversation_context_sha256'] = 'c'*64
    if corruption == 'unavailable': messages[0]['conversation_input_unavailable'] = True
    proof = original_auth_rejection(op, messages)
    if corruption is None:
        import hashlib
        assert proof['native_user_messages'] == {'msg_unituser': hashlib.sha256(wire.encode()).hexdigest()}
    else:
        assert proof is None


@pytest.mark.parametrize('change', ['partial_text', 'tool', 'other_parent', 'other_session',
    'missing_user', 'changed_user', 'unselected_assistant', 'unfinished', 'retryable_failure',
    'not_acknowledged', 'not_posted', 'another_selected', 'duplicate', 'after_deadline', 'bad_payload'])
def test_incomplete_or_effectful_outcome_stays_unknown(setup, change):
    _, op, messages = failure(setup)
    assistant = messages[1]
    if change == 'partial_text': assistant['parts'] = [{'type': 'text', 'text': 'partial'}]
    elif change == 'tool': assistant['parts'] = [{'type': 'tool', 'state': {'status': 'completed'}}]
    elif change == 'other_parent': assistant['info']['parentID'] = 'msg_foreign'
    elif change == 'other_session': assistant['info']['sessionID'] = 'ses_foreign'
    elif change == 'missing_user': messages.pop(0)
    elif change == 'changed_user': messages[0]['parts'][0]['text'] += 'changed'
    elif change == 'unselected_assistant':
        old = deepcopy(assistant); old['info']['id'] = 'msg_old'; messages.append(old)
    elif change == 'unfinished': assistant['info']['time'] = {}
    elif change == 'retryable_failure': assistant['info']['error']['data']['statusCode'] = 429
    elif change == 'not_acknowledged': op['native_dispatch_acknowledged'] = False
    elif change == 'not_posted': op['native_post_attempted'] = False
    elif change == 'another_selected': op['native_assistant_ids'] = ['msg_foreign']
    elif change == 'duplicate': messages.append(deepcopy(assistant))
    elif change == 'after_deadline': assistant['info']['time']['completed'] = 3000000000000
    elif change == 'bad_payload': op['host_payload'] = {'text_parts': [None]}
    assert original_auth_rejection(op, messages) is None


def test_old_success_cannot_hide_new_auth_rejection_and_cold_read_preserves_it(setup):
    v = setup; profile = v['profile']; success, messages = case(v)
    record_subscription_response(profile, success, messages)
    old = read_subscription_verification(profile)
    assert v['account']._public()['model_access'] == 'VERIFIED'
    source, op, messages = failure(v)
    assert record_subscription_rejection(profile, source, op, messages)['access_status'] == 'REAUTH_REQUIRED'
    restored = OpenCodeAccount(v['providers'], v['data'])
    public = restored._public()
    assert public['phase'] == 'REAUTH_REQUIRED' and public['model_access'] == 'NOT_VALIDATED'
    assert public['profile'] == v['saved']
    assert read_subscription_verification(profile) == old
    marker = v['account'].root/'access-observation.json'; before = marker.stat().st_mtime_ns
    for _ in range(5):
        record_subscription_rejection(profile, source, op, messages)
        assert restored._public()['phase'] == 'REAUTH_REQUIRED'
    assert marker.stat().st_mtime_ns == before
    assert 'PRIVATE_' not in marker.read_text()
    assert not (profile.account_data_home/'opencode/auth.json').exists()


def test_out_of_order_success_and_old_epoch_cannot_overwrite_latest_access(setup):
    v = setup; p = v['profile']; source, op, messages = failure(v)
    record_subscription_rejection(p, source, op, messages)
    success, old = case(v)
    record_subscription_response(p, success, old)
    assert v['account']._public()['phase'] == 'REAUTH_REQUIRED'
    success['operation_id'] = 'newer-success'
    old[1]['info']['time']['completed'] = 1792000002000
    record_subscription_response(p, success, old)
    assert read_subscription_access(p)['status'] == 'AVAILABLE'
    record_subscription_rejection(p, source, op, messages)
    assert read_subscription_access(p)['status'] == 'AVAILABLE'
    v['account']._save(authorization_epoch='f'*32)
    fresh = replace(p, authorization_epoch='f'*32)
    assert read_subscription_access(fresh) is None
    with pytest.raises(Conflict): record_subscription_rejection(p, source, op, messages)
    assert v['account']._public()['model_access'] == 'NOT_VALIDATED'


def test_unrelated_or_partial_error_cannot_invalidate_account(setup):
    v = setup; source, op, messages = failure(v)
    messages[1]['parts'] = [{'type': 'text', 'text': 'partial'}]
    assert record_subscription_rejection(v['profile'], source, op, messages) == {'status': 'NOT_RECORDED'}
    assert read_subscription_access(v['profile']) is None
    source, op, messages = failure(v); source['native_user_messages'] = {'msg_other': 'a'*64}
    with pytest.raises(Conflict): record_subscription_rejection(v['profile'], source, op, messages)
    assert read_subscription_access(v['profile']) is None
