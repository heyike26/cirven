"""Pause the ordinary observer after a genuine CLI final answer and before DOCX publication.

All three cases use the actual App, store, CLI, private ordinary tool and OOXML.
A local external-model fixture supplies the three tool calls and terminal reply.
"""
import asyncio
from pathlib import Path
import threading
import time

from test_native_file_continuity_api import scene, app_client, send, MESSAGES
from native_docx_wire_fixture import ANSWER, RevisionWire, configure
from test_native_messages_docx import saved_docx


def check(value, message='native DOCX publication edge failed'):
    if not value:
        raise AssertionError(message)


def linked_document(scene):
    client, pid, folder, base, *_ = scene
    source_path = folder / 'Saved.docx'
    source_path.write_bytes(saved_docx())
    source = client.get(base + '/file', params={'path': source_path.name})
    check(source.status_code == 200, source.text)
    response = client.post('/api/desktop-host/native-open/file', json={
        'project_id': pid, 'file_source_ref': source.json()['source_ref']})
    check(response.status_code == 200, response.text)
    return response.json()['file_link_ref'], source_path


class TerminalCleanupGate:
    def __init__(self, native, operation):
        self.native = native
        self.operation = operation
        self.entered, self.release = threading.Event(), threading.Event()
        self.observe = native.ordinary._owner_changed
        self.wire = RevisionWire()

    async def pause_terminal_observer(self, pid, sid):
        with self.native.ws.lock:
            record = self.native.ordinary._record(self.operation)
            state, op = self.native.ordinary._state_for(record)
            ready = (op and op['status'] == 'RETURNED' and state['active_operation_id'] is None
                and record['status'] == 'PROCESSING' and record.get('docx_revision', {}).get('proposal_status') == 'PROPOSED')
        if ready:
            self.entered.set()
            if not await asyncio.to_thread(self.release.wait, 90):
                raise AssertionError('DOCX observer gate was not released by the test')
        await self.observe(pid, sid)

    def close(self):
        self.release.set()
        self.wire.close()


def begin(scene, monkeypatch, operation='publication-edge'):
    client, pid, *_rest, settings = scene
    link, source_path = linked_document(scene)
    original = source_path.read_bytes()
    native = client.app.state.native_messages
    gate = TerminalCleanupGate(native, operation)
    monkeypatch.setattr(native.ordinary, '_owner_changed', gate.pause_terminal_observer)
    try:
        profile = configure(client, settings, gate.wire)
        response = send(client, pid, link, operation=operation, provider_profile_ref=profile)
        check(response.status_code == 200, response.text)
        check(gate.entered.wait(180), 'The ordinary owner did not reach the actual terminal-publication window')
        check(len(gate.wire.calls) == 4 and gate.wire.results[-1]['result']['status'] == 'PROPOSED')
        check(source_path.read_bytes() == original)
    except BaseException:
        gate.close()
        raise
    return gate, native, source_path, original


def settled(client, native, operation):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with native.ws.lock:
            record = native.ws._read(native.path(operation), authority=False)
        if record['status'] != 'PROCESSING':
            result = client.get(MESSAGES + '/' + operation)
            check(result.status_code == 200, result.text)
            return result.json()
        time.sleep(.01)
    raise AssertionError('The actual native finalizer did not settle')


def registry_files(settings):
    root = settings.data_dir / 'docx-revisions'
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_cancel_terminal_model_before_publication_retains_answer_and_restart_never_publishes(scene, monkeypatch):
    client, pid, *_rest, settings = scene
    gate, native, source_path, original = begin(scene, monkeypatch)
    try:
        pending = client.get(MESSAGES + '/publication-edge').json()
        check(pending['status'] == 'PROCESSING' and pending['artifact_status'] == 'PREPARING')
        cohort = pending['native_cohort']
        operation = native.ordinary.sessions.operation(pid, pending['owner']['session_ref']['id'], cohort['native_operation_id'])
        check(operation['status'] == 'RETURNED')
        cancel = client.post(MESSAGES + '/publication-edge/cancel')
        check(cancel.status_code == 200, cancel.text)
        value = cancel.json()
        check(value['status'] in {'PROCESSING', 'RETURNED'}, 'An already returned model reply must survive stop')
        check(value['artifact_status'] == 'CANCELLED_NOT_PUBLISHED' and value['artifacts'] == [])
        with native.ws.lock:
            record = native.ws._read(native.path('publication-edge'), authority=True)
        check(record['docx_publication_cancel_requested'] is True)
        check(not native.docx_owner_current(pid, native._docx_owner(record)))
        gate.release.set()
        result = settled(client, native, 'publication-edge')
        check(result['status'] == 'RETURNED' and result['artifact_status'] == 'CANCELLED_NOT_PUBLISHED')
        check(result['text'] == ANSWER and result['answer_source'] == 'ORIGINAL_NATIVE_TEXT_JSON_VALIDATED')
        entry = record['docx_revision']
        check(native.docx_revisions.operation(pid, entry['operation_id'])['status'] == 'NOT_ADMITTED')
        frozen = registry_files(settings)
        for _ in range(3):
            check(client.get(MESSAGES + '/publication-edge').json()['artifact_status'] == 'CANCELLED_NOT_PUBLISHED')
        with app_client(settings) as restarted:
            for _ in range(2):
                value = restarted.get(MESSAGES + '/publication-edge').json()
                check(value['status'] == 'RETURNED' and value['record_origin'] == 'HISTORICAL_READONLY')
                check(value['artifact_status'] == 'CANCELLED_NOT_PUBLISHED' and value['artifacts'] == [])
        check(registry_files(settings) == frozen, 'GET or restart must not publish the retained proposal')
        check(source_path.read_bytes() == original and len(gate.wire.calls) == 4)
    finally:
        gate.close()


def test_cancel_after_published_artifact_never_rolls_back_or_relabels_it(scene, monkeypatch):
    client, pid, *_rest, settings = scene
    gate, native, source_path, original = begin(scene, monkeypatch, 'already-published')
    try:
        gate.release.set()
        result = settled(client, native, 'already-published')
        check(result['status'] == 'RETURNED' and result['artifact_status'] == 'NEW_ARTIFACT')
        artifact = result['artifacts'][0]
        before = registry_files(settings)
        cancel = client.post(MESSAGES + '/already-published/cancel')
        check(cancel.status_code == 200, cancel.text)
        check(cancel.json()['artifact_status'] == 'NEW_ARTIFACT')
        check(cancel.json()['artifacts'] == result['artifacts'])
        with native.ws.lock:
            record = native.ws._read(native.path('already-published'), authority=True)
        check(record.get('docx_publication_cancel_requested', False) is False)
        check(registry_files(settings) == before)
        check(client.get(artifact['download_url']).status_code == 200)
        check(source_path.read_bytes() == original and len(gate.wire.calls) == 4)
    finally:
        gate.close()


def test_actual_partial_publication_is_unknown_and_get_restart_cancel_do_not_retry(scene, monkeypatch):
    client, pid, *_rest, settings = scene
    gate, native, source_path, original = begin(scene, monkeypatch, 'partial-publication')
    service = native.docx_revisions
    real_write = service._write
    manifest_attempts = []

    def interrupted_manifest(path, value):
        if Path(path).name == 'manifest.json':
            manifest_attempts.append(str(path))
            raise OSError('Explicit local artifact-manifest persistence fault fixture')
        return real_write(path, value)

    monkeypatch.setattr(service, '_write', interrupted_manifest)
    try:
        gate.release.set()
        result = settled(client, native, 'partial-publication')
        check(result['status'] == 'RETURNED' and result['artifact_status'] == 'PUBLICATION_UNKNOWN')
        check(result['artifacts'] == [] and result['automatic_retry'] is False)
        with native.ws.lock:
            record = native.ws._read(native.path('partial-publication'), authority=True)
        operation = service.operation(pid, record['docx_revision']['operation_id'])
        check(operation['status'] == 'UNKNOWN_OUTCOME')
        artifact_bytes = list((settings.data_dir / 'docx-revisions' / pid / 'artifacts').rglob('revision.docx'))
        check(len(artifact_bytes) == 1 and artifact_bytes[0].stat().st_size > 0,
            'A real partial write must not be described as certainly unpublished')
        before = registry_files(settings)
        for _ in range(3):
            check(client.get(MESSAGES + '/partial-publication').json()['artifact_status'] == 'PUBLICATION_UNKNOWN')
        cancel = client.post(MESSAGES + '/partial-publication/cancel')
        check(cancel.status_code == 200 and cancel.json()['artifact_status'] == 'PUBLICATION_UNKNOWN')
        with app_client(settings) as restarted:
            for _ in range(2):
                value = restarted.get(MESSAGES + '/partial-publication').json()
                check(value['status'] == 'RETURNED' and value['artifact_status'] == 'PUBLICATION_UNKNOWN')
        check(registry_files(settings) == before and len(manifest_attempts) == 1)
        check(source_path.read_bytes() == original and len(gate.wire.calls) == 4)
    finally:
        gate.close()
