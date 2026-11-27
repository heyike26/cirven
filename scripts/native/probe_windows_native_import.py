"""No-GUI Windows import/ABI probe; run with portable Python -B -I -S."""
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import platform
import struct
import sys


def main():
    root = Path(sys.argv[1]).resolve()
    if os.name != 'nt' or root.drive.upper() not in {'D:', 'E:'}:
        raise RuntimeError('WINDOWS_D_OR_E_RUNTIME_REQUIRED')
    if sys.version_info[:2] != (3, 10) or struct.calcsize('P') != 8:
        raise RuntimeError('ACTUAL_WINDOWS_X64_PYTHON310_REQUIRED')
    if not Path(sys.executable).resolve().is_relative_to(root):
        raise RuntimeError('PORTABLE_PYTHON_OUTSIDE_SELECTED_RUNTIME')
    sys.path[:0] = [str(root), str(root / 'runtime/site-packages')]
    manifest = root / 'toolchain/components/cua-driver-shell-v014/MANIFEST.json'
    data = json.loads(manifest.read_bytes())
    checked = []
    for item in data['wheels']:
        dist = importlib.metadata.distribution(item['name'])
        if dist.version != item['version']:
            raise RuntimeError('INSTALLED_VERSION_CHANGED')
        for member, sha in item['engine_files'].items():
            path = Path(dist.locate_file(member)).resolve()
            if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise RuntimeError('INSTALLED_ENGINE_BYTES_CHANGED')
        checked.append({'name': item['name'], 'version': item['version'], 'engine_members': len(item['engine_files'])})
    import cua_driver as cua
    from packages.adapters.windows_shell_com import shell_api_check
    from packages.adapters.windows_desktop_region import Win32RegionAPI
    from packages.adapters.windows_appbar_lease import Win32AppBarAPI, APPBARDATA, MONITORINFO
    ref = {'root': str(root), 'manifest_path': str(manifest.relative_to(root)).replace('\\', '/'),
           'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
    shell = shell_api_check(ref)
    # Bind exported stock DLL symbols only. No HWND/COM browser/session is made.
    Win32RegionAPI()
    Win32AppBarAPI()
    names = ['CuaDriver', 'CuaDriverSession', 'DriverOptions', 'ConfiguredDriverOptions',
             'TrustedSessionOptions', 'GetDesktopStateInput', 'GetWindowStateInput', 'ClickInput']
    signatures = {name: str(inspect.signature(getattr(cua, name))) for name in names}
    for name in ['get_desktop_state', 'get_window_state', 'click', 'press_key', 'type_text', 'close']:
        signatures['CuaDriverSession.' + name] = str(inspect.signature(getattr(cua.CuaDriverSession, name)))
    receipt = {'schema': 'eeas.windows-native-import-probe.v1', 'status': 'PASS_NATIVE_DLL_IMPORT_AND_API_DECLARATIONS',
        'os': platform.platform(), 'python': platform.python_version(), 'executable': sys.executable,
        'pointer_bits': struct.calcsize('P') * 8, 'frozen_installed_engines': checked,
        'cua_version': cua.__version__, 'cua_signatures': signatures, 'shell_api': shell,
        'region_api': 'STOCK_DLL_EXPORTS_BOUND_ONLY', 'appbar_api': 'STOCK_DLL_EXPORTS_BOUND_ONLY',
        'appbar_struct_bytes': __import__('ctypes').sizeof(APPBARDATA),
        'monitor_struct_bytes': __import__('ctypes').sizeof(MONITORINFO),
        'gui_created': False, 'driver_created': False,
        'session_created': False, 'model_call': False, 'desktop_capture': 'NOT_RUN', 'desktop_input': 'NOT_RUN',
        'native_human_interaction': 'NOT_RUN_ROOT_ONLY'}
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
