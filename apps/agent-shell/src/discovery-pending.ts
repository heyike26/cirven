import type {DocumentRef} from './contracts';
import type {RevisionRef} from './workbench-contracts';

/** Local UI hints only. Execution identity and basis always come from the server. */
export type DiscoveryInput={text:string;materials:string[];feedback:string[]};
export type DiscoveryPending={id:string;sessionId:string;basisRevision?:number;retainedInput?:DiscoveryInput};
type Storage=Pick<globalThis.Storage,'getItem'|'setItem'|'removeItem'>;
const key=(projectId:string)=>`eeas-discovery-operation:${projectId}`;
const normalized=(text:string)=>text.replace(/\r\n?/g,'\n').trim();
export function discoveryInput(text:string,materials:DocumentRef[],feedback:RevisionRef[]):DiscoveryInput{
  return {text:normalized(text),materials:materials.map(r=>JSON.stringify([r.document_id,r.revision,r.sha256])).sort(),feedback:feedback.map(r=>JSON.stringify([r.id,r.revision,r.sha256])).sort()};
}
export function sameDiscoveryInput(a:DiscoveryInput,b:DiscoveryInput):boolean{return JSON.stringify(a)===JSON.stringify(b);}
function validInput(value:unknown):value is DiscoveryInput{
  if(!value||typeof value!=='object')return false;const v=value as DiscoveryInput;
  return typeof v.text==='string'&&v.text.length<=128000&&[v.materials,v.feedback].every((items,index)=>Array.isArray(items)&&items.length<=(index?20:19)&&items.every(x=>typeof x==='string'&&x.length<=2048));
}
export function readDiscoveryPending(storage:Storage,projectId:string):DiscoveryPending|undefined{
  const raw=storage.getItem(key(projectId));if(raw===null)return;
  if(raw.length>262144)throw new Error('本机处理恢复记录无法读取，原输入保留。');
  let v:DiscoveryPending;try{v=JSON.parse(raw);}catch{throw new Error('本机处理恢复记录无法读取，原输入保留。');}
  if(!v||typeof v.id!=='string'||!v.id||v.id.length>256||typeof v.sessionId!=='string'||!v.sessionId||v.sessionId.length>256||v.basisRevision!==undefined&&(!Number.isSafeInteger(v.basisRevision)||v.basisRevision<1)||v.retainedInput!==undefined&&!validInput(v.retainedInput))throw new Error('本机处理恢复记录无法读取，原输入保留。');
  return {id:v.id,sessionId:v.sessionId,...(v.basisRevision!==undefined?{basisRevision:v.basisRevision}:{}),...(v.retainedInput?{retainedInput:v.retainedInput}:{})};
}
export function saveDiscoveryPending(storage:Storage,projectId:string,value:DiscoveryPending):void{
  // Preserve malformed historical bytes instead of silently replacing them.
  readDiscoveryPending(storage,projectId);
  if(value.retainedInput&&!validInput(value.retainedInput))throw new Error('输入范围超过本机恢复记录限制，原稿已保留。');
  const raw=JSON.stringify(value);if(raw.length>262144)throw new Error('输入范围超过本机恢复记录限制，原稿已保留。');
  storage.setItem(key(projectId),raw);if(storage.getItem(key(projectId))!==raw)throw new Error('本机未能保存处理恢复记录，原输入保留，尚未发送。');
}
export function clearDiscoveryPending(storage:Storage,projectId:string,id:string):void{
  if(readDiscoveryPending(storage,projectId)?.id!==id)return;
  storage.removeItem(key(projectId));
  if(readDiscoveryPending(storage,projectId)?.id===id)throw new Error('本机处理恢复记录未能清理。');
}
export function discoverySendGate(operation:{projectId:string;sessionId?:string;kind?:string;discoveryPurpose?:string;status:string;historicalInspection?:boolean;retainedInput?:DiscoveryInput}|undefined,projectId:string|undefined,sessionId:string|undefined,input:DiscoveryInput):'normal'|'blocked'|'new-message'{
  if(!operation||operation.kind!=='discovery'||operation.discoveryPurpose==='PLAN'||operation.projectId!==projectId||operation.sessionId!==sessionId||operation.historicalInspection||!['UNKNOWN_OUTCOME','TIMED_OUT','INTERRUPTED'].includes(operation.status))return 'normal';
  return !operation.retainedInput||sameDiscoveryInput(operation.retainedInput,input)?'blocked':'new-message';
}
