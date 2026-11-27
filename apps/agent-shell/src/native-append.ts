import {authoredCopy,authoredErrorCopy,type AuthoredUICopy} from './authored-ui-copy.ts';
import { nativeReference, sameNativeReference, type NativeRef } from './native-collaboration.ts';

export const NATIVE_APPEND_HISTORY_KEY = 'eeas-native-append-history';
export const NATIVE_APPEND_SCOPE = 'CLASSIC_NOTEPAD_APPEND_AT_UTF16_END_SINGLE_TYPE_FOREGROUND';
export const NATIVE_APPEND_PAYLOAD_LIMIT = 256;
export type AppendSpan = { start:number; end:number; quote:string };
export type AppendPayloadSource = { kind:'RAW_INSTRUCTION'; span:AppendSpan; reply_ref?:null } | { kind:'ORIGINAL_REPLY'; span:AppendSpan; reply_ref:NativeRef };
export type AppendPrepare = { operation_id:string; context_ref:NativeRef; raw_text:string; payload_source:AppendPayloadSource; provider_profile_ref?:NativeRef|null };
export type AppendVerification = { status:string; reason?:string; evidence_refs?:NativeRef[] };
export type NativeAppendState = {
  adapter_id:'classic_notepad_append'; state:string; automatic_retry:false; reason?:string|null;
  scope?:string; permission_state?:string; activation_state?:string; foreground_required?:boolean;
  no_selection_overwrite?:boolean; no_save_action?:boolean; native_input_global_enabled?:boolean;
  input_epoch?:number; expected_intent_epoch?:number; target_label?:string; provider_state?:string;
  task_operation_id?:string; task_status?:string; task_completed?:boolean;
  preview_ref?:NativeRef; approval_ref?:NativeRef|null; qualification_ref?:NativeRef; baseline_ref?:NativeRef;
  payload_text?:string; payload_sha256?:string;
  task?:{ task_outcome?:{ task_outcome?:{ verification?:AppendVerification; verification_ref?:NativeRef|null; task_completed?:boolean } }; [key:string]:unknown };
  [key:string]:unknown;
};
export type NativeAppendRevocation = {
  status:string; intent_epoch?:number; owner?:string; mutation_authority?:boolean;
  cancellation?:{ confirmed?:boolean; status?:string; [key:string]:unknown };
  foreground_handoff_stop?:{ status:'OWN_HELPER_STOP_REQUESTED'|'NO_OWN_HELPER_PENDING'; owned_helpers:number; foreground_effect:'UNKNOWN'|'NOT_OBSERVED' };
  readonly_intent_cancellation_status?:string; [key:string]:unknown;
};
export function appendTakeoverNotice(receipt:NativeAppendRevocation,translate:(message:string)=>string=message=>message){
  const sdk=translate(receipt.cancellation?.confirmed===true?'原 SDK 任务取消已确认。':'原 SDK 任务是否停止仍需核查。');
  const interpretation=receipt.readonly_intent_cancellation_status==='UNKNOWN_OUTCOME'?translate('原解释任务的取消结果仍需核查。'):'';
  return receipt.foreground_handoff_stop?.foreground_effect==='UNKNOWN'
    ?`${translate('已请求停止自有助手；焦点交接是否发生仍需核查，未自动继续追加。')}${sdk}${interpretation}`
    :`${translate('已收到本次追加权限的撤销回执，未自动继续追加。')}${sdk}${interpretation}`;
}
/** Only Main can obtain gesture/epoch/target tickets. There is no renderer input primitive. */
export type NativeAppendBridge = {
  getNativeAppend:(contextRef?:NativeRef,operationId?:string)=>Promise<unknown>;
  prepareNativeAppend:(body:AppendPrepare)=>Promise<unknown>;
  approveNativeAppend:(body:{operationId:string;previewRef:NativeRef})=>Promise<unknown>;
  startNativeAppend:(body:{operationId:string;previewRef:NativeRef;approvalRef:NativeRef})=>Promise<unknown>;
  revokeNativeAppend:(body:{operationId:string;expectedIntentEpoch:number})=>Promise<unknown>;
};
export type AppendStorage = Pick<Storage,'getItem'|'setItem'>;
export type AppendAction = 'PREPARE'|'APPROVE'|'START';
export type AppendOperation = {
  kind:'APPEND'; operationId:string; contextRef:NativeRef; rawTextSha256:string; payloadSha256:string;
  sourceKind:AppendPayloadSource['kind']; actions:AppendAction[]; previewRef?:NativeRef; approvalRef?:NativeRef;
};
export type AppendRevokeOperation = { kind:'REVOKE'; operationId:string; expectedIntentEpoch:number };
export type AppendHistoryEntry = AppendOperation|AppendRevokeOperation;
export type AppendView = {
  operation:AppendOperation; origin:'LIVE'|'RECOVERED'; snapshot?:NativeAppendState;
  review:'OBSERVED'|'NO_RECORD'|'INSPECTION_UNAVAILABLE'; message?:string;message_copy?:AuthoredUICopy;
};

const validId=(value:unknown):value is string=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
const validSha=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const positiveInteger=(value:unknown):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>0;
const newId=()=> 'native-append-'+crypto.randomUUID().replaceAll('-','');
const copyRef=(value:NativeRef):NativeRef=>({id:value.id,revision:value.revision,sha256:value.sha256});
function assertUnicode(value:string){
  for(let i=0;i<value.length;i++){
    const code=value.charCodeAt(i);
    if(code>=0xd800&&code<=0xdbff){const next=value.charCodeAt(++i);if(!(next>=0xdc00&&next<=0xdfff))throw new Error('原话包含不完整 Unicode 字符，请重新输入');}
    else if(code>=0xdc00&&code<=0xdfff)throw new Error('原话包含不完整 Unicode 字符，请重新输入');
  }
}
export function appendSelection(rawText:string,selectionStart:number,selectionEnd:number):AppendSpan {
  assertUnicode(rawText);
  if(!Number.isSafeInteger(selectionStart)||!Number.isSafeInteger(selectionEnd)||selectionStart<0||selectionEnd<=selectionStart||selectionEnd>rawText.length)throw new Error('请在用户原话中选中要追加的文字');
  const boundary=(offset:number)=>!(offset>0&&offset<rawText.length&&rawText.charCodeAt(offset-1)>=0xd800&&rawText.charCodeAt(offset-1)<=0xdbff&&rawText.charCodeAt(offset)>=0xdc00&&rawText.charCodeAt(offset)<=0xdfff);
  if(!boundary(selectionStart)||!boundary(selectionEnd))throw new Error('选文不能截断 Unicode 字符');
  const quote=rawText.slice(selectionStart,selectionEnd);
  if(quote.length>NATIVE_APPEND_PAYLOAD_LIMIT)throw new Error('单次追加最多 256 个 UTF-16 单元；请缩小选文');
  if(/[\u0000-\u001f\u007f]/u.test(quote))throw new Error('本轮追加不支持换行、制表符或控制字符');
  return {start:Array.from(rawText.slice(0,selectionStart)).length,end:Array.from(rawText.slice(0,selectionEnd)).length,quote};
}
export function appendPrepare(contextRef:NativeRef,rawText:string,span:AppendSpan,providerProfileRef?:NativeRef,key=newId()):AppendPrepare {
  if(!validId(key)||!nativeReference(contextRef)||!validId(contextRef.id)||providerProfileRef&&(!nativeReference(providerProfileRef)||!validId(providerProfileRef.id)))throw new Error('追加操作或来源引用不完整');
  assertUnicode(rawText);const points=Array.from(rawText);
  if(!rawText||points.length>16000)throw new Error('用户原话应为 1–16000 个字符；草稿保留，未准备追加');
  if(!Number.isSafeInteger(span.start)||!Number.isSafeInteger(span.end)||span.start<0||span.end<=span.start||span.end>points.length||points.slice(span.start,span.end).join('')!==span.quote)throw new Error('选文与当前用户原话不一致，请重新选择');
  appendSelection(span.quote,0,span.quote.length);
  return {operation_id:key,context_ref:copyRef(contextRef),raw_text:rawText,payload_source:{kind:'RAW_INSTRUCTION',span:{start:span.start,end:span.end,quote:span.quote},reply_ref:null},provider_profile_ref:providerProfileRef?copyRef(providerProfileRef):null};
}
export function appendActionRequest(value:unknown):AppendPrepare {
  const body=value as AppendPrepare,source=body?.payload_source;
  const exact=(input:unknown,keys:string[])=>!!input&&typeof input==='object'&&!Array.isArray(input)&&Object.keys(input).sort().join(',')===keys.sort().join(',');
  if(!exact(body,['operation_id','context_ref','raw_text','payload_source',...Object.hasOwn(body??{},'provider_profile_ref')?['provider_profile_ref']:[]])||
    !source||source.kind!=='RAW_INSTRUCTION'||!exact(source,['kind','span',...Object.hasOwn(source,'reply_ref')?['reply_ref']:[]])||source.reply_ref!=null||
    !exact(source.span,['start','end','quote'])||typeof body.raw_text!=='string')throw new Error('追加请求必须来自原始指令与准确选文');
  return appendPrepare(body.context_ref,body.raw_text,source.span,body.provider_profile_ref??undefined,body.operation_id);
}
export function appendChatRequest(source:{operationId:string;rawText:string;contextRef:NativeRef;providerProfileRef?:NativeRef},span:AppendSpan):AppendPrepare {
  return appendPrepare(source.contextRef,source.rawText,span,source.providerProfileRef,source.operationId);
}
async function textSha(value:string){const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));return Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');}

function assertEntry(value:unknown):AppendHistoryEntry {
  const entry=value as AppendHistoryEntry;
  if(!entry||!validId(entry.operationId))throw new Error('追加历史的操作身份不完整');
  if(entry.kind==='REVOKE'&&positiveInteger(entry.expectedIntentEpoch))return {kind:'REVOKE',operationId:entry.operationId,expectedIntentEpoch:entry.expectedIntentEpoch};
  if(entry.kind!=='APPEND'||!nativeReference(entry.contextRef)||!validSha(entry.rawTextSha256)||!validSha(entry.payloadSha256)||!['RAW_INSTRUCTION','ORIGINAL_REPLY'].includes(entry.sourceKind)||!Array.isArray(entry.actions)||!entry.actions.includes('PREPARE')||entry.actions.some(action=>!['PREPARE','APPROVE','START'].includes(action))||new Set(entry.actions).size!==entry.actions.length||entry.previewRef!==undefined&&!nativeReference(entry.previewRef)||entry.approvalRef!==undefined&&!nativeReference(entry.approvalRef))throw new Error('追加历史元数据不完整；未重发原操作');
  return {kind:'APPEND',operationId:entry.operationId,contextRef:copyRef(entry.contextRef),rawTextSha256:entry.rawTextSha256,payloadSha256:entry.payloadSha256,sourceKind:entry.sourceKind,actions:[...entry.actions],...entry.previewRef?{previewRef:copyRef(entry.previewRef)}:{},...entry.approvalRef?{approvalRef:copyRef(entry.approvalRef)}:{}};
}
export function readAppendHistory(storage:AppendStorage):AppendHistoryEntry[] {
  const raw=storage.getItem(NATIVE_APPEND_HISTORY_KEY);if(raw===null)return[];
  let values:unknown;try{values=JSON.parse(raw);}catch{throw new Error('追加历史无法读取；原记录保留，未重发');}
  if(!Array.isArray(values)||values.length>128)throw new Error('追加历史格式或数量需核查；原记录保留，未重发');
  const actual=values.map(assertEntry);if(new Set(actual.map(entry=>entry.operationId)).size!==actual.length)throw new Error('追加历史包含重复操作身份，请核查');return actual;
}
function saveEntry(storage:AppendStorage,entry:AppendHistoryEntry){
  const values=readAppendHistory(storage);let actual=assertEntry(entry);const index=values.findIndex(value=>value.operationId===actual.operationId);
  const prior=values[index];
  if(prior){
    if(prior.kind!==actual.kind)throw new Error('此操作身份已绑定另一操作类型');
    if(prior.kind==='APPEND'&&actual.kind==='APPEND'){
      if(prior.rawTextSha256!==actual.rawTextSha256||prior.payloadSha256!==actual.payloadSha256||prior.sourceKind!==actual.sourceKind||!sameNativeReference(prior.contextRef,actual.contextRef)||prior.previewRef&&actual.previewRef&&!sameNativeReference(prior.previewRef,actual.previewRef)||prior.approvalRef&&actual.approvalRef&&!sameNativeReference(prior.approvalRef,actual.approvalRef))throw new Error('追加历史的原绑定引用已变化');
      actual={...prior,...actual,actions:[...new Set([...prior.actions,...actual.actions])],previewRef:actual.previewRef??prior.previewRef,approvalRef:actual.approvalRef??prior.approvalRef};
    }else if(prior.kind==='REVOKE'&&actual.kind==='REVOKE'&&prior.expectedIntentEpoch!==actual.expectedIntentEpoch)throw new Error('此接管操作已绑定另一意图版本');
  }
  if(index===-1){if(values.length>=128)throw new Error('追加历史已达本地索引上限，未删除历史或发送新操作');values.push(actual);}else values[index]=actual;
  storage.setItem(NATIVE_APPEND_HISTORY_KEY,JSON.stringify(values));return actual;
}
export function nativeAppendBridge(host:unknown):NativeAppendBridge|undefined {
  const bridge=(host as {eeasDesktop?:Partial<NativeAppendBridge>}|undefined)?.eeasDesktop;
  return bridge&&['getNativeAppend','prepareNativeAppend','approveNativeAppend','startNativeAppend','revokeNativeAppend'].every(key=>typeof bridge[key as keyof NativeAppendBridge]==='function')?bridge as NativeAppendBridge:undefined;
}
export function assertAppendState(value:unknown,operation?:AppendOperation):NativeAppendState {
  const state=value as NativeAppendState;
  if(!state||state.adapter_id!=='classic_notepad_append'||typeof state.state!=='string'||state.automatic_retry!==false)throw new Error('原生追加回执不完整，请核查原操作');
  if(state.scope!==undefined&&state.scope!==NATIVE_APPEND_SCOPE||state.foreground_required===false||state.no_save_action===false||state.no_selection_overwrite===false||state.native_input_global_enabled===true)throw new Error('回执超出本轮单次末尾追加范围');
  for(const ref of [state.preview_ref,state.approval_ref,state.qualification_ref,state.baseline_ref])if(ref!=null&&!nativeReference(ref))throw new Error('原生追加回执引用不完整');
  if(operation){
    if(state.task_operation_id!==operation.operationId&&state.preview_ref)throw new Error('原生追加回执属于另一操作');
    if(state.payload_sha256!==undefined&&state.payload_sha256!==operation.payloadSha256)throw new Error('回执追加文字与用户复核选文不同');
    if(operation.previewRef&&!sameNativeReference(operation.previewRef,state.preview_ref))throw new Error('原生追加预览引用已改变');
    if(operation.approvalRef&&!sameNativeReference(operation.approvalRef,state.approval_ref??undefined))throw new Error('原生追加审批引用已改变');
  }
  return state;
}
export function appendVerification(state?:NativeAppendState){return state?.task?.task_outcome?.task_outcome?.verification;}
export function appendResultLabel(state?:NativeAppendState,translate:(message:string)=>string=message=>message):string {
  if(!state)return translate('尚未读取追加状态');
  if(state.task_completed===true&&appendVerification(state)?.status==='VERIFIED')return translate('原缓冲区单次追加已核实 · 未保存文件');
  const names:Record<string,string>={NOT_ADAPTED:'当前软件或追加路径尚未适配',ADAPTED_REQUIRES_APPROVAL:'此记事本可准备追加，尚未授权',PREPARED:'选文预览已锁定，尚未授权',APPROVED:'本次操作已允许',NOT_CONFIGURED:'尚未配置本次模型',HANDOFF_REQUIRED:'未完成准确前台交接，需核查',BASE_CHANGED:'原缓冲区已变化，需重新核查',PARTIAL:'可能部分追加，需核查',UNKNOWN_OUTCOME:'追加结果未知，需核查',UNKNOWN:'追加状态未知，需核查',HISTORICAL_READONLY:'历史操作，只读核查',REVOKED:'本次追加权限已撤销',FINISHED:'原任务已结束，追加目标尚未核实',APPROVAL_CANCELLED:'已取消审批，未授予追加权限',CANCELLED:'审批已取消',STARTING:'正在交接并启动原任务',INTERPRETING:'原 Agent 正在解释本次已审批指令',RUNNING:'原任务进行中',AWAITING_VERIFICATION:'正在核对实际缓冲区',NO_RECORD:'未找到回执；不能据此判定未执行'};
  return names[state.state]?translate(names[state.state]):`${translate('原追加状态：')}${state.state}`;
}
export function appendActions(view:AppendView,contextRef?:NativeRef){
  const state=view.snapshot,live=view.origin==='LIVE'&&view.review==='OBSERVED'&&nativeReference(state?.preview_ref)&&nativeReference(state?.qualification_ref)&&state?.scope===NATIVE_APPEND_SCOPE&&state.foreground_required===true&&state.no_selection_overwrite===true&&state.no_save_action===true&&sameNativeReference(view.operation.contextRef,contextRef),configured=state?.provider_state==='PROFILE_SELECTED_UNVALIDATED';
  return {approve:!!live&&configured&&state!.state==='PREPARED'&&!view.operation.actions.includes('APPROVE'),start:false};
}
export function appendNeedsTargetReview(view:AppendView){return view.review!=='OBSERVED'||['UNKNOWN','UNKNOWN_OUTCOME','PARTIAL','BASE_CHANGED','HANDOFF_REQUIRED'].includes(view.snapshot?.state??'')||view.snapshot?.state==='FINISHED'&&view.snapshot.task_completed!==true;}
export const appendPending=(state?:NativeAppendState)=>!!state&&['STARTING','INTERPRETING','RUNNING','AWAITING_VERIFICATION'].includes(state.state);

export class NativeAppendClient {
  private bridge:NativeAppendBridge;private storage:AppendStorage;
  constructor(bridge:NativeAppendBridge,storage:AppendStorage){this.bridge=bridge;this.storage=storage;}
  current(contextRef?:NativeRef){if(contextRef!==undefined&&!nativeReference(contextRef))throw new Error('当前页引用不完整');return this.bridge.getNativeAppend(contextRef).then(value=>assertAppendState(value));}
  async inspect(operation:AppendOperation,origin:AppendView['origin']='RECOVERED'):Promise<AppendView>{
    const actual=assertEntry(operation) as AppendOperation;
    try{
      const value=await this.bridge.getNativeAppend(undefined,actual.operationId),failure=value as {state?:string;status?:string;error?:{code?:string;message?:string}};
      if(failure?.state==='NO_RECORD'||failure?.status==='NO_RECORD'||failure?.error?.code==='NOT_FOUND')return {operation:actual,origin,review:'NO_RECORD',message:'服务中未找到原追加回执；不证明没有执行，未自动重新审批或追加。'};
      const snapshot=assertAppendState(value,actual);if(snapshot.payload_text!==undefined&&await textSha(snapshot.payload_text)!==actual.payloadSha256)throw new Error('追加回执文字与其原选文摘要不一致');
      const next=saveEntry(this.storage,{...actual,...snapshot.preview_ref?{previewRef:snapshot.preview_ref}:{},...snapshot.approval_ref?{approvalRef:snapshot.approval_ref}:{}}) as AppendOperation;
      return {operation:next,origin,review:'OBSERVED',snapshot};
    }catch(error){return {operation:actual,origin,review:'INSPECTION_UNAVAILABLE',message:`原操作尚未核对：${error instanceof Error?error.message:String(error)}。历史保留，未重新审批或追加。`,message_copy:authoredCopy('原操作尚未核对：{error}。历史保留，未重新审批或追加。',{error:authoredErrorCopy(error)})};}
  }
  async restore(limit=128){if(!Number.isSafeInteger(limit)||limit<1||limit>128)throw new Error('追加历史读取范围无效');const entries=readAppendHistory(this.storage);return Promise.all(entries.filter((entry):entry is AppendOperation=>entry.kind==='APPEND').slice(-limit).map(operation=>this.inspect(operation)));}
  async prepare(body:AppendPrepare):Promise<AppendView>{
    // ORIGINAL_REPLY stays typed for the future trusted reader; this UI never derives approval from an answer.
    if(body.payload_source.kind!=='RAW_INSTRUCTION')throw new Error('原 Agent 回复的可信选文读取尚未适配，请使用用户原话');
    const exact=appendPrepare(body.context_ref,body.raw_text,body.payload_source.span,body.provider_profile_ref??undefined,body.operation_id),rawTextSha256=await textSha(exact.raw_text),payloadSha256=await textSha(exact.payload_source.span.quote);
    const prior=readAppendHistory(this.storage).find(entry=>entry.operationId===exact.operation_id);
    if(prior){if(prior.kind!=='APPEND'||prior.rawTextSha256!==rawTextSha256||prior.payloadSha256!==payloadSha256||!sameNativeReference(prior.contextRef,exact.context_ref))throw new Error('此操作身份已绑定另一原话或来源，未发送');return this.inspect(prior);}
    const operation:AppendOperation={kind:'APPEND',operationId:exact.operation_id,contextRef:exact.context_ref,rawTextSha256,payloadSha256,sourceKind:'RAW_INSTRUCTION',actions:['PREPARE']};
    saveEntry(this.storage,operation);return this.dispatch(operation,()=>this.bridge.prepareNativeAppend(exact));
  }
  private async dispatch(operation:AppendOperation,work:()=>Promise<unknown>):Promise<AppendView>{
    try{const snapshot=assertAppendState(await work(),operation);if(snapshot.payload_text!==undefined&&await textSha(snapshot.payload_text)!==operation.payloadSha256)throw new Error('回执追加文字与原选文不一致');const next=saveEntry(this.storage,{...operation,...snapshot.preview_ref?{previewRef:snapshot.preview_ref}:{},...snapshot.approval_ref?{approvalRef:snapshot.approval_ref}:{}}) as AppendOperation;return{operation:next,origin:'LIVE',review:'OBSERVED',snapshot};}
    catch(error){const review=await this.inspect(operation);return{...review,message:`${review.message??'原回执已只读核对；本次调用结果需检查。'} ${error instanceof Error?error.message:String(error)}`,message_copy:authoredCopy('{review} {error}',{review:review.message_copy??authoredCopy(review.message??'原回执已只读核对；本次调用结果需检查。'),error:authoredErrorCopy(error)})};}
  }
  async approve(view:AppendView,contextRef:NativeRef){
    const prior=this.actualOperation(view.operation);if(prior.actions.includes('APPROVE'))return this.inspect(prior);
    assertAppendState(view.snapshot,prior);
    if(!appendActions({...view,operation:prior},contextRef).approve)throw new Error('只有本轮已核对的选文预览可以请求 Main 原生审批');
    const operation={...prior,actions:[...prior.actions,'APPROVE' as const]};saveEntry(this.storage,operation);
    return this.dispatch(operation,()=>this.bridge.approveNativeAppend({operationId:operation.operationId,previewRef:view.snapshot!.preview_ref!}));
  }
  async start(view:AppendView){
    // Main approve owns the sole dialog/ticket/handoff/start chain. Old operation
    // refs cannot create another dialog or input; compatibility calls inspect only.
    const actual=await this.inspect(this.actualOperation(view.operation));return{...actual,message:`${actual.message??''}旧操作不重新开始；请核对目标实际内容，再准备新的选文预览。`,message_copy:authoredCopy('{review}旧操作不重新开始；请核对目标实际内容，再准备新的选文预览。',{review:actual.message_copy??authoredCopy(actual.message??'')})};
  }
  private actualOperation(operation:AppendOperation){const actual=readAppendHistory(this.storage).find(entry=>entry.operationId===operation.operationId);if(actual?.kind!=='APPEND'||actual.payloadSha256!==operation.payloadSha256||actual.rawTextSha256!==operation.rawTextSha256||!sameNativeReference(actual.contextRef,operation.contextRef))throw new Error('原追加操作尚未在本地准确记录，未派发');return actual;}
  async revoke(key='native-takeover-'+crypto.randomUUID().replaceAll('-','')):Promise<NativeAppendRevocation>{
    if(!validId(key))throw new Error('接管操作身份无效');if(readAppendHistory(this.storage).some(entry=>entry.operationId===key))throw new Error('此接管操作已尝试；请读取当前状态，不重新撤销');
    const current=await this.current();if(!positiveInteger(current.expected_intent_epoch))throw new Error('尚无实际当前意图版本，未撤销或接管');
    saveEntry(this.storage,{kind:'REVOKE',operationId:key,expectedIntentEpoch:current.expected_intent_epoch});
    const value=await this.bridge.revokeNativeAppend({operationId:key,expectedIntentEpoch:current.expected_intent_epoch}) as NativeAppendRevocation;
    if(!value||typeof value.status!=='string'||value.mutation_authority!==false)throw new Error('撤销回执尚未核实；请只读检查当前任务');return value;
  }
}

export class NativeAppendRequests {
  private pending=new Map<string,{identity:string;promise:Promise<AppendView>}>();
  private client:NativeAppendClient;
  constructor(client:NativeAppendClient){this.client=client;}
  prepare(value:unknown):Promise<AppendView>{
    const body=appendActionRequest(value),identity=JSON.stringify(body),old=this.pending.get(body.operation_id);
    if(old){if(old.identity!==identity)return Promise.reject(new Error('同一追加操作的指令或目标已变化'));return old.promise;}
    if(this.pending.size>=8)return Promise.reject(new Error('请先核查正在准备的追加请求'));
    const promise=this.client.prepare(body);this.pending.set(body.operation_id,{identity,promise});
    promise.finally(()=>{if(this.pending.get(body.operation_id)?.promise===promise)this.pending.delete(body.operation_id);}).catch(()=>{});
    return promise;
  }
}
