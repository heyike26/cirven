"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),{createHash}=require('node:crypto');
const {projectSupervisorSnapshot,readSupervisorTaskDetail,ownedSupervisorOperation}=require('../supervisor-host-integration.cjs');
const label='总管问题',operation='same_operation',requestSha='a'.repeat(64);
const key=(extra={})=>({kind:'native_message',project_id:null,object_id:operation,operation_id:operation,generation:7,...extra});
const basis=(request=requestSha,status='PROCESSING')=>({id:operation,revision:1,sha256:createHash('sha256').update(JSON.stringify({request_sha256:request,status})).digest('hex')});
const task=(extra={})=>({key:key(),label:'当前页问答 · same_ope',source_status:'PROCESSING',source_ref:basis(),classification:'RUNNING',...extra});
const alert=(extra={})=>({id:'alert_one',key:key(),label:'当前页问答 · same_ope',source_status:'PROCESSING',category:'RUNNING',...extra});
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};
function store(question='我的原问题😀\r\n继续保留。',operations=[{operation_id:operation,request_sha256:requestSha,status:'PROCESSING'}]){
 const value=freeze({operations,messages:[{role:'user',operation_id:operation,text:question}]});return {supervisorConversationTestDouble:true,snapshot:()=>value};
}
const observation=(tasks=[task()],alerts=[alert()])=>({tasks,alerts,observer:{status:'READY'},extra:'retain'});
const detail=(selected=key(),extra={})=>({key:selected,task:task({key:selected}),input_text:'INTERNAL WIRE ENVELOPE',answer_text:'原答复',artifacts:[],automatic_retry:false,execution_authorized:false,...extra});
const read=(options={})=>readSupervisorTaskDetail({store:store(),key:key(),snapshot:observation(),request:async()=>detail(),label,...options});

test('snapshot relabels only verified own global tasks and matching alerts without touching original objects',()=>{
 const owned=task(),foreignProject=task({key:key({project_id:'another_project'}),label:'project KEEP'}),foreignKind=task({key:key({kind:'planning'}),label:'planning KEEP'}),foreignObject=task({key:key({object_id:'another_object'}),label:'object KEEP'});
 const goodAlert=alert(),projectAlert=alert({id:'p',key:foreignProject.key,label:'project alert KEEP'}),oldGenerationAlert=alert({id:'old',key:key({generation:8}),label:'unmatched generation KEEP'});
 const input=freeze(observation([owned,foreignProject,foreignKind,foreignObject],[goodAlert,projectAlert,oldGenerationAlert]));const before=JSON.stringify(input);
 const output=projectSupervisorSnapshot(store(),input,label);
 assert.equal(output.tasks[0].label,label);assert.equal(output.alerts[0].label,label);
 assert.equal(output.tasks[1].label,'project KEEP');assert.equal(output.tasks[2].label,'planning KEEP');assert.equal(output.tasks[3].label,'object KEEP');
 assert.equal(output.alerts[1].label,'project alert KEEP');assert.equal(output.alerts[2].label,'unmatched generation KEEP');
 assert.equal(JSON.stringify(input),before);assert.notEqual(output,input);assert.notEqual(output.tasks[0],input.tasks[0]);assert.equal(output.extra,'retain');
});

test('same operation with another or missing request basis never becomes a supervisor task or alert',()=>{
 for(const source of [basis('b'.repeat(64)),null,{...basis(),revision:2},{...basis(),id:'other_operation'}]){
  const input=freeze(observation([task({source_ref:source})],[alert()]));const before=JSON.stringify(input);
  const output=projectSupervisorSnapshot(store(),input,label);
  assert.equal(output.tasks[0].label,input.tasks[0].label);assert.equal(output.alerts[0].label,input.alerts[0].label);assert.equal(JSON.stringify(input),before);
 }
});

test('no store or no snapshot does not invent ownership, and a newer valid execution observation keeps the same original basis',()=>{
 assert.equal(projectSupervisorSnapshot(store(),null,label),null);
 const input=freeze(observation());assert.deepEqual(projectSupervisorSnapshot(undefined,input,label),input);
 const returned=task({source_status:'RETURNED',source_ref:basis(requestSha,'RETURNED'),classification:'EXECUTION_RETURNED'});
 assert.equal(projectSupervisorSnapshot(store(),observation([returned],[]),label).tasks[0].label,label);
 assert.equal(ownedSupervisorOperation(store(),task()).request_sha256,requestSha);
 assert.equal(ownedSupervisorOperation(store(),task({key:key({project_id:'another_project'})})),undefined);assert.equal(ownedSupervisorOperation(store(),task({key:key({kind:'execution'})})),undefined);
});

test('detail checks exact task identity and uses the original own user question without normalizing or mutating wire records',async()=>{
 const question='You are the application-wide Cirven coordinator. 这是用户原话\r\nUSER QUESTION:\n请原样保留😀  ';const incoming=freeze(detail()),input=freeze(observation()),calls=[];const before=JSON.stringify(incoming);
 const output=await read({store:store(question),snapshot:input,request:async(...args)=>{calls.push(args);return incoming;}});
 assert.equal(output.input_text,question);assert.equal(output.task.label,label);assert.equal(output.answer_text,'原答复');assert.equal(output.automatic_retry,false);assert.equal(output.execution_authorized,false);
 assert.equal(incoming.input_text,'INTERNAL WIRE ENVELOPE');assert.equal(JSON.stringify(incoming),before);assert.notEqual(output,incoming);assert.notEqual(output.task,incoming.task);
 assert.equal(calls.length,1);assert.equal(calls[0].length,1);const url=new URL(calls[0][0],'http://local.invalid');assert.equal(url.pathname,'/api/supervisor/task-detail');
 assert.equal(url.searchParams.get('kind'),'native_message');assert.equal(url.searchParams.get('object_id'),operation);assert.equal(url.searchParams.get('operation_id'),operation);assert.equal(url.searchParams.get('generation'),'7');assert.equal(url.searchParams.has('project_id'),false);
});

test('ordinary user text that starts like the coordinator wire is never stripped or replaced by an operation-id collision',async()=>{
 for(const selected of [key({project_id:'project'}),key({kind:'planning'}),key({object_id:'ordinary_operation',operation_id:'ordinary_operation'})]){
  const text='You are the application-wide Cirven coordinator.\nUSER QUESTION:\n这只是普通用户文字😀';const incoming=freeze(detail(selected,{input_text:text,task:task({key:selected,label:'ordinary KEEP'})}));
  const output=await read({key:selected,snapshot:observation([task({key:selected})],[]),request:async()=>incoming});assert.equal(output,incoming);assert.equal(output.input_text,text);assert.equal(output.task.label,'ordinary KEEP');
 }
});

test('missing selected task and wrong response identity reject, while the original API failure propagates exactly without local fallback',async()=>{
 let calls=0;await assert.rejects(read({snapshot:observation([],[]),request:async()=>{calls++;return detail();}}),/SUPERVISOR_TASK_REFERENCE_CHANGED/);assert.equal(calls,0);
 for(const selected of [key({project_id:'project'}),key({kind:'planning'}),key({generation:8}),key({object_id:'other'})])await assert.rejects(read({request:async()=>detail(selected)}),/SUPERVISOR_TASK_REFERENCE_CHANGED/);
 const failure=Object.assign(Error('original API offline'),{code:'ORIGINAL_CONNECTION_LOST'});await assert.rejects(read({request:async()=>{throw failure;}}),error=>error===failure);
});

test('detail rejects nested foreign task identity or missing real task rather than fabricating a labelled task',async()=>{
 for(const value of [detail(key(),{task:task({key:key({project_id:'foreign'})})}),detail(key(),{task:undefined}),detail(key(),{task:null})]){
  await assert.rejects(read({request:async()=>value}),/SUPERVISOR_TASK_REFERENCE_CHANGED/);
 }
});

test('detail cannot replace another request basis with this store user question even if all operation-key fields collide',async()=>{
 const value=freeze(detail(key(),{task:task({source_ref:basis('b'.repeat(64))})}));const before=JSON.stringify(value);
 await assert.rejects(read({request:async()=>value}),/SUPERVISOR_TASK_REFERENCE_CHANGED/);assert.equal(JSON.stringify(value),before);
});

test('missing own user question fails closed instead of displaying the internal envelope as a fabricated normal answer',async()=>{
 await assert.rejects(read({store:{snapshot:()=>({operations:[{operation_id:operation,request_sha256:requestSha}],messages:[]})}}),/SUPERVISOR_QUESTION_UNAVAILABLE/);
});


test('snapshot projection reads the store once for many task rows and keeps the exact request-basis checks',()=>{
 const operations=Array.from({length:64},(_,i)=>({operation_id:'op_'+i,request_sha256:i.toString(16).padStart(64,'0'),status:'PROCESSING'}));
 const rows=operations.map(op=>task({key:key({object_id:op.operation_id,operation_id:op.operation_id}),source_ref:{...basis(op.request_sha256),id:op.operation_id}}));
 const input=freeze(observation(rows,rows.map((row,i)=>alert({id:'alert_'+i,key:row.key}))));const source=freeze({operations,messages:[]});let snapshots=0;
 const output=projectSupervisorSnapshot({snapshot:()=>{snapshots++;return source;}},input,label);
 assert.equal(snapshots,1);assert.ok(output.tasks.every(row=>row.label===label));assert.ok(output.alerts.every(row=>row.label===label));assert.ok(input.tasks.every(row=>row.label!==label));
});

test('detail prefers the strict question getter for an old question outside the snapshot message window',async()=>{
 const question='更早的原问题😀\r\n保留我输入的尾部空格  ',calls=[];let snapshots=0;
 const selectedStore={snapshot:()=>{snapshots++;return {operations:[{operation_id:operation,request_sha256:requestSha}],messages:[]};},questionForOperation:(...args)=>{calls.push(args);return question;}};
 const value=await read({store:selectedStore});assert.equal(value.input_text,question);assert.deepEqual(calls,[[operation,requestSha]]);assert.equal(snapshots,2);
});

test('unmarked snapshot-only objects cannot supply an original question and strict getter errors never fall back to wire text',async()=>{
 const legacy=store('snapshot text is not a production getter');delete legacy.supervisorConversationTestDouble;
 await assert.rejects(read({store:legacy}),/SUPERVISOR_QUESTION_UNAVAILABLE/);
 const failure=Object.assign(Error('SUPERVISOR_QUESTION_REFERENCE_CHANGED'),{code:'SUPERVISOR_QUESTION_REFERENCE_CHANGED'});
 const selectedStore={...store('do not use fallback'),questionForOperation:()=>{throw failure;}};
 await assert.rejects(read({store:selectedStore}),error=>error===failure);
});

test('only a twice-verified own operation receives an exact minimal supervisor continuation',async()=>{
 const incoming=freeze(detail(key(),{continuation:{kind:'foreign',operation_id:'wrong',request_sha256:'f'.repeat(64),authority:'FORBIDDEN'}}));
 const output=await read({request:async()=>incoming});
 assert.deepEqual(output.continuation,{kind:'supervisor',operation_id:operation,request_sha256:requestSha});
 assert.deepEqual(Object.keys(output.continuation).sort(),['kind','operation_id','request_sha256']);
 assert.equal(output.input_text,'我的原问题😀\r\n继续保留。');assert.equal(output.answer_text,incoming.answer_text);assert.equal(output.task.source_status,'PROCESSING');
 assert.equal(incoming.input_text,'INTERNAL WIRE ENVELOPE');assert.equal(incoming.continuation.kind,'foreign');
 for(const selected of [key({project_id:'other'}),key({kind:'planning'}),key({object_id:'other',operation_id:'other'})]){
  const ordinary=freeze(detail(selected,{task:task({key:selected})}));
  const value=await read({key:selected,snapshot:observation([task({key:selected})],[]),request:async()=>ordinary});
  assert.equal(Object.hasOwn(value,'continuation'),false);assert.equal(value,ordinary);
 }
});

test('external detail cannot forge a supervisor continuation for an unowned operation',async()=>{
 for(const change of [{key:key({project_id:'other'})},{source_ref:basis('b'.repeat(64))},{source_ref:null}]){
  const row=task(change),incoming=freeze(detail(row.key,{task:row,continuation:{kind:'supervisor',operation_id:operation,request_sha256:requestSha}}));
  const before=JSON.stringify(incoming),output=await read({key:row.key,snapshot:observation([row],[]),request:async()=>incoming});
  assert.equal(Object.hasOwn(output,'continuation'),false);assert.equal(output.input_text,incoming.input_text);assert.equal(output.answer_text,incoming.answer_text);
  assert.equal(JSON.stringify(incoming),before);assert.notEqual(output,incoming);
 }
});

test('a changed nested task basis or strict question getter failure never emits a continuation',async()=>{
 const incoming=freeze(detail(key(),{task:task({source_ref:basis('b'.repeat(64))})}));
 await assert.rejects(read({request:async()=>incoming}),/SUPERVISOR_TASK_REFERENCE_CHANGED/);
 assert.equal(Object.hasOwn(incoming,'continuation'),false);
 const failure=Error('SUPERVISOR_QUESTION_REFERENCE_CHANGED');
 await assert.rejects(read({store:{...store(),questionForOperation:()=>{throw failure;}}}),error=>error===failure);
});

test('operation removal, request replacement or ownership appearing during the async detail GET rejects navigation',async()=>{
 for(const scenario of ['removed','replaced','appeared']){
  let operations=scenario==='appeared'?[]:[{operation_id:operation,request_sha256:requestSha,status:'PROCESSING'}],resolve,questions=0;
  const pending=new Promise(yes=>{resolve=yes;}),selectedStore={snapshot:()=>({operations,messages:[]}),questionForOperation:()=>{questions++;return 'must not use a changed owner';}};
  const reading=read({store:selectedStore,request:()=>pending});
  operations=scenario==='removed'?[]:[{operation_id:operation,request_sha256:scenario==='replaced'?'b'.repeat(64):requestSha,status:'PROCESSING'}];
  resolve(detail());await assert.rejects(reading,/SUPERVISOR_TASK_REFERENCE_CHANGED/);assert.equal(questions,0);
 }
});

test('caller mutation during async detail GET cannot redefine the selected key or original source basis',async()=>{
 let operations=[{operation_id:operation,request_sha256:requestSha,status:'PROCESSING'}],resolve;
 const row=task(),input=observation([row],[]),selected=key(),pending=new Promise(yes=>{resolve=yes;});
 const reading=read({store:{snapshot:()=>({operations}),questionForOperation:()=> 'original'},key:selected,snapshot:input,request:()=>pending});
 operations=[{operation_id:operation,request_sha256:'b'.repeat(64),status:'PROCESSING'}];row.source_ref=basis('b'.repeat(64));
 resolve(detail());await assert.rejects(reading,/SUPERVISOR_TASK_REFERENCE_CHANGED/);
 let resolveOther;const next=new Promise(yes=>{resolveOther=yes;}),readingOther=read({key:selected,request:()=>next});
 selected.generation=99;resolveOther(detail(selected,{task:task({key:selected})}));await assert.rejects(readingOther,/SUPERVISOR_TASK_REFERENCE_CHANGED/);
});

test('real persisted question beyond 24 projected messages has an exact continuation and answer without replay or store writes',async t=>{
 const fs=require('node:fs'),path=require('node:path');
 const {SupervisorConversation,supervisorConversationDigest:digest}=require('../supervisor-conversation.cjs');
 const root=process.env.EEAS_TEST_TMPDIR??require('node:os').tmpdir();
 fs.mkdirSync(root,{recursive:true});const dataDir=fs.mkdtempSync(path.join(root,'history-'));let nativeRequests=0;
 const original='更早的原问题😀\r\nUSER QUESTION: 用户原文  ',answer='原操作的精确回答，并非最近回答',profile={id:'profile',revision:1,sha256:'c'.repeat(64)};
 const owner=new SupervisorConversation({dataDir,request:async(url,body)=>{
  nativeRequests++;
  if(url==='/api/supervisor')return {tasks:[],observer:{status:'READY',last_success_at:null}};
  assert.equal(url,'/api/desktop-host/native-messages');assert.equal(body.operation_id,operation);
  return {operation_id:operation,status:'RETURNED',text:answer,automatic_retry:false,project_id:null,conversation_id:body.conversation_id,
   request_sha256:digest(body),provider_profile_ref:profile,context_ref:null,file_source_ref:null,file_link_ref:null};
 }});
 t.after(async()=>{await owner.close();fs.rmSync(dataDir,{recursive:true,force:true});});
 await owner.ask({operationId:operation,text:original,providerProfileRef:profile});
 for(let i=0;i<14;i++)owner.persistLocal({question:'later_'+i,answer:'后来的回答'+i});
 assert.ok(!owner.snapshot().messages.some(row=>row.operation_id===operation));assert.equal(owner.snapshot().messages.length,24);
 const exact=owner.snapshot().operations.find(row=>row.operation_id===operation),row=task({source_status:'RETURNED',source_ref:basis(exact.request_sha256,'RETURNED')}),before=fs.readFileSync(owner.file);
 let reads=0;const value=await read({store:owner,snapshot:observation([row],[]),request:async()=>{reads++;return detail(key(),{task:row,input_text:'INTERNAL WIRE ENVELOPE',answer_text:answer});}});
 assert.deepEqual(value.continuation,{kind:'supervisor',operation_id:operation,request_sha256:exact.request_sha256});
 assert.equal(value.input_text,original);assert.equal(value.answer_text,answer);assert.equal(value.task.source_status,'RETURNED');
 assert.equal(reads,1);assert.equal(nativeRequests,2,'only the original observation and explicit original POST occurred');assert.deepEqual(fs.readFileSync(owner.file),before);
});
