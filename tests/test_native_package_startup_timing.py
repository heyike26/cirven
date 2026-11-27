"""Real payload bytes, guards and API entry; not Windows startup qualification."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from scripts import native_package_bootstrap as bootstrap


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / 'secret-filename-not-for-timing'
    entries = {'python':'runtime/python/python.exe', 'electron':'runtime/electron/electron.exe',
        'main':'apps/desktop-host/main.cjs', 'component_manifest':'toolchain/components/fixture.json',
        'readonly_manifest':'toolchain/policies/readonly.yaml'}
    rows = []
    for index, name in enumerate([*entries.values(), 'docs/private-note.md']):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        data = b'secret-body-not-for-timing' * (index + 1)
        path.write_bytes(data)
        rows.append({'path':name, 'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest(),
            'startup_critical':index < len(entries)})
    manifest = {'schema':bootstrap.SCHEMA, 'target':'windows-x64-py310', 'state':'DEVELOPMENT_PREVIEW',
        'entrypoints':entries, 'files':rows, 'pth_files':[]}
    (root / bootstrap.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    return root, manifest


def rows(timing):
    return {item['phase']:item for item in timing.records}


def test_real_full_and_partial_hashes_keep_selection_paths_and_exact_read_counts(payload, monkeypatch):
    root, manifest = payload
    original = bootstrap.sha
    observed = []
    def actual_sha(path, **kwargs):
        observed.append(Path(path).relative_to(root).as_posix())
        return original(path, **kwargs)
    monkeypatch.setattr(bootstrap, 'sha', actual_sha)
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-environment-not-for-timing')
    for full in (True, False):
        stream = io.StringIO(); timing = bootstrap.StartupTiming(mode='API', stream=stream)
        observed.clear()
        assert bootstrap.verify_package(root, full=full, timing=timing) == manifest
        selected = [row for row in manifest['files'] if full or row['startup_critical']]
        assert sorted(observed) == sorted(row['path'] for row in selected)
        facts = rows(timing)
        assert facts['PAYLOAD_PATH_METADATA']['file_count'] == len(manifest['files'])
        assert facts['PAYLOAD_PATH_METADATA']['byte_count'] == sum(row['bytes'] for row in manifest['files'])
        assert facts['PAYLOAD_HASH']['file_count'] == facts['PAYLOAD_HASH']['verified_file_count'] == len(selected)
        assert facts['PAYLOAD_HASH']['byte_count'] == sum(row['bytes'] for row in selected)
        assert facts['PAYLOAD_HASH']['measurement'] == 'ACCUMULATED_ACTIVE_TIME'
        assert facts['PAYLOAD_HASH']['verification_mode'] == ('FULL' if full else 'STARTUP_CRITICAL')
        assert facts['PACKAGE_VERIFICATION']['status'] == 'COMPLETED'
        assert all(row['duration_ms'] >= 0 for row in timing.records)
        assert all(row['pid'] == os.getpid() and row['component'] == 'PACKAGE_BOOTSTRAP'
            for row in timing.records)
        assert [json.loads(line) for line in stream.getvalue().splitlines()] == timing.records
        assert 'secret-' not in stream.getvalue() and not (root / 'user-data').exists()
    inert = root / manifest['files'][-1]['path']; inert.write_bytes(b'X' * inert.stat().st_size)
    assert bootstrap.verify_package(root, full=False) == manifest
    with pytest.raises(bootstrap.PackageError, match='hash changed'):
        bootstrap.verify_package(root, full=True)


@pytest.mark.parametrize('fault', ['length', 'symlink', 'hash'])
def test_actual_failure_keeps_original_guard_and_does_not_claim_verification_success(payload, fault, monkeypatch):
    root, manifest = payload
    path = root / manifest['files'][0]['path']
    if fault == 'length':
        path.write_bytes(b'x')
    elif fault == 'hash':
        path.write_bytes(b'x' * path.stat().st_size)
    else:
        other = root / 'original.bin'; path.rename(other); path.symlink_to(other)
    timing = bootstrap.StartupTiming(mode='API', stream=io.StringIO())
    original = bootstrap.sha; completed = []
    def observed_hash(path, **kwargs):
        actual = original(path, **kwargs)
        completed.append((Path(path).relative_to(root).as_posix(),actual))
        return actual
    monkeypatch.setattr(bootstrap,'sha',observed_hash)
    with pytest.raises(bootstrap.PackageError) as caught:
        bootstrap.verify_package(root, full=False, timing=timing)
    assert str(caught.value)  # Original diagnostic remains on the exception only.
    facts = rows(timing)
    assert facts['PACKAGE_VERIFICATION']['status'] == 'FAILED'
    assert facts['PACKAGE_VERIFICATION']['error_type'] == 'PackageError'
    expected = {row['path']:row['sha256'] for row in manifest['files']}
    assert facts['PAYLOAD_HASH']['verified_file_count'] == sum(expected[name] == actual for name,actual in completed)
    assert facts['PAYLOAD_HASH']['file_count'] == len(completed)
    assert facts['PAYLOAD_HASH']['verified_file_count'] < len(manifest['files'])
    assert 'secret-' not in timing.stream.getvalue() and not (root / 'user-data').exists()


def test_manifest_parse_failure_reports_no_payload_work_or_sensitive_error_text(payload):
    root, _manifest = payload
    (root / bootstrap.MANIFEST).write_bytes(b'{secret-content-invalid-json')
    timing = bootstrap.StartupTiming(mode='API', stream=io.StringIO())
    with pytest.raises(json.JSONDecodeError):
        bootstrap.verify_package(root, timing=timing)
    assert len(timing.records) == 1
    assert timing.records[0]['phase'] == 'MANIFEST_PARSE' and timing.records[0]['status'] == 'FAILED'
    assert 'secret-' not in timing.stream.getvalue() and not (root / 'user-data').exists()


def test_cancellation_counts_only_the_actual_first_chunk_and_preserves_pre_main_cancel(payload, monkeypatch):
    root, manifest = payload
    path = root / manifest['files'][0]['path']; path.write_bytes(b'z' * (3 * 1024 * 1024))
    manifest['files'][0].update(bytes=path.stat().st_size, sha256=bootstrap.sha(path))
    (root / bootstrap.MANIFEST).write_text(json.dumps(manifest))
    timing = bootstrap.StartupTiming(mode='ELECTRON')
    original = bootstrap.sha; chunks = []
    def actual_sha(path, **kwargs):
        observer = kwargs['on_read']
        def read(size):
            chunks.append(size); observer(size)
        return original(path, **{**kwargs, 'on_read':read})
    monkeypatch.setattr(bootstrap, 'sha', actual_sha)
    def progress(_done, _total):
        if chunks:
            raise bootstrap.StartupCancelled('original cancellation remains original')
    with pytest.raises(bootstrap.StartupCancelled, match='original cancellation'):
        # Exact one-file/one-chunk serial control, not the default-four schedule.
        bootstrap.verify_package(root, progress=progress, timing=timing, workers=1)
    assert chunks == [1024 * 1024]
    facts = rows(timing)
    assert facts['PAYLOAD_HASH']['byte_count'] == 1024 * 1024
    assert facts['PAYLOAD_HASH']['file_count'] == facts['PAYLOAD_HASH']['verified_file_count'] == 0
    assert facts['PAYLOAD_HASH']['status'] == facts['PACKAGE_VERIFICATION']['status'] == 'CANCELLED'
    assert timing.path is None and not (root / 'user-data').exists()


def test_verified_gui_log_is_small_local_and_timing_io_failure_never_changes_verification(payload, monkeypatch):
    root, manifest = payload
    class BrokenStream:
        def write(self, _value): raise OSError('secret IO failure')
    timing = bootstrap.StartupTiming(mode='API', stream=BrokenStream())
    assert bootstrap.verify_package(root, timing=timing) == manifest
    assert timing.write_error == 'STREAM_UNAVAILABLE'
    environment = bootstrap.runtime_environment(root, manifest, inherited={'EEAS_STARTUP_TIMING':'1'})
    assert 'EEAS_STARTUP_TIMING' not in environment
    timing.persist_verified(root, data_dir=environment['EEAS_DATA_DIR'])
    assert timing.path.parent == root / 'user-data/logs'
    for _index in range(50): timing.record('BOUNDED_TEST', 'ENTERED')
    persisted = [json.loads(line) for line in timing.path.read_text().splitlines()]
    assert persisted == timing.records and len(persisted) == 32
    assert timing.path.stat().st_size < 16384
    unavailable = bootstrap.StartupTiming(mode='ELECTRON')
    def deny(*_args, **_kwargs): raise bootstrap.PackageError('secret guarded path')
    monkeypatch.setattr(bootstrap, 'private_paths', deny)
    unavailable.persist_verified(root)
    assert unavailable.path is None and unavailable.write_error == 'LOG_UNAVAILABLE'


def test_actual_python_activation_records_success_and_refuses_unregistered_startup_code(payload, monkeypatch):
    root, manifest = payload
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'dont_write_bytecode', sys.dont_write_bytecode)
    timing = bootstrap.StartupTiming(mode='API', stream=io.StringIO())
    bootstrap._timed_call(timing, 'PYTHON_ACTIVATION', bootstrap.activate_python, root, manifest)
    assert timing.records[-1]['status'] == 'COMPLETED' and str(root) in sys.path
    startup = root / 'runtime/site-packages/unregistered.pth'; startup.parent.mkdir(parents=True, exist_ok=True)
    startup.write_text('import ignored_unregistered_module\n')
    monkeypatch.delenv('EEAS_STARTUP_TIMING', raising=False)
    with pytest.raises(bootstrap.PackageError, match='Unregistered'):
        bootstrap.execute_python(root, manifest, module='apps.api', args=['diagnose'], timing=timing)
    assert timing.records[-1]['status'] == 'FAILED'
    assert 'EEAS_STARTUP_TIMING' not in os.environ
    assert 'Unregistered' not in timing.stream.getvalue()


def test_original_api_module_entry_gets_explicit_flag_after_activation_without_stdout_timing(tmp_path):
    # Original production API's readonly diagnose command, real Python child and
    # local owned HTTP fixture. This does not substitute a fake API module or
    # claim the packaged Windows guard/import closure was executed.
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(b'{"status":"ORIGINAL_DIAGNOSE_FIXTURE"}')
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    root = Path(__file__).resolve().parents[1]
    code = ('import json,os,sys;from pathlib import Path;'
        'from scripts import native_package_bootstrap as b;'
        't=b.StartupTiming(mode="API",stream=sys.stderr);'
        '\ntry:b.execute_python(Path(sys.argv[1]),{"files":[],"pth_files":[]},module="apps.api",args=["diagnose"],timing=t)'
        '\nexcept SystemExit as e:print(json.dumps({"entry_exit":e.code,"timing_flag":os.environ.get("EEAS_STARTUP_TIMING")}));raise')
    environment = {key:os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'LANG') if key in os.environ}
    environment.update(EEAS_HOST='127.0.0.1', EEAS_PORT=str(server.server_port), EEAS_DATA_DIR=str(tmp_path / 'api-data'),
        EEAS_WORKER='0', PYTHONDONTWRITEBYTECODE='1')
    try:
        child = subprocess.run([sys.executable, '-B', '-c', code, str(root)], cwd=root,
            env=environment, text=True, capture_output=True, timeout=30)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    assert child.returncode == 0, child.stderr
    output = [json.loads(line) for line in child.stdout.splitlines()]
    assert output == [{'status':'ORIGINAL_DIAGNOSE_FIXTURE'}, {'entry_exit':0, 'timing_flag':'1'}]
    facts = [json.loads(line) for line in child.stderr.splitlines() if line.startswith('{')]
    own = [item for item in facts if item.get('component') == 'PACKAGE_BOOTSTRAP']
    assert [(item['phase'], item['status']) for item in own] == [('PYTHON_ACTIVATION', 'COMPLETED'), ('API_MODULE_EXECUTION', 'ENTERED')]
    assert not any(item['phase'] in {'API_IMPORT', 'CREATE_APP', 'UVICORN_RUN'} for item in own)
    assert not (tmp_path / 'api-data').exists()
