'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const ref={id:'session',revision:1,sha256:'a'.repeat(64)},scope={project_id:'project',session_ref:ref,generation:1};
const route='/api/projects/project/agent-sessions/session';
function canon(v){if(Array.isArray(v))return '['+v.map(canon).join(',')+']';if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canon(v[k])).join(',')+'}';return JSON.stringify(v);}
const sha=v=>crypto.createHash('sha256').update(canon(v)).digest('hex');
const session=changes=>({project_id:'project',session_ref:ref,generation:1,owner_status:'READY',native_status:'idle',connected_owner:true,configuration_current:true,observation_is_current:true,active_operation_id:null,messages:[],todo:[],children:[],guidance:[],pending_questions:[],pending_permissions:[],last_event_seq:2,automatic_post_replay:false,...changes});
const nativeScope={project_id:'project',conversation_id:'desktop-topic'},nativeProfile={...ref,id:'chosen-profile'},nativeRef={...ref,id:'native-parent'};
test('exact root-delegated foreground guidance uses the same child route and refuses stale or sibling targets',async()=>{
 const child={id:'ses_child',parentID:'ses_root',directory:'/original'},childRef={id:child.id,root_native_session_id:'ses_root',sha256:sha(child)},calls=[];
 const raw=session({original_session_id:'ses_root',active_operation_id:'root_active',children:[child],child_guidance_targets:[{parent_operation_id:'root_active',generation:1,child_ref:childRef,status:'RUNNING_FOREGROUND',agent:'explore'}]});
 const dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return body?{...receipt(body,'CHILD_GUIDANCE'),generation:1,native_dispatch_acknowledged:true,native_post_attempted:true,child_ref:childRef,original_session_id:child.id,root_original_session_id:'ses_root',original_active_operation_id:'root_active'}:raw;}});
 const payload={operation_id:'guide_running_child',child_ref:childRef,raw_text:'Correct the task',expected_active_operation_id:'root_active'};
 const result=await dispatch('session.child.message',{scope,payload});assert.equal(result.kind,'CHILD_GUIDANCE');assert.equal(calls.filter(row=>row.body).length,1);
 assert.equal(calls.find(row=>row.body).url,route+'/child-messages');
 raw.child_guidance_targets[0].parent_operation_id='old_root';await assert.rejects(dispatch('session.child.message',{scope,payload:{...payload,operation_id:'late'}}),{code:'CHILD_CHANGED'});
 raw.child_guidance_targets[0].parent_operation_id='root_active';raw.child_guidance_targets[0].child_ref={...childRef,id:'ses_sibling'};
 await assert.rejects(dispatch('session.child.message',{scope,payload:{...payload,operation_id:'wrong'}}),{code:'CHILD_CHANGED'});assert.equal(calls.filter(row=>row.body).length,1);
});
test('child dispatch API conflict after a native POST inspects its exact receipt instead of treating it as a safe retry',async()=>{
 const child={id:'ses_child',parentID:'ses_root',directory:'/original'},childRef={id:child.id,root_native_session_id:'ses_root',sha256:sha(child)},calls=[];
 let original;const dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});
  if(body){original={...receipt(body,'CHILD_MESSAGE'),status:'UNKNOWN_OUTCOME',generation:1,native_post_attempted:true,native_dispatch_acknowledged:false,child_ref:childRef,original_session_id:child.id};throw Object.assign(Error('Native dispatch acknowledgement lost'),{status:409});}
  if(url.includes('/operations/'))return original;
  return session({original_session_id:'ses_root',children:[child]});}});
 const result=await dispatch('session.child.message',{scope,payload:{operation_id:'child_unknown',child_ref:childRef,raw_text:'Continue exact child'}});
 assert.equal(result.status,'UNKNOWN_OUTCOME');assert.equal(result.native_post_attempted,true);
 assert.equal(calls.filter(row=>row.body).length,1);assert.ok(calls.at(-1).url.endsWith('/operations/child_unknown'));
 assert.equal(calls.find(row=>row.body).body.expected_active_operation_id,null);
});
test('explicit child view rereads the same original lineage and sends GET only without moving session',async()=>{
 const child={id:'ses_child',parentID:'ses_root',directory:'/original',title:'Review'},raw=session({original_session_id:'ses_root',children:[child]}),calls=[];
 const childRef={id:child.id,root_native_session_id:'ses_root',sha256:sha({id:child.id,parentID:child.parentID,directory:child.directory})};
 const value={kind:'ORIGINAL_CHILD_VIEW',scope,child_ref:childRef,title:'Review',messages:[{info:{id:'msg_child',sessionID:child.id,role:'assistant'},parts:[{id:'prt_child',type:'text',sessionID:child.id,messageID:'msg_child',text:'ORIGINAL_CHILD_RESULT'}]}],coverage:{history_complete:false,partial:true},read_only:true,model_calls:0,native_posts:0,new_owner_started:false,transcript_copied:false,observation_is_current:false};
 const dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});assert.equal(body,undefined);return url.includes('/children/')?value:raw;}});
 const view=await dispatch('session.get',{scope,payload:{}});assert.deepEqual(view.children[0].child_ref,childRef);
 const result=await dispatch('session.child',{scope,payload:{child_ref:childRef}});
 assert.equal(result.messages[0].parts[0].text,'ORIGINAL_CHILD_RESULT');assert.deepEqual(result.scope,scope);assert.equal(result.new_owner_started,false);
 assert.equal(calls.length,3);assert.ok(calls[2].url.includes('child_sha256='+childRef.sha256));
 raw.children[0]={...child,parentID:'ses_other'};
 await assert.rejects(dispatch('session.child',{scope,payload:{child_ref:childRef}}),{code:'CHILD_CHANGED'});
});
const nativeRoute='/api/desktop-host/native-conversations/desktop-topic';
const nativeCoverage=(returned,changes={})=>({candidate_limit:256,candidate_window_truncated:false,returned,has_more:false,complete:true,
  scope_filter:'EXPLICIT_PROJECT_ONLY',unbound_messages_included:false,order_basis:'RECORD_MODIFIED_AT',...changes});
const nativeMessage=(operation='parent',changes={})=>({kind:'NATIVE_MESSAGE',...nativeScope,operation_id:operation,status:'PROCESSING',
  request_sha256:'b'.repeat(64),record_origin:'CURRENT_HOST',input:{raw_text:'原始消息',origin:'ORIGINAL_NATIVE_REQUEST'},
  provider_profile_ref:nativeProfile,native_message_ref:nativeRef,file_source_ref:null,file_link_ref:null,
  control_capabilities:{can_guide:true,can_status:true,can_cancel:true},automatic_retry:false,...changes});
const nativeConversation=(messages=[nativeMessage()],changes={})=>({kind:'NATIVE_CONVERSATION',...nativeScope,messages,
  latest_provider_profile_ref:nativeProfile,coverage:nativeCoverage(messages.length),...changes});
function receipt(body,kind='MESSAGE'){return{id:body.operation_id,kind,status:'ACCEPTED',session_ref:ref,request_sha256:sha(body),request:body,automatic_replay:false};}
test('typed methods reject arbitrary routes, raw credentials and incomplete scope before any request',async()=>{
  let calls=0;const dispatch=createIdeApiAdapter({request:async()=>{calls++;}});
  for(const [method,params]of [['shell',{payload:{command:'x'}}],['session.get',{scope:{project_id:'project'}}],['session.message',{scope,payload:{operation_id:'x',raw_text:'hello',api_key:'private'}}],['projects.list',{scope:{project_id:'../bad'}}]])await assert.rejects(dispatch(method,params),e=>e.public===true);
  assert.equal(calls,0);
});
test('projects and status whitelist safe fields; credentials, origin and source internals stay in Main',async()=>{
  const dispatch=createIdeApiAdapter({request:async url=>url==='/api/projects'?{projects:[{id:'project',name:'真正项目',description:'scope',api_key:'LEAK',provider_url:'LEAK'}]}:url.endsWith('configuration')?{capability_binding_sha256:'c'.repeat(64),secret:'LEAK'}:{providers:[{profile_ref:{...ref,id:'model'},provider_id:'custom',model_id:'model',credential_present:true,provider_url:'LEAK',api_key:'LEAK',transport:{headers:[{value:'LEAK'}]}}]}});
  const projects=await dispatch('projects.list');assert.equal(projects.projects[0].name,'真正项目');
  const status=await dispatch('status');assert.equal(status.connected,true);assert.equal(status.providers[0].profile_ref.id,'model');assert.doesNotMatch(JSON.stringify({projects,status}),/LEAK|provider_url|api_key/);
});
test('ordinary send uses exact same owner once, with API defaults bound into receipt hash',async()=>{
  const calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return body?receipt(body):session();}});
  const result=await dispatch('session.message',{scope,payload:{operation_id:'message-1',raw_text:'保留原正文'}});
  assert.equal(result.kind,'MESSAGE');assert.deepEqual(calls.map(c=>c.url),[route,route+'/messages']);assert.deepEqual(calls[1].body,{operation_id:'message-1',session_ref:ref,generation:1,raw_text:'保留原正文',agent:'build',timeout_seconds:60});
});
test('busy same conversation accepts AUTO guidance without an earlier failed message POST',async()=>{
  const calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return body?receipt(body,'GUIDANCE'):session({active_operation_id:'first'});}});
  const result=await dispatch('session.message',{scope,payload:{operation_id:'correction',raw_text:'不要改表格'}});
  assert.equal(result.kind,'GUIDANCE');assert.deepEqual(calls.map(c=>c.url),[route,route+'/guidance']);assert.equal(calls[1].body.expected_active_operation_id,'first');assert.equal(calls[1].body.intent,'AUTO');
});
test('late old generation prevents new dispatch; operation recovery can read its exact historical generation',async()=>{
  let posts=0;const body={operation_id:'old',session_ref:ref,generation:1,raw_text:'old',agent:'build',timeout_seconds:60};
  const dispatch=createIdeApiAdapter({request:async(url,b)=>{if(b)posts++;return url.includes('/operations/')?receipt(body):session({generation:2});}});
  await assert.rejects(dispatch('session.message',{scope,payload:{operation_id:'new',raw_text:'new'}}),e=>e.code==='STALE_SESSION_SCOPE');assert.equal(posts,0);
  assert.equal((await dispatch('session.operation',{scope,payload:{operation_id:'old'}})).id,'old');
  await assert.rejects(dispatch('session.operation',{scope:{...scope,generation:2},payload:{operation_id:'old'}}),e=>e.code==='STALE_SESSION_SCOPE');
});
test('lost POST response recovers only original GET; a mismatching input receipt never becomes success',async()=>{
  for(const mismatch of [false,true]){
    let saved,posts=0,gets=0;const dispatch=createIdeApiAdapter({request:async(url,body)=>{if(body){posts++;saved=receipt({...body,...mismatch?{raw_text:'unrelated'}:{}});throw Error('PRIVATE transport credential');}if(url.includes('/operations/')){gets++;return saved;}return session();}});
    const result=dispatch('session.message',{scope,payload:{operation_id:'lost',raw_text:'真实输入'}});
    if(mismatch)await assert.rejects(result,e=>e.code==='OPERATION_INPUT_MISMATCH');else assert.equal((await result).id,'lost');assert.equal(posts,1);assert.equal(gets,1);
  }
});
test('unknown without receipt and API rejection never repeat a write or expose raw errors',async()=>{
  for(const rejected of [false,true]){
    let posts=0,gets=0;const dispatch=createIdeApiAdapter({request:async(url,body)=>{if(body){posts++;const error=Error('PRIVATE');if(rejected)error.status=409;throw error;}if(url.includes('/operations/')){gets++;throw Error('PRIVATE');}return session();}});
    await assert.rejects(dispatch('session.message',{scope,payload:{operation_id:'lost',raw_text:'真实输入'}}),e=>e.public&&!e.message.includes('PRIVATE')&&e.code===(rejected?'API_REQUEST_REJECTED':'OPERATION_OUTCOME_UNKNOWN'));
    assert.equal(posts,1);assert.equal(gets,rejected?0:1);
  }
});
test('bounded history keeps latest reply, declares omissions and excludes raw tool/image/secret payloads',async()=>{
  const messages=Array.from({length:100},(_,n)=>({info:{id:'msg_'+n,role:'assistant',sessionID:'native'},parts:[{type:'text',text:'旧'.repeat(20000)},{type:'tool',tool:'read',callID:'call',state:{status:'completed',input:{secret:'LEAK'},output:'LEAK'}},{type:'file',url:'data:image/png;base64,LEAK',mime:'image/png'}]}));messages[99].parts[0].text='最新答复';
  const dispatch=createIdeApiAdapter({request:async()=>session({messages,private_server_url:'LEAK',process_identity:{pid:123}})});
  const value=await dispatch('session.get',{scope});assert.equal(value.messages.at(-1).parts[0].text,'最新答复');assert.equal(value.messages[0].info.id,'msg_20');assert.equal(value.ide_coverage.messages.truncated,true);assert.equal(value.ide_coverage.messages.text_truncated,true);assert.doesNotMatch(JSON.stringify(value),/LEAK|private_server_url|process_identity|data:image/);assert.deepEqual(value.artifacts,[]);assert.equal(value.artifact_channel,'REGISTERED_ARTIFACTS_AND_WORKSPACE_FILES');
});
test('session events are saved metadata history with explicit older-generation coverage, never model/tool payload',async()=>{
  const dispatch=createIdeApiAdapter({request:async url=>url.includes('/events?')?{events:[{seq:2,type:'ORIGINAL_MESSAGE_SETTLED',payload:{operation_id:'x',status:'RETURNED',api_key:'LEAK',text:'LEAK'}}],last_event_seq:2,native_replay:false}:session()});
  const value=await dispatch('session.events',{scope,payload:{after:1,limit:20}});assert.equal(value.events[0].payload.operation_id,'x');assert.equal(value.coverage.generation_filter,'NOT_AVAILABLE_SESSION_HISTORY_MAY_INCLUDE_OLDER_GENERATIONS');assert.doesNotMatch(JSON.stringify(value),/LEAK/);
});
test('cancel requires exact active target and reports original acknowledgement, not semantic completion',async()=>{
  const calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return{...receipt(body,'INTERRUPT'),original_abort_acknowledged:true,original_message_stop_confirmed:false,external_actions_rolled_back:false};}});
  await assert.rejects(dispatch('session.cancel',{scope,payload:{operation_id:'stop'}}));assert.equal(calls.length,0);
  const value=await dispatch('session.cancel',{scope,payload:{operation_id:'stop',expected_active_operation_id:'first'}});assert.equal(calls[0].url,route+'/interrupt');assert.equal(calls[0].body.expected_active_operation_id,'first');assert.equal(value.original_message_stop_confirmed,false);
});
test('artifact opening uses exact report manifest callback, never arbitrary paths or resolver output paths',async()=>{
  const opened=[],dispatch=createIdeApiAdapter({request:async()=>{},openArtifact:async body=>{opened.push(body);return{path:'PRIVATE'};}}),artifact_id='b'.repeat(64);
  for(const payload of [{kind:'report',artifact_id,sha256:'c'.repeat(64),download_url:'file:///private'},{kind:'report',artifact_id,sha256:'c'.repeat(64),download_url:'/api/projects/other/report-artifacts/'+artifact_id+'/download'},{kind:'report',artifact_id,sha256:'c'.repeat(64),path:'D:/private'}])await assert.rejects(dispatch('artifact.open',{scope:{project_id:'project'},payload}));
  assert.equal(opened.length,0);const value=await dispatch('artifact.open',{scope:{project_id:'project'},payload:{kind:'report',artifact_id,sha256:'c'.repeat(64),download_url:'/api/projects/project/report-artifacts/'+artifact_id+'/download'}});assert.equal(opened.length,1);assert.doesNotMatch(JSON.stringify(value),/PRIVATE/);assert.equal(value.native_software_open_confirmed,false);
});
test('revision artifacts use their exact scoped route with no arbitrary or mismatching destinations',async()=>{
  const opened=[],dispatch=createIdeApiAdapter({request:async()=>{},openArtifact:async body=>{opened.push(body);return{native_path:'PRIVATE'};}}),artifact_id='b'.repeat(64);
  const payload={kind:'report',artifact_id,sha256:'c'.repeat(64),download_url:'/api/projects/project/docx-revisions/artifacts/'+artifact_id+'/download'};
  const result=await dispatch('artifact.open',{scope:{project_id:'project'},payload});
  assert.equal(opened.length,1);assert.equal(opened[0].download_url,payload.download_url);assert.doesNotMatch(JSON.stringify(result),/PRIVATE|native_path/);
  for(const download_url of [payload.download_url.replace('/project/','/other/'),payload.download_url.replace(artifact_id,'d'.repeat(64)),payload.download_url+'?path=other',payload.download_url+'/../download','https://other'+payload.download_url]){
    await assert.rejects(dispatch('artifact.open',{scope:{project_id:'project'},payload:{...payload,download_url}}),e=>e.code==='INVALID_PARAMS');
  }
  assert.equal(opened.length,1);
});
test('signals reach Main and cancelled work does not start; create unknown never opens a replacement',async()=>{
  const abort=new AbortController();abort.abort();let calls=0;
  const dispatch=createIdeApiAdapter({request:async()=>{calls++;throw Error('PRIVATE');}});
  await assert.rejects(dispatch('projects.list',{}, {signal:abort.signal}),e=>e.code==='REQUEST_CANCELLED');assert.equal(calls,0);
  await assert.rejects(dispatch('session.create',{scope:{project_id:'project'},payload:{operation_id:'open',provider_profile_ref:ref,capability_binding_sha256:'c'.repeat(64)}}),e=>e.code==='CREATION_OUTCOME_UNKNOWN');assert.equal(calls,1);
});
test('large collections stay within transport bytes with explicit coverage; oversized single detail fails honestly',async()=>{
  const projects=Array.from({length:256},(_,n)=>({id:'project_'+n,name:'中'.repeat(300),description:'文'.repeat(1000)}));
  const list=createIdeApiAdapter({request:async()=>({projects})}),value=await list('projects.list');
  assert(Buffer.byteLength(JSON.stringify(value))<=512*1024);assert.equal(value.coverage.available,256);assert.equal(value.coverage.returned,value.projects.length);assert.equal(value.coverage.truncated,true);
  const pending={request_ref:{id:'que_big',sha256:'a'.repeat(64),generation:1},kind:'QUESTION',allowed_choices:['reply'],native_request:{questions:Array.from({length:16},()=>({question:'字'.repeat(2000),options:Array.from({length:32},()=>({label:'字'.repeat(300),description:'字'.repeat(1000)}))}))}};
  const detail=createIdeApiAdapter({request:async()=>session({pending_questions:[pending]})});
  await assert.rejects(detail('session.get',{scope}),e=>e.code==='RESPONSE_TOO_LARGE');assert.equal(pending.native_request.questions.length,16);
});
test('create preserves explicit provider and workspace, and rejects a foreign successful-looking receipt',async()=>{
  const profile={...ref,id:'explicit-model'},payload={operation_id:'new-owner',provider_profile_ref:profile,capability_binding_sha256:'c'.repeat(64)};
  const calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return session({provider_profile_ref:profile,workspace_ref:null});}});
  const result=await dispatch('session.create',{scope:{project_id:'project'},payload});assert.equal(result.provider_profile_ref.id,'explicit-model');assert.equal(calls.length,1);assert.equal(calls[0].body.project_id,'project');assert.equal(calls[0].body.capability_binding_sha256,'c'.repeat(64));
  const foreign=createIdeApiAdapter({request:async()=>session({provider_profile_ref:{...profile,sha256:'d'.repeat(64)}})});
  await assert.rejects(foreign('session.create',{scope:{project_id:'project'},payload}),e=>e.code==='STALE_SESSION_SCOPE');
});

// UNIT original MCP message boundary; registry verification is exercised below
// and against real HTTP/SQLite by test_ide_artifact_projection.py.
const artifactId='b'.repeat(64),artifactSha='c'.repeat(64);
const artifactManifest=(changes={})=>({id:artifactId,project_id:'project',revision:1,kind:'REPORT_PPTX',filename:'原制作稿.pptx',media_type:'application/vnd.openxmlformats-officedocument.presentationml.presentation',sha256:artifactSha,byte_length:1200,download_url:'/api/projects/project/report-artifacts/'+artifactId+'/download',...changes});
const artifactMessage=(manifest=artifactManifest(),changes={})=>({info:{id:'msg_report',role:'assistant',sessionID:'ses_original'},parts:[{id:'prt_report',type:'tool',sessionID:'ses_original',messageID:'msg_report',callID:'call_report',tool:'deliverable-authoring_make_report_deck',state:{status:'completed',output:JSON.stringify({status:'SAVED_NOT_VISUALLY_VERIFIED',artifact_manifest:manifest,artifact_ref:{project_id:manifest.project_id,id:manifest.id,revision:manifest.revision,sha256:manifest.sha256},producer:{id:'eeas.deliverable-authoring',version:'0.1.0'}})},...changes}]});
const artifactSession=(messages=[artifactMessage()])=>session({original_session_id:'ses_original',messages});
test('completed admitted MCP report is projected only after exact registry-byte metadata verification',async()=>{
 const calls=[],dispatch=createIdeApiAdapter({request:async url=>{calls.push(url);return url===route?artifactSession():{...artifactManifest(),request_sha256:'DO_NOT_EXPORT',traceability:{body:'PRIVATE'}};}});
 const value=await dispatch('session.get',{scope});assert.equal(value.artifact_channel,'REGISTERED_ARTIFACTS_AND_WORKSPACE_FILES');assert.equal(value.artifacts.length,1);assert.equal(value.artifacts[0].filename,'原制作稿.pptx');assert.equal(value.artifacts[0].source.call_id,'call_report');assert.equal(value.artifacts[0].verification,'REGISTRY_BYTES_VERIFIED');assert.equal(value.artifacts[0].visual_validation,'NOT_RUN');assert.deepEqual(calls,[route,'/api/projects/project/report-artifacts/'+artifactId]);assert.doesNotMatch(JSON.stringify(value),/DO_NOT_EXPORT|PRIVATE|artifact_manifest/);
});
test('untrusted output, foreign owner/project and running tools cannot become artifact links or trigger remote reads',async()=>{
 const cases=[artifactMessage(artifactManifest({project_id:'foreign'})),artifactMessage(artifactManifest(),{sessionID:'foreign'}),artifactMessage(artifactManifest(),{messageID:'foreign'}),artifactMessage(artifactManifest(),{tool:'arbitrary_make_report_deck'}),artifactMessage(artifactManifest(),{state:{status:'running',output:artifactMessage().parts[0].state.output}}),{info:{id:'msg_text',role:'assistant',sessionID:'ses_original'},parts:[{type:'text',text:artifactMessage().parts[0].state.output},{type:'file',filename:'result.pptx',url:'file:///private/result.pptx'}]}];
 for(const message of cases){let gets=0;const dispatch=createIdeApiAdapter({request:async url=>{if(url!==route)gets++;return artifactSession([message]);}});const value=await dispatch('session.get',{scope});assert.deepEqual(value.artifacts,[]);assert.equal(gets,0);}
});
test('registry change or missing/denied record stays unavailable while a healthy report remains independently usable',async()=>{
 for(const changed of [{sha256:'d'.repeat(64)},{revision:2},{project_id:'other'},{filename:'changed.pptx'},{download_url:'https://private.invalid/report'},{missing:true}]){
  const dispatch=createIdeApiAdapter({request:async url=>{if(url===route)return artifactSession();if(changed.missing)throw Object.assign(Error('PRIVATE'),{status:404});return artifactManifest(changed);}}),value=await dispatch('session.get',{scope});assert.deepEqual(value.artifacts,[]);assert.equal(value.ide_coverage.artifacts.unavailable,1);assert.doesNotMatch(JSON.stringify(value),/PRIVATE|https:/);
 }
 const second=artifactManifest({id:'e'.repeat(64),download_url:'/api/projects/project/report-artifacts/'+'e'.repeat(64)+'/download'});const dispatch=createIdeApiAdapter({request:async url=>{if(url===route)return artifactSession([artifactMessage(),artifactMessage(second,{id:'prt_second',callID:'call_second'})]);if(url.endsWith(artifactId))throw Object.assign(Error('denied'),{status:403});return second;}});const value=await dispatch('session.get',{scope});assert.equal(value.artifacts.length,1);assert.equal(value.artifacts[0].artifact_id,second.id);assert.equal(value.ide_coverage.artifacts.unavailable,1);
});
test('duplicate successful calls reuse one card; malformed and oversized result payloads stay bounded',async()=>{
 let registryReads=0;const messages=[artifactMessage(),artifactMessage(artifactManifest(),{id:'prt_second',callID:'call_second'}),artifactMessage(artifactManifest(),{state:{status:'completed',output:'not JSON'}}),artifactMessage(artifactManifest(),{state:{status:'completed',output:'x'.repeat(131073)}})];
 const dispatch=createIdeApiAdapter({request:async url=>{if(url===route)return artifactSession(messages);registryReads++;return artifactManifest();}});const value=await dispatch('session.get',{scope});assert.equal(value.artifacts.length,1);assert.equal(registryReads,1);assert.equal(value.artifacts[0].source.call_id,'call_second');assert.equal(value.ide_coverage.artifacts.unavailable,2);
});
test('selected-session artifact open binds the actual completed tool, exact generation and explicit click',async()=>{
 const opened=[],dispatch=createIdeApiAdapter({request:async()=>artifactSession(),openArtifact:async body=>opened.push(body)});const payload={kind:'report',artifact_id:artifactId,sha256:artifactSha,download_url:artifactManifest().download_url};
 assert.equal((await dispatch('artifact.open',{scope,payload})).status,'OPEN_REQUESTED');assert.equal(opened.length,1);
 await assert.rejects(dispatch('artifact.open',{scope,payload:{...payload,sha256:'e'.repeat(64)}}),e=>e.code==='ARTIFACT_CHANGED');assert.equal(opened.length,1);
 await assert.rejects(dispatch('artifact.open',{scope:{...scope,generation:2},payload}),e=>e.code==='STALE_SESSION_SCOPE');assert.equal(opened.length,1);
});

const workspaceRef={id:'e'.repeat(64),revision:1,binding_sha256:'f'.repeat(64)};
const fileSession=(url='file:///D:/Original/report.docx',changes={})=>session({original_session_id:'ses_original',workspace_ref:workspaceRef,messages:[{info:{id:'msg_file',role:'user',sessionID:'ses_original'},parts:[{id:'prt_file',messageID:'msg_file',sessionID:'ses_original',type:'file',url,filename:'Untrusted.docx',mime:'application/octet-stream'}]}],...changes});
const binding=(changes={})=>({project_id:'project',...workspaceRef,root:'D:\\Original',...changes});
const source=(changes={})=>({kind:'local_file',project_id:'project',workspace_id:workspaceRef.id,binding_revision:1,binding_sha256:workspaceRef.binding_sha256,path:'report.docx',revision:1,sha256:'1'.repeat(64),...changes});
test('an exact original file URL in its registered Windows workspace becomes one format-neutral candidate without reading bytes on poll',async()=>{
 for(const extension of ['docx','xlsx','pptx','pdf','md','ts','diff']){const reads=[],dispatch=createIdeApiAdapter({request:async url=>{reads.push(url);if(url===route)return fileSession('file:///D:/Original/report.'+extension);return binding();}});const value=await dispatch('session.get',{scope});assert.equal(value.artifacts.length,1);assert.equal(value.artifacts[0].filename,'report.'+extension);assert.equal(value.artifacts[0].target,['md','ts','diff'].includes(extension)?'editor':'native');assert.equal(value.artifacts[0].content_basis,'CURRENT_SAVED_BYTES_ON_EXPLICIT_OPEN');assert.equal(reads.length,2);assert.equal(reads.some(url=>url.includes('/file?')),false);assert.doesNotMatch(JSON.stringify(value),/file:\/\/\/|D:\\\\Original/);}
});
test('out-of-root, WSL/UNC, executable and unbound file URLs remain plain references, never path authority',async()=>{
 for(const url of ['file:///D:/Other/report.docx','file://wsl.localhost/Ubuntu/tmp/a.docx','file://server/share/a.docx','https://example.invalid/a.docx','data:text/plain,a','file:///D:/Original/.git/config','file:///D:/Original/app.exe','file:///D:/Original/path%2Freport.docx','file:///E:/Original/report.docx']){
  let opened=0;const dispatch=createIdeApiAdapter({request:async endpoint=>endpoint===route?fileSession(url):binding(),openArtifact:async()=>opened++});const result=await dispatch('session.get',{scope});assert.deepEqual(result.artifacts,[]);assert.equal(opened,0);
 }
 const changed=createIdeApiAdapter({request:async endpoint=>endpoint===route?fileSession():binding({revision:2})});const value=await changed('session.get',{scope});assert.deepEqual(value.artifacts,[]);assert.equal(value.ide_coverage.artifacts.unavailable,1);
});
test('workspace Office open resolves a current exact FileSource before the sole controlled native callback',async()=>{
 const requests=[],opened=[],dispatch=createIdeApiAdapter({request:async url=>{requests.push(url);if(url===route)return fileSession();if(url.includes('/file?'))return {source_ref:source(),current:true,live_sha256:source().sha256};return binding();},openArtifact:async(body,context)=>{opened.push({body,context});return{status:'OPEN_REQUESTED'};}});
 const projected=await dispatch('session.get',{scope});requests.length=0;const result=await dispatch('artifact.open',{scope,payload:{kind:'workspace-file',file_ref:projected.artifacts[0].file_ref}});assert.equal(result.status,'OPEN_REQUESTED');assert.equal(opened.length,1);assert.deepEqual(opened[0].body,{kind:'workspace-file',target:'native',project_id:'project',file_source_ref:source()});assert.deepEqual(opened[0].context.scope,scope);assert.equal(requests.filter(url=>url.includes('/file?')).length,1);
});
test('file hash/binding/identity drift is rejected before opening; exact local Markdown is resolved for the editor, not shell',async()=>{
 const file_ref={workspace_ref:workspaceRef,path:'report.docx',message_id:'msg_file',part_id:'prt_file'};
 for(const changed of [{project_id:'other'},{binding_revision:2},{sha256:'invalid'},{path:'other.docx'},{workspace_id:'0'.repeat(64)}]){let opened=0;const dispatch=createIdeApiAdapter({request:async url=>url===route?fileSession():url.includes('/file?')?{source_ref:source(changed),current:true,live_sha256:source().sha256}:binding(),openArtifact:async()=>opened++});await assert.rejects(dispatch('artifact.open',{scope,payload:{kind:'workspace-file',file_ref}}),e=>e.code==='ARTIFACT_CHANGED');assert.equal(opened,0);}
 const mdSource=source({path:'notes.md'}),dispatch=createIdeApiAdapter({request:async url=>url===route?fileSession('file:///owned/notes.md'):url.includes('/file?')?{source_ref:mdSource,current:true,live_sha256:mdSource.sha256}:binding({root:'/owned'}),openArtifact:async body=>{assert.equal(body.target,'editor');return {status:'RESOLVED',native_path:'/owned/notes.md',file_source_ref:mdSource};}});
 const result=await dispatch('artifact.open',{scope,payload:{kind:'workspace-file',file_ref:{...file_ref,path:'notes.md'}}});assert.equal(result.status,'EDITOR_READY');assert.equal(result.editor_uri,'file:///owned/notes.md');assert.equal(result.editor_kind,'markdown');assert.equal(result.editor_buffer_modified,false);
});

test('actual Main injection routes only admitted workspace/editor/report opens, keeps Main hidden and avoids borrowing another Quick topic',async()=>{
 const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),main=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');const start=main.indexOf('const dispatch=createIdeApiAdapter({request,');const end=main.indexOf('  candidate=new IdeCompanionBridge',start);assert(start>0&&end>start);const native=[],reads=[],options=vm.runInNewContext('(function(){'+main.slice(start,end)+'return dispatch;})()',{createIdeApiAdapter:value=>value,request:async(...args)=>{reads.push(args);return{status:'RESOLVED'};},resolveAndOpenDocument:async(...args)=>{native.push(args);return{status:'OPEN_REQUESTED'};}});
 const s={session_ref:{id:'the-original-session'}},body={project_id:'project',file_source_ref:source()};await options.openArtifact({kind:'workspace-file',target:'editor',...body},{scope:s});assert.equal(reads.length,1);assert.equal(native.length,0);assert.equal(reads[0][0],'/api/desktop-host/native-open/file');assert.deepEqual(JSON.parse(JSON.stringify(reads[0][1])),body);
 await options.openArtifact({kind:'workspace-file',target:'native',...body},{scope:s});assert.equal(native.length,1);assert.equal(native[0][0],'/api/desktop-host/native-open/file');assert.equal(native[0][2].keepHidden,true);assert.equal(native[0][2].associationScope.sessionId,s.session_ref.id);assert.equal(native[0][2].associationScope.conversationId,undefined);
 await options.openArtifact({kind:'report',project_id:'project',artifact_id:artifactId,sha256:artifactSha,download_url:artifactManifest().download_url});assert.equal(native[1][0],'/api/desktop-host/native-open/report');assert.equal(native[1][2].keepHidden,true);
});

const linkedSession=(text,changes={})=>fileSession(undefined,{messages:[{info:{id:'msg_link',role:'assistant',sessionID:'ses_original'},parts:[{type:'text',id:'prt_link',sessionID:'ses_original',messageID:'msg_link',text}]}],...changes});
test('original Markdown AST links project format-neutral files; code, HTML, images, remote and out-of-scope hrefs never grant authority',async()=>{
 const text='[Word](report.docx) [sheet](<folder/sheet 1.xlsx>) [notes][n]\n\n[n]: notes.md\n\n`[code](code.docx)`\n\n```md\n[fenced](bad.docx)\n```\n<img src="image.docx">\n![image](picture.png) [remote](https://remote.invalid/a.docx) [js](javascript:evil) [outside](../outside.docx) [UNC](file://server/share/a.docx)';
 const gets=[],dispatch=createIdeApiAdapter({request:async url=>{gets.push(url);return url===route?linkedSession(text):binding();}}),result=await dispatch('session.get',{scope});
 assert.deepEqual(result.artifacts.map(a=>a.file_ref.path),['report.docx','folder/sheet 1.xlsx','notes.md']);assert(result.artifacts.every(a=>a.verification==='EXACT_ORIGINAL_MARKDOWN_HREF_AND_WORKSPACE_BINDING'&&/^[a-f0-9]{64}$/.test(a.file_ref.href_sha256)));assert.equal(gets.length,2);assert.equal(gets.some(url=>url.includes('/file?')),false);assert.doesNotMatch(JSON.stringify(result.artifacts),/D:|javascript:|remote.invalid/);
});
test('Markdown explicit click reparses exact original href and rejects tampered target, old owner or changed link without reading an ungranted file',async()=>{
 let message='[Word](report.docx)',opened=0,fileReads=0;const dispatch=createIdeApiAdapter({request:async url=>{if(url===route)return linkedSession(message);if(url.includes('/file?')){fileReads++;return{source_ref:source(),current:true,live_sha256:source().sha256};}return binding();},openArtifact:async()=>{opened++;return{status:'OPEN_REQUESTED'};}});
 const item=(await dispatch('session.get',{scope})).artifacts[0],payload={kind:'workspace-file',file_ref:item.file_ref};await dispatch('artifact.open',{scope,payload});assert.equal(opened,1);assert.equal(fileReads,1);
 for(const file_ref of [{...item.file_ref,path:'other.docx'},{...item.file_ref,link_index:1},{...item.file_ref,href_sha256:'0'.repeat(64)}])await assert.rejects(dispatch('artifact.open',{scope,payload:{kind:'workspace-file',file_ref}}),e=>e.code==='ARTIFACT_CHANGED');
 message='[Word](./report.docx)';await assert.rejects(dispatch('artifact.open',{scope,payload}),e=>e.code==='ARTIFACT_CHANGED');assert.equal(fileReads,1);assert.equal(opened,1);
 await assert.rejects(dispatch('artifact.open',{scope:{...scope,generation:scope.generation+1},payload}),e=>e.code==='STALE_SESSION_SCOPE');assert.equal(opened,1);
});
test('Markdown requires genuine root assistant source and explicit link; unknown paths remain unavailable until user click',async()=>{
 for(const messages of [linkedSession('[file](report.docx)').messages.map(m=>({...m,info:{...m.info,role:'user'}})),linkedSession('[file](report.docx)').messages.map(m=>({...m,parts:m.parts.map(p=>({...p,messageID:'foreign'}))})),linkedSession('report.docx is ready; file:///D:/Original/report.docx').messages,linkedSession('[too big](report.docx)'+'.'.repeat(16001)).messages]){
  const dispatch=createIdeApiAdapter({request:async url=>url===route?linkedSession('',{messages}):binding()});assert.deepEqual((await dispatch('session.get',{scope})).artifacts,[]);
 }
 let opened=0;const dispatch=createIdeApiAdapter({request:async url=>{if(url===route)return linkedSession('[missing](missing.docx)');if(url.includes('/file?'))throw Object.assign(Error('private-path'),{status:404});return binding();},openArtifact:async()=>opened++});const item=(await dispatch('session.get',{scope})).artifacts[0];await assert.rejects(dispatch('artifact.open',{scope,payload:{kind:'workspace-file',file_ref:item.file_ref}}));assert.equal(opened,0);
});
test('native directory and history keep explicit project/discussion identity and safe original request origins',async()=>{
  const calls=[],dispatch=createIdeApiAdapter({request:async url=>{calls.push(url);return url.includes('/desktop-topic')?
    nativeConversation([nativeMessage('one',{input:{raw_text:'{"host":"internal prompt"}',origin:'ORIGINAL_NATIVE_REQUEST'},execution:{token:'PRIVATE'},events:['PRIVATE'],error:{message:'PRIVATE raw diagnostic'}})],{title:'交付与返工 😀'}):
    {kind:'NATIVE_CONVERSATION_DIRECTORY',project_id:'project',conversations:[{kind:'NATIVE_CONVERSATION',...nativeScope,title:'交付与返工 😀',message_count_observed:1,last_operation_id:'one',private:'PRIVATE'}],coverage:nativeCoverage(1)};}});
  const list=await dispatch('native.conversations.list',{scope:{project_id:'project'},payload:{limit:2}});
  const history=await dispatch('native.conversation.get',{scope:nativeScope,payload:{limit:2}});
  assert.equal(list.conversations[0].conversation_id,'desktop-topic');assert.equal(history.messages[0].input.origin,'ORIGINAL_NATIVE_REQUEST');
  assert.equal(list.conversations[0].title,'交付与返工 😀');assert.equal(history.title,'交付与返工 😀');
  assert.deepEqual(history.latest_provider_profile_ref,nativeProfile);assert.doesNotMatch(JSON.stringify({list,history}),/PRIVATE|execution|events/);
  assert.deepEqual(calls,['/api/desktop-host/native-conversations?project_id=project&limit=2',nativeRoute+'?project_id=project&limit=2']);
});
test('native scope cannot borrow AgentSession identity and a same-project different discussion response is refused',async()=>{
  let calls=0;const dispatch=createIdeApiAdapter({request:async()=>{calls++;return nativeConversation([],{conversation_id:'another'});}});
  for(const method of ['native.conversation.get','native.message'])await assert.rejects(dispatch(method,{scope}),e=>e.public);
  await assert.rejects(dispatch('native.conversations.list',{scope:nativeScope}),e=>e.public);
  await assert.rejects(dispatch('session.get',{scope:nativeScope}),e=>e.public);assert.equal(calls,0);
  await assert.rejects(dispatch('native.conversation.get',{scope:nativeScope}),e=>e.code==='STALE_NATIVE_SCOPE');assert.equal(calls,1);
});
test('native send binds API defaults and chosen public profile, once; a lost reply only reads its original operation',async()=>{
  for(const lost of [false,true]){
    const calls=[];let saved;const dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});
      if(body){saved=body;if(lost)throw Error('PRIVATE transport');return {};}
      return url.includes('/operations/')?nativeMessage(saved.operation_id,{request_sha256:sha(saved)}):nativeConversation();}});
    const result=await dispatch('native.message',{scope:nativeScope,payload:{operation_id:'new-native',raw_text:'  原话 😀\r\n',provider_profile_ref:nativeProfile}});
    assert.equal(result.operation_id,'new-native');assert.deepEqual(saved,{operation_id:'new-native',raw_text:'  原话 😀\r\n',context_ref:null,project_id:'project',conversation_id:'desktop-topic',provider_profile_ref:nativeProfile,file_source_ref:null,file_link_ref:null});
    assert.equal(calls.filter(c=>c.body).length,1);assert.equal(calls.filter(c=>c.url.includes('/operations/new-native')).length,1);
  }
});
test('native send missing profile, foreign file, known rejection, missing receipt and mismatch never repeat a POST',async()=>{
  let posts=0,gets=0;const dispatch=createIdeApiAdapter({request:async(url,body)=>{if(body)posts++;gets++;return nativeConversation();}});
  for(const payload of [{operation_id:'x',raw_text:'hi'},{operation_id:'x',raw_text:'hi',provider_profile_ref:nativeProfile,file_source_ref:{project_id:'other'}}])
    await assert.rejects(dispatch('native.message',{scope:nativeScope,payload}));assert.equal(posts,0);assert.equal(gets,0);
  for(const scenario of ['rejected','unknown','mismatch']){
    let post=0,read=0;const action=createIdeApiAdapter({request:async(url,body)=>{
      if(body){post++;throw Object.assign(Error('PRIVATE'),scenario==='rejected'?{status:409,quick_submission_observation:{kind:'HTTP_RESPONSE',http_status:409,api_code:'REVISION_CONFLICT'}}:{});}
      if(url.includes('/operations/')){read++;if(scenario==='unknown')throw Error('PRIVATE');return nativeMessage('once');}return nativeConversation();}});
    await assert.rejects(action('native.message',{scope:nativeScope,payload:{operation_id:'once',raw_text:'hi',provider_profile_ref:nativeProfile}}),e=>e.code===(scenario==='rejected'?'API_REQUEST_REJECTED':scenario==='unknown'?'OPERATION_OUTCOME_UNKNOWN':'OPERATION_INPUT_MISMATCH'));
    assert.equal(post,1);assert.equal(read,scenario==='rejected'?0:1);
  }
});
test('native guidance/status reads exact parent, uses one control receipt and rejects forged control identity',async()=>{
  for(const mismatch of [false,true]){
    let saved,posts=0;const dispatch=createIdeApiAdapter({request:async(url,body)=>{
      if(body){posts++;saved=body;throw Error('lost acknowledgement');}
      if(url.includes('/controls/')){const request={...saved,...mismatch?{operation_id:'foreign'}:{}};return{operation_id:'guide',parent_operation_id:'parent',native_message_ref:nativeRef,status:'ACCEPTED',request,request_sha256:sha(request),execution:'PRIVATE'};}
      return nativeMessage('parent',{control_capabilities:{can_guide:false,can_status:true,can_cancel:false}});}});
    const pending=dispatch('native.guidance',{scope:nativeScope,payload:{parent_operation_id:'parent',operation_id:'guide',expected_native_message_ref:nativeRef,raw_text:'进展如何',intent:'STATUS'}});
    if(mismatch)await assert.rejects(pending,e=>e.code==='OPERATION_INPUT_MISMATCH');else {const value=await pending;assert.equal(value.kind,'NATIVE_CONTROL');assert.doesNotMatch(JSON.stringify(value),/PRIVATE/);}assert.equal(posts,1);
    await assert.rejects(dispatch('native.guidance',{scope:nativeScope,payload:{parent_operation_id:'parent',operation_id:'steer',expected_native_message_ref:nativeRef,raw_text:'改目标',intent:'STEER'}}),e=>e.code==='NATIVE_CONTROL_NOT_AVAILABLE');assert.equal(posts,1);
  }
});
test('native cancel is same original current owner only; historical and wrong conversation cannot cancel',async()=>{
  for(const change of [{},{record_origin:'HISTORICAL_READONLY'},{conversation_id:'other'}]){
    let posts=0;const dispatch=createIdeApiAdapter({request:async(_url,body)=>{if(body){posts++;throw Error('lost reply');}return nativeMessage('parent',{...change,status:posts?'CANCEL_REQUESTED':'PROCESSING'});}});
    const pending=dispatch('native.cancel',{scope:nativeScope,payload:{operation_id:'parent',expected_native_message_ref:nativeRef}});
    if(Object.keys(change).length)await assert.rejects(pending);else assert.equal((await pending).status,'CANCEL_REQUESTED');
    assert.equal(posts,Object.keys(change).length?0:1);
  }
});
test('native abort during prerequisite read never starts a late write',async()=>{
  for(const method of ['native.message','native.guidance','native.cancel']){
    const controller=new AbortController();let posts=0;const dispatch=createIdeApiAdapter({request:async(url,body)=>{
      if(body)posts++;controller.abort();return url.includes('/operations/')?nativeMessage():nativeConversation();}});
    const payload=method==='native.message'?{operation_id:'new',raw_text:'hi',provider_profile_ref:nativeProfile}:
      method==='native.guidance'?{parent_operation_id:'parent',operation_id:'guide',raw_text:'hi',expected_native_message_ref:nativeRef}:
      {operation_id:'parent',expected_native_message_ref:nativeRef};
    await assert.rejects(dispatch(method,{scope:nativeScope,payload},{signal:controller.signal}),e=>e.code==='REQUEST_CANCELLED');assert.equal(posts,0);
  }
});
test('native history payload trimming declares incomplete coverage instead of dropping rows silently',async()=>{
  const messages=Array.from({length:8},(_,i)=>nativeMessage('op'+i,{text:'字'.repeat(45000)}));
  const dispatch=createIdeApiAdapter({request:async()=>nativeConversation(messages)}),value=await dispatch('native.conversation.get',{scope:nativeScope});
  assert.ok(value.messages.length<8);assert.equal(value.coverage.has_more,true);assert.equal(value.coverage.complete,false);
  assert.equal(value.coverage.returned,value.messages.length);assert.ok(Buffer.byteLength(JSON.stringify(value))<=512*1024);
});
test('native ambiguous HTTP 408/429 and bare 409 only inspect original receipts, never imply a rejected POST',async()=>{
  for(const status of [408,429,409])for(const method of ['native.message','native.guidance','native.cancel'])for(const readable of [true,false]){
    let posts=0,recovery=0,saved;const dispatch=createIdeApiAdapter({request:async(url,body)=>{
      if(body){posts++;saved=body;throw Object.assign(Error('PRIVATE body'),{status,quick_submission_observation:{kind:'HTTP_RESPONSE',http_status:status}});}
      if(posts){recovery++;if(!readable)throw Object.assign(Error('no receipt'),{status:404});
        if(method==='native.guidance')return{operation_id:'new',parent_operation_id:'parent',native_message_ref:nativeRef,status:'ACCEPTED',request:saved,request_sha256:sha(saved)};
        return nativeMessage(method==='native.message'?'new':'parent',{request_sha256:method==='native.message'?sha(saved):'b'.repeat(64),native_message_phase:'STOP_REQUESTED'});}
      return url.includes('/operations/')?nativeMessage():nativeConversation();}});
    const payload=method==='native.message'?{operation_id:'new',raw_text:'hi',provider_profile_ref:nativeProfile}:
      method==='native.guidance'?{parent_operation_id:'parent',operation_id:'new',raw_text:'hi',expected_native_message_ref:nativeRef}:
      {operation_id:'parent',expected_native_message_ref:nativeRef};
    const result=dispatch(method,{scope:nativeScope,payload});
    if(readable)assert.ok(await result);else await assert.rejects(result,e=>e.code==='OPERATION_OUTCOME_UNKNOWN');
    assert.equal(posts,1);assert.equal(recovery,1);
  }
});


test('Workflow IDE messages preserve the explicit mode and exact owner; active tasks receive guidance without mode replacement',async()=>{
 for(const busy of [false,true]){
  const calls=[],dispatch=createIdeApiAdapter({request:async(url,body)=>{calls.push({url,body});return body?receipt(body,busy?'GUIDANCE':'MESSAGE'):session({active_operation_id:busy?'frozen_task':null});}});
  const result=await dispatch('session.message',{scope,payload:{operation_id:busy?'workflow_guide':'workflow_idle',raw_text:'Same shared task 中文 😀',agent:'workflow'}});
  assert.equal(calls.filter(row=>row.body).length,1);assert.deepEqual(calls[1].body.session_ref,scope.session_ref);assert.equal(calls[1].body.generation,scope.generation);
  if(busy){assert.equal(result.kind,'GUIDANCE');assert.equal(calls[1].body.expected_active_operation_id,'frozen_task');assert.equal('agent' in calls[1].body,false);}
  else{assert.equal(result.kind,'MESSAGE');assert.equal(calls[1].body.agent,'workflow');}
 }
});


test('IDE projection carries only admitted modes and exact current-task mode, not runtime policy or stale mode bindings',async()=>{const current={operation_id:'current',generation:1,agent:'workflow',policy_sha256:'b'.repeat(64),native_message_id:'msg_current',runtime_secret:'LEAK'};const source=session({active_operation_id:'current',available_agent_modes:['plan','build','workflow','shell'],workflow_mode_supported:true,work_mode_message:current});const dispatch=createIdeApiAdapter({request:async()=>source});const result=await dispatch('session.get',{scope});assert.deepEqual(result.available_agent_modes,['plan','build','workflow']);assert.equal(result.workflow_mode_supported,true);assert.deepEqual(result.work_mode_message,{operation_id:'current',generation:1,agent:'workflow'});assert.doesNotMatch(JSON.stringify(result),/runtime_secret|LEAK|policy_sha256/);source.work_mode_message={...current,generation:2};const stale=await dispatch('session.get',{scope});assert.equal(stale.work_mode_message,undefined);source.workflow_mode_supported=false;assert.deepEqual((await dispatch('session.get',{scope})).available_agent_modes,['plan','build']);});
