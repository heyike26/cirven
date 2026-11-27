"""Fresh readonly page context from an actual recent non-EEAS window.

No driver/session/model is created. The host owns all scope/target references.
WinEvent registration is explicit and optional; it creates no GUI window.
"""
from __future__ import annotations
import ctypes as ct
import json
import threading
import time
import uuid

from packages.adapters.cua_desktop_input import _copy, _integer, _json_result, NativeCuaSessionFactory
from packages.adapters.windows_desktop_region import surface_reference, _sha
from packages.adapters.windows_native_window_host import Win32WindowApi, HANDLE, DWORD, LONG, BOOL, RECT
from packages.domain.local_workspaces import digest


class CurrentContextError(ValueError):
    pass


class _MSG(ct.Structure):
    _fields_ = [('hwnd', HANDLE), ('message', DWORD), ('wParam', ct.c_size_t),
               ('lParam', ct.c_ssize_t), ('time', DWORD), ('point', LONG * 2), ('private', DWORD)]


class _LASTINPUTINFO(ct.Structure):
    _fields_ = [('cbSize', DWORD), ('dwTime', DWORD)]


class _CURSORPOINT(ct.Structure):
    _fields_ = [('x', LONG), ('y', LONG)]


class Win32CurrentWindowSource:
    """Readonly stock Win32; excluded owner PIDs come from trusted main."""
    def __init__(self, *, excluded_pid_reader, api=None):
        if not callable(excluded_pid_reader):
            raise CurrentContextError('TRUSTED_EEAS_OWNER_EXCLUSION_REQUIRED')
        self.api = Win32WindowApi() if api is None else api
        self.excluded_pid_reader = excluded_pid_reader
        bind = self.api._bind
        self.foreground = bind(self.api.user, 'GetForegroundWindow', HANDLE, [])
        self.ancestor = bind(self.api.user, 'GetAncestor', HANDLE, [HANDLE, DWORD])
        self.title = bind(self.api.user, 'GetWindowTextW', ct.c_int32, [HANDLE, ct.c_wchar_p, ct.c_int32])
        self.thread_dpi = bind(self.api.user, 'SetThreadDpiAwarenessContext', HANDLE, [HANDLE])
        self.callback_type = getattr(ct, 'WINFUNCTYPE', ct.CFUNCTYPE)(None, HANDLE, DWORD, HANDLE, LONG, LONG, DWORD, DWORD)
        self.set_hook = bind(self.api.user, 'SetWinEventHook', HANDLE,
            [DWORD, DWORD, HANDLE, self.callback_type, DWORD, DWORD, DWORD])
        self.unhook = bind(self.api.user, 'UnhookWinEvent', BOOL, [HANDLE])
        self.peek = bind(self.api.user, 'PeekMessageW', BOOL, [ct.POINTER(_MSG), HANDLE, DWORD, DWORD, DWORD])
        self.get_message = bind(self.api.user, 'GetMessageW', ct.c_int32, [ct.POINTER(_MSG), HANDLE, DWORD, DWORD])
        self.post_thread = bind(self.api.user, 'PostThreadMessageW', BOOL, [DWORD, DWORD, ct.c_size_t, ct.c_ssize_t])
        self.current_thread = bind(self.api.kernel, 'GetCurrentThreadId', DWORD, [])
        self.hook_thread = None
        self.hook_callback = self.hook_handle = self.hook_thread_id = None
        self.hook_fault = None

    def excluded_pids(self):
        values = self.excluded_pid_reader()
        if (not isinstance(values, (list, tuple, set)) or not values
                or any(type(v) is not int or v <= 0 for v in values)):
            raise CurrentContextError('CURRENT_TRUSTED_EEAS_PID_SET_REQUIRED')
        return set(values)

    def inspect(self, window_id):
        _integer(window_id, 1, (1 << 64) - 1, 'EXACT_NATIVE_WINDOW_REQUIRED')
        previous = self.thread_dpi(-4)  # Own thread PMv2; no process/system DPI mutation.
        if not previous:
            raise CurrentContextError('PHYSICAL_WINDOW_METADATA_DPI_CONTEXT_UNAVAILABLE')
        try:
            return self._inspect(window_id)
        finally:
            if not self.thread_dpi(previous):
                raise CurrentContextError('READONLY_THREAD_DPI_RESTORE_UNKNOWN')

    def _inspect(self, window_id):
        root = int(self.ancestor(window_id, 2) or 0)  # GA_ROOT, never Progman adoption.
        if not root:
            raise CurrentContextError('NATIVE_WINDOW_LOST')
        window = self.api.window(root)
        if window['pid'] in self.excluded_pids():
            return {'status': 'EXCLUDED_EEAS_WINDOW'}
        handle = self.api.kernel.OpenProcess(0x1000, False, window['pid'])  # QUERY_LIMITED_INFORMATION only.
        if not handle:
            raise CurrentContextError('WINDOW_PROCESS_IDENTITY_UNAVAILABLE')
        try:
            process = self.api.process(handle)
        finally:
            self.api.kernel.CloseHandle(handle)
        if process['exit_code'] != 259 or process['pid'] != window['pid']:
            raise CurrentContextError('WINDOW_PROCESS_LIFETIME_CHANGED')
        title = ct.create_unicode_buffer(2049)
        self.title(root, title, len(title))  # Empty title is permitted, never page content.
        bounds = RECT()
        self.api.require(self.api.user.GetWindowRect(root, ct.byref(bounds)), 'WINDOW_BOUNDS_UNAVAILABLE')
        identity = {'window_id': root, 'pid': process['pid'], 'process_creation': process['creation'],
                    'os_session': process['session'], 'window_class': window['class'], 'window_thread': window['thread']}
        return {'status': 'WINDOW_OBSERVED', **identity, 'identity_sha256': _sha(identity),
            'title': title.value, 'visible': window['visible'], 'minimized': window['minimized'],
            'dpi': window['dpi'], 'bounds': {'x': bounds.left, 'y': bounds.top,
                'width': bounds.right - bounds.left, 'height': bounds.bottom - bounds.top},
            'coordinate_space': 'PHYSICAL_PIXELS_OWN_THREAD_PMV2',
            'source': 'ACTUAL_WIN32_WINDOW_METADATA', 'page_content_observed': False}

    def sample(self):
        hwnd = int(self.foreground() or 0)
        return {'status': 'NO_FOREGROUND_WINDOW'} if not hwnd else self.inspect(hwnd)

    def _settle_activation_trigger(self, read_input, first, hwnd, *, clock=time.monotonic, sleep=time.sleep):
        """Finish only the invoking Ctrl+Shift+Space gesture, at most one second.

        This bounded readonly wait is used only before capture, never by the
        final focus check. Tick changes without a key release remain changes.
        Win32 snapshots are not an input event log or input authorization.
        """
        key_state = self.api._bind(self.api.user, 'GetAsyncKeyState', ct.c_int16, [ct.c_int32])
        cursor = self.api._bind(self.api.user, 'GetCursorPos', BOOL, [ct.POINTER(_CURSORPOINT)])
        keys = (0x11, 0x10, 0x20)
        def pressed():
            # Only the high bit is reliable; never consume the low history bit.
            return {key for key in keys if key_state(key) & 0x8000}
        def position():
            point = _CURSORPOINT()
            self.api.require(cursor(ct.byref(point)), 'CURSOR_STATE_UNAVAILABLE')
            return point.x, point.y
        previous = pressed()
        origin = position()
        started = clock()
        for _ in range(251):
            remaining = pressed()
            current = _LASTINPUTINFO(ct.sizeof(_LASTINPUTINFO), 0)
            self.api.require(read_input(ct.byref(current)), 'INPUT_STATE_UNAVAILABLE')
            elapsed = clock() - started
            if (not 0 <= elapsed <= 1 or int(self.foreground() or 0) != hwnd
                    or position() != origin or not remaining <= previous
                    or any(key_state(key) & 0x8000 for key in (0x01, 0x02, 0x04, 0x05, 0x06))
                    or current.dwTime != first.dwTime and remaining == previous):
                raise CurrentContextError('FOREGROUND_OR_INPUT_CHANGED_DURING_TRIGGER')
            first.dwTime = current.dwTime
            if not remaining:
                return
            previous = remaining
            sleep(0.004)
        raise CurrentContextError('FOCUS_TRIGGER_STILL_HELD')

    def activation_state(self, *, settle_trigger=False):
        """Transient own-window focus guard, never a page/source observation.

        Unlike inspect(), it must recognize our own foreground windows too.
        No title, pixels, window handle or process identity leaves this method.
        """
        read_input = self.api._bind(self.api.user, 'GetLastInputInfo', BOOL, [ct.POINTER(_LASTINPUTINFO)])
        first, last = _LASTINPUTINFO(), _LASTINPUTINFO()
        first.cbSize = last.cbSize = ct.sizeof(_LASTINPUTINFO)
        self.api.require(read_input(ct.byref(first)), 'INPUT_STATE_UNAVAILABLE')
        hwnd = int(self.foreground() or 0)
        root = int(self.ancestor(hwnd, 2) or 0) if hwnd else 0
        if not root:
            raise CurrentContextError('FOREGROUND_STATE_UNAVAILABLE')
        window = self.api.window(root)
        handle = self.api.kernel.OpenProcess(0x1000, False, window['pid'])
        if not handle:
            raise CurrentContextError('FOREGROUND_PROCESS_UNAVAILABLE')
        try:
            if settle_trigger:
                self._settle_activation_trigger(read_input, first, hwnd)
            process = self.api.process(handle)
            after = self.api.window(root)
            self.api.require(read_input(ct.byref(last)), 'INPUT_STATE_UNAVAILABLE')
            if (int(self.foreground() or 0) != hwnd or first.dwTime != last.dwTime
                    or any(after[key] != window[key] for key in ('pid', 'class', 'thread'))
                    or process['pid'] != window['pid'] or process['exit_code'] != 259):
                raise CurrentContextError('FOREGROUND_OR_INPUT_CHANGED_DURING_SAMPLE')
        finally:
            self.api.kernel.CloseHandle(handle)
        identity = {'window_id': root, 'pid': process['pid'], 'process_creation': process['creation'],
                    'os_session': process['session'], 'window_class': window['class'], 'window_thread': window['thread']}
        return {'status': 'OBSERVED', 'foreground_identity_sha256': _sha(identity), 'last_input_tick': int(last.dwTime)}

    def process_executable(self, window):
        """Read an actual process image only while the supplied window stays current.

        Title/class strings are not executable resources. This method queries
        the limited-information process handle and checks the process and HWND
        identities on both sides; it neither activates nor changes a window.
        """
        from pathlib import PureWindowsPath
        import re
        if (not isinstance(window, dict) or window.get('status') != 'WINDOW_OBSERVED'
                or type(window.get('window_id')) is not int
                or not isinstance(window.get('identity_sha256'), str)
                or not re.fullmatch('[a-f0-9]{64}', window['identity_sha256'])):
            raise CurrentContextError('ACTUAL_WINDOW_IDENTITY_REQUIRED_FOR_EXECUTABLE')
        window_id, identity = window['window_id'], window['identity_sha256']
        before = self.inspect(window_id)
        if before.get('status') != 'WINDOW_OBSERVED' or before.get('identity_sha256') != identity:
            raise CurrentContextError('WINDOW_IDENTITY_CHANGED_BEFORE_EXECUTABLE_QUERY')
        handle = self.api.kernel.OpenProcess(0x1000, False, before['pid'])
        if not handle:
            raise CurrentContextError('WINDOW_PROCESS_EXECUTABLE_UNAVAILABLE')
        try:
            query = self.api._bind(self.api.kernel, 'QueryFullProcessImageNameW', BOOL,
                [HANDLE, DWORD, ct.c_wchar_p, ct.POINTER(DWORD)])
            def check_process():
                actual = self.api.process(handle)
                if (actual.get('exit_code') != 259 or actual.get('pid') != before['pid']
                        or actual.get('creation') != before['process_creation']
                        or actual.get('session') != before['os_session']):
                    raise CurrentContextError('WINDOW_PROCESS_LIFETIME_CHANGED_DURING_EXECUTABLE_QUERY')
            check_process()
            buffer = ct.create_unicode_buffer(32768)
            count = DWORD(len(buffer))
            if not query(handle, 0, buffer, ct.byref(count)):
                raise CurrentContextError('WINDOW_PROCESS_EXECUTABLE_QUERY_FAILED')
            executable = buffer.value
            try:
                utf16_units = len(executable.encode('utf-16-le')) // 2
            except UnicodeEncodeError:
                raise CurrentContextError('ACTUAL_PROCESS_EXECUTABLE_INVALID_UNICODE') from None
            if (not 0 < count.value < len(buffer) or count.value != utf16_units
                    or not PureWindowsPath(executable).is_absolute()
                    or any(ord(c) < 32 for c in executable)
                    or any(c in executable.removeprefix('\\\\?\\') for c in '*?')):
                raise CurrentContextError('ABSOLUTE_ACTUAL_PROCESS_EXECUTABLE_REQUIRED')
            check_process()
        finally:
            if not self.api.kernel.CloseHandle(handle):
                raise CurrentContextError('EXECUTABLE_QUERY_OWN_HANDLE_CLOSE_UNKNOWN')
        after = self.inspect(window_id)
        if after.get('status') != 'WINDOW_OBSERVED' or after.get('identity_sha256') != identity:
            raise CurrentContextError('WINDOW_IDENTITY_CHANGED_AFTER_EXECUTABLE_QUERY')
        return executable

    def start(self, callback):
        """Host-only explicit registration; no SDK work in native callbacks."""
        if not callable(callback):
            raise CurrentContextError('PRIVATE_HOST_FOREGROUND_OBSERVER_REQUIRED')
        if self.hook_thread is not None:
            raise CurrentContextError('FOREGROUND_OBSERVER_ALREADY_STARTED')
        ready = threading.Event()
        def run():
            self.hook_thread_id = int(self.current_thread())
            message = _MSG()
            self.peek(ct.byref(message), None, 0, 0, 0)  # Own message queue, no HWND.
            def event(hook, kind, hwnd, object_id, child_id, thread_id, event_time):
                if kind != 3 or not hwnd:  # EVENT_SYSTEM_FOREGROUND only.
                    return
                try:
                    record = self.inspect(int(hwnd))
                    callback(record, {'source': 'ACTUAL_WINEVENT_FOREGROUND', 'os_event_time': int(event_time)})
                except Exception as error:
                    self.hook_fault = type(error).__name__
            self.hook_callback = self.callback_type(event)  # Retain strong reference.
            self.hook_handle = self.set_hook(3, 3, None, self.hook_callback, 0, 0, 0)
            if not self.hook_handle:
                self.hook_fault = 'SET_WINEVENT_HOOK_FAILED'; ready.set(); return
            ready.set()
            try:
                while True:
                    result = self.get_message(ct.byref(message), None, 0, 0)
                    if result <= 0:
                        if result < 0: self.hook_fault = 'FOREGROUND_MESSAGE_LOOP_FAILED'
                        break
            finally:
                if not self.unhook(self.hook_handle):
                    self.hook_fault = 'UNHOOK_OUTCOME_UNKNOWN'
                else:
                    self.hook_handle = None
        self.hook_thread = threading.Thread(target=run, name='EEAS-readonly-foreground-events', daemon=True)
        self.hook_thread.start()
        if not ready.wait(5) or self.hook_fault:
            raise CurrentContextError(self.hook_fault or 'FOREGROUND_OBSERVER_START_UNKNOWN')
        return {'status': 'TRACKING', 'native_input_enabled': False}

    def close(self):
        if self.hook_thread is None:
            return {'status': 'CLOSED'}
        if self.hook_thread.is_alive():
            if not self.post_thread(self.hook_thread_id, 0x0012, 0, 0):  # WM_QUIT own thread only.
                return {'status': 'FOREGROUND_OBSERVER_CLOSE_UNKNOWN'}
            self.hook_thread.join(5)
        return {'status': 'CLOSED' if not self.hook_thread.is_alive() and not self.hook_handle else 'FOREGROUND_OBSERVER_CLOSE_UNKNOWN'}


class CuaReadonlyPageObservation:
    """Companion to sealed Cua adapter: same private session, worker and guard."""
    def __init__(self, cua_adapter):
        self.cua = cua_adapter

    def accessibility(self, session_ref, target_ref, surface_ref, input_epoch):
        session_ref, target_ref, surface_ref = map(surface_reference, [session_ref, target_ref, surface_ref])
        async def run():
            record = self.cua._record(session_ref)
            if record['runtime_role'] != 'READONLY_OBSERVATION':
                raise CurrentContextError('DEDICATED_READONLY_OBSERVATION_SESSION_REQUIRED')
            if record['binding']['surface_ref'] != surface_ref:
                raise CurrentContextError('ACCESSIBILITY_DIFFERS_FROM_FROZEN_SURFACE')
            with self.cua.observation_guard(surface_ref, input_epoch):
                target = self.cua._target(target_ref, surface_ref, input_epoch)
                if target['kind'] != 'WINDOW':
                    raise CurrentContextError('ACCESSIBILITY_EXACT_WINDOW_REQUIRED')
                args = {'pid': target['pid'], 'window_id': target['window_id'], 'include_screenshot': False,
                        'include_accessibility_tree': True, 'full_output': True,
                        'timeout_ms': record['binding']['budget']['timeout_ms']}
                result = await self.cua._bounded(record, record['native'].call_tool('get_window_state', json.dumps(args)))
                structured = _json_result(result)
                metadata = structured.get('structured_json')
                if (structured.get('is_error') is not False or structured.get('images')
                        or not isinstance(metadata, str) or len(metadata.encode()) > 2 * 1024 * 1024):
                    raise CurrentContextError('ORIGINAL_SDK_ACCESSIBILITY_REFUSED_OR_UNBOUNDED')
                data = json.loads(metadata)
                if (not isinstance(data, dict) or data.get('pid') != target['pid']
                        or data.get('window_id') != target['window_id']
                        or self.cua._target(target_ref, surface_ref, input_epoch) != target):
                    raise CurrentContextError('ORIGINAL_SDK_ACCESSIBILITY_TARGET_CHANGED')
                identifier = uuid.uuid4().hex
                artifact = self.cua._write(identifier + '/uia.json', metadata.encode())
                return {'observation_ref': {'id': identifier, 'revision': 1, 'sha256': _sha(data)},
                    'raw_artifact_ref': artifact, 'observed_at': self.cua.clock(),
                    'metadata': {k: v for k, v in data.items() if k not in {'pid', 'window_id'}},
                    'status': 'OBSERVATION_NOT_ENGINEERING_FACT',
                    'source': 'ORIGINAL_CUA_DRIVER_0_35_2' if isinstance(self.cua.factory, NativeCuaSessionFactory) else 'INJECTED_TEST_PROVIDER',
                    'coverage': {'accessibility': bool(data.get('tree_markdown') or data.get('elements')),
                        'elements_complete': data.get('elements_complete') is True,
                        'degraded': structured.get('degraded') is True or data.get('degraded') is True,
                        'truncated': data.get('truncated') is True},
                    'visual_captured': False}
        return self.cua.worker.call(run)


class DesktopCurrentContext:
    def __init__(self, *, window_source, context_authority, cua_adapter, binding_reader,
                 observation_guard, accessibility_reader=None, ocr_reader=None, clock=None, before_read=None,
                 visual_capture=None, document_identity_reader=None):
        if not all(callable(x) for x in [context_authority, binding_reader, observation_guard]):
            raise CurrentContextError('PRIVATE_CURRENT_SCOPE_AND_WINDOW_AUTHORITY_REQUIRED')
        if not callable(getattr(window_source, 'sample', None)) or not callable(getattr(window_source, 'inspect', None)):
            raise CurrentContextError('ACTUAL_FOREGROUND_SOURCE_REQUIRED')
        self.source, self.authority, self.cua = window_source, context_authority, cua_adapter
        self.binding_reader, self.observation_guard = binding_reader, observation_guard
        self.accessibility = CuaReadonlyPageObservation(cua_adapter).accessibility if accessibility_reader is None else accessibility_reader
        self.ocr = ocr_reader
        self.before_read = before_read
        self.visual_capture = visual_capture
        self.document_identity_reader = document_identity_reader
        self.clock = time.time if clock is None else clock
        self.lock = threading.RLock()
        self.recent = None
        self.recent_issue = None
        self.contexts = {}
        self.generation = uuid.uuid4().hex
        self.revision = 0
        self.closed = False
        self.last_event_time = None

    def activation_state(self, *, settle_trigger=False):
        if self.closed:
            raise CurrentContextError('CURRENT_CONTEXT_CLOSED')
        read = getattr(self.source, 'activation_state', None)
        if not callable(read):
            raise CurrentContextError('NATIVE_INPUT_STATE_UNAVAILABLE')
        return read(settle_trigger=True) if settle_trigger else read()

    def _binding(self):
        if self.closed: raise CurrentContextError('CURRENT_CONTEXT_CLOSED')
        value = _copy(self.binding_reader(), limit=65536)
        keys = {'workspace_session_ref', 'surface_ref', 'input_epoch', 'sdk_session_ref'}
        if not isinstance(value, dict) or set(value) != keys:
            raise CurrentContextError('CURRENT_WORKSPACE_AND_SDK_SCOPE_UNAVAILABLE')
        for key in keys - {'input_epoch'}:
            if key != 'sdk_session_ref' or value[key] is not None:
                value[key] = surface_reference(value[key])
        _integer(value['input_epoch'], 0, 1 << 63, 'CURRENT_INPUT_EPOCH_REQUIRED')
        return value

    def _record(self, record, event=None):
        binding = self._binding()
        with self.observation_guard(binding['surface_ref'], binding['input_epoch']):
            if self._binding() != binding: raise CurrentContextError('CURRENT_SCOPE_CHANGED')
            record = _copy(record, limit=65536)
            if record.get('status') != 'WINDOW_OBSERVED':
                return {'status': 'IGNORED_HOST_OR_UNAVAILABLE_WINDOW', 'recent_preserved': True}
            # OS event timestamp is UINT32 and wraps; reject late delivery using
            # modular ordering, never browser timestamps as selection authority.
            if event and event.get('os_event_time') is not None:
                event_time = _integer(event['os_event_time'], 0, (1 << 32) - 1, 'INVALID_NATIVE_EVENT_TIME')
                with self.lock:
                    if self.last_event_time is not None and ((event_time - self.last_event_time) & 0xffffffff) >= 0x80000000:
                        return {'status': 'IGNORED_LATE_FOREGROUND_EVENT'}
                    self.last_event_time = event_time
            attested = self.authority(record, binding)
            if not isinstance(attested, dict) or attested.get('eligible') is not True:
                with self.lock:
                    self.recent = None
                    self.recent_issue = 'FOREGROUND_WINDOW_OUTSIDE_VERIFIED_SCOPE'
                return {'status': 'IGNORED_OUTSIDE_CURRENT_WORKSPACE', 'recent_preserved': False}
            with self.lock:
                self.revision += 1
                self.recent = {'window': record, 'workspace_session_ref': binding['workspace_session_ref'],
                    'used_at': self.clock(), 'revision': self.revision,
                    'selection_source': event.get('source') if event else 'ACTUAL_FOREGROUND_SAMPLE'}
                self.recent_issue = None
            return {'status': 'RECENT_WORKSPACE_WINDOW_RECORDED', 'revision': self.revision}

    def observe_foreground(self):
        return self._record(self.source.sample())

    def start_tracking(self):
        # Explicit trusted-host lifecycle. No capture or SDK call from callback.
        return self.source.start(self._record)

    @staticmethod
    def _missing(code, question, *, alternatives=None):
        return {'status': 'NEEDS_CLARIFICATION', 'reason': code,
            'questions': [{'code': code, 'text': question}], 'coverage': {'whole_page_seen': False},
            'alternatives': [] if alternatives is None else alternatives,
            'automatic_input_authorized': False}

    def get_context(self, *, intent, task_ref=None, need=None, expected_workspace_session_ref=None):
        """Fresh host-private context. No mutable PID/HWND or file path input."""
        if not isinstance(intent, str) or not 1 <= len(intent) <= 8192:
            raise CurrentContextError('BOUNDED_ACTUAL_USER_INTENT_REQUIRED')
        if task_ref is not None: task_ref = surface_reference(task_ref)
        requested = {'metadata': True, 'accessibility': True, 'visual': False, 'ocr': False} if need is None else _copy(need)
        if (not isinstance(requested, dict) or set(requested) != {'metadata', 'accessibility', 'visual', 'ocr'}
                or any(type(v) is not bool for v in requested.values()) or not any(requested.values())):
            raise CurrentContextError('EXPLICIT_ADAPTIVE_OBSERVATION_NEED_REQUIRED')
        binding = self._binding()
        if (expected_workspace_session_ref is not None
                and surface_reference(expected_workspace_session_ref) != binding['workspace_session_ref']):
            return self._missing('WORKSPACE_SESSION_CHANGED', '工作区已切换，请在当前工作区重新发问。')
        try:
            self.observe_foreground()
        except Exception:
            pass  # GetForegroundWindow can return NULL while losing activation.
        with self.lock: recent = _copy(self.recent) if self.recent else None
        if not recent or recent['workspace_session_ref'] != binding['workspace_session_ref']:
            if self.recent_issue:
                return self._missing(self.recent_issue, '刚才的前台软件不在已确认的读取范围，请确认要查看哪个软件页面。')
            return self._missing('NO_RECENT_WORKSPACE_WINDOW', '请先在要询问的软件页面操作，再回到聊天发问。')
        before = recent['window']
        try:
            current = self.source.inspect(before['window_id'])
        except Exception:
            return self._missing('RECENT_WINDOW_LOST', '刚才的软件窗口已关闭或无法读取，请确认要查看哪个窗口。')
        if current.get('status') != 'WINDOW_OBSERVED' or current.get('identity_sha256') != before.get('identity_sha256'):
            return self._missing('WINDOW_LIFETIME_CHANGED', '软件窗口身份已变化，请重新选择要查看的页面。')
        if current.get('minimized') is True or current.get('visible') is not True:
            return self._missing('WINDOW_NOT_OBSERVABLE', '刚才的软件窗口已最小化或隐藏，请打开该页面后再问。')
        errors = []
        own_visual = callable(self.visual_capture) and (requested['visual'] or requested['ocr'])
        sdk_readable = True
        if callable(self.before_read) and (requested['accessibility'] or
                (not own_visual and (requested['visual'] or requested['ocr']))):
            try:
                self.before_read(current)
            except Exception as error:
                if not own_visual:
                    raise
                sdk_readable = False
                errors.append({'mode': 'accessibility', 'error_type': type(error).__name__})
            updated = self._binding()
            if any(updated[key] != binding[key] for key in ('workspace_session_ref', 'surface_ref', 'input_epoch')):
                return self._missing('CURRENT_SCOPE_CHANGED', '读取前工作区状态变化，请重新发问。')
            binding = updated
        with self.observation_guard(binding['surface_ref'], binding['input_epoch']):
            if self._binding() != binding: return self._missing('CURRENT_SCOPE_CHANGED', '工作区状态已变化，请重新发问。')
            attested = _copy(self.authority(current, binding), limit=65536)
        if attested.get('eligible') is not True or attested.get('ambiguous') is True:
            return self._missing('WINDOW_SCOPE_AMBIGUOUS', '当前窗口范围无法唯一确认，请说明是哪一个软件窗口。', alternatives=attested.get('alternatives'))
        target_ref = surface_reference(attested.get('target_ref'))
        document_before = self._document_identity(current)
        outputs = {}
        if binding['sdk_session_ref'] is None and not own_visual and any(requested[k] for k in ['accessibility', 'visual', 'ocr']):
            return {'status': 'UNAVAILABLE', 'reason': 'ORIGINAL_CUA_SDK_SESSION_UNAVAILABLE',
                'window_ref': target_ref, 'window_lifetime_sha256': current['identity_sha256'],
                'window': {k: current[k] for k in ['title', 'window_class', 'dpi', 'bounds']},
                'coverage': {'metadata': True, 'accessibility': False, 'visual': False, 'whole_page_seen': False},
                'automatic_input_authorized': False, 'questions': [],
                'limitations': ['METADATA_ONLY_NOT_PAGE_CONTENT', 'ORIGINAL_READONLY_SDK_SESSION_REQUIRED']}
        # Never hold a caller-thread Root RLock while waiting for SDK worker:
        # each original readonly call holds its own worker-thread guard.
        if requested['accessibility']:
            try:
                if binding['sdk_session_ref'] is None or not sdk_readable:
                    raise CurrentContextError('READONLY_ACCESSIBILITY_SESSION_UNAVAILABLE')
                outputs['accessibility'] = self.accessibility(binding['sdk_session_ref'], target_ref,
                    binding['surface_ref'], binding['input_epoch'])
            except Exception as error:
                errors.append({'mode': 'accessibility', 'error_type': type(error).__name__})
        if requested['visual'] or requested['ocr']:
            if own_visual:
                try:
                    with self.observation_guard(binding['surface_ref'], binding['input_epoch']):
                        if self._binding() != binding:
                            return self._missing('CURRENT_SCOPE_CHANGED', '读取前工作区状态变化，请重新发问。')
                        outputs['visual'] = self.visual_capture(current, target_ref, binding)
                except Exception as error:
                    errors.append({'mode': 'visible_rectangle', 'error_type': type(error).__name__})
            if 'visual' not in outputs and self.cua is not None and binding['sdk_session_ref'] is not None and sdk_readable:
                try:
                    outputs['visual'] = self.cua.capture(binding['sdk_session_ref'], target_ref,
                        binding['surface_ref'], binding['input_epoch'])
                except Exception as error:
                    errors.append({'mode': 'visual', 'error_type': type(error).__name__})
            if requested['ocr'] and 'visual' in outputs:
                try:
                    if not callable(self.ocr):
                        outputs['ocr'] = {'status': 'UNAVAILABLE', 'reason': 'OFFLINE_OCR_NOT_CONFIGURED'}
                    else:
                        frame = outputs['visual']
                        ocr = _copy(self.ocr(_copy(frame)), limit=2 * 1024 * 1024)
                        if (ocr.get('frame_ref') != frame['frame_ref']
                                or ocr.get('image_sha256') != frame['image_ref']['sha256']):
                            raise CurrentContextError('OCR_DIFFERS_FROM_EXACT_CURRENT_FRAME')
                        outputs['ocr'] = ocr
                except Exception as error:
                    errors.append({'mode': 'ocr', 'error_type': type(error).__name__})
        try:
            with self.observation_guard(binding['surface_ref'], binding['input_epoch']):
                if self._binding() != binding: return self._missing('CURRENT_SCOPE_CHANGED', '读取过程中工作区变化，请重新发问。')
                after = self.source.inspect(before['window_id'])
                after_authority = self.authority(after, binding)
                if (after != current or not isinstance(after_authority, dict)
                        or after_authority.get('eligible') is not True or after_authority.get('ambiguous') is True
                        or after_authority.get('target_ref') != target_ref):
                    return self._missing('WINDOW_CHANGED_DURING_OBSERVATION', '软件页面或窗口状态在读取时变化，请重新发问。')
        except Exception as error:
            return self._missing('WINDOW_LOST_DURING_OBSERVATION', '读取过程中窗口关闭或范围无法再次确认，请重新打开要询问的页面。',
                alternatives=[{'error_type': type(error).__name__}])
        document = None
        if document_before is not None and document_before.get('status') == 'OBSERVED':
            document_after = self._document_identity(after)
            try:
                final_window = self.source.inspect(before['window_id'])
                if document_before == document_after and final_window.get('identity_sha256') == current['identity_sha256']:
                    document = document_before
            except Exception:
                pass
        accessibility = outputs.get('accessibility', {}).get('coverage', {})
        visual = outputs.get('visual')
        coverage = {'metadata': requested['metadata'], 'accessibility': accessibility.get('accessibility') is True,
            'accessibility_complete': accessibility.get('elements_complete') is True,
            'visual': visual is not None, 'ocr': outputs.get('ocr', {}).get('status') == 'RECOGNIZED',
            'whole_page_seen': False, 'page_content_is_engineering_fact': False,
            # Host metadata cannot certify where screenshot pixels originated.
            # Only the adapter's proof bound to these exact bytes may do that.
            'pixel_source': visual.get('pixel_source', 'UNKNOWN') if visual else 'NOT_CAPTURED',
            'degraded': accessibility.get('degraded') is True, 'truncated': accessibility.get('truncated') is True}
        questions = []
        if requested['accessibility'] and not coverage['accessibility'] and not visual:
            questions.append({'code': 'NO_READABLE_PAGE_CONTENT', 'text': '没有读到可访问性页面正文，需要按需截图或 OCR 来查看当前页。'})
        if visual and coverage['pixel_source'] not in {'EXACT_TARGET_WINDOW', 'VERIFIED_TARGET_PIXELS'}:
            if coverage['pixel_source'] == 'VISIBLE_TARGET_RECTANGLE':
                questions.append({'code': 'VISIBLE_RECTANGLE_MAY_INCLUDE_OCCLUSION',
                    'text': '画面仅包含此刻可见的窗口区域；若被其他软件遮挡，请先露出要讨论的位置。'})
            else:
                questions.append({'code': 'PIXEL_SOURCE_UNKNOWN_OR_OCCLUDED', 'text': '截图是否包含被遮挡窗口尚未确认，请确认要查看的页面。'})
        if requested['visual'] and not visual:
            questions.append({'code': 'VISUAL_OBSERVATION_UNAVAILABLE', 'text': '没有取得当前窗口的可靠画面，可尝试可访问性读取或确认页面状态。'})
        if requested['ocr'] and not coverage['ocr']:
            questions.append({'code': 'OCR_UNAVAILABLE_OR_NO_TEXT', 'text': 'OCR 未读到可靠正文，可使用可访问性或当前画面继续核对。'})
        safe_window = {k: current[k] for k in ['title', 'window_class', 'dpi', 'bounds']}
        public = {'status': 'NEEDS_CLARIFICATION' if questions else 'READY_OBSERVATION',
            'runtime_role': 'READONLY_OBSERVATION',
            'workspace_session_ref': binding['workspace_session_ref'], 'surface_ref': binding['surface_ref'],
            'input_epoch': binding['input_epoch'], 'sdk_session_ref': binding['sdk_session_ref'],
            'window_ref': target_ref, 'window_lifetime_sha256': current['identity_sha256'],
            'window': safe_window, 'selected_at': recent['used_at'], 'observed_at': self.clock(),
            'selection_source': recent['selection_source'], 'observation_mode': requested,
            'intent': intent, 'task_ref': task_ref, 'observations': outputs, 'coverage': coverage,
            'questions': questions, 'observation_errors': errors,
            'send_full_image_to_model': False, 'automatic_input_authorized': False,
            'limitations': ['OBSERVATION_NOT_ENGINEERING_FACT', 'NO_FILE_SOURCE_INFERRED_FROM_TITLE',
                'WINDOW_CONTENT_CAN_CHANGE_WITHOUT_GEOMETRY_EVENT', 'ADAPTIVE_TOOL_SELECTION_REMAINS_ORIGINAL_OPENCODE']}
        if callable(self.document_identity_reader):
            from packages.adapters.native_document_identity import public_document_identity
            public['document_identity'] = public_document_identity(document or {'status': 'UNAVAILABLE'})
        public['context_ref'] = {'id': uuid.uuid4().hex, 'revision': 1, 'sha256': digest(public)}
        with self.lock:
            self.contexts[public['context_ref']['id']] = {'public': _copy(public), 'window': _copy(current), 'binding': binding}
            if document is not None:
                self.contexts[public['context_ref']['id']]['document_identity'] = _copy(document)
            while len(self.contexts) > 16:
                self.contexts.pop(next(iter(self.contexts)))
        return public

    def _document_identity(self, window):
        if not callable(self.document_identity_reader):
            return None
        try:
            value = _copy(self.document_identity_reader(_copy(window)), limit=16384)
            proof = {key: value[key] for key in ('source', 'native_path', 'saved', 'window_lifetime_sha256', 'content_equals_saved_bytes')}
            if (value.get('status') == 'OBSERVED' and value.get('identity_sha256') == digest(proof)
                    and window.get('window_class') == 'OpusApp' and proof['source'] == 'WORD_WINDOW_NATIVEOM_PROPERTYGET'
                    and proof['window_lifetime_sha256'] == window['identity_sha256']
                    and proof['content_equals_saved_bytes'] is False and type(proof['saved']) is bool
                    and isinstance(proof['native_path'], str) and 0 < len(proof['native_path']) <= 4096):
                return value
        except Exception:
            pass
        return {'status': 'UNAVAILABLE'}

    def document_identity(self, context_ref):
        """Host-private lookup of the frozen observation, not a fresh COM read."""
        context_ref = surface_reference(context_ref)
        with self.lock:
            record = self.contexts.get(context_ref['id'])
            if self.closed or not record or record['public']['context_ref'] != context_ref:
                raise CurrentContextError('EXACT_CONTEXT_UNKNOWN_OR_RESTARTED')
            return _copy(record.get('document_identity')) if record.get('document_identity') else None

    def get(self, context_ref):
        """Immutable snapshot lookup; never opens or awaits a native SDK call."""
        context_ref = surface_reference(context_ref)
        with self.lock:
            record = self.contexts.get(context_ref['id'])
            if self.closed or not record or record['public']['context_ref'] != context_ref:
                raise CurrentContextError('EXACT_CONTEXT_UNKNOWN_OR_RESTARTED')
            return _copy(record['public'])

    def check_current(self, context_ref, target_ref, surface_ref, input_epoch):
        """Identity/scope proof only, not a proof that page contents are unchanged."""
        return self._check_scope(context_ref, target_ref, surface_ref, input_epoch, readonly=False)

    def check_observation_current(self, context_ref, target_ref, surface_ref, input_epoch):
        """Partial readable content can still belong to the current readonly object."""
        return self._check_scope(context_ref, target_ref, surface_ref, input_epoch, readonly=True)

    def _check_scope(self, context_ref, target_ref, surface_ref, input_epoch, *, readonly):
        try:
            context_ref, target_ref, surface_ref = map(surface_reference, [context_ref, target_ref, surface_ref])
            _integer(input_epoch, 0, 1 << 63, 'CURRENT_INPUT_EPOCH_REQUIRED')
            with self.lock:
                record = self.contexts.get(context_ref['id'])
                if self.closed or not record or record['public']['context_ref'] != context_ref:
                    return False
                record = _copy(record)
                recent = _copy(self.recent) if self.recent else None
            binding = self._binding()
            public = record['public']
            accepted = ({'READY_OBSERVATION', 'NEEDS_CLARIFICATION'}
                if readonly and public.get('runtime_role') == 'READONLY_OBSERVATION' else {'READY_OBSERVATION'})
            if (public['status'] not in accepted or public['window_ref'] != target_ref
                    or public['surface_ref'] != surface_ref or public['input_epoch'] != input_epoch
                    or record['binding'] != binding or not recent
                    or recent['window']['identity_sha256'] != public['window_lifetime_sha256']):
                return False
            # Native metadata only, safe while Root sync guard owns wsRLock on
            # SDK worker. No waiting for that same worker or a new screenshot.
            with self.observation_guard(surface_ref, input_epoch):
                actual = self.source.inspect(record['window']['window_id'])
                authority = self.authority(actual, binding)
                return (actual == record['window'] and self._binding() == binding
                        and isinstance(authority, dict) and authority.get('eligible') is True
                        and authority.get('ambiguous') is not True and authority.get('target_ref') == target_ref)
        except Exception:
            return False

    def close(self):
        self.closed = True
        with self.lock:
            self.recent = None
            self.contexts.clear()
        return self.source.close()
