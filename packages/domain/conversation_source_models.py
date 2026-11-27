"""Exact original message locations; no stored conversation text or tool grants."""
from typing import Annotated, Literal
from pydantic import Field, StrictInt, model_validator
from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.discovery_schema import RevisionRef

Alias = Annotated[str, Field(pattern=r'^(native|original|discovery):[a-f0-9]{64}$')]


class MessageEventRef(RequestModel):
    seq: StrictInt = Field(ge=1, le=9007199254740991)
    sha256: Hash


class MessagePartEventRef(MessageEventRef):
    part_id: str = Field(pattern=r'^prt_[A-Za-z0-9_-]{1,100}$')


class ConversationSourceAnchor(RequestModel):
    kind: Literal['NATIVE_USER', 'NATIVE_ASSISTANT', 'NATIVE_GUIDANCE', 'ORIGINAL_USER',
        'ORIGINAL_MESSAGE', 'ORIGINAL_PART', 'DISCOVERY_STATEMENT', 'DISCOVERY_ANSWER', 'DISCOVERY_REVIEW']
    source_alias_id: Alias
    original_project_id: Id | None = None
    role: Literal['user', 'assistant']
    message_key: str = Field(min_length=1, max_length=320)
    source_sha256: Hash
    operation_id: Id | None = None
    guidance_operation_id: Id | None = None
    original_operation_id: Id | None = None
    generation: StrictInt | None = Field(default=None, ge=1)
    session_ref: RevisionRef | None = None
    original_session_id: Id | None = None
    native_message_id: Id | None = None
    info_event: MessageEventRef | None = None
    part_events: list[MessagePartEventRef] = Field(default_factory=list, max_length=64)
    object_ref: RevisionRef | None = None
    history_index: StrictInt | None = Field(default=None, ge=0, le=1000000)
    answer_id: Id | None = None

    @model_validator(mode='after')
    def exact_location(self):
        if len({part.part_id for part in self.part_events}) != len(self.part_events):
            raise ValueError('Original message part locations must be unique')
        if self.kind.startswith('NATIVE_') and not self.operation_id:
            raise ValueError('Select the original native input operation')
        if self.kind == 'NATIVE_GUIDANCE' and not all((self.guidance_operation_id, self.original_operation_id,
                self.session_ref, self.original_session_id, self.generation)):
            raise ValueError('Select the exact guidance owner and parent operation')
        if self.kind == 'ORIGINAL_USER' and not all((self.operation_id, self.session_ref)):
            raise ValueError('Select the exact original user operation')
        if self.kind in {'ORIGINAL_MESSAGE','ORIGINAL_PART'} and not all((self.session_ref, self.original_session_id,
                self.native_message_id, self.info_event, self.part_events)):
            raise ValueError('Select original info and text-part event locations')
        if self.kind == 'ORIGINAL_PART' and len(self.part_events) != 1:
            raise ValueError('Select exactly one original text-part event')
        if self.kind.startswith('DISCOVERY_') and not self.object_ref:
            raise ValueError('Select the original immutable discovery revision')
        if self.kind == 'DISCOVERY_STATEMENT' and self.history_index is None:
            raise ValueError('Select the exact original history item')
        if self.kind == 'DISCOVERY_ANSWER' and not self.answer_id:
            raise ValueError('Select the exact original answer')
        expected = 'assistant' if self.kind in {'NATIVE_ASSISTANT','DISCOVERY_REVIEW','ORIGINAL_MESSAGE','ORIGINAL_PART'} else 'user'
        if self.role != expected:
            raise ValueError('Original source role differs')
        common = {'kind','source_alias_id','original_project_id','role','message_key','source_sha256'}
        extra = {
            'NATIVE_USER': {'operation_id'}, 'NATIVE_ASSISTANT': {'operation_id'},
            'NATIVE_GUIDANCE': {'operation_id','guidance_operation_id','original_operation_id','generation','session_ref','original_session_id'},
            'ORIGINAL_USER': {'operation_id','session_ref'},
            'ORIGINAL_MESSAGE': {'session_ref','original_session_id','native_message_id','info_event','part_events'},
            'ORIGINAL_PART': {'session_ref','original_session_id','native_message_id','info_event','part_events'},
            'DISCOVERY_STATEMENT': {'object_ref','history_index'}, 'DISCOVERY_ANSWER': {'object_ref','answer_id'},
            'DISCOVERY_REVIEW': {'object_ref'},
        }[self.kind]
        if any(getattr(self, key) not in (None, []) for key in type(self).model_fields if key not in common|extra):
            raise ValueError('Original location fields belong to another source kind')
        return self
