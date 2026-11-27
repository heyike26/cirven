"""Fresh Main token restart: real durable receipts, no SDK/model/Windows claims."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from packages.domain.desktop_intents import DesktopIntents
from packages.domain.native_task_authority import NativeTaskAuthority
from packages.domain.local_workspaces import canonical, digest
from packages.domain.store import Conflict, NotFound
from test_native_collaboration import host, TOKEN

NEW_TOKEN = 'b' * 64
EXECUTION = {'project_id': 'historical-project', 'execution_id': 'historical-execution',
    'request_sha256': 'c' * 64, 'task_ref': None}


def owner(root, token):
    base, native = host(root, token=token)
    authority = NativeTaskAuthority(base, context_reader=lambda _: None)
    return base, DesktopIntents(authority)


def test_new_host_token_restart_uses_history_only_and_never_adopts_old_grant(tmp_path):
    original_base, original = owner(tmp_path, TOKEN)
    saved = original.record_message({'operation_id': 'old-message', 'raw_text': '旧说明，不得重放'}, host_token=TOKEN)
    old_head = original._path('head.json').read_bytes()
    base, restarted = owner(tmp_path, NEW_TOKEN)
    assert restarted.get()['owner'] == 'USER'
    assert restarted.get()['active_grant_ref'] is None
    assert restarted.get()['model_calls'] == 0 and not restarted.get()['can_takeover']
    assert restarted.get()['intent_epoch'] == original.get()['intent_epoch'] + 1
    with pytest.raises(Conflict): restarted.message(saved['message_ref'])
    with pytest.raises(Conflict): original.record_message({'operation_id': 'late', 'raw_text': '不得写'}, host_token=TOKEN)
    assert old_head != restarted._path('head.json').read_bytes()


def test_new_token_active_and_unresolved_history_keeps_zero_dispatch_and_cancel(tmp_path):
    original_base, original = owner(tmp_path, TOKEN)
    grant = original._put('grants', {'execution_ref': EXECUTION, 'generation': original.generation, 'expires_unix_ms': 0})
    original.state['active_grant'] = grant
    original.state['unresolved_execution_refs'] = [EXECUTION]
    original._save('EXPLICIT_NEGATIVE_OLD_UNADMITTED_GRANT_FIXTURE')
    grant_bytes = original._path('grants', grant['id'] + '.json').read_bytes()
    base, native = host(tmp_path, token=NEW_TOKEN)
    calls = []
    authority = NativeTaskAuthority(base, context_reader=lambda _: None)
    transport = type('NoReplay', (), {'inspect': lambda *_: calls.append('inspect') or (_ for _ in ()).throw(Conflict('original source missing')),
        'request_cancel': lambda *_: calls.append('cancel')})()
    restarted = DesktopIntents(authority, transport=transport,
        queue_revoker=lambda *_: calls.append('queue'), cua_adapter=object())
    assert calls == [] and restarted.get()['active_grant_ref'] is None
    assert not restarted.get()['cancellation']['confirmed']
    assert restarted.state['unresolved_execution_refs'] == [EXECUTION]
    assert grant_bytes == original._path('grants', grant['id'] + '.json').read_bytes()
    with pytest.raises(Conflict): restarted._grant(grant)
    result = asyncio.run(restarted.revoke('explicit-new-main-takeover', restarted.state['epoch'], host_token=NEW_TOKEN))
    assert result['cancellation']['status'] == 'UNKNOWN_OUTCOME'
    assert calls == ['inspect']  # Original row validation refused before any cancel.


@pytest.mark.parametrize('part', ['head_sha', 'head_ref', 'state_sha', 'state_ref', 'state_payload', 'active_ref'])
def test_bad_history_ref_or_payload_blocks_startup_without_overwriting_evidence(tmp_path, part):
    base, original = owner(tmp_path, TOKEN)
    pointer = base._read(original._path('head.json'), authority=True)
    state = original._path('states', pointer['state_ref']['id'] + '.json')
    target = original._path('head.json') if part.startswith('head') else state
    wrapper = json.loads(target.read_bytes())
    if part == 'head_sha': wrapper['sha256'] = 'f' * 64
    elif part == 'head_ref': wrapper['payload']['state_ref']['sha256'] = 'f' * 64; wrapper['sha256'] = digest(wrapper['payload'])
    elif part == 'state_sha': wrapper['sha256'] = 'f' * 64
    elif part == 'state_ref': wrapper['payload']['ref']['sha256'] = 'f' * 64; wrapper['sha256'] = digest(wrapper['payload'])
    elif part == 'state_payload': wrapper['payload']['payload']['epoch'] = True; wrapper['sha256'] = digest(wrapper['payload'])
    else:
        original.state['active_grant'] = {'id': 'missing-grant', 'revision': 1, 'sha256': 'f' * 64}
        original._save('BROKEN_HISTORICAL_ACTIVE_POINTER'); target = original._path('head.json')
        wrapper = json.loads(target.read_bytes())
    target.write_text(canonical(wrapper))
    before = {path: path.read_bytes() for path in original._path('head.json').parent.rglob('*.json')}
    with pytest.raises((Conflict, NotFound, ValueError, FileNotFoundError)): owner(tmp_path, NEW_TOKEN)
    assert {path: path.read_bytes() for path in original._path('head.json').parent.rglob('*.json')} == before


def test_startup_history_read_is_not_available_to_runtime_authorization(tmp_path):
    base, original = owner(tmp_path, TOKEN)
    with pytest.raises(Conflict, match='startup'): original._head(history=True)


@pytest.mark.parametrize('payload', [None, {}, {'epoch': True}])
def test_even_valid_old_sha_and_mac_cannot_hide_malformed_history_state(tmp_path, payload):
    base, original = owner(tmp_path, TOKEN)
    ref = original._put('states', payload)
    base._write(original._path('head.json'), {'state_ref': ref})
    before = original._path('head.json').read_bytes()
    with pytest.raises(Conflict): owner(tmp_path, NEW_TOKEN)
    assert original._path('head.json').read_bytes() == before


def test_same_host_bad_mac_is_not_downgraded_to_historical_bytes(tmp_path):
    base, original = owner(tmp_path, TOKEN)
    head = original._path('head.json')
    wrapper = json.loads(head.read_bytes()); wrapper['host_mac'] = 'f' * 64
    head.write_text(canonical(wrapper)); before = head.read_bytes()
    with pytest.raises(Conflict): DesktopIntents(original.ws)
    assert head.read_bytes() == before


def test_historical_head_and_state_must_have_same_writer_host(tmp_path):
    base, original = owner(tmp_path, TOKEN)
    head = original._path('head.json'); pointer = base._read(head, authority=True)
    state = original._path('states', pointer['state_ref']['id'] + '.json')
    wrapper = json.loads(state.read_bytes()); wrapper['host_token_sha256'] = 'f' * 64
    state.write_text(canonical(wrapper)); before = head.read_bytes()
    with pytest.raises(Conflict): owner(tmp_path, NEW_TOKEN)
    assert head.read_bytes() == before


def test_old_head_is_retained_in_explicit_readonly_receipt_and_third_restart_works(tmp_path):
    base, original = owner(tmp_path, TOKEN)
    old_head_wrapper = json.loads(original._path('head.json').read_bytes())
    base2, next_value = owner(tmp_path, NEW_TOKEN)
    history = next_value._get('startup-history', next_value.startup_history_ref)
    assert history['prior_head_wrapper'] == old_head_wrapper
    assert history['authority_adopted'] is False and history['startup_model_calls'] == history['startup_sdk_calls'] == history['startup_cancel_calls'] == 0
    base3, third = owner(tmp_path, 'c' * 64)
    assert third.state['epoch'] == next_value.state['epoch'] + 1
    assert third.get()['active_grant_ref'] is None and not third.get()['historical_authority_adopted']
