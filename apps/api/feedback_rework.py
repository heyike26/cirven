"""Actual persistent feedback/object binding and scoped repair API."""
from fastapi.responses import FileResponse, JSONResponse

from packages.domain.feedback_rework import BindingInput, FeedbackInput, PlanInput, ApplyInput, FeedbackRework


def register_feedback_rework_routes(app, store):
    service = FeedbackRework(store, app.state.local_workspaces, app.state.planning_proposals,
        workbench=getattr(app.state, 'workbench_feedback', None))
    app.state.feedback_rework = service
    root = '/api/projects/{pid}/feedback-rework'

    def with_artifact(pid, value, key):
        artifact = service.artifact(pid, value[key]['id'])
        return {**value, 'artifact': artifact}

    @app.post(root + '/bindings', status_code=201)
    def bind(pid: str, body: BindingInput):
        return service.bind(pid, body)

    @app.get(root + '/bindings/{ident}')
    def binding(pid: str, ident: str):
        return service.get(pid, ident, 'BINDING')

    @app.post(root + '/feedback', status_code=201)
    def feedback(pid: str, body: FeedbackInput):
        return service.feedback(pid, body)

    @app.get(root + '/feedback')
    def feedbacks(pid: str, binding_id: str | None = None):
        return {'feedback': service.listing(pid, 'FEEDBACK', binding_id)}

    @app.get(root + '/feedback/{ident}')
    def get_feedback(pid: str, ident: str):
        return service.get(pid, ident, 'FEEDBACK')

    @app.post(root + '/plans', status_code=201)
    def plan(pid: str, body: PlanInput):
        return with_artifact(pid, service.plan(pid, body), 'plan_ref')

    @app.get(root + '/plans/{ident}')
    def get_plan(pid: str, ident: str):
        value = service.get(pid, ident, 'PLAN')
        return {**value, 'artifact': service.artifact(pid, ident)}

    @app.post(root + '/plans/{ident}/apply')
    def apply(pid: str, ident: str, body: ApplyInput):
        if ident != body.plan_ref.id:
            from packages.domain.store import InvalidInput
            raise InvalidInput('Route plan ID differs from the exact plan reference')
        value = with_artifact(pid, service.apply(pid, body), 'receipt_ref')
        return JSONResponse(value, status_code=207 if value['status'] == 'PARTIAL' else 409 if value['status'] in {'CONFLICT', 'UNKNOWN_OUTCOME'} else 200)

    @app.get(root + '/operations/{operation_id}')
    def operation(pid: str, operation_id: str):
        return service.operation(pid, operation_id)

    @app.get(root + '/records/{ident}')
    def record(pid: str, ident: str):
        return service.get(pid, ident)

    @app.get(root + '/records/{ident}/artifact')
    def artifact(pid: str, ident: str):
        value = service.artifact(pid, ident)
        return FileResponse(value['path'], media_type=value['media_type'], filename='rework-' + ident + '.md')
