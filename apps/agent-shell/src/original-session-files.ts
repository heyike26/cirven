import {api,type ApiClient} from './api.ts';
import type {AgentSession} from './agent-sessions';
import type {FileContent} from './contracts';
import type {NativeRef} from './native-collaboration';

type WorkspaceRef={id:string;revision:number;binding_sha256:string};
export type OriginalFileScope={project_id:string;session_ref:NativeRef;generation:number;workspace_ref:WorkspaceRef};
export type OriginalFileEntry={diff_ref:NativeRef;path:string;status:'added'|'modified';message_id:string;part_id:string;call_id:string;source_generation:number;source_scope:OriginalFileScope};
export type OriginalFileCatalog={scope:OriginalFileScope;snapshot_ref:NativeRef;files:OriginalFileEntry[]};
const sha=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const positive=(v:unknown):v is number=>Number.isSafeInteger(v)&&Number(v)>0;
const ref=(v:unknown):v is NativeRef=>!!v&&typeof v==='object'&&typeof (v as NativeRef).id==='string'&&(v as NativeRef).id.length>0&&(v as NativeRef).revision===1&&sha((v as NativeRef).sha256);
const canonical=(v:unknown):string=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical((v as Record<string,unknown>)[k])).join(',')+'}':JSON.stringify(v);
const same=(a:unknown,b:unknown)=>canonical(a)===canonical(b);
const path=(v:unknown):v is string=>typeof v==='string'&&v.length>0&&v.length<=4096&&!/[\\:\x00-\x1f\x7f]/.test(v)&&!v.startsWith('/')&&v.split('/').every(p=>p!==''&&p!=='.'&&p!=='..');

export function originalFileScope(session:AgentSession):OriginalFileScope|undefined{
 const w=session.workspace_ref as WorkspaceRef|undefined;
 if(!w||!sha(w.id)||!positive(w.revision)||!sha(w.binding_sha256)||!ref(session.session_ref)||!positive(session.generation))return;
 return {project_id:session.project_id,session_ref:session.session_ref,generation:session.generation,workspace_ref:{id:w.id,revision:w.revision,binding_sha256:w.binding_sha256}};
}
/** One metadata read after a completed observation; idle timestamps/events do not refetch. */
export function originalFilesObservation(session?:AgentSession):string|undefined{
 if(!session||session.active_operation_id||session.pending_permissions.length||session.pending_questions.length)return;
 const scope=originalFileScope(session);if(!scope)return;
 const calls=session.messages.flatMap(message=>message.info.role==='assistant'?message.parts.filter(p=>p.type==='tool'&&(p.state as {status?:string}|undefined)?.status==='completed'&&['edit','write','apply_patch'].includes(String(p.tool))).map(p=>[message.info.id,p.id,p.callID]):[]);
 return calls.length?canonical({scope,calls}):undefined;
}

export function parseOriginalFileCatalog(raw:unknown,session:AgentSession):OriginalFileCatalog{
 const value=raw as {scope:OriginalFileScope;snapshot_ref:NativeRef;files:Record<string,unknown>[];read_only:boolean;model_calls:number},scope=originalFileScope(session);
 if(!scope||!value||!same(value.scope,scope)||!ref(value.snapshot_ref)||value.read_only!==true||value.model_calls!==0||!Array.isArray(value.files)||value.files.length>128)throw Error('FILE_OBSERVATION_CHANGED');
 const complete=new Map<string,{index:number;parts:Set<string>}>();
 session.messages.forEach((m,index)=>{if(m.info.role==='assistant'&&m.info.sessionID===session.original_session_id)complete.set(m.info.id,{index,parts:new Set(m.parts.filter(p=>p.type==='tool'&&(p.state as {status?:string}|undefined)?.status==='completed').map(p=>canonical([p.id,p.callID])))});});
 const files=new Map<string,{file?:OriginalFileEntry;index:number}>();
 for(const row of value.files){
  if(row.content_scope!=='FULL_FILE'||row.source!=='AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS'||row.availability!=='AVAILABLE'||!['added','modified','deleted'].includes(String(row.status)))continue;
  const s=row.source_scope as OriginalFileScope,owner=complete.get(String(row.message_id));
  if(!path(row.path)||!ref(row.diff_ref)||!s||!positive(s.generation)||s.generation>scope.generation||row.source_generation!==s.generation||!same({...s,generation:scope.generation},scope)||!owner?.parts.has(canonical([row.part_id,row.call_id])))throw Error('FILE_OBSERVATION_CHANGED');
  const file=row as unknown as OriginalFileEntry,previous=files.get(file.path);
  if(!previous||owner.index>=previous.index)files.set(file.path,{file:row.status==='deleted'?undefined:file,index:owner.index});
 }
 return {scope,snapshot_ref:value.snapshot_ref,files:[...files.values()].sort((a,b)=>a.index-b.index).flatMap(v=>v.file?[v.file]:[])};
}

export class OriginalSessionFilesApi{
 private client:ApiClient;
 constructor(client:ApiClient=api){this.client=client;}
 private route(scope:OriginalFileScope){return `${this.client.projectPath(scope.project_id)}/agent-sessions/${encodeURIComponent(scope.session_ref.id)}/diffs`;}
 private query(scope:OriginalFileScope){const w=scope.workspace_ref;return new URLSearchParams({session_sha256:scope.session_ref.sha256,generation:String(scope.generation),workspace_id:w.id,binding_revision:String(w.revision),binding_sha256:w.binding_sha256});}
 async list(session:AgentSession,signal?:AbortSignal){const scope=originalFileScope(session);if(!scope)throw Error('FILE_OBSERVATION_CHANGED');return parseOriginalFileCatalog(await this.client.request(this.route(scope)+'?'+this.query(scope),'GET',undefined,signal),session);}
 async read(catalog:OriginalFileCatalog,entry:OriginalFileEntry,signal?:AbortSignal):Promise<{file:FileContent;changed:boolean}>{
  if(!catalog.files.some(v=>same(v,entry)))throw Error('FILE_OBSERVATION_CHANGED');
  const query=this.query(catalog.scope);query.set('snapshot_sha256',catalog.snapshot_ref.sha256);query.set('diff_sha256',entry.diff_ref.sha256);
  const detail=await this.client.request<Record<string,unknown>>(this.route(catalog.scope)+'/'+encodeURIComponent(entry.diff_ref.id)+'?'+query,'GET',undefined,signal);
  if(!same(detail.scope,catalog.scope)||!same(detail.snapshot_ref,catalog.snapshot_ref)||!same(detail.diff_ref,entry.diff_ref)||detail.path!==entry.path||detail.content_scope!=='FULL_FILE'||detail.source!=='AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS'||detail.read_only!==true||detail.model_calls!==0||!sha(detail.after_sha256)||typeof detail.after!=='string')throw Error('FILE_OBSERVATION_CHANGED');
  const bytes=new TextEncoder().encode(detail.after),hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
  if(hash!==detail.after_sha256)throw Error('FILE_OBSERVATION_CHANGED');
  const {scope}=catalog,w=scope.workspace_ref,file=await this.client.file(scope.project_id,w.id,entry.path,signal),s=file.source_ref;
  if(!s||s.kind!=='local_file'||s.project_id!==scope.project_id||s.workspace_id!==w.id||s.binding_revision!==w.revision||s.binding_sha256!==w.binding_sha256||s.path!==entry.path||!positive(s.revision)||!sha(s.sha256)||!file.current||file.live_sha256!==s.sha256)throw Error('FILE_OBSERVATION_CHANGED');
  return {file,changed:s.sha256!==hash};
 }
}
