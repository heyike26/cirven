"""Exact new plugin plan/copy/refresh closure, not a Windows runtime claim."""
import hashlib
import shutil
import pytest
from scripts import package_native_companion as builder
from scripts.native_package_bootstrap import PackageError, verify_package, MCP_MODULES
from test_native_package_ide_capability import scene, write

PREFIX = 'toolchain/opencode-plugins/native-review-context/'
MEMBERS = {PREFIX + name for name in ('index.mjs', 'package.json', 'README.md', 'manifest.json')}


def check(value):
    if not value: raise AssertionError('Native review package contract differs')


def test_actual_plan_copy_and_refresh_keep_exact_required_plugin_without_user_data_or_runtime_mutation(scene, tmp_path):
    source, runtime, structural = scene
    write(source / PREFIX / 'tests' / 'own-test.mjs', b'not production')
    plan = builder.make_refresh_plan(source, runtime)
    check({name for name in plan['jobs'] if name.startswith(PREFIX)} == MEMBERS)
    output = tmp_path / 'ordinary-output'
    before = builder.copy_plan({**structural, 'jobs': {**structural['jobs'], **plan['jobs']}}, output)
    user = write(output / 'user-data' / 'draft.txt', b'keep private draft')
    runtime_rows = [row for row in before['files'] if row['path'].startswith('runtime/')]
    actual = builder.refresh_source(builder.make_refresh_plan(source, runtime), output)
    check(verify_package(output) == actual)
    check([row for row in actual['files'] if row['path'].startswith('runtime/')] == runtime_rows)
    check(user.read_bytes() == b'keep private draft')
    rows = {row['path']: row for row in actual['files']}
    for name in MEMBERS:
        check(rows[name]['sha256'] == hashlib.sha256((source / name).read_bytes()).hexdigest())
    check('packages.adapters.opencode_native_review' in MCP_MODULES)


@pytest.mark.parametrize('fault', ['tree', 'member', 'hash', 'alias', 'link'])
def test_required_native_review_closure_refuses_missing_changed_or_aliased_files(scene, tmp_path, fault):
    source, runtime, _ = scene
    root = source / PREFIX
    if fault == 'tree': shutil.rmtree(root)
    elif fault == 'member': (root / 'index.mjs').unlink()
    elif fault == 'hash': (root / 'index.mjs').write_bytes(b'changed')
    elif fault == 'alias': write(root / 'INDEX.MJS', b'alias')
    else:
        target = tmp_path / 'linked.mjs'; (root / 'index.mjs').rename(target); (root / 'index.mjs').symlink_to(target)
    with pytest.raises(PackageError): builder.make_refresh_plan(source, runtime)


def test_selected_partial_plugin_cannot_be_copied_or_get_a_success_manifest(scene, tmp_path):
    source, runtime, structural = scene
    plan = builder.make_refresh_plan(source, runtime)
    jobs = {**structural['jobs'], **plan['jobs']}; jobs.pop(PREFIX + 'index.mjs')
    with pytest.raises(PackageError): builder.copy_plan({**structural, 'jobs': jobs}, tmp_path / 'partial')
    check(not (tmp_path / 'partial' / 'PACKAGE_MANIFEST.json').exists())


def test_legacy_generic_mcp_arguments_are_rejected_by_actual_private_adapter(tmp_path):
    import os, subprocess, sys
    from pathlib import Path
    repository = Path(__file__).resolve().parents[1]
    child = subprocess.run([sys.executable, '-B', str(repository / 'packages/adapters/opencode_native_review.py'),
        '--data-dir', str(tmp_path), '--project-id', 'diagnostic-project', '--source-root', str(repository)],
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, timeout=20)
    (tmp_path / 'original-argparse.stderr').write_bytes(child.stderr)
    check(child.returncode == 2 and b'--scope' in child.stderr)
    check(not list(tmp_path.rglob('scope.json')))


def test_diagnostic_real_private_mcp_scope_initialize_list_with_no_broker_or_tool_call(scene, tmp_path, monkeypatch):
    import asyncio, json, os, sys
    from pathlib import Path
    from scripts import native_package_bootstrap as boot
    source, _runtime, structural = scene
    write(source / 'profiles/stack.lock.json', b'{}')
    directory = tmp_path / 'diagnostic'; directory.mkdir()
    stub = tmp_path / 'unchanged-three-mcp-boundary.py'
    stub.write_text('from mcp.server import MCPServer\ns=MCPServer("unchanged-three-modules-controlled-boundary",version="1.0.0")\ns.run(transport="stdio")\n')
    commands = []
    def command(root, module, args):
        commands.append((module, list(args)))
        if module == 'packages.adapters.opencode_native_review':
            return [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'packages/adapters/opencode_native_review.py'), *args]
        return [sys.executable, '-B', str(stub)]
    monkeypatch.setattr(boot, 'python_module_command', command)
    monkeypatch.setattr(boot, 'runtime_environment', lambda *_a, **_k: {k:v for k,v in os.environ.items() if k in {'PATH','PYTHONPATH','PYTHONDONTWRITEBYTECODE','TMP','TEMP','TMPDIR'}})
    results = asyncio.run(boot.diagnose_mcp(source, structural, directory, timeout_seconds=20))
    check({x['module'] for x in results} == boot.MCP_MODULES and len(results) == 4)
    private = next(x for x in results if x['module'] == 'packages.adapters.opencode_native_review')
    check(private['tools'] == ['acceptance_coverage'] and private['tool_calls'] == 0)
    tail = next(args for module,args in commands if module == private['module'])
    check(len(tail) == 2 and tail[0] == '--scope')
    scope_path = Path(tail[1]); scope = json.loads(scope_path.read_text())
    check(scope['native_session_id'] is None and scope['broker_url'] == 'http://127.0.0.1:9')
    check(scope_path.is_relative_to(directory) and scope_path.stat().st_nlink == 1)
    receipt = json.loads(scope_path.with_name('mcp-listed.json').read_text())
    check(receipt['initial_scope_sha256'] == scope['scope_sha256'] and [x['name'] for x in receipt['tools']] == ['acceptance_coverage'])
    check(json.loads((directory/'phase.json').read_text())['stage'] == 'ALL_MCP_CHILDREN_CLOSED')
    check(not scope_path.with_name('ready.json').exists())
