"""Real private HTTP, durable replay and original PNG byte guards.

No HWND is queried and no driver/session/capture is created. Injected providers
only fail or emit malformed records; the image is an original local fixture,
not evidence that a native software page was captured.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from apps.api.desktop_context import DesktopContextRPC, register_desktop_context_routes, safe, digest
from packages.domain.document_import import image_metadata
from packages.domain.store import Conflict, NotFound, InvalidInput
from packages.domain.workspace_desktop import WorkspaceDesktop

TOKEN = 'a' * 64
HEADERS = {'X-EEAS-Desktop-Host': TOKEN}
ROOT = '/api/desktop-host/context'
REF = {'id': 'f' * 32, 'revision': 1, 'sha256': 'b' * 64}
BODY = {'operation_id': 'quickask-one', 'intent': '  QUICK_ASK_CONTEXT\r\n中文🙂  ',
    'need': {'metadata': True, 'accessibility': True, 'visual': True, 'ocr': False}}


def application(tmp_path, *, context=None, reader=None, value=None, token=TOKEN):
    ws = WorkspaceDesktop(tmp_path, token=token, owner_pid=123, platform='posix')
    app = FastAPI(); app.state.workspace_desktop = ws
    @app.exception_handler(Conflict)
    async def conflict(_request, exc): return JSONResponse({'error': str(exc)}, status_code=409)
    @app.exception_handler(NotFound)
    async def missing(_request, exc): return JSONResponse({'error': str(exc)}, status_code=404)
    @app.exception_handler(InvalidInput)
    async def invalid(_request, exc): return JSONResponse({'error': str(exc)}, status_code=422)
    service = register_desktop_context_routes(app, None, tmp_path, service=value,
        context=context, exact_frame_image_reader=reader)
    return app, service


@pytest.fixture
def api(tmp_path):
    app, value = application(tmp_path)
    with TestClient(app) as client:
        yield client, value


def observe(client, body=BODY): return client.post(ROOT + '/observe', json=body, headers=HEADERS)


def test_activation_state_requires_host_and_is_fresh_private_bounded_no_capture_or_record(tmp_path):
    class FocusOnly:
        calls=0
        def activation_state(self):
            self.calls+=1
            return {'status':'OBSERVED','foreground_identity_sha256':'e'*64,'last_input_tick':self.calls,
                'hwnd':'NEVER_EXPORT_HANDLE','title':'NEVER_EXPORT_TITLE','pid':999}
        def get_context(self, **_):raise AssertionError('Focus guard cannot capture or start SDK')
    context=FocusOnly();app,value=application(tmp_path,context=context)
    with TestClient(app) as client:
        assert client.get(ROOT+'/activation-state').status_code==409
        assert context.calls==0
        before=sorted(str(p) for p in tmp_path.rglob('*'))
        first=client.get(ROOT+'/activation-state',headers=HEADERS)
        second=client.get(ROOT+'/activation-state',headers=HEADERS)
        assert first.status_code==second.status_code==200
        assert first.headers['cache-control']=='no-store'
        assert first.json()['last_input_tick']==1 and second.json()['last_input_tick']==2
        assert first.json()['authority_generation']==value.generation
        assert first.json()['automatic_input_authorized'] is False
        assert 'NEVER_EXPORT' not in first.text and 'pid' not in first.json()
        assert sorted(str(p) for p in tmp_path.rglob('*'))==before


def test_only_authenticated_explicit_begin_query_requests_trigger_release_wait(tmp_path):
    class FocusOnly:
        calls=[]
        def activation_state(self, *, settle_trigger=False):
            self.calls.append(settle_trigger)
            return {'status':'OBSERVED','foreground_identity_sha256':'e'*64,'last_input_tick':91}
    context=FocusOnly();app,_=application(tmp_path,context=context)
    with TestClient(app) as client:
        assert client.get(ROOT+'/activation-state?settle_trigger=true').status_code==409
        assert context.calls==[]
        for query in ['', '?settle_trigger=true', '?settle_trigger=false']:
            response=client.get(ROOT+'/activation-state'+query,headers=HEADERS)
            assert response.status_code==200 and response.json()['status']=='OBSERVED'
            assert response.headers['cache-control']=='no-store'
        assert context.calls==[False,True,False]


@pytest.mark.parametrize('state',[None,{'status':'OBSERVED','foreground_identity_sha256':'e'*64,'last_input_tick':True},
    {'status':'OBSERVED','foreground_identity_sha256':'e'*64,'last_input_tick':2**32},
    {'status':'OBSERVED','foreground_identity_sha256':'not-an-identity','last_input_tick':1}])
def test_activation_state_bad_provider_never_grants_activation_or_leaks_content(tmp_path,state):
    class Provider:
        def activation_state(self):return state
    app,_=application(tmp_path,context=Provider())
    with TestClient(app) as client:
        result=client.get(ROOT+'/activation-state',headers=HEADERS)
        assert result.json()=={'schema':'eeas.quick-activation-state.v1','status':'UNAVAILABLE','automatic_input_authorized':False}


class FailingContext:
    """Failure-only seam: never returns a native observation or frame."""
    def __init__(self, started=None, release=None):
        self.calls, self.arguments = 0, []
        self.started, self.release = started, release
    def get_context(self, **kwargs):
        self.calls += 1; self.arguments.append(kwargs)
        if self.started: self.started.set()
        if self.release and not self.release.wait(5): raise TimeoutError('test did not release failed read')
        raise OSError('private path /not-for-renderer HWND 123 original failure')
    def get(self, _ref): raise LookupError('no actual context was ever captured')
    def check_current(self, *_args): return False


def original_png():
    stream = BytesIO()
    Image.new('RGB', (7, 5), (240, 243, 247)).save(stream, 'PNG')
    return stream.getvalue()


def frozen_fixture(value):
    """Host-signed original image document, not a native screenshot receipt."""
    raw = original_png(); info = image_metadata(raw)
    image_ref = {'id': 'e' * 32, 'revision': 1, 'sha256': info['sha256']}
    frame_ref = {'id': 'c' * 32, 'revision': 1, 'sha256': 'd' * 64}
    value.ws._write(value._path('images', image_ref['id'] + '.json'),
        {'image_ref': image_ref, 'context_ref': REF, 'frame_ref': frame_ref, **info})
    value._path('images', image_ref['id'] + '.png').write_bytes(raw)
    observation = {'status': 'NEEDS_CLARIFICATION', 'context_ref': REF, 'window_ref': frame_ref,
        'observations': {'visual': {'frame_ref': frame_ref,
            'image_ref': {'sha256': info['sha256'], 'byte_length': len(raw)},
            'source': 'ORIGINAL_LOCAL_IMAGE_FIXTURE_NOT_NATIVE_PAGE_CAPTURE'}},
        'coverage': {'visual': False, 'whole_page_seen': False, 'pixel_source': 'UNKNOWN'},
        'automatic_input_authorized': False, 'send_full_image_to_model': False}
    response = {'operation_id': 'historical-image-document', 'request_sha256': 'a' * 64,
        'observation': observation, 'current': False, 'replayed': False,
        'preview': {'status': 'AVAILABLE', 'image_ref': {**image_ref, **info}},
        'automatic_input_authorized': False, 'send_full_image_to_model': False}
    value.ws._write(value._path('contexts', REF['id'] + '.json'), response)
    return raw, image_ref, frame_ref, response


def test_frozen_document_identity_is_private_hash_bound_and_survives_rpc_restart(tmp_path):
    _, value = application(tmp_path)
    _, _, _, response = frozen_fixture(value)
    basis = {'source': 'WORD_WINDOW_NATIVEOM_PROPERTYGET', 'native_path': 'C:\\selected\\私有稿.docx',
        'saved': False, 'window_lifetime_sha256': 'c'*64, 'content_equals_saved_bytes': False}
    proof = {'status': 'OBSERVED', **basis, 'identity_sha256': digest(basis)}
    from packages.adapters.native_document_identity import public_document_identity
    response['observation']['window_lifetime_sha256'] = basis['window_lifetime_sha256']
    response['observation']['document_identity'] = public_document_identity(proof)
    value.ws._write(value._path('contexts', REF['id']+'.json'), response)
    path = value._path('document-identities', REF['id']+'.json')
    value.ws._write(path, {'context_ref': REF, 'proof': proof, 'proof_sha256': digest(proof)})
    restarted = DesktopContextRPC(value.ws)
    assert restarted.frozen_document_identity(REF) == proof
    public = restarted.frozen_lookup(REF)
    assert 'native_path' not in json.dumps(public) and public['current'] is False
    changed = {**proof, 'native_path': 'C:\\different\\file.docx'}
    value.ws._write(path, {'context_ref': REF, 'proof': changed, 'proof_sha256': digest(changed)})
    with pytest.raises(Conflict):
        restarted.frozen_document_identity(REF)


def test_default_unconfigured_real_http_replay_scope_conflict_and_restart(api, tmp_path):
    client, value = api
    status = client.get(ROOT + '/get', headers=HEADERS).json()
    assert status['status'] == 'UNAVAILABLE' and not status['configured']
    assert not status['automatic_input_authorized'] and not status['native_capture_qualified']
    first = observe(client)
    assert first.status_code == 201
    result = first.json()
    assert result['observation']['reason'] == 'CURRENT_CONTEXT_NOT_CONFIGURED'
    assert not result['current'] and result['preview']['status'] == 'UNAVAILABLE'
    assert not result['observation']['coverage']['visual']
    replay = observe(client)
    assert replay.status_code == 200 and replay.json() == result | {'replayed': True}
    for patch in ({'intent': BODY['intent'].strip()}, {'expected_workspace_session_ref': REF},
            {'need': BODY['need'] | {'ocr': True}}):
        assert observe(client, BODY | patch).status_code == 409
    callback = FailingContext()
    restarted_app, restarted = application(tmp_path, context=callback)
    with TestClient(restarted_app) as other:
        replayed = observe(other).json()
        assert replayed == result | {'replayed': True} and callback.calls == 0
        assert observe(other, BODY | {'operation_id': 'new-click'}).json()['observation']['status'] == 'FAILED'
        assert callback.calls == 1
    saved = value._read(value._path('operations', BODY['operation_id'] + '.json'))
    assert saved['request']['intent'] == BODY['intent'] and saved['request_sha256'] == result['request_sha256']
    assert saved['request']['need'] == BODY['need']
    assert len(list(value._path('operations').glob('*.json'))) == 2


def test_all_routes_require_main_token_before_storage_or_capture(api):
    client, value = api
    endpoints = [('GET', '/get', None), ('GET', '/activation-state', None), ('POST', '/observe', BODY),
        ('POST', '/get', {'context_ref': REF}), ('GET', '/images/' + REF['id'] + '?revision=1&sha256=' + REF['sha256'], None)]
    for method, endpoint, body in endpoints:
        for headers in ({}, {'X-EEAS-Desktop-Host': 'b' * 64}):
            response = client.request(method, ROOT + endpoint, json=body, headers=headers)
            assert response.status_code == 409
    assert not value._path().exists()


@pytest.mark.parametrize('patch', [
    {'operation_id': '../escape'}, {'operation_id': True}, {'intent': ' \r\n '},
    {'intent': True}, {'intent': 'x' * 8193}, {'need': {}},
    {'need': BODY['need'] | {'visual': 1}}, {'need': BODY['need'] | {'metadata': 'true'}},
    {'need': {'metadata': False, 'accessibility': False, 'visual': False, 'ocr': False}},
    {'need': BODY['need'] | {'path': '/private'}},
    {'path': '/private'}, {'url': 'https://example.com/page.png'}, {'hwnd': 123},
    {'authorized': True}, {'image_bytes': 'pretend'}, {'context_ref': REF},
    {'task_ref': REF | {'revision': True}},
    {'expected_workspace_session_ref': REF | {'sha256': 'A' * 64}},
])
def test_malformed_observe_body_never_creates_record(api, patch):
    client, value = api
    assert observe(client, BODY | patch).status_code == 422
    assert not value._path().exists()


def test_preserves_original_read_failure_once_and_does_not_leak_private_exception(tmp_path):
    context = FailingContext(); app, value = application(tmp_path, context=context)
    with TestClient(app) as client:
        first = observe(client).json(); second = observe(client).json()
        assert first['observation']['status'] == 'FAILED' and first['observation']['error_type'] == 'OSError'
        assert second == first | {'replayed': True} and context.calls == 1
        assert '/not-for-renderer' not in json.dumps(first) and 'HWND' not in json.dumps(first)
        assert context.arguments == [{'intent': BODY['intent'], 'task_ref': None,
            'need': BODY['need'], 'expected_workspace_session_ref': None}]
    record = value._read(value._path('operations', BODY['operation_id'] + '.json'))
    assert record['original_failure']['message'].endswith('original failure')
    assert record['response']['observation']['status'] == 'FAILED'


def test_same_operation_concurrency_waits_outside_workspace_lock_and_never_retries_read(tmp_path):
    started, release = threading.Event(), threading.Event()
    context = FailingContext(started, release); app, value = application(tmp_path, context=context)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(observe, client)
        assert started.wait(3)
        assert value.ws.lock.acquire(timeout=.2)
        value.ws.lock.release()
        second = pool.submit(observe, client)
        release.set()
        results = [first.result(5), second.result(5)]
        assert sorted(r.status_code for r in results) == [200, 201]
        assert context.calls == 1
        assert all(r.json()['observation']['status'] == 'FAILED' for r in results)
        assert results[0].json()['observation'] == results[1].json()['observation']


def test_restart_processing_unknown_is_frozen_and_cannot_be_replayed_as_new_capture(tmp_path):
    app, value = application(tmp_path)
    body = __import__('apps.api.desktop_context', fromlist=['DesktopObserveInput']).DesktopObserveInput.model_validate(BODY).model_dump()
    value.ws._write(value._path('operations', body['operation_id'] + '.json'),
        {'request': body, 'request_sha256': digest(body), 'generation': 'previous-host-process', 'state': 'PROCESSING'})
    context = FailingContext(); restarted_app, restarted = application(tmp_path, context=context)
    with TestClient(restarted_app) as client:
        first = observe(client).json(); second = observe(client).json()
        assert first == second and first['replayed'] and first['observation']['status'] == 'UNKNOWN'
        assert first['observation']['reason'] == 'OBSERVATION_INTERRUPTED_OR_RESTARTED'
        assert context.calls == 0
    assert restarted._read(restarted._path('operations', body['operation_id'] + '.json'))['state'] == 'UNKNOWN'


def test_signed_receipt_tamper_or_changed_host_secret_cannot_reuse_operation(api, tmp_path):
    client, value = api
    observe(client)
    path = value._path('operations', BODY['operation_id'] + '.json')
    saved = path.read_bytes(); wrapper = json.loads(saved)
    wrapper['payload']['response']['current'] = True
    path.write_text(json.dumps(wrapper))
    assert observe(client).status_code == 409
    path.write_bytes(saved)
    app, _ = application(tmp_path, token='b' * 64)
    with TestClient(app) as other:
        assert other.post(ROOT + '/observe', json=BODY,
            headers={'X-EEAS-Desktop-Host': 'b' * 64}).status_code == 409


def test_lookup_is_exact_historical_observation_and_never_refreshes_native_capture(api, tmp_path):
    client, value = api
    raw, image_ref, frame_ref, response = frozen_fixture(value)
    result = client.post(ROOT + '/get', json={'context_ref': REF}, headers=HEADERS)
    assert result.status_code == 200 and result.json() == response | {'replayed': True}
    assert result.json()['observation']['observations']['visual']['frame_ref'] == frame_ref
    assert not result.json()['current'] and not result.json()['automatic_input_authorized']
    assert client.post(ROOT + '/get', json={'context_ref': REF | {'sha256': 'd' * 64}}, headers=HEADERS).status_code == 409
    assert client.post(ROOT + '/get', json={'context_ref': REF | {'id': 'unknown'}}, headers=HEADERS).status_code == 404
    restarted_app, _ = application(tmp_path)
    with TestClient(restarted_app) as other:
        assert other.post(ROOT + '/get', json={'context_ref': REF}, headers=HEADERS).json() == result.json()


def test_original_png_private_download_exact_bytes_restart_sha_and_tamper_guards(api, tmp_path):
    client, value = api; raw, ref, _, _ = frozen_fixture(value)
    url = ROOT + '/images/' + ref['id']; params = {'revision': 1, 'sha256': ref['sha256']}
    result = client.get(url, params=params, headers=HEADERS)
    assert result.status_code == 200 and result.content == raw
    assert result.headers['content-type'] == 'image/png' and result.headers['cache-control'] == 'no-store'
    assert hashlib.sha256(result.content).hexdigest() == ref['sha256']
    assert client.get(url, params=params | {'sha256': 'd' * 64}, headers=HEADERS).status_code == 409
    assert client.get(ROOT + '/images/unknown', params=params, headers=HEADERS).status_code == 404
    restarted_app, _ = application(tmp_path)
    with TestClient(restarted_app) as other:
        assert other.get(url, params=params, headers=HEADERS).content == raw
    path = value._path('images', ref['id'] + '.png')
    path.write_bytes(original_png() + b'changed bytes')
    assert client.get(url, params=params, headers=HEADERS).status_code == 409
    path.unlink(); outside = tmp_path / 'outside.png'; outside.write_bytes(raw); path.symlink_to(outside)
    assert client.get(url, params=params, headers=HEADERS).status_code == 422


def test_preview_guards_check_original_fixture_bytes_without_claiming_native_capture(tmp_path):
    raw = original_png(); info = image_metadata(raw); calls = []
    def reader(context_ref, frame_ref):
        calls.append((context_ref, frame_ref))
        return {'bytes': raw, **info}
    _, value = application(tmp_path, reader=reader)
    frame = {'frame_ref': REF, 'image_ref': {'path': '/never-opened.png',
        'sha256': info['sha256'], 'byte_length': len(raw)}, 'width': 7, 'height': 5}
    original = {'context_ref': REF, 'observations': {'visual': frame}}
    preview = value._preview(original)
    assert preview['status'] == 'AVAILABLE' and calls == [(REF, REF)]
    assert preview['image_ref']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert value.image({key: preview['image_ref'][key] for key in ('id', 'revision', 'sha256')}) == raw
    for bad in (frame | {'width': True}, frame | {'height': 9},
            frame | {'image_ref': frame['image_ref'] | {'sha256': 'e' * 64}}):
        result = value._preview(original | {'observations': {'visual': bad}})
        assert result['status'] == 'UNAVAILABLE' and result['reason'] == 'EXACT_FRAME_IMAGE_UNAVAILABLE'
    assert value.status()['native_capture_qualified'] is False


def test_recursive_sanitizing_never_exports_private_handles_or_storage_paths():
    value = {'window_ref': REF, 'path': '/private', 'hwnd': 42, 'pid': 11,
        'observations': {'visual': {'frame_ref': REF, 'image_ref': {'path': '/frame.png', 'sha256': REF['sha256']},
            'sdk_metadata_ref': {'path': '/sdk.json', 'sha256': 'c' * 64}},
            'accessibility': {'elements': [{'label': 'button', 'owner_pid': 1, 'marker_value': 3}]}}}
    result = safe(value)
    assert '/private' not in json.dumps(result) and '/frame.png' not in json.dumps(result)
    assert result['observations']['visual']['frame_ref'] == REF
    assert result['observations']['accessibility']['elements'] == [{'label': 'button'}]


@pytest.mark.parametrize('malformed', [
    {'status': 'READY_OBSERVATION', 'automatic_input_authorized': True},
    {'status': 'READY_OBSERVATION', 'automatic_input_authorized': False, 'send_full_image_to_model': True},
    {'status': 'READY_OBSERVATION', 'automatic_input_authorized': False, 'coverage': {'whole_page_seen': True}},
    {'status': 'READY_OBSERVATION', 'automatic_input_authorized': False, 'coverage': {'page_content_is_engineering_fact': True}},
    {'status': 'READY_OBSERVATION', 'automatic_input_authorized': False, 'context_ref': REF},
])
def test_malformed_context_authority_fails_before_preview_or_input(tmp_path, malformed):
    class MalformedOutput:
        def get_context(self, **_kwargs): return malformed
        def get(self, _ref): return malformed
    images = []
    app, value = application(tmp_path, context=MalformedOutput(), reader=lambda *_: images.append('called'))
    with TestClient(app) as client:
        response = observe(client).json()
        assert response['observation']['status'] == 'FAILED'
        assert not response['automatic_input_authorized'] and images == []
        assert not value._path('images').exists()


def test_registration_binds_authority_once_and_never_instantiates_native_worker(tmp_path):
    app, value = application(tmp_path)
    count = len(app.routes)
    injected = FailingContext()
    assert register_desktop_context_routes(app, None, tmp_path, context=injected) is value
    assert len(app.routes) == count and value.context is None
    assert value.condition._lock is not value.ws.lock
    replacement = DesktopContextRPC(value.ws)
    with pytest.raises(Conflict, match='replace'):
        register_desktop_context_routes(app, None, tmp_path, service=replacement)
    second = FastAPI(); second.state.desktop_context_rpc = value
    assert register_desktop_context_routes(second, None, tmp_path) is value
    assert second.state.desktop_context_routes_registered
