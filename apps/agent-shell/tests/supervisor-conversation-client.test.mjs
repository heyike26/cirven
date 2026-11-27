import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import ts from 'typescript';
import {webcrypto} from 'node:crypto';
globalThis.crypto??=webcrypto;
const require=createRequire(import.meta.url);
const {SupervisorConversation,supervisorConversationDigest:digest}=require('../../desktop-host/supervisor-conversation.cjs');
const source=await readFile(new URL('../src/supervisor-conversation-client.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {SupervisorConversationClient,supervisorClientSnapshot,supervisorClientErrorCode,supervisorDraftFailureKind,supervisorHistoryPage}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const clone=value=>JSON.parse(JSON.stringify(value)),sha='a'.repeat(64),profile={id:'provider',revision:1,sha256:sha},stamp='2026-10-19T12:00:00.000Z';
const snapshot=(revision=0,text='',draftRevision=0)=>({revision,conversation_id:'global_conversation',draft:{text,revision:draftRevision},messages:[],operations:[]});
const operation=(id,status='UNKNOWN')=>({operation_id:id,status,request_sha256:sha,created_at:stamp});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fakeClock(){let seq=0;const timers=new Map();return {timers,setTimeout:(fn,ms)=>{assert.equal(ms,300);timers.set(++seq,fn);return seq;},clearTimeout:id=>timers.delete(id),fire(){const queued=[...timers.values()];timers.clear();queued.forEach(fn=>fn());}};}
function fixture(extra={}){
  let state=snapshot(),listener;const calls=[],updates=[],clock=fakeClock();
  const emit=value=>{state=clone(value);listener?.(clone(value));};
  const bridge={getSnapshot:async()=>clone(state),saveDraft:async body=>{calls.push({kind:'save',body:clone(body)});assert.equal(body.expectedRevision,state.draft.revision);const next={...state,revision:state.revision+1,draft:{text:body.text,revision:state.draft.revision+1}};emit(next);return clone(next);},
    ask:async body=>{calls.push({kind:'ask',body:clone(body)});return {operation_id:body.operationId,status:'RETURNED',text:'原问题答复'};},
    inspect:async id=>{calls.push({kind:'inspect',id});return {operation_id:id,status:'RETURNED',text:'读取原操作'};},
    persistLocal:async body=>{calls.push({kind:'local',body});const next={...state,revision:state.revision+1,draft:{text:state.draft.text===body.question?'':state.draft.text,revision:state.draft.revision+1}};emit(next);return clone(next);},
    onChange:callback=>{listener=callback;return()=>{listener=undefined;};},...extra};
  const client=new SupervisorConversationClient(bridge,value=>updates.push(value),{clock});
  return {client,bridge,calls,updates,clock,emit,get state(){return clone(state);}};
}

const historyInfo=(basis=sha,total=144)=>({basis_sha256:basis,total_records:total,archived_records:120,archived_operations:0,archive_bytes:12000,archive_limit_bytes:33554432,segments:5});
function historyPage(offset=0,basis=sha,total=144){const end=total-offset,start=Math.max(0,end-24),messages=[];for(let i=start;i<end;i++)messages.push({id:'history_'+i+'_user',role:'user',text:'原问题 '+i},{id:'history_'+i+'_assistant',role:'assistant',text:'原答复 '+i,status:'RETURNED'});return {conversation_id:'global_conversation',basis_sha256:basis,messages,operations:[],next_cursor:start?'cursor_'+(offset+24):null,record_count:end-start,total_records:total,archived_records:120};}

test('paged history is one read-only flight, prepends stable ids, bounds its window, and never saves or dispatches',async()=>{
  const reads=[];const f=fixture({readHistoryPage:async body=>{reads.push(body);return historyPage(body.cursor?Number(body.cursor.split('_')[1]):0);}});await f.client.start();f.emit({...snapshot(1),history:historyInfo()});assert.equal(f.client.view.history.hasMore,true);
  const first=f.client.readEarlier();assert.equal(f.client.readEarlier(),first);await first;assert.equal(reads.length,1);assert.deepEqual(reads[0],{cursor:null,limit:24});assert.equal(f.client.view.history.messages.length,48);
  const frozen=f.client.view.history.messages;assert.ok(Object.isFrozen(frozen));f.client.edit('保留当前草稿');assert.equal(f.client.view.history.messages,frozen);
  for(let i=0;i<5;i++)await f.client.readEarlier();const view=f.client.view;assert.equal(view.history.messages.length,240);assert.equal(view.history.windowed,true);assert.equal(view.history.hasMore,false);assert.equal(view.history.messages[0].text,'原问题 0');assert.equal(new Set(view.history.messages.map(row=>row.id)).size,240);assert.equal(view.draft,'保留当前草稿');assert.equal(f.calls.length,0);f.client.resetHistory();assert.equal(f.client.view.history.messages.length,0);assert.equal(f.client.view.history.hasMore,true);assert.equal(f.client.view.draft,'保留当前草稿');
});

test('read-window reset and close reject late pages without clearing input, UNKNOWN or sending a model request',async()=>{
  const gate=deferred();const f=fixture({readHistoryPage:()=>gate.promise});await f.client.start();f.emit({...snapshot(1),operations:[operation('unknown_original')],history:historyInfo()});const pending=f.client.readEarlier();await tick();f.client.edit('分页期间新增输入');f.client.resetHistory();gate.resolve(historyPage());await pending;
  assert.equal(f.client.view.history.messages.length,0);assert.equal(f.client.view.history.loading,false);assert.equal(f.client.view.draft,'分页期间新增输入');assert.equal(f.client.view.snapshot.operations[0].status,'UNKNOWN');assert.equal(f.calls.length,0);
  const later=deferred();f.bridge.readHistoryPage=()=>later.promise;const late=f.client.readEarlier();await tick();await f.client.close();const updates=f.updates.length;later.resolve(historyPage());await late;assert.equal(f.updates.length,updates);assert.equal(f.calls.filter(row=>row.kind==='ask').length,0);
});

test('new history basis preserves an open read window and read errors do not block independent draft persistence',async()=>{
  let unavailable=false,basis=sha;const reads=[];const f=fixture({readHistoryPage:async body=>{reads.push(body);if(unavailable)throw Error('private disk failure');return historyPage(0,basis);}});await f.client.start();f.emit({...snapshot(1),history:historyInfo()});await f.client.readEarlier();const before=f.client.view.history.messages;
  basis='b'.repeat(64);f.emit({...snapshot(2),history:historyInfo(basis)});assert.equal(f.client.view.history.messages,before);assert.equal(f.client.view.history.changed,true);unavailable=true;await f.client.readEarlier();assert.equal(f.client.view.history.messages,before);assert.equal(f.client.view.history.error,'UNAVAILABLE');assert.equal(f.client.view.draftBlocked,false);
  f.client.edit('分页坏了仍可保存');await f.client.flush();assert.equal(f.state.draft.text,'分页坏了仍可保存');unavailable=false;await f.client.readEarlier();assert.equal(reads.at(-1).cursor,null);assert.equal(f.client.view.history.changed,false);assert.equal(f.client.view.error,undefined);assert.equal(f.calls.filter(row=>row.kind==='ask').length,0);
});

test('an archived operation is read by exact reference, foreign reads fail, and maximum original ids retain paired message identities',async()=>{
  const ref={operation_id:'old_archived',request_sha256:sha},reads=[];let foreign=false;const f=fixture({readOperation:async body=>{reads.push(body);return {conversation_id:foreign?'foreign':'global_conversation',operation:operation(ref.operation_id,'RETURNED'),question:'原问题',answer_text:'原回答'};}});await f.client.start();assert.equal((await f.client.readOperation({...ref,ignored:'not a wire field'})).operation_id,ref.operation_id);assert.deepEqual(reads,[ref]);assert.equal(f.calls.length,0);foreign=true;await assert.rejects(f.client.readOperation(ref),/FOLLOWUP_CHANGED/);
  const id='x'.repeat(100),page=historyPage();page.record_count=1;page.messages=[{id:id+'_user',role:'user',operation_id:id,text:'最长身份问题'},{id:id+'_assistant',role:'assistant',operation_id:id,text:'最长身份回答',status:'RETURNED'}];page.operations=[operation(id,'RETURNED')];assert.equal(supervisorHistoryPage(page).messages[1].id,id+'_assistant');
});

test('more than 128 terminal client asks continue while a retained UNKNOWN never replays or loses its original key',async()=>{
  let calls=0;const f=fixture({ask:async body=>{calls++;return {operation_id:body.operationId,status:body.operationId==='unknown_keep'?'UNKNOWN':'RETURNED',text:'原回执'};}});await f.client.start();
  for(let i=0;i<130;i++)assert.equal((await f.client.ask({operationId:'complete_'+i,text:'明确问题 '+i,providerProfileRef:profile})).status,'RETURNED');
  const unknown={operationId:'unknown_keep',text:'原未知问题',providerProfileRef:profile};await f.client.ask(unknown);for(let i=130;i<200;i++)await f.client.ask({operationId:'complete_'+i,text:'明确问题 '+i,providerProfileRef:profile});const count=calls;assert.equal((await f.client.ask(unknown)).status,'UNKNOWN');assert.equal(calls,count);await assert.rejects(f.client.ask({...unknown,operationId:'different_key'}),/REPLAY_BLOCKED/);assert.equal(calls,count);
});

test('128 PROCESSING receipts followed by exact terminal snapshots continue after archival, without a stale pending cache fence',async()=>{
 let f,revision=0;f=fixture({ask:async body=>{const op=operation(body.operationId,'PROCESSING');f.emit({...snapshot(++revision),operations:[op],messages:[{id:body.operationId+'_user',role:'user',operation_id:body.operationId,text:body.text,provider_profile_ref:profile}]});return {operation_id:body.operationId,status:'PROCESSING'};}});await f.client.start();
 for(let i=0;i<130;i++){const id='observed_'+i,text='明确提问 '+i;await f.client.ask({operationId:id,text,providerProfileRef:profile});f.emit({...f.state,revision:++revision,operations:[operation(id,'RETURNED')]});assert.equal(f.client.askAdmission('不同后续问题',profile).allowed,true);f.emit({...snapshot(++revision),operations:[]});assert.equal(f.client.askAdmission('不同后续问题',profile).allowed,true);}
 assert.equal(f.client.view.error,undefined);
});

test('a bound exact terminal fact wins over a late UNKNOWN receipt, while a changed request SHA never releases an UNKNOWN',async()=>{
 const gate=deferred();let f;f=fixture({ask:async body=>{f.emit({...snapshot(1),operations:[operation(body.operationId,'PROCESSING')],messages:[{id:'original_user',role:'user',operation_id:body.operationId,text:body.text,provider_profile_ref:profile}]});await gate.promise;return {operation_id:body.operationId,status:'UNKNOWN'};}});await f.client.start();const pending=f.client.ask({operationId:'original',text:'原问题',providerProfileRef:profile});await tick();f.emit({...f.state,revision:2,operations:[operation('original','RETURNED')]});gate.resolve();assert.equal((await pending).status,'RETURNED');assert.equal(f.client.askAdmission('新问题',profile).allowed,true);
 let g;g=fixture({ask:async body=>{g.emit({...snapshot(1),operations:[operation(body.operationId,'UNKNOWN')],messages:[{id:'unknown_user',role:'user',operation_id:body.operationId,text:body.text,provider_profile_ref:profile}]});return {operation_id:body.operationId,status:'UNKNOWN'};}});await g.client.start();await g.client.ask({operationId:'unknown',text:'原未知问题',providerProfileRef:profile});g.emit({...g.state,revision:2,operations:[{...operation('unknown','RETURNED'),request_sha256:'b'.repeat(64)}]});assert.equal(g.client.askAdmission('原未知问题',profile).allowed,false);g.emit({...snapshot(3),operations:[]});assert.equal(g.client.askAdmission('原未知问题',profile).allowed,false);
});

test('actual pre-claim capacity proof keeps the draft editable, never dispatches, and only an explicit later send succeeds after original inspection',async t=>{
 const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-proof-real-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));let listener,posts=0;const request=async(route,body)=>{if(route==='/api/supervisor')return {tasks:[],observer:{status:'READY'}};if(body)posts++;const id=body?.operation_id??route.split('/').at(-1),op=host.operation(id);return {operation_id:id,status:'RETURNED',text:'明确原操作回执',automatic_retry:false,project_id:null,conversation_id:host.state.conversation_id,request_sha256:body?digest(body):op.request_sha256,provider_profile_ref:profile,context_ref:null,file_source_ref:null,file_link_ref:null};};
 let host=new SupervisorConversation({dataDir,request,onChange:value=>listener?.(value)});const state=clone(host.state);state.operations=Array.from({length:7},(_,i)=>({operation_id:'unknown_'+i,question:'原未知问题 '+i,provider_profile_ref:profile,input_sha256:digest({operation_id:'unknown_'+i,text:'原未知问题 '+i,provider_profile_ref:profile}),request_sha256:digest({fixture:i}),context_sha256:sha,status:'UNKNOWN',text:'',created_at:stamp}));fs.writeFileSync(host.file,JSON.stringify({version:1,state,sha256:digest(state)})+'\n');host=new SupervisorConversation({dataDir,request,onChange:value=>listener?.(value)});
 const client=new SupervisorConversationClient({getSnapshot:async()=>host.snapshot(),saveDraft:async body=>host.saveDraft(body),ask:body=>host.ask(body),inspect:id=>host.inspect(id),persistLocal:body=>host.persistLocal(body),onChange:fn=>{listener=fn;return()=>listener=undefined;}},()=>{},{clock:fakeClock()});await client.start();const text='问'.repeat(8000);client.edit(text);const result=await client.ask({operationId:'preclaim_full',text,providerProfileRef:profile});assert.equal(result.status,'REJECTED');assert.equal(result.error,'SUPERVISOR_STORAGE_FULL');assert.equal(posts,0);assert.equal(client.view.draft,text);assert.equal(client.view.draftBlocked,false);assert.equal(client.askAdmission(text,profile).allowed,true);assert.equal(host.lookupOperation('preclaim_full'),undefined);
 await client.inspect('unknown_0');assert.equal(posts,0);client.edit('缩短后的明确新问题');assert.equal((await client.ask({operationId:'explicit_retry',text:client.view.draft,providerProfileRef:profile})).status,'RETURNED');assert.equal(posts,1);await client.close();
});

test('missing, forged, wrong-input or stale-basis non-dispatch claims remain UNKNOWN and cannot authorize a replay',async()=>{
 const proof=(body,state)=>({operation_id:body.operationId,status:'REJECTED',error:'SUPERVISOR_STORAGE_FULL',dispatch_proof:{version:1,outcome:'NOT_DISPATCHED',conversation_id:state.conversation_id,operation_id:body.operationId,input_sha256:digest({operation_id:body.operationId,text:body.text,provider_profile_ref:body.providerProfileRef}),basis:{revision:state.revision,sha256:state.state_sha256}}});
 for(const mutate of [value=>delete value.dispatch_proof,value=>value.dispatch_proof.conversation_id='foreign',value=>value.dispatch_proof.operation_id='foreign',value=>value.dispatch_proof.input_sha256='b'.repeat(64),value=>value.dispatch_proof.basis.sha256='b'.repeat(64),value=>value.dispatch_proof.basis.revision++,value=>value.dispatch_proof.extra=true]){
  let f;f=fixture({ask:async body=>{const value=proof(body,f.state);mutate(value);return value;}});await f.client.start();f.emit({...snapshot(1),state_sha256:sha});assert.equal((await f.client.ask({operationId:'guarded',text:'原问题',providerProfileRef:profile})).status,'UNKNOWN');assert.equal(f.client.askAdmission('原问题',profile).allowed,false);
 }
 const gate=deferred();let f;f=fixture({ask:body=>{const value=proof(body,f.state);return gate.promise.then(()=>value);}});await f.client.start();f.emit({...snapshot(1),state_sha256:sha});const pending=f.client.ask({operationId:'late',text:'原问题',providerProfileRef:profile});await tick();f.emit({...snapshot(2),state_sha256:'b'.repeat(64)});gate.resolve();assert.equal((await pending).status,'UNKNOWN');assert.equal(f.client.askAdmission('原问题',profile).allowed,false);
});

test('local typing publishes immediately and the 300ms batch saves only the newest real CAS draft',async()=>{
  const f=fixture();await f.client.start();f.client.edit('a');assert.equal(f.updates.at(-1).draft,'a');f.client.edit('ab');f.client.edit('abc');
  assert.equal(f.calls.length,0);assert.equal(f.clock.timers.size,1);f.clock.fire();await tick();
  assert.deepEqual(f.calls,[{kind:'save',body:{text:'abc',expectedRevision:0}}]);assert.equal(f.client.view.dirty,false);assert.equal(f.client.view.snapshot.draft.text,'abc');
});

test('new typing during save remains visible through its old event/ack and flush saves the new draft with the new basis',async()=>{
  const gate=deferred();let f;f=fixture({saveDraft:async body=>{f.calls.push({kind:'save',body});if(f.calls.length===1)await gate.promise;const next={...f.state,revision:f.state.revision+1,draft:{text:body.text,revision:body.expectedRevision+1}};f.emit(next);return next;}});
  await f.client.start();f.client.edit('发起时文字');f.clock.fire();await tick();f.client.edit('后来继续写的文字');const marker=f.updates.length;
  gate.resolve();await f.client.flush();assert.equal(f.client.view.draft,'后来继续写的文字');assert.equal(f.client.view.dirty,false);
  assert.ok(f.updates.slice(marker).every(view=>view.draft==='后来继续写的文字'));assert.deepEqual(f.calls.map(row=>row.body),[{text:'发起时文字',expectedRevision:0},{text:'后来继续写的文字',expectedRevision:1}]);
});

test('out-of-order event and initial GET cannot downgrade a newer global revision or replace a new local edit',async()=>{
  const gate=deferred();const f=fixture({getSnapshot:()=>gate.promise});const starting=f.client.start();
  f.emit(snapshot(4,'最新外部草稿',3));f.client.edit('用户此时新输入');gate.resolve(snapshot(0));await starting;
  assert.equal(f.client.view.snapshot.revision,4);assert.equal(f.client.view.draft,'用户此时新输入');f.emit(snapshot(2,'旧快照',1));assert.equal(f.client.view.snapshot.revision,4);
});

test('external draft conflict retains both drafts and refuses automatic overwrite until explicit resolution',async()=>{
  const f=fixture();await f.client.start();f.client.edit('本地尚未保存');f.emit(snapshot(2,'另一个窗口的新草稿',1));
  assert.equal(f.client.view.draft,'本地尚未保存');assert.equal(f.client.view.snapshot.draft.text,'另一个窗口的新草稿');assert.match(f.client.view.error.code,/DRAFT_CONFLICT/);
  f.clock.fire();await tick();assert.equal(f.calls.length,0);await assert.rejects(f.client.flush(),/DRAFT_CONFLICT/);
  f.client.resolveDraftConflict('keep-local');assert.equal(f.calls.length,0,'resolution alone does not post');await f.client.flush();assert.equal(f.calls[0].body.expectedRevision,1);assert.equal(f.state.draft.text,'本地尚未保存');
});

test('first-load remote nonempty draft cannot be silently overwritten by typing before load',async()=>{
  const gate=deferred();const f=fixture({getSnapshot:()=>gate.promise});const starting=f.client.start();f.client.edit('载入期间写的');gate.resolve(snapshot(1,'旧的跨窗口草稿',1));await starting;
  assert.equal(f.client.view.draft,'载入期间写的');assert.match(f.client.view.error.code,/DRAFT_CONFLICT/);await assert.rejects(f.client.flush());assert.equal(f.calls.length,0);
  f.client.resolveDraftConflict('use-remote');assert.equal(f.client.view.draft,'旧的跨窗口草稿');assert.equal(f.client.view.dirty,false);
});

test('failed or synchronously throwing saves preserve input and do not automatically repeat CAS',async()=>{
  for(const synchronous of [false,true]){
    let saves=0;const failure=()=>{saves++;throw Object.assign(Error('PRIVATE SECRET'),{code:'SUPERVISOR_DRAFT_CONFLICT'});};
    const f=fixture({saveDraft:synchronous?failure:async()=>failure()});await f.client.start();f.client.edit('保留输入');await assert.rejects(f.client.flush(),/DRAFT_CONFLICT/);
    f.clock.fire();await tick();await assert.rejects(f.client.flush());assert.equal(saves,1);assert.equal(f.client.view.draft,'保留输入');assert.doesNotMatch(JSON.stringify(f.client.view),/PRIVATE SECRET/);
  }
});

test('ask flushes first and late model results preserve later typing; close flush does not wait for the model',async()=>{
  const model=deferred();const f=fixture({ask:body=>{f.calls.push({kind:'ask',body});return model.promise;}});await f.client.start();f.client.edit('原问题');
  const asking=f.client.ask({operationId:'original',text:'原问题',providerProfileRef:profile});await tick();assert.deepEqual(f.calls.map(row=>row.kind),['save','ask']);
  f.client.edit('模型运行时继续输入');await f.client.close();assert.equal(f.calls.at(-1).kind,'save');assert.equal(f.state.draft.text,'模型运行时继续输入');
  const count=f.updates.length;model.resolve({operation_id:'original',status:'RETURNED',text:'原问题答复'});await asking;assert.equal(f.updates.length,count,'closed client ignores late model UI update');
});

test('a known old-draft clear from ask preserves newer unsaved input without reporting a false CAS conflict',async()=>{
  const model=deferred();const f=fixture({ask:body=>{f.calls.push({kind:'ask',body});return model.promise;}});await f.client.start();f.client.edit('原问题');
  const asking=f.client.ask({operationId:'original',text:'原问题',providerProfileRef:profile});await tick();f.client.edit('300ms内的新输入');
  f.emit({...f.state,revision:2,draft:{text:'',revision:2}});assert.equal(f.client.view.draft,'300ms内的新输入');assert.equal(f.client.view.error,undefined);
  model.resolve({operation_id:'original',status:'RETURNED',text:'答复'});await asking;await f.client.flush();assert.equal(f.state.draft.text,'300ms内的新输入');assert.equal(f.calls.at(-1).body.expectedRevision,2);
});

test('same operation is never re-asked after unknown and a restart-snapshot unknown uses only explicit original inspect',async()=>{
  let asks=0,inspects=0;const f=fixture({ask:async body=>{asks++;return {operation_id:body.operationId,status:'UNKNOWN'};},inspect:async id=>{inspects++;return {operation_id:id,status:'RETURNED',text:'原GET答复'};}});
  await f.client.start();f.client.edit('原问题');const body={operationId:'original',text:'原问题',providerProfileRef:profile};assert.equal((await f.client.ask(body)).status,'UNKNOWN');
  assert.equal((await f.client.ask(body)).status,'UNKNOWN');assert.equal(asks,1);await f.client.inspect('original');assert.equal(asks,1);assert.equal(inspects,1);
  const g=fixture();await g.client.start();g.emit({...snapshot(2,'原问题',1),operations:[operation('old')],messages:[{id:'old_user',role:'user',operation_id:'old',text:'原问题',provider_profile_ref:profile},{id:'old_assistant',role:'assistant',operation_id:'old',text:'未确认',status:'UNKNOWN'}]});
  assert.equal((await g.client.ask({operationId:'old',text:'原问题',providerProfileRef:profile})).status,'UNKNOWN');assert.equal(g.calls.filter(row=>row.kind==='ask').length,0);
  await assert.rejects(g.client.ask({operationId:'renamed',text:'原问题',providerProfileRef:profile}),/REPLAY_BLOCKED/);
});

test('a different explicit question remains possible while an older unknown is retained',async()=>{
  const f=fixture();await f.client.start();f.emit({...snapshot(2),operations:[operation('old')],messages:[{id:'old_user',role:'user',operation_id:'old',text:'旧问题',provider_profile_ref:profile},{id:'old_answer',role:'assistant',operation_id:'old',text:'旧结果待核',status:'UNKNOWN'}]});
  f.client.edit('不同新问题');const result=await f.client.ask({operationId:'new',text:'不同新问题',providerProfileRef:profile});assert.equal(result.status,'RETURNED');assert.equal(f.calls.filter(row=>row.kind==='ask').length,1);assert.ok(f.client.view.snapshot.operations.some(row=>row.operation_id==='old'));
});

test('local status persistence performs no model ask, and close failure preserves an editable client',async()=>{
  const f=fixture();await f.client.start();f.client.edit('任务进度');await f.client.persistLocal({question:'任务进度',answer:{text:'2项运行中'}});
  assert.equal(f.client.view.draft,'');assert.equal(f.calls.filter(row=>row.kind==='ask').length,0);assert.deepEqual(f.calls.map(row=>row.kind),['save','local']);
  let broken=true;f.bridge.saveDraft=async body=>{if(broken)throw Error('offline');return {...f.state,revision:3,draft:{text:body.text,revision:body.expectedRevision+1}};};
  f.client.edit('不能丢失');await assert.rejects(f.client.close());f.client.edit('仍然能编辑');assert.equal(f.client.view.draft,'仍然能编辑');broken=false;
});

test('malformed snapshot and foreign-operation receipt preserve valid state and cannot route inspection to another operation',async()=>{
  const f=fixture({ask:async()=>({operation_id:'foreign',status:'RETURNED',text:'FOREIGN'})});await f.client.start();f.emit({...snapshot(1),conversation_id:'foreign'});
  assert.equal(f.client.view.snapshot.conversation_id,'global_conversation');assert.match(f.client.view.error.code,/CONVERSATION_CHANGED/);
  assert.throws(()=>supervisorClientSnapshot({...snapshot(),messages:[{id:'bad',role:'tool',text:'secret'}]}));
  const g=fixture({ask:async()=>({operation_id:'foreign',status:'RETURNED',text:'FOREIGN'})});await g.client.start();g.client.edit('问题');const result=await g.client.ask({operationId:'original',text:'问题',providerProfileRef:profile});
  assert.equal(result.operation_id,'original');assert.equal(result.status,'UNKNOWN');assert.doesNotMatch(JSON.stringify(g.client.view),/FOREIGN/);assert.throws(()=>g.client.inspect('foreign'),/OPERATION_NOT_FOUND/);
});

test('real production host store and renderer client retain a new draft across claim, late result, reload and local answer',async t=>{
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-supervisor-client-real-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
  const model=deferred(),calls=[];let listener,body;
  const host=new SupervisorConversation({dataDir,onChange:value=>listener?.(value),request:async(route,input)=>{
    calls.push({route,input});if(route==='/api/supervisor')return {observer:{status:'READY',last_success_at:stamp},tasks:[]};
    if(input){body=input;await model.promise;}return {operation_id:body.operation_id,status:'RETURNED',text:'真实store回执',project_id:null,conversation_id:body.conversation_id,context_ref:null,file_source_ref:null,file_link_ref:null,provider_profile_ref:profile,request_sha256:digest(body),automatic_retry:false};
  }});
  const updates=[],clock=fakeClock(),bridge={getSnapshot:async()=>host.snapshot(),saveDraft:async value=>host.saveDraft(value),ask:value=>host.ask(value),inspect:id=>host.inspect(id),persistLocal:async value=>host.persistLocal(value),onChange:fn=>{listener=fn;return()=>listener=undefined;}};
  const client=new SupervisorConversationClient(bridge,value=>updates.push(value),{clock});await client.start();client.edit('全局问题');const asking=client.ask({operationId:'real_original',text:'全局问题',providerProfileRef:profile});await tick();
  client.edit('等待模型时的新字');model.resolve();await asking;assert.equal(client.view.draft,'等待模型时的新字');assert.equal(client.view.error,undefined);await client.flush();await client.close();
  assert.equal(calls.filter(row=>row.input).length,1);const restored=new SupervisorConversation({dataDir,request:async()=>{throw Error('must not auto run');}});
  assert.equal(restored.snapshot().draft.text,'等待模型时的新字');assert.equal(restored.snapshot().messages[0].text,'全局问题');assert.equal(restored.snapshot().messages[1].text,'真实store回执');
});

test('late save ACK cannot downgrade newer messages or silently overwrite an external newer draft',async()=>{
  const gate=deferred();let f;f=fixture({saveDraft:async body=>{const own=snapshot(1,body.text,1);f.emit(own);await gate.promise;return own;}});
  await f.client.start();f.client.edit('第一次保存');f.clock.fire();await tick();f.client.edit('本地第二稿');
  f.emit(snapshot(3,'另一窗口第三稿',2));gate.resolve();await assert.rejects(f.client.flush(),/DRAFT_CONFLICT/);
  assert.equal(f.client.view.snapshot.revision,3);assert.equal(f.client.view.snapshot.draft.text,'另一窗口第三稿');assert.equal(f.client.view.draft,'本地第二稿');
});

test('late read rejection after a newer event is ignored, and same draft revision with different text is rejected',async()=>{
  const gate=deferred();const f=fixture({getSnapshot:()=>gate.promise});const pending=f.client.start();f.emit(snapshot(4,'新观察',2));gate.reject(Error('old read failed'));await pending;
  assert.equal(f.client.view.error,undefined);assert.equal(f.client.view.draft,'新观察');f.emit(snapshot(5,'没有版本的篡改',2));
  assert.equal(f.client.view.snapshot.revision,4);assert.equal(f.client.view.draft,'新观察');assert.match(f.client.view.error.code,/DRAFT_REVISION_INVALID/);
});

test('unconfirmed send without a store broadcast still blocks the same question under a new operation key',async()=>{
  let asks=0;const f=fixture({ask:async()=>{asks++;throw Error('IPC response lost');}});await f.client.start();f.client.edit('原问题');
  assert.equal((await f.client.ask({operationId:'uncertain',text:'原问题',providerProfileRef:profile})).status,'UNKNOWN');
  await assert.rejects(f.client.ask({operationId:'renamed',text:'原问题',providerProfileRef:profile}),/REPLAY_BLOCKED/);assert.equal(asks,1);
  f.client.edit('不同问题');assert.equal((await f.client.ask({operationId:'different',text:'不同问题',providerProfileRef:profile})).status,'UNKNOWN');assert.equal(asks,2);
});

test('read-only admission finds in-memory UNKNOWN absent from snapshot without saves, timers or new operations',async()=>{
 const f=fixture({ask:async()=>{throw Error('lost callback with no store event');}});await f.client.start();f.client.edit('原问题');
 assert.equal((await f.client.ask({operationId:'original_unknown',text:'原问题',providerProfileRef:profile})).status,'UNKNOWN');
 assert.equal(f.client.view.snapshot.operations.length,0);const before=clone(f.client.view),calls=f.calls.length,timers=f.clock.timers.size;
 for(let i=0;i<4;i++)assert.deepEqual(f.client.askAdmission('原问题',profile),{allowed:false,code:'SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED',operation_id:'original_unknown'});
 assert.deepEqual(f.client.view,before);assert.equal(f.calls.length,calls);assert.equal(f.clock.timers.size,timers);
 assert.deepEqual(f.client.askAdmission('不同新问题',profile),{allowed:true});assert.deepEqual(f.client.askAdmission('原问题',{...profile,revision:2}),{allowed:true});
 await f.client.inspect('original_unknown');assert.deepEqual(f.client.askAdmission('原问题',profile),{allowed:true});
});

test('admission preserves pending and original-key idempotency while snapshot-only UNKNOWN uses the exact public profile',async()=>{
 const gate=deferred();const f=fixture({ask:body=>gate.promise});await f.client.start();f.client.edit('运行中的问题');
 const body={operationId:'pending_original',text:'运行中的问题',providerProfileRef:profile};const pending=f.client.ask(body);await tick();
 assert.deepEqual(f.client.askAdmission('任意另一个问题',profile),{allowed:false,code:'SUPERVISOR_OPERATION_PENDING',operation_id:'pending_original'});
 assert.equal(f.client.ask(body),pending);gate.resolve({operation_id:'pending_original',status:'RETURNED',text:'原回执'});await pending;
 const g=fixture();await g.client.start();g.emit({...snapshot(2,'原问题',1),operations:[operation('restart_unknown')],messages:[{id:'u',role:'user',operation_id:'restart_unknown',text:'原问题',provider_profile_ref:profile}]});
 assert.deepEqual(g.client.askAdmission('原问题',profile),{allowed:false,code:'SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED',operation_id:'restart_unknown'});
 assert.deepEqual(g.client.askAdmission('不同新问题',profile),{allowed:true});assert.deepEqual(g.client.askAdmission('原问题',{...profile,sha256:'b'.repeat(64)}),{allowed:true});
 assert.equal((await g.client.ask({operationId:'restart_unknown',text:'原问题',providerProfileRef:profile})).status,'UNKNOWN');assert.equal(g.calls.length,0);
});

test('processing receipt without snapshot remains pending until its exact observed terminal state supersedes memory',async()=>{
 for(const includeQuestion of [true,false]){
  const f=fixture({ask:async body=>({operation_id:body.operationId,status:'PROCESSING',request_sha256:sha})});await f.client.start();f.client.edit('原运行问题');
  const body={operationId:'processing_original',text:'原运行问题',providerProfileRef:profile};assert.equal((await f.client.ask(body)).status,'PROCESSING');
  assert.deepEqual(f.client.askAdmission('任意新问题',{...profile,revision:2}),{allowed:false,code:'SUPERVISOR_OPERATION_PENDING',operation_id:'processing_original'});
  await assert.rejects(f.client.ask({operationId:'blocked_next',text:'任意新问题',providerProfileRef:profile}),/SUPERVISOR_OPERATION_PENDING/);
  const observed={...f.state,revision:f.state.revision+1,operations:[operation('processing_original','RETURNED')],messages:includeQuestion?[{id:'u',role:'user',operation_id:'processing_original',text:body.text,provider_profile_ref:profile},{id:'a',role:'assistant',operation_id:'processing_original',text:'原终态',status:'RETURNED'}]:[]};f.emit(observed);
  const calls=f.calls.length;assert.deepEqual(f.client.askAdmission('任意新问题',profile),{allowed:true});assert.equal(f.calls.length,calls);assert.equal((await f.client.ask(body)).status,'RETURNED','original key adopts the exact observed terminal without POST');
 }
});

test('observed original terminal clears only matching memory UNKNOWN and does not grant a foreign question identity',async()=>{
 const f=fixture({ask:async body=>({operation_id:body.operationId,status:'UNKNOWN'})});await f.client.start();f.client.edit('原问题');await f.client.ask({operationId:'old_unknown',text:'原问题',providerProfileRef:profile});
 const state={...f.state,revision:f.state.revision+1,operations:[operation('old_unknown','RETURNED')],messages:[{id:'u',role:'user',operation_id:'old_unknown',text:'另一问题',provider_profile_ref:profile}]};f.emit(state);
 assert.equal(f.client.askAdmission('原问题',profile).allowed,false);
 f.emit({...state,revision:state.revision+1,messages:[{...state.messages[0],text:'原问题'}]});assert.deepEqual(f.client.askAdmission('原问题',profile),{allowed:true});
});

test('a confirmed original terminal receipt is not blocked by its older UNKNOWN or pending snapshot',async()=>{
 for(const staleStatus of ['UNKNOWN','PROCESSING']){
  let f;f=fixture({ask:async body=>{f.emit({...f.state,revision:f.state.revision+1,operations:[operation(body.operationId,staleStatus)],messages:[{id:'u',role:'user',operation_id:body.operationId,text:body.text,provider_profile_ref:body.providerProfileRef}]});return {operation_id:body.operationId,status:'RETURNED',text:'原终态'};}});
  await f.client.start();f.client.edit('原问题');const body={operationId:'terminal_original',text:'原问题',providerProfileRef:profile};await f.client.ask(body);
  const before=clone(f.client.view),calls=f.calls.length;assert.deepEqual(f.client.askAdmission('原问题',profile),{allowed:true});assert.deepEqual(f.client.askAdmission('新问题',profile),{allowed:true});assert.deepEqual(f.client.view,before);assert.equal(f.calls.length,calls);assert.equal((await f.client.ask(body)).status,'RETURNED');
 }
});

test('a settled original operation cannot release another pending or same-input unknown operation',async()=>{
 const f=fixture();await f.client.start();f.client.edit('原问题');await f.client.ask({operationId:'settled',text:'原问题',providerProfileRef:profile});
 const own={id:'own_user',role:'user',operation_id:'settled',text:'原问题',provider_profile_ref:profile},other={id:'other_user',role:'user',operation_id:'other',text:'原问题',provider_profile_ref:profile};
 f.emit({...f.state,revision:f.state.revision+1,operations:[operation('settled'),operation('other','PROCESSING')],messages:[own,other]});
 assert.deepEqual(f.client.askAdmission('任意问题',profile),{allowed:false,code:'SUPERVISOR_OPERATION_PENDING',operation_id:'other'});
 f.emit({...f.state,revision:f.state.revision+1,operations:[operation('settled'),operation('other')],messages:[own,other]});
 assert.deepEqual(f.client.askAdmission('原问题',profile),{allowed:false,code:'SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED',operation_id:'other'});assert.deepEqual(f.client.askAdmission('不同问题',profile),{allowed:true});
});

test('closing with no dirty draft does not wait for a pending initial read',async()=>{
  const gate=deferred();const f=fixture({getSnapshot:()=>gate.promise});const loading=f.client.start();await f.client.close();
  const count=f.updates.length;gate.resolve(snapshot(1,'晚到读取',1));await loading;assert.equal(f.updates.length,count);
});

test('serialized Electron CAS error refreshes only the current snapshot and retains local draft for explicit user resolution',async()=>{
  let remote=snapshot(),broken=true,reads=0,saves=0;
  const f=fixture({getSnapshot:async()=>{reads++;return clone(remote);},saveDraft:async body=>{saves++;if(broken)throw Error("Error invoking remote method 'eeas:supervisor-draft-save': Error: SUPERVISOR_DRAFT_CONFLICT");assert.equal(body.expectedRevision,remote.draft.revision);remote=snapshot(remote.revision+1,body.text,remote.draft.revision+1);return clone(remote);}});
  await f.client.start();f.client.edit('本地绝不丢失');remote=snapshot(2,'另一表面的新稿',1);
  await assert.rejects(f.client.flush(),/SUPERVISOR_DRAFT_CONFLICT/);assert.equal(saves,1);assert.equal(reads,2,'one read-only recovery after failed CAS');
  assert.equal(f.client.view.draft,'本地绝不丢失');assert.equal(f.client.view.snapshot.draft.text,'另一表面的新稿');assert.equal(f.client.view.draftConflict,true);
  f.clock.fire();await tick();await assert.rejects(f.client.flush());assert.equal(saves,1,'no automatic CAS replay');
  f.client.resolveDraftConflict('keep-local');broken=false;await f.client.flush();assert.equal(saves,2);assert.equal(remote.draft.text,'本地绝不丢失');
});

test('Electron error extraction accepts only exact known method/code wrappers and never echoes private error content',()=>{
  const fallback='SAFE_UNKNOWN';
  assert.equal(supervisorClientErrorCode(Error("Error invoking remote method 'eeas:supervisor-draft-save': Error: SUPERVISOR_STORAGE_FULL"),fallback),'SUPERVISOR_STORAGE_FULL');
  for(const message of ["Error invoking remote method 'foreign-method': Error: SUPERVISOR_DRAFT_CONFLICT","prefix SUPERVISOR_DRAFT_CONFLICT suffix", "Error invoking remote method 'eeas:supervisor-draft-save': Error: SUPERVISOR_DRAFT_CONFLICT SECRET", "Error invoking remote method 'eeas:supervisor-draft-save': Error: UNKNOWN_PRIVATE_TOKEN"])
    assert.equal(supervisorClientErrorCode(Error(message),fallback),fallback);
});

test('clean draft flush performs no IPC and does not wait for an unrelated initial snapshot read',async()=>{
  const gate=deferred();let reads=0;const f=fixture({getSnapshot:()=>{reads++;return gate.promise;}});const loading=f.client.start();
  await f.client.flush();assert.equal(reads,1);assert.equal(f.calls.length,0);gate.resolve(snapshot());await loading;
});

test('local answer holds draft saves until its exact clear receipt, then saves newer typing with the new basis',async()=>{
  const gate=deferred();let f;f=fixture({persistLocal:async body=>{
    f.calls.push({kind:'local',body});await gate.promise;
    const next={...f.state,revision:f.state.revision+1,draft:{text:'',revision:f.state.draft.revision+1}};f.emit(next);return clone(next);
  }});
  await f.client.start();f.client.edit('任务进度');const saving=f.client.persistLocal({question:'任务进度',answer:'真实状态摘要'});await tick();
  f.client.edit('本地状态保存期间的新稿');f.clock.fire();await tick();
  assert.equal(f.calls.filter(row=>row.kind==='save').length,1,'new draft CAS must wait for the local clear mutation');
  gate.resolve();await saving;await f.client.flush();
  assert.equal(f.client.view.error,undefined);assert.equal(f.client.view.draft,'本地状态保存期间的新稿');assert.equal(f.client.view.dirty,false);
  assert.deepEqual(f.calls.filter(row=>row.kind==='save').map(row=>row.body),[{text:'任务进度',expectedRevision:0},{text:'本地状态保存期间的新稿',expectedRevision:2}]);
});

test('local mutation does not hide a real external draft conflict or automatically overwrite it',async()=>{
  const gate=deferred();let f;f=fixture({persistLocal:async body=>{f.calls.push({kind:'local',body});await gate.promise;const next={...f.state,revision:f.state.revision+1};f.emit(next);return clone(next);}});
  await f.client.start();f.client.edit('任务进度');const saving=f.client.persistLocal({question:'任务进度',answer:'状态'});await tick();f.client.edit('保留本地新稿');
  f.emit(snapshot(2,'另一表面独立写入的新稿',2));gate.resolve();await saving;f.clock.fire();await tick();
  await assert.rejects(f.client.flush(),/DRAFT_CONFLICT/);assert.equal(f.client.view.draft,'保留本地新稿');assert.equal(f.client.view.snapshot.draft.text,'另一表面独立写入的新稿');
  assert.equal(f.calls.filter(row=>row.kind==='save').length,1,'real conflict never automatically retries CAS');
});

test('lost local mutation receipt retains new input and never retries either local mutation or draft write',async()=>{
  const gate=deferred();let f;f=fixture({persistLocal:async body=>{f.calls.push({kind:'local',body});await gate.promise;throw Error('private transport failure');}});
  await f.client.start();f.client.edit('任务进度');const saving=f.client.persistLocal({question:'任务进度',answer:'状态'});await tick();f.client.edit('保存结果未知时的新稿');f.clock.fire();await tick();
  gate.resolve();await assert.rejects(saving);f.clock.fire();await tick();await assert.rejects(f.client.flush(),/LOCAL_SAVE_UNCONFIRMED/);
  assert.equal(f.client.view.draft,'保存结果未知时的新稿');assert.equal(f.calls.filter(row=>row.kind==='local').length,1);assert.equal(f.calls.filter(row=>row.kind==='save').length,1);
  assert.doesNotMatch(JSON.stringify(f.client.view),/private transport failure/);
});

test('actual host clear before delayed local ACK holds newer draft and close only flushes that dependent draft',async t=>{
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-supervisor-local-lease-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
  const gate=deferred(),clock=fakeClock(),calls=[];let listener;
  const host=new SupervisorConversation({dataDir,onChange:value=>listener?.(value),request:async()=>{throw Error('local status must not use model');}});
  const bridge={getSnapshot:async()=>host.snapshot(),saveDraft:async body=>{calls.push({kind:'save',body:clone(body)});return host.saveDraft(body);},ask:async()=>{throw Error('must not ask');},inspect:async()=>{throw Error('must not inspect');},
    persistLocal:async body=>{calls.push({kind:'local'});const value=host.persistLocal(body);await gate.promise;return value;},onChange:fn=>{listener=fn;return()=>listener=undefined;}};
  const client=new SupervisorConversationClient(bridge,()=>{},{clock});await client.start();client.edit('任务进度');const local=client.persistLocal({question:'任务进度',answer:'来自现有状态'});await tick();
  client.edit('原生关闭前的新稿');clock.fire();await tick();assert.equal(calls.filter(row=>row.kind==='save').length,1);let closed=false;const closing=client.close().then(()=>closed=true);await tick();assert.equal(closed,false,'close waits only for the local draft dependency');
  gate.resolve();await local;await closing;assert.equal(closed,true);assert.equal(host.snapshot().draft.text,'原生关闭前的新稿');assert.equal(host.snapshot().messages[0].text,'任务进度');
  assert.deepEqual(calls.filter(row=>row.kind==='save').map(row=>row.body.expectedRevision),[0,2]);assert.equal(client.view.error,undefined);await host.close();
});

test('follow-up reference is exact, bound to a known original operation and never changes the user text',async()=>{
 const f=fixture();await f.client.start();f.emit({...snapshot(1),operations:[operation('original','RETURNED')]});
 const followupRef={operation_id:'original',request_sha256:sha},text='  Keep my exact question\n继续分析  ';f.client.edit(text);
 const body={operationId:'followup',text,providerProfileRef:profile,followupRef};await f.client.ask(body);
 assert.deepEqual(f.calls.find(row=>row.kind==='ask').body,body);assert.equal((await f.client.ask({...body,followupRef:{...followupRef}})).status,'RETURNED');assert.equal(f.calls.filter(row=>row.kind==='ask').length,1);
 assert.throws(()=>f.client.ask({...body,followupRef:undefined}),/OPERATION_MISMATCH/);assert.throws(()=>f.client.ask({...body,followupRef:{...followupRef,request_sha256:'b'.repeat(64)}}),/OPERATION_MISMATCH/);
});
test('invalid or missing follow-up target is rejected before draft CAS or model dispatch',async()=>{
 const f=fixture();await f.client.start();f.client.edit('Never lose this draft');const body={operationId:'followup',text:'Never lose this draft',providerProfileRef:profile};
 for(const followupRef of [null,{},[],{operation_id:'original',request_sha256:sha,extra:true},{operation_id:'original',request_sha256:'bad'},{operation_id:null,request_sha256:sha}])assert.throws(()=>f.client.ask({...body,followupRef}),/FOLLOWUP_INVALID/);
 await assert.rejects(f.client.ask({...body,followupRef:{operation_id:'absent',request_sha256:sha}}),/FOLLOWUP_CHANGED/);assert.equal(f.calls.length,0);assert.equal(f.client.view.draft,body.text);
});
test('follow-up target is checked again after a draft flush and cannot drift to another digest',async()=>{
 const gate=deferred();let f;f=fixture({saveDraft:async body=>{f.calls.push({kind:'save',body});await gate.promise;const next={...f.state,revision:f.state.revision+1,draft:{text:body.text,revision:body.expectedRevision+1},operations:[{...operation('original','RETURNED'),request_sha256:'b'.repeat(64)}]};f.emit(next);return next;}});
 await f.client.start();f.emit({...snapshot(1),operations:[operation('original','RETURNED')]});f.client.edit('New follow-up');const pending=f.client.ask({operationId:'followup',text:'New follow-up',providerProfileRef:profile,followupRef:{operation_id:'original',request_sha256:sha}});await tick();gate.resolve();await assert.rejects(pending,/FOLLOWUP_CHANGED/);assert.equal(f.calls.filter(row=>row.kind==='ask').length,0);assert.equal(f.client.view.draft,'New follow-up');
});
test('persisted operation identity includes its follow-up reference after renderer restart',async()=>{
 const f=fixture();await f.client.start();const followupRef={operation_id:'original',request_sha256:sha},body={operationId:'followup',text:'Same exact follow-up',providerProfileRef:profile,followupRef};
 f.emit({...snapshot(1),operations:[operation('original','RETURNED'),{...operation('followup','RETURNED'),followup_ref:followupRef}],messages:[{id:'followup_user',role:'user',operation_id:'followup',text:body.text,provider_profile_ref:profile},{id:'followup_answer',role:'assistant',operation_id:'followup',status:'RETURNED',text:'Persisted original reply'}]});
 assert.equal((await f.client.ask(body)).text,'Persisted original reply');assert.equal(f.calls.length,0);assert.throws(()=>f.client.ask({...body,followupRef:undefined}),/OPERATION_MISMATCH/);
 assert.deepEqual(f.client.view.snapshot.operations[1].followup_ref,followupRef);
});
test('changing follow-up reference does not bypass UNKNOWN same-text same-profile protection',async()=>{
 let asks=0;const f=fixture({ask:async body=>{asks++;return{operation_id:body.operationId,status:'UNKNOWN'};}});await f.client.start();f.emit({...snapshot(1),operations:[operation('original','RETURNED'),operation('another','RETURNED')]});
 const body={operationId:'unknown_followup',text:'The unchanged user question',providerProfileRef:profile,followupRef:{operation_id:'original',request_sha256:sha}};assert.equal((await f.client.ask(body)).status,'UNKNOWN');
 await assert.rejects(f.client.ask({...body,operationId:'new_key',followupRef:{operation_id:'another',request_sha256:sha}}),/REPLAY_BLOCKED/);assert.equal(asks,1,'changing reference cannot dispatch a second model ask');
});


test('storage-full save is not reported as a foreign draft and retains the write/close barrier',async()=>{
 let saves=0;const f=fixture({saveDraft:async()=>{saves++;throw Error("Error invoking remote method 'eeas:supervisor-draft-save': Error: SUPERVISOR_STORAGE_FULL");}});
 await f.client.start();f.client.edit('不能丢失的草稿');await assert.rejects(f.client.flush(),/SUPERVISOR_STORAGE_FULL/);
 assert.equal(f.client.view.draftConflict,false,'storage failure is not a CAS conflict');assert.equal(f.client.view.draft,'不能丢失的草稿');
 f.client.edit('失败后继续写的新稿');f.clock.fire();await tick();await assert.rejects(f.client.close(),/SUPERVISOR_STORAGE_FULL/);assert.equal(saves,1);assert.equal(f.client.view.draft,'失败后继续写的新稿');
});

test('draft failure classification separates real CAS from capacity limits and uncertain storage',()=>{
 assert.equal(supervisorDraftFailureKind('SUPERVISOR_DRAFT_CONFLICT'),'CONFLICT');assert.equal(supervisorDraftFailureKind('SUPERVISOR_CLIENT_DRAFT_CONFLICT'),'CONFLICT');
 assert.equal(supervisorDraftFailureKind('SUPERVISOR_STORAGE_FULL'),'FAILED');
 for(const code of ['SUPERVISOR_STORAGE_CONFLICT','SUPERVISOR_CLIENT_DRAFT_SAVE_UNKNOWN','SUPERVISOR_CLIENT_SAVE_RECEIPT_MISMATCH','SUPERVISOR_CLIENT_DRAFT_RECOVERY_READ_UNAVAILABLE'])assert.equal(supervisorDraftFailureKind(code),'UNKNOWN');
 assert.equal(supervisorDraftFailureKind('PRIVATE TOKEN'),undefined);
});
test('confirmed capacity rejection needs an explicit current-basis GET then a separate guarded retry',async()=>{
 const f=fixture();let broken=true,saves=0,reads=0;const save=f.bridge.saveDraft,get=f.bridge.getSnapshot;
 f.bridge.saveDraft=async body=>{saves++;if(broken)throw Object.assign(Error('capacity'),{code:'SUPERVISOR_STORAGE_FULL'});return save(body);};f.bridge.getSnapshot=async()=>{reads++;return get();};
 await f.client.start();f.client.edit('最初的稿');await assert.rejects(f.client.flush(),/STORAGE_FULL/);assert.equal(f.client.view.draftBlocked,true);assert.equal(f.client.draftSaveRecovery(),'CHECK_REQUIRED');
 assert.throws(()=>f.client.retryDraftSave(),/RECOVERY_REQUIRED/);assert.throws(()=>f.client.resolveDraftConflict('keep-local'),/RECOVERY_REQUIRED/);
 f.client.edit('失败后继续写的新稿');const before=reads;await f.client.inspectDraftSave();assert.equal(reads,before+1);assert.equal(saves,1);assert.equal(f.client.draftSaveRecovery(),'RETRY_ALLOWED');assert.equal(f.client.view.draftBlocked,true);f.clock.fire();await tick();assert.equal(saves,1);
 broken=false;await f.client.retryDraftSave();assert.equal(saves,2);assert.deepEqual(f.calls.filter(r=>r.kind==='save').at(-1).body,{text:'失败后继续写的新稿',expectedRevision:0});assert.equal(f.client.view.dirty,false);assert.equal(f.client.view.draftBlocked,false);await f.client.close();
});
test('unknown save that is confirmed at its exact revision converges without replay and retains newer input for a separate save',async()=>{
 const f=fixture();let first=true,saves=0;const save=f.bridge.saveDraft;f.bridge.saveDraft=async body=>{saves++;const value=await save(body);if(first){first=false;throw Error('lost save receipt');}return value;};
 await f.client.start();f.client.edit('原保存');await assert.rejects(f.client.flush(),/SAVE_UNKNOWN/);assert.equal(saves,1);assert.equal(f.client.view.draftConflict,false);assert.equal(f.client.view.draftBlocked,true);
 f.client.edit('保存未知后写的新稿');await f.client.inspectDraftSave();assert.equal(saves,1);assert.equal(f.client.view.draft,'保存未知后写的新稿');assert.equal(f.client.view.dirty,true);assert.equal(f.client.view.draftBlocked,false);assert.equal(f.clock.timers.size,0,'inspection itself never schedules writes');
 await f.client.close();assert.equal(saves,2);assert.equal(f.state.draft.text,'保存未知后写的新稿');assert.equal(f.calls.filter(r=>r.kind==='ask').length,0);
});
test('unknown save with unchanged basis cannot be retried or disguised as keep-local',async()=>{
 let saves=0;const f=fixture({saveDraft:async()=>{saves++;throw Error('transport unknown');}});await f.client.start();f.client.edit('未知保存');await assert.rejects(f.client.flush(),/SAVE_UNKNOWN/);
 await f.client.inspectDraftSave();assert.equal(f.client.draftSaveRecovery(),'CHECK_REQUIRED');assert.equal(f.client.view.draftConflict,false);assert.equal(f.client.view.draftBlocked,true);
 assert.throws(()=>f.client.retryDraftSave(),/RECOVERY_REQUIRED/);assert.throws(()=>f.client.resolveDraftConflict('keep-local'),/RECOVERY_REQUIRED/);assert.throws(()=>f.client.resolveDraftConflict('use-remote'),/RECOVERY_REQUIRED/);
 f.client.edit('未知期间继续输入');f.clock.fire();await tick();await assert.rejects(f.client.close(),/SAVE_UNKNOWN/);assert.equal(saves,1);assert.equal(f.client.view.draft,'未知期间继续输入');
});
test('remote change after checked rejection turns into real CAS and the old retry cannot overwrite it',async()=>{
 let saves=0;const f=fixture({saveDraft:async()=>{saves++;throw Object.assign(Error('capacity'),{code:'SUPERVISOR_STORAGE_FULL'});}});await f.client.start();f.client.edit('本地');await assert.rejects(f.client.flush());await f.client.inspectDraftSave();assert.equal(f.client.draftSaveRecovery(),'RETRY_ALLOWED');
 f.emit(snapshot(2,'另一窗口的新稿',1));assert.equal(f.client.view.draftConflict,true);assert.equal(f.client.view.draftBlocked,true);assert.equal(f.client.draftSaveRecovery(),undefined);assert.throws(()=>f.client.retryDraftSave(),/DRAFT_CONFLICT/);assert.equal(saves,1);assert.equal(f.state.draft.text,'另一窗口的新稿');assert.equal(f.client.view.draft,'本地');
});
test('failed recovery read invalidates prior retry eligibility and preserves both draft and closing barrier',async()=>{
 const f=fixture();let readFails=false;const get=f.bridge.getSnapshot;f.bridge.getSnapshot=async()=>{if(readFails)throw Error('read failed');return get();};f.bridge.saveDraft=async()=>{throw Object.assign(Error('capacity'),{code:'SUPERVISOR_STORAGE_FULL'});};
 await f.client.start();f.client.edit('要保留');await assert.rejects(f.client.flush());await f.client.inspectDraftSave();assert.equal(f.client.draftSaveRecovery(),'RETRY_ALLOWED');readFails=true;
 await f.client.inspectDraftSave();assert.equal(f.client.draftSaveRecovery(),'CHECK_REQUIRED');assert.equal(f.client.view.error.code,'SUPERVISOR_CLIENT_DRAFT_RECOVERY_READ_UNAVAILABLE');assert.equal(f.client.view.draftBlocked,true);assert.equal(f.client.view.draftConflict,false);assert.throws(()=>f.client.retryDraftSave(),/RECOVERY_REQUIRED/);await assert.rejects(f.client.close());assert.equal(f.client.view.draft,'要保留');
});
test('typing during delayed recovery remains visible and duplicate inspect is one read-only request',async()=>{
 const f=fixture();let gate,gets=0,broken=true;const get=f.bridge.getSnapshot,save=f.bridge.saveDraft;f.bridge.getSnapshot=async()=>{gets++;return gate?gate.promise:get();};f.bridge.saveDraft=async body=>{if(broken)throw Object.assign(Error('capacity'),{code:'SUPERVISOR_STORAGE_FULL'});return save(body);};
 await f.client.start();f.client.edit('旧稿');await assert.rejects(f.client.flush());gate=deferred();const before=gets,first=f.client.inspectDraftSave(),second=f.client.inspectDraftSave();assert.equal(first,second);assert.equal(gets,before+1);f.client.edit('核查期间的新稿');gate.resolve(f.state);await first;assert.equal(f.client.view.draft,'核查期间的新稿');assert.equal(f.client.draftSaveRecovery(),'RETRY_ALLOWED');gate=undefined;broken=false;await f.client.retryDraftSave();assert.equal(f.state.draft.text,'核查期间的新稿');
});
test('late recovery GET cannot undo a newer external CAS observation',async()=>{
 const f=fixture();let gate;const get=f.bridge.getSnapshot;f.bridge.getSnapshot=async()=>gate?gate.promise:get();f.bridge.saveDraft=async()=>{throw Object.assign(Error('capacity'),{code:'SUPERVISOR_STORAGE_FULL'});};
 await f.client.start();f.client.edit('本地');await assert.rejects(f.client.flush());gate=deferred();const pending=f.client.inspectDraftSave();f.emit(snapshot(3,'远端新稿',1));gate.resolve(snapshot());await pending;assert.equal(f.client.view.snapshot.revision,3);assert.equal(f.client.view.draftConflict,true);assert.equal(f.client.view.draft,'本地');assert.equal(f.client.view.snapshot.draft.text,'远端新稿');assert.throws(()=>f.client.retryDraftSave(),/DRAFT_CONFLICT/);
});
test('storage integrity conflict is uncertain and cannot use the capacity retry path',async()=>{
 const f=fixture({saveDraft:async()=>{throw Object.assign(Error('integrity'),{code:'SUPERVISOR_STORAGE_CONFLICT'});}});await f.client.start();f.client.edit('原稿');await assert.rejects(f.client.flush(),/STORAGE_CONFLICT/);await f.client.inspectDraftSave();assert.equal(f.client.view.draftConflict,false);assert.equal(f.client.view.draftBlocked,true);assert.equal(f.client.draftSaveRecovery(),'CHECK_REQUIRED');assert.throws(()=>f.client.retryDraftSave(),/RECOVERY_REQUIRED/);
});
test('actual bounded host capacity failure keeps its exact disk and client draft, then explicit smaller retry uses the same basis',async t=>{
 const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-supervisor-full-real-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));let listener,saves=0,requests=0;
 let host=new SupervisorConversation({dataDir,onChange:value=>listener?.(value),request:async()=>{requests++;throw Error('no model or API');}});
 const seeded=clone(host.state);seeded.local=Array.from({length:16},(_,i)=>({id:'bounded_'+i,question:'local '+i,text:'a'.repeat(15000),created_at:stamp}));
 fs.writeFileSync(host.file,JSON.stringify({version:1,state:seeded,sha256:digest(seeded)})+'\n');host=new SupervisorConversation({dataDir,onChange:value=>listener?.(value),request:async()=>{requests++;throw Error('no model or API');}});
 const before=fs.readFileSync(host.file),state=host.snapshot(),clock=fakeClock(),bridge={getSnapshot:async()=>host.snapshot(),saveDraft:async body=>{saves++;return host.saveDraft(body);},ask:async()=>{throw Error('no ask');},inspect:async()=>{throw Error('no inspect');},persistLocal:async body=>host.persistLocal(body),onChange:fn=>{listener=fn;return()=>listener=undefined;}};
 const client=new SupervisorConversationClient(bridge,()=>{},{clock});await client.start();client.edit('中'.repeat(8000));const open=fs.openSync;
 fs.openSync=(file,...rest)=>{if(String(file).includes('supervisor-conversation.archive.v1')&&String(file).endsWith('.tmp'))throw Object.assign(Error('actual archive boundary full'),{code:'ENOSPC'});return open(file,...rest);};
 try{await assert.rejects(client.flush(),/SUPERVISOR_STORAGE_FULL/);}finally{fs.openSync=open;}
 assert.deepEqual(fs.readFileSync(host.file),before);assert.equal(host.snapshot().draft.revision,state.draft.revision);assert.equal(client.view.draftConflict,false);assert.equal(client.view.draftBlocked,true);assert.equal(client.view.draft.length,8000);await assert.rejects(client.close());
 await client.inspectDraftSave();assert.equal(client.draftSaveRecovery(),'RETRY_ALLOWED');assert.equal(saves,1);client.edit('短稿');await client.retryDraftSave();assert.equal(saves,2);assert.equal(host.snapshot().draft.text,'短稿');assert.equal(host.snapshot().draft.revision,state.draft.revision+1);assert.equal(requests,0);await client.close();
});

test('confirming an old unknown save never lets quit bypass a failed newer-draft save',async()=>{
 const f=fixture();let saves=0;const save=f.bridge.saveDraft;f.bridge.saveDraft=async body=>{saves++;if(saves===1){await save(body);throw Error('lost old save receipt');}throw Object.assign(Error('capacity'),{code:'SUPERVISOR_STORAGE_FULL'});};
 await f.client.start();f.client.edit('旧保存');await assert.rejects(f.client.flush());f.client.edit('必须另存的新稿');await f.client.inspectDraftSave();assert.equal(f.client.view.dirty,true);assert.equal(f.client.view.draftBlocked,false);
 await assert.rejects(f.client.close(),/STORAGE_FULL/);assert.equal(saves,2);assert.equal(f.client.view.draft,'必须另存的新稿');assert.equal(f.client.view.draftBlocked,true);assert.equal(f.state.draft.text,'旧保存');f.client.edit('关闭受阻后仍可编辑');assert.equal(f.client.view.draft,'关闭受阻后仍可编辑');
});
test('foreign recovery snapshot cannot unlock or keep old retry eligibility',async()=>{
 const f=fixture();let foreign=false;const get=f.bridge.getSnapshot;f.bridge.getSnapshot=async()=>foreign?{...snapshot(),conversation_id:'another_global'}:get();f.bridge.saveDraft=async()=>{throw Object.assign(Error('capacity'),{code:'SUPERVISOR_STORAGE_FULL'});};
 await f.client.start();f.client.edit('当前总管稿');await assert.rejects(f.client.flush());await f.client.inspectDraftSave();assert.equal(f.client.draftSaveRecovery(),'RETRY_ALLOWED');foreign=true;await f.client.inspectDraftSave();assert.equal(f.client.draftSaveRecovery(),'CHECK_REQUIRED');assert.equal(f.client.view.snapshot.conversation_id,'global_conversation');assert.equal(f.client.view.draftBlocked,true);assert.throws(()=>f.client.retryDraftSave(),/RECOVERY_REQUIRED/);assert.equal(f.client.view.draft,'当前总管稿');
});

test('same-revision recovery integrity rejection cannot be cleared by matching only the draft fields',async()=>{
 const f=fixture();let tamper=false;const save=f.bridge.saveDraft,get=f.bridge.getSnapshot;f.bridge.saveDraft=async body=>{await save(body);throw Error('unknown save receipt');};f.bridge.getSnapshot=async()=>{const value=await get();return tamper?{...value,messages:[{id:'injected',role:'user',text:'same revision changed body'}]}:value;};
 await f.client.start();f.client.edit('实际保存的原稿');await assert.rejects(f.client.flush(),/SAVE_UNKNOWN/);tamper=true;await f.client.inspectDraftSave();assert.equal(f.client.view.draftBlocked,true);assert.equal(f.client.view.error.code,'SUPERVISOR_CLIENT_REVISION_CONFLICT');assert.equal(f.client.view.snapshot.messages.length,0);assert.equal(f.client.draftSaveRecovery(),'CHECK_REQUIRED');assert.throws(()=>f.client.retryDraftSave(),/RECOVERY_REQUIRED/);
});
