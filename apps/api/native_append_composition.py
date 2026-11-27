"""Compose one original task service, deferring OS/qualification until bind."""
from pathlib import Path

from apps.api.native_append import NotepadAppendTasks, register_native_append_routes
from apps.api.native_append_host import NativeAppendHostBroker, register_native_append_host_routes
from apps.api.native_tasks import register_native_task_routes
from packages.adapters.native_append import NotepadQualificationReader, Win32ClassicNotepadInspector
from packages.domain.store import Conflict


class BoundNotepadInspector:
    def __init__(self, companion):
        self.companion=companion
        self.source=None
        self.inspector=None

    def read(self, expected_window):
        current=self.companion.source
        if current is None or self.companion.closed:
            raise Conflict('CURRENT_BOUND_WINDOWS_SOURCE_REQUIRED')
        if current is not self.source:
            self.inspector=Win32ClassicNotepadInspector(current)
            self.source=current
        return self.inspector.read(expected_window)


class BoundAppendQualification:
    def __init__(self, companion, source_root):
        self.companion=companion
        self.source_root=Path(source_root)
        self.reason='QUALIFICATION_NOT_CONFIGURED'

    def read(self, snapshot):
        runtime=self.companion.runtime or {}
        policy=runtime.get('notepad_append_qualification_ref')
        if policy is None:
            self.reason='QUALIFICATION_NOT_CONFIGURED'
            return None
        root=runtime['runtime_root']
        reader=NotepadQualificationReader(source_root=self.source_root,runtime_root=root,
            evidence_root=root,policy_ref=policy)
        value=reader.read(snapshot)
        self.reason=reader.reason
        return value


def register_bound_native_append(app, *, companion, source_root, session_cookie_checker):
    if (getattr(app.state, 'native_tasks', None) is not None
            or getattr(app.state, 'native_append_host', None) is not None
            or getattr(companion, 'task_service', None) is not None):
        # Constructors write a fresh intent generation and inspect owned loops;
        # detecting duplicates after construction would invalidate the old owner.
        raise Conflict('ONE_ORIGINAL_NATIVE_TASK_SERVICE_REQUIRED')
    runtime=companion.runtime or {}
    root=runtime.get('runtime_root',str(source_root))
    service=NotepadAppendTasks(app,companion=companion,
        inspector=BoundNotepadInspector(companion),
        qualification_reader=BoundAppendQualification(companion,source_root),runtime_root=root)
    registered=register_native_task_routes(app,companion=companion,service=service)
    if registered is not service:
        raise Conflict('ONE_ORIGINAL_NATIVE_TASK_SERVICE_REQUIRED')
    register_native_append_routes(app,service=service)
    broker=NativeAppendHostBroker(service=service,session_cookie_checker=session_cookie_checker)
    register_native_append_host_routes(app,broker=broker)
    companion.task_service=service
    app.state.native_append_host=broker
    return service
