'use strict';
// Main-owned saved evidence and metadata ledger. No runner, workspace-body read or model call.
const wire=require('../../plugins/vscode-companion/work-case-wire.cjs');
const reports=require('./ide-command-test-reports.cjs');
const METHODS=Object.freeze(['session.work.cases','session.work.cases.map','session.work.cases.operation']);
function fail(code='WORK_CASE_CHANGED'){throw Object.assign(Error('用例对应尚未核对一致；原选择和请求保留。'),{code,public:true});}
const id=v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v),sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
function keys(v,names){if(!v||typeof v!=='object'||Array.isArray(v)||![Object.prototype,null].includes(Object.getPrototypeOf(v))||Object.keys(v).some(k=>!names.includes(k)))fail('INVALID_PARAMS');}
async function dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants}){
 if(!METHODS.includes(method))fail('INVALID_PARAMS');
 const authorized=async()=>{if(signal?.aborted)fail('REQUEST_CANCELLED');if(currentGrants){const g=await currentGrants();if(!g.methods.includes(method)||g.project_ids!==null&&!g.project_ids.includes(s.project_id))fail('EXECUTION_SCOPE_DENIED');}};
 await authorized();const raw=await get(path+'/snapshot');
 if(raw?.project_id!==s.project_id||!wire.same(raw.session_ref,s.session_ref)||raw.generation!==s.generation)fail();
 const w=raw.workspace_ref;if(!w||!sha(w.id)||!sha(w.binding_sha256)||!Number.isSafeInteger(w.revision)||w.revision<1)fail();
 const owner=root=>({project_id:s.project_id,session_ref:s.session_ref,workspace_ref:w,root_operation_id:root});
 if(method===METHODS[0]){
  keys(p,['tool_ref','root_operation_id','workspace_ref','offset','case_source','selected_requirement_link_operation_id','mapping_key']);
  let root=p.root_operation_id;
  if(p.tool_ref!==undefined){
   if(root!==undefined||p.workspace_ref!==undefined)fail('INVALID_PARAMS');
   const catalog=await reports.candidates(raw,s,p.tool_ref,get,path);root=catalog.command?.root_operation_id;
   if(p.case_source!=null){const c=p.case_source,command=catalog.command;if(!wire.validSource(c,s.generation)||!command||!wire.same(c.command_ref,command.record_ref)||c.command_generation!==command.source_generation||c.call_id!==command.call_id||!catalog.sources.some(r=>r.path===c.report_path&&r.sha256===c.report_sha256))fail();}
  }else if(!wire.same(p.workspace_ref,w))fail();
  if(!id(root)||p.offset!==undefined&&(!Number.isSafeInteger(p.offset)||p.offset<0||p.offset>128)||p.case_source!=null&&!wire.validSource(p.case_source,s.generation)||p.selected_requirement_link_operation_id!=null&&!id(p.selected_requirement_link_operation_id)||p.mapping_key!=null&&!sha(p.mapping_key))fail('INVALID_PARAMS');
  const body={session_ref:s.session_ref,generation:s.generation,workspace_ref:w,root_operation_id:root,offset:p.offset??0,case_source:p.case_source??null,selected_requirement_link_operation_id:p.selected_requirement_link_operation_id??null,mapping_key:p.mapping_key??null};
  await authorized();const result=await request(path+'/work-test-cases/inspect',body,signal);
  let checked;try{checked=await wire.validateView(result,raw,root,body);}catch{fail();}
  await authorized();return checked;
 }
 let frozen,o,originalGeneration;
 if(p.request!==undefined){
  keys(p,['request','request_sha256']);frozen=p.request;o=owner(frozen?.root_operation_id);
  if(!wire.validRequest(frozen,o)||!id(frozen.root_operation_id)||!sha(p.request_sha256)||wire.hash(frozen)!==p.request_sha256)fail('INVALID_PARAMS');
 }else{
  if(method!==METHODS[2])fail('INVALID_PARAMS');
  keys(p,['root_operation_id','workspace_ref','operation_id','request_sha256','root_sha256','original_generation']);
  if(!id(p.root_operation_id)||!wire.same(p.workspace_ref,w)||!id(p.operation_id)||!sha(p.request_sha256)||!sha(p.root_sha256)||!Number.isSafeInteger(p.original_generation)||p.original_generation<1||p.original_generation>s.generation)fail('INVALID_PARAMS');
  o=owner(p.root_operation_id);originalGeneration=p.original_generation;
 }

 if(method===METHODS[1]){
  if(frozen.generation!==s.generation)fail();await authorized();let result;
  try{result=await request(path+'/work-test-cases/mappings',frozen,signal);}
  catch(error){if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&![408,429].includes(error.status))fail('WORK_CASE_REJECTED');fail('WORK_CASE_UNKNOWN');}
  let checked;try{checked=await wire.verifyReceipt(result,frozen,p.request_sha256,o);}catch{fail('WORK_CASE_UNKNOWN');}
  await authorized();return checked;
 }
 if(frozen&&frozen.generation>s.generation)fail('INVALID_PARAMS');
 const body={session_ref:s.session_ref,generation:s.generation,workspace_ref:w,root_operation_id:frozen?.root_operation_id??p.root_operation_id,operation_id:frozen?.operation_id??p.operation_id,request_sha256:p.request_sha256};
 await authorized();let result;try{result=await request(path+'/work-test-cases/operations/inspect',body,signal);}catch(error){if(error?.status===404)fail('WORK_CASE_NOT_FOUND');throw error;}
 if(!frozen){if(result?.root_sha256!==p.root_sha256||result?.operation_id!==p.operation_id)fail();frozen={session_ref:s.session_ref,generation:originalGeneration,workspace_ref:w,root_operation_id:p.root_operation_id,operation_id:result.operation_id,root_sha256:result.root_sha256,expected_revision:result.revision-1,expected_requirement_revision:result.requirement_revision,action:result.action,mapping_key:result.mapping_key,selection:result.selection};}
 let checked;try{checked=await wire.verifyReceipt(result,frozen,p.request_sha256,o);}catch{fail();}
 await authorized();return checked;
}
module.exports={METHODS,dispatch};
