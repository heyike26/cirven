"""Actual offline model inference and immutable quotation feedback over a real folder PNG."""
from pathlib import Path
from copy import deepcopy
import hashlib
from PIL import Image,ImageDraw,ImageFont
from packages.domain.local_workspaces import WorkspaceInput
from test_discovery_workbench import create,edited
from test_workbench_feedback import body,route
from test_project_intake import project

ROOT=Path(__file__).resolve().parents[1]


def test_actual_offline_ocr_api_quote_feedback_reload_and_source_drift(client,tmp_path):
    pid,_,_,view=create(client);view=edited(client,pid,view)
    folder=tmp_path.parent/(tmp_path.name+'-ocr-source');folder.mkdir()
    image=Image.new('RGB',(1500,500),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(str(ROOT/'toolchain/fonts/NotoSansCJK-Regular.ttc'),56,index=2)
    lines=['需求确认 REQ-001','离线识别 Offline OCR','报告版本 Version 3.10']
    for index,line in enumerate(lines):draw.text((60,50+index*130),line,font=font,fill='black')
    image.save(folder/'实际屏幕样图.png')
    ws=client.app.state.local_workspaces;binding=ws.bind(pid,WorkspaceInput(root=str(folder),idempotency_key='ocr-folder'))
    source=ws.read(pid,binding['id'],'实际屏幕样图.png')['source_ref']
    bitmap={'kind':'LOCAL_FILE','file_source_ref':source,'sha256':source['sha256']}
    r=client.post(f'/api/projects/{pid}/workbench/ocr',json={'bitmap_ref':bitmap});assert r.status_code==200,r.text
    result=r.json();recognition=result['payload']['recognition'];assert recognition['network_access']=='NEVER'
    assert recognition['text'].splitlines()==lines and recognition['qualification']=='OBSERVATION_ONLY'
    first=recognition['lines'][0];quote=first['text'][:4]
    request=body(view,op='ocr-quote',tool='TEXT_SELECTION');request['selection']={'bitmap_ref':bitmap,
        'ocr_ref':result['observation_ref'],'text_selection':{'text':quote,
        'quote_sha256':hashlib.sha256(quote.encode()).hexdigest(),'locator':{'kind':'OCR_LINE',
        'observation_ref':result['observation_ref'],'line_id':first['id'],'char_start':0,'char_end':4}}}
    feedback=client.post(route(pid),json=request);assert feedback.status_code==200,feedback.text
    assert feedback.json()['native_requested'] is False
    assert client.get(f'/api/projects/{pid}/workbench/ocr/{result["observation_ref"]["id"]}').json()==result
    assert client.post(f'/api/projects/{pid}/workbench/ocr',json={'bitmap_ref':bitmap}).json()==result
    assert client.get(f'/api/projects/{project(client)}/workbench/ocr/{result["observation_ref"]["id"]}').status_code==404
    # Equal pixels in a different admitted source are still separate observations.
    image.save(folder/'另一份样图.png')
    other_source=ws.read(pid,binding['id'],'另一份样图.png')['source_ref']
    other_bitmap={'kind':'LOCAL_FILE','file_source_ref':other_source,'sha256':other_source['sha256']}
    other=client.post(f'/api/projects/{pid}/workbench/ocr',json={'bitmap_ref':other_bitmap})
    assert other.status_code==200,other.text
    forged=deepcopy(request);forged['operation_id']='cross-observation'
    forged['selection'].update(bitmap_ref=other_bitmap,ocr_ref=other.json()['observation_ref'])
    assert client.post(route(pid),json=forged).status_code==422
    forged['operation_id']='cross-image';forged['selection']['ocr_ref']=result['observation_ref']
    assert client.post(route(pid),json=forged).status_code==409
    for absent in ['bitmap_ref','ocr_ref']:
        missing=deepcopy(request);missing['operation_id']='missing-'+absent
        del missing['selection'][absent]
        assert client.post(route(pid),json=missing).status_code==422
    Image.new('RGB',(1500,500),'black').save(folder/'实际屏幕样图.png')
    changed={**request,'operation_id':'stale-ocr'}
    assert client.post(route(pid),json=changed).status_code==409
    with client.app.state.store.connect() as c:assert c.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==0
