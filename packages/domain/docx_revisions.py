"""Exact saved-DOCX proposals and immutable redlines; never overwrite a source.

The original model loop may propose bounded ranges. Only the terminal host calls
publish. Neither a proposal nor a new artifact is acceptance or source apply.
"""
from __future__ import annotations

from copy import copy, deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import BytesIO
import json
import os
from pathlib import Path
import re
import zipfile

from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain.local_workspaces import canonical, digest, sha
from packages.domain.paths import resolve_data_path
from packages.domain.planning_docx import bounded_docx, DOCX_MIME
from packages.domain.store import Conflict, InvalidInput, NotFound

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_OPERATIONS = 1000
MAX_STORAGE_BYTES = 256 * 1024 * 1024
HASH = r'^[a-f0-9]{64}$'
ID = r'^[A-Za-z0-9_-]{1,100}$'


def q(name):
    return '{' + W + '}' + name


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ExactRef(Model):
    id: str = Field(pattern=HASH)
    revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=HASH)


class ProjectRef(ExactRef):
    project_id: str = Field(pattern=ID)


class RevisionOwner(Model):
    operation_id: str = Field(pattern=ID)
    generation: str = Field(pattern=ID)
    conversation_id: str | None = Field(default=None, pattern=ID)


class DocxPatch(Model):
    part: str = Field(default='word/document.xml', pattern=r'^word/document\.xml$')
    paragraph_path: str = Field(min_length=1, max_length=2048)
    char_start: int = Field(ge=0, strict=True)
    char_end: int = Field(ge=0, strict=True)
    quote: str = Field(max_length=8000)
    quote_sha256: str = Field(pattern=HASH)
    replacement: str = Field(max_length=8000)
    reason: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def coherent(self):
        if self.char_end < self.char_start or self.char_end - self.char_start != len(self.quote):
            raise ValueError('Range must use exact Unicode codepoint offsets')
        if sha(self.quote.encode()) != self.quote_sha256:
            raise ValueError('Quote hash differs from the supplied exact text')
        if self.quote == self.replacement:
            raise ValueError('A revision must contain an actual text change')
        if any(ord(c) < 32 and c not in '\t\n\r' for c in self.replacement):
            raise ValueError('Replacement contains XML control characters')
        if '\n' in self.replacement or '\r' in self.replacement or '\t' in self.replacement:
            raise ValueError('Paragraph splitting, tabs and line breaks need a different qualified adapter')
        return self


class DocxRevisionInput(Model):
    operation_id: str = Field(pattern=ID)
    owner: RevisionOwner
    file_link_ref: ProjectRef
    snapshot_ref: ProjectRef
    patches: list[DocxPatch] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def budget(self):
        if sum(len(p.quote) + len(p.replacement) for p in self.patches) > 64000:
            raise ValueError('The complete revision exceeds its shared text budget')
        if self.file_link_ref.project_id != self.snapshot_ref.project_id:
            raise ValueError('Snapshot and link must belong to the same project')
        return self


DOCX_PROPOSAL_SCHEMA = DocxRevisionInput.model_json_schema()


class Clarification(Conflict):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _tree(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_SOURCE_BYTES:
        raise InvalidInput('Saved DOCX exceeds the 16 MiB source budget')
    archive = bounded_docx(raw, allow_reference_hyperlinks=True)
    if any(name.startswith('_xmlsignatures/') for name in archive.data):
        raise Clarification('SIGNED_PACKAGE_NOT_SUPPORTED', 'A signed document cannot be revised without invalidating its signatures')
    # Existing reference hyperlinks are inert, but active field instructions are
    # never allowed merely because the template reader permits hyperlinks.
    for root in archive.xml.values():
        for element in root.iter():
            if element.tag in {q('instrText'), q('fldSimple')}:
                text = (element.text or '') + element.get(q('instr'), '')
                if any(token in text.upper() for token in ('DDE', 'INCLUDETEXT', 'INCLUDEPICTURE', 'DATABASE', 'MACROBUTTON', 'LINK ')):
                    raise Clarification('ACTIVE_FIELD_NOT_SUPPORTED', 'Active or external Word fields are not supported')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
        remove_blank_text=False, remove_comments=False, huge_tree=False)
    root = etree.fromstring(archive.data['word/document.xml'], parser)
    paths, pending = {}, [(root, '/document[1]')]
    while pending:
        element, path = pending.pop()
        paths[element] = path
        counts = {}
        for child in element:
            if not isinstance(child.tag, str):
                continue
            local = etree.QName(child).localname
            counts[local] = counts.get(local, 0) + 1
            pending.append((child, path + '/' + local + '[' + str(counts[local]) + ']'))
    paragraphs = {paths[element]: element for element in root.iter(q('p'))}
    return archive, root, paragraphs


def _paragraph(element):
    allowed = {q(name) for name in ('pPr', 'r', 'commentRangeStart', 'commentRangeEnd', 'bookmarkStart', 'bookmarkEnd', 'proofErr')}
    text = []
    editable, reason = True, None
    if any(e.tag in {q(name) for name in ('ins', 'del', 'moveFrom', 'moveTo', 'sdt', 'hyperlink', 'fldSimple')}
            for e in element.iterancestors()):
        editable, reason = False, 'COMPLEX_PARAGRAPH_ANCESTOR_NOT_SUPPORTED'
    if any(e.tag in {q('ins'), q('del'), q('moveFrom'), q('moveTo'), q('pPrChange'), q('rPrChange')}
            for e in element.iter()):
        editable, reason = False, 'TARGET_HAS_EXISTING_REVISIONS'
    for child in element:
        if child.tag not in allowed:
            editable, reason = False, reason or 'COMPLEX_PARAGRAPH_NOT_SUPPORTED'
        if child.tag == q('r'):
            if any(e.tag not in {q('rPr'), q('t'), q('commentReference')} for e in child):
                editable, reason = False, reason or 'COMPLEX_RUN_NOT_SUPPORTED'
            if child.find(q('t')) is not None and child.find(q('commentReference')) is not None:
                editable, reason = False, reason or 'MIXED_COMMENT_REFERENCE_RUN_NOT_SUPPORTED'
            text.extend(e.text or '' for e in child if e.tag == q('t'))
    if not any(e.tag == q('r') and e.find(q('t')) is not None for e in element):
        editable, reason = False, reason or 'EMPTY_PARAGRAPH_NOT_SUPPORTED'
    return ''.join(text), editable, reason


def _run(original, text, *, deleted=False):
    result = etree.Element(q('r'), attrib=dict(original.attrib))
    props = original.find(q('rPr'))
    if props is not None:
        result.append(deepcopy(props))
    leaf = etree.SubElement(result, q('delText') if deleted else q('t'))
    leaf.set(XML_SPACE, 'preserve')
    leaf.text = text
    return result


def _tracked(original, text, kind, number, date):
    result = etree.Element(q(kind), attrib={q('id'): str(number), q('author'): 'Cirven', q('date'): date})
    result.append(_run(original, text, deleted=kind == 'del'))
    return result


def _normalize_patches(paragraphs, patches):
    original_groups = {}
    for patch in patches:
        original_groups.setdefault(patch['paragraph_path'], []).append(patch)
    _check_overlaps(original_groups)
    result = []
    for patch in patches:
        element = paragraphs.get(patch['paragraph_path'])
        if element is None:
            raise Clarification('PARAGRAPH_NOT_FOUND', 'Exact paragraph no longer exists in the frozen snapshot')
        text, editable, reason = _paragraph(element)
        if not editable:
            raise Clarification(reason, 'This target contains an unsupported structure or existing revisions; source retained')
        start, end = patch['char_start'], patch['char_end']
        if end > len(text) or text[start:end] != patch['quote']:
            raise Clarification('QUOTE_RANGE_MISMATCH', 'The exact quote does not match this paragraph range')
        # Preserve unchanged styled runs even when the model replaces a whole
        # paragraph. This bounded diff is presentation preservation, not merging
        # later source versions or making semantic decisions.
        for kind, a, b, c, d in SequenceMatcher(None, patch['quote'], patch['replacement'], autojunk=True).get_opcodes():
            if kind != 'equal':
                result.append({**patch, 'char_start': start + a, 'char_end': start + b,
                    'quote': patch['quote'][a:b], 'quote_sha256': sha(patch['quote'][a:b].encode()),
                    'replacement': patch['replacement'][c:d]})
    grouped = {}
    for patch in result:
        grouped.setdefault(patch['paragraph_path'], []).append(patch)
    if len(result) > 256:
        raise Clarification('TOO_MANY_LOCAL_DIFFERENCES', 'Too many local differences; use a smaller explicit revision')
    _check_overlaps(grouped)
    return grouped


def _check_overlaps(grouped):
    for patches in grouped.values():
        patches.sort(key=lambda p: (p['char_start'], p['char_end']))
        for previous, current in zip(patches, patches[1:]):
            if (current['char_start'] < previous['char_end'] or
                    current['char_start'] == previous['char_start'] or
                    current['char_start'] == previous['char_end'] and (
                        current['char_start'] == current['char_end'] or previous['char_start'] == previous['char_end'])):
                raise Clarification('OVERLAPPING_RANGES', 'Proposed ranges overlap or have an ambiguous insertion boundary')


def _redline(raw, patches, date):
    archive, root, paragraphs = _tree(raw)
    grouped = _normalize_patches(paragraphs, patches)
    revision_ids = [int(e.get(q('id'))) for e in root.iter()
        if e.tag in {q('ins'), q('del')} and (e.get(q('id')) or '').isdecimal()]
    number = max(revision_ids, default=-1) + 1
    changes = []
    for path, edits in grouped.items():
        paragraph = paragraphs[path]
        children, output, position, inserted = list(paragraph), [], 0, set()
        total = len(_paragraph(paragraph)[0])
        last_text_run = next((e for e in reversed(children) if e.tag == q('r') and e.find(q('t')) is not None), None)
        for child in children:
            if child.tag != q('r') or child.find(q('t')) is None:
                output.append(child)
                continue
            text = ''.join(e.text or '' for e in child if e.tag == q('t'))
            end = position + len(text)
            active = [(i, p) for i, p in enumerate(edits) if
                p['char_start'] < end and p['char_end'] > position or
                p['char_start'] == p['char_end'] and position <= p['char_start'] < end]
            if not active:
                output.append(child)
            else:
                boundaries = sorted({0, len(text), *[max(0, min(len(text), p[key] - position))
                    for _, p in active for key in ('char_start', 'char_end')]})
                for a, b in zip(boundaries, boundaries[1:]):
                    for i, patch in active:
                        if i not in inserted and patch['char_start'] == patch['char_end'] == position + a:
                            if patch['replacement']:
                                output.append(_tracked(child, patch['replacement'], 'ins', number, date)); number += 1
                            inserted.add(i)
                    covered = next(((i, p) for i, p in active if p['char_start'] <= position + a < p['char_end']), None)
                    if covered:
                        i, patch = covered
                        output.append(_tracked(child, text[a:b], 'del', number, date)); number += 1
                        if i not in inserted:
                            if patch['replacement']:
                                output.append(_tracked(child, patch['replacement'], 'ins', number, date)); number += 1
                            inserted.add(i)
                    elif b > a:
                        output.append(_run(child, text[a:b]))
            if child is last_text_run:
                for i, patch in enumerate(edits):
                    if i not in inserted and patch['char_start'] == patch['char_end'] == total:
                        output.append(_tracked(child, patch['replacement'], 'ins', number, date)); number += 1
                        inserted.add(i)
            position = end
        if len(inserted) != len(edits):
            raise Clarification('RANGE_NOT_RESOLVED', 'Not every exact range resolved to a supported text run')
        paragraph[:] = output
        changes.extend({key: patch[key] for key in ('part', 'paragraph_path', 'char_start', 'char_end', 'quote', 'quote_sha256', 'replacement', 'reason')} for patch in edits)
    changed = etree.tostring(root.getroottree(), xml_declaration=True, encoding='UTF-8', standalone=True)
    stream = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as source, zipfile.ZipFile(stream, 'w') as target:
        for info in source.infolist():
            target.writestr(copy(info), changed if info.filename == 'word/document.xml' else archive.data[info.filename])
        target.comment = source.comment
    output = stream.getvalue()
    if len(output) > MAX_SOURCE_BYTES:
        raise InvalidInput('Derived DOCX exceeds its 16 MiB artifact budget')
    return output, changes


class DocxRevisions:
    def __init__(self, store, data_dir, *, snapshot_reader, owner_checker=None,
                 max_operations=MAX_OPERATIONS, max_storage_bytes=MAX_STORAGE_BYTES):
        self.store, self.data_dir = store, Path(data_dir)
        self.snapshot_reader, self.owner_checker = snapshot_reader, owner_checker
        if type(max_operations) is not int or not 1 <= max_operations <= MAX_OPERATIONS:
            raise InvalidInput('Invalid DOCX operation limit')
        if type(max_storage_bytes) is not int or not 1 <= max_storage_bytes <= MAX_STORAGE_BYTES:
            raise InvalidInput('Invalid DOCX storage budget')
        self.max_operations, self.max_storage_bytes = max_operations, max_storage_bytes
        if self.data_dir.is_symlink():
            raise InvalidInput('DOCX registry data root must be ordinary')

    def _path(self, pid, *parts):
        if not isinstance(pid, str) or not re.fullmatch(ID, pid):
            raise InvalidInput('Exact project required')
        return resolve_data_path('/'.join(('docx-revisions', pid, *parts)), self.data_dir)

    def _owner(self, pid, owner):
        owner = RevisionOwner.model_validate(owner).model_dump()
        if not callable(self.owner_checker) or self.owner_checker(pid, owner) is not True:
            raise Conflict('Current original question owner/generation is not admitted for DOCX publication')
        return owner

    def _source(self, pid, link, snapshot):
        link, snapshot = ProjectRef.model_validate(link).model_dump(), ProjectRef.model_validate(snapshot).model_dump()
        self.store.project(pid)
        if link['project_id'] != pid or snapshot['project_id'] != pid:
            raise Conflict('DOCX source belongs to another project')
        value = self.snapshot_reader(link, snapshot)
        raw = value.get('raw')
        if (value.get('link_ref') != link or value.get('snapshot_ref') != snapshot or
                not isinstance(raw, bytes) or value.get('current_sha256') != sha(raw)):
            raise Conflict('Exact frozen DOCX snapshot integrity differs')
        filename = value.get('filename', '')
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.lower().endswith('.docx') or any(c in filename for c in '\\/:\x00'):
            raise InvalidInput('A bounded ordinary DOCX filename is required')
        return value

    def context(self, pid, link_ref, snapshot_ref, *, offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 30:
            raise InvalidInput('DOCX paragraph pagination is outside its budget')
        source = self._source(pid, link_ref, snapshot_ref)
        _, _, paragraphs = _tree(source['raw'])
        items, budget, selected = [], 0, list(paragraphs.items())
        for path, element in selected[offset:offset + limit]:
            text, editable, reason = _paragraph(element)
            if len(text) > 8000:
                editable, reason = False, 'PARAGRAPH_EXCEEDS_PATCH_BUDGET'
            if items and budget + len(text[:8000]) > 16000:
                break
            item = {'part': 'word/document.xml', 'paragraph_path': path, 'text': text[:8000],
                'text_sha256': sha(text.encode()), 'text_truncated': len(text) > 8000,
                'editable': editable, 'reason': reason,
                'comment_ids': [e.get(q('id')) for e in element.iter() if e.tag == q('commentRangeStart')]}
            if editable:
                item['anchor'] = {'part': 'word/document.xml', 'paragraph_path': path,
                    'char_start': 0, 'char_end': len(text), 'quote': text, 'quote_sha256': sha(text.encode())}
            items.append(item); budget += len(item['text'])
        return {'file_link_ref': link_ref, 'snapshot_ref': snapshot_ref,
            'source_sha256': source['current_sha256'], 'paragraphs': items,
            'total_paragraphs': len(selected), 'next_offset': offset + len(items) if offset + len(items) < len(selected) else None,
            'coverage': 'SAVED_MAIN_DOCUMENT_PARAGRAPHS_NOT_UNSAVED_WORD_BUFFER', 'model_calls': 0}

    def locate(self, pid, link_ref, snapshot_ref, *, paragraph_path, quote):
        if not isinstance(quote, str) or not 1 <= len(quote) <= 8000:
            raise InvalidInput('A bounded exact quote is required')
        source = self._source(pid, link_ref, snapshot_ref)
        _, _, paragraphs = _tree(source['raw'])
        if paragraph_path not in paragraphs:
            raise Clarification('PARAGRAPH_NOT_FOUND', 'Copy an exact paragraph anchor from the current snapshot context')
        text, editable, reason = _paragraph(paragraphs[paragraph_path])
        if not editable:
            raise Clarification(reason, 'This paragraph needs another qualified editing adapter')
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            raise Clarification('QUOTE_NOT_UNIQUE', 'The quote is absent or ambiguous within this exact paragraph')
        return {'part': 'word/document.xml', 'paragraph_path': paragraph_path,
            'char_start': start, 'char_end': start + len(quote), 'quote': quote, 'quote_sha256': sha(quote.encode())}

    @staticmethod
    def _write(path, value):
        raw = (canonical({'payload': value, 'sha256': digest(value)}) + '\n').encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise InvalidInput('DOCX receipt exceeds its metadata budget')
        with path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())

    @staticmethod
    def _read(path):
        if not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
            raise Conflict('DOCX receipt is unavailable or outside its metadata budget')
        try:
            wrapped = json.loads(path.read_bytes()); value = wrapped['payload']
            if digest(value) != wrapped['sha256']:
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError, RecursionError):
            raise Conflict('DOCX receipt integrity failed') from None

    def _capacity(self, pid, size=0, *, new_operation=True):
        root = self._path(pid)
        total, count = 0, 0
        if root.exists():
            for path in root.rglob('*'):
                if path.is_symlink():
                    raise Conflict('DOCX registry contains a non-ordinary entry')
                if path.is_file():
                    total += path.stat().st_size
                    if path.name == 'claim.json': count += 1
        if (new_operation and count >= self.max_operations) or total + size > self.max_storage_bytes:
            raise Conflict('DOCX registry capacity reached; old records and source retained')

    def _operation(self, pid, operation_id, method, body, build):
        if not re.fullmatch(ID, operation_id):
            raise InvalidInput('Bounded exact operation ID required')
        request_sha = digest({'method': method, 'body': body})
        directory = self._path(pid, 'operations', digest(operation_id))
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if directory.exists():
                claim = self._read(directory / 'claim.json')
                if claim['request_sha256'] != request_sha or claim['method'] != method:
                    raise Conflict('Original DOCX operation is bound to different input')
                return self.operation(pid, operation_id)
            self._capacity(pid, len(canonical(body).encode()) * 3)
            directory.mkdir(parents=True)
            self._write(directory / 'claim.json', {'operation_id': operation_id, 'project_id': pid,
                'method': method, 'request_sha256': request_sha, 'body': body})
            result = build()
            result = {**result, 'operation_id': operation_id, 'request_sha256': request_sha, 'automatic_retry': False}
            self._write(directory / 'result.json', result)
            return result

    def prepare(self, pid, body):
        body = DocxRevisionInput.model_validate(body).model_dump()
        self._owner(pid, body['owner'])
        source = self._source(pid, body['file_link_ref'], body['snapshot_ref'])
        def build():
            created = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
            value = {'schema_version': 'eeas.docx-revision-proposal.v1', 'project_id': pid,
                'id': digest([pid, body['operation_id']]), 'revision': 1, 'owner': body['owner'],
                'file_link_ref': body['file_link_ref'], 'snapshot_ref': body['snapshot_ref'],
                'source_sha256': source['current_sha256'], 'source_filename': source['filename'],
                'original_report_origin': source.get('original_report_origin'),
                'created_at': created, 'patches': body['patches'], 'source_files_modified': False}
            try:
                output, changes = _redline(source['raw'], body['patches'], created)
                value.update(status='PROPOSED', changes=changes, expected_artifact_sha256=sha(output))
            except Clarification as error:
                value.update(status='NEEDS_CLARIFICATION', code=error.code, reason=str(error), changes=[])
            self._capacity(pid, len(canonical(value).encode()) + 200, new_operation=False)
            directory = self._path(pid, 'proposals', value['id']); directory.mkdir(parents=True)
            self._write(directory / 'proposal.json', value)
            return {'status': value['status'], 'proposal_ref': {'id': value['id'], 'revision': 1, 'sha256': digest(value)},
                **{key: value[key] for key in ('code', 'reason') if key in value},
                'changes': value['changes'], 'source_files_modified': False, 'artifact_created': False}
        return self._operation(pid, body['operation_id'], 'PREPARE', body, build)

    def proposal(self, pid, proposal_ref):
        ref = ExactRef.model_validate(proposal_ref).model_dump()
        value = self._read(self._path(pid, 'proposals', ref['id'], 'proposal.json'))
        if value['project_id'] != pid or value['id'] != ref['id'] or value['revision'] != ref['revision'] or digest(value) != ref['sha256']:
            raise Conflict('DOCX proposal exact reference differs')
        return value

    def publish(self, pid, proposal_ref, *, operation_id, owner):
        owner = self._owner(pid, owner)
        proposal = self.proposal(pid, proposal_ref)
        if proposal['owner'] != owner:
            raise Conflict('DOCX proposal belongs to another original question or generation')
        if proposal['status'] != 'PROPOSED':
            raise Conflict('A proposal needing clarification cannot publish an artifact')
        body = {'proposal_ref': ExactRef.model_validate(proposal_ref).model_dump(), 'owner': owner}
        def build():
            source = self._source(pid, proposal['file_link_ref'], proposal['snapshot_ref'])
            if source['current_sha256'] != proposal['source_sha256']:
                raise Conflict('Frozen DOCX publication source differs from its prepared snapshot')
            raw, changes = _redline(source['raw'], proposal['patches'], proposal['created_at'])
            if sha(raw) != proposal['expected_artifact_sha256'] or changes != proposal['changes']:
                raise Conflict('DOCX redline differs from its prepared deterministic proposal')
            self._capacity(pid, len(raw) + len(canonical(proposal).encode()) * 2, new_operation=False)
            aid = digest([pid, operation_id])
            manifest = {'schema_version': 'eeas.docx-revision-artifact.v1', 'kind': 'docx_revision',
                'id': aid, 'project_id': pid, 'revision': 1, 'sha256': sha(raw), 'byte_length': len(raw),
                'filename': 'revision-' + aid[:12] + '.docx', 'media_type': DOCX_MIME,
                'download_url': f'/api/projects/{pid}/docx-revisions/artifacts/{aid}/download',
                'source_link_ref': proposal['file_link_ref'], 'snapshot_ref': proposal['snapshot_ref'],
                'source_sha256': proposal['source_sha256'], 'owner': owner, 'proposal_ref': body['proposal_ref'],
                'original_report_origin': proposal['original_report_origin'], 'created_at': proposal['created_at'],
                'review': {'changes': changes, 'mode': 'WORD_TRACKED_INSERTIONS_AND_DELETIONS',
                    'replacement_style': 'FIRST_CHANGED_RUN_PROPERTIES_UNCHANGED_RUNS_RETAINED',
                    'semantic_acceptance': 'NOT_PERFORMED', 'native_word_rendering': 'NOT_RUN'},
                'coverage': 'EXACT_FROZEN_SAVED_SNAPSHOT_LATER_HUMAN_SAVES_NOT_INCLUDED',
                'later_source_edits_included': False, 'source_files_modified': False,
                'execution_effects': 'CREATE_NEW_DERIVED_DOCX', 'changes_applied': False}
            # The host also serializes its owner lifecycle. Recheck after local
            # parsing/diff work, before any externally reviewable artifact bytes.
            self._owner(pid, owner)
            directory = self._path(pid, 'artifacts', aid); directory.mkdir(parents=True)
            with (directory / 'revision.docx').open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            self._write(directory / 'manifest.json', manifest)
            return {'status': 'NEW_ARTIFACT', 'owner': owner, 'proposal_ref': body['proposal_ref'], 'artifact': manifest,
                'source_files_modified': False, 'execution_effects': 'CREATE_NEW_DERIVED_DOCX', 'changes_applied': False}
        return self._operation(pid, operation_id, 'PUBLISH', body, build)

    def operation(self, pid, operation_id):
        self.store.project(pid)
        if not isinstance(operation_id, str) or not re.fullmatch(ID, operation_id):
            raise InvalidInput('Exact operation ID required')
        directory = self._path(pid, 'operations', digest(operation_id))
        if not directory.exists():
            return {'status': 'NOT_ADMITTED', 'operation_id': operation_id, 'automatic_retry': False}
        try:
            claim = self._read(directory / 'claim.json')
            if claim['project_id'] != pid or claim['operation_id'] != operation_id or digest({'method': claim['method'], 'body': claim['body']}) != claim['request_sha256']:
                raise Conflict('Original operation integrity differs')
            if not (directory / 'result.json').exists():
                return {'status': 'UNKNOWN_OUTCOME', 'operation_id': operation_id, 'request_sha256': claim['request_sha256'], 'automatic_retry': False}
            result = self._read(directory / 'result.json')
            if result['operation_id'] != operation_id or result['request_sha256'] != claim['request_sha256']:
                raise Conflict('Original result binding differs')
            if result['status'] == 'NEW_ARTIFACT':
                manifest = self.get(pid, result['artifact']['id'])
                if manifest != result['artifact']:
                    raise Conflict('Original artifact binding differs')
            return result
        except (Conflict, OSError, ValueError, KeyError):
            return {'status': 'UNKNOWN_OUTCOME', 'operation_id': operation_id, 'automatic_retry': False,
                'code': 'ORIGINAL_DOCX_RECEIPT_UNAVAILABLE'}

    def get(self, pid, artifact_id):
        self.store.project(pid)
        if not isinstance(artifact_id, str) or not re.fullmatch(HASH, artifact_id):
            raise InvalidInput('Exact DOCX artifact ID required')
        directory = self._path(pid, 'artifacts', artifact_id)
        if not directory.exists():
            raise NotFound('DOCX revision artifact is not present in this project')
        value = self._read(directory / 'manifest.json')
        raw_path = directory / 'revision.docx'
        if (value.get('kind') != 'docx_revision' or value.get('project_id') != pid or
                value.get('id') != artifact_id or value.get('revision') != 1 or value.get('media_type') != DOCX_MIME or
                not raw_path.is_file() or raw_path.is_symlink() or raw_path.stat().st_size != value.get('byte_length') or
                raw_path.stat().st_size > MAX_SOURCE_BYTES or sha(raw_path.read_bytes()) != value.get('sha256')):
            raise Conflict('DOCX artifact exact project/manifest/bytes integrity differs')
        return value

    def download(self, pid, artifact_id):
        manifest = self.get(pid, artifact_id)
        raw = self._path(pid, 'artifacts', artifact_id, 'revision.docx').read_bytes()
        if sha(raw) != manifest['sha256']:
            raise Conflict('DOCX artifact changed while downloading')
        return raw, manifest
