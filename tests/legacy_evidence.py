"""Select separate, writable test outputs while preserving frozen input paths."""
from functools import lru_cache
import os
from pathlib import Path, PureWindowsPath
from tempfile import gettempdir, mkdtemp


def internal_output(path):
    original = os.fspath(path)
    candidate = Path(path)
    if not candidate.is_absolute() or PureWindowsPath(original).drive.lower() == 'd:':
        raise ValueError('Select an absolute internal evidence directory outside D:')
    resolved = candidate.resolve()
    if PureWindowsPath(str(resolved)).drive.lower() == 'd:' or resolved.is_relative_to(Path('/mnt/d')):
        raise ValueError('Select an internal evidence directory outside D:')
    return resolved


@lru_cache(None)
def _temporary_directory(suite, root):
    parent = internal_output(root)
    parent.mkdir(parents=True, exist_ok=True)
    return Path(mkdtemp(dir=parent, prefix=suite+'-'))


def evidence_folder(suite, variable='EEAS_EVIDENCE_DIR'):
    selected = os.environ.get(variable)
    if selected:
        folder = internal_output(selected)
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    root = os.environ.get('EEAS_LEGACY_EVIDENCE_ROOT',gettempdir())
    return _temporary_directory(suite, root)


def chromium_executable():
    selected = os.environ.get('EEAS_TEST_CHROMIUM') or os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH')
    if selected:
        return Path(selected)
    cache = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH',Path.home()/'.cache/ms-playwright'))
    return cache/'chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell'
