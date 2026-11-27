'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {SupervisorConversation,boundedSupervisorSnapshot,supervisorConversationDigest:digest,MAX_SUPERVISOR_STATE_BYTES}=require('../supervisor-conversation.cjs');
const profile={id:'chosen_provider',revision:2,sha256:'a'.repeat(64)},stamp='2026-10-19T10:00:00.000Z';
const task=(index,extra={})=>({key:{kind:'native_message',project_id:'project',object_id:'task_'+index,operation_id:'task_'+index,generation:1},label:'工程任务 '+index,classification:'RUNNING',observation_status:'CURRENT',observed_at:stamp,execution_returned:null,validation_status:'NOT_RUN',...extra});
const observation=(tasks=[task(1)])=>({observer:{status:'READY',last_success_at:stamp},tasks});
const receipt=(body,extra={})=>({operation_id:body.operation_id,project_id:null,conversation_id:body.conversation_id,context_ref:null,file_source_ref:null,file_link_ref:null,
  provider_profile_ref:body.provider_profile_ref,request_sha256:digest(body),automatic_retry:false,status:'RETURNED',text:'原模型的简短答复',...extra});
function fixture(t,dispatch){
  const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-supervisor-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
  const calls=[],saved=new Map(),changes=[];let host;
  const request=async(route,body)=>{calls.push({route,body});if(route==='/api/supervisor')return observation();if(body)saved.set(body.operation_id,body);
    if(dispatch)return dispatch(route,body,{saved,host,calls});return receipt(body??saved.get(route.split('/').at(-1)));};
  host=new SupervisorConversation({dataDir,request,onChange:value=>changes.push(value)});
  return {host,dataDir,calls,saved,changes,request,ask:(id='question_one',text='帮我分析有哪些任务需要关注')=>host.ask({operationId:id,text,providerProfileRef:profile})};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('selected-operation display reads the exact old question and updated answer outside recent history with no request or write',async t=>{
  const f=fixture(t);await f.ask('old_original','原问题绝不换成最近的问题');const original=f.host.snapshot().operations.find(row=>row.operation_id==='old_original');
  for(let index=0;index<14;index++)f.host.persistLocal({question:'状态 '+index,answer:{text:'本地事实 '+index}});
  assert.equal(f.host.snapshot().messages.some(row=>row.operation_id==='old_original'),false);
  const before=fs.readFileSync(f.host.file),calls=f.calls.length;
  const display=f.host.displayForOperation('old_original',original.request_sha256);
  assert.equal(display.question,'原问题绝不换成最近的问题');assert.equal(display.text,'原模型的简短答复');
  assert.equal(display.request_sha256,original.request_sha256);assert.equal(display.status,'RETURNED');
  assert.throws(()=>f.host.displayForOperation('old_original','b'.repeat(64)),/SUPERVISOR_QUESTION_REFERENCE_CHANGED/);
  assert.equal(f.calls.length,calls);assert.deepEqual(fs.readFileSync(f.host.file),before);
  f.host.observe('old_original',receipt(f.saved.get('old_original'),{text:'原操作核查后的明确答案'}));
  assert.equal(f.host.displayForOperation('old_original',original.request_sha256).text,'原操作核查后的明确答案');
  assert.equal(f.host.snapshot().messages.some(row=>row.operation_id==='old_original'),false);
});

test('same native runtime receives one no-screen/no-file request after the exact durable operation claim',async t=>{
  const f=fixture(t,async(route,body,{host})=>{
    if(body){const disk=JSON.parse(fs.readFileSync(host.file,'utf8')).state;assert.equal(disk.operations[0].status,'CLAIMED');assert.equal(disk.operations[0].request_sha256,digest(body));}
    return receipt(body);
  });
  f.host.saveDraft({text:'帮我分析有哪些任务需要关注',expectedRevision:0});const result=await f.ask();assert.equal(result.status,'RETURNED');
  assert.equal(f.calls.filter(row=>row.body).length,1);const body=f.calls.find(row=>row.body).body;
  assert.equal(body.context_ref,null);assert.equal(body.file_source_ref,null);assert.equal(body.file_link_ref,null);assert.equal(body.project_id,null);
  assert.deepEqual(body.provider_profile_ref,profile);assert.equal(body.conversation_id,f.host.snapshot().conversation_id);assert.ok(body.raw_text.endsWith('USER QUESTION:\n帮我分析有哪些任务需要关注'));
  assert.equal(f.host.snapshot().messages[0].text,'帮我分析有哪些任务需要关注');assert.doesNotMatch(f.host.snapshot().messages[0].text,/task_observation|You are/);
  assert.equal(f.host.snapshot().draft.text,'');assert.ok(!f.calls.some(row=>/capture|context\/current|screenshot/.test(row.route)));
});

test('draft CAS and local status history perform no request, preserve independent new typing and survive restart',t=>{
  const f=fixture(t);const first=f.host.saveDraft({text:'原问题',expectedRevision:0});assert.equal(first.draft.revision,1);
  assert.throws(()=>f.host.saveDraft({text:'冲突覆盖',expectedRevision:0}),error=>error.code==='SUPERVISOR_DRAFT_CONFLICT');
  f.host.persistLocal({question:'原问题',answer:{kind:'LOCAL_STATUS',text:'2 项运行中'}});assert.equal(f.host.snapshot().draft.text,'');assert.equal(f.calls.length,0);
  const g=new SupervisorConversation({dataDir:f.dataDir,request:f.request});assert.equal(g.snapshot().conversation_id,f.host.snapshot().conversation_id);assert.equal(g.snapshot().messages[1].source,'LOCAL_STATUS');
  g.saveDraft({text:'用户随后输入',expectedRevision:2});assert.throws(()=>f.host.saveDraft({text:'旧实例覆盖',expectedRevision:2}),error=>error.code==='SUPERVISOR_STORAGE_CONFLICT');
  assert.equal(new SupervisorConversation({dataDir:f.dataDir,request:f.request}).snapshot().draft.text,'用户随后输入');
});

test('duplicate operation is one promise and one POST even during initial snapshot read; changed text/profile are rejected',async t=>{
  let release;const gate=new Promise(resolve=>release=resolve);const f=fixture(t,async(_route,body)=>{await gate;return receipt(body);});
  const first=f.ask(),second=f.ask();assert.equal(first,second);assert.throws(()=>f.ask('question_one','另一个问题'),error=>error.code==='SUPERVISOR_OPERATION_CONFLICT');
  release();await first;await f.ask();assert.equal(f.calls.filter(row=>row.body).length,1);
  assert.throws(()=>f.host.ask({operationId:'question_one',text:'帮我分析有哪些任务需要关注',providerProfileRef:{...profile,revision:3}}),error=>error.code==='SUPERVISOR_OPERATION_CONFLICT');
});

test('lost response and HTTP 400 both use only the original GET; there is no automatic task retry',async t=>{
  for(const status of [undefined,400]){
    const f=fixture(t,async(route,body,{saved})=>{if(body)throw Object.assign(Error('PRIVATE RESPONSE TOKEN'),{status});return receipt(saved.get(route.split('/').at(-1)));});
    const result=await f.ask();assert.equal(result.status,'RETURNED');assert.equal(f.calls.filter(row=>row.body).length,1);assert.equal(f.calls.filter(row=>/native-messages\/question_one$/.test(row.route)).length,1);
    assert.doesNotMatch(fs.readFileSync(f.host.file,'utf8'),/PRIVATE RESPONSE TOKEN/);
  }
});

test('unknown survives restart and duplicate ask cannot dispatch; exact inspect can recover it',async t=>{
  let available=false;const f=fixture(t,async(route,body,{saved})=>{if(body||!available)throw Error('offline');return receipt(saved.get(route.split('/').at(-1)));});
  f.host.saveDraft({text:'帮我分析有哪些任务需要关注',expectedRevision:0});const result=await f.ask();assert.equal(result.status,'UNKNOWN');
  assert.equal(f.host.snapshot().draft.text,'帮我分析有哪些任务需要关注');const g=new SupervisorConversation({dataDir:f.dataDir,request:f.request});
  assert.equal((await g.ask({operationId:'question_one',text:'帮我分析有哪些任务需要关注',providerProfileRef:profile})).status,'UNKNOWN');assert.equal(f.calls.filter(row=>row.body).length,1);
  available=true;const recovered=await g.inspect('question_one');assert.equal(recovered.status,'RETURNED');assert.equal(f.calls.filter(row=>row.body).length,1);
});

test('wrong scope, profile, request hash or missing producer fields keep unknown without adopting foreign text',async t=>{
  for(const mismatch of [{project_id:'foreign'},{conversation_id:'foreign'},{context_ref:{id:'screen'}},{file_source_ref:{id:'file'}},{file_link_ref:{id:'file'}},{request_sha256:'b'.repeat(64)},{request_sha256:undefined},{provider_profile_ref:{...profile,revision:3}},{automatic_retry:true}]){
    const f=fixture(t,async(route,body,{saved})=>receipt(body??saved.get(route.split('/').at(-1)),{text:'FOREIGN ANSWER',...mismatch}));
    assert.equal((await f.ask()).status,'UNKNOWN');assert.doesNotMatch(JSON.stringify(f.host.snapshot()),/FOREIGN ANSWER/);assert.equal(f.calls.filter(row=>row.body).length,1);
  }
});

test('processing is not completion; late completion preserves newer user draft and read failure cannot erase a final reply',async t=>{
  let status='PROCESSING';const f=fixture(t,async(route,body,{saved})=>{if(status==='OFFLINE')throw Error('offline');return receipt(body??saved.get(route.split('/').at(-1)),{status,text:status==='RETURNED'?'实际答复':''});});
  f.host.saveDraft({text:'帮我分析有哪些任务需要关注',expectedRevision:0});assert.equal((await f.ask()).status,'PROCESSING');
  f.host.saveDraft({text:'我现在继续补充',expectedRevision:f.host.snapshot().draft.revision});status='RETURNED';assert.equal((await f.host.inspect('question_one')).status,'RETURNED');
  assert.equal(f.host.snapshot().draft.text,'我现在继续补充');status='OFFLINE';assert.equal((await f.host.inspect('question_one')).status,'RETURNED');assert.match(f.host.snapshot().messages[1].text,/实际答复/);
});

test('bounded context contains at most eight real task metadata rows and no arbitrary private source fields',()=>{
  const input=observation(Array.from({length:100},(_,index)=>task(index,{label:'工程任务'+index+'文'.repeat(180),secret:'DO_NOT_COPY',classification:index===99?'EXECUTION_ERROR':'RUNNING'})));
  const value=boundedSupervisorSnapshot(input);assert.equal(value.tasks.length,8);assert.equal(value.total_tasks,100);assert.equal(value.tasks_truncated,true);assert.equal(value.tasks[0].key.object_id,'task_99');
  assert.doesNotMatch(JSON.stringify(value),/DO_NOT_COPY/);assert.deepEqual(value.counts,{running:99,attention:1,recent:0});
  assert.throws(()=>boundedSupervisorSnapshot(observation([task(1),task(1)])),/SUPERVISOR_SNAPSHOT_INVALID/);
});

test('large task lists and prior discussion are bounded in the wire, while original user Unicode text remains exact',async t=>{
  const f=fixture(t);for(let i=0;i<4;i++)f.host.persistLocal({question:'此前问题'+i,answer:'答'.repeat(4000)});
  const question='😀'.repeat(7900)+'请分析';await f.ask('unicode_question',question);const body=f.calls.find(row=>row.body).body;
  assert.ok(Array.from(body.raw_text).length<=12000);assert.ok(body.raw_text.endsWith(question));assert.equal(f.host.snapshot().messages.at(-2).text,question);
});

test('oversized answers have explicit bounded projection and exact digest, never grow state past its cap',async t=>{
  const answer='原答复。'.repeat(10000);const f=fixture(t,async(_route,body)=>receipt(body,{text:answer,private_token:'PRIVATE_DO_NOT_STORE'}));
  const result=await f.ask();assert.equal(result.status,'RETURNED');assert.equal(result.text_truncated,true);assert.ok(Buffer.byteLength(result.text)<=16000);
  assert.ok(fs.statSync(f.host.file).size<=MAX_SUPERVISOR_STATE_BYTES);assert.doesNotMatch(fs.readFileSync(f.host.file,'utf8'),/PRIVATE_DO_NOT_STORE/);
  assert.equal(JSON.parse(fs.readFileSync(f.host.file,'utf8')).state.operations[0].answer_sha256,digest(answer));
});

test('archive write ENOSPC fails before dispatch and preserves previously committed head and unsent draft',async t=>{
  const f=fixture(t);f.host.saveDraft({text:'问'.repeat(8000),expectedRevision:0});let full=false;
  const open=fs.openSync,write=fs.writeFileSync,close=fs.closeSync,archiveFds=new Set();
  fs.openSync=(target,...args)=>{const fd=open(target,...args);if(typeof target==='string'&&target.includes('supervisor-conversation.archive.v1'))archiveFds.add(fd);return fd;};
  fs.closeSync=fd=>{archiveFds.delete(fd);return close(fd);};
  fs.writeFileSync=(target,...args)=>{if(archiveFds.has(target))throw Object.assign(Error('disk full'),{code:'ENOSPC'});return write(target,...args);};
  try{
    for(let index=0;index<64;index++){try{f.host.persistLocal({question:'问题'+index,answer:'答'.repeat(5000)});}catch(error){assert.equal(error.code,'SUPERVISOR_STORAGE_FULL');full=true;break;}}
    assert.equal(full,true);const before=fs.readFileSync(f.host.file),basis=f.host.snapshot(),result=await f.ask('no_capacity','问'.repeat(8000));
    assert.equal(result.status,'REJECTED');assert.equal(result.dispatch_proof.outcome,'NOT_DISPATCHED');assert.equal(result.dispatch_proof.basis.sha256,basis.state_sha256);assert.equal(result.dispatch_proof.input_sha256,digest({operation_id:'no_capacity',text:'问'.repeat(8000),provider_profile_ref:profile}));assert.equal(f.calls.filter(row=>row.body).length,0);assert.deepEqual(fs.readFileSync(f.host.file),before);assert.equal(f.host.snapshot().draft.text,'问'.repeat(8000));
  }finally{fs.openSync=open;fs.writeFileSync=write;fs.closeSync=close;}
});

test('closing rejects new work, joins the original in-flight ask, and never cancels another task',async t=>{
  let release;const gate=new Promise(resolve=>release=resolve);const f=fixture(t,async(_route,body)=>{await gate;return receipt(body);});
  const pending=f.ask();await tick();const closing=f.host.close();assert.throws(()=>f.ask('next'),error=>error.code==='SUPERVISOR_CLOSED');
  release();await pending;await closing;assert.equal(f.host.closed,true);assert.equal(f.calls.filter(row=>row.body).length,1);assert.ok(!f.calls.some(row=>/cancel|retry/.test(row.route)));
});

test('concurrent inspect requests share one original GET and a late processing POST cannot downgrade a read final reply',async t=>{
  let releaseRead,releasePost,readWait=false;
  const readGate=new Promise(resolve=>releaseRead=resolve),postGate=new Promise(resolve=>releasePost=resolve);
  const f=fixture(t,async(route,body,{saved})=>{if(body){await postGate;return receipt(body,{status:'PROCESSING',text:''});}if(readWait)await readGate;return receipt(saved.get(route.split('/').at(-1)));});
  const asking=f.ask();await tick();readWait=true;const first=f.host.inspect('question_one'),second=f.host.inspect('question_one');assert.equal(first,second);
  releaseRead();assert.equal((await first).status,'RETURNED');releasePost();assert.equal((await asking).status,'RETURNED');
  assert.equal(f.calls.filter(row=>/native-messages\/question_one$/.test(row.route)).length,1);assert.equal(f.calls.filter(row=>row.body).length,1);
});

test('pending operation remains reachable despite many zero-model local queries after restart',async t=>{
  const f=fixture(t,async()=>{throw Error('offline');});assert.equal((await f.ask()).status,'UNKNOWN');
  for(let index=0;index<20;index++)f.host.persistLocal({question:'进度 '+index,answer:'本地观察'});
  const value=new SupervisorConversation({dataDir:f.dataDir,request:f.request}).snapshot();assert.equal(value.messages.length,24);
  assert.ok(value.messages.some(row=>row.operation_id==='question_one'&&row.status==='UNKNOWN'));
  assert.equal(f.calls.filter(row=>row.body).length,1);
});

test('unknown blocks the same question/profile under a new key, but permits a different explicit question in the same conversation',async t=>{
  let offline=true;const f=fixture(t,async(route,body,{saved})=>{if(offline)throw Error('lost');return receipt(body??saved.get(route.split('/').at(-1)));});
  assert.equal((await f.ask('original_unknown','原来的问题')).status,'UNKNOWN');
  assert.throws(()=>f.ask('renamed_replay','原来的问题'),error=>error.code==='SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED');
  offline=false;const result=await f.ask('different_question','现在我只想了解另一个问题');assert.equal(result.status,'RETURNED');
  const state=f.host.snapshot();assert.equal(state.operations.find(row=>row.operation_id==='original_unknown').status,'UNKNOWN');
  assert.equal(f.saved.get('original_unknown').conversation_id,f.saved.get('different_question').conversation_id);
  assert.equal(f.calls.filter(row=>row.body&&row.body.operation_id==='original_unknown').length,1);
  assert.equal((await f.host.inspect('original_unknown')).status,'RETURNED');
});

test('an early inspect 404 cannot permit a parallel new POST while the original native send is still in flight',async t=>{
  let release;const gate=new Promise(resolve=>release=resolve);const f=fixture(t,async(_route,body)=>{if(body){await gate;return receipt(body);}throw Error('not recorded yet');});
  const original=f.ask();await tick();assert.equal(f.host.snapshot().operations[0].status,'REQUESTING');
  assert.equal((await f.host.inspect('question_one')).status,'UNKNOWN');assert.throws(()=>f.ask('parallel_new','另一个问题'),error=>error.code==='SUPERVISOR_OPERATION_PENDING');
  release();await original;assert.equal(f.calls.filter(row=>row.body).length,1);
});


test('historical question getter opens an original task after more than 24 history messages, rejects wrong hashes and never dispatches',async t=>{
 const f=fixture(t),question='更早的总管原文😀\r\nUSER QUESTION:\n用户仍可写这句  ';
 await f.ask('historical_original',question);
 for(let i=0;i<26;i++)f.host.persistLocal({question:'后续本地问题 '+i,answer:'本地状态 '+i});
 const snapshot=f.host.snapshot(),op=snapshot.operations.find(row=>row.operation_id==='historical_original');
 assert.equal(snapshot.messages.length,24);assert.equal(snapshot.messages.some(row=>row.operation_id==='historical_original'),false);
 const before=fs.readFileSync(f.host.file),calls=f.calls.length;
 assert.equal(f.host.questionForOperation(op.operation_id,op.request_sha256),question);
 const badHash=(op.request_sha256[0]==='0'?'1':'0')+op.request_sha256.slice(1);
 for(const bad of [badHash,null,undefined,'bad','A'.repeat(64),{toString:()=>op.request_sha256}])assert.throws(()=>f.host.questionForOperation(op.operation_id,bad));
 assert.throws(()=>f.host.questionForOperation('missing_operation',op.request_sha256));assert.throws(()=>f.host.questionForOperation('../bad',op.request_sha256));
 assert.equal(f.calls.length,calls);assert.deepEqual(fs.readFileSync(f.host.file),before);
 const restored=new SupervisorConversation({dataDir:f.dataDir,request:f.request});assert.equal(restored.questionForOperation(op.operation_id,op.request_sha256),question);
 const {readSupervisorTaskDetail}=require('../supervisor-host-integration.cjs');
 const selected={kind:'native_message',project_id:null,object_id:op.operation_id,operation_id:op.operation_id,generation:7};
 const task={key:selected,label:'original native question',source_status:'RETURNED',source_ref:{id:op.operation_id,revision:1,sha256:digest({request_sha256:op.request_sha256,status:'RETURNED'})}};
 let reads=0;const actual=await readSupervisorTaskDetail({store:restored,key:selected,snapshot:{tasks:[task],alerts:[]},label:'总管问题',request:async route=>{reads++;assert.match(route,/^\/api\/supervisor\/task-detail\?/);return {key:selected,task,input_text:f.saved.get(op.operation_id).raw_text,answer_text:'原答复'};}});
 assert.equal(reads,1);assert.equal(actual.input_text,question);assert.equal(actual.task.label,'总管问题');assert.equal(f.calls.length,calls);assert.deepEqual(fs.readFileSync(f.host.file),before);
});

const followup=(host,operationId)=>{const op=host.snapshot().operations.find(row=>row.operation_id===operationId);return {operation_id:op.operation_id,request_sha256:op.request_sha256};};
const wireContext=body=>JSON.parse(body.raw_text.slice(body.raw_text.indexOf('\n')+1,body.raw_text.lastIndexOf('\nUSER QUESTION:\n')));

test('old three-field asks keep the original input hash and no followup fields after restart',async t=>{
 const f=fixture(t),question='旧调用保持兼容';await f.ask('legacy_ask',question);
 const op=JSON.parse(fs.readFileSync(f.host.file,'utf8')).state.operations[0];
 assert.equal(op.input_sha256,digest({operation_id:'legacy_ask',text:question,provider_profile_ref:profile}));assert.equal(Object.hasOwn(op,'followup_ref'),false);
 assert.equal(Object.hasOwn(f.host.snapshot().operations[0],'followup_ref'),false);assert.equal(Object.hasOwn(wireContext(f.saved.get('legacy_ask')),'selected_discussion'),false);
 const restored=new SupervisorConversation({dataDir:f.dataDir,request:f.request}),before=f.calls.length;
 await restored.ask({operationId:'legacy_ask',text:question,providerProfileRef:profile});assert.equal(f.calls.length,before);
});

test('followup explicitly includes the old selected question and answer outside 24 messages without injecting fake history or authority',async t=>{
 const f=fixture(t),original='早先的原问题😀\r\n保留这两个空格  ';await f.ask('old_selected',original);
 for(let i=0;i<14;i++)f.host.persistLocal({question:'后来的本地话题'+i,answer:'最近状态'+i});
 assert.ok(!f.host.snapshot().messages.some(row=>row.operation_id==='old_selected'));
 const reference=followup(f.host,'old_selected'),before=f.calls.length,question='继续解释';
 await f.host.ask({operationId:'continue_selected',text:question,providerProfileRef:profile,followupRef:reference});
 const body=f.saved.get('continue_selected'),context=wireContext(body),selected=context.selected_discussion;
 assert.deepEqual(selected,{...reference,question:original,answer:'原模型的简短答复',status:'RETURNED',source_status:'RETURNED',question_truncated:false,answer_truncated:false});
 assert.equal(f.calls.length-before,2);assert.ok(!f.calls.slice(before).some(row=>/native-messages\//.test(row.route)));assert.equal(body.context_ref,null);assert.equal(body.project_id,null);assert.equal(body.file_source_ref,null);assert.equal(body.file_link_ref,null);
 assert.equal(body.raw_text.endsWith('\nUSER QUESTION:\n'+question),true);assert.equal(f.host.snapshot().messages.find(row=>row.role==='user'&&row.operation_id==='continue_selected').text,question);
 assert.ok(!f.host.snapshot().messages.some(row=>row.operation_id==='old_selected'),'selecting old context does not fabricate a recent message');
 const op=JSON.parse(fs.readFileSync(f.host.file,'utf8')).state.operations.find(row=>row.operation_id==='continue_selected');
 assert.deepEqual(op.followup_ref,reference);assert.equal(op.input_sha256,digest({operation_id:'continue_selected',text:question,provider_profile_ref:profile,followup_ref:reference}));
 assert.deepEqual(f.host.snapshot().operations.find(row=>row.operation_id==='continue_selected').followup_ref,reference);
 const projected=f.host.snapshot().operations.find(row=>row.operation_id==='continue_selected').followup_ref;projected.request_sha256='f'.repeat(64);assert.deepEqual(followup(f.host,'old_selected'),reference);assert.deepEqual(f.host.snapshot().operations.find(row=>row.operation_id==='continue_selected').followup_ref,reference);
});

test('wrong hash, foreign operation or malformed followup is rejected before any metadata GET or POST',async t=>{
 const f=fixture(t);await f.ask('valid_target','原问题');const reference=followup(f.host,'valid_target'),before=f.calls.length,bytes=fs.readFileSync(f.host.file);
 for(const value of [null,{},[],reference.operation_id,{...reference,request_sha256:'f'.repeat(64)},{...reference,operation_id:'foreign_operation'},{...reference,operation_id:'../bad'},
  {...reference,request_sha256:'A'.repeat(64)},{...reference,kind:'supervisor'},{...reference,project_id:'foreign'}, {...reference,request_sha256:{toString:()=>reference.request_sha256}}]){
  assert.throws(()=>f.host.ask({operationId:'invalid_followup',text:'继续解释',providerProfileRef:profile,followupRef:value}));
 }
 assert.equal(f.calls.length,before);assert.deepEqual(fs.readFileSync(f.host.file),bytes);assert.equal(f.host.snapshot().operations.length,1);
});

test('same operation id binds its followup during pending and durable replay, including restart',async t=>{
 let release;const gate=new Promise(resolve=>release=resolve),f=fixture(t,async(route,body,{saved})=>{if(body?.operation_id==='bound_followup')await gate;return receipt(body??saved.get(route.split('/').at(-1)));});
 await f.ask('target_a','原问题A');await f.ask('target_b','原问题B');const a=followup(f.host,'target_a'),b=followup(f.host,'target_b');
 const input={operationId:'bound_followup',text:'继续解释',providerProfileRef:profile,followupRef:a},first=f.host.ask(input);assert.equal(first,f.host.ask(input));
 assert.throws(()=>f.host.ask({...input,followupRef:b}),error=>error.code==='SUPERVISOR_OPERATION_CONFLICT');assert.throws(()=>f.host.ask({...input,followupRef:undefined}),error=>error.code==='SUPERVISOR_OPERATION_CONFLICT');
 release();await first;const before=f.calls.length;await f.host.ask(input);assert.equal(f.calls.length,before);
 const restored=new SupervisorConversation({dataDir:f.dataDir,request:f.request});await restored.ask(input);assert.equal(f.calls.length,before);
 assert.throws(()=>restored.ask({...input,followupRef:b}),error=>error.code==='SUPERVISOR_OPERATION_CONFLICT');assert.equal(f.calls.filter(row=>row.body?.operation_id==='bound_followup').length,1);
});

test('UNKNOWN same text and profile remain blocked under another followup target or new key',async t=>{
 let unavailable=false;const f=fixture(t,async(route,body,{saved})=>{if(unavailable)throw Error('fixture offline');return receipt(body??saved.get(route.split('/').at(-1)));});
 await f.ask('target_a','原问题A');await f.ask('target_b','原问题B');const a=followup(f.host,'target_a'),b=followup(f.host,'target_b');unavailable=true;
 assert.equal((await f.host.ask({operationId:'unknown_followup',text:'继续解释',providerProfileRef:profile,followupRef:a})).status,'UNKNOWN');const before=f.calls.length;
 for(const target of [a,b,undefined])assert.throws(()=>f.host.ask({operationId:'new_key',text:'继续解释',providerProfileRef:profile,...target?{followupRef:target}:{}}),error=>error.code==='SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED');
 assert.equal(f.calls.length,before);assert.equal(f.calls.filter(row=>row.body?.operation_id==='unknown_followup').length,1);
 unavailable=false;await f.host.ask({operationId:'explicit_different_question',text:'请另外说明第二个问题的依据',providerProfileRef:profile,followupRef:b});assert.equal(f.host.snapshot().operations.find(row=>row.operation_id==='unknown_followup').status,'UNKNOWN');
});

test('selected context shrink terminates for Chinese, emoji, escaped controls and backslashes within the unchanged total budget',async t=>{
 for(const seed of ['中文段落','😀🧪','\u0000\n\r\t','\\"']){
  const answer=seed.repeat(Math.floor(18000/seed.length)),f=fixture(t,async(_route,body)=>receipt(body,{text:body.operation_id==='unicode_target'?answer:'追问答复'})),original=seed.repeat(Math.floor(7900/Array.from(seed).length));
  await f.ask('unicode_target',original);const reference=followup(f.host,'unicode_target'),question='😀'.repeat(7900)+'继续解释';
  await f.host.ask({operationId:'unicode_followup',text:question,providerProfileRef:profile,followupRef:reference});
  const body=f.saved.get('unicode_followup'),selected=wireContext(body).selected_discussion;
  assert.ok(Array.from(body.raw_text).length<=12000);assert.ok(body.raw_text.endsWith(question));assert.equal(selected.operation_id,'unicode_target');assert.equal(selected.request_sha256,reference.request_sha256);
  assert.equal(selected.question_truncated,true);assert.equal(selected.answer_truncated,true);assert.equal(f.host.displayForOperation(reference.operation_id,reference.request_sha256).question,original);
  assert.ok(fs.statSync(f.host.file).size<=MAX_SUPERVISOR_STATE_BYTES);assert.equal(f.calls.filter(row=>row.body).length,2);assert.ok(!f.calls.some(row=>/native-messages\//.test(row.route)));
  t.diagnostic(JSON.stringify({seed,wire_chars:Array.from(body.raw_text).length,selected_question_chars:Array.from(selected.question).length,selected_answer_chars:Array.from(selected.answer).length,state_bytes:fs.statSync(f.host.file).size}));
 }
});

test('followup source is revalidated after the metadata read and no changed reference is posted or committed',async t=>{
 const f=fixture(t);await f.ask('target','原问题');const reference=followup(f.host,'target'),original=f.host.request,before=fs.readFileSync(f.host.file);let release;
 const gate=new Promise(resolve=>release=resolve);f.host.request=async(route,body)=>{if(route==='/api/supervisor')await gate;return original(route,body);};
 const pending=f.host.ask({operationId:'changed_target_followup',text:'继续解释',providerProfileRef:profile,followupRef:reference});
 f.host.state.operations.find(row=>row.operation_id==='target').request_sha256='f'.repeat(64);release();await assert.rejects(pending,error=>error.code==='SUPERVISOR_QUESTION_REFERENCE_CHANGED');
 assert.equal(f.calls.filter(row=>row.body?.operation_id==='changed_target_followup').length,0);assert.deepEqual(fs.readFileSync(f.host.file),before);
});

test('persisted followup refs validate shape, original hash and input-hash binding while retaining legacy records',async t=>{
 const f=fixture(t);await f.ask('target_a','原问题A');await f.ask('target_b','原问题B');const reference=followup(f.host,'target_a');
 await f.host.ask({operationId:'followup',text:'继续解释',providerProfileRef:profile,followupRef:reference});const before=fs.readFileSync(f.host.file),original=JSON.parse(before);
 const restored=new SupervisorConversation({dataDir:f.dataDir,request:f.request});assert.deepEqual(restored.snapshot().operations.find(row=>row.operation_id==='followup').followup_ref,reference);
 assert.equal(Object.hasOwn(restored.snapshot().operations.find(row=>row.operation_id==='target_a'),'followup_ref'),false);
 for(const changed of [{...reference,operation_id:'foreign'},{...reference,request_sha256:'f'.repeat(64)},{...reference,kind:'supervisor'},followup(f.host,'target_b'),null]){
  const envelope=JSON.parse(before);envelope.state.operations.find(row=>row.operation_id==='followup').followup_ref=changed;envelope.sha256=digest(envelope.state);fs.writeFileSync(f.host.file,JSON.stringify(envelope)+'\n');
  assert.throws(()=>new SupervisorConversation({dataDir:f.dataDir,request:f.request}),/SUPERVISOR_FOLLOWUP_REFERENCE_INVALID/);
 }
 fs.writeFileSync(f.host.file,before);assert.deepEqual(JSON.parse(fs.readFileSync(f.host.file,'utf8')),original);
});
