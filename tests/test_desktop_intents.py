"""Real records, original-loop interpretation and negative native admission.

Only the external model wire is replaced. No native window, driver, session or
qualified capability is manufactured. Positive desktop input remains Root GUI.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sqlite3
from pathlib import Path
import threading

import pytest
from pydantic import ValidationError

from packages.domain.desktop_intents import (DesktopIntents, DesktopMessageInput,
    DesktopIntentCandidate, DesktopInputRequest, DesktopTaskVerification, INTENT_PROMPT_PREFIX)
from packages.domain.local_workspaces import canonical, digest, sha
from packages.domain.store import Conflict, NotFound
from packages.domain.workspace_desktop import WorkspaceDesktop
from test_opencode_planning import scene, transport, KEY, MODEL, CLI, CLI_SHA


TOKEN = 'a' * 64
REF = {'id': 'f' * 32, 'revision': 1, 'sha256': 'b' * 64}


def service(tmp_path, **kwargs):
    return DesktopIntents(WorkspaceDesktop(tmp_path, token=TOKEN, owner_pid=123, platform='posix'), **kwargs)


def record(value, text='  当前页面是什么？\r\n', op='raw-message', context=None):
    return value.record_message({'operation_id': op, 'raw_text': text, 'context_ref': context}, host_token=TOKEN)


def test_default_user_sdk_import_or_declared_caps_are_not_input_qualification(tmp_path):
    value = service(tmp_path)
    assert value.lock is value.ws.lock
    state = value.get()
    assert state['owner'] == 'USER' and state['model_calls'] == 0 and state['active_grant_ref'] is None
    assert not state['can_takeover'] and not state['capabilities']['single_task_input_ready']
    assert state['capabilities']['direct_file_cas_requires_task_grant'] is False
    claimed = service(tmp_path / 'sdk-only', capability_reader=lambda: {
        'desktop_input_routes_qualified': True, 'owned_queue_drain_qualified': True})
    assert not claimed.get()['capabilities']['single_task_input_ready']
    assert not any(k in canonical(state) for k in ['hwnd', 'owner_pid', 'marker_name'])


def test_private_main_message_keeps_exact_utf8_whitespace_and_idempotent_bytes(tmp_path):
    value = service(tmp_path); text = '  打开 VSCode 左栏 E09.md。\r\n中文🙂\t'
    first = record(value, text)
    assert first['raw_text_sha256'] == sha(text.encode('utf-8'))
    assert not first['mutation_authority'] and first['interaction_mode'] == 'NONE'
    saved = value.message(first['message_ref'])
    assert saved['raw_text'] == text and saved['origin'] == 'RIGHT_AGENT_COMPOSER' and saved['role'] == 'user'
    assert first['message_ref']['sha256'] == digest(saved)
    replay = record(value, text); assert replay['replayed'] and replay['message_ref'] == first['message_ref']
    with pytest.raises(Conflict): record(value, text.strip())
    assert len(list(value._path('messages').glob('*.json'))) == 1


def test_untrusted_message_cannot_create_raw_authorization_or_native_fields(tmp_path):
    value = service(tmp_path)
    with pytest.raises(Conflict): value.record_message({'operation_id': 'forged', 'raw_text': '打开VSCode'}, host_token='b' * 64)
    assert not value._path('messages').exists()
    for extra in [{'user_requested_write': True}, {'owner': 'AGENT'}, {'hwnd': '123'}, {'authorization_ref': REF}]:
        with pytest.raises(ValidationError): DesktopMessageInput.model_validate({'operation_id': 'message', 'raw_text': '打开VSCode'} | extra)


def test_concurrent_same_original_message_has_one_immutable_receipt(tmp_path):
    value = service(tmp_path); barrier = threading.Barrier(8)
    def actor(_index):
        barrier.wait(); return record(value, '  原创用户消息🙂\r\n')
    with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(actor, range(8)))
    assert len({r['message_ref']['id'] for r in results}) == 1
    assert sum(not r['replayed'] for r in results) == 1
    assert len(list(value._path('messages').glob('*.json'))) == 1


def test_native_unavailable_action_binding_keeps_user_and_does_not_read_fake_execution(tmp_path):
    value = service(tmp_path)
    execution = {'project_id': 'internal-desktop', 'execution_id': 'no-execution-created', 'request_sha256': 'c' * 64}
    result = value.bind_execution('action', REF, execution, 1, host_token=TOKEN)
    assert result['status'] == 'UNAVAILABLE' and result['grant_ref'] is None and result['owner'] == 'USER'
    assert result['mutation_authority'] is False and value.get()['intent_epoch'] == 1
    assert value.bind_execution('action', REF, execution, 1, host_token=TOKEN)['replayed']
    with pytest.raises(Conflict): value.bind_execution('other', REF, execution, True, host_token=TOKEN)
    assert not value._path('grants').exists()


def test_user_revocation_is_cas_idempotent_and_no_model_or_unrelated_cancel(tmp_path):
    value = service(tmp_path)
    first = asyncio.run(value.revoke('take', 1, host_token=TOKEN))
    assert first['owner'] == 'USER' and first['intent_epoch'] == 2 and not first['mutation_authority']
    assert first['cancellation']['confirmed'] and first['cancellation']['outcomes'] == []
    replay = asyncio.run(value.revoke('take', 1, host_token=TOKEN))
    assert replay['replayed'] and replay['cancellation'] == first['cancellation']
    with pytest.raises(Conflict): asyncio.run(value.revoke('late', 1, host_token=TOKEN))
    assert value.get()['model_calls'] == 0


def test_concurrent_takeover_has_one_epoch_winner(tmp_path):
    value = service(tmp_path); barrier = threading.Barrier(8)
    def actor(index):
        barrier.wait()
        try: return asyncio.run(value.revoke('take-' + str(index), 1, host_token=TOKEN))
        except Conflict: return None
    with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(actor, range(8)))
    assert sum(result is not None for result in results) == 1
    assert value.get()['intent_epoch'] == 2 and value.get()['owner'] == 'USER'


def test_restart_preserves_original_message_but_invalidates_controller_lifetime(tmp_path):
    original = service(tmp_path); saved = record(original)
    restarted = DesktopIntents(original.ws)
    assert restarted.get()['owner'] == 'USER' and restarted.get()['intent_epoch'] == 2
    assert restarted.message(saved['message_ref']) == original.message(saved['message_ref'])
    assert record(restarted)['message_ref'] == saved['message_ref']
    with pytest.raises(Conflict, match='lifetime'): record(original, op='late-controller')
    assert not restarted.get()['can_takeover']


def test_tampering_sha_without_current_host_mac_does_not_reauthorize(tmp_path):
    value = service(tmp_path); saved = record(value)
    path = value._path('messages', saved['message_ref']['id'] + '.json')
    wrapper = json.loads(path.read_bytes()); wrapper['payload']['payload']['raw_text'] = '伪造命令'
    wrapper['sha256'] = digest(wrapper['payload']); path.write_text(canonical(wrapper))
    with pytest.raises(Conflict, match='integrity'): value.message(saved['message_ref'])
    head = value._path('head.json'); wrapper = json.loads(head.read_bytes())
    wrapper['payload']['state_ref']['sha256'] = 'f' * 64; wrapper['sha256'] = digest(wrapper['payload'])
    head.write_text(canonical(wrapper))
    with pytest.raises(Conflict, match='integrity'): DesktopIntents(value.ws)


def test_ref_mismatch_unknown_id_and_path_escape_are_rejected(tmp_path):
    value = service(tmp_path); saved = record(value)
    with pytest.raises(Conflict): value.message(saved['message_ref'] | {'sha256': 'c' * 64})
    with pytest.raises(NotFound): value.message(REF)
    with pytest.raises(ValidationError): value.message(REF | {'id': '../outside'})


@pytest.mark.parametrize('candidate', [
    {'intent': 'ACTION', 'goal': '打开文件', 'allowed_effects': ['CLICK']},
    {'intent': 'OBSERVE', 'goal': '查看页面', 'allowed_effects': ['CLICK']},
    {'intent': 'UNKNOWN', 'goal': '目标未知', 'allowed_effects': ['TYPE_TEXT']},
    {'intent': 'ACTION', 'goal': '打开文件', 'instruction_span': {'start': True, 'end': 4, 'quote': '打开文件'}, 'allowed_effects': ['CLICK']},
    {'intent': 'ACTION', 'goal': '打开文件', 'instruction_span': {'start': 0, 'end': 4, 'quote': '打开文件'}, 'allowed_effects': ['SHELL']},
    {'intent': 'OBSERVE', 'goal': '查看页面', 'user_authorized': True},
])
def test_untrusted_interpretation_cannot_self_authorize_or_expand_input_methods(candidate):
    with pytest.raises(ValidationError): DesktopIntentCandidate.model_validate(candidate)


@pytest.mark.parametrize('action', [
    {'kind': 'SHELL', 'command': 'start code'},
    {'kind': 'CLICK', 'x': True, 'y': 1, 'delivery_mode': 'foreground'},
    {'kind': 'CLICK', 'x': float('nan'), 'y': 1, 'delivery_mode': 'foreground'},
    {'kind': 'TYPE_TEXT', 'text': 'x', 'target_hwnd': '123'},
    {'kind': 'PRESS_KEY', 'key': 'CTRL+DELETE'},
])
def test_per_input_exact_schema_has_no_unbounded_shell_or_target_override(action):
    with pytest.raises(ValidationError): DesktopInputRequest.model_validate({
        'operation_id': 'input', 'grant_ref': REF, 'session_ref': REF, 'target_ref': REF, 'frame_ref': REF, 'action': action})


def test_expired_unverified_negative_record_never_admits_input_and_restart_retains_cancel_gap(tmp_path):
    value = service(tmp_path)
    execution = {'project_id': 'negative-history', 'execution_id': 'no-native-session-created', 'request_sha256': 'c' * 64, 'task_ref': None}
    # Explicitly invalid fixture: not an admitted grant, current native scope,
    # original execution, or positive platform qualification.
    ref = value._put('grants', {'execution_ref': execution, 'generation': value.generation, 'expires_unix_ms': 0})
    value.state['active_grant'] = ref; value._save('NEGATIVE_EXPIRED_UNVERIFIED_RECORD')
    with pytest.raises(Conflict, match='expired'): value._grant(ref)
    assert value.get()['owner'] == 'USER' and value.get()['can_takeover']
    restarted = DesktopIntents(value.ws)
    assert restarted.get()['active_grant_ref'] is None and restarted.get()['cancellation']['status'] == 'UNKNOWN_OUTCOME'
    assert not restarted.get()['cancellation']['confirmed'] and restarted.get()['can_takeover']
    revoked = asyncio.run(restarted.revoke('retry-cancel', 2, host_token=TOKEN))
    assert revoked['cancellation']['status'] == 'UNKNOWN_OUTCOME'
    assert revoked['cancellation']['queued_inputs']['status'] == 'UNKNOWN_OUTCOME'
    assert revoked['cancellation']['outcomes'][0]['status'] == 'FAILED' and not revoked['cancellation']['confirmed']


def test_scope_envelope_keeps_sealed_control_abi_and_rejects_partial_mixed_source_refs():
    scope = {'surface_ref': REF, 'input_epoch': 1, 'task_ref': None, 'target_ref': REF,
        'context_ref': REF, 'frame_ref': None, 'authorization_ref': REF, 'user_message_ref': REF}
    legacy = {name: scope[name] for name in ['surface_ref', 'input_epoch', 'task_ref']}
    assert DesktopIntents._scope_matches({'desktop_scope': scope}, scope)
    assert DesktopIntents._scope_matches({'desktop_scope': legacy, 'desktop_intent_scope': scope}, scope)
    assert not DesktopIntents._scope_matches({'desktop_scope': legacy}, scope)
    assert not DesktopIntents._scope_matches({'desktop_scope': scope, 'desktop_intent_scope': scope}, scope)
    assert not DesktopIntents._scope_matches({'desktop_scope': legacy | {'input_epoch': 2}, 'desktop_intent_scope': scope}, scope)
    assert not DesktopIntents._scope_matches({'desktop_scope': legacy | {'input_epoch': True}, 'desktop_intent_scope': scope}, scope)
    assert not DesktopIntents._scope_matches({'desktop_scope': legacy, 'desktop_intent_scope': scope | {'input_epoch': True}}, scope)
    assert not DesktopIntents._scope_matches({'desktop_scope': {}}, {})


def test_unknown_or_corrupt_input_ref_never_falls_through_into_another_authority(tmp_path):
    value = service(tmp_path)
    with pytest.raises(Conflict):
        with value.composite_dispatch_guard(REF, REF, 1, 1): pytest.fail('Unowned input must not be admitted')
    known = value._put('inputs', {'negative_record': 'never-dispatched'})
    with pytest.raises(Conflict, match='reference'):
        with value.composite_dispatch_guard(known | {'sha256': 'e' * 64}, REF, 1, 1):
            pytest.fail('Corrupt permit must not fall through to a long-control lease')


@pytest.mark.parametrize('status', ['DISPATCHING', 'UNKNOWN_OUTCOME'])
def test_interrupted_input_is_not_resent_or_guessed_success_on_restart(tmp_path, status):
    value = service(tmp_path)
    # Invalid native source fixture only; execute_input must fail before ever
    # reading an adapter or admitting a scope. No input or native grant exists.
    ref = value._put('inputs', {'negative_record': 'receipt-lost-before-qualification'})
    value.ws._write(value._path('input-states', ref['id'] + '.json'), {'status': status, 'input_ref': ref})
    restarted = DesktopIntents(value.ws)
    with pytest.raises(Conflict, match='never automatically resend'): restarted.execute_input(ref)
    assert restarted.get()['owner'] == 'USER' and restarted.get()['active_grant_ref'] is None


def test_source_checker_rejects_unknown_execution_scope_and_native_unavailability(tmp_path):
    value = service(tmp_path)
    request = {'desktop_scope': {'surface_ref': REF, 'input_epoch': 1, 'task_ref': None}}
    row = {'request': canonical(request), 'request_hash': digest(request), 'execution_status': 'RUNNING'}
    assert not value.check_sources(row, request['desktop_scope'])
    assert not value.check_sources(row | {'request_hash': 'f' * 64}, request['desktop_scope'])
    assert not value.check_sources(row | {'execution_status': 'RETURNED'}, request['desktop_scope'])
    assert not value.check_grant(row, request['desktop_scope'], REF)


def test_sdk_return_or_model_success_boolean_cannot_verify_user_goal_without_actual_evidence():
    with pytest.raises(ValidationError): DesktopTaskVerification.model_validate({'status': 'VERIFIED', 'reason': '模型说已完成'})
    with pytest.raises(ValidationError): DesktopTaskVerification.model_validate({'status': 'VERIFIED', 'reason': 'SDK返回成功',
        'evidence_refs': [REF], 'sdk_is_error': False})
    assert DesktopTaskVerification.model_validate({'status': 'UNKNOWN', 'reason': '没有实际结果检查器'}).status == 'UNKNOWN'


def test_no_task_grant_finish_does_not_adopt_execution_or_call_result_checker(tmp_path):
    value = service(tmp_path)
    result = asyncio.run(value.finish({'project_id': 'ordinary-file', 'execution_id': 'not-owned-desktop-task',
        'request_sha256': 'c' * 64}, host_token=TOKEN))
    assert result == {'status': 'NO_TASK_GRANT', 'owner': 'USER', 'mutation_authority': False}
    assert value.get()['model_calls'] == 0 and value.get()['intent_epoch'] == 1


class IntentModelWire:
    """Only external provider HTTP; actual original CLI parses StructuredOutput."""
    def __init__(self):
        self.requests = []; owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append({'body': body, 'auth_matches': self.headers.get('Authorization') == 'Bearer ' + KEY})
                contents = [m['content'] for m in body['messages'] if m['role'] == 'user']
                joined = '\n'.join(c if isinstance(c, str) else '\n'.join(p.get('text', '') for p in c) for c in contents)
                envelope = json.loads(joined.split(INTENT_PROMPT_PREFIX, 1)[1].split('\n', 1)[0])
                raw = envelope['raw_text']; observe = '？' in raw
                output = {'intent': 'OBSERVE' if observe else 'ACTION', 'goal': '查看当前页面' if observe else '打开明确指定文件',
                    'instruction_span': None if observe else {'start': len(raw) - len(raw.lstrip()),
                        'end': len(raw.rstrip()), 'quote': raw.strip()},
                    'allowed_effects': [] if observe else ['CLICK'], 'question': None}
                events = [{'id': 'chatcmpl-original-intent', 'object': 'chat.completion.chunk', 'created': 0, 'model': MODEL,
                    'choices': [{'index': 0, 'delta': {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_intent',
                        'type': 'function', 'function': {'name': 'StructuredOutput', 'arguments': canonical(output)}}]}, 'finish_reason': None}]},
                    {'id': 'chatcmpl-original-intent', 'object': 'chat.completion.chunk', 'created': 0, 'model': MODEL,
                        'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}],
                        'usage': {'prompt_tokens': 25, 'completion_tokens': 45, 'total_tokens': 70}}]
                response = (''.join('data: ' + canonical(e) + '\n\n' for e in events) + 'data: [DONE]\n\n').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(response))); self.end_headers(); self.wfile.write(response)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def start(self): self.thread.start(); return self
    @property
    def base_url(self): return f'http://127.0.0.1:{self.server.server_port}/v1'
    def close(self): self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)


def test_actual_original_loop_observation_and_clear_command_do_not_grant_unqualified_desktop_input(scene, tmp_path):
    assert sha(CLI.read_bytes()) == CLI_SHA[os.name]
    wire = IntentModelWire().start(); native, request, _ = transport(scene, wire)
    value = service(tmp_path / 'desktop-intent-real-records', transport=native)
    evidence = {'qualification': 'ACTUAL_ORIGINAL_CLI_EXTERNAL_MODEL_WIRE_ONLY',
        'native_input': 'NOT_RUN_NO_NATIVE_GRANT', 'cases': [], 'paid_model_calls': 0}
    try:
        for index, text in enumerate(['  当前页面是什么？\r\n', '  打开 VSCode 左栏 E09.md。\r\n']):
            message = record(value, text, 'original-user-' + str(index))
            actual_request = request | {'operation_id': 'original-intent-' + str(index),
                'prompt': value.interpretation_prompt(message['message_ref']),
                'output_schema': DesktopIntentCandidate.model_json_schema()}
            result = asyncio.run(native.propose(actual_request))
            assert result['status'] == 'RETURNED' and result['usage']['rpc_attempts'] == 1
            row = native.inspect(scene['pid'], result['execution_id'])
            execution_ref = {'project_id': scene['pid'], 'execution_id': row['id'], 'request_sha256': row['request_hash']}
            assert row['request_hash'] == digest(json.loads(row['request']))
            interpreted = value.interpret('interpret-' + str(index), message['message_ref'], execution_ref, host_token=TOKEN)
            assert interpreted['intent'] == ('OBSERVE' if index == 0 else 'ACTION') and not interpreted['mutation_authority']
            authorization = value.authorize('authorize-' + str(index), interpreted['interpretation_ref'], host_token=TOKEN)
            assert authorization['status'] == ('OBSERVE' if index == 0 else 'NEEDS_INPUT')
            assert authorization['interaction_mode'] == ('NONE' if index == 0 else 'INLINE_QUESTION')
            assert authorization['owner'] == 'USER' and not authorization['mutation_authority']
            assert value.authorize('authorize-' + str(index), interpreted['interpretation_ref'], host_token=TOKEN)['replayed']
            assert value.message(message['message_ref'])['raw_text'] == text
            assert value.get()['active_grant_ref'] is None and not value.get()['capabilities']['single_task_input_ready']
            evidence['cases'].append({'message': message, 'execution_ref': execution_ref, 'interpreted': interpreted,
                'authorization': authorization, 'native_result': result, 'native_request': json.loads(row['request'])})
            if index == 1:
                with pytest.raises(Conflict): value.interpret('wrong-message', evidence['cases'][0]['message']['message_ref'], execution_ref, host_token=TOKEN)
                with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                    with scene['service'].connect() as db:
                        db.execute('UPDATE agent_executions SET request=? WHERE id=?', (canonical(json.loads(row['request']) | {'prompt': 'tampered original request'}), row['id']))
                assert native.inspect(scene['pid'], row['id'])['request'] == row['request']
                with pytest.raises(Conflict, match='identity'):
                    value.interpret('changed-source', message['message_ref'], execution_ref | {'request_sha256': 'e' * 64}, host_token=TOKEN)
        assert len(wire.requests) == 2 and all(r['auth_matches'] for r in wire.requests)
        assert scene['file'].read_bytes() == scene['original']
        assert all([t['function']['name'] for t in r['body']['tools']] == ['StructuredOutput'] for r in wire.requests)
        with scene['service'].connect() as db:
            assert db.execute('SELECT count(*) FROM task_runs').fetchone()[0] == 0
        assert not value._path('inputs').exists() and not value._path('grants').exists()
    finally:
        evidence['wire_requests'] = wire.requests
        destination = os.environ.get('EEAS_DESKTOP_INTENT_EVIDENCE')
        if destination:
            directory = Path(destination); directory.mkdir(parents=True, exist_ok=True)
            (directory / 'original-interpretation-receipt.json').write_text(canonical(evidence).replace(KEY, '<fixture-credential-redacted>'))
            for index, case in enumerate(evidence['cases']):
                row = native.inspect(scene['pid'], case['execution_ref']['execution_id'])
                output = directory / ('native-' + str(index)); output.mkdir(exist_ok=True)
                for name in ['http-starts.jsonl', 'http-responses.jsonl', 'native-events.jsonl', 'planning-native-result.json']:
                    path = Path(row['workspace']) / name
                    if path.is_file(): (output / name).write_bytes(path.read_bytes().replace(KEY.encode(), b'<fixture-credential-redacted>'))
        asyncio.run(native.close()); wire.close()
