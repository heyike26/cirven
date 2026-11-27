"""Portable graph tests using original self-QA graph data and a synthetic PNG.

The 8x8 PNG and render/session metadata are generated test data, not the native
saved Source33 PNG or its qualification. The real frozen snapshot/native binding
is validated separately by the owning Root workflow.
"""
from copy import deepcopy
import base64
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path

import pytest
from PIL import Image

from packages.domain import planning_word_projection as projection
from packages.domain.intake import canonical
from packages.domain.store import InvalidInput

R = Path(__file__).resolve().parents[1]


def check(value, message='Projection acceptance failed'):
    if not value:
        raise AssertionError(message)


@pytest.fixture(scope='module')
def diagram():
    graph = json.loads((R/'tests/fixtures/diagram_six_task_dependencies.json').read_bytes())
    output = BytesIO()
    Image.new('RGB', (8, 8), '#ddeeff').save(output, format='PNG')
    png = output.getvalue()
    return {
        'graph': graph, 'model_sha256': hashlib.sha256(canonical(graph).encode()).hexdigest(),
        'png_base64': base64.b64encode(png).decode('ascii'),
        'png_sha256': hashlib.sha256(png).hexdigest(),
        'render_geometry': {'synthetic_test_metadata': True, 'width_px': 8, 'height_px': 8,
                            'text_complete': False, 'minimum_font_px': 1},
        'render_source': {'synthetic_test_metadata': True, 'layout': {'test_record': 'retain exactly'}},
        'session_ref': {'id': 'portable_test_session', 'revision': 1,
                        'sha256': hashlib.sha256(b'portable-test-session-only').hexdigest()},
    }


@pytest.fixture(scope='module')
def rendered(diagram):
    before = deepcopy(diagram)
    calls = []
    original = projection.layout_graph
    def captured(*args):
        value = original(*args)
        calls.append((deepcopy(args[:2]), deepcopy(value)))
        return value
    projection.layout_graph = captured
    try:
        result = projection.build_reading_projection(diagram, 464.88188976377955, 626.8346456692914, source_root=R)
    finally:
        projection.layout_graph = original
    check(diagram == before, 'Projection cannot modify original PNG, metadata, origins, anchors or any graph content')
    check(len(calls) == 1, 'One existing pinned layout call is the complete execution budget')
    return result, calls[0][1]


def test_six_task_ten_edge_graph_fits_completely_with_unchanged_nine_pt_gate(diagram, rendered):
    result, _ = rendered
    check(result['status'] == 'GEOMETRY_CHECKED_NATIVE_WORD_NOT_RUN')
    check(result['minimum_font_pt'] >= 9.5)
    canvas = result['audit']['canvas_transform']
    check(math.ceil(canvas['original_width_px']) == 766 and canvas['original_height_px'] == 996)
    left, top, right, bottom = canvas['complete_painted_bounds']
    check(result['width_px'] == math.floor(right) - math.floor(left) + 1 + 32)
    check(result['height_px'] == math.floor(bottom) - math.floor(top) + 1 + 32)
    check(canvas['outer_margin_px'] == 16 and canvas['source_layout_unchanged'])
    audit = result['audit']
    check(audit['minimum_font_pt_required'] == 9 and projection.MINIMUM_FONT_PT == 9)
    check(audit['node_count'] == 6 and audit['edge_count'] == 10)
    check(audit['nodes'] == [{key: node[key] for key in ('id', 'label')} for node in diagram['graph']['nodes']])
    check(audit['edges'] == [{key: edge[key] for key in ('id', 'source', 'target', 'relation')} for edge in diagram['graph']['edges']])
    check(audit['derived_layout'] and not audit['native_word_visual_acceptance'])
    check(audit['source_png_sha256'] == diagram['png_sha256'])
    check(audit['source_model_sha256'] == diagram['model_sha256'])
    check('重新布局' in audit['caption_required'])
    check(audit['png_sha256'] == hashlib.sha256(result['png_bytes']).hexdigest())
    check(Image.open(BytesIO(result['png_bytes'])).size == (result['width_px'], result['height_px']))
    for key in ('physical_width_pt', 'physical_height_pt'):
        check(abs(result[key] * 20 - round(result[key] * 20)) < 1e-6, 'New physical extents are exact twip multiples')


def test_all_original_visible_words_fit_measured_boxes_and_every_endpoint_is_complete(diagram, rendered):
    _, (layout, _) = rendered
    face, _ = projection._font(R)
    face = face.font_variant(size=18)
    nodes = {node['id']: node for node in layout['nodes']}
    edges = {edge['id']: edge for edge in layout['edges']}
    def measured(text, width, box, pad):
        lines = projection._lines(text, width, face)
        check(''.join(lines) == text, 'Every exact original character is rendered in order')
        for index, line in enumerate(lines):
            left, top, right, bottom = face.getbbox(line, anchor='lt')
            check(left + pad >= 0 and right + pad <= box['width'])
            check(top + pad + index * 24 >= 0 and bottom + pad + index * 24 <= box['height'])
    def boundary(point, box):
        l, t, r, b = box['x'], box['y'], box['x'] + box['width'], box['y'] + box['height']
        return l <= point['x'] <= r and t <= point['y'] <= b and min(abs(point['x'] - l), abs(point['x'] - r), abs(point['y'] - t), abs(point['y'] - b)) < .01
    for node in diagram['graph']['nodes']:
        measured(node['label'], 216, nodes[node['id']], 8)
    for edge in diagram['graph']['edges']:
        value = edges[edge['id']]
        measured(edge['relation'], 90, value['labels'][0], 6)
        check(len(value['sections']) == 1)
        check(boundary(value['sections'][0]['start'], nodes[edge['source']]))
        check(boundary(value['sections'][0]['end'], nodes[edge['target']]))


@pytest.mark.parametrize('fault', ['shape', 'ports', 'group', 'missing-endpoint', 'duplicate-node', 'unknown-kind'])
def test_unsupported_shapes_and_invalid_structure_do_not_call_layout_or_flatten(diagram, monkeypatch, fault):
    candidate = deepcopy(diagram)
    graph = candidate['graph']
    if fault == 'shape': graph['nodes'][0]['shape'] = 'diamond'
    if fault == 'ports': graph['nodes'][0]['ports'] = [{'id': 'port1'}]
    if fault == 'group': graph['nodes'][0]['parent'] = 'group1'
    if fault == 'missing-endpoint': graph['edges'][0]['source'] = 'missing'
    if fault == 'duplicate-node': graph['nodes'].append(deepcopy(graph['nodes'][0]))
    if fault == 'unknown-kind': graph['kind'] = 'BPMN'
    def forbidden(*args): raise AssertionError('Unsupported graph must stop before any runtime/font load')
    monkeypatch.setattr(projection, '_font', forbidden)
    before = deepcopy(candidate)
    result = projection.build_reading_projection(candidate, 465, 626, source_root=R)
    check(result['reason'] == 'UNSUPPORTED_GRAPH_SCHEMA' and 'png_bytes' not in result)
    check(candidate == before)


@pytest.mark.parametrize('dimension', [0, -1, float('nan'), float('inf'), True])
def test_invalid_page_area_is_rejected_before_execution(diagram, dimension):
    with pytest.raises(InvalidInput):
        projection.build_reading_projection(diagram, dimension, 626, source_root=R)


def test_large_graph_budget_is_honest_before_any_runtime_or_image_allocation(diagram, monkeypatch):
    candidate = deepcopy(diagram)
    candidate['graph']['nodes'] *= 5
    def forbidden(*args): raise AssertionError('Large graph must not wake a layout runtime')
    monkeypatch.setattr(projection, '_font', forbidden)
    result = projection.build_reading_projection(candidate, 465, 626, source_root=R)
    check(result['reason'] == 'BOUNDED_GRAPH_LIMIT' and 'png_bytes' not in result)
    check(result['node_count'] == 30 and result['minimum_font_pt_required'] == 9)


def test_changed_graph_cannot_borrow_original_source_identity(diagram, monkeypatch):
    candidate = deepcopy(diagram)
    candidate['graph']['edges'][0]['relation'] = 'A different relation'
    def forbidden(*args): raise AssertionError('Changed source must stop before runtime/font load')
    monkeypatch.setattr(projection, '_font', forbidden)
    result = projection.build_reading_projection(candidate, 465, 626, source_root=R)
    check(result['reason'] == 'SOURCE_GRAPH_IDENTITY_MISMATCH' and 'png_bytes' not in result)


def test_small_area_cannot_be_signed_readable_by_shrinking_or_partial_cropping(diagram, monkeypatch):
    original = projection.layout_graph
    calls = []
    def measured(*args):
        value = original(*args)
        calls.append(value)
        return value
    monkeypatch.setattr(projection, 'layout_graph', measured)
    result = projection.build_reading_projection(diagram, 100, 100, source_root=R)
    check(result['reason'] == 'COMPLETE_LAYOUT_BELOW_TARGET_9_5PT' and 'png_bytes' not in result)
    check(result['candidate_minimum_font_pt'] < 9 and not result['native_word_visual_acceptance'])
    check(len(calls) == 2, 'The tiny-page refusal must cover both genuine wrap candidates')


def test_wrong_or_fragmented_route_cannot_be_signed_complete(diagram, rendered, monkeypatch):
    _, measured = rendered
    layout, engine = deepcopy(measured)
    layout['edges'][0]['sections'][0]['start']['x'] = 0
    monkeypatch.setattr(projection, 'layout_graph', lambda *args: (layout, engine))
    result = projection.build_reading_projection(diagram, 465, 626, source_root=R)
    check(result['reason'] == 'UNSUPPORTED_COMPLETE_ROUTE' and 'png_bytes' not in result)


def test_layout_failure_has_explicit_nonqualified_fallback(diagram, monkeypatch):
    def failed(*args): raise InvalidInput('Bounded layout failed')
    monkeypatch.setattr(projection, 'layout_graph', failed)
    result = projection.build_reading_projection(diagram, 465, 626, source_root=R)
    check(result['reason'] == 'LOCAL_LAYOUT_UNAVAILABLE' and 'png_bytes' not in result)
    check(not result['native_word_visual_acceptance'])
