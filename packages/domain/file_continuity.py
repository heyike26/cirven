"""Source-bound, read-only file continuity independent of native window lifetime.

Links and observations do not grant writes, identify an application's unsaved
buffer, or choose a file by name/recency. Different origins remain different
immutable links. Report working copies have one derived path, never a caller
supplied absolute path. No directory watcher, model or Agent loop is started.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import uuid

from packages.domain.document_import import DocumentImportError, parse_document
from packages.domain.local_workspaces import FileSource, MAX_FILE_BYTES, canonical, digest, relative, sha
from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.context_read_budget import source_bytes, current_context_read_budget


MAX_LINKS = 1000
MAX_SNAPSHOTS = 128
MAX_RECORD_BYTES = 20 * 1024 * 1024
VIEW_BYTES = 64000
DEFAULT_LINK_STORAGE_BYTES = 64 * 1024 * 1024
DEFAULT_TOTAL_STORAGE_BYTES = 256 * 1024 * 1024
ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
HASH = re.compile(r'^[a-f0-9]{64}$')
REPORT_PARTS = {'docx': 'download', 'pending_markdown': 'pending/download',
    'pending_json': 'pending/json/download', 'source_snapshot': 'source/download'}
ARTIFACT_WORKING_COPY_PART = 'artifact_pptx'
DOCX_REVISION_PART = 'docx_revision'
ORIGIN_IDS = {'conversation_id', 'session_id', 'original_session_id', 'operation_id', 'source_id'}
ORIGIN_REFS = {'artifact_ref', 'proposal_ref', 'discovery_ref', 'session_ref'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(value, *, hashed=False):
    if not isinstance(value, str) or not (HASH if hashed else ID).fullmatch(value):
        raise InvalidInput('Continuity identity must have its exact bounded format')
    return value


def _ref(value, *, project=False):
    keys = {'id', 'revision', 'sha256'} | ({'project_id'} if project else set())
    if (not isinstance(value, dict) or set(value) != keys or
            type(value.get('revision')) is not int or value['revision'] < 1):
        raise InvalidInput('An exact continuity revision reference is required')
    _id(value['id'], hashed=project)
    _id(value['sha256'], hashed=True)
    if project:
        _id(value['project_id'])
    return dict(value)


def _origin(value, project_id):
    allowed = ORIGIN_IDS | ORIGIN_REFS | {'kind', 'project_id', 'task_refs', 'source_kind',
        'generation_file_source_ref', 'source_link_ref', 'snapshot_ref', 'original_report_origin'}
    if not isinstance(value, dict) or not value or set(value) - allowed:
        raise InvalidInput('Origin needs bounded, supported source identities only')
    result = {}
    for key, item in value.items():
        if key == 'kind':
            if not isinstance(item, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', item):
                raise InvalidInput('Origin kind must be a bounded source code')
        elif key == 'project_id':
            if item != project_id:
                raise InvalidInput('Origin belongs to another project')
        elif key in ORIGIN_IDS:
            _id(item)
        elif key in ORIGIN_REFS:
            item = _ref(item)
        elif key in {'source_link_ref', 'snapshot_ref'}:
            item = _ref(item, project=True)
            if item['project_id'] != project_id:
                raise InvalidInput('Revision provenance belongs to another project')
        elif key == 'original_report_origin':
            if item is not None:
                if (not isinstance(item, dict) or item.get('kind') not in
                        {'GENERATED_REPORT', 'GENERATED_DELIVERY_REPORT'} or
                        'original_report_origin' in item):
                    raise InvalidInput('Revision report origin must be one bounded original report identity')
                item = _origin(item, project_id)
        elif key == 'generation_file_source_ref':
            try:
                item = FileSource.model_validate(item).model_dump()
            except (ValueError, TypeError):
                raise InvalidInput('Delivery origin requires an exact generated FileSource') from None
            if item['project_id'] != project_id:
                raise InvalidInput('Generated file origin belongs to another project')
        elif key == 'source_kind':
            if item not in {'PROPOSAL', 'DISCOVERY'}:
                raise InvalidInput('Unsupported report source kind')
        elif key == 'task_refs':
            if not isinstance(item, list) or len(item) > 64:
                raise InvalidInput('Origin task references exceed their bounded list')
            item = [_ref(ref) for ref in item]
            if len({canonical(ref) for ref in item}) != len(item):
                raise InvalidInput('Origin task references must be unique')
        result[key] = item
    if len(canonical(result).encode()) > 16384:
        raise InvalidInput('Origin metadata exceeds 16 KiB')
    return result


def _cut(value, size):
    return value.encode('utf-8')[:size].decode('utf-8', errors='ignore')


def _request_id(request):
    # Parent identity is an immutable admission check, not a way to mint a
    # fresh link after replacing the same registered report directory.
    return digest({key: value for key, value in request.items() if key != 'parent_identity'})


class FileContinuity:
    def __init__(self, store, data_dir, workspaces, *, report_reader=None, artifact_reader=None,
                 revision_reader=None,
                 max_link_snapshot_bytes=DEFAULT_LINK_STORAGE_BYTES,
                 max_total_snapshot_bytes=DEFAULT_TOTAL_STORAGE_BYTES):
        self.store, self.data_dir, self.ws = store, Path(data_dir).absolute(), workspaces
        self.report_reader = report_reader
        self.artifact_reader = artifact_reader
        self.revision_reader = revision_reader
        for limit in (max_link_snapshot_bytes, max_total_snapshot_bytes):
            if type(limit) is not int or not 1 <= limit <= 16 * 1024 * 1024 * 1024:
                raise InvalidInput('Snapshot storage budgets must be positive integers up to 16 GiB')
        self.max_link_snapshot_bytes = max_link_snapshot_bytes
        self.max_total_snapshot_bytes = max_total_snapshot_bytes
        if self.data_dir.is_symlink():
            raise InvalidInput('Continuity data root cannot be a symlink')

    def _path(self, *parts):
        return resolve_data_path('/'.join(('file-continuity', *parts)), self.data_dir)

    def _read(self, path):
        path = resolve_data_path(path, self.data_dir)
        if not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
            raise Conflict('Continuity record is unavailable or outside its size bound')
        try:
            wrapped = json.loads(source_bytes(path, MAX_RECORD_BYTES))
            value = wrapped['payload']
            if not isinstance(value, dict) or digest(value) != wrapped['sha256']:
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise Conflict('Continuity record integrity failed; prior snapshots remain retained') from None

    def _write(self, path, value):
        raw = (canonical({'payload': value, 'sha256': digest(value)}) + '\n').encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise InvalidInput('Continuity metadata is too large; old records are retained')
        path.parent.mkdir(parents=True, exist_ok=True)
        path = resolve_data_path(path, self.data_dir)
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _link_ref(value):
        return {'project_id': value['project_id'], 'id': value['id'], 'revision': 1, 'sha256': digest(value)}

    def _public(self, value):
        return {'link_ref': self._link_ref(value), **{key: value[key] for key in
            ('project_id', 'kind', 'filename', 'display_path', 'baseline_sha256',
             'baseline_file_source_ref', 'origin', 'registered_at')},
            'write_authorized': False, 'window_identity_required': False,
            'origin_authority': 'RECORDED_SOURCE_METADATA_NOT_EXECUTION_AUTHORITY'}

    def _links(self):
        folder = self._path('links')
        if not folder.exists():
            return []
        paths = []
        for path in folder.iterdir():
            if re.fullmatch(r'[a-f0-9]{64}\.json', path.name):
                paths.append(path)
                if len(paths) > MAX_LINKS:
                    raise Conflict('Continuity link registry exceeds its limit; nothing was removed')
        return sorted(paths)

    def _restored_parent_identity(self, value, parent):
        """Exact restore admission; a later parent replacement still fails closed."""
        try:
            root = Path(self.data_dir).resolve()
            marker = resolve_data_path('restore-provenance.json', root)
            if not marker.is_file() or marker.stat().st_nlink != 1 or marker.stat().st_size > 65536:
                return None
            proof = json.loads(marker.read_bytes())
            if (proof['schema_version'] != 'eeas.restore-provenance.v1'
                    or Path(proof['target_data_dir']) != root
                    or not HASH.fullmatch(proof['snapshot_sha256'])
                    or proof['manifest_sha256'] != proof['snapshot_sha256']
                    or re.fullmatch('[a-f0-9]{32}', proof['snapshot_id']) is None):
                return None
            reference = proof['managed_file_bindings_ref']
            expected_path = 'file-continuity/restored-bindings/' + proof['snapshot_id'] + '.json'
            if (set(reference) != {'path', 'bytes', 'sha256'} or reference['path'] != expected_path
                    or type(reference['bytes']) is not int or not 0 < reference['bytes'] <= 2 * 1024 * 1024):
                return None
            index = resolve_data_path(reference['path'], root)
            if not index.is_file() or index.stat().st_nlink != 1 or index.stat().st_size != reference['bytes']:
                return None
            raw = index.read_bytes()
            if len(raw) != reference['bytes'] or sha(raw) != reference['sha256']:
                return None
            saved = json.loads(raw)
            if (saved['schema_version'] != 'eeas.restored-managed-file-bindings.v1'
                    or saved['snapshot_id'] != proof['snapshot_id']
                    or saved['snapshot_sha256'] != proof['snapshot_sha256']
                    or saved['target_data_dir'] != proof['target_data_dir']
                    or not isinstance(saved['bindings'], dict) or len(saved['bindings']) > MAX_LINKS):
                return None
            binding = saved['bindings'][value['id']]
            expected = binding['restored_parent_identity']
            if (set(binding) != {'reference', 'relative_parent', 'saved_parent_identity', 'restored_parent_identity'}
                    or binding['reference'] != self._link_ref(value)
                    or binding['saved_parent_identity'] != value['request']['parent_identity']
                    or resolve_data_path(binding['relative_parent'], root) != parent
                    or not binding['relative_parent'].startswith('native-working-copies/')
                    or not isinstance(expected, dict) or set(expected) != {'device', 'inode'}
                    or not all(type(n) is int and n >= 0 for n in expected.values())):
                return None
            return expected
        except (OSError, ValueError, KeyError, TypeError, AttributeError, InvalidInput):
            return None

    def _register(self, request, baseline):
        ident = _request_id(request)
        path = self._path('links', ident + '.json')
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if path.exists():
                value = self._read(path)
                if value.get('request') != request or value.get('id') != ident:
                    previous = value.get('request', {})
                    part = request.get('report') if request['kind'] == 'REPORT_WORKING_COPY' else request.get('artifact')
                    if not isinstance(part, dict):
                        raise Conflict('Continuity link request integrity differs')
                    parent = resolve_data_path('/'.join(('native-working-copies', request['project_id'],
                        part['artifact_id'], part['kind'])), self.data_dir)
                    restored = self._restored_parent_identity(value, parent)
                    if (value.get('id') != ident or restored is None or restored != request.get('parent_identity')
                            or {k: v for k, v in previous.items() if k != 'parent_identity'} !=
                               {k: v for k, v in request.items() if k != 'parent_identity'}):
                        raise Conflict('Continuity link request integrity differs')
                return self._public(value)
            if len(self._links()) >= MAX_LINKS:
                raise Conflict('Continuity link registry is full; existing links remain retained')
            value = {'schema_version': 'eeas.file-continuity-link.v1', 'id': ident,
                'revision': 1, 'project_id': request['project_id'], 'kind': request['kind'],
                'filename': request['filename'], 'display_path': request['display_path'],
                'baseline_sha256': sha(baseline), 'baseline_file_source_ref': request.get('file_source_ref'),
                'origin': request['origin'], 'registered_at': _now(), 'request': request}
            self._write(path, value)
            return self._public(value)

    def _workspace_registration(self, project_id, file_source_ref, origin):
        _id(project_id)
        self.store.project(project_id)
        try:
            source = FileSource.model_validate(file_source_ref).model_dump()
            relative(source['path'])
        except (ValueError, TypeError):
            raise InvalidInput('A valid registered workspace FileSource is required') from None
        if source['project_id'] != project_id:
            raise InvalidInput('Continuity file belongs to another project')
        origin = _origin(origin, project_id)
        binding = self.ws.get(project_id, source['workspace_id'], source['binding_revision'])
        if binding['binding_sha256'] != source['binding_sha256']:
            raise Conflict('Continuity workspace binding differs')
        read = self.ws.read_saved_snapshot(project_id, source['workspace_id'], source['path'], source['revision'])
        if read['source_ref'] != source:
            raise Conflict('Continuity baseline differs from its actual frozen FileSource')
        baseline = read['content'].encode() if read['encoding'] == 'utf-8' else base64.b64decode(read['content'], validate=True)
        if sha(baseline) != source['sha256']:
            raise Conflict('Continuity baseline bytes differ')
        return {'kind': 'WORKSPACE_FILE', 'project_id': project_id,
            'filename': source['path'].split('/')[-1], 'display_path': source['path'],
            'file_source_ref': source, 'origin': origin}, baseline

    def register_workspace(self, project_id, file_source_ref, origin):
        if isinstance(origin, dict) and origin.get('kind') in {'GENERATED_DELIVERY_REPORT', 'DOCX_REVISION'}:
            raise InvalidInput('Generated provenance requires its verified artifact registration path')
        request, baseline = self._workspace_registration(project_id, file_source_ref, origin)
        return self._register(request, baseline)

    def register_delivery_report(self, project_id, current_source, generated_source, *,
                                 operation_id, artifact_id, source_kind, source_id, source_sha256):
        """Trusted route calls this after exact delivery-op membership validation.

        Current saved bytes may differ from the generation snapshot. Neither is
        rewritten or promoted to the other's requirements/confirmation authority.
        """
        _id(operation_id); _id(artifact_id, hashed=True); _id(source_id); _id(source_sha256, hashed=True)
        generated_request, generated_raw = self._workspace_registration(project_id,
            generated_source, {'kind': 'USER_OPENED_WORKSPACE_FILE'})
        generated_source = generated_request['file_source_ref']
        if self.report_reader is None or source_kind not in {'PROPOSAL', 'DISCOVERY'}:
            raise Conflict('Delivery continuity requires the immutable report reader')
        raw, item = self.report_reader(project_id, source_id, artifact_id, 'docx', source_kind=source_kind)
        if (not isinstance(raw, bytes) or sha(raw) != source_sha256 or item.get('sha256') != source_sha256
                or generated_source['sha256'] != source_sha256 or raw != generated_raw):
            raise Conflict('Generated workspace file differs from its immutable report baseline')
        origin = {'kind': 'GENERATED_DELIVERY_REPORT', 'operation_id': operation_id,
            'source_kind': source_kind, 'source_id': source_id,
            'artifact_ref': {'id': artifact_id, 'revision': 1, 'sha256': source_sha256},
            'generation_file_source_ref': generated_source}
        request, baseline = self._workspace_registration(project_id, current_source, origin)
        identity = ('kind', 'project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path')
        if any(request['file_source_ref'][key] != generated_source[key] for key in identity):
            raise Conflict('Current draft must retain the exact generated file location and binding')
        return self._register(request, baseline)

    def _managed_raw(self, parent, filename, expected=None):
        """Read a derived working-copy leaf; directories remain identity-bound.

        A Word-style atomic replacement of the leaf is allowed between reads.
        Directory replacement, links, and replacement during one read fail.
        """
        native = self.ws._native_handles()
        if native is not None:
            root, identity = native.root_identity(str(parent))
            if expected is not None and identity != expected:
                raise Conflict('CONTINUITY_PARENT_DRIFT: working-copy parent was replaced')
            raw = native.read_regular({'root': root, 'root_identity': identity}, filename,
                shared_saved_document=True)
            if native.root_identity(str(parent)) != (root, identity):
                raise Conflict('CONTINUITY_PARENT_DRIFT: working-copy parent changed during reading')
            return raw, identity
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
            raise InvalidInput('Secure managed-file reads are unavailable on this platform')
        descriptors, chain = [], []
        try:
            current = os.open(parent.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(current)
            for component in parent.parts[1:]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                descriptors.append(child)
                chain.append((current, component, child))
                current = child
            info = os.fstat(current)
            identity = {'device': info.st_dev, 'inode': info.st_ino}
            if expected is not None and identity != expected:
                raise Conflict('CONTINUITY_PARENT_DRIFT: working-copy parent was replaced')
            leaf = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
            descriptors.append(leaf)
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode):
                raise InvalidInput('Continuity reads require a regular file')
            if before.st_size > MAX_FILE_BYTES:
                raise InvalidInput('Continuity file exceeds 16 MiB; old snapshots remain retained')
            chunks, count = [], 0
            while True:
                part = os.read(leaf, min(1024 * 1024, MAX_FILE_BYTES + 1 - count))
                if not part:
                    break
                chunks.append(part)
                count += len(part)
                if count > MAX_FILE_BYTES:
                    raise InvalidInput('Continuity file grew beyond 16 MiB')
            after = os.fstat(leaf)
            live = os.stat(filename, dir_fd=current, follow_symlinks=False)
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or
                    (live.st_dev, live.st_ino) != (after.st_dev, after.st_ino)):
                raise Conflict('SOURCE_DRIFT: working copy changed during reading')
            for above, name, opened in chain:
                actual, held = os.stat(name, dir_fd=above, follow_symlinks=False), os.fstat(opened)
                if not stat.S_ISDIR(actual.st_mode) or (actual.st_dev, actual.st_ino) != (held.st_dev, held.st_ino):
                    raise Conflict('CONTINUITY_PARENT_DRIFT: a parent changed during reading')
            return b''.join(chunks), identity
        except FileNotFoundError:
            raise NotFound('Registered working copy is missing; rename/save-as needs an explicit new association') from None
        except OSError:
            raise InvalidInput('Working-copy path is unavailable or contains a rejected link') from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def register_report(self, project_id, artifact_id, kind, filename, source_sha256,
                        download_url, source_kind, source_id, *, origin=None):
        _id(project_id)
        _id(artifact_id, hashed=True)
        _id(source_sha256, hashed=True)
        _id(source_id)
        self.store.project(project_id)
        if kind not in REPORT_PARTS or source_kind not in {'PROPOSAL', 'DISCOVERY'}:
            raise InvalidInput('Unsupported exact report part or source kind')
        if (not isinstance(filename, str) or not filename or len(filename) > 240 or
                any(c in filename for c in '/\\:\0') or filename in {'.', '..'} or
                any(ord(c) < 32 for c in filename)):
            raise InvalidInput('Report filename must be one bounded plain filename')
        segment = 'planning/proposals' if source_kind == 'PROPOSAL' else 'discovery/sessions'
        expected_url = f'/api/projects/{project_id}/{segment}/{source_id}/reports/{artifact_id}/{REPORT_PARTS[kind]}'
        if download_url != expected_url:
            raise InvalidInput('Report URL differs from its exact project/source/artifact')
        if self.report_reader is None:
            raise Conflict('Report continuity needs the configured immutable report reader')
        raw, item = self.report_reader(project_id, source_id, artifact_id, kind, source_kind=source_kind)
        if (not isinstance(raw, bytes) or len(raw) > MAX_FILE_BYTES or sha(raw) != source_sha256 or
                item.get('sha256') != source_sha256 or item.get('filename') != filename):
            raise Conflict('Immutable report baseline bytes or metadata differ')
        base_origin = {'kind': 'GENERATED_REPORT', 'source_kind': source_kind, 'source_id': source_id,
            'artifact_ref': {'id': artifact_id, 'revision': 1, 'sha256': source_sha256}}
        supplied = _origin(origin, project_id) if origin is not None else {}
        if any(key in supplied and supplied[key] != value for key, value in base_origin.items()):
            raise InvalidInput('Report origin cannot replace its exact artifact/source identities')
        origin = _origin({**supplied, **base_origin}, project_id)
        parent = resolve_data_path('/'.join(('native-working-copies', project_id, artifact_id, kind)), self.data_dir)
        _, identity = self._managed_raw(parent, filename)
        return self._register({'kind': 'REPORT_WORKING_COPY', 'project_id': project_id,
            'filename': filename, 'display_path': filename, 'origin': origin,
            'report': {'artifact_id': artifact_id, 'kind': kind, 'filename': filename,
                'source_sha256': source_sha256, 'download_url': download_url,
                'source_kind': source_kind, 'source_id': source_id}, 'parent_identity': identity}, raw)

    def artifact_baseline(self, project_id, artifact_id, source_sha256, download_url):
        """Verify the exact registry origin before creating any working copy."""
        _id(project_id)
        _id(artifact_id, hashed=True)
        _id(source_sha256, hashed=True)
        self.store.project(project_id)
        expected_url = f'/api/projects/{project_id}/report-artifacts/{artifact_id}/download'
        if download_url != expected_url:
            raise InvalidInput('Artifact URL differs from its exact project and identity')
        if self.artifact_reader is None:
            raise Conflict('Artifact continuity needs the configured immutable artifact reader')
        item, raw = self.artifact_reader(project_id, artifact_id)
        if (not isinstance(item, dict) or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FILE_BYTES or
                item.get('project_id') != project_id or item.get('id') != artifact_id or
                type(item.get('revision')) is not int or item['revision'] != 1 or item.get('kind') != 'REPORT_PPTX' or
                item.get('sha256') != source_sha256 or sha(raw) != source_sha256 or
                item.get('byte_length') != len(raw) or
                item.get('download_url') != download_url):
            raise Conflict('Immutable artifact baseline bytes or metadata differ')
        filename = item.get('filename')
        if (not isinstance(filename, str) or not filename or len(filename) > 200 or
                any(c in filename for c in '/\\:\0') or filename in {'.', '..'} or
                any(ord(c) < 32 for c in filename) or not filename.lower().endswith('.pptx')):
            raise InvalidInput('Artifact filename must be one bounded plain PPTX filename')
        return raw, item

    def _artifact_working_raw(self, parent, filename, expected=None):
        raw, identity = self._managed_raw(parent, filename, expected)
        # The Windows handle adapter already excludes multiply linked files;
        # also enforce independence on the POSIX path used by API qualification.
        try:
            info = os.stat(parent / filename, follow_symlinks=False)
        except OSError:
            raise Conflict('Artifact working copy changed during its independence check') from None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise InvalidInput('Artifact working copies must be independent regular files')
        return raw, identity

    def register_artifact(self, project_id, artifact_id, filename, source_sha256, download_url):
        """Bind a verified generic artifact, never a planning report origin."""
        raw, item = self.artifact_baseline(project_id, artifact_id, source_sha256, download_url)
        if item['filename'] != filename:
            raise Conflict('Artifact working-copy filename differs from its immutable origin')
        origin = _origin({'kind': 'GENERATED_REPORT_ARTIFACT',
            'artifact_ref': {'id': artifact_id, 'revision': 1, 'sha256': source_sha256}}, project_id)
        parent = resolve_data_path('/'.join(('native-working-copies', project_id, artifact_id,
            ARTIFACT_WORKING_COPY_PART)), self.data_dir)
        _, identity = self._artifact_working_raw(parent, filename)
        return self._register({'kind': 'ARTIFACT_WORKING_COPY', 'project_id': project_id,
            'filename': filename, 'display_path': filename, 'origin': origin,
            'artifact': {'artifact_id': artifact_id, 'kind': ARTIFACT_WORKING_COPY_PART,
                'filename': filename, 'source_sha256': source_sha256, 'download_url': download_url},
            'parent_identity': identity}, raw)

    def _saved_snapshot(self, link_ref, snapshot_ref):
        link, ref = self._link(link_ref), _ref(snapshot_ref, project=True)
        if ref['project_id'] != link['project_id']:
            raise Conflict('Frozen snapshot belongs to another project')
        directory = self._path('snapshots', link['id'], ref['id'])
        if not directory.is_dir():
            raise NotFound('Exact snapshot is not registered for this file link')
        saved = self._snapshot(directory)
        actual = {'project_id': saved['project_id'], 'id': saved['id'],
            'revision': saved['revision'], 'sha256': digest(saved)}
        if actual != ref or saved['link_ref'] != self._link_ref(link):
            raise Conflict('Frozen snapshot differs from the exact file link or revision')
        return link, saved, directory

    def context_snapshot_receipt(self, link_ref, snapshot_ref):
        """Exact historical metadata for navigation, no archived/current bytes.

        This does not verify archive bytes, parse content, or current source.
        Those remain the responsibility of the full original snapshot reader.
        """
        link, ref = self._link(link_ref), _ref(snapshot_ref, project=True)
        if ref['project_id'] != link['project_id']:
            raise Conflict('Snapshot navigation reference belongs to another project')
        directory = self._path('snapshots', link['id'], ref['id'])
        saved = self._read(directory / 'metadata.json')
        actual = {'project_id': saved['project_id'], 'id': saved['id'],
                  'revision': saved['revision'], 'sha256': digest(saved)}
        if (actual != ref or saved.get('schema_version') != 'eeas.file-continuity-snapshot.v1'
                or saved.get('link_ref') != self._link_ref(link) or not HASH.fullmatch(saved.get('current_sha256', ''))):
            raise Conflict('Snapshot navigation metadata differs from the exact registered reference')
        return {'snapshot_ref': ref, 'link_ref': self._link_ref(link), 'saved_sha256': saved['current_sha256'],
                'archive_bytes_verified': False, 'current_source_verified': False}

    def original_report_link(self, link_ref):
        """Follow only registered immutable revision provenance, never current files.

        An ordinary workspace origin cannot mint report ancestry. Each revision
        retains its own artifact hash and the precise saved parent snapshot.
        The original report service still verifies its report/source manifest
        when FrozenLinkedRequirements reads the actual requirements.
        """
        value, seen, declared = self._link(link_ref), set(), []
        for _ in range(32):
            if value['id'] in seen:
                raise Conflict('Revision ancestry contains a cycle')
            seen.add(value['id'])
            origin = value['origin']
            if value['kind'] != 'DOCX_REVISION_WORKING_COPY':
                eligible = ((value['kind'] == 'REPORT_WORKING_COPY' and origin.get('kind') == 'GENERATED_REPORT')
                    or (value['kind'] == 'WORKSPACE_FILE' and origin.get('kind') == 'GENERATED_DELIVERY_REPORT'))
                expected = origin if eligible else None
                if any(item != expected for item in declared):
                    raise Conflict('Revision original report ancestry differs')
                return self._public(value) if eligible else None
            if origin.get('kind') != 'DOCX_REVISION':
                raise Conflict('Revision working copy has an unsupported origin')
            checked = _origin(origin, value['project_id'])
            artifact, part = checked.get('artifact_ref', {}), value['request'].get('artifact', {})
            if (artifact.get('revision') != 1 or artifact.get('sha256') != value['baseline_sha256']
                    or artifact.get('id') != part.get('artifact_id') or part.get('kind') != DOCX_REVISION_PART
                    or part.get('source_sha256') != value['baseline_sha256']
                    or part.get('source_link_ref') != checked.get('source_link_ref')
                    or part.get('snapshot_ref') != checked.get('snapshot_ref')
                    or part.get('original_report_origin') != checked.get('original_report_origin')):
                raise Conflict('Revision artifact and registered ancestry differ')
            parent_ref, saved_ref = checked.get('source_link_ref'), checked.get('snapshot_ref')
            parent, _, _ = self._saved_snapshot(parent_ref, saved_ref)
            declared.append(checked.get('original_report_origin'))
            value = parent
        raise Conflict('Revision ancestry exceeds its bounded depth; history is retained')

    def original_report_origin(self, link_ref):
        original = self.original_report_link(link_ref)
        return original['origin'] if original is not None else None

    def read_snapshot_bytes(self, link_ref, snapshot_ref):
        """Read a frozen owned snapshot; do not observe or access its current file."""
        link, saved, directory = self._saved_snapshot(link_ref, snapshot_ref)
        raw = resolve_data_path(directory / 'bytes', self.data_dir).read_bytes()
        if len(raw) != saved['byte_length'] or sha(raw) != saved['current_sha256']:
            raise Conflict('Frozen snapshot changed during reading')
        return {'link_ref': self._link_ref(link), 'snapshot_ref': _ref(snapshot_ref, project=True),
            'filename': link['filename'], 'raw': raw, 'byte_length': len(raw),
            # This legacy field names the bytes at the saved observation, not
            # the file now. Explicit scope prevents a current/latest claim.
            'current_sha256': saved['current_sha256'], 'origin': link['origin'],
            'original_report_origin': self.original_report_origin(link_ref),
            'scope': 'EXACT_FROZEN_SNAPSHOT_NOT_CURRENT_FILE'}

    def revision_baseline(self, project_id, artifact_id, source_sha256, download_url):
        _id(project_id); _id(artifact_id, hashed=True); _id(source_sha256, hashed=True)
        self.store.project(project_id)
        expected = f'/api/projects/{project_id}/docx-revisions/artifacts/{artifact_id}/download'
        if download_url != expected:
            raise InvalidInput('Revision URL differs from its exact project and artifact')
        if self.revision_reader is None:
            raise Conflict('Revision continuity needs the configured immutable revision reader')
        raw, item = self.revision_reader(project_id, artifact_id)
        if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FILE_BYTES or not isinstance(item, dict)
                or item.get('project_id') != project_id or item.get('id') != artifact_id
                or type(item.get('revision')) is not int or item['revision'] != 1
                or item.get('kind') != 'docx_revision' or item.get('sha256') != source_sha256
                or item.get('media_type') != 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                or sha(raw) != source_sha256 or item.get('byte_length') != len(raw)
                or item.get('download_url') != expected or item.get('source_files_modified') is not False):
            raise Conflict('Immutable revision baseline bytes or metadata differ')
        filename = item.get('filename')
        if (not isinstance(filename, str) or not filename or len(filename) > 200
                or any(c in filename for c in '/\\:\0') or filename in {'.', '..'}
                or any(ord(c) < 32 for c in filename) or not filename.lower().endswith('.docx')):
            raise InvalidInput('Revision filename must be one bounded plain DOCX filename')
        source = self.read_snapshot_bytes(item.get('source_link_ref'), item.get('snapshot_ref'))
        if (source['current_sha256'] != item.get('source_sha256')
                or source['original_report_origin'] != item.get('original_report_origin')):
            raise Conflict('Revision original report origin differs from its exact parent snapshot')
        return raw, item

    def register_revision(self, project_id, artifact_id, filename, source_sha256, download_url):
        raw, item = self.revision_baseline(project_id, artifact_id, source_sha256, download_url)
        if item['filename'] != filename:
            raise Conflict('Revision working-copy filename differs from its immutable origin')
        ancestry = {key: item.get(key) for key in ('source_link_ref', 'snapshot_ref', 'original_report_origin')}
        origin = _origin({'kind': 'DOCX_REVISION',
            'artifact_ref': {'id': artifact_id, 'revision': 1, 'sha256': source_sha256}, **ancestry}, project_id)
        parent = resolve_data_path('/'.join(('native-working-copies', project_id, artifact_id,
            DOCX_REVISION_PART)), self.data_dir)
        _, identity = self._artifact_working_raw(parent, filename)
        receipt, receipt_identity = self._artifact_working_raw(parent, filename + '.source.json', identity)
        expected_receipt = canonical({'project_id': project_id, 'artifact_id': artifact_id,
            'source_sha256': source_sha256, 'kind': DOCX_REVISION_PART, 'download_url': download_url}).encode()
        if receipt_identity != identity or receipt != expected_receipt:
            raise Conflict('Revision working-copy receipt differs from its immutable artifact')
        return self._register({'kind': 'DOCX_REVISION_WORKING_COPY', 'project_id': project_id,
            'filename': filename, 'display_path': filename, 'origin': origin,
            'artifact': {'artifact_id': artifact_id, 'kind': DOCX_REVISION_PART,
                'filename': filename, 'source_sha256': source_sha256, 'download_url': download_url,
                **ancestry}, 'parent_identity': identity}, raw)

    def _link(self, reference):
        ref = _ref(reference, project=True)
        self.store.project(ref['project_id'])
        path = self._path('links', ref['id'] + '.json')
        if not path.exists():
            raise NotFound('Continuity link does not exist')
        value = self._read(path)
        if (self._link_ref(value) != ref or value.get('revision') != 1 or
                _request_id(value['request']) != ref['id'] or value['request']['project_id'] != ref['project_id']):
            raise Conflict('Continuity link reference, scope or request differs')
        return value

    def get(self, link_ref):
        return self._public(self._link(link_ref))

    def context_file_location(self, link_ref):
        """Logical location, separate from saved bytes and physical open proof.

        Managed file aliases share the same registered artifact/part/name.
        Their original/restored parent identity is still checked by _current.
        """
        value = self._link(link_ref)
        if value['kind'] == 'WORKSPACE_FILE':
            source = value['request']['file_source_ref']
            return self.ws.file_location(value['project_id'], source['workspace_id'], source['path'],
                binding_revision=source['binding_revision'], binding_sha256=source['binding_sha256'])
        if value['kind'] not in {'REPORT_WORKING_COPY', 'ARTIFACT_WORKING_COPY', 'DOCX_REVISION_WORKING_COPY'}:
            raise Conflict('Unsupported registered context file location')
        part = value['request'].get('report') or value['request']['artifact']
        identity = {'kind': 'REGISTERED_MANAGED_FILE', 'project_id': value['project_id'],
            'artifact_id': part['artifact_id'], 'part_kind': part['kind'], 'filename': value['filename']}
        return {**identity, 'file_key': digest(identity)}

    def resolve_open(self, link_ref):
        """Resolve only an already registered location, without copying/writing it."""
        value = self._link(link_ref)
        raw, source = self._current(value)
        if len(raw) > MAX_FILE_BYTES:
            raise InvalidInput('Continuity file exceeds 16 MiB')
        result = {**self._public(value), 'current_sha256': sha(raw),
            'working_copy_modified': sha(raw) != value['baseline_sha256'], 'file_source_ref': source}
        if source is None:
            part = value['request'].get('report') or value['request'].get('artifact')
            parent = resolve_data_path('/'.join(('native-working-copies', value['project_id'],
                part['artifact_id'], part['kind'])), self.data_dir)
            result['native_path'] = str(parent / value['filename'])
        return result

    def list_candidates(self, project_id=None, *, limit=30):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput('Candidate limit must be between 1 and 100')
        if project_id is not None:
            _id(project_id)
            self.store.project(project_id)
        candidates = []
        for path in self._links():
            value = self._read(path)
            if value.get('id') != path.stem or _request_id(value.get('request', {})) != path.stem:
                raise Conflict('Continuity candidate index integrity differs')
            if project_id is None or value['project_id'] == project_id:
                candidates.append(self._public(value))
        return {'candidates': candidates[:limit], 'total': len(candidates),
            'has_more': len(candidates) > limit, 'automatic_selection': False,
            'coverage': 'ONLY_EXPLICITLY_REGISTERED_FILES_NO_USER_DISK_SCAN'}

    def _current(self, value):
        request = value['request']
        if value['kind'] == 'WORKSPACE_FILE':
            source = request['file_source_ref']
            binding = self.ws.get(value['project_id'], source['workspace_id'], source['binding_revision'])
            if binding['binding_sha256'] != source['binding_sha256']:
                raise Conflict('Continuity workspace binding changed')
            current = self.ws.read_saved_snapshot(value['project_id'], source['workspace_id'], source['path'])
            raw = current['content'].encode() if current['encoding'] == 'utf-8' else base64.b64decode(current['content'], validate=True)
            return raw, current['source_ref']
        if value['kind'] not in {'REPORT_WORKING_COPY', 'ARTIFACT_WORKING_COPY', 'DOCX_REVISION_WORKING_COPY'}:
            raise Conflict('Unsupported managed continuity origin')
        report = request['report'] if value['kind'] == 'REPORT_WORKING_COPY' else request['artifact']
        parent = resolve_data_path('/'.join(('native-working-copies', value['project_id'],
            report['artifact_id'], report['kind'])), self.data_dir)
        reader = self._managed_raw if value['kind'] == 'REPORT_WORKING_COPY' else self._artifact_working_raw
        expected = self._restored_parent_identity(value, parent) or request['parent_identity']
        allowance = current_context_read_budget()
        if allowance is not None:
            raw = allowance.fixed(lambda: reader(parent, value['filename'], expected)[0], MAX_FILE_BYTES + 1)
        else:
            raw, _ = reader(parent, value['filename'], expected)
        return raw, None

    @staticmethod
    def _parsed(filename, raw):
        try:
            parsed = parse_document(filename, raw)
        except DocumentImportError as error:
            if Path(filename).suffix.lower() in {'.docx', '.pdf', '.pptx', '.vsdx', '.xmind', '.png', '.jpg', '.jpeg'}:
                return {'status': 'UNAVAILABLE', 'format': Path(filename).suffix[1:], 'text_content': '',
                    'comments': [], 'warnings': ['PARSER_UNAVAILABLE_OR_MALFORMED_DOCUMENT'],
                    'structure': {}, 'error_code': type(error).__name__}
            try:
                text = raw.decode('utf-8')
                if '\0' in text:
                    raise UnicodeError
            except UnicodeError:
                return {'status': 'UNAVAILABLE', 'format': 'unsupported', 'text_content': '',
                    'comments': [], 'warnings': ['NO_READONLY_CONTENT_ADAPTER'], 'structure': {},
                    'error_code': 'UNSUPPORTED_FORMAT'}
            return {'status': 'AVAILABLE', 'format': 'utf-8', 'text_content': text,
                'comments': [], 'warnings': [], 'structure': {'formatting_intent': 'NOT_INTERPRETED'}}
        nodes = parsed.graph.get('nodes', [])
        by_id = {node['id']: node for node in nodes}
        children = {}
        for node in nodes:
            children.setdefault(node.get('parent_id'), []).append(node)
        comments = []
        for node in nodes:
            if node.get('kind') != 'comment':
                continue
            pending, content = list(reversed(children.get(node['id'], []))), []
            while pending:
                child = pending.pop()
                if child.get('text'):
                    content.append(child['text'])
                pending.extend(reversed(children.get(child['id'], [])))
            anchors = [by_id[edge['source']]['source'] for edge in parsed.graph.get('edges', [])
                if edge.get('kind') == 'note-reference' and edge.get('target') == node['id'] and edge.get('source') in by_id]
            comments.append({'id': node['id'], 'logical_id': node.get('logical_id'), 'source': node['source'],
                'text': '\n'.join(content), 'anchor_sources': anchors, 'exact_text_range_verified': False})
        return {'status': 'AVAILABLE', 'format': parsed.format, 'text_content': parsed.content,
            'comments': comments, 'warnings': parsed.warnings,
            'structure': {'nodes_total': len(nodes), 'edges_total': len(parsed.graph.get('edges', [])),
                'comment_coverage': 'CLASSIC_NOTE_REFERENCES_NOT_THREADED_OR_EXACT_RANGES',
                'tracked_text': 'EXPLICIT_MARKERS_NOT_ACCEPTED', 'formatting_intent': 'NOT_INTERPRETED'}}

    @staticmethod
    def _view(parsed):
        # Separate comment budget prevents a long body hiding every tail comment.
        result = {key: parsed[key] for key in ('status', 'format', 'structure')}
        result['warnings'] = [_cut(item, 500) for item in parsed['warnings'][:16]]
        result['warnings_truncated'] = len(parsed['warnings']) > 16
        result['text_content'] = _cut(parsed['text_content'], 30000)
        result['text_length'] = len(parsed['text_content'])
        result['text_truncated'] = result['text_content'] != parsed['text_content']
        result['comments_total'], result['comments'] = len(parsed['comments']), []
        for item in parsed['comments'][:64]:
            bounded = {**item, 'text': _cut(item['text'], 6000)}
            bounded['text_truncated'] = bounded['text'] != item['text']
            if len(canonical(result['comments'] + [bounded]).encode()) > 24000:
                break
            result['comments'].append(bounded)
        result['comments_truncated'] = len(result['comments']) != len(parsed['comments']) or any(item['text_truncated'] for item in result['comments'])
        if 'error_code' in parsed:
            result['error_code'] = parsed['error_code']
        if len(canonical(result).encode()) > VIEW_BYTES:
            raise InvalidInput('Parsed continuity preview exceeds its 64 KiB limit; original snapshot is retained')
        return result

    def observe(self, link_ref):
        value = self._link(link_ref)
        raw, current_source = self._current(value)
        if len(raw) > MAX_FILE_BYTES:
            raise InvalidInput('Continuity file exceeds 16 MiB')
        parsed = self._parsed(value['filename'], raw)
        # A changed file during parsing is not falsely described as current.
        checked, checked_source = self._current(value)
        if checked != raw or checked_source != current_source:
            raise Conflict('SOURCE_DRIFT: file changed while preparing its continuity snapshot')
        signature = digest({'raw_sha256': sha(raw), 'parsed_sha256': digest(parsed)})
        directory = self._path('snapshots', value['id'], signature)
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if directory.exists():
                snapshot = self._snapshot(directory)
            else:
                parent = self._path('snapshots', value['id'])
                records = [entry for entry in parent.iterdir() if HASH.fullmatch(entry.name)] if parent.exists() else []
                if len(records) >= MAX_SNAPSHOTS:
                    raise Conflict('Continuity snapshot storage is full; old snapshots remain retained')
                revision = max((self._read(entry / 'metadata.json')['revision'] for entry in records), default=0) + 1
                snapshot = {'schema_version': 'eeas.file-continuity-snapshot.v1', 'id': signature,
                    'revision': revision, 'project_id': value['project_id'], 'link_ref': self._link_ref(value),
                    'baseline_sha256': value['baseline_sha256'], 'current_sha256': sha(raw),
                    'byte_length': len(raw), 'current_file_source_ref': current_source,
                    'status': 'UNCHANGED' if sha(raw) == value['baseline_sha256'] else 'CHANGED',
                    'observed_at': _now(), 'parsed': parsed}
                encoded_size = len((canonical({'payload': snapshot, 'sha256': digest(snapshot)}) + '\n').encode())
                if encoded_size > MAX_RECORD_BYTES:
                    raise InvalidInput('Snapshot metadata exceeds its limit; old snapshots remain retained')
                self._check_storage_budget(value['id'], len(raw) + encoded_size)
                parent.mkdir(parents=True, exist_ok=True)
                staging = self._path('snapshots', value['id'], '.pending-' + uuid.uuid4().hex)
                staging.mkdir()
                try:
                    with (staging / 'bytes').open('xb') as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    self._write(staging / 'metadata.json', snapshot)
                    staging.rename(directory)
                except Exception:
                    # Own incomplete staging is retained for diagnosis, never
                    # counted as a successful observation or deleted user data.
                    raise
        ref = {'project_id': snapshot['project_id'], 'id': snapshot['id'],
            'revision': snapshot['revision'], 'sha256': digest(snapshot)}
        return {'link_ref': snapshot['link_ref'], 'snapshot_ref': ref,
            **{key: snapshot[key] for key in ('status', 'baseline_sha256', 'current_sha256',
                'byte_length', 'observed_at')},
            'current_file_source_ref': current_source,
            'snapshot_file_source_ref': snapshot['current_file_source_ref'],
            'parsed': self._view(snapshot['parsed']), 'write_authorized': False,
            'automatic_input_authorized': False, 'unsaved_changes': 'NOT_OBSERVED',
            'coverage': 'REGISTERED_SAVED_FILE_BYTES_NOT_APPLICATION_MEMORY',
            'current_at_return_guaranteed': False}

    def _snapshot(self, directory):
        value = self._read(directory / 'metadata.json')
        if (value.get('schema_version') != 'eeas.file-continuity-snapshot.v1' or
                value.get('id') != directory.name or type(value.get('revision')) is not int or
                value['revision'] < 1 or value.get('link_ref', {}).get('id') != directory.parent.name or
                value.get('link_ref', {}).get('project_id') != value.get('project_id')):
            raise Conflict('Continuity snapshot scope or identity differs')
        self._link(value['link_ref'])
        path = resolve_data_path(directory / 'bytes', self.data_dir)
        if (not path.is_file() or path.stat().st_size != value['byte_length'] or
                value['byte_length'] > MAX_FILE_BYTES or sha(source_bytes(path, MAX_FILE_BYTES)) != value['current_sha256'] or
                digest({'raw_sha256': value['current_sha256'], 'parsed_sha256': digest(value['parsed'])}) != directory.name):
            raise Conflict('Continuity snapshot bytes or parse integrity failed')
        return value

    def _check_storage_budget(self, link_id, additional_bytes):
        """Stat only this bounded private registry, including incomplete stages.

        Called under the same SQLite writer transaction as new snapshots, so
        independent service instances cannot both spend the remaining budget.
        Metadata/parsed text is charged too; no raw/base64 doubling is hidden.
        A cache hit never needs another reservation or removes old snapshots.
        """
        folder = self._path('snapshots')
        total, own, links = 0, 0, 0
        if folder.exists():
            for project_link in folder.iterdir():
                if not HASH.fullmatch(project_link.name):
                    raise Conflict('Unexpected continuity snapshot index entry; old records retained')
                project_link = resolve_data_path(project_link, self.data_dir)
                if not project_link.is_dir():
                    raise Conflict('Continuity snapshot index is not a regular directory')
                links += 1
                if links > MAX_LINKS:
                    raise Conflict('Continuity snapshot index exceeds its bounded link count')
                snapshots = 0
                for saved in project_link.iterdir():
                    if not (HASH.fullmatch(saved.name) or re.fullmatch(r'\.pending-[a-f0-9]{32}', saved.name)):
                        raise Conflict('Unexpected continuity snapshot entry; old records retained')
                    saved = resolve_data_path(saved, self.data_dir)
                    if not saved.is_dir():
                        raise Conflict('Continuity snapshot entry is not a regular directory')
                    snapshots += 1
                    if snapshots > MAX_SNAPSHOTS * 2:
                        raise Conflict('Too many incomplete continuity snapshots; inspection required')
                    for leaf in saved.iterdir():
                        leaf = resolve_data_path(leaf, self.data_dir)
                        if leaf.name not in {'bytes', 'metadata.json'} or not leaf.is_file():
                            raise Conflict('Unexpected snapshot storage entry; inspection required')
                        size = leaf.stat().st_size
                        total += size
                        if project_link.name == link_id:
                            own += size
                        if total + additional_bytes > self.max_total_snapshot_bytes:
                            raise Conflict('CONTINUITY_GLOBAL_STORAGE_FULL: old snapshots and user files retained')
        if own + additional_bytes > self.max_link_snapshot_bytes:
            raise Conflict('CONTINUITY_LINK_STORAGE_FULL: old snapshots and user files retained')
        if total + additional_bytes > self.max_total_snapshot_bytes:
            raise Conflict('CONTINUITY_GLOBAL_STORAGE_FULL: old snapshots and user files retained')

    def context_snapshot(self, snapshot_ref, *, link_ref=None, offset=0, limit=8000, include_source_text=False):
        """Read an exact snapshot only while the registered saved bytes match.

        No new observation, snapshot, model call, or source-file write. This is
        a current saved-file check, not evidence about unsaved application edits.
        """
        ref = _ref(snapshot_ref, project=True)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
            raise InvalidInput('Context range is outside its bounded range')
        links = (self._link(link_ref),) if link_ref is not None else (self._read(path) for path in self._links())
        for link in links:
            if link['project_id'] != ref['project_id']:
                continue
            directory = self._path('snapshots', link['id'], ref['id'])
            if not directory.exists():
                continue
            candidate = self._read(directory / 'metadata.json')
            actual = {'project_id': candidate['project_id'], 'id': candidate['id'],
                      'revision': candidate['revision'], 'sha256': digest(candidate)}
            if actual != ref:
                continue  # Equal bytes in another file do not share identity.
            registered, saved, _ = self._saved_snapshot(self._link_ref(link), ref)
            if registered['kind'] == 'WORKSPACE_FILE':
                from packages.domain.local_workspaces import FileSource
                source = FileSource.model_validate(saved['current_file_source_ref'])
                checked = self.ws.check_saved_sources(ref['project_id'], [source])[0]
                if checked['status'] != 'CURRENT' or checked['live_sha256'] != saved['current_sha256']:
                    raise Conflict('SOURCE_DRIFT: context snapshot no longer matches the saved file')
            else:
                raw, _ = self._current(registered)
                if sha(raw) != saved['current_sha256']:
                    raise Conflict('SOURCE_DRIFT: context snapshot no longer matches the saved file')
            return {'snapshot_ref': ref, 'filename': registered['filename'],
                'text_content': saved['parsed']['text_content'][offset:offset + limit],
                **({'source_text_content': saved['parsed']['text_content']} if include_source_text else {}),
                'coverage': 'REGISTERED_SAVED_BYTES_NOT_UNSAVED_APPLICATION_EDITS', 'write_authorized': False}
        raise NotFound('Exact context snapshot is not registered in this project')

    def read_text(self, snapshot_ref, *, offset=0, limit=8000):
        ref = _ref(snapshot_ref, project=True)
        self.store.project(ref['project_id'])
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
            raise InvalidInput('Snapshot text pagination is outside its bounded range')
        # Only the bounded owned-link registry is searched, never the user disk.
        for path in self._links():
            link = self._read(path)
            if link['project_id'] != ref['project_id']:
                continue
            directory = self._path('snapshots', link['id'], ref['id'])
            if not directory.exists():
                continue
            value = self._snapshot(directory)
            actual = {'project_id': value['project_id'], 'id': value['id'],
                'revision': value['revision'], 'sha256': digest(value)}
            if actual != ref:
                continue
            self._link(value['link_ref'])
            text = value['parsed']['text_content']
            excerpt = text[offset:offset + limit]
            return {'snapshot_ref': actual, 'offset': offset, 'offset_unit': 'UNICODE_CODEPOINT',
                'text_content': excerpt, 'total_characters': len(text),
                'has_more': offset + len(excerpt) < len(text), 'write_authorized': False}
        raise NotFound('Exact continuity snapshot does not exist in this project')
