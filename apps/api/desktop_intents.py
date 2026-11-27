"""Private trusted-main RPC for sealed desktop-message authority records.

No model, driver, session or qualified native capability is created here. The
original desktop transport and semantic/result authorities are injected by the
Root composition. Raw input execution remains an execution-bound MCP service.
"""
from fastapi import Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import Field

from packages.domain.desktop_control import DesktopExecutionRef
from packages.domain.desktop_intents import DesktopIntents, DesktopMessageInput
from packages.domain.discovery_schema import RevisionRef
from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.store import Conflict


class DesktopInterpretInput(RequestModel):
    operation_id: Id
    message_ref: RevisionRef
    execution_ref: DesktopExecutionRef


class DesktopAuthorizeInput(RequestModel):
    operation_id: Id
    interpretation_ref: RevisionRef


class DesktopBindExecutionInput(RequestModel):
    operation_id: Id
    authorization_ref: RevisionRef
    execution_ref: DesktopExecutionRef
    expected_intent_epoch: int = Field(strict=True, ge=1)


class DesktopRevokeInput(RequestModel):
    operation_id: Id
    expected_intent_epoch: int = Field(strict=True, ge=1)


class DesktopFinishInput(RequestModel):
    execution_ref: DesktopExecutionRef


def register_desktop_intent_routes(app, store, data_dir, *, service=None,
        workspace=None, transport=None, context_reader=None, context_checker=None,
        authorizer=None, capability_reader=None, source_checker=None,
        queue_revoker=None, cua_adapter=None, long_control=None, result_checker=None):
    """Register once; existing service/authority is never silently replaced.

    No planning fallback for desktop actions: the strict read-only planner may
    only be selected explicitly for an interpretation protocol probe. Keeping
    transport/callbacks unset leaves mutation qualification false.
    """
    existing = getattr(app.state, 'desktop_intents', None)
    if existing is not None and service is not None and existing is not service:
        raise Conflict('Desktop intent routes cannot replace the current authority')
    if getattr(app.state, 'desktop_intents_routes_registered', False):
        return existing
    value = existing or service or DesktopIntents(workspace or app.state.workspace_desktop,
        transport=transport if transport is not None else getattr(app.state, 'desktop_transport', None),
        context_reader=context_reader, context_checker=context_checker,
        authorizer=authorizer, capability_reader=capability_reader,
        source_checker=source_checker, queue_revoker=queue_revoker,
        cua_adapter=cua_adapter,
        long_control=long_control if long_control is not None else getattr(app.state, 'desktop_control', None),
        result_checker=result_checker)
    app.state.desktop_intents = value

    def host(x_eeas_desktop_host: str | None = Header(None)):
        value.ws.check_host(x_eeas_desktop_host)
        return x_eeas_desktop_host

    root = '/api/desktop-host/intents'

    @app.get(root + '/get')
    def get(_host=Depends(host)):
        return value.get()

    @app.post(root + '/messages')
    def record(body: DesktopMessageInput, token=Depends(host)):
        result = value.record_message(body.model_dump(), host_token=token)
        return JSONResponse(result, status_code=200 if result['replayed'] else 201)

    @app.get(root + '/messages/{message_id}')
    def message(message_id: Id, revision: int = Query(ge=1), sha256: Hash = Query(), _host=Depends(host)):
        reference = RevisionRef(id=message_id, revision=revision, sha256=sha256).model_dump()
        return {'message_ref': reference, **value.message(reference)}

    @app.post(root + '/interpret')
    def interpret(body: DesktopInterpretInput, token=Depends(host)):
        return value.interpret(body.operation_id, body.message_ref.model_dump(),
            body.execution_ref.model_dump(), host_token=token)

    @app.post(root + '/authorize')
    def authorize(body: DesktopAuthorizeInput, token=Depends(host)):
        result = value.authorize(body.operation_id, body.interpretation_ref.model_dump(), host_token=token)
        if result['status'] == 'AUTHORIZER_UNAVAILABLE':
            return JSONResponse({**result, 'error': {'code': 'DESKTOP_INTENT_AUTHORIZER_UNAVAILABLE',
                'message': '当前指令授权服务尚未配置；没有授予桌面输入权限。'}}, status_code=409)
        return result

    @app.post(root + '/bind')
    def bind(body: DesktopBindExecutionInput, token=Depends(host)):
        result = value.bind_execution(body.operation_id, body.authorization_ref.model_dump(),
            body.execution_ref.model_dump(), body.expected_intent_epoch, host_token=token)
        if result['status'] == 'UNAVAILABLE':
            return JSONResponse({**result, 'error': {'code': 'DESKTOP_TASK_INPUT_UNAVAILABLE',
                'message': '当前桌面输入路径尚未完成实际资格验收，控制权仍归用户。'}}, status_code=409)
        return result

    @app.post(root + '/revoke')
    async def revoke(body: DesktopRevokeInput, token=Depends(host)):
        return await value.revoke(body.operation_id, body.expected_intent_epoch, host_token=token)

    @app.post(root + '/finish')
    async def finish(body: DesktopFinishInput, token=Depends(host)):
        return await value.finish(body.execution_ref.model_dump(), host_token=token)

    app.state.desktop_intents_routes_registered = True
    return value
