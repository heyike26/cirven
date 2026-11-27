"""Controlled transport/ABI counters only; no native driver/session/GUI."""
import asyncio
import base64
from contextlib import contextmanager
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from PIL import Image
from packages.adapters.cua_desktop_input import CuaDesktopInput, CuaInputError, _NativeSession


SURFACE = {'id': 'surface', 'revision': 1, 'sha256': 'a' * 64}
TARGET = {'id': 'target', 'revision': 1, 'sha256': 'b' * 64}
LEASE = {'id': 'lease', 'revision': 1, 'sha256': 'c' * 64}
EXEC = {'project_id': 'actual-project', 'execution_id': 'original-loop', 'request_sha256': 'd' * 64}
BINDING = {'surface_ref': SURFACE, 'execution_ref': EXEC, 'session_label': 'original-session-1',
           'budget': {'max_actions': 3, 'timeout_ms': 1000, 'max_frame_age_ms': 5000,
                      'ttl_seconds': 300, 'allow_foreground': False}}
CLICK = {'kind': 'CLICK', 'x': 2, 'y': 2, 'delivery_mode': 'background'}


class Session:
    def __init__(self, fixture):
        self.f = fixture
        self.closed = False
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append(('capture', name, json.loads(arguments), threading.get_ident()))
        self.f.assertTrue(self.f.observing)
        if self.f.capture_hook:
            self.f.capture_hook()
        return {'is_error': self.f.capture_error, 'error_code': 'capture-refused' if self.f.capture_error else None,
            'degraded': False, 'structured_json': self.f.metadata_override if self.f.metadata_override is not None else json.dumps(
                {'pid': self.f.target.get('pid'), 'window_id': self.f.target.get('window_id'),
                 'capture_id': 'actual-sdk-capture-id' if self.f.capture_id else None,
                 'display': self.f.target.get('display_id')}),
            'images': [{'mime_type': 'image/png', 'data_base64': self.f.png}]}

    async def action(self, name, args):
        self.calls.append(('action', name, copy.deepcopy(args), threading.get_ident()))
        self.f.assertTrue(self.f.guarding)
        self.f.action_started.set()
        if self.f.block_action:
            while not self.f.release_action.is_set():
                await asyncio.sleep(.005)
        if self.f.action_error:
            raise OSError('native outcome unavailable')
        return {'is_error': self.f.refusal, 'error_code': 'background_unavailable' if self.f.refusal else None,
                'action': {'effect': 'UNKNOWN' if self.f.refusal else 'CONFIRMED'}, 'images': []}

    def close(self):
        self.closed = True
        self.calls.append(('close', threading.get_ident()))


class Factory:
    def __init__(self, fixture):
        self.f = fixture
        self.calls = []

    async def open(self, binding):
        self.calls.append(('open', threading.get_ident()))
        self.session = Session(self.f)
        return self.session

    async def shutdown(self):
        self.calls.append(('shutdown', threading.get_ident()))


class CuaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.factory = Factory(self)
        self.target = {'kind': 'WINDOW', 'pid': 42, 'window_id': 199, 'process_creation': 123456,
            'identity_sha256': 'e' * 64, 'self_target': False,
            'bounds': {'x': 10, 'y': 20, 'width': 8, 'height': 6}}
        output = BytesIO()
        image = Image.new('RGB', (8, 6), (30, 100, 200))
        image.putpixel((1, 1), (255, 10, 10)); image.save(output, 'PNG')
        self.raw = output.getvalue(); self.png = base64.b64encode(self.raw).decode()
        self.observing = self.guarding = False
        self.capture_error = self.action_error = self.refusal = False
        self.capture_id = True; self.capture_hook = None
        self.metadata_override = None
        self.block_action = False
        self.now = time.time()
        self.action_started, self.release_action = threading.Event(), threading.Event()
        self.lock = threading.RLock()
        self.owner = 'AGENT'; self.epoch = 2; self.scope_execution = copy.deepcopy(EXEC)

        @contextmanager
        def observe(surface, epoch):
            self.assertEqual(surface, SURFACE); self.assertEqual(epoch, 1)
            self.observing = True
            try: yield
            finally: self.observing = False

        @contextmanager
        def guard(lease, surface, input_epoch, control_epoch):
            with self.lock:
                if self.owner != 'AGENT' or control_epoch != self.epoch or lease != LEASE:
                    raise CuaInputError('CONTROL_GUARD_REFUSED')
                self.assertEqual(surface, SURFACE); self.assertEqual(input_epoch, 1)
                self.guarding = True
                try: yield {'execution_ref': copy.deepcopy(self.scope_execution)}
                finally: self.guarding = False

        self.adapter = CuaDesktopInput(data_dir=Path(self.tmp.name), session_factory=self.factory,
            target_authority=lambda ref, surface, epoch: copy.deepcopy(self.target),
            observation_guard=observe, control_guard=guard, frame_validator=self.fixture_pixel_proof,
            platform='TEST_TRANSPORT', clock=lambda: self.now)
        self.session = self.adapter.open_session(BINDING)['session_ref']

    def tearDown(self):
        self.release_action.set()
        self.adapter.shutdown()
        self.tmp.cleanup()

    def capture(self):
        return self.adapter.capture(self.session, TARGET, SURFACE, 1)

    def fixture_pixel_proof(self, png, target, _metadata):
        """Witness for these exact local counter-fixture bytes, not GUI qualification."""
        self.assertEqual(png, self.raw)
        return {'status': 'VERIFIED_TARGET_PIXELS', 'image_sha256': hashlib.sha256(png).hexdigest(),
            'target_identity_sha256': target['identity_sha256'], 'target_bounds': copy.deepcopy(target['bounds']),
            'evidence_ref': {'id': 'original-counter-frame', 'revision': 1, 'sha256': hashlib.sha256(png).hexdigest()},
            'capture_method': 'QUALIFIED_EXACT_FRAME', 'self_exclusion_verified': True}

    def act(self, frame, action=CLICK, operation='act-1', **override):
        return self.adapter.act(self.session, TARGET, frame['frame_ref'], action,
            lease_ref=override.get('lease_ref', LEASE), surface_ref=SURFACE,
            input_epoch=1, control_epoch=override.get('control_epoch', 2), operation_id=operation)

    def test_capture_original_bytes_and_opaque_refs(self):
        frame = self.capture()
        self.assertEqual((Path(self.tmp.name) / frame['image_ref']['path']).read_bytes(), self.raw)
        self.assertEqual((frame['width'], frame['height']), (8, 6))
        self.assertIn('NOT_ACCEPTED_ENGINEERING_FACT', frame['status'])
        self.assertNotIn('pid', frame)
        metadata = json.loads((Path(self.tmp.name) / frame['sdk_metadata_ref']['path']).read_bytes())
        self.assertEqual(metadata['capture_id'], 'actual-sdk-capture-id')
        self.assertEqual(self.factory.session.calls[0][2]['max_image_dimension'], 0)

    def test_single_thread_loop_guards_and_one_original_action(self):
        frame = self.capture(); result = self.act(frame)
        self.assertEqual(result['status'], 'RETURNED_REOBSERVATION_REQUIRED')
        self.assertFalse(result['task_completed'])
        self.assertEqual(self.factory.session.calls[1][2]['target'], {'kind': 'window', 'pid': 42, 'window_id': 199})
        ids = [self.factory.calls[0][1], self.factory.session.calls[0][-1], self.factory.session.calls[1][-1]]
        self.assertEqual(len(set(ids)), 1)
        self.assertNotEqual(ids[0], threading.get_ident())

    def test_exact_idempotent_result_does_not_repeat_native_post(self):
        frame = self.capture(); self.act(frame); replay = self.act(frame)
        self.assertTrue(replay['replayed'])
        self.assertEqual(len([c for c in self.factory.session.calls if c[0] == 'action']), 1)
        with self.assertRaisesRegex(CuaInputError, 'IDEMPOTENCY_CONFLICT'):
            self.act(frame, dict(CLICK, x=3))

    def test_frame_is_consumed_once_and_requires_reobservation(self):
        frame = self.capture(); self.act(frame)
        with self.assertRaisesRegex(CuaInputError, 'CONSUMED'):
            self.act(frame, operation='act-2')

    def test_control_guard_denies_user_and_stale_epoch_before_sdk(self):
        frame = self.capture(); self.owner = 'USER'
        with self.assertRaisesRegex(CuaInputError, 'CONTROL_GUARD_REFUSED'):
            self.act(frame)
        self.owner = 'AGENT'
        with self.assertRaisesRegex(CuaInputError, 'CONTROL_GUARD_REFUSED'):
            self.act(frame, control_epoch=1)
        self.assertFalse(any(c[0] == 'action' for c in self.factory.session.calls))

    def test_execution_binding_cannot_change_via_control_lease(self):
        frame = self.capture(); self.scope_execution['execution_id'] = 'other-original-loop'
        with self.assertRaisesRegex(CuaInputError, 'ORIGINAL_EXECUTION'):
            self.act(frame)

    def test_target_reuse_or_bounds_change_rejects_old_frame(self):
        frame = self.capture(); self.target['process_creation'] += 1
        with self.assertRaisesRegex(CuaInputError, 'CURRENT_SESSION_TARGET_FRAME'):
            self.act(frame)

    def test_self_target_never_captured(self):
        self.target['self_target'] = True
        with self.assertRaisesRegex(CuaInputError, 'NON_SELF_TARGET'):
            self.capture()
        self.assertEqual(self.factory.session.calls, [])

    def test_capture_target_drift_after_sdk_is_not_attested(self):
        self.capture_hook = lambda: self.target.update(process_creation=99999)
        with self.assertRaisesRegex(CuaInputError, 'CAPTURE_TARGET_CHANGED'):
            self.capture()

    def test_missing_capture_id_never_falls_back_to_unbound_click(self):
        self.capture_id = False; frame = self.capture()
        with self.assertRaisesRegex(CuaInputError, 'NO_UNBOUND_COORDINATE_FALLBACK'):
            self.act(frame)
        self.assertFalse(any(c[0] == 'action' for c in self.factory.session.calls))

    def test_changed_disk_frame_fails_before_dispatch(self):
        frame = self.capture(); (Path(self.tmp.name) / frame['image_ref']['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(CuaInputError, 'ARTIFACT_CHANGED'):
            self.act(frame)

    def test_click_bounds_and_nonfinite_do_not_enter_sdk(self):
        frame = self.capture()
        for action in [dict(CLICK, x=8), dict(CLICK, x=-1), dict(CLICK, x=True), dict(CLICK, delivery_mode='auto')]:
            with self.assertRaises(CuaInputError):
                self.act(frame, action)
        self.assertFalse(any(c[0] == 'action' for c in self.factory.session.calls))

    def test_foreground_must_be_pinned_not_silent_escalation(self):
        frame = self.capture()
        with self.assertRaisesRegex(CuaInputError, 'FOREGROUND_PERMISSION'):
            self.act(frame, dict(CLICK, delivery_mode='foreground'))

    def test_refusal_burns_frame_and_does_not_retry_foreground(self):
        frame = self.capture(); self.refusal = True
        result = self.act(frame)
        self.assertEqual(result['sdk_error_code'], 'background_unavailable')
        self.assertFalse(result['task_completed'])
        self.assertEqual(len([c for c in self.factory.session.calls if c[0] == 'action']), 1)
        with self.assertRaisesRegex(CuaInputError, 'CONSUMED'):
            self.act(frame, operation='new-op')

    def test_exception_has_unknown_outcome_and_revokes_session(self):
        frame = self.capture(); self.action_error = True
        result = self.act(frame)
        self.assertEqual(result['status'], 'OUTCOME_UNKNOWN_SESSION_REVOKED')
        self.assertTrue(self.factory.session.closed)
        with self.assertRaisesRegex(CuaInputError, 'OUTCOME_UNKNOWN'):
            self.capture()

    def test_fixed_action_budget_survives_multiple_fresh_frames(self):
        for i in range(3): self.act(self.capture(), operation='op-' + str(i))
        with self.assertRaisesRegex(CuaInputError, 'BUDGET_EXCEEDED'):
            self.act(self.capture(), operation='over-budget')

    def test_single_native_label_never_adopts_previous_lifetime(self):
        self.adapter.close_session(self.session)
        with self.assertRaisesRegex(CuaInputError, 'NO_LIFETIME_ADOPTION'):
            self.adapter.open_session(BINDING)

    def test_human_takeover_waits_original_sdk_await_to_drain(self):
        frame = self.capture(); self.block_action = True
        result, taken = [], threading.Event()
        action_thread = threading.Thread(target=lambda: result.append(self.act(frame)))
        action_thread.start(); self.assertTrue(self.action_started.wait(2))
        def human():
            with self.lock: self.owner = 'USER'; taken.set()
        human_thread = threading.Thread(target=human); human_thread.start()
        self.assertFalse(taken.wait(.05))
        self.release_action.set(); action_thread.join(2); human_thread.join(2)
        self.assertTrue(taken.is_set()); self.assertEqual(len(result), 1)

    def test_threadsafe_revoke_physically_removes_queued_mutation(self):
        frame1, frame2 = self.capture(), self.capture(); self.block_action = True
        results, errors = [], []
        t1 = threading.Thread(target=lambda: results.append(self.act(frame1)))
        t1.start(); self.assertTrue(self.action_started.wait(2))
        def second():
            try: results.append(self.act(frame2, operation='queued-second'))
            except Exception as error: errors.append(str(error))
        t2 = threading.Thread(target=second); t2.start()
        for _ in range(200):
            if self.adapter.worker.jobs.qsize(): break
            time.sleep(.001)
        receipt = self.adapter.revoke_queued({'execution_ref': EXEC})
        self.assertEqual(receipt['discarded_count'], 1)
        self.release_action.set(); t1.join(2); t2.join(2)
        self.assertTrue(any('REVOKED_BEFORE_SDK' in e for e in errors))
        self.assertEqual(len([c for c in self.factory.session.calls if c[0] == 'action']), 1)

    def test_revoked_execution_blocks_new_jobs(self):
        frame = self.capture(); self.adapter.revoke_queued({'execution_ref': EXEC})
        with self.assertRaisesRegex(CuaInputError, 'QUEUE_REVOKED'):
            self.act(frame)

    def test_human_takeover_preserves_readonly_observation(self):
        self.adapter.revoke_queued({'execution_ref': EXEC})
        self.owner = 'USER'
        frame = self.capture()
        self.assertEqual(frame['status'], 'OBSERVATION_NOT_ACCEPTED_ENGINEERING_FACT')
        self.assertFalse(any(c[0] == 'action' for c in self.factory.session.calls))

    def test_text_and_key_use_only_declared_targeted_actions(self):
        self.act(self.capture(), {'kind': 'TYPE_TEXT', 'text': '真实资料与中文'}, operation='text')
        self.act(self.capture(), {'kind': 'PRESS_KEY', 'key': 'ENTER'}, operation='key')
        self.assertEqual([c[1] for c in self.factory.session.calls if c[0] == 'action'], ['type_text', 'press_key'])
        with self.assertRaises(CuaInputError):
            self.act(self.capture(), {'kind': 'PRESS_KEY', 'key': 'ARBITRARY_SYSTEM_COMMAND'}, operation='not-key')

    def test_declared_transport_does_not_claim_native_gui_qualification(self):
        self.adapter.close_session(self.session)
        binding = copy.deepcopy(BINDING); binding['session_label'] = 'new-label'
        value = self.adapter.open_session(binding)
        self.assertEqual(value['transport_kind'], 'INJECTED_TEST_PROVIDER')
        self.assertIn('UNQUALIFIED', value['status'])

    def test_fixed_session_expiry_closes_original_session(self):
        self.now += 301
        with self.assertRaisesRegex(CuaInputError, 'SESSION_EXPIRED'):
            self.capture()
        self.assertTrue(self.factory.session.closed)

    def test_stale_frame_cannot_mutate(self):
        frame = self.capture(); self.now += 6
        with self.assertRaisesRegex(CuaInputError, 'CONSUMED_OR_EXPIRED'):
            self.act(frame)
        self.assertFalse(any(c[0] == 'action' for c in self.factory.session.calls))

    def test_sdk_timeout_revokes_session_without_claiming_stop_or_success(self):
        binding = copy.deepcopy(BINDING); binding['session_label'] = 'short-lived'; binding['budget']['timeout_ms'] = 100
        self.adapter.close_session(self.session); self.session = self.adapter.open_session(binding)['session_ref']
        frame = self.capture(); self.block_action = True
        result = self.act(frame)
        self.assertEqual(result['status'], 'OUTCOME_UNKNOWN_SESSION_REVOKED')
        self.assertFalse(result['task_completed'])
        self.assertTrue(self.factory.session.closed)

    def test_capture_metadata_budget_and_capture_id_are_bounded(self):
        for raw in [' ' * (2 * 1024 * 1024 + 1), json.dumps({'pid': 42, 'window_id': 199, 'capture_id': ' '})]:
            self.metadata_override = raw
            with self.assertRaises(CuaInputError): self.capture()
        self.assertFalse(any(c[0] == 'action' for c in self.factory.session.calls))

    def test_primary_desktop_requires_actual_self_exclusion_attestation(self):
        self.adapter.frame_validator = None
        self.target = {'kind': 'DESKTOP', 'display_id': 'secondary', 'self_target': False,
            'identity_sha256': 'e' * 64, 'bounds': {'x': 0, 'y': 0, 'width': 8, 'height': 6}}
        with self.assertRaisesRegex(CuaInputError, 'PRIMARY_DISPLAY_ONLY'): self.capture()
        self.target['display_id'] = 'primary'
        with self.assertRaisesRegex(CuaInputError, 'SELF_EXCLUSION_VALIDATOR_REQUIRED'): self.capture()
        self.adapter.frame_validator = lambda png, target, metadata: False
        with self.assertRaisesRegex(CuaInputError, 'SELF_EXCLUSION_NOT_VERIFIED'): self.capture()
        self.adapter.frame_validator = lambda png, target, metadata: True
        with self.assertRaisesRegex(CuaInputError, 'SELF_EXCLUSION_NOT_VERIFIED'): self.capture()
        self.adapter.frame_validator = self.fixture_pixel_proof
        frame = self.capture()
        with self.assertRaisesRegex(CuaInputError, 'EXPLICIT_FOREGROUND_NO_FALLBACK'): self.act(frame)
        self.assertFalse(any(c[0] == 'action' for c in self.factory.session.calls))

    def test_invalid_frozen_budgets_and_scope_never_open_factory(self):
        for key, value in [('max_actions', True), ('timeout_ms', 0), ('allow_foreground', 'yes')]:
            binding = copy.deepcopy(BINDING); binding['budget'][key] = value
            with self.assertRaises(CuaInputError): self.adapter.open_session(binding)
        self.assertEqual(len(self.factory.calls), 1)

    def test_shutdown_closes_admission_without_recreating_sdk_session(self):
        self.assertEqual(self.adapter.shutdown()['status'], 'CLOSED')
        binding = copy.deepcopy(BINDING); binding['session_label'] = 'post-shutdown'
        with self.assertRaisesRegex(CuaInputError, 'ADAPTER_CLOSED'): self.adapter.open_session(binding)
        with self.assertRaises(CuaInputError): self.capture()
        self.assertEqual(len([call for call in self.factory.calls if call[0] == 'open']), 1)


if __name__ == '__main__':
    unittest.main()
