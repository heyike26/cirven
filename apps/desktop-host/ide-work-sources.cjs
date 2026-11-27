'use strict';
// Root-scoped selected data only. No source workspace or execution grant.
const wire=require('../../plugins/vscode-companion/work-source-wire.cjs');
const METHODS=Object.freeze(['session.work.sources.catalog','session.work.sources.review','session.work.sources.decision','session.work.sources.operation']);
function fail(code='WORK_SOURCE_CHANGED'){throw Object.assign(Error('只读来源尚未核对完成，原选择和请求保留。'),{code,public:true});}
function keys(v,names){if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).some(k=>!names.includes(k)))fail('INVALID_PARAMS');}
async function dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants}){
 if(!METHODS.includes(method))fail('INVALID_PARAMS');const catalog=method===METHODS[0],review=method===METHODS[1],positive=method===METHODS[2];let sourcePid;
 const authorized=async()=>{if(signal?.aborted)fail('REQUEST_CANCELLED');if(currentGrants){const g=await currentGrants();if(!g.methods.includes(method)||g.project_ids!==null&&(!g.project_ids.includes(s.project_id)||sourcePid&&!g.project_ids.includes(sourcePid)))fail('EXECUTION_SCOPE_DENIED');}};
 await authorized();keys(p,catalog?['root_operation_id','workspace_ref','source_project_id','selected_requirement_ref','catalog_offset']:review?['request']:['request','request_sha256']);
 const raw=await get(path+'/snapshot');if(raw?.project_id!==s.project_id||raw.generation!==s.generation||!wire.sourceSame(raw.session_ref,s.session_ref))fail();
 let r,root;
 if(catalog){root=p.root_operation_id;if(!/^[A-Za-z0-9_-]{1,100}$/.test(root??'')||!wire.sourceSame(p.workspace_ref,raw.workspace_ref))fail('INVALID_PARAMS');sourcePid=p.source_project_id;}
 else{r=p.request;if(review?!wire.sourceQuery(r):!wire.sourceDecision(r)||wire.hash(r)!==p.request_sha256)fail('INVALID_PARAMS');root=r.root_operation_id;if(!wire.sourceSame(r.session_ref,s.session_ref)||!wire.sourceSame(r.workspace_ref,raw.workspace_ref)||r.generation>s.generation||(review||positive)&&r.generation!==s.generation)fail();if(review||positive&&r.action==='ALLOW')sourcePid=r.source_project_id;}
 // Discovery/review/new allowance require both projects. Original receipts and
 // withdrawal are target-owned metadata; they remain usable without source-body access.
 if(sourcePid!==undefined&&(!/^[A-Za-z0-9_-]{1,100}$/.test(sourcePid)||sourcePid===s.project_id))fail('INVALID_PARAMS');await authorized();
 const reader=new wire.OriginalWorkRequirementSourcesApi({request:async(route,verb,body,abort,retry)=>{if(verb!=='POST'||retry!==false||abort?.aborted)fail('INVALID_PARAMS');await authorized();return request(route,body,abort);}});
 let value;try{if(catalog)value=await reader.catalog(raw,root,p.source_project_id,p.selected_requirement_ref??null,p.catalog_offset??0,signal);else if(review)value=await reader.review(raw,r,signal);else if(positive)value=await reader.command(raw,r,signal);else value=await reader.operation(raw,r,signal);}
 catch(error){if(positive)fail('WORK_SOURCE_UNKNOWN');if(error?.status===404)fail('WORK_SOURCE_NOT_FOUND');throw error;}
 await authorized();return value;
}
module.exports={METHODS,dispatch};
