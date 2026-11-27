"""Versioned project harness API; explicit node starts retain old approvals."""
from fastapi import Query

from packages.domain.harnesses import HarnessInput, HarnessService, HarnessStart
from packages.domain.taskbooks import MAX_REVISION


def register_harness_routes(app, store, verifier, caller_id):
    service = HarnessService(store, verifier)
    app.state.harnesses = service

    @app.post('/api/projects/{project_id}/harnesses', status_code=201)
    def create(project_id: str, body: HarnessInput):
        return service.save(project_id, body, caller_id=caller_id)

    @app.put('/api/projects/{project_id}/harnesses/{harness_id}')
    def edit(project_id: str, harness_id: str, body: HarnessInput):
        return service.save(project_id, body, harness_id, caller_id)

    @app.get('/api/projects/{project_id}/harnesses')
    def listing(project_id: str):
        return {'harnesses': service.list(project_id)}

    @app.get('/api/projects/{project_id}/harnesses/{harness_id}')
    def get(project_id: str, harness_id: str, revision: int | None = Query(default=None, ge=1, le=MAX_REVISION)):
        return service.get(project_id, harness_id, revision)

    @app.get('/api/projects/{project_id}/harnesses/{harness_id}/state')
    def state(project_id: str, harness_id: str, revision: int | None = Query(default=None, ge=1, le=MAX_REVISION)):
        return service.state(project_id, harness_id, revision)

    @app.post('/api/projects/{project_id}/harnesses/{harness_id}/nodes/{node_id}/runs', status_code=202)
    def start(project_id: str, harness_id: str, node_id: str, body: HarnessStart):
        return service.start_node(project_id, harness_id, node_id, body, caller_id)
