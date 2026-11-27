import {quickReplyTarget,type QuickReplyTarget} from './quick-reply-navigation.ts';
import {reviewInput,sameReview,type NativeReviewInput,type NativeReviewBridge} from './native-review.ts';
import {AuthoredUIError,authoredErrorCopy} from './authored-ui-copy.ts';
import type { SourceRef } from './contracts';
import type {SupervisorConversationClientSnapshot,SupervisorConversationAsk,SupervisorConversationLocal} from './supervisor-conversation-client';
import type {SupervisorCenterReply} from './SupervisorCommandCenter';

export type NativeMode = 'companion' | 'workbench';
export type NativeRef = { id: string; revision: number; sha256: string };
export type FileLinkRef = NativeRef & {project_id:string};
export type FileAssociationScope={projectId?:string;workspaceId?:string;sessionId?:string;conversationId?:string};
export type LinkedFileSelection={scope:FileAssociationScope;fileLinkRef:FileLinkRef;filename?:string};
export type QuickFileContextState={scope:FileAssociationScope;selected:LinkedFileSelection|null;legacy_candidates:{fileLinkRef:FileLinkRef;filename?:string}[]};
export type NativeRegion = { x: number; y: number; width: number; height: number };
export type NativeConversationRef={id:string;revision:number};
export type NativeInputAttachment={kind:'CURRENT_PAGE'|'SCREENSHOT';context_ref:NativeRef;label:string;regions:NativeRegion[];image_sha256?:string;user_note?:string};
export type NativeInputThumbnail={context_ref:NativeRef;image_sha256:string;data_url:string;max_dimension:number;automatic_input_authorized:false};
export type NativeContextSnapshot = {
  operation_id: string;
  observation: {
    status: string; reason?: string; context_ref?: NativeRef; window_ref?: NativeRef;
    surface_ref?: NativeRef; input_epoch?: number;
    window?: { title?: string; window_class?: string; [key: string]: unknown };
    observations?: Record<string, unknown>; coverage?: Record<string, unknown>;
    questions?: unknown[]; automatic_input_authorized?: false;
    [key: string]: unknown;
  };
  current: boolean;
  preview?: { status: string; reason?: string; data_url?: string; image_ref?: Record<string, unknown> };
  automatic_input_authorized?: false;
  [key: string]: unknown;
};
export type NativeCollaborationState = {
  mode: NativeMode; fullscreen?: boolean; pinned?: boolean; pinned_preference?: boolean; platform?: string; status?: string; reason?: string;
  session_ref?: NativeRef; surface_ref?: NativeRef;
  input?: { epoch: number; enabled: boolean };
  capabilities?: { current_context: boolean; native_input: false; [key: string]: unknown };
  context_status?: { status?: string; configured?: boolean; reason?: string; [key: string]: unknown };
  context_capabilities?: Record<string, unknown>;
  context?: NativeContextSnapshot;
};
export type NativeMessageArtifact = {
  id?: string; kind?:string; project_id?:string; filename?: string; sha256?: string; download_url?:string; file_source_ref?: SourceRef;
  source_ref?: SourceRef; [key: string]: unknown;
};
export type NativeDocxRevisionChange={part:string;paragraph_path:string;char_start:number;char_end:number;quote:string;quote_sha256:string;replacement:string;reason:string};
export type NativeDocxRevisionArtifact=NativeMessageArtifact&{
  kind:'docx_revision';id:string;project_id:string;filename:string;sha256:string;download_url:string;
  owner:{operation_id:string;generation:string;conversation_id?:string|null};
  source_link_ref:FileLinkRef;snapshot_ref:FileLinkRef;source_sha256:string;proposal_ref:NativeRef;
  review:{changes:NativeDocxRevisionChange[];mode:'WORD_TRACKED_INSERTIONS_AND_DELETIONS';[key:string]:unknown};
  source_files_modified:false;changes_applied:false;later_source_edits_included:false;execution_effects:'CREATE_NEW_DERIVED_DOCX';
};
export type NativeExecutionObservation={
  stop?:{reason:'OWNER_DISCONNECTED_NO_POST_REPLAY'|'OWNER_LOST_NO_AUTOMATIC_REPLAY'|'MESSAGE_DEADLINE_ABORT_REQUESTED'|'ORIGINAL_OBSERVATION_UNAVAILABLE_NO_POST_REPLAY';owner_stop_status?:'EXPIRED';observed_at?:string};
  budget?:{scope:'SHARED_ABSOLUTE_TASK_DEADLINE';timeout_seconds:number;absolute_deadline:string;admitted_at?:string;dispatch_at?:string;operation_deadline?:string;startup_elapsed_seconds?:number;remaining_at_dispatch_seconds?:number};
};
export type NativeMessage = {
  operation_id: string; status: string; text?: string; summary?: string;
  conversation_id?:string|null;project_id?:string|null;file_link_ref?:FileLinkRef|null;context_selection_id?:string;
  context_summary?:unknown;native_context_issue?:import('./agent-sessions').ContextAuthorityIssue;native_context_recovery?:{parent_operation_id:string;expected_native_ref:NativeRef;record_current:boolean;read_only:true;model_calls:0};
  events?: unknown[]; artifacts?: NativeMessageArtifact[];
  artifact_status?:'PREPARING'|'NEW_ARTIFACT'|'PROPOSED_NOT_PUBLISHED'|'NEEDS_CLARIFICATION'|'CANCELLED_NOT_PUBLISHED'|'PUBLICATION_UNKNOWN'|'UNAVAILABLE';
  artifact_error?:{message:string};docx_revision_proposal_ref?:NativeRef;
  rework_previews?: unknown[];
  native_execution?:NativeExecutionObservation;
  file_source_ref?: SourceRef; file_review_status?: string;
  error?: { message: string; [key: string]: unknown }; reason?: string;
  execution?: { id?: string; session_id?: string | null; execution_status?: string };
  [key: string]: unknown;
};
export type QuickScope = import('./quick-inbox').QuickScope;
export type QuickRecord = {
  id:string; revision:number; sha256:string; scope:QuickScope; delivery:'DRAFT'|'SEND';
  comment:string; regions:NativeRegion[]; marks?:unknown[]; contextRef?:NativeRef;
  snapshot?:NativeContextSnapshot; operationId?:string; message?:NativeMessage;
  acknowledged?:unknown;
};
export type NativeCollaborationBridge = NativeReviewBridge & {
  version: 1 | 2;
  getCollaborationState: () => Promise<NativeCollaborationState>;
  captureCurrentContext: (options?: { intent?: string; taskRef?: NativeRef; need?: { metadata: boolean; accessibility: boolean; visual: boolean; ocr: boolean } }) => Promise<NativeContextSnapshot>;
  getConversationScope?:(conversationId:string)=>Promise<unknown>;
  getConversationHistory?:(body:{conversationId:string;limit?:number;cursor?:string})=>Promise<unknown>;
  getNativeInputPreview?: (body:{contextRef:NativeRef;imageSha256:string;view:'THUMBNAIL'|'FULL'})=>Promise<NativeInputThumbnail|NativeContextSnapshot>;
  setCollaborationMode: (mode: NativeMode) => Promise<NativeCollaborationState>;
  setCollaborationFullscreen?: (enabled: boolean) => Promise<NativeCollaborationState>;
  setCompanionPinned?: (enabled: boolean) => Promise<NativeCollaborationState>;
  getAssistantOrbState?: () => Promise<{ enabled: boolean; visible: boolean }>;
  setWorkflowOrbScope?: (scope:import('./stage-workflows').WorkflowOrbScope|null) => Promise<void>;
  onWorkflowConversationOpen?: (listener:(target:import('./stage-workflows').WorkflowOrbScope & {intent:'chat'|'annotate'})=>void)=>()=>void;
  setAssistantOrbEnabled?: (enabled: boolean) => Promise<{ enabled: boolean; visible: boolean }>;
  setQuickScope?: (scope:QuickScope) => Promise<QuickScope>;
  getQuickFileContext?:()=>Promise<QuickFileContextState>;
  clearQuickFileContext?:(body:{scope:FileAssociationScope;expectedRef:FileLinkRef})=>Promise<QuickFileContextState>;
  selectQuickFileContext?:(body:{scope:FileAssociationScope;fileLinkRef:FileLinkRef})=>Promise<QuickFileContextState>;
  getQuickDrafts?: () => Promise<{records:QuickRecord[]}>;
  ackQuickDraft?: (ref:NativeRef) => Promise<unknown>;
  submitQuickDraft?: (body:{ref:NativeRef;text?:string}) => Promise<NativeMessage>;
  openQuickDraft?: (ref:NativeRef) => Promise<unknown>;
  openNativeReplyLink?: (body:{operationId:string;text_sha256:string;url:string}) => Promise<unknown>;
  onQuickDraftsChanged?: (listener:()=>void) => () => void;
  onDraftFlushRequested?: (listener:()=>Promise<void>) => () => void;
  onAnnotationRequested?: (listener: () => void) => () => void;
  onCollaborationState?: (listener: (state: NativeCollaborationState) => void) => () => void;
  onQuickQuestion?: (listener: (target?:QuickReplyTarget) => void) => () => void;
  getTaskActivity?: () => Promise<{snapshot:unknown;readStatus:string}>;
  getSupervisorConversation?:()=>Promise<SupervisorConversationClientSnapshot>;
  getSupervisorHistoryPage?:(body:{cursor:string|null;limit:number})=>Promise<unknown>;
  getSupervisorOperation?:(body:{operation_id:string;request_sha256:string})=>Promise<unknown>;
  saveSupervisorDraft?:(body:{text:string;expectedRevision:number})=>Promise<SupervisorConversationClientSnapshot>;
  askSupervisor?:(body:SupervisorConversationAsk)=>Promise<SupervisorCenterReply>;
  inspectSupervisor?:(operationId:string)=>Promise<SupervisorCenterReply>;
  saveSupervisorLocalAnswer?:(body:SupervisorConversationLocal)=>Promise<SupervisorConversationClientSnapshot>;
  onSupervisorConversation?:(listener:(snapshot:unknown)=>void)=>()=>void;
  onSupervisorOpen?:(listener:()=>void)=>()=>void;
  onSupervisorConfigure?:(listener:()=>void)=>()=>void;
  getSupervisedTaskDetail?:(key:unknown)=>Promise<unknown>;
  setVisibleTask?: (key:unknown|null) => Promise<unknown>;
  onTaskActivity?: (listener:(snapshot:unknown)=>void) => () => void;
  onSupervisedTask?: (listener:(key:unknown)=>void) => () => void;
  onNativeContext: (listener: (snapshot: NativeContextSnapshot) => void) => () => void;
  submitNativeMessage?: (body: { operationId: string; rawText: string; contextRef?: NativeRef; providerProfileRef?: NativeRef; fileSourceRef?: SourceRef; fileLinkRef?:FileLinkRef;projectId?:string;conversationId?:string;nativeReview?:NativeReviewInput;inputAttachments?:NativeInputAttachment[];conversationRef?:NativeConversationRef;contextSelectionId?:string }) => Promise<NativeMessage>;
  prepareNativeContextRecovery?:(body:{parentOperationId:string;expectedNativeRef:NativeRef})=>Promise<unknown>;
  restartNativeContextRecovery?:(body:{parentOperationId:string;request:import('./native-context-recovery').NativeRecoveryRequest})=>Promise<unknown>;
  inspectNativeContextRecovery?:(body:{parentOperationId:string;operationId:string;requestSha256:string})=>Promise<unknown>;
  inspectNativeMessage?: (operationId: string) => Promise<NativeMessage>;
  getNativeConversation?: (target:{projectId:string|null;conversationId:string;operationId:string})=>Promise<unknown>;
  getNativeConversations?: (scope:{projectId:string|null})=>Promise<unknown>;
  cancelNativeMessage?: (operationId: string) => Promise<NativeMessage>;
  openSubscriptionLogin?: (attemptId: string) => Promise<{ status: string; attempt_id: string; authentication_verified: false }>;
  openDocument?: (projectId: string, source: SourceRef, options?:{scope?:FileAssociationScope;deliveryOperationId?:string}) => Promise<unknown>;
  openLinkedFile?:(fileLinkRef:FileLinkRef,scope:FileAssociationScope)=>Promise<unknown>;
  openContextFile?:(source:import('./context-graph').ContextSource,scope:FileAssociationScope)=>Promise<unknown>;
  openReportArtifact?: (projectId: string, artifactId: string, sha256: string, downloadUrl?: string,options?:{scope?:FileAssociationScope}) => Promise<unknown>;
};

export function nativeCollaborationBridge(host: unknown): NativeCollaborationBridge | undefined {
  const value = (host as { eeasDesktop?: Partial<NativeCollaborationBridge> } | undefined)?.eeasDesktop;
  return (value?.version === 1 || value?.version === 2) && ['getCollaborationState', 'captureCurrentContext', 'setCollaborationMode', 'onNativeContext'].every(key => typeof value[key as keyof NativeCollaborationBridge] === 'function') ? value as NativeCollaborationBridge : undefined;
}
export function nativeSnapshot(value: unknown): NativeContextSnapshot {
  const snapshot = value as NativeContextSnapshot | undefined;
  if (snapshot?.observation && typeof snapshot.observation.status === 'string' && typeof snapshot.operation_id === 'string') return snapshot;
  const failure = value as { status?: string; error?: { message?: string }; reason?: string } | undefined;
  return { operation_id: '', current: false, automatic_input_authorized: false, observation: { status: failure?.status ?? 'UNAVAILABLE', reason: failure?.error?.message ?? failure?.reason ?? '原生对象回执不完整，请重新引用' } };
}
export function nativeInitialMode(host: unknown, search = ''): NativeMode {
  const requested = new URLSearchParams(search).get('mode');
  if (requested === 'companion' || requested === 'workbench') return requested;
  return 'workbench';
}
export function nativeReference(value: unknown): value is NativeRef {
  const ref = value as Partial<NativeRef> | undefined;
  return !!ref && typeof ref.id === 'string' && !!ref.id && Number.isSafeInteger(ref.revision) && ref.revision! > 0 && typeof ref.sha256 === 'string' && /^[a-f0-9]{64}$/.test(ref.sha256);
}
export function currentNativeReference(snapshot?: NativeContextSnapshot): NativeRef | undefined {
  const ref = snapshot?.observation?.context_ref;
  return snapshot?.current === true && nativeReference(ref) ? ref : undefined;
}
/** An image existing in the preview does not prove that its pixels came from
 * the selected window. Keep the observation's clarification out of normal
 * attachments; the caller retains the original message and all prior inputs. */
export function nativeCapturedInput(snapshot:NativeContextSnapshot,visual:boolean):NativeDraftAttachment {
  const ref=currentNativeReference(snapshot),observation=snapshot.observation;
  const unavailable=()=>new AuthoredUIError(visual
    ?'暂时无法取得来源明确的截图。原消息和附件已保留，可先使用“引用当前”。'
    :'暂时无法读取当前页面。请先切换到要引用的窗口，原消息和附件已保留。');
  if(!ref||!['READY_OBSERVATION','OBSERVED','PARTIAL'].includes(observation.status)||observation.questions?.length)throw unavailable();
  const imageSha=snapshot.preview?.image_ref?.sha256;
  if(visual&&(snapshot.preview?.status!=='AVAILABLE'||typeof imageSha!=='string'||!/^[a-f0-9]{64}$/.test(imageSha)
    ||observation.coverage?.visual!==true||!['EXACT_TARGET_WINDOW','VERIFIED_TARGET_PIXELS'].includes(String(observation.coverage?.pixel_source))))throw unavailable();
  if(!visual&&observation.coverage?.accessibility!==true)throw unavailable();
  return {context_ref:ref,label:nativeContextLabel(snapshot),comment:'',regions:[],...(visual?{preview_sha256:imageSha as string}:{})};
}
export function sameNativeReference(a?: NativeRef, b?: NativeRef): boolean { return !!a && !!b && a.id===b.id && a.revision===b.revision && a.sha256===b.sha256; }
export function nativeObservationScope(state?: NativeCollaborationState, snapshot?: NativeContextSnapshot,translate:(message:string)=>string=message=>message): string {
  const service = state?.context_status?.configured === true ? '观察服务已配置' : state?.context_status?.configured === false ? '观察服务未配置' : '观察服务状态待核实';
  const partial = snapshot?.observation.status === 'PARTIAL' ? ' · 当前仅部分观察' : '';
  return `${translate(service)}${translate(partial)}${translate(' · 只读观察，原生输入未启用')}`;
}
export function nativePreview(snapshot?: NativeContextSnapshot): string | undefined {
  const preview = snapshot?.preview;
  return preview?.status === 'AVAILABLE' && typeof preview.data_url === 'string' && /^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(preview.data_url) ? preview.data_url : undefined;
}
const nativeContextReasonLabels:Readonly<Record<string,string>>={
  WINDOW_NOT_OBSERVABLE:'请打开要查看的页面',
  NO_RECENT_WORKSPACE_WINDOW:'先切换到要查看的页面',
  RECENT_WINDOW_LOST:'请重新打开要查看的页面',
  WINDOW_LOST_DURING_OBSERVATION:'请重新打开要查看的页面',
  WINDOW_LIFETIME_CHANGED:'页面已变化，请重新引用',
  WINDOW_CHANGED_DURING_OBSERVATION:'页面已变化，请重新引用',
  WORKSPACE_SESSION_CHANGED:'工作区已变化，请重新引用',
  CURRENT_SCOPE_CHANGED:'工作区已变化，请重新引用',
  WINDOW_SCOPE_AMBIGUOUS:'请选择要查看的窗口',
};
export function nativeContextLabel(snapshot?: NativeContextSnapshot,translate:(message:string)=>string=message=>message): string {
  // A real window title is user/source content. Failure codes stay unchanged in
  // the observation/diagnostics; only this compact UI summary is authored copy.
  const observation=snapshot?.observation;
  const reason=observation?.reason??'';
  const label=Object.hasOwn(nativeContextReasonLabels,reason)?nativeContextReasonLabels[reason]:'当前对象尚未取得';
  return observation?.window?.title || translate(label);
}
export const nativeMessagePending = (value?: NativeMessage) => !!value && ['PROCESSING', 'REQUESTING', 'RUNNING', 'CANCEL_REQUESTED'].includes(value.status);
export function assertNativeMessage(value: NativeMessage, operationId: string): NativeMessage {
  if (!value || value.operation_id !== operationId || typeof value.status !== 'string') throw new Error('原生会话回执不属于本次操作');
  return value;
}

export class NativeMessageClient {
  private bridge: NativeCollaborationBridge;
  constructor(bridge: NativeCollaborationBridge) { this.bridge=bridge; }
  async send(body: Parameters<NonNullable<NativeCollaborationBridge['submitNativeMessage']>>[0]) {
    if (!this.bridge.submitNativeMessage) throw new Error('原生会话尚未连接');
    // Exactly one dispatch. A transport error is an unknown outcome, never a retry.
    const value=assertNativeMessage(await this.bridge.submitNativeMessage(body),body.operationId);
    if(body.contextSelectionId&&value.dispatch_state!=='NOT_DISPATCHED'&&value.context_selection_id!==body.contextSelectionId)throw new Error('资料与记忆选择尚未获原服务确认，原操作和输入保留，请核查。');
    return value;
  }
  async inspect(operationId: string) {
    if (!this.bridge.inspectNativeMessage) throw new Error('原生会话观测尚未连接');
    const value = assertNativeMessage(await this.bridge.inspectNativeMessage(operationId),operationId);
    return ['NO_RECORD', 'NOT_FOUND'].includes(value.status) ? { ...value, text: '尚未查到这次操作的服务记录。没有记录不证明未执行；原问题与操作编号保留，请核查，不能自动重发。' } : value;
  }
  async cancel(operationId: string) {
    if (!this.bridge.cancelNativeMessage) throw new Error('原生会话取消接口尚未连接');
    return assertNativeMessage(await this.bridge.cancelNativeMessage(operationId),operationId);
  }
}

export function draftContextReference(attachments: NativeDraftAttachment[], current?: NativeContextSnapshot): NativeRef | undefined {
  const ref = currentNativeReference(current);
  if (attachments.some(item => !ref || item.context_ref.id !== ref.id || item.context_ref.revision !== ref.revision || item.context_ref.sha256 !== ref.sha256)) throw new Error('草稿引用的对象版本已变化；请核对旧反馈并在原生页面重新定位。');
  return attachments[0]?.context_ref ?? ref;
}

export async function confirmedCollaborationFullscreen(bridge: NativeCollaborationBridge, enabled: boolean) {
  if (!bridge.setCollaborationFullscreen) throw new Error('当前宿主尚未接入 App 全屏');
  const state = await bridge.setCollaborationFullscreen(enabled);
  if (state.fullscreen !== enabled) throw new Error('宿主尚未确认 App 全屏状态');
  return state;
}

/** Ordinary questions request a fresh Main observation; only explicit annotations freeze a ref. */
export function nativeMessageInput(operationId: string, rawText: string, attachments: NativeDraftAttachment[], current?: NativeContextSnapshot, providerProfileRef?: NativeRef, fileSourceRef?: SourceRef, conversationId?:string,fileLinkRef?:FileLinkRef,nativeReview?:NativeReviewInput|null,conversationRef?:NativeConversationRef,contextSelectionId?:string) {
  if(conversationRef&&(!/^(native|original|discovery):[a-f0-9]{64}$/.test(conversationRef.id)||!Number.isSafeInteger(conversationRef.revision)||conversationRef.revision<1))throw new Error('会话归属版本不可用，当前草稿保留，请刷新会话。');
  if(contextSelectionId!==undefined&&!/^[a-f0-9]{64}$/.test(contextSelectionId))throw new Error('资料与记忆选择引用不可用，当前草稿保留。');
  if(fileLinkRef&&fileSourceRef)throw new Error('请明确一个文件依据，已有文字与附件保留。');
  const review=reviewInput(nativeReview);if(review?.version===1&&(fileSourceRef||fileLinkRef))throw new Error('当前页检查不能混用保存稿输入');
  const inputAttachments=nativeInputAttachments(attachments);
  const first=inputAttachments[0],contextRef=first&&(review||inputAttachments.length===1&&first.kind==='CURRENT_PAGE'&&sameNativeReference(first.context_ref,currentNativeReference(current)))?first.context_ref:undefined;
  const text=rawText.trim()?rawText:inputAttachments.length?'请查看所附内容。':rawText;
  if(Array.from(text).length>16000)throw new Error('本条消息超过16000字符，草稿保留；请缩小本轮问题或批注范围。');
  return { operationId, rawText: text, ...(review?{nativeReview:review}:{}), ...(fileSourceRef?{fileSourceRef}:{ }),...(fileLinkRef?{fileLinkRef,projectId:fileLinkRef.project_id}:{}),
    ...(inputAttachments.length?{inputAttachments}:{}),...(conversationRef?{conversationRef:{...conversationRef}}:{}),
    ...(contextSelectionId?{contextSelectionId}:{}),...(contextRef ? { contextRef } : {}), ...(providerProfileRef ? { providerProfileRef } : {}), ...(conversationId?{conversationId}:{}) };
}

export type NativeFeedbackDisplayIdentity={purpose:'FILE_REWORK_PREVIEW';project_id:string;conversation_id:string;operation_id:string;raw_sha256:string}|{purpose:'UNVERIFIED_FILE_REWORK_PREVIEW'};
export type NativeHistoryEntry = { input: string; operation_id: string; request?:ReturnType<typeof nativeMessageInput>;feedback_display_identity?:NativeFeedbackDisplayIdentity };
function historyDisplay(value:unknown,operation:string,conversation?:string):NativeFeedbackDisplayIdentity{const v=value as Record<string,unknown>|undefined;return v?.purpose==='FILE_REWORK_PREVIEW'&&['project_id','conversation_id','operation_id'].every(k=>typeof v[k]==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v[k] as string))&&v.operation_id===operation&&(!conversation||v.conversation_id===conversation)&&typeof v.raw_sha256==='string'&&/^[a-f0-9]{64}$/.test(v.raw_sha256)?v as NativeFeedbackDisplayIdentity:{purpose:'UNVERIFIED_FILE_REWORK_PREVIEW'};}
const nativeHistoryKey = 'eeas-native-message-history';
const conversationHistoryKey=(conversationId?:string)=>conversationId?`${nativeHistoryKey}:${conversationId}`:nativeHistoryKey;
export function readNativeHistory(storage: Storage,conversationId?:string): NativeHistoryEntry[] {
  try {
    const value: unknown = JSON.parse(storage.getItem(conversationHistoryKey(conversationId)) ?? '[]');
    return Array.isArray(value) ? value.filter((item): item is NativeHistoryEntry =>
      !!item && typeof item.input === 'string' && typeof item.operation_id === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(item.operation_id)).slice(-30).map(item=>Object.prototype.hasOwnProperty.call(item,'feedback_display_identity')?{...item,feedback_display_identity:historyDisplay(item.feedback_display_identity,item.operation_id,conversationId)}:item) : [];
  } catch { return []; }
}
export class NativeRequestIndexError extends AuthoredUIError {}
export function saveNativeHistory(storage: Storage, incoming: NativeHistoryEntry[],conversationId?:string) {
  let entries = readNativeHistory(storage,conversationId);
  for (const item of incoming) {
    const prior = entries.find(entry => entry.operation_id === item.operation_id);
    if (prior && prior.input !== item.input) throw new Error('本机操作编号已关联另一条问题');
    if(prior?.request&&item.request&&!sameReview(prior.request,item.request))throw new Error('原操作输入已固定');
    if(item.feedback_display_identity&&historyDisplay(item.feedback_display_identity,item.operation_id,conversationId).purpose==='UNVERIFIED_FILE_REWORK_PREVIEW')throw new Error('反馈展示身份尚未核对');
    if(prior?.feedback_display_identity&&item.feedback_display_identity&&!sameReview(prior.feedback_display_identity,item.feedback_display_identity))throw new Error('反馈展示身份已绑定原操作');
    entries = [...entries.filter(entry => entry.operation_id !== item.operation_id), {...item,...(prior?.request&&!item.request?{request:prior.request}:{}),...(prior?.feedback_display_identity&&!item.feedback_display_identity?{feedback_display_identity:prior.feedback_display_identity}:{})}].slice(-30);
  }
  storage.setItem(conversationHistoryKey(conversationId), JSON.stringify(entries));
}
export async function dispatchNativeMessage(client: NativeMessageClient, storage: Storage, body: ReturnType<typeof nativeMessageInput>) {
  // Save the exact question/op before IPC. A local entry is an index, never proof of execution.
  try {
    saveNativeHistory(storage, [{ input: body.rawText, operation_id: body.operationId,...(body.nativeReview||body.inputAttachments?.length||body.conversationRef||body.contextSelectionId?{request:body}:{}) }],body.conversationId);
    if (!readNativeHistory(storage,body.conversationId).some(item => item.operation_id === body.operationId && item.input === body.rawText)) throw new Error('本机操作索引未能读取核对');
  } catch (error) {
    throw new NativeRequestIndexError('问题索引未能保存，尚未发送；草稿保留：{error}',{error:authoredErrorCopy(error)});
  }
  return client.send(body);
}
export function nativeInspectionFailure(operationId: string, error: unknown): NativeMessage {
  const value = error as { status?: number; code?: string; message?: string } | undefined;
  const missing = value?.status === 404 || value?.code === 'NOT_FOUND';
  return { operation_id: operationId, status: missing ? 'NO_RECORD' : 'INSPECTION_UNAVAILABLE',
    text: missing ? '尚未查到这次操作的服务记录。没有记录不证明未执行；原问题与操作编号保留，请核查，不能自动重发。' : '这次操作的实际结果暂未能核查；原问题与操作编号保留，请检查原执行，不能自动重发。',
    error: { message: value?.message ?? String(error) }, automatic_retry: false, execution_outcome: 'NOT_PROVEN' };
}
export async function restoreNativeHistory(client: NativeMessageClient, entries: NativeHistoryEntry[]) {
  return Promise.all(entries.map(async entry => {
    try { return { input: entry.input, message: await client.inspect(entry.operation_id),...(entry.feedback_display_identity?{feedback_display_identity:entry.feedback_display_identity}:{}) }; }
    catch (error) { return { input: entry.input, message: nativeInspectionFailure(entry.operation_id, error),...(entry.feedback_display_identity?{feedback_display_identity:entry.feedback_display_identity}:{}) }; }
  }));
}

/** Epochs belong to main. This controller never sends geometry or native input. */
export class NativeCollaborationController {
  private generation = 0; private contextGeneration = 0; private stateGeneration = 0; private stopped = false;
  private release?: () => void; private releaseState?: () => void; private releaseQuick?: () => void;
  private bridge: NativeCollaborationBridge;
  private state: (state: NativeCollaborationState) => void;
  private context: (snapshot: NativeContextSnapshot) => void;
  private quickQuestion?: (target?:QuickReplyTarget) => void;
  constructor(bridge: NativeCollaborationBridge, state: (state: NativeCollaborationState) => void, context: (snapshot: NativeContextSnapshot) => void, quickQuestion?: (target?:QuickReplyTarget) => void) { this.bridge=bridge;this.state=state;this.context=context;this.quickQuestion=quickQuestion; }
  async start() {
    const generation = ++this.generation, contextGeneration = this.contextGeneration, stateGeneration = this.stateGeneration;
    this.release = this.bridge.onNativeContext(snapshot => { if (!this.stopped) { this.contextGeneration++; this.context(nativeSnapshot(snapshot)); } });
    this.releaseState = this.bridge.onCollaborationState?.(state => { if (!this.stopped) { this.stateGeneration++; this.state(state); } });
    this.releaseQuick = this.bridge.onQuickQuestion?.(raw => { if(this.stopped)return;if(raw===undefined)this.quickQuestion?.();else{const target=quickReplyTarget(raw);if(target)this.quickQuestion?.(target);} });
    const value = await this.bridge.getCollaborationState();
    if (this.stopped || generation !== this.generation) return;
    if (stateGeneration === this.stateGeneration) this.state(value);
    if (value.context && contextGeneration === this.contextGeneration) this.context(nativeSnapshot(value.context));
  }
  async mode(mode: NativeMode) {
    const generation = ++this.generation, stateGeneration = this.stateGeneration, value = await this.bridge.setCollaborationMode(mode);
    if (this.stopped || generation !== this.generation) return;
    if (value.mode !== mode) throw new Error('宿主未确认协作窗口切换，请重试');
    if (stateGeneration === this.stateGeneration) this.state(value);
  }
  async pin(enabled: boolean) {
    if (!this.bridge.setCompanionPinned) throw new Error('此宿主尚未提供固定轻量窗口');
    const generation = ++this.generation, stateGeneration = this.stateGeneration, value = await this.bridge.setCompanionPinned(enabled);
    if (this.stopped || generation !== this.generation) return;
    if (value.mode !== 'companion' || value.pinned !== enabled) throw new Error('宿主未确认轻量窗口固定状态');
    if (stateGeneration === this.stateGeneration) this.state(value);
  }
  async capture(options?: Parameters<NativeCollaborationBridge['captureCurrentContext']>[0],accept:()=>boolean=()=>true) {
    const generation = ++this.contextGeneration, value = await this.bridge.captureCurrentContext(options);
    if (!this.stopped && generation === this.contextGeneration&&accept()) this.context(nativeSnapshot(value));
    return value;
  }
  stop() { this.stopped = true; this.generation++; this.contextGeneration++; this.stateGeneration++; this.release?.(); this.releaseState?.(); this.releaseQuick?.(); this.release = undefined; this.releaseState = undefined; this.releaseQuick = undefined; }
}

export type NativeDraftAttachment = {
  context_ref: NativeRef; label: string; comment: string; regions: NativeRegion[];
  preview_sha256?: string; feedback_ref?: NativeRef;
};
export function nativeInputAttachments(attachments:readonly NativeDraftAttachment[]):NativeInputAttachment[]{
 if(attachments.length>20)throw new Error('本轮最多20个输入，请先整理当前附件。');
 const result=attachments.map(item=>{
  if(!nativeReference(item.context_ref)||typeof item.label!=='string'||!item.label.trim()||Array.from(item.label).length>500||typeof item.comment!=='string'||Array.from(item.comment).length>16000||!Array.isArray(item.regions)||item.regions.length>32||!item.regions.every(validNativeRegion))throw new Error('附件引用或圈选不完整，原草稿保留，请核对该附件。');
  const screenshot=item.preview_sha256!==undefined;if(screenshot&&!/^[a-f0-9]{64}$/.test(item.preview_sha256!)||!screenshot&&item.regions.length)throw new Error('圈选需要对应截图的准确依据，请核对附件。');
  return {kind:screenshot?'SCREENSHOT' as const:'CURRENT_PAGE' as const,context_ref:{...item.context_ref},label:item.label,regions:item.regions.map(({x,y,width,height})=>({x,y,width,height})),...(screenshot?{image_sha256:item.preview_sha256}:{}),...(item.comment.trim()?{user_note:item.comment}:{})};
 });
 if(new TextEncoder().encode(JSON.stringify(result)).length>96*1024)throw new Error('附件说明超过本轮输入大小，请缩小本轮范围，原稿保留。');
 return result;
}
type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem'>;
export const nativeDraftKey = (projectId?: string) => `eeas-native-draft:${projectId ?? 'unbound'}`;
export const nativeTextKey = (projectId?: string) => `eeas-native-text:${projectId ?? 'unbound'}`;
export function readNativeText(storage: Storage, projectId?: string): string { return storage.getItem(nativeTextKey(projectId)) ?? ''; }
export function saveNativeText(storage: Storage, projectId: string | undefined, text: string) { storage.setItem(nativeTextKey(projectId),text); }
export function readNativeDraft(storage: Storage, projectId?: string): NativeDraftAttachment[] {
  try {
    const value: unknown = JSON.parse(storage.getItem(nativeDraftKey(projectId)) ?? '[]');
    return Array.isArray(value) ? value.filter((item): item is NativeDraftAttachment => !!item && nativeReference(item.context_ref) && typeof item.label === 'string' && typeof item.comment === 'string' && Array.isArray(item.regions) && item.regions.every(validNativeRegion)).slice(-20) : [];
  } catch { return []; }
}
export function saveNativeDraft(storage: Storage, projectId: string | undefined, attachments: NativeDraftAttachment[]) {
  storage.setItem(nativeDraftKey(projectId), JSON.stringify(attachments.slice(-20)));
}
export function validNativeRegion(value: unknown): value is NativeRegion {
  const region = value as NativeRegion | undefined;
  return !!region && [region.x, region.y, region.width, region.height].every(Number.isFinite) && region.x >= 0 && region.y >= 0 && region.width > 0 && region.height > 0 && region.x + region.width <= 1.000001 && region.y + region.height <= 1.000001;
}
export function nativeRegion(start: { x: number; y: number }, end: { x: number; y: number }, bounds: { left: number; top: number; width: number; height: number }): NativeRegion | undefined {
  if (![start.x,start.y,end.x,end.y,bounds.left,bounds.top,bounds.width,bounds.height].every(Number.isFinite) || bounds.width <= 0 || bounds.height <= 0) return;
  const x = (client: number) => Math.max(0, Math.min(1, (client - bounds.left) / bounds.width)), y = (client: number) => Math.max(0, Math.min(1, (client - bounds.top) / bounds.height));
  const region = { x: Math.min(x(start.x),x(end.x)), y: Math.min(y(start.y),y(end.y)), width: Math.abs(x(start.x)-x(end.x)), height: Math.abs(y(start.y)-y(end.y)) };
  return validNativeRegion(region) && region.width * bounds.width >= 3 && region.height * bounds.height >= 3 ? region : undefined;
}
export function nativeDraftInstruction(text: string, attachments: NativeDraftAttachment[]): string {
  return [text, ...attachments.map((item, index) => `原生反馈 ${index + 1}：${item.comment}\n对象：${item.label}\n依据：${item.context_ref.id} / v${item.context_ref.revision} / ${item.context_ref.sha256}${item.regions.length ? `\n用户框选区域（相对已冻结帧的坐标，非自动识别对象）：${JSON.stringify(item.regions)}\n截图依据：${item.preview_sha256 ?? '未取得'}` : ''}`)].filter(Boolean).join('\n\n');
}
