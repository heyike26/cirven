import {configurationCanonical as canonical} from './conversation-configuration';
import {nativeReference,sameNativeReference,type NativeRef} from './native-collaboration';
import {type ReworkRecord} from './native-rework';
import {NativeReworkConflictsApi,conflictNativeRequestHash,conflictQuery,conflictRequest,conflictTextHash,type ConflictQuery,type ConflictRequest,type ConflictReview} from './native-rework-conflicts';

type StoragePort=Pick<Storage,'getItem'|'setItem'>;
type Owner={project_id:string;feedback_ref:NativeRef;parent_plan_ref:NativeRef};
export type ConflictDraft={query:ConflictQuery|null;review_sha256:string|null;replacement:string|null;rationale:string;confirmed:boolean};
export type ConflictAttempt={request:ConflictRequest;checksum:string;phase:'PENDING'|'UNKNOWN'|'RECORDED';initial_unknown:boolean;known_request:boolean;last_lookup:'NOT_CHECKED'|'NOT_FOUND'|'INCOMPLETE'|'RECORDED';receipt:{record_ref:NativeRef;request_sha256:string}|null;retained:boolean};
export type ConflictLocal={version:1;owner:Owner;revision:number;draft:ConflictDraft;copies:{id:string;draft:ConflictDraft}[];attempts:ConflictAttempt[]};
const clone=<T,>(v:T):T=>structuredClone(v),same=(a:unknown,b:unknown)=>canonical(a)===canonical(b),sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),id=(v:unknown)=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v);
const count=(v:unknown)=>Number.isSafeInteger(v)&&Number(v)>=0;
function check(v:unknown,code='NATIVE_CONFLICT_LOCAL_INVALID'):asserts v{if(!v)throw Error(code);}
function text(v:unknown,max:number):v is string{if(typeof v!=='string'||v.includes('\0'))return false;const b=new TextEncoder().encode(v);return b.length<=max&&new TextDecoder('utf-8',{fatal:true}).decode(b)===v;}
function exact(v:unknown,keys:string[]){return !!v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&Object.keys(v).every(k=>keys.includes(k));}
export const emptyConflictDraft=():ConflictDraft=>({query:null,review_sha256:null,replacement:null,rationale:'',confirmed:false});
function owner(p:ReworkRecord):Owner{check(p.kind==='PLAN'&&nativeReference(p.record_ref)&&nativeReference(p.payload.native_feedback_ref));return {project_id:p.payload.project_id,feedback_ref:clone(p.payload.native_feedback_ref),parent_plan_ref:clone(p.record_ref)};}
function owned(q:ConflictQuery,o:Owner){return !!q&&conflictQuery({plan_ref:q.plan_ref,current_source:q.current_source,current_locator:q.current_locator})&&q.current_source.project_id===o.project_id&&sameNativeReference(q.plan_ref,o.parent_plan_ref);}
function draft(d:ConflictDraft,o:Owner){return exact(d,['query','review_sha256','replacement','rationale','confirmed'])&&(d.query===null||owned(d.query,o))&&(d.review_sha256===null||sha(d.review_sha256))&&(d.replacement===null||text(d.replacement,4194304))&&text(d.rationale,16000)&&Array.from(d.rationale).length<=4000&&typeof d.confirmed==='boolean'&&(!d.confirmed||d.query!==null&&d.review_sha256!==null&&d.replacement!==null)&&(!d.review_sha256||d.query!==null);}
function attempt(a:ConflictAttempt,o:Owner){return exact(a,['request','checksum','phase','initial_unknown','known_request','last_lookup','receipt','retained'])&&conflictRequest(a.request)&&owned(a.request,o)&&sha(a.checksum)&&['PENDING','UNKNOWN','RECORDED'].includes(a.phase)&&typeof a.initial_unknown==='boolean'&&typeof a.known_request==='boolean'&&typeof a.retained==='boolean'&&['NOT_CHECKED','NOT_FOUND','INCOMPLETE','RECORDED'].includes(a.last_lookup)&&(a.receipt===null?a.phase!=='RECORDED':exact(a.receipt,['record_ref','request_sha256'])&&a.phase==='RECORDED'&&a.known_request&&a.last_lookup==='RECORDED'&&nativeReference(a.receipt.record_ref)&&a.receipt.request_sha256===a.checksum);}
export function conflictLocalKey(p:ReworkRecord){return 'cirven-native-conflict-v1:'+canonical(owner(p));}
export function validateConflictLocal(raw:string|null,o:Owner):ConflictLocal{
  if(raw===null)return {version:1,owner:clone(o),revision:0,draft:emptyConflictDraft(),copies:[],attempts:[]};
  check(new TextEncoder().encode(raw).length<=4194304,'NATIVE_CONFLICT_LOCAL_CAPACITY');const v=JSON.parse(raw) as ConflictLocal;
  check(exact(v,['version','owner','revision','draft','copies','attempts'])&&v.version===1&&same(v.owner,o)&&count(v.revision)&&draft(v.draft,o)&&Array.isArray(v.copies)&&v.copies.length<=2&&v.copies.every(r=>exact(r,['id','draft'])&&id(r.id)&&draft(r.draft,o))&&new Set(v.copies.map(r=>r.id)).size===v.copies.length&&Array.isArray(v.attempts)&&v.attempts.length<=8&&v.attempts.every(a=>attempt(a,o))&&new Set(v.attempts.map(a=>a.checksum)).size===v.attempts.length&&new Set(v.attempts.map(a=>a.request.operation_id)).size===v.attempts.length);
  return v;
}
const dirty=new Set<ConflictJournal>(),queued=new Set<ConflictJournal>();
export async function flushNativeConflictDrafts(){try{for(;;){const all=[...new Set([...dirty,...queued])];if(!all.length)return;await Promise.all(all.map(j=>j.flush()));}}catch{throw Error('NATIVE_CONFLICT_DRAFT_UNSAVED');}}
export class ConflictJournal{
  readonly owner:Owner;readonly key:string;private local:ConflictLocal;private raw:string|null;private changed=false;private waiting=0;private tail:Promise<unknown>=Promise.resolve();private listeners=new Set<()=>void>();
  constructor(p:ReworkRecord,private storage:StoragePort,private locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,private newId=()=>crypto.randomUUID()){
    this.owner=owner(p);this.key=conflictLocalKey(p);this.raw=storage.getItem(this.key);this.local=validateConflictLocal(this.raw,this.owner);
    if(this.local.attempts.some(a=>a.phase==='PENDING')){this.local.attempts=this.local.attempts.map(a=>a.phase==='PENDING'?{...a,phase:'UNKNOWN',initial_unknown:true}:a);this.local.revision++;this.changed=true;dirty.add(this);}
  }
  value(){return clone(this.local);}unsaved(){return this.changed;}running(){return this.waiting>0;}observed(){return this.listeners.size>0;}
  subscribe(fn:()=>void){this.listeners.add(fn);return ()=>this.listeners.delete(fn);}private emit(){for(const fn of this.listeners)fn();}
  async validate(){for(const a of this.local.attempts)check(await conflictNativeRequestHash(this.owner.project_id,a.request)===a.checksum);return this;}
  edit(d:ConflictDraft){check(draft(d,this.owner));const v=this.value();v.draft=clone(d);v.revision++;this.local=v;this.changed=true;dirty.add(this);this.emit();}
  private write(v:ConflictLocal,basis=this.raw){
    const raw=JSON.stringify(v);check(new TextEncoder().encode(raw).length+v.attempts.filter(a=>a.receipt===null).length*2048<=4194304,'NATIVE_CONFLICT_LOCAL_CAPACITY');
    this.local=clone(v);this.changed=true;dirty.add(this);this.emit();check(this.storage.getItem(this.key)===basis,'NATIVE_CONFLICT_DRAFT_CONFLICT');
    this.storage.setItem(this.key,raw);check(this.storage.getItem(this.key)===raw,'NATIVE_CONFLICT_DRAFT_CONFLICT');this.raw=raw;this.changed=false;dirty.delete(this);this.emit();return this.value();
  }
  current(){check(!this.changed,'NATIVE_CONFLICT_DRAFT_UNSAVED');check(this.storage.getItem(this.key)===this.raw,'NATIVE_CONFLICT_DRAFT_CONFLICT');return this.value();}
  serial<T>(run:()=>Promise<T>){
    this.waiting++;queued.add(this);
    const next=this.tail.catch(()=>{}).then(async()=>{check(this.locks,'NATIVE_CONFLICT_LOCK_UNAVAILABLE');const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);try{return await this.locks.request(this.key,{mode:'exclusive',signal:abort.signal},async()=>{clearTimeout(timer);return run();});}finally{clearTimeout(timer);}}).finally(()=>{this.waiting--;if(!this.waiting)queued.delete(this);this.emit();});
    this.tail=next;return next;
  }
  flush(){return this.serial(async()=>this.changed?this.write(this.local):this.value());}
  save(a:ConflictAttempt){check(attempt(a,this.owner));const v=this.value(),i=v.attempts.findIndex(r=>r.checksum===a.checksum);if(i<0){check(v.attempts.length<8,'NATIVE_CONFLICT_HISTORY_CAPACITY');check(!v.attempts.some(r=>r.request.operation_id===a.request.operation_id));v.attempts.push(clone(a));}else{check(same(v.attempts[i].request,a.request));v.attempts[i]=clone(a);}v.revision++;return this.write(v);}
  accept(a:ConflictAttempt,r:ReworkRecord){const current=this.local.attempts.find(row=>row.checksum===a.checksum);check(current);return this.save({...clone(current),phase:'RECORDED',known_request:true,last_lookup:'RECORDED',receipt:{record_ref:clone(r.record_ref),request_sha256:a.checksum}});}
  retain(sum:string){return this.serial(async()=>{const a=this.current().attempts.find(r=>r.checksum===sum);check(a&&a.phase==='UNKNOWN'&&['NOT_FOUND','INCOMPLETE'].includes(a.last_lookup),'NATIVE_CONFLICT_PENDING');return this.save({...a,retained:true});});}
  copyAction(id:string,remove=false){return this.serial(async()=>{const v=this.current(),row=v.copies.find(r=>r.id===id);check(row);if(remove)v.copies=v.copies.filter(r=>r.id!==id);else{const prior=v.draft;v.draft=clone(row.draft);row.draft=prior;}v.revision++;return this.write(v);});}
  resolve(which:'LOCAL'|'SAVED'){
    return this.serial(async()=>{
      const raw=this.storage.getItem(this.key),saved=validateConflictLocal(raw,this.owner),local=this.value();
      for(const a of saved.attempts)check(await conflictNativeRequestHash(this.owner.project_id,a.request)===a.checksum);
      const selected=which==='LOCAL'?local.draft:saved.draft,result={...clone(saved),draft:clone(selected)};
      const keep=(d:ConflictDraft,id?:string)=>{if(same(d,selected)||result.copies.some(r=>same(r.draft,d)))return;check(result.copies.length<2,'NATIVE_CONFLICT_COPY_CAPACITY');result.copies.push({id:id&&!result.copies.some(r=>r.id===id)?id:this.newId(),draft:clone(d)});};
      for(const r of local.copies)keep(r.draft,r.id);keep(which==='LOCAL'?saved.draft:local.draft);
      for(const a of local.attempts){const i=result.attempts.findIndex(r=>r.checksum===a.checksum);if(i<0){check(result.attempts.length<8,'NATIVE_CONFLICT_HISTORY_CAPACITY');check(!result.attempts.some(r=>r.request.operation_id===a.request.operation_id));result.attempts.push(clone(a));}else{const b=result.attempts[i];check(same(a.request,b.request));if(a.receipt&&b.receipt)check(same(a.receipt,b.receipt));const known=a.receipt?a:b.receipt?b:a;result.attempts[i]={...clone(known),phase:known.phase==='PENDING'?'UNKNOWN':known.phase,initial_unknown:a.initial_unknown||b.initial_unknown||known.phase==='PENDING',known_request:a.known_request||b.known_request,retained:a.retained||b.retained};}}
      result.revision=Math.max(local.revision,saved.revision)+1;return this.write(result,raw);
    });
  }
}
const journals=new WeakMap<object,Map<string,ConflictJournal>>();
export async function conflictJournal(storage:StoragePort,p:ReworkRecord){let map=journals.get(storage);if(!map){map=new Map();journals.set(storage,map);}const key=conflictLocalKey(p),old=map.get(key);if(old)return old.validate();if(map.size>=8){const free=[...map].find(([,j])=>!j.unsaved()&&!j.running()&&!j.observed());check(free,'NATIVE_CONFLICT_LOCAL_CAPACITY');map.delete(free[0]);}const j=new ConflictJournal(p,storage);map.set(key,j);await j.validate();return j;}
export class ConflictActions{
  constructor(private journal:ConflictJournal,private reader:Pick<NativeReworkConflictsApi,'inspect'|'resolve'|'operation'>=new NativeReworkConflictsApi(),private newId=()=>crypto.randomUUID()){}
  private async bounded<T>(run:(signal:AbortSignal)=>Promise<T>){const abort=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;try{return await Promise.race([run(abort.signal),new Promise<never>((_,reject)=>{timer=setTimeout(()=>{abort.abort();reject(Error('NATIVE_CONFLICT_TRANSPORT_UNKNOWN'));},25000);})]);}finally{clearTimeout(timer);}}
  private async transmit(p:ReworkRecord,a:ConflictAttempt){let record:ReworkRecord;try{record=await this.bounded(signal=>this.reader.resolve(p,a.request,signal));}catch{const current=this.journal.value().attempts.find(r=>r.checksum===a.checksum)!;this.journal.save({...current,phase:current.receipt?'RECORDED':'UNKNOWN',initial_unknown:true});return null;}this.journal.accept(a,record);return record;}
  submit(p:ReworkRecord,review:ConflictReview){return this.journal.serial(async()=>{
    await this.journal.validate();const v=this.journal.current(),d=v.draft;
    check(d.query&&d.review_sha256===review.review_sha256&&d.confirmed&&d.replacement!==null&&!v.attempts.some(a=>a.phase!=='RECORDED'&&!a.retained),'NATIVE_CONFLICT_REVIEW_REQUIRED');
    const fresh=await this.bounded(signal=>this.reader.inspect(p,d.query!,signal));
    check(fresh.review_sha256===review.review_sha256&&fresh.selection_status==='EXACT_CURRENT_OBJECT'&&same(this.journal.current().draft,d),'NATIVE_CONFLICT_REVIEW_REQUIRED');
    const request:ConflictRequest={...clone(d.query),operation_id:this.newId(),review_sha256:review.review_sha256,resolved_value_text:d.replacement,rationale:d.rationale,current_object_confirmed:true};check(conflictRequest(request));
    const a:ConflictAttempt={request,checksum:await conflictNativeRequestHash(p.payload.project_id,request),phase:'PENDING',initial_unknown:false,known_request:false,last_lookup:'NOT_CHECKED',receipt:null,retained:false};this.journal.save(a);return this.transmit(p,a);
  });}
  private async lookup(p:ReworkRecord,a:ConflictAttempt){
    let result:Awaited<ReturnType<NativeReworkConflictsApi['operation']>>;
    try{result=await this.bounded(signal=>this.reader.operation(p,a.request,signal));}catch(error){if((error as {status?:number})?.status!==404)throw error;const current=this.journal.value().attempts.find(r=>r.checksum===a.checksum)!;if(!current.receipt)this.journal.save({...current,phase:'UNKNOWN',initial_unknown:true,last_lookup:'NOT_FOUND'});return null;}
    if('record_ref' in result){this.journal.accept(a,result);return result;}
    const current=this.journal.value().attempts.find(r=>r.checksum===a.checksum)!;check(!current.receipt,'NATIVE_CONFLICT_ORIGINAL_RECORD_UNAVAILABLE');this.journal.save({...current,phase:'UNKNOWN',known_request:true,last_lookup:'INCOMPLETE'});return null;
  }
  inspect(p:ReworkRecord,sum:string){return this.journal.serial(async()=>{await this.journal.validate();const a=this.journal.value().attempts.find(r=>r.checksum===sum);check(a);return this.lookup(p,a);});}
  continue(p:ReworkRecord,sum:string){return this.journal.serial(async()=>{
    await this.journal.validate();let a=this.journal.current().attempts.find(r=>r.checksum===sum);check(a);const result=await this.lookup(p,a);if(result)return result;
    a=this.journal.value().attempts.find(r=>r.checksum===sum)!;check(!a.receipt&&!(a.known_request&&a.last_lookup==='NOT_FOUND'),'NATIVE_CONFLICT_ORIGINAL_RECORD_UNAVAILABLE');
    const {operation_id,review_sha256,resolved_value_text,rationale,current_object_confirmed,...query}=a.request,fresh=await this.bounded(signal=>this.reader.inspect(p,query,signal));
    check(fresh.review_sha256===review_sha256&&fresh.selection_status==='EXACT_CURRENT_OBJECT','NATIVE_CONFLICT_REVIEW_REQUIRED');this.journal.save({...a,phase:'PENDING'});return this.transmit(p,a);
  });}
}
/** Textarea selection uses UTF-16 and normalizes CRLF; server ranges use saved
 * Unicode code points. Only exact normalized text and complete boundaries map. */
export async function conflictTextSelection(saved:string,display:string,start:number,end:number){
  check(text(saved,1048576)&&display===saved.replace(/\r\n?/g,'\n')&&count(start)&&count(end)&&end>start&&end<=display.length,'NATIVE_CONFLICT_SELECTION_CHANGED');
  const chars=Array.from(saved),boundaries=new Map<number,number>([[0,0]]);let offset=0;
  for(let i=0;i<chars.length;i++){let c=chars[i];if(c==='\r'){if(chars[i+1]==='\n')i++;c='\n';}offset+=c.length;boundaries.set(offset,i+1);}
  const a=boundaries.get(start),b=boundaries.get(end);check(a!==undefined&&b!==undefined&&b>a,'NATIVE_CONFLICT_SELECTION_CHANGED');
  return {kind:'TEXT_RANGE' as const,char_start:a,char_end:b,quote_sha256:await conflictTextHash(chars.slice(a,b).join(''))};
}
