"""Local, model-free supervision of existing application-owned work.

Collection reads recorded facts. It cannot prompt, retry, abort, adopt a PID,
read screenshots, or turn an execution return into an acceptance result.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from packages.domain.tasks import digest
from packages.domain.store import Conflict, NotFound
from packages.domain.original_response import original_answer as _original_answer
from packages.domain.conversation_threads import source_identity
from packages.agent_sessions.supervisor import TaskDisplayContext, TaskObservation


class SupervisorSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)
    notifications_enabled: bool = Field(strict=True)
    interval_seconds: int = Field(ge=5, le=3600, strict=True)
    expected_revision: int = Field(ge=0, strict=True)


class SupervisorAlertRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) else None


def _generation(value):
    if type(value) is int and value >= 0:
        return value
    return int(hashlib.sha256(str(value).encode()).hexdigest()[:12], 16)


def _native_question_project(store, request):
    from apps.api.native_messages import NativeQuestionInput
    # Execution storage may belong to an internal owner project. Only the
    # explicit user question identifies user scope. File-ref formatting alone
    # does not prove source admission; this collector never reopens latest files.
    question = NativeQuestionInput.model_validate(request).model_dump()
    project_id = question['project_id']
    if project_id is not None:
        store.project(project_id)
    return project_id




class ApplicationTaskCollector:
    """Bounded source adapters; no transcript or ambient credential export."""

    def __init__(self, app, *, limit=256):
        self.app, self.limit = app, limit
        self.coverage = {}
        self._reads = set()
        self._labels = {}
        self._display_contexts = {}
        self._ordinary_projection = {}
        self.closed = False

    @staticmethod
    def _recent(paths, errors, error_code):
        candidates = []
        for path in paths:
            try:
                if path.is_symlink():
                    raise ValueError("Linked task source")
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                errors.append(error_code)
            except ValueError:
                errors.append(error_code)
        return [path for _mtime, path in sorted(candidates, key=lambda item: item[0], reverse=True)]

    @staticmethod
    def _observation(kind, object_id, status, facts, *, project_id=None,
                     operation_id=None, generation=0, wait_reason=None,
                     observed_at=None, source_ref=None, error_code=None,
                     validation="NOT_OBSERVED", label=None, display_context=None):
        return {
            "key": {"kind": kind, "project_id": project_id, "object_id": object_id,
                    "operation_id": operation_id, "generation": _generation(generation)},
            "label": label or {"original_session": "Agent 会话", "native_message": "当前页问答",
                      "discovery": "需求梳理", "planning": "任务拆解",
                      "execution": "任务执行"}.get(kind, "任务"),
            "source_status": status,
            "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
            "actual_error_code": _code(error_code),
            "execution_returned": True if status in {"RETURNED", "COMPLETED"} else None,
            "validation_status": validation, "wait_reason": wait_reason,
            "coverage": "PARTIAL", "fresh_for_seconds": 120,
            "source_ref": source_ref or {"id": operation_id or object_id, "revision": 1, "sha256": digest(facts)},
            **({"display_context": display_context} if display_context else {}),
        }

    @staticmethod
    def _display_name(value):
        if not isinstance(value, str) or len(value) > 200:
            return None
        try:
            return TaskObservation.safe_label(value.strip())
        except (ValueError, UnicodeError):
            return None

    def _task_label(self, project_id, source=None):
        """Public navigation metadata only; never derive a label from a prompt.

        Cache within this collection, so 32 operations of one owner require one
        metadata lookup. Labels confer no source, execution or file authority.
        """
        alias, identity = source_identity(source) if source else (None, None)
        key = (project_id, alias)
        if key in self._labels:
            return self._labels[key]
        title = project = None
        context = {}
        try:
            with self.app.state.store.connect() as connection:
                if project_id is not None:
                    row = connection.execute('SELECT substr(name,1,201) AS name FROM projects WHERE id=?',
                                             (project_id,)).fetchone()
                    project = self._display_name(row['name']) if row else None
                    if project:
                        context['project_label'] = project
                if alias:
                    row = connection.execute(
                        'SELECT substr(t.display_title,1,201) AS title,substr(t.created_at,1,65) AS created_at,'
                        'substr(a.identity_json,1,1025) AS identity_json,'
                        'substr(a.source_json,1,8193) AS source_json,a.source_sha256 '
                        'FROM conversation_source_aliases a JOIN conversation_threads t '
                        'ON t.conversation_id=a.conversation_id WHERE a.alias_id=? AND t.deleted_at IS NULL',
                        (alias,)).fetchone()
                    if row and json.loads(row['identity_json']) == identity:
                        saved = json.loads(row['source_json']) if row['source_json'] is not None else None
                        if (saved is None and row['source_sha256'] is None and source['kind'] == 'native'
                                or saved is not None and digest(saved) == row['source_sha256']
                                and source_identity(saved) == (alias, identity)):
                            title = self._display_name(row['title'])
                            created = self._display_time(row['created_at'])
                            if created:
                                context['conversation_created_at'] = created
        except (ValueError, TypeError, KeyError, sqlite3.Error, Conflict):
            # Display metadata damage cannot invent a failed execution.
            title = None
        self._labels[key] = title or project
        self._display_contexts[key] = context
        return self._labels[key]

    @staticmethod
    def _display_time(value):
        if not isinstance(value, str) or len(value) > 64:
            return None
        try:
            return TaskDisplayContext(task_created_at=value).task_created_at
        except (ValueError, UnicodeError):
            return None

    def _task_context(self, project_id, source, *, task_created_at=None):
        # Share the bounded label lookup. Do not reread prompts, files, current
        # workspace paths or an owner merely to distinguish two navigation rows.
        self._task_label(project_id, source)
        alias, _ = source_identity(source) if source else (None, None)
        context = dict(self._display_contexts.get((project_id, alias), {}))
        created = self._display_time(task_created_at)
        if created:
            context['task_created_at'] = created
        return context or None

    @staticmethod
    def _equivalent_display_state(row):
        if (row.get("wait_reason") or row.get("actual_error_code")
                or row.get("validation_status") == "FAILED"
                or row.get("observation_status", "CURRENT") != "CURRENT"):
            return None
        status = row.get("source_status")
        if status in {"RETURNED", "COMPLETED"} and row.get("execution_returned") is True:
            state = "EXECUTION_RETURNED"
        elif status in {"RUNNING", "REQUESTING", "PROCESSING", "STARTING", "DISPATCHING", "ACCEPTED", "QUEUED", "BUSY"} and row.get("execution_returned") is not True:
            state = "RUNNING"
        else:
            return None
        return state if row.get("classification", state) == state else None

    def project_snapshot(self, snapshot):
        """Read-only default-list projection; original records/details remain.

        No file/API/model reads. A stale proof, duplicate key, unavailable outer
        or independent inner attention/error always preserves the fallback row.
        Historical alerts retain their exact identity and pagination semantics.
        """
        observer = snapshot.get("observer")
        if observer is not None and observer.get("status") not in {"READY", "PARTIAL"}:
            return snapshot
        grouped = {}
        for row in snapshot.get("tasks", []):
            grouped.setdefault(digest(row["key"]), []).append(row)
        hidden = set()
        for proof in self._ordinary_projection.values():
            inner, outer = proof["inner"], proof["outer"]
            inner_key, outer_key = digest(inner["key"]), digest(outer["key"])
            left, right = grouped.get(inner_key, []), grouped.get(outer_key, [])
            if len(left) != 1 or len(right) != 1:
                continue
            current_inner, current_outer = left[0], right[0]
            if any(current.get("source_ref") != saved.get("source_ref")
                   or current.get("source_status") != saved.get("source_status")
                   for current, saved in ((current_inner, inner), (current_outer, outer))):
                continue
            state = self._equivalent_display_state(current_inner)
            if state is not None and state == self._equivalent_display_state(current_outer):
                hidden.add(inner_key)
        if not hidden:
            return snapshot
        return {**snapshot, "tasks": [row for row in snapshot["tasks"] if digest(row["key"]) not in hidden]}

    @staticmethod
    def _ordinary_inner_identity(messages, record, candidates):
        """Collapse only a proven inner owner of this already-collected task.

        Historical admissions are read as evidence, never as current execution
        authority. A missing/mismatched link keeps both original observations.
        Labels, internal project names and prompt prefixes confer no identity.
        """
        try:
            owner = record["owner"]
            generation = owner["generation"]
            if type(generation) is not int or generation < 1:
                return None
            identity = (owner["project_id"], owner["session_ref"]["id"],
                        record["native_operation_id"], generation)
            inner = candidates.get(identity)
            if inner is None:
                return None
            reference = record["native_message_ref"]
            parent = record["request"]["operation_id"]
            if (set(reference) != {"id", "revision", "sha256"}
                    or reference["id"] != parent or type(reference["revision"]) is not int
                    or reference["revision"] != 1):
                return None
            ordinary = getattr(messages, "ordinary", None)
            if ordinary is None:
                return None
            admission = ordinary.read("admissions", parent, authority=False)
            request = record["request"]
            project = (request.get("project_id")
                       or (request.get("file_source_ref") or {}).get("project_id")
                       or (request.get("file_link_ref") or {}).get("project_id"))
            if (admission["schema"] != "eeas.native-message-admission.v1"
                    or reference["sha256"] != digest(admission)
                    or admission["parent_operation_id"] != parent
                    or admission["body"] != request
                    or admission["body_sha256"] != record["request_sha256"]
                    or admission["request_sha256"] != digest(admission["request"])
                    or admission["source_host_generation"] != record["generation"]
                    or admission["project_id"] != project
                    or admission["request"]["project_id"] != project
                    or admission["conversation_id"] != request.get("conversation_id")
                    or admission["provider_profile_ref"] != request.get("provider_profile_ref")
                    or admission["request"]["provider_profile_ref"] != request.get("provider_profile_ref")):
                return None
            if (inner["admission_ref"] != reference
                    or inner["session_ref"] != owner["session_ref"]
                    or inner["provider_profile_ref"] != request.get("provider_profile_ref")
                    or project is not None and owner["project_id"] != project
                    or inner["raw_text_sha256"] != hashlib.sha256(request["raw_text"].encode("utf-8")).hexdigest()):
                return None
            cohort = dict(record["ordinary_cohort"])
            cohort_sha = cohort.pop("cohort_sha256")
            if (digest(cohort) != cohort_sha or cohort["native_message_ref"] != reference
                    or cohort["parent_operation_id"] != parent
                    or cohort["owner_ref"] != owner["session_ref"]
                    or type(cohort["generation"]) is not int or cohort["generation"] != generation
                    or cohort["native_operation_id"] != identity[2]
                    or not inner["native_message_id"] or not inner["original_session_id"]
                    or cohort["native_user_message_id"] != inner["native_message_id"]
                    or cohort["native_session_id"] != inner["original_session_id"]):
                return None
            return identity
        except (AttributeError, KeyError, TypeError, ValueError, Conflict, NotFound, OSError):
            return None

    def collect_sync(self):
        self._labels = {}
        self._display_contexts = {}
        state = self.app.state
        collected, covered_executions, errors = [], set(), []
        ordinary_candidates, covered_originals = {}, {}
        self.coverage = {"adapters": {}, "model_calls": 0, "automatic_retry": False,
                         "legacy_mock_worker_included": False}
        sessions = getattr(state, "agent_sessions", None)
        if sessions is not None:
            count = 0
            root = Path(sessions.data_dir) / "agent-sessions"
            candidates = self._recent(root.glob("*/*/manifest.json"), errors, "ORIGINAL_SESSION_OBSERVATION_UNAVAILABLE")
            for path in candidates[:self.limit]:
                pid, ident = path.parent.parent.name, path.parent.name
                try:
                    _manifest, session_ref = sessions._manifest(pid, ident)
                    view = sessions._state(pid, ident)
                    context = sessions.active.get(ident)
                    connected = bool(context and not context.get("closing") and
                                     context["process"].returncode is None)
                    operations = self._recent(path.parent.joinpath("operations").glob("*.json"),
                                              errors, "ORIGINAL_SESSION_OBSERVATION_UNAVAILABLE")
                    active_path = path.parent / "operations" / (digest(view["active_operation_id"]) + ".json") if view.get("active_operation_id") else None
                    candidates_operations = ([active_path] if active_path is not None and active_path.exists() else []) + [item for item in operations[:256] if item != active_path]
                    message_count = 0
                    for operation_path in candidates_operations:
                        op = sessions._read(operation_path)
                        if op.get("kind") != "MESSAGE":
                            continue
                        if message_count >= 32:
                            break
                        message_count += 1
                        status = op["status"]
                        active = view.get("active_operation_id") == op["id"]
                        pending = None
                        if active:
                            pending = "PERMISSION" if view.get("pending_permissions") else "USER_INPUT" if view.get("pending_questions") else None
                        if status not in {"RETURNED", "FAILED", "CANCELLED", "UNKNOWN_OUTCOME"} and not connected:
                            status = "UNKNOWN_OUTCOME"
                        if status == "UNKNOWN_OUTCOME":
                            pending = None
                        collected.append(self._observation("original_session", ident, status,
                            {"operation_ref": op["operation_ref"], "status": status,
                             "wait_reason": pending, "session_ref": session_ref},
                            project_id=pid, operation_id=op["id"],
                            generation=op.get("request", {}).get("generation", view.get("generation", 0)),
                            wait_reason=pending, source_ref=session_ref,
                            observed_at=view.get("observation_at") if active and connected else None,
                            error_code="ORIGINAL_OBSERVATION_UNAVAILABLE" if status == "UNKNOWN_OUTCOME" else op.get("error_type"),
                            validation="NOT_RUN", label=self._task_label(pid,
                                {'kind': 'original', 'project_id': pid, 'session_ref': session_ref}),
                            display_context=self._task_context(pid,
                                {'kind': 'original', 'project_id': pid, 'session_ref': session_ref},
                                task_created_at=op.get('created_at'))))
                        # Retain only bounded identity/digest metadata, not
                        # another copy of the original prompt/transcript.
                        if _manifest.get("native_message_admission") is not None:
                            request = op.get("request", {})
                            generation = request.get("generation")
                            request_sha = op.get("request_sha256")
                            if (type(generation) is int and generation >= 1
                                    and op.get("generation") == generation
                                    and request.get("operation_id") == op["id"]
                                    and op.get("session_ref") == request.get("session_ref") == session_ref
                                    and op.get("operation_ref") == {"id": op["id"], "request_sha256": request_sha}
                                    and isinstance(request.get("raw_text"), str)
                                    and request_sha == digest(request)):
                                ordinary_candidates[(pid, ident, op["id"], generation)] = {
                                    "admission_ref": _manifest["native_message_admission"],
                                    "session_ref": session_ref,
                                    "provider_profile_ref": _manifest.get("request", {}).get("provider_profile_ref"),
                                    "raw_text_sha256": hashlib.sha256(request["raw_text"].encode("utf-8")).hexdigest(),
                                    "native_message_id": op.get("native_message_id"),
                                    "original_session_id": op.get("original_session_id"),
                                    "observation": collected[-1]}
                        count += 1
                except Exception:
                    errors.append("ORIGINAL_SESSION_OBSERVATION_UNAVAILABLE")
            self.coverage["adapters"]["original_sessions"] = {
                "collected": count, "session_limit": self.limit, "operations_per_session_limit": 32,
                "truncated": len(candidates) > self.limit, "children": "ROOT_AND_DIRECT_CHILD_METADATA_ONLY"}

        messages = getattr(state, "native_messages", None)
        if messages is not None:
            try:
                root = messages.ws._path("native-messages")
                paths = self._recent(root.glob("*.json"), errors, "NATIVE_MESSAGE_OBSERVATION_UNAVAILABLE")
                count = 0
                for path in paths[:self.limit]:
                    try:
                        record = messages.ws._read(path, authority=False)
                        request = record["request"]
                        if digest(request) != record["request_sha256"] or path.stem != request["operation_id"]:
                            raise ValueError("Native message source identity changed")
                        project_id = _native_question_project(state.store, request)
                        status = record["status"]
                        execution = record.get("execution")
                        if execution:
                            if project_id is not None and execution.get("project_id") != project_id:
                                raise ValueError("Native execution differs from the exact question project")
                            covered_executions.add(execution["id"])
                            with state.store.connect() as connection:
                                row = connection.execute("SELECT execution_status FROM agent_executions WHERE id=? AND project_id=?",
                                                         (execution["id"], execution["project_id"])).fetchone()
                            if row is not None:
                                status = row["execution_status"]
                        if record.get("generation") != messages.generation and status in {"PROCESSING", "RUNNING", "QUEUED"}:
                            status = "UNKNOWN_OUTCOME"
                        collected.append(self._observation("native_message", request["operation_id"], status,
                            {"request_sha256": record["request_sha256"], "status": status},
                            project_id=project_id,
                            operation_id=request["operation_id"], generation=record.get("generation"),
                            error_code=record.get("error", {}).get("type"), validation="NOT_RUN",
                            label=self._task_label(project_id, {'kind': 'native', 'project_id': project_id,
                                'conversation_id': request['conversation_id']} if request.get('conversation_id') else None),
                            display_context=self._task_context(project_id, {'kind': 'native', 'project_id': project_id,
                                'conversation_id': request['conversation_id']} if request.get('conversation_id') else None)))
                        # Only a successfully collected outer task can cover
                        # its exact original MESSAGE owner. Orphans stay visible.
                        inner = self._ordinary_inner_identity(messages, record, ordinary_candidates)
                        if inner is not None and not record.get("error"):
                            inner_row, outer_row = ordinary_candidates[inner]["observation"], collected[-1]
                            equivalent = self._equivalent_display_state(inner_row)
                            if equivalent is not None and equivalent == self._equivalent_display_state(outer_row):
                                covered_originals[inner] = {"inner": inner_row, "outer": outer_row}
                        count += 1
                    except Exception:
                        errors.append("NATIVE_MESSAGE_OBSERVATION_UNAVAILABLE")
                self.coverage["adapters"]["native_messages"] = {"collected": count, "limit": self.limit,
                                                               "truncated": len(paths) > self.limit}
            except Exception:
                errors.append("NATIVE_MESSAGE_ADAPTER_UNAVAILABLE")

        with state.store.connect() as connection:
            for kind, table, object_column in (("discovery", "discovery_operations", "session_id"),
                                                ("planning", "planning_proposal_operations", "proposal_id")):
                rows = connection.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (self.limit + 1,)).fetchall()
                for row in rows[:self.limit]:
                    try:
                        result = json.loads(row["result"]) if row["result"] else {}
                        # A result checksum is required before trusting its fields.
                        if not isinstance(result, dict) or result and digest(result) != row["result_sha256"]:
                            raise ValueError("Result integrity changed")
                    except (ValueError, TypeError):
                        errors.append(kind.upper() + "_RESULT_INTEGRITY_FAILED")
                        continue
                    status = row["status"]
                    if result.get("status") in {"NEEDS_INPUT", "AWAITING_CONFIRMATION"}:
                        status = "NEEDS_INPUT"
                    native = result.get("native_execution")
                    if isinstance(native, dict) and isinstance(native.get("id"), str):
                        covered_executions.add(native["id"])
                    if kind == "discovery":
                        native_operation = ("discplan-" if row["kind"] == "PLAN" else "disc-") + digest([row["project_id"], row["operation_id"]])[:40]
                    else:
                        native_operation = row["operation_id"]
                    associated = connection.execute("SELECT id,execution_status FROM agent_executions WHERE project_id=? AND operation_id=?",
                                                    (row["project_id"], native_operation)).fetchone()
                    if associated is not None:
                        covered_executions.add(associated["id"])
                        if status in {"PROCESSING", "UNKNOWN_OUTCOME"} and associated["execution_status"] in {"UNKNOWN_OUTCOME", "CANCELLED", "FAILED", "TIMED_OUT"}:
                            status = associated["execution_status"]
                    collected.append(self._observation(kind, row[object_column], status,
                        {"request_sha256": row["request_sha256"], "result_sha256": row["result_sha256"], "status": status},
                        project_id=row["project_id"], operation_id=row["operation_id"],
                        wait_reason="USER_INPUT" if status == "NEEDS_INPUT" else None,
                        label=self._task_label(row['project_id'], {'kind': 'discovery',
                            'project_id': row['project_id'], 'object_ref': {'id': row[object_column]}}
                            if kind == 'discovery' else None),
                        display_context=self._task_context(row['project_id'], {'kind': 'discovery',
                            'project_id': row['project_id'], 'object_ref': {'id': row[object_column]}}
                            if kind == 'discovery' else None)))
                self.coverage["adapters"][kind] = {"limit": self.limit, "truncated": len(rows) > self.limit}
            rows = connection.execute("SELECT id,project_id,operation_id,execution_status,verification_status,request_hash,runtime "
                                      "FROM agent_executions ORDER BY rowid DESC LIMIT ?", (self.limit + 1,)).fetchall()
            for row in rows[:self.limit]:
                if row["id"] in covered_executions or row["runtime"] not in {
                        "opencode-http", "opencode-planning-http", "opencode-desktop-http"}:
                    continue
                collected.append(self._observation("execution", row["id"], row["execution_status"], dict(row),
                    project_id=row["project_id"], operation_id=row["operation_id"],
                    validation=row["verification_status"] if row["verification_status"] in
                    {"NOT_RUN", "PENDING", "PASSED", "FAILED"} else "NOT_OBSERVED",
                    label=self._task_label(row['project_id']),
                    display_context=self._task_context(row['project_id'], None)))
            self.coverage["adapters"]["executions"] = {"limit": self.limit, "truncated": len(rows) > self.limit}
        self._ordinary_projection = covered_originals
        self.coverage["original_native_task_deduplication"] = {
            "verified_inner_outer_relations": len(covered_originals),
            "basis": "EXACT_SAVED_ADMISSION_OWNER_OPERATION_AND_COHORT"}
        self.coverage["errors"] = sorted(set(errors))
        # A broken historical source must not stall unrelated live tasks.
        # Coverage failures remain visible without inventing failed task facts.
        return {"observations": collected, "unavailable_codes": sorted(set(errors))}

    async def __call__(self):
        if self.closed:
            raise RuntimeError("Application task reader is closed")
        # Cancelling an await does not stop its filesystem thread. Retain the
        # actual read task until it ends, and join it during owner shutdown.
        task = asyncio.create_task(asyncio.to_thread(self.collect_sync))
        self._reads.add(task)
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._reads.discard(task)

    async def close(self):
        self.closed = True
        if self._reads:
            await asyncio.gather(*list(self._reads), return_exceptions=True)
        self._reads.clear()


def register_supervisor_routes(app, data_dir):
    from packages.agent_sessions.supervisor import GlobalSupervisor

    collector = ApplicationTaskCollector(app)
    supervisor = GlobalSupervisor(Path(data_dir), collector)
    app.state.global_supervisor, app.state.supervisor_collector = supervisor, collector
    # This observer is a subordinate local resource, not a second Agent owner
    # or model loop. The existing owner closes it before closing its sessions.
    app.state.agent_sessions.global_supervisor = supervisor
    app.state.agent_sessions.supervisor_collector = collector
    app.state.agent_sessions.on_observed_change = lambda: supervisor.wake("OWNER_CHANGED")

    def response():
        return {**collector.project_snapshot(supervisor.snapshot()), "source_coverage": collector.coverage,
                "model_calls": 0, "automatic_task_retry": False}

    def setting_error(error):
        name = type(error).__name__
        raise HTTPException(409 if "Conflict" in name or "Integrity" in name else 422,
                            "监督设置或原回执暂未确认；请读取当前状态，未自动重试。") from None

    @app.get("/api/supervisor")
    def snapshot():
        return response()

    @app.get("/api/supervisor/changes")
    async def changes(after: int = Query(ge=0, le=9007199254740991),
                      timeout: int = Query(default=25, ge=1, le=25)):
        # Wait for cached facts. This GET never starts a source read/model call.
        sequence = await supervisor.wait_for_change(after, timeout=timeout)
        return {"change_sequence": sequence, "closed": supervisor.snapshot()["observer"]["status"] == "CLOSED"}

    @app.post("/api/supervisor/settings")
    async def settings(body: SupervisorSettingsInput):
        try:
            result = supervisor.update_settings(**body.model_dump())
            await supervisor.start()
            return result
        except Exception as error:
            setting_error(error)

    @app.post("/api/supervisor/alerts/ack")
    def acknowledge(body: SupervisorAlertRef):
        try:
            return supervisor.ack(body.model_dump())
        except Exception as error:
            setting_error(error)

    @app.post("/api/supervisor/check")
    async def check():
        await supervisor.poll_once()
        return response()

    @app.get("/api/supervisor/task-detail")
    def task_detail(kind: str, object_id: str, generation: int = Query(ge=0),
                    project_id: str | None = None, operation_id: str | None = None):
        from packages.agent_sessions.supervisor import TaskKey
        from apps.api.supervised_navigation import navigation
        try:
            key = TaskKey(kind=kind, object_id=object_id, generation=generation,
                          project_id=project_id, operation_id=operation_id).model_dump()
            task = next((item for item in supervisor.snapshot()["tasks"] if item["key"] == key), None)
            if task is None:
                raise HTTPException(409, "这项任务的原引用已变化，请先读取任务动态。")
            detail = {"key": key, "task": task, "input_text": "", "answer_text": "", "artifacts": [],
                      "automatic_retry": False, "execution_authorized": False}
            if kind == "original_session":
                owner = app.state.agent_sessions
                session = owner.get(project_id, object_id)
                operation = owner.operation(project_id, object_id, operation_id)
                if operation.get("request", {}).get("generation") != generation:
                    raise HTTPException(409, "原会话代次与本次任务不同，保留旧引用。")
                detail["session_ref"] = session["session_ref"]
                detail["input_text"] = operation.get("request", {}).get("raw_text", "")
                detail.update(_original_answer(owner, project_id, object_id, session, operation, generation))
                detail['navigation'] = navigation(app, task, session=session, operation=operation)
            elif kind == "native_message":
                source = app.state.native_messages
                record = source.ws._read(source.path(object_id), authority=False)
                if _generation(record.get("generation")) != generation:
                    raise HTTPException(409, "原页面任务的代次已变化。")
                if (digest(record["request"]) != record["request_sha256"] or
                        _native_question_project(app.state.store, record["request"]) != project_id):
                    raise HTTPException(409, "原页面任务的项目依据已变化，请读取当前任务动态；没有重新发送。")
                result = source.get(object_id)
                detail.update(input_text=record["request"]["raw_text"], answer_text=result.get("text", ""),
                              artifacts=result.get("artifacts", []), context_ref=result.get("context_ref"))
                detail['navigation'] = navigation(app, task, native_record=record, native_result=result)
            elif kind in {"discovery", "planning"}:
                table = "discovery_operations" if kind == "discovery" else "planning_proposal_operations"
                object_column = "session_id" if kind == "discovery" else "proposal_id"
                with app.state.store.connect() as connection:
                    row = connection.execute(f"SELECT * FROM {table} WHERE project_id=? AND operation_id=? AND {object_column}=?",
                                             (project_id, operation_id, object_id)).fetchone()
                if row is None:
                    raise HTTPException(409, "原任务结果引用暂不可读。")
                source = json.loads(row["result"]) if row["result"] else {}
                if source and digest(source) != row["result_sha256"]:
                    raise HTTPException(409, "原任务结果校验尚未通过。")
                detail["answer_text"] = source.get("payload", {}).get("candidate", {}).get("summary", "")
                if all(field in source for field in ("id", "revision", "sha256")):
                    detail["object_ref"] = {field: source[field] for field in ("id", "revision", "sha256")}
                detail["text_coverage"] = "EXACT_RECORDED_OPERATION_RESULT"
                detail['navigation'] = navigation(app, task, operation_row=row, object_result=source)
                if detail['navigation']['object_ref'] is not None:
                    detail['object_ref'] = detail['navigation']['object_ref']
                if kind == 'discovery' and isinstance(source.get('session'), dict):
                    detail['answer_text'] = source['session'].get('payload', {}).get('candidate', {}).get('summary', '')
            elif kind == "execution":
                with app.state.store.connect() as connection:
                    row = connection.execute("SELECT * FROM agent_executions WHERE id=? AND project_id=? AND operation_id=?",
                                             (object_id, project_id, operation_id)).fetchone()
                if row is None:
                    raise HTTPException(409, "原执行引用暂不可读。")
                result = json.loads(row["result"]) if row["result"] else {}
                candidate = result.get("candidate_json") if isinstance(result, dict) else None
                if isinstance(candidate, dict):
                    detail["answer_text"] = candidate.get("text") or candidate.get("summary") or ""
                detail["text_coverage"] = "RECORDED_EXECUTION_RESULT_NOT_SEMANTIC_ACCEPTANCE"
            else:
                raise HTTPException(422, "此类任务暂未接入详情。")
            detail["text_truncated"] = False
            for field in ("input_text", "answer_text"):
                if not isinstance(detail[field], str):
                    detail[field] = ""
                if len(detail[field]) > 64000:
                    detail["text_truncated"] = True
                detail[field] = detail[field][:64000]
            return detail
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(409, "原任务详情暂未核对成功，记录保留；没有重发任务。") from None

    return supervisor
