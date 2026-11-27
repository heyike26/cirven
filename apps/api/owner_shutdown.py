"""Close only this application's current owners; never adopt persisted PIDs."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future as ThreadFuture
import copy
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid

from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest, now


def _blocked(value):
    if isinstance(value, dict):
        return any((key in {"status", "owner_status"} and isinstance(item, str) and
                    ("UNKNOWN" in item or item.startswith("BLOCKED") or item in {"FAILED", "ERROR"}))
                   or _blocked(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_blocked(item) for item in value)
    return False


class OwnerShutdown:
    """One shielded close attempt, with retained handle evidence after registry pop.

    A close failure is sticky: inspection never retries an action. Old receipts
    remain readable after restart, but never authorize closing a new controller.
    """

    def __init__(self, app, data_dir, *, close_timeout_seconds=30):
        self.app, self.data_dir = app, Path(data_dir)
        self.timeout = close_timeout_seconds
        self.shutdown_ref = uuid.uuid4().hex
        self.draining = False
        self._task = self._loop = self._receipt = None
        self._requests = {}
        self._closers = []
        self._admitted_at_seal = 0

    @staticmethod
    def close_route(path):
        return path in {"/api/shutdown", "/api/desktop-host/collaboration/close", "/api/desktop-host/close"}

    @classmethod
    def allowed_during_close(cls, method, path):
        if method in {"GET", "HEAD"}:
            return True
        if method != "POST":
            return False
        return cls.close_route(path) or bool(re.fullmatch(
            r"/api/projects/[^/]+/agent-sessions/[^/]+/(?:interrupt|disconnect)|"
            r"/api/desktop-host/native-messages/[^/]+/cancel|"
            r"/api/desktop-host/native-tasks/revoke|"
            r"/api/projects/[^/]+/(?:planning/operations|direct-actions)/[^/]+/cancel", path))

    def enter(self, method, path):
        if self.draining and not self.allowed_during_close(method, path):
            raise Conflict("Application owners are closing; inspect the recorded operation instead of submitting new work")
        if method in {"GET", "HEAD"} or self.allowed_during_close(method, path):
            return None
        token = object()
        self._requests[token] = (path, asyncio.Event())
        return token

    def leave(self, token):
        row = self._requests.pop(token, None)
        if row:
            row[1].set()

    def _path(self, suffix):
        return resolve_data_path("owner-shutdown/" + self.shutdown_ref + suffix, self.data_dir)

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = json.dumps({"value": value, "sha256": digest(value)}, ensure_ascii=False, sort_keys=True).encode()
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)

    def inspect(self, reference=None):
        if reference is None or reference == self.shutdown_ref:
            if self._receipt is not None:
                return copy.deepcopy(self._receipt)
            if reference is None:
                return {"status": "CLOSING" if self.draining else "OPEN", "shutdown_ref": self.shutdown_ref,
                        "outcomes": [], "automatic_retry": False, "pending_request_count": len(self._requests)}
        if not isinstance(reference, str) or not re.fullmatch(r"[a-f0-9]{32}", reference):
            raise NotFound("Owner shutdown receipt does not exist")
        for suffix in (".json", ".started.json"):
            path = resolve_data_path("owner-shutdown/" + reference + suffix, self.data_dir)
            if path.is_file():
                if path.stat().st_size > 131072:
                    raise Conflict("Owner shutdown receipt is oversized")
                try:
                    record = json.loads(path.read_bytes())
                    if (set(record) != {"value", "sha256"} or digest(record["value"]) != record["sha256"]
                            or record["value"]["shutdown_ref"] != reference):
                        raise ValueError()
                except (ValueError, TypeError, KeyError):
                    raise Conflict("Owner shutdown receipt integrity changed") from None
                return {**record["value"], "receipt_ref": {"id": reference, "sha256": record["sha256"]}}
        raise NotFound("Owner shutdown receipt does not exist")

    def _specs(self):
        state = self.app.state
        tasks = getattr(state, "native_tasks", None)
        sessions = getattr(state, "agent_sessions", None)
        messages = getattr(state, "native_messages", None)
        planning = getattr(getattr(state, "planning_proposals", None), "transport", None)
        reviews = getattr(state, "native_reviews", None)
        bridge = getattr(reviews, "bridge", None)
        selected = getattr(bridge, "selected", bridge)
        runtime = getattr(state, "native_collaboration_runtime", None)
        subscription = getattr(state, "subscription_account", None)
        return [
            ("NATIVE_TASKS", tasks, [tasks, getattr(tasks, "transport", None)]),
            ("AGENT_SESSIONS", sessions, [sessions, getattr(state, 'canonical_conversations', None)]),
            ("NATIVE_MESSAGES", messages, [messages, getattr(messages, "transport", None),
                                            getattr(messages, "ordinary", None)]),
            ("PLANNING", planning, [planning]),
            ("NATIVE_REVIEWS", reviews, [reviews, selected]),
            ("DESKTOP_HOST", getattr(state, "desktop_host", None), []),
            *([("SUBSCRIPTION_ACCOUNT", subscription, [subscription])] if subscription is not None else []),
            ("RUNTIME_SDK", runtime, [runtime, getattr(runtime, "cua", None)]),
        ]

    @staticmethod
    def _capture(nodes, contexts, processes, tasks, threads):
        def retain(resource):
            if resource is None:
                return
            for process in getattr(resource, "owned_processes", ()):
                if isinstance(process, (subprocess.Popen, asyncio.subprocess.Process)):
                    processes[id(process)] = process
            for thread in getattr(resource, "owned_threads", ()):
                if isinstance(thread, threading.Thread):
                    threads[id(thread)] = thread
            for task in getattr(resource, "owned_tasks", ()):
                if isinstance(task, (asyncio.Future, ThreadFuture)):
                    tasks[id(task)] = task

        active_count = 0
        for node in nodes:
            if node is None:
                continue
            retain(node)
            for bridge in getattr(node, "owned_bridges", ()):
                retain(bridge)
            thread = getattr(getattr(node, "worker", None), "thread", None)
            if isinstance(thread, threading.Thread):
                threads[id(thread)] = thread
            active = getattr(node, "active", None)
            if isinstance(active, dict):
                active_count += len(active)
                for context in list(active.values()):
                    if isinstance(context, dict):
                        contexts[id(context)] = context
            for context in getattr(node, "pending_cleanup_contexts", ()):
                contexts[id(context)] = context
                active_count += 1
            for name in ("_owners", "tasks", "jobs"):
                rows = getattr(node, name, None)
                if isinstance(rows, dict):
                    for task in list(rows.values()):
                        if isinstance(task, (asyncio.Future, ThreadFuture)):
                            tasks[id(task)] = task
        for context in list(contexts.values()):
            process = context.get("process")
            if process is not None:
                processes[id(process)] = process
            for name in ('listener','monitor','writer','cleanup_task'):
                task=context.get(name)
                if isinstance(task,(asyncio.Future,ThreadFuture)):tasks[id(task)]=task
            for resource in [context.get('wire'), context.get('relay'), context.get('broker'), context.get('received_writer'), *context.get('workflow_wires', []),
                             (context.get('native_review') or {}).get('bridge'),
                             (context.get('native_message') or {}).get('bridge'),
                             (context.get('model_authority') or {}).get('bridge')]:
                retain(resource)
        return active_count

    async def _wait_requests(self, *, startups_only=False, timeout=1):
        rows = list(self._requests.values())
        if startups_only:
            rows = [(path, event) for path, event in rows if re.fullmatch(
                r"/api/agent-sessions/open|/api/projects/[^/]+/agent-sessions(?:/[^/]+/load|/open)|"
                r"/api/subscription/login|/api/conversations/[^/]+/messages", path)]
        if not rows:
            return 0
        waiters = [asyncio.create_task(event.wait()) for _path, event in rows]
        _done, pending = await asyncio.wait(waiters, timeout=timeout)
        for task in pending:
            task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        return len(pending)

    async def _invoke(self, kind, service):
        callback = getattr(service, "close", None)
        if not callable(callback):
            raise RuntimeError("OWNER_CLOSE_UNAVAILABLE")
        result = callback() if inspect.iscoroutinefunction(callback) else await asyncio.to_thread(callback)
        if inspect.isawaitable(result):
            result = await result
        # Native reviews also has legacy native executions admitted directly
        # through its existing bridge. Cancel only current exact bridge owners.
        if kind == "NATIVE_REVIEWS":
            bridge = getattr(service, "bridge", None)
            for ident in list(getattr(bridge, "active", {})):
                with bridge.service.connect() as connection:
                    row = connection.execute("SELECT project_id FROM agent_executions WHERE id=?", (ident,)).fetchone()
                if row is None:
                    raise RuntimeError("CURRENT_REVIEW_OWNER_RECORD_UNAVAILABLE")
                await bridge.request_cancel(row["project_id"], ident)
        return result

    async def _close_one(self, spec, evidence):
        kind, service, nodes = spec
        contexts, processes, tasks, threads = evidence
        self._capture(nodes, contexts, processes, tasks, threads)
        result = None
        error_code = None
        close_call = "NOT_RUN"
        if service is not None:
            closer = asyncio.create_task(self._invoke(kind, service))
            self._closers.append(closer)
            # A timed-out close is retained, never replayed or cancelled. Its
            # late exception is consumed without leaking raw exception text.
            closer.add_done_callback(self._consume_close_exception)
            done, _pending = await asyncio.wait({closer}, timeout=self.timeout)
            if not done:
                close_call, error_code = "PENDING", "OWNER_CLOSE_PENDING"
            else:
                try:
                    result = closer.result()
                    close_call = "COMPLETED"
                except BaseException:
                    close_call, error_code = "RAISED", "OWNER_CLOSE_RAISED"
        active_count = self._capture(nodes, contexts, processes, tasks, threads)
        pending_processes = 0
        for process in processes.values():
            try:
                stopped = (process.returncode is not None if isinstance(process, asyncio.subprocess.Process)
                           else process.poll() is not None if isinstance(process, subprocess.Popen) else False)
            except Exception:
                stopped = False
            pending_processes += not stopped
        missing = [context for context in contexts.values() if context.get("process") is None]
        no_process = sum(kind == "AGENT_SESSIONS" and context.get("process_start_evidence") in
                         {"NOT_ATTEMPTED", "OS_REFUSED"} for context in missing)
        unobserved = len(missing) - no_process
        pending_tasks = sum(not task.done() for task in tasks.values())
        pending_threads = sum(thread.is_alive() for thread in threads.values())
        blocked = _blocked(result) or (service is not None and kind in {"NATIVE_TASKS", "DESKTOP_HOST", "SUBSCRIPTION_ACCOUNT", "RUNTIME_SDK"}
            and (not isinstance(result, dict) or result.get("status") != "CLOSED"))
        unknown = bool(error_code or blocked or pending_processes or unobserved or pending_tasks or pending_threads or active_count)
        return {"owner_kind": kind, "status": "CLOSE_UNKNOWN" if unknown else "CLOSED" if service is not None else "NOT_ACTIVE",
                "close_call": close_call, "captured_process_count": len(processes),
                "stopped_process_count": len(processes) - pending_processes,
                "pending_process_count": pending_processes, "unobserved_owner_count": unobserved,
                "confirmed_no_process_count": no_process,
                "active_context_count": active_count, "pending_task_count": pending_tasks,
                "captured_thread_count": len(threads), "pending_thread_count": pending_threads,
                "stopped_thread_count": len(threads) - pending_threads,
                "blocked": blocked, **({"error_code": error_code} if error_code else {})}

    @staticmethod
    def _consume_close_exception(task):
        if not task.cancelled():
            task.exception()

    async def _run(self):
        specs = self._specs()
        evidence = [({}, {}, {}, {}) for _spec in specs]
        for (_kind, _service, nodes), (contexts, processes, tasks, threads) in zip(specs, evidence):
            self._capture(nodes, contexts, processes, tasks, threads)
        # These are existing admission flags. Runtime.closed is deliberately
        # untouched: setting it early would bypass its actual SDK cleanup.
        for kind, service, _nodes in specs:
            if service is not None and kind in {"NATIVE_TASKS", "AGENT_SESSIONS", "NATIVE_MESSAGES", "PLANNING"}:
                service.closed = True
            if service is not None and kind == "NATIVE_REVIEWS":
                service.closing = True
        receipt = {"status": "CLOSING", "shutdown_ref": self.shutdown_ref, "started_at": now(),
                   "outcomes": [], "automatic_retry": False, "process_evidence": "CURRENT_OWNED_HANDLES_ONLY",
                   "admitted_request_count": self._admitted_at_seal}
        persistence_failed = False
        try:
            self._write(self._path(".started.json"), receipt)
        except Exception:
            persistence_failed = True
        # Revoke input before waiting for an already-admitted readonly startup.
        receipt["outcomes"].append(await self._close_one(specs[0], evidence[0]))
        starts_pending = await self._wait_requests(startups_only=True, timeout=self.timeout)
        for spec, captured in zip(specs[1:], evidence[1:]):
            receipt["outcomes"].append(await self._close_one(spec, captured))
        mutations_pending = await self._wait_requests(timeout=1)
        receipt["pending_request_count"] = mutations_pending
        if starts_pending or mutations_pending:
            receipt["outcomes"].append({"owner_kind": "ADMITTED_REQUESTS", "status": "CLOSE_UNKNOWN",
                "close_call": "NOT_RUN", "blocked": True, "pending_request_count": max(starts_pending, mutations_pending)})
        if persistence_failed:
            receipt["outcomes"].append({"owner_kind": "RECEIPT", "status": "CLOSE_UNKNOWN",
                "close_call": "RAISED", "blocked": True, "error_code": "START_RECEIPT_UNAVAILABLE"})
        receipt.update(status="CLOSE_UNKNOWN" if any(row["status"] == "CLOSE_UNKNOWN" for row in receipt["outcomes"]) else "CLOSED", finished_at=now())
        try:
            self._write(self._path(".json"), receipt)
            receipt["receipt_ref"] = {"id": self.shutdown_ref, "sha256": digest(receipt)}
        except Exception:
            receipt["status"] = "CLOSE_UNKNOWN"
            receipt["outcomes"].append({"owner_kind": "RECEIPT", "status": "CLOSE_UNKNOWN", "close_call": "RAISED",
                "blocked": True, "error_code": "FINAL_RECEIPT_UNAVAILABLE"})
        self._receipt = receipt
        return copy.deepcopy(receipt)

    async def close(self):
        if self._receipt is not None:
            return copy.deepcopy(self._receipt)
        if self._task is None:
            self.draining = True
            self._admitted_at_seal = len(self._requests)
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._run())
        if self._loop is not asyncio.get_running_loop():
            if not self._loop.is_running():
                return {"status": "CLOSE_UNKNOWN", "shutdown_ref": self.shutdown_ref, "outcomes": [],
                        "error_code": "OWNER_CLOSE_LOOP_UNAVAILABLE", "automatic_retry": False}
            future = asyncio.run_coroutine_threadsafe(self.close(), self._loop)
            return await asyncio.wrap_future(future)
        return copy.deepcopy(await asyncio.shield(self._task))


def register_owner_shutdown(app, data_dir):
    existing = getattr(app.state, "owner_shutdown", None)
    if existing is not None:
        return existing
    value = OwnerShutdown(app, data_dir)
    app.state.owner_shutdown = value

    @app.get("/api/owner-shutdown")
    def current():
        return value.inspect()

    @app.get("/api/owner-shutdown/{shutdown_ref}")
    def recorded(shutdown_ref: str):
        return value.inspect(shutdown_ref)

    return value
