"""Real companion HTTP/Store/original CLI/MCP; only the external model is a fixture."""
from contextlib import asynccontextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.desktop_context import DesktopContextRPC
from apps.api.native_messages import register_native_message_routes
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.local_workspaces import canonical, digest
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.provider_settings import ProviderSettings
from packages.domain.store import Conflict, Store
from test_native_collaboration import HostLifecycleCounter

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'a' * 64


class ModelWire:
    def __init__(self, method='read_message'):
        self.calls, self.tool_read = [], []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append(body)
                previous = [m for m in body['messages'] if m['role'] == 'tool']
                if not previous:
                    name = next(x['function']['name'] for x in body['tools'] if x['function']['name'].endswith('_invoke'))
                    arguments = {'method': method, 'arguments': {}}
                else:
                    content = previous[-1]['content']
                    if isinstance(content, list): content = '\n'.join(v.get('text', '') for v in content)
                    observed = json.loads(content)
                    owner.tool_read.append(observed)
                    name, arguments = 'StructuredOutput', {'text': observed['result'].get('raw_text',observed['result'].get('text_content')), 'questions': []}
                chunks = [{'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'companion-wire',
                    'choices': [{'index': 0, 'delta': {'role': 'assistant', 'tool_calls': [{'index': 0,
                        'id': 'call_' + str(len(owner.calls)), 'type': 'function',
                        'function': {'name': name, 'arguments': canonical(arguments)}}]}, 'finish_reason': None}]},
                    {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'companion-wire',
                     'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]}]
                raw = (''.join('data: ' + canonical(v) + '\n\n' for v in chunks) + 'data: [DONE]\n\n').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


def application(tmp_path, wire=None, token=TOKEN):
    authority = NativeCollaborationAuthority(tmp_path, token=token, owner_pid=123,
        native=HostLifecycleCounter(), platform='posix')
    @asynccontextmanager
    async def lifespan(_app):
        yield
        await value.close()
    app = FastAPI(lifespan=lifespan)
    store = Store(tmp_path / 'eeas.sqlite3')
    app.state.store, app.state.settings = store, SimpleNamespace(data_dir=tmp_path)
    app.state.provider_settings = ProviderSettings(store, tmp_path, ROOT)
    @app.exception_handler(Conflict)
    async def conflict(_request, exc): return JSONResponse({'error': str(exc)}, status_code=409)
    value = register_native_message_routes(app, authority=authority, context_rpc=DesktopContextRPC(authority))
    profile = None
    if wire:
        profile = {'schema': 'eeas.opencode-provider.v1', 'id': 'companion-wire', 'revision': 1,
            'version': VERSION, 'source_commit': COMMIT,
            'executable': str(ROOT / 'var/opencode-ui-offline-guard/opencode'), 'executable_sha256': CLI_SHA['posix'],
            'provider_id': 'eeas-companion-wire', 'protocol': 'OPENAI_COMPATIBLE_CHAT_COMPLETIONS',
            'provider_url': f'http://127.0.0.1:{wire.server.server_port}/v1', 'model_id': 'companion-wire',
            'secret_ref': 'secret:fixture-only', 'transport_kind': 'MOCK_WIRE'}
        value.transport.provider_registry = OpenCodeProviderRegistry(lambda *_: copy.deepcopy(profile), lambda *_: 'fixture-only')
    return app, value, profile


def test_not_configured_is_durable_and_never_silently_switches_provider(tmp_path):
    app, value, _ = application(tmp_path)
    body = {'operation_id': 'question', 'raw_text': '当前页这个问题是什么？'}
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        first = client.post('/api/desktop-host/native-messages', json=body)
        assert first.status_code == 200, first.text
        assert first.json()['status'] == 'NOT_CONFIGURED'
        assert first.json()['artifacts'] == [] and first.json()['automatic_input_authorized'] is False
        assert client.post('/api/desktop-host/native-messages', json=body).json() == first.json()
        changed = {**body, 'raw_text': '另一条要求'}
        assert client.post('/api/desktop-host/native-messages', json=changed).status_code == 409
        assert value.transport.active == {}


def test_actual_original_question_progress_result_replay_and_restart(tmp_path):
    wire = ModelWire()
    try:
        app, value, profile = application(tmp_path, wire)
        body = {'operation_id': 'original-question', 'raw_text': '  原始提问 中文🙂\r\n',
            'provider_profile_ref': {'id': profile['id'], 'revision': 1, 'sha256': digest(profile)}}
        with TestClient(app) as client:
            client.headers['X-EEAS-Desktop-Host'] = TOKEN
            first = client.post('/api/desktop-host/native-messages', json=body)
            assert first.status_code == 200, first.text
            assert first.json()['status'] == 'PROCESSING'
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                result = client.get('/api/desktop-host/native-messages/original-question').json()
                if result['status'] != 'PROCESSING': break
                time.sleep(.1)
            assert result['status'] == 'RETURNED', result
            assert result['text'] == body['raw_text']
            assert result['execution']['session_id'].startswith('ses_')
            assert result['artifacts'] == [] and result['automatic_input_authorized'] is False
            assert wire.tool_read[0]['result']['raw_text'] == body['raw_text']
            assert client.post('/api/desktop-host/native-messages', json=body).json()['execution'] == result['execution']
            assert len(wire.calls) == 2
        restarted, _, _ = application(tmp_path, wire)
        with TestClient(restarted) as client:
            client.headers['X-EEAS-Desktop-Host'] = TOKEN
            saved = client.get('/api/desktop-host/native-messages/original-question').json()
            assert saved['status'] == 'RETURNED' and saved['text'] == body['raw_text']
            assert len(wire.calls) == 2
    finally:
        wire.close()


def test_renderer_cannot_choose_native_handle_or_generic_tool(tmp_path):
    app, _, _ = application(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        assert client.post('/api/desktop-host/native-messages', json={
            'operation_id': 'bad', 'raw_text': '解释', 'hwnd': 123, 'method': 'click'}).status_code == 422
        client.headers.pop('X-EEAS-Desktop-Host')
        assert client.post('/api/desktop-host/native-messages', json={
            'operation_id': 'bad', 'raw_text': '解释'}).status_code == 409


def test_new_host_token_restarts_without_adopting_old_message_authority_and_history_remains_readonly(tmp_path):
    app, _value, _ = application(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        first = client.post('/api/desktop-host/native-messages', json={
            'operation_id': 'previous-question', 'raw_text': '之前问过的研究方向', 'conversation_id': 'research-history'}).json()
        assert first['status'] == 'NOT_CONFIGURED'
    second_token = 'b' * 64
    app, value, _ = application(tmp_path, token=second_token)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = second_token
        historical = client.get('/api/desktop-host/native-messages/previous-question').json()
        assert historical['status'] == 'NOT_CONFIGURED'
        assert historical['record_origin'] == 'HISTORICAL_READONLY'
        assert historical['automatic_input_authorized'] is False and historical['changes_applied'] is False
        with pytest.raises(Conflict):
            value.read_history({}, {'request': {'operation_id': 'new-question'}})
        current = client.post('/api/desktop-host/native-messages', json={
            'operation_id': 'new-question', 'conversation_id': 'research-history', 'raw_text': '继续原研究方向'})
        assert current.status_code == 200 and current.json()['status'] == 'NOT_CONFIGURED'
        history = value.read_history({}, {'request': {'operation_id': 'new-question'}})
        assert history['messages'][0]['user_text'] == '之前问过的研究方向'
        assert history['messages'][0]['scope'] == 'HISTORICAL_DATA_NO_NATIVE_AUTHORITY'
        assert value.transport.active == {}
