'use strict';
// AUTHORED_NOT_RUN: actual Main projection/HTTP adapter, injected API and notification boundary.
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const digest=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
function fixture(){const scope={project_id:'qa',session_ref:{id:'original',revision:1,sha256:'a'.repeat(64)},generation:1},workspace_ref={id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},context={client_id:'paired_client'},payload={operation_id:'capture_original',workspace_ref,permission_ref:null};
 const body={...payload,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:context.client_id};
 const common={id:payload.operation_id,capture_ref:'d'.repeat(64),scope:{...scope,workspace_ref},editor_client_id:context.client_id,request_sha256:digest(body),required_windows:2,expires_at_ms:Date.now()+12000,read_only:true,permission_granted:false,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,atomic_editor_write_guarantee:false};
 const receipt={kind:'EDITOR_CAPTURE_COHORT',...common,created_at_ms:Date.now(),observation_time_scope:'IMMUTABLE_CAPTURE_INTENT_NOT_CURRENT_CLEAN_STATE'};
 const status={kind:'EDITOR_CAPTURE_STATUS',...common,state:'WAITING',ready_windows:1,waiting_windows:1,dirty_windows:0,unavailable_windows:0,status_scope:'CURRENT_METADATA_SNAPSHOT_NOT_EXECUTION_LEASE',dispatch_requests:[{observer_id:'e'.repeat(64),editor_client_id:'other_paired_client',editor_window_id:'other_window',project_id:'other_project',capture_ref:common.capture_ref,expires_at_ms:common.expires_at_ms}]};
 return {scope,workspace_ref,context,payload,body,receipt,status};}
test('Main-only capture actor and lost HTTP conflict recover only original immutable intent',async()=>{
 const x=fixture(),calls=[],adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});if(body){assert.deepEqual(body,x.body);throw Object.assign(Error('UNIT lost original capture ACK'),{status:409});}return x.receipt;}});
 const receipt=await adapter('session.editor.capture.request',{scope:x.scope,payload:x.payload},x.context);assert.equal(receipt.permission_granted,false);assert.equal(receipt.required_windows,2);assert.equal(calls.filter(v=>v.body).length,1);assert.match(calls[1].url,/capture_original\?editor_client_id=paired_client/);
 await assert.rejects(adapter('session.editor.capture.request',{scope:x.scope,payload:{...x.payload,editor_client_id:'injected'}},x.context));
});
test('private dispatch routes only through the Main callback and never enters the requester view',async()=>{
 const x=fixture(),delivered=[],adapter=createIdeApiAdapter({request:async()=>x.status,notifyEditorCaptures:async rows=>delivered.push(rows)});
 const result=await adapter('session.editor.capture.status',{scope:x.scope,payload:{operation_id:x.payload.operation_id}},x.context);
 assert.equal(result.state,'WAITING');assert.equal(result.dispatch_requests,undefined);assert.equal(delivered.length,1);assert.equal(delivered[0][0].editor_client_id,'other_paired_client');assert.doesNotMatch(JSON.stringify(result),/other_project|other_window|other_paired_client/);
});
test('status cannot masquerade as an immutable intent or incomplete READY state',async()=>{
 const x=fixture();let value=x.status;const adapter=createIdeApiAdapter({request:async()=>value});
 await assert.rejects(adapter('session.editor.capture.operation',{scope:x.scope,payload:{operation_id:x.payload.operation_id}},x.context),{code:'EDITOR_CAPTURE_INVALID'});
 value={...x.status,state:'READY',ready_windows:1,waiting_windows:0};await assert.rejects(adapter('session.editor.capture.status',{scope:x.scope,payload:{operation_id:x.payload.operation_id}},x.context),{code:'EDITOR_CAPTURE_INVALID'});
 value={...x.status,editor_client_id:'forged'};await assert.rejects(adapter('session.editor.capture.status',{scope:x.scope,payload:{operation_id:x.payload.operation_id}},x.context),{code:'EDITOR_CAPTURE_INVALID'});
});
test('private notifications with a changed purpose digest cannot reach any observer',async()=>{
 const x=fixture(),delivered=[];x.status.dispatch_requests[0].capture_ref='f'.repeat(64);
 const adapter=createIdeApiAdapter({request:async()=>x.status,notifyEditorCaptures:async rows=>delivered.push(rows)});
 await assert.rejects(adapter('session.editor.capture.status',{scope:x.scope,payload:{operation_id:x.payload.operation_id}},x.context),{code:'EDITOR_CAPTURE_INVALID'});assert.equal(delivered.length,0);
});

// AUTHORED_NOT_RUN: waits are woken by metadata progress, no idle polling.
test('capture wait reads only on progress and returns the original current status without granting execution',async()=>{
 const x=fixture(),calls=[];let raw=x.status,version=0,wakes=0;
 const adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return raw;},notifyEditorCaptures:async()=>{},captureCursor:()=>version,waitEditorCapture:async(ref,options)=>{assert.equal(ref,x.status.capture_ref);assert.equal(options.after,0);wakes++;version++;raw={...x.status,state:'READY',ready_windows:2,waiting_windows:0,dispatch_requests:[]};return true;}});
 const result=await adapter('session.editor.capture.wait',{scope:x.scope,payload:{operation_id:x.payload.operation_id,capture_ref:x.status.capture_ref}},x.context);
 assert.equal(result.state,'READY');assert.equal(result.permission_granted,false);assert.equal(wakes,1);assert.equal(calls.length,2);assert.ok(calls.every(v=>v.body===undefined));
});
test('capture wait does one final read on its bounded timeout and never recreates the capture intent',async()=>{
 const x=fixture(),calls=[],adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return x.status;},notifyEditorCaptures:async()=>{},captureCursor:()=>0,waitEditorCapture:async()=>false});
 const result=await adapter('session.editor.capture.wait',{scope:x.scope,payload:{operation_id:x.payload.operation_id,capture_ref:x.status.capture_ref}},x.context);assert.equal(result.state,'WAITING');assert.equal(result.wait_deadline_reached,true);assert.equal(calls.length,2);assert.ok(calls.every(v=>v.body===undefined));
});
