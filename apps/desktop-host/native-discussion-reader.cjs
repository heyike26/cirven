'use strict';

const ID=/^[A-Za-z0-9_-]{1,100}$/;
function scope(body,keys){
  if(!body||typeof body!=='object'||Array.isArray(body)||Object.keys(body).sort().join(',')!==[...keys].sort().join(',')||
    !(body.projectId===null||typeof body.projectId==='string'&&ID.test(body.projectId)))throw new Error('NATIVE_DISCUSSION_REFERENCE_REQUIRED');
  for(const key of keys.filter(key=>key!=='projectId'))if(typeof body[key]!=='string'||!ID.test(body[key]))throw new Error('NATIVE_DISCUSSION_REFERENCE_REQUIRED');
  return body.projectId===null?'':`project_id=${encodeURIComponent(body.projectId)}&`;
}
async function readNativeConversation(request,body){
  const query=scope(body,['projectId','conversationId','operationId']);
  const root='/api/desktop-host/native-conversations/'+encodeURIComponent(body.conversationId);
  const selectedPath=`${root}/operations/${encodeURIComponent(body.operationId)}?${query.slice(0,-1)}`;
  const operation=await request(selectedPath);
  const matches=value=>value?.project_id===body.projectId&&value.conversation_id===body.conversationId&&value.operation_id===body.operationId&&typeof value.request_sha256==='string'&&/^[a-f0-9]{64}$/.test(value.request_sha256);
  if(!matches(operation))throw new Error('NATIVE_DISCUSSION_REFERENCE_CHANGED');
  const result=await request(`${root}?${query}limit=30`);
  if(result?.kind!=='NATIVE_CONVERSATION'||result.project_id!==body.projectId||result.conversation_id!==body.conversationId||!Array.isArray(result.messages)||result.messages.length>30)throw new Error('NATIVE_DISCUSSION_REFERENCE_CHANGED');
  const selected=await request(selectedPath);
  if(!matches(selected)||selected.request_sha256!==operation.request_sha256)throw new Error('NATIVE_DISCUSSION_REFERENCE_CHANGED');
  const included=result.messages.find(row=>row.operation_id===body.operationId);
  if(included&&included.request_sha256!==selected.request_sha256)throw new Error('NATIVE_DISCUSSION_REFERENCE_CHANGED');
  if(included)return result;
  const messages=[selected,...result.messages.slice(-29)];
  while(messages.length>1&&Buffer.byteLength(JSON.stringify(messages),'utf8')>512*1024)messages.splice(1,1);
  if(Buffer.byteLength(JSON.stringify(messages),'utf8')>512*1024)throw new Error('NATIVE_DISCUSSION_PROJECTION_TOO_LARGE');
  return {...result,messages,coverage:{...result.coverage,returned:messages.length,has_more:true,complete:false,selected_operation_included:true}};
}
async function readNativeConversationDirectory(request,body){
  const query=scope(body,['projectId']);
  const result=await request(`/api/desktop-host/native-conversations?${query}limit=30`);
  if(result?.kind!=='NATIVE_CONVERSATION_DIRECTORY'||result.project_id!==body.projectId||!Array.isArray(result.conversations)||result.conversations.length>30)throw new Error('NATIVE_DISCUSSION_REFERENCE_CHANGED');
  return result;
}
module.exports={readNativeConversation,readNativeConversationDirectory};
