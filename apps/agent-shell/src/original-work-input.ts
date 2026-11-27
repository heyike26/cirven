import type {AgentSession} from './agent-sessions';
import type {WorkRequirements} from './original-work-requirements';
import {configurationCanonical as canonical,configurationHash as hash} from './conversation-configuration';

export type OriginalWorkRef={root_operation_id:string;root_sha256:string};
export type OriginalWorkInputSource=Pick<WorkRequirements,'root_basis'|'root_sha256'|'label'>;
export type OriginalWorkChoice={selection_id:string;owner:{project_id:string;session_ref:AgentSession['session_ref'];workspace_ref:{id:string;revision:number;binding_sha256:string}};reference:OriginalWorkRef;label:string};
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:unknown,keys:string[]):v is Record<string,unknown>=>object(v)&&Object.keys(v).length===keys.length&&Object.keys(v).every(k=>keys.includes(k));
const id=(v:unknown):v is string=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v),sha=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const revision=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=1;
function check(v:unknown):asserts v{if(!v)throw Error('ORIGINAL_WORK_INPUT_CHANGED');}
export function originalWorkRef(v:unknown):OriginalWorkRef|undefined{if(v===undefined)return undefined;check(exact(v,['root_operation_id','root_sha256'])&&id(v.root_operation_id)&&sha(v.root_sha256));return {root_operation_id:v.root_operation_id,root_sha256:v.root_sha256};}
function workOwner(s:Pick<AgentSession,'project_id'|'session_ref'>&{workspace_ref?:unknown}):OriginalWorkChoice['owner']{
 const ref=s.session_ref,w=s.workspace_ref as OriginalWorkChoice['owner']['workspace_ref'];
 check(id(s.project_id)&&exact(ref,['id','revision','sha256'])&&id(ref.id)&&ref.revision===1&&sha(ref.sha256)&&exact(w,['id','revision','binding_sha256'])&&sha(w.id)&&revision(w.revision)&&sha(w.binding_sha256));
 return structuredClone({project_id:s.project_id,session_ref:ref,workspace_ref:w});
}
export function validateOriginalWorkChoice(value:unknown):OriginalWorkChoice{
 const v=structuredClone(value) as OriginalWorkChoice;check(exact(v,['selection_id','owner','reference','label'])&&id(v.selection_id)&&exact(v.owner,['project_id','session_ref','workspace_ref'])&&typeof v.label==='string'&&Array.from(v.label).length<=280&&!/[\0\uD800-\uDFFF]/u.test(v.label));
 workOwner(v.owner);originalWorkRef(v.reference);check(new TextEncoder().encode(canonical(v)).length<=4096);return v;
}
export function originalWorkChoiceMatches(value:OriginalWorkChoice|undefined,s:AgentSession|undefined){if(!value)return true;if(!s||!revision(s.generation))return false;try{return canonical(validateOriginalWorkChoice(value).owner)===canonical(workOwner(s));}catch{return false;}}
export function sameOriginalWorkChoice(a:OriginalWorkChoice|undefined,b:OriginalWorkChoice|undefined){return canonical(a??null)===canonical(b??null);}
export function originalWorkChoiceForRef(session:AgentSession,reference:unknown,label=''):OriginalWorkChoice|undefined{const ref=originalWorkRef(reference);return ref?validateOriginalWorkChoice({selection_id:crypto.randomUUID(),owner:workOwner(session),reference:ref,label}):undefined;}
export async function makeOriginalWorkChoice(session:AgentSession,source:OriginalWorkInputSource):Promise<OriginalWorkChoice>{
 const owner=workOwner(session),generation=session.generation,v={root_basis:structuredClone(source.root_basis),root_sha256:source.root_sha256,label:source.label},b=v.root_basis;
 check(exact(b,['project_id','session_ref','workspace_ref','root_operation_id','root_generation','native_session_id','native_message_id','request_sha256'])&&canonical({project_id:b.project_id,session_ref:b.session_ref,workspace_ref:b.workspace_ref})===canonical(owner)&&id(b.root_operation_id)&&revision(b.root_generation)&&b.root_generation<=generation&&sha(b.request_sha256));
 for(const n of [b.native_session_id,b.native_message_id])check(typeof n==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(n));
 check(sha(v.root_sha256)&&await hash(b)===v.root_sha256);
 return validateOriginalWorkChoice({selection_id:crypto.randomUUID(),owner,reference:{root_operation_id:b.root_operation_id,root_sha256:v.root_sha256},label:v.label});
}
const WORK_STATUS=new Set(['进度','进度如何','现在进度如何','现在进度怎么样','当前进度','查看进度','目前进度','现在到哪一步了','status','progress','what is the status']);
const STATUS_ASCII_FOLDS:Record<string,string>={'ß':'ss','ſ':'s','ﬀ':'ff','ﬁ':'fi','ﬂ':'fl','ﬃ':'ffi','ﬄ':'ffl','ﬅ':'st','ﬆ':'st'};
// Match the existing Python str.strip/casefold protocol, including its C1
// whitespace and excluding BOM. JS trim alone changes routing for these inputs.
function statusStrip(value:string){return value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,'');}
export function originalWorkStatusText(value:string){const text=statusStrip(statusStrip(value).replace(/[?？。.!！]+$/u,'')).toLowerCase().replace(/[ßſﬀﬁﬂﬃﬄﬅﬆ]/gu,ch=>STATUS_ASCII_FOLDS[ch]);return WORK_STATUS.has(text);}
export function verifyOriginalWorkInputReceipt(value:unknown,owner:{session_ref:AgentSession['session_ref'];generation:number},reference:OriginalWorkRef,text:string,local=false){
 const v=value as Record<string,unknown>,r=v?.request as Record<string,unknown>|undefined;
 check(object(v)&&object(r)&&['MESSAGE','GUIDANCE'].includes(v.kind as string)&&canonical(v.session_ref)===canonical(owner.session_ref)&&canonical(r.session_ref)===canonical(owner.session_ref)&&r.generation===owner.generation&&r.operation_id===v.id&&r.raw_text===text&&canonical(originalWorkRef(r.original_work_ref))===canonical(reference));
 if(local&&(v.status==='ACCEPTED'||v.status==='RETURNED'))check(v.role==='LOCAL_STATUS');
 if(v.role==='LOCAL_STATUS'){
  const status=v.local_status as Record<string,unknown>,w=status?.original_work as Record<string,unknown>;
  check(object(status)&&object(w)&&v.kind==='GUIDANCE'&&v.model_calls===0&&v.native_post_count===0&&v.semantic_acceptance===false&&canonical(originalWorkRef(w.reference))===canonical(reference)&&w.model_calls===0&&w.native_posts===0&&w.coverage==='LAST_SAVED_ORIGINAL_ROOT_STATUS_NOT_SEMANTIC_ACCEPTANCE'&&revision(w.root_generation)&&w.root_generation<=owner.generation&&typeof w.status==='string'&&w.status.length<=100&&typeof w.root_status==='string'&&w.root_status.length<=100&&w.status===status.operation_status&&w.active_operation_id===status.active_operation_id&&(w.active_operation_id===null||id(w.active_operation_id)));
  if(w.active_relation!==undefined){const relation=w.active_relation,active=w.active_operation_id,current=w.owner_active_operation_id;check(['SAME_WORK','OTHER_OR_UNVERIFIED_WORK','NO_ACTIVE_WORK'].includes(relation as string)&&current===status.owner_active_operation_id&&(current===null||id(current))&&(relation==='SAME_WORK'?active!==null&&active===current:relation==='NO_ACTIVE_WORK'?active===null&&current===null:active===null&&current!==null));}
 }
}
