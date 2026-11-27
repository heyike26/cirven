// AUTHORED_NOT_RUN: durable client with explicit UNIT IPC/storage/WebLock boundaries.
// No SDK, native process, renderer layout, Windows or IDE acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {NativeContextRecoveryClient} from '../src/native-context-recovery.ts';
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex');
const source=()=>({parent_operation_id:'native_parent',expected_native_ref:{id:'native_parent',revision:1,sha256:'a'.repeat(64)}});
const storage=()=>{const data=new Map();return {getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),data};};
const locks=()=>{let tail=Promise.resolve();return {request:(_key,_options,work)=>{const next=tail.then(work);tail=next.catch(()=>{});return next;}};};
function preparation(s=source(),existing=null){
 const basis={parent_operation_id:s.parent_operation_id,expected_native_ref:s.expected_native_ref,subject_sha256:'b'.repeat(64),operation_id:'native_recovery_'+'c'.repeat(48),origin:{project_id:'project',conversation_id:'old_chat'},target:{project_id:'project',conversation_ref:null},issue:{code:'SOURCE_CONTEXT_CHANGED'},existing_recovery:existing};
 return {kind:'NATIVE_CONTEXT_RECOVERY_PREPARATION',...basis,preparation_sha256:sha(basis),target_label:'Project',read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_post_replay:false};
}
function result(parent,request,phase='COMPLETE',stop_status=phase==='STOP_PENDING'?'UNKNOWN_OUTCOME':'RETURNED'){
 const suffix=request.operation_id.slice('native_recovery_'.length),creation_request={operation_id:'native_context_chat_'+suffix,project_id:'project',title:null};
 const receipt={version:1,operation_id:creation_request.operation_id,request_sha256:sha(creation_request),conversation_ref:{id:'native:'+'d'.repeat(64),revision:1},project_id:'project',next_message:{project_id:'project',conversation_id:'chat_new'},created_at:'2026-11-03T08:00:00Z',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};
 return {kind:'NATIVE_CONTEXT_RECOVERY',parent_operation_id:parent,operation_id:request.operation_id,request_sha256:sha(request),expected_native_ref:request.expected_native_ref,origin:{project_id:'project',conversation_id:'old_chat'},owner_binding_sha256:'e'.repeat(64),phase,stop_operation_id:'native_context_stop_'+suffix,stop_status,creation_request,creation_receipt:phase==='COMPLETE'?receipt:null,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,external_actions_rolled_back:false,automatic_post_replay:false,model_calls_by_recovery:0};
}
function harness(options={}){
 const disk=options.disk??storage(),lock=options.lock??locks(),calls=[];let saved;
 const bridge={
  prepareNativeContextRecovery:async body=>{calls.push(['PREPARE',structuredClone(body)]);return options.preparation??preparation({parent_operation_id:body.parentOperationId,expected_native_ref:body.expectedNativeRef});},
  restartNativeContextRecovery:async body=>{calls.push(['POST',structuredClone(body)]);saved=result(body.parentOperationId,body.request,options.phase,options.stop_status);if(options.lost)throw Error('UNIT lost response');return saved;},
  inspectNativeContextRecovery:async body=>{calls.push(['GET',structuredClone(body)]);if(options.missing)return {kind:'NATIVE_CONTEXT_RECOVERY_NOT_FOUND',parent_operation_id:body.parentOperationId,operation_id:body.operationId,request_sha256:body.requestSha256,read_only:true,native_posts:0,model_calls:0,original_execution_outcome:'NOT_PROVEN'};return options.observed??saved;},
 };
 return {client:new NativeContextRecoveryClient(bridge,disk,()=>{},lock),bridge,disk,lock,calls,options};
}

test('confirmation is stored before one stop POST and never sends old input or draft',async()=>{
 const h=harness(),s=source(),p=await h.client.prepare(s);let body;
 const post=h.bridge.restartNativeContextRecovery;
 h.bridge.restartNativeContextRecovery=async value=>{body=value;const saved=h.client.pending(s);assert.deepEqual(saved.request,value.request);assert.equal(saved.request_sha256,sha(value.request));return post(value);};
 const r=await h.client.start(s,p,()=>true);
 assert.deepEqual(Object.keys(body.request).sort(),['operation_id','expected_native_ref','preparation_sha256'].sort());
 assert.equal(r.phase,'COMPLETE');assert.deepEqual(h.calls.map(c=>c[0]),['PREPARE','POST','GET']);
 assert.equal(h.client.pending(s).phase,'CONFIRMED');await h.client.opened(s,r.creation_receipt.conversation_ref.id);
 assert.equal(h.client.pending(s).phase,'OPENED');await assert.rejects(()=>h.client.start(s,p,()=>true));
});

test('lost stop/create reply recovers after reload by the original GET without replay',async()=>{
 const h=harness({lost:true}),s=source();await h.client.start(s,await h.client.prepare(s),()=>true);
 const reloaded=new NativeContextRecoveryClient(h.bridge,h.disk,()=>{},h.lock);await reloaded.inspect(s);
 assert.deepEqual(h.calls.map(c=>c[0]),['PREPARE','POST','GET','GET']);assert.equal(reloaded.pending(s).phase,'CONFIRMED');
});

test('unknown stop remains observable and explicit continue does not send another POST',async()=>{
 const h=harness({phase:'STOP_PENDING'}),s=source();await h.client.start(s,await h.client.prepare(s),()=>true);
 await h.client.inspect(s);await h.client.continue(s,()=>true);
 assert.equal(h.client.pending(s).phase,'ATTEMPTED');assert.equal(h.calls.filter(c=>c[0]==='POST').length,1);
});

test('missing original record can retry only the same frozen request with explicit continue',async()=>{
 const h=harness({missing:true}),s=source(),p=await h.client.prepare(s);
 await assert.rejects(()=>h.client.start(s,p,()=>true),/NOT_FOUND/);assert.equal(h.client.pending(s).phase,'NOT_FOUND');
 // The fresh pre-retry GET must still prove absence; a recovered receipt must
 // suppress another POST even after an earlier NOT_FOUND observation.
 const originalPost=h.bridge.restartNativeContextRecovery;h.bridge.restartNativeContextRecovery=async body=>{h.options.missing=false;return originalPost(body);};
 await h.client.continue(s,()=>true);
 const posts=h.calls.filter(c=>c[0]==='POST');assert.equal(posts.length,2);assert.deepEqual(posts[0][1],posts[1][1]);
 const recovered=harness({missing:true}),ready=await recovered.client.prepare(s);await assert.rejects(()=>recovered.client.start(s,ready,()=>true),/NOT_FOUND/);
 recovered.options.missing=false;await recovered.client.continue(s,()=>true);assert.equal(recovered.calls.filter(c=>c[0]==='POST').length,1);assert.equal(recovered.client.pending(s).phase,'CONFIRMED');
});

test('stop-confirmed metadata continuation is explicit and preserves the original request',async()=>{
 const h=harness({phase:'STOP_CONFIRMED'}),s=source();await h.client.start(s,await h.client.prepare(s),()=>true);
 await h.client.inspect(s);assert.equal(h.calls.filter(c=>c[0]==='POST').length,1);
 h.options.phase='COMPLETE';await h.client.continue(s,()=>true);
 const posts=h.calls.filter(c=>c[0]==='POST');assert.equal(posts.length,2);assert.deepEqual(posts[0][1],posts[1][1]);
});

test('another native parent already recovering the same owner is read by its original key',async()=>{
 const s=source(),other={parent_operation_id:'other_parent',expected_native_ref:{...s.expected_native_ref,id:'other_parent'}};
 const p=preparation(s),request={operation_id:p.operation_id,expected_native_ref:other.expected_native_ref,preparation_sha256:'f'.repeat(64)};
 const h=harness({preparation:preparation(s,{parent_operation_id:other.parent_operation_id,request,request_sha256:sha(request)}),observed:result(other.parent_operation_id,request)});
 const r=await h.client.start(s,await h.client.prepare(s),()=>true);
 assert.equal(r.parent_operation_id,'other_parent');assert.deepEqual(h.calls.map(c=>c[0]),['PREPARE','GET']);assert.equal(h.client.pending(s).parent_operation_id,'other_parent');
});

test('foreign or broadened receipt is retained as attempted and cannot acknowledge recovery',async()=>{
 for(const changed of [{parent_operation_id:'foreign'},{source_data_copied:true},{execution_started:true},{external_actions_rolled_back:true},{model_calls_by_recovery:1}]){
  const h=harness(),s=source(),p=await h.client.prepare(s),request={operation_id:p.operation_id,expected_native_ref:s.expected_native_ref,preparation_sha256:p.preparation_sha256};
  h.options.observed={...result(s.parent_operation_id,request),...changed};
  await assert.rejects(()=>h.client.start(s,p,()=>true));assert.equal(h.client.pending(s).phase,'ATTEMPTED');
 }
});

test('wrong new-chat route cannot be accepted despite a matching creation request hash',async()=>{
 const h=harness(),s=source(),p=await h.client.prepare(s),request={operation_id:p.operation_id,expected_native_ref:s.expected_native_ref,preparation_sha256:p.preparation_sha256},r=result(s.parent_operation_id,request);
 r.creation_receipt.next_message.project_id='foreign_project';h.options.observed=r;
 await assert.rejects(()=>h.client.start(s,p,()=>true));assert.equal(h.client.pending(s).phase,'ATTEMPTED');
});

test('cross-window WebLock and durable pending record prevent a second stop POST',async()=>{
 const h=harness(),s=source(),p=await h.client.prepare(s),other=new NativeContextRecoveryClient(h.bridge,h.disk,()=>{},h.lock);
 const values=await Promise.allSettled([h.client.start(s,p,()=>true),other.start(s,p,()=>true)]);
 assert.equal(values.filter(v=>v.status==='fulfilled').length,1);assert.equal(h.calls.filter(c=>c[0]==='POST').length,1);
});

test('storage failure or navigation change prevents dispatch and retains existing input owner',async()=>{
 const h=harness({disk:{getItem:()=>null,setItem:()=>{throw Error('UNIT quota');}}}),s=source(),p=await h.client.prepare(s);
 await assert.rejects(()=>h.client.start(s,p,()=>true));assert.equal(h.calls.filter(c=>c[0]==='POST').length,0);
 const other=harness(),fresh=await other.client.prepare(s);await assert.rejects(()=>other.client.start(s,fresh,()=>false));
 assert.equal(other.calls.filter(c=>c[0]==='POST').length,0);
});
