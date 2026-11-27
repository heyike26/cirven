"""Real authority/TCP streaming with only Win32 calls replaced: NOT Windows QA."""
from contextlib import contextmanager
import asyncio
import copy
import ctypes as ct
import json
import queue
import socket
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx
import pytest
import uvicorn

from apps.api.native_runtime import NativeCollaborationRuntime, register_native_collaboration_routes
from apps.api.orb_pointer_watch import OrbPointerWatchService, _WatchResponse
from packages.adapters.orb_pointer_watch import (
    OrbPointerWatch, PointerWatchError, Win32PointerAPI, _MSLLHOOKSTRUCT,
)
from packages.domain.store import Conflict
from test_native_collaboration import HostLifecycleCounter, TOKEN, host


def check(value, message='Orb pointer acceptance condition failed'):
    if not value:
        raise AssertionError(message)


BIND = {'main_window': {'hwnd_decimal': '77', 'owner_pid': 123},
        'owned_windows': {'assistantOrb': {'hwnd_decimal': '78', 'owner_pid': 123}}}
URL = '/api/desktop-host/collaboration/orb-pointer-events'
HEADERS = {'X-EEAS-Desktop-Host': TOKEN}


class Function:
    def __init__(self, call): self.call = call
    def __call__(self, *args): return self.call(*args)


class Kernel:
    """Message-pump boundary, including native callback execution on its thread."""
    def __init__(self):
        self.messages = queue.Queue()
        self.current = None
        self.callback = None
        self.install_ok = self.unhook_ok = True
        self.next_calls, self.roots, self.waits = [], [], []
        self.install_count = self.unhook_count = 0
        self.install_gate = None
        self.install_entered = threading.Event()
        self.root_event = threading.Event()
        self.closed_event = threading.Event()
        self.dpi = []
        self.root = 78
        for name in ('SetWindowsHookExW', 'CallNextHookEx', 'UnhookWindowsHookEx',
                     'PeekMessageW', 'MsgWaitForMultipleObjectsEx', 'PostThreadMessageW',
                     'WindowFromPoint', 'GetAncestor', 'SetThreadDpiAwarenessContext',
                     'GetCurrentThreadId', 'GetModuleHandleW'):
            setattr(self, name, Function(getattr(self, '_' + name)))
        self.api = Win32PointerAPI(libraries={'user32': self, 'kernel32': self})

    def _GetCurrentThreadId(self): return 909
    def _GetModuleHandleW(self, name): check(name is None); return 1234
    def _SetWindowsHookExW(self, kind, callback, module, thread):
        check(kind == 14 and module == 1234 and thread == 0)
        self.install_entered.set()
        if self.install_gate is not None:
            check(self.install_gate.wait(2), 'Fixture install gate must be released')
        self.callback = callback; self.install_count += 1
        return 101 if self.install_ok else 0
    def _CallNextHookEx(self, handle, code, kind, address):
        self.next_calls.append((handle, code, kind, threading.get_ident()))
        return 27
    def _UnhookWindowsHookEx(self, handle):
        check(handle == 101); self.unhook_count += 1
        self.closed_event.set()
        return int(self.unhook_ok)
    def _PeekMessageW(self, out, _hwnd, _minimum, _maximum, remove):
        if not remove or self.current is None: return 0
        value, self.current = self.current, None
        if value == 'QUIT': out._obj.message = 0x12; return 1
        code, kind, event, reply = value
        reply.append(self.callback(code, kind, ct.addressof(event)))
        return 0  # Sent callback processed; no posted window message.
    def _MsgWaitForMultipleObjectsEx(self, count, handles, milliseconds, mask, flags):
        check(count == 0 and handles is None and mask == 0x4ff and flags == 4)
        self.waits.append(milliseconds)
        try: self.current = self.messages.get(timeout=milliseconds / 1000)
        except queue.Empty: return 258
        return 0
    def _PostThreadMessageW(self, thread, kind, a, b):
        check(thread == 909 and kind == 0x12 and a == b == 0)
        self.messages.put('QUIT'); return 1
    def _WindowFromPoint(self, point):
        self.roots.append((point.x, point.y, threading.get_ident()))
        self.root_event.set(); return self.root
    def _GetAncestor(self, hwnd, kind): check(kind == 2); return hwnd
    def _SetThreadDpiAwarenessContext(self, value): self.dpi.append(value); return 99
    def event(self, kind=0x201, *, code=0, x=12, y=16, time=4321, data=0):
        value = _MSLLHOOKSTRUCT(); value.point.x = x; value.point.y = y
        value.time, value.mouseData = time, data
        reply = []
        self.messages.put((code, kind, value, reply))
        return reply


def scene(tmp_path, *, lease=60, kernels=None):
    authority, native = host(tmp_path)
    authority.bind(BIND)
    made = []
    def factory(identity, validate):
        kernel = kernels.pop(0) if kernels else Kernel()
        watch = OrbPointerWatch(identity, validate, api=kernel.api, lease_seconds=lease)
        made.append((watch, kernel))
        return watch
    service = OrbPointerWatchService(authority, source_factory=factory)
    return authority, native, service, made


def test_authority_identity_is_exact_role_birth_marker_and_binding_not_hwnd_only(tmp_path):
    authority, native, _, _ = scene(tmp_path)
    identity = authority.own_role_identity('assistantOrb')
    check(identity['marker']['window_id'] == 78)
    check(authority.validate_own_role_identity(identity))
    changed = copy.deepcopy(identity); changed['role'] = 'main'
    with pytest.raises(Conflict): authority.validate_own_role_identity(changed)
    native.markers[78]['marker_value'] += 1
    with pytest.raises(Conflict): authority.validate_own_role_identity(identity)
    native.markers[78] = copy.deepcopy(identity['marker'])
    authority.close(); authority.bind(BIND)
    with pytest.raises(Conflict): authority.validate_own_role_identity(identity)
    check(authority.generation == identity['host_generation'])  # A rebind still differs.
    authority.close()


def test_callback_always_passes_through_and_does_no_hit_test_or_authority_read(tmp_path):
    authority, _, _, _ = scene(tmp_path)
    kernel = Kernel(); identity = authority.own_role_identity('assistantOrb')
    validations = []
    watch = OrbPointerWatch(identity, lambda value: validations.append(value), api=kernel.api)
    event = _MSLLHOOKSTRUCT(); event.point.x, event.point.y, event.time = -15, 99, 0xffffffff
    for code, kind in ((-1, 0x201), (0, 0x200), (0, 0x202), (0, 0x201)):
        check(watch._callback(code, kind, ct.addressof(event)) == 27)
    check(len(kernel.next_calls) == 4 and not kernel.roots and not validations)
    check(watch.queue.maxsize == 1 and watch.queue.qsize() == 1)
    check(watch.queue.get_nowait() == (-15, 99, 'LEFT', 0xffffffff))
    for kind, data, expected in ((0x204, 0, 'RIGHT'), (0x207, 0, 'MIDDLE'),
                                 (0x20b, 1 << 16, 'X1'), (0x20b, 2 << 16, 'X2')):
        event.mouseData = data
        check(watch._callback(0, kind, ct.addressof(event)) == 27)
        check(watch.queue.get_nowait()[2] == expected)
    watch.close(); authority.close()


def test_own_root_ignored_then_same_external_window_down_yields_one_metadata_event(tmp_path):
    authority, _, service, made = scene(tmp_path)
    lease = service.reserve(); lease.start()
    watch, kernel = made[0]
    result = []
    consumer = threading.Thread(target=lambda: result.append(watch.next_event()))
    consumer.start(); own_reply = kernel.event()
    check(kernel.root_event.wait(2)); check(result == [])
    kernel.root_event.clear(); kernel.root = 900  # No foreground transition is needed.
    outside_reply = kernel.event(0x204, x=-1111, y=70)
    consumer.join(2); check(not consumer.is_alive())
    check(result == [{'kind': 'OUTSIDE_POINTER_DOWN', 'button': 'RIGHT', 'os_event_time': 4321}])
    check(own_reply == outside_reply == [27])
    check(all(row[2] != watch.thread.ident for row in kernel.roots))
    check(kernel.dpi == [-4, 99, -4, 99])
    check(lease.close() == {'status': 'CLOSED'} and kernel.unhook_count == 1)
    check(service.active is None); authority.close()


def test_queue_overflow_and_identity_drift_are_unavailable_not_false_outside(tmp_path):
    authority, native, service, made = scene(tmp_path)
    lease = service.reserve(); lease.start(); watch, kernel = made[0]
    event = _MSLLHOOKSTRUCT()
    watch._callback(0, 0x201, ct.addressof(event)); watch._callback(0, 0x201, ct.addressof(event))
    check(watch.queue.qsize() == 1)
    check(watch.next_event() == {'kind': 'UNAVAILABLE', 'code': 'POINTER_EVENT_OVERFLOW'})
    lease.close()
    lease = service.reserve(); lease.start(); watch, kernel = made[1]
    native.owner['process_creation'] += 1
    kernel.event()
    check(watch.next_event()['kind'] == 'UNAVAILABLE' and not kernel.roots)
    check(lease.close()['status'] == 'CLOSED')
    native.owner['process_creation'] -= 1; authority.close()


def test_hard_lease_unhooks_without_stream_consumer_or_periodic_timer(tmp_path):
    authority, _, service, made = scene(tmp_path, lease=0.05)
    lease = service.reserve(); lease.start(); watch, kernel = made[0]
    watch.thread.join(2)
    check(not watch.thread.is_alive() and watch.handle is None)
    check(watch.next_event() == {'kind': 'EXPIRED'})
    check(len(kernel.waits) == 1 and 1 <= kernel.waits[0] <= 50)
    check(lease.close() == {'status': 'CLOSED'}); authority.close()


def test_close_wakes_pending_read_and_keeps_unknown_unhook_claimed(tmp_path):
    authority, _, service, made = scene(tmp_path)
    lease = service.reserve(); lease.start(); watch, kernel = made[0]
    result = []; consumer = threading.Thread(target=lambda: result.append(watch.next_event()))
    consumer.start(); check(service.close() == {'status': 'CLOSED'})
    consumer.join(2); check(result == [None] and not consumer.is_alive())
    check(service.close() == {'status': 'CLOSED'} and kernel.unhook_count == 1)
    # A failed Unhook is retained, never silently replaced with another hook.
    service = OrbPointerWatchService(authority, source_factory=lambda i, v:
        OrbPointerWatch(i, v, api=kernel.api))
    kernel = Kernel(); kernel.unhook_ok = False
    lease = service.reserve(); lease.start()
    check(lease.close()['status'] == 'CLOSE_UNKNOWN')
    check(service.active is lease)
    with pytest.raises(Exception) as error: service.reserve()
    check(error.value.status_code == 409)
    authority.close()


@contextmanager
def live_app(tmp_path, *, lease=60, kernels=None):
    authority, native, service, made = scene(tmp_path, lease=lease, kernels=kernels)
    runtime = NativeCollaborationRuntime(tmp_path, authority=authority, platform='POLICY_FAULT_PROBE')
    runtime.orb_pointer_watch = service
    app = FastAPI()
    @app.exception_handler(Conflict)
    async def conflict(_request, error):
        return JSONResponse({'error': str(error)}, status_code=409)
    register_native_collaboration_routes(app, None, tmp_path, service=runtime)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(32)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), name='orb-watch-TCP-unit', daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url='http://127.0.0.1:' + str(sock.getsockname()[1]),
                          timeout=3, trust_env=False) as client:
            yield client, runtime, native, made
    finally:
        runtime.close(); server.should_exit = True
        thread.join(5); sock.close()
        check(not thread.is_alive(), 'Owned TCP fixture must terminate')


def test_actual_http_auth_single_stream_disconnect_reopen_and_terminal(tmp_path):
    with live_app(tmp_path) as (client, runtime, native, made):
        check(client.get(URL).status_code == 409 and made == [])
        check(client.get(URL + '?hwnd=900', headers=HEADERS).status_code == 422 and made == [])
        with client.stream('GET', URL, headers=HEADERS) as response:
            check(response.status_code == 200 and response.headers['cache-control'] == 'no-store')
            lines = response.iter_lines(); ready = json.loads(next(lines))
            check(ready['kind'] == 'READY' and ready['sequence'] == 0 and ready['lease_ms'] == 60000)
            check(set(ready) == {'schema', 'watch_id', 'host_generation', 'sequence', 'kind', 'lease_ms'})
            check(ready['host_generation'] == runtime.ws.generation)
            other = client.get(URL, headers=HEADERS)
            check(other.status_code == 409 and other.json()['detail']['code'] == 'ORB_POINTER_WATCH_ACTIVE')
            original_lease = runtime.orb_pointer_watch.active
            # Real TCP client disconnect, without a terminal native event.
        check(made[0][1].closed_event.wait(2), 'Disconnect must release native hook')
        made[0][0].thread.join(2)
        check(original_lease.released.wait(2), 'Disconnect must independently release the lease')
        check(runtime.orb_pointer_watch.active is None)
        with client.stream('GET', URL, headers=HEADERS) as response:
            lines = response.iter_lines(); again = json.loads(next(lines))
            check(again['watch_id'] != ready['watch_id'])
            watch, kernel = made[1]; kernel.root = 900; kernel.event(0x201)
            terminal = json.loads(next(lines))
            check(terminal == {key: again[key] for key in ('schema', 'watch_id', 'host_generation')} |
                  {'sequence': 1, 'kind': 'OUTSIDE_POINTER_DOWN', 'button': 'LEFT', 'os_event_time': 4321})
            check(list(lines) == [])
        check(not watch.thread.is_alive() and watch.handle is None)
        check(native.clear_calls == [])  # Stream cleanup never destroys host authority.


def test_actual_http_expiry_start_failure_and_owner_close_release(tmp_path):
    with live_app(tmp_path / 'expire', lease=0.05) as (client, runtime, _, made):
        response = client.get(URL, headers=HEADERS)
        events = [json.loads(line) for line in response.text.splitlines()]
        check([event['kind'] for event in events] == ['READY', 'EXPIRED'])
        check(made[0][1].unhook_count == 1 and runtime.orb_pointer_watch.active is None)
    kernel = Kernel(); kernel.install_ok = False
    with live_app(tmp_path / 'failure', kernels=[kernel]) as (client, runtime, _, made):
        events = [json.loads(line) for line in client.get(URL, headers=HEADERS).text.splitlines()]
        check(len(events) == 1 and events[0]['kind'] == 'UNAVAILABLE')
        check(runtime.orb_pointer_watch.active is None and not made[0][0].thread.is_alive())
    with live_app(tmp_path / 'close') as (client, runtime, _, made):
        with client.stream('GET', URL, headers=HEADERS) as response:
            lines = response.iter_lines(); check(json.loads(next(lines))['kind'] == 'READY')
            receipt = runtime.close()
            check(receipt['outcomes']['orb_pointer_watch'] == {'status': 'CLOSED'})
            check(receipt['status'] == 'CLOSED' and list(lines) == [])
        check(not made[0][0].thread.is_alive())


def test_actual_http_disconnect_during_install_joins_before_releasing_lease(tmp_path):
    kernel = Kernel(); kernel.install_gate = threading.Event()
    with live_app(tmp_path, kernels=[kernel]) as (client, runtime, _, made):
        with client.stream('GET', URL, headers=HEADERS) as response:
            check(response.status_code == 200 and kernel.install_entered.wait(2))
            lease = runtime.orb_pointer_watch.active
            check(lease is not None and not lease.released.is_set())
            # No READY has been emitted; disconnect while native install is pending.
        kernel.install_gate.set()
        check(lease.released.wait(2))
        check(runtime.orb_pointer_watch.active is None)
        watch, _ = made[0]
        check(not watch.thread.is_alive() and watch.handle is None and kernel.unhook_count == 1)


def test_asgi_header_send_failure_releases_before_iterator_start(tmp_path):
    authority, _, service, made = scene(tmp_path)
    lease = service.reserve()
    async def run():
        async def receive():
            await asyncio.Event().wait()
        async def send(message):
            check(message['type'] == 'http.response.start')
            raise OSError('Synthetic client gone before headers')
        with pytest.raises(Exception):
            await _WatchResponse(lease)({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    asyncio.run(run())
    check(lease.released.is_set() and service.active is None and made == [])
    authority.close()


def test_asgi_24_disconnect_cancels_silent_stream_without_waiting_for_expiry(tmp_path):
    authority, _, service, made = scene(tmp_path)
    lease = service.reserve()
    async def run():
        ready = asyncio.Event()
        sent = []
        async def receive():
            await ready.wait()
            return {'type': 'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.body' and message.get('body'):
                sent.append(json.loads(message['body']))
                ready.set()
        await asyncio.wait_for(_WatchResponse(lease)(
            {'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send), 2)
        check([event['kind'] for event in sent] == ['READY'])
    asyncio.run(run())
    check(lease.released.is_set() and service.active is None)
    check(made[0][1].unhook_count == 1 and not made[0][0].thread.is_alive())
    authority.close()
