"""Original MCP SDK facade for project-scoped discovery evidence, with no writes."""
import argparse
from pathlib import Path
from packages.domain.discovery import DiscoveryWorkbench
from packages.domain.discovery_schema import DiscoveryCandidate
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.tasks import TaskService
from packages.planning.discovery_methods import method_pack
from packages.planning.mcp_server import create_server
from packages.planning.toolkit import PlanningToolkit
from packages.domain.store import NotFound


def create_discovery_server(proposals,project_id):
    discovery=DiscoveryWorkbench(proposals.store,proposals,recover_operations=False)
    server=create_server(PlanningToolkit(proposals),project_id)

    @server.tool(name='discovery_session')
    def session(session_id:str,revision:int|None=None)->dict:
        return discovery.get(project_id,session_id,revision)

    @server.tool(name='discovery_pending')
    def pending(session_id:str,revision:int|None=None)->dict:
        return discovery.pending(project_id,session_id,revision)

    @server.tool(name='discovery_methods')
    def methods()->dict:return method_pack(proposals.source_root)

    @server.tool(name='validate_discovery')
    def validate(candidate:dict)->dict:
        value=DiscoveryCandidate.model_validate(candidate)
        return {'valid_structure':True,'claim_count':len(value.claims),'graph_count':len(value.graphs),
            'semantic_correctness':'REQUIRES_HUMAN_REVIEW','source_quotes':'NOT_VERIFIED_BY_STRUCTURE_ONLY'}

    @server.tool(name='discovery_graph')
    def graph(session_id:str,graph_id:str,revision:int|None=None)->dict:
        value=discovery.get(project_id,session_id,revision)
        model=next((g for g in value['payload']['candidate']['graphs'] if g['id']==graph_id),None)
        if model is None:raise NotFound('Graph is outside this project/session')
        return {'session_ref':{k:value[k] for k in ['id','revision','sha256']},'graph':model,
            'source_current':value['source_current'],'semantic_correctness':'REQUIRES_HUMAN_REVIEW'}
    return server


def main():
    parser=argparse.ArgumentParser(description='EEAS project-scoped read-only discovery MCP service')
    parser.add_argument('--data-dir',required=True,type=Path);parser.add_argument('--project-id',required=True)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[2]);args=parser.parse_args()
    store=TaskService(args.data_dir/'eeas.sqlite3',args.source_root)
    proposals=PlanningProposals(store,args.source_root,workspaces=LocalWorkspaces(store,args.data_dir))
    create_discovery_server(proposals,args.project_id).run(transport='stdio')


if __name__=='__main__':main()
