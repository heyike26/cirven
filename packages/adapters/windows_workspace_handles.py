"""Win32 selected-folder handles. Native Windows qualification is a separate gate.

No HANDLE is a POSIX descriptor. Domain ownership, grants, backups and CAS remain
in LocalWorkspaces; this adapter implements the OS operations behind its hooks.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes as C
from dataclasses import dataclass
import hashlib
import json
import ntpath
import os
import re
import uuid

from packages.domain.store import Conflict, InvalidInput, NotFound

DWORD = C.c_uint32
BOOL = C.c_int32
HANDLE = C.c_void_p
WORD = C.c_uint16
ULONGLONG = C.c_uint64
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_READ_ATTRIBUTES = 0x80
DELETE = 0x00010000
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_SHARE_DELETE = 1, 2, 4
FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT = 0x10, 0x400
FILE_ATTRIBUTE_READONLY, FILE_ATTRIBUTE_NORMAL = 1, 0x80
RECOVERABLE_ATTRIBUTES = 0x01 | 0x02 | 0x04 | 0x20 | 0x80 | 0x100 | 0x2000
FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS = 0x00200000, 0x02000000
FILE_FLAG_WRITE_THROUGH = 0x80000000
CREATE_NEW, OPEN_EXISTING, OPEN_ALWAYS = 1, 3, 4
MAX_FILE_BYTES, MAX_EDIT_BYTES = 16 * 1024 * 1024, 1024 * 1024
PRIVATE = {".git", ".eeas-sdk-state", ".eeas-runtime"}


def validate_file_metadata(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'attributes', 'creation', 'access', 'write', 'security_sddl'}
            or value['schema'] != 'eeas.ntfs-file-metadata.v1' or type(value['attributes']) is not int
            or value['attributes'] < 0 or value['attributes'] & ~RECOVERABLE_ATTRIBUTES
            or not isinstance(value['security_sddl'], str) or '\x00' in value['security_sddl']
            or not 1 <= len(value['security_sddl'].encode('utf-8')) <= 8192):
        raise InvalidInput('Bounded original ordinary-file NTFS metadata is required')
    for key in ('creation', 'access', 'write'):
        if (not isinstance(value[key], str) or not re.fullmatch(r'[0-9]{1,19}', value[key])
                or not 0 < int(value[key]) < (1 << 63)):
            raise InvalidInput('Original NTFS timestamps must be exact supported decimal FILETIMEs')
    return value


def file_metadata_sha256(value):
    validate_file_metadata(value)
    # Last-access time is kept for restoration, never used as a drift identity.
    stable = {key: field for key, field in value.items() if key != 'access'}
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class FILETIME(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class BY_HANDLE_FILE_INFORMATION(C.Structure):
    _fields_ = [("attributes", DWORD), ("creation", FILETIME), ("access", FILETIME), ("write", FILETIME),
        ("volume", DWORD), ("size_high", DWORD), ("size_low", DWORD), ("links", DWORD),
        ("index_high", DWORD), ("index_low", DWORD)]


class FILE_RENAME_INFO(C.Structure):
    # DWORD represents the ReplaceIfExists/Flags union; WCHAR is explicitly 16-bit
    # so its ABI can also be checked on a non-Windows author host.
    _fields_ = [("flags", DWORD), ("root_directory", HANDLE), ("name_length", DWORD), ("name", WORD * 1)]


class FILE_BASIC_INFO(C.Structure):
    _fields_ = [("creation", C.c_int64), ("access", C.c_int64), ("write", C.c_int64), ("change", C.c_int64), ("attributes", DWORD)]


class FILE_ID_BOTH_DIR_INFO(C.Structure):
    _fields_ = [("next", DWORD), ("index", DWORD), ("creation", C.c_int64), ("access", C.c_int64),
        ("write", C.c_int64), ("change", C.c_int64), ("size", C.c_int64), ("allocation", C.c_int64),
        ("attributes", DWORD), ("name_length", DWORD), ("ea_size", DWORD), ("short_length", C.c_uint8),
        ("short_name", WORD * 12), ("file_id", C.c_int64), ("name", WORD * 1)]


class OVERLAPPED(C.Structure):
    _fields_ = [("internal", C.c_size_t), ("internal_high", C.c_size_t), ("offset", DWORD), ("offset_high", DWORD), ("event", HANDLE)]


class UNICODE_STRING(C.Structure):
    _fields_ = [("length", WORD), ("maximum_length", WORD), ("buffer", HANDLE)]


class OBJECT_ATTRIBUTES(C.Structure):
    _fields_ = [("length", DWORD), ("root", HANDLE), ("name", C.POINTER(UNICODE_STRING)),
        ("attributes", DWORD), ("security", HANDLE), ("quality", HANDLE)]


class IO_STATUS_UNION(C.Union):
    _fields_ = [("status", C.c_int32), ("pointer", HANDLE)]


class IO_STATUS_BLOCK(C.Structure):
    _fields_ = [("result", IO_STATUS_UNION), ("information", C.c_size_t)]


def _component(value, *, internal=False):
    try: units = len(value.encode("utf-16-le"))
    except (AttributeError, UnicodeError): raise InvalidInput("Windows path must contain valid Unicode text") from None
    if (not value or value in {".", ".."} or value[-1] in {".", " "} or
            units > 510 or any(ord(c) < 32 or ord(c) == 127 or c in '\\/:<>"|?*' for c in value) or
            re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", value.upper())):
        raise InvalidInput("Windows path contains an alias, device, stream or invalid component")
    if not internal and (value.casefold() in PRIVATE or value.startswith(".eeas-write-")):
        raise InvalidInput("Private workspace paths are outside the grant")
    return value


def normalized_root(value):
    if not isinstance(value, str) or not 3 <= len(value) <= 4096 or any(ord(c) < 32 for c in value):
        raise InvalidInput("Choose an existing absolute Windows local folder")
    value = value.replace("/", "\\")
    if not re.match(r"^[A-Za-z]:\\", value) or value.startswith("\\\\"):
        raise InvalidInput("Windows local folders require a drive-absolute path; device and UNC namespaces are rejected")
    drive, tail = ntpath.splitdrive(value)
    parts = tail[1:].split("\\")
    if parts and not parts[-1]: parts.pop()
    for part in parts: _component(part, internal=True)
    return drive.upper() + "\\" + "\\".join(parts)


def relative_path(value, *, empty=False):
    if value == "" and empty: return value
    if not isinstance(value, str) or not value or len(value) > 1024: raise InvalidInput("Invalid relative Windows workspace path")
    for part in value.split("/"): _component(part)
    return value


def _final(value):
    if value.startswith("\\\\?\\"): value = value[4:]
    return normalized_root(value)


def _equal(left, right): return ntpath.normcase(left) == ntpath.normcase(right)


class Win32Kernel:
    """Real ctypes bindings, loaded only on Windows. A test seam may inject another API."""
    def __init__(self):
        if os.name != "nt": raise InvalidInput("The native Win32 kernel is unavailable on this host")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.a = C.WinDLL("advapi32", use_last_error=True)
        self.n = C.WinDLL("ntdll", use_last_error=True)
        def bind(dll, name, restype, args):
            function = getattr(dll, name); function.restype = restype; function.argtypes = args; return function
        self.create = bind(self.k, "CreateFileW", HANDLE, [C.c_wchar_p, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE])
        self.close_handle = bind(self.k, "CloseHandle", BOOL, [HANDLE])
        self.get_info = bind(self.k, "GetFileInformationByHandle", BOOL, [HANDLE, C.POINTER(BY_HANDLE_FILE_INFORMATION)])
        self.get_final = bind(self.k, "GetFinalPathNameByHandleW", DWORD, [HANDLE, C.c_wchar_p, DWORD, DWORD])
        self.get_type = bind(self.k, "GetFileType", DWORD, [HANDLE])
        self.get_ex = bind(self.k, "GetFileInformationByHandleEx", BOOL, [HANDLE, C.c_int32, HANDLE, DWORD])
        self.set_ex = bind(self.k, "SetFileInformationByHandle", BOOL, [HANDLE, C.c_int32, HANDLE, DWORD])
        self.read_file = bind(self.k, "ReadFile", BOOL, [HANDLE, HANDLE, DWORD, C.POINTER(DWORD), HANDLE])
        self.write_file = bind(self.k, "WriteFile", BOOL, [HANDLE, HANDLE, DWORD, C.POINTER(DWORD), HANDLE])
        self.flush_file = bind(self.k, "FlushFileBuffers", BOOL, [HANDLE])
        self.set_pointer = bind(self.k, "SetFilePointerEx", BOOL, [HANDLE, C.c_int64, C.POINTER(C.c_int64), DWORD])
        self.set_end = bind(self.k, "SetEndOfFile", BOOL, [HANDLE])
        self.mkdir = bind(self.k, "CreateDirectoryW", BOOL, [C.c_wchar_p, HANDLE])
        self.get_volume = bind(self.k, "GetVolumeInformationByHandleW", BOOL,
            [HANDLE, C.c_wchar_p, DWORD, C.POINTER(DWORD), C.POINTER(DWORD), C.POINTER(DWORD), C.c_wchar_p, DWORD])
        self.lock_file = bind(self.k, "LockFileEx", BOOL, [HANDLE, DWORD, DWORD, DWORD, DWORD, C.POINTER(OVERLAPPED)])
        self.unlock_file = bind(self.k, "UnlockFileEx", BOOL, [HANDLE, DWORD, DWORD, DWORD, C.POINTER(OVERLAPPED)])
        self.get_security = bind(self.a, "GetSecurityInfo", DWORD,
            [HANDLE, C.c_int32, DWORD, C.POINTER(HANDLE), C.POINTER(HANDLE), C.POINTER(HANDLE), C.POINTER(HANDLE), C.POINTER(HANDLE)])
        self.set_security = bind(self.a, "SetSecurityInfo", DWORD, [HANDLE, C.c_int32, DWORD, HANDLE, HANDLE, HANDLE, HANDLE])
        self.get_control = bind(self.a, "GetSecurityDescriptorControl", BOOL, [HANDLE, C.POINTER(WORD), C.POINTER(DWORD)])
        self.sd_to_string = bind(self.a, 'ConvertSecurityDescriptorToStringSecurityDescriptorW', BOOL,
            [HANDLE, DWORD, DWORD, C.POINTER(HANDLE), C.POINTER(DWORD)])
        self.string_to_sd = bind(self.a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', BOOL,
            [C.c_wchar_p, DWORD, C.POINTER(HANDLE), C.POINTER(DWORD)])
        self.sd_owner = bind(self.a, 'GetSecurityDescriptorOwner', BOOL, [HANDLE, C.POINTER(HANDLE), C.POINTER(BOOL)])
        self.sd_group = bind(self.a, 'GetSecurityDescriptorGroup', BOOL, [HANDLE, C.POINTER(HANDLE), C.POINTER(BOOL)])
        self.sd_dacl = bind(self.a, 'GetSecurityDescriptorDacl', BOOL,
            [HANDLE, C.POINTER(BOOL), C.POINTER(HANDLE), C.POINTER(BOOL)])
        self.set_times = bind(self.k, 'SetFileTime', BOOL,
            [HANDLE, C.POINTER(FILETIME), C.POINTER(FILETIME), C.POINTER(FILETIME)])
        self.local_free = bind(self.k, "LocalFree", HANDLE, [HANDLE])
        self.nt_create = bind(self.n, "NtCreateFile", C.c_int32,
            [C.POINTER(HANDLE), DWORD, C.POINTER(OBJECT_ATTRIBUTES), C.POINTER(IO_STATUS_BLOCK), HANDLE,
             DWORD, DWORD, DWORD, DWORD, HANDLE, DWORD])
        self.nt_set_information = bind(self.n, "NtSetInformationFile", C.c_int32,
            [HANDLE, C.POINTER(IO_STATUS_BLOCK), HANDLE, DWORD, C.c_int32])
        self.status_error = bind(self.n, "RtlNtStatusToDosError", DWORD, [C.c_int32])

    @staticmethod
    def error(operation, code=None):
        code = C.get_last_error() if code is None else code
        error = OSError(code, "Win32 " + operation + " failed"); error.winerror = code
        raise error

    def open(self, path, access, share, disposition=OPEN_EXISTING, *, directory=False, through=False):
        flags = FILE_FLAG_OPEN_REPARSE_POINT | (FILE_FLAG_BACKUP_SEMANTICS if directory else FILE_ATTRIBUTE_NORMAL)
        if through: flags |= FILE_FLAG_WRITE_THROUGH
        value = self.create("\\\\?\\" + path, access, share, None, disposition, flags, None)
        if value in {None, C.c_void_p(-1).value}: self.error("CreateFileW")
        return value

    def close(self, handle):
        if not self.close_handle(handle): self.error("CloseHandle")

    def open_relative(self, parent, leaf, access, share, disposition=OPEN_EXISTING, *, directory=False, through=False, reject_reparse=True):
        """One-component NtCreateFile name rooted at the already verified directory.

        There is deliberately no absolute-path fallback. OBJ_DONT_REPARSE refuses
        parsing through a reparse; the final opened handle is checked independently.
        """
        _component(leaf, internal=True)
        encoded = leaf.encode("utf-16-le"); buffer = C.create_string_buffer(encoded + b"\x00\x00")
        name = UNICODE_STRING(len(encoded), len(encoded) + 2, C.addressof(buffer))
        attributes = OBJECT_ATTRIBUTES(C.sizeof(OBJECT_ATTRIBUTES), parent, C.pointer(name),
            0x40 | (0x1000 if reject_reparse else 0), None, None)
        result, status = HANDLE(), IO_STATUS_BLOCK()
        options = 0x20 | 0x00200000 | (1 if directory is True else 0x40 if directory is False else 0)
        if through: options |= 2
        dispositions = {CREATE_NEW: 2, OPEN_EXISTING: 1, OPEN_ALWAYS: 3}
        code = self.nt_create(C.byref(result), access | 0x00100000, C.byref(attributes), C.byref(status), None,
            FILE_ATTRIBUTE_NORMAL, share,
            dispositions[disposition], options, None, 0)
        if code < 0: self.error("NtCreateFile(relative)", self.status_error(code))
        if result.value is None: raise InvalidInput("Native relative file handle was not established")
        return result.value

    def info(self, handle):
        info = BY_HANDLE_FILE_INFORMATION()
        if not self.get_info(handle, C.byref(info)): self.error("GetFileInformationByHandle")
        if self.get_type(handle) != 1: raise InvalidInput("Selected path is not a disk file or directory")
        return {"device": info.volume, "inode": (info.index_high << 32) | info.index_low,
            "attributes": info.attributes, "size": (info.size_high << 32) | info.size_low, "links": info.links,
            "creation": (info.creation.high << 32) | info.creation.low,
            "access": (info.access.high << 32) | info.access.low, "write": (info.write.high << 32) | info.write.low}

    def final_path(self, handle):
        needed = self.get_final(handle, None, 0, 0)
        if not needed or needed > 32768: self.error("GetFinalPathNameByHandleW")
        output = C.create_unicode_buffer(needed + 1)
        length = self.get_final(handle, output, len(output), 0)
        if not length or length >= len(output): self.error("GetFinalPathNameByHandleW")
        return _final(output.value)

    def filesystem(self, handle):
        result = C.create_unicode_buffer(64)
        if not self.get_volume(handle, None, 0, None, None, None, result, len(result)): self.error("GetVolumeInformationByHandleW")
        return result.value

    def make_directory(self, path):
        if not self.mkdir("\\\\?\\" + path, None) and C.get_last_error() != 183: self.error("CreateDirectoryW")

    def read(self, handle, amount):
        buffer, received = C.create_string_buffer(amount), DWORD()
        if not self.read_file(handle, buffer, amount, C.byref(received), None): self.error("ReadFile")
        return buffer.raw[:received.value]

    def write(self, handle, raw):
        written = DWORD(); buffer = C.create_string_buffer(raw)
        if not self.write_file(handle, buffer, len(raw), C.byref(written), None): self.error("WriteFile")
        return written.value

    def flush(self, handle):
        if not self.flush_file(handle): self.error("FlushFileBuffers")

    def seek_start(self, handle):
        if not self.set_pointer(handle, 0, None, 0): self.error("SetFilePointerEx")

    def truncate_here(self, handle):
        if not self.set_end(handle): self.error("SetEndOfFile")

    def copy_security(self, source, target):
        owner, group, dacl, descriptor = HANDLE(), HANDLE(), HANDLE(), HANDLE()
        result = self.get_security(source, 1, 7, C.byref(owner), C.byref(group), C.byref(dacl), None, C.byref(descriptor))
        if result: self.error("GetSecurityInfo", result)
        try:
            control, revision = WORD(), DWORD()
            if not self.get_control(descriptor, C.byref(control), C.byref(revision)): self.error("GetSecurityDescriptorControl")
            flags = 7 | (0x80000000 if control.value & 0x1000 else 0x20000000)
            result = self.set_security(target, 1, flags, owner, group, dacl, None)
            if result: self.error("SetSecurityInfo", result)
        finally: self.local_free(descriptor)

    def attributes(self, handle, attributes):
        value = FILE_BASIC_INFO(); value.attributes = attributes or FILE_ATTRIBUTE_NORMAL
        if not self.set_ex(handle, 0, C.byref(value), C.sizeof(value)): self.error("SetFileInformationByHandle(attributes)")

    def ordinary_stream(self, handle):
        # FILE_STREAM_INFO (class 7), one unnamed data stream only. Do not erase
        # ADS, compressed/encrypted/sparse or unknown metadata under byte Undo.
        import struct
        buffer = C.create_string_buffer(64*1024)
        if not self.get_ex(handle, 7, buffer, len(buffer)): self.error('GetFileInformationByHandleEx(streams)')
        offset, names = 0, []
        while True:
            if offset+24 > len(buffer): raise InvalidInput('NTFS stream metadata exceeded its bound')
            following, length = struct.unpack_from('<II', buffer.raw, offset)
            if length % 2 or not 0 < length <= 1024 or offset+24+length > len(buffer):
                raise InvalidInput('NTFS stream name metadata is invalid')
            names.append(buffer.raw[offset+24:offset+24+length].decode('utf-16-le'))
            if len(names)>1 or names[0] != '::$DATA':
                raise InvalidInput('Named NTFS streams require explicit native handling; original file retained')
            if not following: break
            if following % 8 or following < 24+length: raise InvalidInput('NTFS stream metadata is invalid')
            offset += following

    def security_sddl(self, handle):
        owner, group, dacl, descriptor, text = HANDLE(), HANDLE(), HANDLE(), HANDLE(), HANDLE()
        result = self.get_security(handle, 1, 7, C.byref(owner), C.byref(group), C.byref(dacl), None, C.byref(descriptor))
        if result: self.error('GetSecurityInfo(recovery)', result)
        try:
            if not owner.value or not group.value or not dacl.value:
                raise InvalidInput('Original owner/group/non-null DACL recovery is required before deleting')
            length = DWORD()
            if not self.sd_to_string(descriptor, 1, 7, C.byref(text), C.byref(length)):
                self.error('ConvertSecurityDescriptorToStringSecurityDescriptorW')
            if not text.value or not 1 <= length.value <= 8193:
                raise InvalidInput('Original security descriptor exceeds its bounded recovery material')
            result = C.wstring_at(text.value)
            if len(result.encode('utf-8'))>8192: raise InvalidInput('Original security descriptor exceeds its bound')
            return result
        finally:
            if text.value: self.local_free(text)
            self.local_free(descriptor)

    def capture_metadata(self, handle, original_info=None):
        info = self.info(handle) if original_info is None else original_info
        if info['attributes'] & ~RECOVERABLE_ATTRIBUTES:
            raise InvalidInput('Special NTFS file attributes require explicit native handling; original retained')
        self.ordinary_stream(handle)
        return validate_file_metadata({'schema': 'eeas.ntfs-file-metadata.v1', 'attributes': info['attributes'],
            **{key: str(info[key]) for key in ('creation', 'access', 'write')}, 'security_sddl': self.security_sddl(handle)})

    def freeze_access_write_times(self, handle):
        keep = FILETIME(0xffffffff, 0xffffffff)
        if not self.set_times(handle, None, C.byref(keep), C.byref(keep)):
            self.error('SetFileTime(preserve pending handle timestamps)')

    def restore_metadata(self, handle, value):
        value = validate_file_metadata(value)
        descriptor, length = HANDLE(), DWORD()
        if not self.string_to_sd(value['security_sddl'], 1, C.byref(descriptor), C.byref(length)):
            self.error('ConvertStringSecurityDescriptorToSecurityDescriptorW')
        try:
            owner, group, dacl = HANDLE(), HANDLE(), HANDLE()
            defaulted, present, control, revision = BOOL(), BOOL(), WORD(), DWORD()
            if not self.sd_owner(descriptor, C.byref(owner), C.byref(defaulted)): self.error('GetSecurityDescriptorOwner')
            if not self.sd_group(descriptor, C.byref(group), C.byref(defaulted)): self.error('GetSecurityDescriptorGroup')
            if not self.sd_dacl(descriptor, C.byref(present), C.byref(dacl), C.byref(defaulted)): self.error('GetSecurityDescriptorDacl')
            if not owner.value or not group.value or not present.value or not dacl.value:
                raise InvalidInput('Recovery descriptor cannot broaden a missing/null DACL')
            if not self.get_control(descriptor, C.byref(control), C.byref(revision)): self.error('GetSecurityDescriptorControl')
            flags = 7 | (0x80000000 if control.value & 0x1000 else 0x20000000)
            result = self.set_security(handle, 1, flags, owner, group, dacl, None)
            if result: self.error('SetSecurityInfo(recovery)', result)
            stamps = [FILETIME(int(value[key]) & 0xffffffff, int(value[key]) >> 32) for key in ('creation', 'access', 'write')]
            if not self.set_times(handle, *(C.byref(stamp) for stamp in stamps)): self.error('SetFileTime(recovery)')
            self.attributes(handle, value['attributes'])
            self.freeze_access_write_times(handle)
            observed = self.capture_metadata(handle)
            if observed != value: raise Conflict('UNKNOWN_OUTCOME: restored NTFS metadata differs under its original handle')
        finally:
            self.local_free(descriptor)

    def rename(self, handle, parent, leaf):
        _component(leaf)
        encoded = leaf.encode("utf-16-le")
        size = C.sizeof(FILE_RENAME_INFO) + len(encoded)
        buffer = C.create_string_buffer(size); value = FILE_RENAME_INFO.from_buffer(buffer)
        value.flags = 1; value.root_directory = parent; value.name_length = len(encoded)
        C.memmove(C.addressof(buffer) + FILE_RENAME_INFO.name.offset, encoded, len(encoded))
        status = IO_STATUS_BLOCK()
        result = self.nt_set_information(handle, C.byref(status), buffer, size, 10)
        if result < 0: self.error("NtSetInformationFile(relative rename)", self.status_error(result))

    def delete(self, handle):
        value = BOOL(1)
        if not self.set_ex(handle, 4, C.byref(value), C.sizeof(value)): self.error("SetFileInformationByHandle(disposition)")

    def directory_entries(self, handle):
        first = True
        while True:
            buffer = C.create_string_buffer(64 * 1024)
            if not self.get_ex(handle, 11 if first else 10, buffer, len(buffer)):
                if C.get_last_error() == 18: return
                self.error("GetFileInformationByHandleEx(directory)")
            first = False; offset = 0
            while True:
                if offset + FILE_ID_BOTH_DIR_INFO.name.offset > len(buffer): raise InvalidInput("Invalid native directory frame")
                value = FILE_ID_BOTH_DIR_INFO.from_buffer(buffer, offset)
                end = offset + FILE_ID_BOTH_DIR_INFO.name.offset + value.name_length
                if value.name_length % 2 or value.name_length > 1024 or end > len(buffer):
                    raise InvalidInput("Invalid native directory filename")
                try: name = buffer.raw[offset + FILE_ID_BOTH_DIR_INFO.name.offset:end].decode("utf-16-le", errors="strict")
                except UnicodeError: raise InvalidInput("Native directory filename is not valid Unicode") from None
                if name not in {".", ".."}: yield name, value.file_id & ((1 << 64) - 1)
                if not value.next: break
                if value.next < FILE_ID_BOTH_DIR_INFO.name.offset + value.name_length or value.next % 8:
                    raise InvalidInput("Invalid native directory entry offset")
                offset += value.next

    def lock(self, handle, *, blocking=True):
        if type(blocking) is not bool:raise InvalidInput('Lock wait mode must be a boolean')
        overlap = OVERLAPPED()
        if not self.lock_file(handle, 2 | (0 if blocking else 1), 0, 1, 0, C.byref(overlap)): self.error("LockFileEx")
        return overlap

    def unlock(self, handle, overlap):
        if not self.unlock_file(handle, 0, 1, 0, C.byref(overlap)): self.error("UnlockFileEx")


@dataclass(frozen=True)
class WindowsParentHandle:
    native_handle: int
    path: str
    root: str


class WindowsFileBusy(Conflict):
    code = 'BUSY'


class WindowsReadProofScope:
    """Operation-local mandatory-sharing proof; never a persisted CLEAR cache.

    Every leaf and ancestor stays open without delete sharing. Leaves also deny
    write sharing. A fresh safe path/identity probe is required for each reuse.
    Raw bytes are bounded again by the owning manual-input ledger.
    """
    MAX_FILES, MAX_HANDLES = 256, 4096

    def __init__(self, adapter, binding, private_check, *, max_files=MAX_FILES, max_handles=MAX_HANDLES):
        if (type(max_files) is not int or not 1 <= max_files <= self.MAX_FILES or
                type(max_handles) is not int or not 1 <= max_handles <= self.MAX_HANDLES):
            raise InvalidInput('Read-proof resource limits must stay within their fixed caps')
        self.adapter, self.binding, self.private_check = adapter, binding, private_check
        self.max_files, self.max_handles = max_files, max_handles
        self.entries, self.handle_count, self.probe_cost, self.closed = {}, 0, 0, False

    def contains(self, path):
        if self.closed: raise Conflict('Read-proof scope has ended')
        return path in self.entries

    def _cost(self, path):
        root = normalized_root(self.binding['root'])
        # _chain opens the drive and each root component, open_parent opens
        # each relative parent, and one leaf handle follows. Reserve the same
        # cost for the temporary identity probe before opening anything.
        tail = ntpath.splitdrive(root)[1].strip('\\')
        return 1 + (len(tail.split('\\')) if tail else 0) + len(path.split('/'))

    def _verify(self, path, entry):
        a = self.adapter
        with a.open_parent(self.binding, path, private_check=self.private_check) as (parent, name):
            handle = a.api.open_relative(parent.native_handle, name, FILE_READ_ATTRIBUTES, FILE_SHARE_READ)
            try:
                live = a._check(handle, entry['expected'], directory=False)
                held = a._check(entry['handle'], entry['expected'], directory=False)
                if any(entry['identity'][key] != live[key] or live[key] != held[key]
                        for key in ('device', 'inode', 'size', 'write', 'creation')):
                    raise Conflict('SOURCE_DRIFT: held read proof no longer names the same frozen bytes')
            finally: a.api.close(handle)

    def read_regular(self, path, *, max_bytes):
        if self.closed: raise Conflict('Read-proof scope has ended')
        relative_path(path)
        if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_FILE_BYTES:
            raise InvalidInput('Read-proof byte cap exceeds the existing authority')
        a = self.adapter
        try:
            entry = self.entries.get(path)
            if entry is not None:
                if len(entry['raw']) > max_bytes: raise InvalidInput('Held proof exceeds the selected byte cap')
                self._verify(path, entry)
                return entry['raw'], True
            cost = self._cost(path)
            if len(self.entries) >= self.max_files or self.handle_count + cost + max(cost,self.probe_cost) > self.max_handles:
                raise InvalidInput('Read-proof file/handle scope exceeded; no uncovered path was skipped')
            with ExitStack() as pending:
                parent, name = pending.enter_context(a.open_parent(self.binding, path, private_check=self.private_check))
                expected = ntpath.join(parent.path, name)
                handle = a.api.open_relative(parent.native_handle, name, GENERIC_READ, FILE_SHARE_READ)
                pending.callback(a.api.close, handle)
                before = a._check(handle, expected, directory=False)
                if before['size'] > max_bytes: raise InvalidInput('Read-proof file exceeds its remaining byte budget')
                chunks, received = [], 0
                while True:
                    part = a.api.read(handle, min(1024 * 1024, max_bytes + 1 - received))
                    if not part: break
                    received += len(part); chunks.append(part)
                    if received > max_bytes: raise InvalidInput('Read-proof file grew beyond its byte cap')
                entry = {'handle': handle, 'identity': before, 'expected': expected, 'raw': b''.join(chunks)}
                if received != before['size']: raise Conflict('SOURCE_DRIFT: read-proof size changed')
                self._verify(path, entry)
                entry['lifetime'] = pending.pop_all()
            self.entries[path] = entry; self.handle_count += cost; self.probe_cost = max(cost,self.probe_cost)
            return entry['raw'], False
        except OSError as error: a._translate(error)

    def close(self):
        if self.closed: return
        self.closed = True
        first_error = None
        for entry in reversed(list(self.entries.values())):
            try: entry['lifetime'].close()
            except Exception as error:
                if first_error is None: first_error = error
        self.entries.clear(); self.handle_count = self.probe_cost = 0
        if first_error is not None: raise first_error


class WindowsWorkspaceHandles:
    def __init__(self, api=None): self.api = api if api is not None else Win32Kernel()

    @contextmanager
    def read_proof_scope(self, binding, *, private_check=None, max_files=256, max_handles=4096):
        scope = WindowsReadProofScope(self, binding, private_check, max_files=max_files, max_handles=max_handles)
        try: yield scope
        finally: scope.close()

    @staticmethod
    def _translate(error):
        code = getattr(error, "winerror", error.errno)
        if code in {2, 3}: raise NotFound("Selected Windows file or folder does not exist") from None
        if code in {32, 33}: raise WindowsFileBusy("Selected Windows file is busy or its namespace changed") from None
        raise InvalidInput("Windows file handle operation was denied; no outside path was followed") from None

    def _check(self, handle, expected, *, directory):
        info = self.api.info(handle)
        if info["attributes"] & FILE_ATTRIBUTE_REPARSE_POINT:
            raise InvalidInput("Reparse points, symlinks and junctions are outside the selected-folder scope")
        if bool(info["attributes"] & FILE_ATTRIBUTE_DIRECTORY) != directory:
            raise InvalidInput("Selected Windows path has the wrong file kind")
        if not _equal(self.api.final_path(handle), expected):
            raise Conflict("WINDOWS_PATH_DRIFT: opened handle resolves to another path")
        if not directory and info["links"] != 1:
            raise InvalidInput("Multiply linked files are outside this Windows write/read qualification")
        return info

    @contextmanager
    def _chain(self, root, *, create=False, writable=False):
        root = normalized_root(root); drive, tail = ntpath.splitdrive(root)
        parts = tail[1:].split("\\") if tail[1:] else []
        paths = [drive + "\\"]
        for part in parts: paths.append(ntpath.join(paths[-1], part))
        opened = []
        try:
            for index, path in enumerate(paths):
                access = 0x81 | (2 if writable and index == len(paths) - 1 else 0)
                handle = (self.api.open(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, directory=True) if not index else
                    self.api.open_relative(opened[-1], ntpath.basename(path), access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        OPEN_ALWAYS if create else OPEN_EXISTING, directory=True))
                opened.append(handle); self._check(handle, path, directory=True)
                if not index and self.api.filesystem(handle).upper() != "NTFS":
                    raise InvalidInput("This Windows handle adapter currently supports local NTFS volumes")
                # Sharing restrictions protect names while handle-relative opens
                # avoid reparsing the ancestor's absolute name again.
            yield WindowsParentHandle(opened[-1], self.api.final_path(opened[-1]), self.api.final_path(opened[0]))
            for handle, expected in zip(opened, paths):
                self._check(handle, expected, directory=True)
        except OSError as error: self._translate(error)
        finally:
            for handle in reversed(opened): self.api.close(handle)

    def root_identity(self, value):
        with self._chain(value) as root:
            info = self.api.info(root.native_handle)
            return root.path, {"device": info["device"], "inode": info["inode"]}

    @contextmanager
    def open_parent(self, binding, path, *, directory=False, private_check=None, _writable=False):
        relative_path(path, empty=directory)
        if private_check: private_check(binding, path)
        root = normalized_root(binding["root"])
        parts = path.split("/") if path else []
        parent_parts = parts if directory else parts[:-1]
        with self._chain(root, writable=_writable and not parent_parts) as root_handle:
            info = self.api.info(root_handle.native_handle)
            if {"device": info["device"], "inode": info["inode"]} != binding["root_identity"]:
                raise Conflict("WORKSPACE_ROOT_DRIFT: Windows selected folder identity changed")
            descendants, current = [], root_handle.path
            try:
                parent = root_handle
                for index, part in enumerate(parent_parts):
                    current = ntpath.join(current, part)
                    access = 0x81 | (2 if _writable and index == len(parent_parts) - 1 else 0)
                    handle = self.api.open_relative(parent.native_handle, part, access, FILE_SHARE_READ | FILE_SHARE_WRITE, directory=True)
                    descendants.append(handle); self._check(handle, current, directory=True)
                    parent = WindowsParentHandle(handle, current, root)
                yield parent, None if directory else parts[-1]
                current = root_handle.path
                for handle, part in zip(descendants, parent_parts):
                    current = ntpath.join(current, part)
                    self._check(handle, current, directory=True)
            except OSError as error: self._translate(error)
            finally:
                for handle in reversed(descendants): self.api.close(handle)

    def read_regular(self, binding, path, *, missing=False, private_check=None, shared_saved_document=False,max_bytes=MAX_FILE_BYTES):
        """Opt in only for saved native-document snapshots, never workspace writes.

        Writers may retain their own handles in this mode. Reopening the leaf
        through the pinned parent prevents returning an unlinked old file after
        an application's atomic save replaced its name.
        """
        if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_FILE_BYTES:
            raise InvalidInput('Read byte limit must be an integer within the existing 16 MiB authority cap')
        if type(shared_saved_document) is not bool:
            raise InvalidInput("Shared saved-document reads require an explicit boolean")
        sharing = FILE_SHARE_READ | (FILE_SHARE_WRITE | FILE_SHARE_DELETE if shared_saved_document else 0)
        with self.open_parent(binding, path, private_check=private_check) as (parent, name):
            expected = ntpath.join(parent.path, name)
            try: handle = self.api.open_relative(parent.native_handle, name, GENERIC_READ, sharing)
            except OSError as error:
                if missing and getattr(error, "winerror", error.errno) == 2: return None
                self._translate(error)
            try:
                before = self._check(handle, expected, directory=False)
                if before["size"] > max_bytes:
                    raise InvalidInput("Local read exceeds 16 MiB; no bytes were truncated" if max_bytes==MAX_FILE_BYTES else 'Local read exceeds the selected smaller byte limit')
                chunks, received = [], 0
                while True:
                    part = self.api.read(handle, min(1024 * 1024, max_bytes + 1 - received))
                    if not part: break
                    received += len(part); chunks.append(part)
                    if received > max_bytes: raise InvalidInput("Local file grew beyond the read limit")
                after = self._check(handle, expected, directory=False)
                if any(before[key] != after[key] for key in ("device", "inode", "size", "write", "creation")):
                    raise Conflict("SOURCE_DRIFT: Windows file changed while being read")
                if shared_saved_document:
                    live = self.api.open_relative(parent.native_handle, name, FILE_READ_ATTRIBUTES, sharing)
                    try:
                        current = self._check(live, expected, directory=False)
                        held = self._check(handle, expected, directory=False)
                        keys = ("device", "inode", "size", "write", "creation")
                        if (received != before["size"] or any(before[key] != held[key] or
                                current[key] != held[key] for key in keys)):
                            raise Conflict("SOURCE_DRIFT: saved document changed or its leaf was replaced during reading")
                    finally:
                        self.api.close(live)
                return b"".join(chunks)
            except OSError as error: self._translate(error)
            finally: self.api.close(handle)

    def list_directory(self, binding, path="", *, private_check=None):
        rows = []
        with self.open_parent(binding, path, directory=True, private_check=private_check) as (parent, _):
            try:
                for name, discovered_identity in self.api.directory_entries(parent.native_handle):
                    child = "/".join(filter(None, (path, name)))
                    try:
                        relative_path(child)
                        if private_check: private_check(binding, child)
                    except InvalidInput: continue
                    expected = ntpath.join(parent.path, name)
                    try: handle = self.api.open_relative(parent.native_handle, name, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        directory=None, reject_reparse=False)
                    except OSError as error:
                        if getattr(error, "winerror", error.errno) == 2: continue
                        raise
                    try:
                        info = self.api.info(handle)
                        if not _equal(self.api.final_path(handle), expected) or info["inode"] != discovered_identity:
                            raise Conflict("SOURCE_DRIFT: Windows directory child was replaced during listing")
                        reparse = bool(info["attributes"] & FILE_ATTRIBUTE_REPARSE_POINT)
                        directory = bool(info["attributes"] & FILE_ATTRIBUTE_DIRECTORY)
                        kind = "symlink" if reparse else "directory" if directory else "file"
                        readable = not reparse and (directory or info["links"] == 1)
                        rows.append({"name": name, "path": child, "type": kind, "readable": readable, "byte_length": info["size"]})
                        if len(rows) > 10000: raise InvalidInput("Directory exceeds bounded 10000-entry listing")
                    finally: self.api.close(handle)
            except OSError as error: self._translate(error)
        return sorted(rows, key=lambda item: (item["type"] != "directory", item["name"]))

    @staticmethod
    def _expected_parent(info, expected_parent):
        if expected_parent is not None and any(str(info[key]) != str(expected_parent.get(key)) for key in ('device', 'inode')):
            raise Conflict('SOURCE_DRIFT: Windows original editor parent identity changed')

    def metadata_regular(self, binding, path, *, private_check=None):
        """Read supported NTFS recovery metadata without changing file bytes."""
        with self.open_parent(binding, path, private_check=private_check) as (parent, name):
            handle = None
            try:
                expected = ntpath.join(parent.path, name)
                handle = self.api.open_relative(parent.native_handle, name, FILE_READ_ATTRIBUTES | READ_CONTROL, FILE_SHARE_READ)
                before = self._check(handle, expected, directory=False)
                metadata = self.api.capture_metadata(handle, before)
                after = self._check(handle, expected, directory=False)
                if any(before[key] != after[key] for key in ('device', 'inode', 'attributes', 'creation', 'write', 'size')):
                    raise Conflict('SOURCE_DRIFT: NTFS metadata changed while being observed')
                return {'metadata': metadata, 'sha256': file_metadata_sha256(metadata),
                    'identity': {key: str(before[key]) for key in ('device', 'inode')}}
            except OSError as error: self._translate(error)
            finally:
                if handle is not None: self.api.close(handle)

    def compare_delete(self, binding, path, *, expected_raw, expected_parent, private_check=None,
                       expected_metadata_sha256=None, expected_identity=None, persist_recovery=None):
        """Delete only the exact ordinary leaf held against writers and rename.

        The caller must persist intent and retain the original immutable byte
        source before entry. This primitive neither removes directories nor
        walks a tree and never retries a disposition with an unknown outcome.
        """
        if (not isinstance(expected_raw, bytes) or len(expected_raw) > MAX_EDIT_BYTES
                or not isinstance(expected_parent, dict) or not callable(persist_recovery)):
            raise InvalidInput('Bounded exact Windows deletion preimage is required')
        with self.open_parent(binding, path, private_check=private_check, _writable=True) as (parent, name):
            self._expected_parent(self._check(parent.native_handle, parent.path, directory=True), expected_parent)
            handle, dispatched = None, False
            expected = ntpath.join(parent.path, name)
            try:
                try:
                    handle = self.api.open_relative(parent.native_handle, name,
                        GENERIC_READ | FILE_READ_ATTRIBUTES | READ_CONTROL | DELETE, FILE_SHARE_READ, OPEN_EXISTING)
                except OSError as error:
                    if getattr(error, 'winerror', error.errno) == 2:
                        raise Conflict('SOURCE_DRIFT: Windows deletion source is absent') from None
                    raise
                before = self._check(handle, expected, directory=False)
                if before['attributes'] & FILE_ATTRIBUTE_READONLY:
                    raise InvalidInput('Readonly Windows source cannot be deleted')
                if before['size'] != len(expected_raw):
                    raise Conflict('SOURCE_DRIFT: Windows deletion preimage size changed')
                identity = {key: str(before[key]) for key in ('device', 'inode')}
                if expected_identity is not None and identity != expected_identity:
                    raise Conflict('SOURCE_DRIFT: Windows deletion leaf was replaced')
                metadata = self.api.capture_metadata(handle, before)
                metadata_sha = file_metadata_sha256(metadata)
                if expected_metadata_sha256 is not None and metadata_sha != expected_metadata_sha256:
                    raise Conflict('SOURCE_DRIFT: original Windows file permissions, attributes or timestamps changed')
                chunks, received = [], 0
                while True:
                    part = self.api.read(handle, min(1024*1024, MAX_EDIT_BYTES+1-received))
                    if not part: break
                    received += len(part); chunks.append(part)
                    if received > MAX_EDIT_BYTES:
                        raise InvalidInput('Windows deletion preimage exceeds its bound')
                after = self._check(handle, expected, directory=False)
                if b''.join(chunks) != expected_raw or any(before[key] != after[key]
                        for key in ('device', 'inode', 'size', 'write', 'creation')):
                    raise Conflict('SOURCE_DRIFT: Windows deletion preimage changed under its exclusive handle')
                self._expected_parent(self._check(parent.native_handle, parent.path, directory=True), expected_parent)
                recovery_ref = persist_recovery({'metadata': metadata, 'sha256': metadata_sha, 'identity': identity})
                if (not isinstance(recovery_ref, dict) or not re.fullmatch(r'[a-f0-9]{64}', recovery_ref.get('id', ''))
                        or not re.fullmatch(r'[a-f0-9]{64}', recovery_ref.get('sha256', ''))):
                    raise Conflict('Original NTFS metadata recovery material was not persisted')
                # Security changes are not excluded by ordinary file sharing.
                # Recheck them after recovery publication, before disposition.
                if file_metadata_sha256(self.api.capture_metadata(handle)) != metadata_sha:
                    raise Conflict('SOURCE_DRIFT: NTFS metadata changed before deletion')
                dispatched = True
                self.api.delete(handle)
                return {'kind': 'WINDOWS_EXCLUSIVE_HANDLE_COMPARE_DELETE',
                    'external_writers_and_delete_excluded': True, 'crash_atomic': False,
                    'before_bytes': len(expected_raw), 'after_exists': False,
                    'deleted_identity': {key: str(before[key]) for key in ('device', 'inode')},
                    'metadata_recovery_ref': recovery_ref, 'metadata_sha256': metadata_sha,
                    'namespace_absence_requires_post_observation': True}
            except OSError as error:
                if dispatched:
                    raise Conflict('UNKNOWN_OUTCOME: Windows delete disposition needs original outcome inspection') from None
                self._translate(error)
            finally:
                if handle is not None: self.api.close(handle)

    def compare_write(self, binding, path, raw, *, expected_raw, private_check=None, expected_parent=None,
                      restore_metadata=None, record_created=None):
        """Compare and commit under one handle excluding external writers/delete.

        Existing files keep their identity and ACL. This is an exclusive in-place
        commit, not crash-atomic rename; the caller's immutable before snapshot
        and write-begun journal must remain available for interrupted outcomes.
        """
        if (not isinstance(raw, bytes) or len(raw) > MAX_EDIT_BYTES
                or expected_raw is not None and (not isinstance(expected_raw, bytes) or len(expected_raw) > MAX_FILE_BYTES)):
            raise InvalidInput("Bounded exact before/after Windows edit bytes are required")
        if restore_metadata is not None:
            validate_file_metadata(restore_metadata)
            if expected_raw is not None: raise InvalidInput('Metadata recovery requires exclusive file recreation')
        with self.open_parent(binding, path, private_check=private_check, _writable=True) as (parent, name):
            self._expected_parent(self._check(parent.native_handle, parent.path, directory=True), expected_parent)
            handle, dispatched = None, False
            expected = ntpath.join(parent.path, name)
            try:
                try:
                    access = GENERIC_READ | GENERIC_WRITE | FILE_READ_ATTRIBUTES
                    if record_created is not None or restore_metadata is not None: access |= READ_CONTROL
                    if restore_metadata is not None: access |= WRITE_DAC | WRITE_OWNER
                    handle = self.api.open_relative(parent.native_handle, name, access, FILE_SHARE_READ,
                        CREATE_NEW if expected_raw is None else OPEN_EXISTING, through=True)
                except OSError as error:
                    if getattr(error, 'winerror', error.errno) in {2, 80, 183}:
                        raise Conflict("SOURCE_DRIFT: Windows source changed before exclusive commit") from None
                    raise
                info = self._check(handle, expected, directory=False)
                if info['attributes'] & FILE_ATTRIBUTE_READONLY:
                    raise InvalidInput("Readonly Windows source cannot be edited")
                if expected_raw is not None:
                    if info['size'] != len(expected_raw):
                        raise Conflict("SOURCE_DRIFT: Windows source differs under the exclusive commit handle")
                    chunks, received = [], 0
                    while True:
                        part = self.api.read(handle, min(1024 * 1024, MAX_FILE_BYTES + 1 - received))
                        if not part: break
                        received += len(part); chunks.append(part)
                        if received > MAX_FILE_BYTES: raise InvalidInput("Windows before snapshot exceeds 16 MiB")
                    if b''.join(chunks) != expected_raw:
                        raise Conflict("SOURCE_DRIFT: Windows source differs under the exclusive commit handle")
                self.api.seek_start(handle)
                # Set before any mutating syscall: an interrupted write cannot
                # be attributed to a definitely untouched target.
                dispatched = True
                offset = 0
                while offset < len(raw):
                    written = self.api.write(handle, raw[offset:])
                    if not 0 < written <= len(raw) - offset:
                        raise Conflict("UNKNOWN_OUTCOME: Windows exclusive write made no bounded progress")
                    offset += written
                self.api.truncate_here(handle)
                self.api.flush(handle)
                self._check(handle, expected, directory=False)
                self.api.seek_start(handle)
                observed = bytearray()
                while True:
                    part = self.api.read(handle, min(1024 * 1024, MAX_EDIT_BYTES + 1 - len(observed)))
                    if not part: break
                    observed.extend(part)
                    if len(observed) > MAX_EDIT_BYTES: break
                if bytes(observed) != raw:
                    raise Conflict("UNKNOWN_OUTCOME: committed Windows bytes differ under the exclusive handle")
                metadata_ref = None
                if restore_metadata is not None:
                    self.api.restore_metadata(handle, restore_metadata)
                if record_created is not None:
                    self.api.freeze_access_write_times(handle)
                    metadata = self.api.capture_metadata(handle)
                    info = self._check(handle, expected, directory=False)
                    metadata_ref = record_created({'metadata': metadata, 'sha256': file_metadata_sha256(metadata),
                        'identity': {key: str(info[key]) for key in ('device', 'inode')}})
                    if (not isinstance(metadata_ref, dict) or not re.fullmatch(r'[a-f0-9]{64}', metadata_ref.get('id', ''))
                            or not re.fullmatch(r'[a-f0-9]{64}', metadata_ref.get('sha256', ''))):
                        raise Conflict('UNKNOWN_OUTCOME: created file metadata endpoint was not persisted')
                return {'kind': 'WINDOWS_EXCLUSIVE_HANDLE_COMPARE_COMMIT',
                    'external_writers_and_delete_excluded': True, 'crash_atomic': False,
                    'before_bytes': len(expected_raw) if expected_raw is not None else None,
                    'after_bytes': len(raw), 'created': expected_raw is None,
                    **({'created_metadata_ref': metadata_ref, 'metadata_restored': restore_metadata is not None}
                        if metadata_ref is not None else {})}
            except OSError as error:
                if dispatched:
                    raise Conflict("UNKNOWN_OUTCOME: interrupted Windows exclusive edit; inspect immutable history") from None
                self._translate(error)
            finally:
                if handle is not None: self.api.close(handle)

    def replace_atomic(self, binding, path, raw, *, private_check=None):
        if not isinstance(raw, bytes) or len(raw) > MAX_EDIT_BYTES: raise InvalidInput("Windows edit exceeds its 1 MiB byte bound")
        with self.open_parent(binding, path, private_check=private_check, _writable=True) as (parent, name):
            temporary = ntpath.join(parent.path, ".eeas-write-" + uuid.uuid4().hex)
            source = None; renamed = False
            try:
                access = GENERIC_WRITE | FILE_READ_ATTRIBUTES | DELETE | READ_CONTROL | WRITE_DAC | WRITE_OWNER
                source = self.api.open_relative(parent.native_handle, ntpath.basename(temporary), access, 0, CREATE_NEW, through=True)
                self._check(source, temporary, directory=False)
                offset = 0
                while offset < len(raw):
                    written = self.api.write(source, raw[offset:])
                    if not 0 < written <= len(raw) - offset: raise Conflict("Windows write made no bounded progress")
                    offset += written
                self.api.flush(source)
                destination = ntpath.join(parent.path, name)
                try: old = self.api.open_relative(parent.native_handle, name, READ_CONTROL | FILE_READ_ATTRIBUTES, FILE_SHARE_READ)
                except OSError as error:
                    if getattr(error, "winerror", error.errno) != 2: raise
                else:
                    try:
                        old_info = self._check(old, destination, directory=False)
                        if old_info["attributes"] & FILE_ATTRIBUTE_READONLY: raise InvalidInput("Readonly Windows source cannot be replaced")
                        self.api.copy_security(old, source)
                        self.api.attributes(source, old_info["attributes"])
                    finally: self.api.close(old)
                self.api.rename(source, parent.native_handle, name); renamed = True
                self.api.flush(source)
                self._check(source, destination, directory=False)
            except OSError as error: self._translate(error)
            finally:
                if source is not None:
                    try:
                        if not renamed: self.api.delete(source)
                    finally: self.api.close(source)

    def create_directories(self, binding, relative_directory, *, private_check=None):
        """Create confirmed output directories while keeping every ancestor pinned."""
        relative_path(relative_directory, empty=True)
        with self.open_parent(binding, "", directory=True, private_check=private_check) as (parent, _):
            descendants, current, relative, current_handle = [], parent.path, [], parent.native_handle
            try:
                for part in relative_directory.split("/") if relative_directory else []:
                    relative.append(part)
                    if private_check: private_check(binding, "/".join(relative))
                    current = ntpath.join(current, part)
                    handle = self.api.open_relative(current_handle, part, 0x81, FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_ALWAYS, directory=True)
                    descendants.append(handle); self._check(handle, current, directory=True)
                    current_handle = handle
            except OSError as error: self._translate(error)
            finally:
                for handle in reversed(descendants): self.api.close(handle)

    def walk_files(self, binding, path="", *, private_check=None, max_files=2001, max_directories=2000):
        """Handle-checked discovery for Windows search; never traverse a junction."""
        if type(max_files) is not int or not 1 <= max_files <= 10000:
            raise InvalidInput("Invalid Windows search file bound")
        if type(max_directories) is not int or not 1 <= max_directories <= 10000:
            raise InvalidInput("Invalid Windows search directory bound")
        relative_path(path, empty=True); pending = [path]; visited = found = 0
        while pending:
            current = pending.pop(0); visited += 1
            if visited > max_directories: raise InvalidInput("Windows search exceeds its bounded directory traversal")
            for child in self.list_directory(binding, current, private_check=private_check):
                if not child["readable"]: continue
                if child["type"] == "directory": pending.append(child["path"])
                elif child["type"] == "file":
                    yield child["path"]; found += 1
                    if found >= max_files: return

    @contextmanager
    def lock(self, path, *, blocking=True, create=True):
        if type(blocking) is not bool or type(create) is not bool:
            raise InvalidInput('Lock modes must be explicit booleans')
        absolute = normalized_root(str(path)); parent_path, name = ntpath.split(absolute); _component(name, internal=True)
        with self._chain(parent_path, create=create) as parent:
            handle, overlap = None, None
            try:
                handle = self.api.open_relative(parent.native_handle, name, GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE, OPEN_ALWAYS if create else OPEN_EXISTING)
                self._check(handle, absolute, directory=False)
                overlap = self.api.lock(handle) if blocking else self.api.lock(handle,blocking=False)
                yield
            except OSError as error: self._translate(error)
            finally:
                if handle is not None:
                    try:
                        if overlap is not None: self.api.unlock(handle, overlap)
                    finally: self.api.close(handle)
