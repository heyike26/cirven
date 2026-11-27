import { api, operationId, type ApiClient } from './api.ts';
import type { ArtifactEnvelope, SourceRef, Workspace } from './contracts';
export type Json = null | boolean | number | string | Json[] | { [key:string]:Json };
export type WorkbenchKey = {project_id:string;workspace_id?:string;binding_revision?:number;binding_sha256?:string;session_id?:string;kind:string;object_id:string;revision:number;sha256:string};
export type WorkbenchState = {status:'EMPTY'|'SAVED';schema_version:1;key:WorkbenchKey;state_revision:number;fields:Record<string,Json>;view:Record<string,Json>;dirty:boolean;sha256?:string|null;updated_at?:string|null};
export type DraftStatus = 'loading'|'ready'|'saving'|'saved'|'error'|'conflict'|'unknown';
const hash = (value:unknown):value is string => typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export function keyIdentity(key:WorkbenchKey){return JSON.stringify([key.project_id,key.workspace_id??'',key.binding_revision??0,key.binding_sha256??'',key.session_id??'',key.kind,key.object_id,key.revision,key.sha256]);}
export function artifactStateKey(artifact:ArtifactEnvelope):WorkbenchKey|undefined{
 if(!artifact.scope.projectId||!hash(artifact.contentHash)||!Number.isSafeInteger(artifact.revision)||artifact.revision<1)return;
 if(!['local-file','taskbook','harness','proposal','discovery','report','competition','experience'].includes(artifact.type))return;
 const source=(artifact.type==='local-file'?(artifact.data as {source_ref?:SourceRef})?.source_ref:undefined);
 return {project_id:artifact.scope.projectId,...source?{workspace_id:source.workspace_id,binding_revision:source.binding_revision,binding_sha256:source.binding_sha256}:{},...(artifact.scope.sessionId?{session_id:artifact.scope.sessionId}:{}),kind:artifact.type,object_id:source?.path??artifact.artifactId,revision:artifact.revision,sha256:artifact.contentHash};
}
export function workspaceStateKey(value:Workspace):WorkbenchKey{return {project_id:value.project_id,workspace_id:value.id,binding_revision:value.revision,binding_sha256:value.binding_sha256,kind:'workspace',object_id:value.id,revision:value.revision,sha256:value.binding_sha256};}
export class WorkbenchStateApi{
 private client:Pick<ApiClient,'request'|'projectPath'>;constructor(client:Pick<ApiClient,'request'|'projectPath'>=api){this.client=client;}
 load(key:WorkbenchKey){return this.client.request<WorkbenchState>(`${this.client.projectPath(key.project_id)}/workbench-state/load`,'POST',{key},undefined,false);}
 save(key:WorkbenchKey,expected_revision:number,fields:Record<string,Json>,view:Record<string,Json>,dirty:boolean,id=operationId()){
  return this.client.request<WorkbenchState&{operation_id:string;replayed:boolean}>(`${this.client.projectPath(key.project_id)}/workbench-state/save`,'POST',{key,expected_revision,operation_id:id,fields,view,dirty},undefined,false);
 }
 operation(key:WorkbenchKey,id:string){return this.client.request<WorkbenchState&{operation_id:string;replayed?:boolean;status:WorkbenchState['status']}>(`${this.client.projectPath(key.project_id)}/workbench-state/operations/${encodeURIComponent(id)}`);}
 drafts(pid:string,offset=0){return this.client.request<{drafts:WorkbenchState[];unavailable?:{key_sha256:string;state_revision:number;status:string}[];has_more:boolean;next_offset:number|null}>(`${this.client.projectPath(pid)}/workbench-state/drafts?offset=${offset}&limit=50`);}
}
export const workbenchStateApi=new WorkbenchStateApi();
function compatible(value:unknown,initial:unknown):boolean{if(initial===undefined)return true;if(initial===null)return value===null||typeof value==='object';if(Array.isArray(initial))return Array.isArray(value);if(typeof initial==='object')return !!value&&typeof value==='object'&&!Array.isArray(value)&&Object.entries(initial as object).every(([k,v])=>compatible((value as Record<string,unknown>)[k],v));return typeof value===typeof initial;}

/** Merge only untouched portions of a draft at the identical immutable basis.
 * New source revisions have different keys and never enter this merge. */
function mergeRestored(initial:unknown,local:unknown,saved:unknown):unknown{
 if(JSON.stringify(initial)===JSON.stringify(local))return saved===undefined?local:saved;
 if(local&&saved&&typeof local==='object'&&typeof saved==='object'&&!Array.isArray(local)&&!Array.isArray(saved)){const a=initial&&typeof initial==='object'?initial as Record<string,unknown>:{},b=local as Record<string,unknown>,c=saved as Record<string,unknown>;return Object.fromEntries([...new Set([...Object.keys(b),...Object.keys(c)])].flatMap(key=>{if(!(key in b)&&key in a)return [];return [[key,key in b?mergeRestored(a[key],b[key],c[key]):c[key]]];}));}
 if(Array.isArray(initial)&&Array.isArray(local)&&Array.isArray(saved)){const key=(v:unknown)=>{if(!v||typeof v!=='object')return;const r=v as Record<string,unknown>;for(const k of ['id','section_id','entry_id','node_id'])if(typeof r[k]==='string')return r[k] as string;};if([...initial,...local,...saved].every(v=>!!key(v))){const a=new Map(initial.map(v=>[key(v),v])),c=new Map(saved.map(v=>[key(v),v])),b=new Set(local.map(key));return [...local.map(v=>mergeRestored(a.get(key(v)),v,c.get(key(v)))),...saved.filter(v=>!a.has(key(v))&&!b.has(key(v)))];}}
 return local;
}

/** One exact-version editing session. UI input wins over an initial late GET;
 * network-unknown writes are only inspected, never silently replayed. */
export class ObjectDraftSession{
 readonly key:WorkbenchKey;fields:Record<string,Json>={};view:Record<string,Json>={};status:DraftStatus='loading';error='';dirty=false;
 private defaults:{fields:Record<string,unknown>;view:Record<string,unknown>}={fields:{},view:{}};
 private revision=0;private version=0;private persistedVersion=0;private fieldEdits=new Set<string>();private viewEdits=new Set<string>();private listeners=new Set<()=>void>();private timer?:ReturnType<typeof setTimeout>;private writing=false;private idleWaiters:(()=>void)[]=[];private unknown?:string;private unknownVersion=0;private loaded=false;
 readonly ready:Promise<void>;
 private remote:WorkbenchStateApi;constructor(key:WorkbenchKey,remote:WorkbenchStateApi=workbenchStateApi){this.key=structuredClone(key);this.remote=remote;this.ready=this.load();}
 subscribe(listener:()=>void){this.listeners.add(listener);return()=>this.listeners.delete(listener);}
 private emit(){for(const fn of this.listeners)fn();}
 registerDefault(channel:'fields'|'view',name:string,value:unknown){if(!(name in this.defaults[channel]))this.defaults[channel][name]=structuredClone(value);}
 registerRecordDefault(name:string,key:string,value:unknown){const current=(this.defaults.fields[name]??{}) as Record<string,unknown>;if(!(key in current))this.defaults.fields[name]={...current,[key]:structuredClone(value)};}
 value<T>(channel:'fields'|'view',name:string,initial:T):T{const value=this[channel][name];return value!==undefined&&compatible(value,initial)?value as T:initial;}
 set<T>(channel:'fields'|'view',name:string,value:T){if(value===undefined){if(!(name in this[channel]))return;delete this[channel][name];}else{if(JSON.stringify(this[channel][name])===JSON.stringify(value))return;this[channel]={...this[channel],[name]:structuredClone(value) as Json};}
  (channel==='fields'?this.fieldEdits:this.viewEdits).add(name);if(channel==='fields')this.dirty=true;this.version++;if(this.status==='saved')this.status='ready';this.emit();this.schedule();}
 markClean(){this.fields={};this.fieldEdits.clear();this.dirty=false;this.version++;this.emit();this.schedule();}
 private async load(){try{const value=await this.remote.load(this.key);this.accept(value,true);this.loaded=true;this.status='ready';this.emit();if(this.version)this.schedule();}catch(error){this.status='error';this.error='本机草稿暂未读到，当前输入仍保留：'+(error instanceof Error?error.message:String(error));this.emit();}}
 private accept(value:WorkbenchState,initial=false){if(!value||keyIdentity(value.key)!==keyIdentity(this.key)||!Number.isSafeInteger(value.state_revision))throw new Error('草稿回执不属于当前对象版本');this.revision=value.state_revision;if(initial){this.fields={...value.fields,...Object.fromEntries([...this.fieldEdits].map(key=>[key,mergeRestored(this.defaults.fields[key],this.fields[key],value.fields[key]) as Json]))};this.view={...value.view,...Object.fromEntries([...this.viewEdits].map(key=>[key,key==='graphs'?{...(value.view.graphs as object??{}),...(this.view.graphs as object??{})}:this.view[key]]))};if(!this.fieldEdits.size)this.dirty=value.dirty;}}
 private schedule(){if(this.timer)clearTimeout(this.timer);if(!this.loaded||this.unknown||this.status==='conflict')return;this.timer=setTimeout(()=>{this.timer=undefined;void this.flush();},220);}
 async flush():Promise<void>{if(this.writing){await new Promise<void>(resolve=>this.idleWaiters.push(resolve));return this.flush();}if(this.timer){clearTimeout(this.timer);this.timer=undefined;}if(!this.loaded||this.writing||this.unknown||this.status==='conflict'||this.version===this.persistedVersion)return;const version=this.version,id=operationId(),fields=structuredClone(this.fields),view=structuredClone(this.view),dirty=this.dirty;this.writing=true;this.status='saving';this.error='';this.emit();
  try{const saved=await this.remote.save(this.key,this.revision,fields,view,dirty,id);if(saved.operation_id!==id)throw new Error('保存回执不属于本次草稿操作');this.accept(saved);this.persistedVersion=version;this.status=this.version===version?'saved':'ready';}
  catch(error){const failure=error as {status?:number;message?:string};if(failure.status===409){this.status='conflict';this.error='本机草稿已有另一版本，当前输入已保留，请先核对。';}else if(failure.status&&failure.status<500){this.status='error';this.error='草稿未保存：'+(failure.message??String(error));}else{this.unknown=id;this.unknownVersion=version;this.status='unknown';this.error='草稿保存结果待核查；当前输入保留，不会自动重写。';}}
  finally{this.writing=false;for(const done of this.idleWaiters.splice(0))done();this.emit();if(this.status==='ready')this.schedule();}
 }
 async finish(){await this.ready;for(let round=0;round<4;round++){if(this.version===this.persistedVersion&&!this.unknown)return;if(!this.loaded||this.unknown||['error','conflict','unknown'].includes(this.status))throw new Error(this.error||'仍有本机草稿没有完成保存');await this.flush();}if(this.version!==this.persistedVersion||this.unknown)throw new Error(this.error||'仍有本机草稿没有完成保存');}
 async inspect(){if(!this.unknown){if(!this.loaded)await this.load();else if(this.status==='error'){this.status='ready';await this.flush();}return;}try{const value=await this.remote.operation(this.key,this.unknown);if(value.operation_id!==this.unknown)throw new Error('操作回执不一致');this.accept(value);this.persistedVersion=this.unknownVersion;this.unknown=undefined;this.status=this.persistedVersion===this.version?'saved':'ready';this.error='上次保存已核对，当前输入保留；继续编辑可保存后续变化。';this.emit();}catch(error){this.error='仍未能核查上次草稿保存，未重发：'+(error instanceof Error?error.message:String(error));this.emit();}}
}
const sessions=new Map<string,ObjectDraftSession>();
export function objectDraft(key:WorkbenchKey){const id=keyIdentity(key);let value=sessions.get(id);if(!value){value=new ObjectDraftSession(key);sessions.set(id,value);}return value;}

/** Normal-close barrier: read-only/clean sessions do not block; every edited
 * exact-version session must have a real persisted receipt before shutdown. */
export async function flushWorkbenchDrafts(){await Promise.all([...sessions.values()].map(session=>session.finish()));}
