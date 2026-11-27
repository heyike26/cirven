'use strict';
const wire=require('./work-requirement-wire.cjs'),tools=require('./session-tool.cjs');
const METHODS=Object.freeze(['session.work.requirements','session.work.requirements.link','session.work.requirements.operation']);
const copy=structuredClone,same=wire.same,hash=wire.hash,ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
function check(value,code='WORK_REQUIREMENT_CHANGED'){if(!value)throw Object.assign(Error('原关联依据需要核对；选择和原请求保留。'),{code});}
function owner(scope,workspace,root){return {project_id:scope.project_id,session_ref:copy(scope.session_ref),workspace_ref:copy(workspace),root_operation_id:root};}
const keyOf=o=>hash(o),scopeFor=(s,w,r)=>({session_ref:copy(s.session_ref),generation:s.generation,workspace_ref:copy(w),root_operation_id:r});
function validOwner(o){return o&&ID.test(o.project_id??'')&&ID.test(o.session_ref?.id??'')&&o.session_ref.revision===1&&SHA.test(o.session_ref.sha256??'')&&SHA.test(o.workspace_ref?.id??'')&&Number.isSafeInteger(o.workspace_ref.revision)&&o.workspace_ref.revision>=1&&SHA.test(o.workspace_ref.binding_sha256??'')&&ID.test(o.root_operation_id??'');}
function validDrafts(values){try{return values===undefined||values&&typeof values==='object'&&!Array.isArray(values)&&Object.keys(values).length<=32&&Object.entries(values).every(([key,row])=>validOwner(row.owner)&&keyOf(row.owner)===key&&Number.isSafeInteger(row.revision)&&row.revision>=0&&wire.validDraft(row.draft,row.owner)&&Array.isArray(row.recovery)&&row.recovery.length<=4&&row.recovery.every(r=>ID.test(r.id??'')&&wire.validDraft(r.draft,row.owner))&&new Set(row.recovery.map(r=>r.id)).size===row.recovery.length&&Buffer.byteLength(JSON.stringify(row))<=524288);}catch{return false;}}
function validEntry(e){try{return e.method===METHODS[1]&&validOwner(e.work_requirement_owner)&&wire.validRequest(e.work_requirement_request,e.work_requirement_owner)&&e.operation_id===e.work_requirement_request.operation_id&&same(e.scope,{project_id:e.work_requirement_owner.project_id,session_ref:e.work_requirement_request.session_ref,generation:e.work_requirement_request.generation})&&e.work_requirement_checksum===hash(e.work_requirement_request)&&typeof e.work_requirement_initial_unknown==='boolean'&&(!e.work_requirement_receipt||wire.verifyReceipt(e.work_requirement_receipt,e.work_requirement_request,e.work_requirement_checksum,e.work_requirement_owner))&&(!e.work_requirement_disposition||same(e.work_requirement_disposition.root_sha256,e.work_requirement_request.root_sha256)&&Number.isSafeInteger(e.work_requirement_disposition.generation)&&e.work_requirement_disposition.generation>=e.scope.generation&&Number.isSafeInteger(e.work_requirement_disposition.revision)&&e.work_requirement_disposition.revision>=e.work_requirement_request.expected_revision&&(e.status==='ERROR'||e.work_requirement_disposition.generation>e.scope.generation||e.work_requirement_disposition.revision>e.work_requirement_request.expected_revision));}catch{return false;}}
class WorkRequirements{
 constructor(host,scopeOf,safeCode){this.h=host;this.scopeOf=scopeOf;this.safeCode=safeCode;this.dirty=new Map();this.observed=new Map();this.sequence=0;this.busy=false;this.reset();}
 reset(){this.sequence++;this.view=null;this.tool=null;this.loading=false;this.error=null;}
 supported(){return [METHODS[0],METHODS[2]].every(m=>this.h.client?.methods?.includes(m));}
 writable(){return this.supported()&&this.h.client?.methods?.includes(METHODS[1]);}
 scope(){return this.h.selected?.session_ref?this.scopeOf(this.h.selected):null;}
 owns(o){const s=this.scope();return !!s&&s.project_id===o.project_id&&same(s.session_ref,o.session_ref)&&same(this.h.selected.workspace_ref,o.workspace_ref);}
 current(){return !!this.scope()&&!!this.view&&this.view.project_id===this.h.selected?.project_id&&this.owns(owner(this.scope(),this.view.scope.workspace_ref,this.view.scope.root_operation_id))&&same(this.view.scope,scopeFor(this.scope(),this.h.selected.workspace_ref,this.view.scope.root_operation_id));}
 key(){return this.view?keyOf(owner(this.scope(),this.view.scope.workspace_ref,this.view.scope.root_operation_id)):null;}
 slot(){return this.key()?this.dirty.get(this.key())??this.h.state.value.work_requirement_drafts?.[this.key()]??null:null;}
 contextToken(){return this.current()?hash({project_id:this.view.project_id,scope:this.view.scope,root_sha256:this.view.root_sha256,revision:this.view.revision}):null;}
 draftToken(){const slot=this.slot();return slot&&this.current()?hash({key:this.key(),query_sha256:this.view.query_sha256,scope:this.view.scope,revision:this.view.revision,selected:this.view.selected}):null;}
 entries(){return this.h.state.value.requests.filter(e=>e.work_requirement_request&&validEntry(e)&&this.owns(e.work_requirement_owner));}
 pending(o){return this.entries().filter(e=>same(e.work_requirement_owner,o)&&!e.work_requirement_disposition&&!e.work_requirement_receipt&&!this.observed.get(e.request_id)&&['PENDING','UNKNOWN','ERROR'].includes(e.status));}
 stage(key,slot){check(validDrafts({[key]:slot}),'WORK_REQUIREMENT_DRAFT_CAPACITY');this.dirty.set(key,copy(slot));this.h.emit();return this.flush(key);}
 flush(key){const slot=this.dirty.get(key);if(!slot)return;this.h.state.commit(v=>{v.work_requirement_drafts??={};const old=v.work_requirement_drafts[key];check(!old||old.revision<slot.revision,'LOCAL_STATE_CONFLICT');v.work_requirement_drafts[key]=copy(slot);});this.dirty.delete(key);this.h.emit();}
 flushAll(){for(const key of this.dirty.keys())this.flush(key);for(const [id,receipt]of [...this.observed]){const entry=this.h.state.value.requests.find(r=>r.request_id===id);check(entry,'LOCAL_RECEIPT_UNPERSISTED');this.onEnvelope(entry,{status:'OK',result:receipt});check(!this.observed.has(id),'LOCAL_RECEIPT_UNPERSISTED');}}
 update(choice,view=this.view){check(view?.selected&&wire.validChoice(choice));const o=owner(this.scope(),view.scope.workspace_ref,view.scope.root_operation_id),key=keyOf(o),before=this.dirty.get(key)??this.h.state.value.work_requirement_drafts?.[key],recovery=copy(before?.recovery??[]),draft=wire.draftFor(view,choice);
  if(before&&!same(before.draft.choice.requirement_ref,choice.requirement_ref)&&!recovery.some(r=>same(r.draft,before.draft))){check(recovery.length<4,'WORK_REQUIREMENT_DRAFT_CAPACITY');recovery.push({id:this.h.newId(),draft:copy(before.draft)});}
  return this.stage(key,{owner:o,revision:(before?.revision??0)+1,draft,recovery});
 }
 snapshot(){if(this.view&&!this.owns(owner({project_id:this.view.project_id,session_ref:this.view.scope.session_ref},this.view.scope.workspace_ref,this.view.scope.root_operation_id)))this.reset();const slot=this.slot();
  return {supported:this.supported(),writable:this.writable(),tool:this.tool,loading:this.loading,busy:this.busy,error:this.error,view:this.supported()&&this.view?{...this.view,catalog:{...this.view.catalog,entries:this.view.catalog.entries.map(row=>({...row,token:hash(row.ref)}))},links:this.view.links.map(row=>({...row,token:hash(row)}))}:null,draft:slot?.draft??null,draft_token:this.draftToken(),context_token:this.contextToken(),review_token:slot?hash(slot.draft):null,copies:slot?.recovery??[],unsaved:!!this.key()&&this.dirty.has(this.key()),matches:!!slot&&!!this.current()&&wire.matches(this.view,slot.draft),can_save:!!slot&&!!this.current()&&this.writable()&&wire.canSave(this.view,slot.draft)&&!wire.unchanged(this.view,slot.draft)&&this.pending(slot.owner).length===0,
  attempts:this.entries().map(e=>({request_id:e.request_id,root_operation_id:e.work_requirement_owner.root_operation_id,action:e.work_requirement_request.action,version:e.work_requirement_request.requirement_ref.revision,requirement_ids:e.work_requirement_request.requirement_ids,status:this.observed.has(e.request_id)||e.work_requirement_receipt?'RECORDED':e.status==='ERROR'?'REJECTED':'UNKNOWN',unpersisted:this.observed.has(e.request_id)&&!e.work_requirement_receipt,not_found:e.work_requirement_last_observation==='NOT_FOUND',retained:!!e.work_requirement_disposition})).slice(-32)};
 }
 async bounded(run){let timer;try{return await Promise.race([run(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Object.assign(Error('Unknown transport'),{code:'WORK_REQUIREMENT_UNKNOWN'})),25000);})]);}finally{clearTimeout(timer);}}
 async readValue(scope,payload){const workspace=copy(payload.workspace_ref??this.h.selected?.workspace_ref);const value=await this.bounded(()=>this.h.read(METHODS[0],scope,payload)),root=payload.root_operation_id??value?.scope?.root_operation_id;
  const request={...scopeFor(scope,workspace,root),include_catalog:payload.include_catalog??false,catalog_offset:payload.catalog_offset??0,selected_requirement_ref:payload.selected_requirement_ref??null,detail_kind:payload.detail_kind??null,detail_id:payload.detail_id??null,...(payload.selected_source_authority?{selected_source_authority:copy(payload.selected_source_authority)}:{})};
  return wire.validateView(value,{project_id:scope.project_id,session_ref:scope.session_ref},root,request);
 }
 async read(options={}){check(this.supported()&&!this.loading);const h=this.h,s=this.scope(),epoch=h.epoch,seq=++this.sequence,payload={root_operation_id:this.view?.scope.root_operation_id,workspace_ref:h.selected?.workspace_ref,include_catalog:true,catalog_offset:options.offset??0,selected_requirement_ref:options.selected??null,detail_kind:options.detail?.kind??null,detail_id:options.detail?.id??null};
  const requestedAuthority=options.sourceAuthority??(!this.view?.links.some(r=>same(r.requirement_ref,options.selected))&&same(this.slot()?.draft.choice.requirement_ref,options.selected)?this.slot()?.draft.choice.source_authority:undefined);if(requestedAuthority)payload.selected_source_authority=copy(requestedAuthority);
  if(options.tool){const ref=tools.find(h.selected,s,options.tool);check(ref);delete payload.root_operation_id;delete payload.workspace_ref;payload.tool_ref=ref;}
  else if(options.root)payload.root_operation_id=options.root;
  check(s&&s.generation>=1);this.loading=true;this.error=null;h.emit();
  try{const value=await this.readValue(s,payload);if(h.epoch===epoch&&same(this.scope(),s)&&seq===this.sequence){this.view=value;if(options.tool)this.tool=options.tool;return value;}}
  catch(error){if(h.epoch===epoch&&seq===this.sequence)this.error=this.safeCode(error);throw error;}
  finally{if(seq===this.sequence){this.loading=false;h.emit();}}
 }
 async open(token){check(!this.busy);if(this.tool!==token)this.view=null;this.tool=token;const value=await this.read({tool:token}),slot=this.slot();if(value&&slot&&!same(value.selected?.ref,slot.draft.choice.requirement_ref))await this.read({selected:slot.draft.choice.requirement_ref});}
 async choose(token){check(!this.busy&&this.current());const row=this.view.catalog.entries.find(r=>hash(r.ref)===token);check(row?.eligible);const old=this.slot()?.draft,linked=this.view.links.find(r=>r.requirement_ref.id===row.ref.id),next=await this.read({selected:row.ref});if(!next)return;
  const sameSet=old?.choice.requirement_ref.id===row.ref.id;this.update({requirement_ref:row.ref,requirement_ids:copy(sameSet?old.choice.requirement_ids:linked?.requirement_ids??[]),acceptance_ids:copy(sameSet?old.choice.acceptance_ids:linked?.acceptance_ids??[])},next);
 }
 toggle(token,kind,id,checked){check(!this.busy&&this.current()&&this.draftToken()===token&&['requirement_ids','acceptance_ids'].includes(kind)&&/^[A-Za-z0-9_-]{1,40}$/.test(id)&&typeof checked==='boolean');const d=this.slot().draft;check(wire.matches(this.view,d));this.update({...copy(d.choice),[kind]:checked?[...new Set([...d.choice[kind],id])]:d.choice[kind].filter(v=>v!==id)});}
 async review(){check(!this.busy&&this.slot());const d=copy(this.slot().draft),next=await this.read({selected:d.choice.requirement_ref,sourceAuthority:d.choice.source_authority});if(next)this.update(d.choice,next);}
 copyAction(id,remove){check(!this.busy&&this.slot());const key=this.key(),slot=copy(this.slot()),row=slot.recovery.find(r=>r.id===id);check(row);if(remove)slot.recovery=slot.recovery.filter(r=>r.id!==id);else{const old=slot.draft;slot.draft=row.draft;row.draft=old;}slot.revision++;return this.stage(key,slot);}
 metadata(scope,request,extra={}){return {operation_id:request.operation_id,work_requirement_request:copy(request),work_requirement_checksum:hash(request),work_requirement_owner:owner(scope,request.workspace_ref,request.root_operation_id),work_requirement_initial_unknown:false,...extra};}
 onEnvelope(e,envelope){check(validEntry(e));let receipt=this.observed.get(e.request_id)??e.work_requirement_receipt,status='UNKNOWN';
  if(!receipt&&envelope.status==='OK'){try{receipt=wire.verifyReceipt(envelope.result,e.work_requirement_request,e.work_requirement_checksum,e.work_requirement_owner);}catch{}}
  if(receipt){this.observed.set(e.request_id,copy(receipt));status='OK';}
  else if(envelope.status==='ERROR'&&envelope.error?.code==='WORK_REQUIREMENT_REJECTED'&&!e.work_requirement_initial_unknown)status='ERROR';
  try{this.h.state.commit(v=>{for(const row of v.requests.filter(r=>r.work_requirement_checksum===e.work_requirement_checksum&&validEntry(r))){row.status=status;row.work_requirement_initial_observation??=status==='OK'?'RETURNED':status==='ERROR'?'REJECTED':'UNKNOWN';row.work_requirement_initial_unknown||=status==='UNKNOWN';if(receipt)row.work_requirement_receipt=copy(receipt);}
    const slot=v.work_requirement_drafts?.[keyOf(e.work_requirement_owner)],d=slot?.draft,r=e.work_requirement_request;
    if(receipt&&r.action==='ASSOCIATE'&&d&&d.revision===r.expected_revision&&d.root_sha256===r.root_sha256&&same(d.scope,{session_ref:r.session_ref,generation:r.generation,workspace_ref:r.workspace_ref,root_operation_id:r.root_operation_id})&&same(d.choice,{requirement_ref:r.requirement_ref,requirement_ids:r.requirement_ids,acceptance_ids:r.acceptance_ids,...(r.source_authority?{source_authority:r.source_authority}:{})})){d.revision=receipt.revision;slot.revision++;}
   });for(const row of this.h.state.value.requests.filter(r=>r.work_requirement_checksum===e.work_requirement_checksum))this.observed.delete(row.request_id);this.error=status==='UNKNOWN'?'WORK_REQUIREMENT_UNKNOWN':status==='ERROR'?'WORK_REQUIREMENT_REJECTED':null;
  }catch{this.error='LOCAL_RECEIPT_UNPERSISTED';}
  this.h.emit();return {status:receipt?'RECORDED':status==='ERROR'?'REJECTED':'UNKNOWN',receipt};
 }
 transportUnknown(id,error){const e=this.h.state.value.requests.find(r=>r.request_id===id);if(e)this.onEnvelope(e,{status:'UNKNOWN'});this.error=this.error==='LOCAL_RECEIPT_UNPERSISTED'?this.error:this.safeCode(error);this.h.emit();return {status:'UNKNOWN',request_id:id};}
 async submit(action,targetToken){check(this.writable()&&!this.busy&&this.current());this.flushAll();const h=this.h,s=this.scope(),epoch=h.epoch,reviewed=this.view,d=this.slot()?.draft,o=owner(s,reviewed.scope.workspace_ref,reviewed.scope.root_operation_id);
  check(this.pending(o).length===0,'WORK_REQUIREMENT_PENDING');const target=action==='REMOVE'?reviewed.links.find(r=>hash(r)===targetToken):null;
  check(action==='ASSOCIATE'?d&&wire.canSave(reviewed,d)&&!wire.unchanged(reviewed,d):action==='REMOVE'&&target);this.busy=true;this.error=null;h.emit();
  try{const fresh=await this.readValue(s,{root_operation_id:o.root_operation_id,workspace_ref:o.workspace_ref,selected_requirement_ref:action==='ASSOCIATE'?d.choice.requirement_ref:null,...(action==='ASSOCIATE'&&d.choice.source_authority?{selected_source_authority:copy(d.choice.source_authority)}:{})});check(epoch===h.epoch&&same(this.scope(),s)&&fresh.root_sha256===reviewed.root_sha256&&fresh.revision===reviewed.revision&&same(fresh.scope,reviewed.scope));check(action==='ASSOCIATE'?wire.canSave(fresh,d):fresh.links.some(r=>same(r,target)));
   const request={...copy(fresh.scope),operation_id:h.newId(),root_sha256:fresh.root_sha256,expected_revision:fresh.revision,action,requirement_ref:copy(action==='ASSOCIATE'?d.choice.requirement_ref:target.requirement_ref),requirement_ids:action==='ASSOCIATE'?[...d.choice.requirement_ids]:[],acceptance_ids:action==='ASSOCIATE'?[...d.choice.acceptance_ids]:[],...(action==='ASSOCIATE'&&d.choice.source_authority?{source_authority:copy(d.choice.source_authority)}:{})};
   const result=await this.bounded(()=>h.mutation(METHODS[1],s,{request,request_sha256:hash(request)},this.metadata(s,request)));if(epoch===h.epoch&&same(this.scope(),s)&&h.state.value.requests.some(e=>e.work_requirement_checksum===hash(request)&&e.work_requirement_receipt))try{await this.read({selected:this.slot()?.draft.choice.requirement_ref??null});}catch{/* Verified result is separate from display refresh. */}return result;
  }catch(error){if(epoch===h.epoch&&same(this.scope(),s))this.error=this.safeCode(error);throw error;}finally{this.busy=false;h.emit();}
 }
 entry(id){const e=this.h.state.value.requests.find(r=>r.request_id===id);check(e&&validEntry(e)&&this.owns(e.work_requirement_owner));return e;}
 async inspect(id){check(this.supported());const e=this.entry(id),known=this.observed.get(id);if(known)return this.onEnvelope(e,{status:'OK',result:known});const s=this.scope();
  try{const value=await this.bounded(()=>this.h.read(METHODS[2],s,{request:copy(e.work_requirement_request),request_sha256:e.work_requirement_checksum}));return this.onEnvelope(e,{status:'OK',result:value});}
  catch(error){if(error.code!=='WORK_REQUIREMENT_NOT_FOUND'){this.error=this.safeCode(error);this.h.emit();throw error;}
   this.h.state.commit(v=>{const row=v.requests.find(r=>r.request_id===id);row.work_requirement_last_observation='NOT_FOUND';if(row.status!=='ERROR'){row.status='UNKNOWN';row.work_requirement_initial_unknown=true;}});this.error='WORK_REQUIREMENT_NOT_FOUND';this.h.emit();return {status:'NOT_FOUND'};
  }
 }
 async retry(id){check(this.writable()&&!this.busy);this.flushAll();this.busy=true;this.h.emit();try{const before=this.entry(id),observed=await this.inspect(id);if(observed.status==='RECORDED')return observed;const e=this.entry(id),s=this.scope(),epoch=this.h.epoch,r=e.work_requirement_request;check(observed.status==='NOT_FOUND'&&e.status!=='ERROR'&&!e.work_requirement_disposition,'WORK_REQUIREMENT_PENDING');
   const current=await this.readValue(s,{root_operation_id:r.root_operation_id,workspace_ref:r.workspace_ref});check(epoch===this.h.epoch&&same(this.scope(),s)&&same(current.scope,{session_ref:r.session_ref,generation:r.generation,workspace_ref:r.workspace_ref,root_operation_id:r.root_operation_id})&&current.root_sha256===r.root_sha256&&current.revision===r.expected_revision,'WORK_REQUIREMENT_REVIEW_REQUIRED');
   return await this.bounded(()=>this.h.mutation(METHODS[1],s,{request:copy(r),request_sha256:e.work_requirement_checksum},this.metadata(s,r,{work_requirement_initial_unknown:before.work_requirement_initial_unknown||e.work_requirement_initial_unknown,continuation_of:id})));
  }finally{this.busy=false;this.h.emit();}
 }
 async retain(id){check(!this.busy);const e=this.entry(id),r=e.work_requirement_request;check(!e.work_requirement_receipt&&!this.observed.has(id));const s=this.scope(),epoch=this.h.epoch;this.busy=true;this.h.emit();try{const fresh=await this.readValue(s,{root_operation_id:r.root_operation_id,workspace_ref:r.workspace_ref});check(epoch===this.h.epoch&&same(this.scope(),s)&&fresh.root_sha256===r.root_sha256);check(e.status==='ERROR'||e.work_requirement_last_observation==='NOT_FOUND'&&(fresh.scope.generation>r.generation||fresh.revision>r.expected_revision),'WORK_REQUIREMENT_PENDING');
   this.h.state.commit(v=>{for(const row of v.requests.filter(row=>row.work_requirement_checksum===e.work_requirement_checksum))row.work_requirement_disposition={root_sha256:fresh.root_sha256,generation:fresh.scope.generation,revision:fresh.revision};});this.h.emit();return fresh;
  }finally{this.busy=false;this.h.emit();}
 }
}
module.exports={WorkRequirements,METHODS,validDrafts,validEntry,keyOf,owner};

function validMessage(m){
 const fields={CONTINUE:['draft_token','revision'],OPEN:['token'],READ:['selected','offset','detail'],CHOOSE:['token'],TOGGLE:['token','kind','id','checked'],REVIEW:[],SAVE:['token'],REMOVE:['token'],INSPECT:['id'],RETRY:['id'],RETAIN:['id'],LOAD:['id'],RESTORE:['id'],DELETE:['id'],FLUSH:[]};
 check(m&&m.type==='workRequirements'&&Object.hasOwn(fields,m.action)&&Object.keys(m).every(k=>['type','action','context',...fields[m.action]].includes(k)),'INVALID_WEBVIEW_MESSAGE');
 if(['CONTINUE','READ','CHOOSE','TOGGLE','REVIEW','SAVE','REMOVE','RESTORE','DELETE'].includes(m.action))check(SHA.test(m.context??''),'INVALID_WEBVIEW_MESSAGE');
 if(['OPEN','CHOOSE','TOGGLE','SAVE','REMOVE'].includes(m.action))check(SHA.test(m.token??''),'INVALID_WEBVIEW_MESSAGE');
 if(['INSPECT','RETRY','RETAIN','LOAD','RESTORE','DELETE'].includes(m.action))check(ID.test(m.id??''),'INVALID_WEBVIEW_MESSAGE');
 if(m.action==='TOGGLE')check(['requirement_ids','acceptance_ids'].includes(m.kind)&&/^[A-Za-z0-9_-]{1,40}$/.test(m.id??'')&&typeof m.checked==='boolean','INVALID_WEBVIEW_MESSAGE');
 if(m.action==='READ'){check(m.selected===null||wire.validChoice({requirement_ref:m.selected,requirement_ids:[],acceptance_ids:[]}),'INVALID_WEBVIEW_MESSAGE');check(Number.isSafeInteger(m.offset)&&m.offset>=0&&m.offset<=1000000,'INVALID_WEBVIEW_MESSAGE');if(m.detail!==undefined)check(m.detail&&Object.keys(m.detail).length===2&&['requirement','acceptance'].includes(m.detail.kind)&&/^[A-Za-z0-9_-]{1,40}$/.test(m.detail.id??''),'INVALID_WEBVIEW_MESSAGE');}
 if(m.action==='CONTINUE')check(SHA.test(m.draft_token??'')&&Number.isSafeInteger(m.revision)&&m.revision>=0,'INVALID_WEBVIEW_MESSAGE');
 return m;
}
WorkRequirements.prototype.handle=function(m){validMessage(m);if(['CONTINUE','READ','CHOOSE','TOGGLE','REVIEW','SAVE','REMOVE','RESTORE','DELETE'].includes(m.action))check(m.context===this.contextToken(),'WORK_REQUIREMENT_CHANGED');if(m.action==='SAVE')check(this.slot()&&m.token===hash(this.slot().draft),'WORK_REQUIREMENT_CHANGED');switch(m.action){
 case 'CONTINUE':return this.h.chooseOriginalWorkInput(m.draft_token,m.revision,m.context);
 case 'OPEN':return this.open(m.token);case 'READ':return this.read(m);case 'CHOOSE':return this.choose(m.token);
 case 'TOGGLE':return this.toggle(m.token,m.kind,m.id,m.checked);case 'REVIEW':return this.review();
 case 'SAVE':return this.submit('ASSOCIATE');case 'REMOVE':return this.submit('REMOVE',m.token);
 case 'INSPECT':return this.inspect(m.id);case 'RETRY':return this.retry(m.id);case 'RETAIN':return this.retain(m.id);
 case 'LOAD':{const e=this.entry(m.id);this.tool=null;return this.read({root:e.work_requirement_owner.root_operation_id,selected:e.work_requirement_request.requirement_ref,sourceAuthority:e.work_requirement_request.source_authority});}
 case 'RESTORE':return this.copyAction(m.id,false);case 'DELETE':return this.copyAction(m.id,true);case 'FLUSH':return this.flushAll();
 }};
module.exports.validMessage=validMessage;
