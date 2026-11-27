"""Logical discussion references and per-discussion retrieval preferences."""
from typing import Literal
from pydantic import Field, StrictBool, StrictInt
from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.conversation_threads import ConversationRef


class ConversationLinkChange(RequestModel):
    operation_id: Id
    scope_id: Id
    conversation_ref: ConversationRef
    expected_revision: StrictInt = Field(ge=0)
    state: Literal['CANDIDATE', 'ACTIVE', 'REJECTED', 'RETRACTED'] = 'ACTIVE'
    manual_locked: StrictBool = True
    allow_reference_to_other_project: StrictBool = False
    navigation_only: StrictBool = False
    preference_expected_revision: StrictInt | None = Field(default=None, ge=0)


class ConversationSettingsChange(RequestModel):
    operation_id: Id
    conversation_ref: ConversationRef
    expected_revision: StrictInt = Field(ge=0)
    preferred_scope_id: Id | None = None
    retrieval_mode: Literal['AUTO', 'MANUAL', 'OFF'] = 'AUTO'
    include_global: StrictBool = False
    # A temporary override may restrict retrieval/capture and lower the byte
    # envelope. It does not turn off parent source exclusions or add grants.
    retrieve: StrictBool | None = None
    capture: StrictBool | None = None
    char_budget: StrictInt | None = Field(default=None, ge=0, le=16000)


class AutomaticContextInput(RequestModel):
    operation_id: Id
    project_id: Id | None = None
    conversation_id: str | None = Field(default=None, pattern=r'^(native|original|discovery):[a-f0-9]{64}$')
    input_sha256: Hash
    text_sha256: Hash
