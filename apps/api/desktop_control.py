"""Trusted-main User/Agent control; a disabled Cua path remains disabled."""
from fastapi import Depends,Header
from fastapi.responses import JSONResponse
from packages.domain.desktop_control import DesktopControl,DesktopDelegateInput,DesktopTakeoverInput


def register_desktop_control_routes(app,store,data_dir,*,workspace=None,transport=None,
                                    capability_reader=None,source_checker=None,queue_revoker=None):
    if getattr(app.state,'desktop_control',None) is not None:return app.state.desktop_control
    planner=getattr(app.state,'planning_proposals',None)
    value=DesktopControl(workspace or app.state.workspace_desktop,
        transport=transport if transport is not None else getattr(planner,'transport',None),
        capability_reader=capability_reader,source_checker=source_checker,queue_revoker=queue_revoker)
    app.state.desktop_control=value
    def host(x_eeas_desktop_host:str|None=Header(None)):value.ws.check_host(x_eeas_desktop_host)
    root='/api/desktop-host/control'

    @app.get(root+'/get',dependencies=[Depends(host)])
    def get():return value.get()

    @app.post(root+'/delegate',dependencies=[Depends(host)])
    def delegate(body:DesktopDelegateInput):
        result=value.delegate(body.model_dump())
        if result['operation']['status']=='DENIED':
            return JSONResponse({**result,'error':{'code':'DESKTOP_DELEGATION_UNAVAILABLE',
                'message':'当前桌面输入路径或工作区尚未完成真实资格验证，控制权保留给用户。'}},status_code=409)
        return result

    @app.post(root+'/takeover',dependencies=[Depends(host)])
    async def takeover(body:DesktopTakeoverInput):return await value.takeover(body.model_dump())
    return value
