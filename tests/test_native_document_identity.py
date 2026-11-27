"""Own-process boundary/ABI only; never native Word/COM qualification.

Authored for concentrated debug, with no real Office instance or OS input.
"""
import ctypes
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from packages.adapters.native_document_identity import NativeDocumentIdentity, public_document_identity
from packages.adapters.word_window_identity_worker import VARIANT, DISPPARAMS, ReadonlyDispatch, IdentityUnavailable


def window(**changes):
    return {'window_class': 'OpusApp', 'window_id': 42, 'pid': 123, 'identity_sha256': 'a'*64, **changes}


def reply(**changes):
    return {'status': 'OBSERVED', 'source': 'WORD_WINDOW_NATIVEOM_PROPERTYGET',
        'root_window_id': 42, 'process_id': 123, 'native_path': 'C:\\selected\\人工稿.docx',
        'saved': False, 'content_equals_saved_bytes': False, **changes}


def test_isolated_own_worker_has_deadline_no_files_wrappers_or_office_start_commands():
    calls = []
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply()).encode(), stderr=b'')
    reader = NativeDocumentIdentity(runner=runner, platform='nt', executable='bounded-python')
    value = reader.read(window())
    assert value['status'] == 'OBSERVED' and value['saved'] is False
    assert value['content_equals_saved_bytes'] is False
    argv, options = calls[0]
    assert argv[:3] == ['bounded-python', '-I', '-B'] and argv[-2:] == ['42', '123']
    assert Path(argv[3]).name == 'word_window_identity_worker.py'
    assert options['timeout'] == 2.5 and options['creationflags'] == 0x08000000
    assert options['stdin'] == subprocess.DEVNULL
    public = public_document_identity(value)
    assert 'native_path' not in public and 'process_id' not in public and 'root_window_id' not in public


@pytest.mark.parametrize('changes', [
    {'root_window_id': 43}, {'process_id': 124}, {'source': 'WINDOW_TITLE_GUESS'},
    {'native_path': 'https://example.com/document.docx'}, {'native_path': '\\\\server\\share\\file.docx'},
    {'saved': 'false'}, {'content_equals_saved_bytes': True}, {'native_path': 'Untitled.docx'}])
def test_malformed_wrong_window_remote_or_untyped_result_does_not_bind(changes):
    reader = NativeDocumentIdentity(platform='nt', runner=lambda *_args, **_kwargs:
        SimpleNamespace(returncode=0, stdout=json.dumps(reply(**changes)).encode(), stderr=b''))
    assert reader.read(window())['status'] == 'UNAVAILABLE'


def test_timeout_and_busy_do_not_start_another_worker_or_leak_allowance():
    calls = []
    def runner(argv, **_):
        calls.append(argv)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, 2.5)
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply()).encode(), stderr=b'')
    reader = NativeDocumentIdentity(platform='nt', runner=runner)
    assert reader.read(window())['status'] == 'UNAVAILABLE'
    assert reader.read(window())['status'] == 'OBSERVED'
    assert reader.allowance.acquire(blocking=False)
    try:
        assert reader.read(window())['reason'] == 'NATIVE_DOCUMENT_IDENTITY_BUSY'
        assert len(calls) == 2
    finally:
        reader.allowance.release()
    assert reader.read(window(window_class='Chrome_WidgetWin_1'))['status'] == 'UNAVAILABLE'
    assert len(calls) == 2


def test_native_variant_abi_and_fixed_property_allowlist_have_no_mutation_surface():
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(VARIANT) == 24 and VARIANT.value.offset == 8
        assert ctypes.sizeof(DISPPARAMS) == 24
    dispatch = ReadonlyDispatch(1, None)
    for name in ('Save', 'Open', 'Quit', 'Activate', 'ActiveDocument', 'Application', 'Content'):
        with pytest.raises(IdentityUnavailable, match='ALLOWLISTED'):
            dispatch.get(name)
