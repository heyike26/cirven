"""Source64 external fixtures: model wire, owned pixels and official MCP only.

No AgentSessions/NativeReviews method or SDK response is replaced. The optional
proxy forwards real original HTTP and drops one acknowledged response only.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import re
import site
import socket
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit


def require(value, message):
    if not value:
        raise AssertionError(message)


def mcp_main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--official-mcp', action='store_true', required=True)
    parser.add_argument('--site-packages', required=True)
    parser.add_argument('--receipts', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--server-id', required=True)
    parser.add_argument('--fail-start', action='store_true')
    args = parser.parse_args()
    receipts, source = Path(args.receipts), Path(args.source)
    sys.path.insert(0, args.site_packages)
    import psutil
    startup = {'pid': os.getpid(), 'ppid': os.getppid(), 'birth': psutil.Process().create_time(),
               'server_id': args.server_id, 'dont_write_bytecode': sys.dont_write_bytecode,
               'optimization': sys.flags.optimize}
    with (receipts / ('startup-' + str(os.getpid()) + '.json')).open('x') as stream:
        json.dump(startup, stream)
    if args.fail_start:
        raise SystemExit(23)
    from mcp.server import MCPServer
    server = MCPServer(args.server_id)

    @server.tool()
    def read_receipt(label: str, hold: bool = False) -> dict:
        """Read this fixture's one explicit real file and return an audit receipt."""
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', label):
            raise ValueError('Bounded receipt label required')
        raw = source.read_bytes()
        if len(raw) > 256000:
            raise ValueError('Fixture source exceeds bound')
        result = {'label': label, 'pid': os.getpid(), 'ppid': os.getppid(),
                  'server_id': args.server_id, 'source_sha256': hashlib.sha256(raw).hexdigest(),
                  'source_bytes': len(raw), 'source_excerpt': raw.decode('utf-8')[:160]}
        with (receipts / 'calls.jsonl').open('a') as stream:
            stream.write(json.dumps(result) + '\n'); stream.flush(); os.fsync(stream.fileno())
        if hold:
            deadline = time.monotonic() + 20
            while not (receipts / 'release-tool').exists() and time.monotonic() < deadline:
                time.sleep(.025)
        return result

    server.run(transport='stdio')


if __name__ == '__main__':
    mcp_main()
    raise SystemExit


# Imports below are never loaded in the official child before its explicit
# existing site-packages path is admitted. No dependency is installed.
import psutil
from PIL import Image

from apps.api.native_messages import NativeMessages
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.agent_sessions import AgentSessions
from packages.agent_sessions.native_review import NativeReviews
from packages.domain.file_continuity import FileContinuity
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.provider_settings import ProviderSettings
from packages.domain.tasks import canonical, digest
from legacy_evidence import internal_output
from test_agent_sessions import CLI, CLI_SHA, COMMIT, KEY, MODEL, ROOT, VERSION
from test_native_collaboration import HostLifecycleCounter

TOKEN = 'd' * 64
TOOL = 's64-reader_read_receipt'
TERMINAL = {'RETURNED', 'FAILED', 'CANCELLED', 'UNKNOWN_OUTCOME', 'NOT_CONFIGURED'}


def evidence(name, value):
    directory = os.environ.get('SOURCE64_EVIDENCE')
    if directory:
        path = Path(directory)
        path = internal_output(path)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + '.json')).write_text(canonical(value), encoding='utf-8')


class FrozenPages:
    """Only OS capture/lookup is substituted; all host freezing remains real."""
    def __init__(self):
        self.pages, self.images = {}, {}
        self.first = self.add('one', 'S64_PAGE_ONE_FROZEN_271828', (30, 105, 185))

    def add(self, name, text, color=(43, 160, 80)):
        picture = Image.new('RGB', (16, 12), color)
        stream = BytesIO(); picture.save(stream, 'PNG', optimize=True)
        raw = stream.getvalue()
        ref = {'id': 's64-page-' + name, 'revision': 1, 'sha256': digest([name, text])}
        frame = {'id': 's64-frame-' + name, 'revision': 1, 'sha256': digest(['frame', name])}
        image = {'id': 's64-image-' + name, 'revision': 1,
                 'sha256': hashlib.sha256(raw).hexdigest(), 'width': 16, 'height': 12}
        observation = {'context_ref': ref, 'automatic_input_authorized': False,
            'observations': {'accessibility': {'text_content': text},
                'visual': {'frame_ref': frame, 'image_ref': image}},
            'coverage': {'pixel_source': 'EXACT_TARGET_WINDOW',
                         'qualification': 'OWNED_PNG_FIXTURE_NOT_WINDOWS'}}
        self.pages[digest(ref)] = {'observation': observation,
            'preview': {'status': 'AVAILABLE', 'image_ref': image, 'pixel_source': 'EXACT_TARGET_WINDOW'}}
        self.images[digest({k: image[k] for k in ('id', 'revision', 'sha256')})] = raw
        return ref

    def lookup(self, ref):
        require(digest(ref) in self.pages, 'Unregistered external frozen page')
        return deepcopy(self.pages[digest(ref)])

    def image(self, ref):
        require(digest(ref) in self.images, 'Unregistered external frozen image')
        return self.images[digest(ref)]


class CapabilityWire:
    """Original CLI alone selects iterations; only model replies are fixtures."""
    def __init__(self, replies):
        self.replies, self.requests = replies, []
        self.lock = threading.Lock()
        self.gates = {}
        self.handlers = set()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                owner.handlers.add(threading.current_thread())
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4 * 1024 * 1024:
                    self.send_error(413); return
                body = json.loads(self.rfile.read(size))
                if (self.path != '/v1/chat/completions' or body.get('model') != MODEL
                        or self.headers.get('Authorization') != 'Bearer ' + KEY):
                    self.send_error(403); return
                with owner.lock:
                    number = len(owner.requests)
                    owner.requests.append({'index': number, 'body': body, 'received_at': time.time()})
                    reply = owner.replies[number] if number < len(owner.replies) else 'UNEXPECTED_EXTRA_S64_RPC'
                if isinstance(reply, dict) and 'hold' in reply:
                    key = reply['hold']; gate = owner.gates.setdefault(key, threading.Event())
                    if not gate.wait(35):
                        self.send_error(504); return
                    reply = reply['reply']
                if isinstance(reply, dict) and '_http_error' in reply:
                    raw = canonical({'error': {'message': 'Synthetic model authentication rejection',
                                              'type': 'authentication_error', 'code': 'invalid_token'}}).encode()
                    self.send_response(reply['_http_error'])
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers(); self.wfile.write(raw); self.wfile.flush()
                    return
                calls = reply if isinstance(reply, list) else [reply] if isinstance(reply, dict) else []
                if calls:
                    delta = {'role': 'assistant', 'tool_calls': [
                        {'index': index, 'id': 'call_s64_' + str(number) + '_' + str(index),
                         'type': 'function', 'function': {'name': item['tool'],
                                                       'arguments': canonical(item['arguments'])}}
                        for index, item in enumerate(calls)]}
                else:
                    delta = {'role': 'assistant', 'content': reply}
                chunks = [{'id': 's64-wire', 'object': 'chat.completion.chunk', 'created': 0, 'model': MODEL,
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'id': 's64-wire', 'object': 'chat.completion.chunk', 'created': 0, 'model': MODEL,
                     'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if calls else 'stop'}],
                     'usage': {'prompt_tokens': 13, 'completion_tokens': 7, 'total_tokens': 20}}]
                raw = (''.join('data: ' + canonical(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
                try:
                    self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(raw))); self.end_headers()
                    self.wfile.write(raw); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.05),
                                       name='s64-external-model', daemon=True)

    @property
    def url(self):
        return 'http://127.0.0.1:' + str(self.server.server_port) + '/v1'

    def start(self):
        self.thread.start(); return self

    def release(self, name):
        self.gates.setdefault(name, threading.Event()).set()

    def release_all(self):
        for reply in self.replies:
            if isinstance(reply, dict) and 'hold' in reply:
                self.release(reply['hold'])

    def close(self):
        self.release_all()
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
        for thread in tuple(self.handlers):
            thread.join(timeout=3)
        require(not self.thread.is_alive() and not any(t.is_alive() for t in self.handlers),
                'External model fixture threads did not join')


class LostAckProxy:
    """One real POST is fully forwarded, then its real response is discarded.

    The readonly expected callback returns the already-durable original user ID.
    No application/controller/SDK method or returned body is fabricated.
    """
    def __init__(self, target, path, expected):
        endpoint = urlsplit(str(target))
        require(endpoint.scheme == 'http' and endpoint.hostname == '127.0.0.1' and endpoint.port,
                'Proxy may target this owned original loopback CLI only')
        self.logs, self.dropped, self.handlers = [], False, set()
        self.capture_lock, self.capture_index = threading.Lock(), 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *_):
                pass

            def forward(self):
                owner.handlers.add(threading.current_thread())
                size = int(self.headers.get('Content-Length', '0'))
                require(0 <= size <= 4 * 1024 * 1024, 'Proxy request exceeds bound')
                raw = self.rfile.read(size) if size else None
                parsed = json.loads(raw) if raw else {}
                exact_id = expected() if self.command == 'POST' and urlsplit(self.path).path == path else None
                match = bool(exact_id and parsed.get('messageID') == exact_id)
                headers = {key: val for key, val in self.headers.items()
                           if key.lower() not in {'host', 'connection', 'content-length'}}
                client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=10)
                try:
                    client.request(self.command, self.path, body=raw, headers=headers)
                    response = client.getresponse(); body = response.read(4 * 1024 * 1024 + 1)
                    require(len(body) <= 4 * 1024 * 1024, 'Proxy response exceeds bound')
                    response_headers = response.getheaders()
                    wire_ref = None
                    if os.environ.get('SOURCE64_EVIDENCE'):
                        directory = Path(os.environ['SOURCE64_EVIDENCE']) / 'lost-ack-original-http'
                        directory = internal_output(directory)
                        directory.mkdir(parents=True, exist_ok=True)
                        with owner.capture_lock:
                            index = owner.capture_index; owner.capture_index += 1
                        target = directory / ('response-' + str(index).zfill(4) + '.bin')
                        with target.open('xb') as stream: stream.write(body)
                        with target.with_suffix('.headers.json').open('x') as stream:
                            json.dump({'status': response.status, 'method': self.command,
                                'path': self.path, 'headers': response_headers}, stream)
                        wire_ref = {'path': str(target), 'bytes': len(body),
                                    'sha256': hashlib.sha256(body).hexdigest()}
                    entry = {'method': self.command, 'path': self.path, 'native_user_id': parsed.get('messageID'),
                        'request_sha256': hashlib.sha256(raw or b'').hexdigest(),
                        'forwarded_response_status': response.status, 'forwarded_complete': True,
                        'response_content_encoding': response.getheader('Content-Encoding'),
                        'response_wire_body_sha256': hashlib.sha256(body).hexdigest(),
                        'original_response_headers': response_headers,
                        'original_response_wire_body_ref': wire_ref,
                        'response_discarded': False}
                    owner.logs.append(entry)
                    if match and not owner.dropped:
                        require(response.status == 204, 'Matched original native POST was not acknowledged')
                        owner.dropped = True; entry['response_discarded'] = True
                        self.close_connection = True
                        try: self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        self.connection.close(); return
                    self.send_response(response.status)
                    # HTTPConnection removes transfer framing, but does not
                    # decompress bytes. Preserve the original Content-Encoding
                    # so the real SDK/httpx, not this fixture, decodes them.
                    for name, value in response_headers:
                        if name.lower() not in {'connection', 'transfer-encoding', 'content-length',
                                'server', 'date', 'keep-alive', 'upgrade', 'proxy-authenticate',
                                'proxy-authorization', 'trailer', 'te'}:
                            self.send_header(name, value)
                    self.send_header('Content-Length', str(len(body))); self.end_headers()
                    self.wfile.write(body)
                finally:
                    client.close()

            do_GET = forward
            do_POST = forward

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.05),
                                       name='s64-lost-ack-proxy', daemon=True)

    @property
    def url(self):
        return 'http://127.0.0.1:' + str(self.server.server_port)

    def start(self):
        self.thread.start(); return self

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
        for thread in tuple(self.handlers): thread.join(timeout=11)
        require(not self.thread.is_alive() and not any(t.is_alive() for t in self.handlers),
                'Real HTTP proxy threads did not join')


class CapabilityScene:
    def __init__(self, scene, wire, *, images=True):
        require(CLI.is_file() and hashlib.sha256(CLI.read_bytes()).hexdigest() == CLI_SHA[os.name],
                'Exact unmodified original CLI is mandatory')
        self.scene, self.wire = scene, wire
        self.caps, self.pid, self.data = scene['manager'], scene['pid'], scene['data']
        self.receipts, self.contexts, self.observed_owners, self.identities = {}, {}, [], {}
        self.permission_projections = []
        self.config = {'schema': 'eeas.opencode-provider.v1', 'id': 's64-provider', 'revision': 1,
            'version': VERSION, 'source_commit': COMMIT, 'executable': str(CLI),
            'executable_sha256': CLI_SHA[os.name], 'provider_id': 'eeas-source64-wire',
            'protocol': 'OPENAI_COMPATIBLE_CHAT_COMPLETIONS', 'provider_url': wire.url, 'model_id': MODEL,
            'secret_ref': 'secret:s64-fixture-only', 'transport_kind': 'MOCK_WIRE', 'supports_images': images}
        registry = OpenCodeProviderRegistry(lambda *_: deepcopy(self.config), lambda _: KEY)
        self.profile = {'id': self.config['id'], 'revision': 1, 'sha256': digest(self.config)}
        registry.resolve(self.profile)  # Real preflight before even a probe child may start.
        self.sessions = AgentSessions(scene['store'], self.data, provider_registry=registry,
            workspaces=scene['workspaces'], capabilities=self.caps)
        app = SimpleNamespace(state=SimpleNamespace(store=scene['store'],
            settings=SimpleNamespace(data_dir=self.data), native_capabilities=self.caps,
            provider_settings=ProviderSettings(scene['store'], self.data, ROOT),
            local_workspaces=scene['workspaces'],
            file_continuity=FileContinuity(scene['store'], self.data, scene['workspaces'])))
        self.pages = FrozenPages()
        authority = NativeCollaborationAuthority(self.data, token=TOKEN, owner_pid=123,
            native=HostLifecycleCounter(), platform='posix')
        self.native = NativeMessages(app, authority, self.pages)
        self.native.transport.provider_registry = registry
        self.reviews = NativeReviews(self.native, self.sessions)
        self.native.reviews = self.reviews
        self.skill = self.caps.import_skill({'operation_id': 's64-import-skill', 'id': 's64-evidence',
            'expected_revision': 0, 'enabled': True, 'content': '---\nname: s64-evidence\n'
                'description: Explain exact frozen evidence and limits.\n---\n'
                'Use only explicitly frozen page/file data. Mark incomplete coverage.\n'})['config_ref']

    async def configure(self, ident='s64-reader', *, fail_start=False, selected=True):
        script = self.data / (ident + '-official.py')
        script.write_bytes(Path(__file__).read_bytes())
        directory = self.data / (ident + '-receipts'); directory.mkdir()
        self.receipts[ident] = directory
        executable = Path(sys.executable).resolve()
        command = [str(executable), '-B', str(script), '--official-mcp', '--site-packages',
                   site.getsitepackages()[0], '--receipts', str(directory), '--source',
                   str(self.scene['actual']), '--server-id', ident]
        if fail_start: command.append('--fail-start')
        saved = self.caps.configure_mcp({'operation_id': 's64-config-' + ident, 'id': ident,
            'expected_revision': 0, 'configuration': {'type': 'local', 'command': command,
                'files_sha256': {str(executable): hashlib.sha256(executable.read_bytes()).hexdigest(),
                                 str(script): hashlib.sha256(script.read_bytes()).hexdigest(),
                                 str(self.scene['actual']): hashlib.sha256(self.scene['actual'].read_bytes()).hexdigest()},
                'enabled': True, 'timeout': 10000}})
        if not selected: return saved
        probe = await self.caps.probe_mcp(ident, {'config_ref': saved['config_ref'],
            'allowed_effects': ['PROCESS_START'], 'network_mode': 'AS_CONFIGURED'})
        require(probe['status'] == 'INITIALIZED_AND_LISTED', 'Official MCP initialize/list failed')
        server = next(v for v in self.caps.tool_catalog()['mcp_servers'] if v['id'] == ident)
        tool = next(v for v in server['tools'] if v['name'] == 'read_receipt')
        return {'server_id': ident, 'config_ref': server['config_ref'], 'probe_ref': server['probe_ref'],
                'tool_id': tool['id'], 'name': tool['name'], 'input_schema_sha256': tool['input_schema_sha256']}

    def body(self, operation, *, tools=(), skill=True, consent=True, context=None, root=None, file_ref=None):
        return {'operation_id': operation, 'raw_text': 'Explain this exact frozen page and explicit file: ' + operation,
            'project_id': self.pid, 'provider_profile_ref': self.profile, 'conversation_id': 's64-discussion',
            'context_ref': context or self.pages.first, 'file_source_ref': file_ref, 'file_link_ref': None,
            'native_review': {'version': 2, 'kind': 'CAPABILITY_TASK',
                'selection': {'capability_binding_sha256': self.caps.native_binding_snapshot(validate_mcp_ids=set())['sha256'],
                    'skill_ref': self.skill if skill else None, 'mcp_tool_selection': list(tools)},
                'process_start_consent': consent, 'remote_mcp_connect_consent': False,
                'task_root_operation_id': root}}

    def record(self, operation):
        record = self.reviews._record(operation)
        owner = record.get('owner')
        if owner:
            ident = owner['session_ref']['id']
            self.observed_owners.append(deepcopy(owner)) if owner not in self.observed_owners else None
            if ident in self.sessions.active:
                self.contexts[ident] = self.sessions.active[ident]
                identity = self.sessions._state(owner['project_id'], ident)['process_identity']
                if identity is not None:
                    self.identities.setdefault(ident, deepcopy(identity))
        return record

    async def poll(self, operation, predicate, *, timeout=40):
        deadline = asyncio.get_running_loop().time() + timeout
        result = None
        while asyncio.get_running_loop().time() < deadline:
            record = self.record(operation); owner = record.get('owner')
            if owner and owner['session_ref']['id'] in self.sessions.active:
                await self.sessions.inspect(owner['project_id'], owner['session_ref']['id'])
            result = self.reviews.get(operation)
            if predicate(result): return result
            await asyncio.sleep(.075)
        raise AssertionError('Original capability task did not reach expected state: ' + canonical(result))

    async def control(self, operation, control_id, kind, **fields):
        result = self.reviews.get(operation)
        return await self.reviews.control(operation, {'operation_id': control_id,
            'expected_review_ref': result['review']['review_ref'], **fields}, kind=kind,
            request_id=(fields.get('request_ref') or {}).get('id'))

    def starts(self, ident='s64-reader'):
        return [json.loads(p.read_bytes()) for p in sorted(self.receipts[ident].glob('startup-*.json'))]

    def calls(self, ident='s64-reader'):
        path = self.receipts[ident] / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def original_messages(self, operation):
        record = self.record(operation); owner = record['owner']
        context = self.sessions.active[owner['session_ref']['id']]
        return await self.sessions._http(context, 'GET', '/session/' + context['session_id'] + '/message')

    async def close(self, name):
        self.wire.release_all()
        for path in self.receipts.values(): (path / 'release-tool').touch()
        try:
            await self.native.close()
        finally:
            await self.sessions.close()
            deadline = time.monotonic() + 5
            starts = [row for ident in self.receipts for row in self.starts(ident)]
            def alive_original(row):
                try:
                    return psutil.Process(row['pid']).create_time() == row['birth']
                except psutil.NoSuchProcess:
                    return False
            while any(alive_original(row) for row in starts) and time.monotonic() < deadline:
                await asyncio.sleep(.05)
            alive = [row['pid'] for row in starts if alive_original(row)]
            children = [{'session_id': sid, 'process_identity': self.identities.get(sid),
                'pid': ctx['process'].pid, 'returncode': ctx['process'].returncode,
                'still_registered': sid in self.sessions.active} for sid, ctx in self.contexts.items()]
            wire_threads = [{'session_id': sid, 'name': thread.name, 'ident': thread.ident,
                'is_alive': thread.is_alive()} for sid, ctx in self.contexts.items()
                if ctx.get('wire') is not None for thread in ctx['wire'].owned_threads]
            tasks = [{'session_id': sid, 'key': key, 'done': task.done(), 'cancelled': task.cancelled()}
                     for sid, ctx in self.contexts.items() for key, task in ctx.items()
                     if isinstance(task, asyncio.Task)]
            evidence(name + '-cleanup', {'qualification': 'ACTUAL_LINUX_ORIGINAL_CLI_OFFICIAL_MCP_EXTERNAL_WIRE',
                'windows': 'NOT_RUN', 'real_model_calls': 0, 'owners': self.observed_owners,
                'children': children, 'official_mcp_pids': starts, 'alive': alive,
                'provider_wire_threads': wire_threads, 'original_context_tasks': tasks,
                'active_owners': list(self.sessions.active), 'model_rpc_count': len(self.wire.requests)})
            require(not alive and not self.sessions.active and
                    all(c['returncode'] is not None for c in children) and
                    not any(t['is_alive'] for t in wire_threads) and all(t['done'] for t in tasks),
                    'Actual process/thread/task owner cleanup not confirmed')


def image_urls(request):
    return [part['image_url']['url'] for message in request['body'].get('messages', [])
            if isinstance(message.get('content'), list) for part in message['content']
            if isinstance(part, dict) and part.get('type') == 'image_url']


def text_of(request):
    chunks = []
    for message in request['body'].get('messages', []):
        content = message.get('content')
        if isinstance(content, str): chunks.append(content)
        elif isinstance(content, list):
            chunks.extend(part.get('text', '') for part in content if isinstance(part, dict))
    return '\n'.join(chunks)


def find_objects(value, predicate):
    found = []
    if isinstance(value, dict):
        if predicate(value): found.append(value)
        for child in value.values(): found.extend(find_objects(child, predicate))
    elif isinstance(value, list):
        for child in value: found.extend(find_objects(child, predicate))
    return found
