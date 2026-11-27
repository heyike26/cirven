"""Read-only classic Notepad inspection and exact reviewed append qualification.

This adapter never types, changes a selection, activates a window or saves a
file. The original Cua worker remains the sole input dispatcher.
"""
from __future__ import annotations

import ctypes as ct
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Literal

from pydantic import Field

from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import digest
from packages.domain.questions import Hash, RequestModel
from packages.domain.store import Conflict

SCOPE = 'CLASSIC_NOTEPAD_APPEND_AT_UTF16_END_SINGLE_TYPE_FOREGROUND'
REQUIRED_SOURCES = frozenset({
    'apps/api/native_tasks.py', 'packages/domain/native_task_authority.py',
    'packages/domain/desktop_intents.py', 'packages/adapters/cua_desktop_input.py',
    'packages/adapters/native_append.py', 'apps/api/native_append.py',
    'apps/desktop-host/main.cjs',
    'apps/desktop-host/native-append-host.cjs', 'apps/desktop-host/native-append-activation.cjs',
    'apps/desktop-host/preload.cjs', 'apps/api/native_append_host.py',
    'apps/api/native_append_composition.py', 'apps/api/main.py', 'apps/api/native_runtime.py',
    'packages/desktop/native_append_handoff.py', 'scripts/native_package_bootstrap.py',
    'packages/adapters/desktop_current_context.py', 'packages/domain/native_collaboration.py',
    'packages/adapters/windows_native_window_host.py', 'packages/adapters/opencode_desktop.py',
    'packages/adapters/opencode_provider.py', 'packages/adapters/model_transport.py',
    'packages/adapters/provider_transport_policy.py', 'packages/domain/provider_settings.py',
    'apps/api/providers.py',
})
ROUTE_CHECKS = frozenset({'exact_identity', 'single_type_text', 'utf16_end',
    'buffer_appended', 'disk_unchanged', 'bounds_unchanged', 'fixture_restored'})
HANDOFF_CHECKS = frozenset({'exact_identity', 'main_user_gesture', 'actual_foreground',
    'no_window_relayout', 'no_sdk_dispatch_before_handoff'})


def utf16_length(text):
    try:
        return len(text.encode('utf-16-le')) // 2
    except UnicodeError:
        raise Conflict('Append text must be valid Unicode') from None


def text_sha(text):
    try:
        return hashlib.sha256(text.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise Conflict('Append text must be valid Unicode') from None


class OsFingerprint(RequestModel):
    major: int = Field(strict=True, ge=1)
    minor: int = Field(strict=True, ge=0)
    build: int = Field(strict=True, ge=1)
    ubr: int = Field(strict=True, ge=0)
    architecture: Literal['AMD64']


class QualificationFile(RequestModel):
    path: str
    sha256: Hash


class RuntimeQualificationFile(QualificationFile):
    role: Literal['sdk_engine', 'sdk_component_manifest', 'sdk_dependency_manifest']


class NotepadExecutable(RequestModel):
    path: str
    sha256: Hash
    window_class: Literal['Notepad']
    edit_class: Literal['Edit']


class AppendLimits(RequestModel):
    max_payload_utf16_units: int = Field(strict=True, ge=1, le=256)
    max_buffer_utf16_units: int = Field(strict=True, ge=1, le=65536)


class AppendQualification(RequestModel):
    type: Literal['CLASSIC_NOTEPAD_APPEND_QUALIFICATION']
    version: Literal[1]
    scope: Literal['CLASSIC_NOTEPAD_APPEND_AT_UTF16_END_SINGLE_TYPE_FOREGROUND']
    os: OsFingerprint
    executable: NotepadExecutable
    limits: AppendLimits
    sdk_site_packages: str
    source_files: list[QualificationFile] = Field(min_length=7, max_length=200)
    runtime_files: list[RuntimeQualificationFile] = Field(min_length=3, max_length=2000)
    route_evidence: QualificationFile
    handoff_evidence: QualificationFile


def _safe_file(root, relative):
    root = Path(root).absolute()
    name = PurePosixPath(relative)
    if (not isinstance(relative, str) or '\\' in relative or ':' in relative or name.is_absolute()
            or any(p in {'', '.', '..'} for p in relative.split('/'))):
        raise Conflict('Qualification file must be an exact relative file')
    path = root
    for component in [*reversed(root.parents), root,
                      *[root.joinpath(*name.parts[:n]) for n in range(1, len(name.parts) + 1)]]:
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise Conflict('Qualification file traverses a link or reparse point')
    path = root.joinpath(*name.parts)
    if not path.is_file():
        raise Conflict('Qualification file is unavailable')
    return path


def checked_bytes(root, record, *, maximum=128 * 1024 * 1024):
    value = QualificationFile.model_validate({k: record[k] for k in ('path', 'sha256')}).model_dump()
    path = _safe_file(root, value['path'])
    before = path.stat()
    if not 0 < before.st_size <= maximum:
        raise Conflict('Qualification file exceeds its bound')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
        opened = os.fstat(stream.fileno())
    after = path.stat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if (signature(before) != signature(opened) or signature(before) != signature(after)
            or len(raw) > maximum or hashlib.sha256(raw).hexdigest() != value['sha256']):
        raise Conflict('Qualification file bytes or identity changed')
    _safe_file(root, value['path'])
    return raw


def stable_file_sha(path):
    """Hash the actual image without following a link/reparse substitution."""
    path = Path(path).absolute()
    _safe_file(path.parent, path.name)
    before = path.stat()
    if not 0 < before.st_size <= 128 * 1024 * 1024:
        raise Conflict('Actual executable exceeds the qualification hash bound')
    with path.open('rb') as stream:
        raw = stream.read(128 * 1024 * 1024 + 1)
        opened = os.fstat(stream.fileno())
    after = path.stat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if signature(before) != signature(opened) or signature(before) != signature(after):
        raise Conflict('Actual executable changed during its hash read')
    _safe_file(path.parent, path.name)
    return hashlib.sha256(raw).hexdigest()


def safe_policy_folder(runtime_root):
    """Only a literal private descendant of the selected runtime is writable."""
    root = Path(runtime_root).absolute()
    for part in [*reversed(root.parents), root]:
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise Conflict('Selected runtime ancestor is unsafe')
    folder = root
    for name in ('toolchain', 'native-append-policies'):
        folder = folder / name
        folder.mkdir(exist_ok=True)
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise Conflict('Append SDK policy directory is unsafe')
    return folder


class NotepadQualificationReader:
    """Only trusted composition can select the package path and its digest.

    Reviewed source/runtime hashes are rechecked on every admission/worker
    guard. A receipt assertion alone cannot replace its actual support match.
    """
    def __init__(self, *, source_root, runtime_root, evidence_root, policy_ref=None):
        self.source_root, self.runtime_root = Path(source_root), Path(runtime_root)
        self.evidence_root, self.policy_ref = Path(evidence_root), policy_ref
        self.reason = 'QUALIFICATION_NOT_CONFIGURED'

    def read(self, snapshot):
        try:
            if self.policy_ref is None:
                return None
            if set(self.policy_ref) != {'path', 'sha256'}:
                raise Conflict('Trusted qualification reference has unexpected fields')
            path = Path(self.policy_ref['path']).absolute()
            relative = path.relative_to(self.runtime_root.absolute()).as_posix()
            raw = checked_bytes(self.runtime_root, {'path': relative, 'sha256': self.policy_ref['sha256']}, maximum=1024 * 1024)
            pack = AppendQualification.model_validate_json(raw).model_dump()
            sources = pack['source_files']; runtime = pack['runtime_files']
            if (len({v['path'] for v in sources}) != len(sources)
                    or not REQUIRED_SOURCES <= {v['path'] for v in sources}
                    or len({v['path'] for v in runtime}) != len(runtime)
                    or {v['role'] for v in runtime} != {'sdk_engine', 'sdk_component_manifest', 'sdk_dependency_manifest'}):
                raise Conflict('Exact core/SDK qualification closure is incomplete')
            for value in sources:
                checked_bytes(self.source_root, value)
            manifests = {}
            for value in runtime:
                contents = checked_bytes(self.runtime_root, value)
                if value['role'] != 'sdk_engine':
                    if value['role'] in manifests:
                        raise Conflict('SDK closure has ambiguous manifests')
                    manifests[value['role']] = json.loads(contents)
            installed = {value['path']: value for value in runtime if value['role'] == 'sdk_engine'}
            component = manifests['sdk_component_manifest']
            if component.get('schema_version') != 'eeas.cua-shell-component.v1':
                raise Conflict('Reviewed original SDK component manifest is required')
            wheels = component.get('wheels')
            if (not isinstance(wheels, list) or not wheels or not any(
                    value.get('name') == 'cua-driver' and value.get('version') == '0.35.2' for value in wheels)):
                raise Conflict('Fixed original Cua 0.35.2 engine is required')
            declared = {}
            for wheel in wheels:
                members = wheel.get('engine_files')
                if not isinstance(members, dict) or not members:
                    raise Conflict('Component manifest installed engine closure is missing')
                for member, hash_value in members.items():
                    path = pack['sdk_site_packages'].rstrip('/') + '/' + member
                    if path in declared and declared[path] != hash_value:
                        raise Conflict('Component engine manifests disagree')
                    declared[path] = hash_value
            if any(installed.get(path, {}).get('sha256') != hash_value for path, hash_value in declared.items()):
                raise Conflict('Installed SDK engine bytes are outside the reviewed component closure')
            dependencies = manifests['sdk_dependency_manifest']
            if dependencies.get('type') != 'NATIVE_APPEND_SDK_DEPENDENCY_CLOSURE' or dependencies.get('version') != 1:
                raise Conflict('Exact installed SDK dependency closure is required')
            members = dependencies.get('files')
            if not isinstance(members, list) or not members or len(members) > 2000:
                raise Conflict('SDK dependency inventory is incomplete')
            dependency_files = [QualificationFile.model_validate(value).model_dump() for value in members]
            if (len({value['path'] for value in dependency_files}) != len(dependency_files)
                    or not set(declared) <= {value['path'] for value in dependency_files}
                    or any(installed.get(value['path'], {}).get('sha256') != value['sha256'] for value in dependency_files)):
                raise Conflict('Actual SDK dependencies differ from their installed inventory')
            exe = pack['executable']
            if (snapshot.get('os') != pack['os'] or snapshot.get('executable_sha256') != exe['sha256']
                    or snapshot.get('executable_path', '').casefold() != exe['path'].casefold()
                    or snapshot.get('window_class') != exe['window_class'] or snapshot.get('edit_class') != exe['edit_class']
                    or snapshot.get('same_user') is not True or snapshot.get('same_session') is not True
                    or snapshot.get('same_integrity') is not True):
                raise Conflict('Actual OS/executable/security does not match reviewed classic Notepad')
            for key, kind, checks in [('route_evidence', 'NATIVE_APPEND_ROUTE_EVIDENCE', ROUTE_CHECKS),
                                      ('handoff_evidence', 'NATIVE_APPEND_HANDOFF_EVIDENCE', HANDOFF_CHECKS)]:
                evidence = json.loads(checked_bytes(self.evidence_root, pack[key], maximum=1024 * 1024))
                actual_checks = evidence.get('checks')
                if (evidence.get('type') != kind or evidence.get('status') != 'ACTUAL_WINDOWS_INDEPENDENTLY_VERIFIED'
                        or evidence.get('scope') != SCOPE
                        or evidence.get('support') != {'os': pack['os'], 'executable_sha256': exe['sha256']}
                        or evidence.get('sources_sha256') != digest(sources)
                        or evidence.get('runtime_sha256') != digest(runtime)
                        or not isinstance(actual_checks, dict) or not checks <= set(actual_checks)
                        or any(value is not True for value in actual_checks.values())):
                    raise Conflict('Actual route/handoff evidence is incomplete or belongs to another closure')
            ref = {'id': 'notepad-append-' + self.policy_ref['sha256'][:40], 'revision': 1,
                'sha256': self.policy_ref['sha256']}
            self.reason = None
            return {'qualified': True, 'qualification_ref': ref,
                'target_identity_sha256': snapshot['identity_sha256'], 'delivery': 'FOREGROUND',
                'focus_effect': 'REQUIRED', 'effects': ['TYPE_TEXT'], 'owned_queue_drain_qualified': True,
                'scope': SCOPE, 'limits': pack['limits'], 'executable_path': exe['path'],
                'executable_sha256': exe['sha256'], 'os': pack['os'],
                'sdk_component_manifest_ref': {'path': str(self.runtime_root.absolute() / next(
                    value['path'] for value in runtime if value['role'] == 'sdk_component_manifest')),
                    'sha256': next(value['sha256'] for value in runtime if value['role'] == 'sdk_component_manifest')}}
        except Exception as error:
            self.reason = str(error)[:300] or type(error).__name__
            return None


class Win32ClassicNotepadInspector:
    """Bounded actual Win32 read; no input, activation, file save or test write."""
    def __init__(self, source, *, max_units=65536, timeout_ms=750):
        if os.name != 'nt':
            raise Conflict('ACTUAL_WINDOWS_NOTEPAD_INSPECTOR_UNAVAILABLE')
        self.source, self.api = source, source.api
        self.max_units, self.timeout_ms = max_units, timeout_ms
        bind, user = self.api._bind, self.api.user
        H, D, B = ct.c_void_p, ct.c_uint32, ct.c_int32
        self.find = bind(user, 'FindWindowExW', H, [H, H, ct.c_wchar_p, ct.c_wchar_p])
        self.class_name = bind(user, 'GetClassNameW', ct.c_int32, [H, ct.c_wchar_p, ct.c_int32])
        self.enabled = bind(user, 'IsWindowEnabled', B, [H])
        self.visible = bind(user, 'IsWindowVisible', B, [H])
        self.style = bind(user, 'GetWindowLongPtrW', ct.c_ssize_t, [H, ct.c_int32])
        self.send = bind(user, 'SendMessageTimeoutW', ct.c_ssize_t,
            [H, D, ct.c_size_t, ct.c_ssize_t, D, D, ct.POINTER(ct.c_size_t)])
        self.key = bind(user, 'GetAsyncKeyState', ct.c_int16, [ct.c_int32])
        class Gui(ct.Structure):
            _fields_ = [('cbSize', D), ('flags', D), ('hwndActive', H), ('hwndFocus', H),
                ('hwndCapture', H), ('hwndMenuOwner', H), ('hwndMoveSize', H), ('hwndCaret', H),
                ('rcCaret', ct.c_int32 * 4)]
        self.Gui = Gui
        self.gui = bind(user, 'GetGUIThreadInfo', B, [D, ct.POINTER(Gui)])

    def _message(self, hwnd, kind, wp=0, lp=0):
        result = ct.c_size_t()
        if not self.send(hwnd, kind, wp, lp, 0x0002 | 0x0020, self.timeout_ms, ct.byref(result)):
            raise Conflict('NOTEPAD_READ_TIMEOUT_OR_OS_REFUSAL')
        return result.value

    def _security(self, pid):
        H, D, B = ct.c_void_p, ct.c_uint32, ct.c_int32
        kernel, bind = self.api.kernel, self.api._bind
        adv = ct.WinDLL('advapi32', use_last_error=True)
        token_open = bind(adv, 'OpenProcessToken', B, [H, D, ct.POINTER(H)])
        token_info = bind(adv, 'GetTokenInformation', B, [H, ct.c_int32, H, D, ct.POINTER(D)])
        sid_string = bind(adv, 'ConvertSidToStringSidW', B, [H, ct.POINTER(H)])
        local_free = bind(kernel, 'LocalFree', H, [H])
        process = kernel.OpenProcess(0x1000, False, pid)
        if not process:
            raise Conflict('NOTEPAD_SECURITY_PROCESS_UNAVAILABLE')
        token = H()
        try:
            if not token_open(process, 0x0008, ct.byref(token)):
                raise Conflict('NOTEPAD_TOKEN_QUERY_UNAVAILABLE')
            result = []
            for kind in (1, 25):  # TokenUser, TokenIntegrityLevel; SID starts each structure.
                count = D(); token_info(token, kind, None, 0, ct.byref(count))
                if not 0 < count.value <= 65536:
                    raise Conflict('NOTEPAD_SECURITY_TOKEN_INVALID')
                buf = ct.create_string_buffer(count.value)
                if not token_info(token, kind, buf, count.value, ct.byref(count)):
                    raise Conflict('NOTEPAD_SECURITY_TOKEN_QUERY_FAILED')
                sid = ct.cast(buf, ct.POINTER(H))[0]; out = H()
                if not sid_string(sid, ct.byref(out)):
                    raise Conflict('NOTEPAD_SECURITY_SID_QUERY_FAILED')
                try: result.append(ct.wstring_at(out))
                finally: local_free(out)
            return result
        finally:
            if token: kernel.CloseHandle(token)
            kernel.CloseHandle(process)

    def _os(self):
        import winreg
        class Version(ct.Structure):
            _fields_ = [('size', ct.c_uint32), ('major', ct.c_uint32), ('minor', ct.c_uint32),
                ('build', ct.c_uint32), ('platform', ct.c_uint32), ('service', ct.c_wchar * 128)]
        version = Version(); version.size = ct.sizeof(version)
        rtl = self.api._bind(ct.WinDLL('ntdll'), 'RtlGetVersion', ct.c_int32, [ct.POINTER(Version)])
        if rtl(ct.byref(version)) != 0:
            raise Conflict('ACTUAL_OS_VERSION_UNAVAILABLE')
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            ubr = winreg.QueryValueEx(key, 'UBR')[0]
        process_machine, native_machine = ct.c_uint16(), ct.c_uint16()
        fn = self.api._bind(self.api.kernel, 'IsWow64Process2', ct.c_int32,
            [ct.c_void_p, ct.POINTER(ct.c_uint16), ct.POINTER(ct.c_uint16)])
        own = self.api.kernel.OpenProcess(0x1000, False, os.getpid())
        try:
            if not own or not fn(own, ct.byref(process_machine), ct.byref(native_machine)) or native_machine.value != 0x8664:
                raise Conflict('REVIEWED_AMD64_OS_REQUIRED')
        finally:
            if own: self.api.kernel.CloseHandle(own)
        return {'major': version.major, 'minor': version.minor, 'build': version.build,
            'ubr': ubr, 'architecture': 'AMD64'}

    def read(self, expected_window):
        before = self.source.inspect(expected_window['window_id'])
        if before.get('identity_sha256') != expected_window['identity_sha256'] or before.get('window_class') != 'Notepad':
            raise Conflict('EXACT_CLASSIC_NOTEPAD_IDENTITY_REQUIRED')
        root = before['window_id']; edit = int(self.find(root, None, 'Edit', None) or 0)
        if not edit or self.find(root, edit, 'Edit', None):
            raise Conflict('ONE_CLASSIC_NOTEPAD_EDIT_REQUIRED')
        child = self.api.window(edit)
        if child['pid'] != before['pid'] or child['class'] != 'Edit' or int(self.source.ancestor(edit, 2) or 0) != root:
            raise Conflict('CLASSIC_EDIT_OWNER_CHANGED')
        if not self.enabled(edit) or not self.visible(edit) or self.style(edit, -16) & 0x800:
            raise Conflict('CLASSIC_EDIT_NOT_WRITABLE')
        length = self._message(edit, 0x000E)
        if length > self.max_units:
            raise Conflict('NOTEPAD_BUFFER_EXCEEDS_READ_BOUND')
        buf = ct.create_unicode_buffer(length + 1)
        copied = self._message(edit, 0x000D, length + 1, ct.cast(buf, ct.c_void_p).value)
        text = buf.value
        if copied != length or utf16_length(text) != length or self._message(edit, 0x000E) != length:
            raise Conflict('NOTEPAD_BUFFER_CHANGED_DURING_READ')
        start, end = ct.c_uint32(), ct.c_uint32()
        self._message(edit, 0x00B0, ct.addressof(start), ct.addressof(end))
        info = self.Gui(); info.cbSize = ct.sizeof(info)
        if not self.gui(before['window_thread'], ct.byref(info)):
            raise Conflict('NOTEPAD_GUI_THREAD_STATE_UNAVAILABLE')
        executable = self.source.process_executable(before)
        exe_sha = stable_file_sha(executable)
        target_security, own_security = self._security(before['pid']), self._security(os.getpid())
        session = ct.c_uint32()
        fn = self.api._bind(self.api.kernel, 'ProcessIdToSessionId', ct.c_int32, [ct.c_uint32, ct.POINTER(ct.c_uint32)])
        if not fn(os.getpid(), ct.byref(session)):
            raise Conflict('OWN_OS_SESSION_UNAVAILABLE')
        after = self.source.inspect(root); child_after = self.api.window(edit)
        second = ct.create_unicode_buffer(length + 1)
        copied_after = self._message(edit, 0x000D, length + 1, ct.cast(second, ct.c_void_p).value)
        if (before['identity_sha256'] != after.get('identity_sha256') or before['bounds'] != after['bounds']
                or child != child_after or copied_after != length or text != second.value
                or self._message(edit, 0x000E) != length):
            raise Conflict('NOTEPAD_WINDOW_CHANGED_DURING_READ')
        # Caret/focus can change while OS/security/image bytes are inspected.
        # Take the final selection/thread state after those bounded reads.
        self._message(edit, 0x00B0, ct.addressof(start), ct.addressof(end))
        info = self.Gui(); info.cbSize = ct.sizeof(info)
        if not self.gui(before['window_thread'], ct.byref(info)):
            raise Conflict('NOTEPAD_FINAL_GUI_THREAD_STATE_UNAVAILABLE')
        return {**after, 'edit_window_id': edit, 'edit_class': child['class'], 'text': text,
            'text_sha256': text_sha(text), 'utf16_length': length, 'selection_start': start.value,
            'selection_end': end.value, 'focused_edit': int(info.hwndFocus or 0) == edit,
            'gui_idle': not info.hwndCapture and not info.hwndMenuOwner and not info.hwndMoveSize and not info.flags & ~1,
            'foreground_window_id': int(self.source.foreground() or 0),
            'modifiers_released': all(not self.key(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)),
            'executable_path': executable, 'executable_sha256': exe_sha, 'os': self._os(),
            'same_user': target_security[0] == own_security[0], 'same_integrity': target_security[1] == own_security[1],
            'same_session': session.value == before['os_session'], 'source': 'ACTUAL_WIN32_CLASSIC_NOTEPAD_READONLY'}
