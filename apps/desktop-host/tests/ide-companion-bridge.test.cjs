'use strict';
// Actual Node HTTP + filesystem protocol tests. Approval and EEAS business
// dispatch are injected UNIT boundaries, not Windows/user-consent qualification.
const test=require('node:test'),assert=require('node:assert/strict');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {randomBytes,randomUUID}=require('node:crypto');
const {IdeCompanionBridge,PROTOCOL,METHODS,validateScope}=require('../ide-companion-bridge.cjs');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const scope=()=>({project_id:'project1',session_ref:{id:'session1',revision:1,sha256:'a'.repeat(64)},generation:1});
function directory(t){const base=process.env.EEAS_TEST_TMPDIR||os.tmpdir();fs.mkdirSync(base,{recursive:true});
  const d=fs.mkdtempSync(path.join(base,'ide-bridge-'));t.after(()=>fs.rmSync(d,{recursive:true,force:true}));return d;}
function client(){return {client_id:randomUUID(),client_secret:randomBytes(32).toString('hex'),client_label:'VS Code 单元边界'};}
function request(discovery,route,{method='GET',body,credential,headers={},raw}={}){
  return new Promise((resolve,reject)=>{const bytes=raw??(body===undefined?undefined:JSON.stringify(body));
    const req=http.request({hostname:'127.0.0.1',port:discovery.port,path:route,method,headers:{...(bytes===undefined?{}:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(bytes)}),
      ...(credential?{'Authorization':'Bearer '+credential.client_secret,'X-EEAS-Client-Id':credential.client_id}:{}),...headers}},res=>{
      const chunks=[];res.on('data',c=>chunks.push(c));res.on('end',()=>{try{resolve({http:res.statusCode,value:JSON.parse(Buffer.concat(chunks))});}catch(error){reject(error);}});});
    req.on('error',reject);req.setTimeout(3000,()=>req.destroy(Error('actual test HTTP exceeded deadline')));if(bytes!==undefined)req.write(bytes);req.end();});
}
async function fixture(t,options={}){
  const d=options.dataDir||directory(t),calls=[];
  const b=new IdeCompanionBridge({dataDir:d,version:'unit-1',requestTimeoutMs:100,pairingTimeoutMs:100,
    requestPairing:async()=>({approved:true}),dispatch:async(method,params,context)=>{calls.push({method,params,context});return {status:'RETURNED',text:'真实HTTP后的注入业务结果'};},...options});
  t.after(()=>b.close());const discovery=await b.start();return {b,d,discovery,calls};
}
async function pair(v,c=client()){
  const initial=await request(v.discovery,'/v1/pair',{method:'POST',body:{protocol:PROTOCOL,...c,nonce:v.discovery.nonce}});
  assert.ok([200,202].includes(initial.http),JSON.stringify(initial));
  for(let i=0;i<50;i++){const state=await request(v.discovery,'/v1/pair/status',{credential:c});
    if(state.value.result?.pairing_status==='PAIRED')return c;await wait(5);}
  throw Error('fixture pairing did not resolve');
}
const rpc=(v,c,id='request1',method='session.message',payload={operation_id:'original1',raw_text:'请解释选中的内容'})=>
  request(v.discovery,'/v1/rpc',{method:'POST',credential:c,body:{protocol:PROTOCOL,request_id:id,method,scope:scope(),payload}});

// AUTHORED_NOT_RUN: test-owned actual loopback, UNIT metadata decision boundary.
test('maintenance readonly grants never add writes and uncertain positive journal keeps exact original ID',async t=>{const reads=['session.work.maintenance.deliveries','session.work.maintenance.delivery','session.work.maintenance.prepare','session.work.maintenance.inspect','session.work.maintenance.operation'],v=await fixture(t,{requestPairing:async()=>({approved:true,methods:reads,project_ids:['project1']})}),c=await pair(v),before=Object.keys(v.b.journal.value.requests).length;for(const method of reads)assert.equal((await rpc(v,c,'read-'+method.replaceAll('.','_'),method,{})).value.status,'OK');assert.equal(Object.keys(v.b.journal.value.requests).length,before);assert.equal((await rpc(v,c,'denied-maintenance','session.work.maintenance.change',{})).http,403);const positive=await fixture(t,{dispatch:async()=>{throw Object.assign(Error('UNIT lost metadata reply'),{public:true,code:'WORK_MAINTENANCE_UNKNOWN'});}}),client=await pair(positive),id='maintenance-original';await rpc(positive,client,id,'session.work.maintenance.change',{request:{operation_id:'maintenance_work_key'}});const row=positive.b.journal.value.requests[client.client_id+':'+id];assert.equal(row.operation_id,'maintenance_work_key');assert.equal(row.status,'UNKNOWN');});

// AUTHORED_NOT_RUN: actual test-owned loopback, UNIT business; no native dialog.
test('native save alone receives dialog budget and retains original operation in durable journal',async t=>{
 const v=await fixture(t,{requestTimeoutMs:25,nativeExportTimeoutMs:250,dispatch:async()=>{await wait(75);return {status:'UNIT_METADATA_RETURNED'};}}),c=await pair(v),id='native-budget';
 const out=await rpc(v,c,id,'session.work.deliveries.export',{request:{operation_id:'original_export'}});assert.equal(out.value.status,'OK');assert.equal(v.b.journal.value.requests[c.client_id+':'+id].operation_id,'original_export');
 const ordinary=await rpc(v,c,'ordinary-budget','session.message',{});assert.equal(ordinary.value.status,'UNKNOWN');assert.equal(v.b.requestTimeoutMs,25);
});
test('disconnected native save aborts its pending dialog signal and keeps unknown journal',async t=>{
 const started=deferred(),aborted=deferred(),v=await fixture(t,{dispatch:async(_method,_params,ctx)=>{started.resolve();await new Promise(resolve=>ctx.signal.addEventListener('abort',()=>{aborted.resolve();resolve();},{once:true}));throw Object.assign(Error('UNIT aborted before save'),{public:true,code:'NATIVE_EXPORT_UNKNOWN'});}}),c=await pair(v),id='native-disconnect',body=JSON.stringify({protocol:PROTOCOL,request_id:id,method:'session.work.deliveries.export',scope:scope(),payload:{request:{operation_id:'original_export'}}});
 const req=http.request({hostname:'127.0.0.1',port:v.discovery.port,path:'/v1/rpc',method:'POST',headers:{Authorization:'Bearer '+c.client_secret,'X-EEAS-Client-Id':c.client_id,'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}},res=>res.resume());req.on('error',()=>{});req.end(body);await started.promise;req.destroy();await Promise.race([aborted.promise,wait(1000).then(()=>{throw Error('native disconnect not observed');})]);
 for(let i=0;i<40&&v.b.journal.value.requests[c.client_id+':'+id].status!=='UNKNOWN';i++)await wait(5);assert.equal(v.b.journal.value.requests[c.client_id+':'+id].status,'UNKNOWN');
});

// AUTHORED_NOT_RUN: actual loopback authority; injected business boundary.
test('new report reads need explicit methods and do not grow the durable mutation journal',async t=>{
 const v=await fixture(t,{requestPairing:async()=>({approved:true,methods:['session.tool.reports','session.tool.report'],project_ids:['project1']})}),c=await pair(v),before=Object.keys(v.b.journal.value.requests).length;
 for(const method of ['session.tool.reports','session.tool.report']){const r=await rpc(v,c,'read-'+method.replaceAll('.','_'),method,{});assert.equal(r.value.status,'OK');}
 assert.equal(Object.keys(v.b.journal.value.requests).length,before);assert.deepEqual(v.calls.map(row=>row.method),['session.tool.reports','session.tool.report']);
 const old=await fixture(t,{requestPairing:async()=>({approved:true,methods:['session.tool'],project_ids:['project1']})}),oldClient=await pair(old);assert.equal((await rpc(old,oldClient,'denied','session.tool.reports',{})).http,403);assert.equal(old.calls.length,0);
});

test('actual loopback discovery is non-secret, bounded and concurrent start does not create another server',async t=>{
  const v=await fixture(t);assert.deepEqual(await v.b.start(),v.discovery);
  const saved=JSON.parse(fs.readFileSync(path.join(v.d,'ide-companion/discovery.json')));
  assert.deepEqual(saved,v.discovery);assert.equal(saved.pid,process.pid);assert.equal(saved.version,'unit-1');assert.match(saved.nonce,/^[a-f0-9]{64}$/);
  assert.deepEqual(Object.keys(saved).sort(),['instance_id','nonce','pid','port','protocol','schema','version']);
  assert.equal(v.b.server.address().address,'127.0.0.1');await v.b.close();assert.equal(fs.existsSync(v.b.discoveryFile),false);
});
test('approval is a real pending boundary; no unpaired API dispatch, then typed exact scope reaches injected owner once',async t=>{
  const gate=deferred(),v=await fixture(t,{requestPairing:()=>gate.promise}),c=client();
  const p=await request(v.discovery,'/v1/pair',{method:'POST',body:{protocol:PROTOCOL,...c,nonce:v.discovery.nonce}});
  assert.equal(p.value.status,'PENDING');assert.equal((await rpc(v,c)).http,403);assert.equal(v.calls.length,0);
  gate.resolve({approved:true});for(let i=0;i<30;i++){if((await request(v.discovery,'/v1/pair/status',{credential:c})).value.result.pairing_status==='PAIRED')break;await wait(3);}
  const result=await rpc(v,c);assert.equal(result.value.status,'OK');assert.equal(result.value.result.status,'RETURNED');assert.equal(v.calls.length,1);
  assert.equal(v.calls[0].method,'session.message');assert.deepEqual(v.calls[0].params.scope,scope());assert.equal(v.calls[0].context.client_id,c.client_id);
  assert.equal(v.calls[0].context.request_id,'request1');assert.ok(v.calls[0].context.signal instanceof AbortSignal);
});
test('journal stores ticket hash and request metadata, never the ticket, prompt or business reply',async t=>{
  const v=await fixture(t),c=await pair(v);await rpc(v,c,'saved-one','session.message',{operation_id:'business-op',raw_text:'原始意见绝不能进入桥账本'});
  const raw=fs.readFileSync(path.join(v.d,'ide-companion/clients.json'),'utf8');
  for(const secret of [c.client_secret,'原始意见绝不能进入桥账本','真实HTTP后的注入业务结果','Bearer','DesktopToken','eeas_session'])assert.equal(raw.includes(secret),false);
  const row=JSON.parse(raw).value.requests[c.client_id+':saved-one'];assert.equal(row.operation_id,'business-op');assert.equal(row.status,'RETURNED');assert.equal(row.observed_status,'RETURNED');
});
test('same pending identity cannot reopen consent or change secret; wrong nonce never creates a client',async t=>{
  let approvals=0;const gate=deferred(),v=await fixture(t,{requestPairing:()=>{approvals++;return gate.promise;}}),c=client();
  const body={protocol:PROTOCOL,...c,nonce:v.discovery.nonce};
  await request(v.discovery,'/v1/pair',{method:'POST',body});await request(v.discovery,'/v1/pair',{method:'POST',body});
  assert.equal(approvals,1);assert.equal((await request(v.discovery,'/v1/pair',{method:'POST',body:{...body,client_secret:'f'.repeat(64)}})).value.error.code,'CLIENT_SECRET_MISMATCH');
  const other=client();assert.equal((await request(v.discovery,'/v1/pair',{method:'POST',body:{protocol:PROTOCOL,...other,nonce:'0'.repeat(64)}})).http,400);
  assert.equal(v.b.journal.value.clients[other.client_id],undefined);gate.resolve({approved:false});
});
test('pair deadline and late approval cannot grant permission; read-only status does not re-pair',async t=>{
  const gate=deferred(),v=await fixture(t,{pairingTimeoutMs:30,requestPairing:()=>gate.promise}),c=client();
  const body={protocol:PROTOCOL,...c,nonce:v.discovery.nonce};await request(v.discovery,'/v1/pair',{method:'POST',body});await wait(50);gate.resolve({approved:true});await wait(5);
  const state=await request(v.discovery,'/v1/pair/status',{credential:c});assert.equal(state.value.result.pairing_status,'UNKNOWN');
  assert.equal((await rpc(v,c)).http,403);assert.equal(v.calls.length,0);assert.equal((await request(v.discovery,'/v1/pair',{method:'POST',body})).value.result.pairing_status,'UNKNOWN');
});
test('close ignores late consent, preserves UNKNOWN, and only removes its own discovery',async t=>{
  const gate=deferred(),v=await fixture(t,{requestPairing:()=>gate.promise}),c=client();
  await request(v.discovery,'/v1/pair',{method:'POST',body:{protocol:PROTOCOL,...c,nonce:v.discovery.nonce}});
  const foreign={...v.discovery,instance_id:randomUUID()};fs.writeFileSync(v.b.discoveryFile,JSON.stringify(foreign));
  assert.deepEqual(await v.b.close(),{status:'CLOSED',external_tasks_cancelled:false});gate.resolve({approved:true});await wait(5);
  assert.equal(JSON.parse(fs.readFileSync(v.b.discoveryFile)).instance_id,foreign.instance_id);
  assert.equal(JSON.parse(fs.readFileSync(v.b.journal.filename)).value.clients[c.client_id].status,'UNKNOWN');assert.equal(v.calls.length,0);
});
test('paired ticket survives restart; prior mutation reply uses GET history without redispatch or erasing known outcome',async t=>{
  const v=await fixture(t),c=await pair(v);await rpc(v,c,'keep-op');const oldNonce=v.discovery.nonce;await v.b.close();
  let invoked=0;const next=await fixture(t,{dataDir:v.d,dispatch:async()=>{invoked++;return {status:'RETURNED'};}});assert.notEqual(next.discovery.nonce,oldNonce);
  assert.equal((await request(next.discovery,'/v1/pair/status',{credential:c})).value.result.pairing_status,'PAIRED');
  const original=await request(next.discovery,'/v1/rpc/keep-op',{credential:c});assert.equal(original.value.status,'UNKNOWN');
  assert.equal(original.value.result.request_status,'RETURNED');assert.equal(original.value.result.observed_status,'RETURNED');assert.equal(original.value.result.operation_id,'original1');
  await rpc(next,c,'keep-op');assert.equal(invoked,0);
  assert.equal((await rpc(next,c,'keep-op','session.message',{operation_id:'other',raw_text:'changed'})).value.error.code,'IDEMPOTENCY_CONFLICT');
  const read=await rpc(next,c,'read-original','session.operation',{operation_id:'original1'});assert.equal(read.value.status,'OK');assert.equal(invoked,1);
});
test('actual request deadline returns UNKNOWN; original GET observes late reply while duplicate POST never dispatches twice',async t=>{
  const gate=deferred();let invoked=0;const v=await fixture(t,{requestTimeoutMs:30,dispatch:()=>{invoked++;return gate.promise;}}),c=await pair(v);
  const first=await rpc(v,c,'slow');assert.equal(first.value.status,'UNKNOWN');assert.equal(invoked,1);
  await rpc(v,c,'slow');assert.equal(invoked,1);assert.equal((await request(v.discovery,'/v1/rpc/slow',{credential:c})).value.status,'UNKNOWN');
  gate.resolve({status:'RETURNED',text:'晚到但已知结果'});await wait(10);
  const late=await request(v.discovery,'/v1/rpc/slow',{credential:c});assert.equal(late.value.status,'OK');assert.equal(late.value.result.text,'晚到但已知结果');
});
test('transport close aborts only its observation, is bounded with an uncooperative dispatch and cannot publish late effects',async t=>{
  const gate=deferred();let signal;const v=await fixture(t,{dispatch:(_m,_p,c)=>{signal=c.signal;return gate.promise;}}),c=await pair(v);
  const pending=rpc(v,c,'closing').catch(()=>null);for(let i=0;i<20&&!signal;i++)await wait(3);
  const before=Date.now();const stopped=await v.b.close();assert.ok(Date.now()-before<500);assert.equal(stopped.external_tasks_cancelled,false);assert.equal(signal.aborted,true);
  const raw=fs.readFileSync(v.b.journal.filename);gate.resolve({status:'RETURNED',text:'late'});await wait(10);assert.deepEqual(fs.readFileSync(v.b.journal.filename),raw);await pending;
  const next=await fixture(t,{dataDir:v.d});const observed=await request(next.discovery,'/v1/rpc/closing',{credential:c});assert.equal(observed.value.result.request_status,'UNKNOWN');
});
test('revocation is explicit and durable, blocks future dispatch and suppresses a late response without cancelling original work',async t=>{
  const gate=deferred();let dispatched=0;const v=await fixture(t,{dispatch:()=>{dispatched++;return gate.promise;}}),c=await pair(v);
  const pending=rpc(v,c,'before-revoke');for(let i=0;i<20&&!dispatched;i++)await wait(3);assert.equal(v.b.revoke(c.client_id),true);
  gate.resolve({status:'RETURNED',text:'must not return to revoked client'});const result=await pending;assert.equal(result.value.error.code,'PAIRING_REVOKED');
  assert.equal((await rpc(v,c,'after-revoke')).value.error.code,'PAIRING_REVOKED');assert.equal(dispatched,1);await v.b.close();
  const next=await fixture(t,{dataDir:v.d});assert.equal((await rpc(next,c,'restart-revoked')).value.error.code,'PAIRING_REVOKED');
});
test('Origin, host, query, arbitrary methods, missing authorization and body overflow cannot reach business dispatcher',async t=>{
  const v=await fixture(t),c=await pair(v);
  for(const headers of [{Origin:'https://alien.invalid'},{Origin:'null'},{Host:'localhost:'+v.discovery.port},{'Sec-Fetch-Site':'same-origin'}])
    assert.equal((await request(v.discovery,'/v1/rpc',{method:'POST',credential:c,body:{},headers})).http,403);
  assert.equal((await request(v.discovery,'/v1/rpc?url=http://alien.invalid',{credential:c})).http,404);
  assert.equal((await rpc(v,c,'unknown','process.execute',{command:'oops'})).value.error.code,'NOT_SUPPORTED');
  assert.equal((await request(v.discovery,'/v1/rpc',{method:'POST',body:{}})).http,401);
  assert.equal((await request(v.discovery,'/v1/rpc',{method:'POST',credential:c,raw:'x'.repeat(131073)})).http,413);
  assert.equal((await request(v.discovery,'/v1/rpc',{method:'POST',credential:c,raw:'{bad'})).value.error.code,'INVALID_JSON');assert.equal(v.calls.length,0);
});
test('approved method and project grants are enforced independently, including global create and project list metadata',async t=>{
  const calls=[],v=await fixture(t,{requestPairing:async()=>({approved:true,methods:['projects.list','session.get'],project_ids:['allowed']}),
    dispatch:async(m)=>{calls.push(m);return {projects:[{id:'allowed'},{id:'private'}]};}}),c=await pair(v);
  const capability=(await request(v.discovery,'/v1/pair/status',{credential:c})).value.result.capabilities;
  assert.equal(capability.raw_guidance,false);assert.equal(capability.shared_session_read,true);assert.equal(capability.capability_semantics,'GRANTED_HOST_API_RECHECK_REQUIRED');
  const list=await request(v.discovery,'/v1/rpc',{method:'POST',credential:c,body:{protocol:PROTOCOL,request_id:'list',method:'projects.list',scope:{},payload:{}}});
  assert.deepEqual(list.value.result.projects,[{id:'allowed'}]);assert.equal((await rpc(v,c,'denied-method')).value.error.code,'CAPABILITY_DENIED');
  assert.equal((await rpc(v,c,'denied-project','session.get',{})).value.error.code,'SCOPE_DENIED');assert.deepEqual(calls,['projects.list']);
});
test('read request identity also refuses changed contents and unknown sessions are not inferred from a filename',async t=>{
  const v=await fixture(t),c=await pair(v);await rpc(v,c,'read1','session.get',{});
  assert.equal((await rpc(v,c,'read1','session.get',{path:'different'})).value.error.code,'IDEMPOTENCY_CONFLICT');assert.equal(v.calls.length,1);
  for(const invalid of [{project_id:1},{session_ref:{revision:1,sha256:'a'.repeat(64)}},{generation:-1},{project_id:'ok',filename:'same.md'}])assert.throws(()=>validateScope(invalid));
});
test('mutating ledger capacity and pending dispatch capacity refuse before another execution',async t=>{
  const v=await fixture(t,{maxRecords:1}),c=await pair(v);await rpc(v,c,'first');assert.equal((await rpc(v,c,'second')).value.error.code,'CAPACITY_REACHED');assert.equal(v.calls.length,1);
  const gate=deferred(),w=await fixture(t,{maxConcurrent:1,requestTimeoutMs:25,dispatch:()=>gate.promise}),d=await pair(w);
  await rpc(w,d,'busy1');assert.equal((await rpc(w,d,'busy2')).value.error.code,'BRIDGE_BUSY');gate.resolve({status:'RETURNED'});
});
test('tampered, symlinked or CAS-changed private state fails closed without another proxy call',async t=>{
  const v=await fixture(t),c=await pair(v),file=v.b.journal.filename;fs.appendFileSync(file,' ');
  assert.equal((await rpc(v,c)).value.error.code,'BRIDGE_UNAVAILABLE');assert.equal(v.calls.length,0);
  fs.writeFileSync(file,'{"bad":true}');assert.throws(()=>new IdeCompanionBridge({dataDir:v.d,requestPairing:async()=>({approved:true})}));
  const d=directory(t);fs.mkdirSync(path.join(d,'ide-companion'));const external=path.join(d,'other.json');fs.writeFileSync(external,'{}');fs.symlinkSync(external,path.join(d,'ide-companion/clients.json'));
  assert.throws(()=>new IdeCompanionBridge({dataDir:d,requestPairing:async()=>({approved:true})}),/symbolic/);
});
test('unsupported integration is honest, errors do not expose arbitrary exception secrets, and invalid response does not hang',async t=>{
  const v=await fixture(t,{dispatch:undefined}),c=await pair(v);assert.equal((await rpc(v,c)).value.error.code,'NOT_SUPPORTED');
  const w=await fixture(t,{dispatch:async()=>{throw Error('Bearer private-secret-value');}}),d=await pair(w);
  const rejected=await rpc(w,d);assert.equal(rejected.value.error.code,'DISPATCH_UNCONFIRMED');assert.equal(JSON.stringify(rejected.value).includes('private-secret-value'),false);
  const z=await fixture(t,{dispatch:async()=>({invalid:1n})}),e=await pair(z);assert.equal((await rpc(z,e)).value.error.code,'RESPONSE_UNAVAILABLE');
});
test('explicit lifecycle reveal calls only the injected Main reveal, never the generic dispatcher',async t=>{
  let shown=0;const v=await fixture(t,{reveal:()=>{shown++;return {shown:true};}}),c=await pair(v);
  assert.equal((await rpc(v,c,'show','lifecycle.reveal',{})).value.result.shown,true);assert.equal(shown,1);assert.equal(v.calls.length,0);
  await rpc(v,c,'show','lifecycle.reveal',{});assert.equal(shown,1);
});
test('protocol JSON matches actual path/method/limits and does not offer a second model loop',()=>{
  const p=JSON.parse(fs.readFileSync(path.join(__dirname,'../../../plugins/vscode-companion/protocol.json')));
  assert.equal(p.protocol,PROTOCOL);assert.deepEqual(p.methods,METHODS);assert.equal(p.rpc.status_path,'/v1/rpc/{request_id}');assert.equal(p.limits.body_bytes,131072);assert.equal(p.limits.body_bytes,p.limits.request_bytes);
  assert.equal(p.external_agent_owner,'DESKTOP_EEAS_ONLY');assert.equal(p.client_close_semantics,'DISCONNECT_ONLY_NO_TASK_CANCEL_NO_DESKTOP_SHUTDOWN');
});
test('readonly cursor polling does not grow the durable mutation journal or perform consent again',async t=>{
  let approvals=0;const v=await fixture(t,{requestPairing:async()=>{approvals++;return {approved:true};}}),c=await pair(v);
  const before=fs.readFileSync(v.b.journal.filename);
  for(let i=0;i<5;i++)assert.equal((await rpc(v,c,'events'+i,'session.events',{after:i,limit:20})).value.status,'OK');
  assert.deepEqual(fs.readFileSync(v.b.journal.filename),before);assert.equal(approvals,1);assert.equal(Object.keys(v.b.journal.value.requests).length,0);
});
test('pending consent capacity refuses a second prompt and a denied client cannot silently become paired',async t=>{
  let prompts=0;const gate=deferred(),v=await fixture(t,{maxPendingPairs:1,requestPairing:()=>{prompts++;return gate.promise;}}),c=client(),d=client();
  const body={protocol:PROTOCOL,...c,nonce:v.discovery.nonce};await request(v.discovery,'/v1/pair',{method:'POST',body});
  const second=await request(v.discovery,'/v1/pair',{method:'POST',body:{protocol:PROTOCOL,...d,nonce:v.discovery.nonce}});
  assert.equal(second.http,429);assert.equal(prompts,1);gate.resolve({approved:false});await wait(10);
  await request(v.discovery,'/v1/pair',{method:'POST',body});assert.equal(prompts,1);
  assert.equal((await request(v.discovery,'/v1/pair/status',{credential:c})).value.result.pairing_status,'DENIED');
});
test('task diff reads preserve the journal and require their own method and project grants',async t=>{
  const v=await fixture(t),c=await pair(v),before=fs.readFileSync(v.b.journal.filename);
  for(const method of ['session.diff','session.diff.file','session.child.diff','session.child.diff.file','session.diff.prepare','session.diff.review.operation','session.editor.get','session.editor.control.operation','session.editor.file.prepare','session.editor.file.operation','session.editor.observer.prepare','session.editor.observer.operation','session.editor.observer.get','session.editor.capture.operation','session.editor.capture.status']){
    assert.equal((await rpc(v,c,method.replaceAll('.','_'),method,{snapshot_sha256:'e'.repeat(64)})).value.status,'OK');
  }
  assert.deepEqual(fs.readFileSync(v.b.journal.filename),before);
  assert.equal((await request(v.discovery,'/v1/pair/status',{credential:c})).value.result.capabilities.task_diff_read,true);
  assert.equal((await request(v.discovery,'/v1/pair/status',{credential:c})).value.result.capabilities.child_diff_read,true);
  const old=await fixture(t,{requestPairing:async()=>({approved:true,methods:['session.get'],project_ids:['project1']})}),d=await pair(old);
  assert.equal((await rpc(old,d,'new-diff','session.diff',{})).value.error.code,'CAPABILITY_DENIED');
  assert.equal((await request(old.discovery,'/v1/pair/status',{credential:d})).value.result.capabilities.task_diff_read,false);
  assert.equal((await rpc(old,d,'new-child-diff','session.child.diff',{})).value.error.code,'CAPABILITY_DENIED');
  assert.equal(old.calls.length,0);
  const restricted=await fixture(t,{requestPairing:async()=>({approved:true,methods:['session.diff','session.diff.file'],project_ids:['another']})}),e=await pair(restricted);
  assert.equal((await rpc(restricted,e,'cross-project','session.diff',{})).value.error.code,'SCOPE_DENIED');assert.equal(restricted.calls.length,0);
});
test('close during listen cannot publish late discovery or leave a listening server',async t=>{
  const d=directory(t),b=new IdeCompanionBridge({dataDir:d,requestPairing:async()=>({approved:true})});
  const starting=b.start();await b.close();await assert.rejects(starting,/closed/);assert.equal(b.server.listening,false);assert.equal(fs.existsSync(b.discoveryFile),false);
});
test('dangling private-state symlink and non-string or newline identity are rejected',async t=>{
  const d=directory(t);fs.mkdirSync(path.join(d,'ide-companion'));fs.symlinkSync(path.join(d,'missing'),path.join(d,'ide-companion/clients.json'));
  assert.throws(()=>new IdeCompanionBridge({dataDir:d,requestPairing:async()=>({approved:true})}),/symbolic/);
  const v=await fixture(t),c=client();
  for(const id of [123,'client\n'])assert.equal((await request(v.discovery,'/v1/pair',{method:'POST',body:{protocol:PROTOCOL,...c,client_id:id,nonce:v.discovery.nonce}})).http,400);
});
const nativeScope={project_id:'project1',conversation_id:'desktop-topic'};
const nativeRpc=(v,c,request_id,method,payload={},owner=nativeScope)=>request(v.discovery,'/v1/rpc',{
  method:'POST',credential:c,body:{protocol:PROTOCOL,request_id,method,scope:owner,payload}});
test('native methods use explicit project/discussion scope, never an AgentSession with the same id',async t=>{
  const v=await fixture(t),c=await pair(v);
  const read=await nativeRpc(v,c,'native-read','native.conversation.get');assert.equal(read.value.status,'OK');
  assert.deepEqual(v.calls[0].params.scope,nativeScope);
  for(const [method,owner]of [['native.conversation.get',scope()],['native.conversation.get',{...nativeScope,session_ref:scope().session_ref}],
    ['native.conversation.get',{project_id:'project1'}],['native.conversations.list',nativeScope],['session.get',nativeScope]]){
    const bad=await nativeRpc(v,c,'reject-'+v.calls.length+'-'+method.replaceAll('.','_'),method,{},owner);assert.equal(bad.value.status,'ERROR');
  }
  assert.equal(v.calls.length,1);
  const capability=(await request(v.discovery,'/v1/pair/status',{credential:c})).value.result.capabilities;
  assert.equal(capability.native_discussion_read,true);assert.equal(capability.native_discussion_send,true);
});
test('old paired grants do not gain native methods after restart and project grants remain authoritative',async t=>{
  const v=await fixture(t,{requestPairing:async()=>({approved:true,methods:['session.get'],project_ids:['project1']})}),c=await pair(v);
  await v.b.close();const next=await fixture(t,{dataDir:v.d});
  const denied=await nativeRpc(next,c,'new-method','native.conversation.get');assert.equal(denied.value.error.code,'CAPABILITY_DENIED');assert.equal(next.calls.length,0);
  const pairState=(await request(next.discovery,'/v1/pair/status',{credential:c})).value.result,cap=pairState.capabilities;
  assert.equal(cap.native_discussion_read,false);assert.ok(pairState.supported_methods.includes('native.conversation.get'));
  assert.deepEqual(cap.methods,['session.get']);
  const w=await fixture(t,{requestPairing:async()=>({approved:true,methods:['native.conversation.get'],project_ids:['allowed']})}),d=await pair(w);
  assert.equal((await nativeRpc(w,d,'other-project','native.conversation.get')).value.error.code,'SCOPE_DENIED');assert.equal(w.calls.length,0);
});
test('native mutation UNKNOWN is never redispatched; ledger retains scope but no prompt/configuration/credential',async t=>{
  const gate=deferred();let count=0;const v=await fixture(t,{requestTimeoutMs:25,dispatch:()=>{count++;return gate.promise;}}),c=await pair(v);
  const body={operation_id:'native-original',raw_text:'PRIVATE_ORIGINAL_PROMPT',provider_profile_ref:{id:'profile',revision:1,sha256:'c'.repeat(64)}};
  assert.equal((await nativeRpc(v,c,'native-once','native.message',body)).value.status,'UNKNOWN');
  await nativeRpc(v,c,'native-once','native.message',body);assert.equal(count,1);
  const changed=await nativeRpc(v,c,'native-once','native.message',body,{...nativeScope,conversation_id:'same-project-other-topic'});
  assert.equal(changed.value.error.code,'IDEMPOTENCY_CONFLICT');assert.equal(count,1);
  gate.resolve({kind:'NATIVE_MESSAGE',status:'RETURNED',operation_id:'native-original'});await wait(10);
  const raw=fs.readFileSync(v.b.journal.filename,'utf8'),record=JSON.parse(raw).value.requests[c.client_id+':native-once'];
  assert.deepEqual(record.scope,nativeScope);assert.equal(record.operation_id,'native-original');
  for(const secret of ['PRIVATE_ORIGINAL_PROMPT',c.client_secret,'provider_profile_ref'])assert.equal(raw.includes(secret),false);
  await v.b.close();const next=await fixture(t,{dataDir:v.d});
  const original=await request(next.discovery,'/v1/rpc/native-once',{credential:c});assert.equal(original.value.result.operation_id,'native-original');
  await nativeRpc(next,c,'native-once','native.message',body);assert.equal(next.calls.length,0);
});
test('actual native mapper uncertainty remains UNKNOWN in the durable bridge after error and restart',async t=>{
  const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
  for(const mismatch of [false,true]){
    let posts=0;const profile={id:'profile',revision:1,sha256:'c'.repeat(64)};
    const dispatch=createIdeApiAdapter({request:async(url,body)=>{
      if(body){posts++;throw Object.assign(Error('ambiguous'),{status:408});}
      if(url.includes('/operations/')){
        if(!mismatch)throw Object.assign(Error('receipt not observed'),{status:404});
        return{kind:'NATIVE_MESSAGE',...nativeScope,operation_id:'original',status:'PROCESSING',request_sha256:'b'.repeat(64),
          record_origin:'CURRENT_HOST',input:{raw_text:'different original bytes',origin:'ORIGINAL_NATIVE_REQUEST'},provider_profile_ref:profile,
          native_message_ref:null,file_source_ref:null,file_link_ref:null,control_capabilities:{}};
      }
      return{kind:'NATIVE_CONVERSATION',...nativeScope,messages:[{}]};
    }});
    const v=await fixture(t,{dispatch}),c=await pair(v),payload={operation_id:'original',raw_text:'new explicit input',provider_profile_ref:profile};
    const first=await nativeRpc(v,c,'uncertain','native.message',payload);
    assert.equal(first.value.error.code,mismatch?'OPERATION_INPUT_MISMATCH':'OPERATION_OUTCOME_UNKNOWN');
    assert.equal(v.b.journal.value.requests[c.client_id+':uncertain'].status,'UNKNOWN');
    await nativeRpc(v,c,'uncertain','native.message',payload);assert.equal(posts,1);
    await v.b.close();const next=await fixture(t,{dataDir:v.d});
    const saved=await request(next.discovery,'/v1/rpc/uncertain',{credential:c});assert.equal(saved.value.result.request_status,'UNKNOWN');
    await nativeRpc(next,c,'uncertain','native.message',payload);assert.equal(next.calls.length,0);
  }
});


// AUTHORED_NOT_RUN. Actual bridge/HTTP, observer API persistence is a UNIT boundary.
test('API-journaled observer metadata cannot exhaust the human mutation journal or become readonly permission',async t=>{
 const v=await fixture(t,{maxRecords:1}),c=await pair(v);
 assert.equal((await rpc(v,c,'one_human','session.message')).value.status,'OK');const before=fs.readFileSync(v.b.journal.filename);
 for(let i=0;i<4;i++)assert.equal((await rpc(v,c,'observer_meta_'+i,'session.editor.observer.command',{operation_id:'original_observation_'+i})).value.status,'OK');
 assert.deepEqual(fs.readFileSync(v.b.journal.filename),before);assert.equal(Object.keys(v.b.journal.value.requests).length,1);
 const denied={...v.b.journal.value.clients[c.client_id],methods:['session.get']};
 await assert.rejects(v.b.rpc(c.client_id,denied,{protocol:PROTOCOL,request_id:'cannot_publish',method:'session.editor.observer.command',scope:scope(),payload:{operation_id:'not_allowed'}}),{code:'CAPABILITY_DENIED'});
});

// AUTHORED_NOT_RUN. Actual authenticated SSE socket; injected business dispatch.
async function captureChannel(t,v,c,window='native_window'){
 const frames=[];let remainder='',response;
 const req=http.request({hostname:'127.0.0.1',port:v.discovery.port,path:'/v1/editor-capture-events?window_id='+window,method:'GET',headers:{'Authorization':'Bearer '+c.client_secret,'X-EEAS-Client-Id':c.client_id}},res=>{response=res;res.setEncoding('utf8');res.on('data',chunk=>{remainder+=chunk;let at;while((at=remainder.indexOf('\n\n'))>=0){const frame=remainder.slice(0,at);remainder=remainder.slice(at+2);const data=frame.split('\n').find(line=>line.startsWith('data: '));if(data)frames.push(JSON.parse(data.slice(6)));}});});
 req.on('error',()=>{});t.after(()=>req.destroy());req.end();
 for(let i=0;i<100&&!response;i++)await wait(5);assert.ok(response,'actual capture stream returned headers');
 return {req,response,frames,async next(){for(let i=0;i<100&&!frames.length;i++)await wait(5);assert.ok(frames.length,'actual capture frame arrived');return frames.shift();}};
}
test('capture stream delivers only opaque own-window notices, deduplicates, and has no idle frames or journal writes',async t=>{
 const v=await fixture(t),c=await pair(v),stream=await captureChannel(t,v,c);assert.equal(stream.response.statusCode,200);assert.match(stream.response.headers['content-type'],/text\/event-stream/);assert.equal(stream.response.headers['x-eeas-instance-id'],v.discovery.instance_id);
 const journal=fs.readFileSync(v.b.journal.filename);await wait(25);assert.equal(stream.frames.length,0);
 const row={observer_id:'e'.repeat(64),editor_client_id:c.client_id,editor_window_id:'native_window',project_id:'project1',capture_ref:'f'.repeat(64),expires_at_ms:Date.now()+12000};v.b.notifyEditorCaptures([row]);
 const frame=await stream.next();assert.equal(frame.observer_id,row.observer_id);assert.equal(frame.capture_ref,row.capture_ref);assert.equal(frame.window_id,'native_window');assert.doesNotMatch(JSON.stringify(frame),/project_id|session_ref|permission_ref|native_path|raw_text/);
 v.b.notifyEditorCaptures([row]);await wait(25);assert.equal(stream.frames.length,0);assert.deepEqual(fs.readFileSync(v.b.journal.filename),journal);assert.equal(v.calls.length,0);
});
test('capture notification enforces original ticket project/method grants and revocation closes only its stream',async t=>{
 const v=await fixture(t,{requestPairing:async()=>({approved:true,project_ids:['project1']})}),c=await pair(v),stream=await captureChannel(t,v,c);
 const base={observer_id:'a'.repeat(64),editor_client_id:c.client_id,editor_window_id:'native_window',project_id:'not_granted',capture_ref:'b'.repeat(64),expires_at_ms:Date.now()+12000};
 assert.equal(v.b.notifyEditorCaptures([base]).queued,0);await wait(25);assert.equal(stream.frames.length,0);
 let ended=false;stream.response.on('end',()=>{ended=true;});stream.response.resume();v.b.revoke(c.client_id);for(let i=0;i<100&&!ended;i++)await wait(5);assert.equal(ended,true);assert.equal(v.b.captureStreams.size,0);assert.equal(v.calls.length,0);
});
test('capture listen is not an RPC mutation or a model dispatch',async t=>{
 const v=await fixture(t),c=await pair(v),before=fs.readFileSync(v.b.journal.filename);
 const result=await rpc(v,c,'wrong_stream_transport','session.editor.capture.listen',{});assert.equal(result.value.error.code,'STREAM_TRANSPORT_REQUIRED');assert.deepEqual(fs.readFileSync(v.b.journal.filename),before);assert.equal(v.calls.length,0);
});
test('capture exception accepts only its exact authenticated GET and never arbitrary query parameters',async t=>{
 const v=await fixture(t),c=await pair(v),base='/v1/editor-capture-events';
 for(const query of ['?window_id=','?window_id='+('a'.repeat(101)),'?window_id=one&window_id=two','?window_id=one&url=http://alien.invalid','?window_id=a%2Fb','?window_id=one#fragment']){
  assert.equal((await request(v.discovery,base+query,{credential:c})).http,404);
 }
 assert.equal((await request(v.discovery,base+'?window_id=one',{method:'POST',credential:c,body:{}})).http,404);
 assert.equal((await request(v.discovery,base+'?window_id=one')).http,401);
 assert.equal(v.b.captureStreams.size,0);assert.equal(v.calls.length,0);
});

test('capture wait slots do not consume the original business dispatch capacity',async t=>{
 const held=deferred();let entered=false;const v=await fixture(t,{maxConcurrent:1,dispatch:async method=>{if(method==='session.editor.capture.wait'){entered=true;return held.promise;}return {status:'RETURNED'};}}),c=await pair(v);
 const pending=rpc(v,c,'one_capture_wait','session.editor.capture.wait',{operation_id:'capture',capture_ref:'a'.repeat(64)});for(let i=0;i<40&&!entered;i++)await wait(1);assert.equal(entered,true);
 assert.equal((await rpc(v,c,'independent_status','status',{})).value.status,'OK');held.resolve({state:'READY'});assert.equal((await pending).value.status,'OK');
});
test('capture progress uses exact original metadata and closes bounded waiters without affecting external tasks',async t=>{
 const v=await fixture(t),ref='a'.repeat(64),source={capture_ref:ref,observer_id:'b'.repeat(64),revision:2};
 const first=v.b.waitEditorCapture(ref,{after:0,ms:1000});v.b.recordEditorCapture(source);assert.equal(await first,true);const cursor=v.b.captureCursor(ref);v.b.recordEditorCapture(source);assert.equal(v.b.captureCursor(ref),cursor);
 const aborted=new AbortController(),pending=v.b.waitEditorCapture(ref,{after:cursor,ms:1000,signal:aborted.signal});aborted.abort();assert.equal(await pending,false);assert.equal(v.b.captureWaiters.size,0);
});
// AUTHORED_NOT_RUN: the new link method requires an explicit pairing grant.
test('work requirement reads skip mutation journal and old tickets cannot gain metadata write access',async t=>{
 const methods=['session.work.requirements','session.work.requirements.operation'],v=await fixture(t,{requestPairing:async()=>({approved:true,methods,project_ids:['project1']})}),client=await pair(v),before=Object.keys(v.b.journal.value.requests).length;
 for(const [i,method]of methods.entries())assert.equal((await rpc(v,client,'work-read-'+i,method,{})).value.status,'OK');
 assert.equal(Object.keys(v.b.journal.value.requests).length,before);assert.equal((await rpc(v,client,'work-denied','session.work.requirements.link',{})).http,403);assert.equal(v.calls.length,2);
});
// AUTHORED_NOT_RUN. Existing pair and loopback HTTP; business dispatch stays UNIT.
test('case mapping read and original-key inspection need new grants and do not grow mutation history',async t=>{
 const methods=['session.work.cases','session.work.cases.operation'],v=await fixture(t,{requestPairing:async()=>({approved:true,methods,project_ids:['project1']})}),c=await pair(v),before=Object.keys(v.b.journal.value.requests).length;
 for(const [i,method]of methods.entries())assert.equal((await rpc(v,c,'case-read-'+i,method,{})).value.status,'OK');
 assert.equal(Object.keys(v.b.journal.value.requests).length,before);assert.equal((await rpc(v,c,'case-denied','session.work.cases.map',{})).http,403);assert.equal(v.calls.length,2);
 const old=await fixture(t,{requestPairing:async()=>({approved:true,methods:['session.work.requirements','session.tool.report'],project_ids:['project1']})}),oldClient=await pair(old);
 for(const [i,method]of [...methods,'session.work.cases.map'].entries())assert.equal((await rpc(old,oldClient,'old-case-'+i,method,{})).http,403);assert.equal(old.calls.length,0);
});
// AUTHORED_NOT_RUN: actual paired HTTP authority; all business dispatch is UNIT.
test('retest reads skip mutation history and old tickets cannot gain native submission or continuation methods',async t=>{
 const methods=['session.work.retests.prepare','session.work.retests.operation'],v=await fixture(t,{requestPairing:async()=>({approved:true,methods,project_ids:['project1']})}),c=await pair(v),before=Object.keys(v.b.journal.value.requests).length;
 for(const [i,method]of methods.entries())assert.equal((await rpc(v,c,'retest-read-'+i,method,{})).value.status,'OK');assert.equal(Object.keys(v.b.journal.value.requests).length,before);
 for(const [i,method]of ['session.work.retests.submit','session.work.retests.continue'].entries())assert.equal((await rpc(v,c,'retest-denied-'+i,method,{})).http,403);assert.equal(v.calls.length,2);
 const old=await fixture(t,{requestPairing:async()=>({approved:true,methods:['session.work.cases','session.work.cases.operation'],project_ids:['project1']})}),oldClient=await pair(old);assert.equal((await rpc(old,oldClient,'old-retest','session.work.retests.prepare',{})).http,403);assert.equal(old.calls.length,0);
});

// AUTHORED_NOT_RUN. Actual bridge/loopback authority; source business is UNIT.
test('source previews and original lookup are readonly while new allowance needs an explicit method grant',async t=>{
 const methods=['session.work.sources.catalog','session.work.sources.review','session.work.sources.operation'],v=await fixture(t,{requestPairing:async()=>({approved:true,methods,project_ids:['project1']})}),c=await pair(v),before=Object.keys(v.b.journal.value.requests).length;
 for(const [i,method]of methods.entries())assert.equal((await rpc(v,c,'source-read-'+i,method,{})).value.status,'OK');
 assert.equal(Object.keys(v.b.journal.value.requests).length,before);assert.equal((await rpc(v,c,'source-write-denied','session.work.sources.decision',{})).http,403);
 const old=await fixture(t,{requestPairing:async()=>({approved:true,methods:['session.work.requirements','session.work.requirements.link'],project_ids:['project1']})}),oldClient=await pair(old);
 for(const [i,method]of [...methods,'session.work.sources.decision'].entries())assert.equal((await rpc(old,oldClient,'old-source-'+i,method,{})).http,403);
 assert.equal(old.calls.length,0);
});
