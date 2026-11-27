"""Explicit pause, exact local metadata completion, and reviewed resume.

An unknown native save is never a tool/model retry: its original immutable
configuration can be completed only while an independent negative overlay
excludes the entire capability from every future binding.
"""
from copy import deepcopy
from typing import Literal
from pydantic import Field
from packages.domain.questions import RequestModel,Id,Hash
from packages.domain.store import Conflict,InvalidInput
from packages.domain.tasks import digest,now
from packages.domain import experience_controls as controls
from packages.domain.experience_provenance import status as source_status
from packages.domain.native_capabilities import _mcp
from .models import ConfigurationRef
from .experience_activation import ExperienceActivation,ExperiencePromotePrepare


class ExperiencePendingControl(RequestModel):
    operation_id:Id
    activation_operation_id:Id
    activation_request_sha256:Hash
    expected_control_revision:int=Field(ge=0,le=65,strict=True)
    mode:Literal['PAUSE','COMPLETE_PAUSED_METADATA','RESUME_PAUSED']
    expected_current_configuration_ref:ConfigurationRef|None=None
    confirmed:bool=Field(strict=True)


class ExperiencePending:
    def __init__(self,candidates):
        self.candidates=candidates;self.action=ExperienceActivation(candidates);self.native=self.action.native

    def _intent(self,pid,sid,operation,request_sha):
        intent=self.candidates.operation(pid,sid,operation,request_sha)
        if intent['kind'] not in {'ACTIVATION','ROLLBACK'}:raise Conflict('An exact original configuration action is required')
        return intent

    def inspect(self,pid,sid,operation,request_sha):
        intent=self._intent(pid,sid,operation,request_sha);basis=intent['basis']
        return {**self.action.inspect(pid,sid,operation,request_sha),
            'capability_control':controls.view(self.native,basis['native_kind'],basis['capability_id']),
            'pause_scope':'FUTURE_BINDINGS_AND_REQUEST_ADMISSION_NOT_PAST_OR_INFLIGHT_UNDO'}

    def inspect_control(self,pid,sid,activation_operation,activation_sha,operation,request_sha,mode):
        """Look up an exact control/metadata-completion packet without replay."""
        from itertools import islice
        from pydantic import TypeAdapter
        operation=TypeAdapter(Id).validate_python(operation);request_sha=TypeAdapter(Hash).validate_python(request_sha)
        mode=TypeAdapter(Literal['PAUSE','COMPLETE_PAUSED_METADATA','RESUME_PAUSED']).validate_python(mode)
        intent=self._intent(pid,sid,activation_operation,activation_sha);basis=intent['basis'];kind=basis['native_kind'];ident=basis['capability_id']
        head=controls.head(self.native,kind,ident);record=None
        if mode=='COMPLETE_PAUSED_METADATA':
            paths=list(islice(controls.path(self.native,kind,ident,'completions').glob('*.json'),34))
            if len(paths)>33:raise Conflict('Exact pause completion directory exceeds its bound')
            for path in paths:
                value=self.native._read(path)
                if not isinstance(value,dict) or not isinstance(value.get('request'),dict):raise Conflict('Pause completion metadata is corrupt')
                if value['request'].get('operation_id')==operation:
                    if record is not None:raise Conflict('Original completion identity is duplicated')
                    record=value
        else:
            path=controls.path(self.native,kind,ident,'records',digest(operation)+'.json')
            record=self.native._read(path) if path.exists() else (head['record'] if head and head['record']['operation_id']==operation else None)
        if record is None:
            from packages.domain.store import NotFound
            raise NotFound('Original capability control has no saved receipt; do not infer it was unsent')
        request=ExperiencePendingControl.model_validate(record.get('request')).model_dump()
        if (record.get('request_sha256')!=request_sha or digest(request)!=request_sha
                or request['operation_id']!=operation or request['activation_operation_id']!=activation_operation
                or request['activation_request_sha256']!=activation_sha or request['mode']!=mode):
            raise Conflict('Original capability control request identity differs')
        if mode=='COMPLETE_PAUSED_METADATA':
            if set(record)!={'request','request_sha256','intent_ref'} or record['intent_ref']!=intent['record_ref']:
                raise Conflict('Original metadata completion names another intent')
            revision=request['expected_control_revision'];decision_state=None;record_kind='COMPLETION_PACKET_RESERVED_ONLY'
        else:
            revision=request['expected_control_revision']+1;decision_state='PAUSED' if mode=='PAUSE' else 'ACTIVE';record_kind='CONTROL_DECISION'
            if (record.get('state')!=decision_state or record.get('revision')!=revision
                    or record.get('native_kind')!=kind or record.get('capability_id')!=ident
                    or record.get('project_id')!=pid or record.get('session_id')!=sid
                    or record.get('target_native_operation_id')!=intent['native_operation_id']):
                raise Conflict('Original capability control decision differs')
        observed=self.action._native_observation(intent)
        claim=self.native.experience_claim(kind,ident)
        candidate=(intent['request']['candidate_ref'] if intent['kind']=='ACTIVATION' else self.candidates.saved_record(pid,sid,intent['basis']['source_activation_ref'],'ACTIVATION')['request']['candidate_ref'])
        return {'kind':'EXPERIENCE_PENDING_CONTROL_OBSERVATION','project_id':pid,'session_id':sid,
            'operation_id':operation,'request_sha256':request_sha,'candidate_ref':candidate,
            'decision_ref':{'id':operation,'revision':revision,'sha256':digest(record)},
            'decision_state':decision_state,'control_record_kind':record_kind,
            'capability_control':controls.view(self.native,kind,ident),
            'local_finalization_needed':bool(mode!='COMPLETE_PAUSED_METADATA' and head and not head['finalized'] and head['record']==record),
            'configuration_finalization_needed':bool(claim and claim['state']=='ACTIVE' and claim['native_operation_id']==intent['native_operation_id']),
            'observation':{k:deepcopy(observed[k]) for k in ['status','current_configuration_ref','automatic_replay'] if k in observed},
            'read_only':True,'metadata_writes':0,'model_calls':0,'processes_started':0,'automatic_replay':False}

    def _finish(self,kind,ident,record):
        target=controls.path(self.native,kind,ident,'records',digest(record['operation_id'])+'.json')
        if target.exists():
            if self.native._read(target)!=record:raise Conflict('Original pause decision bytes differ')
        else:self.native._write(target,record)
        head=controls.head(self.native,kind,ident)
        if head and head['record']==record and not head['finalized']:
            if record['state']=='ACTIVE':
                request=record['request'];intent=self._intent(record['project_id'],record['session_id'],request['activation_operation_id'],request['activation_request_sha256'])
                self._resume_basis(record['project_id'],record['session_id'],intent,request['expected_current_configuration_ref'])
            self.native._write(controls.path(self.native,kind,ident,'head.json'),{**head,'finalized':True},replace=True)

    def _resume_basis(self,pid,sid,intent,expected):
        basis=intent['basis'];kind=basis['native_kind'];ident=basis['capability_id'];current=self.native._index()[kind].get(ident)
        if current is None or current!=expected:raise Conflict('Resume requires the exact current configuration preview')
        config=self.native._configuration(kind,current)['configuration'];observed=self.action._native_observation(intent)
        claim=self.native.experience_claim(kind,ident)
        if claim and claim['state']=='ACTIVE':raise Conflict('Finish the original metadata while paused before resuming')
        if config['enabled']:
            if observed.get('status')=='APPLIED_CURRENT':
                if intent['kind']=='ACTIVATION':
                    original=intent['request'];self.action._validated(pid,sid,ExperiencePromotePrepare(**{k:original[k] for k in ExperiencePromotePrepare.model_fields}))
            elif config!=intent['previous_configuration']:
                raise Conflict('A different enabled configuration needs its own explicit review; it was retained')
            source=source_status(self.native,kind,current)
            if source is not None and source.get('availability')!='ACTIVE':raise Conflict('A withdrawn or forgotten source cannot resume')
            if kind=='mcp':_mcp(config)

    def change(self,pid,sid,body):
        body=ExperiencePendingControl.model_validate(body)
        if not body.confirmed:raise InvalidInput('Confirm this exact pending configuration control')
        request=body.model_dump();intent=self._intent(pid,sid,body.activation_operation_id,body.activation_request_sha256)
        basis=intent['basis'];kind=basis['native_kind'];ident=basis['capability_id'];expected=body.expected_current_configuration_ref.model_dump() if body.expected_current_configuration_ref else None
        if body.mode=='COMPLETE_PAUSED_METADATA':return self._complete(pid,sid,intent,body)
        with self.candidates._guard(pid,sid):
            head=controls.head(self.native,kind,ident);revision=head['revision'] if head else 0
            record_path=controls.path(self.native,kind,ident,'records',digest(body.operation_id)+'.json')
            old=self.native._read(record_path) if record_path.exists() else (head['record'] if head and head['record']['operation_id']==body.operation_id else None)
            if old is not None:
                if old.get('request')!=request or old.get('request_sha256')!=digest(request):raise Conflict('Original pause operation binds another input')
                self._finish(kind,ident,old)
                return self.inspect(pid,sid,body.activation_operation_id,body.activation_request_sha256)
            if revision!=body.expected_control_revision:raise Conflict('A newer capability control was retained')
            if body.mode=='PAUSE':
                claim=self.native.experience_claim(kind,ident)
                owns_claim=bool(claim and claim['state']=='ACTIVE' and all(claim[k]==intent[k] for k in ['project_id','session_id','operation_id','request_sha256','native_operation_id']))
                interrupted_resume=bool(head and head['state']=='ACTIVE' and not head['finalized']
                    and head['record']['target_native_operation_id']==intent['native_operation_id']
                    and head['record']['project_id']==pid and head['record']['session_id']==sid)
                if not owns_claim and not interrupted_resume:
                    raise Conflict('Only this exact still-pending native save can be paused')
                if head and head['state']=='PAUSED':raise Conflict('The capability is already paused; inspect the original control')
                state='PAUSED';limit=65
            else:
                if not head or head['state']!='PAUSED' or not head['finalized'] or head['record']['target_native_operation_id']!=intent['native_operation_id']:
                    raise Conflict('Resume requires this exact finalized negative pause')
                self._resume_basis(pid,sid,intent,expected);state='ACTIVE';limit=64
            if revision>=limit:raise Conflict('Capability control history is full; the final negative decision is reserved')
            record={'schema':'eeas.experience-capability-control-decision.v1','native_kind':kind,'capability_id':ident,
                'operation_id':body.operation_id,'request':request,'request_sha256':digest(request),'project_id':pid,'session_id':sid,
                'target_native_operation_id':intent['native_operation_id'],'previous_revision':revision,'revision':revision+1,'state':state,'recorded_at':now()}
            # Negative action is effective before auxiliary history I/O; an
            # unfinished positive action still has effective_state PAUSED.
            updated={'schema':'eeas.experience-capability-control.v1','native_kind':kind,'capability_id':ident,
                'revision':revision+1,'state':state,'record':record,'record_sha256':digest(record),'finalized':False}
            self.native._write(controls.path(self.native,kind,ident,'head.json'),updated,replace=True)
            self._finish(kind,ident,record)
            return self.inspect(pid,sid,body.activation_operation_id,body.activation_request_sha256)

    def _complete(self,pid,sid,intent,body):
        basis=intent['basis'];kind=basis['native_kind'];ident=basis['capability_id']
        # One exact completion packet per pause; repeated explicit recovery may
        # finish metadata, never execute a command, Tool, SDK, or model call.
        with self.candidates._guard(pid,sid):
            head=controls.head(self.native,kind,ident)
            if (not head or head['state']!='PAUSED' or not head['finalized'] or head['revision']!=body.expected_control_revision
                    or head['record']['target_native_operation_id']!=intent['native_operation_id']):
                raise Conflict('Only an exact finalized pause permits local metadata completion')
            control=controls.view(self.native,kind,ident)
            path=controls.path(self.native,kind,ident,'completions',head['record_sha256']+'.json');request=body.model_dump()
            saved={'request':request,'request_sha256':digest(request),'intent_ref':intent['record_ref']}
            if path.exists():
                if self.native._read(path)!=saved:raise Conflict('This pause already binds another exact metadata completion input')
            else:self.native._write(path,saved)
        observation=self.action._native_observation(intent)
        if observation['status'] in {'APPLIED_CURRENT','SAVED_SUPERSEDED'}:self.action._finalize(pid,sid,intent)
        elif observation['status'] in {'UNKNOWN_NO_SAVED_NATIVE_RECEIPT','SAVED_POINTER_UNCONFIRMED'}:
            self.action._save(pid,sid,intent,paused_control=control)
        else:raise Conflict('The saved native pointer needs exact original inspection')
        return self.inspect(pid,sid,intent['operation_id'],intent['request_sha256'])
