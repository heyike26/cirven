'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {NativeCompanion}=require('../native-companion.cjs');
const {inputAttachments,inputPreview}=require('../native-input-attachments.cjs');
const ref={id:'input_source',revision:1,sha256:'a'.repeat(64)},attachment={kind:'SCREENSHOT',context_ref:ref,label:'保存的画面',regions:[],image_sha256:'b'.repeat(64)};
test('real companion dispatches explicit inputs without observing the ambient desktop or replacing raw text',async()=>{
 const calls=[],broker=new NativeCompanion({window:{},request:async(path,body)=>{calls.push({path,body});return {operation_id:body.operation_id,status:'NOT_CONFIGURED'};},platform:'win32'});
 const rawText='用户正文\n{"literal":true}';await broker.submitMessage({operationId:'typed_inputs',rawText,conversationId:'original_chat',inputAttachments:[attachment]});
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/desktop-host/native-messages');assert.equal(calls[0].body.raw_text,rawText);assert.deepEqual(calls[0].body.input_attachments,[attachment]);assert.equal(calls[0].body.context_ref,null);
 await assert.rejects(()=>broker.submitMessage({operationId:'typed_inputs',rawText:'changed',inputAttachments:[attachment]}),/differs/);assert.equal(calls.length,1);
});
test('host rejects injected paths, source replacement, excess attachments and screenshot-shaped page references',async()=>{
 for(const change of [{...attachment,path:'D:/unselected'},{...attachment,context_ref:{...ref,hwnd_decimal:'3'}},{...attachment,kind:'CURRENT_PAGE'},{...attachment,image_sha256:'invalid'}])assert.throws(()=>inputAttachments([change]));
 assert.throws(()=>inputAttachments(Array(21).fill(attachment)));
 let reads=0;await assert.rejects(()=>inputPreview({contextRef:ref,imageSha256:attachment.image_sha256,view:'FULL'},{request:async()=>({current:false,automatic_input_authorized:false,observation:{context_ref:ref},preview:{image_ref:{sha256:attachment.image_sha256,download_url:'https://outside/secret'}}}),imageReader:async()=>{reads++;}}),/exact input/);assert.equal(reads,0);
});
