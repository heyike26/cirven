"""Same-origin reads from the actual owned, source-bound OpenCode session."""
from fastapi import Query,Request
from fastapi.responses import Response,StreamingResponse

from packages.adapters.opencode_gateway import OpenCodeNativeGateway
from packages.domain.store import InvalidInput


def register_native_gateway_routes(app,store):
    service=OpenCodeNativeGateway(store,app.state.planning_proposals.transport,app.state.local_workspaces)
    app.state.native_gateway=service

    @app.get('/api/agent-shell/native')
    def describe(project_id:str|None=None,execution_id:str|None=None):
        if project_id is None:
            return {'status':'UNAVAILABLE','server_url':None,'reason':'PROJECT_SELECTION_REQUIRED',
                'original_app_interface':'DISABLED_REQUIRES_LONG_LIVED_SESSION_OWNER',
                'long_lived_conversation':False,'native_mutations_allowed':False}
        return service.describe(project_id,execution_id)

    @app.get('/api/agent-shell/native/{execution_id}/{native_path:path}')
    async def read(execution_id:str,native_path:str,request:Request,project_id:str=Query(min_length=1)):
        unexpected=set(request.query_params)-{'project_id'}
        if unexpected:raise InvalidInput('Native query overrides are not permitted')
        path='/'+native_path
        if path=='/event':
            # Authorize before returning HTTP200; a denied subscription must not
            # become a successful empty stream.
            service.authorize(project_id,execution_id,'GET',path)
            return StreamingResponse(service.stream(project_id,execution_id),media_type='text/event-stream',
                headers={'X-Accel-Buffering':'no'})
        value=await service.read(project_id,execution_id,path)
        return Response(value.body,status_code=value.status_code,media_type=value.media_type)
