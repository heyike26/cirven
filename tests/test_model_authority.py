"""AUTHORED_NOT_RUN: real metadata authority and original request-plan handler.

PID/transport observation and editor freshness are explicitly UNIT boundaries.
No SDK/plugin process, subscription, upstream call, Windows or IDE is run.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.agent_sessions.model_authority import admit, ModelAuthorityCall
from packages.adapters.opencode_model_authority import prepare,bind
from packages.domain.context_graph import AssociationChange
from packages.domain.store import Conflict
from test_context_authority import scene,track
from test_session_full_diffs import coding


def setup(s):
    graph,guard,ctx,doc,source,edge,selected=scene(s);track(s,guard,ctx,selected)
    owner=s['value'];ctx.update(directory=str(s['folder']),model_authority={'known_sessions':set(),'lock':asyncio.Lock()})
    calls={'context':[],'capture':0,'native_get':[]}
    owner._claim(s['pid'],s['ident'],'unit_task','MESSAGE',{'unit':'ORIGINAL_PARENT'})
    owner._finish(s['pid'],s['ident'],'unit_task','DISPATCHING',generation=1,original_session_id=ctx['session_id'],native_message_id='msg_original')
    # Explicit UNIT dispatch record: production model admission requires the
    # same exact user/work binding that the real send path persists first.
    from packages.agent_sessions.task_resources import for_operation
    from packages.agent_sessions.resource_retention import bind_user
    budget=for_operation(owner,s['manifest'],owner.operation(s['pid'],s['ident'],'unit_task'));budget.allocate()
    with owner.store.connect() as db:
        db.execute('BEGIN IMMEDIATE');bind_user(owner,db,s['manifest'],1,ctx['session_id'],'msg_original',budget)
    def context(pid,sid,ref,generation,*,intent):
        assert owner.active.get(sid) is ctx and generation==ctx['generation']
        assert owner._manifest(pid,sid)[1]==ref;calls['context'].append(intent);return s['manifest'],ctx
    async def capture(*a,**kw):calls['capture']+=1
    async def native(*a,**kw):raise AssertionError('No actual native read expected')
    owner._context=context;owner._http=native;owner.editor_captures=SimpleNamespace(broker=SimpleNamespace(admit=capture,changed=lambda:None))
    body={'schema':'eeas.model-authority-call.v1','owner_ref':{'project_id':s['pid'],'session_ref':s['ref']},
        'generation':1,'root_native_session_id':ctx['session_id'],'native_session_id':ctx['session_id'],
        'user_message_id':'msg_original','agent':'build','step':1}
    return graph,guard,ctx,edge,source,calls,body


def test_native_plan_checks_sources_and_metadata_without_reserving_api_counter_or_reading_bodies(coding,monkeypatch):
    s=coding;graph,guard,ctx,_,_,calls,body=setup(s)
    monkeypatch.setattr(graph,'source_reader',lambda *_:pytest.fail('Metadata admission must not read source bodies'))
    monkeypatch.setattr(s['value'],'_reserve_rpc',lambda *_:pytest.fail('Native hook must not double reserve API'))
    before=s['value']._state(s['pid'],s['ident']).get('model_calls_reserved')
    value=asyncio.run(admit(s['value'],s['manifest'],ctx,body))
    assert value['admitted'] is True and value['model_calls_reserved']==0
    assert calls['context']==['OBSERVE','EXECUTE'] and calls['capture']==1
    assert s['value']._state(s['pid'],s['ident']).get('model_calls_reserved')==before


def test_revoke_between_native_plans_blocks_following_plan_and_preserves_original_source(coding):
    s=coding;graph,guard,ctx,edge,source,calls,body=setup(s)
    asyncio.run(admit(s['value'],s['manifest'],ctx,body))
    graph.associate(AssociationChange(operation_id='withdraw_native',scope_id='root',source=source,expected_revision=edge['revision'],state='RETRACTED'))
    with pytest.raises(Conflict,match='SOURCE_CONTEXT_CHANGED'):
        asyncio.run(admit(s['value'],s['manifest'],ctx,{**body,'step':2}))
    assert calls['capture']==1
    assert s['value']._state(s['pid'],s['ident'])['context_authority_issue']['original_history_retained'] is True


@pytest.mark.parametrize('change',['root','generation','owner'])
def test_wrong_original_scope_is_rejected_before_editor_or_native_reads(coding,change):
    s=coding;*_,calls,body=setup(s);ctx=s['value'].active[s['ident']]
    if change=='root':body['root_native_session_id']='ses_foreign'
    elif change=='generation':body['generation']=2
    else:body['owner_ref']['project_id']='foreign'
    with pytest.raises(Conflict,match='OWNER_CHANGED'):asyncio.run(admit(s['value'],s['manifest'],ctx,body))
    assert calls['context']==[] and calls['capture']==0


@pytest.mark.parametrize('change',['operation','terminal','context'])
def test_original_work_change_during_capture_cannot_admit_later_plan(coding,change):
    s=coding;_,_,ctx,_,_,calls,body=setup(s);owner=s['value'];real=owner._context
    async def capture(*_,**__):
        if change=='operation':owner._update(s['pid'],s['ident'],active_operation_id='another')
        elif change=='terminal':owner._finish(s['pid'],s['ident'],'unit_task','UNKNOWN_OUTCOME')
        else:
            def replaced(*args,**kw):
                manifest,value=real(*args,**kw)
                return manifest,{**value}
            owner._context=replaced
    owner.editor_captures.broker.admit=capture
    with pytest.raises(Conflict,match='ORIGINAL_OPERATION_CHANGED'):
        asyncio.run(admit(owner,s['manifest'],ctx,body))


def test_dirty_capture_denies_before_execute_or_model_admission(coding):
    s=coding;_,_,ctx,_,_,calls,body=setup(s)
    async def capture(*_,**__):raise Conflict('EDITOR_CAPTURE_DIRTY: UNIT original observer')
    s['value'].editor_captures.broker.admit=capture
    with pytest.raises(Conflict,match='EDITOR_CAPTURE_DIRTY'):asyncio.run(admit(s['value'],s['manifest'],ctx,body))
    assert calls['context']==['OBSERVE']


def test_child_requires_exact_native_parent_chain_and_reuses_bounded_metadata_identity(coding):
    s=coding;_,_,ctx,_,_,calls,body=setup(s);owner=s['value']
    for event in [
        {'type':'message.updated','properties':{'info':{'id':'msg_parent','role':'assistant','parentID':'msg_original','sessionID':ctx['session_id']}}},
        {'type':'message.part.updated','properties':{'part':{'id':'prt_task','type':'tool','tool':'task','messageID':'msg_parent','sessionID':ctx['session_id'],
            'state':{'status':'running','metadata':{'sessionId':'ses_child','parentSessionId':ctx['session_id']}}}}}]:
        owner._event(s['pid'],s['ident'],'ORIGINAL_EVENT',{'generation':1,'native_payload':event})
    async def native(_ctx,method,route):
        calls['native_get'].append(route)
        assert method=='GET' and route=='/session/ses_child'
        return {'id':'ses_child','parentID':ctx['session_id'],'directory':ctx['directory']}
    owner._http=native
    async def work():
        await admit(owner,s['manifest'],ctx,{**body,'native_session_id':'ses_child','user_message_id':'msg_child'})
        await admit(owner,s['manifest'],ctx,{**body,'native_session_id':'ses_child','user_message_id':'msg_child','step':2})
    asyncio.run(work());assert calls['native_get']==['/session/ses_child']


@pytest.mark.parametrize('bad',['directory','cycle','depth'])
def test_unproven_child_does_not_reach_capture(coding,bad):
    s=coding;_,_,ctx,_,_,calls,body=setup(s)
    async def native(_ctx,method,route):
        current=route.rsplit('/',1)[1]
        return {'id':current,'parentID':current if bad=='cycle' else current+'_p',
            'directory':'FOREIGN' if bad=='directory' else ctx['directory']}
    s['value']._http=native
    with pytest.raises(Conflict,match='MODEL_AUTHORITY_CHILD_'):
        asyncio.run(admit(s['value'],s['manifest'],ctx,{**body,'native_session_id':'ses_child'}))
    assert calls['capture']==0 and ctx['model_authority']['known_sessions']==set()


def test_reloaded_owner_prepares_separate_generation_scope_and_never_rebinds_old_scope(coding):
    s=coding;root=Path(__file__).resolve().parents[1]
    bridge=SimpleNamespace(base_url='http://127.0.0.1:1',key='f'*64)
    kw={'owner_ref':{'project_id':s['pid'],'session_ref':s['ref']},'directory':str(s['folder']),'bridge':bridge}
    first=prepare(root,s['runtime'],generation=1,**kw);bound=bind(first,'ses_old')
    before=Path(bound['config_path']).read_bytes()
    second=prepare(root,s['runtime'],generation=2,**kw)
    assert first['config_path']!=second['config_path']
    assert Path(bound['config_path']).read_bytes()==before
    with pytest.raises(Conflict,match='INITIAL_SCOPE_CHANGED'):bind(bound,'ses_replacement')


def test_request_schema_never_accepts_message_body_or_model_settings(coding):
    *_,body=setup(coding)
    with pytest.raises(ValueError):ModelAuthorityCall.model_validate({**body,'raw_text':'PRIVATE_SOURCE'})
    with pytest.raises(ValueError):ModelAuthorityCall.model_validate({**body,'model':'expensive-replacement'})
