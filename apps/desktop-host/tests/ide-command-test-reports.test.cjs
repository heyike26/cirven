'use strict';
// AUTHORED_NOT_RUN. Actual Main dispatcher; API transport boundary is synthetic.
const test=require('node:test'),assert=require('node:assert/strict');
const {createIdeApiAdapter}=require('../ide-api-adapter.cjs');
const tools=require('../ide-tool-details.cjs'),wire=require('../../../plugins/vscode-companion/test-report-wire.cjs');
function fixture(){
 const scope={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:2},workspace={id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)};
 const part={id:'prt_one',callID:'call_one',type:'tool',tool:'bash',messageID:'msg_one',sessionID:'ses_one',state:{status:'completed',input:{command:'pytest --junitxml=report.xml'},output:'original'}},message={info:{id:'msg_one',parentID:'msg_user',role:'assistant',sessionID:'ses_one'},parts:[part]};
 const session={...scope,original_session_id:'ses_one',workspace_ref:workspace,messages:[message]},tool_ref=tools.reference(session,message,part),observed={...scope,workspace_ref:workspace};
 const command={record_ref:{id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)},source_generation:1,source_scope:{...observed,generation:1},call_id:part.callID,message_id:part.messageID,part_id:part.id,native_session_id:part.sessionID,parent_native_message_id:'msg_user',native_message_id:'msg_user',operation_id:'op_one',root_operation_id:'op_root',status:'EXITED',exit_code:0,qualification:'ORIGINAL_COMMAND_OBSERVATION_ONLY',sources:[{path:'report.xml',before:{state:'ABSENT'},after:{state:'UTF8',bytes:45,sha256:'f'.repeat(64),blob:'blobs/'+'f'.repeat(64)+'.utf8'}}]};
 const calls=[];let inspectBody;
 const catalog=()=>{const basis={schema:'eeas.original-command-observations.v1',scope:observed,commands:[command],coverage:{basis_scope:'DECLARED_FILES_ONLY',all_dependencies_captured:false,reason_codes:[]},read_only:true,model_calls:0,automatic_replay:false};return {...basis,snapshot_ref:{id:wire.hash(observed),revision:1,sha256:wire.hash(basis)}};};
 const request=async(route,body)=>{calls.push({route,body});if(route.includes('command-observations?')){assert.equal(new URLSearchParams(route.split('?')[1]).get('call_id'),'call_one');return catalog();}if(route.endsWith('/inspect')){inspectBody=body;return makeReport(body,command,observed);}return session;};
 return {scope,observed,part,session,tool_ref,command,calls,request,catalog,dispatch:createIdeApiAdapter({request}),get inspectBody(){return inspectBody;}};
}
function makeReport(body,command,observed){const basis={schema:'eeas.command-test-report.v1',scope:command.source_scope,command_ref:command.record_ref,command_generation:1,call_id:command.call_id,operation_id:command.operation_id,root_operation_id:command.root_operation_id,report_path:body.report_path,report_sha256:body.report_sha256};return {kind:'ORIGINAL_COMMAND_TEST_REPORT',version:1,request_sha256:wire.hash(body),observation_scope:observed,basis,report_ref:{id:wire.hash(basis),revision:1,sha256:'1'.repeat(64)},report_bytes:45,summary:{outcome:'PASSED',total:1,counts:{PASSED:1,FAILED:0,ERROR:0,SKIPPED:0,UNKNOWN:0},integrity_issues:[],metadata_warnings:[],duration_seconds:null},command_status:command.status,command_exit_code:command.exit_code,association:'NEW_SNAPSHOT',reason_codes:command.status==='UNKNOWN_OUTCOME'?['COMMAND_OUTCOME_UNKNOWN']:command.exit_code!==0?['COMMAND_NONZERO_WITH_REPORT_PASS']:[],cases:[{ordinal:0,suite:'suite',classname:'',name:'case',file:'../report-self-stated.py',line:'4',status:'PASSED',duration_seconds:null,message:'',detail:'',type:'',text_truncated:false}],page:{offset:body.offset,limit:20,outcome:body.outcome,matching:1,next_offset:null},source_scope:'IMMUTABLE_AFTER_COMMAND_SNAPSHOT',writer_attribution:'NOT_PROVEN',all_dependencies_captured:false,current_workspace_verified:false,target_environment_verified:false,whole_task_acceptance:'NOT_ASSESSED',read_only:true,model_calls:0,commands_started:0,source_files_written:0,automatic_replay:false};}
async function sources(f){return f.dispatch('session.tool.reports',{scope:f.scope,payload:{tool_ref:f.tool_ref}});}
async function report(f,c,updates={},context={}){const source=c.sources[0];return f.dispatch('session.tool.report',{scope:f.scope,payload:{tool_ref:f.tool_ref,source_ref:{catalog_sha256:c.catalog_sha256,path:source.path,sha256:source.sha256},offset:0,outcome:'ALL',...updates}},context);}
test('original tool sources and one report page use selected original call and immutable inspect only',async()=>{
 const f=fixture(),c=await sources(f),r=await report(f,c);
 assert.equal(c.command.source_generation,1);assert.equal(c.observation_scope.generation,2);assert.equal(r.report.basis.scope.generation,1);
 assert.equal(r.report.summary.outcome,'PASSED');assert.equal(r.report.whole_task_acceptance,'NOT_ASSESSED');assert.equal(f.inspectBody.generation,2);assert.equal(Object.keys(f.inspectBody).length,11);
 assert.equal(f.calls.filter(c=>c.body!==undefined).length,1);assert.ok(f.calls.every(c=>!c.route.includes('operations')&&!c.route.endsWith('/messages')));
 assert.ok(f.calls.filter(c=>!c.route.includes('command-observations?')&&!c.route.endsWith('/inspect')).every(c=>c.route.endsWith('/snapshot')));
 assert.equal(JSON.stringify(c).includes('blobs/'),false);
});
test('changed original part and ambiguous native call fail before command catalog or inspect',async()=>{
 const f=fixture();f.part.state.output='later';await assert.rejects(sources(f),{code:'TOOL_CHANGED'});assert.equal(f.calls.length,1);
 const g=fixture();g.session.messages[0].parts.push({...g.part,id:'prt_other'});await assert.rejects(sources(g),{code:'COMMAND_TEST_REPORT_CHANGED'});assert.equal(g.calls.length,1);
});
test('foreign source and changed source catalog never reach the inspect POST',async()=>{
 const f=fixture(),c=await sources(f);await assert.rejects(report(f,c,{source_ref:{catalog_sha256:c.catalog_sha256,path:'other.xml',sha256:'f'.repeat(64)}}));
 f.command.exit_code=7;await assert.rejects(report(f,c));assert.equal(f.calls.filter(c=>c.body!==undefined).length,0);
});
test('live project grant is rechecked before readonly inspection and before publishing its result',async()=>{
 const f=fixture(),c=await sources(f);let checks=0;
 await assert.rejects(report(f,c,{}, {currentGrants:()=>({methods:['session.tool.report'],project_ids:++checks===1?['project']:[]})}),{code:'COMMAND_TEST_REPORT_CHANGED'});
 assert.equal(checks,2);assert.equal(f.calls.filter(c=>c.body!==undefined).length,1);
 const g=fixture(),gc=await sources(g);await assert.rejects(report(g,gc,{}, {currentGrants:()=>({methods:[],project_ids:null})}));assert.equal(g.calls.filter(c=>c.body!==undefined).length,0);
});
test('unknown command and nonzero exit remain separate from a report listing no failure',async()=>{
 for(const [status,exit,reason]of[['UNKNOWN_OUTCOME',null,'COMMAND_OUTCOME_UNKNOWN'],['EXITED',7,'COMMAND_NONZERO_WITH_REPORT_PASS']]){const f=fixture();f.command.status=status;f.command.exit_code=exit;const c=await sources(f),r=await report(f,c);assert.equal(r.report.command_status,status);assert.equal(r.report.command_exit_code,exit);assert.ok(r.report.reason_codes.includes(reason));}
});
test('expanded command execution input and changed expected report reference are rejected',async()=>{
 const f=fixture(),c=await sources(f);await assert.rejects(report(f,c,{run:true}));assert.equal(f.calls.filter(c=>c.body!==undefined).length,0);
 await assert.rejects(report(f,c,{report_ref:{id:'8'.repeat(64),revision:1,sha256:'9'.repeat(64)}}),{code:'COMMAND_TEST_REPORT_CHANGED'});
});
