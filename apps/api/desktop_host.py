"""Desktop main-only RPC; captured observations retain normal project boundaries."""
from fastapi import Header,Depends
from fastapi.responses import Response,JSONResponse
from pydantic import Field,BaseModel,ConfigDict
from packages.domain.local_workspaces import FileSource
from packages.domain.questions import Id
from packages.domain.desktop_host import DesktopHost


class StrictModel(BaseModel):model_config=ConfigDict(extra='forbid')


class ActionInput(StrictModel):
    project_id: Id
    intent: str
    file_source_ref: FileSource


class OpenInput(StrictModel):
    project_id: Id
    file_source_ref: FileSource
    user_action_ref: Id


class ViewInput(StrictModel):
    project_id: Id
    view_ref: Id


class WindowInput(ViewInput):
    user_action_ref: Id


class AttachInput(WindowInput):surface_ref: Id


class SurfaceInput(StrictModel):
    project_id: Id
    hwnd_decimal: str = Field(pattern=r'^[1-9][0-9]{0,19}$')
    owner_pid: int = Field(ge=1,le=2147483647,strict=True)
    css_bounds: dict[str,float]
    zoom_factor: float = Field(ge=.1,le=10,allow_inf_nan=False)


def register_desktop_host_routes(app,store,data_dir):
    service=DesktopHost(store,app.state.local_workspaces,data_dir);app.state.desktop_host=service
    def host(x_eeas_desktop_host:str|None=Header(None)):
        service.check_host(x_eeas_desktop_host)
    root='/api/desktop-host'

    @app.get(root+'/capabilities',dependencies=[Depends(host)])
    def capabilities():return service.capabilities()

    @app.post(root+'/actions',dependencies=[Depends(host)])
    def action(body:ActionInput):return service.grant(body.project_id,body.intent,body.file_source_ref.model_dump())

    @app.post(root+'/surfaces',dependencies=[Depends(host)])
    def surface(body:SurfaceInput):return service.register_surface(body.project_id,body.hwnd_decimal,body.owner_pid,body.css_bounds,body.zoom_factor)

    @app.post(root+'/documents/open',dependencies=[Depends(host)])
    def open_document(body:OpenInput):return service.documents.open(body.project_id,body.file_source_ref.model_dump(),body.user_action_ref)

    @app.post(root+'/documents/view',dependencies=[Depends(host)])
    def view(body:ViewInput):return service.documents.probe(body.project_id,body.view_ref)

    @app.post(root+'/documents/attach',dependencies=[Depends(host)])
    def attach(body:AttachInput):return service.documents.attach(body.project_id,body.view_ref,body.surface_ref,body.user_action_ref)

    @app.post(root+'/documents/resize',dependencies=[Depends(host)])
    def resize(body:AttachInput):return service.documents.resize(body.project_id,body.view_ref,body.surface_ref,body.user_action_ref)

    @app.post(root+'/documents/focus',dependencies=[Depends(host)])
    def focus(body:WindowInput):return service.documents.focus(body.project_id,body.view_ref,body.user_action_ref)

    @app.post(root+'/documents/detach',dependencies=[Depends(host)])
    def detach(body:WindowInput):return service.documents.detach(body.project_id,body.view_ref,body.user_action_ref)

    @app.post(root+'/documents/capture',dependencies=[Depends(host)])
    def capture(body:WindowInput):return service.capture(body.project_id,body.view_ref,body.user_action_ref)

    @app.post(root+'/close',dependencies=[Depends(host)])
    async def close():
        result=service.close()
        planner=app.state.planning_proposals.transport
        if callable(getattr(planner,'close',None)):await planner.close()
        return result

    @app.get('/api/projects/{pid}/native-captures/{view_ref}/{revision}/image')
    def image(pid:str,view_ref:str,revision:int):
        if revision<1:return JSONResponse({'error':{'code':'INVALID_INPUT','message':'Capture revision must be positive'}},status_code=422)
        raw,value=service.image(pid,view_ref,revision)
        return Response(raw,media_type='image/png',headers={'ETag':'"'+value['image']['sha256']+'"','X-Content-Type-Options':'nosniff'})

    from packages.adapters.windows_native_window_host import NativeWindowError,NativeWindowUnavailable
    @app.exception_handler(NativeWindowUnavailable)
    async def unavailable(_request,exc):
        return JSONResponse({'error':{'code':'NATIVE_WINDOW_UNAVAILABLE','message':str(exc)}},status_code=501)

    @app.exception_handler(NativeWindowError)
    async def native_error(_request,exc):
        return JSONResponse({'error':{'code':'NATIVE_WINDOW_FAILED','message':str(exc)}},status_code=422)
