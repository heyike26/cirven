"""Generic feedback with genuine file versions, independent of mutation adapters."""
from packages.domain.native_feedback import NativeFeedback, NativeFeedbackInput, DirectActionLinkInput


def register_native_feedback_routes(app, store):
    service = NativeFeedback(store, app.state.local_workspaces, direct_actions=getattr(app.state, 'direct_actions', None))
    app.state.native_feedback = service
    base = '/api/projects/{pid}/native-feedback'

    @app.post(base, status_code=201)
    def record(pid: str, body: NativeFeedbackInput):
        return service.record(pid, body)

    @app.get(base + '/operations/{operation_id}')
    def operation(pid: str, operation_id: str):
        return service.operation(pid, operation_id)

    @app.get(base + '/{ident}')
    def feedback(pid: str, ident: str):
        return service.get(pid, ident)

    @app.post(base + '/{ident}/direct-action-links', status_code=201)
    def link(pid: str, ident: str, body: DirectActionLinkInput):
        return service.link(pid, ident, body)
