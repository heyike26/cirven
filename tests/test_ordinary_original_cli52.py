"""Real App routes, original CLI and private plugin; only the model is loopback.

The host observes its actual POSIX process lifetime. This is not Windows,
subscription inference, or a semantic-quality qualification.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from packages.domain.tasks import canonical
from test_native_file_continuity_api import scene, MESSAGES
from test_native_messages_docx import configure, check


class OrdinaryWire:
    def __init__(self):
        self.calls, self.results, self.errors = [], [], []
        self.entered, self.release = threading.Event(), threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    index = len(owner.calls)
                    owner.calls.append(body)
                    names = {row['function']['name'] for row in body['tools']}
                    check(names == {'eeas_native_message_call'}, canonical(sorted(names)))
                    name = None
                    if index == 0:
                        owner.entered.set()
                        check(owner.release.wait(90), 'External fixture was not released')
                        args = {'text': 'Earlier original answer', 'questions': []}
                    elif index == 1:
                        name, args = 'eeas_native_message_call', {'method_id': 'read_message', 'arguments_json': '{}'}
                    else:
                        previous = [m for m in body['messages'] if m['role'] == 'tool']
                        for message in previous:
                            content = message['content']
                            if isinstance(content, list):
                                content = '\n'.join(x.get('text', '') for x in content)
                            try:
                                value = json.loads(content)
                            except (ValueError, TypeError):
                                continue
                            if isinstance(value, dict) and 'result' in value:
                                owner.results.append(value)
                        args = {'text': 'Latest guided task completed', 'questions': []}
                    delta = ({'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'ordinary_real_' + str(index),
                        'type': 'function', 'function': {'name': name, 'arguments': canonical(args)}}]}
                        if name else {'role': 'assistant', 'content': canonical(args)})
                    chunks = [{'id': 'ordinary-fixture', 'object': 'chat.completion.chunk', 'created': 0,
                        'model': 'docx-wire', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                        {'id': 'ordinary-fixture', 'object': 'chat.completion.chunk', 'created': 0,
                         'model': 'docx-wire', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if name else 'stop'}],
                         'usage': {'prompt_tokens': 15, 'completion_tokens': 10, 'total_tokens': 25}}]
                    raw = (''.join('data: ' + canonical(x) + '\n\n' for x in chunks) + 'data: [DONE]\n\n').encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except BaseException as error:
                    owner.errors.append({'type': type(error).__name__, 'message': str(error)})
                    self.send_error(500)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name='ordinary-model-fixture', daemon=False)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def test_real_original_owner_two_guidance_latest_private_tool_and_local_status(scene):
    client, pid, folder, _base, _source, settings = scene
    original = (folder / '材料.md').read_bytes()
    wire = OrdinaryWire()
    trace, controls = [], []
    result, identity = None, None
    try:
        profile = configure(client, settings, wire)
        body = {'operation_id': 'ordinary52-real-parent', 'raw_text': '  保留原任务与用户文件。\r\n中文🙂',
            'project_id': pid, 'conversation_id': 'ordinary-real-conversation', 'provider_profile_ref': profile}
        response = client.post(MESSAGES, json=body)
        check(response.status_code == 200, response.text)
        first = response.json()
        check(first['status'] == 'PROCESSING', canonical(first))
        check(wire.entered.wait(20), 'Original model request was not reached')
        parent = MESSAGES + '/' + body['operation_id']
        initial = client.get(parent).json()
        owner = initial['owner']
        sessions = client.app.state.agent_sessions
        context = sessions.active[owner['session_ref']['id']]
        process = context['process']
        identity = {'pid': process.pid, 'session_id': context['session_id'], 'generation': context['generation']}
        check(process.returncode is None)
        check(context['native_message']['bridge'].thread.is_alive())
        native = sessions.operation(pid, owner['session_ref']['id'], initial['native_cohort']['native_operation_id'])
        before = deepcopy(native)
        status_body = {'operation_id': 'ordinary52-local-status', 'expected_native_message_ref': first['native_message_ref'],
            'raw_text': '现在进度如何？', 'intent': 'AUTO'}
        status = client.post(parent + '/guidance', json=status_body)
        check(status.status_code == 200, status.text)
        local = status.json()['original_operation']
        check(local['role'] == 'LOCAL_STATUS' and local['model_calls'] == 0)
        check(len(wire.calls) == 1)
        check(sessions.operation(pid, owner['session_ref']['id'], native['id']) == before)
        for index, text in enumerate(['请保留每个结论的来源。', '回答完顺便的问题后继续原任务。']):
            guidance = {'operation_id': 'ordinary52-guide-' + str(index),
                'expected_native_message_ref': first['native_message_ref'], 'raw_text': text, 'intent': 'STEER'}
            response = client.post(parent + '/guidance', json=guidance)
            check(response.status_code == 200, response.text)
            control = response.json()
            check(control['status'] == 'ACCEPTED', canonical(control))
            check(control['original_operation']['request']['raw_text'] == text)
            controls.append(control)
            check(client.post(parent + '/guidance', json=guidance).status_code == 200)
        check(process.returncode is None and len(wire.calls) == 1)
        wrong = {**status_body, 'operation_id': 'ordinary52-wrong-ref',
            'expected_native_message_ref': {**first['native_message_ref'], 'sha256': 'e' * 64}}
        check(client.post(parent + '/guidance', json=wrong).status_code == 409)
        latest = client.get(parent).json()['native_cohort']
        check(latest['sequence'] == 3 and latest['native_session_id'] == identity['session_id'])
        check(latest['native_user_message_id'] == controls[-1]['original_operation']['native_message_id'])
        wire.release.set()
        until = time.monotonic() + 80
        while time.monotonic() < until:
            result = client.get(parent).json()
            trace.append({key: result.get(key) for key in ('status', 'native_message_phase', 'error')})
            if result['status'] != 'PROCESSING':
                break
            time.sleep(.1)
        check(result['status'] == 'RETURNED', canonical(result))
        check(result['text'] == 'Latest guided task completed')
        check(result['answer_source'] == 'ORIGINAL_NATIVE_TEXT_JSON_VALIDATED')
        check(result['native_cohort'] == latest)
        check(not wire.errors, canonical(wire.errors))
        check(wire.results and wire.results[-1]['result']['raw_text'] == body['raw_text'], canonical(wire.results))
        check(len(wire.calls) == 3, str(len(wire.calls)))
        check((folder / '材料.md').read_bytes() == original)
        deadline = time.monotonic() + 15
        while process.returncode is None and time.monotonic() < deadline:
            time.sleep(.1)
        check(process.returncode is not None and owner['session_ref']['id'] not in sessions.active)
        check(not context['native_message']['bridge'].thread.is_alive())
        for control in controls:
            recovered = client.get(parent + '/controls/' + control['operation_id'])
            check(recovered.status_code == 200)
            check(recovered.json()['original_operation']['native_message_id'] == control['original_operation']['native_message_id'])
        replay = client.post(MESSAGES, json=body)
        check(replay.status_code == 200 and replay.json()['native_cohort'] == latest)
        check(len(wire.calls) == 3)
    finally:
        wire.release.set()
        (settings.data_dir.parent / 'ordinary52-original-cli.json').write_text(canonical({
            'scope': 'REAL_APP_ASGI_ROUTES_ORIGINAL_CLI_PRIVATE_PLUGIN_ONLY_EXTERNAL_MODEL_FIXTURE',
            'windows': 'NOT_RUN', 'paid_model_calls': 0, 'identity': identity, 'result': result,
            'controls': controls, 'trace': trace, 'model_requests': len(wire.calls),
            'model_fixture_errors': wire.errors, 'source_unchanged': (folder / '材料.md').read_bytes() == original}), encoding='utf8')
        wire.close()
