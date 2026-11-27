from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field
from packages.dynamic_workflow import kernel_settings as settings


class Revision(BaseModel):
    model_config=ConfigDict(extra="forbid")
    revision:int=Field(ge=0,strict=True)


class KernelRoute(Revision):
    endpoint:str=Field(min_length=1,max_length=2048)
    model:str=Field(min_length=1,max_length=160)
    credential_env:str=Field(default="",max_length=90)
    allow_http:bool=False


class InstallVersion(Revision):
    path:str=Field(min_length=1,max_length=4096)
    sha256:str=Field(pattern="^[a-f0-9]{64}$")
    version:str=Field(min_length=1,max_length=80)


class ActivateVersion(Revision):
    sha256:str|None=Field(default=None,pattern="^[a-f0-9]{64}$")


class Download(Revision):
    version:str=Field(min_length=1,max_length=80)
    integrity:str=Field(max_length=100)


class CompanyPolicy(Revision):
    required:bool


def register_workflow_kernels(app:FastAPI,data_dir):
    root="/api/workflow-kernels"
    @app.get(root)
    def available():
        return settings.catalog(data_dir)

    @app.put(root+"/{engine}/route")
    def save(engine:str,body:KernelRoute):
        settings.save_route(data_dir,engine,body.revision,body.model_dump(exclude={"revision"}))
        return available()

    @app.post(root+"/{engine}/versions")
    def install(engine:str,body:InstallVersion):
        settings.install(data_dir,engine,body.revision,body.path,body.sha256,body.version)
        return available()

    @app.put(root+"/{engine}/active")
    def activate(engine:str,body:ActivateVersion):
        settings.activate(data_dir,engine,body.revision,body.sha256)
        return available()

    @app.get(root+"/{engine}/release")
    def lookup(engine:str):
        from packages.dynamic_workflow.kernel_updates import release
        return release(engine)

    @app.post(root+"/{engine}/download")
    def download(engine:str,body:Download):
        from packages.dynamic_workflow.kernel_updates import download as apply_update
        apply_update(data_dir,engine,body.revision,body.version,body.integrity)
        return available()

    @app.put(root+"/company-policy")
    def company(body:CompanyPolicy):
        settings.set_company(data_dir,body.revision,body.required)
        return available()
