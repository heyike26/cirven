"""Project-scoped local writing profiles and explicit revisioned review."""
from fastapi import Query
from fastapi.responses import JSONResponse
from packages.domain.template_profiles import TemplateProfiles,ProfileCreate,ProfileReview,ProfileImport
from packages.domain.discovery_schema import RevisionRef
from packages.domain.questions import Id


def register_template_profile_routes(app,store,data_dir,*,workspaces=None):
    service=TemplateProfiles(store,data_dir,workspaces or app.state.local_workspaces)
    app.state.template_profiles=service;root='/api/projects/{project_id}/template-profiles'

    @app.post(root)
    def create(project_id:str,body:ProfileCreate):
        value=service.create(project_id,body);return JSONResponse(value,status_code=200 if value['replayed'] else 201)

    @app.get(root)
    def listing(project_id:str,summaries:bool=Query(default=False)):
        value=service.listing(project_id)
        if not summaries:return value
        return {'profiles':[{'id':row['id'],'name':row['payload']['name'],'status':row['status'],
            'profile_ref':row['profile_ref'],'current_revision':row['current_revision']} for row in value['profiles']],
            'pending_imports':value['pending_imports']}

    @app.post(root+'/imports')
    def import_reviewed(project_id:str,body:ProfileImport):
        value=service.import_reviewed(project_id,body)
        return JSONResponse(value,status_code=200 if value['replayed'] else 201)

    @app.get(root+'/operations/{operation_id}')
    def operation(project_id:str,operation_id:Id,request_sha256:str=Query(pattern=r'^[0-9a-f]{64}$')):
        return service.operation(project_id,operation_id,request_sha256)

    @app.get(root+'/{profile_id}')
    def get(project_id:str,profile_id:str,revision:int|None=Query(None,ge=1)):return service.get(project_id,profile_id,revision)

    @app.post(root+'/{profile_id}/review')
    def review(project_id:str,profile_id:str,body:ProfileReview):
        value=service.review(project_id,profile_id,body);return JSONResponse(value,status_code=200 if value['replayed'] else 201)

    @app.get(root+'/{profile_id}/instructions')
    def instructions(project_id:str,profile_id:str,revision:int=Query(ge=1),sha256:str=Query(pattern=r'^[0-9a-f]{64}$')):
        return service.instructions(project_id,RevisionRef(id=profile_id,revision=revision,sha256=sha256))
