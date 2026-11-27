"""Actual adapter/domain/SQLite with injected Win32 kernel, not Windows qualification."""
import copy
import ntpath
from pathlib import Path

import pytest

from packages.adapters import windows_workspace_handles as module
from packages.adapters.windows_workspace_handles import (
    FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_SHARE_DELETE, FILE_READ_ATTRIBUTES,
    FILE_ATTRIBUTE_REPARSE_POINT, GENERIC_READ, GENERIC_WRITE, WindowsWorkspaceHandles,
)
from packages.domain.file_continuity import FileContinuity
from packages.domain.local_workspaces import sha
from packages.domain.store import Conflict, InvalidInput, NotFound
from test_file_continuity import scene, report, docx
from test_windows_workspace_handles import OriginalHandleCounter, failure


def check(condition, message='managed saved-read requirement failed'):
    if not condition:
        raise AssertionError(message)


class SharingKernel(OriginalHandleCounter):
    """Existing kernel counter plus bidirectional Win32 handle-sharing rules."""
    def __init__(self):
        super().__init__()
        self.after_read = None
        self.after_attribute_open = None

    def open(self, path, access, share, disposition=3, *, directory=False, through=False):
        node = self.nodes.get(path.casefold())
        if node is not None and not directory:
            for selected in self.handles.values():
                if selected['node'] is not node:
                    continue
                for bit, flag in ((GENERIC_READ, FILE_SHARE_READ), (GENERIC_WRITE, FILE_SHARE_WRITE),
                                  (module.DELETE, FILE_SHARE_DELETE)):
                    if (access & bit and not selected['share'] & flag or
                            selected['access'] & bit and not share & flag):
                        self.calls.append(('sharing-conflict', path, access, share))
                        raise failure(32)
        handle = super().open(path, access, share, disposition, directory=directory, through=through)
        if access == FILE_READ_ATTRIBUTES and self.after_attribute_open:
            callback, self.after_attribute_open = self.after_attribute_open, None
            callback(self, handle)
        return handle

    def read(self, handle, amount):
        raw = super().read(handle, amount)
        if self.after_read:
            callback, self.after_read = self.after_read, None
            callback(self, handle)
        return raw

    def replace_leaf(self, path, **values):
        node = self.nodes[path.casefold()]
        for opened in self.handles.values():
            if opened['node'] is node and not opened['share'] & FILE_SHARE_DELETE:
                raise failure(32)
        self.add(path, **values)


@pytest.fixture
def native():
    kernel = SharingKernel()
    adapter = WindowsWorkspaceHandles(kernel)
    root, identity = adapter.root_identity('C:/selected')
    return kernel, adapter, {'root': root, 'root_identity': identity}


def held_writer(kernel, path=r'C:\selected\nested\source.txt'):
    return kernel.open(path, GENERIC_READ | GENERIC_WRITE,
                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)


def test_strict_default_keeps_real_sharing_counter_rejection_and_opt_in_keeps_user_writer(native):
    kernel, adapter, binding = native
    writer = held_writer(kernel)
    try:
        with pytest.raises(Conflict, match='busy'):
            adapter.read_regular(binding, 'nested/source.txt')
        check(set(kernel.handles) == {writer})
        check(adapter.read_regular(binding, 'nested/source.txt', shared_saved_document=True) == b'nested byte counter')
        check(set(kernel.handles) == {writer}, 'must not close the application writer')
        opens = [call for call in kernel.calls if call[0] == 'open']
        check(all(not call[3] & FILE_SHARE_DELETE for call in opens if call[5]), 'ancestor sharing must remain strict')
        check(any(call[2] == FILE_READ_ATTRIBUTES and call[3] == 7 for call in opens), 'must recheck the live leaf')
    finally:
        kernel.close(writer)
    check(not kernel.handles)


@pytest.mark.parametrize('value', [1, 'true', None, {}])
def test_shared_mode_requires_explicit_bool_without_opening_any_handle(native, value):
    kernel, adapter, binding = native
    before = len(kernel.calls)
    with pytest.raises(InvalidInput, match='boolean'):
        adapter.read_regular(binding, 'nested/source.txt', shared_saved_document=value)
    check(len(kernel.calls) == before and not kernel.handles)


@pytest.mark.parametrize('fault', ['replacement', 'missing', 'reparse', 'hardlink', 'final-outside',
                                   'in-place-write', 'size', 'creation', 'parent-reparse', 'late-write'])
def test_shared_read_rejects_concurrent_save_drift_and_releases_own_handles(native, fault):
    kernel, adapter, binding = native
    writer = held_writer(kernel)
    path = r'C:\selected\nested\source.txt'
    def changed(api, handle):
        if fault in {'replacement', 'reparse', 'hardlink', 'final-outside'}:
            values = {'raw': b'nested byte counter'}
            if fault == 'reparse': values['attributes'] = FILE_ATTRIBUTE_REPARSE_POINT
            if fault == 'hardlink': values['links'] = 2
            if fault == 'final-outside': values['final'] = r'C:\outside\secret.txt'
            api.replace_leaf(path, **values)
        elif fault == 'missing':
            api.nodes.pop(path.casefold())
        elif fault == 'in-place-write':
            api.handles[handle]['node']['write'] += 1
        elif fault == 'size':
            api.handles[handle]['node']['raw'] += b'new bytes'
        elif fault == 'creation':
            api.handles[handle]['node']['creation'] += 1
        elif fault == 'parent-reparse':
            api.nodes[r'c:\selected\nested']['attributes'] |= FILE_ATTRIBUTE_REPARSE_POINT
    if fault == 'late-write':
        kernel.after_attribute_open = lambda api, handle: api.handles[handle]['node'].__setitem__('write', 999)
    else:
        kernel.after_read = changed
    try:
        with pytest.raises((Conflict, InvalidInput, NotFound)):
            adapter.read_regular(binding, 'nested/source.txt', shared_saved_document=True)
        check(set(kernel.handles) == {writer})
    finally:
        kernel.close(writer)
    check(not kernel.handles)


def test_shared_read_still_pins_parent_namespace_and_enforces_size(native, monkeypatch):
    kernel, adapter, binding = native
    def rename_parent(api, _handle):
        with pytest.raises(OSError) as caught:
            api.external_rename(r'C:\selected\nested')
        check(caught.value.winerror == 32)
    kernel.after_read = rename_parent
    check(adapter.read_regular(binding, 'nested/source.txt', shared_saved_document=True) == b'nested byte counter')
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 8)
    with pytest.raises(InvalidInput, match='16 MiB'):
        adapter.read_regular(binding, 'nested/source.txt', shared_saved_document=True)
    check(not kernel.handles)


class ManagedNativeBoundary:
    """Only the OS path is mapped to the existing injected kernel; Store is real."""
    def __init__(self, parent, filename, kernel):
        self.parent, self.filename, self.kernel = str(parent), filename, kernel
        self.adapter = WindowsWorkspaceHandles(kernel)
        self.reads = []
        self.after_first_read = None

    def root_identity(self, path):
        check(path == self.parent, 'domain must not select another managed directory')
        return self.adapter.root_identity('C:/selected')

    def read_regular(self, binding, filename, **kwargs):
        check(filename == self.filename, 'domain must select its registered exact filename')
        self.reads.append(copy.deepcopy(kwargs))
        result = self.adapter.read_regular(binding, filename, **kwargs)
        if len(self.reads) == 1 and self.after_first_read:
            self.after_first_read()
        return result


def managed_scene(scene, monkeypatch):
    service, args, path, raw, reports = report(scene)
    kernel = SharingKernel()
    native_path = ntpath.join(r'C:\selected', path.name)
    kernel.add(native_path, raw=raw)
    boundary = ManagedNativeBoundary(path.parent, path.name, kernel)
    monkeypatch.setattr(service.ws, '_native_handles', lambda: boundary)
    registered = service.register_report(*args)
    boundary.reads.clear()
    return service, registered['link_ref'], path, raw, kernel, boundary, native_path


def test_real_store_managed_report_observes_saved_comments_while_writer_stays_open(scene, monkeypatch):
    service, reference, path, original, kernel, boundary, native_path = managed_scene(scene, monkeypatch)
    updated = docx('保留人工阈值42，只修订错误说法', '请修改这里，不覆盖人工内容')
    path.write_bytes(updated)
    kernel.nodes[native_path.casefold()]['raw'] = updated
    kernel.nodes[native_path.casefold()]['write'] += 1
    writer = held_writer(kernel, native_path)
    try:
        result = service.observe(reference)
        check(result['status'] == 'CHANGED' and result['current_sha256'] == sha(updated))
        check(result['parsed']['comments_total'] == 1 and '42' in result['parsed']['text_content'])
        saved = service.read_snapshot_bytes(reference, result['snapshot_ref'])
        check(saved['raw'] == updated, 'the actual SQLite/FS snapshot must contain the saved bytes')
        check(path.read_bytes() == updated and original != updated)
        check(len(boundary.reads) == 2 and all(item == {'shared_saved_document': True} for item in boundary.reads))
        check(set(kernel.handles) == {writer})
    finally:
        kernel.close(writer)
    check(not kernel.handles)


def test_real_store_observe_double_read_rejects_two_individually_stable_saved_versions(scene, monkeypatch):
    service, reference, path, original, kernel, boundary, native_path = managed_scene(scene, monkeypatch)
    updated = docx('第二次保存的人工内容', '另一版批注')
    boundary.after_first_read = lambda: kernel.replace_leaf(native_path, raw=updated)
    with pytest.raises(Conflict, match='SOURCE_DRIFT'):
        service.observe(reference)
    snapshots = service._path('snapshots', reference['id'])
    check(not snapshots.exists(), 'a drifting observation must not be recorded as a frozen snapshot')
    check(path.read_bytes() == original and not kernel.handles)
