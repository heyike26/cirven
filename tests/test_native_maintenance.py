"""Maintenance handoff guards; native GUI behavior is qualified on Windows."""
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from scripts import native_maintenance as maintenance
from scripts import package_native_companion as package


def test_standalone_entry_imports_exact_bootstrap_without_script_namespace_or_site():
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import runpy,sys;runpy.run_path(sys.argv[1])', str(Path(maintenance.__file__).resolve())],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_package_includes_complete_maintenance_entry_and_matching_guides():
    required = {'scripts/native_maintenance.py', 'scripts/native_maintenance.ps1', 'scripts/native_user_state.py'}
    assert required <= set(package.SOURCE_EXTRAS)
    launch = package.package_launchers()['MAINTAIN_CIRVEN.cmd'].decode('ascii')
    assert 'pythonw.exe" -B "%~dp0scripts\\native_maintenance.py"' in launch
    assert '--electron' not in launch
    guides = package.package_instructions()
    for name in ('README_FIRST.md', 'README_FIRST.en.md'):
        assert b'MAINTAIN_CIRVEN.cmd' in guides[name]
    path = Path(maintenance.__file__).with_suffix('.ps1')
    assert path.read_bytes().startswith(b'\xef\xbb\xbf')


@pytest.mark.parametrize('outcome', ['pass', 'cancel', 'corrupt'])
def test_only_verified_maintenance_handoff_exits_verifier_without_starting_app(monkeypatch, tmp_path, outcome):
    events = []
    class Progress:
        def __init__(self, **_kwargs):
            self.copy = SimpleNamespace(locale='en')
        def close(self):
            events.append('close-progress')
        def update(self, completed, total):
            assert (completed, total) == (1, 1)
    def verify(root, **kwargs):
        events.append('verify')
        assert root == tmp_path
        # Real bootstrap consumes a callback, not the progress window object.
        assert callable(kwargs['progress'])
        kwargs['progress'](1, 1)
        if outcome == 'cancel':
            raise maintenance.StartupCancelled('user cancelled')
        if outcome == 'corrupt':
            raise ValueError('corrupt payload')
    def spawn(argv, **kwargs):
        assert events == ['verify', 'close-progress']
        assert argv[-4:] == ['-Installation', str(tmp_path), '-Language', 'en']
        assert str(tmp_path / 'scripts/native_maintenance.ps1') in argv
        assert kwargs['cwd'] == tmp_path
        events.append('native-window-handoff')
        # No wait() method: the package interpreter must exit, not remain an owner.
        return object()
    monkeypatch.setattr(maintenance, '__file__', str(tmp_path / 'scripts/native_maintenance.py'))
    monkeypatch.setattr(maintenance, 'os', SimpleNamespace(name='nt', environ={'SystemRoot': str(tmp_path / 'Windows')}))
    monkeypatch.setattr(maintenance, 'StartupProgress', Progress)
    monkeypatch.setattr(maintenance, 'verify_package', verify)
    monkeypatch.setattr(maintenance, 'subprocess', SimpleNamespace(Popen=spawn, CREATE_NO_WINDOW=123))
    import ctypes
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(
        MessageBoxW=lambda *_args: events.append('failure-dialog'))), raising=False)
    assert maintenance.main() == (1 if outcome == 'corrupt' else 0)
    assert events == ({'pass': ['verify', 'close-progress', 'native-window-handoff'],
        'cancel': ['verify', 'close-progress'],
        'corrupt': ['verify', 'failure-dialog', 'close-progress']}[outcome])
    assert not (tmp_path / 'user-data').exists()
