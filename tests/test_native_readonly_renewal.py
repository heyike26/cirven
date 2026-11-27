"""Expiry/readability fault probes; no Windows, image or input qualification."""
from fastapi.testclient import TestClient

from test_native_collaboration import TOKEN, BIND, BODY
from test_native_collaboration_runtime import assembly


def test_partial_content_current_object_remains_quoteable_without_action_or_pixel_permission(tmp_path):
    app, runtime, source, _factory = assembly(tmp_path)
    headers = {'X-EEAS-Desktop-Host': TOKEN}
    with TestClient(app) as client:
        client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=headers).raise_for_status()
        result = client.post('/api/desktop-host/context/observe',
            json={**BODY, 'need': {**BODY['need'], 'visual': True}}, headers=headers).json()
        value = result['observation']
        assert value['status'] == 'NEEDS_CLARIFICATION' and result['current'] is True
        assert value['coverage']['pixel_source'] == 'UNKNOWN'
        args = (value['context_ref'], value['window_ref'], value['surface_ref'], value['input_epoch'])
        assert runtime.context.check_observation_current(*args) is True
        assert runtime.context.check_current(*args) is False
        assert value['observations']['visual']['visual_coordinate_input_authorized'] is False
        # Actual metadata drift still invalidates the identity-only read guard.
        source.window['bounds']['width'] += 1
        assert runtime.context.check_observation_current(*args) is False
    runtime.close()


def test_expired_readonly_session_renews_on_observation_without_rebinding_host_or_issuing_input(tmp_path):
    app, runtime, _source, factory = assembly(tmp_path)
    headers = {'X-EEAS-Desktop-Host': TOKEN}
    with TestClient(app) as client:
        bound = client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=headers).json()
        previous = runtime.sdk_receipt['session_ref']
        runtime.cua.records[previous['id']]['expires_at'] = runtime.cua.clock() - 1
        observed = client.post('/api/desktop-host/context/observe', json=BODY, headers=headers)
        assert observed.status_code == 201, observed.text
        assert observed.json()['observation']['coverage']['accessibility'] is True
        assert len(factory.bindings) == 2
        assert runtime.sdk_receipt['session_ref'] != previous
        assert runtime.cua.records[previous['id']]['closed'] is True
        assert runtime.ws.get()['session_ref'] == bound['session_ref']
        assert runtime.ws.get()['input']['epoch'] == bound['input']['epoch']
        assert all(v['runtime_role'] == 'READONLY_OBSERVATION' and v['budget']['max_actions'] == 0
            and v['budget']['allow_foreground'] is False for v in factory.bindings)
    runtime.close()


def test_faulted_session_is_not_renewed_or_replayed(tmp_path):
    app, runtime, _source, factory = assembly(tmp_path)
    headers = {'X-EEAS-Desktop-Host': TOKEN}
    with TestClient(app) as client:
        client.post('/api/desktop-host/collaboration/bind', json=BIND, headers=headers).raise_for_status()
        previous = runtime.sdk_receipt['session_ref']
        runtime.cua.records[previous['id']]['fault'] = True
        runtime.cua.records[previous['id']]['expires_at'] = runtime.cua.clock() - 1
        result = client.post('/api/desktop-host/context/observe', json=BODY, headers=headers)
        assert result.status_code == 201
        assert result.json()['observation']['status'] == 'FAILED'
        assert len(factory.bindings) == 1 and runtime.sdk_receipt['session_ref'] == previous
    runtime.close()
