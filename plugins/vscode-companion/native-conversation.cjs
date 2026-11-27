'use strict';
const {canonical,validateScope,hash}=require('./client.cjs');
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
const native=s=>s?.kind==='NATIVE_CONVERSATION';
function fail(){throw Object.assign(Error('Desktop discussion identity is unavailable'),{code:'SCOPE_MISMATCH'});}
function scope(s){if(!native(s)||!ID.test(s.project_id)||!ID.test(s.conversation_id))fail();return validateScope({project_id:s.project_id,conversation_id:s.conversation_id});}
function profile(v){return v&&typeof v.id==='string'&&ID.test(v.id)&&Number.isSafeInteger(v.revision)&&v.revision>0&&SHA.test(v.sha256)?v:null;}
function same(a,b){return canonical(a)===canonical(b);}
const MAX_TEXT_BYTES=196608;
function fileSource(v,pid){return v?.kind==='local_file'&&v.project_id===pid&&ID.test(v.project_id)&&SHA.test(v.workspace_id)&&Number.isSafeInteger(v.binding_revision)&&v.binding_revision>0&&SHA.test(v.binding_sha256)&&Number.isSafeInteger(v.revision)&&v.revision>0&&SHA.test(v.sha256)&&typeof v.path==='string'&&v.path.length<=1024&&!/[\\:\x00-\x1f\x7f]/.test(v.path)&&!v.path.startsWith('/')&&!v.path.split('/').some(x=>!x||x==='.'||x==='..')?v:null;}
function fileIdentity(a,b){return !!a&&!!b&&['kind','project_id','workspace_id','binding_revision','binding_sha256','path'].every(k=>a[k]===b[k]);}
function planSummary(v){return v?.kind==='PLAN'&&profile(v.record_ref)&&profile(v.mapping_ref)&&profile(v.native_feedback_ref)&&profile(v.proposal_ref)&&typeof v.status==='string'?v:null;}
function messageToken(session,message){return hash(canonical({scope:scope(session),parent_operation_id:message.operation_id,file_source_ref:message.file_source_ref??null,rework_previews:message.rework_previews??[]}));}
function responseScope(v){return {project_id:v?.project_id,conversation_id:v?.conversation_id};}
function bounded(v){if(Buffer.byteLength(JSON.stringify(v))>524288)fail();}
function validateFile(v,owner,parent){bounded(v);
  if(v?.kind!=='NATIVE_FILE'||!same(responseScope(v),owner)||v.parent_operation_id!==parent||!fileSource(v.baseline_file_source_ref,owner.project_id)||!fileSource(v.file_source_ref,owner.project_id)||!fileIdentity(v.baseline_file_source_ref,v.file_source_ref)||!['AVAILABLE','BINARY','TOO_LARGE'].includes(v.availability)||v.read_only!==true||v.model_calls!==0)fail();
  if(v.availability==='AVAILABLE'&&(v.encoding!=='utf-8'||typeof v.content!=='string'||v.content.includes('\0')||Buffer.byteLength(v.content)>MAX_TEXT_BYTES||Buffer.byteLength(v.content)!==v.byte_length||hash(v.content)!==v.file_source_ref.sha256))fail();
  return JSON.parse(JSON.stringify(v));
}
function validatePreview(v,owner,parent,plan){bounded(v);
  if(v?.kind!=='NATIVE_REWORK_PREVIEW'||!same(responseScope(v),owner)||v.parent_operation_id!==parent||!same(v.plan_ref,plan)||!profile(v.mapping_ref)||!profile(v.native_feedback_ref)||!profile(v.proposal_ref)||!fileSource(v.file_source_ref,owner.project_id)||!Array.isArray(v.write_scope)||v.write_scope.length!==1||v.write_scope[0]?.workspace_id!==v.file_source_ref?.workspace_id||v.write_scope[0]?.path!==v.file_source_ref?.path||v.model_calls!==0||typeof v.already_applied!=='boolean'||typeof v.can_apply!=='boolean'||v.already_applied&&v.can_apply)fail();
  const d=v.diff;if(!d||d.encoding!=='UTF-8'||d.content_scope!=='FULL_FILE'||d.read_only!==true||[d.before,d.after].some(x=>typeof x!=='string'||x.includes('\0')||Buffer.byteLength(x)>MAX_TEXT_BYTES)||hash(d.before)!==d.before_sha256||d.before_sha256!==v.file_source_ref.sha256||hash(d.after)!==d.after_sha256)fail();
  return JSON.parse(JSON.stringify(v));
}
function selection(source,text,start,end){
  if(typeof text!=='string'||hash(text)!==source.sha256||!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<=start||end>text.length)fail();
  const splits=i=>i>0&&i<text.length&&/[\uD800-\uDBFF]/.test(text[i-1])&&/[\uDC00-\uDFFF]/.test(text[i]);if(splits(start)||splits(end))fail();
  const quote=text.slice(start,end);if(Array.from(quote).length>4000)fail();
  return {quote,selection:{kind:'TEXT_RANGE',char_start:Array.from(text.slice(0,start)).length,char_end:Array.from(text.slice(0,end)).length,quote_sha256:hash(quote)}};
}
function receipt(value,entry){
  const feedback=entry.method==='native.feedback.record',kind=feedback?'NATIVE_FEEDBACK':'NATIVE_REWORK_APPLY';
  if(value?.kind!==kind||!same(responseScope(value),entry.scope)||value.parent_operation_id!==entry.parent_operation_id||value.operation_id!==entry.operation_id||!same(value.request,entry.native_request)||value.request_sha256!==hash(canonical(entry.native_request))||value.automatic_retry!==false||typeof value.status!=='string')fail();
  if(feedback&&value.status==='RECORDED'){if(!profile(value.feedback_ref)||!same(value.file_source_ref,entry.native_request.file_source_ref))fail();const selected=value.file_selection,original=entry.native_request.selection;if(value.selection_verification==='TEXT_RANGE_HASH_VERIFIED'){if(selected?.kind!=='TEXT_RANGE'||original.kind==='TEXT_RANGE'&&!same(selected,original)||original.kind==='EXACT_QUOTE'&&(selected.quote_sha256!==hash(original.quote)||selected.char_end-selected.char_start!==Array.from(original.quote).length))fail();}else if(original.kind!=='EXACT_QUOTE'||!['QUOTE_NOT_FOUND','QUOTE_AMBIGUOUS','QUOTE_NOT_ADAPTED'].includes(value.selection_verification)||selected!==null)fail();if(original.kind==='EXACT_QUOTE'&&!same(value.requested_selection,original))fail();}
  if(!feedback){if(!same(value.plan_ref,entry.native_request.plan_ref))fail();if(value.status==='APPLIED'&&(!profile(value.receipt_ref)||value.outcomes?.length!==1||!['APPLIED','UNCHANGED'].includes(value.outcomes[0].status)||!fileSource(value.outcomes[0].source_ref,entry.scope.project_id)||!fileIdentity(value.outcomes[0].source_ref,entry.native_request.expected_current_sources[0])))fail();}return value;
}
function previewDocument(value){return {scope:responseScope(value),snapshot_ref:value.plan_ref,diff_ref:value.plan_ref,source_scope:{parent_operation_id:value.parent_operation_id,mapping_ref:value.mapping_ref,native_feedback_ref:value.native_feedback_ref,proposal_ref:value.proposal_ref,file_source_ref:value.file_source_ref},path:value.file_source_ref.path,before:value.diff.before,after:value.diff.after};}
function directory(v,pid){
  if(v?.kind!=='NATIVE_CONVERSATION_DIRECTORY'||v.project_id!==pid||!Array.isArray(v.conversations)||v.conversations.length>30||v.coverage?.returned!==v.conversations.length||typeof v.coverage.has_more!=='boolean'||v.coverage.complete!==!v.coverage.has_more)fail();
  const seen=new Set();for(const row of v.conversations){if(scope(row).project_id!==pid||seen.has(row.conversation_id))fail();seen.add(row.conversation_id);}return v;
}
function present(value,expected){
  if(!same(scope(value),expected)||!Array.isArray(value.messages)||value.messages.length>40||value.coverage?.returned!==value.messages.length||typeof value.coverage.has_more!=='boolean')fail();
  const messages=[],seen=new Set();let active=null;
  for(const message of value.messages){
    if(message?.kind!=='NATIVE_MESSAGE'||message.project_id!==expected.project_id||message.conversation_id!==expected.conversation_id||!ID.test(message.operation_id)||seen.has(message.operation_id)||message.input?.origin!=='ORIGINAL_NATIVE_REQUEST'||typeof message.input.raw_text!=='string'||Array.from(message.input.raw_text).length>16000||!['CURRENT_HOST','HISTORICAL_READONLY'].includes(message.record_origin))fail();
    seen.add(message.operation_id);
    messages.push({info:{id:message.operation_id+':request',role:'user'},native_request:true,parts:[{id:message.operation_id+':input',type:'text',text:message.input.raw_text}]});
    const text=typeof message.text==='string'?message.text:typeof message.summary==='string'?message.summary:'';
    const file=fileSource(message.file_source_ref,expected.project_id),plans=(Array.isArray(message.rework_previews)?message.rework_previews:[]).slice(-24).filter(planSummary);
    messages.push({info:{id:message.operation_id+':reply',role:'assistant'},native_status:message.status,native_execution:message.native_execution,native_parent_operation_id:message.operation_id,native_file_source_ref:file,native_plans:plans,native_action_token:messageToken(value,message),parts:text?[{id:message.operation_id+':text',type:'text',text}]:[]});
    const controls=message.control_capabilities;
    if(message.record_origin==='CURRENT_HOST'&&profile(message.native_message_ref)&&(controls?.can_guide===true||controls?.can_status===true||controls?.can_cancel===true||message.status==='PROCESSING'&&['STARTING','DISPATCHING','STOP_REQUESTED'].includes(message.native_message_phase)))active=message;
  }
  return {...value,native_messages:value.messages,messages,artifacts:[],active_operation_id:active?.operation_id??null,active_native_message:active,latest_provider_profile_ref:profile(value.latest_provider_profile_ref),ide_coverage:{messages:{truncated:value.coverage.has_more}}};
}
module.exports={native,scope,profile,same,directory,present,fileSource,fileIdentity,planSummary,messageToken,validateFile,validatePreview,previewDocument,selection,receipt,responseScope,MAX_TEXT_BYTES};
