"""One approved classic Notepad append through the existing original task loop.

Construct this service instead of NativeTasks, never beside it. Main provides
trusted gesture/activation callbacks; absent reviewed qualification or handoff
fails closed before model or SDK input. No window activation occurs here.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import time
from typing import Literal

from fastapi import Depends, Header
from pydantic import Field, model_validator

from apps.api.native_tasks import NativeTasks, NativeTaskInput, NativeTaskRevokeInput
from packages.adapters.native_append import SCOPE, text_sha, utf16_length
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import canonical, digest
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, NotFound


class AppendSpan(RequestModel):
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, gt=0)
    quote: str = Field(min_length=1, max_length=512)

    @model_validator(mode='after')
    def ordered(self):
        if self.start >= self.end:
            raise ValueError('Payload span must be nonempty')
        return self


class AppendPayloadSource(RequestModel):
    kind: Literal['RAW_INSTRUCTION', 'ORIGINAL_REPLY']
    span: AppendSpan
    reply_ref: RevisionRef | None = None

    @model_validator(mode='after')
    def exact_source(self):
        if (self.kind == 'ORIGINAL_REPLY') != (self.reply_ref is not None):
            raise ValueError('Only original reply payloads require a reply reference')
        return self


class AppendPrepareInput(RequestModel):
    operation_id: Id
    context_ref: RevisionRef
    raw_text: str = Field(min_length=1, max_length=16000)
    payload_source: AppendPayloadSource
    provider_profile_ref: RevisionRef | None = None


class AppendApproveInput(RequestModel):
    operation_id: Id
    preview_ref: RevisionRef
    expected_intent_epoch: int = Field(strict=True, ge=1)
    expected_input_epoch: int = Field(strict=True, ge=1)
    main_gesture_ref: RevisionRef


class AppendStartInput(RequestModel):
    operation_id: Id
    preview_ref: RevisionRef
    approval_ref: RevisionRef


class AppendGetInput(RequestModel):
    operation_id: Id | None = None
    context_ref: RevisionRef | None = None


class NotepadAppendTasks(NativeTasks):
    def __init__(self, app, *, companion, inspector, qualification_reader, runtime_root,
                 gesture_reader=None, trusted_activator=None, reply_reader=None, transport=None, clock=None):
        self.inspector, self.reviewed_qualification = inspector, qualification_reader
        self.runtime_root = Path(runtime_root)
        self.gesture_reader, self.trusted_activator, self.reply_reader = gesture_reader, trusted_activator, reply_reader
        self.clock = clock or time.time
        self.last_reason = 'QUALIFICATION_NOT_CONFIGURED'
        super().__init__(app, companion=companion, qualification_reader=self._qualification,
            authorizer=self._append_authorizer, policy_writer=self._append_policy,
            result_checker=self._append_result, foreground_checker=self._foreground, transport=transport)

    def _append_path(self, kind, ident):
        return self.authority._path('notepad-append', kind, ident + '.json')

    def _append_put(self, kind, value):
        ref = {'id': kind + '-' + digest(value)[:40], 'revision': 1, 'sha256': digest(value)}
        path = self._append_path(kind, ref['id'])
        if path.exists():
            if self.authority._read(path, authority=True) != value:
                raise Conflict('Immutable append receipt changed')
        else:
            self.authority._write(path, value)
        return ref

    def _append_read(self, kind, reference):
        ref = RevisionRef.model_validate(reference).model_dump()
        value = self.authority._read(self._append_path(kind, ref['id']), authority=True)
        if digest(value) != ref['sha256'] or ref['revision'] != 1:
            raise Conflict('Append receipt reference changed')
        return value

    def _append_op(self, ident, *, historical=False):
        path = self._append_path('operations', ident)
        if not path.exists():
            raise NotFound('Append operation has not been prepared')
        value = self.authority._read(path, authority=not historical)
        if not historical and value['generation'] != self.intents.generation:
            raise Conflict('Historical append approval cannot authorize input after restart')
        return value

    def _append_save(self, ident, value):
        self.authority._write(self._append_path('operations', ident), value)

    def _snapshot(self, context_ref):
        context = self.authority.context(context_ref)
        window = self.authority.contexts[context_ref['id']]['window']
        if not callable(getattr(self.inspector, 'read', None)):
            raise Conflict('ACTUAL_WINDOWS_NOTEPAD_INSPECTOR_UNAVAILABLE')
        value = self.inspector.read(copy.deepcopy(window))
        if (not isinstance(value, dict) or value.get('identity_sha256') != window['identity_sha256']
                or any(value.get(k) != window[k] for k in ('window_id', 'pid', 'process_creation'))
                or value.get('text_sha256') != text_sha(value.get('text', ''))
                or value.get('utf16_length') != utf16_length(value.get('text', ''))):
            raise Conflict('Actual Notepad snapshot identity/text differs from its source')
        return context, value

    def _qualification(self, context):
        try:
            _, snapshot = self._snapshot(context['context_ref'])
            value = self.reviewed_qualification.read(snapshot) if callable(getattr(self.reviewed_qualification, 'read', None)) else None
            if value is not None:
                factory = self.adapter().factory
                if (Path(getattr(factory, 'root', '')).absolute() != self.runtime_root.absolute()
                        or getattr(factory, 'manifest_ref', None) != value['sdk_component_manifest_ref']):
                    raise Conflict('Existing original Cua controller differs from reviewed SDK/runtime')
            self.last_reason = None if value is not None else (getattr(self.reviewed_qualification, 'reason', None) or 'QUALIFICATION_NOT_CONFIGURED')
            return value
        except Exception as error:
            self.last_reason = str(error)[:300]
            return None

    @staticmethod
    def _end(snapshot, *, foreground=False):
        if (type(snapshot.get('selection_start')) is not int or type(snapshot.get('selection_end')) is not int
                or snapshot['selection_start'] != snapshot['selection_end']
                or snapshot['selection_end'] != snapshot['utf16_length']):
            raise Conflict('APPEND_REQUIRES_COLLAPSED_CARET_AT_UTF16_END')
        if snapshot.get('focused_edit') is not True or snapshot.get('gui_idle') is not True:
            raise Conflict('NOTEPAD_EDIT_FOCUS_OR_MODAL_STATE_CHANGED')
        if foreground and (snapshot.get('foreground_window_id') != snapshot['window_id']
                or snapshot.get('modifiers_released') is not True):
            raise Conflict('EXACT_FOREGROUND_AND_RELEASED_MODIFIERS_REQUIRED')

    def _preview_current(self, preview, *, foreground=False):
        if (preview['generation'] != self.intents.generation or preview['expires_unix_ms'] <= int(self.clock() * 1000)
                or preview['host_scope'] != self.authority.get()['host_scope']):
            raise Conflict('Append preview expired or belongs to another host generation')
        _, snapshot = self._snapshot(preview['request']['context_ref'])
        qualification = self.authority.qualification(preview['request']['context_ref'])
        if qualification is None or qualification['qualification_ref'] != preview['qualification_ref']:
            raise Conflict('Reviewed append source/runtime qualification changed')
        # Closure hashing can take time. Read the actual buffer again after it,
        # so the worker guard does not use an observation made before hashing.
        _, snapshot = self._snapshot(preview['request']['context_ref'])
        baseline = preview['baseline']
        if any(snapshot.get(k) != baseline[k] for k in ('identity_sha256', 'pid', 'process_creation',
                'window_id', 'edit_window_id', 'text_sha256', 'utf16_length', 'bounds')):
            raise Conflict('Notepad buffer/window changed since the approved preview')
        self._end(snapshot, foreground=foreground)
        return snapshot, qualification

    def append_get(self, ident=None, context_ref=None):
        value = {'adapter_id': 'classic_notepad_append', 'state': 'NOT_ADAPTED', 'reason': self.last_reason,
            'scope': SCOPE, 'permission_state': 'NONE', 'activation_state': 'HANDOFF_REQUIRED',
            'foreground_required': True, 'no_selection_overwrite': True, 'no_save_action': True,
            'automatic_retry': False, 'native_input_global_enabled': False,
            'input_epoch': self.authority.get().get('input', {}).get('epoch'),
            'expected_intent_epoch': self.intents.state['epoch'], 'task_completed': False}
        if context_ref is not None:
            try:
                context_ref = RevisionRef.model_validate(context_ref).model_dump()
                _, snapshot = self._snapshot(context_ref)
                qualification = self.authority.qualification(context_ref)
                if qualification:
                    value.update(state='ADAPTED_REQUIRES_APPROVAL', reason=None,
                        qualification_ref=qualification['qualification_ref'], target_label=snapshot.get('title', 'Notepad'))
                else: value['reason'] = self.last_reason
            except Exception as error: value['reason'] = str(error)[:300]
        if ident is not None:
            op = self._append_op(ident, historical=True)
            preview = self.authority._read(self._append_path('previews', op['preview_ref']['id']), authority=False)
            value.update(state=op['status'], preview_ref=op['preview_ref'], payload_text=preview['payload_text'],
                payload_sha256=preview['payload_sha256'], qualification_ref=preview['qualification_ref'],
                baseline_ref=preview['baseline_ref'],
                target_label=preview['target_label'], task_operation_id=ident,
                permission_state='APPROVED' if op.get('approval_ref') else 'NONE',
                activation_state=op.get('activation_state', 'HANDOFF_REQUIRED'),
                approval_ref=op.get('approval_ref'), handoff_ref=op.get('handoff_ref'),
                provider_state='PROFILE_SELECTED_UNVALIDATED' if preview['request']['provider_profile_ref'] else 'NOT_CONFIGURED')
            if op['generation'] != self.intents.generation:
                value.update(state='HISTORICAL_READONLY', permission_state='EXPIRED', activation_state='HANDOFF_REQUIRED')
            if self._path(ident).exists():
                task = self.get(ident)
                value.update(state=task['status'] if op['generation'] == self.intents.generation else 'HISTORICAL_READONLY',
                    permission_state='CONSUMED' if op['generation'] == self.intents.generation else 'EXPIRED',
                    task_status=task['status'], task_completed=task.get('task_completed', False),
                    task=task)
        return value

    def prepare_append(self, body, token):
        body = AppendPrepareInput.model_validate(body).model_dump()
        self.authority.check_host(token)
        with self.authority.lock:
            if self.closed: raise Conflict('Append service closed')
            path = self._append_path('operations', body['operation_id'])
            if path.exists():
                prior = self._append_op(body['operation_id'])
                preview = self._append_read('previews', prior['preview_ref'])
                if preview['request'] != body: raise Conflict('Append operation already binds another immutable payload')
                return self.append_get(body['operation_id'])
            context, snapshot = self._snapshot(body['context_ref'])
            qualification = self.authority.qualification(body['context_ref'])
            if qualification is None:
                return {**self.append_get(context_ref=body['context_ref']), 'reason': self.last_reason}
            self._end(snapshot)
            source = body['payload_source']; span = source['span']
            if source['kind'] == 'RAW_INSTRUCTION':
                source_text, source_ref = body['raw_text'], None
            else:
                if not callable(self.reply_reader): raise Conflict('Trusted original reply payload reader is not adapted')
                reply = self.reply_reader(copy.deepcopy(source['reply_ref']))
                if not isinstance(reply, dict) or reply.get('reply_ref') != source['reply_ref'] or not isinstance(reply.get('text'), str):
                    raise Conflict('Original reply payload is not an exact persisted source')
                source_text, source_ref = reply['text'], source['reply_ref']
            text = source_text[span['start']:span['end']]
            limit = qualification['limits']
            if text != span['quote'] or span['end'] > len(source_text):
                raise Conflict('Approved payload span does not match its actual source')
            if (not text or any(ord(c) < 32 or ord(c) == 127 for c in text)
                    or utf16_length(text) > limit['max_payload_utf16_units']
                    or snapshot['utf16_length'] + utf16_length(text) > limit['max_buffer_utf16_units']):
                raise Conflict('Payload exceeds reviewed single append Unicode/buffer limits')
            baseline = {k: snapshot[k] for k in ('identity_sha256', 'window_id', 'pid', 'process_creation',
                'edit_window_id', 'text_sha256', 'utf16_length', 'bounds')}
            baseline['text'] = snapshot['text']
            preview = {'request': body, 'generation': self.intents.generation,
                'host_scope': self.authority.get()['host_scope'], 'surface_ref': context['surface_ref'],
                'input_epoch': context['input_epoch'], 'intent_epoch': self.intents.state['epoch'],
                'qualification_ref': qualification['qualification_ref'], 'baseline': baseline,
                'baseline_ref': self._append_put('baselines', baseline),
                'payload_text': text, 'payload_sha256': text_sha(text), 'payload_source_ref': source_ref,
                'target_label': snapshot.get('title', 'Notepad'), 'scope': SCOPE,
                'expires_unix_ms': int(self.clock() * 1000) + 300000}
            ref = self._append_put('previews', preview)
            self._append_save(body['operation_id'], {'generation': self.intents.generation,
                'preview_ref': ref, 'status': 'PREPARED', 'approval_ref': None, 'handoff_ref': None})
            return self.append_get(body['operation_id'])

    def approve_append(self, body, token):
        body = AppendApproveInput.model_validate(body).model_dump()
        self.authority.check_host(token)
        with self.authority.lock:
            op = self._append_op(body['operation_id'])
            if op['preview_ref'] != body['preview_ref']: raise Conflict('Approval binds another preview')
            preview = self._append_read('previews', body['preview_ref'])
            self._preview_current(preview)
            if (body['expected_intent_epoch'] != preview['intent_epoch'] or body['expected_intent_epoch'] != self.intents.state['epoch']
                    or body['expected_input_epoch'] != preview['input_epoch']):
                raise Conflict('Append approval epoch changed')
            if op.get('approval_ref'):
                prior = self._append_read('approvals', op['approval_ref'])
                if prior['request'] != body: raise Conflict('Append approval already binds another Main gesture')
                return self.append_get(body['operation_id'])
            if not callable(self.gesture_reader): raise Conflict('TRUSTED_MAIN_USER_GESTURE_REQUIRED')
            gesture = self.gesture_reader(copy.deepcopy(body['main_gesture_ref']))
            expected = {'type': 'MAIN_NATIVE_APPEND_USER_GESTURE', 'operation_id': body['operation_id'],
                'preview_ref': body['preview_ref'], 'host_scope': preview['host_scope'], 'surface_ref': preview['surface_ref'],
                'input_epoch': preview['input_epoch'], 'intent_epoch': preview['intent_epoch'], 'user_activation': True}
            if (not isinstance(gesture, dict) or any(gesture.get(k) != v for k, v in expected.items())
                    or digest(gesture) != body['main_gesture_ref']['sha256']
                    or type(gesture.get('expires_unix_ms')) is not int
                    or not int(self.clock() * 1000) < gesture['expires_unix_ms'] <= int(self.clock() * 1000) + 60000):
                raise Conflict('Main gesture is forged, stale or belongs to another approval')
            approval = {'request': body, 'preview_ref': body['preview_ref'], 'generation': self.intents.generation,
                'permission': 'EXPLICIT_FOREGROUND_CURRENT_CLASSIC_NOTEPAD_APPEND_ONLY_NO_SAVE',
                'payload_sha256': preview['payload_sha256'], 'raw_text_sha256': text_sha(preview['request']['raw_text']),
                'expires_unix_ms': preview['expires_unix_ms']}
            ref = self._append_put('approvals', approval)
            op.update(status='APPROVED', approval_ref=ref)
            self._append_save(body['operation_id'], op)
            return self.append_get(body['operation_id'])

    async def start_append(self, body, token):
        import inspect
        body = AppendStartInput.model_validate(body).model_dump()
        self.authority.check_host(token)
        with self.authority.lock:
            op, preview, approval = self._permit(body['operation_id'])
            if op['preview_ref'] != body['preview_ref'] or op['approval_ref'] != body['approval_ref']:
                raise Conflict('Start binds another approved payload')
            if self._path(body['operation_id']).exists():
                return self.append_get(body['operation_id'])
            if preview['request']['provider_profile_ref'] is None:
                op.update(status='NOT_CONFIGURED', activation_state='HANDOFF_REQUIRED')
                self._append_save(body['operation_id'], op)
                return self.append_get(body['operation_id'])
            if op.get('activation_state') == 'ACTIVATING':
                raise Conflict('Exact foreground handoff already in progress')
            self._preview_current(preview)
            if not callable(self.trusted_activator):
                op.update(status='HANDOFF_REQUIRED', activation_state='HANDOFF_REQUIRED')
                self._append_save(body['operation_id'], op)
                return self.append_get(body['operation_id'])
            op.update(activation_state='ACTIVATING'); self._append_save(body['operation_id'], op)
            ticket = {'operation_id': body['operation_id'], 'approval_ref': body['approval_ref'],
                'preview_ref': body['preview_ref'], 'target': {k: preview['baseline'][k] for k in ('window_id', 'pid', 'process_creation', 'identity_sha256')},
                'surface_ref': preview['surface_ref'], 'host_scope': preview['host_scope'],
                'input_epoch': preview['input_epoch'], 'intent_epoch': preview['intent_epoch'],
                'payload_sha256': preview['payload_sha256'], 'main_gesture_ref': approval['request']['main_gesture_ref']}
        try:
            handoff = self.trusted_activator(copy.deepcopy(ticket))
            if inspect.isawaitable(handoff): handoff = await handoff
            with self.authority.lock:
                op, preview, _ = self._permit(body['operation_id'])
                self._preview_current(preview, foreground=True)
                if (not isinstance(handoff, dict) or handoff.get('status') != 'ACTIVATED'
                        or handoff.get('target_identity_sha256') != preview['baseline']['identity_sha256']
                        or handoff.get('foreground_window_id') != preview['baseline']['window_id']
                        or handoff.get('operation_id') != body['operation_id']
                        or handoff.get('approval_ref') != body['approval_ref']
                        or handoff.get('source') != 'ACTUAL_MAIN_USER_GESTURE_WIN32_HANDOFF'):
                    raise Conflict('Exact Main foreground handoff was refused or unverified')
                handoff['evidence_ref'] = RevisionRef.model_validate(handoff.get('evidence_ref')).model_dump()
                frozen = {'ticket': ticket, 'receipt': handoff, 'generation': self.intents.generation}
                op.update(handoff_ref=self._append_put('handoffs', frozen), activation_state='ACTIVATED', status='STARTING')
                self._append_save(body['operation_id'], op)
        except Exception as error:
            with self.authority.lock:
                op = self._append_op(body['operation_id'])
                op.update(status='HANDOFF_REQUIRED', activation_state='HANDOFF_REQUIRED', reason=str(error)[:300])
                self._append_save(body['operation_id'], op)
            return self.append_get(body['operation_id'])
        request = {k: preview['request'][k] for k in ('operation_id', 'raw_text', 'context_ref', 'provider_profile_ref')}
        request['foreground_permission_ref'] = body['approval_ref']
        await self.submit(request, token)
        return self.append_get(body['operation_id'])

    def _permit(self, ident):
        op = self._append_op(ident)
        if op['status'] == 'REVOKED' or not op.get('approval_ref'):
            raise Conflict('Valid approved append payload is required')
        preview = self._append_read('previews', op['preview_ref'])
        approval = self._append_read('approvals', op['approval_ref'])
        if (approval['preview_ref'] != op['preview_ref'] or approval['generation'] != self.intents.generation
                or approval['payload_sha256'] != preview['payload_sha256']
                or approval['expires_unix_ms'] <= int(self.clock() * 1000)):
            raise Conflict('Approved append payload expired or changed')
        return op, preview, approval

    async def submit(self, body, token):
        request = NativeTaskInput.model_validate(body).model_dump()
        self.authority.check_host(token)
        op, preview, _ = self._permit(request['operation_id'])
        expected = {k: preview['request'][k] for k in ('operation_id', 'raw_text', 'context_ref', 'provider_profile_ref')}
        expected['foreground_permission_ref'] = op['approval_ref']
        if request != expected or not op.get('handoff_ref'):
            raise Conflict('Native submit does not match its approved payload and real handoff')
        if not self._path(request['operation_id']).exists():
            self._preview_current(preview, foreground=True)
        return await super().submit(request, token)

    def _append_authorizer(self, message, interpretation, context):
        ident = message['operation_id']
        op, preview, _ = self._permit(ident)
        self._preview_current(preview, foreground=True)
        candidate = interpretation['candidate']
        span = candidate.get('instruction_span') or {}
        if (message['raw_text'] != preview['request']['raw_text'] or context['context_ref'] != preview['request']['context_ref']
                or candidate.get('intent') != 'ACTION' or candidate.get('allowed_effects') != ['TYPE_TEXT']
                or span.get('start') != 0 or span.get('end') != len(message['raw_text']) or span.get('quote') != message['raw_text']):
            return None
        return {'policy_ref': op['approval_ref'], 'allowed_effects': ['TYPE_TEXT'], 'max_actions': 1, 'ttl_seconds': 300}

    def _active_append(self):
        matches = []
        for path in self._append_path('operations', 'placeholder').parent.glob('*.json'):
            try:
                op = self.authority._read(path, authority=True)
                if op['generation'] == self.intents.generation and op.get('handoff_ref') and op['status'] != 'REVOKED':
                    preview = self._append_read('previews', op['preview_ref'])
                    if preview['request']['context_ref'] == self.active_context and self._path(preview['request']['operation_id']).exists():
                        record = self._read(preview['request']['operation_id'])
                        if record['status'] in {'INTERPRETING', 'RUNNING', 'AWAITING_VERIFICATION'}:
                            matches.append((op, preview))
            except (Conflict, KeyError):
                continue
        if len(matches) != 1:
            raise Conflict('Exactly one approved native append task must own this method')
        return matches[0]

    def _mutation_method(self, qualification):
        op, preview = self._active_append()
        ident = 'native_append_input_' + digest({'qualification_ref': qualification['qualification_ref'], 'preview_ref': op['preview_ref']})[:40]
        if ident not in self.transport.methods:
            descriptor = {'id': ident, 'route': 'ORIGINAL_CUA_APPROVED_APPEND', 'readonly': False,
                'effects': ['TYPE_TEXT'], 'qualification': 'QUALIFIED', 'coverage': SCOPE,
                'input_schema': {'type': 'object', 'additionalProperties': False, 'required': ['operation_id', 'frame_ref', 'action'],
                    'properties': {'operation_id': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                        'frame_ref': RevisionRef.model_json_schema(), 'action': {'type': 'object', 'additionalProperties': False,
                            'required': ['kind', 'text', 'delivery_mode'], 'properties': {'kind': {'const': 'TYPE_TEXT'},
                                'text': {'const': preview['payload_text']}, 'delivery_mode': {'const': 'foreground'}}}}},
                'cost': {'latency': 'SDK_INPUT', 'tokens': 'APPROVED_EXACT_PAYLOAD', 'measured': False},
                'execution': {'can_background': False, 'background_qualification': 'UNQUALIFIED',
                    'requires_foreground_input': True, 'focus_effect': 'REQUIRED'}}
            self.transport.register_method(descriptor, self.input)
        return ident

    def input(self, arguments, binding):
        if not isinstance(arguments, dict) or set(arguments) != {'operation_id', 'frame_ref', 'action'}:
            raise Conflict('Append input has unexpected fields')
        ident, _ = self._bound(binding)
        _, preview, _ = self._permit(ident)
        if arguments.get('action') != {'kind': 'TYPE_TEXT', 'text': preview['payload_text'], 'delivery_mode': 'foreground'}:
            raise Conflict('Input differs from the immutable approved append payload')
        self._preview_current(preview, foreground=True)
        return super().input(arguments, binding)

    @contextmanager
    def control_guard(self, reference, surface_ref, input_epoch, epoch):
        # This runs in the existing SDK worker after queueing, immediately before
        # the original native await. Early RPC checks cannot replace this read.
        value = self.intents._get('inputs', reference)
        grant = self.intents._grant(value['request']['grant_ref'])
        message = self.intents.message(grant['scope']['user_message_ref'])
        ident = message['operation_id']
        _, preview, _ = self._permit(ident)
        if value['request']['action'] != {'kind': 'TYPE_TEXT', 'text': preview['payload_text'], 'delivery_mode': 'foreground'}:
            raise Conflict('Queued action differs from the approved payload')
        with super().control_guard(reference, surface_ref, input_epoch, epoch) as result:
            self._preview_current(preview, foreground=True)
            yield result

    def _foreground(self, row, method, grant_ref):
        try:
            request = json.loads(row['request']) if isinstance(row['request'], str) else row['request']
            op, preview, _ = self._permit(request['operation_id'])
            grant = self.intents._grant(grant_ref)
            record = self._read(request['operation_id'])
            if (not op.get('handoff_ref') or record['request'].get('foreground_permission_ref') != op['approval_ref']
                    or grant['policy']['policy_ref'] != op['approval_ref']
                    or grant['epoch'] != preview['intent_epoch'] + 1
                    or grant['scope']['context_ref'] != preview['request']['context_ref']
                    or grant['execution_ref'] != self._execution(row)):
                return False
            self._append_read('handoffs', op['handoff_ref'])
            self._preview_current(preview, foreground=True)
            return True
        except Exception:
            return False

    def _append_policy(self, request, execution, policy):
        _, preview, _ = self._permit(request['operation_id'])
        _, qualification = self._preview_current(preview, foreground=True)
        body = {'version': 3, 'expires_after': '5m', 'idle_timeout': '5m',
            'allow': {'tools': ['get_window_state', 'type_text']},
            'resources': {'apps': [{'executable': qualification['executable_path'], 'windows': 'all'}],
                'desktop': {'display': False}}}
        from packages.adapters.native_append import safe_policy_folder, checked_bytes
        folder = safe_policy_folder(self.runtime_root)
        path = folder / (digest({'request': request, 'execution': execution, 'policy': policy}) + '.yaml')
        raw = canonical(body).encode()
        if path.exists():
            if path.read_bytes() != raw: raise Conflict('Immutable append SDK policy changed')
        else:
            with path.open('xb') as stream: stream.write(raw)
        ref = {'path': str(path.absolute()), 'sha256': __import__('hashlib').sha256(raw).hexdigest()}
        checked_bytes(self.runtime_root, {'path': path.relative_to(self.runtime_root).as_posix(), 'sha256': ref['sha256']})
        return ref

    def _append_result(self, row, grant):
        try:
            request = json.loads(row['request']) if isinstance(row['request'], str) else row['request']
            _, preview, _ = self._permit(request['operation_id'])
            _, snapshot = self._snapshot(preview['request']['context_ref'])
            qualification = self.authority.qualification(preview['request']['context_ref'])
            observation = self.observe({}, {'request': request, 'execution_ref': self._execution(row),
                'grant_ref': self._read(request['operation_id'])['grant_ref']})
            metadata = observation['sdk_metadata']
            expected = preview['baseline']['text'] + preview['payload_text']
            checks = {'exact_identity': all(snapshot[k] == preview['baseline'][k] for k in ('identity_sha256', 'pid', 'process_creation', 'window_id', 'edit_window_id')),
                'buffer_exactly_appended': snapshot['text'] == expected,
                'bounds_unchanged': snapshot['bounds'] == preview['baseline']['bounds'],
                'qualification_current': qualification is not None and qualification['qualification_ref'] == preview['qualification_ref'],
                'fresh_sdk_exact_target': metadata.get('pid') == snapshot['pid'] and metadata.get('window_id') == snapshot['window_id'],
                'fresh_sdk_edit_value': any(isinstance(e, dict) and e.get('role') == 'Edit' and e.get('value') == expected
                    for e in metadata.get('elements', []))}
            proof = {'operation_id': request['operation_id'], 'scope': SCOPE, 'checks': checks,
                'actual_text_sha256': snapshot['text_sha256'], 'expected_text_sha256': text_sha(expected),
                'no_save_action': True, 'disk_unchanged_verified': False,
                'fresh_frame_ref': observation['frame']['frame_ref'],
                'sdk_metadata_ref': observation['frame']['sdk_metadata_ref'],
                'sdk_effect_is_goal_proof': False, 'model_answer_is_goal_proof': False}
            ref = self._append_put('verifications', proof)
            return {'status': 'VERIFIED' if all(checks.values()) else 'NOT_VERIFIED',
                'reason': 'Independent actual Win32 exact buffer/identity/bounds; no disk-save claim', 'evidence_refs': [ref]}
        except Exception as error:
            return {'status': 'UNKNOWN', 'reason': str(error)[:300], 'evidence_refs': []}

    async def revoke(self, body, token):
        result = await super().revoke(body, token)
        with self.authority.lock:
            folder = self._append_path('operations', 'placeholder').parent
            for path in folder.glob('*.json'):
                op = self.authority._read(path, authority=False)
                if op['generation'] == self.intents.generation:
                    op.update(status='REVOKED', activation_state='HANDOFF_REQUIRED')
                    self.authority._write(path, op)
        return result


def register_native_append_routes(app, *, service, trusted_activator=None):
    """Register only the same NativeTasks subclass used by runtime/old routes."""
    if not isinstance(service, NotepadAppendTasks):
        raise Conflict('Native append requires the single NotepadAppendTasks owner')
    existing = getattr(app.state, 'native_tasks', None)
    if existing is not None and existing is not service:
        raise Conflict('A second native task/controller owner is forbidden')
    if trusted_activator is not None: service.trusted_activator = trusted_activator
    app.state.native_tasks = service
    def host(x_eeas_desktop_host: str | None = Header(None)):
        service.authority.check_host(x_eeas_desktop_host)
        return x_eeas_desktop_host
    root = '/api/desktop-host/native-append'
    @app.get(root + '/get')
    def get(operation_id: Id | None = None, token=Depends(host)):
        return service.append_get(operation_id)
    @app.post(root + '/get')
    def current(body: AppendGetInput, token=Depends(host)):
        return service.append_get(body.operation_id, body.context_ref.model_dump() if body.context_ref else None)
    @app.post(root + '/prepare')
    def prepare(body: AppendPrepareInput, token=Depends(host)):
        return service.prepare_append(body, token)
    @app.post(root + '/approve')
    def approve(body: AppendApproveInput, token=Depends(host)):
        return service.approve_append(body, token)
    @app.post(root + '/start')
    async def start(body: AppendStartInput, token=Depends(host)):
        return await service.start_append(body, token)
    @app.post(root + '/revoke')
    async def revoke(body: NativeTaskRevokeInput, token=Depends(host)):
        return await service.revoke(body, token)
    return service
