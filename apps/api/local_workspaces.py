"""Actual selected-folder APIs; the desktop picker supplies a directory, not bytes."""
from fastapi import Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from packages.domain.local_workspaces import FileEditInput, FileSource, LocalWorkspaces, WorkspaceInput
from packages.domain.local_materials import LocalMaterials, LocalMaterialsInput
from packages.domain.local_explorer import LocalExplorer


class SourceCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[FileSource] = Field(min_length=1, max_length=64)


def register_local_workspace_routes(app, store, data_dir):
    service = LocalWorkspaces(store, data_dir); materials = LocalMaterials(service)
    app.state.local_workspaces = service
    app.state.local_materials = materials
    explorer = LocalExplorer(service)
    app.state.local_explorer = explorer
    root = "/api/projects/{project_id}/local-workspaces"

    @app.get('/api/projects/{project_id}/local-workspace-operations/{operation_id}')
    def binding_operation(project_id: str, operation_id: str):
        import re
        from packages.domain.local_workspaces import digest
        from packages.domain.store import InvalidInput
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise InvalidInput('Workspace binding operation is invalid')
        return {'operation_id': operation_id,
                'workspace': service.get(project_id, digest({'project_id': project_id, 'key': operation_id}))}

    @app.post(root, status_code=201)
    def bind(project_id: str, body: WorkspaceInput):
        return service.bind(project_id, body)

    @app.get(root)
    def workspaces(project_id: str):
        return {"workspaces": service.list(project_id)}

    @app.get(root + "/{workspace_id}")
    def binding(project_id: str, workspace_id: str):
        return service.get(project_id, workspace_id)

    @app.get(root + "/{workspace_id}/tree")
    def tree(project_id: str, workspace_id: str, path: str = "", offset: int = Query(default=0, ge=0),
             limit: int = Query(default=100, ge=1, le=200)):
        return service.tree(project_id, workspace_id, path, offset, limit)

    @app.get(root + "/{workspace_id}/file")
    def read(project_id: str, workspace_id: str, path: str,
             revision: int | None = Query(default=None, ge=1)):
        # This GET is the user's explicit scoped, nonwriting observation.
        # Domain reads used for edit/preimage/CAS retain their strict default.
        return service.read_saved_snapshot(project_id, workspace_id, path, revision)

    @app.get(root + "/{workspace_id}/explorer")
    def directory_observation(project_id: str, workspace_id: str, path: str = "",
            offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200),
            observation_id: str | None = None, expected_observation_id: str | None = None):
        return explorer.observe(project_id, workspace_id, path, offset, limit,
            observation_id=observation_id, expected_observation_id=expected_observation_id)

    @app.get(root + "/{workspace_id}/search")
    def search(project_id: str, workspace_id: str, q: str, path: str = "",
               limit: int = Query(default=50, ge=1, le=100)):
        return service.search(project_id, workspace_id, q, path=path, limit=limit)

    @app.post(root + "/{workspace_id}/edits")
    async def edit(project_id: str, workspace_id: str, body: FileEditInput):
        sessions=getattr(app.state,'agent_sessions',None)
        if sessions is not None:
            await sessions.editor_captures.broker.admit_workspace(project_id,workspace_id,body)
        return await run_in_threadpool(service.edit,project_id,workspace_id,body)

    @app.get(root + "/{workspace_id}/history")
    def history(project_id: str, workspace_id: str, path: str):
        return {"versions": service.history(project_id, workspace_id, path)}

    @app.get(root + "/{workspace_id}/operations/{operation_id}")
    def operation(project_id: str, workspace_id: str, operation_id: str):
        return service.operation(project_id, workspace_id, operation_id)

    @app.post(root + "/{workspace_id}/source-check")
    def check(project_id: str, workspace_id: str, body: SourceCheckInput):
        if any(ref.workspace_id != workspace_id for ref in body.sources):
            from packages.domain.store import InvalidInput
            raise InvalidInput("FileSources must belong to the selected workspace")
        return {"sources": service.check_saved_sources(project_id, body.sources)}

    @app.post(root + "/{workspace_id}/materials")
    def selected_materials(project_id: str, workspace_id: str, body: LocalMaterialsInput):
        value = materials.materialize(project_id, workspace_id, body)
        return JSONResponse(value, status_code=207 if value["status"] == "PARTIAL" else 200 if value["replayed"] else 201)
