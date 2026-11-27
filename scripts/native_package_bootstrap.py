"""Relative Windows package launcher and shared Python-child entry point.

Only stdlib is imported before payload checks. It does not install/download,
read ambient model credentials, create a model loop, or certify Windows/Pro.
"""
from __future__ import annotations
import argparse
import asyncio
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import runpy
import site
import struct
import subprocess
import sys
import threading
import time
import traceback

MANIFEST = 'PACKAGE_MANIFEST.json'
SCHEMA = 'eeas.native-companion-package.v1'
MCP_MODULES = {'packages.desktop.mcp_server', 'packages.planning.mcp_server', 'packages.planning.discovery_mcp_server', 'packages.adapters.opencode_native_review'}
MODULES = MCP_MODULES | {'apps.api'}
SECRET_ENV = {'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'CHATGPT_ACCESS_TOKEN',
    'EEAS_OPENCODE_PROFILE', 'EEAS_OPENCODE_API_KEY', 'EEAS_OPENCODE_SUPPORT_ROOT',
    'PYTHONPATH', 'PYTHONHOME', 'COLOREDLOGS_AUTO_INSTALL', 'EEAS_STARTUP_TIMING'}


class PackageError(ValueError):
    pass


class StartupCancelled(PackageError):
    """The user cancelled this launcher before Main was started."""


class StartupTiming:
    """Bounded diagnostic facts only; no protocol output, messages or filenames.

    API stderr is already owned by Main's controller log. GUI/pythonw records
    remain in memory until successful verification and environment setup have
    established the existing installation-private log directory.
    """
    def __init__(self, *, mode, stream=None, clock=None, started=None):
        if mode not in {'API', 'ELECTRON'}:
            raise ValueError('An explicit startup timing mode is required')
        self.mode, self.stream = mode, stream
        self.clock = time.perf_counter_ns if clock is None else clock
        self.started = self.clock() if started is None else started
        self.records = []
        self.path = None
        self.write_error = None

    def record(self, phase, status, *, started=None, duration_ns=None, **facts):
        now = self.clock()
        row = {'schema':'eeas.startup-timing.v1', 'event':'STARTUP_PHASE',
            'component':'PACKAGE_BOOTSTRAP', 'pid':os.getpid(), 'mode':self.mode,
            'phase':phase, 'status':status, 'finished_monotonic_ns':now,
            'elapsed_ms':round(max(0, now-self.started)/1_000_000, 3)}
        if started is not None:
            row['started_monotonic_ns'] = started
            duration_ns = max(0, now-started)
        if duration_ns is not None:
            row['duration_ms'] = round(max(0, duration_ns)/1_000_000, 3)
        row.update(facts)
        if len(self.records) >= 32:
            return
        self.records.append(row)
        raw = json.dumps(row, ensure_ascii=True, separators=(',', ':')) + '\n'
        if self.stream is not None:
            try:
                self.stream.write(raw)
                self.stream.flush()
            except (OSError, UnicodeError, ValueError, AttributeError):
                self.write_error = 'STREAM_UNAVAILABLE'
        if self.path is not None:
            try:
                with self.path.open('a', encoding='utf-8') as output:
                    output.write(raw)
            except OSError:
                self.write_error = 'LOG_UNAVAILABLE'

    def persist_verified(self, root, *, data_dir=None):
        # Called only after verify_package and runtime_environment succeeded.
        # Timing IO must never replace the original startup/verification result.
        if self.path is not None:
            return
        try:
            _data, paths = private_paths(root, data_dir=data_dir)
            import uuid
            target = paths['logs'] / ('startup-timing-' + uuid.uuid4().hex + '.jsonl')
            with target.open('x', encoding='utf-8') as output:
                output.write(''.join(json.dumps(row, ensure_ascii=True, separators=(',', ':')) + '\n'
                    for row in self.records))
            target.chmod(0o600)
            self.path = target
        except (OSError, ValueError):
            self.write_error = 'LOG_UNAVAILABLE'


def _timed_call(timing, phase, call, *args, **kwargs):
    if timing is None:
        return call(*args, **kwargs)
    started = timing.clock()
    try:
        result = call(*args, **kwargs)
    except BaseException as error:
        timing.record(phase, 'CANCELLED' if isinstance(error, (StartupCancelled, KeyboardInterrupt)) else 'FAILED',
            started=started, error_type=type(error).__name__)
        raise
    timing.record(phase, 'COMPLETED', started=started)
    return result


class StartupCopy:
    """Bounded local JSON only: no application code runs before verification."""
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
        def local_json(name, maximum):
            try:
                path = self.root / name
                if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.root.resolve()):
                    return {}
                with path.open('rb') as stream:
                    raw = stream.read(maximum + 1)
                if len(raw) > maximum:
                    return {}
                value = json.loads(raw.decode('utf-8'))
                return value if isinstance(value, dict) else {}
            except (OSError, ValueError, UnicodeError):
                return {}
        brand = local_json('packages/branding/product.json', 4096).get('display_name')
        self.brand = brand if isinstance(brand, str) and 0 < len(brand) <= 64 and all(ord(c) >= 32 for c in brand) else 'Agent'
        catalog = local_json('packages/i18n/bootstrap.json', 131072)
        self.messages = catalog.get('messages', {}) if catalog.get('schema_version') == 1 else {}
        if not isinstance(self.messages, dict):
            self.messages = {}
        preference = local_json('user-data/desktop-preferences.json', 4096)
        preference = preference.get('ui_language') if preference.get('schema') == 'eeas.desktop-preferences.v1' else None
        if not isinstance(preference, str) or preference not in {'zh-CN', 'en'}:
            import locale
            system = locale.getlocale()[0] or ''
            if os.name == 'nt':
                try:
                    import ctypes
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.GetUserDefaultUILanguage.restype = ctypes.c_ushort
                    system = locale.windows_locale.get(kernel.GetUserDefaultUILanguage(), system)
                except (AttributeError, OSError):
                    pass
            preference = 'zh-CN' if system.lower().startswith('zh') else 'en'
        self.locale = preference

    def __call__(self, key, **params):
        # These minimal English recovery strings remain available when the
        # package is incomplete. Normal text comes from the shared catalog.
        recovery = {'consoleStarting':'Starting {brand}: checking installation files…',
            'startingTitle':'{brand} · Starting', 'verifyBody':'Checking installation files…\nThe first start can take a minute.',
            'cancel':'Cancel startup', 'cancelledBeforeMain':'Startup was cancelled before the main window started.',
            'verifyConsole':'{brand}: checking installation files ({completed} / {total})',
            'verifyCount':'{completed} / {total} files checked', 'verified':'{brand}: installation verified. Opening the app…',
            'failed':'{brand} could not start.', 'diagnostic':'Diagnostic file: {path}', 'failedTitle':'{brand} · Startup failed',
            'abnormalExit':'{brand} exited unexpectedly (exit code {code}). Original startup log: {path}',
            'cancelled':'{brand} startup was cancelled.', 'cliFailure':'{brand} package startup failed'}
        row = self.messages.get(key)
        value = row.get(self.locale) if isinstance(row, dict) else None
        if not isinstance(value, str) or len(value) > 8192:
            value = recovery.get(key, 'Startup status unavailable')
        arguments = {'brand':self.brand, **params}
        return re.sub(r'\{([A-Za-z][A-Za-z0-9_]*)\}',
            lambda match:str(arguments.get(match.group(1), match.group(0))), value)


class StartupProgress:
    """Optional first-process feedback; never used by API/MCP child modes.

    The native window and its message pump stay in this thread. No package code,
    third-party module, network, or desktop configuration is loaded for it.
    """
    def __init__(self, *, enabled, root=None):
        self.enabled = enabled
        self.copy = StartupCopy(root)
        self.cancelled = False
        self._closed = False
        self._last_console = None
        self._last_native = None
        self._window = None
        self._native_error = False
        if not enabled:
            return
        self._console(self.copy('consoleStarting'))
        if os.name == 'nt':
            try:
                self._open_native()
            except Exception:
                # No GUI support must not bypass verification or block console use.
                self._native_error = True
                self.close()
                self._closed = False

    @staticmethod
    def _console(message):
        stream = sys.stderr
        if stream is None:
            return
        try:
            print(message, file=stream, flush=True)
        except (OSError, UnicodeError, ValueError):
            # pythonw has no console; its native feedback remains available.
            pass

    def _open_native(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._user32 = ctypes.WinDLL('user32', use_last_error=True)
        self._kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
        handle, uint, wide = wintypes.HANDLE, wintypes.UINT, wintypes.LPCWSTR
        self._window_proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, handle, uint, ctypes.c_size_t, ctypes.c_ssize_t)
        class WindowClass(ctypes.Structure):
            _fields_ = [('style', uint), ('lpfnWndProc', self._window_proc_type),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', handle), ('hIcon', handle), ('hCursor', handle),
                ('hbrBackground', handle), ('lpszMenuName', wide), ('lpszClassName', wide)]
        self._user32.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        self._user32.RegisterClassW.restype = wintypes.ATOM
        self._user32.CreateWindowExW.argtypes = [wintypes.DWORD, wide, wide, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, handle, handle, handle, ctypes.c_void_p]
        self._user32.CreateWindowExW.restype = handle
        self._user32.DefWindowProcW.argtypes = [handle, uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self._user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self._user32.DestroyWindow.argtypes = [handle]
        self._user32.UnregisterClassW.argtypes = [wide, handle]
        self._user32.SetWindowTextW.argtypes = [handle, wide]
        self._user32.ShowWindow.argtypes = [handle, ctypes.c_int]
        self._user32.UpdateWindow.argtypes = [handle]
        self._user32.SendMessageW.argtypes = [handle, uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self._user32.SendMessageW.restype = ctypes.c_ssize_t
        self._user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), handle, uint, uint, uint]
        self._user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self._kernel32.GetModuleHandleW.argtypes = [wide]
        self._kernel32.GetModuleHandleW.restype = handle
        self._instance = self._kernel32.GetModuleHandleW(None)
        self._class_name = 'EEAS.Startup.' + str(os.getpid()) + '.' + str(id(self))
        def window_proc(window, message, wparam, lparam):
            if message == 0x0010 or (message == 0x0111 and (wparam & 0xffff) == 2):
                self.cancelled = True
                return 0
            return self._user32.DefWindowProcW(window, message, wparam, lparam)
        self._window_proc = self._window_proc_type(window_proc)
        window_class = WindowClass(0, self._window_proc, 0, 0, self._instance,
            None, None, handle(6), None, self._class_name)
        self._registered = bool(self._user32.RegisterClassW(ctypes.byref(window_class)))
        if not self._registered:
            raise OSError('Cannot register startup window')
        self._window = self._user32.CreateWindowExW(0, self._class_name, self.copy('startingTitle'),
            0x00c80000, -2147483648, -2147483648, 484, 210, None, None, self._instance, None)
        if not self._window:
            raise OSError('Cannot open startup window')
        child_style = 0x50000000
        self._label = self._user32.CreateWindowExW(0, 'STATIC',
            self.copy('verifyBody'), child_style,
            24, 22, 430, 52, self._window, None, self._instance, None)
        self._bar = self._user32.CreateWindowExW(0, 'STATIC', '', child_style | 0x1000,
            24, 85, 430, 25, self._window, None, self._instance, None)
        self._cancel = self._user32.CreateWindowExW(0, 'BUTTON', self.copy('cancel'), child_style | 0x10000,
            310, 126, 142, 32, self._window, handle(2), self._instance, None)
        self._gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wide]
        self._gdi32.CreateFontW.restype = handle
        self._gdi32.DeleteObject.argtypes = [handle]
        self._font = self._gdi32.CreateFontW(-17, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, 'Segoe UI')
        if self._font:
            for control in (self._label, self._bar, self._cancel):
                self._user32.SendMessageW(control, 0x0030, self._font, 1)
        self._message = wintypes.MSG()
        self._user32.ShowWindow(self._window, 1)
        self._user32.UpdateWindow(self._window)
        self._pump()

    def _pump(self):
        if self._window:
            while self._user32.PeekMessageW(self._ctypes.byref(self._message), self._window, 0, 0, 1):
                self._user32.TranslateMessage(self._ctypes.byref(self._message))
                self._user32.DispatchMessageW(self._ctypes.byref(self._message))

    def update(self, completed, total):
        if not self.enabled or self._closed:
            return
        self._pump()
        if self.cancelled:
            raise StartupCancelled(self.copy('cancelledBeforeMain'))
        now = time.monotonic()
        if self._last_console is None or completed == total or now - self._last_console >= 1:
            self._console(self.copy('verifyConsole', completed=completed, total=total))
            self._last_console = now
        if self._window and (self._last_native is None or completed == total or now - self._last_native >= 0.1):
            self._user32.SetWindowTextW(self._bar, self.copy('verifyCount', completed=completed, total=total))
            self._last_native = now

    def finish(self):
        if self.enabled:
            # Closing the progress window never closes any other application.
            self._pump()
            if self.cancelled:
                raise StartupCancelled(self.copy('cancelledBeforeMain'))
            self._console(self.copy('verified'))
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._window:
            self._user32.DestroyWindow(self._window)
            self._window = None
        if getattr(self, '_registered', False):
            self._user32.UnregisterClassW(self._class_name, self._instance)
            self._registered = False
        if getattr(self, '_font', None):
            self._gdi32.DeleteObject(self._font)
            self._font = None


def report_startup_failure(root, error):
    """Keep original failure evidence and make pythonw startup errors visible."""
    receipt = None
    try:
        # Never put a new diagnostic on C: even when an unsupported launch fails.
        if os.name != 'nt' or Path(root).drive.upper() in {'D:', 'E:'}:
            _data, paths = private_paths(root)
            import uuid
            receipt = paths['logs'] / ('startup-error-' + uuid.uuid4().hex + '.json')
            with receipt.open('x', encoding='utf-8') as stream:
                json.dump({'status':'STARTUP_FAILED', 'error':diagnostic_exception(error)}, stream,
                    ensure_ascii=False, indent=2)
                stream.write('\n')
    except Exception:
        # Evidence failure must not replace the original verification exception.
        receipt = None
    if os.name == 'nt':
        try:
            import ctypes
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            detail = type(error).__name__ + ': ' + str(error)[:1800]
            copy = StartupCopy(root)
            message = copy('failed') + '\n\n' + detail
            if receipt is not None:
                message += '\n\n' + copy('diagnostic', path=receipt)
            user32.MessageBoxW(None, message, copy('failedTitle'), 0x10)
        except Exception:
            pass
    return receipt


def sha(path, *, progress=None, on_read=None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(part)
            if on_read is not None:
                on_read(len(part))
            if progress is not None:
                progress()
    return value.hexdigest()


def relative_path(root, name):
    return _relative_path(root,name,canonical_root=None)


def _relative_path(root, name, *, canonical_root):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or any(ord(c) < 32 for c in name) or PurePosixPath(name).is_absolute()
            or PureWindowsPath(name).drive or str(PurePosixPath(name)) != name
            or any(part in {'.', '..'} for part in PurePosixPath(name).parts)):
        raise PackageError('Unsafe relative package member')
    target = root.joinpath(*PurePosixPath(name).parts)
    resolved = target.resolve()
    boundary = root.resolve() if canonical_root is None else canonical_root
    # Only verify_package supplies its initial canonical boundary. Each target
    # is still resolved and every ancestor inspected afresh for every member;
    # a redirected root cannot move that fixed verification boundary.
    if not resolved.is_relative_to(boundary) or any(p.is_symlink() for p in (target, *target.parents)):
        raise PackageError('Package member escaped its installation directory')
    return target


def read_manifest(root):
    path = relative_path(root, MANIFEST)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise PackageError('Package manifest missing or oversized')
    value = json.loads(path.read_bytes())
    if value.get('schema') != SCHEMA or value.get('target') != 'windows-x64-py310' or not isinstance(value.get('files'), list):
        raise PackageError('Unsupported package manifest/target')
    if value.get('state') != 'DEVELOPMENT_PREVIEW':
        raise PackageError('Package qualification state changed')
    return value


class _VerificationStopped(Exception):
    """An owned worker cooperatively stopped after another failure/cancel."""


def verify_plugin_closures(root, manifest, *, full=True, progress=None):
    """Bind the reviewed plugins' consumer manifests to the package inventory.

    Run after member verification: a hash-perfect inventory can still omit a
    file required by a second manifest. No directory scan, import or download
    is needed. Structural packages without these plugins remain structural.
    """
    root = Path(root).resolve()
    inventory = {row['path']: row for row in manifest['files']}
    prefix = 'toolchain/opencode-plugins/'
    plugins = ('dynamic-workflow', 'change-review', 'native-review-context', 'native-message-context')
    manifests = {}
    def tick():
        if progress is not None:
            progress()
    def record(name, expected=None):
        tick()
        path = relative_path(root, name)
        row = inventory.get(name)
        if row is None:
            raise PackageError('Plugin dependency is not in the hashed payload: ' + name)
        if expected is not None and (row['bytes'] != expected.get('bytes') or row['sha256'] != expected.get('sha256')):
            raise PackageError('Plugin dependency differs from its consumer manifest: ' + name)
        # Full/critical hashes have just been verified by verify_package.
        # Child startup must also hash any referenced member marked noncritical.
        if not full and not row.get('startup_critical') and sha(path, progress=tick) != row['sha256']:
            raise PackageError('Plugin dependency hash changed: ' + name)
        return path, row
    for plugin in plugins:
        base = prefix + plugin + '/'
        if not any(name.casefold().startswith(base) for name in inventory):
            continue
        name = base + 'manifest.json'
        path, pin = record(name)
        if pin['bytes'] > 262144:
            raise PackageError('Plugin consumer manifest is oversized')
        with path.open('rb') as stream:
            raw = stream.read(262145)
        if len(raw) != pin['bytes'] or hashlib.sha256(raw).hexdigest() != pin['sha256']:
            raise PackageError('Plugin consumer manifest bytes changed')
        try:
            value = json.loads(raw)
            rows = value['files']
            if value['id'] != 'eeas.' + plugin or not isinstance(rows, list) or not 0 < len(rows) <= 4096:
                raise ValueError()
            seen = set()
            for row in rows:
                member = row['path']
                # Validate relative to the plugin as well as the installation.
                relative_path(path.parent, member)
                if (member.casefold() in seen or type(row.get('bytes')) is not int or row['bytes'] < 0
                        or not isinstance(row.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
                    raise ValueError()
                seen.add(member.casefold())
                record(base + member, row)
            dependencies = value.get('dependencies', [])
            if not isinstance(dependencies, list) or len(dependencies) > 8:
                raise ValueError()
        except (ValueError, KeyError, TypeError) as error:
            if isinstance(error, PackageError):
                raise
            raise PackageError('Invalid bounded plugin consumer manifest: ' + name) from None
        manifests[name] = value
    for value in manifests.values():
        for dep in value.get('dependencies', []):
            if (not isinstance(dep, dict) or not isinstance(dep.get('path'), str)
                    or dep['path'] not in manifests):
                raise PackageError('Plugin transitive manifest is not in the reviewed payload')
            name = dep['path']
            if inventory[name]['sha256'] != dep.get('sha256'):
                raise PackageError('Plugin transitive manifest hash differs')
            member_prefix = dep.get('prefix')
            if not isinstance(member_prefix, str) or not member_prefix.endswith('/'):
                raise PackageError('Plugin dependency prefix is invalid')
            relative_path(root, member_prefix[:-1])
            rows = [row for row in manifests[name]['files'] if row['path'].startswith(member_prefix)]
            if not rows or not any(row['sha256'] == dep.get('entry_sha256') for row in rows):
                raise PackageError('Plugin dependency entry is missing')


def _verify_package_member(root, entry, *, full, checkpoint, clock):
    # Each job owns the complete fresh path/length/hash check and its read handle.
    # checkpoint is an Event check in workers, never the caller's UI callback.
    facts = dict(meta_ns=0, hash_ns=0, meta_count=0, meta_bytes=0,
        read_count=0, read_bytes=0, verified_count=0, meta_failed=False,
        hash_failed=False, error=None)
    def observed_read(size):
        facts['read_bytes'] += size
    try:
        checkpoint()
        mark = clock() if clock is not None else None
        try:
            name = entry['path']; target = _relative_path(root,name,canonical_root=root)
            if (not isinstance(entry.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', entry['sha256'])
                    or type(entry.get('bytes')) is not int or entry['bytes'] < 0):
                raise PackageError('Invalid payload hash/length')
            if not target.is_file() or target.stat().st_size != entry['bytes']:
                raise PackageError('Package payload missing or length changed: ' + name)
            facts['meta_count'] = 1; facts['meta_bytes'] = entry['bytes']
        except BaseException as error:
            facts['meta_failed'] = not isinstance(error, _VerificationStopped)
            raise
        finally:
            if clock is not None:
                facts['meta_ns'] += max(0, clock()-mark)
        if full or entry.get('startup_critical'):
            checkpoint()
            mark = clock() if clock is not None else None
            try:
                actual = sha(target, progress=checkpoint,
                    **({'on_read':observed_read} if clock is not None else {}))
                facts['read_count'] = 1
                if actual != entry['sha256']:
                    raise PackageError('Package payload hash changed: ' + name)
                facts['verified_count'] = 1
            except BaseException as error:
                facts['hash_failed'] = not isinstance(error, _VerificationStopped)
                raise
            finally:
                if clock is not None:
                    facts['hash_ns'] += max(0, clock()-mark)
    except BaseException as error:
        facts['error'] = error
    return facts


def verify_package(root, *, full=True, progress=None, timing=None, workers=4):
    """Complete verification with at most four owned member jobs in flight.

    workers=1 is a serial diagnostic/control, not a launcher preference. Both
    modes retain every path check and the same FULL/critical hash policy.
    Only this calling thread pumps progress and writes timing. Cancel/failure
    stops new jobs and joins all workers; an OS-blocked read cannot be forcibly
    interrupted by Python, so it cannot be advertised as bounded cancellation.
    """
    if type(workers) is not int or workers not in {1,4}:
        raise PackageError('Verification requires one or four bounded workers')
    root = Path(root).resolve()
    started = timing.clock() if timing is not None else None
    value = _timed_call(timing, 'MANIFEST_PARSE', read_manifest, root)
    seen = set()
    total = len(value['files'])
    meta_ns = hash_ns = meta_count = meta_bytes = read_count = read_bytes = verified_count = 0
    meta_failed = hash_failed = False
    failure = None
    completed = submitted = peak = 0
    stop = threading.Event()
    pool = None
    pending = set()
    def checkpoint():
        if stop.is_set():
            raise _VerificationStopped()
    def accept(facts):
        nonlocal meta_ns, hash_ns, meta_count, meta_bytes, read_count, read_bytes, verified_count
        nonlocal meta_failed, hash_failed, completed, failure
        # Include actual work finished during cleanup, not just ordered results.
        meta_ns += facts['meta_ns']; hash_ns += facts['hash_ns']
        meta_count += facts['meta_count']; meta_bytes += facts['meta_bytes']
        read_count += facts['read_count']; read_bytes += facts['read_bytes']
        verified_count += facts['verified_count']
        meta_failed |= facts['meta_failed']; hash_failed |= facts['hash_failed']
        if facts['error'] is None:
            completed += 1
        elif not isinstance(facts['error'], _VerificationStopped) and failure is None:
            failure = facts['error']; stop.set()
    def reserve(entry):
        nonlocal meta_failed
        try:
            name = entry['path']
            if isinstance(name, str):
                if name.casefold() in seen:
                    raise PackageError('Windows-aliased package members')
                seen.add(name.casefold())
        except BaseException:
            meta_failed = True
            raise
    def member(entry):
        facts = _verify_package_member(root,entry,full=full,checkpoint=checkpoint,
            clock=None if timing is None else timing.clock)
        if facts['error'] is not None:
            stop.set()
        return facts
    try:
        if progress is not None:
            progress(0,total)
        if workers == 1:
            for entry in value['files']:
                reserve(entry)
                def serial_checkpoint():
                    if progress is not None:
                        progress(completed,total)
                accept(_verify_package_member(root,entry,full=full,checkpoint=serial_checkpoint,
                    clock=None if timing is None else timing.clock))
                submitted += 1; peak = 1
                if failure is not None:
                    raise failure
        elif total:
            pool = ThreadPoolExecutor(max_workers=4,thread_name_prefix='eeas-verify')
            while submitted < total or pending:
                while submitted < total and len(pending) < 4 and not stop.is_set():
                    entry = value['files'][submitted]; reserve(entry)
                    pending.add(pool.submit(member,entry)); submitted += 1
                    peak = max(peak,len(pending))
                if not pending:
                    break
                done, _ = wait(pending,timeout=0.025,return_when=FIRST_COMPLETED)
                for future in done:
                    pending.remove(future); accept(future.result())
                if failure is not None:
                    raise failure
                if progress is not None:
                    progress(completed,total)
        for field in ('python', 'electron', 'main', 'component_manifest', 'readonly_manifest'):
            name = value['entrypoints'][field]
            if name.casefold() not in seen:
                raise PackageError('Entrypoint is not in the hashed payload')
        verify_plugin_closures(root, value, full=full,
            progress=(lambda: progress(completed, total)) if progress is not None else None)
        if progress is not None:
            progress(total, total)
    except BaseException as error:
        failure = error
        stop.set()
    finally:
        # No executor context-manager exit while UI/cancellation is unpumped.
        # Never return/raise past a live owned read or falsely report it stopped.
        while pending:
            try:
                done, _ = wait(pending,timeout=0.025,return_when=FIRST_COMPLETED)
                for future in done:
                    pending.remove(future); accept(future.result())
                if progress is not None:
                    progress(completed,total)
            except BaseException as error:
                if failure is None:
                    failure = error
                stop.set()
        if pool is not None:
            pool.shutdown(wait=True,cancel_futures=True)
        if timing is not None:
            selection = 'FULL' if full else 'STARTUP_CRITICAL'
            timing.record('PAYLOAD_PATH_METADATA', 'FAILED' if meta_failed else 'COMPLETED' if meta_count == total else 'PARTIAL',
                duration_ns=meta_ns, measurement='ACCUMULATED_ACTIVE_TIME', file_count=meta_count,
                byte_count=meta_bytes, manifest_file_count=total)
            timing.record('PAYLOAD_HASH', 'CANCELLED' if isinstance(failure, (StartupCancelled, KeyboardInterrupt)) else
                'FAILED' if hash_failed else 'COMPLETED' if failure is None else 'PARTIAL',
                duration_ns=hash_ns, measurement='ACCUMULATED_ACTIVE_TIME', file_count=read_count,
                byte_count=read_bytes, verified_file_count=verified_count, verification_mode=selection)
            timing.record('PACKAGE_VERIFICATION', 'COMPLETED' if failure is None else
                'CANCELLED' if isinstance(failure, (StartupCancelled, KeyboardInterrupt)) else 'FAILED',
                started=started, verification_mode=selection, file_count=meta_count,
                worker_limit=workers, peak_inflight=peak,
                **({'error_type':type(failure).__name__} if failure is not None else {}))
    if failure is not None:
        raise failure
    return value


def private_paths(root, *, data_dir=None):
    root = Path(root).resolve()
    data = Path(data_dir).resolve() if data_dir is not None else root / 'user-data'
    if not data.is_relative_to(root) or data == root or any(p.is_symlink() for p in (data, *data.parents)):
        raise PackageError('Data/temp/appdata must stay below this installation directory')
    paths = {name: data / name for name in ('home', 'appdata', 'local-appdata', 'tmp', 'cache', 'config', 'logs', 'bootstrap')}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return data, paths


GUI_PROFILE_KEYS = ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'HOMEDRIVE', 'HOMEPATH')


def capture_gui_profile(root, inherited):
    """Capture the entry's native profile before private runtime setup.

    This validates the launch boundary, not the provenance of an arbitrary
    caller's environment. Missing required fields or a known private profile
    must not be presented as the Windows host profile.
    """
    private = [PureWindowsPath(str(root)) / 'user-data']
    if inherited.get('EEAS_DATA_DIR'):
        private.append(PureWindowsPath(inherited['EEAS_DATA_DIR']))

    def native(value):
        return (isinstance(value, str) and value and '\x00' not in value
                and PureWindowsPath(value).is_absolute() and '..' not in PureWindowsPath(value).parts)

    def is_private(value):
        return any(base.is_absolute() and PureWindowsPath(value).is_relative_to(base) for base in private)

    profile = {}
    for key in ('USERPROFILE', 'APPDATA', 'LOCALAPPDATA'):
        value = inherited.get(key)
        if not native(value) or is_private(value):
            raise PackageError('GUI host profile requires native, non-private ' + key)
        profile[key] = value
    filtered = []
    for key in ('HOME', 'HOMEDRIVE', 'HOMEPATH'):
        if key not in inherited:
            continue
        value = inherited[key]
        valid = native(value) and not is_private(value) if key == 'HOME' else (
            isinstance(value, str) and '\x00' not in value and bool(value)
            and (PureWindowsPath(value + '\\').drive == value if key == 'HOMEDRIVE'
                 else PureWindowsPath(value).root == '\\' and not PureWindowsPath(value).drive))
        if valid:
            profile[key] = value
        else:
            # A WSL HOME does not identify a Windows profile and is not needed
            # by GUI Main. Keep its absence rather than guessing a replacement.
            filtered.append(key)
    return {'profile': profile, 'filtered_optional_keys': filtered}


def gui_runtime_environment(root, manifest, *, inherited=None):
    inherited = dict(os.environ if inherited is None else inherited)
    captured = capture_gui_profile(root, inherited)
    env = runtime_environment(root, manifest, inherited=inherited)
    for key in GUI_PROFILE_KEYS:
        env.pop(key, None)
    env.update(captured['profile'])
    return {name: value for name, value in env.items() if name.upper() != 'ELECTRON_RUN_AS_NODE'}


def runtime_environment(root, manifest, *, inherited=None, data_dir=None):
    root = Path(root).resolve()
    env = dict(os.environ if inherited is None else inherited)
    for name in SECRET_ENV:
        env.pop(name, None)
    data, paths = private_paths(root, data_dir=data_dir)
    env.update(EEAS_DATA_DIR=str(data), EEAS_PYTHON=str(relative_path(root, manifest['entrypoints']['python'])),
        EEAS_PACKAGE_ROOT=str(root), EEAS_PACKAGE_MANIFEST_SHA256=sha(root / MANIFEST),
        PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
        PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1', UV_OFFLINE='1')
    for name, folder in {'HOME':'home', 'USERPROFILE':'home', 'APPDATA':'appdata', 'LOCALAPPDATA':'local-appdata',
            'TEMP':'tmp', 'TMP':'tmp', 'TMPDIR':'tmp', 'XDG_CACHE_HOME':'cache', 'XDG_CONFIG_HOME':'config', 'XDG_DATA_HOME':'home'}.items():
        old = env.get(name)
        # Preserve an execution-private home under this package, never ambient home.
        selected = Path(old).resolve() if old else None
        env[name] = str(selected if selected is not None and selected.is_relative_to(data) else paths[folder])
    home = PureWindowsPath(env['USERPROFILE'])
    if home.drive:
        env.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
    else:
        env.pop('HOMEDRIVE', None); env.pop('HOMEPATH', None)
    component = relative_path(root, manifest['entrypoints']['component_manifest'])
    policy = relative_path(root, manifest['entrypoints']['readonly_manifest'])
    descriptor = {'schema': 'eeas.native-collaboration-runtime.v1', 'runtime_root': str(root),
        'component_manifest_ref': {'path': str(component), 'sha256': sha(component)},
        'readonly_capability_manifest_ref': {'path': str(policy), 'sha256': sha(policy)}}
    append_ref=manifest.get('notepad_append_qualification')
    if append_ref is not None:
        if (not isinstance(append_ref,dict) or set(append_ref)!={'path','sha256'}
                or not isinstance(append_ref['sha256'],str)
                or not re.fullmatch(r'[a-f0-9]{64}',append_ref['sha256'])):
            raise PackageError('Exact packaged append qualification reference is required')
        registered=next((row for row in manifest['files'] if row['path']==append_ref['path']),None)
        if registered is None or registered['sha256']!=append_ref['sha256']:
            raise PackageError('Append qualification is not in the frozen payload')
        append_path=relative_path(root,append_ref['path'])
        if append_path.stat().st_size>1024*1024 or sha(append_path)!=append_ref['sha256']:
            raise PackageError('Packaged append qualification changed')
        descriptor['notepad_append_qualification_ref']={'path':str(append_path),'sha256':append_ref['sha256']}
    raw = (json.dumps(descriptor, sort_keys=True, separators=(',', ':')) + '\n').encode()
    ident = hashlib.sha256(raw).hexdigest()
    path = paths['bootstrap'] / ('native-runtime-' + ident + '.json')
    if path.exists():
        if path.read_bytes() != raw:
            raise PackageError('Private runtime descriptor changed')
    else:
        with path.open('xb') as stream:
            stream.write(raw)
        path.chmod(0o600)
    env.update(EEAS_COLLABORATION_RUNTIME_DESCRIPTOR=str(path), EEAS_COLLABORATION_RUNTIME_DESCRIPTOR_SHA256=sha(path))
    return env


def activate_python(root, manifest):
    root = Path(root).resolve()
    sys.dont_write_bytecode = True
    locations = [root, root / 'runtime/site-packages']
    for path in reversed(locations):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    files = {item['path']: item for item in manifest['files']}
    site_dir = root / 'runtime/site-packages'
    observed = sorted(p.relative_to(root).as_posix() for p in site_dir.glob('*.pth'))
    if observed != sorted(manifest.get('pth_files', [])):
        raise PackageError('Unregistered .pth startup code cannot execute')
    for name in observed:
        path = relative_path(root, name)
        if name not in files or sha(path) != files[name]['sha256']:
            raise PackageError('Packaged .pth source changed')
        for line in path.read_text('utf-8-sig').splitlines():
            text = line.strip()
            if not text or text.startswith('#') or text.startswith(('import ', 'import\t')):
                continue
            if PureWindowsPath(text).is_absolute() or Path(text).is_absolute() or '..' in text.replace('\\','/').split('/'):
                raise PackageError('Absolute/escaping .pth path cannot be relocated')
    # This processes real relative PyWin32 paths and its per-process DLL bootstrap.
    # _pth intentionally has no automatic import site or legacy sitecustomize.
    site.addsitedir(str(site_dir))


def python_module_command(root, module, args=()):
    if module not in MODULES:
        raise PackageError('Unknown packaged Python module')
    root = Path(root).resolve()
    # Embedded _pth ignores PYTHON* startup variables; -B must precede imports.
    return [str(root / 'runtime/python/python.exe'), '-B', str(root / 'scripts/native_package_bootstrap.py'),
        '--module', module, '--', *map(str, args)]


def python_script_command(root, script, args=()):
    root = Path(root).resolve()
    path = relative_path(root, script)
    if not path.is_file() or not script.startswith('packages/') or not script.endswith('.py'):
        raise PackageError('An exact packaged Python script is required')
    return [str(root / 'runtime/python/python.exe'), '-B', str(root / 'scripts/native_package_bootstrap.py'),
        '--script', script, '--', *map(str, args)]


def execute_python(root, manifest, *, module=None, script=None, args=(), timing=None):
    _timed_call(timing, 'PYTHON_ACTIVATION', activate_python, root, manifest)
    if module is not None:
        if module not in MODULES:
            raise PackageError('Unknown packaged Python module')
        sys.argv = [module, *args]
        if timing is not None and module == 'apps.api':
            os.environ['EEAS_STARTUP_TIMING'] = '1'
            # runpy includes the service lifetime; only this entry boundary is
            # observable here. It must never be labelled API import duration.
            timing.record('API_MODULE_EXECUTION', 'ENTERED')
        runpy.run_module(module, run_name='__main__', alter_sys=True)
    else:
        path = relative_path(root, script)
        members = {v['path'] for v in manifest['files']}
        if script not in members or not script.startswith('packages/') or not script.endswith('.py'):
            raise PackageError('Script is not in the immutable packaged payload')
        sys.argv = [str(path), *args]
        runpy.run_path(str(path), run_name='__main__')


def diagnose_child(root, manifest):
    """Real import/COM initialization in this process; no window or model call."""
    activate_python(root, manifest)
    names = ('win32api', 'pythoncom', 'pywintypes', 'cua_driver', 'cua_driver._native',
        'windows_capture', 'numpy', 'cv2', 'onnxruntime', 'PIL', 'mcp', 'httpx',
        'docx', 'apps.api.main', *sorted(MCP_MODULES))
    modules = {name: importlib.import_module(name) for name in names}
    modules['pythoncom'].CoInitialize()
    try:
        paths = {name: str(Path(module.__file__).resolve()) for name, module in modules.items()}
        if any(not Path(path).is_relative_to(root) for path in paths.values()):
            raise PackageError('Diagnostic imported an ambient module outside the package')
    finally:
        modules['pythoncom'].CoUninitialize()
    return {'pid':os.getpid(), 'executable':sys.executable, 'imports':paths,
        'com_initialization':'PASSED_THIS_PROCESS', 'windows_gui':'NOT_RUN', 'native_capture':'NOT_RUN'}


def diagnostic_exception(error, *, depth=0):
    """Preserve backport/native exception groups on the Python3.10 target."""
    record = {'type':type(error).__name__, 'message':str(error),
        'traceback':''.join(traceback.format_exception(type(error),error,error.__traceback__))}
    if depth >= 16:
        record['nested_limit'] = 'REACHED';return record
    nested = getattr(error,'exceptions',())
    if nested:
        record['exceptions'] = [diagnostic_exception(child,depth=depth+1) for child in nested]
    if error.__cause__ is not None:
        record['cause'] = diagnostic_exception(error.__cause__,depth=depth+1)
    elif error.__context__ is not None and not error.__suppress_context__:
        record['context'] = diagnostic_exception(error.__context__,depth=depth+1)
    return record


def diagnostic_state(directory, value):
    value['recorded_monotonic'] = time.monotonic()
    with (directory / 'phases.jsonl').open('a',encoding='utf-8') as history:
        history.write(json.dumps(value,ensure_ascii=False)+'\n')
    path = directory / 'phase.next.json'
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    path.replace(directory / 'phase.json')


async def diagnose_mcp(root, manifest, directory, *, timeout_seconds=180):
    # Use the original SDK and real separate interpreter for every project MCP.
    # Desktop initializes a diagnostic execution binding but invokes no bridge.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from packages.domain.tasks import TaskService
    import anyio
    results,started = [],time.monotonic()
    state = {'stage':'PREPARING_PROJECT','status':'RUNNING','mcp':results,'tool_calls':0}
    diagnostic_state(directory,state)
    store = TaskService(directory / 'eeas.sqlite3', root)
    project = store.create_project('Original package diagnostic fixture', 'No model or desktop operation')
    for module in sorted(MCP_MODULES):
        if module == 'packages.adapters.opencode_native_review':
            from packages.adapters.opencode_native_review import prepare
            from types import SimpleNamespace
            prepared = prepare(root, directory, owner_ref={'id':project['id'],'revision':1,'sha256':'2'*64},
                generation=1, directory=directory,
                bridge=SimpleNamespace(base_url='http://127.0.0.1:9',key='1'*64))
            tail = ['--scope', prepared['config_path']]
        else:
            tail = [] if module == 'packages.desktop.mcp_server' else ['--data-dir',str(directory),
                '--project-id',project['id'],'--source-root',str(root)]
        command = python_module_command(root, module, tail)
        inherited = {key:os.environ[key] for key in ('PATH','SYSTEMROOT','WINDIR','COMSPEC') if key in os.environ}
        environment = runtime_environment(root, manifest, inherited=inherited)
        if module == 'packages.desktop.mcp_server':
            environment.update(EEAS_DESKTOP_BRIDGE_URL='http://127.0.0.1:9',
                EEAS_DESKTOP_BRIDGE_TOKEN='1'*64,EEAS_DESKTOP_EXECUTION_ID='diagnostic-init-only',
                EEAS_DESKTOP_REQUEST_SHA256='2'*64)
        parameters = StdioServerParameters(command=command[0], args=command[1:], cwd=str(root), env=environment)
        name = module.replace('.','-')
        stderr = directory / (name+'.stderr.txt')
        state.update(module=module,command=command,stderr=stderr.name,stage='STDIO_ENTER')
        diagnostic_state(directory,state)
        try:
            with stderr.open('w',encoding='utf-8') as errlog,anyio.fail_after(timeout_seconds):
                async with stdio_client(parameters,errlog=errlog) as (reader,writer):
                    state['stage']='SESSION_ENTER';diagnostic_state(directory,state)
                    async with ClientSession(reader,writer) as client:
                        state['stage']='INITIALIZE';diagnostic_state(directory,state)
                        initialized = await client.initialize()
                        (directory/(name+'.initialize.json')).write_text(
                            initialized.model_dump_json(by_alias=True,indent=2)+'\n',encoding='utf-8')
                        state['stage']='LIST_TOOLS';diagnostic_state(directory,state)
                        listed = await client.list_tools()
                        (directory/(name+'.tools.json')).write_text(
                            listed.model_dump_json(by_alias=True,indent=2)+'\n',encoding='utf-8')
                        results.append({'module':module,'command':command,'server_name':initialized.server_info.name,
                            'protocol':initialized.protocol_version,'tools':[tool.name for tool in listed.tools],
                            'status':'REAL_CHILD_INITIALIZE_LIST_TOOLS_PASSED','tool_calls':0,
                            'elapsed_total_seconds':round(time.monotonic()-started,6),'stderr':stderr.name})
                        state['stage']='SESSION_CLOSE';diagnostic_state(directory,state)
                    state['stage']='STDIO_CLOSE';diagnostic_state(directory,state)
            state['stage']='CHILD_CLOSED';diagnostic_state(directory,state)
        except BaseException as error:
            state.update(status='FAILED',elapsed_total_seconds=round(time.monotonic()-started,6),
                error=diagnostic_exception(error))
            diagnostic_state(directory,state)
            (directory/(name+'.exception.json')).write_text(json.dumps(state['error'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            raise
    state.update(stage='ALL_MCP_CHILDREN_CLOSED',status='PASSED',elapsed_total_seconds=round(time.monotonic()-started,6))
    diagnostic_state(directory,state)
    return results


def diagnose(root, manifest):
    activate_python(root, manifest)
    import uuid
    directory = root / 'user-data/diagnostics' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    child_command = [str(relative_path(root, manifest['entrypoints']['python'])), '-B',
        str(root / 'scripts/native_package_bootstrap.py'),'--diagnose-child']
    child = subprocess.run(child_command, cwd=root, env=dict(os.environ), capture_output=True,
        text=True, encoding='utf-8', timeout=180)
    (directory / 'child.stdout.txt').write_text(child.stdout,encoding='utf-8')
    (directory / 'child.stderr.txt').write_text(child.stderr,encoding='utf-8')
    if child.returncode != 0:
        raise PackageError('Separate Python import/COM diagnostic failed; original stderr is preserved at ' + str(directory))
    actual = json.loads(child.stdout)
    try:
        results = asyncio.run(diagnose_mcp(root, manifest, directory))
    except BaseException as error:
        value={'status':'FAILED','package_root':str(root),'manifest_sha256':sha(root / MANIFEST),
            'child':actual,'error':diagnostic_exception(error),'phase_file':'phase.json',
            'windows_gui':'NOT_RUN','model_access':'NOT_CONFIGURED'}
        (directory/'receipt.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('Original nested diagnostic failure preserved at '+str(directory),file=sys.stderr)
        raise
    value = {'status':'REAL_WINDOWS_CHILD_IMPORT_COM_MCP_INITIALIZATION_PASSED',
        'package_root':str(root),'manifest_sha256':sha(root / MANIFEST),'child':actual,'mcp':results,
        'windows_gui':'NOT_RUN','model_access':'NOT_CONFIGURED','chatgpt_pro':'NOT_VALIDATED',
        'arbitrary_app_editing':'NOT_VALIDATED','os_network_sandbox':'NOT_QUALIFIED'}
    (directory / 'receipt.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return value


def selected_installation_root(directory, executable):
    """Maintenance launches the selected version's Python from that installation."""
    root = Path(directory).resolve(strict=True)
    interpreter = Path(executable).resolve(strict=True)
    if interpreter != root / 'runtime' / 'python' / 'pythonw.exe':
        raise PackageError('Selected installation does not own this interpreter')
    return root


def main(argv=None):
    entered = time.perf_counter_ns()
    parser = argparse.ArgumentParser(description='EEAS relative offline Windows package entry')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--api', action='store_true'); mode.add_argument('--electron', action='store_true')
    mode.add_argument('--mcp', choices=sorted(MCP_MODULES)); mode.add_argument('--module', choices=sorted(MODULES))
    mode.add_argument('--script'); mode.add_argument('--verify', action='store_true')
    mode.add_argument('--diagnose', action='store_true'); mode.add_argument('--diagnose-child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--background', action='store_true', help='Start the desktop App in the background for an explicitly configured companion client')
    parser.add_argument('--selected-installation', help='Use the maintained launcher for a selected desktop installation')
    parser.add_argument('--expected-manifest-sha256', help='Require the explicitly selected program version')
    parser.add_argument('args', nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    if options.background and (options.api or options.mcp or options.module or options.script or options.verify or options.diagnose or options.diagnose_child):
        parser.error('--background is only valid for a desktop App launch')
    root = Path(__file__).resolve().parents[1]
    implicit_mcp = all(os.environ.get(key) for key in ('EEAS_DESKTOP_BRIDGE_URL','EEAS_DESKTOP_BRIDGE_TOKEN',
        'EEAS_DESKTOP_EXECUTION_ID','EEAS_DESKTOP_REQUEST_SHA256'))
    child_mode = options.api or options.mcp or options.module or options.script or options.diagnose_child or implicit_mcp
    if options.selected_installation is not None:
        if not options.electron or child_mode or options.background or options.args or not options.expected_manifest_sha256:
            parser.error('--selected-installation requires a version-bound foreground desktop launch')
        root = selected_installation_root(options.selected_installation, sys.executable)
    api_mode = options.api or options.module == 'apps.api'
    timing = (StartupTiming(mode='API', stream=sys.stderr, started=entered) if api_mode else
        StartupTiming(mode='ELECTRON', started=entered) if not child_mode and not options.verify and not options.diagnose else None)
    if timing is not None:
        timing.record('BOOTSTRAP_ENTRY', 'ENTERED')
    progress = _timed_call(timing, 'STARTUP_FEEDBACK', StartupProgress,
        enabled=not child_mode and not options.verify and not options.diagnose and not options.background,
        **({'root': root} if options.selected_installation is not None else {}))
    progress.timing = timing
    try:
        return _main_checked(root, options, child_mode, implicit_mcp, progress)
    except BaseException as error:
        progress.close()
        if progress.enabled and isinstance(error, Exception) and not isinstance(error, StartupCancelled):
            report_startup_failure(root, error)
        raise
    finally:
        progress.close()


def _main_checked(root, options, child_mode, implicit_mcp, progress):
    timing = getattr(progress, 'timing', None)
    expected_manifest = getattr(options, 'expected_manifest_sha256', None)
    if expected_manifest is not None and (not isinstance(expected_manifest, str)
            or not re.fullmatch('[a-f0-9]{64}', expected_manifest)
            or sha(root / 'PACKAGE_MANIFEST.json') != expected_manifest):
        raise PackageError('Selected program version changed before startup')
    manifest = verify_package(root, full=options.verify or options.diagnose or not child_mode,
        progress=progress.update if progress.enabled else None, timing=timing)
    if expected_manifest is not None and sha(root / 'PACKAGE_MANIFEST.json') != expected_manifest:
        raise PackageError('Selected program version changed during startup verification')
    if options.verify:
        print(json.dumps({'status':'PAYLOAD_HASHES_VERIFIED', 'target':manifest['target'],
            'windows_runtime':'NOT_EXECUTED', 'model_access':'NOT_CONFIGURED', 'qualification':'FILES_ONLY'}))
        return 0
    if os.name != 'nt' or root.drive.upper() not in {'D:', 'E:'} or sys.version_info[:2] != (3,10) or struct.calcsize('P') != 8:
        raise PackageError('Windows x64 Python3.10 on the selected D/E installation is required')
    inherited = dict(os.environ)
    if not child_mode and not options.diagnose:
        env = _timed_call(timing, 'RUNTIME_ENVIRONMENT', gui_runtime_environment,
            root, manifest, inherited=inherited)
    else:
        env = _timed_call(timing, 'RUNTIME_ENVIRONMENT', runtime_environment, root, manifest,
            inherited=inherited, data_dir=inherited.get('EEAS_DATA_DIR') if child_mode else None)
    if timing is not None and timing.mode == 'ELECTRON':
        timing.persist_verified(root, data_dir=env['EEAS_DATA_DIR'])
    env.setdefault('EEAS_NATIVE_RUNTIME','opencode-http')
    os.environ.clear(); os.environ.update(env)
    args = options.args[1:] if options.args[:1] == ['--'] else options.args
    # OpenCodeDesktopTransport already passes this script plus its exact private
    # execution bridge environment; no extra command argument is needed there.
    if options.diagnose_child:
        print(json.dumps(diagnose_child(root,manifest),ensure_ascii=False));return 0
    if options.diagnose:
        print(json.dumps(diagnose(root,manifest),ensure_ascii=False));return 0
    if options.api:
        execute_python(root, manifest, module='apps.api', args=['serve', *args], timing=timing); return 0
    if options.mcp or options.module or options.script or implicit_mcp:
        execute_python(root, manifest, module=options.mcp or options.module or ('packages.desktop.mcp_server' if implicit_mcp else None),
            script=options.script, args=args, timing=timing); return 0
    return launch_electron(root, manifest, env, progress, background=options.background)


def launch_electron(root, manifest, environment, progress, *, background=False):
    """Launch a GUI Main independently of an enclosing Electron/IDE runtime.

    API/MCP/module children do not use this function. Original Main output stays
    in an installation-private log, including failures before its own API log
    exists. A normal Main/user cancellation returns0; pre-Main cancellation is
    the existing StartupCancelled path and must never become a failure dialog.
    """
    if type(background) is not bool:
        raise PackageError('Background launch requires an explicit boolean')
    executable = relative_path(root, manifest['entrypoints']['electron'])
    app = relative_path(root, manifest['entrypoints']['main']).parent
    # VS Code and Electron CLIs can pass this flag to descendants. Passing it to
    # our GUI executable selects Node mode instead of running BrowserWindow.
    env = {name:value for name,value in environment.items()
        if name.upper() != 'ELECTRON_RUN_AS_NODE'}
    progress.finish()
    _data, paths = private_paths(root)
    import uuid
    log = paths['logs'] / ('main-process-' + uuid.uuid4().hex + '.log')
    with log.open('xb') as output:
        log.chmod(0o600)
        timing = getattr(progress, 'timing', None)
        if timing is not None:
            timing.record('ELECTRON_MAIN_CALL', 'ENTERED')
        code = subprocess.call([str(executable), str(app), *(['--eeas-background'] if background else [])], cwd=root, env=env,
            stdout=output, stderr=subprocess.STDOUT)
    if code != 0:
        raise PackageError(progress.copy('abnormalExit', code=code, path=log))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (StartupCancelled, KeyboardInterrupt):
        StartupProgress._console(StartupCopy()('cancelled'))
        raise SystemExit(130)
    except Exception as error:
        # stderr is safe for a stdio MCP client; stdout is its protocol channel.
        print(StartupCopy()('cliFailure') + ': ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)
