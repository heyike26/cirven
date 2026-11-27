"""Freeze host-owned page/file data for a selected capability task."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field

from packages.adapters.native_file_view import selected_file_bytes
from packages.domain.discovery_schema import RevisionRef
from packages.domain.document_import import DocumentImportError, image_metadata
from packages.domain.file_continuity import FileContinuity
from packages.domain.local_workspaces import canonical, digest
from packages.domain.questions import RequestModel
from packages.domain.store import Conflict


class _ImageRef(RevisionRef):
    width: int = Field(strict=True, ge=1)
    height: int = Field(strict=True, ge=1)


class _Visual(RequestModel):
    context_ref: RevisionRef
    frame_ref: RevisionRef
    image_ref: _ImageRef
    pixel_source: Literal['EXACT_TARGET_WINDOW', 'VERIFIED_TARGET_PIXELS', 'UNKNOWN']


def _bounded(text, characters, byte_limit=64000):
    part = text[:characters]
    return part.encode('utf-8')[:byte_limit].decode('utf-8', errors='ignore')


def _text_sha(text):
    return sha256(text.encode('utf-8')).hexdigest()


def _visual(snapshot, reference):
    observation, preview = snapshot['observation'], snapshot.get('preview') or {}
    frame = observation.get('observations', {}).get('visual')
    if not frame or preview.get('status') != 'AVAILABLE':
        return None
    image = preview['image_ref']
    source = preview.get('pixel_source', 'UNKNOWN')
    if source not in {'EXACT_TARGET_WINDOW', 'VERIFIED_TARGET_PIXELS'}:
        source = 'UNKNOWN'
    value = _Visual.model_validate({'context_ref': reference, 'frame_ref': frame['frame_ref'],
        'image_ref': {key: image[key] for key in ('id', 'revision', 'sha256', 'width', 'height')},
        'pixel_source': source}).model_dump()
    original = frame.get('image_ref', {})
    if original.get('sha256') != value['image_ref']['sha256']:
        raise Conflict('Frozen page preview differs from its original frame')
    for key in ('width', 'height'):
        size = frame.get(key, original.get(key))
        if type(size) is not int or size != value['image_ref'][key]:
            raise Conflict('Frozen page preview dimensions differ from its original frame')
    return value


def read_capability_image(native, visual):
    value = _Visual.model_validate(visual).model_dump()
    lookup = getattr(native.context, 'frozen_lookup', native.context.lookup)
    snapshot = lookup(value['context_ref'])
    if snapshot['observation'].get('context_ref') != value['context_ref']:
        raise Conflict('Frozen capability context differs')
    if _visual(snapshot, value['context_ref']) != value:
        raise Conflict('Capability image is not the exact frozen page preview')
    image = value['image_ref']
    try:
        reader = getattr(native.context, 'frozen_image', native.context.image)
        raw = reader({key: image[key] for key in ('id', 'revision', 'sha256')})
        if not isinstance(raw, bytes) or len(raw) > 16 * 1024 * 1024:
            raise Conflict('Capability image exceeds its bounded byte limit')
        actual = image_metadata(raw)
    except DocumentImportError as error:
        raise Conflict('Capability image bytes are unavailable or corrupt') from error
    if (actual['media_type'] != 'image/png'
            or any(actual[key] != image[key] for key in ('sha256', 'width', 'height'))):
        raise Conflict('Capability image bytes differ from the exact frozen reference')
    return raw


def _comments(parsed, warnings):
    if parsed['status'] != 'AVAILABLE':
        return {'items': [], 'total': None, 'truncated': True, 'coverage': 'UNAVAILABLE'}
    comments = parsed.get('comments', [])
    if not comments and parsed['format'] not in {'docx', 'markdown', 'text', 'utf-8', 'image'}:
        warnings.append('FILE_COMMENT_EXTRACTION_UNAVAILABLE_FOR_THIS_FORMAT')
        return {'items': [], 'total': None, 'truncated': True, 'coverage': 'UNAVAILABLE'}
    total = parsed.get('comments_total', len(comments))
    coverage = ('CLASSIC_NOTE_REFERENCES_NOT_THREADED_OR_EXACT_RANGES'
        if parsed['format'] == 'docx' or comments else 'NOT_APPLICABLE')
    items, text_left, locator_left = [], 6000, 6000
    truncated = parsed.get('comments_truncated', False)
    for comment in comments[:16]:
        if not text_left:
            break
        text = _bounded(comment['text'], min(text_left, 2000))
        locator = canonical({key: comment.get(key) for key in ('source', 'anchor_sources', 'logical_id')})
        if len(locator) > 1500 or len(locator.encode('utf-8')) > locator_left:
            locator = canonical({'coverage': 'LOCATOR_EXCEEDS_BUDGET'})
            truncated = True
        if len(locator.encode('utf-8')) > locator_left:
            break
        text_truncated = comment.get('text_truncated', False) or text != comment['text']
        items.append({'id': comment['id'], 'text': text, 'text_sha256': _text_sha(text),
            'text_truncated': text_truncated, 'source_locator_json': locator,
            'exact_text_range_verified': False})
        text_left -= len(text)
        locator_left -= len(locator.encode('utf-8'))
        truncated = truncated or text_truncated
    truncated = truncated or len(items) < total
    if truncated:
        warnings.append('FILE_COMMENTS_OR_LOCATORS_TRUNCATED')
    return {'items': items, 'total': total, 'truncated': bool(truncated), 'coverage': coverage}


def _file(native, body, warnings):
    source, link = body.get('file_source_ref'), body.get('file_link_ref')
    if source is not None and link is not None:
        raise Conflict('Capability task needs one exact file source')
    if source is None and link is None:
        return None
    project = source['project_id'] if source is not None else link['project_id']
    if body.get('project_id') is not None and body['project_id'] != project:
        raise Conflict('Capability file belongs to another project')
    if source is not None:
        source, raw = selected_file_bytes(native.app.state.local_workspaces, source)
        parsed = FileContinuity._parsed(Path(source['path']).name, raw)
        snapshot_ref, source_sha, kind = None, sha256(raw).hexdigest(), 'LOCAL_FILE'
        content, total = parsed['text_content'], len(parsed['text_content'])
    else:
        continuity = native.app.state.file_continuity
        saved = continuity.observe(link)
        if saved['link_ref'] != link or saved['snapshot_ref']['project_id'] != project:
            raise Conflict('Capability file snapshot belongs to another exact link')
        snapshot_ref, source_sha, kind = saved['snapshot_ref'], saved['current_sha256'], 'REGISTERED_FILE'
        parsed = saved['parsed']
        excerpt = continuity.read_text(snapshot_ref, offset=0, limit=16000)
        if excerpt['snapshot_ref'] != snapshot_ref or excerpt['offset'] != 0:
            raise Conflict('Capability file text differs from its frozen snapshot')
        content, total = excerpt['text_content'], excerpt['total_characters']
    warnings.extend(parsed.get('warnings', []))
    available = parsed['status'] == 'AVAILABLE' and parsed['format'] != 'image'
    if available:
        text = _bounded(content, 16000)
        truncated = len(text) < total
    else:
        text, total, truncated = '', None, True
        warnings.append('FILE_TEXT_UNAVAILABLE;_SAVED_BYTES_ARE_NOT_UNSAVED_APPLICATION_CONTENT')
    return {'kind': kind, 'file_source_ref': deepcopy(source), 'file_link_ref': deepcopy(link),
        'snapshot_ref': deepcopy(snapshot_ref), 'source_sha256': source_sha,
        'text': text, 'text_sha256': _text_sha(text), 'offset': 0,
        'returned_characters': len(text), 'total_characters': total,
        'content_truncated': truncated,
        'coverage': 'EXACT_SAVED_FILE_TEXT' if available else 'UNAVAILABLE',
        'unsaved_buffer_verified': False, 'comments': _comments(parsed, warnings)}


def freeze_capability_context(native, body, profile_ref):
    profile = RevisionRef.model_validate(profile_ref).model_dump()
    if profile != body.get('provider_profile_ref'):
        raise Conflict('Capability context provider differs from the exact request')
    reference = RevisionRef.model_validate(body['context_ref']).model_dump()
    snapshot = (native.input_sources.lookup(reference) if body.get('input_attachments') else native.context.lookup(reference))
    observation = snapshot['observation']
    if observation.get('context_ref') != reference or observation.get('automatic_input_authorized') is not False:
        raise Conflict('Capability page observation differs or carries input authority')
    warnings = []
    complete = canonical(observation)
    page = _bounded(complete, 24000)
    visual = None
    provider = native.transport.provider_registry.resolve(profile)
    if not provider.supports_images:
        warnings.append('PROVIDER_IMAGE_INPUT_NOT_DECLARED;_ONLY_FROZEN_OBSERVATION_TEXT_INCLUDED')
    else:
        visual = _visual(snapshot, reference)
        if visual is None:
            warnings.append('NO_EXACT_FROZEN_PAGE_IMAGE_AVAILABLE')
        else:
            read_capability_image(native, visual)
            if visual['pixel_source'] == 'UNKNOWN':
                warnings.append('FRAME_PIXEL_TARGET_COVERAGE_UNVERIFIED')
    file = _file(native, body, warnings)
    if file is not None:
        warnings.append('SAVED_FILE_CONTENT_DOES_NOT_VERIFY_UNSAVED_APPLICATION_BUFFER')
    return {'schema': 'eeas.capability-task-context.v1', 'context_ref': reference,
        'observation_sha256': digest(observation),
        'page': {'text': page, 'text_sha256': _text_sha(page), 'truncated': page != complete,
            'coverage': 'BOUNDED_FROZEN_OBSERVATION', 'whole_page_seen': False},
        'file': file, 'visual': visual, 'warnings': list(dict.fromkeys(_bounded(str(w), 240) for w in warnings))[:16]}
