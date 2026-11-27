"""Readonly visible-rectangle frames, separate from original Cua input frames.

Capture is synchronous inside the caller's existing authority guard. No window
is activated, clicked, moved, hidden or adopted here. Native qualification is a
separate Windows check; injected capture APIs are explicitly test providers.
"""
from __future__ import annotations
from contextlib import contextmanager
import ctypes as ct
from io import BytesIO
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from PIL import Image, ImageGrab, ImageStat

from packages.adapters.desktop_current_context import Win32CurrentWindowSource
from packages.adapters.windows_desktop_region import _sha, surface_reference
from packages.adapters.windows_native_window_host import HANDLE, DWORD, RECT, frame_png
from packages.domain.local_workspaces import canonical, sha
from packages.domain.paths import resolve_data_path
from packages.domain.store import InvalidInput

MAX_PIXELS = 16_000_000
MAX_BYTES = 16 * 1024 * 1024
COORDINATES = 'PHYSICAL_PIXELS_OWN_THREAD_PMV2'
SOURCE = 'EEAS_READONLY_VISIBLE_RECTANGLE_V1'


class VisibleFrameError(ValueError):
    pass


def _fail(code):
    raise VisibleFrameError(code)


def _rect(value, *, desktop=False):
    if type(value) is not dict or set(value) != {'x', 'y', 'width', 'height'} or any(type(v) is not int for v in value.values()):
        _fail('EXACT_PHYSICAL_FRAME_RECTANGLE_REQUIRED')
    if (abs(value['x']) > 1_000_000 or abs(value['y']) > 1_000_000 or
            not 1 <= value['width'] <= 65536 or not 1 <= value['height'] <= 65536 or
            value['width'] * value['height'] > (64_000_000 if desktop else MAX_PIXELS)):
        _fail('VISIBLE_FRAME_GEOMETRY_BUDGET_EXCEEDED')
    return dict(value)


def _inside(inner, outer):
    return (inner['x'] >= outer['x'] and inner['y'] >= outer['y'] and
        inner['x'] + inner['width'] <= outer['x'] + outer['width'] and
        inner['y'] + inner['height'] <= outer['y'] + outer['height'])


def _window(value):
    if (type(value) is not dict or value.get('status') != 'WINDOW_OBSERVED' or
            value.get('visible') is not True or value.get('minimized') is not False or
            value.get('coordinate_space') != COORDINATES):
        _fail('ACTUAL_VISIBLE_EXTERNAL_FOREGROUND_REQUIRED')
    identity = {k: value.get(k) for k in ('window_id', 'pid', 'process_creation', 'os_session', 'window_class', 'window_thread')}
    if (any(type(identity[k]) is not int or identity[k] <= 0 for k in ('window_id', 'pid', 'process_creation', 'window_thread')) or
            type(identity['os_session']) is not int or identity['os_session'] < 0 or
            type(identity['window_class']) is not str or not 1 <= len(identity['window_class']) <= 512 or
            type(value.get('dpi')) is not int or not 1 <= value['dpi'] <= 1536 or
            value.get('identity_sha256') != _sha(identity)):
        _fail('EXACT_CURRENT_WINDOW_LIFETIME_REQUIRED')
    return {**identity, 'identity_sha256': value['identity_sha256'], 'dpi': value['dpi'],
            'bounds': _rect(value.get('bounds')), 'coordinate_space': COORDINATES,
            'status': 'WINDOW_OBSERVED', 'visible': True, 'minimized': False}


def _binding(value):
    if type(value) is not dict or set(value) != {'workspace_session_ref', 'surface_ref', 'input_epoch', 'sdk_session_ref'}:
        _fail('EXACT_READONLY_FRAME_SCOPE_REQUIRED')
    result = {key: surface_reference(value[key]) for key in ('workspace_session_ref', 'surface_ref')}
    result['sdk_session_ref'] = surface_reference(value['sdk_session_ref']) if value['sdk_session_ref'] is not None else None
    epoch = value['input_epoch']
    if type(epoch) is not int or not 0 <= epoch < (1 << 63): _fail('EXACT_FRAME_INPUT_EPOCH_REQUIRED')
    result['input_epoch'] = epoch
    return result


class _Win32CaptureApi:
    """Stock Win32 geometry and already bundled Pillow; no process DPI mutation."""
    def __init__(self, source):
        self.source = source
        self.metrics = source.api._bind(source.api.user, 'GetSystemMetrics', ct.c_int32, [ct.c_int32])
        try:
            self.dwm = ct.WinDLL('dwmapi', use_last_error=True)
            self.dwm_bounds = source.api._bind(self.dwm, 'DwmGetWindowAttribute', ct.c_int32,
                [HANDLE, DWORD, ct.POINTER(RECT), DWORD])
        except (OSError, AttributeError):
            self.dwm = self.dwm_bounds = None

    @contextmanager
    def physical_context(self):
        previous = self.source.thread_dpi(-4)
        if not previous: _fail('VISIBLE_FRAME_THREAD_PMV2_UNAVAILABLE')
        try: yield
        finally:
            if not self.source.thread_dpi(previous): _fail('VISIBLE_FRAME_THREAD_DPI_RESTORE_UNKNOWN')

    def desktop_bounds(self):
        return {'x': int(self.metrics(76)), 'y': int(self.metrics(77)),
                'width': int(self.metrics(78)), 'height': int(self.metrics(79))}

    def visible_bounds(self, current):
        if self.dwm_bounds is None: return None
        rect = RECT()
        # DWMWA_EXTENDED_FRAME_BOUNDS, physical pixels. It excludes invisible
        # resize borders, so a maximized GetWindowRect(-8,-8,...) stays usable.
        if self.dwm_bounds(current['window_id'], 9, ct.byref(rect), ct.sizeof(rect)) != 0: return None
        return {'x': int(rect.left), 'y': int(rect.top),
                'width': int(rect.right-rect.left), 'height': int(rect.bottom-rect.top)}

    @staticmethod
    def grab():
        return ImageGrab.grab(all_screens=True, include_layered_windows=True)


class ReadonlyWindowFrameLedger:
    frame_source = SOURCE
    pixel_source = 'VISIBLE_TARGET_RECTANGLE'

    def __init__(self, data_dir, *, directory):
        self.data = Path(data_dir).absolute() / directory
        if any(p.is_symlink() for p in (self.data, *self.data.parents)):
            _fail('PRIVATE_VISIBLE_FRAME_ROOT_MUST_NOT_BE_SYMLINK')
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, ident, name):
        if not re.fullmatch('[0-9a-f]{32}', ident) or name not in {'frame.png', 'proof.json', 'observation.json'}:
            _fail('EXACT_PRIVATE_FRAME_ID_REQUIRED')
        if any(p.is_symlink() for p in (self.data, *self.data.parents)):
            _fail('PRIVATE_VISIBLE_FRAME_ROOT_CHANGED_TO_SYMLINK')
        try:
            return resolve_data_path(ident + '/' + name, self.data)
        except (InvalidInput, OSError, RuntimeError):
            raise VisibleFrameError('PRIVATE_VISIBLE_FRAME_PATH_IS_UNSAFE') from None

    @staticmethod
    def _write(path, raw):
        with path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())

    @staticmethod
    def _read(path, limit):
        if not path.is_file() or not 0 < path.stat().st_size <= limit: _fail('VISIBLE_FRAME_FILE_OUTSIDE_BUDGET')
        with path.open('rb') as stream: raw = stream.read(limit + 1)
        if not 0 < len(raw) <= limit: _fail('VISIBLE_FRAME_FILE_CHANGED_OR_EXCEEDED_BUDGET')
        return raw

    def _record(self, frame_ref):
        ref = surface_reference(frame_ref)
        try:
            path = self._path(ref['id'], 'observation.json')
            raw = self._read(path, 65536); record = json.loads(raw)
            if (raw != canonical(record).encode('utf-8') or set(record) != {'schema', 'frame_ref', 'public'} or
                    record['schema'] != 'eeas.visible-window-frame.v1' or record['frame_ref'] != ref or
                    _sha(record['public']) != ref['sha256']): _fail('VISIBLE_FRAME_MANIFEST_CHANGED')
            public = record['public']; image = public['image_ref']; proof = public['pixel_source_proof']
            proof_raw = self._read(self._path(ref['id'], 'proof.json'), 65536)
            if len(proof_raw) > 65536 or sha(proof_raw) != proof['evidence_ref']['sha256']:
                _fail('VISIBLE_FRAME_GEOMETRY_PROOF_CHANGED')
            evidence = json.loads(proof_raw)
            if (proof_raw != canonical(evidence).encode('utf-8') or proof['evidence_ref']['id'] != ref['id'] or
                    proof['evidence_ref']['revision'] != 1 or evidence['image_ref'] != image or
                    evidence['target_ref'] != public['target_ref'] or evidence['binding']['surface_ref'] != public['surface_ref'] or
                    evidence['binding']['sdk_session_ref'] != public['session_ref'] or evidence['binding']['input_epoch'] != public['input_epoch'] or
                    evidence['binding']['workspace_session_ref'] != public['workspace_session_ref'] or
                    proof['image_sha256'] != image['sha256'] or proof['capture_bounds'] != evidence['capture_bounds'] or
                    proof['target_bounds'] != evidence['before']['bounds'] or proof['target_identity_sha256'] != evidence['before']['identity_sha256'] or
                    public['pixel_source'] != self.pixel_source or public['source'] != self.frame_source or
                    public['automatic_input_authorized'] is not False or public['visual_coordinate_input_authorized'] is not False or
                    image['path'] != ref['id']+'/frame.png' or image['width'] != public['width'] or image['height'] != public['height'] or
                    (public['width'], public['height']) != (proof['capture_bounds']['width'], proof['capture_bounds']['height'])):
                _fail('VISIBLE_FRAME_EXACT_SCOPE_OR_GEOMETRY_DIFFERS')
            return record, evidence
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            raise VisibleFrameError('EXACT_VISIBLE_FRAME_LEDGER_UNAVAILABLE_OR_CHANGED') from None

    def has(self, frame_ref):
        with self.lock:
            try: self._record(frame_ref); return True
            except (ValueError, TypeError): return False

    def describe(self, frame_ref):
        with self.lock:
            record, _ = self._record(frame_ref)
            return json.loads(canonical({**record['public'], 'frame_ref': record['frame_ref']}))

    def lookup(self, frame_ref, *, target_ref=None, binding=None):
        with self.lock:
            record, evidence = self._record(frame_ref)
            if target_ref is not None and surface_reference(target_ref) != evidence['target_ref']:
                _fail('VISIBLE_FRAME_TARGET_SCOPE_DIFFERS')
            if binding is not None and _binding(binding) != evidence['binding']:
                _fail('VISIBLE_FRAME_CURRENT_SCOPE_DIFFERS')
            image_ref = record['public']['image_ref']; path = self._path(record['frame_ref']['id'], 'frame.png')
            try:
                raw = self._read(path, MAX_BYTES)
                if len(raw) != image_ref['byte_length'] or sha(raw) != image_ref['sha256']: _fail('VISIBLE_FRAME_PNG_CHANGED')
                with Image.open(BytesIO(raw)) as image:
                    if image.format != 'PNG' or image.size != (image_ref['width'], image_ref['height']): _fail('VISIBLE_FRAME_PNG_GEOMETRY_CHANGED')
                    image.verify()
            except (OSError, ValueError, KeyError, TypeError):
                raise VisibleFrameError('EXACT_VISIBLE_PNG_BYTES_UNAVAILABLE_OR_CHANGED') from None
            return {'bytes': raw, 'media_type': 'image/png', 'sha256': image_ref['sha256'],
                    'width': image_ref['width'], 'height': image_ref['height']}


class VisibleWindowFrames(ReadonlyWindowFrameLedger):
    def __init__(self, data_dir, *, window_source, capture_api=None, platform=None, clock=None):
        self.platform = os.name if platform is None else platform
        if not callable(getattr(window_source, 'sample', None)):
            _fail('TRUSTED_CURRENT_WINDOW_SOURCE_REQUIRED')
        if capture_api is None:
            if self.platform != 'nt' or os.name != 'nt': _fail('WINDOWS_VISIBLE_CAPTURE_ONLY')
            if not isinstance(window_source, Win32CurrentWindowSource): _fail('ACTUAL_WIN32_CURRENT_SOURCE_REQUIRED')
            capture_api = _Win32CaptureApi(window_source)
            self.provider_kind = 'ACTUAL_WIN32_PILLOW_CAPTURE'
        else:
            self.provider_kind = 'INJECTED_TEST_PROVIDER_NOT_WINDOWS_EVIDENCE'
        if not all(callable(getattr(capture_api, name, None)) for name in ('physical_context', 'desktop_bounds', 'visible_bounds', 'grab')):
            _fail('EXPLICIT_READONLY_CAPTURE_API_REQUIRED')
        self.source, self.api = window_source, capture_api
        self.clock = time.time if clock is None else clock
        super().__init__(data_dir, directory='visible-window-frames')

    def capture(self, current, target_ref, binding):
        current, target_ref, binding = _window(current), surface_reference(target_ref), _binding(binding)
        with self.lock:
            with self.api.physical_context():
                before = _window(self.source.sample())
                if before != current: _fail('FOREGROUND_TARGET_CHANGED_BEFORE_VISIBLE_CAPTURE')
                desktop = _rect(self.api.desktop_bounds(), desktop=True)
                dwm = self.api.visible_bounds(before)
                capture_bounds = _rect(dwm) if dwm is not None else dict(before['bounds'])
                if not _inside(capture_bounds, before['bounds']): _fail('VISIBLE_DWM_FRAME_OUTSIDE_EXACT_TARGET_BOUNDS')
                if not _inside(capture_bounds, desktop): _fail('VISIBLE_TARGET_RECTANGLE_IS_NOT_FULLY_ON_VIRTUAL_DESKTOP')
                image = self.api.grab()
                try:
                    if not isinstance(image, Image.Image) or image.size != (desktop['width'], desktop['height']):
                        _fail('ACTUAL_VIRTUAL_DESKTOP_IMAGE_DIMENSIONS_DIFFER')
                    x, y = capture_bounds['x']-desktop['x'], capture_bounds['y']-desktop['y']
                    cropped = image.crop((x, y, x+capture_bounds['width'], y+capture_bounds['height'])).convert('RGB')
                    try:
                        if cropped.size != (capture_bounds['width'], capture_bounds['height']):
                            _fail('VISIBLE_CROP_PHYSICAL_DIMENSIONS_DIFFER')
                        raw = frame_png(cropped)
                        if len(raw) > MAX_BYTES: _fail('VISIBLE_FRAME_PNG_BYTE_BUDGET_EXCEEDED')
                        stats = ImageStat.Stat(cropped)
                        quality = {'channel_stddev': stats.stddev, 'extrema': [list(v) for v in cropped.getextrema()]}
                    finally: cropped.close()
                finally:
                    if isinstance(image, Image.Image): image.close()
                after = _window(self.source.sample())
                after_desktop = _rect(self.api.desktop_bounds(), desktop=True)
                after_dwm = self.api.visible_bounds(after)
                after_capture = _rect(after_dwm) if after_dwm is not None else dict(after['bounds'])
                if before != after or desktop != after_desktop or dwm != after_dwm or capture_bounds != after_capture:
                    _fail('VISIBLE_TARGET_OR_DISPLAY_CHANGED_DURING_CAPTURE')
            # DPI restoration must succeed before publishing any successful frame.
            ident = uuid.uuid4().hex; image_sha = sha(raw); stamp = self.clock()
            if type(stamp) not in (int, float) or not 0 <= stamp < 1e12: _fail('BOUNDED_FRAME_CAPTURE_TIME_REQUIRED')
            image_ref = {'path': ident+'/frame.png', 'sha256': image_sha, 'byte_length': len(raw),
                         'width': capture_bounds['width'], 'height': capture_bounds['height']}
            evidence = {'schema': 'eeas.visible-target-geometry.v1', 'before': before, 'after': after,
                'desktop_bounds': desktop, 'capture_bounds': capture_bounds, 'bounds_source': 'DWM_EXTENDED_FRAME_BOUNDS' if dwm is not None else 'PHYSICAL_GET_WINDOW_RECT',
                'target_ref': target_ref, 'binding': binding, 'image_ref': image_ref, 'provider_kind': self.provider_kind}
            proof_raw = canonical(evidence).encode('utf-8')
            evidence_ref = {'id': ident, 'revision': 1, 'sha256': sha(proof_raw)}
            proof = {'status': 'VISIBLE_TARGET_RECTANGLE', 'image_sha256': image_sha,
                'target_bounds': before['bounds'], 'capture_bounds': capture_bounds,
                'target_identity_sha256': before['identity_sha256'], 'coordinate_space': COORDINATES,
                'capture_method': 'PILLOW_ACTUAL_DESKTOP_CROP', 'self_exclusion_verified': False, 'evidence_ref': evidence_ref}
            public = {'session_ref': binding['sdk_session_ref'], 'target_ref': target_ref,
                'surface_ref': binding['surface_ref'], 'input_epoch': binding['input_epoch'],
                'workspace_session_ref': binding['workspace_session_ref'], 'captured_at': stamp,
                'image_ref': image_ref, 'width': capture_bounds['width'], 'height': capture_bounds['height'],
                'status': 'OBSERVATION_NOT_ACCEPTED_ENGINEERING_FACT', 'runtime_role': 'READONLY_OBSERVATION',
                'source': SOURCE, 'provider_kind': self.provider_kind, 'pixel_source': 'VISIBLE_TARGET_RECTANGLE',
                'pixel_source_proof': proof, 'pixel_statistics': quality, 'self_exclusion': 'NOT_VERIFIED',
                'automatic_input_authorized': False, 'visual_coordinate_input_authorized': False,
                'coverage': {'pixel_source': 'VISIBLE_TARGET_RECTANGLE', 'visible_target_rectangle': True,
                    'whole_page_seen': False, 'occlusion_checked': False, 'target_window_only': False},
                'limitations': ['ONLY_CURRENTLY_VISIBLE_RECTANGLE', 'OTHER_WINDOWS_MAY_OCCLUDE_TARGET',
                    'NOT_A_CUA_INPUT_FRAME', 'WINDOW_CONTENT_CAN_CHANGE_AFTER_CAPTURE']}
            ref = {'id': ident, 'revision': 1, 'sha256': _sha(public)}
            record = {'schema': 'eeas.visible-window-frame.v1', 'frame_ref': ref, 'public': public}
            directory = self._path(ident, 'frame.png').parent; directory.mkdir(exist_ok=False)
            self._write(self._path(ident, 'frame.png'), raw)
            self._write(self._path(ident, 'proof.json'), proof_raw)
            self._write(self._path(ident, 'observation.json'), canonical(record).encode('utf-8'))
            return json.loads(canonical({**public, 'frame_ref': ref}))

