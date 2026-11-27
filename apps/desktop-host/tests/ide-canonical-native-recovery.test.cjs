'use strict';
// AUTHORED_NOT_RUN. Real adapter/membership wrapper; scoped API/stop are UNIT.
const test=require('node:test'),assert=require('node:assert/strict'),{createHash}=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const {historyContext}=require('../ide-canonical-native-recovery.cjs');
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex'),copy=v=>JSON.parse(JSON.stringify(v));
function scene(){
 const scope={project_id:'logical',conversation_ref:{id:'native:'+'d'.repeat(64),revision:1}},origin={project_id:null,conversation_id:'unbound_native'},identity={kind:'native',...origin};
 const source_ref={id:'native:'+sha([null,origin.conversation_id]),sha256:sha(identity)},ref={id:'native_parent',revision:1,sha256:'a'.repeat(64)},key='native_recovery_'+'b'.repeat(48);
 const issue={code:'SOURCE_CONTEXT_CHANGED',generation:1,operation_id:'old_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true};
 const recovery={parent_operation_id:'native_parent',expected_native_ref:ref,record_current:true,read_only:true,model_calls:0};
 const alias={id:source_ref.id,identity,original_source:null},current={version:1,...copy(scope),sources:[alias]};
 const basis={parent_operation_id:'native_parent',expected_native_ref:ref,subject_sha256:'c'.repeat(64),operation_id:key,origin,target:{project_id:'logical',conversation_ref:scope.conversation_ref},issue,existing_recovery:null};
 const preparation={kind:'NATIVE_CONTEXT_RECOVERY_PREPARATION',...basis,preparation_sha256:sha(basis),target_label:'Logical project',read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_post_replay:false};
 const body={operation_id:key,expected_native_ref:ref,preparation_sha256:preparation.preparation_sha256};
 const creation_request={operation_id:'native_context_chat_'+'b'.repeat(48),project_id:'logical',title:null};
 const creation_receipt={version:1,operation_id:creation_request.operation_id,request_sha256:sha(creation_request),project_id:'logical',conversation_ref:{id:'native:'+'e'.repeat(64),revision:1},next_message:{project_id:'logical',conversation_id:'chat_new'},created_at:'2026-11-03T10:00:00Z',execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};
 const result={kind:'NATIVE_CONTEXT_RECOVERY',parent_operation_id:'native_parent',operation_id:key,request_sha256:sha(body),expected_native_ref:ref,origin,owner_binding_sha256:'f'.repeat(64),phase:'COMPLETE',stop_operation_id:'native_context_stop_'+'b'.repeat(48),stop_status:'RETURNED',creation_request,creation_receipt,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,external_actions_rolled_back:false,automatic_post_replay:false,model_calls_by_recovery:0};
 const native={kind:'NATIVE_MESSAGE',...origin,operation_id:'native_parent',native_message_ref:ref,native_context_issue:issue,native_context_recovery:recovery};
 const mode={exists:false},calls=[],grants={project_ids:null,methods:[]};
 const api=createIdeApiAdapter({request:async(route,request)=>{
  calls.push({route,request});if(route.endsWith('/scope'))return copy(current);
  if(route.includes('/native-conversations/'))return native;
  if(route.endsWith('/prepare')){if(mode.changeDuringPrepare)current.conversation_ref.revision++;return preparation;}
  if(request){mode.exists=true;return result;}
  if(!mode.exists)throw Object.assign(Error('UNIT missing'),{status:404});if(mode.removeDuringQuery)current.sources=[];return result;
 }});
 const invoke=(method,payload)=>api('conversation.native.recovery.'+method,{scope,payload:{source_ref,...payload}},{grants,currentGrants:()=>grants});
 return {scope,origin,source_ref,ref,key,issue,recovery,alias,current,preparation,body,result,native,mode,calls,grants,invoke,
  prepare:()=>invoke('prepare',{parent_operation_id:'native_parent',expected_native_ref:ref}),
  query:()=>invoke('operation',{parent_operation_id:'native_parent',operation_id:key,request_sha256:sha(body)}),
  command:p=>invoke('command',{parent_operation_id:'native_parent',request:body,preparation:p}),
  posts:()=>calls.filter(c=>c.request&&!c.route.endsWith('/prepare'))};
}

test('canonical history carries exact native alias identity and readonly issue, with no owner identifiers',()=>{
 const x=scene(),row={operation_id:'native_parent',source:{alias_id:x.source_ref.id,kind:'native',...x.origin},native_context_issue:x.issue,native_context_recovery:x.recovery};
 const value=historyContext(row,x.current,x.scope,x.grants);assert.deepEqual(value.native_context_source.source_ref,x.source_ref);assert.deepEqual(value.native_context_source.origin,x.origin);assert.equal(value.native_context_source.native_context_recovery.model_calls,0);
});

test('a logical history grant alone does not disclose the private source identity or action',()=>{
 const x=scene(),row={operation_id:'native_parent',source:{alias_id:x.source_ref.id,kind:'native',...x.origin},native_context_issue:x.issue,native_context_recovery:x.recovery};
 assert.deepEqual(historyContext(row,x.current,x.scope,{project_ids:['logical']}),{native_context_access_required:true});
});

test('unbound native preparation uses omitted project query and binds current canonical membership',async()=>{
 const x=scene(),p=await x.prepare();assert.deepEqual(p.origin,x.origin);assert.deepEqual(p.canonical_binding.requested_scope,x.scope);assert.deepEqual(p.canonical_binding.source_ref,x.source_ref);
 const raw=x.calls.find(c=>c.route.includes('/native-conversations/'));assert.equal(raw.route.includes('project_id=null'),false);assert.equal(raw.route.includes('?project_id='),false);assert.equal(x.posts().length,0);
});

test('a changed alias identity cannot use another native source even with a valid parent ref',async()=>{
 const x=scene();x.source_ref.sha256='0'.repeat(64);await assert.rejects(()=>x.prepare());assert.equal(x.posts().length,0);assert.equal(x.calls.some(c=>c.route.includes('/native-messages/')),false);
});

test('a foreign parent is rejected by the original scoped native operation read',async()=>{
 const x=scene();x.native.conversation_id='another_chat';await assert.rejects(()=>x.prepare());assert.equal(x.posts().length,0);
});

test('organization changes during readonly preparation cannot publish a stale confirmation',async()=>{
 const x=scene();x.mode.changeDuringPrepare=true;await assert.rejects(()=>x.prepare());assert.equal(x.posts().length,0);
});

test('old operation query survives a newer organization revision while naming both scopes explicitly',async()=>{
 const x=scene();x.mode.exists=true;x.current.conversation_ref.revision=2;const r=await x.query();
 assert.equal(r.phase,'COMPLETE');assert.equal(r.canonical_binding.requested_scope.conversation_ref.revision,1);assert.equal(r.canonical_binding.current_scope.conversation_ref.revision,2);assert.equal(r.operation_id,x.key);assert.equal(x.posts().length,0);
});

test('unknown stop on an older scope is observed without another native command',async()=>{
 const x=scene(),p=await x.prepare();x.mode.exists=true;x.current.conversation_ref.revision=2;x.result.phase='STOP_PENDING';x.result.stop_status='UNKNOWN_OUTCOME';x.result.creation_receipt=null;
 const r=await x.command(p);assert.equal(r.phase,'STOP_PENDING');assert.equal(r.stop_status,'UNKNOWN_OUTCOME');assert.equal(x.posts().length,0);
});

test('stale scope cannot dispatch metadata continuation after a confirmed stop',async()=>{
 const x=scene(),p=await x.prepare();x.mode.exists=true;x.current.conversation_ref.revision=2;x.result.phase='STOP_CONFIRMED';x.result.creation_receipt=null;
 const r=await x.command(p);assert.equal(r.phase,'STOP_CONFIRMED');assert.equal(x.posts().length,0);assert.equal(r.creation_receipt,null);
});

test('membership removed while original result is read cannot return that result as current',async()=>{
 const x=scene();x.mode.exists=true;x.mode.removeDuringQuery=true;await assert.rejects(()=>x.query());assert.equal(x.posts().length,0);
});
