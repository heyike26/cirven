'use strict';
// Main projects only source-recovery metadata; the original API owns stop/create.
const {createHash}=require('node:crypto');
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,CID=/^(native|original|discovery):[a-f0-9]{64}$/,KEY=/^native_recovery_[a-f0-9]{48}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':object(v)?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const hash=v=>createHash('sha256').update(canonical(v)).digest('hex'),same=(a,b)=>canonical(a)===canonical(b);
function fail(code='NATIVE_CONTEXT_RECOVERY_UNAVAILABLE'){throw Object.assign(Error('原恢复尚未核实，输入保留。'),{code,public:true});}
function check(v){if(!v)fail();}
function keys(v,names){check(object(v)&&Object.keys(v).length===names.length&&Object.keys(v).every(k=>names.includes(k)));}
function bounded(v){check(Buffer.byteLength(JSON.stringify(v))<=32768);}
const pid=v=>v===null||typeof v==='string'&&ID.test(v);
function ref(v){keys(v,['id','revision','sha256']);check(ID.test(v.id??'')&&v.revision===1&&SHA.test(v.sha256??''));return {id:v.id,revision:1,sha256:v.sha256};}
function issue(v){keys(v,['code','generation','operation_id','scope','original_history_retained']);check(['SOURCE_CONTEXT_CHANGED','SOURCE_CONTEXT_UNAVAILABLE','SOURCE_CONTEXT_LEGACY'].includes(v.code)&&Number.isSafeInteger(v.generation)&&v.generation>0&&(v.operation_id===null||ID.test(v.operation_id??''))&&v.scope==='FUTURE_HOST_ADMISSIONS_AND_API_RELAY'&&v.original_history_retained===true);return {...v};}
function context(value,parent,recordOrigin){
 if(value.native_context_issue===undefined&&value.native_context_recovery===undefined)return {};
 const r=value.native_context_recovery;keys(r,['parent_operation_id','expected_native_ref','record_current','read_only','model_calls']);
 check(r.parent_operation_id===parent&&r.record_current===(recordOrigin==='CURRENT_HOST')&&r.read_only===true&&r.model_calls===0);
 return {native_context_issue:issue(value.native_context_issue),native_context_recovery:{parent_operation_id:parent,expected_native_ref:ref(r.expected_native_ref),record_current:r.record_current,read_only:true,model_calls:0}};
}
function packet(v){keys(v,['operation_id','expected_native_ref','preparation_sha256']);check(KEY.test(v.operation_id??'')&&SHA.test(v.preparation_sha256??''));return {operation_id:v.operation_id,expected_native_ref:ref(v.expected_native_ref),preparation_sha256:v.preparation_sha256};}
function scope(v){keys(v,['project_id','conversation_id']);check(pid(v.project_id)&&ID.test(v.conversation_id??''));return {...v};}
function authority(v,s,parent,expected){
 bounded(v);const basis={parent_operation_id:v?.parent_operation_id,expected_native_ref:v?.expected_native_ref,subject_sha256:v?.subject_sha256,operation_id:v?.operation_id,origin:v?.origin,target:v?.target,issue:v?.issue,existing_recovery:v?.existing_recovery};
 check(v?.kind==='NATIVE_CONTEXT_RECOVERY_PREPARATION'&&basis.parent_operation_id===parent&&same(ref(basis.expected_native_ref),expected)&&SHA.test(basis.subject_sha256??'')&&KEY.test(basis.operation_id??'')&&same(basis.origin,s));
 keys(basis.target,['project_id','conversation_ref']);check(pid(basis.target.project_id));const cr=basis.target.conversation_ref;
 if(cr!==null){keys(cr,['id','revision']);check(CID.test(cr.id??'')&&Number.isSafeInteger(cr.revision)&&cr.revision>=0);}
 issue(basis.issue);if(basis.existing_recovery!==null){const e=basis.existing_recovery;keys(e,['parent_operation_id','request','request_sha256']);check(ID.test(e.parent_operation_id??'')&&e.request.operation_id===basis.operation_id&&e.request_sha256===hash(packet(e.request)));}
 check(v.preparation_sha256===hash(basis)&&(v.target_label===null||typeof v.target_label==='string'&&Array.from(v.target_label).length<=200)&&v.read_only===true&&v.model_calls===0&&v.native_posts===0&&v.new_conversations===0&&v.permission_granted===false&&v.automatic_post_replay===false);
 return {kind:v.kind,...basis,preparation_sha256:v.preparation_sha256,target_label:v.target_label,read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_post_replay:false};
}
function allowed(project,grants){return object(grants)&&(grants.project_ids===null||Array.isArray(grants.project_ids)&&project!==null&&grants.project_ids.includes(project));}
function access(project,grants){if(!allowed(project,grants))fail('NATIVE_CONTEXT_RECOVERY_ACCESS_REQUIRED');}
function result(v,s,parent,key,checksum,expected){
 bounded(v);check(v?.kind==='NATIVE_CONTEXT_RECOVERY'&&v.parent_operation_id===parent&&v.operation_id===key&&KEY.test(key)&&v.request_sha256===checksum&&SHA.test(checksum??'')&&same(v.origin,s)&&SHA.test(v.owner_binding_sha256??''));
 const reference=ref(v.expected_native_ref);if(expected)check(same(reference,expected));
 const suffix=key.slice('native_recovery_'.length),create=v.creation_request;keys(create,['operation_id','project_id','title']);check(create.operation_id==='native_context_chat_'+suffix&&pid(create.project_id)&&create.title===null);
 check(['STOP_PENDING','STOP_CONFIRMED','COMPLETE'].includes(v.phase)&&v.stop_operation_id==='native_context_stop_'+suffix&&typeof v.stop_status==='string'&&/^[A-Z_]{1,80}$/.test(v.stop_status)&&v.original_history_retained===true&&v.drafts_transferred===false&&v.source_data_copied===false&&v.execution_started===false&&v.external_actions_rolled_back===false&&v.automatic_post_replay===false&&v.model_calls_by_recovery===0);
 let created=null;
 if(v.phase==='COMPLETE'){
  const c=v.creation_receipt;check(c?.version===1&&v.stop_status==='RETURNED'&&c.operation_id===create.operation_id&&c.request_sha256===hash(create)&&c.project_id===create.project_id&&CID.test(c.conversation_ref?.id??'')&&c.conversation_ref.revision===1&&c.next_message?.project_id===create.project_id&&ID.test(c.next_message.conversation_id??'')&&typeof c.created_at==='string'&&Number.isFinite(Date.parse(c.created_at))&&c.execution_started===false&&c.messages_created===0&&c.model_calls===0&&c.file_write_authorized===false&&c.automatic_input_authorized===false);
  created={version:1,operation_id:c.operation_id,request_sha256:c.request_sha256,project_id:c.project_id,conversation_ref:{id:c.conversation_ref.id,revision:1},next_message:{project_id:c.project_id,conversation_id:c.next_message.conversation_id},created_at:c.created_at,execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};
 }else check(v.creation_receipt===null);
 return {kind:v.kind,parent_operation_id:parent,operation_id:key,request_sha256:checksum,expected_native_ref:reference,origin:{...s},owner_binding_sha256:v.owner_binding_sha256,phase:v.phase,stop_operation_id:v.stop_operation_id,stop_status:v.stop_status,creation_request:{...create},creation_receipt:created,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,external_actions_rolled_back:false,automatic_post_replay:false,model_calls_by_recovery:0};
}
async function dispatch(method,params,context,api){
 const s=scope(params.scope),p=params.payload??{},signal=context.signal,grants=()=>typeof context.currentGrants==='function'?context.currentGrants():context.grants;
 const base=parent=>'/api/desktop-host/native-messages/'+encodeURIComponent(parent)+'/context-recovery';
 async function owner(parent,expected){
  check(ID.test(parent??''));const v=await api('/api/desktop-host/native-conversations/'+encodeURIComponent(s.conversation_id)+'/operations/'+encodeURIComponent(parent)+(s.project_id===null?'':'?project_id='+encodeURIComponent(s.project_id)),undefined,signal);
  check(v?.kind==='NATIVE_MESSAGE'&&v.operation_id===parent&&v.project_id===s.project_id&&v.conversation_id===s.conversation_id);
  if(expected)check(same(v.native_context_recovery?.expected_native_ref??v.native_message_ref,expected));
 }
 async function original(parent,key,checksum,expected){
  await owner(parent);access(s.project_id,grants());let raw;try{raw=await api(base(parent)+'/'+encodeURIComponent(key),undefined,signal);}catch(error){if(error?.status===404)fail('NATIVE_CONTEXT_RECOVERY_NOT_FOUND');throw error;}
  const v=result(raw,s,parent,key,checksum,expected);access(s.project_id,grants());access(v.creation_request.project_id,grants());return v;
 }
 access(s.project_id,grants());
 if(method==='native.context.recovery.prepare'){
  keys(p,['parent_operation_id','expected_native_ref']);const expected=ref(p.expected_native_ref);await owner(p.parent_operation_id,expected);
  const v=authority(await api(base(p.parent_operation_id)+'/prepare',{expected_native_ref:expected},signal),s,p.parent_operation_id,expected);
  let target=v.target.project_id;if(v.existing_recovery){const e=v.existing_recovery,r=await original(e.parent_operation_id,e.request.operation_id,e.request_sha256,e.request.expected_native_ref);target=r.creation_request.project_id;}
  access(s.project_id,grants());access(target,grants());return {...v,effective_target:{project_id:target,label:target===v.target.project_id?v.target_label:null}};
 }
 if(method==='native.context.recovery.operation'){
  keys(p,['parent_operation_id','operation_id','request_sha256']);check(KEY.test(p.operation_id??'')&&SHA.test(p.request_sha256??''));return original(p.parent_operation_id,p.operation_id,p.request_sha256);
 }
 check(method==='native.context.recovery.command');keys(p,['parent_operation_id','request','preparation']);const body=packet(p.request),v=p.preparation;
 const prepared=authority(v,s,v?.parent_operation_id,ref(v?.expected_native_ref));
 const e=prepared.existing_recovery;
 if(e)check(p.parent_operation_id===e.parent_operation_id&&same(body,e.request));
 else check(p.parent_operation_id===prepared.parent_operation_id&&same(body.expected_native_ref,prepared.expected_native_ref)&&body.operation_id===prepared.operation_id&&body.preparation_sha256===prepared.preparation_sha256);
 check(object(v.effective_target)&&pid(v.effective_target.project_id));await owner(p.parent_operation_id,body.expected_native_ref);
 let saved;try{saved=await original(p.parent_operation_id,body.operation_id,hash(body),body.expected_native_ref);}catch(error){if(error.code!=='NATIVE_CONTEXT_RECOVERY_NOT_FOUND')throw error;}
 if(saved){check(saved.creation_request.project_id===v.effective_target.project_id);if(saved.phase==='COMPLETE'||saved.phase==='STOP_PENDING'&&saved.stop_status!=='NOT_FOUND')return saved;}
 else{
  check(!e);const fresh=authority(await api(base(p.parent_operation_id)+'/prepare',{expected_native_ref:body.expected_native_ref},signal),s,p.parent_operation_id,body.expected_native_ref);
  check(fresh.preparation_sha256===prepared.preparation_sha256&&fresh.target.project_id===v.effective_target.project_id);access(fresh.target.project_id,grants());
 }
 access(s.project_id,grants());access(v.effective_target.project_id,grants());
 if(signal?.aborted)fail('NATIVE_CONTEXT_RECOVERY_PENDING');
 try{await api(base(p.parent_operation_id),body,signal);}catch{/* Only the original GET resolves dispatch, including lost replies. */}
 return original(p.parent_operation_id,body.operation_id,hash(body),body.expected_native_ref);
}
module.exports={dispatch,context,authority,result,packet,allowed};
