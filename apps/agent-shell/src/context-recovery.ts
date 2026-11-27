import type {AgentSession,ContextAuthorityIssue} from './agent-sessions';
import type {ConversationRef} from './conversation-history';
import {parseCreationReceipt,type CreationRequest,type CreationReceipt} from './conversation-creation';
import {nativeReference,sameNativeReference} from './native-collaboration';

type Transport={request<T>(path:string,method?:string,body?:unknown,signal?:AbortSignal,retryUnauthorized?:boolean):Promise<T>};
type StorageLike=Pick<Storage,'getItem'|'setItem'>;
type RestartRequest={operation_id:string;session_ref:AgentSession['session_ref'];generation:number;conversation_ref:ConversationRef;expected_issue:ContextAuthorityIssue};
export type ContextRecoveryAttempt={request:RestartRequest;checksum:string;phase:'ATTEMPTED'|'NOT_FOUND'|'CONFIRMED'|'ADOPTED';receipt?:CreationReceipt;original_attempt?:{request:RestartRequest;checksum:string;transport_status:'UNKNOWN';original_record_last_observed:'NOT_FOUND';replacement_preparation_sha256:string}};
type Preparation={kind:string;scope:{project_id:string;session_ref:AgentSession['session_ref'];generation:number};conversation_ref:ConversationRef;expected_issue:ContextAuthorityIssue;target_project_id:string|null;existing_recovery:{request:RestartRequest;request_sha256:string;target_project_id:string|null}|null;preparation_sha256:string;read_only:boolean;model_calls:number;native_posts:number;new_conversations:number;permission_granted:boolean;automatic_replay:boolean};
type Result={version:number;operation_id:string;request_sha256:string;project_id:string;session_ref:AgentSession['session_ref'];generation:number;conversation_ref:ConversationRef;phase:'STOP_PENDING'|'STOP_CONFIRMED'|'COMPLETE';stop_operation_id:string;stop_status:string;creation_request:CreationRequest;creation_receipt:unknown;original_history_retained:boolean;drafts_transferred:boolean;source_data_copied:boolean;execution_started:boolean;automatic_post_replay:boolean;model_calls_by_recovery:number};
const PREFIX='eeas-source-context-recovery-v1:',ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,CID=/^(native|original|discovery):[a-f0-9]{64}$/;
const stable=(v:unknown):string=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable((v as Record<string,unknown>)[k])).join(',')+'}':JSON.stringify(v);
async function sha(v:unknown){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(stable(v))))].map(n=>n.toString(16).padStart(2,'0')).join('');}
const check=(v:unknown)=>{if(!v)throw Error('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE');};
const issue=(v:ContextAuthorityIssue|null|undefined,g:number)=>!!v&&['SOURCE_CONTEXT_CHANGED','SOURCE_CONTEXT_UNAVAILABLE','SOURCE_CONTEXT_LEGACY'].includes(v.code)&&v.generation===g&&v.scope==='FUTURE_HOST_ADMISSIONS_AND_API_RELAY'&&v.original_history_retained===true&&(v.operation_id===null||typeof v.operation_id==='string'&&ID.test(v.operation_id));
const restart=(r:RestartRequest|undefined,s:AgentSession)=>!!r&&Object.keys(r).sort().join(',')==='conversation_ref,expected_issue,generation,operation_id,session_ref'&&ID.test(r.operation_id)&&sameNativeReference(r.session_ref,s.session_ref)&&r.generation===s.generation&&CID.test(r.conversation_ref?.id??'')&&Number.isSafeInteger(r.conversation_ref?.revision)&&r.conversation_ref.revision>=1&&issue(r.expected_issue,s.generation);
async function preparation(value:unknown,s:AgentSession,conversation:ConversationRef){
 const v=value as Preparation,basis={kind:v?.kind,scope:v?.scope,conversation_ref:v?.conversation_ref,expected_issue:v?.expected_issue,target_project_id:v?.target_project_id,existing_recovery:v?.existing_recovery};
 check(v?.kind==='CONTEXT_RECOVERY_PREPARATION'&&stable(v.scope)===stable({project_id:s.project_id,session_ref:s.session_ref,generation:s.generation})&&stable(v.conversation_ref)===stable(conversation)&&issue(v.expected_issue,s.generation));
 check((v.target_project_id===null||typeof v.target_project_id==='string'&&ID.test(v.target_project_id))&&v.preparation_sha256===await sha(basis)&&v.read_only===true&&v.model_calls===0&&v.native_posts===0&&v.new_conversations===0&&v.permission_granted===false&&v.automatic_replay===false);
 if(v.existing_recovery!==null){const e=v.existing_recovery;check(restart(e?.request,s)&&e.request.conversation_ref.id===conversation.id&&e.request_sha256===await sha(e.request)&&(e.target_project_id===null||typeof e.target_project_id==='string'&&ID.test(e.target_project_id)));}
 return structuredClone(v);
}

/** Durable original stop key, followed only by metadata creation. A lost reply
 * causes a GET; explicit continuation never repeats an uncertain stop. */
export class ContextRecoveryClient{
 private api:Transport;private storage:StorageLike;private changed:()=>void;private locks:Pick<LockManager,'request'>|null;private key:()=>string;private running=new Set<string>();
 constructor(api:Transport,storage:StorageLike,changed:()=>void=()=>{},locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,key=()=>crypto.randomUUID()){
  this.api=api;this.storage=storage;this.changed=changed;this.locks=locks;this.key=key;
 }
 private scope(s:AgentSession){check(ID.test(s.project_id)&&nativeReference(s.session_ref)&&ID.test(s.session_ref.id)&&Number.isSafeInteger(s.generation)&&s.generation>0);return s.project_id+':'+s.session_ref.id+':'+s.generation;}
 private path(s:AgentSession){return '/api/projects/'+s.project_id+'/agent-sessions/'+s.session_ref.id+'/context-recovery';}
 busy(s:AgentSession){return this.running.has(this.scope(s));}
 private read(s:AgentSession){
  const raw=this.storage.getItem(PREFIX+this.scope(s));check(raw===null||new TextEncoder().encode(raw).length<=32768);
  const record=raw===null?{version:1,pending:null}:JSON.parse(raw),a=record.pending as ContextRecoveryAttempt|null,r=a?.request;
  check(record.version===1&&('pending' in record));
  if(a){check(r&&typeof r.operation_id==='string'&&ID.test(r.operation_id)&&sameNativeReference(r.session_ref,s.session_ref)&&r.generation===s.generation&&r.conversation_ref&&CID.test(r.conversation_ref.id)&&Number.isSafeInteger(r.conversation_ref.revision)&&r.conversation_ref.revision>=1&&issue(r.expected_issue,s.generation)&&SHA.test(a.checksum)&&['ATTEMPTED','NOT_FOUND','CONFIRMED','ADOPTED'].includes(a.phase));check(['ATTEMPTED','NOT_FOUND'].includes(a.phase)||!!a.receipt);}
  if(a?.original_attempt){const old=a.original_attempt;check(restart(old.request,s)&&SHA.test(old.checksum)&&old.transport_status==='UNKNOWN'&&old.original_record_last_observed==='NOT_FOUND'&&SHA.test(old.replacement_preparation_sha256));}
  return {raw,pending:a};
 }
 pending(s:AgentSession){return structuredClone(this.read(s).pending);}
 private save(s:AgentSession,basis:string|null,a:ContextRecoveryAttempt){
  const key=PREFIX+this.scope(s);check(this.storage.getItem(key)===basis);const raw=JSON.stringify({version:1,pending:a});check(new TextEncoder().encode(raw).length<=32768);this.storage.setItem(key,raw);check(this.storage.getItem(key)===raw);this.changed();return raw;
 }
 private async locked<T>(s:AgentSession,work:()=>Promise<T>):Promise<T>{
  const key=this.scope(s);check(!this.running.has(key)&&!!this.locks);this.running.add(key);this.changed();
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
  try{return await this.locks!.request(PREFIX+key,{mode:'exclusive',signal:controller.signal},async()=>{clearTimeout(timer);return work();});}
  finally{clearTimeout(timer);this.running.delete(key);this.changed();}
 }
 private async request<T>(path:string,method='GET',body?:unknown){
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  try{return await Promise.race([this.api.request<T>(path,method,body,controller.signal,false),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(Error('SOURCE_CONTEXT_RECOVERY_PENDING'));},25000);})]);}
  finally{if(timer!==undefined)clearTimeout(timer);}
 }
 private async accept(s:AgentSession,a:ContextRecoveryAttempt,basis:string|null,value:unknown){
  const r=value as Result;check(await sha(a.request)===a.checksum);
  check(r?.version===1&&r.operation_id===a.request.operation_id&&r.request_sha256===a.checksum&&r.project_id===s.project_id&&sameNativeReference(r.session_ref,s.session_ref)&&r.generation===s.generation&&stable(r.conversation_ref)===stable(a.request.conversation_ref));
  check(r.original_history_retained===true&&r.drafts_transferred===false&&r.source_data_copied===false&&r.execution_started===false&&r.automatic_post_replay===false&&r.model_calls_by_recovery===0);
  check(['STOP_PENDING','STOP_CONFIRMED','COMPLETE'].includes(r.phase)&&/^context_stop_[a-f0-9]{48}$/.test(r.stop_operation_id)&&r.creation_request&&/^context_chat_[a-f0-9]{48}$/.test(r.creation_request.operation_id));
  if(r.phase!=='COMPLETE'){check(r.creation_receipt===null);throw Error(r.phase==='STOP_CONFIRMED'?'SOURCE_CONTEXT_RECOVERY_READY':r.stop_status==='NOT_FOUND'?'SOURCE_CONTEXT_RECOVERY_STOP_NOT_RECORDED':'SOURCE_CONTEXT_RECOVERY_PENDING');}
  check(r.stop_status==='RETURNED');
  const receipt=await parseCreationReceipt(r.creation_receipt,{request:r.creation_request,checksum:await sha(r.creation_request),phase:'ATTEMPTED'});
  check(receipt.conversation_ref.id!==a.request.conversation_ref.id);this.save(s,basis,{...a,phase:a.phase==='ADOPTED'?'ADOPTED':'CONFIRMED',receipt});return receipt;
 }
 start(s:AgentSession,conversation:ConversationRef){return this.locked(s,async()=>{
  const before=this.read(s);check(before.pending===null&&issue(s.context_authority_issue,s.generation)&&CID.test(conversation.id));
  const frozen=structuredClone(s),reference=structuredClone(conversation),p=await preparation(await this.request(this.path(s)+'/prepare','POST',{session_ref:frozen.session_ref,generation:frozen.generation}),frozen,reference);
  const request:RestartRequest=p.existing_recovery?.request??{operation_id:this.key(),session_ref:frozen.session_ref,generation:frozen.generation,conversation_ref:reference,expected_issue:p.expected_issue};
  check(ID.test(request.operation_id));const a:ContextRecoveryAttempt={request,checksum:await sha(request),phase:'ATTEMPTED'},basis=this.save(s,before.raw,a);
  const value=p.existing_recovery?await this.request(this.path(s)+'/'+request.operation_id):await this.request(this.path(s),'POST',request);
  return this.accept(s,a,basis,value);
 });}
 inspect(s:AgentSession){return this.locked(s,async()=>{
  const before=this.read(s),a=before.pending;check(a&&await sha(a.request)===a.checksum);
  let value:unknown;
  try{value=await this.request(this.path(s)+'/'+a!.request.operation_id);}
  catch(error){if((error as {status?:number})?.status===404&&a!.phase!=='CONFIRMED'&&a!.phase!=='ADOPTED'){
    // Another surface can win the immutable context claim after our prepare.
    // Preserve our lost transport attempt and inspect that exact winner.
    let p:Preparation|undefined;
    try{p=await preparation(await this.request(this.path(s)+'/prepare','POST',{session_ref:s.session_ref,generation:s.generation}),s,a!.request.conversation_ref);}catch{}
    const existing=p?.existing_recovery;
    if(existing&&existing.request.operation_id!==a!.request.operation_id){
      check(!a!.original_attempt);const adopted:ContextRecoveryAttempt={request:existing.request,checksum:existing.request_sha256,phase:'ATTEMPTED',original_attempt:{request:a!.request,checksum:a!.checksum,transport_status:'UNKNOWN',original_record_last_observed:'NOT_FOUND',replacement_preparation_sha256:p!.preparation_sha256}};
      const basis=this.save(s,before.raw,adopted);return this.accept(s,adopted,basis,await this.request(this.path(s)+'/'+adopted.request.operation_id));
    }
    this.save(s,before.raw,{...a!,phase:'NOT_FOUND'});throw Error('SOURCE_CONTEXT_RECOVERY_NOT_FOUND');
   }throw error;}
  return this.accept(s,a!,before.raw,value);
 });}
 continueCreation(s:AgentSession){return this.locked(s,async()=>{
  const before=this.read(s),a=before.pending;check(a&&await sha(a.request)===a.checksum);
  // An existing stop claim is only inspected. A missing claim may be created
  // by this explicit action under the same original idempotency key.
  return this.accept(s,a!,before.raw,await this.request(this.path(s),'POST',a!.request));
 });}
 acknowledge(s:AgentSession,target:string){return this.locked(s,async()=>{
  const before=this.read(s),a=before.pending;check(a&&['CONFIRMED','ADOPTED'].includes(a.phase)&&a.receipt?.conversation_ref.id===target&&await sha(a.request)===a.checksum);if(a!.phase!=='ADOPTED')this.save(s,before.raw,{...a!,phase:'ADOPTED'});
 });}
}
