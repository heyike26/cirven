import {api,type ApiClient} from './api';
import type {AgentSession} from './agent-sessions';
import type {SourceRef,FileContent} from './contracts';
import {workRequirementScope,type WorkRequirementScope,type WorkRequirements} from './original-work-requirements';
import {configurationCanonical as canonical,configurationHash as hash} from './conversation-configuration';

export type DeliveryPrepare=WorkRequirementScope&{root_sha256:string;expected_revision:number;version_label:string;notes:string;files:SourceRef[];include_evidence:boolean};
export type DeliveryRequest=DeliveryPrepare&{operation_id:string;review_sha256:string;review_confirmed:true;known_gaps_acknowledged:boolean};
export type DeliveryLookup=WorkRequirementScope&{operation_id:string;request_sha256:string};
export type DeliveryParent={operation_id:string;request_sha256:string;manifest_sha256:string;revision:number};
export type DeliveryFile={source_ref:SourceRef;byte_length:number;archive_path:string};
export type DeliveryFlags={model_calls:0;commands_started:0;source_files_written:0;permission_granted:false;build_performed:false;deployment_performed:false;restore_performed:false;original_task_status_changed:false;whole_task_acceptance:'NOT_ASSESSED';target_environment_verified:false;all_dependencies_captured:false;unsaved_editor_contents_included:false;atomic_workspace_snapshot:false;file_metadata_restoration:false;source_writer_attribution:'NOT_PROVEN'};
export type DeliveryReview=DeliveryFlags&{kind:'ORIGINAL_WORK_DELIVERY_REVIEW';version:1;project_id:string;query_sha256:string;basis:{schema:'cirven.original-work-delivery-review.v1';query:DeliveryPrepare;manifest_sha256:string;root_sha256:string;expected_revision:number};review_sha256:string;manifest_sha256:string;root_basis:WorkRequirements['root_basis'];root_sha256:string;revision:number;parent:DeliveryParent|null;version_label:string;notes:string;files:DeliveryFile[];file_bytes:number;warnings:string[];pending:{operation_id:string;request_sha256:string}[];history_capacity_remaining:number;read_only:true;artifact_files_written:0};
export type DeliveryArtifact={path:string;sha256:string;byte_length:number;filename:string};
export type DeliveryReceipt=DeliveryFlags&{kind:'ORIGINAL_WORK_DELIVERY';version:1;project_id:string;session_ref:AgentSession['session_ref'];root_operation_id:string;root_sha256:string;operation_id:string;request_sha256:string;revision:number;manifest_sha256:string;parent:DeliveryParent|null;version_label:string;notes:string;recorded_at:string;artifact:DeliveryArtifact;download_url:string;files:DeliveryFile[];file_bytes:number;warnings:string[];artifact_files_written:1};
export type DeliveryRetention={operation_id:string;request_sha256:string;statement:string;retained_at:string;status:'UNKNOWN_RETAINED_NOT_CANCELLED_OR_ROLLED_BACK'};
export type DeliveryPending=DeliveryFlags&{kind:'ORIGINAL_WORK_DELIVERY_PENDING';version:1;project_id:string;operation_id:string;request_sha256:string;manifest_sha256:string;root_sha256:string;status:'INCOMPLETE_REVIEW_REQUIRED';retention:DeliveryRetention|null;automatic_retry:false};
export type DeliveryOutcome=DeliveryReceipt|DeliveryPending;
export type DeliveryEntry=Pick<DeliveryReceipt,'operation_id'|'request_sha256'|'manifest_sha256'|'revision'|'parent'|'version_label'|'recorded_at'|'artifact'|'download_url'|'warnings'|'file_bytes'>&{file_count:number;notes_excerpt:string;notes_sha256:string;notes_length:number};
export type Deliveries=DeliveryFlags&{kind:'ORIGINAL_WORK_DELIVERIES';version:1;project_id:string;scope:WorkRequirementScope;query_sha256:string;root_basis:WorkRequirements['root_basis'];root_sha256:string;revision:number;pending:{operation_id:string;request_sha256:string}[];retained:{operation_id:string;request_sha256:string;manifest_sha256:string;retention:{status:DeliveryRetention['status'];retained_at:string;statement_excerpt:string;statement_sha256:string;statement_length:number}}[];entries:DeliveryEntry[];next_offset:number|null;history_capacity_remaining:number;read_only:true};
export type DeliveryIdentity={scope:WorkRequirementScope;root_sha256:string;operation_id:string;request_sha256:string;manifest_sha256?:string};
export type DeliveryRecovery=DeliveryFlags&{kind:'ORIGINAL_WORK_DELIVERY_RECOVERY';version:1;project_id:string;query_sha256:string;root_basis:WorkRequirements['root_basis'];root_sha256:string;manifest_sha256:string;original_request:DeliveryRequest;preview:Pick<DeliveryReceipt,'version_label'|'notes'|'parent'|'files'|'file_bytes'|'warnings'>;preview_sha256:string;outcome:DeliveryOutcome;read_only:true;metadata_only:true;artifact_files_written:0};
export type DeliveryTree={entries:{name:string;path:string;type:'directory'|'file'|'symlink'|'unsupported';readable:boolean;byte_length:number}[];total:number;next_offset:number|null};

const same=(a:unknown,b:unknown)=>canonical(a)===canonical(b);
const bytes=(v:unknown)=>new TextEncoder().encode(canonical(v)).length;
const obj=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:unknown,keys:string[])=>obj(v)&&Object.keys(v).length===keys.length&&Object.keys(v).every(k=>keys.includes(k));
const sha=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const id=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v);
const count=(v:unknown,max=Number.MAX_SAFE_INTEGER):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0&&v<=max;
const text=(v:unknown,max:number):v is string=>typeof v==='string'&&Array.from(v).length<=max&&!/[\0\uD800-\uDFFF]/u.test(v);
const native=(v:unknown)=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v);
function check(value:unknown,code='ORIGINAL_WORK_DELIVERY_CHANGED'):asserts value{if(!value)throw Error(code);}
function reference(v:AgentSession['session_ref']){return exact(v,['id','revision','sha256'])&&id(v.id)&&v.revision===1&&sha(v.sha256);}
function validScope(v:WorkRequirementScope){return exact(v,['session_ref','generation','workspace_ref','root_operation_id'])&&reference(v.session_ref)&&count(v.generation)&&v.generation>=1&&exact(v.workspace_ref,['id','revision','binding_sha256'])&&sha(v.workspace_ref.id)&&count(v.workspace_ref.revision)&&v.workspace_ref.revision>=1&&sha(v.workspace_ref.binding_sha256)&&id(v.root_operation_id);}
export function deliveryArchivePath(path:unknown):string{
 check(text(path,1024)&&path.length>0&&!/[\\:\x00-\x1f\x7f]/.test(path)&&!path.startsWith('/'));
 for(const part of path.split('/'))check(part!==''&&part!=='.'&&part!=='..'&&!/[<>"|?*]/.test(part)&&!/[ .]$/.test(part)&&! /^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)/i.test(part));
 return 'files/'+path;
}
function source(v:SourceRef,s:WorkRequirementScope,pid:string){
 check(exact(v,['kind','project_id','workspace_id','binding_revision','binding_sha256','path','revision','sha256'])&&v.kind==='local_file'&&v.project_id===pid&&v.workspace_id===s.workspace_ref.id&&v.binding_revision===s.workspace_ref.revision&&v.binding_sha256===s.workspace_ref.binding_sha256&&count(v.revision)&&v.revision>=1&&sha(v.sha256));
 deliveryArchivePath(v.path);
}
function prepareQuery(v:DeliveryPrepare,pid:string,editing=false):DeliveryPrepare{
 const {root_sha256,expected_revision,version_label,notes,files,include_evidence,...scope}=v;
 check(validScope(scope)&&sha(root_sha256)&&count(expected_revision,32)&&text(version_label,100)&&(editing||version_label.length>0)&&text(notes,4000)&&(editing||notes.length>0)&&typeof include_evidence==='boolean'&&Array.isArray(files)&&(editing||files.length>=1)&&files.length<=64&&bytes(v)<=131072);
 const names=new Set<string>();for(const f of files){source(f,scope,pid);const name=deliveryArchivePath(f.path).normalize('NFC').toUpperCase();check(!names.has(name));names.add(name);}
 return structuredClone(v);
}
export function validateDeliveryPrepare(v:DeliveryPrepare,pid:string){return prepareQuery(v,pid);}
/** Local text editing may temporarily have no files, version label or notes. */
export function validateDeliveryDraftQuery(v:DeliveryPrepare,pid:string){return prepareQuery(v,pid,true);}
export function validateDeliveryRequest(v:DeliveryRequest,pid:string):DeliveryRequest{
 const {operation_id,review_sha256,review_confirmed,known_gaps_acknowledged,...query}=v;
 check(id(operation_id)&&sha(review_sha256)&&review_confirmed===true&&typeof known_gaps_acknowledged==='boolean'&&bytes(v)<=131072);validateDeliveryPrepare(query,pid);return structuredClone(v);
}
function flags(v:DeliveryFlags){check(v&&v.model_calls===0&&v.commands_started===0&&v.source_files_written===0&&v.permission_granted===false&&v.build_performed===false&&v.deployment_performed===false&&v.restore_performed===false&&v.original_task_status_changed===false&&v.whole_task_acceptance==='NOT_ASSESSED'&&v.target_environment_verified===false&&v.all_dependencies_captured===false&&v.unsaved_editor_contents_included===false&&v.atomic_workspace_snapshot===false&&v.file_metadata_restoration===false&&v.source_writer_attribution==='NOT_PROVEN');}
function parent(v:DeliveryParent|null,previous:number){if(previous===0){check(v===null);return;}check(exact(v,['operation_id','request_sha256','manifest_sha256','revision'])&&id(v?.operation_id)&&sha(v?.request_sha256)&&sha(v?.manifest_sha256)&&v?.revision===previous);}
function warnings(v:string[]){check(Array.isArray(v)&&v.length>=1&&v.length<=1024&&new Set(v).size===v.length&&v.includes('TARGET_AND_WHOLE_TASK_ACCEPTANCE_NOT_ASSESSED')&&v.every(s=>typeof s==='string'&&/^[A-Z0-9_]{1,160}$/.test(s)));}
function inventory(rows:DeliveryFile[],total:number,s:WorkRequirementScope,pid:string,expected?:SourceRef[]){
 check(Array.isArray(rows)&&rows.length>=1&&rows.length<=64&&count(total,16777216)&&(!expected||expected.length===rows.length));let sum=0;const names=new Set<string>();
 for(const [i,row]of rows.entries()){check(exact(row,['source_ref','byte_length','archive_path'])&&count(row.byte_length,16777216));source(row.source_ref,s,pid);check(row.archive_path===deliveryArchivePath(row.source_ref.path)&&(!expected||same(row.source_ref,expected[i])));const name=row.archive_path.normalize('NFC').toUpperCase();check(!names.has(name));names.add(name);sum+=row.byte_length;}
 check(sum===total);
}
async function rootBasis(v:WorkRequirements['root_basis'],checksum:string,pid:string,scope:WorkRequirementScope){
 check(v&&sha(checksum)&&await hash(v)===checksum&&v.project_id===pid&&same(v.session_ref,scope.session_ref)&&same(v.workspace_ref,scope.workspace_ref)&&v.root_operation_id===scope.root_operation_id&&count(v.root_generation,scope.generation)&&v.root_generation>=1&&native(v.native_session_id)&&native(v.native_message_id)&&sha(v.request_sha256));
}
function pointers(v:{operation_id:string;request_sha256:string}[]){check(Array.isArray(v)&&v.length<=1&&v.every(row=>exact(row,['operation_id','request_sha256'])&&id(row.operation_id)&&sha(row.request_sha256)));}
export function deliveryDownloadPath(pid:string,sid:string,root:string,op:string,requestSha:string){return '/api/projects/'+encodeURIComponent(pid)+'/agent-sessions/'+encodeURIComponent(sid)+'/work-deliveries/'+encodeURIComponent(op)+'/archive?'+new URLSearchParams({root_operation_id:root,request_sha256:requestSha});}
function artifact(v:DeliveryArtifact,revision:number){check(exact(v,['path','sha256','byte_length','filename'])&&/^attempts\/0[1-4]\/package\.zip$/.test(v.path)&&sha(v.sha256)&&count(v.byte_length,25165824)&&v.byte_length>=1&&v.filename==='cirven-delivery-'+revision+'.zip');}
export async function validateDeliveryReview(v:DeliveryReview,pid:string,query:DeliveryPrepare){
 validateDeliveryPrepare(query,pid);check(v&&bytes(v)<=131072);flags(v);
 check(v.kind==='ORIGINAL_WORK_DELIVERY_REVIEW'&&v.version===1&&v.project_id===pid&&v.query_sha256===await hash(query)&&v.root_sha256===query.root_sha256&&v.revision===query.expected_revision&&sha(v.manifest_sha256)&&v.read_only===true&&v.artifact_files_written===0&&v.version_label===query.version_label&&v.notes===query.notes&&count(v.history_capacity_remaining,32));
 const expected={schema:'cirven.original-work-delivery-review.v1',query,manifest_sha256:v.manifest_sha256,root_sha256:query.root_sha256,expected_revision:query.expected_revision};
 check(same(v.basis,expected)&&v.review_sha256===await hash(expected));await rootBasis(v.root_basis,v.root_sha256,pid,query);parent(v.parent,query.expected_revision);inventory(v.files,v.file_bytes,query,pid,query.files);warnings(v.warnings);pointers(v.pending);return structuredClone(v);
}
export async function validateDeliveryOutcome(v:DeliveryOutcome,pid:string,identity:DeliveryIdentity,expected?:DeliveryRequest){
 check(validScope(identity.scope)&&sha(identity.root_sha256)&&id(identity.operation_id)&&sha(identity.request_sha256)&&v&&bytes(v)<=131072);flags(v);
 check(v.version===1&&v.project_id===pid&&v.operation_id===identity.operation_id&&v.request_sha256===identity.request_sha256&&v.root_sha256===identity.root_sha256&&sha(v.manifest_sha256)&&(!identity.manifest_sha256||identity.manifest_sha256===v.manifest_sha256));
 if(expected){validateDeliveryRequest(expected,pid);check(await hash(expected)===identity.request_sha256&&expected.operation_id===identity.operation_id&&expected.root_sha256===identity.root_sha256&&expected.root_operation_id===identity.scope.root_operation_id&&same(expected.session_ref,identity.scope.session_ref)&&same(expected.workspace_ref,identity.scope.workspace_ref)&&expected.generation<=identity.scope.generation);}
 if(v.kind==='ORIGINAL_WORK_DELIVERY_PENDING'){
  check(v.status==='INCOMPLETE_REVIEW_REQUIRED'&&v.automatic_retry===false);
  if(v.retention!==null){const r=v.retention;check(r&&r.operation_id===v.operation_id&&r.request_sha256===v.request_sha256&&text(r.statement,4000)&&r.statement.length>=1&&text(r.retained_at,100)&&r.retained_at.length>0&&r.status==='UNKNOWN_RETAINED_NOT_CANCELLED_OR_ROLLED_BACK'&&bytes(r)<=16384);}
 }else{
  check(v.kind==='ORIGINAL_WORK_DELIVERY'&&same(v.session_ref,identity.scope.session_ref)&&v.root_operation_id===identity.scope.root_operation_id&&count(v.revision,32)&&v.revision>=1&&v.artifact_files_written===1&&text(v.version_label,100)&&v.version_label.length>=1&&text(v.notes,4000)&&v.notes.length>=1&&text(v.recorded_at,100)&&v.recorded_at.length>=1);
  check(!expected||v.revision===expected.expected_revision+1&&v.version_label===expected.version_label&&v.notes===expected.notes);
  parent(v.parent,v.revision-1);inventory(v.files,v.file_bytes,identity.scope,pid,expected?.files);warnings(v.warnings);artifact(v.artifact,v.revision);
  check(v.download_url===deliveryDownloadPath(pid,identity.scope.session_ref.id,identity.scope.root_operation_id,v.operation_id,v.request_sha256));
 }
 return structuredClone(v);
}
export async function validateDeliveries(v:Deliveries,pid:string,scope:WorkRequirementScope,offset:number){
 check(validScope(scope)&&count(offset,32)&&v&&bytes(v)<=131072);flags(v);
 check(v.kind==='ORIGINAL_WORK_DELIVERIES'&&v.version===1&&v.project_id===pid&&same(v.scope,scope)&&v.query_sha256===await hash({...scope,offset})&&v.read_only===true&&count(v.revision,32)&&count(v.history_capacity_remaining,32));
 await rootBasis(v.root_basis,v.root_sha256,pid,scope);pointers(v.pending);
 check(Array.isArray(v.entries)&&v.entries.length===Math.min(10,Math.max(0,v.revision-offset))&&v.next_offset===(offset+10<v.revision?offset+10:null)&&Array.isArray(v.retained)&&v.retained.length<=32&&v.revision+v.retained.length+v.pending.length+v.history_capacity_remaining===32);
 const ops=new Set<string>();for(const [i,e]of v.entries.entries()){
  check(e&&id(e.operation_id)&&!ops.has(e.operation_id)&&sha(e.request_sha256)&&sha(e.manifest_sha256)&&e.revision===offset+i+1&&text(e.version_label,100)&&e.version_label.length>0&&text(e.recorded_at,100)&&e.recorded_at.length>0&&count(e.file_count,64)&&e.file_count>=1&&count(e.file_bytes,16777216)&&text(e.notes_excerpt,240)&&count(e.notes_length,4000)&&e.notes_length>=Array.from(e.notes_excerpt).length&&sha(e.notes_sha256));
  ops.add(e.operation_id);parent(e.parent,e.revision-1);if(i>0){const p=v.entries[i-1];check(same(e.parent,{operation_id:p.operation_id,request_sha256:p.request_sha256,manifest_sha256:p.manifest_sha256,revision:p.revision}));}artifact(e.artifact,e.revision);warnings(e.warnings);check(e.download_url===deliveryDownloadPath(pid,scope.session_ref.id,scope.root_operation_id,e.operation_id,e.request_sha256));
  if(e.notes_length<=240)check(e.notes_length===Array.from(e.notes_excerpt).length&&await utf8Hash(e.notes_excerpt)===e.notes_sha256);
 }
 for(const r of v.retained){const k=r.retention;check(r&&id(r.operation_id)&&!ops.has(r.operation_id)&&sha(r.request_sha256)&&sha(r.manifest_sha256)&&k&&k.status==='UNKNOWN_RETAINED_NOT_CANCELLED_OR_ROLLED_BACK'&&text(k.retained_at,100)&&k.retained_at.length>0&&text(k.statement_excerpt,240)&&count(k.statement_length,4000)&&k.statement_length>=Array.from(k.statement_excerpt).length&&sha(k.statement_sha256));ops.add(r.operation_id);if(k.statement_length<=240)check(k.statement_length===Array.from(k.statement_excerpt).length&&await utf8Hash(k.statement_excerpt)===k.statement_sha256);}
 for(const p of v.pending)check(!ops.has(p.operation_id));return structuredClone(v);
}
export async function validateDeliveryRecovery(v:DeliveryRecovery,pid:string,identity:DeliveryIdentity){
 check(validScope(identity.scope)&&v&&bytes(v)<=524288);flags(v);
 const query={...identity.scope,operation_id:identity.operation_id,request_sha256:identity.request_sha256};
 check(v.kind==='ORIGINAL_WORK_DELIVERY_RECOVERY'&&v.version===1&&v.project_id===pid&&v.query_sha256===await hash(query)&&v.root_sha256===identity.root_sha256&&sha(v.manifest_sha256)&&(!identity.manifest_sha256||identity.manifest_sha256===v.manifest_sha256)&&v.read_only===true&&v.metadata_only===true&&v.artifact_files_written===0);
 await rootBasis(v.root_basis,v.root_sha256,pid,identity.scope);const r=validateDeliveryRequest(v.original_request,pid);
 const {operation_id,review_sha256,review_confirmed,known_gaps_acknowledged,...originalQuery}=r;
 check(r.root_sha256===v.root_sha256&&r.operation_id===identity.operation_id&&await hash(r)===identity.request_sha256&&r.review_sha256===await hash({schema:'cirven.original-work-delivery-review.v1',query:originalQuery,manifest_sha256:v.manifest_sha256,root_sha256:r.root_sha256,expected_revision:r.expected_revision}));
 await validateDeliveryOutcome(v.outcome,pid,{...identity,manifest_sha256:v.manifest_sha256},r);
 const p=v.preview;check(p&&bytes(p)<=131072&&v.preview_sha256===await hash(p)&&p.version_label===r.version_label&&p.notes===r.notes);
 inventory(p.files,p.file_bytes,identity.scope,pid,r.files);parent(p.parent,r.expected_revision);warnings(p.warnings);check(p.warnings.includes('EVIDENCE_CONTENT_NOT_INCLUDED')===!r.include_evidence);
 if(v.outcome.kind==='ORIGINAL_WORK_DELIVERY')check(same(p,{version_label:v.outcome.version_label,notes:v.outcome.notes,parent:v.outcome.parent,files:v.outcome.files,file_bytes:v.outcome.file_bytes,warnings:v.outcome.warnings}));
 return structuredClone(v);
}
async function utf8Hash(value:string){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(b=>b.toString(16).padStart(2,'0')).join('');}
export function validateDeliveryTree(v:DeliveryTree,path:string,offset:number){
 check(typeof path==='string'&&count(offset,10000));if(path)deliveryArchivePath(path);
 check(exact(v,['entries','total','next_offset'])&&bytes(v)<=262144&&count(v.total,10000)&&Array.isArray(v.entries)&&v.entries.length===Math.min(100,Math.max(0,v.total-offset))&&v.next_offset===(offset+100<v.total?offset+100:null));
 const seen=new Set<string>();for(const e of v.entries){check(exact(e,['name','path','type','readable','byte_length'])&&text(e.name,255)&&e.name.length>0&&!/[\\/]/.test(e.name)&&e.path===(path?path+'/':'')+e.name&&!seen.has(e.path)&&['directory','file','symlink','unsupported'].includes(e.type)&&typeof e.readable==='boolean'&&(!e.readable||['directory','file'].includes(e.type))&&count(e.byte_length));seen.add(e.path);}
 return structuredClone(v);
}
export class OriginalWorkDeliveriesApi{
 constructor(private client:Pick<ApiClient,'request'|'bytes'>=api){}
 private path(pid:string,sid:string){check(id(pid)&&id(sid));return '/api/projects/'+encodeURIComponent(pid)+'/agent-sessions/'+encodeURIComponent(sid)+'/work-deliveries';}
 async prepare(s:AgentSession,input:DeliveryPrepare,signal?:AbortSignal){const pid=s.project_id,query=validateDeliveryPrepare(structuredClone(input),pid),path=this.path(pid,s.session_ref.id);check(same(workRequirementScope(s,query.root_operation_id),((({root_sha256,expected_revision,version_label,notes,files,include_evidence,...scope})=>scope)(query))));return validateDeliveryReview(await this.client.request<DeliveryReview>(path+'/prepare','POST',query,signal,false),pid,query);}
 async list(s:AgentSession,root:string,offset=0,signal?:AbortSignal){const pid=s.project_id,scope=workRequirementScope(s,root),path=this.path(pid,s.session_ref.id);check(count(offset,32));return validateDeliveries(await this.client.request<Deliveries>(path+'/list','POST',{...scope,offset},signal,false),pid,scope,offset);}
 async submit(s:AgentSession,input:DeliveryRequest,manifestSha:string,signal?:AbortSignal){const pid=s.project_id,request=validateDeliveryRequest(structuredClone(input),pid),scope=workRequirementScope(s,request.root_operation_id),path=this.path(pid,s.session_ref.id);check(sha(manifestSha)&&same(scope,{session_ref:request.session_ref,generation:request.generation,workspace_ref:request.workspace_ref,root_operation_id:request.root_operation_id}));const identity={scope,root_sha256:request.root_sha256,operation_id:request.operation_id,request_sha256:await hash(request),manifest_sha256:manifestSha};return validateDeliveryOutcome(await this.client.request<DeliveryOutcome>(path+'/packages','POST',request,signal,false),pid,identity,request);}
 async inspect(s:AgentSession,identity:DeliveryIdentity,signal?:AbortSignal,expected?:DeliveryRequest){return this.lookup('operations/inspect',s,identity,undefined,signal,expected);}
 async recovery(s:AgentSession,input:DeliveryIdentity,signal?:AbortSignal){const pid=s.project_id,identity=structuredClone(input),scope=structuredClone(workRequirementScope(s,identity.scope.root_operation_id));check(same(scope,identity.scope)&&sha(identity.root_sha256)&&id(identity.operation_id)&&sha(identity.request_sha256));const path=this.path(pid,scope.session_ref.id),query={...scope,operation_id:identity.operation_id,request_sha256:identity.request_sha256};return validateDeliveryRecovery(await this.client.request<DeliveryRecovery>(path+'/operations/recovery','POST',query,signal,false),pid,identity);}
 async continue(s:AgentSession,identity:DeliveryIdentity,signal?:AbortSignal,expected?:DeliveryRequest){return this.lookup('continue',s,identity,undefined,signal,expected);}
 async retain(s:AgentSession,identity:DeliveryIdentity,statement:string,signal?:AbortSignal,expected?:DeliveryRequest){check(text(statement,4000)&&statement.length>0);const value=await this.lookup('retain',s,identity,statement,signal,expected);check(value.kind==='ORIGINAL_WORK_DELIVERY'||value.retention?.statement===statement);return value;}
 private async lookup(action:string,s:AgentSession,input:DeliveryIdentity,statement?:string,signal?:AbortSignal,expectedInput?:DeliveryRequest){const pid=s.project_id,identity=structuredClone(input),expected=expectedInput&&validateDeliveryRequest(structuredClone(expectedInput),pid),scope=workRequirementScope(s,identity.scope.root_operation_id),path=this.path(pid,s.session_ref.id);check(validScope(identity.scope)&&same(scope,identity.scope)&&sha(identity.root_sha256)&&id(identity.operation_id)&&sha(identity.request_sha256)&&(!identity.manifest_sha256||sha(identity.manifest_sha256)));const body={...scope,operation_id:identity.operation_id,request_sha256:identity.request_sha256,...(statement===undefined?{}:{statement})};return validateDeliveryOutcome(await this.client.request<DeliveryOutcome>(path+'/'+action,'POST',body,signal,false),pid,identity,expected);}
 async download(s:AgentSession,root:string,input:DeliveryReceipt,signal?:AbortSignal){const pid=s.project_id,receipt=structuredClone(input),identity={scope:workRequirementScope(s,root),root_sha256:receipt.root_sha256,operation_id:receipt.operation_id,request_sha256:receipt.request_sha256,manifest_sha256:receipt.manifest_sha256};await validateDeliveryOutcome(receipt,pid,identity);const raw=await this.client.bytes(deliveryDownloadPath(pid,identity.scope.session_ref.id,root,receipt.operation_id,receipt.request_sha256),signal);check(raw.byteLength===receipt.artifact.byte_length&&raw.byteLength<=25165824);const checksum=[...new Uint8Array(await crypto.subtle.digest('SHA-256',raw))].map(b=>b.toString(16).padStart(2,'0')).join('');check(checksum===receipt.artifact.sha256);return {bytes:raw,filename:receipt.artifact.filename,sha256:checksum};}
 async selectSaved(s:AgentSession,root:string,path:string,signal?:AbortSignal){const scope=workRequirementScope(s,root),pid=s.project_id;deliveryArchivePath(path);const url='/api/projects/'+encodeURIComponent(pid)+'/local-workspaces/'+encodeURIComponent(scope.workspace_ref.id)+'/file?'+new URLSearchParams({path}),value=await this.client.request<FileContent>(url,'GET',undefined,signal,false);source(value.source_ref,scope,pid);check(value.source_ref.path===path&&value.current===true&&value.live_sha256===value.source_ref.sha256&&count(value.byte_length,16777216));return {source_ref:structuredClone(value.source_ref),byte_length:value.byte_length};}
 async tree(s:AgentSession,root:string,path='',offset=0,signal?:AbortSignal){const scope=workRequirementScope(s,root),pid=s.project_id;check(typeof path==='string'&&count(offset,10000));if(path)deliveryArchivePath(path);const url='/api/projects/'+encodeURIComponent(pid)+'/local-workspaces/'+encodeURIComponent(scope.workspace_ref.id)+'/tree?'+new URLSearchParams({path,offset:String(offset),limit:'100'});return validateDeliveryTree(await this.client.request<DeliveryTree>(url,'GET',undefined,signal,false),path,offset);}
}
