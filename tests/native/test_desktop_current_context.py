"""Fresh page-context controlled transport; no native driver/session/GUI."""
import asyncio
import base64
from contextlib import contextmanager
import copy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from PIL import Image
from packages.adapters.cua_desktop_input import CuaDesktopInput
from packages.adapters.desktop_current_context import DesktopCurrentContext, CurrentContextError, Win32CurrentWindowSource


def ref(name): return {'id': name, 'revision': 1, 'sha256': 'a' * 64}
SURFACE, WORKSPACE, TARGET = ref('surface'), ref('workspace'), ref('target')
EXECUTION = {'project_id': 'project', 'execution_id': 'original-loop', 'request_sha256': 'b' * 64}


class Source:
    def __init__(self):
        self.window = {'status': 'WINDOW_OBSERVED', 'window_id': 42, 'pid': 123, 'process_creation': 500,
            'os_session': 1, 'window_class': 'Chrome_WidgetWin_1', 'window_thread': 9,
            'identity_sha256': 'c' * 64, 'title': 'E09.md — Visual Studio Code', 'visible': True,
            'minimized': False, 'dpi': 192, 'bounds': {'x': 100, 'y': 50, 'width': 8, 'height': 6},
            'coordinate_space': 'PHYSICAL_PIXELS_TEST', 'source': 'INJECTED_TEST_FOREGROUND',
            'page_content_observed': False}
        self.foreground = 'app'
        self.lost = False
        self.inspect_hook = None
        self.closed = False
    def sample(self):
        if self.foreground == 'eeas': return {'status': 'EXCLUDED_EEAS_WINDOW'}
        if self.foreground is None: return {'status': 'NO_FOREGROUND_WINDOW'}
        return copy.deepcopy(self.window)
    def inspect(self, hwnd):
        if self.lost: raise CurrentContextError('NATIVE_WINDOW_LOST')
        if self.inspect_hook: self.inspect_hook()
        return copy.deepcopy(self.window)
    def start(self, callback): self.callback = callback; return {'status': 'TRACKING_TEST_ONLY'}
    def close(self): self.closed = True; return {'status': 'CLOSED'}


class Session:
    def __init__(self, fixture): self.f = fixture; self.closed = False
    async def call_tool(self, name, argument_json):
        args = json.loads(argument_json); self.f.calls.append((name, args, threading.get_ident()))
        self.f.assertTrue(self.f.guarded)
        if self.f.capture_hook: self.f.capture_hook()
        screenshot = args.get('include_screenshot') is True
        data = {'pid': 123, 'window_id': 42, 'title': 'actual observed document', 'snapshot_id': 's1',
            'tree_markdown': self.f.text, 'elements': [{'name': 'E09.md', 'role': 'treeitem'}] if self.f.text else [],
            'elements_complete': self.f.complete, 'capture_id': 'source-frame' if screenshot else None}
        return {'is_error': self.f.sdk_error, 'degraded': False, 'structured_json': json.dumps(data),
            'images': [{'mime_type': 'image/png', 'data_base64': self.f.png}] if screenshot else []}
    def close(self): self.closed = True


class Factory:
    def __init__(self, fixture): self.f = fixture
    async def open(self, binding): self.session = Session(self.f); return self.session
    async def shutdown(self): pass


class CurrentContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Source(); self.lock = threading.RLock(); self.guarded = False
        self.calls, self.capture_hook = [], None
        self.text = '真实 VSCode 当前页面\nE09.md\n待修需求'
        self.complete, self.sdk_error, self.ambiguous = True, False, False
        self.pixel_source = 'EXACT_TARGET_WINDOW'
        output = BytesIO(); Image.new('RGB', (8, 6), (50, 80, 160)).save(output, 'PNG')
        self.png = base64.b64encode(output.getvalue()).decode()
        @contextmanager
        def guard(surface, epoch):
            with self.lock:
                if surface != self.binding['surface_ref'] or epoch != self.binding['input_epoch']:
                    raise CurrentContextError('CURRENT_EPOCH_CHANGED')
                self.guarded = True
                try: yield
                finally: self.guarded = False
        def target(*_):
            return {'kind': 'WINDOW', 'pid': 123, 'window_id': 42, 'process_creation': 500,
                'identity_sha256': 'c' * 64, 'self_target': False, 'bounds': self.source.window['bounds']}
        self.binding = {'workspace_session_ref': WORKSPACE, 'surface_ref': SURFACE, 'input_epoch': 1, 'sdk_session_ref': None}
        self.factory = Factory(self)
        self.cua = CuaDesktopInput(data_dir=Path(self.tmp.name), session_factory=self.factory,
            target_authority=target, observation_guard=guard,
            control_guard=lambda *_: self.fail('readonly context must never enter mutation guard'),
            frame_validator=self.fixture_pixel_proof, platform='TEST_TRANSPORT')
        self.binding['sdk_session_ref'] = self.cua.open_session({'surface_ref': SURFACE, 'execution_ref': None,
            'runtime_role': 'READONLY_OBSERVATION', 'workspace_session_ref': WORKSPACE, 'observation_run_ref': ref('readonly-run'),
            'session_label': 'readonly-context', 'budget': {'max_actions': 0, 'timeout_ms': 1000,
                'max_frame_age_ms': 5000, 'ttl_seconds': 300, 'allow_foreground': False}})['session_ref']
        def authority(window, scope):
            return {'eligible': True, 'target_ref': TARGET, 'ambiguous': self.ambiguous, 'pixel_source': self.pixel_source}
        self.context = DesktopCurrentContext(window_source=self.source, context_authority=authority,
            cua_adapter=self.cua, binding_reader=lambda: copy.deepcopy(self.binding), observation_guard=guard)
    def tearDown(self):
        self.context.close(); self.cua.shutdown(); self.tmp.cleanup()

    def test_exact_document_probe_is_sampled_twice_and_private_path_never_enters_page(self):
        from packages.domain.tasks import digest
        self.source.window['window_class'] = 'OpusApp'
        calls = []
        def probe(window):
            calls.append(window['identity_sha256'])
            proof = {'source': 'WORD_WINDOW_NATIVEOM_PROPERTYGET', 'native_path': 'C:\\selected\\人工稿.docx',
                'saved': False, 'window_lifetime_sha256': window['identity_sha256'], 'content_equals_saved_bytes': False}
            return {'status': 'OBSERVED', **proof, 'identity_sha256': digest(proof)}
        self.context.document_identity_reader = probe
        observed = self.ask(need=self.need())
        self.assertEqual(len(calls), 2)
        self.assertEqual(observed['document_identity']['status'], 'OBSERVED')
        self.assertNotIn('native_path', json.dumps(observed))
        self.assertFalse(observed['document_identity']['content_equals_saved_bytes'])
        private = self.context.document_identity(observed['context_ref'])
        self.assertEqual(private['native_path'], 'C:\\selected\\人工稿.docx')
        self.assertEqual(self.calls, [])  # Metadata seam, not native or SDK evidence.

    def test_changed_document_during_page_observation_keeps_page_without_file_claim(self):
        from packages.domain.tasks import digest
        self.source.window['window_class'] = 'OpusApp'
        calls = []
        def probe(window):
            calls.append(1)
            proof = {'source': 'WORD_WINDOW_NATIVEOM_PROPERTYGET', 'native_path': 'C:\\selected\\'+str(len(calls))+'.docx',
                'saved': True, 'window_lifetime_sha256': window['identity_sha256'], 'content_equals_saved_bytes': False}
            return {'status': 'OBSERVED', **proof, 'identity_sha256': digest(proof)}
        self.context.document_identity_reader = probe
        observed = self.ask(need=self.need())
        self.assertIsNotNone(observed.get('context_ref'))
        self.assertEqual(observed['document_identity']['status'], 'UNAVAILABLE')
        self.assertIsNone(self.context.document_identity(observed['context_ref']))
    def ask(self, **kwargs): return self.context.get_context(intent='当前页是什么？帮我打开左侧 E09.md', **kwargs)
    def need(self, **kwargs): return {'metadata': True, 'accessibility': False, 'visual': False, 'ocr': False} | kwargs

    def fixture_pixel_proof(self, png, target, _metadata):
        if self.pixel_source == 'UNKNOWN':
            return None
        self.assertEqual(png, base64.b64decode(self.png))
        return {'status': 'VERIFIED_TARGET_PIXELS', 'image_sha256': hashlib.sha256(png).hexdigest(),
            'target_identity_sha256': target['identity_sha256'], 'target_bounds': copy.deepcopy(target['bounds']),
            'evidence_ref': {'id': 'original-context-fixture', 'revision': 1, 'sha256': hashlib.sha256(png).hexdigest()},
            'capture_method': 'QUALIFIED_EXACT_FRAME', 'self_exclusion_verified': True}

    def test_chat_foreground_preserves_last_actual_application(self):
        self.context.observe_foreground(); self.source.foreground = 'eeas'
        observed = self.ask()
        self.assertEqual(observed['status'], 'READY_OBSERVATION')
        self.assertEqual(observed['window']['title'], 'E09.md — Visual Studio Code')
        self.assertTrue(observed['coverage']['accessibility'])
        self.assertFalse(observed['coverage']['visual'])
        self.assertFalse(observed['automatic_input_authorized'])
        self.assertFalse(observed['send_full_image_to_model'])
        self.assertFalse(observed['coverage']['whole_page_seen'])

    def test_default_uses_original_uia_without_screenshot_or_second_session(self):
        observed = self.ask()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.calls[0][1]['include_screenshot'])
        self.assertTrue(self.calls[0][1]['include_accessibility_tree'])
        uia = observed['observations']['accessibility']
        self.assertEqual(uia['source'], 'INJECTED_TEST_PROVIDER')
        self.assertNotIn('pid', uia['metadata']); self.assertNotIn('window_id', uia['metadata'])
        original = json.loads((Path(self.tmp.name) / uia['raw_artifact_ref']['path']).read_bytes())
        self.assertEqual(original['pid'], 123)
        self.assertNotEqual(self.calls[0][-1], threading.get_ident())

    def test_each_question_reads_fresh_content_instead_of_title_cache(self):
        first = self.ask(); self.text = '页面正文已被用户修改'
        second = self.ask()
        self.assertNotEqual(first['context_ref'], second['context_ref'])
        self.assertEqual(second['observations']['accessibility']['metadata']['tree_markdown'], self.text)
        self.assertEqual(len(self.calls), 2)

    def test_metadata_only_needs_no_sdk_or_image(self):
        self.binding['sdk_session_ref'] = None
        result = self.ask(need=self.need())
        self.assertEqual(result['status'], 'READY_OBSERVATION')
        self.assertFalse(result['coverage']['accessibility']); self.assertFalse(result['coverage']['visual'])
        self.assertEqual(self.calls, [])

    def test_page_request_without_sdk_is_unavailable_not_title_read_success(self):
        self.binding['sdk_session_ref'] = None
        result = self.ask()
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertEqual(result['reason'], 'ORIGINAL_CUA_SDK_SESSION_UNAVAILABLE')
        self.assertFalse(result['coverage']['whole_page_seen'])

    def test_visual_is_requested_explicitly_and_bound_to_original_frame(self):
        result = self.ask(need=self.need(visual=True))
        self.assertEqual(result['status'], 'READY_OBSERVATION')
        self.assertTrue(result['coverage']['visual']); self.assertFalse(result['coverage']['accessibility'])
        frame = result['observations']['visual']
        self.assertEqual(frame['target_ref'], TARGET); self.assertEqual(frame['surface_ref'], SURFACE)
        self.assertEqual(frame['source'], 'INJECTED_TEST_PROVIDER')
        self.assertTrue((Path(self.tmp.name) / frame['image_ref']['path']).is_file())

    def test_ocr_is_readonly_and_matches_exact_fresh_frame(self):
        self.context.ocr = lambda f: {'status': 'RECOGNIZED', 'frame_ref': f['frame_ref'],
            'image_sha256': f['image_ref']['sha256'], 'observation': {'text': '真实OCR观察', 'qualification': 'OBSERVATION_ONLY'}}
        result = self.ask(need=self.need(ocr=True))
        self.assertTrue(result['coverage']['ocr']); self.assertTrue(result['coverage']['visual'])
        self.assertFalse(result['automatic_input_authorized'])

    def test_old_ocr_frame_cannot_be_attached_as_current_page(self):
        self.context.ocr = lambda f: {'status': 'RECOGNIZED', 'frame_ref': ref('old-frame'), 'image_sha256': 'f' * 64}
        result = self.ask(need=self.need(ocr=True))
        self.assertEqual(result['status'], 'NEEDS_CLARIFICATION')
        self.assertFalse(result['coverage']['ocr'])

    def test_empty_uia_does_not_claim_whole_page_and_offers_visual_route(self):
        self.text = ''
        result = self.ask()
        self.assertEqual(result['status'], 'NEEDS_CLARIFICATION')
        self.assertEqual(result['questions'][0]['code'], 'NO_READABLE_PAGE_CONTENT')

    def test_requested_visual_can_work_if_accessibility_refuses(self):
        self.context.accessibility = Mock(side_effect=CurrentContextError('UIA_UNAVAILABLE'))
        result = self.ask(need=self.need(accessibility=True, visual=True))
        self.assertEqual(result['status'], 'READY_OBSERVATION')
        self.assertTrue(result['coverage']['visual']); self.assertFalse(result['coverage']['accessibility'])
        self.assertEqual(result['observation_errors'][0]['mode'], 'accessibility')

    def test_unknown_pixel_provenance_does_not_claim_seen_target(self):
        self.pixel_source = 'UNKNOWN'
        result = self.ask(need=self.need(visual=True))
        self.assertEqual(result['status'], 'NEEDS_CLARIFICATION')
        self.assertEqual(result['questions'][0]['code'], 'PIXEL_SOURCE_UNKNOWN_OR_OCCLUDED')

    def test_closed_or_recycled_native_window_requires_clarification(self):
        self.context.observe_foreground(); self.source.foreground = 'eeas'; self.source.lost = True
        self.assertEqual(self.ask()['reason'], 'RECENT_WINDOW_LOST')
        self.source.lost = False; self.source.window['identity_sha256'] = 'd' * 64
        self.assertEqual(self.ask()['reason'], 'WINDOW_LIFETIME_CHANGED')

    def test_minimized_window_not_silently_activated_or_captured(self):
        self.context.observe_foreground(); self.source.foreground = 'eeas'; self.source.window['minimized'] = True
        self.assertEqual(self.ask()['reason'], 'WINDOW_NOT_OBSERVABLE'); self.assertEqual(self.calls, [])

    def test_multiple_same_named_windows_do_not_select_by_title(self):
        self.ambiguous = True
        self.assertEqual(self.ask()['reason'], 'WINDOW_SCOPE_AMBIGUOUS'); self.assertEqual(self.calls, [])

    def test_window_or_workspace_change_during_capture_invalidates_observation(self):
        self.capture_hook = lambda: self.source.window.update(title='另一个页面')
        self.assertEqual(self.ask()['reason'], 'WINDOW_CHANGED_DURING_OBSERVATION')

    def test_window_lost_after_sdk_read_returns_clarification_instead_of_exception(self):
        self.capture_hook = lambda: setattr(self.source, 'lost', True)
        result = self.ask()
        self.assertEqual(result['status'], 'NEEDS_CLARIFICATION')
        self.assertEqual(result['reason'], 'WINDOW_LOST_DURING_OBSERVATION')

    def test_workspace_session_change_cannot_adopt_old_recent_window(self):
        self.context.observe_foreground(); self.source.foreground = 'eeas'
        self.binding['workspace_session_ref'] = ref('other-workspace')
        self.assertEqual(self.ask()['reason'], 'NO_RECENT_WORKSPACE_WINDOW')
        self.assertEqual(self.ask(expected_workspace_session_ref=WORKSPACE)['reason'], 'WORKSPACE_SESSION_CHANGED')

    def test_restart_has_no_recent_window_adoption(self):
        self.source.foreground = 'eeas'
        self.assertEqual(self.ask()['reason'], 'NO_RECENT_WORKSPACE_WINDOW')

    def test_late_winevent_and_uint32_wrap_order(self):
        self.context._record(self.source.window, {'source': 'TEST_NATIVE_EVENT', 'os_event_time': 0xfffffff0})
        self.context._record(self.source.window, {'source': 'TEST_NATIVE_EVENT', 'os_event_time': 20})
        r = self.context._record(self.source.window, {'source': 'TEST_NATIVE_EVENT', 'os_event_time': 10})
        self.assertEqual(r['status'], 'IGNORED_LATE_FOREGROUND_EVENT')

    def test_invalid_needs_or_arbitrary_scope_payload_are_rejected(self):
        for need in [{'metadata': True}, self.need(visual='yes'), self.need(metadata=False)]:
            with self.assertRaises(CurrentContextError): self.ask(need=need)
        with self.assertRaises(TypeError): self.context.get_context(intent='page', hwnd=123)
        self.assertEqual(self.calls, [])

    def test_close_discards_recent_context_and_own_tracker_only(self):
        self.context.observe_foreground(); self.context.close()
        self.assertTrue(self.source.closed); self.assertIsNone(self.context.recent)
        with self.assertRaises(CurrentContextError): self.ask()

    def test_context_snapshot_lookup_and_guard_do_not_wait_for_sdk_worker(self):
        context = self.ask(); before = len(self.calls)
        def forbidden(_): self.fail('guard must not await or dispatch SDK worker')
        original = self.cua.worker.call; self.cua.worker.call = forbidden
        try:
            self.assertEqual(self.context.get(context['context_ref']), context)
            self.assertTrue(self.context.check_current(context['context_ref'], TARGET, SURFACE, 1))
        finally: self.cua.worker.call = original
        self.assertEqual(len(self.calls), before)

    def test_guard_rejects_changed_scope_or_target_lifetime(self):
        context = self.ask()
        self.assertFalse(self.context.check_current(context['context_ref'], ref('other-target'), SURFACE, 1))
        self.assertFalse(self.context.check_current(context['context_ref'], TARGET, SURFACE, 2))
        self.source.window['dpi'] = 144
        self.assertFalse(self.context.check_current(context['context_ref'], TARGET, SURFACE, 1))

    def test_source_authority_refusal_cannot_retain_ready_just_by_matching_target_ref(self):
        context = self.ask()
        self.context.authority = lambda window, binding: {'eligible': False, 'target_ref': TARGET, 'ambiguous': False}
        self.assertFalse(self.context.check_current(context['context_ref'], TARGET, SURFACE, 1))

    def test_source_authority_revoked_during_read_invalidates_same_window(self):
        self.capture_hook = lambda: setattr(self.context, 'authority',
            lambda window, binding: {'eligible': False, 'target_ref': TARGET, 'ambiguous': False})
        self.assertEqual(self.ask()['reason'], 'WINDOW_CHANGED_DURING_OBSERVATION')

    def test_unverified_new_foreground_cannot_reuse_old_application_context(self):
        context = self.ask()
        authority = self.context.authority
        self.context.authority = lambda window, binding: {'eligible': False}
        self.context.observe_foreground(); self.source.foreground = 'eeas'
        self.assertEqual(self.ask()['reason'], 'FOREGROUND_WINDOW_OUTSIDE_VERIFIED_SCOPE')
        self.context.authority = authority
        self.assertFalse(self.context.check_current(context['context_ref'], TARGET, SURFACE, 1))


@unittest.skipUnless(os.name == 'nt', 'stock Win32 readonly ABI requires Windows')
class NativeReadonlyABI(unittest.TestCase):
    def test_actual_library_declarations_and_message_layout_without_registering_hook(self):
        import ctypes as ct
        from packages.adapters.desktop_current_context import _MSG
        source = Win32CurrentWindowSource(excluded_pid_reader=lambda: [os.getpid()])
        self.assertIsNone(source.hook_thread)
        self.assertIsNone(source.hook_handle)
        self.assertEqual(ct.sizeof(_MSG), 48)
        self.assertEqual(len(source.set_hook.argtypes), 7)
        self.assertEqual(source.close()['status'], 'CLOSED')

    def test_actual_invalid_hwnd_is_rejected_without_observing_user_window(self):
        source = Win32CurrentWindowSource(excluded_pid_reader=lambda: [os.getpid()])
        with self.assertRaises((CurrentContextError, ValueError, RuntimeError)):
            source.inspect(1)
        self.assertIsNone(source.hook_thread)


if __name__ == '__main__': unittest.main()
