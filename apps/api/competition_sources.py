"""User-selected competition evidence; application's existing CSRF boundary applies."""
from fastapi.responses import JSONResponse, Response

from packages.domain.competition_sources import CompetitionSources, CompetitionSourceInput


def register_competition_source_routes(app, store, data_dir):
    service = CompetitionSources(store, data_dir)
    app.state.competition_sources = service
    root = "/api/projects/{project_id}/competition/sources"

    @app.post(root)
    def capture(project_id: str, body: CompetitionSourceInput):
        result = service.capture(project_id, body)
        status = 202 if result["status"] in {"PROCESSING", "UNKNOWN_OUTCOME"} else 422 if result["status"] == "FAILED" else 200 if result["replayed"] else 201
        return JSONResponse(result, status_code=status)

    @app.get(root)
    def listing(project_id: str):
        return {"sources": service.list(project_id)}

    @app.get(root + "/{source_id}")
    def get(project_id: str, source_id: str):
        return service.get(project_id, source_id)

    @app.get(root + "/{source_id}/snapshot")
    def snapshot(project_id: str, source_id: str):
        raw = service.snapshot(project_id, source_id)
        return Response(raw, media_type="application/octet-stream", headers={
            "Content-Disposition": 'attachment; filename="competition-snapshot.bin"',
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})
