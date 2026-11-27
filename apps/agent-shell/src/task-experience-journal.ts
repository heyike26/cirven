import type {AgentSession} from './agent-sessions';
import {AuthoredUIError} from './authored-ui-copy';
import type {NativeRef} from './native-collaboration';
import {nativeReference} from './native-collaboration';
import {configurationCanonical as canonical,configurationHash as hash} from './conversation-configuration';
import {experienceScope,experienceOwner,experienceHash,assertExperiencePreparation,TaskExperienceApi,type ExperienceContext,type ExperienceAction,type ExperienceRequests,type PreparedExperience,type ExperienceObservation} from './task-experience';

type StoragePort=Pick<Storage,'getItem'|'setItem'>;
export type ExperienceJournalOwner=ReturnType<typeof ownerOf>;
export type ExperienceDraft={action:ExperienceAction;generation:number;root_sha256:string;candidate_ref:NativeRef|null;review_ref:NativeRef|null;fields:Record<string,string>};
export type ExperienceSummary={kind:string;operation_id:string;request_sha256:string;record_ref:NativeRef|null;decision_ref:NativeRef|null;candidate_ref:NativeRef|null;review_ref:NativeRef|null;result_sha256:string|null;status:string|null;native_status:string|null;decision_state:string|null;qualified:boolean|null;content_withheld:boolean;source_availability:string|null;source_revision:number|null;control_revision:number|null;control_effective_state:'ACTIVE'|'PAUSED'|null;local_finalization_needed:boolean|null;configuration_finalization_needed:boolean|null;control_record_kind:'CONTROL_DECISION'|'COMPLETION_PACKET_RESERVED_ONLY'|null};
export type ExperienceAttempt={packet:PreparedExperience;witness_sha256:string;phase:'PREPARED'|'PENDING'|'UNKNOWN'|'OBSERVED';initial_unknown:boolean;last_lookup:'NOT_CHECKED'|'FOUND'|'NOT_FOUND';summary:ExperienceSummary|null;retained_note:string|null;transmissions:number;draft_snapshot?:ExperienceDraft|null};
export type ExperienceLocal={version:1;owner:ExperienceJournalOwner;revision:number;draft:ExperienceDraft|null;copies:{id:string;draft:ExperienceDraft}[];attempts:ExperienceAttempt[]};
const MAX_BYTES=8388608,MAX_ATTEMPTS=256,MAX_JOURNALS=8;
const NEGATIVE_SLOTS=16,NEGATIVE_BYTES=524288;
const actions:ExperienceAction[]=['PROPOSE','REVIEW','TOOL_VALIDATE','SKILL_VALIDATE','PROMOTE','ROLLBACK','SOURCE_DECISION','PENDING_CONTROL','RECOVER_CONFIGURATION'];
const copy=<T>(v:T):T=>structuredClone(v),same=(a:unknown,b:unknown)=>canonical(a)===canonical(b),bytes=(v:unknown)=>new TextEncoder().encode(JSON.stringify(v)).length;
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:unknown,keys:string[])=>object(v)&&Object.keys(v).length===keys.length&&Object.keys(v).every(k=>keys.includes(k));
const id=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v);
const count=(v:unknown,max=Number.MAX_SAFE_INTEGER):v is number=>Number.isSafeInteger(v)&&Number(v)>=0&&Number(v)<=max;
const text=(v:unknown,max:number):v is string=>typeof v==='string'&&Array.from(v).length<=max&&!/[\0\uD800-\uDFFF]/u.test(v);
function check(v:unknown,code='EXPERIENCE_LOCAL_INVALID'):asserts v{if(!v)throw Error(code);}
function ownerOf(s:ExperienceContext,root:string){const {generation,...scope}=experienceScope(s,root);return {...experienceOwner(s),workspace_ref:scope.workspace_ref,root_operation_id:root};}
export const experienceLocalKey=(s:ExperienceContext,root:string)=>'cirven-task-experience-v1:'+canonical(ownerOf(s,root));
function validDraft(v:ExperienceDraft,o:ExperienceJournalOwner){
 check(exact(v,['action','generation','root_sha256','candidate_ref','review_ref','fields'])&&actions.includes(v.action)&&count(v.generation)&&v.generation>=1&&experienceHash(v.root_sha256)&&(v.candidate_ref===null||nativeReference(v.candidate_ref))&&(v.review_ref===null||nativeReference(v.review_ref))&&object(v.fields)&&Object.keys(v.fields).length<=32&&Object.entries(v.fields).every(([key,value])=>/^[a-z][a-z0-9_]{0,63}$/.test(key)&&text(value,key==='cases'?262144:65536))&&bytes(v)<=270336);
 check(id(o.root_operation_id));
}
function ownedPacket(p:PreparedExperience,o:ExperienceJournalOwner){
 assertExperiencePreparation(p,o,p.action);
 if(p.action==='PROPOSE'){
  const r=p.request as ExperienceRequests['PROPOSE'];check(same(r.workspace_ref,o.workspace_ref)&&r.root_operation_id===o.root_operation_id,'EXPERIENCE_SCOPE_CHANGED');
 }
}
function expectedIdentity(p:PreparedExperience){return {operation_id:p.request.operation_id,request_sha256:p.action==='RECOVER_CONFIGURATION'?(p.request as ExperienceRequests['RECOVER_CONFIGURATION']).request_sha256:p.request_sha256};}
function operationIdentity(p:PreparedExperience){if(p.action==='RECOVER_CONFIGURATION')return 'RECOVER:'+p.request.operation_id+':'+(p.request as ExperienceRequests['RECOVER_CONFIGURATION']).mode;if(p.action==='SOURCE_DECISION')return 'SOURCE:'+canonical((p.request as ExperienceRequests['SOURCE_DECISION']).candidate_ref)+':'+p.request.operation_id;if(p.action==='PENDING_CONTROL')return 'CONTROL:'+p.request.operation_id;return 'CORE:'+p.request.operation_id;}
function summaryOf(v:ExperienceObservation,p:PreparedExperience):ExperienceSummary{
 const expected=expectedIdentity(p);check(v.operation_id===expected.operation_id&&v.request_sha256===expected.request_sha256,'EXPERIENCE_ORIGINAL_CHANGED');
 const source=object(v.source_policy)?v.source_policy:object(v.current)?v.current:null,control=object(v.capability_control)?v.capability_control:null,observation=object(v.observation)?v.observation:null;
 const ref=(value:unknown)=>nativeReference(value)?copy(value):null,short=(value:unknown)=>text(value,100)?value:null,bool=(value:unknown)=>typeof value==='boolean'?value:null;
 return {kind:short(v.kind)??p.action,...expected,record_ref:ref(v.record_ref),decision_ref:ref(v.decision_ref),candidate_ref:ref(v.candidate_ref),review_ref:ref(v.review_ref),result_sha256:experienceHash(v.result_sha256)?v.result_sha256:null,
  status:v.execution_status==='UNKNOWN'||Array.isArray(v.cases)&&v.cases.some(row=>object(row)&&row.status==='UNKNOWN')?'UNKNOWN':short(v.status),native_status:short(observation?.status),decision_state:short(v.decision_state??v.state),qualified:bool(v.qualified),content_withheld:v.content_withheld===true,
  source_availability:short(source?.availability),source_revision:count(source?.revision,33)?source.revision:null,
  control_revision:count(control?.revision,65)?control.revision:null,control_effective_state:control?.effective_state==='ACTIVE'||control?.effective_state==='PAUSED'?control.effective_state:null,
  local_finalization_needed:bool(v.local_finalization_needed),configuration_finalization_needed:bool(v.configuration_finalization_needed),
  control_record_kind:v.control_record_kind==='CONTROL_DECISION'||v.control_record_kind==='COMPLETION_PACKET_RESERVED_ONLY'?v.control_record_kind:null};
}
function validSummary(v:ExperienceSummary,p:PreparedExperience){
 const keys=['kind','operation_id','request_sha256','record_ref','decision_ref','candidate_ref','review_ref','result_sha256','status','native_status','decision_state','qualified','content_withheld','source_availability','source_revision','control_revision','control_effective_state','local_finalization_needed','configuration_finalization_needed','control_record_kind'];
 check(exact(v,keys)&&text(v.kind,100)&&same({operation_id:v.operation_id,request_sha256:v.request_sha256},expectedIdentity(p))&&['record_ref','decision_ref','candidate_ref','review_ref'].every(k=>v[k as keyof ExperienceSummary]===null||nativeReference(v[k as keyof ExperienceSummary]))&&(v.result_sha256===null||experienceHash(v.result_sha256))&&['status','native_status','decision_state','source_availability'].every(k=>v[k as keyof ExperienceSummary]===null||text(v[k as keyof ExperienceSummary],100))&&['qualified','local_finalization_needed','configuration_finalization_needed'].every(k=>v[k as keyof ExperienceSummary]===null||typeof v[k as keyof ExperienceSummary]==='boolean')&&typeof v.content_withheld==='boolean'&&(v.source_revision===null||count(v.source_revision,33))&&(v.control_revision===null||count(v.control_revision,65))&&[null,'ACTIVE','PAUSED'].includes(v.control_effective_state)&&[null,'CONTROL_DECISION','COMPLETION_PACKET_RESERVED_ONLY'].includes(v.control_record_kind)&&bytes(v)<=4096);
}
export function experienceUnresolved(a:ExperienceAttempt):boolean{
 const s=a.summary;if(a.phase!=='OBSERVED'||!s||a.last_lookup==='NOT_FOUND')return true;
 if(s.local_finalization_needed||s.configuration_finalization_needed)return true;
 switch(a.packet.action){
 case 'PROPOSE':case 'REVIEW':return s.record_ref===null;
 case 'TOOL_VALIDATE':case 'SKILL_VALIDATE':return s.result_sha256===null||!['PASS','FAIL','SOURCE_BLOCKED'].includes(s.status??'');
 case 'SOURCE_DECISION':return s.decision_state===null;
 case 'PENDING_CONTROL':return (a.packet.request as ExperienceRequests['PENDING_CONTROL']).mode==='COMPLETE_PAUSED_METADATA'?!['APPLIED_CURRENT','SAVED_SUPERSEDED'].includes(s.native_status??''):s.decision_ref===null;
 default:return !['APPLIED_CURRENT','SAVED_SUPERSEDED'].includes(s.native_status??'');
 }
}
export function validateExperienceLocal(raw:string|null,o:ExperienceJournalOwner):ExperienceLocal{
 if(raw===null)return {version:1,owner:copy(o),revision:0,draft:null,copies:[],attempts:[]};
 check(new TextEncoder().encode(raw).length<=MAX_BYTES,'EXPERIENCE_LOCAL_CAPACITY');const v=JSON.parse(raw) as ExperienceLocal;
 check(exact(v,['version','owner','revision','draft','copies','attempts'])&&v.version===1&&same(v.owner,o)&&count(v.revision)&&Array.isArray(v.copies)&&v.copies.length<=4&&Array.isArray(v.attempts)&&v.attempts.length<=MAX_ATTEMPTS);
 if(v.draft!==null)validDraft(v.draft,o);
 for(const row of v.copies){check(exact(row,['id','draft'])&&id(row.id));validDraft(row.draft,o);}check(new Set(v.copies.map(c=>c.id)).size===v.copies.length);
 for(const a of v.attempts){
  check(exact(a,['packet','witness_sha256','phase','initial_unknown','last_lookup','summary','retained_note','transmissions',...(a.draft_snapshot!==undefined?['draft_snapshot']:[])])&&experienceHash(a.witness_sha256)&&['PREPARED','PENDING','UNKNOWN','OBSERVED'].includes(a.phase)&&typeof a.initial_unknown==='boolean'&&['NOT_CHECKED','FOUND','NOT_FOUND'].includes(a.last_lookup)&&(a.retained_note===null||text(a.retained_note,2000)&&a.retained_note.trim().length>0)&&count(a.transmissions,9));check(a.phase==='PREPARED'?a.transmissions===0&&!a.initial_unknown&&a.summary===null&&a.last_lookup!=='FOUND':a.phase==='OBSERVED'||a.transmissions>=1);ownedPacket(a.packet,o);if(a.draft_snapshot)validDraft(a.draft_snapshot,o);
  if(a.summary!==null){validSummary(a.summary,a.packet);check(a.phase==='OBSERVED'&&a.last_lookup!=='NOT_CHECKED');}else check(a.phase!=='OBSERVED');
 }
 check(new Set(v.attempts.map(a=>a.witness_sha256)).size===v.attempts.length);
 check(new Set(v.attempts.map(a=>operationIdentity(a.packet))).size===v.attempts.length,'EXPERIENCE_ORIGINAL_CHANGED');
 check(bytes(v)+v.attempts.reduce((sum,a)=>sum+4096-bytes(a.summary),0)+1024<=MAX_BYTES,'EXPERIENCE_LOCAL_CAPACITY');
 return v;
}
async function witnesses(v:ExperienceLocal){for(const a of v.attempts)check(await hash(a.packet)===a.witness_sha256,'EXPERIENCE_ORIGINAL_CHANGED');}
const dirty=new Set<ExperienceJournal>(),queued=new Set<ExperienceJournal>();
export async function flushExperienceDrafts(){try{for(;;){const all=[...new Set([...dirty,...queued])];if(!all.length)return;await Promise.all(all.map(j=>j.flush()));}}catch{throw new AuthoredUIError('经验草稿或原操作记录尚需核查；当前输入和已确认结果保留，没有自动重发。');}}

/** Exact user input packets and thin observations, without server output bodies. */
export class ExperienceJournal{
 readonly owner:ExperienceJournalOwner;readonly key:string;private local:ExperienceLocal;private raw:string|null;private changed=false;private waiting=0;private external=0;private listeners=new Set<()=>void>();private tail:Promise<unknown>=Promise.resolve();
 constructor(s:ExperienceContext,root:string,private storage:StoragePort,private locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,private newId=()=>crypto.randomUUID()){
  this.owner=ownerOf(s,root);this.key=experienceLocalKey(s,root);this.raw=storage.getItem(this.key);this.local=validateExperienceLocal(this.raw,this.owner);
  if(this.local.attempts.some(a=>a.phase==='PENDING')){this.local.attempts=this.local.attempts.map(a=>a.phase==='PENDING'?{...a,phase:'UNKNOWN',initial_unknown:true}:a);this.local.revision++;this.changed=true;dirty.add(this);}
 }
 value(){return copy(this.local);}unsaved(){return this.changed;}running(){return this.waiting>0||this.external>0;}observed(){return this.listeners.size>0;}
 draft(){return copy(this.local.draft);}
 view(){return {revision:this.local.revision,draft:this.draft(),copies:this.local.copies.map(c=>({id:c.id,action:c.draft.action,candidate_ref:copy(c.draft.candidate_ref),title:c.draft.fields.title??''})),attempts:this.local.attempts.map(a=>({witness_sha256:a.witness_sha256,action:a.packet.action,operation_id:a.packet.request.operation_id,phase:a.phase,initial_unknown:a.initial_unknown,last_lookup:a.last_lookup,summary:copy(a.summary),retained_note:a.retained_note,transmissions:a.transmissions,has_draft_snapshot:!!a.draft_snapshot,unresolved:experienceUnresolved(a)}))};}
 async inFlight<T>(run:()=>Promise<T>){this.external++;this.emit();try{return await run();}finally{this.external--;this.emit();}}
 subscribe(fn:()=>void){this.listeners.add(fn);return ()=>this.listeners.delete(fn);}private emit(){for(const f of this.listeners)f();}
 async validate(){validateExperienceLocal(JSON.stringify(this.local),this.owner);await witnesses(this.local);return this;}
 current(){check(!this.changed,'EXPERIENCE_LOCAL_UNSAVED');check(this.storage.getItem(this.key)===this.raw,'EXPERIENCE_DRAFT_CONFLICT');return this.value();}
 private write(v:ExperienceLocal,basis=this.raw){this.local=copy(v);this.changed=true;dirty.add(this);this.emit();const raw=JSON.stringify(v);validateExperienceLocal(raw,this.owner);check(this.storage.getItem(this.key)===basis,'EXPERIENCE_DRAFT_CONFLICT');this.storage.setItem(this.key,raw);check(this.storage.getItem(this.key)===raw,'EXPERIENCE_DRAFT_CONFLICT');this.raw=raw;this.changed=this.local.revision!==v.revision;if(this.changed)dirty.add(this);else dirty.delete(this);this.emit();return this.value();}
 serial<T>(run:()=>Promise<T>){this.waiting++;queued.add(this);const next=this.tail.catch(()=>{}).then(async()=>{check(this.locks,'EXPERIENCE_LOCK_UNAVAILABLE');const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);try{return await this.locks.request(this.key,{mode:'exclusive',signal:abort.signal},async()=>{clearTimeout(timer);return run();});}finally{clearTimeout(timer);}}).finally(()=>{this.waiting--;if(!this.waiting)queued.delete(this);this.emit();});this.tail=next;return next;}
 edit(d:ExperienceDraft){validDraft(d,this.owner);this.local.draft=copy(d);this.local.revision++;this.changed=true;dirty.add(this);this.emit();}
 flush(){return this.serial(async()=>{await this.validate();for(let pass=0;this.changed;pass++){check(pass<8,'EXPERIENCE_DRAFT_STILL_CHANGING');this.write(this.local);}return this.value();});}
 select(d:ExperienceDraft){return this.serial(async()=>{validDraft(d,this.owner);const v=this.current();if(v.draft&&!same(v.draft,d)&&!v.copies.some(c=>same(c.draft,v.draft))){check(v.copies.length<4,'EXPERIENCE_DRAFT_COPY_CAPACITY');v.copies.push({id:this.newId(),draft:copy(v.draft)});}v.draft=copy(d);v.revision++;return this.write(v);});}
 parkDraft(){return this.serial(async()=>{const v=this.current();if(v.draft){if(!v.copies.some(c=>same(c.draft,v.draft))){check(v.copies.length<4,'EXPERIENCE_DRAFT_COPY_CAPACITY');v.copies.push({id:this.newId(),draft:copy(v.draft)});}v.draft=null;v.revision++;return this.write(v);}return v;});}
 copyAction(id:string,remove=false){return this.serial(async()=>{const v=this.current(),row=v.copies.find(c=>c.id===id);check(row);if(remove)v.copies=v.copies.filter(c=>c.id!==id);else{const previous=v.draft;v.draft=copy(row.draft);if(previous)row.draft=previous;else v.copies=v.copies.filter(c=>c.id!==id);}v.revision++;return this.write(v);});}
 save(a:ExperienceAttempt){const v=this.value(),i=v.attempts.findIndex(b=>b.witness_sha256===a.witness_sha256);if(i<0){check(v.attempts.length<MAX_ATTEMPTS,'EXPERIENCE_HISTORY_CAPACITY');v.attempts.push(copy(a));}else{const old=v.attempts[i];check(same(old.packet,a.packet)&&same(old.draft_snapshot,a.draft_snapshot)&&(!old.initial_unknown||a.initial_unknown)&&a.transmissions>=old.transmissions&&a.transmissions<=old.transmissions+1&&(old.retained_note===null||old.retained_note===a.retained_note),'EXPERIENCE_ORIGINAL_CHANGED');if(old.summary){check(a.summary!==null,'EXPERIENCE_ORIGINAL_CHANGED');for(const key of ['record_ref','decision_ref','candidate_ref','review_ref','result_sha256'] as const)if(old.summary[key]!==null)check(same(old.summary[key],a.summary[key]),'EXPERIENCE_ORIGINAL_CHANGED');}v.attempts[i]=copy(a);}v.revision++;return this.write(v);}
 releaseRecordedDraft(witness:string){return this.serial(async()=>{const v=this.current(),a=v.attempts.find(row=>row.witness_sha256===witness);check(a&&a.draft_snapshot&&a.summary&&!experienceUnresolved(a));if(same(v.draft,a.draft_snapshot)){v.draft=null;v.revision++;return this.write(v);}return v;});}
 restoreRecordedDraft(witness:string){const a=this.local.attempts.find(row=>row.witness_sha256===witness);check(a?.draft_snapshot);return this.select(a.draft_snapshot);}
 async record(a:ExperienceAttempt,result:ExperienceObservation){const summary=summaryOf(result,a.packet);const old=this.value().attempts.find(b=>b.witness_sha256===a.witness_sha256);check(old);for(const key of ['record_ref','decision_ref','candidate_ref','review_ref','result_sha256'] as const)if(old.summary?.[key]!==null&&old.summary?.[key]!==undefined&&summary[key]===null)Object.assign(summary,{[key]:copy(old.summary[key])});validSummary(summary,a.packet);return this.save({...old,phase:'OBSERVED',last_lookup:'FOUND',summary});}
 resolve(choice:'LOCAL'|'SAVED'){return this.serial(async()=>{
  const raw=this.storage.getItem(this.key),saved=validateExperienceLocal(raw,this.owner),local=this.value();await witnesses(saved);await witnesses(local);const next=copy(saved),selected=choice==='LOCAL'?local.draft:saved.draft;next.draft=copy(selected);
  const keep=(d:ExperienceDraft|null,id?:string)=>{if(!d||same(d,selected)||next.copies.some(c=>same(c.draft,d)))return;check(next.copies.length<4,'EXPERIENCE_DRAFT_COPY_CAPACITY');next.copies.push({id:id&&!next.copies.some(c=>c.id===id)?id:this.newId(),draft:copy(d)});};next.copies=next.copies.filter(c=>!same(c.draft,selected));for(const c of local.copies)keep(c.draft,c.id);keep(choice==='LOCAL'?saved.draft:local.draft);
  for(const a of local.attempts){const i=next.attempts.findIndex(b=>b.witness_sha256===a.witness_sha256);if(i<0){next.attempts.push(copy(a));continue;}const b=next.attempts[i];check(same(a.packet,b.packet)&&same(a.draft_snapshot,b.draft_snapshot)&&(a.retained_note===null||b.retained_note===null||a.retained_note===b.retained_note),'EXPERIENCE_ORIGINAL_CHANGED');if(a.summary&&b.summary){for(const key of ['record_ref','decision_ref','candidate_ref','review_ref','result_sha256'] as const)if(a.summary[key]!==null&&b.summary[key]!==null)check(same(a.summary[key],b.summary[key]),'EXPERIENCE_ORIGINAL_CHANGED');}
   const best=a.transmissions>b.transmissions?a:a.transmissions<b.transmissions?b:a.summary?a:b;let merged=best.summary?copy(best.summary):copy(a.summary??b.summary);
   if(a.summary&&b.summary&&!same(a.summary,b.summary)){
    merged={...copy(a.summary),status:'NEEDS_FRESH_OBSERVATION',native_status:'NEEDS_FRESH_OBSERVATION',source_availability:'NEEDS_FRESH_OBSERVATION',qualified:false,
     content_withheld:a.summary.content_withheld||b.summary.content_withheld,control_effective_state:a.summary.control_effective_state==='PAUSED'||b.summary.control_effective_state==='PAUSED'?'PAUSED':null,
     local_finalization_needed:a.summary.local_finalization_needed||b.summary.local_finalization_needed||null,configuration_finalization_needed:a.summary.configuration_finalization_needed||b.summary.configuration_finalization_needed||null};
    for(const key of ['record_ref','decision_ref','candidate_ref','review_ref','result_sha256'] as const)if(merged[key]===null)Object.assign(merged,{[key]:copy(b.summary[key])});
   }
   next.attempts[i]={...copy(best),summary:merged,phase:merged?'OBSERVED':best.phase,last_lookup:merged?(a.last_lookup==='NOT_FOUND'||b.last_lookup==='NOT_FOUND'?'NOT_FOUND':'FOUND'):best.last_lookup,initial_unknown:a.initial_unknown||b.initial_unknown,retained_note:a.retained_note??b.retained_note,transmissions:Math.max(a.transmissions,b.transmissions)};
  }
  next.attempts=next.attempts.map(a=>a.phase==='PENDING'?{...a,phase:'UNKNOWN',initial_unknown:true}:a);next.revision=Math.max(saved.revision,local.revision)+1;return this.write(next,raw);
 });}
}

const journals=new WeakMap<object,Map<string,ExperienceJournal>>();
export async function experienceJournal(storage:StoragePort,s:ExperienceContext,root:string,locks?:Pick<LockManager,'request'>|null){let map=journals.get(storage);if(!map){map=new Map();journals.set(storage,map);}const key=experienceLocalKey(s,root),old=map.get(key);if(old)return old.validate();if(map.size>=MAX_JOURNALS){const free=[...map].find(([,j])=>!j.unsaved()&&!j.running()&&!j.observed());check(free,'EXPERIENCE_JOURNAL_CAPACITY');map.delete(free[0]);}const j=new ExperienceJournal(s,root,storage,locks);try{await j.validate();}catch(e){dirty.delete(j);throw e;}map.set(key,j);return j;}

function negative(p:PreparedExperience){return p.action==='SOURCE_DECISION'&&['WITHDRAW','FORGET'].includes((p.request as ExperienceRequests['SOURCE_DECISION']).action)||p.action==='PENDING_CONTROL'&&(p.request as ExperienceRequests['PENDING_CONTROL']).mode==='PAUSE'||p.action==='ROLLBACK'&&(p.request as ExperienceRequests['ROLLBACK']).mode==='DISABLE';}
function target(p:PreparedExperience){const r=p.request as unknown as Record<string,unknown>;if(p.action==='PROPOSE')return 'PROPOSE:'+r.root_operation_id;if(p.action==='RECOVER_CONFIGURATION')return 'CONFIGURATION:'+r.operation_id;if(p.action==='ROLLBACK'||p.action==='PENDING_CONTROL')return p.action+':'+r.activation_operation_id;return (['TOOL_VALIDATE','SKILL_VALIDATE'].includes(p.action)?'VALIDATE':p.action)+':'+canonical(r.candidate_ref);}
function meaning(p:PreparedExperience){const {operation_id,generation,...body}=p.request as unknown as Record<string,unknown>;return canonical({action:p.action,body});}

export class ExperienceActions{
 constructor(private journal:ExperienceJournal,private reader=new TaskExperienceApi()){}
 private async bounded<T>(run:(signal:AbortSignal)=>Promise<T>,timeout=25000){const abort=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;try{return await Promise.race([run(abort.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{abort.abort();reject(Error('EXPERIENCE_TRANSPORT_UNKNOWN'));},timeout);})]);}finally{clearTimeout(timer);}}
 private async transmit(a:ExperienceAttempt){let result:ExperienceObservation;try{result=await this.bounded(signal=>this.reader.dispatch(this.journal.owner,a.packet,signal),a.packet.action==='TOOL_VALIDATE'||a.packet.action==='SKILL_VALIDATE'?190000:25000);}catch{await this.journal.serial(async()=>{const old=this.journal.value().attempts.find(b=>b.witness_sha256===a.witness_sha256)!;this.journal.save({...old,phase:old.summary?'OBSERVED':'UNKNOWN',initial_unknown:true});});return null;}
  // Persistence failure must not convert an already received result into a
  // new command or discard it; Journal.write retains it dirty in memory.
  await this.journal.serial(()=>this.journal.record(a,result));return result;
 }
 submit<A extends ExperienceAction>(action:A,input:ExperienceRequests[A]|string,current:()=>boolean=()=>true){const frozen=copy(input),before=this.journal.draft();return this.journal.inFlight(()=>this.submitAction(action,frozen,before,current));}
 private async submitAction<A extends ExperienceAction>(action:A,input:ExperienceRequests[A]|string,before:ExperienceDraft|null,current:()=>boolean){
  await this.journal.flush();check(same(this.journal.current().draft,before),'EXPERIENCE_DRAFT_CHANGED');
  const packet=await this.bounded(signal=>this.reader.prepare(this.journal.owner,action,copy(input),signal));
  const witness=await hash(packet),a:ExperienceAttempt={packet,witness_sha256:witness,phase:'PREPARED',initial_unknown:false,last_lookup:'NOT_CHECKED',summary:null,retained_note:null,transmissions:0,draft_snapshot:copy(before)};
  await this.journal.serial(async()=>{await this.journal.validate();check(current(),'EXPERIENCE_SCOPE_CHANGED');const v=this.journal.current();check(same(v.draft,before),'EXPERIENCE_DRAFT_CHANGED');ownedPacket(packet,this.journal.owner);check(!v.attempts.some(b=>b.witness_sha256===witness),'EXPERIENCE_ORIGINAL_ONLY_INSPECT');
   for(const b of v.attempts){check(operationIdentity(b.packet)!==operationIdentity(packet),'EXPERIENCE_ORIGINAL_CHANGED');if(!negative(packet)&&experienceUnresolved(b)&&target(b.packet)===target(packet))check(b.retained_note!==null&&meaning(b.packet)!==meaning(packet),'EXPERIENCE_PENDING_ORIGINAL');}
   if(!negative(packet)){const next={...v,attempts:[...v.attempts,a]};check(v.attempts.length<MAX_ATTEMPTS-NEGATIVE_SLOTS&&bytes(next)+next.attempts.reduce((sum,b)=>sum+4096-bytes(b.summary),0)+1024<=MAX_BYTES-NEGATIVE_BYTES,'EXPERIENCE_NEGATIVE_CAPACITY_RESERVED');}
   this.journal.save(a);
  });return this.startPrepared(witness,current);
 }
 private async startPrepared(witness:string,current:()=>boolean=()=>true){let attempt:ExperienceAttempt|undefined;await this.journal.serial(async()=>{await this.journal.validate();check(current(),'EXPERIENCE_SCOPE_CHANGED');const a=this.journal.current().attempts.find(b=>b.witness_sha256===witness);check(a&&a.phase==='PREPARED'&&a.transmissions===0&&!a.initial_unknown,'EXPERIENCE_ORIGINAL_ONLY_INSPECT');attempt={...a,phase:'PENDING',transmissions:1};this.journal.save(attempt);});if(!current()){await this.journal.serial(async()=>this.journal.save({...attempt!,phase:'UNKNOWN',initial_unknown:true}));return null;}return this.transmit(attempt!);}
 dispatchPrepared(witness:string,confirmed:boolean){return this.journal.inFlight(async()=>{check(confirmed,'EXPERIENCE_CONFIRM_ORIGINAL_PREPARATION');await this.journal.flush();const a=this.journal.current().attempts.find(b=>b.witness_sha256===witness);check(a&&a.phase==='PREPARED'&&a.transmissions===0&&!a.initial_unknown,'EXPERIENCE_ORIGINAL_ONLY_INSPECT');const fresh=await this.bounded(signal=>this.reader.prepare(this.journal.owner,a.packet.action,a.packet.request_json??copy(a.packet.request),signal));const compare={...fresh};if(a.packet.request_json===undefined)delete compare.request_json;check(same(compare,a.packet),'EXPERIENCE_PREPARATION_CHANGED');return this.startPrepared(witness);});}
 inspect(witness:string){return this.journal.inFlight(()=>this.inspectAction(witness));}
 private async inspectAction(witness:string){await this.journal.flush();let a:ExperienceAttempt;await this.journal.serial(async()=>{await this.journal.validate();a=this.journal.current().attempts.find(b=>b.witness_sha256===witness)!;check(a);});let result:ExperienceObservation;
  try{result=await this.bounded(signal=>this.reader.lookup(this.journal.owner,a!.packet,signal));}catch(e){if((e as {status?:number})?.status===404){await this.journal.serial(async()=>{const old=this.journal.value().attempts.find(b=>b.witness_sha256===witness)!;this.journal.save(old.phase==='PREPARED'?{...old,last_lookup:'NOT_FOUND'}:{...old,last_lookup:'NOT_FOUND',initial_unknown:old.initial_unknown||old.phase!=='OBSERVED',phase:old.summary?'OBSERVED':'UNKNOWN'});});return null;}throw e;}
  await this.journal.serial(()=>this.journal.record(a!,result));return result;
 }
 retain(witness:string,note:string){return this.journal.inFlight(async()=>{check(text(note,2000)&&note.trim().length>0);await this.inspect(witness);return this.journal.serial(async()=>{const old=this.journal.current().attempts.find(a=>a.witness_sha256===witness);check(old);return this.journal.save({...old,retained_note:old.retained_note??note});});});}
 completeKnownMetadata(witness:string,confirmed:boolean){return this.journal.inFlight(()=>this.completeMetadataAction(witness,confirmed));}
 private async completeMetadataAction(witness:string,confirmed:boolean){
  check(confirmed,'EXPERIENCE_CONFIRM_METADATA_RECOVERY');const result=await this.inspect(witness);check(result,'EXPERIENCE_METADATA_NOT_PROVEN');let attempt:ExperienceAttempt|undefined;
  await this.journal.serial(async()=>{const old=this.journal.current().attempts.find(a=>a.witness_sha256===witness);check(old&&old.summary&&old.transmissions<9&&experienceUnresolved(old));const p=old.packet,s=old.summary;
   if(p.action==='PENDING_CONTROL'){const r=p.request as ExperienceRequests['PENDING_CONTROL'];check(r.mode==='COMPLETE_PAUSED_METADATA'&&s.control_record_kind==='COMPLETION_PACKET_RESERVED_ONLY'&&s.control_effective_state==='PAUSED'&&s.control_revision===r.expected_control_revision,'EXPERIENCE_METADATA_NOT_PROVEN');}
   else if(p.action==='RECOVER_CONFIGURATION'){const r=p.request as ExperienceRequests['RECOVER_CONFIGURATION'];check(r.recovery_confirmed&&r.mode!=='INSPECT'&&s.local_finalization_needed===true&&(r.mode==='COMPLETE_EXACT_SAVED_METADATA'?['SAVED_POINTER_UNCONFIRMED','APPLIED_CURRENT','SAVED_SUPERSEDED']:['APPLIED_CURRENT','SAVED_SUPERSEDED']).includes(s.native_status??''),'EXPERIENCE_METADATA_NOT_PROVEN');}
   else check(false,'EXPERIENCE_METADATA_NOT_PROVEN');
   attempt={...old,transmissions:old.transmissions+1};this.journal.save(attempt);
  });return this.transmit(attempt!);
 }
}
