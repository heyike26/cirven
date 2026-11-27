import type {ArtifactEnvelope,PlanningProposal,PlanningQuestion,PlanningResolution} from './contracts';

export type AdoptedQuestion={mode:'ANSWER_ACCEPTED'|'ASSUMPTION_ACCEPTED';text:string;reason:string;answerId?:string;acceptedRevision:number;acceptedSha256:string;confirmedRevision?:number};
export type ResolutionDraft={answerId?:string;assumption?:string;reason:string};
export type ConfirmationSelection={question_id:string;mode:'ANSWER_ACCEPTED'|'ASSUMPTION_ACCEPTED';answer_id?:string;assumption?:string;reason:string};
const hash=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
function sameValue(a:unknown,b:unknown):boolean{
 if(a===b)return true;
 if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
 const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>Object.hasOwn(b,key)&&sameValue((a as Record<string,unknown>)[key],(b as Record<string,unknown>)[key]));
}
function proposalFor(artifact:ArtifactEnvelope):PlanningProposal|undefined{
 const value=artifact.data as PlanningProposal|undefined;
 if(artifact.type!=='proposal'||!value?.payload||value.id!==artifact.artifactId||value.project_id!==artifact.scope.projectId||value.revision!==artifact.revision||!hash(value.sha256)||value.sha256!==artifact.contentHash)return;
 const payload=value.payload;
 if(payload.project_id!==undefined&&payload.project_id!==value.project_id||payload.proposal_id!==undefined&&payload.proposal_id!==value.id||payload.revision!==undefined&&payload.revision!==value.revision)return;
 return value;
}
function exactQuestion(proposal:PlanningProposal,question:PlanningQuestion){
 const rows=proposal.payload.candidate.questions.filter(value=>value.id===question.id);
 return rows.length===1&&sameValue(rows[0],question);
}
function projectResolution(proposal:PlanningProposal,record:PlanningResolution,acceptedRevision:number,acceptedSha256:string,questionHash?:string):AdoptedQuestion|undefined{
 if(typeof record.reason!=='string'||!record.reason.trim())return;
 const basis={reason:record.reason,acceptedRevision,acceptedSha256};
 if(record.mode==='ASSUMPTION_ACCEPTED'){
  if(record.answer_id||typeof record.assumption!=='string'||!record.assumption.trim())return;
  return {...basis,mode:record.mode,text:record.assumption};
 }
 if(record.mode!=='ANSWER_ACCEPTED'||typeof record.answer_id!=='string'||!record.answer_id||!hash(record.answer_sha256)||typeof record.answer_text!=='string')return;
 const answers=proposal.answers.filter(value=>value?.answer_id===record.answer_id);
 if(answers.length!==1)return;
 const answer=answers[0],savedRevision=answer.proposal_revision;
 if(answer.question_id!==record.question_id||answer.proposal_id!==proposal.id||typeof savedRevision!=='number'||!Number.isSafeInteger(savedRevision)||savedRevision<1||savedRevision>acceptedRevision||!hash(answer.question_sha256)||questionHash&&answer.question_sha256!==questionHash||answer.text!==record.answer_text)return;
 return {...basis,mode:record.mode,text:record.answer_text,answerId:record.answer_id};
}

/** The immutable server confirmation is the authority, not answer.accepted or a
 * local resolution draft. No lookup, new decision, execution grant or hash claim.
 * A legitimate earlier answer remains usable: the service checks its question
 * hash at confirmation, so its save revision need not equal accepted_revision. */
export function adoptedProposalQuestions(artifact:ArtifactEnvelope,questions:PlanningQuestion[]):Map<string,AdoptedQuestion>{
 const result=new Map<string,AdoptedQuestion>(),proposal=proposalFor(artifact);
 if(!proposal||proposal.payload.status!=='CONFIRMED')return result;
 const payload=proposal.payload,confirmation=payload.confirmation;
 if(payload.project_id!==undefined&&payload.project_id!==proposal.project_id||payload.proposal_id!==undefined&&payload.proposal_id!==proposal.id||payload.revision!==undefined&&payload.revision!==proposal.revision)return result;
 if(!confirmation||confirmation.user_confirmed!==true||!Number.isSafeInteger(confirmation.accepted_revision)||confirmation.accepted_revision<1||confirmation.accepted_revision!==proposal.revision-1||!hash(confirmation.accepted_sha256)||!Array.isArray(confirmation.resolutions)||!Array.isArray(proposal.answers)||!Array.isArray(payload.candidate?.questions))return result;
 for(const question of questions){
  if(!exactQuestion(proposal,question))continue;
  const records=confirmation.resolutions.filter(value=>value?.question_id===question.id);
  if(records.length!==1)continue;
  const projected=projectResolution(proposal,records[0],confirmation.accepted_revision,confirmation.accepted_sha256);
  if(projected)result.set(question.id,projected);
 }
 return result;
}

/** A draft may carry server-derived decisions, but is never a new confirmation.
 * Hashes and provenance are displayed metadata; only the service may retain or
 * revalidate authority. No browser-generated resolution or provenance is sent. */
export function retainedProposalQuestions(artifact:ArtifactEnvelope,questions:PlanningQuestion[]):Map<string,AdoptedQuestion>{
 const result=new Map<string,AdoptedQuestion>(),proposal=proposalFor(artifact);
 if(!proposal||proposal.payload.status!=='DRAFT'||proposal.payload.confirmation!=null)return result;
 const value=proposal.payload.decision_continuity,basis=value?.basis_ref;
 if(value?.schema!=='cirven.plan-decision-continuity.v1'||!basis||basis.project_id!==proposal.project_id||basis.id!==proposal.id||basis.revision!==proposal.revision-1||!hash(basis.sha256)||!Array.isArray(value.retained_decisions)||!Array.isArray(value.invalidated_question_ids)||value.structural_impact?.semantic_coverage!=='STRUCTURAL_ONLY_NOT_EXHAUSTIVE')return result;
 for(const question of questions){
  if(!exactQuestion(proposal,question)||value.invalidated_question_ids.includes(question.id))continue;
  const rows=value.retained_decisions.filter(row=>row?.resolution?.question_id===question.id);
  if(rows.length!==1)continue;
  const {resolution,provenance}=rows[0],ref=provenance?.proposal_ref;
  if(!ref||ref.project_id!==proposal.project_id||ref.id!==proposal.id||!Number.isSafeInteger(ref.revision)||ref.revision<2||ref.revision>basis.revision||!hash(ref.sha256)||!hash(provenance.question_sha256)||!hash(provenance.resolution_sha256))continue;
  const projected=projectResolution(proposal,resolution,ref.revision-1,ref.sha256,provenance.question_sha256);
  if(projected)result.set(question.id,{...projected,confirmedRevision:ref.revision});
 }
 return result;
}

/** Submit only current explicit changes. Restored selections already present in
 * the immutable server decision are ignored, rather than replayed as new work. */
export function proposalConfirmationSelection(artifact:ArtifactEnvelope,questions:PlanningQuestion[],drafts:Record<string,ResolutionDraft>){
 const decisions:ConfirmationSelection[]=[],incomplete:string[]=[],proposal=proposalFor(artifact);
 if(!proposal)return {decisions,incomplete};
 const existing=new Map([...adoptedProposalQuestions(artifact,questions),...retainedProposalQuestions(artifact,questions)]);
 for(const question of questions){
  if(!exactQuestion(proposal,question))continue;
  const draft=drafts[question.id];if(!draft||!draft.answerId&&!draft.assumption?.trim())continue;
  const old=existing.get(question.id);
  if(old&&draft.reason===old.reason&&(draft.answerId?old.mode==='ANSWER_ACCEPTED'&&draft.answerId===old.answerId:old.mode==='ASSUMPTION_ACCEPTED'&&draft.assumption===old.text))continue;
  if(!draft.reason?.trim()){incomplete.push(question.id);continue;}
  if(draft.answerId){
   const answers=proposal.answers.filter(value=>value.answer_id===draft.answerId&&value.question_id===question.id&&value.proposal_id===proposal.id&&typeof value.proposal_revision==='number'&&(old?.mode==='ANSWER_ACCEPTED'&&old.answerId===value.answer_id||value.proposal_revision>=(proposal.payload.decision_continuity?.question_scope_revisions?.[question.id]??1))&&value.proposal_revision<=proposal.revision&&hash(value.question_sha256));
   if(answers.length!==1){incomplete.push(question.id);continue;}
   decisions.push({question_id:question.id,mode:'ANSWER_ACCEPTED',answer_id:draft.answerId,reason:draft.reason});
  }else decisions.push({question_id:question.id,mode:'ASSUMPTION_ACCEPTED',assumption:draft.assumption,reason:draft.reason});
 }
 return {decisions,incomplete};
}
