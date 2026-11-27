import {api,type ApiClient} from './api.ts';
import type {AgentSession} from './agent-sessions';
import type {NativeRef} from './native-collaboration';
import {originalFileScope,type OriginalFileScope} from './original-session-files.ts';

export type CommandSourceSnapshot={state:'UTF8'|'ABSENT';sha256:string|null;bytes:number;line_count:number;blob:string|null;stat:Record<string,string>|null};
export type OriginalCommandObservation={record_ref:NativeRef;operation_id:string;root_operation_id:string;native_message_id:string;message_id:string;part_id:string;call_id:string;source_generation:number;command:string;workdir:string|null;exit_code:number|null;status:'EXITED'|'UNKNOWN_OUTCOME';source_status:'UNCHANGED'|'CHANGED'|'UNAVAILABLE';sources:{path:string;before:CommandSourceSnapshot|null;after:CommandSourceSnapshot|null}[];output:{text:string;sha256:string;bytes:number;truncated:boolean;original_truncated:boolean;original_output_path:string|null}|null;qualification:'ORIGINAL_COMMAND_OBSERVATION_ONLY'};
export type OriginalCommandCatalog={original_session_id:string;scope:OriginalFileScope;snapshot_ref:NativeRef;commands:OriginalCommandObservation[];coverage:{basis_scope:'DECLARED_FILES_ONLY';all_dependencies_captured:false;reason_codes:string[]}};
export type OriginalCommandDisplayCoverage={contextKey:string;scope:OriginalFileScope;original_session_id:string;entries:{message_id:string;part_id:string;call_id:string}[]};
const sha=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const id=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v);
const count=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>=0;
const reference=(v:unknown):v is NativeRef=>!!v&&typeof v==='object'&&id((v as NativeRef).id)&&(v as NativeRef).revision===1&&sha((v as NativeRef).sha256);
const canonical=(v:unknown):string=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical((v as Record<string,unknown>)[k])).join(',')+'}':JSON.stringify(v);
const same=(a:unknown,b:unknown)=>canonical(a)===canonical(b);
const path=(v:unknown):v is string=>typeof v==='string'&&v.length>0&&v.length<=4096&&!/[\\:\x00-\x1f\x7f]/.test(v)&&!v.startsWith('/')&&v.split('/').every(p=>p!==''&&p!=='.'&&p!=='..');
const text=(v:unknown,max:number):v is string=>typeof v==='string'&&v.length<=max&&!v.includes('\0');
const terminal=(p:AgentSession['messages'][number]['parts'][number])=>p.type==='tool'&&p.tool==='bash'&&['completed','error'].includes(String((p.state as {status?:unknown}|undefined)?.status));
/** A completed unique original call changes the read key; idle timestamps do not. */
export function originalCommandsObservation(session?:AgentSession):string|undefined{
 if(!session||!originalFileScope(session)||!id(session.original_session_id))return;
 const calls=new Set<string>(),identities=new Map<string,number>();
 for(const message of session.messages)if(message.info.role==='assistant'&&message.info.sessionID===session.original_session_id)for(const part of message.parts)if(part.type==='tool'&&part.tool==='bash'&&id(part.callID))identities.set(part.callID,(identities.get(part.callID)??0)+1);
 for(const message of session.messages){
  if(message.info.role!=='assistant'||message.info.sessionID!==session.original_session_id)continue;
  for(const part of message.parts)if(terminal(part)&&id(message.info.id)&&id(part.id)&&id(part.callID)&&identities.get(part.callID)===1)calls.add(canonical([message.info.id,part.id,part.callID]));
 }
 return calls.size?canonical({scope:originalFileScope(session),original_session_id:session.original_session_id,calls:[...calls].sort()}):undefined;
}
function snapshot(value:unknown):value is CommandSourceSnapshot|null{
 if(value===null)return true;
 if(!value||typeof value!=='object')return false;
 const v=value as CommandSourceSnapshot;
 if(!count(v.bytes)||!count(v.line_count))return false;
 if(v.state==='ABSENT')return v.sha256===null&&v.bytes===0&&v.line_count===0&&v.blob===null&&v.stat===null;
 return v.state==='UTF8'&&v.bytes<=262144&&sha(v.sha256)&&v.blob==='blobs/'+v.sha256+'.utf8'&&!!v.stat&&typeof v.stat==='object'&&['dev','ino','size','mtime_ns','ctime_ns'].every(k=>typeof v.stat![k]==='string'&&/^-?\d+$/.test(v.stat![k]));
}
export function parseOriginalCommandObservations(raw:unknown,session:AgentSession):OriginalCommandCatalog{
 const value=raw as OriginalCommandCatalog&{schema:string;read_only:boolean;model_calls:number;automatic_replay:boolean},scope=originalFileScope(session);
 if(!scope||!id(session.original_session_id)||!value||value.schema!=='eeas.original-command-observations.v1'||!same(value.scope,scope)||!reference(value.snapshot_ref)||value.read_only!==true||value.model_calls!==0||value.automatic_replay!==false||!Array.isArray(value.commands)||value.commands.length>512||!value.coverage||value.coverage.basis_scope!=='DECLARED_FILES_ONLY'||value.coverage.all_dependencies_captured!==false||!Array.isArray(value.coverage.reason_codes)||value.coverage.reason_codes.some(v=>!id(v)))throw Error('COMMAND_OBSERVATION_CHANGED');
 const identities=new Map<string,number>();
 for(const message of session.messages)if(message.info.role==='assistant'&&message.info.sessionID===session.original_session_id)for(const part of message.parts)if(part.type==='tool'&&part.tool==='bash'&&id(part.callID))identities.set(part.callID,(identities.get(part.callID)??0)+1);
 const owned=new Map<string,{parent:unknown;index:number;parts:Map<string,number>}>();
 session.messages.forEach((message,index)=>{
  if(message.info.role!=='assistant'||message.info.sessionID!==session.original_session_id)return;
  const parts=new Map<string,number>();for(const part of message.parts)if(terminal(part)&&id(part.id)&&id(part.callID)){const k=canonical([part.id,part.callID]);parts.set(k,(parts.get(k)??0)+1);}
  owned.set(message.info.id,{parent:message.info.parentID,index,parts});
 });
 const seen=new Set<string>();
 for(const entry of value.commands){
  const owner=owned.get(entry.message_id),s=(entry as OriginalCommandObservation&{source_scope?:OriginalFileScope}).source_scope;
  if(!reference(entry.record_ref)||seen.has(entry.record_ref.id)||!id(entry.operation_id)||!id(entry.root_operation_id)||!id(entry.native_message_id)||identities.get(entry.call_id)!==1||!owner||owner.parent!==entry.native_message_id||owner.parts.get(canonical([entry.part_id,entry.call_id]))!==1||!count(entry.source_generation)||entry.source_generation<1||entry.source_generation>scope.generation||s&&(!same({...s,generation:scope.generation},scope)||s.generation!==entry.source_generation)||!text(entry.command,32768)||!entry.command||entry.workdir!==null&&!text(entry.workdir,4096)||entry.exit_code!==null&&!Number.isSafeInteger(entry.exit_code)||entry.status!==(entry.exit_code===null?'UNKNOWN_OUTCOME':'EXITED')||!['UNCHANGED','CHANGED','UNAVAILABLE'].includes(entry.source_status)||entry.qualification!=='ORIGINAL_COMMAND_OBSERVATION_ONLY'||!Array.isArray(entry.sources)||entry.sources.length>32)throw Error('COMMAND_OBSERVATION_CHANGED');
  seen.add(entry.record_ref.id);const paths=new Set<string>();
  for(const source of entry.sources){if(!path(source.path)||paths.has(source.path)||!snapshot(source.before)||!snapshot(source.after))throw Error('COMMAND_OBSERVATION_CHANGED');paths.add(source.path);}
  const expectedSource=!entry.sources.length||entry.sources.some(s=>s.before===null||s.after===null)?'UNAVAILABLE':entry.sources.some(s=>!same(s.before,s.after))?'CHANGED':'UNCHANGED';
  if(entry.source_status!==expectedSource)throw Error('COMMAND_OBSERVATION_CHANGED');
  const output=entry.output;
  if(entry.status==='EXITED'&&output===null)throw Error('COMMAND_OBSERVATION_CHANGED');
  if(output!==null&&(!text(output.text,65536)||!sha(output.sha256)||!count(output.bytes)||output.bytes>65536||new TextEncoder().encode(output.text).length!==output.bytes||typeof output.truncated!=='boolean'||typeof output.original_truncated!=='boolean'||output.original_truncated&&!output.truncated||output.original_output_path!==null&&!text(output.original_output_path,4096)))throw Error('COMMAND_OBSERVATION_CHANGED');
 }
 return {original_session_id:session.original_session_id!,scope,snapshot_ref:value.snapshot_ref,commands:[...value.commands].sort((a,b)=>owned.get(a.message_id)!.index-owned.get(b.message_id)!.index),coverage:value.coverage};
}
/** Display metadata only: cover exactly the offered visible rows, never execution authority. */
export function originalCommandDisplayCoverage(catalog:OriginalCommandCatalog|undefined,session:AgentSession,contextKey:string,expanded:boolean):OriginalCommandDisplayCoverage|undefined{
 const scope=originalFileScope(session);if(!catalog||!scope||!same(catalog.scope,scope)||!id(session.original_session_id)||catalog.original_session_id!==session.original_session_id)return;
 return {contextKey,scope,original_session_id:session.original_session_id,entries:(expanded?catalog.commands:catalog.commands.slice(-3)).map(({message_id,part_id,call_id})=>({message_id,part_id,call_id}))};
}
export class OriginalCommandObservationsApi{
 private client:ApiClient;
 constructor(client:ApiClient=api){this.client=client;}
 async list(session:AgentSession,signal?:AbortSignal):Promise<OriginalCommandCatalog>{
  const scope=originalFileScope(session);if(!scope)throw Error('COMMAND_OBSERVATION_CHANGED');
  const w=scope.workspace_ref,query=new URLSearchParams({session_sha256:scope.session_ref.sha256,generation:String(scope.generation),workspace_id:w.id,binding_revision:String(w.revision),binding_sha256:w.binding_sha256});
  const value=parseOriginalCommandObservations(await this.client.request(`${this.client.projectPath(scope.project_id)}/agent-sessions/${encodeURIComponent(scope.session_ref.id)}/command-observations?${query}`,'GET',undefined,signal),session);
  for(const command of value.commands){if(!command.output)continue;const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(command.output.text));if([...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('')!==command.output.sha256)throw Error('COMMAND_OBSERVATION_CHANGED');}
  return value;
 }
}
