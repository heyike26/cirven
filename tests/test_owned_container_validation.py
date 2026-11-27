"""Original native-container schema must reject before window authority lookup.

Linux exercises only the pre-native rejection boundary. Windows instantiates
the actual Win32 DLL adapter; it never obtains a valid HWND, creates a window,
reserves an AppBar, calls SetWindowPos or manufactures a successful observation.
"""
import copy
import ctypes
import os

import pytest

from packages.adapters.windows_owned_region import WindowsOwnedRegion
from packages.adapters.windows_desktop_region import DesktopRegionError, Win32RegionAPI


REF = {'id': 'f' * 64, 'revision': 1, 'sha256': 'b' * 64}


def original():
    return {'window_bounds': {'x': -2880, 'y': 0, 'width': 2880, 'height': 1840},
        'client_bounds_window_physical': {'x': 0, 'y': 0, 'width': 2880, 'height': 1840}, 'dpi': 192}


def invalid_observations():
    values = [('not_mapping', None), ('scalar_mapping', 192), ('array_mapping', [])]
    for key in ['window_bounds', 'client_bounds_window_physical', 'dpi']:
        value = original(); value.pop(key); values.append(('missing_' + key, value))
    for key in ['hwnd', 'owner_pid', 'renderer_geometry', 'unexpected']:
        values.append(('extra_top_' + key, original() | {key: 1}))
    for dpi in [True, False, 192.0, '192', None, 0, -1, float('nan'), float('inf'), 1 << 32]:
        values.append(('invalid_dpi_' + str(dpi), original() | {'dpi': dpi}))
    for group in ['window_bounds', 'client_bounds_window_physical']:
        for key in ['x', 'y', 'width', 'height']:
            value = original(); value[group].pop(key); values.append(('missing_' + group + '_' + key, value))
            for replacement in [True, False, float(original()[group][key]), '0', None]:
                value = original(); value[group][key] = replacement
                values.append(('invalid_' + group + '_' + key + '_' + repr(replacement), value))
        value = original(); value[group]['extra'] = 0; values.append(('extra_' + group, value))
        for replacement in [None, [], 'not-a-rectangle']:
            value = original(); value[group] = replacement; values.append(('not_rect_' + group + '_' + repr(replacement), value))
        for key, number in [('width', 0), ('height', -1), ('width', 65537), ('height', 65537), ('x', 65537), ('y', -65537)]:
            value = original(); value[group][key] = number; values.append(('range_' + group + '_' + key + '_' + str(number), value))
    for key, replacement in [('x', 1), ('y', 1), ('x', -1), ('y', -1), ('width', 2879), ('height', 1839)]:
        value = original(); value['client_bounds_window_physical'][key] = replacement
        values.append(('non_frameless_client_' + key + '_' + str(replacement), value))
    # Python equality would consider False==0 / 1.0==1 / True==1. These
    # packets must never reach _marker just because equal-shaped dicts compare.
    value = original(); value['client_bounds_window_physical']['x'] = False
    values.append(('boolean_zero_client_alias', value))
    value = original(); value['window_bounds']['width'] = 1; value['client_bounds_window_physical']['width'] = True
    values.append(('boolean_one_client_alias', value))
    return values


def schema_boundary():
    touched = []
    def forbidden_window_lookup(_reference):
        touched.append('WINDOW_AUTHORITY_LOOKUP')
        raise AssertionError('Malformed metadata reached native window authority before schema rejection')
    if os.name == 'nt':
        # Real DLL function objects/ABI, never a fake native-success object.
        native = Win32RegionAPI()
        adapter = WindowsOwnedRegion(surface_authority=forbidden_window_lookup, native=native)
    else:
        # Bypass native constructor only; execute the real schema method.
        # There is deliberately no .native object or successful window lookup.
        adapter = WindowsOwnedRegion.__new__(WindowsOwnedRegion)
        adapter.authority = forbidden_window_lookup
    return adapter, touched


CASES = invalid_observations()


@pytest.mark.parametrize('label,metadata', CASES, ids=[label for label, _ in CASES])
def test_invalid_original_metadata_rejected_before_any_native_window_lookup(label, metadata):
    adapter, touched = schema_boundary()
    preserved = copy.deepcopy(metadata)
    with pytest.raises(DesktopRegionError): adapter.restore_container(REF, metadata)
    assert touched == []
    # Neither validation nor rejection rewrites the original source packet.
    if isinstance(metadata, dict):
        assert metadata.keys() == preserved.keys()
        if metadata.get('dpi') != metadata.get('dpi'):  # NaN remains NaN
            assert preserved['dpi'] != preserved['dpi']
        else:
            assert metadata == preserved


@pytest.mark.skipif(os.name != 'nt', reason='Actual Win32 system DLL ABI requires Windows; no Linux native-success substitution')
def test_actual_windows_dll_has_exact_owned_window_position_and_region_abi_without_calling_it():
    adapter, touched = schema_boundary()
    assert adapter.native.user.SetWindowPos.restype is ctypes.c_int32
    assert len(adapter.native.user.SetWindowPos.argtypes) == 7
    assert adapter.native.user.SetWindowRgn.restype is ctypes.c_int32
    assert len(adapter.native.user.SetWindowRgn.argtypes) == 3
    assert touched == []
