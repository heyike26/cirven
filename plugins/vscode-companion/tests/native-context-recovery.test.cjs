'use strict';
// AUTHORED_NOT_RUN: real private state/controller; RPC/stop/create are UNIT boundaries.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {PrivateState,CompanionController,draftKey}=require('../controller.cjs');
const {METHODS,validEntry}=require('../native-context-recovery.cjs');
const {canonical,hash}=require('../client.cjs');
const copy=v=>JSON.parse(JSON.stringify(v)),sha=v=>hash(canonical(v));
function scene(t){
 const base=process.env.EEAS_TEST_TMPDIR||process.env.TMPDIR||os.tmpdir();assert.ok(!/^(?:\/mnt\/d(?:\/|$)|d:[\\/])/i.test(base));if(fs.existsSync(base))assert.ok(!/^(?:\/mnt\/d(?:\/|$)|d:[\\/])/i.test(fs.realpathSync(base)));
 fs.mkdirSync(base,{recursive:true});const folder=fs.mkdtempSync(path.join(base,'cirven-native-recovery-'));t.after(()=>fs.rmSync(folder,{recursive:true,force:true}));
 const state=new PrivateState(path.join(folder,'private'));let ids=0;const c=new CompanionController({state,newId:()=> 'unit_'+(++ids)});t.after(()=>c.dispose());
 const scope={project_id:'project',conversation_id:'native_chat'},ref={id:'native_parent',revision:1,sha256:'a'.repeat(64)},source={parent_operation_id:'native_parent',expected_native_ref:ref},key='native_recovery_'+'b'.repeat(48);
 const issue={code:'SOURCE_CONTEXT_CHANGED',generation:2,operation_id:'old_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true};
 c.projectId='project';c.selected={kind:'NATIVE_CONVERSATION',...scope,messages:[],artifacts:[],native_messages:[{operation_id:'native_parent',native_context_issue:issue,native_context_recovery:{...source,record_current:true,read_only:true,model_calls:0}}]};
 const draft=draftKey(c.selected);state.commit(v=>{v.selected=copy(scope);v.drafts[draft]={scope:copy(scope),text:'KEEP_UNSENT_IDE_INPUT',revision:1,agent:'build'};});
 const mode={phase:'COMPLETE',lose:false,bad:false,existing:null},records=new Map(),calls=[];
 function preparation(){const basis={parent_operation_id:'native_parent',expected_native_ref:ref,subject_sha256:'c'.repeat(64),operation_id:key,origin:scope,target:{project_id:'project',conversation_ref:null},issue,existing_recovery:mode.existing};return {kind:'NATIVE_CONTEXT_RECOVERY_PREPARATION',...basis,preparation_sha256:sha(basis),target_label:'Project',read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_post_replay:false,effective_target:{project_id:'project',label:'Project'}};}
 function result(parent,r){const creation_request={operation_id:'native_context_chat_'+'b'.repeat(48),project_id:'project',title:null},creation_receipt={version:1,operation_id:creation_request.operation_id,request_sha256:sha(creation_request),project_id:'project',conversation_ref:{id:'native:'+'d'.repeat(64),revision:1},next_message:{project_id:'project',conversation_id:'chat_new'},created_at:'2026-11-03T08:00:00Z',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};return {kind:'NATIVE_CONTEXT_RECOVERY',parent_operation_id:parent,operation_id:r.operation_id,request_sha256:sha(r),expected_native_ref:r.expected_native_ref,origin:scope,owner_binding_sha256:'e'.repeat(64),phase:mode.phase,stop_operation_id:'native_context_stop_'+'b'.repeat(48),stop_status:mode.phase==='STOP_PENDING'?'UNKNOWN_OUTCOME':'RETURNED',creation_request,creation_receipt:mode.phase==='COMPLETE'?creation_receipt:null,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,external_actions_rolled_back:false,automatic_post_replay:false,model_calls_by_recovery:0};}
 const client={methods:[...METHODS],projectIds:['project'],close(){},rpc:async(method,s,payload,request_id)=>{
  calls.push({method,scope:copy(s),payload:copy(payload),request_id});
  if(method===METHODS[0])return {status:'OK',result:preparation()};
  if(method===METHODS[2]){const r=records.get(payload.operation_id);return r?{status:'OK',result:copy(r)}:{status:'ERROR',error:{code:'NATIVE_CONTEXT_RECOVERY_NOT_FOUND'}};}
  assert.equal(method,METHODS[1]);const persisted=JSON.parse(fs.readFileSync(state.filename)).requests.find(r=>r.request_id===request_id);
  assert.deepEqual(persisted.native_recovery_request,payload.request);assert.equal(persisted.native_recovery_request_sha256,sha(payload.request));assert.equal(persisted.status,'PENDING');
  const r=result(payload.parent_operation_id,payload.request);if(mode.bad)r.source_data_copied=true;records.set(payload.request.operation_id,r);
  if(mode.lose){mode.lose=false;throw Object.assign(Error('UNIT lost transport'),{code:'TRANSPORT_TIMEOUT'});}return {status:'OK',result:r};
 }};c.client=client;
 const view=()=>c.nativeSourceRecovery.snapshot().items[0];
 async function start(){const token=view().token;await c.nativeSourceRecovery.prepare(token);return c.nativeSourceRecovery.start(view().token);}
 return {c,state,scope,source,ref,key,draft,folder,mode,records,calls,client,view,start,preparation,result};
}

test('scope review is read-only, exact confirmation is durable before POST and old input stays put',async t=>{
 const x=scene(t),before=copy(x.state.value.drafts[x.draft]),token=x.view().token;
 await x.c.nativeSourceRecovery.prepare(token);assert.equal(x.calls.filter(c=>c.method===METHODS[1]).length,0);assert.equal(x.view().prepared,true);
 await x.c.nativeSourceRecovery.start(x.view().token);assert.equal(x.view().phase,'COMPLETE');assert.deepEqual(x.state.value.drafts[x.draft],before);
 const entry=x.state.value.requests.at(-1);assert.equal(validEntry(entry),true);assert.equal(entry.native_recovery_request.operation_id,x.key);
 assert.equal(entry.native_recovery_request.raw_text,undefined);assert.equal(entry.draft_key,undefined);
});

test('lost transport reply is recovered by exact original operation without another command',async t=>{
 const x=scene(t);x.mode.lose=true;await x.start();assert.equal(x.view().phase,'PENDING');
 await x.c.nativeSourceRecovery.inspect(x.view().token);assert.equal(x.view().phase,'COMPLETE');
 assert.deepEqual(x.calls.map(c=>c.method),[METHODS[0],METHODS[1],METHODS[2]]);
});

test('unknown stop stays pending and explicit continuation only reads the original key',async t=>{
 const x=scene(t);x.mode.phase='STOP_PENDING';await x.start();await x.c.nativeSourceRecovery.continueOriginal(x.view().token);
 assert.equal(x.view().phase,'STOP_PENDING');assert.equal(x.calls.filter(c=>c.method===METHODS[1]).length,1);
});

test('metadata-only continuation uses the same confirmed request and never clears original draft',async t=>{
 const x=scene(t);x.mode.phase='STOP_CONFIRMED';await x.start();assert.equal(x.view().can_continue,true);
 x.mode.phase='COMPLETE';await x.c.nativeSourceRecovery.continueOriginal(x.view().token);
 const commands=x.calls.filter(c=>c.method===METHODS[1]);assert.equal(commands.length,2);assert.deepEqual(commands[0].payload.request,commands[1].payload.request);
 assert.equal(x.state.value.drafts[x.draft].text,'KEEP_UNSENT_IDE_INPUT');
});

test('existing desktop recovery is discovered with its original parent and read without POST',async t=>{
 const x=scene(t),request={operation_id:x.key,expected_native_ref:{...x.ref,id:'desktop_parent'},preparation_sha256:'f'.repeat(64)};
 x.mode.existing={parent_operation_id:'desktop_parent',request,request_sha256:sha(request)};x.records.set(x.key,x.result('desktop_parent',request));
 await x.start();assert.deepEqual(x.calls.map(c=>c.method),[METHODS[0],METHODS[2]]);assert.equal(x.view().phase,'COMPLETE');
 assert.equal(x.state.value.requests.at(-1).native_recovery_parent,'desktop_parent');
});

test('a broadened result retains the original unknown request instead of acknowledging completion',async t=>{
 const x=scene(t);x.mode.bad=true;await x.start();assert.equal(x.view().phase,'PENDING');assert.equal(x.state.value.requests.at(-1).status,'UNKNOWN');
 assert.equal(x.state.value.requests.at(-1).native_recovery_receipt,undefined);assert.equal(x.state.value.drafts[x.draft].text,'KEEP_UNSENT_IDE_INPUT');
});

test('restored private journal validates original packet and queries it without recreating a stop',async t=>{
 const x=scene(t);x.mode.lose=true;await x.start();const saved=x.state.filename;
 const restored=new PrivateState(x.state.directory);restored.recover(path.basename(saved));
 const c=new CompanionController({state:restored,newId:()=> 'reload_query'});t.after(()=>c.dispose());c.client=x.client;c.selected=copy(x.c.selected);c.projectId='project';
 await c.nativeSourceRecovery.inspect(c.nativeSourceRecovery.snapshot().items[0].token);
 assert.equal(c.nativeSourceRecovery.snapshot().items[0].phase,'COMPLETE');assert.equal(x.calls.filter(c=>c.method===METHODS[1]).length,1);
});

test('missing current grant disables opening even when an old receipt was complete',async t=>{
 const x=scene(t);await x.start();x.client.projectIds=[];assert.equal(x.view().can_open,false);
 const old=x.c.selected;await assert.rejects(()=>x.c.nativeSourceRecovery.reveal(x.view().token));assert.equal(x.c.selected,old);
});

test('navigation during original lookup cannot dispatch a late continuation',async t=>{
 const x=scene(t);x.mode.phase='STOP_CONFIRMED';await x.start();const rpc=x.client.rpc;
 x.client.rpc=async(...args)=>{const v=await rpc(...args);if(args[0]===METHODS[2])x.c.epoch++;return v;};
 await assert.rejects(()=>x.c.nativeSourceRecovery.continueOriginal(x.view().token));assert.equal(x.calls.filter(c=>c.method===METHODS[1]).length,1);
});

test('old pairing exposes recovery need without silently acquiring newly supported methods',async t=>{
 const x=scene(t);x.client.methods=['native.operation'];assert.equal(x.view().supported,false);
 await assert.rejects(()=>x.c.nativeSourceRecovery.prepare(x.view().token));assert.equal(x.calls.length,0);
});
