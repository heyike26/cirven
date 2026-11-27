"""On-demand, pass-through Windows pointer-down observation for one owned Orb.

No SDK, input injection, foreground change, disk access or model is involved.
The low-level callback only copies a bounded event and always calls the next
hook. Window/owner checks run on the blocking stream consumer, outside it.
"""
from __future__ import annotations

import ctypes as ct
import math
import os
import queue
import threading
import time


class PointerWatchError(RuntimeError):
    pass


class _POINT(ct.Structure):
    _fields_ = [('x', ct.c_int32), ('y', ct.c_int32)]


class _MSLLHOOKSTRUCT(ct.Structure):
    _fields_ = [('point', _POINT), ('mouseData', ct.c_uint32),
               ('flags', ct.c_uint32), ('time', ct.c_uint32), ('extra', ct.c_size_t)]


class _MSG(ct.Structure):
    _fields_ = [('hwnd', ct.c_void_p), ('message', ct.c_uint32),
               ('wParam', ct.c_size_t), ('lParam', ct.c_ssize_t),
               ('time', ct.c_uint32), ('point', _POINT), ('private', ct.c_uint32)]


class Win32PointerAPI:
    def __init__(self, *, libraries=None):
        if libraries is None:
            if os.name != 'nt':
                raise PointerWatchError('WINDOWS_POINTER_WATCH_UNAVAILABLE')
            libraries = {name: ct.WinDLL(name, use_last_error=True) for name in ('user32', 'kernel32')}
        self.user, self.kernel = libraries['user32'], libraries['kernel32']
        H, D, I = ct.c_void_p, ct.c_uint32, ct.c_int32
        self.callback_type = getattr(ct, 'WINFUNCTYPE', ct.CFUNCTYPE)(ct.c_ssize_t, I, ct.c_size_t, ct.c_ssize_t)
        for lib, name, result, args in (
            (self.user, 'SetWindowsHookExW', H, [I, self.callback_type, H, D]),
            (self.user, 'CallNextHookEx', ct.c_ssize_t, [H, I, ct.c_size_t, ct.c_ssize_t]),
            (self.user, 'UnhookWindowsHookEx', I, [H]),
            (self.user, 'PeekMessageW', I, [ct.POINTER(_MSG), H, D, D, D]),
            (self.user, 'MsgWaitForMultipleObjectsEx', D, [D, ct.POINTER(H), D, D, D]),
            (self.user, 'PostThreadMessageW', I, [D, D, ct.c_size_t, ct.c_ssize_t]),
            (self.user, 'WindowFromPoint', H, [_POINT]),
            (self.user, 'GetAncestor', H, [H, D]),
            (self.user, 'SetThreadDpiAwarenessContext', H, [H]),
            (self.kernel, 'GetCurrentThreadId', D, []),
            (self.kernel, 'GetModuleHandleW', H, [ct.c_wchar_p]),
        ):
            function = getattr(lib, name)
            function.restype, function.argtypes = result, args

    def root_at(self, x, y):
        previous = self.user.SetThreadDpiAwarenessContext(-4)
        if not previous:
            raise PointerWatchError('POINTER_DPI_CONTEXT_UNAVAILABLE')
        try:
            hwnd = self.user.WindowFromPoint(_POINT(x, y))
            root = int(self.user.GetAncestor(hwnd, 2) or 0) if hwnd else 0
            if not root:
                raise PointerWatchError('POINTER_WINDOW_UNAVAILABLE')
            return root
        finally:
            if not self.user.SetThreadDpiAwarenessContext(previous):
                raise PointerWatchError('POINTER_DPI_RESTORE_UNKNOWN')


_STOP = object()


class OrbPointerWatch:
    """One hook thread and a one-event inbox, with no idle sampling loop."""
    def __init__(self, identity, validate, *, api=None, lease_seconds=60, clock=time.monotonic):
        if not callable(validate) or not 0 < lease_seconds <= 60:
            raise PointerWatchError('BOUNDED_OWNED_POINTER_WATCH_REQUIRED')
        self.identity, self.validate = identity, validate
        self.api = api if api is not None else Win32PointerAPI()
        self.lease_seconds, self.clock = lease_seconds, clock
        self.queue = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.overflow = threading.Event()
        self.expired = threading.Event()
        self.thread = self.callback = self.handle = self.thread_id = None
        self.fault = None
        self.deadline = None
        self._close_lock = threading.Lock()

    def _offer(self, item):
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            self.overflow.set()

    def _callback(self, code, kind, address):
        try:
            if code >= 0 and not self.stop.is_set() and kind in (0x201, 0x204, 0x207, 0x20B):
                event = ct.cast(address, ct.POINTER(_MSLLHOOKSTRUCT)).contents
                button = {0x201: 'LEFT', 0x204: 'RIGHT', 0x207: 'MIDDLE'}.get(kind)
                if kind == 0x20B:
                    button = {1: 'X1', 2: 'X2'}.get((event.mouseData >> 16) & 0xffff)
                if button is not None:
                    self._offer((int(event.point.x), int(event.point.y), button, int(event.time)))
        except Exception:
            self.fault = 'POINTER_CALLBACK_UNAVAILABLE'
            self._offer(_STOP)
        # Never suppress, replay or redirect the original mouse event.
        return self.api.user.CallNextHookEx(None, code, kind, address)

    def _run(self):
        try:
            self.thread_id = int(self.api.kernel.GetCurrentThreadId())
            message = _MSG()
            self.api.user.PeekMessageW(ct.byref(message), None, 0, 0, 0)
            self.callback = self.api.callback_type(self._callback)  # Strong until joined.
            self.handle = self.api.user.SetWindowsHookExW(14, self.callback,
                self.api.kernel.GetModuleHandleW(None), 0)
            if not self.handle:
                raise PointerWatchError('POINTER_HOOK_INSTALL_FAILED')
            self.deadline = self.clock() + self.lease_seconds
            self.ready.set()
            while not self.stop.is_set():
                remaining = self.deadline - self.clock()
                if remaining <= 0:
                    self.expired.set()
                    break
                # Kernel-blocked until input/own WM_QUIT or the one lease deadline.
                # PeekMessage dispatches the nonqueued low-level-hook callback.
                result = self.api.user.MsgWaitForMultipleObjectsEx(0, None,
                    max(1, math.ceil(remaining * 1000)), 0x04ff, 0x0004)
                if result == 258:
                    self.expired.set()
                    break
                if result != 0:
                    raise PointerWatchError('POINTER_MESSAGE_WAIT_FAILED')
                while self.api.user.PeekMessageW(ct.byref(message), None, 0, 0, 1):
                    if message.message == 0x12:
                        if not self.stop.is_set():
                            self.fault = 'POINTER_MESSAGE_LOOP_ENDED'
                        return
                    if self.clock() >= self.deadline or self.stop.is_set():
                        break
        except Exception as error:
            self.fault = str(error) if isinstance(error, PointerWatchError) else 'POINTER_HOOK_UNAVAILABLE'
        finally:
            if self.handle:
                try:
                    if self.api.user.UnhookWindowsHookEx(self.handle):
                        self.handle = None
                    else:
                        self.fault = 'POINTER_UNHOOK_UNKNOWN'
                except Exception:
                    self.fault = 'POINTER_UNHOOK_UNKNOWN'
            self.ready.set()
            self._offer(_STOP)

    def start(self):
        if self.thread is not None or self.stop.is_set():
            raise PointerWatchError('POINTER_WATCH_ALREADY_STARTED_OR_CLOSED')
        self.validate(self.identity)
        self.thread = threading.Thread(target=self._run, name='Cirven-orb-pointer-watch', daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.fault or not self.handle:
            raise PointerWatchError(self.fault or 'POINTER_HOOK_START_UNKNOWN')
        self.validate(self.identity)

    def next_event(self):
        """Blocking bounded wait. No native calls run on the callback thread."""
        while not self.stop.is_set():
            if self.fault:
                return {'kind': 'UNAVAILABLE', 'code': self.fault}
            if self.expired.is_set():
                return {'kind': 'EXPIRED'}
            if self.overflow.is_set():
                return {'kind': 'UNAVAILABLE', 'code': 'POINTER_EVENT_OVERFLOW'}
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                return {'kind': 'EXPIRED'}
            try:
                item = self.queue.get(timeout=remaining)
            except queue.Empty:
                return {'kind': 'EXPIRED'}
            if self.stop.is_set():
                return None
            if self.fault or self.overflow.is_set() or item is _STOP:
                if self.expired.is_set() and not self.fault:
                    return {'kind': 'EXPIRED'}
                return {'kind': 'UNAVAILABLE', 'code': self.fault or
                        ('POINTER_EVENT_OVERFLOW' if self.overflow.is_set() else 'POINTER_HOOK_ENDED')}
            try:
                self.validate(self.identity)
                x, y, button, event_time = item
                root = self.api.root_at(x, y)
                self.validate(self.identity)
            except Exception:
                return {'kind': 'UNAVAILABLE', 'code': 'OWNED_ORB_OR_POINTER_LIFETIME_UNAVAILABLE'}
            if root != self.identity['marker']['window_id']:
                return {'kind': 'OUTSIDE_POINTER_DOWN', 'button': button, 'os_event_time': event_time}
        return None

    def close(self):
        with self._close_lock:
            self.stop.set()
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self._offer(_STOP)
            if self.thread is not None and self.thread.is_alive():
                if self.thread_id:
                    self.api.user.PostThreadMessageW(self.thread_id, 0x12, 0, 0)  # Own WM_QUIT only.
                self.thread.join(5)
            closed = not self.handle and (self.thread is None or not self.thread.is_alive())
            if closed:
                self.callback = None
            return {'status': 'CLOSED' if closed else 'CLOSE_UNKNOWN'}
