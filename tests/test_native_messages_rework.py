"""Original OpenCode/official MCP plus real source/task/rework services.

Only the external model wire and non-Windows host lifecycle are fixtures.
This does not qualify Windows input or model semantic correctness.
"""
import asyncio
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from apps.api.desktop_context import DesktopContextRPC
from apps.api.native_messages import NativeMessages
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.local_workspaces import canonical, digest
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict
from test_native_collaboration import HostLifecycleCounter
from test_native_messages import ROOT, TOKEN
from test_native_rework import scene, source, feedback, mapping, call, ref, check


class ReworkWire:
    def __init__(self, contracts):
        self.calls, self.results = [], []
        self.contracts = contracts
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append(body)
                tools = [m for m in body['messages'] if m['role'] == 'tool']
                step = len(tools)
                if tools:
                    content = tools[-1]['content']
                    if isinstance(content, list): content = '\n'.join(v.get('text', '') for v in content)
                    owner.results.append(json.loads(content))
                name = next(x['function']['name'] for x in body['tools'] if x['function']['name'].endswith('_invoke'))
                if step == 0:
                    args = {'method': 'read_rework_context', 'arguments': {}}
                elif step == 1:
                    context = owner.results[-1]['result']
                    args = {'method': 'resolve_native_feedback', 'arguments': {
                        'native_feedback_ref': context['feedback'][-1]['feedback_ref'],
                        'proposal_ref': context['confirmed_proposals'][-1]['proposal_ref'],
                        'locator': {'kind': 'JSON_POINTER', 'pointer': '/tasks/a/requirement'}}}
                elif step == 2:
                    m = owner.results[-1]['result']
                    binding = m['payload']['binding_ref']
                    contracts = [{'task_id': 'a', 'inputs': [{'binding_ref': binding,
                        'input_value_sha256': m['payload']['selected_value_sha256'],
                        'condition': 'VALUE_CHANGED', 'invalidating_kinds': ['CONSTRAINT'],
                        'verification_note': 'Exact prior user-confirmed object mapping; semantic review remains open',
                        'verification_refs': [binding]}]}, *owner.contracts]
                    args = {'method': 'preview_native_rework', 'arguments': {
                        'mapping_ref': m['mapping_ref'], 'classification_kind': 'CONSTRAINT',
                        'rationale': 'Model proposal for the explicitly mapped A object',
                        'proposed_value': 'A 由原版工具调用提出的新要求', 'contracts': contracts}}
                else:
                    name, args = 'StructuredOutput', {'text': '已生成真实局部返工提案，请审阅。', 'questions': []}
                chunks = [{'id': 'rework-wire', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'rework-wire',
                    'choices': [{'index': 0, 'delta': {'role': 'assistant', 'tool_calls': [{'index': 0,
                        'id': 'call_' + str(len(owner.calls)), 'type': 'function',
                        'function': {'name': name, 'arguments': canonical(args)}}]}, 'finish_reason': None}]},
                    {'id': 'rework-wire', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'rework-wire',
                        'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]}]
                raw = (''.join('data: ' + canonical(v) + '\n\n' for v in chunks) + 'data: [DONE]\n\n').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


def messages(s, wire):
    authority = NativeCollaborationAuthority(s['data_dir'] / 'own-companion', token=TOKEN,
        owner_pid=123, native=HostLifecycleCounter(), platform='posix')
    value = NativeMessages(s['client'].app, authority, DesktopContextRPC(authority))
    profile = {'schema': 'eeas.opencode-provider.v1', 'id': 'rework-wire', 'revision': 1,
        'version': VERSION, 'source_commit': COMMIT,
        'executable': str(ROOT / 'var/opencode-ui-offline-guard/opencode'), 'executable_sha256': CLI_SHA['posix'],
        'provider_id': 'eeas-rework-wire', 'protocol': 'OPENAI_COMPATIBLE_CHAT_COMPLETIONS',
        'provider_url': f'http://127.0.0.1:{wire.server.server_port}/v1', 'model_id': 'rework-wire',
        'secret_ref': 'secret:fixture-only', 'transport_kind': 'MOCK_WIRE'}
    value.transport.provider_registry = OpenCodeProviderRegistry(lambda *_: copy.deepcopy(profile), lambda *_: 'fixture-only')
    return value, {'id': profile['id'], 'revision': 1, 'sha256': digest(profile)}


def test_original_native_question_resolves_feedback_and_previews_without_writing(scene):
    s = scene
    prior = mapping(s, feedback(s))
    human = s['original'].replace('原人工备注', '人工并发补充🙂必须保留')
    (s['root'] / '任务与harness.json').write_text(human)
    latest = feedback(s, 'new-human-feedback')
    b = call(s, 'POST', '/feedback-rework/bindings', {'operation_id': 'independent-b',
        'file_source_ref': source(s), 'locator': {'kind': 'JSON_POINTER', 'pointer': '/tasks/b/requirement'},
        'proposal_ref': ref(s['proposal']), 'task_ids': ['b']})
    contracts = [{'task_id': 'b', 'inputs': [{'binding_ref': b['binding_ref'],
        'input_value_sha256': b['payload']['selected_value_sha256'], 'condition': 'VALUE_CHANGED',
        'verification_note': 'User-declared independent B source, checked against actual bytes',
        'verification_refs': [b['binding_ref']]}]}]
    wire = ReworkWire(contracts)
    value, profile = messages(s, wire)
    body = {'operation_id': s.get('native_question_operation_id', 'native-original-rework'), 'raw_text': '请按这次反馈调整 A，保留人工修改和 B。',
        'file_source_ref': source(s), 'provider_profile_ref': profile}

    async def run():
        try:
            await value.submit(body, TOKEN)
            await asyncio.wait_for(asyncio.gather(*list(value.tasks.values())), timeout=90)
            result = value.get(body['operation_id'])
            check(result['status'] == 'RETURNED', str(result))
            check(len(wire.calls) == 4 and len(wire.results) == 3)
            check(result['execution']['session_id'].startswith('ses_'))
            check((s['root'] / '任务与harness.json').read_text() == human)
            previews = result['rework_previews']
            check(len(previews) == 2 and previews[0]['kind'] == 'MAPPING' and previews[1]['kind'] == 'PLAN')
            check(previews[0]['payload']['reused_mapping_ref'] == prior['mapping_ref'])
            check(previews[0]['payload']['native_feedback_ref'] == latest['feedback_ref'])
            plan = previews[1]
            check(plan['payload']['classification_status'] == 'PROPOSAL_NOT_SEMANTICALLY_VERIFIED')
            check(plan['payload']['write_authorized'] is False)
            check(plan['payload']['engine_plan']['payload']['impact']['counts'] == {
                'AFFECTED': 1, 'UNKNOWN': 0, 'RETAINED_BY_DECLARED_CONDITION': 1})
            check('apply_native_rework' not in value.transport.methods)
            check('user_confirmed' not in value.transport.methods['resolve_native_feedback'][0]['input_schema']['properties'])
            check((await value.submit(body, TOKEN))['rework_previews'] == previews)
            check(len(wire.calls) == 4, 'Exact replay must not dispatch another model request')
            applied = call(s, 'POST', '/native-rework/plans/' + plan['plan_ref']['id'] + '/apply', {
                'operation_id': 'explicit-user-apply', 'plan_ref': plan['plan_ref'],
                'expected_current_sources': [source(s)], 'allow_rebase': False})
            check(applied['status'] == 'APPLIED')
            check((s['root'] / '任务与harness.json').read_text() == human.replace(
                'A 原已确认要求', 'A 由原版工具调用提出的新要求'))
            check(call(s, 'GET', '/planning/proposals/' + s['proposal']['id'])['sha256'] == s['proposal']['sha256'])
        finally:
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()


def test_maximum_legal_native_question_operation_id_keeps_derived_ids_bounded(scene):
    scene['native_question_operation_id'] = 'q' * 100
    test_original_native_question_resolves_feedback_and_previews_without_writing(scene)
