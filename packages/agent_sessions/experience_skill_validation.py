"""Fixed Skill cases through the existing AgentSessions/OpenCode authority.

The candidate is visible only in each private test owner. No global Skill index,
MCP, workflow, workspace write or candidate activation is performed here.
"""
from __future__ import annotations
import asyncio
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import hashlib
import os
import re
import time
import uuid

from pydantic import Field
from packages.domain.questions import RequestModel,Id
from packages.domain.native_capabilities import NativeCapabilities,_skill
from packages.domain.store import Conflict,InvalidInput
from packages.domain.tasks import canonical,digest,now
from packages.domain.context_read_budget import context_read_scope
from .models import ConfigurationRef
from .experience_candidates import ExperienceCandidates

MAX_RESULT_BYTES=1048576


class ExperienceSkillValidate(RequestModel):
    operation_id:Id
    candidate_ref:ConfigurationRef
    review_ref:ConfigurationRef
    provider_profile_ref:ConfigurationRef
    validation_confirmed:bool=Field(strict=True)
    process_boundary_acknowledged:bool=Field(strict=True)


class _PrivateSkill:
    def __init__(self,validation,pid,sid,operation):
        self.validation=validation;self.pid=pid;self.sid=sid;self.operation=operation

    def _root(self,*parts):return self.validation._path(self.pid,self.sid,self.operation,'binding',*parts)
    def _path(self,*parts):return self._root(*parts)
    def _configuration(self,kind,ref):
        value=self.validation.candidates._read(self._root('configuration.json'))
        if kind!='skills' or ref!={'id':value['id'],'revision':1,'sha256':digest(value)}:
            raise Conflict('Private candidate Skill reference changed')
        return value
    def _index(self):
        value=self.validation.candidates._read(self._root('configuration.json'))
        ref={'id':value['id'],'revision':1,'sha256':digest(value)}
        return {'schema':'eeas.native-capabilities.v1','skills':{value['id']:ref},'mcp':{}}


class ExperienceSkillValidation:
    def __init__(self,candidates:ExperienceCandidates):
        self.candidates=candidates;self.sessions=candidates.sessions

    def _path(self,pid,sid,operation,*parts):return self.candidates._path(pid,sid,'skill-validation',digest(operation),*parts)

    def _budget(self,pid,sid,operation):
        value=self.candidates._read(self._path(pid,sid,operation,'budget.json'))
        if (set(value)!={'expires_at','max_model_calls','reservations','closed'} or type(value['closed']) is not bool
                or type(value['max_model_calls']) is not int or not 1<=value['max_model_calls']<=16
                or not isinstance(value['reservations'],list) or len(value['reservations'])>value['max_model_calls']):
            raise Conflict('Private Skill shared budget changed')
        return value

    def _admission(self,manifest,*,live=True):
        ref=manifest.get('experience_validation_admission')
        if not isinstance(ref,dict) or set(ref)!={'source_session_id','operation_id','request_sha256','case_id','admission_sha256'}:
            raise Conflict('Original Skill validation admission is missing')
        pid=manifest['project_id'];sid=ref['source_session_id'];operation=ref['operation_id']
        if not re.fullmatch(r'[a-f0-9]{32}',str(sid)) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',str(operation)):
            raise Conflict('Original Skill validation admission identity changed')
        intent=self.candidates.operation(pid,sid,operation,ref['request_sha256'])
        if intent['kind']!='SKILL_VALIDATION':raise Conflict('Original owner is not a Skill test')
        if live:self.candidates.source_policy().require(pid,sid,intent['request']['candidate_ref'])
        admission=self.candidates._read(self._path(pid,sid,operation,'admissions',digest(ref['case_id'])+'.json'))
        if (digest(admission)!=ref['admission_sha256'] or admission['request_sha256']!=ref['request_sha256']
                or admission['case_id']!=ref['case_id'] or admission['source_session_id']!=sid):
            raise Conflict('Original Skill validation immutable admission changed')
        if live:
            budget=self._budget(pid,sid,operation)
            if budget['closed'] or datetime.fromisoformat(budget['expires_at'])<=datetime.now(timezone.utc):
                raise Conflict('Private Skill validation budget is closed or expired')
        return admission,intent

    def binding(self,manifest,*,live=True):
        admission,intent=self._admission(manifest,live=live);request=manifest['request'];owner=intent['request']
        if (request['provider_profile_ref']!=owner['provider_profile_ref'] or request.get('workspace_ref') is not None
                or request.get('workflow_ref') is not None or request.get('workflow_provider_refs')
                or request['allowed_effects']!=['PROCESS_START'] or request['command_allowlist']
                or request.get('command_observation_paths') or request['mcp_tool_allowlist'] or request.get('mcp_tool_selection')
                or request.get('conversation_ref') is not None or request['max_calls']!=admission['max_model_calls']
                or request['max_output_tokens']!=admission['max_output_tokens'] or request['network_mode']!='AS_CONFIGURED'
                or request['ttl_seconds']!=admission['ttl_seconds']):
            raise Conflict('Private Skill owner exceeds the fixed validation admission')
        view=self.capabilities(manifest,live=live)
        binding=NativeCapabilities.native_binding_snapshot(view,validate_mcp_ids=set())
        if request['capability_binding_sha256']!=binding['sha256'] or binding['sha256']!=admission['binding_sha256']:
            raise Conflict('Private Skill original binding changed')
        return binding

    def capabilities(self,manifest,*,live=True):
        self._admission(manifest,live=live)
        ref=manifest['experience_validation_admission']
        return _PrivateSkill(self,manifest['project_id'],ref['source_session_id'],ref['operation_id'])

    def authorize_claim(self,manifest,kind,request):
        if kind in {'INTERRUPT','DISCONNECT'}:return
        admission,_=self._admission(manifest)
        if kind!='MESSAGE':raise Conflict('A private Skill test cannot load, guide, delegate or grant permissions')
        allowed={'operation_id','session_ref','generation','raw_text','agent','timeout_seconds'}
        if (set(request)!=allowed or request['operation_id']!=admission['message_operation_id']
                or request['raw_text']!=admission['raw_text'] or request['agent']!='build' or request['generation']!=1
                or request['session_ref']!={'id':manifest['id'],'revision':1,'sha256':digest(manifest)}
                or not 10<=request['timeout_seconds']<=admission['timeout_seconds']):
            raise Conflict('Only this fixed original Skill case input may be dispatched')

    def reserve_model(self,manifest,request_sha):
        admission,_=self._admission(manifest);ref=manifest['experience_validation_admission'];pid=manifest['project_id'];sid=ref['source_session_id'];op=ref['operation_id']
        with self.candidates._guard(pid,sid):
            budget=self._budget(pid,sid,op)
            if budget['closed'] or len(budget['reservations'])>=budget['max_model_calls'] or datetime.fromisoformat(budget['expires_at'])<=datetime.now(timezone.utc):
                raise Conflict('Shared Skill validation model allowance is exhausted')
            budget['reservations'].append({'id':uuid.uuid4().hex,'case_id':admission['case_id'],'request_sha256':request_sha,'reserved_at':now()})
            self.sessions._write(self._path(pid,sid,op,'budget.json'),budget,replace=True)

    @staticmethod
    def instructions():
        return ('Run only this fixed Skill evaluation case. First call the skill tool with the exact selected Skill name. '
                'Treat its content as instructions within the current read-only test permissions. '
                'Do not delegate, ask questions, execute commands, read user files or change files. '
                'Return the requested case answer as plain text. This is a test, not a production action or capability approval.')

    def inspect(self,pid,sid,operation,request_sha):
        intent=self.candidates.operation(pid,sid,operation,request_sha)
        if intent['kind']!='SKILL_VALIDATION':raise Conflict('Not the original Skill validation operation')
        path=self._path(pid,sid,operation,'result.json')
        if not path.exists():return {'kind':'EXPERIENCE_SKILL_VALIDATION','operation_id':operation,'request_sha256':request_sha,
            'candidate_ref':intent['request']['candidate_ref'],'review_ref':intent['request']['review_ref'],
            'status':'UNKNOWN_OR_IN_PROGRESS','qualified':False,'automatic_replay':False,'enabled':False}
        result=self.candidates._read(path,MAX_RESULT_BYTES)
        if (result.get('operation_id')!=operation or result.get('request_sha256')!=request_sha
                or result.get('candidate_ref')!=intent['request']['candidate_ref'] or result.get('review_ref')!=intent['request']['review_ref']
                or result.get('project_id')!=pid or result.get('session_id')!=sid):raise Conflict('Original Skill validation result differs')
        return deepcopy(result)

    async def validate(self,pid,sid,body):
        body=ExperienceSkillValidate.model_validate(body);request=body.model_dump();request_sha=digest(request);c=self.candidates
        if not body.validation_confirmed or not body.process_boundary_acknowledged:
            raise InvalidInput('Explicit private Skill evaluation and process boundary acknowledgement are required')
        with context_read_scope(),c._guard(pid,sid):
            index=c._index(pid,sid);old=c._record(pid,sid,body.operation_id,'SKILL_VALIDATION',request_sha,index)
            if old is not None:
                c._append(pid,sid,index,old);return self.inspect(pid,sid,body.operation_id,request_sha)
            c.source_policy().require(pid,sid,body.candidate_ref)
            candidate=c.candidate(pid,sid,body.candidate_ref);review=c.saved_record(pid,sid,body.review_ref,'REVIEW')
            if candidate['request']['proposal']['kind']!='SKILL' or review['candidate_ref']!=body.candidate_ref.model_dump():
                raise Conflict('Private Skill validation needs the exact independent Skill review')
            plan=review['request']
            if plan['allowed_effects']!=['PROCESS_START'] or plan['max_model_calls']<len(plan['cases']):raise Conflict('Skill test budget or effects changed')
        # Resolve only the explicitly selected existing provider. No fallback or
        # new account. Subscription user-turn counts cannot certify API hops.
        profile=await asyncio.to_thread(self.sessions.providers.resolve,body.provider_profile_ref.model_dump())
        if getattr(profile,'auth_mode','API_KEY')=='OAUTH_SUBSCRIPTION':
            raise Conflict('Fixed Skill evaluation needs a metered API/local-model profile; native subscription transport and output-token limits are not qualified for private evaluation')
        with c._guard(pid,sid):
            index=c._index(pid,sid)
            if c._record(pid,sid,body.operation_id,'SKILL_VALIDATION',request_sha,index) is not None:
                return self.inspect(pid,sid,body.operation_id,request_sha)
            count=sum(1 for r in index['records'] if r['kind']=='SKILL_VALIDATION' and c._record(pid,sid,r['id'],'SKILL_VALIDATION',r['request_sha256'],index)['request']['candidate_ref']==body.candidate_ref.model_dump())
            if count>=16:raise Conflict('Private Skill validation history is full')
            proposal=candidate['request']['proposal'];configuration={**_skill(proposal['content'],proposal['id']),'enabled':True}
            value={'id':proposal['id'],'revision':1,'configuration':configuration,'created_at':candidate['created_at']}
            target=self._path(pid,sid,body.operation_id,'binding','configuration.json')
            if target.exists():
                if c._read(target)!=value:raise Conflict('Interrupted private Skill bytes changed')
            else:self.sessions._write(target,value)
            path=self._path(pid,sid,body.operation_id,'binding','skill-files',proposal['id'],'0000000001','SKILL.md')
            path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if path.read_bytes()!=proposal['content'].encode():raise Conflict('Private original Skill content changed')
            else:
                with path.open('xb') as stream:
                    stream.write(proposal['content'].encode());stream.flush();os.fsync(stream.fileno())
                path.chmod(0o600)
            binding=NativeCapabilities.native_binding_snapshot(_PrivateSkill(self,pid,sid,body.operation_id),validate_mcp_ids=set())
            intent={'schema':'eeas.task-experience-skill-validation-intent.v1','kind':'SKILL_VALIDATION','project_id':pid,'session_id':sid,
                'operation_id':body.operation_id,'request':request,'request_sha256':request_sha,'created_at':now(),
                'binding_sha256':binding['sha256'],'automatic_replay':False}
            c._append(pid,sid,index,intent)
            self.sessions._write(self._path(pid,sid,body.operation_id,'budget.json'),{
                'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=plan['timeout_seconds'])).isoformat(),
                'max_model_calls':plan['max_model_calls'],'reservations':[],'closed':False})
        started=time.monotonic();progress=[];status='FAIL';error=None
        try:
            for case in plan['cases']:
                c.source_policy().require(pid,sid,body.candidate_ref)
                remaining=plan['timeout_seconds']-(time.monotonic()-started)
                if remaining<10:raise TimeoutError('Shared Skill validation deadline exhausted')
                await asyncio.wait_for(self._case(pid,sid,body,request_sha,case,plan,binding,remaining,progress),timeout=remaining)
            status='PASS' if len(progress)==len(plan['cases']) and all(r['status']=='PASS' for r in progress) else 'FAIL'
        except asyncio.CancelledError:
            self._finish(pid,sid,body,request_sha,progress,'CANCELLED','CancelledError',started);raise
        except Exception as exc:
            error=type(exc).__name__;status='UNKNOWN' if any(r['status']=='UNKNOWN' for r in progress) else 'FAIL'
        return self._finish(pid,sid,body,request_sha,progress,status,error,started)

    async def _case(self,pid,sid,body,request_sha,case,plan,binding,remaining,progress):
        key=digest([pid,sid,body.operation_id,case['id']]);open_op='exp_open_'+key;message_op='exp_case_'+key
        raw='Selected Skill: '+next(iter(binding['value']['configuration_refs']['skills']))+'\nFixed case:\n'+case['prompt']
        admission={'source_session_id':sid,'request_sha256':request_sha,'case_id':case['id'],'binding_sha256':binding['sha256'],
            'max_model_calls':plan['max_model_calls'],'max_output_tokens':plan['max_output_tokens'],'ttl_seconds':max(30,int(remaining)),
            'timeout_seconds':int(remaining),'message_operation_id':message_op,'raw_text':raw}
        ref={'source_session_id':sid,'operation_id':body.operation_id,'request_sha256':request_sha,'case_id':case['id'],'admission_sha256':digest(admission)}
        self.sessions._write(self._path(pid,sid,body.operation_id,'admissions',digest(case['id'])+'.json'),admission)
        row={'id':case['id'],'label':case['label'],'purpose':case['purpose'],'status':'UNKNOWN','open_operation_id':open_op,
             'message_operation_id':message_op,'input_sha256':hashlib.sha256(raw.encode()).hexdigest(),'owner_ref':None}
        progress.append(row);self.sessions._write(self._path(pid,sid,body.operation_id,'progress.json'),{'cases':progress},replace=True)
        opened=None
        try:
            opened=await self.sessions.open(pid,{'operation_id':open_op,'workspace_ref':None,'provider_profile_ref':body.provider_profile_ref.model_dump(),
                'capability_binding_sha256':binding['sha256'],'title':case['label'],'ttl_seconds':admission['ttl_seconds'],
                'max_calls':plan['max_model_calls'],'max_output_tokens':plan['max_output_tokens'],'allowed_effects':['PROCESS_START'],
                'command_allowlist':[],'mcp_tool_allowlist':[],'network_mode':'AS_CONFIGURED'},experience_validation_admission=ref)
            row['owner_ref']=opened['session_ref'];owner=opened['session_ref']['id'];generation=opened['generation']
            message=await self.sessions.send(pid,owner,{'operation_id':message_op,'session_ref':opened['session_ref'],'generation':generation,
                'raw_text':raw,'agent':'build','timeout_seconds':admission['timeout_seconds']})
            if message['status'] not in {'ACCEPTED','RETURNED','RUNNING','DISPATCHING'}:raise Conflict('Original Skill case dispatch was not acknowledged')
            until=asyncio.get_running_loop().time()+remaining
            while True:
                # The existing owner monitor already reads native state. Do
                # not run a competing native observer every 100ms. A terminal
                # result still requires one fresh exact-owner native read.
                observed=self.sessions._state(pid,owner);operation=self.sessions.operation(pid,owner,message_op)
                if operation['status'] in {'RETURNED','FAILED','CANCELLED','UNKNOWN_OUTCOME'}:
                    observed=await self.sessions.inspect(pid,owner)
                    operation=self.sessions.operation(pid,owner,message_op)
                    break
                if observed.get('pending_questions') or observed.get('pending_permissions'):raise Conflict('Fixed Skill case requested additional authority')
                if asyncio.get_running_loop().time()>=until:raise TimeoutError('Original Skill case deadline exceeded')
                await asyncio.sleep(.1)
            if operation['status']!='RETURNED' or not observed.get('observation_is_current'):raise Conflict('Original Skill case result is not a fresh returned observation')
            native=operation.get('native_message_id');messages=[m for m in observed['messages'] if m.get('info',{}).get('role')=='assistant' and m['info'].get('parentID')==native]
            answer='\n'.join(p['text'] for m in messages for p in m.get('parts',[]) if p.get('type')=='text' and isinstance(p.get('text'),str))
            if len(answer.encode())>65536:raise Conflict('Skill case answer exceeds 64KiB')
            skill=next(iter(binding['value']['configuration_refs']['skills']))
            invoked=[p for m in messages for p in m.get('parts',[]) if p.get('type')=='tool' and p.get('tool')=='skill'
                and p.get('state',{}).get('status')=='completed' and p['state'].get('input',{}).get('name')==skill]
            loaded=observed.get('capability_load') or {};load=next((s for s in loaded.get('skills',[]) if s['id']==skill),None)
            unexpected=[p for m in messages for p in m.get('parts',[]) if p.get('type')=='tool' and (p.get('tool')!='skill' or p.get('state',{}).get('input',{}).get('name')!=skill)]
            checks=not unexpected and bool(answer) and bool(invoked) and bool(load and load.get('loaded_in_original_session')) and all(t in answer for t in case['required_text']) and all(t not in answer for t in case['forbidden_text'])
            row.update(status='PASS' if checks else 'FAIL',answer=answer,answer_sha256=hashlib.sha256(answer.encode()).hexdigest(),
                skill_loaded=bool(load and load.get('loaded_in_original_session')),skill_invocation_count=len(invoked),unexpected_tool_calls=len(unexpected),
                original_message_ref={'operation_id':message_op,'request_sha256':operation['request_sha256'],'native_message_id':native},
                observed_message_sha256=digest(messages),required_checks=[{'text':t,'matched':t in answer} for t in case['required_text']],
                forbidden_checks=[{'text':t,'absent':t not in answer} for t in case['forbidden_text']])
        finally:
            if opened is None:
                try:opened=self.sessions.open_operation(open_op,pid).get('session')
                except Exception:opened=None
            if opened is not None:
                owner=opened.get('session_ref',{}).get('id') or opened.get('id')
                if owner:
                    stopped=await self.sessions.disconnect(pid,owner)
                    row['cleanup_owner_status']=stopped['owner_status']
                    if stopped['owner_status']=='BLOCKED_PROCESS_STOP_UNCONFIRMED' or self.sessions._alive(stopped.get('process_identity')):
                        row['status']='UNKNOWN'
            self.sessions._write(self._path(pid,sid,body.operation_id,'progress.json'),{'cases':progress},replace=True)

    def _finish(self,pid,sid,body,request_sha,progress,status,error,started):
        with self.candidates._guard(pid,sid):
            budget=self._budget(pid,sid,body.operation_id);budget['closed']=True
            self.sessions._write(self._path(pid,sid,body.operation_id,'budget.json'),budget,replace=True)
            if self.candidates.source_policy().status(pid,sid,body.candidate_ref)['availability']!='ACTIVE':status='SOURCE_BLOCKED'
            result={'kind':'EXPERIENCE_SKILL_VALIDATION','project_id':pid,'session_id':sid,'operation_id':body.operation_id,
                'request_sha256':request_sha,'candidate_ref':body.candidate_ref.model_dump(),'review_ref':body.review_ref.model_dump(),
                'provider_profile_ref':body.provider_profile_ref.model_dump(),'status':status,'cases':deepcopy(progress),'error_type':error,
                'elapsed_seconds':round(time.monotonic()-started,3),'recorded_at':now(),'model_calls_reserved':len(budget['reservations']),
                'qualified':status=='PASS','enabled':False,'global_configuration_changed':False,'automatic_replay':False,
                'qualification_scope':'EXACT_PRIVATE_ORIGINAL_SDK_SKILL_CASES_NOT_GENERAL_SAFETY_OR_UNSEEN_TASKS',
                'os_network_sandbox':'NOT_QUALIFIED'}
            if len(canonical(result).encode())>MAX_RESULT_BYTES:raise Conflict('Skill validation result exceeds its bound; progress retained')
            self.sessions._write(self._path(pid,sid,body.operation_id,'result.json'),result)
            return result
