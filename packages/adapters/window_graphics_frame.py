"""One explicit readonly HWND frame, isolated from native capture thread hangs.

Only a child created and retained by this instance can be terminated. No target
window is activated, modified or adopted. Original Cua/Agent execution is separate.
"""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

from PIL import Image

from packages.adapters.desktop_current_context import Win32CurrentWindowSource
from packages.adapters.visible_window_frame import (
    ReadonlyWindowFrameLedger, VisibleFrameError, _binding, _window, _rect, _inside,
    _Win32CaptureApi, MAX_BYTES,
)
from packages.adapters.windows_desktop_region import surface_reference, _sha
from packages.domain.local_workspaces import canonical, sha

SOURCE = 'EEAS_READONLY_EXACT_HWND_V1'
METHOD = 'WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND'


def _fail(code):
    raise VisibleFrameError(code)


class WindowCaptureWorker:
    """At most one retained helper, no background activity between captures."""
    def __init__(self, directory, *, timeout=6, process_factory=None):
        if type(timeout) not in (int, float) or not .05 <= timeout <= 10:
            _fail('BOUNDED_HWND_CAPTURE_TIMEOUT_REQUIRED')
        self.directory, self.timeout = Path(directory), timeout
        self.directory.mkdir(parents=True, exist_ok=True)
        self.factory = subprocess.Popen if process_factory is None else process_factory
        self.lock, self.cleanup_lock = threading.RLock(), threading.Lock()
        self.process = None
        self.job = None
        self.closed = False

    @property
    def owned_processes(self):
        with self.lock:
            return () if self.process is None else (self.process,)

    def _settle(self, process):
        # The retained Popen object is the authority, never a persisted PID.
        with self.cleanup_lock:
            if process.poll() is None:
                for stop in (process.terminate, process.kill):
                    try:
                        stop()
                        process.wait(timeout=1)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    if process.poll() is not None:
                        break
            stopped = process.poll() is not None
            with self.lock:
                if stopped and self.process is process:
                    self.process = None
            return stopped

    def capture(self, current, excluded_pids):
        with self.lock:
            if self.closed:
                _fail('HWND_CAPTURE_OWNER_CLOSED')
            if self.process is not None or self.job is not None:
                _fail('PREVIOUS_HWND_CAPTURE_CLEANUP_NOT_SETTLED')
            if any(p.is_symlink() for p in (self.directory, *self.directory.parents)):
                _fail('HWND_CAPTURE_JOB_ROOT_UNSAFE')
            job = self.directory / uuid.uuid4().hex
            job.mkdir(exist_ok=False)
            config = {'window': current, 'excluded_pids': excluded_pids, 'timeout': self.timeout}
            (job / 'request.json').write_text(canonical(config), encoding='utf-8')
            # Preserve the already selected interpreter/import roots, without
            # inherited model secrets or ambient PYTHONPATH/PYTHONHOME.
            code = 'import sys,json;sys.path[:0]=json.loads(sys.argv[1]);from packages.adapters.window_graphics_frame import _worker_main;_worker_main(sys.argv[2])'
            env = {key: value for key, value in os.environ.items() if key.upper() in
                {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'SYSTEMDRIVE'}}
            env.update(TMP=str(job), TEMP=str(job), TMPDIR=str(job), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
            try:
                process = self.factory([sys.executable, '-B', '-Xutf8', '-c', code,
                    json.dumps([str(Path(p).absolute()) for p in sys.path if p]), str(job)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env, cwd=str(Path(__file__).resolve().parents[2]),
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except BaseException:
                shutil.rmtree(job)
                raise
            self.process = process
            self.job = job
        try:
            try:
                code = process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                _fail('EXACT_HWND_CAPTURE_TIMEOUT')
            if code != 0:
                _fail('EXACT_HWND_CAPTURE_HELPER_FAILED')
            with self.lock:
                if self.closed:
                    _fail('HWND_CAPTURE_OWNER_CLOSED')
            result_path, image_path = job / 'result.json', job / 'capture.png'
            if result_path.is_symlink() or image_path.is_symlink():
                _fail('HWND_CAPTURE_OUTPUT_UNSAFE')
            raw_result = ReadonlyWindowFrameLedger._read(result_path, 65536)
            result = json.loads(raw_result)
            if result.get('status') != 'CAPTURED' or result.get('method') != METHOD or result.get('package_version') != '2.0.1':
                _fail('EXACT_HWND_CAPTURE_UNAVAILABLE')
            raw = ReadonlyWindowFrameLedger._read(image_path, MAX_BYTES)
            if sha(raw) != result.get('image_sha256'):
                _fail('EXACT_HWND_CAPTURE_BYTES_CHANGED')
            return {**result, 'bytes': raw}
        finally:
            stopped = self._settle(process)
            if stopped:
                self._discard_job(job)
            if not stopped:
                _fail('EXACT_HWND_CAPTURE_CLEANUP_UNKNOWN')

    def close(self):
        with self.lock:
            self.closed = True
            process, job = self.process, self.job
        stopped = process is None or self._settle(process)
        if stopped and job is not None:
            self._discard_job(job)
        return {'status': 'CLOSED' if stopped else 'CLOSE_UNKNOWN',
                'pending_process_count': 0 if stopped else 1, 'pending_thread_count': 0,
                'user_windows_modified': False}

    def _discard_job(self, job):
        with self.lock:
            if self.job == job:
                if job.is_symlink() or any(p.is_symlink() for p in (self.directory, *self.directory.parents)):
                    _fail('HWND_CAPTURE_JOB_ROOT_UNSAFE')
                if job.exists():
                    shutil.rmtree(job)
                self.job = None


def _worker_main(directory):
    """Trusted interpreter child; output stays private and bounded by the parent."""
    job = Path(directory)
    try:
        config = json.loads(ReadonlyWindowFrameLedger._read(job / 'request.json', 65536))
        current = _window(config['window'])
        excluded = config['excluded_pids']
        if (type(excluded) is not list or not excluded or len(excluded) > 1024 or
                any(type(pid) is not int or pid <= 0 for pid in excluded)):
            _fail('EXACT_TRUSTED_CAPTURE_EXCLUSION_REQUIRED')
        source = Win32CurrentWindowSource(excluded_pid_reader=lambda: excluded + [os.getpid()])
        before = _window(source.inspect(current['window_id']))
        if before != current:
            _fail('HWND_LIFETIME_CHANGED_BEFORE_CAPTURE')
        geometry = _Win32CaptureApi(source)
        with geometry.physical_context():
            dwm_before = geometry.visible_bounds(before)
        # Existing pinned implementation handles exact HWND, first-frame stop,
        # byte/pixel budget and WGC version. Parent bounds even stop() hangs.
        from packages.adapters.windows_native_window_host import _wgc_frame
        frame = _wgc_frame({'hwnd': current['window_id']}, config['timeout'])
        after = _window(source.inspect(current['window_id']))
        with geometry.physical_context():
            dwm_after = geometry.visible_bounds(after)
        if before != after or dwm_before != dwm_after:
            _fail('HWND_LIFETIME_CHANGED_DURING_CAPTURE')
        raw = frame['png']
        if not 0 < len(raw) <= MAX_BYTES:
            _fail('HWND_CAPTURE_BYTE_BUDGET_EXCEEDED')
        with Image.open(BytesIO(raw)) as image:
            size = image.size
        candidates = [before['bounds']]
        if dwm_before is not None:
            visible = _rect(dwm_before)
            if _inside(visible, before['bounds']):
                candidates.append(visible)
        bounds = next((rect for rect in candidates if (rect['width'], rect['height']) == size), None)
        if bounds is None:
            _fail('EXACT_HWND_CAPTURE_GEOMETRY_DIFFERS')
        ReadonlyWindowFrameLedger._write(job / 'capture.png', raw)
        result = {'status': 'CAPTURED', 'method': frame['backend'], 'package_version': frame['package_version'],
                  'before': before, 'after': after, 'capture_bounds': bounds,
                  'dwm_before': dwm_before, 'dwm_after': dwm_after, 'image_sha256': sha(raw)}
    except Exception as error:
        result = {'status': 'UNAVAILABLE', 'error_type': type(error).__name__}
    ReadonlyWindowFrameLedger._write(job / 'result.json', canonical(result).encode())


class WindowGraphicsFrames(ReadonlyWindowFrameLedger):
    frame_source = SOURCE
    pixel_source = 'EXACT_TARGET_WINDOW'

    def __init__(self, data_dir, *, window_source, worker=None, clock=None):
        if not callable(getattr(window_source, 'inspect', None)):
            _fail('EXACT_WINDOW_INSPECTION_REQUIRED')
        if worker is None and (os.name != 'nt' or not isinstance(window_source, Win32CurrentWindowSource)):
            _fail('ACTUAL_WINDOWS_HWND_CAPTURE_REQUIRED')
        super().__init__(data_dir, directory='exact-hwnd-frames')
        self.source, self.clock = window_source, time.time if clock is None else clock
        self.provider_kind = 'ACTUAL_WIN32_WGC_EXACT_HWND' if worker is None else 'INJECTED_TEST_PROVIDER_NOT_WINDOWS_EVIDENCE'
        self.worker = WindowCaptureWorker(self.data / 'jobs') if worker is None else worker

    @property
    def owned_processes(self):
        return self.worker.owned_processes

    def close(self):
        return self.worker.close()

    def capture(self, current, target_ref, binding):
        current, target_ref, binding = _window(current), surface_reference(target_ref), _binding(binding)
        with self.lock:
            before = _window(self.source.inspect(current['window_id']))
            if before != current:
                _fail('EXACT_HWND_TARGET_CHANGED_BEFORE_CAPTURE')
            result = self.worker.capture(current, list(self.source.excluded_pids()))
            after = _window(self.source.inspect(current['window_id']))
            if before != after or result['before'] != before or result['after'] != after:
                _fail('EXACT_HWND_TARGET_CHANGED_DURING_CAPTURE')
            if result['method'] != METHOD or result['package_version'] != '2.0.1':
                _fail('PINNED_HWND_CAPTURE_REQUIRED')
            raw = result['bytes']
            if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES or sha(raw) != result['image_sha256']:
                _fail('EXACT_HWND_FRAME_BYTES_INVALID')
            bounds = _rect(result['capture_bounds'])
            if not _inside(bounds, before['bounds']):
                _fail('EXACT_HWND_CAPTURE_OUTSIDE_TARGET')
            if bounds != before['bounds'] and (result.get('dwm_before') != bounds or result.get('dwm_after') != bounds):
                _fail('EXACT_HWND_DWM_GEOMETRY_NOT_CONFIRMED')
            with Image.open(BytesIO(raw)) as image:
                width, height = image.size
                if image.format != 'PNG' or (width, height) != (bounds['width'], bounds['height']):
                    _fail('EXACT_HWND_CAPTURE_GEOMETRY_DIFFERS')
                image.verify()
            stamp = self.clock()
            if type(stamp) not in (int, float) or not 0 <= stamp < 1e12:
                _fail('BOUNDED_FRAME_CAPTURE_TIME_REQUIRED')
            ident, image_sha = uuid.uuid4().hex, sha(raw)
            image_ref = {'path': ident+'/frame.png', 'sha256': image_sha, 'byte_length': len(raw), 'width': width, 'height': height}
            evidence = {'schema': 'eeas.exact-hwnd-geometry.v1', 'before': before, 'after': after,
                'capture_bounds': bounds, 'bounds_source': METHOD, 'target_ref': target_ref,
                'binding': binding, 'image_ref': image_ref, 'provider_kind': self.provider_kind}
            proof_raw = canonical(evidence).encode()
            proof = {'status': self.pixel_source, 'image_sha256': image_sha, 'target_bounds': before['bounds'],
                'capture_bounds': bounds, 'target_identity_sha256': before['identity_sha256'],
                'coordinate_space': before['coordinate_space'], 'capture_method': METHOD, 'self_exclusion_verified': True,
                'evidence_ref': {'id': ident, 'revision': 1, 'sha256': sha(proof_raw)}}
            public = {'session_ref': binding['sdk_session_ref'], 'target_ref': target_ref, 'surface_ref': binding['surface_ref'],
                'input_epoch': binding['input_epoch'], 'workspace_session_ref': binding['workspace_session_ref'], 'captured_at': stamp,
                'image_ref': image_ref, 'width': width, 'height': height, 'status': 'OBSERVATION_NOT_ACCEPTED_ENGINEERING_FACT',
                'runtime_role': 'READONLY_OBSERVATION', 'source': SOURCE, 'provider_kind': self.provider_kind,
                'pixel_source': self.pixel_source, 'pixel_source_proof': proof, 'self_exclusion': 'EXACT_HWND_EXCLUDES_OTHER_WINDOWS',
                'automatic_input_authorized': False, 'visual_coordinate_input_authorized': False,
                'coverage': {'pixel_source': self.pixel_source, 'target_window_only': True, 'whole_page_seen': False, 'occlusion_checked': False},
                'limitations': ['WINDOW_CONTENT_CAN_CHANGE_AFTER_CAPTURE', 'ONLY_CURRENT_WINDOW_VIEWPORT', 'NOT_A_CUA_INPUT_FRAME']}
            ref = {'id': ident, 'revision': 1, 'sha256': _sha(public)}
            self._path(ident, 'frame.png').parent.mkdir(exist_ok=False)
            self._write(self._path(ident, 'frame.png'), raw)
            self._write(self._path(ident, 'proof.json'), proof_raw)
            self._write(self._path(ident, 'observation.json'), canonical({'schema': 'eeas.visible-window-frame.v1', 'frame_ref': ref, 'public': public}).encode())
            return json.loads(canonical({**public, 'frame_ref': ref}))

    def _record(self, frame_ref):
        record, evidence = super()._record(frame_ref)
        public = record['public']
        try:
            valid = (evidence['schema'] == 'eeas.exact-hwnd-geometry.v1' and
                _window(evidence['before']) == evidence['before'] == evidence['after'] and
                evidence['bounds_source'] == METHOD and
                public['pixel_source_proof']['capture_method'] == METHOD and
                public['pixel_source_proof']['status'] == self.pixel_source and
                public['pixel_source_proof']['self_exclusion_verified'] is True and
                public['coverage']['target_window_only'] is True)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            _fail('EXACT_HWND_PROOF_DIFFERS')
        return record, evidence
