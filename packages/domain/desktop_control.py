"""User/Agent desktop ownership around the existing native dispatch guard.

Default USER. Importing Cua is not a qualified input route. This controller adds
no model loop, does not affect explicitly authorized single-file CAS edits, and
never cancels an unrelated execution or terminates a user's application.
"""
from __future__ import annotations
from contextlib import contextmanager
import copy
import hashlib
from datetime import datetime,timezone
import inspect
import json
import re
import time
import threading
import uuid

from pydantic import Field,model_validator
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import digest,now
from packages.domain.questions import RequestModel,Id,Hash
from packages.domain.store import Conflict,InvalidInput,NotFound


class DesktopExecutionRef(RequestModel):
    project_id: Id
    execution_id: Id
    request_sha256: Hash
    task_ref: RevisionRef | None=None


class DesktopDelegateInput(RequestModel):
    operation_id: Id
    surface_ref: RevisionRef
    expected_input_epoch: int=Field(strict=True,ge=0)
    expected_control_epoch: int=Field(strict=True,ge=1)
    execution_ref: DesktopExecutionRef
    user_requested_delegation: bool=Field(strict=True)
    lease_seconds: int=Field(default=300,strict=True,ge=1,le=3600)

    @model_validator(mode='after')
    def requested(self):
        if self.user_requested_delegation is not True:
            raise ValueError('An explicit desktop-main user delegation is required')
        return self


class DesktopTakeoverInput(RequestModel):
    operation_id: Id
    expected_control_epoch: int=Field(strict=True,ge=1)
    surface_ref: RevisionRef | None=None
    expected_input_epoch: int | None=Field(default=None,strict=True,ge=0)

    @model_validator(mode='after')
    def scope_pair(self):
        if (self.surface_ref is None)!=(self.expected_input_epoch is None):
            raise ValueError('An optional desktop scope must include both surface ref and input epoch')
        return self


class DesktopControl:
    def __init__(self,workspace,*,transport=None,capability_reader=None,source_checker=None,queue_revoker=None):
        self.ws,self.transport=workspace,transport
        self.capability_reader,self.source_checker,self.queue_revoker=capability_reader,source_checker,queue_revoker
        self.lock=workspace.lock
        self._takeover_intent=threading.Event()
        self.generation=uuid.uuid4().hex+uuid.uuid4().hex
        with self.lock:
            prior=self._read_head()
            prior_lease=(prior or {}).get('lease') or {}
            prior_cancel=(prior or {}).get('cancellation') or {}
            revoked=prior_lease.get('execution_ref') or (prior or {}).get('revoked_execution_ref')
            if not revoked and prior_cancel.get('confirmed') is not True:revoked=prior_cancel.get('execution_ref')
            revoked_scope=prior_lease.get('execution_scope') or (prior or {}).get('revoked_execution_scope')
            self.state={'id':prior['id'] if prior else uuid.uuid4().hex+uuid.uuid4().hex,
                'epoch':prior['epoch']+1 if prior else 1,'owner':'USER','lease':None,
                'generation':self.generation,'cancellation':self._no_cancel(),
                'revoked_execution_ref':copy.deepcopy(revoked),'revoked_execution_scope':copy.deepcopy(revoked_scope)}
            if revoked:
                self.state['cancellation']={'status':'UNKNOWN_OUTCOME','confirmed':False,'execution_ref':copy.deepcopy(revoked),
                    'queued_inputs':{'status':'OLD_CONTROLLER_AUTHORITY_REVOKED_NO_REPLAY','discarded_count':None},
                    'reason':'Controller restarted; old execution cancellation was not adopted or resent'}
            self._save('CONTROLLER_RESTART_USER_AUTHORITY' if prior else 'DEFAULT_USER_AUTHORITY')

    @staticmethod
    def _no_cancel():
        return {'status':'NOT_NEEDED','confirmed':True,'execution_ref':None,
                'queued_inputs':{'status':'NO_QUALIFIED_NATIVE_INPUT_QUEUE','discarded_count':0}}

    def _path(self,*parts):return self.ws._path('control',*parts)

    def _read_head(self):
        path=self._path('head.json')
        if not path.exists():return None
        # A different main lifetime must not acquire the preceding authority.
        try:
            head=self.ws._read(path,authority=True)
        except Conflict:
            wrapper=json.loads(path.read_bytes())
            if wrapper.get('host_token_sha256')==hashlib.sha256(self.ws.token.encode()).hexdigest():raise
            evidence=self.ws._read(path)
            if not re.fullmatch('[0-9a-f]{32}\\.json',evidence.get('observation','')):
                raise Conflict('Invalid prior desktop control observation')
            old=self.ws._read(self._path('observations',evidence['observation']))
            if digest(old)!=evidence['sha256']:raise Conflict('Desktop control history changed')
            return {'id':old['id'],'epoch':old['epoch'],'lease':None}
        name=head.get('observation','')
        if not isinstance(name,str) or not re.fullmatch('[0-9a-f]{32}\\.json',name):
            raise Conflict('Invalid desktop control observation')
        value=self.ws._read(self._path('observations',name),authority=True)
        if digest(value)!=head['sha256']:raise Conflict('Desktop control observation changed')
        return value

    def _save(self,kind):
        value={**copy.deepcopy(self.state),'kind':kind,'created_at':now()}
        name=uuid.uuid4().hex+'.json'
        self.ws._write(self._path('observations',name),value)
        self.ws._write(self._path('head.json'),{'observation':name,'sha256':digest(value)})

    def _assert_authority(self):
        current=self._read_head()
        if not current or current.get('generation')!=self.generation:
            raise Conflict('Desktop control controller lifetime was replaced')

    def _operation(self,operation_id,kind,body):
        path=self._path('operations',operation_id+'.json')
        request={'kind':kind,'input':body};request_sha=digest(request)
        if path.exists():
            value=self.ws._read(path,authority=True)
            if value['request_sha256']!=request_sha or value['request']!=request:
                raise Conflict('Desktop control operation id was reused for another request')
            final=self._path('operation-outcomes',operation_id+'.json')
            if final.exists():
                outcome=self.ws._read(final,authority=True)
                if outcome['request_sha256']!=request_sha or outcome['request']!=request:
                    raise Conflict('Desktop control outcome scope changed')
                value=outcome
            return value,path,request,request_sha
        return None,path,request,request_sha

    def _finish(self,path,request,request_sha,result):
        value={'request':request,'request_sha256':request_sha,'result':result,'created_at':now()}
        self.ws._write(path,value)
        return self._response(value,False)

    def _response(self,record,replayed):
        current=self.get();saved=record['result']
        return {**current,'operation':copy.deepcopy(saved),'replayed':replayed,
                'current':saved.get('control_ref')==current['control_ref'],
                'delegated':saved.get('delegated',False) and saved.get('control_ref')==current['control_ref']
                    and current['owner']=='AGENT'}

    def _capability(self):
        try:value=self.capability_reader() if callable(self.capability_reader) else {}
        except Exception:value={}
        qualified=(isinstance(value,dict) and value.get('desktop_input_routes_qualified') is True
            and value.get('owned_queue_drain_qualified') is True and callable(self.queue_revoker)
            and callable(self.source_checker) and callable(getattr(self.transport,'inspect',None))
            and callable(getattr(self.transport,'request_cancel',None)))
        return {'desktop_input_routes_qualified':qualified,'owned_queue_drain_qualified':qualified,
                'qualification':'QUALIFIED_REGISTERED_NATIVE_INPUT_ROUTES' if qualified else 'DESKTOP_CUA_ROUTES_NOT_QUALIFIED',
                'direct_file_cas_requires_desktop_lease':False}

    def _execution(self,ref):
        if not callable(getattr(self.transport,'inspect',None)):raise Conflict('Original execution authority is unavailable')
        row=self.transport.inspect(ref['project_id'],ref['execution_id'])
        if (row.get('id')!=ref['execution_id'] or row.get('project_id')!=ref['project_id']
                or row.get('request_hash')!=ref['request_sha256']):raise Conflict('Exact desktop execution source changed')
        try:
            request=json.loads(row['request']) if isinstance(row['request'],str) else row['request']
            if not isinstance(request,dict) or digest(request)!=ref['request_sha256']:raise ValueError
        except (KeyError,ValueError,TypeError):raise Conflict('Original desktop execution request integrity failed') from None
        return row,request

    @staticmethod
    def _layout_current(value):
        return (value.get('status')=='ACTIVE' and value.get('region_verified') is True
                and value.get('visible_all_verified') is True and not value.get('prepare_pending')
                and value.get('presentation')!='FULL_WORKSPACE' and value.get('input',{}).get('enabled') is True)

    def _lease_current(self,lease,desktop,*,sources=True):
        if (self._takeover_intent.is_set() or not lease or lease['generation']!=self.generation or lease['expires_unix_ms']<=int(time.time()*1000)
                or lease['surface_ref']!=desktop.get('surface_ref') or lease['input_epoch']!=desktop.get('input',{}).get('epoch')
                or not self._layout_current(desktop) or not self._capability()['desktop_input_routes_qualified']):return False
        if sources:
            try:
                row,request=self._execution(lease['execution_ref'])
                if row['execution_status'] not in {'QUEUED','RUNNING'} or request.get('desktop_scope')!=lease['execution_scope']:
                    return False
                return self.source_checker(row,lease['execution_scope']) is True
            except (Conflict,InvalidInput,NotFound,ValueError,KeyError,TypeError,OSError):return False
        return True

    def get(self):
        with self.lock:
            desktop=self.ws.get();cap=self._capability();lease=self.state.get('lease')
            current=self._lease_current(lease,desktop) if lease else False
            owner='AGENT' if self.state['owner']=='AGENT' and current else 'USER'
            ref={'id':self.state['id'],'revision':self.state['epoch'],
                 'sha256':digest({k:self.state[k] for k in ['id','epoch','owner','generation','lease','cancellation']})}
            source_current=self._layout_current(desktop) and (current if lease else True)
            cancellation=self.state['cancellation']
            can_takeover=bool(lease or self.state.get('revoked_execution_ref') or
                (cancellation['status'] in {'REQUESTED','UNKNOWN_OUTCOME','FAILED'} and
                 (cancellation.get('execution_ref') or cancellation.get('queued_inputs',{}).get('status')=='UNKNOWN_OUTCOME')))
            return {'status':'AGENT_CONTROL' if owner=='AGENT' else 'USER_CONTROL','owner':owner,
                'control_epoch':self.state['epoch'],'control_ref':ref,'surface_ref':desktop.get('surface_ref'),
                'session_ref':desktop.get('session_ref'),'input_epoch':desktop.get('input',{}).get('epoch'),
                'lease_ref':copy.deepcopy(lease['lease_ref']) if current else None,
                'execution_ref':copy.deepcopy(lease['execution_ref']) if current else None,
                'source_current':source_current,'source_scope':'NATIVE_LAYOUT_AND_EXACT_EXECUTION_SOURCES_NOT_VERIFIED_SCREEN_CONTENT',
                'queued_actions_enabled':owner=='AGENT' and current,'can_takeover':can_takeover,'prepare_pending':bool(desktop.get('prepare_pending')),
                'full_workspace':desktop.get('presentation')=='FULL_WORKSPACE',
                'capabilities':{**cap,'delegation_ready':cap['desktop_input_routes_qualified'] and source_current
                    and self.state['cancellation']['status'] not in {'REQUESTED','UNKNOWN_OUTCOME','FAILED'},
                    'reason':None if cap['desktop_input_routes_qualified'] else 'DESKTOP_CUA_ROUTES_NOT_QUALIFIED'},
                'cancellation':copy.deepcopy(self.state['cancellation']),
                'lease_state':'CURRENT' if current else 'REVOKED_OR_STALE' if lease else 'NONE',
                'model_calls':0,'scene_effect':'NO_USER_WINDOW_MUTATION_OR_APP_KILL'}

    def delegate(self,request):
        body=DesktopDelegateInput.model_validate(request).model_dump()
        with self.lock:
            self._assert_authority()
            existing,path,claim,request_sha=self._operation(body['operation_id'],'DELEGATE',body)
            if existing:return self._response(existing,True)
            if body['expected_control_epoch']!=self.state['epoch']:raise Conflict('Stale desktop control epoch')
            desktop=self.ws.get()
            if body['surface_ref']!=desktop.get('surface_ref') or body['expected_input_epoch']!=desktop.get('input',{}).get('epoch'):
                raise Conflict('Stale desktop surface or input epoch for delegation')
            cap=self._capability()
            if not cap['desktop_input_routes_qualified'] or not self._layout_current(desktop):
                return self._finish(path,claim,request_sha,{'status':'DENIED','reason':'DESKTOP_INPUT_ROUTES_OR_CURRENT_LAYOUT_UNQUALIFIED',
                    'delegated':False,'control_ref':self.get()['control_ref']})
            if self.state.get('lease') or self.state['cancellation']['status'] in {'REQUESTED','UNKNOWN_OUTCOME','FAILED'}:
                raise Conflict('Previous desktop authority must be explicitly taken over and cancellation resolved')
            row,original=self._execution(body['execution_ref'])
            scope={'surface_ref':body['surface_ref'],'input_epoch':body['expected_input_epoch'],
                   'task_ref':body['execution_ref'].get('task_ref')}
            if row['execution_status'] not in {'QUEUED','RUNNING'} or original.get('desktop_scope')!=scope:
                raise Conflict('Execution is not an exact authorized desktop task')
            if self.source_checker(row,scope) is not True:raise Conflict('Desktop execution sources are stale or unverified')
            self.state['epoch']+=1;identifier=uuid.uuid4().hex+uuid.uuid4().hex
            lease={'id':identifier,'generation':self.generation,'surface_ref':body['surface_ref'],
                   'input_epoch':body['expected_input_epoch'],'control_epoch':self.state['epoch'],
                   'execution_ref':body['execution_ref'],'execution_scope':scope,
                   'expires_unix_ms':int(time.time()*1000)+body['lease_seconds']*1000}
            lease['lease_ref']={'id':identifier,'revision':1,'sha256':digest(lease)}
            self.state['owner']='AGENT';self.state['lease']=lease;self.state['cancellation']=self._no_cancel()
            self._takeover_intent.clear()
            self._save('EXPLICIT_USER_DELEGATED_EXACT_DESKTOP_SCOPE')
            return self._finish(path,claim,request_sha,{'status':'DELEGATED','delegated':True,'control_ref':self.get()['control_ref'],
                'lease_ref':copy.deepcopy(lease['lease_ref']),'execution_ref':body['execution_ref']})

    async def takeover(self,request):
        body=DesktopTakeoverInput.model_validate(request).model_dump()
        # Permission-reducing latch precedes waiting for an already admitted
        # SDK action. A dedicated worker cannot start its next queued mutation
        # by reacquiring the RLock before this trusted-main request drains it.
        if body['expected_control_epoch']==self.state['epoch']:
            self._takeover_intent.set()
        with self.lock:
            self._assert_authority()
            existing,path,claim,request_sha=self._operation(body['operation_id'],'TAKEOVER',body)
            if existing:return self._response(existing,True)
            if body['expected_control_epoch']!=self.state['epoch']:raise Conflict('Stale desktop control epoch')
            desktop=self.ws.get()
            if body['surface_ref'] is not None and (body['surface_ref']!=desktop.get('surface_ref') or
                    body['expected_input_epoch']!=desktop.get('input',{}).get('epoch')):
                raise Conflict('Stale takeover desktop scope')
            lease=self.state.get('lease');execution=(lease or {}).get('execution_ref') or self.state.get('revoked_execution_ref')
            execution_scope=(lease or {}).get('execution_scope') or self.state.get('revoked_execution_scope')
            self.state['revoked_execution_ref']=copy.deepcopy(execution)
            self.state['revoked_execution_scope']=copy.deepcopy(execution_scope)
            self.state['owner']='USER';self.state['lease']=None;self.state['epoch']+=1
            cancellation=self._no_cancel()
            cancellation['queued_inputs']={'status':'NO_QUALIFIED_NATIVE_INPUT_QUEUE','discarded_count':0}
            if callable(self.queue_revoker):
                try:
                    receipt=self.queue_revoker(copy.deepcopy(lease))
                    if (not isinstance(receipt,dict) or receipt.get('status')!='DRAINED' or
                            type(receipt.get('discarded_count')) is not int or receipt['discarded_count']<0):raise ValueError('QUEUE_DRAIN_NOT_VERIFIED')
                    cancellation['queued_inputs']={'status':'DRAINED','discarded_count':receipt['discarded_count']}
                except Exception as exc:
                    cancellation['queued_inputs']={'status':'UNKNOWN_OUTCOME','discarded_count':None,
                                                  'error':{'type':type(exc).__name__,'message':str(exc)}}
            queue_ok=cancellation['queued_inputs']['status'] in {'DRAINED','NO_QUALIFIED_NATIVE_INPUT_QUEUE'}
            self.state['cancellation']={**cancellation,'status':'REQUESTED' if execution else 'NOT_NEEDED' if queue_ok else 'UNKNOWN_OUTCOME',
                'confirmed':not bool(execution) and queue_ok,'execution_ref':copy.deepcopy(execution)}
            self._save('HUMAN_TAKEOVER_REVOKED_BEFORE_ASYNC_CANCEL')
            # Preserve an in-flight layout CAS: it is already fenced and its
            # verified update advances the workspace epoch. Otherwise invalidate
            # every queued action carrying the old workspace input epoch too.
            if self.ws.current and not self.ws.current.get('closed') and not self.ws.current.get('prepare_pending'):
                self.ws.current['epoch']+=1;self.ws._save('HUMAN_TAKEOVER_INPUT_EPOCH_INVALIDATED')
            operation_epoch=self.state['epoch']
            result={'status':'USER_TAKEOVER','delegated':False,'control_ref':self.get()['control_ref'],
                    'cancellation':copy.deepcopy(self.state['cancellation'])}
            self.ws._write(path,{'request':claim,'request_sha256':request_sha,'result':result,'created_at':now()})
        if execution:
            status='UNKNOWN_OUTCOME';confirmed=False;detail=None
            try:
                row,original=self._execution(execution)
                if not execution_scope or original.get('desktop_scope')!=execution_scope:
                    raise Conflict('Cancellation execution is not the exact prior owned desktop task')
                if row['execution_status'] in {'SUCCEEDED','FAILED','CANCELLED','TIMED_OUT'}:
                    status='CONFIRMED';confirmed=True
                elif callable(getattr(self.transport,'request_cancel',None)):
                    receipt=self.transport.request_cancel(execution['project_id'],execution['execution_id'])
                    if inspect.isawaitable(receipt):receipt=await receipt
                    if not isinstance(receipt,dict) or receipt.get('id')!=execution['execution_id'] or receipt.get('project_id')!=execution['project_id'] or receipt.get('request_hash')!=execution['request_sha256']:
                        raise Conflict('Original cancellation receipt changed execution scope')
                    terminal=receipt.get('execution_status')
                    confirmed=terminal in {'SUCCEEDED','FAILED','CANCELLED','TIMED_OUT'}
                    status='CONFIRMED' if confirmed else 'REQUESTED' if terminal=='CANCEL_REQUESTED' else 'UNKNOWN_OUTCOME'
            except Exception as exc:
                status='FAILED';detail={'type':type(exc).__name__,'message':str(exc)}
            with self.lock:
                if self.state['epoch']==operation_epoch:
                    self._assert_authority()
                    queue_ok=self.state['cancellation']['queued_inputs']['status'] in {'DRAINED','NO_QUALIFIED_NATIVE_INPUT_QUEUE'}
                    self.state['cancellation']={**self.state['cancellation'],'status':status if queue_ok else 'UNKNOWN_OUTCOME',
                        'confirmed':confirmed and queue_ok,'execution_terminal_confirmed':confirmed,'error':detail}
                    if confirmed:self.state['revoked_execution_ref']=None;self.state['revoked_execution_scope']=None
                    self._save('EXACT_ORIGINAL_CANCEL_'+status)
        with self.lock:
            record=self.ws._read(path,authority=True)
            if self.state['epoch']==operation_epoch:
                record['result']={**record['result'],'control_ref':self.get()['control_ref'],
                                  'cancellation':copy.deepcopy(self.state['cancellation'])}
                final=self._path('operation-outcomes',body['operation_id']+'.json')
                if not final.exists():self.ws._write(final,record)
            return self._response(record,False)

    @contextmanager
    def dispatch_guard(self,lease_ref,surface_ref,input_epoch,control_epoch):
        """Every actual Cua mutation must remain inside both synchronous gates."""
        if self._takeover_intent.is_set():raise Conflict('Human takeover blocks subsequent desktop mutations')
        with self.lock:
            self._assert_authority()
            if self._takeover_intent.is_set():raise Conflict('Human takeover blocks subsequent desktop mutations')
            lease=self.state.get('lease')
            if (self.state['owner']!='AGENT' or not lease or lease_ref!=lease['lease_ref']
                    or type(control_epoch) is not int or control_epoch!=self.state['epoch']):
                raise Conflict('Desktop input belongs to USER or an expired Agent lease')
            desktop=self.ws.get()
            if not self._lease_current(lease,desktop):raise Conflict('Current desktop Agent scope or source has been revoked')
            if surface_ref!=lease['surface_ref'] or type(input_epoch) is not int or input_epoch!=lease['input_epoch']:
                raise Conflict('Desktop mutation differs from its delegated exact scope')
            with self.ws.dispatch_guard(surface_ref,input_epoch):yield self.get()
