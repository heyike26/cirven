'use strict';
const {supervisorConversationDigest:digest}=require('./supervisor-conversation.cjs');

const identity=key=>JSON.stringify([key?.kind,key?.project_id??null,key?.object_id,key?.operation_id??null,key?.generation]);
const operationIndex=store=>new Map((store?.snapshot().operations??[]).map(row=>[row.operation_id,row]));
function verifiedOperation(operations,task){
  const key=task?.key;
  if(key?.kind!=='native_message'||key.project_id!=null||key.object_id!==key.operation_id)return undefined;
  const operation=typeof operations==='function'?operations(key.operation_id):operations.get(key.operation_id);
  if(!operation||task.source_ref?.id!==operation.operation_id||task.source_ref?.revision!==1||
      task.source_ref?.sha256!==digest({request_sha256:operation.request_sha256,status:task.source_status}))return undefined;
  return operation;
}
function operationLookup(store){return typeof store?.lookupOperation==='function'?id=>store.lookupOperation(id):operationIndex(store);}
function ownedSupervisorOperation(store,task){return verifiedOperation(operationLookup(store),task);}
function originalQuestion(store,operation){
  if(typeof store?.questionForOperation==='function')return store.questionForOperation(operation.operation_id,operation.request_sha256);
  if(store?.supervisorConversationTestDouble!==true)throw new Error('SUPERVISOR_QUESTION_UNAVAILABLE');
  const snapshot=store.snapshot();
  if(!snapshot.operations.some(row=>row.operation_id===operation.operation_id&&row.request_sha256===operation.request_sha256))
    throw new Error('SUPERVISOR_QUESTION_REFERENCE_CHANGED');
  const question=snapshot.messages.find(row=>row.role==='user'&&row.operation_id===operation.operation_id);
  if(!question||typeof question.text!=='string')throw new Error('SUPERVISOR_QUESTION_UNAVAILABLE');
  return question.text;
}
function projectSupervisorSnapshot(store,snapshot,label){
  if(!snapshot)return snapshot;
  const ownedKeys=new Set(),operations=operationLookup(store);
  const tasks=snapshot.tasks.map(row=>{if(!verifiedOperation(operations,row))return row;ownedKeys.add(identity(row.key));return {...row,label};});
  return {...snapshot,tasks,alerts:snapshot.alerts.map(row=>ownedKeys.has(identity(row.key))?{...row,label}:row)};
}
async function readSupervisorTaskDetail({store,key,snapshot,request,label}){
  const selectedIdentity=identity(key),selected=snapshot?.tasks.find(row=>identity(row.key)===selectedIdentity);
  if(!selected)throw new Error('SUPERVISOR_TASK_REFERENCE_CHANGED');
  const original={...selected,key:{...selected.key},...selected.source_ref?{source_ref:{...selected.source_ref}}:{}};
  const initialOperation=verifiedOperation(operationLookup(store),original);
  const initial=initialOperation?{operation_id:initialOperation.operation_id,request_sha256:initialOperation.request_sha256}:undefined;
  const query=new URLSearchParams({kind:key.kind,object_id:key.object_id,generation:String(key.generation),
    ...key.project_id?{project_id:key.project_id}:{},...key.operation_id?{operation_id:key.operation_id}:{}});
  const detail=await request('/api/supervisor/task-detail?'+query);
  if(identity(detail?.key)!==selectedIdentity||identity(detail?.task?.key)!==selectedIdentity)throw new Error('SUPERVISOR_TASK_REFERENCE_CHANGED');
  const operations=operationLookup(store),owned=verifiedOperation(operations,original);
  if(!!initial!==!!owned||initial&&owned.request_sha256!==initial.request_sha256)throw new Error('SUPERVISOR_TASK_REFERENCE_CHANGED');
  if(!owned){
    if(!Object.hasOwn(detail,'continuation'))return detail;
    const {continuation,...ordinary}=detail;return ordinary;
  }
  if(!verifiedOperation(operations,detail.task))throw new Error('SUPERVISOR_TASK_REFERENCE_CHANGED');
  return {...detail,input_text:originalQuestion(store,owned),task:{...detail.task,label},
    continuation:{kind:'supervisor',operation_id:owned.operation_id,request_sha256:owned.request_sha256}};
}
module.exports={projectSupervisorSnapshot,readSupervisorTaskDetail,ownedSupervisorOperation};
