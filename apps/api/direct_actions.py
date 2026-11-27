"""Inline ordinary file actions; no forced discovery or workspace panel."""
from fastapi.responses import JSONResponse
from packages.domain.direct_actions import DirectActions, DirectActionInput


def register_direct_action_routes(app, store, data_dir, *, workspaces=None, transport=None):
    if getattr(app.state, 'direct_actions', None) is not None:
        return
    service = DirectActions(store, data_dir, workspaces or app.state.local_workspaces,
                            transport or app.state.planning_proposals.transport)
    app.state.direct_actions = service
    base = '/api/projects/{project_id}/direct-actions'

    @app.get('/api/direct-actions/capabilities')
    def capabilities():
        return {'tool_id': 'local.file.edit', 'version': 1,
            'context_scope': 'EXACT_EXISTING_UTF8_FILESOURCE', 'max_file_bytes': 1048576,
            'permission_scope': 'EXACT_USER_FILE_WRITE_AND_EXISTING_WORKSPACE_GRANT',
            'interaction_modes': ['NONE', 'INLINE_QUESTION'], 'requires_discovery': False,
            'output_types': ['FILE_CHANGE', 'DIFF', 'INLINE_QUESTION', 'ORIGINAL_EXECUTION_PROGRESS'],
            'input_schema': DirectActionInput.model_json_schema()}

    @app.post(base)
    async def run(project_id: str, body: DirectActionInput):
        result = await service.run(project_id, body)
        status = 202 if result['status'] in {'PROCESSING', 'CANCEL_REQUESTED'} else 422 if result['status'] in {'FAILED', 'UNKNOWN_OUTCOME'} else 200
        return JSONResponse(result, status_code=status)

    @app.get(base + '/{operation_id}')
    def get(project_id: str, operation_id: str):
        return service.get(project_id, operation_id)

    @app.get(base + '/{operation_id}/events')
    def events(project_id: str, operation_id: str):
        return service.events(project_id, operation_id)

    @app.post(base + '/{operation_id}/cancel')
    async def cancel(project_id: str, operation_id: str):
        return await service.cancel(project_id, operation_id)
