import {nativeReference,sameNativeReference,type NativeRef} from './native-collaboration.ts';
import type {AgentSession} from './agent-sessions.ts';

export type ToolsOwnerScope={project_id:string;session_ref:NativeRef;generation:number};
export type ToolProbeRef={id:string;sha256:string};
export type MCPToolAvailability='AVAILABLE'|'DISABLED'|'NOT_PROBED'|'STALE'|'FAILED'|'COLLISION'|'INTEGRITY_ERROR'|'TOO_LARGE';
export type MCPToolSelectionItem={server_id:string;config_ref:NativeRef;probe_ref:ToolProbeRef;tool_id:string;name:string;input_schema_sha256:string};
export type MCPConnectionHint={server_id:string;config_ref:NativeRef;type:'local'|'remote'};
export type MCPToolSelectionDraft={capability_binding_sha256:string;items:MCPToolSelectionItem[];connection_hints?:MCPConnectionHint[]};
export type NativeToolPermission='allow'|'ask'|'deny'|'SCOPED'|'UNKNOWN';
export type NativeToolsCatalog={
 schema:'eeas.native-tools.v1';capability_binding_sha256:string;
 configuration_refs:{skills:Record<string,NativeRef>;mcp:Record<string,NativeRef>};
 owner:{scope:ToolsOwnerScope|null;status:'CURRENT'|'STALE'|'UNAVAILABLE'|'NOT_SELECTED';reason?:string;observed_at?:string;mcp?:Record<string,{config_ref:NativeRef;status:string}>;permission_rules?:unknown[]};
 builtin_tools:{id:string;description:string;description_truncated?:boolean;permission:NativeToolPermission;permission_action?:string;agent_permissions?:{plan:NativeToolPermission;build:NativeToolPermission};input_schema_sha256:string;selectable_for_new_session:false;source:'ORIGINAL_OWNER_REGISTRY'}[];
 mcp_servers:{id:string;config_ref:NativeRef;enabled:boolean;availability:MCPToolAvailability;probe_ref:ToolProbeRef|null;tools:{id:string;name:string;input_schema_sha256:string;selectable:boolean}[];source:'PRIVATE_MCP_PROBE_NOT_ORIGINAL_OWNER'}[];
 read_only:true;model_calls:0;effects_applied:false;
};
export type NativeToolsBridge={version:2;getAgentTools:(scope?:ToolsOwnerScope)=>Promise<NativeToolsCatalog>};
const hash=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const probe=(value:unknown):value is ToolProbeRef=>{const ref=value as ToolProbeRef|undefined;return !!ref&&typeof ref.id==='string'&&/^[a-f0-9]{32}$/.test(ref.id)&&hash(ref.sha256);};
const permissions=new Set(['allow','ask','deny','SCOPED','UNKNOWN']);
const availability=new Set(['AVAILABLE','DISABLED','NOT_PROBED','STALE','FAILED','COLLISION','INTEGRITY_ERROR','TOO_LARGE']);
const sameScope=(a:ToolsOwnerScope|null|undefined,b:ToolsOwnerScope|null|undefined)=>!a&&!b||!!a&&!!b&&a.project_id===b.project_id&&a.generation===b.generation&&sameNativeReference(a.session_ref,b.session_ref);
export function scopeForTools(owner?:AgentSession):ToolsOwnerScope|undefined{return owner?{project_id:owner.project_id,session_ref:{...owner.session_ref},generation:owner.generation}:undefined;}
export function nativeToolsBridge(host:unknown):NativeToolsBridge|undefined{
 const bridge=(host as {eeasDesktop?:Partial<NativeToolsBridge>}|undefined)?.eeasDesktop;
 return bridge?.version===2&&typeof bridge.getAgentTools==='function'?bridge as NativeToolsBridge:undefined;
}
export function assertNativeToolsCatalog(value:unknown,scope?:ToolsOwnerScope):NativeToolsCatalog{
 const result=value as NativeToolsCatalog|undefined;
 if(!result||result.schema!=='eeas.native-tools.v1'||!hash(result.capability_binding_sha256)||result.read_only!==true||result.model_calls!==0||result.effects_applied!==false||!result.configuration_refs?.skills||!result.configuration_refs?.mcp||!Array.isArray(result.builtin_tools)||result.builtin_tools.length>256||!Array.isArray(result.mcp_servers)||result.mcp_servers.length>128||!result.owner||!['CURRENT','STALE','UNAVAILABLE','NOT_SELECTED'].includes(result.owner.status)||!sameScope(result.owner.scope,scope))throw new Error('能力目录回执不完整，请刷新后重试');
 if(new TextEncoder().encode(JSON.stringify(result)).byteLength>524288)throw new Error('能力目录回执不完整，请刷新后重试');
 if(result.owner.status!=='CURRENT'&&result.builtin_tools.length)throw new Error('能力目录回执不完整，请刷新后重试');
 const seen=new Set<string>();let count=0;
 for(const item of result.builtin_tools){if(!item||typeof item.id!=='string'||seen.has(item.id)||typeof item.description!=='string'||!permissions.has(item.permission)||!hash(item.input_schema_sha256)||item.selectable_for_new_session!==false||item.source!=='ORIGINAL_OWNER_REGISTRY')throw new Error('能力目录回执不完整，请刷新后重试');seen.add(item.id);}
 const servers=new Set<string>();
 for(const server of result.mcp_servers){
  if(!server||typeof server.id!=='string'||servers.has(server.id)||!nativeReference(server.config_ref)||server.config_ref.id!==server.id||!sameNativeReference(server.config_ref,result.configuration_refs.mcp[server.id])||typeof server.enabled!=='boolean'||!availability.has(server.availability)||server.probe_ref!==null&&!probe(server.probe_ref)||!Array.isArray(server.tools)||server.source!=='PRIVATE_MCP_PROBE_NOT_ORIGINAL_OWNER'||server.tools.length&&(server.availability!=='AVAILABLE'||!server.enabled||!server.probe_ref))throw new Error('能力目录回执不完整，请刷新后重试');
  servers.add(server.id);
  for(const tool of server.tools){if(!tool||typeof tool.id!=='string'||!/^[A-Za-z0-9_-]{1,200}$/.test(tool.id)||seen.has(tool.id)||typeof tool.name!=='string'||!tool.name||tool.name.length>256||!hash(tool.input_schema_sha256)||tool.selectable!==true||++count>1024)throw new Error('能力目录回执不完整，请刷新后重试');seen.add(tool.id);}
 }
 return result;
}
/** No cache, polling, model call, new owner or permission mutation. */
export class NativeToolsClient{
 private bridge:NativeToolsBridge;
 constructor(bridge:NativeToolsBridge){this.bridge=bridge;}
 async get(scope?:ToolsOwnerScope){return assertNativeToolsCatalog(await this.bridge.getAgentTools(scope),scope);}
}
export function chooseMCPTools(catalog:NativeToolsCatalog,ids:string[]):MCPToolSelectionDraft{
 assertNativeToolsCatalog(catalog,catalog.owner.scope??undefined);
 if(ids.length>64||new Set(ids).size!==ids.length)throw new Error('所选工具已变化，请刷新后重新选择');
 const items:MCPToolSelectionItem[]=ids.map(id=>{
  const server=catalog.mcp_servers.find(server=>server.availability==='AVAILABLE'&&server.enabled&&server.tools.some(tool=>tool.id===id&&tool.selectable));
  const tool=server?.tools.find(tool=>tool.id===id);
  if(!server||!tool||!server.probe_ref)throw new Error('所选工具已变化，请刷新后重新选择');
  return {server_id:server.id,config_ref:{...server.config_ref},probe_ref:{...server.probe_ref},tool_id:tool.id,name:tool.name,input_schema_sha256:tool.input_schema_sha256};
 });
 return {capability_binding_sha256:catalog.capability_binding_sha256,items};
}

function selectedServers(selection:MCPToolSelectionDraft|undefined,names:string[]):Map<string,NativeRef>|undefined{
 if(!selection||!hash(selection.capability_binding_sha256)||!Array.isArray(selection.items)||selection.items.length>64||new Set(names).size!==names.length||JSON.stringify([...names].sort())!==JSON.stringify(selection.items.map(item=>item.tool_id).sort()))return;
 const servers=new Map<string,NativeRef>();
 for(const item of selection.items){if(!item||!nativeReference(item.config_ref)||item.server_id!==item.config_ref.id||servers.has(item.server_id)&&!sameNativeReference(servers.get(item.server_id),item.config_ref))return;servers.set(item.server_id,item.config_ref);}
 return servers;
}

/** Presentation metadata only; session admission still validates the exact selection. */
export function withMCPConnectionHints(selection:MCPToolSelectionDraft,configurations:readonly {id:string;type:'local'|'remote';config_ref:NativeRef;enabled:boolean;integrity:string}[]):MCPToolSelectionDraft{
 const {connection_hints:_,...base}=selection,servers=selectedServers(selection,selection.items.map(item=>item.tool_id));
 if(!servers)return base;
 const hints:MCPConnectionHint[]=[];
 for(const [id,ref] of servers){const matches=configurations.filter(item=>item.id===id&&sameNativeReference(item.config_ref,ref));if(matches.length!==1)return base;const value=matches[0];if(!value.enabled||value.integrity!=='CURRENT'||!['local','remote'].includes(value.type))return base;hints.push({server_id:id,config_ref:{...ref},type:value.type});}
 return {...base,connection_hints:hints};
}

export function mcpPermissionVisibility(selection:MCPToolSelectionDraft|undefined,names:string[]):{local:boolean;remote:boolean}{
 if(!names.length)return {local:false,remote:false};
 const fallback={local:true,remote:true},servers=selectedServers(selection,names),hints=selection?.connection_hints;
 if(!servers||!Array.isArray(hints)||hints.length!==servers.size)return fallback;
 const seen=new Set<string>();
 for(const hint of hints){if(!hint||seen.has(hint.server_id)||!servers.has(hint.server_id)||!sameNativeReference(servers.get(hint.server_id),hint.config_ref)||!['local','remote'].includes(hint.type))return fallback;seen.add(hint.server_id);}
 return {local:hints.some(item=>item.type==='local'),remote:hints.some(item=>item.type==='remote')};
}
