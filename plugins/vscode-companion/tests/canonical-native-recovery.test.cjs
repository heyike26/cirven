'use strict';
// AUTHORED_NOT_RUN. Real private state/controller, UNIT RPC/metadata/stop only.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {PrivateState,CompanionController,draftKey}=require('../controller.cjs');
const {CANONICAL_METHODS,validEntry}=require('../native-context-recovery.cjs');
const canonicalConversations=require('../canonical-conversations.cjs');
const {canonical,hash}=require('../client.cjs');
const copy=v=>JSON.parse(JSON.stringify(v)),sha=v=>hash(canonical(v));
function scene(t){
 const base=process.env.EEAS_TEST_TMPDIR||process.env.TMPDIR||os.tmpdir();assert.ok(!/^(?:\/mnt\/d(?:\/|$)|d:[\\/])/i.test(base));if(fs.existsSync(base))assert.ok(!/^(?:\/mnt\/d(?:\/|$)|d:[\\/])/i.test(fs.realpathSync(base)));
 fs.mkdirSync(base,{recursive:true});const folder=fs.mkdtempSync(path.join(base,'cirven-canonical-native-'));t.after(()=>fs.rmSync(folder,{recursive:true,force:true}));
 const state=new PrivateState(path.join(folder,'private'));let ids=0;const c=new CompanionController({state,newId:()=> 'unit_'+(++ids)});t.after(()=>c.dispose());
 const scope={project_id:null,conversation_ref:{id:'native:'+'d'.repeat(64),revision:1}},origin={project_id:null,conversation_id:'unbound_source'},ref={id:'native_parent',revision:1,sha256:'a'.repeat(64)},key='native_recovery_'+'b'.repeat(48);
 const source_ref={id:'native:'+sha([null,origin.conversation_id]),sha256:sha({kind:'native',...origin})},source={parent_operation_id:'native_parent',expected_native_ref:ref,source_ref};
 const issue={code:'SOURCE_CONTEXT_CHANGED',generation:1,operation_id:'old_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true};
 const row={id:'c'.repeat(64),role:'user',text:'KEEP_ORIGINAL_QUESTION',source_kind:'native',operation_id:'native_parent',native_context_source:{origin,source_ref,native_context_issue:issue,native_context_recovery:{parent_operation_id:'native_parent',expected_native_ref:ref,record_current:true,read_only:true,model_calls:0}}};
 const value={kind:'CANONICAL_CONVERSATION',organization_project_id:null,conversation_ref:scope.conversation_ref,title:null,owner:null,canonical_history:[row],connection_state:'EMPTY',execution_access:true,next_history_cursor:null,history_partial:false,model_calls:0,owner_started_by_query:false};
 c.projectId=canonicalConversations.CHATS;c.selected=canonicalConversations.present(value,scope);
 const draft=draftKey(c.selected);state.commit(v=>{v.selected=copy(scope);v.drafts[draft]={scope:copy(scope),text:'KEEP_CANONICAL_UNSENT',revision:1,agent:'build'};});
 const mode={phase:'COMPLETE',lose:false,existing:null,current:copy(scope)},records=new Map(),calls=[];
 const binding=requested=>({requested_scope:copy(requested),current_scope:copy(mode.current),source_ref:copy(source_ref),origin:copy(origin)});
 function preparation(){const basis={parent_operation_id:'native_parent',expected_native_ref:ref,subject_sha256:'e'.repeat(64),operation_id:key,origin,target:{project_id:null,conversation_ref:scope.conversation_ref},issue,existing_recovery:mode.existing};return {kind:'NATIVE_CONTEXT_RECOVERY_PREPARATION',...basis,preparation_sha256:sha(basis),target_label:null,read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_post_replay:false,effective_target:{project_id:null,label:null},canonical_binding:binding(scope)};}
 function result(parent,request,requested=scope){const creation_request={operation_id:'native_context_chat_'+'b'.repeat(48),project_id:null,title:null},creation_receipt={version:1,operation_id:creation_request.operation_id,request_sha256:sha(creation_request),project_id:null,conversation_ref:{id:'native:'+'f'.repeat(64),revision:1},next_message:{project_id:null,conversation_id:'chat_new'},created_at:'2026-11-03T10:00:00Z',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};return {kind:'NATIVE_CONTEXT_RECOVERY',parent_operation_id:parent,operation_id:request.operation_id,request_sha256:sha(request),expected_native_ref:request.expected_native_ref,origin,owner_binding_sha256:'0'.repeat(64),phase:mode.phase,stop_operation_id:'native_context_stop_'+'b'.repeat(48),stop_status:mode.phase==='STOP_PENDING'?'UNKNOWN_OUTCOME':'RETURNED',creation_request,creation_receipt:mode.phase==='COMPLETE'?creation_receipt:null,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,external_actions_rolled_back:false,automatic_post_replay:false,model_calls_by_recovery:0,canonical_binding:binding(requested)};}
 const client={methods:[...CANONICAL_METHODS,...canonicalConversations.METHODS],projectIds:null,close(){},rpc:async(method,s,payload,request_id)=>{
  calls.push({method,scope:copy(s),payload:copy(payload),request_id});
  if(method===CANONICAL_METHODS[0])return {status:'OK',result:preparation()};
  if(method===CANONICAL_METHODS[2]){const record=records.get(payload.operation_id);return record?{status:'OK',result:{...copy(record),canonical_binding:binding(s)}}:{status:'ERROR',error:{code:'NATIVE_CONTEXT_RECOVERY_NOT_FOUND'}};}
  assert.equal(method,CANONICAL_METHODS[1]);const saved=JSON.parse(fs.readFileSync(state.filename)).requests.find(e=>e.request_id===request_id);assert.equal(validEntry(saved),true);assert.deepEqual(saved.native_recovery_request,payload.request);assert.deepEqual(saved.native_recovery_origin,origin);assert.deepEqual(payload.source_ref,source_ref);
  const r=result(payload.parent_operation_id,payload.request,s);records.set(payload.request.operation_id,r);if(mode.lose){mode.lose=false;throw Object.assign(Error('UNIT lost reply'),{code:'TRANSPORT_TIMEOUT'});}return {status:'OK',result:r};
 }};c.client=client;
 const view=()=>c.nativeSourceRecovery.snapshot().items[0];
 const start=async()=>{await c.nativeSourceRecovery.prepare(view().token);return c.nativeSourceRecovery.start(view().token);};
 return {c,state,scope,origin,source,source_ref,ref,key,draft,mode,records,calls,client,view,start,result};
}

test('unbound canonical history recovery uses a confirmed native alias and keeps stable CID draft',async t=>{
 const x=scene(t),before=copy(x.state.value.drafts[x.draft]);await x.start();assert.equal(x.view().phase,'COMPLETE');assert.equal(x.view().can_open,true);
 const entry=x.state.value.requests.at(-1);assert.deepEqual(entry.scope,x.scope);assert.deepEqual(entry.native_recovery_origin,x.origin);assert.equal(entry.native_recovery_request.raw_text,undefined);assert.deepEqual(x.state.value.drafts[x.draft],before);
});

test('desktop winner for the same private owner is queried under its original parent without POST',async t=>{
 const x=scene(t),request={operation_id:x.key,expected_native_ref:{...x.ref,id:'desktop_parent'},preparation_sha256:'1'.repeat(64)};
 x.mode.existing={parent_operation_id:'desktop_parent',request,request_sha256:sha(request)};x.records.set(x.key,x.result('desktop_parent',request));await x.start();
 assert.deepEqual(x.calls.map(c=>c.method),[CANONICAL_METHODS[0],CANONICAL_METHODS[2]]);assert.equal(x.view().phase,'COMPLETE');
});

test('lost RPC is later resolved by original query while initial unknown transport remains recorded',async t=>{
 const x=scene(t);x.mode.lose=true;await x.start();const entry=x.state.value.requests.at(-1);assert.equal(entry.native_recovery_initial_transport_status,'UNKNOWN');
 await x.c.nativeSourceRecovery.inspectEntry(entry.request_id);const observed=x.state.value.requests.at(-1);assert.equal(observed.native_recovery_initial_transport_status,'UNKNOWN');assert.equal(observed.status,'OK');assert.equal(x.calls.filter(c=>c.method===CANONICAL_METHODS[1]).length,1);
});

test('restored canonical recovery validates its original navigation and source scopes',async t=>{
 const x=scene(t);x.mode.lose=true;await x.start();const saved=x.state.filename;
 const restored=new PrivateState(x.state.directory);restored.recover(path.basename(saved));const c=new CompanionController({state:restored});t.after(()=>c.dispose());c.client=x.client;c.selected=copy(x.c.selected);c.projectId=canonicalConversations.CHATS;
 const entry=restored.value.requests.at(-1);assert.equal(validEntry(entry),true);await c.nativeSourceRecovery.inspectEntry(entry.request_id);assert.equal(c.nativeSourceRecovery.snapshot().items[0].phase,'COMPLETE');assert.equal(restored.value.drafts[x.draft].text,'KEEP_CANONICAL_UNSENT');
});

test('historical recovery query preserves old request scope while reporting newer membership',async t=>{
 const x=scene(t);x.mode.lose=true;await x.start();const entry=x.state.value.requests.at(-1);x.mode.current={project_id:null,conversation_ref:{...x.scope.conversation_ref,revision:2}};
 const r=await x.c.nativeSourceRecovery.inspectEntry(entry.request_id);assert.equal(r.canonical_binding.requested_scope.conversation_ref.revision,1);assert.equal(r.canonical_binding.current_scope.conversation_ref.revision,2);assert.equal(x.state.value.requests.at(-1).scope.conversation_ref.revision,1);
});

test('old pairing cannot substitute native-only grants for canonical recovery capability',async t=>{
 const x=scene(t);x.client.methods=require('../native-context-recovery.cjs').METHODS;assert.equal(x.view().supported,false);await assert.rejects(()=>x.c.nativeSourceRecovery.prepare(x.view().token));assert.equal(x.calls.length,0);
});
