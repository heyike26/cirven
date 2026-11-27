"""Persistent read-only supervision. This object has no model or task mutation API.

``collect`` returns existing TaskObservation models/dicts, or a bounded result
with observations and unavailable_codes. Labels and unavailable codes must be
safe application summaries, never prompts or credentials. Coverage is only the
collected sources. start/close belong to the owning AgentSessions lifecycle.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Awaitable, Callable, Literal, Sequence
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SupervisorInvalidInput(ValueError): pass
class SupervisorConflict(RuntimeError): pass
class SupervisorIntegrityError(RuntimeError): pass


SOURCE_UNAVAILABLE_CODES = frozenset({
    "SOURCE_UNAVAILABLE", "ORIGINAL_SESSION_OBSERVATION_UNAVAILABLE",
    "NATIVE_MESSAGE_OBSERVATION_UNAVAILABLE", "NATIVE_MESSAGE_ADAPTER_UNAVAILABLE",
    "DISCOVERY_RESULT_INTEGRITY_FAILED", "PLANNING_RESULT_INTEGRITY_FAILED",
})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskKey(_Strict):
    kind: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")
    object_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,200}$")
    operation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")
    generation: int = Field(default=0, ge=0, strict=True)


class EvidenceRef(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,200}$")
    revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TaskDisplayContext(_Strict):
    """Public navigation hints, never execution identity or authorization."""
    project_label: str | None = Field(default=None, min_length=1, max_length=200)
    workspace_label: str | None = Field(default=None, min_length=1, max_length=200)
    conversation_created_at: str | None = Field(default=None, max_length=64)
    task_created_at: str | None = Field(default=None, max_length=64)

    @field_validator("project_label", "workspace_label")
    @classmethod
    def safe_label(cls, value):
        return TaskObservation.safe_label(value) if value is not None else None

    @field_validator("conversation_created_at", "task_created_at")
    @classmethod
    def timestamp(cls, value):
        return _parse_time(value).isoformat() if value is not None else None


class TaskObservation(_Strict):
    key: TaskKey
    label: str = Field(min_length=1, max_length=200)
    source_status: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
    observed_at: str
    actual_error_code: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")
    execution_returned: bool | None = Field(default=None, strict=True)
    validation_status: Literal["NOT_OBSERVED", "NOT_RUN", "PENDING", "PASSED", "FAILED"] = "NOT_OBSERVED"
    wait_reason: Literal["USER_INPUT", "PERMISSION"] | None = None
    coverage: Literal["SOURCE_SNAPSHOT", "PARTIAL", "UNKNOWN"] = "PARTIAL"
    source_ref: EvidenceRef | None = None
    fresh_for_seconds: int = Field(default=120, ge=5, le=86400, strict=True)
    display_context: TaskDisplayContext | None = None

    @field_validator("label")
    @classmethod
    def safe_label(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("A safe single-line application label is required")
        value.encode("utf-8")
        if re.search(r"(?i)(?:bearer\s+\S+|sk-[a-z0-9_-]{12,}|(?:api[_-]?key|password|access[_-]?token)\s*[:=])", value):
            raise ValueError("Credential-like labels are not accepted")
        return value

    @field_validator("observed_at")
    @classmethod
    def timestamp(cls, value):
        parsed = _parse_time(value)
        return parsed.isoformat()


def _parse_time(value):
    if not isinstance(value, str): raise ValueError("A timezone-aware timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None: raise ValueError("A timezone-aware timestamp is required")
    return parsed.astimezone(timezone.utc)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value): return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _classification(value):
    status = value["source_status"].upper()
    code = (value.get("actual_error_code") or "").upper()
    if status in {"TIMED_OUT", "TIMEOUT"} or code in {
        "TIMEOUT", "TIMED_OUT", "MODEL_TIMEOUT", "REQUEST_TIMEOUT", "DEADLINE_EXCEEDED", "SERVICE_TIMEOUT",
        "TIMEOUTERROR", "CONNECTTIMEOUT", "READTIMEOUT", "WRITETIMEOUT", "POOLTIMEOUT", "HTTP_TIMEOUT"}:
        return "TIMED_OUT"
    if status in {"CANCEL_REQUESTED", "CANCELLING"}: return "CANCEL_REQUESTED"
    if status in {"CANCELLED", "CANCELED"}: return "CANCELLED"
    if status in {"FAILED", "ERROR"}: return "EXECUTION_ERROR"
    if value.get("wait_reason") or status in {"WAITING_USER", "WAITING_INPUT", "NEEDS_INPUT", "WAITING_PERMISSION", "QUESTION_PENDING", "PERMISSION_PENDING"}:
        return "WAITING_USER"
    if value.get("execution_returned") is True or status in {"RETURNED", "COMPLETED", "SUCCEEDED", "APPLIED", "NO_CHANGE"}:
        return "EXECUTION_RETURNED"
    if status in {"RUNNING", "REQUESTING", "PROCESSING", "STARTING", "DISPATCHING", "ACCEPTED", "QUEUED", "BUSY"}:
        return "RUNNING"
    return "NEEDS_CHECK"


class GlobalSupervisor:
    """One read-only collector loop, persistent CAS settings and exact alerts.

    ``data_dir`` is trusted application storage. ``clock`` returns epoch seconds.
    ``on_change(snapshot)`` is optional and may be async; notification failures
    never change known execution outcomes. Settings default to on/30s, system
    notifications off. update_settings and wake are synchronous/thread-safe.
    Events are only bounded dirty hints: all state still comes from ``collect``.
    """
    EVENT_DEBOUNCE_SECONDS = .1

    def __init__(self, data_dir: Path, collect: Callable[[], Awaitable[Sequence[TaskObservation | dict] | dict]], *,
                 clock: Callable[[], float] | None = None, on_change=None):
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "supervisor.sqlite3"
        self.collect, self.clock, self.on_change = collect, clock or time.time, on_change
        self._mutex = threading.RLock()
        self._poll_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._loop = None
        self._worker = None
        self._active_polls = set()
        self._closed = False
        self._dirty_deadline = None
        self._settings_pending = False
        self._signal_scheduled = False
        self._change_waiters = set()
        self._run_id = uuid.uuid4().hex
        with self._db() as connection:
            connection.executescript("""CREATE TABLE IF NOT EXISTS records(
                kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS alerts(
                id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                acknowledged_at TEXT);""")
            with self._transaction(connection):
                self._insert_default(connection, "settings", {"enabled": True, "interval_seconds": 30,
                    "notifications_enabled": False, "revision": 1})
                self._insert_default(connection, "observer", {"status": "INITIAL", "last_attempt_at": None,
                    "last_success_at": None, "actual_error_code": None, "unavailable_codes": [],
                    "notification_status": "NOT_ATTEMPTED"})
                self._insert_default(connection, "change", {"sequence": 0})
                # Persistent transport metadata, not a second task-state source.
                # Restart must release clients holding a previous process cursor.
                self._advance_change(connection)

    @contextmanager
    def _db(self):
        with self._mutex:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA busy_timeout=5000")
                yield connection
            finally: connection.close()

    @staticmethod
    @contextmanager
    def _transaction(connection):
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _insert_default(connection, kind, value):
        connection.execute("INSERT OR IGNORE INTO records VALUES(?,?,?,?)", (kind, "singleton", _canonical(value), _digest(value)))

    @staticmethod
    def _checked(row):
        if row is None: raise SupervisorIntegrityError("Supervisor record is missing")
        try: value = json.loads(row["payload"])
        except (ValueError, TypeError) as error: raise SupervisorIntegrityError("Supervisor record is unreadable") from error
        if _digest(value) != row["sha256"]: raise SupervisorIntegrityError("Supervisor record integrity mismatch")
        return value

    def _read(self, connection, kind, ident="singleton"):
        return self._checked(connection.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, ident)).fetchone())

    @staticmethod
    def _write(connection, kind, ident, value):
        connection.execute("INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,sha256=excluded.sha256",
                           (kind, ident, _canonical(value), _digest(value)))

    def _stamp(self): return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()

    def _change_value(self, connection):
        value = self._read(connection, "change")
        if set(value) != {"sequence"} or type(value["sequence"]) is not int or value["sequence"] < 0:
            raise SupervisorIntegrityError("Supervisor change cursor is invalid")
        return value["sequence"]

    def _advance_change(self, connection):
        sequence = self._change_value(connection) + 1
        self._write(connection, "change", "singleton", {"sequence": sequence})
        return sequence

    def change_sequence(self):
        with self._db() as connection: return self._change_value(connection)

    def _bind_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop and self._loop is not loop: raise SupervisorConflict("Supervisor belongs to another event loop")
        self._loop = loop
        return loop

    def _broadcast_change(self):
        for event in tuple(self._change_waiters): event.set()

    def _signal_change(self):
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._broadcast_change)

    async def wait_for_change(self, after: int, timeout: float = 25):
        """Wait for committed local observations/settings/ack, never collect.

        Cursors survive restarts. A mismatched (including future) cursor returns
        immediately so clients can recover with GET snapshot. Timeout returns the
        unchanged cursor; close advances it and releases every waiting reader.
        """
        if (type(after) is not int or after < 0 or type(timeout) not in (int, float) or
            not math.isfinite(timeout) or not 0 < timeout <= 25):
            raise SupervisorInvalidInput("Invalid supervisor change wait")
        if self._closed: return self.change_sequence()
        self._bind_loop()
        event = asyncio.Event()
        self._change_waiters.add(event)
        try:
            deadline = time.monotonic() + timeout
            while True:
                event.clear()
                sequence = self.change_sequence()
                if self._closed or after != sequence: return sequence
                remaining = deadline - time.monotonic()
                if remaining <= 0: return sequence
                try: await asyncio.wait_for(event.wait(), remaining)
                except asyncio.TimeoutError: return self.change_sequence()
        finally: self._change_waiters.discard(event)

    def settings(self):
        with self._db() as connection: value = self._read(connection, "settings")
        if (set(value) != {"enabled", "interval_seconds", "notifications_enabled", "revision"} or
            type(value["enabled"]) is not bool or type(value["notifications_enabled"]) is not bool or
            type(value["interval_seconds"]) is not int or not 5 <= value["interval_seconds"] <= 3600 or
            type(value["revision"]) is not int or value["revision"] < 1):
            raise SupervisorIntegrityError("Supervisor settings are invalid")
        return {**value, "sha256": _digest(value)}

    def update_settings(self, *, enabled: bool, interval_seconds: int, expected_revision: int,
                        notifications_enabled: bool | None = None):
        if (type(enabled) is not bool or type(interval_seconds) is not int or not 5 <= interval_seconds <= 3600 or
            type(expected_revision) is not int or expected_revision < 1 or
            notifications_enabled is not None and type(notifications_enabled) is not bool):
            raise SupervisorInvalidInput("Invalid supervisor settings")
        with self._db() as connection, self._transaction(connection):
            current = self._read(connection, "settings")
            if current["revision"] != expected_revision: raise SupervisorConflict("Supervisor settings changed; read the current revision")
            next_value = {"enabled": enabled, "interval_seconds": interval_seconds,
                "notifications_enabled": current["notifications_enabled"] if notifications_enabled is None else notifications_enabled,
                "revision": current["revision"]}
            if next_value != current:
                next_value["revision"] += 1
                self._write(connection, "settings", "singleton", next_value)
                self._advance_change(connection)
                self._settings_pending = True
                if not enabled: self._dirty_deadline = None
        if next_value != current:
            self._signal_change()
            self._signal()
        return {**next_value, "sha256": _digest(next_value)}

    def _signal(self):
        # At most one queued loop callback, even for a cross-thread event storm.
        with self._mutex:
            if not self._loop or self._loop.is_closed() or self._signal_scheduled: return
            self._signal_scheduled = True
            self._loop.call_soon_threadsafe(self._set_wake)

    def _set_wake(self):
        with self._mutex: self._signal_scheduled = False
        self._wake.set()

    def wake(self, reason: str, source_key: TaskKey | dict | None = None):
        """Coalesce owner-change hints; never accept outcomes or invoke a model.

        Returns QUEUED/COALESCED/DISABLED/CLOSED. The first dirty hint starts a
        fixed 100ms window; later hints never postpone it. Identity is validated
        but not retained or used as state. One dirty bit bounds memory and permits
        at most one follow-up collect for events arriving during an active poll.
        Calling before start queues a hint but does not start a worker.
        """
        if not isinstance(reason, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", reason):
            raise SupervisorInvalidInput("A safe supervisor wake reason code is required")
        if source_key is not None:
            try: TaskKey.model_validate(source_key.model_dump() if isinstance(source_key, TaskKey) else source_key)
            except (TypeError, ValueError) as error:
                raise SupervisorInvalidInput("An exact task identity is required") from error
        with self._mutex:
            if self._closed: return "CLOSED"
            if self._dirty_deadline is not None: return "COALESCED"
            if not self.settings()["enabled"]: return "DISABLED"
            self._dirty_deadline = time.monotonic() + self.EVENT_DEBOUNCE_SECONDS
            self._signal()
            return "QUEUED"

    @staticmethod
    def _observation_status(value, epoch):
        return "STALE" if epoch - _parse_time(value["observed_at"]).timestamp() > value["fresh_for_seconds"] else "CURRENT"

    def _task_view(self, value):
        result = dict(value)
        result.pop("fingerprint", None)
        run = result.pop("run_id", None)
        result["observation_status"] = self._observation_status(result, self.clock())
        if run != self._run_id and result["classification"] in {"RUNNING", "CANCEL_REQUESTED", "WAITING_USER"}:
            result["last_classification"] = result["classification"]
            result["classification"] = "NEEDS_CHECK"
            result["observation_status"] = "NEEDS_CHECK_AFTER_RESTART"
        return result

    def snapshot(self, *, include_alerts=True, alert_limit=100):
        if type(include_alerts) is not bool or type(alert_limit) is not int or not 1 <= alert_limit <= 1000:
            raise SupervisorInvalidInput("Invalid supervisor snapshot range")
        with self._db() as connection:
            observer = self._read(connection, "observer")
            change_sequence = self._change_value(connection)
            tasks = [self._task_view(self._checked(row)) for row in connection.execute("SELECT * FROM records WHERE kind='task' ORDER BY id")]
            alerts_total = connection.execute("SELECT count(*) FROM alerts").fetchone()[0]
            alerts = []
            if include_alerts:
                for row in connection.execute("SELECT * FROM alerts ORDER BY rowid DESC LIMIT ?", (alert_limit,)):
                    value = self._checked(row)
                    ref = {"id": row["id"], "revision": row["revision"], "sha256": row["sha256"]}
                    alerts.append({**value, **ref, "alert_ref": ref, "acknowledged": row["acknowledged_at"] is not None,
                                   "acknowledged_at": row["acknowledged_at"]})
        settings = self.settings()
        observer.setdefault("unavailable_codes", [])
        observer["running"] = self._worker is not None and not self._worker.done()
        if self._closed: observer["status"] = "CLOSED"
        elif not settings["enabled"]: observer["status"] = "DISABLED"
        return {"settings": settings, "observer": observer, "tasks": tasks, "alerts": alerts,
                "alerts_total": alerts_total, "alerts_has_more": alerts_total > len(alerts),
                "change_sequence": change_sequence,
                "coverage": "ONLY_COLLECTED_EXISTING_TASKS"}

    def _alert(self, connection, value):
        ident = uuid.uuid4().hex
        payload = {**value, "created_at": self._stamp()}
        connection.execute("INSERT INTO alerts VALUES(?,?,?,?,NULL)", (ident, 1, _canonical(payload), _digest(payload)))

    def ack(self, ref):
        try:
            if set(ref) != {"id", "revision", "sha256"}: raise ValueError()
            valid = EvidenceRef.model_validate(ref).model_dump()
        except (TypeError, ValueError) as error: raise SupervisorInvalidInput("An exact supervisor alert ref is required") from error
        with self._db() as connection, self._transaction(connection):
            row = connection.execute("SELECT * FROM alerts WHERE id=?", (valid["id"],)).fetchone()
            if row is None: raise SupervisorConflict("Supervisor alert is unavailable")
            self._checked(row)
            if row["revision"] != valid["revision"] or row["sha256"] != valid["sha256"]:
                raise SupervisorConflict("Supervisor alert reference changed")
            at = row["acknowledged_at"] or self._stamp()
            connection.execute("UPDATE alerts SET acknowledged_at=? WHERE id=?", (at, valid["id"]))
            if row["acknowledged_at"] is None: self._advance_change(connection)
        if row["acknowledged_at"] is None: self._signal_change()
        return {"alert_ref": valid, "acknowledged": True, "acknowledged_at": at}

    async def _notify(self):
        if not self.on_change: return
        try:
            result = self.on_change(self.snapshot())
            if inspect.isawaitable(result): await result
        except Exception:
            with self._db() as connection, self._transaction(connection):
                value = self._read(connection, "observer")
                changed = value["notification_status"] != "UNAVAILABLE"
                value["notification_status"] = "UNAVAILABLE"
                self._write(connection, "observer", "singleton", value)
                if changed: self._advance_change(connection)
            if changed: self._signal_change()

    async def poll_once(self):
        task = asyncio.current_task()
        self._active_polls.add(task)
        try: return await self._poll()
        finally: self._active_polls.discard(task)

    async def _poll(self, *, respect_enabled=False):
        if self._closed: raise SupervisorConflict("Supervisor is closed")
        async with self._poll_lock:
            if self._closed: raise SupervisorConflict("Supervisor is closed")
            if respect_enabled and not self.settings()["enabled"]: return self.snapshot()
            attempt = self._stamp()
            try:
                raw = await self.collect()
                if self._closed: raise asyncio.CancelledError()
                unavailable_codes = []
                if isinstance(raw, dict):
                    if set(raw) != {"observations", "unavailable_codes"}:
                        raise SupervisorInvalidInput("Invalid collector result fields")
                    codes = raw["unavailable_codes"]
                    if (not isinstance(codes, (list, tuple)) or len(codes) > 32 or
                        any(not isinstance(code, str) or code not in SOURCE_UNAVAILABLE_CODES for code in codes)):
                        raise SupervisorInvalidInput("Invalid collector source codes")
                    unavailable_codes = sorted(set(codes))
                    raw = raw["observations"]
                if not isinstance(raw, (list, tuple)) or len(raw) > 16384: raise SupervisorInvalidInput("Collector result must be a bounded observation list")
                observations = [item if isinstance(item, TaskObservation) else TaskObservation.model_validate(item) for item in raw]
                identities = [_digest(item.key.model_dump()) for item in observations]
                if len(set(identities)) != len(identities): raise SupervisorInvalidInput("Duplicate task observation keys")
                changed = False
                with self._db() as connection, self._transaction(connection):
                    observer = self._read(connection, "observer")
                    next_status = "PARTIAL" if unavailable_codes else "READY"
                    changed = observer["status"] != next_status or observer.get("unavailable_codes", []) != unavailable_codes
                    if unavailable_codes and changed:
                        self._alert(connection, {"kind": "OBSERVER_STATE_CHANGED", "category": "OBSERVATION_UNAVAILABLE",
                            "key": None, "label": "部分任务来源暂不可观测", "actual_error_code": "SOURCE_UNAVAILABLE", "terminal": False})
                    for ident, model in zip(identities, observations):
                        value = model.model_dump()
                        if _parse_time(value["observed_at"]).timestamp() > self.clock() + 300:
                            raise SupervisorInvalidInput("Collector observation is in the future")
                        category = _classification(value)
                        observation_status = self._observation_status(value, self.clock())
                        fingerprint = _digest({**value["key"], "source_status": value["source_status"], "category": category,
                            "actual_error_code": value["actual_error_code"], "execution_returned": value["execution_returned"],
                            "validation_status": value["validation_status"], "wait_reason": value["wait_reason"],
                            "observation_status": observation_status, "coverage": value["coverage"]})
                        prior = connection.execute("SELECT * FROM records WHERE kind='task' AND id=?", (ident,)).fetchone()
                        previous = self._checked(prior) if prior else None
                        if not previous or previous["fingerprint"] != fingerprint:
                            changed = True
                            self._alert(connection, {"kind": "TASK_STATE_CHANGED", "category": category, "key": value["key"],
                                "label": value["label"], "source_status": value["source_status"], "actual_error_code": value["actual_error_code"],
                                "execution_returned": value["execution_returned"], "validation_status": value["validation_status"],
                                "observation_status": observation_status, "terminal": category in {"EXECUTION_RETURNED", "EXECUTION_ERROR", "TIMED_OUT", "CANCELLED"}})
                        self._write(connection, "task", ident, {**value, "classification": category, "last_checked_at": attempt,
                                    "fingerprint": fingerprint, "run_id": self._run_id})
                    observer.update(status=next_status, last_attempt_at=attempt, last_success_at=attempt,
                                    actual_error_code="SOURCE_UNAVAILABLE" if unavailable_codes else None,
                                    unavailable_codes=unavailable_codes)
                    self._write(connection, "observer", "singleton", observer)
                    self._advance_change(connection)
            except asyncio.CancelledError:
                raise
            except Exception:
                changed = False
                with self._db() as connection, self._transaction(connection):
                    observer = self._read(connection, "observer")
                    if observer["status"] != "OBSERVATION_UNAVAILABLE":
                        changed = True
                        self._alert(connection, {"kind": "OBSERVER_STATE_CHANGED", "category": "OBSERVATION_UNAVAILABLE",
                            "key": None, "label": "任务状态观察暂不可用", "actual_error_code": "COLLECT_FAILED", "terminal": False})
                    observer.update(status="OBSERVATION_UNAVAILABLE", last_attempt_at=attempt,
                                    actual_error_code="COLLECT_FAILED", unavailable_codes=[])
                    self._write(connection, "observer", "singleton", observer)
                    self._advance_change(connection)
            self._signal_change()
            if changed: await self._notify()
            return self.snapshot()

    async def start(self):
        if self._closed: raise SupervisorConflict("Supervisor is closed")
        self._bind_loop()
        if self._worker and not self._worker.done(): return
        self._worker = asyncio.create_task(self._run(), name="eeas-global-supervisor")

    async def _run(self):
        next_due = time.monotonic()
        while not self._closed:
            self._wake.clear()
            settings = self.settings()
            with self._mutex:
                settings_pending, self._settings_pending = self._settings_pending, False
                if not settings["enabled"]: self._dirty_deadline = None
                dirty_deadline = self._dirty_deadline
            if not settings["enabled"]:
                await self._wake.wait()
                continue
            if settings_pending: next_due = time.monotonic()
            due = min(next_due, dirty_deadline if dirty_deadline is not None else float("inf"))
            remaining = due - time.monotonic()
            if remaining > 0:
                try: await asyncio.wait_for(self._wake.wait(), remaining)
                except asyncio.TimeoutError: pass
                continue
            with self._mutex: self._dirty_deadline = None
            # The same lock fences manual, timer and event collectors. Recheck
            # enabled after acquiring it: a queued poll must not bypass disable.
            await self._poll(respect_enabled=True)
            next_due = time.monotonic() + self.settings()["interval_seconds"]

    async def close(self):
        with self._mutex:
            first_close = not self._closed
            self._closed = True
            self._dirty_deadline = None
        cursor_error = None
        if first_close:
            try:
                with self._db() as connection, self._transaction(connection): self._advance_change(connection)
            except Exception as error: cursor_error = error
            finally: self._signal_change()
        self._signal()
        tasks = {task for task in [self._worker, *self._active_polls] if task and task is not asyncio.current_task() and not task.done()}
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        self._worker = None
        if cursor_error is not None: raise cursor_error
