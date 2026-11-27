import {draftKey,stageModeKey,type PendingTransition,type Scope,type TransitionReceipt} from './stage-workflows';

const identifier=(value:unknown)=>typeof value==='string'&&/^[a-zA-Z0-9_-]{1,100}$/.test(value);
const session=(value:unknown)=>typeof value==='string'&&/^(native|original|discovery):[a-f0-9]{64}$/.test(value);
const revision=(value:unknown)=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=1;
export const stageDraftSettledEvent='cirven-stage-draft-settled';
export type StageDraftSettled={key:string;field:'rework'|'followUp';edit:string};
type SubmittedDraft={field:'rework';reason:unknown;target:unknown}|{field:'followUp';text:unknown;sourceType:unknown};

export function readPendingTransition(key:string):PendingTransition|undefined{
 try{
  const stored=localStorage.getItem(key);
  if(!stored||stored.length>75000)return;
  const value=JSON.parse(stored),plan=value?.plan;
  if(!identifier(value?.operation_id)||!session(value.request_conversation_id)||!plan)return;
  if(value.draft_edit!==undefined&&!identifier(value.draft_edit))delete value.draft_edit;
  if(plan.kind==='start'){
   if(identifier(plan.definition_id)&&revision(plan.expected_definition_revision))return value;
   return;
  }
  if(!['approve','rework','revise'].includes(plan.kind)||!identifier(plan.run_id)||!identifier(plan.node_id)||
      !session(plan.stage_session_id)||!revision(plan.expected_revision)||!plan.payload||Array.isArray(plan.payload)||
      typeof plan.payload!=='object')return;
  return value;
 }catch{/* A missing or invalid local request is never sent. */}
}

export function matchesTransition(request:PendingTransition,receipt:TransitionReceipt,project:string){
 const plan=request.plan;
 if(receipt.project_id!==project||receipt.request_conversation_id!==request.request_conversation_id||
    receipt.operation_id!==request.operation_id||receipt.kind!==plan.kind||!identifier(receipt.run_id)||
    !revision(receipt.run_revision)||typeof receipt.previous!=='string'||typeof receipt.target_current!=='boolean'||
    typeof receipt.target_historical!=='boolean'||(receipt.target_current&&receipt.target_historical))return false;
 const target=receipt.target;
 if(target===null?plan.kind!=='approve'||receipt.target_historical:!target||!identifier(target.node_id)||
    !session(target.stage_session_id)||typeof target.stage_name!=='string')return false;
 if(plan.kind==='start')return receipt.source_node_id===null&&receipt.source_stage_session_id===null&&
    receipt.definition_id===plan.definition_id&&receipt.definition_revision===plan.expected_definition_revision;
 return receipt.run_id===plan.run_id&&receipt.source_node_id===plan.node_id&&
   receipt.source_stage_session_id===plan.stage_session_id&&receipt.run_revision>plan.expected_revision;
}

export function settleStageDraft(key:string,submitted:SubmittedDraft,edit?:string){
 if(!identifier(edit))return;
 try{
  const draft=JSON.parse(localStorage.getItem(key)??'null');
  const {field}=submitted;
  const editField=`${field}Edit`;
  if(!draft||draft[editField]!==edit)return;
  const matches=submitted.field==='rework'
   ?typeof draft.rework?.reason==='string'&&draft.rework.reason.trim()===submitted.reason&&
    (draft.rework.target===undefined||draft.rework.target===submitted.target)
   :typeof draft.followUp==='string'&&draft.followUp.trim()===submitted.text&&
    (draft.followUpType??'instruction')===(submitted.sourceType??'instruction');
  if(!matches)return;
  delete draft[field];
  delete draft[editField];
  localStorage.setItem(key,JSON.stringify(draft));
  window.dispatchEvent(new CustomEvent(stageDraftSettledEvent,{detail:{key,field,edit}}));
 }catch{/* Leave local drafts intact if they cannot be read. */}
}

export function settleTransitionDraft(owner:Scope,request:PendingTransition){
 const plan=request.plan;
 if(plan.kind!=='rework'&&plan.kind!=='revise')return;
 const key=draftKey({...owner,id:plan.run_id},plan.node_id,plan.stage_session_id);
 const submitted:SubmittedDraft=plan.kind==='rework'
  ?{field:'rework',reason:plan.payload.reason,target:plan.payload.target_node_id}
  :{field:'followUp',text:plan.payload.text,sourceType:plan.payload.source_type};
 settleStageDraft(key,submitted,request.draft_edit);
}

export function clearTransitionRetryKey(project_id:string,request:PendingTransition){
 const suffix=request.plan.kind==='revise'?':revise':':operation:'+request.plan.kind;
 const key=stageModeKey({project_id,conversation_id:request.request_conversation_id})+suffix;
 try{
  if(JSON.parse(localStorage.getItem(key)??'null')?.id===request.operation_id)localStorage.removeItem(key);
 }catch{/* Invalid prior retry data does not change the confirmed outcome. */}
}
