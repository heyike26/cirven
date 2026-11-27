"""Calls the real helper with original Win32 API doubles; NOT native Windows.

The doubles model documented sharing and handle ownership; actual byte reads
and descriptor close checks use tiny local POSIX files. No ABI/NTFS claim.
"""
import ctypes
import errno
import os
from pathlib import PureWindowsPath
import sys
from types import SimpleNamespace

import pytest

from packages.adapters.settings_profile import SettingsRouteError, _windows_open_nofollow


class Function:
    def __init__(self, action):
        self.action = action
        self.calls = []
    def __call__(self, *args):
        self.calls.append(args)
        return self.action(*args)


class OriginalAPI:
    """Original semantic model using real temporary file descriptors."""
    def __init__(self, selected):
        self.selected = selected
        self.descriptors = []
        self.attributes = 0x80
        self.info_ok = True
        self.create_failure = False
        self.invalid_handle = ctypes.c_void_p(-1).value
        self.existing_writer = False
        self.transfer_failure = False
        self.share = None
        self.handle_fd = {}
        self.CreateFileW = Function(self.create)
        self.GetFileInformationByHandle = Function(self.information)
        self.CloseHandle = Function(self.close)
        self.open_osfhandle = Function(self.transfer)
    def create(self, path, access, share, security, disposition, flags, template):
        self.share = share
        if self.create_failure or self.existing_writer and not share & 0x2:
            return self.invalid_handle
        descriptor = os.open(self.selected, os.O_RDONLY)
        self.descriptors.append(descriptor)
        handle = 7000 + len(self.descriptors)
        self.handle_fd[handle] = descriptor
        return handle
    def information(self, handle, pointer):
        pointer._obj.attributes = self.attributes
        return self.info_ok
    def transfer(self, handle, flags):
        if self.transfer_failure:
            raise OSError('original osfhandle transfer failure')
        return self.handle_fd[handle]
    def close(self, handle):
        os.close(self.handle_fd[handle])
        return True
    def try_writer(self):
        if not self.share & 0x2:
            return False
        self.selected.write_bytes(b'ORIGINAL_VERSION_B')
        return True
    def try_atomic_replace(self):
        if not self.share & 0x4:
            return False
        replacement = self.selected.with_name('replacement.json')
        replacement.write_bytes(b'ORIGINAL_VERSION_B')
        replacement.replace(self.selected)
        return True
    def all_closed(self):
        for descriptor in self.descriptors:
            try:
                os.fstat(descriptor)
            except OSError as error:
                if error.errno == errno.EBADF:
                    continue
                raise
            return False
        return True


@pytest.fixture
def original_api(tmp_path, monkeypatch):
    selected = tmp_path / '原创批准配置.json'
    selected.write_bytes(b'ORIGINAL_VERSION_A')
    api = OriginalAPI(selected)
    libraries = []
    def load(name, **kwargs):
        libraries.append((name, kwargs))
        return api
    monkeypatch.setattr(ctypes, 'WinDLL', load, raising=False)
    monkeypatch.setattr(os, 'O_BINARY', 0x8000, raising=False)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(open_osfhandle=api.open_osfhandle))
    try:
        yield api, PureWindowsPath(r'C:\公开夹具 中文空格\approved.json'), libraries
    finally:
        for descriptor in api.descriptors:
            try:
                os.close(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise


def test_actual_windows_helper_blocks_contract_writer_and_transfers_fd(original_api):
    api, path, libraries = original_api
    descriptor = _windows_open_nofollow(path)
    try:
        writer_allowed = api.try_writer()
        with os.fdopen(descriptor, 'rb') as stream:
            raw = stream.read()
        assert not writer_allowed
        assert raw == b'ORIGINAL_VERSION_A'
        arguments = api.CreateFileW.calls[0]
        assert arguments == (str(path), 0x80000000, 0x5, None, 3, 0x00200000 | 0x08000000, None)
        assert libraries == [('kernel32', {'use_last_error': True})]
        assert api.open_osfhandle.calls == [(7001, os.O_RDONLY | os.O_BINARY)]
        assert api.CloseHandle.calls == []  # Ownership transferred to descriptor.
        assert api.all_closed()
    finally:
        if not api.all_closed():
            os.close(descriptor)


def test_actual_windows_helper_keeps_atomic_delete_rotation_and_stable_fd(original_api):
    api, path, _ = original_api
    descriptor = _windows_open_nofollow(path)
    assert api.try_atomic_replace()
    with os.fdopen(descriptor, 'rb') as stream:
        assert stream.read() == b'ORIGINAL_VERSION_A'
    assert api.selected.read_bytes() == b'ORIGINAL_VERSION_B'
    assert api.CloseHandle.calls == [] and api.all_closed()


def test_actual_windows_helper_rejects_contract_already_open_writer(original_api):
    api, path, _ = original_api
    api.existing_writer = True
    with pytest.raises(OSError, match='SETTINGS_ROUTE_FILE_REJECTED'):
        _windows_open_nofollow(path)
    assert api.descriptors == []
    assert api.open_osfhandle.calls == api.CloseHandle.calls == []


@pytest.mark.parametrize('invalid', [None, ctypes.c_void_p(-1).value])
def test_actual_windows_helper_never_closes_or_transfers_invalid_handle(original_api, invalid):
    api, path, _ = original_api
    api.create_failure = True
    api.invalid_handle = invalid
    with pytest.raises(OSError, match='SETTINGS_ROUTE_FILE_REJECTED'):
        _windows_open_nofollow(path)
    assert api.GetFileInformationByHandle.calls == api.open_osfhandle.calls == api.CloseHandle.calls == []


@pytest.mark.parametrize('kind', ['reparse', 'directory', 'device', 'information_failure'])
def test_actual_windows_helper_closes_rejected_native_handle(original_api, kind):
    api, path, _ = original_api
    if kind == 'information_failure':
        api.info_ok = False
    else:
        api.attributes = {'reparse': 0x400, 'directory': 0x10, 'device': 0x40}[kind]
    with pytest.raises(SettingsRouteError, match='SETTINGS_ROUTE_PATH_REJECTED'):
        _windows_open_nofollow(path)
    assert api.CloseHandle.calls == [(7001,)] and api.all_closed()
    assert api.open_osfhandle.calls == []


def test_actual_windows_helper_closes_handle_when_fd_transfer_fails(original_api):
    api, path, _ = original_api
    api.transfer_failure = True
    with pytest.raises(OSError, match='transfer failure'):
        _windows_open_nofollow(path)
    assert api.CloseHandle.calls == [(7001,)] and api.all_closed()
