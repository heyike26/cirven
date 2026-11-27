"""Actual authority/API with controlled Win32 calls: UNIT, not native QA."""
import copy
import ctypes as ct
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.native_runtime import NativeCollaborationRuntime, register_native_collaboration_routes
from packages.adapters.owned_window_geometry import Win32OwnedWindowGeometry, COORDINATES
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict
from test_native_collaboration import HostLifecycleCounter, TOKEN, BIND


def check(condition):
    # These acceptance conditions remain active under python -O.
    if not condition:
        raise AssertionError('Owned geometry acceptance condition failed')


class Function:
    def __init__(self, call): self.call = call
    def __call__(self, *args): return self.call(*args)


class NativeBoundary:
    def __init__(self, *, dwm=True):
        self.calls, self.contexts, self.window_reads = [], [], 0
        self.fail = self.restore_fail = self.drift = False
        self.hwnds = []
        for name in ('SetThreadDpiAwarenessContext', 'GetWindowRect', 'GetClientRect',
                     'ClientToScreen', 'GetDpiForWindow', 'IsIconic', 'IsZoomed'):
            setattr(self, name, Function(getattr(self, '_' + name)))
        self.DwmGetWindowAttribute = Function(self._DwmGetWindowAttribute)
        self.reader = Win32OwnedWindowGeometry(libraries={'user32': self, **({'dwmapi': self} if dwm else {})})

    def _SetThreadDpiAwarenessContext(self, value):
        self.contexts.append(value.value if isinstance(value, ct.c_void_p) else value)
        return 0 if self.restore_fail and len(self.contexts) % 2 == 0 else 99

    def own(self, hwnd):
        self.hwnds.append(hwnd)
        check(hwnd == 77)

    def _GetWindowRect(self, hwnd, out):
        self.own(hwnd); self.window_reads += 1
        value = out._obj
        value.left, value.top, value.right, value.bottom = -1200, 52, -305, 1340
        if self.drift and self.window_reads % 2 == 0: value.right += 1
        return 0 if self.fail else 1

    def _GetClientRect(self, hwnd, out):
        self.own(hwnd)
        value = out._obj
        value.left, value.top, value.right, value.bottom = 0, 0, 870, 1240
        return 1

    def _ClientToScreen(self, hwnd, out):
        self.own(hwnd); out._obj.x, out._obj.y = -1190, 84; return 1

    def _GetDpiForWindow(self, hwnd): self.own(hwnd); return 144
    def _IsIconic(self, hwnd): self.own(hwnd); return 0
    def _IsZoomed(self, hwnd): self.own(hwnd); return 0

    def _DwmGetWindowAttribute(self, hwnd, attribute, out, size):
        self.own(hwnd); check(attribute == 9 and size == 16)
        out._obj.left, out._obj.top, out._obj.right, out._obj.bottom = -1195, 52, -310, 1335
        return 0


def authority(tmp_path, boundary):
    native = HostLifecycleCounter()
    value = NativeCollaborationAuthority(tmp_path, token=TOKEN, owner_pid=123,
        native=native, platform='POLICY_FAULT_PROBE')
    value._own_geometry_reader = boundary.reader
    return value, native


def test_reads_whole_window_client_dwm_in_physical_thread_context_without_using_visible_extent():
    native = NativeBoundary()
    marker = {'window_id': 77}
    validations = []
    def validate(value):
        validations.append(copy.deepcopy(value)); return {'pid': 123, 'birth': 456}
    value = native.reader.read(marker, validate)
    check(value['coordinate_space'] == COORDINATES)
    check(value['window_bounds_physical'] == {'x': -1200, 'y': 52, 'width': 895, 'height': 1288})
    check(value['client_bounds_physical'] == {'x': -1190, 'y': 84, 'width': 870, 'height': 1240})
    check(value['dwm_visible_bounds_physical']['width'] == 885)
    check(value['dwm_status'] == 'AVAILABLE' and value['dpi'] == 144)
    check(len(validations) == 2 and native.contexts == [ct.c_void_p(-4).value, 99])
    check(not value['maximized'] and not value['minimized'])


def test_unavailable_dwm_is_explicit_and_does_not_replace_actual_window_geometry():
    native = NativeBoundary(dwm=False)
    value = native.reader.read({'window_id': 77}, lambda _: {'owner': 1})
    check(value['dwm_status'] == 'UNAVAILABLE' and value['dwm_visible_bounds_physical'] is None)
    check(value['window_bounds_physical']['width'] == 895)


@pytest.mark.parametrize('failure', ['fail', 'drift', 'restore_fail'])
def test_geometry_failure_or_midread_resize_restores_thread_and_never_returns_stale_extent(failure):
    native = NativeBoundary(); setattr(native, failure, True)
    with pytest.raises(Conflict):
        native.reader.read({'window_id': 77}, lambda _: {'owner': 1})
    check(native.contexts == [ct.c_void_p(-4).value, 99])


def test_native_owner_change_during_read_is_rejected_after_restoring_context():
    native = NativeBoundary(); count = 0
    def validate(_):
        nonlocal count
        count += 1
        if count == 2: raise Conflict('reused handle')
        return {'birth': 1}
    with pytest.raises(Conflict, match='reused handle'):
        native.reader.read({'window_id': 77}, validate)
    check(native.contexts[-1] == 99)


def test_authority_reads_only_bound_main_without_disk_writes_and_rejects_reuse_close_restart(tmp_path):
    boundary = NativeBoundary(); value, native = authority(tmp_path, boundary)
    with pytest.raises(Conflict): value.own_geometry()
    value.bind({**BIND, 'owned_windows': {'orb': {'hwnd_decimal': '78', 'owner_pid': 123}}})
    before = {p: p.read_bytes() for p in Path(tmp_path).rglob('*') if p.is_file()}
    first = value.own_geometry(); second = value.own_geometry()
    check(first == second and first['status'] == 'OWN_MAIN_GEOMETRY')
    check(set(boundary.hwnds) == {77} and len(first['geometry_sha256']) == 64)
    check(before == {p: p.read_bytes() for p in Path(tmp_path).rglob('*') if p.is_file()})
    native.markers[78]['marker_value'] += 1
    reads = boundary.window_reads
    with pytest.raises(Conflict): value.own_geometry()
    check(boundary.window_reads == reads)
    native.markers[78]['marker_value'] -= 1
    value.close()
    with pytest.raises(Conflict): value.own_geometry()
    fresh, _ = authority(tmp_path, boundary)
    with pytest.raises(Conflict): fresh.own_geometry()
    check(boundary.window_reads == reads)


def test_actual_private_http_route_requires_host_and_cannot_select_arbitrary_hwnd(tmp_path):
    boundary = NativeBoundary(); value, _ = authority(tmp_path, boundary)
    runtime = NativeCollaborationRuntime(tmp_path, authority=value, platform='POLICY_FAULT_PROBE')
    app = FastAPI()
    @app.exception_handler(Conflict)
    async def conflict(_request, error):
        return JSONResponse({'error': str(error)}, status_code=409)
    register_native_collaboration_routes(app, None, tmp_path, service=runtime)
    path = '/api/desktop-host/collaboration/own-main-geometry'
    with TestClient(app) as client:
        for headers in ({}, {'X-EEAS-Desktop-Host': 'f' * 64}):
            check(client.get(path, headers=headers).status_code == 409)
        check(boundary.window_reads == 0)
        headers = {'X-EEAS-Desktop-Host': TOKEN}
        check(client.get(path, headers=headers).status_code == 409)
        value.bind(BIND)
        response = client.get(path + '?hwnd_decimal=999', headers=headers)
        check(response.status_code == 200 and response.json()['status'] == 'OWN_MAIN_GEOMETRY')
        check(set(boundary.hwnds) == {77})
        check(client.post(path, json={'hwnd_decimal': '999'}, headers=headers).status_code == 405)
        value.close()
        check(client.get(path, headers=headers).status_code == 409)
