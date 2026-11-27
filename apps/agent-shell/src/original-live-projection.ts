import {originalReadableParts,originalCompletedToolOnly,type OriginalMessage,type AgentSession} from './agent-sessions.ts';
import type {ThreadMessage} from './conversation-thread.ts';

/** The saved user turn owns its failure state. Do not append its old transport
 * placeholder after newer answers. Authored answers and unmatched errors stay. */
export function showOriginalLiveMessage(message:OriginalMessage,owner:Pick<AgentSession,'project_id'|'session_ref'>&Partial<Pick<AgentSession,'owner_status'|'connected_owner'|'observation_is_current'|'active_operation_id'>>,saved:ThreadMessage[]){
 const scoped=saved.filter(row=>row.source.kind==='original'&&row.source.project_id===owner.project_id&&row.source.session_id===owner.session_ref.id);
 if(scoped.some(row=>row.native_message_id===message.info.id))return false;
 if(originalCompletedToolOnly(message))return false;
 const parts=originalReadableParts(message);
 if(message.info.role==='assistant'&&typeof message.info.parentID==='string'&&parts.length&&parts.every(part=>part.kind==='tool')){
  const parent=scoped.find(row=>row.role==='user'&&row.native_message_id===message.info.parentID&&['FAILED','CANCELLED','UNKNOWN_OUTCOME','UNAVAILABLE','NOT_DISPATCHED'].includes(row.status??''));
  const stopped=owner.connected_owner===false&&['EXPIRED','DISCONNECTED','CLOSED'].includes(owner.owner_status??'');
  // Failed startup clears the active pointer before it reports failure. The
  // exact saved UNKNOWN parent carries uncertainty; its old transport cannot
  // truthfully advertise current work, even before owner teardown is observed.
  const failedStartup=owner.connected_owner===false&&owner.owner_status==='BLOCKED'&&owner.active_operation_id===null&&!!parent?.operation_id;
  const newerObservedOwner=owner.connected_owner===true&&owner.observation_is_current===true&&parent?.operation_id&&owner.active_operation_id!==undefined&&owner.active_operation_id!==parent.operation_id;
  if(parent&&(stopped||failedStartup||newerObservedOwner||parts.every(part=>part.kind==='tool'&&!['running','pending'].includes(part.status))))return false;
 }
 return parts.length>0||message.info.role==='assistant'&&!!message.info.error;
}
