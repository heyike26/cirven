"""Real API/SQLite sources, original PPTX fixtures, never a model or Office process."""
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from test_documents import project, upload
from test_diagram_documents import manual_requirement
from test_pptx_import import fixture, rewrite


def test_long_pptx_upload_graph_assets_receipt_and_native_readonly_scope(client, tmp_path):
    pid=project(client);raw=fixture(long=True,chart=True)
    response=upload(client,pid,raw,'中文 空格 会议记录.pptx')
    assert response.status_code==201,response.text
    doc=response.json();assert doc['format']=='pptx' and doc['character_length']>70000
    assert doc['sha256']==hashlib.sha256(raw).hexdigest()
    path=f"/api/projects/{pid}/documents/{doc['id']}"
    row=client.get(path).json();assert '资料真实末尾：不要漏掉本行' in row['content']
    structure=client.get(path+'/structure',params={'revision':1}).json()
    assert structure['sha256']==doc['sha256'] and 'content' not in structure
    assert len(structure['outline']['pages'])==len(structure['outline']['slides'])==2
    nodes=structure['graph']['nodes'];assert nodes and len({n['id'] for n in nodes})==len(nodes)
    assert all(n['id']==n['node_id'] and n['source']['part']==n['part_path'] for n in nodes)
    assert {n['source']['scope'] for n in nodes}=={'pptx:slide:1','pptx:slide:2'}
    assert any(n['kind']=='table_cell' for n in nodes)
    assets=client.get(path+'/assets').json()['assets'];linked=[a for a in assets if a['source_anchors']]
    assert len(linked)==2
    for asset in linked:
        for anchor in asset['source_anchors']:
            assert anchor['raw_sha256']==doc['sha256'] and anchor['scope'].startswith('pptx:slide:')
            assert anchor['part'].startswith('ppt/slides/') and anchor['relationship_id']
        actual=client.get(path+f"/assets/{asset['asset_id']}",params={'revision':1})
        assert hashlib.sha256(actual.content).hexdigest()==asset['sha256']
    receipt=client.app.state.store.document_receipt(pid,doc['id'],1)
    assert receipt['content_sha256']==hashlib.sha256(row['content'].encode()).hexdigest()
    assert len(receipt['parse_manifest_sha256'])==64
    hit=client.get(f'/api/projects/{pid}/search',params={'keyword':'资料真实末尾'}).json()['hits'][0]
    assert '资料真实末尾' in hit['excerpt']
    requirement=client.post(f'/api/projects/{pid}/requirements',json=manual_requirement(doc,hit['line_start']))
    assert requirement.status_code==201,requirement.text
    req=requirement.json()
    task=client.post(f'/api/projects/{pid}/tasks',json={'goal':'保留幻灯片原文范围','requirement_refs':[
        {'requirement_id':req['id'],'revision':1,'sha256':req['sha256']}]}).json()
    route=f"/api/projects/{pid}/tasks/{task['id']}/runs"
    admitted=client.post(route,json={'revision':1,'operation_id':'original-pptx-only-scope'})
    assert admitted.status_code==202,admitted.text
    run=admitted.json();captured=json.loads(run['frozen_inputs'])['documents'][0]
    assert base64.b64decode(captured['raw_base64'])==raw and captured['parse_receipt']==receipt
    native=client.app.state.native_reviews.frozen_scope(pid,task['id'],run['id'])
    assert any('资料真实末尾：不要漏掉本行' in text for text in native['source_files'].values())
    from packages.domain.store import Store
    reopened=Store(tmp_path/'eeas.sqlite3')
    assert reopened.document(pid,doc['id'],1)['raw_bytes']==raw
    assert reopened.document_assets(pid,doc['id'],1)==assets
    assert reopened.document_receipt(pid,doc['id'],1)==receipt


def test_pptx_revision_old_sources_crossproject_and_history_remain_immutable(client):
    pid=project(client);raw=fixture();first=upload(client,pid,raw,'version.pptx').json()
    path=f"/api/projects/{pid}/documents/{first['id']}"
    old_structure=client.get(path+'/structure').json()
    req=client.post(f'/api/projects/{pid}/requirements',json=manual_requirement(first)).json()
    other=project(client,'另一项目')
    assert client.get(f"/api/projects/{other}/documents/{first['id']}").status_code==404
    assert client.get(f"/api/projects/{other}/documents/{first['id']}/structure").status_code==404
    changed=rewrite(raw,edit=lambda n,b:b.replace('嵌套来源起点'.encode(),'来源更新的新起点'.encode()) if n=='ppt/slides/slide1.xml' else b)
    updated=client.put(path,files={'file':('updated.pptx',changed)},data={'expected_revision':1})
    assert updated.status_code==200,updated.text
    assert updated.json()['revision']==2 and updated.json()['sha256']==hashlib.sha256(changed).hexdigest()
    assert client.get(path+'/download',params={'revision':1}).content==raw
    historical=client.get(path+'/structure',params={'revision':1}).json()
    assert {k:v for k,v in historical.items() if k not in {'filename','current_revision'}}=={k:v for k,v in old_structure.items() if k not in {'filename','current_revision'}}
    assert historical['filename']=='updated.pptx' and historical['current_revision']==2
    assert client.get(f"/api/projects/{pid}/requirements/{req['id']}").json()['source_status']=='STALE'
    conflict=client.put(path,files={'file':('unchanged.pptx',raw)},data={'expected_revision':1})
    assert conflict.status_code==409
    assert client.get(path).json()['revision']==2


def test_unsafe_pptx_unsupported_legacy_and_bad_images_write_no_documents(client):
    pid=project(client);raw=fixture()
    cases=[('legacy.ppt',raw),('macro.pptm',raw),('broken.pptx',b'not zip'),
           ('traversal.pptx',rewrite(raw,extra=[('../escape',b'x')])),
           ('duplicate.pptx',rewrite(raw,extra=[('PPT/slides/slide1.xml',b'<x/>')])),
           ('badimage.pptx',rewrite(raw,edit=lambda n,b:b'bad' if n.endswith('.png') else b))]
    for name,payload in cases:
        response=upload(client,pid,payload,name)
        assert response.status_code==422,(name,response.text)
        assert client.get(f'/api/projects/{pid}/documents').json()['documents']==[]
    with client.app.state.store.connect() as db:
        for table in ('documents','document_versions','document_parses','document_assets'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_unsupported_media_warning_is_persisted_and_original_archive_retained(client):
    raw=rewrite(fixture(),extra=[('ppt/media/unknown.svg',b'<svg xmlns="http://www.w3.org/2000/svg"/>')],
        edit=lambda n,b:b.replace(b'</Types>',b'<Default Extension="svg" ContentType="image/svg+xml"/></Types>') if n=='[Content_Types].xml' else b)
    pid=project(client);response=upload(client,pid,raw,'partial.pptx');assert response.status_code==201,response.text
    doc=response.json();path=f"/api/projects/{pid}/documents/{doc['id']}"
    assert 'UNSUPPORTED_IMAGE_NOT_RENDERED: ppt/media/unknown.svg' in doc['warnings']
    assert client.get(path).json()['warnings']==doc['warnings']
    assert client.get(path+'/download',params={'revision':1}).content==raw
    assert not any(a['member_path'].endswith('.svg') for a in client.get(path+'/assets').json()['assets'])


def test_powerpoint_softbreaks_have_store_line_consistent_citations(client):
    deck=Presentation();slide=deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(0,0,Inches(3),Inches(2)).text='第一段\v软换行最后条件'
    target=BytesIO();deck.save(target)
    pid=project(client);response=upload(client,pid,target.getvalue(),'softbreak.pptx');assert response.status_code==201,response.text
    doc=response.json();row=client.get(f"/api/projects/{pid}/documents/{doc['id']}").json()
    assert '\v' not in row['content'] and '第一段\n软换行最后条件' in row['content']
    node=next(n for n in row['graph']['nodes'] if n['text']=='第一段\n软换行最后条件')
    assert node['line_anchor_scope']=='TEXT_MATCH'
    assert '\n'.join(row['content'].splitlines()[node['line_start']-1:node['line_end']])==node['text']
    hit=client.get(f'/api/projects/{pid}/search',params={'keyword':'软换行最后条件'}).json()['hits'][0]
    assert hit['line_start']==node['line_end']


def test_upload_accept_lists_pptx_without_macro_or_legacy_and_reader_source_schema():
    from html.parser import HTMLParser
    class Fields(HTMLParser):
        def handle_starttag(self,tag,attrs):
            data=dict(attrs)
            if data.get('id')=='document-file':self.accept=data['accept'].split(',')
    root=Path(__file__).resolve().parents[1];parser=Fields();parser.feed((root/'apps/web/index.html').read_text())
    assert '.pptx' in parser.accept and not {'.ppt','.pptm'} & set(parser.accept)
    from packages.domain.document_import import parse_document
    parsed=parse_document('source.pptx',fixture())
    assert all(n['source']['scope'] and n['id'] for n in parsed.graph['nodes'])
    assert all(e['source'] in {n['id'] for n in parsed.graph['nodes']} and e['target'] in {n['id'] for n in parsed.graph['nodes']} for e in parsed.graph['edges'])
