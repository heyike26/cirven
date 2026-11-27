'use strict';
// Main protocol/admission tests only; native Dialog/OS qualification is separate.
const test=require('node:test');const assert=require('node:assert/strict');
const {NativeAppendHost,publicState}=require('../native-append-host.cjs');
const ref={id:'preview',revision:1,sha256:'a'.repeat(64)};
const approval={id:'approval',revision:1,sha256:'b'.repeat(64)};
function fixture(overrides={}) {
  const calls=[],dialogs=[],activations=[];
  const base={adapter_id:'classic_notepad_append',state:'PREPARED',automatic_retry:false,
    task_operation_id:'op',preview_ref:ref};
  const state={ready:true,commit:'HANDOFF_COMMITTED'};
  const request=async(url,body)=>{
    calls.push({url,body});
    if(url.includes('native-append-host/get?'))return {task_state:base};
    if(url.includes('actual-review'))return {status:'REVIEW_READY',operation_id:'op',preview_ref:ref,review_ref:ref,
      can_show_dialog:state.ready,payload_text:'服务器复核的新增🙂',target_label:'确切记事本',
      baseline:{},task_state:base};
    if(url.endsWith('dialog-result'))return body.result==='APPROVE'?{
      status:'APPROVED_HANDOFF_REQUIRED',operation_id:'op',preview_ref:ref,approval_ref:approval,ticket_ref:ref,
      activation_target:{process_creation_decimal:'134049827366302599'},activation_host:{marker_name:'private'},
      expires_unix_ms:Date.now()+59000,task_state:base}:{status:'DECLINED',task_state:{...base,state:'APPROVAL_CANCELLED'}};
    if(url.endsWith('commit-handoff'))return {status:state.commit,task_state:{...base,state:'UNKNOWN_OUTCOME'}};
    if(url.endsWith('/start')){state.ready=false;return {task_state:{...base,state:'RUNNING',approval_ref:approval}};}
    return base;
  };
  const broker=new NativeAppendHost({window:{isDestroyed:()=>false},platform:'win32',
    dialog:{showMessageBox:async(_window,options)=>{dialogs.push(options);return {response:0};}},request,
    activate:async value=>{activations.push(value);return {status:'ACTIVATION_REQUEST_RETURNED'};},...overrides});
  return {broker,calls,dialogs,activations,state,base};
}
test('only actual server review literal is displayed; native approval performs one handoff and start',async()=>{
  const f=fixture();const result=await f.broker.approve({operationId:'op',previewRef:ref});
  assert.equal(result.state,'RUNNING');assert.equal(f.dialogs.length,1);
  assert.match(f.dialogs[0].detail,/服务器复核的新增🙂/);assert.equal(f.dialogs[0].defaultId,1);
  assert.equal(f.activations[0].activation_target.process_creation_decimal,'134049827366302599');
  assert.deepEqual(f.calls.map(c=>c.url.split('/').at(-1)),['actual-review?operation_id=op','dialog-result','commit-handoff','start']);
  assert.equal(f.calls[1].body.result,'APPROVE');assert.equal(f.calls[2].body.helper_outcome,'RETURNED');
  assert.equal(JSON.stringify(result).includes('activation_host'),false);
  await f.broker.approve({operationId:'op',previewRef:ref});
  assert.equal(f.dialogs.length,1);assert.equal(f.activations.length,1);
  assert.equal(f.calls.filter(c=>c.url.endsWith('/start')).length,1);
});
test('native cancellation is explicit and never invokes activation or start',async()=>{
  const f=fixture({dialog:{showMessageBox:async()=>({response:1})}});
  const result=await f.broker.approve({operationId:'op',previewRef:ref});
  assert.equal(result.state,'APPROVAL_CANCELLED');assert.equal(result.approval_ref,undefined);
  assert.equal(f.activations.length,0);assert.equal(f.calls.length,2);assert.equal(f.calls[1].body.result,'DECLINE');
});
test('unknown helper completion is committed as unknown without retry or input start',async()=>{
  let count=0;const f=fixture({activate:async()=>{count++;throw new Error('lost result');}});f.state.commit='UNKNOWN_OUTCOME';
  const result=await f.broker.approve({operationId:'op',previewRef:ref});
  assert.equal(result.state,'UNKNOWN_OUTCOME');assert.equal(count,1);
  assert.equal(f.calls.at(-1).body.helper_outcome,'UNKNOWN');assert.equal(f.calls.filter(c=>c.url.endsWith('/start')).length,0);
});
test('renderer cannot inject approval, native identity, trusted gesture, baseline or private tickets',async()=>{
  const f=fixture();
  for(const extra of [{approved:true},{hwnd:123},{main_gesture_ref:ref},{ticket_ref:ref},{baseline:{}}]){
    assert.throws(()=>f.broker.approve({operationId:'op',previewRef:ref,...extra}),/Unsupported/);
  }
  assert.throws(()=>f.broker.start({operationId:'op',previewRef:ref,approvalRef:{...approval,revision:true}}),/reference/);
  assert.equal(f.calls.length,0);assert.equal(f.dialogs.length,0);
});
test('stale preview cannot display a dialog or activate anything',async()=>{
  const f=fixture();await assert.rejects(f.broker.approve({operationId:'op',previewRef:{...ref,sha256:'c'.repeat(64)}}),/preview changed/);
  assert.equal(f.calls.length,1);assert.equal(f.dialogs.length,0);assert.equal(f.activations.length,0);
});
test('concurrent approval is rejected and closing app stops after native dialog',async()=>{
  let release,closed=false;
  const f=fixture({isClosing:()=>closed,dialog:{showMessageBox:()=>new Promise(resolve=>{release=resolve;})}});
  const pending=f.broker.approve({operationId:'op',previewRef:ref});await new Promise(r=>setImmediate(r));
  await assert.rejects(f.broker.approve({operationId:'op',previewRef:ref}),/already in progress/);
  closed=true;release({response:0});await assert.rejects(pending,/closing/);
  assert.equal(f.calls.length,1);assert.equal(f.activations.length,0);
});
test('public envelope drops host-only extensions and GET never performs approval/start',async()=>{
  const f=fixture();await f.broker.get({operationId:'op'});
  assert.equal(f.calls.length,1);assert.match(f.calls[0].url,/native-append-host\/get\?operation_id=op$/);
  const value=publicState({...f.base,activation_host:{secret:true},ticket:{private:true},host_token:'secret'});
  assert.deepEqual(value,f.base);
});
test('takeover stops the exact pending Main helper and fences late handoff before commit/start',async()=>{
  let release,stopped=0;
  const activate=()=>new Promise(resolve=>{release=resolve;});
  activate.cancelPending=()=>{stopped++;release({status:'UNKNOWN'});return {status:'OWN_HELPER_STOP_REQUESTED',owned_helpers:1,foreground_effect:'UNKNOWN'};};
  const f=fixture({activate});
  const original=f.broker.request;
  f.broker.request=async(url,body)=>url.endsWith('/revoke')?{status:'REVOKED',intent_epoch:2,owner:'USER',mutation_authority:false,cancellation:{confirmed:true,status:'CONFIRMED'}}:original(url,body);
  const pending=f.broker.approve({operationId:'op',previewRef:ref});
  while(!release)await new Promise(resolve=>setImmediate(resolve));
  const rejected=assert.rejects(pending,/已请求接管/);
  const result=await f.broker.revoke({operationId:'takeover-op',expectedIntentEpoch:1});await rejected;
  assert.equal(stopped,1);assert.equal(result.status,'REVOKED');
  assert.equal(result.cancellation.confirmed,true);assert.equal(result.foreground_handoff_stop.foreground_effect,'UNKNOWN');
  assert.equal(f.calls.filter(call=>call.url.endsWith('commit-handoff')||call.url.endsWith('/start')).length,0);
});
test('a takeover while the native dialog is open fences a late approval before dialog-result',async()=>{
  let release;const f=fixture({dialog:{showMessageBox:()=>new Promise(resolve=>{release=resolve;})}});
  const original=f.broker.request;
  f.broker.request=async(url,body)=>url.endsWith('/revoke')?{status:'REVOKED',intent_epoch:2,owner:'USER',mutation_authority:false}:original(url,body);
  const pending=f.broker.approve({operationId:'op',previewRef:ref});await new Promise(resolve=>setImmediate(resolve));
  await f.broker.revoke({operationId:'takeover-dialog',expectedIntentEpoch:1});
  release({response:0});await assert.rejects(pending,/已请求接管/);
  assert.equal(f.calls.length,1);assert.equal(f.activations.length,0);
});
