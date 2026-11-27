import {originalFileChoice} from '../src/original-file-input.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient } from '../src/api.ts';
import { AgentSessionsApi, AgentSessionOutcomeError, originalGuidanceText, originalSessionReady, originalMessagesNeedAttention, originalSessionCanResumeForSend, originalReadableParts, originalCompletedToolOnly, dispatchOriginalMessage as dispatchOriginalMessageSource, readOriginalOperations, restoreOriginalOperations } from '../src/agent-sessions.ts';
const originalIndexTestLocks={request:async(_name,_options,run)=>run()};
const dispatchOriginalMessage=(client,storage,session,text,agent,key,input={})=>dispatchOriginalMessageSource(client,storage,session,text,agent,key,input,originalIndexTestLocks);

import productBrand from '../../../packages/branding/product.json' with { type: 'json' };

const ref={id:'a'.repeat(32),revision:1,sha256:'b'.repeat(64)},provider={id:'model',revision:3,sha256:'c'.repeat(64)};
const current=(patch={})=>({project_id:'p',session_ref:ref,owner_status:'READY',generation:2,connected_owner:true,configuration_current:true,observation_is_current:true,native_status:'idle',workspace_kind:'APP_INTERNAL_READONLY',messages:[{info:{id:'native-user',role:'user'},parts:[{id:'text',type:'text',text:'原历史😀\r\n'}]}],todo:[],children:[],pending_questions:[],pending_permissions:[],last_event_seq:4,automatic_post_replay:false,...patch});
const receipt=(id,status='ACCEPTED')=>({id,status,request_sha256:'d'.repeat(64),automatic_replay:false,operation_ref:{id,request_sha256:'d'.repeat(64)},dispatch_status:status,session_ref:ref});
const configuration={capability_binding_sha256:'e'.repeat(64),configuration_refs:{skills:{},mcp:{}},permission_granted:false,loaded_in_original_session:false};
function transport(handler){const calls=[];const client=new AgentSessionsApi(new ApiClient(async(url,init)=>{if(url==='/api/bootstrap')return Response.json({csrf_token:'opaque'});const call={url,method:init.method,body:init.body?JSON.parse(init.body):undefined};calls.push(call);return handler(call,calls);}));return{client,calls};}
const storage=()=>{const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};};

test('normal disconnected saved history is readable without a stale-observation warning; real error and unfinished work remain visible',()=>{
 const saved=current({owner_status:'DISCONNECTED',connected_owner:false,observation_is_current:false,active_operation_id:null});
 assert.equal(originalMessagesNeedAttention(saved),false);assert.equal(originalSessionReady(saved),false);
 for(const patch of [{native_messages_error:{code:'ACTUAL_READ_FAILED'}},{observation_error:{code:'ACTUAL_OBSERVATION_FAILED'}},{connected_owner:true},{active_operation_id:'still-unknown'},{pending_questions:[{}]},{pending_permissions:[{}]}])assert.equal(originalMessagesNeedAttention({...saved,...patch}),true);
 assert.equal(originalMessagesNeedAttention(current()),false);
});

test('live presentation keeps authored JSON text and filenames but never emits lifecycle or raw tool payloads',()=>{
 const text='  {"step-start":"This is the user document, keep it"}\n原文 😀  ',message={info:{id:'m',role:'assistant'},parts:[
  {id:'s',type:'step-start',snapshot:'PRIVATE_SNAPSHOT'}, {id:'t',type:'text',text},
  {id:'tool',type:'tool',tool:'private_internal_tool',state:{status:'completed',input:{password:'PRIVATE_INPUT'},output:'PRIVATE_TOOL_OUTPUT'}},
  {id:'f',type:'file',filename:'说明 😀.pdf',url:'PRIVATE_INTERNAL_URL'}, {id:'end',type:'step-finish',cost:123},
  {id:'r',type:'reasoning',text:'PRIVATE_REASONING'},{type:'unknown-native-payload',data:'PRIVATE_DATA'}]};
 const shown=originalReadableParts(message);assert.equal(shown[0].text,text);
 assert.deepEqual(shown.map(p=>p.kind),['text','tool','file']);assert.equal(shown[1].status,'completed');assert.equal(shown[2].filename,'说明 😀.pdf');
 assert(!JSON.stringify(shown).includes('PRIVATE_'));assert(!JSON.stringify(shown).includes('private_internal_tool'));
 assert.deepEqual(originalReadableParts({...message,info:{id:'internal',role:'system'}}),[]);
 const failed=originalReadableParts({info:{id:'a',role:'assistant'},parts:[{type:'tool',state:{status:'error',error:'PRIVATE_FAILURE'}},{type:'tool',state:{status:'unrecognized'}}]});
 assert.deepEqual(failed.map(p=>p.status),['error','unknown']);assert(!JSON.stringify(failed).includes('PRIVATE_'));
});

test('only a known idle persisted disconnected owner may resume on an explicit new send',()=>{
 const stopped=current({owner_status:'EXPIRED',connected_owner:false,observation_is_current:false,active_operation_id:null});
 for(const owner_status of ['EXPIRED','DISCONNECTED'])assert.equal(originalSessionCanResumeForSend({...stopped,owner_status}),true);
 for(const patch of [{owner_status:'STARTING'},{owner_status:'BLOCKED'},{owner_status:'BLOCKED_PROCESS_STOP_UNCONFIRMED'},
  {owner_status:'OBSERVATION_UNAVAILABLE'},{owner_status:'READY'},{connected_owner:true},{configuration_current:false},
  {generation:0},{generation:NaN},{active_operation_id:undefined},{active_operation_id:'unknown-old-task'},
  {pending_questions:[{kind:'QUESTION'}]},{pending_permissions:[{kind:'PERMISSION'}]},
  {pending_questions:undefined},{pending_permissions:undefined},{automatic_post_replay:true},
  {session_ref:{...ref,sha256:'changed'}}])assert.equal(originalSessionCanResumeForSend({...stopped,...patch}),false,JSON.stringify(patch));
 assert.equal(originalSessionCanResumeForSend(undefined),false);
 assert.equal(originalSessionReady(stopped),false);
});

test('public conversation binding preserves original workspace workflow and explicit input for both message and busy guidance',async()=>{
 const conversationRef={id:'original:'+'a'.repeat(64),revision:3},attachment={kind:'SCREENSHOT',context_ref:ref,label:'My frame',regions:[],image_sha256:'f'.repeat(64)};
 const f=transport(call=>call.url.endsWith('/configuration')?Response.json(configuration):Response.json(call.method==='GET'?current():call.url.endsWith('/open')?current():receipt(call.body.operation_id)));
 await f.client.open({provider,projectId:'p',conversationRef,workflowRef:provider},'open-bound');
 const opened=f.calls.find(call=>call.url.endsWith('/open')).body;
 assert.deepEqual(opened.conversation_ref,conversationRef);assert.equal(opened.max_calls,24);assert.deepEqual(opened.workflow_ref,provider);
 for(const active of [false,true]){
  const g=transport(call=>Response.json(call.method==='GET'?current({provider_profile_ref:provider,active_operation_id:active?'old-task':null}):{operation_id:call.body.operation_id,conversation_ref:call.body.conversation_ref,original_source:call.body.original_source,automatic_post_replay:false,operation:receipt(call.body.operation_id)}));
  const input={conversationRef:{...conversationRef},inputAttachments:[structuredClone(attachment)]};
  const pending=g.client.send(current(),'Exact user {"JSON":true}','build','selected-input',input);
  input.conversationRef.revision=99;input.inputAttachments[0].label='Later edit';
  await pending;const post=g.calls.find(call=>call.method==='POST');
  assert.deepEqual(post.body.conversation_ref,conversationRef);assert.deepEqual(post.body.input_attachments,[attachment]);
  assert.equal(post.body.raw_text,'Exact user {"JSON":true}');assert.equal(post.url,'/api/conversations/'+encodeURIComponent(conversationRef.id)+'/messages');
  assert.deepEqual(post.body.original_source,{project_id:'p',session_ref:ref,generation:2});assert.deepEqual(post.body.provider_profile_ref,provider);
 }
});

test('durable original operation stores exact selected inputs before POST and read recovery never resends them',async()=>{
 const selected={conversationRef:{id:'original:'+'a'.repeat(64),revision:4},inputAttachments:[{kind:'SCREENSHOT',context_ref:ref,label:'Frozen screenshot',regions:[],image_sha256:'e'.repeat(64),user_note:'My note'}]};
 const local=storage(),session=current();let sends=0;
 const client={send:async(_session,text,_agent,key,input)=>{sends++;assert.equal(text,'User text');assert.deepEqual(input,selected);throw new Error('connection lost');},operation:async()=>{throw new Error('offline');}};
 await assert.rejects(()=>dispatchOriginalMessage(client,local,session,'User text','build','exact-input',selected),/connection lost/);
 assert.deepEqual(readOriginalOperations(local,session)[0].conversation_input,selected);
 const restored=await restoreOriginalOperations(client,local,session);assert.equal(restored[0].status,'INSPECTION_UNAVAILABLE');assert.deepEqual(restored[0].conversation_input,selected);assert.equal(sends,1);
 const changed=structuredClone(selected);changed.inputAttachments[0].image_sha256='f'.repeat(64);
 await assert.rejects(()=>dispatchOriginalMessage(client,local,session,'User text','build','exact-input',changed),/尚未发送/);
 assert.equal(sends,1);assert.deepEqual(readOriginalOperations(local,session)[0].conversation_input,selected);
 const values=new Map(),corrupt={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value.replace('Frozen screenshot','Dropped screenshot'))};
 await assert.rejects(()=>dispatchOriginalMessage(client,corrupt,session,'User text','build','readback-failed',selected),/尚未发送/);assert.equal(sends,1);
});

test('invalid selected input fails locally before preflight and keeps ordinary user JSON exact',async()=>{
 const f=transport(()=>{throw Error('No request expected');});
 for(const input of [{conversationRef:{id:'another',revision:1}},{inputAttachments:[{kind:'SCREENSHOT',context_ref:ref,label:'Missing pixels',regions:[]}]},{inputAttachments:[{kind:'CURRENT_PAGE',context_ref:ref,label:'No selected screenshot',regions:[{x:0,y:0,width:.5,height:.5}]}]}]){
  await assert.rejects(()=>f.client.send(current(),'User {"JSON":true}','plan','never-dispatched',input),/原稿|原草稿|附件|圈选/);
 }
 assert.equal(f.calls.length,0);
});

test('original owner opens an internal readonly project without a folder grant; process and tool scope only come from explicit selections',async()=>{
  const f=transport(call=>call.url.endsWith('/configuration')?Response.json(configuration):Response.json(current({project_id:'actual-internal'})));
  const opened=await f.client.open({provider},'open-internal');
  assert.equal(opened.project_id,'actual-internal');const body=f.calls.find(item=>item.method==='POST').body;
  assert.equal(body.title,`${productBrand.short_name} 原会话`);
  assert.equal(body.project_id,null);assert.equal(body.workspace_ref,null);assert.deepEqual(body.allowed_effects,[]);assert.deepEqual(body.command_allowlist,[]);assert.deepEqual(body.mcp_tool_allowlist,[]);assert.equal(Object.hasOwn(body,'file_source_ref'),false);assert.equal(Object.hasOwn(body,'context_ref'),false);
  const workspace={id:'w',project_id:'p',revision:2,binding_sha256:'f'.repeat(64),root:'/chosen',allowed_write_paths:['file.md']};
  const g=transport(call=>call.url.endsWith('/configuration')?Response.json(configuration):Response.json(current()));
  await g.client.open({provider,projectId:'p',workspace,title:'保留用户自己的标题',processStart:true,remoteMCPConnect:true,commandAllowlist:['printf exact'],mcpToolAllowlist:['fixture_echo']},'open-explicit');
  const selected=g.calls.find(item=>item.method==='POST').body;assert.equal(selected.title,'保留用户自己的标题');assert.deepEqual(selected.workspace_ref,{id:'w',revision:2,binding_sha256:workspace.binding_sha256});assert.deepEqual(selected.allowed_effects,['PROCESS_START','REMOTE_MCP_CONNECT','BACKGROUND_COMMAND']);assert.deepEqual(selected.command_allowlist,['printf exact']);assert.deepEqual(selected.mcp_tool_allowlist,['fixture_echo']);assert.equal(Object.hasOwn(selected,'allowed_write_paths'),false);
  await assert.rejects(()=>g.client.open({provider,projectId:'another',workspace},'wrong-scope'),/另一项目/);assert.equal(g.calls.filter(item=>item.method==='POST').length,1);
});

test('coding permission is explicit and scoped to the registered workspace grant without copying or expanding paths',async()=>{
  const f=transport(call=>call.url.endsWith('/configuration')?Response.json(configuration):Response.json(current()));
  const workspace={id:'w',project_id:'p',revision:2,binding_sha256:'f'.repeat(64),root:'/chosen',allowed_write_paths:['src/exact.py','delivery/']};
  await f.client.open({provider,projectId:'p',workspace,workspaceWrite:true},'coding-open');
  const post=f.calls.find(item=>item.method==='POST');assert.deepEqual(post.body.allowed_effects,['WORKSPACE_WRITE']);
  assert.deepEqual(post.body.workspace_ref,{id:'w',revision:2,binding_sha256:'f'.repeat(64)});assert.equal(Object.hasOwn(post.body,'allowed_write_paths'),false);
  const count=f.calls.length;
  for(const missing of [undefined,{...workspace,allowed_write_paths:[]}])await assert.rejects(()=>f.client.open({provider,projectId:'p',workspace:missing,workspaceWrite:true},'no-grant'),/写入授权/);
  assert.equal(f.calls.length,count);
});

test('send preflights actual owner generation, observation and configuration; original text remains exact and native/file context is never borrowed',async()=>{
  for(const patch of [{generation:3},{configuration_current:false},{owner_status:'OBSERVATION_UNAVAILABLE',observation_is_current:false},{observation_is_current:false}]){
    const f=transport(()=>Response.json(current(patch)));await assert.rejects(()=>f.client.send(current(),'保留原草稿','plan','blocked'),/不可发送/);assert.equal(f.calls.filter(item=>item.method==='POST').length,0);
  }
  assert.equal(originalSessionReady(current({observation_is_current:false})),false);
  const f=transport(call=>Response.json(call.method==='GET'?current():receipt(call.body.operation_id)));
  const text='  原问题😀\r\n尾空格 ';await f.client.send(current(),text,'build','native-text');const body=f.calls.at(-1).body;
  assert.equal(body.raw_text,text);assert.equal(body.agent,'build');assert.equal(body.generation,2);assert.deepEqual(body.session_ref,ref);assert.equal(body.timeout_seconds,300);assert.equal(Object.hasOwn(body,'context_ref'),false);assert.equal(Object.hasOwn(body,'file_source_ref'),false);
  await assert.rejects(()=>f.client.send(current(),'😀'.repeat(16001),'plan','oversized'),/16000/);assert.equal(f.calls.filter(item=>item.method==='POST').length,1);
});

test('a busy owner accepts exact new guidance without abort, another session, or a model/agent override',async()=>{
  const active=current({active_operation_id:'original-long-task',native_status:'busy'});
  const f=transport(call=>Response.json(call.method==='GET'?active:{...receipt(call.body.operation_id),kind:'GUIDANCE',role:'STEERING'}));
  const input='  先保留我手改的结论😀\r\n再补上新的依据。 ';
  await f.client.send(current(),input,'plan','inflight-update');
  assert.equal(f.calls.length,2);
  assert.match(f.calls[1].url,/\/guidance$/);
  assert.deepEqual(f.calls[1].body,{operation_id:'inflight-update',session_ref:ref,generation:2,
    raw_text:input,intent:'AUTO',expected_active_operation_id:'original-long-task'});
  assert.ok(f.calls.every(call=>!call.url.includes('/abort')&&!call.url.includes('/open')));
});

test('a busy progress question returns a readable local status while uncertain guidance inspects only the same operation',async()=>{
  const active=current({active_operation_id:'keep-running',native_status:'busy'});
  const f=transport(call=>Response.json(call.method==='GET'?active:{...receipt(call.body.operation_id,'RETURNED'),kind:'GUIDANCE',role:'LOCAL_STATUS',model_calls:0,native_post_count:0,
    local_status:{active_operation_id:'keep-running',operation_status:'ACCEPTED',pending_questions:1,pending_permissions:0}}));
  const status=await f.client.send(active,'现在进度如何？','build','progress-only');
  assert.match(originalGuidanceText(status),/没有请求模型，也没有停止原任务/);
  assert.match(originalGuidanceText(status),/待回答 1 项/);
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
  const unknown=transport(call=>{
    if(call.url.endsWith('/operations/lost-guidance'))return Response.json({...receipt('lost-guidance','UNKNOWN_OUTCOME'),kind:'GUIDANCE'});
    if(call.method==='GET')return Response.json(active);
    throw new Error('connection ended after the original owner may have received guidance');
  });
  await assert.rejects(()=>unknown.client.send(active,'继续这个任务，保留人工改动','build','lost-guidance'),error=>error instanceof AgentSessionOutcomeError&&error.receipt?.status==='UNKNOWN_OUTCOME');
  assert.equal(unknown.calls.filter(call=>call.method==='POST').length,1);
  assert.match(unknown.calls.at(-1).url,/\/operations\/lost-guidance$/);
  assert.match(originalGuidanceText({...receipt('lost-guidance','UNKNOWN_OUTCOME'),kind:'GUIDANCE'}),/只核查原操作/);
  assert.match(originalGuidanceText({...receipt('cohort','COHORT_RETURNED'),kind:'GUIDANCE'}),/尚未逐条核验/);
});

test('question and permission replies retain exact native request identity and never widen the server choices',async()=>{
  const f=transport(call=>Response.json(receipt(call.body.operation_id)));
  const pending={kind:'PERMISSION',request_ref:{id:'per_exact',generation:2,sha256:'f'.repeat(64)},native_request:{permission:'bash',patterns:['printf exact']},allowed_choices:['once','reject'],reply_is_execution_authorization:true};
  await assert.rejects(()=>f.client.pendingReply(current(),pending,'always',undefined,'deny-widen'),/可选范围/);await assert.rejects(()=>f.client.pendingReply(current(),{...pending,request_ref:{...pending.request_ref,generation:1}},'once',undefined,'old-generation'),/代次/);assert.equal(f.calls.length,0);
  await f.client.pendingReply(current(),pending,'once',undefined,'permission-once');assert.match(f.calls[0].url,/permissions\/per_exact\/reply$/);assert.deepEqual(f.calls[0].body.request_ref,pending.request_ref);assert.equal(f.calls[0].body.reply,'once');assert.equal(Object.hasOwn(f.calls[0].body,'answers'),false);
  const question={...pending,kind:'QUESTION',request_ref:{...pending.request_ref,id:'que_actual'},allowed_choices:['reply','reject']},answers=[['保留 CRLF\r\n😀']];
  await f.client.pendingReply(current(),question,'reply',answers,'answer-exact');assert.deepEqual(f.calls[1].body.answers,answers);assert.equal(Object.hasOwn(f.calls[1].body,'reply'),false);
  await f.client.pendingReply(current(),question,'reject',undefined,'reject-question');assert.match(f.calls[2].url,/questions\/que_actual\/reject$/);assert.equal(Object.hasOwn(f.calls[2].body,'answers'),false);
});

test('a failed/unauthorized original POST is sent once, then only its original operation is inspected',async()=>{
  for(const failure of ['connection','unauthorized']){
    const f=transport(call=>{if(call.url.endsWith('/operations/unknown'))return Response.json(receipt('unknown','UNKNOWN_OUTCOME'));if(call.method==='GET')return Response.json(current());if(failure==='connection')throw new Error('connection ended after accept');return Response.json({error:{code:'TOKEN_EXPIRED',message:'expired'}},{status:401});});
    await assert.rejects(()=>f.client.send(current(),'草稿保留','plan','unknown'),error=>error instanceof AgentSessionOutcomeError&&error.receipt?.status==='UNKNOWN_OUTCOME');assert.equal(f.calls.filter(item=>item.method==='POST').length,1);assert.equal(f.calls.at(-1).url.endsWith('/operations/unknown'),true);
  }
  const wrong=transport(call=>Response.json(call.method==='GET'&&!call.url.includes('/operations/')?current():receipt('different')));await assert.rejects(()=>wrong.client.send(current(),'原请求','plan','actual'),/结果未知/);assert.equal(wrong.calls.filter(item=>item.method==='POST').length,1);
});

test('a changed workspace approval has concise translated copy and retains the exact failed operation without retry',async()=>{
  const f=transport(call=>call.method==='POST'?Response.json({error:{code:'CONFLICT',message:'WORKSPACE_SOURCE_CHANGED: saved files or review evidence changed; reject this pending request and review a new edit'}},{status:409}):Response.json(receipt('changed-approval','FAILED')));
  const pending={kind:'PERMISSION',request_ref:{id:'per_changed',generation:2,sha256:'f'.repeat(64)},native_request:{permission:'edit',patterns:['src/a.py']},allowed_choices:['once','reject'],reply_is_execution_authorization:true};
  await assert.rejects(()=>f.client.pendingReply(current(),pending,'once',undefined,'changed-approval'),error=>error instanceof AgentSessionOutcomeError&&error.operationId==='changed-approval'&&error.receipt?.status==='FAILED'&&error.ui_copy.message_key==='文件或修订依据已变更，请拒绝这次修改并重新检查。');
  assert.equal(f.calls.filter(item=>item.method==='POST').length,1);assert.equal(f.calls.at(-1).url.endsWith('/operations/changed-approval'),true);
});

test('dispatch writes a readable local index before sending; restart preserves unavailable and missing operations without replay',async()=>{
  const local=storage(),input=' 保留完整问题😀\r\n ',session=current();let sends=0;
  const f=transport(call=>{if(call.url.includes('/operations/'))return call.url.endsWith('/absent')?Response.json({error:{code:'NOT_FOUND',message:'none'}},{status:404}):Promise.reject(new Error('owner unavailable'));if(call.method==='GET')return Response.json(session);sends++;assert.equal(readOriginalOperations(local,session).at(-1).input,input);throw new Error('after possible accept');});
  await assert.rejects(()=>dispatchOriginalMessage(f.client,local,session,input,'plan','uncertain'),/不自动重发/);
  const index=readOriginalOperations(local,session);local.setItem(`eeas-original-operation:${ref.id}`,JSON.stringify([...index,{...index[0],operation_id:'absent'}]));
  const restored=await restoreOriginalOperations(f.client,local,session);assert.deepEqual(restored.map(item=>item.status),['INSPECTION_UNAVAILABLE','NO_RECORD']);assert.ok(restored.every(item=>item.input===input&&!item.receipt&&!item.automatic_retry));assert.match(restored[1].message,/不证明未执行/);assert.equal(sends,1);
  const broken={getItem:()=>null,setItem:()=>{throw new Error('full');}};await assert.rejects(()=>dispatchOriginalMessage(f.client,broken,session,input,'plan','blocked'),/尚未发送/);assert.equal(sends,1);assert.deepEqual(readOriginalOperations(local,current({project_id:'other'})),[]);
});

test('disconnect only revokes the exact owned generation even when configuration or observation no longer admits submit',async()=>{
  const session=current({configuration_current:false,owner_status:'OBSERVATION_UNAVAILABLE',observation_is_current:false});
  const f=transport(call=>Response.json(current({owner_status:'DISCONNECTED',connected_owner:false,observation_is_current:false,last_control_operation:receipt(call.body.operation_id,'RETURNED')})));
  const result=await f.client.disconnect(session,'revoke-own');assert.equal(result.session.connected_owner,false);assert.equal(result.operation.id,'revoke-own');assert.match(f.calls[0].url,/disconnect$/);assert.deepEqual(f.calls[0].body,{operation_id:'revoke-own',session_ref:ref,generation:2});assert.equal(f.calls.length,1);
});

test('original send freezes the visible file choice before connection reads and carries it into the canonical input',async()=>{
 const conversationRef={id:'original:'+'a'.repeat(64),revision:3};
 const fileInput={kind:'LINKED_FILE',link_ref:{project_id:'p',id:'d'.repeat(64),revision:2,sha256:'e'.repeat(64)}};
 const expected=structuredClone(fileInput);
 const g=transport(call=>Response.json(call.method==='POST'?{operation_id:'file-choice',conversation_ref:conversationRef,original_source:{project_id:'p',session_ref:ref},automatic_post_replay:false,operation:receipt('file-choice')}:current({provider_profile_ref:provider,active_operation_id:null})));
 const sending=g.client.send(current(),'Read my file','plan','file-choice',{conversationRef,fileInput});
 fileInput.link_ref.revision=9;
 await sending;
 const sent=g.calls.find(c=>c.method==='POST');assert.deepEqual(sent.body.file_input,expected);
 assert.equal(sent.body.raw_text,'Read my file');assert.equal(sent.body.agent,'plan');assert.deepEqual(sent.body.input_attachments,[]);
});

test('file choice is durable operation identity and recovery cannot swap files or replay a missing reply',async()=>{
 const local=storage(),session=current(),first={fileInput:{kind:'LINKED_FILE',link_ref:{project_id:'p',id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)}}};let sends=0,reads=0;
 const client={send:async()=>{sends++;throw Error('outcome unknown');},operation:async()=>{reads++;return receipt('file-op','RETURNED');}};
 await assert.rejects(()=>dispatchOriginalMessage(client,local,session,'Comment','build','file-op',first),/outcome unknown/);
 await assert.rejects(()=>dispatchOriginalMessage(client,local,session,'Comment','build','file-op',{fileInput:{...first.fileInput,link_ref:{...first.fileInput.link_ref,id:'f'.repeat(64)}}}),/原会话请求索引未保存/);
 assert.equal(sends,1);
 const restored=await restoreOriginalOperations(client,local,session);assert.equal(reads,1);assert.equal(sends,1);assert.deepEqual(restored[0].conversation_input,first);
});

test('invalid or mixed file sources fail before persisting or sending an original request',async()=>{
 for(const fileInput of [null,{kind:'UNKNOWN'},{kind:'LINKED_FILE',link_ref:{id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)}},{kind:'LINKED_FILE',link_ref:{project_id:'p',id:'bad',revision:1,sha256:'e'.repeat(64)}},{kind:'WORKSPACE_FILE',source_ref:{kind:'local_file'}},{kind:'LINKED_FILE',link_ref:{project_id:'p',id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)},source_ref:{}}]){
  let sends=0;const local=storage();await assert.rejects(()=>dispatchOriginalMessage({send:async()=>{sends++;}},local,current(),'Text','build','bad-file',{fileInput}));assert.equal(sends,0);assert.equal(readOriginalOperations(local,current()).length,0);
 }
});


test('only fully completed tool-only transport turns are absent from ordinary chat; active tools and failures remain visible',()=>{
 const finished={info:{id:'tool-turn',role:'assistant',time:{completed:1234}},parts:[{type:'step-start'},{type:'tool',state:{status:'completed'}},{type:'step-finish'}]};
 assert.equal(originalCompletedToolOnly(finished),true);
 for(const state of ['running','pending','error','unknown'])assert.equal(originalCompletedToolOnly({...finished,parts:[{type:'tool',state:{status:state}}]}),false);
 assert.equal(originalCompletedToolOnly({...finished,info:{...finished.info,time:{}}}),false);
 assert.equal(originalCompletedToolOnly({...finished,info:{...finished.info,error:{code:'kept'}}}),false);
 assert.equal(originalCompletedToolOnly({...finished,info:{...finished.info,role:'user'}}),false);
 assert.equal(originalCompletedToolOnly({...finished,parts:[...finished.parts,{type:'text',text:'The actual reply stays.'}]}),false);
 assert.equal(originalCompletedToolOnly({...finished,parts:[...finished.parts,{type:'file',filename:'result.txt'}]}),false);
});


test('a new unassigned or empty chat sends without inventing a file, and never inherits another project selection',()=>{
 const selected={kind:'local_file',project_id:'p',workspace_id:'a'.repeat(64),binding_revision:1,binding_sha256:'b'.repeat(64),path:'known.txt',revision:1,sha256:'c'.repeat(64)};
 assert.equal(originalFileChoice(undefined,undefined,undefined),undefined);
 assert.equal(originalFileChoice('p',undefined,undefined),undefined);
 assert.equal(originalFileChoice(undefined,undefined,selected),undefined);
 assert.equal(originalFileChoice('other',undefined,selected),undefined);
 assert.deepEqual(originalFileChoice('p',undefined,selected),{kind:'WORKSPACE_FILE',source_ref:selected});
 const linked={project_id:'p',id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)};
 assert.deepEqual(originalFileChoice('p',linked,selected),{kind:'LINKED_FILE',link_ref:linked});
});
