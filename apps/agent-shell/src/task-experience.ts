import {api,type ApiClient} from './api';
import type {AgentSession} from './agent-sessions';
import type {SourceRef} from './contracts';
import {nativeReference,type NativeRef} from './native-collaboration';
import {configurationCanonical as canonical} from './conversation-configuration';
import {workRequirementScope,type WorkRequirementScope} from './original-work-requirements';
import type {MCPConfiguration} from './native-capabilities';

export type ExperienceKind='SKILL'|'MCP_TOOL';
export type ExperienceQuote={source:SourceRef;char_start:number;char_end:number;quote_sha256:string};
export type ExperienceProposal=WorkRequirementScope&{operation_id:string;root_sha256:string;title:string;observation:string;proposed_use:string;limitations:string;sources:ExperienceQuote[];proposal:{kind:'SKILL';id:string;content:string}|{kind:'MCP_TOOL';id:string;configuration:MCPConfiguration&{enabled:false}}};
export type ExperienceCase={id:string;label:string;purpose:'POSITIVE'|'NEGATIVE'}&({kind:'SKILL';prompt:string;required_text:string[];forbidden_text:string[]}|{kind:'MCP_TOOL';tool_name:string;input:Record<string,unknown>;expected_result:{content:unknown[];structuredContent:unknown;isError:boolean}});
export type ExperienceReviewRequest={operation_id:string;candidate_ref:NativeRef;review_confirmed:true;statement:string;cases:ExperienceCase[];allowed_effects:('PROCESS_START'|'REMOTE_MCP_CONNECT')[];max_model_calls:number;max_output_tokens:number;timeout_seconds:number;process_boundary_acknowledged:boolean};
export type ExperienceValidationBase={operation_id:string;candidate_ref:NativeRef;review_ref:NativeRef;validation_confirmed:true;process_boundary_acknowledged:true};
export type ExperiencePromotionBasis={candidate_ref:NativeRef;review_ref:NativeRef;validation_kind:ExperienceKind;validation_operation_id:string;validation_request_sha256:string;validation_result_sha256:string};
export type ExperienceRollbackBasis={activation_operation_id:string;activation_request_sha256:string;expected_current_configuration_ref:NativeRef;mode:'DISABLE'|'RESTORE_PREVIOUS'};
export type ExperienceRequests={
 PROPOSE:ExperienceProposal;REVIEW:ExperienceReviewRequest;
 TOOL_VALIDATE:ExperienceValidationBase&{allowed_effects:string[]};
 SKILL_VALIDATE:ExperienceValidationBase&{provider_profile_ref:NativeRef};
 PROMOTE:ExperiencePromotionBasis&{operation_id:string;promotion_review_sha256:string;promotion_confirmed:true};
 ROLLBACK:ExperienceRollbackBasis&{operation_id:string;rollback_review_sha256:string;rollback_confirmed:true};
 SOURCE_DECISION:{operation_id:string;candidate_ref:NativeRef;expected_revision:number;action:'WITHDRAW'|'RESTORE'|'FORGET';confirmed:true;statement:string};
 PENDING_CONTROL:{operation_id:string;activation_operation_id:string;activation_request_sha256:string;expected_control_revision:number;mode:'PAUSE'|'COMPLETE_PAUSED_METADATA'|'RESUME_PAUSED';expected_current_configuration_ref:NativeRef|null;confirmed:true};
 RECOVER_CONFIGURATION:{operation_id:string;request_sha256:string;mode:'INSPECT'|'FINALIZE_LOCAL_RECORD'|'COMPLETE_EXACT_SAVED_METADATA';recovery_confirmed:boolean};
};
export type ExperienceAction=keyof ExperienceRequests;
export type ExperienceOwner=Pick<AgentSession,'project_id'|'session_ref'>;
export type ExperienceContext=ExperienceOwner&Pick<WorkRequirementScope,'generation'|'workspace_ref'>&{provider_profile_ref?:NativeRef};
export type ExperienceOrigin={project_id:string;session_id:string;candidate_ref:NativeRef};
export type ExperienceSourceWork={kind:'EXPERIENCE_SOURCE_WORK';context:ExperienceContext;candidate_ref:NativeRef;root_operation_id:string;root_sha256:string;read_only:true;model_calls:0;processes_started:0};
export type PreparedExperience<A extends ExperienceAction=ExperienceAction>={kind:'EXPERIENCE_REQUEST_PREPARATION';version:1;project_id:string;session_id:string;session_ref:NativeRef;action:A;request:ExperienceRequests[A];request_json?:string;request_sha256:string;read_only:true;metadata_writes:0;model_calls:0;processes_started:0;permission_granted:false;automatic_replay:false;authority_checked_on_actual_command:true};
export type ExperienceObservation=Record<string,unknown>&{kind?:string;operation_id?:string;request_sha256?:string;record_ref?:NativeRef;decision_ref?:NativeRef;candidate_ref?:NativeRef;content_withheld?:boolean;result_sha256?:string};
export type ExperienceCandidatePage={schema:'eeas.task-experience-candidate.v1';project_id:string;session_id:string;entries:{candidate_ref:NativeRef;title:string;kind:ExperienceKind;root_operation_id:string;root_sha256:string;source_policy:{availability:string;revision:number;forgotten:boolean}}[];offset:number;next_offset:number|null;total:number;read_only:true;model_calls:0;commands_started:0;configuration_registered:false;automatic_replay:false};
const routes:Record<ExperienceAction,string>={PROPOSE:'candidates',REVIEW:'reviews',TOOL_VALIDATE:'tool-validations',SKILL_VALIDATE:'skill-validations',PROMOTE:'promotion',ROLLBACK:'rollback',SOURCE_DECISION:'source-policy',PENDING_CONTROL:'pending-configuration',RECOVER_CONFIGURATION:'configuration-operations/recover'};
const same=(a:unknown,b:unknown)=>canonical(a)===canonical(b),copy=<T>(v:T):T=>structuredClone(v);
export const experienceHash=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const id=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v);
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
function check(v:unknown,code='EXPERIENCE_RECEIPT_CHANGED'):asserts v{if(!v)throw Error(code);}
export function experienceOwner(s:ExperienceOwner):ExperienceOwner{check(id(s.project_id)&&nativeReference(s.session_ref)&&/^[a-f0-9]{32}$/.test(s.session_ref.id));return {project_id:s.project_id,session_ref:copy(s.session_ref)};}
export function experienceContext(s:AgentSession):ExperienceContext{const q=workRequirementScope(s,'experience_context');return {...experienceOwner(s),generation:q.generation,workspace_ref:q.workspace_ref,...s.provider_profile_ref?{provider_profile_ref:s.provider_profile_ref}:{}};}
export function experienceScope(s:ExperienceContext,root:string):WorkRequirementScope{experienceOwner(s);check(id(root)&&Number.isSafeInteger(s.generation)&&s.generation>=1&&experienceHash(s.workspace_ref?.id)&&experienceHash(s.workspace_ref?.binding_sha256)&&Number.isSafeInteger(s.workspace_ref?.revision)&&s.workspace_ref.revision>=1);return {session_ref:copy(s.session_ref),generation:s.generation,workspace_ref:copy(s.workspace_ref),root_operation_id:root};}
export function assertExperiencePreparation<A extends ExperienceAction>(value:unknown,owner:ExperienceOwner,action:A,input?:ExperienceRequests[A]):PreparedExperience<A>{
 experienceOwner(owner);const v=value as PreparedExperience<A>;
 check(object(v)&&v.kind==='EXPERIENCE_REQUEST_PREPARATION'&&v.version===1&&v.project_id===owner.project_id&&v.session_id===owner.session_ref.id&&same(v.session_ref,owner.session_ref)&&v.action===action&&Object.hasOwn(routes,action));
 check(object(v.request)&&id(v.request.operation_id)&&experienceHash(v.request_sha256)&&new TextEncoder().encode(JSON.stringify(v)).length<=540672);
 if(v.request_json!==undefined)check(typeof v.request_json==='string'&&new TextEncoder().encode(v.request_json).length<=262144&&same(JSON.parse(v.request_json),v.request));
 check(v.read_only===true&&v.metadata_writes===0&&v.model_calls===0&&v.processes_started===0&&v.permission_granted===false&&v.automatic_replay===false&&v.authority_checked_on_actual_command===true);
 // The server digest is deliberately opaque: Python/JS float formatting is
 // not interchangeable. Compare normalized fields, never rehash this receipt.
 check(input===undefined||same(v.request,input));
 if(action==='PROPOSE'){const r=v.request as ExperienceProposal;check(same(r.session_ref,owner.session_ref));}
 return copy(v);
}
function observed(value:unknown,operation:string,requestSha:string):ExperienceObservation{
 const v=value as ExperienceObservation;check(object(v)&&v.operation_id===operation&&v.request_sha256===requestSha&&new TextEncoder().encode(JSON.stringify(v)).length<=2232320);
 check(v.automatic_replay!==true);if(v.record_ref!==undefined)check(nativeReference(v.record_ref)&&v.record_ref.id===operation);
 if(v.decision_ref!==undefined)check(nativeReference(v.decision_ref)&&v.decision_ref.id===operation);
 if(v.result_sha256!==undefined)check(experienceHash(v.result_sha256));return copy(v);
}

/** All commands disable unauthorized POST replay; recovery is a separate GET. */
export class TaskExperienceApi{
 constructor(private client:ApiClient=api){}
 private path(owner:ExperienceOwner){const s=experienceOwner(owner);return this.client.projectPath(s.project_id)+'/agent-sessions/'+encodeURIComponent(s.session_ref.id)+'/experience/';}
 async prepare<A extends ExperienceAction>(owner:ExperienceOwner,action:A,input:ExperienceRequests[A]|string,signal?:AbortSignal){const s=experienceOwner(owner),request=typeof input==='string'?JSON.parse(input) as ExperienceRequests[A]:copy(input);return assertExperiencePreparation(await this.client.request(this.path(s)+'requests/prepare','POST',typeof input==='string'?{action,request_json:input}:{action,request},signal,false),s,action,request);}
 async dispatch<A extends ExperienceAction>(owner:ExperienceOwner,packet:PreparedExperience<A>,signal?:AbortSignal):Promise<ExperienceObservation>{
 const s=experienceOwner(owner),p=assertExperiencePreparation(packet,s,packet.action),value=p.request_json!==undefined?await this.client.exactExperienceJSON<unknown>(this.path(s)+routes[p.action],p.request_json,signal):await this.client.request<unknown>(this.path(s)+routes[p.action],'POST',p.request,signal,false);
 if(p.action==='PENDING_CONTROL')return this.lookup(s,p,signal);
 if(p.action==='SOURCE_DECISION'){
  const result=value as {decision?:unknown;automatic_replay?:boolean};check(object(result)&&result.automatic_replay===false);return observed(result.decision,p.request.operation_id,p.request_sha256);
 }
 if(p.action==='RECOVER_CONFIGURATION'){const r=p.request as ExperienceRequests['RECOVER_CONFIGURATION'];return observed(value,r.operation_id,r.request_sha256);}
 return observed(value,p.request.operation_id,p.request_sha256);
 }
 async lookup<A extends ExperienceAction>(owner:ExperienceOwner,packet:PreparedExperience<A>,signal?:AbortSignal){
 const s=experienceOwner(owner),p=assertExperiencePreparation(packet,s,packet.action),r=p.request;let suffix:string,expected=p.request_sha256;
 const query=new URLSearchParams({request_sha256:p.request_sha256});
 if(p.action==='SOURCE_DECISION'){
  const q=r as ExperienceRequests['SOURCE_DECISION'];query.set('sha256',q.candidate_ref.sha256);suffix='source-policy/'+encodeURIComponent(q.candidate_ref.id)+'/operations/'+encodeURIComponent(q.operation_id);
 }else if(p.action==='PENDING_CONTROL'){
  const q=r as ExperienceRequests['PENDING_CONTROL'];query.set('activation_request_sha256',q.activation_request_sha256);query.set('mode',q.mode);suffix='pending-configuration/'+encodeURIComponent(q.activation_operation_id)+'/controls/'+encodeURIComponent(q.operation_id);
 }else if(p.action==='RECOVER_CONFIGURATION'){
  const q=r as ExperienceRequests['RECOVER_CONFIGURATION'];expected=q.request_sha256;query.set('request_sha256',expected);suffix='configuration-operations/'+encodeURIComponent(q.operation_id);
 }else{const category=p.action==='TOOL_VALIDATE'?'tool-validations':p.action==='SKILL_VALIDATE'?'skill-validations':['PROMOTE','ROLLBACK'].includes(p.action)?'configuration-operations':'operations';suffix=category+'/'+encodeURIComponent(r.operation_id);}
 return observed(await this.client.request(this.path(s)+suffix+'?'+query,'GET',undefined,signal),r.operation_id,expected);
 }
 async candidates(owner:ExperienceOwner,offset=0,signal?:AbortSignal){
 const s=experienceOwner(owner);check(Number.isSafeInteger(offset)&&offset>=0&&offset<=32);const v=await this.client.request<ExperienceCandidatePage>(this.path(s)+'candidates?offset='+offset,'GET',undefined,signal);
 check(v?.schema==='eeas.task-experience-candidate.v1'&&v.project_id===s.project_id&&v.session_id===s.session_ref.id&&v.read_only===true&&v.model_calls===0&&v.commands_started===0&&v.configuration_registered===false&&v.automatic_replay===false&&Array.isArray(v.entries)&&v.entries.length<=8&&Number.isSafeInteger(v.total)&&v.total>=0&&v.total<=32);
 const start=Math.min(offset,Math.floor(Math.max(v.total-1,0)/8)*8);check(v.offset===start&&v.entries.length===Math.min(8,v.total-start)&&v.next_offset===(start+8<v.total?start+8:null));
 const seen=new Set<string>();for(const row of v.entries){check(nativeReference(row.candidate_ref)&&row.candidate_ref.revision===1&&!seen.has(row.candidate_ref.id)&&['SKILL','MCP_TOOL'].includes(row.kind)&&typeof row.title==='string'&&Array.from(row.title).length<=200&&id(row.root_operation_id)&&experienceHash(row.root_sha256)&&row.source_policy&&typeof row.source_policy.availability==='string'&&row.source_policy.availability.length>0&&row.source_policy.availability.length<=100&&typeof row.source_policy.forgotten==='boolean'&&(!row.source_policy.forgotten||row.source_policy.availability!=='ACTIVE')&&Number.isInteger(row.source_policy.revision)&&row.source_policy.revision>=0&&row.source_policy.revision<=33);seen.add(row.candidate_ref.id);}
 return copy(v);
 }
 async candidate(owner:ExperienceOwner,ref:NativeRef,signal?:AbortSignal){check(nativeReference(ref)&&ref.revision===1);const value=await this.client.request<ExperienceObservation>(this.path(owner)+'candidates/'+encodeURIComponent(ref.id)+'?sha256='+ref.sha256,'GET',undefined,signal);check(value.record_ref&&same(value.record_ref,ref));return copy(value);}
 async sourceWork(origin:ExperienceOrigin,signal?:AbortSignal){check(id(origin.project_id)&&/^[a-f0-9]{32}$/.test(origin.session_id)&&nativeReference(origin.candidate_ref)&&origin.candidate_ref.revision===1);const v=await this.client.request<ExperienceSourceWork>(this.client.projectPath(origin.project_id)+'/agent-sessions/'+origin.session_id+'/experience/candidates/'+encodeURIComponent(origin.candidate_ref.id)+'/source-work?sha256='+origin.candidate_ref.sha256,'GET',undefined,signal);check(v?.kind==='EXPERIENCE_SOURCE_WORK'&&same(v.candidate_ref,origin.candidate_ref)&&v.context.project_id===origin.project_id&&v.context.session_ref.id===origin.session_id&&id(v.root_operation_id)&&experienceHash(v.root_sha256)&&v.read_only===true&&v.model_calls===0&&v.processes_started===0);experienceScope(v.context,v.root_operation_id);return copy(v);}
 async history(owner:ExperienceOwner,ref:NativeRef,offset=0,signal?:AbortSignal,expectedIndex?:string){check(nativeReference(ref)&&Number.isSafeInteger(offset)&&offset>=0&&offset<=256);const v=await this.client.request<{kind:string;project_id:string;session_id:string;candidate_ref:NativeRef;entries:ExperienceHistoryEntry[];offset:number;next_offset:number|null;legacy_body_reads:number;read_only:boolean;model_calls:number;metadata_writes:number;index_sha256:string}>(this.path(owner)+'candidates/'+encodeURIComponent(ref.id)+'/history?'+new URLSearchParams({sha256:ref.sha256,offset:String(offset),...expectedIndex?{expected_index_sha256:expectedIndex}:{}}),'GET',undefined,signal);check(experienceHash(v.index_sha256)&&(!offset||v.index_sha256===expectedIndex)&&v.kind==='EXPERIENCE_CANDIDATE_HISTORY'&&v.project_id===owner.project_id&&v.session_id===owner.session_ref.id&&same(v.candidate_ref,ref)&&Array.isArray(v.entries)&&v.entries.length<=8&&v.read_only===true&&v.model_calls===0&&v.metadata_writes===0&&Number.isInteger(v.legacy_body_reads)&&v.legacy_body_reads>=0&&v.legacy_body_reads<=8&&Number.isInteger(v.offset)&&v.offset>=0&&v.offset<=offset&&(v.next_offset===null||Number.isInteger(v.next_offset)&&v.next_offset>v.offset&&v.next_offset<=256));for(const row of v.entries)check(nativeReference(row.record_ref)&&row.record_ref.revision===1&&same(row.candidate_ref,ref)&&experienceHash(row.request_sha256)&&['PROPOSAL','REVIEW','VALIDATION','SKILL_VALIDATION','ACTIVATION','ROLLBACK'].includes(row.kind));return copy(v);}
 async record(owner:ExperienceOwner,entry:ExperienceHistoryEntry,signal?:AbortSignal){return observed(await this.client.request(this.path(owner)+'operations/'+encodeURIComponent(entry.record_ref.id)+'?request_sha256='+entry.request_sha256,'GET',undefined,signal),entry.record_ref.id,entry.request_sha256);}
 async review(owner:ExperienceOwner,ref:NativeRef,signal?:AbortSignal){check(nativeReference(ref)&&ref.revision===1);const v=await this.client.request<ExperienceObservation>(this.path(owner)+'reviews/'+encodeURIComponent(ref.id)+'?sha256='+ref.sha256,'GET',undefined,signal);check(v.kind==='REVIEW'&&same(v.record_ref,ref));return copy(v);}
 async validation(owner:ExperienceOwner,kind:ExperienceKind,operation:string,sha:string,signal?:AbortSignal){check(id(operation)&&experienceHash(sha));return observed(await this.client.request(this.path(owner)+(kind==='SKILL'?'skill-validations/':'tool-validations/')+encodeURIComponent(operation)+'?request_sha256='+sha,'GET',undefined,signal),operation,sha);}
 async outcome(owner:ExperienceOwner,entry:ExperienceHistoryEntry,signal?:AbortSignal){const category=entry.kind==='VALIDATION'?'tool-validations':entry.kind==='SKILL_VALIDATION'?'skill-validations':entry.kind==='ACTIVATION'||entry.kind==='ROLLBACK'?'configuration-operations':'operations';return observed(await this.client.request(this.path(owner)+category+'/'+encodeURIComponent(entry.record_ref.id)+'?request_sha256='+entry.request_sha256,'GET',undefined,signal),entry.record_ref.id,entry.request_sha256);}
 async sourceStatus(owner:ExperienceOwner,ref:NativeRef,signal?:AbortSignal){const v=await this.client.request<Record<string,unknown>>(this.path(owner)+'source-policy/'+encodeURIComponent(ref.id)+'?sha256='+ref.sha256,'GET',undefined,signal);check(same(v.candidate_ref,ref)&&v.project_id===owner.project_id&&v.session_id===owner.session_ref.id&&typeof v.availability==='string'&&Number.isInteger(v.revision)&&Number(v.revision)>=0&&Number(v.revision)<=33);return copy(v);}
 async pendingStatus(owner:ExperienceOwner,operation:string,sha:string,signal?:AbortSignal){check(id(operation)&&experienceHash(sha));return observed(await this.client.request(this.path(owner)+'pending-configuration/'+encodeURIComponent(operation)+'?request_sha256='+sha,'GET',undefined,signal),operation,sha);}
 async previewPromotion(owner:ExperienceOwner,body:ExperiencePromotionBasis,signal?:AbortSignal){const s=experienceOwner(owner),value=await this.client.request<Record<string,unknown>>(this.path(s)+'promotion/prepare','POST',copy(body),signal,false);check(value?.kind==='EXPERIENCE_PROMOTION_PREVIEW'&&value.read_only===true&&value.model_calls===0&&value.processes_started===0&&value.permission_granted===false&&experienceHash(value.promotion_review_sha256)&&object(value.basis)&&value.basis.project_id===s.project_id&&value.basis.session_id===s.session_ref.id);for(const key of Object.keys(body) as (keyof ExperiencePromotionBasis)[])check(same(value.basis[key],body[key]));return copy(value);}
 async previewRollback(owner:ExperienceOwner,body:ExperienceRollbackBasis,signal?:AbortSignal){const value=await this.client.request<Record<string,unknown>>(this.path(owner)+'rollback/prepare','POST',copy(body),signal,false);check(value?.kind==='EXPERIENCE_ROLLBACK_PREVIEW'&&value.read_only===true&&experienceHash(value.rollback_review_sha256)&&object(value.basis)&&value.basis.action===body.mode&&same(value.basis.current_configuration_ref,body.expected_current_configuration_ref));return copy(value);}
}
export type ExperienceHistoryEntry={kind:'PROPOSAL'|'REVIEW'|'VALIDATION'|'SKILL_VALIDATION'|'ACTIVATION'|'ROLLBACK';record_ref:NativeRef;request_sha256:string;candidate_ref:NativeRef};
