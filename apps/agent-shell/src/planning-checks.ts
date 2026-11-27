export type PlanningCheckKind = 'network' | 'acceptance';
export type MissingPlanningField = 'outputs' | 'acceptance' | 'source_basis';
export type PlanningCheck =
  | {kind:'network';nodes:number;questions:number}
  | {kind:'acceptance';complete:number;total:number;issues:{id:string;missing:MissingPlanningField[]}[]};
export type PlanningCheckReceipt = {kind:PlanningCheckKind;basis:string;value:unknown};
const count = (value:unknown):value is number => Number.isSafeInteger(value) && (value as number)>=0;
const fields = new Set(['outputs','acceptance','source_basis']);

// These are deterministic completeness checks, not semantic or execution acceptance.
export function planningCheck(kind:PlanningCheckKind, input:unknown):PlanningCheck|undefined {
  if(!input || typeof input!=='object')return;
  const value=input as Record<string,unknown>;
  if(kind==='network') {
    if(value.valid!==true || !count(value.node_count) || !count(value.question_count) || value.semantic_correctness!=='UNASSESSED')return;
    return {kind,nodes:value.node_count,questions:value.question_count};
  }
  if(!Array.isArray(value.nodes) || !count(value.complete_nodes) || !count(value.total_tasks) ||
    value.business_criteria_quality!=='UNASSESSED' || value.verified_execution!==false || value.total_tasks!==value.nodes.length)return;
  const seen=new Set<string>();
  const rows:{id:string;missing:MissingPlanningField[]}[]=[];
  for(const row of value.nodes) {
    if(!row || typeof row.node_id!=='string' || !row.node_id || seen.has(row.node_id) || !Array.isArray(row.missing) ||
      row.missing.some((key:unknown)=>typeof key!=='string'||!fields.has(key)) || new Set(row.missing).size!==row.missing.length)return;
    seen.add(row.node_id);
    rows.push({id:row.node_id,missing:row.missing});
  }
  if(rows.filter(row=>!row.missing.length).length!==value.complete_nodes)return;
  return {kind,complete:value.complete_nodes,total:value.total_tasks,issues:rows.filter(row=>row.missing.length)};
}
