import type {ConfigurationOwner,ConfigurationRequest} from './conversation-configuration';
import {AuthoredUIError,authoredCopy,authoredErrorCopy,type AuthoredUICopy} from './authored-ui-copy.ts';
import { api, ApiError, operationId, type ApiClient } from './api.ts';
import { nativeReference, sameNativeReference, nativeInputAttachments, type NativeRef, type NativeConversationRef, type NativeInputAttachment } from './native-collaboration.ts';
import type { Workspace } from './contracts';
import productBrand from './brand.ts';
import type {MCPToolSelectionDraft} from './native-tools.ts';
import {originalFileInput,type OriginalFileInput} from './original-file-input.ts';
import {originalWorkRef,originalWorkStatusText,verifyOriginalWorkInputReceipt} from './original-work-input';

export type OriginalAgent = 'plan' | 'build' | 'workflow';
export type PendingRequest = { request_ref: { id: string; sha256: string; generation: number }; native_request: Record<string, unknown>; kind: string; allowed_choices: string[]; reply_is_execution_authorization: boolean };
export type OriginalMessage = { info: { id: string; role: string; sessionID?: string; [key: string]: unknown }; parts: { id?: string; type: string; text?: string; [key: string]: unknown }[];input_attachments?:NativeInputAttachment[];input_file?:{filename:string};conversation_input_origin?:{operation_id:string;request_sha256:string;context_sha256:string};conversation_input_unavailable?:boolean;context_summary?:unknown };
export type ReadableOriginalPart = {id?:string;kind:'text';text:string}|{id?:string;kind:'file';filename?:string}|{id?:string;kind:'tool';status:'pending'|'running'|'completed'|'error'|'unknown'};
/** Transient live messages use the same user-facing boundary as saved history.
 * Authored text stays exact; SDK lifecycle parts and tool payloads stay private. */
export function originalReadableParts(message:OriginalMessage):ReadableOriginalPart[] {
  if(!['user','assistant'].includes(message.info.role))return [];
  return message.parts.flatMap((part):ReadableOriginalPart[]=>{
    if(part.type==='text'&&typeof part.text==='string'&&part.text.trim())return [{id:part.id,kind:'text',text:part.text}];
    if(part.type==='file')return [{id:part.id,kind:'file',...(typeof part.filename==='string'?{filename:part.filename}:{})}];
    if(part.type==='tool'){
      const status=(part.state as {status?:unknown}|undefined)?.status;
      return [{id:part.id,kind:'tool',status:status==='pending'||status==='running'||status==='completed'||status==='error'?status:'unknown'}];
    }
    return [];
  });
}
/** Finished tool transport turns stay in the original record, while the
 * actual answer and conversation files carry the visible completed result. */
export function originalCompletedToolOnly(message:OriginalMessage):boolean{
  const time=message.info.time as {completed?:unknown}|undefined;
  if(message.info.role!=='assistant'||message.info.error||typeof time?.completed!=='number'||!Number.isFinite(time.completed))return false;
  const parts=originalReadableParts(message);
  return parts.length>0&&parts.every(part=>part.kind==='tool'&&part.status==='completed');
}
export type OriginalConversationInput={conversationRef?:NativeConversationRef;inputAttachments?:NativeInputAttachment[];fileInput?:OriginalFileInput;contextSelectionId?:string;originalWorkRef?:import('./original-work-input').OriginalWorkRef};
function selectedOriginalInput(input:OriginalConversationInput={}):OriginalConversationInput{
  const ref=input.conversationRef;
  if(ref&&(!/^(native|original|discovery):[a-f0-9]{64}$/.test(ref.id)||!Number.isSafeInteger(ref.revision)||ref.revision<1))throw new Error('会话归属已变化，请刷新后继续；原稿保留。');
  if(input.inputAttachments!==undefined&&!Array.isArray(input.inputAttachments))throw new Error('附件引用不完整，原稿保留。');
  const attachments=nativeInputAttachments((input.inputAttachments??[]).map(item=>{
    if(!item||!['CURRENT_PAGE','SCREENSHOT'].includes(item.kind)||item.kind==='SCREENSHOT'&&!item.image_sha256||item.kind==='CURRENT_PAGE'&&item.image_sha256!==undefined)throw new Error('附件类型或图片依据不完整，原稿保留。');
    return {context_ref:item.context_ref,label:item.label,comment:item.user_note??'',regions:item.regions,...(item.kind==='SCREENSHOT'?{preview_sha256:item.image_sha256}:{})};
  }));
  const selection=input.contextSelectionId;if(selection!==undefined&&!/^[a-f0-9]{64}$/.test(selection))throw Error('上下文选择引用不完整，原稿保留');
  const file=originalFileInput(input.fileInput);
  const work=originalWorkRef(input.originalWorkRef);
  return {...(ref?{conversationRef:{id:ref.id,revision:ref.revision}}:{}),...(attachments.length?{inputAttachments:attachments}:{}),...(file?{fileInput:file}:{}),...(selection?{contextSelectionId:selection}:{}),...(work?{originalWorkRef:work}:{})};
}
function canonicalInput(value:unknown):string{
  if(Array.isArray(value))return '['+value.map(canonicalInput).join(',')+']';
  if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonicalInput((value as Record<string,unknown>)[key])).join(',')+'}';
  return JSON.stringify(value);
}
export type SessionCapability = { config_ref: NativeRef; saved: boolean; probed: boolean; loaded_in_original_session: boolean; load_status: string; invoked: boolean; qualified: boolean; load_error?: string; [key: string]: unknown };
export type ContextAuthorityIssue={code:'SOURCE_CONTEXT_CHANGED'|'SOURCE_CONTEXT_UNAVAILABLE'|'SOURCE_CONTEXT_LEGACY';generation:number;operation_id:string|null;scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY';original_history_retained:true};
export function contextAuthorityNotice(session:AgentSession|undefined,en=false):string{
  const issue=session?.context_authority_issue;
  if(!issue||issue.generation!==session?.generation||issue.original_history_retained!==true)return '';
  if(issue.code==='SOURCE_CONTEXT_LEGACY')return en?'This older execution context cannot be fully checked. Its chat and drafts are kept.':'此旧执行上下文尚不能完整核对，原聊天与草稿保留。';
  if(issue.code==='SOURCE_CONTEXT_UNAVAILABLE')return en?'Source authority is unavailable, so this request was blocked. Your chat and drafts are kept.':'来源授权暂不可核对，本次请求已阻止；原聊天与草稿保留。';
  return en?'Source authority changed, so this request was blocked. Check the selected sources; your chat and drafts are kept.':'来源授权已变化，本次请求已阻止。请核对所选资料，原聊天与草稿保留。';
}
export type OriginalModelBudget={kind:'ORIGINAL_MODEL_BUDGET';project_id:string;session_ref:NativeRef;generation:number;counter:'SUBSCRIPTION_HTTP_RESERVATIONS'|'API_RELAY_RESERVATIONS';scope:'ORIGINAL_OWNER_ALL_NATIVE_DESCENDANTS_ALL_GENERATIONS';status:'AVAILABLE'|'UNAVAILABLE';reservations:number|null;max_calls:number;output_token_cap:string;read_only:true;metadata_writes:0;model_calls:0;commercial_usage:'NOT_MEASURED'};
export type WorkResourceUnits={source_bytes:number;source_reads:number;model_attempts:number;retained_output_bytes:number};
export type OriginalWorkResourceBudget={kind:'ORIGINAL_WORK_RESOURCE_BUDGET';project_id:string;session_ref:NativeRef;generation:number;operation_id:string|null;observed_at:string;scope:'EXACT_SELECTED_ORIGINAL_WORK';read_only:true;metadata_writes:0;model_calls:0;status:'AVAILABLE'|'NOT_ALLOCATED'|'UNAVAILABLE'|'NO_WORK_SELECTED';reason:string|null;usage:null|{kind:'ORIGINAL_WORK_RESOURCE_USAGE';allocation_limits?:WorkResourceUnits;allowance_adjustments?:number;adjustment_capacity?:number;resource_ref:NativeRef;authority:Record<string,unknown>;limits:WorkResourceUnits;used:WorkResourceUnits;pending_reads:number;pending_read_bytes:number;accounting_unconfirmed:boolean;retention_threshold_exceeded:boolean;usage_coverage:'SINCE_ALLOCATION_NOT_RETROACTIVE';counters_include_unconfirmed_reservations:true;model_reservations_are_provider_usage:false;physical_disk_io:'NOT_MEASURED';read_only:true;metadata_writes:0};retained_event_attribution:null|{kind:'ORIGINAL_RETAINED_EVENT_ATTRIBUTION';message_locations:number;pending_bytes:number;gap:null|{first_event_seq:number;reason:string};accounting_unconfirmed:boolean;read_only:true;copied_message_bodies:0;coverage:'NEWLY_BOUND_WORK_ONLY_NOT_RETROACTIVE';physical_disk_bytes:'NOT_MEASURED'}};
export function originalResourceOperation(s:AgentSession):string|undefined{return s.active_operation_id??(s.work_mode_message?.generation===s.generation?s.work_mode_message.operation_id:undefined);}
function resourceInteger(v:unknown,minimum=0):v is number{return typeof v==='number'&&Number.isSafeInteger(v)&&v>=minimum;}
function resourceUnits(v:unknown,minimum:number):v is WorkResourceUnits{if(!v||typeof v!=='object'||Array.isArray(v))return false;const row=v as Record<string,unknown>,keys=['source_bytes','source_reads','model_attempts','retained_output_bytes'];return Object.keys(row).length===4&&keys.every(k=>resourceInteger(row[k],minimum));}
export function assertOriginalWorkResourceBudget(value:unknown,s:AgentSession,operationId?:string):OriginalWorkResourceBudget{
  const v=value as OriginalWorkResourceBudget|undefined;
  if(!v||v.kind!=='ORIGINAL_WORK_RESOURCE_BUDGET'||v.project_id!==s.project_id||!sameNativeReference(v.session_ref,s.session_ref)||v.generation!==s.generation||v.operation_id!==(operationId??null)||v.scope!=='EXACT_SELECTED_ORIGINAL_WORK'||v.read_only!==true||v.metadata_writes!==0||v.model_calls!==0||!['AVAILABLE','NOT_ALLOCATED','UNAVAILABLE','NO_WORK_SELECTED'].includes(v.status)||typeof v.observed_at!=='string'||!Number.isFinite(Date.parse(v.observed_at))||!(v.reason===null||typeof v.reason==='string'))throw Error('RESOURCE_BUDGET_ORIGINAL_WORK_CHANGED');
  if(v.status==='NO_WORK_SELECTED'&&operationId!==undefined||['AVAILABLE','NOT_ALLOCATED'].includes(v.status)&&operationId===undefined)throw Error('RESOURCE_BUDGET_WORK_SELECTION_CHANGED');
  const u=v.usage;
  if(v.status!=='AVAILABLE'){if(u!==null)throw Error('RESOURCE_BUDGET_UNCONFIRMED_USAGE');}
  else if(!u||u.kind!=='ORIGINAL_WORK_RESOURCE_USAGE'||!u.resource_ref||!hash(u.resource_ref.id)||!hash(u.resource_ref.sha256)||!resourceInteger(u.resource_ref.revision)||!u.authority||u.authority.project_id!==s.project_id||!resourceUnits(u.limits,1)||!resourceUnits(u.used,0)||Object.values(u.limits).some(n=>n>=2**48)||!resourceInteger(u.pending_reads)||u.pending_reads>32||!resourceInteger(u.pending_read_bytes)||u.used.source_bytes+u.pending_read_bytes>u.limits.source_bytes||u.used.source_reads>u.limits.source_reads||u.used.model_attempts>u.limits.model_attempts||typeof u.accounting_unconfirmed!=='boolean'||u.retention_threshold_exceeded!==(u.used.retained_output_bytes>=u.limits.retained_output_bytes)||u.usage_coverage!=='SINCE_ALLOCATION_NOT_RETROACTIVE'||u.counters_include_unconfirmed_reservations!==true||u.model_reservations_are_provider_usage!==false||u.physical_disk_io!=='NOT_MEASURED'||u.read_only!==true||u.metadata_writes!==0)throw Error('RESOURCE_BUDGET_COUNTERS_UNCONFIRMED');
  if(u&&('allocation_limits' in u||'allowance_adjustments' in u||'adjustment_capacity' in u)&&(!resourceUnits(u.allocation_limits,1)||Object.values(u.allocation_limits).some(n=>n>=2**48)||!resourceInteger(u.allowance_adjustments)||u.allowance_adjustments>32||u.adjustment_capacity!==32))throw Error('RESOURCE_ALLOWANCE_HISTORY_UNCONFIRMED');
  const a=v.retained_event_attribution;
  if(a!==null&&(!a||a.kind!=='ORIGINAL_RETAINED_EVENT_ATTRIBUTION'||!resourceInteger(a.message_locations)||a.message_locations>4096||!resourceInteger(a.pending_bytes)||typeof a.accounting_unconfirmed!=='boolean'||a.gap!==null&&(!a.gap||!resourceInteger(a.gap.first_event_seq)||typeof a.gap.reason!=='string')||a.read_only!==true||a.copied_message_bodies!==0||a.coverage!=='NEWLY_BOUND_WORK_ONLY_NOT_RETROACTIVE'||a.physical_disk_bytes!=='NOT_MEASURED'))throw Error('RESOURCE_BUDGET_ATTRIBUTION_UNCONFIRMED');
  if(v.status==='AVAILABLE'&&a===null)throw Error('RESOURCE_BUDGET_ATTRIBUTION_MISSING');return v;
}
export function originalResourceNeedsReview(v:OriginalWorkResourceBudget):boolean{return v.status==='UNAVAILABLE'||!!v.usage&&(v.usage.accounting_unconfirmed||v.usage.retention_threshold_exceeded||v.usage.pending_reads>0||v.usage.used.model_attempts>=v.usage.limits.model_attempts||v.usage.used.source_reads>=v.usage.limits.source_reads||v.usage.used.source_bytes+v.usage.pending_read_bytes>=v.usage.limits.source_bytes)||!!v.retained_event_attribution&&(v.retained_event_attribution.accounting_unconfirmed||v.retained_event_attribution.pending_bytes>0||v.retained_event_attribution.gap!==null);}
export function originalResourceBytes(bytes:number):string{if(!resourceInteger(bytes))return '—';return bytes<1024?String(bytes)+' B':bytes<1048576?(bytes/1024).toFixed(1)+' KiB':(bytes/1048576).toFixed(1)+' MiB';}
export type AgentSession = {
  project_id: string; session_ref: NativeRef; owner_status: string; generation: number; connected_owner: boolean;
  native_status: string; original_session_id?: string | null; configuration_current: boolean; workspace_kind: string;
  provider_profile_ref?: NativeRef; observation_is_current?: boolean;
  context_authority_issue?:ContextAuthorityIssue|null;
  available_agent_modes?: OriginalAgent[]; workflow_mode_supported?: boolean;
  work_mode_message?: {operation_id:string;agent:OriginalAgent;generation:number;policy_sha256:string|null;native_message_id?:string};
  active_operation_id?: string | null; messages: OriginalMessage[]; todo: Record<string, unknown>[]; children: Record<string, unknown>[];
  guidance?: {operation_id:string;status:string;role?:string;generation?:number;original_active_operation_id?:string;native_user_observed?:boolean;individual_response_observed?:boolean;response_evidence?:string;cohort_response_status?:string}[];
  pending_questions: PendingRequest[]; pending_permissions: PendingRequest[]; original_agents?: Record<string, unknown>[];
  capability_load?: { binding_sha256: string; skills: (SessionCapability & { id: string })[]; mcp: Record<string, SessionCapability> } | null;
  observation_coverage?: Record<string, unknown> | string; native_messages_error?: unknown; last_event_seq: number; observation_at?: string;
  qualification?: Record<string, unknown>; automatic_post_replay: false; [key: string]: unknown;
};
export type AgentSessionOperation = { id: string; status: string; request_sha256: string; automatic_replay: false; operation_ref?: { id: string; request_sha256: string }; session_ref?: NativeRef; dispatch_status?: string; native_message_id?: string; [key: string]: unknown };
export function originalGuidanceText(operation:AgentSessionOperation,translate?:(message:string,params?:Record<string,string|number>)=>string):string{
  if(operation.role==='LOCAL_STATUS'){
    const value=operation.local_status as {active_operation_id?:string|null;operation_status?:string;pending_questions?:number;pending_permissions?:number;original_work?:{status?:string;active_relation?:string;active_operation_id?:string|null}}|undefined;
    if(!value)return translate?translate('当前任务状态尚未读取，原记录保留。'):'当前任务状态尚未读取，原记录保留。';
    if(value.original_work){const work=value.original_work,t=translate??((text:string)=>text),labels:Record<string,string>={RETURNED:'已返回',COHORT_RETURNED:'同轮已返回',FAILED:'未完成',CANCELLED:'已停止',UNKNOWN_OUTCOME:'待核查',ACCEPTED:'已受理',DISPATCHING:'正在发送',RECORDED_NOT_DISPATCHED:'尚未发送'},scope=work.active_relation==='OTHER_OR_UNVERIFIED_WORK'?'原会话另有运行中或尚未核对的执行；这里显示所选原工作的保存状态。':work.active_operation_id?'所选原工作仍有执行尚未结束。':'未发现所选原工作的活动执行，显示其保存状态。';return t(scope)+' '+t('保存状态：')+t(labels[work.status??'']??'待核查');}
    const state=value.operation_status==='UNKNOWN_OUTCOME'?'当前任务的结果需要核查':value.active_operation_id?'当前任务尚未结束':'当前没有活动任务';
    return translate?translate('{state}。待回答 {questions} 项，待授权 {permissions} 项。此次查看没有请求模型，也没有停止原任务。',{state:translate(state),questions:value.pending_questions??0,permissions:value.pending_permissions??0}):`${state}。待回答 ${value.pending_questions??0} 项，待授权 ${value.pending_permissions??0} 项。此次查看没有请求模型，也没有停止原任务。`;
  }
  if(operation.kind!=='GUIDANCE')return '';
  const labels:Record<string,string>={ACCEPTED:'补充已送入原会话，等待后续轮次读取。',OBSERVED:'原会话已记录这条补充，等待后续处理。',NEXT_TURN_ASSOCIATED:'后续回复已关联这条补充，落实情况待核对。',COHORT_RETURNED:'同轮答复已返回，这条补充的落实情况尚未逐条核验。',RETURNED:'这条补充的回复已返回，可继续核对结果。',FAILED:'这条补充处理未完成，原任务和输入记录保留。',CANCELLED:'这条补充的处理已停止。',UNKNOWN_OUTCOME:'这条补充是否已收到尚未确认；保留原文字，只核查原操作。'};
  const text=labels[operation.status]??'补充内容已保留，正在核对原会话。';return translate?translate(text):text;
}
export type AgentSessionConfiguration = { capability_binding_sha256: string; configuration_refs: { skills: Record<string, NativeRef>; mcp: Record<string, NativeRef> }; loaded_in_original_session: false; permission_granted: false };
export type SessionOpenOptions = { provider: NativeRef; workspace?: Workspace; registeredWorkspace?:{project_id:string;workspace_ref:Pick<Workspace,'id'|'revision'|'binding_sha256'>;allowed_write_paths:string[]};configurationPreparation?:import('./conversation-configuration').ConfigurationPreparation; projectId?: string; title?: string; workspaceWrite?: boolean; processStart?: boolean; remoteMCPConnect?: boolean; commandAllowlist?: string[]; commandObservationPaths?:string[]; mcpToolAllowlist?: string[]; mcpToolSelection?:MCPToolSelectionDraft; workflowRef?:NativeRef;workflowProviderRefs?:NativeRef[];conversationRef?:NativeConversationRef;preserveExecutionScope?:boolean;originalSource?:ConfigurationOwner|null;executionProjectId?:string|null;ttlSeconds?:number;maxCalls?:number;maxOutputTokens?:number;networkMode?:'AS_CONFIGURED'|'STRICT_OFFLINE' };
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
export function assertAgentSession(value: unknown, expected?: NativeRef, projectId?: string): AgentSession {
  const result = value as AgentSession | undefined;
  if (!result || !nativeReference(result.session_ref) || result.session_ref.revision !== 1 || typeof result.project_id !== 'string' || typeof result.owner_status !== 'string' || typeof result.connected_owner !== 'boolean' || typeof result.configuration_current !== 'boolean' || !Number.isSafeInteger(result.generation) || result.generation < 0 || typeof result.native_status !== 'string' || !Array.isArray(result.messages) || !Array.isArray(result.todo) || !Array.isArray(result.children) || !Array.isArray(result.pending_questions) || !Array.isArray(result.pending_permissions) || result.automatic_post_replay !== false || !Number.isSafeInteger(result.last_event_seq)) throw new Error('原会话回执不完整，请核对原操作，不能自动重开');
  if (expected && !sameNativeReference(result.session_ref, expected) || projectId && result.project_id !== projectId) throw new Error('原会话回执属于另一作用域');
  return result;
}
export function originalSessionReady(value?: AgentSession): boolean { return !!value && value.owner_status === 'READY' && value.connected_owner && value.configuration_current && value.observation_is_current === true && value.generation > 0; }
/** Saved idle history remains readable after normal disconnect. An actual
 * observation error or uncertain unfinished work still needs visible recovery. */
export function originalMessagesNeedAttention(value:AgentSession):boolean{
 return !!value.native_messages_error||!!value.observation_error||value.observation_is_current===false&&(
  value.connected_owner||!!value.active_operation_id||value.pending_questions.length>0||value.pending_permissions.length>0);
}
/** A new user message may reconnect an idle, exact persisted owner. This never
 * authorizes replay of an old operation or recovery of an uncertain live one. */
export function originalSessionCanResumeForSend(value?: AgentSession): boolean {
  return !!value && ['EXPIRED','DISCONNECTED'].includes(value.owner_status)
    && value.connected_owner === false && value.configuration_current === true
    && Number.isSafeInteger(value.generation) && value.generation > 0
    && nativeReference(value.session_ref) && value.active_operation_id === null
    && Array.isArray(value.pending_questions) && value.pending_questions.length === 0
    && Array.isArray(value.pending_permissions) && value.pending_permissions.length === 0
    && value.automatic_post_replay === false;
}
/** Stop the exact existing owner without admitting new execution on stale configuration. */
export function originalSessionCanStop(value?: AgentSession): boolean { return !!value && value.owner_status === 'READY' && value.connected_owner && value.generation > 0; }
function assertOperation(value: unknown, key: string): AgentSessionOperation {
  const result = value as AgentSessionOperation | undefined;
  if (!result || result.id !== key || typeof result.status !== 'string' || !hash(result.request_sha256) || result.automatic_replay !== false) throw new Error('原会话操作回执未绑定本次请求；结果未知，不能重发');
  return result;
}
export class AgentSessionOutcomeError extends AuthoredUIError {
  readonly operationId: string;
  readonly receipt?: AgentSessionOperation;
  readonly failureCode?: 'WORKSPACE_SOURCE_CHANGED';
  constructor(message: string, operationId: string, receipt?: AgentSessionOperation, copy?:AuthoredUICopy, failureCode?:'WORKSPACE_SOURCE_CHANGED') {
    super(copy?.message_key??message,copy?.params); this.operationId = operationId; this.receipt = receipt;
    // Classify the original failure before copy is localized; consumers never parse UI text.
    this.failureCode = failureCode ?? (message.startsWith('WORKSPACE_SOURCE_CHANGED:') || receipt?.error_code==='WORKSPACE_SOURCE_CHANGED' ? 'WORKSPACE_SOURCE_CHANGED' : undefined);
  }
}
export class AgentSessionsApi {
  private client: ApiClient;
  constructor(client: ApiClient = api) { this.client = client; }
  root(pid: string, id?: string) { return `${this.client.projectPath(pid)}/agent-sessions${id ? '/' + encodeURIComponent(id) : ''}`; }
  async modelBudget(s:AgentSession){
    const v=await this.client.request<OriginalModelBudget>(this.root(s.project_id,s.session_ref.id)+'/model-budget?session_sha256='+encodeURIComponent(s.session_ref.sha256));
    if(v.kind!=='ORIGINAL_MODEL_BUDGET'||v.project_id!==s.project_id||!sameNativeReference(v.session_ref,s.session_ref)||v.generation!==s.generation||v.scope!=='ORIGINAL_OWNER_ALL_NATIVE_DESCENDANTS_ALL_GENERATIONS'||!['SUBSCRIPTION_HTTP_RESERVATIONS','API_RELAY_RESERVATIONS'].includes(v.counter)||!['AVAILABLE','UNAVAILABLE'].includes(v.status)||!Number.isSafeInteger(v.max_calls)||v.max_calls<1||v.max_calls>128||v.read_only!==true||v.metadata_writes!==0||v.model_calls!==0||v.commercial_usage!=='NOT_MEASURED'||(v.status==='AVAILABLE'?typeof v.reservations!=='number'||!Number.isSafeInteger(v.reservations)||v.reservations<0||v.reservations>v.max_calls:v.reservations!==null))throw Error('MODEL_BUDGET_ORIGINAL_OWNER_CHANGED');return v;
  }
  async resourceBudget(s:AgentSession,operationId?:string){
    if(operationId!==undefined&&!/^[A-Za-z0-9_-]{1,100}$/.test(operationId))throw Error('RESOURCE_BUDGET_WORK_SELECTION_INVALID');
    const query='?session_sha256='+encodeURIComponent(s.session_ref.sha256)+(operationId===undefined?'':'&operation_id='+encodeURIComponent(operationId));
    return assertOriginalWorkResourceBudget(await this.client.request<unknown>(this.root(s.project_id,s.session_ref.id)+'/resource-budget'+query),s,operationId);
  }
  async configuration() {
    const result = await this.client.request<AgentSessionConfiguration>('/api/agent-sessions/configuration');
    if (!hash(result.capability_binding_sha256) || !result.configuration_refs?.skills || !result.configuration_refs?.mcp || result.permission_granted !== false || result.loaded_in_original_session !== false) throw new Error('原会话配置绑定尚未可核实');
    return result;
  }
  async list(pid: string) { const result = await this.client.request<{ sessions: unknown[] }>(this.root(pid)); if (!Array.isArray(result.sessions)) throw new Error('会话目录回执不完整'); return result.sessions.map(value => assertAgentSession(value, undefined, pid)); }
  async get(pid: string, ref: NativeRef) { return assertAgentSession(await this.client.request(this.root(pid, ref.id)), ref, pid); }
  async prepareOpen(options:SessionOpenOptions,key:string=operationId()){
    const selected=selectedOriginalInput({conversationRef:options.conversationRef});
    if (!nativeReference(options.provider)) throw new Error('请先明确选择已保存的模型连接');
    const registered=options.registeredWorkspace,workspaceProject=registered?.project_id??options.workspace?.project_id,workspaceRef=registered?.workspace_ref??options.workspace;
    if (workspaceProject && workspaceProject !== options.projectId) throw new Error('会话工作目录属于另一项目');
    if (options.workspaceWrite && !(registered?.allowed_write_paths??options.workspace?.allowed_write_paths)?.length) throw new AuthoredUIError('请先关联有写入授权的工作目录');
    const configuration = await this.configuration();
    if(options.mcpToolSelection&&(options.mcpToolSelection.capability_binding_sha256!==configuration.capability_binding_sha256||JSON.stringify([...options.mcpToolSelection.items.map(item=>item.tool_id)].sort())!==JSON.stringify([...(options.mcpToolAllowlist??[])].sort())))throw new Error('所选工具已变化，请刷新后重新选择');
    const allowed = [...options.processStart ? ['PROCESS_START'] : [], ...options.remoteMCPConnect ? ['REMOTE_MCP_CONNECT'] : [], ...options.commandAllowlist?.length ? ['BACKGROUND_COMMAND'] : [], ...options.workspaceWrite ? ['WORKSPACE_WRITE'] : []];
    return {
      ...(selected.conversationRef?{conversation_ref:selected.conversationRef}:{}),
      operation_id: key, project_id: options.projectId ?? null, workspace_ref: workspaceRef ? { id: workspaceRef.id, revision: workspaceRef.revision, binding_sha256: workspaceRef.binding_sha256 } : null,
      provider_profile_ref: options.provider, capability_binding_sha256: configuration.capability_binding_sha256,
      workflow_ref:options.workflowRef??null,workflow_provider_refs:options.workflowRef?(options.workflowProviderRefs??[]).filter(ref=>ref.id!==options.provider.id):[],
      title: options.title ?? `${productBrand.short_name} 原会话`, ttl_seconds: options.ttlSeconds??600, max_calls: options.maxCalls??24, max_output_tokens: options.maxOutputTokens??4096,
      ...(options.commandObservationPaths===undefined?{}:{command_observation_paths:[...options.commandObservationPaths]}),
      allowed_effects: allowed, command_allowlist: options.commandAllowlist ?? [], mcp_tool_allowlist: options.mcpToolAllowlist ?? [], network_mode: options.networkMode??'AS_CONFIGURED',
      mcp_tool_selection:options.mcpToolSelection?.items??null,
    };
  }
  async prepareConfigurationAdoption(body:unknown,cid:string,signal?:AbortSignal){return this.client.request<unknown>('/api/conversations/'+encodeURIComponent(cid)+'/configuration-adoption/prepare','POST',body,signal,false);}
  async adoptConfiguration(body:unknown,cid:string,signal?:AbortSignal){return this.client.request<unknown>('/api/conversations/'+encodeURIComponent(cid)+'/configuration-adoption','POST',body,signal,false);}
  async configurationAdoptionOperation(cid:string,key:string,signal?:AbortSignal){return this.client.request<unknown>('/api/conversations/'+encodeURIComponent(cid)+'/configuration-adoption-operations/'+encodeURIComponent(key),'GET',undefined,signal);}
  async ownerPreparation(reference:NativeConversationRef,projectId:string|null,signal?:AbortSignal){return this.client.request<unknown>('/api/conversations/'+encodeURIComponent(reference.id)+'/original-owner/prepare','POST',{conversation_ref:reference,project_id:projectId},signal,false);}
  async selectOwner(body:unknown,reference:NativeConversationRef,signal?:AbortSignal){return this.client.request<unknown>('/api/conversations/'+encodeURIComponent(reference.id)+'/original-owner','POST',body,signal,false);}
  async ownerSelectionOperation(reference:NativeConversationRef,key:string,signal?:AbortSignal){return this.client.request<unknown>('/api/conversations/'+encodeURIComponent(reference.id)+'/original-owner-operations/'+encodeURIComponent(key),'GET',undefined,signal);}
  async configurationPreparation(reference:NativeConversationRef,original:ConfigurationOwner|null,signal?:AbortSignal){return this.client.request<unknown>('/api/conversations/'+encodeURIComponent(reference.id)+'/connection-configuration/prepare','POST',{conversation_ref:reference,original_source:original},signal,false);}
  async configurePrepared(body:ConfigurationRequest,signal?:AbortSignal){return assertAgentSession(await this.client.request('/api/conversations/'+encodeURIComponent(body.request.conversation_ref!.id)+'/connection-configuration','POST',body,signal,false));}
  async continueConfiguration(body:ConfigurationRequest,requestSha256:string,signal?:AbortSignal){return this.client.request<unknown>('/api/conversations/'+encodeURIComponent(body.request.conversation_ref!.id)+'/connection-configuration/continue','POST',{operation_id:body.request.operation_id,project_id:body.request.project_id,request_sha256:requestSha256,confirmation_sha256:body.confirmation_sha256},signal,false);}
  async openPrepared(body:PreparedSessionOpen,signal?:AbortSignal){return assertAgentSession(await this.client.request('/api/agent-sessions/open','POST',body,signal,false),undefined,body.conversation_ref?undefined:body.project_id??undefined);}
  async openOperation(projectId:string|null,key:string,signal?:AbortSignal){return this.client.request<unknown>('/api/agent-sessions/open-operations/'+encodeURIComponent(key)+(projectId===null?'':'?project_id='+encodeURIComponent(projectId)),'GET',undefined,signal);}
  async open(options:SessionOpenOptions,key:string=operationId()){
    const body=await this.prepareOpen(options,key);
    try{return await this.openPrepared(body);}
    catch (error) { throw new AgentSessionOutcomeError(`原会话创建未确认（操作 ${key}）；请核查原操作，未自动重开：${error instanceof Error ? error.message : String(error)}`, key,undefined,authoredCopy('原会话创建未确认（操作 {operation}）；请核查原操作，未自动重开：{detail}',{operation:key,detail:authoredErrorCopy(error)})); }
  }
  async operation(session: AgentSession, key: string) { return assertOperation(await this.client.request(`${this.root(session.project_id, session.session_ref.id)}/operations/${encodeURIComponent(key)}`), key); }
  private async write(session: AgentSession, path: string, body: Record<string, unknown>, key: string) {
    const check=(value:unknown)=>{const result=assertOperation(value,key);if(body.original_work_ref)verifyOriginalWorkInputReceipt(result,session,originalWorkRef(body.original_work_ref)!,String(body.raw_text),body.intent!=='STEER'&&originalWorkStatusText(String(body.raw_text)));return result;};
    try { return check(await this.client.request(`${this.root(session.project_id, session.session_ref.id)}${path}`, 'POST', { operation_id: key, session_ref: session.session_ref, generation: session.generation, ...body }, undefined, false)); }
    catch (error) { let receipt: AgentSessionOperation | undefined; try { receipt = check(await this.operation(session, key)); } catch {} throw new AgentSessionOutcomeError(`${error instanceof Error ? error.message : String(error)}（操作 ${key}；仅核查原回执，不自动重发）`, key, receipt,authoredCopy('{detail}（操作 {operation}；仅核查原回执，不自动重发）',{operation:key,detail:authoredErrorCopy(error)})); }
  }
  async send(session: AgentSession, rawText: string, agent: OriginalAgent, key: string = operationId(), input:OriginalConversationInput={}) {
    const selected=selectedOriginalInput(input);
    const inputs={...(selected.conversationRef?{conversation_ref:selected.conversationRef}:{}),...(selected.inputAttachments?.length?{input_attachments:selected.inputAttachments}:{}),...(selected.fileInput?{file_input:selected.fileInput}:{}),...(selected.contextSelectionId?{context_selection_id:selected.contextSelectionId}:{}),...(selected.originalWorkRef?{original_work_ref:selected.originalWorkRef}:{})};
    if (!rawText.trim() || Array.from(rawText).length > 16000) throw new Error('原会话消息须为1–16000字符，原草稿保留');
    const latest = await this.get(session.project_id, session.session_ref);
    const workStatus=!!selected.originalWorkRef&&originalWorkStatusText(rawText);
    if (latest.generation<1||(!originalSessionReady(latest)&&!workStatus) || latest.generation !== session.generation) throw new Error('原会话当前不可发送，请检查连接、配置或原执行；草稿保留');
    if(!workStatus&&selected.contextSelectionId&&latest.active_operation_id)throw Error('当前任务仍在运行，新上下文选择已保留，待任务结束后发送');
    if (!workStatus&&!latest.active_operation_id && agent === 'workflow' && !latest.available_agent_modes?.includes('workflow')) throw new AuthoredUIError('当前连接未加载 Workflow，原输入保留');
    if(selected.conversationRef){
      const root='/api/conversations/'+encodeURIComponent(selected.conversationRef.id)+'/messages';
      const check=(value:unknown)=>{
        const result=value as {operation_id?:string;conversation_ref?:NativeConversationRef;original_source?:{project_id:string;session_ref:NativeRef};operation?:unknown;automatic_post_replay?:boolean};
        if(result?.operation_id!==key||result.conversation_ref?.id!==selected.conversationRef!.id||result.conversation_ref?.revision!==selected.conversationRef!.revision||result.automatic_post_replay!==false||result.original_source?.project_id!==session.project_id||!sameNativeReference(result.original_source.session_ref,session.session_ref))throw Error('原操作身份尚未核对，原稿保留');
        const operation=assertOperation(result.operation,key);if(selected.originalWorkRef)verifyOriginalWorkInputReceipt(operation,latest,selected.originalWorkRef,rawText,workStatus);return operation;
      };
      try{return check(await this.client.request(root,'POST',{operation_id:key,conversation_ref:selected.conversationRef,
        provider_profile_ref:latest.provider_profile_ref,raw_text:rawText,agent,
        original_source:{project_id:latest.project_id,session_ref:latest.session_ref,generation:latest.generation},
        input_attachments:selected.inputAttachments??[],...(selected.fileInput?{file_input:selected.fileInput}:{}),...(selected.contextSelectionId?{context_selection_id:selected.contextSelectionId}:{}),...(selected.originalWorkRef?{original_work_ref:selected.originalWorkRef}:{})},undefined,false));}
      catch(error){let receipt:AgentSessionOperation|undefined;try{receipt=check(await this.client.request(root+'/'+encodeURIComponent(key)));}catch{}throw new AgentSessionOutcomeError('原消息的结果需要核查，原输入与编号已保留；没有自动重发。',key,receipt,authoredCopy('{detail}',{detail:authoredErrorCopy(error)}));}
    }
    if(latest.active_operation_id||workStatus){const guidanceInputs={...inputs};if(workStatus)delete guidanceInputs.context_selection_id;return this.write(latest,'/guidance',{...guidanceInputs,raw_text:rawText,intent:'AUTO',expected_active_operation_id:latest.active_operation_id??null},key);}
    return this.write(session, '/messages', { ...inputs, raw_text: rawText, agent, timeout_seconds: 300 }, key);
  }
  async load(session: AgentSession, key: string = operationId(), expectedGeneration?:number) {
    const result = await this.client.request<{ session: unknown; operation: unknown }>(`${this.root(session.project_id, session.session_ref.id)}/load`, 'POST', { operation_id: key, session_ref: session.session_ref,...expectedGeneration===undefined?{}:{expected_generation:expectedGeneration} }, undefined, false);
    return { session: assertAgentSession(result.session, session.session_ref, session.project_id), operation: assertOperation(result.operation, key) };
  }
  async events(session: AgentSession, after = 0) { return this.client.request<{ events: { seq: number; type: string; payload: unknown }[]; last_event_seq: number; native_replay: false }>(`${this.root(session.project_id, session.session_ref.id)}/events?after=${after}&limit=100`); }
  interrupt(session: AgentSession, key: string = operationId()) { return this.write(session, '/interrupt', {}, key); }
  interruptTask(session:AgentSession,target:string,key:string=operationId()) {
    if(!target||session.active_operation_id!==target||!originalSessionCanStop(session))throw new Error('当前任务已变化，未请求停止');
    return this.write(session,'/interrupt',{expected_active_operation_id:target},key);
  }
  async disconnect(session: AgentSession, key: string = operationId()) {
    const result = assertAgentSession(await this.client.request(`${this.root(session.project_id, session.session_ref.id)}/disconnect`, 'POST', { operation_id: key, session_ref: session.session_ref, generation: session.generation }, undefined, false), session.session_ref, session.project_id);
    return { session: result, operation: assertOperation(result.last_control_operation, key) };
  }
  async pendingReply(session: AgentSession, pending: PendingRequest, choice: string, answers?: string[][], key: string = operationId()) {
    if (!originalSessionReady(session) || pending.request_ref.generation !== session.generation || !pending.allowed_choices.includes(choice) || !hash(pending.request_ref.sha256)) throw new Error('待答请求的原会话、代次或可选范围已变化，请刷新核对');
    const question = pending.kind === 'QUESTION';
    if (question && !['reply', 'reject'].includes(choice) || !question && !['once', 'always', 'reject'].includes(choice)) throw new Error('原请求不接受此操作');
    const path = `/${question ? 'questions' : 'permissions'}/${encodeURIComponent(pending.request_ref.id)}/${question && choice === 'reject' ? 'reject' : 'reply'}`;
    try { return await this.write(session, path, { request_ref: pending.request_ref, ...question && choice === 'reply' ? { answers } : {}, ...!question ? { reply: choice } : {} }, key); }
    catch(error) {
      if(error instanceof AgentSessionOutcomeError&&error.failureCode==='WORKSPACE_SOURCE_CHANGED')throw new AgentSessionOutcomeError(error.message,error.operationId,error.receipt,authoredCopy('文件或修订依据已变更，请拒绝这次修改并重新检查。'),error.failureCode);
      throw error;
    }
  }
}
export type PreparedSessionOpen=Awaited<ReturnType<AgentSessionsApi['prepareOpen']>>;
export type OriginalOperationIndex = { project_id:string;session_ref:NativeRef;operation_id:string;input:string;conversation_input?:OriginalConversationInput;generation?:number;agent?:OriginalAgent };
export type OriginalOperationReview = OriginalOperationIndex & {status:string;receipt?:AgentSessionOperation;message?:string;automatic_retry:false};
type IndexStorage=Pick<Storage,'getItem'|'setItem'>;
export type OriginalOperationLocks={request<T>(name:string,options:{mode:'exclusive';signal:AbortSignal},run:()=>Promise<T>):Promise<T>};
export type OriginalOperationPage={entries:OriginalOperationIndex[];offset:number;next_offset:number|null;total:number};
const ORIGINAL_INDEX_BYTES=1024*1024,ORIGINAL_INDEX_ENTRIES=128,ORIGINAL_RECOVERY_PAGE=8;
const indexKey=(ref:NativeRef)=>`eeas-original-operation:${ref.id}`;
const originalIndexId=(value:unknown):value is string=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
function originalIndexRows(storage:IndexStorage,session:Pick<AgentSession,'project_id'|'session_ref'>){
  if(!originalIndexId(session.project_id)||!nativeReference(session.session_ref)||session.session_ref.revision!==1)throw Error('ORIGINAL_INDEX_SCOPE_CHANGED');
  const raw=storage.getItem(indexKey(session.session_ref));
  if(raw===null)return {raw,rows:[] as OriginalOperationIndex[]};
  if(new TextEncoder().encode(raw).length>ORIGINAL_INDEX_BYTES)throw Error('ORIGINAL_INDEX_CAPACITY');
  let parsed:unknown;try{parsed=JSON.parse(raw);}catch{throw Error('ORIGINAL_INDEX_UNAVAILABLE');}
  const rows=(Array.isArray(parsed)?parsed:[parsed]) as OriginalOperationIndex[];
  if(rows.length>ORIGINAL_INDEX_ENTRIES)throw Error('ORIGINAL_INDEX_CAPACITY');
  // Keep all rows, including other saved references and unfamiliar metadata.
  // They cannot be silently filtered out by a subsequent write to this key.
  for(const row of rows)if(!row||typeof row!=='object'||Array.isArray(row)||!originalIndexId(row.project_id)||!nativeReference(row.session_ref)||row.session_ref.revision!==1||!originalIndexId(row.operation_id)||typeof row.input!=='string'||row.conversation_input!==undefined&&(!row.conversation_input||typeof row.conversation_input!=='object'||Array.isArray(row.conversation_input))||row.generation!==undefined&&(!Number.isSafeInteger(row.generation)||row.generation<1)||row.agent!==undefined&&!['plan','build','workflow'].includes(row.agent))throw Error('ORIGINAL_INDEX_UNAVAILABLE');
  return {raw,rows};
}
export function readOriginalOperations(storage:IndexStorage,session:Pick<AgentSession,'project_id'|'session_ref'>):OriginalOperationIndex[]{
  const {rows}=originalIndexRows(storage,session);
  return structuredClone(rows.filter(row=>row.project_id===session.project_id&&sameNativeReference(row.session_ref,session.session_ref)));
}
export function originalOperationPage(storage:IndexStorage,session:Pick<AgentSession,'project_id'|'session_ref'>,offset=0,limit=ORIGINAL_RECOVERY_PAGE):OriginalOperationPage{
  if(!Number.isSafeInteger(offset)||offset<0||offset>ORIGINAL_INDEX_ENTRIES||!Number.isSafeInteger(limit)||limit<1||limit>16)throw Error('ORIGINAL_INDEX_PAGE_CHANGED');
  const rows=readOriginalOperations(storage,session),at=Math.min(offset,Math.max(0,Math.floor((rows.length-1)/limit)*limit)),end=Math.max(0,rows.length-at),start=Math.max(0,end-limit);
  return {entries:rows.slice(start,end),offset:at,next_offset:start?at+(end-start):null,total:rows.length};
}
async function originalIndexLocked<T>(locks:OriginalOperationLocks|undefined,key:string,run:()=>Promise<T>){
  if(!locks)throw Error('ORIGINAL_INDEX_LOCK_UNAVAILABLE');
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
  try{return await locks.request('cirven-original-operation-index:'+key,{mode:'exclusive',signal:controller.signal},async()=>{clearTimeout(timer);return run();});}
  catch(error){if(controller.signal.aborted)throw Error('ORIGINAL_INDEX_LOCK_UNAVAILABLE');throw error;}
  finally{clearTimeout(timer);}
}
export async function dispatchOriginalMessage(client:Pick<AgentSessionsApi,'send'>,storage:IndexStorage,session:AgentSession,text:string,agent:OriginalAgent,key:string,input:OriginalConversationInput={},locks:OriginalOperationLocks|undefined=typeof navigator!=='undefined'?navigator.locks:undefined):Promise<AgentSessionOperation>{
  const selected=structuredClone(selectedOriginalInput(input)),owner={...session,session_ref:structuredClone(session.session_ref)};
  if(!originalIndexId(key)||!['plan','build','workflow'].includes(agent)||!Number.isSafeInteger(owner.generation)||owner.generation<1||!text.trim()||Array.from(text).length>16000)throw Error('ORIGINAL_INDEX_INPUT_CHANGED');
  const entry:OriginalOperationIndex={project_id:owner.project_id,session_ref:owner.session_ref,operation_id:key,input:text,generation:owner.generation,agent,...(Object.keys(selected).length?{conversation_input:selected}:{})};
  try{await originalIndexLocked(locks,indexKey(owner.session_ref),async()=>{
    const {raw,rows}=originalIndexRows(storage,owner),prior=rows.filter(row=>row.project_id===owner.project_id&&sameNativeReference(row.session_ref,owner.session_ref)&&row.operation_id===key);
    if(prior.length){if(prior.some(row=>row.input!==text||canonicalInput(row.conversation_input??{})!==canonicalInput(selected)||row.generation!==undefined&&row.generation!==owner.generation||row.agent!==undefined&&row.agent!==agent))throw Error('原编号已关联另一条输入');throw Error('ORIGINAL_INDEX_ORIGINAL_ONLY');}
    if(rows.length>=ORIGINAL_INDEX_ENTRIES)throw Error('ORIGINAL_INDEX_CAPACITY');
    const next=JSON.stringify([...rows,entry]);if(new TextEncoder().encode(next).length>ORIGINAL_INDEX_BYTES)throw Error('ORIGINAL_INDEX_CAPACITY');
    if(storage.getItem(indexKey(owner.session_ref))!==raw)throw Error('ORIGINAL_INDEX_CONFLICT');
    storage.setItem(indexKey(owner.session_ref),next);
    if(storage.getItem(indexKey(owner.session_ref))!==next)throw Error('ORIGINAL_INDEX_SAVE_UNCONFIRMED');
  });}catch(error){throw new AuthoredUIError('原会话请求索引未保存，尚未发送；原草稿保留：{detail}',{detail:authoredErrorCopy(error)});}
  return client.send(owner,text,agent,key,selected);
}
export async function inspectOriginalOperation(client:Pick<AgentSessionsApi,'operation'>,session:AgentSession,item:OriginalOperationIndex):Promise<OriginalOperationReview>{
  const original=structuredClone(item),owner={...session,session_ref:structuredClone(session.session_ref)};
  if(original.project_id!==owner.project_id||!sameNativeReference(original.session_ref,owner.session_ref))throw Error('ORIGINAL_INDEX_SCOPE_CHANGED');
  try{
    const receipt=assertOperation(await client.operation(owner,original.operation_id),original.operation_id),request=receipt.request as {session_ref?:NativeRef;generation?:number;raw_text?:string;agent?:OriginalAgent;conversation_ref?:NativeConversationRef;input_attachments?:NativeInputAttachment[];file_input?:OriginalFileInput;context_selection_id?:string;original_work_ref?:unknown}|undefined;
    if(!sameNativeReference(receipt.session_ref,original.session_ref)||!request||!sameNativeReference(request.session_ref,original.session_ref)||request.raw_text!==original.input||!Number.isSafeInteger(request.generation)||(request.generation??0)<1||original.generation!==undefined&&request.generation!==original.generation||!['MESSAGE','GUIDANCE'].includes(String(receipt.kind)))throw Error('ORIGINAL_INDEX_RECEIPT_CHANGED');
    if(original.agent&&receipt.kind==='MESSAGE'&&request.agent!==original.agent)throw Error('ORIGINAL_INDEX_RECEIPT_CHANGED');
    const input=original.conversation_input??{},local=receipt.role==='LOCAL_STATUS'&&receipt.model_calls===0&&receipt.native_post_count===0;
    for(const [saved,wire]of [[input.conversationRef,request.conversation_ref],[input.inputAttachments?.length?input.inputAttachments:undefined,request.input_attachments?.length?request.input_attachments:undefined],[input.fileInput,request.file_input],[input.originalWorkRef,request.original_work_ref],[local?undefined:input.contextSelectionId,local?undefined:request.context_selection_id]])if(canonicalInput(saved??null)!==canonicalInput(wire??null))throw Error('ORIGINAL_INDEX_RECEIPT_CHANGED');
    if(input.originalWorkRef)verifyOriginalWorkInputReceipt(receipt,{session_ref:original.session_ref,generation:request.generation!},input.originalWorkRef,original.input,originalWorkStatusText(original.input));
    return {...original,status:receipt.status,receipt,automatic_retry:false};
  }catch(error){const missing=error instanceof ApiError&&error.status===404;return {...original,status:missing?'NO_RECORD':'INSPECTION_UNAVAILABLE',message:(missing?'服务没有找到此操作回执；没有记录不证明未执行。':'原操作核对暂未完成。')+'保留原文字和编号，不能自动重发。',automatic_retry:false};}
}
export async function restoreOriginalOperations(client:Pick<AgentSessionsApi,'operation'>,storage:IndexStorage,session:AgentSession,page:{offset?:number;limit?:number}={}):Promise<OriginalOperationReview[]>{
  const owner={...session,session_ref:structuredClone(session.session_ref)},rows=originalOperationPage(storage,owner,page.offset??0,page.limit??ORIGINAL_RECOVERY_PAGE).entries,result:OriginalOperationReview[]=Array(rows.length);let cursor=0;
  // Each restoration reads one bounded page, at most four original GETs at once.
  await Promise.all(Array.from({length:Math.min(4,rows.length)},async()=>{while(cursor<rows.length){const i=cursor++;result[i]=await inspectOriginalOperation(client,owner,rows[i]);}}));return result;
}
export const agentSessionsApi = new AgentSessionsApi();
