"""Main-token-only desktop workspace RPC; renderer receives safe refs only."""
from fastapi import Depends, Header
from packages.domain.questions import RequestModel
from packages.domain.store import InvalidInput
from packages.domain.workspace_desktop import WorkspaceDesktop, DesktopBindInput, DesktopUpdateInput, DesktopPrepareInput


class DesktopCloseInput(RequestModel):
    pass


def register_workspace_desktop_routes(app, data_dir, *, service=None):
    value=service or WorkspaceDesktop(data_dir)
    app.state.workspace_desktop=value

    def host(x_eeas_desktop_host: str | None=Header(None)):
        value.check_host(x_eeas_desktop_host)

    def native_call(method, *args):
        try:return method(*args)
        except ValueError as exc:
            raise InvalidInput(type(exc).__name__+':'+str(exc)) from exc

    root='/api/desktop-host/workspace'

    @app.get(root+'/get',dependencies=[Depends(host)])
    def get():return value.get()

    @app.post(root+'/bind',dependencies=[Depends(host)])
    def bind(body:DesktopBindInput):return native_call(value.bind,body.model_dump())

    @app.post(root+'/prepare',dependencies=[Depends(host)])
    def prepare(body:DesktopPrepareInput):return native_call(value.prepare,body.model_dump())

    @app.post(root+'/update',dependencies=[Depends(host)])
    def update(body:DesktopUpdateInput):return native_call(value.update,body.model_dump())

    @app.post(root+'/close',dependencies=[Depends(host)])
    def close(body:DesktopCloseInput):return native_call(value.close)

    return value
