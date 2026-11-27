// AUTHORED_NOT_RUN. Real typed client with explicit UNIT API boundary; no model/UI.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {OriginalTestReportApi,observedReportSources} from '../src/original-test-report.ts';
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const sha=v=>createHash('sha256').update(stable(v)).digest('hex');
function scene(change=()=>{}){
 const session={project_id:'project',session_ref:{id:'original',revision:1,sha256:'a'.repeat(64)},generation:1,workspace_ref:{id:'e'.repeat(64),revision:1,binding_sha256:'f'.repeat(64)},original_session_id:'ses_original'};
 const source={path:'reports/result.xml',before:{state:'ABSENT',sha256:null,bytes:0,line_count:0,blob:null,stat:null},after:{state:'UTF8',sha256:'d'.repeat(64),bytes:123,line_count:2,blob:'blobs/'+'d'.repeat(64)+'.utf8',stat:null}};
 const command={record_ref:{id:'b'.repeat(64),revision:1,sha256:'c'.repeat(64)},source_generation:1,operation_id:'original_run',root_operation_id:'original_root',call_id:'call_report',status:'EXITED',exit_code:0,sources:[source]};
 const calls=[],api=new OriginalTestReportApi({request:async(route,method,request,_signal,retry)=>{
  calls.push({route,method,request,retry});const scope={project_id:session.project_id,session_ref:session.session_ref,generation:session.generation,workspace_ref:session.workspace_ref};
  const basis={schema:'eeas.command-test-report.v1',scope:{...scope,generation:command.source_generation},command_ref:command.record_ref,command_generation:1,call_id:command.call_id,operation_id:command.operation_id,root_operation_id:command.root_operation_id,report_path:source.path,report_sha256:source.after.sha256};
  const row={ordinal:0,suite:'suite',name:'test_one',classname:'TestSuite',file:'tests/test_math.py',line:'1',status:'PASSED',duration_seconds:'0.01',message:'',detail:'',type:'',text_truncated:false};
  const result={kind:'ORIGINAL_COMMAND_TEST_REPORT',version:1,request_sha256:sha(request),basis,observation_scope:scope,report_ref:{id:sha(basis),revision:1,sha256:'0'.repeat(64)},report_bytes:123,summary:{outcome:'PASSED',total:1,counts:{PASSED:1,FAILED:0,ERROR:0,SKIPPED:0,UNKNOWN:0},integrity_issues:[],metadata_warnings:[],duration_seconds:'0.01'},command_status:command.status,command_exit_code:command.exit_code,association:source.before.state==='ABSENT'?'NEW_SNAPSHOT':'UNCHANGED_BYTES',reason_codes:[],cases:request.offset===0&&(request.outcome==='ALL'||request.outcome==='PASSED')?[row]:[],page:{offset:request.offset,limit:20,outcome:request.outcome,matching:request.outcome==='ALL'||request.outcome==='PASSED'?1:0,next_offset:null},source_scope:'IMMUTABLE_AFTER_COMMAND_SNAPSHOT',writer_attribution:'NOT_PROVEN',all_dependencies_captured:false,current_workspace_verified:false,target_environment_verified:false,whole_task_acceptance:'NOT_ASSESSED',read_only:true,model_calls:0,commands_started:0,source_files_written:0,automatic_replay:false};
  change(result,request);return result;
 }});
 return {session,source,command,calls,api};
}

test('report inspection sends exact original scope and source ref without any command execution input',async()=>{
 const x=scene(),r=await x.api.read(x.session,x.command,x.source);assert.equal(r.summary.total,1);assert.equal(x.calls.length,1);assert.equal(x.calls[0].method,'POST');assert.equal(x.calls[0].retry,false);
 const body=x.calls[0].request;assert.deepEqual(body.command_ref,x.command.record_ref);assert.equal(body.call_id,x.command.call_id);assert.equal(body.report_sha256,x.source.after.sha256);assert.equal(body.raw_text,undefined);assert.equal(body.command,undefined);
 assert.match(x.calls[0].route,/agent-sessions\/original\/command-test-reports\/inspect$/);
});

test('foreign command basis, broadened scope or acceptance claims cannot become a report result',async()=>{
 for(const update of [r=>r.basis.root_operation_id='foreign',r=>r.commands_started=1,r=>r.current_workspace_verified=true,r=>r.whole_task_acceptance='PASSED',r=>r.writer_attribution='CONFIRMED']){
  const x=scene(update);await assert.rejects(()=>x.api.read(x.session,x.command,x.source));
 }
});

test('all-pass label cannot hide skipped or inconsistent counts',async()=>{
 const x=scene(r=>{r.summary.total=2;r.summary.counts.SKIPPED=1;});await assert.rejects(()=>x.api.read(x.session,x.command,x.source));
 const other=scene(r=>{r.summary.counts.PASSED=10;});await assert.rejects(()=>other.api.read(other.session,other.command,other.source));
});

test('report bytes matching the earlier snapshot cannot be relabeled newly generated',async()=>{
 const x=scene(r=>r.association='NEW_SNAPSHOT');x.source.before={...x.source.after};await assert.rejects(()=>x.api.read(x.session,x.command,x.source));
});

test('filtered pages preserve report identity and reject mismatched page outcomes or another projection',async()=>{
 const x=scene(),first=await x.api.read(x.session,x.command,x.source),filtered=await x.api.read(x.session,x.command,x.source,{outcome:'PROBLEMS',expectedRef:first.report_ref});assert.equal(filtered.cases.length,0);assert.deepEqual(filtered.report_ref,first.report_ref);
 const other=scene(r=>r.report_ref.sha256='1'.repeat(64));await assert.rejects(()=>other.api.read(other.session,other.command,other.source,{expectedRef:first.report_ref}));
});

test('only explicitly observed after XML snapshots are suggested; absence is not an empty passing report',()=>{
 const x=scene();x.command.sources.push({path:'source.py',before:null,after:x.source.after},{path:'missing.xml',before:null,after:null});assert.deepEqual(observedReportSources(x.command),[x.source]);
});

test('same original report keeps its reference after a newer saved owner observation generation',async()=>{
 const x=scene(),first=await x.api.read(x.session,x.command,x.source);x.session.generation=2;
 const later=await x.api.read(x.session,x.command,x.source,{expectedRef:first.report_ref});
 assert.equal(later.basis.scope.generation,1);assert.equal(later.observation_scope.generation,2);assert.deepEqual(later.report_ref,first.report_ref);assert.equal(x.calls.length,2);
});
