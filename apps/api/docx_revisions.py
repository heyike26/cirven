"""Read-only exact DOCX revision projections; only the original host publishes."""
from urllib.parse import quote

from fastapi.responses import Response

from packages.domain.docx_revisions import DocxRevisions


def register_docx_revision_routes(app, store, data_dir, *, snapshot_reader=None, owner_checker=None):
    service = DocxRevisions(store, data_dir,
        snapshot_reader=snapshot_reader or app.state.file_continuity.read_snapshot_bytes,
        owner_checker=owner_checker)
    app.state.docx_revisions = service
    root = '/api/projects/{pid}/docx-revisions'

    @app.get(root + '/operations/{operation_id}')
    def operation(pid: str, operation_id: str):
        return service.operation(pid, operation_id)

    @app.get(root + '/artifacts/{artifact_id}')
    def artifact(pid: str, artifact_id: str):
        return service.get(pid, artifact_id)

    @app.get(root + '/artifacts/{artifact_id}/download')
    def download(pid: str, artifact_id: str):
        raw, manifest = service.download(pid, artifact_id)
        return Response(raw, media_type=manifest['media_type'], headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(manifest['filename']),
            'ETag': '"' + manifest['sha256'] + '"', 'X-Content-Type-Options': 'nosniff'})

    return service
