"""Actual projection, scoped durable service GET/API, and original CLI child evidence."""
import asyncio
from copy import deepcopy
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from apps.api.agent_collaboration import register_agent_collaboration_routes
from packages.agent_sessions.collaboration import project_collaboration
from packages.domain.store import Conflict, NotFound
from test_agent_sessions import scene, owner, ModelWire, tool, control, until


def check(value, message='collaboration acceptance failed'):
    if not value: raise AssertionError(message)


def basis():
    return {'project_id':'project', 'session_ref':{'id':'a'*32,'revision':1,'sha256':'a'*64}, 'generation':3,
            'original_session_id':'ses_root','original_session':{'id':'ses_root','directory':'/selected'},
            'native_status':'idle','observation_at':'2026-10-15T08:00:00+00:00','observation_is_current':False,
            'messages':[],'children':[{'id':'ses_child','parentID':'ses_root','directory':'/selected','title':'代码审阅','agent':'explore',
                                     'model':{'providerID':'provider','modelID':'child-model'}}],
            'pending_questions':[],'pending_permissions':[]}


def message(call='call_one', target='ses_child', ident='msg_one'):
    return {'info':{'id':ident,'sessionID':'ses_root','role':'assistant','providerID':'provider','modelID':'root-model','variant':'high'},
            'parts':[{'id':'part_'+call,'sessionID':'ses_root','type':'tool','tool':'task','callID':call,
                      'state':{'status':'completed','input':{'description':'只读检查','subagent_type':'explore','prompt':'DO_NOT_PROJECT_PROMPT'},
                               'metadata':{'parentSessionId':'ses_root','sessionId':target,'model':{'providerID':'provider','modelID':'selected-model'}},
                               'output':'DO_NOT_PROJECT_OUTPUT'}}]}


def test_lineage_is_not_delegation_idle_is_not_done_and_metadata_is_not_child_execution():
    original=basis();before=deepcopy(original);value=project_collaboration(original)
    check(original==before and [edge['kind'] for edge in value['edges']]==['LINEAGE'])
    check(value['nodes'][0]['observed_status']=='idle' and value['nodes'][1]['observed_status']=='UNKNOWN')
    check(value['nodes'][1]['models'][0]['evidence_level']=='SESSION_METADATA')
    check(value['coverage']['child_messages_observed'] is False and value['coverage']['subtree_complete'] is False)
    check(value['model_calls']==0 and value['read_only'] and 'IDLE_IS_NOT_COMPLETION' in value['limitations'])


def test_actual_task_metadata_matching_preserves_two_attempts_to_same_child_and_deduplicates_exact_repeat():
    source=basis();one=message();source['messages']=[one,deepcopy(one),message('call_two',ident='msg_two')]
    value=project_collaboration(source);edges=[edge for edge in value['edges'] if edge['kind']=='DELEGATION']
    check(len(edges)==2 and {edge['call_id'] for edge in edges}=={'call_one','call_two'})
    check(all(edge['target']=='ses_child' and edge['evidence_ref']['part_id'] for edge in edges))
    check(any(model['evidence_level']=='ASSISTANT_MESSAGE' and model['variant']=='high' for model in value['nodes'][0]['models']))
    check(any(model['evidence_level']=='TASK_METADATA_SELECTION' for model in value['nodes'][1]['models']))
    check('DO_NOT_PROJECT' not in str(value))
    check(value['source_ref']==project_collaboration(source)['source_ref'])


def test_unmatched_task_is_gap_not_fake_child_or_edge_and_pending_is_exact_session():
    source=basis();source['messages']=[message(target='ses_unobserved')]
    source['pending_questions']=[{'request_ref':{'id':'question','generation':3,'sha256':'b'*64},'native_request':{'sessionID':'ses_child'}}]
    value=project_collaboration(source)
    check(len(value['nodes'])==2 and len(value['edges'])==1 and value['coverage']['gaps']==['UNMATCHED_TASK_METADATA'])
    check(value['nodes'][1]['observed_status']=='WAITING_USER' and value['nodes'][1]['pending_request_refs'][0]['id']=='question')


@pytest.mark.parametrize('change',[lambda x:x['children'][0].update(parentID='foreign'),lambda x:x['children'][0].update(directory='/foreign'),
    lambda x:x.update(messages=[{**message(),'info':{**message()['info'],'sessionID':'foreign'}}]),
    lambda x:x.update(children=x['children']+[{**x['children'][0],'title':'conflict'}]),
    lambda x:x.update(messages=[message(),{**message(),'parts':[{**message()['parts'][0],'state':{'status':'running'}}]}])])
def test_foreign_scope_and_conflicting_duplicate_evidence_fail_closed(change):
    value=basis();change(value)
    with pytest.raises(Conflict):project_collaboration(value)


def test_exact_scope_generation_and_no_native_root_empty():
    value=basis()
    for expected in [{'project_id':'another'},{'session_ref':{**value['session_ref'],'sha256':'b'*64}},{'generation':4}]:
        with pytest.raises(Conflict):project_collaboration(value,**expected)
    value.update(original_session_id=None,children=[])
    check(project_collaboration(value)['nodes']==[])


def application(service):
    app=FastAPI()
    @app.exception_handler(Conflict)
    async def conflict(_request,error):return JSONResponse({'error':str(error)},status_code=409)
    @app.exception_handler(NotFound)
    async def missing(_request,error):return JSONResponse({'error':str(error)},status_code=404)
    register_agent_collaboration_routes(app,service=service)
    return app


def test_real_original_cli_child_durable_api_projection_and_no_get_model_dispatch(scene):
    wire=ModelWire([tool('task',{'description':'Actual child review','prompt':'Read only review','subagent_type':'explore'}),
                    'ACTUAL_COLLABORATION_CHILD','ACTUAL_COLLABORATION_PARENT']).start()
    async def run():
        service,request=owner(scene,wire)
        try:
            opened=await service.open(scene['pid'],request);ident=opened['session_ref']['id']
            await service.send(scene['pid'],ident,{**control(opened,'collaboration_message'),'raw_text':'Use the actual explore child'})
            pending=await until(service,scene,ident,lambda item:bool(item['pending_permissions']))
            permission=pending['pending_permissions'][0]
            await service.reply(scene['pid'],ident,permission['request_ref']['id'],{**control(opened,'collaboration_allow'),
                'request_ref':permission['request_ref'],'reply':'once'},kind='PERMISSION')
            final=await until(service,scene,ident,lambda item:item['active_operation_id'] is None)
            check(len(wire.requests)==3 and len(final['children'])==1)
            path=f"/api/projects/{scene['pid']}/agent-sessions/{ident}/collaboration"
            with TestClient(application(service)) as client:
                response=client.get(path,params={'generation':final['generation'],'session_sha256':final['session_ref']['sha256']})
                check(response.status_code==200,response.text);snapshot=response.json()
                check(len([edge for edge in snapshot['edges'] if edge['kind']=='DELEGATION'])==1)
                check(snapshot['coverage']['child_messages_observed'] is False)
                check(client.get(path,params={'generation':final['generation']+1}).status_code==409)
                check(client.get(path,params={'session_sha256':'b'*64}).status_code==409)
                foreign=scene['store'].create_project('Foreign scope','original fixture')['id']
                check(client.get(f'/api/projects/{foreign}/agent-sessions/{ident}/collaboration').status_code==404)
                check(len(wire.requests)==3,'read-only graph must not call the model')
            check(scene['actual'].read_bytes()==scene['original'])
        finally:await service.close()
    try:asyncio.run(run())
    finally:wire.close()
