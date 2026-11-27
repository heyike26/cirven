'use strict';
// Authored NOT_RUN. Local controller/transport boundary, not live IDE/SDK.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {PrivateState,CompanionController,draftKey,scopeOf}=require('../controller.cjs');
const child=require('../session-child.cjs');
const {canonical,hash}=require('../client.cjs');
function scene(t){const root=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR||os.tmpdir(),'cirven-child-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const c=new CompanionController({state:new PrivateState(root)}),ref={id:'ses_child',root_native_session_id:'ses_root',sha256:'b'.repeat(64)};
 c.selected={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:1,messages:[],children:[{id:ref.id,parentID:ref.root_native_session_id,title:'Original child',child_ref:ref}]};
 c.refreshSelected=async()=>{};
 return {c,ref,scope:scopeOf(c.selected)};}
const response=(scope,ref)=>({kind:'ORIGINAL_CHILD_VIEW',scope,child_ref:ref,messages:[{info:{id:'msg_child',sessionID:ref.id,role:'assistant'},parts:[{type:'text',text:'Saved child response'}]}],coverage:{history_complete:false,partial:true},read_only:true,model_calls:0,native_posts:0,new_owner_started:false,transcript_copied:false,observation_is_current:false});
test('guiding a currently delegated foreground child keeps the original root operation and both private drafts scoped',async t=>{
 const {c,ref,scope}=scene(t),token=child.token(scope,ref),calls=[];
 c.selected.active_operation_id='original_root';c.selected.child_guidance_targets=[{parent_operation_id:'original_root',generation:scope.generation,child_ref:ref,status:'RUNNING_FOREGROUND',agent:'explore'}];
 c.childDetail={token,value:response(scope,ref)};c.setDraft(draftKey(c.selected),0,'ROOT_DRAFT_KEEP');c.setChildDraft(token,0,'Correct the delegated task');
 assert.equal(c.snapshot().delegation.canGuide,true);
 c.client={methods:['session.child.message'],rpc:async(method,s,payload)=>{calls.push({method,s,payload});return {status:'OK',result:accepted(s,payload)};}};
 await c.sendChild(token,1);assert.equal(calls.length,1);assert.equal(calls[0].payload.expected_active_operation_id,'original_root');
 assert.equal(c.selected.active_operation_id,'original_root');assert.equal(c.snapshot().draft.text,'ROOT_DRAFT_KEEP');
 assert.equal(c.state.value.child_drafts[child.draftKey(scope,ref)].text,'');
 c.setChildDraft(token,2,'New child draft after completed send');c.selected.child_guidance_targets[0].child_ref={...ref,id:'ses_sibling'};
 assert.equal(c.snapshot().delegation.canGuide,false);await assert.rejects(c.sendChild(token,3),{code:'CHILD_CHANGED'});assert.equal(calls.length,1);
});
function accepted(scope,payload,changes={}){const request={...payload,session_ref:scope.session_ref,generation:scope.generation};return {id:payload.operation_id,kind:payload.expected_active_operation_id?'CHILD_GUIDANCE':'CHILD_MESSAGE',request_sha256:hash(canonical(request)),session_ref:scope.session_ref,generation:scope.generation,status:'ACCEPTED',automatic_replay:false,native_post_attempted:true,native_dispatch_acknowledged:true,child_ref:payload.child_ref,original_session_id:payload.child_ref.id,root_original_session_id:payload.child_ref.root_native_session_id,original_active_operation_id:payload.expected_active_operation_id,...changes};}
test('a late accepted child input clears only its exact private draft revision and preserves the root draft',async t=>{
 const {c,ref,scope}=scene(t),token=child.token(scope,ref);let release,payload;
 c.client={methods:['session.child.message'],rpc:(method,s,p)=>{assert.equal(method,'session.child.message');payload=p;return new Promise(resolve=>{release=resolve;});}};
 c.setDraft(draftKey(c.selected),0,'ROOT_UNSENT_TEXT');c.setChildDraft(token,0,'First child input');
 const sending=c.sendChild(token,1);c.setChildDraft(token,1,'NEW_CHILD_INPUT_TO_PRESERVE');
 release({status:'OK',result:accepted(scope,payload)});await sending;
 const slot=c.state.value.child_drafts[child.draftKey(scope,ref)];assert.equal(slot.text,'NEW_CHILD_INPUT_TO_PRESERVE');
 assert.equal(c.snapshot().draft.text,'ROOT_UNSENT_TEXT');assert.equal(c.state.value.requests[0].status,'OK');
});
test('a lost child send survives private-slot recovery and inspection uses only the original operation read',async t=>{
 const {c,ref,scope}=scene(t),token=child.token(scope,ref),calls=[];let original;
 c.client={methods:['session.child.message'],rpc:async(method,s,payload)=>{calls.push({method,s,payload});original=accepted(s,payload,{status:'UNKNOWN_OUTCOME',native_dispatch_acknowledged:false});throw Error('lost original child acknowledgement');}};
 c.setChildDraft(token,0,'KEEP_UNKNOWN_CHILD_INPUT');await c.sendChild(token,1);
 const old=c.state.filename,directory=path.dirname(old),disk=new PrivateState(directory);disk.recover(path.basename(old));
 const restored=new CompanionController({state:disk});restored.selected=c.selected;restored.refreshSelected=async()=>{};
 restored.client={methods:['session.child.message','session.operation'],rpc:async(method,s,payload)=>{calls.push({method,s,payload});assert.equal(method,'session.operation');assert.equal(payload.operation_id,original.id);return {status:'OK',result:original};}};
 const saved=restored.state.value.requests[0];await restored.checkResult(saved.request_id);
 assert.equal(restored.state.value.requests[0].status,'UNKNOWN');
 await assert.rejects(restored.sendChild(token,1),{code:'ORIGINAL_REQUEST_UNRESOLVED'});
 assert.equal(calls.length,2);assert.equal(restored.state.value.child_drafts[child.draftKey(scope,ref)].text,'KEEP_UNKNOWN_CHILD_INPUT');
});
test('a child receipt for a different native target cannot discard the frozen input',async t=>{
 const {c,ref,scope}=scene(t),token=child.token(scope,ref);c.setChildDraft(token,0,'Frozen child input');
 c.client={methods:['session.child.message'],rpc:async(method,s,payload)=>({status:'OK',result:accepted(s,payload,{original_session_id:'ses_foreign'})})};
 await c.sendChild(token,1);assert.equal(c.state.value.requests[0].status,'UNKNOWN');
 assert.equal(c.state.value.child_drafts[child.draftKey(scope,ref)].text,'Frozen child input');
});
test('old child draft remains locally readable without selecting or reconnecting its expired original scope',async t=>{
 const {c,ref,scope}=scene(t),token=child.token(scope,ref);c.setChildDraft(token,0,'PRIVATE_EXPIRED_CHILD_DRAFT');
 c.selected=null;c.epoch++;const before=c.snapshot().privateChildDrafts;
 assert.equal(before.entries.length,1);assert.equal(before.view,null);assert.equal(JSON.stringify(before).includes('PRIVATE_EXPIRED_CHILD_DRAFT'),false);
 c.readPrivateChildDraft(child.draftKey(scope,ref));assert.equal(c.snapshot().privateChildDrafts.view.text,'PRIVATE_EXPIRED_CHILD_DRAFT');
 assert.equal(c.state.value.requests.length,0);assert.equal(c.selected,null);
});
test('explicit child read keeps original selection, draft and pending mutations intact',async t=>{
 const {c,ref,scope}=scene(t),calls=[];c.client={methods:['session.child'],rpc:async(method,s,payload)=>{calls.push({method,s,payload});return {status:'OK',result:response(s,payload.child_ref)};}};
 const key=draftKey(c.selected);c.setDraft(key,0,'ROOT_DRAFT_TO_PRESERVE');await c.readChild(child.token(scope,ref));
 assert.equal(c.snapshot().draft.text,'ROOT_DRAFT_TO_PRESERVE');assert.equal(c.snapshot().scopeToken,key);
 assert.equal(c.snapshot().delegation.detail.value.messages[0].parts[0].text,'Saved child response');
 assert.equal(c.state.value.requests.length,0);assert.equal(calls.length,1);assert.equal(calls[0].method,'session.child');
});
test('changed child metadata and missing grants reject before transport',async t=>{
 const {c,ref,scope}=scene(t);let calls=0;c.client={methods:[],rpc:async()=>{calls++;}};
 await assert.rejects(c.readChild(child.token(scope,ref)),{code:'CAPABILITY_DENIED'});
 c.selected.children[0].child_ref={...ref,sha256:'c'.repeat(64)};
 await assert.rejects(c.readChild(child.token(scope,ref)),{code:'CHILD_CHANGED'});assert.equal(calls,0);
});
test('late child response cannot change the newly selected original discussion',async t=>{
 const {c,ref,scope}=scene(t);let release;c.client={methods:['session.child'],rpc:()=>new Promise(resolve=>{release=resolve;})};
 const pending=c.readChild(child.token(scope,ref));c.epoch++;c.selected={...c.selected,session_ref:{...c.selected.session_ref,id:'other'}};
 release({status:'OK',result:response(scope,ref)});await pending;assert.equal(c.snapshot().delegation.detail,null);
});
