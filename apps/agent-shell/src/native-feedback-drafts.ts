/** Private unsent quotes only. Existing feedback requests/receipts remain authoritative. */
import {configurationCanonical as canonical} from './conversation-configuration';
import {exactFileSource} from './companion-files';
import {deliverySource} from './planning-delivery';
import {nativeReference,type NativeRef} from './native-collaboration';
import type {SourceRef} from './contracts';
import type {FeedbackRequest,FeedbackSelection} from './native-feedback-api';

type StoragePort=Pick<Storage,'getItem'|'setItem'>;
export type FeedbackDraftOwner={scope_key:string;source:SourceRef};
export type FeedbackQuoteDraft={quote:string;selection:Extract<FeedbackSelection,{kind:'TEXT_RANGE'}>|null;expanded:boolean;origin:'MANUAL'|'CAPTURE'};
export type FeedbackQuoteAck={operation_id:string;input_sha256:string;feedback_ref:NativeRef};
export type FeedbackQuoteRow={owner:FeedbackDraftOwner;revision:number;draft:FeedbackQuoteDraft;copies:{id:string;draft:FeedbackQuoteDraft}[];last_saved:FeedbackQuoteAck|null};
type Local={version:1;revision:number;rows:FeedbackQuoteRow[]};
const KEY='eeas-native-feedback-drafts-v1',MAX_BYTES=1048576,MAX_ROWS=128;
const clone=<T,>(value:T):T=>structuredClone(value),same=(a:unknown,b:unknown)=>canonical(a)===canonical(b),id=(v:unknown)=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v),sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
function check(v:unknown,code='NATIVE_FEEDBACK_DRAFT_INVALID'):asserts v{if(!v)throw Error(code);}
const exact=(v:unknown,keys:string[])=>!!v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&Object.keys(v).every(k=>keys.includes(k));
const utf8=(text:string)=>new TextDecoder('utf-8',{fatal:true}).decode(new TextEncoder().encode(text))===text;
export async function feedbackQuoteHash(text:string){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))].map(n=>n.toString(16).padStart(2,'0')).join('');}
function source(value:unknown):value is SourceRef{const s=value as SourceRef;return deliverySource(s)&&Number.isSafeInteger(s.revision)&&s.revision>0&&id(s.project_id)&&id(s.workspace_id)&&Array.from(s.path).length<=1024&&utf8(s.path)&&!/[\\:\u0000-\u001f\u007f]/.test(s.path)&&s.path.split('/').every(p=>!!p&&p!=='.'&&p!=='..'&&!p.startsWith('.eeas-write-'));}
export function feedbackDraftOwner(scopeKey:string,file:SourceRef):FeedbackDraftOwner{
 check(typeof scopeKey==='string'&&!!scopeKey&&scopeKey.length<=8192&&utf8(scopeKey)&&source(file));
 let stable=scopeKey;try{const scope=JSON.parse(scopeKey);if(scope&&typeof scope==='object'&&!Array.isArray(scope)&&typeof scope.conversationId==='string'&&scope.conversationId&&scope.conversationId.length<=200)stable=canonical({conversationId:scope.conversationId});}catch{/* Older callers use an opaque, stable discussion key. */}
 const {kind,project_id,workspace_id,path,revision,sha256,binding_revision,binding_sha256}=file;return {scope_key:stable,source:{kind,project_id,workspace_id,path,revision,sha256,binding_revision,binding_sha256}};
}
export function feedbackDraftBasis(scopeKey:string,file:SourceRef){return JSON.stringify([scopeKey,file.kind,file.project_id,file.workspace_id,file.path,file.binding_revision,file.binding_sha256,file.revision,file.sha256]);}
export function pendingFeedbackOwner(entry:{scope:string;request:FeedbackRequest},owner:FeedbackDraftOwner){try{const parts=JSON.parse(entry.scope);return Array.isArray(parts)&&typeof parts[0]==='string'&&entry.scope===feedbackDraftBasis(parts[0],entry.request.file_source_ref)&&same(feedbackDraftOwner(parts[0],entry.request.file_source_ref),owner);}catch{return false;}}
export const emptyFeedbackQuote=():FeedbackQuoteDraft=>({quote:'',selection:null,expanded:false,origin:'MANUAL'});
function draft(v:FeedbackQuoteDraft){const r=v?.selection;return exact(v,['quote','selection','expanded','origin'])&&typeof v.quote==='string'&&Array.from(v.quote).length<=8000&&typeof v.expanded==='boolean'&&['MANUAL','CAPTURE'].includes(v.origin)&&(r===null||exact(r,['kind','char_start','char_end','quote_sha256'])&&r.kind==='TEXT_RANGE'&&Number.isSafeInteger(r.char_start)&&Number.isSafeInteger(r.char_end)&&r.char_start>=0&&r.char_end-r.char_start===Array.from(v.quote).length&&!!v.quote&&sha(r.quote_sha256)&&utf8(v.quote));}
function validOwner(o:FeedbackDraftOwner){return exact(o,['scope_key','source'])&&typeof o.scope_key==='string'&&!!o.scope_key&&o.scope_key.length<=8192&&utf8(o.scope_key)&&source(o.source)&&same(feedbackDraftOwner(o.scope_key,o.source),o);}
function row(v:FeedbackQuoteRow){return exact(v,['owner','revision','draft','copies','last_saved'])&&validOwner(v.owner)&&Number.isSafeInteger(v.revision)&&v.revision>=0&&draft(v.draft)&&Array.isArray(v.copies)&&v.copies.length<=2&&v.copies.every(c=>exact(c,['id','draft'])&&id(c.id)&&draft(c.draft))&&new Set(v.copies.map(c=>c.id)).size===v.copies.length&&(v.last_saved===null||exact(v.last_saved,['operation_id','input_sha256','feedback_ref'])&&id(v.last_saved.operation_id)&&sha(v.last_saved.input_sha256)&&nativeReference(v.last_saved.feedback_ref));}
function decode(raw:string|null):Local{if(raw===null)return {version:1,revision:0,rows:[]};check(new TextEncoder().encode(raw).length<=MAX_BYTES,'NATIVE_FEEDBACK_DRAFT_CAPACITY');const v=JSON.parse(raw) as Local;check(exact(v,['version','revision','rows'])&&v.version===1&&Number.isSafeInteger(v.revision)&&v.revision>=0&&Array.isArray(v.rows)&&v.rows.length<=MAX_ROWS&&v.rows.every(row)&&new Set(v.rows.map(r=>canonical(r.owner))).size===v.rows.length);return v;}
async function validate(v:FeedbackQuoteRow){check(row(v));for(const d of [v.draft,...v.copies.map(c=>c.draft)])if(d.selection)check(d.selection.quote_sha256===await feedbackQuoteHash(d.quote),'NATIVE_FEEDBACK_DRAFT_SELECTION_CHANGED');}
export function feedbackQuoteReady(text:string){return !!text&&Array.from(text).length<=4000&&utf8(text)&&!text.includes('\0');}
export function feedbackQuoteSelection(d:FeedbackQuoteDraft):FeedbackSelection{return d.selection?clone(d.selection):{kind:'EXACT_QUOTE',quote:d.quote};}
export async function feedbackInputHash(request:Omit<FeedbackRequest,'operation_id'>|FeedbackRequest){const {file_source_ref,comment,mode,selection,display_quote}=request;return feedbackQuoteHash(canonical({file_source_ref,comment,mode,selection,...display_quote===undefined?{}:{display_quote}}));}
const dirty=new Set<FeedbackQuoteLease>(),running=new Set<FeedbackQuoteLease>();
export async function flushNativeFeedbackDrafts(){try{for(;;){const all=[...new Set([...dirty,...running])];if(!all.length)return;await Promise.all(all.map(j=>j.settle()));}}catch{throw Error('NATIVE_FEEDBACK_DRAFT_UNSAVED');}}
export class FeedbackQuoteLease{
 readonly owner:FeedbackDraftOwner;private local:FeedbackQuoteRow;private basis:FeedbackQuoteRow|null;private changed=false;private tail:Promise<unknown>=Promise.resolve();private waiting=0;private listeners=new Set<()=>void>();private activities=new Set<Promise<unknown>>();
 constructor(readonly storage:StoragePort,scopeKey:string,file:SourceRef,readonly locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,private newId=()=>crypto.randomUUID()){
  this.owner=feedbackDraftOwner(scopeKey,file);const saved=decode(this.read()).rows.find(r=>same(r.owner,this.owner));this.basis=saved?clone(saved):null;this.local=saved?clone(saved):{owner:clone(this.owner),revision:0,draft:emptyFeedbackQuote(),copies:[],last_saved:null};
 }
 private read(){try{return this.storage.getItem(KEY);}catch{throw Error('NATIVE_FEEDBACK_DRAFT_UNAVAILABLE');}}
 value(){return clone(this.local);}unsaved(){return this.changed;}active(){return this.waiting>0||this.activities.size>0;}observed(){return this.listeners.size>0;}subscribe(fn:()=>void){this.listeners.add(fn);return ()=>this.listeners.delete(fn);}private emit(){for(const fn of this.listeners)fn();}
 async validate(){await validate(this.local);return this;}
 edit(value:FeedbackQuoteDraft,keepPrior=false){check(draft(value));const v=this.value();if(keepPrior&&v.draft.quote&&!same(v.draft,value)&&!v.copies.some(c=>same(c.draft,v.draft))){check(v.copies.length<2,'NATIVE_FEEDBACK_DRAFT_COPY_CAPACITY');v.copies.push({id:this.newId(),draft:clone(v.draft)});}v.draft=clone(value);v.revision++;this.local=v;this.changed=true;dirty.add(this);this.emit();}
 private async locked<T>(work:()=>Promise<T>){check(this.locks,'NATIVE_FEEDBACK_DRAFT_LOCK_UNAVAILABLE');const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);try{return await this.locks.request(KEY,{mode:'exclusive',signal:abort.signal},async()=>{clearTimeout(timer);return work();});}finally{clearTimeout(timer);}}
 private serial<T>(work:()=>Promise<T>){this.waiting++;running.add(this);const next=this.tail.catch(()=>{}).then(()=>this.locked(work)).finally(()=>{this.waiting--;if(!this.active())running.delete(this);this.emit();});this.tail=next;return next;}
 private async write(selected:FeedbackQuoteRow,expected:FeedbackQuoteRow|null=this.basis){
  await validate(selected);check(this.local.revision===selected.revision,'NATIVE_FEEDBACK_DRAFT_CHANGED');const raw=this.read(),saved=decode(raw),old=saved.rows.find(r=>same(r.owner,this.owner))??null;
  check(same(old,expected),'NATIVE_FEEDBACK_DRAFT_CONFLICT');const rows=saved.rows.filter(r=>!same(r.owner,this.owner)),nonempty=!!selected.draft.quote||!!selected.copies.length||!!selected.last_saved;
  if(nonempty)rows.push(clone(selected));check(rows.length<=MAX_ROWS,'NATIVE_FEEDBACK_DRAFT_CAPACITY');const encoded=JSON.stringify({version:1,revision:saved.revision+1,rows});check(new TextEncoder().encode(encoded).length<=MAX_BYTES,'NATIVE_FEEDBACK_DRAFT_CAPACITY');check(this.read()===raw,'NATIVE_FEEDBACK_DRAFT_CONFLICT');
  try{this.storage.setItem(KEY,encoded);}catch{throw Error('NATIVE_FEEDBACK_DRAFT_UNAVAILABLE');}check(this.read()===encoded,'NATIVE_FEEDBACK_DRAFT_SAVE_UNCONFIRMED');this.basis=nonempty?clone(selected):null;this.changed=false;dirty.delete(this);this.emit();return this.value();
 }
 assertSaved(){check(!this.changed&&same(decode(this.read()).rows.find(r=>same(r.owner,this.owner))??null,this.basis),'NATIVE_FEEDBACK_DRAFT_CONFLICT');}
 flush(){return this.serial(async()=>{if(this.changed)return this.write(this.value());this.assertSaved();return this.value();});}
 async settle(){await Promise.all([...this.activities]);return this.flush();}
 activity<T>(work:()=>Promise<T>){running.add(this);const p=Promise.resolve().then(work).finally(()=>{this.activities.delete(p);if(!this.active())running.delete(this);this.emit();});this.activities.add(p);return p;}
 async remember(request:FeedbackRequest,feedback:NativeRef){check(exactFileSource(request.file_source_ref,this.owner.source)&&nativeReference(feedback));const frozen=clone(request),sum=await feedbackInputHash(frozen),v=this.value();v.last_saved={operation_id:frozen.operation_id,input_sha256:sum,feedback_ref:clone(feedback)};v.revision++;this.local=v;this.changed=true;dirty.add(this);this.emit();}
 clear(){this.edit(emptyFeedbackQuote());this.local.last_saved=null;this.emit();}
 copyAction(id:string,remove=false){return this.serial(async()=>{const v=this.value(),copy=v.copies.find(c=>c.id===id);check(copy);if(remove)v.copies=v.copies.filter(c=>c.id!==id);else{const old=v.draft;v.draft=clone(copy.draft);copy.draft=old;}v.revision++;this.local=v;this.changed=true;dirty.add(this);return this.write(v);});}
 resolve(which:'LOCAL'|'SAVED'){return this.serial(async()=>{
  const expected=decode(this.read()).rows.find(r=>same(r.owner,this.owner))??null,saved=expected??{owner:clone(this.owner),revision:0,draft:emptyFeedbackQuote(),copies:[],last_saved:null};await validate(saved);const local=this.value(),selected=which==='LOCAL'?local.draft:saved.draft,result=clone(saved);result.draft=clone(selected);
  const keep=(d:FeedbackQuoteDraft,id?:string)=>{if(!d.quote||same(d,selected)||result.copies.some(c=>same(c.draft,d)))return;check(result.copies.length<2,'NATIVE_FEEDBACK_DRAFT_COPY_CAPACITY');result.copies.push({id:id&&!result.copies.some(c=>c.id===id)?id:this.newId(),draft:clone(d)});};for(const c of local.copies)keep(c.draft,c.id);keep(which==='LOCAL'?saved.draft:local.draft);if(local.last_saved&&!result.last_saved)result.last_saved=clone(local.last_saved);result.revision=Math.max(local.revision,saved.revision)+1;this.local=result;this.changed=true;dirty.add(this);return this.write(result,expected);
 });}
}
const leases=new WeakMap<object,Map<string,FeedbackQuoteLease>>();
export async function feedbackQuoteLease(storage:StoragePort,scopeKey:string,file:SourceRef){let map=leases.get(storage);if(!map){map=new Map();leases.set(storage,map);}const key=canonical(feedbackDraftOwner(scopeKey,file)),old=map.get(key);if(old)return old.validate();if(map.size>=64){const free=[...map].find(([,j])=>!j.unsaved()&&!j.active()&&!j.observed());check(free,'NATIVE_FEEDBACK_DRAFT_CAPACITY');map.delete(free[0]);}const lease=new FeedbackQuoteLease(storage,scopeKey,file);await lease.validate();map.set(key,lease);return lease;}
export function olderFeedbackQuotes(storage:StoragePort,owner:FeedbackDraftOwner,offset=0){check(Number.isSafeInteger(offset)&&offset>=0);let raw;try{raw=storage.getItem(KEY);}catch{throw Error('NATIVE_FEEDBACK_DRAFT_UNAVAILABLE');}const rows=decode(raw).rows.filter(r=>r.draft.quote&&r.owner.scope_key===owner.scope_key&&r.owner.source.project_id===owner.source.project_id&&r.owner.source.workspace_id===owner.source.workspace_id&&r.owner.source.path===owner.source.path&&!exactFileSource(r.owner.source,owner.source)).reverse(),start=Math.min(offset,Math.floor(Math.max(0,rows.length-1)/8)*8);return {offset:start,total:rows.length,rows:clone(rows.slice(start,start+8)),hasOlder:start+8<rows.length,hasNewer:start>0};}
