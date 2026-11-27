"""AUTHORED_NOT_RUN: actual loopback Anthropic meter, SQL links and ledger.

Only selected route/source-current and native client lifetime are UNIT fixtures.
No original SDK/CLI/model, account, paid provider, UI or Windows qualification.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import uuid
from types import SimpleNamespace
import httpx
import pytest
from packages.adapters import claude_http_meter as module
from packages.adapters.claude_sdk import ClaudeBridge
from packages.adapters.settings_profile import RouteProfile
from packages.adapters.task_resources import bind_created_execution
from packages.domain.task_resource_budget import TaskResourceBudget,task_resource_scope
from packages.domain.tasks import canonical,digest,now
from test_task_resource_budget import resource
from test_execution_resource_links import record


@pytest.fixture
def http_meter(resource, request):
    finalize=request.addfinalizer
    s=resource;pid=s['authority']['project_id'];authority={**s['authority'],'root_operation_id':'http-original'}
    limits={'source_bytes':2*1024*1024,'source_reads':64,'model_attempts':2,'retained_output_bytes':2*1024*1024}
    budget=TaskResourceBudget(s['ws'],authority,limits=limits);budget.allocate();s={**s,'budget':budget,'authority':authority,'limits':limits}
    received=[];entered=threading.Event();release=threading.Event();behavior={'status':200,'block':False}
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self,*_args):pass
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['Content-Length']));received.append({'path':self.path,'raw':raw,'headers':dict(self.headers)})
            assert budget.inspect()['used']['source_reads']>0
            if self.path.split('?')[0].endswith('/v1/messages'):assert budget.inspect()['used']['model_attempts']>0
            entered.set()
            if behavior['block']:release.wait(5)
            data=b'{"input_tokens":7}' if self.path.split('?')[0].endswith('/count_tokens') else b'{"type":"message","id":"msg_UNIT","role":"assistant","model":"UNIT_MODEL","content":[],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":0}}'
            try:
                self.send_response(behavior['status']);self.send_header('Content-Type','application/json');self.send_header('request-id','UNIT_REQUEST')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream);upstream.daemon_threads=True
    thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
    def close_upstream():
        release.set();upstream.shutdown();upstream.server_close();thread.join(timeout=2)
    finalize(close_upstream)
    route=RouteProfile({'ANTHROPIC_BASE_URL':'http://127.0.0.1:'+str(upstream.server_port)+'/selected-prefix','ANTHROPIC_API_KEY':'UNIT_UPSTREAM_API_KEY'},'UNIT_MODEL','1',{'UNIT_SELECTED':True})
    owner=object.__new__(ClaudeBridge);owner.service=s['store'];owner.data_dir=s['ws'].data_dir;owner.workspace_authority=s['ws'];owner.active={};owner.clients={};owner.http_relays={};owner._route_secrets={'UNIT_UPSTREAM_API_KEY'}
    with task_resource_scope(budget),s['store'].connect() as db:
        ident=str(uuid.uuid4());request={'prompt':'UNIT input','needs_tools':False,'max_turns':1,'timeout_seconds':30}
        scope={'UNIT_FROZEN_SOURCE':'exact','input_hash':digest(request),'route_hash':route.fingerprint}
        workspace=Path(s['ws'].data_dir)/'agent-workspaces'/ident;workspace.mkdir(parents=True)
        row=record(s,db,key=ident,request=request,scope=scope,runtime='claude-agent-sdk',workspace=workspace,status='RUNNING')['row']
        bind_created_execution(owner,db,row)
    owner._route=lambda:route
    def current(project,actual):
        assert project==pid and actual['request_hash']==digest(request)
        with s['store'].connect() as db:return dict(db.execute('SELECT * FROM agent_scopes WHERE execution_id=?',(ident,)).fetchone())
    owner._frozen_scope=current
    meter=module.ClaudeHTTPMeter(owner,row,request,route);client=object();context={'task_resource_budget':budget}
    def close_meter():
        release.set();meter.close(timeout_seconds=5)
    finalize(close_meter)
    owner.active[ident]=context;owner.clients[ident]=client;meter.bind(context,client);meter.start()
    value={**s,'owner':owner,'row':row,'request':request,'route':route,'meter':meter,'received':received,'behavior':behavior,'entered':entered,'release':release,'context':context}
    yield value


def send(s,body=None,*,path='/v1/messages?beta=true',headers=None,raw=None):
    h={'x-api-key':s['meter'].key,'anthropic-version':'2023-06-01','anthropic-beta':'UNIT_BETA','Content-Type':'application/json',**(headers or {})}
    raw=raw if raw is not None else canonical(body or {'model':'UNIT_MODEL','messages':[{'role':'user','content':'UNIT'}],'max_tokens':8,'stream':True}).encode()
    return httpx.post(s['meter'].base_url+path,content=raw,headers=h,timeout=5,trust_env=False)


def test_actual_generation_preserves_original_body_prefix_beta_version_and_reserved_attempt(http_meter):
    s=http_meter;raw=b' { "model":"UNIT_MODEL", "messages":[{"role":"user","content":"UNIT"}], "max_tokens":8, "stream":false }\n'
    result=send(s,raw=raw)
    assert result.status_code==200 and result.headers['request-id']=='UNIT_REQUEST'
    assert len(s['received'])==1 and s['received'][0]['raw']==raw and s['received'][0]['path']=='/selected-prefix/v1/messages?beta=true'
    headers={k.lower():v for k,v in s['received'][0]['headers'].items()};assert headers['x-api-key']=='UNIT_UPSTREAM_API_KEY' and headers['anthropic-version']=='2023-06-01' and headers['anthropic-beta']=='UNIT_BETA'
    view=s['budget'].inspect();assert view['used']['model_attempts']==1 and view['used']['source_bytes']==len(raw)
    with s['store'].connect() as db:
        events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM agent_events ORDER BY event_seq')]
    assert [e['type'] for e in events]==['claude_http_reserved','claude_http_complete'] and events[0]['request_sha256']==hashlib.sha256(raw).hexdigest()
    assert 'UNIT_UPSTREAM_API_KEY' not in canonical(events) and 'UNIT input' not in canonical(events)


def test_count_tokens_is_real_source_request_without_consuming_generation_attempt(http_meter):
    s=http_meter;raw=canonical({'model':'UNIT_MODEL','messages':[{'role':'user','content':'UNIT'}]}).encode();result=send(s,path='/v1/messages/count_tokens',raw=raw)
    assert result.status_code==200 and len(s['received'])==1
    assert s['budget'].inspect()['used']['model_attempts']==0 and s['budget'].inspect()['used']['source_bytes']==len(raw)


def test_original_sdk_retry_would_reserve_each_attempt_and_never_obtain_fresh_allowance(http_meter):
    s=http_meter;s['behavior']['status']=503
    assert send(s).status_code==503 and send(s).status_code==503
    assert send(s).status_code==403 and len(s['received'])==2 and s['budget'].inspect()['used']['model_attempts']==2


@pytest.mark.parametrize('fault',['auth','path','model','cancel','expired','context','route','link'])
def test_invalid_or_changed_native_owner_never_reaches_upstream(http_meter,fault):
    s=http_meter;params={}
    if fault=='auth':params['headers']={'x-api-key':'UNIT_OTHER_PRIVATE_KEY'}
    elif fault=='path':params['path']='/v1/messages/batches'
    elif fault=='model':params['body']={'model':'OTHER_MODEL','messages':[],'max_tokens':8}
    elif fault=='context':s['owner'].active[s['row']['id']]={}
    elif fault=='route':s['owner']._route=lambda:SimpleNamespace(fingerprint='changed')
    elif fault=='cancel':
        with s['store'].connect() as db:db.execute('UPDATE agent_executions SET cancel_requested=1 WHERE id=?',(s['row']['id'],))
    elif fault=='expired':s['meter'].deadline=time.monotonic()-1
    else:
        with s['store'].connect() as db:db.execute('UPDATE agent_execution_resources SET resource_json=? WHERE execution_id=?',('{broken original link',s['row']['id']))
    assert send(s,**params).status_code==403 and not s['received'] and s['budget'].inspect()['used']['model_attempts']==0


def test_native_gzip_body_is_forwarded_unchanged_with_bounded_decoded_accounting(http_meter):
    s=http_meter;decoded=canonical({'model':'UNIT_MODEL','messages':[{'role':'user','content':'UNIT'}],'max_tokens':8}).encode();raw=gzip.compress(decoded)
    assert send(s,raw=raw,headers={'Content-Encoding':'gzip'}).status_code==200
    assert s['received'][0]['raw']==raw and s['budget'].inspect()['used']['source_bytes']==len(raw)+len(decoded)
    assert {k.lower():v for k,v in s['received'][0]['headers'].items()}['content-encoding']=='gzip'


def test_gzip_expansion_limit_or_exhausted_source_cannot_forward_a_model_request(http_meter,monkeypatch):
    s=http_meter;monkeypatch.setattr(module,'MAX_REQUEST_BYTES',256)
    raw=gzip.compress(b'x'*1024)
    assert send(s,raw=raw,headers={'Content-Encoding':'gzip'}).status_code==403 and not s['received'] and s['budget'].inspect()['used']['model_attempts']==0
    remaining=s['budget'].remaining_source_bytes();s['budget'].consume(source_bytes=remaining)
    assert send(s).status_code==403 and not s['received']


def test_private_native_environment_does_not_disclose_selected_upstream_credential(http_meter):
    s=http_meter;env=s['meter'].environment({'ANTHROPIC_API_KEY':'UNIT_UPSTREAM_API_KEY','UNIT_UNCHANGED':'value'})
    assert env['ANTHROPIC_API_KEY']==s['meter'].key and env['ANTHROPIC_BASE_URL']==s['meter'].base_url and env['UNIT_UNCHANGED']=='value'
    assert 'UNIT_UPSTREAM_API_KEY' not in canonical(env) and env['ANTHROPIC_AUTH_TOKEN']==''


def test_completion_metadata_failure_preserves_receipt_blocks_generation_and_keeps_original_http_response(http_meter,monkeypatch):
    s=http_meter
    def fail(*_args,**_kwargs):raise OSError('UNIT completion receipt store unavailable')
    monkeypatch.setattr(s['owner'],'_event',fail)
    assert send(s).status_code==200
    # Response EOF is received only after the original handler completion path.
    deadline=time.monotonic()+2
    while not s['meter'].recovery_archives and time.monotonic()<deadline:time.sleep(.01)
    assert s['meter'].closed and len(s['meter'].unconfirmed_completions)==1 and s['meter'].recovery_archives
    descriptor=next(iter(s['meter'].recovery_archives.values()));path=Path(s['owner'].data_dir)/descriptor['path'];outer=json.loads(path.read_bytes())
    assert outer['value']['observations'][0]['upstream_attempted'] and not outer['value']['automatic_replay_allowed']
    assert s['budget'].inspect()['accounting_unconfirmed'] and send(s).status_code==403 and len(s['received'])==1


def test_owned_relay_close_cancels_header_wait_without_replaying_or_refunding_unknown_attempt(http_meter):
    s=http_meter;s['behavior']['block']=True;answers=[]
    def request():
        try:answers.append(send(s))
        except httpx.TransportError:answers.append('closed locally')
    worker=threading.Thread(target=request);worker.start();assert s['entered'].wait(3)
    s['meter'].close(timeout_seconds=2);worker.join(timeout=3)
    assert not worker.is_alive() and s['meter'].close_confirmed and s['budget'].inspect()['used']['model_attempts']==1 and len(s['received'])==1
    assert s['meter'].generation_attempted and s['meter'].closed


@pytest.mark.parametrize('path',['https://other.invalid/v1/messages','/v1/messages?beta=true&beta=true','/v1/messages?api_key=UNIT','/v1/messages/../messages','/v1/files'])
def test_only_exact_native_message_or_count_route_is_admitted(path):
    from packages.domain.store import Conflict
    with pytest.raises(Conflict):module.target(path)
