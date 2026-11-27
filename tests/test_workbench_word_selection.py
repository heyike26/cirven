"""Actual generated DOCX paragraphs bound to exact report/discovery bytes."""
import hashlib
import copy
from test_discovery_workbench import create,edited,candidate
from test_workbench_feedback import body,route
from test_project_intake import project


def test_actual_word_selection_records_quotation_and_refuses_changed_or_cross_project_reports(client):
    pid,_,_,view=create(client);view=edited(client,pid,view)
    base=f'/api/projects/{pid}/discovery/sessions/{view["id"]}'
    generated=client.post(base+'/reports',json={'operation_id':'word','expected_revision':view['revision']})
    assert generated.status_code==201,generated.text
    report=generated.json();artifact=report['artifact']
    text_url=f'/api/projects/{pid}/workbench/reports/{view["id"]}/{artifact["id"]}/text'
    response=client.get(text_url);assert response.status_code==200,response.text
    mapping=response.json();assert mapping['docx_sha256']==artifact['sha256']
    assert mapping['artifact_ref']==body(view)['artifact_ref']
    requirement=view['payload']['candidate']['claims'][0]['text']
    paragraph=next(p for p in mapping['paragraphs'] if requirement in p['text'])
    start=paragraph['text'].index(requirement)+2;end=start+7
    quote=paragraph['text'][start:end];request=body(view,op='word-annotation',tool='TEXT_SELECTION')
    request['selection']={'page_number':1,'text_selection':{'text':quote,
        'quote_sha256':hashlib.sha256(quote.encode()).hexdigest(),'locator':{
        'kind':'REPORT_PARAGRAPH','source_id':view['id'],'source_kind':'DISCOVERY',
        'artifact_id':artifact['id'],'docx_sha256':artifact['sha256'],'paragraph_index':paragraph['index'],
        'char_start':start,'char_end':end}}}
    recorded=client.post(route(pid),json=request);assert recorded.status_code==200,recorded.text
    assert recorded.json()['native_requested'] is False
    assert recorded.json()['payload']['selection']['text_selection']['text']==quote
    forged=copy.deepcopy(request);forged['operation_id']='wrong-word';forged['selection']['text_selection']['locator']['docx_sha256']='0'*64
    assert client.post(route(pid),json=forged).status_code==409
    other=project(client)
    assert client.get(text_url.replace('/'+pid+'/', '/'+other+'/')).status_code==404
    update=client.put(base,json={'operation_id':'new-requirements','expected_revision':view['revision'],
        'candidate':candidate(),'change_reason':'用户更新需求'}).json()
    stale=copy.deepcopy(request);stale['operation_id']='stale-word';stale['artifact_ref']=body(update)['artifact_ref']
    assert client.post(route(pid),json=stale).status_code==409
    assert client.get(text_url).json()==mapping
    assert client.get(artifact['download_url']).headers['etag']=='"'+artifact['sha256']+'"'
