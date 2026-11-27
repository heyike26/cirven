"""Frozen local Windows tooling used by the original OpenCode process."""
import hashlib
import json
import os
from pathlib import Path

WIN_RG_SHA = 'decdd4992f3f1b9a5ef9898f1b40ab16886d579d6516b4efd3d5eaa19364e408'


def support_environment(environment, *, platform=None, root=None):
    if (os.name if platform is None else platform) != 'nt':
        return environment
    support = Path(root or os.environ.get('EEAS_OPENCODE_SUPPORT_ROOT') or
        Path(__file__).resolve().parents[2] / 'runtime/opencode-support')
    if not support.is_absolute() or any(p.is_symlink() for p in (support, *support.parents)):
        raise ValueError('Absolute frozen OpenCode support directory required')
    manifest = support / 'SUPPORT_MANIFEST.json'
    if not manifest.is_file() or manifest.stat().st_size > 65536:
        raise ValueError('Offline OpenCode tooling is missing; restore the complete package')
    selected = json.loads(manifest.read_bytes())
    if (selected.get('schema') != 'eeas.opencode-support.v1'
            or selected.get('version') != '15.1.0' or selected.get('platform') != 'windows-x64'):
        raise ValueError('Frozen Windows OpenCode tooling version differs')
    executable = support / 'rg.exe'
    if (executable.is_symlink() or not executable.is_file()
            or executable.stat().st_size != 4266496
            or hashlib.sha256(executable.read_bytes()).hexdigest() != WIN_RG_SHA):
        raise ValueError('Frozen Windows ripgrep bytes differ')
    # Python child output must match the original tool's UTF-8 decoding.
    # Preserve every explicit caller value, including 0 or an empty string.
    environment.setdefault('PYTHONUTF8', '1')
    # PowerShell uses PATHEXT to recognize native executables in this
    # otherwise isolated environment. Preserve caller/host extensions.
    if not environment.get('PATHEXT', '').strip():
        host_pathext = os.environ.get('PATHEXT', '')
        environment['PATHEXT'] = (host_pathext if host_pathext.strip()
            else '.COM;.EXE;.BAT;.CMD')
    # Original OpenCode's which() checks PATH before its auto-download branch.
    # Supply the verified executable without widening native tool permissions.
    environment['PATH'] = str(support) + os.pathsep + environment.get('PATH', '')
    return environment
