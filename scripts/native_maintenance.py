"""Verified, separate Windows maintenance entry; no App, Agent or data startup."""
from pathlib import Path
import importlib.util
import os
import subprocess

# Embedded Python's isolated path does not promise a scripts namespace or the
# entry script directory on sys.path. Load the exact sibling, as recovery does.
_spec = importlib.util.spec_from_file_location('cirven_maintenance_bootstrap',
    Path(__file__).with_name('native_package_bootstrap.py'))
_boot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_boot)
StartupCancelled, StartupProgress, verify_package = _boot.StartupCancelled, _boot.StartupProgress, _boot.verify_package


def main():
    if os.name != 'nt':
        raise OSError('Cirven maintenance requires Windows')
    root = Path(__file__).resolve().parents[1]
    progress = StartupProgress(enabled=True)
    try:
        verify_package(root, progress=progress.update)
    except StartupCancelled:
        return 0
    except Exception:
        import ctypes
        text = ('安装校验未通过，请使用完整的 Cirven 安装包。'
            if progress.copy.locale == 'zh-CN' else 'Installation verification failed. Use a complete Cirven package.')
        ctypes.windll.user32.MessageBoxW(None, text, 'Cirven', 0x10)
        return 1
    finally:
        progress.close()
    # Do not create user-data or activate runtime_environment: a fresh target
    # must remain fresh until the user explicitly restores it.
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    # The verifier exits after handing off the native window. Keeping a package
    # Python process waiting here would correctly trip recovery's stopped guard.
    subprocess.Popen([str(powershell), '-NoLogo', '-NoProfile', '-STA',
        '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File',
        str(root / 'scripts/native_maintenance.ps1'), '-Installation', str(root),
        '-Language', progress.copy.locale], cwd=root,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
