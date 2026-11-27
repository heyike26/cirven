"""Main-child exact foreground handoff; no SDK, input, save or window relayout.

The authenticated broker independently checks actual foreground/baseline after
this helper returns. Its output is a request outcome, never an input receipt.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time


class HandoffRefused(ValueError):
    pass


class HandoffOutcomeUnknown(HandoffRefused):
    """A focus request may have happened; never report an OS refusal."""


def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise HandoffRefused('EXACT_PRIVATE_MAIN_HANDOFF_FIELDS_REQUIRED')
    return value


def integer(value, low=1, high=2**31-1):
    if type(value) is not int or not low <= value <= high:
        raise HandoffRefused('EXACT_HANDOFF_INTEGER_REQUIRED')
    return value


def decimal(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value):
        raise HandoffRefused('EXACT_DECIMAL_NATIVE_IDENTITY_REQUIRED')
    return integer(int(value), high=2**64-1)


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise HandoffRefused('EXACT_NATIVE_SHA_REQUIRED')
    return value


def verify_main_image(path, record):
    """Hash the selected large Electron image with bounded streaming reads."""
    from packages.adapters.native_append import _safe_file
    path=Path(path).absolute()
    expected=record.get('bytes')
    if type(expected) is not int or not 0 < expected <= 512*1024*1024:
        raise HandoffRefused('PACKAGED_MAIN_EXECUTABLE_SIZE_REQUIRED')
    expected_sha=sha(record.get('sha256'))
    _safe_file(path.parent,path.name)
    before=path.stat()
    if before.st_size!=expected:
        raise HandoffRefused('PACKAGED_MAIN_EXECUTABLE_DRIFT')
    signature=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns)
    checksum=hashlib.sha256();count=0
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        if signature(opened)!=signature(before):
            raise HandoffRefused('PACKAGED_MAIN_EXECUTABLE_DRIFT')
        while count < expected:
            part=stream.read(min(1024*1024,expected-count))
            if not part:break
            count+=len(part);checksum.update(part)
        excess=stream.read(1)
        ended=os.fstat(stream.fileno())
    after=path.stat();_safe_file(path.parent,path.name)
    if (count!=expected or excess or signature(ended)!=signature(before)
            or signature(after)!=signature(before) or checksum.hexdigest()!=expected_sha):
        raise HandoffRefused('PACKAGED_MAIN_EXECUTABLE_DRIFT')


def admit(value, *, now_ms=None, parent_pid=None):
    fields(value, ('operation_id','ticket_ref','activation_target','activation_host','baseline','expires_unix_ms'))
    if not isinstance(value['operation_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value['operation_id']):
        raise HandoffRefused('EXACT_NATIVE_OPERATION_REQUIRED')
    ref=fields(value['ticket_ref'], ('id','revision','sha256'))
    if not isinstance(ref['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',ref['id']):
        raise HandoffRefused('EXACT_NATIVE_TICKET_REQUIRED')
    integer(ref['revision']);sha(ref['sha256'])
    now=int(time.time()*1000) if now_ms is None else now_ms
    if not now < integer(value['expires_unix_ms'], high=2**63-1) <= now+60000:
        raise HandoffRefused('FRESH_MAIN_DIALOG_HANDOFF_REQUIRED')
    target=fields(value['activation_target'], ('window_id_decimal','pid','process_creation_decimal','identity_sha256','edit_window_id_decimal'))
    window={
        'window_id':decimal(target['window_id_decimal']),'pid':integer(target['pid']),
        'process_creation':decimal(target['process_creation_decimal']),
        'identity_sha256':sha(target['identity_sha256']),
    }
    edit=decimal(target['edit_window_id_decimal'])
    host=fields(value['activation_host'], ('window_id_decimal','pid','process_creation_decimal',
        'os_session','window_thread','window_class','marker_name','marker_value_decimal'))
    marker={
        'window_id':decimal(host['window_id_decimal']),'pid':integer(host['pid']),
        'process_creation':decimal(host['process_creation_decimal']),
        'os_session':integer(host['os_session']),'window_thread':integer(host['window_thread']),
        'window_class':host['window_class'],'marker_name':host['marker_name'],
        'marker_value':decimal(host['marker_value_decimal']),
    }
    if (marker['pid'] != (os.getppid() if parent_pid is None else parent_pid)
            or marker['pid']==window['pid'] or marker['window_id']==window['window_id']
            or not isinstance(marker['window_class'],str) or not 1<=len(marker['window_class'])<=256
            or not isinstance(marker['marker_name'],str)
            or not re.fullmatch(r'EEAS-NativeCollaboration-[a-f0-9]{32}',marker['marker_name'])):
        raise HandoffRefused('OWNING_MARKED_MAIN_PARENT_REQUIRED')
    baseline=fields(value['baseline'], ('text_sha256','utf16_length','selection_start','selection_end','bounds'))
    sha(baseline['text_sha256'])
    length=integer(baseline['utf16_length'],low=0,high=65536)
    if (integer(baseline['selection_start'],low=0,high=65536)!=length
            or integer(baseline['selection_end'],low=0,high=65536)!=length):
        raise HandoffRefused('EXACT_UTF16_END_BASELINE_REQUIRED')
    bounds=fields(baseline['bounds'], ('x','y','width','height'))
    for key in ('x','y'):integer(bounds[key],low=-32768,high=32768)
    for key in ('width','height'):integer(bounds[key],high=32768)
    return {'window':window,'edit_window_id':edit,'marker':marker,'baseline':baseline}


def activate(value):
    admitted=admit(value)
    if os.name!='nt':
        raise HandoffRefused('ACTUAL_WINDOWS_MAIN_CHILD_REQUIRED')
    # Imports construct no SDK or native driver/session. One stock OS call is
    # the only focus effect, and only after exact fresh checks.
    import ctypes as ct
    from packages.domain.native_collaboration import Win32OwnedHostWindows
    from packages.adapters.desktop_current_context import Win32CurrentWindowSource
    from packages.adapters.native_append import Win32ClassicNotepadInspector
    root=Path(__file__).resolve().parents[2]
    manifest=json.loads((root/'PACKAGE_MANIFEST.json').read_bytes())
    exe_name=manifest['entrypoints']['electron']
    exe_ref=next(row for row in manifest['files'] if row['path']==exe_name)
    expected_exe=root/exe_name
    source=Win32CurrentWindowSource(excluded_pid_reader=lambda:[admitted['marker']['pid']])
    owned=Win32OwnedHostWindows();owned.validate(admitted['marker'])
    H,D,B=ct.c_void_p,ct.c_uint32,ct.c_int32
    query=source.api._bind(source.api.kernel,'QueryFullProcessImageNameW',B,[H,D,ct.c_wchar_p,ct.POINTER(D)])
    handle=source.api.kernel.OpenProcess(0x1000,False,admitted['marker']['pid'])
    if not handle:raise HandoffRefused('ACTUAL_MAIN_PROCESS_UNAVAILABLE')
    try:
        buf=ct.create_unicode_buffer(32768);length=D(len(buf))
        if not query(handle,0,buf,ct.byref(length)) or Path(buf.value).resolve()!=expected_exe.resolve():
            raise HandoffRefused('PACKAGED_MAIN_EXECUTABLE_REQUIRED')
        verify_main_image(expected_exe,exe_ref)
    finally:
        source.api.kernel.CloseHandle(handle)
    inspector=Win32ClassicNotepadInspector(source)
    def inspect_current():
        actual=inspector.read(admitted['window'])
        if (any(actual[key]!=admitted['window'][key] for key in admitted['window'])
                or actual['edit_window_id']!=admitted['edit_window_id']
                or any(actual[key]!=value for key,value in admitted['baseline'].items())
                or actual.get('focused_edit') is not True or actual.get('gui_idle') is not True
                or actual.get('modifiers_released') is not True
                or actual.get('same_user') is not True or actual.get('same_session') is not True
                or actual.get('same_integrity') is not True):
            raise HandoffRefused('ACTUAL_TARGET_BASE_OR_CARET_CHANGED')
        return actual
    inspect_current();owned.validate(admitted['marker'])
    admit(value) # expiry and actual parent rechecked after bounded reads.
    if int(source.foreground() or 0)!=admitted['marker']['window_id']:
        raise HandoffRefused('CURRENT_MAIN_FOREGROUND_HANDOFF_REQUIRED')
    set_foreground=source.api._bind(source.api.user,'SetForegroundWindow',B,[H])
    try:
        requested=set_foreground(admitted['window']['window_id'])
    except Exception as error:
        raise HandoffOutcomeUnknown('FOREGROUND_REQUEST_RESULT_UNKNOWN') from error
    if not requested:
        return {'status':'OS_REFUSED','phase':'OS_RETURNED_ZERO','reason':'SET_FOREGROUND_WINDOW_REFUSED','automatic_retry':False}
    try:
        actual=inspect_current();owned.validate(admitted['marker'])
        if actual['foreground_window_id']!=admitted['window']['window_id']:
            raise HandoffRefused('ACTUAL_FOREGROUND_NOT_VERIFIED')
    except Exception as error:
        raise HandoffOutcomeUnknown('FOREGROUND_EFFECT_NOT_VERIFIED') from error
    return {'status':'ACTIVATION_REQUEST_RETURNED','automatic_retry':False,
        'phase':'FOREGROUND_INDEPENDENTLY_OBSERVED',
        'input_dispatched':False,'sdk_session_created':False,'window_geometry_mutated':False}


def main():
    raw=sys.stdin.buffer.read(32769)
    if len(raw)>32768:raise HandoffRefused('PRIVATE_HANDOFF_REQUEST_TOO_LARGE')
    value=json.loads(raw.decode('utf-8'))
    result=activate(value)
    print(json.dumps(result,separators=(',',':')))


if __name__=='__main__':
    try:main()
    except Exception as error:
        unknown=isinstance(error,HandoffOutcomeUnknown)
        print(json.dumps({'status':'UNKNOWN' if unknown else 'OS_REFUSED',
            'phase':'AFTER_FOREGROUND_REQUEST' if unknown else 'BEFORE_FOREGROUND_REQUEST',
            'reason':str(error)[:300],
            'automatic_retry':False,'input_dispatched':False},separators=(',',':')))
        raise SystemExit(1)
