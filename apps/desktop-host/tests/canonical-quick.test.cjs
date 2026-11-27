'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {QuickJournal,quickScope}=require('../quick-journal.cjs');
const {QuickHost}=require('../quick-host.cjs');
const {canonicalQuickRoot}=require('../canonical-quick.cjs');
const ref=(id)=>({id,revision:1,sha256:'b'.repeat(64)});
const scope={conversationId:'chat-local',conversationRef:{id:'native:'+'a'.repeat(64),revision:3},
 originalSource:{project_id:'private_execution',session_ref:ref('original_owner'),generation:2},providerProfileRef:ref('profile'),agent:'plan'};
function fixture(t,request,initialScope=scope){
 const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'canonical-quick-')),calls=[];
 const journal=new QuickJournal({dataDir,hostGeneration:'same_host'});
 const host=new QuickHost({journal,getCompanion:()=>({capture:async()=>({operation_id:'page',current:true,
  observation:{status:'READY_OBSERVATION',context_ref:ref('frozen'),window:{title:'真实页面'},coverage:{visual:true,pixel_source:'VERIFIED_TARGET_PIXELS'},
    observations:{visual:{image_ref:{sha256:'c'.repeat(64)},width:100,height:100,pixel_source:'VERIFIED_TARGET_PIXELS',
      pixel_source_proof:{status:'VERIFIED_TARGET_PIXELS',self_exclusion_verified:true,image_sha256:'c'.repeat(64),target_bounds:{x:0,y:0,width:100,height:100}}}}},
  preview:{status:'AVAILABLE',image_ref:{sha256:'c'.repeat(64),width:100,height:100}}})}),request:async(url,body)=>{calls.push({url,body});return request(url,body,journal);}});
 host.setScope(initialScope);t.after(()=>{host.close();fs.rmSync(dataDir,{recursive:true,force:true});});return{dataDir,host,journal,calls};
}
function response(key,patch={}){return{operation_id:key,conversation_ref:scope.conversationRef,original_source:scope.originalSource,status:'PROCESSING',
 canonical_message_ref:{id:key,revision:1,sha256:'e'.repeat(64)},
 automatic_post_replay:false,owner_started_by_query:false,model_calls_by_query:0,can_cancel:true,can_continue:true,...patch};}
test('projectless canonical scope keeps organization and original execution separate; legacy session guard stays',()=>{
 const checked=quickScope(scope);assert.equal(checked.projectId,undefined);assert.equal(checked.sessionId,undefined);assert.deepEqual(checked.originalSource,scope.originalSource);
 assert.throws(()=>quickScope({sessionId:'old_legacy_session'}),/needs its project/);
 assert.throws(()=>quickScope({originalSource:scope.originalSource}),/Invalid original/);
 assert.throws(()=>quickScope({...scope,originalSource:{...scope.originalSource,generation:0}}),/Invalid original/);
});
test('reviewed context and choice lease stay with the exact Quick draft and original canonical input',async t=>{
 const selected={...scope,contextSelectionId:'d'.repeat(64),contextSelectionOperationId:'select_original'};
 const f=fixture(t,async(_url,body)=>{
  if(body){assert.equal(body.context_selection_id,selected.contextSelectionId);throw Error('Lost original acknowledgement');}
  return response('selected_context',{context_selection_id:selected.contextSelectionId,status:'RETURNED',response:{text:'Exact selected sources'}});
 },selected);
 const capture=await f.host.capture({intent:'ask',kind:'ask'});
 const draft=f.host.save({draftId:'selected',expectedRevision:0,capture,comment:'Use the reviewed sources',delivery:'DRAFT'});
 f.host.setScope({...selected,contextSelectionId:'e'.repeat(64),contextSelectionOperationId:'newer_selection'});
 const result=await f.host.send({draftId:draft.id,operationId:'selected_context',capture,comment:draft.comment,regions:[],marks:[]});
 assert.equal(result.status,'UNKNOWN_OUTCOME');
 const checked=await f.host.inspect('selected_context');
 assert.equal(checked.context_selection_id,selected.contextSelectionId);assert.equal(checked.status,'RETURNED');
 assert.equal(f.calls.filter(call=>call.body).length,1);
 const restored=new QuickJournal({dataDir:f.dataDir,hostGeneration:'restarted'}).find(draft.id);
 assert.equal(restored.scope.contextSelectionId,selected.contextSelectionId);
 assert.equal(restored.scope.contextSelectionOperationId,'select_original');
});
test('canonical projection rejects a selected-context mismatch without accepting another source choice',()=>{
 const {canonicalQuickProjection}=require('../canonical-quick.cjs');
 const command={operationId:'selected',input:{scope:{...scope,contextSelectionId:'d'.repeat(64)}}};
 assert.throws(()=>canonicalQuickProjection(response('selected',{context_selection_id:'e'.repeat(64)}),command),/context selection differs/);
 assert.throws(()=>quickScope({...scope,contextSelectionId:'invalid'}),/reviewed context selection/);
 assert.throws(()=>quickScope({...scope,contextSelectionOperationId:'dangling_choice'}),/context choice operation/);
});
test('unchanged observation updates do not advance the quick lease; real agent/generation changes do',t=>{
 const f=fixture(t);const initial=f.host.scope.generation;
 for(let i=0;i<100;i++)assert.equal(f.host.setScope({...structuredClone(scope),generation:900+i}).generation,initial);
 assert.equal(f.host.setScope({...scope,agent:'build'}).generation,initial+1);
 assert.equal(f.host.setScope({...scope,originalSource:{...scope.originalSource,generation:3}}).generation,initial+2);
});
test('quick sends original user text plus typed frozen image to shared authority; unknown recovery never uses the private native endpoint',async t=>{
 let first=true;
 const f=fixture(t,async(url,body,journal)=>{
  const saved=new QuickJournal({dataDir:f.dataDir,hostGeneration:'reader'}).command('only_post');
  assert.equal(saved.status,body?'DISPATCHING':'UNKNOWN_OUTCOME');assert.deepEqual(saved.input.scope.originalSource,scope.originalSource);
  if(body){assert.equal(url,canonicalQuickRoot(scope));assert.equal(body.raw_text,'保留中文与 emoji 😀');assert.deepEqual(body.conversation_ref,scope.conversationRef);
   assert.deepEqual(body.original_source,scope.originalSource);assert.deepEqual(body.provider_profile_ref,scope.providerProfileRef);
   assert.equal(body.input_attachments[0].kind,'SCREENSHOT');assert.equal(body.input_attachments[0].image_sha256,'c'.repeat(64));assert.deepEqual(body.input_attachments[0].context_ref,ref('frozen'));
   first=false;throw Error('Lost acknowledgement after original dispatch');}
  assert.equal(first,false);assert.equal(url,canonicalQuickRoot(scope)+'/only_post');return response('only_post',{status:'RETURNED',response:{text:'原会话回答'}});
 });
 const capture=await f.host.capture({intent:'ask',kind:'ask'});
 const draft=f.host.save({draftId:'one',expectedRevision:0,capture,comment:'保留中文与 emoji 😀',regions:[],marks:[],delivery:'DRAFT'});
 const sent=await f.host.send({draftId:'one',operationId:'only_post',capture,comment:draft.comment,regions:[],marks:[]});assert.equal(sent.status,'UNKNOWN_OUTCOME');
 f.host.setScope({projectId:'other_project',conversationId:'other_chat'});
 const inspected=await f.host.inspect('only_post');assert.equal(inspected.text,'原会话回答');assert.equal(inspected.canonical_original,true);
 assert.equal(f.calls.filter(c=>c.body).length,1);assert.ok(f.calls.every(c=>!c.url.includes('/native-messages')));
});
test('query rejects another conversation or actor without recording it as the saved reply',async t=>{
 const f=fixture(t,async(_url,body)=>body?response('foreign_test'):response('foreign_test',{original_source:{...scope.originalSource,project_id:'foreign_actor'}}));
 const draft=f.host.save({draftId:'two',expectedRevision:0,comment:'Keep this draft',delivery:'DRAFT'});
 await f.host.send({draftId:draft.id,operationId:'foreign_test',comment:draft.comment,regions:[],marks:[]});
 await assert.rejects(()=>f.host.inspect('foreign_test'),/owner differs/);
 // The failed receipt cannot authorize another original owner.
 const command=f.journal.list()[0];assert.equal(command.message.status,'PROCESSING');assert.equal(command.message.text,'');
});

test('unverified image and clarification preserve the actual saved draft and issue no POST',async t=>{
 const f=fixture(t,()=>{throw Error('Unverified frame must not dispatch');});
 for(const kind of ['origin','questions','hash']){
  const capture=await f.host.capture({intent:'ask',kind:'ask'});
  if(kind==='origin')capture.snapshot.observation.observations.visual.pixel_source_proof.self_exclusion_verified=false;
  if(kind==='questions')capture.snapshot.observation.questions=[{text:'Choose the intended page'}];
  if(kind==='hash')capture.snapshot.observation.observations.visual.pixel_source_proof.image_sha256='f'.repeat(64);
  const item=f.host.save({draftId:kind,expectedRevision:0,capture,comment:'Keep my input 😀',regions:[],marks:[],delivery:'DRAFT'});
  await assert.rejects(()=>f.host.send({draftId:kind,operationId:'never_'+kind,capture,comment:item.comment,regions:[],marks:[]}),/来源明确/);
  assert.equal(f.journal.find(kind).delivery,'DRAFT');assert.equal(f.journal.find(kind).comment,item.comment);
 }
 assert.equal(f.calls.length,0);assert.equal(f.host.inputProofs.size,3);
 assert.ok([...f.host.inputProofs.values()].every(row=>Object.keys(row).sort().join(',')==='sha256,valid'));
});

test('host local refusal is bound to exact unchanged draft while preflight makes no command or model request',async t=>{
 const {QuickNotSubmittedError}=require('../quick-not-submitted.cjs');
 const f=fixture(t,()=>{throw Error('Not admitted');});
 const capture=await f.host.capture({intent:'ask',kind:'ask'});
 capture.snapshot.observation.observations.visual.pixel_source_proof.self_exclusion_verified=false;
 const item=f.host.save({draftId:'preflight',expectedRevision:0,capture,comment:'Keep this question',delivery:'EDITING'});
 const input={draftId:item.id,operationId:'rejected_local',capture,comment:item.comment,regions:[],marks:[]};
 const before=fs.readFileSync(f.journal.file);
 await assert.rejects(()=>f.host.preflightSend(input),/来源明确/);
 assert.deepEqual(fs.readFileSync(f.journal.file),before);
 await assert.rejects(()=>f.host.send(input),error=>error instanceof QuickNotSubmittedError&&error.matches(input.operationId,item));
 assert.equal(f.journal.ownsCommand(input.operationId),false);assert.deepEqual(fs.readFileSync(f.journal.file),before);assert.equal(f.calls.length,0);
});

test('actual WGC contract passes preflight without a write then dispatches its exact typed image once',async t=>{
 const f=fixture(t,async(_url,body)=>response(body?.operation_id??'actual_wgc',{status:'RETURNED',response:{text:'Same original conversation'}}));
 const capture=await f.host.capture({intent:'ask',kind:'ask'}),o=capture.snapshot.observation,v=o.observations.visual;
 const target=ref('a'.repeat(64)),bounds={x:0,y:0,width:100,height:100};
 o.window_ref=target;o.window_lifetime_sha256=target.id;o.window.bounds=bounds;
 v.target_ref=target;v.pixel_source='EXACT_TARGET_WINDOW';v.pixel_source_proof={status:v.pixel_source,
  capture_method:'WINDOWS_GRAPHICS_CAPTURE_EXACT_HWND',coordinate_space:'PHYSICAL_PIXELS_OWN_THREAD_PMV2',
  self_exclusion_verified:true,image_sha256:v.image_ref.sha256,target_identity_sha256:target.id,target_bounds:bounds,capture_bounds:bounds};
 const item=f.host.save({draftId:'exact_wgc',expectedRevision:0,capture,comment:'真实生产者字段，原问题 😀',delivery:'EDITING'});
 const input={draftId:item.id,operationId:'actual_wgc',capture,comment:item.comment,regions:[],marks:[]};
 const before=fs.readFileSync(f.journal.file);
 const checked=await f.host.preflightSend(input);assert.equal(checked.status,'READY_TO_SUBMIT');
 assert.deepEqual(checked.draft_ref,{id:item.id,revision:item.revision,sha256:item.sha256});
 assert.deepEqual(fs.readFileSync(f.journal.file),before);assert.equal(f.calls.length,0);
 const result=await f.host.send(input);assert.equal(result.status,'RETURNED');
 assert.equal(f.calls.length,1);assert.equal(f.calls[0].body.input_attachments[0].kind,'SCREENSHOT');
 assert.equal(f.calls[0].body.input_attachments[0].image_sha256,v.image_ref.sha256);
 await f.host.send(input);assert.equal(f.calls.filter(c=>c.body).length,1);
});

test('one hundred identical canonical observations neither rewrite the journal nor notify the windows',async t=>{
 const f=fixture(t,async(_url,body)=>response(body?.operation_id??'steady',{status:'RETURNED',can_cancel:false,response:{text:'Stable answer'}}));
 const item=f.host.save({draftId:'steady_draft',expectedRevision:0,comment:'One question',delivery:'DRAFT'});
 await f.host.send({draftId:item.id,operationId:'steady',comment:item.comment,regions:[],marks:[]});
 const before=fs.readFileSync(f.journal.file);let writes=0,notifications=0;
 const commit=f.journal.commit;f.journal.commit=function(next){writes++;return commit.call(this,next);};
 f.host.onChanged=()=>notifications++;f.host.onResult=()=>notifications++;
 for(let index=0;index<100;index++)await f.host.inspect('steady');
 assert.equal(writes,0);assert.equal(notifications,0);assert.deepEqual(fs.readFileSync(f.journal.file),before);
});

test('canonical continuation journals before its only POST and recovers, replies and stops the exact later task through one parent',async t=>{
 let lost=true,childQuery=0,stopCount=0;
 const f=fixture(t,async(url,body,journal)=>{
  if(url.endsWith('/child_two/cancel')){stopCount++;return response('child_two',{status:'PROCESSING',cancel:{operation_id:'stop_original',status:'RETURNED'}});}
  if(url.endsWith('/continuations')){
   const child=body.operation_id;
   assert.equal(journal.reviewControl(child).status,'DISPATCHING');assert.equal(body.expected_message_ref.id,child==='child_one'?'parent':'child_one');
   if(lost){lost=false;throw Error('Lost original continuation ACK');}
   return response(child,{operation:{id:child,status:'ACCEPTED',kind:'MESSAGE'}});
  }
  if(url.endsWith('/child_one')){childQuery++;return response('child_one',{status:'RETURNED',response:{text:'The exact continuation answer'},can_cancel:false,operation:{id:'child_one',status:'RETURNED',kind:'MESSAGE'}});}
  if(url.endsWith('/child_two'))return response('child_two',{status:stopCount?'CANCELLED':'PROCESSING',can_cancel:!stopCount,operation:{id:'child_two',status:stopCount?'CANCELLED':'ACCEPTED',kind:'MESSAGE'}});
  return response(body?.operation_id??'parent',{status:'RETURNED',response:{text:'Original answer'},can_cancel:false});
 });
 const item=f.host.save({draftId:'thread',expectedRevision:0,comment:'Original question',delivery:'DRAFT'});
 await f.host.send({draftId:item.id,operationId:'parent',comment:item.comment,regions:[],marks:[]});
 const parentRef=f.journal.command('parent').message.canonical_message_ref;
 const one={parentOperationId:'parent',kind:'guidance',body:{operation_id:'child_one',expected_canonical_message_ref:parentRef,raw_text:'Next input'}};
 assert.equal((await f.host.reviewControl(one)).status,'UNKNOWN_OUTCOME');
 const recovered=await f.host.reviewControl(one);assert.equal(recovered.text,'The exact continuation answer');assert.equal(childQuery,1);
 const parent=await f.host.inspect('parent');assert.equal(parent.canonical_controls[0].text,recovered.text);assert.equal(parent.canonical_input_ref.id,'child_one');
 const two={parentOperationId:'parent',kind:'guidance',body:{operation_id:'child_two',expected_canonical_message_ref:recovered.canonical_message_ref,raw_text:'Another followup'}};
 await f.host.reviewControl(two);const running=await f.host.inspect('parent');assert.equal(running.canonical_stop_ref.id,'child_two');
 await f.host.cancel('parent');await f.host.cancel('parent');assert.equal(stopCount,1);assert.equal(f.journal.command('parent').cancel.target_ref.id,'child_two');
 assert.equal(f.calls.filter(row=>row.url.endsWith('/continuations')&&row.body?.operation_id==='child_one').length,1);
 const restored=new QuickJournal({dataDir:f.dataDir,hostGeneration:'new_host'});assert.equal(restored.canonicalControls('parent').length,2);
});


test('Workflow scope preserves the same canonical actor and rejects unbound or unknown modes',()=>{
 const current={...scope,agent:'workflow'};assert.deepEqual(quickScope(current),current);
 assert.throws(()=>quickScope({agent:'workflow'}),/Invalid original quick agent/);
 assert.throws(()=>quickScope({...current,agent:'unregistered-agent'}),/Invalid original quick agent/);
});
test('Workflow journal and original POST keep the frozen mode after a later preference and restart; unknown only GETs',async t=>{
 const current={...scope,agent:'workflow'};
 const f=fixture(t,async(url,body)=>{if(body){assert.equal(body.agent,'workflow');assert.deepEqual(body.original_source,scope.originalSource);assert.equal(url,canonicalQuickRoot(scope));throw Error('Fixture lost original POST acknowledgement');}return response('workflow_same_key',{status:'RETURNED',response:{text:'Saved original reply'}});},current);
 const draft=f.host.save({draftId:'workflow_draft',expectedRevision:0,comment:'Keep my Workflow input 中文 😀',delivery:'DRAFT'});
 assert.equal(draft.scope.agent,'workflow');
 const result=await f.host.send({draftId:draft.id,operationId:'workflow_same_key',comment:draft.comment,regions:[],marks:[]});assert.equal(result.status,'UNKNOWN_OUTCOME');
 f.host.setScope({...scope,agent:'build'});
 const reopened=new QuickJournal({dataDir:f.dataDir,hostGeneration:'reopened'});assert.equal(reopened.command('workflow_same_key').input.scope.agent,'workflow');
 const recovered=await f.host.inspect('workflow_same_key');assert.equal(recovered.status,'RETURNED');assert.equal(f.calls.filter(c=>c.body).length,1);assert.equal(f.calls.length,2);assert.equal(f.journal.command('workflow_same_key').input.scope.agent,'workflow');
});
