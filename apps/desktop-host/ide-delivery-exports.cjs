'use strict';
// Main selects the path; only the existing trusted host API creates verified ZIP bytes.
const wire=require('../../plugins/vscode-companion/native-export-wire.cjs');
const METHODS=Object.freeze(['session.work.deliveries.export','session.work.deliveries.export.operation','session.work.deliveries.export.cancel','session.work.deliveries.export.reconcile','session.work.deliveries.exports']);
const READ_ONLY=Object.freeze([METHODS[1],METHODS[4]]),SAVE_TIMEOUT_MS=180000;
function fail(code='NATIVE_EXPORT_CHANGED'){throw Object.assign(Error('保存结果尚未核对完成，原操作和已保存文件保留。'),{code,public:true});}
function keys(v,required,optional=[]){if(!v||typeof v!=='object'||Array.isArray(v)||required.some(k=>!Object.hasOwn(v,k))||Object.keys(v).some(k=>![...required,...optional].includes(k)))fail('INVALID_PARAMS');}
function abortable(promise,signal){if(signal.aborted)return Promise.reject(Object.assign(Error('REQUEST_CANCELLED'),{code:'REQUEST_CANCELLED',public:true}));return new Promise((resolve,reject)=>{const abort=()=>reject(Object.assign(Error('REQUEST_CANCELLED'),{code:'REQUEST_CANCELLED',public:true}));signal.addEventListener('abort',abort,{once:true});Promise.resolve(promise).then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));});}
class NativeDeliveryExportsHost{
 constructor({request,chooseSave,isClosing=()=>false}={}){if(typeof request!=='function')throw TypeError('Main request required');this.request=request;this.chooseSave=chooseSave;this.isClosing=isClosing;this.records=new Set();this.dialogGate=null;}
 dispatch(method,{scope,payload},{signal,currentGrants,client_id}={}){const abort=new AbortController(),record={abort,stage:'READ',done:null},relay=()=>abort.abort();signal?.addEventListener('abort',relay,{once:true});if(signal?.aborted)abort.abort();const timer=setTimeout(()=>abort.abort(),method===METHODS[0]?SAVE_TIMEOUT_MS:15000);this.records.add(record);record.done=Promise.resolve().then(()=>this.run(method,scope,structuredClone(payload),{signal:abort.signal,currentGrants,client_id,record})).finally(()=>{clearTimeout(timer);signal?.removeEventListener('abort',relay);this.records.delete(record);});return record.done;}
 abortPending(){for(const r of this.records)if(r.stage!=='SAVE')r.abort.abort();}
 async drainWrites(){const rows=[...this.records].filter(r=>r.stage==='SAVE');await Promise.allSettled(rows.map(r=>r.done));return {status:'SETTLED',requests:rows.length,file_writes_cancelled:false};}
 async run(method,s,p,{signal,currentGrants,client_id,record}){
  if(!METHODS.includes(method))fail('INVALID_PARAMS');keys(s,['project_id','session_ref','generation']);let root,workspace,r,q,rootSha;
  if(method===METHODS[0]){keys(p,['request','request_sha256']);try{r=wire.validateNativeExportRequest(p.request);}catch{fail('INVALID_PARAMS');}if(wire.hash(r)!==p.request_sha256)fail('INVALID_PARAMS');root=r.root_operation_id;workspace=r.workspace_ref;rootSha=r.root_sha256;q={session_ref:r.session_ref,generation:r.generation,workspace_ref:r.workspace_ref,root_operation_id:root,operation_id:r.operation_id,request_sha256:p.request_sha256};if(!wire.id(client_id))fail('EXACT_CLIENT_REQUIRED');}
  else if(method===METHODS[4]){keys(p,['root_operation_id','workspace_ref','root_sha256'],['offset']);root=p.root_operation_id;workspace=p.workspace_ref;rootSha=p.root_sha256;if(!wire.id(root)||!wire.sha(rootSha)||!wire.count(p.offset??0,512))fail('INVALID_PARAMS');}
  else{keys(p,['identity'],method===METHODS[1]?['observe_output']:[]);const i=p.identity;keys(i,['scope','root_sha256','operation_id','request_sha256']);if(!wire.validNativeExportScope(i.scope)||!wire.sha(i.root_sha256)||!wire.id(i.operation_id)||!wire.sha(i.request_sha256)||p.observe_output!==undefined&&typeof p.observe_output!=='boolean')fail('INVALID_PARAMS');root=i.scope.root_operation_id;workspace=i.scope.workspace_ref;rootSha=i.root_sha256;q={...i.scope,operation_id:i.operation_id,request_sha256:i.request_sha256};}
  if(!s||!wire.id(s.project_id)||!wire.same(s.session_ref,q?.session_ref??s.session_ref)||q&&s.generation!==q.generation||!wire.validNativeExportScope({session_ref:s.session_ref,generation:s.generation,workspace_ref:workspace,root_operation_id:root}))fail('EXACT_SCOPE_REQUIRED');
  const authorize=async({allowClosing=false}={})=>{if(signal.aborted)fail('REQUEST_CANCELLED');if(!allowClosing&&this.isClosing())fail('DESKTOP_CLOSING');if(currentGrants){const g=await currentGrants();if(!g.methods.includes(method)||g.project_ids!==null&&!g.project_ids.includes(s.project_id))fail('EXECUTION_SCOPE_DENIED');}if(signal.aborted)fail('REQUEST_CANCELLED');if(!allowClosing&&this.isClosing())fail('DESKTOP_CLOSING');};
  const base='/api/projects/'+encodeURIComponent(s.project_id)+'/agent-sessions/'+encodeURIComponent(s.session_ref.id),host='/api/desktop-host/projects/'+encodeURIComponent(s.project_id)+'/agent-sessions/'+encodeURIComponent(s.session_ref.id)+'/delivery-exports';
  const snapshot=async()=>{await authorize();const v=await this.request(base+'/snapshot',undefined,signal);if(v?.project_id!==s.project_id||!wire.same(v.session_ref,s.session_ref)||v.generation!==s.generation||!wire.same(v.workspace_ref,workspace))fail('NATIVE_EXPORT_SCOPE_CHANGED');return v;};
  await snapshot();let positiveSent=false;
  const post=async(route,body,{allowClosing=false,positive=false}={})=>{await authorize({allowClosing});if(positive)positiveSent=true;try{return await this.request(host+route,body,signal);}catch(e){if(positive)fail('NATIVE_EXPORT_UNKNOWN');if(e?.status===404)fail('NATIVE_EXPORT_NOT_FOUND');throw e;}};
  try{
   if(method===METHODS[4]){const scope={session_ref:s.session_ref,generation:s.generation,workspace_ref:workspace,root_operation_id:root},v=await wire.validateNativeExportHistory(await post('/list',{...scope,offset:p.offset??0}),s.project_id,scope,rootSha,p.offset??0);await authorize();return v;}
   if(method!==METHODS[0]){const route=method===METHODS[1]?'/operations/inspect':method===METHODS[2]?'/cancel':'/reconcile',body={...q,...(method===METHODS[1]?{observe_output:p.observe_output??false}:method===METHODS[2]?{reason:'USER_CANCELLED'}:{})},value=await post(route,body,{positive:method!==METHODS[1]}),checked=await wire.validateNativeExportOutcome(value,s.project_id,q,rootSha,{observe_output:method===METHODS[1]?(p.observe_output??false):false});await authorize({allowClosing:method!==METHODS[1]});return checked;}
   if(typeof this.chooseSave!=='function')fail('NATIVE_SAVE_UNAVAILABLE');if(this.dialogGate)fail('NATIVE_SAVE_BUSY');const gate={flowDone:false,dialogDone:true};this.dialogGate=gate;const release=()=>{if(gate.flowDone&&gate.dialogDone&&this.dialogGate===gate)this.dialogGate=null;};
   try{
    const value=await post('/begin',{request:r,origin_client_id:client_id},{positive:true}),begun=await wire.validateNativeExportOutcome(value,s.project_id,q,rootSha,{request:r,allow_claim:true,observe_output:false});
    if(!begun.new_intent_claimed){await authorize({allowClosing:true});return begun;}
    await authorize();record.stage='DIALOG';gate.dialogDone=false;
    const actualDialog=Promise.resolve().then(()=>{if(signal.aborted||this.isClosing())fail('REQUEST_CANCELLED');return this.chooseSave({filename:begun.delivery.artifact.filename,version_label:begun.delivery.version_label});});
    actualDialog.finally(()=>{gate.dialogDone=true;release();}).catch(()=>{});
    const choice=await abortable(actualDialog,signal);await authorize();
    if(!choice||typeof choice.canceled!=='boolean')fail('NATIVE_SAVE_RESPONSE_INVALID');
    if(choice.canceled){record.stage='READ';const v=await post('/cancel',{...q,reason:'USER_CANCELLED'},{positive:true});const checked=await wire.validateNativeExportOutcome(v,s.project_id,q,rootSha,{request:r,observe_output:false});await authorize({allowClosing:true});return checked;}
    if(!wire.text(choice.filePath,4096)||!choice.filePath.length)fail('NATIVE_SAVE_RESPONSE_INVALID');await snapshot();await authorize();record.stage='SAVE';
    const saved=await post('/save',{...q,destination:choice.filePath},{positive:true}),checked=await wire.validateNativeExportOutcome(saved,s.project_id,q,rootSha,{request:r,allow_writes:true,observe_output:false});await authorize({allowClosing:true});return checked;
   }finally{gate.flowDone=true;release();}
  }catch(e){if(positiveSent)fail('NATIVE_EXPORT_UNKNOWN');if(e?.public)throw e;fail();}
 }
}
module.exports={METHODS,READ_ONLY,SAVE_TIMEOUT_MS,NativeDeliveryExportsHost};
