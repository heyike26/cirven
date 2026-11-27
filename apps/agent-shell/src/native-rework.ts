import {readReworkIndex,saveReworkIndex,validateReworkPacket,reworkPacketHash,normalizedReworkBody,sameReworkInput,type ReworkPrivatePacket} from './native-rework-index';
import {AuthoredUIError,authoredCopy,authoredErrorCopy,type AuthoredUICopy} from './authored-ui-copy.ts';
import { api, ApiClient, ApiError, operationId } from './api.ts';
import type { SourceRef, Workspace } from './contracts';
import { exactFileSource, sameSelectedFile } from './companion-files.ts';
import { nativeReference, sameNativeReference, type NativeRef } from './native-collaboration.ts';
import { NativeFeedbackApi, type NativeFileFeedback } from './native-feedback-api.ts';
import {configurationCanonical as canonical,configurationHash as hash} from './conversation-configuration';

export type ObjectLocator = {kind:'FILE'}|{kind:'JSON_POINTER';pointer:string}|{kind:'TEXT_RANGE';char_start:number;char_end:number;quote_sha256:string};
export type ChangeKind = 'CONSTRAINT'|'DATA'|'EXPRESSION'|'UNKNOWN';
export type InputCondition = 'VALUE_CHANGED'|'ANY_FILE_VERSION_CHANGED'|'MANUAL_REVIEW';
export type ReworkContract = {task_id:string;inputs:{binding_ref:NativeRef;input_value_sha256:string;condition:InputCondition;invalidating_kinds:ChangeKind[];verification_note:string;verification_refs:NativeRef[]}[]};
export type NativePreviewBody={classification_kind:ChangeKind;classification_origin:'USER_STATEMENT'|'OPENCODE_PROPOSAL';rationale:string;contracts:ReworkContract[]}&({proposed_value:unknown;proposed_value_text?:never}|{proposed_value_text:string;proposed_value?:never});
export type ReworkImpact = {tasks:{task_id:string;status:'AFFECTED'|'UNKNOWN'|'RETAINED_BY_DECLARED_CONDITION';reasons:string[];previous_confirmation_preserved:boolean}[];counts:Record<string,number>;baseline_all_tasks:number;known_dependency_baseline_tasks:number;selected_rework_tasks:number;pending_assessment_tasks:number};
export type ReworkOutcome = {workspace_id:string;path:string;status:'APPLIED'|'UNCHANGED'|'CONFLICT'|'UNKNOWN_OUTCOME';source_ref?:SourceRef;reason?:string;[key:string]:unknown};
export type ReworkRecord = {
  record_ref:NativeRef;kind:'MAPPING'|'PLAN'|'APPLY';status:string;
  mapping_ref?:NativeRef;plan_ref?:NativeRef;receipt_ref?:NativeRef;
  payload:{id:string;project_id:string;revision:number;kind:ReworkRecord['kind'];status:string;native_feedback_ref:NativeRef;proposal_ref:NativeRef;
    baseline_file_source_ref?:SourceRef;locator?:ObjectLocator;task_ids?:string[];candidate_task_ids?:string[];mapping_basis?:string;binding_ref?:NativeRef;selected_value_sha256?:string;
    mapping_ref?:NativeRef;plan_ref?:NativeRef;proposed_value?:unknown;reason?:string;write_scope?:{workspace_id:string;path:string}[];
    engine_plan_ref?:NativeRef;engine_plan?:{plan_ref:NativeRef;payload:{classification:{kind:ChangeKind;origin:string;rationale:string};contracts:ReworkContract[];impact:ReworkImpact;confirmation_preservation:unknown;[key:string]:unknown}};
    engine_receipt?:{payload:{outcomes:ReworkOutcome[];impact:ReworkImpact;modified_file_count:number;unknown_outcome_file_count:number;confirmation_preservation:unknown;[key:string]:unknown};[key:string]:unknown};[key:string]:unknown};
  current_basis_check:{status:'CURRENT_TASK_REQUIREMENT_BASIS'|'STALE_REVIEW_REQUIRED';reason?:string;source_review_status?:string;semantic_correctness_confirmed:false};
  artifact?:{path:string;sha256:string;download_url:string;[key:string]:unknown};
};
export type ReworkContext = {status:'READ_ONLY_CONTEXT';file_source_ref:SourceRef;feedback:NativeFileFeedback[];mappings:ReworkRecord[];confirmed_proposals:{proposal_ref:NativeRef;title:string;review_status:string;relationship:string;task_ids:string[]}[];file_write_authorized:false;automatic_task_selection:false};
export type OriginalPreviewInput={operation_id:string;mapping_ref:NativeRef;classification_kind:ChangeKind;classification_origin:'USER_STATEMENT';rationale:string;proposed_value_text:string;contracts:ReworkContract[]};
export type OriginalPreviewPacket={project_id:string;feedback_ref:NativeRef;input:OriginalPreviewInput;text_kind:'JSON_VALUE'|'UTF8_TEXT'};
export async function originalPreviewRequestHash(packet:OriginalPreviewPacket){return hash({project_id:packet.project_id,operation_id:packet.input.operation_id,kind:'PLAN',input:{...packet.input,contracts:packet.input.contracts.map(c=>({...c,inputs:c.inputs.map(i=>({...i,invalidating_kinds:i.invalidating_kinds??['EXPRESSION','DATA','CONSTRAINT','UNKNOWN']}))}))}});}
export function prepareOriginalPreview(mapping:ReworkRecord,input:OriginalPreviewInput):OriginalPreviewPacket{
  assertReworkRecord(mapping,mapping.payload.project_id);
  if(mapping.kind!=='MAPPING'||mapping.status!=='MAPPED'||mapping.current_basis_check.status!=='CURRENT_TASK_REQUIREMENT_BASIS'||!sameNativeReference(mapping.record_ref,input.mapping_ref)||input.classification_origin!=='USER_STATEMENT'||!proposalText(input.proposed_value_text))throw Error('NATIVE_PREVIEW_CHANGED');
  return structuredClone({project_id:mapping.payload.project_id,feedback_ref:mapping.payload.native_feedback_ref,input,text_kind:mapping.payload.locator?.kind==='JSON_POINTER'?'JSON_VALUE':'UTF8_TEXT'});
}
export async function validateOriginalPreview(value:unknown,packet:OriginalPreviewPacket){
  const record=assertReworkRecord(value,packet.project_id,packet.feedback_ref),request=packet.input,engine=record.payload.engine_plan?.payload;
  if(record.kind!=='PLAN'||!sameNativeReference(record.payload.mapping_ref,request.mapping_ref)||record.payload.request_sha256!==await originalPreviewRequestHash(packet)||engine?.classification.kind!==request.classification_kind||engine.classification.origin!==request.classification_origin||engine.classification.rationale!==request.rationale||canonical(engine.contracts)!==canonical(request.contracts))throw Error('NATIVE_PREVIEW_CHANGED');
  return validateReworkProposalText(record,{text:request.proposed_value_text,kind:packet.text_kind});
}
export type ReworkOperation = {projectId:string;feedbackRef:NativeRef;kind:ReworkRecord['kind'];operationId:string;basisRef:NativeRef;requestSha?:string;packet?:ReworkPrivatePacket};
export type ReworkOriginalInput={operation:ReworkOperation;protocol:'MAPPING'|'ORDINARY_PREVIEW'|'CONFLICT_PREVIEW'|'APPLY';body:Record<string,unknown>;input_json:string;request_sha256:string;local_packet_verified:boolean;saved_metadata_records_read:number};
export type ReworkReview = {status:'INCOMPLETE_REVIEW_REQUIRED'|'NO_RECORD'|'INSPECTION_UNAVAILABLE';operation_id:string;request_sha256?:string;automatic_retry:false;message:string;message_copy?:AuthoredUICopy;engine_operation?:unknown};
type Storage = Pick<globalThis.Storage,'getItem'|'setItem'>;
const states = {MAPPING:['MAPPED','NEEDS_CLARIFICATION','NOT_ADAPTED'],PLAN:['PROPOSED'],APPLY:['APPLIED','PARTIAL','CONFLICT','UNKNOWN_OUTCOME']} as const;
const hex = (value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export function reworkSource(value:unknown):value is SourceRef {
  const source=value as SourceRef;return source?.kind==='local_file'&&typeof source.project_id==='string'&&!!source.project_id&&typeof source.workspace_id==='string'&&!!source.workspace_id&&typeof source.path==='string'&&!!source.path&&!source.path.startsWith('/')&&!source.path.includes('\\')&&!source.path.split('/').some(part=>!part||part==='.'||part==='..')&&!source.path.includes(':')&&Number.isInteger(source.revision)&&source.revision>0&&Number.isInteger(source.binding_revision)&&source.binding_revision>0&&hex(source.sha256)&&hex(source.binding_sha256);
}
export function assertReworkRecord(value:unknown,pid:string,feedback?:NativeRef,expectedRef?:NativeRef):ReworkRecord {
  const record=value as ReworkRecord,p=record?.payload,kind=record?.kind;
  if(!p||!Object.hasOwn(states,kind)||!nativeReference(record.record_ref)||p.project_id!==pid||p.id!==record.record_ref.id||p.revision!==record.record_ref.revision||p.kind!==kind||p.status!==record.status||!(states[kind] as readonly string[]).includes(record.status)||!nativeReference(p.native_feedback_ref)||!nativeReference(p.proposal_ref)||feedback&&!sameNativeReference(p.native_feedback_ref,feedback)||expectedRef&&!sameNativeReference(record.record_ref,expectedRef)||!['CURRENT_TASK_REQUIREMENT_BASIS','STALE_REVIEW_REQUIRED'].includes(record.current_basis_check?.status)||record.current_basis_check.semantic_correctness_confirmed!==false)throw new Error('返工回执的项目、原反馈或持久版本身份不一致，请核对原操作');
  const top=kind==='MAPPING'?record.mapping_ref:kind==='PLAN'?record.plan_ref:record.receipt_ref;
  if(!sameNativeReference(top,record.record_ref))throw new Error('返工持久引用不一致');
  if(kind==='MAPPING'){
    if(!reworkSource(p.baseline_file_source_ref)||p.baseline_file_source_ref.project_id!==pid)throw new Error('任务关联缺少真实文件来源');
    if(record.status==='MAPPED'&&(!nativeReference(p.binding_ref)||!hex(p.selected_value_sha256)||!p.locator||!p.task_ids?.length||new Set(p.task_ids).size!==p.task_ids.length))throw new Error('任务关联缺少实际对象或输入依据');
  } else if(kind==='PLAN'){
    if(!nativeReference(p.mapping_ref)||!nativeReference(p.engine_plan_ref)||!sameNativeReference(p.engine_plan?.plan_ref,p.engine_plan_ref)||!p.write_scope?.length||p.write_scope.length>64||!Array.isArray(p.engine_plan?.payload?.impact?.tasks)||!Array.isArray(p.engine_plan?.payload?.contracts))throw new Error('预览缺少真实限定范围与影响回执');
    const seen=new Set<string>();for(const scope of p.write_scope){if(!scope.workspace_id||!scope.path||scope.path.startsWith('/')||scope.path.includes('\\')||scope.path.includes(':')||scope.path.split('/').some(part=>!part||part==='.'||part==='..')||seen.has(scope.workspace_id+'\n'+scope.path))throw new Error('返工限定路径不合法');seen.add(scope.workspace_id+'\n'+scope.path);}
  } else {
    if(!nativeReference(p.plan_ref)||!Array.isArray(p.engine_receipt?.payload?.outcomes))throw new Error('应用回执缺少真实文件结果');
    for(const outcome of p.engine_receipt.payload.outcomes){if(!['APPLIED','UNCHANGED','CONFLICT','UNKNOWN_OUTCOME'].includes(outcome.status)||outcome.source_ref&&(!reworkSource(outcome.source_ref)||outcome.source_ref.project_id!==pid||outcome.source_ref.workspace_id!==outcome.workspace_id||outcome.source_ref.path!==outcome.path))throw new Error('应用结果的实际文件身份不一致');}
  }
  return record;
}
export function reworkCanApply(record:ReworkRecord|undefined){return record?.kind==='PLAN'&&record.status==='PROPOSED'&&record.current_basis_check.status==='CURRENT_TASK_REQUIREMENT_BASIS';}
export function reworkImpact(record:ReworkRecord|undefined){return record?.payload.engine_plan?.payload.impact??record?.payload.engine_receipt?.payload.impact;}
/** Manual JSON stays original text here too: JSON.stringify on the decoded
 * record could show a rounded large integer before the user authorizes Apply. */
export function reworkProposedText(record:ReworkRecord):string{
  if(record.payload.manual_resolution!==undefined){const manual=record.payload.manual_resolution as {resolved_value_text?:unknown};if(!manual||typeof manual.resolved_value_text!=='string')throw Error('NATIVE_CONFLICT_CHANGED');return manual.resolved_value_text;}
  if(record.payload.proposal_text!==undefined){const original=record.payload.proposal_text as {text?:unknown};if(!original||typeof original.text!=='string')throw Error('NATIVE_CONFLICT_CHANGED');return original.text;}
  return typeof record.payload.proposed_value==='string'?record.payload.proposed_value:JSON.stringify(record.payload.proposed_value,null,2);
}
function proposalText(value:unknown):value is string{if(typeof value!=='string'||value.includes('\0'))return false;const b=new TextEncoder().encode(value);return b.length<=1048576&&new TextDecoder('utf-8',{fatal:true}).decode(b)===value;}
async function textSha(value:string){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(b=>b.toString(16).padStart(2,'0')).join('');}
export async function validateReworkProposalText(record:ReworkRecord,expected?:{text:string;kind:'JSON_VALUE'|'UTF8_TEXT'}){
  const original=record.payload.proposal_text as {kind:string;text:string;sha256:string}|undefined;
  if(original===undefined){if(expected)throw Error('NATIVE_CONFLICT_CHANGED');return record;}
  if(record.kind!=='PLAN'||!original||!['JSON_VALUE','UTF8_TEXT'].includes(original.kind)||!proposalText(original.text)||!hex(original.sha256)||original.sha256!==await textSha(original.text)||expected&&(original.text!==expected.text||original.kind!==expected.kind))throw Error('NATIVE_CONFLICT_CHANGED');
  const value=original.kind==='JSON_VALUE'?JSON.parse(original.text):original.text,patches=record.payload.engine_plan?.payload.patches as {binding_ref:NativeRef;proposed_value:unknown}[]|undefined;
  const pending=[value];for(let visited=0;pending.length;visited++){if(visited>1048576)throw Error('NATIVE_CONFLICT_CHANGED');const v=pending.pop();if(typeof v==='number'&&!Number.isFinite(v))throw Error('NATIVE_CONFLICT_CHANGED');if(v&&typeof v==='object')for(const child of Object.values(v))pending.push(child);}
  if(!Array.isArray(patches)||patches.length!==1||!nativeReference(patches[0].binding_ref)||canonical(patches[0].proposed_value)!==canonical(record.payload.proposed_value)||canonical(value)!==canonical(record.payload.proposed_value))throw Error('NATIVE_CONFLICT_CHANGED');
  return record;
}
export function readReworkHistory(storage:Storage,pid:string,ref:NativeRef){return readReworkIndex(storage,pid,ref);}
/** An already acknowledged manual preview joins the existing readonly index.
 * The full intent and its durable receipt remain in the conflict journal. */
export async function rememberReworkPreview(storage:Storage,record:ReworkRecord,operationId:string){assertReworkRecord(record,record.payload.project_id);if(record.kind!=='PLAN'||!record.payload.mapping_ref)throw Error('NATIVE_CONFLICT_CHANGED');return saveReworkIndex(storage,{projectId:record.payload.project_id,feedbackRef:record.payload.native_feedback_ref,kind:'PLAN',operationId,basisRef:record.payload.mapping_ref});}
export class ReworkOutcomeError extends AuthoredUIError {readonly operation:ReworkOperation;readonly review:ReworkReview;constructor(operation:ReworkOperation,review:ReworkReview){const copy=review.message_copy??authoredCopy('{message}',{message:review.message});super(copy.message_key,copy.params);this.operation=operation;this.review=review;}}
export class NativeReworkApi {
  readonly client:ApiClient;constructor(client:ApiClient=api,private indexLocks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null){this.client=client;}
  root(pid:string){return this.client.projectPath(pid)+'/native-rework';}
  async context(source:SourceRef,signal?:AbortSignal):Promise<ReworkContext>{const value=await this.client.request<ReworkContext>(this.root(source.project_id)+'/context','POST',{file_source_ref:source},signal,false);if(value?.status!=='READ_ONLY_CONTEXT'||!exactFileSource(value.file_source_ref,source)||value.file_write_authorized!==false||value.automatic_task_selection!==false||!Array.isArray(value.confirmed_proposals)||!Array.isArray(value.mappings))throw new Error('返工上下文未绑定实际文件来源');for(const p of value.confirmed_proposals)if(!nativeReference(p.proposal_ref)||!Array.isArray(p.task_ids))throw new Error('任务方案上下文缺少真实版本');value.mappings.forEach(record=>assertReworkRecord(record,source.project_id));return value;}
  async get(pid:string,kind:'MAPPING'|'PLAN',ref:NativeRef,feedback?:NativeRef,signal?:AbortSignal){const actual=assertReworkRecord(await this.client.request(this.root(pid)+'/'+(kind==='MAPPING'?'mappings':'plans')+'/'+encodeURIComponent(ref.id),'GET',undefined,signal),pid,feedback,ref);if(actual.kind!==kind)throw new Error('返工引用属于另一记录类型');return validateReworkProposalText(actual);}
  async binding(mapping:ReworkRecord){const pid=mapping.payload.project_id,ref=mapping.payload.binding_ref;if(mapping.status!=='MAPPED'||!nativeReference(ref))throw new Error('尚无实际对象绑定');const value=await this.client.request<{record_ref:NativeRef;payload:{selected_value:unknown;selected_value_sha256:string;file_source_ref:SourceRef}}>(this.client.projectPath(pid)+'/feedback-rework/bindings/'+encodeURIComponent(ref.id));if(!sameNativeReference(value.record_ref,ref)||value.payload.selected_value_sha256!==mapping.payload.selected_value_sha256||!exactFileSource(value.payload.file_source_ref,mapping.payload.baseline_file_source_ref!))throw new Error('对象原值与持久绑定不一致');return value.payload.selected_value;}
  async previewSource(feedback:NativeFileFeedback){const baseline=feedback.payload.baseline_file_source_ref,file=await this.client.file(baseline.project_id,baseline.workspace_id,baseline.path);if(!file.current||!reworkSource(file.source_ref)||!sameSelectedFile(file.source_ref,baseline)||file.source_ref.binding_revision!==baseline.binding_revision||file.source_ref.binding_sha256!==baseline.binding_sha256)throw new Error('原反馈的实际目录绑定已变化，尚未发送返工提问');return file.source_ref;}
  async inspect(operation:ReworkOperation,signal?:AbortSignal):Promise<ReworkRecord|ReworkReview>{try{operation=structuredClone(operation);await validateReworkPacket(operation);const value=await this.client.request<ReworkRecord|ReworkReview>(this.root(operation.projectId)+'/operations/'+encodeURIComponent(operation.operationId),'GET',undefined,signal);if(value.status==='INCOMPLETE_REVIEW_REQUIRED'){const review=value as ReworkReview;if(review.operation_id!==operation.operationId||review.automatic_retry!==false||operation.requestSha!==undefined&&review.request_sha256!==operation.requestSha)throw new Error('未完成回执操作身份不一致');return{...review,message:'原操作尚未完成，需核查实际文件与执行回执；未自动重新应用。'};}const record=assertReworkRecord(value,operation.projectId,operation.feedbackRef);const basis=record.kind==='MAPPING'?record.payload.proposal_ref:record.kind==='PLAN'?record.payload.mapping_ref:record.payload.plan_ref;if(record.kind!==operation.kind||!sameNativeReference(basis,operation.basisRef)||operation.requestSha!==undefined&&record.payload.request_sha256!==operation.requestSha)throw new Error('原操作回执绑定了另一返工依据');if(operation.packet&&operation.requestSha===undefined){const input=await this.originalInput(operation,signal);if(record.payload.request_sha256!==input.request_sha256)throw Error('NATIVE_REWORK_INPUT_CHANGED');}return await validateReworkProposalText(record);}catch(error){if(signal?.aborted)throw error;return{status:error instanceof ApiError&&error.status===404?'NO_RECORD':'INSPECTION_UNAVAILABLE',operation_id:operation.operationId,automatic_retry:false,message:error instanceof ApiError&&error.status===404?'服务中没有找到原操作回执；这不证明没有执行，请核查，未自动重发。':`原操作暂未核对：${error instanceof Error?error.message:String(error)}；未自动重发。`,message_copy:error instanceof ApiError&&error.status===404?authoredCopy('服务中没有找到原操作回执；这不证明没有执行，请核查，未自动重发。'):authoredCopy('原操作暂未核对：{error}；未自动重发。',{error:authoredErrorCopy(error)})};}}
  async originalInput(operation:ReworkOperation,signal?:AbortSignal):Promise<ReworkOriginalInput>{
    const frozen=structuredClone(operation);await validateReworkPacket(frozen);
    const v=await this.client.request<Record<string,unknown>>(this.root(frozen.projectId)+'/operations/'+encodeURIComponent(frozen.operationId)+'/input','GET',undefined,signal);
    if(!v||typeof v!=='object'||v.kind!=='NATIVE_REWORK_ORIGINAL_INPUT'||v.version!==1||v.project_id!==frozen.projectId||v.operation_id!==frozen.operationId||v.operation_kind!==frozen.kind||v.read_only!==true||v.source_files_written!==0||v.model_calls!==0||v.execution_started!==false||v.write_authorized!==false||v.input_encoding!=='EXACT_SAVED_CANONICAL_JSON_NOT_RECONSTRUCTED_CALLER_LITERAL'||(v.saved_metadata_records_read!==1&&v.saved_metadata_records_read!==2)||typeof v.input_json!=='string'||new TextEncoder().encode(v.input_json).length>2097152||!hex(v.request_sha256)||!hex(v.input_sha256))throw Error('NATIVE_REWORK_INPUT_CHANGED');
    const scope=v.input_scope as {basis_ref?:NativeRef;feedback_ref?:NativeRef};if(!sameNativeReference(scope?.basis_ref,frozen.basisRef)||!sameNativeReference(scope?.feedback_ref,frozen.feedbackRef))throw Error('NATIVE_REWORK_INPUT_CHANGED');
    const body=JSON.parse(v.input_json) as Record<string,unknown>;
    if(!body||typeof body!=='object'||Array.isArray(body)||body.operation_id!==frozen.operationId||await reworkPacketHash(v.input_json)!==v.input_sha256)throw Error('NATIVE_REWORK_INPUT_CHANGED');
    // Insert the backend's exact canonical input as raw JSON. JS must not
    // round-trip old arbitrary integers or Python float spellings for its SHA.
    const basis='{"input":'+v.input_json+',"kind":'+JSON.stringify(frozen.kind)+',"operation_id":'+JSON.stringify(frozen.operationId)+',"project_id":'+JSON.stringify(frozen.projectId)+'}';
    if(await reworkPacketHash(basis)!==v.request_sha256||frozen.requestSha!==undefined&&frozen.requestSha!==v.request_sha256)throw Error('NATIVE_REWORK_INPUT_CHANGED');
    const protocol=v.protocol as ReworkOriginalInput['protocol'];if(!['MAPPING','ORDINARY_PREVIEW','CONFLICT_PREVIEW','APPLY'].includes(protocol)||protocol==='MAPPING'&&frozen.kind!=='MAPPING'||protocol==='APPLY'&&frozen.kind!=='APPLY'||['ORDINARY_PREVIEW','CONFLICT_PREVIEW'].includes(protocol)&&frozen.kind!=='PLAN')throw Error('NATIVE_REWORK_INPUT_CHANGED');
    if(frozen.packet&&!sameReworkInput(normalizedReworkBody(frozen.kind,JSON.parse(frozen.packet.json)),body))throw Error('NATIVE_REWORK_INPUT_CHANGED');
    return {operation:frozen,protocol,body,input_json:v.input_json,request_sha256:v.request_sha256,local_packet_verified:!!frozen.packet,saved_metadata_records_read:Number(v.saved_metadata_records_read)};
  }
  private async dispatch(storage:Storage,operation:ReworkOperation,path:string,body:unknown){operation=structuredClone(operation);const json=JSON.stringify(structuredClone(body));operation.packet={version:1,json,sha256:await reworkPacketHash(json)};body=JSON.parse(json);if(operation.kind!=='PLAN')operation.requestSha=await hash({project_id:operation.projectId,operation_id:operation.operationId,kind:operation.kind,input:normalizedReworkBody(operation.kind,body as Record<string,unknown>)});const saved=await saveReworkIndex(storage,operation,this.indexLocks);if(!saved.fresh){const actual=await this.inspect(saved.operation);if('record_ref' in actual)return actual;throw new ReworkOutcomeError(operation,actual);}let original:unknown;try{const value=await this.client.request(path,'POST',body,undefined,false);if((value as ReworkReview)?.status==='INCOMPLETE_REVIEW_REQUIRED')throw new Error('原操作尚未完成');const actual=assertReworkRecord(value,operation.projectId,operation.feedbackRef);const basis=actual.kind==='MAPPING'?actual.payload.proposal_ref:actual.kind==='PLAN'?actual.payload.mapping_ref:actual.payload.plan_ref;if(actual.kind!==operation.kind||!sameNativeReference(basis,operation.basisRef)||operation.requestSha!==undefined&&actual.payload.request_sha256!==operation.requestSha)throw new Error('返工返回依据与本次操作不一致');if(operation.packet&&operation.requestSha===undefined){const input=await this.originalInput(operation);if(actual.payload.request_sha256!==input.request_sha256)throw Error('NATIVE_REWORK_INPUT_CHANGED');}return await validateReworkProposalText(actual);}catch(error){original=error;}
    const actual=await this.inspect(operation);if('record_ref' in actual)return actual;const detail=original instanceof Error?original.message:String(original);throw new ReworkOutcomeError(operation,{...actual,message:`${actual.message} ${detail}`});
  }
  map(storage:Storage,feedback:NativeFileFeedback,proposal:NativeRef,options:{locator?:ObjectLocator;taskIds?:string[];confirmed?:boolean;mappingContext?:NativeRef}={},key=operationId()){
    return this.dispatch(storage,{projectId:feedback.payload.project_id,feedbackRef:feedback.feedback_ref,kind:'MAPPING',operationId:key,basisRef:proposal},this.root(feedback.payload.project_id)+'/mappings',{operation_id:key,native_feedback_ref:feedback.feedback_ref,proposal_ref:proposal,user_confirmed:options.confirmed===true,...options.locator?{locator:options.locator}:{},...options.taskIds?{task_ids:options.taskIds}:{},...options.mappingContext?{mapping_context_ref:options.mappingContext}:{}});
  }
  preview(storage:Storage,mapping:ReworkRecord,body:NativePreviewBody,key=operationId()){
    assertReworkRecord(mapping,mapping.payload.project_id);if(mapping.kind!=='MAPPING'||mapping.status!=='MAPPED'||mapping.current_basis_check.status!=='CURRENT_TASK_REQUIREMENT_BASIS')throw new Error('请先核对当前任务与对象关联');
    const original=Object.hasOwn(body,'proposed_value_text'),legacy=Object.hasOwn(body,'proposed_value');if(original===legacy||original&&!proposalText(body.proposed_value_text))throw Error('NATIVE_CONFLICT_CHANGED');
    const input=structuredClone({operation_id:key,mapping_ref:mapping.record_ref,...body});
    const operation:ReworkOperation={projectId:mapping.payload.project_id,feedbackRef:structuredClone(mapping.payload.native_feedback_ref),kind:'PLAN',operationId:key,basisRef:structuredClone(mapping.record_ref)};
    const expected=original?{text:input.proposed_value_text!,kind:(mapping.payload.locator?.kind==='JSON_POINTER'?'JSON_VALUE':'UTF8_TEXT') as 'JSON_VALUE'|'UTF8_TEXT'}:undefined;
    return (async()=>{
    if(original){const normalized={...input,contracts:input.contracts.map(c=>({...c,inputs:c.inputs.map(i=>({...i,invalidating_kinds:i.invalidating_kinds??['EXPRESSION','DATA','CONSTRAINT','UNKNOWN']}))}))};operation.requestSha=await hash({project_id:operation.projectId,operation_id:key,kind:'PLAN',input:normalized});}
    const actual=await this.dispatch(storage,operation,this.root(operation.projectId)+'/plans',input);
    return validateReworkProposalText(actual,expected);
    })();
  }

  async postOriginalPreview(packet:OriginalPreviewPacket,signal?:AbortSignal){
    const frozen=structuredClone(packet),value=await this.client.request(this.root(frozen.project_id)+'/plans','POST',frozen.input,signal,false);
    return validateOriginalPreview(value,frozen);
  }
  async inspectOriginalPreview(packet:OriginalPreviewPacket,signal?:AbortSignal):Promise<ReworkRecord|ReworkReview>{
    const frozen=structuredClone(packet),operation:ReworkOperation={projectId:frozen.project_id,feedbackRef:frozen.feedback_ref,kind:'PLAN',operationId:frozen.input.operation_id,basisRef:frozen.input.mapping_ref,requestSha:await originalPreviewRequestHash(frozen)},actual=await this.inspect(operation,signal);
    return 'record_ref' in actual?validateOriginalPreview(actual,frozen):actual;
  }
  async apply(storage:Storage,plan:ReworkRecord,allowRebase=false,key=operationId()){
    const pid=plan.payload.project_id,fresh=await this.get(pid,'PLAN',plan.record_ref,plan.payload.native_feedback_ref);if(!reworkCanApply(fresh))throw new Error('任务或需求依据已变，请核对旧预览；未应用');const sources:SourceRef[]=[];for(const scope of fresh.payload.write_scope!){const file=await this.client.file(pid,scope.workspace_id,scope.path);if(!file.current||!reworkSource(file.source_ref)||file.source_ref.project_id!==pid||file.source_ref.workspace_id!==scope.workspace_id||file.source_ref.path!==scope.path)throw new Error('限定文件尚未得到实际当前版本，未应用');const workspace=await this.client.request<Workspace>(this.client.workspacePath(pid,scope.workspace_id));if(workspace.project_id!==pid||workspace.id!==scope.workspace_id||workspace.revision!==file.source_ref.binding_revision||workspace.binding_sha256!==file.source_ref.binding_sha256||!Array.isArray(workspace.allowed_write_paths)||!workspace.allowed_write_paths.some(path=>path==='*'||path===scope.path||path.endsWith('/')&&scope.path.startsWith(path)))throw new Error('此实际目录绑定没有限定文件的写入许可；预览保留，未应用。先明确单文件授权，再基于新来源准备预览。');sources.push(file.source_ref);}
    return this.dispatch(storage,{projectId:pid,feedbackRef:plan.payload.native_feedback_ref,kind:'APPLY',operationId:key,basisRef:plan.record_ref},this.root(pid)+'/plans/'+encodeURIComponent(plan.record_ref.id)+'/apply',{operation_id:key,plan_ref:plan.record_ref,expected_current_sources:sources,allow_rebase:allowRebase===true});
  }
}
export function declaredMappedContracts(mapping:ReworkRecord,condition:InputCondition,note:string):ReworkContract[]{if(mapping.kind!=='MAPPING'||mapping.status!=='MAPPED'||!mapping.payload.binding_ref||!mapping.payload.selected_value_sha256||!note.trim())throw new Error('输入条件需要实际对象关联与明确声明');return mapping.payload.task_ids!.map(task_id=>({task_id,inputs:[{binding_ref:mapping.payload.binding_ref!,input_value_sha256:mapping.payload.selected_value_sha256!,condition,invalidating_kinds:['EXPRESSION','DATA','CONSTRAINT','UNKNOWN'],verification_note:note,verification_refs:[mapping.payload.binding_ref!]}]}));}
export function reworkPreviewQuestion(feedback:NativeFileFeedback,proposal?:NativeRef,mapping?:ReworkRecord){if(!nativeReference(feedback.feedback_ref)||!reworkSource(feedback.payload.baseline_file_source_ref))throw new Error('缺少真实已保存反馈与文件依据');if(mapping&&(!sameNativeReference(mapping.payload.native_feedback_ref,feedback.feedback_ref)||mapping.payload.project_id!==feedback.payload.project_id))throw new Error('提问引用了另一文件反馈');return `请依据已保存文件反馈准备局部返工预览，仅提出变更，不应用或写入文件。\n反馈的准确引用：${JSON.stringify(feedback.feedback_ref)}。${proposal?`\n已选择任务方案：${JSON.stringify(proposal)}。`:''}${mapping?`\n已有对象关联：${JSON.stringify(mapping.record_ref)}。`:''}\n请读取实际返工上下文并返回持久预览引用。优先沿用已有明确映射或唯一实际来源锚点；歧义时在当前对话澄清。保留人工内容与原确认结论；未声明输入条件的任务影响保持未知，不推定独立性。当前画面不授权文件写入，也不用于猜测文件对象。`;}
export class ReworkPreviewRecovery {
  private generation=0;private signature='';private controller?:AbortController;
  private rework:Pick<NativeReworkApi,'get'>;private feedback:Pick<NativeFeedbackApi,'get'>;private publish:(feedback:NativeFileFeedback,records:ReworkRecord[])=>void;private unavailable?: (pid:string,message:string)=>void;
  constructor(rework:Pick<NativeReworkApi,'get'>,feedback:Pick<NativeFeedbackApi,'get'>,publish:(feedback:NativeFileFeedback,records:ReworkRecord[])=>void,unavailable?:(pid:string,message:string)=>void){this.rework=rework;this.feedback=feedback;this.publish=publish;this.unavailable=unavailable;}
  async restore(pid:string,previews:unknown[],force=false){const rows=previews.flatMap(value=>{try{const r=assertReworkRecord(value,pid);return r.kind==='MAPPING'||r.kind==='PLAN'?[r]:[];}catch{return[];}}),unique=[...new Map(rows.map(r=>[r.record_ref.id,r])).values()],signature=pid+':'+unique.map(r=>r.record_ref.id+':'+r.record_ref.sha256).sort().join(',');if(!force&&signature===this.signature)return;this.signature=signature;this.controller?.abort();const signal=(this.controller=new AbortController()).signal,generation=++this.generation;
    const loaded=await Promise.allSettled(unique.slice(-24).map(async record=>{const actual=await this.rework.get(pid,record.kind as 'MAPPING'|'PLAN',record.record_ref,record.payload.native_feedback_ref,signal),feedback=await this.feedback.get(pid,actual.payload.native_feedback_ref);if(!sameSelectedFile(feedback.payload.baseline_file_source_ref,(record.payload.baseline_file_source_ref??feedback.payload.baseline_file_source_ref)))throw new Error('原生预览与反馈文件身份不一致');return{actual,feedback};}));if(signal.aborted||generation!==this.generation)return;
    const groups=new Map<string,{feedback:NativeFileFeedback;records:ReworkRecord[]}>();for(const value of loaded)if(value.status==='fulfilled'){const {feedback,actual}=value.value,group=groups.get(feedback.feedback_ref.id)??{feedback,records:[]};group.records.push(actual);groups.set(feedback.feedback_ref.id,group);}for(const group of groups.values())this.publish(group.feedback,group.records);const failures=loaded.filter(value=>value.status==='rejected');this.unavailable?.(pid,failures.length?`${failures.length} 个原返工引用尚未核对；历史记录保留，未生成或应用新变更。`:'');
  }
  stop(){this.generation++;this.controller?.abort();}
}
export const nativeReworkApi=new NativeReworkApi();
