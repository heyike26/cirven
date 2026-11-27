"""Revocable source authority for task-derived capability data.

Uses the existing original-context selection/grant authority. A policy decision
never changes original files/conversations or claims physical erasure/past undo.
"""
from __future__ import annotations
from copy import deepcopy
import json
from typing import Literal
from pydantic import Field
from packages.domain.questions import RequestModel,Id,Hash
from packages.domain.store import Conflict,NotFound,InvalidInput
from packages.domain.tasks import digest,now,canonical
from .models import ConfigurationRef

MAX_DECISIONS=33


class ExperienceSourceDecision(RequestModel):
    operation_id:Id
    candidate_ref:ConfigurationRef
    expected_revision:int=Field(ge=0,le=MAX_DECISIONS,strict=True)
    action:Literal['WITHDRAW','RESTORE','FORGET']
    confirmed:bool=Field(strict=True)
    statement:str=Field(min_length=1,max_length=2000)


class ExperienceSourcePolicy:
    def __init__(self,candidates):self.candidates=candidates;self.sessions=candidates.sessions
    def _path(self,pid,sid,ref,*parts):return self.candidates._path(pid,sid,'source-policy',digest(ref),*parts)
    def _authority_path(self,pid,sid,ref):return self.candidates._path(pid,sid,'source-authority',digest(ref['id'])+'.json')

    def freeze(self,pid,sid,root):
        authority=self.sessions.context_authority;manifest,reference=self.sessions._manifest(pid,sid)
        native=root['basis']['native_session_id'];record=authority._read(manifest,{'session_id':native})
        if not record['complete']:raise Conflict('EXPERIENCE_SOURCE_UNVERIFIED: original context source record is incomplete')
        graph=authority.graph
        if graph is None:raise Conflict('EXPERIENCE_SOURCE_UNAVAILABLE: original context authority service is missing')
        inherited=authority._inherited(manifest,{(pid,sid)})
        selections=deepcopy(record['selections']);origins=set()
        for item in selections:
            graph.authorize_selection(**item)
            with self.sessions.store.connect() as db:
                row=db.execute('SELECT selection_json,selection_sha256 FROM context_selections WHERE id=?',(item['selection_id'],)).fetchone()
            if row is None:raise Conflict('Original experience context selection is missing')
            value=json.loads(row['selection_json'])
            if digest(value)!=item['selection_id'] or digest(value)!=row['selection_sha256']:raise Conflict('Original experience selection integrity failed')
            origins.update(selected['origin_id'] for selected in value['selected'])
            if len(origins)>1024:raise Conflict('Experience source authority exceeds its bounded dependency set')
        capability_sources=[]
        from packages.domain.experience_provenance import read
        refs=manifest.get('capability_configuration_refs',{})
        for kind in ['skills','mcp']:
            for native_ref in refs.get(kind,{}).values():
                config=self.sessions.capabilities._configuration(kind,native_ref)['configuration']
                if not config['enabled']:continue
                origin=read(self.sessions.capabilities,kind,native_ref)
                if origin is not None:
                    declaration=origin['source']
                    self.require(declaration['project_id'],declaration['session_id'],declaration['candidate_ref'])
                    if declaration not in capability_sources:capability_sources.append(declaration)
                    if len(capability_sources)>64:raise Conflict('Experience capability-source ancestry exceeds its bound')
        return {'schema':'eeas.experience-source-authority.v1','project_id':pid,'session_id':sid,'session_ref':reference,
            'root_sha256':root['sha256'],'native_session_id':native,'selections':selections,'origin_ids':sorted(origins),'capability_sources':capability_sources,
            'inherited_authority_sha256':digest(inherited) if inherited is not None else None,
            'scope':'FROZEN_ORIGINAL_CONTEXT_SELECTIONS_AND_EXPLICIT_CANDIDATE_DECISION'}

    def ensure_authority(self,pid,sid,value):
        proof=value.get('source_authority')
        if proof is None:return  # Legacy proposals remain readable, never silently trusted.
        if value.get('source_authority_sha256')!=digest(proof):raise Conflict('Candidate source authority hash differs')
        ref={'id':value['operation_id'],'revision':1,'sha256':digest(value)}
        saved={'candidate_ref':ref,'proof':proof,'proof_sha256':digest(proof)};path=self._authority_path(pid,sid,ref)
        if path.exists():
            if self.candidates._read(path)!=saved:raise Conflict('Original candidate source authority differs')
        else:self.sessions._write(path,saved)

    def _proof(self,pid,sid,ref):
        index=self.candidates._index(pid,sid)
        if ref not in index['candidates']:raise NotFound('Exact candidate source authority is not indexed')
        path=self._authority_path(pid,sid,ref)
        if not path.exists():return None
        value=self.candidates._read(path)
        entry=next(row for row in index['records'] if row['id']==ref['id'])
        if (not isinstance(value,dict) or value.get('candidate_ref')!=ref or value.get('proof_sha256')!=entry.get('source_authority_sha256') or value.get('proof_sha256')!=digest(value.get('proof'))
                or value['proof'].get('schema')!='eeas.experience-source-authority.v1'
                or value['proof'].get('project_id')!=pid or value['proof'].get('session_id')!=sid):
            raise Conflict('Candidate source authority metadata changed')
        return value

    def _head(self,pid,sid,ref):
        path=self._path(pid,sid,ref,'head.json')
        value=self.candidates._read(path) if path.exists() else {'schema':'eeas.experience-source-policy.v1','candidate_ref':ref,'state':'ACTIVE','revision':0,'entries':[]}
        if (not isinstance(value,dict) or set(value) not in [{'schema','candidate_ref','state','revision','entries'},{'schema','candidate_ref','state','revision','entries','pending_record'}]
                or value['schema']!='eeas.experience-source-policy.v1' or value['candidate_ref']!=ref
                or value['state'] not in ['ACTIVE','WITHDRAWN','FORGOTTEN'] or type(value['revision']) is not int
                or not 0<=value['revision']<=MAX_DECISIONS or not isinstance(value['entries'],list)
                or len(value['entries'])!=value['revision'] or any(not isinstance(r,dict) or set(r)!={'operation_id','request_sha256','record_sha256'} for r in value['entries'])):
            raise Conflict('Experience source policy is corrupt; original data retained')
        if len({row['operation_id'] for row in value['entries']})!=len(value['entries']):raise Conflict('Experience source decisions are duplicated')
        if not value['revision']:
            if value['state']!='ACTIVE' or value.get('pending_record') is not None:raise Conflict('Empty source policy basis changed')
        else:
            last=value['entries'][-1];pending=value.get('pending_record')
            record=pending if pending is not None else self.candidates._read(self._path(pid,sid,ref,'records',digest(last['operation_id'])+'.json'))
            if (not isinstance(record,dict) or digest(record)!=last['record_sha256'] or record.get('operation_id')!=last['operation_id']
                    or record.get('request_sha256')!=last['request_sha256'] or digest(record.get('request'))!=last['request_sha256']
                    or record.get('candidate_ref')!=ref or record.get('revision')!=value['revision'] or record.get('state')!=value['state']):
                raise Conflict('Source policy differs from its exact current decision')
        return value

    def status(self,pid,sid,ref,*,ignore_local=False,seen=None):
        ref=ConfigurationRef.model_validate(ref).model_dump();seen=set(seen or ());key=(pid,sid,digest(ref))
        if key in seen or len(seen)>=64:raise Conflict('Experience source ancestry exceeds its exact bound')
        seen.add(key);proof=self._proof(pid,sid,ref);head=self._head(pid,sid,ref)
        result={'schema':'eeas.experience-source-status.v1','project_id':pid,'session_id':sid,'candidate_ref':ref,
            'local_state':head['state'],'revision':head['revision'],'policy_sha256':digest(head),
            'availability':'ACTIVE','pending_finalization':head.get('pending_record') is not None,'forgotten':head['state']=='FORGOTTEN','physical_erasure_claimed':False,
            'past_disclosures_reversed':False,'original_files_deleted':False,'original_conversations_deleted':False}
        if not ignore_local and head['state']!='ACTIVE':return {**result,'availability':head['state']}
        if head.get('pending_record') is not None and head['state']=='ACTIVE':return {**result,'availability':'SOURCE_DECISION_PENDING'}
        if proof is None:return {**result,'availability':'LEGACY_SOURCE_AUTHORITY_UNVERIFIED'}
        result['authority_sha256']=proof['proof_sha256'];frozen=proof['proof'];authority=self.sessions.context_authority
        try:
            dependency_states=[]
            for declaration in frozen.get('capability_sources',[]):
                dependency=self.status(declaration['project_id'],declaration['session_id'],declaration['candidate_ref'],seen=seen)
                dependency_states.append({'source':declaration,'status_sha256':digest(dependency)})
                result['dependency_authority_sha256']=digest(dependency_states)
                if dependency['forgotten']:return {**result,'availability':'FORGOTTEN','forgotten':True}
                if dependency['availability']!='ACTIVE':return {**result,'availability':'SOURCE_WITHDRAWN_OR_UNAVAILABLE'}
            manifest,reference=self.sessions._manifest(pid,sid)
            if reference!=frozen['session_ref']:raise Conflict('Original source owner differs')
            current=authority._read(manifest,{'session_id':frozen['native_session_id']})
            if not current['complete'] or any(s not in current['selections'] for s in frozen['selections']):raise Conflict('Original source authority is incomplete')
            graph=authority.graph
            if graph is None:raise NotFound('Original source authority metadata service is unavailable')
            with self.sessions.store.connect() as db:
                ids=frozen['origin_ids']
                for offset in range(0,len(ids),128):
                    chunk=ids[offset:offset+128];marks=','.join('?' for _ in chunk)
                    rows=db.execute('SELECT id,forgotten FROM context_origins WHERE id IN ('+marks+')',chunk).fetchall()
                    if len(rows)!=len(chunk):raise NotFound('An original source is unavailable')
                    if any(row['forgotten'] for row in rows):return {**result,'availability':'FORGOTTEN','forgotten':True}
            for _ in range(2):
                epoch=graph.authority_epoch()
                for item in frozen['selections']:
                    if graph.authorize_selection(**item)['authority_epoch']!=epoch:break
                else:
                    inherited=authority._inherited(manifest,{(pid,sid)})
                    if (digest(inherited) if inherited is not None else None)!=frozen['inherited_authority_sha256']:raise Conflict('Inherited experience source authority changed')
                    if graph.authority_epoch()==epoch:return result
            raise Conflict('Experience source authority changed during admission')
        except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError):
            return {**result,'availability':'SOURCE_WITHDRAWN_OR_UNAVAILABLE'}

    def require(self,pid,sid,ref):
        state=self.status(pid,sid,ref)
        if state['availability']!='ACTIVE':raise Conflict('EXPERIENCE_SOURCE_'+state['availability']+': original data retained; future use is blocked')
        return state

    def decision(self,pid,sid,body):
        body=ExperienceSourceDecision.model_validate(body);request=body.model_dump();ref=body.candidate_ref.model_dump()
        if not body.confirmed:raise InvalidInput('Confirm this exact source decision first')
        with self.candidates._guard(pid,sid):
            if ref not in self.candidates._index(pid,sid)['candidates']:raise NotFound('Exact candidate source policy is not indexed')
            head=self._head(pid,sid,ref);path=self._path(pid,sid,ref,'records',digest(body.operation_id)+'.json')
            old=self.candidates._read(path) if path.exists() else (head.get('pending_record') if head.get('pending_record',{}).get('operation_id')==body.operation_id else None)
            if old is not None:
                if old.get('request_sha256')!=digest(request) or old.get('request')!=request:raise Conflict('Original source decision binds another input')
                if head['revision']==old['previous_revision']:
                    if head['state']!=old['previous_state']:raise Conflict('Original source decision basis changed')
                    self._publish(pid,sid,ref,head,old)
                elif not any(row['operation_id']==body.operation_id and row['record_sha256']==digest(old) for row in head['entries']):
                    raise Conflict('A newer source decision was retained')
                self._finish_pending(pid,sid,ref,old)
                return {'decision':old,'current':self.status(pid,sid,ref),'automatic_replay':False}
            if head['revision']!=body.expected_revision:raise Conflict('Experience source decision changed; retain the newer decision')
            if head['state']=='FORGOTTEN':raise Conflict('This candidate was forgotten; create a separately reviewed source candidate')
            target={'WITHDRAW':'WITHDRAWN','RESTORE':'ACTIVE','FORGET':'FORGOTTEN'}[body.action]
            if target=='ACTIVE' and self.status(pid,sid,ref,ignore_local=True)['availability']!='ACTIVE':
                raise Conflict('The original source is still withdrawn; local restore does not renew its grants')
            # Reserve two final negative decisions after normal control history fills.
            limit=31 if target=='ACTIVE' else 32 if target=='WITHDRAWN' else 33
            if head['revision']>=limit:raise Conflict('Source decision history is full; originals retained')
            value={'schema':'eeas.experience-source-decision.v1','project_id':pid,'session_id':sid,'candidate_ref':ref,
                'operation_id':body.operation_id,'request_sha256':digest(request),'request':request,
                'previous_revision':head['revision'],'previous_state':head['state'],'revision':head['revision']+1,'state':target,
                'recorded_at':now(),'physical_erasure_claimed':False,'original_files_deleted':False,
                'original_conversations_deleted':False,'past_disclosures_reversed':False}
            self._publish(pid,sid,ref,head,value);self._finish_pending(pid,sid,ref,value)
            return {'decision':value,'current':self.status(pid,sid,ref),'automatic_replay':False}

    def _publish(self,pid,sid,ref,head,value):
        entry={'operation_id':value['operation_id'],'request_sha256':value['request_sha256'],'record_sha256':digest(value)}
        updated={**head,'revision':value['revision'],'state':value['state'],'entries':[*head['entries'],entry],'pending_record':value}
        self.sessions._write(self._path(pid,sid,ref,'head.json'),updated,replace=True)

    def _finish_pending(self,pid,sid,ref,value):
        path=self._path(pid,sid,ref,'records',digest(value['operation_id'])+'.json')
        if path.exists():
            if self.candidates._read(path)!=value:raise Conflict('Original source decision record differs')
        else:self.sessions._write(path,value)
        head=self._head(pid,sid,ref)
        if head.get('pending_record')==value:
            head.pop('pending_record',None)
            self.sessions._write(self._path(pid,sid,ref,'head.json'),head,replace=True)

    def origin_status(self,source):
        return self.status(source['project_id'],source['session_id'],source['candidate_ref'])

    def inspect_decision(self,pid,sid,ref,operation,request_sha):
        """Exact historical control identity, including an interrupted journal.

        No source body, user statement, or positive recovery is returned/run.
        A newer head never substitutes for the named original decision.
        """
        from packages.domain.questions import Id as IdType,Hash as HashType
        from pydantic import TypeAdapter
        operation=TypeAdapter(IdType).validate_python(operation);request_sha=TypeAdapter(HashType).validate_python(request_sha)
        ref=ConfigurationRef.model_validate(ref).model_dump()
        self.sessions._manifest(pid,sid)
        if ref not in self.candidates._index(pid,sid)['candidates']:raise NotFound('Exact source candidate is not indexed')
        head=self._head(pid,sid,ref);path=self._path(pid,sid,ref,'records',digest(operation)+'.json')
        pending=head.get('pending_record')
        record=self.candidates._read(path) if path.exists() else (pending if pending is not None and pending['operation_id']==operation else None)
        if record is None:raise NotFound('Original source decision has no saved receipt; do not infer it was unsent')
        entry=next((row for row in head['entries'] if row['operation_id']==operation),None)
        if (record.get('operation_id')!=operation or record.get('request_sha256')!=request_sha
                or digest(record.get('request'))!=request_sha or record.get('candidate_ref')!=ref
                or entry is None or entry['request_sha256']!=request_sha or entry['record_sha256']!=digest(record)):
            raise Conflict('Original source decision identity differs')
        try:current=self.status(pid,sid,ref)
        except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError):
            current={'availability':'SOURCE_METADATA_UNAVAILABLE','local_state':head['state'],
                'revision':head['revision'],'forgotten':head['state']=='FORGOTTEN','physical_erasure_claimed':False}
        return {'kind':'EXPERIENCE_SOURCE_DECISION_OBSERVATION','project_id':pid,'session_id':sid,
            'operation_id':operation,'request_sha256':request_sha,'candidate_ref':ref,
            'decision_ref':{'id':operation,'revision':record['revision'],'sha256':digest(record)},
            'decision_state':record['state'],'current':current,
            'local_finalization_needed':pending==record,'read_only':True,'metadata_writes':0,
            'model_calls':0,'processes_started':0,'automatic_replay':False,'content_withheld':True}

    def public(self,pid,sid,value):
        if not isinstance(value,dict):return value
        ref=value.get('candidate_ref') or value.get('basis',{}).get('candidate_ref')
        if ref is None and value.get('kind')=='PROPOSAL':ref=value.get('record_ref')
        if ref is None and value.get('kind')=='REVIEW':ref=value.get('request',{}).get('candidate_ref')
        if ref is None:return value
        state=self.status(pid,sid,ref)
        root_meta=({key:value['request'][key] for key in ['root_operation_id','root_sha256']} if value.get('kind')=='PROPOSAL' else {})
        validation_meta={}
        if value.get('kind') in {'EXPERIENCE_TOOL_VALIDATION','EXPERIENCE_SKILL_VALIDATION'}:
            # A saved result proves persistence, not that its external effects or
            # owned process shutdown are known. Keep this distinction after the
            # source body is withheld as well as on the active presentation.
            unknown=value.get('status') not in {'PASS','FAIL','SOURCE_BLOCKED'} or any(case.get('status')=='UNKNOWN' for case in value.get('cases',[]))
            validation_meta['execution_status']='UNKNOWN' if unknown else 'RETURNED'
            if value.get('status')!='UNKNOWN_OR_IN_PROGRESS':validation_meta['result_sha256']=digest(value)
        if state['availability']=='ACTIVE':
            result={**value,**root_meta,**validation_meta,'source_policy':state}
            if value.get('kind')=='REVIEW':
                result['case_json']={case['id']:{'input_json':canonical(case['input']),'expected_result_json':canonical(case['expected_result'])} for case in value['request']['cases'] if case['kind']=='MCP_TOOL'}
            if value.get('kind')=='EXPERIENCE_TOOL_VALIDATION':
                result['case_output_json']={case['id']:{'structured_json':canonical(case['result'].get('structuredContent')),'non_text_count':sum(not isinstance(part,dict) or part.get('type')!='text' for part in case['result'].get('content',[]))} for case in value.get('cases',[]) if isinstance(case.get('result'),dict)}
            return result
        # Original private records remain for integrity/rollback; public/model
        # consumers get references/control hashes only after withdrawal/forget.
        keys=['kind','operation_id','request_sha256','record_ref','intent_ref','candidate_ref','action','status',
            'promotion_review_sha256','rollback_review_sha256','read_only','automatic_replay','local_finalization_needed',
            'capability_control','pause_scope','decision_ref','decision_state','control_record_kind','configuration_finalization_needed']
        result={k:deepcopy(value[k]) for k in keys if k in value}
        observation=value.get('observation')
        if isinstance(observation,dict):result['observation']={k:deepcopy(observation[k]) for k in ['status','current_configuration_ref','automatic_replay'] if k in observation}
        if isinstance(value.get('basis'),dict):result['basis']={k:deepcopy(value['basis'][k]) for k in ['action','current_configuration_ref','capability_id','native_kind'] if k in value['basis']}
        return {**result,**root_meta,**validation_meta,'candidate_ref':ref,'source_policy':state,'content_withheld':True,'enabled':False,'qualified':False}
