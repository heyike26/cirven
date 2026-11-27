"""Real app assembly before native bind; no Windows execution qualification."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.native_append import NotepadAppendTasks
from apps.api.native_append_composition import BoundNotepadInspector
from packages.domain.store import Conflict


def test_actual_app_registers_only_one_original_task_owner_without_constructing_sdk_before_bind(tmp_path,monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN','f'*64)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID','123')
    monkeypatch.delenv('EEAS_COLLABORATION_RUNTIME_DESCRIPTOR',raising=False)
    monkeypatch.delenv('EEAS_COLLABORATION_RUNTIME_DESCRIPTOR_SHA256',raising=False)
    app=create_app(Settings(tmp_path,port=8765))
    service=app.state.native_tasks
    runtime=app.state.native_collaboration_runtime
    assert isinstance(service,NotepadAppendTasks)
    assert runtime.task_service is service and app.state.native_append_host.service is service
    assert runtime.source is None and runtime.cua is None and runtime.sdk_receipt is None
    assert service.inspector.inspector is None
    assert service.reviewed_qualification.read({}) is None
    assert service.reviewed_qualification.reason=='QUALIFICATION_NOT_CONFIGURED'
    with pytest.raises(Conflict,match='CURRENT_BOUND_WINDOWS_SOURCE_REQUIRED'):
        service.inspector.read({'window_id':903})
    settings=app.state.settings if hasattr(app.state,'settings') else Settings(tmp_path,port=8765)
    with TestClient(app,base_url=settings.origin) as client:
        bootstrap=client.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'})
        assert bootstrap.status_code==200
        # Real local cookie/CSRF do not grant a missing native Main lifetime.
        response=client.get('/api/desktop-host/native-append-host/get?operation_id=old',
            headers={'X-EEAS-Desktop-Host':'f'*64})
        assert response.status_code==409
    assert runtime.source is None and runtime.cua is None


def test_non_desktop_app_keeps_native_append_route_unmounted(tmp_path,monkeypatch):
    monkeypatch.delenv('EEAS_DESKTOP_HOST_TOKEN',raising=False)
    monkeypatch.delenv('EEAS_DESKTOP_HOST_PID',raising=False)
    app=create_app(Settings(tmp_path,port=8765))
    assert not hasattr(app.state,'native_tasks')
    assert not hasattr(app.state,'native_append_host')
    assert not any(route.path.startswith('/api/desktop-host/native-append') for route in app.routes)
