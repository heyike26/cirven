from typing import Literal
from pathlib import PurePosixPath, PureWindowsPath
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequirementBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=4000)
    requirement_refs: list[RequirementBinding] = Field(min_length=1, max_length=10)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    allowed_write_paths: list[str] = Field(default_factory=lambda: ["candidate.txt"], min_length=1, max_length=20)
    mock_behavior: Literal["success", "failure", "wait_for_cancel", "repairable", "persistent_failure"] = "success"
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_steps: int = Field(default=5, ge=1, le=20)
    max_repairs: int = Field(default=3, ge=0, le=3)
    approval_required: bool = False
    approval_ttl_seconds: int = Field(default=300, ge=1, le=600)

    @field_validator("goal")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Goal cannot be blank or contain null bytes")
        return value.strip()

    @field_validator("allowed_write_paths")
    @classmethod
    def safe_paths(cls, values):
        for value in values:
            path = PurePosixPath(value)
            if not value or not path.parts or path.is_absolute() or any(x in path.parts for x in ("..", ".")) or any(x in value for x in ("\\", ":", "\x00", "*", "?")):
                raise ValueError("Write paths must be explicit relative files without traversal")
            if str(path) != value or len(value) > 200:
                raise ValueError("Write paths must be canonical relative files")
            if any(PureWindowsPath(part).is_reserved() or part.endswith((".", " ")) for part in path.parts):
                raise ValueError("Windows device names and trailing dots/spaces are not allowed")
            if path.parts[0].casefold() in {"frozen-inputs.json", "stdout.txt", "stderr.txt"}:
                raise ValueError("Controller inputs and process logs are protected")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("Write paths must be unique on Windows as well as Linux")
        return values


class StartRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
