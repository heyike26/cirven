"""Real API/SQLite/source state; injected planner is explicitly a protocol mock."""
import asyncio
import copy
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from packages.domain.discovery import DiscoveryWorkbench
from packages.domain.discovery_schema import DiscoveryEdit, DiscoveryCandidate
from packages.domain.intake import digest
from packages.domain.store import Conflict
from test_project_intake import project,upload


def create(client,entry='PROBLEM',files=False):
    pid=project(client);refs=[];doc=None
    if files or entry=='TASK_FILE':doc,ref=upload(client,pid,'原始任务：保留来源，逐条确认要求，再拆分。');refs=[ref]
    body={'operation_id':'start','title':'需求共创原创项目','entry_mode':entry,
          'statement':'我需要减少资料不清导致的工程返工。','materials':refs}
    r=client.post(f'/api/projects/{pid}/discovery/sessions',json=body)
    assert r.status_code==201,r.text
    return pid,doc,body,r.json()


def candidate():
    return {'title':'原创共创任务','phase':'SPECIFY','summary':'形成可核对来源的工程要求。',
        'claims':[{'id':'req','text':'输出可核对原始来源的需求清单。','category':'REQUIREMENT',
            'origin':'MODEL_SUGGESTION','acceptance':['每个条目含原文件身份与位置；缺证据标未知。']},
            {'id':'limit','text':'目标材料范围待用户提供。','category':'ASSUMPTION'}],
        'questions':[{'id':'scope','text':'首批核对哪些材料？','reason':'范围影响来源覆盖和验收。',
            'category':'DECISION','blocking':True,'affects_claim_ids':['req']},
            {'id':'optional','text':'是否需要双语输出？','reason':'影响文稿形式，不阻塞基础清单。',
             'category':'MISSING_INPUT','blocking':False}],
        'competitors':[{'id':'manual','name':'人工核对资料','alternative_type':'MANUAL','summary':'当前替代办法待实测。',
             'observations':[{'dimension':'核对成本','text':'暂无实际时间记录。','status':'UNKNOWN'}]}],
        'graphs':[{'id':'architecture','kind':'ARCHITECTURE','title':'候选架构',
            'nodes':[{'id':'source','label':'来源','description':'保留原件。','claim_ids':['req']},
                     {'id':'review','label':'核对','description':'核对与确认。','claim_ids':['req']}],
            'edges':[{'id':'input','source':'source','target':'review','relation':'读取','claim_ids':['req']},
                     {'id':'feedback','source':'review','target':'source','relation':'反馈','claim_ids':['req']}]}]}


def edited(client,pid,value):
    r=client.put(f'/api/projects/{pid}/discovery/sessions/{value["id"]}',json={'operation_id':'edit',
        'expected_revision':value['revision'],'candidate':candidate(),'change_reason':'用户补充要求与拟议架构'})
    assert r.status_code==200,r.text
    return r.json()


def confirm(client,pid,value,answer_id=None):
    body={'operation_id':'confirm','expected_revision':value['revision'],'session_sha256':value['sha256'],
          'user_confirmed':True,'accepted_claim_ids':['req'],'accepted_graph_ids':['architecture']}
    if answer_id:body['resolutions']=[{'question_id':'scope','mode':'ANSWER_ACCEPTED','answer_id':answer_id,'reason':'用户明确接受首批范围'}]
    return client.post(f'/api/projects/{pid}/discovery/sessions/{value["id"]}/confirm',json=body)


@pytest.mark.parametrize('entry',['PROBLEM','REQUIREMENTS','TASK_FILE'])
def test_three_entries_real_statement_and_no_automatic_decomposition(client,entry):
    pid,doc,body,value=create(client,entry)
    assert value['payload']['entry_mode']==entry and not value['readiness']['can_plan']
    assert len(value['payload']['source_receipt']['documents'])==(2 if doc else 1)
    first=value['payload']['source_receipt']['documents'][0]
    assert first['source'].startswith('discovery-user-statements:') and body['statement'] in first['text_or_selected_chunks']
    assert value['payload']['execution']['model_calls']==0
    path=f'/api/projects/{pid}/discovery/sessions'
    assert client.post(path,json=body).json()==value
    assert client.get(path).json()['sessions'][0]['id']==value['id']
    assert client.post(path+'/'+value['id']+'/plan',json={'operation_id':'too-early','expected_revision':1}).status_code==409
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM task_runs').fetchone()[0]==0


def test_task_file_requires_actual_document_not_fake_claim(client):
    pid=project(client)
    r=client.post(f'/api/projects/{pid}/discovery/sessions',json={'operation_id':'bad','title':'标题','entry_mode':'TASK_FILE','statement':'声称有文件'})
    assert r.status_code==422


def test_multilevel_confirmation_answer_and_restart_pending(client):
    pid,_,_,value=create(client);value=edited(client,pid,value)
    base=f'/api/projects/{pid}/discovery/sessions/{value["id"]}'
    assert confirm(client,pid,value).status_code==409
    a=client.post(base+'/answers',json={'operation_id':'answer','expected_revision':value['revision'],
        'question_id':'scope','text':'首批只核对现有两份资料。'}).json()
    optional=client.post(base+'/answers',json={'operation_id':'optional-answer','expected_revision':value['revision'],
        'question_id':'optional','text':'先只写中文。'}).json()
    assert not a['accepted'] and optional['question_sha256']
    pending=client.get(base+'/pending').json()
    assert pending['pending_count']==2 and all(q['status']=='ANSWERED_PENDING_ACCEPTANCE' for q in pending['questions'])
    assert confirm(client,pid,value).status_code==409
    approved=confirm(client,pid,value,a['id']);assert approved.status_code==200,approved.text
    v=approved.json();assert v['readiness']['can_plan'] and not v['readiness']['execution_authorized']
    # A new service reads the same immutable history; optional answers survive
    # confirmation's new revision but still are not accepted.
    service=DiscoveryWorkbench(client.app.state.store,client.app.state.planning_proposals)
    durable=service.pending(pid,value['id']);assert durable['pending_count']==1
    assert durable['questions'][0]['status']=='ANSWERED_PENDING_ACCEPTANCE'
    assert '先只写中文' in service.pending_markdown(pid,value['id'])
    old=client.get(base+'?revision=2').json();assert old['payload']['status']=='DRAFT'
    body={'operation_id':'re-edit','expected_revision':v['revision'],'candidate':candidate(),'change_reason':'改动要求'}
    r=client.put(base,json=body);assert r.status_code==200
    assert not r.json()['readiness']['can_plan'] and r.json()['payload']['accepted_claim_ids']==[]


@pytest.mark.parametrize('fault',['unanchored-fact','unanchored-official','unknown-claim','bad-edge','mindmap-cycle'])
def test_forged_facts_and_diagram_relations_rejected(client,fault):
    pid,_,_,value=create(client);c=candidate()
    if fault=='unanchored-fact':c['claims'][0]['origin']='SOURCE_FACT'
    if fault=='unanchored-official':c['competitors'][0]['observations'][0]['status']='OFFICIAL_CLAIM'
    if fault=='unknown-claim':c['graphs'][0]['nodes'][0]['claim_ids']=['missing']
    if fault=='bad-edge':c['graphs'][0]['edges'][0]['target']='missing'
    if fault=='mindmap-cycle':c['graphs'][0]['kind']='MINDMAP'
    r=client.put(f'/api/projects/{pid}/discovery/sessions/{value["id"]}',json={'operation_id':'bad',
        'expected_revision':1,'candidate':c,'change_reason':'反例'})
    assert r.status_code==422
    assert client.get(f'/api/projects/{pid}/discovery/sessions/{value["id"]}').json()['revision']==1


def test_byte_anchor_forgery_rejected_and_source_drift_preserves_snapshot(client):
    pid,doc,_,value=create(client,files=True);c=candidate();source=value['payload']['source_receipt']['documents'][1];chunk=source['chunks'][0]
    anchor={'document_id':source['document_id'],'revision':source['revision'],'raw_sha256':source['raw_sha256'],
        'chunk_index':chunk['index'],'chunk_sha256':chunk['sha256'],'char_start':chunk['char_start'],
        'char_end':chunk['char_start']+1,'quote_sha256':'0'*64}
    c['claims'][0].update(origin='SOURCE_FACT',source_anchors=[anchor]);base=f'/api/projects/{pid}/discovery/sessions/{value["id"]}'
    r=client.put(base,json={'operation_id':'forge','expected_revision':1,'candidate':c,'change_reason':'伪造锚点'})
    assert r.status_code==422 and r.json()['error']['code']=='INVALID_INPUT'
    assert 'quote hash' in r.json()['error']['message']
    anchor['quote_sha256']=hashlib.sha256(chunk['content'][:1].encode()).hexdigest()
    good=client.put(base,json={'operation_id':'actual-anchor','expected_revision':1,'candidate':c,'change_reason':'核对真实引用'})
    assert good.status_code==200,good.text
    value=good.json()
    changed=client.put(f'/api/projects/{pid}/documents/{doc["id"]}',files={'file':('变化.md',b'changed')},data={'expected_revision':'1'})
    assert changed.status_code==200
    latest=client.get(base).json();assert not latest['source_current'] and latest['payload']==value['payload']
    assert client.get(base+'/pending/markdown').status_code==200


def test_cas_and_project_isolation(client):
    pid,_,_,value=create(client);service=client.app.state.discovery
    def write(index):
        try:return service.edit(pid,value['id'],DiscoveryEdit(operation_id='parallel-'+str(index),expected_revision=1,
                candidate=DiscoveryCandidate.model_validate(candidate()),change_reason='原创并发修订'))
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(write,range(4)))
    assert sum(r is not None for r in results)==1
    other=project(client)
    assert client.get(f'/api/projects/{other}/discovery/sessions/{value["id"]}').status_code==404
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM discovery_versions').fetchone()[0]==2
        with pytest.raises(Exception):c.execute("UPDATE discovery_versions SET payload='{}'")


def test_restart_unknown_operation_never_replays(client):
    pid,_,_,value=create(client)
    with client.app.state.store.connect() as c:
        c.execute('INSERT INTO discovery_operations VALUES (?,?,?,?,?,?,?,?)',
                  (pid,'lost',value['id'],'ADVANCE',digest({'original':'input'}),'PROCESSING',None,None))
    service=DiscoveryWorkbench(client.app.state.store,client.app.state.planning_proposals)
    op=service.operation(pid,'lost');assert op['status']=='UNKNOWN_OUTCOME'
    with client.app.state.store.connect() as c:assert c.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==0


class ProtocolOnlyPlanner:
    def __init__(self,store):self.store=store;self.calls=[]
    async def propose(self,request):
        self.calls.append(copy.deepcopy(request))
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');assert c.execute('SELECT count(*) FROM discovery_sessions').fetchone()[0]==1
        if 'claims' in request['output_schema']['properties']:result=candidate()
        else:result={'title':'原协议夹具任务','summary':'仅协议验证，不是真实模型能力。',
            'nodes':[{'id':'prepare','title':'核对清单','description':'按明确要求核对。','outputs':['清单'],'acceptance':['逐项来源核对']}],
            'questions':[]}
        return {'status':'RETURNED','candidate_json':result,'source_receipt_sha256':request['source_receipt']['sha256'],
                'model_calls':1,'qualification':'PROTOCOL_MOCK_ONLY'}


def test_dynamic_native_schema_then_requirements_gate_plan_and_idempotency(client,tmp_path):
    pid,_,_,value=create(client);service=client.app.state.discovery;fake=ProtocolOnlyPlanner(client.app.state.store)
    service.transport=fake;client.app.state.planning_proposals.transport=fake
    actual=tmp_path.parent/(tmp_path.name+' 工作资料');actual.mkdir()
    bound=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'folder','root':str(actual)})
    assert bound.status_code==201,bound.text
    workspace={k:bound.json()[k] for k in ['id','revision','binding_sha256']}
    base=f'/api/projects/{pid}/discovery/sessions/{value["id"]}'
    body={'operation_id':'advance','expected_revision':1,'message':'补充逐项来源核对要求，待确认材料范围。','workspace_ref':workspace}
    r=client.post(base+'/advance',json=body);assert r.status_code==200,r.text
    v=r.json();assert v['revision']==2 and not v['readiness']['can_plan'] and len(fake.calls)==1
    assert client.post(base+'/advance',json=body).json()==v and len(fake.calls)==1
    answer=client.post(base+'/answers',json={'operation_id':'scope-answer','expected_revision':2,'question_id':'scope','text':'只核对当前材料。'}).json()
    v=confirm(client,pid,v,answer['id']).json();assert v['readiness']['can_plan']
    planbody={'operation_id':'plan','expected_revision':v['revision']}
    r=client.post(base+'/plan',json=planbody);assert r.status_code==200,r.text
    result=r.json();assert len(fake.calls)==2
    assert result['proposal']['payload']['discovery_ref']=={'id':v['id'],'revision':v['revision'],'sha256':v['sha256']}
    assert not result['proposal']['payload']['implementation_started']
    assert client.post(base+'/plan',json=planbody).json()==result and len(fake.calls)==2
    with client.app.state.store.connect() as c:assert c.execute('SELECT count(*) FROM task_runs').fetchone()[0]==0
