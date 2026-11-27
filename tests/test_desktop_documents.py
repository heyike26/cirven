"""Real authorities' contract/policy tests; no native HWND or Office mock claims."""
import copy
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from packages.adapters.desktop_documents import DesktopDocuments, exact_file_ref
from packages.adapters.windows_native_window_host import NativeWindowError, NativeWindowUnavailable
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest


def file_ref():
    return {"kind": "local_file", "project_id": "project-a", "workspace_id": "a" * 64,
        "binding_revision": 1, "binding_sha256": "b" * 64, "path": "sources/需求.docx",
        "revision": 1, "sha256": "c" * 64}


class Backend:
    def __init__(self): self.calls = []; self.open_status = "WAITING_WINDOW"; self.capture_effect = None; self.restore_failure = False
    def launch(self, path):
        self.calls.append(("launch", path))
        return {"status": self.open_status, "lease_ref": "opaque-backend-lease" if self.open_status == "WAITING_WINDOW" else None}
    def probe(self, lease): return {"status": "OWNED_WINDOW_CANDIDATE", "qualification": "WINDOWS_APP_HOST_NOT_VALIDATED"}
    def attach(self, lease, surface): self.calls.append(("attach", lease, copy.deepcopy(surface))); return {"status": "NATIVE_ATTACHED_UNQUALIFIED"}
    def resize(self, lease, surface): self.calls.append(("resize", lease, copy.deepcopy(surface))); return {"status": "NATIVE_ATTACHED_UNQUALIFIED"}
    def focus(self, lease): self.calls.append(("focus", lease)); return {"status": "FOCUS_DENIED"}
    def detach(self, lease): self.calls.append(("detach", lease)); return {"status": "DETACHED", "app_terminated": False}
    def release(self, lease):
        self.calls.append(("release", lease))
        if self.restore_failure: raise NativeWindowError("WIN32_ERROR_5")
    def snapshot(self, lease, **budget):
        self.calls.append(("snapshot", lease, budget))
        if self.capture_effect: self.capture_effect()
        return {"png": b"unit transport fixture (not Windows capture)", "width": 32, "height": 24, "backend": "POLICY_FIXTURE", "capture_completeness": "UNVERIFIED"}


@pytest.fixture
def documents():
    state = {"ref": file_ref(), "root": r"D:\Selected", "path": "D:\\Selected\\sources\\需求.docx", "admitted": True}
    calls = []
    def selection(pid, ref):
        calls.append(("selection", pid, copy.deepcopy(ref)))
        return {"native_path": state["path"], "root": state["root"], "source_ref": copy.deepcopy(state["ref"]), "current": True}
    def action(pid, name, ref, token):
        calls.append(("action", pid, name, copy.deepcopy(ref), token)); return state["admitted"] and token == "explicit-user-action"
    def surface(pid, reference):
        calls.append(("surface", pid, reference))
        if reference != "opaque-main-surface": raise Conflict("Unregistered surface")
        return {"hwnd": 222, "pid": 202, "creation": 2002, "bounds": {"x": 50, "y": 80, "width": 600, "height": 400}}
    backend = Backend()
    service = DesktopDocuments(selection_authority=selection, action_authority=action, surface_authority=surface, backend=backend)
    return service, backend, state, calls


def opened(documents):
    service, _, _, _ = documents
    return service.open("project-a", file_ref(), "explicit-user-action")["view_ref"]


@pytest.mark.parametrize("field,value", [("revision", True), ("binding_revision", 0), ("workspace_id", "arbitrary-id"),
    ("project_id", "other-project"), ("kind", "document"), ("path", "../x.docx"), ("path", "x:stream"),
    ("path", "sources\\x.docx"), ("path", "/x.docx"), ("path", "sources//x.docx")])
def test_exact_filesource_contract_denies_ambiguous_scope(field, value):
    ref = file_ref(); ref[field] = value
    with pytest.raises(Conflict): exact_file_ref("project-a", ref)


def test_extra_filesource_fields_cannot_smuggle_native_pid_or_path():
    ref = file_ref(); ref["hwnd"] = 111
    with pytest.raises(Conflict): exact_file_ref("project-a", ref)


def test_user_action_is_admitted_before_selection_and_launch(documents):
    service, backend, state, calls = documents; state["admitted"] = False
    with pytest.raises(Conflict, match="user action"): service.open("project-a", file_ref(), "explicit-user-action")
    assert not backend.calls and not any(call[0] == "selection" for call in calls)


@pytest.mark.parametrize("change", ["revision", "root_escape", "executable", "binding"])
def test_open_requires_authoritative_current_ref_path_and_configured_document_handler(documents, change):
    service, backend, state, _ = documents; ref = file_ref()
    if change == "revision": state["ref"]["revision"] = 2
    elif change == "root_escape": state["path"] = r"D:\Elsewhere\sources\需求.docx"
    elif change == "binding": state["ref"]["binding_sha256"] = "d" * 64
    else:
        ref["path"] = "tool.exe"; state["ref"] = ref; state["path"] = r"D:\Selected\tool.exe"
    with pytest.raises(Conflict): service.open("project-a", ref, "explicit-user-action")
    assert not backend.calls


def test_open_freezes_source_and_only_opaque_main_surface_authority_is_used(documents):
    service, backend, state, calls = documents
    result = service.open("project-a", file_ref(), "explicit-user-action"); view = result["view_ref"]
    result["file_source_ref"]["path"] = "mutated.docx"
    assert service.probe("project-a", view)["file_source_ref"] == file_ref()
    with pytest.raises(NotFound): service.probe("other-project", view)
    with pytest.raises(Conflict, match="Unregistered surface"):
        service.attach("project-a", view, {"hwnd": 111, "pid": 101}, "explicit-user-action")
    assert not any(call[0] == "attach" for call in backend.calls)
    assert service.attach("project-a", view, "opaque-main-surface", "explicit-user-action")["status"] == "NATIVE_ATTACHED_UNQUALIFIED"
    assert service.resize("project-a", view, "opaque-main-surface", "explicit-user-action")["status"] == "NATIVE_ATTACHED_UNQUALIFIED"
    assert [call[2] for call in calls if call[0] == "surface"][-1] == "opaque-main-surface"


def test_existing_app_not_silently_adopted(documents):
    service, backend, _, _ = documents; backend.open_status = "OPENED_SEPARATELY"
    result = service.open("project-a", file_ref(), "explicit-user-action")
    with pytest.raises(NativeWindowUnavailable, match="OPENED_SEPARATELY"): service.probe("project-a", result["view_ref"])
    assert result["document_binding"] == "LAUNCH_ARGUMENT_ONLY_UNCONFIRMED_ACTIVE_DOCUMENT"


def test_frame_records_actual_source_changes_without_claiming_saved_document_render(documents):
    service, backend, state, _ = documents; view = opened(documents)
    state["ref"]["revision"] = 2; state["ref"]["sha256"] = "d" * 64
    def saved_during_frame(): state["ref"]["revision"] = 3; state["ref"]["sha256"] = "e" * 64
    backend.capture_effect = saved_during_frame
    result = service.snapshot("project-a", view, "explicit-user-action"); frame = result["frame_ref"]
    assert frame["sha256"] == hashlib.sha256(result["png"]).hexdigest()
    assert frame["file_source_ref_at_open"] == file_ref()
    assert frame["observed_file_source_ref"]["revision"] == 2 and frame["file_changed_during_capture"] is True
    assert frame["observed_file_source_ref_after_capture"]["revision"] == 3
    assert frame["saved_file_render_correspondence"] == "UNCONFIRMED_REQUIRES_APPLICATION_ADAPTER"
    assert frame["rendered_revision"] == {"kind": "CAPTURE_FRAME", "revision": 1}
    assert frame["binding_sha256"] == digest({key: value for key, value in frame.items() if key != "binding_sha256"})
    frame["file_source_ref_at_open"]["path"] = "mutated.docx"
    assert service.probe("project-a", view)["file_source_ref"] == file_ref()


def test_snapshot_scope_drift_denied_and_edited_file_does_not_prevent_detach(documents):
    service, backend, state, _ = documents; view = opened(documents)
    state["ref"]["binding_sha256"] = "d" * 64
    with pytest.raises(Conflict, match="scope/revision changed"): service.snapshot("project-a", view, "explicit-user-action")
    assert not any(call[0] == "snapshot" for call in backend.calls)
    assert service.detach("project-a", view, "explicit-user-action")["app_terminated"] is False


@pytest.mark.parametrize("action", ["focus", "detach", "snapshot"])
def test_each_user_operation_requires_admission(documents, action):
    service, backend, state, _ = documents; view = opened(documents); state["admitted"] = False
    with pytest.raises(Conflict, match="user action"): getattr(service, action)("project-a", view, "explicit-user-action")
    assert len(backend.calls) == 1


def test_shutdown_reports_failed_restore_and_can_retry_without_terminating_app(documents):
    service, backend, _, _ = documents; opened(documents); backend.restore_failure = True
    failed = service.close()
    assert failed["status"] == "RESTORE_REQUIRED" and failed["failures"][0]["error"] == "WIN32_ERROR_5"
    assert failed["app_processes_terminated"] is False
    with pytest.raises(Conflict, match="closed"): service.open("project-a", file_ref(), "explicit-user-action")
    backend.restore_failure = False
    assert service.close() == {"status": "CLOSED", "failures": [], "app_processes_terminated": False}


def test_snapshot_and_shutdown_are_serialized_without_abandoning_own_window(documents):
    service, backend, _, _ = documents; view = opened(documents)
    started, released = threading.Event(), threading.Event()
    def held_capture():
        started.set()
        if not released.wait(3): raise RuntimeError("Test capture release missing")
    backend.capture_effect = held_capture
    with ThreadPoolExecutor(max_workers=2) as pool:
        capture = pool.submit(service.snapshot, "project-a", view, "explicit-user-action")
        assert started.wait(1)
        closing = pool.submit(service.close)
        time.sleep(.03)
        assert not closing.done() and not any(call[0] == "release" for call in backend.calls)
        released.set()
        assert capture.result(timeout=1)["frame_ref"]["frame_revision"] == 1
        assert closing.result(timeout=1)["status"] == "CLOSED"


@pytest.mark.skipif(os.name == "nt", reason="Linux unavailable-path qualification only")
def test_non_windows_reports_unavailable_not_installed_or_qualified():
    service = DesktopDocuments(selection_authority=lambda *_: None, action_authority=lambda *_: False, surface_authority=lambda *_: None)
    status = service.describe()
    assert status["status"] == "UNAVAILABLE" and status["reason"] == "WINDOWS_NATIVE_NOT_RUN"
    assert status["native_windows"] == "NOT_RUN_IN_THIS_DELIVERY" and status["agent_loop"] is None
    assert status["iframe_editor"] is False and status["capture_is_editor"] is False
