'use strict';
// Existing paired authority and original observer only. No runner or file open.
const tools=require('./ide-tool-details.cjs');
const wire=require('../../plugins/vscode-companion/test-report-wire.cjs');
const {check,same,hash}=wire;
function exact(value,keys){check(value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).every(k=>keys.includes(k)));}
async function candidates(raw,scope,toolRef,get,path){
 tools.projectTool(raw,scope,toolRef);
 const part=raw.messages.find(m=>m.info.id===toolRef.message_id).parts.find(p=>p.id===toolRef.part_id);
 check(part.tool==='bash'&&['completed','error'].includes(part.state?.status));
 const matching=raw.messages.filter(m=>m.info?.role==='assistant'&&m.info.sessionID===raw.original_session_id).flatMap(m=>(m.parts||[]).filter(p=>p.type==='tool'&&p.tool==='bash'&&p.callID===toolRef.call_id));check(matching.length===1);
 const w=raw.workspace_ref;check(w&&wire.sha(w.id)&&wire.sha(w.binding_sha256)&&Number.isSafeInteger(w.revision)&&w.revision>=1);
 const observed={...scope,workspace_ref:w},q=new URLSearchParams({session_sha256:scope.session_ref.sha256,generation:String(scope.generation),workspace_id:w.id,binding_revision:String(w.revision),binding_sha256:w.binding_sha256,call_id:toolRef.call_id});
 const catalog=await get(path+'/command-observations?'+q);
 check(catalog?.schema==='eeas.original-command-observations.v1'&&same(catalog.scope,observed)&&wire.ref(catalog.snapshot_ref)&&catalog.read_only===true&&catalog.model_calls===0&&catalog.automatic_replay===false&&Array.isArray(catalog.commands)&&catalog.commands.length<=16&&catalog.coverage?.basis_scope==='DECLARED_FILES_ONLY'&&catalog.coverage.all_dependencies_captured===false);
 const {snapshot_ref:snapshot,...catalogBasis}=catalog;check(snapshot.id===hash(observed)&&snapshot.sha256===hash(catalogBasis));
 const rows=catalog.commands.filter(c=>c.call_id===toolRef.call_id&&c.message_id===toolRef.message_id&&c.part_id===toolRef.part_id&&c.native_session_id===raw.original_session_id&&c.parent_native_message_id===raw.messages.find(m=>m.info.id===toolRef.message_id).info.parentID&&c.native_message_id===c.parent_native_message_id);
 check(catalog.commands.length===rows.length&&rows.length<=1);
 const command=rows[0];let projected=null;const sources=[];
 if(command){check(command.qualification==='ORIGINAL_COMMAND_OBSERVATION_ONLY'&&same(command.source_scope,{...observed,generation:command.source_generation})&&Array.isArray(command.sources)&&command.sources.length<=32);
  projected=Object.fromEntries(['record_ref','source_generation','call_id','operation_id','root_operation_id','status','exit_code'].map(k=>[k,command[k]]));
  for(const source of command.sources){if(!/\.xml$/i.test(source.path)||source.after?.state!=='UTF8')continue;const before=source.before,after=source.after;
   check(wire.path(source.path)&&wire.sha(after.sha256)&&after.blob==='blobs/'+after.sha256+'.utf8');
   if(before!==null){check(before&&['ABSENT','UTF8'].includes(before.state));if(before.state==='UTF8')check(wire.sha(before.sha256));}
   sources.push({path:source.path,sha256:after.sha256,bytes:after.bytes,association:before===null?'BEFORE_UNAVAILABLE':before.state==='ABSENT'?'NEW_SNAPSHOT':before.sha256===after.sha256?'UNCHANGED_BYTES':'CHANGED_BYTES'});
  }
 }
 const basis={kind:'ORIGINAL_COMMAND_TEST_REPORT_SOURCES',scope,tool_ref:toolRef,observation_scope:observed,command:projected,sources,coverage:{basis_scope:'DECLARED_FILES_ONLY',all_dependencies_captured:false,reason_codes:catalog.coverage.reason_codes},read_only:true,model_calls:0,commands_started:0,source_files_written:0,automatic_replay:false,whole_task_acceptance:'NOT_ASSESSED'};
 return wire.validateSources({...basis,catalog_sha256:hash(basis)},scope,toolRef);
}
async function dispatch(method,{scope,payload,read,get,request,path,signal,currentGrants}){
 const report=method==='session.tool.report';exact(payload,report?['tool_ref','source_ref','offset','outcome','report_ref']:['tool_ref']);
 if(report){exact(payload.source_ref,['catalog_sha256','path','sha256']);check(wire.sha(payload.source_ref.catalog_sha256)&&wire.path(payload.source_ref.path)&&wire.sha(payload.source_ref.sha256)&&wire.count(payload.offset)&&wire.FILTERS.includes(payload.outcome));if(payload.report_ref!==undefined)check(wire.ref(payload.report_ref));}
 const catalog=await candidates(await read(),scope,payload.tool_ref,get,path);
 const authorized=async()=>{if(currentGrants){const live=await currentGrants();check(live.methods.includes(method)&&(live.project_ids===null||live.project_ids.includes(scope.project_id)));}if(signal?.aborted)throw Object.assign(new Error('Request cancelled'),{code:'REQUEST_CANCELLED'});};
 // Inspect POST is strictly readonly; recheck the live pairing and project grant.
 await authorized();
 if(!report)return catalog;
 check(payload.source_ref.catalog_sha256===catalog.catalog_sha256);
 const source=catalog.sources.find(row=>row.path===payload.source_ref.path&&row.sha256===payload.source_ref.sha256);check(source);
 const options={offset:payload.offset,outcome:payload.outcome,expectedRef:payload.report_ref},body=wire.requestFor(catalog,source,options);
 const value=wire.validateReport(await request(path+'/command-test-reports/inspect',body,signal),catalog,source,options);
 await authorized();
 return {scope,tool_ref:payload.tool_ref,catalog_sha256:catalog.catalog_sha256,report:value};
}
module.exports={dispatch,candidates};
