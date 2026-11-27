"""Create one new ZIP in an explicitly selected existing directory.

No overwrite, directory creation, rename, deletion or archive extraction. Windows
uses the existing NTFS parent-handle adapter; POSIX uses nofollow dirfd opens.
Creation proof is persisted before the first data write. Interrupted files stay.
"""
from contextlib import contextmanager
import hashlib
import ntpath
import os
from pathlib import PurePosixPath
import stat

from packages.adapters.windows_workspace_handles import (
    WindowsWorkspaceHandles, GENERIC_READ, GENERIC_WRITE, FILE_READ_ATTRIBUTES,
    FILE_SHARE_READ, CREATE_NEW, OPEN_EXISTING)
from packages.domain.context_read_budget import current_context_read_budget
from packages.domain.local_workspaces import digest
from packages.domain.questions import stamp
from packages.domain.store import Conflict, InvalidInput

MAX_EXPORT_BYTES = 24 * 1024 * 1024


class ExportWriteFailure(Conflict):
    def __init__(self, effect):
        self.effect = effect
        super().__init__('Export was not confirmed; preserve the original operation and any created file')


def _identity(info, *, windows):
    if windows:
        return {'device': str(info['device']), 'inode': str(info['inode']),
            'birth': str(info['creation']), 'strength': 'DEVICE_FILE_ID_AND_CREATION_TIME'}
    birth = getattr(info, 'st_birthtime_ns', None)
    return {'device': str(info.st_dev), 'inode': str(info.st_ino),
        'birth': str(birth) if birth is not None else None,
        'strength': 'DEVICE_INODE_AND_BIRTH' if birth is not None else 'DEVICE_INODE_WITHOUT_BIRTH'}


def _bounded_read(reader, maximum=MAX_EXPORT_BYTES):
    def read(limit):
        chunks, received = [], 0
        while True:
            part = reader(min(1024*1024, limit+1-received))
            if not part: break
            received += len(part)
            if received > limit: raise Conflict('Export verification exceeded the bounded read')
            chunks.append(part)
        return b''.join(chunks)
    allowance = current_context_read_budget()
    return allowance.guarded(read, maximum) if allowance is not None else read(maximum)


class DeliveryExportFiles:
    def __init__(self, *, native=None):
        self.native = native

    def _native(self):
        if self.native is not None: return self.native
        if os.name == 'nt':
            self.native = WindowsWorkspaceHandles()
            return self.native
        return None

    @staticmethod
    def same_destination(destination, filename):
        if not isinstance(filename,str):return False
        if destination['platform']=='WINDOWS_NTFS_HANDLES':
            return filename.replace('/','\\').casefold()==destination['path'].casefold()
        return filename==destination['path']

    @contextmanager
    def _posix_parent(self, directory):
        if os.name != 'posix': raise InvalidInput('Unsupported export filesystem')
        if (not isinstance(directory, str) or not directory.startswith('/')
                or str(PurePosixPath(directory)) != directory or '\0' in directory
                or any(p in {'.','..'} for p in directory.split('/'))):
            raise InvalidInput('An exact existing absolute export folder is required')
        handles = []
        try:
            handles.append(os.open('/', os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW))
            for part in directory.split('/')[1:]:
                if part: handles.append(os.open(part, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=handles[-1]))
            yield handles[-1], os.fstat(handles[-1])
        finally:
            for fd in reversed(handles): os.close(fd)

    def capture(self, filename):
        if (not isinstance(filename, str) or not 1 <= len(filename) <= 4096
                or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in filename)):
            raise InvalidInput('Choose an ordinary bounded ZIP path in the native dialog')
        native = self._native()
        if native is not None:
            parent, name = ntpath.split(filename)
            canonical_parent, identity = native.root_identity(parent)
            identity = {k:str(identity[k]) for k in ('device','inode')}
            full = ntpath.join(canonical_parent, name)
            if ntpath.normcase(ntpath.normpath(filename)) != ntpath.normcase(full):
                raise InvalidInput('Export path must be normalized without traversal')
            platform = 'WINDOWS_NTFS_HANDLES'
        else:
            p = PurePosixPath(filename); parent, name = str(p.parent), p.name
            if str(p) != filename or not p.is_absolute(): raise InvalidInput('Export path must be absolute and normalized')
            with self._posix_parent(parent) as (_, info): identity = {'device': str(info.st_dev), 'inode': str(info.st_ino)}
            canonical_parent, full, platform = parent, filename, 'POSIX_DIRFD_NOFOLLOW'
        # Reuse the existing portable delivery filename policy without modifying edit caps.
        from packages.agent_sessions.work_deliveries import archive_name
        archive_name(name)
        if len(name) > 255 or '/' in name or '\\' in name or not name.lower().endswith('.zip'):
            raise InvalidInput('Choose a portable .zip filename')
        return {'path': full, 'directory': canonical_parent, 'name': name,
            'parent_identity': identity, 'platform': platform}

    @contextmanager
    def _parent(self, destination, *, writable=False):
        identity=destination['parent_identity']
        if (set(identity)!={'device','inode'} or any(not isinstance(v,str) or not v.isascii() or not v.isdecimal()
                or str(int(v))!=v or not 0<=int(v)<2**64 for v in identity.values())):
            raise InvalidInput('Original export parent identity must remain exact decimal strings')
        if destination['platform'] == 'WINDOWS_NTFS_HANDLES':
            native = self._native()
            if native is None: raise InvalidInput('Original Windows export needs its Windows handle adapter')
            binding = {'root': destination['directory'], 'root_identity': {k:int(v) for k,v in identity.items()}}
            with native.open_parent(binding, destination['name'], _writable=writable) as (parent, name):
                yield native, parent, name
        elif destination['platform'] == 'POSIX_DIRFD_NOFOLLOW':
            with self._posix_parent(destination['directory']) as (fd, info):
                if {'device': str(info.st_dev), 'inode': str(info.st_ino)} != identity:
                    raise Conflict('Original export folder identity changed')
                yield None, fd, destination['name']
        else: raise InvalidInput('Original export platform differs')

    def create(self, destination, raw, *, record_created, record_verified):
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_EXPORT_BYTES:
            raise InvalidInput('A bounded verified original archive is required')
        invoked, opened, native = False, False, None
        try:
            with self._parent(destination, writable=True) as (native, parent, name):
                handle = None
                try:
                    invoked = True
                    if native:
                        try:
                            handle = native.api.open_relative(parent.native_handle, name,
                                GENERIC_READ|GENERIC_WRITE|FILE_READ_ATTRIBUTES, FILE_SHARE_READ, CREATE_NEW, through=True)
                        except OSError as error:
                            # Classify the leaf's exclusive-create result before the
                            # parent context translates OS errors into scope errors.
                            effect = ('EXISTING_FILE_UNTOUCHED'
                                if getattr(error, 'winerror', error.errno) in {80, 183} else 'UNKNOWN')
                            raise ExportWriteFailure(effect) from error
                        opened = True
                        info = native._check(handle, ntpath.join(parent.path, name), directory=False)
                    else:
                        handle = os.open(name, os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=parent)
                        opened = True
                        info = os.fstat(handle)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise Conflict('Created export identity differs')
                    identity = _identity(info, windows=native is not None)
                    created = {'destination_sha256': digest(destination), 'file_identity': identity,
                        'created_at': stamp(), 'bytes_written_before_record': 0}
                    # Failure here preserves the empty file and never writes archive bytes.
                    record_created(created)
                    offset = 0
                    while offset < len(raw):
                        chunk = raw[offset:offset+1024*1024]
                        written = native.api.write(handle, chunk) if native else os.write(handle, chunk)
                        if not 0 < written <= len(chunk): raise Conflict('Export write did not progress')
                        offset += written
                    if native:
                        native.api.flush(handle); native.api.seek_start(handle)
                        before_read = native._check(handle, ntpath.join(parent.path, name), directory=False)
                        observed = _bounded_read(lambda n: native.api.read(handle, n))
                        after = native._check(handle, ntpath.join(parent.path, name), directory=False)
                        valid = (_identity(after, windows=True) == identity and after['size'] == len(raw)
                            and before_read['write']==after['write'])
                    else:
                        os.fsync(handle); os.lseek(handle, 0, os.SEEK_SET)
                        before = os.fstat(handle); observed = _bounded_read(lambda n: os.read(handle, n)); after = os.fstat(handle)
                        valid = (_identity(after, windows=False) == identity and after.st_size == len(raw)
                            and (before.st_mtime_ns, before.st_ctime_ns) == (after.st_mtime_ns, after.st_ctime_ns))
                        os.fsync(parent)
                    if not valid or observed != raw: raise Conflict('Export verification differed under the original handle')
                    proof = {'destination_sha256': digest(destination), 'file_identity': identity,
                        'sha256': hashlib.sha256(observed).hexdigest(), 'byte_length': len(observed),
                        'verified_at': stamp(), 'create_exclusive': True, 'overwrite_performed': False,
                        'crash_atomic': False, 'external_writers_and_delete_excluded': native is not None,
                        'file_metadata_restoration': False, 'contents_writer_attribution': 'NOT_PROVEN'}
                    record_verified(proof)  # Receipt is committed while the original handle remains held.
                    return proof
                finally:
                    if handle is not None:
                        if native: native.api.close(handle)
                        else: os.close(handle)
        except FileExistsError:
            raise ExportWriteFailure('UNKNOWN' if opened else 'EXISTING_FILE_UNTOUCHED') from None
        except OSError as error:
            if not opened and native is not None and getattr(error, 'winerror', error.errno) in {80,183}:
                raise ExportWriteFailure('EXISTING_FILE_UNTOUCHED') from None
            raise ExportWriteFailure('UNKNOWN' if invoked else 'NOT_INVOKED') from None
        except Exception as error:
            if isinstance(error, ExportWriteFailure): raise
            raise ExportWriteFailure('UNKNOWN' if invoked else 'NOT_INVOKED') from error

    def observe(self, destination, created, expected_sha, expected_length):
        if not isinstance(expected_length, int) or not 1 <= expected_length <= MAX_EXPORT_BYTES:
            raise InvalidInput('Original export length differs')
        if digest(destination) != created['destination_sha256']: raise Conflict('Original export creation proof differs')
        with self._parent(destination) as (native, parent, name):
            handle = None
            try:
                if native:
                    handle = native.api.open_relative(parent.native_handle, name, GENERIC_READ|FILE_READ_ATTRIBUTES,
                        FILE_SHARE_READ, OPEN_EXISTING)
                    before = native._check(handle, ntpath.join(parent.path, name), directory=False)
                else:
                    handle = os.open(name, os.O_RDONLY|os.O_NOFOLLOW, dir_fd=parent); before = os.fstat(handle)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1: raise Conflict('Original export file kind differs')
                identity = _identity(before, windows=native is not None)
                if identity != created['file_identity']: return {'status':'FILE_IDENTITY_CHANGED', 'files_read':0}
                size = before['size'] if native else before.st_size
                if size != expected_length: return {'status':'BYTE_LENGTH_CHANGED', 'files_read':0}
                raw = _bounded_read(lambda n: native.api.read(handle,n) if native else os.read(handle,n), expected_length)
                if native:
                    after = native._check(handle, ntpath.join(parent.path,name), directory=False)
                    stable = (_identity(after,windows=True)==identity and after['size']==size
                        and (before['write'],before['creation'])==(after['write'],after['creation']))
                else:
                    after=os.fstat(handle)
                    stable = (_identity(after,windows=False)==identity and after.st_size==size
                        and (before.st_mtime_ns,before.st_ctime_ns)==(after.st_mtime_ns,after.st_ctime_ns))
                checksum = hashlib.sha256(raw).hexdigest()
                return {'status':'MATCHING_CAPTURED_OUTPUT' if stable and checksum==expected_sha else 'FILE_BYTES_CHANGED',
                    'files_read':1,'file_identity':identity,'sha256':checksum,'byte_length':len(raw),
                    'observed_at':stamp(),'birth_identity_available':identity['birth'] is not None,
                    'contents_writer_attribution':'NOT_PROVEN'}
            finally:
                if handle is not None:
                    if native: native.api.close(handle)
                    else: os.close(handle)
