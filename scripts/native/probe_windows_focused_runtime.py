"""Actual portable Windows runtime imports; no GUI/model/capture/input calls."""
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import site
import struct
import sys
import zipfile


def main():
    root = Path(sys.argv[1]).resolve()
    if (os.name != 'nt' or root.drive.upper() not in {'D:', 'E:'}
            or sys.version_info[:2] != (3, 10) or struct.calcsize('P') != 8
            or not Path(sys.executable).resolve().is_relative_to(root)):
        raise RuntimeError('ACTUAL_PORTABLE_WINDOWS_X64_PYTHON310_D_OR_E_REQUIRED')
    cache = root / 'probe-cache'
    cache.mkdir(exist_ok=True)
    for name in ['TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA', 'XDG_CACHE_HOME', 'MPLCONFIGDIR']:
        os.environ[name] = str(cache)
    # Only frozen product-owned .pth files are processed, no user's site.
    package_root = root / 'runtime/site-packages'
    site.addsitedir(str(package_root))
    closure = json.loads((root / 'dependency-closure-windows310.json').read_bytes())
    if len(closure['packages']) != 77 or any(p['name'] == 'claude-agent-sdk' for p in closure['packages']):
        raise RuntimeError('FOCUSED_77_PACKAGE_NO_LEGACY_LOOP_REQUIRED')
    versions, installed_hashes = [], 0
    skipped = []
    for item in closure['packages']:
        dist = importlib.metadata.distribution(item['name'])
        if dist.version != item['version']:
            raise RuntimeError('EXACT_INSTALLED_VERSION_CHANGED_' + item['name'])
        if not Path(dist.locate_file('')).resolve().is_relative_to(package_root):
            raise RuntimeError('DISTRIBUTION_OUTSIDE_OWN_RUNTIME_' + item['name'])
        wheel = root / 'toolchain/runtime-wheels' / item['filename']
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('FROZEN_WHEEL_BYTES_CHANGED')
        with zipfile.ZipFile(wheel) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.endswith(('.py', '.pyd', '.dll')):
                    continue
                member = info.filename
                parts = member.split('/')
                if parts[0].endswith('.data'):
                    if parts[1] in {'purelib', 'platlib'}:
                        member = '/'.join(parts[2:])
                    else:
                        skipped.append({'package': item['name'], 'member': member, 'reason': 'NON_ENGINE_INSTALL_SCHEME_OR_REWRITTEN_SCRIPT'})
                        continue
                target = package_root / member
                if not target.is_file() or hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(archive.read(info)).digest():
                    raise RuntimeError('INSTALLED_ENGINE_BYTES_CHANGED_' + item['name'] + ':' + member)
                installed_hashes += 1
        versions.append({'name': item['name'], 'version': dist.version, 'wheel_sha256': item['sha256']})
    modules = ['fastapi', 'uvicorn', 'pydantic', 'pydantic_core', 'jsonschema', 'httpx',
        'mcp', 'docx', 'docxtpl', 'lxml.etree', 'pptx', 'pypdf', 'PIL.Image', 'numpy',
        'cv2', 'onnxruntime', 'rapidocr', 'windows_capture', 'win32api', 'comtypes', 'cua_driver']
    imported = []
    for name in modules:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(package_root):
            raise RuntimeError('IMPORT_OUTSIDE_FROZEN_RUNTIME_' + name)
        imported.append({'module': name, 'relative_path': str(path.relative_to(package_root))})
    from pydantic import BaseModel, Field
    class Fixture(BaseModel):
        revision: int = Field(ge=1)
        text: str
    actual = Fixture(revision=1, text='真实 Windows 中文与 emoji ✅')
    if Fixture.model_validate_json(actual.model_dump_json()).text != actual.text:
        raise RuntimeError('PYDANTIC_BINARY_ROUNDTRIP_FAILED')
    from pip._internal.operations.check import check_package_set, create_package_set_from_installed
    package_set, parsing_problems = create_package_set_from_installed()
    missing, conflicts = check_package_set(package_set)
    if parsing_problems or missing or conflicts:
        raise RuntimeError('NATIVE_PIP_CHECK_FAILED_' + repr((missing, conflicts)))
    print(json.dumps({'schema': 'eeas.windows-focused-runtime-probe.v1',
        'status': 'PASS_ACTUAL_WINDOWS_RUNTIME_IMPORTS_AND_PIP_CHECK', 'os': platform.platform(),
        'python': platform.python_version(), 'executable': sys.executable,
        'target_lock_sha256': hashlib.sha256((root / 'requirements-focused-native-windows310.lock').read_bytes()).hexdigest(),
        'frozen_versions': versions, 'installed_engine_hashes_checked': installed_hashes,
        'non_engine_scheme_members': skipped, 'imports': imported, 'pydantic_unicode_roundtrip': 'PASS',
        'pip_check': 'PASS', 'legacy_agent_sdk_installed': False, 'gui_created': False,
        'model_call': False, 'native_appbar_registration': 'NOT_RUN_ROOT_ONLY',
        'ocr_model_inference': 'NOT_RUN_BY_THIS_IMPORT_PROBE', 'desktop_capture_or_input': 'NOT_RUN'}, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
