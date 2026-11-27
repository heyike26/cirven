"""File-version feedback for text, documents and images, without mutation authority.

Screen annotations are user descriptions; they cannot identify or authorize a
file. The caller must supply an actual selected LocalWorkspaces FileSource.
"""
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_materials import LocalMaterials
from packages.domain.local_workspaces import FileSource, MAX_EDIT_BYTES, MAX_FILE_BYTES, canonical, digest, relative, sha
from packages.domain.paths import resolve_data_path
from packages.domain.questions import Hash, Id, RequestModel, stamp
from packages.domain.store import Conflict, InvalidInput, NotFound


class DescribedSelection(RequestModel):
    kind: Literal['USER_DESCRIPTION']
    text: str = Field(min_length=1, max_length=4000)


class TextSelection(RequestModel):
    kind: Literal['TEXT_RANGE']
    char_start: int = Field(ge=0, strict=True)
    char_end: int = Field(gt=0, strict=True)
    quote_sha256: Hash


class ExactQuoteSelection(RequestModel):
    kind: Literal['EXACT_QUOTE']
    quote: str = Field(min_length=1, max_length=4000, strict=True)

    @field_validator('quote')
    @classmethod
    def original_utf8(cls, value):
        # Whitespace is meaningful source text, unlike a human description.
        try:
            value.encode('utf-8')
        except UnicodeError as exc:
            raise ValueError('Text must be valid UTF-8') from exc
        return value


Selection = Annotated[DescribedSelection | TextSelection | ExactQuoteSelection, Field(discriminator='kind')]


class NormalizedRegion(RequestModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    y: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    width: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)
    height: float = Field(gt=0, le=1, allow_inf_nan=False, strict=True)

    @model_validator(mode='after')
    def contained(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError('Normalized region must stay inside its annotated frame')
        return self


class NativeFeedbackInput(RequestModel):
    operation_id: Id
    file_source_ref: FileSource
    comment: str = Field(min_length=1, max_length=16000)
    mode: Literal['DRAFT', 'SUBMITTED'] = 'DRAFT'
    selection: Selection | None = None
    normalized_regions: list[NormalizedRegion] = Field(default_factory=list, max_length=64)
    context_ref: dict[str, JsonValue] | None = None
    frame_ref: dict[str, JsonValue] | None = None

    @model_validator(mode='after')
    def bounded(self):
        relative(self.file_source_ref.path)
        if len(canonical(self.model_dump()).encode()) > 256 * 1024:
            raise ValueError('Feedback metadata exceeds 256 KiB; split it explicitly')
        if self.normalized_regions and self.frame_ref is None:
            raise ValueError('Normalized screen regions need their exact attached frame reference')
        return self


class DirectActionLinkInput(RequestModel):
    operation_id: Id
    feedback_ref: RevisionRef
    direct_operation_id: Id


class NativeFeedback:
    BINARY_SUFFIXES = {'.docx', '.dotx', '.pptx', '.xlsx', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.vsdx', '.xmind', '.zip'}

    def __init__(self, store, workspaces, *, direct_actions=None):
        self.store, self.ws, self.direct = store, workspaces, direct_actions
        self.materials = LocalMaterials(workspaces)

    def _path(self, pid, *parts):
        self.store.project(pid)
        return resolve_data_path('/'.join(('native-feedback', pid, *parts)), self.ws.data_dir)

    def _read(self, path):
        wrapped = self.ws._json(path)
        value = wrapped.get('payload')
        if not isinstance(value, dict) or digest(value) != wrapped.get('sha256'):
            raise Conflict('Native feedback record integrity failed')
        return value

    def _put(self, path, payload):
        record = {'payload': payload, 'sha256': digest(payload)}
        if len((canonical(record) + '\n').encode()) > 1024 * 1024:
            raise InvalidInput('Native feedback record exceeds 1 MiB; original records remain unchanged')
        self.ws._write(path, record)

    def _claim(self, pid, body, kind):
        folder = self._path(pid, 'operations', digest(body.operation_id))
        request = {'project_id': pid, 'operation_id': body.operation_id, 'kind': kind, 'input': body.model_dump()}
        request_sha = digest(request)
        path = folder / 'request.json'
        if path.exists():
            if self._read(path)['request_sha256'] != request_sha:
                raise Conflict('IDEMPOTENCY_CONFLICT: native feedback operation binds another input')
        else:
            self._put(path, {**request, 'request_sha256': request_sha, 'created_at': stamp()})
        return folder, request_sha

    def _baseline(self, pid, ref):
        if ref.project_id != pid:
            raise InvalidInput('Feedback file belongs to another project')
        binding = self.ws.get(pid, ref.workspace_id, ref.binding_revision)
        if binding['binding_sha256'] != ref.binding_sha256:
            raise Conflict('Feedback workspace revision/SHA differs from its actual binding')
        version = self.ws._version(binding, ref.path, ref.revision)
        if not version or self.ws._ref(version) != ref.model_dump():
            raise Conflict('Feedback needs an actual frozen FileSource revision and SHA')
        path = self.ws._versions(binding, ref.path) / f'{ref.revision:010d}' / 'bytes'
        if path.stat().st_size > MAX_FILE_BYTES:
            raise Conflict('Frozen file snapshot exceeds the workspace size bound')
        raw = path.read_bytes()
        if sha(raw) != ref.sha256:
            raise Conflict('Frozen source snapshot bytes changed')
        return raw, self.ws._content(raw)

    def _current(self, pid, ref):
        value = FileSource.model_validate(ref)
        try:
            binding = self.ws.get(pid, value.workspace_id)
            if binding['revision'] != value.binding_revision or binding['binding_sha256'] != value.binding_sha256:
                return {'status': 'BINDING_CHANGED', 'baseline_source_ref': ref, 'current_source_ref': None,
                    'live_sha256': None, 'automatic_rebind': False}
            raw = self.ws._raw(binding, value.path, missing=True)
            if raw is None:
                return {'status': 'MISSING', 'baseline_source_ref': ref, 'current_source_ref': None,
                    'live_sha256': None, 'automatic_rebind': False}
            observed = self.ws.read(pid, value.workspace_id, value.path)
            return {'status': 'CURRENT' if observed['source_ref'] == ref else 'STALE',
                'baseline_source_ref': ref, 'current_source_ref': observed['source_ref'],
                'live_sha256': observed['live_sha256'], 'automatic_rebind': False}
        except (Conflict, InvalidInput, NotFound, OSError) as error:
            status = 'BINDING_CHANGED' if isinstance(error, Conflict) and 'WORKSPACE_ROOT_DRIFT' in str(error) else 'UNKNOWN'
            return {'status': status, 'baseline_source_ref': ref, 'current_source_ref': None,
                'live_sha256': None, 'reason': type(error).__name__, 'automatic_rebind': False}

    def record(self, pid, body):
        folder = self._path(pid, 'operations', digest(body.operation_id))
        with self.materials._lock(folder / 'lock'):
            folder, request_sha = self._claim(pid, body, 'FEEDBACK')
            ident = digest(['native-feedback', pid, body.operation_id])
            target = self._path(pid, 'records', ident + '.json')
            if target.exists():
                if self._read(target)['request_sha256'] != request_sha:
                    raise Conflict('Native feedback record binds another input')
            else:
                raw, content = self._baseline(pid, body.file_source_ref)
                text_adapted = (content['encoding'] == 'utf-8' and len(raw) <= MAX_EDIT_BYTES and
                    Path(body.file_source_ref.path).suffix.lower() not in self.BINARY_SUFFIXES)
                selection = body.selection.model_dump() if body.selection else None
                selection_status = 'NONE'
                if body.selection:
                    selection_status = 'USER_DESCRIPTION_NOT_SOURCE_VERIFIED'
                    if body.selection.kind == 'TEXT_RANGE':
                        s = body.selection
                        if content['encoding'] != 'utf-8' or not 0 <= s.char_start < s.char_end <= len(content['content']):
                            raise InvalidInput('Text selection needs a range in this actual UTF-8 source snapshot')
                        if sha(content['content'][s.char_start:s.char_end].encode()) != s.quote_sha256:
                            raise Conflict('Selected text quote SHA differs from this exact file version')
                        selection_status = 'TEXT_RANGE_HASH_VERIFIED'
                    elif body.selection.kind == 'EXACT_QUOTE':
                        selection = None
                        selection_status = 'QUOTE_NOT_ADAPTED'
                        if text_adapted:
                            text, quote = content['content'], body.selection.quote
                            start = text.find(quote)
                            if start < 0:
                                selection_status = 'QUOTE_NOT_FOUND'
                            elif text.find(quote, start + 1) >= 0:
                                selection_status = 'QUOTE_AMBIGUOUS'
                            else:
                                selection = {'kind': 'TEXT_RANGE', 'char_start': start,
                                    'char_end': start + len(quote), 'quote_sha256': sha(quote.encode('utf-8'))}
                                selection_status = 'TEXT_RANGE_HASH_VERIFIED'
                payload = {'schema_version': 'eeas.native-file-feedback.v1', 'id': ident, 'revision': 1,
                    'project_id': pid, 'request_sha256': request_sha, 'created_at': stamp(),
                    'baseline_file_source_ref': body.file_source_ref.model_dump(),
                    'baseline_snapshot': {'sha256': sha(raw), 'byte_length': len(raw), 'encoding': content['encoding']},
                    'comment': body.comment, 'mode': body.mode, 'comment_origin': 'USER_STATEMENT',
                    'file_selection': selection,
                    'selection_verification': selection_status,
                    'screen_annotations': {'normalized_regions': [r.model_dump() for r in body.normalized_regions],
                        'context_ref': body.context_ref, 'frame_ref': body.frame_ref,
                        'verification': 'CALLER_ATTACHED_NOT_QUALIFIED', 'object_identity_verified': False,
                        'description_origin': 'USER_STATEMENT', 'grants_file_authority': False},
                    'mutation_status': 'EXISTING_DIRECT_ACTION_AVAILABLE' if text_adapted else 'NOT_ADAPTED',
                    'execution_authorized': False, 'write_authorized': False, 'model_dispatched': False,
                    'semantic_verification': 'USER_REVIEW_REQUIRED'}
                if body.selection and body.selection.kind == 'EXACT_QUOTE':
                    payload['requested_selection'] = body.selection.model_dump()
                self._put(target, payload)
            if not (folder / 'result.json').exists():
                self._put(folder / 'result.json', {'kind': 'FEEDBACK', 'record_id': ident})
            return self.get(pid, ident)

    def _feedback(self, pid, ident):
        value = self._read(self._path(pid, 'records', ident + '.json'))
        if value.get('id') != ident or value.get('project_id') != pid or value.get('schema_version') != 'eeas.native-file-feedback.v1':
            raise Conflict('Native feedback identity/project binding changed')
        return value

    def get(self, pid, ident):
        if not self._path(pid, 'records', ident + '.json').exists():
            raise NotFound('Native feedback does not exist in this project')
        value = self._feedback(pid, ident)
        links = []
        for path in sorted(self._path(pid, 'links', ident).glob('*.json')):
            saved = self._read(path)
            links.append(self._link_view(pid, saved))
        return {'status': 'RECORDED', 'feedback_ref': {'id': ident, 'revision': 1, 'sha256': digest(value)},
            'payload': value, 'current_file_check': self._current(pid, value['baseline_file_source_ref']),
            'direct_action_links': links, 'historical_feedback_preserved': True,
            'mutation_status': value['mutation_status'], 'execution_authorized': False}

    def _link_view(self, pid, value):
        reference = {'id': value['id'], 'revision': 1, 'sha256': digest(value)}
        action = value['action_receipt']
        current = self._current(pid, action.get('after_ref') or action['before_ref'])
        return {'link_ref': reference, 'payload': value, 'current_file_check': current}

    def link(self, pid, ident, body):
        if ident != body.feedback_ref.id:
            raise InvalidInput('Route feedback ID differs from its exact reference')
        feedback = self._feedback(pid, ident)
        if body.feedback_ref.revision != 1 or body.feedback_ref.sha256 != digest(feedback):
            raise Conflict('Direct action link needs the exact immutable feedback version')
        folder = self._path(pid, 'operations', digest(body.operation_id))
        with self.materials._lock(folder / 'lock'):
            folder, request_sha = self._claim(pid, body, 'DIRECT_ACTION_LINK')
            link_id = digest(['native-feedback-link', pid, body.operation_id])
            target = self._path(pid, 'links', ident, link_id + '.json')
            if target.exists():
                saved = self._read(target)
                if saved['request_sha256'] != request_sha:
                    raise Conflict('Saved direct action link binds another input')
            else:
                if self.direct is None:
                    raise Conflict('Original DirectActions service is unavailable')
                if feedback['mutation_status'] != 'EXISTING_DIRECT_ACTION_AVAILABLE':
                    raise Conflict('Binary or unsupported source feedback has no qualified mutation adapter')
                action = self.direct.get(pid, body.direct_operation_id)
                if action.get('before_ref') != feedback['baseline_file_source_ref']:
                    raise Conflict('Real direct action before_ref differs from this feedback baseline')
                if action['status'] in {'PROCESSING', 'CANCEL_REQUESTED', 'UNKNOWN_OUTCOME'}:
                    raise Conflict('Direct action outcome is not yet qualified for a final feedback link')
                if action.get('after_ref'):
                    after = FileSource.model_validate(action['after_ref'])
                    before = feedback['baseline_file_source_ref']
                    if any(after.model_dump()[k] != before[k] for k in ('project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path')):
                        raise Conflict('Actual action after_ref exceeds the feedback file identity')
                    self._baseline(pid, after)
                receipt = {k: action.get(k) for k in ('project_id', 'operation_id', 'status', 'before_ref', 'after_ref',
                    'write_performed', 'authorization', 'semantic_verification', 'summary_origin', 'model_calls')}
                execution = action.get('execution')
                if execution:
                    receipt['execution_ref'] = {k: execution[k] for k in ('execution_id', 'status', 'session_id') if k in execution}
                saved = {'schema_version': 'eeas.native-feedback-action-link.v1', 'id': link_id,
                    'project_id': pid, 'revision': 1, 'request_sha256': request_sha, 'created_at': stamp(),
                    'feedback_ref': body.feedback_ref.model_dump(), 'action_receipt': receipt,
                    'link_semantics': 'VERSION_PROVENANCE_ONLY_NOT_SEMANTIC_ACCEPTANCE',
                    'feedback_granted_write_authority': False, 'model_dispatched': False}
                self._put(target, saved)
            if not (folder / 'result.json').exists():
                self._put(folder / 'result.json', {'kind': 'DIRECT_ACTION_LINK', 'record_id': ident, 'link_id': link_id})
            return self._link_view(pid, saved)

    def operation(self, pid, op):
        folder = self._path(pid, 'operations', digest(op))
        if not (folder / 'request.json').exists():
            raise NotFound('Native feedback operation does not exist in this project')
        if not (folder / 'result.json').exists():
            return {'status': 'INCOMPLETE_REVIEW_REQUIRED', 'operation_id': op, 'automatic_retry': False,
                'request': self._read(folder / 'request.json')}
        result = self._read(folder / 'result.json')
        if result['kind'] == 'FEEDBACK':
            return self.get(pid, result['record_id'])
        return self._link_view(pid, self._read(self._path(pid, 'links', result['record_id'], result['link_id'] + '.json')))
