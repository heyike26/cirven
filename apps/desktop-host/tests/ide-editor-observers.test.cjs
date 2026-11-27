'use strict';
// AUTHORED_NOT_RUN. Main API/paired actor/URI boundary only. No running IDE,
// Windows process observation, original CLI, model or native file effects.
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const digest=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
function scene(){const scope={project_id:'qa',session_ref:{id:'original_session',revision:1,sha256:'a'.repeat(64)},generation:1},workspace_ref={id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},context={client_id:'paired_client'},payload={workspace_ref,editor_window_id:'original_window',editor_process_id:1234,editor_parent_process_id:1233,action:'OBSERVE'};
 const id=digest({project_id:scope.project_id,workspace_id:workspace_ref.id,editor_client_id:context.client_id,editor_window_id:payload.editor_window_id});
 const observed={kind:'EDITOR_OBSERVER',id,project_id:scope.project_id,workspace_ref,editor_client_id:context.client_id,editor_window_id:payload.editor_window_id,phase:'ACTIVE',revision:1,observation_state:'OBSERVED',dirty_paths:['src/原稿.ts'],dirty_count:1,dirty_paths_truncated:false,process_identity:{pid:1234,parent_pid:1233,birth:'1700000000.0',parent_birth:'1699999900.0'},observed_documents:1,current:true,read_only:true,workspace_writes:0,model_calls:0,native_posts:0,automatic_replay:false,atomic_editor_write_guarantee:false,captured_at_epoch_ms:Date.now(),received_at:'2026-11-03T00:00:00Z',observed_at:'2026-11-03T00:00:00Z'};
 const observation={captured_at_epoch_ms:Date.now(),availability:'OBSERVED',root_identity:{device:'9007199254740993',inode:'18446744073709551610'},documents:[{path:'src/原稿.ts',document_version:8,content_sha256:'d'.repeat(64),is_dirty:true}],documents_complete:true,unavailable_reason:null,permission_ref:null};
 function preparation(body){const basis={scope:{...scope,workspace_ref},observer_id:id,revision:0,runtime_id:'runtime_exact',action:body.action,editor_client_id:context.client_id,editor_window_id:body.editor_window_id,editor_process_id:body.editor_process_id,editor_parent_process_id:body.editor_parent_process_id,root_identity:observation.root_identity,root_path_sha256:digest('/unit/original'),process_identity:observed.process_identity};return {kind:'EDITOR_OBSERVER_PREPARATION',...basis,preparation_sha256:digest(basis),native_root:'/unit/original',read_only:true,workspace_writes:0,model_calls:0,native_posts:0,automatic_replay:false};}
 function receipt(body){return {kind:'EDITOR_OBSERVER_ACTION',id:body.operation_id,session_ref:scope.session_ref,generation:scope.generation,request_sha256:digest(body),editor_client_id:context.client_id,editor_window_id:body.editor_window_id,status:'RETURNED',action:body.action,observer:observed,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,permission_granted:false,atomic_editor_write_guarantee:false,observation_time_scope:'AT_ORIGINAL_COMMAND_ONLY'};}
 return {scope,workspace_ref,context,payload,id,observed,observation,preparation,receipt};}
test('observer preparation binds original native root digest and Main actor, with exact 64-bit directory identity',async()=>{
 const x=scene(),calls=[];let shift=false;const adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});const result=x.preparation(body);if(shift)result.native_root='/unit/replacement';return result;}});
 const value=await adapter('session.editor.observer.prepare',{scope:x.scope,payload:x.payload},x.context);
 assert.equal(value.root_uri,'file:///unit/original');assert.equal(value.root_identity.inode,x.observation.root_identity.inode);assert.equal(value.native_root,undefined);assert.equal(calls[0].body.editor_client_id,'paired_client');
 shift=true;await assert.rejects(adapter('session.editor.observer.prepare',{scope:x.scope,payload:x.payload},x.context),{code:'EDITOR_OBSERVATION_INVALID'});
 await assert.rejects(adapter('session.editor.observer.prepare',{scope:x.scope,payload:{...x.payload,editor_client_id:'injected'}},x.context));
 await assert.rejects(adapter('session.editor.observer.prepare',{scope:x.scope,payload:x.payload}),{code:'TRUSTED_CLIENT_REQUIRED'});
});
test('an observer command never grants a write and lost/conflict ACK reads only the exact original receipt',async()=>{
 const x=scene(),calls=[];let original;const adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});if(body){original=x.receipt(body);throw Object.assign(Error('UNIT ACK lost'),{status:409});}return original;}});
 const payload={...x.payload,operation_id:'observe_exact',expected_revision:0,preparation_sha256:'e'.repeat(64),observation:x.observation,user_approved:false};
 const result=await adapter('session.editor.observer.command',{scope:x.scope,payload},x.context);
 assert.equal(result.permission_granted,false);assert.equal(result.observation_time_scope,'AT_ORIGINAL_COMMAND_ONLY');assert.equal(calls.filter(r=>r.body).length,1);assert.match(calls[1].url,/editor-observer-actions\/observe_exact\?editor_client_id=paired_client/);
 original.editor_client_id='another_client';await assert.rejects(adapter('session.editor.observer.operation',{scope:x.scope,payload:{operation_id:'observe_exact'}},x.context),{code:'EDITOR_OBSERVATION_INVALID'});
});
test('observer current read is distinct from historical receipt and rejects buffer text or raw host paths',async()=>{
 const x=scene();let leaked=false;const adapter=createIdeApiAdapter({request:async()=>({...x.observed,...leaked?{text:'SECRET_BUFFER',native_root:'/unrelated'}:{}})});
 const value=await adapter('session.editor.observer.get',{scope:x.scope,payload:{observer_id:x.id}},x.context);assert.equal(value.dirty_count,1);assert.equal(value.atomic_editor_write_guarantee,false);
 leaked=true;await assert.rejects(adapter('session.editor.observer.get',{scope:x.scope,payload:{observer_id:x.id}},x.context));
 const malformed={...x.payload,operation_id:'no_buffer_text',expected_revision:0,preparation_sha256:'e'.repeat(64),observation:{...x.observation,documents:[{...x.observation.documents[0],text:'DO_NOT_TRANSMIT'}]},user_approved:false};
 await assert.rejects(adapter('session.editor.observer.command',{scope:x.scope,payload:malformed},x.context));
});
test('unavailable or partial namespaces cannot be coerced into clean observed documents',async()=>{
 const x=scene(),calls=[],adapter=createIdeApiAdapter({request:async(...args)=>{calls.push(args);return {};}}),payload={...x.payload,operation_id:'invalid_capture',expected_revision:0,preparation_sha256:'e'.repeat(64),observation:{...x.observation,availability:'UNAVAILABLE',unavailable_reason:'DOCUMENT_LIMIT'},user_approved:false};
 await assert.rejects(adapter('session.editor.observer.command',{scope:x.scope,payload},x.context),{code:'EDITOR_OBSERVATION_INVALID'});assert.equal(calls.length,0);
});

// AUTHORED_NOT_RUN: actual Main adapter; paired API/process facts UNIT boundary.
function recoveryScene(){const x=scene(),payload={workspace_ref:x.workspace_ref,observer_id:x.id,editor_window_id:'new_window',editor_process_id:1234,editor_parent_process_id:1233};
 function preparation(body){const basis={scope:{...x.scope,workspace_ref:x.workspace_ref},observer_id:x.id,revision:1,phase:'ACTIVE',runtime_id:'same_runtime',editor_client_id:x.context.client_id,editor_window_id:body.editor_window_id,process_identity:x.observed.process_identity,original_process_identity:x.observed.process_identity,original_editor_client_id:x.context.client_id,original_editor_window_id:x.payload.editor_window_id,original_process_state:'SAME_PROCESS',root_identity:x.observation.root_identity,root_path_sha256:digest('/unit/original')};return {kind:'EDITOR_OBSERVER_RECOVERY_PREPARATION',...basis,preparation_sha256:digest(basis),native_root:'/unit/original',read_only:true,workspace_writes:0,model_calls:0,native_posts:0,permission_granted:false,automatic_replay:false,current_scope_is_execution_authorization:false};}
 function receipt(body){return {kind:'EDITOR_OBSERVER_RECOVERY',id:body.operation_id,scope:{...x.scope,workspace_ref:x.workspace_ref},request_sha256:digest(body),observer_id:x.id,editor_client_id:x.context.client_id,editor_window_id:body.editor_window_id,original_editor_client_id:x.context.client_id,original_editor_window_id:x.payload.editor_window_id,original_process_state:'SAME_PROCESS',phase:'RETIRED',revision:2,status:'RETURNED',workspace_writes:0,model_calls:0,native_posts:0,permission_granted:false,automatic_replay:false,atomic_editor_write_guarantee:false,original_evidence_retained:true,observation_time_scope:'AT_ORIGINAL_COMMAND_ONLY'};}
 return {...x,payload,preparation,receipt};}
test('paired recovery preparation returns only exact original namespace and current-session metadata without execution authority',async()=>{
 const x=recoveryScene(),calls=[];let changed=false;const adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});const result=x.preparation(body);if(changed)result.original_editor_window_id='injected';return result;}});
 const result=await adapter('session.editor.observer.recovery.prepare',{scope:x.scope,payload:x.payload},x.context);assert.equal(result.root_uri,'file:///unit/original');assert.equal(result.native_root,undefined);assert.equal(result.current_scope_is_execution_authorization,false);assert.equal(calls[0].body.editor_client_id,x.context.client_id);
 changed=true;await assert.rejects(adapter('session.editor.observer.recovery.prepare',{scope:x.scope,payload:x.payload},x.context),{code:'EDITOR_OBSERVATION_INVALID'});
 await assert.rejects(adapter('session.editor.observer.recovery.prepare',{scope:x.scope,payload:{...x.payload,editor_client_id:'injected'}},x.context));
});
test('recovery lost ACK reads only the original API receipt and preserves its own explicit-null schema hash',async()=>{
 const x=recoveryScene(),calls=[];let original;const adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});if(body){original=x.receipt(body);throw Object.assign(Error('UNIT lost ACK'),{status:409});}return original;}});
 const payload={...x.payload,operation_id:'recover_exact',expected_revision:1,preparation_sha256:'e'.repeat(64),observation:{...x.observation,is_dirty:undefined,documents:x.observation.documents.map(d=>({...d,is_dirty:false})),capture_ref:null},user_preserved_original:true};delete payload.observation.is_dirty;
 const result=await adapter('session.editor.observer.recovery.command',{scope:x.scope,payload},x.context);assert.equal(result.phase,'RETIRED');assert.equal(calls.length,2);assert.equal(calls[0].body.observation.capture_ref,null);assert.equal(result.request_sha256,digest(calls[0].body));assert.match(calls[1].url,/editor-observer-recoveries\/recover_exact/);assert.equal(result.original_evidence_retained,true);
});
test('recovery cannot replace explicit preservation or borrow permission/capture authority',async()=>{
 const x=recoveryScene(),calls=[];const adapter=createIdeApiAdapter({request:async(...args)=>{calls.push(args);throw Error('must not reach API');}}),base={...x.payload,operation_id:'recover_exact',expected_revision:1,preparation_sha256:'e'.repeat(64),observation:{...x.observation,capture_ref:null},user_preserved_original:false};
 await assert.rejects(adapter('session.editor.observer.recovery.command',{scope:x.scope,payload:base},x.context),{code:'INVALID_PARAMS'});
 await assert.rejects(adapter('session.editor.observer.recovery.command',{scope:x.scope,payload:{...base,user_preserved_original:true,observation:{...base.observation,capture_ref:'f'.repeat(64)}}},x.context),{code:'INVALID_PARAMS'});assert.equal(calls.length,0);
});

// AUTHORED_NOT_RUN: actual Main projection, API return is an explicit UNIT boundary.
test('original source authority issue projection is generation-bound and removes extra internal fields',async()=>{
 const x=scene(),issue={code:'SOURCE_CONTEXT_CHANGED',generation:x.scope.generation,operation_id:'old_task',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true,private_source_text:'NEVER_FORWARD'},raw={...x.scope,messages:[],children:[],todo:[],pending_questions:[],pending_permissions:[],context_authority_issue:issue};
 const adapter=createIdeApiAdapter({request:async()=>raw}),result=await adapter('session.get',{scope:x.scope,payload:{}},x.context);assert.equal(result.context_authority_issue.code,'SOURCE_CONTEXT_CHANGED');assert.doesNotMatch(JSON.stringify(result),/NEVER_FORWARD|private_source_text/);issue.generation++;await assert.rejects(adapter('session.get',{scope:x.scope,payload:{}},x.context),{code:'INVALID_UPSTREAM_RESPONSE'});
});
