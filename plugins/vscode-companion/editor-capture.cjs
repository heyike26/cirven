'use strict';
// Original capture intent and authenticated metadata transport. No second Agent.
const {canonical,hash,validateScope}=require('./client.cjs');
const SHA=/^[a-f0-9]{64}$/,ID=/^[A-Za-z0-9_-]{1,100}$/;
const METHODS=['session.editor.capture.request','session.editor.capture.operation','session.editor.capture.status','session.editor.capture.wait','session.editor.capture.listen'];
const same=(a,b)=>canonical(a)===canonical(b),copy=v=>JSON.parse(JSON.stringify(v));
function fail(code){throw Object.assign(new Error(code),{code});}
function validStored(v,slot){try{if(!v)return true;const r=v.request;validateScope(v.scope);return ['PENDING','UNKNOWN','ACCEPTED'].includes(v.status)&&ID.test(v.request_id??'')&&r&&ID.test(r.operation_id??'')&&v.scope.project_id===slot.scope.project_id&&Object.keys(r).sort().join(',')==='editor_client_id,generation,operation_id,permission_ref,session_ref,workspace_ref'&&r.editor_client_id===slot.actor&&same(r.workspace_ref,slot.workspace_ref)&&same(r.session_ref,v.scope.session_ref)&&r.generation===v.scope.generation&&SHA.test(v.request_sha256)&&v.request_sha256===hash(canonical(r))&&(!v.receipt||v.receipt.request_sha256===v.request_sha256);}catch{return false;}}
function validate(value,intent,kind){const r=intent.request;if(!value||value.kind!==kind||value.id!==r.operation_id||value.request_sha256!==intent.request_sha256||value.editor_client_id!==r.editor_client_id||!same(value.scope,{...intent.scope,workspace_ref:r.workspace_ref})||!SHA.test(value.capture_ref??'')||!Number.isSafeInteger(value.required_windows)||value.required_windows<0||value.required_windows>16||!Number.isSafeInteger(value.expires_at_ms)||value.permission_granted!==false||value.workspace_writes!==0||value.model_calls!==0||value.native_posts!==0||value.automatic_replay!==false||value.atomic_editor_write_guarantee!==false||kind==='EDITOR_CAPTURE_COHORT'&&value.observation_time_scope!=='IMMUTABLE_CAPTURE_INTENT_NOT_CURRENT_CLEAN_STATE'||kind==='EDITOR_CAPTURE_STATUS'&&value.status_scope!=='CURRENT_METADATA_SNAPSHOT_NOT_EXECUTION_LEASE')fail('EDITOR_CAPTURE_INVALID');if(kind==='EDITOR_CAPTURE_STATUS'&&(!['READY','WAITING','DIRTY','UNAVAILABLE','OWNER_ENDED','STALE','RESTARTED','MEMBERSHIP_CHANGED','TARGET_CHANGED','EXPIRED'].includes(value.state)||value.state==='READY'&&value.ready_windows!==value.required_windows))fail('EDITOR_CAPTURE_INVALID');return copy(value);}
class EditorCaptureCoordinator{
 constructor(parent){this.p=parent;this.c=parent.c;this.events=null;this.client=null;this.busy=false;this.disposed=false;this.active=new Set();this.queue=new Map();this.draining=false;this.delays=new Map();}
 supported(){return METHODS.every(m=>this.c.client?.methods?.includes(m));}
 async ensureEvents(){if(!this.supported())fail('CAPABILITY_DENIED');const client=this.c.client;if(this.client===client&&this.events?.active){await this.events.ready;return;}
  this.events?.dispose();this.client=client;if(typeof client.observeCaptures!=='function')fail('EDITOR_CAPTURE_CHANNEL_REQUIRED');
  this.events=client.observeCaptures(this.c.state.value.slot,event=>this.receive(event,client),code=>{if(this.disposed||this.c.client!==client)return;for(const [key,v]of Object.entries(this.p.slots()))if(v.actor===client.ticket?.client_id&&!v.recovered&&v.stage!=='RETIRED'){try{this.p.update(key,row=>{row.capture_error=code==='DISCONNECTED'?'EDITOR_CAPTURE_CHANNEL_REQUIRED':code;});}catch{}}});
  await this.events.ready;
 }
 receive(event,client){if(this.disposed||this.c.client!==client||event.window_id!==this.c.state.value.slot||!this.p.canObserve()||event.expires_at_ms<Date.now())return;const v=this.p.slots()[event.observer_id];
  if(!v||v.recovered||v.stage==='RETIRED'||v.window!==this.c.state.value.slot||v.actor!==client.ticket?.client_id||(v.capture_refs||[]).includes(event.capture_ref)&&v.capture_bases?.[event.capture_ref]===hash(this.p.host.workspaceStamp(v.root_uri)))return;
  const key=event.observer_id+':'+event.capture_ref;if(this.active.has(key)||this.queue.has(key))return;if(this.queue.size>=128){this.p.update(event.observer_id,row=>{row.capture_error='EDITOR_CAPTURE_CAPACITY';});return;}
  this.queue.set(key,{event,client});void this.drain();}
 async drain(){if(this.draining)return;this.draining=true;try{while(this.queue.size&&!this.disposed){const [key,item]=this.queue.entries().next().value;this.queue.delete(key);this.active.add(key);const {event,client}=item;
   try{if(event.expires_at_ms<Date.now()||this.c.client!==client)continue;for(let attempt=0;;attempt++){try{await this.p.serial(()=>this.p.publish(event.observer_id,{force:true,capture:event.capture_ref,current:()=>!this.disposed&&this.c.client===client&&Date.now()<=event.expires_at_ms}));break;}catch(error){const delay=45*(attempt+1)+(parseInt(event.observer_id.slice(0,2),16)%8)*17;if(error.code!=='BRIDGE_BUSY'||attempt>=3||this.p.slots()[event.observer_id]?.intents.length||Date.now()+delay>event.expires_at_ms||!await this.pause(delay))throw error;}}
    this.p.update(event.observer_id,row=>{row.capture_error=null;});
   }catch(error){try{this.p.update(event.observer_id,row=>{row.capture_error=/^[A-Z0-9_]{1,80}$/.test(error?.code??'')?error.code:'EDITOR_CAPTURE_CHANNEL_REQUIRED';});}catch{}}
   finally{this.active.delete(key);}
  }}finally{this.draining=false;}}
 pause(ms){if(this.disposed)return Promise.resolve(false);return new Promise(resolve=>{const timer=setTimeout(()=>{this.delays.delete(timer);resolve(!this.disposed);},ms);timer.unref?.();this.delays.set(timer,resolve);});}
 async recover(key,{wait=false,current=()=>true}={}){const v=this.p.slots()[key],intent=v?.capture;if(!intent)return null;
  if(intent.request.editor_client_id!==this.c.client?.ticket?.client_id)fail('TRUSTED_CLIENT_REQUIRED');
  if(!intent.receipt){const value=validate(await this.c.read('session.editor.capture.operation',intent.scope,{operation_id:intent.request.operation_id}),intent,'EDITOR_CAPTURE_COHORT');this.p.update(key,row=>{row.capture.receipt=value;row.capture.status='ACCEPTED';});}
  const exact=this.p.slots()[key].capture,method=wait?'session.editor.capture.wait':'session.editor.capture.status';
  const value=validate(await this.c.read(method,exact.scope,{operation_id:exact.request.operation_id,...wait?{capture_ref:exact.receipt.capture_ref}:{}}),exact,'EDITOR_CAPTURE_STATUS');
  if(value.capture_ref!==exact.receipt.capture_ref)fail('EDITOR_CAPTURE_INVALID');this.p.update(key,row=>{row.capture.last_status=value;row.capture_error=value.state==='READY'?null:'EDITOR_CAPTURE_'+value.state;});if(!current())fail('EDITOR_CHANGE_CHANGED');return value;}
 settle(key){this.p.update(key,row=>{row.last_capture=copy(row.capture);row.capture=null;});}
 async synchronize(key,permission,current){if(this.busy)fail('EDITOR_CHANGE_BUSY');this.busy=true;
  try{await this.ensureEvents();if(!current())fail('EDITOR_CHANGE_CHANGED');let v=this.p.slots()[key];
   if(v.capture){const abandoned=v.capture.abandon===true,previous=await this.recover(key,{wait:!abandoned,current});v=this.p.slots()[key];if(previous?.state==='WAITING'&&!abandoned)fail('EDITOR_CAPTURE_WAITING');const reusable=!abandoned&&previous?.state==='READY'&&same(v.capture.request.permission_ref,permission);this.settle(key);if(reusable)return previous;if(!abandoned&&['DIRTY','UNAVAILABLE','OWNER_ENDED'].includes(previous?.state))fail('EDITOR_CAPTURE_'+previous.state);
   }
   v=this.p.slots()[key];const request={operation_id:this.c.newId(),session_ref:v.scope.session_ref,generation:v.scope.generation,workspace_ref:v.workspace_ref,editor_client_id:v.actor,permission_ref:permission},intent={scope:copy(v.scope),request,request_sha256:hash(canonical(request)),request_id:this.c.newId(),status:'PENDING',receipt:null,last_status:null};
   this.p.update(key,row=>{row.capture=copy(intent);});const {session_ref,generation,editor_client_id,...payload}=request;const client=this.c.client;let envelope;
   try{envelope=await client.rpc('session.editor.capture.request',intent.scope,payload,intent.request_id);}catch{envelope={status:'UNKNOWN'};}
   if(envelope.status==='OK'){let original;try{original=validate(envelope.result,intent,'EDITOR_CAPTURE_COHORT');}catch{original=null;}
    if(original)this.p.update(key,row=>{row.capture.receipt=original;row.capture.status='ACCEPTED';});
   }
   if(this.c.client!==client||!current())fail('EDITOR_CHANGE_CHANGED');
   if(!this.p.slots()[key].capture.receipt){if(envelope.status==='ERROR'&&['API_REQUEST_REJECTED','CAPABILITY_DENIED','SCOPE_DENIED','BRIDGE_BUSY','PAIRING_REQUIRED','PAIRING_REVOKED'].includes(envelope.error?.code)){this.p.update(key,row=>{row.capture.failure_code=envelope.error.code;row.capture_error='EDITOR_CAPTURE_REQUEST_REJECTED';});this.settle(key);fail('EDITOR_CAPTURE_REQUEST_REJECTED');}this.p.update(key,row=>{row.capture.status='UNKNOWN';row.capture_error='ORIGINAL_REQUEST_UNRESOLVED';});fail('ORIGINAL_REQUEST_UNRESOLVED');}
   const value=await this.recover(key,{wait:true,current});if(value.state==='WAITING')fail('EDITOR_CAPTURE_WAITING');this.settle(key);if(value.state!=='READY')fail('EDITOR_CAPTURE_'+value.state);return value;
  }finally{this.busy=false;this.c.emit();}}
 dispose(){this.disposed=true;for(const [timer,resolve]of this.delays){clearTimeout(timer);resolve(false);}this.delays.clear();this.events?.dispose();this.queue.clear();this.active.clear();}
}
module.exports={EditorCaptureCoordinator,validStored,validate,METHODS};
