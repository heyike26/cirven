'use strict';
// Production controller/private journal with a controlled transport; no listener/model/IDE.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),test=require('node:test');
const {PrivateState,CompanionController,draftKey}=require('../controller.cjs');
const native=require('../native-conversation.cjs');
const scope={project_id:'project',conversation_id:'discussion'},ref={id:'parent',revision:1,sha256:'a'.repeat(64)};
function message(controls={}){return{kind:'NATIVE_MESSAGE',...scope,operation_id:'parent',status:'PROCESSING',record_origin:'CURRENT_HOST',
  input:{origin:'ORIGINAL_NATIVE_REQUEST',raw_text:'Original task'},native_message_ref:ref,control_capabilities:controls};}
function conversation(controls={}){return{kind:'NATIVE_CONVERSATION',...scope,messages:[message(controls)],
  latest_provider_profile_ref:{id:'profile',revision:1,sha256:'b'.repeat(64)},coverage:{returned:1,has_more:false}};}
function fixture(t,controls={}){const d=fs.mkdtempSync(path.join(os.tmpdir(),'native-outcome80-'));t.after(()=>fs.rmSync(d,{recursive:true,force:true}));
  let sequence=0;const c=new CompanionController({state:new PrivateState(d),newId:()=>`explicit-${++sequence}`});
  c.projectId=scope.project_id;c.selected=native.present(conversation(controls),scope);const key=draftKey(c.selected);c.setDraft(key,0,'Keep this exact draft');return{c,key};}

for(const method of ['native.message','native.guidance'])test('OK transport with '+method+' UNKNOWN preserves original intent and inspects without resending',async t=>{
  const {c,key}=fixture(t,method==='native.guidance'?{can_status:true}:{});let writes=0,inspections=0;const original=[];
  const outcome={status:'OK',result:{kind:'NATIVE_MESSAGE',...scope,status:'UNKNOWN_OUTCOME',operation_id:'explicit-1',parent_operation_id:'parent'}};
  c.client={rpc:async(m,s,p)=>{assert.deepEqual(s,scope);if(m==='native.conversation.get')return{status:'OK',result:conversation()};
    if(m==='native.control'||m==='native.operation'){original.push({m,p});return outcome;}
    assert.equal(m,method);writes++;return outcome;},inspect:async()=>{inspections++;return outcome;}};
  await c.send(key,1);const entry=c.state.value.requests[0];outcome.result.request_sha256=entry.native_body_sha256;
  assert.equal(entry.status,'UNKNOWN');assert.equal(entry.operation_id,'explicit-1');assert.equal(c.known.has(entry.request_id),false);
  assert.equal(c.state.value.drafts[key].text,'Keep this exact draft');
  await c.inspect(entry.request_id);assert.equal(inspections,1);assert.equal(entry.request_id,c.state.value.requests[0].request_id);
  assert.equal(c.state.value.requests[0].status,'UNKNOWN');assert.equal(c.known.has(entry.request_id),false);
  await c.inspectOperation(entry.request_id);assert.equal(original.length,1);assert.equal(original[0].m,method==='native.guidance'?'native.control':'native.operation');
  assert.equal(original[0].p.operation_id,'explicit-1');if(method==='native.guidance')assert.equal(original[0].p.parent_operation_id,'parent');
  await assert.rejects(()=>c.send(key,1),{code:'ORIGINAL_REQUEST_UNRESOLVED'});assert.equal(writes,1);
});

test('cancel transport return cannot relabel an unknown parent completed or clear its draft',async t=>{
  const {c,key}=fixture(t,{can_cancel:true});let writes=0;
  c.client={rpc:async(m,s,p)=>{assert.equal(m,'native.cancel');assert.deepEqual(s,scope);assert.equal(p.operation_id,'parent');assert.deepEqual(p.expected_native_message_ref,ref);writes++;return{status:'OK',result:{status:'UNKNOWN_OUTCOME',operation_id:'parent'}};}};
  await c.cancel();const entry=c.state.value.requests[0];assert.equal(entry.method,'native.cancel');assert.equal(entry.operation_id,'parent');assert.equal(entry.status,'UNKNOWN');
  assert.equal(c.known.has(entry.request_id),false);assert.equal(c.state.value.drafts[key].text,'Keep this exact draft');assert.equal(writes,1);
});

test('native RETURNED retains accepted-ack draft clearing while ordinary transport semantics stay unchanged',async t=>{
  const {c,key}=fixture(t);let writes=0;c.client={rpc:async m=>m==='native.conversation.get'?{status:'OK',result:conversation()}:(writes++,{status:'OK',result:{status:'RETURNED'}})};
  await c.send(key,1);assert.equal(c.state.value.requests[0].status,'OK');assert.equal(c.state.value.drafts[key].text,'');assert.equal(writes,1);
  const legacy={project_id:'project',session_ref:{id:'legacy',revision:1,sha256:'c'.repeat(64)},generation:1};
  await c.mutation('session.message',legacy,{operation_id:'legacy-original',raw_text:'Legacy semantics'});
  const entry=c.state.value.requests.at(-1);c.applyEnvelope(entry.request_id,{status:'OK',result:{status:'UNKNOWN_OUTCOME'}});
  assert.equal(c.state.value.requests.at(-1).status,'OK');assert.equal(c.known.get(entry.request_id).status,'OK');
});
