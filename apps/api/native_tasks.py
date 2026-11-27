"""One message, original OpenCode executions and existing bounded task grants.

No model loop, native qualification, permission parser or SDK controller is
created here. Root supplies trusted authorization/policy/qualification hooks.
"""
import asyncio
import copy
import json
from pathlib import Path
import traceback
import uuid

from fastapi import Depends, Header

from packages.adapters.opencode_desktop import OpenCodeDesktopTransport
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.desktop_intents import DesktopIntents, DesktopIntentCandidate, DesktopMessageInput
from packages.domain.desktop_control import DesktopExecutionRef
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import digest
from packages.domain.native_task_authority import NativeTaskAuthority
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, NotFound
from pydantic import Field


class NativeTaskInput(DesktopMessageInput):
    provider_profile_ref: RevisionRef | None = None
    foreground_permission_ref: RevisionRef | None = None


class NativeTaskRevokeInput(RequestModel):
    operation_id: Id
    expected_intent_epoch: int = Field(strict=True, ge=1)


ANSWER_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['text', 'questions'],
    'properties': {'text': {'type': 'string', 'maxLength': 24000},
        'questions': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string', 'maxLength': 4000}}}}


def action_schema(effects, delivery):
    """Advertise exact SDK-supported inputs, never a generic tool passthrough."""
    variants = []
    for kind in effects:
        key, value = {'CLICK': ('element_token', {'type': 'string', 'minLength': 1, 'maxLength': 1024}),
            'TYPE_TEXT': ('text', {'type': 'string', 'minLength': 1, 'maxLength': 16384}),
            'PRESS_KEY': ('key', {'enum': ['ENTER', 'TAB', 'ESC', 'BACKSPACE', 'DELETE', 'LEFT', 'RIGHT', 'UP', 'DOWN', 'HOME', 'END']})}[kind]
        variants.append({'type': 'object', 'additionalProperties': False, 'required': ['kind', key, 'delivery_mode'],
            'properties': {'kind': {'const': kind}, key: value, 'delivery_mode': {'const': delivery.lower()}}})
    return {'oneOf': variants}


class NativeTasks:
    def __init__(self, app, *, companion, qualification_reader=None, authorizer=None,
                 policy_writer=None, result_checker=None, foreground_checker=None, transport=None):
        self.app, self.companion = app, companion
        self.authority = NativeTaskAuthority(companion.authority, context_reader=companion.context_rpc.lookup,
            qualification_reader=qualification_reader)
        self.authorizer, self.policy_writer = authorizer, policy_writer
        self.foreground_checker = foreground_checker
        self.active_context = None
        self.tasks, self.sessions = {}, {}
        self.closed = False
        self._session_close_outcomes = {}
        self._close_task = None
        self._close_receipt = None
        profiles = app.state.provider_settings
        source = Path(__file__).resolve().parents[2]
        bootstrap = source / 'scripts/native_package_bootstrap.py'
        if (source / 'PACKAGE_MANIFEST.json').exists() and not bootstrap.is_file():
            raise Conflict('Packaged native-task MCP bootstrap missing')
        self.transport = transport or OpenCodeDesktopTransport(app.state.store, app.state.settings.data_dir,
            provider_registry=OpenCodeProviderRegistry(profiles.configuration, profiles.secret),
            message_reader=lambda ref: self.intents.message(ref), context_reader=self.authority.context,
            context_checker=self._check_transport_context, task_context_checker=self._check_transport_context,
            authorization_reader=lambda ref: self.intents.execution_envelope(ref),
            capability_reader=self.capability, source_checker=lambda row, scope: self.intents.check_sources(row, scope),
            grant_checker=lambda row, scope, ref: self.intents.check_grant(row, scope, ref),
            queue_revoker=self.revoke_queued, foreground_checker=foreground_checker,
            mcp_script=bootstrap if (source / 'PACKAGE_MANIFEST.json').exists() else None)
        # This facade forwards to the one existing adapter/worker. Constructor
        # registration does not create a second SDK/controller or prove a route.
        class Adapter:
            def act(_self, *args, **kwargs): return self.adapter().act(*args, **kwargs)
        self.intents = DesktopIntents(self.authority, transport=self.transport,
            context_reader=self.authority.context, context_checker=self.authority.check_context,
            authorizer=self._authorize, capability_reader=self.capability,
            source_checker=lambda row, scope: self.intents.check_sources(row, scope),
            native_scope_checker=self.authority.check_input_scope, queue_revoker=self.revoke_queued,
            cua_adapter=Adapter(), result_checker=result_checker)
        self.transport.register_method({'id': 'native_task_observe', 'route': 'FRESH_BOUND_WINDOW', 'readonly': True,
            'effects': [], 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'coverage': 'EXACT_TASK_WINDOW_NOT_CURRENT_FOREGROUND_OR_WHOLE_PAGE', 'qualification': 'IMPLEMENTED',
            'cost': {'latency': 'SDK_READ', 'tokens': 'BOUNDED_SDK_METADATA', 'measured': False},
            'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                'requires_foreground_input': False, 'focus_effect': 'NONE'}}, self.observe)

    def adapter(self):
        value = self.companion.cua
        if value is None or getattr(value, 'closed', True):
            raise Conflict('Existing native Cua worker is unavailable')
        return value

    def _check_transport_context(self, context):
        return (isinstance(context, dict) and self.authority.check_context(context.get('context_ref'),
            context.get('window_ref'), context.get('surface_ref'), context.get('input_epoch')))

    def _path(self, ident): return self.authority._path('operations', ident + '.json')
    def _save(self, ident, value): self.authority._write(self._path(ident), value)
    def _read(self, ident):
        path = self._path(ident)
        if not path.exists(): raise NotFound('Native task does not exist')
        return self.authority._read(path, authority=False)

    @staticmethod
    def _execution(row):
        return DesktopExecutionRef.model_validate({'project_id': row['project_id'],
            'execution_id': row['id'], 'request_sha256': row['request_hash']}).model_dump()

    def capability(self):
        try:
            qualification = self.authority.qualification(self.active_context) if self.active_context else None
            available = qualification is not None and callable(self.authorizer) and callable(self.policy_writer)
            self.adapter()
        except Exception:
            available, qualification = False, None
        return {'desktop_input_routes_qualified': bool(available), 'owned_queue_drain_qualified': bool(available),
            'scope': 'EXACT_ACTIVE_MESSAGE_TARGET_ROUTE_ONLY', 'qualification_ref':
                qualification['qualification_ref'] if available else None,
            'readonly_companion_input_enabled': False}

    def _authorize(self, message, interpretation, context):
        if not callable(self.authorizer): return None
        qualification = self.authority.qualification(context['context_ref'])
        if qualification is None: return None
        policy = self.authorizer(copy.deepcopy(message), copy.deepcopy(interpretation), copy.deepcopy(context))
        if policy is None: return None
        # Root's semantic policy may reduce, never expand, actual target routes.
        if not set(policy.get('allowed_effects', [])) <= set(qualification['effects']):
            raise Conflict('Native command effects exceed actual target route qualification')
        return policy

    def _mutation_method(self, qualification):
        ident = 'native_task_input_' + qualification['qualification_ref']['sha256'][:24]
        if ident in self.transport.methods: return ident
        quiet = qualification['delivery'] == 'BACKGROUND' and qualification['focus_effect'] == 'NONE'
        self.transport.register_method({'id': ident, 'route': 'ORIGINAL_CUA_EXACT_TASK', 'readonly': False,
            'effects': qualification['effects'], 'qualification': 'QUALIFIED',
            'input_schema': {'type': 'object', 'additionalProperties': False,
                'required': ['operation_id', 'frame_ref', 'action'], 'properties': {
                    'operation_id': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                    'frame_ref': {'type': 'object', 'additionalProperties': False, 'required': ['id', 'revision', 'sha256'],
                        'properties': {'id': {'type': 'string'}, 'revision': {'type': 'integer', 'minimum': 1},
                            'sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}},
                    'action': action_schema(qualification['effects'], qualification['delivery'])}},
            'coverage': 'QUALIFIED_TARGET_EFFECTS_FRESH_FRAME_ONLY',
            'cost': {'latency': 'SDK_INPUT', 'tokens': 'BOUNDED_ACTION', 'measured': False},
            'execution': {'can_background': quiet, 'background_qualification': 'QUALIFIED' if quiet else 'UNQUALIFIED',
                'requires_foreground_input': not quiet, 'focus_effect': qualification['focus_effect']}}, self.input)
        return ident

    def _bound(self, binding, *, observe=False):
        operation = binding['request']['operation_id']
        record = self._read(operation)
        execution = DesktopExecutionRef.model_validate(binding.get('execution_ref')).model_dump()
        if (self.closed or record.get('execution_ref') != execution
                or record.get('grant_ref') != binding.get('grant_ref')
                or record.get('generation') != self.intents.generation
                or record.get('status') not in ({'RUNNING', 'AWAITING_VERIFICATION'} if observe else {'RUNNING'})):
            raise Conflict('Native tool is outside the exact active message/execution/grant')
        return operation, record

    def observe(self, _arguments, binding):
        _, record = self._bound(binding, observe=True)
        scope = record['scope']
        frame = self.adapter().capture(record['session_ref'], scope['target_ref'], scope['surface_ref'], scope['input_epoch'])
        path = self.adapter().data / frame['sdk_metadata_ref']['path']
        metadata = path.read_bytes()
        import hashlib
        if len(metadata) > 2 * 1024 * 1024 or hashlib.sha256(metadata).hexdigest() != frame['sdk_metadata_ref']['sha256']:
            raise Conflict('Native task metadata exceeds the bounded tool result')
        return {'frame': frame, 'sdk_metadata': json.loads(metadata), 'input_authorized': False,
            'observation_is_task_acceptance': False}

    def input(self, arguments, binding):
        ident, record = self._bound(binding)
        qualification = self.authority.qualification(record['request']['context_ref'])
        action = arguments['action']
        if (qualification is None or qualification['qualification_ref'] != record.get('qualification_ref')
                or action.get('kind') not in qualification['effects']):
            raise Conflict('Native input route qualification changed')
        # Never let the model select a new foreground route after a background
        # failure. Delivery is frozen in this qualified per-message method.
        if action.get('delivery_mode') != qualification['delivery'].lower():
            raise Conflict('Native action delivery differs from its authorized route')
        permitted = self.intents.prepare_input({'operation_id': arguments['operation_id'],
            'grant_ref': record['grant_ref'], 'session_ref': record['session_ref'],
            'target_ref': record['scope']['target_ref'], 'frame_ref': arguments['frame_ref'], 'action': arguments['action']})
        result = self.intents.execute_input(permitted['input_ref'])
        sdk = result.get('sdk_receipt', {})
        if sdk.get('status') != 'RETURNED_REOBSERVATION_REQUIRED' or sdk.get('sdk_effect') != 'CONFIRMED':
            record.update(status='AWAITING_VERIFICATION', task_completed=False, automatic_retry=False)
            self._save(ident, record)
        return result

    def target_authority(self, *args): return self.authority.target_authority(*args)
    def control_guard(self, *args): return self.intents.composite_dispatch_guard(*args)
    def revoke_queued(self, grant): return self.adapter().revoke_queued(grant)

    def _terminal_pin_release(self, record, session_receipt):
        """Release only the finished task's fence after independently drained close.

        A terminal model answer, removed grant, or SDK effect alone is insufficient.
        Unknown execution/cancellation/session outcomes keep the existing pin.
        """
        pin = self.authority.qualification_pin
        if pin is None:
            return {'status': 'NOT_PINNED'}
        context = record['request']['context_ref']
        if (record.get('generation') != self.intents.generation
                or self.active_context != context or pin['context_ref'] != context
                or record.get('qualification_pin_sha256') != digest(pin)):
            return {'status': 'RETAINED', 'reason': 'EXACT_CURRENT_TASK_PIN_REQUIRED'}
        if record.get('status') != 'FINISHED':
            return {'status': 'RETAINED', 'reason': 'TASK_TERMINAL_OUTCOME_UNKNOWN'}
        cancellation = self.intents.state['cancellation']
        queue = cancellation.get('queued_inputs', {})
        if (self.intents.state['active_grant'] is not None or self.intents.state['unresolved_execution_refs']
                or cancellation.get('confirmed') is not True or queue.get('status') != 'DRAINED'
                or type(queue.get('discarded_count')) is not int or queue['discarded_count'] < 0):
            return {'status': 'RETAINED', 'reason': 'EXACT_OWNED_CANCELLATION_AND_QUEUE_DRAIN_REQUIRED'}
        if session_receipt.get('status') != 'CLOSED' or session_receipt.get('session_ref') != record.get('session_ref'):
            return {'status': 'RETAINED', 'reason': 'EXACT_TASK_SESSION_CLOSE_UNCONFIRMED'}
        return {'status': 'RELEASED', 'reason': 'FINISHED_OWNED_TASK_DRAINED_AND_SESSION_CLOSED'}

    async def submit(self, body, token):
        body = NativeTaskInput.model_validate(body).model_dump()
        self.authority.check_host(token)
        if self.closed: raise Conflict('Native task service closed')
        if body['context_ref'] is None: raise Conflict('A native action needs its exact current-page context')
        with self.authority.lock:
            if self._path(body['operation_id']).exists():
                prior = self._read(body['operation_id'])
                if prior['request'] != body: raise Conflict('Native task operation binds another message')
                return self.get(body['operation_id'])
            if any(not task.done() for task in self.tasks.values()):
                raise Conflict('An exact native task is already active; revoke it before another command')
            if self.authority.qualification_pin is not None:
                raise Conflict('Previous native task qualification fence remains unresolved')
            self.authority.context(body['context_ref'])
            self.active_context = body['context_ref']
            recorded = self.intents.record_message({k: body[k] for k in ('operation_id', 'raw_text', 'context_ref')}, host_token=token)
            record = {'request': body, 'message_ref': recorded['message_ref'], 'status': 'INPUT_NOT_QUALIFIED',
                'generation': self.intents.generation, 'submitted_intent_epoch': self.intents.state['epoch'],
                'execution_ref': None, 'grant_ref': None, 'session_ref': None, 'task_completed': False}
            if self.capability()['desktop_input_routes_qualified']:
                record['status'] = 'INTERPRETING' if body['provider_profile_ref'] is not None else 'NOT_CONFIGURED'
            self._save(body['operation_id'], record)
        if record['status'] == 'INTERPRETING':
            self.tasks[body['operation_id']] = asyncio.create_task(self.run(body['operation_id'], token))
        return self.get(body['operation_id'])

    def _request(self, record, mode, schema, methods):
        body = record['request']
        return {'project_id': None, 'operation_id': body['operation_id'], 'prompt': body['raw_text'], 'mode': mode,
            'desktop_intent': {'user_message_ref': record['message_ref'], 'context_ref': body['context_ref']},
            'provider_profile_ref': body['provider_profile_ref'],
            'budget': {'max_calls': 8, 'timeout_seconds': 180, 'max_output_tokens': 4096, 'max_tool_calls': 8},
            'output_schema': schema, 'method_ids': methods}

    async def run(self, ident, token):
        session = None
        try:
            record = self._read(ident)
            intent_request = self._request(record, 'INTENT', DesktopIntentCandidate.model_json_schema(), ['read_message', 'read_context'])
            # A separate operation key prevents an ACTION request from replacing
            # the immutable INTENT request. Both run the original CLI loop.
            intent_request['operation_id'] = 'intent-' + digest(ident)[:40]
            row = self.transport.prepare(intent_request)
            record['intent_execution_ref'] = self._execution(row)
            self._save(ident, record)
            row = await self.transport.start(row['project_id'], row['id'])
            if self.closed or record['submitted_intent_epoch'] != self.intents.state['epoch']:
                raise Conflict('Native task was revoked during readonly interpretation')
            interpreted = self.intents.interpret('interpret-' + digest(ident)[:40], record['message_ref'], self._execution(row), host_token=token)
            authorized = self.intents.authorize('authorize-' + digest(ident)[:40], interpreted['interpretation_ref'], host_token=token)
            if authorized['status'] != 'SINGLE_COMMAND_AUTHORIZED':
                record.update(status=authorized['status'], interpretation=interpreted, authorization=authorized)
                self._save(ident, record); return
            qualification = self.authority.qualification(record['request']['context_ref'])
            if qualification is None: raise Conflict('Native route qualification changed')
            self.authority.pin_qualification(record['request']['context_ref'], qualification)
            method = self._mutation_method(qualification)
            request = self._request(record, 'ACTION', ANSWER_SCHEMA, ['read_message', 'read_context', 'native_task_observe', method])
            request.update(self.intents.execution_envelope(authorized['authorization_ref']))
            row = self.transport.prepare(request)
            execution = self._execution(row)
            bound = self.intents.bind_execution('bind-' + digest(ident)[:40], authorized['authorization_ref'], execution,
                self.intents.state['epoch'], host_token=token)
            if bound['status'] != 'TASK_AUTHORIZED': raise Conflict('Exact native task could not bind input')
            policy = self.intents._get('authorizations', authorized['authorization_ref'])['policy']
            policy_ref = self.policy_writer(copy.deepcopy(record['request']), copy.deepcopy(execution), copy.deepcopy(policy))
            allow_foreground = False
            if qualification['delivery'] == 'FOREGROUND':
                if (record['request']['foreground_permission_ref'] is None or not callable(self.foreground_checker)
                        or self.foreground_checker(row, self.transport.methods[method][0], bound['grant_ref']) is not True):
                    record.update(status='NEEDS_FOREGROUND_HANDOFF', execution_ref=execution, grant_ref=bound['grant_ref'])
                    self._save(ident, record)
                    await self.intents.revoke('handoff-' + digest(ident)[:40], self.intents.state['epoch'], host_token=token)
                    return
                allow_foreground = True
            sdk_binding = self.authority.binding(record['request']['context_ref'], execution, policy_ref=policy_ref,
                max_actions=policy['max_actions'], ttl_seconds=policy['ttl_seconds'], allow_foreground=allow_foreground)
            opening = asyncio.create_task(asyncio.to_thread(self.adapter().open_session, sdk_binding))
            try:
                opened = await asyncio.shield(opening)
            except asyncio.CancelledError:
                # Cancelling an await cannot unsend SDK session creation. Wait
                # for its owned receipt and close exactly that task session in
                # finally; never close the companion's readonly controller.
                opened = await opening
                session = opened['session_ref']; self.sessions[ident] = session
                raise
            session = opened['session_ref']; self.sessions[ident] = session
            with self.authority.lock:
                if self.closed: raise Conflict('Native task service closed during SDK session admission')
                self.intents._grant(bound['grant_ref'])
                pinned_sha256 = digest(self.authority.qualification_pin)
            record.update(status='RUNNING', scope=bound.get('scope') or self.intents.execution_scope(authorized['authorization_ref']),
                execution_ref=execution, grant_ref=bound['grant_ref'], session_ref=session,
                qualification_ref=qualification['qualification_ref'], qualification_pin_sha256=pinned_sha256)
            self._save(ident, record)
            row = await self.transport.start(row['project_id'], row['id'], grant_ref=bound['grant_ref'])
            outcome = await self.intents.finish(execution, host_token=token)
            record = self._read(ident)
            native = json.loads(row['result']) if isinstance(row.get('result'), str) else row.get('result')
            answer = native.get('candidate_json') if isinstance(native, dict) else None
            if row['execution_status'] == 'RETURNED' and isinstance(answer, dict):
                from jsonschema import Draft202012Validator
                Draft202012Validator(ANSWER_SCHEMA).validate(answer)
                record['answer'] = {'text': answer['text'], 'questions': answer['questions'],
                    'original_execution_ref': execution, 'is_goal_verification': False}
            record.update(status='FINISHED', task_outcome=outcome, task_completed=outcome.get('task_completed') is True,
                original_terminal_status=row['execution_status'])
            self._save(ident, record)
        except (Exception, asyncio.CancelledError) as error:
            record = self._read(ident)
            diagnostic = self.intents._put('native-task-failures', {'operation_id': ident,
                'error_type': type(error).__name__, 'traceback': ''.join(traceback.format_exception(
                    type(error), error, error.__traceback__))[-32000:]})
            record.update(status='UNKNOWN_OUTCOME', error_type=type(error).__name__, task_completed=False,
                automatic_retry=False, diagnostic_ref=diagnostic)
            self._save(ident, record)
            if self.intents.state['active_grant']:
                try: await self.intents.revoke('failed-' + digest(ident)[:40], self.intents.state['epoch'], host_token=token)
                except Exception: pass  # Exact unknown cancellation stays in the existing authority.
            if isinstance(error, asyncio.CancelledError): raise
        finally:
            closed_session = {'status': 'NOT_OPENED'}
            if session is not None:
                try:
                    closed_session = await asyncio.to_thread(self.adapter().close_session, session)
                    if (not isinstance(closed_session, dict) or closed_session.get('status') != 'CLOSED'
                            or closed_session.get('session_ref') != session):
                        raise Conflict('Exact owned task SDK session close is unconfirmed')
                    self.sessions.pop(ident, None)
                except Exception as error:
                    closed_session = {'status': 'CLOSE_UNKNOWN', 'session_ref': session,
                        'error_type': type(error).__name__, 'automatic_input_retry': False}
            self._session_close_outcomes[ident] = copy.deepcopy(closed_session)
            try:
                with self.authority.lock:
                    record = self._read(ident)
                    release = self._terminal_pin_release(record, closed_session)
                    record.update(task_session_close=closed_session, qualification_pin_release=release)
                    self._save(ident, record)
                    # Durable outcomes precede removing the in-memory fence.
                    if release['status'] == 'RELEASED':
                        self.authority.qualification_pin = None
            except Exception as error:
                self._session_close_outcomes[ident]['bookkeeping_error_type'] = type(error).__name__

    def get(self, ident=None):
        state = {'type': 'NATIVE_TASK', 'capabilities': self.capability(), 'control': self.intents.get(),
            'readonly_questions_preserved': True, 'automatic_input_authorized': False}
        if ident is not None:
            record = self._read(ident)
            current = record.get('generation') == self.intents.generation
            if not current and record.get('status') in {'INTERPRETING', 'RUNNING', 'AWAITING_VERIFICATION'}:
                record = {**record, 'status': 'UNKNOWN_OUTCOME', 'automatic_retry': False, 'task_completed': False}
            state.update({k: copy.deepcopy(value) for k, value in record.items() if k != 'request'})
            state['record_origin'] = 'CURRENT_HOST' if current else 'HISTORICAL_READONLY'
        return state

    async def revoke(self, body, token):
        body = NativeTaskRevokeInput.model_validate(body).model_dump()
        result = await self.intents.revoke(body['operation_id'], body['expected_intent_epoch'], host_token=token)
        # An INTENT execution has no input grant yet. Cancel only its exact
        # already-owned original execution; the epoch fence also prevents a
        # late interpretation from creating a new mutation grant.
        path = self.authority._path('revocations', digest(body) + '.json')
        with self.authority.lock:
            if path.exists():
                prior = self.authority._read(path, authority=False)
                if prior.get('request') != body: raise Conflict('Exact native revocation bytes changed')
                return {**result, 'readonly_intent_cancellation': prior['outcomes'],
                    'readonly_intent_cancellation_status': prior['status']}
            self.authority._write(path, {'request': body, 'status': 'UNKNOWN_OUTCOME', 'outcomes': []})
        outcomes = []
        for ident, task in self.tasks.items():
            if task.done(): continue
            record = self._read(ident)
            ref = record.get('intent_execution_ref')
            if record.get('generation') != self.intents.generation or record.get('execution_ref') is not None or not ref:
                continue
            try:
                row = self.transport.inspect(ref['project_id'], ref['execution_id'])
                if self._execution(row) != ref: raise Conflict('Readonly intent cancellation execution changed')
                if row['execution_status'] not in {'RETURNED', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'}:
                    row = await self.transport.request_cancel(ref['project_id'], ref['execution_id'])
                outcomes.append({'execution_ref': ref, 'confirmed': self._execution(row) == ref
                    and row['execution_status'] in {'RETURNED', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'},
                    'status': row['execution_status']})
            except Exception as error:
                outcomes.append({'execution_ref': ref, 'confirmed': False, 'status': 'UNKNOWN_OUTCOME',
                    'error_type': type(error).__name__})
        status = 'CONFIRMED' if all(outcome['confirmed'] for outcome in outcomes) else 'UNKNOWN_OUTCOME'
        self.authority._write(path, {'request': body, 'status': status, 'outcomes': outcomes})
        return {**result, 'readonly_intent_cancellation': outcomes, 'readonly_intent_cancellation_status': status}

    async def close(self):
        if self._close_receipt is not None:
            return copy.deepcopy(self._close_receipt)
        self.closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned())
        # HTTP cancellation cannot abandon the one owner's resource cleanup.
        return copy.deepcopy(await asyncio.shield(self._close_task))

    async def _close_owned(self):
        outcomes = {}
        pending = [task for task in self.tasks.values() if not task.done()]
        if self.intents.state['active_grant'] or self.intents.state['unresolved_execution_refs'] or pending:
            try:
                outcomes['revocation'] = await self.revoke({'operation_id': 'service-close-' + uuid.uuid4().hex,
                    'expected_intent_epoch': self.intents.state['epoch']}, self.companion.authority.token)
            except Exception as error:
                outcomes['revocation'] = {'status': 'UNKNOWN_OUTCOME', 'error_type': type(error).__name__}
        for task in self.tasks.values():
            if not task.done(): task.cancel()
        try:
            await self.transport.close()
            outcomes['original_transport'] = {'status': 'CLOSED'}
        except Exception as error:
            outcomes['original_transport'] = {'status': 'CLOSE_UNKNOWN', 'error_type': type(error).__name__}
        pending = [task for task in self.tasks.values() if not task.done()]
        settled = await asyncio.gather(*pending, return_exceptions=True) if pending else []
        errors = [type(value).__name__ for value in settled
                  if isinstance(value, BaseException) and not isinstance(value, asyncio.CancelledError)]
        cancellation = copy.deepcopy(self.intents.state['cancellation'])
        revocation = outcomes.get('revocation', {})
        sessions = copy.deepcopy(self._session_close_outcomes)
        unknown = (self.intents.state['active_grant'] is not None
            or bool(self.intents.state['unresolved_execution_refs']) or cancellation.get('confirmed') is not True
            or outcomes['original_transport']['status'] != 'CLOSED' or bool(errors)
            or revocation.get('status') == 'UNKNOWN_OUTCOME'
            or revocation.get('readonly_intent_cancellation_status') == 'UNKNOWN_OUTCOME'
            or any(item.get('status') == 'CLOSE_UNKNOWN' or item.get('bookkeeping_error_type') for item in sessions.values()))
        receipt = {'status': 'CLOSE_UNKNOWN' if unknown else 'CLOSED', 'owner': 'NATIVE_TASK_SERVICE',
            'generation': self.intents.generation, 'outcomes': {**outcomes, 'task_sessions': sessions,
                'task_errors': errors, 'cancellation': cancellation}, 'readonly_controller_closed': False,
            'qualification_pin_retained': self.authority.qualification_pin is not None,
            'automatic_input_retry': False}
        try:
            self.authority._write(self.authority._path('owner-close', self.intents.generation + '.json'), receipt)
        except Exception as error:
            receipt.update(status='CLOSE_UNKNOWN', receipt_error_type=type(error).__name__)
        self._close_receipt = copy.deepcopy(receipt)
        # Companion, not this service, owns readonly session/controller shutdown.
        return receipt


def register_native_task_routes(app, *, companion, qualification_reader=None, authorizer=None,
                               policy_writer=None, result_checker=None, foreground_checker=None, service=None):
    if getattr(app.state, 'native_tasks', None) is not None: return app.state.native_tasks
    value = service or NativeTasks(app, companion=companion, qualification_reader=qualification_reader,
        authorizer=authorizer, policy_writer=policy_writer, result_checker=result_checker,
        foreground_checker=foreground_checker)
    app.state.native_tasks = value
    def host(x_eeas_desktop_host: str | None = Header(None)):
        companion.authority.check_host(x_eeas_desktop_host)
        return x_eeas_desktop_host
    root = '/api/desktop-host/native-tasks'
    @app.get(root + '/get')
    def get(_token=Depends(host)): return value.get()
    @app.get(root + '/{ident}')
    def task(ident: Id, _token=Depends(host)): return value.get(ident)
    @app.post(root + '/submit')
    async def submit(body: NativeTaskInput, token=Depends(host)): return await value.submit(body, token)
    @app.post(root + '/revoke')
    async def revoke(body: NativeTaskRevokeInput, token=Depends(host)): return await value.revoke(body, token)
    return value
