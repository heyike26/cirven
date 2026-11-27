"""Temporary own-window appbar leases using stock Shell messages.

No window is created here. Electron owns four marked helper HWNDs and moves
them to the returned accepted bounds. No registry/SPI/global desktop changes.
ABM_REMOVE unregisters only the exact same authority-owned helper identity.
"""
from __future__ import annotations
import ctypes as ct
import json
import threading
import uuid

from packages.adapters.windows_desktop_region import (
    DesktopRegionError, Win32RegionAPI, _RECT, _sha, surface_reference,
)


class AppBarError(DesktopRegionError):
    pass


EDGES = {'LEFT': 0, 'TOP': 1, 'RIGHT': 2, 'BOTTOM': 3}


class APPBARDATA(ct.Structure):
    _fields_ = [('cbSize', ct.c_uint32), ('hWnd', ct.c_void_p), ('uCallbackMessage', ct.c_uint32),
               ('uEdge', ct.c_uint32), ('rc', _RECT), ('lParam', ct.c_ssize_t)]


class MONITORINFO(ct.Structure):
    _fields_ = [('cbSize', ct.c_uint32), ('rcMonitor', _RECT), ('rcWork', _RECT), ('dwFlags', ct.c_uint32)]


def _bounds(rect):
    return {'x': rect.left, 'y': rect.top, 'width': rect.right - rect.left, 'height': rect.bottom - rect.top}


def _rect(value):
    if (not isinstance(value, dict) or set(value) != {'x', 'y', 'width', 'height'}
            or any(type(v) is not int for v in value.values()) or min(value['width'], value['height']) < 1
            or max(value['width'], value['height']) > 65536 or abs(value['x']) > 65536 or abs(value['y']) > 65536):
        raise AppBarError('EXACT_PHYSICAL_SCREEN_BOUNDS_REQUIRED')
    return dict(value)


def _thickness(value):
    if (not isinstance(value, dict) or set(value) != set(EDGES)
            or any(type(v) is not int or not 0 <= v <= 8192 for v in value.values())):
        raise AppBarError('FOUR_PHYSICAL_EDGE_THICKNESSES_REQUIRED')
    return dict(value)


class Win32AppBarAPI:
    def __init__(self):
        self.identity = Win32RegionAPI()
        self.positions = {}
        self.shell = ct.WinDLL('shell32', use_last_error=True)
        self.shell.SHAppBarMessage.argtypes = [ct.c_uint32, ct.POINTER(APPBARDATA)]
        self.shell.SHAppBarMessage.restype = ct.c_size_t
        self.identity.user.MonitorFromWindow.argtypes = [ct.c_void_p, ct.c_uint32]
        self.identity.user.MonitorFromWindow.restype = ct.c_void_p
        self.identity.user.GetMonitorInfoW.argtypes = [ct.c_void_p, ct.POINTER(MONITORINFO)]
        self.identity.user.GetMonitorInfoW.restype = ct.c_int32

    def validate(self, value):
        return self.identity.validate(value)

    def monitor(self, value):
        self.validate(value)
        with self.identity.physical_context(value):
            handle = int(self.identity.user.MonitorFromWindow(value['hwnd'], 2) or 0)
            if not handle:
                raise AppBarError('MAIN_MONITOR_UNAVAILABLE')
            info = MONITORINFO()
            info.cbSize = ct.sizeof(info)
            if not self.identity.user.GetMonitorInfoW(handle, ct.byref(info)):
                raise AppBarError('MONITOR_WORKAREA_QUERY_FAILED')
        self.validate(value)
        return {'monitor_id': handle, 'monitor_bounds': _bounds(info.rcMonitor), 'work_area': _bounds(info.rcWork)}

    def read_monitor(self, handle):
        """Read the saved monitor after all own AppBars were already removed.

        This grants no window authority and performs no Shell mutation. A
        cleared lifetime marker must never be reminted to adopt old resources.
        """
        if type(handle) is not int or not 0<handle<1<<(ct.sizeof(ct.c_void_p)*8):
            raise AppBarError('EXACT_SAVED_MONITOR_REQUIRED')
        original=self.identity._require(self.identity.user.SetThreadDpiAwarenessContext(ct.c_void_p(-4)),
            'MONITOR_PHYSICAL_DPI_CONTEXT_UNAVAILABLE')
        try:
            info=MONITORINFO();info.cbSize=ct.sizeof(info)
            if not self.identity.user.GetMonitorInfoW(handle,ct.byref(info)):
                raise AppBarError('SAVED_MONITOR_WORKAREA_QUERY_FAILED')
        finally:
            self.identity._require(self.identity.user.SetThreadDpiAwarenessContext(original),'MONITOR_DPI_CONTEXT_RESTORE_FAILED')
        return {'monitor_id':handle,'monitor_bounds':_bounds(info.rcMonitor),'work_area':_bounds(info.rcWork)}

    def _data(self, value, callback=0, edge=None, rect=None):
        self.validate(value)
        data = APPBARDATA()
        data.cbSize, data.hWnd, data.uCallbackMessage = ct.sizeof(data), value['hwnd'], callback
        if edge is not None:
            data.uEdge = EDGES[edge]
        if rect is not None:
            data.rc = _RECT(rect['x'], rect['y'], rect['x'] + rect['width'], rect['y'] + rect['height'])
        return data

    def register(self, value, callback):
        data = self._data(value, callback)
        if not self.shell.SHAppBarMessage(0, ct.byref(data)):
            raise AppBarError('OWN_APPBAR_REGISTER_FAILED')
        self.validate(value)
        self.positions.pop(_sha(value), None)

    def position(self, value, edge, thickness, monitor_bounds):
        with self.identity.physical_context(value):
            return self._position(value, edge, thickness, monitor_bounds)

    def _position(self, value, edge, thickness, monitor_bounds):
        proposed = dict(monitor_bounds)
        if edge == 'LEFT':
            proposed['width'] = thickness
        elif edge == 'RIGHT':
            proposed['x'] += proposed['width'] - thickness
            proposed['width'] = thickness
        elif edge == 'TOP':
            proposed['height'] = thickness
        else:
            proposed['y'] += proposed['height'] - thickness
            proposed['height'] = thickness
        data = self._data(value, edge=edge, rect=proposed)
        # Return values are message-specific. QUERYPOS/SETPOS return TRUE per SDK.
        if not self.shell.SHAppBarMessage(2, ct.byref(data)):
            raise AppBarError('OWN_APPBAR_QUERY_POSITION_FAILED')
        if edge == 'LEFT':
            data.rc.right = data.rc.left + thickness
        elif edge == 'RIGHT':
            data.rc.left = data.rc.right - thickness
        elif edge == 'TOP':
            data.rc.bottom = data.rc.top + thickness
        else:
            data.rc.top = data.rc.bottom - thickness
        queried = _rect(_bounds(data.rc))
        if self.positions.get(_sha(value)) == queried:
            # SETPOS itself broadcasts ABN_POSCHANGED. Stable requery must not
            # rebroadcast endlessly while the host reconciles its callbacks.
            self.validate(value)
            return queried
        if not self.shell.SHAppBarMessage(3, ct.byref(data)):
            raise AppBarError('OWN_APPBAR_SET_POSITION_FAILED')
        accepted = _rect(_bounds(data.rc))
        self.validate(value)
        self.positions[_sha(value)] = accepted
        return accepted

    def remove(self, value):
        data = self._data(value)
        if not self.shell.SHAppBarMessage(1, ct.byref(data)):
            raise AppBarError('OWN_APPBAR_REMOVE_NOT_VERIFIED')
        self.validate(value)
        self.positions.pop(_sha(value), None)

    def notify(self, value, kind, active=None):
        data = self._data(value)
        if kind == 'ACTIVATE':
            if type(active) is not bool:
                raise AppBarError('EXACT_ACTIVATION_STATE_REQUIRED')
            data.lParam = int(active)
            message = 6
        elif kind == 'WINDOWPOSCHANGED':
            message = 9
        else:
            raise AppBarError('SUPPORTED_APPBAR_NOTIFICATION_REQUIRED')
        # These notifications do not provide a success/failure state receipt.
        self.shell.SHAppBarMessage(message, ct.byref(data))
        self.validate(value)


class WindowsAppBarLease:
    def __init__(self, *, authority, native=None):
        if not callable(authority):
            raise AppBarError('MAIN_APPBAR_AUTHORITY_REQUIRED')
        self.authority = authority
        self.native = Win32AppBarAPI() if native is None else native
        self.records, self.lock = {}, threading.RLock()

    def _authority(self, surface_ref):
        ref = surface_reference(surface_ref)
        value = self.authority(ref)
        if (not isinstance(value, dict) or set(value) != {'main', 'bars', 'callback_message'}
                or not isinstance(value['bars'], dict) or set(value['bars']) != set(EDGES)
                or type(value['callback_message']) is not int or not 0x8000 <= value['callback_message'] < 0xC000):
            raise AppBarError('FOUR_OWN_HELPERS_AND_PRIVATE_CALLBACK_REQUIRED')
        self.native.validate(value['main'])
        windows = [value['main']['hwnd']]
        for bar in value['bars'].values():
            self.native.validate(bar)
            if (bar['pid'] != value['main']['pid'] or bar['creation'] != value['main']['creation']
                    or bar['hwnd'] in windows):
                raise AppBarError('DISTINCT_HELPERS_IN_EXACT_MAIN_PROCESS_REQUIRED')
            windows.append(bar['hwnd'])
        return ref, json.loads(json.dumps(value)), _sha(value)

    def _record(self, lease_ref):
        if (not isinstance(lease_ref, dict) or set(lease_ref) != {'id', 'sha256'}
                or lease_ref.get('id') not in self.records):
            raise AppBarError('EXACT_APPBAR_LEASE_REFERENCE_REQUIRED')
        record = self.records[lease_ref['id']]
        if record['lease_ref'] != lease_ref:
            raise AppBarError('APPBAR_LEASE_IDENTITY_CHANGED')
        return record

    def _validate_record(self, record):
        _, _, identity = self._authority(record['surface_ref'])
        if identity != record['identity']:
            raise AppBarError('APPBAR_MAIN_OR_HELPER_IDENTITY_CHANGED')
        monitor = self.native.monitor(record['authority']['main'])
        if (monitor['monitor_id'] != record['original_monitor']['monitor_id']
                or monitor['monitor_bounds'] != record['original_monitor']['monitor_bounds']):
            raise AppBarError('APPBAR_MONITOR_CHANGED_RELEASE_OR_REBIND_REQUIRED')
        return monitor

    def _receipt(self, record, actual, expected=None):
        verified = (expected is not None and actual['work_area'] == expected and not record['errors']
                    and not record['released'] and actual.get('query_verified', True) is True)
        result = {'lease_ref': record['lease_ref'], 'surface_ref': record['surface_ref'], 'revision': record['revision'],
            'status': 'RELEASED' if record['released'] else 'FAULTED' if record['errors'] else 'RESERVED',
            'original_monitor': record['original_monitor'], 'actual_monitor': actual,
            'actual_monitor_query_verified': actual.get('query_verified', True) is True,
            'accepted_bar_bounds': record['bounds'], 'thickness_physical': record['thickness'],
            'expected_center_physical': expected, 'visible_all_verified': verified,
            'work_area_verified': verified, 'coordinate_space': 'SCREEN_PHYSICAL',
            'qualifications': {'work_area': 'MATCHES_HOST_EXPECTED_CENTER' if verified else 'NOT_VERIFIED',
                'arbitrary_existing_window_reflow': 'NOT_PROVEN_BY_WORKAREA', 'desktop_icon_reflow': 'HOST_GUI_TEST_REQUIRED',
                'gui_created_by_adapter': False, 'persistent_desktop_settings_changed': False},
            'limitations': list(record['errors'])}
        return {**result, 'sha256': _sha(result)}

    def _apply(self, record, thickness, expected):
        self._validate_record(record)
        original = record['original_monitor']['work_area']
        if (thickness['LEFT'] + thickness['RIGHT'] >= original['width']
                or thickness['TOP'] + thickness['BOTTOM'] >= original['height']):
            raise AppBarError('APPBAR_CHROME_EXHAUSTS_WORKAREA')
        record['thickness'] = thickness
        record['revision'] += 1
        record['errors'] = []
        try:
            for edge in EDGES:
                target = record['authority']['bars'][edge]
                if thickness[edge] == 0:
                    if edge in record['registered']:
                        self.native.remove(target)
                        record['registered'].remove(edge)
                    record['bounds'].pop(edge, None)
                    continue
                if edge not in record['registered']:
                    # Register only after identity proof. Keep a pending entry
                    # if the post-call identity/query fails: no false clean state.
                    record['registered'].add(edge)
                    try:
                        self.native.register(target, record['authority']['callback_message'])
                    except AppBarError as error:
                        if str(error) == 'OWN_APPBAR_REGISTER_FAILED':
                            record['registered'].remove(edge)
                        raise
                record['bounds'][edge] = self.native.position(target, edge, thickness[edge], record['original_monitor']['monitor_bounds'])
            actual = self._validate_record(record)
        except Exception as error:
            record['errors'].append('APPBAR_RESERVATION_INCOMPLETE_' + str(error))
            # Preserve the lease, including pending registrations, so explicit
            # host release can recover it. Never remove a reused/foreign HWND.
            try:
                actual = self.native.monitor(record['authority']['main'])
            except Exception:
                actual = {**record['original_monitor'], 'query_verified': False}
            return self._receipt(record, actual, None)
        return self._receipt(record, actual, expected)

    def reserve(self, surface_ref, thickness_physical, *, expected_center_physical=None):
        with self.lock:
            ref, authority, identity = self._authority(surface_ref)
            thickness = _thickness(thickness_physical)
            expected = _rect(expected_center_physical) if expected_center_physical is not None else None
            if any(not r['released'] and r['surface_ref']['id'] == ref['id'] for r in self.records.values()):
                raise AppBarError('SURFACE_ALREADY_HAS_APPBAR_LEASE_USE_UPDATE')
            monitor = self.native.monitor(authority['main'])
            if (thickness['LEFT'] + thickness['RIGHT'] >= monitor['work_area']['width']
                    or thickness['TOP'] + thickness['BOTTOM'] >= monitor['work_area']['height']):
                raise AppBarError('APPBAR_CHROME_EXHAUSTS_WORKAREA')
            identifier = uuid.uuid4().hex
            record = {'lease_ref': {'id': identifier, 'sha256': _sha({'id': identifier, 'surface_ref': ref, 'identity': identity})},
                'surface_ref': ref, 'authority': authority, 'identity': identity, 'original_monitor': monitor,
                'registered': set(), 'bounds': {}, 'thickness': thickness, 'revision': 0, 'released': False, 'errors': []}
            self.records[identifier] = record
            return self._apply(record, thickness, expected)

    def update(self, lease_ref, thickness_physical, expected_revision, *, expected_center_physical=None):
        with self.lock:
            record = self._record(lease_ref)
            if record['released']:
                raise AppBarError('APPBAR_LEASE_RELEASED')
            if type(expected_revision) is not int or expected_revision != record['revision']:
                raise AppBarError('STALE_APPBAR_LEASE_REVISION')
            expected = _rect(expected_center_physical) if expected_center_physical is not None else None
            return self._apply(record, _thickness(thickness_physical), expected)

    def reconcile(self, lease_ref):
        """Host callback on ABN_POSCHANGED; always requery accepted positions."""
        with self.lock:
            record = self._record(lease_ref)
            if record['released']:
                raise AppBarError('APPBAR_LEASE_RELEASED')
            return self._apply(record, record['thickness'], None)

    def verify_center(self, lease_ref, expected_center_physical):
        with self.lock:
            record = self._record(lease_ref)
            actual = self._validate_record(record)
            return self._receipt(record, actual, _rect(expected_center_physical))

    def notify(self, lease_ref, edge, kind, active=None):
        with self.lock:
            record = self._record(lease_ref)
            self._validate_record(record)
            if record['released'] or edge not in record['registered']:
                raise AppBarError('APPBAR_EDGE_NOT_REGISTERED')
            self.native.notify(record['authority']['bars'][edge], kind, active)
            return {'status': 'NOTIFICATION_SENT', 'effect': 'NOT_VERIFIED'}

    def release(self, lease_ref):
        with self.lock:
            record = self._record(lease_ref)
            if record['released']:
                return self._receipt(record, self.native.monitor(record['authority']['main']), None)
            failures = []
            # Original helper snapshots are validated independently, so a lost
            # main/helper does not prevent removing the other exact own bars.
            for edge in list(record['registered']):
                try:
                    self.native.remove(record['authority']['bars'][edge])
                    record['registered'].remove(edge)
                    record['bounds'].pop(edge, None)
                except Exception as error:
                    failures.append('APPBAR_REMOVE_INCOMPLETE_' + edge + '_' + str(error))
            record['errors'] = failures
            record['released'] = not record['registered']
            record['revision'] += 1
            try:
                actual = self.native.monitor(record['authority']['main'])
            except Exception:
                actual = {**record['original_monitor'], 'query_verified': False}
                failures.append('POST_RELEASE_MONITOR_QUERY_UNAVAILABLE')
            result = self._receipt(record, actual, None)
            result['original_work_area_restored'] = record['released'] and not failures and actual == record['original_monitor']
            result['sha256'] = _sha({k: v for k, v in result.items() if k != 'sha256'})
            return result

    def export_state(self, lease_ref):
        """Private persistence only; contains trusted HWNDs, never renderer DTO."""
        with self.lock:
            record = self._record(lease_ref)
            self._validate_record(record)
            payload = {'schema': 'eeas.owned-appbar-cleanup.v1', 'surface_ref': record['surface_ref'],
                'authority': record['authority'], 'identity': record['identity'],
                'original_monitor': record['original_monitor'], 'registered_edges': sorted(record['registered']),
                'released': record['released']}
            return {'payload': payload, 'sha256': _sha(payload)}

    def release_owned_state(self, snapshot):
        """Explicit trusted-main cleanup after controller restart; never adopt.

        Domain must authenticate the current desktop main and verify its private
        persisted receipt. Every saved exact marker/PID/creation is read again.
        Fresh baseline is captured by a subsequent reserve after this cleanup.
        """
        with self.lock:
            if (not isinstance(snapshot, dict) or set(snapshot) != {'payload', 'sha256'}
                    or not isinstance(snapshot['payload'], dict) or _sha(snapshot['payload']) != snapshot['sha256']):
                raise AppBarError('EXACT_PRIVATE_APPBAR_CLEANUP_SNAPSHOT_REQUIRED')
            payload = snapshot['payload']
            if (set(payload) != {'schema', 'surface_ref', 'authority', 'identity', 'original_monitor', 'registered_edges', 'released'}
                    or payload['schema'] != 'eeas.owned-appbar-cleanup.v1'
                    or type(payload['released']) is not bool or not isinstance(payload['registered_edges'], list)
                    or any(not isinstance(edge, str) for edge in payload['registered_edges'])
                    or len(set(payload['registered_edges'])) != len(payload['registered_edges'])
                    or any(edge not in EDGES for edge in payload['registered_edges'])):
                raise AppBarError('APPBAR_CLEANUP_SNAPSHOT_SCHEMA_INVALID')
            _, authority, identity = self._authority(payload['surface_ref'])
            if identity != payload['identity'] or authority != payload['authority']:
                raise AppBarError('APPBAR_RESTART_OWNERSHIP_NOT_VERIFIED')
            failures, removed = [], []
            for edge in payload['registered_edges']:
                try:
                    self.native.remove(authority['bars'][edge])
                    removed.append(edge)
                except Exception as error:
                    failures.append(edge + '_' + str(error))
            monitor = self.native.monitor(authority['main'])
            return {'status': 'OWN_STATE_RELEASED' if not failures else 'OWN_STATE_CLEANUP_INCOMPLETE',
                'removed_edges': removed, 'limitations': failures, 'actual_monitor': monitor,
                'original_work_area_restored': not failures and monitor == payload['original_monitor'],
                'adopted_live_authority': False, 'persisted_observations_are_not_live_authority': True}

    def shutdown(self):
        return [self.release(r['lease_ref']) for r in list(self.records.values()) if not r['released']]
