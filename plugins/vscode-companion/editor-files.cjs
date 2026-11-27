'use strict';
// Original editor handoff only. OS effects remain in the existing paired App.
const {canonical,hash}=require('./client.cjs');
const SHA=/^[a-f0-9]{64}$/,ID=/^[A-Za-z0-9_-]{1,100}$/;
const METHODS=['session.editor.file.prepare','session.editor.file.command','session.editor.file.operation'];
const ACTIONS=['COMMIT','UNDO','CONFIRM','MANUAL_RESOLVE'];
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v),same=(a,b)=>canonical(a)===canonical(b),copy=v=>JSON.parse(JSON.stringify(v));
function fail(code){throw Object.assign(new Error(code),{code});}
function address(entry,action){return {session_ref:entry.scope.session_ref,generation:entry.scope.generation,target_operation_id:entry.request.operation_id,lease_ref:entry.lease.lease_ref,editor_client_id:entry.request.editor_client_id,editor_window_id:entry.request.editor_window_id,editor_nonce:entry.request.editor_nonce,expected_revision:entry.lease.revision,action};}
function requestValid(value,slot){try{
 if(!object(value)||Object.keys(value).sort().join(',')!=='action,editor_client_id,editor_nonce,editor_observation,editor_window_id,expected_revision,generation,lease_ref,operation_id,preparation_sha256,session_ref,target_operation_id,user_approved'||!ID.test(value.operation_id??'')||!SHA.test(value.preparation_sha256??'')||!ACTIONS.includes(value.action)||value.user_approved!==true||!Number.isSafeInteger(value.expected_revision)||value.expected_revision<1||value.expected_revision>64)return false;
 const exact=address({...slot,lease:{...slot.lease,revision:value.expected_revision}},value.action);
 for(const key of Object.keys(exact))if(!same(value[key],exact[key]))return false;
 const seen=value.editor_observation;if(!object(seen)||Object.keys(seen).sort().join(',')!=='documents,native_commands_settled'||seen.native_commands_settled!==true||!Array.isArray(seen.documents)||seen.documents.length>16)return false;
 return seen.documents.every(doc=>object(doc)&&Object.keys(doc).sort().join(',')==='content_sha256,document_version,is_dirty'&&Number.isSafeInteger(doc.document_version)&&doc.document_version>=1&&doc.document_version<=2147483647&&SHA.test(doc.content_sha256??'')&&doc.is_dirty===false);
 }catch{return false;}}
function preparation(value,slot,action){const g=slot.lease?.grant,body=address(slot,action);
 if(!object(value)||value.kind!=='EDITOR_FILE_PREPARATION'||!same(value.scope,g?.scope)||!same(value.lease_ref,body.lease_ref)||value.revision!==body.expected_revision||value.target_operation_id!==body.target_operation_id||value.action!==action||value.editor_client_id!==body.editor_client_id||value.editor_window_id!==body.editor_window_id||value.read_only!==true||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.automatic_replay!==false||typeof value.editor_uri!=='string'||value.editor_uri.length>8192||!value.editor_uri.startsWith('file:///')||Buffer.byteLength(JSON.stringify(value))>65536)fail('EDITOR_CHANGE_MISMATCH');
 const s=value.snapshot;if(!object(s)||!['OBSERVED','UNAVAILABLE'].includes(s.availability)||s.workspace_binding_sha256!==g.scope.workspace_ref.binding_sha256)fail('EDITOR_CHANGE_MISMATCH');
 if(action!=='MANUAL_RESOLVE'&&s.metadata_recovery_version!==1)fail('EDITOR_CAPABILITY_UNAVAILABLE');
 if(s.availability==='OBSERVED'){
  const b=s.saved,p=s.location?.parent_ref,w=g.scope.workspace_ref;
  if(!object(b)||typeof b.exists!=='boolean'||!Number.isSafeInteger(b.byte_length)||b.byte_length<0||b.byte_length>524288||b.exists&&!SHA.test(b.sha256??'')||!b.exists&&(b.sha256!==null||b.byte_length!==0)||!p||p.project_id!==slot.scope.project_id||p.workspace_id!==w.id||p.binding_revision!==w.revision||p.binding_sha256!==w.binding_sha256||!/^\d{1,32}$/.test(p.device??'')||!/^\d{1,32}$/.test(p.inode??'')||typeof s.location.name!=='string'||typeof s.location.path!=='string')fail('EDITOR_CHANGE_MISMATCH');
  if(s.editor_text_sha256!==null&&!SHA.test(s.editor_text_sha256??''))fail('EDITOR_CHANGE_MISMATCH');
  if(s.metadata_sha256!==undefined&&s.metadata_sha256!==null&&(!SHA.test(s.metadata_sha256)||!/^\d{1,32}$/.test(s.file_identity?.device??'')||!/^\d{1,32}$/.test(s.file_identity?.inode??'')))fail('EDITOR_CHANGE_MISMATCH');
 }else if(s.saved!==null||s.location!==null||s.editor_text_sha256!==null||action!=='MANUAL_RESOLVE')fail('EDITOR_SOURCE_UNAVAILABLE');
 const basis={scope:value.scope,lease_ref:value.lease_ref,revision:value.revision,target_operation_id:value.target_operation_id,action:value.action,snapshot:s};
 if(value.preparation_sha256!==hash(canonical(basis)))fail('EDITOR_CHANGE_MISMATCH');return copy(value);}
function receipt(envelope,entry,slot,validateLease){
 if(envelope.status==='ERROR'&&envelope.error?.code==='API_REQUEST_REJECTED')return {status:'ERROR'};
 if(envelope.status!=='OK')return {status:'UNKNOWN'};const v=envelope.result,r=entry.file_request;
 if(!requestValid(r,slot)||!object(v)||v.kind!=='EDITOR_FILE_ACTION'||v.id!==r.operation_id||!same(v.session_ref,slot.scope.session_ref)||v.generation!==slot.scope.generation||v.request_sha256!==hash(canonical(r))||v.automatic_replay!==false||v.automatic_file_replay!==false)fail('EDITOR_CHANGE_MISMATCH');
 if(v.status==='FAILED'&&v.file_effect==='NOT_INVOKED')return {status:'ERROR'};
 if(v.status!=='RETURNED')return {status:'UNKNOWN'};
 const lease=validateLease(v.editor_change,slot),file=v.file_result,mutating=['COMMIT','UNDO'].includes(r.action),expected={COMMIT:'APPLIED',UNDO:'UNDONE',CONFIRM:'CONFIRMED',MANUAL_RESOLVE:'MANUALLY_RESOLVED'}[r.action];
 if(!same(lease.lease_ref,r.lease_ref)||lease.revision!==r.expected_revision+(mutating?2:1)||!object(file)||file.status!==expected||file.workspace_writes!==(mutating?1:0)||file.native_text_undo!==false||!(mutating?lease.phase==='BUFFER_CHANGED':r.action==='CONFIRM'?['SAVED','CANCELLED'].includes(lease.phase):lease.phase==='MANUAL_SAVED'))fail('EDITOR_CHANGE_MISMATCH');
 return {status:'OK',lease,file:{status:file.status,workspace_writes:file.workspace_writes,metadata_recovery_recorded:file.metadata_recovery_recorded===true,...file.observed_saved?{observed_saved:copy(file.observed_saved)}:{}}};}
class EditorFiles{
 constructor(parent,validateLease){this.p=parent;this.c=parent.c;this.validateLease=validateLease;}
 supported(){return METHODS.every(method=>this.c.client?.methods?.includes(method));}
 onEnvelope(entry,envelope){const slot=this.p.slots()[entry.editor_slot_key];if(!slot)return;let result;try{result=receipt(envelope,entry,slot,this.validateLease);}catch{result={status:'UNKNOWN'};}
  try{this.c.state.commit(state=>{const req=state.requests.find(row=>row.request_id===entry.request_id),v=state.editor_changes?.[entry.editor_slot_key];if(!req||!v)fail('EDITOR_CHANGE_CHANGED');req.status=result.status;
   if(result.lease&&(!v.lease||result.lease.revision>=v.lease.revision)){v.lease=result.lease;v.file_result=result.file;v.file_preview=null;v.stage=['SAVED','CANCELLED','MANUAL_SAVED'].includes(v.lease.phase)?'CLOSED':'BUFFER_CHANGED';}
   if(result.status==='UNKNOWN'){v.stage='UNKNOWN';v.error='ORIGINAL_REQUEST_UNRESOLVED';}else v.error=result.status==='ERROR'?'API_REQUEST_REJECTED':null;v.local_revision++;});
   if(result.status==='UNKNOWN')this.c.known.delete(entry.request_id);else this.c.known.set(entry.request_id,envelope);
   if(result.lease&&same(this.c.reviewContext?.selection,result.lease.grant?.selection)){this.c.reviewContext=null;this.c.reviewError=null;this.c.reviewEpoch++;}
  }catch{this.c.error='LOCAL_RECEIPT_UNPERSISTED';}this.c.emit();}
 async readLease(key){const v=this.p.slots()[key];if(!v||this.p.actor()!==v.request.editor_client_id)fail('TRUSTED_CLIENT_REQUIRED');
  const current=this.validateLease(await this.c.read('session.editor.get',v.scope,{operation_id:v.request.operation_id}),v);
  this.p.update(key,slot=>{if(!slot.lease||current.revision>=slot.lease.revision){slot.lease=current;if(['SAVED','CANCELLED','MANUAL_SAVED','FAILED'].includes(current.phase))slot.stage=current.phase==='FAILED'?'REJECTED':'CLOSED';}});return current;}
 async perform(key,action,host,current=()=>true){if(!ACTIONS.includes(action)||!this.supported())fail('CAPABILITY_DENIED');let entry=this.p.slots()[key];
  if(!entry||!this.p.ownerAllowed(entry))fail('EDITOR_ORIGINAL_PROCESS_UNCONFIRMED');
  if(entry.native_intent&&!entry.native_returned&&!entry.recovered)fail('EDITOR_NATIVE_OUTCOME_UNKNOWN');
  if(action==='MANUAL_RESOLVE')await this.readLease(key);else{await this.p.inspectKey(key);if(this.p.pending(key))fail('ORIGINAL_REQUEST_UNRESOLVED');}
  entry=this.p.slots()[key];if(entry.stage==='CLOSED'||entry.stage==='REJECTED')return;
  if(!entry.lease?.grant||this.p.actor()!==entry.request.editor_client_id)fail('EDITOR_CHANGE_MISMATCH');
  if(action==='COMMIT'&&(entry.recovered||entry.file_attempted||entry.stage!=='RESERVED'||entry.lease.phase!=='READY'))fail('ORIGINAL_REQUEST_UNRESOLVED');
  const body=address(entry,action),{session_ref,generation,editor_client_id,...payload}=body;
  const prepared=preparation(await this.c.read('session.editor.file.prepare',entry.scope,payload),entry,action);
  if(!current()||this.p.disposed)fail('EDITOR_CHANGE_CHANGED');
  const observed=host.observeFile({...this.p.slots()[key],editor_uri:entry.editor_uri||prepared.editor_uri},prepared,action);
  this.p.update(key,v=>{v.editor_uri=prepared.editor_uri;v.file_preview={availability:prepared.snapshot.availability,exists:prepared.snapshot.saved?.exists??null,revision:prepared.revision,action};});
  const request={...body,operation_id:this.c.newId(),preparation_sha256:prepared.preparation_sha256,editor_observation:{documents:observed.documents,native_commands_settled:true},user_approved:true};
  if(!requestValid(request,this.p.slots()[key]))fail('EDITOR_CHANGE_MISMATCH');
  this.p.update(key,v=>{v.stage='FILE_INTENT';v.file_attempted=true;v.file_action=action;});
  const final=host.observeFile(this.p.slots()[key],prepared,action);
  if(!same(observed,final)||!current()||this.p.disposed)fail('EDITOR_DOCUMENT_CHANGED');
  const {session_ref:ref,generation:gen,editor_client_id:actor,...command}=request;
  return this.c.mutation('session.editor.file.command',entry.scope,command,{file_request:request,editor_slot_key:key});}
 async act(token,action,host){if(this.p.busy)fail('EDITOR_CHANGE_BUSY');const {key}=this.p.get(token);this.p.busy=true;this.c.emit();
  try{return await this.perform(key,action,host);}catch(error){this.p.update(key,v=>{v.error=/^[A-Z0-9_]{1,80}$/.test(error?.code??'')?error.code:'EDITOR_FILE_OUTCOME_UNKNOWN';});throw error;}
  finally{this.p.busy=false;this.c.emit();}}
}
module.exports={EditorFiles,requestValid,preparation,receipt,METHODS,ACTIONS};
