"""Private Main broker for one reviewed Notepad append handoff.

Main owns the native dialog and the one OS activation attempt. This broker
creates neither a controller nor a model loop and never activates a window.
Client/helper success claims cannot replace its independent fresh inspection.
"""
from __future__ import annotations

import copy
import os
from typing import Literal

from fastapi import Cookie, Depends, Header, HTTPException
from pydantic import Field

from apps.api.native_append import NotepadAppendTasks
from apps.api.native_tasks import NativeTaskRevokeInput
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import digest
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict


class MainDialogResult(RequestModel):
    operation_id: Id
    review_ref: RevisionRef
    dialog_request_id: Id
    result: Literal['APPROVE', 'DECLINE', 'CLOSED']


class MainHandoffCommit(RequestModel):
    operation_id: Id
    ticket_ref: RevisionRef
    helper_outcome: Literal['RETURNED', 'OS_REFUSED', 'UNKNOWN']


class MainAppendStart(RequestModel):
    operation_id: Id
    ticket_ref: RevisionRef


TERMINAL = frozenset({'DECLINED', 'CLOSED', 'REVOKED', 'EXPIRED', 'REVIEW_INVALIDATED',
    'APPROVAL_UNKNOWN', 'HANDOFF_UNVERIFIED', 'HANDOFF_UNKNOWN', 'OS_REFUSED', 'START_UNKNOWN'})


class NativeAppendHostBroker:
    def __init__(self, *, service, session_cookie_checker):
        if not isinstance(service, NotepadAppendTasks):
            raise Conflict('Host broker requires the existing single NotepadAppendTasks service')
        if not callable(session_cookie_checker):
            raise Conflict('Trusted session cookie checker is required')
        if getattr(service, '_native_append_host_broker', None) is not None:
            raise Conflict('The single native append service already has its host broker')
        self.service, self.cookie_checker = service, session_cookie_checker
        self.lock, self.generation = service.authority.lock, service.intents.generation
        # Replace only hooks on that one owner, never create a parallel service.
        service.gesture_reader = self.read_gesture
        service.trusted_activator = self.read_handoff
        service._native_append_host_broker = self

    def _host(self, token, cookie):
        self.service.authority.check_host(token)
        if not isinstance(cookie, str) or self.cookie_checker(cookie) is not True:
            raise HTTPException(status_code=401, detail='A trusted local session cookie is required')
        if self.service.closed or self.generation != self.service.intents.generation:
            raise Conflict('Native append host broker lifetime ended')
        self.service.intents._current()

    def _now(self): return int(self.service.clock() * 1000)
    def _path(self, ident): return self.service._append_path('host-broker-operations', ident)
    def _save(self, ident, value): self.service.authority._write(self._path(ident), value)
    def _state(self, ident, *, historical=False):
        path = self._path(ident)
        if not path.exists(): return None
        value = self.service.authority._read(path, authority=not historical)
        if not historical and value['generation'] != self.generation:
            raise Conflict('Historical Main approval cannot authorize a new host')
        return value
    def _put(self, kind, value): return self.service._append_put('host-' + kind, value)
    def _read(self, kind, ref): return self.service._append_read('host-' + kind, ref)

    @staticmethod
    def _target(preview):
        value = preview['baseline']
        return {'window_id_decimal': str(value['window_id']), 'pid': value['pid'],
            'process_creation_decimal': str(value['process_creation']),
            'identity_sha256': value['identity_sha256'], 'edit_window_id_decimal': str(value['edit_window_id'])}

    def _activation_host(self):
        base = self.service.authority.base
        state = base.get()
        if state.get('status') != 'ACTIVE' or not isinstance(base.current, dict):
            raise Conflict('Actual owning Main window lifecycle is unavailable')
        value = base.current['markers']['main']
        return {'window_id_decimal': str(value['window_id']), 'pid': value['pid'],
            'process_creation_decimal': str(value['process_creation']), 'os_session': value['os_session'],
            'window_thread': value['window_thread'], 'window_class': value['window_class'],
            'marker_name': value['marker_name'], 'marker_value_decimal': str(value['marker_value'])}

    def _review_current(self, state, *, foreground=False, approved=False):
        if state['generation'] != self.generation or state['status'] in TERMINAL:
            raise Conflict('Main native append operation is terminal or historical')
        review = self._read('reviews', state['review_ref'])
        if review['expires_unix_ms'] <= self._now():
            raise Conflict('Main native append review expired')
        op = self.service._append_op(review['operation_id'])
        if op['preview_ref'] != review['preview_ref'] or op['status'] == 'REVOKED':
            raise Conflict('Main review no longer binds the current append preview')
        preview = self.service._append_read('previews', op['preview_ref'])
        if not approved and self.service.intents.state['epoch'] != review['expected_intent_epoch']:
            raise Conflict('Main review intent epoch changed')
        snapshot, qualification = self.service._preview_current(preview, foreground=foreground)
        if (preview['intent_epoch'] != review['expected_intent_epoch'] or preview['input_epoch'] != review['input_epoch']
                or qualification['qualification_ref'] != review['qualification_ref']):
            raise Conflict('Main review scope or qualification changed')
        return review, preview, snapshot

    def _public(self, ident, state):
        task_state = self.service.append_get(ident)
        if state['status'] in {'DECLINED', 'CLOSED'}:
            task_state.update(state='APPROVAL_CANCELLED', permission_state='DECLINED', approval_ref=None,
                activation_state='HANDOFF_REQUIRED', automatic_retry=False, task_completed=False)
        elif state['status'] in {'HANDOFF_UNKNOWN', 'APPROVAL_UNKNOWN', 'START_UNKNOWN'}:
            task_state.update(state='UNKNOWN_OUTCOME', automatic_retry=False, task_completed=False)
        elif state['status'] in {'OS_REFUSED', 'HANDOFF_UNVERIFIED'}:
            task_state.update(state='HANDOFF_REQUIRED', activation_state='HANDOFF_REQUIRED', automatic_retry=False)
        elif state['status'] in {'REVIEW_INVALIDATED', 'EXPIRED', 'REVOKED'}:
            task_state.update(state=state['status'], automatic_retry=False, task_completed=False)
        task_state['host_broker_status'] = state['status']
        if state.get('reason'): task_state['reason'] = state['reason']
        result = {'status': state['status'], 'operation_id': ident, 'review_ref': state.get('review_ref'),
            'ticket_ref': state.get('ticket_ref'), 'preview_ref': state.get('preview_ref'),
            'approval_ref': state.get('approval_ref'), 'can_show_dialog': state['status'] == 'REVIEW_READY',
            'automatic_retry': False, 'task_completed': False,
            'task_state': task_state}
        if state.get('reason'): result['reason'] = state['reason']
        if state.get('evidence_ref'): result['evidence_ref'] = state['evidence_ref']
        if state.get('ticket_ref'):
            ticket = self._read('tickets', state['ticket_ref'])
            result.update(ticket=copy.deepcopy(ticket['service_ticket']),
                activation_target=ticket['activation_target'], activation_host=ticket['activation_host'],
                expires_unix_ms=ticket['expires_unix_ms'])
        if self.service._path(ident).exists():
            result['status'] = self.service.get(ident)['status']
            result['task_completed'] = result['task_state'].get('task_completed', False)
        return result

    def actual_review(self, operation_id, token, cookie):
        self._host(token, cookie)
        with self.lock:
            op = self.service._append_op(operation_id, historical=True)
            prior = self._state(operation_id, historical=True)
            if op['generation'] != self.generation or (prior and prior['generation'] != self.generation):
                return {'status': 'HISTORICAL_READONLY', 'operation_id': operation_id, 'can_show_dialog': False,
                    'preview_ref': op['preview_ref'],
                    'automatic_retry': False, 'task_state': self.service.append_get(operation_id)}
            if prior:
                state = self._state(operation_id)
                if state['status'] == 'REVIEW_READY':
                    try: self._review_current(state)
                    except Exception as error:
                        state.update(status='EXPIRED' if 'expired' in str(error).lower() else 'REVIEW_INVALIDATED', reason=str(error)[:300])
                        self._save(operation_id, state)
                result = self._public(operation_id, state)
                if state['status'] == 'REVIEW_READY': result.update(copy.deepcopy(self._read('reviews', state['review_ref'])))
                return result
            preview = self.service._append_read('previews', op['preview_ref'])
            try:
                snapshot, qualification = self.service._preview_current(preview)
                if self.service.intents.state['epoch'] != preview['intent_epoch']:
                    raise Conflict('Append preview intent epoch changed')
            except Exception as error:
                return {'status': 'NOT_ADAPTED', 'reason': str(error)[:300], 'operation_id': operation_id,
                    'preview_ref': op['preview_ref'],
                    'can_show_dialog': False, 'automatic_retry': False, 'task_state': self.service.append_get(operation_id)}
            review = {'operation_id': operation_id, 'generation': self.generation, 'preview_ref': op['preview_ref'],
                'context_ref': preview['request']['context_ref'], 'qualification_ref': qualification['qualification_ref'],
                'target_label': snapshot.get('title', 'Notepad'), 'payload_text': preview['payload_text'],
                'payload_sha256': preview['payload_sha256'], 'baseline_ref': preview['baseline_ref'],
                'baseline': {k: snapshot[k] for k in ('text_sha256', 'utf16_length', 'selection_start', 'selection_end', 'bounds')},
                'activation_target': self._target(preview), 'input_epoch': preview['input_epoch'],
                'expected_intent_epoch': preview['intent_epoch'], 'foreground_required': True,
                'no_selection_overwrite': True, 'no_save_action': True,
                'expires_unix_ms': min(preview['expires_unix_ms'], self._now() + 120000)}
            if preview['request']['provider_profile_ref'] is None:
                return {'status': 'NOT_CONFIGURED', 'operation_id': operation_id, 'can_show_dialog': False,
                    'preview_ref': op['preview_ref'],
                    'automatic_retry': False, 'task_state': self.service.append_get(operation_id)}
            ref = self._put('reviews', review)
            state = {'generation': self.generation, 'status': 'REVIEW_READY', 'review_ref': ref,
                'preview_ref': op['preview_ref'], 'approval_ref': None, 'ticket_ref': None}
            self._save(operation_id, state)
            return {**self._public(operation_id, state), **copy.deepcopy(review)}

    def get(self, operation_id, token, cookie):
        """Readonly recovery; never create/review/approve/activate on refresh."""
        self._host(token, cookie)
        with self.lock:
            state = self._state(operation_id, historical=True)
            if state is None:
                return {'status': 'NO_MAIN_REVIEW', 'operation_id': operation_id,
                    'can_show_dialog': False, 'automatic_retry': False,
                    'task_state': self.service.append_get(operation_id)}
            if state['generation'] != self.generation:
                return {'status': 'HISTORICAL_READONLY', 'operation_id': operation_id,
                    'can_show_dialog': False, 'automatic_retry': False,
                    'task_state': self.service.append_get(operation_id)}
            state = self._state(operation_id)
            return self._public(operation_id, state)

    def dialog_result(self, body, token, cookie):
        self._host(token, cookie)
        body = MainDialogResult.model_validate(body).model_dump()
        ident = body['operation_id']
        with self.lock:
            state = self._state(ident)
            if not state or state['review_ref'] != body['review_ref']:
                raise Conflict('Native dialog result binds another actual review')
            if state.get('dialog_request'):
                if state['dialog_request'] != body:
                    raise Conflict('Native dialog request is already bound to another result')
                return self._public(ident, state)
            if state['status'] != 'REVIEW_READY':
                raise Conflict('Native dialog review is no longer open')
            try: review, preview, snapshot = self._review_current(state)
            except Exception as error:
                state.update(status='EXPIRED' if 'expired' in str(error).lower() else 'REVIEW_INVALIDATED', reason=str(error)[:300], dialog_request=body)
                self._save(ident, state)
                return self._public(ident, state)
            decision = {'request': body, 'generation': self.generation, 'review_ref': state['review_ref'],
                'preview_ref': state['preview_ref'], 'recorded_unix_ms': self._now(),
                'source': 'TRUSTED_MAIN_NATIVE_DIALOG_CALLBACK', 'renderer_boolean_authority': False}
            state.update(dialog_request=body, dialog_ref=self._put('dialogs', decision))
            if body['result'] != 'APPROVE':
                state['status'] = 'DECLINED' if body['result'] == 'DECLINE' else 'CLOSED'
                self._save(ident, state)
                return self._public(ident, state)
            # Durable pending decision precedes calling the existing approver.
            state['status'] = 'APPROVING'; self._save(ident, state)
            try:
                gesture = {'type': 'MAIN_NATIVE_APPEND_USER_GESTURE', 'operation_id': ident,
                    'preview_ref': state['preview_ref'], 'host_scope': preview['host_scope'],
                    'surface_ref': preview['surface_ref'], 'input_epoch': preview['input_epoch'],
                    'intent_epoch': preview['intent_epoch'], 'user_activation': True,
                    'expires_unix_ms': min(preview['expires_unix_ms'], self._now() + 59000)}
                gesture_ref = self._put('gestures', gesture)
                approved = self.service.approve_append({'operation_id': ident, 'preview_ref': state['preview_ref'],
                    'expected_input_epoch': preview['input_epoch'], 'expected_intent_epoch': preview['intent_epoch'],
                    'main_gesture_ref': gesture_ref}, token)
                service_ticket = {'operation_id': ident, 'approval_ref': approved['approval_ref'],
                    'preview_ref': state['preview_ref'], 'target': {k: preview['baseline'][k]
                        for k in ('window_id', 'pid', 'process_creation', 'identity_sha256')},
                    'surface_ref': preview['surface_ref'], 'host_scope': preview['host_scope'],
                    'input_epoch': preview['input_epoch'], 'intent_epoch': preview['intent_epoch'],
                    'payload_sha256': preview['payload_sha256'], 'main_gesture_ref': gesture_ref}
                ticket = {'generation': self.generation, 'service_ticket': service_ticket,
                    'review_ref': state['review_ref'], 'dialog_ref': state['dialog_ref'],
                    'activation_target': self._target(preview), 'activation_host': self._activation_host(),
                    'expires_unix_ms': min(gesture['expires_unix_ms'], review['expires_unix_ms'])}
                state.update(status='APPROVED_HANDOFF_REQUIRED', approval_ref=approved['approval_ref'],
                    ticket_ref=self._put('tickets', ticket))
            except Exception as error:
                state.update(status='APPROVAL_UNKNOWN', reason=str(error)[:300])
            self._save(ident, state)
            return self._public(ident, state)

    def read_gesture(self, reference):
        with self.lock:
            value = self._read('gestures', reference)
            ident = value['operation_id']; state = self._state(ident)
            if (not state or state['status'] not in {'APPROVING', 'APPROVED_HANDOFF_REQUIRED'}
                    or state['preview_ref'] != value['preview_ref'] or value['expires_unix_ms'] <= self._now()):
                raise Conflict('Main gesture is expired or outside its exact approval')
            return copy.deepcopy(value)

    def commit_handoff(self, body, token, cookie):
        self._host(token, cookie)
        body = MainHandoffCommit.model_validate(body).model_dump()
        ident = body['operation_id']
        with self.lock:
            state = self._state(ident)
            if not state or state.get('ticket_ref') != body['ticket_ref']:
                raise Conflict('Main handoff commit binds another exact ticket')
            if state.get('commit_request'):
                if state['commit_request'] != body: raise Conflict('Handoff ticket already has another outcome')
                return self._public(ident, state)
            if state['status'] != 'APPROVED_HANDOFF_REQUIRED':
                raise Conflict('Main handoff ticket is no longer pending')
            ticket = self._read('tickets', body['ticket_ref'])
            state.update(status='COMMITTING_HANDOFF', commit_request=body)
            self._save(ident, state)
            evidence = {'type': 'NATIVE_APPEND_ACTUAL_HANDOFF_OBSERVATION', 'operation_id': ident,
                'ticket_ref': body['ticket_ref'], 'helper_outcome': body['helper_outcome'],
                'generation': self.generation, 'observed_unix_ms': self._now(),
                'helper_claim_is_authority': False, 'sdk_calls': 0, 'model_calls': 0}
            try:
                if ticket['expires_unix_ms'] <= self._now(): raise Conflict('Handoff ticket expired')
                if body['helper_outcome'] != 'RETURNED': raise Conflict('OS helper refused or outcome is unknown')
                review, preview, snapshot = self._review_current(state, foreground=True, approved=True)
                op, actual_preview, approval = self.service._permit(ident)
                if (op['approval_ref'] != state['approval_ref'] or actual_preview != preview
                        or ticket['service_ticket']['approval_ref'] != op['approval_ref']
                        or self.service.intents.state['epoch'] != review['expected_intent_epoch']):
                    raise Conflict('Approved handoff scope/epoch changed')
                # This may succeed in ABI/protocol tests but cannot be reported
                # as real Windows verification on a non-Windows Python host.
                native = os.name == 'nt' and snapshot.get('source') == 'ACTUAL_WIN32_CLASSIC_NOTEPAD_READONLY'
                evidence.update(checks={'exact_identity': snapshot['identity_sha256'] == preview['baseline']['identity_sha256'],
                    'exact_foreground': snapshot['foreground_window_id'] == snapshot['window_id'],
                    'exact_buffer': snapshot['text_sha256'] == preview['baseline']['text_sha256'],
                    'utf16_end': snapshot['selection_start'] == snapshot['selection_end'] == snapshot['utf16_length'],
                    'modifiers_released': snapshot['modifiers_released'] is True,
                    'qualification_current': True, 'actual_windows_inspector': native},
                    observation_source=snapshot.get('source'), actual_text_sha256=snapshot['text_sha256'],
                    target_identity_sha256=snapshot['identity_sha256'], foreground_window_id=snapshot['foreground_window_id'])
                if not all(evidence['checks'].values()): raise Conflict('Actual Windows handoff is not independently verified')
                evidence['status'] = 'ACTUAL_FOREGROUND_INDEPENDENTLY_VERIFIED'
                state['status'] = 'HANDOFF_COMMITTED'
            except Exception as error:
                evidence.update(status='NOT_VERIFIED', reason=str(error)[:300])
                state['status'] = ('OS_REFUSED' if body['helper_outcome'] == 'OS_REFUSED' else
                    'HANDOFF_UNKNOWN' if body['helper_outcome'] == 'UNKNOWN' else
                    'EXPIRED' if 'expired' in str(error).lower() else 'HANDOFF_UNVERIFIED')
                state['reason'] = str(error)[:300]
            state['evidence_ref'] = self._put('handoff-evidence', evidence)
            if state['status'] == 'HANDOFF_COMMITTED':
                receipt = {'status': 'ACTIVATED', 'operation_id': ident, 'approval_ref': state['approval_ref'],
                    'target_identity_sha256': ticket['service_ticket']['target']['identity_sha256'],
                    'foreground_window_id': ticket['service_ticket']['target']['window_id'],
                    'source': 'ACTUAL_MAIN_USER_GESTURE_WIN32_HANDOFF', 'evidence_ref': state['evidence_ref']}
                state['receipt_ref'] = self._put('activation-receipts', receipt)
            self._save(ident, state)
            return self._public(ident, state)

    def read_handoff(self, exact_service_ticket):
        """Original service hook: consume a proven receipt, no OS call/retry."""
        with self.lock:
            ident = exact_service_ticket.get('operation_id')
            state = self._state(ident)
            if not state or state['status'] != 'STARTING' or state.get('activation_receipt_consumed'):
                raise Conflict('No unconsumed independently verified Main handoff exists')
            ticket = self._read('tickets', state['ticket_ref'])
            if ticket['service_ticket'] != exact_service_ticket or ticket['expires_unix_ms'] <= self._now():
                raise Conflict('Activator requested another or expired exact ticket')
            _, _, snapshot = self._review_current(state, foreground=True, approved=True)
            evidence = self._read('handoff-evidence', state['evidence_ref'])
            if (os.name != 'nt' or snapshot.get('source') != 'ACTUAL_WIN32_CLASSIC_NOTEPAD_READONLY'
                    or evidence.get('status') != 'ACTUAL_FOREGROUND_INDEPENDENTLY_VERIFIED'
                    or not isinstance(evidence.get('checks'), dict) or not evidence['checks']
                    or not all(value is True for value in evidence['checks'].values())):
                raise Conflict('Activator receipt has no actual Windows independent evidence')
            receipt = self._read('activation-receipts', state['receipt_ref'])
            if receipt['evidence_ref'] != state['evidence_ref'] or receipt['approval_ref'] != state['approval_ref']:
                raise Conflict('Activator receipt evidence/approval reference differs')
            state['activation_receipt_consumed'] = True
            self._save(ident, state)
            return copy.deepcopy(receipt)

    async def start(self, body, token, cookie):
        self._host(token, cookie)
        body = MainAppendStart.model_validate(body).model_dump(); ident = body['operation_id']
        with self.lock:
            state = self._state(ident)
            if not state or state.get('ticket_ref') != body['ticket_ref']:
                raise Conflict('Start binds another Main handoff ticket')
            if self.service._path(ident).exists(): return self._public(ident, state)
            if state['status'] != 'HANDOFF_COMMITTED':
                raise Conflict('No committed actual handoff exists; unknown/refused tickets never retry')
            state['status'] = 'STARTING'; self._save(ident, state)
            request = {'operation_id': ident, 'preview_ref': state['preview_ref'], 'approval_ref': state['approval_ref']}
        try:
            await self.service.start_append(request, token)
            with self.lock:
                state = self._state(ident)
                state['status'] = 'STARTED' if self.service._path(ident).exists() else 'START_UNKNOWN'
                self._save(ident, state)
        except Exception as error:
            with self.lock:
                state = self._state(ident); state.update(status='START_UNKNOWN', reason=str(error)[:300])
                self._save(ident, state)
        return self._public(ident, state)

    async def revoke(self, body, token, cookie):
        self._host(token, cookie)
        body = NativeTaskRevokeInput.model_validate(body).model_dump()
        result = await self.service.revoke(body, token)
        with self.lock:
            folder = self._path('placeholder').parent
            for path in folder.glob('*.json'):
                state = self.service.authority._read(path, authority=False)
                if state.get('generation') == self.generation:
                    state['status'] = 'REVOKED'; self.service.authority._write(path, state)
        return result


def register_native_append_host_routes(app, *, broker):
    if not isinstance(broker, NativeAppendHostBroker): raise Conflict('Actual native append broker is required')
    if getattr(app.state, 'native_tasks', None) is not broker.service:
        raise Conflict('Host broker must share the registered single native task owner')
    if getattr(app.state, 'native_append_host', None) is not None:
        if app.state.native_append_host is not broker: raise Conflict('Another native append broker already exists')
        return broker
    app.state.native_append_host = broker
    def host(x_eeas_desktop_host: str | None = Header(None), eeas_session: str | None = Cookie(None)):
        broker._host(x_eeas_desktop_host, eeas_session)
        return x_eeas_desktop_host, eeas_session
    root = '/api/desktop-host/native-append-host'
    @app.get(root + '/get')
    def get(operation_id: Id, auth=Depends(host)):
        return broker.get(operation_id, *auth)
    @app.get(root + '/actual-review')
    def review(operation_id: Id, auth=Depends(host)):
        return broker.actual_review(operation_id, *auth)
    @app.post(root + '/dialog-result')
    def dialog(body: MainDialogResult, auth=Depends(host)):
        return broker.dialog_result(body, *auth)
    @app.post(root + '/commit-handoff')
    def commit(body: MainHandoffCommit, auth=Depends(host)):
        return broker.commit_handoff(body, *auth)
    @app.post(root + '/start')
    async def start(body: MainAppendStart, auth=Depends(host)):
        return await broker.start(body, *auth)
    @app.post(root + '/revoke')
    async def revoke(body: NativeTaskRevokeInput, auth=Depends(host)):
        return await broker.revoke(body, *auth)
    return broker
