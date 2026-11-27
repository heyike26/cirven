"""Real deterministic discovery diagrams over the authoritative session reader."""
from fastapi import Query
from fastapi.responses import Response
from packages.domain.local_workspaces import canonical
from packages.domain.planning_diagrams import PlanningDiagrams


def register_discovery_diagram_routes(app,store,data_dir,*,session_reader=None,source_root=None,font_path=None):
    reader=session_reader or app.state.discovery.get
    service=PlanningDiagrams(store,data_dir,reader,source_root=source_root,font_path=font_path)
    app.state.planning_diagrams=service

    @app.get('/api/projects/{project_id}/discovery/sessions/{session_id}/diagrams/{graph_id}/artifact')
    def artifact(project_id:str,session_id:str,graph_id:str,revision:int|None=Query(None,ge=1)):
        value=service.generate(project_id,session_id,graph_id,revision)
        base=f'/api/projects/{project_id}/discovery/sessions/{session_id}/diagrams/{graph_id}'
        value['files']={key:{k:item[k] for k in ('filename','media_type','sha256','byte_length')}|
            {'download_url':base+f'?revision={value["session_ref"]["revision"]}&format={key}'} for key,item in value['files'].items()}
        return value

    @app.get('/api/projects/{project_id}/discovery/sessions/{session_id}/diagrams/{graph_id}')
    def diagram(project_id:str,session_id:str,graph_id:str,revision:int|None=Query(None,ge=1),format:str='svg'):
        raw,item,result=service.download(project_id,session_id,graph_id,revision,format)
        return Response(raw,media_type=item['media_type'],headers={'ETag':'"'+item['sha256']+'"',
            'X-EEAS-Artifact-Ref':canonical(result['artifact_ref']),'X-EEAS-Session-SHA256':result['session_ref']['sha256'],
            'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; style-src 'none'; sandbox"})
