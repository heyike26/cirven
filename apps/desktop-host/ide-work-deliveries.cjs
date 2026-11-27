'use strict';
// Paired Main authority: exact snapshot, current grants, one API call, no runner or replay.
const wire=require('../../plugins/vscode-companion/work-delivery-wire.cjs');
const METHODS=Object.freeze(['session.work.roots','session.work.deliveries.list','session.work.deliveries.select','session.work.deliveries.prepare','session.work.deliveries.submit','session.work.deliveries.operation','session.work.deliveries.recovery','session.work.deliveries.continue','session.work.deliveries.retain','session.work.deliveries.tree']);
const READ_ONLY=Object.freeze(METHODS.filter(m=>!['session.work.deliveries.submit','session.work.deliveries.continue','session.work.deliveries.retain'].includes(m)));
function fail(code='WORK_DELIVERY_CHANGED'){throw Object.assign(Error('交付记录尚未核对完成，原文件选择、说明和操作编号保留。'),{code,public:true});}
function keys(v,required,optional=[]){if(!v||typeof v!=='object'||Array.isArray(v)||required.some(k=>!Object.hasOwn(v,k))||Object.keys(v).some(k=>![...required,...optional].includes(k)))fail('INVALID_PARAMS');}
async function dispatch(method,{scope:s,payload:input,get,request,path,signal,currentGrants}){
 if(!METHODS.includes(method))fail('INVALID_PARAMS');const p=structuredClone(input),positive=!READ_ONLY.includes(method);
 const authorized=async()=>{if(signal?.aborted)fail('REQUEST_CANCELLED');if(currentGrants){const g=await currentGrants();if(!g.methods.includes(method)||g.project_ids!==null&&!g.project_ids.includes(s.project_id))fail('EXECUTION_SCOPE_DENIED');}};
 await authorized();let root,workspace;
 if(method===METHODS[0]){keys(p,['workspace_ref'],['offset']);if(!wire.count(p.offset??0,1024))fail('INVALID_PARAMS');workspace=p.workspace_ref;}
 else if([METHODS[1],METHODS[2],METHODS[9]].includes(method)){keys(p,method===METHODS[2]?['root_operation_id','workspace_ref','path']:['root_operation_id','workspace_ref'],method===METHODS[1]?['offset']:method===METHODS[9]?['path','offset']:[]);root=p.root_operation_id;workspace=p.workspace_ref;if(!wire.id(root)||method===METHODS[1]&&!wire.count(p.offset??0,32)||method===METHODS[9]&&(!wire.count(p.offset??0,10000)||typeof(p.path??'')!=='string'))fail('INVALID_PARAMS');if(method===METHODS[2]||method===METHODS[9]&&p.path)wire.deliveryArchivePath(p.path);}
 else if([METHODS[3],METHODS[4]].includes(method)){keys(p,method===METHODS[4]?['request','manifest_sha256','request_sha256']:['request']);try{(method===METHODS[3]?wire.validateDeliveryPrepare:wire.validateDeliveryRequest)(p.request,s.project_id);}catch{fail('INVALID_PARAMS');}root=p.request.root_operation_id;workspace=p.request.workspace_ref;if(method===METHODS[4]){const {operation_id,review_sha256,review_confirmed,known_gaps_acknowledged,...query}=p.request;if(!wire.sha(p.manifest_sha256)||wire.hash(p.request)!==p.request_sha256||review_sha256!==wire.hash({schema:'cirven.original-work-delivery-review.v1',query,manifest_sha256:p.manifest_sha256,root_sha256:query.root_sha256,expected_revision:query.expected_revision}))fail('INVALID_PARAMS');}}
 else{keys(p,['identity'],method===METHODS[8]?['request','statement']:['request']);const i=p.identity;keys(i,['scope','root_sha256','operation_id','request_sha256'],['manifest_sha256']);if(!wire.validScope(i.scope)||!wire.sha(i.root_sha256)||!wire.id(i.operation_id)||!wire.sha(i.request_sha256)||i.manifest_sha256!==undefined&&!wire.sha(i.manifest_sha256))fail('INVALID_PARAMS');root=i.scope.root_operation_id;workspace=i.scope.workspace_ref;
  if(p.request!==undefined){try{wire.validateDeliveryRequest(p.request,s.project_id);}catch{fail('INVALID_PARAMS');}const r=p.request;if(wire.hash(r)!==i.request_sha256||r.operation_id!==i.operation_id||r.root_sha256!==i.root_sha256||r.root_operation_id!==root||!wire.same(r.session_ref,s.session_ref)||!wire.same(r.workspace_ref,workspace)||r.generation>s.generation)fail('INVALID_PARAMS');}
  if(method===METHODS[8]&&(!wire.text(p.statement,4000)||!p.statement.length))fail('INVALID_PARAMS');
 }
 // Even root listing requires the exact saved workspace; no SDK-backed session GET.
 const raw=await get(path+'/snapshot');if(raw?.project_id!==s.project_id||!wire.same(raw.session_ref,s.session_ref)||raw.generation!==s.generation||!wire.same(raw.workspace_ref,workspace))fail();
 if(root&&!wire.validScope({...wire.workRequirementScope(raw,root)}))fail();
 if([METHODS[3],METHODS[4]].includes(method)&&!wire.same(wire.workRequirementScope(raw,root),{session_ref:p.request.session_ref,generation:p.request.generation,workspace_ref:workspace,root_operation_id:root}))fail();
 if(p.identity&&!wire.same(p.identity.scope,wire.workRequirementScope(raw,root)))fail();
 let sent=false;
 const transport={request:async(route,verb,body)=>{await authorized();sent=true;try{return await request(route,verb==='GET'?undefined:body,signal);}catch(e){if(positive)fail('WORK_DELIVERY_UNKNOWN');if(e?.status===404)fail('WORK_DELIVERY_NOT_FOUND');throw e;}},bytes(){fail('BINARY_EXPORT_REQUIRES_NATIVE_SAVE');}};
 const api=new wire.OriginalWorkDeliveriesApi(transport);let value;
 try{
  if(method===METHODS[0])value=await new wire.OriginalWorkRootsApi(transport).inspect(raw,p.offset??0,signal);
  else if(method===METHODS[1])value=await api.list(raw,root,p.offset??0,signal);
  else if(method===METHODS[2]){const selected=await api.selectSaved(raw,root,p.path,signal);value={...selected,current:true,live_sha256:selected.source_ref.sha256};}
  else if(method===METHODS[9])value=await api.tree(raw,root,p.path??'',p.offset??0,signal);
  else if(method===METHODS[3])value=await api.prepare(raw,p.request,signal);
  else if(method===METHODS[4])value=await api.submit(raw,p.request,p.manifest_sha256,signal);
  else if(method===METHODS[5])value=await api.inspect(raw,p.identity,signal,p.request);
  else if(method===METHODS[6])value=await api.recovery(raw,p.identity,signal);
  else if(method===METHODS[7])value=await api.continue(raw,p.identity,signal,p.request);
  else value=await api.retain(raw,p.identity,p.statement,signal,p.request);
 }catch(e){if(positive&&sent)fail('WORK_DELIVERY_UNKNOWN');if(e?.public)throw e;fail();}
 await authorized();return value;
}
module.exports={METHODS,READ_ONLY,dispatch};
