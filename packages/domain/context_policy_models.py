"""Policy defaults are configuration, never source or execution grants."""
from typing import Literal
from pydantic import Field, StrictBool, StrictInt
from packages.domain.questions import RequestModel, Id


DEFAULT_POLICY = {'capture': True, 'retrieve': True, 'archive_mode': 'AUTO_REVIEW', 'char_budget': 8000}


class Policy(RequestModel):
    capture: StrictBool | None = None
    retrieve: StrictBool | None = None
    archive_mode: Literal['MANUAL', 'AUTO_REVIEW', 'AUTO'] | None = None
    char_budget: StrictInt | None = Field(default=None, ge=0, le=16000)


class GlobalPolicyChange(RequestModel):
    operation_id: Id
    expected_revision: StrictInt = Field(ge=0)
    policy: Policy = Field(default_factory=Policy)
