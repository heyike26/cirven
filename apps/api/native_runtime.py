"""M0 native collaboration composition, with no AppBar or model loop.

Only an authenticated Main request binds its own windows. Driver/session and
foreground-hook construction happen after that bind, never on module import.
The selected runtime descriptor is host configuration, never renderer input.
"""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid

from fastapi import Depends, Header
from pydantic import Field

from apps.api.desktop_context import DesktopContextRPC, register_desktop_context_routes
from apps.api.orb_pointer_watch import OrbPointerWatchService, register_orb_pointer_watch_routes
from packages.adapters.cua_desktop_input import (
    CuaDesktopInput, NativeCuaSessionFactory, _checked_reference_file, _readonly_manifest,
)
from packages.adapters.desktop_current_context import DesktopCurrentContext, Win32CurrentWindowSource
from packages.adapters.native_document_identity import NativeDocumentIdentity
from packages.domain.native_collaboration import NativeCollaborationAuthority, NativeCollaborationBindInput
from packages.domain.questions import RequestModel
from packages.domain.store import Conflict, InvalidInput


class NativePresentationInput(RequestModel):
    presentation: str = Field(strict=True, pattern=r'^(NATIVE_COMPANION|PROJECT_WORKBENCH)$')


def selected_readonly_runtime(*, descriptor=None, descriptor_sha256=None):
    """Validate real selected bytes; absent configuration stays unavailable."""
    descriptor = descriptor if descriptor is not None else os.environ.get('EEAS_COLLABORATION_RUNTIME_DESCRIPTOR')
    expected = descriptor_sha256 if descriptor_sha256 is not None else os.environ.get('EEAS_COLLABORATION_RUNTIME_DESCRIPTOR_SHA256')
    if descriptor is None and expected is None:
        return None
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise InvalidInput('Exact selected native runtime descriptor SHA256 is required')
    path = _checked_reference_file({'path': descriptor, 'sha256': expected})
    value = json.loads(path.read_bytes())
    keys = {'schema', 'runtime_root', 'component_manifest_ref', 'readonly_capability_manifest_ref'}
    if (not isinstance(value, dict) or not keys <= set(value)
            or set(value)-keys-{'notepad_append_qualification_ref'}
            or value['schema'] != 'eeas.native-collaboration-runtime.v1'):
        raise InvalidInput('Exact selected readonly runtime descriptor schema is required')
    root = Path(value['runtime_root'])
    if not root.is_absolute() or any(item.is_symlink() for item in (root, *root.parents)):
        raise InvalidInput('Selected native runtime must be an absolute real directory')
    if os.name == 'nt' and root.drive.upper() not in {'D:', 'E:'}:
        raise InvalidInput('Selected native runtime must remain on D or E')
    component = _checked_reference_file(value['component_manifest_ref'], boundary=root)
    policy = _checked_reference_file(value['readonly_capability_manifest_ref'], boundary=root)
    if 'notepad_append_qualification_ref' in value:
        _checked_reference_file(value['notepad_append_qualification_ref'], boundary=root)
    _readonly_manifest(policy)
    manifest = json.loads(component.read_bytes())
    wheels = [item for item in manifest.get('wheels', []) if item.get('name') == 'cua-driver']
    if len(wheels) != 1 or wheels[0].get('version') != '0.35.2' or not wheels[0].get('engine_files'):
        raise InvalidInput('Frozen selected Cua driver 0.35.2 engine manifest is required')
    return copy.deepcopy(value)


class NativeCollaborationRuntime:
    def __init__(self, data_dir, *, authority=None, runtime_descriptor=None,
                 runtime_descriptor_sha256=None, source_factory=None, session_factory=None, platform=None):
        self.platform = os.name if platform is None else platform
        self.ws = authority or NativeCollaborationAuthority(Path(data_dir).resolve(), platform=platform)
        self.authority = self.ws
        self.lock = threading.RLock()
        self.source_factory = source_factory or (Win32CurrentWindowSource if self.platform == 'nt' else None)
        self.session_factory = session_factory
        self.task_service = None
        self.source = self.context = self.cua = self.sdk_receipt = None
        self.visible_frames = None
        self.visible_frame_error = None
        self.context_rpc = DesktopContextRPC(self.ws)
        self.orb_pointer_watch = OrbPointerWatchService(self.ws)
        self.runtime, self.runtime_error, self.lifecycle_error = None, None, None
        self.closed = False
        self.close_receipt = None
        try:
            self.runtime = selected_readonly_runtime(descriptor=runtime_descriptor,
                descriptor_sha256=runtime_descriptor_sha256)
        except Exception as error:
            self.runtime_error = type(error).__name__

    def get(self):
        value = self.ws.get()
        sdk = self.sdk_receipt is not None and not self.closed
        return {**value, 'context': {**self.context_rpc.status(),
            'metadata_available': self.context is not None and value['status'] == 'ACTIVE',
            'accessibility_configured': sdk, 'capture_configured': not self.closed and (sdk or self.visible_frames is not None),
            'visible_rectangle_capture_configured': not self.closed and getattr(self.visible_frames, 'pixel_source', None) == 'VISIBLE_TARGET_RECTANGLE',
            'exact_window_capture_configured': not self.closed and getattr(self.visible_frames, 'pixel_source', None) == 'EXACT_TARGET_WINDOW',
            'visible_rectangle_capture_reason': self.visible_frame_error,
            'native_capture_qualified': False, 'native_input_qualified': False,
            'runtime_reason': self.lifecycle_error or self.runtime_error or
                ('WINDOWS_NATIVE_CONTEXT_UNAVAILABLE' if self.source_factory is None else
                 'SELECTED_READONLY_RUNTIME_NOT_CONFIGURED' if not sdk else None)}}

    def _factory(self):
        if self.session_factory is not None:
            return self.session_factory
        if self.runtime is None or self.platform != 'nt':
            return None
        return NativeCuaSessionFactory(runtime_root=self.runtime['runtime_root'],
            component_manifest_ref=self.runtime['component_manifest_ref'],
            capability_manifest_ref=self.runtime['readonly_capability_manifest_ref'],
            readonly_capability_manifest_ref=self.runtime['readonly_capability_manifest_ref'])

    def task_target_authority(self, *args):
        service = self.task_service
        return (service.target_authority if service is not None else self.ws.target_authority)(*args)

    def task_control_guard(self, *args):
        service = self.task_service
        return (service.control_guard if service is not None else self.ws.dispatch_guard)(*args)

    def _start_context(self):
        if self.source_factory is None:
            return
        self.source = self.source_factory(excluded_pid_reader=self.ws.excluded_pids)
        self.ws.set_window_source(self.source)
        if os.name == 'nt' and self.platform == 'nt' and isinstance(self.source, Win32CurrentWindowSource):
            try:
                from packages.adapters.window_graphics_frame import WindowGraphicsFrames
                self.visible_frames = WindowGraphicsFrames(self.ws._path('native-visible-artifacts'),
                    window_source=self.source)
            except Exception as error:
                # A capture failure must not take down metadata or the original
                # SDK. It cannot silently qualify SDK pixels as visible pixels.
                self.visible_frame_error = type(error).__name__
        factory = self._factory()
        if factory is not None:
            self.cua = CuaDesktopInput(data_dir=self.ws._path('native-sdk-artifacts'), session_factory=factory,
                target_authority=self.task_target_authority, observation_guard=self.ws.observation_guard,
                control_guard=self.task_control_guard, platform=self.platform)
            binding = self.ws.observation_session_binding(
                session_label='readonly-' + self.ws.get()['session_ref']['id'],
                budget={'max_actions': 0, 'timeout_ms': 5000, 'max_frame_age_ms': 5000,
                        'ttl_seconds': 300, 'allow_foreground': False})
            try:
                self.sdk_receipt = self.cua.open_session(binding)
                self.ws.set_observation_session(self.sdk_receipt)
            except Exception as error:
                self.lifecycle_error = 'READONLY_SDK_UNAVAILABLE:' + type(error).__name__
                self.cua.shutdown()
                self.cua, self.sdk_receipt = None, None
        self.context = DesktopCurrentContext(window_source=self.source, context_authority=self.ws.context_authority,
            cua_adapter=self.cua, binding_reader=self.ws.binding_reader, observation_guard=self.ws.observation_guard,
            before_read=self.ensure_readonly_session,
            document_identity_reader=NativeDocumentIdentity().read,
            visual_capture=self.visible_frames.capture if self.visible_frames is not None else None)
        self.context_rpc.context = self.context
        try:
            self.context.start_tracking()
            self.context.observe_foreground()
        except Exception as error:
            # Metadata sampling may still work; an event hook error cannot
            # silently turn a prior foreground record into lifetime authority.
            self.lifecycle_error = 'FOREGROUND_TRACKING_UNAVAILABLE:' + type(error).__name__

    def _window_readonly_policy(self, window):
        if self.runtime is None or not callable(getattr(self.source, 'process_executable', None)):
            return None
        executable = self.source.process_executable(window)
        document = {'version': 3, 'expires_after': '5m', 'idle_timeout': '5m',
            'allow': {'tools': ['get_window_state']},
            'resources': {'apps': [{'executable': executable, 'windows': 'all'}], 'desktop': {'display': False}}}
        raw = json.dumps(document, sort_keys=True, separators=(',', ':')).encode()
        digest = hashlib.sha256(raw).hexdigest()
        root = Path(self.runtime['runtime_root'])
        path = root / 'toolchain' / 'host-readonly-policies' / self.ws.generation / (digest + '.yaml')
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise Conflict('Readonly target policy path cannot contain links')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open('xb') as stream: stream.write(raw)
        if path.read_bytes() != raw: raise Conflict('Readonly target policy bytes changed')
        _readonly_manifest(path)
        return {'path': str(path), 'sha256': digest}

    def ensure_readonly_session(self, window=None):
        """Renew only a due readonly session when another observation is requested."""
        with self.lock:
            if self.closed: raise Conflict('Native collaboration runtime is closed')
            if self.cua is None or self.sdk_receipt is None: return
            self.ws.check_host(self.ws.token)
            previous = self.sdk_receipt['session_ref']
            record = self.cua.records.get(previous['id'])
            if not record or record['ref'] != previous or record.get('fault'):
                raise Conflict('Readonly session outcome is unknown; it cannot be adopted or automatically replayed')
            policy = self._window_readonly_policy(window) if window is not None else None
            policy_changed = policy is not None and record['binding'].get('readonly_policy_ref') != policy
            if self.cua.clock() < record['expires_at'] and not policy_changed: return
            closed = self.cua.close_session(previous)
            self.ws.clear_observation_session(previous, closed)
            self.sdk_receipt = None
            binding = self.ws.observation_session_binding(
                session_label='readonly-' + uuid.uuid4().hex,
                budget={'max_actions': 0, 'timeout_ms': 5000, 'max_frame_age_ms': 5000,
                        'ttl_seconds': 300, 'allow_foreground': False},
                **({'readonly_policy_ref': policy} if policy is not None else {}))
            try:
                receipt = self.cua.open_session(binding)
            except Exception as error:
                self.lifecycle_error = 'READONLY_SDK_UNAVAILABLE:' + type(error).__name__
                raise
            self.ws.set_observation_session(receipt)
            self.sdk_receipt = receipt
            self.lifecycle_error = None

    def bind(self, body):
        with self.lock:
            if self.closed:
                raise Conflict('Native collaboration runtime is closed')
            before = self.ws.get()
            active = self.ws.bind(body)
            if before.get('session_ref') == active['session_ref'] and self.context is not None:
                return self.get()
            try:
                self._start_context()
            except Exception as error:
                self.lifecycle_error = 'CURRENT_CONTEXT_UNAVAILABLE:' + type(error).__name__
            return self.get()

    def set_presentation(self, presentation):
        with self.lock:
            self.ws.set_presentation(presentation)
            return self.get()

    def exact_frame_image(self, context_ref, frame_ref):
        context, cua = self.context, self.cua
        if context is None:
            raise Conflict('Exact current frame reader is unavailable')
        observed = context.get(context_ref)
        frame = observed.get('observations', {}).get('visual')
        if frame and self.visible_frames is not None and self.visible_frames.has(frame_ref):
            if frame.get('frame_ref') != frame_ref or self.visible_frames.describe(frame_ref) != frame:
                raise Conflict('Preview differs from its exact readonly visible frame')
            binding = {key: observed[key] for key in
                ('workspace_session_ref', 'surface_ref', 'input_epoch', 'sdk_session_ref')}
            return self.visible_frames.lookup(frame_ref, target_ref=observed['window_ref'], binding=binding)
        if cua is None:
            raise Conflict('Exact original current frame reader is unavailable')
        record = cua.frames.get(frame_ref.get('id')) if isinstance(frame_ref, dict) else None
        if (not frame or frame['frame_ref'] != frame_ref or not record or record['ref'] != frame_ref
                or frame != {**record['public'], 'frame_ref': record['ref']}):
            raise Conflict('Preview does not belong to this exact original current-context frame')
        path = cua.data / frame['image_ref']['path']
        if (not path.resolve().is_relative_to(cua.data.resolve())
                or any(item.is_symlink() for item in (path, *path.parents))
                or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024):
            raise Conflict('Exact native frame bytes are unavailable or unsafe')
        raw = path.read_bytes()
        if len(raw) != frame['image_ref']['byte_length'] or hashlib.sha256(raw).hexdigest() != frame['image_ref']['sha256']:
            raise Conflict('Exact original native frame bytes changed')
        return {'bytes': raw, 'media_type': 'image/png', 'sha256': frame['image_ref']['sha256'],
                'width': frame['width'], 'height': frame['height']}

    @property
    def owned_processes(self):
        return getattr(self.visible_frames, 'owned_processes', ())

    def close(self):
        with self.lock:
            if self.closed:
                return copy.deepcopy(self.close_receipt)
            # Revoke scope before awaiting SDK shutdown; no new reads/actions
            # may enter the previous owned-host session after this point.
            try:
                host = self.ws.close()
            except Exception as error:
                host = {'status': 'CLOSE_UNKNOWN', 'error_type': type(error).__name__}
            self.closed = True
            outcomes = {'host': host}
            try:
                outcomes['orb_pointer_watch'] = self.orb_pointer_watch.close()
            except Exception as error:
                outcomes['orb_pointer_watch'] = {'status': 'CLOSE_UNKNOWN', 'error_type': type(error).__name__}
            if self.context is not None:
                try:
                    outcomes['context'] = self.context.close()
                except Exception as error:
                    outcomes['context'] = {'status': 'CLOSE_UNKNOWN', 'error_type': type(error).__name__}
            if callable(getattr(self.visible_frames, 'close', None)):
                try:
                    outcomes['capture'] = self.visible_frames.close()
                except Exception as error:
                    outcomes['capture'] = {'status': 'CLOSE_UNKNOWN', 'error_type': type(error).__name__}
            if self.cua is not None:
                try:
                    outcomes['sdk'] = self.cua.shutdown()
                except Exception as error:
                    outcomes['sdk'] = {'status': 'CLOSE_UNKNOWN', 'error_type': type(error).__name__}
            self.context_rpc.context = None
            self.context = self.source = self.cua = self.sdk_receipt = None
            if outcomes.get('capture', {}).get('status', 'CLOSED') == 'CLOSED':
                self.visible_frames = None
            unknown = any(isinstance(item, dict) and item.get('status') not in {'CLOSED'}
                for item in outcomes.values())
            self.close_receipt = {'status': 'CLOSE_UNKNOWN' if unknown else 'CLOSED', 'outcomes': outcomes, 'native_input_qualified': False,
                    'user_windows_modified': False, 'processes_terminated': False}
            return copy.deepcopy(self.close_receipt)


def register_native_collaboration_routes(app, store, data_dir, *, service=None, **options):
    existing = getattr(app.state, 'native_collaboration_runtime', None)
    if existing is not None and service is not None and existing is not service:
        raise Conflict('Native collaboration runtime cannot replace a live authority')
    value = existing or service or NativeCollaborationRuntime(data_dir, **options)
    if getattr(app.state, 'native_collaboration_routes_registered', False):
        return value
    app.state.native_collaboration_runtime = value
    app.state.native_collaboration = value.ws
    value.context_rpc.image_reader = value.exact_frame_image
    register_desktop_context_routes(app, store, data_dir, service=value.context_rpc)
    register_orb_pointer_watch_routes(app, value.orb_pointer_watch)

    def host(x_eeas_desktop_host: str | None = Header(None)):
        value.ws.check_host(x_eeas_desktop_host)

    def availability(x_eeas_desktop_host: str | None = Header(None)):
        value.ws.check_host(x_eeas_desktop_host, require_native=False)

    root = '/api/desktop-host/collaboration'

    @app.get(root + '/get', dependencies=[Depends(availability)])
    def get():
        return value.get()

    @app.get(root + '/own-main-geometry', dependencies=[Depends(host)])
    def own_main_geometry():
        return value.ws.own_geometry()

    @app.post(root + '/bind', dependencies=[Depends(host)])
    def bind(body: NativeCollaborationBindInput):
        return value.bind(body.model_dump())

    @app.post(root + '/presentation', dependencies=[Depends(host)])
    def presentation(body: NativePresentationInput):
        return value.set_presentation(body.presentation)

    @app.post(root + '/close', dependencies=[Depends(availability)])
    async def close():
        coordinator = getattr(app.state, 'owner_shutdown', None)
        if coordinator is not None:
            return await coordinator.close()
        # The exact original owner must revoke/cancel and settle its TASK session
        # while the shared worker still exists. Readonly/controller shutdown is
        # last even when cancellation/owned cleanup reports an unknown outcome.
        task_receipt = None
        if value.task_service is not None:
            try:
                task_receipt = await value.task_service.close()
                if not isinstance(task_receipt, dict) or task_receipt.get('status') not in {'CLOSED', 'CLOSE_UNKNOWN'}:
                    task_receipt = {'status': 'CLOSE_UNKNOWN', 'reason': 'Exact original task owner close receipt unavailable'}
            except Exception as error:
                task_receipt = {'status': 'CLOSE_UNKNOWN', 'error_type': type(error).__name__}
        result = await asyncio.to_thread(value.close)
        if task_receipt is not None:
            result.setdefault('outcomes', {})['tasks'] = task_receipt
            if task_receipt['status'] != 'CLOSED': result['status'] = 'CLOSE_UNKNOWN'
        return result

    app.state.native_collaboration_routes_registered = True
    return value
