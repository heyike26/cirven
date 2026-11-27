"""API-accessible material preparation using the existing local authentication."""
from fastapi import Query
from packages.domain.intake import IntakeInput, IntakeService


def register_intake_routes(app, store, source_root):
    service = IntakeService(store, source_root)
    app.state.intake = service

    @app.post('/api/projects/{project_id}/intakes', status_code=201)
    def create_intake(project_id: str, body: IntakeInput):
        return service.save(project_id, body)

    @app.put('/api/projects/{project_id}/intakes/{intake_id}')
    def update_intake(project_id: str, intake_id: str, body: IntakeInput):
        return service.save(project_id, body, intake_id)

    @app.get('/api/projects/{project_id}/intakes/{intake_id}')
    def intake(project_id: str, intake_id: str, revision: int | None = Query(default=None, ge=1)):
        return service.get(project_id, intake_id, revision)

    @app.get('/api/projects/{project_id}/intakes/{intake_id}/context')
    def context(project_id: str, intake_id: str, revision: int | None = Query(default=None, ge=1)):
        return service.context(project_id, intake_id, revision)

    @app.get('/api/projects/{project_id}/intake-events')
    def events(project_id: str, after_seq: int = Query(default=0, ge=0),
               limit: int = Query(default=100, ge=1, le=100)):
        return {'events': service.events(project_id, after_seq, limit)}
