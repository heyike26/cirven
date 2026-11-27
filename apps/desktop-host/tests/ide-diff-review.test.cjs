'use strict';
// AUTHORED_NOT_RUN. Main adapter UNIT with explicit API boundary.
const test=require('node:test'),assert=require('node:assert/strict');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const {fixture}=require('../../../plugins/vscode-companion/tests/diff-fixture.cjs');
const {canonical,hash}=require('../../../plugins/vscode-companion/client.cjs');
function scene(){const f=fixture({full:true}),scope={project_id:f.session.project_id,session_ref:f.session.session_ref,generation:f.session.generation};
 f.directory.snapshot_ref.id=hash(canonical(f.scope));f.file.snapshot_ref=f.directory.snapshot_ref;
 const selection={session_ref:scope.session_ref,generation:scope.generation,workspace_ref:f.scope.workspace_ref,snapshot_ref:f.directory.snapshot_ref,diff_ref:f.entry.diff_ref,child_ref:null},disk={exists:true,sha256:f.file.after_sha256,byte_length:Buffer.byteLength(f.file.after)};
 const basis={selection,path:f.file.path,disk,review_revision:0,before_sha256:f.file.before_sha256,after_sha256:f.file.after_sha256,before_exists:true,after_exists:true};
 const prepared={kind:'DIFF_REVIEW_PREPARATION',scope:f.scope,child_ref:null,preparation_sha256:hash(canonical(basis)),review_revision:0,review:null,current_saved:disk,matches_before:false,matches_after:true,diff:f.file,read_only:true,model_calls:0,native_posts:0,workspace_writes:0,editor_dirty_state:'NOT_OBSERVED',editor_barrier:'NOT_ACQUIRED',effect_scope:'HISTORICAL_USER_REVIEW_ONLY_NOT_FILE_APPLICATION_OR_ROLLBACK'};
 const payload={workspace_ref:selection.workspace_ref,snapshot_ref:selection.snapshot_ref,diff_ref:selection.diff_ref,child_ref:null};
 const request={...selection,operation_id:'review_op',decision:'ACCEPT',preparation_sha256:prepared.preparation_sha256,expected_review_revision:0};
 const result={id:request.operation_id,kind:'DIFF_REVIEW',session_ref:scope.session_ref,generation:scope.generation,request_sha256:hash(canonical(request)),request,status:'RETURNED',automatic_replay:false,review_ref:{id:hash(canonical({project_id:scope.project_id,session_ref:scope.session_ref,generation:scope.generation,workspace_ref:selection.workspace_ref,child_ref:selection.child_ref,diff_ref:selection.diff_ref})),revision:1,sha256:'7'.repeat(64)},review_decision:'ACCEPT',review_selection:selection,preparation_sha256:request.preparation_sha256,current_saved:disk,workspace_writes:0,model_calls:0,native_posts:0,effect_scope:prepared.effect_scope};
 return {f,scope,selection,prepared,payload,request,result};}
test('full historical review preparation verifies saved-basis hash and never opens a file or invokes an owner',async()=>{
 const x=scene(),calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return x.prepared;},openArtifact:()=>assert.fail('No file open for metadata review')});
 const value=await dispatch('session.diff.prepare',{scope:x.scope,payload:x.payload});assert.equal(value.matches_after,true);assert.equal(value.editor_barrier,'NOT_ACQUIRED');assert.equal(value.workspace_writes,0);assert.deepEqual(calls[0].body,x.selection);
 for(const delta of [{current_saved:{...x.prepared.current_saved,sha256:'0'.repeat(64)}},{matches_after:false},{workspace_writes:1},{editor_barrier:'ACQUIRED'}])await assert.rejects(createIdeApiAdapter({request:async()=>({...x.prepared,...delta})})('session.diff.prepare',{scope:x.scope,payload:x.payload}));
});
test('review POST acknowledgement loss inspects exactly one original metadata receipt without replay',async()=>{
 const x=scene(),calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});if(body)throw Object.assign(Error('UNIT lost metadata receipt'),{status:409});return x.result;}});
 const payload={...x.payload,operation_id:x.request.operation_id,decision:'ACCEPT',preparation_sha256:x.request.preparation_sha256,expected_review_revision:0};
 const value=await dispatch('session.diff.review',{scope:x.scope,payload});assert.equal(value.status,'RETURNED');assert.equal(value.review_decision,'ACCEPT');assert.equal(value.workspace_writes,0);assert.equal(calls.length,2);assert.equal(calls.filter(c=>c.body).length,1);assert.ok(calls[1].url.endsWith('/diff-reviews/review_op'));
 const get=await createIdeApiAdapter({request:async()=>x.result})('session.diff.review.operation',{scope:x.scope,payload:{operation_id:'review_op'}});assert.equal(get.request_sha256,x.result.request_sha256);
});
test('wrong metadata request, decision or scope cannot become an accepted review receipt',async()=>{
 const x=scene();for(const delta of [{review_decision:'REJECT'},{request_sha256:'0'.repeat(64)},{generation:3,request:{...x.request,generation:3}},{review_selection:{...x.selection,child_ref:{id:'ses_wrong'}}},{workspace_writes:1}])await assert.rejects(createIdeApiAdapter({request:async()=>({...x.result,...delta})})('session.diff.review.operation',{scope:x.scope,payload:{operation_id:'review_op'}}));
});
