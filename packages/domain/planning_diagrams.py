"""Deterministic real SVG/PNG/model artifacts bound to one discovery revision."""
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
from types import SimpleNamespace
import uuid
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont
from packages.adapters.diagram_layout import engine_identity, layout_graph
from packages.domain.diagram_schema import ArtifactRef, DiscoveryGraph
from packages.domain.document_import import image_metadata
from packages.domain.local_workspaces import canonical, digest, sha
from packages.domain.paths import resolve_data_path
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.store import Conflict, InvalidInput, NotFound

SVG = 'http://www.w3.org/2000/svg'
RENDERER = 'CIRVEN_ELK_LAYERED_V1'
FONT_SHA256='b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a'


def _font(source_root, explicit=None):
    candidates = [Path(explicit)] if explicit else [Path(source_root)/'toolchain/fonts/NotoSansCJK-Regular.ttc']
    for path in candidates:
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 32*1024*1024:
            raw = path.read_bytes()
            if not explicit and sha(raw)!=FONT_SHA256:raise InvalidInput('Packaged diagram font SHA mismatch')
            try: font = ImageFont.truetype(BytesIO(raw), 19,index=2 if path.name=='NotoSansCJK-Regular.ttc' else 0)
            except OSError: continue
            return font, raw
    raise InvalidInput('A real CJK TTF/TTC font is required for diagram PNG rendering; configure font_path')


def _lines(text, width, font):
    result=[]
    for paragraph in text.splitlines() or ['']:
        line=''
        for char in paragraph:
            if line and font.getlength(line+char)>width: result.append(line);line=char
            else: line+=char
        result.append(line)
    return result


def render_graph(graph, font, *, source_root=None, with_geometry=False, _layout_result=None, _viewport=None):
    """One measured geometry for SVG, PNG and later readable Word detail pages."""
    nodes = sorted(graph['nodes'], key=lambda n: n['id'])
    edges = sorted(graph['edges'], key=lambda e: e['id'])
    if any(re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text) for text in [graph['title'],
            *[text for n in nodes for text in (n['label'], n['description'])], *[e['relation'] for e in edges]]):
        raise InvalidInput('Diagram text contains forbidden XML control characters')
    body_font, title_font = font.font_variant(size=18), font.font_variant(size=22)
    line_height, pad = 28, 16
    node_text, edge_text, node_input, edge_input = {}, {}, [], []
    for node in nodes:
        labels = _lines(node['label'], 216, body_font)
        width = max(144, math.ceil(max(body_font.getlength(line) for line in labels)) + 2 * pad)
        node_input.append({'id': node['id'], 'width': width, 'height': len(labels) * line_height + 2 * pad})
        node_text[node['id']] = labels
    for edge in edges:
        rows = _lines(edge['relation'], 168, body_font)
        edge_input.append({'id': edge['id'], 'source': edge['source'], 'target': edge['target'],
                           'width': max(16, math.ceil(max(body_font.getlength(line) for line in rows)) + 16),
                           'height': len(rows) * line_height + 16})
        edge_text[edge['id']] = rows
    root_path = Path(source_root or Path(__file__).resolve().parents[2])
    if _layout_result is None:
        layout, engine = layout_graph(node_input, edge_input, root_path)
    else:
        layout, engine = _layout_result
        from packages.adapters.diagram_layout import validate_geometry
        validate_geometry(layout, node_input, edge_input)
        packet = {'schema_version': 1, 'nodes': node_input, 'edges': edge_input}
        if layout.get('input_sha256') != sha(canonical(packet).encode()):
            raise Conflict('Frozen layout belongs to another graph text or dimensions')
    width = math.ceil(layout['width'])
    title_lines = _lines(graph['title'], width - 32, title_font)
    top = 16 + len(title_lines) * 32 + 16
    height = math.ceil(layout['height']) + top + 48
    scale = min(1., math.sqrt(15_500_000 / (width * height))) if _viewport is None else 1.
    left, view_top = (0., 0.) if _viewport is None else (_viewport['x'], _viewport['y'])
    image_width = max(1, math.ceil(width * scale)) if _viewport is None else math.ceil(_viewport['width'])
    image_height = max(1, math.ceil(height * scale)) if _viewport is None else math.ceil(_viewport['height'])
    image = Image.new('RGB', (image_width, image_height), '#f5f7fa')
    pixels = ImageDraw.Draw(image)
    png_fonts = {}
    class Drawing:
        @staticmethod
        def point(p): return ((p[0] - left) * scale, (p[1] - view_top) * scale)
        def line(self, points, **kw): pixels.line([self.point(p) for p in points], **{**kw, 'width': max(1, round(kw['width'] * scale))})
        def polygon(self, points, **kw): pixels.polygon([self.point(p) for p in points], **kw)
        def rectangle(self, box, **kw): pixels.rectangle((*self.point(box[:2]), *self.point(box[2:])), **kw)
        def rounded_rectangle(self, box, **kw): pixels.rounded_rectangle((*self.point(box[:2]), *self.point(box[2:])), **{**kw, 'radius': max(1, round(kw['radius'] * scale)), 'width': max(1, round(kw['width'] * scale))})
        def text(self, point, value, *, font, **kw):
            if font.size not in png_fonts: png_fonts[font.size] = font.font_variant(size=max(1, round(font.size * scale)))
            pixels.text(self.point(point), value, font=png_fonts[font.size], **kw)
    draw = Drawing()
    root = ET.Element('svg', {'xmlns': SVG, 'width': str(width), 'height': str(height),
        'viewBox': f'0 0 {width} {height}', 'role': 'img', 'aria-label': graph['title']})
    ET.SubElement(root, 'title').text = graph['title']
    ET.SubElement(root, 'rect', {'width': '100%', 'height': '100%', 'fill': '#f5f7fa'})
    def number(value): return f'{value:.3f}'.rstrip('0').rstrip('.')
    def text(group, x, baseline, value, face, color):
        draw.text((x, baseline), value, font=face, fill=color, anchor='ls')
        ET.SubElement(group, 'text', {'x': number(x), 'y': number(baseline),
            'font-family': 'Noto Sans CJK SC, sans-serif', 'font-size': str(face.size), 'fill': color}).text = value
    def complete_box(x, y, w, h):
        return (_viewport is None or (x >= left and y >= view_top
            and x + w <= left + _viewport['width'] and y + h <= view_top + _viewport['height']))
    if _viewport is None:
        for i, line in enumerate(title_lines):
            text(root, 16, 16 + title_font.getmetrics()[0] + i * 32, line, title_font, '#203a53')
    edge_map = {edge['id']: edge for edge in layout['edges']}
    node_map = {node['id']: node for node in layout['nodes']}
    content_boxes = []
    # Routes and label boxes came from the same ELK computation, not endpoints'
    # arithmetic midpoint. Labels participate in routing before nodes are drawn.
    for edge in edges:
        value = edge_map[edge['id']]
        group = ET.SubElement(root, 'g', {'data-edge-id': edge['id']})
        ET.SubElement(group, 'title').text = edge['relation'] + ' [' + edge['source'] + ' → ' + edge['target'] + ']'
        for section in value['sections']:
            points = [(p['x'], p['y'] + top) for p in [section['start'], *section['bends'], section['end']]]
            draw.line(points, fill='#60758c', width=2)
            ET.SubElement(group, 'polyline', {'points': ' '.join(f'{number(x)},{number(y)}' for x, y in points),
                'fill': 'none', 'stroke': '#60758c', 'stroke-width': '2'})
            distinct = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
            if len(distinct) < 2:
                raise InvalidInput('Diagram returned a zero-length arrow route')
            a, b = distinct[-2:]; angle = math.atan2(b[1] - a[1], b[0] - a[0])
            arrow = [b, (b[0] - 9 * math.cos(angle - .45), b[1] - 9 * math.sin(angle - .45)),
                     (b[0] - 9 * math.cos(angle + .45), b[1] - 9 * math.sin(angle + .45))]
            draw.polygon(arrow, fill='#60758c')
            ET.SubElement(group, 'polygon', {'points': ' '.join(f'{number(x)},{number(y)}' for x, y in arrow), 'fill': '#60758c'})
        label = value['labels'][0]
        x, y, w, h = label['x'], label['y'] + top, label['width'], label['height']
        if complete_box(x, y, w, h):
            draw.rectangle((x, y, x + w, y + h), fill='#f5f7fa')
            ET.SubElement(group, 'rect', {'x': number(x), 'y': number(y), 'width': number(w), 'height': number(h), 'fill': '#f5f7fa'})
            for i, line in enumerate(edge_text[edge['id']]):
                text(group, x + 8, y + 8 + body_font.getmetrics()[0] + i * line_height, line, body_font, '#203a53')
        content_boxes.append({'kind': 'EDGE_LABEL', 'id': edge['id'], 'x': x, 'y': y, 'width': w, 'height': h})
    for node in nodes:
        value = node_map[node['id']]
        x, y, w, h = value['x'], value['y'] + top, value['width'], value['height']
        content_boxes.append({'kind': 'NODE', 'id': node['id'], 'x': x, 'y': y, 'width': w, 'height': h})
        if not complete_box(x, y, w, h):
            continue
        draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill='white', outline='#9eafc1', width=2)
        group = ET.SubElement(root, 'g', {'data-node-id': node['id']})
        ET.SubElement(group, 'title').text = node['label'] + '\n' + node['description']
        ET.SubElement(group, 'rect', {'x': number(x), 'y': number(y), 'width': number(w), 'height': number(h),
            'rx': '10', 'fill': 'white', 'stroke': '#9eafc1'})
        for i, line in enumerate(node_text[node['id']]):
            text(group, x + pad, y + pad + body_font.getmetrics()[0] + i * line_height,
                 line, body_font, '#263445')
    geometry = {'version': 1, 'width_px': image_width, 'height_px': image_height,
        'minimum_font_px': max(1, round(18 * scale)), 'direction': 'DOWN', 'layout_engine': 'elkjs@0.12.0',
        'text_complete': True, 'png_scale': scale, 'png_is_overview': scale < 1,
        'logical_width_px': width, 'logical_height_px': height, 'logical_minimum_font_px': 18,
        'logical_content_boxes': content_boxes, 'layout_sha256': digest(layout),
        'content_boxes': [{**box, **{key: box[key] * scale for key in ('x', 'y', 'width', 'height')}} for box in content_boxes]}
    out = BytesIO(); image.save(out, format='PNG', optimize=False)
    result = ET.tostring(root, encoding='utf-8', xml_declaration=True), out.getvalue()
    return (*result, {'render_geometry': geometry, 'layout': layout, 'layout_engine': engine}) if with_geometry else result


def render_diagram_viewport(diagram, viewport, *, source_root=None):
    """Redraw one bounded readable detail from frozen logical geometry.

    This never crops/upsamples the overview, recomputes layout or calls a model.
    The report keeps its original PNG/source SHA; details are explicit views.
    """
    source = diagram.get('render_source')
    geometry = diagram.get('render_geometry')
    if not isinstance(source, dict) or not isinstance(geometry, dict):
        raise InvalidInput('This historical diagram has no frozen detail rendering geometry')
    if (source.get('renderer_sha256') != sha(Path(__file__).read_bytes())
            or source.get('layout_engine') != engine_identity()
            or digest(source.get('layout')) != geometry.get('layout_sha256')
            or digest(diagram['graph']) != diagram['model_sha256']):
        raise Conflict('Frozen diagram detail renderer or geometry differs')
    if (not isinstance(viewport, dict) or set(viewport) != {'x', 'y', 'width', 'height'}
            or not all(type(viewport[k]) in (int, float) and math.isfinite(viewport[k]) for k in viewport)
            or viewport['x'] < 0 or viewport['y'] < 0 or viewport['width'] <= 0 or viewport['height'] <= 0
            or math.ceil(viewport['width']) * math.ceil(viewport['height']) > 2_000_000
            or viewport['x'] + viewport['width'] > geometry['logical_width_px'] + .01
            or viewport['y'] + viewport['height'] > geometry['logical_height_px'] + .01):
        raise InvalidInput('Diagram detail viewport is outside its bounded logical image')
    font, raw = _font(source_root or Path(__file__).resolve().parents[2])
    if sha(raw) != source.get('font_sha256') or font.index != source.get('font_face_index'):
        raise Conflict('Frozen diagram detail font changed')
    _, png = render_graph(diagram['graph'], font, source_root=source_root,
        _layout_result=(source['layout'], source['layout_engine']), _viewport=viewport)
    return png


class PlanningDiagrams:
    def __init__(self,store,data_dir,session_reader,*,source_root=None,font_path=None):
        self.store,self.data_dir,self.session_reader=store,Path(data_dir),session_reader
        self.source_root=Path(source_root or Path(__file__).resolve().parents[2]);self.font_path=font_path
        if self.data_dir.is_symlink():raise InvalidInput('Diagram registry must not be a symlink')

    def _path(self,pid,*parts):
        self.store.project(pid)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',pid):raise InvalidInput('Unsafe diagram project identity')
        return resolve_data_path('/'.join(('planning-diagrams',pid,*parts)),self.data_dir)

    @staticmethod
    def _write(path,raw):
        with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())

    def _saved(self,pid,ident):
        if not re.fullmatch(r'[0-9a-f]{64}',ident):raise InvalidInput('Diagram artifact identity must be SHA256')
        folder=self._path(pid,ident)
        if not folder.is_dir():raise NotFound('Diagram artifact does not exist in this project')
        try:
            wrapper=json.loads(self._path(pid,ident,'manifest.json').read_bytes());manifest=wrapper['payload']
            if digest(manifest)!=wrapper['sha256'] or manifest['id']!=ident or manifest['project_id']!=pid:raise ValueError
            for value in manifest['files'].values():
                path=self._path(pid,ident,value['stored_name']);raw=path.read_bytes()
                if len(raw)!=value['byte_length'] or sha(raw)!=value['sha256']:raise ValueError
            model=json.loads(self._path(pid,ident,'model.json').read_bytes())
            if model['project_id']!=pid or digest(model['graph'])!=manifest['model_sha256'] or model['session_ref']!=manifest['session_ref']:raise ValueError
            binding=digest({'project_id':pid,'session_ref':manifest['session_ref'],'graph_sha256':manifest['model_sha256'],
                             'renderer':manifest['renderer'],'renderer_sha256':manifest['renderer_sha256'],
                             'pillow_version':manifest['pillow_version'],'font_face_index':manifest['font_face_index'],
                             'font_sha256':manifest['font_sha256']})
            if manifest['renderer']==RENDERER:
                if model.get('layout_engine')!=manifest.get('layout_engine') or model.get('render_geometry')!=manifest.get('render_geometry'):raise ValueError
                binding=digest({'project_id':pid,'session_ref':manifest['session_ref'],'graph_sha256':manifest['model_sha256'],
                    'renderer':manifest['renderer'],'renderer_sha256':manifest['renderer_sha256'],'pillow_version':manifest['pillow_version'],
                    'font_face_index':manifest['font_face_index'],'font_sha256':manifest['font_sha256'],'layout_engine':manifest['layout_engine']})
            if binding!=manifest['binding_sha256'] or ident!=digest({'binding_sha256':binding,'files':manifest['files']}):raise ValueError
        except (OSError,ValueError,KeyError,TypeError):raise Conflict('Diagram artifact integrity failed') from None
        return manifest,model

    def generate(self,pid,sid,gid,revision=None):
        self.store.project(pid);session=self.session_reader(pid,sid,revision)
        if (session.get('id'),session.get('project_id'))!=(sid,pid) or (revision is not None and session['revision']!=revision):
            raise Conflict('Discovery reader returned another scope/revision')
        if digest(session['payload'])!=session['sha256']:raise Conflict('Discovery payload SHA mismatch')
        matches=[g for g in session['payload']['candidate']['graphs'] if g['id']==gid]
        if len(matches)!=1:raise NotFound('Diagram does not exist exactly once in this session revision')
        try:typed=DiscoveryGraph.model_validate(matches[0])
        except ValueError as exc:raise InvalidInput('Invalid discovery graph: '+str(exc)) from exc
        claims={c['id'] for c in session['payload']['candidate']['claims']}
        if {n.id for n in typed.nodes}&{e.id for e in typed.edges}:raise InvalidInput('Diagram nodes and edges must not share IDs')
        if any(not set(item.claim_ids)<=claims for item in [*typed.nodes,*typed.edges]):raise InvalidInput('Diagram names missing discovery claims')
        if any(not item.claim_ids and not item.source_anchors for item in [*typed.nodes,*typed.edges]):raise InvalidInput('Diagram element has no actual requirement/source basis')
        PlanningProposals._validate_sources(SimpleNamespace(nodes=[*typed.nodes,*typed.edges],questions=[]),session['payload']['source_receipt'])
        graph=typed.model_dump();ref={'id':sid,'revision':session['revision'],'sha256':session['sha256']}
        font,font_raw=_font(self.source_root,self.font_path);font_hash=sha(font_raw)
        renderer_hash=sha(Path(__file__).read_bytes());layout_engine=engine_identity()
        binding=digest({'project_id':pid,'session_ref':ref,'graph_sha256':digest(graph),'renderer':RENDERER,
                       'renderer_sha256':renderer_hash,'pillow_version':Image.__version__,'font_face_index':font.index,'font_sha256':font_hash,'layout_engine':layout_engine})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            index=self._path(pid,'indexes',binding+'.json')
            if index.exists():
                try:
                    saved=json.loads(index.read_bytes())
                    if saved['binding_sha256']!=binding or digest(saved['payload'])!=saved['sha256']:raise ValueError
                    ident=saved['payload']['artifact_id']
                except (ValueError,KeyError,TypeError):raise Conflict('Diagram index integrity failed') from None
            else:
                svg,png,rendered=render_graph(graph,font,source_root=self.source_root,with_geometry=True);image_metadata(png)
                if rendered['layout_engine']!=layout_engine:raise Conflict('Diagram layout identity changed')
                model={'schema_version':'eeas.discovery-diagram-model.v1','project_id':pid,'session_ref':ref,'graph':graph,**rendered}
                blobs={'svg':(svg,'image/svg+xml','diagram.svg'),'png':(png,'image/png','diagram.png'),
                       'json':((canonical(model)+'\n').encode(),'application/json','model.json')}
                files={key:{'stored_name':name,'filename':gid+'.'+('json' if key=='json' else key),'media_type':mime,
                            'sha256':sha(raw),'byte_length':len(raw)} for key,(raw,mime,name) in blobs.items()}
                ident=digest({'binding_sha256':binding,'files':files});folder=self._path(pid,ident)
                stage=self._path(pid,'.pending-'+uuid.uuid4().hex);stage.mkdir(parents=True)
                for key,(raw,mime,name) in blobs.items():
                    self._write(stage/name,raw)
                manifest={'schema_version':'eeas.discovery-diagram-artifact.v1','id':ident,'project_id':pid,'session_ref':ref,
                          'graph_id':gid,'model_sha256':digest(graph),'binding_sha256':binding,'renderer':RENDERER,
                          'renderer_sha256':renderer_hash,'pillow_version':Image.__version__,'font_face_index':font.index,'font_sha256':font_hash,'files':files,'layout_engine':layout_engine,'render_geometry':rendered['render_geometry']}
                self._write(stage/'manifest.json',(canonical({'payload':manifest,'sha256':digest(manifest)})+'\n').encode());stage.rename(folder)
                index.parent.mkdir(parents=True,exist_ok=True);payload={'artifact_id':ident}
                self._write(index,(canonical({'binding_sha256':binding,'payload':payload,'sha256':digest(payload)})+'\n').encode())
            manifest,_=self._saved(pid,ident)
            if manifest['binding_sha256']!=binding:raise Conflict('Diagram index points to another source binding')
        return {'artifact_ref':{'id':ident,'revision':ref['revision'],'sha256':digest(manifest)},'session_ref':ref,
                'graph_id':gid,'model_sha256':manifest['model_sha256'],'files':manifest['files']}

    def read_artifact(self,pid,ref):
        ref=ArtifactRef.model_validate(ref);manifest,model=self._saved(pid,ref.id)
        if ref.revision!=manifest['session_ref']['revision'] or ref.sha256!=digest(manifest):raise Conflict('Diagram artifact ref revision/SHA mismatch')
        raw=self._path(pid,ref.id,'diagram.png').read_bytes();info=image_metadata(raw)
        if info['sha256']!=manifest['files']['png']['sha256']:raise Conflict('Diagram PNG changed while reading')
        return {'artifact_ref':ref.model_dump(),'session_ref':manifest['session_ref'],'graph':model['graph'],
                'model_sha256':manifest['model_sha256'],'png_bytes':raw,'png_sha256':info['sha256'],'manifest':manifest,
                **({'render_geometry':manifest['render_geometry'], 'render_source':{'layout':model['layout'],
                    'font_sha256':manifest['font_sha256'],'font_face_index':manifest['font_face_index'],
                    'renderer_sha256':manifest['renderer_sha256'],'layout_engine':manifest['layout_engine']}}
                   if 'render_geometry' in manifest else {})}

    def download(self,pid,sid,gid,revision,format):
        if format not in {'svg','png','json'}:raise InvalidInput('Diagram format must be svg, png or json')
        result=self.generate(pid,sid,gid,revision);manifest,model=self._saved(pid,result['artifact_ref']['id'])
        item=manifest['files'][format]
        raw=self._path(pid,manifest['id'],item['stored_name']).read_bytes()
        if sha(raw)!=item['sha256']:raise Conflict('Diagram download changed while reading')
        return raw,item,result
