"""Native feedback continuation; existing repair services own scoped writes."""
from fastapi.responses import JSONResponse

from packages.domain.native_rework import MappingInput, NativeApplyInput, NativeContextInput, NativePlanInput, NativeRework
from packages.domain.native_rework_conflicts import (NativeConflictInspect,NativeConflictResolve,
    inspect_native_rework_conflict,resolve_native_rework_conflict)


def register_native_rework_routes(app, store):
    if getattr(app.state, 'native_rework', None) is not None:
        return app.state.native_rework
    service = NativeRework(store, app.state.local_workspaces, app.state.native_feedback,
        app.state.feedback_rework, app.state.planning_proposals, getattr(app.state, 'discovery', None))
    app.state.native_rework = service
    root = '/api/projects/{pid}/native-rework'

    @app.post(root + '/context')
    def context(pid: str, body: NativeContextInput):
        return service.context(pid, body.file_source_ref)

    @app.post(root + '/mappings', status_code=201)
    def mapping(pid: str, body: MappingInput):
        return service.map(pid, body)

    @app.get(root + '/mappings')
    def mappings(pid: str):
        return {'mappings': service.listing(pid)}

    @app.get(root + '/mappings/{ident}')
    def get_mapping(pid: str, ident: str):
        return service.get(pid, ident)

    @app.post(root + '/plans', status_code=201)
    def plan(pid: str, body: NativePlanInput):
        return service.plan(pid, body)

    @app.get(root + '/plans/{ident}')
    def get_plan(pid: str, ident: str):
        return service.get(pid, ident)

    @app.post(root + '/conflicts/inspect')
    def conflict_inspect(pid:str,body:NativeConflictInspect):
        return inspect_native_rework_conflict(service,pid,body)

    @app.post(root + '/conflicts/resolve',status_code=201)
    def conflict_resolve(pid:str,body:NativeConflictResolve):
        return resolve_native_rework_conflict(service,pid,body)

    @app.post(root + '/plans/{ident}/apply')
    def apply(pid: str, ident: str, body: NativeApplyInput):
        result = service.apply(pid, ident, body)
        status = 207 if result['status'] == 'PARTIAL' else 409 if result['status'] in {
            'CONFLICT', 'UNKNOWN_OUTCOME', 'INCOMPLETE_REVIEW_REQUIRED'} else 200
        return JSONResponse(result, status_code=status)

    @app.get(root + '/operations/{operation_id}/input')
    def original_input(pid: str, operation_id: str):
        return service.original_input(pid, operation_id)

    @app.get(root + '/operations/{operation_id}')
    def operation(pid: str, operation_id: str):
        return service.operation(pid, operation_id)

    return service
