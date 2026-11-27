"""Local authenticated proposal editing, question answering and acceptance."""
from fastapi import Query
from fastapi.responses import JSONResponse, Response

from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_schema import (ProposalInput, ProposalEdit, ProposalAnswer, ProposalConfirm, HarnessExportInput,
    ManualCaptureInput, ManualCaptureRef, WorkspaceRef)
from packages.domain.discovery_schema import RevisionRef
from packages.domain.planning_exports import PlanningExports
from packages.domain.harness_manual import ManualChangesConflict, conflict_response
from packages.domain.questions import Hash, Id


def register_harness_manual_routes(app,service):
    manual=service.manual_changes
    app.state.harness_manual=manual
    root='/api/projects/{project_id}/planning/proposals/{proposal_id}/harness/manual-changes'

    def call(action):
        try:return action()
        except ManualChangesConflict as error:return JSONResponse(conflict_response(error),status_code=409)
        except (KeyError,TypeError,ValueError):
            from packages.domain.store import Conflict
            raise Conflict('Manual-change metadata cannot be verified') from None

    @app.get(root)
    def inspect(project_id:str,proposal_id:Id,revision:int=Query(ge=1),sha256:Hash=Query(),
                workspace_id:Hash=Query(),binding_revision:int=Query(ge=1),binding_sha256:Hash=Query()):
        target=RevisionRef(id=proposal_id,revision=revision,sha256=sha256)
        workspace=WorkspaceRef(id=workspace_id,revision=binding_revision,binding_sha256=binding_sha256)
        return call(lambda:manual.inspect(project_id,proposal_id,workspace.model_dump(),target=target.model_dump()))

    @app.post(root)
    def capture(project_id:str,proposal_id:str,body:ManualCaptureInput):
        value=call(lambda:manual.capture(project_id,proposal_id,body))
        if isinstance(value,Response):return value
        return JSONResponse(value,status_code=200 if value['replayed'] else 201)

    @app.get(root+'/operations/{operation_id}')
    def operation(project_id:str,proposal_id:str,operation_id:Id,request_sha256:Hash=Query()):
        return call(lambda:manual.operation(project_id,proposal_id,operation_id,request_sha256))

    @app.get(root+'/{capture_id}')
    def captured(project_id:str,proposal_id:str,capture_id:Hash,sha256:Hash=Query(),revision:int=Query(ge=1,le=1)):
        return call(lambda:manual.get(project_id,proposal_id,ManualCaptureRef(id=capture_id,revision=revision,sha256=sha256).model_dump()))


def register_planning_routes(app, store, source_root):
    service = PlanningProposals(store, source_root, workspaces=getattr(app.state, 'local_workspaces', None))
    app.state.planning_proposals = service
    exports=PlanningExports(service,service.workspaces)
    app.state.planning_exports=exports
    register_harness_manual_routes(app,service)
    from packages.adapters.opencode_provider import OpenCodeProviderRegistry
    from packages.adapters.opencode_planning import OpenCodePlanningTransport
    profiles=app.state.provider_settings
    service.transport=OpenCodePlanningTransport(store,app.state.settings.data_dir,
        provider_registry=OpenCodeProviderRegistry(profiles.configuration,profiles.secret),
        workspace_authority=service.workspaces,source_validator=service.source_current)

    def native_execution(project_id,operation_id):
        with store.connect() as connection:
            row=connection.execute('''SELECT id FROM agent_executions
                WHERE project_id=? AND operation_id=? AND runtime=?''',
                (project_id,operation_id,service.transport.runtime)).fetchone()
        return service.transport.inspect(project_id,row['id']) if row else None

    @app.post('/api/projects/{project_id}/planning/proposals')
    async def create(project_id: str, body: ProposalInput):
        result = await service.create(project_id, body)
        status = 202 if result.get('status')=='PROCESSING' else 422 if result.get('status')=='FAILED' else 201
        return JSONResponse(result,status_code=status)

    @app.get('/api/projects/{project_id}/planning/proposals')
    def listing(project_id: str):
        return {'proposals':service.list(project_id)}

    @app.get('/api/projects/{project_id}/planning/operations/{operation_id}')
    def operation(project_id: str, operation_id: str,after:int=Query(default=0,ge=0)):
        value=service.operation(project_id,operation_id)
        native=native_execution(project_id,operation_id)
        if native is None:return value
        if value.get('status')=='PROCESSING' and native['execution_status'] in {'UNKNOWN_OUTCOME','CANCELLED','FAILED','TIMED_OUT'}:
            value={**value,'status':native['execution_status']}
        return {**value,'native_execution':{k:native[k] for k in ['id','execution_status','session_id']},
                'events':service.transport.stream_events(project_id,native['id'],after)}

    @app.post('/api/projects/{project_id}/planning/operations/{operation_id}/cancel')
    async def cancel(project_id:str,operation_id:str):
        from packages.domain.store import Conflict
        service.operation(project_id,operation_id)
        native=native_execution(project_id,operation_id)
        if native is None:raise Conflict('请求尚未提交到原 OpenCode，稍后可取消；没有原生进程被停止。')
        return await service.transport.request_cancel(project_id,native['id'])

    @app.get('/api/projects/{project_id}/planning/proposals/{proposal_id}')
    def get(project_id: str, proposal_id: str, revision: int | None=Query(default=None,ge=1)):
        return service.get(project_id,proposal_id,revision)

    @app.put('/api/projects/{project_id}/planning/proposals/{proposal_id}')
    def edit(project_id: str, proposal_id: str, body: ProposalEdit):
        return service.edit(project_id,proposal_id,body)

    @app.post('/api/projects/{project_id}/planning/proposals/{proposal_id}/answers')
    def answer(project_id: str, proposal_id: str, body: ProposalAnswer):
        return service.answer(project_id,proposal_id,body)

    @app.post('/api/projects/{project_id}/planning/proposals/{proposal_id}/confirm')
    def confirm(project_id: str, proposal_id: str, body: ProposalConfirm):
        return service.confirm(project_id,proposal_id,body)

    @app.get('/api/projects/{project_id}/planning/proposals/{proposal_id}/events')
    def events(project_id: str, proposal_id: str, after: int=Query(default=0,ge=0),limit: int=Query(default=100,ge=1,le=1000)):
        return {'events':service.events(project_id,proposal_id,after,limit)}

    @app.get('/api/projects/{project_id}/planning/proposals/{proposal_id}/harness')
    def harness(project_id: str, proposal_id: str, revision: int | None=Query(default=None,ge=1)):
        return service.harness(project_id,proposal_id,revision)

    @app.get('/api/projects/{project_id}/planning/proposals/{proposal_id}/pending')
    def pending(project_id: str, proposal_id: str, revision: int | None=Query(default=None,ge=1)):
        return service.pending(project_id,proposal_id,revision)

    @app.get('/api/projects/{project_id}/planning/proposals/{proposal_id}/pending/markdown')
    def pending_file(project_id: str, proposal_id: str, revision: int | None=Query(default=None,ge=1)):
        raw=service.pending_markdown(project_id,proposal_id,revision).encode('utf-8')
        return Response(raw,media_type='text/markdown',headers={'Content-Disposition':'attachment; filename="pending-confirmation.md"'})

    @app.post('/api/projects/{project_id}/planning/proposals/{proposal_id}/taskbook')
    def taskbook(project_id: str, proposal_id: str, revision: int | None=Query(default=None,ge=1)):
        return service.taskbook(project_id,proposal_id,revision)

    @app.post('/api/projects/{project_id}/planning/proposals/{proposal_id}/harness/export')
    def export_harness(project_id: str,proposal_id: str,body: HarnessExportInput):
        try:return exports.export(project_id,proposal_id,body)
        except ManualChangesConflict as error:return JSONResponse(conflict_response(error),status_code=409)
