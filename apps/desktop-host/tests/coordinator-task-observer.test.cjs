'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {CoordinatorTaskObserver}=require('../coordinator-task-observer.cjs');
const {SupervisorConversation,supervisorConversationDigest:digest}=require('../supervisor-conversation.cjs');
const profile={id:'profile',revision:1,sha256:'a'.repeat(64)},sha='b'.repeat(64);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const task=(operation='op',requestSha=sha,status='RETURNED',extra={})=>({
  key:{kind:'native_message',project_id:null,object_id:operation,operation_id:operation,generation:7},
  source_status:status,source_ref:{id:operation,revision:1,sha256:digest({request_sha256:requestSha,status})},observation_status:'CURRENT',...extra});
const snapshot=tasks=>({tasks});
function fakeStore(operations,inspect){
  const calls=[];let reads=0;
  return {operations,calls,get reads(){return reads;},snapshot(){reads++;return {operations};},inspect(id){calls.push(id);return inspect?.(id);}};
}
function realStore(t,{postStatus='PROCESSING',get}={}){
  const root=process.env.EEAS_TEST_TMPDIR??require('node:os').tmpdir();
  fs.mkdirSync(root,{recursive:true});const dataDir=fs.mkdtempSync(path.join(root,'observer-'));
  const requests=[],bodies=new Map(),changes=[];
  const receipt=(body,status,text='')=>({operation_id:body.operation_id,project_id:null,conversation_id:body.conversation_id,context_ref:null,
    file_source_ref:null,file_link_ref:null,automatic_retry:false,request_sha256:digest(body),provider_profile_ref:body.provider_profile_ref,status,text});
  const store=new SupervisorConversation({dataDir,onChange:value=>changes.push(value),request:async(url,body)=>{
    requests.push({url,...body?{body:JSON.parse(JSON.stringify(body))}:{}});
    if(url==='/api/supervisor')return {tasks:[],observer:{status:'READY',last_success_at:null}};
    if(url==='/api/desktop-host/native-messages'){bodies.set(body.operation_id,body);return receipt(body,postStatus);}
    const id=url.split('/').at(-1),original=bodies.get(id);assert.ok(original,'GET must use an original operation');
    return get?get({id,body:original,receipt}):receipt(original,'RETURNED','原操作的后台回答😀');
  }});
  t.after(async()=>{await store.close();fs.rmSync(dataDir,{recursive:true,force:true});});
  return {store,requests,changes,receipt,bodies,async ask(id='op'){await store.ask({operationId:id,text:'帮我看看任务状态',providerProfileRef:profile});return store.snapshot().operations.find(row=>row.operation_id===id);}};
}
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};

test('real PROCESSING operation receives its background RETURNED answer through one original GET and store broadcast',async t=>{
  const f=realStore(t),operation=await f.ask(),observer=new CoordinatorTaskObserver({store:f.store});t.after(()=>observer.close());
  const running=freeze(snapshot([task('op',operation.request_sha256,'PROCESSING')]));
  observer.observe(running);await observer.flush();assert.equal(f.requests.filter(row=>!row.body&&row.url.includes('/native-messages/')).length,0);
  const completed=freeze(snapshot([task('op',operation.request_sha256)])),before=JSON.stringify(completed);observer.observe(completed);await observer.flush();
  for(let i=0;i<4;i++)observer.observe(completed);await observer.flush();
  assert.equal(f.requests.filter(row=>row.body).length,1);assert.deepEqual(f.requests.filter(row=>row.url.includes('/native-messages/')).map(row=>row.url),['/api/desktop-host/native-messages/op']);
  assert.equal(f.store.snapshot().operations[0].status,'RETURNED');assert.ok(f.changes.at(-1).messages.some(row=>row.role==='assistant'&&row.text==='原操作的后台回答😀'));
  assert.equal(JSON.stringify(completed),before);
});

test('real UNKNOWN operation is only inspected on a new exact observation, never reposted',async t=>{
  const f=realStore(t,{postStatus:'UNKNOWN_OUTCOME'}),operation=await f.ask();assert.equal(operation.status,'UNKNOWN');
  const observer=new CoordinatorTaskObserver({store:f.store});t.after(()=>observer.close());observer.observe(snapshot([task('op',operation.request_sha256)]));await observer.flush();
  assert.equal(f.store.snapshot().operations[0].status,'RETURNED');assert.equal(f.requests.filter(row=>row.body).length,1);assert.equal(f.requests.filter(row=>row.url.endsWith('/op')).length,1);
});

test('real GET failure preserves UNKNOWN and same basis does not loop; explicit original inspect remains available',async t=>{
  let unavailable=true;
  const f=realStore(t,{get:({body,receipt})=>{if(unavailable)throw Error('local fixture GET unavailable');return receipt(body,'RETURNED','后来核对到原回答');}}),operation=await f.ask();
  const observer=new CoordinatorTaskObserver({store:f.store});t.after(()=>observer.close());const observed=snapshot([task('op',operation.request_sha256)]);
  observer.observe(observed);await observer.flush();assert.equal(f.store.snapshot().operations[0].status,'UNKNOWN');
  observer.observe(observed);await observer.flush();assert.equal(f.requests.filter(row=>row.url.endsWith('/op')).length,1);assert.equal(f.requests.filter(row=>row.body).length,1);
  unavailable=false;await f.store.inspect('op');assert.equal(f.store.snapshot().operations[0].status,'RETURNED');assert.equal(f.requests.filter(row=>row.body).length,1);
});

test('real wrong original GET receipt cannot inject another request answer',async t=>{
  const f=realStore(t,{get:({body,receipt})=>({...receipt(body,'RETURNED','foreign answer'),request_sha256:'c'.repeat(64)})}),operation=await f.ask();
  const observer=new CoordinatorTaskObserver({store:f.store});t.after(()=>observer.close());observer.observe(snapshot([task('op',operation.request_sha256)]));await observer.flush();
  assert.equal(f.store.snapshot().operations[0].status,'UNKNOWN');assert.ok(!f.store.snapshot().messages.some(row=>row.text==='foreign answer'));assert.equal(f.requests.filter(row=>row.body).length,1);
});

test('foreign project, kind, object, source id/revision/hash/status basis, stale and invalid generation never inspect',async()=>{
  const op={operation_id:'op',request_sha256:sha,status:'PROCESSING'},store=fakeStore([op]),observer=new CoordinatorTaskObserver({store});
  const base=task(),rows=[
    task('op',sha,'RETURNED',{key:{...base.key,project_id:'other'}}),task('op',sha,'RETURNED',{key:{...base.key,kind:'planning'}}),
    task('op',sha,'RETURNED',{key:{...base.key,object_id:'other'}}),task('op','c'.repeat(64)),
    task('op',sha,'FAILED',{source_ref:base.source_ref}),task('op',sha,'RETURNED',{source_ref:{...base.source_ref,id:'other'}}),
    task('op',sha,'RETURNED',{source_ref:{...base.source_ref,revision:2}}),task('op',sha,'RETURNED',{source_ref:null}),
    task('op',sha,'RETURNED',{observation_status:'STALE'}),task('op',sha,'RETURNED',{observation_status:'NEEDS_CHECK_AFTER_RESTART'}),
    task('op',sha,'RETURNED',{key:{...base.key,generation:-1}}),task('op',sha,'RETURNED',{key:{...base.key,generation:NaN}}),task('unknown'),
  ];
  observer.observe(freeze(snapshot(rows)));await observer.flush();assert.deepEqual(store.calls,[]);assert.equal(observer.get().tracked,0);await observer.close();
});

test('running sources and already terminal local operations do not perform useless GETs; ownership lookup clones once per snapshot',async()=>{
  const operations=Array.from({length:64},(_,i)=>({operation_id:'op_'+i,request_sha256:sha,status:'RETURNED'})),store=fakeStore(operations),observer=new CoordinatorTaskObserver({store});
  observer.observe(snapshot(operations.map(op=>task(op.operation_id))));assert.equal(store.reads,1);assert.deepEqual(store.calls,[]);
  operations[0].status='PROCESSING';observer.observe(snapshot(['CLAIMED','PROCESSING','REQUESTING','RUNNING','QUEUED','PENDING','ACCEPTED','CANCEL_REQUESTED'].map(status=>task('op_0',sha,status))));
  assert.deepEqual(store.calls,[]);await observer.close();
});

test('two concurrent GETs and bounded 128 operation slots drain without same-operation overlap',async()=>{
  const operations=Array.from({length:128},(_,i)=>({operation_id:'op_'+i,request_sha256:sha,status:'PROCESSING'})),gates=new Map();let active=0,maximum=0;
  const store=fakeStore(operations,id=>{active++;maximum=Math.max(maximum,active);const gate=deferred();gates.set(id,gate);return gate.promise.finally(()=>active--);}),observer=new CoordinatorTaskObserver({store});
  const observed=freeze(snapshot(operations.map(op=>task(op.operation_id))));observer.observe(observed);observer.observe(observed);
  assert.equal(store.calls.length,2);assert.deepEqual(observer.get(),{closed:false,active:2,queued:126,tracked:128});
  for(let i=0;i<128;i++){const id='op_'+i;while(!gates.has(id))await tick();gates.get(id).resolve();}
  await observer.flush();assert.equal(store.calls.length,128);assert.equal(new Set(store.calls).size,128);assert.equal(maximum,2);assert.equal(observer.get().queued,0);await observer.close();
});

test('queued targets are revalidated against the store before GET, including a changed request basis',async()=>{
  const operations=['a','b','c'].map(operation_id=>({operation_id,request_sha256:sha,status:'PROCESSING'})),gates=new Map();
  const store=fakeStore(operations,id=>{const gate=deferred();gates.set(id,gate);return gate.promise;}),observer=new CoordinatorTaskObserver({store});
  observer.observe(snapshot(operations.map(op=>task(op.operation_id))));operations[2].request_sha256='c'.repeat(64);
  gates.get('a').resolve();gates.get('b').resolve();await observer.flush();assert.deepEqual(store.calls,['a','b']);await observer.close();
});

test('same operation merges a later basis during GET and skips a follow-up when that GET reaches a terminal result',async()=>{
  const op={operation_id:'op',request_sha256:sha,status:'PROCESSING'},gate=deferred(),store=fakeStore([op],()=>gate.promise),observer=new CoordinatorTaskObserver({store});
  observer.observe(snapshot([task()]));observer.observe(snapshot([task('op',sha,'FAILED')]));assert.deepEqual(store.calls,['op']);
  op.status='RETURNED';gate.resolve();await observer.flush();assert.deepEqual(store.calls,['op']);await observer.close();
});

test('later basis remains eligible after an in-flight UNKNOWN result, and old basis replay remains deduplicated',async()=>{
  const op={operation_id:'op',request_sha256:sha,status:'UNKNOWN'},gate=deferred();let first=true;
  const store=fakeStore([op],()=>{if(first){first=false;return gate.promise;}}),observer=new CoordinatorTaskObserver({store});
  const original=snapshot([task()]),later=snapshot([task('op',sha,'FAILED')]);observer.observe(original);observer.observe(later);assert.equal(store.calls.length,1);
  gate.resolve();await observer.flush();assert.equal(store.calls.length,2);observer.observe(original);observer.observe(later);await observer.flush();assert.equal(store.calls.length,2);await observer.close();
});

test('close stops new observations, drops queued GETs and joins only the already started requests',async()=>{
  const operations=['a','b','c'].map(operation_id=>({operation_id,request_sha256:sha,status:'UNKNOWN'})),gates=new Map();
  const store=fakeStore(operations,id=>{const gate=deferred();gates.set(id,gate);return gate.promise;}),observer=new CoordinatorTaskObserver({store});
  const observed=snapshot(operations.map(op=>task(op.operation_id)));observer.observe(observed);let closed=false;const closing=observer.close().then(()=>{closed=true;});
  assert.equal(observer.close(),observer.close());observer.observe(observed);await tick();assert.equal(closed,false);assert.deepEqual(store.calls,['a','b']);assert.equal(observer.get().queued,0);
  gates.get('a').resolve();await tick();assert.equal(closed,false);gates.get('b').resolve();await closing;assert.equal(closed,true);assert.deepEqual(store.calls,['a','b']);
});

test('synchronous onChange reentry cannot start a second GET for the same operation',async()=>{
  const op={operation_id:'op',request_sha256:sha,status:'UNKNOWN'},gate=deferred();let observer,first=true;
  const store=fakeStore([op],()=>{if(first){first=false;observer.observe(snapshot([task('op',sha,'FAILED')]));return gate.promise;}});observer=new CoordinatorTaskObserver({store});
  observer.observe(snapshot([task()]));assert.equal(store.calls.length,1);op.status='RETURNED';gate.resolve();await observer.flush();assert.equal(store.calls.length,1);await observer.close();
});

test('synchronous inspect failure is contained once per basis and notification errors do not leak',async()=>{
  const errors=[],store=fakeStore([{operation_id:'op',request_sha256:sha,status:'UNKNOWN'}],()=>{throw Error('fixture inspect failure');}),observer=new CoordinatorTaskObserver({store,onError:error=>{errors.push(error);throw Error('ignored notification error');}});
  observer.observe(snapshot([task()]));await observer.flush();observer.observe(snapshot([task()]));await observer.flush();assert.equal(errors.length,1);assert.equal(store.calls.length,1);assert.equal(observer.get().active,0);await observer.close();
});

test('generation is part of deduplication and automatic observation memory has a bounded per-operation basis budget',async()=>{
  const errors=[],store=fakeStore([{operation_id:'op',request_sha256:sha,status:'UNKNOWN'}]),observer=new CoordinatorTaskObserver({store,onError:error=>errors.push(error.code)});
  for(let generation=0;generation<20;generation++){const value=task();value.key.generation=generation;observer.observe(snapshot([value]));await observer.flush();}
  assert.equal(store.calls.length,16);assert.equal(observer.get().tracked,1);assert.deepEqual(errors,['COORDINATOR_OBSERVER_BASIS_LIMIT']);store.inspect('op');assert.equal(store.calls.length,17,'manual exact GET is unaffected');await observer.close();
});
