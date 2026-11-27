"""Controller HTTP diagnostics against a real bounded loopback server; no model."""
import asyncio
from contextlib import contextmanager
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from packages.adapters.opencode import OpenCodeBridge

if os.environ.get('EEAS_HTTP_PHASE_SOURCE'):
    spec=importlib.util.spec_from_file_location('retained_http_phase',os.environ['EEAS_HTTP_PHASE_SOURCE'])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    OpenCodeBridge=module.OpenCodeBridge

@contextmanager
def server():
    entered,release=threading.Event(),threading.Event();calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_args):pass
        def do_GET(self):
            calls.append(self.path)
            if self.path=='/held':entered.set();release.wait(5)
            raw=json.dumps({'apiKey':'private-fixture-value'}if self.path=='/config'else{'ok':True}).encode()
            self.send_response(400 if self.path=='/bad'else 200);self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')));calls.append(self.path)
            self.send_response(204);self.send_header('Content-Length','0');self.end_headers()
    host=ThreadingHTTPServer(('127.0.0.1',0),Handler);host.daemon_threads=True
    thread=threading.Thread(target=host.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{host.server_port}',entered,release,calls
    finally:release.set();host.shutdown();host.server_close();thread.join(2)

def test_accepted_http_is_not_left_as_inflight_and_config_secrets_stay_out_of_journal(tmp_path):
    with server()as(url,_,_,calls):
        async def check():
            async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=3)as client:
                context={'client':client,'workspace':tmp_path}
                assert await OpenCodeBridge._http(None,context,'POST','/session/owned/prompt_async',{'parts':[]},204)is None
                assert context['http_phase']is None and context['http_inflight_count']==0
                assert context['http_last_response']['status']==204
                assert context['http_last_response']['path'].endswith('/prompt_async')
                result=await OpenCodeBridge._http(None,context,'GET','/config')
                assert result['apiKey']=='private-fixture-value'
                assert 'private-fixture-value'not in(tmp_path/'http-responses.jsonl').read_text()
                assert context['http_phase']is None and context['http_inflight']=={}
                assert len(calls)==2
        asyncio.run(check())

def test_an_overlapping_accepted_request_does_not_clear_another_owned_inflight_request(tmp_path):
    with server()as(url,entered,release,calls):
        async def check():
            async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=3)as client:
                context={'client':client,'workspace':tmp_path};held=asyncio.create_task(OpenCodeBridge._http(None,context,'GET','/held'))
                try:
                    assert await asyncio.to_thread(entered.wait,2)
                    await OpenCodeBridge._http(None,context,'POST','/session/owned/prompt_async',{},204)
                    assert context['http_inflight_count']==1 and context['http_phase']=='GET /held'
                    assert len(context['http_inflight'])==1 and context['http_last_response']['status']==204
                finally:release.set();await held
                assert context['http_inflight_count']==0 and context['http_phase']is None
                assert len(calls)==2
        asyncio.run(check())

def test_cancelled_http_clears_its_phase_without_claiming_the_remote_request_was_not_received(tmp_path):
    with server()as(url,entered,release,calls):
        async def check():
            async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=3)as client:
                context={'client':client,'workspace':tmp_path};held=asyncio.create_task(OpenCodeBridge._http(None,context,'GET','/held'))
                assert await asyncio.to_thread(entered.wait,2);held.cancel()
                with pytest.raises(asyncio.CancelledError):await held
                assert context['http_phase']is None and context['http_inflight_count']==0
                assert context['http_last_error']['error_type']=='CancelledError'
                assert context.get('http_last_response')is None and calls==['/held'];release.set()
        asyncio.run(check())

def test_bad_http_response_is_observed_but_not_left_pending_or_treated_as_success(tmp_path):
    with server()as(url,_,_,calls):
        async def check():
            async with httpx.AsyncClient(base_url=url,trust_env=False,timeout=3)as client:
                context={'client':client,'workspace':tmp_path}
                with pytest.raises(ValueError):await OpenCodeBridge._http(None,context,'GET','/bad')
                assert context['http_phase']is None and context['http_inflight_count']==0
                assert context['http_last_response']['status']==400
                assert context['http_last_error']['error_type']=='ValueError'and calls==['/bad']
        asyncio.run(check())
