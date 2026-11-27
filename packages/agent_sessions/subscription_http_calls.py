"""Reference-only DTO for one pre-forward original subscription HTTP attempt."""
from typing import Literal
from pydantic import Field,model_serializer
from packages.domain.questions import Hash
from .model_authority import ModelAuthorityCall


class SubscriptionHTTPCall(ModelAuthorityCall):
    schema_tag:Literal['eeas.model-authority-http-hop.v1']=Field(alias='schema')
    hop:int=Field(ge=1,le=1048576,strict=True)
    plan_sha256:Hash
    request_sha256:Hash
    request_bytes:int=Field(default=0,ge=0,le=4194304,strict=True)

    @model_serializer(mode='wrap')
    def preserve_legacy_hop(self,handler):
        value=handler(self)
        if 'request_bytes' not in self.model_fields_set:value.pop('request_bytes',None)
        return value

