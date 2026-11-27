"""Read-only collaboration graph from exact durable original observations."""
from fastapi import Query
from packages.agent_sessions.collaboration import project_collaboration
from packages.domain.store import Conflict
from packages.agent_sessions.child_observations import ChildObservations
from packages.domain.context_read_budget import context_read_scope
from packages.agent_sessions.child_controls import ChildMessage
from packages.agent_sessions.diffs import file_detail
from packages.agent_sessions.editor_changes import EditorChangeRequest, EditorChangeControl
from packages.agent_sessions.editor_files import EditorFileAddress, EditorFileCommand
from packages.agent_sessions.editor_observers import EditorObserverAddress, EditorObserverCommand
from packages.agent_sessions.editor_captures import EditorCaptureRequest
from packages.agent_sessions.editor_observer_recovery import (EditorObserverRecovery,
    EditorObserverRecoveryAddress, EditorObserverRecoveryCommand)
from packages.agent_sessions.diff_reviews import DiffReviews, DiffPreparation, DiffDecision


def register_agent_collaboration_routes(app, *, service=None):
    value = service or app.state.agent_sessions
    child_sources = ChildObservations(value)

    reviews=DiffReviews(value)
    from .editor_capture_feed import register_editor_capture_feed
    register_editor_capture_feed(app,value)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-observer-recovery/prepare')
    def prepare_observer_recovery(project_id:str,session_id:str,body:EditorObserverRecoveryAddress):
        return EditorObserverRecovery(value.editor_observers).prepare(project_id,session_id,body)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-observer-recoveries')
    def recover_editor_observer(project_id:str,session_id:str,body:EditorObserverRecoveryCommand):
        return EditorObserverRecovery(value.editor_observers).command(project_id,session_id,body)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/editor-observer-recoveries/{operation_id}')
    def inspect_observer_recovery(project_id:str,session_id:str,operation_id:str,editor_client_id:str=Query(min_length=1,max_length=100)):
        return EditorObserverRecovery(value.editor_observers).operation(project_id,session_id,operation_id,client=editor_client_id)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-capture-cohorts')
    def request_editor_capture(project_id:str,session_id:str,body:EditorCaptureRequest):
        return value.editor_captures.request(project_id,session_id,body)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/editor-capture-cohorts/{operation_id}')
    def inspect_editor_capture(project_id:str,session_id:str,operation_id:str,editor_client_id:str=Query(min_length=1,max_length=100)):
        return value.editor_captures.operation(project_id,session_id,operation_id,client=editor_client_id)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/editor-capture-cohorts/{operation_id}/status')
    def status_editor_capture(project_id:str,session_id:str,operation_id:str,editor_client_id:str=Query(min_length=1,max_length=100),include_dispatch:bool=False):
        return value.editor_captures.status(project_id,session_id,operation_id,client=editor_client_id,include_dispatch=include_dispatch)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-observers/prepare')
    def prepare_editor_observer(project_id:str,session_id:str,body:EditorObserverAddress):
        return value.editor_observers.prepare(project_id,session_id,body)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-observer-actions')
    def publish_editor_observer(project_id:str,session_id:str,body:EditorObserverCommand):
        return value.editor_observers.command(project_id,session_id,body)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/editor-observer-actions/{operation_id}')
    def inspect_editor_observer_action(project_id:str,session_id:str,operation_id:str,editor_client_id:str=Query(min_length=1,max_length=100)):
        return value.editor_observers.operation(project_id,session_id,operation_id,client=editor_client_id)

    @app.get('/api/projects/{project_id}/editor-observers/{observer_id}')
    def inspect_editor_observer(project_id:str,observer_id:str,editor_client_id:str=Query(min_length=1,max_length=100)):
        return value.editor_observers.inspect(project_id,observer_id,client=editor_client_id)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-files/prepare')
    def prepare_editor_file(project_id:str,session_id:str,body:EditorFileAddress):
        with context_read_scope():
            return value.editor_files.prepare(project_id,session_id,body)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-file-actions')
    async def act_editor_file(project_id:str,session_id:str,body:EditorFileCommand):
        with context_read_scope():
            return await value.editor_files.command(project_id,session_id,body)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/editor-file-actions/{operation_id}')
    def inspect_editor_file(project_id:str,session_id:str,operation_id:str,editor_client_id:str=Query(min_length=1,max_length=100)):
        return value.editor_files.operation(project_id,session_id,operation_id,client=editor_client_id)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-changes')
    async def reserve_editor_change(project_id:str,session_id:str,body:EditorChangeRequest):
        with context_read_scope():
            return await value.editor_changes.reserve(project_id,session_id,body)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/editor-changes/{operation_id}')
    def inspect_editor_change(project_id:str,session_id:str,operation_id:str,editor_client_id:str=Query(min_length=1,max_length=100)):
        return value.editor_changes.inspect(project_id,session_id,operation_id,client=editor_client_id)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/editor-change-controls')
    def control_editor_change(project_id:str,session_id:str,body:EditorChangeControl):
        with context_read_scope():
            return value.editor_changes.control(project_id,session_id,body)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/editor-change-controls/{operation_id}')
    def inspect_editor_control(project_id:str,session_id:str,operation_id:str,editor_client_id:str=Query(min_length=1,max_length=100)):
        return value.editor_changes.control_operation(project_id,session_id,operation_id,client=editor_client_id)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/diff-reviews/prepare')
    async def prepare_diff_review(project_id:str,session_id:str,body:DiffPreparation):
        with context_read_scope():
            return await reviews.prepare(project_id,session_id,body)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/diff-reviews')
    async def record_diff_review(project_id:str,session_id:str,body:DiffDecision):
        with context_read_scope():
            return await reviews.decide(project_id,session_id,body)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/diff-reviews/{operation_id}')
    def read_diff_review(project_id:str,session_id:str,operation_id:str):
        return reviews.operation(project_id,session_id,operation_id)

    @app.post('/api/projects/{project_id}/agent-sessions/{session_id}/child-messages')
    async def child_message(project_id: str,session_id: str,body: ChildMessage):
        return await value.child_controls.submit(project_id,session_id,body)

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/children/{child_id}/diffs')
    async def child_diffs(project_id:str,session_id:str,child_id:str,generation:int=Query(ge=1),
            session_sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),child_sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),
            root_native_session_id:str=Query(pattern=r'^ses_[A-Za-z0-9_-]{1,100}$'),
            workspace_id:str=Query(pattern=r'^[a-f0-9]{64}$'),binding_revision:int=Query(ge=1),
            binding_sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),diff_id:str|None=Query(None,pattern=r'^[a-f0-9]{64}$'),
            snapshot_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$'),diff_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
        child_ref={'id':child_id,'root_native_session_id':root_native_session_id,'sha256':child_sha256}
        with context_read_scope():
            directory,content=await child_sources.diffs(project_id,session_id,
                {'id':session_id,'revision':1,'sha256':session_sha256},generation,child_ref,
                {'id':workspace_id,'revision':binding_revision,'binding_sha256':binding_sha256})
            if diff_id is None:
                return directory
            if snapshot_sha256 is None or diff_sha256 is None:
                raise Conflict('An exact child snapshot and file diff reference is required')
            return {**file_detail(directory,content,{'id':directory['snapshot_ref']['id'],'revision':1,'sha256':snapshot_sha256},
                {'id':diff_id,'revision':1,'sha256':diff_sha256}),'child_ref':child_ref}

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/children/{child_id}')
    async def child(project_id: str, session_id: str, child_id: str,
                    generation: int = Query(ge=1),
                    session_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
                    child_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
                    root_native_session_id: str = Query(pattern=r'^[A-Za-z0-9_-]{1,100}$')):
        with context_read_scope():
            return await child_sources.read(project_id, session_id,
                {'id':session_id,'revision':1,'sha256':session_sha256}, generation,
                {'id':child_id,'root_native_session_id':root_native_session_id,'sha256':child_sha256})

    @app.get('/api/projects/{project_id}/agent-sessions/{session_id}/collaboration')
    def collaboration(project_id: str, session_id: str,
                      generation: int | None = Query(default=None, ge=0),
                      session_sha256: str | None = Query(default=None, pattern=r'^[a-f0-9]{64}$')):
        observed = value.get(project_id, session_id)
        if session_sha256 is not None and observed['session_ref']['sha256'] != session_sha256:
            raise Conflict('Original collaboration session reference changed')
        return project_collaboration(observed, project_id=project_id, generation=generation)

    return value
