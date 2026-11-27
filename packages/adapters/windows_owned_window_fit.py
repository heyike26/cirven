"""Fit only the authenticated host's own window to the actual monitor rcWork.

Electron's integer DIP bounds can produce a one-pixel outer size discrepancy.
Normalize the owned native window, then verify it; never relax region proofs.
"""
import ctypes as ct

from packages.adapters.windows_desktop_region import DesktopRegionError, _RECT, _POINT


class WindowsOwnedWindowFit:
    def __init__(self, native, monitor):
        self.native, self.monitor = native, monitor
        self.user = native.user
        self.user.SetWindowPos.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_int32,
            ct.c_int32, ct.c_int32, ct.c_int32, ct.c_uint32]
        self.user.SetWindowPos.restype = ct.c_int32
        self.user.GetClientRect.argtypes = [ct.c_void_p, ct.POINTER(_RECT)]
        self.user.GetClientRect.restype = ct.c_int32
        self.user.ClientToScreen.argtypes = [ct.c_void_p, ct.POINTER(_POINT)]
        self.user.ClientToScreen.restype = ct.c_int32

    def fit(self, marker):
        before = self.native.validate(marker)
        monitor = self.monitor(marker)
        target = monitor['work_area']
        with self.native.physical_context(marker):
            if not self.user.SetWindowPos(marker['hwnd'], None, target['x'], target['y'],
                    target['width'], target['height'], 0x0004 | 0x0010 | 0x0200):
                raise DesktopRegionError('OWN_WINDOW_WORKAREA_FIT_FAILED')
            client, origin = _RECT(), _POINT(0, 0)
            if not self.user.GetClientRect(marker['hwnd'], ct.byref(client)) or not self.user.ClientToScreen(marker['hwnd'], ct.byref(origin)):
                raise DesktopRegionError('OWN_WINDOW_CLIENT_GEOMETRY_UNAVAILABLE')
        actual = self.native.validate(marker)
        if (actual['window_bounds'] != target or self.monitor(marker) != monitor
                or (origin.x, origin.y) != (target['x'], target['y'])
                or (client.left, client.top, client.right, client.bottom) != (0, 0, target['width'], target['height'])):
            raise DesktopRegionError('OWN_WINDOW_WORKAREA_FIT_NOT_VERIFIED')
        return {'status': 'OWN_WINDOW_FIT_VERIFIED', 'before': before,
                'actual': actual, 'monitor': monitor, 'client_offset': {'x': 0, 'y': 0}}
