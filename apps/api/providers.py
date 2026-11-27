"""User-selected custom API/model configuration for native OpenCode planning."""
from packages.domain.provider_settings import ProviderInput,ProviderSettings


def register_provider_routes(app,store,data_dir,source_root):
    if hasattr(app.state,'provider_settings'):return app.state.provider_settings
    service=ProviderSettings(store,data_dir,source_root)
    app.state.provider_settings=service

    @app.get('/api/planning/providers')
    def providers():return {'providers':service.list(),'subscription_auth':'SEPARATE_NATIVE_OPENCODE_OAUTH','silent_paid_fallback':False}

    @app.post('/api/planning/providers',status_code=201)
    def save(body:ProviderInput):return service.save(body)

    @app.get('/api/planning/providers/operations/{operation_id}')
    def operation(operation_id:str):return service.operation(operation_id)

    @app.get('/api/planning/providers/{profile_id}')
    def get(profile_id:str,revision:int|None=None):return service.get(profile_id,revision)
    return service
