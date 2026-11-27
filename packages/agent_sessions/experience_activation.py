"""Explicit validated experience promotion/rollback via NativeCapabilities.

Native configuration snapshots remain the sole runtime authority. A global
per-capability claim prevents another source owner from replacing a pending
metadata save. Unknown outcomes are inspected, not submitted under a new nonce.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
from typing import Literal
from pydantic import Field
from packages.domain.questions import RequestModel,Id,Hash
from packages.domain.native_capabilities import _skill,_mcp
from packages.domain.store import Conflict,InvalidInput,NotFound
from packages.domain.tasks import canonical,digest,now
from .models import ConfigurationRef
from .experience_candidates import ExperienceCandidates
from .experience_tool_validation import ExperienceToolValidation
from .experience_skill_validation import ExperienceSkillValidation


class ExperiencePromotePrepare(RequestModel):
    candidate_ref:ConfigurationRef
    review_ref:ConfigurationRef
    validation_kind:Literal['MCP_TOOL','SKILL']
    validation_operation_id:Id
    validation_request_sha256:Hash
    validation_result_sha256:Hash


class ExperiencePromote(ExperiencePromotePrepare):
    operation_id:Id
    promotion_review_sha256:Hash
    promotion_confirmed:bool=Field(strict=True)


class ExperienceRollbackPrepare(RequestModel):
    activation_operation_id:Id
    activation_request_sha256:Hash
    expected_current_configuration_ref:ConfigurationRef
    mode:Literal['DISABLE','RESTORE_PREVIOUS']


class ExperienceRollback(ExperienceRollbackPrepare):
    operation_id:Id
    rollback_review_sha256:Hash
    rollback_confirmed:bool=Field(strict=True)


class ExperienceActivationRecover(RequestModel):
    operation_id:Id
    request_sha256:Hash
    mode:Literal['INSPECT','FINALIZE_LOCAL_RECORD','COMPLETE_EXACT_SAVED_METADATA']='INSPECT'
    recovery_confirmed:bool=Field(default=False,strict=True)


class ExperienceActivation:
    def __init__(self,candidates:ExperienceCandidates):
        self.candidates=candidates;self.sessions=candidates.sessions;self.native=self.sessions.capabilities
        if self.native is not None:self.native.experience_source_reader=candidates.source_policy().origin_status

    def _path(self,pid,sid,operation,*parts):return self.candidates._path(pid,sid,'activation',digest(operation),*parts)

    def _claim_path(self,kind,ident):return self.native._path('experience-claims',digest([kind,ident.casefold()])+'.json')

    def _validated(self,pid,sid,body):
        c=self.candidates;c.source_policy().require(pid,sid,body.candidate_ref);candidate=c.candidate(pid,sid,body.candidate_ref);review=c.saved_record(pid,sid,body.review_ref,'REVIEW')
        if review['candidate_ref']!=body.candidate_ref.model_dump() or candidate['request']['proposal']['kind']!=body.validation_kind:
            raise Conflict('Promotion review and exact candidate disagree')
        validation=(ExperienceToolValidation(c) if body.validation_kind=='MCP_TOOL' else ExperienceSkillValidation(c))
        result=validation.inspect(pid,sid,body.validation_operation_id,body.validation_request_sha256)
        if (digest(result)!=body.validation_result_sha256 or result.get('status')!='PASS' or result.get('qualified') is not True
                or result.get('candidate_ref')!=body.candidate_ref.model_dump() or result.get('review_ref')!=body.review_ref.model_dump()):
            raise Conflict('Promotion requires the exact saved passing validation; unknown and failed results remain unqualified')
        plan=review['request']['cases'];rows=result.get('cases')
        if not isinstance(rows,list) or len(rows)!=len(plan) or [r.get('id') for r in rows]!=[r['id'] for r in plan]:
            raise Conflict('Validation does not cover all fixed review cases')
        for case,row in zip(plan,rows):
            if row.get('status')!='PASS' or row.get('purpose')!=case['purpose']:raise Conflict('A reviewed case is not passed')
            if body.validation_kind=='MCP_TOOL':
                expected=case['expected_result'];actual=row.get('result')
                if (actual!=expected or digest(actual)!=digest(expected) or digest(actual)!=row.get('result_sha256') or digest(expected)!=row.get('expected_result_sha256')
                        or row.get('tool_name')!=case['tool_name'] or row.get('input_sha256')!=digest(case['input'])
                        or row.get('call_reserved') is not True or row.get('is_error')!=expected['isError']):
                    raise Conflict('Tool validation bytes differ from the fixed expected output')
            else:
                answer=row.get('answer');ref=row.get('owner_ref');message=row.get('original_message_ref')
                if (not isinstance(answer,str) or not answer or hashlib.sha256(answer.encode()).hexdigest()!=row.get('answer_sha256')
                        or row.get('skill_loaded') is not True or row.get('skill_invocation_count',0)<1 or row.get('unexpected_tool_calls')!=0
                        or any(text not in answer for text in case['required_text']) or any(text in answer for text in case['forbidden_text'])
                        or not isinstance(ref,dict) or not isinstance(message,dict)):
                    raise Conflict('Skill validation output or actual load/invocation evidence differs')
                manifest,_=self.sessions._manifest(pid,ref['id'],ref)
                admission=manifest.get('experience_validation_admission') or {}
                op=self.sessions.operation(pid,ref['id'],message['operation_id'])
                if (admission.get('source_session_id')!=sid or admission.get('operation_id')!=body.validation_operation_id
                        or admission.get('request_sha256')!=body.validation_request_sha256 or admission.get('case_id')!=case['id']
                        or op.get('status')!='RETURNED' or op.get('request_sha256')!=message.get('request_sha256')
                        or op.get('native_message_id')!=message.get('native_message_id')):
                    raise Conflict('Skill result is not its exact acknowledged original test operation')
                validation.binding(manifest,live=False)
        return candidate,review,result

    def _configuration(self,candidate,*,enabled=True):
        proposal=candidate['request']['proposal']
        if proposal['kind']=='SKILL':return 'skills',proposal['id'],{**_skill(proposal['content'],proposal['id']),'enabled':enabled}
        return 'mcp',proposal['id'],_mcp({**candidate['configuration'],'enabled':enabled})

    def prepare(self,pid,sid,body):
        body=ExperiencePromotePrepare.model_validate(body);candidate,review,result=self._validated(pid,sid,body)
        kind,ident,configuration=self._configuration(candidate)
        current=self.native._index()[kind].get(ident);previous=self.native._configuration(kind,current)['configuration'] if current else None
        basis={'action':'PROMOTE','project_id':pid,'session_id':sid,'candidate_ref':body.candidate_ref.model_dump(),
            'review_ref':body.review_ref.model_dump(),'validation_kind':body.validation_kind,
            'validation_operation_id':body.validation_operation_id,'validation_request_sha256':body.validation_request_sha256,
            'validation_result_sha256':body.validation_result_sha256,'native_kind':kind,'capability_id':ident,
            'current_configuration_ref':current,'configuration_sha256':digest(configuration),
            'previous_configuration_sha256':digest(previous) if previous is not None else None}
        return {'kind':'EXPERIENCE_PROMOTION_PREVIEW','basis':basis,'promotion_review_sha256':digest(basis),
            'previous_configuration':deepcopy(previous),'proposed_configuration':deepcopy(configuration),
            'validation_scope':result['qualification_scope'],'read_only':True,'permission_granted':False,
            'model_calls':0,'processes_started':0,'applies_to':'FUTURE_EXPLICIT_OWNER_BINDING_ONLY'}

    def _intent(self,pid,sid,operation,kind,request,prepared):
        index=self.candidates._index(pid,sid);old=self.candidates._record(pid,sid,operation,kind,digest(request),index)
        if old is not None:
            self.candidates._append(pid,sid,index,old);return old,False
        basis=prepared['basis'];claim_path=self._claim_path(basis['native_kind'],basis['capability_id'])
        if claim_path.exists():
            claim=self.native.experience_claim(basis['native_kind'],basis['capability_id'])
            if claim.get('state') not in {'CLOSED'}:raise Conflict('Another original experience configuration save requires inspection before replacement')
        native_operation='experience_'+digest([pid,sid,operation])
        value={'schema':'eeas.experience-activation-intent.v1','kind':kind,'project_id':pid,'session_id':sid,
            'operation_id':operation,'request_sha256':digest(request),'request':request,'basis':deepcopy(basis),
            'configuration':deepcopy(prepared['proposed_configuration']),'previous_configuration':deepcopy(prepared['previous_configuration']),
            'native_operation_id':native_operation,'created_at':now(),'automatic_replay':False}
        self.candidates._append(pid,sid,index,value)
        self.native._write(claim_path,{'schema':'eeas.experience-capability-claim.v1','state':'ACTIVE',
            'project_id':pid,'session_id':sid,'operation_id':operation,'request_sha256':digest(request),
            'native_operation_id':native_operation,'native_kind':basis['native_kind'],'capability_id':basis['capability_id']},replace=True)
        return value,True

    def _before_save(self,intent,index,*,paused_control=None):
        basis=intent['basis'];kind=basis['native_kind'];ident=basis['capability_id']
        claim=self.native.experience_claim(kind,ident)
        if claim is None or claim.get('state')!='ACTIVE' or claim.get('native_operation_id')!=intent['native_operation_id']:
            raise Conflict('Original experience save no longer owns this capability claim')
        if index[kind].get(ident)!=basis['current_configuration_ref']:
            raise Conflict('Native configuration changed; original user edits were retained')
        if basis['current_configuration_ref'] is not None:self.native._configuration(kind,basis['current_configuration_ref'])
        if intent['kind']=='ACTIVATION' and paused_control is None:self.candidates.source_policy().require(intent['project_id'],intent['session_id'],intent['request']['candidate_ref'])
        configuration=intent['configuration']
        if digest(configuration)!=basis['configuration_sha256']:raise Conflict('Original intended configuration bytes changed')
        if kind=='skills':_skill(configuration['content'],ident)
        elif configuration['enabled'] and paused_control is None:_mcp(configuration)

    def _native_observation(self,intent):
        basis=intent['basis'];kind=basis['native_kind'];ident=basis['capability_id']
        try:value=self.native.operation(intent['native_operation_id'])
        except NotFound:return {'status':'UNKNOWN_NO_SAVED_NATIVE_RECEIPT','automatic_replay':False}
        expected=intent['basis']['current_configuration_ref'];revision=(expected['revision'] if expected else 0)+1
        if (value['kind']!=kind or value['configuration']!=intent['configuration']
                or value['receipt']['config_ref']['id']!=ident or value['receipt']['config_ref']['revision']!=revision):
            raise Conflict('Native saved operation is not the original experience configuration')
        current=self.native._index()[kind].get(ident);saved=value['receipt']['config_ref']
        if current==saved:status='APPLIED_CURRENT'
        elif current==expected:status='SAVED_POINTER_UNCONFIRMED'
        elif current and current['revision']>saved['revision']:status='SAVED_SUPERSEDED'
        else:raise Conflict('Native capability pointer history differs from the original save')
        return {'status':status,'native_operation':value,'current_configuration_ref':current,'automatic_replay':False}

    def inspect(self,pid,sid,operation,request_sha):
        intent=self.candidates.operation(pid,sid,operation,request_sha)
        if intent['kind'] not in {'ACTIVATION','ROLLBACK'}:raise Conflict('Not the original experience activation or rollback operation')
        path=self._path(pid,sid,operation,'result.json');result=self.candidates._read(path) if path.exists() else None
        if result is not None and (result.get('request_sha256')!=request_sha or result.get('operation_id')!=operation):raise Conflict('Local activation result changed')
        observation=self._native_observation(intent)
        claim_path=self._claim_path(intent['basis']['native_kind'],intent['basis']['capability_id'])
        claim=self.native.experience_claim(intent['basis']['native_kind'],intent['basis']['capability_id'])
        return {'kind':'EXPERIENCE_CONFIGURATION_OPERATION','project_id':pid,'session_id':sid,'operation_id':operation,
            'request_sha256':request_sha,'intent_ref':intent['record_ref'],'action':intent['basis']['action'],
            'candidate_ref':(intent['request']['candidate_ref'] if intent['kind']=='ACTIVATION' else self.candidates.saved_record(pid,sid,intent['basis']['source_activation_ref'],'ACTIVATION')['request']['candidate_ref']),
            'observation':observation,'local_result':deepcopy(result),
            'local_finalization_needed':bool(claim and claim.get('state')=='ACTIVE' and claim.get('native_operation_id')==intent['native_operation_id']),
            'permission_granted':False,'model_calls':0,'processes_started':0,'read_only':True,'automatic_replay':False}

    def _finalize(self,pid,sid,intent):
        observation=self._native_observation(intent)
        if observation['status'] not in {'APPLIED_CURRENT','SAVED_SUPERSEDED'}:raise Conflict('Only a confirmed saved native configuration can finish its local record')
        result={'kind':'EXPERIENCE_CONFIGURATION_SAVED','project_id':pid,'session_id':sid,'operation_id':intent['operation_id'],
            'request_sha256':intent['request_sha256'],'intent_ref':intent['record_ref'],'native_operation_id':intent['native_operation_id'],
            'configuration_ref':observation['native_operation']['receipt']['config_ref'],
            'previous_configuration_ref':intent['basis']['current_configuration_ref'],'action':intent['basis']['action'],
            'recorded_at':now(),'permission_granted':False,'model_calls':0,'processes_started':0,
            'applies_to':'FUTURE_EXPLICIT_OWNER_BINDING_ONLY','automatic_replay':False}
        with self.candidates._guard(pid,sid):
            path=self._path(pid,sid,intent['operation_id'],'result.json')
            if path.exists():
                old=self.candidates._read(path)
                if {k:v for k,v in old.items() if k!='recorded_at'}!={k:v for k,v in result.items() if k!='recorded_at'}:raise Conflict('Original local activation record differs')
                result=old
            else:self.sessions._write(path,result)
            claim_path=self._claim_path(intent['basis']['native_kind'],intent['basis']['capability_id'])
            claim=self.native.experience_claim(intent['basis']['native_kind'],intent['basis']['capability_id'])
            if claim and claim.get('state')=='ACTIVE' and claim.get('native_operation_id')==intent['native_operation_id']:
                self.native._write(claim_path,{**claim,'state':'CLOSED','closed_at':now()},replace=True)
            # A newer claim belongs to another exact operation. Historical
            # receipt finalization never clears or replaces that claim.
        return self.inspect(pid,sid,intent['operation_id'],intent['request_sha256'])

    def _save(self,pid,sid,intent,*,paused_control=None):
        basis=intent['basis'];previous=basis['current_configuration_ref'];expected=previous['revision'] if previous else 0
        source=None
        if intent['kind']=='ACTIVATION':
            source={'project_id':pid,'session_id':sid,'candidate_ref':intent['request']['candidate_ref'],
                'activation_operation_id':intent['operation_id'],'activation_request_sha256':intent['request_sha256']}
        elif basis['action']=='DISABLE':
            from packages.domain.experience_provenance import read
            provenance=read(self.native,basis['native_kind'],basis['current_configuration_ref'])
            source=provenance['source'] if provenance else None
        elif basis['action']=='RESTORE_PREVIOUS':
            original=self.candidates.saved_record(pid,sid,basis['source_activation_ref'],'ACTIVATION')
            previous_ref=original['basis']['current_configuration_ref']
            if previous_ref is not None:
                from packages.domain.experience_provenance import read
                provenance=read(self.native,basis['native_kind'],previous_ref)
                source=provenance['source'] if provenance else None
        self.native._save(basis['native_kind'],basis['capability_id'],expected,intent['native_operation_id'],intent['configuration'],
            before_save=lambda connection,index:self._before_save(intent,index,paused_control=paused_control),experience_source=source,
            paused_control=paused_control)
        # Use the exact persisted view, including its immutable record_ref.
        return self._finalize(pid,sid,self.candidates.operation(pid,sid,intent['operation_id'],intent['request_sha256']))

    def promote(self,pid,sid,body):
        body=ExperiencePromote.model_validate(body)
        if not body.promotion_confirmed:raise InvalidInput('Confirm the exact validation and configuration difference first')
        request=body.model_dump();c=self.candidates
        with c._guard(pid,sid):
            index=c._index(pid,sid);old=c._record(pid,sid,body.operation_id,'ACTIVATION',digest(request),index)
            if old is not None:
                c._append(pid,sid,index,old);return self.inspect(pid,sid,body.operation_id,digest(request))
            prepared=self.prepare(pid,sid,ExperiencePromotePrepare(**body.model_dump(include=set(ExperiencePromotePrepare.model_fields))))
            if prepared['promotion_review_sha256']!=body.promotion_review_sha256:raise Conflict('Promotion preview changed; original input retained')
            intent,_=self._intent(pid,sid,body.operation_id,'ACTIVATION',request,prepared)
        return self._save(pid,sid,intent)

    def prepare_rollback(self,pid,sid,body):
        body=ExperienceRollbackPrepare.model_validate(body);source=self.candidates.operation(pid,sid,body.activation_operation_id,body.activation_request_sha256)
        if source['kind']!='ACTIVATION':raise Conflict('Rollback must name its original experience activation')
        observation=self._native_observation(source)
        if observation['status']!='APPLIED_CURRENT' or observation['current_configuration_ref']!=body.expected_current_configuration_ref.model_dump():
            raise Conflict('A newer native configuration or pending original save was retained; rollback did not overwrite it')
        basis=source['basis'];configuration=deepcopy(source['configuration'])
        if body.mode=='RESTORE_PREVIOUS':
            if source['previous_configuration'] is None:raise Conflict('No earlier configuration exists; disable this candidate instead')
            configuration=deepcopy(source['previous_configuration'])
        else:configuration['enabled']=False
        if basis['native_kind']=='skills':_skill(configuration['content'],basis['capability_id'])
        elif configuration['enabled']:_mcp(configuration)
        result_basis={'action':body.mode,'project_id':pid,'session_id':sid,'source_activation_ref':source['record_ref'],
            'native_kind':basis['native_kind'],'capability_id':basis['capability_id'],
            'current_configuration_ref':body.expected_current_configuration_ref.model_dump(),'configuration_sha256':digest(configuration),
            'previous_configuration_sha256':digest(source['configuration'])}
        return {'kind':'EXPERIENCE_ROLLBACK_PREVIEW','candidate_ref':source['request']['candidate_ref'],'basis':result_basis,'rollback_review_sha256':digest(result_basis),
            'proposed_configuration':configuration,'previous_configuration':deepcopy(source['configuration']),
            'read_only':True,'permission_granted':False,'model_calls':0,'processes_started':0}

    def rollback(self,pid,sid,body):
        body=ExperienceRollback.model_validate(body)
        if not body.rollback_confirmed:raise InvalidInput('Confirm this exact previous configuration or disable action')
        request=body.model_dump();c=self.candidates
        with c._guard(pid,sid):
            index=c._index(pid,sid);old=c._record(pid,sid,body.operation_id,'ROLLBACK',digest(request),index)
            if old is not None:
                c._append(pid,sid,index,old);return self.inspect(pid,sid,body.operation_id,digest(request))
            prepared=self.prepare_rollback(pid,sid,ExperienceRollbackPrepare(**body.model_dump(include=set(ExperienceRollbackPrepare.model_fields))))
            if prepared['rollback_review_sha256']!=body.rollback_review_sha256:raise Conflict('Rollback preview changed')
            intent,_=self._intent(pid,sid,body.operation_id,'ROLLBACK',request,prepared)
        return self._save(pid,sid,intent)

    def recover(self,pid,sid,body):
        body=ExperienceActivationRecover.model_validate(body)
        if body.mode=='INSPECT':return self.inspect(pid,sid,body.operation_id,body.request_sha256)
        if not body.recovery_confirmed:raise InvalidInput('Explicit exact original metadata recovery is required')
        intent=self.candidates.operation(pid,sid,body.operation_id,body.request_sha256)
        if intent['kind'] not in {'ACTIVATION','ROLLBACK'}:raise Conflict('Not the original configuration action')
        observation=self._native_observation(intent)
        if observation['status'] in {'APPLIED_CURRENT','SAVED_SUPERSEDED'}:return self._finalize(pid,sid,intent)
        if body.mode=='COMPLETE_EXACT_SAVED_METADATA' and observation['status']=='SAVED_POINTER_UNCONFIRMED':
            return self._save(pid,sid,intent)
        raise Conflict('Original native save is not proven; no positive call was replayed')
