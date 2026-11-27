'use strict';
// Projection of the original API recovery journal. No credentials or model IO.
const {createHash}=require('node:crypto');
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,CID=/^(native|original|discovery):[a-f0-9]{64}$/;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':object(v)?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const hash=v=>createHash('sha256').update(stable(v)).digest('hex');
const same=(a,b)=>stable(a)===stable(b);
function fail(code='SOURCE_CONTEXT_RECOVERY_UNAVAILABLE'){throw Object.assign(new Error('原恢复记录暂不能核对，输入保留。'),{code,public:true});}
function check(v){if(!v)fail();}
function keys(v,names){check(object(v)&&Object.keys(v).every(k=>names.includes(k)));}
function scope(v,expected){keys(v,['project_id','session_ref','generation']);check(ID.test(v.project_id??'')&&object(v.session_ref)&&ID.test(v.session_ref.id??'')&&v.session_ref.revision===1&&SHA.test(v.session_ref.sha256??'')&&Number.isSafeInteger(v.generation)&&v.generation>0);check(!expected||same(v,expected));return {project_id:v.project_id,session_ref:{id:v.session_ref.id,revision:1,sha256:v.session_ref.sha256},generation:v.generation};}
function conversation(v){keys(v,['id','revision']);check(CID.test(v.id??'')&&Number.isSafeInteger(v.revision)&&v.revision>=1);return {id:v.id,revision:v.revision};}
function issue(v,generation){check(object(v)&&['SOURCE_CONTEXT_CHANGED','SOURCE_CONTEXT_UNAVAILABLE','SOURCE_CONTEXT_LEGACY'].includes(v.code)&&v.generation===generation&&(v.operation_id===null||typeof v.operation_id==='string'&&ID.test(v.operation_id))&&v.scope==='FUTURE_HOST_ADMISSIONS_AND_API_RELAY'&&v.original_history_retained===true);return {code:v.code,generation,operation_id:v.operation_id,scope:v.scope,original_history_retained:true};}
function preparation(v,expected){
 check(v?.kind==='CONTEXT_RECOVERY_PREPARATION');const s=scope(v.scope,expected),basis={kind:v.kind,scope:s,conversation_ref:conversation(v.conversation_ref),expected_issue:issue(v.expected_issue,s.generation),target_project_id:v.target_project_id,existing_recovery:null};
 if(v.existing_recovery!==null){const e=v.existing_recovery,r=e?.request;keys(e,['request','request_sha256','target_project_id']);keys(r,['operation_id','session_ref','generation','conversation_ref','expected_issue']);check(ID.test(r.operation_id??'')&&same(r.session_ref,s.session_ref)&&r.generation===s.generation&&conversation(r.conversation_ref).id===basis.conversation_ref.id&&same(r.expected_issue,issue(r.expected_issue,s.generation))&&e.request_sha256===hash(r)&&(e.target_project_id===null||typeof e.target_project_id==='string'&&ID.test(e.target_project_id)));basis.existing_recovery={request:r,request_sha256:e.request_sha256,target_project_id:e.target_project_id};}
 check((basis.target_project_id===null||typeof basis.target_project_id==='string'&&ID.test(basis.target_project_id))&&v.preparation_sha256===hash(basis)&&v.read_only===true&&v.model_calls===0&&v.native_posts===0&&v.new_conversations===0&&v.permission_granted===false&&v.automatic_replay===false);
 return {...basis,preparation_sha256:v.preparation_sha256,read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_replay:false};
}
function request(s,p){
 keys(p,['operation_id','preparation']);check(typeof p.operation_id==='string'&&ID.test(p.operation_id));const v=preparation(p.preparation,s);
 if(v.existing_recovery){check(v.existing_recovery.request.operation_id===p.operation_id);return JSON.parse(stable(v.existing_recovery.request));}
 return {operation_id:p.operation_id,session_ref:s.session_ref,generation:s.generation,conversation_ref:v.conversation_ref,expected_issue:v.expected_issue};
}
function result(v,s,operation,requestSha){
 check(v?.version===1&&v.operation_id===operation&&v.request_sha256===requestSha&&v.project_id===s.project_id&&same(v.session_ref,s.session_ref)&&v.generation===s.generation);
 const ref=conversation(v.conversation_ref),create=v.creation_request;keys(create,['operation_id','project_id','title']);
 check(/^context_chat_[a-f0-9]{48}$/.test(create.operation_id??'')&&(create.project_id===null||typeof create.project_id==='string'&&ID.test(create.project_id))&&create.title===null);
 check(['STOP_PENDING','STOP_CONFIRMED','COMPLETE'].includes(v.phase)&&/^context_stop_[a-f0-9]{48}$/.test(v.stop_operation_id??'')&&typeof v.stop_status==='string'&&v.stop_status.length<=80&&v.original_history_retained===true&&v.drafts_transferred===false&&v.source_data_copied===false&&v.execution_started===false&&v.automatic_post_replay===false&&v.model_calls_by_recovery===0);
 let created=null;
 if(v.phase==='COMPLETE'){
  const r=v.creation_receipt;check(object(r)&&r.version===1&&r.operation_id===create.operation_id&&r.request_sha256===hash(create)&&r.project_id===create.project_id&&r.conversation_ref?.id!==ref.id&&r.execution_started===false&&r.messages_created===0&&r.model_calls===0&&r.file_write_authorized===false&&r.automatic_input_authorized===false&&object(r.next_message)&&r.next_message.project_id===r.project_id&&ID.test(r.next_message.conversation_id??'')&&typeof r.created_at==='string'&&Number.isFinite(Date.parse(r.created_at))&&v.stop_status==='RETURNED');
  created={version:1,operation_id:r.operation_id,request_sha256:r.request_sha256,project_id:r.project_id,conversation_ref:conversation(r.conversation_ref),next_message:{project_id:r.project_id,conversation_id:r.next_message.conversation_id},created_at:r.created_at,execution_started:false,messages_created:0,model_calls:0,file_write_authorized:false,automatic_input_authorized:false};
 }else check(v.creation_receipt===null);
 return {kind:'CONTEXT_RECOVERY',version:1,operation_id:operation,request_sha256:requestSha,project_id:s.project_id,session_ref:s.session_ref,generation:s.generation,conversation_ref:ref,phase:v.phase,stop_operation_id:v.stop_operation_id,stop_status:v.stop_status,creation_request:{operation_id:create.operation_id,project_id:create.project_id,title:null},creation_receipt:created,original_history_retained:true,drafts_transferred:false,source_data_copied:false,execution_started:false,automatic_post_replay:false,model_calls_by_recovery:0};
}
async function dispatch(method,s,p,api,signal){
 const owner=scope(s),base='/api/projects/'+encodeURIComponent(s.project_id)+'/agent-sessions/'+encodeURIComponent(s.session_ref.id)+'/context-recovery';
 const get=key=>api(base+'/'+encodeURIComponent(key),undefined,signal);
 if(method==='session.context.recovery.prepare'){keys(p,[]);return preparation(await api(base+'/prepare',{session_ref:s.session_ref,generation:s.generation},signal),owner);}
 if(method==='session.context.recovery.operation'){keys(p,['operation_id','request_sha256']);check(typeof p.operation_id==='string'&&ID.test(p.operation_id)&&SHA.test(p.request_sha256??''));let value;try{value=await get(p.operation_id);}catch(error){fail(error?.status===404?'SOURCE_CONTEXT_RECOVERY_NOT_FOUND':'SOURCE_CONTEXT_RECOVERY_PENDING');}return result(value,owner,p.operation_id,p.request_sha256);}
 if(method!=='session.context.recovery.command')fail('METHOD_NOT_ALLOWED');
 const body=request(owner,p);let value;
 if(signal?.aborted)fail('REQUEST_CANCELLED');
 try{value=await api(base,body,signal);}
 catch(error){
  if(signal?.aborted)fail('SOURCE_CONTEXT_RECOVERY_PENDING');
  try{value=await get(body.operation_id);}
  catch(failure){fail(failure?.status===404?'SOURCE_CONTEXT_RECOVERY_NOT_FOUND':'SOURCE_CONTEXT_RECOVERY_PENDING');}
 }
 const projected=result(value,owner,body.operation_id,hash(body));
 const target=p.preparation.existing_recovery?p.preparation.existing_recovery.target_project_id:p.preparation.target_project_id;
 check(same(projected.conversation_ref,body.conversation_ref)&&projected.creation_request.project_id===target);
 return projected;
}
module.exports={dispatch,preparation,request,result};
