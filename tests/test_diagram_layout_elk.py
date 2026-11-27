"""Real ELK/PNG/SVG and Store/API. No model or native Windows qualification."""
from copy import deepcopy
from io import BytesIO
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from apps.api.discovery_diagrams import register_discovery_diagram_routes
from packages.adapters.diagram_layout import engine_identity, layout_graph, validate_geometry
from packages.domain.discovery import DiscoveryWorkbench
from packages.domain.discovery_schema import DiscoveryCreate, DiscoveryEdit
from packages.domain.local_workspaces import digest, sha
from packages.domain.planning_diagrams import PlanningDiagrams, _font, render_graph
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import TaskService

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = Path(__file__).with_name('fixtures') / 'diagram_original_b_g01.json'


def require(value, why):
    if not value: raise AssertionError(why)


def complex_graph():
    labels = ['提交真实材料 <字段> & 原件不改', '审批/退回：保留每次真实理由',
              '核查中文长标签与未拆分英文Identifier0123456789', '归还并记录异常，仅保留实际已确认关系']
    return {'id': 'stress', 'kind': 'FLOW', 'title': '中文/English：回边、自环、平行关系 <完整显示> & 追溯',
            'nodes': [{'id': f'N{i+1}', 'label': label, 'description': '完整说明不改变。',
                       'claim_ids': ['requirement'], 'source_anchors': [], 'origin': 'USER_STATEMENT'} for i, label in enumerate(labels)],
            'edges': [{'id': f'E{i+1}', 'source': source, 'target': target, 'relation': label,
                       'claim_ids': ['requirement'], 'source_anchors': [], 'origin': 'USER_STATEMENT'}
                      for i, (source, target, label) in enumerate([
                          ('N1', 'N2', '直接提交与核查'), ('N1', 'N2', '同端点第二条关系，不能合并或丢失'),
                          ('N2', 'N1', '退回后补充原材料，并保留原审核记录与本次反馈；不代表自动执行或覆盖来源。'),
                          ('N2', 'N2', '在本节点复核：这是明确自环，不允许删边'),
                          ('N2', 'N3', '核查完整字段 <xml> & 原始字节'), ('N3', 'N4', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 中文长说明需要完整换行')]) ]}


def svg_text(root, ident, kind):
    group = next(n for n in root.iter() if n.get('data-' + kind + '-id') == ident)
    return ''.join(n.text or '' for n in group if n.tag.endswith('text'))


def check_render(graph, svg, png, meta):
    parsed = ET.fromstring(svg); geometry = meta['render_geometry']
    require(len([n for n in parsed.iter() if n.get('data-node-id')]) == len(graph['nodes']), 'Every original node retained')
    require(len([n for n in parsed.iter() if n.get('data-edge-id')]) == len(graph['edges']), 'Every original edge retained')
    for edge in graph['edges']:
        require(svg_text(parsed, edge['id'], 'edge') == ''.join(edge['relation'].splitlines()), 'Complete relation without truncation')
    for node in graph['nodes']:
        require(svg_text(parsed, node['id'], 'node') == ''.join(node['label'].splitlines()), 'Complete business label without an extra internal ID row')
    with Image.open(BytesIO(png)) as image:
        require(image.format == 'PNG' and image.size == (geometry['width_px'], geometry['height_px']) and image.width * image.height <= 16_000_000, 'Real bounded PNG/geometry equality')
    require(geometry['minimum_font_px'] == 18 and geometry['text_complete'] is True, 'Truthful full label font size')
    require(len(geometry['content_boxes']) == len(graph['nodes']) + len(graph['edges']), 'Word coverage has every real content box')
    for box in geometry['content_boxes']:
        require(box['x'] >= 0 and box['y'] >= 0 and box['x'] + box['width'] <= geometry['width_px'] and box['y'] + box['height'] <= geometry['height_px'], 'Final PNG coordinates contain every box')


@pytest.mark.parametrize('which', ['original_b', 'cjk_cycles_parallel'])
def test_actual_original_and_complex_graphs_keep_text_geometry_and_deterministic_bytes(tmp_path, which):
    raw = ORIGINAL.read_bytes(); graph = json.loads(raw)['graph'] if which == 'original_b' else complex_graph()
    before = deepcopy(graph); font, _ = _font(ROOT)
    svg, png, meta = render_graph(graph, font, source_root=ROOT, with_geometry=True)
    check_render(graph, svg, png, meta)
    again = render_graph(graph, font, source_root=ROOT, with_geometry=True)
    require(again == (svg, png, meta), 'Fixed seed/IDs/layout/font produce repeated exact bytes')
    require(graph == before and ORIGINAL.read_bytes() == raw, 'Original graph and old QA input not mutated')
    (tmp_path / (which + '.svg')).write_bytes(svg); (tmp_path / (which + '.png')).write_bytes(png)
    (tmp_path / (which + '-geometry.json')).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n')


def test_user_export_keeps_duplicate_business_labels_and_structural_ids_without_id_text(tmp_path):
    graph = complex_graph()
    graph['id'] = 'INTERNAL_GRAPH_SENTINEL'
    graph['nodes'][0]['label'] = graph['nodes'][1]['label'] = '业务工单 N1：审批与复核 <原文> & 不改编号'
    before = deepcopy(graph); font, _ = _font(ROOT)
    svg, png, meta = render_graph(graph, font, source_root=ROOT, with_geometry=True)
    (tmp_path / 'user-labels.svg').write_bytes(svg)
    (tmp_path / 'user-labels.png').write_bytes(png)
    (tmp_path / 'user-labels-geometry.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n')
    check_render(graph, svg, png, meta)
    parsed = ET.fromstring(svg)
    visible_text = [element.text or '' for element in parsed.iter() if element.tag.endswith('}text')]
    require(graph['id'] not in visible_text, 'Internal graph ID is not a user-facing footer')
    require(not set(node['id'] for node in graph['nodes']) & set(visible_text), 'Internal node IDs stay out of visible text')
    require({element.get('data-node-id') for element in parsed.iter() if element.get('data-node-id')}
            == {node['id'] for node in graph['nodes']}, 'Duplicate labels keep separate original node identities')
    require({element.get('data-edge-id') for element in parsed.iter() if element.get('data-edge-id')}
            == {edge['id'] for edge in graph['edges']}, 'Parallel, return and self-loop edges keep original identities')
    require(svg_text(parsed, 'N1', 'node') == svg_text(parsed, 'N2', 'node')
            == graph['nodes'][0]['label'], 'Business identifiers and duplicate Unicode labels are not stripped or merged')
    with Image.open(BytesIO(png)) as image:
        for box in meta['render_geometry']['content_boxes']:
            if box['kind'] != 'NODE': continue
            inner = image.crop((math.ceil(box['x']) + 8, math.ceil(box['y']) + 8,
                                math.floor(box['x'] + box['width']) - 8, math.floor(box['y'] + box['height']) - 8))
            require(any(max(pixel) < 100 for pixel in inner.getdata()), 'Real PNG contains dark label glyphs inside every node')
    require(graph == before, 'User export never changes canonical labels, IDs, endpoints or conditions')


def candidate(graph):
    return {'title': '真实图布局验收', 'phase': 'SPECIFY', 'summary': '只验证显示；不授权执行。',
            'claims': [{'id': 'requirement', 'text': '完整保留图中的真实关系。', 'category': 'REQUIREMENT',
                        'origin': 'USER_STATEMENT', 'source_anchors': [], 'acceptance': ['逐项核对']}],
            'questions': [], 'competitors': [], 'graphs': [graph], 'next_actions': []}


@pytest.fixture
def scene(tmp_path):
    data = tmp_path / 'data'; store = TaskService(data / 'actual.sqlite3', ROOT)
    pid = store.create_project('原创图质量项目', '')['id']
    proposals = PlanningProposals(store, ROOT); discovery = DiscoveryWorkbench(store, proposals)
    session = discovery.create(pid, DiscoveryCreate(operation_id='create', title='图可读', entry_mode='PROBLEM', statement='完整可见的原图与来源'))
    session = discovery.edit(pid, session['id'], DiscoveryEdit(operation_id='edit', expected_revision=1, candidate=candidate(complex_graph()), change_reason='明确原创QA'))
    app = FastAPI(); app.state.discovery = discovery
    for error, status in [(Conflict, 409), (InvalidInput, 422), (NotFound, 404)]:
        async def handler(request: Request, exc: Exception, status=status): return JSONResponse({'error': str(exc)}, status_code=status)
        app.add_exception_handler(error, handler)
    register_discovery_diagram_routes(app, store, data, source_root=ROOT)
    with TestClient(app) as client:
        yield {'store': store, 'pid': pid, 'data': data, 'discovery': discovery, 'session': session,
               'service': app.state.planning_diagrams, 'client': client}


def test_real_api_store_download_restart_stale_scope_and_sources_preserved(scene):
    e = scene; ref = e['session']; endpoint = f"/api/projects/{e['pid']}/discovery/sessions/{ref['id']}/diagrams/stress"
    response = e['client'].get(endpoint + '/artifact', params={'revision': 2})
    require(response.status_code == 200, response.text); artifact = response.json(); data = {}
    for kind in ['svg', 'png', 'json']:
        result = e['client'].get(endpoint, params={'revision': 2, 'format': kind})
        require(result.status_code == 200 and sha(result.content) == artifact['files'][kind]['sha256'], 'Actual API bytes/ref')
        data[kind] = result.content
    model = json.loads(data['json'])
    require(model['graph'] == ref['payload']['candidate']['graphs'][0] and model['session_ref'] == {k: ref[k] for k in ['id', 'revision', 'sha256']}, 'Exact source graph/ref')
    restart = PlanningDiagrams(e['store'], e['data'], e['discovery'].get, source_root=ROOT)
    require(restart.read_artifact(e['pid'], artifact['artifact_ref'])['png_bytes'] == data['png'], 'Restart reads exact immutable pixels')
    other = e['store'].create_project('不同工程', '')['id']
    with pytest.raises(NotFound): restart.read_artifact(other, artifact['artifact_ref'])
    forged = {**artifact['artifact_ref'], 'sha256': '0' * 64}
    with pytest.raises(Conflict): restart.read_artifact(e['pid'], forged)
    updated = deepcopy(ref['payload']['candidate']); updated['graphs'][0]['nodes'][0]['label'] = '用户后来改了图；旧版本保持'
    newest = e['discovery'].edit(e['pid'], ref['id'], DiscoveryEdit(operation_id='changed', expected_revision=2, candidate=updated, change_reason='用户显式新版本'))
    latest = restart.generate(e['pid'], ref['id'], 'stress', newest['revision'])
    require(latest['artifact_ref'] != artifact['artifact_ref'] and restart.read_artifact(e['pid'], artifact['artifact_ref'])['png_bytes'] == data['png'], 'New edits never overwrite old artifact')
    require(e['discovery'].get(e['pid'], ref['id'], 2)['payload'] == ref['payload'], 'Frozen original payload untouched')


def test_geometry_rejects_wrong_ids_overlap_nonfinite_and_node_crossing():
    nodes = [{'id': 'A', 'width': 100, 'height': 100}, {'id': 'B', 'width': 100, 'height': 100}]
    edges = [{'id': 'E', 'source': 'A', 'target': 'B', 'width': 50, 'height': 20}]
    good = {'schema_version': 1, 'engine': 'elkjs@0.12.0', 'direction': 'DOWN', 'width': 400, 'height': 400,
            'nodes': [{'id': 'A', 'x': 20, 'y': 20, 'width': 100, 'height': 100}, {'id': 'B', 'x': 20, 'y': 240, 'width': 100, 'height': 100}],
            'edges': [{'id': 'E', 'source': 'A', 'target': 'B', 'labels': [{'id': '$label:E', 'x': 150, 'y': 150, 'width': 50, 'height': 20}],
                       'sections': [{'start': {'x': 70, 'y': 120}, 'bends': [], 'end': {'x': 70, 'y': 240}}]}]}
    validate_geometry(good, nodes, edges)
    for mutate in [lambda v: v['nodes'][0].update(id='wrong'), lambda v: v['edges'][0]['labels'][0].update(x=30, y=40),
                   lambda v: v['nodes'][0].update(x=float('nan')), lambda v: v['edges'][0]['sections'][0]['start'].update(y=40)]:
        bad = deepcopy(good); mutate(bad)
        with pytest.raises(InvalidInput): validate_geometry(bad, nodes, edges)


def test_engine_closure_and_fixed_child_do_not_use_path_or_ambient_model_secrets():
    from packages.adapters.diagram_layout import _runtime
    identity = engine_identity(); command, env = _runtime(ROOT)
    require(identity['version'] == '0.12.0' and command[0] == str(ROOT / '.node-tools/node_modules/node/bin/node'), 'Exact approved local runtime')
    require(command[1].endswith('/diagram_layout_assets/worker.cjs') and not any(k in env for k in ['PATH', 'NODE_OPTIONS', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY']), 'Fixed worker; no ambient model credentials')


def test_snapshot_projection_and_readable_detail_redraw_use_frozen_geometry(scene, tmp_path):
    from packages.domain.planning_reports import PlanningReports, PlanningReportInput
    from packages.domain.planning_diagrams import render_diagram_viewport
    e = scene; session = e['session']; value = e['service'].generate(e['pid'], session['id'], 'stress', 2)
    reports = PlanningReports(e['store'], e['data'], e['discovery'].proposals,
        discovery_reader=e['discovery'].get, diagram_reader=e['service'].read_artifact)
    body = PlanningReportInput(operation_id='new-report', expected_revision=2, diagram_refs=[value['artifact_ref']])
    _, _, attachments = reports._attachments(e['pid'], body, session, 'DISCOVERY')
    diagram = attachments[0]
    source = e['service'].read_artifact(e['pid'], value['artifact_ref'])
    require(diagram['render_geometry'] == source['manifest']['render_geometry'] and diagram['render_source'] == source['render_source'], 'Exact private render metadata frozen with original artifact')
    require(diagram['png_sha256'] == sha(source['png_bytes']), 'Original overview bytes/hash unchanged')
    view = {'x': 10, 'y': 10, 'width': 300, 'height': 400}
    detail = render_diagram_viewport(diagram, view, source_root=ROOT)
    with Image.open(BytesIO(source['png_bytes'])) as full, Image.open(BytesIO(detail)) as part:
        require(part.size == (300, 400), 'Detail keeps the requested original-resolution viewport')
        box = next(b for b in diagram['render_geometry']['logical_content_boxes'] if b['kind'] == 'NODE')
        left, top = math.floor(box['x']), math.floor(box['y'])
        right, bottom = math.ceil(box['x']+box['width']), math.ceil(box['y']+box['height'])
        complete = render_diagram_viewport(diagram, {'x':left,'y':top,'width':right-left,'height':bottom-top}, source_root=ROOT)
        with Image.open(BytesIO(complete)) as node_image:
            require(node_image.tobytes() == full.crop((left,top,right,bottom)).tobytes(), 'A whole object retains exact original pixels; partial duplicates/title are intentionally omitted')
    (tmp_path / 'actual-readable-detail.png').write_bytes(detail)
    for key in ['font_sha256', 'renderer_sha256']:
        bad = deepcopy(diagram); bad['render_source'][key] = '0' * 64
        with pytest.raises(Conflict): render_diagram_viewport(bad, view, source_root=ROOT)
    with pytest.raises(InvalidInput): render_diagram_viewport(diagram, {**view, 'width': 2000, 'height': 2000}, source_root=ROOT)
    bad = deepcopy(diagram); bad['render_source']['layout']['nodes'][0]['x'] += 10
    with pytest.raises(Conflict): render_diagram_viewport(bad, view, source_root=ROOT)


def test_local_child_timeout_and_output_limits_are_real_bounded_processes(tmp_path, monkeypatch):
    import packages.adapters.diagram_layout as adapter
    node = str(ROOT / '.node-tools/node_modules/node/bin/node')
    slow = tmp_path / 'own-slow.cjs'; slow.write_text('setTimeout(()=>{},10000);')
    monkeypatch.setattr(adapter, 'TIMEOUT_SECONDS', .1)
    with pytest.raises(InvalidInput, match='timed out'): adapter._execute([node, str(slow)], {}, b'')
    monkeypatch.setattr(adapter, 'TIMEOUT_SECONDS', 3)
    monkeypatch.setattr(adapter, 'MAX_OUTPUT', 256)
    loud = tmp_path / 'own-oversized.cjs'; loud.write_text("process.stdout.write('x'.repeat(8192));setTimeout(()=>{},1000);")
    with pytest.raises(InvalidInput, match='output budget'): adapter._execute([node, str(loud)], {}, b'')


def test_changed_bundle_is_rejected_before_start_without_touching_original(tmp_path, monkeypatch):
    import shutil
    import packages.adapters.diagram_layout as adapter
    original_hash = sha((adapter.ASSETS / 'elk.bundled.js').read_bytes())
    clone = tmp_path / 'assets'; shutil.copytree(adapter.ASSETS, clone)
    raw = bytearray((clone / 'elk.bundled.js').read_bytes()); raw[-2] ^= 1; (clone / 'elk.bundled.js').write_bytes(raw)
    monkeypatch.setattr(adapter, 'ASSETS', clone)
    with pytest.raises(InvalidInput, match='dependency changed'): adapter.engine_identity()
    require(sha((ROOT / 'packages/adapters/diagram_layout_assets/elk.bundled.js').read_bytes()) == original_hash, 'Original reviewed bundle unchanged')
