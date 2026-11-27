"""Bounded, complete Word reading view derived from a frozen flat graph.

The original graph, source PNG and frozen layout are inputs, never outputs.
This is a new layout, not a reproduction of the original diagram. A geometric
fit is deliberately distinct from an actual native Word visual acceptance.
"""
from __future__ import annotations

from io import BytesIO
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw
from pydantic import ValidationError

from packages.adapters.diagram_layout import layout_graph
from packages.domain.discovery_schema import DiscoveryGraph
from packages.domain.planning_diagrams import _font, _lines
from packages.domain.store import InvalidInput


PROJECTION = 'CIRVEN_WORD_READING_PROJECTION_V1'
MINIMUM_FONT_PT = 9
TARGET_MINIMUM_FONT_PT = 9.5
MAX_NODES = 24
MAX_EDGES = 64
MAX_TEXT = 12000
MAX_PIXELS = 4_000_000
FONT_PX = 18
NODE_WRAP_PX = 216
EDGE_WRAP_PX = 90
LINE_HEIGHT = 24
NODE_PAD = 8
EDGE_PAD = 6
COMPACT_NODE_WRAP_PX = 112
COMPACT_EDGE_WRAP_PX = 72
OUTER_MARGIN_PX = 16
LINE_WIDTH_PX = 2
ARROW_LENGTH_PX = 8
FORBIDDEN_WRAP_START = frozenset('、。，．！？：；)]}〉》」』】〗〙〛’”…%‰/')


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _failure(reason, **facts):
    return {'status': 'NOT_SINGLE_PAGE_READABLE', 'reason': reason,
            'minimum_font_pt_required': MINIMUM_FONT_PT,
            'native_word_visual_acceptance': False, **facts}


def _row_metrics(face, row):
    """Allocate actual ink as well as advance, including negative bearings."""
    left, top, right, bottom = face.getbbox(row, anchor='lt')
    offset_x, offset_y = -min(0, left), -min(0, top)
    return (offset_x, offset_y,
            math.ceil(max(face.getlength(row), right) + offset_x),
            math.ceil(bottom + offset_y))


def _word_lines(text, width, face):
    """Keep closing punctuation with preceding text at generated wrap breaks.

    Explicit source paragraphs and every character remain in order. When a
    single character plus its closing marks cannot fit the nominal wrap width,
    keep that unit intact; its real measured box still has to pass the page gate.
    """
    rows = []
    for paragraph in text.splitlines() or ['']:
        if not paragraph:
            rows.append('')
        start = 0
        while start < len(paragraph):
            end = start + 1
            while end < len(paragraph) and face.getlength(paragraph[start:end + 1]) <= width:
                end += 1
            if end < len(paragraph) and (paragraph[end] in FORBIDDEN_WRAP_START or unicodedata.combining(paragraph[end])):
                split = end
                while split > start and (paragraph[split] in FORBIDDEN_WRAP_START or unicodedata.combining(paragraph[split])):
                    split -= 1
                if split > start:
                    end = split
                else:
                    while end < len(paragraph) and (paragraph[end] in FORBIDDEN_WRAP_START or unicodedata.combining(paragraph[end])):
                        end += 1
            rows.append(paragraph[start:end])
            start = end
    return rows


def _complete_routes(layout, graph):
    nodes = {node['id']: node for node in layout['nodes']}
    edges = {edge['id']: edge for edge in layout['edges']}
    def boundary(point, box):
        x, y = point['x'], point['y']
        left, top = box['x'], box['y']
        right, bottom = left + box['width'], top + box['height']
        return (left - .01 <= x <= right + .01 and top - .01 <= y <= bottom + .01
                and min(abs(x - left), abs(x - right), abs(y - top), abs(y - bottom)) <= .01)
    return all(len(edges[edge['id']]['sections']) == 1
               and boundary(edges[edge['id']]['sections'][0]['start'], nodes[edge['source']])
               and boundary(edges[edge['id']]['sections'][0]['end'], nodes[edge['target']])
               for edge in graph['edges'])


def _reading_canvas(layout, graph, node_rows, edge_rows, face):
    """Rebase complete painted primitives; never crop a frozen source raster.

    ELK's fresh result and the source diagram remain unchanged. The union uses
    allocated node/label boxes, actual glyph ink, line strokes and arrowheads;
    removing only the surrounding empty area leaves an explicit 16px margin.
    """
    boxes, glyphs = [], []
    def box(value):
        return (value['x'], value['y'], value['x'] + value['width'], value['y'] + value['height'])
    def text(value, rows, pad):
        bounds = box(value)
        boxes.append(bounds)
        for index, row in enumerate(rows):
            ox, oy, _, _ = _row_metrics(face, row)
            left, top, right, bottom = face.getbbox(row, anchor='lt')
            x, y = value['x'] + pad + ox, value['y'] + pad + index * LINE_HEIGHT + oy
            ink = (x + left, y + top, x + right, y + bottom)
            if not (bounds[0] + pad - .01 <= ink[0] <= ink[2] <= bounds[2] - pad + .01
                    and bounds[1] + pad - .01 <= ink[1] <= ink[3] <= bounds[3] - pad + .01):
                raise InvalidInput('Complete Word glyph ink escaped its allocated label box')
            glyphs.append(ink)
    for node in layout['nodes']:
        text(node, node_rows[node['id']], NODE_PAD)
    for edge in layout['edges']:
        text(edge['labels'][0], edge_rows[edge['id']], EDGE_PAD)
        for section in edge['sections']:
            points = [section['start'], *section['bends'], section['end']]
            for point in points:
                x, y = point['x'], point['y']
                half = LINE_WIDTH_PX / 2
                boxes.append((x - half, y - half, x + half, y + half))
            distinct = [point for i, point in enumerate(points) if i == 0 or point != points[i - 1]]
            if len(distinct) < 2:
                raise InvalidInput('Word reading projection has a zero-length arrow')
            start, end = distinct[-2:]
            angle = math.atan2(end['y'] - start['y'], end['x'] - start['x'])
            for delta in (-.45, .45):
                x = end['x'] - ARROW_LENGTH_PX * math.cos(angle + delta)
                y = end['y'] - ARROW_LENGTH_PX * math.sin(angle + delta)
                boxes.append((x, y, x, y))
    complete = boxes + glyphs
    left, top = min(b[0] for b in complete), min(b[1] for b in complete)
    right, bottom = max(b[2] for b in complete), max(b[3] for b in complete)
    dx, dy = OUTER_MARGIN_PX - math.floor(left), OUTER_MARGIN_PX - math.floor(top)
    result = deepcopy(layout)
    # Pillow rectangles include an integer right/bottom pixel. Keep the outer
    # margin after that pixel as well, rather than counting its coordinate as
    # an exclusive image extent.
    result['width'] = math.floor(right) - math.floor(left) + 1 + 2 * OUTER_MARGIN_PX
    result['height'] = math.floor(bottom) - math.floor(top) + 1 + 2 * OUTER_MARGIN_PX
    def move(point):
        point['x'] += dx
        point['y'] += dy
    for node in result['nodes']:
        move(node)
    for edge in result['edges']:
        for label in edge['labels']:
            move(label)
        for section in edge['sections']:
            for point in [section['start'], *section['bends'], section['end']]:
                move(point)
    if not _complete_routes(result, graph):
        raise InvalidInput('Complete Word route changed during canvas translation')
    return result, {
        'original_layout_sha256': _digest(layout),
        'original_width_px': layout['width'], 'original_height_px': layout['height'],
        'complete_painted_bounds': [left, top, right, bottom],
        'glyph_ink_bounds': [min(b[0] for b in glyphs), min(b[1] for b in glyphs),
                             max(b[2] for b in glyphs), max(b[3] for b in glyphs)],
        'offset_x_px': dx, 'offset_y_px': dy, 'outer_margin_px': OUTER_MARGIN_PX,
        'all_glyph_ink_inside_label_boxes': True,
        'complete_node_label_count': len(layout['nodes']) + len(layout['edges']),
        'complete_route_count': len(layout['edges']),
        'source_layout_unchanged': True,
    }


def build_reading_projection(diagram, page_width_pt, page_height_pt, *, source_root=None):
    """Return a PNG only if the complete new layout fits at >=9pt.

    The caller supplies actual available image area, after reserving title and
    caption space. Unsupported extended shapes, oversized flat graphs and fits
    below 9pt return a bounded failure without substituting partial diagrams.
    The existing pinned ELK worker executes at most twice: the original wrap,
    then one fixed compact wrap only when the first complete view cannot fit.
    There is no model, new dependency, orientation mutation or semantic rewrite.
    """
    if (type(page_width_pt) not in (int, float) or type(page_height_pt) not in (int, float)
            or not math.isfinite(page_width_pt) or not math.isfinite(page_height_pt)
            or min(page_width_pt, page_height_pt) <= 0):
        raise InvalidInput('Word reading projection has no available page area')
    graph = diagram.get('graph') if isinstance(diagram, dict) else None
    if (not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list)
            or not isinstance(graph.get('edges'), list)):
        return _failure('UNSUPPORTED_GRAPH_SCHEMA')
    if len(graph['nodes']) > MAX_NODES or len(graph['edges']) > MAX_EDGES:
        return _failure('BOUNDED_GRAPH_LIMIT', node_count=len(graph['nodes']), edge_count=len(graph['edges']))
    # The authoritative typed graph forbids extra shape/port/group properties.
    # Never silently flatten a richer imported diagram into ordinary rectangles.
    try:
        DiscoveryGraph.model_validate(graph)
    except (ValidationError, ValueError, TypeError):
        return _failure('UNSUPPORTED_GRAPH_SCHEMA')
    graph_sha256 = _digest(graph)
    if diagram.get('model_sha256') != graph_sha256:
        return _failure('SOURCE_GRAPH_IDENTITY_MISMATCH')
    texts = [graph['title'], *[n['label'] for n in graph['nodes']],
             *[e['relation'] for e in graph['edges']]]
    if sum(len(text) for text in texts) > MAX_TEXT:
        return _failure('BOUNDED_TEXT_LIMIT')
    if any(unicodedata.category(char) in {'Cc', 'Cf'} and char not in '\r\n'
           for text in texts for char in text):
        return _failure('UNSUPPORTED_CONTROL_CHARACTER')
    root = Path(source_root or Path(__file__).resolve().parents[2])
    face, font_raw = _font(root)
    face = face.font_variant(size=FONT_PX)
    attempts = []
    for node_wrap, edge_wrap in ((NODE_WRAP_PX, EDGE_WRAP_PX),
                                  (COMPACT_NODE_WRAP_PX, COMPACT_EDGE_WRAP_PX)):
        node_rows, edge_rows, node_inputs, edge_inputs = {}, {}, [], []
        for node in sorted(graph['nodes'], key=lambda value: value['id']):
            rows = _word_lines(node['label'], node_wrap, face)
            node_rows[node['id']] = rows
            node_inputs.append({'id': node['id'],
                                'width': max(_row_metrics(face, row)[2] for row in rows) + 2 * NODE_PAD,
                                'height': len(rows) * LINE_HEIGHT + 2 * NODE_PAD})
        for edge in sorted(graph['edges'], key=lambda value: value['id']):
            rows = _word_lines(edge['relation'], edge_wrap, face)
            edge_rows[edge['id']] = rows
            edge_inputs.append({'id': edge['id'], 'source': edge['source'], 'target': edge['target'],
                                'width': max(_row_metrics(face, row)[2] for row in rows) + 2 * EDGE_PAD,
                                'height': len(rows) * LINE_HEIGHT + 2 * EDGE_PAD})
        if any(_row_metrics(face, row)[3] > LINE_HEIGHT
               for rows in [*node_rows.values(), *edge_rows.values()] for row in rows):
            return _failure('UNSUPPORTED_GLYPH_HEIGHT')
        try:
            original_layout, engine = layout_graph(node_inputs, edge_inputs, root)
        except InvalidInput as error:
            return _failure('LOCAL_LAYOUT_UNAVAILABLE', detail=str(error))
        if not _complete_routes(original_layout, graph):
            return _failure('UNSUPPORTED_COMPLETE_ROUTE')
        layout, canvas = _reading_canvas(original_layout, graph, node_rows, edge_rows, face)
        width, height = layout['width'], layout['height']
        scale = min(page_width_pt / width, page_height_pt / height)
        # Word stores image extents in twips. Keep the same strict page area
        # and quantize before the >=9.5pt decision, including compact fallback.
        physical_width_pt = math.floor(width * scale * 20) / 20
        physical_height_pt = math.floor(height * scale * 20) / 20
        font_pt = FONT_PX * min(physical_width_pt / width, physical_height_pt / height)
        attempts.append({'node_wrap_px': node_wrap, 'edge_wrap_px': edge_wrap,
                         'width_px': width, 'height_px': height, 'minimum_font_pt': font_pt,
                         'original_layout_sha256': canvas['original_layout_sha256']})
        if width * height > MAX_PIXELS:
            return _failure('BOUNDED_IMAGE_LIMIT', width_px=width, height_px=height,
                            layout_attempts=attempts)
        if font_pt >= TARGET_MINIMUM_FONT_PT:
            break
    else:
        return _failure('COMPLETE_LAYOUT_BELOW_TARGET_9_5PT', candidate_minimum_font_pt=font_pt,
                        target_minimum_font_pt=TARGET_MINIMUM_FONT_PT,
                        width_px=width, height_px=height, layout_attempts=attempts)
    image = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(image)
    nodes = {node['id']: node for node in layout['nodes']}
    edges = {edge['id']: edge for edge in layout['edges']}
    def label(box, rows, pad, color):
        for index, row in enumerate(rows):
            offset_x, offset_y, _, _ = _row_metrics(face, row)
            draw.text((box['x'] + pad + offset_x, box['y'] + pad + index * LINE_HEIGHT + offset_y),
                      row, font=face, fill=color, anchor='lt')
    # Complete routes are drawn on one canvas. No viewport clipping is used.
    for edge in sorted(graph['edges'], key=lambda value: value['id']):
        for section in edges[edge['id']]['sections']:
            points = [(p['x'], p['y']) for p in [section['start'], *section['bends'], section['end']]]
            draw.line(points, fill='#60758c', width=LINE_WIDTH_PX)
            distinct = [point for i, point in enumerate(points) if i == 0 or point != points[i - 1]]
            if len(distinct) < 2:
                raise InvalidInput('Word reading projection has a zero-length arrow')
            start, end = distinct[-2:]
            angle = math.atan2(end[1] - start[1], end[0] - start[0])
            draw.polygon([end,
                          (end[0] - ARROW_LENGTH_PX * math.cos(angle - .45), end[1] - ARROW_LENGTH_PX * math.sin(angle - .45)),
                          (end[0] - ARROW_LENGTH_PX * math.cos(angle + .45), end[1] - ARROW_LENGTH_PX * math.sin(angle + .45))], fill='#60758c')
    for edge in sorted(graph['edges'], key=lambda value: value['id']):
        box = edges[edge['id']]['labels'][0]
        draw.rectangle((box['x'], box['y'], box['x'] + box['width'], box['y'] + box['height']), fill='white')
        label(box, edge_rows[edge['id']], EDGE_PAD, '#203a53')
    for node in sorted(graph['nodes'], key=lambda value: value['id']):
        box = nodes[node['id']]
        draw.rounded_rectangle((box['x'], box['y'], box['x'] + box['width'], box['y'] + box['height']),
                               radius=8, fill='#f5f7fa', outline='#9eafc1', width=2)
        label(box, node_rows[node['id']], NODE_PAD, '#263445')
    output = BytesIO()
    image.save(output, format='PNG', optimize=False)
    png = output.getvalue()
    audit = {
        'projection': PROJECTION, 'derived_layout': True,
        'graph_id': graph['id'], 'graph_sha256': graph_sha256,
        'source_model_sha256': diagram.get('model_sha256'), 'source_png_sha256': diagram.get('png_sha256'),
        'source_session_ref': diagram.get('session_ref'),
        'png_sha256': hashlib.sha256(png).hexdigest(), 'font_sha256': hashlib.sha256(font_raw).hexdigest(),
        'layout_sha256': _digest(layout), 'layout_engine': engine,
        'canvas_transform': canvas, 'layout_attempts': attempts,
        'node_count': len(graph['nodes']), 'edge_count': len(graph['edges']),
        'nodes': [{'id': node['id'], 'label': node['label']} for node in graph['nodes']],
        'edges': [{key: edge[key] for key in ('id', 'source', 'target', 'relation')} for edge in graph['edges']],
        'all_routes_complete': True, 'all_labels_complete': True,
        'minimum_font_pt_required': MINIMUM_FONT_PT, 'minimum_font_pt': font_pt,
        'target_minimum_font_pt': TARGET_MINIMUM_FONT_PT,
        'physical_width_pt': physical_width_pt, 'physical_height_pt': physical_height_pt,
        'native_word_visual_acceptance': False,
        'source_graph_unchanged': True, 'source_png_unchanged': True,
        'caption_required': '依据所附原图数据重新布局；节点说明见可追溯附录，原图见配套材料。',
    }
    return {'status': 'GEOMETRY_CHECKED_NATIVE_WORD_NOT_RUN', 'png_bytes': png,
            'width_px': width, 'height_px': height, 'minimum_font_pt': font_pt,
            'physical_width_pt': physical_width_pt, 'physical_height_pt': physical_height_pt,
            'audit': audit}
