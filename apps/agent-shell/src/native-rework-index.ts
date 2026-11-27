/** Bounded private index at the existing key. Unknown/manual rows are never evicted. */
import {nativeReference,sameNativeReference,type NativeRef} from './native-collaboration';
import {configurationCanonical as canonical} from './conversation-configuration';
import type {ReworkOperation} from './native-rework';
export type ReworkPrivatePacket={version:1;json:string;sha256:string};
type StoragePort=Pick<Storage,'getItem'|'setItem'>;
const MAX_BYTES=4194304,MAX_ROWS=128,sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),ident=(v:unknown)=>typeof v==='string'&&v.length>0&&v.length<=100&&/^[A-Za-z0-9_-]+$/.test(v);
function check(value:unknown,code='NATIVE_REWORK_INDEX_INVALID'):asserts value{if(!value)throw Error(code);}
export const reworkIndexKey=(pid:string,ref:NativeRef)=>`eeas-native-rework:${pid}:${ref.id}`;
export async function reworkPacketHash(value:string){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(v=>v.toString(16).padStart(2,'0')).join('');}
function packetShape(packet:ReworkPrivatePacket,operation:ReworkOperation){
 check(packet&&Object.keys(packet).sort().join(',')==='json,sha256,version'&&packet.version===1&&typeof packet.json==='string'&&new TextEncoder().encode(packet.json).length<=2097152&&sha(packet.sha256));
 const body=JSON.parse(packet.json);check(body&&typeof body==='object'&&!Array.isArray(body)&&body.operation_id===operation.operationId);
 const basis=operation.kind==='MAPPING'?body.proposal_ref:operation.kind==='PLAN'?body.mapping_ref:body.plan_ref;check(sameNativeReference(basis,operation.basisRef)&&(!Object.hasOwn(body,'native_feedback_ref')||sameNativeReference(body.native_feedback_ref,operation.feedbackRef)));return body;
}
function validateRow(row:ReworkOperation){check(row&&typeof row==='object'&&!Array.isArray(row)&&ident(row.projectId)&&nativeReference(row.feedbackRef)&&Number.isSafeInteger(row.feedbackRef.revision)&&['MAPPING','PLAN','APPLY'].includes(row.kind)&&ident(row.operationId)&&nativeReference(row.basisRef)&&Number.isSafeInteger(row.basisRef.revision)&&(row.requestSha===undefined||sha(row.requestSha)));if(row.packet!==undefined)packetShape(row.packet,row);}
export function decodeReworkIndex(raw:string|null):ReworkOperation[]{
 if(raw===null)return [];
 check(new TextEncoder().encode(raw).length<=MAX_BYTES,'NATIVE_REWORK_INDEX_CAPACITY');const rows=JSON.parse(raw) as ReworkOperation[];
 check(Array.isArray(rows)&&rows.length<=MAX_ROWS,'NATIVE_REWORK_INDEX_CAPACITY');
 rows.forEach(validateRow);
 return rows;
}
function read(storage:StoragePort,key:string){try{return storage.getItem(key);}catch{throw Error('NATIVE_REWORK_INDEX_UNAVAILABLE');}}
export function readReworkIndex(storage:StoragePort,pid:string,feedback:NativeRef){check(ident(pid)&&nativeReference(feedback));return structuredClone(decodeReworkIndex(read(storage,reworkIndexKey(pid,feedback))).filter(row=>row.projectId===pid&&sameNativeReference(row.feedbackRef,feedback)));}
export async function validateReworkPacket(operation:ReworkOperation){if(operation.packet===undefined)return;packetShape(operation.packet,operation);check(await reworkPacketHash(operation.packet.json)===operation.packet.sha256);}
export async function saveReworkIndex(storage:StoragePort,value:ReworkOperation,locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null){
 // Capture everything before awaiting a hash or lock. Preserve unfamiliar saved
 // metadata and rows belonging to another saved revision of the same feedback.
 const operation=structuredClone(value);validateRow(operation);await validateReworkPacket(operation);check(locks,'NATIVE_REWORK_INDEX_LOCK_UNAVAILABLE');
 const key=reworkIndexKey(operation.projectId,operation.feedbackRef),abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);
 try{return await locks.request(key,{mode:'exclusive',signal:abort.signal},()=>{
  clearTimeout(timer);const before=read(storage,key),rows=decodeReworkIndex(before),old=rows.find(row=>row.operationId===operation.operationId);
  if(old){check(old.projectId===operation.projectId&&sameNativeReference(old.feedbackRef,operation.feedbackRef)&&old.kind===operation.kind&&sameNativeReference(old.basisRef,operation.basisRef)&&(old.requestSha===undefined||operation.requestSha===undefined||old.requestSha===operation.requestSha)&&(!old.packet||!operation.packet||old.packet.sha256===operation.packet.sha256),'NATIVE_REWORK_INDEX_ORIGINAL_ONLY');return {fresh:false,operation:structuredClone(old)};}
  check(rows.length<MAX_ROWS,'NATIVE_REWORK_INDEX_CAPACITY');const encoded=JSON.stringify(operation),end=before?.lastIndexOf(']')??-1,raw=before===null?'['+encoded+']':before.slice(0,end)+(rows.length?',':'')+encoded+before.slice(end);check(new TextEncoder().encode(raw).length<=MAX_BYTES,'NATIVE_REWORK_INDEX_CAPACITY');check(read(storage,key)===before,'NATIVE_REWORK_INDEX_CONFLICT');
  try{storage.setItem(key,raw);}catch{throw Error('NATIVE_REWORK_INDEX_UNAVAILABLE');}check(read(storage,key)===raw,'NATIVE_REWORK_INDEX_SAVE_UNCONFIRMED');return {fresh:true,operation};
 });}finally{clearTimeout(timer);}
}
export function reworkIndexPage(rows:ReworkOperation[],offset=0,limit=8){check(Number.isSafeInteger(offset)&&offset>=0&&Number.isSafeInteger(limit)&&limit>=1&&limit<=16);const newest=[...rows].reverse(),start=Math.min(offset,Math.floor(Math.max(0,newest.length-1)/limit)*limit);return {offset:start,total:newest.length,rows:structuredClone(newest.slice(start,start+limit)),hasOlder:start+limit<newest.length,hasNewer:start>0};}
/** Compare a saved local wire body with the backend's normalized saved input.
 * This is metadata comparison, never a replay body or permission grant. */
export function normalizedReworkBody(kind:ReworkOperation['kind'],body:Record<string,unknown>){
 if(kind==='MAPPING')return {locator:null,task_ids:[],user_confirmed:false,mapping_context_ref:null,discovery_ref:null,requirement_ids:[],...body};
 if(kind==='APPLY')return {allow_rebase:false,...body};
 const value=structuredClone(body);if(Array.isArray(value.contracts))value.contracts=value.contracts.map(c=>({...c,inputs:c.inputs.map((i:Record<string,unknown>)=>({invalidating_kinds:['EXPRESSION','DATA','CONSTRAINT','UNKNOWN'],...i}))}));return value;
}
export function sameReworkInput(a:unknown,b:unknown){return canonical(a)===canonical(b);}
