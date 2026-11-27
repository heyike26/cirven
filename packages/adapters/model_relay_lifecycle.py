"""Owned HTTP listener/handlers and cancellable reads, shared by model relays.

No credentials, retries, model loop or idle event-loop thread. Async reads run
on each existing handler thread; blocked admission/completion remains owned.
"""
import asyncio
import math
import socket
import threading
import time
from http.server import ThreadingHTTPServer


class WireCloseUnconfirmed(RuntimeError):
    code = 'WIRE_CLOSE_UNCONFIRMED'

    def __init__(self, pending_thread_count):
        super().__init__(self.code)
        self.pending_thread_count = pending_thread_count


class ModelRelayLifecycle:
    def __init__(self, handler, *, lock=None):
        self.lock = threading.RLock() if lock is None else lock
        self.close_lock = threading.Lock()
        self.closed = self.close_confirmed = self.started = False
        self.connections, self.handlers, self.upstreams = set(), set(), {}
        self.stopper = None
        owner = self

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

            def get_request(self):
                request, address = super().get_request()
                with owner.lock:
                    owner.connections.add(request)
                return request, address

            def process_request(self, request, address):
                with owner.lock:
                    if owner.closed:
                        self.shutdown_request(request)
                        return
                    owner.handlers = {t for t in owner.handlers if t.is_alive()}
                    thread = threading.Thread(target=self.process_request_thread,
                        args=(request, address), name='eeas-provider-handler', daemon=True)
                    owner.handlers.add(thread)
                    try:
                        thread.start()
                    except BaseException:
                        self.shutdown_request(request)
                        raise

            def shutdown_request(self, request):
                try:
                    super().shutdown_request(request)
                finally:
                    with owner.lock:
                        owner.connections.discard(request)

            def handle_error(self, request, address):
                # A shutdown BrokenPipe is not a reason to print private input.
                pass

        self.server = Server(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.05),
                                       name='eeas-provider-listener', daemon=True)

    def start(self):
        with self.lock:
            if self.closed:
                raise RuntimeError('WIRE_SEALED')
            if self.started:
                return
            try:
                self.thread.start()
            except BaseException:
                self.closed = True
                self.server.server_close()
                raise
            self.started = True

    @property
    def owned_threads(self):
        with self.lock:
            return [self.thread, *self.handlers, *([self.stopper] if self.stopper else [])]

    @property
    def owned_tasks(self):
        with self.lock:
            return tuple(self.upstreams)

    def run(self, forward, *args):
        return asyncio.run(self._run(forward, args))

    async def _run(self, forward, args):
        task, loop = asyncio.current_task(), asyncio.get_running_loop()
        with self.lock:
            if self.closed:
                raise ValueError('Wire sealed before forwarding')
            self.upstreams[task] = {'loop': loop, 'cancel_requested': False}
        try:
            return await forward(*args)
        finally:
            with self.lock:
                self.upstreams.pop(task, None)

    def _cancel(self):
        with self.lock:
            pending = [(task, row['loop']) for task, row in self.upstreams.items() if not row['cancel_requested']]
            for task, _loop in pending:
                self.upstreams[task]['cancel_requested'] = True
        for task, loop in pending:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # A completed handler may already have closed its loop.

    def _stop_listener(self):
        try:
            if self.started:
                self.server.shutdown()
        finally:
            try:
                self.server.server_close()
            except OSError:
                pass  # Real descriptor liveness still prevents false CLOSED.

    @staticmethod
    def validate_timeout(timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ValueError('WIRE_CLOSE_DEADLINE_INVALID')

    def close(self, *, timeout_seconds=5):
        self.validate_timeout(timeout_seconds)
        deadline = time.monotonic()+timeout_seconds
        with self.lock:
            self.closed = True
            if self.close_confirmed:
                return
        if not self.close_lock.acquire(timeout=max(0, deadline-time.monotonic())):
            raise WireCloseUnconfirmed(sum(t.is_alive() for t in self.owned_threads))
        try:
            with self.lock:
                if self.close_confirmed:
                    return
                if self.stopper is None or not self.stopper.is_alive() and self.server.fileno() != -1:
                    self.stopper = threading.Thread(target=self._stop_listener,
                        name='eeas-provider-listener-stop', daemon=True)
                    self.stopper.start()
                connections = tuple(self.connections)
            for connection in connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection.close()
                except OSError:
                    pass
            self._cancel()
            for thread in self.owned_threads:
                if thread.ident is not None and thread is not threading.current_thread():
                    thread.join(timeout=max(0, deadline-time.monotonic()))
            with self.lock:
                pending = sum(t.is_alive() for t in self.owned_threads)
                if pending or self.upstreams or self.server.fileno() != -1:
                    raise WireCloseUnconfirmed(pending)
                self.close_confirmed = True
        finally:
            self.close_lock.release()
