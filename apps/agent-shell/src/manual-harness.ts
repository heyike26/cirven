import {coverageDraft,coverageDraftEqual} from './claim-coverage';
import type {ClaimCoverageDraft} from './contracts';
import {ApiError} from './api';
import type {PlanningCandidate,PlanningProposal,SourceRef} from './contracts';
import type {RevisionRef} from './workbench-contracts';
import {sameWritingRef,writingDigest} from './writing-experience';

export type ManualWorkspace={id:string;revision:number;binding_sha256:string};
export type ManualExportRef={id:string;request_sha256:string;result_sha256:string};
export type ManualFileRef={export_ref:ManualExportRef;path:string;basis_sha256:string;edited_sha256:string};
export type ManualCoverage={complete:boolean;total_registered_files:number;checked_files:number;scanned_export_count:number;unverified:{path?:string;reason:string}[];uncovered_changed_paths?:string[]};
export type ManualGate={code:'MANUAL_REVIEW_REQUIRED'|'SOURCE_DRIFT'|'UNVERIFIED';target_proposal_ref:RevisionRef;workspace_ref:ManualWorkspace|null;status:string;files:(ManualFileRef&{node_id:string|null;kind:'PROMPT'|'OTHER';status:string})[];coverage:ManualCoverage;predecessor_export_refs:ManualExportRef[]};
export type ManualCaptureRequest={operation_id:string;target_proposal_ref:RevisionRef;workspace_ref:ManualWorkspace;files:ManualFileRef[];predecessor_export_ref:ManualExportRef|null};
export type ManualHunk={id:string;sha256:string;kind:'INSERT'|'REPLACE'|'DELETE';removed_text:string;added_text:string;keep_text:string;before_range:{start:number;end:number};after_range:{start:number;end:number};path:string;basis_sha256:string;edited_sha256:string};
export type ManualCaptureRef=RevisionRef&{revision:1};
export type ManualCapture={capture_ref:ManualCaptureRef;snapshot:{project_id:string;target_proposal_ref:RevisionRef;workspace_ref:ManualWorkspace;files:{file_ref:ManualFileRef;basis_source_ref:SourceRef;edited_source_ref:SourceRef;node_id:string|null;hunks:ManualHunk[]}[];coverage:ManualCoverage;[key:string]:unknown};current:{status:'APPLICABLE'|'APPLIED'|'STALE'|'UNVERIFIED';pending:{path?:string;reason:string}[];target_proposal_ref:RevisionRef};replayed?:boolean};
export type ManualCaptureOperation={request:ManualCaptureRequest;request_sha256:string;operation_hash:string;state:'PREPARED'|'UNKNOWN'|'REJECTED'|'COMPLETED';capture_ref:ManualCaptureRef|null};
export type ManualOperationReceipt={operation_hash:string;request_sha256:string;status:'NOT_FOUND'|'INTENT_ONLY'|'PENDING'|'COMPLETED';capture_ref:ManualCaptureRef|null};
export type ManualField='description'|'inputs'|'outputs'|'acceptance'|'risks';
export type ManualTarget={node_id:string;field:ManualField;edit_action:'ADD'|'REPLACE'|'REMOVE';item_index:number|null;before_value_sha256:string};
export type ManualDecision={hunk_id:string;hunk_sha256:string;mode:'KEEP'|'REPLACE'|'DISCARD';target:ManualTarget|null;text:string|null;reason:string};
export type ManualReview={change_ref:ManualCaptureRef;decisions:ManualDecision[]};
export type ManualChoice={hunk_id:string;hunk_sha256:string;node_id:string;field:ManualField|'';edit_action:'ADD'|'REPLACE'|'REMOVE'|'';item_index:string;mode:'KEEP'|'REPLACE'|'DISCARD'|'';text:string;reason:string};
export type ManualReviewDraft={change_ref:ManualCaptureRef;choices:ManualChoice[];applied:ManualDecision[]};
export type ManualSave={operation_id:string;expected_revision:number;candidate:PlanningCandidate;change_reason:string;manual_review:ManualReview;requirement_coverage?:ClaimCoverageDraft;request_sha256:string;state:'PREPARED'|'UNKNOWN'|'REJECTED'|'COMPLETED';result_ref:RevisionRef|null};
export const manualReference=(value:PlanningProposal):RevisionRef=>({id:value.id,revision:value.revision,sha256:value.sha256});
export const sameManualRef=sameWritingRef;
function canonical(value:unknown):string{if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';if(value!==null&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical((value as Record<string,unknown>)[key])).join(',')+'}';return JSON.stringify(value);}
const same=(a:unknown,b:unknown)=>canonical(a)===canonical(b);
const hex=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const validRef=(value:unknown):value is RevisionRef=>{const v=value as RevisionRef;return !!v&&typeof v.id==='string'&&Number.isSafeInteger(v.revision)&&v.revision>0&&hex(v.sha256);};
export function manualGateFromError(error:unknown,proposal:PlanningProposal):ManualGate|undefined{
 if(!(error instanceof ApiError)||error.status!==409||!['MANUAL_REVIEW_REQUIRED','SOURCE_DRIFT','UNVERIFIED'].includes(error.code))return;
 const data=(error.detail as {detail?:{manual_changes?:Partial<ManualGate>}})?.detail?.manual_changes;
 if(!data||!validRef(data.target_proposal_ref)||!sameManualRef(data.target_proposal_ref,manualReference(proposal)))throw new Error('人工修改提示不属于当前计划版本');
 if(error.code==='SOURCE_DRIFT'&&(!data.workspace_ref||!data.files))return {code:error.code,target_proposal_ref:data.target_proposal_ref,status:data.status??'STALE',workspace_ref:proposal.payload.workspace_ref??null,files:[],coverage:{complete:false,total_registered_files:0,checked_files:0,scanned_export_count:0,unverified:[{reason:'SOURCE_DRIFT'}]},predecessor_export_refs:[]};
 if(!data.workspace_ref||!hex(data.workspace_ref.id)||!Number.isSafeInteger(data.workspace_ref.revision)||!hex(data.workspace_ref.binding_sha256)||!Array.isArray(data.files)||data.files.length>32||!data.coverage||typeof data.coverage.complete!=='boolean'||!Array.isArray(data.coverage.unverified)||!Array.isArray(data.predecessor_export_refs))throw new Error('人工修改提示缺少可核对的原文件依据');
 return {...data,code:error.code} as ManualGate;
}
export function manualInspection(value:unknown,proposal:PlanningProposal,workspace:ManualWorkspace):ManualGate|null{
 const data=value as Omit<ManualGate,'code'>;
 if(!data||!['CLEAR','REVIEW_REQUIRED','UNVERIFIED'].includes(data.status)||!sameManualRef(data.target_proposal_ref,manualReference(proposal))||!same(data.workspace_ref,workspace))throw new Error('人工修改提示不属于当前计划版本');
 const code=data.status==='UNVERIFIED'?'UNVERIFIED':'MANUAL_REVIEW_REQUIRED';
 const checked=manualGateFromError(new ApiError(409,code,'',{detail:{manual_changes:data}}),proposal)!;
 if(data.status==='CLEAR'){if(!checked.coverage.complete||checked.files.length||checked.coverage.unverified.length)throw new Error('有文件尚未核对，暂不能确认完整交付。');return null;}
 return checked;
}
export async function prepareManualCapture(gate:ManualGate,operationId:string):Promise<ManualCaptureOperation>{
 if(gate.code!=='MANUAL_REVIEW_REQUIRED'||!gate.workspace_ref||!gate.files.length||!gate.coverage.complete)throw new Error('先核对未覆盖的原文件，不能把不完整检查当作没有修改');
 const request:ManualCaptureRequest={operation_id:operationId,target_proposal_ref:gate.target_proposal_ref,workspace_ref:gate.workspace_ref,files:gate.files.map(({export_ref,path,basis_sha256,edited_sha256})=>({export_ref,path,basis_sha256,edited_sha256})),predecessor_export_ref:null};
 const {operation_id,...body}=request;return {request,request_sha256:await writingDigest(body),operation_hash:await writingDigest(operation_id),capture_ref:null,state:'PREPARED'};
}
export async function verifyManualCapture(value:ManualCapture,proposal:PlanningProposal,original?:ManualCaptureOperation){
 const ref=value?.capture_ref,snapshot=value?.snapshot;
 if(!ref||ref.revision!==1||!hex(ref.id)||!hex(ref.sha256)||!snapshot||snapshot.project_id!==proposal.project_id||!sameManualRef(snapshot.target_proposal_ref,manualReference(proposal))||await writingDigest(snapshot)!==ref.sha256)throw new Error('人工修改记录与原计划依据不一致');
 if(original&&(!same(snapshot.workspace_ref,original.request.workspace_ref)||!sameManualRef(snapshot.target_proposal_ref,original.request.target_proposal_ref)||original.capture_ref&&!sameManualRef(original.capture_ref,ref)))throw new Error('人工修改记录不属于原核对操作');
 if(!Array.isArray(snapshot.files)||snapshot.files.length>32||snapshot.files.reduce((n,f)=>n+f.hunks.length,0)>200||!snapshot.coverage||!['APPLICABLE','APPLIED','STALE','UNVERIFIED'].includes(value.current?.status))throw new Error('人工修改记录超出可核对范围');
 return value;
}
export function manualHunks(value:ManualCapture){return value.snapshot.files.flatMap(file=>file.hunks.map(hunk=>({file,hunk})));}
export function startManualDraft(value:ManualCapture):ManualReviewDraft{return {change_ref:value.capture_ref,choices:manualHunks(value).map(({file,hunk})=>({hunk_id:hunk.id,hunk_sha256:hunk.sha256,node_id:file.node_id??'',field:'',edit_action:'',item_index:'',mode:'',text:'',reason:''})),applied:[]};}
export async function manualDecisions(proposal:PlanningProposal,capture:ManualCapture,draft:ManualReviewDraft,complete:boolean):Promise<ManualDecision[]>{
 if(!sameManualRef(draft.change_ref,capture.capture_ref)||capture.current.status!=='APPLICABLE'||!capture.snapshot.coverage.complete)throw new Error('原核对依据已变化或尚未完整覆盖，当前输入已保留');
 const hunks=new Map(manualHunks(capture).map(({hunk})=>[hunk.id,hunk]));const decisions:ManualDecision[]=[];
 for(const choice of draft.choices){const hunk=hunks.get(choice.hunk_id);if(!hunk||hunk.sha256!==choice.hunk_sha256)throw new Error('原修改片段已变化');if(!choice.mode){if(complete)throw new Error('还有人工修改尚未作出决定');continue;}if(!choice.reason.trim())throw new Error('请填写这项决定的理由');
  if(choice.mode==='DISCARD'){decisions.push({hunk_id:hunk.id,hunk_sha256:hunk.sha256,mode:'DISCARD',target:null,text:null,reason:choice.reason});continue;}
  const node=proposal.payload.candidate.nodes.find(n=>n.id===choice.node_id);if(!node||!choice.field||!choice.edit_action)throw new Error('请选择实际节点、字段和采用方式');
  const field=node[choice.field],index=choice.item_index===''?null:Number(choice.item_index);if(index!==null&&(!Number.isSafeInteger(index)||index<0))throw new Error('请选择原字段中的实际条目');
  if(typeof field==='string'&&(choice.edit_action!=='REPLACE'||index!==null))throw new Error('工作说明只支持明确替换');
  if(Array.isArray(field)&&choice.edit_action!=='ADD'&&(index===null||index>=field.length))throw new Error('替换或删除需要原字段中的实际条目');
  const text=choice.edit_action==='REMOVE'?null:choice.mode==='KEEP'?hunk.keep_text:choice.text;
  if(text!==null&&!text.trim())throw new Error('采用的文字不能为空');if(text!==null&&text.length>24000)throw new Error('这段改文过长，请明确改写或分条核对');
  if(choice.edit_action==='REMOVE'&&(hunk.added_text||typeof field==='string'||hunk.removed_text.split(/\r\n|[\n\r\v\f\u0085\u2028\u2029]/).map(line=>line.startsWith('- ')?line.slice(2):line).join('\n').trim()!==field[index!]))throw new Error('只能明确删除与原改文相同的列表条目');
  decisions.push({hunk_id:hunk.id,hunk_sha256:hunk.sha256,mode:choice.mode,target:{node_id:node.id,field:choice.field,edit_action:choice.edit_action,item_index:index,before_value_sha256:await writingDigest(field)},text,reason:choice.reason});
 }
 if(complete&&decisions.length!==hunks.size)throw new Error('还有人工修改尚未作出决定');return decisions;
}
function decidedFields(base:PlanningCandidate,decisions:ManualDecision[]){
 const nodes=structuredClone(base.nodes),fields=new Map<string,{node_id:string;field:ManualField}>(),removed=new Map<string,number[]>(),occupied=new Set<string>();
 for(const decision of decisions){if(decision.mode==='DISCARD')continue;const target=decision.target!,node=nodes.find(n=>n.id===target.node_id);if(!node)throw new Error('原节点已不存在');const key=JSON.stringify([node.id,target.field]);const slot=target.field==='description'?key:target.edit_action!=='ADD'?JSON.stringify([node.id,target.field,target.item_index]):null;if(slot&&occupied.has(slot))throw new Error('多处修改指向同一原字段或条目，请先明确各自目标');if(slot)occupied.add(slot);fields.set(key,{node_id:node.id,field:target.field});const value=node[target.field];
  if(typeof value==='string'){node.description=decision.text!;continue;}
  const i=target.item_index,actual=i===null?value.length:i-(removed.get(key)??[]).filter(v=>v<i).length;
  if(target.edit_action==='ADD')value.splice(i??value.length,0,decision.text!);else if(target.edit_action==='REPLACE')value[actual]=decision.text!;else{value.splice(actual,1);removed.set(key,[...removed.get(key)??[],i!]);}
 }
 return {nodes,fields};
}
export function applyManualDecisions(base:PlanningCandidate,current:PlanningCandidate,decisions:ManualDecision[],previous:ManualDecision[]){
 const before=decidedFields(base,previous),after=decidedFields(base,decisions),next=structuredClone(current);
 for(const {node_id,field} of new Map([...before.fields,...after.fields]).values()){
  const original=base.nodes.find(n=>n.id===node_id),local=next.nodes.find(n=>n.id===node_id),prior=before.nodes.find(n=>n.id===node_id),wanted=after.nodes.find(n=>n.id===node_id);if(!original||!local||!prior||!wanted)throw new Error('原节点已不存在，当前草稿未被覆盖');
  if(!same(local[field],original[field])&&!same(local[field],prior[field])&&!same(local[field],wanted[field]))throw new Error('该字段还有手工编辑，请先核对原字段；当前草稿未被覆盖');
  Object.assign(local,{[field]:structuredClone(wanted[field]),origin:'USER_STATEMENT'});
 }
 return next;
}
export async function prepareManualSave(proposal:PlanningProposal,candidate:PlanningCandidate,reason:string,review:ManualReview,operationId:string,requirementCoverage?:ClaimCoverageDraft):Promise<ManualSave>{
 const body={expected_revision:proposal.revision,candidate,change_reason:reason,manual_review:review,...requirementCoverage?{requirement_coverage:structuredClone(requirementCoverage)}:{}};return {operation_id:operationId,...body,request_sha256:await writingDigest({proposal_id:proposal.id,...body}),state:'PREPARED',result_ref:null};
}
export function verifyManualSave(value:PlanningProposal,proposal:PlanningProposal,original:ManualSave){
 const marker=(value?.payload as PlanningProposal['payload']&{manual_inheritance?:{capture_ref:ManualCaptureRef;basis_proposal_ref:RevisionRef;decisions:ManualDecision[]}[]})?.manual_inheritance?.find(item=>sameManualRef(item.capture_ref,original.manual_review.change_ref));
 if(value?.project_id!==proposal.project_id||value.id!==proposal.id||value.revision!==original.expected_revision+1||!hex(value.sha256)||!same(value.payload.candidate,original.candidate)||!marker||!sameManualRef(marker.basis_proposal_ref,manualReference(proposal))||!same(marker.decisions,original.manual_review.decisions))throw new Error('原保存结果尚未能核对；当前草稿与原操作已保留');
 if(original.requirement_coverage&&(!value.coverage||!coverageDraftEqual(coverageDraft(value.coverage),original.requirement_coverage)))throw new Error('原保存的事项承接尚未能核对');
 return value;
}
