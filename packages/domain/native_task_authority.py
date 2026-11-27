"""Exact native-task scope over the existing readonly companion authority.

This module grants no SDK/route qualification. Trusted composition supplies an
actual per-target qualification receipt; plain questions retain their readonly
authority and historical annotations never become input permissions.
"""
import copy
from contextlib import contextmanager

from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import digest
from packages.domain.store import Conflict


class NativeTaskAuthority:
    def __init__(self, base, *, context_reader, qualification_reader=None):
        self.base, self.lock = base, base.lock
        self.reader, self.qualification_reader = context_reader, qualification_reader
        self.contexts, self.targets = {}, {}
        self.qualification_pin = None

    def _path(self, *parts): return self.base._path('native-task-authority', *parts)
    def _read(self, *args, **kwargs): return self.base._read(*args, **kwargs)
    def _write(self, *args, **kwargs): return self.base._write(*args, **kwargs)
    def check_host(self, *args, **kwargs): return self.base.check_host(*args, **kwargs)
    def get(self): return self.base.get()

    def context(self, reference):
        ref = RevisionRef.model_validate(reference).model_dump()
        with self.lock:
            if ref['id'] in self.contexts:
                record = self.contexts[ref['id']]
                if record['context']['context_ref'] != ref:
                    raise Conflict('Native task context reference changed')
                self._current(record)
                return copy.deepcopy(record['context'])
            value = self.reader(ref)
            value = value.get('observation', value) if isinstance(value, dict) else value
            if not isinstance(value, dict) or value.get('context_ref') != ref:
                raise Conflict('Native task needs an exact registered context')
            window_ref = RevisionRef.model_validate(value['window_ref']).model_dump()
            parent = self.base.targets.get(window_ref['id'])
            if not parent or parent['ref'] != window_ref:
                raise Conflict('Native task target is not from the actual readonly host')
            state = self.base.get()
            if (value.get('surface_ref') != state.get('surface_ref') or type(value.get('input_epoch')) is not int
                    or value['input_epoch'] != state.get('input', {}).get('epoch')):
                raise Conflict('Native context belongs to another owned host scope')
            record = {'context': copy.deepcopy(value), 'window': copy.deepcopy(parent['window']),
                'target_ref': window_ref, 'host_scope': copy.deepcopy(state['host_scope']),
                'surface_ref': copy.deepcopy(state['surface_ref']), 'input_epoch': state['input']['epoch']}
            self._current(record)
            self.contexts[ref['id']] = record
            prior = self.targets.get(window_ref['id'])
            if prior and prior['target_ref'] != window_ref:
                raise Conflict('Native task target identity collision')
            self.targets[window_ref['id']] = record
            return copy.deepcopy(value)

    def _current(self, record):
        state = self.base.get()
        if (state.get('status') != 'ACTIVE' or state.get('host_scope') != record['host_scope']
                or not self.base.check_current(record['surface_ref'], record['input_epoch'], host_scope=record['host_scope'])):
            raise Conflict('Native task owning host lifecycle changed')
        source = self.base.window_source
        if not callable(getattr(source, 'inspect', None)):
            raise Conflict('Actual native target inspector unavailable')
        actual = source.inspect(record['window']['window_id'])
        if (actual.get('status') != 'WINDOW_OBSERVED'
                or actual.get('identity_sha256') != record['window']['identity_sha256']
                or any(actual.get(key) != record['window'][key] for key in ('window_id', 'pid', 'process_creation'))):
            raise Conflict('Native task HWND/PID/process birth identity changed')
        # Titles, edit-buffer contents and foreground are mutable observations,
        # not process identity. Cua separately fences each fresh frame/bounds.
        return actual

    def check_context(self, context_ref, target_ref, surface_ref, input_epoch):
        try:
            with self.lock:
                ref = RevisionRef.model_validate(context_ref).model_dump()
                record = self.contexts.get(ref['id'])
                if (not record or record['context']['context_ref'] != ref or record['target_ref'] != target_ref
                        or record['surface_ref'] != surface_ref or record['input_epoch'] != input_epoch):
                    return False
                self._current(record)
                return True
        except Exception:
            return False

    def target_authority(self, reference, surface_ref, input_epoch):
        ref = RevisionRef.model_validate(reference).model_dump()
        with self.lock:
            record = self.targets.get(ref['id'])
            if record is None:
                return self.base.target_authority(ref, surface_ref, input_epoch)
            if record['target_ref'] != ref or record['surface_ref'] != surface_ref or record['input_epoch'] != input_epoch:
                raise Conflict('Native task target differs from its frozen host scope')
            actual = self._current(record)
            return {'kind': 'WINDOW', 'pid': actual['pid'], 'window_id': actual['window_id'],
                'process_creation': actual['process_creation'], 'identity_sha256': actual['identity_sha256'],
                'self_target': False, 'bounds': copy.deepcopy(actual['bounds'])}

    def qualification(self, context_ref):
        """Public data receipt only. No renderer booleans or runtime import proof."""
        with self.lock:
            context = self.context(context_ref)
            record = self.contexts[context_ref['id']]
            if not callable(self.qualification_reader): return None
            value = self.qualification_reader(copy.deepcopy(context))
            if (not isinstance(value, dict) or value.get('qualified') is not True
                    or value.get('target_identity_sha256') != record['window']['identity_sha256']
                    or value.get('delivery') not in {'BACKGROUND', 'FOREGROUND'}
                    or value.get('focus_effect') not in {'NONE', 'POSSIBLE', 'REQUIRED'}
                    or not isinstance(value.get('effects'), list) or not value['effects']
                    or not set(value['effects']) <= {'CLICK', 'TYPE_TEXT', 'PRESS_KEY'}
                    or len(set(value['effects'])) != len(value['effects'])
                    or value.get('owned_queue_drain_qualified') is not True):
                return None
            value = copy.deepcopy(value)
            value['qualification_ref'] = RevisionRef.model_validate(value['qualification_ref']).model_dump()
            pin = self.qualification_pin
            if pin and (pin['context_ref'] != context_ref or pin['receipt_sha256'] != digest(value)):
                return None
            return value

    def pin_qualification(self, context_ref, qualification):
        """Freeze the whole trusted route receipt for one active original task."""
        with self.lock:
            actual = self.qualification(context_ref)
            if actual != qualification:
                raise Conflict('Native task qualification changed before binding')
            self.qualification_pin = {'context_ref': copy.deepcopy(context_ref),
                'receipt_sha256': digest(actual)}

    @contextmanager
    def dispatch_guard(self, surface_ref, input_epoch):
        """Inner host fence; DesktopIntents supplies the exact input permit.

        Readonly base.dispatch_guard deliberately refuses mutations and stays
        unchanged. This separate authority never grants an input on its own.
        """
        with self.lock:
            pin = self.qualification_pin
            if not pin:
                raise Conflict('Native task has no frozen route qualification')
            record = self.contexts[pin['context_ref']['id']]
            if (surface_ref != record['surface_ref'] or type(input_epoch) is not int
                    or input_epoch != record['input_epoch'] or self.qualification(pin['context_ref']) is None):
                raise Conflict('Native task host or actual route qualification changed')
            self._current(record)
            yield {'host_scope': copy.deepcopy(record['host_scope'])}

    def check_input_scope(self, scope, row):
        """Called by existing DesktopIntents in place of obsolete region checks."""
        try:
            with self.lock:
                if not self.check_context(scope['context_ref'], scope['target_ref'], scope['surface_ref'], scope['input_epoch']):
                    return False
                if row.get('execution_status') not in {'QUEUED', 'RUNNING'}:
                    return False
                return self.qualification(scope['context_ref']) is not None
        except Exception:
            return False

    def binding(self, context_ref, execution_ref, *, policy_ref, max_actions, ttl_seconds, allow_foreground=False):
        with self.lock:
            self.context(context_ref)
            record = self.contexts[context_ref['id']]
            if self.qualification(context_ref) is None:
                raise Conflict('Native task input route is not actually qualified for this target')
            from packages.adapters.cua_desktop_input import CuaDesktopInput
            binding = {'runtime_role': 'TASK_INPUT', 'workspace_session_ref': self.base.get()['session_ref'],
                'surface_ref': record['surface_ref'], 'execution_ref': copy.deepcopy(execution_ref),
                'task_policy_ref': copy.deepcopy(policy_ref), 'session_label': 'task-' + digest(execution_ref)[:40],
                'budget': {'max_actions': max_actions, 'timeout_ms': 5000, 'max_frame_age_ms': 15000,
                    'ttl_seconds': ttl_seconds, 'allow_foreground': allow_foreground}}
            return CuaDesktopInput._binding(binding)
