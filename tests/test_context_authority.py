"""AUTHORED_NOT_RUN: real Store/context graph and owner authority registry.

The native session identity below is an explicit UNIT boundary. No CLI, model,
Windows/IDE, source-erasure or fresh-owner recovery qualification is claimed.
"""
from types import SimpleNamespace

import pytest

from apps.api.context_graph import ApplicationContextSources
from packages.domain.context_graph import (ContextGraph,AssociationChange,ScopeChange,SelectionInput,
    SourceRef,NoteChange,ForgetInput)
from packages.domain.store import Conflict
from test_session_full_diffs import coding
from test_context_defaults_and_grants import foreign_scope,review,grant_request,revoke


def scene(s,*,loaded=False):
    owner=s['value'];pid=s['pid'];store=owner.store
    adapter=ApplicationContextSources(SimpleNamespace(state=SimpleNamespace(store=store)))
    graph=ContextGraph(store,adapter.read,lambda _: {'project_id':pid})
    graph.save_scope('root',ScopeChange(operation_id='scope_root',expected_revision=0,kind='PROJECT',project_id=pid,label='Root'))
    document=store.save_document(pid,'authority.md',b'KEEP_ORIGINAL_SOURCE','UPLOAD')
    source=SourceRef(kind='DOCUMENT',project_id=pid,object_id=document['id'],revision=1,sha256=document['sha256'])
    edge=graph.associate(AssociationChange(operation_id='link',scope_id='root',source=source,expected_revision=0))
    selected=graph.select(SelectionInput(operation_id='select',scope_id='root'))
    context={'generation':1,'session_id':'ses_context_unit'};owner.active[s['ident']]=context
    owner._update(pid,s['ident'],owner_status='READY',active_operation_id='unit_task',last_event_seq=0)
    guard=owner.context_authority;guard.graph=graph;guard.begin(s['manifest'],context,loaded_existing=loaded)
    return graph,guard,context,document,source,edge,selected


def track(s,guard,context,selected,*,project=None):
    guard.track(s['manifest'],context,selection_id=selected['selection_id'],project_id=project or s['pid'],conversation_id=None)


# AUTHORED_NOT_RUN: fresh saved metadata proof used by explicit configuration.
def test_saved_source_authority_is_fresh_readonly_and_does_not_change_live_cache_or_owner_state(coding,monkeypatch):
    s=coding;graph,guard,context,*_,selected=scene(s);track(s,guard,context,selected)
    owner=s['value'];state={**owner._state(s['pid'],s['ident']),'original_session_id':context['session_id']}
    path=owner._path(s['pid'],s['ident'],'state.json');before=path.read_bytes();registry=guard._path(s['manifest'],context).read_bytes();cache=dict(context)
    monkeypatch.setattr(graph,'source_reader',lambda *_:pytest.fail('No source body read during saved metadata proof'))
    first=guard.saved(s['manifest'],state);second=guard.saved(s['manifest'],state)
    assert first==second and first['source_selections_verified']==1
    assert context==cache and path.read_bytes()==before and guard._path(s['manifest'],context).read_bytes()==registry


def test_saved_source_retraction_cannot_hide_behind_a_previous_live_cache_and_never_writes_an_issue(coding):
    s=coding;graph,guard,context,_,source,edge,selected=scene(s);track(s,guard,context,selected);guard.check(s['manifest'],context)
    graph.associate(AssociationChange(operation_id='withdraw_for_configuration',scope_id='root',source=source,expected_revision=edge['revision'],state='RETRACTED'))
    owner=s['value'];state={**owner._state(s['pid'],s['ident']),'original_session_id':context['session_id']};path=owner._path(s['pid'],s['ident'],'state.json');before=path.read_bytes()
    with pytest.raises(Conflict):guard.saved(s['manifest'],state)
    assert path.read_bytes()==before and not owner._state(s['pid'],s['ident']).get('context_authority_issue')


def test_saved_legacy_context_requires_new_empty_recovery_without_altering_owner_metadata(coding):
    s=coding;_,guard,context,*_=scene(s,loaded=True);owner=s['value'];state={**owner._state(s['pid'],s['ident']),'original_session_id':context['session_id']}
    path=owner._path(s['pid'],s['ident'],'state.json');before=path.read_bytes()
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_LEGACY'):guard.saved(s['manifest'],state)
    assert path.read_bytes()==before


def inherited_configuration(s):
    """Real dependency records/journals; both native contexts are explicit UNIT identities."""
    from apps.api.canonical_conversations import CanonicalConversations
    from apps.api.conversation_configuration import confirmation_value
    from apps.api.conversation_runtime import ConversationRuntime
    from packages.agent_sessions.models import OpenConversation
    from packages.domain.tasks import digest
    graph,guard,parent,_,source,edge,selected=scene(s);track(s,guard,parent,selected)
    owner=s['value'];pid=s['pid']
    owner._update(pid,s['ident'],original_session_id=parent['session_id'],owner_status='DISCONNECTED',active_operation_id=None)
    owner.active.pop(s['ident'])  # Synthetic prior native owner has retired.
    previous=guard.saved(s['manifest'],owner._state(pid,s['ident']))
    reference={'id':'native:'+'d'*64,'revision':1}
    body=OpenConversation(operation_id='configuration_child',project_id=pid,conversation_ref=reference,
        provider_profile_ref=s['manifest']['request']['provider_profile_ref'],
        capability_binding_sha256=s['manifest']['request']['capability_binding_sha256'],mcp_tool_selection=[]).model_dump()
    original={'project_id':pid,'session_ref':s['ref'],'generation':1}
    authority={'version':1,'conversation_ref':reference,'project_id':pid,'original_source':original,
        'original_request_sha256':digest(s['manifest']['request']),'source_authority':previous,
        'capability_binding_sha256':body['capability_binding_sha256']}
    preparation=digest(authority)
    proof={'authority':authority,'preparation_sha256':preparation,'execution_project_id':pid,
        'request_sha256':digest(body),'confirmation_sha256':digest(confirmation_value(body,preparation,pid))}
    app=SimpleNamespace(state=SimpleNamespace(agent_sessions=owner,native_messages=SimpleNamespace(),conversation_threads=SimpleNamespace(store=owner.store)))
    runtime=ConversationRuntime(app,app.state.conversation_threads,None);app.state.conversation_runtime=runtime
    service=CanonicalConversations(app);app.state.canonical_conversations=service;owner.conversations=runtime
    service._save(service._path('connections',[pid,body['operation_id']]),{'kind':'CONNECTION','request':body,'body_sha256':digest(body),
        'request_sha256':digest({k:v for k,v in body.items() if k not in {'project_id','operation_id'}}),
        'phase':'BOUND','source':None,'conversation_ref':reference,'configuration_confirmation':proof,
        'configuration_confirmation_sha256':digest(proof),'closed_previous_owner':{
            'original_source':original,'owner_closed':True,'process_identity':None,'controller_identity':None}})
    claim={'operation_id':body['operation_id'],'project_id':pid,'confirmation_sha256':proof['confirmation_sha256'],'proof_sha256':digest(proof)}
    manifest={'id':'b'*32,'project_id':pid,'workspace_kind':'APP_INTERNAL_READONLY',
        'request':{k:v for k,v in body.items() if k not in {'project_id','operation_id'}},'canonical_execution_claim':claim}
    state={'generation':1,'original_session_id':'ses_configuration_child','owner_status':'READY','native_status':'idle',
        'messages':[],'active_operation_id':'child_unit_task','pending_questions':[],'pending_permissions':[],'last_event_seq':0}
    owner._write(owner._path(pid,manifest['id'],'manifest.json'),manifest)
    owner._write(owner._path(pid,manifest['id'],'state.json'),state)
    child={'generation':1,'session_id':state['original_session_id']};owner.active[manifest['id']]=child
    guard.begin(manifest,child,loaded_existing=False)
    return graph,guard,source,edge,manifest,state,child


def test_new_configuration_retains_readonly_dependency_proof_after_previous_owner_retirement(coding,monkeypatch):
    s=coding;graph,guard,_,_,manifest,state,context=inherited_configuration(s)
    monkeypatch.setattr(graph,'source_reader',lambda *_:pytest.fail('No source body read for inherited proof'))
    before=s['value']._path(s['pid'],manifest['id'],'state.json').read_bytes()
    receipt=guard.saved(manifest,state)
    assert receipt['inherited_authority_sha256'] and receipt['source_selections_verified']==0
    assert s['ident'] not in s['value'].active
    assert s['value']._path(s['pid'],manifest['id'],'state.json').read_bytes()==before
    guard.check(manifest,context)


def test_withdrawal_from_retired_parent_blocks_future_new_owner_admission_and_preserves_history(coding):
    s=coding;graph,guard,source,edge,manifest,_,context=inherited_configuration(s)
    guard.check(manifest,context)
    graph.associate(AssociationChange(operation_id='withdraw_retired_parent_source',scope_id='root',source=source,expected_revision=edge['revision'],state='RETRACTED'))
    with pytest.raises(Conflict):guard.check(manifest,context)
    current=s['value']._state(s['pid'],manifest['id'])
    assert current['context_authority_issue']['code']=='SOURCE_CONTEXT_CHANGED'
    assert current['context_authority_issue']['original_history_retained'] is True
    assert graph.store.document(s['pid'],source.object_id,1)['raw_bytes']==b'KEEP_ORIGINAL_SOURCE'


def test_configuration_start_admission_rejects_retired_source_before_any_process_hook(coding,monkeypatch):
    s=coding;graph,guard,source,edge,manifest,state,_=inherited_configuration(s)
    graph.associate(AssociationChange(operation_id='withdraw_before_configuration_start',scope_id='root',source=source,expected_revision=edge['revision'],state='RETRACTED'))
    monkeypatch.setattr(s['value'],'_start_impl',lambda *_a,**_k:pytest.fail('No process startup from source metadata validation'))
    with pytest.raises(Conflict):guard.admit_configuration_start(manifest,state)
    assert s['value']._state(s['pid'],manifest['id'])['context_authority_issue']['original_history_retained']


def test_authority_check_reads_no_source_bodies_and_unchanged_epoch_reuses_one_metadata_proof(coding,monkeypatch):
    s=coding;graph,guard,context,*_,selected=scene(s);track(s,guard,context,selected)
    count=[];original=graph.authorize_selection
    def checked(*a,**k):count.append(1);return original(*a,**k)
    monkeypatch.setattr(graph,'authorize_selection',checked)
    monkeypatch.setattr(graph,'source_reader',lambda *_:pytest.fail('No source body read during authority check'))
    guard.check(s['manifest'],context);guard.check(s['manifest'],context)
    assert len(count)==1
    proof=original(selected['selection_id'],s['pid'])
    assert proof['source_bodies_read'] is False and proof['execution_authorized'] is False


def test_retraction_invalidates_future_authority_and_preserves_original_document_history_and_registry(coding):
    s=coding;graph,guard,context,document,source,edge,selected=scene(s);track(s,guard,context,selected);guard.check(s['manifest'],context)
    before=guard._path(s['manifest'],context).read_bytes()
    graph.associate(AssociationChange(operation_id='withdraw',scope_id='root',source=source,expected_revision=edge['revision'],state='RETRACTED'))
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_CHANGED'):guard.check(s['manifest'],context)
    state=s['value']._state(s['pid'],s['ident']);seq=state['last_event_seq']
    assert state['context_authority_issue']['original_history_retained'] is True
    with pytest.raises(Conflict):guard.check(s['manifest'],context)
    assert s['value']._state(s['pid'],s['ident'])['last_event_seq']==seq
    assert guard._path(s['manifest'],context).read_bytes()==before
    assert graph.store.document(s['pid'],document['id'],1)['raw_bytes']==b'KEEP_ORIGINAL_SOURCE'


def test_original_document_revision_change_advances_epoch_and_invalidates_cached_authority(coding):
    s=coding;graph,guard,context,document,_,_,selected=scene(s);track(s,guard,context,selected);guard.check(s['manifest'],context)
    epoch=graph.authority_epoch()
    graph.store.save_document(s['pid'],'authority.md',b'USER_CHANGED_SOURCE','UPLOAD',document_id=document['id'],expected_revision=1)
    assert graph.authority_epoch()>epoch
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_CHANGED'):guard.check(s['manifest'],context)
    assert graph.store.document(s['pid'],document['id'],1)['raw_bytes']==b'KEEP_ORIGINAL_SOURCE'


def test_unrelated_scope_label_edit_does_not_revoke_ongoing_authority_but_new_dispatch_still_requires_fresh_selection(coding):
    s=coding;graph,guard,context,*_,selected=scene(s);track(s,guard,context,selected);guard.check(s['manifest'],context)
    graph.save_scope('root',ScopeChange(operation_id='rename',expected_revision=1,kind='PROJECT',project_id=s['pid'],label='New label'))
    guard.check(s['manifest'],context)
    with pytest.raises(Conflict):graph.materialize(selected['selection_id'],s['pid'])


def test_forgetting_a_dependency_blocks_a_previously_admitted_derived_note(coding):
    s=coding;graph,guard,context,_,_,edge,_=scene(s)
    note=graph.note(NoteChange(operation_id='derived',scope_id='root',title='Derived',text='REVIEWED_DERIVED_NOTE',basis=[edge['origin_id']]))
    graph.associate(AssociationChange(operation_id='approve_note',scope_id='root',expected_revision=1,
        source=SourceRef(kind='NOTE',project_id=s['pid'],object_id=note['origin_id'])))
    chosen=graph.select(SelectionInput(operation_id='with_note',scope_id='root'));track(s,guard,context,chosen)
    graph.forget(ForgetInput(operation_id='forget',origin_id=edge['origin_id'],scope_id='root'))
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_'):guard.check(s['manifest'],context)


def test_withdrawn_foreign_grant_does_not_revive_old_context_after_new_review(coding):
    s=coding;graph,guard,context,_,source,_,_=scene(s);other=graph.store.create_project('Other','')['id']
    foreign_scope(graph,other,'other');graph.change_read_grant(grant_request(review(graph,source,'other')))
    chosen=graph.select(SelectionInput(operation_id='foreign_select',scope_id='other'))
    track(s,guard,context,chosen,project=other);guard.check(s['manifest'],context)
    revoke(graph,'other');graph.change_read_grant(grant_request(review(graph,source,'other'),'new_review'))
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_CHANGED'):guard.check(s['manifest'],context)


def test_legacy_native_context_and_damaged_registry_do_not_infer_source_authority(coding):
    s=coding;graph,guard,context,*_=scene(s,loaded=True)
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_LEGACY'):guard.check(s['manifest'],context)
    assert s['value']._state(s['pid'],s['ident'])['context_authority_issue']['code']=='SOURCE_CONTEXT_LEGACY'
    path=guard._path(s['manifest'],context);path.write_text('{broken')
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_UNAVAILABLE'):guard.check(s['manifest'],context)


def test_registry_capacity_keeps_existing_dependencies_and_does_not_silently_evict_them(coding,monkeypatch):
    import packages.agent_sessions.context_authority as module
    s=coding;graph,guard,context,*_,selected=scene(s);track(s,guard,context,selected)
    before=guard._path(s['manifest'],context).read_bytes();another=graph.select(SelectionInput(operation_id='select_again',scope_id='root'))
    # A second scope creates a genuinely distinct selected-authority identity.
    graph.save_scope('topic',ScopeChange(operation_id='topic',expected_revision=0,kind='TOPIC',parent_id='root',project_id=s['pid'],label='Topic'))
    another=graph.select(SelectionInput(operation_id='topic_select',scope_id='topic'))
    monkeypatch.setattr(module,'MAX_SELECTIONS',1)
    with pytest.raises(Conflict,match='CAPACITY'):track(s,guard,context,another)
    assert guard._path(s['manifest'],context).read_bytes()==before


def test_recorded_context_does_not_silently_bypass_checks_when_graph_service_is_unavailable(coding):
    s=coding;graph,guard,context,*_,selected=scene(s);track(s,guard,context,selected);guard.graph=None
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_UNAVAILABLE'):guard.check(s['manifest'],context)
    assert s['value']._state(s['pid'],s['ident'])['context_authority_issue']['code']=='SOURCE_CONTEXT_UNAVAILABLE'
