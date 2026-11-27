"""Ink-bearing and control-character boundaries; generated data only."""
import base64
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from packages.domain import planning_word_projection as projection
from packages.domain.intake import canonical
from packages.domain.store import InvalidInput

R = Path(__file__).resolve().parents[1]


def check(value, message='Glyph projection acceptance failed'):
    if not value:
        raise AssertionError(message)


def generated_diagram(label):
    graph = {'id': 'G_GLYPH_FIXTURE', 'kind': 'FLOW', 'title': 'Original glyph boundary fixture',
             'nodes': [{'id': 'N1', 'label': label, 'description': 'Generated test only',
                        'origin': 'MODEL_SUGGESTION', 'claim_ids': ['TEST'], 'source_anchors': []}],
             'edges': []}
    output = BytesIO()
    Image.new('RGB', (1, 1), 'white').save(output, format='PNG')
    png = output.getvalue()
    return {'graph': graph, 'model_sha256': hashlib.sha256(canonical(graph).encode()).hexdigest(),
            'png_base64': base64.b64encode(png).decode('ascii'),
            'png_sha256': hashlib.sha256(png).hexdigest()}


@pytest.mark.parametrize('label', ['\u0301', 'Yj'])
def test_actual_ink_and_negative_bearing_are_painted_inside_the_measured_node_box(label, monkeypatch):
    draw_original, layout_original = projection.ImageDraw.Draw, projection.layout_graph
    actual_text, layouts = [], []
    def capture_draw(image):
        real = draw_original(image)
        class Recorder:
            def __getattr__(self, name):
                return getattr(real, name)
            def text(self, point, text, **kwargs):
                actual_text.append((point, text, kwargs['font'].getbbox(text, anchor='lt')))
                return real.text(point, text, **kwargs)
        return Recorder()
    def capture_layout(*args):
        result = layout_original(*args)
        layouts.append(result[0])
        return result
    monkeypatch.setattr(projection.ImageDraw, 'Draw', capture_draw)
    monkeypatch.setattr(projection, 'layout_graph', capture_layout)
    result = projection.build_reading_projection(generated_diagram(label), 465, 626, source_root=R)
    check('png_bytes' in result and len(layouts) == 1 and len(actual_text) == 1)
    node = layouts[0]['nodes'][0]
    canvas = result['audit']['canvas_transform']
    node = {**node, 'x': node['x'] + canvas['offset_x_px'],
            'y': node['y'] + canvas['offset_y_px']}
    (x, y), text, (left, top, right, bottom) = actual_text[0]
    check(text == label, 'The original glyph sequence must be passed to the actual drawing call')
    check(x + left >= node['x'] + projection.NODE_PAD, 'Negative bearing must keep the intended left padding')
    check(x + right <= node['x'] + node['width'] - projection.NODE_PAD,
          'Ink overflow cannot escape the allocated right boundary')
    check(y + top >= node['y'] + projection.NODE_PAD)
    check(y + bottom <= node['y'] + node['height'] - projection.NODE_PAD)


@pytest.mark.parametrize('control', ['\x01', '\t', '\x7f', '\x85', '\u202e', '\u200d'])
def test_control_or_invisible_format_character_stops_before_runtime_or_drawing(control, monkeypatch):
    def forbidden(*args):
        raise AssertionError('Unsupported invisible characters must stop before font/runtime use')
    monkeypatch.setattr(projection, '_font', forbidden)
    result = projection.build_reading_projection(generated_diagram('A' + control + 'B'), 465, 626, source_root=R)
    check(result['reason'] == 'UNSUPPORTED_CONTROL_CHARACTER' and 'png_bytes' not in result)


def test_negative_vertical_bearing_cannot_hide_an_oversized_ink_row(monkeypatch):
    class OversizedInk:
        def font_variant(self, **kwargs):
            return self
        def getlength(self, row):
            return len(row) * 18
        def getbbox(self, row, **kwargs):
            return (-1, -4, len(row) * 18, 22)
    def forbidden(*args):
        raise AssertionError('An oversized ink row must stop before layout')
    monkeypatch.setattr(projection, '_font', lambda *args: (OversizedInk(), b'generated-font-record'))
    monkeypatch.setattr(projection, 'layout_graph', forbidden)
    result = projection.build_reading_projection(generated_diagram('A'), 465, 626, source_root=R)
    check(result['reason'] == 'UNSUPPORTED_GLYPH_HEIGHT' and 'png_bytes' not in result)


def test_missing_or_invalid_fixed_font_is_a_strict_no_artifact_error(monkeypatch):
    def missing(*args):
        raise InvalidInput('Fixed font unavailable or identity invalid')
    monkeypatch.setattr(projection, '_font', missing)
    with pytest.raises(InvalidInput, match='Fixed font'):
        projection.build_reading_projection(generated_diagram('A'), 465, 626, source_root=R)
