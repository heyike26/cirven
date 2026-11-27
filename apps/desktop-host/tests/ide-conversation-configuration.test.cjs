'use strict';
// AUTHORED_NOT_RUN: actual Main boundary, explicit UNIT canonical HTTP transport.
const test=require('node:test'),assert=require('node:assert/strict'),{createHash}=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const {connectionBody}=require('../ide-canonical-conversations.cjs');
const {confirmation}=require('../ide-conversation-configuration.cjs');
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex');
const scope={project_id:null,conversation_ref:{id:'native:'+'a'.repeat(64),revision:1}},context={grants:{project_ids:null}},profile={id:'saved',revision:1,sha256:'b'.repeat(64)};
const view=()=>({version:1,project_id:null,conversation_ref:scope.conversation_ref,sources:[],current_owner:{status:'LEGACY'}});
function packet(s=scope){const request=connectionBody(s,{operation_id:'frozen_config',provider_profile_ref:profile,capability_binding_sha256:'c'.repeat(64)}),preparation_sha256='d'.repeat(64),execution_project_id=s.project_id;return {request,preparation_sha256,original_source:null,execution_project_id,confirmation_sha256:confirmation(request,preparation_sha256,execution_project_id)};}
function receipt(p){const body={...p.request};delete body.operation_id;delete body.project_id;return {version:1,operation_id:p.request.operation_id,request_sha256:sha(body),conversation_ref:p.request.conversation_ref,organization_project_id:p.request.project_id,execution_confirmation_sha256:p.confirmation_sha256,execution_project_id:p.execution_project_id,conversation_bound:false,session:null,status:'UNKNOWN_OUTCOME',owner_started_by_query:false,model_calls_by_query:0,source_changed_by_query:false,automatic_post_replay:false};}

// batch44 AUTHORED_NOT_RUN: wire admission and actual Main transport boundary.
test('explicit observation is part of the confirmed request without adding workspace writes',async()=>{
 const p=packet(),calls=[];p.request.workspace_ref={id:'1'.repeat(64),revision:1,binding_sha256:'2'.repeat(64)};p.request.allowed_effects=['BACKGROUND_COMMAND'];p.request.command_allowlist=['python check.py'];p.request.command_observation_paths=['reports/tests.xml'];p.confirmation_sha256=confirmation(p.request,p.preparation_sha256,p.execution_project_id);
 const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return route.endsWith('/scope')?view():receipt(p);}});
 await api('conversation.configure',{scope,payload:p},context);assert.deepEqual(calls.find(c=>c.body).body.request.command_observation_paths,['reports/tests.xml']);assert.equal(p.request.allowed_effects.includes('WORKSPACE_WRITE'),false);
});
test('glob private and changed observation scope fail before any Main transport dispatch',async()=>{
 for(const paths of [['*.xml'],['../report.xml'],['.git/config'],['same','same'],['f'.repeat(1025)]]){const p=packet();p.request.command_observation_paths=paths;p.confirmation_sha256=confirmation(p.request,p.preparation_sha256,p.execution_project_id);let calls=0;const api=createIdeApiAdapter({request:async()=>{calls++;throw Error('Must not read or post');}});await assert.rejects(api('conversation.configure',{scope,payload:p},context));assert.equal(calls,0);}
 const p=packet();p.request.command_observation_paths=[];let calls=0;const api=createIdeApiAdapter({request:async()=>{calls++;}});await assert.rejects(api('conversation.configure',{scope,payload:p},context));assert.equal(calls,0);
});

test('preparation reads only saved metadata and checks its authority digest',async()=>{
 const authority={conversation_ref:scope.conversation_ref,project_id:null,original_source:null},prepared={kind:'CONVERSATION_CONFIGURATION_PREPARATION',authority,preparation_sha256:sha(authority),read_only:true,model_calls:0,owner_started:false,permission_granted:false,automatic_message_replay:false,workspaces:[],mcp_servers:[]},calls=[];
 const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return route.endsWith('/prepare')?prepared:view();}});
 const value=await api('conversation.configuration.prepare',{scope,payload:{}},context);assert.equal(value.owner_started,false);assert.equal(calls.filter(v=>v.body).length,1);assert.equal(calls.some(v=>/snapshot|\/load|\/messages|\/open$/.test(v.route)),false);
});
test('lost configuration reply inspects its frozen operation and never posts a replacement',async()=>{
 const p=packet(),calls=[];const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});if(route.endsWith('/scope'))return view();if(body){assert.deepEqual(body,p);throw Error('UNIT lost reply');}return receipt(p);}});
 const result=await api('conversation.configure',{scope,payload:p},context);assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(result.confirmation_sha256,p.confirmation_sha256);assert.equal(calls.filter(v=>v.body).length,1);assert.match(calls.at(-1).route,/open-operations\/frozen_config$/);
});
test('project-limited peers cannot configure an ungranted execution project',async()=>{
 const s={...scope,project_id:'logical'},p=packet(s);p.execution_project_id='foreign';p.confirmation_sha256=confirmation(p.request,p.preparation_sha256,p.execution_project_id);let posts=0;
 const api=createIdeApiAdapter({request:async(route,body)=>{if(body)posts++;return {...view(),project_id:'logical'};}});
 await assert.rejects(api('conversation.configure',{scope:s,payload:p},{grants:{project_ids:['logical']}}),{code:'EXECUTION_SCOPE_DENIED'});assert.equal(posts,0);
});
test('unknown fields cannot smuggle an endpoint, credential or host path into configuration',async()=>{
 for(const field of ['provider_url','api_key','root']){const p=packet();p.request[field]='UNIT_REJECT_BEFORE_POST';p.confirmation_sha256=confirmation(p.request,p.preparation_sha256,p.execution_project_id);let posts=0;const api=createIdeApiAdapter({request:async()=>{posts++;throw Error('Must not reach transport');}});await assert.rejects(api('conversation.configure',{scope,payload:p},context));assert.equal(posts,0);}
});
test('another confirmation cannot satisfy the original configuration receipt',async()=>{
 const p=packet(),value={...receipt(p),execution_confirmation_sha256:'f'.repeat(64)};const api=createIdeApiAdapter({request:async route=>route.endsWith('/scope')?view():value});await assert.rejects(api('conversation.configure',{scope,payload:p},context));
});
test('original configuration receipt stays readable after the current conversation metadata changes',async()=>{
 const p=packet(),value=receipt(p),api=createIdeApiAdapter({request:async route=>{assert.ok(!route.endsWith('/scope'));return value;}});
 const result=await api('conversation.configuration.operation',{scope,payload:{operation_id:p.request.operation_id,request_sha256:value.request_sha256,confirmation_sha256:p.confirmation_sha256}},context);assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(result.owner_started_by_query,false);
});
test('user project names are retained while exact internal execution markers are omitted from navigation',async()=>{
 const api=createIdeApiAdapter({request:async()=>({projects:[{id:'real_user',name:'EEAS conversation',description:'USER_OWN_PROJECT'},{id:'internal_root',name:'Runtime',description:'eeas.original-agent-session.internal-project.v1'},{id:'internal_native',name:'Runtime 2',description:'EEAS_INTERNAL_DESKTOP_RUNTIME_V1'}]})});const value=await api('projects.list',{scope:{},payload:{}},context);assert.deepEqual(value.projects.map(p=>p.id),['real_user']);assert.equal(value.coverage.available,1);
});


// batch33 AUTHORED_NOT_RUN: command admission uses only reference fields.
function continuationInput(p){const r={...p.request};delete r.operation_id;delete r.project_id;return {operation_id:p.request.operation_id,request_sha256:sha(r),confirmation_sha256:p.confirmation_sha256};}
function pendingConfiguration(p,change={}){return {...receipt(p),session:null,conversation_bound:false,status:'FAILED_NOT_STARTED',configuration_original_source:null,...change};}
test('continuation reads original receipt before one reference-only POST then observes it',async()=>{
 const p=packet(),calls=[];let posted=false;const original=pendingConfiguration(p);
 const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});if(route.endsWith('/scope'))return view();if(body){assert.match(route,/connection-configuration\/continue$/);assert.deepEqual(body,{...continuationInput(p),project_id:null});posted=true;throw Error('UNIT lost continue acknowledgement');}return posted?{...original,status:'UNKNOWN_OUTCOME'}:original;}});
 const result=await api('conversation.configuration.continue',{scope,payload:continuationInput(p)},context);assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(calls.filter(c=>c.body).length,1);assert.match(calls[0].route,/open-operations/);assert.match(calls.at(-1).route,/open-operations/);assert.doesNotMatch(JSON.stringify(calls),/raw_text|allowed_effects/);
});
for(const status of ['PROCESSING','UNKNOWN_OUTCOME'])test('Main observes '+status+' and never posts continuation',async()=>{
 const p=packet();let writes=0;const api=createIdeApiAdapter({request:async(_route,body)=>{if(body)writes++;return pendingConfiguration(p,{status});}});await api('conversation.configuration.continue',{scope,payload:continuationInput(p)},context);assert.equal(writes,0);
});
test('continuation refuses an ungranted old owner even when new execution project is granted',async()=>{
 const s={...scope,project_id:'logical'},p=packet(s);let writes=0;const api=createIdeApiAdapter({request:async(_route,body)=>{if(body)writes++;return pendingConfiguration(p,{organization_project_id:'logical',configuration_original_source:{project_id:'retired_private',session_ref:profile,generation:1}});}});
 await assert.rejects(api('conversation.configuration.continue',{scope:s,payload:continuationInput(p)},{grants:{project_ids:['logical']}}),{code:'EXECUTION_SCOPE_DENIED'});assert.equal(writes,0);
});
test('continuation refuses an ungranted new execution project before mutation',async()=>{
 const s={...scope,project_id:'logical'},p=packet(s);let writes=0;const api=createIdeApiAdapter({request:async(_route,body)=>{if(body)writes++;return pendingConfiguration(p,{organization_project_id:'logical',execution_project_id:'foreign'});}});
 await assert.rejects(api('conversation.configuration.continue',{scope:s,payload:continuationInput(p)},{grants:{project_ids:['logical']}}),{code:'EXECUTION_SCOPE_DENIED'});assert.equal(writes,0);
});
test('continuation with mismatched original hash cannot POST',async()=>{
 const p=packet();let writes=0;const api=createIdeApiAdapter({request:async(_route,body)=>{if(body)writes++;return pendingConfiguration(p,{request_sha256:'f'.repeat(64)});}});await assert.rejects(api('conversation.configuration.continue',{scope,payload:continuationInput(p)},context));assert.equal(writes,0);
});
test('continuation rejects changed scope before mutation and keeps original receipt readable',async()=>{
 const p=packet();let writes=0;const api=createIdeApiAdapter({request:async(route,body)=>{if(body)writes++;return route.endsWith('/scope')?{...view(),conversation_ref:{...scope.conversation_ref,revision:2}}:pendingConfiguration(p);}});await assert.rejects(api('conversation.configuration.continue',{scope,payload:continuationInput(p)},context));assert.equal(writes,0);
});
test('continuation rejects a request body masquerading as reference metadata',async()=>{
 const p=packet();let writes=0;const api=createIdeApiAdapter({request:async(_route,body)=>{if(body)writes++;throw Error('must reject before I/O');}});await assert.rejects(api('conversation.configuration.continue',{scope,payload:{...continuationInput(p),request:p.request}},context));assert.equal(writes,0);
});

// batch35 AUTHORED_NOT_RUN: candidate states share exact continuation admission.
for(const status of ['CANDIDATE_READY','CANDIDATE_CLOSED'])test('Main permits explicit '+status+' continuation after original receipt and grants',async()=>{
 const p=packet(),calls=[];let continued=false;const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});if(route.endsWith('/scope'))return view();if(body){assert.match(route,/connection-configuration\/continue$/);assert.deepEqual(body,{...continuationInput(p),project_id:null});continued=true;return {};}return continued?{...pendingConfiguration(p),status:'RETURNED',conversation_bound:true,session:{project_id:'internal',connected_owner:true,owner_status:'READY',configuration_current:true,observation_is_current:true}}:pendingConfiguration(p,{status});}});
 const result=await api('conversation.configuration.continue',{scope,payload:continuationInput(p)},context);assert.equal(result.status,'RETURNED');assert.equal(calls.filter(c=>c.body).length,1);
});

// batch36 AUTHORED_NOT_RUN: current organization acknowledgement / original SDK key.
function movedAdoption(p=packet()){
 const current={project_id:'moved',conversation_ref:{...scope.conversation_ref,revision:2}},old=receipt(p),original={operation_id:p.request.operation_id,project_id:scope.project_id,request_sha256:old.request_sha256,confirmation_sha256:p.confirmation_sha256};
 const preparation={original,conversation_ref:current.conversation_ref,project_id:current.project_id},authority={...preparation,execution_project_id:null,original_source:null,candidate:null},preparation_sha256=sha(authority);
 const prepared={kind:'CONFIGURATION_ADOPTION_PREPARATION',preparation,authority,preparation_sha256,adoption_allowed:true,read_only:true,model_calls:0,owner_started:false,permission_granted:false,automatic_message_replay:false};const body={operation_id:'ack_current_scope',preparation,preparation_sha256},command={...body,confirmation_sha256:sha(body)};
 const result={kind:'CONFIGURATION_ADOPTION',operation_id:body.operation_id,request_sha256:sha(command),original,conversation_ref:current.conversation_ref,project_id:current.project_id,preparation_sha256,confirmation_sha256:command.confirmation_sha256,accepted:true,current:true,model_calls:0,owner_started:false,permission_granted:false,automatic_message_replay:false};return {current,original,prepared,command,result};
}
test('metadata adoption lost reply inspects original metadata key without SDK or configuration replay',async()=>{
 const x=movedAdoption(),calls=[];const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});if(route.endsWith('/prepare'))return x.prepared;if(body)throw Error('UNIT lost metadata ack');return x.result;}});
 const r=await api('conversation.configuration.adoption.command',{scope:x.current,payload:x.command},context);assert(r.current);assert.equal(calls.filter(c=>c.body&&!c.route.endsWith('/prepare')).length,1);assert.match(calls.at(-1).route,/configuration-adoption-operations\/ack_current_scope$/);assert(!calls.some(c=>/\/load|\/open$|\/messages|\/connection-configuration/.test(c.route)));
});
test('adoption receipt is readonly without current scope or preparation calls',async()=>{
 const x=movedAdoption(),calls=[];const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return {...x.result,current:false};}});
 const r=await api('conversation.configuration.adoption.operation',{scope:x.current,payload:{operation_id:x.command.operation_id,request_sha256:x.result.request_sha256,original:x.original,confirmation_sha256:x.command.confirmation_sha256}},context);assert(!r.current);assert.equal(calls.length,1);assert(!calls[0].body);
});
test('adoption cannot borrow an ungranted original organization even when current is granted',async()=>{
 const p=packet({...scope,project_id:'old_private'}),x=movedAdoption(p);x.original.project_id='old_private';let calls=0;const api=createIdeApiAdapter({request:async()=>{calls++;return x.prepared;}});
 await assert.rejects(api('conversation.configuration.adoption.prepare',{scope:x.current,payload:{original:x.original}},{grants:{project_ids:['moved']}}),{code:'EXECUTION_SCOPE_DENIED'});assert.equal(calls,0);
});
test('explicit continuation after adoption keeps old business scope while checking new organization',async()=>{
 const p=packet(),x=movedAdoption(p),calls=[];const original=pendingConfiguration(p,{configuration_adoption:{operation_id:'ack_current_scope',conversation_ref:x.current.conversation_ref,project_id:'moved',authority_sha256:'e'.repeat(64),execution_project_id:null,current:true}});
 const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});if(route.endsWith('/scope'))return {...view(),project_id:'moved',conversation_ref:x.current.conversation_ref};return original;}});
 await api('conversation.configuration.continue',{scope,payload:continuationInput(p)},context);const post=calls.find(c=>c.body);assert(post);assert.equal(post.body.project_id,null);assert.equal(post.body.operation_id,p.request.operation_id);assert(calls.some(c=>c.route.endsWith('/scope')));
});
test('new organization lost grant prevents original SDK continuation',async()=>{
 const allowedScope={...scope,project_id:'old_organization'},p=packet(allowedScope),x=movedAdoption(p);let posts=0;const original=pendingConfiguration(p,{configuration_adoption:{operation_id:'ack_current_scope',conversation_ref:x.current.conversation_ref,project_id:'moved',authority_sha256:'e'.repeat(64),execution_project_id:p.execution_project_id,current:true}}),api=createIdeApiAdapter({request:async(_route,body)=>{if(body)posts++;return original;}});
 await assert.rejects(api('conversation.configuration.continue',{scope:allowedScope,payload:continuationInput(p)},{grants:{project_ids:['old_organization']}}),{code:'EXECUTION_SCOPE_DENIED'});assert.equal(posts,0);
});
for(const status of [408,429])test('ambiguous metadata HTTP '+status+' plus missing receipt stays unresolved',async()=>{
 const x=movedAdoption(),api=createIdeApiAdapter({request:async(route,body)=>{if(route.endsWith('/prepare'))return x.prepared;throw Object.assign(Error('UNIT ambiguous acknowledgement'),{status:body?status:404});}});
 await assert.rejects(api('conversation.configuration.adoption.command',{scope:x.current,payload:x.command},context),error=>error.code!=='API_REQUEST_REJECTED');
});

// batch38 AUTHORED_NOT_RUN: replacement routing does not change old operation outcomes.
function superseded(p=packet(),currentProject='current_logical'){
 const original=continuationInput(p),value={kind:'SUPERSEDED_CONFIGURATION',original:{...original,project_id:p.request.project_id},current:{operation_id:'new_configuration',project_id:currentProject,request_sha256:'1'.repeat(64),provider_generation:2,confirmation_sha256:'2'.repeat(64)},current_scope:{project_id:currentProject,conversation_ref:{...p.request.conversation_ref,revision:2}},original_source:{project_id:'new_execution',session_ref:{id:'new_actor',revision:1,sha256:'3'.repeat(64)},generation:1},provider_profile_ref:profile,read_only:true,owner_started:false,model_calls:0,permission_granted:false,automatic_message_replay:false,old_execution_outcome_changed:false};return {...value,basis_sha256:sha(value)};
}
test('Main original observation forwards granted replacement metadata and retains old UNKNOWN',async()=>{const p=packet(),d=superseded(p),calls=[];const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return pendingConfiguration(p,{status:'UNKNOWN_OUTCOME',configuration_disposition:d});}});const r=await api('conversation.configuration.operation',{scope,payload:continuationInput(p)},context);assert.equal(r.status,'UNKNOWN_OUTCOME');assert.deepEqual(r.configuration_disposition,d);assert.equal(calls.length,1);assert(!calls[0].body);});
test('Main superseded continuation only observes its original operation',async()=>{const p=packet(),d=superseded(p),calls=[];const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return pendingConfiguration(p,{configuration_disposition:d});}});const r=await api('conversation.configuration.continue',{scope,payload:continuationInput(p)},context);assert.equal(r.status,'FAILED_NOT_STARTED');assert.equal(calls.length,1);assert(!calls[0].body);});
test('limited peer gets no replacement project or actor metadata without those grants',async()=>{const s={...scope,project_id:'old_logical'},p=packet(s),d=superseded(p,'private_current');const api=createIdeApiAdapter({request:async()=>pendingConfiguration(p,{configuration_disposition:d})});const r=await api('conversation.configuration.operation',{scope:s,payload:continuationInput(p)},{grants:{project_ids:['old_logical']}});assert.equal(r.configuration_disposition,undefined);assert.equal(r.configuration_replaced_without_access,true);assert(!JSON.stringify(r).includes('private_current'));assert(!JSON.stringify(r).includes('new_execution'));});
