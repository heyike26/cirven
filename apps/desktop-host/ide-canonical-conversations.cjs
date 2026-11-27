'use strict';
// Logical conversation navigation delegates to the existing canonical API.
const {createHash}=require('node:crypto');
const nativeRecovery=require('./ide-canonical-native-recovery.cjs');
const workInput=require('../../plugins/vscode-companion/work-input-wire.cjs');
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,CID=/^(native|original|discovery):[a-f0-9]{64}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v),copy=v=>JSON.parse(JSON.stringify(v));
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':object(v)?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const digest=v=>createHash('sha256').update(stable(v)).digest('hex'),same=(a,b)=>stable(a)===stable(b);
function fail(code='CANONICAL_SCOPE_CHANGED'){throw Object.assign(new Error('会话状态已变化，输入保留，请核查原操作。'),{code,public:true});}
function check(v,code){if(!v)fail(code);}
function keys(v,names){check(object(v)&&Object.keys(v).every(k=>names.includes(k)),'INVALID_PARAMS');}
function id(v){check(typeof v==='string'&&ID.test(v),'INVALID_PARAMS');return v;}
function hash(v){check(typeof v==='string'&&SHA.test(v),'INVALID_PARAMS');return v;}
function project(v){check(v===null||typeof v==='string'&&ID.test(v),'INVALID_SCOPE');return v;}
function ref(v){keys(v,['id','revision','sha256']);check(Number.isSafeInteger(v.revision)&&v.revision>=1,'INVALID_PARAMS');return {id:id(v.id),revision:v.revision,sha256:hash(v.sha256)};}
function conversation(v,minimum=1){keys(v,['id','revision']);check(CID.test(v.id??'')&&Number.isSafeInteger(v.revision)&&v.revision>=minimum,'INVALID_SCOPE');return {id:v.id,revision:v.revision};}
function scope(v,required){keys(v,['project_id','conversation_ref']);const s={project_id:project(v.project_id)};if(required)s.conversation_ref=conversation(v.conversation_ref,0);else check(v.conversation_ref===undefined,'INVALID_SCOPE');return s;}
function owner(v,minimum=1){keys(v,['project_id','session_ref','generation']);check(Number.isSafeInteger(v.generation)&&v.generation>=minimum);const r=ref(v.session_ref);check(r.revision===1);return {project_id:id(v.project_id),session_ref:r,generation:v.generation};}
function allowed(pid,grants){return grants.project_ids===null||Array.isArray(grants.project_ids)&&pid!==null&&grants.project_ids.includes(pid);}
function inputFile(row,grants){
 const file=row.input_file;
 if(row.role!=='user'||row.source.kind!=='original'||!file?.verified||
   typeof file.filename!=='string'||!ID.test(row.source.project_id??'')||!allowed(row.source.project_id,grants))return {};
 const canOpen=file.openable===true&&ID.test(row.operation_id??'');
 return {input_file:{filename:file.filename.slice(0,300),execution_project_id:canOpen?row.source.project_id:null,
   open_allowed:canOpen&&grants.methods?.includes('conversation.file.open')===true}};
}
function legacySource(v){
 const names=['kind','project_id','session_ref','generation','provider_profile_ref','workspace_ref','request_project_id','conversation_id','last_operation_id','request_sha256','record_origin','object_ref'];keys(v,names);
 check(['native','original','discovery'].includes(v.kind));project(v.project_id);
 if(v.kind==='original'){ref(v.session_ref);check(Number.isSafeInteger(v.generation)&&v.generation>=0);}
 else if(v.kind==='native'){id(v.conversation_id);id(v.last_operation_id);hash(v.request_sha256);check(['CURRENT_HOST','HISTORICAL_READONLY'].includes(v.record_origin));project(v.request_project_id);}
 else ref(v.object_ref);
 if(v.provider_profile_ref!==null&&v.provider_profile_ref!==undefined)ref(v.provider_profile_ref);
 if(v.workspace_ref!==null&&v.workspace_ref!==undefined){keys(v.workspace_ref,['id','revision','binding_sha256']);hash(v.workspace_ref.id);hash(v.workspace_ref.binding_sha256);check(Number.isSafeInteger(v.workspace_ref.revision)&&v.workspace_ref.revision>=1);}
 check(Buffer.byteLength(JSON.stringify(v))<=8192);return copy(v);
}
function messageBody(s,p){
 keys(p,['operation_id','provider_profile_ref','raw_text','agent','original_source','original_work_ref']);
 let work;try{work=workInput.originalWorkRef(p.original_work_ref);}catch{fail('INVALID_PARAMS');}
 check(typeof p.raw_text==='string'&&p.raw_text.trim()&&Array.from(p.raw_text).length<=16000,'INVALID_PARAMS');const agent=p.agent??'build';check(['plan','build','workflow'].includes(agent),'INVALID_PARAMS');
 return {operation_id:id(p.operation_id),conversation_ref:conversation(s.conversation_ref),provider_profile_ref:ref(p.provider_profile_ref),raw_text:p.raw_text,agent,original_source:p.original_source==null?null:owner(p.original_source,0),input_attachments:[],...(work?{original_work_ref:work}:{})};
}
function connectionBody(s,p){
 keys(p,['operation_id','provider_profile_ref','capability_binding_sha256']);
 return {operation_id:id(p.operation_id),conversation_ref:conversation(s.conversation_ref),workspace_ref:null,provider_profile_ref:ref(p.provider_profile_ref),workflow_ref:null,workflow_provider_refs:[],capability_binding_sha256:hash(p.capability_binding_sha256),title:'Cirven conversation',ttl_seconds:600,max_calls:24,max_output_tokens:4096,allowed_effects:[],command_allowlist:[],mcp_tool_allowlist:[],mcp_tool_selection:null,network_mode:'AS_CONFIGURED',project_id:s.project_id};
}
function messageReceipt(v,s,key,expected,body){
 check(v?.version===1&&v.operation_id===key&&same(v.conversation_ref,s.conversation_ref)&&v.request_sha256===expected&&v.model_calls_by_query===0&&v.owner_started_by_query===false&&v.automatic_post_replay===false);
 const op=v.operation,ack=op?.native_dispatch_acknowledged===true,local=op?.role==='LOCAL_STATUS'&&op.status==='RETURNED';
 check(typeof v.status==='string'&&v.status.length<=80);
 const reference=body?.original_work_ref??op?.request?.original_work_ref;let extra={};
 if(reference){try{const work=workInput.originalWorkRef(reference);if(ack||local){const source=owner(v.original_source);if(body)check(same(source,body.original_source));workInput.verifyOriginalWorkInputReceipt(op,source,work,body?.raw_text??op.request.raw_text,workInput.originalWorkStatusText(body?.raw_text??op.request.raw_text));check(op.id===key);extra={original_work_ref:work,...(local?{local_status:copy(op.local_status)}:{})};check(Buffer.byteLength(JSON.stringify(extra))<=8192);}}catch{fail('CANONICAL_RECEIPT_CHANGED');}}
 return {...extra,kind:'CANONICAL_MESSAGE',version:1,operation_id:key,conversation_ref:s.conversation_ref,request_sha256:expected,status:v.status,original_source:v.original_source?owner(v.original_source):null,accepted:ack||local,native_dispatch_acknowledged:ack,local_status_returned:local,can_cancel:v.can_cancel===true,model_calls_by_query:0,owner_started_by_query:false,automatic_post_replay:false};
}
async function dispatch(method,params,context,api,projectSession){
 const p=params.payload??{},list=['conversation.list','conversation.create','conversation.creation.operation'].includes(method),s=scope(params.scope??{},!list),signal=context.signal,grants=context.grants;
 check(grants&&allowed(s.project_id,grants),'SCOPE_DENIED');
 const get=path=>api(path,undefined,signal),post=(path,body)=>{if(signal?.aborted)fail('REQUEST_CANCELLED');return api(path,body,signal);},base=s.conversation_ref?'/api/conversations/'+encodeURIComponent(s.conversation_ref.id):null;
 async function current(){const value=await get(base+'/scope');check(value?.version===1&&value.project_id===s.project_id&&same(value.conversation_ref,s.conversation_ref)&&Array.isArray(value.sources)&&value.sources.length<=64);return value;}
 function selectedOwner(value){
  const originals=value.sources.map(row=>row.original_source).filter(row=>row?.kind==='original');
  const head=value.current_owner;
  if(head&&head.status!=='LEGACY'){
   if(head.status==='PENDING')return {status:'PENDING',source:null};if(head.status==='SELECTION_REQUIRED')return {status:'OWNER_SELECTION_REQUIRED',source:null};check(['CURRENT','SELECTED'].includes(head.status));const selected=owner(head.original_source);check(originals.some(row=>row.project_id===selected.project_id&&same(row.session_ref,selected.session_ref)));return {status:head.status,source:selected};
  }
  if(originals.length>1)return {status:'OWNER_SELECTION_REQUIRED',source:null};
  return {status:originals.length?'LEGACY':'EMPTY',source:originals.length?{project_id:id(originals[0].project_id),session_ref:ref(originals[0].session_ref)}:null};
 }
 async function checkedOwner(value){const target=selectedOwner(value);if(target.source&&!allowed(target.source.project_id,grants))fail('EXECUTION_SCOPE_DENIED');return target;}
 if(method.startsWith('conversation.owner.')){
  if(method==='conversation.owner.prepare'){
   keys(p,[]);const value=await post(base+'/original-owner/prepare',{conversation_ref:s.conversation_ref,project_id:s.project_id});
   check(value?.kind==='ORIGINAL_OWNER_PREPARATION'&&same(value.conversation_ref,s.conversation_ref)&&value.project_id===s.project_id&&typeof value.selection_allowed==='boolean'&&value.read_only===true&&value.model_calls===0&&value.owner_started===false&&value.permission_granted===false&&Array.isArray(value.candidates)&&value.candidates.length<=64&&Buffer.byteLength(JSON.stringify(value))<=524288);
   const candidates=value.candidates.filter(row=>allowed(owner(row.original_source,0).project_id,grants));return {...value,candidates,hidden_by_project_grants:value.candidates.length-candidates.length};
  }
  let request,key,expected,selected,rejected;
  if(method==='conversation.owner.select'){
   keys(p,['operation_id','original_source','expected_selection_operation_id']);key=id(p.operation_id);selected=owner(p.original_source);check(allowed(selected.project_id,grants),'EXECUTION_SCOPE_DENIED');
   check(p.expected_selection_operation_id===null||ID.test(p.expected_selection_operation_id??''));request={operation_id:key,conversation_ref:s.conversation_ref,project_id:s.project_id,original_source:selected,expected_selection_operation_id:p.expected_selection_operation_id};expected=digest(request);
   try{await post(base+'/original-owner',request);}catch(error){rejected=error;}
  }else{
   check(method==='conversation.owner.operation');keys(p,['operation_id','request_sha256','original_source']);key=id(p.operation_id);expected=hash(p.request_sha256);selected=owner(p.original_source);check(allowed(selected.project_id,grants),'EXECUTION_SCOPE_DENIED');
  }
  let value;try{value=await get(base+'/original-owner-operations/'+encodeURIComponent(key));}catch(error){if(rejected&&Number.isInteger(rejected.status)&&rejected.status>=400&&rejected.status<500&&error?.status===404)fail('API_REQUEST_REJECTED');throw error;}
  check(value?.kind==='ORIGINAL_OWNER_SELECTION'&&value.operation_id===key&&value.request_sha256===expected&&value.project_id===s.project_id&&same(value.conversation_ref,s.conversation_ref)&&same(value.original_source,selected)&&value.selection_saved===true&&typeof value.selection_applied==='boolean'&&value.model_calls===0&&value.owner_started===false&&value.permission_granted===false&&value.automatic_message_replay===false);return value;
 }
 if(method.startsWith('conversation.configuration.')||method==='conversation.configure')return require('./ide-conversation-configuration.cjs').dispatch(method,s,p,{get,post,current,checkedOwner,allowed,grants});
 if(method==='conversation.list'){
  keys(p,['cursor','limit']);const limit=p.limit??30;check(Number.isSafeInteger(limit)&&limit>=1&&limit<=100,'INVALID_PARAMS');check(p.cursor===undefined||typeof p.cursor==='string'&&p.cursor.length<=2048,'INVALID_PARAMS');
  const query=s.project_id===null?'scope=unbound':'scope=project&project_id='+encodeURIComponent(s.project_id);
  const value=await get('/api/conversations?'+query+'&state=active&limit='+limit+(p.cursor?'&cursor='+encodeURIComponent(p.cursor):''));check(value?.version===1&&Array.isArray(value.items)&&value.items.length<=limit);
  const conversations=value.items.map(row=>{check(row.project_id===s.project_id&&CID.test(row.id??'')&&typeof row.organization?.archived==='boolean');const reference=row.conversation_ref?conversation(row.conversation_ref):{id:row.id,revision:0};check(reference.id===row.id);
   return {kind:'CANONICAL_CONVERSATION',organization_project_id:s.project_id,project_id:s.project_id,conversation_ref:reference,title:typeof row.title==='string'?Array.from(row.title).slice(0,300).join(''):null,source_ref:reference.revision===0?legacySource(row.source):null,archived:row.organization.archived};});
  check(new Set(conversations.map(v=>v.conversation_ref.id)).size===conversations.length);check(value.next_cursor===null||typeof value.next_cursor==='string'&&value.next_cursor.length<=2048);
  return {kind:'CANONICAL_CONVERSATION_DIRECTORY',project_id:s.project_id,conversations,next_cursor:value.next_cursor,coverage:{truncated:value.next_cursor!==null||value.coverage?.complete!==true},model_calls:0};
 }
 if(method==='conversation.select'){
  keys(p,['source_ref']);check(s.conversation_ref.revision===0);const source=legacySource(p.source_ref),value=await post(base+'/selection',{source});
  check(value?.version===1&&value.project_id===s.project_id&&conversation(value.conversation_ref).id===s.conversation_ref.id&&value.execution_started===false&&value.files_moved===false);
  return {kind:'CANONICAL_SELECTION',project_id:s.project_id,conversation_ref:conversation(value.conversation_ref),owner_started:false,model_calls:0};
 }
 if(method==='conversation.get'){
  keys(p,['cursor']);check(s.conversation_ref.revision>=1);check(p.cursor===undefined||typeof p.cursor==='string'&&p.cursor.length<=8192);const value=await current(),target=selectedOwner(value);
  const canControl=!target.source||allowed(target.source.project_id,grants);
  const ownerPath=target.source&&canControl?'/api/projects/'+encodeURIComponent(target.source.project_id)+'/agent-sessions/'+encodeURIComponent(target.source.session_ref.id)+'/snapshot':null;
  const [history,raw]=await Promise.all([get(base+'/history?limit=50'+(p.cursor?'&cursor='+encodeURIComponent(p.cursor):'')),ownerPath?get(ownerPath):Promise.resolve(null)]);
  check(history?.version===1&&same(history.conversation_ref,s.conversation_ref)&&history.project_id===s.project_id&&history.execution_authorized===false&&history.automatic_input_authorized===false&&history.file_write_authorized===false&&Array.isArray(history.messages)&&history.messages.length<=50);
  check(history.next_cursor===null||typeof history.next_cursor==='string'&&history.next_cursor.length<=8192);
  let historyTextBudget=65536;
  const nativeAnchors=new Map();for(const row of history.messages)if(row.source?.kind==='native'&&row.native_context_recovery){const key=row.source.alias_id+':'+row.operation_id;const chosen=nativeAnchors.get(key);if(!chosen||row.role==='assistant')nativeAnchors.set(key,row.id);}
  const messages=history.messages.map(row=>{check(SHA.test(row.id??'')&&['user','assistant'].includes(row.role)&&typeof row.text==='string'&&Buffer.byteLength(row.text)<=524288&&typeof row.text_truncated==='boolean'&&SHA.test(row.text_sha256??'')&&row.source&&['native','original','discovery'].includes(row.source.kind));const text=Array.from(row.text).slice(0,Math.min(4096,historyTextBudget)).join('');historyTextBudget-=Array.from(text).length;const privateContext=nativeAnchors.get(row.source?.alias_id+':'+row.operation_id)===row.id?nativeRecovery.historyContext(row,value,s,typeof context.currentGrants==='function'?context.currentGrants():grants):{};return {...privateContext,...inputFile(row,grants),id:row.id,role:row.role,text,text_truncated:row.text_truncated||text!==row.text,text_sha256:row.text_sha256,status:typeof row.status==='string'?row.status.slice(0,80):null,operation_id:typeof row.operation_id==='string'&&ID.test(row.operation_id)?row.operation_id:null,source_kind:row.source.kind,native_message_id:typeof row.native_message_id==='string'&&/^msg_[A-Za-z0-9_-]{1,100}$/.test(row.native_message_id)?row.native_message_id:null,original_source_id:row.source.kind==='original'&&ID.test(row.source.session_id??'')?row.source.session_id:null};});
  let original=null;
  if(raw){check(raw.project_id===target.source.project_id&&same(raw.session_ref,target.source.session_ref)&&(!target.source.generation||raw.generation===target.source.generation));original=projectSession(raw);}
  const after=await current();check(same(after.current_owner??null,value.current_owner??null),'CANONICAL_OWNER_CHANGED');
  return {kind:'CANONICAL_CONVERSATION',organization_project_id:s.project_id,conversation_ref:s.conversation_ref,title:typeof value.title==='string'?value.title.slice(0,300):null,archived:value.archived===true,connection_state:target.status,execution_access:canControl,owner:original,canonical_history:messages,next_history_cursor:history.next_cursor,history_partial:history.coverage?.complete!==true||messages.some(row=>row.text_truncated),model_calls:0,owner_started_by_query:false,owner_observation:'SAVED_SNAPSHOT_ONLY_NO_SDK_REFRESH'};
 }
 function creation(v,key){check(v?.version===1&&v.operation_id===key&&v.project_id===s.project_id&&v.request_sha256===digest({operation_id:key,project_id:s.project_id,title:null})&&v.execution_started===false&&v.messages_created===0&&v.model_calls===0&&v.file_write_authorized===false&&v.automatic_input_authorized===false);return {kind:'CANONICAL_CREATION',operation_id:key,request_sha256:v.request_sha256,project_id:s.project_id,conversation_ref:conversation(v.conversation_ref),execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};}
 if(method==='conversation.create'||method==='conversation.creation.operation'){
  keys(p,['operation_id']);const key=id(p.operation_id);let value;
  if(method==='conversation.create'){try{value=await post('/api/conversations',{operation_id:key,project_id:s.project_id,title:null});}catch{value=await get('/api/conversation-creations/'+encodeURIComponent(key));}}
  else value=await get('/api/conversation-creations/'+encodeURIComponent(key));return creation(value,key);
 }
 if(method==='conversation.message'||method==='conversation.operation'){
  let body,expected,key;
  if(method==='conversation.message'){check(s.conversation_ref.revision>=1);body=messageBody(s,p);key=body.operation_id;expected=digest(body);const value=await current(),target=await checkedOwner(value);if(body.original_source)check(target.source&&target.source.project_id===body.original_source.project_id&&same(target.source.session_ref,body.original_source.session_ref));}
  else{keys(p,['operation_id','request_sha256']);key=id(p.operation_id);expected=hash(p.request_sha256);}
  let value,lookedUp=false;if(body){try{value=await post(base+'/messages',body);}catch{lookedUp=true;value=await get(base+'/messages/'+encodeURIComponent(key));}}else value=await get(base+'/messages/'+encodeURIComponent(key));
  try{return messageReceipt(value,s,key,expected,body);}catch(error){if(body?.original_work_ref&&!lookedUp){value=await get(base+'/messages/'+encodeURIComponent(key));return messageReceipt(value,s,key,expected,body);}throw error;}
 }
 if(method==='conversation.connect'||method==='conversation.connection.operation'){
  let body,key,expected;
  if(method==='conversation.connect'){body=connectionBody(s,p);key=body.operation_id;const native={...body};delete native.project_id;delete native.operation_id;expected=digest(native);await checkedOwner(await current());try{await post('/api/agent-sessions/open',body);}catch{/* Only the original operation is inspected below. */}}
  else{keys(p,['operation_id','request_sha256']);key=id(p.operation_id);expected=hash(p.request_sha256);}
  const v=await get('/api/agent-sessions/open-operations/'+encodeURIComponent(key)+(s.project_id===null?'':'?project_id='+encodeURIComponent(s.project_id)));
  check(v?.version===1&&v.operation_id===key&&v.request_sha256===expected&&same(v.conversation_ref,s.conversation_ref)&&v.organization_project_id===s.project_id&&v.owner_started_by_query===false&&v.model_calls_by_query===0&&v.source_changed_by_query===false&&v.automatic_post_replay===false);
  const ready=v.session?.connected_owner===true&&v.session.owner_status==='READY'&&v.session.configuration_current===true&&v.session.observation_is_current===true;
  return {kind:'CANONICAL_CONNECTION',operation_id:key,conversation_ref:s.conversation_ref,request_sha256:expected,conversation_bound:v.conversation_bound===true,owner_ready:ready,status:v.status==='FAILED_NOT_STARTED'?'FAILED_NOT_STARTED':v.conversation_bound===true&&ready?'RETURNED':'UNKNOWN_OUTCOME',owner_started_by_query:false,model_calls_by_query:0,automatic_post_replay:false};
 }
 fail('NOT_SUPPORTED');
}
module.exports={dispatch,messageBody,connectionBody,scope,allowed};
