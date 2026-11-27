from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class RequirementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Key
    text: Text
    source_refs: list[SourceRef] = Field(default_factory=list, max_length=20)


class AcceptanceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Key
    text: Text
    requirement_ids: list[Key] = Field(min_length=1, max_length=100)


class RequirementBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["eeas.requirements.v1"]
    title: str = Field(min_length=1, max_length=200)
    requirements: list[RequirementItem] = Field(min_length=1, max_length=100)
    assumptions: list[Text] = Field(default_factory=list, max_length=100)
    questions: list[Text] = Field(default_factory=list, max_length=100)
    acceptance: list[AcceptanceItem] = Field(min_length=1, max_length=100)
    confirmed: bool = False
    origin: Literal["manual", "mock", "live_suggestion"] = "manual"

    @model_validator(mode="after")
    def consistent_mapping(self):
        keys = {r.id for r in self.requirements}
        if len(keys) != len(self.requirements) or len({a.id for a in self.acceptance}) != len(self.acceptance):
            raise ValueError("Requirement and acceptance IDs must be unique")
        if any(set(a.requirement_ids) - keys for a in self.acceptance):
            raise ValueError("Acceptance references an unknown requirement ID")
        if any(not r.text.strip() for r in self.requirements):
            raise ValueError("Requirements cannot be blank")
        return self
