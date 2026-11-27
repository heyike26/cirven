"""Owned-host lifetime and read scope for native collaboration.

This authority does not reserve desktop space, alter window geometry, inject
input, or start an agent loop. Its native backend loads only user32/kernel32;
the sole native mutation is a private property on a verified host-owned HWND.
Saved receipts are history, never authority to adopt a previous host session.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import ctypes as ct
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading
import uuid

from pydantic import Field, model_validator

from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import canonical, digest, now
from packages.domain.paths import resolve_data_path
from packages.domain.questions import RequestModel
from packages.domain.store import Conflict, InvalidInput

PRESENTATIONS = ('NATIVE_COMPANION', 'PROJECT_WORKBENCH')


def _adapter_digest(value):
    # The existing native adapter contract hashes JSON with ASCII escaping.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class NativeHostWindow(RequestModel):
    hwnd_decimal: str = Field(strict=True, pattern=r'^[1-9][0-9]{0,19}$')
    owner_pid: int = Field(strict=True, ge=1, le=2147483647)

    @model_validator(mode='after')
    def pointer(self):
        if int(self.hwnd_decimal) >= 1 << 64:
            raise ValueError('Native window exceeds the supported pointer range')
        return self


class NativeCollaborationBindInput(RequestModel):
    main_window: NativeHostWindow
    owned_windows: dict[str, NativeHostWindow] = Field(default_factory=dict, max_length=8)
    presentation: str = Field(default='NATIVE_COMPANION', pattern=r'^(NATIVE_COMPANION|PROJECT_WORKBENCH)$')

    @model_validator(mode='after')
    def owned(self):
        if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', key) or key == 'main'
               for key in self.owned_windows):
            raise ValueError('Bounded distinct host window role names are required')
        windows = [self.main_window, *self.owned_windows.values()]
        if (len({item.hwnd_decimal for item in windows}) != len(windows)
                or any(item.owner_pid != self.main_window.owner_pid for item in windows)):
            raise ValueError('Every distinct owned window must belong to the same host process')
        return self


class _FILETIME(ct.Structure):
    _fields_ = [('low', ct.c_uint32), ('high', ct.c_uint32)]

    @property
    def value(self):
        return self.low | (self.high << 32)


class Win32OwnedHostWindows:
    """Exact property-marked host windows; no GDI, AppBar or focus API."""
    def __init__(self, *, libraries=None):
        if libraries is None:
            if os.name != 'nt':
                raise OSError('WINDOWS_OWNED_HOST_NOT_AVAILABLE')
            libraries = {name: ct.WinDLL(name, use_last_error=True) for name in ('user32', 'kernel32')}
        self.user, self.kernel = libraries['user32'], libraries['kernel32']
        H, D, B = ct.c_void_p, ct.c_uint32, ct.c_int32
        for lib, name, result, args in (
            (self.user, 'IsWindow', B, [H]),
            (self.user, 'GetAncestor', H, [H, D]),
            (self.user, 'GetWindowThreadProcessId', D, [H, ct.POINTER(D)]),
            (self.user, 'GetClassNameW', ct.c_int32, [H, ct.c_wchar_p, ct.c_int32]),
            (self.user, 'SetPropW', B, [H, ct.c_wchar_p, H]),
            (self.user, 'GetPropW', H, [H, ct.c_wchar_p]),
            (self.user, 'RemovePropW', H, [H, ct.c_wchar_p]),
            (self.kernel, 'OpenProcess', H, [D, B, D]),
            (self.kernel, 'GetProcessTimes', B, [H, *([ct.POINTER(_FILETIME)] * 4)]),
            (self.kernel, 'GetExitCodeProcess', B, [H, ct.POINTER(D)]),
            (self.kernel, 'ProcessIdToSessionId', B, [D, ct.POINTER(D)]),
            (self.kernel, 'CloseHandle', B, [H]),
        ):
            function = getattr(lib, name)
            function.restype, function.argtypes = result, args

    @staticmethod
    def _require(value, code):
        if not value:
            raise Conflict(code)
        return value

    def process_identity(self, pid):
        if type(pid) is not int or not 0 < pid < 1 << 31:
            raise Conflict('EXACT_HOST_PID_REQUIRED')
        handle = self._require(self.kernel.OpenProcess(0x1000, False, pid), 'HOST_PROCESS_UNAVAILABLE')
        try:
            creation, exit_time, kernel_time, user_time = (_FILETIME() for _ in range(4))
            self._require(self.kernel.GetProcessTimes(handle, *(ct.byref(item) for item in
                (creation, exit_time, kernel_time, user_time))), 'HOST_PROCESS_LIFETIME_UNAVAILABLE')
            exit_code, session = ct.c_uint32(), ct.c_uint32()
            self._require(self.kernel.GetExitCodeProcess(handle, ct.byref(exit_code)), 'HOST_PROCESS_STATE_UNAVAILABLE')
            self._require(self.kernel.ProcessIdToSessionId(pid, ct.byref(session)), 'HOST_OS_SESSION_UNAVAILABLE')
            if exit_code.value != 259 or not creation.value or session.value == 0:
                raise Conflict('LIVE_INTERACTIVE_HOST_PROCESS_REQUIRED')
            return {'pid': pid, 'process_creation': creation.value, 'os_session': session.value}
        finally:
            self.kernel.CloseHandle(handle)

    def window_identity(self, hwnd):
        if type(hwnd) is not int or not 0 < hwnd < 1 << (ct.sizeof(ct.c_void_p) * 8):
            raise Conflict('EXACT_OWNED_HOST_WINDOW_REQUIRED')
        self._require(self.user.IsWindow(hwnd), 'OWNED_HOST_WINDOW_LOST')
        if int(self.user.GetAncestor(hwnd, 2) or 0) != hwnd:
            raise Conflict('OWNED_TOP_LEVEL_HOST_WINDOW_REQUIRED')
        pid = ct.c_uint32()
        thread = int(self._require(self.user.GetWindowThreadProcessId(hwnd, ct.byref(pid)), 'HOST_WINDOW_OWNER_UNAVAILABLE'))
        class_name = ct.create_unicode_buffer(257)
        self._require(self.user.GetClassNameW(hwnd, class_name, len(class_name)), 'HOST_WINDOW_CLASS_UNAVAILABLE')
        return {'window_id': hwnd, **self.process_identity(pid.value),
                'window_thread': thread, 'window_class': class_name.value}

    def mark_window(self, hwnd, owner):
        identity = self.window_identity(hwnd)
        if any(identity[key] != owner[key] for key in owner):
            raise Conflict('WINDOW_OUTSIDE_OWNED_HOST_PROCESS_LIFETIME')
        marker_name = 'EEAS-NativeCollaboration-' + uuid.uuid4().hex
        marker_value = (uuid.uuid4().int & ((1 << (ct.sizeof(ct.c_void_p) * 8 - 1)) - 1)) or 1
        self._require(self.user.SetPropW(hwnd, marker_name, marker_value), 'HOST_WINDOW_LIFETIME_MARK_FAILED')
        marker = {**identity, 'marker_name': marker_name, 'marker_value': marker_value}
        self.validate(marker)
        return marker

    def validate(self, marker):
        actual = self.window_identity(marker['window_id'])
        if (any(actual[key] != marker[key] for key in actual)
                or int(self.user.GetPropW(marker['window_id'], marker['marker_name']) or 0) != marker['marker_value']):
            raise Conflict('OWNED_HOST_WINDOW_LIFETIME_CHANGED')
        return actual

    def clear_marker(self, marker):
        self.validate(marker)
        if not marker['marker_name'].startswith('EEAS-NativeCollaboration-'):
            raise Conflict('OWNED_COLLABORATION_MARKER_REQUIRED')
        result = int(self.user.RemovePropW(marker['window_id'], marker['marker_name']) or 0)
        if result != marker['marker_value']:
            raise Conflict('HOST_WINDOW_MARKER_CLEANUP_UNKNOWN')


class NativeCollaborationAuthority:
    """Trusted Main scope compatible with readonly ws consumers.

    A native injection is a policy-test seam, never a qualification receipt.
    Native input stays disabled even when every owned-host check succeeds.
    """
    def __init__(self, data_dir, *, token=None, owner_pid=None, native=None, platform=None,
                 window_source=None):
        self.data_dir = Path(data_dir)
        self.platform = os.name if platform is None else platform
        if (not self.data_dir.is_absolute() or any(item.is_symlink() for item in (self.data_dir, *self.data_dir.parents))
                or (self.platform == 'nt' and self.data_dir.drive.upper() not in {'D:', 'E:'})):
            raise InvalidInput('Absolute private D or E runtime data root is required')
        self.token = token if token is not None else os.environ.get('EEAS_DESKTOP_HOST_TOKEN', '')
        raw_pid = owner_pid if owner_pid is not None else os.environ.get('EEAS_DESKTOP_HOST_PID', '0')
        try:
            self.owner_pid = int(raw_pid) if isinstance(raw_pid, str) and re.fullmatch(r'[0-9]+', raw_pid) else raw_pid
        except ValueError:
            self.owner_pid = 0
        self.enabled = (isinstance(self.token, str) and re.fullmatch(r'[0-9a-f]{64}', self.token) is not None
                        and type(self.owner_pid) is int and 0 < self.owner_pid < 1 << 31)
        self.lock = threading.RLock()
        self.generation = uuid.uuid4().hex
        self.current, self.targets, self.native_error, self.owner_identity = None, {}, None, None
        self.observation_bindings = {}
        self.native = native
        self.window_source = window_source
        if self.native is None and self.platform == 'nt':
            try:
                self.native = Win32OwnedHostWindows()
            except (OSError, AttributeError) as error:
                self.native_error = type(error).__name__
        if self.enabled and self.native is not None:
            try:
                self.owner_identity = self._owner(self.native.process_identity(self.owner_pid))
            except Exception as error:
                self.native_error = type(error).__name__

    def _owner(self, value):
        if (not isinstance(value, dict) or set(value) != {'pid', 'process_creation', 'os_session'}
                or value['pid'] != self.owner_pid or any(type(item) is not int or item <= 0 for item in value.values())):
            raise Conflict('EXACT_LIVE_HOST_PROCESS_IDENTITY_REQUIRED')
        return copy.deepcopy(value)

    def check_host(self, provided, *, require_native=True):
        if not self.enabled or not isinstance(provided, str) or not hmac.compare_digest(provided, self.token):
            raise Conflict('A trusted desktop main process is required')
        if not require_native and (self.native is None or self.owner_identity is None):
            return  # Sanitized availability only; no bind/read/input authority.
        if self.native is None or self.owner_identity is None:
            raise Conflict('Native owned-host lifetime is unavailable')
        if self._owner(self.native.process_identity(self.owner_pid)) != self.owner_identity:
            raise Conflict('Desktop host process lifetime changed')

    def _path(self, *parts):
        if any(not isinstance(item, str) for item in parts):
            raise InvalidInput('Private collaboration path parts must be strings')
        return resolve_data_path('/'.join(['native-collaboration', *parts]), self.data_dir)

    def _write(self, path, value):
        if not self.enabled:
            raise Conflict('Trusted collaboration record writer is unavailable')
        path = Path(path)
        relative = path.relative_to(self.data_dir / 'native-collaboration').as_posix()
        checked = self._path(relative)
        if path != checked:
            raise InvalidInput('Collaboration receipt escaped its private namespace')
        raw = canonical(value).encode()
        if len(raw) > 4 * 1024 * 1024:
            raise InvalidInput('Collaboration receipt exceeds its size bound')
        wrapper = {'payload': value, 'sha256': hashlib.sha256(raw).hexdigest(),
            'host_token_sha256': hashlib.sha256(self.token.encode()).hexdigest(),
            'host_mac': hmac.new(self.token.encode(), raw, hashlib.sha256).hexdigest()}
        path.parent.mkdir(parents=True, exist_ok=True)
        # Resolve again after mkdir; no symlink directory is a valid writer.
        self._path(relative)
        temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
        try:
            with temporary.open('xb') as stream:
                stream.write(canonical(wrapper).encode()); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _read(self, path, *, authority=False):
        path = Path(path)
        relative = path.relative_to(self.data_dir / 'native-collaboration').as_posix()
        if path != self._path(relative) or not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            raise Conflict('Collaboration receipt is unavailable or unsafe')
        try:
            wrapper = json.loads(path.read_bytes())
            raw = canonical(wrapper['payload']).encode()
            if wrapper['sha256'] != hashlib.sha256(raw).hexdigest():
                raise ValueError
            if authority and (wrapper['host_token_sha256'] != hashlib.sha256(self.token.encode()).hexdigest()
                    or not hmac.compare_digest(wrapper['host_mac'], hmac.new(self.token.encode(), raw, hashlib.sha256).hexdigest())):
                raise ValueError
            return copy.deepcopy(wrapper['payload'])
        except (ValueError, KeyError, TypeError):
            raise Conflict('Collaboration receipt integrity failed') from None

    def _save(self, kind):
        identifier = uuid.uuid4().hex
        record = {'generation': self.generation, 'owner_identity': self.owner_identity,
                  'state': self.current, 'kind': kind, 'created_at': now()}
        self._write(self._path('sessions', identifier + '.json'), record)
        self._write(self._path('head.json'), {'record': identifier, 'sha256': digest(record)})

    def _validate_current(self):
        self.check_host(self.token)
        if self.current is None or self.current['closed']:
            raise Conflict('Owned collaboration host is unbound or closed')
        for marker in self.current['markers'].values():
            self.native.validate(marker)

    def _public(self, status, *, reason=None):
        state = self.current or {}
        return {'status': status, 'type': 'NATIVE_COLLABORATION', 'fit_policy': 'PRESERVE_USER_DESKTOP',
            'session_ref': copy.deepcopy(state.get('session_ref')), 'surface_ref': copy.deepcopy(state.get('surface_ref')),
            'host_scope': copy.deepcopy(state.get('host_scope')), 'presentation': state.get('presentation', 'NATIVE_COMPANION'),
            'input': {'epoch': state.get('epoch', 0), 'enabled': False, 'input_root': 'OWNED_HOST_SESSION'},
            'runtime_role': 'READONLY_OBSERVATION', 'native_input_qualified': False,
            'automatic_input_authorized': False, 'verified_agent_dispatch_blocked': True,
            'region_verified': False, 'visible_all_verified': False, 'verified_input_blocked': False,
            'prepare_pending': False, 'desktop_geometry_mutated': False, 'appbar_reserved': False,
            'capabilities': {'native_desktop': False, 'current_context_metadata': status == 'ACTIVE',
                'desktop_input_routes_qualified': False, 'implementation': 'OWNED_HOST_LIFETIME_READ_SCOPE'},
            'qualification': 'OWNED_HOST_SCOPE_ONLY_NATIVE_CAPTURE_AND_INPUT_NOT_QUALIFIED', 'reason': reason}

    def get(self):
        with self.lock:
            if self.current is None:
                prior = self._path('head.json').exists()
                return self._public('NEEDS_REBIND' if prior else 'UNBOUND',
                    reason='PRIOR_HOST_AUTHORITY_NOT_ADOPTED' if prior else 'HOST_NOT_BOUND')
            if self.current['closed']:
                return self._public('CLOSED')
            try:
                self._validate_current()
                return self._public('ACTIVE')
            except Exception:
                return self._public('HOST_LOST', reason='OWNED_HOST_LIFETIME_UNAVAILABLE_OR_CHANGED')

    def bind(self, request):
        body = NativeCollaborationBindInput.model_validate(request).model_dump()
        with self.lock:
            self.check_host(self.token)
            if body['main_window']['owner_pid'] != self.owner_pid:
                raise Conflict('Binding is outside the trusted main process')
            if self.current is not None and not self.current['closed']:
                self._validate_current()
                if self.current['request'] == body:
                    return self.get()
                raise Conflict('Current host binding must be explicitly closed before replacing its windows')
            marked = {}
            try:
                for role, window in {'main': body['main_window'], **body['owned_windows']}.items():
                    marked[role] = self.native.mark_window(int(window['hwnd_decimal']), self.owner_identity)
                identifier = uuid.uuid4().hex
                session_ref = {'id': identifier, 'revision': 1,
                    'sha256': digest({'id': identifier, 'generation': self.generation, 'owner': self.owner_identity})}
                scope = {'session_ref': session_ref, 'generation': self.generation,
                         'owned_window_lifetimes_sha256': digest(marked)}
                surface_ref = {'id': uuid.uuid4().hex, 'revision': 1, 'sha256': digest(scope)}
                self.current = {'request': body, 'markers': marked, 'session_ref': session_ref,
                    'surface_ref': surface_ref, 'host_scope': scope, 'epoch': 1, 'closed': False,
                    'presentation': body['presentation'], 'sdk_session_ref': None}
                self.targets.clear()
                self.observation_bindings.clear()
                self._save('OWNED_HOST_BOUND_NO_DESKTOP_LAYOUT_MUTATION')
                return self.get()
            except Exception:
                self.current = None
                for marker in marked.values():
                    try:
                        self.native.clear_marker(marker)
                    except Exception:
                        pass  # Never remove a marker from a reused/user HWND.
                raise

    def check_current(self, surface_ref, input_epoch, *, host_scope=None):
        with self.lock:
            try:
                ref = RevisionRef.model_validate(surface_ref).model_dump()
                self._validate_current()
                return (type(input_epoch) is int and input_epoch == self.current['epoch']
                    and ref == self.current['surface_ref']
                    and (host_scope is None or host_scope == self.current['host_scope']))
            except Exception:
                return False

    def own_geometry(self):
        """Bound Main metadata only; never accepts a renderer-selected HWND."""
        with self.lock:
            self._validate_current()
            if not hasattr(self, '_own_geometry_reader'):
                from packages.adapters.owned_window_geometry import Win32OwnedWindowGeometry
                self._own_geometry_reader = Win32OwnedWindowGeometry()
            marker = copy.deepcopy(self.current['markers']['main'])
            value = self._own_geometry_reader.read(marker, self.native.validate)
            self._validate_current()
            return {'status': 'OWN_MAIN_GEOMETRY', **value, 'geometry_sha256': digest(value),
                    'surface_ref': copy.deepcopy(self.current['surface_ref']),
                    'host_scope': copy.deepcopy(self.current['host_scope']), 'input_epoch': self.current['epoch']}

    def own_role_identity(self, role):
        """Private host consumer identity; never accepts or adopts a supplied HWND."""
        with self.lock:
            self.check_host(self.token)
            if (not isinstance(role, str) or self.current is None or self.current['closed']
                    or role not in self.current['markers']):
                raise Conflict('OWNED_HOST_ROLE_UNAVAILABLE')
            marker = self.current['markers'][role]
            self.native.validate(marker)
            return {'host_generation': self.generation, 'role': role,
                    'session_ref': copy.deepcopy(self.current['session_ref']),
                    'marker': copy.deepcopy(marker)}

    def validate_own_role_identity(self, identity):
        """Recheck the exact live binding, process birth and private window property."""
        with self.lock:
            self.check_host(self.token)
            if (not isinstance(identity, dict) or self.current is None or self.current['closed']
                    or set(identity) != {'host_generation', 'role', 'session_ref', 'marker'}
                    or identity['host_generation'] != self.generation
                    or identity['session_ref'] != self.current['session_ref']
                    or identity['marker'] != self.current['markers'].get(identity['role'])):
                raise Conflict('OWNED_HOST_ROLE_LIFETIME_CHANGED')
            self.native.validate(identity['marker'])
            return True

    @contextmanager
    def observation_guard(self, surface_ref, input_epoch):
        with self.lock:
            if not self.check_current(surface_ref, input_epoch):
                raise Conflict('Current owned-host read scope changed')
            yield self.get()
            if not self.check_current(surface_ref, input_epoch):
                raise Conflict('Owned-host lifetime changed during observation')

    @contextmanager
    def dispatch_guard(self, *_args, **_kwargs):
        raise Conflict('Native input is not qualified for this collaboration host')
        yield  # A contextmanager must fail before admitting any action.

    def excluded_pids(self):
        with self.lock:
            self._validate_current()
            return [self.owner_pid, os.getpid()]

    def set_window_source(self, source):
        if not callable(getattr(source, 'inspect', None)):
            raise InvalidInput('Actual current-window inspector is required')
        with self.lock:
            if self.window_source is not None and self.window_source is not source:
                raise Conflict('Current window lifetime source cannot be replaced within this host')
            self.window_source = source

    def set_presentation(self, presentation):
        if presentation not in PRESENTATIONS:
            raise InvalidInput('Unknown native collaboration presentation')
        with self.lock:
            self._validate_current()
            self.current['presentation'] = presentation
            self._save('PRESENTATION_RECORDED_NO_NATIVE_LAYOUT_OR_PERMISSION_CHANGE')
            return self.get()

    def binding_reader(self):
        with self.lock:
            self._validate_current()
            return {'workspace_session_ref': copy.deepcopy(self.current['session_ref']),
                'surface_ref': copy.deepcopy(self.current['surface_ref']), 'input_epoch': self.current['epoch'],
                'sdk_session_ref': copy.deepcopy(self.current['sdk_session_ref'])}

    def observation_session_binding(self, *, session_label, budget, readonly_policy_ref=None):
        # CuaDesktopInput remains the authoritative validator for this DTO.
        with self.lock:
            self._validate_current()
            identifier = uuid.uuid4().hex
            run_ref = {'id': identifier, 'revision': 1,
                'sha256': digest({'id': identifier, 'host_scope': self.current['host_scope']})}
            binding = {'runtime_role': 'READONLY_OBSERVATION', 'execution_ref': None,
                'workspace_session_ref': copy.deepcopy(self.current['session_ref']),
                'surface_ref': copy.deepcopy(self.current['surface_ref']), 'observation_run_ref': run_ref,
                'session_label': session_label, 'budget': copy.deepcopy(budget)}
            if readonly_policy_ref is not None:
                binding['readonly_policy_ref'] = copy.deepcopy(readonly_policy_ref)
            from packages.adapters.cua_desktop_input import CuaDesktopInput
            binding = CuaDesktopInput._binding(binding)
            if len(self.observation_bindings) >= 16:
                raise Conflict('Too many pending SDK observation session bindings')
            self.observation_bindings[identifier] = copy.deepcopy(binding)
            return binding

    def set_observation_session(self, receipt):
        with self.lock:
            self._validate_current()
            if (not isinstance(receipt, dict) or receipt.get('runtime_role') != 'READONLY_OBSERVATION'
                    or receipt.get('workspace_session_ref') != self.current['session_ref']
                    or receipt.get('surface_ref') != self.current['surface_ref']):
                raise Conflict('SDK observation session differs from this owned host')
            reference = RevisionRef.model_validate(receipt['session_ref']).model_dump()
            run_ref = RevisionRef.model_validate(receipt.get('observation_run_ref')).model_dump()
            binding = self.observation_bindings.get(run_ref['id'])
            if (not binding or binding['observation_run_ref'] != run_ref
                    or receipt.get('binding_sha256') != _adapter_digest(binding)
                    or reference['sha256'] != _adapter_digest({'id': reference['id'], 'binding': binding})):
                raise Conflict('SDK session was not opened for this exact host-issued observation run')
            if self.current['sdk_session_ref'] is not None and self.current['sdk_session_ref'] != reference:
                raise Conflict('Current SDK read session must be explicitly cleared before replacement')
            self.current['sdk_session_ref'] = reference
            self._save('READONLY_SDK_SESSION_BOUND')
            self.observation_bindings.pop(run_ref['id'], None)
            return self.binding_reader()

    def clear_observation_session(self, expected_ref, close_receipt):
        with self.lock:
            self._validate_current()
            reference = RevisionRef.model_validate(expected_ref).model_dump()
            if (reference != self.current['sdk_session_ref'] or not isinstance(close_receipt, dict)
                    or close_receipt.get('session_ref') != reference or close_receipt.get('status') != 'CLOSED'):
                raise Conflict('Exact readonly session close receipt is required before renewal')
            self.current['sdk_session_ref'] = None
            self.targets.clear()
            self._save('READONLY_SDK_SESSION_CLOSED_BEFORE_RENEWAL')
            return self.binding_reader()

    def context_authority(self, window, binding):
        with self.lock:
            if binding != self.binding_reader():
                raise Conflict('Current context differs from its owned-host session')
            identity_keys = ('window_id', 'pid', 'process_creation', 'os_session', 'window_class', 'window_thread')
            if (not isinstance(window, dict) or window.get('status') != 'WINDOW_OBSERVED'
                    or window.get('pid') in self.excluded_pids() or window.get('os_session') != self.owner_identity['os_session']
                    or any(type(window.get(key)) is not int or window[key] <= 0
                           for key in identity_keys if key != 'window_class')
                    or not isinstance(window.get('window_class'), str) or not window['window_class']
                    or window.get('identity_sha256') != _adapter_digest({key: window[key] for key in identity_keys})):
                return {'eligible': False, 'ambiguous': False, 'pixel_source': 'UNKNOWN'}
            identity = window['identity_sha256']
            target_ref = {'id': identity, 'revision': 1,
                'sha256': digest({'window_lifetime': identity, 'host_scope': self.current['host_scope']})}
            self.targets[identity] = {'ref': target_ref, 'window': copy.deepcopy(window), 'binding': copy.deepcopy(binding)}
            while len(self.targets) > 128:
                self.targets.pop(next(iter(self.targets)))
            return {'eligible': True, 'ambiguous': False, 'target_ref': target_ref,
                    'pixel_source': 'UNKNOWN', 'authority': 'ACTUAL_FOREGROUND_WINDOW_METADATA_ONLY'}

    def target_authority(self, reference, surface_ref, input_epoch):
        with self.observation_guard(surface_ref, input_epoch):
            ref = RevisionRef.model_validate(reference).model_dump()
            record = self.targets.get(ref['id'])
            if not record or record['ref'] != ref or record['binding'] != self.binding_reader():
                raise Conflict('Target is outside this current readonly host scope')
            window = record['window']
            if not callable(getattr(self.window_source, 'inspect', None)):
                raise Conflict('Actual current-window lifetime inspector is not configured')
            if self.window_source.inspect(window['window_id']) != window:
                raise Conflict('Observed external window lifetime or metadata changed')
            return {'kind': 'WINDOW', 'pid': window['pid'], 'window_id': window['window_id'],
                'process_creation': window['process_creation'], 'identity_sha256': window['identity_sha256'],
                'self_target': False, 'bounds': copy.deepcopy(window['bounds'])}

    def close(self):
        with self.lock:
            if self.current is None or self.current['closed']:
                return self._public('CLOSED')
            self.current['closed'] = True
            self.current['epoch'] += 1
            self.current['sdk_session_ref'] = None
            self.targets.clear()
            self.observation_bindings.clear()
            cleanup = []
            for role, marker in self.current['markers'].items():
                try:
                    self.native.clear_marker(marker)
                    cleanup.append({'role': role, 'status': 'REMOVED_OWN_MARKER'})
                except Exception as error:
                    cleanup.append({'role': role, 'status': 'NOT_REMOVED_LIFETIME_CHANGED_OR_UNKNOWN',
                                    'error_type': type(error).__name__})
            self.current['cleanup'] = cleanup
            self._save('OWNED_HOST_CLOSED_AUTHORITY_REVOKED')
            return {**self._public('CLOSED'), 'cleanup': copy.deepcopy(cleanup),
                    'user_windows_modified': False, 'processes_terminated': False}
