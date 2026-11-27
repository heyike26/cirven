'use strict';
// Explicit metadata-only recovery. Original actor requests stay in their slot.
const {canonical,hash,validateScope}=require('./client.cjs');
const {fileURLToPath}=require('node:url');
const SHA=/^[a-f0-9]{64}$/,ID=/^[A-Za-z0-9_-]{1,100}$/;
const METHODS=['session.editor.observer.recovery.prepare','session.editor.observer.recovery.command','session.editor.observer.recovery.operation'];
const same=(a,b)=>canonical(a)===canonical(b),copy=v=>JSON.parse(JSON.stringify(v));
function fail(code){throw Object.assign(new Error(code),{code});}
function validRecord(v,slot){try{if(!v)return true;validateScope(v.scope);const r=v.request;
 return ID.test(v.request_id??'')&&['PENDING','UNKNOWN','ACCEPTED','REJECTED','NOT_SENT'].includes(v.status)&&r&&Object.keys(r).sort().join(',')==='editor_client_id,editor_parent_process_id,editor_process_id,editor_window_id,expected_revision,generation,observation,observer_id,operation_id,preparation_sha256,session_ref,user_preserved_original,workspace_ref'&&same(r.session_ref,v.scope.session_ref)&&r.generation===v.scope.generation&&v.scope.project_id===slot.scope.project_id&&same(r.session_ref,slot.scope.session_ref)&&same(r.workspace_ref,slot.workspace_ref)&&r.observer_id===slot.id&&ID.test(r.editor_client_id)&&ID.test(r.editor_window_id)&&ID.test(r.operation_id)&&Number.isSafeInteger(r.editor_process_id)&&r.editor_process_id>0&&Number.isSafeInteger(r.editor_parent_process_id)&&r.editor_parent_process_id>0&&Number.isSafeInteger(r.expected_revision)&&r.expected_revision>=1&&SHA.test(r.preparation_sha256)&&r.user_preserved_original===true&&r.observation?.capture_ref===null&&r.observation.permission_ref===null&&r.observation.documents_complete===true&&Array.isArray(r.observation.documents)&&r.observation.documents.length<=64&&r.observation.documents.every(d=>d.is_dirty===false&&typeof d.path==='string'&&SHA.test(d.content_sha256)&&!('text'in d))&&Number.isSafeInteger(r.observation.captured_at_epoch_ms)&&SHA.test(v.request_sha256)&&v.request_sha256===hash(canonical(r))&&Buffer.byteLength(JSON.stringify(v))<=65536&&(!v.receipt||v.receipt.request_sha256===v.request_sha256);
 }catch{return false;}}
function validStored(v){return validRecord(v.recovery,v)&&(!v.recovery_history||Array.isArray(v.recovery_history)&&v.recovery_history.length<=2&&v.recovery_history.every(r=>validRecord(r,v)));}
function validateReceipt(value,intent,slot){const r=intent.request;
 if(!value||value.kind!=='EDITOR_OBSERVER_RECOVERY'||value.id!==r.operation_id||!same(value.scope,{...intent.scope,workspace_ref:r.workspace_ref})||value.request_sha256!==intent.request_sha256||value.observer_id!==slot.id||value.editor_client_id!==r.editor_client_id||value.editor_window_id!==r.editor_window_id||value.original_editor_client_id!==slot.actor||value.original_editor_window_id!==slot.window||!['SAME_PROCESS','ENDED'].includes(value.original_process_state)||value.phase!=='RETIRED'||value.revision!==r.expected_revision+1||value.status!=='RETURNED'||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.permission_granted!==false||value.automatic_replay!==false||value.atomic_editor_write_guarantee!==false||value.original_evidence_retained!==true||value.observation_time_scope!=='AT_ORIGINAL_COMMAND_ONLY')fail('EDITOR_OBSERVATION_INVALID');return copy(value);
}
class ObserverRecovery{
 constructor(parent){this.p=parent;this.c=parent.c;}
 supported(){return METHODS.every(m=>this.c.client?.methods?.includes(m));}
 needed(v){return v.recovered||v.actor!==this.c.client?.ticket?.client_id||Boolean(v.recovery);}
 finish(key,proof){this.p.update(key,v=>{v.stage='RETIRED';v.recovery_state=copy(proof);v.error=null;v.capture_error=null;});return proof;}
 async inspectOriginal(key){const v=this.p.slots()[key],i=v.recovery;if(!i||i.request.editor_client_id!==this.c.client?.ticket?.client_id)fail('TRUSTED_CLIENT_REQUIRED');
  const result=validateReceipt(await this.c.read(METHODS[2],i.scope,{operation_id:i.request.operation_id}),i,v);
  this.p.update(key,row=>{row.recovery.receipt=result;row.recovery.status='ACCEPTED';});return this.finish(key,{phase:'RETIRED',revision:result.revision,basis:'ORIGINAL_RECOVERY_RECEIPT'});
 }
 async prepare(key){if(!this.supported()||!this.p.canObserve()||!this.p.host||this.p.subscription?.complete===false)fail('CAPABILITY_DENIED');
  const v=this.p.slots()[key],client=this.c.client,address={observer_id:key,workspace_ref:v.workspace_ref,editor_window_id:this.c.state.value.slot,editor_process_id:process.pid,editor_parent_process_id:process.ppid};
  const value=await this.c.read(METHODS[0],v.scope,address),scope=validateScope({project_id:value.scope?.project_id,session_ref:value.scope?.session_ref,generation:value.scope?.generation});
  const basis={scope:value.scope,observer_id:value.observer_id,revision:value.revision,phase:value.phase,runtime_id:value.runtime_id,editor_client_id:value.editor_client_id,editor_window_id:value.editor_window_id,process_identity:value.process_identity,original_process_identity:value.original_process_identity,original_editor_client_id:value.original_editor_client_id,original_editor_window_id:value.original_editor_window_id,original_process_state:value.original_process_state,root_identity:value.root_identity,root_path_sha256:value.root_path_sha256};
  if(this.p.disposed||this.c.client!==client||value.kind!=='EDITOR_OBSERVER_RECOVERY_PREPARATION'||scope.project_id!==v.scope.project_id||!same(scope.session_ref,v.scope.session_ref)||!same(value.scope.workspace_ref,v.workspace_ref)||value.observer_id!==key||value.editor_client_id!==client.ticket.client_id||value.editor_window_id!==address.editor_window_id||value.original_editor_client_id!==v.actor||value.original_editor_window_id!==v.window||!['ACTIVE','RETIRED'].includes(value.phase)||!['SAME_PROCESS','ENDED','OTHER_LIVE'].includes(value.original_process_state)||value.original_process_state==='OTHER_LIVE'&&value.phase!=='RETIRED'||value.process_identity?.pid!==process.pid||value.process_identity?.parent_pid!==process.ppid||value.preparation_sha256!==hash(canonical(basis))||!value.root_uri?.startsWith('file:///')||value.root_path_sha256!==hash(canonical(fileURLToPath(value.root_uri)))||v.root_uri&&v.root_uri!==value.root_uri||value.read_only!==true||value.current_scope_is_execution_authorization!==false||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.permission_granted!==false||value.automatic_replay!==false)fail('EDITOR_OBSERVATION_INVALID');
  this.p.update(key,row=>{row.scope=scope;row.root_uri=value.root_uri;row.error=null;});return {value,scope,address,client};
 }
 async act(key,action){let v=this.p.slots()[key];if(v.recovery&&['PENDING','UNKNOWN','ACCEPTED'].includes(v.recovery.status)&&v.recovery.request.editor_client_id===this.c.client?.ticket?.client_id)return this.inspectOriginal(key);
  const {value,scope,address,client}=await this.prepare(key);
  // This current metadata proof never changes an old UNKNOWN into success.
  if(value.phase==='RETIRED')return this.finish(key,{phase:'RETIRED',revision:value.revision,basis:'CURRENT_METADATA_ONLY_ORIGINAL_UNKNOWN_RETAINED'});
  if(action==='inspect')return value;
  v=this.p.slots()[key];const stamp=this.p.host.workspaceStamp(value.root_uri),observation=this.p.host.observeWorkspace(value,null);observation.capture_ref=null;
  if(!observation.documents_complete||observation.documents.some(d=>d.is_dirty)||observation.availability!=='OBSERVED'&&observation.unavailable_reason!=='NAMESPACE_CHANGED')fail('DIRTY_EDITOR');
  const request={...address,session_ref:scope.session_ref,generation:scope.generation,editor_client_id:client.ticket.client_id,operation_id:this.c.newId(),expected_revision:value.revision,preparation_sha256:value.preparation_sha256,observation,user_preserved_original:true},intent={scope,request,request_sha256:hash(canonical(request)),request_id:this.c.newId(),status:'PENDING',receipt:null};
  if(!validRecord(intent,v))fail('EDITOR_OBSERVATION_INVALID');
  this.p.update(key,row=>{if(row.recovery){row.recovery_history??=[];if(row.recovery_history.length>=2)fail('LOCAL_CAPACITY');row.recovery_history.push(copy(row.recovery));}row.recovery=copy(intent);});
  if(this.p.disposed||this.c.client!==client||!this.p.canObserve()||this.p.host.workspaceStamp(value.root_uri)!==stamp){this.p.update(key,row=>{row.recovery.status='NOT_SENT';row.error='EDITOR_DOCUMENT_CHANGED';});fail('EDITOR_DOCUMENT_CHANGED');}
  const {session_ref,generation,editor_client_id,...payload}=request;let envelope;try{envelope=await client.rpc(METHODS[1],scope,payload,intent.request_id);}catch{envelope={status:'UNKNOWN'};}
  this.p.update(key,row=>{row.recovery.status='UNKNOWN';});
  if(envelope.status==='OK'){const result=validateReceipt(envelope.result,intent,v);this.p.update(key,row=>{row.recovery.receipt=result;row.recovery.status='ACCEPTED';});return this.finish(key,{phase:'RETIRED',revision:result.revision,basis:'ORIGINAL_RECOVERY_RECEIPT'});}
  if(envelope.status==='ERROR'&&['API_REQUEST_REJECTED','CAPABILITY_DENIED','SCOPE_DENIED','BRIDGE_BUSY','PAIRING_REQUIRED','PAIRING_REVOKED','INVALID_PARAMS'].includes(envelope.error?.code)){this.p.update(key,row=>{row.recovery.status='REJECTED';row.recovery.failure_code=envelope.error.code;row.error='EDITOR_OBSERVATION_REQUIRED';});fail('EDITOR_OBSERVATION_REQUIRED');}
  this.p.update(key,row=>{row.error='ORIGINAL_REQUEST_UNRESOLVED';});fail('ORIGINAL_REQUEST_UNRESOLVED');
 }
}
module.exports={ObserverRecovery,validStored,validRecord,validateReceipt,METHODS};
