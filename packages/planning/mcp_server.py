"""Official MCP SDK stdio adapter, scoped to one explicit local project."""
import argparse
from pathlib import Path

from mcp.server import MCPServer

from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.tasks import TaskService
from packages.planning.toolkit import PlanningToolkit


def create_server(toolkit,project_id):
    # The selected project is captured, never a model-supplied tool argument.
    toolkit.proposals.store.project(project_id)
    server=MCPServer('eeas-planning',version='1.0.0',instructions=(
        'Read source-bound planning data. Check gaps and ask people for missing information. '
        'These tools do not accept a plan, approve tool permissions, or start execution.'))

    @server.tool(name='validate_network')
    def validate_network(candidate:dict)->dict:
        return toolkit.validate_network(candidate)

    @server.tool(name='acceptance_coverage')
    def acceptance_coverage(candidate:dict)->dict:
        return toolkit.acceptance_coverage(candidate)

    @server.tool(name='review_sources')
    def review_sources(proposal_id:str,revision:int|None=None)->dict:
        return toolkit.review_sources(project_id,proposal_id,revision)

    @server.tool(name='pending_questions')
    def pending_questions(proposal_id:str,revision:int|None=None)->dict:
        return toolkit.pending_questions(project_id,proposal_id,revision)

    @server.tool(name='prompt_preview')
    def prompt_preview(proposal_id:str,revision:int|None=None)->dict:
        return toolkit.prompt_preview(project_id,proposal_id,revision)

    @server.tool(name='planning_templates')
    def planning_templates()->dict:
        return toolkit.planning_templates()
    return server


def main():
    parser=argparse.ArgumentParser(description='EEAS project-scoped planning MCP stdio service')
    parser.add_argument('--data-dir',required=True,type=Path)
    parser.add_argument('--project-id',required=True)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args()
    store=TaskService(args.data_dir/'eeas.sqlite3',args.source_root)
    proposals=PlanningProposals(store,args.source_root,workspaces=LocalWorkspaces(store,args.data_dir))
    create_server(PlanningToolkit(proposals),args.project_id).run(transport='stdio')


if __name__=='__main__':main()
