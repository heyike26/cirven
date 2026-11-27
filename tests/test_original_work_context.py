"""AUTHORED_NOT_RUN: original work in existing public host input context.

Test-owned Store/files/receipts and real ConversationRuntime when eventually
run; owner/SDK/ACK/model/automatic-context/thread lookup/input-source adapters
below are explicit UNIT boundaries. No App/Windows/IDE/target qualification.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from apps.api.conversation_runtime import ConversationRuntime
from packages.agent_sessions import work_context as mod
from packages.agent_sessions.models import SendMessage,OriginalWorkRef,operation_request
from packages.agent_sessions.guidance import GuidanceInput
from packages.agent_sessions.work_retests import resolved_command_roots
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import digest
from test_original_work_maintenance_retests import maintained,change
from test_original_work_retests import retest,routed
from test_original_work_test_cases import mapped
from test_opencode_command_observation import command_scene


@pytest.fixture
def work(maintained):
    s=maintained;change(s)
    s['manifest']=s['service']._manifest(s['pid'],s['ident'])[0]
    s['work_ref']={'root_operation_id':s['input']['root_operation_id'],'root_sha256':s['root_sha256']}
    return s


def message(s,key='followup',*,select=True,text='只修复此原工作，保留人工修改。',generation=None):
    body=SendMessage(session_ref=s['ref'],generation=generation or s['input']['generation'],operation_id=key,
        raw_text=text,original_work_ref=s['work_ref'] if select else None)
    op,_=s['service']._claim(s['pid'],s['ident'],key,'MESSAGE',operation_request(body))
    return s['service']._finish(s['pid'],s['ident'],key,'DISPATCHING',generation=body.generation,
        original_session_id='ses_command',native_message_id='msg_'+key,native_dispatch_acknowledged=False)


def saved(s,op,*,guidance=False):
    value=mod.freeze(s['service'],s['manifest'],op,guidance=guidance)
    data={'original_request_sha256':op['request_sha256'],'guidance':guidance,'original_work_context':value}
    from packages.agent_sessions.capability_task import prompt_evidence
    parts=[{'type':'text','text':mod.prompt(value)},{'type':'text','text':op['request']['raw_text']}]
    proof={**prompt_evidence(parts),'conversation_context_sha256':digest(data),
        'payload_sha256':digest({'messageID':op['native_message_id'],'parts':parts})}
    return s['service']._finish(s['pid'],s['ident'],op['id'],op['status'],
        conversation_context=data,conversation_context_sha256=digest(data),host_payload=proof)


def guide(s,parent=None,*,key='guide',selected=None):
    parent=parent or s['input']['root_operation_id']
    body=GuidanceInput(session_ref=s['ref'],generation=1,operation_id=key,raw_text='继续保留我的修改',
        intent='STEER',expected_active_operation_id=parent,original_work_ref=selected)
    op,_=s['service']._claim(s['pid'],s['ident'],key,'GUIDANCE',operation_request(body))
    s['service']._update(s['pid'],s['ident'],active_operation_id=parent)
    return s['service']._finish(s['pid'],s['ident'],key,'DISPATCHING',generation=1,
        original_session_id='ses_command',native_message_id='msg_'+key,original_active_operation_id=parent,
        native_dispatch_acknowledged=False)


def runtime(s,monkeypatch):
    import apps.api.conversation_runtime as runtime_module
    monkeypatch.setattr(runtime_module,'automatic_input_context',lambda *a,**k:None)
    sources=SimpleNamespace(validate=lambda v:None,prompt_parts=lambda v:[])
    app=SimpleNamespace(state=SimpleNamespace(agent_sessions=s['service'],native_messages=SimpleNamespace(input_sources=sources)))
    threads=SimpleNamespace(store=s['service'].store,original_scope=lambda *a,**k:None,_alias=lambda *a,**k:None)
    value=ConversationRuntime(app,threads,SimpleNamespace())
    context={'profile':SimpleNamespace(supports_images=True),'generation':1,'session_id':'ses_command'}
    return value,context


def test_optional_work_reference_preserves_legacy_message_and_guidance_request_hashes():
    base={'session_ref':{'id':'session','revision':1,'sha256':'a'*64},'generation':1,'operation_id':'legacy','raw_text':'保留原文'}
    for model in [SendMessage,GuidanceInput]:
        value=operation_request(model.model_validate(base));assert 'original_work_ref' not in value
        assert operation_request(model.model_validate({**base,'original_work_ref':None}))==value
    assert operation_request(SendMessage.model_validate(base))=={**base,'agent':'build','timeout_seconds':300}
    assert operation_request(GuidanceInput.model_validate(base))=={**base,'intent':'AUTO','expected_active_operation_id':None}
    with pytest.raises(ValidationError):OriginalWorkRef.model_validate({'root_operation_id':'root','root_sha256':'b'*64,'path':'arbitrary'})


def test_explicit_selection_freezes_exact_root_and_confirmed_maintenance_without_editing_user_text(work):
    s=work;op=message(s);value=mod.freeze(s['service'],s['manifest'],op)
    assert value['original_work_ref']==s['work_ref'] and value['scope']['root_operation_id']==s['input']['root_operation_id']
    assert value['maintenance_context']['associations'][0]['delivery_ref']==s['baseline_ref']
    assert value['original_request_sha256']==op['request_sha256']
    assert s['service'].operation(s['pid'],s['ident'],op['id'])['request']==op['request']
    assert value['permission_granted'] is False and value['execution_scope_changed'] is False


def test_unrelated_new_message_never_inherits_work_from_title_or_recent_maintenance(work):
    s=work;op=message(s,select=False,text=s['service'].operation(s['pid'],s['ident'],s['input']['root_operation_id'])['request']['raw_text'])
    assert mod.freeze(s['service'],s['manifest'],op) is None
    assert not mod.resolved_roots(s['service'],s['pid'],s['ident'],[op])


def test_running_guidance_uses_exact_parent_without_needing_a_second_issue_or_user_selector(work):
    s=work;op=guide(s);value=mod.freeze(s['service'],s['manifest'],op,guidance=True)
    assert value['original_work_ref']==s['work_ref'] and value['maintenance_context']['revision']==1
    assert 'original_work_ref' not in op['request'] and op['original_active_operation_id']==s['input']['root_operation_id']


def test_legacy_guidance_with_no_maintenance_keeps_original_context_behavior(maintained):
    s=maintained;s['manifest']=s['service']._manifest(s['pid'],s['ident'])[0]
    assert mod.freeze(s['service'],s['manifest'],guide(s),guidance=True) is None


def test_guidance_explicit_selection_cannot_retarget_a_different_work(work):
    s=work;wrong={'root_operation_id':'other_work','root_sha256':'f'*64}
    with pytest.raises(Conflict,match='retarget another'):mod.freeze(s['service'],s['manifest'],guide(s,selected=wrong),guidance=True)


def test_changed_root_hash_is_rejected_without_reading_a_different_workspace(work):
    s=work;s['work_ref']['root_sha256']='f'*64
    with pytest.raises(Conflict,match='identity changed'):mod.freeze(s['service'],s['manifest'],message(s))


def test_new_work_cannot_reference_itself_or_another_derived_action(work):
    s=work;first=saved(s,message(s));ref=mod._saved(s['service'],s['pid'],s['ident'],first)['original_work_ref']
    assert ref==s['work_ref']
    scope=mod.requirements.WorkRequirementScope.model_validate({**s['input'],'root_operation_id':first['id']})
    derived=mod.requirements._root(s['service'],s['pid'],s['ident'],scope)
    s['work_ref']={'root_operation_id':first['id'],'root_sha256':derived['sha256']}
    with pytest.raises(Conflict,match='derived action'):mod.freeze(s['service'],s['manifest'],message(s,'next'))
    s['work_ref']={'root_operation_id':'self','root_sha256':'f'*64}
    with pytest.raises(Conflict,match='own original work'):mod.freeze(s['service'],s['manifest'],message(s,'self'))


def test_maintenance_withdrawal_before_dispatch_preserves_frozen_record_but_blocks_new_payload(work):
    s=work;op=saved(s,message(s));before=deepcopy(op)
    change(s,action='REMOVE',op='withdraw')
    with pytest.raises(Conflict,match='maintenance associations changed'):mod.before_dispatch(s['service'],s['manifest'],op)
    assert s['service'].operation(s['pid'],s['ident'],op['id'])==before


def test_guidance_parent_change_before_dispatch_rejects_without_new_post(work):
    s=work;op=saved(s,guide(s),guidance=True)
    s['service']._update(s['pid'],s['ident'],active_operation_id='another_work')
    with pytest.raises(Conflict,match='guidance parent changed'):mod.before_dispatch(s['service'],s['manifest'],op)


def test_explicit_displayed_message_continuation_recovers_same_logical_work_after_action(work):
    s=work;op=saved(s,message(s))
    assert mod.continuation_ref(s['service'],s['manifest'],op,1)==s['work_ref']
    original=s['service'].operation(s['pid'],s['ident'],s['input']['root_operation_id'])
    assert mod.continuation_ref(s['service'],s['manifest'],original,1)==s['work_ref']


def test_running_guidance_inherits_derived_parent_root_instead_of_creating_another_work(work):
    s=work;parent=saved(s,message(s));op=guide(s,parent['id'])
    value=mod.freeze(s['service'],s['manifest'],op,guidance=True)
    assert value['original_work_ref']==s['work_ref'] and value['scope']['root_operation_id']!=parent['id']


def test_historical_grouping_uses_frozen_proof_even_after_later_metadata_changes(work):
    s=work;op=saved(s,message(s));op=s['service']._finish(s['pid'],s['ident'],op['id'],'UNKNOWN_OUTCOME',native_dispatch_acknowledged=True)
    change(s,action='REMOVE',op='withdraw')
    assert resolved_command_roots(s['service'],s['pid'],s['ident'],[op])=={op['id']:s['input']['root_operation_id']}
    assert mod._saved(s['service'],s['pid'],s['ident'],op)['maintenance_context']['revision']==1


def test_unknown_or_tampered_action_cannot_be_hidden_under_another_work(work):
    s=work;op=saved(s,message(s));assert not mod.resolved_roots(s['service'],s['pid'],s['ident'],[op])
    op['native_dispatch_acknowledged']=True;op['conversation_context']['original_work_context']['root_input']['excerpt']='forged'
    assert not mod.resolved_roots(s['service'],s['pid'],s['ident'],[op])
    op['conversation_context_sha256']=digest(op['conversation_context'])
    assert not mod.resolved_roots(s['service'],s['pid'],s['ident'],[op])


def test_context_cannot_be_copied_to_same_title_different_original_request(work):
    s=work;op=saved(s,message(s));other=message(s,'other',select=False,text=op['request']['raw_text'])
    other.update(conversation_context=op['conversation_context'],conversation_context_sha256=op['conversation_context_sha256'],native_dispatch_acknowledged=True)
    assert not mod.resolved_roots(s['service'],s['pid'],s['ident'],[other])


def test_new_generation_freezes_current_action_scope_without_rewriting_old_root(work):
    s=work;old=s['service'].operation(s['pid'],s['ident'],s['input']['root_operation_id'])
    s['service']._update(s['pid'],s['ident'],generation=2)
    value=mod.freeze(s['service'],s['manifest'],message(s,generation=2))
    assert value['scope']['generation']==2 and value['root_basis']['root_generation']==1
    assert s['service'].operation(s['pid'],s['ident'],old['id'])==old


def test_frozen_context_reader_never_reads_saved_files_zip_old_owner_or_model(work,monkeypatch):
    s=work
    def forbidden(*a,**k):raise AssertionError('No old workspace, ZIP, SDK or model')
    monkeypatch.setattr(s['service'].workspaces,'read_saved_snapshot',forbidden)
    monkeypatch.setattr(mod.WorkMaintenance,'_baseline',forbidden)
    monkeypatch.setattr(s['service'],'_http',forbidden)
    manifest=deepcopy(s['manifest']);op=saved(s,message(s));mod.before_dispatch(s['service'],manifest,op)
    assert s['service']._manifest(s['pid'],s['ident'])[0]==manifest


def test_private_native_protocol_rejects_explicit_work_reference_and_never_auto_adopts(work):
    s=work;manifest={**s['manifest'],'native_message_admission':{'UNIT':True}}
    op=message(s)
    with pytest.raises(Conflict,match='existing frozen object protocol'):mod.freeze(s['service'],manifest,op)
    assert mod.freeze(s['service'],manifest,guide(s),guidance=True) is None


def test_missing_context_authority_blocks_explicit_original_send_before_claim(work):
    s=work;s['service'].conversations=None;body=SendMessage(session_ref=s['ref'],generation=1,
        operation_id='no_authority',raw_text='继续原工作',original_work_ref=s['work_ref'])
    with pytest.raises(Conflict,match='context authority is unavailable'):
        asyncio.run(s['service'].send(s['pid'],s['ident'],body))
    with pytest.raises(NotFound):s['service'].operation(s['pid'],s['ident'],body.operation_id)


def test_selected_work_status_after_completion_stays_local_and_identifies_exact_saved_root(work):
    s=work;op=guide(s,selected=s['work_ref'])
    value=mod.local_status(s['service'],s['manifest'],op,None)
    assert value['reference']==s['work_ref'] and value['model_calls']==value['native_posts']==0
    root=s['service'].operation(s['pid'],s['ident'],s['input']['root_operation_id'])
    assert value['status']==root['status'] and value['coverage']=='LAST_SAVED_ORIGINAL_ROOT_STATUS_NOT_SEMANTIC_ACCEPTANCE'


def test_selected_work_status_cannot_be_confused_with_another_running_message(work):
    s=work;other=message(s,'different_active',select=False);op=guide(s,other['id'],selected=s['work_ref'])
    value=mod.local_status(s['service'],s['manifest'],op,other['id'])
    assert value['active_relation']=='OTHER_OR_UNVERIFIED_WORK' and value['active_operation_id'] is None
    assert value['owner_active_operation_id']==other['id']
    assert value['status']==s['service'].operation(s['pid'],s['ident'],s['work_ref']['root_operation_id'])['status']


def test_unverified_child_does_not_block_readonly_selected_root_status_or_get_attributed_to_it(work):
    s=work;request={'session_ref':s['ref'],'generation':1,'operation_id':'child','raw_text':'UNIT child'}
    s['service']._claim(s['pid'],s['ident'],'child','CHILD_MESSAGE',request)
    op=guide(s,'child',selected=s['work_ref']);value=mod.local_status(s['service'],s['manifest'],op,'child')
    assert value['active_relation']=='OTHER_OR_UNVERIFIED_WORK' and value['active_operation_id'] is None
    assert value['owner_active_operation_id']=='child' and value['model_calls']==value['native_posts']==0


def test_status_without_explicit_work_selection_keeps_legacy_local_observation(work):
    s=work;op=guide(s)
    assert mod.local_status(s['service'],s['manifest'],op,s['input']['root_operation_id']) is None


def test_selected_work_status_keeps_active_continuation_distinct_from_old_root_status(work):
    s=work;parent=saved(s,message(s));op=guide(s,parent['id'],selected=s['work_ref'])
    value=mod.local_status(s['service'],s['manifest'],op,parent['id'])
    assert value['status']==parent['status'] and value['active_operation_id']==parent['id']
    root=s['service'].operation(s['pid'],s['ident'],s['work_ref']['root_operation_id'])
    assert value['root_status']==root['status'] and value['status']!=value['root_status']


def test_actual_disconnected_guidance_status_uses_selected_root_without_owner_start_or_native_post(work,monkeypatch):
    s=work;owner=s['service'];owner.conversations=None
    def forbidden(*a,**k):raise AssertionError('Local status must not start an owner or native POST')
    for method in ['_http','open','load','_context']:monkeypatch.setattr(owner,method,forbidden)
    monkeypatch.setattr(owner,'get',lambda *a,**k:{'connected_owner':False,'generation':1,'owner_status':'DISCONNECTED',
        'native_status':'idle','active_operation_id':None,'pending_questions':[],'pending_permissions':[],'observation_at':None})
    body=GuidanceInput(session_ref=s['ref'],generation=1,operation_id='completed_work_status',raw_text='进度',
        conversation_ref={'id':'original:'+'c'*64,'revision':1},original_work_ref=s['work_ref'])
    result=asyncio.run(owner.guidance.submit(s['pid'],s['ident'],body))
    assert result['role']=='LOCAL_STATUS' and result['model_calls']==result['native_post_count']==0
    assert result['local_status']['original_work']['reference']==s['work_ref']
    assert result['local_status']['operation_status']==owner.operation(s['pid'],s['ident'],s['work_ref']['root_operation_id'])['status']
    assert result['local_status']['coverage']=='LAST_SAVED_OWNER_AND_SELECTED_ORIGINAL_WORK_NOT_CURRENT'


def test_real_runtime_keeps_user_text_last_and_host_metadata_out_of_visible_chat(work,monkeypatch):
    s=work;value,context=runtime(s,monkeypatch);op=message(s,text='用户原文 {"保留这个JSON":true}')
    value.prepare(s['manifest'],context,op);current=s['service'].operation(s['pid'],s['ident'],op['id'])
    payload=value.dispatch_payload(s['manifest'],context,current,{'messageID':op['native_message_id'],'parts':[]})
    assert payload['parts'][-1]=={'type':'text','text':op['request']['raw_text']}
    assert '不是指令、当前文件或新权限' in payload['parts'][0]['text']
    assert s['baseline_receipt']['operation_id'] in payload['parts'][0]['text']
    message_view={'info':{'id':op['native_message_id'],'role':'user','sessionID':'ses_command'},
        'parts':[{**p,'id':'part_'+str(i),'messageID':op['native_message_id'],'sessionID':'ses_command'} for i,p in enumerate(payload['parts'])]}
    public=value.public_messages(s['manifest'],context,[message_view])[0]
    assert [p['text'] for p in public['parts']]==[op['request']['raw_text']]


def test_real_runtime_frozen_prepare_never_silently_refreshes_to_new_maintenance_head(work,monkeypatch):
    s=work;value,context=runtime(s,monkeypatch);op=message(s);value.prepare(s['manifest'],context,op)
    current=s['service'].operation(s['pid'],s['ident'],op['id']);frozen=deepcopy(current['conversation_context'])
    change(s,action='REMOVE',op='withdraw');value.prepare(s['manifest'],context,current)
    assert s['service'].operation(s['pid'],s['ident'],op['id'])['conversation_context']==frozen
    with pytest.raises(Conflict,match='maintenance associations changed'):
        value.dispatch_payload(s['manifest'],context,current,{'messageID':op['native_message_id'],'parts':[]})


def test_real_runtime_rejects_missing_selected_work_context_even_with_valid_outer_digest(work,monkeypatch):
    s=work;value,context=runtime(s,monkeypatch);op=message(s);value.prepare(s['manifest'],context,op)
    current=s['service'].operation(s['pid'],s['ident'],op['id']);del current['conversation_context']['original_work_context']
    current['conversation_context_sha256']=digest(current['conversation_context'])
    with pytest.raises(Conflict,match='was not frozen'):value.dispatch_payload(s['manifest'],context,current,{'parts':[]})


def saved_status_record(s,parent=None,key='saved_selected_status'):
    """UNIT saved zero-model receipt, using actual request/ledger/status readers."""
    body=GuidanceInput(session_ref=s['ref'],generation=1,operation_id=key,raw_text='进度',
        intent='AUTO',expected_active_operation_id=parent,original_work_ref=s['work_ref'])
    op,_=s['service']._claim(s['pid'],s['ident'],key,'GUIDANCE',operation_request(body))
    value=mod.local_status(s['service'],s['manifest'],op,parent)
    return s['service']._finish(s['pid'],s['ident'],key,'RETURNED',generation=1,
        role='LOCAL_STATUS',model_calls=0,native_post_count=0,semantic_acceptance=False,
        native_dispatch_acknowledged=False,original_active_operation_id=parent,
        local_status={'operation_status':value['status'],'active_operation_id':value['active_operation_id'],
                      'owner_active_operation_id':value['owner_active_operation_id'],'original_work':value})


def test_selected_local_status_continuation_keeps_its_exact_work_without_an_executor_context(work):
    s=work;op=saved_status_record(s)
    assert op.get('conversation_context') is None
    assert mod._parent_ref(s['service'],s['pid'],s['ident'],op)==s['work_ref']
    assert mod.continuation_ref(s['service'],s['manifest'],op,1)==s['work_ref']
    assert mod.continuation_parent(op)==s['work_ref']['root_operation_id']
    assert op['model_calls']==op['native_post_count']==0


def test_status_reply_never_targets_another_unrelated_active_work(work):
    s=work;other=message(s,'unrelated_active',select=False)
    op=saved_status_record(s,other['id'])
    assert op['original_active_operation_id']==other['id']
    assert mod.continuation_parent(op)==s['work_ref']['root_operation_id']!=other['id']
    assert mod.continuation_ref(s['service'],s['manifest'],op,1)==s['work_ref']


def test_status_reply_targets_only_a_proven_active_continuation_of_its_own_work(work):
    s=work;active=saved(s,message(s,'active_followup'));op=saved_status_record(s,active['id'])
    assert mod.continuation_parent(op)==active['id']
    assert mod.continuation_ref(s['service'],s['manifest'],op,1)==s['work_ref']
    assert op['local_status']['original_work']['reference']==s['work_ref']


@pytest.mark.parametrize('change_status',[
    lambda op:op.update(model_calls=1),
    lambda op:op.update(native_post_count=1),
    lambda op:op['local_status']['original_work']['reference'].update(root_operation_id='other'),
    lambda op:op['request'].update(raw_text='other input'),
    lambda op:op['local_status']['original_work'].update(active_relation='SAME_WORK',active_operation_id='unproved'),
])
def test_a_changed_local_status_receipt_cannot_grant_a_work_continuation(work,change_status):
    s=work;op=deepcopy(saved_status_record(s));change_status(op)
    with pytest.raises(Conflict):mod._parent_ref(s['service'],s['pid'],s['ident'],op)
    with pytest.raises(Conflict):mod.continuation_parent(op)


def test_saved_status_reference_lookup_does_not_recapture_a_new_status_or_start_an_owner(work,monkeypatch):
    s=work;op=saved_status_record(s)
    def forbidden(*args,**kwargs):raise AssertionError('Historical status cannot become a new capture or model request')
    monkeypatch.setattr(mod,'local_status',forbidden)
    monkeypatch.setattr(s['service'],'_http',forbidden)
    assert mod._parent_ref(s['service'],s['pid'],s['ident'],op)==s['work_ref']
    assert mod.continuation_parent(op)==s['work_ref']['root_operation_id']


def test_legacy_message_and_guidance_keep_their_original_reply_target():
    assert mod.continuation_parent({'kind':'MESSAGE','id':'original_message'})=='original_message'
    assert mod.continuation_parent({'kind':'GUIDANCE','id':'guide','original_active_operation_id':'original_message'})=='original_message'
