"""Bounded model view of one frozen PNG; it carries no desktop input authority."""
import base64
from hashlib import sha256
from io import BytesIO
import math

from PIL import Image
from packages.domain.local_workspaces import canonical
from packages.domain.store import Conflict


VIEW_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'region': {'type': 'object', 'additionalProperties': False, 'required': ['x', 'y', 'width', 'height'],
        'properties': {key: {'type': 'number', 'minimum': 0, 'maximum': 1}
            for key in ('x', 'y', 'width', 'height')}}}}


def frame_view(raw, *, context_ref, frame_ref, image_ref, pixel_source, region=None,
               max_dimension=1280, max_bytes=2 * 1024 * 1024):
    if type(max_dimension) is not int or not 1 <= max_dimension <= 1280 or type(max_bytes) is not int or not 1 <= max_bytes <= 2 * 1024 * 1024:
        raise Conflict('Frozen view budget must stay within the host image bounds')
    if not isinstance(raw, bytes) or len(raw) > 16 * 1024 * 1024 or sha256(raw).hexdigest() != image_ref['sha256']:
        raise Conflict('Frozen frame bytes differ')
    region = region or {'x': 0, 'y': 0, 'width': 1, 'height': 1}
    if (not isinstance(region, dict) or set(region) != {'x', 'y', 'width', 'height'}
            or any(type(v) not in (float, int) or not math.isfinite(v) for v in region.values())
            or region['x'] < 0 or region['y'] < 0 or min(region['width'], region['height']) <= 0
            or region['x'] + region['width'] > 1 or region['y'] + region['height'] > 1):
        raise Conflict('A normalized region within this exact frame is required')
    with Image.open(BytesIO(raw)) as original:
        if original.format != 'PNG' or original.size != (image_ref['width'], image_ref['height']):
            raise Conflict('Frozen frame dimensions or format differ')
        w, h = original.size
        box = (math.floor(region['x'] * w), math.floor(region['y'] * h),
            math.ceil((region['x'] + region['width']) * w), math.ceil((region['y'] + region['height']) * h))
        image = original.crop(box).convert('RGB')
        image.thumbnail((max_dimension, max_dimension))
        output = BytesIO(); image.save(output, format='PNG', optimize=True)
        view = output.getvalue()
        if len(view) > max_bytes: raise Conflict('Bounded frame view exceeds the model budget')
        provenance = {'context_ref': context_ref, 'frame_ref': frame_ref, 'original_image_sha256': image_ref['sha256'],
            'original_size': [w, h], 'original_pixel_box': list(box), 'view_size': list(image.size),
            'view_sha256': sha256(view).hexdigest(), 'pixel_source': pixel_source,
            'coverage': 'ONLY_THIS_FROZEN_FRAME_REGION', 'whole_page_seen': False,
            'automatic_input_authorized': False, 'visual_coordinate_input_authorized': False}
        return {'content': [{'type': 'text', 'text': canonical(provenance)},
            {'type': 'image', 'data': base64.b64encode(view).decode(), 'mimeType': 'image/png'}]}
