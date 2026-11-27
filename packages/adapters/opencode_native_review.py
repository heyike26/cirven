"""Pinned private page tools and a reference-only MCP facade for one native owner.

No model loop, arbitrary URL/path tool, ambient credentials or user-file writes.
The local MCP process is trusted packaged code, not an OS sandbox.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import threading
import urllib.request
from urllib.parse import urlsplit

# The packaged branch MUST use native_package_bootstrap. Developer CLI tests
# execute this one fixed file: bind only our package namespace to its ordinary
# sibling source; never broaden sys.path or accept a caller-supplied source root.
if __name__ == '__main__' and not __package__:
    import importlib.util
    _root = Path(__file__).resolve().parents[2]
    if (_root / 'PACKAGE_MANIFEST.json').exists():
        raise SystemExit('Packaged native review MCP requires the verified bootstrap')
    _init = _root / 'packages' / '__init__.py'
    if _init.is_symlink() or any(p.is_symlink() for p in _init.parents) or not _init.is_file():
        raise SystemExit('Developer review package source is not ordinary')
    if 'packages' in sys.modules:
        raise SystemExit('An ambient packages namespace cannot be adopted')
    _spec = importlib.util.spec_from_file_location('packages', _init, submodule_search_locations=[str(_init.parent)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules['packages'] = _module
    _spec.loader.exec_module(_module)

from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest
from packages.adapters.private_tool_bridge import PrivateToolBridge

PLUGIN_ID = 'eeas.native-review-context'
PLUGIN_PATH = 'toolchain/opencode-plugins/native-review-context'
PLUGIN_FILES = ('index.mjs', 'package.json', 'README.md')
PLUGIN_MANIFEST_SHA256 = 'd6e6e5c7528f153e0f35f01e5c53c0782c686c85a76c7f9b33727017354129a0'
SERVER_ID = 'cirven-review-acceptance'
TOOL_NAME = SERVER_ID + '_acceptance_coverage'
BUILTIN_ID = 'planning_acceptance_coverage'


def regular(path, limit=65536, *, private=False):
    path = Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise Conflict('Review source cannot be a link')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit or private and info.st_nlink != 1:
        raise Conflict('Review source is not a bounded ordinary file')
    raw = path.read_bytes()
    after = path.stat()
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(raw) != info.st_size:
        raise Conflict('Review source changed during read')
    return raw


def write_private(path, value, *, replace=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise Conflict('Review private path is linked')
    raw = canonical(value).encode('utf-8')
    if len(raw) > 65536:
        raise Conflict('Review scope exceeds 64 KiB')
    if not replace:
        with path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    else:
        if path.exists(): regular(path, private=True)
        temporary = path.with_name(path.name + '-' + secrets.token_hex(8) + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary.exists(): temporary.unlink()
    path.chmod(0o600)


def verify_bundle(source_root):
    root = Path(source_root) / PLUGIN_PATH
    raw = regular(root / 'manifest.json')
    if hashlib.sha256(raw).hexdigest() != PLUGIN_MANIFEST_SHA256:
        raise Conflict('The native review plugin manifest differs')
    value = json.loads(raw)
    if value.get('id') != PLUGIN_ID or value.get('schema') != 'eeas.native-review-plugin.v1' or value.get('dependencies') != []:
        raise Conflict('The native review plugin contract differs')
    records = value.get('files')
    if not isinstance(records, list) or {r.get('path') for r in records} != set(PLUGIN_FILES) or len(records) != len(PLUGIN_FILES):
        raise Conflict('The native review plugin closure is incomplete')
    for row in records:
        content = regular(root / row['path'])
        if len(content) != row['bytes'] or hashlib.sha256(content).hexdigest() != row['sha256']:
            raise Conflict('The native review plugin member differs')
    return {'entry': str(root / 'index.mjs'), 'manifest_sha256': PLUGIN_MANIFEST_SHA256, 'files': records}


def create_acceptance_server(resolve, listed=None):
    """Actual official MCP SDK; no store opening or model-supplied candidate."""
    from mcp.server import MCPServer
    class ObservedServer(MCPServer):
        async def list_tools(self):
            tools = await super().list_tools()
            if listed is not None:
                listed([{'name': item.name, 'input_schema': item.input_schema} for item in tools])
            return tools
    server = ObservedServer('cirven-review-acceptance', version='1.0.0', instructions=(
        'Check only the frozen report plan explicitly attached to this review. '
        'Pass its review_ref; do not construct or copy a candidate. Structural coverage is not business correctness.'))

    @server.tool(name='acceptance_coverage')
    def acceptance_coverage(review_ref: dict) -> dict:
        return resolve(review_ref)
    return server


def builtin_schema():
    # list_tools is an in-process declaration read: no MCP child/probe, no I/O.
    server = create_acceptance_server(lambda _ref: None)
    return server._tool_input_schema('acceptance_coverage')


class ReviewBridge(PrivateToolBridge):
    """Frozen review routes with the same complete original-owner barrier."""
    def __init__(self, callback, loop):
        super().__init__(callback,loop,routes={'/v1/page','/v1/coverage'},label='review',timeout=12)


def prepare(source_root, runtime, *, owner_ref, generation, directory, bridge):
    bundle = verify_bundle(source_root)
    root = Path(runtime) / 'native-review' / ('generation-' + str(generation))
    scope = {'schema': 'eeas.native-review-scope.v1', 'plugin_id': PLUGIN_ID,
        'plugin_manifest_sha256': bundle['manifest_sha256'], 'owner_ref': owner_ref,
        'generation': generation, 'directory': str(directory), 'native_session_id': None,
        'broker_url': bridge.base_url, 'broker_key': bridge.key}
    value = {**scope, 'scope_sha256': digest(scope)}
    path = root / 'scope.json'; write_private(path, value)
    return {'entry': bundle['entry'], 'config_path': str(path), 'ready_path': str(root / 'ready.json'),
            'scope_sha256': value['scope_sha256'], 'initial_scope_sha256': value['scope_sha256'],
            'manifest_sha256': bundle['manifest_sha256']}


def bind(prepared, native_session_id):
    path = Path(prepared['config_path'])
    value = json.loads(regular(path, private=True))
    if digest({k: v for k, v in value.items() if k != 'scope_sha256'}) != value['scope_sha256'] or value['native_session_id'] is not None:
        raise Conflict('Review plugin initial scope differs')
    value['native_session_id'] = native_session_id
    value['scope_sha256'] = digest({k: v for k, v in value.items() if k != 'scope_sha256'})
    write_private(path, value, replace=True)
    return {**prepared, 'scope_sha256': value['scope_sha256']}


def ready(prepared):
    path = Path(prepared['ready_path'])
    if not path.exists(): return False
    value = json.loads(regular(path, private=True))
    return (value.get('schema') == 'eeas.native-review-ready.v1' and value.get('plugin_id') == PLUGIN_ID
            and value.get('plugin_manifest_sha256') == prepared['manifest_sha256']
            and value.get('initial_scope_sha256') == prepared['initial_scope_sha256'])


def listed_receipt(prepared):
    value = json.loads(regular(Path(prepared['config_path']).with_name('mcp-listed.json'), private=True))
    expected = {'schema': 'eeas.native-review-mcp-listed.v1',
        'initial_scope_sha256': prepared['initial_scope_sha256'],
        'tools': [{'name': 'acceptance_coverage', 'input_schema': builtin_schema()}]}
    if value != expected: raise Conflict('Original private MCP list receipt differs')
    return value


def _mcp_main(scope_path):
    initial = json.loads(regular(scope_path, private=True))
    if digest({k: v for k, v in initial.items() if k != 'scope_sha256'}) != initial['scope_sha256']:
        raise Conflict('Initial private MCP scope differs')
    def listed(tools):
        receipt = {'schema': 'eeas.native-review-mcp-listed.v1',
            'initial_scope_sha256': initial['scope_sha256'], 'tools': tools}
        path = Path(scope_path).with_name('mcp-listed.json')
        if path.exists():
            if json.loads(regular(path, private=True)) != receipt: raise Conflict('MCP tools changed after listing')
        else: write_private(path, receipt)
    def resolve(review_ref):
        value = json.loads(regular(scope_path, private=True))
        if digest({k: v for k, v in value.items() if k != 'scope_sha256'}) != value['scope_sha256'] or not value['native_session_id']:
            raise Conflict('Review MCP is not bound to the original session')
        parsed = urlsplit(value['broker_url'])
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path or not parsed.port:
            raise Conflict('Review MCP broker is not private loopback')
        raw = canonical({'review_ref': review_ref, 'owner_ref': value['owner_ref'],
                         'generation': value['generation'], 'native_session_id': value['native_session_id']}).encode()
        request = urllib.request.Request(value['broker_url'] + '/v1/coverage', data=raw,
            headers={'Authorization': 'Bearer ' + value['broker_key'], 'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=15) as response: result = response.read(65537)
        if len(result) > 65536: raise Conflict('Review MCP result exceeds 64 KiB')
        return json.loads(result)
    create_acceptance_server(resolve, listed).run(transport='stdio')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Private fixed native review MCP')
    parser.add_argument('--scope', type=Path, required=True)
    _mcp_main(parser.parse_args().scope)
