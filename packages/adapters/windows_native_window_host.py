"""Exact newly launched document-window host and bounded selected-window frames.

Win32 calls are implemented, but real Windows/app qualification is independent
of the portable ABI and policy tests. No desktop/monitor screenshot fallback.
"""
from __future__ import annotations

import ctypes as ct
from dataclasses import dataclass
from functools import wraps
import hashlib
from io import BytesIO
import importlib.metadata
import inspect
import multiprocessing
import os
from pathlib import Path, PureWindowsPath
import threading
import time
import uuid

from PIL import Image, ImageStat

DWORD = ct.c_uint32
LONG = ct.c_int32
BOOL = ct.c_int32
HANDLE = ct.c_void_p
LONG_PTR = ct.c_ssize_t

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
GWL_STYLE = -16
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
STILL_ACTIVE = 259
MAX_FRAME_PIXELS = 16_000_000
MAX_FRAME_BYTES = 32 * 1024 * 1024
SUPPORTED_WGC_VERSION = "2.0.1"


class NativeWindowError(RuntimeError): pass
class NativeWindowUnavailable(NativeWindowError): pass


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock: return method(self, *args, **kwargs)
    return call


class POINT(ct.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(ct.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class FILETIME(ct.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]
    @property
    def value(self): return self.low | (self.high << 32)


class WINDOWPLACEMENT(ct.Structure):
    _fields_ = [("length", DWORD), ("flags", DWORD), ("showCmd", DWORD),
        ("ptMinPosition", POINT), ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]


class SHELLEXECUTEINFOW(ct.Structure):
    _fields_ = [("cbSize", DWORD), ("fMask", DWORD), ("hwnd", HANDLE),
        ("lpVerb", ct.c_wchar_p), ("lpFile", ct.c_wchar_p), ("lpParameters", ct.c_wchar_p),
        ("lpDirectory", ct.c_wchar_p), ("nShow", ct.c_int32), ("hInstApp", HANDLE),
        ("lpIDList", HANDLE), ("lpClass", ct.c_wchar_p), ("hkeyClass", HANDLE),
        ("dwHotKey", DWORD), ("hIconOrMonitor", HANDLE), ("hProcess", HANDLE)]


class BITMAPINFOHEADER(ct.Structure):
    _fields_ = [("size", DWORD), ("width", LONG), ("height", LONG), ("planes", ct.c_uint16),
        ("bit_count", ct.c_uint16), ("compression", DWORD), ("image_size", DWORD),
        ("x_pixels", LONG), ("y_pixels", LONG), ("colors_used", DWORD), ("colors_important", DWORD)]


@dataclass
class _OwnedWindow:
    process_handle: int
    pid: int
    creation: int
    image_path: str
    hwnd: int | None = None
    window_thread: int | None = None
    window_class: str | None = None
    original_parent: int | None = None
    original_style: int | None = None
    original_placement: bytes | None = None
    attached_parent: int | None = None
    property_name: str | None = None
    property_value: int | None = None


def physical_bounds(value):
    if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}:
        raise NativeWindowError("NATIVE_PHYSICAL_BOUNDS_REQUIRED")
    if any(type(v) is not int for v in value.values()): raise NativeWindowError("NATIVE_BOUNDS_INVALID")
    if not (-32768 <= value["x"] <= 32768 and -32768 <= value["y"] <= 32768 and
            1 <= value["width"] <= 32768 and 1 <= value["height"] <= 32768 and
            value["width"] * value["height"] <= MAX_FRAME_PIXELS):
        raise NativeWindowError("NATIVE_BOUNDS_INVALID")
    return dict(value)


def native_document_path(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value) or '"' in value:
        raise NativeWindowError("NATIVE_SELECTED_LOCAL_FILE_REQUIRED")
    path = PureWindowsPath(value)
    if (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ":" or
            any(part in {".", ".."} or ":" in part for part in path.parts[1:])):
        raise NativeWindowError("NATIVE_SELECTED_LOCAL_FILE_REQUIRED")
    return str(path)


def frame_png(image):
    if image.width * image.height > MAX_FRAME_PIXELS or image.width < 1 or image.height < 1:
        raise NativeWindowError("NATIVE_FRAME_SIZE_INVALID")
    image = image.convert("RGB")
    # This rejects uniform images; it does not certify complete or correct text.
    stats = ImageStat.Stat(image)
    if max(stats.stddev) < .5: raise NativeWindowUnavailable("NATIVE_FRAME_BLANK")
    stream = BytesIO(); image.save(stream, "PNG"); raw = stream.getvalue()
    if len(raw) > MAX_FRAME_BYTES: raise NativeWindowError("NATIVE_FRAME_TOO_LARGE")
    return raw


class Win32WindowApi:
    """Stock user32/kernel32/shell32 ABI; injection is only for ABI tests."""
    def __init__(self, *, libraries=None):
        if libraries is None:
            if os.name != "nt": raise NativeWindowUnavailable("WINDOWS_NATIVE_NOT_RUN")
            try:
                libraries = {name: ct.WinDLL(name, use_last_error=True) for name in ("user32", "kernel32", "shell32", "ole32", "gdi32")}
            except OSError:
                raise NativeWindowUnavailable("WINDOWS_NATIVE_SYSTEM_LIBRARY_UNAVAILABLE") from None
        self.user, self.kernel, self.shell, self.ole, self.gdi = (libraries[n] for n in ("user32", "kernel32", "shell32", "ole32", "gdi32"))
        callback = getattr(ct, "WINFUNCTYPE", ct.CFUNCTYPE)
        self.enum_callback = callback(BOOL, HANDLE, LONG_PTR)
        self._bind(self.shell, "ShellExecuteExW", BOOL, [ct.POINTER(SHELLEXECUTEINFOW)])
        self._bind(self.ole, "CoInitializeEx", LONG, [HANDLE, DWORD]); self._bind(self.ole, "CoUninitialize", None, [])
        for name, result, args in (
            ("CloseHandle", BOOL, [HANDLE]), ("GetProcessId", DWORD, [HANDLE]),
            ("GetProcessTimes", BOOL, [HANDLE, ct.POINTER(FILETIME), ct.POINTER(FILETIME), ct.POINTER(FILETIME), ct.POINTER(FILETIME)]),
            ("GetExitCodeProcess", BOOL, [HANDLE, ct.POINTER(DWORD)]),
            ("GetSystemTimeAsFileTime", None, [ct.POINTER(FILETIME)]),
            ("QueryFullProcessImageNameW", BOOL, [HANDLE, DWORD, ct.c_wchar_p, ct.POINTER(DWORD)]),
            ("OpenProcess", HANDLE, [DWORD, BOOL, DWORD]), ("ProcessIdToSessionId", BOOL, [DWORD, ct.POINTER(DWORD)]),
        ): self._bind(self.kernel, name, result, args)
        for name, result, args in (
            ("EnumWindows", BOOL, [self.enum_callback, LONG_PTR]), ("IsWindow", BOOL, [HANDLE]),
            ("IsWindowVisible", BOOL, [HANDLE]), ("IsIconic", BOOL, [HANDLE]),
            ("GetWindowThreadProcessId", DWORD, [HANDLE, ct.POINTER(DWORD)]),
            ("GetClassNameW", ct.c_int32, [HANDLE, ct.c_wchar_p, ct.c_int32]), ("GetParent", HANDLE, [HANDLE]),
            ("SetParent", HANDLE, [HANDLE, HANDLE]), ("GetWindowRect", BOOL, [HANDLE, ct.POINTER(RECT)]),
            ("SetPropW", BOOL, [HANDLE, ct.c_wchar_p, HANDLE]), ("GetPropW", HANDLE, [HANDLE, ct.c_wchar_p]),
            ("RemovePropW", HANDLE, [HANDLE, ct.c_wchar_p]),
            ("GetClientRect", BOOL, [HANDLE, ct.POINTER(RECT)]),
            ("GetWindowPlacement", BOOL, [HANDLE, ct.POINTER(WINDOWPLACEMENT)]),
            ("SetWindowPlacement", BOOL, [HANDLE, ct.POINTER(WINDOWPLACEMENT)]),
            ("SetWindowPos", BOOL, [HANDLE, HANDLE, ct.c_int32, ct.c_int32, ct.c_int32, ct.c_int32, DWORD]),
            ("SetForegroundWindow", BOOL, [HANDLE]), ("SetFocus", HANDLE, [HANDLE]),
            ("SendMessageTimeoutW", LONG_PTR, [HANDLE, DWORD, ct.c_size_t, LONG_PTR, DWORD, DWORD, ct.POINTER(ct.c_size_t)]),
            ("GetWindowDpiAwarenessContext", HANDLE, [HANDLE]),
            ("AreDpiAwarenessContextsEqual", BOOL, [HANDLE, HANDLE]), ("GetDpiForWindow", DWORD, [HANDLE]),
            ("PrintWindow", BOOL, [HANDLE, HANDLE, DWORD]), ("GetWindowDC", HANDLE, [HANDLE]),
            ("ReleaseDC", ct.c_int32, [HANDLE, HANDLE]),
        ): self._bind(self.user, name, result, args)
        get_name = "GetWindowLongPtrW" if ct.sizeof(HANDLE) == 8 else "GetWindowLongW"
        set_name = "SetWindowLongPtrW" if ct.sizeof(HANDLE) == 8 else "SetWindowLongW"
        self.get_style = self._bind(self.user, get_name, LONG_PTR, [HANDLE, ct.c_int32])
        self.set_style = self._bind(self.user, set_name, LONG_PTR, [HANDLE, ct.c_int32, LONG_PTR])
        for name, result, args in (
            ("CreateCompatibleDC", HANDLE, [HANDLE]), ("CreateCompatibleBitmap", HANDLE, [HANDLE, ct.c_int32, ct.c_int32]),
            ("SelectObject", HANDLE, [HANDLE, HANDLE]), ("DeleteObject", BOOL, [HANDLE]), ("DeleteDC", BOOL, [HANDLE]),
            ("GetDIBits", ct.c_int32, [HANDLE, HANDLE, DWORD, DWORD, HANDLE, ct.POINTER(BITMAPINFOHEADER), DWORD]),
        ): self._bind(self.gdi, name, result, args)

    @staticmethod
    def _bind(library, name, result, args):
        try: function = getattr(library, name)
        except AttributeError:
            raise NativeWindowUnavailable("WINDOWS_NATIVE_API_UNAVAILABLE_" + name) from None
        function.restype = result; function.argtypes = args
        return function

    @staticmethod
    def require(value, code):
        if not value: raise NativeWindowError(code)
        return value

    @staticmethod
    def pointer_call(function, *args):
        ct.set_last_error(0); result = function(*args); error = ct.get_last_error()
        if not result and error: raise NativeWindowError("WIN32_ERROR_" + str(error))
        return result or 0

    def timestamp(self):
        value = FILETIME(); self.kernel.GetSystemTimeAsFileTime(ct.byref(value)); return value.value

    def process(self, handle):
        pid = self.require(self.kernel.GetProcessId(handle), "NATIVE_PROCESS_ID_UNAVAILABLE")
        creation, end, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        self.require(self.kernel.GetProcessTimes(handle, ct.byref(creation), ct.byref(end), ct.byref(kernel), ct.byref(user)), "NATIVE_PROCESS_IDENTITY_UNAVAILABLE")
        code = DWORD(); self.require(self.kernel.GetExitCodeProcess(handle, ct.byref(code)), "NATIVE_PROCESS_STATE_UNAVAILABLE")
        path = ct.create_unicode_buffer(32768); size = DWORD(len(path))
        self.require(self.kernel.QueryFullProcessImageNameW(handle, 0, path, ct.byref(size)), "NATIVE_PROCESS_IMAGE_UNAVAILABLE")
        session = DWORD(); self.require(self.kernel.ProcessIdToSessionId(pid, ct.byref(session)), "NATIVE_PROCESS_SESSION_UNAVAILABLE")
        return {"pid": int(pid), "creation": creation.value, "image_path": path.value, "exit_code": code.value, "session": session.value}

    def window(self, hwnd):
        self.require(self.user.IsWindow(hwnd), "NATIVE_WINDOW_LOST")
        pid = DWORD(); thread = self.require(self.user.GetWindowThreadProcessId(hwnd, ct.byref(pid)), "NATIVE_WINDOW_OWNER_UNAVAILABLE")
        name = ct.create_unicode_buffer(256); self.require(self.user.GetClassNameW(hwnd, name, len(name)), "NATIVE_WINDOW_CLASS_UNAVAILABLE")
        return {"pid": pid.value, "thread": int(thread), "class": name.value,
            "visible": bool(self.user.IsWindowVisible(hwnd)), "minimized": bool(self.user.IsIconic(hwnd)),
            "dpi_context": self.require(self.user.GetWindowDpiAwarenessContext(hwnd), "NATIVE_DPI_UNAVAILABLE"),
            "dpi": self.require(self.user.GetDpiForWindow(hwnd), "NATIVE_DPI_UNAVAILABLE")}

    def shell_open(self, native_path):
        before = self.timestamp()
        info = SHELLEXECUTEINFOW(); info.cbSize = ct.sizeof(info)
        info.fMask = 0x40 | 0x100 | 0x400  # process handle, synchronous DDE, no error UI
        info.lpVerb = "open"; info.lpFile = native_document_path(native_path)
        info.lpParameters = None; info.lpDirectory = str(PureWindowsPath(info.lpFile).parent)
        info.nShow = 4  # requested show without activation; app decides whether to honor it
        initialized = self.ole.CoInitializeEx(None, 2)
        if initialized not in (0, 1): raise NativeWindowUnavailable("NATIVE_COM_APARTMENT_UNAVAILABLE")
        try:
            self.require(self.shell.ShellExecuteExW(ct.byref(info)), "NATIVE_ASSOCIATION_OPEN_FAILED")
        finally: self.ole.CoUninitialize()
        if not info.hProcess: return None
        try:
            process = self.process(info.hProcess)
            if process["creation"] < before or process["exit_code"] != STILL_ACTIVE or process["session"] == 0:
                self.kernel.CloseHandle(info.hProcess); return None
            return _OwnedWindow(info.hProcess, process["pid"], process["creation"], process["image_path"])
        except BaseException:
            self.kernel.CloseHandle(info.hProcess); raise

    def candidates(self, pid):
        windows = []
        @self.enum_callback
        def receive(hwnd, _param):
            try:
                value = self.window(hwnd)
                if value["pid"] == pid and value["visible"] and not value["minimized"] and not self.user.GetParent(hwnd):
                    windows.append((int(hwnd), value))
            except NativeWindowError: pass
            return 1
        self.require(self.user.EnumWindows(receive, 0), "NATIVE_WINDOW_ENUM_FAILED")
        return windows


class WindowsNativeWindowHost:
    """Opaque leases minted solely from this instance's ShellExecuteEx result."""
    def __init__(self, *, api=None):
        self.api = api or Win32WindowApi()
        self.leases = {}; self.lock = threading.RLock()

    @serialized
    def launch(self, path):
        owned = self.api.shell_open(native_document_path(path))
        if owned is None: return {"status": "OPENED_SEPARATELY", "reason": "NO_NEW_OWNED_PROCESS", "lease_ref": None}
        reference = uuid.uuid4().hex; self.leases[reference] = owned
        return {"status": "WAITING_WINDOW", "lease_ref": reference, "app_path": owned.image_path,
            "background_launch": "REQUEST_ONLY_APPLICATION_CONTROLLED", "qualification": "WINDOWS_APP_HOST_NOT_VALIDATED"}

    def _owned(self, reference, *, require_window=False):
        if not isinstance(reference, str) or reference not in self.leases: raise NativeWindowError("NATIVE_LEASE_NOT_OWNED")
        owned = self.leases[reference]; process = self.api.process(owned.process_handle)
        if process["pid"] != owned.pid or process["creation"] != owned.creation or process["exit_code"] != STILL_ACTIVE:
            raise NativeWindowError("NATIVE_PROCESS_IDENTITY_CHANGED")
        if owned.hwnd:
            window = self.api.window(owned.hwnd)
            if (window["pid"] != owned.pid or window["thread"] != owned.window_thread or window["class"] != owned.window_class):
                raise NativeWindowError("NATIVE_WINDOW_IDENTITY_CHANGED")
            if int(self.api.user.GetPropW(owned.hwnd, owned.property_name) or 0) != owned.property_value:
                raise NativeWindowError("NATIVE_WINDOW_LIFETIME_CHANGED")
        elif require_window: raise NativeWindowUnavailable("NATIVE_WINDOW_NOT_ESTABLISHED")
        return owned

    @serialized
    def probe(self, reference):
        owned = self._owned(reference)
        if owned.hwnd is None:
            candidates = self.api.candidates(owned.pid)
            if len(candidates) != 1: raise NativeWindowUnavailable("NATIVE_WINDOW_NOT_UNIQUE")
            owned.hwnd, window = candidates[0]
            owned.window_thread, owned.window_class = window["thread"], window["class"]
            owned.property_name = "EEAS.NativeView." + uuid.uuid4().hex
            owned.property_value = (uuid.uuid4().int & ((1 << (ct.sizeof(HANDLE)*8-1))-1)) or 1
            try:
                self.api.require(self.api.user.SetPropW(owned.hwnd, owned.property_name, owned.property_value), "NATIVE_WINDOW_LIFETIME_MARK_FAILED")
            except BaseException:
                owned.hwnd = None; raise
        window = self.api.window(owned.hwnd)
        return {"status": "OWNED_WINDOW_CANDIDATE", "app_path": owned.image_path, "window_class": owned.window_class,
            "dpi": window["dpi"], "qualification": "WINDOWS_APP_HOST_NOT_VALIDATED",
            "document_binding": "LAUNCH_ARGUMENT_ONLY_UNCONFIRMED_ACTIVE_DOCUMENT"}

    def _surface(self, surface):
        if not isinstance(surface, dict) or set(surface) != {"hwnd", "pid", "creation", "bounds"}:
            raise NativeWindowError("TRUSTED_MAIN_PROCESS_SURFACE_REQUIRED")
        if any(type(surface[k]) is not int or surface[k] <= 0 for k in ("hwnd", "pid", "creation")):
            raise NativeWindowError("TRUSTED_MAIN_PROCESS_SURFACE_REQUIRED")
        window = self.api.window(surface["hwnd"])
        if window["pid"] != surface["pid"]: raise NativeWindowError("NATIVE_SURFACE_OWNER_CHANGED")
        process_handle = self.api.kernel.OpenProcess(0x1000, False, surface["pid"])
        self.api.require(process_handle, "NATIVE_SURFACE_PROCESS_UNAVAILABLE")
        try:
            process = self.api.process(process_handle)
            if process["creation"] != surface["creation"] or process["exit_code"] != STILL_ACTIVE:
                raise NativeWindowError("NATIVE_SURFACE_PROCESS_CHANGED")
        finally: self.api.kernel.CloseHandle(process_handle)
        bounds = physical_bounds(surface["bounds"])
        client = RECT(); self.api.require(self.api.user.GetClientRect(surface["hwnd"], ct.byref(client)), "NATIVE_SURFACE_BOUNDS_UNAVAILABLE")
        if bounds["x"] < 0 or bounds["y"] < 0 or bounds["x"] + bounds["width"] > client.right or bounds["y"] + bounds["height"] > client.bottom:
            raise NativeWindowError("NATIVE_SURFACE_BOUNDS_ESCAPE")
        return window, process, bounds

    @serialized
    def attach(self, reference, surface):
        self.probe(reference); owned = self._owned(reference, require_window=True)
        parent, parent_process, bounds = self._surface(surface)
        child = self.api.window(owned.hwnd); child_process = self.api.process(owned.process_handle)
        if parent_process["session"] != child_process["session"] or not self.api.user.AreDpiAwarenessContextsEqual(parent["dpi_context"], child["dpi_context"]):
            raise NativeWindowUnavailable("NATIVE_SESSION_OR_DPI_MISMATCH")
        if owned.attached_parent and owned.attached_parent != surface["hwnd"]:
            raise NativeWindowError("NATIVE_WINDOW_ALREADY_HOSTED")
        if owned.attached_parent: return self.resize(reference, surface)
        owned.original_parent = int(self.api.user.GetParent(owned.hwnd) or 0)
        owned.original_style = int(self.api.get_style(owned.hwnd, GWL_STYLE)) & 0xffffffff
        placement = WINDOWPLACEMENT(); placement.length = ct.sizeof(placement)
        self.api.require(self.api.user.GetWindowPlacement(owned.hwnd, ct.byref(placement)), "NATIVE_PLACEMENT_UNAVAILABLE")
        owned.original_placement = bytes(placement)
        try:
            style = (owned.original_style & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME)) | WS_CHILD
            self.api.pointer_call(self.api.set_style, owned.hwnd, GWL_STYLE, style)
            self.api.pointer_call(self.api.user.SetParent, owned.hwnd, surface["hwnd"])
            owned.attached_parent = surface["hwnd"]
            self.resize(reference, surface)
            state = ct.c_size_t()
            # Clear hidden accelerator/focus-cue state, bounded to avoid a UI thread hang.
            self.api.user.SendMessageTimeoutW(owned.hwnd, 0x127, 2 | (3 << 16), 0, 2, 250, ct.byref(state))
        except BaseException:
            self._restore(owned); raise
        return {"status": "NATIVE_ATTACHED_UNQUALIFIED", "mode": "NATIVE_WINDOW", "qualification": "WINDOWS_APP_HOST_NOT_VALIDATED"}

    @serialized
    def resize(self, reference, surface):
        owned = self._owned(reference, require_window=True); _parent, _process, bounds = self._surface(surface)
        if owned.attached_parent != surface["hwnd"] or int(self.api.user.GetParent(owned.hwnd) or 0) != owned.attached_parent:
            raise NativeWindowError("NATIVE_PARENT_CHANGED")
        self.api.require(self.api.user.SetWindowPos(owned.hwnd, None, bounds["x"], bounds["y"], bounds["width"], bounds["height"],
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED), "NATIVE_RESIZE_FAILED")
        return {"status": "NATIVE_ATTACHED_UNQUALIFIED", "bounds": bounds, "qualification": "WINDOWS_APP_HOST_NOT_VALIDATED"}

    @serialized
    def focus(self, reference):
        owned = self._owned(reference, require_window=True)
        accepted = bool(self.api.user.SetForegroundWindow(owned.hwnd))
        # Windows may deny focus; do not synthesize global keys or steal another window.
        return {"status": "FOCUS_REQUESTED" if accepted else "FOCUS_DENIED", "focus_delivery": "NOT_VERIFIED"}

    def _restore(self, owned):
        if owned.original_style is None: return
        self.api.pointer_call(self.api.user.SetParent, owned.hwnd, owned.original_parent or None)
        self.api.pointer_call(self.api.set_style, owned.hwnd, GWL_STYLE, owned.original_style)
        if owned.original_placement:
            placement = WINDOWPLACEMENT.from_buffer_copy(owned.original_placement)
            self.api.require(self.api.user.SetWindowPlacement(owned.hwnd, ct.byref(placement)), "NATIVE_RESTORE_PLACEMENT_FAILED")
        self.api.require(self.api.user.SetWindowPos(owned.hwnd, None, 0, 0, 0, 0,
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | 1 | 2), "NATIVE_RESTORE_STYLE_FAILED")
        owned.attached_parent = None; owned.original_style = None; owned.original_placement = None

    @serialized
    def detach(self, reference):
        owned = self._owned(reference, require_window=True); self._restore(owned)
        return {"status": "DETACHED", "app_terminated": False}

    @serialized
    def release(self, reference):
        owned = self.leases.get(reference)
        if owned is None: return
        try:
            self._owned(reference, require_window=bool(owned.hwnd))
        except NativeWindowError as error:
            if str(error) not in {"NATIVE_PROCESS_IDENTITY_CHANGED", "NATIVE_WINDOW_LOST",
                    "NATIVE_WINDOW_IDENTITY_CHANGED", "NATIVE_WINDOW_LIFETIME_CHANGED"}:
                raise
            # A lost/reused target must never be restored or have its properties
            # removed. Only close the process handle minted by our own launch.
            self.api.kernel.CloseHandle(owned.process_handle)
            self.leases.pop(reference, None)
            return
        if owned.original_style is not None:
            # Preserve the lease if a live own window could not be restored.
            # A caller can retry rather than abandoning a still-hosted editor.
            self.detach(reference)
        if owned.hwnd:
            self._owned(reference, require_window=True)
            self.api.user.RemovePropW(owned.hwnd, owned.property_name)
        self.api.kernel.CloseHandle(owned.process_handle); self.leases.pop(reference, None)

    @serialized
    def snapshot(self, reference, *, timeout_seconds=5, allow_printwindow_fallback=False):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 15 or type(allow_printwindow_fallback) is not bool:
            raise NativeWindowError("NATIVE_CAPTURE_BUDGET_INVALID")
        owned = self._owned(reference, require_window=True)
        if self.api.window(owned.hwnd)["minimized"]: raise NativeWindowUnavailable("NATIVE_WINDOW_MINIMIZED")
        target = {"hwnd": owned.hwnd, "pid": owned.pid, "creation": owned.creation,
            "thread": owned.window_thread, "class": owned.window_class,
            "property_name": owned.property_name, "property_value": owned.property_value}
        result = bounded_capture(target, timeout_seconds, allow_printwindow_fallback)
        self._owned(reference, require_window=True)
        return result


def _capture_identity(api, target):
    window = api.window(target["hwnd"])
    if window["pid"] != target["pid"] or window["thread"] != target["thread"] or window["class"] != target["class"]:
        raise NativeWindowError("NATIVE_CAPTURE_WINDOW_CHANGED")
    if int(api.user.GetPropW(target["hwnd"], target["property_name"]) or 0) != target["property_value"]:
        raise NativeWindowError("NATIVE_CAPTURE_WINDOW_LIFETIME_CHANGED")
    handle = api.require(api.kernel.OpenProcess(0x1000, False, target["pid"]), "NATIVE_CAPTURE_PROCESS_UNAVAILABLE")
    try:
        process = api.process(handle)
        if process["creation"] != target["creation"] or process["exit_code"] != STILL_ACTIVE:
            raise NativeWindowError("NATIVE_CAPTURE_PROCESS_CHANGED")
    finally: api.kernel.CloseHandle(handle)


def _wgc_frame(target, timeout):
    try:
        from windows_capture import WindowsCapture
    except ImportError: raise NativeWindowUnavailable("WGC_EXACT_HWND_PACKAGE_UNAVAILABLE") from None
    if "window_hwnd" not in inspect.signature(WindowsCapture).parameters:
        raise NativeWindowUnavailable("WGC_EXACT_HWND_VERSION_UNSUPPORTED")
    try: package_version = importlib.metadata.version("windows-capture")
    except importlib.metadata.PackageNotFoundError:
        raise NativeWindowUnavailable("WGC_PACKAGE_METADATA_UNAVAILABLE") from None
    if package_version != SUPPORTED_WGC_VERSION:
        raise NativeWindowUnavailable("WGC_EXACT_HWND_VERSION_UNSUPPORTED")
    capture = WindowsCapture(window_hwnd=target["hwnd"], window_name=None, monitor_index=None,
        # Keep the OS default border. Even explicitly requesting True calls
        # the newer border-toggle API and fails on supported Windows 10 hosts.
        cursor_capture=False, draw_border=None)
    arrived = threading.Event(); values = {}
    @capture.event
    def on_frame_arrived(frame, control):
        try:
            if frame.width * frame.height > MAX_FRAME_PIXELS: raise NativeWindowError("NATIVE_FRAME_SIZE_INVALID")
            values["png"] = frame_png(Image.frombytes("RGBA", (frame.width, frame.height), frame.frame_buffer.tobytes(), "raw", "BGRA"))
            values["frame_timespan"] = frame.timespan
        except Exception as error: values["error"] = str(error) if isinstance(error, NativeWindowError) else type(error).__name__
        finally: control.stop(); arrived.set()
    @capture.event
    def on_closed():
        if not arrived.is_set(): values["error"] = "NATIVE_CAPTURE_CLOSED"; arrived.set()
    control = capture.start_free_threaded()
    try:
        if not arrived.wait(timeout): raise NativeWindowUnavailable("WGC_FRAME_TIMEOUT")
        if "error" in values: raise NativeWindowUnavailable(values["error"])
        return {**values, "backend": "WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND", "package_version": package_version}
    finally: control.stop()


def _print_frame(api, target):
    rect = RECT(); api.require(api.user.GetWindowRect(target["hwnd"], ct.byref(rect)), "NATIVE_FRAME_RECT_UNAVAILABLE")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if not 0 < width * height <= MAX_FRAME_PIXELS or width <= 0 or height <= 0: raise NativeWindowError("NATIVE_FRAME_SIZE_INVALID")
    dc = api.require(api.user.GetWindowDC(target["hwnd"]), "NATIVE_FRAME_DC_UNAVAILABLE")
    memory, bitmap, previous = None, None, None
    try:
        memory = api.require(api.gdi.CreateCompatibleDC(dc), "NATIVE_FRAME_MEMORY_DC_FAILED")
        bitmap = api.require(api.gdi.CreateCompatibleBitmap(dc, width, height), "NATIVE_FRAME_BITMAP_FAILED")
        previous = api.require(api.gdi.SelectObject(memory, bitmap), "NATIVE_FRAME_BITMAP_SELECT_FAILED")
        api.require(api.user.PrintWindow(target["hwnd"], memory, 0), "NATIVE_PRINT_WINDOW_FAILED")
        api.gdi.SelectObject(memory, previous); previous = None
        header = BITMAPINFOHEADER(); header.size = ct.sizeof(header); header.width = width; header.height = -height
        header.planes = 1; header.bit_count = 32
        pixels = ct.create_string_buffer(width * height * 4)
        copied = api.gdi.GetDIBits(memory, bitmap, 0, height, pixels, ct.byref(header), 0)
        if copied != height: raise NativeWindowError("NATIVE_FRAME_PIXELS_INCOMPLETE")
        image = Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
        return {"png": frame_png(image), "backend": "PRINTWINDOW_BOUNDED_FALLBACK", "capture_completeness": "UNVERIFIED"}
    finally:
        if previous and memory: api.gdi.SelectObject(memory, previous)
        if bitmap: api.gdi.DeleteObject(bitmap)
        if memory: api.gdi.DeleteDC(memory)
        api.user.ReleaseDC(target["hwnd"], dc)


def _capture_worker(connection, target, timeout, allow_fallback):
    try:
        api = Win32WindowApi(); _capture_identity(api, target)
        try: result = _wgc_frame(target, timeout)
        except NativeWindowUnavailable as error:
            if not allow_fallback: raise
            result = _print_frame(api, target)
            result["fallback_reason"] = str(error)
        _capture_identity(api, target); connection.send({"ok": True, **result})
    except Exception as error:
        connection.send({"ok": False, "error": str(error) if isinstance(error, NativeWindowError) else type(error).__name__})
    finally: connection.close()


def bounded_capture(target, timeout, allow_fallback):
    context = multiprocessing.get_context("spawn"); receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=_capture_worker, args=(sender, target, timeout, allow_fallback), daemon=True)
    worker.start(); sender.close()
    try:
        if not receiver.poll(timeout): raise NativeWindowUnavailable("NATIVE_CAPTURE_WORKER_TIMEOUT")
        try: result = receiver.recv()
        except EOFError: raise NativeWindowUnavailable("NATIVE_CAPTURE_WORKER_EXITED") from None
        if not isinstance(result, dict): raise NativeWindowError("NATIVE_FRAME_OUTPUT_INVALID")
        if not result.get("ok"): raise NativeWindowUnavailable(result.get("error", "NATIVE_CAPTURE_FAILED"))
        raw = result.get("png")
        if not isinstance(raw, bytes) or len(raw) > MAX_FRAME_BYTES: raise NativeWindowError("NATIVE_FRAME_OUTPUT_INVALID")
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG": raise NativeWindowError("NATIVE_FRAME_OUTPUT_INVALID")
            if image.width * image.height > MAX_FRAME_PIXELS: raise NativeWindowError("NATIVE_FRAME_SIZE_INVALID")
            image.load(); frame_png(image)
            result.update(width=image.width, height=image.height, sha256=hashlib.sha256(raw).hexdigest())
        return {key: value for key, value in result.items() if key != "ok"}
    finally:
        receiver.close(); worker.join(timeout=.25)
        if worker.is_alive():
            worker.terminate(); worker.join(timeout=2)
            if worker.is_alive(): worker.kill(); worker.join(timeout=2)
