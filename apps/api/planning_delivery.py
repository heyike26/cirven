"""Persistent entry/stage/confirmed export composition over the original B chain."""
from fastapi import Query
from fastapi.responses import JSONResponse

from packages.domain.planning_delivery import PlanningDelivery, DeliveryIntake, DeliveryStage, DeliveryExport, DeliveryExportResume
from packages.domain.harness_manual import ManualChangesConflict, conflict_response
from packages.domain.store import Conflict


def register_planning_delivery_routes(app, store):
    service = PlanningDelivery(store, app.state.local_workspaces, app.state.local_materials,
        app.state.discovery, app.state.planning_proposals, app.state.planning_reports,
        app.state.planning_diagrams, app.state.planning_exports)
    app.state.planning_delivery = service
    root = '/api/projects/{pid}/planning-delivery'

    @app.post(root + '/intakes')
    async def intake(pid: str, body: DeliveryIntake):
        value = await service.intake(pid, body)
        return JSONResponse(value, status_code=202 if value['status'] == 'PROCESSING' else
            422 if value['status'] in {'FAILED', 'UNKNOWN_OUTCOME', 'FAILED_OR_PARTIAL', 'INTERRUPTED_REVIEW_REQUIRED'} else 201)

    @app.post(root + '/sessions/{sid}/stage')
    def stage(pid: str, sid: str, body: DeliveryStage):
        return service.stage(pid, sid, body)

    @app.post(root + '/proposals/{ident}/export')
    def export(pid: str, ident: str, body: DeliveryExport):
        try:return service.export(pid, ident, body)
        except ManualChangesConflict as error:return JSONResponse(conflict_response(error),status_code=409)

    @app.post(root + '/operations/{operation_id}/resume-export')
    def resume_export(pid: str, operation_id: str, body: DeliveryExportResume):
        try:return service.resume_export(pid,operation_id,body)
        except ManualChangesConflict as error:return JSONResponse(conflict_response(error),status_code=409)
        except Conflict as error:
            if getattr(error,'code',None)=='BUSY':return JSONResponse({'error':str(error),'code':'BUSY'},status_code=409)
            raise

    @app.get(root + '/operations/{operation_id}')
    def operation(pid: str, operation_id: str):
        return service.operation(pid, operation_id)

    @app.get(root + '/sessions/{sid}/operations')
    def session_operations(pid: str, sid: str, revision: int = Query(..., ge=1),
                           sha256: str = Query(..., pattern='^[a-f0-9]{64}$')):
        return service.session_operations(pid, sid, revision, sha256)
