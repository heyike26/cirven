'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {validateScope}=require('../client.cjs');
const {PrivateState,CompanionController,draftKey,scopeOf}=require('../controller.cjs');
const native=require('../native-conversation.cjs');
const profile={id:'selected-model',revision:1,sha256:'a'.repeat(64)},ref={id:'b'.repeat(64),revision:1,sha256:'c'.repeat(64)};
const scope={project_id:'project',conversation_id:'desktop-discussion'};
function message(extra={}){return {kind:'NATIVE_MESSAGE',...scope,operation_id:'original-op',status:'SUCCEEDED',record_origin:'CURRENT_HOST',input:{raw_text:'Original request <script> remains inert',origin:'ORIGINAL_NATIVE_REQUEST'},text:'Original answer',provider_profile_ref:profile,native_message_ref:ref,control_capabilities:{can_guide:false,can_cancel:false},...extra};}
function conversation(messages=[message()],extra={}){return {kind:'NATIVE_CONVERSATION',...scope,messages,latest_provider_profile_ref:profile,coverage:{returned:messages.length,has_more:false},...extra};}
function controller(t){const dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-native-client-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return new CompanionController({state:new PrivateState(dir)});}
function selected(t,extra={}){const c=controller(t);c.projectId=scope.project_id;c.selected=native.present(conversation(undefined,extra),scope);return c;}
test('native scope is separate from legacy sessions and cannot share draft keys',()=>{
 assert.deepEqual(validateScope(scope),scope);assert.throws(()=>validateScope({...scope,generation:1}),{code:'INVALID_SCOPE'});assert.throws(()=>validateScope({...scope,session_ref:ref}),{code:'INVALID_SCOPE'});
 assert.notEqual(draftKey(scope),draftKey({...scope,conversation_id:'another'}));assert.notEqual(draftKey(scope),draftKey({project_id:scope.project_id,session_ref:ref}));
});
test('project directory includes the actual desktop discussion only with explicit approved methods',async t=>{
 const c=controller(t),calls=[];c.projects=[{id:scope.project_id}];c.client={methods:['native.conversations.list','native.conversation.get'],rpc:async(method,s)=>{calls.push([method,s]);return {status:'OK',result:method==='sessions.list'?{sessions:[]}:method==='native.conversations.list'?{kind:'NATIVE_CONVERSATION_DIRECTORY',project_id:scope.project_id,conversations:[{kind:'NATIVE_CONVERSATION',...scope}],coverage:{returned:1,has_more:false,complete:true}}:conversation()};}};
 await c.selectProject(scope.project_id);assert.equal(c.sessions[0].conversation_id,scope.conversation_id);await c.selectSession(0,c.snapshot().sessionTokens[0]);assert.deepEqual(scopeOf(c.selected),scope);assert.equal(c.selected.messages[1].parts[0].text,'Original answer');assert.equal(c.snapshot().diffs.scopeToken,null);assert.equal(calls.at(-1)[0],'native.conversation.get');
 c.client.methods=[];await c.selectProject(scope.project_id);assert.equal(c.sessions.length,0);assert.equal(calls.at(-1)[0],'sessions.list');
});
test('cross-discussion response and late reply cannot replace selected history or current draft',async t=>{
 const c=selected(t);c.setDraft(draftKey(c.selected),0,'Keep my unsent correction');c.client={rpc:async()=>({status:'OK',result:conversation(undefined,{conversation_id:'other'})})};await c.refreshSelected();assert.equal(c.error,'SCOPE_MISMATCH');assert.equal(c.selected.conversation_id,scope.conversation_id);assert.equal(c.snapshot().draft.text,'Keep my unsent correction');
 let done;c.client.rpc=async()=>await new Promise(r=>{done=r;});const read=c.refreshSelected();c.epoch++;c.selected=native.present(conversation([], {conversation_id:'other'}),{...scope,conversation_id:'other'});done({status:'OK',result:conversation()});await read;assert.equal(c.selected.conversation_id,'other');
});
test('continuing sends exact same conversation and explicit public model without inheriting file authority',async t=>{
 const c=selected(t),calls=[];c.client={rpc:async(method,s,p)=>{calls.push({method,s,p});return {status:'OK',result:method==='native.message'?message({operation_id:p.operation_id}):conversation()};}};const key=draftKey(c.selected);c.setDraft(key,0,'Please explain this');await c.send(key,1);
 assert.equal(calls[0].method,'native.message');assert.deepEqual(calls[0].s,scope);assert.deepEqual(calls[0].p.provider_profile_ref,profile);assert.equal(calls[0].p.raw_text,'Please explain this');assert.ok(!('file_source_ref'in calls[0].p));assert.ok(!('file_link_ref'in calls[0].p));assert.equal(c.state.value.drafts[key].text,'');
});
test('running task correction and cancel use exact original parent, not a new task',async t=>{
 const c=selected(t),parent=message({status:'PROCESSING',control_capabilities:{can_guide:true,can_cancel:true}}),calls=[];c.selected=native.present(conversation([parent]),scope);c.client={rpc:async(method,s,p)=>{calls.push({method,s,p});return {status:'OK',result:method==='native.conversation.get'?conversation([parent]):{status:'ACCEPTED'}};}};const key=draftKey(c.selected);c.setDraft(key,0,'Keep going after answering status');await c.send(key,1);assert.equal(calls[0].method,'native.guidance');assert.deepEqual(calls[0].p.expected_native_message_ref,ref);assert.equal(calls[0].p.parent_operation_id,'original-op');assert.ok(!('provider_profile_ref'in calls[0].p));await c.cancel();assert.equal(calls.at(-1).method,'native.cancel');assert.equal(calls.at(-1).p.operation_id,'original-op');
});
test('lost native POST is recoverable across restart without resending the draft',async t=>{
 const c=selected(t),key=draftKey(c.selected);let count=0;c.client={rpc:async(method)=>{if(method==='native.message'){count++;throw Error('lost reply');}return {status:'OK',result:conversation()};}};c.setDraft(key,0,'Must not repeat');const result=await c.send(key,1);assert.equal(result.status,'UNKNOWN');assert.equal(c.state.value.drafts[key].text,'Must not repeat');
 const recovered=new PrivateState(c.state.directory);recovered.recover(path.basename(c.state.filename));const next=new CompanionController({state:recovered});next.selected=c.selected;next.client=c.client;await assert.rejects(()=>next.send(key,1),{code:'ORIGINAL_REQUEST_UNRESOLVED'});assert.equal(count,1);assert.equal(recovered.value.requests[0].scope.conversation_id,scope.conversation_id);
});
test('old host controls, missing model and failed dispatch preserve drafts without new authority',async t=>{
 const c=selected(t,{latest_provider_profile_ref:null});let calls=0;c.client={rpc:async()=>{calls++;return {status:'OK',result:message({status:'FAILED'})};}};const key=draftKey(c.selected);c.setDraft(key,0,'Keep');await assert.rejects(()=>c.send(key,1),{code:'NATIVE_MODEL_SELECTION_REQUIRED'});assert.equal(calls,0);
 const old=native.present(conversation([message({record_origin:'HISTORICAL_READONLY',control_capabilities:{can_guide:true,can_cancel:true}})]),scope);assert.equal(old.active_native_message,null);
 c.selected=native.present(conversation(),scope);c.client.rpc=async method=>{calls++;return{status:'OK',result:method==='native.message'?message({status:'NOT_DISPATCHED'}):conversation()};};await c.send(key,1);assert.equal(c.state.value.drafts[key].text,'Keep');
});

for(const code of ['OPERATION_OUTCOME_UNKNOWN','OPERATION_INPUT_MISMATCH','DISPATCH_UNCONFIRMED','RESPONSE_UNAVAILABLE'])test('native uncertainty '+code+' stays pending across the original RPC read',async t=>{
 const c=selected(t),key=draftKey(c.selected);let writes=0;c.setDraft(key,0,'Do not repeat');c.client={rpc:async method=>{if(method==='native.message'){writes++;return {status:'ERROR',error:{code}};}return {status:'OK',result:conversation()};},inspect:async()=>({status:'ERROR',error:{code}})};
 await c.send(key,1);assert.equal(c.state.value.requests[0].status,'UNKNOWN');assert.equal(c.state.value.drafts[key].text,'Do not repeat');await c.inspect(c.state.value.requests[0].request_id);assert.equal(c.state.value.requests[0].status,'UNKNOWN');await assert.rejects(()=>c.send(key,1),{code:'ORIGINAL_REQUEST_UNRESOLVED'});assert.equal(writes,1);
});
test('old grants expose a deliberate update action without reading or auto-granting native history',t=>{
 const c=controller(t);c.client={supportedMethods:['native.conversation.get'],methods:['sessions.list']};assert.equal(c.snapshot().nativePairingRequired,true);assert.equal(c.state.value.requests.length,0);
});

test('status-only running owner remains the parent and never starts another model operation',async t=>{
 const c=selected(t),parent=message({status:'PROCESSING',control_capabilities:{can_guide:false,can_status:true,can_cancel:false}}),calls=[];c.selected=native.present(conversation([parent]),scope);assert.equal(c.selected.active_operation_id,'original-op');c.client={rpc:async(method,s,p)=>{calls.push({method,p});return {status:'OK',result:method==='native.conversation.get'?conversation([parent]):{status:'RETURNED'}};}};const key=draftKey(c.selected);c.setDraft(key,0,'What is the progress?');await c.send(key,1);assert.equal(calls[0].method,'native.guidance');assert.equal(calls[0].p.parent_operation_id,'original-op');assert.equal(calls[0].p.intent,'AUTO');assert.ok(!calls.some(c=>c.method==='native.message'));
});
