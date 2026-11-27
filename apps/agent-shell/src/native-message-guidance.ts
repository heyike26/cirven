import type {NativeMessage,NativeRef} from './native-collaboration';
import {sameReview,type ReviewControl} from './native-review.ts';
export type OrdinaryControlSummary={operation_id:string;raw_text:string;intent?:string;status:string;role?:string;local_status?:Record<string,unknown>};
export type OrdinaryControlScope={ref:NativeRef;can_guide:boolean;can_status:boolean;can_cancel:boolean};
const validRef=(v:unknown):v is NativeRef=>!!v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')==='id,revision,sha256'&&typeof (v as NativeRef).id==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test((v as NativeRef).id)&&(v as NativeRef).revision===1&&/^[a-f0-9]{64}$/.test((v as NativeRef).sha256);
export function ordinaryControlScope(message:NativeMessage|undefined):OrdinaryControlScope|null{
 if(!message||message.review||!validRef(message.native_message_ref)||message.native_message_ref.id!==message.operation_id)return null;
 const value=message.control_capabilities as Record<string,unknown>|undefined;
 if(!value||['can_guide','can_status','can_cancel'].some(key=>typeof value[key]!=='boolean'))return null;
 return {ref:message.native_message_ref,can_guide:value.can_guide===true,can_status:value.can_status===true,can_cancel:value.can_cancel===true};
}
export function ordinaryControlSummaries(message:NativeMessage):OrdinaryControlSummary[]{
 return Array.isArray(message.native_message_controls)?message.native_message_controls.slice(-16).filter((row):row is OrdinaryControlSummary=>!!row&&typeof row==='object'&&typeof row.operation_id==='string'&&typeof row.raw_text==='string'&&typeof row.status==='string'):[];
}
export function controlNotice(receipt:{status:string;role?:unknown;local_status?:unknown}):string{
 const local=receipt.local_status as Record<string,unknown>|undefined;
 if(receipt.role==='LOCAL_STATUS')return local?.active_operation_id?'任务仍在运行':'任务当前空闲';
 if(receipt.status==='COHORT_RETURNED')return '任务已返回';
 if(['ACCEPTED','OBSERVED','NEXT_TURN_ASSOCIATED'].includes(receipt.status))return '补充已送达';
 if(['RETURNED','COMPLETED'].includes(receipt.status))return '补充已有回复';
 return '核查原操作';
}
const INDEX='eeas-native-review-control-index-v1';
function index(storage:Pick<Storage,'getItem'>):Record<string,ReviewControl>{
 const raw=storage.getItem(INDEX)??'{}';if(raw.length>262144)throw new Error('结果尚未确认，请核查原操作');
 const value=JSON.parse(raw);if(!value||Array.isArray(value)||typeof value!=='object'||Object.keys(value).length>16)throw new Error('结果尚未确认，请核查原操作');return value;
}
export async function dispatchOrdinaryGuidance(storage:Pick<Storage,'getItem'|'setItem'>,parent:NativeMessage,conversation:string,rawText:string,operation:string,send:(request:ReviewControl)=>Promise<{operation_id:string;status:string;[key:string]:unknown}>){
 const scope=ordinaryControlScope(parent);if(!scope||Array.from(rawText).length>16000)throw new Error('当前任务暂不能补充，请先核查原操作');
 const values=index(storage),slot='guidance:'+conversation+':'+parent.operation_id;
 const retained=(parent.local_review_controls as ReviewControl[]|undefined)?.find(row=>row.kind==='guidance'&&row.parentOperationId===parent.operation_id);
 let request=values[slot]??retained;
 if(request){
  if(request.parentOperationId!==parent.operation_id||request.kind!=='guidance'||request.body.expected_review_ref||!sameReview(request.body.expected_native_message_ref,scope.ref))throw new Error('结果尚未确认，请核查原操作');
  request={...request,inspect:true};
 }else{
  if(!(scope.can_guide||scope.can_status)||!rawText.trim())throw new Error('当前任务暂不能补充，请先核查原操作');
  if(Object.keys(values).length>=16)throw new Error('结果尚未确认，请核查原操作');
  request={parentOperationId:parent.operation_id,kind:'guidance',body:{operation_id:operation,expected_native_message_ref:scope.ref,raw_text:rawText,intent:'AUTO'}};
  values[slot]=request;const serialized=JSON.stringify(values);if(serialized.length>262144)throw new Error('问题索引未能保存，尚未发送');storage.setItem(INDEX,serialized);if(storage.getItem(INDEX)!==serialized)throw new Error('问题索引未能保存，尚未发送');
 }
 const result=await send(request);if(result.operation_id!==request.body.operation_id)throw new Error('结果尚未确认，请核查原操作');
 if(['ACCEPTED','OBSERVED','NEXT_TURN_ASSOCIATED','COHORT_RETURNED','RETURNED','COMPLETED','FAILED_NOT_DISPATCHED','FAILED','REJECTED','CANCELLED'].includes(result.status)){
  const latest=index(storage);if(sameReview(latest[slot],values[slot])){delete latest[slot];storage.setItem(INDEX,JSON.stringify(latest));}
 }
 return {result,submittedText:request.body.raw_text};
}
