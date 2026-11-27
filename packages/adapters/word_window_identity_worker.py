"""Isolated stock-Windows reader for an existing Word Window.Document.

Only fixed PROPERTYGET calls, never Activate/Open/Save/Quit/ActiveDocument.
Run with -I -B; no package imports, generated COM wrappers or on-disk output.
API basis: https://learn.microsoft.com/en-us/windows/win32/api/oleacc/nf-oleacc-accessibleobjectfromwindow
https://learn.microsoft.com/en-us/office/vba/api/word.window.document
https://learn.microsoft.com/en-us/windows/win32/api/oaidl/nf-oaidl-idispatch-invoke
"""
import ctypes as C
import json
import os
from pathlib import PureWindowsPath
import sys
import uuid


class IdentityUnavailable(ValueError):
    pass


class GUID(C.Structure):
    _fields_ = [('bytes', C.c_ubyte * 16)]


class _Record(C.Structure):
    _fields_ = [('record', C.c_void_p), ('info', C.c_void_p)]


class _Value(C.Union):
    _fields_ = [('pointer', C.c_void_p), ('i64', C.c_int64), ('i32', C.c_int32),
                ('u32', C.c_uint32), ('boolean', C.c_int16), ('record', _Record)]


class VARIANT(C.Structure):
    _fields_ = [('vt', C.c_uint16), ('reserved', C.c_uint16 * 3), ('value', _Value)]


class DISPPARAMS(C.Structure):
    _fields_ = [('args', C.c_void_p), ('named', C.c_void_p), ('count', C.c_uint32), ('named_count', C.c_uint32)]


def bind(dll, name, result, args):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, args
    return function


class ReadonlyDispatch:
    ALLOWED = {'Hwnd', 'Document', 'Path', 'FullName', 'Saved'}

    def __init__(self, pointer, automation):
        if not pointer:
            raise IdentityUnavailable('NULL_NATIVE_OBJECT')
        self.pointer, self.automation = pointer, automation

    def method(self, index, result, args):
        table = C.cast(self.pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(result, C.c_void_p, *args)(table[index])

    def close(self):
        if self.pointer:
            self.method(2, C.c_uint32, [])(self.pointer)
            self.pointer = None

    def get(self, name):
        if name not in self.ALLOWED:
            raise IdentityUnavailable('PROPERTY_NOT_READONLY_ALLOWLISTED')
        names, ident, null = (C.c_wchar_p * 1)(name), C.c_int32(), GUID()
        result = self.method(5, C.c_int32, [C.POINTER(GUID), C.POINTER(C.c_wchar_p), C.c_uint32, C.c_uint32, C.POINTER(C.c_int32)])(
            self.pointer, C.byref(null), names, 1, 0, C.byref(ident))
        if result < 0:
            raise IdentityUnavailable('NATIVE_PROPERTY_UNAVAILABLE')
        value, arguments = VARIANT(), DISPPARAMS()
        try:
            # DISPATCH_PROPERTYGET=2, zero arguments; exception/arg-error outputs may be NULL.
            result = self.method(6, C.c_int32, [C.c_int32, C.POINTER(GUID), C.c_uint32, C.c_uint16,
                C.POINTER(DISPPARAMS), C.POINTER(VARIANT), C.c_void_p, C.c_void_p])(
                self.pointer, ident, C.byref(null), 0, 2, C.byref(arguments), C.byref(value), None, None)
            if result < 0:
                raise IdentityUnavailable('NATIVE_PROPERTY_READ_FAILED')
            if name == 'Document' and value.vt == 9 and value.value.pointer:
                pointer = value.value.pointer
                value.vt = 0  # Transfer the returned IDispatch reference, do not double release.
                return ReadonlyDispatch(pointer, self.automation)
            if name in {'Path', 'FullName'} and value.vt == 8 and value.value.pointer:
                size = self.automation.SysStringLen(value.value.pointer)
                if not 0 < size <= 4096:
                    raise IdentityUnavailable('EMPTY_OR_UNBOUNDED_DOCUMENT_PATH')
                return C.wstring_at(value.value.pointer, size)
            if name == 'Saved' and value.vt == 11 and value.value.boolean in (0, -1):
                return value.value.boolean == -1
            if name == 'Hwnd' and value.vt in (3, 19, 20, 21):
                return value.value.u32 if value.vt in (3, 19) else value.value.i64
            raise IdentityUnavailable('NATIVE_PROPERTY_TYPE_DIFFERS')
        finally:
            self.automation.VariantClear(C.byref(value))


def read_word_window(root, pid):
    if os.name != 'nt' or C.sizeof(C.c_void_p) != 8 or C.sizeof(VARIANT) != 24:
        raise IdentityUnavailable('WINDOWS_X64_REQUIRED')
    user, ole, access, automation = [C.WinDLL(name) for name in ('user32', 'ole32', 'oleacc', 'oleaut32')]
    hwnd, u32 = C.c_void_p, C.c_uint32
    ancestor = bind(user, 'GetAncestor', hwnd, [hwnd, u32])
    get_pid = bind(user, 'GetWindowThreadProcessId', u32, [hwnd, C.POINTER(u32)])
    get_class = bind(user, 'GetClassNameW', C.c_int32, [hwnd, C.c_wchar_p, C.c_int32])
    visible = bind(user, 'IsWindowVisible', C.c_int32, [hwnd])
    callback_type = C.WINFUNCTYPE(C.c_int32, hwnd, C.c_ssize_t)
    enumerate_children = bind(user, 'EnumChildWindows', C.c_int32, [hwnd, callback_type, C.c_ssize_t])
    initialize = bind(ole, 'CoInitializeEx', C.c_int32, [C.c_void_p, u32])
    uninitialize = bind(ole, 'CoUninitialize', None, [])
    native_object = bind(access, 'AccessibleObjectFromWindow', C.c_int32, [hwnd, u32, C.POINTER(GUID), C.POINTER(C.c_void_p)])
    bind(automation, 'VariantClear', C.c_int32, [C.POINTER(VARIANT)])
    bind(automation, 'SysStringLen', u32, [C.c_void_p])

    def window_class(handle):
        name = C.create_unicode_buffer(256)
        if not get_class(handle, name, len(name)):
            raise IdentityUnavailable('WINDOW_CLASS_UNAVAILABLE')
        return name.value

    def check(handle):
        actual_pid = u32()
        get_pid(handle, C.byref(actual_pid))
        if int(ancestor(handle, 2) or 0) != root or actual_pid.value != pid:
            raise IdentityUnavailable('WINDOW_SCOPE_CHANGED')

    check(root)
    if window_class(root) != 'OpusApp' or not visible(root):
        raise IdentityUnavailable('EXACT_VISIBLE_WORD_WINDOW_REQUIRED')
    children, visits, overflow = [], 0, False
    @callback_type
    def collect(child, _):
        nonlocal visits, overflow
        try:
            visits += 1
            if visits > 256:
                overflow = True
                return 0
            if window_class(child) == '_WwG' and visible(child):
                children.append(int(child))
                if len(children) > 8:
                    overflow = True
                    return 0
        except Exception:
            overflow = True
            return 0
        return 1
    if not enumerate_children(root, collect, 0):
        raise IdentityUnavailable('DOCUMENT_PANE_ENUMERATION_INCOMPLETE')
    if overflow or not children:
        raise IdentityUnavailable('DOCUMENT_PANE_MISSING_OR_BUDGET_REACHED')
    result = initialize(None, 2)
    if result not in (0, 1):
        raise IdentityUnavailable('COM_APARTMENT_UNAVAILABLE')
    try:
        iid = GUID()
        C.memmove(C.byref(iid), uuid.UUID('00020400-0000-0000-C000-000000000046').bytes_le, 16)
        samples = []
        for child in children:
            check(child)
            pointer = C.c_void_p()
            if native_object(child, 0xfffffff0, C.byref(iid), C.byref(pointer)) < 0 or not pointer.value:
                raise IdentityUnavailable('NATIVE_WINDOW_DOCUMENT_UNAVAILABLE')
            window, document = ReadonlyDispatch(pointer.value, automation), None
            try:
                if window.get('Hwnd') != root:
                    raise IdentityUnavailable('WORD_WINDOW_OBJECT_DIFFERS')
                document = window.get('Document')
                first = {'native_path': document.get('FullName'), 'directory': document.get('Path'), 'saved': document.get('Saved')}
                second = {'native_path': document.get('FullName'), 'directory': document.get('Path'), 'saved': document.get('Saved')}
                path = PureWindowsPath(first['native_path'])
                if (first != second or window.get('Hwnd') != root or not path.is_absolute()
                        or len(path.drive) != 2 or path.drive[1:] != ':'
                        or str(path.parent) != str(PureWindowsPath(first['directory']))
                        or any(ord(char) < 32 for char in first['native_path'])):
                    raise IdentityUnavailable('DOCUMENT_IDENTITY_UNSAVED_REMOTE_OR_CHANGED')
                samples.append(first)
                check(child)
            finally:
                if document is not None:
                    document.close()
                window.close()
        check(root)
        if any(value != samples[0] for value in samples):
            raise IdentityUnavailable('MULTIPLE_DOCUMENTS_IN_WINDOW')
        return {'status': 'OBSERVED', 'source': 'WORD_WINDOW_NATIVEOM_PROPERTYGET',
            'native_path': samples[0]['native_path'], 'saved': samples[0]['saved'],
            'root_window_id': root, 'process_id': pid, 'content_equals_saved_bytes': False}
    finally:
        uninitialize()


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or not all(value.isdecimal() for value in sys.argv[1:]):
            raise IdentityUnavailable('EXACT_PRIVATE_WINDOW_ARGUMENTS_REQUIRED')
        root, pid = map(int, sys.argv[1:])
        if not 0 < root < 2**64 or not 0 < pid < 2**32:
            raise IdentityUnavailable('PRIVATE_WINDOW_ARGUMENTS_OUTSIDE_RANGE')
        print(json.dumps(read_word_window(root, pid)))
    except Exception as error:
        print(json.dumps({'status': 'UNAVAILABLE', 'reason': str(error) if isinstance(error, IdentityUnavailable) else type(error).__name__}))
