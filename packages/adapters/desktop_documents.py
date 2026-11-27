"""User-selected document actions through trusted file/action/host authorities.

This module owns opaque views; it implements no filesystem authority, annotation
store, OCR or Agent Loop. Those remain the existing controller's responsibility.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import PureWindowsPath
import re
import threading
import uuid

from packages.adapters.windows_native_window_host import WindowsNativeWindowHost, NativeWindowError, NativeWindowUnavailable, native_document_path, serialized
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest

DEFAULT_DOCUMENT_SUFFIXES = {".docx", ".dotx", ".pptx", ".vsdx", ".xmind", ".pdf", ".xlsx", ".txt"}
FILE_FIELDS = {"kind", "project_id", "workspace_id", "binding_revision", "binding_sha256", "path", "revision", "sha256"}
IDENTITY_FIELDS = FILE_FIELDS - {"revision", "sha256"}


def exact_file_ref(project_id, ref):
    if not isinstance(ref, dict) or set(ref) != FILE_FIELDS or ref["kind"] != "local_file" or ref["project_id"] != project_id:
        raise Conflict("Exact selected workspace FileSource is required")
    for name in ("binding_revision", "revision"):
        if type(ref[name]) is not int or ref[name] < 1: raise Conflict("FileSource revision is invalid")
    for name in ("workspace_id", "binding_sha256", "sha256"):
        if not isinstance(ref[name], str) or not re.fullmatch(r"[0-9a-f]{64}", ref[name]): raise Conflict("FileSource hash is invalid")
    if (not isinstance(ref["path"], str) or not ref["path"] or "\\" in ref["path"] or ":" in ref["path"] or
            ref["path"].startswith("/") or any(part in {"", ".", ".."} for part in ref["path"].split("/"))):
        raise Conflict("FileSource path is invalid")
    return copy.deepcopy(ref)


class DesktopDocuments:
    """Authority callbacks are trusted server/main-process code, never browser data.

    selection_authority(pid, FileSource) -> {native_path, root, source_ref,
    current:True}; the authority resolves the actual selected folder and verifies
    no links/escapes. action_authority(pid, action, FileSource, user_action_ref)
    returns True only for an admitted user operation. surface_authority(pid,
    surface_ref) derives {hwnd,pid,creation,bounds} from an actual desktop host.
    """
    def __init__(self, *, selection_authority, action_authority, surface_authority, backend=None,
            allowed_suffixes=None):
        if not all(callable(value) for value in (selection_authority, action_authority, surface_authority)):
            raise ValueError("Trusted file, user-action and native host authorities are required")
        self.select, self.authorize, self.surface = selection_authority, action_authority, surface_authority
        self.allowed_suffixes = frozenset(allowed_suffixes or DEFAULT_DOCUMENT_SUFFIXES)
        if not self.allowed_suffixes or any(not re.fullmatch(r"\.[a-z0-9]{1,12}", value) for value in self.allowed_suffixes):
            raise ValueError("Native document suffix policy is invalid")
        self.backend = backend
        self.unavailable_reason = None
        if self.backend is None:
            try: self.backend = WindowsNativeWindowHost()
            except NativeWindowUnavailable as error: self.unavailable_reason = str(error)
        self.views = {}; self.closed = False; self.lock = threading.RLock()

    def describe(self):
        return {"status": "IMPLEMENTED_NOT_QUALIFIED" if self.backend else "UNAVAILABLE",
            "reason": self.unavailable_reason, "native_host": "WIN32_OWNED_WINDOW",
            "native_windows": "NOT_RUN_IN_THIS_DELIVERY", "app_compatibility": "REQUIRES_TARGET_WINDOWS_APP_QUALIFICATION",
            "original_app_interface": True, "iframe_editor": False,
            "capture_backends": ["WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND", "PRINTWINDOW_BOUNDED_OPTIONAL_FALLBACK"],
            "capture_is_editor": False, "agent_loop": None}

    def _available(self):
        if self.closed: raise Conflict("Desktop document controller is closed")
        if self.backend is None: raise NativeWindowUnavailable(self.unavailable_reason or "WINDOWS_NATIVE_UNAVAILABLE")

    def _action(self, project_id, action, ref, token):
        if not isinstance(token, str) or not 1 <= len(token) <= 200 or self.authorize(project_id, action, copy.deepcopy(ref), token) is not True:
            raise Conflict("An admitted selected-document user action is required")

    def _selection(self, project_id, ref, *, allow_new_revision=False):
        original = exact_file_ref(project_id, ref)
        selected = self.select(project_id, copy.deepcopy(original))
        if not isinstance(selected, dict) or selected.get("current") is not True:
            raise Conflict("Selected actual document is no longer current")
        current = exact_file_ref(project_id, selected.get("source_ref"))
        if any(current[key] != original[key] for key in IDENTITY_FIELDS) or (not allow_new_revision and current != original):
            raise Conflict("Selected document scope/revision changed")
        path, root = native_document_path(selected.get("native_path")), native_document_path(selected.get("root"))
        expected = PureWindowsPath(root).joinpath(*current["path"].split("/"))
        if PureWindowsPath(path) != expected: raise Conflict("Native document path is outside the selected authoritative root")
        if PureWindowsPath(path).suffix.lower() not in self.allowed_suffixes:
            raise Conflict("This installed document-handler type is outside the configured native policy")
        return path, current

    def _view(self, project_id, view_ref):
        self._available()
        value = self.views.get(view_ref)
        if value is None or value["project_id"] != project_id: raise NotFound("Native view is outside this project")
        if not value["lease_ref"]: raise NativeWindowUnavailable("NATIVE_WINDOW_NOT_OWNED_OPENED_SEPARATELY")
        return value

    @serialized
    def open(self, project_id, file_source_ref, user_action_ref):
        self._available(); ref = exact_file_ref(project_id, file_source_ref)
        self._action(project_id, "OPEN_NATIVE", ref, user_action_ref)
        path, current = self._selection(project_id, ref)
        opened = self.backend.launch(path)
        view_ref = uuid.uuid4().hex
        self.views[view_ref] = {"project_id": project_id, "view_ref": view_ref, "source_ref": copy.deepcopy(current),
            "lease_ref": opened.get("lease_ref"), "native_path": path, "frame_revision": 0,
            "status": opened["status"], "opened_at": datetime.now(timezone.utc).isoformat()}
        return {**opened, "view_ref": view_ref, "file_source_ref": current,
            "file_source_sha256": digest(current), "document_binding": "LAUNCH_ARGUMENT_ONLY_UNCONFIRMED_ACTIVE_DOCUMENT"}

    @serialized
    def probe(self, project_id, view_ref):
        value = self._view(project_id, view_ref)
        return {**self.backend.probe(value["lease_ref"]), "view_ref": view_ref, "file_source_ref": copy.deepcopy(value["source_ref"])}

    @serialized
    def attach(self, project_id, view_ref, surface_ref, user_action_ref):
        value = self._view(project_id, view_ref); self._action(project_id, "HOST_NATIVE", value["source_ref"], user_action_ref)
        surface = self.surface(project_id, surface_ref)
        result = self.backend.attach(value["lease_ref"], surface); value["status"] = result["status"]
        return {**result, "view_ref": view_ref}

    @serialized
    def resize(self, project_id, view_ref, surface_ref, user_action_ref):
        value = self._view(project_id, view_ref); self._action(project_id, "HOST_NATIVE", value["source_ref"], user_action_ref)
        return {**self.backend.resize(value["lease_ref"], self.surface(project_id, surface_ref)), "view_ref": view_ref}

    @serialized
    def focus(self, project_id, view_ref, user_action_ref):
        value = self._view(project_id, view_ref); self._action(project_id, "FOCUS_NATIVE", value["source_ref"], user_action_ref)
        return {**self.backend.focus(value["lease_ref"]), "view_ref": view_ref}

    @serialized
    def snapshot(self, project_id, view_ref, user_action_ref, *, timeout_seconds=5, allow_printwindow_fallback=False):
        value = self._view(project_id, view_ref); self._action(project_id, "CAPTURE_NATIVE", value["source_ref"], user_action_ref)
        path, observed = self._selection(project_id, value["source_ref"], allow_new_revision=True)
        if path != value["native_path"]: raise Conflict("Native document path changed")
        captured = self.backend.snapshot(value["lease_ref"], timeout_seconds=timeout_seconds,
            allow_printwindow_fallback=allow_printwindow_fallback)
        _after_path, after = self._selection(project_id, value["source_ref"], allow_new_revision=True)
        value["frame_revision"] += 1
        raw = captured["png"]
        frame = {"view_ref": view_ref, "frame_revision": value["frame_revision"],
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "width": captured["width"], "height": captured["height"],
            "captured_at": datetime.now(timezone.utc).isoformat(), "backend": captured["backend"],
            "file_source_ref_at_open": copy.deepcopy(value["source_ref"]), "observed_file_source_ref": observed,
            "observed_file_source_ref_after_capture": after,
            "file_changed_during_capture": observed != after, "window_lease_ref": value["lease_ref"],
            "rendered_revision": {"kind": "CAPTURE_FRAME", "revision": value["frame_revision"]},
            "document_binding": "LAUNCH_ARGUMENT_ONLY_UNCONFIRMED_ACTIVE_DOCUMENT",
            "saved_file_render_correspondence": "UNCONFIRMED_REQUIRES_APPLICATION_ADAPTER",
            "capture_completeness": captured.get("capture_completeness", "UNVERIFIED"),
            "fallback_reason": captured.get("fallback_reason"),
            "qualification": "WINDOWS_APP_CAPTURE_NOT_VALIDATED"}
        frame["binding_sha256"] = digest(frame)
        return {"png": raw, "frame_ref": frame}

    @serialized
    def detach(self, project_id, view_ref, user_action_ref):
        value = self._view(project_id, view_ref); self._action(project_id, "DETACH_NATIVE", value["source_ref"], user_action_ref)
        # File edits do not remove the authority to safely restore this own view.
        result = self.backend.detach(value["lease_ref"]); value["status"] = "DETACHED"
        return {**result, "view_ref": view_ref}

    @serialized
    def close(self):
        self.closed = True
        failures = []
        if self.backend:
            for value in self.views.values():
                if value["lease_ref"]:
                    try: self.backend.release(value["lease_ref"])
                    except NativeWindowError as error: failures.append({"view_ref": value["view_ref"], "error": str(error)})
        return {"status": "CLOSED" if not failures else "RESTORE_REQUIRED", "failures": failures,
            "app_processes_terminated": False}
