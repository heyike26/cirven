"""Exact directory scopes; neither a path prefix nor a file read/write grant."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt, StrictStr, model_validator
from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.local_workspaces import relative

ConversationId = Annotated[str, Field(pattern=r'^(native|original|discovery):[a-f0-9]{64}$')]


class FolderRef(RequestModel):
    project_id: Id
    workspace_id: Hash
    binding_revision: StrictInt = Field(ge=1)
    binding_sha256: Hash
    segments: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(default_factory=list, max_length=128)
    device: StrictInt | StrictStr
    inode: StrictInt | StrictStr

    @model_validator(mode='after')
    def exact_directory(self):
        if any('/' in part for part in self.segments):
            raise ValueError('Directory components must be separate')
        relative('/'.join(self.segments), empty=True)
        if any(type(value) is int and value < 0 or isinstance(value, str) and len(value) > 256
               for value in (self.device, self.inode)):
            raise ValueError('Directory identity is outside its bounded format')
        return self


class FolderSelection(RequestModel):
    project_id: Id
    workspace_id: Hash
    binding_revision: StrictInt = Field(ge=1)
    binding_sha256: Hash
    segments: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(default_factory=list, max_length=128)

    @model_validator(mode='after')
    def relative_selection(self):
        if any('/' in part for part in self.segments):
            raise ValueError('Directory components must be separate')
        relative('/'.join(self.segments), empty=True)
        return self


class FolderRuleChange(RequestModel):
    operation_id: Id
    scope_id: Id
    conversation_id: ConversationId | None = None
    expected_revision: StrictInt = Field(ge=0)
    folder_ref: FolderRef
    range: Literal['CURRENT_DIRECTORY', 'INCLUDING_DESCENDANTS'] = 'CURRENT_DIRECTORY'
    state: Literal['ACTIVE', 'CANDIDATE', 'REJECTED', 'RETRACTED'] = 'ACTIVE'


class FileExclusionRef(RequestModel):
    id: Hash
    revision: StrictInt = Field(ge=1)
