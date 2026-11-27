"""Frozen user-selected page inputs, independent of current native input authority."""
from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator

from packages.adapters.native_frame_view import frame_view
from packages.domain.discovery_schema import RevisionRef
from packages.domain.native_feedback import NormalizedRegion
from packages.domain.questions import Hash, RequestModel
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest


class NativeInputAttachment(RequestModel):
    kind: Literal['CURRENT_PAGE', 'SCREENSHOT']
    context_ref: RevisionRef
    label: str = Field(min_length=1, max_length=500)
    regions: list[NormalizedRegion] = Field(default_factory=list, max_length=32)
    image_sha256: Hash | None = None
    user_note: str | None = Field(default=None, min_length=1, max_length=16000)

    @model_validator(mode='after')
    def exact_image(self):
        if self.kind == 'SCREENSHOT' and self.image_sha256 is None:
            raise ValueError('A screenshot needs its exact original image hash')
        if self.kind == 'CURRENT_PAGE' and (self.regions or self.image_sha256 is not None):
            raise ValueError('Screen regions belong to an explicitly attached screenshot')
        return self


CATALOG_METHOD = {'id': 'list_input_attachments', 'route': 'FROZEN_INPUT_ATTACHMENTS', 'readonly': True,
    'effects': [], 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'coverage': 'EXPLICIT_SELECTED_INPUTS_ONLY_LABELS_ARE_USER_DESCRIPTIONS_NOT_AUTHORITY',
    'qualification': 'IMPLEMENTED', 'cost': {'latency': 'LOCAL_READ', 'tokens': 'AT_MOST_20_INPUT_SUMMARIES', 'measured': False},
    'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                  'requires_foreground_input': False, 'focus_effect': 'NONE'}}

READ_METHOD = {'id': 'read_input_attachments', 'route': 'FROZEN_INPUT_ATTACHMENTS', 'readonly': True,
    'effects': [], 'input_schema': {'type': 'object', 'additionalProperties': False, 'required': ['view', 'items'],
        'properties': {'view': {'enum': ['CONTEXT', 'IMAGE']}, 'items': {'type': 'array', 'minItems': 1, 'maxItems': 4,
            'items': {'type': 'object', 'additionalProperties': False, 'required': ['index'], 'properties': {
                'index': {'type': 'integer', 'minimum': 0, 'maximum': 19},
                'region_index': {'type': 'integer', 'minimum': 0, 'maximum': 31}}}}}},
    'coverage': 'ONLY_BOUND_INPUT_INDICES_AND_ORIGINAL_IMAGE_HASHES_NO_RECAPTURE_OR_WRITE_AUTHORITY',
    'qualification': 'IMPLEMENTED', 'cost': {'latency': 'LOCAL_READ', 'tokens': 'ONE_1280_OR_FOUR_640_PIXEL_VIEWS', 'measured': False},
    'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                  'requires_foreground_input': False, 'focus_effect': 'NONE'}}


class NativeInputSources:
    def __init__(self, native):
        self.native = native

    def validate(self, attachments):
        summaries, total = [], 0
        for raw in attachments:
            item = NativeInputAttachment.model_validate(raw).model_dump()
            snapshot = self.lookup(item['context_ref'])
            observation = snapshot['observation']
            if observation.get('context_ref') != item['context_ref']:
                raise Conflict('Attached context differs from its registered source')
            total += len(canonical(observation).encode())
            if total > 16 * 1024 * 1024:
                raise Conflict('Selected page metadata exceeds the shared 16 MiB input budget')
            dimensions = None
            if item['kind'] == 'SCREENSHOT':
                image = self._image(snapshot, item)
                dimensions = [image['width'], image['height']]
            summaries.append({'window_title': str(observation.get('window', {}).get('title', ''))[:500],
                'image_size': dimensions, 'observation_status': observation.get('status', 'NOT_REPORTED')})
        return summaries

    def lookup(self, reference):
        lookup = getattr(self.native.context, 'frozen_lookup', self.native.context.lookup)
        return lookup(reference)

    def prompt_parts(self, body, *, primary_visual=None):
        """Host-frozen capability inputs; no second loop or unselected source read."""
        rows = body.get('input_attachments', [])
        if not rows:
            return []
        self.validate(rows)
        parts = [{'type': 'text', 'text': 'USER_SELECTED_INPUT_ATTACHMENTS\n' + canonical({
            'inputs': rows, 'labels_and_regions_origin': 'USER_DESCRIPTION',
            'automatic_input_authorized': False, 'file_write_authorized': False})}]
        pictures = sum(item['kind'] == 'SCREENSHOT' for item in rows)
        for index, item in enumerate(rows):
            snapshot = self.lookup(item['context_ref'])
            if item['kind'] == 'CURRENT_PAGE':
                observation = snapshot['observation']
                if len(canonical(observation).encode()) > 64000:
                    raise Conflict('Selected page metadata is too large for this input; use its screenshot or an exact file')
                parts.append({'type': 'text', 'text': 'FROZEN_INPUT_PAGE_DATA\n' + canonical({
                    'attachment_index': index, 'observation': observation, 'automatic_input_authorized': False})})
                continue
            if primary_visual == item['context_ref'] and not item['regions']:
                continue  # The base capability context already carries these exact pixels.
            image = self._image(snapshot, item)
            visual = snapshot['observation']['observations']['visual']
            reader = getattr(self.native.context, 'frozen_image', self.native.context.image)
            raw = reader({key: image[key] for key in ('id', 'revision', 'sha256')})
            region = None
            if item['regions']:
                left = min(area['x'] for area in item['regions']); top = min(area['y'] for area in item['regions'])
                right = max(area['x'] + area['width'] for area in item['regions'])
                bottom = max(area['y'] + area['height'] for area in item['regions'])
                region = {'x': left, 'y': top, 'width': min(1 - left, right - left), 'height': min(1 - top, bottom - top)}
            result = frame_view(raw, context_ref=item['context_ref'], frame_ref=visual['frame_ref'], image_ref=image,
                pixel_source=snapshot['preview'].get('pixel_source', 'UNKNOWN'), region=region,
                max_dimension=1280 if pictures == 1 else 640,
                max_bytes=2 * 1024 * 1024 if pictures == 1 else 480 * 1024)
            parts.append({'type': 'text', 'text': 'FROZEN_INPUT_IMAGE_DATA\n' + canonical({
                'attachment_index': index, 'context_ref': item['context_ref'], 'selected_regions': item['regions']})})
            for block in result['content']:
                if block['type'] == 'text':
                    parts.append({'type': 'text', 'text': block['text']})
                elif block['type'] == 'image':
                    parts.append({'type': 'file', 'mime': block['mimeType'],
                        'url': 'data:' + block['mimeType'] + ';base64,' + block['data']})
            if len(canonical(parts).encode()) > 3 * 1024 * 1024:
                raise Conflict('Selected capability inputs exceed the shared 3 MiB payload budget; narrow the selected regions')
        return parts

    @staticmethod
    def _image(snapshot, item):
        preview = snapshot.get('preview') or {}
        image = preview.get('image_ref') or {}
        visual = snapshot['observation'].get('observations', {}).get('visual') or {}
        if (preview.get('status') != 'AVAILABLE' or image.get('sha256') != item['image_sha256']
                or snapshot['observation'].get('coverage', {}).get('pixel_source') not in {'EXACT_TARGET_WINDOW', 'VERIFIED_TARGET_PIXELS'}
                or visual.get('image_ref', {}).get('sha256') != item['image_sha256']
                or not visual.get('frame_ref') or any(type(image.get(key)) is not int or image[key] <= 0 for key in ('width', 'height'))):
            raise Conflict('Attached screenshot differs from its exact registered frame')
        return image

    def _record(self, binding):
        operation = binding['request']['operation_id']
        record = self.native.ws._read(self.native.path(operation), authority=True)
        if (record['request'].get('operation_id') != operation or digest(record['request']) != record['request_sha256']
                or record['generation'] != self.native.generation
                or binding.get('native_message_ref') is not None and record.get('native_message_ref') != binding['native_message_ref']):
            raise Conflict('Input attachment binding differs from its original operation')
        return record

    def catalog(self, _arguments, binding):
        record = self._record(binding)
        rows = record['request'].get('input_attachments', [])
        summaries = record.get('input_attachment_sources', [])
        result = {'attachments': [{'index': index, **deepcopy(item),
            'source': deepcopy(summaries[index]) if index < len(summaries) else {'status': 'NOT_RECHECKED'}}
            for index, item in enumerate(rows)], 'coverage': 'ALL_EXPLICIT_INPUT_ATTACHMENTS',
            'labels_are_user_descriptions': True, 'automatic_input_authorized': False,
            'file_write_authorized': False, 'current_foreground_observed': False}
        if len(canonical(result).encode()) > 128 * 1024:
            raise Conflict('Attachment catalog exceeds its shared bound')
        return result

    def read(self, arguments, binding):
        if (not isinstance(arguments, dict) or set(arguments) != {'view', 'items'}
                or not isinstance(arguments.get('items'), list) or not 1 <= len(arguments['items']) <= 4):
            raise Conflict('Read at most four attachment views per call')
        record = self._record(binding)
        rows = record['request'].get('input_attachments', [])
        selected, seen = [], set()
        for pointer in arguments['items']:
            if not isinstance(pointer, dict) or 'index' not in pointer or set(pointer) - {'index', 'region_index'}:
                raise Conflict('Read only bound attachment indices and regions')
            index, region_index = pointer['index'], pointer.get('region_index')
            key = (index, region_index)
            if key in seen or type(index) is not int or not 0 <= index < len(rows):
                raise Conflict('Select distinct indices from this operation attachment catalog')
            seen.add(key)
            item = NativeInputAttachment.model_validate(rows[index]).model_dump()
            if region_index is not None and (type(region_index) is not int or not 0 <= region_index < len(item['regions'])):
                raise Conflict('Region is not part of this exact selected attachment')
            selected.append((index, item, region_index))
        if arguments['view'] == 'CONTEXT':
            if len(selected) != 1 or selected[0][2] is not None:
                raise Conflict('Read one complete bound context at a time')
            index, item, _ = selected[0]
            return {'index': index, 'context_ref': item['context_ref'],
                'observation': self.lookup(item['context_ref'])['observation'], 'automatic_input_authorized': False}
        if arguments['view'] != 'IMAGE':
            raise Conflict('Unknown attachment view')
        profile = self.native.transport.provider_registry.resolve(record['request']['provider_profile_ref'])
        if not profile.supports_images:
            raise Conflict('Selected model has no declared image input support; no image was omitted')
        content = []
        for index, item, region_index in selected:
            if item['kind'] != 'SCREENSHOT':
                raise Conflict('This attachment is a page reference, not a selected screenshot')
            snapshot = self.lookup(item['context_ref'])
            image = self._image(snapshot, item)
            visual = snapshot['observation']['observations']['visual']
            reader = getattr(self.native.context, 'frozen_image', self.native.context.image)
            raw = reader({key: image[key] for key in ('id', 'revision', 'sha256')})
            result = frame_view(raw, context_ref=item['context_ref'], frame_ref=visual['frame_ref'], image_ref=image,
                pixel_source=snapshot['preview'].get('pixel_source', 'UNKNOWN'),
                region=None if region_index is None else item['regions'][region_index],
                max_dimension=1280 if len(selected) == 1 else 640,
                max_bytes=2 * 1024 * 1024 if len(selected) == 1 else 480 * 1024)
            content.append({'type': 'text', 'text': canonical({'attachment_index': index, 'region_index': region_index,
                'context_ref': item['context_ref'], 'label': item['label'], 'label_origin': 'USER_DESCRIPTION'})})
            content.extend(result['content'])
        if len(canonical(content).encode()) > 3 * 1024 * 1024 - 65536:
            raise Conflict('Selected image views exceed the shared response budget; read fewer views or a region')
        return {'content': content, 'coverage': 'ONLY_REQUESTED_ATTACHMENT_VIEWS', 'automatic_input_authorized': False}
