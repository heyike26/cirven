"""Private Main-only NDJSON lease for an authority-owned assistantOrb."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid

import anyio
from fastapi import Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from packages.adapters.orb_pointer_watch import OrbPointerWatch, PointerWatchError


SCHEMA = 'eeas.orb-pointer-watch.v1'


class _Lease:
    def __init__(self, service, identity):
        self.service, self.identity = service, identity
        self.id = uuid.uuid4().hex
        self.source = None
        self.lock = threading.Lock()
        self.closed = False
        self.released = threading.Event()

    def line(self, sequence, kind, **fields):
        return (json.dumps({'schema': SCHEMA, 'watch_id': self.id,
            'host_generation': self.identity['host_generation'], 'sequence': sequence,
            'kind': kind, **fields}, separators=(',', ':')) + '\n').encode()

    def start(self):
        with self.lock:
            if self.closed:
                raise PointerWatchError('POINTER_WATCH_CLOSED')
            self.source = self.service.source_factory(self.identity,
                self.service.authority.validate_own_role_identity)
            self.source.start()

    def close(self):
        with self.lock:
            self.closed = True
            receipt = self.source.close() if self.source is not None else {'status': 'CLOSED'}
        # Never permit another hook while cleanup of the original is unconfirmed.
        if receipt.get('status') == 'CLOSED':
            with self.service.lock:
                if self.service.active is self:
                    self.service.active = None
            self.released.set()
        return receipt


class OrbPointerWatchService:
    def __init__(self, authority, *, source_factory=OrbPointerWatch):
        self.authority, self.source_factory = authority, source_factory
        self.lock = threading.Lock()
        self.active = None
        self.closed = False

    def reserve(self):
        identity = self.authority.own_role_identity('assistantOrb')
        with self.lock:
            if self.closed:
                raise HTTPException(409, detail={'code': 'ORB_POINTER_WATCH_CLOSED'})
            if self.active is not None:
                raise HTTPException(409, detail={'code': 'ORB_POINTER_WATCH_ACTIVE'})
            self.active = _Lease(self, identity)
            return self.active

    def close(self):
        with self.lock:
            self.closed = True
            active = self.active
        return active.close() if active is not None else {'status': 'CLOSED'}


class _WatchResponse(StreamingResponse):
    def __init__(self, lease):
        self.lease = lease
        super().__init__(_events(lease), media_type='application/x-ndjson', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Accel-Buffering': 'no'})

    async def __call__(self, scope, receive, send):
        try:
            # Keep an explicit disconnect listener on ASGI 2.4 too. A silent
            # stream otherwise learns of disconnect only on its next send,
            # which would unnecessarily retain this hook for the whole lease.
            async with anyio.create_task_group() as tasks:
                async def stream():
                    await self.stream_response(send)
                    tasks.cancel_scope.cancel()
                tasks.start_soon(stream)
                await self.listen_for_disconnect(receive)
                tasks.cancel_scope.cancel()
        finally:
            # Also release a reservation if sending headers fails before the
            # async generator was ever entered (its finally cannot run then).
            with anyio.CancelScope(shield=True):
                await asyncio.to_thread(self.lease.close)


async def _events(lease):
    pending = None
    try:
        pending = asyncio.create_task(asyncio.to_thread(lease.start))
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            raise
        except Exception:
            yield lease.line(0, 'UNAVAILABLE', code='POINTER_HOOK_START_UNAVAILABLE')
            return
        yield lease.line(0, 'READY', lease_ms=60000)
        pending = asyncio.create_task(asyncio.to_thread(lease.source.next_event))
        event = await asyncio.shield(pending)
        if event is not None:
            # Adapter creates only this closed, metadata-only event vocabulary.
            if event['kind'] == 'OUTSIDE_POINTER_DOWN':
                yield lease.line(1, event['kind'], button=event['button'], os_event_time=event['os_event_time'])
            elif event['kind'] == 'EXPIRED':
                yield lease.line(1, 'EXPIRED')
            else:
                yield lease.line(1, 'UNAVAILABLE', code=event['code'])
    finally:
        # close() wakes a pending queue wait and joins the exact hook thread.
        with anyio.CancelScope(shield=True):
            cleanup = asyncio.create_task(asyncio.to_thread(lease.close))
            await asyncio.shield(cleanup)
            if pending is not None:
                try:
                    await asyncio.shield(pending)
                except Exception:
                    pass  # Startup failure already reported; cleanup still joined.


def register_orb_pointer_watch_routes(app, service):
    @app.get('/api/desktop-host/collaboration/orb-pointer-events')
    async def events(request: Request, x_eeas_desktop_host: str | None = Header(None)):
        service.authority.check_host(x_eeas_desktop_host)
        if request.query_params:
            raise HTTPException(422, detail={'code': 'ORB_POINTER_WATCH_PARAMETERS_NOT_ACCEPTED'})
        lease = service.reserve()
        return _WatchResponse(lease)
