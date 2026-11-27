"""Real ledger/API annotation lifecycles; only the external planner is substituted."""
import copy
import hashlib
import sqlite3
import pytest
from packages.domain.workbench_feedback import WorkbenchFeedback
from test_discovery_workbench import create,edited,candidate,ProtocolOnlyPlanner
from test_project_intake import project


def body(view,op='annotation',tool='COMMENT'):
    return {'operation_id':op,'artifact_ref':{'kind':'DISCOVERY','id':view['id'],
        'revision':view['revision'],'sha256':view['sha256']},'tool':tool,
        'comment':'请补充这个环节的输入和验收。','selection':{}}


def route(pid):return f'/api/projects/{pid}/workbench/feedback'


def text_selection(text,pointer='/claims/0/text',start=0,end=None):
    end=len(text) if end is None else end;quote=text[start:end]
    return {'text':quote,'quote_sha256':hashlib.sha256(quote.encode()).hexdigest(),
        'offset_unit':'UNICODE_CODEPOINT','locator':{'kind':'CANDIDATE_FIELD',
        'field_pointer':pointer,'char_start':start,'char_end':end}}


def test_recording_annotation_is_not_native_submission_and_survives_reload(client):
    pid,_,_,view=create(client);view=edited(client,pid,view)
    request=body(view,tool='RECTANGLE');request['selection']={'graph_id':'architecture',
        'node_ids':['source'],'normalized_points':[{'x':.1,'y':.2},{'x':.8,'y':.9}],
        'viewport':{'width':1024,'height':768,'zoom':1.2,'pan':{'x':-10,'y':20}}}
    r=client.post(route(pid),json=request);assert r.status_code==200,r.text
    value=r.json();assert value['status']=='RECORDED' and value['native_requested'] is False
    assert value['session']['revision']==view['revision']
    assert client.post(route(pid),json=request).json()==value
    restored=WorkbenchFeedback(client.app.state.store,client.app.state.discovery)
    assert restored.get(pid,value['feedback_ref']['id'])['payload']==value['payload']
    assert len(client.get(route(pid),params={'session_id':view['id']}).json()['feedback'])==1
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM task_runs').fetchone()[0]==0
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE workbench_feedback SET payload='{}'")
    other=project(client)
    assert client.get(route(other)+'/'+value['feedback_ref']['id']).status_code==404
    changed=copy.deepcopy(request);changed['comment']='另一条批注'
    assert client.post(route(pid),json=changed).status_code==409


def test_text_selection_is_exact_unicode_quotation_without_editing(client):
    pid,_,_,view=create(client);value=candidate();value['summary']='方案😀输入与验收要明确。'
    view=client.put(f'/api/projects/{pid}/discovery/sessions/{view["id"]}',json={
        'operation_id':'unicode','expected_revision':1,'candidate':value,'change_reason':'用户补充'}).json()
    request=body(view,tool='TEXT_SELECTION')
    request['selection']['text_selection']=text_selection(value['summary'],'/summary',2,6)
    response=client.post(route(pid),json=request);assert response.status_code==200,response.text
    assert response.json()['payload']['selection']['text_selection']['text']=='😀输入与'
    assert response.json()['session']['payload']['candidate']==view['payload']['candidate']
    assert response.json()['session']['revision']==view['revision']
    forged=copy.deepcopy(request);forged['operation_id']='forged';forged['selection']['text_selection']['text']='另一个意思'
    assert client.post(route(pid),json=forged).status_code==422
    bad=copy.deepcopy(request);bad['operation_id']='out-of-range';bad['selection']['text_selection']['locator']['char_end']=999
    assert client.post(route(pid),json=bad).status_code==422
    no_locator=body(view,op='missing',tool='TEXT_SELECTION')
    assert client.post(route(pid),json=no_locator).status_code==422


def test_selected_source_chunk_has_actual_byte_revision_and_exact_quote(client):
    pid,_,_,view=create(client,files=True)
    doc=view['payload']['source_receipt']['documents'][-1];chunk=doc['chunks'][0]
    quote=chunk['content'][:6];sha=hashlib.sha256(quote.encode()).hexdigest()
    request=body(view,tool='TEXT_SELECTION');request['selection']['text_selection']={
        'text':quote,'quote_sha256':sha,'locator':{'kind':'SOURCE_CHUNK','source_anchor':{
        'document_id':doc['document_id'],'revision':doc['revision'],'raw_sha256':doc['raw_sha256'],
        'chunk_index':chunk['index'],'chunk_sha256':chunk['sha256'],'char_start':chunk['char_start'],
        'char_end':chunk['char_start']+len(quote),'quote_sha256':sha}}}
    assert client.post(route(pid),json=request).status_code==200
    request['operation_id']='bad-source';request['selection']['text_selection']['locator']['source_anchor']['raw_sha256']='0'*64
    assert client.post(route(pid),json=request).status_code==422


def test_user_text_edit_applies_actual_change_then_stale_annotation_cannot_overwrite(client):
    pid,_,_,view=create(client);view=edited(client,pid,view)
    old=client.post(route(pid),json=body(view)).json()
    request=body(view,op='edit-selection',tool='TEXT_EDIT');request['edited_candidate']=candidate()
    request['edited_candidate']['claims'][0]['text']='用户直接修改后的要求。'
    response=client.post(route(pid),json=request);assert response.status_code==200,response.text
    updated=response.json()['session'];assert updated['revision']==view['revision']+1
    assert updated['payload']['candidate']['claims'][0]['text']=='用户直接修改后的要求。'
    assert updated['payload']['status']=='DRAFT'
    assert response.json()['revision_target_ref']['sha256']==updated['sha256']
    assert client.post(route(pid),json=request).json()==response.json()
    revise=client.post(route(pid)+'/'+old['feedback_ref']['id']+'/revise',json={
        'operation_id':'stale-send','expected_revision':view['revision']})
    assert revise.status_code==409,revise.text
    assert client.get(f'/api/projects/{pid}/discovery/sessions/{view["id"]}').json()['sha256']==updated['sha256']


@pytest.mark.parametrize('selection',[{'graph_id':'architecture','node_ids':['missing']},
    {'node_ids':['source']},{'graph_id':'missing'},
    {'graph_id':'architecture','normalized_points':[{'x':-1,'y':.2},{'x':.5,'y':.7}]}])
def test_forged_graph_selection_rejected_without_ledger_or_model_calls(client,selection):
    pid,_,_,view=create(client);view=edited(client,pid,view);request=body(view)
    request['selection']=selection
    assert client.post(route(pid),json=request).status_code==422
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM workbench_feedback').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==0


def test_multiple_draft_attachments_submit_as_one_native_advance(client,tmp_path):
    pid,_,_,view=create(client);view=edited(client,pid,view)
    first=client.post(route(pid),json=body(view,op='first')).json()
    second_request=body(view,op='second',tool='TEXT_SELECTION')
    second_request['comment']='同时补充出处。'
    second_request['selection']['text_selection']=text_selection(view['payload']['candidate']['claims'][0]['text'])
    second=client.post(route(pid),json=second_request).json()
    fake=ProtocolOnlyPlanner(client.app.state.store);client.app.state.discovery.transport=fake
    folder=tmp_path.parent/(tmp_path.name+'资料');folder.mkdir()
    binding=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'folder','root':str(folder)}).json()
    workspace={k:binding[k] for k in ['id','revision','binding_sha256']}
    base=f'/api/projects/{pid}/discovery/sessions/{view["id"]}'
    request={'operation_id':'full-message','expected_revision':view['revision'],
        'message':'两处一起改，并说明修改原因。','workspace_ref':workspace,
        'feedback_refs':[first['feedback_ref'],second['feedback_ref']]}
    r=client.post(base+'/advance',json=request);assert r.status_code==200,r.text
    assert len(fake.calls)==1 and first['feedback_ref']['id'] in fake.calls[0]['prompt']
    assert second['feedback_ref']['id'] in fake.calls[0]['prompt']
    assert '两处一起改' in fake.calls[0]['prompt'] and '同时补充出处' in fake.calls[0]['prompt']
    assert client.post(base+'/advance',json=request).json()==r.json() and len(fake.calls)==1
    assert r.json()['payload']['status']=='DRAFT'
    bad=copy.deepcopy(request);bad['operation_id']='duplicate-attachments';bad['feedback_refs']*=2
    assert client.post(base+'/advance',json=bad).status_code==422 and len(fake.calls)==1
