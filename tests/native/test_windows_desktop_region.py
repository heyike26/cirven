"""Portable authority/epoch counters; no GUI or actual input is synthesized."""
import copy
import unittest

from packages.adapters.windows_desktop_region import (
    DesktopRegionError, RegionAgentInputGate, WindowsDesktopRegionProbe,
    rectangles, surface_reference,
)


REF = {'id': 'surface-real-authority', 'revision': 1, 'sha256': 'a' * 64}
RECTS = [{'x': 0, 'y': 0, 'width': 200, 'height': 1000}]


class FakeNative:
    def __init__(self):
        self.matches = True
        self.affinity_value = 0x11
        self.target = {'hwnd': 22, 'pid': 33, 'creation': 44}
        self.invalid = False
        self.foreground = False
        self.reads = []

    def validate(self, value):
        self.reads.append(value)
        if self.invalid:
            raise DesktopRegionError('MAIN_WINDOW_IDENTITY_CHANGED')

    def equal_region(self, value, rects):
        self.validate(value)
        return {'matches': self.matches, 'window_bounds': {'x': 0, 'y': 0, 'width': 1920, 'height': 1080}, 'dpi': 144}

    def affinity(self, value):
        self.validate(value)
        return self.affinity_value

    def target_at(self, value, point):
        self.validate(value)
        return self.target

    def foreground_matches(self, value):
        self.validate(value)
        return self.foreground


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        self.probe = WindowsDesktopRegionProbe(surface_authority=lambda ref: {'main_nonce': ref['sha256']}, native=self.native)

    def test_truthful_region_proof_and_physical_mapping(self):
        value = self.probe.verify_region(REF, RECTS)
        self.assertTrue(value['verified_region'])
        self.assertEqual(value['dpi'], 144)
        self.assertEqual(value['coordinate_space'], 'WINDOW_PHYSICAL')
        self.assertIn('REQUIRES_HOST_TEST', value['qualification'])
        self.assertNotIn('hwnd', value)

    def test_native_mismatch_is_not_success(self):
        self.native.matches = False
        with self.assertRaisesRegex(DesktopRegionError, 'REGION_DIFFERS'):
            self.probe.verify_region(REF, RECTS)

    def test_affinity_is_not_frame_exclusion_evidence(self):
        value = self.probe.capture_exclusion(REF)
        self.assertTrue(value['verified_affinity_excluded'])
        self.assertEqual(value['frame_self_exclusion'], 'NOT_PROVEN_BY_AFFINITY_ALONE')
        self.native.affinity_value = 1
        self.assertFalse(self.probe.capture_exclusion(REF)['verified_affinity_excluded'])

    def test_underlay_observation_never_grants_action(self):
        result = self.probe.hit_test(REF, {'x': -100, 'y': 30})
        self.assertEqual(result['target'], self.native.target)
        self.assertEqual(result['action_authority'], 'NOT_GRANTED')

    def test_keyboard_proof_requires_actual_own_foreground(self):
        self.assertFalse(self.probe.verify_foreground(REF)['verified_keyboard_owned'])
        self.native.foreground = True
        self.assertTrue(self.probe.verify_foreground(REF)['verified_keyboard_owned'])
        self.native.invalid = True
        with self.assertRaisesRegex(DesktopRegionError, 'IDENTITY_CHANGED'):
            self.probe.verify_foreground(REF)

    def test_main_replacement_fails_every_read(self):
        self.native.invalid = True
        for method, argument in [(self.probe.verify_region, RECTS), (self.probe.hit_test, {'x': 0, 'y': 0})]:
            with self.assertRaisesRegex(DesktopRegionError, 'IDENTITY_CHANGED'):
                method(REF, argument)
        with self.assertRaises(DesktopRegionError):
            self.probe.capture_exclusion(REF)

    def test_reference_validation_survives_optimized_python(self):
        for value in [None, {}, {**REF, 'revision': True}, {**REF, 'hwnd': 99}, {**REF, 'sha256': 'A' * 64}]:
            with self.assertRaises(DesktopRegionError):
                surface_reference(value)

    def test_explicit_region_not_electron_empty_reset(self):
        for value in [[], [dict(RECTS[0], width=0)], [dict(RECTS[0], x=-1)], [dict(RECTS[0], width=True)], RECTS * 257]:
            with self.assertRaises(DesktopRegionError):
                rectangles(value)

    def test_invalid_screen_point_rejected_before_hit_test(self):
        for value in [{}, {'x': True, 'y': 1}, {'x': 70000, 'y': 1}, {'x': 0, 'y': 0, 'hwnd': 4}]:
            with self.assertRaises(DesktopRegionError):
                self.probe.hit_test(REF, value)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.actual = {'identity_tag': 'marked-main:created-at:nonce', 'region_sha256': 'b' * 64,
            'mode': 'DESKTOP_OPEN', 'verified_region': True, 'verified_keyboard_owned': True}
        self.forwarded = True
        self.calls = []
        self.fail = None

        def fence(ref, enabled):
            self.calls.append(enabled)
            if self.fail == 'raise':
                raise OSError('lost main pipe')
            if self.fail == 'false':
                return False
            self.forwarded = enabled
            return True

        self.gate = RegionAgentInputGate(region_authority=lambda ref: copy.deepcopy(self.actual), forwarding_fence=fence)

    def pause(self):
        self.actual['mode'] = 'FULL_WORKSPACE'
        return self.gate.pause_input(REF, 'FULL_WORKSPACE', 1)

    def test_actual_shape_and_agent_dispatch_block_both_required(self):
        receipt = self.pause()
        self.assertEqual(receipt['epoch'], 2)
        self.assertFalse(self.forwarded)
        self.assertTrue(receipt['verified_native_region_covered'])
        self.assertFalse(receipt['verified_native_visibility_suspended'])
        with self.assertRaisesRegex(DesktopRegionError, 'DISPATCH_BLOCKED'):
            self.gate.admit(REF, 2)
        self.actual['mode'] = 'DESKTOP_OPEN'
        resumed = self.gate.resume_input(receipt['pause_ref'], 2)
        self.assertEqual(resumed['epoch'], 3)
        self.assertTrue(self.forwarded)
        self.assertTrue(self.gate.admit(REF, 3)['enabled'])

    def test_unverified_full_shape_does_not_pause_or_claim_human_coverage(self):
        self.actual['mode'] = 'FULL_WORKSPACE'
        self.actual['verified_region'] = False
        with self.assertRaisesRegex(DesktopRegionError, 'ACTUAL_REGION_REQUIRED'):
            self.gate.pause_input(REF, 'FULL_WORKSPACE', 1)
        self.assertEqual(self.calls, [])

    def test_modeless_never_globally_pauses(self):
        self.actual['mode'] = 'MODELESS'
        with self.assertRaisesRegex(DesktopRegionError, 'ACTUAL_REGION_REQUIRED'):
            self.gate.pause_input(REF, 'MODELESS', 1)

    def test_full_region_with_foreign_or_unknown_keyboard_focus_is_not_input_proof(self):
        for actual in [False, None]:
            with self.subTest(keyboard_owned=actual):
                self.setUp()
                self.actual['mode'] = 'FULL_WORKSPACE'
                if actual is None:
                    self.actual.pop('verified_keyboard_owned')
                else:
                    self.actual['verified_keyboard_owned'] = actual
                with self.assertRaisesRegex(DesktopRegionError, 'ACTUAL_FOREGROUND_REQUIRED'):
                    self.gate.pause_input(REF, 'FULL_WORKSPACE', 1)
                self.assertEqual(self.calls, [])

    def test_stale_epoch_does_not_mutate(self):
        self.actual['mode'] = 'FULL_WORKSPACE'
        for epoch in [0, True, 2]:
            with self.assertRaisesRegex(DesktopRegionError, 'STALE_INPUT_EPOCH'):
                self.gate.pause_input(REF, 'FULL_WORKSPACE', epoch)
        self.assertEqual(self.calls, [])

    def test_delayed_resume_cannot_restore_different_window(self):
        receipt = self.pause()
        self.actual['identity_tag'] = 'replacement-main'
        self.actual['mode'] = 'DESKTOP_OPEN'
        with self.assertRaisesRegex(DesktopRegionError, 'IDENTITY_CHANGED'):
            self.gate.resume_input(receipt['pause_ref'], 2)
        self.assertEqual(self.calls, [False])

    def test_resume_requires_actual_reopened_region(self):
        receipt = self.pause()
        with self.assertRaisesRegex(DesktopRegionError, 'RESTORED_NATIVE_REGION_NOT_VERIFIED'):
            self.gate.resume_input(receipt['pause_ref'], 2)
        self.assertFalse(self.forwarded)

    def test_foreign_and_replayed_receipt_rejected(self):
        receipt = self.pause()
        for foreign in [None, {}, {**receipt['pause_ref'], 'sha256': 'c' * 64}]:
            with self.assertRaises(DesktopRegionError):
                self.gate.resume_input(foreign, 2)
        self.actual['mode'] = 'MODELESS'
        self.gate.resume_input(receipt['pause_ref'], 2)
        with self.assertRaisesRegex(DesktopRegionError, 'REVOKED'):
            self.gate.resume_input(receipt['pause_ref'], 3)

    def test_pause_pipe_error_and_false_are_fail_closed(self):
        for failure in ['raise', 'false']:
            with self.subTest(failure=failure):
                self.setUp()
                self.fail = failure
                with self.assertRaises(DesktopRegionError):
                    self.pause()
                state = self.gate.inspect(REF)
                self.assertEqual(state['status'], 'FAULTED')
                self.assertFalse(state['enabled'])
                with self.assertRaisesRegex(DesktopRegionError, 'DISPATCH_BLOCKED'):
                    self.gate.admit(REF, state['epoch'])

    def test_resume_failure_stays_fenced_and_epoch_advances(self):
        receipt = self.pause()
        self.actual['mode'] = 'DESKTOP_OPEN'
        self.fail = 'false'
        with self.assertRaisesRegex(DesktopRegionError, 'RESTORE_NOT_VERIFIED'):
            self.gate.resume_input(receipt['pause_ref'], 2)
        state = self.gate.inspect(REF)
        self.assertFalse(state['enabled'])
        self.assertEqual(state['epoch'], 3)

    def test_close_revokes_agent_dispatch_without_user_window_mutation(self):
        self.gate.inspect(REF)
        value = self.gate.close_surface(REF)
        self.assertFalse(value['agent_dispatch_enabled'])
        self.assertEqual(self.calls, [False])
        with self.assertRaises(DesktopRegionError):
            self.gate.admit(REF, 2)


if __name__ == '__main__':
    unittest.main()
