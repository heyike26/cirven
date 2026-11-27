"""AUTHORED_NOT_RUN. Real Store/workspaces/capabilities and canonical journals.

The original owner I/O uses metadata_service's explicit UNIT boundary; no
model, SDK or OS process is evidence from these tests.
"""
import asyncio
from copy import deepcopy
from types import MethodType, SimpleNamespace

import pytest

from apps.api.conversation_configuration import ConversationConfiguration, confirmation_value
from packages.agent_sessions.models import OpenConversation
from packages.agent_sessions.service import AgentSessions
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Store, Conflict, NotFound
from packages.domain.tasks import digest
from test_canonical_provider_generation import metadata_service


@pytest.fixture
def configured(metadata_service, tmp_path):
    assert not str(tmp_path.resolve()).startswith('/mnt/d/')
    h=metadata_service; s=h.service.sessions
    store=Store(tmp_path/'configuration.sqlite3')
    pid=store.create_project('Execution configuration', 'UNIT no SDK')['id']
    caps=NativeCapabilities(store,tmp_path)
    s.capabilities=caps; s.workspaces=LocalWorkspaces(store,tmp_path)
    s.internal_project=lambda:pid
    s.configuration=lambda:{'capability_binding_sha256':caps.native_binding_snapshot(validate_mcp_ids=set())['sha256'],
                            'configuration_refs':caps._index()}
    s._admit_open=MethodType(AgentSessions._admit_open,s)
    s._admit_observation_paths=MethodType(AgentSessions._admit_observation_paths,s)
    # No SDK context exists at this explicit owner-I/O UNIT boundary.
    def saved_source(_manifest,state):
        if state.get('context_authority_issue'):raise Conflict('SOURCE_CONTEXT_CHANGED: UNIT withdrawn source')
        return {'native_context_present':False,'record_sha256':None,'source_selections_verified':0}
    s.context_authority=SimpleNamespace(saved=saved_source)
    s.conversations=SimpleNamespace(validate_open=lambda *_args,**_kwargs:None)
    h.service.threads.store=store
    def missing(*_args):raise NotFound('UNIT original open not committed')
    s.open_operation=missing
    async def stop(pid,sid,body):
        h.calls['stop']+=1
        old=h.views[sid]
        old.update(connected_owner=False,owner_status='DISCONNECTED',process_identity=None,controller_identity=None)
        return {**deepcopy(old),'last_control_operation':{'status':'RETURNED','exact_owned_process_stop_confirmed':True}}
    s.controlled_disconnect=stop
    h.configuration=ConversationConfiguration(h.service);h.pid=pid;h.caps=caps
    return h


def packet(h,key,*,workspace=None,write=False,commands=None,max_calls=24):
    p=h.configuration.prepare({'conversation_ref':h.scope['conversation_ref']})
    request=OpenConversation(operation_id=key,conversation_ref=h.scope['conversation_ref'],project_id=None,
        provider_profile_ref=h.profile,capability_binding_sha256=p['authority']['capability_binding_sha256'],
        workspace_ref=workspace,allowed_effects=(['WORKSPACE_WRITE'] if write else [])+(['BACKGROUND_COMMAND'] if commands else []),
        command_allowlist=commands or [],mcp_tool_selection=[],max_calls=max_calls).model_dump()
    execution=h.pid if workspace else p['default_execution_project_id']
    return {'request':request,'original_source':p['authority']['original_source'],
        'preparation_sha256':p['preparation_sha256'],'execution_project_id':execution,
        'confirmation_sha256':digest(confirmation_value(request,p['preparation_sha256'],execution))}


def test_preparation_is_saved_metadata_without_new_owner_message_or_project(configured):
    h=configured
    with h.service.threads.store.connect() as connection:before=connection.execute('SELECT count(*) FROM projects').fetchone()[0]
    a=h.configuration.prepare({'conversation_ref':h.scope['conversation_ref']})
    b=h.configuration.prepare({'conversation_ref':h.scope['conversation_ref']})
    assert a['preparation_sha256']==b['preparation_sha256'] and a['original_request'] is None
    assert a['read_only'] and not a['owner_started'] and not a['permission_granted'] and a['model_calls']==0
    assert h.calls=={'open':0,'send':0,'stop':0}
    with h.service.threads.store.connect() as connection:assert before==connection.execute('SELECT count(*) FROM projects').fetchone()[0]


def test_confirmed_empty_chat_opens_one_owner_without_sending_a_message(configured):
    h=configured;p=packet(h,'first_config')
    view=asyncio.run(h.configuration.configure(p));assert view['connected_owner']
    result=h.service.open_operation('first_config',None)
    assert result['execution_confirmation_sha256']==p['confirmation_sha256']
    assert result['conversation_bound'] and result['owner_started_by_query'] is False
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_settled_same_profile_can_explicitly_change_workspace_write_and_exact_commands(configured,tmp_path_factory):
    h=configured;asyncio.run(h.configuration.configure(packet(h,'first')))
    root=tmp_path_factory.mktemp('user-workspace');(root/'a.py').write_text('KEEP_USER_BYTES')
    binding=h.service.sessions.workspaces.bind(h.pid,WorkspaceInput(idempotency_key='selected',root=str(root),allowed_write_paths=['a.py']))
    workspace={key:binding[key] for key in ('id','revision','binding_sha256')}
    p=packet(h,'confirmed_configuration',workspace=workspace,write=True,commands=['python check.py'],max_calls=12)
    result=asyncio.run(h.configuration.configure(p))
    assert result['session_ref']!=p['original_source']['session_ref']
    request=h.service.sessions._manifest(result['project_id'],result['session_ref']['id'])[0]['request']
    assert request['workspace_ref']==workspace and request['command_allowlist']==['python check.py']
    assert request['provider_profile_ref']==h.profile and request['max_calls']==12
    assert h.calls=={'open':2,'send':0,'stop':1} and (root/'a.py').read_text()=='KEEP_USER_BYTES'
    assert h.service.current_owner(h.scope)['status']=='CURRENT'


def test_lost_reply_uses_original_receipt_and_same_confirmation_without_restarting(configured):
    h=configured;p=packet(h,'exact_original')
    first=asyncio.run(h.configuration.configure(p));before=deepcopy(h.calls)
    receipt=h.service.open_operation('exact_original',None)
    again=asyncio.run(h.configuration.configure(deepcopy(p)))
    assert receipt['session']['session_ref']==first['session_ref']==again['session_ref'] and h.calls==before


def test_stale_preparation_rejected_before_original_stop_or_new_claim(configured):
    h=configured;asyncio.run(h.configuration.configure(packet(h,'first')))
    p=packet(h,'stale');p['preparation_sha256']='f'*64
    p['confirmation_sha256']=digest(confirmation_value(p['request'],p['preparation_sha256'],p['execution_project_id']))
    with pytest.raises(Conflict):asyncio.run(h.configuration.configure(p))
    assert h.calls=={'open':1,'send':0,'stop':0}
    assert not h.service._path('connections',[None,'stale']).exists()


def test_write_without_registered_workspace_rejected_before_retiring_owner(configured):
    h=configured;asyncio.run(h.configuration.configure(packet(h,'first')))
    p=packet(h,'bad_write',write=True)
    with pytest.raises(Conflict):asyncio.run(h.configuration.configure(p))
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_unknown_original_operation_blocks_explicit_configuration(configured):
    h=configured;view=asyncio.run(h.configuration.configure(packet(h,'first')))
    request={'original':'KEEP_UNKNOWN'}
    h.service.sessions._write(h.service.sessions._path(h.pid,view['session_ref']['id'],'operations','unknown.json'),
        {'id':'unknown','kind':'MESSAGE','status':'UNKNOWN_OUTCOME','session_ref':view['session_ref'],
         'request':request,'request_sha256':digest(request)})
    with pytest.raises(Conflict):asyncio.run(h.configuration.configure(packet(h,'blocked')))
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_withdrawn_source_requires_empty_context_recovery_instead_of_configuration(configured):
    h=configured;view=asyncio.run(h.configuration.configure(packet(h,'first')))
    h.views[view['session_ref']['id']]['context_authority_issue']={'code':'SOURCE_CONTEXT_CHANGED'}
    with pytest.raises(Conflict):h.configuration.prepare({'conversation_ref':h.scope['conversation_ref']})
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_raw_open_cannot_borrow_a_confirmed_configuration_operation(configured):
    h=configured;p=packet(h,'confirmed');asyncio.run(h.configuration.configure(p));before=deepcopy(h.calls)
    with pytest.raises(Conflict):asyncio.run(h.service.open(p['request']))
    assert h.calls==before


def test_confirmation_journal_tamper_rejected_by_original_read(configured):
    h=configured;p=packet(h,'confirmed');asyncio.run(h.configuration.configure(p))
    path=h.service._path('connections',[None,'confirmed']);row=h.service.sessions._read(path)
    row['configuration_confirmation']['execution_project_id']='OTHER_UNAUTHORIZED_PROJECT'
    h.service.sessions._write(path,row,replace=True)
    with pytest.raises(Conflict):h.service.open_operation('confirmed',None)
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_legacy_provider_recovery_still_requires_proven_original_auth_failure(configured):
    h=configured;asyncio.run(h.configuration.configure(packet(h,'first')))
    body=packet(h,'legacy')['request'];body['provider_profile_ref']={**h.profile,'id':'another_profile'}
    with pytest.raises(Conflict,match='401'):asyncio.run(h.service.open(body))
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_idle_owner_without_any_original_turn_does_not_require_an_operations_folder(configured):
    h=configured;view=asyncio.run(h.configuration.configure(packet(h,'first')))
    h.service.sessions._path(h.pid,view['session_ref']['id'],'operations').rmdir()
    asyncio.run(h.configuration.configure(packet(h,'second')))
    assert h.calls=={'open':2,'send':0,'stop':1}


def test_missing_operations_after_a_recorded_turn_is_never_treated_as_an_empty_history(configured):
    h=configured;view=asyncio.run(h.configuration.configure(packet(h,'first')))
    h.service.sessions._path(h.pid,view['session_ref']['id'],'operations').rmdir()
    h.views[view['session_ref']['id']]['native_turns_started']=1
    with pytest.raises(Conflict,match='history is missing'):asyncio.run(h.configuration.configure(packet(h,'blocked')))
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_native_and_discovery_history_sources_never_fabricate_an_original_owner(configured):
    h=configured;h.scope['sources']=[{'original_source':{'kind':'native','project_id':None,'conversation_id':'native_history'}},
        {'original_source':{'kind':'discovery','project_id':h.pid,'object_ref':{'id':'discovery','revision':1,'sha256':'a'*64}}}]
    p=h.configuration.prepare({'conversation_ref':h.scope['conversation_ref']})
    assert p['authority']['original_source'] is None and p['original_request'] is None
    assert h.calls=={'open':0,'send':0,'stop':0}


def test_one_original_owner_is_unambiguous_alongside_readonly_native_history(configured):
    h=configured;view=asyncio.run(h.configuration.configure(packet(h,'first')))
    h.scope['sources'].append({'original_source':{'kind':'native','project_id':None,'conversation_id':'old_native_history'}})
    p=h.configuration.prepare({'conversation_ref':h.scope['conversation_ref']})
    assert p['authority']['original_source']['session_ref']==view['session_ref']
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_legacy_original_generation_hint_cannot_prepare_a_reloaded_owner(configured):
    h=configured;view=asyncio.run(h.configuration.configure(packet(h,'first')))
    h.views[view['session_ref']['id']]['generation']=2
    with pytest.raises(Conflict,match='generation'):
        h.configuration.prepare({'conversation_ref':h.scope['conversation_ref'],'original_source':h.service._source(view)})
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_current_provider_generation_hint_cannot_prepare_a_different_loaded_generation(configured):
    h=configured;asyncio.run(h.configuration.configure(packet(h,'first')))
    view=asyncio.run(h.configuration.configure(packet(h,'second')))
    h.views[view['session_ref']['id']]['generation']=2
    with pytest.raises(Conflict,match='generation'):
        h.configuration.prepare({'conversation_ref':h.scope['conversation_ref'],'original_source':h.service._source(view)})
    assert h.calls=={'open':2,'send':0,'stop':1}


def test_existing_original_open_key_is_rejected_before_retiring_the_current_owner(configured):
    h=configured;asyncio.run(h.configuration.configure(packet(h,'first')))
    p=packet(h,'already_used')
    h.service.sessions.open_operation=lambda *_:{'operation_id':'already_used'}
    with pytest.raises(Conflict,match='already belongs'):asyncio.run(h.configuration.configure(p))
    assert h.calls=={'open':1,'send':0,'stop':0}


def test_fresh_current_source_authority_can_supersede_a_cached_unavailable_warning(configured):
    h=configured;view=asyncio.run(h.configuration.configure(packet(h,'first')))
    h.views[view['session_ref']['id']]['context_authority_issue']={'code':'SOURCE_CONTEXT_UNAVAILABLE'}
    # UNIT boundary: the authoritative read has recovered; it does not clear
    # or edit the old warning as a side effect of preparation.
    h.service.sessions.context_authority.saved=lambda *_:{'native_context_present':False,'record_sha256':None,'source_selections_verified':0}
    p=packet(h,'confirmed_current_source')
    asyncio.run(h.configuration.configure(p))
    assert h.calls=={'open':2,'send':0,'stop':1}
