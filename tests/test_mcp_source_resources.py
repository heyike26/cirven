"""AUTHORED_NOT_RUN: real ledger and HTTPX with a UNIT in-memory MCP transport.

No remote server, model, SDK client or native application is exercised here.
The separate installed-MCP/SDK scenarios remain concentrated-debug work.
"""
import asyncio

import httpx2
import pytest

from packages.adapters.mcp_resources import MCPSourceTransport, MAX_HTTP_EXCHANGES
from packages.domain.store import Conflict
from packages.domain.task_resource_budget import TaskResourceLimit
from test_task_resource_budget import resource
from test_sdk_search_mcp import local_mcp,search_config


class Body(httpx2.AsyncByteStream):
    def __init__(self,chunks):self.chunks=chunks;self.closes=0
    async def __aiter__(self):
        for chunk in self.chunks:yield chunk
    async def aclose(self):self.closes+=1


class OriginalTransport(httpx2.AsyncBaseTransport):
    def __init__(self,budget=None,chunks=(b'first',b'second')):
        self.budget=budget;self.chunks=chunks;self.before=[];self.bodies=[];self.closed=False
    async def handle_async_request(self,request):
        self.before.append(None if self.budget is None else self.budget.inspect())
        body=Body(self.chunks);self.bodies.append(body)
        return httpx2.Response(200,stream=body)
    async def aclose(self):self.closed=True


def test_each_original_http_response_is_reserved_before_forward_and_settled_to_its_body(resource):
    b=resource['budget'];inner=OriginalTransport(b);admitted=[]
    async def run():
        transport=MCPSourceTransport(inner,32,b,lambda:admitted.append(True))
        async with httpx2.AsyncClient(transport=transport) as client:
            for _ in range(2):assert (await client.post('http://unit.invalid/mcp',content=b'UNIT RPC')).content==b'firstsecond'
        assert not transport.active and inner.closed
    asyncio.run(run());v=b.inspect()
    assert admitted==[True,True] and all(x['pending_read_bytes']==32 for x in inner.before)
    assert v['used']['source_bytes']==22 and v['used']['source_reads']==2 and v['used']['model_attempts']==0
    assert v['pending_reads']==0 and all(body.closes==1 for body in inner.bodies)


@pytest.mark.parametrize('failure',['authority','bytes','reads'])
def test_current_authority_or_shared_budget_rejection_sends_no_http_request(resource,failure):
    b=resource['budget'];inner=OriginalTransport(b)
    if failure=='bytes':b.consume(source_bytes=64)
    elif failure=='reads':b.consume(source_reads=16)
    def admit():
        if failure=='authority':raise Conflict('UNIT revoked original scope')
    async def run():
        transport=MCPSourceTransport(inner,32,b,admit)
        async with httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(Conflict):await client.post('http://unit.invalid/mcp')
    asyncio.run(run());assert inner.before==[] and b.inspect()['pending_reads']==0


def test_partial_close_and_cancellation_keep_original_reserved_bound_without_retry(resource):
    b=resource['budget'];inner=OriginalTransport(b)
    async def run():
        transport=MCPSourceTransport(inner,32,b)
        async with httpx2.AsyncClient(transport=transport) as client:
            async with client.stream('POST','http://unit.invalid/mcp') as response:
                iterator=response.aiter_raw();assert await anext(iterator)==b'first'
                await response.aclose()
            assert b.inspect()['used']['source_bytes']==32
        assert len(inner.before)==1 and not transport.active
    asyncio.run(run());assert b.inspect()['pending_reads']==0


def test_cancelled_forward_keeps_conservative_read_and_original_cancel(resource):
    b=resource['budget'];entered=asyncio.Event()
    class Waiting(httpx2.AsyncBaseTransport):
        async def handle_async_request(self,request):
            assert b.inspect()['pending_read_bytes']==32
            entered.set();await asyncio.Event().wait()
        async def aclose(self):pass
    async def run():
        transport=MCPSourceTransport(Waiting(),32,b)
        async with httpx2.AsyncClient(transport=transport) as client:
            task=asyncio.create_task(client.post('http://unit.invalid/mcp'))
            await entered.wait();task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run());v=b.inspect();assert v['used']['source_bytes']==32 and v['pending_reads']==0


def test_completed_result_survives_settlement_ack_loss_and_future_generation_stays_blocked(resource,monkeypatch):
    b=resource['budget'];original=b.finish_read
    def lost_ack(ticket,size):original(ticket,size);raise OSError('UNIT committed read receipt, ACK lost')
    monkeypatch.setattr(b,'finish_read',lost_ack);inner=OriginalTransport(b)
    async def run():
        async with httpx2.AsyncClient(transport=MCPSourceTransport(inner,32,b)) as client:
            response=await client.post('http://unit.invalid/mcp')
            assert response.content==b'firstsecond'
    asyncio.run(run());v=b.inspect()
    assert v['used']['source_bytes']==11 and v['pending_reads']==0 and v['accounting_unconfirmed']
    with pytest.raises(Conflict):b.consume(model_attempts=1)


def test_response_over_bound_does_not_get_a_free_new_read(resource):
    b=resource['budget'];inner=OriginalTransport(b,chunks=(b'x'*33,))
    async def run():
        async with httpx2.AsyncClient(transport=MCPSourceTransport(inner,32,b)) as client:
            with pytest.raises(Conflict):await client.post('http://unit.invalid/mcp')
    asyncio.run(run());v=b.inspect()
    assert len(inner.before)==1 and v['used']['source_bytes']==32 and v['pending_reads']==0


def test_overlapping_source_reads_hold_the_same_original_ledger(resource):
    b=resource['budget'];inner=OriginalTransport(b,chunks=(b'abc',))
    async def run():
        transport=MCPSourceTransport(inner,32,b)
        async with httpx2.AsyncClient(transport=transport) as client:
            request=httpx2.Request('POST','http://unit.invalid/mcp')
            first=await client.send(request,stream=True);second=await client.send(request,stream=True)
            assert b.inspect()['pending_read_bytes']==64
            with pytest.raises(Conflict):await client.send(request,stream=True)
            assert len(inner.before)==2
            assert await first.aread()==b'abc';await first.aclose()
            assert await second.aread()==b'abc';await second.aclose()
        assert not transport.active
    asyncio.run(run());assert b.inspect()['used']['source_bytes']==6


def test_one_call_has_bounded_protocol_exchanges_and_no_added_retry():
    inner=OriginalTransport(chunks=(b'{}',))
    async def run():
        transport=MCPSourceTransport(inner,32)
        async with httpx2.AsyncClient(transport=transport) as client:
            for _ in range(MAX_HTTP_EXCHANGES):await client.post('http://unit.invalid/mcp')
            with pytest.raises(Conflict):await client.post('http://unit.invalid/mcp')
        assert len(inner.before)==MAX_HTTP_EXCHANGES and inner.closed and not transport.active
    asyncio.run(run())


def test_installed_mcp_protocol_counts_original_initialize_catalog_and_tool_responses(resource,local_mcp,tmp_path,monkeypatch):
    """When run: real loopback MCP, no external model/native SDK client."""
    from packages.adapters.search_mcp import call_search,resolve_search_profile
    from packages.domain.task_resource_budget import TaskResourceBudget
    search_config(tmp_path,monkeypatch,local_mcp,limits={'max_result_bytes':4096})
    profile=resolve_search_profile()
    budget=TaskResourceBudget(resource['ws'],{**resource['authority'],'root_operation_id':'mcp_protocol_work'},
        limits={**resource['limits'],'source_bytes':4*1024*1024,'source_reads':64})
    budget.allocate();admissions=[]
    output=asyncio.run(call_search(profile,{'query':'UNIT exact source clue'},resource_budget=budget,
        admit=lambda:admissions.append(True)))
    assert output['kind']=='SEARCH_LEAD' and output['original_document_read'] is False
    assert local_mcp['calls']==[{'tool':'original_approved_search','arguments':{'q':'UNIT exact source clue'}}]
    v=budget.inspect()
    assert 0<v['used']['source_bytes']<=4*1024*1024 and 3<=v['used']['source_reads']<=MAX_HTTP_EXCHANGES
    assert v['pending_reads']==0 and v['used']['model_attempts']==0 and len(admissions)==v['used']['source_reads']
