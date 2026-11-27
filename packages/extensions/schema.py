from pathlib import PurePosixPath, PureWindowsPath
import json
from pydantic import BaseModel, ConfigDict, Field, field_validator


def safe_member(name):
    path = PurePosixPath(name)
    if not name or str(path) != name or path.is_absolute() or not path.parts or any(p in {".", ".."} for p in path.parts):
        return False
    if len(name) > 200 or any(c in name for c in '\\:*?<>|"') or any(ord(c) < 32 for c in name):
        return False
    return not any(PureWindowsPath(p).is_reserved() or p.endswith((".", " ")) for p in path.parts)


class ExtensionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = Field(pattern=r"^eeas\.extension\.v1$")
    provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,80}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
    adapter: str = Field(max_length=100)
    source_kind: str = Field(pattern=r"^(synthetic|public)$")
    entrypoint: str = Field(max_length=200)
    capabilities: list[str] = Field(min_length=1, max_length=20)
    permissions: list[str] = Field(max_length=20)
    parameter_schema: dict
    native_parameters: dict = Field(default_factory=dict)

    @field_validator('parameter_schema', 'native_parameters')
    @classmethod
    def json_parameters(cls, value):
        # Preserve new namespaces, but reject non-JSON numbers before a candidate
        # can poison the registry's API/GUI responses.
        json.dumps(value, allow_nan=False)
        return value


def fixture_manifest(version):
    return {"schema_version": "eeas.extension.v1", "provider": "eeas.fixture.formatter", "version": str(version),
            "adapter": "trusted-original-python-formatter", "source_kind": "synthetic", "entrypoint": "plugin.py",
            "capabilities": ["text.format"] + (["text.length"] if str(version) == "2" else []),
            "permissions": ["text.format"] + (["text.length"] if str(version) == "2" else []),
            "parameter_schema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 4000}}, "required": ["text"], "additionalProperties": False},
            "native_parameters": {"observable_format": "prefix" if str(version) == "1" else "json-unicode-length"}}
