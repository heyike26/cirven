import { ApiError } from './api.ts';
import type { ArtifactEnvelope, PlanningProposal } from './contracts';
import type { DiscoverySession } from './discovery-contracts';
import { nativeReference, sameNativeReference, type NativeRef } from './native-collaboration.ts';
import { assertDeliveryOperation, deliveryComplete, deliveryFiles, readDeliveryHistory, type DeliveryOperation, type PlanningDeliveryApi } from './planning-delivery.ts';
import {authoredCopy,type AuthoredUICopy} from './authored-ui-copy.ts';

export type DeliveryTarget = { projectId: string; kind: 'STAGE' | 'EXPORT'; basis: NativeRef };
export type DeliveryDirectoryState = 'PENDING' | 'COMPLETE' | 'UNAVAILABLE';
export type DeliveryDirectoryView = { state: DeliveryDirectoryState; operationIds: string[] };
export type DeliveryHistory = { createdAt: string; basis: NativeRef; directoryKey: string };
export type DeliveryFeedItem = { id: string; title: string; body: string; title_copy?:AuthoredUICopy;body_copy?:AuthoredUICopy;status: string; artifact?: ArtifactEnvelope; deliveryHistory?: DeliveryHistory; deliveryTarget: DeliveryTarget; deliveryInspection?: {operationId:string;diagnostic:string} };
type Entity = { project_id: string; id: string; revision: number; sha256: string };
type StorageIndex = Pick<Storage, 'getItem'>;
export const deliveryTargetKey = (target: DeliveryTarget) => JSON.stringify([target.projectId, target.kind, target.basis.id, target.basis.revision, target.basis.sha256]);
function entityTarget(value: Entity, kind: DeliveryTarget['kind']): DeliveryTarget {
  const basis = { id: value.id, revision: value.revision, sha256: value.sha256 };
  if (!value.project_id || !nativeReference(basis)) throw new Error('交付恢复缺少实际来源版本');
  return { projectId: value.project_id, kind, basis };
}
export function deliveryTarget(artifact: ArtifactEnvelope): DeliveryTarget {
  if (!['discovery', 'proposal'].includes(artifact.type)) throw new Error('从实际需求工作或提案读取交付回执');
  const target = entityTarget(artifact.data as Entity, artifact.type === 'discovery' ? 'STAGE' : 'EXPORT');
  if (artifact.scope.projectId !== target.projectId || artifact.artifactId !== target.basis.id || artifact.revision !== target.basis.revision || artifact.contentHash !== target.basis.sha256) throw new Error('交付读取入口与当前来源身份不一致');
  return target;
}
export function selectedDeliverySession(pid:string,discoveryMode:boolean,active:DiscoverySession|undefined,savedId:string|null,sessions:DiscoverySession[]):DiscoverySession|undefined {
  if(!discoveryMode)return undefined;
  return active?.project_id===pid?active:sessions.find(item=>item.project_id===pid&&item.id===savedId);
}
export function loadedDeliveryTargets(pid: string, session: DiscoverySession | undefined, proposals: PlanningProposal[], opened?: ArtifactEnvelope): DeliveryTarget[] {
  const selected = session?.project_id === pid ? session : undefined;
  const explicit = opened?.type === 'proposal' && opened.scope.projectId === pid ? deliveryTarget(opened) : undefined;
  const candidates = proposals.filter(item => item.project_id === pid && (explicit ? item.id === explicit.basis.id : selected ? item.payload.discovery_ref?.id === selected.id : !item.payload.discovery_ref));
  const targets = [...selected ? [entityTarget(selected, 'STAGE')] : [], ...candidates.map(item => entityTarget(item, 'EXPORT')), ...explicit ? [explicit] : []];
  return [...new Map(targets.map(target => [JSON.stringify([target.projectId,target.kind,target.basis.id]),target])).values()];
}
export function verifyDeliveryTarget(value: DeliveryOperation, target: DeliveryTarget, key: string): DeliveryOperation {
  const result = assertDeliveryOperation(value, key);
  if(result.project_id!==undefined&&result.project_id!==target.projectId)throw new Error('原操作属于另一项目，未恢复为当前交付');
  if (deliveryComplete(result)) {
    const kind = result.status === 'STAGE_REPORT' ? 'STAGE' : 'EXPORT', basis = kind === 'STAGE' ? result.session_ref : result.proposal_ref;
    if (kind !== target.kind || basis?.id !== target.basis.id || result.project_id !== undefined && result.project_id !== target.projectId || deliveryFiles(result).some(file => (file.file_source_ref ?? file.source_ref)?.project_id !== undefined && (file.file_source_ref ?? file.source_ref)!.project_id !== target.projectId)) throw new Error('原操作属于另一项目或来源；未恢复为当前交付');
  } else {
    const basis=target.kind==='STAGE'?result.session_ref:result.proposal_ref,other=target.kind==='STAGE'?result.proposal_ref:result.session_ref;
    if(result.kind!==target.kind||result.project_id!==target.projectId||!nativeReference(basis)||basis.id!==target.basis.id||other!==undefined)throw new Error('原操作的项目与来源尚未核对；保留原编号，请核查原回执。');
  }
  return result;
}
export function deliveryFeedItem(target: DeliveryTarget, result: DeliveryOperation, restored = true): DeliveryFeedItem {
  verifyDeliveryTarget(result,target,result.operation_id);
  const complete = deliveryComplete(result), basis = complete ? result.session_ref ?? result.proposal_ref : undefined;
  const historical = complete && !sameNativeReference(basis,target.basis);
  const label = target.kind === 'STAGE' ? '阶段产物' : '交付文件';
  const body = complete ? `${restored ? '已读取原交付回执；' : ''}依据${target.kind==='STAGE'?'需求':'提案'} ${basis!.id.slice(0,12)} · v${basis!.revision}${historical ? `（当前选中 v${target.basis.revision}，历史来源保留）` : ''} · ${result.current_review_status}。${target.kind === 'STAGE' ? `待确认 ${result.pending_count} 项；` : ''}打开前核对实际文件版本。` : `${result.status === 'PROCESSING' ? '原操作仍在处理中' : result.status === 'INTERRUPTED_REVIEW_REQUIRED' ? '原操作已中断' : '原操作失败或仅完成部分步骤'}；来源关联和已保存步骤需核对，未重新生成。`;
  const body_copy=complete?authoredCopy('依据{kind} v{revision}{historical}。{pending}',{kind:authoredCopy(target.kind==='STAGE'?'需求':'提案'),revision:String(basis!.revision),historical:historical?authoredCopy('（当前选中 v{revision}，历史来源保留）',{revision:String(target.basis.revision)}):'',pending:target.kind==='STAGE'?authoredCopy('{count} 项待确认。',{count:String(result.pending_count)}):''}):authoredCopy('{state}；来源关联和已保存步骤需核对，未重新生成。',{state:authoredCopy(result.status==='PROCESSING'?'原操作仍在处理中':result.status==='INTERRUPTED_REVIEW_REQUIRED'?'原操作已中断':'原操作失败或仅完成部分步骤')});
  const title_copy=complete?authoredCopy(restored?'{label} · 回执已读取':'{label}已保存',{label:authoredCopy(label)}):authoredCopy('交付操作待核对');
  return { id:`delivery:${target.projectId}:${result.operation_id}`, title:complete ? restored ? `${label} · 回执已读取` : `${label}已保存` : '交付操作待核对', body,title_copy,body_copy,status:result.status, deliveryTarget:target,
    artifact:{artifactId:result.operation_id,type:'planning-delivery',schemaVersion:1,revision:basis?.revision??target.basis.revision,scope:{projectId:target.projectId},availability:'IMPLEMENTED',data:result} };
}
export function unavailableDeliveryItem(target: DeliveryTarget, key: string, error: unknown): DeliveryFeedItem {
  const missing = error instanceof ApiError && error.status === 404;
  const message = missing ? '未查到回执；没有记录不证明未生成文件。' : '暂时无法核对原交付回执。';
  return {id:`delivery:${target.projectId}:${key}`,title:'交付回执尚未核对',title_copy:authoredCopy('交付回执尚未核对'),status:missing?'NO_RECORD':'INSPECTION_UNAVAILABLE',deliveryTarget:target,
    body:message,body_copy:authoredCopy(message),deliveryInspection:{operationId:key,diagnostic:error instanceof Error?error.message:String(error??'')}};
}
/** Presentation identity only: structurally identical GET JSON must not reset a live card. */
function sameReceiptJSON(a:unknown,b:unknown):boolean {
  if(a===b)return true;
  if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
  const left=a as Record<string,unknown>,right=b as Record<string,unknown>,keys=Object.keys(left);
  return keys.length===Object.keys(right).length&&keys.every(key=>Object.hasOwn(right,key)&&sameReceiptJSON(left[key],right[key]));
}
export function upsertDeliveryFeed<T extends {id:string;artifact?:ArtifactEnvelope;deliveryTarget?:DeliveryTarget;deliveryHistory?:DeliveryHistory}>(items:T[], incoming:DeliveryFeedItem): (T|DeliveryFeedItem)[] {
  const index=items.findIndex(item=>item.id===incoming.id||item.artifact?.type==='planning-delivery'&&item.artifact.scope.projectId===incoming.deliveryTarget.projectId&&item.artifact.artifactId===incoming.artifact?.artifactId);
  if(index<0)return [...items,incoming];
  const prior=items[index];
  if(prior.artifact&&incoming.artifact&&sameReceiptJSON(prior.artifact,incoming.artifact))incoming={...incoming,artifact:prior.artifact};
  // A manual GET has no creation clock. Keep the original claim only for the same source.
  const observed=incoming.artifact?.data as DeliveryOperation|undefined,original=observed?.session_ref??observed?.proposal_ref;
  if(!incoming.deliveryHistory&&prior.deliveryHistory&&(!incoming.artifact||nativeReference(original)&&sameNativeReference(original,prior.deliveryHistory.basis)))incoming={...incoming,deliveryHistory:prior.deliveryHistory};
  // A tainted local index must not replace a qualified card from another source.
  if(prior.deliveryTarget&&JSON.stringify([prior.deliveryTarget.kind,prior.deliveryTarget.basis.id])!==JSON.stringify([incoming.deliveryTarget.kind,incoming.deliveryTarget.basis.id])&&(!incoming.artifact||!deliveryComplete(incoming.artifact.data as DeliveryOperation)))return items;
  // A failed refresh does not erase an already qualified original card or its files.
  if(prior.artifact?.type==='planning-delivery'&&!incoming.artifact){const retained=authoredCopy('产物来源尚未核对，原回复与文件保留。'),next:(T|DeliveryFeedItem)[]=[...items];next[index]={...prior,title:incoming.title,title_copy:incoming.title_copy,status:incoming.status,body:incoming.body+' 产物来源尚未核对，原回复与文件保留。',body_copy:authoredCopy('{state} {retained}',{state:incoming.body_copy??incoming.body,retained}),deliveryInspection:incoming.deliveryInspection};return next;}
  const next:(T|DeliveryFeedItem)[]=[...items];next[index]=incoming;return next;
}

/** A bounded GET-only pass. No polling, generation, model or export replay. */
export class DeliveryHistoryRecovery {
  private generation=0;private signature='';private controller?:AbortController;private stopped=false;private tickets=new Map<string,number>();private ticketSequence=0;
  private client:Pick<PlanningDeliveryApi,'operation'>&Partial<Pick<PlanningDeliveryApi,'sessionOperations'>>;private storage:StorageIndex;private receive:(item:DeliveryFeedItem)=>void;private directory?:(target:DeliveryTarget,state:DeliveryDirectoryState,operationIds?:string[])=>void;
  constructor(client:Pick<PlanningDeliveryApi,'operation'>&Partial<Pick<PlanningDeliveryApi,'sessionOperations'>>,storage:StorageIndex,receive:(item:DeliveryFeedItem)=>void,directory?:(target:DeliveryTarget,state:DeliveryDirectoryState,operationIds?:string[])=>void){this.client=client;this.storage=storage;this.receive=receive;this.directory=directory;}
  private ticket(target:DeliveryTarget,key:string){const id=`${target.projectId}:${key}`,number=++this.ticketSequence;this.tickets.set(id,number);return()=>this.tickets.get(id)===number;}
  async read(target:DeliveryTarget,key:string,current:()=>boolean=()=>true):Promise<DeliveryOperation>{
    if(this.stopped||!/^[A-Za-z0-9_-]{1,100}$/.test(key)||!nativeReference(target.basis))throw new Error('读取交付需要原操作编号和实际来源');
    const newest=this.ticket(target,key);
    try{const result=verifyDeliveryTarget(await this.client.operation(target.projectId,key),target,key);
      if(this.stopped||!current())throw new Error('当前项目或来源已切换，回执未加入另一聊天');
      if(!newest())throw new Error('已有更新的原回执读取，旧响应未替换当前核对');
      this.receive(deliveryFeedItem(target,result));return result;
    }catch(error){if(!this.stopped&&current()&&newest())this.receive(unavailableDeliveryItem(target,key,error));throw error;}
  }
  async restore(targets:DeliveryTarget[],options:{refresh?:boolean}={}):Promise<void>{
    if(this.stopped)return;
    const requests:{target:DeliveryTarget;key:string;claim?:NativeRef;history?:DeliveryHistory}[]=targets.flatMap(target=>readDeliveryHistory(this.storage,target.projectId,target.kind,target.basis.id).map(key=>({target,key})));
    const signature=JSON.stringify([targets.map(deliveryTargetKey),requests.map(item=>item.key)]);
    if(!options.refresh&&signature===this.signature)return;
    this.signature=signature;const generation=++this.generation;this.controller?.abort();const controller=new AbortController();this.controller=controller;
    const current=()=>!this.stopped&&generation===this.generation&&!controller.signal.aborted;
    let directoryError:unknown;const listed:DeliveryTarget[]=[];
    // Server claims survive another renderer and lost local indexes. EXPORT keeps its local recovery path.
    for(const target of targets){
      if(!current())return;
      if(target.kind!=='STAGE'||!this.client.sessionOperations)continue;
      this.directory?.(target,'PENDING');
      try{const directory=await this.client.sessionOperations(target.projectId,target.basis,controller.signal);if(!current())return;
        for(const entry of directory.entries){const history={createdAt:entry.created_at,basis:entry.session_ref,directoryKey:deliveryTargetKey(target)},known=requests.find(item=>item.key===entry.operation_id&&deliveryTargetKey(item.target)===deliveryTargetKey(target));if(known){known.claim=entry.session_ref;known.history=history;}else requests.push({target,key:entry.operation_id,claim:entry.session_ref,history});}
        listed.push(target);
      }catch(error){if(!current())return;directoryError=error;this.directory?.(target,'UNAVAILABLE');}
    }
    let cursor=0;const worker=async()=>{while(cursor<requests.length&&current()){const{target,key,claim,history}=requests[cursor++],newest=this.ticket(target,key);let item:DeliveryFeedItem;try{const result=verifyDeliveryTarget(await this.client.operation(target.projectId,key,controller.signal),target,key);
      if(claim&&!sameNativeReference(result.session_ref as NativeRef|undefined,claim))throw new Error('原交付结果与目录中的来源版本不一致，未采用该结果。');
      item=deliveryFeedItem(target,result);
    }catch(error){item=unavailableDeliveryItem(target,key,error);}if(current()&&newest())this.receive({...item,...history?{deliveryHistory:history}:{}});}};
    await Promise.all(Array.from({length:Math.min(4,requests.length)},worker));
    if(current())for(const target of listed)this.directory?.(target,'COMPLETE',requests.filter(item=>deliveryTargetKey(item.target)===deliveryTargetKey(target)).map(item=>item.key));
    if(directoryError&&current())throw directoryError;
  }
  resetView(){if(this.stopped)return;this.signature='';this.generation++;this.controller?.abort();this.controller=undefined;this.tickets.clear();}
  stop(){this.stopped=true;this.generation++;this.controller?.abort();}
}
