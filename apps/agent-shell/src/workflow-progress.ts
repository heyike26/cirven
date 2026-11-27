import type {AgentSession} from './agent-sessions';

/** Todo is session-wide in OpenCode. A generation alone cannot identify its task. */
export function workflowTodoIsCurrent(session?:AgentSession):boolean{
 const binding=session?.work_mode_message;
 if(!session||!binding||!session.observation_is_current||binding.generation!==session.generation)return false;
 const users=new Set<string>();
 if(binding.native_message_id)users.add(binding.native_message_id);
 const operationIds=new Set([binding.operation_id,...(session.guidance??[])
  .filter(row=>row.generation===session.generation&&row.original_active_operation_id===binding.operation_id&&row.native_user_observed)
  .map(row=>row.operation_id)]);
 for(const message of session.messages){
  if(message.info.role==='user'&&operationIds.has(message.conversation_input_origin?.operation_id??''))users.add(message.info.id);
 }
 const writes:{parent:string;end:number;todos:unknown}[]=[];
 for(const message of session.messages){
  if(message.info.role!=='assistant'||typeof message.info.parentID!=='string')continue;
  if(session.original_session_id&&message.info.sessionID!==session.original_session_id)continue;
  for(const part of message.parts){
   const state=part.state as {status?:string;metadata?:{todos?:unknown};time?:{end?:number}}|undefined;
   if(part.type==='tool'&&part.tool==='todowrite'&&state?.status==='completed'&&Number.isFinite(state.time?.end))
    writes.push({parent:message.info.parentID,end:state.time!.end!,todos:state.metadata?.todos});
  }
 }
 writes.sort((a,b)=>b.end-a.end);
 if(!writes.length||writes.length>1&&writes[0].end===writes[1].end||!users.has(writes[0].parent))return false;
 const rows=(value:unknown)=>Array.isArray(value)?JSON.stringify(value.map(row=>
  row&&typeof row==='object'?[(row as Record<string,unknown>).content,(row as Record<string,unknown>).status,(row as Record<string,unknown>).priority]:null)):null;
 const observed=rows(writes[0].todos);
 return observed!==null&&observed===rows(session.todo);
}
