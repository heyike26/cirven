"""Persistent workflow UI, backed by the original OpenCode planner."""
from fastapi import Query
from fastapi.responses import JSONResponse, Response
from packages.domain.discovery import DiscoveryWorkbench
from packages.domain.discovery_schema import (
    DiscoveryCreate,DiscoveryAdvance,DiscoveryEdit,DiscoveryAnswer,DiscoveryConfirm,DiscoveryPlan)


def register_discovery_routes(app,store):
    service=DiscoveryWorkbench(store,app.state.planning_proposals)
    app.state.discovery=service

    @app.get('/api/discovery/methods')
    def methods():
        from packages.planning.discovery_methods import method_pack
        return method_pack(app.state.planning_proposals.source_root)

    def response(value,created=False):
        status=value.get('status')
        code=202 if status=='PROCESSING' else 422 if status in {'FAILED','UNKNOWN_OUTCOME','CANCELLED'} else 201 if created else 200
        return JSONResponse(value,status_code=code)

    @app.post('/api/projects/{pid}/discovery/sessions')
    def create(pid:str,body:DiscoveryCreate):return response(service.create(pid,body),True)

    @app.get('/api/projects/{pid}/discovery/sessions')
    def listing(pid:str):return {'sessions':service.list(pid)}

    @app.get('/api/projects/{pid}/discovery/sessions/{sid}')
    def get(pid:str,sid:str,revision:int|None=Query(default=None,ge=1)):return service.get(pid,sid,revision)

    @app.get('/api/projects/{pid}/discovery/sessions/{sid}/recoverable-operations')
    def recoverable(pid:str,sid:str,before:int|None=Query(default=None,ge=1),limit:int=Query(default=20,ge=1,le=20)):
        return service.recoverable_operations(pid,sid,before,limit)

    @app.put('/api/projects/{pid}/discovery/sessions/{sid}')
    def edit(pid:str,sid:str,body:DiscoveryEdit):return service.edit(pid,sid,body)

    @app.post('/api/projects/{pid}/discovery/sessions/{sid}/advance')
    async def advance(pid:str,sid:str,body:DiscoveryAdvance):return response(await service.advance(pid,sid,body))

    @app.post('/api/projects/{pid}/discovery/sessions/{sid}/answers')
    def answer(pid:str,sid:str,body:DiscoveryAnswer):return service.answer(pid,sid,body)

    @app.post('/api/projects/{pid}/discovery/sessions/{sid}/confirm')
    def confirm(pid:str,sid:str,body:DiscoveryConfirm):return service.confirm(pid,sid,body)

    @app.post('/api/projects/{pid}/discovery/sessions/{sid}/plan')
    async def plan(pid:str,sid:str,body:DiscoveryPlan):return response(await service.plan(pid,sid,body))

    @app.get('/api/projects/{pid}/discovery/sessions/{sid}/pending')
    def pending(pid:str,sid:str,revision:int|None=Query(default=None,ge=1)):return service.pending(pid,sid,revision)

    @app.get('/api/projects/{pid}/discovery/sessions/{sid}/pending/markdown')
    def pending_file(pid:str,sid:str,revision:int|None=Query(default=None,ge=1)):
        return Response(service.pending_markdown(pid,sid,revision).encode(),media_type='text/markdown',
                        headers={'Content-Disposition':'attachment; filename="pending-confirmation.md"'})

    @app.get('/api/projects/{pid}/discovery/operations/{op}')
    def operation(pid:str,op:str,after:int=Query(default=0,ge=0)):return service.operation(pid,op,after)

    @app.post('/api/projects/{pid}/discovery/operations/{op}/cancel')
    async def cancel(pid:str,op:str):return await service.cancel(pid,op)
