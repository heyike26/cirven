"""Exact session/generation inputs. Imported skills do not grant execution rights."""
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator, model_serializer

from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.native_tools import MCPToolSelection
from packages.domain.conversation_threads import ConversationRef
from packages.domain.native_input_attachments import NativeInputAttachment
from packages.domain.conversation_file_input import ConversationFileInput


def operation_request(value):
    """Keep legacy operation hashes unchanged when new context is absent."""
    result = value.model_dump()
    if result.get('conversation_ref') is None:
        result.pop('conversation_ref', None)
    if not result.get('input_attachments'):
        result.pop('input_attachments', None)
    if result.get('file_input') is None:
        result.pop('file_input', None)
    if result.get('context_selection_id') is None:
        result.pop('context_selection_id', None)
    if result.get('original_work_ref') is None:
        result.pop('original_work_ref', None)
    return result


class SessionRef(RequestModel):
    id: Id
    revision: int = Field(default=1, ge=1, le=1, strict=True)
    sha256: Hash


class ConfigurationRef(RequestModel):
    id: Id
    revision: int = Field(ge=1, strict=True)
    sha256: Hash


class WorkspaceRef(RequestModel):
    id: Hash
    revision: int = Field(ge=1, strict=True)
    binding_sha256: Hash


class OpenSession(RequestModel):
    operation_id: Id
    conversation_ref: ConversationRef | None = None
    workspace_ref: WorkspaceRef | None = None
    provider_profile_ref: ConfigurationRef
    workflow_ref: ConfigurationRef | None = None
    workflow_provider_refs: list[ConfigurationRef] = Field(default_factory=list, max_length=16)
    capability_binding_sha256: Hash
    title: str = Field(default="EEAS conversation", max_length=200)
    ttl_seconds: int = Field(default=600, ge=30, le=3600, strict=True)
    max_calls: int = Field(default=24, ge=1, le=128, strict=True)
    max_output_tokens: int = Field(default=4096, ge=64, le=16384, strict=True)
    allowed_effects: list[Literal["PROCESS_START", "REMOTE_MCP_CONNECT", "BACKGROUND_COMMAND", "WORKSPACE_WRITE"]] = Field(default_factory=list, max_length=4)
    command_allowlist: list[str] = Field(default_factory=list, max_length=16)
    # None preserves legacy inferred literal paths; [] explicitly captures no files.
    command_observation_paths: list[str] | None = Field(default=None, max_length=32)
    mcp_tool_allowlist: list[str] = Field(default_factory=list, max_length=64)
    mcp_tool_selection: list[MCPToolSelection] | None = Field(default=None, max_length=64)
    network_mode: Literal["AS_CONFIGURED", "STRICT_OFFLINE"] = "AS_CONFIGURED"

    @model_serializer(mode='wrap')
    def serialize_observation_scope(self, handler):
        value = handler(self)
        # Old open/configuration/continuation hashes must remain byte-identical,
        # including nested ConfigureConversation dumps and excluded-field dumps.
        if value.get('command_observation_paths') is None:
            value.pop('command_observation_paths', None)
        return value

    @field_validator('command_observation_paths', mode='before')
    @classmethod
    def observation_paths(cls, values):
        if values is None:
            return None
        from packages.adapters.opencode_command_observation import declared_paths
        return declared_paths(values)

    @field_validator("allowed_effects", "command_allowlist", "mcp_tool_allowlist")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Scope entries must be unique")
        return value

    @field_validator("command_allowlist")
    @classmethod
    def literal_commands(cls, value):
        for command in value:
            if not command.strip() or len(command) > 1024 or any(ord(c) < 32 or c in "*?[]" for c in command):
                raise ValueError("Commands must be literal single-line strings without permission globs")
        return value

    @field_validator("mcp_tool_allowlist")
    @classmethod
    def names(cls, value):
        import re
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", name) for name in value):
            raise ValueError("MCP tools must be exact original registry names")
        return value


class OpenConversation(OpenSession):
    project_id: Id | None = None


class LoadSession(RequestModel):
    operation_id: Id
    session_ref: SessionRef
    expected_generation: int | None = Field(default=None, ge=1, strict=True)


class SessionControl(RequestModel):
    operation_id: Id
    session_ref: SessionRef
    generation: int = Field(ge=1, strict=True)


class InterruptSession(SessionControl):
    # Omission retains the existing session-level interrupt contract.  IDE
    # clients must freeze the exact operation they mean to stop.
    expected_active_operation_id: Id | None = None


class OriginalWorkRef(RequestModel):
    root_operation_id: Id
    root_sha256: Hash


class SendMessage(SessionControl):
    raw_text: str = Field(min_length=1, max_length=16000)
    agent: Literal["plan", "build", "workflow"] = "build"
    timeout_seconds: int = Field(default=300, ge=10, le=300, strict=True)
    conversation_ref: ConversationRef | None = None
    input_attachments: list[NativeInputAttachment] = Field(default_factory=list, max_length=20)
    file_input: ConversationFileInput | None = None
    context_selection_id: Hash | None = None
    original_work_ref: OriginalWorkRef | None = None


class PendingRef(RequestModel):
    id: str = Field(pattern=r"^(?:que|per)_[A-Za-z0-9_-]{1,100}$")
    sha256: Hash
    generation: int = Field(ge=1, strict=True)


class PendingControl(SessionControl):
    request_ref: PendingRef


class QuestionReply(PendingControl):
    answers: list[list[str]] = Field(min_length=1, max_length=16)

    @field_validator("answers")
    @classmethod
    def bounded(cls, value):
        if any(len(answer) > 32 or any(not item.strip() or len(item) > 2000 for item in answer) for answer in value):
            raise ValueError("Question answers exceed their bound")
        return value


class PermissionReply(PendingControl):
    reply: Literal["once", "always", "reject"]


class ReviewFileLinkRef(ConfigurationRef):
    project_id: Id
    id: Hash


class ReviewPlanBasisRef(RequestModel):
    kind: Literal['REPORT_PLAN']
    file_link_ref: ReviewFileLinkRef
    proposal_ref: ConfigurationRef
    source_snapshot_sha256: Hash
    candidate_sha256: Hash


class ReviewBuiltinToolRef(ConfigurationRef):
    id: Literal['planning_acceptance_coverage']
    revision: int = Field(default=1, ge=1, le=1, strict=True)


class ReviewBuiltinSelection(RequestModel):
    kind: Literal['BUILTIN_READONLY']
    tool_ref: ReviewBuiltinToolRef


class ReviewMCPDraft(RequestModel):
    capability_binding_sha256: Hash
    items: list[MCPToolSelection] = Field(min_length=1, max_length=1)


class ReviewUserMCPSelection(RequestModel):
    kind: Literal['USER_MCP']
    mcp_tool_selection: ReviewMCPDraft


class NativeReviewSelection(RequestModel):
    skill_ref: ConfigurationRef | None = None
    tool_selection: Annotated[ReviewBuiltinSelection | ReviewUserMCPSelection, Field(discriminator='kind')] | None = None
    capability_binding_sha256: Hash
    basis_ref: ReviewPlanBasisRef | None = None

    @model_validator(mode='after')
    def bounded_selection(self):
        if self.skill_ref is None and self.tool_selection is None:
            raise ValueError('Select a Skill or an admitted read-only tool')
        if isinstance(self.tool_selection, ReviewBuiltinSelection) and self.basis_ref is None:
            raise ValueError('The built-in acceptance check requires its exact report plan basis')
        if isinstance(self.tool_selection, ReviewUserMCPSelection) and self.tool_selection.mcp_tool_selection.capability_binding_sha256 != self.capability_binding_sha256:
            raise ValueError('Tool and capability bindings differ')
        if len(self.model_dump_json().encode('utf-8')) > 8192:
            raise ValueError('Review selection exceeds 8 KiB')
        return self


class NativeReviewInput(RequestModel):
    version: int = Field(ge=1, le=1, strict=True)
    selection: NativeReviewSelection
    process_start_consent: bool = Field(default=False, strict=True)


class NativeCapabilityTaskSelection(RequestModel):
    capability_binding_sha256: Hash
    skill_ref: ConfigurationRef | None = None
    mcp_tool_selection: list[MCPToolSelection] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def explicit_selection(self):
        if self.skill_ref is None and not self.mcp_tool_selection:
            raise ValueError('Select an exact Skill or at least one exact MCP tool')
        names = [item.tool_id for item in self.mcp_tool_selection]
        if len(names) != len(set(names)):
            raise ValueError('Selected MCP tools must be unique')
        servers = {}
        for item in self.mcp_tool_selection:
            source = (item.config_ref.model_dump_json(), item.probe_ref.model_dump_json())
            if item.server_id in servers and servers[item.server_id] != source:
                raise ValueError('Selected tools on one server must share the exact configuration and probe')
            servers[item.server_id] = source
        if len(self.model_dump_json().encode('utf-8')) > 96 * 1024:
            raise ValueError('Capability task selection exceeds 96 KiB')
        return self


class NativeCapabilityTaskInput(RequestModel):
    version: int = Field(ge=2, le=2, strict=True)
    kind: Literal['CAPABILITY_TASK']
    selection: NativeCapabilityTaskSelection
    process_start_consent: bool = Field(default=False, strict=True)
    remote_mcp_connect_consent: bool = Field(default=False, strict=True)
    task_root_operation_id: Id | None = None


class ReviewControlInput(RequestModel):
    operation_id: Id
    expected_review_ref: ConfigurationRef


class ReviewGuidanceInput(ReviewControlInput):
    raw_text: str = Field(min_length=1, max_length=16000)


class OrdinaryGuidanceInput(RequestModel):
    operation_id: Id
    expected_native_message_ref: ConfigurationRef
    raw_text: str = Field(min_length=1, max_length=16000)
    intent: Literal["AUTO", "STEER", "STATUS"] = "AUTO"


class ReviewPermissionInput(ReviewControlInput):
    request_ref: PendingRef
    reply: Literal['once', 'reject']


class ReviewQuestionInput(ReviewControlInput):
    request_ref: PendingRef
    answers: list[list[str]] = Field(min_length=1, max_length=16)

    @field_validator('answers')
    @classmethod
    def bounded(cls, value):
        return QuestionReply.bounded(value)


class ReviewQuestionRejectInput(ReviewControlInput):
    request_ref: PendingRef
