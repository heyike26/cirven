'use strict';
// AUTHORED_NOT_RUN. Actual private controller state; RPC is an explicit UNIT boundary.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {PrivateState,CompanionController,draftKey,scopeOf}=require('../controller.cjs'),tools=require('../session-tool.cjs'),wire=require('../test-report-wire.cjs');
function scene(t){const root=fs.mkdtempSync(path.join(process.env.EEAS_TEST_TMPDIR||os.tmpdir(),'cirven-report-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const c=new CompanionController({state:new PrivateState(root)}),ref={message_id:'msg_one',part_id:'prt_one',call_id:'call_one',sha256:'b'.repeat(64)};
 c.selected={project_id:'project',session_ref:{id:'session',revision:1,sha256:'a'.repeat(64)},generation:2,messages:[{info:{id:'msg_one'},parts:[{id:'prt_one',type:'tool',tool_ref:ref}]}]};
 const scope=scopeOf(c.selected),command={record_ref:{id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)},source_generation:1,call_id:'call_one',operation_id:'op_one',root_operation_id:'op_root',status:'EXITED',exit_code:0};
 const basis={kind:'ORIGINAL_COMMAND_TEST_REPORT_SOURCES',scope,tool_ref:ref,observation_scope:{...scope,workspace_ref:{id:'c'.repeat(64),revision:1,binding_sha256:'f'.repeat(64)}},command,sources:[{path:'tests/report.xml',sha256:'1'.repeat(64),bytes:45,association:'NEW_SNAPSHOT'}],coverage:{basis_scope:'DECLARED_FILES_ONLY',all_dependencies_captured:false,reason_codes:[]},read_only:true,model_calls:0,commands_started:0,source_files_written:0,automatic_replay:false,whole_task_acceptance:'NOT_ASSESSED'},catalog={...basis,catalog_sha256:wire.hash(basis)},token=tools.token(scope,ref),calls=[];
 c.client={methods:['session.tool.reports','session.tool.report'],rpc:async(method,s,payload)=>{calls.push({method,s,payload});return {status:'OK',result:method==='session.tool.reports'?catalog:reply(catalog,payload)};}};
 return {c,catalog,scope,ref,token,calls};
}
function reply(catalog,payload){const source=catalog.sources[0],request=wire.requestFor(catalog,source,payload),c=catalog.command,basis={schema:'eeas.command-test-report.v1',scope:{...catalog.observation_scope,generation:c.source_generation},command_ref:c.record_ref,command_generation:c.source_generation,call_id:c.call_id,operation_id:c.operation_id,root_operation_id:c.root_operation_id,report_path:source.path,report_sha256:source.sha256},matching=payload.outcome==='ALL'||payload.outcome==='PASSED'?21:0;
 const report={kind:'ORIGINAL_COMMAND_TEST_REPORT',version:1,request_sha256:wire.hash(request),observation_scope:catalog.observation_scope,basis,report_ref:{id:wire.hash(basis),revision:1,sha256:'2'.repeat(64)},report_bytes:45,summary:{outcome:'PASSED',total:21,counts:{PASSED:21,FAILED:0,ERROR:0,SKIPPED:0,UNKNOWN:0},integrity_issues:[],metadata_warnings:[],duration_seconds:null},command_status:c.status,command_exit_code:c.exit_code,association:'NEW_SNAPSHOT',reason_codes:[],cases:Array.from({length:Math.min(20,Math.max(0,matching-payload.offset))},(_,i)=>({ordinal:payload.offset+i,suite:'suite',classname:'',name:'case'+(payload.offset+i),file:'../../self-stated.py',line:'12',status:'PASSED',duration_seconds:null,message:'',detail:'',type:'',text_truncated:false})),page:{offset:payload.offset,limit:20,outcome:payload.outcome,matching,next_offset:payload.offset+20<matching?payload.offset+20:null},source_scope:'IMMUTABLE_AFTER_COMMAND_SNAPSHOT',writer_attribution:'NOT_PROVEN',all_dependencies_captured:false,current_workspace_verified:false,target_environment_verified:false,whole_task_acceptance:'NOT_ASSESSED',read_only:true,model_calls:0,commands_started:0,source_files_written:0,automatic_replay:false};
 return {scope:catalog.scope,tool_ref:catalog.tool_ref,catalog_sha256:catalog.catalog_sha256,report};}
async function load(f){await f.c.commandReports.sources(f.token);return f.c.commandReports.snapshot().catalog.sources[0].token;}
test('original tool report lookup and page preserve original draft without a mutation journal',async t=>{
 const f=scene(t),key=draftKey(f.c.selected);f.c.setDraft(key,0,'UNSENT_MANUAL_WORK');const token=await load(f);await f.c.commandReports.page(token);
 const s=f.c.snapshot();assert.equal(s.draft.text,'UNSENT_MANUAL_WORK');assert.equal(s.tools.reports.detail.report.basis.scope.generation,1);assert.equal(s.tools.reports.detail.report.observation_scope.generation,2);assert.equal(f.c.state.value.requests.length,0);assert.deepEqual(f.calls.map(x=>x.method),['session.tool.reports','session.tool.report']);
});
test('late source catalog and page cannot appear after navigation or changed original part',async t=>{
 const f=scene(t);let release;f.c.client.rpc=()=>new Promise(resolve=>{release=resolve;});const p=f.c.commandReports.sources(f.token);f.c.epoch++;release({status:'OK',result:f.catalog});await p;assert.equal(f.c.commandReports.catalog,null);
 const g=scene(t),token=await load(g);g.c.client.rpc=(_method,_scope,payload)=>new Promise(resolve=>{release=()=>resolve({status:'OK',result:reply(g.catalog,payload)});});const page=g.c.commandReports.page(token);g.c.selected.messages[0].parts[0].tool_ref={...g.ref,sha256:'9'.repeat(64)};release();await page;assert.equal(g.c.snapshot().tools.reports.detail,null);
});
test('revoked report methods clear current view and reject further calls without altering input',async t=>{
 const f=scene(t),token=await load(f);await f.c.commandReports.page(token);f.c.client.methods=[];assert.equal(f.c.snapshot().tools.reports.detail,null);const n=f.calls.length;await assert.rejects(f.c.commandReports.sources(f.token));await assert.rejects(f.c.commandReports.page(token));assert.equal(f.calls.length,n);
});
test('pagination carries the original report ref and retains the same report across filters',async t=>{
 const f=scene(t),token=await load(f);await f.c.commandReports.page(token);const original=f.c.commandReports.detail.report.report_ref;await f.c.commandReports.page(token,20,'ALL');assert.equal(f.c.commandReports.detail.report.cases[0].ordinal,20);assert.deepEqual(f.calls.at(-1).payload.report_ref,original);await f.c.commandReports.page(token,0,'PROBLEMS');assert.equal(f.c.commandReports.detail.report.cases.length,0);assert.deepEqual(f.c.commandReports.detail.report.report_ref,original);
});
test('a changed report page preserves the previous verified result and shows an error',async t=>{
 const f=scene(t),token=await load(f);await f.c.commandReports.page(token);const previous=f.c.commandReports.detail;f.c.client.rpc=async(_m,_s,p)=>{const r=reply(f.catalog,p);r.report.report_ref.sha256='3'.repeat(64);return {status:'OK',result:r};};await f.c.commandReports.page(token,20);assert.equal(f.c.commandReports.detail,previous);assert.equal(f.c.commandReports.error,'COMMAND_TEST_REPORT_CHANGED');
});
test('foreign source tokens or out of bounds filters fail before contacting desktop',async t=>{
 const f=scene(t),token=await load(f),n=f.calls.length;for(const args of [['4'.repeat(64),0,'ALL'],[token,2001,'ALL'],[token,0,'RUN']])await assert.rejects(f.c.commandReports.page(...args));assert.equal(f.calls.length,n);
});
test('widened acceptance and false skipped status are rejected as report facts',async t=>{
 const f=scene(t),token=await load(f);for(const tamper of [r=>{r.report.target_environment_verified=true;},r=>{r.report.summary.counts.SKIPPED=1;},r=>{r.report.command_status='UNKNOWN_OUTCOME';},r=>{r.report.cases[0].status='SKIPPED';}]){f.c.client.rpc=async(_m,_s,p)=>{const r=reply(f.catalog,p);tamper(r);return {status:'OK',result:r};};await f.c.commandReports.page(token);assert.equal(f.c.commandReports.detail,null);assert.equal(f.c.commandReports.error,'COMMAND_TEST_REPORT_CHANGED');}
});
test('wrong catalog and explicit transport failure do not replace drafts or send commands',async t=>{
 const f=scene(t);f.c.client.rpc=async()=>({status:'OK',result:{...f.catalog,catalog_sha256:'0'.repeat(64)}});await f.c.commandReports.sources(f.token);assert.equal(f.c.commandReports.catalog,null);assert.equal(f.c.commandReports.error,'COMMAND_TEST_REPORT_CHANGED');f.c.client.rpc=async()=>{throw Object.assign(Error('lost'),{code:'ETIMEDOUT'});};await f.c.commandReports.sources(f.token);assert.equal(f.c.commandReports.error,'ETIMEDOUT');assert.equal(f.c.state.value.requests.length,0);
});
