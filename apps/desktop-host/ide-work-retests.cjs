'use strict';
// Existing original SEND authority only. No SDK, runner, report-path execution or retry.
const wire=require('../../plugins/vscode-companion/work-retest-wire.cjs');
const METHODS=Object.freeze(['session.work.retests.prepare','session.work.retests.submit','session.work.retests.operation','session.work.retests.continue']);
function fail(code='WORK_RETEST_CHANGED'){throw Object.assign(Error('复测尚未核对完成，原选择和编号保留。'),{code,public:true});}
function keys(v,allowed){if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).some(k=>!allowed.includes(k)))fail('INVALID_PARAMS');}
async function dispatch(method,{scope:s,payload:p,get,request,path,signal,currentGrants}){
 if(!METHODS.includes(method))fail('INVALID_PARAMS');const positive=[METHODS[1],METHODS[3]].includes(method);
 const authorized=async()=>{if(signal?.aborted)fail('REQUEST_CANCELLED');if(currentGrants){const g=await currentGrants();if(!g.methods.includes(method)||g.project_ids!==null&&!g.project_ids.includes(s.project_id))fail('EXECUTION_SCOPE_DENIED');}};
 await authorized();keys(p,method===METHODS[0]?['request']:method===METHODS[2]?['request','request_sha256','include_command_evidence']:['request','request_sha256']);
 const r=p.request;if(method===METHODS[0]?!wire.retestPrepareBody(r):!wire.retestRequest(r)||wire.hash(r)!==p.request_sha256)fail('INVALID_PARAMS');
 if(method===METHODS[2]&&p.include_command_evidence!==undefined&&typeof p.include_command_evidence!=='boolean')fail('INVALID_PARAMS');
 const raw=await get(path+'/snapshot');if(raw?.project_id!==s.project_id||!wire.same(raw.session_ref,s.session_ref)||raw.generation!==s.generation||!wire.same(raw.workspace_ref,r.workspace_ref)||!wire.same(s.session_ref,r.session_ref)||r.generation>s.generation||(positive||method===METHODS[0])&&r.generation!==s.generation)fail();
 if(method===METHODS[0]){await authorized();const v=await request(path+'/work-retests/prepare',r,signal);let checked;try{checked=await wire.validateRetestPreparation(v,s.project_id,r);}catch{fail();}await authorized();return checked;}
 const query={...wire.workRequirementScope(raw,r.root_operation_id),operation_id:r.operation_id,request_sha256:p.request_sha256,include_command_evidence:method===METHODS[2]?(p.include_command_evidence??false):false};
 let body,route;if(method===METHODS[1]){body=r;route='/commands';}else if(method===METHODS[3]){const {include_command_evidence,...rest}=query;body={...rest,preparation_sha256:r.preparation_sha256};route='/continue';}else{body=query;route='/operations/inspect';}
 await authorized();let v;try{v=await request(path+'/work-retests'+route,body,signal);}catch(error){if(positive)fail('WORK_RETEST_UNKNOWN');if(error?.status===404)fail('WORK_RETEST_NOT_FOUND');throw error;}
 let checked;try{checked=await wire.validateRetestInspection(v,raw,query,r);}catch{fail(positive?'WORK_RETEST_UNKNOWN':'WORK_RETEST_CHANGED');}await authorized();return checked;
}
module.exports={METHODS,dispatch};
