import {AuthoredUIError} from './authored-ui-copy.ts';
import { nativeReference, sameNativeReference, type NativeRef } from './native-collaboration.ts';
import type {ExperienceOrigin} from './task-experience';

export const MCP_TIMEOUT_MIN = 100;
export const MCP_TIMEOUT_MAX = 120000;
export const MCP_TIMEOUT_DEFAULT = 60000;

export type ExperienceSourceState={availability:string;forgotten:boolean;revision?:number};
export type CapabilityControl={revision:number;state:'ACTIVE'|'PAUSED';effective_state:'ACTIVE'|'PAUSED';record_sha256:string;finalized:boolean;target_native_operation_id:string;operation_id:string;model_calls:0;processes_started:0};
export type CapabilityState = {
  enabled: boolean; integrity: string; loaded_in_original_session: boolean;
  qualified: boolean; native_binding: string; config_ref: NativeRef;
  effective_enabled?:boolean;experience_source?:ExperienceSourceState|null;capability_control?:CapabilityControl|null;
  availability_observation_current?:boolean;
};
export type AgentSkill = CapabilityState & { id: string; title: string; description: string; source?: string };
export function capabilityExperienceOrigin(item:CapabilityState):ExperienceOrigin|undefined{
 const value=item.experience_source as (ExperienceSourceState&Partial<ExperienceOrigin>)|null|undefined;
 if(value&&typeof value.project_id==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value.project_id)&&typeof value.session_id==='string'&&/^[a-f0-9]{32}$/.test(value.session_id)&&nativeReference(value.candidate_ref)&&value.candidate_ref.revision===1)return {project_id:value.project_id,session_id:value.session_id,candidate_ref:{...value.candidate_ref}};
}
export type MCPProbe = {
  id: string; config_ref: NativeRef; status: string; initialized: boolean;
  list_tools_succeeded: boolean; loaded_in_original_session: boolean; qualified: boolean;
  current_configuration?: boolean; tools_invoked: number; tool_count?: number;
  tools?: { name: string; input_schema_sha256?: string; invoked?: boolean; permission?: string }[];
  error_type?: string; cleanup?: string; scope?: string; [key: string]: unknown;
};
export type AgentMCP = CapabilityState & {
  id: string; type: 'local' | 'remote'; timeout: number; probe_permitted: boolean;
  last_probe?: MCPProbe | null;
};
export type AgentCapabilities = {
  schema: 'eeas.native-capabilities.v1'; skills: AgentSkill[]; mcp: AgentMCP[];
  version?: string; network_mode?: string; base_interfaces?: { id: string; state: string }[];
  [key: string]: unknown;
};
export type CapabilityReceipt = {
  config_ref: NativeRef; saved: boolean; loaded_in_original_session: boolean; qualified: boolean;
  [key: string]: unknown;
};
export type SkillSource = { config_ref: NativeRef; content: string; enabled: boolean; source: string };
export type MCPConfiguration =
  | { type: 'local'; command: string[]; files_sha256: Record<string, string>; enabled: boolean; timeout: number }
  | { type: 'remote'; url: string; enabled: boolean; timeout: number };
export type MCPSource={config_ref:NativeRef;configuration:MCPConfiguration;current_configuration:true;integrity:string};
export type CapabilityOperation={operation_id:string;input_sha256:string;receipt:CapabilityReceipt;kind:'skills'|'mcp';configuration:Record<string,unknown>;previous_config_ref?:NativeRef;previous_configuration?:Record<string,unknown>};
export class CapabilityRejectedError extends AuthoredUIError{
 readonly confirmedRejected=true;readonly code:'PRE_DISPATCH'|'HTTP_REJECTED';readonly status?:number;
 constructor(message:string,code:'PRE_DISPATCH'|'HTTP_REJECTED'='PRE_DISPATCH',status?:number,params?:Record<string,string|number>){super(message,params);this.code=code;this.status=status;}
}
export function isCapabilityRejected(error:unknown):error is CapabilityRejectedError{return error instanceof CapabilityRejectedError&&error.confirmedRejected;}
function rejected(message:string):never{throw new CapabilityRejectedError(message);}
type CapabilityIntent={kind:'skills'|'mcp';id:string;expected:number}&({mode:'IMPORT';content:string;enabled:boolean}|{mode:'CONFIGURE';configuration:MCPConfiguration}|{mode:'ENABLED';ref:NativeRef;enabled:boolean});
export type AgentCapabilityBridge = {
  version: 2;
  getAgentCapabilities: () => Promise<AgentCapabilities>;
  readAgentMCP?: (id:string,ref:NativeRef)=>Promise<MCPSource>;
  inspectAgentCapabilityOperation?: (id:string)=>Promise<CapabilityOperation>;
  readAgentSkill: (id: string) => Promise<SkillSource>;
  importAgentSkill: (body: { operation_id: string; id: string; expected_revision: number; content: string; enabled: boolean }) => Promise<CapabilityReceipt>;
  setAgentSkillEnabled: (id: string, body: { operation_id: string; expected_revision: number; enabled: boolean }) => Promise<CapabilityReceipt>;
  configureAgentMCP: (body: { operation_id: string; id: string; expected_revision: number; configuration: MCPConfiguration }) => Promise<CapabilityReceipt>;
  setAgentMCPEnabled: (id: string, body: { operation_id: string; expected_revision: number; enabled: boolean }) => Promise<CapabilityReceipt>;
  probeAgentMCP: (id: string, body: { config_ref: NativeRef; allowed_effects: ['PROCESS_START']; network_mode: 'AS_CONFIGURED' }) => Promise<MCPProbe>;
};
const bridgeMethods = ['getAgentCapabilities', 'readAgentSkill', 'importAgentSkill', 'setAgentSkillEnabled', 'configureAgentMCP', 'setAgentMCPEnabled', 'probeAgentMCP'] as const;
export function agentCapabilityBridge(host: unknown): AgentCapabilityBridge | undefined {
  const bridge = (host as { eeasDesktop?: Partial<AgentCapabilityBridge> } | undefined)?.eeasDesktop;
  return bridge?.version === 2 && bridgeMethods.every(name => typeof bridge[name] === 'function') ? bridge as AgentCapabilityBridge : undefined;
}
function validRecord(value: unknown): value is CapabilityState & { id: string } {
  const item = value as Partial<CapabilityState & { id: string }> | undefined;
  return !!item && typeof item.id === 'string' && nativeReference(item.config_ref) && item.config_ref.id === item.id &&
    typeof item.enabled === 'boolean' && typeof item.integrity === 'string' && typeof item.loaded_in_original_session === 'boolean' && typeof item.qualified === 'boolean' && typeof item.native_binding === 'string' && validExperienceState(item);
}
function validExperienceState(item:Partial<CapabilityState>):boolean{
 const source=item.experience_source,control=item.capability_control;
 if(source!==undefined&&source!==null&&(!source||typeof source!=='object'||typeof source.availability!=='string'||source.availability.length<1||source.availability.length>100||typeof source.forgotten!=='boolean'||source.revision!==undefined&&(!Number.isInteger(source.revision)||source.revision<0||source.revision>33)))return false;
 if(control!==undefined&&control!==null&&(!control||typeof control!=='object'||!Number.isInteger(control.revision)||control.revision<1||control.revision>65||!['ACTIVE','PAUSED'].includes(control.state)||!['ACTIVE','PAUSED'].includes(control.effective_state)||typeof control.finalized!=='boolean'||!control.finalized&&control.effective_state!=='PAUSED'||control.state==='PAUSED'&&control.effective_state!=='PAUSED'||typeof control.record_sha256!=='string'||!/^[a-f0-9]{64}$/.test(control.record_sha256)||typeof control.target_native_operation_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(control.target_native_operation_id)||typeof control.operation_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(control.operation_id)||control.model_calls!==0||control.processes_started!==0))return false;
 if((source||control)&&typeof item.effective_enabled!=='boolean')return false;
 if(item.effective_enabled!==undefined&&(typeof item.effective_enabled!=='boolean'||item.effective_enabled&&(!item.enabled||!!capabilityBlockReason(item))))return false;
 return true;
}
export function capabilityBlockReason(item:Pick<CapabilityState,'experience_source'|'capability_control'>):'FORGOTTEN'|'SOURCE_UNAVAILABLE'|'PAUSED'|undefined{
 if(item.experience_source?.forgotten)return 'FORGOTTEN';
 if(item.experience_source&&item.experience_source.availability!=='ACTIVE')return 'SOURCE_UNAVAILABLE';
 if(item.capability_control?.effective_state==='PAUSED')return 'PAUSED';
}
export function capabilityAvailability(item:CapabilityState):string{
 if(item.availability_observation_current===false)return '可用状态待核对';
 const reason=capabilityBlockReason(item);
 if(reason==='FORGOTTEN')return '来源已忘记';
 if(reason==='SOURCE_UNAVAILABLE')return '来源已撤回或不可用';
 if(reason==='PAUSED')return '已暂停';
 if(item.enabled&&item.effective_enabled===false)return '当前不可用';
 return item.enabled?'已启用':'已停用';
}
export function assertAgentCapabilities(value: unknown): AgentCapabilities {
  const state = value as AgentCapabilities | undefined;
  if (state?.schema !== 'eeas.native-capabilities.v1' || !Array.isArray(state.skills) || !Array.isArray(state.mcp) ||
    !state.skills.every(item => validRecord(item) && typeof item.title === 'string' && typeof item.description === 'string') ||
    !state.mcp.every(item => validRecord(item) && ['local', 'remote'].includes(item.type) && Number.isInteger(item.timeout) && typeof item.probe_permitted === 'boolean')) throw new Error('能力目录回执不完整，请刷新后重试');
  if (new Set(state.skills.map(item=>item.id)).size !== state.skills.length || new Set(state.mcp.map(item=>item.id)).size !== state.mcp.length) throw new Error('能力目录包含重复身份，请核对服务记录');
  return state;
}
function receipt(value: CapabilityReceipt, id: string, expected: number): CapabilityReceipt {
  const denial=value as unknown as {schema?:string;confirmed_rejected?:boolean;status?:number;code?:string;message?:string};
  if(denial?.schema==='eeas.capability-rejection.v1'&&denial.confirmed_rejected===true&&denial.code==='HTTP_REJECTED'&&[400,403,404,409,422].includes(denial.status??0)&&typeof denial.message==='string')throw new CapabilityRejectedError(denial.message,'HTTP_REJECTED',denial.status);
  if (!nativeReference(value?.config_ref) || value.config_ref.id !== id || value.config_ref.revision !== expected + 1 || value.saved !== true || typeof value.loaded_in_original_session !== 'boolean' || typeof value.qualified !== 'boolean') throw new Error('保存回执未绑定此次配置；结果未知，请刷新核对，勿重复提交');
  return value;
}
function baseline(id: string, ref?: NativeRef): number {
  if (ref && (!nativeReference(ref) || ref.id !== id)) rejected('配置基线身份不一致，请重新读取原版本');
  return ref?.revision ?? 0;
}
function sameConfiguration(a:unknown,b:unknown):boolean{
 if(a===b)return true;if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
 if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((value,index)=>sameConfiguration(value,b[index]));
 const left=Object.keys(a).sort(),right=Object.keys(b).sort();return left.length===right.length&&left.every((key,index)=>key===right[index]&&sameConfiguration((a as Record<string,unknown>)[key],(b as Record<string,unknown>)[key]));
}
export function capabilityStatus(item: CapabilityState,translate:(message:string,params?:Record<string,string|number>)=>string=value=>value): string {
  return [capabilityAvailability(item),item.enabled?'声明已启用':'声明已停用',item.integrity==='CURRENT'?'来源完整':'来源完整性异常',item.loaded_in_original_session?'原会话已加载':'尚未加载原会话',item.qualified?'资格已核实':'资格未核实'].map(value=>translate(value)).join(' · ');
}
export function probeStatus(probe: MCPProbe | null | undefined, current: NativeRef,translate?:(message:string,params?:Record<string,string|number>)=>string): string {
  if (!probe) return translate?translate('尚未连接探测'):'尚未连接探测';
  if (!sameNativeReference(probe.config_ref, current) || probe.current_configuration === false) return translate?translate('旧配置的探测结果，当前配置尚未探测'):'旧配置的探测结果，当前配置尚未探测';
  if (probe.status === 'INITIALIZED_AND_LISTED' && probe.initialized === true && probe.list_tools_succeeded === true) return translate?translate('已初始化并读取 {count} 个工具 · {loaded} · {qualified}',{count:probe.tool_count??probe.tools?.length??0,loaded:translate(probe.loaded_in_original_session?'原会话已加载':'原会话尚未加载'),qualified:translate(probe.qualified?'资格已核实':'资格未核实')}):`已初始化并读取 ${probe.tool_count ?? probe.tools?.length ?? 0} 个工具 · ${probe.loaded_in_original_session?'原会话已加载':'原会话尚未加载'} · ${probe.qualified?'资格已核实':'资格未核实'}`;
  return `${probe.status==='FAILED'?(translate?translate('连接探测失败'):'连接探测失败'):probe.status} ${probe.error_type??''}`.trim();
}
export function skillText(bytes: Uint8Array, filename: string): string {
  if (filename.toLowerCase() !== 'skill.md') throw new Error('请选择原始 SKILL.md 文件');
  if (!bytes.byteLength || bytes.byteLength > 65536) throw new Error('SKILL.md 应为 1–65536 字节的 UTF-8 文本');
  try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { throw new Error('SKILL.md 不是有效 UTF-8，原始内容未导入'); }
}
export function mcpConfiguration(input: { type: 'local' | 'remote'; command: string; files: string; url: string; enabled: boolean; timeout: number }): MCPConfiguration {
  if (!Number.isInteger(input.timeout) || input.timeout < MCP_TIMEOUT_MIN || input.timeout > MCP_TIMEOUT_MAX) throw new CapabilityRejectedError('连接超时应为 {minimum}–{maximum} 毫秒','PRE_DISPATCH',undefined,{minimum:MCP_TIMEOUT_MIN,maximum:MCP_TIMEOUT_MAX});
  if (input.type === 'remote') return { type: 'remote', url: input.url.trim(), enabled: input.enabled, timeout: input.timeout };
  let command: unknown, files: unknown;
  try { command = JSON.parse(input.command); files = JSON.parse(input.files); } catch { rejected('命令与文件 SHA256 应填写有效 JSON，编辑内容已保留'); }
  if (!Array.isArray(command) || !command.length || !command.every(item => typeof item === 'string' && !!item) || !files || typeof files !== 'object' || Array.isArray(files) || !Object.values(files).length || !Object.values(files).every(sha => typeof sha === 'string' && /^[a-f0-9]{64}$/.test(sha))) rejected('填写实际命令参数数组，以及绝对文件路径对应的 SHA256');
  return { type: 'local', command, files_sha256: files as Record<string, string>, enabled: input.enabled, timeout: input.timeout };
}
export function planningToolNames(items: unknown): string[] {
  return Array.isArray(items) ? items.flatMap(item => typeof item === 'string' ? [item] : item && typeof item === 'object' ? [((item as Record<string, unknown>).title ?? (item as Record<string, unknown>).name ?? (item as Record<string, unknown>).id)].filter((name):name is string=>typeof name==='string') : []) : [];
}

export class AgentCapabilityClient {
  private bridge: AgentCapabilityBridge;
  private unresolved=new Map<string,CapabilityIntent>();
  constructor(bridge: AgentCapabilityBridge) { this.bridge = bridge; }
  private remember(operation:string,intent:CapabilityIntent){if(!this.unresolved.has(operation)&&this.unresolved.size>=16)rejected('请先核查未确认的保存操作');const old=this.unresolved.get(operation);if(old&&!sameConfiguration(old,intent))rejected('保存回执未绑定此次配置；结果未知，请刷新核对，勿重复提交');this.unresolved.set(operation,structuredClone(intent));}
  private async commit(operation:string,send:()=>Promise<CapabilityReceipt>,id:string,expected:number){try{const value=receipt(await send(),id,expected);this.unresolved.delete(operation);return value;}catch(error){if(isCapabilityRejected(error))this.unresolved.delete(operation);throw error;}}
  async get() { return assertAgentCapabilities(await this.bridge.getAgentCapabilities()); }
  async operation(id:string){
    if(!/^[A-Za-z0-9_-]{1,100}$/.test(id)||!this.bridge.inspectAgentCapabilityOperation)throw new Error('配置缺少确切版本，请刷新');
    const result=await this.bridge.inspectAgentCapabilityOperation(id);
    const intent=this.unresolved.get(id),configuration=result?.configuration;
    if(!intent||result?.operation_id!==id||!/^[a-f0-9]{64}$/.test(result.input_sha256)||result.kind!==intent.kind||!nativeReference(result.receipt?.config_ref)||result.receipt.config_ref.id!==intent.id||result.receipt.config_ref.revision!==intent.expected+1||result.receipt.saved!==true||!configuration||
      (intent.mode==='IMPORT'&&(configuration.content!==intent.content||configuration.enabled!==intent.enabled||configuration.name!==intent.id))||
      (intent.mode==='CONFIGURE'&&!sameConfiguration(configuration,intent.configuration))||
      (intent.mode==='ENABLED'&&(!sameNativeReference(result.previous_config_ref,intent.ref)||!result.previous_configuration||!sameConfiguration(configuration,{...result.previous_configuration,enabled:intent.enabled}))))throw new Error('保存回执未绑定此次配置；结果未知，请刷新核对，勿重复提交');
    this.unresolved.delete(id);
    return result;
  }
  async mcp(ref:NativeRef):Promise<MCPSource>{
    if(!nativeReference(ref)||!this.bridge.readAgentMCP)throw new Error('配置缺少确切版本，请刷新');
    const result=await this.bridge.readAgentMCP(ref.id,ref),config=result?.configuration;
    if(!sameNativeReference(result?.config_ref,ref)||result.current_configuration!==true||typeof result.integrity!=='string'||!config||typeof config.enabled!=='boolean'||!Number.isInteger(config.timeout)||config.timeout<MCP_TIMEOUT_MIN||config.timeout>MCP_TIMEOUT_MAX||!['local','remote'].includes(config.type)||config.type==='local'&&(!Array.isArray(config.command)||!config.command.every(item=>typeof item==='string')||!config.files_sha256)||config.type==='remote'&&typeof config.url!=='string')throw new Error('所选工具已变化，请刷新后重新选择');
    return result;
  }
  async skill(ref: NativeRef): Promise<SkillSource> {
    const result = await this.bridge.readAgentSkill(ref.id);
    if (!sameNativeReference(result?.config_ref, ref) || typeof result.content !== 'string' || typeof result.enabled !== 'boolean') throw new Error('Skill 已变更，请刷新目录后重新读取；当前编辑内容已保留');
    return result;
  }
  async importSkill(id: string, content: string, enabled: boolean, ref?: NativeRef, operation:string = crypto.randomUUID()) {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) || id.length > 64) rejected('Skill 标识使用小写字母、数字与连字符，并与原文 name 一致');
    if (!content || new TextEncoder().encode(content).byteLength > 65536) rejected('Skill 原文应为 1–65536 字节，内容已保留');
    const expected = baseline(id, ref);
    this.remember(operation,{kind:'skills',mode:'IMPORT',id,expected,content,enabled});
    return this.commit(operation,()=>this.bridge.importAgentSkill({ operation_id: operation, id, expected_revision: expected, content, enabled }),id,expected);
  }
  async enabled(kind: 'skills' | 'mcp', ref: NativeRef, enabled: boolean, operation:string = crypto.randomUUID()) {
    if (!nativeReference(ref)) rejected('配置缺少确切版本，请刷新');
    const body = { operation_id: operation, expected_revision: ref.revision, enabled };
    this.remember(operation,{kind,mode:'ENABLED',id:ref.id,expected:ref.revision,ref,enabled});
    return this.commit(operation,()=>kind==='skills'?this.bridge.setAgentSkillEnabled(ref.id,body):this.bridge.setAgentMCPEnabled(ref.id,body),ref.id,ref.revision);
  }
  async configure(id: string, configuration: MCPConfiguration, ref?: NativeRef, operation:string = crypto.randomUUID()) {
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(id)) rejected('MCP 标识使用字母、数字、下划线或连字符');
    const expected = baseline(id, ref);
    this.remember(operation,{kind:'mcp',mode:'CONFIGURE',id,expected,configuration});
    return this.commit(operation,()=>this.bridge.configureAgentMCP({ operation_id: operation, id, expected_revision: expected, configuration }),id,expected);
  }
  async probe(item: AgentMCP) {
    if (!item.probe_permitted || item.type !== 'local' || !item.enabled || item.integrity !== 'CURRENT') throw new Error('此配置当前不可连接探测，请核对启用状态、来源与支持范围');
    const result = await this.bridge.probeAgentMCP(item.id, { config_ref: item.config_ref, allowed_effects: ['PROCESS_START'], network_mode: 'AS_CONFIGURED' });
    if (!sameNativeReference(result?.config_ref, item.config_ref) || typeof result.status !== 'string' || typeof result.initialized !== 'boolean' || typeof result.list_tools_succeeded !== 'boolean' || typeof result.loaded_in_original_session !== 'boolean' || typeof result.qualified !== 'boolean' || !Number.isInteger(result.tools_invoked)) throw new Error('连接探测回执不完整；结果未知，请刷新核对，勿重新启动');
    return result;
  }
}
