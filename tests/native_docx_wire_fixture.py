"""Scripted external model for real ordinary CLI calls and controlled saved-file changes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading

from packages.adapters.opencode_profile import CLI_SHA, VERSION
from packages.domain.local_workspaces import canonical, sha

ROOT = Path(__file__).resolve().parents[1]
QUOTE = '验收必须自动通过。'
REPLACEMENT = '验收失败须保留日志并等待负责人确认。'
ANSWER = '已提出局部修订，请审阅。'


def check(value, message='native DOCX fixture requirement failed'):
    if not value:
        raise AssertionError(message)


class RevisionWire:
    def __init__(self, *, gate_model=False, read_only=False, read_method='read_linked_file'):
        self.calls, self.results = [], []
        self.entered, self.release = threading.Event(), threading.Event()
        if not gate_model:
            self.release.set()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append(body)
                previous = [m for m in body['messages'] if m['role'] == 'tool']
                if previous:
                    content = previous[-1]['content']
                    if isinstance(content, list):
                        content = '\n'.join(x.get('text', '') for x in content)
                    observed = json.loads(content)
                    owner.results.append(observed)
                    check(observed.get('status') != 'ERROR', canonical(observed))
                step = len(previous)
                name = 'eeas_native_message_call'
                check(any(t['function']['name'] == name for t in body['tools']))
                method, arguments = None, {}
                if read_only and step == 0:
                    method = read_method
                elif read_only:
                    check(step == 1)
                elif step == 0:
                    method = 'read_docx_revision_context'
                elif step == 1:
                    context = owner.results[-1]['result']
                    paragraph = next(p for p in context['paragraphs'] if QUOTE in p['text'])
                    method = 'locate_docx_revision'
                    arguments = {'part': 'word/document.xml', 'paragraph_path': paragraph['paragraph_path'], 'quote': QUOTE}
                elif step == 2:
                    located = owner.results[-1]['result']
                    anchor = located.get('anchor', located)
                    method = 'propose_docx_revision'
                    arguments = {'patches': [{**anchor, 'replacement': REPLACEMENT,
                        'reason': '只修订批注指定的验收说法，保留人工阈值。'}]}
                if (read_only and step == 0) or (not read_only and step == 3):
                    owner.entered.set()
                    if not owner.release.wait(90):
                        self.send_error(503)
                        return
                if method:
                    tool_input = {'method_id': method, 'arguments_json': canonical(arguments)}
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_' + str(len(owner.calls)),
                        'type': 'function', 'function': {'name': name, 'arguments': canonical(tool_input)}}]}
                else:
                    text = owner.results[-1]['result']['text_content'] if read_only else ANSWER
                    delta = {'role': 'assistant', 'content': canonical({'text': text, 'questions': []})}
                chunks = [
                    {'id': 'docx-wire', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'docx-wire',
                     'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'id': 'docx-wire', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'docx-wire',
                     'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if method else 'stop'}]}]
                raw = (''.join('data: ' + canonical(x) + '\n\n' for x in chunks) + 'data: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def configure(client, settings, wire):
    approved = Path(os.environ.get('EEAS_TEST_APPROVED_CLI_CACHE',
        str(ROOT / 'var/opencode-ui-offline-guard/opencode')))
    check(sha(approved.read_bytes()) == CLI_SHA[os.name])
    target = settings.data_dir / 'provider-settings' / 'runtime' / VERSION / 'opencode'
    target.parent.mkdir(parents=True, exist_ok=True)
    if approved.stat().st_dev == target.parent.stat().st_dev:
        os.link(approved, target)
    else:
        shutil.copy2(approved, target)
    saved = client.post('/api/planning/providers', json={'operation_id': 'save-docx-fixture',
        'id': 'docx-fixture', 'provider_url': f'http://127.0.0.1:{wire.server.server_port}/v1',
        'model_id': 'docx-wire', 'api_key': 'fixture-only', 'auth_mode': 'API_KEY'})
    check(saved.status_code in {200, 201}, saved.text)
    return saved.json()['profile_ref']
