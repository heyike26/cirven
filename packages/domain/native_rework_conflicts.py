"""Source-bound three-way review and a new manual preview, never a file writer.

Only existing UTF-8/JSON rework adapters are used. A changed text object needs
an explicit current range when its unchanged quotation is no longer unique.
Old previews, feedback, input contracts and outcomes remain immutable.
"""
from copy import deepcopy
from pydantic import Field,model_validator,field_validator
from packages.domain.questions import RequestModel,Id,Hash,raw_hash
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import FileSource,MAX_EDIT_BYTES,canonical,digest
from packages.domain.feedback_rework import (BindingInput,Classification,Locator,Patch,PlanInput,
    Scope,TaskContract,_parts,selected,_json)
from packages.domain.store import Conflict,InvalidInput

MAX_REVIEW_BYTES=4*1024*1024


class NativeConflictInspect(RequestModel):
    plan_ref:RevisionRef
    current_source:FileSource
    current_locator:Locator|None=None


class NativeConflictResolve(NativeConflictInspect):
    operation_id:Id
    review_sha256:Hash
    resolved_value_text:str=Field(max_length=MAX_EDIT_BYTES)
    rationale:str=Field(min_length=1,max_length=4000)
    current_object_confirmed:bool=Field(strict=True)

    @field_validator('*')
    @classmethod
    def original_utf8(cls,value,info):
        if isinstance(value,str):
            try:encoded=value.encode('utf-8')
            except UnicodeError as error:raise ValueError('Text must be valid UTF-8') from error
            limit=MAX_EDIT_BYTES if info.field_name=='resolved_value_text' else 65536
            if len(encoded)>limit or '\x00' in value:raise ValueError('Text exceeds its UTF-8 bound or contains NUL')
        return value

    @model_validator(mode='after')
    def explicit_current_object(self):
        if not self.current_object_confirmed:raise ValueError('Confirm the exact current object before proposing its resolution')
        if not self.rationale.strip():raise ValueError('Explain the manual resolution')
        return self


def _current_locator(old,base,current,explicit):
    if explicit is not None:
        candidate=explicit.model_dump()
        if candidate['kind']!=old['kind']:
            raise InvalidInput('A resolution cannot silently change the original object adapter')
        if old['kind']=='JSON_POINTER' and _parts(candidate['pointer'])!=_parts(old['pointer']):
            raise InvalidInput('The current JSON pointer must name the same original object')
        return candidate,'USER_CONFIRMED_CURRENT_TEXT_RANGE' if old['kind']=='TEXT_RANGE' else 'SAME_ORIGINAL_OBJECT'
    if old['kind']!='TEXT_RANGE':return deepcopy(old),'SAME_ORIGINAL_OBJECT'
    if base==current:return deepcopy(old),'SAME_IMMUTABLE_TEXT_RANGE'
    quote=selected(base,old)
    if base.count(quote)==1 and current.count(quote)==1:
        start=current.index(quote)
        return {'kind':'TEXT_RANGE','char_start':start,'char_end':start+len(quote),
            'quote_sha256':old['quote_sha256']},'EXACT_UNCHANGED_UNIQUE_QUOTATION'
    return None,'EXPLICIT_CURRENT_TEXT_SELECTION_REQUIRED'


def _load(service,pid,body):
    parent=service._resolve(pid,body.plan_ref.model_dump(),'PLAN')
    proposal=service._proposal(pid,parent['proposal_ref'])
    mapping=service._resolve(pid,parent['mapping_ref'],'MAPPING')
    service._requirements(pid,proposal,mapping.get('discovery_ref'),mapping['requirement_ids'])
    engine=service.rework.resolve(pid,parent['engine_plan_ref'],'PLAN')
    if (parent.get('status')!='PROPOSED' or engine['proposal_ref']!=parent['proposal_ref']
            or len(engine['patches'])!=1 or len(engine['write_scope'])!=1):
        raise Conflict('Conflict review requires the original single-object native preview')
    patch=engine['patches'][0];binding=service.rework.resolve(pid,patch['binding_ref'],'BINDING')
    if digest(parent['proposed_value'])!=digest(patch['proposed_value']):
        raise Conflict('The native preview and its original scoped patch differ')
    old_source=binding['file_source_ref'];current_source=body.current_source.model_dump()
    if (service._file_identity(old_source)!=service._file_identity(current_source)
            or engine['write_scope']!=parent['write_scope']
            or engine['write_scope']!=[{'workspace_id':old_source['workspace_id'],'path':old_source['path']}]):
        raise Conflict('The current source must retain the original exact file and workspace authority')
    base=service.rework._file(pid,old_source);current=service.rework._file(pid,current_source)
    if not current['current']:raise Conflict('Inspect a fresh current file version before reviewing a conflict')
    if digest(selected(base['content'],binding['locator']))!=binding['selected_value_sha256']:
        raise Conflict('The original selected object differs from its immutable binding')
    locator,relation=_current_locator(binding['locator'],base['content'],current['content'],body.current_locator)
    value=selected(current['content'],locator) if locator else None
    basis={'schema':'eeas.native-rework-conflict.v1','project_id':pid,'plan_ref':body.plan_ref.model_dump(),
        'mapping_ref':parent['mapping_ref'],'native_feedback_ref':parent['native_feedback_ref'],
        'proposal_ref':parent['proposal_ref'],'engine_plan_ref':parent['engine_plan_ref'],
        'binding_ref':patch['binding_ref'],'base_source':old_source,'current_source':current_source,
        'original_locator':binding['locator'],'current_locator':locator,'scope_relation':relation,
        'base_value_sha256':binding['selected_value_sha256'],'current_value_sha256':digest(value) if locator else None,
        'proposed_value_sha256':digest(patch['proposed_value']),'write_scope':engine['write_scope']}
    render=lambda v:canonical(v) if binding['locator']['kind']=='JSON_POINTER' else v
    basis.update(base_text_sha256=raw_hash(render(binding['selected_value'])),
        current_text_sha256=raw_hash(render(value)) if locator else None,
        proposed_text_sha256=raw_hash(render(patch['proposed_value'])))
    return parent,mapping,engine,binding,base,current,basis,value


def inspect_native_rework_conflict(service,pid,body):
    body=NativeConflictInspect.model_validate(body)
    parent,_mapping,_engine,binding,_base,current,basis,value=_load(service,pid,body)
    render=lambda v:canonical(v) if binding['locator']['kind']=='JSON_POINTER' else v
    result={'kind':'NATIVE_REWORK_CONFLICT_REVIEW','version':1,'project_id':pid,
        'query_sha256':digest(body.model_dump()),'basis':basis,'review_sha256':digest(basis),
        'base_text':render(binding['selected_value']),'current_text':render(value) if basis['current_locator'] else None,
        'proposed_text':render(parent['proposed_value']),
        'current_content':current['content'] if binding['locator']['kind']=='TEXT_RANGE' else None,
        'selection_status':'EXACT_CURRENT_OBJECT' if basis['current_locator'] else 'CURRENT_SELECTION_REQUIRED',
        'read_only':True,'model_calls':0,'source_files_written':0,'execution_started':False,
        'write_authorized':False,'semantic_link_verified':False,'whole_task_acceptance':'NOT_ASSESSED',
        'outside_selected_object_preservation':'EXISTING_SCOPED_APPLY_AND_CAS_REQUIRED'}
    if len(canonical(result).encode())>MAX_REVIEW_BYTES:
        raise Conflict('The three-way review exceeds its bounded response')
    # This is only a final saved-source observation, not a cross-process editor lock.
    if service.ws.check_sources(pid,[body.current_source])[0]['status']!='CURRENT':
        raise Conflict('The current file changed during conflict inspection')
    return result


def resolve_native_rework_conflict(service,pid,body):
    body=NativeConflictResolve.model_validate(body)
    def build(_fresh):
        query=NativeConflictInspect(plan_ref=body.plan_ref,current_source=body.current_source,
            current_locator=body.current_locator)
        review=inspect_native_rework_conflict(service,pid,query)
        if review['review_sha256']!=body.review_sha256 or review['selection_status']!='EXACT_CURRENT_OBJECT':
            raise Conflict('Review the exact current object again before saving a resolved preview')
        parent,mapping,engine,_binding,_base,_current,basis,_value=_load(service,pid,query)
        if digest(basis)!=body.review_sha256:raise Conflict('The resolution basis changed during preparation')
        proposed=_json(body.resolved_value_text) if basis['original_locator']['kind']=='JSON_POINTER' else body.resolved_value_text
        try:encoded=canonical(proposed).encode('utf-8')
        except (ValueError,UnicodeError) as error:raise InvalidInput('The resolved value must contain valid UTF-8 JSON values') from error
        if len(encoded)>MAX_EDIT_BYTES:raise InvalidInput('The resolved value exceeds the existing edit bound')
        bound=service.rework.bind(pid,BindingInput(operation_id='nr-resolve-bind-'+digest([pid,body.operation_id])[:48],
            file_source_ref=body.current_source,locator=basis['current_locator'],
            proposal_ref=parent['proposal_ref'],task_ids=mapping['task_ids']))
        contracts=[TaskContract.model_validate(c) for c in engine['contracts']]
        result=service.rework.plan(pid,PlanInput(operation_id='nr-resolve-plan-'+digest([pid,body.operation_id])[:48],
            feedback_ref=engine['feedback_ref'],proposal_ref=parent['proposal_ref'],
            classification=Classification(kind='UNKNOWN',origin='USER_STATEMENT',rationale=body.rationale,
                evidence_refs=[bound['binding_ref']]),patches=[Patch(binding_ref=bound['binding_ref'],
                proposed_value=proposed)],write_scope=[Scope.model_validate(s) for s in engine['write_scope']],
            contracts=contracts))
        if service.ws.check_sources(pid,[body.current_source])[0]['status']!='CURRENT':
            raise Conflict('The current file changed before the resolved preview was recorded')
        return {'status':'PROPOSED','mapping_ref':parent['mapping_ref'],
            'native_feedback_ref':parent['native_feedback_ref'],'proposal_ref':parent['proposal_ref'],
            'proposed_value':proposed,'engine_plan_ref':result['plan_ref'],'engine_plan':result,
            'write_scope':engine['write_scope'],'classification_status':'PROPOSAL_NOT_SEMANTICALLY_VERIFIED',
            'write_authorized':False,'manual_resolution':{'parent_plan_ref':body.plan_ref.model_dump(),
                'review_sha256':body.review_sha256,'basis':basis,'current_binding_ref':bound['binding_ref'],
                'resolved_value_sha256':digest(proposed),'resolved_text_sha256':raw_hash(body.resolved_value_text),
                'resolved_value_text':body.resolved_value_text,'rationale':body.rationale,
                'current_object_confirmed':True,'old_input_contracts_preserved':True,
                'source_files_written':0,'model_calls':0,'semantic_link_verified':False}}
    # New PLAN identity and the existing immutable operation journal. No APPLY,
    # LocalWorkspaces.edit, model, shell, external writer or new permission.
    return service._run(pid,body,'PLAN',build)
