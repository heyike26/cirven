"""Actual HTTP/Store/domain path, with only Windows handle APIs injected.

The counter applies bidirectional sharing, never qualifies a Windows kernel.
"""
import base64
from copy import deepcopy
import ntpath

import pytest

from packages.adapters.windows_workspace_handles import WindowsWorkspaceHandles
from packages.domain.local_workspaces import FileEditInput, FileSource, sha
from packages.domain.store import Conflict, InvalidInput, NotFound
from test_delivery_report_discussion_continuity import scene, unchanged_sources, human_save
from test_managed_saved_read import SharingKernel, held_writer


def check(value, message='saved workspace snapshot boundary failed'):
    if not value:
        raise AssertionError(message)


class NativeWorkspaceBoundary:
    """Map this real fixture root into the unchanged native handle adapter."""
    def __init__(self, folder):
        self.folder = folder
        self.kernel = SharingKernel()
        self.adapter = WindowsWorkspaceHandles(self.kernel)
        root, identity = self.adapter.root_identity('C:/selected')
        self.native_binding = {'root': root, 'root_identity': identity}
        self.calls = []
        self.after_read = None
        for item in sorted(folder.rglob('*')):
            relative = item.relative_to(folder).parts
            native = ntpath.join(root, *relative)
            if item.is_dir():
                self.kernel.add(native, directory=True)
            else:
                self.kernel.add(native, raw=item.read_bytes())

    def read_regular(self, binding, path, **options):
        check(binding['root'] == str(self.folder))
        actual = self.folder.stat()
        check(binding['root_identity'] == {'device': actual.st_dev, 'inode': actual.st_ino})
        private_check = options.pop('private_check', None)
        if private_check:
            private_check(binding, path)
        self.calls.append({'path': path, **options})
        result = self.adapter.read_regular(self.native_binding, path, **options)
        if self.after_read:
            callback, self.after_read = self.after_read, None
            callback()
        return result

    def native_path(self, path):
        return ntpath.join(self.native_binding['root'], *path.split('/'))


def install(scene, monkeypatch):
    boundary = NativeWorkspaceBoundary(scene['folder'])
    ws = scene['client'].app.state.local_workspaces
    monkeypatch.setattr(ws, '_native_handles', lambda: boundary)
    return ws, boundary


@pytest.mark.parametrize('scene', ['PROPOSAL'], indirect=True)
def test_normal_delivery_file_get_open_registration_and_observation_share_saved_bytes(scene, monkeypatch):
    client = scene['client']
    ws, boundary = install(scene, monkeypatch)
    path = scene['generated']['path']
    writer = held_writer(boundary.kernel, boundary.native_path(path))
    original = deepcopy(scene['delivered'])
    try:
        response = client.get(scene['local'] + '/file', params={'path': path})
        check(response.status_code == 200, response.text)
        value = response.json()
        check(value['source_ref'] == scene['generated'] and value['current'])
        check(base64.b64decode(value['content']) == scene['original'])
        opened = client.post('/api/desktop-host/native-open/file', json={
            'project_id': scene['pid'], 'file_source_ref': value['source_ref'],
            'delivery_operation_id': scene['operation']})
        check(opened.status_code == 200, opened.text)
        link = opened.json()['file_link_ref']
        basis, candidate = client.app.state.native_messages.reviews._basis(link)
        check(basis['file_link_ref'] == link and basis['proposal_ref'] == scene['delivered']['proposal_ref'])
        check(candidate['nodes'][0]['id'] == 'review')
        observed = client.post('/api/desktop-host/file-contexts/observe', json={'link_ref': link})
        check(observed.status_code == 200, observed.text)
        saved = observed.json()
        check(saved['status'] == 'UNCHANGED' and saved['current_sha256'] == sha(scene['original']))
        check(saved['unsaved_changes'] == 'NOT_OBSERVED')
        check(saved['coverage'] == 'REGISTERED_SAVED_FILE_BYTES_NOT_APPLICATION_MEMORY')
        linked = client.post('/api/desktop-host/native-open/link', json={'link_ref': link})
        check(linked.status_code == 200, linked.text)
        current = client.get(scene['base'] + '/planning-delivery/operations/' + scene['operation'])
        check(current.status_code == 200, current.text)
        check(current.json()['current_review_status'] == 'CURRENT')
        check(scene['delivered'] == original, 'historical receipt must remain unchanged')
        check(set(boundary.kernel.handles) == {writer}, 'reader must retain the application writer')
        check(all(call.get('shared_saved_document') is True for call in boundary.calls
                  if call['path'] == path))
        check(any(call['path'] == '要求.txt' and not call.get('shared_saved_document')
                  for call in boundary.calls), 'original requirement/preimage checks remain strict')
        with pytest.raises(Conflict, match='busy'):
            ws.read(scene['pid'], scene['workspace']['id'], path)
        with pytest.raises(Conflict, match='busy'):
            ws.check_sources(scene['pid'], [FileSource.model_validate(scene['generated'])])
        with pytest.raises(Conflict, match='busy'):
            client.app.state.desktop_host.selection(scene['pid'], scene['generated'])
        # Preimage admission must fail at the unchanged strict read, before a
        # write implementation could be called or any user bytes replaced.
        with pytest.raises(Conflict, match='busy'):
            ws.edit(scene['pid'], scene['workspace']['id'], FileEditInput(
                operation_id='explicit-edit-still-strict', path=path,
                expected_revision=1, expected_sha256=scene['generated']['sha256'], content='never applied'))
        check(set(boundary.kernel.handles) == {writer})
        unchanged_sources(scene)
        # An actual human-authored saved replacement becomes a new FileSource;
        # it does not overwrite or borrow authority from the immutable report.
        changed = human_save(scene['path'], 'OPEN_APPLICATION_SAVED_EDIT')
        boundary.kernel.replace_leaf(boundary.native_path(path), raw=changed)
        current = client.get(scene['local'] + '/file', params={'path': path}).json()
        check(current['current'] and current['source_ref']['revision'] == 2)
        check(current['source_ref']['sha256'] == sha(changed))
        rejected = client.post('/api/desktop-host/native-open/file', json={
            'project_id': scene['pid'], 'file_source_ref': scene['generated'],
            'delivery_operation_id': scene['operation']})
        check(rejected.status_code == 409)
        reopened = client.post('/api/desktop-host/native-open/file', json={
            'project_id': scene['pid'], 'file_source_ref': current['source_ref'],
            'delivery_operation_id': scene['operation']})
        check(reopened.status_code == 200, reopened.text)
        frozen = client.app.state.native_messages.reviews._basis(reopened.json()['file_link_ref'])[0]
        check(frozen['proposal_ref'] == basis['proposal_ref'] and
              frozen['source_snapshot_sha256'] == basis['source_snapshot_sha256'] and
              frozen['candidate_sha256'] == basis['candidate_sha256'])
        saved = client.post('/api/desktop-host/file-contexts/observe',
                            json={'link_ref': reopened.json()['file_link_ref']}).json()
        check(saved['current_sha256'] == sha(changed) and saved['unsaved_changes'] == 'NOT_OBSERVED')
        check('HUMAN_BODY_OPEN_APPLICATION_SAVED_EDIT' in saved['parsed']['text_content'])
        check(client.get(scene['source_url']).content == scene['source_raw'])
        check(client.get(scene['artifact']['download_url']).content == scene['original'])
        check(scene['path'].read_bytes() == changed)
    finally:
        boundary.kernel.close(writer)
    check(not boundary.kernel.handles)


@pytest.mark.parametrize('fault', ['replacement', 'in-place-write', 'parent-reparse', 'between-reads'])
def test_saved_snapshot_rejects_metadata_namespace_or_double_read_drift(scene, monkeypatch, fault):
    ws, boundary = install(scene, monkeypatch)
    path = scene['generated']['path']
    native = boundary.native_path(path)
    writer = held_writer(boundary.kernel, native)
    if fault == 'between-reads':
        boundary.after_read = lambda: boundary.kernel.replace_leaf(native, raw=scene['original'] + b'new saved version')
    else:
        def changed(kernel, handle):
            if fault == 'replacement':
                kernel.replace_leaf(native, raw=scene['original'])
            elif fault == 'in-place-write':
                kernel.handles[handle]['node']['write'] += 1
            else:
                parent = ntpath.dirname(native).casefold()
                kernel.nodes[parent]['attributes'] |= 0x400
        boundary.kernel.after_read = changed
    before = ws.history(scene['pid'], scene['workspace']['id'], path)
    try:
        with pytest.raises((Conflict, InvalidInput, NotFound)):
            ws.read_saved_snapshot(scene['pid'], scene['workspace']['id'], path)
        check(ws.history(scene['pid'], scene['workspace']['id'], path) == before)
        check(set(boundary.kernel.handles) == {writer})
        check(scene['path'].read_bytes() == scene['original'])
    finally:
        boundary.kernel.close(writer)


def test_snapshot_api_scope_and_saved_revision_do_not_expand_write_or_private_access(scene, monkeypatch):
    ws, boundary = install(scene, monkeypatch)
    path = scene['generated']['path']
    writer = held_writer(boundary.kernel, boundary.native_path(path))
    try:
        current = ws.read_saved_snapshot(scene['pid'], scene['workspace']['id'], path, 1)
        check(current['source_ref'] == scene['generated'] and current['current'])
        for invalid in ('../outside.docx', '.git/secret', '/outside.docx'):
            response = scene['client'].get(scene['local'] + '/file', params={'path': invalid})
            check(response.status_code == 422)
        wrong = scene['client'].post('/api/projects', json={'name': 'other exact scope'}).json()['id']
        response = scene['client'].get(f'/api/projects/{wrong}/local-workspaces/{scene["workspace"]["id"]}/file',
                                     params={'path': path})
        check(response.status_code == 404)
        check(set(boundary.kernel.handles) == {writer})
    finally:
        boundary.kernel.close(writer)


def test_scoped_source_check_observes_open_saved_file_without_changing_strict_domain(scene, monkeypatch):
    ws, boundary = install(scene, monkeypatch)
    source = scene['generated']
    native = boundary.native_path(source['path'])
    writer = held_writer(boundary.kernel, native)
    client = scene['client']
    try:
        response = client.post(scene['local'] + '/source-check', json={'sources': [source]})
        check(response.status_code == 200, response.text)
        check(response.json()['sources'] == [{'source_ref': source, 'status': 'CURRENT',
                                             'live_sha256': source['sha256']}])
        with pytest.raises(Conflict, match='busy'):
            ws.check_sources(scene['pid'], [FileSource.model_validate(source)])
        calls = len(boundary.calls)
        invalid = {**source, 'workspace_id': '0' * 64}
        for body in ({'sources': [invalid]}, {'sources': [source] * 65}):
            response = client.post(scene['local'] + '/source-check', json=body)
            check(response.status_code == 422, response.text)
        check(len(boundary.calls) == calls, 'invalid scope/oversized lists must not open a file')
        changed = b'explicitly saved replacement bytes for source check'
        boundary.kernel.replace_leaf(native, raw=changed)
        response = client.post(scene['local'] + '/source-check', json={'sources': [source]})
        check(response.status_code == 200, response.text)
        check(response.json()['sources'][0]['status'] == 'STALE')
        check(response.json()['sources'][0]['live_sha256'] == sha(changed))
        check(scene['path'].read_bytes() == scene['original'])
        check(set(boundary.kernel.handles) == {writer})
    finally:
        boundary.kernel.close(writer)
