"""Read-only proof of an authority-owned Electron window's native shape.

The host sets its own region/content protection. This module never moves,
disables, captures, reparents or destroys a user's application window.
Coordinates are physical pixels relative to the *window*, not CSS/client DIP.
"""
from __future__ import annotations
import ctypes as ct
from contextlib import contextmanager
import hashlib
import json
import os
import re
import threading
import uuid


class DesktopRegionError(ValueError):
    pass


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def surface_reference(value):
    if (not isinstance(value, dict) or set(value) != {'id', 'revision', 'sha256'}
            or not isinstance(value['id'], str) or not value['id'] or len(value['id']) > 200
            or type(value['revision']) is not int or value['revision'] < 1
            or not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256'])):
        raise DesktopRegionError('EXACT_SURFACE_REFERENCE_REQUIRED')
    return dict(value)


def rectangles(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 256:
        # [] makes Electron restore a full rectangle; it is not an empty region.
        raise DesktopRegionError('EXPLICIT_NONEMPTY_PHYSICAL_REGION_REQUIRED')
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'x', 'y', 'width', 'height'}:
            raise DesktopRegionError('PHYSICAL_RECTANGLE_REQUIRED')
        if (any(type(v) is not int for v in item.values()) or item['x'] < 0 or item['y'] < 0
                or min(item['width'], item['height']) < 1
                or item['x'] + item['width'] > 65536 or item['y'] + item['height'] > 65536):
            raise DesktopRegionError('PHYSICAL_RECTANGLE_INVALID')
        result.append(dict(item))
    return result


class _POINT(ct.Structure):
    _fields_ = [('x', ct.c_int32), ('y', ct.c_int32)]


class _RECT(ct.Structure):
    _fields_ = [('left', ct.c_int32), ('top', ct.c_int32), ('right', ct.c_int32), ('bottom', ct.c_int32)]


class _FILETIME(ct.Structure):
    _fields_ = [('low', ct.c_uint32), ('high', ct.c_uint32)]


class Win32RegionAPI:
    def __init__(self):
        if os.name != 'nt':
            raise DesktopRegionError('WINDOWS_NATIVE_REQUIRED')
        self.user = ct.WinDLL('user32', use_last_error=True)
        self.kernel = ct.WinDLL('kernel32', use_last_error=True)
        self.gdi = ct.WinDLL('gdi32', use_last_error=True)
        H, B, D, I = ct.c_void_p, ct.c_int32, ct.c_uint32, ct.c_int32
        for dll, name, ret, args in [
            (self.user, 'IsWindow', B, [H]),
            (self.user, 'GetWindowThreadProcessId', D, [H, ct.POINTER(D)]),
            (self.user, 'GetPropW', H, [H, ct.c_wchar_p]),
            (self.user, 'SetPropW', B, [H, ct.c_wchar_p, H]),
            (self.user, 'RemovePropW', H, [H, ct.c_wchar_p]),
            (self.user, 'GetWindowRect', B, [H, ct.POINTER(_RECT)]),
            (self.user, 'GetDpiForWindow', D, [H]),
            (self.user, 'GetWindowDpiAwarenessContext', H, [H]),
            (self.user, 'SetThreadDpiAwarenessContext', H, [H]),
            (self.user, 'GetAncestor', H, [H, D]),
            (self.user, 'GetForegroundWindow', H, []),
            (self.user, 'GetWindowRgn', I, [H, H]),
            (self.user, 'GetWindowDisplayAffinity', B, [H, ct.POINTER(D)]),
            (self.user, 'WindowFromPoint', H, [_POINT]),
            (self.kernel, 'OpenProcess', H, [D, B, D]),
            (self.kernel, 'GetProcessTimes', B, [H, ct.POINTER(_FILETIME), ct.POINTER(_FILETIME), ct.POINTER(_FILETIME), ct.POINTER(_FILETIME)]),
            (self.kernel, 'GetExitCodeProcess', B, [H, ct.POINTER(D)]),
            (self.kernel, 'CloseHandle', B, [H]),
            (self.gdi, 'CreateRectRgn', H, [I, I, I, I]),
            (self.gdi, 'CombineRgn', I, [H, H, H, I]),
            (self.gdi, 'EqualRgn', B, [H, H]),
            (self.gdi, 'DeleteObject', B, [H]),
        ]:
            function = getattr(dll, name)
            function.restype, function.argtypes = ret, args

    @staticmethod
    def _require(value, reason):
        if not value:
            raise DesktopRegionError(reason)
        return value

    def _process(self, pid):
        handle = self._require(self.kernel.OpenProcess(0x1000, False, pid), 'PROCESS_QUERY_UNAVAILABLE')
        try:
            times = [_FILETIME() for _ in range(4)]
            self._require(self.kernel.GetProcessTimes(handle, *(ct.byref(t) for t in times)), 'PROCESS_CREATION_UNAVAILABLE')
            code = ct.c_uint32()
            self._require(self.kernel.GetExitCodeProcess(handle, ct.byref(code)), 'PROCESS_STATE_UNAVAILABLE')
            if code.value != 259:
                raise DesktopRegionError('PROCESS_EXITED')
            return times[0].low | times[0].high << 32
        finally:
            self.kernel.CloseHandle(handle)

    @contextmanager
    def physical_context(self, value):
        # A portable Python process may start DPI unaware. Mirror the owned
        # Electron window's DPI context for geometry, restoring this thread.
        target = self._require(self.user.GetWindowDpiAwarenessContext(value['hwnd']), 'MAIN_DPI_CONTEXT_UNAVAILABLE')
        original = self._require(self.user.SetThreadDpiAwarenessContext(target), 'CALLER_PHYSICAL_DPI_CONTEXT_UNAVAILABLE')
        try:
            yield
        finally:
            self._require(self.user.SetThreadDpiAwarenessContext(original), 'CALLER_DPI_CONTEXT_RESTORE_FAILED')

    def validate(self, value):
        if (not isinstance(value, dict) or set(value) != {'hwnd', 'pid', 'creation', 'marker_name', 'marker_value'}
                or any(type(value[k]) is not int or value[k] <= 0 for k in ['hwnd', 'pid', 'creation', 'marker_value'])
                or not isinstance(value['marker_name'], str) or not value['marker_name'].startswith('EEAS-')):
            raise DesktopRegionError('TRUSTED_MARKED_MAIN_WINDOW_REQUIRED')
        hwnd = value['hwnd']
        self._require(self.user.IsWindow(hwnd), 'MAIN_WINDOW_LOST')
        pid = ct.c_uint32()
        self._require(self.user.GetWindowThreadProcessId(hwnd, ct.byref(pid)), 'WINDOW_OWNER_UNAVAILABLE')
        if (pid.value != value['pid'] or self._process(pid.value) != value['creation']
                or int(self.user.GetPropW(hwnd, value['marker_name']) or 0) != value['marker_value']
                or int(self.user.GetAncestor(hwnd, 2) or 0) != hwnd):
            raise DesktopRegionError('MAIN_WINDOW_IDENTITY_CHANGED')
        bounds = _RECT()
        with self.physical_context(value):
            self._require(self.user.GetWindowRect(hwnd, ct.byref(bounds)), 'MAIN_WINDOW_BOUNDS_UNAVAILABLE')
            dpi = self._require(self.user.GetDpiForWindow(hwnd), 'MAIN_WINDOW_DPI_UNAVAILABLE')
        return {'window_bounds': {'x': bounds.left, 'y': bounds.top, 'width': bounds.right - bounds.left,
                    'height': bounds.bottom - bounds.top}, 'dpi': int(dpi)}

    def mark_window(self, hwnd, owner_pid):
        """Private main registration seam; never expose arbitrary HWND to UI.

        Domain caller authenticates the desktop main secret/sender. Query the
        actual owner/creation before minting a lifetime marker, then recheck.
        """
        if (type(hwnd) is not int or not 0 < hwnd < 1 << (ct.sizeof(ct.c_void_p) * 8)
                or type(owner_pid) is not int or not 0 < owner_pid < 1 << 32):
            raise DesktopRegionError('EXACT_MAIN_WINDOW_AND_PID_REQUIRED')
        self._require(self.user.IsWindow(hwnd), 'MAIN_WINDOW_LOST')
        pid = ct.c_uint32()
        self._require(self.user.GetWindowThreadProcessId(hwnd, ct.byref(pid)), 'WINDOW_OWNER_UNAVAILABLE')
        if pid.value != owner_pid or int(self.user.GetAncestor(hwnd, 2) or 0) != hwnd:
            raise DesktopRegionError('MAIN_WINDOW_IDENTITY_CHANGED')
        creation = self._process(owner_pid)
        name = 'EEAS-Workspace-' + uuid.uuid4().hex
        nonce = (uuid.uuid4().int & ((1 << (ct.sizeof(ct.c_void_p) * 8 - 1)) - 1)) or 1
        self._require(self.user.SetPropW(hwnd, name, nonce), 'MAIN_WINDOW_LIFETIME_MARK_FAILED')
        value = {'hwnd': hwnd, 'pid': owner_pid, 'creation': creation, 'marker_name': name, 'marker_value': nonce}
        self.validate(value)
        return value

    def clear_marker(self, value):
        self.validate(value)
        if not value['marker_name'].startswith('EEAS-Workspace-'):
            raise DesktopRegionError('WORKSPACE_OWN_MARKER_REQUIRED')
        result = int(self.user.RemovePropW(value['hwnd'], value['marker_name']) or 0)
        if result != value['marker_value']:
            raise DesktopRegionError('MAIN_WINDOW_LIFETIME_MARK_REMOVE_NOT_VERIFIED')

    def equal_region(self, value, rects):
        metadata = self.validate(value)
        actual = self._require(self.gdi.CreateRectRgn(0, 0, 0, 0), 'REGION_ALLOC_FAILED')
        wanted = 0
        try:
            # GetWindowRgn virtualizes returned coordinates for a DPI-unaware
            # caller. Read under the marked window context, matching the
            # physical region setter and geometry proof.
            with self.physical_context(value):
                region_type = self.user.GetWindowRgn(value['hwnd'], actual)
            if region_type == 0:
                # ERROR also denotes no custom shape; never claim this is empty.
                raise DesktopRegionError('NATIVE_CUSTOM_REGION_UNAVAILABLE')
            wanted = self._require(self.gdi.CreateRectRgn(0, 0, 0, 0), 'REGION_ALLOC_FAILED')
            for rect in rects:
                if rect['x'] + rect['width'] > metadata['window_bounds']['width'] or rect['y'] + rect['height'] > metadata['window_bounds']['height']:
                    raise DesktopRegionError('REGION_EXCEEDS_ACTUAL_WINDOW')
                part = self._require(self.gdi.CreateRectRgn(rect['x'], rect['y'], rect['x'] + rect['width'], rect['y'] + rect['height']), 'REGION_ALLOC_FAILED')
                try:
                    self._require(self.gdi.CombineRgn(wanted, wanted, part, 2), 'REGION_UNION_FAILED')
                finally:
                    self.gdi.DeleteObject(part)
            matches = bool(self.gdi.EqualRgn(actual, wanted))
            if self.validate(value) != metadata:
                raise DesktopRegionError('MAIN_WINDOW_CHANGED_DURING_REGION_QUERY')
            return {**metadata, 'matches': matches, 'region_type': int(region_type)}
        finally:
            if wanted:
                self.gdi.DeleteObject(wanted)
            self.gdi.DeleteObject(actual)

    def affinity(self, value):
        self.validate(value)
        result = ct.c_uint32()
        self._require(self.user.GetWindowDisplayAffinity(value['hwnd'], ct.byref(result)), 'CAPTURE_AFFINITY_QUERY_UNAVAILABLE')
        self.validate(value)
        return int(result.value)

    def foreground_matches(self, value):
        self.validate(value)
        hwnd = int(self.user.GetForegroundWindow() or 0)
        root = int(self.user.GetAncestor(hwnd, 2) or 0) if hwnd else 0
        pid = ct.c_uint32()
        if root:
            self.user.GetWindowThreadProcessId(root, ct.byref(pid))
        self.validate(value)
        return root == value['hwnd'] and pid.value == value['pid']

    def target_at(self, value, point):
        self.validate(value)
        with self.physical_context(value):
            hwnd = int(self.user.WindowFromPoint(_POINT(point['x'], point['y'])) or 0)
        if not hwnd:
            raise DesktopRegionError('UNDERLAY_WINDOW_UNAVAILABLE')
        root = int(self.user.GetAncestor(hwnd, 2) or 0)
        pid = ct.c_uint32()
        self._require(self.user.GetWindowThreadProcessId(root, ct.byref(pid)), 'UNDERLAY_OWNER_UNAVAILABLE')
        if root == value['hwnd'] or pid.value == value['pid']:
            raise DesktopRegionError('SELF_TARGET_REJECTED')
        creation = self._process(pid.value)
        self.validate(value)
        return {'hwnd': root, 'pid': int(pid.value), 'creation': creation}


class WindowsDesktopRegionProbe:
    def __init__(self, *, surface_authority, native=None):
        if not callable(surface_authority):
            raise DesktopRegionError('MAIN_SURFACE_AUTHORITY_REQUIRED')
        self.authority = surface_authority
        self.native = Win32RegionAPI() if native is None else native

    def _surface(self, surface_ref):
        ref = surface_reference(surface_ref)
        value = self.authority(ref)
        self.native.validate(value)
        return ref, value

    def verify_region(self, surface_ref, rectangles_physical):
        ref, value = self._surface(surface_ref)
        rects = rectangles(rectangles_physical)
        result = self.native.equal_region(value, rects)
        if result.get('matches') is not True:
            raise DesktopRegionError('NATIVE_REGION_DIFFERS_FROM_EXPECTED')
        return {'surface_ref': ref, 'status': 'VERIFIED_NATIVE_REGION', 'verified_region': True,
            'region_sha256': _sha(rects), 'coordinate_space': 'WINDOW_PHYSICAL',
            'rectangles_physical': rects, 'window_bounds': result['window_bounds'], 'dpi': result['dpi'],
            'qualification': 'NATIVE_REGION_QUERY_ONLY_HUMAN_INTERACTION_REQUIRES_HOST_TEST'}

    def capture_exclusion(self, surface_ref):
        ref, value = self._surface(surface_ref)
        affinity = self.native.affinity(value)
        return {'surface_ref': ref, 'affinity': affinity, 'status': 'AFFINITY_EXCLUDED' if affinity == 0x11 else 'NOT_EXCLUDED',
            'verified_affinity_excluded': affinity == 0x11, 'frame_self_exclusion': 'NOT_PROVEN_BY_AFFINITY_ALONE'}

    def verify_foreground(self, surface_ref):
        ref, value = self._surface(surface_ref)
        verified = self.native.foreground_matches(value) is True
        return {'surface_ref': ref, 'status': 'OWN_MAIN_FOREGROUND' if verified else 'OWN_MAIN_NOT_FOREGROUND',
            'verified_keyboard_owned': verified, 'foreground_scope': 'OWN_MAIN' if verified else 'OTHER_OR_UNKNOWN',
            'qualification': 'READONLY_FOREGROUND_OBSERVATION_RECHECK_ON_FOCUS_OR_ACTION'}

    def hit_test(self, surface_ref, point):
        """Host-only observation. Raw HWND/PID must never become renderer authority."""
        ref, value = self._surface(surface_ref)
        if (not isinstance(point, dict) or set(point) != {'x', 'y'}
                or any(type(v) is not int or not -65536 <= v <= 65536 for v in point.values())):
            raise DesktopRegionError('EXACT_PHYSICAL_SCREEN_POINT_REQUIRED')
        return {'surface_ref': ref, 'point': dict(point), 'target': self.native.target_at(value, point),
            'status': 'OBSERVED_UNDERLAY_TARGET', 'action_authority': 'NOT_GRANTED'}


class RegionAgentInputGate:
    """Fence only Agent dispatch; native region proof controls human coverage.

    Region authority callback returns current {identity_tag, region_sha256,
    mode, verified_region}; Root must query the actual native region. The fence
    callback synchronously locks/drains dispatch before returning True.
    """
    def __init__(self, *, region_authority, forwarding_fence):
        if not callable(region_authority) or not callable(forwarding_fence):
            raise DesktopRegionError('MAIN_REGION_AUTHORITY_CALLBACKS_REQUIRED')
        self.authority, self.fence = region_authority, forwarding_fence
        self.records, self.lock = {}, threading.RLock()

    def _record(self, surface_ref):
        ref = surface_reference(surface_ref)
        actual = self.authority(ref)
        if not isinstance(actual, dict) or not isinstance(actual.get('identity_tag'), str) or not actual['identity_tag']:
            raise DesktopRegionError('MAIN_REGION_IDENTITY_REQUIRED')
        record = self.records.setdefault(ref['id'], {'ref': ref, 'identity': actual['identity_tag'], 'epoch': 1, 'pause': None, 'fault': False, 'closed': False})
        if record['ref'] != ref or record['identity'] != actual['identity_tag'] or record['closed']:
            raise DesktopRegionError('SURFACE_IDENTITY_CHANGED_OR_CLOSED')
        return record, actual

    @staticmethod
    def _epoch(record, expected):
        if type(expected) is not int or expected != record['epoch']:
            raise DesktopRegionError('STALE_INPUT_EPOCH')

    def inspect(self, surface_ref):
        with self.lock:
            record, _ = self._record(surface_ref)
            return {'surface_ref': record['ref'], 'epoch': record['epoch'], 'enabled': not record['pause'] and not record['fault'],
                'status': 'FAULTED' if record['fault'] else 'PAUSED' if record['pause'] else 'ACTIVE'}

    def admit(self, surface_ref, expected_epoch):
        with self.lock:
            record, _ = self._record(surface_ref)
            self._epoch(record, expected_epoch)
            if record['pause'] or record['fault']:
                raise DesktopRegionError('AGENT_DISPATCH_BLOCKED')
            return self.inspect(surface_ref)

    def pause_input(self, surface_ref, reason, expected_epoch):
        with self.lock:
            record, actual = self._record(surface_ref)
            self._epoch(record, expected_epoch)
            if reason != 'FULL_WORKSPACE' or actual.get('mode') != 'FULL_WORKSPACE' or actual.get('verified_region') is not True:
                raise DesktopRegionError('FULL_WORKSPACE_ACTUAL_REGION_REQUIRED')
            if actual.get('verified_keyboard_owned') is not True:
                raise DesktopRegionError('FULL_WORKSPACE_ACTUAL_FOREGROUND_REQUIRED')
            if record['pause'] or record['fault']:
                raise DesktopRegionError('SURFACE_ALREADY_BLOCKED')
            shape_sha = actual.get('region_sha256')
            if not isinstance(shape_sha, str) or not re.fullmatch('[0-9a-f]{64}', shape_sha):
                raise DesktopRegionError('EXACT_NATIVE_REGION_HASH_REQUIRED')
            record['fault'] = True
            try:
                verified = self.fence(record['ref'], False) is True
            except Exception:
                record['epoch'] += 1
                raise DesktopRegionError('AGENT_FENCE_OUTCOME_UNKNOWN') from None
            if not verified:
                record['epoch'] += 1
                raise DesktopRegionError('AGENT_FENCE_NOT_VERIFIED')
            record['epoch'] += 1
            identifier = uuid.uuid4().hex
            receipt = {'pause_ref': {'id': identifier, 'sha256': _sha({'id': identifier, 'surface': record['ref'], 'identity': record['identity'], 'epoch': record['epoch'], 'region_sha256': shape_sha})},
                'surface_ref': record['ref'], 'epoch': record['epoch'], 'status': 'PAUSED',
                'verified_input_blocked': True, 'verified_agent_dispatch_blocked': True,
                'verified_native_region_covered': True, 'verified_native_visibility_suspended': False,
                'native_visibility_strategy': 'OWN_MAIN_REGION_COVERS_WORKSPACE', 'region_sha256': shape_sha}
            record['pause'], record['fault'] = receipt, False
            return json.loads(json.dumps(receipt))

    def resume_input(self, pause_ref, expected_epoch):
        with self.lock:
            if not isinstance(pause_ref, dict) or set(pause_ref) != {'id', 'sha256'}:
                raise DesktopRegionError('EXACT_PAUSE_RECEIPT_REQUIRED')
            matches = [r for r in self.records.values() if r['pause'] and r['pause']['pause_ref'] == pause_ref]
            if len(matches) != 1:
                raise DesktopRegionError('PAUSE_RECEIPT_UNKNOWN_OR_REVOKED')
            record = matches[0]
            self._epoch(record, expected_epoch)
            _, actual = self._record(record['ref'])
            if actual.get('mode') not in {'DESKTOP_OPEN', 'MODELESS'} or actual.get('verified_region') is not True:
                raise DesktopRegionError('RESTORED_NATIVE_REGION_NOT_VERIFIED')
            try:
                enabled = self.fence(record['ref'], True) is True
            except Exception:
                enabled = False
            if not enabled:
                record['fault'] = True
                record['epoch'] += 1
                raise DesktopRegionError('AGENT_FENCE_RESTORE_NOT_VERIFIED')
            record['pause'], record['fault'] = None, False
            record['epoch'] += 1
            return {'surface_ref': record['ref'], 'epoch': record['epoch'], 'status': 'ACTIVE', 'resumed': True}

    def close_surface(self, surface_ref):
        with self.lock:
            record, _ = self._record(surface_ref)
            record['closed'] = True
            record['epoch'] += 1
            # Closing never grants actions or mutates application windows.
            self.fence(record['ref'], False)
            return {'status': 'CLOSED', 'epoch': record['epoch'], 'agent_dispatch_enabled': False}
