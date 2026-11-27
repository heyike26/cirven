'use strict';
// Main-owned transport only. No fetch, credentials, model loop or implicit retry.
const crypto = require('node:crypto');
const paths = require('node:path');
const markdown = require('./vendor/marked.cjs');
const {pathToFileURL} = require('node:url');
const toolDetails = require('./ide-tool-details.cjs');
const commandReports = require('./ide-command-test-reports.cjs');
const workRequirements = require('./ide-work-requirements.cjs');
const workTestCases = require('./ide-work-test-cases.cjs');
const workRetests = require('./ide-work-retests.cjs');
const workDeliveries = require('./ide-work-deliveries.cjs');
const workMaintenance = require('./ide-work-maintenance.cjs');
const deliveryExports = require('./ide-delivery-exports.cjs');
const workSources = require('./ide-work-sources.cjs');
const {originalWorkRef,originalWorkStatusText,verifyOriginalWorkInputReceipt}=require('../../plugins/vscode-companion/work-input-wire.cjs');
const contextRecovery=require('./ide-context-recovery.cjs');
const nativeContextRecovery=require('./ide-native-context-recovery.cjs');
const canonicalNativeRecovery=require('./ide-canonical-native-recovery.cjs');
const canonicalConversations=require('./ide-canonical-conversations.cjs');
const METHODS = Object.freeze(['session.work.maintenance.deliveries','session.work.maintenance.delivery','session.work.maintenance.prepare','session.work.maintenance.change','session.work.maintenance.inspect','session.work.maintenance.operation','status','projects.list','sessions.list','session.get','session.create',
  'session.message','session.guidance','session.operation','session.events','session.cancel','session.question.reply','session.question.reject','session.permission.reply','session.diff','session.diff.file','session.tool','session.tool.reports','session.tool.report','session.work.requirements','session.work.requirements.link','session.work.requirements.operation','session.work.cases','session.work.cases.map','session.work.cases.operation','session.work.retests.prepare','session.work.retests.submit','session.work.retests.operation','session.work.retests.continue','session.work.roots','session.work.deliveries.list','session.work.deliveries.tree','session.work.deliveries.export','session.work.deliveries.export.operation','session.work.deliveries.export.cancel','session.work.deliveries.export.reconcile','session.work.deliveries.exports','session.work.deliveries.select','session.work.deliveries.prepare','session.work.deliveries.submit','session.work.deliveries.operation','session.work.deliveries.recovery','session.work.deliveries.continue','session.work.deliveries.retain','session.work.sources.catalog','session.work.sources.review','session.work.sources.decision','session.work.sources.operation','session.child','session.child.message','session.child.diff','session.child.diff.file','session.diff.prepare','session.diff.review','session.diff.review.operation','session.editor.reserve','session.editor.get','session.editor.control','session.editor.control.operation','artifact.open','lifecycle.reveal',
  'conversation.native.recovery.prepare','conversation.native.recovery.command','conversation.native.recovery.operation',
  'native.context.recovery.prepare','native.context.recovery.command','native.context.recovery.operation',
  'native.conversations.list','native.conversation.get','native.message','native.operation','native.guidance','native.control','native.cancel',
  'native.file.read','native.file.open','native.feedback.record','native.feedback.operation','native.rework.preview','native.rework.apply','native.rework.operation',
  'session.editor.file.prepare','session.editor.file.command','session.editor.file.operation',
  'session.editor.observer.prepare','session.editor.observer.command','session.editor.observer.operation','session.editor.observer.get','session.editor.capture.request','session.editor.capture.operation','session.editor.capture.status','session.editor.capture.listen','session.editor.capture.wait','session.editor.observer.recovery.prepare','session.editor.observer.recovery.command','session.editor.observer.recovery.operation','session.context.recovery.prepare','session.context.recovery.command','session.context.recovery.operation','conversation.list','conversation.select','conversation.get','conversation.file.open','conversation.create','conversation.creation.operation','conversation.message','conversation.operation','conversation.connect','conversation.connection.operation','conversation.configuration.prepare','conversation.configure','conversation.configuration.operation','conversation.configuration.continue','conversation.owner.prepare','conversation.owner.select','conversation.owner.operation','conversation.configuration.adoption.prepare','conversation.configuration.adoption.command','conversation.configuration.adoption.operation']);
const ID = /^[A-Za-z0-9_-]{1,100}$/, SHA = /^[a-f0-9]{64}$/;
const REPORT_TOOLS = new Set(['make_report_deck','get_report_artifact','get_report_operation'].map(name=>'deliverable-authoring_'+name));
const PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
const EDITOR_TYPES = new Set('.md .markdown .txt .log .csv .tsv .json .jsonc .js .jsx .ts .tsx .py .pyi .css .scss .html .htm .xml .yaml .yml .toml .ini .cfg .conf .sql .rs .go .c .h .cpp .hpp .java .kt .cs .sh .ps1 .bat .cmd .r .lua .vue .svelte .tex .diff .patch'.split(' '));
const NATIVE_TYPES = new Set('.docx .dotx .xlsx .pptx .pdf .vsdx .xmind .png .jpg .jpeg'.split(' '));
class IdeApiError extends Error {
  constructor(code,message){super(message);this.code=code;this.public=true;}
}
const fail=(code,message)=>{throw new IdeApiError(code,message);};
const object=value=>value&&typeof value==='object'&&!Array.isArray(value)&&[Object.prototype,null].includes(Object.getPrototypeOf(value));
function keys(value,allowed){if(!object(value)||Object.keys(value).some(key=>!allowed.includes(key)))fail('INVALID_PARAMS','参数不属于此方法的明确范围。');return value;}
function id(value){if(typeof value!=='string'||!ID.test(value))fail('INVALID_PARAMS','需要准确的对象或操作引用。');return value;}
function hash(value){if(typeof value!=='string'||!SHA.test(value))fail('INVALID_PARAMS','需要准确的版本哈希。');return value;}
function number(value,min,max){if(!Number.isSafeInteger(value)||value<min||value>max)fail('INVALID_PARAMS','数值超出允许范围。');return value;}
function text(value,max,{empty=false}={}){if(typeof value!=='string'||Array.from(value).length>max||(!empty&&!value.trim()))fail('INVALID_PARAMS','文字为空或超过此方法的长度限制。');return value;}
function ref(value,session=false){keys(value,['id','revision','sha256']);return{id:id(value.id),revision:number(value.revision,1,session?1:2147483647),sha256:hash(value.sha256)};}
function scope(value={},session=false,write=false){keys(value,['project_id','session_ref','generation']);const result={};if(value.project_id!=null)result.project_id=id(value.project_id);if(value.session_ref!=null)result.session_ref=ref(value.session_ref,true);if(value.generation!=null)result.generation=number(value.generation,write?1:0,Number.MAX_SAFE_INTEGER);if(session&&(!result.project_id||!result.session_ref||result.generation==null))fail('EXACT_SCOPE_REQUIRED','请选择准确的项目、原会话及代次。');return result;}
function canonical(value){if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';if(object(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';return JSON.stringify(value);}
const digest=value=>crypto.createHash('sha256').update(canonical(value)).digest('hex');
const same=(a,b)=>a&&b&&a.id===b.id&&a.revision===b.revision&&a.sha256===b.sha256;
const clip=(v,n=200)=>typeof v==='string'?Array.from(v).slice(0,n).join(''):undefined;
function selected(value,names){const result={};for(const name of names)if(['boolean','number'].includes(typeof value?.[name])||value?.[name]===null)result[name]=value[name];else if(typeof value?.[name]==='string')result[name]=clip(value[name],200);return result;}
function sourceRef(value,session=false){try{return ref(value,session);}catch{return undefined;}}
function boundedRows(value,limit,project){const rows=Array.isArray(value)?value:[];return{rows:(limit?rows.slice(-limit):[]).map(project),coverage:{available:rows.length,returned:Math.min(rows.length,limit),truncated:rows.length>limit}};}
function childReference(session,child){if(!ID.test(session.original_session_id??'')||!ID.test(child?.id??'')||child.id===session.original_session_id||child.parentID!==session.original_session_id)return undefined;
  return {id:child.id,root_native_session_id:session.original_session_id,sha256:digest({id:child.id,parentID:child.parentID,directory:child.directory??null})};}
function childRef(value){keys(value,['id','root_native_session_id','sha256']);return {id:id(value.id),root_native_session_id:id(value.root_native_session_id),sha256:hash(value.sha256)};}
function artifactCandidates(value){
  const found=new Map();let unavailable=0;
  for(const message of value.messages.slice(-80)){
    if(message.info?.role!=='assistant'||!ID.test(message.info?.id??'')||!value.original_session_id||message.info.sessionID!==value.original_session_id)continue;
    for(const part of (Array.isArray(message.parts)?message.parts:[]).slice(-80)){
      if(part.type!=='tool'||!REPORT_TOOLS.has(part.tool)||part.state?.status!=='completed'||part.sessionID!==value.original_session_id||part.messageID!==message.info.id||!ID.test(part.id??'')||!ID.test(part.callID??''))continue;
      const output=part.state.output;if(typeof output!=='string'||Buffer.byteLength(output,'utf8')>131072){unavailable++;continue;}
      let result;try{result=JSON.parse(output);}catch{unavailable++;continue;}
      const item=result?.artifact_manifest,reference=result?.artifact_ref;
      if(result?.status!=='SAVED_NOT_VISUALLY_VERIFIED'||result?.producer?.id!=='eeas.deliverable-authoring'||!object(item)||!object(reference)||
        Object.keys(reference).sort().join(',')!=='id,project_id,revision,sha256'||reference.project_id!==value.project_id||reference.revision!==1||!SHA.test(reference.id??'')||!SHA.test(reference.sha256??'')||
        item.id!==reference.id||item.project_id!==reference.project_id||item.revision!==reference.revision||item.sha256!==reference.sha256||item.kind!=='REPORT_PPTX'||item.media_type!==PPTX_MIME||
        item.download_url!=='/api/projects/'+value.project_id+'/report-artifacts/'+item.id+'/download'){unavailable++;continue;}
      found.set(item.id,{manifest:item,source:{message_id:message.info.id,part_id:part.id,call_id:part.callID,tool:part.tool}});
    }
  }
  return {candidates:[...found.values()].slice(-16),available:found.size,unavailable,truncated:found.size>16};
}
function workspaceBinding(value,binding){const expected=value.workspace_ref;return object(expected)&&object(binding)&&binding.project_id===value.project_id&&binding.id===expected.id&&binding.revision===expected.revision&&binding.binding_sha256===expected.binding_sha256&&typeof binding.root==='string'&&binding.root.length<=4096;}
// Hrefs are candidates only. The original message AST and registered workspace
// are reread on every explicit open; no filesystem read occurs while polling.
function scopedFile(href,binding){
  if(typeof href!=='string'||!href||href.length>8192||/[\x00-\x1f\x7f]/.test(href)||/%(?:2f|5c|00)/i.test(href)||/[?#]/.test(href))return null;
  const flavor=/^[A-Za-z]:[\\/]/.test(binding.root)?paths.win32:paths.posix;let local;
  try{
    if(/^file:/i.test(href)){const url=new URL(href);if(url.host||url.search||url.hash)return null;local=decodeURIComponent(url.pathname);if(flavor===paths.win32){if(!/^\/[A-Za-z]:\//.test(local))return null;local=local.slice(1).replace(/\//g,'\\');}}
    else{if(/^(?:[A-Za-z][A-Za-z0-9+.-]*:|[\\/]{2})/.test(href)&&!(/^[A-Za-z]:[\\/]/.test(href)&&flavor===paths.win32))return null;local=decodeURIComponent(href);if(flavor===paths.win32&&flavor.isAbsolute(local)&&!(/^[A-Za-z]:[\\/]/.test(local)))return null;local=flavor.resolve(binding.root,local);}
    if(!flavor.isAbsolute(local)||local.startsWith('\\\\')||/[\x00-\x1f]/.test(local))return null;
    const relative=flavor.relative(binding.root,local).split(flavor.sep).join('/');
    if(!relative||relative.length>1024||relative.startsWith('/')||relative.split('/').some(p=>!p||p==='.'||p==='..'||['.git','.eeas-sdk-state','.eeas-runtime'].includes(p.toLowerCase())||p.startsWith('.eeas-write-'))||/[\\:]/.test(relative))return null;
    const extension=flavor.extname(local).toLowerCase(),target=EDITOR_TYPES.has(extension)?'editor':NATIVE_TYPES.has(extension)?'native':null;
    return target?{path:relative,filename:flavor.basename(local),target}:null;
  }catch{return null;}
}
function markdownLinks(text){
  if(typeof text!=='string'||Array.from(text).length>16000)return[];const links=[];
  try{markdown.walkTokens(markdown.lexer(text),token=>{if(token.type==='link')links.push({href:token.href,explicit:token.raw.startsWith('[')});});}catch{return[];}
  return links;
}
function workspaceFiles(value,binding){
  if(!workspaceBinding(value,binding))return[];const found=new Map();
  for(const message of value.messages.slice(-80)){
    if(!['user','assistant'].includes(message.info?.role)||message.info.sessionID!==value.original_session_id||!ID.test(message.info.id??''))continue;
    for(const part of (Array.isArray(message.parts)?message.parts:[]).slice(-80)){
      if(part.sessionID!==value.original_session_id||part.messageID!==message.info.id||!ID.test(part.id??''))continue;
      const candidates=part.type==='file'?[{href:part.url}]:part.type==='text'&&message.info.role==='assistant'?markdownLinks(part.text).map((link,index)=>({...link,index})).filter(link=>link.explicit):[];
      for(const link of candidates){const parsed=scopedFile(link.href,binding);if(!parsed)continue;
        const source={message_id:message.info.id,part_id:part.id},file_ref={workspace_ref:{id:binding.id,revision:binding.revision,binding_sha256:binding.binding_sha256},path:parsed.path,...source};
        if(link.index!==undefined){source.link_index=link.index;file_ref.link_index=link.index;file_ref.href_sha256=crypto.createHash('sha256').update(link.href).digest('hex');}
        found.set(canonical(source),{kind:'workspace-file',project_id:value.project_id,filename:parsed.filename,file_ref,target:parsed.target,source,
          verification:link.index===undefined?'EXACT_MESSAGE_FILE_URL_AND_WORKSPACE_BINDING':'EXACT_ORIGINAL_MARKDOWN_HREF_AND_WORKSPACE_BINDING',content_basis:'CURRENT_SAVED_BYTES_ON_EXPLICIT_OPEN'});
      }
    }
  }
  return [...found.values()].slice(-16);
}
function projectSession(value,expected,{summary=false}={}){
  if(!object(value)||!sourceRef(value.session_ref,true)||typeof value.project_id!=='string'||!Number.isSafeInteger(value.generation)||!Array.isArray(value.messages))fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');
  if(expected&&(value.project_id!==expected.project_id||!same(value.session_ref,expected.session_ref)||value.generation!==expected.generation))fail('STALE_SESSION_SCOPE','原会话或代次已变化，请重新读取；草稿仍保留。');
  const result=selected(value,['project_id','generation','owner_status','native_status','connected_owner','configuration_current','observation_is_current','active_operation_id','original_session_id','workspace_kind','last_event_seq','observation_at','automatic_post_replay','file_writes']);
  if(value.context_authority_issue!=null){const issue=value.context_authority_issue;
    if(!object(issue)||!['SOURCE_CONTEXT_CHANGED','SOURCE_CONTEXT_UNAVAILABLE','SOURCE_CONTEXT_LEGACY'].includes(issue.code)||issue.generation!==value.generation||issue.scope!=='FUTURE_HOST_ADMISSIONS_AND_API_RELAY'||issue.original_history_retained!==true||issue.operation_id!=null&&!ID.test(issue.operation_id))fail('INVALID_UPSTREAM_RESPONSE','原来源授权状态不完整。');
    result.context_authority_issue={code:issue.code,generation:issue.generation,operation_id:issue.operation_id??null,scope:issue.scope,original_history_retained:true};
  }
  result.workflow_mode_supported=value.workflow_mode_supported===true;
  result.available_agent_modes=['plan','build',...(result.workflow_mode_supported&&Array.isArray(value.available_agent_modes)&&value.available_agent_modes.includes('workflow')?['workflow']:[])];
  const mode=value.work_mode_message;
  if(object(mode)&&mode.operation_id===value.active_operation_id&&mode.generation===value.generation&&['plan','build','workflow'].includes(mode.agent))result.work_mode_message={operation_id:id(mode.operation_id),generation:mode.generation,agent:mode.agent};
  result.session_ref=ref(value.session_ref,true);if(sourceRef(value.provider_profile_ref))result.provider_profile_ref=ref(value.provider_profile_ref);
  result.title=clip(value.title??value.original_session?.title,200);
  const childOperation=value.active_child_operation;
  if(object(childOperation)&&childOperation.operation_id===value.active_operation_id&&childOperation.generation===value.generation){
    try{const reference=childRef(childOperation.child_ref);if(reference.root_native_session_id!==value.original_session_id)throw Error('Wrong root');
      result.active_child_operation={...selected(childOperation,['operation_id','generation','status','guidance_operation_id','guidance_status']),child_ref:reference};
    }catch{fail('CHILD_CHANGED','原子任务控制身份已变化。');}}
  if(object(value.child_operation_observation))result.child_operation_observation=selected(value.child_operation_observation,['operation_id','status','original_session_id','observed_at']);
  result.child_guidance_targets=[];
  if(value.child_guidance_targets!==undefined){
    if(!Array.isArray(value.child_guidance_targets)||value.child_guidance_targets.length>32)fail('CHILD_CHANGED','原子任务指导范围无效。');
    for(const target of value.child_guidance_targets){const reference=childRef(target.child_ref),matches=(value.children||[]).filter(child=>child.id===reference.id);
      if(target.parent_operation_id!==value.active_operation_id||target.generation!==value.generation||target.status!=='RUNNING_FOREGROUND'||
        matches.length!==1||canonical(childReference(value,matches[0]))!==canonical(reference)||typeof target.agent!=='string'||target.agent.length>100)continue;
      result.child_guidance_targets.push({parent_operation_id:target.parent_operation_id,generation:target.generation,child_ref:reference,status:target.status,agent:target.agent});
    }
  }
  if(value.workspace_ref&&object(value.workspace_ref))result.workspace_ref=selected(value.workspace_ref,['id','revision','binding_sha256']);
  let remaining=64000,textTruncated=false;const raw=summary?[]:value.messages.slice(-80);
  result.messages=[...raw].reverse().map(message=>{
    const info=selected(message.info,['id','role','sessionID','parentID','agent','finish','providerID','modelID','variant']);
    if(object(message.info?.model))info.model=selected(message.info.model,['providerID','modelID']);
    if(object(message.info?.time))info.time=selected(message.info.time,['created','completed']);
    if(message.info?.error)info.error={name:clip(message.info.error.name,100)??'ORIGINAL_ERROR'};
    const allParts=Array.isArray(message.parts)?message.parts:[],parts=allParts.slice(-80).flatMap(part=>{
      const base={id:clip(part.id,100),type:clip(part.type,40)};
      if(part.type==='text'&&typeof part.text==='string'){
        const original=Array.from(part.text),count=Math.min(original.length,16000,remaining);remaining-=count;
        if(count<original.length)textTruncated=true;return [{...base,text:original.slice(0,count).join(''),text_truncated:count<original.length}];
      }
      if(part.type==='tool')return[{...base,tool:clip(part.tool,200),callID:clip(part.callID,100),state:{status:clip(part.state?.status,40)},tool_ref:toolDetails.reference(value,message,part)}];
      if(part.type==='file')return[{...base,mime:clip(part.mime,100),filename:clip(part.filename,200)}];
      return[];
    });return {info,parts,parts_coverage:{available:allParts.length,returned:parts.length,truncated:allParts.length>80,safe_types_only:true}};
  }).reverse();
  const pending=require('./ide-pending-controls.cjs').projectPending;
  const fields={todo:[100,row=>({...selected(row,['id','status','priority']),content:clip(row.content,2000)})],
    children:[100,row=>({...selected(row,['id','parentID']),title:clip(row.title,300),child_ref:childReference(value,row)})],
    guidance:[64,row=>selected(row,['operation_id','status','role','generation','original_active_operation_id','native_user_observed','individual_response_observed','response_evidence','linked_latest_operation_id','cohort_response_status'])],
    pending_questions:[64,pending],pending_permissions:[64,pending]};
  const coverage={bounded:true,messages:{available:value.messages.length,returned:raw.length,truncated:summary?value.messages.length>0:value.messages.length>80,text_truncated:textTruncated,summary_only:summary},tool_payloads:'OMITTED_EXPLICIT_EXACT_SESSION_TOOL_READ',events_generation:'SESSION_HISTORY_NOT_GENERATION_FILTERED',pending_replies:'EXACT_ORIGINAL_REQUEST_REF_RECHECKED'};
  for(const [name,[limit,project]]of Object.entries(fields)){const section=boundedRows(value[name],summary?0:limit,project);result[name]=summary?[]:section.rows;coverage[name]={...section.coverage,returned:summary?0:section.rows.length,summary_only:summary};}
  result.observation_coverage=typeof value.observation_coverage==='string'?clip(value.observation_coverage,600):'SOURCE_DECLARATION_NOT_PROJECTED';
  result.ide_coverage=coverage;result.artifact_channel='NOT_CONNECTED';result.artifacts=[];result.child_continuation='EXPLICIT_EXISTING_OWNER_WITH_ROOT_BARRIER';result.child_write_review='ORIGINAL_ROOT_GRANT_EXACT_CHILD_LEDGER';
  return result;
}
function projectOperation(value,owner,key,expected){
  if(!object(value)||value.id!==key||!SHA.test(value.request_sha256??'')||value.automatic_replay!==false||!same(value.session_ref,owner.session_ref))fail('INVALID_OPERATION_RECEIPT','原操作回执不匹配；没有重新发送。');
  const generation=value.request?.generation??value.generation;
  if(generation!==owner.generation)fail('STALE_SESSION_SCOPE','原操作属于另一个代次，没有采用或重新发送。');
  if(expected&&(value.request_sha256!==digest(expected.body)||value.kind!==expected.kind))fail('OPERATION_INPUT_MISMATCH','原操作绑定了其他输入，不能作为这次请求的回执。');
  const work=expected?.body?.original_work_ref??value.request?.original_work_ref;
  if(work){try{const input=expected?.body??value.request;verifyOriginalWorkInputReceipt(value,owner,originalWorkRef(work),input.raw_text,input.intent!=='STEER'&&originalWorkStatusText(input.raw_text));}catch{fail('INVALID_OPERATION_RECEIPT','原工作状态的身份或执行范围不匹配。');}}
  const result=selected(value,['id','kind','status','request_sha256','dispatch_status','created_at','updated_at','generation','native_message_id','original_session_id','root_original_session_id','automatic_replay','role','original_active_operation_id','target_operation_id','target_native_session_id','control_scope','original_abort_acknowledged','original_message_stop_confirmed','external_actions_rolled_back','native_user_observed','individual_response_observed','response_evidence','linked_latest_operation_id','cohort_response_status','semantic_acceptance','independent_verification','model_calls','native_post_count','native_post_attempted','native_dispatch_acknowledged']);
  if(value.child_ref!==undefined)result.child_ref=childRef(value.child_ref);
  if(['CHILD_MESSAGE','CHILD_GUIDANCE'].includes(value.kind))result.generation=generation;
  result.session_ref=ref(value.session_ref,true);result.operation_ref={id:key,request_sha256:value.request_sha256};
  if(object(value.local_status))result.local_status=selected(value.local_status,['owner_status','native_status','active_operation_id','owner_active_operation_id','operation_status','pending_questions','pending_permissions','observation_at','coverage']);
  if(value.local_status?.original_work!==undefined){
    const w=value.local_status.original_work,source=expected?.body?.original_work_ref??value.request?.original_work_ref;
    keys(w,['reference','status','root_status','active_operation_id','owner_active_operation_id','active_relation','root_generation','coverage','model_calls','native_posts']);
    if(!source||canonical(originalWorkRef(w.reference))!==canonical(originalWorkRef(source))||w.model_calls!==0||w.native_posts!==0||value.role!=='LOCAL_STATUS'||value.model_calls!==0||value.native_post_count!==0||w.coverage!=='LAST_SAVED_ORIGINAL_ROOT_STATUS_NOT_SEMANTIC_ACCEPTANCE'||w.active_operation_id!==value.local_status.active_operation_id)fail('INVALID_OPERATION_RECEIPT','原工作状态的身份或执行范围不匹配。');
    result.local_status.original_work={reference:originalWorkRef(w.reference),status:text(w.status,100),root_status:text(w.root_status,100),active_operation_id:w.active_operation_id==null?null:id(w.active_operation_id),root_generation:number(w.root_generation,1,owner.generation),coverage:w.coverage,model_calls:0,native_posts:0};
    if(w.active_relation!==undefined)Object.assign(result.local_status.original_work,{active_relation:w.active_relation,owner_active_operation_id:w.owner_active_operation_id});
  }
  if(Array.isArray(value.native_assistant_ids))result.native_assistant_ids=value.native_assistant_ids.slice(0,200).map(v=>clip(v,100));
  if(Array.isArray(value.continuation_guidance_ids))result.continuation_guidance_ids=value.continuation_guidance_ids.slice(0,32).map(v=>clip(v,100));
  result.independent_verification=value.independent_verification??'NOT_RUN';result.automatic_replay=false;return result;
}
function diffWorkspace(value){
  keys(value,['id','revision','binding_sha256']);return{id:hash(value.id),revision:number(value.revision,1,2147483647),binding_sha256:hash(value.binding_sha256)};
}
function diffReference(value){keys(value,['id','revision','sha256']);return{id:hash(value.id),revision:number(value.revision,1,1),sha256:hash(value.sha256)};}
function diffProjection(value,owner,workspace,{snapshot,diff,file=false,child=null}={}){
  const expectedScope={...owner,workspace_ref:workspace},expectedId=child?digest({scope:expectedScope,child_ref:child}):digest(expectedScope);
  if(child?canonical(value?.child_ref)!==canonical(child):value?.child_ref!==undefined)fail('CHILD_CHANGED','原子任务身份已变化，请刷新。');
  if(!object(value)||canonical(value.scope)!==canonical({...owner,workspace_ref:workspace})||value.read_only!==true||value.model_calls!==0||value.current_disk_compared!==false||value.editor_dirty_state!=='NOT_OBSERVED')fail('STALE_SESSION_SCOPE','原会话或代次已变化，请重新读取；草稿仍保留。');
  const snapshot_ref=diffReference(value.snapshot_ref);
  if(snapshot_ref.id!==expectedId||(snapshot&&canonical(snapshot)!==canonical(snapshot_ref)))fail('DIFF_CHANGED','文件已更新，请刷新');
  function row(item){
    if(!object(item)||typeof item.path!=='string'||!item.path||item.path.length>1024||/[\\:\x00-\x1f\x7f]/.test(item.path)||item.path.startsWith('/')||item.path.split('/').some(p=>!p||p==='.'||p==='..'||['.git','.eeas-runtime','.eeas-sdk-state'].includes(p.toLowerCase()))||!['UNKNOWN','added','deleted','modified'].includes(item.status)||!['AVAILABLE','UNAVAILABLE','BINARY','TOO_LARGE'].includes(item.availability)||!['HUNK_EXCERPTS','FULL_FILE','UNAVAILABLE'].includes(item.content_scope)||!['ORIGINAL_TOOL_FILEDIFF','ORIGINAL_MESSAGE_SUMMARY','ORIGINAL_TOOL_METADATA','AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS'].includes(item.source))fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');
    const result={diff_ref:diffReference(item.diff_ref),path:item.path,status:item.status,source:item.source,content_scope:item.content_scope,availability:item.availability};
    for(const field of ['message_id','part_id','call_id','native_operation_id'])if(item[field]!=null)result[field]=id(item[field]);
    for(const field of ['additions','deletions','summary_index'])if(item[field]!=null)result[field]=number(item[field],0,10000000);
    if(item.reason_code!=null){if(typeof item.reason_code!=='string'||!(/^[A-Z_]{1,100}$/).test(item.reason_code))fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');result.reason_code=item.reason_code;}else result.reason_code=null;
    if(item.availability==='AVAILABLE'&&!['HUNK_EXCERPTS','FULL_FILE'].includes(item.content_scope))fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');
    if(item.content_scope==='FULL_FILE'){
      keys(item.source_scope,['project_id','session_ref','generation','workspace_ref']);
      const generation=number(item.source_generation,1,owner.generation),source=scope({project_id:item.source_scope.project_id,session_ref:item.source_scope.session_ref,generation:item.source_scope.generation},true);
      if(generation!==source.generation||source.project_id!==owner.project_id||!same(source.session_ref,owner.session_ref)||canonical(item.source_scope.workspace_ref)!==canonical(workspace))fail('STALE_SESSION_SCOPE','原会话或代次已变化，请重新读取；草稿仍保留。');
      result.source_generation=generation;result.source_scope={...source,workspace_ref:workspace};
      if(child){if(item.source_native_session_id!==child.id)fail('CHILD_CHANGED','原子任务身份已变化，请刷新。');result.source_native_session_id=child.id;result.child_lineage_ref=diffReference(item.child_lineage_ref);}
      else if(item.source_native_session_id!==undefined||item.child_lineage_ref!==undefined)fail('CHILD_CHANGED','父会话不能借用子任务写入记录。');
    }
    else if(item.content_scope==='HUNK_EXCERPTS'&&(item.source_generation!=null||item.source_scope!=null))fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');
    return result;
  }
  const shared={scope:expectedScope,snapshot_ref,...(child?{child_ref:child}:{}),read_only:true,model_calls:0,current_disk_compared:false,editor_dirty_state:'NOT_OBSERVED',file_hashes:value.file_hashes??'NOT_OBSERVED'};
  if(!file){
    if(!Array.isArray(value.files)||value.files.length>128||!object(value.coverage)||value.coverage.source!=='SAVED_ORIGINAL_ROOT_MESSAGE_OBSERVATION'||value.coverage.complete!==false||!Array.isArray(value.coverage.reason_codes)||value.coverage.reason_codes.some(code=>typeof code!=='string'||!(/^[A-Z_]{1,100}$/).test(code)))fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');
    const coverage={...selected(value.coverage,['source','complete','messages_returned','messages_limit','historical_generations','available','returned','unavailable','excluded_paths','truncated','snapshot_enabled','commands_and_children','rename_detection']),reason_codes:[...value.coverage.reason_codes]};
    if(child){const c=value.coverage.child_observation;if(!object(c)||c.history_complete!==false)fail('INVALID_UPSTREAM_RESPONSE','原子任务覆盖记录不完整。');coverage.child_observation={...selected(c,['source','partial','history_complete','messages_limit','messages_returned','text_coverage'])};}
    return {...shared,files:value.files.map(row),coverage,observation_at:typeof value.observation_at==='string'&&value.observation_at.length<=100?value.observation_at:null,content_scope:value.content_scope};
  }
  const result={...shared,...row(value)};
  if(canonical(diff)!==canonical(result.diff_ref)||result.availability!=='AVAILABLE'||typeof value.before!=='string'||typeof value.after!=='string')fail('DIFF_CHANGED','文件已更新，请刷新');
  if(result.content_scope==='FULL_FILE'){
    if(value.composition!=='EXACT_SAVED_BEFORE_AFTER'||value.source!=='AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS'||value.encoding!=='UTF-8'||(value.status==='added'&&value.before!=='')||(value.status==='deleted'&&value.after!=='')||crypto.createHash('sha256').update(value.before,'utf8').digest('hex')!==hash(value.before_sha256)||crypto.createHash('sha256').update(value.after,'utf8').digest('hex')!==hash(value.after_sha256))fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');
    return {...result,composition:value.composition,encoding:'UTF-8',before:value.before,after:value.after,before_sha256:value.before_sha256,after_sha256:value.after_sha256};
  }
  if(value.composition!=='LOCATION_MARKED_HUNK_EXCERPTS'||typeof value.patch!=='string'||Buffer.byteLength(value.patch,'utf8')>131072||!Array.isArray(value.hunks)||!value.hunks.length||value.hunks.length>128)fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');
  const hunks=value.hunks.map(h=>{if(!object(h)||typeof h.before!=='string'||typeof h.after!=='string')fail('INVALID_UPSTREAM_RESPONSE','原会话回执不完整，未采用其他会话。');return{old_start:number(h.old_start,0,10000000),old_lines:number(h.old_lines,0,10000000),new_start:number(h.new_start,0,10000000),new_lines:number(h.new_lines,0,10000000),before:h.before,after:h.after};});
  return {...result,composition:value.composition,before:value.before,after:value.after,patch:value.patch,hunks};
}
function reviewSelection(p,s){return {session_ref:s.session_ref,generation:number(s.generation,1,2147483647),workspace_ref:diffWorkspace(p.workspace_ref),snapshot_ref:diffReference(p.snapshot_ref),diff_ref:diffReference(p.diff_ref),child_ref:p.child_ref==null?null:childRef(p.child_ref)};}
function reviewKey(s,v){return digest({project_id:s.project_id,session_ref:s.session_ref,generation:s.generation,workspace_ref:v.workspace_ref,child_ref:v.child_ref,diff_ref:v.diff_ref});}
function savedBasis(v){keys(v,['exists','sha256','byte_length']);if(typeof v.exists!=='boolean'||!Number.isSafeInteger(v.byte_length)||v.byte_length<0||v.byte_length>524288||v.exists&&!SHA.test(v.sha256??'')||!v.exists&&(v.sha256!==null||v.byte_length!==0))fail('INVALID_UPSTREAM_RESPONSE','保存版本回执不完整。');return {exists:v.exists,sha256:v.sha256,byte_length:v.byte_length};}
function reviewPreparation(value,s,selection){
  if(!object(value)||value.kind!=='DIFF_REVIEW_PREPARATION'||canonical(value.scope)!==canonical({...s,workspace_ref:selection.workspace_ref})||canonical(value.child_ref)!==canonical(selection.child_ref)||value.read_only!==true||value.model_calls!==0||value.native_posts!==0||value.workspace_writes!==0||value.editor_dirty_state!=='NOT_OBSERVED'||value.editor_barrier!=='NOT_ACQUIRED'||value.effect_scope!=='HISTORICAL_USER_REVIEW_ONLY_NOT_FILE_APPLICATION_OR_ROLLBACK')fail('INVALID_UPSTREAM_RESPONSE','原审阅准备回执不完整。');
  const d=diffProjection(value.diff,s,selection.workspace_ref,{snapshot:selection.snapshot_ref,diff:selection.diff_ref,file:true,child:selection.child_ref}),disk=savedBasis(value.current_saved),revision=number(value.review_revision,0,1024);
  if(d.content_scope!=='FULL_FILE')fail('DIFF_UNAVAILABLE','需要完整的原修改前后版本。');
  const basis={selection,path:d.path,disk,review_revision:revision,before_sha256:d.before_sha256,after_sha256:d.after_sha256,before_exists:d.status!=='added',after_exists:d.status!=='deleted'};
  if(digest(basis)!==value.preparation_sha256||value.matches_before!==(disk.exists===basis.before_exists&&(!basis.before_exists||disk.sha256===d.before_sha256))||value.matches_after!==(disk.exists===basis.after_exists&&(!basis.after_exists||disk.sha256===d.after_sha256)))fail('DIFF_CHANGED','审阅的保存版本已变化。');
  let review=null;if(revision){if(!object(value.review)||value.review.revision!==revision||!['ACCEPT','REJECT'].includes(value.review.decision))fail('INVALID_UPSTREAM_RESPONSE','原审阅记录不完整。');review={revision,decision:value.review.decision,operation_id:id(value.review.operation_id),review_ref:ref(value.review.review_ref)};if(review.review_ref.revision!==revision||review.review_ref.id!==reviewKey(s,selection))fail('INVALID_UPSTREAM_RESPONSE','原审阅版本不一致。');}else if(value.review!==null)fail('INVALID_UPSTREAM_RESPONSE','原审阅版本不一致。');
  return {kind:value.kind,scope:value.scope,child_ref:selection.child_ref,preparation_sha256:hash(value.preparation_sha256),review_revision:revision,review,current_saved:disk,matches_before:value.matches_before,matches_after:value.matches_after,diff:d,read_only:true,model_calls:0,native_posts:0,workspace_writes:0,editor_dirty_state:'NOT_OBSERVED',editor_barrier:'NOT_ACQUIRED',effect_scope:value.effect_scope};
}
function reviewReceipt(value,s,operationId,expected=null){
  if(!object(value)||value.kind!=='DIFF_REVIEW'||value.id!==operationId||value.automatic_replay!==false||!same(value.session_ref,s.session_ref)||!object(value.request))fail('INVALID_OPERATION_RECEIPT','原审阅操作回执不匹配。');
  const raw=value.request;keys(raw,['session_ref','generation','workspace_ref','snapshot_ref','diff_ref','child_ref','operation_id','preparation_sha256','expected_review_revision','decision']);
  const body={...reviewSelection(raw,s),operation_id:id(raw.operation_id),preparation_sha256:hash(raw.preparation_sha256),expected_review_revision:number(raw.expected_review_revision,0,1024),decision:raw.decision};
  if(raw.generation!==s.generation||!same(raw.session_ref,s.session_ref)||!['ACCEPT','REJECT'].includes(body.decision)||body.operation_id!==operationId||value.request_sha256!==digest(body)||expected&&canonical(expected)!==canonical(body))fail('OPERATION_INPUT_MISMATCH','原审阅请求引用不符，未重新提交。');
  if(!['RETURNED','FAILED','UNKNOWN_OUTCOME','RECORDED_NOT_DISPATCHED'].includes(value.status))fail('INVALID_OPERATION_RECEIPT','原审阅状态不完整。');
  const out={id:operationId,kind:value.kind,session_ref:s.session_ref,generation:s.generation,request_sha256:value.request_sha256,status:value.status,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0};
  if(value.status==='RETURNED'){
    const selection=reviewSelection(body,s),review=ref(value.review_ref);
    if(value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.review_decision!==body.decision||canonical(value.review_selection)!==canonical(selection)||value.preparation_sha256!==body.preparation_sha256||review.revision!==body.expected_review_revision+1||review.id!==reviewKey(s,body)||value.effect_scope!=='HISTORICAL_USER_REVIEW_ONLY_NOT_FILE_APPLICATION_OR_ROLLBACK')fail('INVALID_OPERATION_RECEIPT','原审阅结果引用不符。');
    Object.assign(out,{review_ref:review,review_decision:value.review_decision,review_selection:selection,preparation_sha256:body.preparation_sha256,current_saved:savedBasis(value.current_saved),effect_scope:value.effect_scope,semantic_acceptance:false,independent_verification:'NOT_RUN'});
  }
  return out;
}
function editorActor(context){if(!ID.test(context?.client_id??''))fail('TRUSTED_CLIENT_REQUIRED','需要原配对编辑器连接。');return context.client_id;}
function editorSelection(p,s,context){return {...reviewSelection(p,s),operation_id:id(p.operation_id),preparation_sha256:hash(p.preparation_sha256),direction:p.direction,editor_client_id:editorActor(context),editor_window_id:id(p.editor_window_id),editor_nonce:hash(p.editor_nonce)};}
function editorProjection(value,s,context,{operation,expected=null}={}){
  if(!object(value)||value.kind!=='EDITOR_CHANGE'||value.id!==operation||!same(value.session_ref,s.session_ref)||value.generation!==s.generation||value.editor_client_id!==editorActor(context)||!ID.test(value.editor_window_id??'')||!SHA.test(value.request_sha256??'')||value.automatic_replay!==false||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||!['RESERVING','READY','ARMED','BUFFER_CHANGED','SAVED','CANCELLED','MANUAL_SAVED','FAILED','UNKNOWN_OUTCOME'].includes(value.phase))fail('EDITOR_CHANGE_MISMATCH','原编辑交接引用不符。');
  if(expected&&value.request_sha256!==digest(expected))fail('OPERATION_INPUT_MISMATCH','原编辑请求引用不符。');
  const out={kind:value.kind,id:operation,session_ref:s.session_ref,generation:s.generation,request_sha256:value.request_sha256,editor_client_id:value.editor_client_id,editor_window_id:value.editor_window_id,phase:value.phase,revision:number(value.revision,1,64),lease_ref:null,grant:null,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,first_reservation_reply:value.first_reservation_reply===true};
  if(value.grant!==null){const g=value.grant;keys(g,['scope','selection','direction','editor_client_id','editor_window_id','editor_nonce','path','source_ref','file_location','saved_basis','target_exists','target_sha256','source_editor_sha256','target_editor_sha256','source_has_bom','target_has_bom','mode']);
    const selection=reviewSelection(g.selection??{},s),workspace=selection.workspace_ref;
    if(canonical(g.selection)!==canonical(selection)||canonical(g.scope)!==canonical({...s,workspace_ref:workspace})||g.editor_client_id!==value.editor_client_id||g.editor_window_id!==value.editor_window_id||!['RESTORE_BEFORE','APPLY_AFTER'].includes(g.direction)||!['TEXT_REPLACE','FILE_CREATE','FILE_DELETE'].includes(g.mode)||typeof g.path!=='string'||!g.path||g.path.length>1024||/[\\:\x00-\x1f\x7f]/.test(g.path)||g.path.startsWith('/')||g.path.split('/').some(part=>!part||part==='.'||part==='..'||['.git','.eeas-runtime','.eeas-sdk-state'].includes(part.toLowerCase()))||typeof g.target_exists!=='boolean'||typeof g.source_has_bom!=='boolean'||typeof g.target_has_bom!=='boolean')fail('EDITOR_CHANGE_MISMATCH','原编辑授权来源不符。');
    hash(g.editor_nonce);hash(g.source_editor_sha256);hash(g.target_editor_sha256);const disk=savedBasis(g.saved_basis);
    if(g.target_exists)hash(g.target_sha256);else if(g.target_sha256!==null)fail('EDITOR_CHANGE_MISMATCH','原目标状态不符。');
    if((g.mode==='TEXT_REPLACE')!==(disk.exists&&g.target_exists)||(g.mode==='FILE_CREATE')!==(!disk.exists&&g.target_exists)||(g.mode==='FILE_DELETE')!==(disk.exists&&!g.target_exists))fail('EDITOR_CHANGE_MISMATCH','原文件生命周期不符。');
    if(disk.exists){const source=nativeFile(g.source_ref,s.project_id);if(source.workspace_id!==workspace.id||source.binding_revision!==workspace.revision||source.binding_sha256!==workspace.binding_sha256||source.path!==g.path||source.sha256!==disk.sha256)fail('EDITOR_CHANGE_MISMATCH','原编辑文件版本不符。');}else if(g.source_ref!==null)fail('EDITOR_CHANGE_MISMATCH','原文件缺失状态不符。');
    const lease=diffReference(value.lease_ref);if(lease.sha256!==digest(g)||lease.id!==digest({project_id:s.project_id,session_id:s.session_ref.id,operation_id:operation}))fail('EDITOR_CHANGE_MISMATCH','原编辑授权摘要不符。');
    if(expected&&(g.editor_window_id!==expected.editor_window_id||g.editor_nonce!==expected.editor_nonce||g.direction!==expected.direction||canonical(selection)!==canonical(reviewSelection(expected,s))))fail('OPERATION_INPUT_MISMATCH','原编辑请求与授权来源不符。');
    out.lease_ref=lease;out.grant=g;
  }else if(value.lease_ref!==null||!['RESERVING','FAILED','CANCELLED'].includes(value.phase))fail('EDITOR_CHANGE_MISMATCH','原编辑授权尚未发布。');
  return out;
}
function editorControlBody(p,s,context){const actor=editorActor(context),basis=p.editor_basis??null;
  if(basis!==null){keys(basis,['document_version','content_sha256','is_dirty']);number(basis.document_version,1,2147483647);hash(basis.content_sha256);if(typeof basis.is_dirty!=='boolean')fail('INVALID_PARAMS','需要原编辑器保存状态。');}
  if(!['ARM','BUFFER_CHANGED','RESOLVE_SAVED','RESOLVE_CANCELLED','RESOLVE_MANUAL_SAVED'].includes(p.action)||p.user_approved_manual_saved!==undefined&&typeof p.user_approved_manual_saved!=='boolean')fail('INVALID_PARAMS','请选择明确的原编辑操作。');
  return {operation_id:id(p.operation_id),session_ref:s.session_ref,generation:number(s.generation,1,2147483647),target_operation_id:id(p.target_operation_id),lease_ref:p.lease_ref==null?null:diffReference(p.lease_ref),editor_client_id:actor,editor_window_id:id(p.editor_window_id),editor_nonce:hash(p.editor_nonce),expected_revision:number(p.expected_revision,1,64),action:p.action,editor_basis:basis,expected_saved_sha256:p.expected_saved_sha256==null?null:hash(p.expected_saved_sha256),user_approved_manual_saved:p.user_approved_manual_saved??false};}
function editorControlReceipt(value,s,context,operation,expected=null){
  if(!object(value)||value.kind!=='EDITOR_CHANGE_CONTROL'||value.id!==operation||!same(value.session_ref,s.session_ref)||value.automatic_replay!==false||!object(value.request))fail('EDITOR_CHANGE_MISMATCH','原编辑控制回执不符。');
  const raw=value.request;keys(raw,['operation_id','session_ref','generation','target_operation_id','lease_ref','editor_client_id','editor_window_id','editor_nonce','expected_revision','action','editor_basis','expected_saved_sha256','user_approved_manual_saved']);const body=editorControlBody(raw,s,context);if(raw.editor_client_id!==editorActor(context)||raw.generation!==s.generation||!same(raw.session_ref,s.session_ref)||body.operation_id!==operation||value.request_sha256!==digest(body)||expected&&canonical(expected)!==canonical(body))fail('OPERATION_INPUT_MISMATCH','原编辑控制输入不符。');
  if(!['RETURNED','FAILED','UNKNOWN_OUTCOME','RECORDED_NOT_DISPATCHED'].includes(value.status))fail('EDITOR_CHANGE_MISMATCH','原编辑控制状态不完整。');
  const out={id:operation,kind:value.kind,session_ref:s.session_ref,generation:s.generation,status:value.status,request_sha256:value.request_sha256,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,first_control_reply:false};
  if(value.status==='RETURNED'){out.editor_change=editorProjection(value.editor_change,s,context,{operation:body.target_operation_id});if(out.editor_change.revision!==body.expected_revision+1||canonical(out.editor_change.lease_ref)!==canonical(body.lease_ref)||out.editor_change.editor_window_id!==body.editor_window_id)fail('EDITOR_CHANGE_MISMATCH','原编辑控制版本不符。');
    const phase={ARM:'ARMED',BUFFER_CHANGED:'BUFFER_CHANGED',RESOLVE_SAVED:'SAVED',RESOLVE_CANCELLED:'CANCELLED',RESOLVE_MANUAL_SAVED:'MANUAL_SAVED'}[body.action];if(out.editor_change.phase!==phase)fail('EDITOR_CHANGE_MISMATCH','原编辑控制结果不符。');out.first_control_reply=value.first_control_reply===true;}
  return out;
}
function editorFileAddress(p,s,context){
  if(!['COMMIT','UNDO','CONFIRM','MANUAL_RESOLVE'].includes(p.action))fail('INVALID_PARAMS','请选择明确的文件操作。');
  return {session_ref:s.session_ref,generation:number(s.generation,1,2147483647),target_operation_id:id(p.target_operation_id),lease_ref:diffReference(p.lease_ref),editor_client_id:editorActor(context),editor_window_id:id(p.editor_window_id),editor_nonce:hash(p.editor_nonce),expected_revision:number(p.expected_revision,1,64),action:p.action};
}
function editorFileCommandBody(p,s,context){
  const observation=p.editor_observation;keys(observation,['documents','native_commands_settled']);
  if(!Array.isArray(observation.documents)||observation.documents.length>16||typeof observation.native_commands_settled!=='boolean'||typeof p.user_approved!=='boolean')fail('INVALID_PARAMS','需要原编辑器的文件状态。');
  const documents=observation.documents.map(row=>{keys(row,['document_version','content_sha256','is_dirty']);if(typeof row.is_dirty!=='boolean')fail('INVALID_PARAMS','需要原编辑器保存状态。');return {document_version:number(row.document_version,1,2147483647),content_sha256:hash(row.content_sha256),is_dirty:row.is_dirty};});
  return {...editorFileAddress(p,s,context),operation_id:id(p.operation_id),preparation_sha256:hash(p.preparation_sha256),editor_observation:{documents,native_commands_settled:observation.native_commands_settled},user_approved:p.user_approved};
}
function editorFilePreparation(value,s,body,lease){
  if(!object(value)||value.kind!=='EDITOR_FILE_PREPARATION'||!same(value.scope,lease.grant?.scope)||!same(value.lease_ref,body.lease_ref)||value.target_operation_id!==body.target_operation_id||value.revision!==body.expected_revision||value.action!==body.action||value.editor_client_id!==body.editor_client_id||value.editor_window_id!==body.editor_window_id||value.read_only!==true||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.automatic_replay!==false)fail('EDITOR_CHANGE_MISMATCH','原文件准备身份不符。');
  const snapshot=value.snapshot;keys(snapshot,['availability','saved','location','editor_text_sha256','workspace_binding_sha256','unavailable_reason','metadata_sha256','file_identity','metadata_recovery_version']);
  if(body.action!=='MANUAL_RESOLVE'&&snapshot.metadata_recovery_version!==1)fail('EDITOR_CAPABILITY_UNAVAILABLE','原文件恢复接口版本需要更新；未执行文件操作。');
  if(snapshot.workspace_binding_sha256!==lease.grant.scope.workspace_ref.binding_sha256||!['OBSERVED','UNAVAILABLE'].includes(snapshot.availability))fail('EDITOR_CHANGE_MISMATCH','原文件观察范围不符。');
  if(snapshot.availability==='OBSERVED'){
    savedBasis(snapshot.saved);const location=snapshot.location,parent=location?.parent_ref,workspace=lease.grant.scope.workspace_ref;
    if(!object(location)||location.kind!=='WORKSPACE_FILE_SLOT'||location.project_id!==s.project_id||typeof location.path!=='string'||location.path.length>1024||!object(parent)||parent.project_id!==s.project_id||parent.workspace_id!==workspace.id||parent.binding_revision!==workspace.revision||parent.binding_sha256!==workspace.binding_sha256||!/^\d{1,32}$/.test(parent.device??'')||!/^\d{1,32}$/.test(parent.inode??''))fail('EDITOR_CHANGE_MISMATCH','原文件目录身份不符。');
    if(snapshot.editor_text_sha256!==null)hash(snapshot.editor_text_sha256);
    if(snapshot.metadata_sha256!==undefined&&snapshot.metadata_sha256!==null){hash(snapshot.metadata_sha256);const identity=snapshot.file_identity;keys(identity,['device','inode']);if(!/^\d{1,32}$/.test(identity.device??'')||!/^\d{1,32}$/.test(identity.inode??''))fail('EDITOR_CHANGE_MISMATCH','原文件物理身份不符。');}
    else if(snapshot.file_identity!==undefined&&snapshot.file_identity!==null)fail('EDITOR_CHANGE_MISMATCH','原文件元数据观察不完整。');
  }else if(snapshot.saved!==null||snapshot.location!==null||snapshot.editor_text_sha256!==null||typeof snapshot.unavailable_reason!=='string'||snapshot.unavailable_reason.length>100)fail('EDITOR_CHANGE_MISMATCH','不可用文件状态不完整。');
  const basis={scope:value.scope,lease_ref:value.lease_ref,revision:value.revision,target_operation_id:value.target_operation_id,action:value.action,snapshot};
  if(hash(value.preparation_sha256)!==digest(basis)||typeof value.native_path!=='string'||!paths.isAbsolute(value.native_path)||paths.basename(value.native_path)!==lease.grant.path.split('/').at(-1))fail('EDITOR_CHANGE_MISMATCH','原文件准备摘要或位置不符。');
  return {kind:value.kind,...basis,preparation_sha256:value.preparation_sha256,editor_client_id:value.editor_client_id,editor_window_id:value.editor_window_id,editor_uri:pathToFileURL(value.native_path).href,read_only:true,workspace_writes:0,model_calls:0,native_posts:0,automatic_replay:false};
}
function editorFileReceipt(value,s,context,operation,expected=null){
  if(!object(value)||value.id!==operation||value.kind!=='EDITOR_FILE_ACTION'||!object(value.request)||!same(value.session_ref,s.session_ref)||value.automatic_replay!==false)fail('EDITOR_CHANGE_MISMATCH','原文件操作回执身份不符。');
  const raw=value.request;keys(raw,['session_ref','generation','target_operation_id','lease_ref','editor_client_id','editor_window_id','editor_nonce','expected_revision','action','operation_id','preparation_sha256','editor_observation','user_approved']);
  const body=editorFileCommandBody(raw,s,context);
  if(raw.editor_client_id!==editorActor(context)||!same(raw.session_ref,s.session_ref)||raw.generation!==s.generation||body.operation_id!==operation||value.request_sha256!==digest(body)||expected&&!same(body,expected))fail('OPERATION_INPUT_MISMATCH','原文件操作输入不符，未重新提交。');
  if(!['RETURNED','FAILED','RECORDED_NOT_DISPATCHED','DISPATCHING','UNKNOWN_OUTCOME'].includes(value.status))fail('EDITOR_CHANGE_MISMATCH','原文件操作状态不完整。');
  const result={kind:value.kind,id:operation,session_ref:s.session_ref,generation:s.generation,request_sha256:value.request_sha256,status:value.status,automatic_replay:false,automatic_file_replay:false,file_effect:value.file_effect??'UNCONFIRMED',model_calls:0,native_posts:0};
  if(value.status==='RETURNED'){
    const lease=editorProjection(value.editor_change,s,context,{operation:body.target_operation_id}),file=value.file_result,mutating=['COMMIT','UNDO'].includes(body.action),status={COMMIT:'APPLIED',UNDO:'UNDONE',CONFIRM:'CONFIRMED',MANUAL_RESOLVE:'MANUALLY_RESOLVED'}[body.action];
    if(body.action!=='MANUAL_RESOLVE'&&!['FILE_CREATE','FILE_DELETE'].includes(lease.grant?.mode))fail('EDITOR_CHANGE_MISMATCH','原文件操作不能冒作文本修改。');
    if(!same(lease.lease_ref,body.lease_ref)||lease.editor_window_id!==body.editor_window_id||lease.revision!==body.expected_revision+(mutating?2:1)||!object(file)||file.status!==status||file.workspace_writes!==(mutating?1:0)||!(['COMMIT','UNDO'].includes(body.action)?lease.phase==='BUFFER_CHANGED':body.action==='CONFIRM'?['SAVED','CANCELLED'].includes(lease.phase):lease.phase==='MANUAL_SAVED'))fail('EDITOR_CHANGE_MISMATCH','原文件操作结果不符。');
    result.editor_change=lease;result.file_result={status:file.status,workspace_writes:file.workspace_writes,effect_scope:file.effect_scope,native_text_undo:false};
    result.file_result.metadata_recovery_recorded=Boolean(file.metadata_ref&&SHA.test(file.metadata_ref.id??'')&&SHA.test(file.metadata_ref.sha256??''));
    if(file.observed_saved)result.file_result.observed_saved=savedBasis(file.observed_saved);
    if(file.snapshot)result.file_result.snapshot=file.snapshot;
    result.semantic_acceptance=false;result.independent_verification='NOT_RUN';
  }
  return result;
}
function nativeScope(value,list=false){
  keys(value,['project_id','conversation_id']);const result={project_id:id(value.project_id)};
  if(list){if(value.conversation_id!==undefined)fail('EXACT_SCOPE_REQUIRED','目录只接受项目范围。');}
  else result.conversation_id=id(value.conversation_id);
  return result;
}
function nativeFile(value,project){
  keys(value,['kind','project_id','workspace_id','binding_revision','binding_sha256','path','revision','sha256']);
  if(value.kind!=='local_file'||value.project_id!==project||typeof value.path!=='string'||!value.path||value.path.length>1024||
      /[\\:\x00-\x1f\x7f]/.test(value.path)||value.path.startsWith('/')||value.path.split('/').some(p=>!p||p==='.'||p==='..'))fail('INVALID_PARAMS','文件不属于此讨论的已登记范围。');
  return {kind:'local_file',project_id:project,workspace_id:hash(value.workspace_id),binding_revision:number(value.binding_revision,1,2147483647),
    binding_sha256:hash(value.binding_sha256),path:value.path,revision:number(value.revision,1,2147483647),sha256:hash(value.sha256)};
}
function nativeLink(value,project){keys(value,['project_id','id','revision','sha256']);if(value.project_id!==project)fail('INVALID_PARAMS','文稿关联属于另一项目。');return {project_id:project,id:hash(value.id),revision:number(value.revision,1,1),sha256:hash(value.sha256)};}
function nativeMessage(value,s,operation){
  if(!object(value)||value.kind!=='NATIVE_MESSAGE'||value.project_id!==s.project_id||value.conversation_id!==s.conversation_id||value.operation_id!==operation||
      typeof value.status!=='string'||!/^[A-Z][A-Z0-9_]{0,63}$/.test(value.status)||!['CURRENT_HOST','HISTORICAL_READONLY'].includes(value.record_origin)||
      !object(value.input)||value.input.origin!=='ORIGINAL_NATIVE_REQUEST')fail('STALE_NATIVE_SCOPE','原操作不属于所选桌面讨论。');
  const result={kind:'NATIVE_MESSAGE',...s,operation_id:id(operation),status:value.status,record_origin:value.record_origin,request_sha256:hash(value.request_sha256),
    input:{raw_text:text(value.input.raw_text,16000),origin:'ORIGINAL_NATIVE_REQUEST'},provider_profile_ref:value.provider_profile_ref==null?null:ref(value.provider_profile_ref),
    native_message_ref:value.native_message_ref==null?null:ref(value.native_message_ref),
    file_source_ref:value.file_source_ref==null?null:nativeFile(value.file_source_ref,s.project_id),file_link_ref:value.file_link_ref==null?null:nativeLink(value.file_link_ref,s.project_id),
    control_capabilities:Object.fromEntries(['can_guide','can_status','can_cancel'].map(k=>[k,value.control_capabilities?.[k]===true])),automatic_retry:false};
  Object.assign(result,nativeContextRecovery.context(value,operation,value.record_origin));
  for(const k of ['text','summary'])if(value[k]!==undefined)result[k]=text(value[k],48000,{empty:true});
  if(value.questions!==undefined){if(!Array.isArray(value.questions)||value.questions.length>20)fail('INVALID_UPSTREAM_RESPONSE','原回复问题范围不完整。');result.questions=value.questions.map(v=>text(v,4000,{empty:true}));}
  if(value.native_message_phase!==undefined)result.native_message_phase=text(value.native_message_phase,64);
  if(value.native_execution!==undefined)result.native_execution=nativeExecution(value.native_execution);
  if(value.native_feedback_ref!=null)result.native_feedback_ref=ref(value.native_feedback_ref);
  if(value.rework_previews!==undefined){if(!Array.isArray(value.rework_previews)||value.rework_previews.length>16)fail('INVALID_UPSTREAM_RESPONSE','预览引用超出范围。');
    result.rework_previews=value.rework_previews.map(v=>{if(v?.kind!=='PLAN')fail('INVALID_UPSTREAM_RESPONSE','预览种类不符。');return {kind:'PLAN',record_ref:ref(v.record_ref),mapping_ref:ref(v.mapping_ref),native_feedback_ref:ref(v.native_feedback_ref),proposal_ref:ref(v.proposal_ref),status:text(v.status,64)};});}
  if(value.error)result.error={code:'ORIGINAL_OPERATION_ERROR',message:'原操作未完成，请在桌面核查原记录；未自动重试。'};
  return result;
}
function nativeExecution(value){
  keys(value,['stop','budget']);const result={},time=v=>{if(typeof v!=='string'||v.length>64||!/^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/.test(v)||!Number.isFinite(Date.parse(v)))fail('INVALID_UPSTREAM_RESPONSE','执行时间信息暂不可核对。');return v;};
  if(value.stop!==undefined){const v=value.stop;keys(v,['reason','owner_stop_status','observed_at']);if(!['OWNER_DISCONNECTED_NO_POST_REPLAY','OWNER_LOST_NO_AUTOMATIC_REPLAY','MESSAGE_DEADLINE_ABORT_REQUESTED','ORIGINAL_OBSERVATION_UNAVAILABLE_NO_POST_REPLAY'].includes(v.reason)||v.owner_stop_status!==undefined&&v.owner_stop_status!=='EXPIRED')fail('INVALID_UPSTREAM_RESPONSE','执行状态暂不可核对。');result.stop={reason:v.reason,...(v.owner_stop_status===undefined?{}:{owner_stop_status:v.owner_stop_status}),...(v.observed_at===undefined?{}:{observed_at:time(v.observed_at)})};}
  if(value.budget!==undefined){const v=value.budget;keys(v,['scope','timeout_seconds','absolute_deadline','admitted_at','dispatch_at','operation_deadline','startup_elapsed_seconds','remaining_at_dispatch_seconds']);if(v.scope!=='SHARED_ABSOLUTE_TASK_DEADLINE')fail('INVALID_UPSTREAM_RESPONSE','执行时限范围暂不可核对。');const b={scope:v.scope,timeout_seconds:number(v.timeout_seconds,1,3600),absolute_deadline:time(v.absolute_deadline)};
    for(const k of ['admitted_at','dispatch_at','operation_deadline'])if(v[k]!==undefined)b[k]=time(v[k]);
    for(const k of ['startup_elapsed_seconds','remaining_at_dispatch_seconds'])if(v[k]!==undefined){if(typeof v[k]!=='number'||!Number.isFinite(v[k])||v[k]<0||v[k]>3600)fail('INVALID_UPSTREAM_RESPONSE','执行时间信息暂不可核对。');b[k]=v[k];}result.budget=b;}
  return result;
}
function nativeCoverage(value,returned){
  if(!object(value)||!Number.isSafeInteger(value.candidate_limit)||value.candidate_limit<1||value.candidate_limit>256||
      typeof value.candidate_window_truncated!=='boolean'||typeof value.has_more!=='boolean'||value.complete!==!value.has_more||
      value.returned!==returned||value.candidate_window_truncated&&!value.has_more)fail('INVALID_UPSTREAM_RESPONSE','讨论目录覆盖范围无法核对。');
  if(value.scope_filter!=='EXPLICIT_PROJECT_ONLY'||value.unbound_messages_included!==false||value.order_basis!=='RECORD_MODIFIED_AT')fail('INVALID_UPSTREAM_RESPONSE','讨论目录项目归属未明确。');
  return selected(value,['candidate_limit','candidate_window_truncated','observed_messages','message_bytes_limit','returned','has_more','complete','scope_filter','unbound_messages_included','order_basis']);
}
function nativeRejected(error){
  const observation=error?.quick_submission_observation;
  // Only the Main-owned API observation of this service's pre-dispatch
  // rejection contracts is definite. A bare HTTP status is not that proof.
  return observation?.kind==='HTTP_RESPONSE'&&observation.http_status===error.status&&
    [[401,'UNAUTHORIZED'],[403,'FORBIDDEN'],[404,'NOT_FOUND'],[409,'REVISION_CONFLICT'],[422,'INVALID_INPUT']]
      .some(([status,code])=>error.status===status&&observation.api_code===code);
}
function nativeSelection(value){
  if(value?.kind==='TEXT_RANGE'){keys(value,['kind','char_start','char_end','quote_sha256']);const start=number(value.char_start,0,1048576),end=number(value.char_end,1,1048576);if(end<=start)fail('INVALID_PARAMS','选择范围无效。');return {kind:'TEXT_RANGE',char_start:start,char_end:end,quote_sha256:hash(value.quote_sha256)};}
  keys(value,['kind','quote']);if(value.kind!=='EXACT_QUOTE'||typeof value.quote!=='string'||!value.quote.length||Array.from(value.quote).length>4000||Buffer.from(value.quote,'utf8').toString('utf8')!==value.quote)fail('INVALID_PARAMS','需要原保存文本的精确引文。');return {kind:'EXACT_QUOTE',quote:value.quote};
}
const nativeFileMethods=new Set(['native.file.read','native.file.open','native.feedback.record','native.feedback.operation','native.rework.preview','native.rework.apply','native.rework.operation']);
function nativeEnvelope(value,kind,s,parent){if(!object(value)||value.kind!==kind||value.project_id!==s.project_id||value.conversation_id!==s.conversation_id||value.parent_operation_id!==parent)fail('STALE_NATIVE_SCOPE','文件回执属于另一讨论或原操作。');return {kind,...s,parent_operation_id:parent};}
function nativeSavedFile(value,s,parent){
  const result=nativeEnvelope(value,'NATIVE_FILE',s,parent),source=nativeFile(value.file_source_ref,s.project_id),baseline=nativeFile(value.baseline_file_source_ref,s.project_id);
  if(['workspace_id','binding_revision','binding_sha256','path'].some(k=>source[k]!==baseline[k])||!['AVAILABLE','BINARY','TOO_LARGE'].includes(value.availability)||typeof value.current!=='boolean'||value.read_only!==true||value.model_calls!==0)fail('INVALID_UPSTREAM_RESPONSE','原文件保存版本无法核对。');
  Object.assign(result,{file_source_ref:source,baseline_file_source_ref:baseline,availability:value.availability,encoding:value.encoding,byte_length:number(value.byte_length,0,67108864),current:value.current,live_sha256:value.live_sha256==null?null:hash(value.live_sha256),read_only:true,model_calls:0});
  if(value.availability==='AVAILABLE'){if(value.encoding!=='utf-8'||typeof value.content!=='string'||Buffer.byteLength(value.content)>196608||value.content.includes('\0')||Buffer.from(value.content,'utf8').toString('utf8')!==value.content||crypto.createHash('sha256').update(value.content).digest('hex')!==source.sha256||Buffer.byteLength(value.content)!==value.byte_length)fail('INVALID_UPSTREAM_RESPONSE','保存文本与版本哈希不同。');result.content=value.content;}
  else if(value.encoding!=='UNAVAILABLE')fail('INVALID_UPSTREAM_RESPONSE','文件内容可用范围不明。');
  return result;
}
function nativeReworkPreview(value,s,parent,expected){
  const out=nativeEnvelope(value,'NATIVE_REWORK_PREVIEW',s,parent),source=nativeFile(value.file_source_ref,s.project_id),d=value.diff;
  if(!same(ref(value.plan_ref),expected)||value.model_calls!==0||!Array.isArray(value.write_scope)||value.write_scope.length!==1||value.write_scope[0].workspace_id!==source.workspace_id||value.write_scope[0].path!==source.path||!object(d)||d.encoding!=='UTF-8'||d.content_scope!=='FULL_FILE'||d.read_only!==true)fail('INVALID_UPSTREAM_RESPONSE','提案原对象或限定范围无法核对。');
  for(const name of ['before','after'])if(typeof d[name]!=='string'||d[name].includes('\0')||Buffer.byteLength(d[name])>196608||Buffer.from(d[name],'utf8').toString('utf8')!==d[name]||crypto.createHash('sha256').update(d[name]).digest('hex')!==hash(d[name+'_sha256']))fail('INVALID_UPSTREAM_RESPONSE','不可变差异文本与哈希不同。');
  if(d.before_sha256!==source.sha256)fail('INVALID_UPSTREAM_RESPONSE','差异基线不是原文件版本。');
  const locator=value.locator;let selected;
  if(locator?.kind==='TEXT_RANGE')selected=nativeSelection(locator);
  else if(locator?.kind==='FILE'){keys(locator,['kind']);selected={kind:'FILE'};}
  else{keys(locator,['kind','pointer']);if(locator.kind!=='JSON_POINTER'||typeof locator.pointer!=='string'||locator.pointer.length>4000)fail('INVALID_UPSTREAM_RESPONSE','提案对象定位无法核对。');selected={kind:'JSON_POINTER',pointer:locator.pointer};}
  if(typeof value.already_applied!=='boolean'||typeof value.can_apply!=='boolean'||value.already_applied&&value.can_apply)fail('INVALID_UPSTREAM_RESPONSE','提案应用状态无法核对。');
  return {...out,status:text(value.status,64),plan_ref:ref(value.plan_ref),mapping_ref:ref(value.mapping_ref),native_feedback_ref:ref(value.native_feedback_ref),proposal_ref:ref(value.proposal_ref),file_source_ref:source,locator:selected,write_scope:[{workspace_id:source.workspace_id,path:source.path}],current_basis_status:text(value.current_basis_status,64),already_applied:value.already_applied,can_apply:value.can_apply,diff:{before:d.before,after:d.after,before_sha256:d.before_sha256,after_sha256:d.after_sha256,encoding:'UTF-8',content_scope:'FULL_FILE',read_only:true},model_calls:0};
}
function nativeFileReceipt(value,s,parent,op,apply,expected){
  const out=nativeEnvelope(value,apply?'NATIVE_REWORK_APPLY':'NATIVE_FEEDBACK',s,parent),p=value.request;
  if(value.operation_id!==op||!object(p)||p.operation_id!==op||value.automatic_retry!==false||hash(value.request_sha256)!==digest(p)||expected&&digest(p)!==digest(expected))fail('OPERATION_INPUT_MISMATCH','原操作回执与输入不同；不要重复执行。');
  const request=apply?{operation_id:op,plan_ref:ref(p.plan_ref),expected_current_sources:Array.isArray(p.expected_current_sources)?p.expected_current_sources.map(v=>nativeFile(v,s.project_id)):null,allow_rebase:p.allow_rebase}:{operation_id:op,file_source_ref:nativeFile(p.file_source_ref,s.project_id),comment:text(p.comment,16000),selection:nativeSelection(p.selection)};
  if(digest(request)!==digest(p)||apply&&(!request.expected_current_sources||request.expected_current_sources.length!==1||typeof request.allow_rebase!=='boolean'||!same(ref(value.plan_ref),request.plan_ref)))fail('OPERATION_INPUT_MISMATCH','原操作保存范围与请求不同。');
  Object.assign(out,{operation_id:op,status:text(value.status,64),request,request_sha256:value.request_sha256,automatic_retry:false});
  if(apply){out.plan_ref=request.plan_ref;out.outcomes=[];if(value.receipt_ref!=null)out.receipt_ref=ref(value.receipt_ref);if(!Array.isArray(value.outcomes)||value.outcomes.length>1)fail('INVALID_UPSTREAM_RESPONSE','限定应用结果范围不符。');out.outcomes=value.outcomes.map(v=>{const source=v.source_ref==null?null:nativeFile(v.source_ref,s.project_id);if(typeof v.path!=='string'||v.path!==request.expected_current_sources[0].path||source&&['workspace_id','binding_revision','binding_sha256','path'].some(key=>source[key]!==request.expected_current_sources[0][key]))fail('INVALID_UPSTREAM_RESPONSE','应用结果不是所选文件。');return {path:v.path,status:text(v.status,64),...(source?{source_ref:source}:{})};});}
  else{out.file_source_ref=request.file_source_ref;if(canonical(nativeFile(value.file_source_ref,s.project_id))!==canonical(request.file_source_ref))fail('OPERATION_INPUT_MISMATCH','反馈来源不同。');if(value.feedback_ref!=null){out.feedback_ref=ref(value.feedback_ref);out.selection_verification=text(value.selection_verification,64);out.file_selection=value.file_selection==null?null:nativeSelection(value.file_selection);if(value.requested_selection!==undefined){out.requested_selection=nativeSelection(value.requested_selection);if(canonical(out.requested_selection)!==canonical(request.selection))fail('OPERATION_INPUT_MISMATCH','反馈引文不是本次请求。');}}}
  if(apply&&out.status==='APPLIED'&&(!out.receipt_ref||out.outcomes.length!==1||!['APPLIED','UNCHANGED'].includes(out.outcomes[0].status)||!out.outcomes[0].source_ref))fail('OPERATION_INPUT_MISMATCH','应用成功尚缺真实限定文件回执。');
  if(!apply&&out.status==='RECORDED'){
    if(!out.feedback_ref)fail('OPERATION_INPUT_MISMATCH','已记录反馈尚缺原引用。');
    if(out.selection_verification==='TEXT_RANGE_HASH_VERIFIED'){
      if(out.file_selection?.kind!=='TEXT_RANGE'||request.selection.kind==='TEXT_RANGE'&&canonical(out.file_selection)!==canonical(request.selection)||request.selection.kind==='EXACT_QUOTE'&&(out.file_selection.quote_sha256!==crypto.createHash('sha256').update(request.selection.quote).digest('hex')||out.file_selection.char_end-out.file_selection.char_start!==Array.from(request.selection.quote).length))fail('OPERATION_INPUT_MISMATCH','已核对选区与本次输入不同。');
    }else if(request.selection.kind!=='EXACT_QUOTE'||!['QUOTE_NOT_FOUND','QUOTE_AMBIGUOUS','QUOTE_NOT_ADAPTED'].includes(out.selection_verification)||out.file_selection!==null)fail('OPERATION_INPUT_MISMATCH','反馈选区核对状态不符。');
    if(request.selection.kind==='EXACT_QUOTE'&&!out.requested_selection)fail('OPERATION_INPUT_MISMATCH','原引文回执缺失。');
  }
  return out;
}
async function openSavedInput(source,target,scope,{signal,openArtifact,authorize}){
  const ext=paths.extname(source.path).toLowerCase();
  if(!(target==='editor'?EDITOR_TYPES:NATIVE_TYPES).has(ext))fail('NOT_SUPPORTED','此文件类型尚未接入该打开方式。');
  if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');
  if(typeof openArtifact!=='function')fail('NOT_SUPPORTED','打开入口不可用。');
  const opened=await openArtifact({kind:'workspace-file',target,project_id:source.project_id,file_source_ref:source},{signal,scope,authorize});
  const out={status:target==='editor'?'EDITOR_READY':'OPEN_REQUESTED',file_source_ref:source,native_software_open_confirmed:false,editor_buffer_modified:false};
  if(target==='editor'){
    if(opened?.status!=='RESOLVED'||canonical(opened.file_source_ref)!==canonical(source)||typeof opened.native_path!=='string'||!paths.isAbsolute(opened.native_path))fail('ARTIFACT_CHANGED','原文件解析版本不同。');
    out.editor_uri=pathToFileURL(opened.native_path).href;
    out.editor_kind=/\.(md|markdown)$/i.test(source.path)?'markdown':'text';
  }else if(opened?.status!=='OPEN_REQUESTED'||canonical(opened.file_source_ref)!==canonical(source)){
    fail('OPERATION_OUTCOME_UNKNOWN','原软件打开结果尚未核对；请检查原请求。');
  }
  return out;
}

async function canonicalInputOpen(params,context,{request,signal,openArtifact}){
  const s=canonicalConversations.scope(params.scope??{},true),p=params.payload??{};
  keys(p,['operation_id','execution_project_id']);
  const operation_id=id(p.operation_id),execution_project_id=id(p.execution_project_id);
  if(!s.conversation_ref.revision)fail('INVALID_SCOPE','请选择已确认的原会话。');
  const authorize=()=>{
    if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');
    const grants=context.currentGrants?.()??context.grants??{};
    if(!grants.methods?.includes('conversation.file.open')||![s.project_id,execution_project_id].every(pid=>canonicalConversations.allowed(pid,grants)))fail('SCOPE_DENIED','此连接尚未获准打开该会话的原始文件。');
  };
  authorize();
  const expected={...s,operation_id,execution_project_id};
  const resolved=await request('/api/desktop-host/native-open/conversation-file',expected,signal);
  if(resolved?.kind!=='CANONICAL_INPUT_FILE'||resolved.operation_id!==operation_id||resolved.project_id!==s.project_id||canonical(resolved.conversation_ref)!==canonical(s.conversation_ref))fail('ARTIFACT_CHANGED','原文件会话范围已变化，请刷新。');
  const source=nativeFile(resolved.file_source_ref,execution_project_id),baseline=nativeFile(resolved.baseline_file_source_ref,execution_project_id);
  if(['workspace_id','binding_revision','binding_sha256','path'].some(key=>source[key]!==baseline[key]))fail('ARTIFACT_CHANGED','原文件绑定已变化，请刷新。');
  const target=EDITOR_TYPES.has(paths.extname(source.path).toLowerCase())?'editor':'native';
  authorize();
  return {kind:'CANONICAL_FILE_OPEN',...expected,...await openSavedInput(source,target,s,{signal,openArtifact,authorize})};
}

async function nativeFileDispatch(method,params,{request,signal,openArtifact}){
  const p=params.payload??{},s=nativeScope(params.scope??{}),parent=id(p.parent_operation_id),query='?project_id='+encodeURIComponent(s.project_id),base='/api/desktop-host/native-conversations/'+encodeURIComponent(s.conversation_id)+'/operations/'+encodeURIComponent(parent),get=route=>request(route,undefined,signal);
  if(method==='native.file.read'||method==='native.file.open'){
    keys(p,method==='native.file.read'?['parent_operation_id','version']:['parent_operation_id','file_source_ref','target']);const version=method==='native.file.read'?(p.version??'CURRENT_SAVED'):'CURRENT_SAVED';if(!['CURRENT_SAVED','ORIGINAL_SAVED'].includes(version))fail('INVALID_PARAMS','请选择明确的保存版本。');
    const current=nativeSavedFile(await get(base+'/file'+query+'&version='+version),s,parent);if(method==='native.file.read')return current;
    const source=nativeFile(p.file_source_ref,s.project_id),target=p.target;
    if(!['editor','native'].includes(target)||canonical(current.file_source_ref)!==canonical(source)||!current.current||current.live_sha256!==source.sha256||typeof openArtifact!=='function')fail('ARTIFACT_CHANGED','原文件已更新或打开入口不可用，请刷新');
    return {kind:'NATIVE_FILE_OPEN',...s,parent_operation_id:parent,...await openSavedInput(source,target,s,{signal,openArtifact})};
  }
  if(method==='native.rework.preview'){keys(p,['parent_operation_id','plan_ref']);const expected=ref(p.plan_ref);return nativeReworkPreview(await get(base+'/rework-preview'+query+'&plan_ref='+encodeURIComponent(canonical(expected))),s,parent,expected);}
  const apply=method.startsWith('native.rework.'),write=method.endsWith('.record')||method.endsWith('.apply');let body,expected;const op=id(p.operation_id),route=base+(apply?'/rework-operations/':'/feedback-operations/')+encodeURIComponent(op)+query;
  if(write){
    if(apply){keys(p,['parent_operation_id','operation_id','plan_ref','expected_current_sources','allow_rebase','user_approved']);if(p.user_approved!==true||p.allow_rebase!==undefined&&typeof p.allow_rebase!=='boolean'||!Array.isArray(p.expected_current_sources)||p.expected_current_sources.length!==1)fail('INVALID_PARAMS','请明确审阅并批准限定应用与当前保存版本。');expected={operation_id:op,plan_ref:ref(p.plan_ref),expected_current_sources:p.expected_current_sources.map(v=>nativeFile(v,s.project_id)),allow_rebase:p.allow_rebase??false};body={...expected,user_approved:true};}
    else{keys(p,['parent_operation_id','operation_id','file_source_ref','comment','selection']);body=expected={operation_id:op,file_source_ref:nativeFile(p.file_source_ref,s.project_id),comment:text(p.comment,16000),selection:nativeSelection(p.selection)};}
    // These domain services can claim an operation before a 4xx validation
    // failure. An HTTP status is not proof that the request was never admitted.
    if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');try{await request(base+(apply?'/rework-apply':'/feedback')+query,body,signal);}catch{}
  }else keys(p,['parent_operation_id','operation_id']);
  let result;try{result=await get(route);}catch{fail('OPERATION_OUTCOME_UNKNOWN','原限定操作尚未核对；只查原操作，不重复写入。');}
  try{return nativeFileReceipt(result,s,parent,op,apply,expected);}
  catch{fail('OPERATION_INPUT_MISMATCH','原操作回执无法与保存输入核对；只查原操作，不重复写入。');}
}
async function nativeDispatch(method,params,{request,signal,openArtifact}){
  if(nativeFileMethods.has(method))return nativeFileDispatch(method,params,{request,signal,openArtifact});
  const p=params.payload??{},s=nativeScope(params.scope??{},method==='native.conversations.list'),query='?project_id='+encodeURIComponent(s.project_id);
  const base='/api/desktop-host/native-conversations',conversation=base+'/'+encodeURIComponent(s.conversation_id??'');
  const get=route=>request(route,undefined,signal),operation=async op=>nativeMessage(await get(conversation+'/operations/'+encodeURIComponent(id(op))+query),s,op);
  const post=(route,body)=>{if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');return request(route,body,signal);};
  if(method==='native.conversations.list'){
    keys(p,['limit']);const limit=number(p.limit??20,1,30),value=await get(base+query+'&limit='+limit);
    if(value?.kind!=='NATIVE_CONVERSATION_DIRECTORY'||value.project_id!==s.project_id||!Array.isArray(value.conversations)||value.conversations.length>limit)fail('INVALID_UPSTREAM_RESPONSE','桌面讨论目录无法核对。');
    const conversations=value.conversations.map(v=>{if(v?.kind!=='NATIVE_CONVERSATION'||v.project_id!==s.project_id)fail('STALE_NATIVE_SCOPE','讨论目录包含另一项目。');return {kind:v.kind,project_id:s.project_id,conversation_id:id(v.conversation_id),...(typeof v.title==='string'&&v.title.trim()?{title:clip(v.title,300)}:{}),message_count_observed:number(v.message_count_observed,1,256),last_operation_id:id(v.last_operation_id)};});
    if(new Set(conversations.map(v=>v.conversation_id)).size!==conversations.length)fail('INVALID_UPSTREAM_RESPONSE','讨论目录重复。');
    return {kind:value.kind,...s,conversations,coverage:nativeCoverage(value.coverage,conversations.length),model_calls:0};
  }
  if(method==='native.conversation.get'){
    keys(p,['limit']);const limit=number(p.limit??20,1,40),value=await get(conversation+query+'&limit='+limit);
    if(value?.kind!=='NATIVE_CONVERSATION'||value.project_id!==s.project_id||value.conversation_id!==s.conversation_id||!Array.isArray(value.messages)||value.messages.length>limit)fail('STALE_NATIVE_SCOPE','读取的不是所选桌面讨论。');
    const messages=value.messages.map(v=>nativeMessage(v,s,id(v?.operation_id)));
    if(new Set(messages.map(v=>v.operation_id)).size!==messages.length)fail('INVALID_UPSTREAM_RESPONSE','原操作重复。');
    return {kind:value.kind,...s,...(typeof value.title==='string'&&value.title.trim()?{title:clip(value.title,300)}:{}),messages,latest_provider_profile_ref:value.latest_provider_profile_ref==null?null:ref(value.latest_provider_profile_ref),coverage:nativeCoverage(value.coverage,messages.length),model_calls:0};
  }
  if(method==='native.operation'){keys(p,['operation_id']);return operation(id(p.operation_id));}
  if(method==='native.message'){
    keys(p,['operation_id','raw_text','provider_profile_ref','file_source_ref','file_link_ref','native_feedback_ref']);
    if(p.file_source_ref!=null&&p.file_link_ref!=null)fail('INVALID_PARAMS','只能明确选择一种文件依据。');
    const body={operation_id:id(p.operation_id),raw_text:text(p.raw_text,16000),context_ref:null,project_id:s.project_id,conversation_id:s.conversation_id,
      provider_profile_ref:ref(p.provider_profile_ref),file_source_ref:p.file_source_ref==null?null:nativeFile(p.file_source_ref,s.project_id),file_link_ref:p.file_link_ref==null?null:nativeLink(p.file_link_ref,s.project_id)};
    if(p.native_feedback_ref!=null){if(!body.file_source_ref)fail('INVALID_PARAMS','精确反馈需要同一原文件。');body.native_feedback_ref=ref(p.native_feedback_ref);}
    const current=await get(conversation+query+'&limit=1');
    if(current?.kind!=='NATIVE_CONVERSATION'||current.project_id!==s.project_id||current.conversation_id!==s.conversation_id||!current.messages?.length)fail('STALE_NATIVE_SCOPE','先读取现有桌面讨论。');
    if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');
    try{await post('/api/desktop-host/native-messages',body);}catch(error){if(nativeRejected(error))fail('API_REQUEST_REJECTED','原服务拒绝此消息；请核对模型与来源，原稿保留。');}
    let result;try{result=await operation(body.operation_id);}catch{fail('OPERATION_OUTCOME_UNKNOWN','原消息尚未核对，请只读取原操作，不重复发送。');}
    if(result.request_sha256!==digest(body))fail('OPERATION_INPUT_MISMATCH','原消息回执与输入不一致。');return result;
  }
  if(method==='native.control'||method==='native.guidance'){
    const write=method==='native.guidance';keys(p,write?['parent_operation_id','operation_id','expected_native_message_ref','raw_text','intent']:['parent_operation_id','operation_id']);
    const parent=id(p.parent_operation_id),op=id(p.operation_id),current=await operation(parent),route='/api/desktop-host/native-messages/'+encodeURIComponent(parent);let body;
    if(write){const expected=ref(p.expected_native_message_ref),intent=p.intent??'AUTO';if(!['AUTO','STEER','STATUS'].includes(intent))fail('INVALID_PARAMS','纠正意图无效。');
      if(!same(current.native_message_ref,expected)||current.record_origin!=='CURRENT_HOST'||!(current.control_capabilities.can_guide||intent!=='STEER'&&current.control_capabilities.can_status))fail('NATIVE_CONTROL_NOT_AVAILABLE','原任务当前不可接收此纠正。');
      body={operation_id:op,expected_native_message_ref:expected,raw_text:text(p.raw_text,16000),intent};
      if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');
      try{await post(route+'/guidance',body);}catch(error){if(nativeRejected(error))fail('API_REQUEST_REJECTED','原服务拒绝此纠正；请核对原任务。');}
    }
    let result;try{result=await get(route+'/controls/'+encodeURIComponent(op));}catch{fail('OPERATION_OUTCOME_UNKNOWN','原纠正结果尚未核对，不重复发送。');}
    if(result?.operation_id!==op||result.parent_operation_id!==parent||!same(result.native_message_ref,current.native_message_ref)||!object(result.request)||
      result.request.operation_id!==op||!same(result.request.expected_native_message_ref,current.native_message_ref)||!['AUTO','STEER','STATUS'].includes(result.request.intent)||
      hash(result.request_sha256)!==digest(result.request)||body&&result.request_sha256!==digest(body))fail('OPERATION_INPUT_MISMATCH','原纠正回执与讨论或输入不一致。');
    return {kind:'NATIVE_CONTROL',...s,operation_id:op,parent_operation_id:parent,native_message_ref:ref(result.native_message_ref),status:text(result.status,64),request_sha256:result.request_sha256,
      request:{operation_id:op,expected_native_message_ref:ref(result.request.expected_native_message_ref),raw_text:text(result.request.raw_text,16000),intent:text(result.request.intent,16)},automatic_retry:false};
  }
  if(method==='native.cancel'){
    keys(p,['operation_id','expected_native_message_ref']);const op=id(p.operation_id),current=await operation(op);
    if(!same(current.native_message_ref,ref(p.expected_native_message_ref))||current.record_origin!=='CURRENT_HOST'||!current.control_capabilities.can_cancel)fail('NATIVE_CONTROL_NOT_AVAILABLE','所选原任务当前不可取消。');
    if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');
    try{await post('/api/desktop-host/native-messages/'+encodeURIComponent(op)+'/cancel',{});}catch(error){if(nativeRejected(error))fail('API_REQUEST_REJECTED','原服务拒绝取消；请核对原任务。');}
    try{return await operation(op);}catch{fail('OPERATION_OUTCOME_UNKNOWN','取消尚未核对，请读取原任务，不重复请求。');}
  }
  fail('NOT_SUPPORTED','此桌面讨论方法尚未接入。');
}
function editorObservedPath(value){
  if(typeof value!=='string'||!value||value.length>1024||/[\\:\x00-\x1f\x7f]/.test(value)||value.startsWith('/')||value.split('/').some(p=>!p||p==='.'||p==='..'||p.toLowerCase().startsWith('.eeas-write-')||['.git','.eeas-runtime','.eeas-sdk-state'].includes(p.toLowerCase())))fail('EDITOR_OBSERVATION_INVALID','原文件相对路径无效。');return value;
}
function editorObserverAddress(p,s,context){
  if(!['OBSERVE','RETIRE'].includes(p.action))fail('INVALID_PARAMS','请选择准确的编辑器观察动作。');
  return {session_ref:s.session_ref,generation:number(s.generation,1,2147483647),workspace_ref:diffWorkspace(p.workspace_ref),editor_client_id:editorActor(context),editor_window_id:id(p.editor_window_id),editor_process_id:number(p.editor_process_id,1,2147483647),editor_parent_process_id:number(p.editor_parent_process_id,1,2147483647),action:p.action};
}
function editorProcessIdentity(value){
  keys(value,['pid','parent_pid','birth','parent_birth']);number(value.pid,1,2147483647);number(value.parent_pid,1,2147483647);
  for(const key of ['birth','parent_birth'])if(typeof value[key]!=='string'||!/^\d{1,12}(?:\.\d{1,12})?$/.test(value[key])||Number(value[key])<=0)fail('EDITOR_OBSERVATION_INVALID','原编辑器进程出生身份不完整。');return value;
}
function observerRecoveryAddress(p,s,context){
  return {session_ref:s.session_ref,generation:number(s.generation,1,2147483647),workspace_ref:diffWorkspace(p.workspace_ref),observer_id:hash(p.observer_id),editor_client_id:editorActor(context),editor_window_id:id(p.editor_window_id),editor_process_id:number(p.editor_process_id,1,2147483647),editor_parent_process_id:number(p.editor_parent_process_id,1,2147483647)};
}
function observerRecoveryReceipt(value,s,context,operation,body){
  keys(value,['kind','id','scope','request_sha256','observer_id','editor_client_id','editor_window_id','original_editor_client_id','original_editor_window_id','original_process_state','phase','revision','status','workspace_writes','model_calls','native_posts','permission_granted','automatic_replay','atomic_editor_write_guarantee','original_evidence_retained','observation_time_scope']);
  if(value.kind!=='EDITOR_OBSERVER_RECOVERY'||value.id!==operation||!same(value.scope,{...s,workspace_ref:diffWorkspace(value.scope?.workspace_ref)})||value.editor_client_id!==editorActor(context)||value.phase!=='RETIRED'||value.status!=='RETURNED'||!['SAME_PROCESS','ENDED'].includes(value.original_process_state)||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.permission_granted!==false||value.automatic_replay!==false||value.atomic_editor_write_guarantee!==false||value.original_evidence_retained!==true||value.observation_time_scope!=='AT_ORIGINAL_COMMAND_ONLY')fail('EDITOR_OBSERVATION_INVALID','原保存状态恢复回执身份不符。');
  hash(value.observer_id);hash(value.request_sha256);id(value.editor_window_id);id(value.original_editor_client_id);id(value.original_editor_window_id);number(value.revision,2,2147483647);
  if(value.observer_id!==digest({project_id:s.project_id,workspace_id:value.scope.workspace_ref.id,editor_client_id:value.original_editor_client_id,editor_window_id:value.original_editor_window_id})||body&&(value.request_sha256!==digest(body)||value.observer_id!==body.observer_id||value.editor_window_id!==body.editor_window_id||!same(value.scope.workspace_ref,body.workspace_ref)||value.revision!==body.expected_revision+1))fail('OPERATION_INPUT_MISMATCH','原恢复操作不符，未重新提交。');return value;
}
function editorRootIdentity(value){keys(value,['device','inode']);if(!/^\d{1,32}$/.test(value.device??'')||!/^\d{1,32}$/.test(value.inode??''))fail('EDITOR_OBSERVATION_INVALID','原目录物理身份不完整。');return {device:value.device,inode:value.inode};}
function editorObservation(value){
  keys(value,['captured_at_epoch_ms','availability','root_identity','documents','documents_complete','unavailable_reason','permission_ref','capture_ref']);
  if(!Number.isSafeInteger(value.captured_at_epoch_ms)||value.captured_at_epoch_ms<=0)fail('EDITOR_OBSERVATION_INVALID','需要原地编辑器采集时间。');
  if(!['OBSERVED','UNAVAILABLE'].includes(value.availability)||typeof value.documents_complete!=='boolean'||!Array.isArray(value.documents)||value.documents.length>64)fail('EDITOR_OBSERVATION_INVALID','编辑器观察不完整。');
  const root=value.root_identity==null?null:editorRootIdentity(value.root_identity),reason=value.unavailable_reason??null;
  if(value.availability==='OBSERVED'&&(!root||!value.documents_complete||reason!==null)||value.availability==='UNAVAILABLE'&&(root||!['NOT_CAPTURED','EDITOR_UNAVAILABLE','NAMESPACE_CHANGED','DOCUMENT_LIMIT'].includes(reason)||value.documents_complete&&reason!=='NAMESPACE_CHANGED'||value.documents.length&&!value.documents_complete))fail('EDITOR_OBSERVATION_INVALID','不可用观察不能当作已保存。');
  const documents=value.documents.map(row=>{keys(row,['path','document_version','content_sha256','is_dirty']);if(typeof row.is_dirty!=='boolean')fail('EDITOR_OBSERVATION_INVALID','编辑状态无效。');return {path:editorObservedPath(row.path),document_version:number(row.document_version,1,2147483647),content_sha256:hash(row.content_sha256),is_dirty:row.is_dirty};});
  let permission=null;if(value.permission_ref!=null){keys(value.permission_ref,['id','generation','sha256']);if(!/^per_[A-Za-z0-9_-]{1,100}$/.test(value.permission_ref.id??'')||value.availability!=='OBSERVED')fail('EDITOR_OBSERVATION_INVALID','原权限观察无效。');permission={id:value.permission_ref.id,generation:number(value.permission_ref.generation,1,2147483647),sha256:hash(value.permission_ref.sha256)};}
  const result={captured_at_epoch_ms:value.captured_at_epoch_ms,availability:value.availability,root_identity:root,documents,documents_complete:value.documents_complete,unavailable_reason:reason,permission_ref:permission};
  if(value.capture_ref!=null)result.capture_ref=hash(value.capture_ref);
  if(Buffer.byteLength(canonical(result))>49152)fail('EDITOR_OBSERVATION_CAPACITY','编辑器观察过大，请先保留当前内容。');return result;
}
function editorObserverView(value,s,context){
  keys(value,['kind','id','project_id','workspace_ref','editor_client_id','editor_window_id','phase','revision','observation_state','dirty_paths','dirty_count','dirty_paths_truncated','process_identity','observed_documents','current','read_only','workspace_writes','model_calls','native_posts','automatic_replay','atomic_editor_write_guarantee','captured_at_epoch_ms','observed_at','received_at','capture_ref','current_scope','current_scope_is_execution_authorization']);
  const w=diffWorkspace(value.workspace_ref),identity=value.process_identity;
  if(value.kind!=='EDITOR_OBSERVER'||value.project_id!==s.project_id||value.editor_client_id!==editorActor(context)||value.id!==digest({project_id:s.project_id,workspace_id:w.id,editor_client_id:value.editor_client_id,editor_window_id:id(value.editor_window_id)})||!['ACTIVE','RETIRED'].includes(value.phase)||!['OBSERVED','UNAVAILABLE','STALE'].includes(value.observation_state)||typeof value.current!=='boolean'||value.read_only!==true||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.automatic_replay!==false||value.atomic_editor_write_guarantee!==false)fail('EDITOR_OBSERVATION_INVALID','原编辑器观察身份不符。');
  if(value.capture_ref!=null)hash(value.capture_ref);
  if(value.current_scope!==undefined){const current=scope(value.current_scope,true);if(current.project_id!==s.project_id||!same(current.session_ref,s.session_ref)||value.current_scope_is_execution_authorization!==false)fail('EDITOR_OBSERVATION_INVALID','原观察范围不能转为执行授权。');}
  number(value.revision,1,2147483647);number(value.dirty_count,0,64);number(value.observed_documents,0,64);
  if(!Array.isArray(value.dirty_paths)||value.dirty_paths.length>4||value.dirty_paths.length>value.dirty_count||typeof value.dirty_paths_truncated!=='boolean'||value.dirty_paths_truncated!==(value.dirty_count!==value.dirty_paths.length))fail('EDITOR_OBSERVATION_INVALID','未保存内容摘要不完整。');for(const p of value.dirty_paths)editorObservedPath(p);
  editorProcessIdentity(identity);
  if(!Number.isSafeInteger(value.captured_at_epoch_ms)||value.captured_at_epoch_ms<=0||typeof value.received_at!=='string'||value.received_at.length>64||typeof value.observed_at!=='string'||value.observed_at.length>64||Buffer.byteLength(JSON.stringify(value))>8192)fail('EDITOR_OBSERVATION_INVALID','原编辑器观察过大。');return JSON.parse(JSON.stringify(value));
}
function editorObserverReceipt(value,s,context,operation,body=null){
  keys(value,['kind','id','session_ref','generation','request_sha256','editor_client_id','editor_window_id','status','action','observer','automatic_replay','workspace_writes','model_calls','native_posts','permission_granted','atomic_editor_write_guarantee','observation_time_scope']);
  if(value.kind!=='EDITOR_OBSERVER_ACTION'||value.id!==operation||!same(value.session_ref,s.session_ref)||value.generation!==s.generation||value.editor_client_id!==editorActor(context)||value.status!=='RETURNED'||!['OBSERVE','RETIRE'].includes(value.action)||value.automatic_replay!==false||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.permission_granted!==false||value.atomic_editor_write_guarantee!==false||value.observation_time_scope!=='AT_ORIGINAL_COMMAND_ONLY')fail('EDITOR_OBSERVATION_INVALID','原编辑器观察回执身份不符。');
  hash(value.request_sha256);const observed=editorObserverView(value.observer,s,context);
  if(value.editor_window_id!==observed.editor_window_id||observed.phase!==(value.action==='RETIRE'?'RETIRED':'ACTIVE')||body&&(value.request_sha256!==digest(body)||value.action!==body.action||value.editor_window_id!==body.editor_window_id||canonical(observed.workspace_ref)!==canonical(body.workspace_ref)||observed.revision!==body.expected_revision+1))fail('OPERATION_INPUT_MISMATCH','原编辑器观察请求不符，未重新提交。');return {...value,observer:observed};
}
function editorCaptureProjection(value,s,context,operation,body=null){
  if(!object(value)||!['EDITOR_CAPTURE_COHORT','EDITOR_CAPTURE_STATUS'].includes(value.kind)||value.id!==operation||value.editor_client_id!==editorActor(context)||!same(value.scope?.session_ref,s.session_ref)||value.scope?.project_id!==s.project_id||value.scope?.generation!==s.generation||value.read_only!==true||value.permission_granted!==false||value.automatic_replay!==false||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.atomic_editor_write_guarantee!==false)fail('EDITOR_CAPTURE_INVALID','原窗口核对身份不符。');
  const workspace=diffWorkspace(value.scope.workspace_ref);hash(value.capture_ref);hash(value.request_sha256);number(value.required_windows,0,16);number(value.expires_at_ms,1,Number.MAX_SAFE_INTEGER);
  if(body&&(value.request_sha256!==digest(body)||canonical(workspace)!==canonical(body.workspace_ref)))fail('OPERATION_INPUT_MISMATCH','原窗口核对输入不符，未重新请求。');
  const result={kind:value.kind,id:value.id,capture_ref:value.capture_ref,scope:{...s,workspace_ref:workspace},editor_client_id:value.editor_client_id,request_sha256:value.request_sha256,required_windows:value.required_windows,expires_at_ms:value.expires_at_ms,read_only:true,permission_granted:false,automatic_replay:false,workspace_writes:0,model_calls:0,native_posts:0,atomic_editor_write_guarantee:false};
  if(value.kind==='EDITOR_CAPTURE_COHORT'){if(value.observation_time_scope!=='IMMUTABLE_CAPTURE_INTENT_NOT_CURRENT_CLEAN_STATE')fail('EDITOR_CAPTURE_INVALID','原窗口请求不能冒当前保存状态。');result.observation_time_scope=value.observation_time_scope;result.created_at_ms=number(value.created_at_ms,1,Number.MAX_SAFE_INTEGER);}
  else{if(!['READY','WAITING','DIRTY','UNAVAILABLE','OWNER_ENDED','STALE','RESTARTED','MEMBERSHIP_CHANGED','TARGET_CHANGED','EXPIRED'].includes(value.state)||value.status_scope!=='CURRENT_METADATA_SNAPSHOT_NOT_EXECUTION_LEASE')fail('EDITOR_CAPTURE_INVALID','原窗口核对状态不完整。');result.state=value.state;result.status_scope=value.status_scope;for(const key of ['ready_windows','waiting_windows','dirty_windows','unavailable_windows'])result[key]=number(value[key],0,value.required_windows);if(value.state==='READY'&&(value.ready_windows!==value.required_windows||value.waiting_windows||value.dirty_windows||value.unavailable_windows))fail('EDITOR_CAPTURE_INVALID','窗口状态未完整核对。');}
  return result;
}
function editorCaptureDispatch(value){
  if(!Array.isArray(value.dispatch_requests)||value.dispatch_requests.length>16)fail('EDITOR_CAPTURE_INVALID','窗口采集请求过多。');
  return value.dispatch_requests.map(row=>{keys(row,['observer_id','editor_client_id','editor_window_id','project_id','capture_ref','expires_at_ms']);if(row.capture_ref!==value.capture_ref||row.expires_at_ms!==value.expires_at_ms)fail('EDITOR_CAPTURE_INVALID','窗口采集依据不符。');return {observer_id:hash(row.observer_id),editor_client_id:id(row.editor_client_id),editor_window_id:id(row.editor_window_id),project_id:id(row.project_id),capture_ref:hash(row.capture_ref),expires_at_ms:number(row.expires_at_ms,1,Number.MAX_SAFE_INTEGER)};});
}
function createIdeApiAdapter({request,openArtifact,reveal,notifyEditorCaptures,recordEditorCapture,waitEditorCapture,captureCursor,nativeDeliveryExports}={}){
  if(typeof request!=='function')throw new TypeError('Main-owned request is required');
  async function dispatch(method,params={},context={}){
    if(!METHODS.includes(method))fail('NOT_SUPPORTED','此功能尚未接入IDE。');
    if(method==='session.editor.capture.listen')fail('STREAM_TRANSPORT_REQUIRED','状态采集通知使用原配对流连接。');
    keys(params,['scope','payload','signal']);const p=params.payload??{};if(!object(p))fail('INVALID_PARAMS','需要结构化参数。');
    const signal=context.signal??params.signal;
    if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');
    if(method.startsWith('conversation.native.recovery.'))return canonicalNativeRecovery.dispatch(method,params,{...context,signal},request);
    if(method.startsWith('native.context.recovery.'))return nativeContextRecovery.dispatch(method,params,{...context,signal},request);
    if(method.startsWith('native.'))return nativeDispatch(method,params,{request,signal,openArtifact});
    if(method==='conversation.file.open')return canonicalInputOpen(params,context,{request,signal,openArtifact});
    if(method.startsWith('conversation.'))return canonicalConversations.dispatch(method,params,context,request,projectSession);
    const s=scope(params.scope??{},method.startsWith('session.')&&!['session.create'].includes(method),['session.message','session.guidance','session.cancel','session.question.reply','session.question.reject','session.permission.reply','session.child.message','session.diff.review','session.editor.reserve','session.editor.control'].includes(method));
    if(method.startsWith('session.context.recovery.'))return contextRecovery.dispatch(method,s,p,request,signal);
    const path=s.project_id?'/api/projects/'+encodeURIComponent(s.project_id)+'/agent-sessions'+(s.session_ref?'/'+encodeURIComponent(s.session_ref.id):''):null;
    const get=(route)=>request(route,undefined,signal);
    const read=async()=>{const raw=await get(path);projectSession(raw,s);return raw;};
    const write=async(route,body,kind)=>{
      let value,lookedUp=false;try{value=await request(route,body,signal);}catch(error){
        if(!body.original_work_ref&&Number.isInteger(error?.status)&&error.status>=400&&error.status<500)fail('API_REQUEST_REJECTED','原服务拒绝此操作；请核对原范围，草稿保留。');
        // A lost reply permits one GET of the exact original receipt, not POST.
        try{lookedUp=true;value=await request(path+'/operations/'+encodeURIComponent(body.operation_id),undefined,signal);}
        catch{fail('OPERATION_OUTCOME_UNKNOWN','结果尚未确认；请核查原操作，勿重复发送。');}
      }
      try{return projectOperation(value,s,body.operation_id,{body,kind});}catch(error){if(body.original_work_ref){if(!lookedUp)try{return projectOperation(await request(path+'/operations/'+encodeURIComponent(body.operation_id),undefined,signal),s,body.operation_id,{body,kind});}catch{}fail('OPERATION_OUTCOME_UNKNOWN','原工作续接回执尚未核对；原编号与输入保留，没有重新发送。');}throw error;}
    };
    if(method==='status'){
      keys(p,[]);try{
        const [config,catalog]=await Promise.all([get('/api/agent-sessions/configuration'),get('/api/planning/providers')]);
        const providers=(Array.isArray(catalog.providers)?catalog.providers:[]).slice(0,64).map(row=>({profile_ref:sourceRef(row.profile_ref),...selected(row,['provider_id','model_id','auth_kind','credential_present']),reasoning_efforts:(Array.isArray(row.reasoning_efforts)?row.reasoning_efforts:[]).slice(0,16).map(v=>clip(v,40))}));
        return{protocol:'eeas.ide-companion.v1',connected:true,configuration:{capability_binding_sha256:hash(config.capability_binding_sha256)},providers,
          capabilities:{original_sessions:true,running_guidance:true,exact_operation_cancel:true,historical_diffs:true,pending_replies:true,artifact_channel:'REGISTERED_ARTIFACTS_AND_WORKSPACE_FILES',child_continuation:true,child_write_review:true},
          configuration_status:providers.length?'EXPLICIT_PROVIDER_REQUIRED':'NOT_CONFIGURED',coverage:{providers_truncated:Array.isArray(catalog.providers)&&catalog.providers.length>64},limitations:['子任务写入沿原根范围和原权限核对；历史差异不代表当前磁盘或未保存编辑器，完整IDE差异操作仍在开发。']};
      }catch(error){if(error?.code==='REQUEST_CANCELLED')throw error;return{protocol:'eeas.ide-companion.v1',connected:false,configuration_status:'UNAVAILABLE',automatic_retry:false};}
    }
    if(method==='projects.list'){
      keys(p,[]);const value=await get('/api/projects');if(!Array.isArray(value.projects))fail('INVALID_UPSTREAM_RESPONSE','项目目录暂不可核对。');
      const eligible=value.projects.filter(row=>!['eeas.original-agent-session.internal-project.v1','EEAS_INTERNAL_DESKTOP_RUNTIME_V1'].includes(row.description));const projects=eligible.slice(0,256).map(row=>({...selected(row,['id','created_at']),name:clip(row.name,300),description:clip(row.description,1000)}));return{projects,coverage:{available:eligible.length,returned:projects.length,truncated:eligible.length>256}};
    }
    if(method==='sessions.list'){
      keys(p,[]);if(!s.project_id||s.session_ref||s.generation!=null)fail('EXACT_SCOPE_REQUIRED','请选择要读取的项目。');const value=await get(path);if(!Array.isArray(value.sessions))fail('INVALID_UPSTREAM_RESPONSE','会话目录暂不可核对。');
      const rows=value.sessions.slice(-128);if(rows.some(row=>row.project_id!==s.project_id))fail('INVALID_UPSTREAM_RESPONSE','会话目录包含不同项目，未采用。');return{sessions:rows.map(row=>projectSession(row,undefined,{summary:true})),coverage:{available:value.sessions.length,returned:rows.length,truncated:value.sessions.length>128}};
    }
    if(method==='session.get'){
      keys(p,[]);const raw=await read(),result=projectSession(raw,s),found=artifactCandidates(raw);let unavailable=found.unavailable;
      result.artifacts=[];
      for(const candidate of found.candidates){
        try{
          const item=await get('/api/projects/'+s.project_id+'/report-artifacts/'+candidate.manifest.id),expected=candidate.manifest;
          if(!object(item)||['id','project_id','revision','sha256','kind','media_type','filename','byte_length','download_url'].some(key=>item[key]!==expected[key])||
            typeof item.filename!=='string'||!item.filename||Array.from(item.filename).length>200||/[\x00-\x1f\\/:]/.test(item.filename)||!Number.isSafeInteger(item.byte_length)||item.byte_length<1||item.byte_length>16777216)throw Error('Registry mismatch');
          result.artifacts.push({...selected(item,['id','project_id','revision','sha256','filename','media_type','byte_length','download_url']),kind:'report',artifact_id:item.id,
            download_url:item.download_url,source:candidate.source,verification:'REGISTRY_BYTES_VERIFIED',visual_validation:'NOT_RUN'});
        }catch(error){if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');unavailable++;}
      }
      result.artifact_channel='REGISTERED_ARTIFACTS_AND_WORKSPACE_FILES';result.ide_coverage.artifacts={source:'COMPLETED_ROOT_TOOL_VERIFIED_REGISTRY_OR_EXACT_ORIGINAL_MESSAGE_FILE_LINK_IN_REGISTERED_WORKSPACE',available:found.available,returned:result.artifacts.length,unavailable,truncated:found.truncated,other_file_parts:'REFERENCE_ONLY'};
      if(raw.workspace_ref){try{const binding=await get('/api/projects/'+s.project_id+'/local-workspaces/'+id(raw.workspace_ref.id));if(!workspaceBinding(raw,binding))throw Error('Workspace binding changed');const files=workspaceFiles(raw,binding);result.artifacts.push(...files);result.ide_coverage.artifacts.returned=result.artifacts.length;result.ide_coverage.artifacts.workspace_files=files.length;result.ide_coverage.artifacts.file_bytes='READ_ONLY_ON_EXPLICIT_OPEN';}catch(error){if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');result.ide_coverage.artifacts.unavailable++;}}
      return result;
    }
    if(method==='session.operation'){keys(p,['operation_id']);const key=id(p.operation_id);return projectOperation(await get(path+'/operations/'+encodeURIComponent(key)),s,key);}
    if(method==='session.tool'){keys(p,['tool_ref']);return toolDetails.projectTool(await read(),s,p.tool_ref);}
    if(deliveryExports.METHODS.includes(method)){if(!nativeDeliveryExports)fail('NATIVE_SAVE_UNAVAILABLE','本机保存尚未就绪。');return nativeDeliveryExports.dispatch(method,{scope:s,payload:p},{...context,signal});}
    if(workSources.METHODS.includes(method))return workSources.dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants:context.currentGrants});
    if(workMaintenance.METHODS.includes(method))return workMaintenance.dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants:context.currentGrants});
    if(workDeliveries.METHODS.includes(method))return workDeliveries.dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants:context.currentGrants});
    if(workRetests.METHODS.includes(method))return workRetests.dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants:context.currentGrants});
    if(workTestCases.METHODS.includes(method))return workTestCases.dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants:context.currentGrants});
    if(workRequirements.METHODS.includes(method))return workRequirements.dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants:context.currentGrants});
    if(['session.tool.reports','session.tool.report'].includes(method))return commandReports.dispatch(method,{scope:s,payload:p,
      read:async()=>{const raw=await get(path+'/snapshot');projectSession(raw,s);return raw;},
      get,request,path,signal,currentGrants:context.currentGrants});
    if(method==='session.child'){
      keys(p,['child_ref']);const reference=childRef(p.child_ref),raw=await read();
      const children=(raw.children||[]).filter(child=>child.id===reference.id);
      if(children.length!==1||canonical(childReference(raw,children[0]))!==canonical(reference))fail('CHILD_CHANGED','原子任务身份已变化，请刷新。');
      const query=new URLSearchParams({session_sha256:s.session_ref.sha256,generation:String(s.generation),child_sha256:reference.sha256,root_native_session_id:reference.root_native_session_id});
      const value=await get(path+'/children/'+encodeURIComponent(reference.id)+'?'+query.toString());
      if(!object(value)||value.kind!=='ORIGINAL_CHILD_VIEW'||canonical(value.scope)!==canonical(s)||canonical(value.child_ref)!==canonical(reference)||
        value.read_only!==true||value.native_posts!==0||value.model_calls!==0||value.new_owner_started!==false||value.transcript_copied!==false||
        !Array.isArray(value.messages)||value.messages.length>40||!object(value.coverage)||value.coverage.history_complete!==false)fail('CHILD_CHANGED','原子任务读取回执不匹配。');
      const projected=projectSession({...raw,original_session_id:reference.id,messages:value.messages},s);
      for(const message of projected.messages){if(message.info.sessionID!==reference.id)fail('CHILD_CHANGED','子任务记录属于另一会话。');for(const part of message.parts)delete part.tool_ref;}
      return {kind:'ORIGINAL_CHILD_VIEW',scope:s,child_ref:reference,title:clip(value.title,300),messages:projected.messages,
        coverage:{...value.coverage,display:projected.ide_coverage.messages},observation_is_current:value.observation_is_current===true,
        read_only:true,model_calls:0,native_posts:0,new_owner_started:false,transcript_copied:false};
    }
    if(method==='session.child.message'){
      keys(p,['operation_id','child_ref','raw_text','timeout_seconds','expected_active_operation_id']);
      const body={operation_id:id(p.operation_id),session_ref:s.session_ref,generation:s.generation,child_ref:childRef(p.child_ref),
        raw_text:text(p.raw_text,16000),timeout_seconds:number(p.timeout_seconds??300,10,300),
        expected_active_operation_id:p.expected_active_operation_id==null?null:id(p.expected_active_operation_id)};
      const latest=await read(),matches=(latest.children||[]).filter(child=>child.id===body.child_ref.id);
      if(latest.owner_status!=='READY'||latest.connected_owner!==true||latest.configuration_current!==true||latest.observation_is_current!==true)fail('SESSION_NOT_READY','原子任务owner尚未就绪，输入已保留。');
      if(matches.length!==1||canonical(childReference(latest,matches[0]))!==canonical(body.child_ref)||
         (latest.active_operation_id??null)!==body.expected_active_operation_id)fail('CHILD_CHANGED','子任务或运行身份已变化，输入已保留。');
      const kind=body.expected_active_operation_id===null?'CHILD_MESSAGE':'CHILD_GUIDANCE';
      if(kind==='CHILD_GUIDANCE'){
        const direct=latest.active_child_operation?.operation_id===body.expected_active_operation_id&&canonical(latest.active_child_operation.child_ref)===canonical(body.child_ref),
          delegated=!latest.active_child_operation&&(latest.child_guidance_targets||[]).some(target=>target.parent_operation_id===body.expected_active_operation_id&&target.generation===s.generation&&target.status==='RUNNING_FOREGROUND'&&canonical(target.child_ref)===canonical(body.child_ref));
        if(!direct&&!delegated)fail('CHILD_CHANGED','原指导属于另一子任务或该委派已结束。');
      }
      let value;
      try{value=await request(path+'/child-messages',body,signal);}
      catch(error){
        // Even an API 409 can follow an uncertain native POST. Inspect the
        // original child receipt before interpreting rejection as non-dispatch.
        try{value=await request(path+'/operations/'+encodeURIComponent(body.operation_id),undefined,signal);}
        catch(observation){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&observation?.status===404)
          fail('API_REQUEST_REJECTED','原服务拒绝了此输入，草稿已保留。');
          fail('OPERATION_OUTCOME_UNKNOWN','原子任务发送结果待核查，请勿重发。');}
      }
      return projectOperation(value,s,body.operation_id,{body,kind});
    }
    if(method==='session.editor.capture.wait'){
      keys(p,['operation_id','capture_ref']);const operation=id(p.operation_id),reference=hash(p.capture_ref),deadline=Date.now()+8000;
      if(typeof waitEditorCapture!=='function'||typeof captureCursor!=='function')fail('EDITOR_CAPTURE_CHANNEL_REQUIRED','原窗口采集连接尚未就绪。');
      let value;for(let checks=0;checks<32;checks++){
        if(signal?.aborted)fail('REQUEST_CANCELLED','原窗口核对已停止等待，未重发。');const cursor=captureCursor(reference);
        const raw=await get(path+'/editor-capture-cohorts/'+encodeURIComponent(operation)+'/status?'+new URLSearchParams({editor_client_id:editorActor(context),include_dispatch:'true'}));
        value=editorCaptureProjection(raw,s,context,operation);if(value.kind!=='EDITOR_CAPTURE_STATUS'||value.capture_ref!==reference)fail('OPERATION_INPUT_MISMATCH','原窗口核对目的不符，未重新请求。');
        if(value.state!=='WAITING')return value;if(typeof notifyEditorCaptures==='function')await notifyEditorCaptures(editorCaptureDispatch(raw),{signal});
        const remaining=Math.min(deadline-Date.now(),value.expires_at_ms-Date.now());if(remaining<=0)return {...value,wait_deadline_reached:true};
        if(!await waitEditorCapture(reference,{after:cursor,ms:remaining,signal})){
          const final=editorCaptureProjection(await get(path+'/editor-capture-cohorts/'+encodeURIComponent(operation)+'/status?'+new URLSearchParams({editor_client_id:editorActor(context)})),s,context,operation);
          if(final.kind!=='EDITOR_CAPTURE_STATUS'||final.capture_ref!==reference)fail('OPERATION_INPUT_MISMATCH','原窗口核对目的不符。');return {...final,wait_deadline_reached:final.state==='WAITING'};
        }
      }return {...value,wait_deadline_reached:true};
    }
    if(['session.editor.capture.request','session.editor.capture.operation','session.editor.capture.status'].includes(method)){
      let value,operation,body=null;
      if(method==='session.editor.capture.request'){
        keys(p,['operation_id','workspace_ref','permission_ref']);let permission=null;
        if(p.permission_ref!=null){keys(p.permission_ref,['id','generation','sha256']);if(!/^per_[A-Za-z0-9_-]{1,100}$/.test(p.permission_ref.id??''))fail('INVALID_PENDING_REFERENCE','需要准确原权限引用。');permission={id:p.permission_ref.id,generation:number(p.permission_ref.generation,1,2147483647),sha256:hash(p.permission_ref.sha256)};if(permission.generation!==s.generation)fail('STALE_PENDING_REFERENCE','原权限代次已变化。');}
        operation=id(p.operation_id);body={operation_id:operation,session_ref:s.session_ref,generation:number(s.generation,0,2147483647),workspace_ref:diffWorkspace(p.workspace_ref),editor_client_id:editorActor(context),permission_ref:permission};
        try{value=await request(path+'/editor-capture-cohorts',body,signal);}catch(error){try{value=await get(path+'/editor-capture-cohorts/'+encodeURIComponent(operation)+'?'+new URLSearchParams({editor_client_id:editorActor(context)}));}catch(missing){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&missing?.status===404)fail('API_REQUEST_REJECTED','窗口保存状态核对未受理，原输入保留。');fail('OPERATION_OUTCOME_UNKNOWN','原窗口核对结果待查询，未重新请求。');}}
      }else{keys(p,['operation_id']);operation=id(p.operation_id);value=await get(path+'/editor-capture-cohorts/'+encodeURIComponent(operation)+(method.endsWith('.status')?'/status':'')+'?'+new URLSearchParams({editor_client_id:editorActor(context),...(method.endsWith('.status')?{include_dispatch:'true'}:{})}));}
      if(method.endsWith('.status')!==(value?.kind==='EDITOR_CAPTURE_STATUS'))fail('EDITOR_CAPTURE_INVALID','原窗口请求与当前观察不能混用。');
      const publicValue=editorCaptureProjection(value,s,context,operation,body);
      if(method.endsWith('.status')&&typeof notifyEditorCaptures==='function'&&value.state==='WAITING')await notifyEditorCaptures(editorCaptureDispatch(value),{signal});
      return publicValue;
    }
    if(method==='session.editor.observer.recovery.prepare'){
      keys(p,['workspace_ref','observer_id','editor_window_id','editor_process_id','editor_parent_process_id']);const body=observerRecoveryAddress(p,s,context),value=await request(path+'/editor-observer-recovery/prepare',body,signal);
      keys(value,['kind','scope','observer_id','revision','phase','runtime_id','editor_client_id','editor_window_id','process_identity','original_process_identity','original_editor_client_id','original_editor_window_id','original_process_state','root_identity','root_path_sha256','preparation_sha256','native_root','read_only','workspace_writes','model_calls','native_posts','permission_granted','automatic_replay','current_scope_is_execution_authorization']);
      const basis={scope:value.scope,observer_id:value.observer_id,revision:value.revision,phase:value.phase,runtime_id:value.runtime_id,editor_client_id:value.editor_client_id,editor_window_id:value.editor_window_id,process_identity:value.process_identity,original_process_identity:value.original_process_identity,original_editor_client_id:value.original_editor_client_id,original_editor_window_id:value.original_editor_window_id,original_process_state:value.original_process_state,root_identity:value.root_identity,root_path_sha256:value.root_path_sha256};
      const current=scope({project_id:value.scope?.project_id,session_ref:value.scope?.session_ref,generation:value.scope?.generation},true);
      if(value.kind!=='EDITOR_OBSERVER_RECOVERY_PREPARATION'||current.project_id!==s.project_id||!same(current.session_ref,s.session_ref)||!same(value.scope.workspace_ref,body.workspace_ref)||value.observer_id!==body.observer_id||value.editor_client_id!==body.editor_client_id||value.editor_window_id!==body.editor_window_id||!['ACTIVE','RETIRED'].includes(value.phase)||!['SAME_PROCESS','ENDED','OTHER_LIVE'].includes(value.original_process_state)||value.original_process_state==='OTHER_LIVE'&&value.phase!=='RETIRED'||value.read_only!==true||value.workspace_writes!==0||value.native_posts!==0||value.model_calls!==0||value.permission_granted!==false||value.automatic_replay!==false||value.current_scope_is_execution_authorization!==false||typeof value.native_root!=='string'||!paths.isAbsolute(value.native_root)||value.native_root.length>4096||value.root_path_sha256!==digest(value.native_root)||value.preparation_sha256!==digest(basis))fail('EDITOR_OBSERVATION_INVALID','原保存状态恢复准备不符。');
      editorRootIdentity(value.root_identity);editorProcessIdentity(value.process_identity);editorProcessIdentity(value.original_process_identity);id(value.runtime_id);number(value.revision,1,2147483646);
      if(value.process_identity.pid!==body.editor_process_id||value.process_identity.parent_pid!==body.editor_parent_process_id||value.observer_id!==digest({project_id:s.project_id,workspace_id:body.workspace_ref.id,editor_client_id:id(value.original_editor_client_id),editor_window_id:id(value.original_editor_window_id)})||(value.original_process_state==='SAME_PROCESS')!==same(value.process_identity,value.original_process_identity))fail('EDITOR_OBSERVATION_INVALID','原进程恢复身份不符。');
      const {native_root,...publicValue}=value;return {...publicValue,root_uri:pathToFileURL(native_root).href};
    }
    if(method==='session.editor.observer.recovery.command'){
      keys(p,['workspace_ref','observer_id','editor_window_id','editor_process_id','editor_parent_process_id','operation_id','expected_revision','preparation_sha256','observation','user_preserved_original']);
      if(p.user_preserved_original!==true)fail('INVALID_PARAMS','请先保留原内容，再结束保护。');
      const observation=editorObservation(p.observation);if(observation.permission_ref||observation.capture_ref)fail('INVALID_PARAMS','保存状态恢复不能借用执行权限。');
      // The recovery schema retains its explicit null field in its own hash.
      observation.capture_ref=null;
      const body={...observerRecoveryAddress(p,s,context),operation_id:id(p.operation_id),expected_revision:number(p.expected_revision,1,2147483646),preparation_sha256:hash(p.preparation_sha256),observation,user_preserved_original:true};
      let value;try{value=await request(path+'/editor-observer-recoveries',body,signal);}catch(error){
        try{value=await get(path+'/editor-observer-recoveries/'+encodeURIComponent(body.operation_id)+'?'+new URLSearchParams({editor_client_id:editorActor(context)}));}
        catch(missing){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&missing?.status===404)fail('API_REQUEST_REJECTED','恢复未保存，请核对原窗口。');fail('OPERATION_OUTCOME_UNKNOWN','原恢复结果待核查，未重复发送。');}
      }const result=observerRecoveryReceipt(value,s,context,body.operation_id,body);if(typeof recordEditorCapture==='function')recordEditorCapture({capture_ref:null,observer_id:result.observer_id,revision:result.revision});return result;
    }
    if(method==='session.editor.observer.recovery.operation'){
      keys(p,['operation_id']);const operation=id(p.operation_id);return observerRecoveryReceipt(await get(path+'/editor-observer-recoveries/'+encodeURIComponent(operation)+'?'+new URLSearchParams({editor_client_id:editorActor(context)})),s,context,operation);
    }
    if(method==='session.editor.observer.prepare'){
      keys(p,['workspace_ref','editor_window_id','editor_process_id','editor_parent_process_id','action']);const body=editorObserverAddress(p,s,context),value=await request(path+'/editor-observers/prepare',body,signal);
      const basis={scope:value.scope,observer_id:value.observer_id,revision:value.revision,runtime_id:value.runtime_id,action:value.action,editor_client_id:value.editor_client_id,editor_window_id:value.editor_window_id,editor_process_id:value.editor_process_id,editor_parent_process_id:value.editor_parent_process_id,root_identity:value.root_identity,root_path_sha256:value.root_path_sha256,process_identity:value.process_identity};
      if(value.kind!=='EDITOR_OBSERVER_PREPARATION'||canonical(value.scope)!==canonical({...s,workspace_ref:body.workspace_ref})||value.observer_id!==digest({project_id:s.project_id,workspace_id:body.workspace_ref.id,editor_client_id:body.editor_client_id,editor_window_id:body.editor_window_id})||value.action!==body.action||value.editor_client_id!==body.editor_client_id||value.editor_window_id!==body.editor_window_id||value.editor_process_id!==body.editor_process_id||value.editor_parent_process_id!==body.editor_parent_process_id||value.read_only!==true||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.automatic_replay!==false||typeof value.native_root!=='string'||!paths.isAbsolute(value.native_root)||value.native_root.length>4096||value.root_path_sha256!==digest(value.native_root)||value.preparation_sha256!==digest(basis))fail('EDITOR_OBSERVATION_INVALID','原编辑器观察准备身份不符。');
      number(value.revision,0,2147483646);id(value.runtime_id);editorRootIdentity(value.root_identity);editorProcessIdentity(value.process_identity);if(value.process_identity.pid!==body.editor_process_id||value.process_identity.parent_pid!==body.editor_parent_process_id)fail('EDITOR_OBSERVATION_INVALID','原进程准备身份不符。');
      return {kind:value.kind,...basis,preparation_sha256:value.preparation_sha256,root_uri:pathToFileURL(value.native_root).href,read_only:true,workspace_writes:0,model_calls:0,native_posts:0,automatic_replay:false};
    }
    if(method==='session.editor.observer.command'){
      keys(p,['workspace_ref','editor_window_id','editor_process_id','editor_parent_process_id','action','operation_id','expected_revision','preparation_sha256','observation','user_approved']);
      if(typeof p.user_approved!=='boolean')fail('INVALID_PARAMS','需要明确的观察状态。');
      const body={...editorObserverAddress(p,s,context),operation_id:id(p.operation_id),expected_revision:number(p.expected_revision,0,2147483646),preparation_sha256:hash(p.preparation_sha256),observation:editorObservation(p.observation),user_approved:p.user_approved};
      let value;try{value=await request(path+'/editor-observer-actions',body,signal);}catch(error){
        try{value=await get(path+'/editor-observer-actions/'+encodeURIComponent(body.operation_id)+'?'+new URLSearchParams({editor_client_id:editorActor(context)}));}
        catch(missing){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&missing?.status===404)fail('API_REQUEST_REJECTED','编辑器观察未保存，请保留当前内容。');fail('OPERATION_OUTCOME_UNKNOWN','原编辑器观察结果待核查，未重复发送。');}
      }const result=editorObserverReceipt(value,s,context,body.operation_id,body);if(typeof recordEditorCapture==='function')recordEditorCapture({capture_ref:result.observer.capture_ref??null,observer_id:result.observer.id,revision:result.observer.revision});return result;
    }
    if(method==='session.editor.observer.operation'){
      keys(p,['operation_id']);const operation=id(p.operation_id);return editorObserverReceipt(await get(path+'/editor-observer-actions/'+encodeURIComponent(operation)+'?'+new URLSearchParams({editor_client_id:editorActor(context)})),s,context,operation);
    }
    if(method==='session.editor.observer.get'){
      keys(p,['observer_id']);return editorObserverView(await get('/api/projects/'+encodeURIComponent(s.project_id)+'/editor-observers/'+hash(p.observer_id)+'?'+new URLSearchParams({editor_client_id:editorActor(context)})),s,context);
    }
    if(method==='session.editor.file.prepare'){
      keys(p,['target_operation_id','lease_ref','editor_window_id','editor_nonce','expected_revision','action']);const body=editorFileAddress(p,s,context);
      const lease=editorProjection(await get(path+'/editor-changes/'+encodeURIComponent(body.target_operation_id)+'?'+new URLSearchParams({editor_client_id:editorActor(context)})),s,context,{operation:body.target_operation_id});
      if(!lease.grant||lease.revision!==body.expected_revision||!same(lease.lease_ref,body.lease_ref))fail('EDITOR_CHANGE_MISMATCH','原文件交接已变化。');
      const value=await request(path+'/editor-files/prepare',body,signal);return editorFilePreparation(value,s,body,lease);
    }
    if(method==='session.editor.file.operation'){
      keys(p,['operation_id']);const operation=id(p.operation_id),query=new URLSearchParams({editor_client_id:editorActor(context)});
      return editorFileReceipt(await get(path+'/editor-file-actions/'+encodeURIComponent(operation)+'?'+query),s,context,operation);
    }
    if(method==='session.editor.file.command'){
      keys(p,['target_operation_id','lease_ref','editor_window_id','editor_nonce','expected_revision','action','operation_id','preparation_sha256','editor_observation','user_approved']);const body=editorFileCommandBody(p,s,context);let value;
      try{value=await request(path+'/editor-file-actions',body,signal);}catch(error){
        try{value=await get(path+'/editor-file-actions/'+encodeURIComponent(body.operation_id)+'?'+new URLSearchParams({editor_client_id:editorActor(context)}));}
        catch(missing){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&missing?.status===404)fail('API_REQUEST_REJECTED','原文件操作未获准，原稿保留。');fail('OPERATION_OUTCOME_UNKNOWN','原文件操作结果待核查，未重新提交。');}
      }
      return editorFileReceipt(value,s,context,body.operation_id,body);
    }
    if(method==='session.editor.get'){
      keys(p,['operation_id']);const op=id(p.operation_id),query=new URLSearchParams({editor_client_id:editorActor(context)});
      return {...editorProjection(await get(path+'/editor-changes/'+encodeURIComponent(op)+'?'+query),s,context,{operation:op}),first_reservation_reply:false};
    }
    if(method==='session.editor.reserve'){
      keys(p,['operation_id','workspace_ref','snapshot_ref','diff_ref','child_ref','preparation_sha256','direction','editor_window_id','editor_nonce']);const body=editorSelection(p,s,context);
      if(!['RESTORE_BEFORE','APPLY_AFTER'].includes(body.direction))fail('INVALID_PARAMS','请选择原历史修改前或修改后版本。');
      let value,fresh=true;try{value=await request(path+'/editor-changes',body,signal);}catch(error){fresh=false;try{value=await get(path+'/editor-changes/'+encodeURIComponent(body.operation_id)+'?'+new URLSearchParams({editor_client_id:editorActor(context)}));}catch(missing){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&missing?.status===404)fail('API_REQUEST_REJECTED','编辑交接未获准，原文件保留。');fail('OPERATION_OUTCOME_UNKNOWN','原编辑交接结果待核查，未重新提交。');}}
      const result=editorProjection(value,s,context,{operation:body.operation_id,expected:body});result.first_reservation_reply=result.first_reservation_reply&&fresh;
      if(result.first_reservation_reply&&result.phase==='READY'&&result.grant.mode==='TEXT_REPLACE'){
        if(typeof openArtifact!=='function')fail('EDITOR_SOURCE_UNAVAILABLE','原编辑器文件解析不可用，请核查原交接。');
        const opened=await openArtifact({kind:'workspace-file',target:'editor',project_id:s.project_id,file_source_ref:result.grant.source_ref},{signal,scope:s});
        if(opened?.status!=='RESOLVED'||canonical(opened.file_source_ref)!==canonical(result.grant.source_ref)||typeof opened.native_path!=='string'||!paths.isAbsolute(opened.native_path))fail('EDITOR_SOURCE_UNAVAILABLE','原编辑器文件版本已变化，请核查原交接。');
        result.editor_uri=pathToFileURL(opened.native_path).href;
      }
      return result;
    }
    if(method==='session.editor.control.operation'){
      keys(p,['operation_id']);const op=id(p.operation_id);return {...editorControlReceipt(await get(path+'/editor-change-controls/'+encodeURIComponent(op)+'?'+new URLSearchParams({editor_client_id:editorActor(context)})),s,context,op),first_control_reply:false};
    }
    if(method==='session.editor.control'){
      keys(p,['operation_id','target_operation_id','lease_ref','editor_window_id','editor_nonce','expected_revision','action','editor_basis','expected_saved_sha256','user_approved_manual_saved']);const body=editorControlBody(p,s,context);
      let value,fresh=true;try{value=await request(path+'/editor-change-controls',body,signal);}catch(error){fresh=false;try{value=await get(path+'/editor-change-controls/'+encodeURIComponent(body.operation_id)+'?'+new URLSearchParams({editor_client_id:editorActor(context)}));}catch(missing){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&missing?.status===404)fail('API_REQUEST_REJECTED','原编辑控制未获准，请核查原交接。');fail('OPERATION_OUTCOME_UNKNOWN','原编辑控制结果待核查，未重新提交。');}}
      const result=editorControlReceipt(value,s,context,body.operation_id,body);result.first_control_reply=result.first_control_reply&&fresh;return result;
    }
    if(method==='session.diff.prepare'){
      keys(p,['workspace_ref','snapshot_ref','diff_ref','child_ref']);const selection=reviewSelection(p,s);
      const value=await request(path+'/diff-reviews/prepare',selection,signal);
      return reviewPreparation(value,s,selection);
    }
    if(method==='session.diff.review.operation'){
      keys(p,['operation_id']);const op=id(p.operation_id);return reviewReceipt(await get(path+'/diff-reviews/'+encodeURIComponent(op)),s,op);
    }
    if(method==='session.diff.review'){
      keys(p,['operation_id','workspace_ref','snapshot_ref','diff_ref','child_ref','preparation_sha256','expected_review_revision','decision']);
      const selection=reviewSelection(p,s),body={...selection,operation_id:id(p.operation_id),preparation_sha256:hash(p.preparation_sha256),expected_review_revision:number(p.expected_review_revision,0,1024),decision:p.decision};
      if(!['ACCEPT','REJECT'].includes(body.decision))fail('INVALID_PARAMS','请选择明确的审阅决定。');
      let value;try{value=await request(path+'/diff-reviews',body,signal);}catch(error){
        try{value=await get(path+'/diff-reviews/'+encodeURIComponent(body.operation_id));}
        catch(missing){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&missing?.status===404)fail('API_REQUEST_REJECTED','原服务拒绝审阅决定，请刷新原记录。');fail('OPERATION_OUTCOME_UNKNOWN','原审阅结果待核查，未重新提交。');}
      }
      return reviewReceipt(value,s,body.operation_id,body);
    }
    if(method==='session.child.diff'||method==='session.child.diff.file'){
      number(s.generation,1,2147483647);const file=method==='session.child.diff.file';keys(p,file?['child_ref','workspace_ref','snapshot_ref','diff_ref']:['child_ref','workspace_ref']);
      const reference=childRef(p.child_ref),workspace=diffWorkspace(p.workspace_ref),raw=await read();
      const matches=(raw.children||[]).filter(child=>child.id===reference.id);
      if(matches.length!==1||canonical(childReference(raw,matches[0]))!==canonical(reference))fail('CHILD_CHANGED','原子任务身份已变化，请刷新。');
      const query=new URLSearchParams({session_sha256:s.session_ref.sha256,generation:String(s.generation),child_sha256:reference.sha256,root_native_session_id:reference.root_native_session_id,workspace_id:workspace.id,binding_revision:String(workspace.revision),binding_sha256:workspace.binding_sha256});
      const snapshot=file?diffReference(p.snapshot_ref):undefined,diff=file?diffReference(p.diff_ref):undefined;
      if(file){if(snapshot.id!==digest({scope:{...s,workspace_ref:workspace},child_ref:reference}))fail('STALE_SESSION_SCOPE','原子任务快照已变化，请重新读取。');query.set('snapshot_sha256',snapshot.sha256);query.set('diff_sha256',diff.sha256);query.set('diff_id',diff.id);}
      let value;try{value=await get(path+'/children/'+encodeURIComponent(reference.id)+'/diffs?'+query.toString());}
      catch(error){if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');fail(error?.status===409?'DIFF_CHANGED':'DIFF_UNAVAILABLE','子任务改动暂不可用，请刷新原记录。');}
      return diffProjection(value,s,workspace,{snapshot,diff,file,child:reference});
    }
    if(method==='session.diff'||method==='session.diff.file'){
      const file=method==='session.diff.file';keys(p,file?['workspace_ref','snapshot_ref','diff_ref']:['workspace_ref']);
      const workspace=diffWorkspace(p.workspace_ref),query=new URLSearchParams({session_sha256:s.session_ref.sha256,generation:String(s.generation),workspace_id:workspace.id,binding_revision:String(workspace.revision),binding_sha256:workspace.binding_sha256});
      const snapshot=file?diffReference(p.snapshot_ref):undefined,diff=file?diffReference(p.diff_ref):undefined;
      if(file){if(snapshot.id!==digest({...s,workspace_ref:workspace}))fail('STALE_SESSION_SCOPE','原会话或代次已变化，请重新读取；草稿仍保留。');query.set('snapshot_sha256',snapshot.sha256);query.set('diff_sha256',diff.sha256);}
      let value;try{value=await get(path+'/diffs'+(file?'/'+diff.id:'')+'?'+query.toString());}
      catch(error){if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');if(error?.status===409)fail('DIFF_CHANGED','文件已更新，请刷新');fail('DIFF_UNAVAILABLE','部分文件暂不可用');}
      return diffProjection(value,s,workspace,{snapshot,diff,file});
    }
    if(method==='session.events'){
      keys(p,['after','limit']);await read();const after=number(p.after??0,0,Number.MAX_SAFE_INTEGER),limit=number(p.limit??100,1,200);const value=await get(path+'/events?after='+after+'&limit='+limit);
      if(!Array.isArray(value.events)||value.native_replay!==false)fail('INVALID_UPSTREAM_RESPONSE','原事件回执暂不可核对。');
      return{events:value.events.slice(0,limit).map(row=>({...selected(row,['seq','type','created_at']),payload:selected(row.payload,['generation','original_session_id','operation_id','status','error_type']),payload_coverage:'SAFE_METADATA_ONLY'})),last_event_seq:number(value.last_event_seq,0,Number.MAX_SAFE_INTEGER),native_replay:false,coverage:{source:'SAVED_SESSION_EVENT_HISTORY',generation_filter:'NOT_AVAILABLE_SESSION_HISTORY_MAY_INCLUDE_OLDER_GENERATIONS',truncated:value.events.length>limit}};
    }
    if(method==='session.create'){
      keys(p,['operation_id','workspace_ref','provider_profile_ref','capability_binding_sha256','workflow_ref','workflow_provider_refs','title','ttl_seconds','max_calls','max_output_tokens','allowed_effects','command_allowlist','mcp_tool_allowlist','network_mode']);
      if(s.session_ref||s.generation!=null)fail('INVALID_PARAMS','新会话只能选择项目，不接管原owner。');
      const body={operation_id:id(p.operation_id),project_id:s.project_id??null,provider_profile_ref:ref(p.provider_profile_ref),capability_binding_sha256:hash(p.capability_binding_sha256)};
      if(p.workspace_ref!=null){keys(p.workspace_ref,['id','revision','binding_sha256']);body.workspace_ref={id:hash(p.workspace_ref.id),revision:number(p.workspace_ref.revision,1,2147483647),binding_sha256:hash(p.workspace_ref.binding_sha256)};}
      for(const key of ['title','ttl_seconds','max_calls','max_output_tokens','allowed_effects','command_allowlist','mcp_tool_allowlist','network_mode','workflow_ref','workflow_provider_refs'])if(p[key]!=null)body[key]=p[key];
      // Existing API validates each field and explicit permission, never default
      // to a profile, paid route, workspace or new execution from IDE metadata.
      let value;try{value=await request('/api/agent-sessions/open',body,signal);}catch(error){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500)fail('API_REQUEST_REJECTED','原服务拒绝创建；请在App核对配置。');fail('CREATION_OUTCOME_UNKNOWN','创建结果尚未确认；没有自动重开，请在App核查原创建操作。');}
      if((s.project_id&&value.project_id!==s.project_id)||!same(value.provider_profile_ref,body.provider_profile_ref)||canonical(value.workspace_ref??null)!==canonical(body.workspace_ref??null))fail('STALE_SESSION_SCOPE','新会话回执的项目、模型或工作目录与原请求不同。');return projectSession(value);
    }
    if(method==='session.message'){
      keys(p,['operation_id','raw_text','agent','timeout_seconds','original_work_ref','expected_active_operation_id']);if(Object.hasOwn(p,'expected_active_operation_id')){if(!p.original_work_ref)fail('INVALID_PARAMS','活动依据仅用于明确的原工作输入。');if(p.expected_active_operation_id!==null)id(p.expected_active_operation_id);}const operation_id=id(p.operation_id),raw_text=text(p.raw_text,16000),agent=p.agent??'build';if(!['plan','build','workflow'].includes(agent))fail('INVALID_PARAMS','请选择已支持的原Agent。');let work;try{work=originalWorkRef(p.original_work_ref);}catch{fail('INVALID_PARAMS','原工作引用不完整，原稿保留。');}const extra=work?{original_work_ref:work}:{},local=!!work&&originalWorkStatusText(raw_text),timeout_seconds=number(p.timeout_seconds??60,10,300);const latest=await read();if(Object.hasOwn(p,'expected_active_operation_id')&&(latest.active_operation_id??null)!==p.expected_active_operation_id)fail('WORK_INPUT_ACTIVE_CHANGED','活动执行已变化，尚未提交；输入与原编号保留。');
      if(!local&&(latest.owner_status!=='READY'||latest.connected_owner!==true||latest.configuration_current!==true||latest.observation_is_current!==true))fail('SESSION_NOT_READY','原会话当前不可发送；请在App核对连接与配置。');
      if(!local&&latest.active_child_operation)fail('CHILD_CONTEXT_REQUIRED','请在原子任务记录中继续，当前输入已保留。');
      if(latest.active_operation_id||local){const body={operation_id,session_ref:s.session_ref,generation:s.generation,raw_text,intent:'AUTO',expected_active_operation_id:latest.active_operation_id?id(latest.active_operation_id):null,...extra};return write(path+'/guidance',body,'GUIDANCE');}
      return write(path+'/messages',{operation_id,session_ref:s.session_ref,generation:s.generation,raw_text,agent,timeout_seconds,...extra},'MESSAGE');
    }
    if(method==='session.guidance'){
      keys(p,['operation_id','raw_text','intent','expected_active_operation_id','original_work_ref']);const intent=p.intent??'AUTO';if(!['AUTO','STEER','STATUS'].includes(intent))fail('INVALID_PARAMS','指导意图不受支持。');let work;try{work=originalWorkRef(p.original_work_ref);}catch{fail('INVALID_PARAMS','原工作引用不完整，原稿保留。');}
      const body={operation_id:id(p.operation_id),session_ref:s.session_ref,generation:s.generation,raw_text:text(p.raw_text,16000),intent,expected_active_operation_id:p.expected_active_operation_id==null?null:id(p.expected_active_operation_id),...(work?{original_work_ref:work}:{})};
      return write(path+'/guidance',body,'GUIDANCE');
    }
    if(['session.question.reply','session.question.reject','session.permission.reply'].includes(method)){
      const permission=method==='session.permission.reply',reject=method==='session.question.reject';
      keys(p,['operation_id','request_ref',...(permission?['reply']:reject?[]:['answers'])]);
      keys(p.request_ref,['id','sha256','generation']);
      const requestId=p.request_ref.id;
      if(typeof requestId!=='string'||!(permission?/^per_[A-Za-z0-9_-]{1,100}$/:/^que_[A-Za-z0-9_-]{1,100}$/).test(requestId))fail('INVALID_PENDING_REFERENCE','请选择准确的待答或权限请求。');
      const request_ref={id:requestId,sha256:hash(p.request_ref.sha256),generation:number(p.request_ref.generation,1,2147483647)};
      if(request_ref.generation!==s.generation)fail('STALE_PENDING_REFERENCE','待处理请求属于旧代次。');
      const latest=await read(),items=permission?latest.pending_permissions:latest.pending_questions;
      const selectedRequest=Array.isArray(items)&&items.find(item=>canonical(item.request_ref)===canonical(request_ref));
      if(!selectedRequest)fail('STALE_PENDING_REFERENCE','原请求已变化或已处理，请刷新。');
      if(require('./ide-pending-controls.cjs').projectPending(selectedRequest).display_complete!==true)fail('INCOMPLETE_PENDING_REQUEST','原请求不能完整显示，请在桌面 App 核查。');
      const body={operation_id:id(p.operation_id),session_ref:s.session_ref,generation:s.generation,request_ref};
      if(permission){
        if(!['once','always','reject'].includes(p.reply)||!selectedRequest.allowed_choices?.includes(p.reply))fail('PERMISSION_CHOICE_DENIED','原权限策略不允许该答复。');
        body.reply=p.reply;
      }else if(!reject){
        const questions=selectedRequest.native_request?.questions;
        if(!Array.isArray(questions)||!Array.isArray(p.answers)||p.answers.length!==questions.length||p.answers.length<1||p.answers.length>16)fail('INVALID_ANSWERS','请逐项回答原问题。');
        body.answers=p.answers.map((answers,index)=>{
          const q=questions[index];if(!Array.isArray(answers)||answers.length<1||answers.length>32||q.multiple!==true&&answers.length!==1)fail('INVALID_ANSWERS','答案数量与原问题不符。');
          const values=answers.map(value=>text(value,2000));
          if(new Set(values).size!==values.length||q.custom===false&&values.some(value=>!q.options?.some(option=>option.label===value)))fail('INVALID_ANSWERS','请选择原问题允许的答案。');
          return values;
        });
      }
      if(reject&&!selectedRequest.allowed_choices?.includes('reject')||!permission&&!reject&&!selectedRequest.allowed_choices?.includes('reply'))fail('PENDING_CHOICE_DENIED','原请求不允许此答复。');
      return write(path+(permission?'/permissions/':'/questions/')+encodeURIComponent(requestId)+(reject?'/reject':'/reply'),body,permission?'PERMISSION_REPLY':reject?'QUESTION_REJECT':'QUESTION_REPLY');
    }
    if(method==='session.cancel'){
      keys(p,['operation_id','expected_active_operation_id']);const body={operation_id:id(p.operation_id),session_ref:s.session_ref,generation:s.generation,expected_active_operation_id:id(p.expected_active_operation_id)};
      return write(path+'/interrupt',body,'INTERRUPT');
    }
    if(method==='artifact.open'){
      if(p.kind==='workspace-file'){
        keys(p,['kind','file_ref']);if(!s.project_id||!s.session_ref||s.generation==null||typeof openArtifact!=='function')fail('EXACT_SCOPE_REQUIRED','请选择准确的项目、原会话及代次。');keys(p.file_ref,['workspace_ref','path','message_id','part_id','link_index','href_sha256']);
        const current=await read();if(!current.workspace_ref)fail('ARTIFACT_CHANGED','文件已更新，请刷新');const binding=await get('/api/projects/'+s.project_id+'/local-workspaces/'+id(current.workspace_ref.id));const candidate=workspaceFiles(current,binding).find(item=>canonical(item.file_ref)===canonical(p.file_ref));if(!candidate)fail('ARTIFACT_CHANGED','文件已更新，请刷新');
        const saved=await get('/api/projects/'+s.project_id+'/local-workspaces/'+id(current.workspace_ref.id)+'/file?path='+encodeURIComponent(candidate.file_ref.path)),source=saved.source_ref;
        if(!object(source)||source.kind!=='local_file'||source.project_id!==s.project_id||source.workspace_id!==current.workspace_ref.id||source.binding_revision!==current.workspace_ref.revision||source.binding_sha256!==current.workspace_ref.binding_sha256||source.path!==candidate.file_ref.path||!Number.isSafeInteger(source.revision)||source.revision<1||!SHA.test(source.sha256??'')||saved.current!==true||saved.live_sha256!==source.sha256)fail('ARTIFACT_CHANGED','文件已更新，请刷新');
        const body={kind:'workspace-file',target:candidate.target,project_id:s.project_id,file_source_ref:source};if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');const opened=await openArtifact(body,{signal,scope:s});
        if(candidate.target==='editor'){
          if(opened?.status!=='RESOLVED'||canonical(opened.file_source_ref)!==canonical(source)||typeof opened.native_path!=='string'||!paths.isAbsolute(opened.native_path))fail('ARTIFACT_CHANGED','文件已更新，请刷新');
          return {status:'EDITOR_READY',editor_uri:pathToFileURL(opened.native_path).href,editor_kind:/\.(md|markdown)$/i.test(source.path)?'markdown':'text',file_source_ref:source,content_basis:'CURRENT_SAVED_BYTES_ON_EXPLICIT_OPEN',editor_buffer_modified:false};
        }
        return {status:'OPEN_REQUESTED',file_source_ref:source,content_basis:'CURRENT_SAVED_BYTES_ON_EXPLICIT_OPEN',native_software_open_confirmed:false};
      }
      keys(p,['kind','artifact_id','sha256','download_url']);if(!s.project_id||p.kind!=='report'||typeof openArtifact!=='function')fail('NOT_CONNECTED','此产物尚未接入原生打开，请在App核对真实产物。');
      const body={kind:'report',project_id:s.project_id,artifact_id:hash(p.artifact_id),sha256:hash(p.sha256)};
      const url=text(p.download_url,700),artifact=url.match(/^\/api\/projects\/([A-Za-z0-9_-]{1,100})\/(?:report-artifacts|docx-revisions\/artifacts)\/([a-f0-9]{64})\/download$/),report=url.match(/^\/api\/projects\/([A-Za-z0-9_-]{1,100})\/(planning\/proposals|discovery\/sessions)\/([A-Za-z0-9_-]{1,100})\/reports\/([a-f0-9]{64})\/(download|pending\/download|pending\/json\/download|source\/download)$/);
      if(!((artifact&&artifact[1]===s.project_id&&artifact[2]===body.artifact_id)||(report&&report[1]===s.project_id&&report[4]===body.artifact_id)))fail('INVALID_PARAMS','产物下载必须匹配当前项目的准确manifest引用。');body.download_url=url;
      // A selected conversation may open only a report actually returned by
      // its completed admitted tool. Main's native resolver rechecks bytes.
      if(s.session_ref){const current=artifactCandidates(await read());if(!current.candidates.some(item=>item.manifest.id===body.artifact_id&&item.manifest.sha256===body.sha256&&item.manifest.download_url===url))fail('ARTIFACT_CHANGED','文件已更新，请刷新');}
      if(signal?.aborted)fail('REQUEST_CANCELLED','请求已取消，没有开始新的操作。');
      await openArtifact(body,{signal});return{status:'OPEN_REQUESTED',artifact:{kind:'report',artifact_id:body.artifact_id,sha256:body.sha256},native_software_open_confirmed:false};
    }
    if(method==='lifecycle.reveal'){keys(p,[]);if(typeof reveal!=='function')fail('NOT_CONNECTED','App显示入口尚未接入。');await reveal({scope:s},{signal});return{status:'REVEAL_REQUESTED'};}
    fail('NOT_SUPPORTED','此功能尚未接入IDE。');
  }
  return async function boundedDispatch(...args){
    const result=await dispatch(...args);
    // The transport has its own body bound. Stay below it and declare every
    // dropped row instead of returning an apparently complete giant history.
    while(Buffer.byteLength(JSON.stringify(result),'utf8')>512*1024){
      const field=['messages','pending_questions','pending_permissions','todo','children','guidance','sessions','projects'].find(name=>Array.isArray(result[name])&&result[name].length>1);
      if(!field)fail('RESPONSE_TOO_LARGE','此记录超出IDE预览范围；请在App核对，原操作未重发。');
      result[field].shift();
      const coverage=result.ide_coverage?.[field]??result.coverage;
      if(coverage){coverage.returned=result[field].length;coverage.truncated=true;
        if(result.kind==='NATIVE_CONVERSATION'){coverage.has_more=true;coverage.complete=false;}}
    }
    return result;
  };
}
module.exports={createIdeApiAdapter,IdeApiError,METHODS};
