'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {NativeCompanion}=require('../native-companion.cjs');
const {conversationIdentity,conversationReference}=require('../conversation-reference.cjs');
const ref={id:'native:'+'a'.repeat(64),revision:3};
test('stable conversation references are metadata only and reject arbitrary targets, grants and malformed revisions',()=>{
 assert.deepEqual(conversationReference(ref),ref);assert.equal(conversationIdentity(ref.id),ref.id);assert.equal(conversationReference(undefined),null);
 for(const value of [{...ref,id:'../../other'},{...ref,revision:true},{...ref,revision:0},{...ref,project_id:'borrowed'},{...ref,allow_write:true}])assert.throws(()=>conversationReference(value));
});
test('a canonical next-message route uses one actual host POST without borrowing ambient page context',async()=>{
 const calls=[],companion=new NativeCompanion({window:{},platform:'win32',request:async(path,body)=>{calls.push({path,body});return {operation_id:body.operation_id,status:'NOT_CONFIGURED'};}});
 const body={operationId:'new_organized_turn',rawText:'Continue in this project.\n{"my JSON":true}',projectId:'target_project',conversationId:'target_alias',conversationRef:ref};
 await companion.submitMessage(body);assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/desktop-host/native-messages');assert.deepEqual(calls[0].body.conversation_ref,ref);assert.equal(calls[0].body.project_id,'target_project');assert.equal(calls[0].body.conversation_id,'target_alias');assert.equal(calls[0].body.context_ref,null);assert.equal(calls[0].body.raw_text,body.rawText);
 await assert.rejects(()=>companion.submitMessage({...body,conversationRef:{...ref,revision:4}}),/differs/);assert.equal(calls.length,1,'A bound operation cannot adopt a newer organization scope');
});
