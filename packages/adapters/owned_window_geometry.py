"""Read only a caller-validated owned HWND, in a restored thread DPI context.

No window placement, region mutation, focus, subprocess or disk operations.
The authority supplies its private Main marker; this is not a public HWND API.
"""
from contextlib import contextmanager
import ctypes as ct
import os

from packages.domain.store import Conflict


COORDINATES = 'PHYSICAL_SCREEN_PIXELS_THREAD_PMV2'


class _RECT(ct.Structure):
    _fields_ = [(name, ct.c_int32) for name in ('left', 'top', 'right', 'bottom')]


class _POINT(ct.Structure):
    _fields_ = [('x', ct.c_int32), ('y', ct.c_int32)]


def _rect(value):
    result = {'x': int(value.left), 'y': int(value.top),
              'width': int(value.right - value.left), 'height': int(value.bottom - value.top)}
    if (min(result['width'], result['height']) < 1
            or max(result['width'], result['height']) > 1 << 20):
        raise Conflict('OWN_MAIN_GEOMETRY_INVALID')
    return result


class Win32OwnedWindowGeometry:
    def __init__(self, *, libraries=None):
        if libraries is None:
            if os.name != 'nt':
                raise OSError('WINDOWS_OWN_GEOMETRY_UNAVAILABLE')
            libraries = {'user32': ct.WinDLL('user32', use_last_error=True)}
            try:
                libraries['dwmapi'] = ct.WinDLL('dwmapi', use_last_error=True)
            except OSError:
                pass
        self.user, self.dwm = libraries['user32'], libraries.get('dwmapi')
        H, B = ct.c_void_p, ct.c_int32
        for name, result, args in (
            ('SetThreadDpiAwarenessContext', H, [H]),
            ('GetWindowRect', B, [H, ct.POINTER(_RECT)]),
            ('GetClientRect', B, [H, ct.POINTER(_RECT)]),
            ('ClientToScreen', B, [H, ct.POINTER(_POINT)]),
            ('GetDpiForWindow', ct.c_uint32, [H]),
            ('IsIconic', B, [H]), ('IsZoomed', B, [H]),
        ):
            function = getattr(self.user, name)
            function.restype, function.argtypes = result, args
        if self.dwm is not None:
            self.dwm.DwmGetWindowAttribute.restype = ct.c_int32
            self.dwm.DwmGetWindowAttribute.argtypes = [H, ct.c_uint32, H, ct.c_uint32]

    @staticmethod
    def _require(value, reason):
        if not value:
            raise Conflict(reason)
        return value

    @contextmanager
    def physical_context(self):
        previous = self._require(self.user.SetThreadDpiAwarenessContext(ct.c_void_p(-4)),
                                 'OWN_MAIN_PHYSICAL_DPI_CONTEXT_UNAVAILABLE')
        try:
            yield
        finally:
            self._require(self.user.SetThreadDpiAwarenessContext(previous),
                          'OWN_MAIN_DPI_CONTEXT_RESTORE_FAILED')

    def _sample(self, hwnd):
        window, client, visible, origin = _RECT(), _RECT(), _RECT(), _POINT()
        self._require(self.user.GetWindowRect(hwnd, ct.byref(window)), 'OWN_MAIN_BOUNDS_UNAVAILABLE')
        self._require(self.user.GetClientRect(hwnd, ct.byref(client)), 'OWN_MAIN_CLIENT_UNAVAILABLE')
        self._require(self.user.ClientToScreen(hwnd, ct.byref(origin)), 'OWN_MAIN_CLIENT_ORIGIN_UNAVAILABLE')
        dpi = int(self._require(self.user.GetDpiForWindow(hwnd), 'OWN_MAIN_DPI_UNAVAILABLE'))
        if not 48 <= dpi <= 9600:
            raise Conflict('OWN_MAIN_DPI_INVALID')
        client.left, client.right = origin.x, origin.x + client.right - client.left
        client.top, client.bottom = origin.y, origin.y + client.bottom - client.top
        dwm_ok = (self.dwm is not None and
                  self.dwm.DwmGetWindowAttribute(hwnd, 9, ct.byref(visible), ct.sizeof(visible)) == 0)
        return {'coordinate_space': COORDINATES, 'window_bounds_physical': _rect(window),
                'client_bounds_physical': _rect(client),
                'dwm_visible_bounds_physical': _rect(visible) if dwm_ok else None,
                'dwm_status': 'AVAILABLE' if dwm_ok else 'UNAVAILABLE', 'dpi': dpi,
                'minimized': bool(self.user.IsIconic(hwnd)), 'maximized': bool(self.user.IsZoomed(hwnd))}

    def read(self, marker, validate):
        before_owner = validate(marker)
        with self.physical_context():
            before = self._sample(marker['window_id'])
            after = self._sample(marker['window_id'])
        after_owner = validate(marker)
        if before != after or before_owner != after_owner:
            raise Conflict('OWN_MAIN_GEOMETRY_CHANGED_DURING_READ')
        return before
