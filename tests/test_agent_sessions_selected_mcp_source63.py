"""Real original CLI + official MCP; only the external model is a wire fixture.

SOURCE63_BEFORE_SNAPSHOT optionally replays exact preserved old implementation
classes for the independent counterexample. It does not patch their methods.
"""
import asyncio
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from legacy_evidence import internal_output
from pathlib import Path
import site
import sys
import time

import httpx
import psutil
import pytest

from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.agent_sessions import AgentSessions
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.tasks import canonical, digest
from test_agent_sessions import (CLI, CLI_SHA, COMMIT, KEY, MODEL, ROOT, VERSION,
                                 ModelWire, control, scene, tool, until)
from test_native_tool_management import api as tool_management_api, TOKEN as TOOL_HOST_TOKEN


def check(value, message):
    if not value:
        raise AssertionError(message)


def selected_classes():
    """Load preserved whole classes only for explicitly labelled old-source QA."""
    directory = os.environ.get('SOURCE63_BEFORE_SNAPSHOT')
    if not directory:
        return AgentSessions, NativeCapabilities
    base = Path(directory)
    parent = json.loads((base.parent / 'BASELINE.json').read_bytes())
    rows = {row['path']: row for row in parent['files']}
    classes = []
    for name, member, cls in [
        ('packages.agent_sessions.source63_before_service', 'packages/agent_sessions/service.py', 'AgentSessions'),
        ('packages.domain.source63_before_capabilities', 'packages/domain/native_capabilities.py', 'NativeCapabilities'),
    ]:
        source = base / member
        raw = source.read_bytes()
        check(len(raw) == rows[member]['bytes'] and hashlib.sha256(raw).hexdigest() == rows[member]['sha256'],
              'Exact preserved old source changed')
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        classes.append(getattr(module, cls))
    return tuple(classes)


def configure(scene, ident, *, fail_start=False, tool_name='read_lines'):
    script = scene['data'] / (ident + '-source63-mcp.py')
    script.write_bytes((ROOT / 'tests/agent_sessions_selected_mcp_source63_fixture.py').read_bytes())
    receipts = scene['data'] / (ident + '-process-receipts')
    receipts.mkdir()
    executable = Path(sys.executable).resolve()
    command = [str(executable), '-B', str(script), '--site-packages', site.getsitepackages()[0],
               '--receipt-dir', str(receipts), '--server-id', ident, '--tool-name', tool_name]
    if fail_start:
        command.append('--fail-start')
    saved = scene['manager'].configure_mcp({'operation_id': 'configure-' + ident, 'id': ident,
        'expected_revision': 0, 'configuration': {'type': 'local', 'command': command,
            'files_sha256': {str(executable): hashlib.sha256(executable.read_bytes()).hexdigest(),
                             str(script): hashlib.sha256(script.read_bytes()).hexdigest()},
            'enabled': True, 'timeout': 10000}})
    return saved, receipts, script


def owner63(scene, wire, cls):
    check(CLI.is_file() and hashlib.sha256(CLI.read_bytes()).hexdigest() == CLI_SHA[os.name],
          'The exact fixed original CLI is mandatory')
    config = {'schema': 'eeas.opencode-provider.v1', 'id': 'selected-mcp-source63', 'revision': 1,
        'version': VERSION, 'source_commit': COMMIT, 'executable': str(CLI), 'executable_sha256': CLI_SHA[os.name],
        'provider_id': 'eeas-session-provider', 'protocol': 'OPENAI_COMPATIBLE_CHAT_COMPLETIONS',
        'provider_url': wire.url, 'model_id': MODEL, 'secret_ref': 'secret:source63-wire', 'transport_kind': 'MOCK_WIRE'}
    registry = OpenCodeProviderRegistry(lambda *_: deepcopy(config), lambda _: KEY)
    value = cls(scene['store'], scene['data'], provider_registry=registry,
                workspaces=scene['workspaces'], capabilities=scene['manager'])
    bound = scene['bound']
    request = {'operation_id': 'open-source63-selected',
        'workspace_ref': {'id': bound['id'], 'revision': 1, 'binding_sha256': bound['binding_sha256']},
        'provider_profile_ref': {'id': config['id'], 'revision': 1, 'sha256': digest(config)},
        'capability_binding_sha256': scene['manager'].native_binding_snapshot()['sha256'],
        'title': 'Source63 selected official MCP', 'ttl_seconds': 120, 'max_calls': 4, 'max_output_tokens': 1024}
    return value, request


def starts(directory):
    return [json.loads(p.read_bytes()) for p in sorted(directory.glob('startup-*.json'))]


def save_evidence(name, value):
    directory = os.environ.get('EEAS_AGENT_SESSIONS_EVIDENCE')
    if directory:
        base = Path(directory)
        base = internal_output(base)
        base.mkdir(parents=True, exist_ok=True)
        (base / (name + '.json')).write_text(canonical(value), encoding='utf-8')


@pytest.fixture(scope='module', autouse=True)
def actual_source_inputs_unchanged():
    # The package's imported production modules, actual schema migrations, and
    # these two owned test inputs are read inputs. UI source outside this set
    # may change independently; this receipt never signs that unrelated source.
    paths = {Path(module.__file__).resolve() for module in list(sys.modules.values())
             if getattr(module, '__file__', None) and Path(module.__file__).suffix == '.py'
             and Path(module.__file__).resolve().is_relative_to(ROOT)
             and Path(module.__file__).resolve().relative_to(ROOT).parts[0] in {'apps', 'packages', 'tests'}}
    paths.update((ROOT / 'packages/domain/migrations').glob('*.sql'))
    paths.update([Path(__file__), ROOT / 'tests/agent_sessions_selected_mcp_source63_fixture.py'])
    before = {str(p.relative_to(ROOT)): {'bytes': p.stat().st_size,
              'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(paths)}
    save_evidence('source-inputs-before', {'scope': 'SELECTED_IMPORTED_REPOSITORY_PYTHON_MIGRATIONS_AND_TEST_FIXTURE',
                                         'files': before})
    yield
    after = {str(p.relative_to(ROOT)): {'bytes': p.stat().st_size,
             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(paths)}
    save_evidence('source-inputs-after', {'files': after, 'unchanged': before == after})
    check(before == after, 'An actual imported production/test/schema input changed during the run')


def prepare_scene(scene):
    cls, manager_cls = selected_classes()
    if os.environ.get('SOURCE63_BEFORE_SNAPSHOT'):
        scene['manager'] = manager_cls(scene['store'], scene['data'])
    return cls


async def call_once(value, scene, wire, opened, name, receipts):
    ident = opened['session_ref']['id']
    await value.send(scene['pid'], ident, {**control(opened, 'call-source63-selected'),
        'raw_text': 'Read real.md using only the explicitly selected MCP tool.'})
    pending = await until(value, scene, ident, lambda v: bool(v['pending_permissions']))
    check(len(pending['pending_permissions']) == 1, 'Exactly one real native tool permission is required')
    item = pending['pending_permissions'][0]
    check(item['native_request']['permission'] == name, 'Permission belongs to another tool')
    await value.reply(scene['pid'], ident, item['request_ref']['id'],
        {**control(opened, 'allow-source63-selected-once'), 'request_ref': item['request_ref'], 'reply': 'once'},
        kind='PERMISSION')
    final = await until(value, scene, ident, lambda v: v['active_operation_id'] is None)
    calls = [json.loads(line) for line in (receipts / 'calls.jsonl').read_text().splitlines()]
    check(len(calls) == 1 and calls[0]['sha256'] == hashlib.sha256(scene['original']).hexdigest(),
          'One actual tool call must read the original selected bytes')
    check(len(wire.requests) == 2, 'The original loop must use exactly two external-fixture RPCs')
    advertised = {entry['function']['name'] for entry in wire.requests[0]['body']['tools']}
    check(name in advertised, 'The selected real tool is not advertised by the original loop')
    return final, calls


@pytest.mark.parametrize('mode', ['legacy', 'structured'])
def test_one_selected_official_mcp_starts_calls_and_closes_without_starting_other(scene, mode):
    cls = prepare_scene(scene)
    saved, selected_receipts, _ = configure(scene, 'source63-a')
    other, other_receipts, _ = configure(scene, 'source63-b')
    name = 'source63-a_read_lines'
    wire = ModelWire([tool(name, {'path': 'real.md'}), 'SOURCE63_EXACT_SELECTED_TOOL_RETURNED']).start()

    async def run():
        selected = None
        if mode == 'structured':
            probe = await scene['manager'].probe_mcp('source63-a', {
                'config_ref': saved['config_ref'], 'allowed_effects': ['PROCESS_START'], 'network_mode': 'AS_CONFIGURED'})
            check(probe['status'] == 'INITIALIZED_AND_LISTED', 'Actual official MCP probe must finish')
            catalog = scene['manager'].tool_catalog()
            server = next(item for item in catalog['mcp_servers'] if item['id'] == 'source63-a')
            found = next(item for item in server['tools'] if item['id'] == name)
            selected = [{'server_id': server['id'], 'config_ref': server['config_ref'],
                'probe_ref': server['probe_ref'], 'tool_id': found['id'], 'name': found['name'],
                'input_schema_sha256': found['input_schema_sha256']}]
        previous = {row['pid'] for row in starts(selected_receipts)}
        value, request = owner63(scene, wire, cls)
        request.update(allowed_effects=['PROCESS_START'], mcp_tool_allowlist=[name], mcp_tool_selection=selected)
        context = opened = final = None
        try:
            opened = await value.open(scene['pid'], request)
            context = value.active[opened['session_ref']['id']]
            loaded = opened['capability_load']['mcp']
            actual = [row for row in starts(selected_receipts) if row['pid'] not in previous]
            unexpected = starts(other_receipts)
            before = {'scope': 'REAL_ORIGINAL_CLI_OFFICIAL_MCP_STARTUP_NO_MODEL_CALL_YET',
                'source_mode': 'PRESERVED_BEFORE' if os.environ.get('SOURCE63_BEFORE_SNAPSHOT') else 'CURRENT',
                'mode': mode, 'selected_ref': saved['config_ref'], 'unselected_ref': other['config_ref'],
                'selected_actual_starts': actual, 'unselected_actual_starts': unexpected,
                'capability_load': loaded, 'original_process_identity': opened['process_identity'],
                'model_rpc_count': len(wire.requests), 'paid_model_calls': 0}
            save_evidence('selected-startup-' + mode, before)
            check(bool(actual) and all(row['ppid'] == opened['process_identity']['pid'] for row in actual),
                  'Selected MCP must actually start beneath the exact original CLI')
            check(all(row['python_dont_write_bytecode'] is True for row in actual),
                  'Real MCP fixture child must run with its own explicit no-bytecode flag')
            check(not unexpected, 'Unselected enabled MCP started in the original CLI')
            check(loaded['source63-a']['loaded_in_original_session'], 'Selected original MCP must be loaded')
            unselected = loaded.get('source63-b', {})
            check(not unselected.get('configured_in_original') and not unselected.get('loaded_in_original_session'),
                  'Unselected source must not be configured/loaded in this owner')
            final, calls = await call_once(value, scene, wire, opened, name, selected_receipts)
            check(not starts(other_receipts), 'Calling selected MCP must not start another server')
            check(scene['actual'].read_bytes() == scene['original'], 'Original user file changed')
            save_evidence('selected-call-' + mode, {**before, 'status': 'PASS_SELECTED_REAL_CALL',
                'calls': calls, 'final': final, 'model_rpc_count': len(wire.requests)})
        finally:
            await value.close()
            if context:
                check(context['process'].returncode is not None and not value.active,
                      'Original process must actually stop and leave no registered owner')
                observed = starts(selected_receipts) + starts(other_receipts)
                deadline = time.monotonic() + 5
                while any(psutil.pid_exists(row['pid']) for row in observed) and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
                alive = [row['pid'] for row in observed if psutil.pid_exists(row['pid'])]
                save_evidence('selected-cleanup-' + mode, {'original_process_returncode': context['process'].returncode,
                    'owner_registered': bool(value.active), 'model_rpc_count': len(wire.requests),
                    'selected_start_pids': [r['pid'] for r in starts(selected_receipts)],
                    'unselected_start_pids': [r['pid'] for r in starts(other_receipts)],
                    'fixture_processes_still_alive': alive})
                check(not alive, 'A real official MCP fixture process survived original owner cleanup')
    try:
        asyncio.run(run())
    finally:
        wire.close()
        check(not wire.thread.is_alive(), 'External model fixture thread did not join')


def test_empty_allowlist_has_zero_mcp_without_process_grant_even_in_strict_offline(scene):
    cls = prepare_scene(scene)
    _, first, _ = configure(scene, 'source63-empty-a')
    _, second, _ = configure(scene, 'source63-empty-b')
    wire = ModelWire(['SOURCE63_ZERO_MCP_ORIGINAL_RESPONSE']).start()

    async def run():
        value, request = owner63(scene, wire, cls)
        request.update(allowed_effects=[], mcp_tool_allowlist=[], mcp_tool_selection=[], network_mode='STRICT_OFFLINE')
        try:
            opened = await value.open(scene['pid'], request)
            check(not starts(first) and not starts(second), 'Empty allowlist started an enabled MCP')
            check(all(not row['configured_in_original'] and not row['loaded_in_original_session']
                      for row in opened['capability_load']['mcp'].values()), 'Zero-MCP owner loaded an MCP')
            check(not wire.requests, 'Opening a zero-MCP owner must not make a model call')
            await value.send(scene['pid'], opened['session_ref']['id'],
                {**control(opened, 'empty-source63-message'), 'raw_text': 'Return a short answer without MCP.'})
            final = await until(value, scene, opened['session_ref']['id'], lambda v: v['active_operation_id'] is None)
            check(len(wire.requests) == 1 and not starts(first) and not starts(second), 'Zero-MCP original response scope changed')
            save_evidence('zero-mcp', {'status': 'PASS', 'final': final, 'mcp_start_count': 0,
                'allowed_effects': [], 'network_mode': 'STRICT_OFFLINE', 'model_rpc_count': 1, 'paid_model_calls': 0})
        finally:
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()
        check(not wire.thread.is_alive(), 'External model fixture thread did not join')


def test_unselected_colliding_faulty_mcp_does_not_block_selected_original_tool(scene):
    cls = prepare_scene(scene)
    _, selected_receipts, _ = configure(scene, 'source63')
    _, other_receipts, _ = configure(scene, 'source63_side', fail_start=True)
    name = 'source63_read_lines'  # Uniquely owned, despite the unselected server-prefix overlap.
    wire = ModelWire([tool(name, {'path': 'real.md'}), 'SOURCE63_UNSELECTED_FAILURE_ISOLATED']).start()

    async def run():
        value, request = owner63(scene, wire, cls)
        request.update(allowed_effects=['PROCESS_START'], mcp_tool_allowlist=[name])
        try:
            opened = await value.open(scene['pid'], request)
            check(not starts(other_receipts), 'Unselected colliding faulty server started')
            final, calls = await call_once(value, scene, wire, opened, name, selected_receipts)
            check(not starts(other_receipts), 'Unselected failure was activated by a selected tool call')
            save_evidence('unselected-collision-failure-isolation', {'status': 'PASS', 'final': final,
                'calls': calls, 'unselected_starts': [], 'model_rpc_count': len(wire.requests), 'paid_model_calls': 0})
        finally:
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()
        check(not wire.thread.is_alive(), 'External model fixture thread did not join')


def test_unselected_broken_bytes_and_new_revision_do_not_poison_selected_owner_reload(scene):
    cls = prepare_scene(scene)
    _, selected_receipts, _ = configure(scene, 'source63-kept')
    other, other_receipts, other_script = configure(scene, 'source63-unused')
    name = 'source63-kept_read_lines'
    wire = ModelWire([tool(name, {'path': 'real.md'}), 'FIRST_SELECTED_RESULT',
                      tool(name, {'path': 'real.md'}), 'SECOND_SELECTED_RESULT']).start()

    async def run():
        value, request = owner63(scene, wire, cls)
        request.update(allowed_effects=['PROCESS_START'], mcp_tool_allowlist=[name])
        try:
            opened = await value.open(scene['pid'], request)
            # An unselected source changes after the exact owner admission. Its
            # saved metadata remains readable, but its executable bytes are bad.
            other_script.write_text(other_script.read_text() + '\n# unselected concurrent byte change\n')
            app = tool_management_api(scene['manager'], scene['data'] / 'source63-api-host')
            app.state.agent_sessions = value  # The actual live original owner, not a fake or replacement.
            scope = {'project_id': scene['pid'], 'session_ref': opened['session_ref'], 'generation': opened['generation']}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                observed = await client.get('/api/desktop-host/capabilities/tools',
                    params={'scope': canonical(scope)}, headers={'X-EEAS-Desktop-Host': TOOL_HOST_TOKEN})
            check(observed.status_code == 200 and observed.json()['owner']['status'] == 'CURRENT',
                  'Actual capabilities HTTP route poisoned the selected owner with unselected broken bytes')
            save_evidence('unselected-broken-owner-http', {'status_code': observed.status_code,
                'scope': scope, 'response': observed.json(), 'model_rpc_count': len(wire.requests)})
            first, calls = await call_once(value, scene, wire, opened, name, selected_receipts)
            check(not starts(other_receipts), 'Unselected changed source was started')
            stopped = await value.controlled_disconnect(scene['pid'], opened['session_ref']['id'],
                control(opened, 'source63-disconnect-before-reload'))
            check(not stopped['connected_owner'], 'Original selected owner did not disconnect')
            old_configuration = scene['manager']._configuration('mcp', other['config_ref'])['configuration']
            updated = deepcopy(old_configuration)
            updated['files_sha256'][str(other_script)] = hashlib.sha256(other_script.read_bytes()).hexdigest()
            new_other = scene['manager'].configure_mcp({'operation_id': 'source63-unselected-new-revision',
                'id': 'source63-unused', 'expected_revision': 1, 'configuration': updated})
            loaded = await value.load(scene['pid'], opened['session_ref']['id'],
                {'operation_id': 'source63-reload-selected-only', 'session_ref': opened['session_ref']})
            restored = loaded['session']
            check(restored['generation'] == opened['generation'] + 1 and
                  restored['original_session_id'] == opened['original_session_id'] and len(wire.requests) == 2,
                  'Reload changed the original session or replayed a prior model message')
            check(not starts(other_receipts), 'Reload widened to the unselected newly revised source')
            await value.send(scene['pid'], restored['session_ref']['id'],
                {**control(restored, 'source63-after-reload-message'), 'raw_text': 'Use only the same exact selected MCP again.'})
            pending = await until(value, scene, restored['session_ref']['id'], lambda v: bool(v['pending_permissions']))
            item = pending['pending_permissions'][0]
            check(item['native_request']['permission'] == name, 'Reload borrowed an unrelated tool permission')
            await value.reply(scene['pid'], restored['session_ref']['id'], item['request_ref']['id'],
                {**control(restored, 'source63-after-reload-allow'), 'request_ref': item['request_ref'], 'reply': 'once'},
                kind='PERMISSION')
            final = await until(value, scene, restored['session_ref']['id'], lambda v: v['active_operation_id'] is None)
            check(len(wire.requests) == 4 and not starts(other_receipts), 'Reload dispatched an unexpected model/server request')
            check(scene['actual'].read_bytes() == scene['original'], 'Original selected file changed')
            save_evidence('unselected-change-reload', {'status': 'PASS', 'original_session_id': opened['original_session_id'],
                'generations': [opened['generation'], restored['generation']], 'old_unselected_ref': other['config_ref'],
                'new_unselected_ref': new_other['config_ref'], 'selected_starts': starts(selected_receipts),
                'unselected_starts': [], 'final': final, 'model_rpc_count': 4, 'automatic_model_replays': 0})
        finally:
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()
        check(not wire.thread.is_alive(), 'External model fixture thread did not join')
