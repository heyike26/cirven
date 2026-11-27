"""Authenticated project report generation and verified-byte downloads."""
from urllib.parse import quote

from fastapi.responses import JSONResponse, Response

from packages.domain.report_artifacts import ReportArtifactInput, ReportArtifacts


def register_report_routes(app, store, data_dir):
    service = ReportArtifacts(store, data_dir)
    app.state.report_artifacts = service

    @app.post("/api/projects/{project_id}/report-artifacts")
    def create_report(project_id: str, body: ReportArtifactInput):
        result = service.create(project_id, body)
        return JSONResponse(result, status_code=200 if result["replayed"] else 201)

    @app.get("/api/projects/{project_id}/report-artifacts")
    def reports(project_id: str):
        return {"artifacts": service.list(project_id)}

    @app.get("/api/projects/{project_id}/report-artifact-attempts")
    def attempts(project_id: str):
        return {"attempts": service.attempts(project_id)}

    @app.get("/api/projects/{project_id}/report-artifacts/{artifact_id}")
    def report(project_id: str, artifact_id: str):
        return service.get(project_id, artifact_id)

    @app.get("/api/projects/{project_id}/report-artifacts/{artifact_id}/download")
    def download(project_id: str, artifact_id: str):
        manifest, raw = service.download(project_id, artifact_id)
        # Response sends the bytes actually checked above; it does not reopen
        # an unverified path later in FileResponse's asynchronous send.
        return Response(raw, media_type=manifest["media_type"], headers={
            "Content-Disposition": "attachment; filename=\"report.pptx\"; filename*=UTF-8''" + quote(manifest["filename"]),
            "ETag": '"' + manifest["sha256"] + '"',
            "X-Content-Type-Options": "nosniff"})
