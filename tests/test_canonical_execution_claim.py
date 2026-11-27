"""AUTHORED_NOT_RUN: actual ASGI/Store, journals and original-source binding.

Native ownership/closure observations are synthesized UNIT facts. No SDK,
model, OS sandbox, Windows or usable permission qualification is claimed.
"""
from copy import deepcopy

import pytest

from apps.api.conversation_configuration import confirmation_value
from packages.agent_sessions.models import OpenConversation
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_conversation_directory import api, project, unit_session
from test_conversation_creation import organize


def claimed(api, *, closed=True):
    owner,pid,sid,state=unit_session(api,guided=False)
    row=next(row for row in api.get('/api/conversations?kind=original').json()['items']
             if row['source']['session_ref']['id']==sid)
    ident=row['id']
    selected=api.post('/api/conversations/'+row['id']+'/selection',json={'source':row['source']})
    assert selected.status_code==200
    row=next(item for item in api.get('/api/conversations?kind=original').json()['items'] if item['id']==ident)
    target=project(api,'Moved logical project')
    moved=organize(api,row,'move','move_for_explicit_claim',target_project_id=target)
    assert moved.status_code==200
    scope=api.get('/api/conversations/'+row['id']+'/scope').json()
    manifest,reference=owner._manifest(pid,sid)
    # Fresh synthetic native context, with no selected external sources. The
    # registry and subsequent proof are real metadata, not an SDK observation.
    owner.context_authority.begin(manifest,{'session_id':state['original_session_id']},loaded_existing=False)
    source={'project_id':pid,'session_ref':reference,'generation':state['generation']}
    prepared=api.app.state.conversation_configuration.prepare({'conversation_ref':scope['conversation_ref'],'original_source':source})
    body=OpenConversation(operation_id='explicit_claim',project_id=target,
        conversation_ref=scope['conversation_ref'],provider_profile_ref=manifest['request']['provider_profile_ref'],
        capability_binding_sha256=prepared['authority']['capability_binding_sha256'],mcp_tool_selection=[]).model_dump()
    proof={'authority':prepared['authority'],'preparation_sha256':prepared['preparation_sha256'],
        'execution_project_id':pid,'request_sha256':digest(body),
        'confirmation_sha256':digest(confirmation_value(body,prepared['preparation_sha256'],pid))}
    record={'kind':'CONNECTION','request':body,'body_sha256':digest(body),'request_sha256':digest({k:v for k,v in body.items() if k not in {'project_id','operation_id'}}),
        'phase':'PREPARING','source':None,'conversation_ref':scope['conversation_ref'],
        'configuration_confirmation':proof,'configuration_confirmation_sha256':digest(proof)}
    if closed:record['closed_previous_owner']={'original_source':source,'owner_closed':True,'process_identity':None,'controller_identity':None}
    service=api.app.state.canonical_conversations
    service._save(service._path('connections',[target,body['operation_id']]),record)
    claim={'operation_id':body['operation_id'],'project_id':target,
        'confirmation_sha256':proof['confirmation_sha256'],'proof_sha256':digest(proof)}
    native={k:v for k,v in body.items() if k!='project_id'}
    replacement={'id':'b'*32,'project_id':pid,'workspace_kind':'APP_INTERNAL_READONLY',
        'request':{k:v for k,v in body.items() if k not in {'project_id','operation_id'}},
        'canonical_execution_claim':claim}
    owner._write(owner._path(pid,replacement['id'],'manifest.json'),replacement)
    return owner,pid,scope,body,native,claim,replacement


def test_moved_conversation_needs_the_exact_internal_claim_to_retain_original_execution_project(api):
    owner,pid,scope,_,native,claim,manifest=claimed(api)
    runtime=api.app.state.conversation_runtime
    with pytest.raises(Conflict):runtime.validate_open(pid,scope['conversation_ref'])
    runtime.validate_open(pid,scope['conversation_ref'],canonical_execution_claim=claim,request=native)
    runtime.bind_open(manifest);runtime.assert_bound(manifest)
    sources=api.app.state.conversation_threads.view(scope['conversation_ref']['id'])['sources']
    assert any(row['original_source'] and row['original_source'].get('session_ref',{}).get('id')==manifest['id'] for row in sources)
    assert owner.active=={}


def test_configuration_source_cannot_be_registered_before_original_owner_closure(api):
    _,pid,scope,_,native,claim,manifest=claimed(api,closed=False)
    runtime=api.app.state.conversation_runtime
    runtime.validate_open(pid,scope['conversation_ref'],canonical_execution_claim=claim,request=native)
    with pytest.raises(Conflict,match='closure'):runtime.bind_open(manifest)
    assert not any(row['original_source'] and row['original_source'].get('session_ref',{}).get('id')==manifest['id']
        for row in api.app.state.conversation_threads.view(scope['conversation_ref']['id'])['sources'])


def test_public_open_body_cannot_serialize_the_internal_execution_claim(api):
    _,_,_,body,_,claim,_=claimed(api)
    result=api.post('/api/agent-sessions/open',json={**deepcopy(body),'canonical_execution_claim':claim})
    assert result.status_code==422 and api.app.state.agent_sessions.active=={}
