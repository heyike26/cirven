"""Observable sharing/lifetime protocol tests; native kernel has a separate gate."""
import pytest
from packages.adapters.windows_workspace_handles import (
    WindowsWorkspaceHandles, WindowsFileBusy, GENERIC_READ, GENERIC_WRITE, DELETE,
    FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_SHARE_DELETE,
)
from packages.domain.store import Conflict, InvalidInput
from test_windows_workspace_handles import OriginalHandleCounter, failure


class SharingCounter(OriginalHandleCounter):
    def __init__(self):
        super().__init__(); self.physical_bytes = 0

    def open(self, path, access, share, disposition=3, **kwargs):
        node = self.nodes.get(path.casefold())
        for held in self.handles.values():
            if node is None or held['node'] is not node:
                continue
            for desired, allowed in [(access, held['share']), (held['access'], share)]:
                if ((desired & (GENERIC_READ | 1) and not allowed & FILE_SHARE_READ) or
                    (desired & (GENERIC_WRITE | 2) and not allowed & FILE_SHARE_WRITE) or
                    (desired & DELETE and not allowed & FILE_SHARE_DELETE)):
                    raise failure(32)
        return super().open(path, access, share, disposition, **kwargs)

    def read(self, handle, amount):
        raw = super().read(handle, amount); self.physical_bytes += len(raw); return raw


def selected():
    api = SharingCounter(); adapter = WindowsWorkspaceHandles(api)
    root, identity = adapter.root_identity('C:/selected')
    return api, adapter, {'root': root, 'root_identity': identity}


def test_held_proof_reuses_bytes_but_rechecks_identity_and_keeps_writers_out():
    api, adapter, binding = selected()
    with adapter.read_proof_scope(binding) as lease:
        raw, reused = lease.read_regular('original.txt', max_bytes=1024)
        assert raw == b'original actual byte counter' and not reused
        consumed = api.physical_bytes
        assert lease.read_regular('original.txt', max_bytes=1024) == (raw, True)
        assert api.physical_bytes == consumed
        reader = api.open('C:\\selected\\original.txt', GENERIC_READ, 7); api.close(reader)
        for access in [GENERIC_WRITE, DELETE]:
            with pytest.raises(OSError):api.open('C:\\selected\\original.txt', access, 7)
        with pytest.raises(OSError):api.external_rename('C:\\selected')
        api.nodes['c:\\selected\\original.txt']['inode'] += 1
        with pytest.raises(Conflict):lease.read_regular('original.txt', max_bytes=1024)
    assert not api.handles


@pytest.mark.parametrize('access', [GENERIC_WRITE, DELETE])
def test_preexisting_writer_or_delete_handle_blocks_new_proof_without_leaking(access):
    api, adapter, binding = selected()
    writer = api.open('C:\\selected\\original.txt', access, 7)
    with adapter.read_proof_scope(binding) as lease:
        with pytest.raises(WindowsFileBusy):lease.read_regular('original.txt', max_bytes=1024)
    assert list(api.handles) == [writer]
    api.close(writer); assert not api.handles


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt, SystemExit])
def test_failure_cancel_and_shutdown_unwind_release_all_handles(error_type):
    api, adapter, binding = selected()
    with pytest.raises(error_type):
        with adapter.read_proof_scope(binding) as lease:
            lease.read_regular('original.txt', max_bytes=1024)
            lease.read_regular('nested/source.txt', max_bytes=1024)
            raise error_type('owned scope unwind')
    assert not api.handles
    writer = api.open('C:\\selected\\original.txt', GENERIC_WRITE, 7); api.close(writer)
    assert not api.handles


def test_capacity_refuses_uncovered_path_and_releases_existing_proof():
    api, adapter, binding = selected()
    with adapter.read_proof_scope(binding, max_files=1) as lease:
        lease.read_regular('original.txt', max_bytes=1024)
        with pytest.raises(InvalidInput):lease.read_regular('nested/source.txt', max_bytes=1024)
    assert not api.handles
    with adapter.read_proof_scope(binding, max_handles=3) as lease:
        with pytest.raises(InvalidInput):lease.read_regular('original.txt', max_bytes=1024)
    assert not api.handles
