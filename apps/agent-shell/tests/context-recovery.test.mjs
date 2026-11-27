// AUTHORED_NOT_RUN: real durable client, fake HTTP/lock/storage boundaries.
// No original process stop, model, renderer layout, Windows or IDE qualification.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {ContextRecoveryClient} from '../src/context-recovery.ts';
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex');
const conversation={id:'native:'+'c'.repeat(64),revision:1};
const session=()=>({project_id:'project',session_ref:{id:'a'.repeat(32),revision:1,sha256:'b'.repeat(64)},generation:2,
 context_authority_issue:{code:'SOURCE_CONTEXT_CHANGED',generation:2,operation_id:'old_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true},
 messages:[{private:'KEEP_OLD_CHAT'}],draft:'KEEP_OLD_DRAFT',active_operation_id:'old_task'});
const storage=()=>{const data=new Map();return {getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),data};};
const locks=()=>{let tail=Promise.resolve();return {request:(_n,_o,work)=>{const next=tail.then(work);tail=next.catch(()=>{});return next;}};};
function result(request,phase='COMPLETE'){
 const creation={operation_id:'context_chat_'+'e'.repeat(48),project_id:'project',title:null};
 const receipt={version:1,operation_id:creation.operation_id,request_sha256:sha(creation),conversation_ref:{id:'native:'+'d'.repeat(64),revision:1},project_id:'project',next_message:{project_id:'project',conversation_id:'chat_fresh'},created_at:'2026-11-03T03:00:00Z',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};
 return {version:1,operation_id:request.operation_id,request_sha256:sha(request),project_id:'project',session_ref:request.session_ref,generation:request.generation,conversation_ref:request.conversation_ref,phase,stop_operation_id:'context_stop_'+'e'.repeat(48),stop_status:phase==='STOP_PENDING'?'UNKNOWN_OUTCOME':'RETURNED',creation_request:creation,creation_receipt:phase==='COMPLETE'?receipt:null,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,automatic_post_replay:false,model_calls_by_recovery:0};
}
function preparationFixture(scope,existing=null){const basis={kind:'CONTEXT_RECOVERY_PREPARATION',scope,conversation_ref:conversation,expected_issue:session().context_authority_issue,target_project_id:'project',existing_recovery:existing};return {...basis,preparation_sha256:sha(basis),read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_replay:false};}
function harness(handler,disk=storage(),lock=locks()){
 const calls=[],prepares=[],h={};const api={request:async(...args)=>{
  assert.equal(args[4],false);
  if(args[0].endsWith('/prepare')){prepares.push(structuredClone(args.slice(0,3)));return h.prepare?h.prepare(args[2]):preparationFixture({project_id:'project',session_ref:args[2].session_ref,generation:args[2].generation});}
  calls.push(structuredClone(args.slice(0,3)));return handler(...args);
 }};
 Object.assign(h,{client:new ContextRecoveryClient(api,disk,()=>{},lock,()=> 'original_recovery'),api,calls,prepares,disk,lock});return h;
}

test('only the frozen source issue is sent; old chat/draft/file authority never leave this client',async()=>{
 const s=session(),before=structuredClone(s);let h;h=harness((_p,_m,body)=>{
  const saved=[...h.disk.data.values()].map(v=>JSON.parse(v))[0].pending;assert.deepEqual(saved.request,body);assert.equal(saved.checksum,sha(body));
  assert.deepEqual(Object.keys(body).sort(),['operation_id','session_ref','generation','conversation_ref','expected_issue'].sort());assert.doesNotMatch(JSON.stringify(body),/KEEP_OLD|raw_text|allowed_effects/);return result(body);
 });
 const receipt=await h.client.start(s,conversation);assert.deepEqual(s,before);assert.equal(h.calls.length,1);assert.equal(h.client.pending(s).phase,'CONFIRMED');
 await h.client.acknowledge(s,receipt.conversation_ref.id);await h.client.acknowledge(s,receipt.conversation_ref.id);assert.equal(h.client.pending(s).phase,'ADOPTED');
 await assert.rejects(()=>h.client.start(s,conversation));assert.equal(h.calls.length,1);
});

test('lost response survives reload and checks exact original key with GET only',async()=>{
 let saved;const h=harness((_path,method,body)=>{if(method==='POST'){saved=result(body);throw Error('UNIT lost ack');}return saved;}),s=session();
 await assert.rejects(()=>h.client.start(s,conversation));
 const restarted=new ContextRecoveryClient(h.api,h.disk,()=>{},h.lock);
 assert.equal(restarted.pending(s).phase,'ATTEMPTED');const receipt=await restarted.inspect(s);
 assert.equal(receipt.conversation_ref.id,saved.creation_receipt.conversation_ref.id);assert.deepEqual(h.calls.map(c=>c[1]),['POST','GET']);assert.match(h.calls[1][0],/context-recovery\/original_recovery$/);
});

test('confirmed stop requires an explicit metadata continuation using the same request',async()=>{
 let saved,allow=false;const h=harness((_p,method,body)=>{if(body)saved=structuredClone(body);return result(saved,allow?'COMPLETE':'STOP_CONFIRMED');}),s=session();
 await assert.rejects(()=>h.client.start(s,conversation),/RECOVERY_READY/);await assert.rejects(()=>h.client.inspect(s),/RECOVERY_READY/);assert.deepEqual(h.calls.map(c=>c[1]),['POST','GET']);
 allow=true;await h.client.continueCreation(s);assert.deepEqual(h.calls[0][2],h.calls[2][2]);assert.equal(h.client.pending(s).phase,'CONFIRMED');
});

test('unknown original stop remains pending across inspections without automatic POST',async()=>{
 let saved;const h=harness((_p,_m,body)=>{if(body)saved=body;return result(saved,'STOP_PENDING');}),s=session();
 await assert.rejects(()=>h.client.start(s,conversation),/RECOVERY_PENDING/);await assert.rejects(()=>h.client.inspect(s),/RECOVERY_PENDING/);await assert.rejects(()=>h.client.inspect(s),/RECOVERY_PENDING/);
 assert.deepEqual(h.calls.map(c=>c[1]),['POST','GET','GET']);assert.equal(h.client.pending(s).phase,'ATTEMPTED');
});

test('a missing recovery can only be retried explicitly with its original frozen key',async()=>{
 let missing=true;const h=harness((_p,method,body)=>{if(method==='GET')throw Object.assign(Error('missing'),{status:404});if(missing){missing=false;throw Error('UNIT request never arrived');}return result(body);}),s=session();
 await assert.rejects(()=>h.client.start(s,conversation));await assert.rejects(()=>h.client.inspect(s),/RECOVERY_NOT_FOUND/);assert.equal(h.client.pending(s).phase,'NOT_FOUND');
 await h.client.continueCreation(s);assert.deepEqual(h.calls[0][2],h.calls[2][2]);assert.deepEqual(h.calls.map(c=>c[1]),['POST','GET','POST']);
});

test('foreign or broadened receipts cannot acknowledge a context reset',async()=>{
 for(const changed of [{generation:3},{drafts_transferred:true},{source_data_copied:true},{execution_started:true},{model_calls_by_recovery:1},{conversation_ref:{id:'native:'+'f'.repeat(64),revision:1}}]){
  const h=harness((_p,_m,body)=>({...result(body),...changed})),s=session();await assert.rejects(()=>h.client.start(s,conversation),/RECOVERY_UNAVAILABLE/);assert.equal(h.client.pending(s).phase,'ATTEMPTED');
 }
});

test('cross-window lock and stored original attempt prevent a second recovery POST',async()=>{
 const h=harness((_p,_m,body)=>result(body)),other=new ContextRecoveryClient(h.api,h.disk,()=>{},h.lock,()=> 'other_key'),s=session();
 const values=await Promise.allSettled([h.client.start(s,conversation),other.start(s,conversation)]);assert.equal(values.filter(v=>v.status==='fulfilled').length,1);assert.equal(h.calls.length,1);
});

test('unavailable durable storage or stale issue cannot dispatch even a stop',async()=>{
 const h=harness(()=>assert.fail('No HTTP'),{getItem:()=>null,setItem:()=>{throw Error('UNIT storage full');}}),s=session();await assert.rejects(()=>h.client.start(s,conversation));assert.equal(h.calls.length,0);
 const fresh=harness(()=>assert.fail('No HTTP'));await assert.rejects(()=>fresh.client.start({...s,generation:3},conversation));assert.equal(fresh.calls.length,0);
});

// AUTHORED_NOT_RUN batch29: preparation is a separate read-only metadata call.
test('an existing IDE recovery is inspected under its original request without a new stop POST',async()=>{
 const s=session(),request={operation_id:'ide_original',session_ref:s.session_ref,generation:s.generation,conversation_ref:conversation,expected_issue:s.context_authority_issue};
 const h=harness((_p,method)=>{assert.equal(method,'GET');return result(request);});
 h.prepare=body=>preparationFixture({project_id:s.project_id,...body},{request,request_sha256:sha(request),target_project_id:s.project_id});
 const receipt=await h.client.start(s,conversation);assert.equal(h.prepares.length,1);assert.deepEqual(h.calls.map(c=>c[1]),['GET']);assert.match(h.calls[0][0],/ide_original$/);assert.equal(h.client.pending(s).request.operation_id,'ide_original');assert.equal(receipt.messages_created,0);
});

test('a cross-surface winner after preparation preserves our lost attempt and checks that exact original operation',async()=>{
 const s=session(),other={operation_id:'other_surface',session_ref:s.session_ref,generation:s.generation,conversation_ref:conversation,expected_issue:s.context_authority_issue};let h;
 h=harness((route,method)=>{
  if(method==='POST')throw Error('UNIT local dispatch lost');
  if(route.endsWith('/original_recovery'))throw Object.assign(Error('UNIT original not found'),{status:404});
  assert.match(route,/other_surface$/);return result(other);
 });
 let prepared=0;h.prepare=body=>preparationFixture({project_id:s.project_id,...body},++prepared===1?null:{request:other,request_sha256:sha(other),target_project_id:s.project_id});
 await assert.rejects(()=>h.client.start(s,conversation));await h.client.inspect(s);
 const saved=h.client.pending(s);assert.equal(saved.request.operation_id,'other_surface');assert.equal(saved.original_attempt.request.operation_id,'original_recovery');assert.equal(saved.original_attempt.transport_status,'UNKNOWN');assert.equal(saved.original_attempt.original_record_last_observed,'NOT_FOUND');assert.equal(saved.original_attempt.checksum,sha(saved.original_attempt.request));assert.deepEqual(h.calls.map(c=>c[1]),['POST','GET','GET']);
});
