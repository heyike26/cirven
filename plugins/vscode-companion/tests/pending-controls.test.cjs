'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {PrivateState,CompanionController,draftKey}=require('../controller.cjs');
const pending={kind:'PERMISSION',request_ref:{id:'per_native',sha256:'b'.repeat(64),generation:1},allowed_choices:['once','reject'],display_complete:true,native_request:{permission:'edit',patterns:['src/a.py'],questions:[]}};
function scene(t){const dir=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR||os.tmpdir(),'cirven-pending-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const c=new CompanionController({state:new PrivateState(dir)});c.selected={project_id:'p',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:1,active_operation_id:'task',pending_questions:[],pending_permissions:[pending],messages:[]};c.refreshSelected=async()=>{};return c;}
test('reply preserves composer and UNKNOWN blocks another POST even after local receipt reload',async t=>{
  const c=scene(t),calls=[];c.client={methods:['session.permission.reply'],rpc:async(method,scope,payload)=>{calls.push({method,scope,payload});return {status:'ERROR',error:{code:'OPERATION_OUTCOME_UNKNOWN'}};}};
  const key=draftKey(c.selected);c.setDraft(key,0,'人工尚未发送的下一条消息');const token=c.pendingControls()[0].token;
  await c.replyPending(token,{reply:'once'});assert.equal(c.state.value.requests[0].status,'UNKNOWN');assert.equal(c.snapshot().draft.text,'人工尚未发送的下一条消息');assert.equal(calls.length,1);assert.deepEqual(calls[0].payload.request_ref,pending.request_ref);
  await assert.rejects(c.replyPending(token,{reply:'once'}),{code:'ORIGINAL_REQUEST_UNRESOLVED'});assert.equal(calls.length,1);
});
test('stale parent, incomplete display and missing grant reject before transport',async t=>{
  const c=scene(t);let calls=0;c.client={methods:['session.permission.reply'],rpc:async()=>{calls++;}};const token=c.pendingControls()[0].token;c.selected.active_operation_id='new-task';
  await assert.rejects(c.replyPending(token,{reply:'once'}),{code:'PENDING_REQUEST_CHANGED'});
  c.selected.pending_permissions=[{...pending,display_complete:false}];await assert.rejects(c.replyPending(c.pendingControls()[0].token,{reply:'once'}),{code:'PENDING_REQUEST_CHANGED'});
  c.selected.pending_permissions=[pending];c.client.methods=[];await assert.rejects(c.replyPending(c.pendingControls()[0].token,{reply:'once'}),{code:'CAPABILITY_DENIED'});assert.equal(calls,0);
});
