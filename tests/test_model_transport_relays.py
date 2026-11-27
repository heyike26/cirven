"""Real relay HTTP/SSE with only the external model endpoint replaced.

No original CLI/model semantics or Windows compatibility claimed here.
"""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from packages.adapters.model_transport import open_model_transport,private_model_redactions
from packages.adapters.opencode_desktop import _DesktopRelay
from packages.adapters.opencode_planning import _PlanningRelay
from packages.adapters.provider_transport_policy import EnterpriseTransportPolicy,MaterialRef,HeaderSpec
from packages.agent_sessions.wire import ProviderWire
from packages.domain.store import Conflict


@contextmanager
def gateway():
    requests=[];raw=b'data: {"choices":[]}\n\ndata: [DONE]\n\n'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']))
            requests.append({'path':self.path,'headers':{k.lower():v for k,v in self.headers.items()},'body':json.loads(body)})
            self.send_response(200);self.send_header('Content-Type','text/event-stream')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}/v1',requests,raw
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def profile(url,mode):
    values={};key=b'qa-explicit-only-key'
    ref=MaterialRef('private-model-key',1,hashlib.sha256(key).hexdigest());values[ref]=key
    auth='API_KEY' if mode=='BEARER' else 'NONE'
    headers=(HeaderSpec('X-Corporate-Auth',secret_ref=ref),) if mode=='CUSTOM' else ()
    policy=EnterpriseTransportPolicy.admit(policy_id='actual-relay-'+mode.lower(),revision=1,
        auth_mode=auth,api_key_ref=ref if auth=='API_KEY' else None,headers=headers,
        read_secret=lambda requested:values[requested],timeout_seconds=5)
    return SimpleNamespace(provider_url=url,model_id='qa-relay-model',api_key=None if auth=='NONE' else key.decode(),
        transport_policy=policy,transport_secrets=(key.decode(),)),values,ref


@pytest.mark.parametrize('kind',['planning','desktop','conversation'])
@pytest.mark.parametrize('mode',['NONE','BEARER','CUSTOM'])
def test_all_three_actual_model_forwarders_use_selected_authentication_without_dummy_keys(kind,mode):
    with gateway() as (url,requests,raw):
        selected,_,_=profile(url,mode);completed=[]
        budget={'timeout_seconds':10,'max_calls':2,'max_output_tokens':64}
        if kind=='planning':relay=_PlanningRelay(selected,budget)
        elif kind=='desktop':relay=_DesktopRelay(selected,budget,{'StructuredOutput'})
        else:relay=ProviderWire(selected,admit=lambda sha:'receipt-'+sha[:16],complete=completed.append,max_output_tokens=64)
        relay.start()
        body={'model':selected.model_id,'stream':True,'max_tokens':64,
            'messages':[{'role':'user','content':'只读协议校验'}],
            'tools':[{'type':'function','function':{'name':'StructuredOutput','parameters':{'type':'object'}}}]}
        try:
            with httpx.Client(trust_env=False,timeout=10) as client:
                response=client.post(relay.base_url+'/chat/completions',json=body,headers={'Authorization':'Bearer '+relay.key})
            assert response.status_code==200 and response.content==raw
        finally:relay.close()
        assert len(requests)==1 and requests[0]['body']==body
        headers=requests[0]['headers']
        assert headers.get('authorization')==('Bearer qa-explicit-only-key' if mode=='BEARER' else None)
        assert headers.get('x-corporate-auth')==('qa-explicit-only-key' if mode=='CUSTOM' else None)
        assert relay.key not in repr(requests)
        if kind=='conversation':assert completed[0]['http_status']==200


@pytest.mark.parametrize('cap',[0,-1,float('nan'),float('inf'),True])
def test_invalid_or_exhausted_budget_cannot_open_a_client(cap):
    with pytest.raises(Conflict,match='DEADLINE_EXHAUSTED'):
        with open_model_transport(SimpleNamespace(),timeout_cap_seconds=cap):pass


def test_legacy_explicit_bearer_still_works_and_redactions_ignore_empty_and_none():
    with gateway() as (url,requests,_):
        old=SimpleNamespace(api_key='qa-legacy-explicit',provider_url=url)
        with open_model_transport(old,timeout_cap_seconds=2) as client:
            assert client.request('POST',url+'/chat/completions',json={'legacy':'explicit'}).status_code==200
        assert requests[0]['headers']['authorization']=='Bearer qa-legacy-explicit'
    values=private_model_redactions(SimpleNamespace(api_key=None,transport_secrets=('',None,'private-proxy','private-proxy')))
    assert values==['private-proxy']
