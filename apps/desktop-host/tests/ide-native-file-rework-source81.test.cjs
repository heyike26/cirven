'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path');
const {createIdeApiAdapter,METHODS}=require('../ide-api-adapter.cjs');
const {IdeCompanionBridge,PROTOCOL,METHODS:BRIDGE_METHODS}=require('../ide-companion-bridge.cjs');
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>crypto.createHash('sha256').update(v).digest('hex'),digest=v=>sha(canonical(v));
const s={project_id:'project',conversation_id:'discussion'},parent='original',scope={...s};
const before='\ufeff原句😀\r\n不要改\r\n',after='\ufeff新句😀\r\n不要改\r\n';
const source={kind:'local_file',project_id:s.project_id,workspace_id:'a'.repeat(64),binding_revision:1,binding_sha256:'b'.repeat(64),path:'work/原页.md',revision:1,sha256:sha(before)};
const ref=n=>({id:n.repeat(64),revision:1,sha256:(n==='a'?'c':'a').repeat(64)}),plan=ref('d'),mapping=ref('e'),feedback=ref('f'),proposal={id:'proposal',revision:3,sha256:'a'.repeat(64)};
const file=extra=>({kind:'NATIVE_FILE',...s,parent_operation_id:parent,baseline_file_source_ref:source,file_source_ref:source,availability:'AVAILABLE',encoding:'utf-8',content:before,byte_length:Buffer.byteLength(before),current:true,live_sha256:source.sha256,read_only:true,model_calls:0,...extra});
const preview=extra=>({kind:'NATIVE_REWORK_PREVIEW',...s,parent_operation_id:parent,status:'PROPOSED',plan_ref:plan,mapping_ref:mapping,native_feedback_ref:feedback,proposal_ref:proposal,file_source_ref:source,locator:{kind:'TEXT_RANGE',char_start:1,char_end:4,quote_sha256:sha('原句😀')},write_scope:[{workspace_id:source.workspace_id,path:source.path}],current_basis_status:'CURRENT_TASK_REQUIREMENT_BASIS',already_applied:false,can_apply:true,diff:{before,after,before_sha256:sha(before),after_sha256:sha(after),encoding:'UTF-8',content_scope:'FULL_FILE',read_only:true},model_calls:0,...extra});
const selection={kind:'EXACT_QUOTE',quote:'原句😀'},fbBody={operation_id:'feedback-once',file_source_ref:source,comment:'仅替换原句。',selection};
const fbReceipt=(body=fbBody)=>({kind:'NATIVE_FEEDBACK',...s,parent_operation_id:parent,operation_id:body.operation_id,status:'RECORDED',request:body,request_sha256:digest(body),file_source_ref:body.file_source_ref,feedback_ref:feedback,selection_verification:'TEXT_RANGE_HASH_VERIFIED',file_selection:{kind:'TEXT_RANGE',char_start:1,char_end:4,quote_sha256:sha('原句😀')},requested_selection:body.selection,automatic_retry:false});
const applyBody={operation_id:'apply-once',plan_ref:plan,expected_current_sources:[source],allow_rebase:true};
const applyReceipt=()=>({kind:'NATIVE_REWORK_APPLY',...s,parent_operation_id:parent,operation_id:applyBody.operation_id,status:'APPLIED',request:applyBody,request_sha256:digest(applyBody),plan_ref:plan,receipt_ref:ref('c'),outcomes:[{path:source.path,status:'APPLIED',source_ref:{...source,revision:2,sha256:sha(after)}}],automatic_retry:false});

test('all seven native methods are separately advertised; old grant does not gain file or apply authority',async t=>{
 const methods=['native.file.read','native.file.open','native.feedback.record','native.feedback.operation','native.rework.preview','native.rework.apply','native.rework.operation'];
 for(const m of methods){assert.ok(METHODS.includes(m));assert.ok(BRIDGE_METHODS.includes(m));}
 const d=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR,'bridge81-'));t.after(()=>fs.rmSync(d,{recursive:true,force:true}));let calls=0;
 const b=new IdeCompanionBridge({dataDir:d,version:'test',dispatch:async()=>{calls++;},requestPairing:async()=>({approved:true,methods:['native.operation']})});t.after(()=>b.close());
 const c={client_id:crypto.randomUUID(),client_secret:crypto.randomBytes(32).toString('hex'),client_label:'旧读权限'};
 await b.pair({protocol:PROTOCOL,...c,nonce:b.nonce});await new Promise(r=>setImmediate(r));
 const granted=b.journal.value.clients[c.client_id],result=b.pairingResult(c.client_id);assert.ok(result.supported_methods.includes('native.rework.apply'));assert.deepEqual(result.capabilities.methods,['native.operation']);
 for(const method of methods)await assert.rejects(b.rpc(c.client_id,granted,{protocol:PROTOCOL,request_id:'deny-'+method.replaceAll('.','-'),method,scope:s,payload:{parent_operation_id:parent}}),e=>e.code==='CAPABILITY_DENIED');assert.equal(calls,0);
});

test('file read binds exact original discussion and content SHA; open reuses Main resolver and exact native scope',async()=>{
 const calls=[],opens=[];const adapter=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return file({api_key:'PRIVATE'});},openArtifact:async(body,ctx)=>{opens.push({body,ctx});return {status:'RESOLVED',file_source_ref:source,native_path:'/tmp/qa81/原页.md'};}});
 const read=await adapter('native.file.read',{scope:s,payload:{parent_operation_id:parent}});assert.equal(read.content,before);assert.ok(!JSON.stringify(read).includes('PRIVATE'));
 const open=await adapter('native.file.open',{scope:s,payload:{parent_operation_id:parent,file_source_ref:source,target:'editor'}});assert.equal(open.status,'EDITOR_READY');assert.equal(open.editor_buffer_modified,false);assert.equal(opens.length,1);assert.deepEqual(opens[0].ctx.scope,s);
 await assert.rejects(adapter('native.file.open',{scope:s,payload:{parent_operation_id:parent,file_source_ref:{...source,sha256:'0'.repeat(64)},target:'editor'}}));assert.equal(opens.length,1);assert.ok(calls.every(v=>v.body===undefined));
 for(const bad of [file({conversation_id:'other'}),file({content:'changed'}),file({file_source_ref:{...source,path:'../private'}})]){const a=createIdeApiAdapter({request:async()=>bad});await assert.rejects(a('native.file.read',{scope:s,payload:{parent_operation_id:parent}}));}
});

test('preview displays exact immutable full-file diff and rejects cross-scope, changed hash or broader scope',async()=>{
 const a=createIdeApiAdapter({request:async()=>preview({sdk_error:'PRIVATE'})});const v=await a('native.rework.preview',{scope:s,payload:{parent_operation_id:parent,plan_ref:plan}});assert.equal(v.diff.after,after);assert.equal(v.plan_ref.sha256,plan.sha256);assert.ok(!JSON.stringify(v).includes('PRIVATE'));
 for(const bad of [preview({parent_operation_id:'other'}),preview({plan_ref:{...plan,revision:2}}),preview({write_scope:[{workspace_id:source.workspace_id,path:'other.md'}]}),preview({diff:{...preview().diff,after:'forged'}}),preview({already_applied:true,can_apply:true})]){const adapter=createIdeApiAdapter({request:async()=>bad});await assert.rejects(adapter('native.rework.preview',{scope:s,payload:{parent_operation_id:parent,plan_ref:plan}}));}
});

test('lost feedback or apply POST including 408/429/bare409 permits one exact GET and never replays',async()=>{
 for(const apply of [false,true])for(const status of [undefined,408,429,409]){
  let posts=0,gets=0;const adapter=createIdeApiAdapter({request:async(route,body)=>{if(body){posts++;throw Object.assign(Error('PRIVATE_DIAGNOSTIC'),{status});}gets++;return apply?applyReceipt():fbReceipt();}});
  const result=await adapter(apply?'native.rework.apply':'native.feedback.record',{scope:s,payload:{parent_operation_id:parent,...(apply?{...applyBody,user_approved:true}:fbBody)}});assert.equal(result.status,apply?'APPLIED':'RECORDED');assert.equal(posts,1);assert.equal(gets,1);assert.ok(!JSON.stringify(result).includes('PRIVATE'));
 }
});

test('missing original GET stays UNKNOWN; wrong input cannot be borrowed; false approval makes zero requests',async()=>{
 for(const mismatch of [false,true]){let posts=0,gets=0;const a=createIdeApiAdapter({request:async(_route,body)=>{if(body){posts++;throw Error('lost');}gets++;if(!mismatch)throw Error('not found');return fbReceipt({...fbBody,comment:'not this request'});}});
 await assert.rejects(a('native.feedback.record',{scope:s,payload:{parent_operation_id:parent,...fbBody}}),e=>e.code===(mismatch?'OPERATION_INPUT_MISMATCH':'OPERATION_OUTCOME_UNKNOWN'));assert.equal(posts,1);assert.equal(gets,1);}
 let calls=0;const a=createIdeApiAdapter({request:async()=>{calls++;}});await assert.rejects(a('native.rework.apply',{scope:s,payload:{parent_operation_id:parent,...applyBody,user_approved:false}}));assert.equal(calls,0);
});

test('even Main observed 422 cannot prove these domain writes were not claimed: inspect original only',async()=>{
 let calls=0;const a=createIdeApiAdapter({request:async()=>{calls++;throw Object.assign(Error('PRIVATE'),{status:422,quick_submission_observation:{kind:'HTTP_RESPONSE',http_status:422,api_code:'INVALID_INPUT'}});}});
 await assert.rejects(a('native.feedback.record',{scope:s,payload:{parent_operation_id:parent,...fbBody}}),e=>e.code==='OPERATION_OUTCOME_UNKNOWN'&&!e.message.includes('PRIVATE'));assert.equal(calls,2);
});

test('recorded or applied labels cannot substitute for the exact selection or actual write receipt',async()=>{
 for(const value of [{...fbReceipt(),file_selection:{...fbReceipt().file_selection,quote_sha256:'0'.repeat(64)}},{...fbReceipt(),requested_selection:undefined},{...fbReceipt(),selection_verification:'QUOTE_AMBIGUOUS',file_selection:fbReceipt().file_selection}]){
  const a=createIdeApiAdapter({request:async()=>value});await assert.rejects(a('native.feedback.operation',{scope:s,payload:{parent_operation_id:parent,operation_id:fbBody.operation_id}}),e=>e.code==='OPERATION_INPUT_MISMATCH');
 }
 const a=createIdeApiAdapter({request:async()=>({...applyReceipt(),receipt_ref:undefined})});await assert.rejects(a('native.rework.operation',{scope:s,payload:{parent_operation_id:parent,operation_id:applyBody.operation_id}}),e=>e.code==='OPERATION_INPUT_MISMATCH');
});

test('typed feedback Send stays the existing native message path with exact body hash, legacy absent field unchanged',async()=>{
 for(const typed of [false,true]){let body,posts=0;const profile={id:'provider',revision:1,sha256:'a'.repeat(64)};
 const a=createIdeApiAdapter({request:async(route,b)=>{if(b){body=b;posts++;return {};}
  if(route.includes('/operations/'))return {kind:'NATIVE_MESSAGE',...s,operation_id:'send',status:'UNKNOWN_OUTCOME',record_origin:'CURRENT_HOST',request_sha256:digest(body),input:{raw_text:body.raw_text,origin:'ORIGINAL_NATIVE_REQUEST'},provider_profile_ref:profile,file_source_ref:source,file_link_ref:null,native_feedback_ref:typed?feedback:undefined,control_capabilities:{},automatic_retry:false,rework_previews:[]};
  return {kind:'NATIVE_CONVERSATION',...s,messages:[{}]};}});
 const v=await a('native.message',{scope:s,payload:{operation_id:'send',raw_text:'按原稿准备。',provider_profile_ref:profile,file_source_ref:source,...(typed?{native_feedback_ref:feedback}:{})}});assert.equal(posts,1);assert.equal(v.status,'UNKNOWN_OUTCOME');assert.equal('native_feedback_ref'in body,typed);if(typed)assert.deepEqual(body.native_feedback_ref,feedback);
 }
});


test('apply outcome stays in its exact workspace binding; invalid saved receipt stays UNKNOWN after one original GET',async t=>{
 for(const [field,bad] of [['workspace_id','9'.repeat(64)],['binding_revision',2],['binding_sha256','9'.repeat(64)],['binding_revision',0]]){
  let posts=0,gets=0;const value=applyReceipt();value.outcomes[0].source_ref[field]=bad;
  const a=createIdeApiAdapter({request:async(_route,body)=>{if(body){posts++;return {};}gets++;return value;}});
  await assert.rejects(a('native.rework.apply',{scope:s,payload:{parent_operation_id:parent,...applyBody,user_approved:true}}),e=>e.code==='OPERATION_INPUT_MISMATCH');
  assert.equal(posts,1);assert.equal(gets,1);
 }
 const value=fbReceipt();value.feedback_ref={...value.feedback_ref,revision:0};let posts=0,gets=0;
 const adapter=createIdeApiAdapter({request:async(_route,body)=>{if(body){posts++;return {};}gets++;return value;}});
 const d=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR,'invalid-receipt81-'));t.after(()=>fs.rmSync(d,{recursive:true,force:true}));
 const b=new IdeCompanionBridge({dataDir:d,dispatch:adapter,requestPairing:async()=>({approved:true})});t.after(()=>b.close());
 const c={client_id:crypto.randomUUID(),client_secret:crypto.randomBytes(32).toString('hex'),client_label:'原回执核对'};
 await b.pair({protocol:PROTOCOL,...c,nonce:b.nonce});await new Promise(setImmediate);
 const body={protocol:PROTOCOL,request_id:'invalid-original-receipt',method:'native.feedback.record',scope:s,payload:{parent_operation_id:parent,...fbBody}};
 const result=await b.rpc(c.client_id,b.journal.value.clients[c.client_id],body);
 assert.equal(result.error.code,'OPERATION_INPUT_MISMATCH');assert.equal(b.journal.value.requests[c.client_id+':'+body.request_id].status,'UNKNOWN');
 await b.rpc(c.client_id,b.journal.value.clients[c.client_id],body);assert.equal(posts,1);assert.equal(gets,1);
});

test('registered Word file opens through Main only with the current approved source', async () => {
  const docx = {...source, path: 'tasks/review.docx'};
  const payload = {parent_operation_id: parent, file_source_ref: docx, target: 'native'};
  const binary = {...file(), baseline_file_source_ref: docx, file_source_ref: docx,
    availability: 'BINARY', encoding: 'UNAVAILABLE', content: undefined, live_sha256: docx.sha256};
  let snapshot = binary;
  let receipt = {status: 'OPEN_REQUESTED', file_source_ref: docx};
  const calls = [];
  const adapter = createIdeApiAdapter({
    request: async () => snapshot,
    openArtifact: async (body, {scope}) => { calls.push({body, scope}); return receipt; },
  });
  const ok = await adapter('native.file.open', {scope: s, payload});
  assert.equal(ok.status, 'OPEN_REQUESTED');
  assert.equal(ok.native_software_open_confirmed, false);
  assert.equal(ok.editor_uri, undefined);
  assert.deepEqual(calls[0], {body: {kind: 'workspace-file', target: 'native',
    project_id: s.project_id, file_source_ref: docx}, scope: s});

  snapshot = {...binary, live_sha256: '8'.repeat(64)};
  await assert.rejects(adapter('native.file.open', {scope: s, payload}), {code: 'ARTIFACT_CHANGED'});
  assert.equal(calls.length, 1);

  snapshot = binary;
  receipt = {status: 'OPEN_REQUESTED', file_source_ref: {...docx, revision: 2}};
  await assert.rejects(adapter('native.file.open', {scope: s, payload}), {code: 'OPERATION_OUTCOME_UNKNOWN'});
  assert.equal(calls.length, 2);
});
