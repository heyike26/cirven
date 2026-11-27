'use strict';
// AUTHORED_NOT_RUN: real Main adapter, explicit UNIT scoped API/journal boundary.
const test=require('node:test'),assert=require('node:assert/strict'),{createHash}=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex');
function scene(){
 const scope={project_id:'project',conversation_id:'native_chat'},ref={id:'native_parent',revision:1,sha256:'a'.repeat(64)},key='native_recovery_'+'b'.repeat(48);
 const issue={code:'SOURCE_CONTEXT_CHANGED',generation:2,operation_id:'old_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true};
 const basis={parent_operation_id:'native_parent',expected_native_ref:ref,subject_sha256:'c'.repeat(64),operation_id:key,origin:scope,target:{project_id:'project',conversation_ref:null},issue,existing_recovery:null};
 const preparation={kind:'NATIVE_CONTEXT_RECOVERY_PREPARATION',...basis,preparation_sha256:sha(basis),target_label:'Project',read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_post_replay:false};
 const native={kind:'NATIVE_MESSAGE',...scope,operation_id:'native_parent',status:'UNKNOWN_OUTCOME',record_origin:'CURRENT_HOST',request_sha256:'d'.repeat(64),input:{raw_text:'ORIGINAL PRIVATE INPUT','origin':'ORIGINAL_NATIVE_REQUEST'},provider_profile_ref:null,native_message_ref:ref,file_source_ref:null,file_link_ref:null,control_capabilities:{can_guide:false,can_status:true,can_cancel:true},native_context_issue:issue,native_context_recovery:{parent_operation_id:'native_parent',expected_native_ref:ref,record_current:true,read_only:true,model_calls:0}};
 const body={operation_id:key,expected_native_ref:ref,preparation_sha256:preparation.preparation_sha256};
 const creation_request={operation_id:'native_context_chat_'+'b'.repeat(48),project_id:'project',title:null};
 const creation_receipt={version:1,operation_id:creation_request.operation_id,request_sha256:sha(creation_request),conversation_ref:{id:'native:'+'e'.repeat(64),revision:1},project_id:'project',next_message:{project_id:'project',conversation_id:'chat_new'},created_at:'2026-11-03T08:00:00Z',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};
 const result={kind:'NATIVE_CONTEXT_RECOVERY',parent_operation_id:'native_parent',operation_id:key,request_sha256:sha(body),expected_native_ref:ref,origin:scope,owner_binding_sha256:'f'.repeat(64),phase:'COMPLETE',stop_operation_id:'native_context_stop_'+'b'.repeat(48),stop_status:'RETURNED',creation_request,creation_receipt,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,external_actions_rolled_back:false,automatic_post_replay:false,model_calls_by_recovery:0};
 const calls=[],mode={exists:false,lose:false},grants={methods:[],project_ids:null};
 const api=createIdeApiAdapter({request:async(route,request)=>{
  calls.push({route,request});if(route.includes('/native-conversations/'))return native;
  if(route.endsWith('/prepare')){if(mode.revokeAfterPrepare)mode.revoked=true;return {...preparation,private_source:'NEVER_PROJECT'};}
  if(request){mode.exists=true;if(mode.lose)throw Error('UNIT stop/create reply lost');return result;}
  if(!mode.exists)throw Object.assign(Error('UNIT missing original'),{status:404});if(mode.loseSourceOnRead)grants.project_ids=['destination'];return {...result,private_source:'NEVER_PROJECT'};
 }});
 const invoke=(method,payload)=>api('native.context.recovery.'+method,{scope,payload},{grants,currentGrants:()=>{if(mode.revoked)throw Object.assign(Error('UNIT pairing revoked'),{code:'PAIRING_REVOKED'});return grants;}});
 return {scope,ref,key,issue,preparation,native,body,result,calls,mode,grants,api,invoke,
  prepare:()=>invoke('prepare',{parent_operation_id:'native_parent',expected_native_ref:ref}),
  command:p=>invoke('command',{parent_operation_id:'native_parent',request:body,preparation:p}),
  posts:()=>calls.filter(c=>c.request&&!c.route.endsWith('/prepare'))};
}

test('scoped native operation projects bounded source issue and private recovery identity only',async()=>{
 const x=scene();const r=await x.api('native.operation',{scope:x.scope,payload:{operation_id:'native_parent'}});
 assert.deepEqual(r.native_context_issue,x.issue);assert.deepEqual(r.native_context_recovery.expected_native_ref,x.ref);assert.equal(r.private_source,undefined);
});

test('prepare reads the exact discussion and target grant without any owned stop',async()=>{
 const x=scene(),p=await x.prepare();assert.equal(p.private_source,undefined);assert.equal(p.effective_target.project_id,'project');assert.equal(p.model_calls,0);assert.equal(x.posts().length,0);
 assert.match(x.calls[0].route,/native-conversations\/native_chat\/operations\/native_parent\?project_id=project$/);
});

test('a different scoped parent cannot prepare or stop even when its native ref is supplied',async()=>{
 const x=scene();await assert.rejects(()=>x.invoke('prepare',{parent_operation_id:'foreign_parent',expected_native_ref:x.ref}));assert.equal(x.posts().length,0);
});

test('changed target outside grant is opaque and cannot dispatch a stop',async()=>{
 const x=scene();x.preparation.target.project_id='other_project';const {kind,preparation_sha256,target_label,read_only,model_calls,native_posts,new_conversations,permission_granted,automatic_post_replay,...basis}=x.preparation;x.preparation.preparation_sha256=sha(basis);x.grants.project_ids=['project'];
 await assert.rejects(()=>x.prepare(),e=>e.code==='NATIVE_CONTEXT_RECOVERY_ACCESS_REQUIRED'&&!e.message.includes('other_project'));assert.equal(x.posts().length,0);
});

test('command freezes the confirmed packet and recovers a lost reply by original GET once',async()=>{
 const x=scene(),p=await x.prepare();x.mode.lose=true;const r=await x.command(p);
 assert.equal(r.phase,'COMPLETE');assert.deepEqual(x.posts().map(c=>c.request),[x.body]);assert.equal(r.private_source,undefined);
 assert.equal(x.calls.at(-1).request,undefined);assert.match(x.calls.at(-1).route,new RegExp('/'+x.key+'$'));
});

test('unknown original stop is observed even when command is explicitly invoked again',async()=>{
 const x=scene(),p=await x.prepare();x.mode.exists=true;x.result.phase='STOP_PENDING';x.result.stop_status='UNKNOWN_OUTCOME';x.result.creation_receipt=null;
 assert.equal((await x.command(p)).phase,'STOP_PENDING');assert.equal(x.posts().length,0);
});

test('original result reads recheck the frozen destination grant before exposing its identity',async()=>{
 const x=scene();x.mode.exists=true;x.result.creation_request.project_id='ungranted';x.result.creation_receipt=null;x.result.phase='STOP_CONFIRMED';x.grants.project_ids=['project'];
 await assert.rejects(()=>x.invoke('operation',{parent_operation_id:'native_parent',operation_id:x.key,request_sha256:sha(x.body)}),e=>e.code==='NATIVE_CONTEXT_RECOVERY_ACCESS_REQUIRED'&&!e.message.includes('ungranted'));assert.equal(x.posts().length,0);
});

test('foreign request hash and broadened effect flags cannot acknowledge a recovery',async()=>{
 for(const changed of [{request_sha256:'0'.repeat(64)},{source_data_copied:true},{model_calls_by_recovery:1},{external_actions_rolled_back:true}]){
  const x=scene();x.mode.exists=true;Object.assign(x.result,changed);
  await assert.rejects(()=>x.invoke('operation',{parent_operation_id:'native_parent',operation_id:x.key,request_sha256:sha(x.body)}));assert.equal(x.posts().length,0);
 }
});

// AUTHORED_NOT_RUN: grant revocation at the last metadata await, before stop POST.
test('live pairing revoked while preparing cannot dispatch a delayed stop',async()=>{
 const x=scene(),p=await x.prepare();x.mode.revokeAfterPrepare=true;
 await assert.rejects(()=>x.command(p),e=>e.code==='PAIRING_REVOKED');assert.equal(x.posts().length,0);
});

test('original discussion grant is rechecked after a result read even if destination access remains',async()=>{
 const x=scene();x.mode.exists=true;x.mode.loseSourceOnRead=true;x.grants.project_ids=['project','destination'];
 x.result.phase='STOP_CONFIRMED';x.result.creation_receipt=null;x.result.creation_request.project_id='destination';
 await assert.rejects(()=>x.invoke('operation',{parent_operation_id:'native_parent',operation_id:x.key,request_sha256:sha(x.body)}),e=>e.code==='NATIVE_CONTEXT_RECOVERY_ACCESS_REQUIRED');assert.equal(x.posts().length,0);
});
