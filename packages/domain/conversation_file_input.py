"""Explicit saved-file input on the public original loop; never write authority."""
from typing import Literal

from pydantic import Field, model_validator

from packages.domain.local_workspaces import FileSource
from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.store import Conflict


class ConversationFileLink(RequestModel):
    project_id: Id
    id: Hash
    revision: int = Field(ge=1, strict=True)
    sha256: Hash

class ConversationFileInput(RequestModel):
    kind: Literal['LINKED_FILE', 'WORKSPACE_FILE']
    link_ref: ConversationFileLink | None = None
    source_ref: FileSource | None = None

    @model_validator(mode='after')
    def one_explicit_source(self):
        if ((self.kind == 'LINKED_FILE') != (self.link_ref is not None)
                or (self.kind == 'WORKSPACE_FILE') != (self.source_ref is not None)):
            raise ValueError('Choose one exact saved-file source')
        return self


def freeze_conversation_file(files, project_id, selected):
    """Observe once for this operation, using existing immutable snapshots."""
    if selected is None:
        return None
    value = ConversationFileInput.model_validate(selected)
    expected = value.source_ref.model_dump() if value.source_ref is not None else None
    source = expected if expected is not None else value.link_ref.model_dump()
    if source['project_id'] != project_id:
        raise Conflict('Selected file belongs to another execution project; input retained')
    if expected is not None:
        registered = files.register_workspace(project_id, expected, {'kind': 'USER_SELECTED_CONVERSATION_FILE'})
        reference = registered['link_ref']
    else:
        reference = source
    filename = files.get(reference)['filename']
    snapshot = files.observe(reference)
    if expected is not None and snapshot['snapshot_file_source_ref'] != expected:
        raise Conflict('Selected file version changed; refresh it before sending')
    if snapshot['parsed']['status'] != 'AVAILABLE':
        raise Conflict('Selected file could not be read; input retained without a model call')
    return {'schema': 'eeas.original-file-input.v1', 'filename': filename,
        'link_ref': reference, 'snapshot_ref': snapshot['snapshot_ref'],
        'file_source_ref': snapshot['snapshot_file_source_ref'],
        'sha256': snapshot['current_sha256'], 'parsed': snapshot['parsed'],
        'coverage': snapshot['coverage'], 'unsaved_changes': 'NOT_OBSERVED',
        'write_authorized': False}
