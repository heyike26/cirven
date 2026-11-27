'use strict';
const wire=require('../../plugins/vscode-companion/work-requirement-wire.cjs');
const reports=require('./ide-command-test-reports.cjs');
const METHODS=Object.freeze(['session.work.requirements','session.work.requirements.link','session.work.requirements.operation']);
function fail(code='WORK_REQUIREMENT_CHANGED'){throw Object.assign(Error('原要求关联尚未核对一致；原选择和请求保留。'),{code,public:true});}
const sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),id=v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v);
function keys(v,names){if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).some(k=>!names.includes(k)))fail('INVALID_PARAMS');}
async function dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants}){
 const authorized=async()=>{if(signal?.aborted)fail('REQUEST_CANCELLED');if(currentGrants){const g=await currentGrants();if(!g.methods.includes(method)||g.project_ids!==null&&!g.project_ids.includes(s.project_id))fail('EXECUTION_SCOPE_DENIED');}};
 await authorized();
 const raw=await get(path+'/snapshot');
 if(raw?.project_id!==s.project_id||!wire.same(raw.session_ref,s.session_ref)||raw.generation!==s.generation)fail();
 const w=raw.workspace_ref;if(!w||!sha(w.id)||!sha(w.binding_sha256)||!Number.isSafeInteger(w.revision)||w.revision<1)fail();
 const owner=root=>({project_id:s.project_id,session_ref:s.session_ref,workspace_ref:w,root_operation_id:root});
 if(method===METHODS[0]){
  keys(p,['tool_ref','root_operation_id','workspace_ref','include_catalog','catalog_offset','selected_requirement_ref','detail_kind','detail_id','selected_source_authority']);
  let root=p.root_operation_id;
  if(p.tool_ref!==undefined){if(root!==undefined||p.workspace_ref!==undefined)fail('INVALID_PARAMS');const c=await reports.candidates(raw,s,p.tool_ref,get,path);root=c.command?.root_operation_id;}
  else if(!wire.same(p.workspace_ref,w))fail();
  if(!id(root)||p.include_catalog!==undefined&&typeof p.include_catalog!=='boolean'||p.catalog_offset!==undefined&&(!Number.isSafeInteger(p.catalog_offset)||p.catalog_offset<0||p.catalog_offset>1000000))fail('INVALID_PARAMS');
  const body={session_ref:s.session_ref,generation:s.generation,workspace_ref:w,root_operation_id:root,include_catalog:p.include_catalog??false,catalog_offset:p.catalog_offset??0,selected_requirement_ref:p.selected_requirement_ref??null,detail_kind:p.detail_kind??null,detail_id:p.detail_id??null,...(p.selected_source_authority!=null?{selected_source_authority:p.selected_source_authority}:{})};
  if((body.detail_kind===null)!==(body.detail_id===null)||body.detail_kind!==null&&!['requirement','acceptance'].includes(body.detail_kind))fail('INVALID_PARAMS');
  await authorized();const value=await request(path+'/work-requirements/inspect',body,signal);
  const checked=await wire.validateView(value,raw,root,body);await authorized();return checked;
 }
 keys(p,['request','request_sha256']);const frozen=p.request;
 if(!wire.validRequest(frozen,owner(frozen?.root_operation_id))||!id(frozen.root_operation_id)||!sha(p.request_sha256)||wire.hash(frozen)!==p.request_sha256)fail('INVALID_PARAMS');
 if(method===METHODS[1]){
  if(frozen.generation!==s.generation)fail();await authorized();let value;
  try{value=await request(path+'/work-requirements/links',frozen,signal);}
  catch(error){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&![408,429].includes(error.status))fail('WORK_REQUIREMENT_REJECTED');fail('WORK_REQUIREMENT_UNKNOWN');}
  let checked;try{checked=wire.verifyReceipt(value,frozen,p.request_sha256,owner(frozen.root_operation_id));}catch{fail();}
  await authorized();return checked;
 }
 if(method!==METHODS[2]||frozen.generation>s.generation)fail('INVALID_PARAMS');
 const body={session_ref:s.session_ref,generation:s.generation,workspace_ref:w,root_operation_id:frozen.root_operation_id,operation_id:frozen.operation_id,request_sha256:p.request_sha256};
 await authorized();let value;
 try{value=await request(path+'/work-requirements/operations/inspect',body,signal);}catch(error){if(error?.status===404)fail('WORK_REQUIREMENT_NOT_FOUND');throw error;}
 let checked;try{checked=wire.verifyReceipt(value,frozen,p.request_sha256,owner(frozen.root_operation_id));}catch{fail();}
 await authorized();return checked;
}
module.exports={METHODS,dispatch};
