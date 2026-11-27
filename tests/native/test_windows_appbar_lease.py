"""Appbar authority/lifecycle counters; no portable result signs Win GUI."""
import copy
import ctypes as ct
from contextlib import contextmanager
import unittest

from packages.adapters.windows_appbar_lease import APPBARDATA, MONITORINFO, AppBarError, WindowsAppBarLease, Win32AppBarAPI
from packages.adapters.windows_desktop_region import DesktopRegionError


REF = {'id': 'workspace-no-project', 'revision': 1, 'sha256': 'a' * 64}
BASE = {'monitor_id': 9, 'monitor_bounds': {'x': -1920, 'y': 0, 'width': 1920, 'height': 1080},
    'work_area': {'x': -1920, 'y': 0, 'width': 1920, 'height': 1040}}
THICK = {'LEFT': 240, 'RIGHT': 320, 'TOP': 40, 'BOTTOM': 0}


def identity(hwnd):
    return {'hwnd': hwnd, 'pid': 77, 'creation': 99000, 'marker_name': 'EEAS-' + str(hwnd), 'marker_value': hwnd + 100}


class FakeAppBarAPI:
    def __init__(self):
        self.registrations, self.bounds = {}, {}
        self.calls = []
        self.invalid_hwnd = None
        self.fail_position = False
        self.fail_register = False
        self.monitor_changed = False
        self.external_change = 0

    def validate(self, item):
        if item['hwnd'] == self.invalid_hwnd:
            raise DesktopRegionError('MAIN_WINDOW_IDENTITY_CHANGED')

    def monitor(self, item):
        self.validate(item)
        result = copy.deepcopy(BASE)
        if self.monitor_changed:
            result['monitor_id'] += 1
        work = result['work_area']
        left, top, right, bottom = work['x'], work['y'], work['x'] + work['width'], work['y'] + work['height']
        for hwnd, edge in self.registrations.items():
            if hwnd not in self.bounds:
                continue
            bar = self.bounds[hwnd]
            if edge == 'LEFT': left = max(left, bar['x'] + bar['width'])
            elif edge == 'RIGHT': right = min(right, bar['x'])
            elif edge == 'TOP': top = max(top, bar['y'] + bar['height'])
            else: bottom = min(bottom, bar['y'])
        result['work_area'] = {'x': left + self.external_change, 'y': top, 'width': right - left - self.external_change, 'height': bottom - top}
        return result

    def register(self, item, callback):
        self.validate(item)
        if self.fail_register:
            raise AppBarError('OWN_APPBAR_REGISTER_FAILED')
        self.registrations[item['hwnd']] = None
        self.calls.append(('NEW', item['hwnd'], callback))

    def position(self, item, edge, thickness, monitor_bounds):
        self.validate(item)
        if self.fail_position:
            raise AppBarError('OWN_APPBAR_SET_POSITION_FAILED')
        work = BASE['work_area']
        bar = dict(work)
        if edge == 'LEFT': bar['width'] = thickness
        elif edge == 'RIGHT': bar.update(x=work['x'] + work['width'] - thickness, width=thickness)
        elif edge == 'TOP': bar['height'] = thickness
        else: bar.update(y=work['y'] + work['height'] - thickness, height=thickness)
        self.registrations[item['hwnd']] = edge
        self.bounds[item['hwnd']] = bar
        self.calls.append(('POSITION', item['hwnd'], edge))
        return dict(bar)

    def remove(self, item):
        self.validate(item)
        if item['hwnd'] not in self.registrations:
            raise AppBarError('OWN_APPBAR_REMOVE_NOT_VERIFIED')
        self.calls.append(('REMOVE', item['hwnd']))
        self.registrations.pop(item['hwnd'])
        self.bounds.pop(item['hwnd'], None)

    def notify(self, item, kind, active):
        self.validate(item)
        self.calls.append(('NOTIFY', item['hwnd'], kind, active))


class AppBarTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAppBarAPI()
        self.authority = {'main': identity(1), 'bars': {edge: identity(i) for i, edge in enumerate(['LEFT', 'RIGHT', 'TOP', 'BOTTOM'], 2)}, 'callback_message': 0x8005}
        self.service = WindowsAppBarLease(authority=lambda ref: copy.deepcopy(self.authority), native=self.api)

    def reserve(self):
        return self.service.reserve(REF, THICK)

    def test_x64_sdk_layout_and_monitor_layout(self):
        self.assertEqual(ct.sizeof(MONITORINFO), 40)
        self.assertEqual(ct.sizeof(APPBARDATA), 48 if ct.sizeof(ct.c_void_p) == 8 else 36)
        self.assertEqual(APPBARDATA.rc.offset, 24 if ct.sizeof(ct.c_void_p) == 8 else 16)

    def test_actual_workarea_must_match_host_center(self):
        result = self.reserve()
        self.assertEqual(result['original_monitor'], BASE)
        self.assertEqual(result['actual_monitor']['work_area'], {'x': -1680, 'y': 40, 'width': 1360, 'height': 1000})
        self.assertFalse(result['visible_all_verified'])
        verified = self.service.verify_center(result['lease_ref'], result['actual_monitor']['work_area'])
        self.assertTrue(verified['visible_all_verified'])
        self.assertEqual(verified['qualifications']['arbitrary_existing_window_reflow'], 'NOT_PROVEN_BY_WORKAREA')
        wrong = dict(result['actual_monitor']['work_area'], width=1359)
        self.assertFalse(self.service.verify_center(result['lease_ref'], wrong)['visible_all_verified'])

    def test_only_positive_edges_register_and_exact_release_restores(self):
        result = self.reserve()
        self.assertEqual(len([c for c in self.api.calls if c[0] == 'NEW']), 3)
        value = self.service.release(result['lease_ref'])
        self.assertEqual(value['status'], 'RELEASED')
        self.assertTrue(value['original_work_area_restored'])
        self.assertEqual(self.api.registrations, {})
        self.assertNotIn(('REMOVE', 1), self.api.calls)

    def test_collapse_to_zero_unregisters_own_edges(self):
        result = self.reserve()
        zero = {edge: 0 for edge in THICK}
        updated = self.service.update(result['lease_ref'], zero, result['revision'], expected_center_physical=BASE['work_area'])
        self.assertTrue(updated['work_area_verified'])
        self.assertEqual(self.api.registrations, {})
        self.assertEqual(updated['accepted_bar_bounds'], {})

    def test_width_change_reflows_without_duplicate_registration(self):
        result = self.reserve()
        expanded = dict(THICK, LEFT=400)
        updated = self.service.update(result['lease_ref'], expanded, result['revision'])
        self.assertEqual(updated['actual_monitor']['work_area']['x'], -1520)
        self.assertEqual(len([c for c in self.api.calls if c[0] == 'NEW']), 3)

    def test_duplicate_surface_reserve_requires_versioned_update(self):
        self.reserve()
        with self.assertRaisesRegex(AppBarError, 'USE_UPDATE'):
            self.reserve()

    def test_stale_revision_rejected_before_native_mutation(self):
        result = self.reserve()
        before = list(self.api.calls)
        for revision in [True, 0, result['revision'] + 1]:
            with self.assertRaisesRegex(AppBarError, 'STALE_APPBAR'):
                self.service.update(result['lease_ref'], THICK, revision)
        self.assertEqual(self.api.calls, before)

    def test_helper_in_foreign_process_rejected_before_registration(self):
        self.authority['bars']['LEFT']['pid'] = 88
        with self.assertRaisesRegex(AppBarError, 'EXACT_MAIN_PROCESS'):
            self.reserve()
        self.assertEqual(self.api.calls, [])

    def test_main_or_duplicate_helper_cannot_be_reserved(self):
        for duplicate in [1, self.authority['bars']['RIGHT']['hwnd']]:
            self.authority['bars']['LEFT']['hwnd'] = duplicate
            with self.assertRaisesRegex(AppBarError, 'DISTINCT_HELPERS'):
                self.reserve()
        self.assertEqual(self.api.calls, [])

    def test_private_callback_range_and_bool_are_rejected(self):
        for value in [1, True, 0xC000]:
            self.authority['callback_message'] = value
            with self.assertRaisesRegex(AppBarError, 'PRIVATE_CALLBACK'):
                self.reserve()

    def test_invalid_or_exhausting_dimensions_do_not_mutate(self):
        for value in [dict(THICK, LEFT=True), dict(THICK, LEFT=-1), {'LEFT': 1}, dict(THICK, RIGHT=1920)]:
            with self.assertRaises(AppBarError):
                self.service.reserve(REF, value)
        self.assertEqual(self.api.calls, [])
        # A rejected oversized request must not mint a lease blocking retry.
        self.assertEqual(self.reserve()['status'], 'RESERVED')

    def test_failed_registration_never_claims_success_and_can_release(self):
        self.api.fail_register = True
        result = self.reserve()
        self.assertEqual(result['status'], 'FAULTED')
        self.assertFalse(result['visible_all_verified'])
        self.assertEqual(self.api.registrations, {})
        released = self.service.release(result['lease_ref'])
        self.assertTrue(released['original_work_area_restored'])

    def test_failed_set_position_preserves_pending_lease_for_exact_cleanup(self):
        self.api.fail_position = True
        result = self.reserve()
        self.assertEqual(result['status'], 'FAULTED')
        self.assertEqual(len(self.api.registrations), 1)
        self.service.release(result['lease_ref'])
        self.assertEqual(self.api.registrations, {})

    def test_lost_helper_not_removed_but_other_own_bars_release(self):
        result = self.reserve()
        self.api.invalid_hwnd = self.authority['bars']['LEFT']['hwnd']
        released = self.service.release(result['lease_ref'])
        self.assertEqual(released['status'], 'FAULTED')
        self.assertFalse(released['original_work_area_restored'])
        self.assertEqual(list(self.api.registrations), [self.api.invalid_hwnd])
        self.assertNotIn(('REMOVE', self.api.invalid_hwnd), self.api.calls)

    def test_replaced_authority_cannot_update_old_helper_set(self):
        result = self.reserve()
        self.authority['bars']['LEFT']['marker_value'] += 1
        before = list(self.api.calls)
        with self.assertRaisesRegex(AppBarError, 'IDENTITY_CHANGED'):
            self.service.update(result['lease_ref'], THICK, result['revision'])
        self.assertEqual(self.api.calls, before)

    def test_monitor_change_requires_release_or_rebind_not_silent_retarget(self):
        result = self.reserve()
        self.api.monitor_changed = True
        with self.assertRaisesRegex(AppBarError, 'MONITOR_CHANGED'):
            self.service.reconcile(result['lease_ref'])

    def test_other_appbar_change_does_not_restore_permanent_baseline(self):
        result = self.reserve()
        self.api.external_change = 25
        value = self.service.release(result['lease_ref'])
        self.assertEqual(value['status'], 'RELEASED')
        self.assertFalse(value['original_work_area_restored'])
        self.assertEqual(value['actual_monitor']['work_area']['x'], BASE['work_area']['x'] + 25)

    def test_abn_reconciliation_does_not_certify_old_center(self):
        result = self.reserve()
        self.api.external_change = 25
        value = self.service.reconcile(result['lease_ref'])
        self.assertFalse(value['visible_all_verified'])
        self.assertEqual(value['revision'], result['revision'] + 1)

    def test_forged_or_released_lease_cannot_update(self):
        result = self.reserve()
        with self.assertRaises(AppBarError):
            self.service.update(dict(result['lease_ref'], sha256='c' * 64), THICK, result['revision'])
        self.service.release(result['lease_ref'])
        with self.assertRaisesRegex(AppBarError, 'RELEASED'):
            self.service.update(result['lease_ref'], THICK, result['revision'] + 1)

    def test_shutdown_only_own_leases(self):
        self.reserve()
        result = self.service.shutdown()
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['original_work_area_restored'])
        self.assertEqual(self.service.shutdown(), [])

    def test_explicit_restart_cleanup_not_automatic_adoption(self):
        result = self.reserve()
        snapshot = self.service.export_state(result['lease_ref'])
        restarted = WindowsAppBarLease(authority=lambda ref: copy.deepcopy(self.authority), native=self.api)
        self.assertEqual(restarted.records, {})
        receipt = restarted.release_owned_state(snapshot)
        self.assertEqual(receipt['status'], 'OWN_STATE_RELEASED')
        self.assertFalse(receipt['adopted_live_authority'])
        self.assertTrue(receipt['original_work_area_restored'])
        self.assertEqual(restarted.records, {})
        fresh = restarted.reserve(REF, THICK)
        self.assertEqual(fresh['original_monitor'], BASE)

    def test_restart_cleanup_rejects_changed_marks_and_corrupt_private_snapshot(self):
        result = self.reserve()
        snapshot = self.service.export_state(result['lease_ref'])
        corrupt = copy.deepcopy(snapshot)
        corrupt['payload']['registered_edges'] = ['BOTTOM']
        with self.assertRaisesRegex(AppBarError, 'PRIVATE_APPBAR_CLEANUP'):
            self.service.release_owned_state(corrupt)
        self.authority['bars']['LEFT']['marker_value'] += 1
        with self.assertRaisesRegex(AppBarError, 'RESTART_OWNERSHIP'):
            self.service.release_owned_state(snapshot)
        self.assertFalse(any(c[0] == 'REMOVE' for c in self.api.calls))


class StockABITests(unittest.TestCase):
    def test_stock_query_set_restore_thickness_and_suppress_own_callback_loop(self):
        calls = []
        class Identity:
            def validate(self, value):
                return {'dpi': 144}
            @contextmanager
            def physical_context(self, value):
                yield
        class Shell:
            def SHAppBarMessage(inner, message, pointer):
                data = pointer._obj
                self.assertEqual(data.cbSize, ct.sizeof(APPBARDATA))
                self.assertEqual(data.hWnd, 2)
                self.assertEqual(data.uEdge, 0)
                calls.append(message)
                if message == 2:
                    # Shell changes the offered rectangle; caller must restore
                    # requested edge thickness before passing SETPOS.
                    data.rc.left, data.rc.top = -1900, 40
                    data.rc.right, data.rc.bottom = -1800, 1040
                if message == 3:
                    self.assertEqual(data.rc.right - data.rc.left, 240)
                return 1
        native = Win32AppBarAPI.__new__(Win32AppBarAPI)
        native.identity, native.shell, native.positions = Identity(), Shell(), {}
        result = native.position(identity(2), 'LEFT', 240, BASE['monitor_bounds'])
        self.assertEqual(result, {'x': -1900, 'y': 40, 'width': 240, 'height': 1000})
        self.assertEqual(calls, [2, 3])
        self.assertEqual(native.position(identity(2), 'LEFT', 240, BASE['monitor_bounds']), result)
        self.assertEqual(calls, [2, 3, 2])


if __name__ == '__main__':
    unittest.main()
