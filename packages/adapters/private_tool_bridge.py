"""One tracked loopback JSON bridge for existing native tool callbacks.

This owns only its listener, request threads, callback tasks and worker pool.
It grants no tool/source/model authority and never starts another model loop.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import copy_context
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time

from packages.domain.store import Conflict
from packages.domain.tasks import canonical


def _constant(value):
    raise ValueError('Non-finite private bridge JSON')


class PrivateToolBridge:
    def __init__(self, callback, loop, *, on_closed=None, routes=('/v1/call',),
                 label='ordinary', request_bytes=65536, timeout=15, header_check=None):
        self.routes=frozenset(routes)
        if (not self.routes or any(not isinstance(p,str) or not p.startswith('/') for p in self.routes)
                or type(request_bytes) is not int or not 1<=request_bytes<=256*1024
                or type(timeout) not in {int,float} or not 0<timeout<=600):
            raise ValueError('Private bridge needs finite routes, bytes and deadline')
        self.callback,self.loop,self.on_closed=callback,loop,on_closed
        self.label,self.request_bytes,self.timeout,self.header_check=label,request_bytes,timeout,header_check
        self.key=secrets.token_hex(32);self.closed=False;self.close_confirmed=False
        self.lock=threading.RLock();self.close_lock=threading.Lock()
        self.futures,self.tasks,self.workers,self.requests=set(),set(),set(),set()
        self.drains=set()
        self.worker_threads=set();self.request_gate=threading.BoundedSemaphore(4)
        self.worker_slots=0;self.worker_cleanup_unconfirmed=False
        self.gate=threading.BoundedSemaphore(4);self.worker_gate=threading.BoundedSemaphore(4)
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix=label+'-callback',initializer=self._register_worker)
        owner=self

        class Server(ThreadingHTTPServer):
            daemon_threads=False
            block_on_close=False
            def process_request(self,request,client_address):
                with owner.lock:
                    admitted=not owner.closed and owner.request_gate.acquire(blocking=False)
                    if admitted:
                        thread=threading.Thread(target=self.process_request_thread,args=(request,client_address),
                            name=label+'-request',daemon=False)
                        owner.requests.add(thread)
                if not admitted:
                    try:
                        request.settimeout(3)
                        body=b'{"error":"PRIVATE_BRIDGE_BUSY_OR_CLOSED"}'
                        request.sendall(b'HTTP/1.1 429 Too Many Requests\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                    except OSError:pass
                    finally:self.shutdown_request(request)
                    return
                try:thread.start()
                except BaseException:
                    with owner.lock:owner.requests.discard(thread)
                    owner.request_gate.release();self.shutdown_request(request);raise
            def process_request_thread(self,request,client_address):
                try:super().process_request_thread(request,client_address)
                finally:
                    with owner.lock:owner.requests.discard(threading.current_thread())
                    owner.request_gate.release()

        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def setup(self):
                super().setup();self.connection.settimeout(3)
            def log_message(self,*_args):pass
            def reply(self,status,value):
                raw=canonical(value).encode()
                if len(raw)>3*1024*1024:status,raw=413,b'{"error":"PRIVATE_OUTPUT_TOO_LARGE"}'
                self.send_response(status);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close');self.end_headers()
                try:self.wfile.write(raw)
                except (OSError,TimeoutError):pass
                self.close_connection=True
            def do_GET(self):self.reply(403,{'error':label.upper()+'_ROUTE_DENIED'})
            def do_POST(self):
                if (owner.closed or self.path not in owner.routes
                        or not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.key)
                        or owner.header_check is not None and not owner.header_check(self.headers)):
                    self.reply(403,{'error':label.upper()+'_ROUTE_DENIED'});return
                if not owner.gate.acquire(blocking=False):
                    self.reply(429,{'error':label.upper()+'_BUSY'});return
                future=None
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if self.headers.get('Transfer-Encoding') or not 0<size<=owner.request_bytes:
                        raise ValueError('Private input is not bounded')
                    raw=self.rfile.read(size)
                    if len(raw)!=size:raise ValueError('Private input body is incomplete')
                    value=json.loads(raw,parse_constant=_constant)
                    if not isinstance(value,dict):raise ValueError('Private input must be an object')
                    with owner.lock:
                        if owner.closed:raise Conflict('Private bridge sealed before dispatch')
                        coroutine=owner._invoke(value,self.path)
                        try:future=asyncio.run_coroutine_threadsafe(coroutine,owner.loop)
                        except BaseException:
                            coroutine.close();raise
                        owner.futures.add(future)
                    self.reply(200,future.result(timeout=owner.timeout))
                except Exception as error:
                    if future is not None and not future.done():future.cancel()
                    self.reply(409,{'error':label.upper()+'_SCOPE_OR_CALL_REJECTED','error_type':type(error).__name__})
                finally:
                    if future is not None:
                        with owner.lock:owner.futures.discard(future)
                    owner.gate.release()
        try:
            self.server=Server(('127.0.0.1',0),Handler)
            self.thread=threading.Thread(target=self.server.serve_forever,name=label+'-broker',daemon=False)
            self.thread.start()
        except BaseException:
            self.closed=True
            if hasattr(self,'server'):self.server.server_close()
            self.executor.shutdown(wait=True)
            raise
        self.base_url='http://127.0.0.1:'+str(self.server.server_address[1])

    def _register_worker(self):
        with self.lock:self.worker_threads.add(threading.current_thread())

    @property
    def owned_threads(self):
        with self.lock:return tuple({self.thread,*self.requests,*self.worker_threads})

    @property
    def owned_tasks(self):
        with self.lock:return tuple({*self.tasks,*self.futures,*self.workers,*self.drains})

    async def _invoke(self,value,route=None):
        route=route or '/v1/call';task=asyncio.current_task()
        with self.lock:
            if self.closed or route not in self.routes:raise Conflict('Private bridge closed or route changed')
            self.tasks.add(task)
        try:return await self.callback(route,value)
        finally:
            with self.lock:self.tasks.discard(task)

    def _worker_done(self,future):
        if future.cancelled():
            # A cancelled queued Future leaves a queue item in ThreadPoolExecutor.
            # Keep its slot until a FIFO control item acknowledges draining it;
            # repeated cancellation therefore cannot grow an unbounded queue.
            try:
                with self.lock:
                    marker=self.executor.submit(lambda:None);self.workers.add(marker)
                marker.add_done_callback(lambda done:self._release_worker(future,done))
            except Exception:
                with self.lock:self.worker_cleanup_unconfirmed=True
        else:self._release_worker(future)

    def _release_worker(self,future,marker=None):
        with self.lock:
            self.workers.discard(future)
            if marker is not None:self.workers.discard(marker)
            self.worker_slots-=1
        self.worker_gate.release()

    async def run_sync(self,callback,*args):
        with self.lock:
            if self.closed:raise Conflict('Private callback scope closed')
            if not self.worker_gate.acquire(blocking=False):raise Conflict('Private callback workers busy')
            self.worker_slots+=1
            try:
                scope=copy_context();future=self.executor.submit(scope.run,callback,*args)
            except BaseException:
                self.worker_slots-=1;self.worker_gate.release();raise
            self.workers.add(future);future.add_done_callback(self._worker_done)
        wrapped=asyncio.wrap_future(future)
        wrapped.add_done_callback(self._consume_exception)
        try:return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # cancel() succeeds only before execution. Running callbacks keep
            # their original scope and remain owned until actual completion.
            future.cancel()
            raise

    @staticmethod
    def _consume_exception(future):
        if not future.cancelled():future.exception()

    def _drain_done(self,future):
        with self.lock:self.drains.discard(future)

    def resources(self):
        with self.lock:
            return {'server_thread_alive':self.thread.is_alive(),'request_threads':sum(t.is_alive() for t in self.requests),
                'callback_tasks':len(self.tasks),'callback_futures':sum(not f.done() for f in self.workers),
                'broker_futures':sum(not f.done() for f in {*self.futures,*self.drains}),
                'callback_threads_alive':sum(t.is_alive() for t in self.worker_threads),
                'held_worker_slots':self.worker_slots,'worker_cleanup_unconfirmed':self.worker_cleanup_unconfirmed}

    async def _drain(self):
        with self.lock:tasks=list(self.tasks)
        for task in tasks:task.cancel()
        if tasks:
            _done,pending=await asyncio.wait(tasks,timeout=3)
            if pending:raise Conflict('Private callbacks did not finish cancellation')

    def close(self):
        if not self.close_lock.acquire(blocking=False):raise Conflict('Private bridge close is already running')
        try:
            with self.lock:
                if self.close_confirmed:return
                self.closed=True
            try:running=asyncio.get_running_loop()
            except RuntimeError:running=None
            if running is self.loop:raise Conflict('Private bridge close must run outside its callback loop')
            self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
            with self.lock:futures,tasks=list(self.futures),list(self.tasks)
            for future in futures:future.cancel()
            errors=[]
            if self.loop.is_running():
                # Always cross the loop queue, even if an admitted request's
                # coroutine has not reached the tracked callback set yet.
                coroutine=self._drain()
                try:barrier=asyncio.run_coroutine_threadsafe(coroutine,self.loop)
                except BaseException:
                    coroutine.close();raise
                with self.lock:self.drains.add(barrier)
                barrier.add_done_callback(self._drain_done)
                try:barrier.result(timeout=4)
                except Exception as error:errors.append(error)
            elif tasks or any(not future.done() for future in futures):
                errors.append(Conflict('Private callback loop stopped before its barrier'))
            until=time.monotonic()+4
            with self.lock:requests=list(self.requests)
            for thread in requests:thread.join(timeout=max(0,until-time.monotonic()))
            with self.lock:pending=[future for future in self.workers if not future.done()]
            if pending:wait(pending,timeout=max(0,until-time.monotonic()))
            with self.lock:
                cancelled_queue_only=(self.worker_cleanup_unconfirmed
                    and not any(not future.done() for future in self.workers))
            if cancelled_queue_only:
                # All business Futures are terminal; a failed FIFO marker can
                # be replaced by the stronger actual pool drain/join barrier.
                # This clears no resource usage, model intent or unknown ACK.
                self.executor.shutdown(wait=True,cancel_futures=True)
                with self.lock:
                    self.worker_slots=0;self.worker_cleanup_unconfirmed=False
            with self.lock:
                unsettled=(self.worker_slots>0 or self.worker_cleanup_unconfirmed
                    or any(not future.done() for future in self.workers) or bool(self.tasks)
                    or any(not future.done() for future in self.futures))
            if unsettled or self.thread.is_alive() or any(thread.is_alive() for thread in requests):
                errors.append(Conflict('Private request/callback workers still active; owner is not closed'))
            if errors:raise errors[0]
            self.executor.shutdown(wait=True)
            self.close_confirmed=True
            if self.on_closed is not None:self.on_closed()
        finally:self.close_lock.release()
