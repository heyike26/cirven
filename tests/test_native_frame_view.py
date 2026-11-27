import base64
from hashlib import sha256
from io import BytesIO
import json

from PIL import Image
import pytest
from packages.adapters.native_frame_view import frame_view
from packages.domain.store import Conflict


def sample():
    image = Image.new('RGB', (3000, 2000), '#ffffff')
    image.paste('#112233', (750, 500, 1500, 1000))
    output = BytesIO(); image.save(output, 'PNG'); raw = output.getvalue()
    return raw, {'sha256': sha256(raw).hexdigest(), 'width': 3000, 'height': 2000}


def test_frozen_region_uses_real_original_pixels_and_preserves_explicit_transform_without_input_proof():
    raw, info = sample()
    result = frame_view(raw, image_ref=info, context_ref={'id': 'context'}, frame_ref={'id': 'frame'},
        pixel_source='UNKNOWN', region={'x': .25, 'y': .25, 'width': .25, 'height': .25})
    evidence = json.loads(result['content'][0]['text'])
    assert evidence['original_pixel_box'] == [750, 500, 1500, 1000]
    with Image.open(BytesIO(base64.b64decode(result['content'][1]['data']))) as image:
        assert image.size == (750, 500) and image.getpixel((50, 50)) == (17, 34, 51)
    assert evidence['pixel_source'] == 'UNKNOWN' and not evidence['automatic_input_authorized']
    assert not evidence['visual_coordinate_input_authorized'] and not evidence['whole_page_seen']


@pytest.mark.parametrize('region', [{'x': .9, 'y': 0, 'width': .2, 'height': 1},
    {'x': 0, 'y': 0, 'width': 0, 'height': 1}, {'x': 0, 'y': 0, 'width': float('nan'), 'height': 1}])
def test_invalid_regions_do_not_read_another_object_or_invent_a_view(region):
    raw, info = sample()
    with pytest.raises(Conflict): frame_view(raw, image_ref=info, context_ref={}, frame_ref={}, pixel_source='UNKNOWN', region=region)


def test_changed_frame_hash_is_rejected_and_full_view_has_bounded_dimensions():
    raw, info = sample()
    with pytest.raises(Conflict): frame_view(raw + b'changed', image_ref=info, context_ref={}, frame_ref={}, pixel_source='UNKNOWN')
    result = frame_view(raw, image_ref=info, context_ref={}, frame_ref={}, pixel_source='UNKNOWN')
    evidence = json.loads(result['content'][0]['text'])
    assert max(evidence['view_size']) == 1280 and evidence['original_size'] == [3000, 2000]
