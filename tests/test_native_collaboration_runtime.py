"""Real private RPC composition and unavailable/fault paths; no native GUI."""
import copy
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.native_runtime import (
    NativeCollaborationRuntime, register_native_collaboration_routes, selected_readonly_runtime,
)
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict
from test_native_collaboration import (
    TOKEN, BIND, BODY, HostLifecycleCounter, CurrentWindowCounter, SessionFactoryCounter,
)


class TrackingCounter(CurrentWindowCounter):
    def start(self, callback):
        self.callback = callback
        return {'status': 'TRACKING_FAULT_PROBE_ONLY', 'native_input_enabled': False}


def assembly(tmp_path, *, sdk=True):
    authority = NativeCollaborationAuthority(tmp_path, token=TOKEN, owner_pid=123,
        native=HostLifecycleCounter(), platform='POLICY_FAULT_PROBE')
    source = TrackingCounter()
    factory = SessionFactoryCounter(source) if sdk else None
    runtime = NativeCollaborationRuntime(tmp_path, authority=authority, platform='POLICY_FAULT_PROBE',
        source_factory=lambda **_options: source, session_factory=factory)
    app = FastAPI()
    @app.exception_handler(Conflict)
    async def conflict(_request, error):
        return JSONResponse({'error': str(error)}, status_code=409)
    register_native_collaboration_routes(app, None, tmp_path, service=runtime)
    return app, runtime, source, factory


def test_composition_registers_context_before_bind_and_opens_only_readonly_session(tmp_path):
    app, runtime, source, factory = assembly(tmp_path)
    headers = {'X-EEAS-Desktop-Host': TOKEN}
    with TestClient(app) as client:
        assert client.get('/api/desktop-host/collaboration/get', headers=headers).json()['status'] == 'UNBOUND'
        assert client.post('/api/desktop-host/collaboration/bind', json=BIND).status_code == 409
        bound = client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=headers)
        assert bound.status_code == 200
        assert bound.json()['status'] == 'ACTIVE'
        assert bound.json()['context']['accessibility_configured']
        assert not bound.json()['native_input_qualified']
        assert factory.bindings[0]['execution_ref'] is None
        assert factory.bindings[0]['runtime_role'] == 'READONLY_OBSERVATION'
        assert factory.bindings[0]['budget']['max_actions'] == 0
        assert not factory.bindings[0]['budget']['allow_foreground']
        again = client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=headers)
        assert again.status_code == 200 and len(factory.bindings) == 1
        observed = client.post('/api/desktop-host/context/observe', json=BODY, headers=headers).json()
        assert observed['observation']['coverage']['accessibility']
        assert observed['observation']['runtime_role'] == 'READONLY_OBSERVATION'
        mode = client.post('/api/desktop-host/collaboration/presentation',
            json={'presentation': 'PROJECT_WORKBENCH'}, headers=headers).json()
        assert mode['input']['epoch'] == bound.json()['input']['epoch']
        assert not mode['input']['enabled'] and not mode['desktop_geometry_mutated']
        invalid = client.post('/api/desktop-host/collaboration/presentation',
            json={'presentation': 'DESKTOP'}, headers=headers)
        assert invalid.status_code == 422
        closed = client.post('/api/desktop-host/collaboration/close', headers=headers).json()
        assert closed['status'] == 'CLOSED'
        assert source.closed and factory.session.closed
        assert not closed['user_windows_modified']


def test_original_frame_preview_is_bound_to_context_and_unknown_pixels_stay_unknown(tmp_path):
    app, runtime, _source, factory = assembly(tmp_path)
    headers = {'X-EEAS-Desktop-Host': TOKEN}
    with TestClient(app) as client:
        client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=headers).raise_for_status()
        result = client.post('/api/desktop-host/context/observe',
            json={**BODY, 'need': {**BODY['need'], 'visual': True}}, headers=headers).json()
        assert result['preview']['status'] == 'AVAILABLE'
        assert result['preview']['pixel_source'] == 'UNKNOWN'
        assert result['observation']['status'] == 'NEEDS_CLARIFICATION'
        assert not result['observation']['observations']['visual']['visual_coordinate_input_authorized']
        image = client.get(result['preview']['image_ref']['download_url'], headers=headers)
        assert image.status_code == 200 and image.content == factory.session.png
        frame = result['observation']['observations']['visual']
        with pytest.raises(Conflict):
            runtime.exact_frame_image(result['observation']['context_ref'],
                {**frame['frame_ref'], 'sha256': 'f' * 64})
    runtime.close()


def test_missing_driver_does_not_block_metadata_or_pretend_page_content(tmp_path):
    app, runtime, _source, _factory = assembly(tmp_path, sdk=False)
    headers = {'X-EEAS-Desktop-Host': TOKEN}
    with TestClient(app) as client:
        bound = client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=headers).json()
        assert bound['context']['metadata_available']
        assert not bound['context']['capture_configured']
        body = {**BODY, 'need': {'metadata': True, 'accessibility': False, 'visual': False, 'ocr': False}}
        metadata = client.post('/api/desktop-host/context/observe', json=body, headers=headers).json()
        assert metadata['observation']['status'] == 'READY_OBSERVATION'
        assert not metadata['observation']['coverage']['accessibility']
        page = client.post('/api/desktop-host/context/observe',
            json={**BODY, 'operation_id': 'unconfigured-page'}, headers=headers).json()
        assert page['observation']['status'] == 'UNAVAILABLE'
        assert page['observation']['reason'] == 'ORIGINAL_CUA_SDK_SESSION_UNAVAILABLE'
    runtime.close()


def test_nonwindows_availability_is_readable_but_cannot_bind_native_host(tmp_path):
    authority = NativeCollaborationAuthority(tmp_path, token=TOKEN, owner_pid=123, platform='posix')
    runtime = NativeCollaborationRuntime(tmp_path, authority=authority, platform='posix')
    app = FastAPI()
    @app.exception_handler(Conflict)
    async def conflict(_request, error):
        return JSONResponse({'error': str(error)}, status_code=409)
    register_native_collaboration_routes(app, None, tmp_path, service=runtime)
    headers = {'X-EEAS-Desktop-Host': TOKEN}
    with TestClient(app) as client:
        state = client.get('/api/desktop-host/collaboration/get', headers=headers)
        assert state.status_code == 200
        assert client.get('/api/desktop-host/context/status', headers=headers).status_code == 200
        assert not state.json()['context']['metadata_available']
        assert client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=headers).status_code == 409


def descriptor_scene(tmp_path):
    root = tmp_path / 'selected'; root.mkdir()
    component = root / 'component.json'
    component.write_text(json.dumps({'wheels': [{'name': 'cua-driver', 'version': '0.35.2', 'engine_files': {'engine': 'a' * 64}}]}))
    policy = root / 'readonly.yaml'
    policy.write_text('version: 3\nexpires_after: 5m\nidle_timeout: 5m\nallow:\n  tools: [get_window_state]\n')
    def ref(path):
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    value = {'schema': 'eeas.native-collaboration-runtime.v1', 'runtime_root': str(root),
             'component_manifest_ref': ref(component), 'readonly_capability_manifest_ref': ref(policy)}
    descriptor = root / 'runtime.json'; descriptor.write_text(json.dumps(value))
    return descriptor, ref(descriptor)['sha256'], policy


def test_selected_descriptor_and_policies_require_real_matching_bytes(tmp_path):
    path, sha, policy = descriptor_scene(tmp_path)
    value = selected_readonly_runtime(descriptor=str(path), descriptor_sha256=sha)
    assert value['readonly_capability_manifest_ref']['path'] == str(policy)
    with pytest.raises(ValueError):
        selected_readonly_runtime(descriptor=str(path), descriptor_sha256='f' * 64)
    policy.write_text('version: 3\nallow:\n  tools: [click]\n')
    with pytest.raises(ValueError):
        selected_readonly_runtime(descriptor=str(path), descriptor_sha256=sha)
    runtime = NativeCollaborationRuntime(tmp_path, platform='posix', runtime_descriptor=str(path), runtime_descriptor_sha256=sha)
    assert runtime.get()['context']['runtime_reason']
    assert not runtime.get()['context']['capture_configured']


def test_close_runs_sdk_cleanup_even_when_source_reports_failure(tmp_path):
    _app, runtime, source, factory = assembly(tmp_path)
    runtime.bind(BIND)
    def failed_close():
        raise OSError('source close outcome unknown')
    source.close = failed_close
    result = runtime.close()
    assert result['status'] == 'CLOSE_UNKNOWN'
    assert factory.session.closed
    assert runtime.authority.get()['status'] == 'CLOSED'
    assert runtime.close() == result


def test_record_failure_on_close_still_revokes_scope_and_closes_own_sdk(tmp_path):
    _app, runtime, _source, factory = assembly(tmp_path)
    runtime.bind(BIND)
    def failed_save(_kind):
        raise OSError('private disk write failed')
    runtime.authority._save = failed_save
    result = runtime.close()
    assert result['status'] == 'CLOSE_UNKNOWN'
    assert runtime.authority.get()['status'] == 'CLOSED'
    assert factory.session.closed
