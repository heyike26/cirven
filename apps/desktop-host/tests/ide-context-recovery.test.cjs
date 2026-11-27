'use strict';
// AUTHORED_NOT_RUN: actual Main adapter, explicit UNIT API/owner boundary.
const test=require('node:test'),assert=require('node:assert/strict'),{createHash}=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex');
function scene(){
 const scope={project_id:'project',session_ref:{id:'original',revision:1,sha256:'a'.repeat(64)},generation:2},conversation_ref={id:'native:'+'b'.repeat(64),revision:3},expected_issue={code:'SOURCE_CONTEXT_CHANGED',generation:2,operation_id:'original_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true};
 const basis={kind:'CONTEXT_RECOVERY_PREPARATION',scope,conversation_ref,expected_issue,target_project_id:null,existing_recovery:null};
 const preparation={...basis,preparation_sha256:sha(basis),read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_replay:false};
 const body={operation_id:'recover_original',session_ref:scope.session_ref,generation:2,conversation_ref,expected_issue};
 const creation_request={operation_id:'context_chat_'+'c'.repeat(48),project_id:null,title:null};
 const creation_receipt={version:1,operation_id:creation_request.operation_id,request_sha256:sha(creation_request),conversation_ref:{id:'native:'+'d'.repeat(64),revision:1},project_id:null,next_message:{project_id:null,conversation_id:'chat_new'},created_at:'2026-11-03T06:00:00Z',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};
 const result={version:1,operation_id:body.operation_id,request_sha256:sha(body),...scope,conversation_ref,phase:'COMPLETE',stop_operation_id:'context_stop_'+'c'.repeat(48),stop_status:'RETURNED',creation_request,creation_receipt,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,automatic_post_replay:false,model_calls_by_recovery:0};
 return {scope,preparation,body,result};
}

test('preparation sends only exact session metadata and strips private upstream fields',async()=>{
 const x=scene(),calls=[],api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return {...x.preparation,private_source:'NEVER_PROJECT'};}});
 const value=await api('session.context.recovery.prepare',{scope:x.scope,payload:{}});
 assert.match(calls[0].route,/context-recovery\/prepare$/);assert.deepEqual(calls[0].body,{session_ref:x.scope.session_ref,generation:2});assert.equal(value.private_source,undefined);assert.equal(value.read_only,true);assert.equal(value.new_conversations,0);
});

test('command freezes original body and projects a zero-model empty chat without private output',async()=>{
 const x=scene(),calls=[],api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return {...x.result,private_source:'KEEP_PRIVATE'};}});
 const value=await api('session.context.recovery.command',{scope:x.scope,payload:{operation_id:x.body.operation_id,preparation:x.preparation}});
 assert.deepEqual(calls[0].body,x.body);assert.equal(calls.length,1);assert.equal(value.kind,'CONTEXT_RECOVERY');assert.equal(value.private_source,undefined);assert.equal(value.creation_receipt.project_id,null);assert.equal(value.model_calls_by_recovery,0);
});

test('lost command reply reads the original API recovery key with no second POST',async()=>{
 const x=scene(),calls=[],api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});if(body)throw Error('UNIT lost reply');return x.result;}});
 const value=await api('session.context.recovery.command',{scope:x.scope,payload:{operation_id:x.body.operation_id,preparation:x.preparation}});
 assert.equal(value.phase,'COMPLETE');assert.deepEqual(calls.map(c=>!!c.body),[true,false]);assert.match(calls[1].route,/context-recovery\/recover_original$/);
});

test('a pending stop never projects a fresh conversation or reserves a model',async()=>{
 const x=scene(),api=createIdeApiAdapter({request:async()=>({...x.result,phase:'STOP_PENDING',stop_status:'UNKNOWN_OUTCOME',creation_receipt:null})});
 const value=await api('session.context.recovery.command',{scope:x.scope,payload:{operation_id:x.body.operation_id,preparation:x.preparation}});
 assert.equal(value.phase,'STOP_PENDING');assert.equal(value.creation_receipt,null);assert.equal(value.source_data_copied,false);assert.equal(value.execution_started,false);
});

test('wrong preparation scope, altered metadata, or injected source text never reaches API',async()=>{
 const x=scene();let calls=0;const api=createIdeApiAdapter({request:async()=>{calls++;assert.fail('No API request');}});
 for(const preparation of [{...x.preparation,read_only:false},{...x.preparation,preparation_sha256:'f'.repeat(64)},{...x.preparation,scope:{...x.scope,generation:3}}])await assert.rejects(api('session.context.recovery.command',{scope:x.scope,payload:{operation_id:x.body.operation_id,preparation}}));
 await assert.rejects(api('session.context.recovery.command',{scope:x.scope,payload:{operation_id:x.body.operation_id,preparation:x.preparation,raw_text:'DO_NOT_SEND'}}));assert.equal(calls,0);
});

test('readonly operation requires original request hash and cannot adopt another generation',async()=>{
 const x=scene(),api=createIdeApiAdapter({request:async()=>x.result});
 await assert.rejects(api('session.context.recovery.operation',{scope:x.scope,payload:{operation_id:x.body.operation_id,request_sha256:'f'.repeat(64)}}));
 await assert.rejects(api('session.context.recovery.operation',{scope:{...x.scope,generation:3},payload:{operation_id:x.body.operation_id,request_sha256:sha(x.body)}}));
 const value=await api('session.context.recovery.operation',{scope:x.scope,payload:{operation_id:x.body.operation_id,request_sha256:sha(x.body)}});assert.equal(value.phase,'COMPLETE');
});

test('an existing cross-surface request is preserved and cannot be rebound to another operation key',async()=>{
 const x=scene(),original={...x.body,operation_id:'desktop_original'},basis={kind:x.preparation.kind,scope:x.scope,conversation_ref:x.preparation.conversation_ref,expected_issue:x.preparation.expected_issue,target_project_id:null,existing_recovery:{request:original,request_sha256:sha(original),target_project_id:null}};
 const p={...basis,preparation_sha256:sha(basis),read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_replay:false};let posted;
 const api=createIdeApiAdapter({request:async(_route,body)=>{posted=body;return {...x.result,operation_id:original.operation_id,request_sha256:sha(original)};}});
 await assert.rejects(api('session.context.recovery.command',{scope:x.scope,payload:{operation_id:'different',preparation:p}}));assert.equal(posted,undefined);
 await api('session.context.recovery.command',{scope:x.scope,payload:{operation_id:original.operation_id,preparation:p}});assert.deepEqual(posted,original);
});
