import {appendActionRequest,type AppendPrepare} from './native-append.ts';
import {nativeReference,sameNativeReference,type NativeMessage,type NativeRef} from './native-collaboration.ts';

export type NativeProposalScope={projectId:string|null;conversationId:string};
export type NativeProposalCheck={status:'ABSENT';identity:string;validationKey:string}|{status:'READONLY'|'INVALID';identity:string;validationKey:string;reason:string}|{status:'CANDIDATE';identity:string;validationKey:string;request:AppendPrepare;rawTextSha256:string};
export type NativeProposalAssessment=Exclude<NativeProposalCheck,{status:'CANDIDATE'}>|{status:'APPEND';identity:string;validationKey:string;request:AppendPrepare};
const id=(value:unknown):value is string=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
const sha=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const object=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
const exact=(value:unknown,keys:string[])=>object(value)&&Object.keys(value).sort().join(',')===[...keys].sort().join(',');
const maybeRef=(value:unknown):value is NativeRef|null=>value===null||nativeReference(value);
const sameRef=(a:NativeRef|null,b:NativeRef|null)=>a===null&&b===null||a!==null&&b!==null&&sameNativeReference(a,b);

export function checkNativeActionProposal(message:NativeMessage,scope:NativeProposalScope,contextRef?:NativeRef):NativeProposalCheck{
  const identity=JSON.stringify([message.operation_id,message.request_sha256]);
  const result=(status:'ABSENT'|'READONLY'|'INVALID',reason=''):NativeProposalCheck=>status==='ABSENT'?{status,identity,validationKey:identity+':ABSENT'}:{status,identity,reason,validationKey:identity+':'+status+':'+reason};
  const values=message.native_action_proposals;
  if(values===undefined||Array.isArray(values)&&values.length===0)return result('ABSENT');
  if(!Array.isArray(values)||values.length!==1)return result('INVALID','INVALID_PROPOSAL');
  if(!id(message.operation_id)||!sha(message.request_sha256)||!id(scope.conversationId)||!(scope.projectId===null||id(scope.projectId))||message.project_id!==scope.projectId||message.conversation_id!==scope.conversationId)return result('INVALID','SCOPE_MISMATCH');
  const row=values[0];
  if(!exact(row,['kind','parent_operation_id','parent_request_sha256','proposal_ref','status','reason_code','automatic_retry','execution_authorized','source','request'])||row.kind!=='CLASSIC_NOTEPAD_APPEND'||row.parent_operation_id!==message.operation_id||row.parent_request_sha256!==message.request_sha256||row.automatic_retry!==false||row.execution_authorized!==false||!['PROPOSED','READONLY'].includes(String(row.status))||!(row.reason_code===null||typeof row.reason_code==='string'&&/^[A-Z0-9_]{1,100}$/.test(row.reason_code)))return result('INVALID','INVALID_PROPOSAL');
  const reference=row.proposal_ref,source=row.source;
  if(!nativeReference(reference)||reference.revision!==1||reference.id!==reference.sha256)return result('INVALID','INVALID_PROPOSAL_REFERENCE');
  if(!exact(source,['kind','operation_id','request_sha256','project_id','conversation_id','context_ref','provider_profile_ref','raw_text_sha256'])||source.kind!=='NATIVE_MESSAGE'||source.operation_id!==message.operation_id||source.request_sha256!==message.request_sha256||!(source.project_id===null||id(source.project_id))||source.project_id!==message.request_project_id||source.conversation_id!==message.conversation_id||!sha(source.raw_text_sha256)||!nativeReference(source.context_ref)||!nativeReference(message.context_ref)||!sameNativeReference(source.context_ref,message.context_ref)||!maybeRef(source.provider_profile_ref)||!maybeRef(message.provider_profile_ref)||!sameRef(source.provider_profile_ref,message.provider_profile_ref))return result('INVALID','SOURCE_MISMATCH');
  if(row.status==='READONLY')return row.request===null?result('READONLY',typeof row.reason_code==='string'?row.reason_code:'READONLY'):result('INVALID','INVALID_PROPOSAL');
  if(row.reason_code!==null)return result('INVALID','INVALID_PROPOSAL');
  if(message.record_origin!=='CURRENT_HOST'||message.status!=='RETURNED'||message.automatic_retry!==false||message.automatic_input_authorized!==false)return result('READONLY','PARENT_NOT_ACTIONABLE');
  let request:AppendPrepare;try{request=appendActionRequest(row.request);}catch{return result('INVALID','INVALID_APPEND_REQUEST');}
  if(!/^native-append-[a-f0-9]{48}$/.test(request.operation_id)||request.operation_id===message.operation_id||!sameNativeReference(request.context_ref,source.context_ref)||!sameRef(request.provider_profile_ref??null,source.provider_profile_ref))return result('INVALID','SOURCE_MISMATCH');
  if(!contextRef||!sameNativeReference(contextRef,request.context_ref))return result('READONLY','CONTEXT_CHANGED');
  return {status:'CANDIDATE',identity,validationKey:JSON.stringify([identity,reference.id,source.raw_text_sha256,request]),request,rawTextSha256:source.raw_text_sha256};
}

export async function verifyNativeActionProposal(value:NativeProposalCheck,digest?:SubtleCrypto['digest']):Promise<NativeProposalAssessment>{
  if(value.status!=='CANDIDATE')return value;
  try{
    const hash=digest??globalThis.crypto.subtle.digest.bind(globalThis.crypto.subtle);
    const actual=Array.from(new Uint8Array(await hash('SHA-256',new TextEncoder().encode(value.request.raw_text))),byte=>byte.toString(16).padStart(2,'0')).join('');
    if(actual===value.rawTextSha256)return {status:'APPEND',identity:value.identity,validationKey:value.validationKey,request:value.request};
  }catch{}
  return {status:'INVALID',identity:value.identity,validationKey:value.validationKey+':HASH_FAILED',reason:'SOURCE_MISMATCH'};
}
