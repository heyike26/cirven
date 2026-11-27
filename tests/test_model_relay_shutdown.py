"""Adjacent planning/desktop routes must drain the same real blocked sockets."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from packages.adapters.opencode_desktop import _DesktopRelay
from packages.adapters.opencode_planning import _PlanningRelay
from packages.domain.tasks import canonical
from test_provider_wire_shutdown import Gateway


def make(kind,gateway):
    profile=SimpleNamespace(model_id='wire-boundary-fixture',api_key='private-fixture-not-user-key',
        provider_url=f'http://127.0.0.1:{gateway.server.server_port}/v1',transport_policy=None)
    budget={'timeout_seconds':20,'max_output_tokens':100,'max_calls':2}
    return (_PlanningRelay(profile,budget) if kind=='planning' else _DesktopRelay(profile,budget,{'StructuredOutput'})).start()


def request(relay):
    parsed=urlsplit(relay.base_url);client=http.client.HTTPConnection(parsed.hostname,parsed.port,timeout=5)
    body={'model':'wire-boundary-fixture','stream':True,'max_tokens':10,'messages':[],
        'tools':[{'type':'function','function':{'name':'StructuredOutput','parameters':{'type':'object'}}}]}
    try:
        client.request('POST','/v1/chat/completions',body=canonical(body),headers={'Authorization':'Bearer '+relay.key})
        response=client.getresponse();return response.status,response.read()
    except (OSError,http.client.HTTPException):return None
    finally:client.close()


@pytest.mark.parametrize('kind',['planning','desktop'])
@pytest.mark.parametrize('phase',['headers','stream'])
def test_adjacent_relay_close_cancels_exact_upstream_keeps_remote_unknown_and_reaps_threads(kind,phase):
    gateway=Gateway();gateway.midstream=phase=='stream';gateway.release.clear();relay=make(kind,gateway)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(request,relay)
        try:
            assert gateway.entered.wait(3)
            threads,tasks=relay.owned_threads,relay.owned_tasks;assert len(tasks)==1
            start=time.monotonic();relay.close();assert time.monotonic()-start<2
            assert not gateway.release.is_set() and relay.close_confirmed
            assert not any(t.is_alive() for t in threads+relay.owned_threads)
            assert not relay.owned_tasks and all(task.done() for task in tasks)
            assert len(relay.records)==len(gateway.requests)==relay.calls==1
            record=relay.records[0];assert record['remote_outcome']=='UNKNOWN' and record['local_cancel_requested']
            assert 'response_sha256' not in record
            pending.result(timeout=2);relay.close()
            assert relay.calls==1
        finally:
            gateway.release.set();relay.close();gateway.close()


def test_cancelling_one_task_route_does_not_stop_another_route_or_duplicate_the_request():
    first,second=Gateway(),Gateway();first.release.clear();one=make('planning',first);two=make('desktop',second)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(request,one)
        try:
            assert first.entered.wait(3);one.close();pending.result(timeout=2)
            response=request(two);assert response==(200,second.data)
            assert len(first.requests)==len(second.requests)==1 and not two.closed
        finally:
            first.release.set();one.close();two.close();first.close();second.close()
