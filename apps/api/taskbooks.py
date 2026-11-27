"""Authenticated local taskbook routes; saving never dispatches implementation."""
from fastapi import Query
from fastapi.responses import Response

from packages.domain.taskbooks import MAX_REVISION, TaskBookInput, TaskBookService


def register_taskbook_routes(app, store, caller_id):
    service = TaskBookService(store)
    app.state.taskbooks = service

    @app.get('/api/taskbook-template')
    def template():
        return service.template()

    @app.post('/api/projects/{project_id}/taskbooks', status_code=201)
    def create(project_id: str, body: TaskBookInput):
        return service.save(project_id, body, caller_id=caller_id)

    @app.put('/api/projects/{project_id}/taskbooks/{book_id}')
    def edit(project_id: str, book_id: str, body: TaskBookInput):
        return service.save(project_id, body, book_id, caller_id)

    @app.get('/api/projects/{project_id}/taskbooks')
    def listing(project_id: str):
        return {'taskbooks': service.list(project_id)}

    @app.get('/api/projects/{project_id}/taskbooks/{book_id}')
    def get(project_id: str, book_id: str, revision: int | None = Query(default=None, ge=1, le=MAX_REVISION)):
        return service.get(project_id, book_id, revision)

    @app.get('/api/projects/{project_id}/taskbooks/{book_id}/markdown')
    def markdown(project_id: str, book_id: str, revision: int | None = Query(default=None, ge=1, le=MAX_REVISION)):
        value = service.get(project_id, book_id, revision)
        return Response(value['payload']['markdown'].encode('utf-8'), media_type='text/markdown',
                        headers={'Content-Disposition': f'attachment; filename="taskbook-{value["revision"]}.md"'})

    @app.get('/api/projects/{project_id}/taskbooks/{book_id}/sections')
    def sections(project_id: str, book_id: str, revision: int | None = Query(default=None, ge=1, le=MAX_REVISION),
                 offset: int = Query(default=0, ge=0), limit: int = Query(default=13, ge=1, le=13)):
        value = service.get(project_id, book_id, revision)
        rows = value['payload']['sections']
        return {'taskbook_id': book_id, 'revision': value['revision'], 'sha256': value['sha256'],
                'total': len(rows), 'sections': rows[offset:offset+limit]}
