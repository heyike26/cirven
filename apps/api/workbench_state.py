"""Project-scoped presentation persistence; existing business APIs stay authoritative."""
from fastapi import Query

from packages.domain.workbench_state import WorkbenchLoadInput, WorkbenchSaveInput, WorkbenchState


def register_workbench_state_routes(app, store, data_dir, *, workspaces=None):
    service = WorkbenchState(store, data_dir, workspaces=workspaces)
    app.state.workbench_state = service
    base = '/api/projects/{pid}/workbench-state'

    @app.post(base + '/load')
    def load(pid: str, body: WorkbenchLoadInput):
        return service.load(pid, body)

    @app.post(base + '/save')
    def save(pid: str, body: WorkbenchSaveInput):
        return service.save(pid, body)

    @app.get(base + '/drafts')
    def drafts(pid: str, offset: int = Query(0, ge=0, le=1_000_000), limit: int = Query(50, ge=1, le=100)):
        return service.drafts(pid, offset=offset, limit=limit)

    @app.get(base + '/operations/{operation_id}')
    def operation(pid: str, operation_id: str):
        return service.operation(pid, operation_id)
