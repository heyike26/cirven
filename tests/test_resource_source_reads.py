"""AUTHORED_NOT_RUN: real ledger and httpx bounded source reception.

Controlled native HTTP transport and immutable owner records only. No original
SDK process/model/tool, physical network/disk accounting or Windows qualification.
"""
import asyncio
from copy import deepcopy
import httpx
import pytest
from packages.domain.store import Conflict
from packages.domain.task_resource_budget import task_resource_scope,current_task_resource_budget
from packages.domain.context_read_budget import context_read_scope,current_context_read_budget
from packages.agent_sessions.resource_source_reads import read_native_json
from packages.agent_sessions.service import AgentSessions
from packages.agent_sessions.task_resources import claimed_read_scope,for_operation
from test_task_resource_budget import resource
from test_experience_candidates import experience


class Chunks(httpx.AsyncByteStream):
    def __init__(self,parts,error=None):self.parts=parts;self.error=error;self.closed=False
    async def __aiter__(self):
        for part in self.parts:yield part
        if self.error is not None:raise self.error
    async def aclose(self):self.closed=True


def test_scoped_native_get_counts_actual_raw_bytes_before_parsing_without_duplicate_source_charge(resource):
    async def run():
        b=resource['budget'];raw=b' { "a" : 1 } \n';stream=Chunks([raw[:4],raw[4:]]);calls=[]
        async def transport(request):
            calls.append(request)
            assert b.inspect()['pending_read_bytes']>0 and b.inspect()['used']['source_reads']==1
            return httpx.Response(200,stream=stream)
        async with httpx.AsyncClient(base_url='http://native.unit.invalid',transport=httpx.MockTransport(transport)) as client:
            context={'project_id':resource['authority']['project_id'],'directory':'/original','client':client,'native_command_epoch':7}
            with task_resource_scope(b),context_read_scope() as source:
                assert await AgentSessions._http(None,context,'GET','/session/ses_child',params={'limit':'40'},timeout=3)=={'a':1}
                assert source.charged_bytes==len(raw) and source.reads==1
            assert context['native_command_epoch']==7 and 'native_commands_inflight' not in context
        view=b.inspect();assert len(calls)==1 and calls[0].url.params['directory']=='/original' and calls[0].url.params['limit']=='40'
        assert view['used']['source_bytes']==len(raw) and view['used']['source_reads']==1 and view['pending_read_bytes']==0 and stream.closed
        assert view['used']['model_attempts']==0
    asyncio.run(run())
    assert current_task_resource_budget() is None and current_context_read_budget() is None


@pytest.mark.parametrize('mode',['oversized','transport','cancelled'])
def test_failed_or_cancelled_native_read_holds_its_bound_closes_stream_and_never_retries(resource,mode):
    async def run():
        b=resource['budget'];calls=[];error={'transport':httpx.ReadError('UNIT receive failed'),'cancelled':asyncio.CancelledError()} .get(mode)
        stream=Chunks([b'x'*65] if mode=='oversized' else [b'{'],error)
        async def transport(request):calls.append(request);return httpx.Response(200,stream=stream)
        async with httpx.AsyncClient(base_url='http://native.unit.invalid',transport=httpx.MockTransport(transport)) as client:
            context={'project_id':resource['authority']['project_id'],'client':client}
            with task_resource_scope(b),context_read_scope():
                with pytest.raises(asyncio.CancelledError if mode=='cancelled' else (Conflict,httpx.ReadError)):
                    await read_native_json(context,'/session/ses_child/message',expected=200,params={},options={})
                with pytest.raises(Conflict):await read_native_json(context,'/session/ses_child/message',expected=200,params={},options={})
        view=b.inspect();assert len(calls)==1 and stream.closed and view['used']['source_bytes']==64 and view['used']['source_reads']==1
        assert view['pending_read_bytes']==0 and view['used']['model_attempts']==0 and context['last_http_error']['task_resource_read']
    asyncio.run(run())


@pytest.mark.parametrize('mode',['status','malformed_json'])
def test_complete_error_response_charges_actual_bytes_and_preserves_original_status_without_retry(resource,mode):
    async def run():
        b=resource['budget'];raw=b'{}' if mode=='status' else b'{not json';calls=[];status=503 if mode=='status' else 200
        async def transport(request):calls.append(request);return httpx.Response(status,stream=Chunks([raw]))
        async with httpx.AsyncClient(base_url='http://native.unit.invalid',transport=httpx.MockTransport(transport)) as client:
            context={'project_id':resource['authority']['project_id'],'client':client}
            with task_resource_scope(b),context_read_scope():
                with pytest.raises((Conflict,ValueError)):await read_native_json(context,'/session/ses_child',expected=200,params={},options={})
        assert len(calls)==1 and b.inspect()['used']['source_bytes']==len(raw) and b.inspect()['pending_read_bytes']==0
        assert context['last_http_error']['body_complete'] and context['last_http_error']['http_status']==status
    asyncio.run(run())


def test_exhausted_or_foreign_allowance_refuses_before_opening_native_source_stream(resource):
    async def run():
        b=resource['budget'];calls=[]
        async def transport(request):calls.append(request);return httpx.Response(200,json={})
        async with httpx.AsyncClient(base_url='http://native.unit.invalid',transport=httpx.MockTransport(transport)) as client:
            with task_resource_scope(b),context_read_scope():
                with pytest.raises(Conflict):await read_native_json({'project_id':'foreign','client':client},'/session/ses_child',expected=200,params={},options={})
                b.consume(source_bytes=64)
                with pytest.raises(Conflict):await read_native_json({'project_id':resource['authority']['project_id'],'client':client},'/session/ses_child',expected=200,params={},options={})
        assert not calls and b.inspect()['used']['source_reads']==0
    asyncio.run(run())


def test_claimed_child_guidance_preflight_and_later_dispatch_resolve_same_original_work(experience):
    s=experience;o=s['sessions'];pid,sid=s['pid'],s['sid'];root_id=s['scope']['root_operation_id'];manifest,ref=o._manifest(pid,sid)
    root=o._finish(pid,sid,root_id,'ACCEPTED',generation=1);o._update(pid,sid,generation=1,active_operation_id=root_id)
    req={'operation_id':'child_preflight','session_ref':ref,'generation':1,'expected_active_operation_id':root_id}
    o._claim(pid,sid,req['operation_id'],'CHILD_GUIDANCE',req)
    original=for_operation(o,manifest,root)
    with claimed_read_scope(o,manifest,req['operation_id']) as shared:
        assert shared.id==original.id and current_task_resource_budget().id==original.id
        shared.consume(source_bytes=3,source_reads=1)
        operation=o._finish(pid,sid,req['operation_id'],'DISPATCHING',original_active_operation_id=root_id,generation=1)
        later=for_operation(o,manifest,operation)
        assert later.id==shared.id and later.inspect()['used']['source_bytes']==shared.inspect()['used']['source_bytes']>=3
    assert current_task_resource_budget() is None and current_context_read_budget() is None


@pytest.mark.parametrize('fault',['parent','generation','request_session','parent_unacknowledged'])
def test_claimed_child_parent_or_generation_mismatch_never_allocates_a_replacement_allowance(experience,fault):
    s=experience;o=s['sessions'];pid,sid=s['pid'],s['sid'];root_id=s['scope']['root_operation_id'];manifest,ref=o._manifest(pid,sid)
    o._finish(pid,sid,root_id,'RETURNED' if fault=='parent_unacknowledged' else 'ACCEPTED',generation=1)
    o._update(pid,sid,generation=1,active_operation_id='another' if fault=='parent' else root_id)
    req={'operation_id':'bad_preflight','session_ref':deepcopy(ref),'generation':2 if fault=='generation' else 1,'expected_active_operation_id':root_id}
    if fault=='request_session':req['session_ref']['sha256']='f'*64
    o._claim(pid,sid,req['operation_id'],'CHILD_GUIDANCE',req)
    before=sorted(p.name for p in (s['data']/'task-resource-budgets').glob('*'))
    with pytest.raises(Conflict):
        with claimed_read_scope(o,manifest,req['operation_id']):pytest.fail('Foreign claim admitted')
    assert before==sorted(p.name for p in (s['data']/'task-resource-budgets').glob('*'))


def test_source_settlement_ack_failure_keeps_pending_reservation_without_another_http_read(resource,monkeypatch):
    async def run():
        b=resource['budget'];calls=[];stream=Chunks([b'{}'])
        async def transport(request):calls.append(request);return httpx.Response(200,stream=stream)
        def lost_ack(*_args,**_kwargs):raise Conflict('UNIT settlement acknowledgement unavailable')
        monkeypatch.setattr(b,'finish_read',lost_ack)
        async with httpx.AsyncClient(base_url='http://native.unit.invalid',transport=httpx.MockTransport(transport)) as client:
            context={'project_id':resource['authority']['project_id'],'client':client}
            with task_resource_scope(b),context_read_scope():
                with pytest.raises(Conflict):await read_native_json(context,'/session/ses_child',expected=200,params={},options={})
                first_error=deepcopy(context['last_http_error'])
                with pytest.raises(Conflict):await read_native_json(context,'/session/ses_child',expected=200,params={},options={})
        view=b.inspect();assert len(calls)==1 and stream.closed and view['pending_read_bytes']==64 and view['used']['source_reads']==1
        assert view['used']['source_bytes']==0 and first_error['body_complete']
    asyncio.run(run())


def test_idle_child_preflight_uses_the_actual_claim_and_does_not_switch_to_another_active_work(experience):
    s=experience;o=s['sessions'];pid,sid=s['pid'],s['sid'];manifest,ref=o._manifest(pid,sid)
    o._update(pid,sid,generation=1,active_operation_id=None)
    req={'operation_id':'idle_child','session_ref':ref,'generation':1,'expected_active_operation_id':None}
    claim,_=o._claim(pid,sid,req['operation_id'],'CHILD_MESSAGE',req)
    with claimed_read_scope(o,manifest,req['operation_id']) as shared:
        assert shared.authority['root_operation_id']==req['operation_id'] and shared.id==for_operation(o,manifest,claim).id
    before=shared.path.read_bytes();o._update(pid,sid,active_operation_id=s['scope']['root_operation_id'])
    with pytest.raises(Conflict):
        with claimed_read_scope(o,manifest,req['operation_id']):pytest.fail('Idle child borrowed active allowance')
    assert shared.path.read_bytes()==before
