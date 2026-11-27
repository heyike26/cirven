'use strict';
// Production private journal/controller; controlled transport, no model or IDE.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {PrivateState,CompanionController,draftKey}=require('../controller.cjs');
const {canonical,hash}=require('../client.cjs'),native=require('../native-conversation.cjs');
const scope={project_id:'project',conversation_id:'discussion'},profile={id:'profile',revision:1,sha256:'a'.repeat(64)};
function conversation(messages=[]){return {kind:'NATIVE_CONVERSATION',...scope,messages,latest_provider_profile_ref:profile,coverage:{returned:messages.length,has_more:false}};}
function fixture(t){const directory=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-admission83-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));let sequence=0;const c=new CompanionController({state:new PrivateState(directory),newId:()=>`op-${++sequence}`});c.projectId=scope.project_id;c.selected=native.present(conversation(),scope);const key=draftKey(c.selected);c.setDraft(key,0,'The exact user input 😀');return {c,key};}
function originalBody(payload){return {...scope,operation_id:payload.operation_id,raw_text:payload.raw_text,context_ref:null,provider_profile_ref:payload.provider_profile_ref,file_source_ref:payload.file_source_ref??null,file_link_ref:payload.file_link_ref??null,...(payload.native_feedback_ref?{native_feedback_ref:payload.native_feedback_ref}:{})};}
async function lost(c,key){let payload;c.client={rpc:async (m,s,p)=>{if(m==='native.message'){payload=p;throw Error('CONTROLLED_LOST_ACK');}return {status:'OK',result:conversation()};}};await c.send(key,1);return originalBody(payload);}

test('lost admission persists full normalized body digest and exact-key check clears only the original draft',async t=>{
 const {c,key}=fixture(t),body=await lost(c,key),entry=c.state.value.requests[0];
 assert.equal(entry.native_body_sha256,hash(canonical(body)));assert.equal(c.snapshot().draftUnresolved,true);
 let reads=0;c.client.rpc=async(m,s,p)=>{reads++;assert.equal(m,'native.operation');assert.deepEqual(s,scope);assert.equal(p.operation_id,body.operation_id);return {status:'OK',result:{kind:'NATIVE_MESSAGE',...scope,operation_id:body.operation_id,request_sha256:hash(canonical(body)),status:'PROCESSING'}};};
 await c.checkResult(entry.request_id);assert.equal(reads,1);assert.equal(c.state.value.requests[0].status,'OK');assert.equal(c.state.value.drafts[key].text,'');assert.equal(c.snapshot().draftUnresolved,false);
});

for(const changed of ['project','conversation','operation','body'])test('exact-key check rejects changed '+changed+' identity and preserves unknown/draft',async t=>{
 const {c,key}=fixture(t),body=await lost(c,key),entry=c.state.value.requests[0];let reads=0;
 const result={kind:'NATIVE_MESSAGE',...scope,operation_id:body.operation_id,request_sha256:hash(canonical(body)),status:'RETURNED'};
 if(changed==='project')result.project_id='other';if(changed==='conversation')result.conversation_id='other';if(changed==='operation')result.operation_id='another';if(changed==='body')result.request_sha256='b'.repeat(64);
 c.client.rpc=async m=>{reads++;assert.equal(m,'native.operation');return {status:'OK',result};};
 await assert.rejects(()=>c.checkResult(entry.request_id),{code:'OPERATION_INPUT_MISMATCH'});assert.equal(reads,1);assert.equal(c.state.value.requests[0].status,'UNKNOWN');assert.equal(c.state.value.drafts[key].text,body.raw_text);
});

test('late original acceptance never clears newer edits or changes the selected discussion',async t=>{
 const {c,key}=fixture(t),body=await lost(c,key),entry=c.state.value.requests[0];let release;
 c.client.rpc=async()=>await new Promise(r=>release=r);const checking=c.checkResult(entry.request_id);
 c.setDraft(key,1,'A newer unsent user edit');c.selected=native.present({...conversation(),conversation_id:'other'},{...scope,conversation_id:'other'});c.error='CURRENT_DISCUSSION_ERROR';
 release({status:'OK',result:{kind:'NATIVE_MESSAGE',...scope,operation_id:body.operation_id,request_sha256:hash(canonical(body)),status:'RETURNED'}});await checking;
 assert.equal(c.state.value.requests[0].status,'OK');assert.equal(c.state.value.drafts[key].text,'A newer unsent user edit');assert.equal(c.selected.conversation_id,'other');assert.equal(c.error,'CURRENT_DISCUSSION_ERROR');
});

test('legacy unknown without full input digest remains unknown after read-only observation',async t=>{
 const {c,key}=fixture(t),body=await lost(c,key);c.state.commit(v=>delete v.requests[0].native_body_sha256);const entry=c.state.value.requests[0],before=JSON.stringify(entry);
 c.client.rpc=async()=>({status:'OK',result:{kind:'NATIVE_MESSAGE',...scope,operation_id:body.operation_id,request_sha256:hash(canonical(body)),status:'RETURNED'}});
 await c.checkResult(entry.request_id);assert.equal(JSON.stringify(c.state.value.requests[0]),before);assert.equal(c.state.value.drafts[key].text,body.raw_text);
});

test('recovered new-format unknown can confirm the exact input without modifying its source slot',async t=>{
 const {c,key}=fixture(t),body=await lost(c,key),filename=c.state.filename,before=fs.readFileSync(filename),state=new PrivateState(c.state.directory);
 state.recover(path.basename(filename));const recovered=new CompanionController({state});recovered.selected=native.present(conversation(),scope);
 recovered.client={rpc:async(m,s,p)=>{assert.equal(m,'native.operation');assert.equal(p.operation_id,body.operation_id);return {status:'OK',result:{kind:'NATIVE_MESSAGE',...scope,operation_id:body.operation_id,request_sha256:hash(canonical(body)),status:'RETURNED'}};}};
 await recovered.checkResult(state.value.requests[0].request_id);assert.equal(state.value.requests[0].status,'OK');assert.equal(state.value.drafts[key].text,'');assert.deepEqual(fs.readFileSync(filename),before);
});

test('startup is an active task but cannot turn typed input into a second dispatch',async t=>{
 const {c,key}=fixture(t);c.selected=native.present(conversation([{kind:'NATIVE_MESSAGE',...scope,input:{origin:'ORIGINAL_NATIVE_REQUEST',raw_text:'Original cold task'},operation_id:'starting',status:'PROCESSING',record_origin:'CURRENT_HOST',native_message_phase:'STARTING',native_message_ref:{id:'starting',revision:1,sha256:'c'.repeat(64)},control_capabilities:{can_guide:false,can_status:false,can_cancel:true}}]),scope);let calls=0;c.client={rpc:async()=>{calls++;throw Error('must not dispatch');}};
 await assert.rejects(()=>c.send(key,1),{code:'SESSION_NOT_READY'});assert.equal(calls,0);assert.equal(c.state.value.drafts[key].text,'The exact user input 😀');const stopping={...c.selected.active_native_message,native_message_phase:'STOP_REQUESTED',control_capabilities:{can_guide:false,can_status:false,can_cancel:false}};c.selected=native.present(conversation([stopping]),scope);assert.equal(c.selected.active_operation_id,'starting');await assert.rejects(()=>c.send(key,1),{code:'SESSION_NOT_READY'});assert.equal(calls,0);
});
