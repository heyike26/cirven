export type ContextUsageSummary={selection_id:string|null;selection_receipt_id:string|null;mode:'AUTO'|'MANUAL';status:string;project_id:string|null;conversation_id:string|null;scope_id?:string|null;selected_count:number;omitted_count:number;characters:number;model_calls:0;reason?:string};
const hash=(value:unknown)=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const project=(value:unknown)=>value===null||typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
export function contextUsageSummary(value:unknown):ContextUsageSummary|undefined{
  if(!value||typeof value!=='object'||Array.isArray(value))return;
  const row=value as Record<string,unknown>;
  if(!['AUTO','MANUAL'].includes(String(row.mode))||typeof row.status!=='string'||!/^[A-Z_]{1,80}$/.test(row.status)||!project(row.project_id)||row.model_calls!==0)return;
  if(row.conversation_id!==null&&!(typeof row.conversation_id==='string'&&/^(native|original|discovery):[a-f0-9]{64}$/.test(row.conversation_id)))return;
  if(row.selection_id!==null&&!hash(row.selection_id)||row.selection_receipt_id!==null&&!hash(row.selection_receipt_id))return;
  if(!Number.isSafeInteger(row.selected_count)||Number(row.selected_count)<0||Number(row.selected_count)>32||!Number.isSafeInteger(row.omitted_count)||Number(row.omitted_count)<0||Number(row.omitted_count)>8192||!Number.isSafeInteger(row.characters)||Number(row.characters)<0||Number(row.characters)>16000)return;
  return row as ContextUsageSummary;
}
