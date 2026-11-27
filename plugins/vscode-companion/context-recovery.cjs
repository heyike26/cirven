'use strict';
// The original API owns stop/create. This module journals transport and intent.
const {canonical,hash,validateScope}=require('./client.cjs');
const METHODS=['session.context.recovery.prepare','session.context.recovery.command','session.context.recovery.operation'];
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,CID=/^(native|original|discovery):[a-f0-9]{64}$/;
const copy=v=>JSON.parse(JSON.stringify(v)),same=(a,b)=>canonical(a)===canonical(b),digest=v=>hash(canonical(v));
function fail(code='SOURCE_CONTEXT_RECOVERY_UNAVAILABLE'){throw Object.assign(new Error(code),{code});}
function check(v){if(!v)fail();}
function conversation(v){return v&&typeof v.id==='string'&&CID.test(v.id)&&Number.isSafeInteger(v.revision)&&v.revision>=1&&Object.keys(v).sort().join(',')==='id,revision';}
function issue(v,g){return v&&['SOURCE_CONTEXT_CHANGED','SOURCE_CONTEXT_UNAVAILABLE','SOURCE_CONTEXT_LEGACY'].includes(v.code)&&v.generation===g&&(v.operation_id===null||typeof v.operation_id==='string'&&ID.test(v.operation_id))&&v.scope==='FUTURE_HOST_ADMISSIONS_AND_API_RELAY'&&v.original_history_retained===true&&Object.keys(v).sort().join(',')==='code,generation,operation_id,original_history_retained,scope';}
function prepare(value,scope){
 const basis={kind:value?.kind,scope:value?.scope,conversation_ref:value?.conversation_ref,expected_issue:value?.expected_issue,target_project_id:value?.target_project_id,existing_recovery:value?.existing_recovery};
 check(basis.kind==='CONTEXT_RECOVERY_PREPARATION'&&same(basis.scope,scope)&&conversation(basis.conversation_ref)&&issue(basis.expected_issue,scope.generation)&&(basis.target_project_id===null||typeof basis.target_project_id==='string'&&ID.test(basis.target_project_id)));
 check(value.preparation_sha256===digest(basis)&&value.read_only===true&&value.model_calls===0&&value.native_posts===0&&value.new_conversations===0&&value.permission_granted===false&&value.automatic_replay===false);
 const e=basis.existing_recovery;if(e!==null){const r=e?.request;check(r&&ID.test(r.operation_id??'')&&same(r.session_ref,scope.session_ref)&&r.generation===scope.generation&&conversation(r.conversation_ref)&&r.conversation_ref.id===basis.conversation_ref.id&&issue(r.expected_issue,scope.generation)&&e.request_sha256===digest(r)&&(e.target_project_id===null||typeof e.target_project_id==='string'&&ID.test(e.target_project_id)));check(same(r,request(scope,{...basis,existing_recovery:null,conversation_ref:r.conversation_ref,expected_issue:r.expected_issue},r.operation_id)));}
 return copy({...basis,preparation_sha256:value.preparation_sha256,read_only:true,model_calls:0,native_posts:0,new_conversations:0,permission_granted:false,automatic_replay:false});
}
function request(scope,preparation,operation){if(preparation.existing_recovery){check(operation===preparation.existing_recovery.request.operation_id);return copy(preparation.existing_recovery.request);}return {operation_id:operation,session_ref:scope.session_ref,generation:scope.generation,conversation_ref:preparation.conversation_ref,expected_issue:preparation.expected_issue};}
function validEntry(entry){try{
 if(!entry||entry.method!==METHODS[1]||!ID.test(entry.request_id??''))return false;
 const scope=validateScope(entry.scope);check(scope.session_ref&&Number.isSafeInteger(scope.generation)&&scope.generation>=1);
 const p=prepare(entry.recovery_preparation,scope),r=entry.recovery_request;
 check(ID.test(entry.operation_id??'')&&same(r,request(scope,p,entry.operation_id))&&entry.recovery_request_sha256===digest(r));
 check(entry.recovery_parent_request_id===undefined||ID.test(entry.recovery_parent_request_id));
 if(entry.recovery_receipt)receipt(entry.recovery_receipt,entry);
 return Buffer.byteLength(JSON.stringify(entry))<=32768;
 }catch{return false;}}
function receipt(value,entry){
 const r=entry.recovery_request,p=entry.recovery_preparation,v=value;
 const allowed=['kind','version','operation_id','request_sha256','project_id','session_ref','generation','conversation_ref','phase','stop_operation_id','stop_status','creation_request','creation_receipt','original_history_retained','drafts_transferred','source_data_copied','execution_started','automatic_post_replay','model_calls_by_recovery'];
 check(v&&Object.keys(v).every(k=>allowed.includes(k)));
 check(v?.kind==='CONTEXT_RECOVERY'&&v.version===1&&v.operation_id===r.operation_id&&v.request_sha256===entry.recovery_request_sha256&&v.project_id===entry.scope.project_id&&same(v.session_ref,r.session_ref)&&v.generation===r.generation&&same(v.conversation_ref,r.conversation_ref));
 check(['STOP_PENDING','STOP_CONFIRMED','COMPLETE'].includes(v.phase)&&/^context_stop_[a-f0-9]{48}$/.test(v.stop_operation_id??'')&&typeof v.stop_status==='string'&&v.stop_status.length<=80&&v.original_history_retained===true&&v.drafts_transferred===false&&v.source_data_copied===false&&v.execution_started===false&&v.automatic_post_replay===false&&v.model_calls_by_recovery===0);
 const target=p.existing_recovery?p.existing_recovery.target_project_id:p.target_project_id;
 const create=v.creation_request;check(create&&/^context_chat_[a-f0-9]{48}$/.test(create.operation_id??'')&&create.project_id===target&&create.title===null);
 if(v.phase==='COMPLETE'){
  const c=v.creation_receipt;check(v.stop_status==='RETURNED'&&c?.version===1&&c.operation_id===create.operation_id&&c.request_sha256===digest(create)&&c.project_id===target&&conversation(c.conversation_ref)&&c.conversation_ref.id!==r.conversation_ref.id&&c.next_message?.project_id===c.project_id&&ID.test(c.next_message.conversation_id??'')&&typeof c.created_at==='string'&&Number.isFinite(Date.parse(c.created_at))&&c.execution_started===false&&c.messages_created===0&&c.model_calls===0&&c.file_write_authorized===false&&c.automatic_input_authorized===false);
 }else check(v.creation_receipt===null);
 // Main already projects a fixed schema. Still store only this bounded result.
 check(Buffer.byteLength(JSON.stringify(v))<=16384);return copy(v);
}
class ContextRecovery{
 constructor(controller){this.c=controller;this.running=new Set();}
 supported(){return METHODS.every(m=>this.c.client?.methods?.includes(m));}
 selectedScope(){const s=this.c.selected;if(!s||s.kind==='NATIVE_CONVERSATION'||!s.session_ref||!s.generation)return null;return validateScope({project_id:s.project_id,session_ref:s.session_ref,generation:s.generation});}
 entries(scope){return this.c.state.value.requests.filter(r=>r.method===METHODS[1]&&same(r.scope,scope));}
 snapshot(){
  const scope=this.selectedScope();if(!scope)return {needed:false};const entries=this.entries(scope),entry=entries.at(-1),current=this.c.selected.context_authority_issue;
  if(!entry&&!issue(current,scope.generation))return {needed:false};
  const valid=!entry||validEntry(entry),v=valid?entry?.recovery_receipt:null;
  const phase=!valid?'UNAVAILABLE':v?.phase??(entry?.recovery_not_found?'NOT_FOUND':entry?'PENDING':'READY');
  const key=digest(scope),token=digest({scope,operation_id:entry?.operation_id??null,issue:issue(current,scope.generation)?current:null});
  return {needed:true,supported:this.supported(),token,busy:this.running.has(key),phase,
   can_continue:phase==='STOP_CONFIRMED'||phase==='NOT_FOUND'||phase==='STOP_PENDING'&&v?.stop_status==='NOT_FOUND',
   retry_stop:phase==='NOT_FOUND'||phase==='STOP_PENDING'&&v?.stop_status==='NOT_FOUND',
   error:valid?entry?.recovery_error??null:'SOURCE_CONTEXT_RECOVERY_UNAVAILABLE',request_id:entry?.request_id??null,
   desktop_continuation_required:phase==='COMPLETE'&&!(this.c.canonicalConversations?.supported()&&(this.c.client?.projectIds===null||Array.isArray(this.c.client?.projectIds)&&this.c.client.projectIds.includes(v?.creation_receipt?.project_id)))};
 }
 current(token){const value=this.snapshot();if(!value.needed||value.token!==token||!this.supported())fail('SOURCE_CONTEXT_RECOVERY_CHANGED');return {value,scope:this.selectedScope()};}
 async locked(scope,work){const key=digest(scope);if(this.running.has(key))fail('SOURCE_CONTEXT_RECOVERY_PENDING');this.running.add(key);this.c.emit();try{return await work();}finally{this.running.delete(key);this.c.emit();}}
 metadata(scope,p,r,parent){return {recovery_preparation:copy(p),recovery_request:copy(r),recovery_request_sha256:digest(r),...(parent?{recovery_parent_request_id:parent}:{})};}
 async start(token){const {scope,value}=this.current(token);check(value.phase==='READY');const client=this.c.client,epoch=this.c.epoch;
  return this.locked(scope,async()=>{
   const p=prepare(await this.c.read(METHODS[0],scope),scope);
   if(this.c.client!==client||this.c.epoch!==epoch||this.snapshot().token!==token||!same(p.expected_issue,this.c.selected?.context_authority_issue))fail('SOURCE_CONTEXT_RECOVERY_CHANGED');
   const operation=p.existing_recovery?.request.operation_id??this.c.newId(),r=request(scope,p,operation);
   if(p.existing_recovery){
    const entry={request_id:this.c.newId(),method:METHODS[1],scope:copy(scope),operation_id:operation,status:'UNKNOWN',...this.metadata(scope,p,r),recovery_discovered:true};
    this.c.state.commit(v=>{v.requests.push(entry);});this.c.emit();return this.readOriginal(entry);
   }
   return this.c.mutation(METHODS[1],scope,{operation_id:operation,preparation:p},this.metadata(scope,p,r));
  });
 }
 onEnvelope(entry,envelope){
  if(!validEntry(entry))fail();let verified=null,error=null;
  try{if(envelope.status==='OK')verified=receipt(envelope.result,entry);else error=envelope.error?.code==='SOURCE_CONTEXT_RECOVERY_NOT_FOUND'?'SOURCE_CONTEXT_RECOVERY_NOT_FOUND':'SOURCE_CONTEXT_RECOVERY_PENDING';}
  catch{error='SOURCE_CONTEXT_RECOVERY_UNAVAILABLE';}
  if(entry.recovery_receipt?.phase==='COMPLETE'&&verified?.phase!=='COMPLETE'){verified=receipt(entry.recovery_receipt,entry);error=null;}
  this.c.state.commit(v=>{
   const rows=v.requests.filter(r=>r.method===METHODS[1]&&same(r.scope,entry.scope)&&r.operation_id===entry.operation_id&&r.recovery_request_sha256===entry.recovery_request_sha256);
   for(const row of rows){
    if(row.recovery_receipt?.phase==='COMPLETE'&&verified?.phase!=='COMPLETE')continue;
    if(verified?.phase==='COMPLETE'||row.request_id===entry.request_id){
     row.status=verified?.phase==='COMPLETE'?'OK':'UNKNOWN';row.recovery_error=error;
     row.recovery_not_found=error==='SOURCE_CONTEXT_RECOVERY_NOT_FOUND';
     if(verified)row.recovery_receipt=copy(verified);
    }
   }
  });
  if(verified?.phase==='COMPLETE')this.c.known.set(entry.request_id,{status:'OK',result:verified});else this.c.known.delete(entry.request_id);
  this.c.emit();return verified;
 }
 async readOriginal(entry,discover=true){
   let result;
   try{result=await this.c.read(METHODS[2],entry.scope,{operation_id:entry.operation_id,request_sha256:entry.recovery_request_sha256});}
   catch(error){
    this.onEnvelope(entry,{status:'ERROR',error:{code:error.code}});
    if(discover&&error.code==='SOURCE_CONTEXT_RECOVERY_NOT_FOUND'){
     let p;
     try{p=prepare(await this.c.read(METHODS[0],entry.scope),entry.scope);}catch{}
     const existing=p?.existing_recovery;
     if(existing&&existing.request.operation_id!==entry.operation_id){
      const r=existing.request,replacement={request_id:this.c.newId(),method:METHODS[1],scope:copy(entry.scope),operation_id:r.operation_id,status:'UNKNOWN',...this.metadata(entry.scope,p,r,entry.request_id),recovery_discovered:true};
      this.c.state.commit(v=>{
       const old=v.requests.find(row=>row.request_id===entry.request_id);if(!old||old.recovery_request_sha256!==entry.recovery_request_sha256)fail();
       old.recovery_original_transport_status=old.status;old.status='ERROR';
       old.recovery_superseded_by={operation_id:r.operation_id,request_sha256:existing.request_sha256,preparation_sha256:p.preparation_sha256,original_record_last_observed:'NOT_FOUND'};
       v.requests.push(replacement);
      });this.c.emit();return this.readOriginal(replacement,false);
     }
    }
    throw error;
   }
   return this.onEnvelope(entry,{status:'OK',result});
 }
 async inspectEntry(id){
  const entry=this.c.state.value.requests.find(r=>r.request_id===id);if(!validEntry(entry))fail();
  return this.locked(entry.scope,()=>this.readOriginal(entry));
 }
 inspect(token){const {value}=this.current(token);check(value.request_id);return this.inspectEntry(value.request_id);}
 async continueOriginal(token){const {scope,value}=this.current(token);check(value.can_continue);const entry=this.entries(scope).at(-1);check(validEntry(entry));
  return this.locked(scope,()=>this.c.mutation(METHODS[1],scope,{operation_id:entry.operation_id,preparation:copy(entry.recovery_preparation)},this.metadata(scope,entry.recovery_preparation,entry.recovery_request,entry.request_id)));
 }
 async reveal(token){const {scope,value}=this.current(token);check(value.phase==='COMPLETE');if(!value.desktop_continuation_required){const entry=this.entries(scope).at(-1),created=entry.recovery_receipt.creation_receipt;return this.c.canonicalConversations.openReference({project_id:created.project_id,conversation_ref:created.conversation_ref});}return this.c.mutation('lifecycle.reveal',scope,{});}
}
module.exports={ContextRecovery,METHODS,prepare,request,validEntry,receipt};
