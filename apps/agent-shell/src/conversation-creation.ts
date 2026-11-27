import {discussionDraftKey,type DiscussionDraftKey} from './discussion-drafts.ts';
import type {ConversationRef} from './conversation-history';

type Transport={request<T>(path:string,method?:string,body?:unknown,signal?:AbortSignal,retryUnauthorized?:boolean):Promise<T>};
type StorageLike=Pick<Storage,'getItem'|'setItem'>;
export type CreationRequest={operation_id:string;project_id:string|null;title:null};
export type CreationReceipt={version:1;operation_id:string;request_sha256:string;conversation_ref:ConversationRef;project_id:string|null;next_message:{project_id:string|null;conversation_id:string};created_at:string;execution_started:false;messages_created:0;model_calls:0;file_write_authorized:false;automatic_input_authorized:false};
export type PendingCreation={request:CreationRequest;checksum:string;phase:'ATTEMPTED'|'NOT_FOUND'|'CONFIRMED';seedOwner?:DiscussionDraftKey;receipt?:CreationReceipt};
const KEY='eeas-conversation-creation-v1',ID=/^[A-Za-z0-9_-]{1,100}$/,IDENT=/^(native|original|discovery):[a-f0-9]{64}$/,SHA=/^[a-f0-9]{64}$/;
const pid=(value:unknown)=>value===null||typeof value==='string'&&ID.test(value);
const check=(value:unknown)=>{if(!value)throw Error('CREATION_RECEIPT_MISMATCH');};
const clone=<T>(value:T):T=>structuredClone(value);
function canonical(value:unknown):string{return Array.isArray(value)?'['+value.map(canonical).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical((value as Record<string,unknown>)[key])).join(',')+'}':JSON.stringify(value);}
async function checksum(value:unknown){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value))))].map(n=>n.toString(16).padStart(2,'0')).join('');}
export async function parseCreationReceipt(value:unknown,pending:PendingCreation):Promise<CreationReceipt>{
 const r=value as CreationReceipt,expected=await checksum(pending.request);
 check(expected===pending.checksum&&r?.version===1&&r.operation_id===pending.request.operation_id&&r.request_sha256===expected&&r.project_id===pending.request.project_id);
 check(r.conversation_ref&&IDENT.test(r.conversation_ref.id)&&r.conversation_ref.revision===1&&r.next_message?.project_id===r.project_id&&typeof r.next_message.conversation_id==='string'&&/^[A-Za-z0-9_-]{1,200}$/.test(r.next_message.conversation_id));
 check(r.execution_started===false&&r.messages_created===0&&r.model_calls===0&&r.file_write_authorized===false&&r.automatic_input_authorized===false&&typeof r.created_at==='string'&&Number.isFinite(Date.parse(r.created_at)));
 return clone(r);
}
/** A confirmed creation stays journaled until scope, input and selection are saved.
 * A lost acknowledgement is a GET of the original key, never a second creation. */
export class ConversationCreationClient{
 pending:PendingCreation|null=null;busy=false;error='';
 private raw:string|null=null;private api:Transport;private storage:StorageLike;private changed:()=>void;private locks:Pick<LockManager,'request'>|null;private operation:()=>string;private timeoutMs:number;
 constructor(api:Transport,storage:StorageLike,changed:()=>void=()=>{},locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,operation=()=>crypto.randomUUID(),timeoutMs=15000){this.api=api;this.storage=storage;this.changed=changed;this.locks=locks;this.operation=operation;this.timeoutMs=timeoutMs;this.reload();}
 reload(){try{this.hydrate();this.error='';}catch{this.error='CREATION_STORAGE_UNAVAILABLE';}this.changed();}
 private hydrate(){
  const raw=this.storage.getItem(KEY);if(raw!==null&&new TextEncoder().encode(raw).length>32768)throw Error('CREATION_STORAGE_UNAVAILABLE');
  const value=raw===null?{version:1,pending:null}:JSON.parse(raw),p=value.pending as PendingCreation|null;
  if(value.version!==1||p!==null&&(!p?.request||Object.keys(p.request).length!==3||typeof p.request.operation_id!=='string'||!ID.test(p.request.operation_id)||!pid(p.request.project_id)||p.request.title!==null||!SHA.test(p.checksum)||!['ATTEMPTED','NOT_FOUND','CONFIRMED'].includes(p.phase)||(p.phase==='CONFIRMED')!==!!p.receipt))throw Error('CREATION_STORAGE_UNAVAILABLE');
  if(p?.seedOwner){if(Object.keys(p.seedOwner).length!==3)throw Error('CREATION_STORAGE_UNAVAILABLE');discussionDraftKey(p.seedOwner);}
  this.raw=raw;this.pending=p;
 }
 private save(pending:PendingCreation|null){
  if(this.storage.getItem(KEY)!==this.raw)throw Error('CREATION_STORAGE_CONFLICT');
  const next=JSON.stringify({version:1,pending});if(new TextEncoder().encode(next).length>32768)throw Error('CREATION_STORAGE_UNAVAILABLE');
  this.storage.setItem(KEY,next);if(this.storage.getItem(KEY)!==next)throw Error('CREATION_STORAGE_CONFLICT');this.raw=next;this.pending=pending;this.changed();
 }
 private async locked<T>(work:()=>Promise<T>):Promise<T>{
  if(this.busy)throw Error('CREATION_BUSY');if(!this.locks)throw Error('CREATION_LOCK_UNAVAILABLE');
  this.busy=true;this.error='';this.changed();const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
  try{return await this.locks.request(KEY,{mode:'exclusive',signal:controller.signal},async()=>{clearTimeout(timer);this.hydrate();return work();});}
  catch(error){this.error=error instanceof Error&&error.message.startsWith('CREATION_')?error.message:'CREATION_CHECK_REQUIRED';this.changed();throw error;}
  finally{clearTimeout(timer);this.busy=false;this.changed();}
 }
 private async bounded(work:(signal:AbortSignal)=>Promise<unknown>){
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  try{return await Promise.race([work(controller.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(Error('CREATION_CHECK_REQUIRED'));},this.timeoutMs);})]);}
  finally{if(timer!==undefined)clearTimeout(timer);}
 }
 private async confirmed(value:unknown,p:PendingCreation){const receipt=await parseCreationReceipt(value,p);this.save({...p,phase:'CONFIRMED',receipt});return receipt;}
 private async send(p:PendingCreation){
  let value:unknown;
  try{value=await this.bounded(signal=>this.api.request('/api/conversations','POST',p.request,signal,false));}
  catch(error){if([400,401,403,404,409,413,422].includes((error as {status?:number})?.status??0)){this.save(null);throw Error('CREATION_REJECTED');}throw error;}
  return this.confirmed(value,p);
 }
 create(projectId:string|null,seedOwner?:DiscussionDraftKey){return this.locked(async()=>{
  if(this.pending)throw Error('CREATION_CHECK_REQUIRED');if(!pid(projectId))throw Error('CREATION_TARGET_REQUIRED');if(seedOwner)discussionDraftKey(seedOwner);
  const request:CreationRequest={operation_id:this.operation(),project_id:projectId,title:null},p:PendingCreation={request,checksum:await checksum(request),phase:'ATTEMPTED',...(seedOwner?{seedOwner:clone(seedOwner)}:{})};
  this.save(p);return this.send(p);
 });}
 inspect(){return this.locked(async()=>{
  const p=this.pending;if(!p)throw Error('CREATION_NOT_PENDING');
  let value:unknown;
  try{value=await this.bounded(signal=>this.api.request('/api/conversation-creations/'+encodeURIComponent(p.request.operation_id),'GET',undefined,signal));}
  catch(error){if((error as {status?:number})?.status===404){if(p.phase!=='CONFIRMED')this.save({...p,phase:'NOT_FOUND'});throw Error('CREATION_NOT_FOUND');}throw error;}
  return this.confirmed(value,p);
 });}
 retry(){return this.locked(async()=>{const p=this.pending;if(!p||p.phase!=='NOT_FOUND')throw Error('CREATION_CHECK_REQUIRED');if(await checksum(p.request)!==p.checksum)throw Error('CREATION_RECEIPT_MISMATCH');this.save({...p,phase:'ATTEMPTED'});return this.send(p);});}
 acknowledge(operationId:string,id:string){return this.locked(async()=>{const p=this.pending;if(!p||p.phase!=='CONFIRMED'||p.request.operation_id!==operationId||p.receipt?.conversation_ref.id!==id)throw Error('CREATION_CHECK_REQUIRED');await parseCreationReceipt(p.receipt,p);this.save(null);});}
}
