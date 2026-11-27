'use strict';
// AUTHORED_NOT_RUN: Main transport/URI resolver boundaries. No native editor.
const test=require('node:test'),assert=require('node:assert/strict');
const {fileURLToPath}=require('node:url');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const {fixture}=require('../../../plugins/vscode-companion/tests/diff-fixture.cjs');
const {canonical,hash}=require('../../../plugins/vscode-companion/client.cjs');
const digest=v=>hash(canonical(v));
function scene(){const f=fixture({full:true}),scope={project_id:f.session.project_id,session_ref:f.session.session_ref,generation:f.session.generation},context={client_id:'paired_client'};
 const selection={session_ref:scope.session_ref,generation:scope.generation,workspace_ref:f.scope.workspace_ref,snapshot_ref:f.directory.snapshot_ref,diff_ref:f.entry.diff_ref,child_ref:null};
 const request={...selection,operation_id:'editor_one',preparation_sha256:'8'.repeat(64),direction:'RESTORE_BEFORE',editor_client_id:context.client_id,editor_window_id:'window_one',editor_nonce:'9'.repeat(64)};
 const source={kind:'local_file',project_id:scope.project_id,workspace_id:f.scope.workspace_ref.id,binding_revision:f.scope.workspace_ref.revision,binding_sha256:f.scope.workspace_ref.binding_sha256,path:f.file.path,revision:1,sha256:f.file.after_sha256};
 const grant={scope:f.scope,selection,direction:request.direction,editor_client_id:context.client_id,editor_window_id:request.editor_window_id,editor_nonce:request.editor_nonce,path:source.path,source_ref:source,file_location:null,saved_basis:{exists:true,sha256:source.sha256,byte_length:Buffer.byteLength(f.file.after)},target_exists:true,target_sha256:f.file.before_sha256,source_editor_sha256:hash(f.file.after.slice(1)),target_editor_sha256:hash(f.file.before.slice(1)),source_has_bom:true,target_has_bom:true,mode:'TEXT_REPLACE'};
 const value={kind:'EDITOR_CHANGE',id:request.operation_id,session_ref:scope.session_ref,generation:scope.generation,request_sha256:digest(request),phase:'READY',revision:2,grant,lease_ref:{id:digest({project_id:scope.project_id,session_id:scope.session_ref.id,operation_id:request.operation_id}),revision:1,sha256:digest(grant)},automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,editor_client_id:context.client_id,editor_window_id:request.editor_window_id,first_reservation_reply:true};
 const {session_ref,generation,editor_client_id,...payload}=request;
 return {f,scope,context,request,payload,value,source};}
test('file preparation resolves an exact host URI and strips the API native path without modifying a file',async()=>{
 const x=scene(),calls=[],workspace=x.f.scope.workspace_ref,parent={project_id:x.scope.project_id,workspace_id:workspace.id,binding_revision:workspace.revision,binding_sha256:workspace.binding_sha256,segments:[],device:'17',inode:'18446744073709551601'};
 const payload={target_operation_id:x.request.operation_id,lease_ref:x.value.lease_ref,editor_window_id:x.request.editor_window_id,editor_nonce:x.request.editor_nonce,expected_revision:2,action:'MANUAL_RESOLVE'},
  snapshot={availability:'OBSERVED',saved:x.value.grant.saved_basis,location:{kind:'WORKSPACE_FILE_SLOT',project_id:x.scope.project_id,parent_ref:parent,name:x.source.path,path:x.source.path},editor_text_sha256:x.value.grant.source_editor_sha256,workspace_binding_sha256:workspace.binding_sha256};
 const basis={scope:x.f.scope,lease_ref:payload.lease_ref,revision:2,target_operation_id:payload.target_operation_id,action:payload.action,snapshot},
  prepared={kind:'EDITOR_FILE_PREPARATION',...basis,preparation_sha256:digest(basis),editor_client_id:x.context.client_id,editor_window_id:payload.editor_window_id,native_path:'/unit/'+x.source.path,read_only:true,workspace_writes:0,model_calls:0,native_posts:0,automatic_replay:false};
 const dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return body?prepared:x.value;}});
 const result=await dispatch('session.editor.file.prepare',{scope:x.scope,payload},x.context);
 assert.equal(fileURLToPath(result.editor_uri),prepared.native_path);assert.equal(result.native_path,undefined);assert.equal(calls.length,2);
 assert.equal(calls[1].body.editor_client_id,x.context.client_id);assert.equal(result.snapshot.location.parent_ref.inode,parent.inode);
 await assert.rejects(dispatch('session.editor.file.prepare',{scope:x.scope,payload:{...payload,editor_client_id:'untrusted'}},x.context));
 prepared.preparation_sha256='0'.repeat(64);await assert.rejects(dispatch('session.editor.file.prepare',{scope:x.scope,payload},x.context),{code:'EDITOR_CHANGE_MISMATCH'});
});
test('file preparations bind exact decimal leaf identity and reject legacy write capability or exposed ACL payloads',async()=>{
 const x=scene(),workspace=x.f.scope.workspace_ref,payload={target_operation_id:x.request.operation_id,lease_ref:x.value.lease_ref,editor_window_id:x.request.editor_window_id,editor_nonce:x.request.editor_nonce,expected_revision:2,action:'MANUAL_RESOLVE'};
 const snapshot={availability:'OBSERVED',saved:x.value.grant.saved_basis,location:{kind:'WORKSPACE_FILE_SLOT',project_id:x.scope.project_id,path:x.source.path,name:x.source.path,parent_ref:{project_id:x.scope.project_id,workspace_id:workspace.id,binding_revision:workspace.revision,binding_sha256:workspace.binding_sha256,device:'17',inode:'18446744073709551601'}},editor_text_sha256:x.value.grant.source_editor_sha256,workspace_binding_sha256:workspace.binding_sha256,metadata_sha256:'e'.repeat(64),file_identity:{device:'17',inode:'18446744073709551600'},metadata_recovery_version:1};
 let includeSecret=false,legacy=false;
 const adapter=createIdeApiAdapter({request:async(_url,body)=>{if(!body)return x.value;const current={...snapshot,...includeSecret?{security_sddl:'PRIVATE_ACL_MUST_NOT_ENTER_VIEW'}:{}};if(legacy)delete current.metadata_recovery_version;const basis={scope:x.f.scope,lease_ref:body.lease_ref,revision:2,target_operation_id:body.target_operation_id,action:body.action,snapshot:current};return {kind:'EDITOR_FILE_PREPARATION',...basis,preparation_sha256:digest(basis),editor_client_id:x.context.client_id,editor_window_id:body.editor_window_id,native_path:'/unit/'+x.source.path,read_only:true,workspace_writes:0,model_calls:0,native_posts:0,automatic_replay:false};}});
 const valid=await adapter('session.editor.file.prepare',{scope:x.scope,payload},x.context);assert.equal(valid.snapshot.file_identity.inode,snapshot.file_identity.inode);assert.doesNotMatch(JSON.stringify(valid),/PRIVATE_ACL/);
 includeSecret=true;await assert.rejects(adapter('session.editor.file.prepare',{scope:x.scope,payload},x.context));includeSecret=false;legacy=true;
 await assert.rejects(adapter('session.editor.file.prepare',{scope:x.scope,payload:{...payload,action:'COMMIT'}},x.context),{code:'EDITOR_CAPABILITY_UNAVAILABLE'});
});
test('file command HTTP conflict reads the original action once and preserves an unknown OS result',async()=>{
 const x=scene(),calls=[],payload={target_operation_id:x.request.operation_id,lease_ref:x.value.lease_ref,editor_window_id:x.request.editor_window_id,editor_nonce:x.request.editor_nonce,expected_revision:2,action:'COMMIT',operation_id:'file_unknown',preparation_sha256:'7'.repeat(64),editor_observation:{documents:[],native_commands_settled:true},user_approved:true};let raw;
 const dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});if(body){raw={id:body.operation_id,kind:'EDITOR_FILE_ACTION',session_ref:x.scope.session_ref,generation:x.scope.generation,request:body,request_sha256:digest(body),status:'UNKNOWN_OUTCOME',automatic_replay:false,automatic_file_replay:false,file_effect:'UNCONFIRMED'};throw Object.assign(Error('UNIT OS reply lost'),{status:409});}return raw;}});
 const result=await dispatch('session.editor.file.command',{scope:x.scope,payload},x.context);
 assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(result.file_effect,'UNCONFIRMED');assert.equal(calls.filter(row=>row.body).length,1);
 assert.ok(calls[1].url.includes('/editor-file-actions/file_unknown?editor_client_id=paired_client'));
 raw.request_sha256='0'.repeat(64);await assert.rejects(dispatch('session.editor.file.operation',{scope:x.scope,payload:{operation_id:'file_unknown'}},x.context),{code:'OPERATION_INPUT_MISMATCH'});
});
test('an exact file action receipt keeps OS file effects distinct from its metadata lease and native text Undo',async()=>{
 const x=scene();Object.assign(x.value.grant,{mode:'FILE_DELETE',target_exists:false,target_sha256:null,target_editor_sha256:hash(''),target_has_bom:false});x.value.lease_ref.sha256=digest(x.value.grant);
 const payload={target_operation_id:x.request.operation_id,lease_ref:x.value.lease_ref,editor_window_id:x.request.editor_window_id,editor_nonce:x.request.editor_nonce,expected_revision:2,action:'COMMIT',operation_id:'delete_exact',preparation_sha256:'7'.repeat(64),editor_observation:{documents:[],native_commands_settled:true},user_approved:true};
 const adapter=createIdeApiAdapter({request:async(_url,body)=>({kind:'EDITOR_FILE_ACTION',id:body.operation_id,session_ref:x.scope.session_ref,generation:x.scope.generation,request:body,request_sha256:digest(body),status:'RETURNED',automatic_replay:false,automatic_file_replay:false,file_effect:'APPLIED',editor_change:{...x.value,phase:'BUFFER_CHANGED',revision:4},file_result:{status:'APPLIED',workspace_writes:1,native_text_undo:false,effect_scope:'EXCLUSIVE_ORIGINAL_FILE_EXISTENCE_CHANGE',observed_saved:{exists:false,sha256:null,byte_length:0}}})});
 const result=await adapter('session.editor.file.command',{scope:x.scope,payload},x.context);
 assert.equal(result.file_result.workspace_writes,1);assert.equal(result.file_result.native_text_undo,false);assert.equal(result.editor_change.phase,'BUFFER_CHANGED');assert.equal(result.file_effect,'APPLIED');
});
test('paired actor is Main-owned and only the first definite reservation reply resolves an exact editor URI',async()=>{
 const x=scene(),calls=[],opens=[],adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return x.value;},openArtifact:async(body)=>{opens.push(body);return {status:'RESOLVED',file_source_ref:x.source,native_path:'/unit/exact-original.py'};}});
 const value=await adapter('session.editor.reserve',{scope:x.scope,payload:x.payload},x.context);
 assert.equal(calls.length,1);assert.deepEqual(calls[0].body,x.request);assert.equal(opens.length,1);assert.equal(value.first_reservation_reply,true);assert.ok(value.editor_uri.endsWith('/unit/exact-original.py'));assert.deepEqual(opens[0].file_source_ref,x.source);
 const historical=await adapter('session.editor.get',{scope:x.scope,payload:{operation_id:x.request.operation_id}},x.context);assert.equal(historical.first_reservation_reply,false);assert.equal(opens.length,1);assert.ok(!historical.editor_uri);
 await assert.rejects(adapter('session.editor.reserve',{scope:x.scope,payload:{...x.payload,editor_client_id:'injected'}},x.context));
 await assert.rejects(adapter('session.editor.reserve',{scope:x.scope,payload:x.payload}),{code:'TRUSTED_CLIENT_REQUIRED'});
});
test('HTTP conflict or lost reservation ACK inspects original identity without granting an editor dispatch',async()=>{
 const x=scene(),calls=[];const adapter=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});if(body)throw Object.assign(Error('UNIT ACK lost'),{status:409});return x.value;},openArtifact:()=>assert.fail('No URI/native permission from recovered reservation')});
 const value=await adapter('session.editor.reserve',{scope:x.scope,payload:x.payload},x.context);assert.equal(calls.length,2);assert.equal(calls.filter(r=>r.body).length,1);assert.equal(value.first_reservation_reply,false);assert.ok(calls[1].url.includes('/editor-changes/editor_one?editor_client_id=paired_client'));
});
test('actor, exact source, lease digest and original control receipt bind one native intent',async()=>{
 const x=scene();for(const delta of [{editor_client_id:'another_client'},{request_sha256:'0'.repeat(64)},{lease_ref:{...x.value.lease_ref,sha256:'0'.repeat(64)}},{grant:{...x.value.grant,path:'../escape.py'}}]){
  const adapter=createIdeApiAdapter({request:async()=>({...x.value,...delta})});await assert.rejects(adapter('session.editor.reserve',{scope:x.scope,payload:x.payload},x.context));
 }
 const body={operation_id:'arm_one',session_ref:x.scope.session_ref,generation:x.scope.generation,target_operation_id:x.request.operation_id,lease_ref:x.value.lease_ref,editor_client_id:x.context.client_id,editor_window_id:x.request.editor_window_id,editor_nonce:x.request.editor_nonce,expected_revision:2,action:'ARM',editor_basis:{document_version:4,content_sha256:x.value.grant.source_editor_sha256,is_dirty:false},expected_saved_sha256:null,user_approved_manual_saved:false};
 const raw={id:body.operation_id,kind:'EDITOR_CHANGE_CONTROL',session_ref:x.scope.session_ref,generation:x.scope.generation,request:body,request_sha256:digest(body),status:'RETURNED',automatic_replay:false,editor_change:{...x.value,phase:'ARMED',revision:3},first_control_reply:true};
 const {session_ref,generation,editor_client_id,...payload}=body;
 const direct=await createIdeApiAdapter({request:async()=>raw})('session.editor.control',{scope:x.scope,payload},x.context);assert.equal(direct.first_control_reply,true);
 const recovered=await createIdeApiAdapter({request:async(_url,payload)=>{if(payload)throw Object.assign(Error('UNIT control reply lost'),{status:409});return raw;}})('session.editor.control',{scope:x.scope,payload},x.context);assert.equal(recovered.first_control_reply,false);assert.equal(recovered.editor_change.phase,'ARMED');
});
