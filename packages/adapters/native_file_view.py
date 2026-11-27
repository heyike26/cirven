"""Bounded reads of an explicitly selected file, separate from window identity."""
import base64
import hashlib
from io import BytesIO
from pathlib import Path

from mcp.types import CallToolResult, ImageContent, TextContent
from PIL import Image

from packages.domain.document_import import parse_document, image_metadata, DocumentImportError
from packages.domain.local_workspaces import FileSource, canonical
from packages.domain.store import Conflict


def selected_file_bytes(workspaces, reference):
    ref = FileSource.model_validate(reference).model_dump()
    binding = workspaces.get(ref['project_id'], ref['workspace_id'])
    if binding['revision'] != ref['binding_revision'] or binding['binding_sha256'] != ref['binding_sha256']:
        raise Conflict('Selected file workspace binding changed')
    value = workspaces.read(ref['project_id'], ref['workspace_id'], ref['path'], ref['revision'])
    if not value['current'] or value['source_ref'] != ref:
        raise Conflict('Selected file version changed; no current-page/file association was inferred')
    if value['byte_length'] > 16 * 1024 * 1024:
        raise Conflict('Selected file exceeds the readonly 16 MiB limit')
    raw = value['content'].encode('utf-8') if value['encoding'] == 'utf-8' else base64.b64decode(value['content'], validate=True)
    if len(raw) != value['byte_length'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise Conflict('Selected file bytes differ from the exact reference')
    return ref, raw


def selected_file_view(workspaces, reference, *, supports_images=False):
    ref, raw = selected_file_bytes(workspaces, reference)
    try:
        parsed = parse_document(Path(ref['path']).name, raw)
    except DocumentImportError:
        # Source code and other genuine UTF-8 files do not need a document parser.
        try: text = raw.decode('utf-8')
        except UnicodeError: raise Conflict('This binary format has no readonly content adapter') from None
        if '\0' in text or Path(ref['path']).suffix.lower() in {'.exe','.com','.zip','.msi'}:
            raise Conflict('This binary format has no readonly content adapter')
        content, format_name, warnings = text, 'utf-8', []
    else:
        content, format_name, warnings = parsed.content, parsed.format, parsed.warnings
    value = {'file_source_ref': ref, 'scope': 'EXPLICIT_SELECTED_FILE_READONLY',
        'native_document_window_identity_verified': False, 'page_position_verified': False,
        'engineering_fact_accepted': False, 'automatic_input_authorized': False,
        'text_content': content[:64000], 'content_truncated': len(content) > 64000,
        'character_limit': 64000, 'format': format_name, 'warnings': warnings,
        'file_mutation_adapter': 'NOT_ENABLED_BY_READONLY_QUESTION'}
    if format_name != 'image' or not supports_images:
        return value
    original = image_metadata(raw)
    with Image.open(BytesIO(raw)) as opened:
        picture = opened.convert('RGB')
        picture.thumbnail((1280,1280))
        output = BytesIO(); picture.save(output,format='PNG')
        preview = output.getvalue()
    if len(preview) > 3 * 1024 * 1024:
        raise Conflict('Selected image view exceeds its bounded model input limit')
    value['image_view'] = {'original_sha256':original['sha256'], 'original_width':original['width'],
        'original_height':original['height'], 'view_width':picture.width, 'view_height':picture.height,
        'view_sha256':hashlib.sha256(preview).hexdigest(), 'transform':'FIT_WITHIN_1280_NO_UPSCALE',
        'source':'EXPLICIT_SELECTED_FILE_NOT_NATIVE_WINDOW_FRAME'}
    return CallToolResult(content=[TextContent(type='text',text=canonical(value)),
        ImageContent(type='image',mimeType='image/png',data=base64.b64encode(preview).decode('ascii'))])
