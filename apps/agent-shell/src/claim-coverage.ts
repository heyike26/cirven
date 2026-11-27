import {ApiError} from './api';
import type {ClaimCoverageDraft,ClaimCoverageOperation,ClaimCoverageTarget,ClaimCoverageView,PlanningCandidate,PlanningNode,PlanningProposal,PlanningResolution} from './contracts';

export const COVERAGE_CONTRACT='ACCEPTED_CLAIMS_V1' as const;
export function canonicalCoverage(value:unknown):string {
  if(value===null)return 'null';
  if(Array.isArray(value))return '['+value.map(canonicalCoverage).join(',')+']';
  if(typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonicalCoverage((value as Record<string,unknown>)[key])).join(',')+'}';
  if(typeof value==='number'&&!Number.isSafeInteger(value))throw new Error('要求承接的数值依据无法核对');
  if(!['string','number','boolean'].includes(typeof value))throw new Error('要求承接的依据不完整');
  return JSON.stringify(value);
}
export async function coverageDigest(value:unknown){const text=canonicalCoverage(value),bytes=new TextEncoder().encode(text);if(bytes.length>2*1024*1024)throw new Error('事项与任务过多，请拆分后再核对');const digest=await crypto.subtle.digest('SHA-256',bytes);return Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,'0')).join('');}
export function normalizedPlanningNode(node:PlanningNode):PlanningNode{return {id:node.id,title:node.title,description:node.description,kind:node.kind??'TASK',parent_id:node.parent_id??null,depends_on:node.depends_on??[],inputs:node.inputs??[],outputs:node.outputs??[],acceptance:node.acceptance??[],capabilities:node.capabilities??[],risks:node.risks??[],source_anchors:node.source_anchors??[],origin:node.origin??'MODEL_SUGGESTION'};}
export function coverageFromProposal(proposal:PlanningProposal){return proposal.coverage;}
export function hasCoverageContract(proposal:PlanningProposal){return proposal.payload.schema_version==='eeas.planning-proposal.v2'||!!proposal.payload.requirement_coverage||!!proposal.coverage;}
export function coverageDraft(view:ClaimCoverageView):ClaimCoverageDraft{return structuredClone({expected_basis_sha256:view.basis_sha256,claim_links:view.claim_links,user_exclusions:view.user_exclusions});}
const compareExact=(a:string,b:string)=>a<b?-1:a>b?1:0;
export function explicitCoverageRejection(error:unknown){return error instanceof ApiError&&error.code!=='INVALID_RESPONSE'&&[400,401,403,404,409,412,415,422].includes(error.status);}
export function coverageDraftProjection(value:ClaimCoverageDraft){return {...value,claim_links:[...value.claim_links].sort((a,b)=>compareExact(a.claim_id,b.claim_id)).map(link=>({...link,targets:[...link.targets].sort((a,b)=>compareExact(canonicalCoverage(a),canonicalCoverage(b)))})),user_exclusions:[...value.user_exclusions].sort((a,b)=>compareExact(a.claim_id,b.claim_id))};}
export function coverageDraftEqual(a:ClaimCoverageDraft,b:ClaimCoverageDraft){return canonicalCoverage(coverageDraftProjection(a))===canonicalCoverage(coverageDraftProjection(b));}
export function targetKey(value:ClaimCoverageTarget){return value.kind==='TASK'?'TASK:'+value.node_id:'SHARED_CONSTRAINT:'+value.constraint_index;}
export type CoverageInventory={tasks:{node_id:string;title:string;node_sha256:string}[];constraints:{constraint_index:number;text:string;value_sha256:string}[]};
export async function currentCoverageInventory(candidate:PlanningCandidate,cache=new Map<string,{value:string;sha256:string}>()):Promise<CoverageInventory>{if(candidate.nodes.length>2000||new TextEncoder().encode(canonicalCoverage(candidate)).length>2*1024*1024)throw new Error('事项与任务过多，请拆分后再核对');const keys=new Set<string>();async function digest(key:string,value:unknown){keys.add(key);const text=canonicalCoverage(value),old=cache.get(key);if(old?.value===text)return old.sha256;const hash=await coverageDigest(value);cache.set(key,{value:text,sha256:hash});return hash;}const result={tasks:await Promise.all(candidate.nodes.filter(node=>(node.kind??'TASK')==='TASK').map(async node=>({node_id:node.id,title:node.title,node_sha256:await digest('TASK:'+node.id,normalizedPlanningNode(node))}))),constraints:await Promise.all(candidate.constraints.map(async(text,constraint_index)=>({constraint_index,text,value_sha256:await digest('SHARED:'+constraint_index,text)})))};for(const key of cache.keys())if(!keys.has(key))cache.delete(key);return result;}
export function targetCurrent(target:ClaimCoverageTarget,inventory:CoverageInventory){return target.kind==='TASK'?inventory.tasks.some(value=>value.node_id===target.node_id&&value.node_sha256===target.node_sha256):target.scope==='ALL_TASKS'&&inventory.constraints.some(value=>value.constraint_index===target.constraint_index&&value.value_sha256===target.value_sha256);}
export function localCoverageState(view:ClaimCoverageView,draft:ClaimCoverageDraft,inventory:CoverageInventory|undefined){
  const basisCurrent=view.status!=='STALE'&&draft.expected_basis_sha256===view.basis_sha256;
  const states=view.accepted_claims.map(({claim,sha256})=>{const link=draft.claim_links.find(value=>value.claim_id===claim.id),exclusion=draft.user_exclusions.find(value=>value.claim_id===claim.id);const valid=!!inventory&&!!link&&link.claim_sha256===sha256&&link.targets.length>0&&link.targets.every(target=>targetCurrent(target,inventory));return {id:claim.id,state:!basisCurrent?'STALE':exclusion?exclusion.claim_sha256===sha256&&exclusion.reason.trim()&&!link?.targets.length?'EXCLUDED':'INVALID':valid?'LINKED':link?.targets.length?'STALE_TARGET':'MISSING'};});
  return {states,missing:states.filter(value=>!['LINKED','EXCLUDED'].includes(value.state)).length,status:!basisCurrent?'STALE':inventory&&states.every(value=>['LINKED','EXCLUDED'].includes(value.state))?'STRUCTURALLY_COMPLETE':'INCOMPLETE'};
}
export function validateCoverageDraft(view:ClaimCoverageView,draft:ClaimCoverageDraft){
  if(view.status==='STALE'||draft.expected_basis_sha256!==view.basis_sha256)throw new Error('已确认事项的依据已变化，请读取原提案后核对');
  if(view.accepted_claims.length>2000||draft.claim_links.length>2000||draft.user_exclusions.length>2000)throw new Error('事项与任务过多，请拆分后再核对');
  const claims=new Map(view.accepted_claims.map(value=>[value.claim.id,value.sha256])),seen=new Set<string>();let count=0;
  for(const link of draft.claim_links){if(seen.has(link.claim_id)||claims.get(link.claim_id)!==link.claim_sha256||link.targets.length>16)throw new Error('事项的承接依据不匹配');seen.add(link.claim_id);const targets=new Set<string>();for(const target of link.targets){const key=targetKey(target);if(targets.has(key))throw new Error('同一事项不能重复映射相同目标');targets.add(key);count++;}}
  for(const row of draft.user_exclusions){if(seen.has(row.claim_id)||claims.get(row.claim_id)!==row.claim_sha256||!row.reason.trim()||row.reason.length>4000)throw new Error('请为不形成任务的事项填写理由');seen.add(row.claim_id);}
  if(count>8000)throw new Error('事项与任务过多，请拆分后再核对');
}
export async function prepareCoverageOperation(proposal:PlanningProposal,kind:'SAVE'|'CONFIRM',id:string,candidate:PlanningCandidate,draft:ClaimCoverageDraft,resolutions:PlanningResolution[]=[],reason=''):Promise<ClaimCoverageOperation>{
  const view=coverageFromProposal(proposal);if(!view)throw new Error('原提案的事项承接依据尚未读取');validateCoverageDraft(view,draft);
  const common={expected_revision:proposal.revision};const body=kind==='SAVE'?{...common,candidate,change_reason:reason,requirement_coverage:draft}:{...common,proposal_sha256:proposal.sha256,user_confirmed:true,resolutions,coverage_sha256:view.snapshot_sha256,confirmed_exclusion_ids:view.user_exclusions.map(value=>value.claim_id)};
  return {kind,operation_id:id,project_id:proposal.project_id,proposal_ref:{id:proposal.id,revision:proposal.revision,sha256:proposal.sha256},request_sha256:await coverageDigest({proposal_id:proposal.id,...body}),candidate_sha256:await coverageDigest(candidate),coverage_draft_sha256:await coverageDigest(coverageDraftProjection(draft)),coverage_sha256:kind==='CONFIRM'?view.snapshot_sha256:null,resolutions_sha256:kind==='CONFIRM'?await coverageDigest(resolutions):null,state:'PREPARED',result_ref:null};
}
export async function verifyCoverageResult(value:unknown,original:ClaimCoverageOperation):Promise<PlanningProposal>{
  const result=value as PlanningProposal;if(!result||result.project_id!==original.project_id||result.id!==original.proposal_ref.id||result.revision!==original.proposal_ref.revision+1||!/^[0-9a-f]{64}$/.test(result.sha256)||await coverageDigest(result.payload?.candidate)!==original.candidate_sha256)throw new Error('原操作结果尚未能核对；当前草稿已保留');
  if(!result.coverage||await coverageDigest(coverageDraftProjection(coverageDraft(result.coverage)))!==original.coverage_draft_sha256)throw new Error('原保存的事项承接尚未能核对');
  if(original.kind==='CONFIRM'){const confirmation=result.payload.confirmation;if(result.payload.status!=='CONFIRMED'||confirmation?.accepted_revision!==original.proposal_ref.revision||confirmation.accepted_sha256!==original.proposal_ref.sha256||confirmation.coverage_sha256!==original.coverage_sha256||canonicalCoverage([...(confirmation.confirmed_exclusion_ids??[])].sort())!==canonicalCoverage(result.coverage.user_exclusions.map(value=>value.claim_id).sort()))throw new Error('原确认结果尚未能核对');}
  return result;
}
