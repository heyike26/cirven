from pathlib import PureWindowsPath

from packages.adapters.claude_sdk import ClaudeBridge
from packages.domain.tasks import TaskService, digest, source_hashes


class WindowsNames:
    """Read real files but expose Windows Path's native relative-name flavor."""
    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return str(self.path)

    def __truediv__(self, suffix):
        return WindowsNames(self.path / suffix)

    def rglob(self, pattern):
        return (WindowsNames(path) for path in self.path.rglob(pattern))

    def relative_to(self, root):
        return PureWindowsPath(self.path.relative_to(root.path))

    def __getattr__(self, name):
        return getattr(self.path, name)


def test_task_and_native_scope_hash_real_source_bytes_with_portable_relative_names(tmp_path):
    root = tmp_path / '原创 中文源'
    for relative, data in [('apps/api/original.py', b'original = 1\n'),
                           ('packages/original.py', b'original = 2\n'),
                           ('profiles/stack.lock.json', b'{"original":true}')]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    native = TaskService(tmp_path / 'posix/eeas.sqlite3', root)
    foreign = TaskService(tmp_path / 'windows/eeas.sqlite3', WindowsNames(root))
    assert source_hashes(root) == source_hashes(WindowsNames(root))
    assert native.source_hash == foreign.source_hash
    request = {'needs_tools': False, 'allowed_write_paths': [], 'source_files': {}, 'document_refs': []}
    assert ClaudeBridge(foreign, tmp_path / 'windows')._scope('original-unused-project', request)['source_hash'] == native.source_hash
    (root / 'packages/original.py').write_bytes(b'original = 3\n')
    assert digest(source_hashes(root)) != native.source_hash
    # Windows path flavor is a portability contract probe, not a native host qualification.
