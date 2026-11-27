"""Task-sourced immutable capability proposals, before any execution or enable.

Candidates and human review plans are data. They never register native Skill/MCP
configuration, qualify code, approve a model's own proposal or start a process.
Validation/promotion must consume these exact references through the existing
original session and NativeCapabilities authorities.
"""
from __future__ import annotations
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import Field, model_validator
from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.local_workspaces import FileSource
from packages.domain.native_capabilities import _skill, _mcp, _ref
from packages.adapters.opencode_desktop import MCP_NAME
from packages.domain.context_read_budget import context_read_scope
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest, now
from .models import ConfigurationRef
from .work_requirements import WorkRequirementScope, _root, _unchanged

MAX_CANDIDATES = 32
MAX_RECORDS = 256
MAX_CANDIDATE_BYTES = 131072
MAX_REVIEW_BYTES = 65536
MAX_SOURCE_BYTES = 262144
MAX_SOURCES_TOTAL = 1048576
PAGE = 8
SCHEMA = 'eeas.task-experience-candidate.v1'


class ExperienceQuote(RequestModel):
    source: FileSource
    char_start: int = Field(ge=0, strict=True)
    char_end: int = Field(gt=0, strict=True)
    quote_sha256: Hash

    @model_validator(mode='after')
    def bounded_span(self):
        if not 1 <= self.char_end-self.char_start <= 4000:
            raise ValueError('Select 1–4000 original Unicode codepoints')
        return self


class SkillProposal(RequestModel):
    kind: Literal['SKILL']
    id: str = Field(pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$', max_length=64)
    content: str = Field(min_length=1, max_length=65536)


class ToolProposal(RequestModel):
    kind: Literal['MCP_TOOL']
    id: Id
    configuration: dict


class ExperiencePropose(WorkRequirementScope):
    operation_id: Id
    root_sha256: Hash
    title: str = Field(min_length=1, max_length=200)
    observation: str = Field(min_length=1, max_length=4000)
    proposed_use: str = Field(min_length=1, max_length=4000)
    limitations: str = Field(min_length=1, max_length=4000)
    proposal: Annotated[SkillProposal | ToolProposal, Field(discriminator='kind')]
    sources: list[ExperienceQuote] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def exact_source_scope(self):
        keys=[]
        for selected in self.sources:
            s=selected.source
            if (s.workspace_id != self.workspace_ref.id or s.binding_revision != self.workspace_ref.revision
                    or s.binding_sha256 != self.workspace_ref.binding_sha256):
                raise ValueError('Experience sources must use the original exact workspace binding')
            keys.append((s.path,s.revision,s.sha256,selected.char_start,selected.char_end))
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate source selections are not accepted')
        if len(canonical(self.model_dump()).encode()) > MAX_CANDIDATE_BYTES:
            raise ValueError('Candidate input exceeds its 128KiB limit')
        return self


class SkillCheck(RequestModel):
    kind: Literal['SKILL']
    id: Id
    label: str = Field(min_length=1, max_length=200)
    purpose: Literal['POSITIVE', 'NEGATIVE']
    prompt: str = Field(min_length=1, max_length=4000)
    required_text: list[str] = Field(default_factory=list, max_length=8)
    forbidden_text: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def actual_checks(self):
        values=self.required_text+self.forbidden_text
        if (not values or any(not isinstance(v,str) or not v or len(v)>1000 or '\x00' in v for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('Each Skill case needs distinct bounded explicit output checks')
        return self


class ToolCheck(RequestModel):
    kind: Literal['MCP_TOOL']
    id: Id
    label: str = Field(min_length=1, max_length=200)
    purpose: Literal['POSITIVE', 'NEGATIVE']
    tool_name: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    input: dict
    expected_result: dict

    @model_validator(mode='after')
    def bounded_input(self):
        try:
            sizes = [len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode('utf-8'))
                for value in (self.input, self.expected_result)]
        except (ValueError, TypeError, RecursionError) as error:
            raise ValueError('Tool checks require finite UTF-8 JSON values') from error
        if any(size > 8192 for size in sizes):
            raise ValueError('Tool test input or expected result exceeds 8KiB')
        if (set(self.expected_result)!={'content','structuredContent','isError'}
                or not isinstance(self.expected_result['content'],list) or type(self.expected_result['isError']) is not bool):
            raise ValueError('Expected result must fix content, structuredContent and an explicit isError boolean')
        return self


class ExperienceReview(RequestModel):
    operation_id: Id
    candidate_ref: ConfigurationRef
    review_confirmed: bool = Field(strict=True)
    statement: str = Field(min_length=1, max_length=4000)
    cases: list[Annotated[SkillCheck | ToolCheck, Field(discriminator='kind')]] = Field(min_length=2, max_length=8)
    # Consent is bounded to later explicit validation; this review starts nothing.
    allowed_effects: list[Literal['PROCESS_START', 'REMOTE_MCP_CONNECT']] = Field(default_factory=list, max_length=2)
    max_model_calls: int = Field(default=8, ge=0, le=16, strict=True)
    max_output_tokens: int = Field(default=2048, ge=64, le=4096, strict=True)
    timeout_seconds: int = Field(default=120, ge=10, le=180, strict=True)
    process_boundary_acknowledged: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def independent_fixed_cases(self):
        if not self.review_confirmed:
            raise ValueError('A separate explicit human review of these exact bytes is required')
        if (len({c.id for c in self.cases}) != len(self.cases)
                or {c.purpose for c in self.cases} != {'POSITIVE', 'NEGATIVE'}
                or len(set(self.allowed_effects)) != len(self.allowed_effects)):
            raise ValueError('Fix distinct positive and negative cases and exact effects before validation')
        if self.allowed_effects and not self.process_boundary_acknowledged:
            raise ValueError('A private working directory is not an OS/network sandbox')
        if len(canonical(self.model_dump()).encode()) > MAX_REVIEW_BYTES:
            raise ValueError('Review plan exceeds 64KiB')
        return self


class ExperienceCandidates:
    def __init__(self, sessions):
        self.sessions=sessions
        self.store=sessions.store
        self.ws=sessions.workspaces

    def source_policy(self):
        from .experience_source_policy import ExperienceSourcePolicy
        return ExperienceSourcePolicy(self)

    def _path(self,pid,sid,*parts):
        return self.sessions._path(pid,sid,'experience-candidates',*parts)

    def _read(self,path,maximum=MAX_CANDIDATE_BYTES):
        if not path.is_file() or path.stat().st_size > maximum+512:
            raise Conflict('Experience record is missing or oversized')
        result=self.sessions._read(path)
        if len(canonical(result).encode()) > maximum:
            raise Conflict('Experience record exceeds its bound')
        return result

    @contextmanager
    def _guard(self,pid,sid):
        self.sessions._manifest(pid,sid)
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            yield

    def _index(self,pid,sid):
        path=self._path(pid,sid,'index.json')
        v=self._read(path) if path.exists() else {'schema':SCHEMA,'revision':0,'candidates':[], 'records':[]}
        if (not isinstance(v,dict) or set(v) != {'schema','revision','candidates','records'} or v['schema'] != SCHEMA
                or type(v['revision']) is not int or v['revision']<0 or not isinstance(v['candidates'],list)
                or not isinstance(v['records'],list) or len(v['candidates'])>MAX_CANDIDATES or len(v['records'])>MAX_RECORDS):
            raise Conflict('Experience index integrity failed; old candidates are retained')
        for row in v['records']:
            if (not isinstance(row,dict) or not {'id','kind','request_sha256','record_sha256'}<=set(row) or set(row)-{'id','kind','request_sha256','record_sha256','source_authority_sha256','candidate_ref'}
                    or not isinstance(row['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',row['id'])
                    or row['kind'] not in ['PROPOSAL','REVIEW','VALIDATION','SKILL_VALIDATION','ACTIVATION','ROLLBACK']
                    or any(not isinstance(row[k],str) or not re.fullmatch(r'[a-f0-9]{64}',row[k]) for k in ['request_sha256','record_sha256'])):
                raise Conflict('Experience operation index changed')
        if len({row['id'] for row in v['records']})!=len(v['records']):
            raise Conflict('Experience operation IDs are duplicated')
        for ref in v['candidates']:
            if (not isinstance(ref,dict) or set(ref)!={'id','revision','sha256'} or not isinstance(ref['id'],str)
                    or type(ref['revision']) is not int or ref['revision']!=1
                    or not isinstance(ref['sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',ref['sha256'])
                    or not any(row['id']==ref['id'] and row['kind']=='PROPOSAL' and row['record_sha256']==ref['sha256'] for row in v['records'])):
                raise Conflict('Experience candidate index changed')
        if len({ref['id'] for ref in v['candidates']}) != len(v['candidates']):
            raise Conflict('Experience candidate IDs are duplicated')
        for row in v['records']:
            if 'candidate_ref' in row and ConfigurationRef.model_validate(row['candidate_ref']).model_dump() not in v['candidates']:
                raise Conflict('Experience history candidate identity changed')
            if 'source_authority_sha256' in row and (row['kind']!='PROPOSAL' or not isinstance(row['source_authority_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',row['source_authority_sha256'])):
                raise Conflict('Experience source authority index changed')
        return v

    def _record(self,pid,sid,operation,kind,request_hash,index=None):
        index=index if index is not None else self._index(pid,sid)
        row=next((r for r in index['records'] if r['id']==operation),None)
        path=self._path(pid,sid,'records',digest(operation)+'.json')
        if row is None and not path.exists():
            return None
        value=self._read(path,262144 if kind in {'ACTIVATION','ROLLBACK'} else MAX_CANDIDATE_BYTES)
        if (not isinstance(value,dict) or value.get('project_id')!=pid or value.get('session_id')!=sid or value.get('operation_id')!=operation
                or value.get('kind')!=kind or value.get('request_sha256')!=request_hash
                or digest(value.get('request'))!=request_hash
                or row is not None and (row['kind']!=kind or row['request_sha256']!=request_hash or row['record_sha256']!=digest(value))):
            raise Conflict('IDEMPOTENCY_CONFLICT: original experience operation binds different bytes')
        if row is not None and 'source_authority_sha256' in row and (value.get('source_authority_sha256')!=row['source_authority_sha256'] or digest(value.get('source_authority'))!=row['source_authority_sha256']):
            raise Conflict('Experience source authority differs from its indexed candidate')
        if row is not None and 'candidate_ref' in row and self._candidate_for_record(pid,sid,value,index)!=row['candidate_ref']:
            raise Conflict('Experience history names another candidate')
        return value

    def _candidate_for_record(self,pid,sid,value,index):
        if value['kind']=='PROPOSAL':return {'id':value['operation_id'],'revision':1,'sha256':digest(value)}
        if 'candidate_ref' in value['request']:return value['request']['candidate_ref']
        if value['kind']=='ROLLBACK':
            ref=value['basis']['source_activation_ref'];row=next((r for r in index['records'] if r['id']==ref['id'] and r['record_sha256']==ref['sha256'] and r['kind']=='ACTIVATION'),None)
            if row is None:raise Conflict('Original activation for rollback is unavailable')
            source=self._record(pid,sid,row['id'],'ACTIVATION',row['request_sha256'],index)
            return source['request']['candidate_ref']
        raise Conflict('Original experience candidate relation is unavailable')

    def _append(self,pid,sid,index,value):
        old=next((r for r in index['records'] if r['id']==value['operation_id']),None)
        if old is not None:
            if old['record_sha256']!=digest(value):raise Conflict('Experience operation bytes changed')
            if value['kind']=='PROPOSAL':self.source_policy().ensure_authority(pid,sid,value)
            return
        if len(index['records'])>=MAX_RECORDS or value['kind']=='PROPOSAL' and len(index['candidates'])>=MAX_CANDIDATES:
            raise Conflict('Experience capacity reached; no original record was evicted')
        if len(canonical(value).encode())>(262144 if value['kind'] in {'ACTIVATION','ROLLBACK'} else MAX_CANDIDATE_BYTES):
            raise Conflict('Experience record exceeds its preserved metadata bound')
        index=deepcopy(index)
        row={'id':value['operation_id'],'kind':value['kind'],'request_sha256':value['request_sha256'],'record_sha256':digest(value)}
        row['candidate_ref']=deepcopy(self._candidate_for_record(pid,sid,value,index))
        if value['kind']=='PROPOSAL' and 'source_authority_sha256' in value:row['source_authority_sha256']=value['source_authority_sha256']
        index['records'].append(row)
        if value['kind']=='PROPOSAL':index['candidates'].append({'id':row['id'],'revision':1,'sha256':row['record_sha256']})
        index['revision']+=1
        path=self._path(pid,sid,'records',digest(value['operation_id'])+'.json')
        if path.exists():
            if self._read(path,262144 if value['kind'] in {'ACTIVATION','ROLLBACK'} else MAX_CANDIDATE_BYTES)!=value:raise Conflict('Interrupted experience record differs')
        else:self.sessions._write(path,value)
        if value['kind']=='PROPOSAL':self.source_policy().ensure_authority(pid,sid,value)
        self.sessions._write(self._path(pid,sid,'index.json'),index,replace=True)
        if self._index(pid,sid)!=index:raise Conflict('Experience index save was not confirmed')

    def _quotes(self,pid,body):
        results=[];total=0
        for selected in body.sources:
            source=selected.source
            if source.project_id!=pid:raise InvalidInput('Candidate source belongs to another project')
            binding=self.ws.binding_record(pid,source.workspace_id,source.binding_revision)
            if binding['binding_sha256']!=source.binding_sha256:raise Conflict('Candidate source binding changed')
            self.ws._private(binding,source.path)
            saved=self.ws._version(binding,source.path,source.revision,max_bytes=MAX_SOURCE_BYTES,_exact_revision=True,_with_bytes=True)
            if saved is None:raise Conflict('Candidate source saved version is missing')
            version,raw=saved
            if not version or self.ws._ref(version)!=source.model_dump() or hashlib.sha256(raw).hexdigest()!=source.sha256:
                raise Conflict('Candidate source is not the exact saved file')
            total+=len(raw)
            if total>MAX_SOURCES_TOTAL:raise Conflict('Candidate source reads exceed 1MiB')
            try:text=raw.decode('utf-8')
            except UnicodeError:raise InvalidInput('Candidate source quote must be UTF-8') from None
            quote=text[selected.char_start:selected.char_end]
            if selected.char_end>len(text) or hashlib.sha256(quote.encode()).hexdigest()!=selected.quote_sha256:
                raise Conflict('Candidate quote range or SHA differs from saved source')
            results.append({**selected.model_dump(),'quote':quote,'source_status':'EXACT_SAVED_VERSION_NOT_A_CURRENT_FILE_CLAIM'})
        return results

    @staticmethod
    def _view(value):
        result=deepcopy(value)
        result.update(record_ref={'id':value['operation_id'],'revision':1,'sha256':digest(value)},
            metadata_only=True,enabled=False,configuration_registered=False,model_calls=0,commands_started=0,
            permission_granted=False,qualification='NOT_VALIDATED',automatic_replay=False)
        if value['kind'] in {'VALIDATION','SKILL_VALIDATION'}:
            result.pop('commands_started',None)
            result['execution_status']='READ_EXACT_VALIDATION_OUTCOME'
        if value['kind'] in {'ACTIVATION','ROLLBACK'}:
            result.pop('enabled',None);result.pop('configuration_registered',None)
            result['configuration_status']='READ_EXACT_CONFIGURATION_OPERATION'
        return result

    def propose(self,pid,sid,body):
        body=ExperiencePropose.model_validate(body);request=body.model_dump();request_hash=digest(request)
        with context_read_scope(),self._guard(pid,sid):
            index=self._index(pid,sid);old=self._record(pid,sid,body.operation_id,'PROPOSAL',request_hash,index)
            if old is not None:
                self._append(pid,sid,index,old)
                return self._view(old)
            if len(index['records'])>=MAX_RECORDS or len(index['candidates'])>=MAX_CANDIDATES:
                raise Conflict('Experience capacity reached; original proposals were retained')
            root=_root(self.sessions,pid,sid,body)
            if root['sha256']!=body.root_sha256:raise Conflict('Candidate original task basis changed')
            proposal=body.proposal
            if proposal.kind=='SKILL':
                configuration={k:v for k,v in _skill(proposal.content,proposal.id).items() if k!='content'}
                configuration['enabled']=False
            else:
                if proposal.configuration.get('enabled') is not False:raise InvalidInput('Proposed Tool must be disabled')
                if proposal.id==MCP_NAME:raise InvalidInput('The execution-owned namespace cannot be proposed')
                _ref({'id':proposal.id,'revision':1,'sha256':'0'*64})
                configuration=_mcp(proposal.configuration)
            quotes=self._quotes(pid,body)
            source_authority=self.source_policy().freeze(pid,sid,root)
            _unchanged(self.sessions,pid,sid,body,root)
            value={'schema':SCHEMA,'kind':'PROPOSAL','project_id':pid,'session_id':sid,
                'operation_id':body.operation_id,'request_sha256':request_hash,'request':request,
                'root_basis':root['basis'],'root_status':root['status'],'root_label':root['label'],
                'sources':quotes,'configuration':configuration,'created_at':now(),
                'source_authority':source_authority,'source_authority_sha256':digest(source_authority),
                'author_scope':'PROPOSED_FROM_TASK_NOT_INDEPENDENTLY_REVIEWED',
                'source_status':'FROZEN_HISTORICAL_EVIDENCE_NOT_CURRENT_PERMISSION',
                'whole_task_acceptance':'NOT_ASSESSED'}
            if len(canonical(value).encode())>MAX_CANDIDATE_BYTES:raise Conflict('Frozen candidate exceeds 128KiB')
            self._append(pid,sid,index,value)
            return self._view(value)

    def saved_record(self,pid,sid,ref,kind):
        ref=ConfigurationRef.model_validate(ref).model_dump();self.sessions._manifest(pid,sid)
        index=self._index(pid,sid)
        row=next((r for r in index['records'] if r['id']==ref['id'] and r['kind']==kind),None)
        if row is None or ref['revision']!=1 or row['record_sha256']!=ref['sha256']:
            raise NotFound('Exact experience record is not indexed for this original task owner')
        value=self._record(pid,sid,ref['id'],kind,row['request_sha256'],index)
        if digest(value)!=ref['sha256']:raise Conflict('Experience record hash changed')
        return self._view(value)

    def candidate(self,pid,sid,ref):
        ref=ConfigurationRef.model_validate(ref).model_dump();self.sessions._manifest(pid,sid)
        index=self._index(pid,sid)
        if ref not in index['candidates']:raise NotFound('Exact original candidate is not indexed in this task owner')
        row=next(r for r in index['records'] if r['id']==ref['id'])
        v=self._record(pid,sid,ref['id'],'PROPOSAL',row['request_sha256'],index)
        if digest(v)!=ref['sha256']:raise Conflict('Original candidate hash changed')
        return self._view(v)

    def review(self,pid,sid,body):
        body=ExperienceReview.model_validate(body);request=body.model_dump();request_hash=digest(request)
        with context_read_scope(),self._guard(pid,sid):
            index=self._index(pid,sid);old=self._record(pid,sid,body.operation_id,'REVIEW',request_hash,index)
            if old is not None:
                self._append(pid,sid,index,old)
                return self._view(old)
            self.source_policy().require(pid,sid,body.candidate_ref)
            candidate=self.candidate(pid,sid,body.candidate_ref)
            if any(case.kind!=candidate['request']['proposal']['kind'] for case in body.cases):
                raise InvalidInput('Review cases must exercise the exact candidate kind')
            existing=[r for r in index['records'] if r['kind']=='REVIEW']
            count=sum(self._record(pid,sid,r['id'],'REVIEW',r['request_sha256'],index)['request']['candidate_ref']==body.candidate_ref.model_dump() for r in existing)
            if count>=8:raise Conflict('Eight review plans are already retained for this candidate')
            kind=candidate['request']['proposal']['kind']
            if kind=='MCP_TOOL':
                transport=candidate['configuration']['type']
                effect='PROCESS_START' if transport=='local' else 'REMOTE_MCP_CONNECT'
                if body.allowed_effects!=[effect] or body.max_model_calls!=0:
                    raise InvalidInput('Tool validation requires its exact transport effect and no model calls')
            elif body.max_model_calls<len(body.cases) or body.allowed_effects!=['PROCESS_START']:
                raise InvalidInput('Original Skill validation requires explicit SDK start and one bounded call per fixed case')
            value={'schema':SCHEMA,'kind':'REVIEW','project_id':pid,'session_id':sid,
                'operation_id':body.operation_id,'request_sha256':request_hash,'request':request,
                'candidate_ref':body.candidate_ref.model_dump(),'created_at':now(),
                'review_origin':'EXPLICIT_USER_CONTROL_NOT_MODEL_SELF_APPROVAL',
                'validation_status':'FIXED_PLAN_NOT_EXECUTED','activation_authorized':False,
                'process_boundary':'PRIVATE_TEST_OWNER_AND_WORKING_DIRECTORY_NOT_OS_OR_NETWORK_SANDBOX'}
            self._append(pid,sid,index,value)
            return self._view(value)

    def operation(self,pid,sid,operation_id,request_sha256):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',operation_id) or not re.fullmatch(r'[a-f0-9]{64}',request_sha256):
            raise InvalidInput('Exact original experience operation and input SHA are required')
        self.sessions._manifest(pid,sid);index=self._index(pid,sid)
        row=next((r for r in index['records'] if r['id']==operation_id),None)
        if row is None:raise NotFound('Original experience operation has no indexed receipt')
        return self._view(self._record(pid,sid,operation_id,row['kind'],request_sha256,index))

    def listing(self,pid,sid,offset=0):
        if type(offset) is not int or not 0<=offset<=MAX_CANDIDATES:raise InvalidInput('Invalid candidate page')
        self.sessions._manifest(pid,sid);index=self._index(pid,sid);refs=list(reversed(index['candidates']))
        start=min(offset,((max(len(refs)-1,0))//PAGE)*PAGE);rows=[]
        for ref in refs[start:start+PAGE]:
            value=self.candidate(pid,sid,ref)
            policy=self.source_policy().status(pid,sid,ref)
            rows.append({'source_policy':policy,'candidate_ref':ref,'title':value['request']['title'] if policy['availability']=='ACTIVE' else '已停用的经验来源','kind':value['request']['proposal']['kind'],
                'root_operation_id':value['request']['root_operation_id'],'root_sha256':value['request']['root_sha256'],
                'created_at':value['created_at'],'state':'PROPOSED_NOT_ENABLED','qualified':False})
        return {'schema':SCHEMA,'project_id':pid,'session_id':sid,'entries':rows,'offset':start,
            'next_offset':start+PAGE if start+PAGE<len(refs) else None,'total':len(refs),'read_only':True,
            'model_calls':0,'commands_started':0,'configuration_registered':False,'automatic_replay':False}

    def source_work(self,pid,sid,ref):
        value=self.candidate(pid,sid,ref);manifest,owner=self.sessions._manifest(pid,sid);request=value['request'];state=self.sessions._state(pid,sid)
        if request['session_ref']!=owner:raise Conflict('Original experience source owner changed')
        context={'project_id':pid,'session_ref':owner,'generation':state['generation'],'workspace_ref':request['workspace_ref']}
        profile=manifest.get('request',{}).get('provider_profile_ref')
        if profile is not None:context['provider_profile_ref']=profile
        return {'kind':'EXPERIENCE_SOURCE_WORK','context':context,'candidate_ref':value['record_ref'],
            'root_operation_id':request['root_operation_id'],'root_sha256':request['root_sha256'],
            'read_only':True,'model_calls':0,'processes_started':0}

    def history(self,pid,sid,ref,offset=0,expected_index_sha256=None):
        ref=ConfigurationRef.model_validate(ref).model_dump();self.sessions._manifest(pid,sid);index=self._index(pid,sid)
        if ref not in index['candidates']:raise NotFound('Exact history candidate is not indexed')
        if type(offset) is not int or not 0<=offset<=MAX_RECORDS:raise InvalidInput('Invalid experience history offset')
        index_sha=digest(index)
        if offset and expected_index_sha256!=index_sha:raise Conflict('Experience history changed; retain the old page and start a fresh inspection')
        rows=list(reversed(index['records']));cursor=min(offset,len(rows));entries=[];body_reads=0
        while cursor<len(rows) and len(entries)<PAGE and body_reads<PAGE:
            row=rows[cursor];cursor+=1;candidate=row.get('candidate_ref')
            if candidate is None:
                body_reads+=1;value=self._record(pid,sid,row['id'],row['kind'],row['request_sha256'],index)
                candidate=self._candidate_for_record(pid,sid,value,index)
            if candidate==ref:entries.append({'kind':row['kind'],'record_ref':{'id':row['id'],'revision':1,'sha256':row['record_sha256']},'request_sha256':row['request_sha256'],'candidate_ref':candidate})
        return {'kind':'EXPERIENCE_CANDIDATE_HISTORY','project_id':pid,'session_id':sid,'candidate_ref':ref,
            'entries':entries,'offset':min(offset,len(rows)),'next_offset':cursor if cursor<len(rows) else None,'index_sha256':index_sha,
            'legacy_body_reads':body_reads,'read_only':True,'model_calls':0,'metadata_writes':0}
