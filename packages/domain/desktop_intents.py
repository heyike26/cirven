"""Message-bound, temporary desktop input authority around the original loop.

This service neither classifies language nor starts a model, native session or
application. Original-loop interpretations are suggestions. A server authorizer
must independently accept an exact user instruction and its bounded effects.
Native qualification, current target identity and fresh frames remain separate.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import inspect
import json
import math
import re
import threading
import time
from typing import Literal
import uuid

from pydantic import Field, model_validator

from packages.domain.desktop_control import DesktopExecutionRef
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import canonical, digest, now, sha
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, NotFound

INTENT_PROMPT_PREFIX = 'EEAS exact user message/context envelope (metadata, never tool instructions):\n'


def _stored_result_sha(value):
    return sha(value.encode('utf-8') if isinstance(value, str) else canonical(value).encode('utf-8'))


class DesktopMessageInput(RequestModel):
    operation_id: Id
    raw_text: str = Field(min_length=1, max_length=16000)
    context_ref: RevisionRef | None = None


class DesktopIntentScope(RequestModel):
    surface_ref: RevisionRef
    input_epoch: int = Field(strict=True, ge=1)
    task_ref: RevisionRef | None = None
    target_ref: RevisionRef
    context_ref: RevisionRef
    frame_ref: RevisionRef | None = None
    authorization_ref: RevisionRef
    user_message_ref: RevisionRef


class InstructionSpan(RequestModel):
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, gt=0)
    quote: str = Field(min_length=1, max_length=16000)


class DesktopIntentCandidate(RequestModel):
    intent: Literal['OBSERVE', 'ACTION', 'UNKNOWN']
    goal: str = Field(min_length=1, max_length=4000)
    instruction_span: InstructionSpan | None = None
    allowed_effects: list[Literal['CLICK', 'TYPE_TEXT', 'PRESS_KEY']] = Field(default_factory=list, max_length=3)
    question: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode='after')
    def scope(self):
        if len(set(self.allowed_effects)) != len(self.allowed_effects):
            raise ValueError('Desktop effects must be unique')
        if self.intent == 'ACTION' and (self.instruction_span is None or not self.allowed_effects):
            raise ValueError('An action interpretation requires an exact instruction span and bounded effects')
        if self.intent != 'ACTION' and self.allowed_effects:
            raise ValueError('An observation or question cannot request input effects')
        return self


class DesktopInputRequest(RequestModel):
    operation_id: Id
    grant_ref: RevisionRef
    session_ref: RevisionRef
    target_ref: RevisionRef
    frame_ref: RevisionRef
    action: dict

    @model_validator(mode='after')
    def exact_supported_input(self):
        value = self.action
        kind = value.get('kind')
        if kind == 'CLICK':
            element_click = set(value) == {'kind', 'element_token', 'delivery_mode'}
            if element_click:
                if (not isinstance(value['element_token'], str) or not 1 <= len(value['element_token']) <= 1024
                        or value['delivery_mode'] not in {'foreground', 'background'}):
                    raise ValueError('An exact frame-bound SDK element token is required')
            elif (set(value) != {'kind', 'x', 'y', 'delivery_mode'} or
                    any(type(value[k]) not in (int, float) or not math.isfinite(value[k]) or value[k] < 0 for k in ['x', 'y']) or
                    value['delivery_mode'] not in {'foreground', 'background'}):
                raise ValueError('An exact finite capture-pixel click is required')
        elif kind == 'TYPE_TEXT':
            if (set(value) not in ({'kind', 'text'}, {'kind', 'text', 'delivery_mode'}) or not isinstance(value['text'], str) or
                    not 1 <= len(value['text'].encode('utf-8')) <= 65536):
                raise ValueError('Bounded exact UTF-8 text is required')
        elif kind == 'PRESS_KEY':
            if set(value) not in ({'kind', 'key'}, {'kind', 'key', 'delivery_mode'}) or value['key'] not in {'ENTER', 'TAB', 'ESC', 'BACKSPACE', 'DELETE', 'LEFT', 'RIGHT', 'UP', 'DOWN', 'HOME', 'END'}:
                raise ValueError('A declared single key is required')
        else:
            raise ValueError('This slice supports only the original declared Cua input kinds')
        if 'delivery_mode' in value and value['delivery_mode'] not in {'foreground', 'background'}:
            raise ValueError('Native input delivery must be explicit; no automatic fallback')
        return self


class DesktopTaskVerification(RequestModel):
    status: Literal['VERIFIED', 'NOT_VERIFIED', 'UNKNOWN']
    reason: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[RevisionRef] = Field(default_factory=list, max_length=64)
    after_context_ref: RevisionRef | None = None

    @model_validator(mode='after')
    def evidence(self):
        if self.status == 'VERIFIED' and not self.evidence_refs:
            raise ValueError('A verified user goal requires actual source observation receipts')
        if len({(r.id, r.revision, r.sha256) for r in self.evidence_refs}) != len(self.evidence_refs):
            raise ValueError('Goal verification evidence references must be unique')
        return self


class DesktopIntents:
    """Trusted-main records plus bounded task grants; durable USER default.

    context_reader(ref) and context_checker(ref,target,surface,epoch) must be
    readonly, nonblocking native-authority lookups, never SDK-worker waits.
    authorizer(message,interpretation,context) returns None or a reviewed policy
    receipt {policy_ref,allowed_effects,max_actions,ttl_seconds}. It is not an
    endpoint accepting model booleans. Capability receipts must describe actual
    qualified tool routes; importing an SDK does not satisfy them.
    """
    def __init__(self, workspace, *, transport=None, context_reader=None,
                 context_checker=None, authorizer=None, capability_reader=None,
                 source_checker=None, queue_revoker=None, cua_adapter=None,
                 long_control=None, result_checker=None, clock=None, native_scope_checker=None):
        self.ws, self.lock, self.transport = workspace, workspace.lock, transport
        self.context_reader, self.context_checker, self.authorizer = context_reader, context_checker, authorizer
        self.capability_reader, self.source_checker = capability_reader, source_checker
        self.queue_revoker, self.cua, self.long_control = queue_revoker, cua_adapter, long_control
        self.result_checker = result_checker
        self.native_scope_checker = native_scope_checker
        self.clock = clock or time.time
        self.generation = uuid.uuid4().hex + uuid.uuid4().hex
        self.takeover_pending = threading.Event()
        self._startup_reading = False
        self._startup_host_sha = None
        self._startup_head = None
        self.startup_history_ref = None
        with self.lock:
            self._startup_reading = True
            try:
                prior = self._head(history=True) if self._path('head.json').exists() else None
                if prior is not None and (not isinstance(prior, dict) or type(prior.get('epoch')) is not int
                        or not 1 <= prior['epoch'] < (1 << 63) or not isinstance(prior.get('generation'), str)
                        or not re.fullmatch('[a-f0-9]{64}', prior['generation'])
                        or not isinstance(prior.get('id'), str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}', prior['id'])
                        or not isinstance(prior.get('unresolved_execution_refs'), list)
                        or len(prior['unresolved_execution_refs']) > 1000):
                    raise Conflict('Desktop startup historical state integrity failed')
                unresolved = [DesktopExecutionRef.model_validate(ref).model_dump()
                    for ref in (prior or {}).get('unresolved_execution_refs', [])]
                if prior is not None and prior.get('active_grant'):
                    grant = self._get('grants', prior['active_grant'], history=True)
                    execution = DesktopExecutionRef.model_validate(grant['execution_ref']).model_dump()
                    if execution not in unresolved: unresolved.append(execution)
                if prior is not None:
                    self.startup_history_ref = self._put('startup-history', {
                        'type': 'READONLY_PREVIOUS_HOST_DESKTOP_INTENT_STATE', 'authority_adopted': False,
                        'prior_head_wrapper': copy.deepcopy(self._startup_head),
                        'prior_state_sha256': digest(prior), 'prior_generation': prior['generation'],
                        'prior_epoch': prior['epoch'], 'prior_active_grant_ref': prior.get('active_grant'),
                        'historical_execution_refs': copy.deepcopy(unresolved),
                        'startup_model_calls': 0, 'startup_sdk_calls': 0, 'startup_cancel_calls': 0})
            finally:
                self._startup_reading = False
                self._startup_host_sha = None
                self._startup_head = None
            self.historical_execution_refs = copy.deepcopy(unresolved)
            self.state = {'id': (prior or {}).get('id', uuid.uuid4().hex + uuid.uuid4().hex),
                'epoch': (prior or {}).get('epoch', 0) + 1, 'generation': self.generation,
                'active_grant': None, 'unresolved_execution_refs': unresolved,
                'cancellation': {'status': 'UNKNOWN_OUTCOME' if unresolved else 'NOT_NEEDED',
                                 'confirmed': not bool(unresolved)}}
            self._save('RESTART_REVOKES_TASK_INPUT_NO_AUTOMATIC_REPLAY' if prior else 'DEFAULT_USER_NO_TASK_INPUT')

    def _path(self, *parts): return self.ws._path('intents', *parts)

    def _startup_read(self, path):
        """Constructor-only historical bytes; never a current permission read.

        A new Main token cannot authenticate its predecessor's MAC. Historical
        refs/hashes remain evidence only. Records from this same host must still
        pass its current MAC; their tampering is never downgraded to history.
        """
        if not self._startup_reading:
            raise Conflict('Historical receipt reads are limited to startup')
        value = self.ws._read(path, authority=False)
        try:
            wrapper = json.loads(path.read_bytes())
            if (set(wrapper) != {'payload', 'sha256', 'host_token_sha256', 'host_mac'}
                    or wrapper['payload'] != value or wrapper['sha256'] != digest(value)
                    or not re.fullmatch('[a-f0-9]{64}', wrapper['host_token_sha256'])
                    or not re.fullmatch('[a-f0-9]{64}', wrapper['host_mac'])):
                raise ValueError
            token = getattr(self.ws, 'token', None)
            if token is None: token = getattr(getattr(self.ws, 'base', None), 'token', None)
            if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{64}', token):
                raise ValueError
            if self._startup_host_sha is None:
                self._startup_host_sha = wrapper['host_token_sha256']
                self._startup_head = copy.deepcopy(wrapper)
            elif wrapper['host_token_sha256'] != self._startup_host_sha:
                raise ValueError
            if wrapper['host_token_sha256'] == sha(token.encode('utf-8')):
                return self.ws._read(path, authority=True)
            return copy.deepcopy(value)
        except (ValueError, KeyError, TypeError):
            raise Conflict('Desktop startup historical receipt integrity failed') from None

    def _head(self, *, history=False):
        pointer = self._startup_read(self._path('head.json')) if history else self.ws._read(self._path('head.json'), authority=True)
        if history and (not isinstance(pointer, dict) or set(pointer) != {'state_ref'}):
            raise Conflict('Desktop startup historical head integrity failed')
        view = self._get('states', pointer['state_ref'], history=history)
        if history and not isinstance(view, dict):
            raise Conflict('Desktop startup historical state payload integrity failed')
        return view

    def _save(self, kind):
        value = {**copy.deepcopy(self.state), 'kind': kind, 'created_at': now()}
        ref = self._put('states', value)
        self.ws._write(self._path('head.json'), {'state_ref': ref})

    def _current(self):
        current = self._head()
        if current['generation'] != self.generation:
            raise Conflict('This desktop intent controller lifetime is no longer authoritative')

    def _put(self, group, payload):
        value = copy.deepcopy(payload)
        identifier = uuid.uuid4().hex + uuid.uuid4().hex
        ref = {'id': identifier, 'revision': 1, 'sha256': digest(value)}
        self.ws._write(self._path(group, identifier + '.json'), {'ref': ref, 'payload': value})
        return ref

    def _get(self, group, reference, *, history=False):
        ref = RevisionRef.model_validate(reference).model_dump()
        path = self._path(group, ref['id'] + '.json')
        if not path.exists(): raise NotFound('Desktop intent reference does not exist')
        value = self._startup_read(path) if history else self.ws._read(path, authority=True)
        if value.get('ref') != ref or digest(value.get('payload')) != ref['sha256']:
            raise Conflict('Desktop intent reference or immutable bytes changed')
        return copy.deepcopy(value['payload'])

    def _operation(self, operation_id, kind, request):
        # Different operation families cannot overwrite one another's receipts.
        name = digest({'operation_id': operation_id, 'kind': kind}) + '.json'
        path = self._path('operations', name)
        claim = {'operation_id': operation_id, 'kind': kind, 'request_sha256': digest(request)}
        if path.exists():
            prior = self.ws._read(path, authority=True)
            if prior.get('claim') != claim: raise Conflict('Desktop intent operation is already bound to different bytes')
            outcome = self._path('operation-outcomes', name)
            if outcome.exists():
                completed = self.ws._read(outcome, authority=True)
                if completed.get('claim') != claim: raise Conflict('Desktop intent operation outcome changed scope')
                prior = completed
            return copy.deepcopy(prior['result']), path, claim
        return None, path, claim

    def _finish(self, path, claim, result):
        self.ws._write(path, {'claim': claim, 'result': result, 'created_at': now()})
        return {**copy.deepcopy(result), 'replayed': False}

    def _capability(self):
        try: receipt = self.capability_reader() if callable(self.capability_reader) else {}
        except Exception: receipt = {}
        ready = (isinstance(receipt, dict) and receipt.get('desktop_input_routes_qualified') is True
            and receipt.get('owned_queue_drain_qualified') is True and callable(self.source_checker)
            and callable(self.context_checker) and callable(self.queue_revoker)
            and callable(getattr(self.transport, 'inspect', None))
            and callable(getattr(self.transport, 'request_cancel', None)) and self.cua is not None)
        return {'single_task_input_ready': ready, 'desktop_input_routes_qualified': ready,
                'qualification': 'QUALIFIED_REGISTERED_DESKTOP_TASK_ROUTES' if ready else 'DESKTOP_TASK_ROUTES_NOT_QUALIFIED',
                'direct_file_cas_requires_task_grant': False}

    def record_message(self, request, *, host_token):
        """Private main IPC only: preserve actual composer bytes, including whitespace."""
        self.ws.check_host(host_token)
        body = DesktopMessageInput.model_validate(request).model_dump()
        with self.lock:
            self._current()
            prior, path, claim = self._operation(body['operation_id'], 'MESSAGE', body)
            if prior: return {**prior, 'replayed': True}
            message = {**body, 'origin': 'RIGHT_AGENT_COMPOSER', 'role': 'user',
                'raw_text_sha256': sha(body['raw_text'].encode('utf-8')), 'received_at': now()}
            ref = self._put('messages', message)
            return self._finish(path, claim, {'status': 'RECORDED', 'message_ref': ref,
                'raw_text_sha256': message['raw_text_sha256'], 'context_ref': body['context_ref'],
                'interaction_mode': 'NONE', 'mutation_authority': False})

    def message(self, message_ref):
        with self.lock: return self._get('messages', message_ref)

    def interpretation_prompt(self, message_ref):
        """Compatibility envelope for the existing read-only planning transport.

        The native prompt freezes original whitespace and source refs without
        relaxing that transport's strict request schema or adding a second loop.
        A desktop-tool transport may instead carry the same refs in its typed
        desktop_intent field. Neither form authorizes native input.
        """
        message = self.message(message_ref)
        envelope = {'user_message_ref': copy.deepcopy(message_ref), 'context_ref': message['context_ref'],
                    'raw_text_sha256': message['raw_text_sha256'], 'raw_text': message['raw_text']}
        return (INTENT_PROMPT_PREFIX + canonical(envelope) + '\n'
                'Interpret only this original user message as OBSERVE, ACTION or UNKNOWN. '
                'A question is read-only. Unclear target or effects must remain UNKNOWN. '
                'Return an exact character span for a clear instruction. '
                'Effect names are suggestions and grant no permissions. Do not execute anything.')

    def _execution(self, reference):
        ref = DesktopExecutionRef.model_validate(reference).model_dump()
        if not callable(getattr(self.transport, 'inspect', None)):
            raise Conflict('Original desktop execution authority is unavailable')
        row = self.transport.inspect(ref['project_id'], ref['execution_id'])
        if (row.get('id') != ref['execution_id'] or row.get('project_id') != ref['project_id']
                or row.get('request_hash') != ref['request_sha256']):
            raise Conflict('Exact original desktop execution identity changed')
        try:
            request = json.loads(row['request']) if isinstance(row['request'], str) else row['request']
            if not isinstance(request, dict) or digest(request) != ref['request_sha256']: raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise Conflict('Original desktop execution request bytes changed') from None
        return row, request

    def interpret(self, operation_id, message_ref, execution_ref, *, host_token):
        """Read committed original-loop output; caller cannot submit an ACTION boolean."""
        self.ws.check_host(host_token)
        operation_id = DesktopMessageInput.model_validate({'operation_id': operation_id, 'raw_text': 'x'}).operation_id
        message_ref = RevisionRef.model_validate(message_ref).model_dump()
        execution_ref = DesktopExecutionRef.model_validate(execution_ref).model_dump()
        body = {'message_ref': message_ref, 'execution_ref': execution_ref}
        with self.lock:
            self._current()
            prior, path, claim = self._operation(operation_id, 'INTERPRETATION', body)
            if prior: return {**prior, 'replayed': True}
            message = self._get('messages', message_ref)
            row, native_request = self._execution(execution_ref)
            typed = native_request.get('desktop_intent') == {'user_message_ref': message_ref, 'context_ref': message['context_ref']}
            compatibility = native_request.get('prompt') == self.interpretation_prompt(message_ref)
            if not typed and not compatibility:
                raise Conflict('Original interpretation is not bound to this exact user message and context')
            if row['execution_status'] not in {'SUCCEEDED', 'RETURNED'}:
                raise Conflict('Original interpretation has not returned a committed successful result')
            try:
                output = json.loads(row['result']) if isinstance(row['result'], str) else row['result']
                candidate = DesktopIntentCandidate.model_validate(output['candidate_json']).model_dump()
            except (KeyError, TypeError, ValueError):
                raise Conflict('Original interpretation result is incomplete or invalid') from None
            span = candidate['instruction_span']
            if span and message['raw_text'][span['start']:span['end']] != span['quote']:
                raise Conflict('Interpreted instruction span is not the exact original user text')
            value = {'user_message_ref': message_ref, 'execution_ref': execution_ref,
                'original_result_sha256': digest(output), 'candidate': candidate,
                'semantic_authority': 'MODEL_INTERPRETATION_NOT_USER_AUTHORIZATION', 'created_at': now()}
            ref = self._put('interpretations', value)
            return self._finish(path, claim, {'status': 'INTERPRETED', 'interpretation_ref': ref,
                'intent': candidate['intent'], 'mutation_authority': False,
                'interaction_mode': 'INLINE_QUESTION' if candidate['intent'] == 'UNKNOWN' else 'NONE',
                'question': candidate['question']})

    def authorize(self, operation_id, interpretation_ref, *, host_token):
        """Record an accepted single instruction; qualification is still required to bind input."""
        self.ws.check_host(host_token)
        operation_id = DesktopMessageInput.model_validate({'operation_id': operation_id, 'raw_text': 'x'}).operation_id
        interpretation_ref = RevisionRef.model_validate(interpretation_ref).model_dump()
        with self.lock:
            self._current()
            prior, path, claim = self._operation(operation_id, 'AUTHORIZE', interpretation_ref)
            if prior: return {**prior, 'replayed': True}
            interpretation = self._get('interpretations', interpretation_ref)
            message = self._get('messages', interpretation['user_message_ref'])
            row, _ = self._execution(interpretation['execution_ref'])
            try: original_result = json.loads(row['result']) if isinstance(row['result'], str) else row['result']
            except (KeyError, TypeError, ValueError): raise Conflict('Original interpretation result was lost') from None
            if (row['execution_status'] not in {'SUCCEEDED', 'RETURNED'} or
                    digest(original_result) != interpretation['original_result_sha256']):
                raise Conflict('Original interpretation result bytes changed')
            candidate = interpretation['candidate']
            base = {'user_message_ref': interpretation['user_message_ref'], 'interpretation_ref': interpretation_ref,
                    'owner': 'USER', 'mutation_authority': False}
            if candidate['intent'] == 'OBSERVE':
                return self._finish(path, claim, {**base, 'status': 'OBSERVE', 'interaction_mode': 'NONE',
                    'observation_authority': 'READ_ONLY_CURRENT_CONTEXT_NOT_SOURCE_FACT'})
            if candidate['intent'] == 'UNKNOWN' or not message['context_ref']:
                return self._finish(path, claim, {**base, 'status': 'NEEDS_INPUT', 'interaction_mode': 'INLINE_QUESTION',
                    'question': candidate['question'] or '请明确要操作的目标或必要信息。'})
            if not callable(self.context_reader) or not callable(self.authorizer):
                return self._finish(path, claim, {**base, 'status': 'AUTHORIZER_UNAVAILABLE', 'interaction_mode': 'NONE'})
            context = self.context_reader(message['context_ref'])
            if not isinstance(context, dict) or context.get('context_ref') != message['context_ref']:
                raise Conflict('Exact current-page context authority changed')
            if context.get('status') != 'READY_OBSERVATION':
                questions = context.get('questions') or []
                return self._finish(path, claim, {**base, 'status': 'NEEDS_INPUT', 'interaction_mode': 'INLINE_QUESTION',
                    'question': questions[0].get('text', '请明确当前要操作的目标。') if questions else '请明确当前要操作的目标。'})
            # The embedding policy must derive its decision from actual user
            # text, not model-proposed effect names or a renderer boolean.
            policy = self.authorizer(copy.deepcopy(message), copy.deepcopy(interpretation), copy.deepcopy(context))
            if policy is None:
                return self._finish(path, claim, {**base, 'status': 'NEEDS_INPUT', 'interaction_mode': 'INLINE_QUESTION',
                    'question': '当前指令的操作范围尚不明确，请说明要操作的目标和结果。'})
            if (not isinstance(policy, dict) or set(policy) != {'policy_ref', 'allowed_effects', 'max_actions', 'ttl_seconds'} or
                    not isinstance(policy['allowed_effects'], list) or not policy['allowed_effects'] or
                    len(set(policy['allowed_effects'])) != len(policy['allowed_effects']) or
                    not set(policy['allowed_effects']) <= set(candidate['allowed_effects']) or
                    type(policy['max_actions']) is not int or not 1 <= policy['max_actions'] <= 100 or
                    type(policy['ttl_seconds']) is not int or not 1 <= policy['ttl_seconds'] <= 600):
                raise Conflict('Server single-instruction authorization is invalid or expands the interpretation')
            policy['policy_ref'] = RevisionRef.model_validate(policy['policy_ref']).model_dump()
            scope = {'surface_ref': RevisionRef.model_validate(context['surface_ref']).model_dump(),
                'input_epoch': context['input_epoch'], 'task_ref': None,
                'target_ref': RevisionRef.model_validate(context['window_ref']).model_dump(),
                'context_ref': message['context_ref'], 'frame_ref': None,
                'authorization_ref': None, 'user_message_ref': interpretation['user_message_ref']}
            if type(scope['input_epoch']) is not int or scope['input_epoch'] < 1:
                raise Conflict('Current desktop input epoch is unavailable')
            value = {'scope': scope, 'policy': policy, 'interpretation_ref': interpretation_ref,
                'user_message_sha256': message['raw_text_sha256'], 'instruction_span': candidate['instruction_span'],
                'goal': candidate['goal'], 'context_proof_scope': 'NATIVE_WINDOW_IDENTITY_AND_CURRENT_WORKSPACE_ONLY',
                'created_at': now()}
            ref = self._put('authorizations', value)
            # Reference cannot contain its own digest. The exact reference is
            # inserted only in the execution scope envelope returned below.
            return self._finish(path, claim, {**base, 'status': 'SINGLE_COMMAND_AUTHORIZED', 'authorization_ref': ref,
                'interaction_mode': 'NONE', 'capabilities': self._capability(), 'scope': scope | {'authorization_ref': ref}})

    def execution_scope(self, authorization_ref):
        with self.lock:
            value = self._get('authorizations', authorization_ref)
            return value['scope'] | {'authorization_ref': copy.deepcopy(authorization_ref)}

    def execution_envelope(self, authorization_ref):
        """Preserve the sealed long-control ABI while binding extended sources."""
        scope = self.execution_scope(authorization_ref)
        legacy = {name: copy.deepcopy(scope[name]) for name in ['surface_ref', 'input_epoch', 'task_ref']}
        return {'desktop_scope': legacy, 'desktop_intent_scope': scope}

    @staticmethod
    def _scope_matches(request, scope):
        # Direct V2 transports may use the extended key. The sibling envelope
        # is recommended so existing long-control exact comparisons keep their
        # original three-field scope. Never silently accept a partial union.
        try:
            scope = DesktopIntentScope.model_validate(scope).model_dump()
            if request.get('desktop_scope') == scope:
                typed = DesktopIntentScope.model_validate(request['desktop_scope']).model_dump()
                return typed == scope and digest(request['desktop_scope']) == digest(scope) and 'desktop_intent_scope' not in request
            legacy = {name: scope[name] for name in ['surface_ref', 'input_epoch', 'task_ref']}
            native_legacy = request.get('desktop_scope')
            if (not isinstance(native_legacy, dict) or type(native_legacy.get('input_epoch')) is not int or
                    native_legacy != legacy or set(native_legacy) != set(legacy)): return False
            extended = DesktopIntentScope.model_validate(request.get('desktop_intent_scope')).model_dump()
            return extended == scope and digest(request['desktop_intent_scope']) == digest(scope)
        except (ValueError, TypeError, KeyError):
            return False

    def bind_execution(self, operation_id, authorization_ref, execution_ref, expected_epoch, *, host_token):
        self.ws.check_host(host_token)
        operation_id = DesktopMessageInput.model_validate({'operation_id': operation_id, 'raw_text': 'x'}).operation_id
        authorization_ref = RevisionRef.model_validate(authorization_ref).model_dump()
        execution_ref = DesktopExecutionRef.model_validate(execution_ref).model_dump()
        body = {'authorization_ref': authorization_ref, 'execution_ref': execution_ref, 'expected_epoch': expected_epoch}
        with self.lock:
            self._current()
            prior, path, claim = self._operation(operation_id, 'BIND', body)
            if prior: return {**prior, 'replayed': True, 'current': prior.get('grant_ref') == self.state['active_grant']}
            if type(expected_epoch) is not int or expected_epoch != self.state['epoch']:
                raise Conflict('Stale desktop task input epoch')
            if self.state['active_grant'] or self.state['unresolved_execution_refs']:
                raise Conflict('Previous desktop task must be explicitly revoked and cancellation resolved')
            if not self._capability()['single_task_input_ready']:
                return self._finish(path, claim, {'status': 'UNAVAILABLE', 'grant_ref': None, 'owner': 'USER',
                    'reason': 'DESKTOP_TASK_ROUTES_NOT_QUALIFIED', 'mutation_authority': False})
            authorization = self._get('authorizations', authorization_ref)
            consumed_path = self._path('authorization-use', authorization_ref['id'] + '.json')
            if consumed_path.exists():
                consumed = self.ws._read(consumed_path, authority=True)
                if consumed != {'authorization_ref': authorization_ref, 'execution_ref': execution_ref}:
                    raise Conflict('A single user command cannot authorize another execution')
            scope = self.execution_scope(authorization_ref)
            row, request = self._execution(execution_ref)
            if row['execution_status'] not in {'QUEUED', 'RUNNING'} or not self._scope_matches(request, scope):
                raise Conflict('Execution is not bound to the exact authorized desktop command')
            self._check_scope(scope, row)
            if self.long_control and self.long_control.get().get('owner') != 'USER':
                raise Conflict('A long-lived Agent controller already owns desktop input')
            self.state['epoch'] += 1
            value = {'execution_ref': execution_ref, 'scope': scope, 'policy': authorization['policy'],
                'generation': self.generation, 'epoch': self.state['epoch'],
                'expires_unix_ms': int(self.clock() * 1000) + authorization['policy']['ttl_seconds'] * 1000}
            ref = self._put('grants', value)
            self.ws._write(consumed_path, {'authorization_ref': authorization_ref, 'execution_ref': execution_ref})
            self.ws._write(self._path('execution-grants', digest(execution_ref) + '.json'), {'execution_ref': execution_ref, 'grant_ref': ref})
            self.state['active_grant'] = ref; self.takeover_pending.clear(); self._save('SINGLE_COMMAND_BOUNDED_TASK_INPUT')
            return self._finish(path, claim, {'status': 'TASK_AUTHORIZED', 'owner': 'USER',
                'task_owner': 'BOUNDED_TASK', 'grant_ref': ref, 'intent_epoch': self.state['epoch'],
                'execution_ref': execution_ref, 'current': True, 'mutation_authority': True})

    def _source_current(self, scope, row):
        desktop = self.ws.get()
        if (desktop.get('status') != 'ACTIVE' or desktop.get('prepare_pending') or
                scope['surface_ref'] != desktop.get('surface_ref') or
                scope['input_epoch'] != desktop.get('input', {}).get('epoch')):
            return False
        if callable(self.native_scope_checker):
            # A trusted exact-target input authority replaces the historical
            # desktop-region prerequisite. It never attests a region or lets
            # a renderer flag authorize a native mutation.
            return self.native_scope_checker(copy.deepcopy(scope), copy.deepcopy(row)) is True
        return not (desktop.get('region_verified') is not True or
                desktop.get('visible_all_verified') is not True or desktop.get('prepare_pending') or
                desktop.get('presentation') == 'FULL_WORKSPACE' or desktop.get('input', {}).get('enabled') is not True or
                scope['surface_ref'] != desktop.get('surface_ref') or scope['input_epoch'] != desktop.get('input', {}).get('epoch'))

    def _check_scope(self, scope, row):
        if not self._source_current(scope, row):
            raise Conflict('Current native desktop source is unverified, changed or fenced')
        if (self.context_checker(scope['context_ref'], scope['target_ref'], scope['surface_ref'], scope['input_epoch']) is not True or
                self.source_checker(row, scope) is not True):
            raise Conflict('Current target or exact execution/message sources changed')
        self._get('messages', scope['user_message_ref'])
        self._get('authorizations', scope['authorization_ref'])

    def check_sources(self, row, scope):
        """Readonly source_checker seam for both single-task and sealed Control.

        This is scope/identity verification, not route qualification or a proof
        that a document body remained unchanged. Cua verifies each fresh frame.
        No SDK worker call, model call, mutable record or browser path is used.
        """
        try:
            with self.lock:
                self._current()
                request = json.loads(row['request']) if isinstance(row['request'], str) else row['request']
                if digest(request) != row['request_hash'] or row['execution_status'] not in {'QUEUED', 'RUNNING'}: return False
                extended = request.get('desktop_intent_scope', request.get('desktop_scope'))
                if not isinstance(extended, dict) or not self._scope_matches(request, extended): return False
                legacy = {name: extended[name] for name in ['surface_ref', 'input_epoch', 'task_ref']}
                if digest(scope) not in [digest(legacy), digest(extended)]: return False
                if self.execution_scope(extended['authorization_ref']) != extended: return False
                authorization = self._get('authorizations', extended['authorization_ref'])
                message = self._get('messages', extended['user_message_ref'])
                if (message['context_ref'] != extended['context_ref'] or
                        message['raw_text_sha256'] != authorization['user_message_sha256'] or
                        sha(message['raw_text'].encode('utf-8')) != authorization['user_message_sha256']): return False
                if not self._source_current(extended, row):
                    return False
                return (callable(self.context_checker) and
                    self.context_checker(extended['context_ref'], extended['target_ref'], extended['surface_ref'], extended['input_epoch']) is True)
        except Exception:
            return False

    def _grant(self, ref):
        self._current()
        if self.takeover_pending.is_set() or ref != self.state['active_grant']:
            raise Conflict('Single-command desktop authority was revoked')
        grant = self._get('grants', ref)
        if grant['generation'] != self.generation or grant['expires_unix_ms'] <= int(self.clock() * 1000):
            raise Conflict('Single-command desktop authority expired')
        if not self._capability()['single_task_input_ready']:
            raise Conflict('Actual desktop task routes are not qualified')
        row, request = self._execution(grant['execution_ref'])
        if row['execution_status'] not in {'QUEUED', 'RUNNING'} or not self._scope_matches(request, grant['scope']):
            raise Conflict('Single-command original execution is no longer current')
        self._check_scope(grant['scope'], row)
        if self.long_control and self.long_control.get().get('owner') != 'USER':
            raise Conflict('Another Agent controller acquired desktop input')
        return grant

    def check_grant(self, row, scope, grant_ref):
        """Original desktop transport start gate; no native input is dispatched."""
        try:
            with self.lock:
                grant = self._grant(RevisionRef.model_validate(grant_ref).model_dump())
                ref = grant['execution_ref']
                legacy = {name: grant['scope'][name] for name in ['surface_ref', 'input_epoch', 'task_ref']}
                return (row.get('id') == ref['execution_id'] and row.get('project_id') == ref['project_id'] and
                    row.get('request_hash') == ref['request_sha256'] and digest(scope) in [digest(legacy), digest(grant['scope'])] and
                    self.check_sources(row, scope))
        except Exception:
            return False

    def prepare_input(self, request):
        """Tool admission freezes exact arguments; renderer/model receives no input authority."""
        body = DesktopInputRequest.model_validate(request).model_dump()
        with self.lock:
            prior, path, claim = self._operation(body['operation_id'], 'INPUT', body)
            if prior: return {**prior, 'replayed': True}
            grant = self._grant(body['grant_ref'])
            if body['target_ref'] != grant['scope']['target_ref'] or body['action']['kind'] not in grant['policy']['allowed_effects']:
                raise Conflict('Input exceeds the bounded user command target or effects')
            used = self._path('grant-inputs', body['grant_ref']['id'])
            if len(list(used.glob('*.json'))) >= grant['policy']['max_actions']:
                raise Conflict('Single-command input budget exhausted')
            ref = self._put('inputs', {'request': body, 'scope': grant['scope'],
                'execution_ref': grant['execution_ref'], 'intent_epoch': grant['epoch'], 'created_at': now()})
            self.ws._write(used / (ref['id'] + '.json'), {'input_ref': ref})
            self.ws._write(self._path('input-states', ref['id'] + '.json'), {'status': 'PREPARED', 'input_ref': ref})
            return self._finish(path, claim, {'status': 'PREPARED', 'input_ref': ref, 'input_sha256': digest(body['action']),
                'mutation_authority': False, 'execution_ref': grant['execution_ref']})

    @contextmanager
    def dispatch_guard(self, input_ref, surface_ref, input_epoch, intent_epoch):
        """Cua worker only; hold both guards across the whole original SDK await."""
        if self.takeover_pending.is_set(): raise Conflict('Human takeover blocks subsequent desktop task input')
        with self.lock:
            value = self._get('inputs', input_ref); grant = self._grant(value['request']['grant_ref'])
            scope = grant['scope']
            if (surface_ref != scope['surface_ref'] or type(input_epoch) is not int or input_epoch != scope['input_epoch'] or
                    type(intent_epoch) is not int or intent_epoch != grant['epoch']):
                raise Conflict('Input dispatch differs from its exact single-command scope')
            path = self._path('input-states', input_ref['id'] + '.json')
            state = self.ws._read(path, authority=True)
            if state.get('input_ref') != input_ref or state.get('status') != 'PREPARED':
                raise Conflict('Input was already dispatched or has an unknown outcome; no automatic replay')
            self.ws._write(path, {'status': 'DISPATCHING', 'input_ref': input_ref})
            with self.ws.dispatch_guard(surface_ref, input_epoch):
                yield {'execution_ref': copy.deepcopy(value['execution_ref']), 'authorization_ref': scope['authorization_ref'],
                    'user_message_ref': scope['user_message_ref'], 'owner': 'USER', 'task_owner': 'BOUNDED_TASK'}

    @contextmanager
    def composite_dispatch_guard(self, reference, surface_ref, input_epoch, epoch):
        """One Cua worker: known input permit or the existing long-control lease.

        A corrupt known permit never falls through to a different authority.
        Desktop file CAS is intentionally not part of either branch.
        """
        ref = RevisionRef.model_validate(reference).model_dump()
        with self.lock:
            is_input = self._path('inputs', ref['id'] + '.json').exists()
        if is_input:
            with self.dispatch_guard(ref, surface_ref, input_epoch, epoch) as value: yield value
        elif self.long_control is not None:
            with self.long_control.dispatch_guard(ref, surface_ref, input_epoch, epoch) as value: yield value
        else:
            raise Conflict('No current single-task permit or long-lived desktop authority exists')

    def execute_input(self, input_ref):
        """Invoke the one existing adapter from frozen arguments, never a second body."""
        with self.lock:
            value = self._get('inputs', input_ref)
            path = self._path('input-states', input_ref['id'] + '.json')
            state = self.ws._read(path, authority=True)
            if state.get('input_ref') != input_ref: raise Conflict('Exact input receipt changed')
            if state['status'] == 'RETURNED': return {**state['result'], 'replayed': True}
            if state['status'] != 'PREPARED': raise Conflict('Unknown input outcome; never automatically resend')
            self._grant(value['request']['grant_ref'])
            if self.cua is None: raise Conflict('Actual Cua input adapter is unavailable')
        # No shared RLock while waiting for the same SDK worker. That worker
        # enters dispatch_guard and keeps it throughout the actual native await.
        request, scope = value['request'], value['scope']
        try:
            result = self.cua.act(request['session_ref'], request['target_ref'], request['frame_ref'], request['action'],
                lease_ref=input_ref, surface_ref=scope['surface_ref'], input_epoch=scope['input_epoch'],
                control_epoch=value['intent_epoch'], operation_id=request['operation_id'])
        except Exception as exc:
            with self.lock:
                self.ws._write(path, {'status': 'UNKNOWN_OUTCOME', 'input_ref': input_ref,
                    'error': {'type': type(exc).__name__, 'message': str(exc)}})
            raise
        with self.lock:
            # An SDK return reports transport outcome, never completed user goal.
            response = {'status': 'SDK_RETURNED_REOBSERVATION_REQUIRED', 'input_ref': input_ref,
                        'sdk_receipt': result, 'task_completed': False, 'replayed': False}
            self.ws._write(path, {'status': 'RETURNED', 'input_ref': input_ref, 'result': response})
            return response

    async def revoke(self, operation_id, expected_epoch, *, host_token):
        """Trusted-main takeover: revoke before queue drain and exact owned cancel."""
        self.ws.check_host(host_token)
        operation_id = DesktopMessageInput.model_validate({'operation_id': operation_id, 'raw_text': 'x'}).operation_id
        if type(expected_epoch) is int and expected_epoch == self.state['epoch']: self.takeover_pending.set()
        with self.lock:
            self._current()
            prior, path, claim = self._operation(operation_id, 'REVOKE', {'expected_epoch': expected_epoch})
            if prior: return {**prior, 'replayed': True}
            if type(expected_epoch) is not int or expected_epoch != self.state['epoch']:
                raise Conflict('Stale desktop task revocation epoch')
            grant = self._get('grants', self.state['active_grant']) if self.state['active_grant'] else None
            unresolved = copy.deepcopy(self.state['unresolved_execution_refs'])
            if grant and grant['execution_ref'] not in unresolved: unresolved.append(grant['execution_ref'])
            self.state['epoch'] += 1; self.state['active_grant'] = None
            self.state['unresolved_execution_refs'] = unresolved
            queue = {'status': 'DRAINED' if not unresolved else 'UNKNOWN_OUTCOME', 'discarded_count': 0 if not unresolved else None}
            try:
                if unresolved and callable(self.queue_revoker):
                    discarded = 0
                    for ref in unresolved:
                        if ref in self.historical_execution_refs:
                            raise Conflict('Historical input queues need original row/source validation before handling')
                        receipt = self.queue_revoker({'execution_ref': copy.deepcopy(ref)})
                        if (not isinstance(receipt, dict) or receipt.get('status') != 'DRAINED' or
                                type(receipt.get('discarded_count')) is not int or receipt['discarded_count'] < 0):
                            raise ValueError('Exact owned input queue drain is unverified')
                        discarded += receipt['discarded_count']
                    queue = {'status': 'DRAINED', 'discarded_count': discarded}
            except Exception as exc:
                queue = {'status': 'UNKNOWN_OUTCOME', 'discarded_count': None,
                         'error': {'type': type(exc).__name__, 'message': str(exc)}}
            self.state['cancellation'] = {'status': 'REQUESTED' if unresolved else queue['status'],
                'confirmed': not unresolved and queue['status'] == 'DRAINED', 'queued_inputs': queue}
            self._save('TASK_AUTHORITY_REVOKED_BEFORE_ORIGINAL_CANCEL')
            epoch = self.state['epoch']
            # Durable claim precedes await; losing its outcome does not resend.
            self._finish(path, claim, {'status': 'REVOKED', 'intent_epoch': epoch, 'owner': 'USER',
                'cancellation': copy.deepcopy(self.state['cancellation']), 'mutation_authority': False})
        outcomes = []
        for ref in unresolved:
            try:
                row, native_request = self._execution(ref)
                scope = native_request.get('desktop_intent_scope', native_request.get('desktop_scope'))
                if not isinstance(scope, dict): raise Conflict('Owned desktop command scope is unavailable')
                self._get('authorizations', scope['authorization_ref'])
                if self.execution_scope(scope['authorization_ref']) != scope or not self._scope_matches(native_request, scope):
                    raise Conflict('Cancellation scope changed')
                if row['execution_status'] not in {'SUCCEEDED', 'RETURNED', 'FAILED', 'CANCELLED', 'TIMED_OUT'}:
                    row = self.transport.request_cancel(ref['project_id'], ref['execution_id'])
                    if inspect.isawaitable(row): row = await row
                    if row.get('id') != ref['execution_id'] or row.get('project_id') != ref['project_id'] or row.get('request_hash') != ref['request_sha256']:
                        raise Conflict('Original cancellation receipt changed execution identity')
                outcomes.append({'execution_ref': ref, 'confirmed': row['execution_status'] in {'SUCCEEDED', 'RETURNED', 'FAILED', 'CANCELLED', 'TIMED_OUT'},
                                 'status': row['execution_status']})
            except Exception as exc:
                outcomes.append({'execution_ref': ref, 'confirmed': False, 'status': 'FAILED',
                                 'error': {'type': type(exc).__name__, 'message': str(exc)}})
        with self.lock:
            self._current()
            if self.state['epoch'] == epoch:
                self.state['unresolved_execution_refs'] = [o['execution_ref'] for o in outcomes if not o['confirmed']]
                confirmed = not self.state['unresolved_execution_refs'] and queue['status'] == 'DRAINED'
                self.state['cancellation'] = {'status': 'CONFIRMED' if confirmed else 'UNKNOWN_OUTCOME',
                    'confirmed': confirmed, 'queued_inputs': queue, 'outcomes': outcomes}
                self._save('EXACT_TASK_CANCELLATION_OUTCOME')
                result = {'status': 'REVOKED', 'intent_epoch': epoch, 'owner': 'USER',
                    'cancellation': copy.deepcopy(self.state['cancellation']), 'mutation_authority': False}
                outcome_path = self._path('operation-outcomes', path.name)
                self.ws._write(outcome_path, {'claim': claim, 'result': result})
                return {**result, 'replayed': False}
            return {'status': 'REVOKED', 'intent_epoch': epoch, 'owner': 'USER', 'current': False,
                    'cancellation': {'status': 'UNKNOWN_OUTCOME', 'confirmed': False}, 'mutation_authority': False}

    async def verify_outcome(self, execution_ref):
        """Fresh trusted result checker; native/model completion is not user-goal proof.

        A checker may need the same SDK worker for readonly reobservation, so it
        runs without the shared lock. It must load and verify real observations,
        never return a model-supplied success boolean as evidence.
        """
        execution_ref = DesktopExecutionRef.model_validate(execution_ref).model_dump()
        with self.lock:
            self._current()
            row, request = self._execution(execution_ref)
            if row['execution_status'] not in {'SUCCEEDED', 'RETURNED', 'FAILED', 'CANCELLED', 'TIMED_OUT'}:
                raise Conflict('Original task has not reached a confirmed terminal record')
            pointer = self.ws._read(self._path('execution-grants', digest(execution_ref) + '.json'), authority=True)
            if pointer.get('execution_ref') != execution_ref: raise Conflict('Goal verification execution scope changed')
            grant = self._get('grants', pointer['grant_ref'])
            if grant['execution_ref'] != execution_ref or not self._scope_matches(request, grant['scope']):
                raise Conflict('Goal verification is outside the original authorized scope')
            result_sha = _stored_result_sha(row.get('result'))
        if row['execution_status'] not in {'SUCCEEDED', 'RETURNED'}:
            checked = {'status': 'NOT_VERIFIED', 'reason': 'Original execution ended without a successful transport result', 'evidence_refs': []}
        elif not callable(self.result_checker):
            checked = {'status': 'UNKNOWN', 'reason': 'Actual user-goal result checker is not configured', 'evidence_refs': []}
        else:
            try:
                checked = self.result_checker(copy.deepcopy(row), copy.deepcopy(grant))
                if inspect.isawaitable(checked): checked = await checked
                checked = DesktopTaskVerification.model_validate(checked).model_dump()
            except Exception as exc:
                checked = {'status': 'UNKNOWN', 'reason': 'Actual result checker failed: ' + type(exc).__name__, 'evidence_refs': []}
        checked = DesktopTaskVerification.model_validate(checked).model_dump()
        with self.lock:
            self._current()
            current, _ = self._execution(execution_ref)
            if current['execution_status'] != row['execution_status'] or _stored_result_sha(current.get('result')) != result_sha:
                raise Conflict('Original execution changed during result reobservation')
            receipt = {'execution_ref': execution_ref, 'grant_ref': pointer['grant_ref'], 'scope': grant['scope'],
                'original_terminal_status': row['execution_status'], 'original_result_sha256': result_sha,
                'verification': checked, 'task_completed': checked['status'] == 'VERIFIED',
                'proof_scope': 'TRUSTED_USER_GOAL_CHECKER_ACTUAL_SOURCE_OBSERVATIONS', 'observed_at': now()}
            ref = self._put('verifications', receipt)
            return {'verification_ref': ref, **receipt}

    async def finish(self, execution_ref, *, host_token):
        """Release on actual terminal; separately report a fresh goal verification."""
        self.ws.check_host(host_token)
        execution_ref = DesktopExecutionRef.model_validate(execution_ref).model_dump()
        with self.lock:
            self._current()
            if not self.state['active_grant']: return {'status': 'NO_TASK_GRANT', 'owner': 'USER', 'mutation_authority': False}
            grant = self._get('grants', self.state['active_grant'])
            if grant['execution_ref'] != execution_ref: raise Conflict('Only the exact active desktop task can finish its authority')
            row, _ = self._execution(execution_ref)
            if row['execution_status'] not in {'SUCCEEDED', 'RETURNED', 'FAILED', 'CANCELLED', 'TIMED_OUT'}:
                raise Conflict('Original task completion is not confirmed')
            epoch = self.state['epoch']
        # Revoke first; a checker exception or slow readonly capture must never
        # leave task input authorized after the original loop ended.
        revoked = await self.revoke('finished-' + execution_ref['execution_id'], epoch, host_token=host_token)
        try:
            verified = await self.verify_outcome(execution_ref)
        except Exception as exc:
            verified = {'verification_ref': None, 'task_completed': False,
                'verification': {'status': 'UNKNOWN', 'reason': 'Fresh result verification failed: ' + type(exc).__name__, 'evidence_refs': []}}
        return {**revoked, 'task_outcome': verified, 'task_completed': verified['task_completed']}

    def get(self):
        with self.lock:
            return {'owner': 'USER', 'intent_epoch': self.state['epoch'], 'active_grant_ref': self.state['active_grant'],
                'can_takeover': bool(self.state['active_grant'] or self.state['unresolved_execution_refs']),
                'cancellation': copy.deepcopy(self.state['cancellation']), 'capabilities': self._capability(),
                'startup_history_ref': copy.deepcopy(self.startup_history_ref),
                'historical_execution_refs': copy.deepcopy(self.historical_execution_refs),
                'historical_authority_adopted': False,
                'model_calls': 0, 'scene_effect': 'NO_APP_KILL_OR_GLOBAL_INPUT_DISABLE'}
