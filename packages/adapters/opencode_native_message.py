"""Private ordinary-message plugin bridge; no provider loop or ambient tool scope."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time

from packages.adapters.opencode_native_review import regular, write_private
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest
from packages.adapters.private_tool_bridge import PrivateToolBridge

PLUGIN_ID = 'eeas.native-message-context'
PLUGIN_PATH = 'toolchain/opencode-plugins/native-message-context'
PLUGIN_FILES = ('index.mjs', 'package.json', 'README.md')
PLUGIN_MANIFEST_SHA256 = 'd90b06450ced34ee3af287c993b3efb31b5b0be23503d4ecaca2171a57f0bbfe'
TOOL_NAME = 'eeas_native_message_call'
ENV_KEY = 'EEAS_NATIVE_MESSAGE_CONFIG'
MAX_BYTES = 3 * 1024 * 1024


def verify_bundle(source_root):
    root = Path(source_root) / PLUGIN_PATH
    raw = regular(root / 'manifest.json')
    if hashlib.sha256(raw).hexdigest() != PLUGIN_MANIFEST_SHA256:
        raise Conflict('Ordinary plugin manifest changed')
    value = json.loads(raw)
    if value.get('schema') != 'eeas.native-message-plugin.v1' or value.get('id') != PLUGIN_ID:
        raise Conflict('Ordinary plugin identity changed')
    rows = value.get('files', [])
    if len(rows) != len(PLUGIN_FILES) or {r['path'] for r in rows} != set(PLUGIN_FILES):
        raise Conflict('Ordinary plugin member set changed')
    for row in rows:
        content = regular(root / row['path'])
        if len(content) != row['bytes'] or hashlib.sha256(content).hexdigest() != row['sha256']:
            raise Conflict('Ordinary plugin member changed')
    dependencies = value.get('dependencies')
    if not isinstance(dependencies, list) or len(dependencies) != 1:
        raise Conflict('Ordinary plugin shared vendor closure missing')
    dep = dependencies[0]
    if dep.get('path') != 'toolchain/opencode-plugins/dynamic-workflow/manifest.json' or dep.get('prefix') != 'vendor/zod/' or dep.get('version') != '4.1.8':
        raise Conflict('Ordinary plugin shared vendor identity differs')
    manifest = regular(Path(source_root) / dep['path'], 262144)
    if hashlib.sha256(manifest).hexdigest() != dep['sha256']:
        raise Conflict('Ordinary shared vendor manifest changed')
    records = [r for r in json.loads(manifest)['files'] if r['path'].startswith(dep['prefix'])]
    if not records or not any(r['path'] == 'vendor/zod/index.js' and r['sha256'] == dep['entry_sha256'] for r in records):
        raise Conflict('Ordinary shared Zod entry differs')
    for row in records:
        member = regular((Path(source_root) / dep['path']).parent / row['path'], 1048576)
        if len(member) != row['bytes'] or hashlib.sha256(member).hexdigest() != row['sha256']:
            raise Conflict('Ordinary shared vendor closure changed')
    return {'entry': str(root / 'index.mjs'), 'manifest_sha256': PLUGIN_MANIFEST_SHA256}


class NativeMessageBridge(PrivateToolBridge):
    """Original ordinary-message interface over the shared tracked bridge."""
    pass


def prepare(source_root, runtime, *, owner_ref, generation, directory, bridge, methods=None):
    bundle = verify_bundle(source_root)
    root = Path(runtime) / 'native-message' / ('generation-' + str(generation))
    scope = {'schema': 'eeas.native-message-scope.v1', 'plugin_id': PLUGIN_ID,
        'plugin_manifest_sha256': bundle['manifest_sha256'], 'owner_ref': owner_ref, 'generation': generation,
        'directory': str(directory), 'native_session_id': None, 'broker_url': bridge.base_url, 'broker_key': bridge.key,
        'methods': methods or []}
    value = {**scope, 'scope_sha256': digest(scope)}
    path = root / 'scope.json'; write_private(path, value)
    return {'entry': bundle['entry'], 'config_path': str(path), 'ready_path': str(root/'ready.json'),
            'scope_sha256': value['scope_sha256'], 'initial_scope_sha256': value['scope_sha256'],
            'manifest_sha256': bundle['manifest_sha256']}


def bind(prepared, native_session_id):
    path = Path(prepared['config_path']); value = json.loads(regular(path, private=True))
    if digest({k:v for k,v in value.items() if k != 'scope_sha256'}) != value['scope_sha256'] or value['native_session_id'] is not None:
        raise Conflict('Ordinary plugin initial scope changed')
    value['native_session_id'] = native_session_id; value['scope_sha256'] = digest({k:v for k,v in value.items() if k != 'scope_sha256'})
    write_private(path, value, replace=True)
    return {**prepared, 'scope_sha256': value['scope_sha256']}


def ready(prepared):
    path = Path(prepared['ready_path'])
    if not path.exists(): return False
    value = json.loads(regular(path, private=True))
    return value == {'schema':'eeas.native-message-ready.v1', 'plugin_id':PLUGIN_ID,
        'plugin_manifest_sha256':prepared['manifest_sha256'], 'initial_scope_sha256':prepared['initial_scope_sha256']}
