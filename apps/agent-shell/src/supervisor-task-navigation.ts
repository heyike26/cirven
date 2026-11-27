import type {SupervisorConversationClientSnapshot,SupervisorConversationOperation} from './supervisor-conversation-client';
import type {SupervisorCenterReplyStatus} from './SupervisorCommandCenter';

export interface SupervisorOperationSelection {
  operation_id:string;request_sha256:string;question:string;answer_text:string;status:SupervisorCenterReplyStatus;
}
type TaskKey={kind:string;project_id?:string|null;object_id:string;operation_id?:string|null;generation:number};
type Detail={key:TaskKey;task:{key:TaskKey};input_text:string;answer_text:string;continuation?:{kind:string;operation_id:string;request_sha256:string}};
const identity=(key:TaskKey)=>JSON.stringify([key.kind,key.project_id??null,key.object_id,key.operation_id??null,key.generation]);
export function selectedSupervisorOperation(detail:Detail,snapshot:SupervisorConversationClientSnapshot|undefined,readOperation?:SupervisorConversationOperation):SupervisorOperationSelection {
  const target=detail.continuation,key=detail.key;
  if(!snapshot||target?.kind!=='supervisor'||key.kind!=='native_message'||key.project_id!=null||key.object_id!==key.operation_id||
    identity(detail.task.key)!==identity(key)||target.operation_id!==key.operation_id)throw new Error('SUPERVISOR_TASK_REFERENCE_CHANGED');
  const known=snapshot.operations.find(row=>row.operation_id===target.operation_id);
  if(known&&known.request_sha256!==target.request_sha256)throw new Error('SUPERVISOR_TASK_REFERENCE_CHANGED');
  const operation=known??readOperation;
  if(operation&&(operation.operation_id!==target.operation_id||operation.request_sha256!==target.request_sha256))throw new Error('SUPERVISOR_TASK_REFERENCE_CHANGED');
  if(!operation||!['RETURNED','UNKNOWN','REJECTED','PROCESSING','REQUESTING','RUNNING','FAILED','CANCELLED'].includes(operation.status)||
    typeof detail.input_text!=='string'||!detail.input_text.trim()||detail.input_text.length>8000||typeof detail.answer_text!=='string'||detail.answer_text.length>48000)
    throw new Error('SUPERVISOR_TASK_REFERENCE_CHANGED');
  return {operation_id:operation.operation_id,request_sha256:operation.request_sha256,question:detail.input_text,answer_text:detail.answer_text,status:operation.status as SupervisorCenterReplyStatus};
}
