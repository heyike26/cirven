'use strict';
// Private transport journal only. The desktop API owns original stop/create.
const {canonical,hash,validateScope}=require('./client.cjs');
const METHODS=['native.context.recovery.prepare','native.context.recovery.command','native.context.recovery.operation'];
const CANONICAL_METHODS=['conversation.native.recovery.prepare','conversation.native.recovery.command','conversation.native.recovery.operation'];
const method=(scope,index)=>(scope?.conversation_ref?CANONICAL_METHODS:METHODS)[index];
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,KEY=/^native_recovery_[a-f0-9]{48}$/,CID=/^(native|original|discovery):[a-f0-9]{64}$/;
const copy=v=>JSON.parse(JSON.stringify(v)),same=(a,b)=>canonical(a)===canonical(b),digest=v=>hash(canonical(v));
function fail(code='NATIVE_CONTEXT_RECOVERY_UNAVAILABLE'){throw Object.assign(Error(code),{code});}
function check(v){if(!v)fail();}
const project=v=>v===null||typeof v==='string'&&ID.test(v);
const ref=v=>v&&ID.test(v.id??'')&&v.revision===1&&SHA.test(v.sha256??'')&&Object.keys(v).sort().join(',')==='id,revision,sha256';
const packet=v=>v&&KEY.test(v.operation_id??'')&&ref(v.expected_native_ref)&&SHA.test(v.preparation_sha256??'')&&Object.keys(v).sort().join(',')==='expected_native_ref,operation_id,preparation_sha256';
function origin(v){return v&&project(v.project_id)&&ID.test(v.conversation_id??'')&&Object.keys(v).sort().join(',')==='conversation_id,project_id';}
function sourceRef(v){return v&&/^native:[a-f0-9]{64}$/.test(v.id??'')&&SHA.test(v.sha256??'')&&Object.keys(v).sort().join(',')==='id,sha256';}
function binding(v,scope,source,expected){
 if(!scope.conversation_ref){check(v===undefined);return;}
 const current=v?.current_scope;check(same(v?.requested_scope,scope)&&same(v?.source_ref,source.source_ref)&&sourceRef(source.source_ref)&&same(v?.origin,expected)&&current&&project(current.project_id)&&current.conversation_ref?.id===scope.conversation_ref.id&&Number.isSafeInteger(current.conversation_ref.revision)&&current.conversation_ref.revision>=scope.conversation_ref.revision);
 check(source.source_ref.id==='native:'+digest([expected.project_id,expected.conversation_id])&&source.source_ref.sha256===digest({kind:'native',...expected}));
}
function prepare(v,scope,source,expected=scope){
 check(v&&Buffer.byteLength(JSON.stringify(v))<=32768);check(Object.keys(v).every(k=>['kind','parent_operation_id','expected_native_ref','subject_sha256','operation_id','origin','target','issue','existing_recovery','preparation_sha256','target_label','read_only','model_calls','native_posts','new_conversations','permission_granted','automatic_post_replay','effective_target','canonical_binding'].includes(k)));
 const basis={parent_operation_id:v.parent_operation_id,expected_native_ref:v.expected_native_ref,subject_sha256:v.subject_sha256,operation_id:v.operation_id,origin:v.origin,target:v.target,issue:v.issue,existing_recovery:v.existing_recovery};
 check(v.kind==='NATIVE_CONTEXT_RECOVERY_PREPARATION'&&basis.parent_operation_id===source.parent_operation_id&&same(basis.expected_native_ref,source.expected_native_ref)&&ref(basis.expected_native_ref)&&SHA.test(basis.subject_sha256??'')&&KEY.test(basis.operation_id??'')&&origin(expected)&&same(basis.origin,expected)&&project(basis.target?.project_id)&&v.preparation_sha256===digest(basis));
 check(v.read_only===true&&v.model_calls===0&&v.native_posts===0&&v.new_conversations===0&&v.permission_granted===false&&v.automatic_post_replay===false&&project(v.effective_target?.project_id)&&(v.effective_target.label===null||typeof v.effective_target.label==='string'&&Array.from(v.effective_target.label).length<=200));
 const e=basis.existing_recovery;if(e!==null)check(e&&ID.test(e.parent_operation_id??'')&&packet(e.request)&&e.request.operation_id===basis.operation_id&&e.request_sha256===digest(e.request));
 else check(v.effective_target.project_id===basis.target.project_id);
 binding(v.canonical_binding,scope,source,expected);return copy(v);
}
function request(p){return p.existing_recovery?.request??{operation_id:p.operation_id,expected_native_ref:p.expected_native_ref,preparation_sha256:p.preparation_sha256};}
function receipt(v,entry){
 check(v&&Buffer.byteLength(JSON.stringify(v))<=16384);check(Object.keys(v).every(k=>['kind','parent_operation_id','operation_id','request_sha256','expected_native_ref','origin','owner_binding_sha256','phase','stop_operation_id','stop_status','creation_request','creation_receipt','original_history_retained','drafts_transferred','source_data_copied','execution_started','external_actions_rolled_back','automatic_post_replay','model_calls_by_recovery','canonical_binding'].includes(k)));const r=entry.native_recovery_request,p=entry.native_recovery_preparation;
 check(v.kind==='NATIVE_CONTEXT_RECOVERY'&&v.parent_operation_id===entry.native_recovery_parent&&v.operation_id===r.operation_id&&v.request_sha256===entry.native_recovery_request_sha256&&same(v.expected_native_ref,r.expected_native_ref)&&same(v.origin,entry.native_recovery_origin??entry.scope)&&SHA.test(v.owner_binding_sha256??''));
 const suffix=r.operation_id.slice('native_recovery_'.length),create=v.creation_request;
 check(['STOP_PENDING','STOP_CONFIRMED','COMPLETE'].includes(v.phase)&&v.stop_operation_id==='native_context_stop_'+suffix&&typeof v.stop_status==='string'&&/^[A-Z_]{1,80}$/.test(v.stop_status)&&create?.operation_id==='native_context_chat_'+suffix&&create.project_id===p.effective_target.project_id&&create.title===null);
 check(v.original_history_retained===true&&v.drafts_transferred===false&&v.source_data_copied===false&&v.execution_started===false&&v.external_actions_rolled_back===false&&v.automatic_post_replay===false&&v.model_calls_by_recovery===0);
 if(v.phase==='COMPLETE'){
  const c=v.creation_receipt;check(v.stop_status==='RETURNED'&&c?.version===1&&c.operation_id===create.operation_id&&c.request_sha256===digest(create)&&c.project_id===create.project_id&&CID.test(c.conversation_ref?.id??'')&&c.conversation_ref.revision===1&&c.next_message?.project_id===c.project_id&&ID.test(c.next_message.conversation_id??'')&&typeof c.created_at==='string'&&Number.isFinite(Date.parse(c.created_at))&&c.execution_started===false&&c.messages_created===0&&c.model_calls===0&&c.file_write_authorized===false&&c.automatic_input_authorized===false);
 }else check(v.creation_receipt===null);
 binding(v.canonical_binding,entry.scope,entry.native_recovery_source,entry.native_recovery_origin??entry.scope);return copy(v);
}
function validEntry(e){try{
 check(e?.method===method(e.scope,1)&&ID.test(e.request_id??'')&&ID.test(e.native_recovery_source?.parent_operation_id??'')&&ref(e.native_recovery_source.expected_native_ref));
 const s=validateScope(e.scope),expected=e.native_recovery_origin??s;check(origin(expected)&&(s.conversation_ref?s.conversation_ref.revision>=1:s.project_id&&s.conversation_id&&Object.keys(s).length===2));
 const p=prepare(e.native_recovery_preparation,s,e.native_recovery_source,expected),r=e.native_recovery_request;
 check(packet(r)&&same(r,request(p))&&e.operation_id===r.operation_id&&e.native_recovery_request_sha256===digest(r)&&e.native_recovery_parent===(p.existing_recovery?.parent_operation_id??p.parent_operation_id));
 if(e.native_recovery_receipt)receipt(e.native_recovery_receipt,e);
 check(Buffer.byteLength(JSON.stringify(e))<=32768);return true;
 }catch{return false;}}
class NativeContextRecovery{
 constructor(c){this.c=c;this.running=new Set();this.plans=new Map();}
 supported(){return (this.selectedScope()?.conversation_ref?CANONICAL_METHODS:METHODS).every(m=>this.c.client?.methods?.includes(m));}
 reset(){this.plans.clear();}
 selectedScope(){const s=this.c.selected;if(s?.kind==='CANONICAL_CONVERSATION')return validateScope({project_id:s.organization_project_id,conversation_ref:s.conversation_ref});return s?.kind==='NATIVE_CONVERSATION'?validateScope({project_id:s.project_id,conversation_id:s.conversation_id}):null;}
 sources(){
  const scope=this.selectedScope();if(!scope)return [];
  if(scope.conversation_ref)return (this.c.selected.canonical_history??[]).filter(row=>row.native_context_source?.native_context_issue&&origin(row.native_context_source.origin)&&sourceRef(row.native_context_source.source_ref)&&row.native_context_source.native_context_recovery?.parent_operation_id===row.operation_id&&ref(row.native_context_source.native_context_recovery.expected_native_ref)).slice(-50).map(row=>{const value=row.native_context_source;return {scope,origin:value.origin,source:{parent_operation_id:row.operation_id,expected_native_ref:value.native_context_recovery.expected_native_ref,source_ref:value.source_ref},issue:value.native_context_issue,current:value.native_context_recovery.record_current===true};});
  return (this.c.selected.native_messages??[]).filter(m=>m.native_context_issue&&m.native_context_recovery?.parent_operation_id===m.operation_id&&ref(m.native_context_recovery.expected_native_ref)).slice(-40).map(m=>({scope,origin:scope,source:{parent_operation_id:m.operation_id,expected_native_ref:m.native_context_recovery.expected_native_ref},issue:m.native_context_issue,current:m.native_context_recovery.record_current===true}));
 }
 entries(subject){return this.c.state.value.requests.filter(e=>e.method===method(subject.scope,1)&&same(e.scope,subject.scope)&&same(e.native_recovery_source,subject.source));}
 token(subject,entry){return digest({scope:subject.scope,source:subject.source,issue:subject.issue,request_id:entry?.request_id??null});}
 snapshot(){return {supported:this.supported(),items:this.sources().map(subject=>{
  const e=this.entries(subject).at(-1),valid=!e||validEntry(e),v=valid?e?.native_recovery_receipt:null,token=this.token(subject,e),plan=this.plans.get(token),phase=!valid?'UNAVAILABLE':v?.phase??(e?.native_recovery_not_found?'NOT_FOUND':e?'PENDING':'READY');
  return {parent_operation_id:subject.source.parent_operation_id,token,phase,supported:this.supported(),busy:this.running.has(digest([subject.scope,subject.source])),current:subject.current,prepared:!!plan,target_label:plan?(plan.effective_target.label??plan.effective_target.project_id):null,error:valid?e?.native_recovery_error??null:'NATIVE_CONTEXT_RECOVERY_UNAVAILABLE',can_continue:phase==='NOT_FOUND'||phase==='STOP_CONFIRMED'||phase==='STOP_PENDING'&&v?.stop_status==='NOT_FOUND',retry_stop:phase==='NOT_FOUND'||phase==='STOP_PENDING'&&v?.stop_status==='NOT_FOUND',request_id:e?.request_id??null,can_open:phase==='COMPLETE'&&this.c.canonicalConversations.supported()&&(this.c.client?.projectIds===null||Array.isArray(this.c.client?.projectIds)&&v?.creation_receipt?.project_id!==null&&this.c.client.projectIds.includes(v.creation_receipt.project_id))};
 })};}
 current(token){const subject=this.sources().find(s=>this.token(s,this.entries(s).at(-1))===token);check(subject&&this.supported());return subject;}
 async locked(subject,work){const key=digest([subject.scope,subject.source]);if(this.running.has(key))fail('NATIVE_CONTEXT_RECOVERY_PENDING');this.running.add(key);this.c.emit();try{return await work();}finally{this.running.delete(key);this.c.emit();}}
 async prepare(token){const subject=this.current(token);check(subject.current);const client=this.c.client,epoch=this.c.epoch;
  return this.locked(subject,async()=>{const p=prepare(await this.c.read(method(subject.scope,0),subject.scope,subject.source),subject.scope,subject.source,subject.origin);check(this.c.client===client&&this.c.epoch===epoch&&same(this.current(token),subject));if(this.plans.size>=16)this.plans.delete(this.plans.keys().next().value);this.plans.set(token,p);this.c.emit();return p;});
 }
 metadata(subject,p){const r=request(p);return {operation_id:r.operation_id,native_recovery_source:copy(subject.source),native_recovery_origin:copy(subject.origin),native_recovery_preparation:copy(p),native_recovery_parent:p.existing_recovery?.parent_operation_id??p.parent_operation_id,native_recovery_request:copy(r),native_recovery_request_sha256:digest(r)};}
 async start(token){const subject=this.current(token),p=this.plans.get(token);check(subject.current&&p);const prior=this.entries(subject).at(-1);check(!prior||prior.native_recovery_not_found===true);
  const client=this.c.client,epoch=this.c.epoch;return this.locked(subject,async()=>{
   if(prior){try{await this.readOriginal(prior);fail('NATIVE_CONTEXT_RECOVERY_CHANGED');}catch(error){if(error.code!=='NATIVE_CONTEXT_RECOVERY_NOT_FOUND')throw error;}}
   check(this.c.client===client&&this.c.epoch===epoch);this.current(token);
   const meta=this.metadata(subject,p);
   if(p.existing_recovery){const e={request_id:this.c.newId(),method:method(subject.scope,1),scope:copy(subject.scope),status:'UNKNOWN',...meta};this.c.state.commit(v=>{v.requests.push(e);});this.c.emit();return this.readOriginal(e);}
   check(same(this.current(token),subject));return this.c.mutation(method(subject.scope,1),subject.scope,{...(subject.source.source_ref?{source_ref:subject.source.source_ref}:{}),parent_operation_id:meta.native_recovery_parent,request:meta.native_recovery_request,preparation:p},meta);
  });
 }
 onEnvelope(entry,envelope){
  let verified=null,code=envelope.error?.code??'NATIVE_CONTEXT_RECOVERY_PENDING';
  try{check(validEntry(entry)&&envelope.status==='OK');verified=receipt(envelope.result,entry);}catch{}
  this.c.state.commit(value=>{const row=value.requests.find(e=>e.request_id===entry.request_id);check(row&&row.native_recovery_request_sha256===entry.native_recovery_request_sha256);
   if(row.native_recovery_initial_transport_status===undefined){row.native_recovery_initial_transport_status=row.native_recovery_transport_status??(row.status==='UNKNOWN'?'UNKNOWN':envelope.status);if(envelope.error?.code)row.native_recovery_initial_transport_error=envelope.error.code;}
   row.native_recovery_transport_status=envelope.status;row.native_recovery_error=verified?null:code;row.native_recovery_not_found=!verified&&code==='NATIVE_CONTEXT_RECOVERY_NOT_FOUND';
   if(verified){row.native_recovery_receipt=verified;row.status=verified.phase==='COMPLETE'?'OK':'UNKNOWN';}else row.status='UNKNOWN';
  });this.c.emit();return verified;
 }
 async readOriginal(entry){check(validEntry(entry));let value;
  try{value=await this.c.read(method(entry.scope,2),entry.scope,{...(entry.native_recovery_source.source_ref?{source_ref:entry.native_recovery_source.source_ref}:{}),parent_operation_id:entry.native_recovery_parent,operation_id:entry.operation_id,request_sha256:entry.native_recovery_request_sha256});}
  catch(error){this.onEnvelope(entry,{status:'ERROR',error:{code:error.code}});throw error;}
  const verified=this.onEnvelope(entry,{status:'OK',result:value});check(verified);return verified;
 }
 inspectEntry(id){const entry=this.c.state.value.requests.find(e=>e.request_id===id);check(validEntry(entry));return this.locked({scope:entry.scope,source:entry.native_recovery_source},()=>this.readOriginal(entry));}
 inspect(token){const subject=this.current(token),e=this.entries(subject).at(-1);check(e);return this.inspectEntry(e.request_id);}
 async continueOriginal(token){const subject=this.current(token),e=this.entries(subject).at(-1);check(validEntry(e));const client=this.c.client,epoch=this.c.epoch;
  return this.locked(subject,async()=>{
   try{const r=await this.readOriginal(e);if(r.phase==='COMPLETE'||r.phase==='STOP_PENDING'&&r.stop_status!=='NOT_FOUND')return r;}catch(error){if(error.code!=='NATIVE_CONTEXT_RECOVERY_NOT_FOUND')throw error;}
   check(this.c.client===client&&this.c.epoch===epoch);this.current(token);return this.c.mutation(method(subject.scope,1),subject.scope,{...(subject.source.source_ref?{source_ref:subject.source.source_ref}:{}),parent_operation_id:e.native_recovery_parent,request:e.native_recovery_request,preparation:e.native_recovery_preparation},this.metadata(subject,e.native_recovery_preparation));
  });
 }
 async reveal(token){const subject=this.current(token),e=this.entries(subject).at(-1);check(validEntry(e));const client=this.c.client,epoch=this.c.epoch;
  return this.locked(subject,async()=>{const r=await this.readOriginal(e);check(r.phase==='COMPLETE'&&this.c.client===client&&this.c.epoch===epoch);this.current(token);const created=r.creation_receipt;
   check(this.c.canonicalConversations.supported()&&(client.projectIds===null||Array.isArray(client.projectIds)&&created.project_id!==null&&client.projectIds.includes(created.project_id)));
   return this.c.canonicalConversations.openReference({project_id:created.project_id,conversation_ref:created.conversation_ref});
  });
 }
}
module.exports={NativeContextRecovery,METHODS,CANONICAL_METHODS,validEntry,prepare,receipt};
