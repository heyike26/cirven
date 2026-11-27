"""Explicit scoped local-user decisions; no model or automatic task dispatch."""
from packages.domain.decisions import DecisionService, ResolutionInput, StartDecisionInput


def register_decision_routes(app, store, caller_id):
    service = DecisionService(store)
    app.state.decisions = service

    @app.post('/api/projects/{project_id}/question-resolutions', status_code=201)
    def resolution(project_id: str, body: ResolutionInput):
        return service.resolve(project_id, body, caller_id)

    @app.get('/api/projects/{project_id}/question-resolutions/{resolution_id}')
    def get_resolution(project_id: str, resolution_id: str):
        return service.get_resolution(project_id, resolution_id)

    @app.post('/api/projects/{project_id}/start-decisions', status_code=201)
    def start_decision(project_id: str, body: StartDecisionInput):
        return service.start(project_id, body, caller_id)

    @app.get('/api/projects/{project_id}/start-decisions/{decision_id}')
    def get_start(project_id: str, decision_id: str):
        return service.get_start(project_id, decision_id)
