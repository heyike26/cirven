"""Same planning toolkit for the interface and original MCP clients."""
from pydantic import BaseModel,ConfigDict

from packages.domain.planning_schema import PlanningCandidate
from packages.planning.toolkit import TOOLS,PlanningToolkit


class NetworkInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    candidate:PlanningCandidate


def register_planning_tool_routes(app):
    toolkit=PlanningToolkit(app.state.planning_proposals)
    app.state.planning_toolkit=toolkit

    @app.get('/api/planning/capabilities')
    def capabilities():
        return {'tools':TOOLS,'mcp':{'name':'eeas-planning','transport':'stdio','sdk':'mcp==2.3.0',
            'project_scope':'ONE_SELECTED_PROJECT','execution_permissions':[]},
            'skills':['source-review','clarify-gaps','task-decomposition','acceptance-review','report-handoff']}

    @app.post('/api/planning/tools/validate-network')
    def validate(body:NetworkInput):return toolkit.validate_network(body.candidate.model_dump())

    @app.post('/api/planning/tools/acceptance-coverage')
    def coverage(body:NetworkInput):return toolkit.acceptance_coverage(body.candidate.model_dump())

    @app.get('/api/planning/tools/templates')
    def templates():return toolkit.planning_templates()
