"""Pinned ELK geometry only; no model, HTTP, renderer window or mutable graph.

The selected bundle is self-contained. One bounded child computes coordinates;
Python checks them before any output artifact can be registered.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import threading

from packages.domain.store import InvalidInput

ASSETS_MANIFEST_SHA256 = '709e38d0ef832688ed488bf5f6e4b075f0b311e328c318d7735b86e2255654c6'
MAX_INPUT = 4 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
TIMEOUT_SECONDS = 12
ASSETS = Path(__file__).with_name('diagram_layout_assets')


def _check(value, message):
    if not value:
        raise InvalidInput(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def engine_identity():
    """Verify the selected offline closure, including its notices, every call."""
    path = ASSETS / 'UPSTREAM.json'
    _check(not ASSETS.is_symlink() and path.is_file() and not path.is_symlink()
           and path.stat().st_size <= 32768, 'Diagram layout manifest is unavailable')
    raw = path.read_bytes()
    _check(_sha(raw) == ASSETS_MANIFEST_SHA256, 'Diagram layout manifest changed')
    value = json.loads(raw)
    _check(value['engine'] == 'elkjs' and value['version'] == '0.12.0'
           and set(value['files']) == {'worker.cjs', 'elk.bundled.js', 'LICENSE.md', 'upstream-package.json'},
           'Diagram layout dependency closure differs')
    for name, record in value['files'].items():
        member = ASSETS / name
        _check(member.is_file() and not member.is_symlink()
               and member.stat().st_size == record['bytes']
               and _sha(member.read_bytes()) == record['sha256'], 'Diagram layout dependency changed')
    return {'engine': 'elkjs', 'version': '0.12.0', 'direction': 'DOWN',
            'asset_manifest_sha256': ASSETS_MANIFEST_SHA256,
            'adapter_sha256': _sha(Path(__file__).read_bytes()),
            'worker_sha256': value['files']['worker.cjs']['sha256'],
            'bundle_sha256': value['files']['elk.bundled.js']['sha256']}


def _runtime(source_root):
    root = Path(source_root)
    if os.name == 'nt':
        program = root / 'runtime/electron/electron.exe'
    else:
        program = root / '.node-tools/node_modules/node/bin/node'
    _check(program.is_file(), 'The fixed local diagram layout runtime is unavailable')
    # No ambient Node flags, provider secrets or caller-chosen script/environment.
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
    if os.name == 'nt':
        env['ELECTRON_RUN_AS_NODE'] = '1'
    return [str(program), str(ASSETS / 'worker.cjs')], env


def _execute(command, env, raw):
    """Bound streams during reading, not just after communicate allocated them."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, creationflags=flags)
    output, errors = {}, []
    stop = threading.Event()

    def read(name, stream, limit):
        try:
            value = stream.read(limit + 1)
            if len(value) > limit:
                stop.set()
                # Only this newly-created exact child; never a task or GUI owner.
                try: child.kill()
                except OSError: pass
            output[name] = value[:limit]
        except (OSError, ValueError):
            errors.append(name)
        finally:
            stream.close()

    def write():
        try:
            child.stdin.write(raw)
            child.stdin.close()
        except (OSError, ValueError):
            errors.append('stdin')

    threads = [threading.Thread(target=read, args=('stdout', child.stdout, MAX_OUTPUT), daemon=True),
               threading.Thread(target=read, args=('stderr', child.stderr, 4096), daemon=True),
               threading.Thread(target=write, daemon=True)]
    for thread in threads: thread.start()
    try:
        child.wait(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=2)
        raise InvalidInput('Diagram layout timed out; no artifact was created') from None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)
        for thread in threads: thread.join(timeout=2)
    _check(not stop.is_set() and not errors and all(not thread.is_alive() for thread in threads)
           and child.returncode == 0 and not output.get('stderr'),
           'Diagram layout failed or exceeded its output budget; no artifact was created')
    return output.get('stdout', b'')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _rect(row):
    _check(isinstance(row, dict), 'Invalid diagram layout box')
    _check(all(_number(row.get(k)) for k in ('x', 'y', 'width', 'height'))
           and row['width'] > 0 and row['height'] > 0, 'Invalid diagram layout box')
    return row['x'], row['y'], row['x'] + row['width'], row['y'] + row['height']


def _overlap(a, b, margin=0):
    return min(a[2] + margin, b[2]) > max(a[0] - margin, b[0]) + .01 and min(a[3] + margin, b[3]) > max(a[1] - margin, b[1]) + .01


class _Index:
    def __init__(self): self.cells = {}
    @staticmethod
    def keys(box):
        for x in range(math.floor(box[0] / 256), math.floor(box[2] / 256) + 1):
            for y in range(math.floor(box[1] / 256), math.floor(box[3] / 256) + 1): yield x, y
    def add(self, ident, box):
        for key in self.keys(box): self.cells.setdefault(key, set()).add(ident)
    def query(self, box):
        result = set()
        for key in self.keys(box): result.update(self.cells.get(key, ()))
        return result


def _segment_crosses(a, b, box):
    if abs(a['y'] - b['y']) < .01:
        return box[1] + .01 < a['y'] < box[3] - .01 and min(max(a['x'], b['x']), box[2]) > max(min(a['x'], b['x']), box[0]) + .01
    if abs(a['x'] - b['x']) < .01:
        return box[0] + .01 < a['x'] < box[2] - .01 and min(max(a['y'], b['y']), box[3]) > max(min(a['y'], b['y']), box[1]) + .01
    raise InvalidInput('Diagram layout returned a nonorthogonal route')


def validate_geometry(value, nodes, edges):
    """Reject a layout that hides content. Never fall back to the old grid."""
    _check(isinstance(value, dict) and type(value.get('schema_version')) is int and value['schema_version'] == 1
           and value.get('engine') == 'elkjs@0.12.0' and value.get('direction') == 'DOWN',
           'Diagram layout protocol differs')
    _check(all(_number(value.get(k)) and 0 < value[k] <= 200000 for k in ('width', 'height'))
           and value['width'] * value['height'] <= 2_000_000_000, 'Diagram layout exceeds the readable image budget')
    wanted_nodes = {n['id']: n for n in nodes}; wanted_edges = {e['id']: e for e in edges}
    _check(isinstance(value.get('nodes'), list) and isinstance(value.get('edges'), list)
           and len(value['nodes']) == len(wanted_nodes) and len(value['edges']) == len(wanted_edges),
           'Diagram layout changed graph membership')
    _check(all(isinstance(n, dict) and isinstance(n.get('id'), str) for n in value['nodes'] + value['edges']), 'Invalid diagram layout members')
    node_map = {n['id']: n for n in value['nodes']}; edge_map = {e['id']: e for e in value['edges']}
    _check(set(node_map) == set(wanted_nodes) and set(edge_map) == set(wanted_edges), 'Diagram layout changed graph IDs')
    nodes_index, labels_index, boxes, labels = _Index(), _Index(), {}, {}
    def contained(box): return box[0] >= -.01 and box[1] >= -.01 and box[2] <= value['width'] + .01 and box[3] <= value['height'] + .01
    for ident, node in node_map.items():
        box = _rect(node)
        _check(contained(box) and all(abs(node[k] - wanted_nodes[ident][k]) < .01 for k in ('width', 'height')), 'Diagram node geometry changed dimensions or escaped canvas')
        _check(not any(_overlap(box, boxes[other]) for other in nodes_index.query(box)), 'Diagram node boxes overlap')
        boxes[ident] = box; nodes_index.add(ident, box)
    for ident, edge in edge_map.items():
        wanted = wanted_edges[ident]
        _check(edge.get('source') == wanted['source'] and edge.get('target') == wanted['target']
               and isinstance(edge.get('labels'), list) and len(edge['labels']) == 1,
               'Diagram layout changed an edge or omitted its label')
        label = edge['labels'][0]; box = _rect(label)
        _check(label.get('id') == '$label:' + ident and contained(box)
               and all(abs(label[k] - wanted[k]) < .01 for k in ('width', 'height')), 'Diagram edge label geometry differs')
        _check(not any(_overlap(box, boxes[n]) for n in nodes_index.query(box)), 'Diagram label overlaps a node')
        _check(not any(_overlap(box, labels[n]) for n in labels_index.query(box)), 'Diagram edge labels overlap')
        labels[ident] = box; labels_index.add(ident, box)
    for ident, edge in edge_map.items():
        sections = edge.get('sections')
        _check(isinstance(sections, list) and 1 <= len(sections) <= 8, 'Diagram edge has no bounded route')
        for section in sections:
            _check(isinstance(section, dict) and isinstance(section.get('bends'), list) and len(section['bends']) <= 100, 'Diagram edge route is oversized')
            points = [section.get('start'), *section['bends'], section.get('end')]
            _check(all(isinstance(p, dict) and set(p) == {'x', 'y'} and all(_number(p[k]) for k in ('x', 'y')) and -.01 <= p['x'] <= value['width'] + .01 and -.01 <= p['y'] <= value['height'] + .01 for p in points), 'Diagram edge route is invalid or outside canvas')
            for a, b in zip(points, points[1:]):
                bounds = min(a['x'], b['x']), min(a['y'], b['y']), max(a['x'], b['x']), max(a['y'], b['y'])
                _check(not any(_segment_crosses(a, b, boxes[n]) for n in nodes_index.query(bounds)), 'Diagram edge crosses a node interior')
                _check(not any(_segment_crosses(a, b, labels[n]) for n in labels_index.query(bounds) if n != ident), 'Diagram edge crosses another edge label')
    return value


def layout_graph(nodes, edges, source_root):
    identity = engine_identity()
    raw = json.dumps({'schema_version': 1, 'nodes': nodes, 'edges': edges}, ensure_ascii=False,
                     sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _check(len(raw) <= MAX_INPUT, 'Diagram layout input exceeds its local budget')
    command, env = _runtime(source_root)
    try:
        value = json.loads(_execute(command, env, raw))
    except (OSError, UnicodeError, ValueError):
        raise InvalidInput('Diagram layout did not return a valid local result') from None
    _check(isinstance(value, dict) and value.get('input_sha256') == _sha(raw), 'Diagram layout result names another input')
    validate_geometry(value, nodes, edges)
    _check(engine_identity() == identity, 'Diagram layout assets changed during computation')
    return value, identity
