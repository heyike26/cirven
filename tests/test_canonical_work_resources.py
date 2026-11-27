"""AUTHORED_NOT_RUN: real journals/ledger, explicit UNIT owner metadata only.

No SDK/model/native process is started. These cases cannot qualify Windows,
actual provider switching, or the complete desktop continuation workflow.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from apps.api.canonical_conversations import CanonicalConversations
from apps.api.conversation_configuration import confirmation_value
from apps.api.conversation_runtime import ConversationRuntime
from packages.agent_sessions.service import AgentSessions
from packages.agent_sessions.task_resources import for_operation
from packages.agent_sessions.canonical_resources import origin, replacement_path, resource_value, saved_status
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_task_resource_budget import resource


@pytest.fixture
def linked(resource):
    pid=resource['authority']['project_id'];cid='native:'+'c'*64
    reference={'id':cid,'revision':1};manifests={};operations={}
    owner=SimpleNamespace(workspaces=resource['ws'],data_dir=resource['ws'].data_dir,
        _write=AgentSessions._write,_read=AgentSessions._read)
    owner._manifest=lambda project,sid:(deepcopy(manifests[sid]),
        {'id':sid,'revision':1,'sha256':digest(manifests[sid])})
    owner.operation=lambda project,sid,key:deepcopy(operations[(sid,key)])
    service=CanonicalConversations.__new__(CanonicalConversations);service.sessions=owner
    runtime=ConversationRuntime.__new__(ConversationRuntime);runtime.sessions=owner
    runtime.app=SimpleNamespace(state=SimpleNamespace(canonical_conversations=service))
    service.runtime=runtime;owner.conversations=runtime
    service._bound=lambda source,conversation:conversation==cid and source['session_ref']['id'] in manifests

    def add_owner(sid,previous=None):
        request={'operation_id':'connect_'+sid,'conversation_ref':reference,
            'capability_binding_sha256':'b'*64,'provider_profile_ref':{'id':sid,'revision':1,'sha256':'a'*64}}
        manifest={'id':sid,'project_id':pid,'request':request}
        row=None
        if previous is not None:
            full={**request,'project_id':pid}
            authority={'conversation_ref':reference,'project_id':pid,
                'capability_binding_sha256':'b'*64,'original_source':previous}
            preparation=digest(authority)
            proof={'authority':authority,'preparation_sha256':preparation,
                'execution_project_id':pid,'request_sha256':digest(full),
                'confirmation_sha256':digest(confirmation_value(full,preparation,pid))}
            manifest['canonical_execution_claim']={'operation_id':request['operation_id'],
                'project_id':pid,'confirmation_sha256':proof['confirmation_sha256'],'proof_sha256':digest(proof)}
            row={'request':full,'body_sha256':digest(full),'phase':'BOUND',
                'configuration_confirmation':proof,'configuration_confirmation_sha256':digest(proof),
                'closed_previous_owner':{'original_source':previous,'owner_closed':True,
                    'process_identity':None,'controller_identity':None}}
        manifests[sid]=manifest;ref=owner._manifest(pid,sid)[1]
        source={'project_id':pid,'session_ref':ref,'generation':1}
        if row is not None:
            row['source']=source
            service._save(service._path('connections',[pid,request['operation_id']]),row)
        return source

    def message(source,key,parent=None,legacy=False):
        sid=source['session_ref']['id']
        public={'operation_id':key,'conversation_ref':reference,'raw_text':'UNIT user request '+key}
        request={**public,'session_ref':source['session_ref'],'generation':source['generation']}
        op={'id':key,'kind':'MESSAGE','request':request,'request_sha256':digest(request),
            'session_ref':source['session_ref'],'generation':source['generation'],
            'status':'RETURNED','native_dispatch_acknowledged':True}
        row={'request':deepcopy(public),'source':source,'phase':'OBSERVED',
            'dispatch_request_sha256':op['request_sha256']}
        if parent is not None:
            prior_row=service._read(service._path('messages',[cid,parent]))
            parent_ref={'id':parent,'revision':1,'sha256':prior_row['body_sha256']}
            row['request']['parent_message_ref']=parent_ref
            if not legacy:
                parent_source=prior_row['source'];parent_sid=parent_source['session_ref']['id']
                b=for_operation(owner,manifests[parent_sid],operations[(parent_sid,parent)])
                row['work_resource_lineage']={'version':1,'parent_message_ref':parent_ref,'resource':resource_value(b)}
        row['body_sha256']=digest(row['request'])
        operations[(sid,key)]=op
        service._save(service._path('messages',[cid,key]),row)
        return op

    original=add_owner('original');root=message(original,'root')
    budget=for_operation(owner,manifests['original'],root);budget.allocate()
    return SimpleNamespace(pid=pid,cid=cid,owner=owner,service=service,runtime=runtime,
        manifests=manifests,operations=operations,original=original,root=root,budget=budget,
        add_owner=add_owner,message=message,reference=reference)


def test_replacement_and_two_replies_share_consumed_pending_and_original_ledger(linked):
    h=linked;h.budget.consume(model_attempts=1,source_bytes=7);ticket=h.budget.reserve_read(11)
    target=h.add_owner('replacement',h.original)
    op=h.message(target,'new_input','root');b=for_operation(h.owner,h.manifests['replacement'],op)
    assert b.id==h.budget.id and b.path==h.budget.path
    assert b.inspect()['pending_read_bytes']==11 and b.inspect()['used']['model_attempts']==1
    b.consume(model_attempts=1);b.finish_read(ticket,3)
    assert h.budget.inspect()['used']['model_attempts']==2
    assert h.budget.inspect()['used']['source_bytes']==10
    assert h.operations[('original','root')]==h.root


def test_more_than_a_hundred_same_owner_replies_use_constant_root_without_allocating_new_ledgers(linked):
    h=linked;parent='root';h.budget.consume(model_attempts=1);before=h.budget.path.read_bytes()
    for index in range(120):
        key='reply_'+str(index);op=h.message(h.original,key,parent)
        assert for_operation(h.owner,h.manifests['original'],op).id==h.budget.id
        parent=key
    assert h.budget.path.read_bytes()==before
    assert len(list(h.budget.path.parent.parent.glob('*/allocation.json')))==2  # fixture ledger plus original work


def test_legacy_accepted_reply_is_not_reinterpreted_or_backfilled(linked):
    h=linked;legacy=h.message(h.original,'legacy','root',legacy=True)
    b=for_operation(h.owner,h.manifests['original'],legacy);b.allocate();b.consume(model_attempts=2)
    original_before=h.budget.path.read_bytes();legacy_before=b.path.read_bytes()
    op=h.message(h.original,'new_after_legacy','legacy')
    assert for_operation(h.owner,h.manifests['original'],op).id==b.id!=h.budget.id
    assert h.budget.path.read_bytes()==original_before and b.path.read_bytes()==legacy_before


@pytest.mark.parametrize('damage',['parent_hash','resource_id','resource_authority','native_ack','lineage','closed_owner','published_owner'])
def test_wrong_original_proofs_never_allocate_or_reset_a_replacement_ledger(linked,damage):
    h=linked;target=h.add_owner('replacement',h.original);op=h.message(target,'reply','root')
    path=h.service._path('messages',[h.cid,'reply']);row=h.service._read(path)
    if damage=='parent_hash':row['request']['parent_message_ref']['sha256']='0'*64;row['body_sha256']=digest(row['request'])
    elif damage=='resource_id':row['work_resource_lineage']['resource']['resource_id']='0'*64
    elif damage=='resource_authority':row['work_resource_lineage']['resource']['authority']['request_sha256']='0'*64
    elif damage=='native_ack':h.operations[('original','root')]['native_dispatch_acknowledged']=False
    elif damage=='lineage':row['work_resource_lineage']=[]
    else:
        config=h.service._path('connections',[h.pid,'connect_replacement']);saved=h.service._read(config)
        if damage=='closed_owner':saved['closed_previous_owner']['owner_closed']=False
        else:saved['source']['session_ref']=h.original['session_ref']
        h.service._save(config,saved,replace=True)
    h.service._save(path,row,replace=True);before=h.budget.path.read_bytes()
    with pytest.raises(Conflict):for_operation(h.owner,h.manifests['replacement'],op)
    assert h.budget.path.read_bytes()==before


def test_owner_selection_without_configuration_and_different_execution_project_are_not_work_proof(linked):
    h=linked;unrelated=h.add_owner('unrelated')
    with pytest.raises(Conflict):replacement_path(h.service,unrelated,h.original,h.cid)
    with pytest.raises(Conflict):replacement_path(h.service,{**unrelated,'project_id':'another'},h.original,h.cid)


def test_sticky_unknown_accounting_survives_replacement_and_blocks_generation(linked):
    h=linked;h.budget.mark_unconfirmed('UNIT_UNKNOWN_ACK')
    target=h.add_owner('replacement',h.original);op=h.message(target,'reply','root')
    b=for_operation(h.owner,h.manifests['replacement'],op);before=b.gap.read_bytes()
    with pytest.raises(Conflict):b.consume(model_attempts=1)
    assert h.budget.gap.read_bytes()==before


def test_saved_progress_names_original_operation_without_model_or_new_ledger(linked):
    h=linked;target=h.add_owner('replacement',h.original);op=h.message(target,'progress','root')
    before=h.budget.path.read_bytes();v=saved_status(h.runtime,h.manifests['replacement'],op)
    assert v['operation_id']=='root' and v['session_ref']==h.original['session_ref']
    assert v['model_calls']==v['native_posts']==0 and not v['permission_granted']
    assert h.budget.path.read_bytes()==before


def test_explicit_continuation_uses_current_model_without_replaying_old_attachments(linked):
    h=linked;target=h.add_owner('replacement',h.original)
    path=h.service._path('messages',[h.cid,'root']);row=h.service._read(path)
    row['request'].update(agent='build',input_attachments=[{'UNIT':'old attachment'}],file_input={'UNIT':'old file'},
        provider_profile_ref={'id':'old','revision':1,'sha256':'a'*64})
    row['body_sha256']=digest(row['request']);h.service._save(path,row,replace=True)
    profile={'id':'current','revision':1,'sha256':'b'*64};captured=[]
    h.service.threads=SimpleNamespace(view=lambda cid:{'conversation_ref':h.reference})
    h.service._member=lambda scope:target
    h.service._view=lambda source:{**source,'provider_profile_ref':profile,'active_operation_id':None}
    async def capture(cid,body,*,parent):captured.append((body,parent));return {'UNIT':'captured only'}
    h.service.send=capture
    asyncio.run(h.service.continuation(h.cid,'root',{'operation_id':'continue_now','raw_text':'Explicit new input',
        'expected_message_ref':{'id':'root','revision':1,'sha256':row['body_sha256']}}))
    body,parent=captured[0]
    assert body.provider_profile_ref.model_dump()==profile and body.input_attachments==[] and body.file_input is None
    assert body.raw_text=='Explicit new input' and parent['resource']['resource_id']==h.budget.id
    assert parent['replacement'] is True
