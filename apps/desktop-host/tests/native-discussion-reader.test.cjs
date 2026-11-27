'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {readNativeConversation,readNativeConversationDirectory}=require('../native-discussion-reader.cjs');
test('unbound history uses an explicit null scope and reads the selected operation before its discussion',async()=>{
 const paths=[],body={projectId:null,conversationId:'discussion',operationId:'original-operation'};
 const value=await readNativeConversation(async path=>{paths.push(path);return path.includes('/operations/')?{project_id:null,conversation_id:'discussion',operation_id:'original-operation',request_sha256:'a'.repeat(64)}:{kind:'NATIVE_CONVERSATION',project_id:null,conversation_id:'discussion',messages:[]}},body);
 assert.equal(value.project_id,null);assert.equal(paths.length,3);assert.ok(paths[0].includes('/operations/original-operation?'));assert.ok(paths.every(path=>!path.includes('project_id=')));
 assert.equal(value.messages[0].operation_id,'original-operation');assert.equal(value.coverage.complete,false);
});
test('a selected operation outside the recent window is retained with honest partial coverage',async()=>{
 const op={project_id:'p',conversation_id:'chat',operation_id:'old',request_sha256:'a'.repeat(64)};
 const recent=Array.from({length:30},(_,index)=>({...op,operation_id:'recent'+index}));
 const value=await readNativeConversation(async path=>path.includes('/operations/')?op:{kind:'NATIVE_CONVERSATION',project_id:'p',conversation_id:'chat',messages:recent,coverage:{complete:true}}, {projectId:'p',conversationId:'chat',operationId:'old'});
 assert.equal(value.messages.length,30);assert.equal(value.messages[0],op);assert.equal(value.coverage.has_more,true);assert.equal(value.coverage.complete,false);
});
test('a changed selected request between the first and final read cannot publish the stale discussion',async()=>{
 let reads=0;const op={project_id:null,conversation_id:'chat',operation_id:'old'};
 await assert.rejects(readNativeConversation(async path=>path.includes('/operations/')?{...op,request_sha256:(++reads===1?'a':'b').repeat(64)}:{kind:'NATIVE_CONVERSATION',project_id:null,conversation_id:'chat',messages:[]},{projectId:null,conversationId:'chat',operationId:'old'}),/REFERENCE_CHANGED/);
});
test('a changed operation scope rejects before reading a different discussion',async()=>{
 let reads=0;await assert.rejects(readNativeConversation(async()=>{reads++;return {project_id:'other',conversation_id:'discussion',operation_id:'original-operation'}},{projectId:'project',conversationId:'discussion',operationId:'original-operation'}),/REFERENCE_CHANGED/);assert.equal(reads,1);
});
test('history APIs cannot accept a missing scope, arbitrary URL, path or secret-bearing option',async()=>{
 for(const body of [{},{projectId:undefined},{projectId:null,url:'https://example.invalid'},{projectId:'../project'},{projectId:null,conversationId:'a',operationId:'op',token:'secret'}]){
  let reads=0;await assert.rejects(readNativeConversationDirectory(async()=>{reads++;},body),/REFERENCE_REQUIRED/);assert.equal(reads,0);
 }
});
test('directory preserves partial coverage and never resolves execution or adds requests',async()=>{
 const paths=[],result={kind:'NATIVE_CONVERSATION_DIRECTORY',project_id:'p',conversations:[],coverage:{complete:false,has_more:true}};
 assert.equal(await readNativeConversationDirectory(async path=>{paths.push(path);return result;},{projectId:'p'}),result);
 assert.deepEqual(paths,['/api/desktop-host/native-conversations?project_id=p&limit=30']);
});
