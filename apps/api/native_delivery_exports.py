"""Trusted Main native-dialog Save As; paired Webview never chooses an API path."""
from fastapi import Depends, Header
from packages.domain.questions import Id, RequestModel
from packages.agent_sessions.work_delivery_exports import (
    WorkDeliveryExports, WorkDeliveryExportRequest, WorkDeliveryExportInspect,
    WorkDeliveryExportSave, WorkDeliveryExportCancel, WorkDeliveryExportLookup,
    WorkDeliveryExportList)


class NativeDeliveryExportBegin(RequestModel):
    request: WorkDeliveryExportRequest
    origin_client_id: Id


def register_native_delivery_export_routes(app, *, authority):
    service=WorkDeliveryExports(app.state.agent_sessions)
    app.state.native_delivery_exports=service
    def host(x_eeas_desktop_host: str | None = Header(None)):
        authority.check_host(x_eeas_desktop_host, require_native=False)
    root='/api/desktop-host/projects/{project_id}/agent-sessions/{session_id}/delivery-exports'

    @app.post(root+'/begin', dependencies=[Depends(host)])
    def begin(project_id:Id,session_id:Id,body:NativeDeliveryExportBegin):
        return service.begin(project_id,session_id,body.request,client_id=body.origin_client_id)

    @app.post(root+'/operations/inspect', dependencies=[Depends(host)])
    def inspect(project_id:Id,session_id:Id,body:WorkDeliveryExportInspect):
        return service.operation(project_id,session_id,body)

    @app.post(root+'/save', dependencies=[Depends(host)])
    def save(project_id:Id,session_id:Id,body:WorkDeliveryExportSave):
        return service.save(project_id,session_id,body)

    @app.post(root+'/cancel', dependencies=[Depends(host)])
    def cancel(project_id:Id,session_id:Id,body:WorkDeliveryExportCancel):
        return service.cancel(project_id,session_id,body)

    @app.post(root+'/reconcile', dependencies=[Depends(host)])
    def reconcile(project_id:Id,session_id:Id,body:WorkDeliveryExportLookup):
        return service.reconcile(project_id,session_id,body)

    @app.post(root+'/list', dependencies=[Depends(host)])
    def listing(project_id:Id,session_id:Id,body:WorkDeliveryExportList):
        return service.listing(project_id,session_id,body)
    return service
