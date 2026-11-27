"""AUTHORED_NOT_RUN: snapshot route uses saved state; original PID/SDK is UNIT."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.agent_sessions import register_agent_session_routes
from test_session_full_diffs import coding


def test_saved_snapshot_never_calls_original_inspection_start_or_http(coding,monkeypatch):
    s=coding;owner=s['value']
    def forbidden(*_args,**_kwargs):raise AssertionError('Snapshot must not invoke an original SDK or start an owner')
    monkeypatch.setattr(owner,'inspect',forbidden);monkeypatch.setattr(owner,'_start',forbidden);monkeypatch.setattr(owner,'_http',forbidden)
    app=FastAPI();register_agent_session_routes(app,owner.store,owner.data_dir,service=owner)
    with TestClient(app) as client:
        response=client.get(f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}/snapshot")
    assert response.status_code==200
    assert response.json()['session_ref']==s['ref'] and response.json()['connected_owner'] is False
    assert response.json()['automatic_post_replay'] is False
