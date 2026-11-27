"""Real source/SQLite/API tests; one injected transport is explicitly mock."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3

import pytest

from packages.domain.intake import digest
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_schema import ProposalInput, ProposalEdit, PlanningCandidate
from packages.domain.store import Conflict
from test_project_intake import project, upload


def candidate():
    return {'title':'原创复杂任务','summary':'依据资料规划，设备接口尚不清楚。','goals':['输出可验证方案'],
        'constraints':['不改变原始资料'],'nodes':[
            {'id':'design','title':'明确接口与方案','description':'先核实接口，再给出方案。','outputs':['方案说明书'],
             'acceptance':['逐项说明接口并由用户接受'],'capabilities':['document-review']},
            {'id':'implement','title':'实现独立原型','description':'使用确认的方案实现。','depends_on':['design'],
             'outputs':['可运行原型'],'acceptance':['按已确认接口自测']},
            {'id':'archive','title':'整理原始资料','description':'保留来源与哈希。',
             'outputs':['来源索引'],'acceptance':['字节SHA与原件一致']}],
        'questions':[{'id':'interface','text':'设备使用什么接口？','reason':'资料未说明，影响方案及实现。',
            'category':'MISSING_INPUT','blocking':True,'affects_node_ids':['design'],
            'requested_materials':['接口说明或原理图']}]}


def make(client, value=None, mode='manual', key='create-original'):
    pid=project(client); doc,ref=upload(client,pid,'# 原创说明\r\n接口待提供。\r\n尾段原文。\r\n')
    body={'operation_id':key,'prompt':'拆解原始资料并澄清接口','materials':[ref],'mode':mode}
    if mode=='manual': body['candidate']=value or candidate()
    result=client.post(f'/api/projects/{pid}/planning/proposals',json=body)
    assert result.status_code==201,result.text
    return pid,doc,body,result.json()


def confirm_body(value, resolutions=(), key='confirm-original'):
    return {'operation_id':key,'expected_revision':value['revision'],'proposal_sha256':value['sha256'],
            'user_confirmed':True,'resolutions':list(resolutions)}


def test_real_source_network_and_taskbook_have_exact_content_no_execution(client):
    pid,doc,body,value=make(client)
    assert value['payload']['source_receipt']['documents'][0]['text_or_selected_chunks']=='# 原创说明\r\n接口待提供。\r\n尾段原文。\r\n'
    source=value['payload']['source_receipt']
    assert source['sha256']==digest({k:source[k] for k in ('documents','coverage')})
    statuses={n['id']:n['planning_status'] for n in value['flow']['nodes']}
    assert statuses=={'design':'NEEDS_INPUT','implement':'NEEDS_INPUT','archive':'DRAFT','interface':'NEEDS_INPUT'}
    assert value['payload']['execution']['model_calls']==0 and not value['flow']['execution_started']
    assert value['payload']['taskbook_ref']['status']=='DRAFT'
    with client.app.state.store.connect() as connection:
        for table in ['tasks','task_runs','runner_jobs','agent_executions']:
            assert connection.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
    assert client.post(f'/api/projects/{pid}/planning/proposals',json=body).json()==value


def test_answer_is_durable_but_does_not_accept_question_or_start_task(client):
    pid,_,_,value=make(client); base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    answer={'operation_id':'answer-1','expected_revision':1,'question_id':'interface','text':'使用UART，说明稍后补充。'}
    saved=client.post(base+'/answers',json=answer); assert saved.status_code==200,saved.text
    assert not saved.json()['accepted']
    assert client.post(base+'/answers',json=answer).json()==saved.json()
    assert client.get(base+'/pending').json()['questions'][0]['status']=='ANSWERED_PENDING_ACCEPTANCE'
    assert client.post(base+'/confirm',json=confirm_body(value)).status_code==409
    service=PlanningProposals(client.app.state.store,client.app.state.store.source_root)
    assert service.get(pid,value['id'])['answers'][0]['text']==answer['text']
    markdown=client.get(base+'/pending/markdown')
    assert markdown.status_code==200 and '使用UART'.encode() in markdown.content
    assert b'ANSWERED_PENDING_ACCEPTANCE' in markdown.content


def test_explicit_answer_acceptance_creates_new_plan_revision_not_execution(client):
    pid,_,_,value=make(client); base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    answer=client.post(base+'/answers',json={'operation_id':'answer','expected_revision':1,'question_id':'interface','text':'使用UART'}).json()
    body=confirm_body(value,[{'question_id':'interface','mode':'ANSWER_ACCEPTED','answer_id':answer['answer_id'],'reason':'用户本次明确接受接口'}])
    response=client.post(base+'/confirm',json=body); assert response.status_code==200,response.text
    approved=response.json(); assert approved['revision']==2 and approved['payload']['status']=='CONFIRMED'
    assert not approved['payload']['confirmation']['execution_authorized']
    assert client.get(base+'?revision=1').json()['payload']['status']=='DRAFT'
    assert client.get(base+'/pending').json()['pending_count']==0
    harness=client.get(base+'/harness').json(); assert harness['status']=='CONFIRMED_PLAN'
    assert len(harness['prompts'])==3
    assert all(hashlib.sha256(p['content'].encode()).hexdigest()==p['sha256'] for p in harness['prompts'])
    assert '使用UART' in harness['prompts'][0]['content']
    assert client.post(base+'/confirm',json=body).json()==approved


@pytest.mark.parametrize('change',[ 'dependency_cycle','parent_cycle','unknown_dependency','question_scope','duplicate_node'])
def test_invalid_graph_rejected_before_any_proposal_creation(client,change):
    value=candidate()
    if change=='dependency_cycle': value['nodes'][0]['depends_on']=['implement']
    if change=='parent_cycle':
        value['nodes'][0]['parent_id']='implement'; value['nodes'][1]['parent_id']='design'
    if change=='unknown_dependency': value['nodes'][0]['depends_on']=['absent']
    if change=='question_scope': value['questions'][0]['affects_node_ids']=['absent']
    if change=='duplicate_node': value['nodes'].append(copy.deepcopy(value['nodes'][0]))
    pid=project(client); _,ref=upload(client,pid,'原创资料')
    result=client.post(f'/api/projects/{pid}/planning/proposals',json={'operation_id':'invalid','prompt':'规划',
        'materials':[ref],'mode':'manual','candidate':value})
    assert result.status_code==422
    with client.app.state.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM planning_proposals').fetchone()[0]==0


def test_source_drift_keeps_old_network_pending_file_and_blocks_acceptance(client):
    pid,doc,body,value=make(client); base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    changed=client.put(f'/api/projects/{pid}/documents/{doc["id"]}',files={'file':('变化.md',b'changed')},data={'expected_revision':'1'})
    assert changed.status_code==200
    old=client.get(base).json(); assert old['review_status']=='NEEDS_REVIEW'
    assert old['payload']==value['payload']
    assert client.get(base+'/pending/markdown').status_code==200
    assert client.post(base+'/confirm',json=confirm_body(value)).status_code==409
    assert client.post(f'/api/projects/{pid}/planning/proposals',json=body).json()==value


def test_concurrent_cas_only_one_revision_and_preserves_question_history(client):
    pid,_,_,value=make(client); service=client.app.state.planning_proposals
    def edit(i):
        body=ProposalEdit(operation_id='edit-'+str(i),expected_revision=1,candidate=PlanningCandidate.model_validate(candidate()),change_reason='原创修订'+str(i))
        try:return service.edit(pid,value['id'],body)
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=5) as pool: results=list(pool.map(edit,range(5)))
    assert sum(v is not None for v in results)==1
    assert service.get(pid,value['id'])['revision']==2
    with client.app.state.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM planning_proposal_versions').fetchone()[0]==2


def test_answer_from_changed_question_and_cross_project_ref_cannot_resolve(client):
    pid,_,_,value=make(client); base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    answer=client.post(base+'/answers',json={'operation_id':'answer','expected_revision':1,'question_id':'interface','text':'UART'}).json()
    new=candidate(); new['questions'][0]['text']='接口速率是多少？'
    edited=client.put(base,json={'operation_id':'edit','expected_revision':1,'candidate':new,'change_reason':'问题更具体'}).json()
    resolution={'question_id':'interface','mode':'ANSWER_ACCEPTED','answer_id':answer['answer_id'],'reason':'同意'}
    assert client.post(base+'/confirm',json=confirm_body(edited,[resolution])).status_code==409
    other=project(client)
    assert client.get(f'/api/projects/{other}/planning/proposals/{value["id"]}').status_code==404
    with client.app.state.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM planning_proposal_versions').fetchone()[0]==2


def test_source_outline_cannot_masquerade_as_semantic_plan(client):
    pid,_,_,value=make(client,mode='source_outline'); base=f'/api/projects/{pid}/planning/proposals/{value["id"]}'
    assert all(n['origin']=='SOURCE_OUTLINE' for n in value['payload']['candidate']['nodes'])
    assert value['payload']['execution']['model_calls']==0
    assert client.post(base+'/confirm',json=confirm_body(value,[{'question_id':'outline-review','mode':'ASSUMPTION_ACCEPTED','assumption':'已了解','reason':'明确确认'}])).status_code==409
    edited=client.put(base,json={'operation_id':'actual-tasks','expected_revision':1,'candidate':candidate(),'change_reason':'用户编写实际任务'}).json()
    assert edited['payload']['mode']=='manual'


def test_model_call_runs_outside_sql_lock_and_duplicate_does_not_call_again(client):
    pid=project(client); _,ref=upload(client,pid,'原创规划来源')
    class Workspace:
        def get(self,*args):return {'binding_sha256':'b'*64}
    class Transport:
        calls=0
        async def propose(self,request):
            self.calls+=1
            # A different connection can write while the native request waits.
            with client.app.state.store.connect() as c:
                c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE projects SET description=? WHERE id=?',('调用中仍能交互',pid))
            return {'status':'RETURNED','source_receipt_sha256':request['source_receipt']['sha256'],
                    'candidate_json':candidate(),'usage':{'status':'UNKNOWN'},'model':'mock-original','session_id':'mock-session'}
    service=client.app.state.planning_proposals; transport=Transport(); service.transport=transport; service.workspaces=Workspace()
    body={'operation_id':'model-original','prompt':'拆解','materials':[ref],'workspace_ref':{'id':'a'*64,'revision':1,'binding_sha256':'b'*64}}
    first=client.post(f'/api/projects/{pid}/planning/proposals',json=body); assert first.status_code==201,first.text
    assert client.post(f'/api/projects/{pid}/planning/proposals',json=body).json()==first.json()
    assert transport.calls==1
    assert first.json()['payload']['execution']['usage']['status']=='UNKNOWN'


def test_forged_source_anchor_is_recorded_failure_and_not_retried(client):
    pid=project(client); doc,ref=upload(client,pid,'原创资料')
    c=candidate(); c['nodes'][0]['source_anchors']=[{'document_id':doc['id'],'revision':1,'raw_sha256':doc['sha256'],
        'chunk_index':0,'chunk_sha256':doc['sha256'],'char_start':0,'char_end':4,'quote_sha256':'a'*64}]
    body={'operation_id':'bad-anchor','prompt':'拆解','materials':[ref],'mode':'manual','candidate':c}
    r=client.post(f'/api/projects/{pid}/planning/proposals',json=body); assert r.status_code==422
    assert r.json()['status']=='FAILED'
    assert client.get(f'/api/projects/{pid}/planning/operations/bad-anchor').json()==r.json()
    assert client.post(f'/api/projects/{pid}/planning/proposals',json=body).json()==r.json()
    body['prompt']='变了'; assert client.post(f'/api/projects/{pid}/planning/proposals',json=body).status_code==409


def test_immutable_revisions_answers_events_and_results(client):
    _,_,_,value=make(client)
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with client.app.state.store.connect() as c:c.execute('UPDATE planning_proposal_versions SET sha256="forged"')
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with client.app.state.store.connect() as c:c.execute('DELETE FROM planning_proposal_events')
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with client.app.state.store.connect() as c:c.execute('UPDATE planning_proposal_operations SET result="forged"')


def test_large_plan_1000_nodes_is_complete_and_declares_taskbook_capacity(client):
    c=candidate(); c['questions']=[]
    template=c['nodes'][0]; c['nodes']=[{**template,'id':'node-'+str(i),'title':'原创大型节点'+str(i)} for i in range(1000)]
    pid,_,_,value=make(client,c)
    assert len(value['flow']['nodes'])==1000 and value['flow']['render_limit'] is None
    assert value['payload']['taskbook_ref']['status']=='CAPACITY_REVIEW_REQUIRED'
    assert len(client.get(f'/api/projects/{pid}/planning/proposals/{value["id"]}/harness').json()['prompts'])==1000
