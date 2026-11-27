"""Bounded MCP HTTP source reads under an explicitly resolved work ledger.

This measures raw identity-encoded response body bytes exposed by HTTPX, not
socket traffic, provider billing, MCP server internals or native SDK file I/O.
Reservations precede forwarding. Unknown/cancelled reads retain their bound.
"""
import httpx2

from packages.domain.store import Conflict

MAX_HTTP_EXCHANGES = 32


class MCPSourceStream(httpx2.AsyncByteStream):
    def __init__(self, stream, maximum, budget, ticket, done):
        self.stream, self.maximum, self.budget, self.ticket = stream, maximum, budget, ticket
        self.done = done
        self.size = 0
        self.settled = False
        self.closed = False

    def settle(self, actual):
        if self.settled:
            return
        self.settled = True
        try:
            if self.budget is not None:
                self.budget.finish_read(self.ticket, actual)
        except Exception as error:
            self.budget.mark_unconfirmed(type(error).__name__, original_evidence_retained=False)
            # The source body has already arrived. Keep delivery intact so the
            # original handler can save its result; block later generation via
            # the sticky accounting marker instead of discarding that result.
        finally:
            self.done(self)

    async def __aiter__(self):
        if self.settled or self.closed:
            raise Conflict('MCP source response was already consumed or closed')
        try:
            async for chunk in self.stream:
                self.size += len(chunk)
                if self.size > self.maximum:
                    raise Conflict('MCP source response exceeds its reserved bound')
                yield chunk
        except BaseException:
            self.settle(None)
            raise
        else:
            self.settle(self.size)

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        try:
            await self.stream.aclose()
        except BaseException:
            self.settle(None)
            raise
        else:
            self.settle(None)


class MCPSourceTransport(httpx2.AsyncBaseTransport):
    """One original transport, with no redirect/retry or background monitor."""
    def __init__(self, inner, maximum, budget=None, admit=None):
        if type(maximum) is not int or maximum < 1:
            raise Conflict('MCP source transport requires a positive finite body limit')
        self.inner, self.maximum, self.budget, self.admit = inner, maximum, budget, admit
        self.exchanges = 0
        self.active = set()
        self.closed = False

    async def handle_async_request(self, request):
        if self.closed or self.exchanges >= MAX_HTTP_EXCHANGES:
            raise Conflict('MCP source transport is closed or its exchange allowance is exhausted')
        self.exchanges += 1
        if self.admit is not None:
            self.admit()
        maximum = self.maximum
        ticket = None
        if self.budget is not None:
            maximum = min(maximum, self.budget.remaining_source_bytes())
            if maximum < 1:
                raise Conflict('Original work MCP source allowance is exhausted')
            ticket = self.budget.reserve_read(maximum)
        try:
            response = await self.inner.handle_async_request(request)
        except BaseException:
            if self.budget is not None:
                try:
                    self.budget.finish_read(ticket, None)
                except Exception as error:
                    self.budget.mark_unconfirmed(type(error).__name__, original_evidence_retained=False)
            raise
        stream = MCPSourceStream(response.stream, maximum, self.budget, ticket, self.active.discard)
        self.active.add(stream)
        response.stream = stream
        if self.closed:
            await stream.aclose()
            raise Conflict('MCP source transport closed during its original request')
        return response

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        errors = []
        for stream in tuple(self.active):
            try:
                await stream.aclose()
            except BaseException as error:
                errors.append(error)
        try:
            await self.inner.aclose()
        except BaseException as error:
            errors.append(error)
        if errors:
            raise errors[0]
