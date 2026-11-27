"""Actual Anthropic HTTP admission for the original Claude SDK native loop.

No new model iteration, route selection, retry, credential discovery or protocol
translation. The private native endpoint sees a per-instance key; selected
upstream credentials stay inside this controller. Local close never proves
remote cancellation. Counters are software payload/attempt units, not money.
"""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler
import secrets
import threading
import time
import zlib
from urllib.parse import parse_qsl,urlsplit

import httpx
from packages.adapters.model_relay_lifecycle import ModelRelayLifecycle
from packages.adapters.task_resources import resolve,require_confirmed,retain_execution_output
from packages.domain.tasks import canonical,digest,now
from packages.domain.store import Conflict

MAX_REQUEST_BYTES=128*1024*1024
MAX_RESERVATIONS=256
MAX_CONCURRENT_READS=4
REQUEST_HEADERS=frozenset({'content-type','content-encoding','anthropic-version','anthropic-beta','anthropic-workspace-id',
    'anthropic-user-profile-id','anthropic-organization-id'})
RESPONSE_HEADERS=frozenset({'content-type','content-encoding','request-id','retry-after','anthropic-version','anthropic-beta'})


def _header(value):
    if not isinstance(value,str) or len(value)>8192 or any(ord(c)<32 or ord(c)>126 for c in value):
        raise Conflict('CLAUDE_HTTP_HEADER_INVALID')
    return value


def target(path):
    value=urlsplit(path)
    if (value.scheme or value.netloc or value.fragment or len(path)>512
            or value.path not in {'/v1/messages','/v1/messages/count_tokens'}
            or parse_qsl(value.query,keep_blank_values=True) not in ([],[('beta','true')],[('beta','false')])):
        raise Conflict('CLAUDE_HTTP_ROUTE_DENIED')
    return value.path=='/v1/messages'


class ClaudeHTTPMeter:
    def __init__(self,adapter,row,request,route):
        self.adapter=adapter;self.row=deepcopy(row);self.request=deepcopy(request);self.route=route
        self.pid,self.ident=row['project_id'],row['id'];self.context=None;self.client=None
        if row['runtime']!='claude-agent-sdk' or row['request_hash']!=digest(request) or not route.model_id or not route.secrets:
            raise Conflict('CLAUDE_HTTP_EXACT_NATIVE_EXECUTION_REQUIRED')
        self.budget=resolve(adapter,self.pid,row,None)
        self.key=secrets.token_hex(32);self._lock=threading.RLock();self.closed=False;self.reservations=0;self.generation_attempted=False;self.unconfirmed_completions=[];self.recovery_archives={};self.last_archived_count=0
        self.deadline=time.monotonic()+request['timeout_seconds'];self.readers=threading.BoundedSemaphore(MAX_CONCURRENT_READS)
        self.models=frozenset({route.model_id,*[v for k,v in route.environment.items() if k.startswith('ANTHROPIC_DEFAULT_') and k.endswith('_MODEL') and v]})
        self.endpoint=route.environment.get('ANTHROPIC_BASE_URL') or 'https://api.anthropic.com'
        selected=urlsplit(self.endpoint)
        if selected.scheme not in {'http','https'} or not selected.hostname or selected.username or selected.password or selected.query or selected.fragment:
            raise Conflict('CLAUDE_HTTP_SELECTED_ENDPOINT_INVALID')
        self.endpoint=self.endpoint.rstrip('/')
        owner=self

        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*_args):pass
            def reply(self,status,code):
                raw=canonical({'type':'error','error':{'type':'invalid_request_error','message':code}}).encode()
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                self.send_header('Connection','close');self.end_headers();self.wfile.write(raw);self.close_connection=True
            def do_GET(self):self.reply(403,'CLAUDE_HTTP_ROUTE_DENIED')
            def do_POST(self):
                receipt=None;observation=None
                if not owner.readers.acquire(blocking=False):self.reply(429,'CLAUDE_HTTP_READ_CAPACITY');return
                try:
                    generating=target(self.path)
                    upstream_headers=owner.headers(self.headers)
                    lengths=self.headers.get_all('Content-Length') or []
                    if (len(lengths)!=1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding')
                            or self.headers.get('Content-Encoding') not in {None,'identity','gzip'}):
                        raise Conflict('CLAUDE_HTTP_FRAMING_DENIED')
                    size=int(lengths[0])
                    if not 0<size<=MAX_REQUEST_BYTES:raise Conflict('CLAUDE_HTTP_BODY_LIMIT')
                    ticket=owner.reserve_source(size)
                    chunks=[];received=0
                    try:
                        while received<size:
                            remaining=owner.deadline-time.monotonic()
                            if remaining<=0:raise TimeoutError('CLAUDE_HTTP_DEADLINE')
                            self.connection.settimeout(min(10,remaining))
                            chunk=self.rfile.read(min(65536,size-received))
                            if not chunk:raise ConnectionError('CLAUDE_HTTP_BODY_INCOMPLETE')
                            chunks.append(chunk);received+=len(chunk)
                    except BaseException:
                        try:owner.budget.finish_read(ticket,None)
                        except Exception:pass  # Original durable reservation remains held.
                        raise
                    raw=b''.join(chunks);owner.budget.finish_read(ticket,len(raw))
                    from packages.domain.native_capabilities import _json
                    decoded=owner.decode(raw,self.headers.get('Content-Encoding'))
                    body=_json(decoded)
                    if (not isinstance(body,dict) or body.get('model') not in owner.models
                            or not isinstance(body.get('messages'),list)):
                        raise Conflict('CLAUDE_HTTP_FROZEN_MODEL_REQUIRED')
                    if generating and (type(body.get('max_tokens')) is not int or not 1<=body['max_tokens']<2**31):
                        raise Conflict('CLAUDE_HTTP_OUTPUT_LIMIT_INVALID')
                    receipt=owner.admit(generating,raw,self.path)
                    observation={'type':'claude_http_complete','reservation_id':receipt,'request_sha256':hashlib.sha256(raw).hexdigest(),
                        'generation_request':generating,'upstream_attempted':False,'remote_outcome':'UNKNOWN'}
                    owner._io.run(self.forward,raw,upstream_headers,observation)
                except asyncio.CancelledError:
                    if observation is not None:observation.update(local_cancel_requested=True,error_type='UPSTREAM_CANCELLED_LOCAL_CLOSE')
                    self.close_connection=True
                except Exception as error:
                    if observation is None:self.reply(403,'CLAUDE_HTTP_SCOPE_OR_BUDGET_DENIED')
                    else:observation['error_type']=type(error).__name__;self.close_connection=True
                finally:
                    try:
                        if observation is not None:owner.complete(observation)
                    finally:owner.readers.release()
            async def forward(self,raw,headers,observation):
                remaining=owner.deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('CLAUDE_HTTP_DEADLINE')
                # The native SDK remains the only retry owner. This transport
                # does not follow redirects, consult ambient proxies or replay.
                async with httpx.AsyncClient(timeout=remaining,trust_env=False,follow_redirects=False) as client:
                    with owner._lock:
                        if owner.closed:raise Conflict('CLAUDE_HTTP_SEALED')
                        observation['upstream_attempted']=True
                        if observation['generation_request']:owner.generation_attempted=True
                    async with client.stream('POST',owner.endpoint+self.path,content=raw,headers=headers) as response:
                        observation['http_status']=response.status_code
                        if 300<=response.status_code<400:self.reply(502,'CLAUDE_HTTP_REDIRECT_DENIED');return
                        self.send_response(response.status_code)
                        for name,value in response.headers.items():
                            if name in RESPONSE_HEADERS or name.startswith('anthropic-ratelimit-'):
                                self.send_header(name,_header(value))
                        self.send_header('Connection','close');self.end_headers();self.close_connection=True
                        sha=hashlib.sha256();count=0
                        iterator=response.aiter_raw(chunk_size=65536).__aiter__()
                        while True:
                            remaining=owner.deadline-time.monotonic()
                            if remaining<=0:raise TimeoutError('CLAUDE_HTTP_DEADLINE')
                            try:chunk=await asyncio.wait_for(iterator.__anext__(),remaining)
                            except StopAsyncIteration:break
                            sha.update(chunk);count+=len(chunk)
                            observation.update(response_bytes=count,response_prefix_sha256=sha.hexdigest())
                            self.wfile.write(chunk);self.wfile.flush()
                        observation.update(response_complete=True,response_sha256=sha.hexdigest(),remote_outcome='HTTP_RESPONSE_COMPLETE')
        self._io=ModelRelayLifecycle(Handler,lock=self._lock)

    @property
    def base_url(self):return 'http://127.0.0.1:'+str(self._io.server.server_port)

    def environment(self,original):
        return {**original,'ANTHROPIC_BASE_URL':self.base_url,
            'ANTHROPIC_API_KEY':self.key if self.route.environment.get('ANTHROPIC_API_KEY') else '',
            'ANTHROPIC_AUTH_TOKEN':self.key if self.route.environment.get('ANTHROPIC_AUTH_TOKEN') else ''}

    def decode(self,raw,encoding):
        if encoding!='gzip':return raw
        maximum=min(MAX_REQUEST_BYTES,self.budget.remaining_source_bytes())
        ticket=self.budget.reserve_read(maximum)
        try:
            decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
            decoded=decoder.decompress(raw,maximum+1)
            if len(decoded)>maximum or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise Conflict('CLAUDE_HTTP_COMPRESSED_BODY_LIMIT')
        except BaseException:
            try:self.budget.finish_read(ticket,None)
            except Exception:pass
            raise
        self.budget.finish_read(ticket,len(decoded))
        return decoded

    def headers(self,incoming):
        if any(len(incoming.get_all(name) or [])>1 for name in ['Authorization','x-api-key']):
            raise Conflict('CLAUDE_HTTP_PRIVATE_AUTH_REQUIRED')
        result={name:_header(value) for name,value in incoming.items() if name.lower() in REQUEST_HEADERS}
        authenticated=False
        api=incoming.get('x-api-key');auth=incoming.get('authorization')
        if api is not None:
            selected=self.route.environment.get('ANTHROPIC_API_KEY')
            if not selected or not secrets.compare_digest(api,self.key):raise Conflict('CLAUDE_HTTP_PRIVATE_AUTH_REQUIRED')
            result['x-api-key']=selected;authenticated=True
        if auth is not None:
            selected=self.route.environment.get('ANTHROPIC_AUTH_TOKEN') or self.route.environment.get('ANTHROPIC_API_KEY')
            if not selected or not secrets.compare_digest(auth,'Bearer '+self.key):raise Conflict('CLAUDE_HTTP_PRIVATE_AUTH_REQUIRED')
            result['Authorization']='Bearer '+selected;authenticated=True
        if not authenticated:raise Conflict('CLAUDE_HTTP_PRIVATE_AUTH_REQUIRED')
        result['Accept-Encoding']='identity'
        return result

    def bind(self,context,client):
        if self.context is not None or self.adapter.active.get(self.ident) is not context:
            raise Conflict('CLAUDE_HTTP_ORIGINAL_CLIENT_REQUIRED')
        self.context,self.client=context,client

    def _current(self,db):
        current=db.execute('SELECT * FROM agent_executions WHERE id=? AND project_id=?',(self.ident,self.pid)).fetchone()
        context=self.context
        if (self.closed or self._io.closed or time.monotonic()>=self.deadline or context is None
                or self.adapter.active.get(self.ident) is not context or self.adapter.clients.get(self.ident) is not self.client
                or current is None or current['execution_status']!='RUNNING' or current['cancel_requested']
                or current['request_hash']!=self.row['request_hash'] or current['runtime']!=self.row['runtime']):
            raise Conflict('CLAUDE_HTTP_ORIGINAL_OWNER_UNAVAILABLE')
        frozen=self.adapter._frozen_scope(self.pid,dict(current))
        if self.adapter._route().fingerprint!=self.route.fingerprint:
            raise Conflict('CLAUDE_HTTP_FROZEN_ROUTE_CHANGED')
        if datetime.now(timezone.utc)>=datetime.fromisoformat(frozen['expires_at']):
            raise Conflict('CLAUDE_HTTP_OWNER_EXPIRED')
        resolved=resolve(self.adapter,self.pid,dict(current),None)
        if resolved.id!=self.budget.id or resolved.path!=self.budget.path or resolved.limits!=self.budget.limits:
            raise Conflict('CLAUDE_HTTP_RESOURCE_LINK_CHANGED')
        require_confirmed(db,self.pid,current,self.budget)
        active=context.get('task_resource_budget')
        if active is None or active.id!=self.budget.id or active.path!=self.budget.path or active.accounting_unconfirmed:
            raise Conflict('CLAUDE_HTTP_ORIGINAL_RESOURCE_UNAVAILABLE')
        return current

    def reserve_source(self,size):
        with self.adapter.service.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._current(db)
            return self.budget.reserve_read(size)

    def admit(self,generating,raw,path):
        with self._lock:
            if self.closed or self.reservations>=MAX_RESERVATIONS:raise Conflict('CLAUDE_HTTP_RESERVATION_CAPACITY')
            self.reservations+=1;receipt=self.reservations
        saved=False
        with self.adapter.service.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._current(db)
            if generating:self.budget.consume(model_attempts=1)
            value={'type':'claude_http_reserved','reservation_id':receipt,'request_sha256':hashlib.sha256(raw).hexdigest(),
                'request_bytes':len(raw),'path':path,'generation_request':generating,'model_attempt_reserved':generating,
                'automatic_replay_allowed':False,'upstream_attempted':False}
            encoded=canonical(value)
            saved=retain_execution_output(self.adapter,db,self.ident,len(encoded.encode()))
            seq=db.execute('SELECT coalesce(max(event_seq),0)+1 FROM agent_events WHERE execution_id=?',(self.ident,)).fetchone()[0]
            db.execute('INSERT INTO agent_events VALUES (?,?,?,?)',(self.ident,seq,encoded,now()))
        if not saved:raise Conflict('CLAUDE_HTTP_RESERVATION_ACCOUNTING_UNCONFIRMED')
        return receipt

    def complete(self,value):
        try:self.adapter._event(self.ident,value)
        except Exception as error:
            with self._lock:
                self.closed=True
                self.unconfirmed_completions.append(deepcopy(value))
            self.budget.mark_unconfirmed(type(error).__name__,original_evidence_retained=False)
            active=(self.context or {}).get('task_resource_budget')
            if active is not None:active.accounting_unconfirmed=True
            try:self.persist_unconfirmed()
            except Exception:pass  # Keep the original observation in this owned relay.
            raise

    def persist_unconfirmed(self):
        with self._lock:
            values=deepcopy(self.unconfirmed_completions)
        if not values:return True
        from packages.domain.native_capabilities import NativeCapabilities,_json
        from packages.domain.paths import resolve_data_path
        from packages.adapters.opencode_native_review import regular
        record={'schema':'eeas.claude-http-unconfirmed.v1','execution_id':self.ident,'request_sha256':self.row['request_hash'],
            'observations':values,'automatic_replay_allowed':False}
        fingerprint=digest(record)
        workspace=resolve_data_path(self.row['workspace'],self.adapter.data_dir)
        path=workspace/'.eeas-sdk-state'/'claude-http-unconfirmed'/(fingerprint+'.json')
        if not path.exists():NativeCapabilities._write(path,record)
        observed=_json(regular(path,MAX_RESERVATIONS*4096,private=True))
        if observed!={'value':record,'sha256':fingerprint}:raise Conflict('CLAUDE_HTTP_RECOVERY_RECEIPT_CHANGED')
        if fingerprint not in self.recovery_archives:
            size=path.stat().st_size
            confirmed=self.budget.retain_received(size) is not None
            self.recovery_archives[fingerprint]={'path':str(path.relative_to(Path(self.adapter.data_dir).absolute())),
                'sha256':fingerprint,'bytes':size,'counter_charge_observed':confirmed}
        self.last_archived_count=max(self.last_archived_count,len(values))
        return True

    def seal(self):
        with self._lock:self.closed=True

    def start(self):self._io.start();return self

    @property
    def close_confirmed(self):return self._io.close_confirmed

    def close(self,*,timeout_seconds=2):
        self._io.validate_timeout(timeout_seconds)
        self.seal()
        self._io.close(timeout_seconds=timeout_seconds)
