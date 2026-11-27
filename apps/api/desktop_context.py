"""Trusted-main current-page observations; replay never takes a second screenshot.

Native lifecycle, the original Cua worker and exact frame-image lookup are
injected. This module creates no driver, session or input authorization.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from fastapi import Depends, Header, Query
from fastapi.responses import JSONResponse, Response
from pydantic import Field, model_validator

from packages.domain.discovery_schema import RevisionRef
from packages.domain.document_import import image_metadata
from packages.domain.local_workspaces import canonical
from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.store import Conflict, NotFound
from packages.domain.context_read_budget import context_read_scope,source_bytes


class DesktopObservationNeed(RequestModel):
    metadata: bool = Field(strict=True)
    accessibility: bool = Field(strict=True)
    visual: bool = Field(strict=True)
    ocr: bool = Field(strict=True)

    @model_validator(mode='after')
    def nonempty(self):
        if not any((self.metadata, self.accessibility, self.visual, self.ocr)):
            raise ValueError('At least one actual observation mode is required')
        return self


class DesktopObserveInput(RequestModel):
    operation_id: Id
    intent: str = Field(strict=True, min_length=1, max_length=8192)
    task_ref: RevisionRef | None = None
    need: DesktopObservationNeed
    expected_workspace_session_ref: RevisionRef | None = None


class DesktopContextLookup(RequestModel):
    context_ref: RevisionRef


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def safe(value):
    """Remove native/private storage fields; refs still bind the original record."""
    private = {'path', 'hwnd', 'hwnd_decimal', 'pid', 'owner_pid', 'process_id',
        'window_id', 'handle', 'creation', 'marker_name', 'marker_value',
        'host_token', 'url', 'download_url', 'native_path', 'document_identity_private'}
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items() if key not in private}
    if isinstance(value, list):
        return [safe(item) for item in value]
    return value


class DesktopContextRPC:
    def __init__(self, workspace, *, context=None, exact_frame_image_reader=None):
        self.ws, self.context = workspace, context
        self.image_reader = exact_frame_image_reader
        self.generation = uuid.uuid4().hex
        # This lock is intentionally separate from WorkspaceDesktop.lock.
        # get_context may wait for the sole SDK worker which acquires that lock.
        self.condition = threading.Condition(threading.RLock())

    def _path(self, *parts):
        return self.ws._path('context-rpc', *parts)

    def _read(self, path):
        return self.ws._read(path, authority=True)

    def activation_state(self, *, settle_trigger=False):
        # Authenticated Main reads only; no SDK, capture, records or new owner.
        unavailable = {'schema': 'eeas.quick-activation-state.v1', 'status': 'UNAVAILABLE',
            'automatic_input_authorized': False}
        try:
            read = getattr(self.context, 'activation_state', None)
            if not callable(read):
                return unavailable
            state = read(settle_trigger=True) if settle_trigger else read()
            if (not isinstance(state, dict) or state.get('status') != 'OBSERVED'
                    or not isinstance(state.get('foreground_identity_sha256'), str)
                    or len(state['foreground_identity_sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in state['foreground_identity_sha256'])
                    or type(state.get('last_input_tick')) is not int
                    or not 0 <= state['last_input_tick'] <= 0xffffffff):
                return unavailable
            return {'schema': unavailable['schema'], 'status': 'OBSERVED',
                'authority_generation': self.generation,
                'foreground_identity_sha256': state['foreground_identity_sha256'],
                'last_input_tick': state['last_input_tick'], 'automatic_input_authorized': False}
        except Exception:
            return unavailable

    def status(self):
        host = self.ws.get()
        scoped = host.get('type') == 'NATIVE_COLLABORATION'
        ready = self.context is not None and (not scoped or host.get('status') == 'ACTIVE')
        return {'status': 'CONFIGURED' if ready else 'UNAVAILABLE',
            'configured': self.context is not None,
            'reason': None if ready else 'CURRENT_CONTEXT_NOT_CONFIGURED' if self.context is None else 'OWNED_HOST_SCOPE_NOT_CURRENT',
            'runtime_role': 'READONLY_OBSERVATION', 'host_status': host.get('status'),
            'workspace_session_ref': host.get('session_ref'), 'surface_ref': host.get('surface_ref'),
            'automatic_input_authorized': False, 'send_full_image_to_model': False,
            'native_capture_qualified': False,
            'qualification_scope': 'RPC_RECORDING_ONLY_NATIVE_CAPTURE_REQUIRES_SEPARATE_RECEIPT'}

    def _current(self, observation):
        if self.context is None or not observation.get('context_ref'):
            return False
        try:
            value = self.context.get(observation['context_ref'])
            self._validate(value)
            if safe(value) != observation:
                return False
            checker = getattr(self.context, 'check_observation_current', self.context.check_current)
            return checker(value['context_ref'], value['window_ref'],
                value['surface_ref'], value['input_epoch']) is True
        except Exception:
            return False

    def _validate(self, value):
        if not isinstance(value, dict) or len(canonical(value).encode()) > 2 * 1024 * 1024:
            raise Conflict('Current context output is invalid or exceeds its limit')
        if value.get('automatic_input_authorized') is not False:
            raise Conflict('A page observation must carry no input authorization')
        if value.get('send_full_image_to_model', False) is not False:
            raise Conflict('A local observation must not enable automatic full-image sending')
        if value.get('coverage', {}).get('whole_page_seen', False) is not False:
            raise Conflict('Current page observation does not prove complete page coverage')
        if value.get('coverage', {}).get('page_content_is_engineering_fact', False) is not False:
            raise Conflict('A page observation is not an accepted engineering fact')
        if value.get('context_ref') is not None:
            if self.ws.get().get('type') == 'NATIVE_COLLABORATION' and value.get('runtime_role') != 'READONLY_OBSERVATION':
                raise Conflict('Native current context requires its dedicated readonly role')
            ref = RevisionRef.model_validate(value['context_ref']).model_dump()
            original = {k: v for k, v in value.items() if k != 'context_ref'}
            if digest(original) != ref['sha256']:
                raise Conflict('Exact current context snapshot hash differs')
            if self.context is None or self.context.get(ref) != value:
                raise Conflict('Context snapshot is not held by the current authority')
        visual = value.get('observations', {}).get('visual')
        if visual and visual.get('visual_coordinate_input_authorized', False) is not False:
            raise Conflict('Readonly page frames must carry no coordinate input authority')
        if visual and value.get('coverage', {}).get('pixel_source') == 'VERIFIED_TARGET_PIXELS':
            if visual.get('pixel_source') != 'VERIFIED_TARGET_PIXELS' or not visual.get('pixel_source_proof'):
                raise Conflict('Page pixel coverage requires exact frame source evidence')

    def _preview(self, value):
        frame = value.get('observations', {}).get('visual')
        if not frame or not value.get('context_ref'):
            return {'status': 'UNAVAILABLE', 'reason': 'NO_EXACT_VISUAL_FRAME'}
        if self.image_reader is None:
            return {'status': 'UNAVAILABLE', 'reason': 'EXACT_FRAME_IMAGE_READER_NOT_CONFIGURED'}
        try:
            context_ref = RevisionRef.model_validate(value['context_ref']).model_dump()
            frame_ref = RevisionRef.model_validate(frame['frame_ref']).model_dump()
            # Caller supplies neither a path nor a URL. Root resolves its own
            # registered context -> original frame -> original image bytes.
            result = self.image_reader(copy.deepcopy(context_ref), copy.deepcopy(frame_ref))
            raw = result['bytes']
            if not isinstance(raw, bytes) or len(raw) > 16 * 1024 * 1024:
                raise Conflict('Exact preview bytes are unavailable or exceed the limit')
            info = image_metadata(raw)
            if (info['media_type'] != 'image/png' or result['media_type'] != 'image/png'
                    or info['sha256'] != result['sha256']
                    or info['sha256'] != frame['image_ref']['sha256']
                    or info['byte_length'] != frame['image_ref']['byte_length']
                    or any(type(result[key]) is not int or result[key] != info[key]
                        or type(frame[key]) is not int or frame[key] != info[key]
                        for key in ('width', 'height'))):
                raise Conflict('Exact frame image metadata or bytes differ')
            identifier = uuid.uuid4().hex
            path = self._path('images', identifier + '.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            ref = {'id': identifier, 'revision': 1, 'sha256': info['sha256']}
            receipt = {'image_ref': ref, 'context_ref': context_ref, 'frame_ref': frame_ref, **info}
            self.ws._write(self._path('images', identifier + '.json'), receipt)
            return {'status': 'AVAILABLE', 'pixel_source': frame.get('pixel_source', 'UNKNOWN'),
                'qualification_scope': 'EXACT_IMAGE_BYTES_ONLY_NOT_TARGET_PIXEL_QUALIFICATION',
                'image_ref': {**ref, **info,
                'download_url': '/api/desktop-host/context/images/' + identifier
                    + '?revision=1&sha256=' + info['sha256']}}
        except Exception as error:
            return {'status': 'UNAVAILABLE', 'reason': 'EXACT_FRAME_IMAGE_UNAVAILABLE',
                'error_type': type(error).__name__}

    def _response(self, record, replayed):
        value = copy.deepcopy(record['response'])
        value['replayed'] = replayed
        value['current'] = self._current(value['observation'])
        return value

    def observe(self, body):
        body = DesktopObserveInput.model_validate(body).model_dump()
        path = self._path('operations', body['operation_id'] + '.json')
        request_sha = digest(body)
        with self.condition:
            deadline = time.monotonic() + 45
            while path.exists():
                prior = self._read(path)
                if prior['request_sha256'] != request_sha or prior['request'] != body:
                    raise Conflict('Observation operation id is already bound to another request')
                if prior['state'] != 'PROCESSING':
                    return self._response(prior, True)
                if prior['generation'] != self.generation:
                    prior['state'] = 'UNKNOWN'
                    prior['response'] = self._unavailable(body, request_sha,
                        'OBSERVATION_INTERRUPTED_OR_RESTARTED', status='UNKNOWN')
                    self.ws._write(path, prior)
                    return self._response(prior, True)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._unavailable(body, request_sha, 'OBSERVATION_STILL_PROCESSING', status='PROCESSING', replayed=True)
                self.condition.wait(remaining)
            record = {'request': body, 'request_sha256': request_sha,
                'generation': self.generation, 'state': 'PROCESSING'}
            self.ws._write(path, record)
        # No WorkspaceDesktop lock held while the original SDK worker is awaited.
        try:
            if self.context is None:
                response = self._unavailable(body, request_sha, 'CURRENT_CONTEXT_NOT_CONFIGURED')
            else:
                args = {key: body[key] for key in ('intent', 'task_ref', 'need', 'expected_workspace_session_ref')}
                observed = self.context.get_context(**args)
                self._validate(observed)
                # Optional private path evidence never enters page/model/renderer JSON.
                if observed.get('document_identity', {}).get('status') == 'OBSERVED':
                    try:
                        proof = self.context.document_identity(observed['context_ref'])
                        if proof is not None:
                            self.ws._write(self._path('document-identities', observed['context_ref']['id'] + '.json'),
                                {'context_ref': observed['context_ref'], 'proof': proof, 'proof_sha256': digest(proof)})
                    except Exception:
                        record['document_identity_recording'] = 'UNAVAILABLE'
                response = {'operation_id': body['operation_id'], 'request_sha256': request_sha,
                    'replayed': False, 'observation': safe(observed), 'current': False,
                    'preview': self._preview(observed), 'automatic_input_authorized': False,
                    'send_full_image_to_model': False}
        except Exception as error:
            response = self._unavailable(body, request_sha, 'CONTEXT_OBSERVATION_FAILED', status='FAILED')
            response['observation']['error_type'] = type(error).__name__
            # Original failure is kept privately; arbitrary exception text may
            # include native handles, a local path or confidential page content.
            record['original_failure'] = {'type': type(error).__name__, 'message': str(error)[:65536]}
        with self.condition:
            record['response'] = response
            record['state'] = 'COMPLETED'
            if response['observation'].get('context_ref'):
                ref = response['observation']['context_ref']
                self.ws._write(self._path('contexts', ref['id'] + '.json'), response)
            self.ws._write(path, record)
            self.condition.notify_all()
            return self._response(record, False)

    @staticmethod
    def _unavailable(body, request_sha, reason, *, status='UNAVAILABLE', replayed=False):
        return {'operation_id': body['operation_id'], 'request_sha256': request_sha,
            'replayed': replayed, 'current': False,
            'observation': {'status': status, 'reason': reason,
                'observations': {}, 'coverage': {'metadata': False, 'accessibility': False,
                    'visual': False, 'ocr': False, 'whole_page_seen': False,
                    'page_content_is_engineering_fact': False},
                'automatic_input_authorized': False, 'send_full_image_to_model': False},
            'preview': {'status': 'UNAVAILABLE', 'reason': 'NO_EXACT_VISUAL_FRAME'},
            'automatic_input_authorized': False, 'send_full_image_to_model': False}

    def lookup(self, reference):
        reference = RevisionRef.model_validate(reference).model_dump()
        path = self._path('contexts', reference['id'] + '.json')
        if not path.exists():
            raise NotFound('Exact current context was not registered by this host')
        value = self._read(path)
        if value['observation']['context_ref'] != reference:
            raise Conflict('Exact context reference differs from its frozen observation')
        return self._response({'response': value}, True)

    def frozen_lookup(self, reference):
        """Historical source bytes are data; they never renew a native input scope."""
        reference = RevisionRef.model_validate(reference).model_dump()
        path = self._path('contexts', reference['id'] + '.json')
        if not path.exists():
            raise NotFound('Saved page reference is unavailable')
        value = self.ws._read(path, authority=False)
        if value.get('observation', {}).get('context_ref') != reference:
            raise Conflict('Saved page reference differs from its immutable observation')
        value.update(current=False, automatic_input_authorized=False,
                     source_role='FROZEN_DATA_NO_CURRENT_NATIVE_AUTHORITY')
        value['observation']['automatic_input_authorized'] = False
        image = value.get('preview', {}).get('image_ref')
        if image:
            image['download_url'] = ('/api/desktop-host/context/frozen-images/' + image['id']
                + '?revision=' + str(image['revision']) + '&sha256=' + image['sha256'])
        return value

    def frozen_document_identity(self, reference):
        """Private provenance only; it cannot renew current window authority."""
        reference = RevisionRef.model_validate(reference).model_dump()
        observed = self.frozen_lookup(reference)['observation']
        public = observed.get('document_identity', {})
        if public.get('status') != 'OBSERVED':
            return None
        path = self._path('document-identities', reference['id'] + '.json')
        if not path.exists():
            return None
        value = self.ws._read(path, authority=False)
        proof = value['proof']
        basis = {key: proof[key] for key in ('source', 'native_path', 'saved', 'window_lifetime_sha256', 'content_equals_saved_bytes')}
        if (value['context_ref'] != reference or digest(proof) != value['proof_sha256']
                or digest(basis) != proof['identity_sha256'] or proof['identity_sha256'] != public.get('identity_sha256')
                or proof['window_lifetime_sha256'] != observed.get('window_lifetime_sha256')
                or proof['source'] != 'WORD_WINDOW_NATIVEOM_PROPERTYGET'
                or proof['content_equals_saved_bytes'] is not False or type(proof['saved']) is not bool
                or not isinstance(proof['native_path'], str) or not 0 < len(proof['native_path']) <= 4096):
            raise Conflict('Saved document identity differs from its exact page observation')
        return proof

    def frozen_image(self, reference):
        reference = RevisionRef.model_validate(reference).model_dump()
        manifest = self._path('images', reference['id'] + '.json')
        if not manifest.exists():
            raise NotFound('Saved screenshot is unavailable')
        value = self.ws._read(manifest, authority=False)
        if value.get('image_ref') != reference:
            raise Conflict('Saved image reference changed')
        path = self._path('images', reference['id'] + '.png')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise Conflict('Saved screenshot storage is unsafe or unavailable')
        with context_read_scope():
            raw = source_bytes(path,16 * 1024 * 1024)
        info = image_metadata(raw)
        if any(info[key] != value[key] for key in info):
            raise Conflict('Saved screenshot bytes differ from their receipt')
        return raw

    def thumbnail(self, reference):
        from packages.adapters.native_frame_view import frame_view
        snapshot = self.frozen_lookup(reference)
        preview = snapshot.get('preview') or {}
        if preview.get('status') != 'AVAILABLE':
            raise NotFound('This page reference has no saved screenshot')
        image = preview['image_ref']
        visual = snapshot['observation']['observations']['visual']
        result = frame_view(self.frozen_image({key: image[key] for key in ('id', 'revision', 'sha256')}),
            context_ref=reference, frame_ref=visual['frame_ref'], image_ref=image,
            pixel_source=preview.get('pixel_source', 'UNKNOWN'), max_dimension=320, max_bytes=512 * 1024)
        block = next(item for item in result['content'] if item['type'] == 'image')
        return {'context_ref': reference, 'image_sha256': image['sha256'],
                'data_url': 'data:image/png;base64,' + block['data'], 'max_dimension': 320,
                'rendering': 'DOWNSAMPLED_PREVIEW_ONLY', 'automatic_input_authorized': False}

    def image(self, reference):
        reference = RevisionRef.model_validate(reference).model_dump()
        manifest = self._path('images', reference['id'] + '.json')
        if not manifest.exists():
            raise NotFound('Exact context preview image is unavailable')
        value = self._read(manifest)
        if value['image_ref'] != reference:
            raise Conflict('Exact context preview image reference differs')
        path = self._path('images', reference['id'] + '.png')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise Conflict('Exact context preview image storage is unsafe or unavailable')
        with context_read_scope():
            raw = source_bytes(path,16 * 1024 * 1024)
        info = image_metadata(raw)
        if any(info[key] != value[key] for key in info):
            raise Conflict('Exact context preview image bytes differ from its frozen receipt')
        return raw


def register_desktop_context_routes(app, store, data_dir, *, service=None, workspace=None,
        context=None, exact_frame_image_reader=None):
    """Register only trusted-main RPC; Native/Cua lifecycle remains Root-owned."""
    existing = getattr(app.state, 'desktop_context_rpc', None)
    if existing is not None and service is not None and existing is not service:
        raise Conflict('Desktop context routes cannot replace the current authority')
    if getattr(app.state, 'desktop_context_routes_registered', False):
        return existing
    value = existing or service or DesktopContextRPC(workspace or getattr(app.state, 'native_collaboration', None)
        or app.state.workspace_desktop,
        context=context if context is not None else getattr(app.state, 'desktop_current_context', None),
        exact_frame_image_reader=exact_frame_image_reader)
    app.state.desktop_context_rpc = value

    def host(x_eeas_desktop_host: str | None = Header(None)):
        value.ws.check_host(x_eeas_desktop_host)

    def availability(x_eeas_desktop_host: str | None = Header(None)):
        from packages.domain.native_collaboration import NativeCollaborationAuthority
        if isinstance(value.ws, NativeCollaborationAuthority):
            value.ws.check_host(x_eeas_desktop_host, require_native=False)
        else:
            value.ws.check_host(x_eeas_desktop_host)

    root = '/api/desktop-host/context'

    @app.get(root + '/activation-state', dependencies=[Depends(availability)])
    def activation_state(settle_trigger: bool = False):
        return JSONResponse(value.activation_state(settle_trigger=settle_trigger), headers={'Cache-Control': 'no-store'})

    @app.get(root + '/status', dependencies=[Depends(availability)])
    @app.get(root + '/get', dependencies=[Depends(availability)])
    def status():
        return value.status()

    @app.post(root + '/observe', dependencies=[Depends(host)])
    def observe(body: DesktopObserveInput):
        result = value.observe(body.model_dump())
        return JSONResponse(result, status_code=200 if result['replayed'] else 201)

    @app.post(root + '/get', dependencies=[Depends(host)])
    def get(body: DesktopContextLookup):
        return value.lookup(body.context_ref.model_dump())

    @app.post(root + '/frozen', dependencies=[Depends(host)])
    def frozen(body: DesktopContextLookup):
        return value.frozen_lookup(body.context_ref.model_dump())

    @app.post(root + '/thumbnail', dependencies=[Depends(host)])
    def thumbnail(body: DesktopContextLookup):
        return value.thumbnail(body.context_ref.model_dump())

    @app.get(root + '/frozen-images/{image_id}', dependencies=[Depends(host)])
    def frozen_image(image_id: Id, revision: int = Query(ge=1), sha256: Hash = Query()):
        ref = RevisionRef(id=image_id, revision=revision, sha256=sha256).model_dump()
        return Response(value.frozen_image(ref), media_type='image/png', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get(root + '/images/{image_id}', dependencies=[Depends(host)])
    def image(image_id: Id, revision: int = Query(ge=1), sha256: Hash = Query()):
        ref = RevisionRef(id=image_id, revision=revision, sha256=sha256).model_dump()
        return Response(value.image(ref), media_type='image/png', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    app.state.desktop_context_routes_registered = True
    return value
