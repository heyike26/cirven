"""Portable ABI/policy counters are not native Windows app qualification."""
import ctypes as ct
import hashlib
from io import BytesIO
import os
import sys
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from packages.adapters import windows_native_window_host as native


class Function:
    def __init__(self, effect=None, result=1):
        self.effect, self.result, self.calls = effect, result, []
    def __call__(self, *args):
        self.calls.append(args)
        return self.effect(*args) if self.effect else self.result


class Library:
    def __getattr__(self, name):
        value = Function(); setattr(self, name, value); return value


def libraries():
    return {name: Library() for name in ("user32", "kernel32", "shell32", "ole32", "gdi32")}


def image_png():
    image = Image.new("RGB", (32, 24), "white")
    for x in range(16):
        for y in range(24): image.putpixel((x, y), (30, 60, 90))
    return native.frame_png(image)


class PolicyApi:
    """Explicit fake HWNDs exercise guard decisions, not Windows rendering."""
    def __init__(self):
        self.records = {
            11: {"pid": 101, "creation": 1001, "exit_code": native.STILL_ACTIVE, "session": 1, "image_path": r"D:\Apps\Editor.exe"},
            22: {"pid": 202, "creation": 2002, "exit_code": native.STILL_ACTIVE, "session": 1, "image_path": r"D:\Apps\Electron.exe"},
        }
        self.windows = {111: {"pid": 101, "thread": 51, "class": "LabEditor", "visible": True, "minimized": False, "dpi_context": 3, "dpi": 144},
            222: {"pid": 202, "thread": 52, "class": "LabShell", "visible": True, "minimized": False, "dpi_context": 3, "dpi": 144}}
        self.props, self.parents = {}, {111: 0, 222: 0}
        self.styles = {111: native.WS_POPUP | native.WS_CAPTION | native.WS_THICKFRAME | 0x10000000}
        self.closed, self.moves, self.restored, self.opened = [], [], [], []
        self.unique, self.new_process, self.fail_restore = True, True, False
        self.kernel = SimpleNamespace(OpenProcess=Function(lambda _rights, _inherit, pid: {101: 11, 202: 22}[pid]),
            CloseHandle=Function(lambda handle: self.closed.append(handle) or 1))
        self.user = SimpleNamespace(
            SetPropW=Function(lambda hwnd, name, value: self.props.__setitem__((hwnd, name), value) or 1),
            GetPropW=Function(lambda hwnd, name: self.props.get((hwnd, name), 0)),
            RemovePropW=Function(lambda hwnd, name: self.props.pop((hwnd, name), 0)),
            GetParent=Function(lambda hwnd: self.parents.get(hwnd, 0)),
            SetParent=Function(self.set_parent),
            GetClientRect=Function(self.client_rect),
            GetWindowPlacement=Function(self.placement),
            SetWindowPlacement=Function(lambda hwnd, value: self.restored.append(bytes(value._obj)) or 1),
            SetWindowPos=Function(lambda *args: self.moves.append(args) or 1),
            AreDpiAwarenessContextsEqual=Function(lambda a, b: int(a == b)),
            SendMessageTimeoutW=Function(), SetForegroundWindow=Function(result=0))
        self.get_style = Function(lambda hwnd, _index: self.styles[hwnd])
        self.set_style = Function(lambda hwnd, _index, value: self.styles.__setitem__(hwnd, value) or 0)
    require = staticmethod(native.Win32WindowApi.require)
    pointer_call = staticmethod(lambda function, *args: function(*args))
    def set_parent(self, hwnd, parent):
        if self.fail_restore and not parent: raise native.NativeWindowError("WIN32_ERROR_5")
        previous = self.parents[hwnd]; self.parents[hwnd] = parent or 0; return previous
    def client_rect(self, hwnd, pointer):
        pointer._obj.right, pointer._obj.bottom = 1200, 900; return 1
    def placement(self, hwnd, pointer):
        pointer._obj.showCmd = 1; pointer._obj.rcNormalPosition.right = 800; return 1
    def process(self, handle): return dict(self.records[handle])
    def window(self, hwnd):
        if hwnd not in self.windows: raise native.NativeWindowError("NATIVE_WINDOW_LOST")
        return dict(self.windows[hwnd])
    def shell_open(self, path):
        self.opened.append(path)
        return native._OwnedWindow(11, 101, 1001, self.records[11]["image_path"]) if self.new_process else None
    def candidates(self, pid):
        values = [(111, self.window(111))]
        return values if self.unique else values * 2


@pytest.fixture
def policy():
    api = PolicyApi(); host = native.WindowsNativeWindowHost(api=api)
    reference = host.launch(r"D:\Selected\real.docx")["lease_ref"]
    surface = {"hwnd": 222, "pid": 202, "creation": 2002, "bounds": {"x": 50, "y": 80, "width": 600, "height": 400}}
    return api, host, reference, surface


def test_fixed_win32_widths_and_declared_pointer_abi():
    libs = libraries(); api = native.Win32WindowApi(libraries=libs)
    assert ct.sizeof(native.DWORD) == ct.sizeof(native.LONG) == ct.sizeof(native.BOOL) == 4
    assert ct.sizeof(native.WINDOWPLACEMENT) == 44
    assert ct.sizeof(native.BITMAPINFOHEADER) == 40
    assert ct.sizeof(native.SHELLEXECUTEINFOW) == (112 if ct.sizeof(native.HANDLE) == 8 else 60)
    assert libs["user32"].SetParent.restype is ct.c_void_p
    assert libs["user32"].SetPropW.argtypes == [native.HANDLE, ct.c_wchar_p, native.HANDLE]
    assert api.set_style.restype is native.LONG_PTR
    assert libs["kernel32"].GetProcessTimes.argtypes[0] is native.HANDLE
    assert libs["shell32"].ShellExecuteExW.argtypes == [ct.POINTER(native.SHELLEXECUTEINFOW)]


def test_missing_required_os_api_reports_explicit_unavailable():
    with pytest.raises(native.NativeWindowUnavailable, match="API_UNAVAILABLE_GetDpiForWindow"):
        native.Win32WindowApi._bind(SimpleNamespace(), "GetDpiForWindow", native.DWORD, [native.HANDLE])


@pytest.mark.parametrize("path", ["https://site/doc", r"\\server\share\a.docx", r"\\?\D:\a.docx", r"D:\a.docx:stream", r"D:\..\a.docx", 'D:\\bad".docx', r"D:relative.docx"])
def test_native_path_rejects_nonlocal_shell_arguments(path):
    with pytest.raises(native.NativeWindowError, match="SELECTED_LOCAL_FILE"): native.native_document_path(path)


@pytest.mark.parametrize("change", [{"width": True}, {"x": -1, "width": 0}, {"height": 32768, "width": 32768}, {"extra": 1}])
def test_physical_bounds_rejects_invalid_inputs(change):
    value = {"x": 0, "y": 0, "width": 100, "height": 100}; value.update(change)
    with pytest.raises(native.NativeWindowError): native.physical_bounds(value)


def test_shell_association_passes_only_exact_file_and_never_adopts_existing_process():
    libs = libraries(); api = native.Win32WindowApi(libraries=libs)
    libs["ole32"].CoInitializeEx.result = 0
    api.timestamp = lambda: 500
    seen = []
    def execute(pointer):
        info = pointer._obj
        seen.append((info.lpFile, info.lpVerb, info.lpParameters, info.fMask, info.nShow))
        info.hProcess = 11; return 1
    libs["shell32"].ShellExecuteExW.effect = execute
    api.process = lambda _: {"pid": 101, "creation": 499, "exit_code": native.STILL_ACTIVE, "session": 1, "image_path": "Editor.exe"}
    assert api.shell_open(r"D:\Selected\真实 资料.docx") is None
    assert seen == [(r"D:\Selected\真实 资料.docx", "open", None, 0x540, 4)]
    assert libs["kernel32"].CloseHandle.calls == [(11,)]
    assert len(libs["ole32"].CoUninitialize.calls) == 1
    libs["shell32"].ShellExecuteExW.effect = lambda pointer: 1
    assert api.shell_open(r"D:\Selected\real.docx") is None


def test_pointer_call_distinguishes_zero_success_from_access_denied(monkeypatch):
    error = [999]
    monkeypatch.setattr(ct, "set_last_error", lambda value: error.__setitem__(0, value), raising=False)
    monkeypatch.setattr(ct, "get_last_error", lambda: error[0], raising=False)
    assert native.Win32WindowApi.pointer_call(lambda: 0) == 0
    def denied(): error[0] = 5; return 0
    with pytest.raises(native.NativeWindowError, match="WIN32_ERROR_5"):
        native.Win32WindowApi.pointer_call(denied)


def test_no_new_owned_process_remains_separate_and_opaque(policy):
    api, host, ref, _ = policy
    api.new_process = False
    assert host.launch(r"D:\Selected\real.docx") == {"status": "OPENED_SEPARATELY", "reason": "NO_NEW_OWNED_PROCESS", "lease_ref": None}
    with pytest.raises(native.NativeWindowError, match="LEASE_NOT_OWNED"): host.probe("111")
    assert len(host.leases) == 1


def test_unique_window_required_before_any_mutation(policy):
    api, host, ref, surface = policy; api.unique = False
    with pytest.raises(native.NativeWindowUnavailable, match="WINDOW_NOT_UNIQUE"): host.attach(ref, surface)
    assert not api.user.SetParent.calls and not api.props


@pytest.mark.parametrize("drift", ["pid", "creation", "exit", "window_pid", "thread", "class", "marker"])
def test_process_and_window_lifetime_drift_denied_before_mutation(policy, drift):
    api, host, ref, surface = policy; host.probe(ref)
    if drift in {"pid", "creation"}: api.records[11][drift] += 1
    elif drift == "exit": api.records[11]["exit_code"] = 0
    elif drift == "window_pid": api.windows[111]["pid"] = 999
    elif drift in {"thread", "class"}: api.windows[111][drift] = "changed" if drift == "class" else 99
    else: api.props.clear()
    with pytest.raises(native.NativeWindowError): host.attach(ref, surface)
    assert not api.user.SetParent.calls


@pytest.mark.parametrize("drift", ["pid", "creation", "session", "dpi", "escape", "extra"])
def test_parent_surface_owner_dpi_and_bounds_checked_before_reparent(policy, drift):
    api, host, ref, surface = policy
    if drift == "pid": surface["pid"] = 404
    elif drift == "creation": surface["creation"] += 1
    elif drift == "session": api.records[22]["session"] = 2
    elif drift == "dpi": api.windows[222]["dpi_context"] = 4
    elif drift == "escape": surface["bounds"]["x"] = 1000
    else: surface["renderer_supplied_pid"] = 202
    with pytest.raises(native.NativeWindowError): host.attach(ref, surface)
    assert not api.user.SetParent.calls


def test_attach_resize_restores_style_parent_placement_and_closes_only_own_handle(policy):
    api, host, ref, surface = policy; original = api.styles[111]
    result = host.attach(ref, surface)
    assert result["mode"] == "NATIVE_WINDOW" and "UNQUALIFIED" in result["status"]
    assert api.parents[111] == 222 and api.styles[111] & native.WS_CHILD
    assert not api.styles[111] & (native.WS_POPUP | native.WS_CAPTION | native.WS_THICKFRAME)
    surface["bounds"]["width"] = 700
    assert host.resize(ref, surface)["bounds"]["width"] == 700
    assert host.focus(ref) == {"status": "FOCUS_DENIED", "focus_delivery": "NOT_VERIFIED"}
    assert host.detach(ref) == {"status": "DETACHED", "app_terminated": False}
    assert api.parents[111] == 0 and api.styles[111] == original and len(api.restored) == 1
    host.release(ref)
    assert ref not in host.leases and not api.props and api.closed.count(11) == 1
    assert not hasattr(api.kernel, "TerminateProcess")


def test_failed_restore_retains_live_lease_and_can_retry(policy):
    api, host, ref, surface = policy; host.attach(ref, surface); api.fail_restore = True
    with pytest.raises(native.NativeWindowError, match="WIN32_ERROR_5"): host.release(ref)
    assert ref in host.leases and 11 not in api.closed and api.parents[111] == 222
    api.fail_restore = False; host.release(ref)
    assert ref not in host.leases and api.parents[111] == 0 and api.closed.count(11) == 1


@pytest.mark.parametrize("lost", ["window", "process", "marker"])
def test_lost_or_reused_window_release_never_touches_foreign_window(policy, lost):
    api, host, ref, surface = policy; host.attach(ref, surface)
    previous = len(api.user.SetParent.calls)
    if lost == "window": del api.windows[111]
    elif lost == "process": api.records[11]["exit_code"] = 0
    else: api.props.clear()
    host.release(ref)
    assert len(api.user.SetParent.calls) == previous and not api.user.RemovePropW.calls
    assert ref not in host.leases and api.closed.count(11) == 1


def test_failed_attach_style_change_is_restored_even_without_attached_flag(policy):
    api, host, ref, surface = policy; original = api.styles[111]
    api.user.SetParent.effect = lambda *_args: (_ for _ in ()).throw(native.NativeWindowError("WIN32_ERROR_5"))
    with pytest.raises(native.NativeWindowError): host.attach(ref, surface)
    assert host.leases[ref].original_style == original
    api.user.SetParent.effect = api.set_parent
    host.release(ref)
    assert api.styles[111] == original and ref not in host.leases


def test_snapshot_requires_marked_owned_nonminimized_window_and_exact_target(monkeypatch, policy):
    api, host, ref, _surface = policy
    with pytest.raises(native.NativeWindowUnavailable, match="WINDOW_NOT_ESTABLISHED"): host.snapshot(ref)
    host.probe(ref); api.windows[111]["minimized"] = True
    with pytest.raises(native.NativeWindowUnavailable, match="WINDOW_MINIMIZED"): host.snapshot(ref)
    api.windows[111]["minimized"] = False; seen = []
    def capture(target, timeout, fallback):
        seen.append((dict(target), timeout, fallback)); return {"png": image_png(), "width": 32, "height": 24, "backend": "POLICY_FIXTURE"}
    monkeypatch.setattr(native, "bounded_capture", capture)
    assert host.snapshot(ref)["backend"] == "POLICY_FIXTURE"
    assert seen[0][0]["hwnd"] == 111 and seen[0][0]["creation"] == 1001
    assert seen[0][0]["property_name"].startswith("EEAS.NativeView.") and seen[0][1:] == (5, False)
    with pytest.raises(native.NativeWindowError, match="BUDGET_INVALID"): host.snapshot(ref, timeout_seconds=True)


def test_blank_frame_refused_while_nonblank_png_has_factual_dimensions():
    with pytest.raises(native.NativeWindowUnavailable, match="FRAME_BLANK"):
        native.frame_png(Image.new("RGB", (20, 20), "black"))
    with Image.open(BytesIO(image_png())) as image: assert image.size == (32, 24)


def test_wgc_sdk_seam_passes_exact_hwnd_and_keeps_windows10_default_border(monkeypatch):
    calls = []; controls = []
    class Capture:
        def __init__(self, *, window_hwnd, window_name, monitor_index, cursor_capture, draw_border):
            if draw_border is not None:
                raise RuntimeError('Win10 does not support toggling the capture border')
            calls.append((window_hwnd, window_name, monitor_index, cursor_capture, draw_border)); self.handlers = {}
        def event(self, function): self.handlers[function.__name__] = function; return function
        def start_free_threaded(self):
            control = SimpleNamespace(stop=lambda: controls.append("stop"))
            with Image.open(BytesIO(image_png())) as image: raw = image.convert("RGBA").tobytes("raw", "BGRA")
            frame = SimpleNamespace(width=32, height=24, timespan=1234, frame_buffer=SimpleNamespace(tobytes=lambda: raw))
            self.handlers["on_frame_arrived"](frame, control); return control
    monkeypatch.setitem(sys.modules, "windows_capture", SimpleNamespace(WindowsCapture=Capture))
    monkeypatch.setattr(native.importlib.metadata, "version", lambda _: "2.0.1")
    result = native._wgc_frame({"hwnd": 111}, 1)
    assert calls == [(111, None, None, False, None)] and len(controls) == 2
    assert result["frame_timespan"] == 1234 and result["package_version"] == "2.0.1"
    assert result["backend"] == "WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND"


def test_wgc_missing_or_old_sdk_refuses_instead_of_screen_capture(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_capture", None)
    with pytest.raises(native.NativeWindowUnavailable, match="PACKAGE_UNAVAILABLE"): native._wgc_frame({"hwnd": 111}, 1)
    monkeypatch.setitem(sys.modules, "windows_capture", SimpleNamespace(WindowsCapture=lambda window_name=None: None))
    with pytest.raises(native.NativeWindowUnavailable, match="VERSION_UNSUPPORTED"): native._wgc_frame({"hwnd": 111}, 1)


def test_unfrozen_wgc_version_is_not_silently_admitted(monkeypatch):
    def constructor(*, window_hwnd): raise AssertionError("Unsupported package must not create a capture")
    monkeypatch.setitem(sys.modules, "windows_capture", SimpleNamespace(WindowsCapture=constructor))
    monkeypatch.setattr(native.importlib.metadata, "version", lambda _: "999.0")
    with pytest.raises(native.NativeWindowUnavailable, match="VERSION_UNSUPPORTED"): native._wgc_frame({"hwnd": 111}, 1)


def test_printwindow_abi_seam_uses_only_selected_hwnd_and_releases_gdi(policy):
    api, host, ref, _surface = policy; host.probe(ref)
    def rectangle(hwnd, pointer): pointer._obj.right = 32; pointer._obj.bottom = 24; return 1
    api.user.GetWindowRect = Function(rectangle)
    api.user.GetWindowDC = Function(result=500)
    api.user.PrintWindow = Function()
    api.user.ReleaseDC = Function()
    def copy_bits(dc, bitmap, start, lines, pixels, header, mode):
        with Image.open(BytesIO(image_png())) as image: pixels.raw = image.convert("RGB").tobytes("raw", "BGRX")
        assert header._obj.height == -24 and header._obj.bit_count == 32
        return lines
    api.gdi = SimpleNamespace(CreateCompatibleDC=Function(result=501), CreateCompatibleBitmap=Function(result=502),
        SelectObject=Function(result=503), DeleteObject=Function(), DeleteDC=Function(), GetDIBits=Function(copy_bits))
    result = native._print_frame(api, {"hwnd": 111})
    assert api.user.GetWindowDC.calls == [(111,)] and api.user.PrintWindow.calls == [(111, 501, 0)]
    assert api.user.ReleaseDC.calls == [(111, 500)] and api.gdi.DeleteObject.calls == [(502,)] and api.gdi.DeleteDC.calls == [(501,)]
    assert result["backend"] == "PRINTWINDOW_BOUNDED_FALLBACK" and result["capture_completeness"] == "UNVERIFIED"


def _hung_worker(connection, target, timeout, allow_fallback):
    time.sleep(60)


def _fixture_worker(connection, target, timeout, allow_fallback):
    connection.send({"ok": True, "png": image_png(), "backend": "SPAWNED_TEST_FIXTURE"}); connection.close()


def _exited_worker(connection, target, timeout, allow_fallback):
    connection.close()


def test_bounded_capture_real_own_worker_timeout_is_joined(monkeypatch):
    import multiprocessing
    before = {process.pid for process in multiprocessing.active_children()}
    monkeypatch.setattr(native, "_capture_worker", _hung_worker)
    start = time.monotonic()
    with pytest.raises(native.NativeWindowUnavailable, match="WORKER_TIMEOUT"):
        native.bounded_capture({}, 1, False)
    assert time.monotonic() - start < 6
    assert {process.pid for process in multiprocessing.active_children()} <= before


def test_bounded_capture_real_own_worker_png_and_abrupt_exit(monkeypatch):
    monkeypatch.setattr(native, "_capture_worker", _fixture_worker)
    result = native.bounded_capture({}, 5, False)
    assert result["sha256"] == hashlib.sha256(result["png"]).hexdigest()
    assert (result["width"], result["height"]) == (32, 24)
    monkeypatch.setattr(native, "_capture_worker", _exited_worker)
    with pytest.raises(native.NativeWindowUnavailable, match="WORKER_EXITED"):
        native.bounded_capture({}, 5, False)


@pytest.mark.skipif(os.name != "nt" or os.environ.get("EEAS_NATIVE_WINDOW_HOST_TEST") != "1",
    reason="Requires explicit native Windows GUI lab qualification; portable fixtures are insufficient")
def test_opt_in_actual_windows_selected_document_frame_and_release():
    path = os.environ.get("EEAS_NATIVE_WINDOW_HOST_LAB_SOURCE")
    if not path: pytest.fail("An explicit existing selected lab document path is required")
    host = native.WindowsNativeWindowHost(); opened = host.launch(path)
    if not opened.get("lease_ref"): pytest.skip("Association reused existing process: attach and snapshot intentionally refused")
    ref = opened["lease_ref"]
    try:
        deadline = time.monotonic() + 30
        while True:
            try: probed = host.probe(ref); break
            except native.NativeWindowUnavailable:
                if time.monotonic() >= deadline: raise
                time.sleep(.2)
        assert probed["document_binding"] == "LAUNCH_ARGUMENT_ONLY_UNCONFIRMED_ACTIVE_DOCUMENT"
        captured = host.snapshot(ref, timeout_seconds=10)
        assert captured["backend"] == "WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND"
        assert captured["sha256"] == hashlib.sha256(captured["png"]).hexdigest()
    finally: host.release(ref)
