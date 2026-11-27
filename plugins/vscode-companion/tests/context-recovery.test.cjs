'use strict';
// AUTHORED_NOT_RUN: real private state/controller and metadata recovery manager.
// RPC, original process stop and creation are explicit UNIT boundaries.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {PrivateState,CompanionController,draftKey}=require('../controller.cjs');
const {METHODS,validEntry}=require('../context-recovery.cjs');
const {canonical,hash}=require('../client.cjs');
const copy=v=>JSON.parse(JSON.stringify(v)),sha=v=>hash(canonical(v));
function scene(t){
 const base=process.env.EEAS_TEST_TMPDIR||process.env.TMPDIR||os.tmpdir();assert.ok(!/^(?:\/mnt\/d(?:\/|$)|d:[\\/])/i.test(base));if(fs.existsSync(base))assert.ok(!/^(?:\/mnt\/d(?:\/|$)|d:[\\/])/i.test(fs.realpathSync(base)));
 fs.mkdirSync(base,{recursive:true});const folder=fs.mkdtempSync(path.join(base,'cirven-context-recovery-'));t.after(()=>fs.rmSync(folder,{recursive:true,force:true}));
 const state=new PrivateState(path.join(folder,'private'));let ids=0;const c=new CompanionController({state,newId:()=> 'unit_'+(++ids)});t.after(()=>c.dispose());
 const scope={project_id:'project',session_ref:{id:'original',revision:1,sha256:'a'.repeat(64)},generation:2},conversation_ref={id:'native:'+'b'.repeat(64),revision:1},issue={code:'SOURCE_CONTEXT_CHANGED',generation:2,operation_id:'original_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true};
 c.projectId='project';c.selected={...scope,context_authority_issue:issue,owner_status:'READY',connected_owner:true,messages:[],artifacts:[],pending_questions:[],pending_permissions:[],active_operation_id:'original_task'};
 const key=draftKey(c.selected);state.commit(v=>{v.selected=copy(scope);v.drafts[key]={scope:copy(scope),text:'KEEP_IDE_UNSENT_DRAFT',revision:1,agent:'build'};});
 const calls=[],records=new Map(),mode={phase:'COMPLETE',lose:false,existing:null,bad:false};
 function preparation(){const basis={kind:'CONTEXT_RECOVERY_PREPARATION',scope,conversation_ref,expected_issue:issue,target_project_id:'project',existing_recovery:mode.existing};return {...basis,preparation_sha256:sha(basis),read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_replay:false};}
 function result(body){const creation_request={operation_id:'context_chat_'+'c'.repeat(48),project_id:'project',title:null},creation_receipt={version:1,operation_id:creation_request.operation_id,request_sha256:sha(creation_request),project_id:'project',conversation_ref:{id:'native:'+'d'.repeat(64),revision:1},next_message:{project_id:'project',conversation_id:'chat_fresh'},created_at:'2026-11-03T06:30:00Z',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};return {kind:'CONTEXT_RECOVERY',version:1,operation_id:body.operation_id,request_sha256:sha(body),...scope,conversation_ref:body.conversation_ref,phase:mode.phase,stop_operation_id:'context_stop_'+'c'.repeat(48),stop_status:mode.phase==='STOP_PENDING'?'UNKNOWN_OUTCOME':'RETURNED',creation_request,creation_receipt:mode.phase==='COMPLETE'?creation_receipt:null,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,automatic_post_replay:false,model_calls_by_recovery:0};}
 c.client={methods:[...METHODS,'lifecycle.reveal'],close(){},rpc:async(method,s,payload,request_id)=>{
  calls.push({method,scope:copy(s),payload:copy(payload),request_id});
  if(method===METHODS[0])return {status:'OK',result:preparation()};
  if(method===METHODS[2]){const value=records.get(payload.operation_id);return value?{status:'OK',result:copy(value)}:{status:'ERROR',error:{code:'SOURCE_CONTEXT_RECOVERY_NOT_FOUND'}};}
  if(method==='lifecycle.reveal')return {status:'OK',result:{status:'REVEAL_REQUESTED'}};
  assert.equal(method,METHODS[1]);const p=payload.preparation,body=p.existing_recovery?.request??{operation_id:payload.operation_id,session_ref:s.session_ref,generation:s.generation,conversation_ref:p.conversation_ref,expected_issue:p.expected_issue};
  const committed=JSON.parse(fs.readFileSync(state.filename)).requests.find(r=>r.request_id===request_id);assert.deepEqual(committed.recovery_request,body);assert.equal(committed.recovery_request_sha256,sha(body));assert.equal(committed.status,'PENDING');
  const value=result(body);if(mode.bad)value.generation++;
  records.set(body.operation_id,value);if(mode.lose){mode.lose=false;throw Object.assign(Error('UNIT lost transport acknowledgement'),{code:'TRANSPORT_TIMEOUT'});}return {status:'OK',result:value};
 }};
 return {c,state,scope,key,calls,mode,records,preparation,result,folder,body:operation_id=>({operation_id,session_ref:scope.session_ref,generation:2,conversation_ref,expected_issue:issue})};
}

test('start journals the exact original request before command and preserves the old input',async t=>{
 const x=scene(t),before=copy(x.state.value.drafts[x.key]);await x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token);
 assert.deepEqual(x.calls.map(c=>c.method),[METHODS[0],METHODS[1]]);assert.equal(x.c.sourceRecovery.snapshot().phase,'COMPLETE');assert.deepEqual(x.state.value.drafts[x.key],before);
 const entry=x.state.value.requests[0];assert.equal(validEntry(entry),true);assert.equal(entry.status,'OK');assert.doesNotMatch(JSON.stringify(x.calls),/KEEP_IDE_UNSENT_DRAFT|allowed_effects|provider_profile_ref/);
});

test('lost reply is resolved through original API receipt, with no second command',async t=>{
 const x=scene(t);x.mode.lose=true;await x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token);
 assert.equal(x.c.sourceRecovery.snapshot().phase,'PENDING');await x.c.checkResult(x.state.value.requests[0].request_id);
 assert.equal(x.c.sourceRecovery.snapshot().phase,'COMPLETE');assert.equal(x.calls.filter(c=>c.method===METHODS[1]).length,1);assert.equal(x.calls.at(-1).method,METHODS[2]);assert.equal(x.state.value.drafts[x.key].text,'KEEP_IDE_UNSENT_DRAFT');
});

test('metadata creation continuation keeps original body/key and distinct transport receipts',async t=>{
 const x=scene(t);x.mode.phase='STOP_CONFIRMED';await x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token);const first=copy(x.state.value.requests[0]);
 x.mode.phase='COMPLETE';await x.c.sourceRecovery.continueOriginal(x.c.sourceRecovery.snapshot().token);
 const commands=x.calls.filter(c=>c.method===METHODS[1]);assert.equal(commands.length,2);assert.deepEqual(commands[0].payload,commands[1].payload);assert.notEqual(commands[0].request_id,commands[1].request_id);
 assert.equal(x.state.value.requests[0].recovery_request_sha256,first.recovery_request_sha256);assert.equal(x.state.value.requests.every(r=>r.status==='OK'),true);assert.equal(x.c.sourceRecovery.snapshot().phase,'COMPLETE');
});

test('unknown original stop only offers inspection and refuses a fresh key or continuation',async t=>{
 const x=scene(t);x.mode.phase='STOP_PENDING';await x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token);
 assert.equal(x.c.sourceRecovery.snapshot().can_continue,false);await assert.rejects(()=>x.c.sourceRecovery.continueOriginal(x.c.sourceRecovery.snapshot().token));await assert.rejects(()=>x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token));
 await x.c.sourceRecovery.inspect(x.c.sourceRecovery.snapshot().token);assert.equal(x.calls.filter(c=>c.method===METHODS[1]).length,1);
});

test('a recovery already created on desktop is discovered and read without a command',async t=>{
 const x=scene(t),body=x.body('desktop_original');x.mode.existing={request:body,request_sha256:sha(body),target_project_id:'project'};x.records.set(body.operation_id,x.result(body));
 await x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token);assert.deepEqual(x.calls.map(c=>c.method),[METHODS[0],METHODS[2]]);assert.equal(x.state.value.requests[0].operation_id,'desktop_original');assert.equal(x.state.value.requests[0].recovery_discovered,true);
});

test('a late receipt from another generation remains unknown and cannot navigate or clear input',async t=>{
 const x=scene(t);x.mode.bad=true;await x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token);
 assert.equal(x.c.sourceRecovery.snapshot().phase,'PENDING');assert.equal(x.state.value.requests[0].status,'UNKNOWN');assert.equal(x.state.value.drafts[x.key].text,'KEEP_IDE_UNSENT_DRAFT');assert.equal(x.calls.some(c=>c.method==='lifecycle.reveal'),false);
});

test('private recovery survives extension reactivation without replaying commands',async t=>{
 const x=scene(t);x.mode.lose=true;await x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token);const old=path.basename(x.state.filename),next=new PrivateState(path.join(x.folder,'private'));
 next.recover(old);const recovered=next.value.requests.find(r=>r.recovery_request);assert.equal(validEntry(recovered),true);assert.equal(recovered.status,'UNKNOWN');assert.equal(next.value.drafts[x.key].text,'KEEP_IDE_UNSENT_DRAFT');assert.equal(x.calls.filter(c=>c.method===METHODS[1]).length,1);
});

test('stale action token after generation change stops nothing',async t=>{
 const x=scene(t),token=x.c.sourceRecovery.snapshot().token;x.c.selected={...x.c.selected,generation:3};await assert.rejects(()=>x.c.sourceRecovery.start(token));assert.equal(x.calls.length,0);
});

test('a cross-surface winning claim is discovered after a lost uncommitted local attempt',async t=>{
 const x=scene(t);x.mode.lose=true;await x.c.sourceRecovery.start(x.c.sourceRecovery.snapshot().token);const first=copy(x.state.value.requests[0]);
 // UNIT API boundary: the first request never committed; the other surface did.
 x.records.delete(first.operation_id);const other=x.body('desktop_winner');x.mode.existing={request:other,request_sha256:sha(other),target_project_id:'project'};x.records.set(other.operation_id,x.result(other));
 await x.c.sourceRecovery.inspect(x.c.sourceRecovery.snapshot().token);
 const old=x.state.value.requests.find(r=>r.request_id===first.request_id),latest=x.state.value.requests.at(-1);
 assert.equal(old.recovery_request_sha256,first.recovery_request_sha256);assert.equal(old.recovery_original_transport_status,'UNKNOWN');assert.equal(old.recovery_superseded_by.operation_id,'desktop_winner');assert.equal(latest.operation_id,'desktop_winner');assert.equal(x.c.sourceRecovery.snapshot().phase,'COMPLETE');assert.equal(x.calls.filter(c=>c.method===METHODS[1]).length,1);assert.equal(x.state.value.drafts[x.key].text,'KEEP_IDE_UNSENT_DRAFT');
});
