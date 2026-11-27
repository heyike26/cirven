"""Real TCP, streaming gateway, SQLite callbacks and retained thread ownership."""
from concurrent.futures import Future
from contextlib import contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import sqlite3
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from packages.agent_sessions.wire import ProviderWire, WireCloseUnconfirmed
from packages.domain.tasks import canonical


def check(value,message='provider wire close acceptance failed'):
    if not value: raise AssertionError(message)


def until(predicate,timeout=3):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>=deadline:raise AssertionError('actual TCP/thread state did not settle')
        time.sleep(.005)


class Gateway:
    def __init__(self):
        self.entered,self.release=threading.Event(),threading.Event()
        self.release.set();self.requests=[];self.midstream=False
        self.data=b'data: {"fixture":"original-gateway-boundary"}\n\ndata: [DONE]\n\n'
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                try:
                    if owner.midstream:
                        self.send_response(200);self.send_header('Content-Type','text/event-stream')
                        self.send_header('Content-Length',str(len(owner.data)));self.end_headers()
                        self.wfile.write(owner.data[:12]);self.wfile.flush()
                    owner.entered.set();owner.release.wait()
                    if not owner.midstream:
                        self.send_response(200);self.send_header('Content-Type','text/event-stream')
                        self.send_header('Content-Length',str(len(owner.data)));self.end_headers()
                    self.wfile.write(owner.data[12:] if owner.midstream else owner.data);self.wfile.flush()
                except (OSError,ValueError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=False
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.05),daemon=True)
        self.thread.start()

    def close(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(2)
        check(not self.thread.is_alive())


class Scene:
    def __init__(self,tmp_path,*,hold_complete=False,hold_admit=False):
        self.gateway=Gateway();self.clients=[];self.path=tmp_path/'callbacks.sqlite'
        self.admit_entered,self.complete_entered=threading.Event(),threading.Event()
        self.admit_release,self.complete_release=threading.Event(),threading.Event()
        if not hold_admit:self.admit_release.set()
        if not hold_complete:self.complete_release.set()
        with sqlite3.connect(self.path) as db:
            db.executescript('CREATE TABLE admitted(id TEXT,sha TEXT);CREATE TABLE completed(payload TEXT);')
        profile=SimpleNamespace(model_id='wire-boundary-fixture',api_key='original-fixture-key-not-a-user-key',
            provider_url=f'http://127.0.0.1:{self.gateway.server.server_port}/v1',transport_policy=None)
        def admit(sha):
            self.admit_entered.set();self.admit_release.wait()
            with sqlite3.connect(self.path) as db:db.execute('INSERT INTO admitted VALUES(?,?)',('receipt-one',sha))
            return 'receipt-one'
        def complete(observation):
            self.complete_entered.set();self.complete_release.wait()
            with sqlite3.connect(self.path) as db:db.execute('INSERT INTO completed VALUES(?)',(canonical(observation),))
        self.wire=ProviderWire(profile,admit=admit,complete=complete,max_output_tokens=100).start()

    def rows(self,name):
        with sqlite3.connect(self.path) as db:return db.execute('SELECT * FROM '+name).fetchall()

    def request(self):
        base=urlsplit(self.wire.base_url)
        client=http.client.HTTPConnection(base.hostname,base.port,timeout=10)
        body={'model':'wire-boundary-fixture','stream':True,'max_tokens':10,'messages':[{'role':'user','content':'original test data'}]}
        try:
            client.request('POST','/v1/chat/completions',body=canonical(body),
                           headers={'Authorization':'Bearer '+self.wire.key,'Content-Type':'application/json'})
            response=client.getresponse();return {'status':response.status,'data':response.read()}
        except (OSError,http.client.HTTPException) as error:return {'error_type':type(error).__name__}
        finally:client.close()

    def request_async(self):
        future=Future()
        def request():
            try:future.set_result(self.request())
            except BaseException as error:future.set_exception(error)
        thread=threading.Thread(target=request,name='test-owned-client',daemon=True)
        self.clients.append(thread);thread.start();return future

    def close(self):
        self.admit_release.set();self.complete_release.set();self.gateway.release.set()
        try:self.wire.close()
        finally:
            self.gateway.close()
            for thread in self.clients:thread.join(2)
        check(not any(thread.is_alive() for thread in self.clients))


@contextmanager
def scene(tmp_path,**options):
    value=Scene(tmp_path,**options)
    try:yield value
    finally:value.close()


def test_normal_rpc_drains_real_callback_sqlite_and_idempotent_close_has_no_live_owned_threads(tmp_path):
    with scene(tmp_path) as value:
        result=value.request()
        check(result=={'status':200,'data':value.gateway.data})
        until(lambda:len(value.rows('completed'))==1)
        observed=json.loads(value.rows('completed')[0][0])
        check(observed['upstream'] is True and observed['http_status']==200)
        check(observed['response_sha256']==hashlib.sha256(value.gateway.data).hexdigest())
        check(observed['response_bytes']==len(value.gateway.data) and len(value.rows('admitted'))==1)
        retained=value.wire.owned_threads
        value.wire.close()
        check(value.wire.close_confirmed and not any(thread.is_alive() for thread in retained+value.wire.owned_threads))
        value.wire.close();check(len(value.rows('completed'))==1)
        with pytest.raises(RuntimeError,match='WIRE_SEALED'):value.wire.start()


@pytest.mark.parametrize('phase',['headers','body'])
def test_incomplete_inbound_socket_is_registered_before_dispatch_and_shutdown_interrupts_actual_read(tmp_path,phase):
    with scene(tmp_path) as value:
        base=urlsplit(value.wire.base_url)
        with socket.create_connection((base.hostname,base.port),timeout=2) as client:
            request=b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n'
            if phase=='body':request+=('Authorization: Bearer '+value.wire.key+'\r\nContent-Length: 1000\r\n\r\n{').encode()
            client.sendall(request)
            until(lambda:any(thread.name=='eeas-provider-handler' and thread.is_alive() for thread in value.wire.owned_threads))
            check(bool(value.wire._connections))
            value.wire.close(timeout_seconds=1)
            check(value.wire.close_confirmed and not any(thread.is_alive() for thread in value.wire.owned_threads))
            check(value.rows('admitted')==[] and value.rows('completed')==[] and not value.gateway.requests)
            check(client.recv(1024)==b'')


@pytest.mark.parametrize('phase',['headers','stream'])
def test_held_real_upstream_is_cancelled_locally_without_release_retry_or_remote_success(tmp_path,phase):
    with scene(tmp_path) as value:
        value.gateway.midstream=phase=='stream';value.gateway.release.clear()
        request=value.request_async();check(value.gateway.entered.wait(3))
        check(len(value.rows('admitted'))==1)
        retained=value.wire.owned_threads;tasks=value.wire.owned_tasks
        check(len(tasks)==1)
        began=time.monotonic();value.wire.close();elapsed=time.monotonic()-began
        check(elapsed<2,'owned network cancellation should not wait for remote response or the five-second close cap')
        check(not value.gateway.release.is_set(),'the external blocked model was never released to make cleanup pass')
        check(value.wire.close_confirmed and not any(thread.is_alive() for thread in retained+value.wire.owned_threads))
        check(not value.wire.owned_tasks and all(task.done() for task in tasks))
        check(len(value.rows('completed'))==1)
        observed=json.loads(value.rows('completed')[0][0])
        check(observed['upstream'] is True and observed['local_cancel_requested'] is True)
        check(observed['remote_outcome']=='UNKNOWN' and observed['error_type']=='UPSTREAM_CANCELLED_LOCAL_CLOSE')
        check('response_sha256' not in observed)
        value.wire.close(timeout_seconds=.05)
        check(len(value.gateway.requests)==len(value.rows('admitted'))==len(value.rows('completed'))==1)
        request.result(timeout=3)


def test_complete_callback_is_owned_until_it_really_finishes_and_parallel_close_does_not_fake_success(tmp_path):
    with scene(tmp_path,hold_complete=True) as value:
        request=value.request_async();check(value.complete_entered.wait(3))
        check(value.rows('completed')==[])
        outcome=Future()
        def close():
            try:value.wire.close(timeout_seconds=.3);outcome.set_result('CLOSED')
            except WireCloseUnconfirmed as error:outcome.set_result(error.code)
        closer=threading.Thread(target=close,daemon=True);closer.start()
        until(lambda:value.wire.closed)
        with pytest.raises(WireCloseUnconfirmed):value.wire.close(timeout_seconds=.05)
        closer.join(1);check(outcome.result()=='WIRE_CLOSE_UNCONFIRMED')
        check(not value.wire.close_confirmed and value.rows('completed')==[])
        value.complete_release.set();request.result(timeout=3)
        until(lambda:len(value.rows('completed'))==1)
        value.wire.close(timeout_seconds=.5)
        check(not any(thread.is_alive() for thread in value.wire.owned_threads))


def test_preseal_admission_callback_cannot_forward_late_after_close_and_keeps_no_upstream_receipt_falsehood(tmp_path):
    with scene(tmp_path,hold_admit=True) as value:
        request=value.request_async();check(value.admit_entered.wait(3))
        with pytest.raises(WireCloseUnconfirmed):value.wire.close(timeout_seconds=.1)
        value.admit_release.set();request.result(timeout=3)
        until(lambda:len(value.rows('completed'))==1)
        observed=json.loads(value.rows('completed')[0][0])
        check(observed['upstream'] is False and observed['error_type']=='ValueError')
        check(not value.gateway.requests and len(value.rows('admitted'))==1)
        value.wire.close(timeout_seconds=.5)
        check(value.wire.close_confirmed)


def test_close_only_interrupts_this_wire_and_another_original_transport_keeps_working(tmp_path):
    first=tmp_path/'first';second=tmp_path/'second';first.mkdir();second.mkdir()
    with scene(first) as one,scene(second) as two:
        base=urlsplit(one.wire.base_url)
        with socket.create_connection((base.hostname,base.port),timeout=2) as client:
            client.sendall(b'POST /v1/chat/completions HTTP/1.1\r\n')
            until(lambda:bool(one.wire._connections))
            one.wire.close(timeout_seconds=1)
            check(two.wire.thread.is_alive() and not two.wire.closed)
            check(two.request()=={'status':200,'data':two.gateway.data})
            check(one.rows('admitted')==[] and len(two.rows('admitted'))==1)


def test_unstarted_wire_closes_its_listener_without_shutdown_deadlock_and_invalid_deadline_does_not_seal():
    value=ProviderWire(SimpleNamespace(model_id='unused'),admit=lambda _:None,complete=lambda _:None,max_output_tokens=10)
    try:
        for deadline in [True,0,-1,6,float('inf'),float('nan'),'1']:
            with pytest.raises(ValueError):value.close(timeout_seconds=deadline)
        check(not value.closed and not value.close_confirmed)
        value.close(timeout_seconds=.5)
        check(value.close_confirmed and not any(thread.is_alive() for thread in value.owned_threads))
        value.close(timeout_seconds=.5)
    finally:value.close()


def test_real_listener_fd_still_open_after_close_fault_is_not_confirmed_and_second_close_can_recheck(tmp_path,monkeypatch):
    with scene(tmp_path) as value:
        check(value.request()['status']==200)
        original=value.wire.server.server_close
        attempted=[]
        def close_once_fault():
            attempted.append(True)
            if len(attempted)==1:raise OSError('owned socket close fault seam')
            original()
        # Inject only an OS close failure, not a fake thread/socket state. The
        # genuine listening descriptor remains open and is checked independently.
        monkeypatch.setattr(value.wire.server,'server_close',close_once_fault)
        with pytest.raises(WireCloseUnconfirmed) as failure:value.wire.close(timeout_seconds=.5)
        check(failure.value.pending_thread_count==0 and value.wire.server.fileno()!=-1)
        check(not value.wire.close_confirmed and not any(thread.is_alive() for thread in value.wire.owned_threads))
        value.wire.close(timeout_seconds=.5)
        check(len(attempted)==2 and value.wire.server.fileno()==-1 and value.wire.close_confirmed)
