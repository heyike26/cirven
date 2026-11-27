"""Real routes/SQLite/ZIP: read-only HTTP publication projection, no model/GUI."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.docx_revisions import register_docx_revision_routes
from packages.domain.store import Conflict, InvalidInput, NotFound
from test_docx_revisions import revision_scene, check, publish
from test_file_continuity import scene


def test_actual_http_artifact_download_and_operation_are_get_only(revision_scene):
    s = revision_scene; value = publish(s); app = FastAPI()
    for typ, status in ((Conflict, 409), (InvalidInput, 422), (NotFound, 404)):
        def handler(_request, error, status=status): return JSONResponse({'error': type(error).__name__}, status_code=status)
        app.add_exception_handler(typ, handler)
    register_docx_revision_routes(app, s['store'], s['data'], snapshot_reader=s['continuity'].read_snapshot_bytes)
    with TestClient(app) as client:
        artifact = value['artifact']; root = f'/api/projects/{s["pid"]}/docx-revisions'
        result = client.get(root + '/operations/publication-one')
        check(result.status_code == 200 and result.json() == value)
        fetched = client.get(root + '/artifacts/' + artifact['id'])
        check(fetched.status_code == 200 and fetched.json() == artifact)
        downloaded = client.get(artifact['download_url'])
        check(downloaded.status_code == 200 and downloaded.content[:2] == b'PK')
        check(downloaded.headers['etag'] == '"' + artifact['sha256'] + '"')
        check(client.post(root + '/artifacts/' + artifact['id']).status_code == 405)
        check(client.post(root + '/publish', json={}).status_code in (404, 405))
        check(client.get(root + '/operations/not-existing').json()['status'] == 'NOT_ADMITTED')
        other = s['store'].create_project('Other', '')['id']
        check(client.get(f'/api/projects/{other}/docx-revisions/artifacts/{artifact["id"]}').status_code == 404)
        check((s['folder'] / '论文.docx').read_bytes() == s['raw'])
