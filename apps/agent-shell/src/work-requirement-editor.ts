import {api,operationId,type ApiClient} from './api.ts';
import type {AgentSession} from './agent-sessions';
import {OriginalWorkRequirementsApi,parseRequirementLink,validRequirementSourceAuthority,workRequirementScope,type RequirementRef,type RequirementLink,type WorkRequirements,type WorkRequirementScope} from './original-work-requirements.ts';
import {configurationCanonical as canonical,configurationHash as hash} from './conversation-configuration.ts';

type StorageLike=Pick<Storage,'getItem'|'setItem'>;
type Owner={project_id:string;session_ref:AgentSession['session_ref'];workspace_ref:WorkRequirementScope['workspace_ref'];root_operation_id:string};
export type WorkRequirementChoice={source_authority?:import('./original-work-requirements').RequirementSourceAuthority;requirement_ref:RequirementRef;requirement_ids:string[];acceptance_ids:string[]};
export type WorkRequirementDraft={scope:WorkRequirementScope;root_sha256:string;revision:number;choice:WorkRequirementChoice;basis:unknown};
export type WorkRequirementRequest=WorkRequirementScope&WorkRequirementChoice&{operation_id:string;root_sha256:string;expected_revision:number;action:'ASSOCIATE'|'REMOVE'};
export type WorkRequirementAttempt={request:WorkRequirementRequest;checksum:string;phase:'PENDING'|'UNKNOWN'|'REJECTED'|'RECORDED';initial_transport_unknown:boolean;last_observation:'NOT_CHECKED'|'NOT_FOUND'|'RECORDED';error_code:string|null;receipt:RequirementLink|null};
type Retained={attempt:WorkRequirementAttempt;disposition:{root_sha256:string;generation:number;revision:number}|null};
type DraftCopy={id:string;draft:WorkRequirementDraft};
export type WorkRequirementLocal={version:1;owner:Owner;revision:number;draft:WorkRequirementDraft|null;recovery:DraftCopy[];attempt:WorkRequirementAttempt|null;retained:Retained[]};
export const workRequirementPendingLocal=(value:WorkRequirementLocal)=>!!value.attempt&&value.attempt.phase!=='RECORDED'||value.retained.some(r=>r.disposition===null&&['PENDING','UNKNOWN'].includes(r.attempt.phase));
const PREFIX='cirven-work-requirement-v1:',same=(a:unknown,b:unknown)=>canonical(a)===canonical(b),copy=<T>(value:T):T=>structuredClone(value);
const sha=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const id=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v);
const count=(v:unknown,max=Number.MAX_SAFE_INTEGER):v is number=>Number.isSafeInteger(v)&&Number(v)>=0&&Number(v)<=max;
const object=(v:unknown):v is Record<string,unknown>=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:unknown,names:string[])=>object(v)&&Object.keys(v).length===names.length&&Object.keys(v).every(k=>names.includes(k));
function check(value:unknown,code='WORK_REQUIREMENT_LOCAL_INVALID'):asserts value{if(!value)throw Error(code);}
function reference(value:unknown):value is RequirementRef{const r=value as RequirementRef;return exact(r,['id','revision','sha256'])&&id(r.id)&&count(r.revision,2147483647)&&r.revision>=1&&sha(r.sha256);}
function ids(value:unknown):value is string[]{return Array.isArray(value)&&value.length<=100&&value.every(v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,40}$/.test(v))&&new Set(value).size===value.length;}
function choice(value:unknown):value is WorkRequirementChoice{const c=value as WorkRequirementChoice;return exact(c,['requirement_ref','requirement_ids','acceptance_ids',...(c.source_authority!==undefined?['source_authority']:[])])&&(c.source_authority===undefined||validRequirementSourceAuthority(c.source_authority))&&reference(c.requirement_ref)&&ids(c.requirement_ids)&&ids(c.acceptance_ids);}
function scope(value:unknown,owner:Owner):value is WorkRequirementScope{const s=value as WorkRequirementScope;return exact(s,['session_ref','generation','workspace_ref','root_operation_id'])&&same(s.session_ref,owner.session_ref)&&same(s.workspace_ref,owner.workspace_ref)&&s.root_operation_id===owner.root_operation_id&&count(s.generation)&&s.generation>=1;}
function request(value:unknown,owner:Owner):value is WorkRequirementRequest{const r=value as WorkRequirementRequest;return exact(r,['session_ref','generation','workspace_ref','root_operation_id','operation_id','root_sha256','expected_revision','action','requirement_ref','requirement_ids','acceptance_ids',...(r.source_authority!==undefined?['source_authority']:[])])&&(r.source_authority===undefined||validRequirementSourceAuthority(r.source_authority)&&r.source_authority.source_project_id!==owner.project_id)&&scope({session_ref:r.session_ref,generation:r.generation,workspace_ref:r.workspace_ref,root_operation_id:r.root_operation_id},owner)&&id(r.operation_id)&&sha(r.root_sha256)&&count(r.expected_revision,512)&&reference(r.requirement_ref)&&ids(r.requirement_ids)&&ids(r.acceptance_ids)&&(r.action==='ASSOCIATE'?r.requirement_ids.length>0:r.action==='REMOVE'&&r.requirement_ids.length===0&&r.acceptance_ids.length===0);}
function draft(value:unknown,owner:Owner):value is WorkRequirementDraft{const d=value as WorkRequirementDraft;return exact(d,['scope','root_sha256','revision','choice','basis'])&&scope(d.scope,owner)&&sha(d.root_sha256)&&count(d.revision,512)&&choice(d.choice)&&object(d.basis);}
function attempt(value:unknown,owner:Owner):value is WorkRequirementAttempt{const a=value as WorkRequirementAttempt;if(!exact(a,['request','checksum','phase','initial_transport_unknown','last_observation','error_code','receipt'])||!request(a.request,owner)||!sha(a.checksum)||!['PENDING','UNKNOWN','REJECTED','RECORDED'].includes(a.phase)||typeof a.initial_transport_unknown!=='boolean'||!['NOT_CHECKED','NOT_FOUND','RECORDED'].includes(a.last_observation)||!(a.error_code===null||typeof a.error_code==='string'&&/^[A-Z0-9_]{1,100}$/.test(a.error_code)))return false;
 if(a.receipt===null)return a.phase!=='RECORDED';try{verifyReceipt(a.receipt,a.request,a.checksum,owner);return a.phase==='RECORDED'&&a.last_observation==='RECORDED';}catch{return false;}}
function parse(raw:string|null,owner:Owner):WorkRequirementLocal{
 if(raw===null)return {version:1,owner:copy(owner),revision:0,draft:null,recovery:[],attempt:null,retained:[]};
 check(new TextEncoder().encode(raw).length<=524288,'WORK_REQUIREMENT_LOCAL_CAPACITY');const value=JSON.parse(raw) as WorkRequirementLocal;
 check(exact(value,['version','owner','revision','draft','recovery','attempt','retained'])&&value.version===1&&same(value.owner,owner)&&count(value.revision)&&(value.draft===null||draft(value.draft,owner))&&(value.attempt===null||attempt(value.attempt,owner))&&Array.isArray(value.recovery)&&value.recovery.length<=4&&value.recovery.every(r=>exact(r,['id','draft'])&&id(r.id)&&draft(r.draft,owner))&&new Set(value.recovery.map(r=>r.id)).size===value.recovery.length&&Array.isArray(value.retained)&&value.retained.length<=8&&value.retained.every(r=>exact(r,['attempt','disposition'])&&attempt(r.attempt,owner)&&(r.disposition===null||exact(r.disposition,['root_sha256','generation','revision'])&&sha(r.disposition.root_sha256)&&count(r.disposition.generation)&&r.disposition.generation>=1&&count(r.disposition.revision,512))));return value;
}
function ownerOf(session:AgentSession,root:string):Owner{const s=workRequirementScope(session,root);return {project_id:session.project_id,session_ref:copy(s.session_ref),workspace_ref:copy(s.workspace_ref),root_operation_id:root};}
export function workRequirementLocalKey(session:AgentSession,root:string){return PREFIX+canonical(ownerOf(session,root));}
function scopedView(owner:Owner,view:WorkRequirements){check(view.project_id===owner.project_id&&scope(view.scope,owner)&&sha(view.root_sha256),'WORK_REQUIREMENT_SCOPE_CHANGED');}
function verifyReceipt(value:RequirementLink,r:WorkRequirementRequest,checksum:string,owner:Owner){parseRequirementLink(value,{project_id:owner.project_id,session_ref:owner.session_ref} as AgentSession,owner.root_operation_id,r.root_sha256);check(value.operation_id===r.operation_id&&value.request_sha256===checksum&&value.revision===r.expected_revision+1&&value.action===r.action&&same(value.requirement_ref,r.requirement_ref)&&same(value.requirement_ids,r.requirement_ids)&&same(value.acceptance_ids,r.acceptance_ids)&&same(value.source_authority,r.source_authority),'WORK_REQUIREMENT_RECEIPT_CHANGED');return copy(value);}

/** The draft keeps only selected IDs and their reviewed fingerprints, never a second spec. */
export function workRequirementDraft(view:WorkRequirements,selected:WorkRequirementChoice):WorkRequirementDraft{
 check(choice(selected)&&view.selected&&same(view.selected.ref,selected.requirement_ref),'WORK_REQUIREMENT_SELECTION_UNAVAILABLE');
 const visible=view.selected,requirements=[...selected.requirement_ids].sort().map(key=>{const row=visible.requirements.find(r=>r.id===key),source=visible.sources.find(r=>r.id===key);return row?{id:key,text_sha256:row.text_sha256,source_count:row.source_count,source_status:source?.status??'UNAVAILABLE'}:{id:key,missing:true};}),acceptance=[...selected.acceptance_ids].sort().map(key=>{const row=visible.acceptance.find(r=>r.id===key);return row?{id:key,text_sha256:row.text_sha256,requirement_ids:[...row.requirement_ids].sort()}:{id:key,missing:true};});
 return {scope:copy(view.scope),root_sha256:view.root_sha256,revision:view.revision,choice:copy(selected),basis:{...(visible.source_authority?{source_authority:copy(visible.source_authority)}:{}),ref:copy(visible.ref),confirmed:visible.confirmed,origin:visible.origin,current_revision:visible.current_revision,requirements,acceptance}};
}
export function workRequirementDraftMatches(view:WorkRequirements,d:WorkRequirementDraft){try{return same(workRequirementDraft(view,d.choice),d);}catch{return false;}}
export function workRequirementCanSave(view:WorkRequirements,d:WorkRequirementDraft){try{check(workRequirementDraftMatches(view,d),'WORK_REQUIREMENT_REVIEW_REQUIRED');const v=view.selected!;check(same(v.source_authority,d.choice.source_authority),'WORK_REQUIREMENT_SOURCE_AUTHORITY_CHANGED');check(v.confirmed&&v.current_revision===d.choice.requirement_ref.revision&&d.choice.requirement_ids.length>0,'WORK_REQUIREMENT_SELECTION_STALE');check(d.choice.requirement_ids.every(key=>v.requirements.some(r=>r.id===key)&&!v.sources.some(r=>r.id===key&&['SUPERSEDED','UNAVAILABLE'].includes(r.status))),'WORK_REQUIREMENT_SELECTION_STALE');check(d.choice.acceptance_ids.every(key=>v.acceptance.some(a=>a.id===key&&a.requirement_ids.every(id=>d.choice.requirement_ids.includes(id)))),'WORK_REQUIREMENT_INCOMPLETE_CRITERION');return true;}catch{return false;}}
export function workRequirementUnchanged(view:WorkRequirements,d:WorkRequirementDraft){const old=view.links.find(r=>r.requirement_ref.id===d.choice.requirement_ref.id);return !!old&&same(old.requirement_ref,d.choice.requirement_ref)&&same([...old.requirement_ids].sort(),[...d.choice.requirement_ids].sort())&&same([...old.acceptance_ids].sort(),[...d.choice.acceptance_ids].sort())&&same(old.source_authority,d.choice.source_authority);}

const dirty=new Set<WorkRequirementJournal>(),queued=new Set<WorkRequirementJournal>();
export async function flushWorkRequirementDrafts(){try{for(;;){const all=[...new Set([...dirty,...queued])];if(!all.length)return;await Promise.all(all.map(j=>j.flush()));}}catch{throw Error('WORK_REQUIREMENT_DRAFT_UNSAVED');}}
export class WorkRequirementJournal{
 readonly owner:Owner;readonly key:string;private raw:string|null;private local:WorkRequirementLocal;private changed=false;private tail:Promise<unknown>=Promise.resolve();private listeners=new Set<()=>void>();private active=false;private waiting=0;
 constructor(readonly session:AgentSession,readonly root:string,private storage:StorageLike,private locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,private newId=()=>crypto.randomUUID()){
  this.owner=ownerOf(session,root);this.key=workRequirementLocalKey(session,root);this.raw=storage.getItem(this.key);this.local=parse(this.raw,this.owner);
 }
 value(){return copy(this.local);}unsaved(){return this.changed;}running(){return this.active||this.waiting>0;}observed(){return this.listeners.size>0;}
 subscribe(fn:()=>void){this.listeners.add(fn);return ()=>this.listeners.delete(fn);}
 private emit(){for(const listener of this.listeners)listener();}
 edit(next:WorkRequirementDraft){check(draft(next,this.owner));const state=copy(this.local);if(state.draft&&!same(state.draft.choice.requirement_ref,next.choice.requirement_ref)&&!state.recovery.some(r=>same(r.draft,state.draft))){check(state.recovery.length<4,'WORK_REQUIREMENT_DRAFT_CAPACITY');state.recovery.push({id:this.newId(),draft:copy(state.draft)});}state.draft=copy(next);state.revision++;this.local=state;this.changed=true;dirty.add(this);this.emit();return this.value();}
 private write(next:WorkRequirementLocal,basis=this.raw){const raw=JSON.stringify(next);check(new TextEncoder().encode(raw).length<=524288,'WORK_REQUIREMENT_LOCAL_CAPACITY');this.local=copy(next);this.changed=true;dirty.add(this);this.emit();check(this.storage.getItem(this.key)===basis,'WORK_REQUIREMENT_DRAFT_CONFLICT');this.storage.setItem(this.key,raw);check(this.storage.getItem(this.key)===raw,'WORK_REQUIREMENT_DRAFT_CONFLICT');this.raw=raw;this.changed=false;dirty.delete(this);this.emit();return this.value();}
 private async locked<T>(run:()=>Promise<T>){check(this.locks,'WORK_REQUIREMENT_LOCK_UNAVAILABLE');const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);try{return await this.locks.request(this.key,{mode:'exclusive',signal:controller.signal},async()=>{clearTimeout(timer);this.active=true;this.emit();try{return await run();}finally{this.active=false;this.emit();}});}finally{clearTimeout(timer);}}
 serial<T>(run:()=>Promise<T>){this.waiting++;queued.add(this);const next=this.tail.catch(()=>{}).then(()=>this.locked(run)).finally(()=>{this.waiting--;if(!this.waiting)queued.delete(this);this.emit();});this.tail=next;return next;}
 flush(){return this.serial(async()=>{if(!this.changed)return this.value();return this.write(this.local);});}
 async resolve(choice:'LOCAL'|'SAVED'){return this.serial(async()=>{
  const raw=this.storage.getItem(this.key),saved=parse(raw,this.owner),local=this.local;
  const selected=choice==='LOCAL'?local.draft:saved.draft,recovery=copy(saved.recovery);
  const keepDraft=(d:WorkRequirementDraft|null,id?:string)=>{
   if(!d||same(d,selected)||recovery.some(r=>same(r.draft,d)))return;
   check(recovery.length<4,'WORK_REQUIREMENT_DRAFT_CAPACITY');
   recovery.push({id:id&&!recovery.some(r=>r.id===id)?id:this.newId(),draft:copy(d)});
  };
  for(const row of local.recovery)keepDraft(row.draft,row.id);
  keepDraft(choice==='LOCAL'?saved.draft:local.draft);
  let currentAttempt=saved.attempt;const retained=copy(saved.retained);
  const merge=(before:WorkRequirementAttempt,after:WorkRequirementAttempt)=>{
   check(same(before.request,after.request),'WORK_REQUIREMENT_LOCAL_INVALID');
   if(after.phase==='RECORDED')return copy(after);
   if(before.phase==='RECORDED')return copy(before);
   const unknown=before.initial_transport_unknown||after.initial_transport_unknown||before.phase==='UNKNOWN'||after.phase==='UNKNOWN';
   return {...copy(after),phase:unknown?'UNKNOWN':after.phase,initial_transport_unknown:unknown,
    last_observation:before.last_observation==='NOT_FOUND'||after.last_observation==='NOT_FOUND'?'NOT_FOUND':after.last_observation} as WorkRequirementAttempt;
  };
  const keepAttempt=(row:Retained)=>{
   if(currentAttempt?.checksum===row.attempt.checksum){currentAttempt=merge(currentAttempt,row.attempt);return;}
   const before=retained.find(r=>r.attempt.checksum===row.attempt.checksum);
   if(before){before.attempt=merge(before.attempt,row.attempt);return;}
   check(retained.length<8,'WORK_REQUIREMENT_RECOVERY_CAPACITY');retained.push(copy(row));
  };
  for(const row of local.retained)keepAttempt(row);
  if(local.attempt)keepAttempt({attempt:local.attempt,disposition:null});
  return this.write({...saved,revision:Math.max(saved.revision,local.revision)+1,draft:copy(selected),recovery,attempt:currentAttempt,retained},raw);
 });}
 async restore(id:string){return this.serial(async()=>{check(!this.changed,'WORK_REQUIREMENT_DRAFT_UNSAVED');const state=copy(this.local),slot=state.recovery.find(r=>r.id===id);check(slot&&state.draft);const before=state.draft;state.draft=slot.draft;slot.draft=before;state.revision++;return this.write(state);});}
 async deleteCopy(id:string){return this.serial(async()=>{check(!this.changed,'WORK_REQUIREMENT_DRAFT_UNSAVED');const state=copy(this.local);check(state.recovery.some(r=>r.id===id));state.recovery=state.recovery.filter(r=>r.id!==id);state.revision++;return this.write(state);});}
 async restoreAttempt(checksum:string){return this.serial(async()=>{
  const state=this.current(),index=state.retained.findIndex(r=>r.attempt.checksum===checksum);
  check(index>=0&&(!state.attempt||['RECORDED','REJECTED'].includes(state.attempt.phase)),'WORK_REQUIREMENT_PENDING');
  const [row]=state.retained.splice(index,1);
  if(state.attempt)state.retained.push({attempt:state.attempt,disposition:null});
  state.attempt=row.attempt;state.revision++;return this.write(state);
 });}
 current(){check(!this.changed,'WORK_REQUIREMENT_DRAFT_UNSAVED');check(this.storage.getItem(this.key)===this.raw,'WORK_REQUIREMENT_DRAFT_CONFLICT');return this.value();}
 saveAttempt(a:WorkRequirementAttempt){check(attempt(a,this.owner));const state=copy(this.local);state.attempt=copy(a);state.revision++;return this.write(state);}
 saveRetained(a:WorkRequirementAttempt){check(attempt(a,this.owner));const state=copy(this.local),row=state.retained.find(r=>r.attempt.checksum===a.checksum);check(row&&same(row.attempt.request,a.request),'WORK_REQUIREMENT_NO_PENDING');row.attempt=copy(a);state.revision++;return this.write(state);}
 accept(a:WorkRequirementAttempt,receipt:RequirementLink){const verified=verifyReceipt(receipt,a.request,a.checksum,this.owner),state=copy(this.local);state.attempt={...copy(a),phase:'RECORDED',last_observation:'RECORDED',error_code:null,receipt:verified};const d=state.draft;if(d&&d.revision===a.request.expected_revision&&same(d.scope,{session_ref:a.request.session_ref,generation:a.request.generation,workspace_ref:a.request.workspace_ref,root_operation_id:a.request.root_operation_id})&&d.root_sha256===a.request.root_sha256&&same(d.choice,{requirement_ref:a.request.requirement_ref,requirement_ids:a.request.requirement_ids,acceptance_ids:a.request.acceptance_ids,...(a.request.source_authority?{source_authority:a.request.source_authority}:{})})&&a.request.action==='ASSOCIATE')state.draft={...d,revision:receipt.revision};state.revision++;return this.write(state);}
 async retain(view:WorkRequirements){return this.serial(async()=>{const state=this.current(),a=state.attempt;scopedView(this.owner,view);check(a&&a.phase!=='RECORDED'&&view.root_sha256===a.request.root_sha256,'WORK_REQUIREMENT_PENDING');const advanced=view.scope.generation>a.request.generation||view.revision>a.request.expected_revision;check(a.phase==='REJECTED'||a.last_observation==='NOT_FOUND'&&advanced,'WORK_REQUIREMENT_PENDING');check(state.retained.length<8,'WORK_REQUIREMENT_RECOVERY_CAPACITY');state.retained.push({attempt:copy(a),disposition:advanced?{root_sha256:view.root_sha256,generation:view.scope.generation,revision:view.revision}:null});state.attempt=null;state.revision++;return this.write(state);});}
}

const journals=new WeakMap<object,Map<string,WorkRequirementJournal>>();
export function workRequirementJournal(storage:StorageLike,session:AgentSession,root:string){let map=journals.get(storage);if(!map){map=new Map();journals.set(storage,map);}const key=workRequirementLocalKey(session,root),old=map.get(key);if(old)return old;if(map.size>=64){const disposable=[...map].find(([,j])=>!j.unsaved()&&!j.running()&&!j.observed());check(disposable,'WORK_REQUIREMENT_LOCAL_CAPACITY');map.delete(disposable[0]);}const current=new WorkRequirementJournal(session,root,storage);map.set(key,current);return current;}

export class WorkRequirementActions{
 constructor(private journal:WorkRequirementJournal,private reader=new OriginalWorkRequirementsApi(),private transport:Pick<ApiClient,'request'>=api,private newId=operationId){}
 private async bounded<T>(run:(signal:AbortSignal)=>Promise<T>){const abort=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;try{return await Promise.race([run(abort.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{abort.abort();reject(Error('WORK_REQUIREMENT_TRANSPORT_UNKNOWN'));},25000);})]);}finally{if(timer!==undefined)clearTimeout(timer);}}
 private call<T>(path:string,body:unknown){return this.bounded(signal=>this.transport.request<T>(path,'POST',body,signal,false));}
 private path(){const o=this.journal.owner;return '/api/projects/'+encodeURIComponent(o.project_id)+'/agent-sessions/'+encodeURIComponent(o.session_ref.id)+'/work-requirements/links';}
 private async transmit(a:WorkRequirementAttempt){check(await hash(a.request)===a.checksum,'WORK_REQUIREMENT_LOCAL_INVALID');let value:RequirementLink;
  try{value=await this.call<RequirementLink>(this.path(),copy(a.request));}
  catch(error){const status=(error as {status?:number})?.status,rejected=!a.initial_transport_unknown&&Number.isInteger(status)&&Number(status)>=400&&Number(status)<500&&![408,429].includes(Number(status));const next:WorkRequirementAttempt={...a,phase:rejected?'REJECTED':'UNKNOWN',initial_transport_unknown:a.initial_transport_unknown||!rejected,error_code:rejected?'HTTP_REJECTED':'TRANSPORT_UNKNOWN'};this.journal.saveAttempt(next);return this.journal.value().attempt!;}
  // Validation/persistence failure after a returned response never becomes a new POST.
  let receipt:RequirementLink;try{receipt=verifyReceipt(value,a.request,a.checksum,this.journal.owner);}catch{this.journal.saveAttempt({...a,phase:'UNKNOWN',initial_transport_unknown:true,error_code:'RECEIPT_CHANGED'});throw Error('WORK_REQUIREMENT_RECEIPT_CHANGED');}this.journal.accept(a,receipt);return this.journal.value().attempt!;
 }
 private async pending(){const a=this.journal.value().attempt;check(a&&a.phase!=='RECORDED','WORK_REQUIREMENT_NO_PENDING');check(await hash(a.request)===a.checksum,'WORK_REQUIREMENT_LOCAL_INVALID');return a;}
 private async inspectAttempt(session:AgentSession,a:WorkRequirementAttempt){let value:RequirementLink;
  try{value=await this.bounded(signal=>this.reader.operation(session,this.journal.root,a.request.root_sha256,a.request.operation_id,a.checksum,signal));}
  catch(error){if((error as {status?:number})?.status===404){this.journal.saveAttempt({...a,phase:a.phase==='REJECTED'?'REJECTED':'UNKNOWN',initial_transport_unknown:a.phase==='REJECTED'?a.initial_transport_unknown:true,last_observation:'NOT_FOUND'});return this.journal.value().attempt!;}throw error;}
  this.journal.accept(a,verifyReceipt(value,a.request,a.checksum,this.journal.owner));return this.journal.value().attempt!;
 }
 inspect(session:AgentSession){return this.journal.serial(async()=>this.inspectAttempt(session,await this.pending()));}
 inspectRetained(session:AgentSession,checksum:string){return this.journal.serial(async()=>{
  const a=this.journal.value().retained.find(r=>r.attempt.checksum===checksum)?.attempt;
  check(a&&await hash(a.request)===a.checksum,'WORK_REQUIREMENT_LOCAL_INVALID');
  if(a.phase==='RECORDED')return a;
  let receipt:RequirementLink;
  try{receipt=await this.bounded(signal=>this.reader.operation(session,this.journal.root,a.request.root_sha256,a.request.operation_id,a.checksum,signal));}
  catch(error){if((error as {status?:number})?.status!==404)throw error;const next:WorkRequirementAttempt={...a,phase:a.phase==='REJECTED'?'REJECTED':'UNKNOWN',initial_transport_unknown:a.phase==='REJECTED'?a.initial_transport_unknown:true,last_observation:'NOT_FOUND'};this.journal.saveRetained(next);return next;}
  const next:WorkRequirementAttempt={...a,phase:'RECORDED',last_observation:'RECORDED',error_code:null,receipt:verifyReceipt(receipt,a.request,a.checksum,this.journal.owner)};
  this.journal.saveRetained(next);return next;
 });}
 async submit(session:AgentSession,reviewed:WorkRequirements,action:'ASSOCIATE'|'REMOVE',target?:RequirementLink){
  await this.journal.flush();return this.journal.serial(async()=>{const state=this.journal.current();check(!workRequirementPendingLocal(state),'WORK_REQUIREMENT_PENDING');scopedView(this.journal.owner,reviewed);const d=state.draft;if(action==='ASSOCIATE')check(d&&workRequirementCanSave(reviewed,d)&&!workRequirementUnchanged(reviewed,d),'WORK_REQUIREMENT_REVIEW_REQUIRED');else check(target&&reviewed.links.some(r=>same(r,target)),'WORK_REQUIREMENT_REVIEW_REQUIRED');
   const selected=action==='ASSOCIATE'?d!.choice.requirement_ref:null,fresh=await this.bounded(signal=>this.reader.inspect(session,this.journal.root,{selected,...(action==='ASSOCIATE'&&d!.choice.source_authority?{sourceAuthority:d!.choice.source_authority}:{}),catalog:false},signal));scopedView(this.journal.owner,fresh);check(same(fresh.scope,reviewed.scope)&&fresh.root_sha256===reviewed.root_sha256&&fresh.revision===reviewed.revision,'WORK_REQUIREMENT_REVIEW_REQUIRED');if(action==='ASSOCIATE')check(workRequirementCanSave(fresh,d!),'WORK_REQUIREMENT_REVIEW_REQUIRED');else check(fresh.links.some(r=>same(r,target)),'WORK_REQUIREMENT_REVIEW_REQUIRED');
   const r:WorkRequirementRequest={...copy(fresh.scope),operation_id:this.newId(),root_sha256:fresh.root_sha256,expected_revision:fresh.revision,action,requirement_ref:copy(action==='ASSOCIATE'?d!.choice.requirement_ref:target!.requirement_ref),requirement_ids:action==='ASSOCIATE'?[...d!.choice.requirement_ids]:[],acceptance_ids:action==='ASSOCIATE'?[...d!.choice.acceptance_ids]:[],...(action==='ASSOCIATE'&&d!.choice.source_authority?{source_authority:copy(d!.choice.source_authority)}:{})};check(request(r,this.journal.owner));
   const a:WorkRequirementAttempt={request:r,checksum:await hash(r),phase:'PENDING',initial_transport_unknown:false,last_observation:'NOT_CHECKED',error_code:null,receipt:null};this.journal.saveAttempt(a);return this.transmit(a);
  });
 }
 async retry(session:AgentSession){await this.journal.flush();return this.journal.serial(async()=>{let a=await this.pending();const observed=await this.inspectAttempt(session,a);if(observed.phase==='RECORDED')return observed;a=observed;check(a.last_observation==='NOT_FOUND','WORK_REQUIREMENT_PENDING');const current=await this.bounded(signal=>this.reader.inspect(session,this.journal.root,{catalog:false},signal));scopedView(this.journal.owner,current);check(same(current.scope,{session_ref:a.request.session_ref,generation:a.request.generation,workspace_ref:a.request.workspace_ref,root_operation_id:a.request.root_operation_id})&&current.root_sha256===a.request.root_sha256&&current.revision===a.request.expected_revision,'WORK_REQUIREMENT_REVIEW_REQUIRED');this.journal.saveAttempt({...a,phase:'PENDING'});return this.transmit({...a,phase:'PENDING'});});}
}
