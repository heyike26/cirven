'use strict';
// Original private state/controller; no model, native host or runtime is substituted.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {PrivateState,CompanionController,draftKey}=require('../controller.cjs'),{canonical,hash}=require('../client.cjs'),native=require('../native-conversation.cjs');
const scope={project_id:'project',conversation_id:'discussion'},ref=id=>({id,revision:1,sha256:hash(id)}),plan=ref('plan');
const source={kind:'local_file',project_id:scope.project_id,workspace_id:'a'.repeat(64),binding_revision:1,binding_sha256:'b'.repeat(64),path:'任务说明.md',revision:1,sha256:'c'.repeat(64)};
function conversation(){return {kind:'NATIVE_CONVERSATION',...scope,messages:[{kind:'NATIVE_MESSAGE',...scope,operation_id:'parent',status:'RETURNED',record_origin:'HISTORICAL_READONLY',input:{origin:'ORIGINAL_NATIVE_REQUEST',raw_text:'Only change the selected expression'},text:'Prepared; not yet written.',rework_previews:[{kind:'PLAN',record_ref:plan,mapping_ref:ref('mapping'),native_feedback_ref:ref('feedback'),proposal_ref:ref('proposal'),status:'PROPOSED'}]}],coverage:{returned:1,has_more:false}};}
function fixture(t){const directory=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-context83-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));const c=new CompanionController({state:new PrivateState(directory)});c.projectId=scope.project_id;c.selected=native.present(conversation(),scope);return c;}
function applied(){const request={operation_id:'apply',plan_ref:plan,expected_current_sources:[source],allow_rebase:false};return {request_id:'apply-rpc',method:'native.rework.apply',scope,parent_operation_id:'parent',operation_id:'apply',status:'OK',native_request:request,native_receipt:{kind:'NATIVE_REWORK_APPLY',...scope,parent_operation_id:'parent',operation_id:'apply',status:'APPLIED',request,request_sha256:hash(canonical(request)),automatic_retry:false,plan_ref:plan,receipt_ref:ref('receipt'),outcomes:[{path:source.path,status:'APPLIED',source_ref:source}]}};}

test('current discussion shows its unresolved row while other discussion stays preserved behind a count',t=>{
 const c=fixture(t),other={...scope,conversation_id:'other'};c.state.commit(v=>v.requests.push({request_id:'mine',method:'native.message',scope,operation_id:'own-op',status:'UNKNOWN'},{request_id:'other',method:'native.message',scope:other,operation_id:'other-op',status:'UNKNOWN'}));const before=fs.readFileSync(c.state.filename),s=c.snapshot();
 assert.deepEqual(s.pending.map(r=>r.request_id),['mine']);assert.equal(s.otherPendingCount,1);assert.deepEqual(fs.readFileSync(c.state.filename),before);assert.equal(c.state.value.requests.length,2);
});

test('a global desktop-reveal outcome remains relevant without being labelled another discussion',t=>{
 const c=fixture(t);c.state.commit(v=>v.requests.push({request_id:'reveal',method:'lifecycle.reveal',scope:{},operation_id:null,status:'UNKNOWN'},{request_id:'other',method:'native.message',scope:{...scope,conversation_id:'other'},operation_id:'other-op',status:'UNKNOWN'}));
 assert.deepEqual(c.snapshot().pending.map(r=>r.request_id),['reveal']);assert.equal(c.snapshot().otherPendingCount,1);c.selected=null;assert.deepEqual(c.snapshot().pending.map(r=>r.request_id),['reveal']);
});

test('an older generation in the same persistent discussion remains inspectable by its exact original scope',async t=>{
 const c=fixture(t),session_ref=ref('legacy'),original={project_id:'project',session_ref,generation:1};c.selected={...original,generation:2,messages:[]};c.state.commit(v=>v.requests.push({request_id:'older',method:'session.message',scope:original,operation_id:'old-op',status:'UNKNOWN'}));
 assert.equal(c.snapshot().pending.length,1);assert.equal(c.snapshot().otherPendingCount,0);assert.equal(c.snapshot().pending[0].scope.generation,1);let calls=0;c.client={rpc:async(m,s,p)=>{calls++;assert.equal(m,'session.operation');assert.deepEqual(s,original);assert.equal(p.operation_id,'old-op');return {status:'OK',result:{status:'UNKNOWN_OUTCOME'}};}};await c.inspectOperation('older');assert.equal(calls,1);assert.equal(c.state.value.requests[0].status,'UNKNOWN');
});

for(const method of ['native.message','session.message'])test('late '+method+' transport failure preserves the current discussion error and original unknown row',async t=>{
 const c=fixture(t),original=method==='native.message'?scope:{project_id:'project',session_ref:ref('legacy'),generation:1};if(method==='session.message')c.selected={...original,messages:[]};let reject;
 c.client={rpc:async()=>await new Promise((_r,no)=>{reject=no;})};const pending=c.mutation(method,original,{operation_id:'old-send',raw_text:'Original input'});
 const next={project_id:'other-project',conversation_id:'other'};c.selected=native.present({kind:'NATIVE_CONVERSATION',...next,messages:[],coverage:{returned:0,has_more:false}},next);c.error='CURRENT_DISCUSSION_ERROR';reject(Object.assign(Error('lost old reply'),{code:'TRANSPORT_TIMEOUT'}));await pending;
 assert.equal(c.error,'CURRENT_DISCUSSION_ERROR');assert.equal(c.state.value.requests[0].status,'UNKNOWN');assert.deepEqual(c.state.value.requests[0].scope,original);assert.equal(c.snapshot().pending.length,0);assert.equal(c.snapshot().otherPendingCount,1);
});

test('known legacy result from an older generation cannot clear the current generation error',async t=>{
 const c=fixture(t),original={project_id:'project',session_ref:ref('legacy'),generation:1};c.selected={...original,messages:[]};let resolve;c.client={rpc:async()=>await new Promise(yes=>{resolve=yes;})};const pending=c.mutation('session.message',original,{operation_id:'old-send',raw_text:'Original input'});
 c.selected={...original,generation:2,messages:[]};c.error='CURRENT_GENERATION_ERROR';resolve({status:'OK',result:{status:'RETURNED'}});await pending;
 assert.equal(c.error,'CURRENT_GENERATION_ERROR');assert.equal(c.state.value.requests[0].status,'OK');assert.equal(c.state.value.requests[0].scope.generation,1);
});

test('legacy native query shows an original-operation observation without confirming or rewriting the old input',async t=>{
 const c=fixture(t),key=draftKey(c.selected);c.setDraft(key,0,'Keep original private draft');c.state.commit(v=>v.requests.push({request_id:'legacy',method:'native.message',scope,operation_id:'old-op',status:'UNKNOWN',draft_key:key,draft_revision:1,draft_body_sha256:hash('Keep original private draft')}));
 const before=fs.readFileSync(c.state.filename);c.client={rpc:async m=>{assert.equal(m,'native.operation');return {status:'OK',result:{kind:'NATIVE_MESSAGE',...scope,operation_id:'old-op',request_sha256:'d'.repeat(64),status:'RETURNED'}};}};
 await c.checkResult('legacy');const row=c.snapshot().pending[0];assert.equal(row.status,'UNKNOWN');assert.equal(row.observed_status,'RETURNED');assert.equal(row.observation_only,true);assert.equal(c.state.value.drafts[key].text,'Keep original private draft');assert.equal(c.known.has('legacy'),false);assert.deepEqual(fs.readFileSync(c.state.filename),before);
});

test('legacy observation still requires the exact operation and original discussion',async t=>{
 const c=fixture(t);c.state.commit(v=>v.requests.push({request_id:'legacy',method:'native.message',scope,operation_id:'old-op',status:'UNKNOWN'}));c.client={rpc:async()=>({status:'OK',result:{kind:'NATIVE_MESSAGE',...scope,conversation_id:'other',operation_id:'old-op',request_sha256:'d'.repeat(64),status:'RETURNED'}})};
 await assert.rejects(()=>c.checkResult('legacy'),{code:'OPERATION_INPUT_MISMATCH'});assert.equal(c.snapshot().pending[0].observed_status,null);assert.equal(c.state.value.requests[0].status,'UNKNOWN');
});

test('a persisted exact applied receipt can identify the corresponding historical preview without rereading or writing',t=>{
 const c=fixture(t);c.state.commit(v=>v.requests.push(applied()));const before=fs.readFileSync(c.state.filename),s=c.snapshot();assert.deepEqual(s.native.applied,[{parent_operation_id:'parent',plan_ref:plan}]);assert.deepEqual(fs.readFileSync(c.state.filename),before);assert.equal(c.selected.messages.at(-1).parts[0].text,'Prepared; not yet written.');
});

for(const field of ['unknown','conflict','scope','parent','plan','request','outcome'])test('applied display rejects '+field+' mismatch instead of declaring success',t=>{
 const c=fixture(t),row=applied();
 if(field==='unknown')row.status='UNKNOWN';if(field==='conflict')row.native_receipt.status='CONFLICT';if(field==='scope')row.native_receipt.conversation_id='other';if(field==='parent')row.native_receipt.parent_operation_id='other-parent';if(field==='plan')row.native_receipt.plan_ref=ref('other-plan');if(field==='request')row.native_receipt.request_sha256='d'.repeat(64);if(field==='outcome')row.native_receipt.outcomes[0].source_ref={...source,path:'other.md'};
 c.state.commit(v=>v.requests.push(row));assert.deepEqual(c.snapshot().native.applied,[]);
});
