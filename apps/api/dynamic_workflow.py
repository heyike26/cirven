"""Same-origin task planning; no execution, model loop or arbitrary plugin loading."""
from copy import deepcopy
from typing import Literal

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from packages.agent_sessions.models import ConfigurationRef
from packages.dynamic_workflow.store import (DynamicWorkflowStore, WorkflowConflict,
    WorkflowIntegrityError, WorkflowInvalidInput, WorkflowNotFound)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


class Create(Input):
    plan: dict


class Replan(Create):
    expected_ref: ConfigurationRef
    reason: str = Field(min_length=1, max_length=8000)


class Answer(Input):
    expected_ref: ConfigurationRef
    answer: str = Field(min_length=1, max_length=8000)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal['AUTO', 'MANUAL']
    value: StrictStr | None = Field(max_length=300)

    @model_validator(mode='after')
    def exact_choice(self):
        if (self.mode == 'AUTO' and self.value is not None or
                self.mode == 'MANUAL' and (self.value is None or not self.value.strip())):
            raise ValueError('An automatic choice has no value; a manual choice requires one')
        return self


class AgentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Selection
    depth: Selection


class Policy(Input):
    expected_ref: ConfigurationRef
    agent_policy: AgentPolicy


class Locks(Input):
    expected_ref: ConfigurationRef
    fields: list[str] = Field(max_length=8)


class PluginOwner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    session_ref: ConfigurationRef
    generation: int = Field(ge=1, strict=True)


class PluginEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    event_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    owner_ref: PluginOwner
    native_session_id: str = Field(pattern=r'^ses_[A-Za-z0-9_-]{1,100}$')
    event_type: Literal['SESSION_STATUS', 'SESSION_IDLE', 'SESSION_ERROR']
    status: Literal['busy', 'idle', 'retry'] | None = None
    observed_at: str = Field(min_length=1, max_length=80)
    source_event_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,120}$')


class PluginPlan(Input):
    schema_version: Literal[1]
    owner_ref: PluginOwner
    expected_ref: ConfigurationRef
    plan: dict
    reason: str = Field(min_length=1, max_length=8000)


def public_workflow_catalog(app):
    # Saved configuration is selectable intent, not inference qualification.
    # Reasoning variants are not inferred from a model name or token allowance.
    routes = []
    for item in app.state.provider_settings.list():
        ref = item["profile_ref"]
        model = item.get("model_id") or item.get("model")
        if not isinstance(model, str):
            continue
        efforts = item.get('reasoning_efforts', [])
        routes.append({"id": ref["id"], "label": model, "model_id": model,
                       "provider_id": item["provider_id"], "profile_ref": ref,
                       "depth_options": efforts, "depth_qualification": "DECLARED_NOT_OBSERVED" if efforts else "UNSUPPORTED",
                       "model_qualification": "CONFIGURED_NOT_OBSERVED"})
    return {"routes": routes, "model_calls": 0, "implicit_account_discovery": False}


def register_dynamic_workflow_routes(app, store):
    workflows = DynamicWorkflowStore(store)
    app.state.dynamic_workflows = workflows
    app.state.agent_sessions.dynamic_workflows = workflows
    app.state.agent_sessions.workflow_event_origin = app.state.settings.origin
    root = "/api/projects/{project_id}/dynamic-workflows"

    def invoke(method, *args, **kwargs):
        try:
            result = method(*args, **kwargs)
            if method.__name__ in {'replan', 'answer', 'set_locks'}:
                from packages.agent_sessions.workflow import sync_workflow_policies
                result = {**result, 'runtime_activation': sync_workflow_policies(app.state.agent_sessions, args[0], args[1])}
            return result
        except WorkflowNotFound:
            raise HTTPException(404, "尚未找到原工作流或操作回执；未重发。") from None
        except (WorkflowConflict, WorkflowIntegrityError):
            raise HTTPException(409, "任务链版本或人工锁定已变化；原输入保留，请核对当前版本。") from None
        except WorkflowInvalidInput:
            raise HTTPException(422, "任务链、依赖或配置不完整；原记录保留。") from None

    @app.get("/api/dynamic-workflow/catalog")
    def catalog():
        return public_workflow_catalog(app)

    @app.post('/api/dynamic-workflow/plugin-events')
    def plugin_event(body: PluginEvent, request: Request):
        from packages.agent_sessions.workflow import admitted_event_owner
        from packages.domain.tasks import digest
        sessions = app.state.agent_sessions
        admitted = admitted_event_owner(sessions, request.headers.get('authorization'))
        if admitted is None:
            raise HTTPException(401, '此插件事件不属于当前原执行。')
        ident, context = admitted
        expected = context['workflow']['admission']['owner_ref']
        if (body.owner_ref.model_dump() != expected
                or body.native_session_id not in context['known_sessions']):
            raise HTTPException(409, '插件事件的原会话或代次已变化。')
        seen = context.setdefault('workflow_event_receipts', {})
        payload = body.model_dump(exclude_none=True)
        checksum = digest(payload)
        if body.event_id in seen and seen[body.event_id] != checksum:
            raise HTTPException(409, '原插件事件标识已绑定其他内容。')
        stored = body.event_id in seen
        if not stored and len(seen) < 2048:
            sessions._event(expected['project_id'], ident, 'WORKFLOW_PLUGIN_DIRTY_HINT', payload)
            seen[body.event_id] = checksum
            stored = True
        try:
            sessions.global_supervisor.wake('PLUGIN_EVENT')
        except Exception:
            pass
        return {'accepted': True, 'recorded': stored, 'authority': 'REOBSERVATION_HINT_ONLY',
                'model_calls': 0, 'dispatch_performed': False}

    def plan_owner(request, *, read_only=False):
        from packages.agent_sessions.workflow import admitted_workflow_owner
        result = admitted_workflow_owner(app.state.agent_sessions, request.headers.get('authorization'), plan=True, read_only=read_only)
        if result is None:
            raise HTTPException(401, '计划工具未绑定当前原执行；事件令牌不能修改计划。')
        return result

    def plan_operation(context, ident, operation_id):
        from packages.domain.tasks import digest
        sessions = app.state.agent_sessions
        owner_ref = context['workflow']['admission']['owner_ref']
        path = sessions._path(owner_ref['project_id'], ident, 'workflow-plan-operations', digest([context['generation'], operation_id]) + '.json')
        if not path.is_file():
            raise HTTPException(404, '尚未观察到原计划工具回执；没有重发。')
        intent = sessions._read(path)
        source = intent['request']
        core_id = 'wfplan_' + digest([ident, context['generation'], operation_id])[:48]
        if (source['owner_ref'] != owner_ref or source['operation_id'] != operation_id
                or digest(source) != intent['request_sha256'] or intent['core_operation_id'] != core_id
                or source['expected_ref']['id'] != context['workflow_id']):
            raise HTTPException(409, '计划工具的原操作归属不一致。')
        result = invoke(workflows.operation, owner_ref['project_id'], context['workflow_id'], intent['core_operation_id'])
        from packages.dynamic_workflow.store import _plan as normalize_plan
        expected_core_sha = digest({'method': 'replan', 'workflow_id': context['workflow_id'],
            'plan': normalize_plan(source['plan'], owner_ref['project_id']),
            'expected_ref': source['expected_ref'], 'actor': 'MODEL', 'reason': source['reason']})
        if result['request_sha256'] != expected_core_sha:
            raise HTTPException(409, '原计划回执绑定了不同输入；本次提交仍待核对，没有重发。')
        return {**result, 'operation_id': operation_id, 'core_operation_id': intent['core_operation_id'],
                'plugin_request_sha256': intent['request_sha256'], 'write_status': 'COMMITTED',
                'runtime_activation': [{'session_id': ident, 'generation': context['generation'],
                    'status': context.get('workflow_activation_status', 'POLICY_SYNC_UNCONFIRMED') if
                        context.get('workflow_plan_ref') == result['workflow_ref'] else 'POLICY_SYNC_UNCONFIRMED'}]}

    @app.get('/api/dynamic-workflow/plugin-plan/operations/{operation_id}')
    def plugin_plan_operation(operation_id: str, request: Request):
        ident, context = plan_owner(request, read_only=True)
        return plan_operation(context, ident, operation_id)

    @app.post('/api/dynamic-workflow/plugin-plan')
    def plugin_plan(body: PluginPlan, request: Request):
        from packages.domain.tasks import digest
        sessions = app.state.agent_sessions
        ident, context = plan_owner(request)
        expected = context['workflow']['admission']['owner_ref']
        if body.owner_ref.model_dump() != expected or body.expected_ref.id != context['workflow_id']:
            raise HTTPException(409, '计划工具不能修改其他任务链或原执行代次。')
        raw = body.model_dump()
        checksum = digest(raw)
        path = sessions._path(expected['project_id'], ident, 'workflow-plan-operations', digest([context['generation'], body.operation_id]) + '.json')
        with store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if path.exists():
                if sessions._read(path)['request_sha256'] != checksum:
                    raise HTTPException(409, '此计划操作已绑定其他输入；未重发。')
                created = False
            else:
                created = True
                sessions._write(path, {'request': raw, 'request_sha256': checksum,
                    'core_operation_id': 'wfplan_' + digest([ident, context['generation'], body.operation_id])[:48]})
        if created:
            intent = sessions._read(path)
            # Intent precedes the one mutation. A lost response is recovered
            # only through the original operation; no hidden replan retry.
            invoke(workflows.replan, expected['project_id'], context['workflow_id'], body.plan,
                expected_ref=body.expected_ref.model_dump(), operation_id=intent['core_operation_id'],
                actor='MODEL', reason=body.reason)
        return plan_operation(context, ident, body.operation_id)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/dynamic-workflow')
    def plugin_projection(project_id: str, session_id: str, generation: int = Query(ge=1),
                          session_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        from packages.agent_sessions.workflow import read_workflow_projection
        sessions = app.state.agent_sessions
        _manifest, reference = sessions._manifest(project_id, session_id)
        if sessions._state(project_id, session_id)['generation'] != generation or reference['sha256'] != session_sha256:
            raise HTTPException(409, '插件观察属于其他原会话或代次。')
        return read_workflow_projection(sessions, project_id, session_id)

    @app.get(root)
    def directory(project_id: str):
        return {"workflows": invoke(workflows.list, project_id)}

    @app.post(root)
    def create(project_id: str, body: Create):
        return invoke(workflows.create, project_id, body.plan,
                      operation_id=body.operation_id, actor="USER")

    @app.get(root + "/operations/{operation_id}")
    def operation(project_id: str, operation_id: str):
        return invoke(workflows.operation, project_id, None, operation_id)

    @app.get(root + "/{workflow_id}")
    def read(project_id: str, workflow_id: str, revision: int | None = Query(default=None, ge=1)):
        return invoke(workflows.get, project_id, workflow_id, revision=revision)

    @app.post(root + "/{workflow_id}/replan")
    def replan(project_id: str, workflow_id: str, body: Replan):
        return invoke(workflows.replan, project_id, workflow_id, body.plan,
                      expected_ref=body.expected_ref.model_dump(), operation_id=body.operation_id,
                      actor="USER", reason=body.reason)

    @app.post(root + "/{workflow_id}/questions/{question_id}/answer")
    def answer(project_id: str, workflow_id: str, question_id: str, body: Answer):
        return invoke(workflows.answer, project_id, workflow_id, question_id, body.answer,
                      expected_ref=body.expected_ref.model_dump(), operation_id=body.operation_id, actor="USER")

    @app.post(root + "/{workflow_id}/nodes/{node_id}/policy")
    def policy(project_id: str, workflow_id: str, node_id: str, body: Policy):
        basis = invoke(workflows.get, project_id, workflow_id, revision=body.expected_ref.revision)
        if basis["workflow_ref"] != body.expected_ref.model_dump():
            raise HTTPException(409, "任务链版本已变化；选择保留，尚未提交新策略。")
        plan = deepcopy(basis["plan"])
        node = next((item for item in plan["nodes"] if item["id"] == node_id), None)
        if node is None:
            raise HTTPException(404, "所选节点不在当前任务链中。")
        routes = {item["id"]: item for item in public_workflow_catalog(app)["routes"]}
        selected = body.agent_policy.model_dump()
        model = selected.get("model", {})
        depth = selected.get("depth", {})
        if model.get("mode") == "MANUAL" and model.get("value") not in routes:
            raise HTTPException(422, "所选模型连接不可用；请配置连接或恢复自动。")
        if depth.get("mode") == "MANUAL":
            candidates = [routes[model["value"]]] if model.get("mode") == "MANUAL" and model.get("value") in routes else list(routes.values())
            if not any(depth.get("value") in route["depth_options"] for route in candidates):
                raise HTTPException(422, "当前连接没有可核对的思考深度选项；未伪装为已生效。")
        node["agent_policy"] = selected
        return invoke(workflows.replan, project_id, workflow_id, plan,
                      expected_ref=body.expected_ref.model_dump(), operation_id=body.operation_id,
                      actor="USER", reason="CHANGE_AGENT_POLICY")

    @app.post(root + "/{workflow_id}/nodes/{node_id}/locks")
    def locks(project_id: str, workflow_id: str, node_id: str, body: Locks):
        return invoke(workflows.set_locks, project_id, workflow_id, node_id, body.fields,
                      expected_ref=body.expected_ref.model_dump(), operation_id=body.operation_id, actor="USER")

    return workflows
