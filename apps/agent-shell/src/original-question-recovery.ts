import {originalSessionReady,type AgentSession,type AgentSessionOperation} from './agent-sessions.ts';
import {DiscussionDraftLease,discussionDraftKey} from './discussion-drafts.ts';
import {nativeReference,sameNativeReference} from './native-collaboration.ts';
import {controlDraftScope,controlKey,type ControlAttempt,type ControlDraft,type ControlIdentity} from './original-pending-controls.ts';

type StoragePort=Pick<Storage,'getItem'|'setItem'|'key'|'length'>;
export type QuestionDraftRecovery={key:string;raw:string;identity:ControlIdentity;draft:ControlDraft;questions:string[];copiedTo?:string};
const unavailable=()=>new Error('QUESTION_DRAFT_RECOVERY_UNAVAILABLE');
const closed=new Set(['RETURNED','COMPLETED']);
const failed=new Set(['FAILED','CANCELLED','REJECTED']);

/** Read local answer data, never a permission or a replayable response. */
export function readQuestionRecoveries(storage:StoragePort,s:AgentSession,attempts:ControlAttempt[]):QuestionDraftRecovery[]{
 if(storage.length>8192)throw unavailable();
 const prefix='eeas-discussion-draft-v1:'+JSON.stringify(['original',s.project_id]).slice(0,-1)+',"ctl_'+s.session_ref.id+'_';
 const result:QuestionDraftRecovery[]=[];
 for(let index=0;index<storage.length;index++){
  const key=storage.key(index);if(!key?.startsWith(prefix))continue;
  const raw=storage.getItem(key);if(raw===null||raw.length>262144)throw unavailable();
  const envelope=JSON.parse(raw);if(typeof envelope.text!=='string'||envelope.text.length>64000||!Array.isArray(envelope.attachments)||envelope.attachments.length)throw unavailable();
  const data=JSON.parse(envelope.text),i=data.identity as ControlIdentity,d=data.draft as ControlDraft;
  if(!i||i.kind!=='QUESTION'||i.project!==s.project_id||!nativeReference(i.session)||!sameNativeReference(i.session,s.session_ref)||!Number.isSafeInteger(i.generation)||i.generation<1||!i.parent||!/^[A-Za-z0-9_-]{1,100}$/.test(i.parent)||!i.request||!/^[A-Za-z0-9_-]{1,100}$/.test(i.request.id)||!/^[a-f0-9]{64}$/.test(i.request.sha256)||i.request.generation!==i.generation)throw unavailable();
  if(discussionDraftKey(controlDraftScope(i))!==key)throw unavailable();
  if(!d||!Array.isArray(d.answers)||d.answers.length>128||!Array.isArray(d.custom)||d.custom.length>128||d.answers.some(a=>!Array.isArray(a)||a.some(v=>typeof v!=='string'))||d.custom.some(v=>typeof v!=='string'))throw unavailable();
  if(data.questions!==undefined&&(!Array.isArray(data.questions)||data.questions.length>128||data.questions.some((v:unknown)=>typeof v!=='string')))throw unavailable();
  if(data.copied_to!==undefined&&(typeof data.copied_to!=='string'||!/^(native|original|discovery):[a-f0-9]{64}$/.test(data.copied_to)))throw unavailable();
  if(i.generation>s.generation||!d.answers.some(a=>a.length)&&!d.custom.some(v=>v.trim()))continue;
  if(originalSessionReady(s)&&s.active_operation_id===i.parent&&s.pending_questions.some(p=>p.request_ref.id===i.request.id&&p.request_ref.sha256===i.request.sha256&&p.request_ref.generation===i.generation))continue;
  const last=attempts.filter(a=>controlKey(a.identity)===controlKey(i)).at(-1);
  if(last&&(closed.has(last.status)||!failed.has(last.status)))continue;
  result.push({key,raw,identity:i,draft:structuredClone(d),questions:data.questions??[],copiedTo:data.copied_to});
  if(result.length>128)throw unavailable();
 }
 return result.sort((a,b)=>b.identity.generation-a.identity.generation||a.key.localeCompare(b.key));
}

export function assertQuestionRecoveryCurrent(storage:Pick<Storage,'getItem'>,candidate:QuestionDraftRecovery){
 if(discussionDraftKey(controlDraftScope(candidate.identity))!==candidate.key||storage.getItem(candidate.key)!==candidate.raw)throw new Error('QUESTION_DRAFT_RECOVERY_CHANGED');
}

export function questionRecoveryText(candidate:QuestionDraftRecovery,operation:AgentSessionOperation,s:AgentSession,conversationId:string,locale:string){
 const i=candidate.identity,request=operation.request as {raw_text?:unknown;conversation_ref?:{id?:string}}|undefined;
 if(i.project!==s.project_id||!sameNativeReference(i.session,s.session_ref)||operation.id!==i.parent||operation.generation!==i.generation||operation.original_session_id!==s.original_session_id||!['UNKNOWN_OUTCOME','CANCELLED','FAILED'].includes(operation.status)||typeof request?.raw_text!=='string'||!request.raw_text.trim()||request.conversation_ref&&request.conversation_ref.id!==conversationId)throw new Error('QUESTION_DRAFT_ORIGINAL_NOT_CONFIRMED');
 const en=locale.startsWith('en'),answers=Array.from({length:Math.max(candidate.draft.answers.length,candidate.draft.custom.length)},(_,index)=>{
  const values=[...candidate.draft.answers[index]??[],...(candidate.draft.custom[index]?.trim()?[candidate.draft.custom[index]]:[])];
  return values.length?`${index+1}. ${candidate.questions[index]?candidate.questions[index]+'\n':''}${values.join('\n')}`:'';
 }).filter(Boolean);
 if(!answers.length)throw unavailable();
 const context=candidate.questions.length?'':`${en?'Earlier request (quoted for context):':'此前要求（引用供核对）：'}\n${request.raw_text.split('\n').map(line=>'> '+line).join('\n')}\n\n`;
 return `${context}${en?'Follow-up answers to the earlier question:':'补充此前问题的回答：'}\n${answers.join('\n\n')}`;
}

export function appendQuestionRecovery(current:string,answer:string){
 const next=current.includes(answer)?current:current+(current?'\n\n':'')+answer;
 if(Array.from(next).length>16000)throw new Error('QUESTION_DRAFT_MESSAGE_TOO_LONG');
 return next;
}

/** Mark a successful local copy only after its destination draft was saved. */
export function markQuestionRecovered(storage:Pick<Storage,'getItem'|'setItem'>,candidate:QuestionDraftRecovery,conversationId:string){
 assertQuestionRecoveryCurrent(storage,candidate);
 if(!/^(native|original|discovery):[a-f0-9]{64}$/.test(conversationId))throw unavailable();
 const lease=new DiscussionDraftLease(storage,controlDraftScope(candidate.identity)),saved=lease.read();
 const original=JSON.parse(saved.text);saved.text=JSON.stringify({...original,copied_to:conversationId});lease.save(saved);
}
