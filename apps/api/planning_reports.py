"""Authenticated real Word and frozen pending-question downloads."""
from urllib.parse import quote
from fastapi.responses import JSONResponse,Response

from packages.domain.planning_reports import PlanningReports,PlanningReportInput


def register_planning_report_routes(app,store,data_dir,*,proposals=None,workspaces=None,experience_reader=None,discovery_reader=None,diagram_reader=None):
    profiles=getattr(app.state,'template_profiles',None);discovery=getattr(app.state,'discovery',None);diagrams=getattr(app.state,'planning_diagrams',None)
    service=PlanningReports(store,data_dir,proposals or app.state.planning_proposals,
                           workspaces or getattr(app.state,'local_workspaces',None),
                           experience_reader=experience_reader or (profiles.instructions if profiles else None),
                           discovery_reader=discovery_reader or (discovery.get if discovery else None),
                           diagram_reader=diagram_reader or (diagrams.read_artifact if diagrams else None))
    app.state.planning_reports=service
    root='/api/projects/{project_id}/planning/proposals/{proposal_id}/reports'

    @app.post(root)
    def generate(project_id:str,proposal_id:str,body:PlanningReportInput):
        value=service.generate(project_id,proposal_id,body)
        return JSONResponse(value,status_code=200 if value['replayed'] else 201)

    def part(project_id,proposal_id,artifact_id,kind,source_kind='PROPOSAL'):
        raw,item=service.download(project_id,proposal_id,artifact_id,kind,source_kind=source_kind)
        return Response(raw,media_type=item['media_type'],headers={
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(item['filename']),
            'ETag':'"'+item['sha256']+'"','X-Content-Type-Options':'nosniff'})

    @app.get(root+'/{artifact_id}/download')
    def word(project_id:str,proposal_id:str,artifact_id:str):return part(project_id,proposal_id,artifact_id,'docx')

    @app.get(root+'/{artifact_id}/pending/download')
    def pending(project_id:str,proposal_id:str,artifact_id:str):return part(project_id,proposal_id,artifact_id,'pending_markdown')

    @app.get(root+'/{artifact_id}/pending/json/download')
    def pending_json(project_id:str,proposal_id:str,artifact_id:str):return part(project_id,proposal_id,artifact_id,'pending_json')

    @app.get(root+'/{artifact_id}/source/download')
    def source(project_id:str,proposal_id:str,artifact_id:str):return part(project_id,proposal_id,artifact_id,'source_snapshot')

    discovery_root='/api/projects/{project_id}/discovery/sessions/{session_id}/reports'
    @app.post(discovery_root)
    def discovery_report(project_id:str,session_id:str,body:PlanningReportInput):
        value=service.generate_discovery(project_id,session_id,body)
        return JSONResponse(value,status_code=200 if value['replayed'] else 201)

    @app.get(discovery_root+'/{artifact_id}/download')
    def discovery_word(project_id:str,session_id:str,artifact_id:str):return part(project_id,session_id,artifact_id,'docx','DISCOVERY')

    @app.get(discovery_root+'/{artifact_id}/pending/download')
    def discovery_pending(project_id:str,session_id:str,artifact_id:str):return part(project_id,session_id,artifact_id,'pending_markdown','DISCOVERY')

    @app.get(discovery_root+'/{artifact_id}/pending/json/download')
    def discovery_pending_json(project_id:str,session_id:str,artifact_id:str):return part(project_id,session_id,artifact_id,'pending_json','DISCOVERY')

    @app.get(discovery_root+'/{artifact_id}/source/download')
    def discovery_source(project_id:str,session_id:str,artifact_id:str):return part(project_id,session_id,artifact_id,'source_snapshot','DISCOVERY')
