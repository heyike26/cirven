import {deliveryComplete,type DeliveryOperation} from './planning-delivery';
import type {DiscoverySession} from './discovery-contracts';
import type {PlanningProposal,TaskBook,Harness,ReportArtifact} from './contracts';
export type ReviewObject={key:string;kind:'discovery'|'proposal'|'taskbook'|'harness'|'report';id:string;title:string;revision:number;sha256:string;planRevision?:number;pending:number;review:boolean;summary:string;data:DiscoverySession|PlanningProposal|TaskBook|Harness|ReportArtifact};
export function reviewObjects(pid:string,input:{sessions:DiscoverySession[];proposals:PlanningProposal[];books:TaskBook[];harnesses:Harness[];reports:ReportArtifact[];deliveries?:DeliveryOperation[]}):ReviewObject[]{
 const rows:ReviewObject[]=[];
 // Only an explicit complete receipt links a taskbook's exact bytes to a plan.
 // Equal titles, intake IDs, latest timestamps and nearby revisions are not lineage.
 const origins=new Map<string,Map<string,number>>();
 for(const delivery of input.deliveries??[]){
  if(!deliveryComplete(delivery)||!delivery.proposal_ref)continue;
  const plan=delivery.proposal_ref,book=delivery.taskbook_ref;
  if(!input.proposals.some(value=>value.project_id===pid&&value.id===plan.id)||!Number.isSafeInteger(plan.revision)||plan.revision<1||!/^[a-f0-9]{64}$/.test(plan.sha256)||!book?.taskbook_id||!book.sha256)continue;
  const key=JSON.stringify([book.taskbook_id,book.revision,book.sha256]);
  let versions=origins.get(key);if(!versions){versions=new Map();origins.set(key,versions);}versions.set(JSON.stringify([plan.id,plan.revision,plan.sha256]),plan.revision);
 }
 const planRevision=(book:TaskBook)=>{const versions=origins.get(JSON.stringify([book.id,book.revision,book.sha256]));return versions?.size===1?[...versions.values()][0]:undefined;};
 for(const s of input.sessions.filter(x=>x.project_id===pid)){const resolved=new Set((s.payload.resolutions??[]).flatMap(raw=>{const r=raw as {question_id?:string};return r?.question_id?[r.question_id]:[];}));const pending=s.payload.candidate.questions.filter(q=>!resolved.has(q.id)).length;rows.push({key:`discovery:${s.id}:${s.revision}:${s.sha256}`,kind:'discovery',id:s.id,title:s.payload.candidate.title,revision:s.revision,sha256:s.sha256,pending,review:!s.source_current,summary:s.payload.status==='CONFIRMED'?'要求已确认':'要求与关系图',data:s});}
 for(const s of input.proposals.filter(x=>x.project_id===pid)){const flow=(s.flow as {nodes?:{id:string;kind?:string;planning_status?:string}[]})?.nodes??[];const resolved=new Set(flow.filter(x=>x.kind==='QUESTION'&&x.planning_status==='RESOLVED').map(x=>x.id));const pending=s.payload.candidate.questions.filter(q=>!resolved.has(q.id)).length;rows.push({key:`proposal:${s.id}:${s.revision}:${s.sha256}`,kind:'proposal',id:s.id,title:s.payload.candidate.title,revision:s.revision,sha256:s.sha256,pending,review:s.review_status!=='CURRENT',summary:s.payload.status==='CONFIRMED'?'计划已确认':'任务与依赖图',data:s});}
 for(const [kind,values] of [['taskbook',input.books],['harness',input.harnesses]] as const)for(const s of values.filter(x=>x.project_id===pid))rows.push({key:`${kind}:${s.id}:${s.revision}:${s.sha256}`,kind,id:s.id,title:s.payload.title,revision:s.revision,sha256:s.sha256,pending:0,review:s.review_status!=='CURRENT',summary:kind==='harness'?'提示词与任务链':'可编辑任务书',planRevision:kind==='taskbook'?planRevision(s as TaskBook):undefined,data:s});
 for(const s of input.reports)if(!s.project_id||s.project_id===pid)rows.push({key:`report:${s.id}:${s.sha256}`,kind:'report',id:s.id,title:s.filename??s.id,revision:1,sha256:s.sha256,pending:0,review:false,summary:'实际报告文件',data:s});
 return rows;
}
