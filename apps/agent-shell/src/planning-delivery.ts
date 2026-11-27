import {AuthoredUIError,authoredCopy,authoredErrorCopy,type AuthoredUICopy} from './authored-ui-copy.ts';
import { api, operationId, ApiError, type ApiClient } from './api.ts';
import { DiscoveryApi, workspaceRef } from './discovery-api.ts';
import { nativeReference, sameNativeReference } from './native-collaboration.ts';
import type { DiscoverySession } from './discovery-contracts';
import type { FileContent, PlanningProposal, SourceRef, Workspace } from './contracts';
import type { DiagramArtifact, RevisionRef, WordResult } from './workbench-contracts';

export type DeliveryWorkspace = Pick<Workspace,'id'|'revision'|'binding_sha256'>;
export type DeliveryFile = { path:string;sha256:string;byte_length?:number;file_source_ref?:SourceRef;source_ref?:SourceRef;outcome?:string;native_path?:string };
export type DeliveryAsset = { kind:string;path:string;filename:string;sha256:string;byte_length:number;download_url:string;media_type?:string };
export type DeliveryReceipt = {
  operation_id:string;status:'STAGE_REPORT'|'EXPORTED_CONFIRMED_PLAN_DERIVATIVES';
  session_ref?:RevisionRef;proposal_ref?:RevisionRef;discovery_ref?:RevisionRef;
  report:WordResult;diagrams:DiagramArtifact[];artifacts:DeliveryAsset[];project_files:DeliveryFile[];
  harness?:{workspace_ref:DeliveryWorkspace;version_directory:string;files:DeliveryFile[];[key:string]:unknown};
  version_directory?:string;manifest?:DeliveryFile;taskbook_ref?:{taskbook_id:string;revision:number;sha256?:string};
  pending_count?:number;blocking_count?:number;basis_current:boolean;current_review_status:string;
  file_checks:{path:string;status:string;source_ref?:SourceRef;[key:string]:unknown}[];
  historical_receipt_preserved:boolean;execution_authorized:false;[key:string]:unknown;
};
export type DeliveryIncomplete = {operation_id:string;status:'FAILED_OR_PARTIAL'|'PROCESSING'|'INTERRUPTED_REVIEW_REQUIRED';kind?:string;steps?:Record<string,unknown>;failures?:unknown[];automatic_model_replay:false;[key:string]:unknown};
export type DeliveryOperation = DeliveryReceipt | DeliveryIncomplete;
export type DeliverySessionEntry = {operation_id:string;session_ref:RevisionRef;created_at:string;status:string;basis_current:boolean|null;current_review_status:string|null};
export type DeliverySessionOperations = {project_id:string;session_ref:RevisionRef;entries:DeliverySessionEntry[];complete:true};
/** A complete directory locates original claims; it never qualifies an operation as successful. */
export function assertDeliverySessionOperations(value:unknown,pid:string,basis:RevisionRef):DeliverySessionOperations {
  const result=value as DeliverySessionOperations|undefined,keys=new Set<string>();
  if(!nativeReference(basis)||!result||result.project_id!==pid||!sameNativeReference(result.session_ref,basis)||result.complete!==true||!Array.isArray(result.entries)||result.entries.length>64)throw new Error('交付目录与当前需求版本不一致，请刷新原讨论；已保存回执保留。');
  for(const item of result.entries){
    if(!item||typeof item.operation_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(item.operation_id)||keys.has(item.operation_id)||!nativeReference(item.session_ref)||item.session_ref.id!==basis.id||item.session_ref.revision>basis.revision||item.session_ref.revision===basis.revision&&!sameNativeReference(item.session_ref,basis)||typeof item.created_at!=='string'||!item.created_at.length||item.created_at.length>100||typeof item.status!=='string'||!item.status.length||item.status.length>100||item.basis_current!==null&&typeof item.basis_current!=='boolean'||item.current_review_status!==null&&(typeof item.current_review_status!=='string'||!item.current_review_status.length||item.current_review_status.length>100))throw new Error('交付目录响应不完整，原操作保留，未重新生成。');
    keys.add(item.operation_id);
  }
  return result;
}
export type ExportResume = {action:'RESUME_EXPORT';operation_id:string;project_id:string;request_sha256:string;proposal_ref:RevisionRef};
/** A server-issued observation authorizes only an explicit attempt of this saved request. */
export function resumableExport(value:DeliveryOperation,projectId:string):ExportResume|undefined {
  if(deliveryComplete(value)||value.status!=='FAILED_OR_PARTIAL'||value.kind!=='EXPORT'||value.automatic_model_replay!==false||value.project_id!==projectId||!/^[A-Za-z0-9_-]{1,100}$/.test(value.operation_id))return;
  const resume=value.export_resume as ExportResume|undefined;
  if(!resume||resume.action!=='RESUME_EXPORT'||resume.project_id!==projectId||resume.operation_id!==value.operation_id||!/^[a-f0-9]{64}$/.test(resume.request_sha256)||resume.request_sha256!==value.request_sha256||!nativeReference(resume.proposal_ref)||!sameNativeReference(resume.proposal_ref,value.proposal_ref as RevisionRef))return;
  return resume;
}
/** Adopt only a current incomplete observation bound to this exact original request. */
export function originalExportObservation(value:unknown,resume:ExportResume):DeliveryIncomplete|undefined {
  try {
    const observed=assertDeliveryOperation(value,resume.operation_id);
    if(deliveryComplete(observed)||observed.kind!=='EXPORT'||observed.project_id!==resume.project_id||observed.request_sha256!==resume.request_sha256||!sameNativeReference(observed.proposal_ref as RevisionRef,resume.proposal_ref))return;
    return observed;
  }catch{return;}
}
export function assertResumedExport(value:unknown,resume:ExportResume):DeliveryReceipt {
  const result=assertDeliveryOperation(value,resume.operation_id);
  if(!deliveryComplete(result)||result.status!=='EXPORTED_CONFIRMED_PLAN_DERIVATIVES'||result.project_id!==undefined&&result.project_id!==resume.project_id||!sameNativeReference(result.proposal_ref,resume.proposal_ref)||[...result.harness?.files??[],...result.project_files].some(file=>[file.file_source_ref,file.source_ref].some(source=>source!==undefined&&(!deliverySource(source)||source.project_id!==resume.project_id||source.path!==file.path||source.sha256!==file.sha256))))throw new Error('原交付回执不属于当前操作与来源，未采用该结果。');
  return result;
}

export type StageOptions = {template?:SourceRef;experience?:RevisionRef;graphs?:string[];workspace?:Workspace;directory?:string};
export type ExportOptions = {template?:SourceRef;experience?:RevisionRef;workspace:Workspace;directory:string};
const reference = (value:{id:string;revision:number;sha256:string}):RevisionRef=>({id:value.id,revision:value.revision,sha256:value.sha256});
export function deliveryComplete(value:DeliveryOperation):value is DeliveryReceipt{return value.status==='STAGE_REPORT'||value.status==='EXPORTED_CONFIRMED_PLAN_DERIVATIVES';}
export type SavedDeliveryReport={report:Pick<WordResult,'artifact'>;basis:RevisionRef;sourceKind:'PROPOSAL'|'DISCOVERY'};
/** Read an already committed report step without promoting its parent to success. */
export function savedDeliveryReport(value:DeliveryOperation,projectId:string):SavedDeliveryReport|undefined {
  if(deliveryComplete(value)||!['FAILED_OR_PARTIAL','PROCESSING','INTERRUPTED_REVIEW_REQUIRED'].includes(value.status)||value.automatic_model_replay!==false||!['EXPORT','STAGE'].includes(value.kind??'')||!/^[A-Za-z0-9_-]{1,100}$/.test(projectId))return;
  const report=value.steps?.report as {source_kind?:unknown;proposal_ref?:RevisionRef;session_ref?:RevisionRef;artifact?:WordResult['artifact'];status?:unknown}|undefined;
  if(!report||typeof report.status!=='string'||!['DRAFT','CONFIRMED'].includes(report.status))return;
  const sourceKind=value.kind==='EXPORT'?'PROPOSAL':'DISCOVERY',basis=sourceKind==='PROPOSAL'?report.proposal_ref:report.session_ref;
  if(report.source_kind!==sourceKind||!nativeReference(basis)||!/^[A-Za-z0-9_-]{1,100}$/.test(basis.id))return;
  const item=report.artifact;
  if(!item||typeof item.id!=='string'||!/^[a-f0-9]{64}$/.test(item.id)||typeof item.sha256!=='string'||!/^[a-f0-9]{64}$/.test(item.sha256)||!Number.isSafeInteger(item.byte_length)||item.byte_length<1||item.byte_length>64*1024*1024||typeof item.filename!=='string'||!item.filename.length||item.filename.length>1024||/[\0\r\n]/.test(item.filename)||!/\.docx$/i.test(item.filename)||item.media_type!=='application/vnd.openxmlformats-officedocument.wordprocessingml.document')return;
  const segment=sourceKind==='PROPOSAL'?'planning/proposals':'discovery/sessions';
  if(item.download_url!==`/api/projects/${projectId}/${segment}/${basis.id}/reports/${item.id}/download`)return;
  return {report:{artifact:item},basis,sourceKind};
}
export function deliverySource(value:unknown):value is SourceRef {
  const ref=value as SourceRef|undefined;
  return !!ref&&ref.kind==='local_file'&&typeof ref.project_id==='string'&&!!ref.project_id&&typeof ref.workspace_id==='string'&&!!ref.workspace_id&&typeof ref.path==='string'&&!!ref.path&&Number.isSafeInteger(ref.binding_revision)&&ref.binding_revision>0&&/^[a-f0-9]{64}$/.test(ref.binding_sha256)&&nativeReference({id:ref.path,revision:ref.revision,sha256:ref.sha256});
}
export function assertDeliveryOperation(value:unknown,key:string):DeliveryOperation {
  const result=value as DeliveryOperation|undefined;
  if(!result||result.operation_id!==key||typeof result.status!=='string')throw new Error('交付回执未绑定本次操作；请读取原操作回执，勿重复生成');
  if(deliveryComplete(result)) {
    const basis=result.status==='STAGE_REPORT'?result.session_ref:result.proposal_ref;
    if(!nativeReference(basis)||!result.report?.artifact?.id||!/^[a-f0-9]{64}$/.test(result.report.artifact.sha256)||!Array.isArray(result.project_files)||!Array.isArray(result.artifacts)||!Array.isArray(result.diagrams)||!Array.isArray(result.file_checks)||typeof result.basis_current!=='boolean'||typeof result.current_review_status!=='string'||result.execution_authorized!==false)throw new Error('交付结果不完整，不能声明已完成；请核对原操作回执');
    if(result.status==='STAGE_REPORT'&&(!Number.isSafeInteger(result.pending_count)||result.pending_count!<0||!Number.isSafeInteger(result.blocking_count)||result.blocking_count!<0))throw new Error('阶段回执未提供实际待确认数量，请核对原操作');
    if(result.status==='EXPORTED_CONFIRMED_PLAN_DERIVATIVES'&&(!result.harness||!Array.isArray(result.harness.files)||!result.harness.files.length||!result.version_directory||!result.manifest||!result.taskbook_ref?.taskbook_id||!Number.isSafeInteger(result.taskbook_ref.revision)||result.taskbook_ref.revision<1))throw new Error('完整交付回执缺少 harness、任务书或提交标记，不能声明已完成');
    for(const item of [...result.project_files,...result.harness?.files??[]]){const source=item.file_source_ref??item.source_ref;if(typeof item.path!=='string'||!/^[a-f0-9]{64}$/.test(item.sha256)||(source&&(!deliverySource(source)||source.path!==item.path||source.sha256!==item.sha256)))throw new Error('交付文件缺少一致的实际来源，请核对回执');}
  } else if(!['FAILED_OR_PARTIAL','PROCESSING','INTERRUPTED_REVIEW_REQUIRED'].includes(result.status)||result.automatic_model_replay!==false)throw new Error('未知交付状态，请核对原操作回执');
  return result;
}
export function deliveryFiles(result:DeliveryOperation):DeliveryFile[] {
  if(!deliveryComplete(result))return [];
  const unique=new Map<string,DeliveryFile>();
  for(const item of [...result.harness?.files??[],...result.project_files])unique.set(item.path,item);
  return [...unique.values()];
}
export function deliveryFileStatus(result:DeliveryReceipt,file:DeliveryFile):string{return result.file_checks.find(item=>item.path===file.path)?.status??'UNKNOWN';}
export function deliveryDirectoryAllowed(workspace:Workspace|undefined,directory:string):boolean {
  if(!workspace||!directory||directory.startsWith('/')||directory.includes('\\')||directory.split('/').some(part=>!part||part==='.'||part==='..')||directory.includes(':'))return false;
  return workspace.allowed_write_paths.some(path=>path==='*'||path.endsWith('/')&&(directory+'/').startsWith(path));
}
export class DeliveryError extends AuthoredUIError {
  readonly operationId:string;readonly receipt?:DeliveryOperation;
  constructor(message:string,key:string,receipt?:DeliveryOperation,copy?:AuthoredUICopy){super(copy?.message_key??'{message}',copy?.params??{message});this.name='DeliveryError';this.operationId=key;this.receipt=receipt;}
}
export class DeliveryGateError extends ApiError {
  readonly operationId:string;readonly preclaim:boolean;readonly receipt?:DeliveryOperation;
  constructor(error:ApiError,key:string,preclaim:boolean,receipt?:DeliveryOperation){super(error.status,error.code,error.message,error.detail);this.name='DeliveryGateError';this.operationId=key;this.preclaim=preclaim;this.receipt=receipt;}
}
export function deliveryHistoryKey(pid:string,kind:'STAGE'|'EXPORT',id:string){return `eeas-delivery-history:${pid}:${kind}:${id}`;}
export function readDeliveryHistory(storage:Pick<Storage,'getItem'>,pid:string,kind:'STAGE'|'EXPORT',id:string):string[]{try{const value=JSON.parse(storage.getItem(deliveryHistoryKey(pid,kind,id))??'[]');return Array.isArray(value)?[...new Set(value.filter(item=>typeof item==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(item)))].slice(-20):[];}catch{return [];}}
export function saveDeliveryHistory(storage:Pick<Storage,'getItem'|'setItem'>,pid:string,kind:'STAGE'|'EXPORT',id:string,key:string){const previous=readDeliveryHistory(storage,pid,kind,id);storage.setItem(deliveryHistoryKey(pid,kind,id),JSON.stringify([...previous.filter(item=>item!==key),key].slice(-20)));return previous.includes(key)?false:previous;}
export function discardPreclaimDelivery(storage:Pick<Storage,'getItem'|'setItem'>,pid:string,id:string,key:string,error:unknown,prepared:string[]|false){if(prepared!==false&&error instanceof DeliveryGateError&&error.operationId===key&&error.preclaim)storage.setItem(deliveryHistoryKey(pid,'EXPORT',id),JSON.stringify([...new Set([...prepared,...readDeliveryHistory(storage,pid,'EXPORT',id).filter(item=>item!==key)])].slice(-20)));}


export class PlanningDeliveryApi {
  private client:ApiClient;private discovery:DiscoveryApi;
  constructor(client:ApiClient=api){this.client=client;this.discovery=new DiscoveryApi(client);}
  root(pid:string){return `${this.client.projectPath(pid)}/planning-delivery`;}
  async operation(pid:string,key:string,signal?:AbortSignal){return assertDeliveryOperation(await this.client.request<DeliveryOperation>(`${this.root(pid)}/operations/${encodeURIComponent(key)}`,'GET',undefined,signal),key);}
  async sessionOperations(pid:string,basis:RevisionRef,signal?:AbortSignal):Promise<DeliverySessionOperations>{
    if(!nativeReference(basis))throw new Error('读取交付目录需要实际需求版本');
    const query=new URLSearchParams({revision:String(basis.revision),sha256:basis.sha256});
    return assertDeliverySessionOperations(await this.client.request<unknown>(`${this.root(pid)}/sessions/${encodeURIComponent(basis.id)}/operations?${query}`,'GET',undefined,signal),pid,basis);
  }
  async resumeExport(pid:string,operation:DeliveryOperation):Promise<DeliveryReceipt> {
    const resume=resumableExport(operation,pid);
    if(!resume)throw new Error('只能继续已核对的原失败交付；请先读取原回执。');
    try {
      return assertResumedExport(await this.client.request<DeliveryOperation>(`${this.root(pid)}/operations/${encodeURIComponent(resume.operation_id)}/resume-export`,'POST',{request_sha256:resume.request_sha256}),resume);
    }catch(error){
      let receipt:DeliveryOperation|undefined,failure:unknown=error;try{receipt=await this.operation(pid,resume.operation_id);}catch{}
      // A committed original GET resolves a lost POST reply; it never causes another write.
      if(receipt&&deliveryComplete(receipt))try{return assertResumedExport(receipt,resume);}catch(invalid){failure=invalid;receipt=undefined;}
      const state=authoredCopy(failure instanceof ApiError&&failure.code==='BUSY'?'原交付正在处理中；本次未启动第二次继续。':'原交付未继续完成；已保存报告与原步骤保留，请核对原回执。');
      throw new DeliveryError(`${failure instanceof Error?failure.message:String(failure)}（操作 ${resume.operation_id}；可读取已保存步骤，不自动重放）`,resume.operation_id,receipt,authoredCopy('{state} {error}（操作 {operation}；可读取已保存步骤，不自动重放）',{state,error:authoredErrorCopy(failure),operation:resume.operation_id}));
    }
  }
  private async write(pid:string,path:string,body:{operation_id:string;[key:string]:unknown},basis:RevisionRef,beforePost?:()=>void) {
    beforePost?.();
    try {
      const result=assertDeliveryOperation(await this.client.request<DeliveryOperation>(path,'POST',body),body.operation_id);
      if(!deliveryComplete(result)||!sameNativeReference(result.session_ref??result.proposal_ref,basis))throw new Error('交付回执对应另一来源版本，请读取原回执核对');
      return result;
    }catch(error){if(error instanceof ApiError&&error.status===409&&['MANUAL_REVIEW_REQUIRED','SOURCE_DRIFT','UNVERIFIED'].includes(error.code)){
      const attempt=(error.detail as {detail?:{delivery_attempt?:{phase?:string;operation_id?:string;project_id?:string;proposal_ref?:RevisionRef;workspace_ref?:DeliveryWorkspace}}})?.detail?.delivery_attempt,w=body.workspace_ref as DeliveryWorkspace|undefined;
      const preclaim=attempt?.phase==='PRECLAIM'&&attempt.operation_id===body.operation_id&&attempt.project_id===pid&&sameNativeReference(attempt.proposal_ref,basis)&&!!w&&attempt.workspace_ref?.id===w.id&&attempt.workspace_ref.revision===w.revision&&attempt.workspace_ref.binding_sha256===w.binding_sha256;
      let receipt:DeliveryOperation|undefined;if(!preclaim)try{receipt=await this.operation(pid,body.operation_id);}catch{}
      throw new DeliveryGateError(error,body.operation_id,preclaim,receipt);
    }let result:DeliveryOperation|undefined;try{result=await this.operation(pid,body.operation_id);}catch{}throw new DeliveryError(`${error instanceof Error?error.message:String(error)}（操作 ${body.operation_id}；可读取已保存步骤，不自动重放）`,body.operation_id,result,authoredCopy('{error}（操作 {operation}；可读取已保存步骤，不自动重放）',{error:authoredErrorCopy(error),operation:body.operation_id}));}
  }
  async stage(session:DiscoverySession,options:StageOptions={},key=operationId()) {
    const latest=await this.discovery.get(session.project_id,session.id),basis=reference(session);
    if(!sameNativeReference(reference(latest),basis)||latest.current_revision!==basis.revision)throw new Error('需求版本已变化，请读取现行版本；本次未生成新产物');
    if(!!options.workspace!==!!options.directory)throw new Error('保存到目录需要同时指定实际工作目录与相对交付目录');
    if(options.workspace&&(options.workspace.project_id!==session.project_id||!deliveryDirectoryAllowed(options.workspace,options.directory!)))throw new Error('交付目录未获得当前项目的写入范围');
    if(options.template&&options.template.project_id!==session.project_id)throw new Error('模板不属于当前项目');
    return this.write(session.project_id,`${this.root(session.project_id)}/sessions/${encodeURIComponent(session.id)}/stage`,{operation_id:key,session_ref:basis,...options.template?{template_ref:options.template}:{},...options.experience?{experience_ref:options.experience}:{},graph_ids:options.graphs??[],...options.workspace?{workspace_ref:workspaceRef(options.workspace),directory:options.directory}:{}},basis);
  }
  async export(proposal:PlanningProposal,options:ExportOptions,key=operationId(),beforePost?:()=>void) {
    const latest=await this.client.proposal(proposal.project_id,proposal.id),basis=reference(proposal);
    if(!sameNativeReference(reference(latest),basis)||latest.current_revision!==basis.revision||latest.payload.status!=='CONFIRMED'||latest.review_status!=='CURRENT')throw new Error('完整交付需要现行已确认且来源未变化的计划；请读取现行版本，本次未导出');
    if(options.workspace.project_id!==proposal.project_id||!deliveryDirectoryAllowed(options.workspace,options.directory))throw new Error('交付目录未获得当前项目的写入范围');
    if(options.template&&options.template.project_id!==proposal.project_id)throw new Error('模板不属于当前项目');
    return this.write(proposal.project_id,`${this.root(proposal.project_id)}/proposals/${encodeURIComponent(proposal.id)}/export`,{operation_id:key,proposal_ref:basis,workspace_ref:workspaceRef(options.workspace),directory:options.directory,...options.template?{template_ref:options.template}:{},...options.experience?{experience_ref:options.experience}:{}},basis,beforePost);
  }
  async readFile(pid:string,result:DeliveryReceipt,file:DeliveryFile):Promise<FileContent> {
    if(!deliveryFiles(result).some(item=>item.path===file.path&&item.sha256===file.sha256))throw new Error('文件不属于此次交付回执');
    const expected=file.file_source_ref??file.source_ref,workspace=expected?.workspace_id??result.harness?.workspace_ref.id;
    if(!workspace||expected&&expected.project_id!==pid)throw new Error('尚未返回此文件的实际工作目录来源');
    const value=await this.client.file(pid,workspace,file.path),actual=value.source_ref;
    if(!value.current||!deliverySource(actual)||actual.project_id!==pid||actual.workspace_id!==workspace||actual.path!==file.path||actual.sha256!==file.sha256)throw new Error('交付文件已变化，当前人工修改保留；请核对实际文件后重新定位');
    if(expected&&(['kind','project_id','workspace_id','binding_revision','binding_sha256','path','revision','sha256'] as const).some(key=>actual[key]!==expected[key]))throw new Error('交付文件来源版本已变化，原回执保留；当前文件未自动打开');
    if(!expected&&result.harness&&(actual.binding_revision!==result.harness.workspace_ref.revision||actual.binding_sha256!==result.harness.workspace_ref.binding_sha256))throw new Error('旧交付记录的目录绑定已变化，请核对当前来源');
    return value;
  }
  async readCurrentFile(pid:string,result:DeliveryReceipt,file:DeliveryFile):Promise<FileContent>{
    if(!deliveryFiles(result).some(item=>item.path===file.path&&item.sha256===file.sha256))throw new Error('文件不属于此次交付回执');
    const original=file.file_source_ref??file.source_ref;
    if(!deliverySource(original)||original.project_id!==pid)throw new Error('尚未返回此文件的实际工作目录来源');
    const value=await this.client.file(pid,original.workspace_id,original.path),current=value.source_ref;
    if(!value.current||!deliverySource(current)||(['kind','project_id','workspace_id','binding_revision','binding_sha256','path'] as const).some(key=>current[key]!==original[key]))throw new Error('文件位置或目录绑定已变化，原稿保留；请明确重新选择。');
    return value;
  }
}
export const planningDeliveryApi=new PlanningDeliveryApi();
