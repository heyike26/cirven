import {checkedDisposition,validDisposition,type ConfigurationDisposition} from './configuration-disposition.ts';
import {configurationRequest,parseConfigurationPreparation,validConfigurationRequest,type ConfigurationRequest} from './conversation-configuration.ts';
import {assertAgentSession,type AgentSession,type AgentSessionsApi,type PreparedSessionOpen,type SessionOpenOptions} from './agent-sessions.ts';
import {nativeReference,sameNativeReference} from './native-collaboration.ts';

type API=Pick<AgentSessionsApi,'prepareOpen'|'openPrepared'|'openOperation'>&Partial<Pick<AgentSessionsApi,'configurationPreparation'|'configurePrepared'|'continueConfiguration'>>;
type StorageLike=Pick<Storage,'getItem'|'setItem'>;
type Owner={project_id:string;session_ref:AgentSession['session_ref'];conversation_bound:boolean;organization_project_id?:string|null};
export type AdoptedConfigurationScope={operation_id:string;conversation_ref:{id:string;revision:number};project_id:string|null;authority_sha256:string;execution_project_id:string|null;current:boolean};
export type PendingConnection={superseded?:ConfigurationDisposition;adopted_scope?:AdoptedConfigurationScope;request:PreparedSessionOpen;checksum:string;phase:'ATTEMPTED'|'NOT_FOUND'|'NOT_COMMITTED'|'FOUND_UNBOUND'|'CONFIRMED';rejectedStatus?:number;owner?:Owner;configuration_request?:ConfigurationRequest;last_status?:'PROCESSING'|'FAILED_NOT_STARTED'|'UNKNOWN_OUTCOME'|'CANDIDATE_READY'|'CANDIDATE_CLOSED';last_read_bound?:boolean};
const IDENT=/^(native|original|discovery):[a-f0-9]{64}$/,ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,PREFIX='eeas-original-connection-v1:';
const canonical=(v:unknown):string=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical((v as Record<string,unknown>)[k])).join(',')+'}':JSON.stringify(v);
async function hash(v:unknown){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(v))))].map(n=>n.toString(16).padStart(2,'0')).join('');}
function requestValue(body:PreparedSessionOpen){const {project_id:_,operation_id:__,...request}=body;return request;}
const check=(v:unknown)=>{if(!v)throw Error('CONNECTION_RECEIPT_MISMATCH');};
/** Each chat owns its own frozen connection attempt. A slow/unknown A never
 * reroutes B, changes a saved configuration, or automatically sends a message. */
export class OriginalConnectionClient{
 private api:API;private storage:StorageLike;private changed:()=>void;private locks:Pick<LockManager,'request'>|null;private operation:()=>string;private timeoutMs:number;
 private running=new Set<string>();
 constructor(api:API,storage:StorageLike,changed:()=>void=()=>{},locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,operation=()=>crypto.randomUUID(),timeoutMs=120000){this.api=api;this.storage=storage;this.changed=changed;this.locks=locks;this.operation=operation;this.timeoutMs=timeoutMs;}
 busy(id:string){return this.running.has(id);}
 private read(id:string){
  if(!IDENT.test(id))throw Error('CONNECTION_SCOPE_REQUIRED');
  const raw=this.storage.getItem(PREFIX+id);if(raw!==null&&new TextEncoder().encode(raw).length>262144)throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  const value=raw===null?{version:1,pending:null}:JSON.parse(raw),p=value.pending as PendingConnection|null,b=p?.request;
  if(value.version!==1||p!==null&&(!b||b.conversation_ref?.id!==id||!Number.isSafeInteger(b.conversation_ref.revision)||b.conversation_ref.revision<1||typeof b.operation_id!=='string'||!ID.test(b.operation_id)||!(b.project_id===null||typeof b.project_id==='string'&&ID.test(b.project_id))||!nativeReference(b.provider_profile_ref)||!SHA.test(p.checksum)||!['ATTEMPTED','NOT_FOUND','NOT_COMMITTED','FOUND_UNBOUND','CONFIRMED'].includes(p.phase)||!Number.isSafeInteger(b.max_calls)||b.max_calls<1||b.max_calls>128||!Number.isSafeInteger(b.ttl_seconds)||b.ttl_seconds<30||b.ttl_seconds>3600))throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  if(p?.configuration_request&&(!SHA.test(p.configuration_request.confirmation_sha256)||!SHA.test(p.configuration_request.preparation_sha256)||canonical(p.configuration_request.request)!==canonical(b)||!['PROCESSING','FAILED_NOT_STARTED','UNKNOWN_OUTCOME','CANDIDATE_READY','CANDIDATE_CLOSED',undefined].includes(p.last_status)))throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  if(p?.owner&&(!nativeReference(p.owner.session_ref)||typeof p.owner.project_id!=='string'||!ID.test(p.owner.project_id)||typeof p.owner.conversation_bound!=='boolean'||b?.project_id!==null&&p.owner.project_id!==b?.project_id&&p.owner.organization_project_id!==b?.project_id))throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  if(p?.adopted_scope){const a=p.adopted_scope;if(a.conversation_ref?.id!==id||!Number.isSafeInteger(a.conversation_ref.revision)||a.conversation_ref.revision<1||!ID.test(a.operation_id)||!SHA.test(a.authority_sha256)||typeof a.current!=='boolean'||!(a.project_id===null||typeof a.project_id==='string'&&ID.test(a.project_id))||a.execution_project_id!==p.configuration_request?.execution_project_id)throw Error('CONNECTION_STORAGE_UNAVAILABLE');}
  if(p?.last_read_bound!==undefined&&typeof p.last_read_bound!=='boolean')throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  if(p&&(['CONFIRMED','FOUND_UNBOUND'].includes(p.phase)&&(!p.owner||p.owner.conversation_bound!==(p.phase==='CONFIRMED'))||p.phase==='NOT_COMMITTED'&&(![400,401,403,404,409,413,422].includes(p.rejectedStatus??0)||p.owner)))throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  if(value.retained_configuration_head!==undefined&&!SHA.test(value.retained_configuration_head))throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  if(p?.superseded&&(!p.configuration_request||!validDisposition(p.superseded,{operation_id:p.request.operation_id,project_id:p.request.project_id,request_sha256:p.checksum,confirmation_sha256:p.configuration_request.confirmation_sha256},id)))throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  return {raw,pending:p,retained:value.retained_configuration_head as string|undefined};
 }
 pending(id:string){return structuredClone(this.read(id).pending);}
 private save(id:string,basis:string|null,pending:PendingConnection|null){
  if(this.storage.getItem(PREFIX+id)!==basis)throw Error('CONNECTION_STORAGE_CONFLICT');
  const retained=basis?JSON.parse(basis).retained_configuration_head:undefined;const raw=JSON.stringify({version:1,pending,...retained?{retained_configuration_head:retained}:{}});if(new TextEncoder().encode(raw).length>262144)throw Error('CONNECTION_STORAGE_UNAVAILABLE');
  this.storage.setItem(PREFIX+id,raw);if(this.storage.getItem(PREFIX+id)!==raw)throw Error('CONNECTION_STORAGE_CONFLICT');this.changed();return raw;
 }
 private async locked<T>(id:string,work:()=>Promise<T>):Promise<T>{
  if(this.running.has(id))throw Error('CONNECTION_BUSY');if(!this.locks)throw Error('CONNECTION_LOCK_UNAVAILABLE');
  this.running.add(id);this.changed();const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
  try{return await this.locks.request(PREFIX+id,{mode:'exclusive',signal:controller.signal},async()=>{clearTimeout(timer);return work();});}
  finally{clearTimeout(timer);this.running.delete(id);this.changed();}
 }
 private async bounded<T>(work:(signal:AbortSignal)=>Promise<T>){const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;try{return await Promise.race([work(controller.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(Error('CONNECTION_CHECK_REQUIRED'));},this.timeoutMs);})]);}finally{if(timer!==undefined)clearTimeout(timer);}}
 private async observed(id:string,p:PendingConnection,basis:string|null){
  check(await hash(requestValue(p.request))===p.checksum);
  if(p.configuration_request)check(await validConfigurationRequest(p.configuration_request,p.request));
  let value:unknown;
  try{value=await this.bounded(signal=>this.api.openOperation(p.request.project_id,p.request.operation_id,signal));}
  catch(error){if((error as {status?:number})?.status===404){if(!p.owner)this.save(id,basis,{...p,phase:p.rejectedStatus?'NOT_COMMITTED':'NOT_FOUND'});throw Error(p.rejectedStatus?'CONNECTION_NOT_COMMITTED':'CONNECTION_NOT_FOUND');}throw error;}
  const r=value as Record<string,unknown>,ref=r?.conversation_ref as {id?:string;revision?:number}|undefined;
  check(r?.version===1&&r.operation_id===p.request.operation_id&&r.request_sha256===p.checksum&&ref?.id===id&&ref.revision===p.request.conversation_ref!.revision);
  check(r.owner_started_by_query===false&&r.model_calls_by_query===0&&r.source_changed_by_query===false&&r.automatic_post_replay===false&&typeof r.conversation_bound==='boolean');
  if(p.configuration_request){const reference={operation_id:p.request.operation_id,project_id:p.request.project_id,request_sha256:p.checksum,confirmation_sha256:p.configuration_request.confirmation_sha256};p={...p,superseded:r.configuration_disposition?await checkedDisposition(r.configuration_disposition,reference,id):undefined};}
  if(p.configuration_request){const a=r.configuration_adoption as AdoptedConfigurationScope|undefined;if(a){check(a.conversation_ref?.id===id&&Number.isSafeInteger(a.conversation_ref.revision)&&a.conversation_ref.revision>=1&&ID.test(a.operation_id)&&SHA.test(a.authority_sha256)&&typeof a.current==='boolean'&&(a.project_id===null||typeof a.project_id==='string'&&ID.test(a.project_id))&&a.execution_project_id===p.configuration_request.execution_project_id);p={...p,adopted_scope:structuredClone(a)};}else if(p.adopted_scope)p={...p,adopted_scope:{...p.adopted_scope,current:false}};}
  if(p.configuration_request){check(r.execution_confirmation_sha256===p.configuration_request.confirmation_sha256&&r.execution_project_id===p.configuration_request.execution_project_id);if(r.session===null){check(['PROCESSING','FAILED_NOT_STARTED','UNKNOWN_OUTCOME'].includes(String(r.status)));this.save(id,basis,{...p,last_status:r.status as PendingConnection['last_status']});throw Error('CONNECTION_ORIGINAL_OWNER_PENDING');}}
  const session=assertAgentSession(r.session);check(session.project_id===r.project_id);
  if(p.configuration_request)check(p.configuration_request.execution_project_id===null||session.project_id===p.configuration_request.execution_project_id);
  check(p.request.project_id===null||session.project_id===p.request.project_id||r.organization_project_id===p.request.project_id);
  if(p.owner)check(session.project_id===p.owner.project_id&&sameNativeReference(session.session_ref,p.owner.session_ref));
  const owner:Owner={project_id:session.project_id,session_ref:session.session_ref,conversation_bound:r.conversation_bound as boolean,...Object.hasOwn(r,'organization_project_id')?{organization_project_id:r.organization_project_id as string|null}:{}};
  const candidateStatus=p.configuration_request&&['CANDIDATE_READY','CANDIDATE_CLOSED'].includes(String(r.status))?r.status as PendingConnection['last_status']:undefined;
  this.save(id,basis,p.phase==='CONFIRMED'&&!owner.conversation_bound?{...p,last_status:undefined,last_read_bound:false}:{...p,owner,phase:owner.conversation_bound?'CONFIRMED':'FOUND_UNBOUND',last_status:candidateStatus,last_read_bound:owner.conversation_bound});return {session,bound:owner.conversation_bound};
 }
 private async posted(id:string,p:PendingConnection,basis:string|null){
  check(await hash(requestValue(p.request))===p.checksum);
  if(p.configuration_request)check(await validConfigurationRequest(p.configuration_request,p.request));
  let session:AgentSession;
  try{session=await this.bounded(signal=>p.configuration_request?(check(this.api.configurePrepared),this.api.configurePrepared!(p.configuration_request!,signal)):this.api.openPrepared(p.request,signal));}
  catch(error){const status=(error as {status?:number})?.status;if(typeof status==='number'&&[400,401,403,404,409,413,422].includes(status))this.save(id,basis,{...p,rejectedStatus:status});throw error;}
  const result=await this.observed(id,p,basis);check(result.session.project_id===session.project_id&&sameNativeReference(result.session.session_ref,session.session_ref));return result;
 }
 open(id:string,options:SessionOpenOptions,current:()=>boolean=()=>true){return this.locked(id,async()=>{
  const old=this.read(id);if(old.pending)throw Error('CONNECTION_CHECK_REQUIRED');
  if(options.configurationPreparation&&!(options.conversationRef&&this.api.configurationPreparation&&this.api.configurePrepared))throw Error('CONFIGURATION_DRAFT_REVIEW_REQUIRED');
  const key=this.operation();let configured:ConfigurationRequest|undefined;
  if(options.conversationRef&&this.api.configurationPreparation&&this.api.configurePrepared){const scope=options.conversationRef,original=options.originalSource??null;const prepared=await parseConfigurationPreparation(await this.bounded(signal=>this.api.configurationPreparation!(scope,original,signal)),scope,options.projectId??null,options.originalSource);if(!current())throw Error('CONNECTION_NAVIGATION_CHANGED');if(options.configurationPreparation&&!await validReviewedPreparation(options.configurationPreparation,prepared,scope,options.projectId??null,options.originalSource))throw Error('CONFIGURATION_DRAFT_REVIEW_REQUIRED');const base=await this.api.prepareOpen({...options,projectId:options.registeredWorkspace?.project_id??options.workspace?.project_id??options.projectId},key);base.project_id=options.projectId??null;configured=await configurationRequest(prepared,base,options);}
  const request=configured?.request??await this.api.prepareOpen(options,key);check(request.conversation_ref?.id===id);if(!current())throw Error('CONNECTION_NAVIGATION_CHANGED');
  const p:PendingConnection={request:structuredClone(request),checksum:await hash(requestValue(request)),phase:'ATTEMPTED',...(configured?{configuration_request:configured}:{})};
  const raw=this.save(id,old.raw,p);return this.posted(id,p,raw);
 });}
 inspect(id:string){return this.locked(id,async()=>{const old=this.read(id);if(!old.pending)throw Error('CONNECTION_NOT_PENDING');return this.observed(id,old.pending,old.raw);});}
 retry(id:string){return this.locked(id,async()=>{
  const old=this.read(id),p=old.pending;if(!p)throw Error('CONNECTION_CHECK_REQUIRED');
  if(p.configuration_request){
   if(!this.api.continueConfiguration||!await validConfigurationRequest(p.configuration_request,p.request))throw Error('CONNECTION_CHECK_REQUIRED');
   // Recheck before the explicit action. A committed or processing owner is
   // never restarted by repeating its original configure/open request.
   try{const observed=await this.observed(id,p,old.raw);if(observed.bound||!['CANDIDATE_READY','CANDIDATE_CLOSED'].includes(this.read(id).pending?.last_status??''))return observed;}catch(error){const checked=this.read(id);if(!['FAILED_NOT_STARTED','CANDIDATE_READY','CANDIDATE_CLOSED'].includes(checked.pending?.last_status??''))throw error;}
   const checked=this.read(id);if(checked.pending?.superseded)throw Error('CONFIGURATION_SUPERSEDED');const next={...checked.pending!,phase:'ATTEMPTED' as const,last_status:undefined};
   const raw=this.save(id,checked.raw,next);
   try{await this.bounded(signal=>this.api.continueConfiguration!(next.configuration_request!,next.checksum,signal));}catch{/* The exact original receipt decides the result, including a lost acknowledgement. */}
   return this.observed(id,next,raw);
  }
  if(!['NOT_FOUND','FOUND_UNBOUND'].includes(p.phase))throw Error('CONNECTION_CHECK_REQUIRED');const raw=this.save(id,old.raw,{...p,phase:'ATTEMPTED'});return this.posted(id,{...p,phase:'ATTEMPTED'},raw);
 });}
 resolveSuperseded(id:string,basisSha:string,current:(value:ConfigurationDisposition)=>boolean){return this.locked(id,async()=>{const before=this.read(id);check(before.pending?.configuration_request);try{await this.observed(id,before.pending!,before.raw);}catch(error){if(!(error instanceof Error)||error.message!=='CONNECTION_ORIGINAL_OWNER_PENDING')throw error;}const saved=this.read(id),p=saved.pending!,d=p?.superseded;check(d&&d.basis_sha256===basisSha&&current(d));await checkedDisposition(d!,{operation_id:p.request.operation_id,project_id:p.request.project_id,request_sha256:p.checksum,confirmation_sha256:p.configuration_request!.confirmation_sha256},id);const node=await hash([p.request.project_id,p.request.operation_id,p.checksum,p.configuration_request!.confirmation_sha256,d!.basis_sha256]),key=PREFIX+'retained:'+id+':'+node,prior=this.storage.getItem(key);if(prior===null){const raw=JSON.stringify({version:1,id:node,previous:saved.retained??null,reason:'SUPERSEDED_CONFIGURATION',pending:p});check(new TextEncoder().encode(raw).length<=270336&&current(d!));this.storage.setItem(key,raw);check(this.storage.getItem(key)===raw);}else{const archived=JSON.parse(prior);check(archived.id===node&&canonical(archived.pending.request)===canonical(p.request)&&canonical(archived.pending.configuration_request)===canonical(p.configuration_request)&&archived.pending.checksum===p.checksum);}
  check(current(d!)&&this.storage.getItem(PREFIX+id)===saved.raw);const raw=JSON.stringify({version:1,pending:null,retained_configuration_head:node});this.storage.setItem(PREFIX+id,raw);check(this.storage.getItem(PREFIX+id)===raw);this.changed();return d!;});}
 async retained(id:string,node?:string){const head=node??this.read(id).retained;if(!head)return null;check(SHA.test(head));const raw=this.storage.getItem(PREFIX+'retained:'+id+':'+head);check(raw&&new TextEncoder().encode(raw!).length<=270336);const saved=JSON.parse(raw!);check(saved.version===1&&saved.id===head&&(saved.previous===null||SHA.test(saved.previous))&&saved.pending.request.conversation_ref.id===id);check(await hash([saved.pending.request.project_id,saved.pending.request.operation_id,saved.pending.checksum,saved.pending.configuration_request.confirmation_sha256,saved.pending.superseded.basis_sha256])===head&&await hash(requestValue(saved.pending.request))===saved.pending.checksum);check(await validConfigurationRequest(saved.pending.configuration_request,saved.pending.request));await checkedDisposition(saved.pending.superseded,{operation_id:saved.pending.request.operation_id,project_id:saved.pending.request.project_id,request_sha256:saved.pending.checksum,confirmation_sha256:saved.pending.configuration_request.confirmation_sha256},id);return structuredClone(saved) as {version:1;id:string;previous:string|null;pending:PendingConnection};}
 acknowledge(id:string,session:Pick<AgentSession,'project_id'|'session_ref'>){return this.locked(id,async()=>{const old=this.read(id),p=old.pending;check(p?.phase==='CONFIRMED'&&p.owner?.conversation_bound===true&&p.owner.project_id===session.project_id&&sameNativeReference(p.owner.session_ref,session.session_ref));check(await hash(requestValue(p!.request))===p!.checksum);this.save(id,old.raw,null);});}
 discardRejected(id:string){return this.locked(id,async()=>{const old=this.read(id),p=old.pending;if(p?.phase!=='NOT_COMMITTED'||!p.rejectedStatus||p.owner)throw Error('CONNECTION_CHECK_REQUIRED');this.save(id,old.raw,null);});}
}

async function validReviewedPreparation(reviewed:import('./conversation-configuration').ConfigurationPreparation,fresh:import('./conversation-configuration').ConfigurationPreparation,scope:import('./native-collaboration').NativeConversationRef,projectId:string|null,original?:import('./conversation-configuration').ConfigurationOwner|null){try{const value=await parseConfigurationPreparation(reviewed,scope,projectId,original);return value.preparation_sha256===fresh.preparation_sha256;}catch{return false;}}

export type OriginalResumeAttempt={project_id:string;session_ref:AgentSession['session_ref'];generation:number;originGeneration?:number;original_session_id:string;parent:string|null;operationId:string;phase:'ATTEMPTED'|'RETURNED'|'FAILED';requestRestored?:boolean;previousOperationIds?:string[]};
const RESUME='eeas-original-resume-v1:';
/** Explicitly load the SAME original owner. This journals only the existing
 * LOAD API; it never grants a pending permission or submits a model message. */
export function originalOwnerCanResume(s?:AgentSession){return !!s&&['EXPIRED','DISCONNECTED','BLOCKED'].includes(s.owner_status)&&s.connected_owner===false&&s.configuration_current===true&&s.generation>0&&!!s.original_session_id&&s.automatic_post_replay===false;}
export function originalOwnerNeedsRecovery(s:AgentSession,a?:OriginalResumeAttempt){
 return a?.phase==='ATTEMPTED'||a?.phase==='FAILED'||!!(a?.parent&&!a.requestRestored)||originalOwnerCanResume(s)&&!!(s.active_operation_id||s.pending_questions.length||s.pending_permissions.length);
}
export class OriginalSessionResumption{
 private running=new Set<string>();
 private api:Pick<AgentSessionsApi,'load'|'operation'|'get'>;private storage:StorageLike;private changed:()=>void;private locks:Pick<LockManager,'request'>|null;private operation:()=>string;private timeoutMs:number;
 constructor(api:Pick<AgentSessionsApi,'load'|'operation'|'get'>,storage:StorageLike,changed:()=>void=()=>{},locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,operation=()=>crypto.randomUUID(),timeoutMs=120000){this.api=api;this.storage=storage;this.changed=changed;this.locks=locks;this.operation=operation;this.timeoutMs=timeoutMs;}
 private async bounded<T>(promise:Promise<T>){let timer:ReturnType<typeof setTimeout>|undefined;try{return await Promise.race([promise,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('CONNECTION_CHECK_REQUIRED')),this.timeoutMs);})]);}finally{if(timer!==undefined)clearTimeout(timer);}}
 private key(s:Pick<AgentSession,'project_id'|'session_ref'|'generation'>){return RESUME+canonical([s.project_id,s.session_ref,s.generation]);}
 private read(s:Pick<AgentSession,'project_id'|'session_ref'|'generation'>){
  const raw=this.storage.getItem(this.key(s));if(raw===null)return {raw,attempt:undefined};
  check(new TextEncoder().encode(raw).length<=8192);const value=JSON.parse(raw),a=value.attempt as OriginalResumeAttempt;
  check(value.version===1&&a&&a.project_id===s.project_id&&sameNativeReference(a.session_ref,s.session_ref)&&a.generation===s.generation&&Number.isSafeInteger(a.generation)&&a.generation>0&&typeof a.original_session_id==='string'&&/^ses_[A-Za-z0-9_-]{1,100}$/.test(a.original_session_id)&&typeof a.operationId==='string'&&ID.test(a.operationId)&&(a.parent===null||typeof a.parent==='string'&&ID.test(a.parent))&&['ATTEMPTED','RETURNED','FAILED'].includes(a.phase)&&(a.requestRestored===undefined||typeof a.requestRestored==='boolean')&&(a.previousOperationIds===undefined||Array.isArray(a.previousOperationIds)&&a.previousOperationIds.length<=32&&a.previousOperationIds.every(id=>typeof id==='string'&&ID.test(id))));
  check(a.originGeneration===undefined||Number.isSafeInteger(a.originGeneration)&&a.originGeneration>0&&a.originGeneration<=a.generation);
  return {raw,attempt:a};
 }
 attempt(s:AgentSession){return structuredClone(this.read(s).attempt);}
 previous(s:AgentSession){if(s.generation<2)return;const a=this.read({...s,generation:s.generation-1}).attempt;return a&&(a.phase==='ATTEMPTED'||a.parent&&!a.requestRestored)&&a.original_session_id===s.original_session_id?structuredClone(a):undefined;}
 busy(s:AgentSession){return this.running.has(this.key(s));}
 private save(s:Pick<AgentSession,'project_id'|'session_ref'|'generation'>,basis:string|null,a:OriginalResumeAttempt){const key=this.key(s);check(this.storage.getItem(key)===basis);const raw=JSON.stringify({version:1,attempt:a});this.storage.setItem(key,raw);check(this.storage.getItem(key)===raw);this.changed();return raw;}
 private async locked<T>(s:AgentSession,work:()=>Promise<T>):Promise<T>{
  const key=this.key(s);if(this.running.has(key)||!this.locks)throw Error('CONNECTION_BUSY');this.running.add(key);this.changed();
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
  try{return await this.locks.request(key,{mode:'exclusive',signal:controller.signal},async()=>{clearTimeout(timer);return work();});}finally{clearTimeout(timer);this.running.delete(key);this.changed();}
 }
 private async observed(s:AgentSession,a:OriginalResumeAttempt,basis:string|null){
  const receipt=await this.bounded(this.api.operation(s,a.operationId)),request={operation_id:a.operationId,session_ref:a.session_ref,expected_generation:a.generation};
  check(receipt.kind==='LOAD'&&receipt.id===a.operationId&&sameNativeReference(receipt.session_ref,a.session_ref)&&receipt.automatic_replay===false&&receipt.request_sha256===await hash(request)&&canonical(receipt.request)===canonical(request));
  if(receipt.status==='FAILED'){this.save(s,basis,{...a,phase:'FAILED'});throw Error('CONNECTION_LOAD_FAILED');}
  check(receipt.status==='RETURNED'&&receipt.loaded_generation===a.generation+1);
  const current=assertAgentSession(await this.bounded(this.api.get(s.project_id,s.session_ref)),a.session_ref,a.project_id);
  check(current.generation===a.generation+1&&current.original_session_id===a.original_session_id);
  this.save(s,basis,{...a,phase:'RETURNED'});return current;
 }
 resume(s:AgentSession,current:()=>boolean=()=>true){return this.locked(s,async()=>{
  check(originalOwnerCanResume(s));const old=this.read(s);if(old.attempt&&old.attempt.phase!=='FAILED')throw Error('CONNECTION_CHECK_REQUIRED');
  const carried=old.attempt??this.previous(s);if(carried?.phase==='ATTEMPTED')throw Error('CONNECTION_CHECK_REQUIRED');
  const previousOperationIds=old.attempt?[...(old.attempt.previousOperationIds??[]),old.attempt.operationId]:[];check(previousOperationIds.length<=32);
  if(!current())throw Error('CONNECTION_NAVIGATION_CHANGED');
  const parent=s.active_operation_id??(carried&&!carried.requestRestored?carried.parent:null)??null;
  const originGeneration=parent&&parent===carried?.parent?(carried.originGeneration??carried.generation):s.generation;
  const a:OriginalResumeAttempt={project_id:s.project_id,session_ref:{...s.session_ref},generation:s.generation,originGeneration,original_session_id:s.original_session_id!,parent,operationId:this.operation(),phase:'ATTEMPTED',previousOperationIds};
  const basis=this.save(s,old.raw,a);await this.bounded(this.api.load(s,a.operationId,a.generation));
  return this.observed(s,a,basis);
 });}
 inspect(s:AgentSession){return this.locked(s,async()=>{const old=this.read(s);if(!old.attempt)throw Error('CONNECTION_NOT_PENDING');return this.observed(s,old.attempt,old.raw);});}
 restored(a:OriginalResumeAttempt){const s={project_id:a.project_id,session_ref:a.session_ref,generation:a.generation},old=this.read(s);check(old.attempt?.phase==='RETURNED'&&old.attempt.operationId===a.operationId);this.save(s,old.raw,{...old.attempt!,requestRestored:true});}
}
