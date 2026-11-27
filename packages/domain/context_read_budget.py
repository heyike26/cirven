"""One shared allowance for a context action's source records and payloads.

Filesystem stats and SQLite page-cache I/O are not byte measurements here.
Successful reads charge their returned bytes; failed guarded reads reserve their
maximum allowance conservatively. The scope has no background work or model.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import stat

from packages.domain.store import Conflict

MAX_CONTEXT_SOURCE_BYTES = 128 * 1024 * 1024
MAX_CONTEXT_SOURCE_READS = 2048
_CURRENT = ContextVar('cirven_context_source_read_budget', default=None)


class ContextReadLimit(Conflict):
    pass


class ContextReadBudget:
    def __init__(self, byte_limit=MAX_CONTEXT_SOURCE_BYTES, read_limit=MAX_CONTEXT_SOURCE_READS):
        self.byte_limit, self.read_limit = byte_limit, read_limit
        self.charged_bytes, self.reads = 0, 0
        self.cache = {}

    @property
    def remaining(self):
        return max(0, self.byte_limit - self.charged_bytes)

    def charge(self, size, *, read=True, task=True):
        if type(size) is not int or size < 0:
            raise ContextReadLimit('Invalid context source read size')
        if self.charged_bytes + size > self.byte_limit or read and self.reads >= self.read_limit:
            raise ContextReadLimit('Shared context source read budget reached')
        if task:
            from packages.domain.task_resource_budget import current_task_resource_budget
            shared=current_task_resource_budget()
            if shared is not None:
                from packages.domain.task_resource_budget import TaskResourceLimit
                try:shared.consume(source_bytes=size,source_reads=int(read))
                except TaskResourceLimit as error:raise ContextReadLimit(str(error)) from error
        self.charged_bytes += size
        self.reads += int(read)

    def guarded(self, reader, maximum):
        from packages.domain.task_resource_budget import current_task_resource_budget
        shared=current_task_resource_budget()
        limit=min(maximum,self.remaining-1)
        if shared is not None:limit=min(limit,shared.remaining_source_bytes()-1)
        if limit<0 or self.reads>=self.read_limit:raise ContextReadLimit('Shared context source read budget reached')
        ticket=shared.reserve_read(limit+1) if shared is not None else None
        try:
            raw=reader(limit)
        except BaseException:
            self._finish_unknown(shared,ticket)
            self.charge(limit+1,task=False);raise
        if raw is not None and (not isinstance(raw,bytes) or len(raw)>limit):
            self._finish_unknown(shared,ticket)
            self.charge(limit+1,task=False);raise ContextReadLimit('Guarded context source exceeded its allowance')
        size=len(raw) if raw is not None else 0
        if shared is not None:shared.finish_read(ticket,size)
        self.charge(size,task=False);return raw

    async def guarded_async(self, reader, maximum):
        """Reserve before one async read; cancellation keeps its full bound.

        The reader must enforce the supplied byte limit while receiving data.
        No retry or inference of an unfinished read is performed here.
        """
        from packages.domain.task_resource_budget import current_task_resource_budget
        shared=current_task_resource_budget()
        limit=min(maximum,self.remaining-1)
        if shared is not None:limit=min(limit,shared.remaining_source_bytes()-1)
        if limit<0 or self.reads>=self.read_limit:raise ContextReadLimit('Shared context source read budget reached')
        ticket=shared.reserve_read(limit+1) if shared is not None else None
        try:
            raw=await reader(limit)
        except BaseException:
            self._finish_unknown(shared,ticket)
            self.charge(limit+1,task=False)
            raise
        if not isinstance(raw,bytes) or len(raw)>limit:
            self._finish_unknown(shared,ticket)
            self.charge(limit+1,task=False)
            raise ContextReadLimit('Guarded async context source exceeded its allowance')
        if shared is not None:shared.finish_read(ticket,len(raw))
        self.charge(len(raw),task=False)
        return raw

    def fixed(self, reader, maximum):
        from packages.domain.task_resource_budget import current_task_resource_budget
        shared=current_task_resource_budget()
        self.charge(maximum,task=False)
        ticket=shared.reserve_read(maximum) if shared is not None else None
        try:raw=reader()
        except BaseException:
            self._finish_unknown(shared,ticket)
            raise
        if not isinstance(raw,bytes) or len(raw)>maximum:
            self._finish_unknown(shared,ticket)
            raise ContextReadLimit('Fixed context source exceeded its allowance')
        if shared is not None:shared.finish_read(ticket,len(raw))
        self.charged_bytes-=maximum-len(raw);return raw

    @staticmethod
    def _finish_unknown(shared,ticket):
        if shared is not None:
            try:shared.finish_read(ticket,None)
            except Exception:
                # The durable pending reservation still holds the full bound.
                # Preserve the source error instead of replacing it with a
                # bookkeeping acknowledgement/lock failure.
                pass

    def file(self, path, maximum):
        path = Path(path)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise Conflict('Context source is not an ordinary bounded file')
        self.charge(before.st_size)
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise Conflict('Context source identity changed before reading')
            raw = stream.read(before.st_size)
            after = os.fstat(stream.fileno())
        if (len(raw) != before.st_size or
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) !=
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns)):
            raise Conflict('Context source changed during reading')
        return raw

    def summary(self):
        return {'scope': 'ONE_CONTEXT_ACTION_SOURCE_RECORDS_AND_DATABASE_PAYLOADS',
            'charged_bytes': self.charged_bytes, 'byte_limit': self.byte_limit,
            'reads': self.reads, 'read_limit': self.read_limit,
            'physical_disk_io': 'NOT_MEASURED', 'model_calls': 0}


def current_context_read_budget():
    return _CURRENT.get()


@contextmanager
def context_read_scope():
    existing = _CURRENT.get()
    if existing is not None:
        yield existing
        return
    budget = ContextReadBudget()
    token = _CURRENT.set(budget)
    try:
        yield budget
    finally:
        _CURRENT.reset(token)


def source_bytes(path, maximum=128 * 1024 * 1024):
    budget = _CURRENT.get()
    return budget.file(path, maximum) if budget is not None else Path(path).read_bytes()


def source_regular(reader, path, maximum):
    budget = _CURRENT.get()
    return budget.guarded(lambda limit: reader(path, limit), maximum) if budget is not None else reader(path, maximum)
