"""Explicit single-file acts through the original OpenCode loop and safe CAS.

No discovery/plan acceptance gate, another model loop, shell execution, software
installation or automatic workspace panel is introduced.
"""
import asyncio
import difflib
import json
import re
from typing import Literal
import uuid

from pydantic import Field, model_validator

from packages.domain.discovery_schema import RevisionRef
from packages.domain.document_chunks import chunks
from packages.domain.local_materials import LocalMaterials
from packages.domain.local_workspaces import FileEditInput, FileSource, MAX_EDIT_BYTES, canonical, digest, now, sha
from packages.domain.planning_schema import PlanningBudget
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, InvalidInput, NotFound


class DirectBudget(PlanningBudget):
    max_calls: int = Field(default=1, ge=1, le=1, strict=True)


class DirectActionInput(RequestModel):
    operation_id: Id
    instruction: str = Field(min_length=1, max_length=16000)
    file_ref: FileSource | None = None
    user_requested_write: bool = Field(default=False, strict=True)
    provider_profile_ref: RevisionRef | None = None
    budget: DirectBudget = Field(default_factory=DirectBudget)


class InlineQuestion(RequestModel):
    id: Id
    text: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)
    choices: list[str] = Field(default_factory=list, max_length=5)


class DirectCandidate(RequestModel):
    outcome: Literal['EDIT', 'NO_CHANGE', 'NEEDS_INPUT']
    summary: str = Field(min_length=1, max_length=4000)
    content: str | None = Field(default=None, max_length=MAX_EDIT_BYTES)
    questions: list[InlineQuestion] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def complete_outcome(self):
        if self.outcome == 'EDIT':
            if self.content is None or self.questions or '\x00' in self.content or len(self.content.encode()) > MAX_EDIT_BYTES:
                raise ValueError('EDIT needs complete bounded UTF-8 content and no unresolved question')
        elif self.content is not None:
            raise ValueError('Only EDIT returns file content')
        if (self.outcome == 'NEEDS_INPUT') != bool(self.questions):
            raise ValueError('NEEDS_INPUT requires actual inline questions; other outcomes cannot silently leave gaps')
        return self


class DirectActions:
    def __init__(self, store, data_dir, workspaces, transport):
        self.store, self.ws, self.transport = store, workspaces, transport
        self.active = {}

    def _path(self, pid, operation_id, *parts):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise InvalidInput('Invalid direct action operation identity')
        return self.ws._data(pid, None, 'direct-actions', digest(operation_id), *parts)

    def _put(self, path, value):
        self.ws._write(path, {'payload': value, 'sha256': digest(value)})

    def _read(self, path):
        value = self.ws._json(path)
        try:
            if not isinstance(value['payload'], dict) or digest(value['payload']) != value['sha256']:
                raise ValueError
            return value['payload']
        except (KeyError, TypeError, ValueError):
            raise Conflict('Direct action record integrity failed') from None

    def _event(self, pid, op, kind, **fields):
        self._put(self._path(pid, op, 'events', uuid.uuid4().hex + '.json'),
                  {'kind': kind, 'created_at': now(), **fields})

    @staticmethod
    def _native_op(pid, op):
        return 'direct-' + digest([pid, op])[:40]

    def _native(self, pid, op):
        with self.store.connect() as db:
            row = db.execute('SELECT id FROM agent_executions WHERE project_id=? AND operation_id=?',
                             (pid, self._native_op(pid, op))).fetchone()
        return self.transport.inspect(pid, row['id']) if row else None

    def _finish(self, pid, op, result):
        value = {'project_id': pid, 'operation_id': op, **result}
        path = self._path(pid, op, 'result.json')
        if path.exists():
            return self.get(pid, op, replayed=True)
        self._put(path, value)
        self._event(pid, op, 'DIRECT_ACTION_' + result['status'])
        return self.get(pid, op)

    def _question(self, pid, op, ident, text, reason):
        return self._finish(pid, op, {'status': 'NEEDS_INPUT', 'interaction': {'mode': 'INLINE_QUESTION',
            'questions': [{'id': ident, 'text': text, 'reason': reason, 'choices': []}]},
            'before_ref': None, 'after_ref': None, 'model_calls': 0})

    def get(self, pid, op, *, replayed=False):
        self.store.project(pid)
        claim = self._path(pid, op, 'request.json')
        if not claim.exists():
            raise NotFound('Direct action does not exist in this project')
        saved = self._read(claim)
        if saved['project_id'] != pid or saved['operation_id'] != op or digest(saved['request']) != saved['request_sha256']:
            raise Conflict('Direct action scope changed')
        path = self._path(pid, op, 'result.json')
        if path.exists():
            value = self._read(path)
            if (value['project_id'], value['operation_id']) != (pid, op):
                raise Conflict('Direct action result scope changed')
            cancellation_path = self._path(pid, op, 'cancel-receipt.json')
            if (value['status'] == 'UNKNOWN_OUTCOME' and value.get('error', {}).get('type') == 'CancelledError' and
                    cancellation_path.exists() and not self._path(pid, op, 'edit-request.json').exists()):
                cancellation = self._read(cancellation_path)
                native = self._native(pid, op)
                if native and cancellation == {k: native[k] for k in ['id', 'execution_status', 'session_id']} and native['execution_status'] == 'CANCELLED':
                    # Preserve the earlier provisional immutable record while a
                    # later actual owned-process receipt resolves its outcome.
                    value = {**value, 'status': 'CANCELLED', 'provisional_outcome': 'UNKNOWN_OUTCOME',
                             'native_cancel': cancellation, 'cancellation_effect': 'OWNED_ORIGINAL_PROCESS_STOPPED_NO_CAS_WRITE'}
            if value.get('before_ref') and value['before_ref'] != saved['request']['file_ref']:
                raise Conflict('Direct action result differs from the exact selected source')
            if value.get('after_ref'):
                after = FileSource.model_validate(value['after_ref']).model_dump()
                before = saved['request']['file_ref']
                if (not saved['request']['user_requested_write'] or not before or
                        any(after[key] != before[key] for key in ['kind', 'project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path'])):
                    raise Conflict('Direct action saved result exceeds its exact file write authorization')
            ref = value.get('after_ref') or value.get('before_ref')
            current = None
            error = None
            if ref:
                try: current = self.ws.check_sources(pid, [FileSource.model_validate(ref)])[0]['status'] == 'CURRENT'
                except (Conflict, InvalidInput, NotFound, OSError) as exc:
                    current = False; error = {'type': type(exc).__name__, 'message': str(exc)}
            return {**value, 'replayed': replayed, 'current': current, 'current_error': error}
        native = self._native(pid, op)
        status = 'PROCESSING' if (pid, op) in self.active else 'UNKNOWN_OUTCOME'
        if self._path(pid, op, 'cancel-request.json').exists():
            status = 'CANCEL_REQUESTED' if status == 'PROCESSING' or (native and native['execution_status'] in {'QUEUED', 'RUNNING', 'CANCEL_REQUESTED'}) else 'UNKNOWN_OUTCOME'
        return {'project_id': pid, 'operation_id': op, 'status': status, 'replayed': replayed,
                'interaction': {'mode': 'NONE', 'questions': []},
                'execution': {k: native[k] for k in ['id', 'execution_status', 'session_id']} if native else None,
                'automatic_retry': False}

    def events(self, pid, op):
        self.get(pid, op)
        folder = self._path(pid, op, 'events')
        values = [self._read(path) for path in folder.glob('*.json')] if folder.exists() else []
        native = self._native(pid, op)
        return {'events': sorted(values, key=lambda row: row['created_at']),
                'native_events': self.transport.stream_events(pid, native['id']) if native else []}

    def _source(self, pid, op, body):
        ref = body.file_ref
        if ref.project_id != pid:
            raise InvalidInput('Selected file belongs to another project')
        binding = self.ws.get(pid, ref.workspace_id, ref.binding_revision)
        if binding['binding_sha256'] != ref.binding_sha256:
            raise Conflict('Selected file workspace binding changed')
        value = self.ws.read(pid, ref.workspace_id, ref.path, ref.revision)
        head = self.ws._version(binding, ref.path)
        if value['source_ref'] != ref.model_dump() or not value['current'] or head['revision'] != ref.revision:
            raise Conflict('SOURCE_DRIFT: direct action requires the exact current selected FileSource')
        if value['encoding'] != 'utf-8' or value['byte_length'] > MAX_EDIT_BYTES:
            raise InvalidInput('Direct single-file editing supports selected UTF-8 text up to 1 MiB')
        raw = value['content'].encode()
        if sha(raw) != ref.sha256:
            raise Conflict('Selected UTF-8 source bytes differ from the original FileSource SHA')
        snapshot = self.store.save_document(pid, '直接动作原文件-' + digest([pid, op])[:24] + '.txt', raw,
                                           'direct-action-selected-file-snapshot:' + digest(ref.model_dump()))
        doc = self.store.document(pid, snapshot['id'], snapshot['revision'])
        receipt = {'documents': [{'document_id': doc['id'], 'revision': doc['revision'], 'raw_sha256': doc['sha256'],
            'parsed_sha256': sha(doc['content'].encode()), 'filename': doc['filename'], 'source': doc['source'],
            'text_or_selected_chunks': doc['content'], 'chunks': chunks(doc['content']),
            'local_file_source_ref': ref.model_dump(), 'original_selected_path': ref.path}], 'coverage': 'COMPLETE_SELECTED_UTF8_FILE'}
        receipt['sha256'] = digest(receipt)
        frozen = {'before_ref': ref.model_dump(), 'before_content': value['content'], 'source_receipt': receipt,
                  'workspace_ref': {'id': binding['id'], 'revision': binding['revision'], 'binding_sha256': binding['binding_sha256']}}
        self._put(self._path(pid, op, 'source.json'), frozen)
        return frozen

    def _apply(self, pid, op, body, source, execution):
        candidate = DirectCandidate.model_validate(execution['candidate_json'])
        base = {'before_ref': source['before_ref'], 'after_ref': None, 'execution': execution,
                'model_calls': execution['usage']['rpc_attempts'], 'summary': candidate.summary,
                'summary_origin': 'MODEL_SUGGESTION', 'semantic_verification': 'USER_REVIEW_REQUIRED',
                'interaction': {'mode': 'NONE', 'questions': []}}
        cancel_requested = self._path(pid, op, 'cancel-request.json').exists()
        if cancel_requested:
            state = None
            if self._path(pid, op, 'edit-request.json').exists():
                try: state = self.ws.operation(pid, body.file_ref.workspace_id, 'direct-edit-' + digest([pid, op])[:40])
                except NotFound: pass
            if state is None or state['status'] not in {'APPLIED', 'UNKNOWN_OUTCOME'}:
                return self._finish(pid, op, {**base, 'status': 'CANCELLED', 'write_performed': False})
            # A previous known atomic write is reconciled before reporting
            # cancellation; cancellation never pretends to undo saved bytes.
        if candidate.outcome == 'NEEDS_INPUT':
            return self._finish(pid, op, {**base, 'status': 'NEEDS_INPUT', 'write_performed': False,
                'interaction': {'mode': 'INLINE_QUESTION', 'questions': [q.model_dump() for q in candidate.questions]}})
        if candidate.outcome == 'NO_CHANGE' or candidate.content == source['before_content']:
            return self._finish(pid, op, {**base, 'status': 'NO_CHANGE', 'write_performed': False})
        diff = ''.join(difflib.unified_diff(source['before_content'].splitlines(keepends=True),
            candidate.content.splitlines(keepends=True), fromfile=body.file_ref.path, tofile=body.file_ref.path))
        if not body.user_requested_write:
            return self._finish(pid, op, {**base, 'status': 'READY_NOT_AUTHORIZED', 'diff': diff,
                'write_performed': False, 'authorization': 'EXACT_USER_FILE_WRITE_NOT_REQUESTED'})
        edit = FileEditInput(operation_id='direct-edit-' + digest([pid, op])[:40], path=body.file_ref.path,
            expected_revision=body.file_ref.revision, expected_sha256=body.file_ref.sha256, content=candidate.content)
        path = self._path(pid, op, 'edit-request.json')
        if path.exists():
            if self._read(path)['edit'] != edit.model_dump():
                raise Conflict('Frozen direct edit request changed')
        else:
            self._put(path, {'edit': edit.model_dump(), 'user_requested_write': True, 'before_ref': source['before_ref']})
        self._event(pid, op, 'CAS_EDIT_REQUESTED', path=body.file_ref.path)
        result = self.ws.edit(pid, body.file_ref.workspace_id, edit)
        if result['source_ref']['sha256'] != sha(candidate.content.encode()):
            raise Conflict('Actual edited source does not match the complete original CLI output')
        return self._finish(pid, op, {**base, 'status': 'APPLIED', 'after_ref': result['source_ref'],
            'diff': diff, 'edit_result': result, 'write_performed': True,
            'authorization': 'EXPLICIT_USER_REQUEST_FOR_THIS_EXACT_FILE', 'after_sha256': sha(candidate.content.encode()),
            'cancellation_effect': 'ALREADY_APPLIED_NO_ROLLBACK' if cancel_requested else None})

    async def run(self, pid, body):
        self.store.project(pid)
        op = body.operation_id; key = (pid, op)
        request = body.model_dump(exclude={'operation_id'}); request_hash = digest(request)
        with LocalMaterials(self.ws)._lock(self._path(pid, op, 'lock')):
            claim = self._path(pid, op, 'request.json')
            if claim.exists():
                saved = self._read(claim)
                if saved['request_sha256'] != request_hash:
                    raise Conflict('IDEMPOTENCY_CONFLICT: direct action already binds another file/instruction/authorization')
                if self._path(pid, op, 'result.json').exists() or key in self.active:
                    return self.get(pid, op, replayed=True)
                if not self._path(pid, op, 'native-result.json').exists():
                    return self.get(pid, op, replayed=True)
            else:
                self._put(claim, {'project_id': pid, 'operation_id': op, 'request': request, 'request_sha256': request_hash})
            self.active[key] = asyncio.current_task()
        try:
            if body.file_ref is None:
                return self._question(pid, op, 'select-file', '请选中需要处理的文件。', '此动作需要精确文件目标，不能猜测当前软件或路径。')
            if body.provider_profile_ref is None:
                return self._question(pid, op, 'select-model', '请选择已配置的原 OpenCode 模型连接。', '没有配置不会暗切其他付费模型。')
            source_path = self._path(pid, op, 'source.json')
            source = self._read(source_path) if source_path.exists() else self._source(pid, op, body)
            if (source['before_ref'] != body.file_ref.model_dump() or sha(source['before_content'].encode()) != body.file_ref.sha256 or
                    source['source_receipt']['sha256'] != digest({k: source['source_receipt'][k] for k in ['documents', 'coverage']})):
                raise Conflict('Frozen direct source differs from the exact request bytes/scope')
            if body.user_requested_write:
                binding = self.ws.get(pid, body.file_ref.workspace_id, body.file_ref.binding_revision)
                if not any(s == '*' or s == body.file_ref.path or (s.endswith('/') and body.file_ref.path.startswith(s))
                           for s in binding['allowed_write_paths']):
                    return self._question(pid, op, 'file-write-grant', '此文件所在工作区目前只读，请授权这个文件的写入范围。',
                                          '用户意图不能扩大已选工作区的真实写入授权。')
            native_path = self._path(pid, op, 'native-result.json')
            if native_path.exists():
                execution = self._read(native_path)
            else:
                prompt = ('执行一个明确的单文件编辑请求，无需需求发现或计划确认；不能执行shell或修改任何其他文件。'
                    '输出完整修改后UTF-8文本，不省略未改部分。缺必要信息返回NEEDS_INPUT和具体问题；不猜测或伪造完成。'
                    '返回内容是建议，只有服务端核用户对此精确文件的写授权与原始版本后才能CAS保存。\n'
                    '精确目标：' + canonical(source['before_ref']) + '\n用户指令：' + body.instruction)
                native_request = {'project_id': pid, 'operation_id': self._native_op(pid, op), 'prompt': prompt,
                    'workspace_ref': source['workspace_ref'], 'source_receipt': source['source_receipt'],
                    'provider_profile_ref': body.provider_profile_ref.model_dump(), 'budget': body.budget.model_dump(),
                    'timeout_seconds': body.budget.timeout_seconds, 'needs_tools': False,
                    'output_schema': DirectCandidate.model_json_schema()}
                self._put(self._path(pid, op, 'native-request.json'), native_request)
                self._event(pid, op, 'ORIGINAL_OPENCODE_REQUESTED', before_ref=source['before_ref'])
                execution = await self.transport.propose(native_request)
                self._put(native_path, execution)
            if execution['status'] != 'RETURNED' or execution['source_receipt_sha256'] != source['source_receipt']['sha256']:
                return self._finish(pid, op, {'status': execution['status'] if execution['status'] in {'CANCELLED', 'UNKNOWN_OUTCOME'} else 'FAILED',
                    'interaction': {'mode': 'NONE', 'questions': []}, 'before_ref': source['before_ref'], 'after_ref': None,
                    'execution': execution, 'write_performed': False, 'error': {'type': 'OriginalOpenCodeOutcome', 'message': execution['status']}})
            with LocalMaterials(self.ws)._lock(self._path(pid, op, 'lock')):
                return self._apply(pid, op, body, source, execution)
        except asyncio.CancelledError:
            native = self._native(pid, op)
            if native and native['execution_status'] in {'QUEUED', 'RUNNING', 'CANCEL_REQUESTED'} and self._path(pid, op, 'cancel-request.json').exists():
                return {**self.get(pid, op), 'status': 'CANCEL_REQUESTED', 'write_performed': False}
            return self._finish(pid, op, {'status': 'CANCELLED' if native and native['execution_status'] == 'CANCELLED' else 'UNKNOWN_OUTCOME',
                'interaction': {'mode': 'NONE', 'questions': []}, 'write_performed': False,
                'before_ref': body.file_ref.model_dump() if body.file_ref else None, 'after_ref': None,
                'error': {'type': 'CancelledError', 'message': 'Original owned operation cancelled; interrupted effects are not guessed'}})
        except Exception as error:
            failure = {'type': type(error).__name__, 'message': str(error), 'created_at': now()}
            self._put(self._path(pid, op, 'failures', uuid.uuid4().hex + '.json'), failure)
            self._event(pid, op, 'DIRECT_ACTION_FAILED', error=failure)
            # Preserve recoverable partial edit claims. Explicit same-input retry
            # may reconcile known saved output via existing edit idempotency;
            # it can never dispatch another model for an unknown outcome.
            if self._path(pid, op, 'edit-request.json').exists():
                edit = self._read(self._path(pid, op, 'edit-request.json'))['edit']
                begun = self.ws._data(pid, body.file_ref.workspace_id, 'operations', digest(edit['operation_id']), 'write-begun.json')
                if begun.exists():
                    return {**self.get(pid, op), 'status': 'UNKNOWN_OUTCOME', 'write_performed': None, 'error': failure}
            return self._finish(pid, op, {'status': 'FAILED', 'interaction': {'mode': 'NONE', 'questions': []},
                'before_ref': body.file_ref.model_dump() if body.file_ref else None, 'after_ref': None,
                'write_performed': False, 'error': failure})
        finally:
            self.active.pop(key, None)

    async def cancel(self, pid, op):
        value = self.get(pid, op)
        if value['status'] not in {'PROCESSING', 'CANCEL_REQUESTED', 'UNKNOWN_OUTCOME'}:
            return {**value, 'cancellation_effect': 'ALREADY_COMPLETED_NO_ROLLBACK'}
        with LocalMaterials(self.ws)._lock(self._path(pid, op, 'lock')):
            path = self._path(pid, op, 'cancel-request.json')
            if not path.exists():
                self._put(path, {'requested_at': now(), 'rollback': False})
                self._event(pid, op, 'CANCEL_REQUESTED')
        native = self._native(pid, op)
        receipt = await self.transport.request_cancel(pid, native['id']) if native else None
        if receipt and receipt['execution_status'] == 'CANCELLED' and not self._path(pid, op, 'edit-request.json').exists():
            saved = self._read(self._path(pid, op, 'request.json'))
            cancellation = {k: receipt[k] for k in ['id', 'execution_status', 'session_id']}
            path = self._path(pid, op, 'cancel-receipt.json')
            if not path.exists():
                self._put(path, cancellation)
                self._event(pid, op, 'ORIGINAL_CANCELLED_OBSERVED', native_cancel=cancellation)
            self._finish(pid, op, {'status': 'CANCELLED', 'before_ref': saved['request']['file_ref'], 'after_ref': None,
                'interaction': {'mode': 'NONE', 'questions': []}, 'write_performed': False,
                'native_cancel': cancellation, 'cancellation_effect': 'OWNED_ORIGINAL_PROCESS_STOPPED_NO_CAS_WRITE'})
        return {**self.get(pid, op), 'native_cancel': {k: receipt[k] for k in ['id', 'execution_status', 'session_id']} if receipt else None}
