'use strict';
// AUTHORED_NOT_RUN: actual Main facade, UNIT canonical HTTP/owner boundary.
const test=require('node:test'),assert=require('node:assert/strict'),{createHash}=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex');
const reference={id:'native:'+'a'.repeat(64),revision:1},scope={project_id:null,conversation_ref:reference},context={grants:{project_ids:null},signal:new AbortController().signal};
function view(){return {version:1,project_id:null,conversation_ref:reference,title:null,archived:false,sources:[],current_owner:{status:'LEGACY'}};}
function history(){return {version:1,project_id:null,conversation_ref:reference,messages:[],next_cursor:null,coverage:{complete:true},execution_authorized:false,automatic_input_authorized:false,file_write_authorized:false};}

test('canonical input attachment exposes no open authority to a legacy or differently scoped pairing',async()=>{
 const row={id:'f'.repeat(64),role:'user',text:'check attached Word file',text_truncated:false,text_sha256:'e'.repeat(64),
   operation_id:'old_message',source:{kind:'original',project_id:'original_project',session_id:'session'},
   input_file:{filename:'review.docx',verified:true,openable:true,private_path:'C:/private/review.docx'}};
 const logical={...scope,project_id:'logical_project'};
 const api=createIdeApiAdapter({request:async route=>route.includes('/history?')
   ?{...history(),project_id:logical.project_id,messages:[row]}:{...view(),project_id:logical.project_id}});
 const read=async (project_ids,methods)=>{
   const result=await api('conversation.get',{scope:logical,payload:{}},{grants:{project_ids,methods}});
   return result.canonical_history[0].input_file;
 };
 assert.equal(await read(['logical_project'],['conversation.file.open']),undefined);
 assert.equal((await read(null,[])).open_allowed,false);
 assert.deepEqual(await read(null,['conversation.file.open']),{
   filename:'review.docx',execution_project_id:'original_project',open_allowed:true});
});

test('canonical open uses latest registered Word version and rechecks grants before opening',async()=>{
 const payload={operation_id:'old_message',execution_project_id:'original_project'};
 const baseline={kind:'local_file',project_id:payload.execution_project_id,workspace_id:'a'.repeat(64),
   binding_revision:1,binding_sha256:'b'.repeat(64),path:'review.docx',revision:1,sha256:'c'.repeat(64)};
 const latest={...baseline,revision:3,sha256:'d'.repeat(64)};
 const resolved={kind:'CANONICAL_INPUT_FILE',...scope,operation_id:payload.operation_id,file_source_ref:latest,baseline_file_source_ref:baseline};
 const grant={project_ids:null,methods:['conversation.file.open']};
 const revoked={...grant,methods:[]};
 const calls=[];
 let result=resolved,live=grant;
 const api=createIdeApiAdapter({
   request:async(route,body)=>{calls.push(route);assert.deepEqual(body,{...scope,...payload});return result;},
   openArtifact:async(body,{authorize})=>{
     authorize();
     calls.push('shell.open');
     assert.deepEqual(body,{kind:'workspace-file',target:'native',project_id:payload.execution_project_id,file_source_ref:latest});
     return {status:'OPEN_REQUESTED',file_source_ref:latest};
   }
 });
 const invoke=()=>api('conversation.file.open',{scope,payload},{grants:grant,currentGrants:()=>live});
 const opened=await invoke();
 assert.equal(opened.status,'OPEN_REQUESTED');
 assert.equal(opened.editor_buffer_modified,false);
 assert.equal(opened.native_software_open_confirmed,false);
 assert.deepEqual(opened.file_source_ref,latest);
 assert.deepEqual(calls,['/api/desktop-host/native-open/conversation-file','shell.open']);
 result={...resolved,file_source_ref:{...latest,path:'wrong.docx'}};
 await assert.rejects(invoke(),{code:'ARTIFACT_CHANGED'});
 result=resolved;
 let grantsChecked=0;
 await assert.rejects(api('conversation.file.open',{scope,payload},{
   currentGrants:()=>++grantsChecked===1?grant:revoked
 }),{code:'SCOPE_DENIED'});
 live=revoked;
 await assert.rejects(invoke(),{code:'SCOPE_DENIED'});
 assert.equal(calls.filter(x=>x==='shell.open').length,1);
});

test('empty Chats metadata opens with no fabricated owner and no SDK refresh',async()=>{
 const calls=[],api=createIdeApiAdapter({request:async route=>{calls.push(route);return route.includes('/history?')?history():view();}});
 const value=await api('conversation.get',{scope,payload:{}},context);assert.equal(value.owner,null);assert.equal(value.connection_state,'EMPTY');assert.deepEqual(value.canonical_history,[]);assert.equal(value.owner_started_by_query,false);assert.equal(calls.some(p=>p.includes('agent-sessions')),false);
});
test('listing preserves an unenrolled source as revision-zero metadata for explicit selection',async()=>{
 const source={kind:'original',project_id:'project',session_ref:{id:'b'.repeat(32),revision:1,sha256:'c'.repeat(64)},generation:0,provider_profile_ref:null,workspace_ref:null};let body;
 const api=createIdeApiAdapter({request:async(route,value)=>{body=value;if(route.includes('?'))return {version:1,items:[{id:reference.id,project_id:'project',title:'OLD_USER_TITLE',source,organization:{archived:false}}],next_cursor:null,coverage:{complete:true}};return {...view(),project_id:'project',execution_started:false,files_moved:false};}});
 const listed=await api('conversation.list',{scope:{project_id:'project'},payload:{}},context);assert.equal(listed.conversations[0].conversation_ref.revision,0);
 await api('conversation.select',{scope:{project_id:'project',conversation_ref:{...reference,revision:0}},payload:{source_ref:source}},context);assert.deepEqual(body,{source});
});
test('project-restricted peers cannot borrow Chats or a different logical project',async()=>{
 let calls=0;const api=createIdeApiAdapter({request:async()=>{calls++;return {...view(),project_id:'foreign'};}}),restricted={grants:{project_ids:['project']}};
 await assert.rejects(api('conversation.get',{scope,payload:{}},restricted),{code:'SCOPE_DENIED'});assert.equal(calls,0);
 await assert.rejects(api('conversation.get',{scope:{project_id:'project',conversation_ref:reference},payload:{}},restricted));
});
test('a moved conversation never exposes or controls an unauthorized execution owner',async()=>{
 const calls=[],target={project_id:'old_project',session_ref:{id:'b'.repeat(32),revision:1,sha256:'c'.repeat(64)},generation:1};
 const api=createIdeApiAdapter({request:async route=>{calls.push(route);return route.includes('/history?')?{...history(),project_id:'new_project'}:{...view(),project_id:'new_project',sources:[{original_source:{kind:'original',...target}}],current_owner:{status:'CURRENT',original_source:target}};}});
 const value=await api('conversation.get',{scope:{project_id:'new_project',conversation_ref:reference},payload:{}},{grants:{project_ids:['new_project']}});assert.equal(value.execution_access,false);assert.equal(value.owner,null);assert.equal(calls.some(p=>p.includes('/snapshot')),false);
});
test('first message preserves exact canonical body and a lost acknowledgement only queries its original key',async()=>{
 const calls=[],profile={id:'saved_model',revision:2,sha256:'b'.repeat(64)};let frozen;
 const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});if(route.endsWith('/scope'))return view();if(body){frozen=body;throw Error('UNIT lost reply');}return {version:1,operation_id:'message_original',conversation_ref:reference,request_sha256:sha(frozen),status:'PROCESSING',operation:{native_dispatch_acknowledged:true},original_source:{project_id:'execution',session_ref:{id:'c'.repeat(32),revision:1,sha256:'d'.repeat(64)},generation:1},model_calls_by_query:0,owner_started_by_query:false,automatic_post_replay:false};}});
 const value=await api('conversation.message',{scope,payload:{operation_id:'message_original',provider_profile_ref:profile,raw_text:'KEEP_EXACT_USER_INPUT',agent:'plan'}},context);
 assert.deepEqual(frozen,{operation_id:'message_original',conversation_ref:reference,provider_profile_ref:profile,raw_text:'KEEP_EXACT_USER_INPUT',agent:'plan',original_source:null,input_attachments:[]});assert.equal(calls.filter(c=>c.body).length,1);assert.equal(value.accepted,true);assert.match(calls.at(-1).route,/messages\/message_original$/);
});
test('a historical message receipt remains readable by its original hash after metadata revision changes',async()=>{
 const api=createIdeApiAdapter({request:async route=>{assert.ok(!route.endsWith('/scope'));return {version:1,operation_id:'original',conversation_ref:reference,request_sha256:'b'.repeat(64),status:'UNKNOWN_OUTCOME',operation:null,original_source:null,model_calls_by_query:0,owner_started_by_query:false,automatic_post_replay:false};}});
 const value=await api('conversation.operation',{scope,payload:{operation_id:'original',request_sha256:'b'.repeat(64)}},context);assert.equal(value.status,'UNKNOWN_OUTCOME');assert.equal(value.accepted,false);
});
test('history text has one shared bounded projection while preserving row identity, cursor and full text hash',async()=>{
 const text='😀'.repeat(10000),row={id:'f'.repeat(64),role:'assistant',text,text_truncated:false,text_sha256:'e'.repeat(64),source:{kind:'native'}};
 const api=createIdeApiAdapter({request:async route=>route.includes('/history?')?{...history(),messages:Array.from({length:30},(_,index)=>({...row,id:index.toString(16).padStart(64,'0')})),next_cursor:'original_history_cursor'}:view()});
 const value=await api('conversation.get',{scope,payload:{}},context);assert.equal(value.canonical_history.length,30);assert.ok(value.canonical_history.reduce((n,row)=>n+Array.from(row.text).length,0)<=65536);assert.equal(value.canonical_history.every(row=>row.text_truncated&&row.text_sha256==='e'.repeat(64)),true);assert.equal(value.next_history_cursor,'original_history_cursor');
});


// batch34 AUTHORED_NOT_RUN: shared existing owner selection, metadata only.
const existingOwner={project_id:'execution',session_ref:{id:'actor',revision:1,sha256:'d'.repeat(64)},generation:3};
function ownerChoicePacket(){return {operation_id:'choose_existing',original_source:existingOwner,expected_selection_operation_id:null};}
function ownerChoiceReceipt(p=ownerChoicePacket(),s=scope){const request={...p,project_id:s.project_id,conversation_ref:s.conversation_ref};return {kind:'ORIGINAL_OWNER_SELECTION',operation_id:p.operation_id,request_sha256:sha(request),project_id:s.project_id,conversation_ref:s.conversation_ref,original_source:p.original_source,selection_saved:true,selection_applied:true,model_calls:0,owner_started:false,permission_granted:false,automatic_message_replay:false};}
test('owner preparation is metadata only and filters candidates outside project grants',async()=>{
 const s={project_id:'logical',conversation_ref:reference},p={kind:'ORIGINAL_OWNER_PREPARATION',project_id:'logical',conversation_ref:reference,current_owner:{status:'LEGACY'},selection_allowed:true,candidates:[{original_source:existingOwner},{original_source:{...existingOwner,project_id:'foreign'}}],read_only:true,model_calls:0,owner_started:false,permission_granted:false};let calls=0;
 const api=createIdeApiAdapter({request:async(route,body)=>{calls++;assert.match(route,/original-owner\/prepare$/);assert.deepEqual(body,{conversation_ref:reference,project_id:'logical'});return p;}});const result=await api('conversation.owner.prepare',{scope:s,payload:{}},{grants:{project_ids:['logical','execution']}});assert.equal(result.candidates.length,1);assert.equal(result.hidden_by_project_grants,1);assert.equal(calls,1);
});
test('lost owner selection acknowledgement resolves the frozen original metadata operation',async()=>{
 const p=ownerChoicePacket(),calls=[];const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});if(body){assert.deepEqual(body,{...p,project_id:null,conversation_ref:reference});throw Error('UNIT lost acknowledgement');}return ownerChoiceReceipt(p);}});const r=await api('conversation.owner.select',{scope,payload:p},context);assert(r.selection_saved&&r.selection_applied);assert.equal(calls.filter(c=>c.body).length,1);assert.match(calls.at(-1).route,/original-owner-operations\/choose_existing$/);assert.doesNotMatch(JSON.stringify(calls),/agent-sessions|raw_text|allowed_effects/);
});
test('owner selection checks the original execution project grant before POST',async()=>{
 const s={project_id:'logical',conversation_ref:reference};let calls=0;const api=createIdeApiAdapter({request:async()=>{calls++;}});await assert.rejects(api('conversation.owner.select',{scope:s,payload:ownerChoicePacket()},{grants:{project_ids:['logical']}}),{code:'EXECUTION_SCOPE_DENIED'});assert.equal(calls,0);
});
test('owner selection operation is a historical read without starting or selecting again',async()=>{
 const p=ownerChoicePacket(),r={...ownerChoiceReceipt(p),selection_applied:false},calls=[];const api=createIdeApiAdapter({request:async(route,body)=>{calls.push({route,body});return r;}});const result=await api('conversation.owner.operation',{scope,payload:{operation_id:p.operation_id,request_sha256:r.request_sha256,original_source:existingOwner}},context);assert(result.selection_saved&&!result.selection_applied);assert(calls.every(c=>!c.body));
});
test('owner selection rejects new permissions or a substituted receipt',async()=>{
 const p=ownerChoicePacket();let posts=0;const api=createIdeApiAdapter({request:async(_route,body)=>{if(body)posts++;return {...ownerChoiceReceipt(p),original_source:{...existingOwner,generation:4}};}});await assert.rejects(api('conversation.owner.select',{scope,payload:{...p,allowed_effects:['WORKSPACE_WRITE']}},context));assert.equal(posts,0);await assert.rejects(api('conversation.owner.select',{scope,payload:p},context));assert.equal(posts,1);
});
test('confirmed HTTP rejection plus missing original metadata receipt is classified as not committed',async()=>{
 const api=createIdeApiAdapter({request:async(_route,body)=>{throw Object.assign(Error('UNIT rejected'),{status:body?409:404});}});await assert.rejects(api('conversation.owner.select',{scope,payload:ownerChoicePacket()},context),{code:'API_REQUEST_REJECTED'});
});
