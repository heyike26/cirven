"""Durable exact-session ownership around an unmodified original OpenCode serve.

This controller forwards original API commands and records original observations.
It never chooses a model iteration, invents a todo/subagent, replays an unknown
POST, imports ambient credentials, or grants file writes through native tools.
"""
from __future__ import annotations

import asyncio
import errno
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from packages.adapters.model_transport import private_model_redactions
import socket
import stat
import threading
import time
import uuid

import httpx
import psutil

from packages.adapters.opencode import OpenCodeBridge, decode_event
from packages.adapters.opencode_gateway import OpenCodeNativeGateway
from packages.adapters.opencode_profile import COMMIT, VERSION
from packages.adapters.opencode_startup_guard import reject_unreviewed_project_origins
from packages.adapters.subscription_runtime import (is_subscription, native_provider_config,
    subscription_budget_summary, subscription_environment)
from packages.domain.native_capabilities import NativeCapabilities, _json
from packages.domain.paths import resolve_data_path
from packages.domain.context_read_budget import source_bytes, source_regular
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest, now
from .work_modes import (WORKFLOW_AGENT, WORKFLOW_POLICY_SHA256,
                         native_workflow_definition, observed_workflow_definition, workflow_mode_admitted)

from .models import (InterruptSession, LoadSession, OpenSession, PendingControl, PermissionReply,
                     QuestionReply, SendMessage, SessionControl, operation_request)
from .wire import ProviderWire
from .mcp_names import admitted_server_prefixes, exact_tool_owner, original_mcp_identifier
from .workflow import workflow_scope, prepare_workflow, reconcile_workflow
from .guidance import SessionGuidance
from .capability_scope import current_scope, freeze_scope
from .event_journal import reconcile_tail
from .message_locations import index_message_event

TERMINAL = {"RETURNED", "FAILED", "CANCELLED", "UNKNOWN_OUTCOME"}

# A relay admission callback runs on a ProviderWire HTTP worker while async
# observers use these same durable records. Coordinate the complete bounded
# read and atomic replacement within this process, including static callers.
# This is not an inter-process lock and does not make read-modify-write atomic;
# the existing Store transaction remains the authority for such updates.
_RECORD_IO_LOCK = threading.RLock()


def stored_record_guard():
    """Guard one bounded stored projection read against our atomic writers.

    This neither inspects an owner nor starts a connection. The caller still
    validates its own byte budget, file identity and signed record contents.
    """
    return _RECORD_IO_LOCK


def _native_message_id():
    # Original Identifier.ascending's six-byte time prefix, with independent
    # entropy; its original HTTP schema explicitly accepts supplied message IDs.
    encoded = (int(time.time() * 1000) * 0x1000 + 1) & ((1 << 48) - 1)
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "msg_" + f"{encoded:012x}" + "".join(secrets.choice(alphabet) for _ in range(14))


class AgentSessions:
    def __init__(self, store, data_dir, *, provider_registry, workspaces, capabilities):
        self.store, self.data_dir = store, Path(data_dir).resolve()
        self.providers, self.workspaces, self.capabilities = provider_registry, workspaces, capabilities
        self.active = {}
        self._detached_cleanup = {}
        self.instance_id = uuid.uuid4().hex
        self.closed = False
        self.guidance = SessionGuidance(self)
        from .child_controls import ChildControls
        self.child_controls = ChildControls(self)
        from .editor_changes import EditorChanges
        self.editor_changes = EditorChanges(self)
        from .editor_files import EditorFiles
        self.editor_files = EditorFiles(self)
        from .editor_observers import EditorObservers
        self.editor_observers = EditorObservers(self)
        from .editor_captures import EditorCaptures
        self.editor_captures = EditorCaptures(self)
        self.native_reviews = None
        self.native_messages = None
        self.experience_validation = None
        self.conversations = None
        from .context_authority import ContextAuthority
        self.context_authority=ContextAuthority(self)
        self._identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}

    def _notify_observer(self):
        # A committed owner change is only a dirty hint. The supervisor rereads
        # original records; notification failure cannot change an execution.
        notify = getattr(self, "on_observed_change", None)
        if notify is not None:
            try:
                notify()
            except Exception:
                pass

    def _path(self, pid, ident, *parts):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(pid)) or not re.fullmatch(r"[a-f0-9]{32}", str(ident)):
            raise InvalidInput("Session identity is not a local scoped identity")
        return resolve_data_path("/".join(["agent-sessions", pid, ident, *parts]), self.data_dir)

    @staticmethod
    def _read(path):
        with _RECORD_IO_LOCK:
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise Conflict("Session record is missing or oversized")
            try:
                record = _json(source_bytes(path, 8 * 1024 * 1024))
                if set(record) != {"value", "sha256"} or digest(record["value"]) != record["sha256"]:
                    raise ValueError()
                return record["value"]
            except (ValueError, TypeError, KeyError):
                raise Conflict("Session record integrity failed") from None

    @staticmethod
    def _write(path, value, *, replace=False):
        with _RECORD_IO_LOCK:
            if len(canonical(value).encode()) > 8 * 1024 * 1024:
                raise Conflict("Session observation exceeds its durable record bound")
            NativeCapabilities._write(path, value, replace=replace)

    def _manifest(self, pid, ident, ref=None):
        self.store.project(pid)
        path = self._path(pid, ident, "manifest.json")
        if not path.exists():
            raise NotFound("Conversation does not exist in this project")
        value = self._read(path)
        expected = {"id": ident, "revision": 1, "sha256": digest(value)}
        if value.get("project_id") != pid or value.get("id") != ident or (ref is not None and ref != expected):
            raise Conflict("Conversation reference differs from its immutable scope")
        return value, expected

    def _state(self, pid, ident):
        return self._read(self._path(pid, ident, "state.json"))

    def _update(self, pid, ident, **changes):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            value = self._state(pid, ident)
            relevant = {"active_operation_id", "native_status", "owner_status",
                        "pending_permissions", "pending_questions", "observation_error","context_authority_issue"}
            changed = any(key in relevant and value.get(key) != item for key, item in changes.items())
            capture_changed=any(key in {'active_operation_id','owner_status','generation','pending_permissions'} and value.get(key)!=item for key,item in changes.items())
            value.update(changes, updated_at=now())
            self._write(self._path(pid, ident, "state.json"), value, replace=True)
        if changed:
            self._notify_observer()
        captures=getattr(self,'editor_captures',None)
        if capture_changed and captures is not None:captures.broker.changed()
        return value

    def _event(self, pid, ident, kind, payload):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            value = self._state(pid, ident)
            value, recovered = self._reconcile_event_tail(pid, ident, value)
            if recovered:
                for item in recovered['events']:
                    original = self._read(self._path(pid, ident, 'events', f"{item['seq']:010d}.json"))
                    index_message_event(connection, pid, ident, original)
                    from .resource_retention import observe
                    if observe(self,connection,pid,ident,original)=='OUTPUT_ATTRIBUTION_UNCONFIRMED':value['resource_retention_unconfirmed']=True
            seq = value["last_event_seq"] + 1
            event = {"seq": seq, "type": kind, "payload": payload, "created_at": now(),
                     "original_native_replay": False}
            self._write(self._path(pid, ident, "events", f"{seq:010d}.json"), event)
            message_location_status = index_message_event(connection, pid, ident, event)
            from .resource_retention import observe
            retention_status=observe(self,connection,pid,ident,event)
            if retention_status!='NOT_APPLICABLE':value['resource_retention_status']=retention_status
            if retention_status=='OUTPUT_ATTRIBUTION_UNCONFIRMED':value['resource_retention_unconfirmed']=True
            value.update(last_event_seq=seq, updated_at=now())
            if message_location_status not in {'NOT_APPLICABLE', 'INDEXED', 'UNCHANGED'}:
                value['message_location_status'] = message_location_status
            self._write(self._path(pid, ident, "state.json"), value, replace=True)
        return seq

    def _reconcile_event_tail(self, pid, ident, state):
        return reconcile_tail(self._path(pid, ident), state,
            {"project_id": pid, "session_id": ident, "generation": state["generation"]},
            read=self._read, write=self._write)

    @classmethod
    def repair_event_journal(cls, store, data_dir, pid, ident, *, session_ref,
                             expected_generation, expected_cursor=None):
        """Exact local maintenance; no owner construction, model or native POST.

        Reuses the normal append recovery and only publishes its verified
        observational cursor. Commands and generation/permission state stay put.
        """
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(pid))
                or not re.fullmatch(r"[a-f0-9]{32}", str(ident))
                or type(expected_generation) is not int or expected_generation < 0
                or expected_cursor is not None and (type(expected_cursor) is not int or expected_cursor < 0)):
            raise InvalidInput("Session recovery scope is invalid")
        root = resolve_data_path(f"agent-sessions/{pid}/{ident}", Path(data_dir).resolve())
        store.project(pid)
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            manifest = cls._read(root / "manifest.json")
            if (manifest.get("project_id") != pid or manifest.get("id") != ident
                    or session_ref != {"id": ident, "revision": 1, "sha256": digest(manifest)}):
                raise Conflict("Session recovery reference changed")
            state = cls._read(root / "state.json")
            if (state.get("generation") != expected_generation
                    or expected_cursor is not None and state.get("last_event_seq") != expected_cursor):
                raise Conflict("Session recovery generation or cursor changed")
            updated, prepared = reconcile_tail(root, state,
                {"project_id": pid, "session_id": ident, "generation": expected_generation},
                read=cls._read, write=cls._write)
            if prepared is not None:
                cls._write(root / "state.json", updated, replace=True)
            return {"from_seq": state["last_event_seq"], "to_seq": updated["last_event_seq"],
                    "recovery_ref": updated.get("last_event_cursor_recovery"),
                    "cursor_published": True, "native_replay": False}

    def events(self, pid, ident, after=0, limit=100):
        self._manifest(pid, ident)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidInput("Event cursor/limit invalid")
        state = self._state(pid, ident)
        return {"events": [self._read(self._path(pid, ident, "events", f"{seq:010d}.json"))
                           for seq in range(after + 1, min(state["last_event_seq"], after + limit) + 1)],
                "last_event_seq": state["last_event_seq"], "native_replay": False}

    def configuration(self):
        # This is the complete metadata the user selects from, not permission
        # to start or inspect every configured MCP executable.
        value = self.capabilities.native_binding_snapshot(validate_mcp_ids=set())
        return {"capability_binding_sha256": value["sha256"],
                "configuration_refs": value["value"]["configuration_refs"],
                "permission_granted": False, "loaded_in_original_session": False}

    async def tool_catalog(self, scope):
        from .tool_catalog import owner_tool_catalog
        return await owner_tool_catalog(self, scope)

    def internal_project(self):
        description = "eeas.original-agent-session.internal-project.v1"
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM projects WHERE description=? ORDER BY created_at LIMIT 1", (description,)).fetchone()
            if row:
                return row["id"]
            ident = str(uuid.uuid4())
            connection.execute("INSERT INTO projects VALUES (?,?,?,?)", (ident, "EEAS conversation", description, now()))
            return ident

    def list(self, pid):
        self.store.project(pid)
        root = resolve_data_path("agent-sessions/" + pid, self.data_dir)
        views=[self.get(pid,path.name) for path in sorted(root.iterdir()) if re.fullmatch(r"[a-f0-9]{32}",path.name) and (path/"manifest.json").is_file()] if root.exists() else []
        return [view for view in views if view["execution_purpose"]!="EXPERIENCE_VALIDATION"]

    def _diff_observation(self, pid, ident, *, session_sha256, generation, workspace_ref):
        """No owner start/inspect, provider lookup, native request or file read.

        Historical facts remain readable after disconnect/restart. The request
        must still match the exact saved owner generation and registered root.
        A future full-file history provider must supply immutable authorized
        snapshots; neither current files nor editor buffers are substitutes.
        """
        from .models import WorkspaceRef
        from .diffs import project_diffs
        manifest, reference = self._manifest(pid, ident)
        expected = WorkspaceRef.model_validate(workspace_ref).model_dump()
        state = self._state(pid, ident)
        if (reference['sha256'] != session_sha256 or type(generation) is not int or
                generation != state['generation'] or generation < 0):
            raise Conflict('Original diff session reference or generation changed')
        if manifest['request']['workspace_ref'] != expected:
            raise Conflict('Historical diff workspace differs from the original owner')
        binding = self.workspaces.get(pid, expected['id'], expected['revision'])
        if (binding['binding_sha256'] != expected['binding_sha256'] or
                binding['root_identity'] != manifest['directory_identity']):
            raise Conflict('Historical diff registered workspace changed')
        observed = {**state, 'project_id': pid, 'session_ref': reference, 'workspace_ref': expected}
        from packages.adapters.opencode_change_review import read_history, historical_admissions
        full, reasons = [], []
        if 'WORKSPACE_WRITE' in manifest['request'].get('allowed_effects', []):
            try:
                for admitted in historical_admissions(self._path(pid,ident,'runtime'), observed):
                    source_generation = admitted['scope']['generation']
                    source = {**observed,'generation':source_generation}
                    entries, why = read_history(admitted, source, data_dir=self.data_dir)
                    for entry in entries:
                        entry.update(source_generation=source_generation,
                            source_scope={k:source[k] for k in ['project_id','session_ref','generation','workspace_ref']})
                    full.extend(entries[:128-len(full)]); reasons.extend(why)
                    if len(full) >= 128:
                        reasons.append('FULL_FILE_COUNT_LIMIT'); break
            except (Conflict, InvalidInput, OSError, TypeError, KeyError, ValueError):
                reasons.append('FULL_FILE_HISTORY_INDEX_UNAVAILABLE')
        if not full:
            reasons.append('NO_ADMITTED_FULL_FILE_HISTORY')
        result = project_diffs(observed, binding, path_checker=self.workspaces._private,
                              full_entries=full, full_reason_codes=reasons)
        latest = self._state(pid, ident)
        if latest['generation'] != generation or latest.get('original_session_id') != state.get('original_session_id'):
            raise Conflict('Original diff owner generation changed while reading')
        return result

    def diffs(self, pid, ident, *, session_sha256, generation, workspace_ref):
        return self._diff_observation(pid, ident, session_sha256=session_sha256,
            generation=generation, workspace_ref=workspace_ref)[0]

    def command_test_report(self,pid,ident,body):
        from .test_reports import command_test_report
        return command_test_report(self,pid,ident,body)

    def command_observations(self, pid, ident, *, session_sha256, generation, workspace_ref, call_id=None, _command=None):
        """Read saved original command facts without starting or inspecting an owner."""
        from .models import WorkspaceRef
        from .command_observations import project_command_observations, required_parent_ids
        from packages.adapters.opencode_command_observation import historical_admissions, read_history
        from packages.adapters.opencode_change_review import _regular
        if call_id is not None:
            if (not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}', call_id)
                    or _command is not None):
                raise InvalidInput('An exact original command call is required')
            _command = {'generation': None, 'call_id': call_id}
        manifest, reference = self._manifest(pid, ident)
        expected = WorkspaceRef.model_validate(workspace_ref).model_dump()
        state = self._state(pid, ident)
        if (reference['sha256'] != session_sha256 or type(generation) is not int or
                generation != state['generation'] or generation < 0):
            raise Conflict('Original command session reference or generation changed')
        if manifest['request']['workspace_ref'] != expected:
            raise Conflict('Command history workspace differs from the original owner')
        binding = self.workspaces.get(pid, expected['id'], expected['revision'])
        if (binding['binding_sha256'] != expected['binding_sha256'] or
                binding['root_identity'] != manifest['directory_identity']):
            raise Conflict('Command history registered workspace changed')
        observed = {**state, 'project_id': pid, 'session_ref': reference, 'workspace_ref': expected}
        entries, reasons = [], []
        if state.get('command_observation_reasons') == ['COMMAND_GENERATION_CAPACITY_REACHED']:
            reasons.append('COMMAND_GENERATION_CAPACITY_REACHED')
        if 'BACKGROUND_COMMAND' in manifest['request'].get('allowed_effects', []):
            try:
                for admission in historical_admissions(self._path(pid, ident, 'runtime'), observed):
                    if (_command is not None and _command['generation'] is not None
                            and admission['scope']['generation'] != _command['generation']):
                        continue
                    source = {**observed, 'generation': admission['scope']['generation']}
                    selection = {'selected_call_id': _command['call_id']} if _command is not None else {}
                    rows, why = read_history(admission, source, data_dir=self.data_dir,**selection)
                    entries.extend(rows)
                    reasons.extend(why)
                    if len(entries) > 128:
                        entries = entries[-128:]
                        reasons.append('COMMAND_HISTORY_WINDOW_LIMIT')
            except (Conflict, InvalidInput, OSError, TypeError, KeyError, ValueError):
                entries = []
                reasons.append('COMMAND_HISTORY_INDEX_UNAVAILABLE')
        else:
            reasons.append('COMMAND_OBSERVATION_NOT_ADMITTED')
        parents = set(required_parent_ids(entries))
        operations, used, scanned = [], 0, 0
        directory = self._path(pid, ident, 'operations')
        if parents:
            try:
                if directory.is_symlink() or not directory.is_dir():
                    raise Conflict('Command operation index is unavailable')
                with os.scandir(directory) as files:
                    for row in files:
                        scanned += 1
                        if scanned > 1024:
                            raise Conflict('Command operation index exceeds its scan bound')
                        if not re.fullmatch(r'[a-f0-9]{64}\.json', row.name):
                            continue
                        info = Path(row.path).lstat()
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise Conflict('Command operation metadata is not an independent regular file')
                        remaining = 8 * 1024 * 1024 - used
                        if info.st_size > remaining:
                            raise Conflict('Command operation metadata exceeds its byte bound')
                        wrapper_raw = source_regular(_regular, Path(row.path), remaining)
                        used += len(wrapper_raw)
                        wrapper = _json(wrapper_raw)
                        if (not isinstance(wrapper, dict) or set(wrapper) != {'value', 'sha256'} or
                                not isinstance(wrapper['value'], dict) or
                                digest(wrapper['value']) != wrapper['sha256']):
                            raise Conflict('Command operation metadata integrity failed')
                        operation = wrapper['value']
                        if operation.get('native_message_id') in parents:
                            if row.name != digest(operation.get('id')) + '.json':
                                raise Conflict('Command operation identity differs from its stored key')
                            operations.append(operation)
                            if len(operations) > 128:
                                raise Conflict('Command operation coverage exceeds its count bound')
            except (Conflict, OSError, TypeError, ValueError):
                operations = []
                reasons.append('COMMAND_OPERATION_INDEX_UNAVAILABLE')
        from .work_retests import resolved_command_roots
        trusted_work_roots = resolved_command_roots(self,pid,ident,operations)
        result = project_command_observations(observed, entries, operations, reason_codes=reasons,
            trusted_work_roots=trusted_work_roots)
        latest = self._state(pid, ident)
        if latest['generation'] != generation or latest.get('original_session_id') != state.get('original_session_id'):
            raise Conflict('Original command owner generation changed while reading')
        return result

    def diff_file(self, pid, ident, *, session_sha256, generation, workspace_ref, snapshot_ref, diff_ref):
        from .diffs import file_detail
        directory, content = self._diff_observation(pid, ident, session_sha256=session_sha256,
            generation=generation, workspace_ref=workspace_ref)
        return file_detail(directory, content, snapshot_ref, diff_ref)

    @staticmethod
    def _alive(identity):
        if not identity:
            return False
        try:
            process = psutil.Process(identity["pid"])
            return process.is_running() and abs(process.create_time() - identity["created"]) < .01
        except (psutil.Error, KeyError, TypeError):
            return False

    def open_operation(self, operation_id, pid=None):
        """Observe a public open receipt without a new project, bind or runtime."""
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise NotFound('Unknown original conversation open operation')
        if pid is None:
            with self.store.connect() as connection:
                row = connection.execute('SELECT id FROM projects WHERE description=? ORDER BY created_at LIMIT 1',
                    ('eeas.original-agent-session.internal-project.v1',)).fetchone()
            if row is None:
                raise NotFound('No original conversation open was committed')
            pid = row['id']
        operation = resolve_data_path('agent-sessions/open-operations/' + digest([pid, operation_id]) + '.json', self.data_dir)
        if not operation.exists():
            raise NotFound('No original conversation open was committed')
        old = self._read(operation)
        if not isinstance(old, dict) or not isinstance(old.get('id'), str):
            raise Conflict('Original conversation open receipt integrity changed')
        manifest, _ = self._manifest(pid, old['id'])
        if manifest.get('native_message_admission') or manifest.get('native_review_admission'):
            raise Conflict('This receipt belongs to a separately scoped native operation')
        expected_open=(digest({'request':manifest['request'],'experience_validation_admission':manifest['experience_validation_admission']})
            if manifest.get('experience_validation_admission') else digest(manifest['request']))
        if old.get('request_sha256') != expected_open:
            raise Conflict('Original conversation open receipt differs from its manifest')
        bound = True
        if manifest['request'].get('conversation_ref') is not None:
            try:
                if self.conversations is None:
                    raise Conflict('Conversation registration is unavailable')
                self.conversations.assert_bound(manifest)
            except (Conflict, NotFound):
                bound = False
        return {'version': 1, 'operation_id': operation_id, 'request_sha256': old['request_sha256'],
            'project_id': pid, 'conversation_ref': manifest['request'].get('conversation_ref'),
            'conversation_bound': bound, 'session': self.get(pid, old['id']),
            'owner_started_by_query': False, 'model_calls_by_query': 0,
            'source_changed_by_query': False, 'automatic_post_replay': False}

    def get(self, pid, ident):
        manifest, ref = self._manifest(pid, ident)
        state = self._state(pid, ident)
        context = self.active.get(ident)
        connected = bool(context and not context.get("closing") and context["process"].returncode is None)
        if not connected and state["owner_status"] in {"READY", "STARTING"}:
            state = {**state, "owner_status": "DISCONNECTED", "native_status": "UNKNOWN"}
        elif connected and state.get("observation_error"):
            state = {**state, "owner_status": "OBSERVATION_UNAVAILABLE"}
        return {**state, "project_id": pid, "session_ref": ref, "workspace_ref": manifest["request"]["workspace_ref"],
                "execution_purpose": "EXPERIENCE_VALIDATION" if manifest.get("experience_validation_admission") else "USER_WORK",
                "available_agent_modes": ["plan", "build", *([WORKFLOW_AGENT] if connected and workflow_mode_admitted(context) else [])],
                "workflow_mode_supported": not bool(manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')),
                "workspace_kind": manifest["workspace_kind"], "provider_profile_ref": manifest["request"]["provider_profile_ref"],
                "workflow_ref": manifest["request"].get("workflow_ref"), "workflow_plugin": state.get("workflow_plugin"),
                "guidance": self.guidance.public_snapshot(pid, ident),
                "connected_owner": connected, "long_lived_conversation": True,
                "native_mutations_allowed": False,
                "file_writes": "EXPLICIT_WORKSPACE_WRITE_ASK_WITH_CHANGE_REVIEW" if 'WORKSPACE_WRITE' in manifest['request'].get('allowed_effects', []) else "NOT_ADAPTED_USE_DIRECT_ACTIONS_CAS",
                "configuration_current": self._configuration_current(manifest),
                "observation_coverage": "LAST_200_ORIGINAL_ROOT_MESSAGES_ROOT_TODO_CHILD_REFERENCES_AND_PENDING_REQUESTS",
                "native_messages_error": state.get("native_messages_error"),
                "observation_is_current": connected and not state.get("observation_error"),
                "qualification": {"original_cli_version": VERSION, "source_commit": COMMIT,
                    "model_semantics": "NOT_VALIDATED", "company_model": "NOT_VALIDATED", "windows": "NOT_VALIDATED",
                    "os_sandbox": "NOT_QUALIFIED", "commercial_cost": "UNKNOWN", "silent_paid_fallback": False},
                "private_server_url_exposed": False, "automatic_post_replay": False}

    def _configuration_current(self, manifest, *, check_capabilities=True):
        try:
            request = manifest["request"]
            ref = request["provider_profile_ref"]
            config = self.providers.configuration_resolver(ref["id"], ref["revision"])
            if digest(config) != ref["sha256"]:
                return False
            if self.providers.current_fingerprint(ref) != manifest["provider_fingerprint"]:
                return False
            for extra in request.get("workflow_provider_refs", []):
                if self.providers.current_fingerprint(extra) != manifest.get("workflow_provider_fingerprints", {}).get(extra["id"]):
                    return False
            if check_capabilities:
                if manifest.get('experience_validation_admission'):
                    if self.experience_validation is None:return False
                    self.experience_validation.binding(manifest)
                elif manifest.get('native_review_admission') and self.native_reviews.capability_task_admission(manifest['native_review_admission']):
                    self.native_reviews.binding(manifest, None, None)
                elif 'capability_scope' in manifest:
                    current_scope(self.capabilities, manifest)
                elif self.capabilities._index() != manifest["capability_configuration_refs"]:
                    return False
            workspace = request["workspace_ref"]
            if workspace is not None:
                actual = self.workspaces.get(manifest["project_id"], workspace["id"], workspace["revision"])
                if actual["binding_sha256"] != workspace["binding_sha256"] or actual["root_identity"] != manifest["directory_identity"]:
                    return False
            else:
                _, identity = self.workspaces._root(str(self._path(manifest["project_id"], manifest["id"], "runtime-project")))
                if identity != manifest["directory_identity"]:
                    return False
            if (check_capabilities and not manifest.get('experience_validation_admission') and 'capability_scope' not in manifest
                    and not (manifest.get('native_review_admission') and self.native_reviews.capability_task_admission(manifest['native_review_admission']))
                    and self.capabilities.native_binding_snapshot()["sha256"] != request["capability_binding_sha256"]):
                return False
            return True
        except (Conflict, InvalidInput, NotFound, OSError):
            return False

    def _check_scope(self, manifest):
        request = manifest["request"]
        profile = self.providers.resolve(request["provider_profile_ref"])
        if profile.fingerprint != manifest["provider_fingerprint"]:
            raise Conflict("Selected provider credential/configuration changed")
        if manifest.get('experience_validation_admission'):
            if self.experience_validation is None:raise Conflict('Original Skill validation coordinator is unavailable')
            binding = self.experience_validation.binding(manifest)
        elif manifest.get('native_review_admission') and self.native_reviews.capability_task_admission(manifest['native_review_admission']):
            binding = self.native_reviews.binding(manifest, None, None)
        elif 'capability_scope' in manifest:
            binding = current_scope(self.capabilities, manifest)
        else:
            # Existing owners keep their original complete binding semantics;
            # loading an old manifest must never silently migrate its scope.
            binding = self.capabilities.native_binding_snapshot()
            if binding["sha256"] != request["capability_binding_sha256"]:
                raise Conflict("Capability configuration changed; start a newly scoped conversation")
        # A persisted original session must also satisfy the current owner
        # registration boundary; loading does not bypass namespace admission.
        admitted_server_prefixes(binding["value"]["native_config_fragment"]["mcp"])
        if request["workspace_ref"] is not None:
            ref = request["workspace_ref"]
            selected = self.workspaces.get(manifest["project_id"], ref["id"], ref["revision"])
            if selected["binding_sha256"] != ref["binding_sha256"] or selected["root_identity"] != manifest["directory_identity"]:
                raise Conflict("Selected actual workspace changed")
            directory = selected["root"]
            if 'WORKSPACE_WRITE' in request.get('allowed_effects', []):
                from packages.adapters.opencode_change_review import write_patterns
                write_patterns(selected)
        else:
            directory = str(self._path(manifest["project_id"], manifest["id"], "runtime-project"))
            _root, identity = self.workspaces._root(directory)
            if identity != manifest["directory_identity"]:
                raise Conflict("Internal conversation directory changed")
        self._preflight_origins(directory)
        if manifest.get('native_review_admission'):
            if self.native_reviews is None:
                raise Conflict('Native review ownership is unavailable')
            binding = self.native_reviews.binding(manifest, binding, directory)
        if manifest.get('native_message_admission'):
            if self.native_messages is None:
                raise Conflict('Native ordinary ownership is unavailable')
            binding = self.native_messages.binding(manifest, binding, directory)
        return profile, binding, directory

    @staticmethod
    def _preflight_origins(directory):
        """Reject actual unreviewed V2 ancestry before original initialization.

        Tool denial cannot stop imported plugin top-level code. This is an
        observed-file admission check, not an OS filesystem/network sandbox.
        Git metadata is also rejected to avoid implicit original Git discovery
        in this no-Git delivery slice.
        """
        reject_unreviewed_project_origins(directory)

    @staticmethod
    def _preflight_private(home, config_path, expected_sha=None):
        root = config_path.parent
        for candidate in [home / ".opencode", home / ".claude", home / ".agents", *[
            root / name for name in ("config.json", "config", "opencode.jsonc", "plugin", "plugins", "tool", "tools",
                                   "skill", "skills", "agent", "agents", "command", "commands", "mode", "modes")]]:
            if candidate.exists() or candidate.is_symlink():
                raise Conflict("UNREVIEWED_PRIVATE_ORIGINAL_CONFIG_SOURCE")
        if config_path.is_symlink():
            raise Conflict("Private original config cannot be a symlink")
        if config_path.exists() and expected_sha is not None and hashlib.sha256(config_path.read_bytes()).hexdigest() != expected_sha:
            raise Conflict("Private original configuration bytes changed; no silent overwrite or adoption")

    def _admit_open(self, request, binding):
        if 'WORKSPACE_WRITE' in request.allowed_effects and request.workspace_ref is None:
            raise Conflict('WORKSPACE_WRITE requires a selected registered workspace')
        if request.command_allowlist and (request.workspace_ref is None or "BACKGROUND_COMMAND" not in request.allowed_effects):
            raise Conflict("Exact commands require a selected workspace and explicit BACKGROUND_COMMAND admission")
        if request.command_observation_paths and (request.workspace_ref is None or not request.command_allowlist
                or 'BACKGROUND_COMMAND' not in request.allowed_effects):
            raise Conflict('Observed files require the original admitted commands and registered workspace')
        servers = binding["value"]["native_config_fragment"]["mcp"]
        prefixes = admitted_server_prefixes(servers)
        if any(c["type"] == "local" for c in servers.values()) and "PROCESS_START" not in request.allowed_effects:
            raise Conflict("Enabled local MCP requires explicit PROCESS_START admission")
        if any(c["type"] == "remote" for c in servers.values()) and "REMOTE_MCP_CONNECT" not in request.allowed_effects:
            raise Conflict("Enabled remote MCP requires explicit REMOTE_MCP_CONNECT admission")
        if request.network_mode == "STRICT_OFFLINE" and servers:
            raise Conflict("A selected MCP process/endpoint has no qualified offline network sandbox")
        for name in request.mcp_tool_allowlist:
            exact_tool_owner(name, prefixes)
        self.capabilities.validate_tool_selection(
            None if request.mcp_tool_selection is None else [item.model_dump() for item in request.mcp_tool_selection],
            request.mcp_tool_allowlist)

    def _admit_observation_paths(self, request, execution_project_id):
        """Reject controller-private paths before replacing any current owner."""
        if not request.command_observation_paths:
            return
        if request.workspace_ref is None or execution_project_id is None:
            raise Conflict('Observed paths require the confirmed workspace execution project')
        ref = request.workspace_ref
        selected = self.workspaces.get(execution_project_id, ref.id, ref.revision)
        if selected['binding_sha256'] != ref.binding_sha256:
            raise Conflict('Observed workspace binding changed')
        for path in request.command_observation_paths:
            self.workspaces._private(selected, path)

    def _ordinary_deadline(self, manifest):
        if self.native_messages is None:
            raise Conflict('Native ordinary admission is unavailable')
        deadline = datetime.fromisoformat(self.native_messages.deadline(manifest))
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise Conflict('Ordinary admission requires an absolute aware deadline')
        if deadline > datetime.now(timezone.utc) + timedelta(seconds=180):
            raise Conflict('Ordinary admission cannot exceed its absolute180-second limit')
        return deadline

    async def open(self, pid, body, *, native_review_admission=None, native_message_admission=None, canonical_execution_claim=None, experience_validation_admission=None):
        body = OpenSession.model_validate(body)
        self.store.project(pid)
        ordinary = native_message_admission is not None
        experience = experience_validation_admission is not None
        if experience and (self.experience_validation is None or native_review_admission is not None or ordinary
                or canonical_execution_claim is not None or body.conversation_ref is not None):
            raise Conflict('Private Skill validation cannot share another execution admission')
        if canonical_execution_claim is not None and (body.conversation_ref is None or native_review_admission is not None or ordinary):
            raise Conflict('An explicit execution configuration claim is public-conversation only')
        if body.conversation_ref is not None and (ordinary or native_review_admission is not None or self.conversations is None):
            raise Conflict('This public conversation binding is unavailable for the selected owner')
        if ordinary and (native_review_admission is not None or body.workflow_ref is not None or
                body.workflow_provider_refs or 'WORKSPACE_WRITE' in body.allowed_effects):
            raise Conflict('Ordinary admission cannot share review, workflow or write authority')
        if ordinary and (body.ttl_seconds > 180 or body.max_calls > 8 or body.max_output_tokens > 4096 or
                body.command_allowlist or body.mcp_tool_allowlist or body.mcp_tool_selection):
            raise Conflict('Ordinary native owner exceeds its fixed scope or shared budget')
        request = body.model_dump(exclude={"operation_id"})
        if body.conversation_ref is None:
            request.pop('conversation_ref', None)
        request_sha = (digest({'request': request, 'experience_validation_admission': experience_validation_admission}) if experience else
            digest({'request': request, 'native_message_admission': native_message_admission}) if ordinary else
            digest({'request': request, 'native_review_admission': native_review_admission}) if native_review_admission else digest(request))
        operation = resolve_data_path("agent-sessions/open-operations/" + digest([pid, body.operation_id]) + ".json", self.data_dir)
        if operation.exists():
            old = self._read(operation)
            if old["request_sha256"] != request_sha:
                raise Conflict("IDEMPOTENCY_CONFLICT: conversation operation binds another scope")
            if body.conversation_ref is not None:
                self.conversations.bind_open(self._manifest(pid, old['id'])[0])
            return self.get(pid, old["id"])
        if body.conversation_ref is not None:
            if canonical_execution_claim is not None:
                self.conversations.validate_open(pid,body.conversation_ref.model_dump(),canonical_execution_claim=canonical_execution_claim,request=body.model_dump())
            else:
                self.conversations.validate_open(pid, body.conversation_ref.model_dump())
        profile = await asyncio.to_thread(self.providers.resolve, request["provider_profile_ref"])
        workflow = await asyncio.to_thread(workflow_scope, self, pid, request, profile, initial=True)
        private_admission = native_review_admission is not None or ordinary or experience
        capability_task_v2 = bool(native_review_admission and self.native_reviews and self.native_reviews.capability_task_admission(native_review_admission))
        binding = (self.experience_validation.binding({'project_id':pid,'request':request,
                    'experience_validation_admission':experience_validation_admission}) if experience else
            self.capabilities.native_binding_snapshot() if private_admission and not capability_task_v2 else
            self.capabilities.native_binding_snapshot(validate_mcp_ids=set()))
        if binding["sha256"] != body.capability_binding_sha256:
            raise Conflict("Selected exact M4 configuration changed")
        capability_scope = None
        if experience:
            self._admit_open(body,binding)
        elif ordinary:
            if self.native_messages is None:
                raise Conflict('Native ordinary admission is unavailable')
            self.native_messages.validate_admission(native_message_admission, request)
            admitted = {**binding, 'value': {**binding['value'], 'native_config_fragment': {'skills': {'paths': []}, 'mcp': {}}}}
            self._admit_open(body, admitted)
        elif native_review_admission:
            if self.native_reviews is None:
                raise Conflict('Native review admission is unavailable')
            admission = self.native_reviews.validate_admission(native_review_admission, request)
            selected = admission['selection']
            if capability_task_v2:
                from .capability_task import current_selection
                binding = current_selection(self.capabilities, selected, admission['capability_scope'])
                self._admit_open(body, binding)
            else:
                admitted = {**binding, 'value': {**binding['value'], 'native_config_fragment': {
                    'skills': {'paths': []}, 'mcp': {}}}}
                if selected['tool_selection']:
                    from packages.adapters.opencode_native_review import SERVER_ID, TOOL_NAME
                    if request['mcp_tool_allowlist'] != [TOOL_NAME] or 'PROCESS_START' not in request['allowed_effects']:
                        raise Conflict('Native review tool admission differs')
                    admitted['value']['native_config_fragment']['mcp'] = {SERVER_ID: {'type': 'local'}}
                self._admit_open(body, admitted)
        else:
            binding, capability_scope = freeze_scope(self.capabilities, request, binding)
            self._admit_open(body, binding)
        self._admit_observation_paths(body, pid)
        if body.network_mode == "STRICT_OFFLINE":
            from urllib.parse import urlsplit
            if urlsplit(profile.provider_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise Conflict("Strict offline owner requires an explicit local model endpoint")
        ident = uuid.uuid4().hex
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if operation.exists():
                old = self._read(operation)
                if old["request_sha256"] != request_sha:
                    raise Conflict("IDEMPOTENCY_CONFLICT: conversation operation binds another scope")
                if body.conversation_ref is not None:
                    self.conversations.assert_bound(self._manifest(pid, old['id'])[0])
                return self.get(pid, old["id"])
            if body.workspace_ref is None:
                directory = self._path(pid, ident, "runtime-project")
                directory.mkdir(parents=True)
                _, identity = self.workspaces._root(str(directory))
            else:
                selected = self.workspaces.get(pid, body.workspace_ref.id, body.workspace_ref.revision)
                if selected["binding_sha256"] != body.workspace_ref.binding_sha256:
                    raise Conflict("Exact selected workspace differs")
                if 'WORKSPACE_WRITE' in body.allowed_effects:
                    from packages.adapters.opencode_change_review import write_patterns
                    write_patterns(selected)
                directory, identity = Path(selected["root"]), selected["root_identity"]
            manifest = {"schema": "eeas.agent-session.v1", "id": ident, "project_id": pid, "created_at": now(),
                        "request": request, "provider_fingerprint": profile.fingerprint,
                        "workflow_provider_fingerprints": {key: value.fingerprint for key, value in workflow['profiles'].items()} if workflow else {},
                        "provider_auth_mode": getattr(profile, "auth_mode", "API_KEY"),
                        "capability_configuration_refs": binding["value"]["configuration_refs"],
                        "directory_identity": identity, "workspace_kind": "LOCAL_WORKSPACE" if body.workspace_ref else "APP_INTERNAL_READONLY"}
            if canonical_execution_claim is not None:
                manifest['canonical_execution_claim']=dict(canonical_execution_claim)
            if experience:
                manifest['experience_validation_admission'] = experience_validation_admission
            if native_review_admission:
                manifest['native_review_admission'] = native_review_admission
            if ordinary:
                manifest['native_message_admission'] = native_message_admission
            if capability_scope is not None:
                manifest['capability_scope'] = capability_scope
            self._write(self._path(pid, ident, "manifest.json"), manifest)
            self._write(self._path(pid, ident, "state.json"), {
                "owner_status": "DISCONNECTED", "native_status": "UNKNOWN", "original_session_id": None,
                "generation": 0, "last_event_seq": 0, "model_calls_reserved": None if is_subscription(profile) else 0,
                "native_turns_started": 0,
                "provider_auth_mode": getattr(profile, "auth_mode", "API_KEY"),
                **({"budget_observation": subscription_budget_summary({**request,
                    "timeout_seconds": request["ttl_seconds"], "native_steps": min(16, request["max_calls"])})}
                   if is_subscription(profile) else {}), "active_operation_id": None,
                "messages": [], "todo": [], "children": [], "pending_questions": [], "pending_permissions": [],
                "capability_load": None, "updated_at": now(), "controller_identity": None, "process_identity": None})
            self._write(operation, {"request_sha256": request_sha, "id": ident})
        if native_review_admission:
            self.native_reviews.owner_created(manifest, self._manifest(pid, ident)[1])
        if ordinary:
            self.native_messages.owner_created(manifest, self._manifest(pid, ident)[1])
            remaining = (self._ordinary_deadline(manifest) - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                raise Conflict('Ordinary admission absolute deadline expired before owner startup')
            return await asyncio.wait_for(self._start(pid, ident), timeout=remaining)
        if body.conversation_ref is not None:
            self.conversations.bind_open(manifest)
        return await self._start(pid, ident)

    def _permissions(self, request, workspace=None):
        value = {"*": "deny", "question": "allow", "skill": "allow", "todowrite": "allow",
                 "read": "ask", "glob": "ask", "grep": "ask", "list": "ask",
                 "task": {"*": "deny", "explore": "ask"}, "external_directory": "deny",
                 "bash": {"*": "deny", **{command: "ask" for command in request["command_allowlist"]}}}
        for name in request["mcp_tool_allowlist"]:
            value[name] = "ask"
        if 'WORKSPACE_WRITE' in request.get('allowed_effects', []):
            from packages.adapters.opencode_change_review import write_patterns
            if workspace is None:
                raise Conflict('WORKSPACE_WRITE has no exact registered workspace grant')
            value['edit'] = write_patterns(workspace)
        return value

    def _config(self, manifest, profile, binding, wire):
        request = manifest["request"]
        workspace = self.workspaces.get(manifest['project_id'], request['workspace_ref']['id'], request['workspace_ref']['revision']) if 'WORKSPACE_WRITE' in request.get('allowed_effects', []) else None
        policy = self._permissions(request, workspace)
        if manifest.get('native_review_admission'):
            policy = self.native_reviews.permissions(manifest)
        if manifest.get('native_message_admission'):
            policy = self.native_messages.permissions(manifest)
        if manifest.get('experience_validation_admission'):
            policy = {'*':'deny','skill':{'*':'deny',**{name:'allow' for name in binding['value']['configuration_refs']['skills']}}}
        fragment = binding["value"]["native_config_fragment"]
        mcp = {name: {**config, **({"oauth": False} if config["type"] == "remote" else {})}
               for name, config in fragment["mcp"].items()}
        model = profile.provider_id + "/" + profile.model_id
        config = {"autoupdate": False, "share": "disabled", "snapshot": False, "formatter": False,
                "lsp": False, "plugin": [], "instructions": [], "skills": fragment["skills"], "mcp": mcp,
                "permission": policy, "enabled_providers": [profile.provider_id], "model": model, "small_model": model,
                "provider": native_provider_config(profile, request["max_output_tokens"])["provider"] if is_subscription(profile) else {
                    profile.provider_id: {"npm": "@ai-sdk/openai-compatible", "name": "EEAS explicit session provider",
                    "options": {"baseURL": wire.base_url, "apiKey": wire.key, "timeout": 60000},
                    "models": {profile.model_id: {"tool_call": True, "reasoning": False, "attachment": False,
                        "limit": {"context": 131072, "output": request["max_output_tokens"]},
                        "modalities": {"input": ["text"], "output": ["text"]}}}}},
                "agent": {name: {"model": model, "steps": 16,
                    "permission": {**policy, 'edit': 'deny'} if workspace is not None and name != 'build' else policy}
                    for name in ("build", "plan", "explore")}}
        if not (manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')):
            config['agent'][WORKFLOW_AGENT] = native_workflow_definition(model, policy, 16)
        if is_subscription(profile):
            config.update(native_provider_config(profile, request["max_output_tokens"]))
            for agent in config["agent"].values():
                agent["steps"] = min(16, request["max_calls"])
        if not is_subscription(profile) and profile.supports_images:
            configured = config['provider'][profile.provider_id]['models'][profile.model_id]
            configured.update(attachment=True, modalities={'input': ['text', 'image'], 'output': ['text']})
        if manifest.get('native_review_admission'):
            instructions = ('Review only the frozen page returned by eeas_review_context. Call that tool first to learn '
                'the immutable review_ref, selected Skill and exact source-plan basis. Load only the selected Skill. '
                'Use eeas_review_visual only when visual evidence helps. The read-only acceptance MCP takes '
                'review_ref only; never invent or copy its candidate. Separate visible-page evidence, historical '
                'source-plan structure, suggestions, missing information and actual execution. Never write user files.')
            if self.native_reviews.capability_task_admission(manifest['native_review_admission']):
                from .capability_task import INSTRUCTIONS
                instructions = INSTRUCTIONS
            for agent in config['agent'].values():
                agent['prompt'] = instructions
        if manifest.get('native_message_admission'):
            for agent in config['agent'].values():
                agent['prompt'] = self.native_messages.instructions(manifest)
                agent['steps'] = min(8, request['max_calls'])
        if manifest.get('experience_validation_admission'):
            for agent in config['agent'].values():
                agent['prompt']=self.experience_validation.instructions()
                agent['steps']=min(16,request['max_calls'])
        efforts = getattr(profile, 'reasoning_efforts', ())
        if efforts and not is_subscription(profile):
            configured = config['provider'][profile.provider_id]['models'][profile.model_id]
            configured.update(reasoning=True, variants={value: {'reasoningEffort': value} for value in efforts})
        return config

    from .task_resources import model_scope as _model_resource_scope
    @_model_resource_scope
    def _reserve_rpc(self, pid, ident, request_sha, *, expected_context=None,input_bytes=0):
        manifest, _ = self._manifest(pid, ident)
        if manifest.get("provider_auth_mode") == "OAUTH_SUBSCRIPTION":
            raise Conflict("Native subscription inference does not use the API relay counter")
        if not self._configuration_current(manifest):
            raise Conflict("Selected original session configuration changed before model forwarding")
        from .editor_relay_capture import refresh_relay_capture
        capture_basis=refresh_relay_capture(self,pid,ident,manifest,request_sha,expected_context)
        authority=getattr(self,'context_authority',None)
        if authority is not None:
            current=self.active.get(ident)
            if current is not None:authority.check(manifest,current)
        if capture_basis is not None and not self._configuration_current(manifest):
            raise Conflict('Selected original session configuration changed during editor capture')
        if manifest.get('experience_validation_admission'):
            self.experience_validation.reserve_model(manifest,request_sha)
        elif manifest.get('native_review_admission') and self.native_reviews.capability_task_admission(manifest['native_review_admission']):
            self.native_reviews.reserve_model(manifest, request_sha)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._configuration_current(manifest):
                raise Conflict('EXPERIENCE_SOURCE_CHANGED: current source/configuration changed before RPC reservation')
            value = self._state(pid, ident)
            self._editor_assert(manifest,connection=connection)
            context = self.active.get(ident)
            if (expected_context is not None and (context is not expected_context or value.get('generation')!=expected_context['generation'])
                    or capture_basis is not None and (context is not capture_basis['context'] or value.get('generation')!=capture_basis['generation'] or value.get('active_operation_id')!=capture_basis['operation_id'])):
                raise Conflict('Original relay owner/operation changed before model reservation')
            if expected_context is not None and (context.get('process') is None or context['process'].returncode is not None):
                raise Conflict('Original relay process ended before model reservation')
            if (not context or context.get("closing") or value["owner_status"] != "READY" or context.get("workflow_policy_sync_error") or
                    value.get("observation_error") or
                    value["model_calls_reserved"] >= manifest["request"]["max_calls"] or
                    datetime.fromisoformat(context["expires_at"]) <= datetime.now(timezone.utc) or
                    not value["active_operation_id"]):
                raise Conflict("Conversation model-call allowance expired")
            from .resource_retention import require_confirmed
            require_confirmed(self,connection,pid,ident)
            from .task_resources import reserve_model
            reserve_model(input_bytes)
            receipt = uuid.uuid4().hex
            self._write(self._path(pid, ident, "rpc", receipt + ".json"), {
                "id": receipt, "request_sha256": request_sha, "generation": value["generation"],
                "operation_id": value["active_operation_id"], "reserved_at": now(), "status": "RESERVED_BEFORE_FORWARD"})
            value["model_calls_reserved"] += 1
            self._write(self._path(pid, ident, "state.json"), value, replace=True)
            return receipt

    def _rpc_complete(self, pid, ident, value):
        self._write(self._path(pid, ident, "rpc-results", value["receipt_id"] + ".json"), value)

    async def _http(self, context, method, path, body=None, *, expected=200, params=None, timeout=None):
        # Explicit merge preserves the UTF-8 authoritative directory when a
        # native endpoint also needs provider/model/limit query parameters.
        directory_key = "location[directory]" if path.startswith("/api/") else "directory"
        options = {}
        if timeout is not None:
            if method != "GET" or type(timeout) not in {int, float} or not 0 < timeout <= 130:
                raise InvalidInput("An explicit bounded deadline is allowed only for original readonly GET")
            options["timeout"] = timeout
        if method == 'GET':
            from packages.domain.task_resource_budget import current_task_resource_budget
            if current_task_resource_budget() is not None:
                from .resource_source_reads import read_native_json
                return await read_native_json(context,path,expected=expected,
                    params={directory_key:context['directory'],**(params or {})},options=options)
        # An idle GET taken across an in-flight mutation is not a stop barrier.
        # This tracks our HTTP lifecycle, not a claim that a timed-out upstream
        # POST was cancelled. Such dispatches still need exact terminal proof.
        mutation = method != "GET"
        if mutation:
            context["native_commands_inflight"] = context.get("native_commands_inflight", 0) + 1
            context["native_command_epoch"] = context.get("native_command_epoch", 0) + 1
        try:
            response = await context["client"].request(method, path, json=body,
                params={directory_key: context["directory"], **(params or {})}, **options)
        finally:
            if mutation:
                context["native_commands_inflight"] -= 1
                context["native_command_epoch"] += 1
        if response.status_code != expected or len(response.content) > 4 * 1024 * 1024:
            context["last_http_error"] = {"method": method, "path": path, "http_status": response.status_code,
                                           "body_sha256": hashlib.sha256(response.content).hexdigest()}
            raise Conflict("Original API response unavailable: " + method + " " + path + " " + str(response.status_code))
        context.pop("last_http_error", None)
        return None if not response.content else _json(response.content)

    def _clean(self, context, value):
        return OpenCodeNativeGateway._redact(value, context)

    async def _native_session(self, manifest, context, config):
        rules = [{"permission": key, "pattern": pattern, "action": action}
                 for key, value in config["permission"].items()
                 for pattern, action in (value.items() if isinstance(value, dict) else [("*", value)])]
        edit_rules = None
        if context.get('change_review'):
            from packages.adapters.opencode_permission_paths import native_write_policy
            ref = manifest['request']['workspace_ref']
            workspace = self.workspaces.get(manifest['project_id'], ref['id'], ref['revision'])
            _root, identity = self.workspaces._root(workspace['root'])
            if (workspace['binding_sha256'] != ref['binding_sha256'] or identity != workspace['root_identity'] or
                    identity != manifest['directory_identity']):
                raise Conflict('The selected write workspace identity changed before native session admission')
            location = await self._http(context, 'GET', '/path')
            policy = native_write_policy(workspace, location)
            edit_rules = [{'permission': 'edit', 'pattern': pattern, 'action': action} for pattern, action in policy.items()]
            rules = [rule for rule in rules if rule['permission'] != 'edit'] + edit_rules
            context['native_write_declaration'] = {'directory': location['directory'], 'worktree': location['worktree'],
                'permission_sha256': digest(edit_rules), 'source': 'ORIGINAL_PATH_API_AND_EXISTING_WORKSPACE_GRANTS'}
        if context['session_id']:
            session = await self._http(context, 'GET', '/session/' + context['session_id'])
            if session.get('id') != context['session_id'] or session.get('directory') != context['directory']:
                raise Conflict('Persisted original session has another directory or identity')
            if edit_rules is not None and [r for r in session.get('permission', []) if r.get('permission') == 'edit'] != edit_rules:
                raise Conflict('Existing original write declarations differ; create a new explicit conversation without patching the old session')
        else:
            # Persist intent before the non-idempotent native create. Missing
            # legacy evidence or an unknown create response must never be
            # converted into permission to create another native session.
            self._update(manifest['project_id'],manifest['id'],native_initialization={
                'generation':context['generation'],'manifest_sha256':digest(manifest),
                'stage':'NATIVE_CREATE_ATTEMPTED','original_session_id':None})
            session = await self._http(context, 'POST', '/session', {'title': manifest['request']['title'], 'permission': rules})
            if (not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}', str(session.get('id'))) or
                    edit_rules is not None and (session.get('directory') != context['directory'] or session.get('permission') != rules)):
                raise Conflict('Original session identity and exact write declarations were not established')
            context['session_id'] = session['id']
        # This identity receipt survives a later plugin/context/startup failure.
        # It does not mark READY or authorize a user prompt.
        self._update(manifest['project_id'],manifest['id'],original_session_id=context['session_id'],
            native_initialization={'generation':context['generation'],'manifest_sha256':digest(manifest),
                'stage':'NATIVE_SESSION_KNOWN','original_session_id':context['session_id']})
        return session

    def _editor_assert(self,manifest,*,connection=None):
        editor=getattr(self,'editor_changes',None)
        if editor is not None:editor.assert_manifest(manifest,connection=connection)

    @staticmethod
    def _admit_native_initialization(manifest,state):
        if (type(state.get('generation')) is not int or state['generation']<0
                or state.get('original_session_id') is not None and not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}',str(state['original_session_id']))):
            raise Conflict('The saved original native initialization identity is malformed')
        if state.get('original_session_id') or state.get('generation')==0:return
        if state.get('native_initialization')!={'generation':state.get('generation'),
                'manifest_sha256':digest(manifest),'stage':'NATIVE_NOT_ATTEMPTED','original_session_id':None}:
            raise Conflict('The original native session creation outcome is unknown; no duplicate create is allowed')

    async def _start(self,pid,ident,*,expected_generation=None,load_operation=None):
        manifest,_=self._manifest(pid,ident)
        self._admit_native_initialization(manifest,self._state(pid,ident))
        if load_operation is not None and getattr(self,'conversations',None) is not None and callable(getattr(self.conversations,'admit_load',None)):
            self.conversations.admit_load(manifest,self.operation(pid,ident,load_operation))
        if manifest.get('canonical_execution_claim') is not None:
            self.context_authority.admit_configuration_start(manifest,self._state(pid,ident))
        await self.editor_captures.broker.admit(manifest,generation=self._state(pid,ident)['generation'],
            operation_id=ident,kind='OWNER_START')
        editor=getattr(self,'editor_changes',None)
        if editor is None:
            return await self._start_impl(pid,ident,expected_generation=expected_generation,load_operation=load_operation)
        with editor.starting_owner(pid,ident):
            return await self._start_impl(pid,ident,expected_generation=expected_generation,load_operation=load_operation)

    async def _start_impl(self, pid, ident, *, expected_generation=None,load_operation=None):
        if self.closed:
            raise Conflict("Conversation controller is closed")
        manifest, _ref = self._manifest(pid, ident)
        profile, binding, directory = await asyncio.to_thread(self._check_scope, manifest)
        runtime = self._path(pid, ident, "runtime")
        home = runtime / "home"
        config_path = home / "config" / "opencode" / "opencode.json"
        self._preflight_private(home, config_path, self._state(pid, ident).get("config_file_sha256"))
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._state(pid, ident)
            if expected_generation is not None and (state['generation'] != expected_generation
                    or state['owner_status'] not in {'EXPIRED', 'DISCONNECTED', 'BLOCKED'}):
                raise Conflict('The previous original generation changed; inspect the exact load operation')
            if ident in self.active or self._alive(state.get("controller_identity")) and state["owner_status"] in {"STARTING", "READY"}:
                raise Conflict("Conversation has another live owner")
            if self._alive(state.get("process_identity")):
                raise Conflict("Previous exact original process remains alive; no adoption or duplicate owner is allowed")
            if load_operation is not None and getattr(self,'conversations',None) is not None and callable(getattr(self.conversations,'admit_load',None)):
                self.conversations.admit_load(manifest,self.operation(pid,ident,load_operation))
            self._admit_native_initialization(manifest,state)
            state, _ = self._reconcile_event_tail(pid, ident, state)
            self._editor_assert(manifest,connection=connection)
            state.update(owner_status="STARTING", native_status="UNKNOWN", generation=state["generation"] + 1,
                         controller_identity=self._identity, updated_at=now())
            state['native_initialization']={'generation':state['generation'],'manifest_sha256':digest(manifest),
                'stage':'NATIVE_SESSION_KNOWN' if state.get('original_session_id') else 'NATIVE_NOT_ATTEMPTED',
                'original_session_id':state.get('original_session_id')}
            self._write(self._path(pid, ident, "state.json"), state, replace=True)
        (home / "tmp").mkdir(parents=True, exist_ok=True)
        context = {"project_id":pid,"process_start_evidence":"NOT_ATTEMPTED",
                   "workspace_root_identity":manifest.get("directory_identity"),"generation": state["generation"], "closing": False, "loop": asyncio.get_running_loop(), "profile": profile, "directory": directory,
                   "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=manifest["request"]["ttl_seconds"])).isoformat(),
                   "connected": asyncio.Event(), "session_id": state["original_session_id"], "known_sessions": set(), "lock": asyncio.Lock()}
        ordinary_tools = None
        if manifest.get('native_message_admission'):
            from packages.adapters.opencode_native_message import TOOL_NAME
            deadline = self._ordinary_deadline(manifest)
            if deadline <= datetime.now(timezone.utc):
                raise Conflict('Ordinary admission absolute deadline expired')
            context['expires_at'] = min(datetime.fromisoformat(context['expires_at']), deadline).isoformat()
            context['native_message_absolute_deadline'] = deadline.isoformat()
            ordinary_tools = {TOOL_NAME}
        if manifest.get('native_review_admission') and self.native_reviews.capability_task_admission(manifest['native_review_admission']):
            admission = self.native_reviews.validate_admission(manifest['native_review_admission'], manifest['request'])
            deadline = self.native_reviews._task(admission['task_root_operation_id'])['expires_at']
            context['expires_at'] = min(context['expires_at'], deadline)
        password = secrets.token_hex(24)
        wire = None if is_subscription(profile) else ProviderWire(profile,
            admit=lambda sha,input_bytes=0: self._reserve_rpc(pid, ident, sha,expected_context=context,input_bytes=input_bytes),
            complete=lambda value: self._rpc_complete(pid, ident, value),
            max_output_tokens=manifest["request"]["max_output_tokens"],include_input_bytes=True, allowed_tool_names=ordinary_tools,
            absolute_deadline=datetime.fromisoformat(context["expires_at"]).timestamp() if ordinary_tools is not None or
                manifest.get('native_review_admission') and self.native_reviews.capability_task_admission(manifest['native_review_admission']) else None).start()
        context.update(wire=wire, secrets=[*private_model_redactions(profile), password, *([wire.key] if wire else [])])
        try:
            config = self._config(manifest, profile, binding, wire)
            from .model_authority import start as start_model_authority
            start_model_authority(self,manifest,context,runtime,config)
            await prepare_workflow(self, manifest, context, config, binding)
            if manifest.get('native_review_admission'):
                self.native_reviews.prepare_owner(manifest, context, runtime, config)
            if manifest.get('native_message_admission'):
                self.native_messages.prepare_owner(manifest, context, runtime, config)
            if 'WORKSPACE_WRITE' in manifest['request'].get('allowed_effects', []):
                from packages.adapters.opencode_change_review import prepare
                selected = self.workspaces.get(pid, manifest['request']['workspace_ref']['id'], manifest['request']['workspace_ref']['revision'])
                context['change_review'] = prepare(runtime, project_id=pid, session_ref=_ref,
                    generation=context['generation'], workspace=selected)
                config['plugin'].append(Path(context['change_review']['entry']).as_uri())
            if ('BACKGROUND_COMMAND' in manifest['request'].get('allowed_effects', [])
                    and manifest['request'].get('command_allowlist') and manifest['request'].get('workspace_ref')):
                from packages.adapters.opencode_command_observation import (
                    CommandObservationCapacity, prepare as prepare_command_observation)
                selected = self.workspaces.get(pid, manifest['request']['workspace_ref']['id'],
                    manifest['request']['workspace_ref']['revision'])
                # This explicit file set is a limited observation basis,
                # never a claim to capture all of a command's dependencies.
                from .command_observation_scope import observation_sources
                sources = observation_sources(manifest['request'], selected,
                    clean=lambda name: self._clean(context, name))
                for name in sources:
                    self.workspaces._private(selected, name)
                try:
                    context['command_observation'] = prepare_command_observation(runtime,
                        project_id=pid, session_ref=_ref, generation=context['generation'],
                        workspace=selected, source_paths=sources)
                except CommandObservationCapacity:
                    # The optional observer is bounded. Exhausting its history
                    # must not prevent the original owner from being resumed.
                    # All integrity/configuration failures still fail closed.
                    context['command_observation_reasons'] = ['COMMAND_GENERATION_CAPACITY_REACHED']
                else:
                    config['plugin'].append(Path(context['command_observation']['entry']).as_uri())
            # V1 and V2 use this same private file. V2 does not consume the
            # V1-only OPENCODE_CONFIG environment override.
            config_path.parent.mkdir(parents=True, exist_ok=True)
            if config_path.exists():
                previous = runtime / f"generation-{state['generation'] - 1}-config.json"
                raw = config_path.read_bytes()
                if previous.exists():
                    if previous.read_bytes() != raw:
                        raise Conflict("Historical private configuration archive differs")
                else:
                    with previous.open("xb") as output:
                        output.write(raw)
                        output.flush()
                        os.fsync(output.fileno())
                    previous.chmod(0o600)
            config_path.write_text(canonical(config), encoding="utf-8")
            config_path.chmod(0o600)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            context["logs"] = [open(runtime / (f"generation-{state['generation']}-" + name), "xb")
                               for name in ("stdout.bin", "stderr.bin")]
            environment = OpenCodeBridge._environment(home, config_path, password)
            if context.get('model_authority'):
                environment['EEAS_MODEL_AUTHORITY_CONFIG']=context['model_authority']['prepared']['config_path']
                environment['OPENCODE_PURE']='false'
            if is_subscription(profile):
                environment = subscription_environment(environment, profile)
            if context.get("workflow"):
                environment["EEAS_WORKFLOW_EVENT_TOKEN"] = context["workflow_event_token"]
                environment["EEAS_WORKFLOW_PLAN_TOKEN"] = context["workflow_plan_token"]
                environment["OPENCODE_PURE"] = "false"
            if context.get('change_review'):
                environment['EEAS_CHANGE_REVIEW_CONFIG'] = context['change_review']['config_path']
                environment['OPENCODE_PURE'] = 'false'
            if context.get('command_observation'):
                environment['EEAS_COMMAND_OBSERVATION_CONFIG'] = context['command_observation']['config_path']
                environment['OPENCODE_PURE'] = 'false'
            if context.get('native_review', {}).get('prepared'):
                environment['EEAS_NATIVE_REVIEW_CONFIG'] = context['native_review']['prepared']['config_path']
                environment['OPENCODE_PURE'] = 'false'
            if context.get('native_message'):
                environment['EEAS_NATIVE_MESSAGE_CONFIG'] = context['native_message']['prepared']['config_path']
                environment['OPENCODE_PURE'] = 'false'
            # The original registry enables this tool for app/cli/desktop clients
            # or this documented runtime flag. Our explicit client remains eeas.
            environment["OPENCODE_ENABLE_QUESTION_TOOL"] = "true"
            executable = str(profile.executable)
            context['process_start_evidence'] = 'ATTEMPTED'
            try:
                context["process"] = await asyncio.create_subprocess_exec(executable,
                    *([] if context.get('model_authority') or context.get("workflow") or context.get('change_review') or context.get('command_observation') or context.get('native_review', {}).get('prepared') or context.get('native_message') else ["--pure"]), "serve",
                    "--hostname", "127.0.0.1", "--port", str(port), cwd=directory,
                    env=environment, stdout=context["logs"][0], stderr=context["logs"][1])
            except OSError as launch_error:
                # Only a refusal identifying the requested program or working directory
                # establishes this process never started. Cancellation and other errors remain uncertain.
                if (launch_error.errno in {errno.ENOENT, errno.EACCES, errno.ENOEXEC, errno.ENOTDIR}
                        and launch_error.filename in {executable, str(directory)}):
                    context['process_start_evidence'] = 'OS_REFUSED'
                raise
            context['process_start_evidence'] = 'HANDLE_RECEIVED'
            if type(context['process'].pid) is not int or context['process'].pid <= 0:
                raise Conflict('Original owner process has no positive native PID')
            self.active[ident] = context
            process_identity = {"pid": context["process"].pid, "created": psutil.Process(context["process"].pid).create_time()}
            self._update(pid, ident, process_identity=process_identity)
            context["client"] = httpx.AsyncClient(base_url="http://127.0.0.1:" + str(port), auth=("eeas", password),
                follow_redirects=False, trust_env=False, timeout=10)
            async def readiness():
                while context["process"].returncode is None:
                    try:
                        return await self._http(context, "GET", "/global/health")
                    except httpx.TransportError:
                        await asyncio.sleep(.05)
                raise Conflict("Original CLI exited during startup")
            health = await asyncio.wait_for(readiness(), timeout=20)
            if health != {"healthy": True, "version": VERSION}:
                raise Conflict("Original CLI version differs")
            effective = await self._http(context, "GET", "/config")
            for key in ("permission", "plugin", "lsp", "autoupdate", "enabled_providers", "mcp", "skills", "model"):
                if effective.get(key) != config[key]:
                    raise Conflict("Original effective configuration differs: " + key)
            # Selected MCP sources already carry explicit reviewed request
            # deadlines. Verified Windows package activation can take over 10s;
            # extend only these startup observations, never mutation/reply POSTs.
            selected_mcp = binding["value"]["native_config_fragment"]["mcp"]
            startup_deadline = min(130, max(10, max((item["timeout"] for item in selected_mcp.values()), default=0) / 1000 + 10))
            definitions = await self._http(context, "GET", "/experimental/tool", params={"provider": profile.provider_id, "model": profile.model_id}, timeout=startup_deadline)
            agents = await self._http(context, "GET", "/agent")
            from packages.adapters.opencode_model_authority import ready as model_authority_ready
            until_ready = asyncio.get_running_loop().time() + 5
            while not model_authority_ready(context['model_authority']['prepared']):
                if asyncio.get_running_loop().time() >= until_ready:
                    raise Conflict('Original model authority hook did not confirm loading')
                await asyncio.sleep(.05)
            context['workflow_mode'] = observed_workflow_definition(
                config.get('agent', {}).get(WORKFLOW_AGENT), effective, agents)
            self._update(pid, ident, workflow_mode_definition=context['workflow_mode'])
            if context.get('change_review'):
                from packages.adapters.opencode_change_review import ready
                until_ready = asyncio.get_running_loop().time() + 5
                while not ready(context['change_review']):
                    if asyncio.get_running_loop().time() >= until_ready:
                        raise Conflict('The original change review plugin did not confirm loading')
                    await asyncio.sleep(.05)
            if context.get('command_observation'):
                from packages.adapters.opencode_command_observation import ready as command_observation_ready
                until_command_ready = asyncio.get_running_loop().time() + 5
                while not command_observation_ready(context['command_observation']):
                    if asyncio.get_running_loop().time() >= until_command_ready:
                        raise Conflict('The original command observation plugin did not confirm loading')
                    await asyncio.sleep(.05)
            if context.get("workflow"):
                expected_aliases = {item['alias'] for item in context['workflow']['aliases']}
                if not expected_aliases <= {item.get('name') for item in agents} or not any(
                        item.get('id') == 'eeas_workflow_graph' for item in definitions):
                    raise Conflict('The approved Dynamic Workflow plugin did not load in the original runtime')
                self._update(pid, ident, workflow_plugin={
                    'id': 'eeas.dynamic-workflow', 'status': 'ORIGINAL_TOOL_AND_ALIASES_OBSERVED',
                    'plan_ref': context['workflow_plan_ref'],
                    'policy_ref': context['workflow']['admission']['policy_ref'],
                    'admission_sha256': digest(context['workflow']['admission']),
                    'aliases_observed': len(expected_aliases), 'qualification': 'LOAD_ONLY_NOT_INFERENCE'})
            mcp = await self._http(context, "GET", "/mcp", timeout=startup_deadline)
            if context.get('native_review'):
                if context['native_review'].get('prepared'):
                    from packages.adapters.opencode_native_review import ready
                    until_review_ready = asyncio.get_running_loop().time() + 5
                    while not ready(context['native_review']['prepared']):
                        if asyncio.get_running_loop().time() >= until_review_ready:
                            raise Conflict('The original frozen-page plugin did not confirm loading')
                        await asyncio.sleep(.05)
                self.native_reviews.validate_loaded(manifest, context, definitions, mcp)
            if context.get('native_message'):
                from packages.adapters.opencode_native_message import ready
                until_message_ready = asyncio.get_running_loop().time() + 5
                while not ready(context['native_message']['prepared']):
                    if asyncio.get_running_loop().time() >= until_message_ready:
                        raise Conflict('The original ordinary context plugin did not confirm loading')
                    await asyncio.sleep(.05)
                self.native_messages.validate_loaded(manifest, context, definitions, mcp)
            # Original V2 configuration plugins start asynchronously. Observe
            # that API until selected sources appear; saved config is not load
            # evidence, and an absent source remains explicitly unqualified.
            discovery_deadline = asyncio.get_running_loop().time() + 5
            while True:
                skills = await self._http(context, "GET", "/api/skill")
                if skills.get("location", {}).get("directory") != directory or not isinstance(skills.get("data"), list):
                    raise Conflict("Original V2 skill discovery has another location or protocol shape")
                loaded = self._capability_load(binding, definitions, mcp, skills,manifest=manifest)
                if all(not item["configured_in_original"] or item["loaded_in_original_session"] for item in loaded["skills"]) or asyncio.get_running_loop().time() >= discovery_deadline:
                    break
                await asyncio.sleep(.1)
            self._update(pid, ident, native_skill_discovery=self._clean(context, skills), native_skill_discovery_sha256=digest(skills))
            session = await self._native_session(manifest, context, config)
            self.context_authority.begin(manifest,context,loaded_existing=bool(state['original_session_id']))
            from packages.adapters.opencode_model_authority import bind as bind_model_authority
            context['model_authority']['prepared']=bind_model_authority(context['model_authority']['prepared'],context['session_id'])
            if context.get('native_review', {}).get('prepared'):
                from packages.adapters.opencode_native_review import bind
                context['native_review']['prepared'] = bind(context['native_review']['prepared'], context['session_id'])
            if context.get('native_message'):
                from packages.adapters.opencode_native_message import bind
                context['native_message']['prepared'] = bind(context['native_message']['prepared'], context['session_id'])
            if context.get('change_review'):
                from packages.adapters.opencode_change_review import bind, record_admission
                context['change_review'] = bind(context['change_review'], context['session_id'])
                with self.store.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    record_admission(runtime, context['change_review'])
            if context.get('command_observation'):
                from packages.adapters.opencode_command_observation import bind as bind_command_observation
                from packages.adapters.opencode_command_observation import record_admission as record_command_admission
                context['command_observation'] = bind_command_observation(
                    context['command_observation'], context['session_id'])
                with self.store.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    record_command_admission(runtime, context['command_observation'])
            context["known_sessions"].add(context["session_id"])
            if context.get('native_message'):
                # This private ordinary owner has a new session and no replay/load.
                # Start before subscribing and before the first prompt can dispatch.
                context['ordinary_message_snapshot'] = {'generation': context['generation'],
                    'session_id': context['session_id'], 'infos': {}, 'parts': {}, 'last_event_seq': 0}
            self._update(pid, ident, original_session_id=context["session_id"], owner_status="READY", native_status="idle",
                active_operation_id=None, capability_load=loaded,
                original_agents=self._clean(context, agents), original_session=self._clean(context, session),
                owner_expires_at=context["expires_at"], effective_config_sha256=digest(effective),
                config_file_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                change_review_admission=context.get('change_review'),
                command_observation_admission=context.get('command_observation'),
                command_observation_reasons=context.get('command_observation_reasons', []),
                native_write_declaration=context.get('native_write_declaration'),
                observation_error=None, native_messages_error=None, load_error=None)
            # Bind lifecycle operations to the private client and exact process
            # handle created here, not any later global capability/config index.
            context['owner_binding'] = {'process': context['process'], 'client': context['client'],
                'session_id': context['session_id'], 'directory': directory, 'generation': context['generation'],
                'process_identity': process_identity, 'config_path': str(config_path),
                'config_file_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest()}
            context["listener"] = asyncio.create_task(self._listen(pid, ident, context))
            await asyncio.wait_for(context["connected"].wait(), timeout=10)
            if context.get("stream_error"):
                raise Conflict("Original event stream did not establish continuity")
            context["monitor"] = asyncio.create_task(self._monitor(pid, ident, context))
            self._event(pid, ident, "ORIGINAL_SESSION_OWNER_READY", {"generation": state["generation"], "original_session_id": context["session_id"],
                "loaded_existing_session": bool(state["original_session_id"]), "automatic_post_replay": False})
            return await self.inspect(pid, ident)
        except BaseException as error:
            # A broken journal/state writer must never bypass exact-handle
            # cleanup or replace the original startup error with a log error.
            try:
                current = self.active.get(ident)
                if (current is None or current is context) and self._state(pid, ident)['generation'] == context['generation']:
                    # A relay may exist before subprocess creation. Publish the closing
                    # owner before cleanup yields, so an unconfirmed handle can be retried.
                    context['closing'] = True
                    self.active[ident] = context
                    self._update(pid, ident, owner_status="BLOCKED", native_status="UNKNOWN", load_error=type(error).__name__)
                    self._event(pid, ident, "ORIGINAL_OWNER_START_FAILED", {"error_type": type(error).__name__})
            except BaseException as reporting_error:
                context["startup_reporting_error"] = type(reporting_error).__name__
            finally:
                try:
                    await self._dispose(pid, ident, context, status="BLOCKED")
                except BaseException as cleanup_error:
                    context["startup_cleanup_error"] = type(cleanup_error).__name__
                    if self.active.get(ident) is not context:
                        self._detached_cleanup[id(context)] = (pid, ident, context)
            raise

    def _capability_load(self, binding, definitions, mcp, skill_observation,*,manifest=None):
        capabilities=(self.experience_validation.capabilities(manifest) if manifest and manifest.get('experience_validation_admission') else self.capabilities)
        skill_registered = any(item.get("id") == "skill" for item in definitions)
        skills = []
        for ident, ref in binding["value"]["configuration_refs"]["skills"].items():
            saved = capabilities._configuration("skills", ref)["configuration"]
            expected_path = str(capabilities._path("skill-files", ident, f'{ref["revision"]:010d}', "SKILL.md"))
            effective=saved['enabled'] and str(Path(expected_path).parent) in binding['value']['native_config_fragment']['skills']['paths']
            actual = next((item for item in skill_observation["data"] if item.get("name") == ident and item.get("location") == expected_path), None)
            skills.append({"id": ident, "config_ref": ref, "saved": True, "probed": False,
                "configured_in_original": effective, "native_tool_registered": skill_registered,
                "loaded_in_original_session": bool(effective and actual),
                "load_status": "ORIGINAL_V2_SKILL_API_DISCOVERED" if effective and actual else "EFFECTIVE_CONFIG_NOT_YET_DISCOVERED" if effective else "DISABLED",
                "load_error": "SELECTED_SKILL_NOT_DISCOVERED" if effective and not actual else None,
                "discovery_receipt": {"native_path": "/api/skill", "response_sha256": digest(skill_observation),
                                      "actual_skill_sha256": digest(actual)} if actual else None,
                "invoked": False, "qualified": False, "permission_granted_by_import": False})
        servers = {}
        for ident, ref in binding["value"]["configuration_refs"]["mcp"].items():
            status = mcp.get(ident, {})
            servers[ident] = {"config_ref": ref, "saved": True, "probed": False,
                "configured_in_original": ident in binding["value"]["native_config_fragment"]["mcp"],
                "native_tool_prefix": original_mcp_identifier(ident) + "_",
                "loaded_in_original_session": status.get("status") == "connected", "load_status": status.get("status", "NOT_LOADED"),
                "load_error": status.get("error"), "invoked": False, "qualified": False}
        return {"binding_sha256": binding["sha256"], "skills": skills, "mcp": servers}

    async def load(self, pid, ident, body, *, configuration_generation_zero=False):
        body = LoadSession.model_validate(body)
        manifest, _ = self._manifest(pid, ident, body.session_ref.model_dump())
        if manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission'):
            raise Conflict('An expired native page owner requires a new explicit submission and fresh permission')
        expected_generation=body.expected_generation
        if configuration_generation_zero:
            if expected_generation is not None or manifest.get('canonical_execution_claim') is None:
                raise Conflict('Only an exact confirmed configuration candidate can use internal generation-zero LOAD')
            expected_generation=0
        old, created = self._claim(pid, ident, body.operation_id, "LOAD", body.model_dump(exclude_none=True))
        if not created:
            return {"operation": old, "session": self.get(pid, ident)}
        try:
            state = self._state(pid, ident)
            if configuration_generation_zero and (state.get('original_session_id') is not None
                    or state.get('active_operation_id') is not None or state.get('process_identity') is not None
                    or state.get('controller_identity') is not None):
                raise Conflict('The generation-zero candidate already has native or process identity; inspect its original LOAD')
            if expected_generation is not None and (state['generation'] != expected_generation
                    or state['owner_status'] not in {'EXPIRED', 'DISCONNECTED', 'BLOCKED'}):
                raise Conflict('The previous original generation changed; inspect the exact load operation')
            if (ident in self.active or self._alive(state.get("process_identity"))
                    or self._alive(state.get("controller_identity")) and state["owner_status"] in {"STARTING", "READY"}):
                raise Conflict("Disconnect the exact previous original owner before loading a new generation")
            if getattr(self,'conversations',None) is not None and callable(getattr(self.conversations,'admit_load',None)):
                self.conversations.admit_load(manifest,self.operation(pid,ident,body.operation_id))
            if state.get("active_operation_id"):
                operation = self.operation(pid, ident, state["active_operation_id"])
                if operation["status"] not in TERMINAL:
                    self._finish(pid, ident, operation["id"], "UNKNOWN_OUTCOME", reason="OWNER_LOST_NO_AUTOMATIC_REPLAY")
            session = await self._start(pid, ident, expected_generation=expected_generation,load_operation=body.operation_id)
            self._finish(pid, ident, body.operation_id, "RETURNED", loaded_generation=session['generation'])
            return {"operation": self.operation(pid, ident, body.operation_id), "session": session}
        except BaseException:
            self._finish(pid, ident, body.operation_id, "FAILED")
            raise

    async def _listen(self, pid, ident, context):
        try:
            async with context["client"].stream("GET", "/event", params={"directory": context["directory"]}, timeout=None) as response:
                if response.status_code != 200:
                    raise Conflict("Original event stream denied")
                lines, size = [], 0
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        lines.append(line[5:].lstrip())
                        size += len(line.encode())
                        if size > 1024 * 1024:
                            raise Conflict("Original event frame exceeds bound")
                    elif not line and lines:
                        raw = "\n".join(lines).encode()
                        lines, size = [], 0
                        event = decode_event(raw)
                        if event["type"] == "server.connected":
                            context["connected"].set()
                        props = event.get("properties", {})
                        info = props.get("info", {})
                        part = props.get('part', {})
                        native_id = props.get("sessionID") or info.get("sessionID") or part.get('sessionID') or info.get("id")
                        if event["type"] == "session.created" and info.get("parentID") in context["known_sessions"]:
                            context["known_sessions"].add(info["id"])
                        if event["type"] != "server.connected" and native_id not in context["known_sessions"]:
                            continue
                        committed_seq = await asyncio.to_thread(self._event, pid, ident, "ORIGINAL_EVENT", {
                            "native_payload": self._clean(context, event), "native_payload_sha256": hashlib.sha256(raw).hexdigest(),
                            "generation": context["generation"]})
                        attribution_changed=context.get('resource_attribution_changed')
                        if attribution_changed is not None:attribution_changed.set()
                        self._ordinary_message_event(context, event, committed_seq)
                        if (context.get('native_review') or context.get('native_message')) and (event['type'] == 'session.idle' or
                                event['type'] == 'session.status' and props.get('status', {}).get('type') == 'idle' or
                                event['type'] == 'message.updated' and info.get('role') == 'assistant' and info.get('time', {}).get('completed')):
                            # Only original events + the existing monitor may
                            # advance a queued review. GET/inspect never does.
                            if context.get('native_review'):
                                context['native_review_terminal_event'] = True
                            if context.get('native_message'):
                                context['native_message_terminal_event'] = True
            if not context["closing"]:
                raise Conflict("Original stream ended")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            context["stream_error"] = type(error).__name__
            context["connected"].set()
            await asyncio.to_thread(self._event, pid, ident, "ORIGINAL_EVENT_CONTINUITY_LOST", {"error_type": type(error).__name__})

    @staticmethod
    def _ordinary_message_event(context, event, committed_seq):
        """Project only factual updates already committed by this owner's SSE reader.

        Original v1.19.0 cannot encode its persisted json_schema user messages in
        the list endpoint. This is not a successful GET, event replay, or a new
        model loop. Missing continuity and bounded-cache failure stay unknown.
        """
        if not context.get('native_message'):
            return
        snapshot = context.get('ordinary_message_snapshot')
        if (not snapshot or snapshot['generation'] != context['generation']
                or snapshot['session_id'] != context['session_id'] or snapshot.get('error')):
            raise Conflict('Ordinary committed SSE snapshot has another owner or lost continuity')
        kind, props = event['type'], event.get('properties', {})
        native = context['session_id']
        if props.get('sessionID') != native:
            return  # Known child sessions never become the primary conversation.
        if type(committed_seq) is not int or committed_seq <= snapshot['last_event_seq']:
            raise Conflict('Ordinary SSE update is not a new committed owner event')
        infos, parts = snapshot['infos'], snapshot['parts']
        def identity(value, prefix):
            if not isinstance(value, str) or not re.fullmatch(prefix + r'[A-Za-z0-9_-]{1,100}', value):
                raise Conflict('Ordinary SSE message/part identity is invalid')
            return value
        try:
            if kind == 'message.updated':
                info = props.get('info', {})
                mid = identity(info.get('id'), 'msg_')
                if info.get('sessionID') != native or info.get('role') not in {'user', 'assistant'}:
                    raise Conflict('Ordinary SSE message has another session or role')
                if info['role'] == 'assistant':
                    identity(info.get('parentID'), 'msg_')
                prior = infos.get(mid)
                if prior and (prior['role'] != info['role'] or prior.get('parentID') != info.get('parentID')):
                    raise Conflict('Ordinary SSE message parent/role changed')
                infos[mid] = json.loads(canonical(info))
            elif kind == 'message.part.updated':
                part = props.get('part', {})
                part_id, mid = identity(part.get('id'), 'prt_'), identity(part.get('messageID'), 'msg_')
                if part.get('sessionID') != native:
                    raise Conflict('Ordinary SSE part has another session')
                prior = parts.get(part_id)
                if prior and (prior['messageID'] != mid or prior.get('type') != part.get('type')
                              or prior.get('callID') != part.get('callID')):
                    raise Conflict('Ordinary SSE part changed its original message/tool call')
                parts[part_id] = json.loads(canonical(part))
            elif kind == 'message.part.delta':
                part_id, mid = identity(props.get('partID'), 'prt_'), identity(props.get('messageID'), 'msg_')
                part = parts.get(part_id)
                if (not part or part['messageID'] != mid or props.get('field') != 'text'
                        or not isinstance(part.get('text'), str) or not isinstance(props.get('delta'), str)):
                    raise Conflict('Ordinary SSE text delta has no exact original part')
                part['text'] += props['delta']
            elif kind == 'message.removed':
                mid = identity(props.get('messageID'), 'msg_')
                infos.pop(mid, None)
                for key in [key for key, part in parts.items() if part['messageID'] == mid]:
                    parts.pop(key)
            elif kind == 'message.part.removed':
                part_id, mid = identity(props.get('partID'), 'prt_'), identity(props.get('messageID'), 'msg_')
                if part_id in parts and parts[part_id]['messageID'] != mid:
                    raise Conflict('Ordinary SSE removal has another original part')
                parts.pop(part_id, None)
            if len(infos) > 200 or len(parts) > 4000 or len(canonical([infos, parts]).encode()) > 4 * 1024 * 1024:
                raise Conflict('Ordinary SSE snapshot exceeds its bounded message budget')
            snapshot['last_event_seq'] = committed_seq
        except (Conflict, ValueError, KeyError, TypeError):
            snapshot['error'] = 'ORIGINAL_COMMITTED_SSE_SNAPSHOT_UNAVAILABLE'
            raise

    @staticmethod
    def _ordinary_messages(context):
        snapshot = context.get('ordinary_message_snapshot')
        if (not context.get('native_message') or not snapshot or snapshot.get('error')
                or snapshot['generation'] != context['generation'] or snapshot['session_id'] != context['session_id']
                or context.get('stream_error') or not context['connected'].is_set()):
            raise Conflict('Ordinary original SSE continuity is unavailable; no message GET substitution')
        messages = [{'info': info, 'parts': [part for part in snapshot['parts'].values()
                     if part['messageID'] == mid]} for mid, info in snapshot['infos'].items()]
        return json.loads(canonical(messages))

    def _lifecycle_current(self, manifest, context):
        """A changed future-owner capability index is not a new execution grant.

        Original result observation may continue only while its provider,
        workspace and private configuration still identify the admitted owner.
        Stopping that exact process never depends on these mutable materials.
        """
        try:
            if not self._configuration_current(manifest, check_capabilities=False):
                return False
            if is_subscription(context['profile']):
                from packages.adapters.opencode_account import authorization_epoch
                if (not context['profile'].authorization_epoch or
                        authorization_epoch(context['profile'].account_data_home) != context['profile'].authorization_epoch):
                    return False
            binding = context.get('owner_binding')
            if binding:
                config_path = Path(binding['config_path'])
                if not config_path.is_file():
                    return False
                self._preflight_private(config_path.parents[2], config_path, binding['config_file_sha256'])
            return True
        except (Conflict, InvalidInput, NotFound, OSError):
            return False

    def _context(self, pid, ident, ref=None, generation=None, *, verify_scope=True,
                 allow_stale_authorization=False, intent=None):
        # Existing catalog readers use verify_scope=False. It maps only to the
        # explicitly checked observation intent, never an execution shortcut.
        intent = intent or ('EXECUTE' if verify_scope else 'OBSERVE')
        if intent not in {'EXECUTE', 'OBSERVE', 'STOP'}:
            raise InvalidInput('Unknown original owner context intent')
        manifest, _ = self._manifest(pid, ident, ref)
        context = self.active.get(ident)
        state = self._state(pid, ident)
        if (not context or context.get("closing") or context["process"].returncode is not None or
                state["owner_status"] != "READY" or state.get('generation') != context['generation'] or
                state.get('original_session_id') != context.get('session_id') or
                generation is not None and context["generation"] != generation):
            raise Conflict("Exact original session owner/generation is not connected")
        binding = context.get('owner_binding')
        if binding:
            if (context['process'] is not binding['process'] or context['client'] is not binding['client'] or
                    context['session_id'] != binding['session_id'] or context['directory'] != binding['directory'] or
                    context['generation'] != binding['generation'] or
                    state.get('process_identity') != binding['process_identity'] or
                    type(binding['process_identity'].get('pid')) is not int or binding['process_identity']['pid'] <= 0 or
                    getattr(context['process'], 'pid', None) != binding['process_identity']['pid'] or
                    state.get('controller_identity') != self._identity):
                raise Conflict('Exact original private transport/process binding changed')
        elif getattr(context['process'], 'pid', None) is not None:
            raise Conflict('Exact original private transport/process binding is unavailable')
        if context["loop"] is not asyncio.get_running_loop():
            raise Conflict("Conversation controller event loop differs")
        if intent == 'STOP':
            if context.get('interrupt_barrier'):
                raise Conflict('Original stop is awaiting an idle observation; inspect the original operation without resending')
            # Abort only this captured private transport, even after expiry,
            # credential revocation, changed origins or workspace replacement.
            return manifest, context
        if datetime.fromisoformat(context["expires_at"]) <= datetime.now(timezone.utc):
            raise Conflict("Original owner authority expired; explicitly load a newly admitted generation")
        if intent == 'OBSERVE':
            if not self._lifecycle_current(manifest, context):
                context['lifecycle_scope_changed'] = True
                raise Conflict('Original owner provider, authorization, workspace or private configuration changed')
            return manifest, context
        self._editor_assert(manifest)
        self.context_authority.check(manifest,context)
        if context.get("interrupt_barrier"):
            raise Conflict("Original stop is awaiting an idle observation; inspect the original operation without resending")
        if not self._configuration_current(manifest):
            raise Conflict("Selected current configuration or workspace changed")
        if not allow_stale_authorization and is_subscription(context['profile']):
            from packages.adapters.opencode_account import authorization_epoch, subscription_reauth_required
            if (not context['profile'].authorization_epoch or
                    authorization_epoch(context['profile'].account_data_home)!=context['profile'].authorization_epoch):
                raise Conflict('Subscription authorization changed; explicitly load a newly admitted generation')
            if subscription_reauth_required(context['profile']):
                raise Conflict('模型连接需要重新登录。请检查模型连接，完成后再明确发送新请求；原请求不会自动重发。')
        if state.get("observation_error"):
            raise Conflict("Original observation is unavailable; no new command is admitted")
        self._preflight_origins(context["directory"])
        # Recheck enabled skill/MCP bytes at execution boundaries, without
        # hashing the already running CLI for every UI observation.
        if manifest.get('experience_validation_admission'):
            self.experience_validation.binding(manifest)
        elif manifest.get('native_review_admission') and self.native_reviews.capability_task_admission(manifest['native_review_admission']):
            self.native_reviews.binding(manifest, None, None)
        elif 'capability_scope' in manifest:
            current_scope(self.capabilities, manifest)
        else:
            current_binding = self.capabilities.native_binding_snapshot()
            if current_binding["sha256"] != manifest["request"]["capability_binding_sha256"]:
                raise Conflict("Selected original capability bytes changed")
        if 'WORKSPACE_WRITE' in manifest['request'].get('allowed_effects', []):
            from packages.adapters.opencode_change_review import verify_bundle, _regular
            admission = context.get('change_review')
            if (not admission or verify_bundle()['bundle_sha256'] != admission['bundle_sha256'] or
                    hashlib.sha256(_regular(Path(admission['config_path']), 524288)).hexdigest() != admission['config_sha256']):
                raise Conflict('The original change review plugin or exact scope changed')
        return manifest, context

    async def inspect(self, pid, ident):
        if ident not in self.active:
            return self.get(pid, ident)
        context = self.active[ident]
        try:
            manifest, current = self._context(pid, ident, intent='OBSERVE')
        except (Conflict, InvalidInput, NotFound, OSError) as error:
            # A cached view is not a fresh native observation. In particular,
            # do not contact a replacement directory, transport or generation.
            return {**self.get(pid, ident), 'observation_is_current': False,
                    'observation_error': {'error_type': type(error).__name__, 'scope': 'EXACT_OWNER_UNAVAILABLE'}}
        if current is not context:
            return self.get(pid, ident)
        try:
            return await self._inspect_current(pid, ident, manifest, context)
        except (httpx.HTTPError, Conflict, ValueError, KeyError, TypeError) as error:
            detail = {"error_type": type(error).__name__, **context.get("last_http_error", {})}
            state = self._state(pid, ident)
            if (self.active.get(ident) is not context or context.get("closing")
                    or state.get("generation") != context["generation"]):
                return self.get(pid, ident)
            if state["active_operation_id"] and self.operation(pid, ident, state["active_operation_id"])["status"] not in TERMINAL:
                self._finish(pid, ident, state["active_operation_id"], "UNKNOWN_OUTCOME",
                             reason="ORIGINAL_OBSERVATION_UNAVAILABLE_NO_POST_REPLAY", observation_error=detail)
            self._update(pid, ident, native_status="UNKNOWN", observation_error=detail,
                         native_messages_error=detail if detail.get("path", "").endswith("/message") else None)
            return self.get(pid, ident)

    async def _inspect_current(self, pid, ident, manifest, context):
        async with context["lock"]:
            if (self.active.get(ident) is not context or context.get("closing")
                    or self._state(pid, ident).get("generation") != context["generation"]):
                return self.get(pid, ident)
            # A status fetched while abort is still in flight cannot release
            # the input barrier, even if the HTTP response arrives before this
            # observation finishes processing.
            barrier = context.get("interrupt_barrier")
            can_release_interrupt = bool(barrier and barrier.get("http_finished"))
            command_epoch = context.get("native_command_epoch", 0)
            quiescent = not context.get("native_commands_inflight", 0)
            native = context["session_id"]
            session = await self._http(context, "GET", "/session/" + native)
            if session.get("id") != native:
                raise Conflict("Original observation has another session")
            if context.get('native_message'):
                messages = self._ordinary_messages(context)
            else:
                messages = await self._http(context, "GET", "/session/" + native + "/message", params={"limit": "200"})
            if any(m.get("info", {}).get("sessionID") != native for m in messages):
                raise Conflict("Original messages have another session")
            todo = await self._http(context, "GET", "/session/" + native + "/todo")
            children = await self._http(context, "GET", "/session/" + native + "/children")
            for child in children:
                if child.get("parentID") != native or child.get("directory") != session.get("directory"):
                    raise Conflict("Original child session is outside the owner")
                context["known_sessions"].add(child["id"])
            statuses = await self._http(context, "GET", "/session/status")
            questions = await self._http(context, "GET", "/question")
            permissions = await self._http(context, "GET", "/permission")
            child_messages = await self.child_controls.messages(pid,ident,context,statuses,[*questions,*permissions])
            delegated_messages = await self.child_controls.delegated.messages(pid,ident,context,statuses,[*questions,*permissions])
            permission_messages = await self._path_permission_messages(pid,ident,context,permissions,[*messages,*(child_messages or [])])
            pending_questions = [self._pending(context, item, "QUESTION", manifest)
                                 for item in questions if item.get("sessionID") in context["known_sessions"]]
            pending_permissions = [self._pending(context, item, "PERMISSION", manifest, messages=permission_messages)
                                   for item in permissions if item.get("sessionID") in context["known_sessions"]]
            if (self.active.get(ident) is not context or context.get("closing")
                    or self._state(pid, ident).get("generation") != context["generation"]):
                return self.get(pid, ident)
            loaded = self._observed_calls(self._state(pid, ident)["capability_load"], messages)
            persisted_messages = (self.native_reviews.public_messages(manifest, context, messages)
                if context.get('native_review', {}).get('kind') == 'CAPABILITY_TASK' else messages)
            if self.conversations is not None and not (manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')):
                persisted_messages = self.conversations.public_messages(manifest, context, persisted_messages)
            self._update(pid, ident, original_session=self._clean(context, session), messages=self._clean(context, persisted_messages),
                         todo=self._clean(context, todo), children=self._clean(context, children),
                         native_status=statuses.get(native, {"type": "idle"}).get("type", "UNKNOWN"),
                         pending_questions=pending_questions, pending_permissions=pending_permissions,
                         capability_load=loaded, observation_at=now(), messages_limit=200,
                         message_source=('ORIGINAL_COMMITTED_SSE_UPDATES' if context.get('native_message') else 'ORIGINAL_HTTP_GET'),
                         message_source_generation=context['generation'],
                         message_source_last_event_seq=(context['ordinary_message_snapshot']['last_event_seq']
                             if context.get('native_message') else None),
                         observation_error=None, native_messages_error=None)
            child_guidance_targets = self.child_controls.delegated.targets(pid,ident,context,messages)
            self._update(pid,ident,child_guidance_targets=child_guidance_targets)
            quiescent = (quiescent and not context.get("native_commands_inflight", 0)
                         and context.get("native_command_epoch", 0) == command_epoch)
            context["quiescent_observation"] = quiescent
            context["observed_command_epoch"] = command_epoch
            child_handled = await self.child_controls.observe(pid,ident,context,child_messages,statuses,
                quiescent=quiescent,barrier=barrier,can_release_interrupt=can_release_interrupt)
            if not child_handled:
                delegated_human_wait = self.child_controls.delegated.human_wait(pid,ident,context,messages,delegated_messages,statuses,quiescent=quiescent)
                if not delegated_human_wait:
                    self._observe_human_wait(pid, ident, context, messages, statuses, quiescent=quiescent)
                await reconcile_workflow(self, pid, ident, context, messages)
                delegated_waiting = self.child_controls.delegated.observe(pid,ident,context,delegated_messages,statuses,
                    quiescent=quiescent,barrier=barrier,can_release_interrupt=can_release_interrupt)
                if not delegated_waiting:
                    await self._settle(pid, ident, context, messages, statuses=statuses,
                        quiescent=quiescent, barrier=barrier, can_release_interrupt=can_release_interrupt)
                    self._recover_unknown(pid, ident, context, messages, statuses,
                                          quiescent=quiescent, barrier=barrier,
                                          can_release_interrupt=can_release_interrupt)
            if (can_release_interrupt and context.get("interrupt_barrier") is barrier
                    and self.active.get(ident) is context and not context.get("closing")
                    and context["generation"] == barrier["generation"]
                    and self._state(pid, ident)["native_status"] == "idle"
                    and self._state(pid, ident)["active_operation_id"] is None):
                context.pop("interrupt_barrier", None)
        return self.get(pid, ident)

    @staticmethod
    def _observed_calls(loaded, messages):
        value = json.loads(canonical(loaded))
        for message in messages:
            for part in message.get("parts", []):
                state = part.get("state", {})
                if part.get("type") != "tool" or state.get("status") != "completed":
                    continue
                items = []
                if part.get("tool") == "skill":
                    items = [item for item in value["skills"] if item["id"] == state.get("input", {}).get("name")]
                else:
                    prefixes = {ident: item.get("native_tool_prefix", original_mcp_identifier(ident) + "_")
                                for ident, item in value["mcp"].items()
                                if item.get("configured_in_original", item.get("loaded_in_original_session", False))}
                    matches = [ident for ident, prefix in prefixes.items() if part.get("tool", "").startswith(prefix)]
                    if matches:
                        source = exact_tool_owner(part["tool"], prefixes)
                        items = [value["mcp"][source]]
                for item in items:
                    item.update(invoked=True, loaded_in_original_session=True,
                        last_actual_invocation={"native_message_id": message["info"]["id"], "native_part_id": part["id"],
                            "input_sha256": digest(state.get("input")), "output_sha256": digest(state.get("output"))},
                        qualified=False)
                    if part.get("tool") == "skill":
                        item["load_status"] = "ORIGINAL_SKILL_TOOL_COMPLETED"
        return value

    async def _native_lineage(self,context,native,*,get=None):
        started=asyncio.get_running_loop().time()
        async def bounded_get(route):
            remaining=2.5-(asyncio.get_running_loop().time()-started)
            if remaining<=0:
                raise Conflict('Original child lineage deadline reached')
            return await self._http(context,'GET',route,timeout=min(.5,remaining))
        fetch=get or bounded_get
        chain=[];seen={context['session_id']};current=native
        while current!=context['session_id']:
            if (not isinstance(current,str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}',current)
                    or current in seen or len(chain)>=12):
                raise Conflict('Original child lineage is cyclic or excessive')
            seen.add(current);node=await fetch('/session/'+current);parent=node.get('parentID')
            if (node.get('id')!=current or not isinstance(parent,str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}',parent)
                    or node.get('directory')!=context['directory']):
                raise Conflict('Original child lineage belongs to another workspace')
            chain.append(current);current=parent
        return chain

    async def _path_permission_messages(self,pid,ident,context,permissions,messages):
        """Bounded GET-only evidence for descendant path prompts on this owner.

        The original tool before-hook records the file versions. This only
        checks SDK lineage and the exact message named by a pending request.
        """
        state=self._state(pid,ident)
        scope={'generation':context['generation'],'root':context['session_id'],
            'parent':state.get('active_operation_id'),'entries':{}}
        context['path_permission_lineages']=scope
        result=list(messages);found={(row.get('info',{}).get('sessionID'),row.get('info',{}).get('id')) for row in messages}
        started=asyncio.get_running_loop().time();reads=0
        async def get(route):
            nonlocal reads
            remaining=2.5-(asyncio.get_running_loop().time()-started)
            if remaining<=0 or reads>=104:
                raise Conflict('Descendant permission metadata read budget reached')
            reads+=1
            return await self._http(context,'GET',route,timeout=min(.5,remaining))
        for item in permissions[:8]:
            native=item.get('sessionID');mid=item.get('tool',{}).get('messageID')
            if (item.get('permission') not in {'read','edit','glob','grep'} or native==context['session_id'] or
                    native not in context['known_sessions'] or not isinstance(native,str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}',native)
                    or not isinstance(mid,str) or not re.fullmatch(r'msg_[A-Za-z0-9_-]{1,100}',mid)):
                continue
            try:
                if native not in scope['entries']:
                    scope['entries'][native]=await self._native_lineage(context,native,get=get)
                if (native,mid) not in found:
                    message=await get('/session/'+native+'/message/'+mid)
                    info=message.get('info',{})
                    if (info.get('id')!=mid or info.get('sessionID')!=native or info.get('role')!='assistant'
                            or not isinstance(message.get('parts'),list) or len(message['parts'])>256
                            or any(part.get('sessionID')!=native or part.get('messageID')!=mid for part in message['parts'])):
                        raise Conflict('Descendant permission tool message identity differs')
                    result.append(message);found.add((native,mid))
            except Exception:
                scope['entries'].pop(native,None)
        return result

    def _permission_choices(self, item, manifest, *, message=None):
        action, patterns = item.get("permission"), item.get("patterns", [])
        request = manifest["request"]
        if not patterns or not isinstance(patterns, list) or any(not isinstance(p, str) for p in patterns):
            return ["reject"]
        allowed = False
        if action in {"read", "edit", "glob", "grep"}:
            try:
                from packages.adapters.opencode_permission_paths import permission_paths, check_read_target, check_search_target
                ref = request.get('workspace_ref')
                context = self.active.get(manifest['id'])
                if ref is None or not context or context.get('closing'):
                    return ['reject']
                state = self._state(manifest['project_id'], manifest['id'])
                if state.get('generation') != context.get('generation') or state.get('original_session_id') != context.get('session_id'):
                    return ['reject']
                if item.get('sessionID') != context.get('session_id'):
                    parent = self.operation(manifest['project_id'],manifest['id'],state['active_operation_id']) if state.get('active_operation_id') else None
                    if (not parent or parent.get('kind') not in {'MESSAGE','CHILD_MESSAGE'} or parent.get('status')!='ACCEPTED'
                            or parent.get('generation')!=context['generation'] or manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')):
                        return ['reject']
                    active_native=self._execution_target(manifest['project_id'],manifest['id'],context,parent)
                    evidence=context.get('path_permission_lineages',{})
                    chain=evidence.get('entries',{}).get(item.get('sessionID'),[])
                    if (evidence.get('generation')!=context['generation'] or evidence.get('root')!=context['session_id']
                            or evidence.get('parent')!=state['active_operation_id'] or not chain
                            or active_native!=context['session_id'] and active_native not in chain):
                        return ['reject']
                    if action=='edit':
                        from packages.adapters.opencode_change_review import read_child_lineage
                        read_child_lineage(context['change_review'],item['sessionID'],ancestor=active_native)
                if message is None:
                    matches = [m for m in state.get('messages', [])
                               if m.get('info', {}).get('id') == item.get('tool', {}).get('messageID')]
                    if len(matches) != 1:
                        return ['reject']
                    message = matches[0]
                workspace = self.workspaces.get(manifest['project_id'], ref['id'], ref['revision'])
                if workspace['binding_sha256'] != ref['binding_sha256'] or workspace['root_identity'] != manifest['directory_identity']:
                    return ['reject']
                paths = permission_paths(item, message, workspace['root'])
                if action == 'edit':
                    allowed = bool('WORKSPACE_WRITE' in request.get('allowed_effects', []) and context.get('change_review') and
                        all(any(g == '*' or p == g or (g.endswith('/') and p.startswith(g))
                            for g in workspace['allowed_write_paths']) for p in paths))
                    # Remembering a write would skip the next before-ledger guard.
                    return ['once', 'reject'] if allowed else ['reject']
                for path in paths:
                    if action in {'glob', 'grep'}:
                        check_search_target(self.workspaces, workspace, path, action)
                    else:
                        check_read_target(self.workspaces, workspace, path)
                if action in {'glob', 'grep'}:
                    return ['once', 'reject']
                allowed = True
            except (Conflict, InvalidInput, NotFound, TypeError, KeyError, ValueError, AttributeError, OSError):
                allowed = False
        elif action == "task":
            context = self.active.get(manifest['id'])
            admitted = {row['alias'] for row in context.get('workflow', {}).get('aliases', [])} if context and not context.get('closing') else set()
            allowed = all(p == "explore" or p in admitted for p in patterns)
        elif action == "bash":
            allowed = all(p in request["command_allowlist"] for p in patterns)
        elif action in request["mcp_tool_allowlist"]:
            allowed = True
        # There is no independent list tool in the pinned runtime. Directory
        # listing uses read, with the same exact native path evidence.
        if not allowed:
            return ["reject"]
        remember = item.get("always", [])
        exact_remember = bool(remember) and set(remember) <= set(patterns)
        return ["once", "always", "reject"] if exact_remember or action in request["mcp_tool_allowlist"] else ["once", "reject"]

    def _pending(self, context, item, kind, manifest, *, messages=None):
        message = None
        if messages is not None:
            matches = [m for m in messages if m.get('info', {}).get('id') == item.get('tool', {}).get('messageID')]
            message = matches[0] if len(matches) == 1 else {}
        result = {"request_ref": {"id": item["id"], "sha256": digest(item), "generation": context["generation"]},
                "native_request": self._clean(context, item), "kind": kind,
                "allowed_choices": ["reply", "reject"] if kind == "QUESTION" else self._permission_choices(item, manifest, message=message),
                "reply_is_execution_authorization": kind == "PERMISSION"}
        if (kind == 'PERMISSION' and manifest.get('native_review_admission')
                and self.native_reviews.capability_task_admission(manifest['native_review_admission'])):
            result['capability_call'] = self.native_reviews.permission_call(manifest, context, item, messages)
            if not result['capability_call'] or result['capability_call']['coverage'] == 'UNAVAILABLE':
                result['allowed_choices'] = ['reject']
        return result

    def _claim(self, pid, ident, operation_id, kind, request):
        _manifest, session_ref = self._manifest(pid, ident)
        if _manifest.get('experience_validation_admission') and kind not in {'INTERRUPT','DISCONNECT'}:
            if self.experience_validation is None:raise Conflict('Original Skill validation coordinator is unavailable')
            self.experience_validation.authorize_claim(_manifest,kind,request)
        path = self._path(pid, ident, "operations", digest(operation_id) + ".json")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if path.exists():
                value = self._read(path)
                if value["kind"] != kind or value["request_sha256"] != digest(request):
                    raise Conflict("IDEMPOTENCY_CONFLICT: session operation binds another input")
                return value, False
            from .work_retests import admit_claim
            work_retest = admit_claim(connection,pid,ident,operation_id,request,kind=kind) if kind in {"MESSAGE","GUIDANCE"} else None
            value = {"id": operation_id, "kind": kind, "request_sha256": digest(request), "request": request,
                     "status": "RECORDED_NOT_DISPATCHED", "dispatch_status": "RECORDED_NOT_DISPATCHED",
                     "operation_ref": {"id": operation_id, "request_sha256": digest(request)}, "session_ref": session_ref,
                     "created_at": now(), "automatic_replay": False}
            if work_retest is not None:value['work_retest']=work_retest
            self._write(path, value)
            return value, True

    def _finish(self, pid, ident, operation_id, status, **details):
        path = self._path(pid, ident, "operations", digest(operation_id) + ".json")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            value = self._read(path)
            changed = value.get("status") != status
            value.update(status=status, dispatch_status=status, updated_at=now(), **details)
            self._write(path, value, replace=True)
        if changed:
            self._notify_observer()
        return value

    def operation(self, pid, ident, operation_id):
        self._manifest(pid, ident)
        path = self._path(pid, ident, "operations", digest(operation_id) + ".json")
        if not path.exists():
            raise NotFound("Original operation receipt does not exist")
        return self._read(path)

    def _authorize_review_before_claim(self, manifest, body, *, guidance=False):
        """Persist a v2 rejection proven by this synchronous pre-claim guard.

        Missing receipts elsewhere are not evidence that a POST never happened.
        An existing exact operation is retained even if admission now rejects it.
        """
        authorize = self.native_reviews.authorize_guidance if guidance else self.native_reviews.authorize_dispatch
        try:
            authorize(manifest, body)
        except (Conflict, InvalidInput, NotFound) as error:
            if self.native_reviews.capability_task_admission(manifest['native_review_admission']):
                _old, created = self._claim(manifest['project_id'], manifest['id'], body.operation_id,
                                            'GUIDANCE' if guidance else 'MESSAGE', operation_request(body))
                if created:
                    self._finish(manifest['project_id'], manifest['id'], body.operation_id, 'FAILED',
                        rejection_phase='BEFORE_ORIGINAL_CLAIM', native_post_attempted=False,
                        execution_started=False, generation=body.generation, error_type=type(error).__name__)
            raise

    async def send(self, pid, ident, body):
        body = SendMessage.model_validate(body)
        manifest, _ = self._manifest(pid, ident, body.session_ref.model_dump())
        if body.agent == WORKFLOW_AGENT and (manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')):
            raise Conflict('The private native input protocol does not admit the public Workflow mode')
        if (body.conversation_ref is not None or body.input_attachments or body.file_input or body.original_work_ref is not None) and (manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')):
            raise Conflict('Private native tasks use their existing frozen input protocol')
        if body.original_work_ref is not None and self.conversations is None:
            raise Conflict('Original work context authority is unavailable; input retained')
        if manifest.get('native_review_admission'):
            self._authorize_review_before_claim(manifest, body)
        if manifest.get('native_message_admission'):
            self.native_messages.authorize_dispatch(manifest, body)
        request = operation_request(body)
        old, created = self._claim(pid, ident, body.operation_id, "MESSAGE", request)
        if not created:
            return old
        context = None
        try:
            await self.editor_captures.broker.admit(manifest,generation=body.generation,
                operation_id=body.operation_id,kind='MESSAGE')
            manifest, context = self._context(pid, ident, body.session_ref.model_dump(), body.generation)
            async with context["lock"]:
                # A stop may have established its barrier while this send was
                # waiting for the owner lock.  Do not begin a new native run.
                _, current = self._context(pid, ident, body.session_ref.model_dump(), body.generation)
                if current is not context:
                    raise Conflict("Original message owner changed while waiting")
                if body.agent == WORKFLOW_AGENT and not workflow_mode_admitted(context):
                    raise Conflict('Workflow mode was not observed in this original owner; input is retained')
                native_id = _native_message_id()
                with self.store.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    state = self._state(pid, ident)
                    self._editor_assert(manifest,connection=connection)
                    from .work_retests import admit_dispatch
                    admit_dispatch(self,connection,pid,ident,manifest,body,state)
                    if state["active_operation_id"]:
                        raise Conflict("Another original message is active; poll, reply or interrupt it first")
                    if is_subscription(context["profile"]) or manifest.get("native_message_admission"):
                        if state.get("native_turns_started", 0) >= manifest["request"]["max_calls"]:
                            raise Conflict("Original user-turn allowance is exhausted; native RPC count is not metered")
                        state["native_turns_started"] = state.get("native_turns_started", 0) + 1
                    if not is_subscription(context["profile"]) and state["model_calls_reserved"] >= manifest["request"]["max_calls"]:
                        raise Conflict("Original model-call allowance is exhausted")
                    state["active_operation_id"] = body.operation_id
                    state["native_status"] = "DISPATCHING"
                    if not (manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')):
                        state['work_mode_message'] = {'operation_id': body.operation_id,
                            'agent': body.agent, 'generation': body.generation,
                            'native_message_id': native_id,
                            'policy_sha256': WORKFLOW_POLICY_SHA256 if body.agent == WORKFLOW_AGENT else None}
                    self._write(self._path(pid, ident, "state.json"), state, replace=True)
                self._finish(pid, ident, body.operation_id, "DISPATCHING", native_message_id=native_id,
                             generation=body.generation, original_session_id=context["session_id"],
                             work_mode_definition=context.get('workflow_mode') if body.agent == WORKFLOW_AGENT else None,
                             native_dispatch_acknowledged=False,native_post_attempted=False,
                             dispatch_at=now(), deadline=min(datetime.now(timezone.utc) + timedelta(seconds=body.timeout_seconds),
                                 datetime.fromisoformat(context["expires_at"]) if manifest.get("native_message_admission") else datetime.max.replace(tzinfo=timezone.utc)).isoformat())
                from .resource_retention import dispatch_scope
                with dispatch_scope(self,manifest,context,self.operation(pid,ident,body.operation_id)):
                    if self.conversations is not None and not (manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')):
                        self._finish(pid, ident, body.operation_id, 'DISPATCHING', native_post_attempted=False)
                    if manifest.get('native_review_admission'):
                        self.native_reviews.before_dispatch(manifest, context, self.operation(pid, ident, body.operation_id))
                    if self.conversations is not None:
                        self.conversations.prepare(manifest, context, self.operation(pid, ident, body.operation_id))
                    elif body.conversation_ref is not None or body.input_attachments or body.file_input:
                        raise Conflict('Public conversation inputs are unavailable; no model was called')
                    # Persist intent before the sole POST. A timeout after dispatch is
                    # UNKNOWN_OUTCOME, not permission to resend even the same body.
                    payload = {"messageID": native_id, "agent": body.agent,
                        "model": {"providerID": context["profile"].provider_id, "modelID": context["profile"].model_id},
                        "parts": [{"type": "text", "text": body.raw_text}]}
                    if manifest.get('native_message_admission'):
                        native_format = self.native_messages.output_format(manifest)
                        if native_format is not None:
                            payload['format'] = native_format
                        self.native_messages.before_dispatch(manifest, context, self.operation(pid, ident, body.operation_id))
                    if manifest.get('native_review_admission'):
                        payload = self.native_reviews.dispatch_payload(manifest, context, self.operation(pid, ident, body.operation_id), payload)
                        if self.native_reviews.capability_task_admission(manifest['native_review_admission']):
                            self._finish(pid, ident, body.operation_id, 'DISPATCHING', native_post_attempted=True)
                    if self.conversations is not None:
                        payload = self.conversations.dispatch_payload(manifest, context, self.operation(pid, ident, body.operation_id), payload)
                        if not (manifest.get('native_review_admission') or manifest.get('native_message_admission') or manifest.get('experience_validation_admission')):
                            self._finish(pid, ident, body.operation_id, 'DISPATCHING', native_post_attempted=True)
                    self._finish(pid,ident,body.operation_id,"DISPATCHING",native_post_attempted=True)
                    await self._http(context, "POST", "/session/" + context["session_id"] + "/prompt_async", payload, expected=204)
                    latest = self._state(pid, ident)
                    if (self.active.get(ident) is not context or context.get("closing")
                            or latest.get("generation") != body.generation or latest.get("active_operation_id") != body.operation_id):
                        raise Conflict("Original dispatch owner changed before its acknowledgement was recorded")
                    return self._finish(pid, ident, body.operation_id, "ACCEPTED", native_message_id=native_id,
                                        generation=body.generation, original_session_id=context["session_id"],
                                        native_dispatch_acknowledged=True)
        except BaseException as error:
            current = self.operation(pid, ident, body.operation_id)
            if current["status"] in TERMINAL:
                raise  # A late dispatch result never rewrites an archived unknown/cancel.
            v2_not_sent = bool(manifest.get('native_review_admission') and self.native_reviews.capability_task_admission(manifest['native_review_admission'])
                               and current.get('native_post_attempted') is not True)
            status = "UNKNOWN_OUTCOME" if (current["status"] == "DISPATCHING" and not v2_not_sent
                and current.get('native_post_attempted') is not False) else "FAILED"
            self._finish(pid, ident, body.operation_id, status, error_type=type(error).__name__)
            state = self._state(pid, ident)
            if (status == "FAILED" and state["active_operation_id"] == body.operation_id
                    and state.get("generation") == body.generation and self.active.get(ident) is context):
                self._update(pid, ident, active_operation_id=None)
            raise

    def _observe_subscription_response(self, pid, ident, context, operation, messages, parent_operation=None):
        if not is_subscription(context.get('profile')) or operation.get('status') not in {'RETURNED', 'FAILED'}:return
        try:
            from packages.adapters.opencode_account import record_subscription_response, record_subscription_rejection
            manifest,ref=self._manifest(pid,ident)
            if self.operation(pid,ident,operation['id'])!=operation:
                raise Conflict('Subscription original operation receipt changed')
            if (self.active.get(ident) is not context or context.get('closing')
                    or operation.get('kind')!='MESSAGE' or operation.get('session_ref')!=ref
                    or operation.get('generation')!=context['generation']
                    or operation.get('original_session_id')!=context['session_id']
                    or self._state(pid,ident).get('generation')!=context['generation']
                    or manifest['request']['provider_profile_ref']!=context['profile'].reference):
                raise Conflict('Subscription response owner changed')
            parent=self.operation(pid,ident,parent_operation) if isinstance(parent_operation,str) else parent_operation or operation
            if (parent.get('session_ref')!=ref or parent.get('generation')!=context['generation']
                    or parent.get('original_session_id')!=context['session_id']
                    or parent_operation and (parent.get('kind')!='GUIDANCE'
                        or parent.get('original_active_operation_id')!=operation['id']
                        or parent.get('status')!=operation['status'])):
                raise Conflict('Subscription response user operation changed')
            source={'kind':'ORIGINAL_SESSION','project_id':pid,'object_ref':ref,'operation_id':operation['id'],
                'request_sha256':operation['request_sha256'],'generation':context['generation'],
                'deadline_ms':datetime.fromisoformat(operation['deadline']).timestamp()*1000,
                'original_session_id':context['session_id'],'status':operation['status'],
                'idle_observed':self._state(pid,ident)['native_status']=='idle',
                'cancel_requested':bool(context.get('interrupt_barrier') or operation.get('user_reject_acknowledged')),
                'native_user_messages':{parent['native_message_id']:hashlib.sha256((
                    ''.join(parent['host_payload']['text_parts']) if parent.get('host_payload') else parent['request']['raw_text']).encode()).hexdigest()},
                'native_assistant_ids':operation['native_assistant_ids']}
            observed=(record_subscription_response(context['profile'],source,messages) if operation['status']=='RETURNED'
                      else record_subscription_rejection(context['profile'],source,parent,messages))
        except Exception as error:
            observed={'status':'UNAVAILABLE','error_type':type(error).__name__,'original_operation_unchanged':True}
        # This is a separate read-only qualification projection. Its persistence
        # failure must never turn an already returned operation into UNKNOWN.
        try:self._update(pid,ident,subscription_verification=observed)
        except Exception:pass

    def _cohort_records(self, pid, ident, context, state, operation):
        """Only our acknowledged root dispatch and its exact user guidance."""
        _manifest, ref = self._manifest(pid, ident)
        ids = state.get("guidance_operation_ids", []) if state.get("guidance_cohort_operation_id") == operation["id"] else []
        if not isinstance(ids, list) or len(ids) > 32 or len(set(ids)) != len(ids):
            return []
        records = [operation] + [self.operation(pid, ident, key) for key in ids]
        for index, record in enumerate(records):
            if (record.get("session_ref") != ref or record.get("generation") != context["generation"]
                    or record.get("original_session_id") != context["session_id"]
                    or record.get("native_dispatch_acknowledged") is not True
                    or not record.get("native_message_id")
                    or record.get("kind") != ("GUIDANCE" if index else "MESSAGE")
                    or index and record.get("original_active_operation_id") != operation["id"]):
                return []
        return records

    @staticmethod
    def _cohort_users_observed(context, records, messages):
        if not records or any(item.get("info", {}).get("sessionID") != context["session_id"] for item in messages):
            return False
        users = [item for item in messages if item.get("info", {}).get("role") == "user"]
        if not users or users[-1]["info"].get("id") != records[-1]["native_message_id"]:
            return False  # A different native turn is never this operation's termination proof.
        for record in records:
            exact = [item for item in users if item["info"].get("id") == record["native_message_id"]]
            if len(exact) != 1:
                return False
            if record.get('host_payload'):
                from .native_review import NativeReviews
                if NativeReviews.observed_payload(record, exact[0]) is not True:
                    return False
            elif "".join(part.get("text", "") for part in exact[0].get("parts", [])
                    if part.get("type") == "text" and not part.get("synthetic")) != record["request"]["raw_text"]:
                return False
        return True

    def _human_wait_refs(self, pid, ident, context, state, operation, messages, statuses):
        # The pinned original ask awaits a Deferred and names its assistant
        # message + call. A child request or an unrelated pending request alone
        # proves nothing about whether this root turn is waiting for the user.
        if operation["status"] != "ACCEPTED" or context.get("interrupt_barrier"):
            return []
        records = self._cohort_records(pid, ident, context, state, operation)
        if (not self._cohort_users_observed(context, records, messages)
                or any(record["status"] == "UNKNOWN_OUTCOME" for record in records)
                or statuses.get(context["session_id"], {}).get("type") != "busy"
                or any(statuses.get(key, {"type": "idle"}).get("type") != "idle"
                       for key in context["known_sessions"] if key != context["session_id"])):
            return []
        requests = [(kind, item) for kind, name in (("QUESTION", "pending_questions"), ("PERMISSION", "pending_permissions"))
                    for item in state.get(name, [])]
        if not 1 <= len(requests) <= 64:
            return []
        waiting = {}
        for kind, item in requests:
            native = item["native_request"]
            tool = native.get("tool") or {}
            key = (tool.get("messageID"), tool.get("callID"))
            if native.get("sessionID") != context["session_id"] or not all(key) or key in waiting:
                return []
            waiting[key] = {"kind": kind, "request_ref": item["request_ref"]}
        parents = {record["native_message_id"] for record in records}
        active = set()
        for message in messages:
            info = message["info"]
            if info.get("role") != "assistant" or info.get("time", {}).get("completed"):
                continue
            if info.get("parentID") not in parents:
                return []
            for part in message.get("parts", []):
                if part.get("type") == "tool" and part.get("state", {}).get("status") in {"pending", "running"}:
                    key = (info.get("id"), part.get("callID"))
                    if (part.get("sessionID") != context["session_id"] or part.get("messageID") != info.get("id")
                            or part.get("state", {}).get("status") != "running" or key not in waiting or key in active):
                        return []  # A parallel unblocked tool still consumes execution time.
                    active.add(key)
                elif part.get("type") in {"text", "reasoning"} and part.get("time", {}).get("start") and not part["time"].get("end"):
                    return []
        return [waiting[key] for key in sorted(active)] if active and active == set(waiting) else []

    def _observe_human_wait(self, pid, ident, context, messages, statuses, *, quiescent):
        state = self._state(pid, ident)
        if (not quiescent or self.active.get(ident) is not context or context.get("closing")
                or state.get("generation") != context["generation"] or not state.get("active_operation_id")):
            return
        operation = self.operation(pid, ident, state["active_operation_id"])
        if operation["status"] != "ACCEPTED":
            return  # In particular, observing a wait must never rewrite historical UNKNOWN.
        refs = self._human_wait_refs(pid, ident, context, state, operation, messages, statuses)
        previous = operation.get("human_wait", {})
        current_time = datetime.now(timezone.utc)
        deadline = datetime.fromisoformat(operation["deadline"])
        was_waiting = previous.get("status") == "WAITING_FOR_USER"
        elapsed = max(0., (current_time - datetime.fromisoformat(previous["since"])).total_seconds()) if was_waiting else 0.
        # Explicit guidance may already have reset the run deadline. Do not add
        # the same earlier human interval to that new user-authorized budget.
        same_deadline = previous.get("deadline_before_wait") == operation["deadline"]
        if refs and (was_waiting or deadline > current_time):
            if was_waiting and same_deadline and previous.get("requests") == refs:
                return
            value = {"status": "WAITING_FOR_USER", "since": previous["since"] if was_waiting and same_deadline else current_time.isoformat(),
                     "requests": refs, "deadline_before_wait": operation["deadline"],
                     "completed_wait_seconds": round(previous.get("completed_wait_seconds", 0.) + (elapsed if was_waiting and not same_deadline else 0.), 6),
                     "coverage": "EXACT_ROOT_TOOL_REQUESTS_NO_PARALLEL_WORK", "owner_ttl_extended": False}
            self._finish(pid, ident, operation["id"], operation["status"], human_wait=value)
        elif was_waiting:
            resumed = min(datetime.fromisoformat(context["expires_at"]), deadline + timedelta(seconds=
                elapsed if same_deadline and not context.get('native_message_absolute_deadline') else 0.))
            self._finish(pid, ident, operation["id"], operation["status"], deadline=resumed.isoformat(),
                human_wait={"status": "RUNNING", "completed_wait_seconds": round(previous.get("completed_wait_seconds", 0.) + elapsed, 6),
                            "resumed_at": current_time.isoformat(), "owner_ttl_extended": False})

    def _recover_unknown(self, pid, ident, context, messages, statuses, *, quiescent, barrier, can_release_interrupt):
        """Archive separate termination evidence; never relabel UNKNOWN success.

        A missing 204, an unobserved guidance turn or idle alone remains blocked.
        That owner must be explicitly disconnected before a new generation can
        be loaded. No observation here resends, aborts or starts a model.
        """
        state = self._state(pid, ident)
        operation_id = state.get("active_operation_id")
        if (not operation_id or not quiescent or self.active.get(ident) is not context or context.get("closing")
                or state.get("generation") != context["generation"]
                or state.get("original_session_id") != context["session_id"]
                or datetime.fromisoformat(context["expires_at"]) <= datetime.now(timezone.utc)
                or context.get("native_commands_inflight", 0)
                or context.get("observed_command_epoch") != context.get("native_command_epoch", 0)
                or state.get("pending_questions") or state.get("pending_permissions")
                or any(statuses.get(key, {"type": "idle"}).get("type") != "idle" for key in context["known_sessions"])):
            return
        operation = self.operation(pid, ident, operation_id)
        if operation["status"] != "UNKNOWN_OUTCOME":
            return
        deadline_stop = self._deadline_stop_evidence(pid, ident, context, operation)
        if deadline_stop is None:
            return  # An earlier unacknowledged abort may still stop a new turn.
        records = self._cohort_records(pid, ident, context, state, operation)
        if not self._cohort_users_observed(context, records, messages):
            return
        # No unfinished native assistant/tool may be concealed by a root idle
        # response. Also require each admitted dispatch to have actually entered
        # the original message history, not just an unrelated final response.
        assistants = [item for item in messages if item["info"].get("role") == "assistant"]
        if any(not item["info"].get("time", {}).get("completed") or
               any(part.get("type") == "tool" and part.get("state", {}).get("status") in {"pending", "running"}
                   for part in item.get("parts", [])) for item in assistants):
            return
        if any(not any(item["info"].get("parentID") == record["native_message_id"] for item in assistants) for record in records):
            return
        finals = [item for item in assistants if item["info"].get("parentID") == records[-1]["native_message_id"] and
                  (item["info"].get("finish") == "stop" or item["info"].get("error"))]
        if not finals:
            return
        stop = None
        if barrier is not None:
            if (not can_release_interrupt or context.get("interrupt_barrier") is not barrier
                    or barrier.get("generation") != context["generation"]
                    or barrier.get("target_operation_id") != operation_id):
                return
            stop = self.operation(pid, ident, barrier["operation_id"])
            if (stop.get("kind") != "INTERRUPT" or stop.get("status") != "RETURNED"
                    or stop.get("original_abort_acknowledged") is not True
                    or stop.get("generation") != context["generation"] or stop.get("target_operation_id") != operation_id):
                return
        manifest, ref = self._manifest(pid, ident)
        if not self._configuration_current(manifest):
            return
        recovery = {"schema": "eeas.agent-session-recovery.v1", "status": "TERMINATION_OBSERVED_NEW_INPUT_ALLOWED",
                    "project_id": pid, "session_ref": ref, "generation": context["generation"],
                    "original_session_id": context["session_id"], "original_operation_id": operation_id,
                    "original_operation_sha256": digest(operation), "original_status": "UNKNOWN_OUTCOME",
                    "native_message_ids": [record["native_message_id"] for record in records],
                    "native_assistant_ids": [item["info"]["id"] for item in finals],
                    "messages_sha256": digest(messages), "observed_at": state["observation_at"],
                    "dispatches_acknowledged": True, "no_native_commands_inflight": True,
                    "stop_operation_id": stop["id"] if stop else None, "stop_receipt_sha256": digest(stop) if stop else None,
                    "deadline_stop": deadline_stop or None,
                    "original_operation_unchanged": True, "automatic_replay": False, "semantic_acceptance": False,
                    "external_actions_rolled_back": False,
                    "coverage": "EXACT_ROOT_TURNS_AND_KNOWN_CHILD_STATUSES_NOT_EXTERNAL_JOB_COMPLETION"}
        path = self._path(pid, ident, "recoveries", operation_id + ".json")
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = self._state(pid, ident)
            if (self.active.get(ident) is not context or context.get("closing")
                    or latest.get("generation") != context["generation"] or latest.get("active_operation_id") != operation_id
                    or self.operation(pid, ident, operation_id) != operation):
                return
            if path.exists():
                saved = self._read(path)
                if any(saved.get(key) != recovery[key] for key in ("session_ref", "generation", "original_session_id", "original_operation_sha256")):
                    raise Conflict("Original unknown recovery evidence changed")
                recovery = saved
            else:
                self._write(path, recovery)
            latest.update(active_operation_id=None, last_operation_recovery={"operation_id": operation_id,
                "generation": context["generation"], "status": recovery["status"], "sha256": digest(recovery)}, updated_at=now())
            self._write(self._path(pid, ident, "state.json"), latest, replace=True)
        self._event(pid, ident, "ORIGINAL_UNKNOWN_TERMINATION_OBSERVED", latest["last_operation_recovery"])
        self._notify_observer()

    def _execution_target(self,pid,ident,context,operation):
        return self.child_controls.target(pid,ident,context,operation) if operation.get('kind')=='CHILD_MESSAGE' else context['session_id']

    def _deadline_stop_evidence(self, pid, ident, context, operation):
        key = digest(operation["id"]) + ".json"
        intent_path = self._path(pid, ident, "deadline-stops", key)
        result_path = self._path(pid, ident, "deadline-stop-results", key)
        if operation.get("reason") != "MESSAGE_DEADLINE_ABORT_REQUESTED" and not intent_path.exists():
            return {}
        if not intent_path.exists() or not result_path.exists():
            return None
        intent, result = self._read(intent_path), self._read(result_path)
        if (intent.get("schema") != "eeas.agent-session-deadline-stop.v1"
                or intent.get("dispatch_attempted") is not True or intent.get("native_abort_acknowledged") is not False
                or intent.get("parent_operation_id") != operation["id"]
                or intent.get("parent_operation_sha256") != digest(operation)
                or intent.get("session_ref") != operation.get("session_ref")
                or intent.get("generation") != context["generation"]
                or intent.get("original_session_id") != self._execution_target(pid,ident,context,operation)
                or result.get("schema") != "eeas.agent-session-deadline-stop-result.v1"
                or result.get("intent_sha256") != digest(intent)
                or result.get("status") != "ACKNOWLEDGED" or result.get("native_abort_acknowledged") is not True):
            return None
        return {"intent_sha256": digest(intent), "result_sha256": digest(result)}

    async def _deadline_abort(self, pid, ident, context, operation):
        # Archive the parent UNKNOWN before dispatch. The independently saved
        # original stop result never rewrites that history or permits replay.
        target = self._execution_target(pid,ident,context,operation)
        unknown = self._finish(pid, ident, operation["id"], "UNKNOWN_OUTCOME", reason="MESSAGE_DEADLINE_ABORT_REQUESTED")
        key = digest(operation["id"]) + ".json"
        intent = {"schema": "eeas.agent-session-deadline-stop.v1", "status": "UNKNOWN_OUTCOME",
                  "dispatch_attempted": True, "native_abort_acknowledged": False,
                  "parent_operation_id": operation["id"], "parent_operation_sha256": digest(unknown),
                  "session_ref": unknown["session_ref"], "generation": context["generation"],
                  "original_session_id": target, "attempted_at": now(), "automatic_replay": False}
        self._write(self._path(pid, ident, "deadline-stops", key), intent)
        result_path = self._path(pid, ident, "deadline-stop-results", key)

        def record(acknowledged, error=None):
            self._write(result_path, {"schema": "eeas.agent-session-deadline-stop-result.v1",
                "intent_sha256": digest(intent), "status": "ACKNOWLEDGED" if acknowledged else "UNKNOWN_OUTCOME",
                "native_abort_acknowledged": acknowledged, "error_type": error, "observed_at": now()})

        try:
            result = await self._http(context, "POST", "/session/" + target + "/abort")
        except BaseException as error:
            try:
                record(False, type(error).__name__)
            except Exception:
                pass  # The immutable intent without an ACK already fails closed.
            if not isinstance(error, Exception):
                raise  # Preserve owner cancellation and interpreter shutdown.
            # Do not end the only owner TTL monitor on an unknown stop ACK.
            # It can keep observing/expire this owner, but can never retry this
            # POST: the parent was already archived as terminal UNKNOWN above.
            return
        record(result is True)

    def _settle_interrupted_cohort(self, pid, ident, context, state, operation, messages, *,
                                  statuses, quiescent, barrier, can_release_interrupt):
        """An admitted guidance user need not receive a reply after explicit abort.

        This is termination evidence only. Preserve UNKNOWN and external effects;
        ACK alone, cached idle, unfinished tools or an unrelated abort prove nothing.
        """
        reason = 'EXPLICIT_NATIVE_ABORT_OF_ADMITTED_GUIDANCE_COHORT'
        recovering = operation.get('status') == 'CANCELLED' and operation.get('reason') == reason
        if (not (operation.get('status') == 'ACCEPTED' or recovering) or not quiescent or not can_release_interrupt
                or not barrier or context.get('interrupt_barrier') is not barrier
                or barrier.get('http_finished') is not True
                or barrier.get('generation') != context['generation']
                or barrier.get('target_operation_id') != operation['id']
                or state.get('original_session_id') != context['session_id']
                or state.get('native_status') != 'idle' or not isinstance(statuses, dict)
                or context.get('native_commands_inflight', 0)
                or context.get('observed_command_epoch') != context.get('native_command_epoch', 0)):
            return False
        known = context.get('known_sessions', set())
        if context['session_id'] not in known or any(
                not isinstance(statuses.get(key, {'type': 'idle'}), dict)
                or statuses.get(key, {'type': 'idle'}).get('type') != 'idle' for key in known):
            return False
        stop = self.operation(pid, ident, barrier['operation_id'])
        if (stop.get('kind') != 'INTERRUPT' or stop.get('status') != 'RETURNED'
                or stop.get('original_abort_acknowledged') is not True
                or stop.get('session_ref') != operation.get('session_ref')
                or stop.get('generation') != context['generation']
                or stop.get('target_operation_id') != operation['id']):
            return False
        records = self._cohort_records(pid, ident, context, state, operation)
        if (len(records) < 2 or any(row.get('status') == 'UNKNOWN_OUTCOME' for row in records)
                or not self._cohort_users_observed(context, records, messages)):
            return False
        assistants = [item for item in messages if item.get('info', {}).get('role') == 'assistant']
        if any(not item['info'].get('time', {}).get('completed') or
               any(part.get('type') == 'tool' and part.get('state', {}).get('status') in {'pending', 'running'}
                   for part in item.get('parts', [])) for item in assistants):
            return False
        if any(item['info'].get('parentID') == records[-1]['native_message_id'] and
               (item['info'].get('finish') == 'stop' or item['info'].get('error')) for item in assistants):
            return False  # An actual latest reply keeps its normal evidence/status path.
        parents = {row['native_message_id'] for row in records}
        aborted = [item for item in assistants if item['info'].get('parentID') in parents
                   and item['info'].get('error', {}).get('name') == 'MessageAbortedError']
        if not aborted:
            return False
        evidence = {'stop_operation_id': stop['id'], 'stop_receipt_sha256': digest(stop),
            'native_message_ids': [row['native_message_id'] for row in records],
            'native_assistant_ids': [item['info']['id'] for item in aborted],
            'messages_sha256': digest(messages), 'observed_at': state.get('observation_at'),
            'scope': 'ACKNOWLEDGED_ABORT_FRESH_IDLE_EXACT_ADMITTED_COHORT',
            'external_actions_rolled_back': False, 'semantic_acceptance': False}
        if recovering:
            stored = operation.get('termination_evidence', {})
            if any(stored.get(key) != evidence[key] for key in ('stop_operation_id', 'stop_receipt_sha256',
                    'native_message_ids', 'native_assistant_ids', 'messages_sha256')):
                return False
        for record in records[1:]:
            if record['status'] not in TERMINAL:
                record = self._finish(pid, ident, record['id'], 'CANCELLED',
                    termination_evidence=evidence, individual_response_observed=False,
                    independent_verification='NOT_RUN', semantic_acceptance=False)
            # Also repair a lost public projection after a partial state write.
            # An immutable terminal receipt must never remain displayed as waiting.
            self.guidance._publish(context, pid, ident, record)
        context.pop('guidance_observation_cache', None)
        if not recovering:
            self._finish(pid, ident, operation['id'], 'CANCELLED', reason=reason,
                termination_evidence=evidence, continuation_guidance_ids=[row['id'] for row in records[1:]],
                native_assistant_ids=evidence['native_assistant_ids'],
                independent_verification='NOT_RUN', semantic_acceptance=False)
        self._update(pid, ident, active_operation_id=None)
        self._event(pid, ident, 'ORIGINAL_MESSAGE_SETTLED', {'operation_id': operation['id'],
            'status': 'CANCELLED', 'stop_operation_id': stop['id']})
        return True

    async def _settle(self, pid, ident, context, messages, *, statuses=None,
                      quiescent=False, barrier=None, can_release_interrupt=False):
        state = self._state(pid, ident)
        if (self.active.get(ident) is not context or context.get("closing")
                or state.get("generation") != context["generation"]):
            return
        operation_id = state["active_operation_id"]
        if not operation_id:
            return
        operation = self.operation(pid, ident, operation_id)
        if self._settle_interrupted_cohort(pid, ident, context, state, operation, messages,
                statuses=statuses, quiescent=quiescent, barrier=barrier,
                can_release_interrupt=can_release_interrupt):
            return
        if operation["status"] in TERMINAL:
            return
        guidance = await self.guidance.observe(pid, ident, context, messages)
        guided = (guidance.get('cohort_operation_id') == operation_id and
                  guidance.get('latest_guidance_native_message_id'))
        if guided:
            # 204 only acknowledges dispatch. A mid-run user message may
            # supersede the original assistant parent ID, so the run must
            # await the actual newest guidance response, never the old stop.
            if guidance['unobserved_operation_ids']:
                return
            final_guidance = next((item for item in guidance['finals'] if
                item['native_message_id'] == guidance['latest_guidance_native_message_id']), None)
            if final_guidance is None or state['native_status'] != 'idle':
                return
            finals = [item for item in messages if item['info'].get('id') in final_guidance['native_assistant_ids']]
            if not finals:
                return
            completed=self._finish(pid, ident, operation_id, final_guidance['status'],
                native_assistant_ids=[item['info']['id'] for item in finals],
                continuation_guidance_ids=list(state.get('guidance_operation_ids', [])),
                response_evidence='RUN_COHORT_LATEST_GUIDANCE_RESPONSE',
                individual_original_response_observed=any(item['info'].get('role') == 'assistant' and
                    item['info'].get('parentID') == operation.get('native_message_id') and
                    item['info'].get('finish') == 'stop' for item in messages),
                independent_verification='NOT_RUN', semantic_acceptance=False)
            self._observe_subscription_response(pid,ident,context,completed,messages,
                final_guidance['operation_id'])
            self._update(pid, ident, active_operation_id=None)
            self._event(pid, ident, 'ORIGINAL_MESSAGE_SETTLED',
                {'operation_id': operation_id, 'status': final_guidance['status'],
                 'continuation_guidance_ids': list(state.get('guidance_operation_ids', []))})
            return
        assistants = [item for item in messages if item["info"].get("role") == "assistant" and
                      item["info"].get("parentID") == operation.get("native_message_id")]
        finals = [item for item in assistants if item["info"].get("time", {}).get("completed") and
                  (item["info"].get("finish") == "stop" or item["info"].get("error"))]
        rejected = [item for item in assistants if item["info"].get("time", {}).get("completed") and
                    any(part.get("type") == "tool" and part.get("state", {}).get("status") == "error" for part in item.get("parts", []))]
        if not finals and operation.get("user_reject_acknowledged") and rejected and state["native_status"] == "idle":
            self._finish(pid, ident, operation_id, "CANCELLED", reason="USER_REJECTED_ORIGINAL_TOOL_REQUEST",
                         native_assistant_ids=[item["info"]["id"] for item in rejected],
                         independent_verification="NOT_RUN", semantic_acceptance=False)
            self._update(pid, ident, active_operation_id=None)
            self._event(pid, ident, "ORIGINAL_MESSAGE_SETTLED", {"operation_id": operation_id, "status": "CANCELLED"})
            return
        if not finals or state["native_status"] != "idle":
            return
        errors = [item["info"]["error"] for item in finals if item["info"].get("error")]
        status = "CANCELLED" if errors and all(e.get("name") == "MessageAbortedError" for e in errors) else "FAILED" if errors else "RETURNED"
        completed=self._finish(pid, ident, operation_id, status, native_assistant_ids=[item["info"]["id"] for item in finals],
                     independent_verification="NOT_RUN", semantic_acceptance=False)
        self._observe_subscription_response(pid,ident,context,completed,messages)
        self._update(pid, ident, active_operation_id=None)
        self._event(pid, ident, "ORIGINAL_MESSAGE_SETTLED", {"operation_id": operation_id, "status": status})

    async def _monitor(self, pid, ident, context):
        while not context["closing"]:
            try:
                if self.active.get(ident) is not context or self._state(pid, ident).get("generation") != context["generation"]:
                    return
                if datetime.fromisoformat(context["expires_at"]) <= datetime.now(timezone.utc):
                    await self.disconnect(pid, ident, status="EXPIRED")
                    return
                state = self._state(pid, ident)
                if state["active_operation_id"] or context.get('workflow_evidence_pending') or context.get('native_review_terminal_event') or context.get('native_message_terminal_event'):
                    # Observe exact user waits before deciding that execution
                    # time expired. A cached pending list cannot renew a lease.
                    observed = await self.inspect(pid, ident)
                    if context.get('lifecycle_scope_changed'):
                        # Revocation/root/private-config drift stops only this
                        # owned generation. Index-only management changes do
                        # not set this flag and do not interrupt observation.
                        if (self.active.get(ident) is context and not context.get('closing') and
                                self._state(pid, ident).get('generation') == context['generation']):
                            await self.disconnect(pid, ident, status='BLOCKED')
                        return
                    if (self.active.get(ident) is not context or context.get("closing")
                            or datetime.fromisoformat(context["expires_at"]) <= datetime.now(timezone.utc)):
                        continue
                    if not observed.get('observation_is_current'):
                        # Cached/unavailable evidence cannot justify a fresh
                        # deadline abort, settle or queued-page advancement.
                        await asyncio.sleep(.15)
                        continue
                    state = self._state(pid, ident)
                    if context.get('native_review_terminal_event') and state.get('active_operation_id') is None:
                        context['native_review_terminal_event'] = False
                        self.native_reviews.owner_changed(pid, ident)
                    if context.get('native_message_terminal_event') and state.get('active_operation_id') is None:
                        context['native_message_terminal_event'] = False
                        self.native_messages.owner_changed(pid, ident)
                    if state["active_operation_id"]:
                        operation = self.operation(pid, ident, state["active_operation_id"])
                        if operation.get("deadline", "9999") < now() and operation["status"] not in TERMINAL:
                            # A just-arrived user correction can extend its
                            # turn deadline within the owner's TTL. Serialize
                            # and re-read instead of aborting an old deadline.
                            async with context['lock']:
                                latest = self._state(pid, ident)
                                if latest.get('active_operation_id') == operation['id']:
                                    current = self.operation(pid, ident, operation['id'])
                                    if (self.active.get(ident) is context and not context.get('closing')
                                            and latest.get('generation') == context['generation']
                                            and context.get('quiescent_observation')
                                            and context.get('observed_command_epoch') == context.get('native_command_epoch', 0)
                                            and not context.get('native_commands_inflight', 0)
                                            and (context.get('native_message_absolute_deadline') or
                                                 current.get('human_wait', {}).get('status') != 'WAITING_FOR_USER')
                                            and not context.get('interrupt_barrier') and
                                            current.get('deadline','9999') < now() and current['status'] not in TERMINAL):
                                        await self._deadline_abort(pid, ident, context, current)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                state = self._state(pid, ident)
                if (self.active.get(ident) is not context or context.get("closing")
                        or state.get("generation") != context["generation"]):
                    return
                if state["active_operation_id"] and self.operation(pid, ident, state["active_operation_id"])["status"] not in TERMINAL:
                    self._finish(pid, ident, state["active_operation_id"], "UNKNOWN_OUTCOME", error_type=type(error).__name__)
                if state.get('native_status') != 'UNKNOWN' or state.get('observation_error') != type(error).__name__:
                    self._update(pid, ident, native_status="UNKNOWN", observation_error=type(error).__name__)
                # Preserve the original UNKNOWN. Keep this same bounded monitor
                # alive for subsequent evidence, explicit stop and owner TTL.
            await asyncio.sleep(.15)

    async def interrupt(self, pid, ident, body):
        body = InterruptSession.model_validate(body.model_dump() if isinstance(body, SessionControl) else body)
        self._manifest(pid, ident, body.session_ref.model_dump())
        # Preserve old SessionControl receipt hashes when the optional exact
        # operation was omitted; legacy callers retain session-level meaning.
        request = body.model_dump(exclude={"expected_active_operation_id"}) if body.expected_active_operation_id is None else body.model_dump()
        old, created = self._claim(pid, ident, body.operation_id, "INTERRUPT", request)
        if not created:
            return old
        barrier = None
        try:
            _, context = self._context(pid, ident, body.session_ref.model_dump(), body.generation,intent='STOP')
            async with context["lock"]:
                _, current = self._context(pid, ident, body.session_ref.model_dump(), body.generation,intent='STOP')
                if current is not context:
                    raise Conflict("Original interrupt owner changed while waiting")
                active = self._state(pid, ident)["active_operation_id"]
                if body.expected_active_operation_id is not None and active != body.expected_active_operation_id:
                    raise Conflict("Original active operation differs from the exact stop target")
                target = self._execution_target(pid,ident,context,self.operation(pid,ident,active)) if active else context['session_id']
                barrier = {"operation_id": body.operation_id, "generation": body.generation,
                           "target_operation_id": active, "http_finished": False}
                self._finish(pid, ident, body.operation_id, "DISPATCHING", generation=body.generation,
                             target_operation_id=active,target_native_session_id=target,
                             control_scope="EXACT_OPERATION" if body.expected_active_operation_id is not None else "SESSION")
                context["interrupt_barrier"] = barrier
            # No owner lock is held during native network I/O.  Writes fail
            # quickly at the barrier; readonly observation remains available.
            result = await self._http(context, "POST", "/session/" + target + "/abort")
            return self._finish(pid, ident, body.operation_id, "RETURNED" if result is True else "UNKNOWN_OUTCOME",
                                original_abort_acknowledged=result is True, original_message_stop_confirmed=False,
                                external_actions_rolled_back=False)
        except BaseException as error:
            previous = self.operation(pid, ident, body.operation_id)
            self._finish(pid, ident, body.operation_id, "UNKNOWN_OUTCOME" if previous["status"] == "DISPATCHING" else "FAILED",
                         error_type=type(error).__name__)
            raise
        finally:
            if barrier is not None:
                # This is the original context object, never a newly loaded
                # generation.  Only a later native idle observation releases it.
                barrier["http_finished"] = True

    async def reply(self, pid, ident, request_id, body, *, kind, reject=False):
        model = PermissionReply if kind == "PERMISSION" else PendingControl if reject else QuestionReply
        body = model.model_validate(body)
        self._manifest(pid, ident, body.session_ref.model_dump())
        old, created = self._claim(pid, ident, body.operation_id, kind + ("_REJECT" if reject else "_REPLY"), body.model_dump())
        if not created:
            return old
        try:
            rejecting = reject or kind == "PERMISSION" and body.reply == "reject"
            reply_intent = 'STOP' if rejecting else 'EXECUTE'
            if not rejecting:
                manifest,_=self._manifest(pid,ident,body.session_ref.model_dump())
                current=self._state(pid,ident)
                items=current.get('pending_permissions' if kind=='PERMISSION' else 'pending_questions',[])
                selected=next((p for p in items if p.get('request_ref')==body.request_ref.model_dump()),None)
                if selected is not None:
                    await self.editor_captures.broker.admit(manifest,generation=body.generation,
                        operation_id=body.operation_id,kind=kind+'_REPLY',
                        permission_ref=body.request_ref.model_dump() if kind=='PERMISSION' and selected.get('native_request',{}).get('permission')=='edit' else None)
            manifest, context = self._context(pid, ident, body.session_ref.model_dump(), body.generation,intent=reply_intent)
            parent_id = self._state(pid, ident).get("active_operation_id")

            def current_parent():
                self._context(pid, ident, body.session_ref.model_dump(), body.generation,intent=reply_intent)
                state = self._state(pid, ident)
                if not parent_id or state.get("active_operation_id") != parent_id:
                    raise Conflict("Pending reply's original parent operation changed")
                parent = self.operation(pid, ident, parent_id)
                if (parent.get("generation") != body.generation or parent.get("original_session_id") != self._execution_target(pid,ident,context,parent)
                        or parent["status"] != "ACCEPTED" and not (rejecting and parent["status"] == "UNKNOWN_OUTCOME")):
                    raise Conflict("Pending reply cannot resume an unconfirmed original operation")
                return parent

            if body.request_ref.id != request_id or body.request_ref.generation != body.generation:
                raise Conflict("Pending original request identity/generation differs")
            root = "/permission" if kind == "PERMISSION" else "/question"
            pending = await self._http(context, "GET", root)
            native = next((item for item in pending if item.get("id") == request_id and
                           item.get("sessionID") in context["known_sessions"]), None)
            if native is None or digest(native) != body.request_ref.sha256:
                raise Conflict("Pending original request no longer matches the reviewed exact request")
            admitted_parent = current_parent()
            if admitted_parent.get('kind')=='CHILD_MESSAGE' and native.get('sessionID')!=self._execution_target(pid,ident,context,admitted_parent):
                chain=await self._native_lineage(context,native.get('sessionID'))
                if self._execution_target(pid,ident,context,admitted_parent) not in chain:
                    raise Conflict('Pending request belongs to another child operation')
            if kind == "PERMISSION":
                if native.get('permission') in {'read', 'edit', 'glob', 'grep'} and body.reply != 'reject':
                    async with context['lock']:
                        current_parent()
                        current = await self._http(context, 'GET', '/permission')
                        exact = next((item for item in current if item.get('id') == request_id), None)
                        if exact != native:
                            raise Conflict('The reviewed original path permission changed')
                        message_id = native.get('tool', {}).get('messageID')
                        if not isinstance(message_id, str) or not re.fullmatch(r'msg_[A-Za-z0-9_-]{1,100}', message_id):
                            raise Conflict('Original path permission has no exact tool message evidence')
                        message = await self._http(context, 'GET', '/session/'+native['sessionID']+'/message/'+message_id)
                        await self._path_permission_messages(pid,ident,context,[native],[message])
                        if body.reply not in self._permission_choices(native, manifest, message=message):
                            raise Conflict("Original permission lies outside this conversation's explicit allowlists")
                        reviewed = None
                        if native['permission'] == 'edit':
                            self.editor_observers.assert_permission(manifest,body.request_ref.model_dump())
                            ref = manifest['request']['workspace_ref']
                            selected = self.workspaces.get(pid, ref['id'], ref['revision'])
                            from packages.adapters.opencode_change_review import approve_guard
                            reviewed = approve_guard(context.get('change_review'),
                                {'project_id':pid, 'session_ref':body.session_ref.model_dump(), 'generation':body.generation,
                                 'workspace_ref':ref, 'original_session_id':native['sessionID'],
                                 'root_original_session_id':context['session_id'],
                                 'active_native_session_id':self._execution_target(pid,ident,context,current_parent())},
                                native, message, workspaces=self.workspaces, binding=selected)
                        current_parent()
                        if reviewed is not None:
                            self.editor_observers.assert_permission(manifest,body.request_ref.model_dump())
                        fields = {'workspace_write_review':reviewed} if reviewed is not None else {}
                        self._finish(pid, ident, body.operation_id, 'DISPATCHING', **fields)
                        result = await self._http(context, 'POST', '/permission/'+request_id+'/reply', {'reply':body.reply})
                        return self._finish(pid, ident, body.operation_id, 'RETURNED' if result is True else 'UNKNOWN_OUTCOME',
                            original_request_processed=result is True, automatically_granted=False,
                            **fields, **({'atomic_write_guaranteed':False} if reviewed is not None else {}))
                if body.reply not in self._permission_choices(native, manifest):
                    raise Conflict("Original permission lies outside this conversation's explicit allowlists")
                payload, path = {"reply": body.reply}, root + "/" + request_id + "/reply"
            elif reject:
                payload, path = None, root + "/" + request_id + "/reject"
            else:
                if len(body.answers) != len(native.get("questions", [])):
                    raise InvalidInput("Answer count differs from the exact original questions")
                for question, answers in zip(native["questions"], body.answers):
                    if len(answers) != len(set(answers)) or not question.get("multiple", False) and len(answers) > 1:
                        raise InvalidInput("Answers differ from the original single/multiple selection contract")
                    labels = {item["label"] for item in question.get("options", [])}
                    if question.get("custom", True) is False and any(answer not in labels for answer in answers):
                        raise InvalidInput("This exact original question does not accept custom answers")
                payload, path = {"answers": body.answers}, root + "/" + request_id + "/reply"
            async with context["lock"]:
                parent = current_parent()
                if manifest.get('native_review_admission'):
                    await self.native_reviews.before_reply(manifest, context, native, body, kind, rejecting)
                self._finish(pid, ident, body.operation_id, "DISPATCHING")
                result = await self._http(context, "POST", path, payload)
                if manifest.get('native_review_admission'):
                    self.native_reviews.after_reply(manifest, context, body.operation_id, result is True)
                if (result is True and rejecting and self.active.get(ident) is context and not context.get("closing")
                        and self._state(pid, ident).get("active_operation_id") == parent_id
                        and self._state(pid, ident).get("generation") == body.generation and parent["status"] == "ACCEPTED"):
                    self._finish(pid, ident, parent_id, parent["status"], user_reject_acknowledged=True,
                                 rejected_request_ref=body.request_ref.model_dump(), rejecting_control_operation_id=body.operation_id)
                return self._finish(pid, ident, body.operation_id, "RETURNED" if result is True else "UNKNOWN_OUTCOME",
                                    original_request_processed=result is True, automatically_granted=False)
        except BaseException as error:
            value = self.operation(pid, ident, body.operation_id)
            if locals().get('manifest', {}).get('native_review_admission') and 'context' in locals():
                self.native_reviews.after_reply(manifest, context, body.operation_id, False)
            # Only a guard rejection before dispatch proves no edit was forwarded.
            failure = ({"error_code": "WORKSPACE_SOURCE_CHANGED"}
                       if value["status"] != "DISPATCHING" and isinstance(error, Conflict)
                       and str(error).startswith("WORKSPACE_SOURCE_CHANGED:") else {})
            self._finish(pid, ident, body.operation_id, "UNKNOWN_OUTCOME" if value["status"] == "DISPATCHING" else "FAILED",
                         error_type=type(error).__name__, **failure)
            raise

    async def disconnect(self, pid, ident, *, status="DISCONNECTED"):
        context = self.active.get(ident)
        if context:
            await self._dispose(pid, ident, context, status=status)
        return self.get(pid, ident)

    async def controlled_disconnect(self, pid, ident, body):
        """Revoke an exact generation even after its selected config changed.

        Stopping our owned process cannot require admitting that changed config.
        Its durable operation is never replayed against a later generation.
        """
        body = SessionControl.model_validate(body)
        self._manifest(pid, ident, body.session_ref.model_dump())
        previous, created = self._claim(pid, ident, body.operation_id, "DISCONNECT", body.model_dump())
        if not created:
            return {**self.get(pid, ident), "last_control_operation": previous}
        try:
            if self._state(pid, ident)["generation"] != body.generation:
                raise Conflict("Cannot disconnect another original owner generation")
            self._finish(pid, ident, body.operation_id, "DISPATCHING")
            view = await self.disconnect(pid, ident)
            receipt = self._finish(pid, ident, body.operation_id,
                "UNKNOWN_OUTCOME" if view["owner_status"] == "BLOCKED_PROCESS_STOP_UNCONFIRMED" else "RETURNED",
                exact_owned_process_stop_confirmed=view["owner_status"] != "BLOCKED_PROCESS_STOP_UNCONFIRMED",
                external_actions_rolled_back=False)
            return {**view, "last_control_operation": receipt}
        except BaseException as error:
            current = self.operation(pid, ident, body.operation_id)
            self._finish(pid, ident, body.operation_id, "UNKNOWN_OUTCOME" if current["status"] == "DISPATCHING" else "FAILED",
                         error_type=type(error).__name__)
            raise

    async def _dispose(self, pid, ident, context, *, status):
        context["closing"] = True
        cleanup_errors=[]
        self.editor_captures.broker.changed()
        binding = context.get('owner_binding') or context
        process, client = binding.get('process'), binding.get('client')
        # A failed identity check must not redirect cleanup into a replacement
        # transport. Use only the client/process handles captured at startup.
        cleanup_context = {**context, 'process': process, 'client': client,
                           'session_id': binding.get('session_id'), 'directory': binding.get('directory')}
        if client and cleanup_context.get("session_id") and process and process.returncode is None:
            try:
                await self._http(cleanup_context, "POST", "/session/" + cleanup_context["session_id"] + "/abort")
                # Original instance disposal closes only this process's selected
                # MCP transports before stopping the process we created.
                await self._http(cleanup_context, "POST", "/instance/dispose")
            except Exception:
                pass
        current = asyncio.current_task()
        tasks = [context[k] for k in ("listener", "monitor") if context.get(k) and context[k] is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if client:
            try:await client.aclose()
            except Exception as error:cleanup_errors.append(error)
        bridges={id(value):value for value in [
            (context.get('native_review') or {}).get('bridge'),
            (context.get('native_message') or {}).get('bridge'),
            (context.get('model_authority') or {}).get('bridge')] if value is not None}
        if bridges:
            results=await asyncio.gather(*(asyncio.to_thread(value.close) for value in bridges.values()),return_exceptions=True)
            cleanup_errors.extend(value for value in results if isinstance(value,BaseException))
        if process and process.returncode is None:
            try:
                try:process.terminate()  # Exact subprocess handle created by this owner.
                except ProcessLookupError:pass
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                # No process-name/global kill or automatic adoption. Preserve
                # identity and refuse a new owner while it remains alive.
                status = "BLOCKED_PROCESS_STOP_UNCONFIRMED"
                cleanup_errors.append(Conflict('Original owned process stop remains unconfirmed'))
            except Exception as error:cleanup_errors.append(error)
        wires = [value for value in [context.get('wire'), *context.get('workflow_wires', [])] if value is not None]
        if wires:
            # Seal every admitted relay even if one upstream refuses to stop.
            # Each relay joins its own real handlers; an unconfirmed close
            # leaves this context registered for the owner's pending receipt.
            results = await asyncio.gather(*(asyncio.to_thread(value.close) for value in wires), return_exceptions=True)
            cleanup_errors.extend(value for value in results if isinstance(value,BaseException))
        for stream in context.get("logs", []):
            try:stream.close()
            except Exception as error:cleanup_errors.append(error)
        state = self._state(pid, ident)
        if (self.active.get(ident) is not context or state.get('generation') != binding.get('generation') or
                state.get('original_session_id') != binding.get('session_id')):
            # Keep only unconfirmed old handles. Their cleanup never writes the new owner's state.
            if cleanup_errors:
                self._detached_cleanup[id(context)] = (pid, ident, context)
                raise cleanup_errors[0]
            self._detached_cleanup.pop(id(context), None)
            return
        if cleanup_errors:
            self._update(pid,ident,owner_status=('BLOCKED_PROCESS_STOP_UNCONFIRMED' if process and process.returncode is None
                else 'BLOCKED_RESOURCE_STOP_UNCONFIRMED'),native_status='UNKNOWN',
                process_identity=state.get('process_identity') if process and process.returncode is None else None)
            self._event(pid,ident,'ORIGINAL_OWNER_STOP_UNCONFIRMED',{
                'error_types':[type(error).__name__ for error in cleanup_errors],
                'original_handles_retained':True,'automatic_post_replay':False})
            raise cleanup_errors[0]
        if state["active_operation_id"]:
            try:
                original = self.operation(pid, ident, state["active_operation_id"])
            except NotFound:
                if context.get('native_review', {}).get('kind') != 'CAPABILITY_TASK':
                    raise
                # No receipt is invented. The review hook records uncertainty;
                # exact-handle teardown above still releases this dead owner.
            else:
                if original["status"] not in TERMINAL:
                    self._finish(pid, ident, state["active_operation_id"], "UNKNOWN_OUTCOME", reason="OWNER_DISCONNECTED_NO_POST_REPLAY",
                                 **({"owner_stop_status": "EXPIRED"} if status == "EXPIRED" else {}))
        self._update(pid, ident, owner_status=status, native_status="UNKNOWN", controller_identity=None,
                     process_identity=None if not process or process.returncode is not None else state.get("process_identity"))
        if context.get('native_review', {}).get('kind') == 'CAPABILITY_TASK':
            self.native_reviews.owner_stopped(pid, ident, context)
        self.active.pop(ident, None)
        self._event(pid, ident, "ORIGINAL_OWNER_DISCONNECTED", {"status": status,
            "returncode": process.returncode if process else None, "automatic_post_replay": False})

    @property
    def pending_cleanup_contexts(self):
        return tuple(context for _pid, _ident, context in self._detached_cleanup.values())

    async def close(self):
        self.closed = True
        self.editor_captures.broker.close()
        observer = getattr(self, "global_supervisor", None)
        if observer is not None:
            await observer.close()
        collector = getattr(self, "supervisor_collector", None)
        if collector is not None:
            await collector.close()
        errors = []
        detached = list(self._detached_cleanup.values())
        for ident in list(self.active):
            root = resolve_data_path("agent-sessions", self.data_dir)
            matches = list(root.glob("*/" + ident + "/manifest.json"))
            if len(matches) == 1:
                try:
                    await self.disconnect(matches[0].parent.parent.name, ident)
                except Exception as error:
                    errors.append(error)
        for pid, ident, context in detached:
            try:
                await self._dispose(pid, ident, context, status="DISCONNECTED")
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]
