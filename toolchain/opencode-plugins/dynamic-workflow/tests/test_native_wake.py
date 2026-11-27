"""Actual pinned Bun customtool/task loop with 200 JSON or stalled hint HTTP.

Only external HTTP model replies and the hint endpoint are fixtures. A hint
cannot withhold the actual plan receipt or original task continuation.
"""
import importlib.util
import json
import os
from pathlib import Path
import threading

import pytest


@pytest.mark.parametrize('hint_mode', ['json-200', 'no-response'])
def test_native_hint_cannot_block_committed_plan_and_original_task(tmp_path, monkeypatch, hint_mode):
    source = Path(__file__).with_name('test_native_runtime.py')
    spec = importlib.util.spec_from_file_location('native_workflow_wake_fixture', source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    release = threading.Event()
    hints = []
    original_server = fixture.ThreadingHTTPServer

    def server(address, handler):
        original_post = handler.do_POST

        def post(self):
            if self.path != '/api/dynamic-workflow/plugin-events':
                return original_post(self)
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            hints.append(body)
            if hint_mode == 'no-response':
                release.wait(90)
                return
            raw = json.dumps({'accepted': True, 'authority': 'REOBSERVATION_HINT_ONLY'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        handler.do_POST = post
        handler.protocol_version = 'HTTP/1.1'
        return original_server(address, handler)

    monkeypatch.setattr(fixture, 'ThreadingHTTPServer', server)
    original_prepare = fixture.prepare_workflow_plugin
    bundle = os.environ.get('EEAS_WORKFLOW_NATIVE_TEST_BUNDLE')
    if bundle:
        def prepare(*args, **kwargs):
            return original_prepare(*args, **kwargs, plugin_root=Path(bundle))
        monkeypatch.setattr(fixture, 'prepare_workflow_plugin', prepare)
    try:
        fixture.test_actual_fixed_cli_loads_plugin_and_original_task_uses_two_provider_models_with_real_variants(tmp_path, True)
        if not hints:
            raise AssertionError('Actual original events must reach the hint endpoint')
        (tmp_path / 'hint-qualification.json').write_text(json.dumps({
            'hint_mode': hint_mode, 'actual_hint_requests': len(hints),
            'result': 'COMMITTED_PLAN_AND_TWO_ORIGINAL_CHILDREN_RETURNED',
            'native_windows': 'NOT_RUN', 'production_api': 'NOT_RUN'}))
    finally:
        release.set()
