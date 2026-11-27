"""Real private HTTP + PNG/ledger integration with injected capture boundary.

No Windows APIs, desktop screenshots, GUI, model calls or native input. The
original runtime/context/authority/RPC/PNG persistence run without mocks; only
the external capture/window/SDK transports use explicit original fixtures.
"""
import copy
from io import BytesIO

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from apps.api.native_runtime import NativeCollaborationRuntime, register_native_collaboration_routes
from packages.adapters.visible_window_frame import VisibleWindowFrames
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict
from test_native_collaboration import BIND, TOKEN, HostLifecycleCounter, SessionFactoryCounter
from test_visible_window_frame import CaptureApi, Source, check, window


HEADERS = {'X-EEAS-Desktop-Host': TOKEN}
OBSERVE = {'operation_id': 'visible-one', 'intent': '解释当前页面的这一处原创夹具问题',
    'need': {'metadata': True, 'accessibility': False, 'visual': True, 'ocr': False}}


class WindowTransport(Source):
    def __init__(self):
        super().__init__(window(title='原创页面夹具，不是实际用户窗口'))
        self.window = self.current  # Existing readonly SDK transport fixture.
        self.closed = False

    def inspect(self, hwnd):
        check(not self.closed and hwnd == self.current['window_id'])
        return copy.deepcopy(self.current)

    def start(self, callback):
        self.callback = callback
        return {'status': 'INJECTED_WINDOW_TRANSPORT_NOT_WINDOWS'}

    def close(self):
        self.closed = True
        return {'status': 'CLOSED'}


def scene(tmp_path, *, sdk=False):
    source, capture = WindowTransport(), CaptureApi()
    factory = SessionFactoryCounter(source) if sdk else None
    authority = NativeCollaborationAuthority(tmp_path, token=TOKEN, owner_pid=123,
        native=HostLifecycleCounter(), platform='POLICY_FAULT_PROBE')

    class InjectedCaptureRuntime(NativeCollaborationRuntime):
        def _start_context(self):
            super()._start_context()
            # The native default intentionally rejects WSL. Attach the actual
            # adapter at its documented external-capture seam, never fake nt.
            self.visible_frames = VisibleWindowFrames(tmp_path / 'injected-frame-ledger',
                window_source=source, capture_api=capture, platform='INJECTED_NOT_WINDOWS')
            self.context.visual_capture = self.visible_frames.capture

    runtime = InjectedCaptureRuntime(tmp_path, authority=authority, platform='POLICY_FAULT_PROBE',
        source_factory=lambda **_options: source, session_factory=factory)
    app = FastAPI()

    @app.exception_handler(Conflict)
    async def conflict(_request, error):
        return JSONResponse({'error': str(error)}, status_code=409)

    register_native_collaboration_routes(app, None, tmp_path, service=runtime)
    return app, runtime, source, capture, factory


def bind(client):
    response = client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=HEADERS)
    check(response.status_code == 200, response.text)
    check(response.json()['status'] == 'ACTIVE')
    return response.json()


def observe(client, body=None):
    response = client.post('/api/desktop-host/context/observe', json=body or OBSERVE, headers=HEADERS)
    check(response.status_code in (200, 201), response.text)
    result = response.json()
    check(result['preview']['status'] == 'AVAILABLE', str(result))
    return result


def readonly(result):
    public = result['observation']
    visual = public['observations']['visual']
    check(result['current'] is True)
    check(public['runtime_role'] == 'READONLY_OBSERVATION')
    check(public['coverage']['pixel_source'] == 'VISIBLE_TARGET_RECTANGLE')
    check(visual['provider_kind'] == 'INJECTED_TEST_PROVIDER_NOT_WINDOWS_EVIDENCE')
    check(visual['pixel_source_proof']['self_exclusion_verified'] is False)
    check(visual['coverage']['occlusion_checked'] is False)
    check(not visual['automatic_input_authorized'] and not visual['visual_coordinate_input_authorized'])
    check(not public['automatic_input_authorized'] and not result['automatic_input_authorized'])
    check(not public['coverage']['whole_page_seen'] and not public['coverage']['page_content_is_engineering_fact'])
    check(not result['send_full_image_to_model'])
    return public, visual


def test_no_sdk_actual_http_preview_is_exact_png_readonly_and_replay_does_not_recapture(tmp_path):
    app, runtime, source, capture, factory = scene(tmp_path)
    try:
        with TestClient(app) as client:
            state = bind(client)
            check(factory is None and runtime.cua is None)
            check(state['context']['capture_configured'] and state['context']['visible_rectangle_capture_configured'])
            result = observe(client)
            public, frame = readonly(result)
            check(public['sdk_session_ref'] is None and frame['session_ref'] is None)
            check(public['coverage']['accessibility'] is False)
            check('VISIBLE_RECTANGLE_MAY_INCLUDE_OCCLUSION' in [x['code'] for x in public['questions']])
            raw = runtime.exact_frame_image(public['context_ref'], frame['frame_ref'])
            downloaded = client.get(result['preview']['image_ref']['download_url'], headers=HEADERS)
            check(downloaded.status_code == 200 and downloaded.content == raw['bytes'])
            check(downloaded.headers['cache-control'] == 'no-store')
            with Image.open(BytesIO(downloaded.content)) as image:
                check(image.format == 'PNG' and image.size == (180, 120))
            count = capture.calls.count('actual-image-fixture-copy')
            replay = observe(client)
            check(replay['replayed'] and replay['observation'] == public)
            check(capture.calls.count('actual-image-fixture-copy') == count == 1)
            check(runtime.visible_frames.describe(frame['frame_ref']) == runtime.context.get(public['context_ref'])['observations']['visual'])
            check(not state['native_input_qualified'] and not state['desktop_geometry_mutated'])
    finally:
        check(runtime.close()['status'] == 'CLOSED')
        check(source.closed)


def test_real_sdk_expiry_and_failed_reopen_preserve_visible_readonly_preview(tmp_path):
    app, runtime, _source, capture, factory = scene(tmp_path, sdk=True)
    try:
        with TestClient(app) as client:
            bind(client)
            previous = copy.deepcopy(runtime.sdk_receipt['session_ref'])
            old = runtime.cua.records[previous['id']]
            old['expires_at'] = 0
            attempts = []

            async def failed_reopen(binding):
                attempts.append(copy.deepcopy(binding))
                raise OSError('Original fixture SDK reopen is unavailable')

            factory.open = failed_reopen
            result = observe(client, {**OBSERVE, 'need': {**OBSERVE['need'], 'accessibility': True}})
            public, frame = readonly(result)
            check(old['closed'] and factory.session.closed and len(attempts) == 1)
            check(public['sdk_session_ref'] is None and frame['session_ref'] is None)
            check(runtime.sdk_receipt is None, 'A closed SDK receipt must not remain configured after a failed renewal')
            availability=client.get('/api/desktop-host/collaboration/get',headers=HEADERS).json()['context']
            check(not availability['accessibility_configured'] and availability['capture_configured'])
            check(availability['runtime_reason'].startswith('READONLY_SDK_UNAVAILABLE:'))
            check(not public['coverage']['accessibility'] and public['coverage']['visual'])
            check(any(x['mode'] == 'accessibility' for x in public['observation_errors']))
            check(capture.calls.count('actual-image-fixture-copy') == 1)
            check(factory.session.calls == [], 'SDK fallback or native input was dispatched after failed renewal')
            check(attempts[0]['budget']['max_actions'] == 0 and not attempts[0]['budget']['allow_foreground'])
            check(client.get(result['preview']['image_ref']['download_url'], headers=HEADERS).status_code == 200)
    finally:
        check(runtime.close()['status'] == 'CLOSED')


def test_context_frame_scope_and_http_image_refs_cannot_be_crossed_or_tampered(tmp_path):
    app, runtime, _source, capture, _factory = scene(tmp_path / 'one')
    other_app, other, _other_source, _capture, _factory = scene(tmp_path / 'two')
    try:
        with TestClient(app) as client, TestClient(other_app) as second:
            bind(client); bind(second)
            first = observe(client)
            second_frame = observe(client, {**OBSERVE, 'operation_id': 'visible-two'})
            foreign = observe(second)
            public, frame = readonly(first)
            other_public, other_frame = readonly(foreign)
            with pytest.raises((ValueError, Conflict)):
                runtime.exact_frame_image(public['context_ref'], second_frame['observation']['observations']['visual']['frame_ref'])
            with pytest.raises((ValueError, Conflict)):
                runtime.exact_frame_image(other_public['context_ref'], other_frame['frame_ref'])
            with pytest.raises((ValueError, Conflict)):
                runtime.exact_frame_image(public['context_ref'], {**frame['frame_ref'], 'sha256': 'f' * 64})
            wrong_binding = {key: public[key] for key in ('workspace_session_ref', 'surface_ref', 'input_epoch', 'sdk_session_ref')}
            wrong_binding['workspace_session_ref'] = other_public['workspace_session_ref']
            with pytest.raises(ValueError):
                runtime.visible_frames.lookup(frame['frame_ref'], target_ref=public['window_ref'], binding=wrong_binding)
            url = first['preview']['image_ref']['download_url']
            check(client.get(url).status_code == 409)
            forged = url.replace(first['preview']['image_ref']['sha256'], 'f' * 64)
            check(client.get(forged, headers=HEADERS).status_code == 409)
            wrong_context = {**public['context_ref'], 'sha256': 'f' * 64}
            check(client.post('/api/desktop-host/context/get', json={'context_ref': wrong_context}, headers=HEADERS).status_code == 409)
            image_path = runtime.visible_frames.data / frame['frame_ref']['id'] / 'frame.png'
            original = image_path.read_bytes()
            image_path.write_bytes(original + b'tampered')
            with pytest.raises((ValueError, Conflict)):
                runtime.exact_frame_image(public['context_ref'], frame['frame_ref'])
            check(image_path.read_bytes() == original + b'tampered', 'failed lookup changed stored evidence')
            check(capture.calls.count('actual-image-fixture-copy') == 2, 'rejected refs caused recapture')
    finally:
        check(runtime.close()['status'] == 'CLOSED')
        check(other.close()['status'] == 'CLOSED')
