"""First-launch feedback uses real files; Linux evidence does not qualify Win32 UI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import types

import pytest

from scripts import native_package_bootstrap as bootstrap


@pytest.fixture
def startup_bundle(tmp_path):
    root = tmp_path / '可移动 EEAS 预览'
    entrypoints = {'python':'runtime/python/python.exe', 'electron':'runtime/electron/electron.exe',
        'main':'apps/desktop-host/main.cjs', 'component_manifest':'toolchain/components/fixture.json',
        'readonly_manifest':'toolchain/policies/readonly.yaml'}
    files = []
    for name in entrypoints.values():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = ('original fixture ' + name + '\n').encode()
        path.write_bytes(payload)
        files.append({'path':name, 'bytes':len(payload), 'sha256':hashlib.sha256(payload).hexdigest(),
            'startup_critical':True})
    manifest = {'schema':bootstrap.SCHEMA, 'target':'windows-x64-py310', 'state':'DEVELOPMENT_PREVIEW',
        'entrypoints':entrypoints, 'files':files}
    (root / bootstrap.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    return root, manifest


def own_launcher(monkeypatch, root):
    monkeypatch.setattr(bootstrap, '__file__', str(root / 'scripts/native_package_bootstrap.py'))
    def forbidden_spawn(*args, **kwargs):
        pytest.fail('Main must not start on cancellation, tamper, or a child-only path')
    monkeypatch.setattr(bootstrap.subprocess, 'call', forbidden_spawn)


def test_observed_full_verification_reads_every_actual_payload_hash(startup_bundle, monkeypatch):
    root, manifest = startup_bundle
    original = bootstrap.sha
    hashes, events = [], []
    def observed_hash(path, **kwargs):
        hashes.append(Path(path).relative_to(root).as_posix())
        return original(path, **kwargs)
    monkeypatch.setattr(bootstrap, 'sha', observed_hash)
    assert bootstrap.verify_package(root, progress=lambda done, total:events.append((done,total))) == manifest
    assert sorted(hashes) == sorted(row['path'] for row in manifest['files'])
    assert events[0] == (0,5) and events[-1] == (5,5)
    assert all(first[0] <= second[0] for first,second in zip(events,events[1:]))
    assert len(events) >= 2  # Caller pumps at completion/finite waits, not in workers.


def test_user_can_cancel_before_first_payload_read_and_main_never_spawns(startup_bundle, monkeypatch):
    root, _manifest = startup_bundle
    own_launcher(monkeypatch, root)
    def user_cancel(self, completed, total):
        self.cancelled = True
        raise bootstrap.StartupCancelled('original user cancellation')
    monkeypatch.setattr(bootstrap.StartupProgress, 'update', user_cancel)
    monkeypatch.setattr(bootstrap, 'sha', lambda *_args, **_kwargs:pytest.fail('No payload read after cancel'))
    with pytest.raises(bootstrap.StartupCancelled, match='original user cancellation'):
        bootstrap.main(['--electron'])
    assert not (root / 'user-data').exists()


def test_large_file_cancellation_interrupts_after_one_actual_mebibyte(startup_bundle, monkeypatch):
    root, manifest = startup_bundle
    payload = root / manifest['entrypoints']['python']
    payload.write_bytes(b'x' * (3 * 1024 * 1024))
    manifest['files'][0].update(bytes=payload.stat().st_size, sha256=bootstrap.sha(payload))
    (root / bootstrap.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    original_open = Path.open
    reads = []
    class CountingReader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def read(self, size):
            data = self.stream.read(size)
            reads.append(len(data))
            return data
    def counted_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return CountingReader(stream) if path == payload else stream
    monkeypatch.setattr(Path, 'open', counted_open)
    def cancel_after_bytes(done, total):
        if reads:
            raise bootstrap.StartupCancelled('cancelled during a real file read')
    with pytest.raises(bootstrap.StartupCancelled, match='during a real file read'):
        # Serial control retains the exact first-chunk boundary; the four-worker
        # cancellation/join path is exercised separately with four live reads.
        bootstrap.verify_package(root, progress=cancel_after_bytes, workers=1)
    assert reads == [1024 * 1024]


def test_console_is_immediate_flushed_and_cancellation_cannot_report_success(capsys):
    progress = bootstrap.StartupProgress(enabled=True)
    first = capsys.readouterr()
    assert first.out == '' and bootstrap.StartupCopy()('consoleStarting') in first.err
    progress.update(0,10)
    assert '0 / 10' in capsys.readouterr().err
    progress.cancelled = True
    with pytest.raises(bootstrap.StartupCancelled):
        progress.finish()
    progress.close()
    assert bootstrap.StartupCopy()('verified') not in capsys.readouterr().err


def test_same_size_tamper_preserves_original_exception_and_startup_receipt(startup_bundle, monkeypatch, capsys):
    root, manifest = startup_bundle
    own_launcher(monkeypatch, root)
    changed = root / manifest['entrypoints']['python']
    changed.write_bytes(b'x' * changed.stat().st_size)
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-secret-must-not-appear')
    with pytest.raises(bootstrap.PackageError, match='Package payload hash changed') as caught:
        bootstrap.main(['--electron'])
    receipts = list((root / 'user-data/logs').glob('startup-error-*.json'))
    assert len(receipts) == 1
    value = json.loads(receipts[0].read_text(encoding='utf-8'))
    assert value['status'] == 'STARTUP_FAILED'
    assert value['error']['message'] == str(caught.value)
    assert 'raise PackageError' in value['error']['traceback']
    assert 'ambient-secret' not in receipts[0].read_text(encoding='utf-8')
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('arguments', [['--verify'], ['--module','packages.desktop.mcp_server'],
    ['--api'], ['--mcp','packages.planning.mcp_server'], ['--script','packages/runner/supervisor.py']])
def test_child_and_verify_modes_never_open_progress_or_pollute_protocol(startup_bundle, monkeypatch, capsys, arguments):
    root, manifest = startup_bundle
    own_launcher(monkeypatch, root)
    def forbidden_native(self):
        pytest.fail('Child modes may not open a startup GUI')
    monkeypatch.setattr(bootstrap.StartupProgress, '_open_native', forbidden_native)
    if arguments == ['--verify']:
        assert bootstrap.main(arguments) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)['status'] == 'PAYLOAD_HASHES_VERIFIED'
        assert captured.err == ''
    else:
        changed = root / manifest['entrypoints']['python']
        changed.write_bytes(b'x' * changed.stat().st_size)
        with pytest.raises(bootstrap.PackageError):
            bootstrap.main(arguments)
        captured = capsys.readouterr()
        assert captured.out == ''
        if arguments == ['--api']:
            events = [json.loads(line) for line in captured.err.splitlines()]
            assert events and all(row['schema'] == 'eeas.startup-timing.v1' for row in events)
            assert events[-1]['phase'] == 'PACKAGE_VERIFICATION' and events[-1]['status'] == 'FAILED'
        else:
            assert captured.err == ''
    assert not (root / 'user-data').exists()


def test_native_ui_unavailable_falls_back_without_bypassing_verification(startup_bundle, monkeypatch, capsys):
    root, manifest = startup_bundle
    def unavailable(self):
        raise OSError('No display fixture')
    # A module-local platform adapter leaves pathlib's actual platform intact.
    with monkeypatch.context() as native_attempt:
        native_attempt.setattr(bootstrap, 'os', types.SimpleNamespace(name='nt'))
        native_attempt.setattr(bootstrap.StartupProgress, '_open_native', unavailable)
        progress = bootstrap.StartupProgress(enabled=True)
    assert progress._native_error is True and progress._window is None
    assert bootstrap.verify_package(root, progress=progress.update) == manifest
    progress.finish()
    assert progress.copy('verified') in capsys.readouterr().err


def test_diagnostic_write_failure_cannot_replace_original_error(startup_bundle, monkeypatch):
    root, _manifest = startup_bundle
    original = bootstrap.PackageError('original hash failure must survive')
    def unavailable(*args, **kwargs):
        raise OSError('original readonly evidence directory fixture')
    monkeypatch.setattr(bootstrap, 'private_paths', unavailable)
    assert bootstrap.report_startup_failure(root, original) is None
    assert str(original) == 'original hash failure must survive'


def test_pythonw_failure_uses_visible_native_error_even_without_console(startup_bundle, monkeypatch):
    import ctypes
    root, _manifest = startup_bundle
    dialogs = []
    def message_box(*arguments):
        dialogs.append(arguments)
        return 1
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs:types.SimpleNamespace(MessageBoxW=message_box), raising=False)
    # This fixture has no D/E Windows drive, so no unsupported-drive write occurs;
    # the error must still be visible when pythonw has no stderr handle.
    monkeypatch.setattr(bootstrap, 'os', types.SimpleNamespace(name='nt'))
    original = bootstrap.PackageError('original missing payload')
    assert bootstrap.report_startup_failure(root, original) is None
    assert len(dialogs) == 1
    assert 'original missing payload' in dialogs[0][1] and dialogs[0][2] == bootstrap.StartupCopy(root)('failedTitle')
    assert not (root / 'user-data').exists()


def test_implicit_bridge_mcp_launch_stays_silent_on_failure(startup_bundle, monkeypatch, capsys):
    root, manifest = startup_bundle
    own_launcher(monkeypatch, root)
    for name in ('EEAS_DESKTOP_BRIDGE_URL','EEAS_DESKTOP_BRIDGE_TOKEN',
            'EEAS_DESKTOP_EXECUTION_ID','EEAS_DESKTOP_REQUEST_SHA256'):
        monkeypatch.setenv(name, 'fixture-opaque-owned-binding')
    changed = root / manifest['entrypoints']['python']
    changed.write_bytes(b'x' * changed.stat().st_size)
    with pytest.raises(bootstrap.PackageError):
        bootstrap.main([])
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == ''
    assert not (root / 'user-data').exists()
