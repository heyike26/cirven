'use strict';
// Shared inert wire validation. No transport, filesystem, XML parser or execution.
const {createHash}=require('node:crypto');
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const canonical=v=>JSON.stringify(v,(_k,x)=>object(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const hash=v=>createHash('sha256').update(canonical(v)).digest('hex');
const same=(a,b)=>canonical(a)===canonical(b);
const sha=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const id=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v);
const count=(v,max=2000)=>Number.isSafeInteger(v)&&v>=0&&v<=max;
const text=(v,max)=>typeof v==='string'&&Array.from(v).length<=max&&!/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(v);
const ref=v=>object(v)&&Object.keys(v).sort().join(',')==='id,revision,sha256'&&sha(v.id)&&v.revision===1&&sha(v.sha256);
const duration=v=>v===null||typeof v==='string'&&v.length<=64&&/^[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?$/.test(v);
const path=v=>typeof v==='string'&&v.length>0&&v.length<=4096&&!/[\\:\x00-\x1f\x7f]/.test(v)&&!v.startsWith('/')&&v.split('/').every(p=>p!==''&&p!=='.'&&p!=='..');
const codes=v=>Array.isArray(v)&&v.length<=64&&v.every(code=>typeof code==='string'&&/^[A-Z_]{1,100}$/.test(code));
const FILTERS=Object.freeze(['ALL','PROBLEMS','SKIPPED','PASSED']),STATUSES=Object.freeze(['PASSED','FAILED','ERROR','SKIPPED','UNKNOWN']);
function check(v){if(!v)throw Object.assign(new Error('原命令报告依据已变化，请重新读取。'),{code:'COMMAND_TEST_REPORT_CHANGED',public:true});}
function flags(v){check(v.read_only===true&&v.model_calls===0&&v.commands_started===0&&v.source_files_written===0&&v.automatic_replay===false&&v.whole_task_acceptance==='NOT_ASSESSED');}
function validateSources(value,scope,toolRef){
 check(object(value)&&Buffer.byteLength(JSON.stringify(value))<=196608&&value.kind==='ORIGINAL_COMMAND_TEST_REPORT_SOURCES'&&same(value.scope,scope)&&same(value.tool_ref,toolRef));flags(value);
 const {catalog_sha256:catalogHash,...basis}=value;check(sha(catalogHash)&&catalogHash===hash(basis));
 const observed=value.observation_scope,w=observed?.workspace_ref;
 check(same(observed,{...scope,workspace_ref:w})&&object(w)&&sha(w.id)&&count(w.revision,2147483647)&&w.revision>=1&&sha(w.binding_sha256));
 check(Array.isArray(value.sources)&&value.sources.length<=32&&object(value.coverage)&&value.coverage.basis_scope==='DECLARED_FILES_ONLY'&&value.coverage.all_dependencies_captured===false&&codes(value.coverage.reason_codes));
 const command=value.command;
 if(command===null)check(value.sources.length===0);else check(object(command)&&ref(command.record_ref)&&id(command.call_id)&&id(command.operation_id)&&id(command.root_operation_id)&&count(command.source_generation,Number.MAX_SAFE_INTEGER)&&command.source_generation>=1&&command.source_generation<=scope.generation&&['EXITED','UNKNOWN_OUTCOME'].includes(command.status)&&(command.exit_code===null||Number.isSafeInteger(command.exit_code))&&command.status===(command.exit_code===null?'UNKNOWN_OUTCOME':'EXITED')&&command.call_id===toolRef.call_id);
 const seen=new Set();for(const source of value.sources){check(object(source)&&Object.keys(source).sort().join(',')==='association,bytes,path,sha256'&&path(source.path)&&/\.xml$/i.test(source.path)&&!seen.has(source.path)&&sha(source.sha256)&&count(source.bytes,262144)&&source.bytes>=1&&['BEFORE_UNAVAILABLE','NEW_SNAPSHOT','UNCHANGED_BYTES','CHANGED_BYTES'].includes(source.association));seen.add(source.path);}
 return JSON.parse(JSON.stringify(value));
}
function requestFor(catalog,source,options={}){
 check(catalog.command&&catalog.sources.some(row=>same(row,source))&&count(options.offset??0)&&FILTERS.includes(options.outcome??'ALL'));
 const s=catalog.observation_scope,c=catalog.command;
 return {session_ref:s.session_ref,generation:s.generation,workspace_ref:s.workspace_ref,command_ref:c.record_ref,command_generation:c.source_generation,call_id:c.call_id,report_path:source.path,report_sha256:source.sha256,offset:options.offset??0,limit:20,outcome:options.outcome??'ALL'};
}
function validateReport(value,catalog,source,options={}){
 const request=requestFor(catalog,source,options),c=catalog.command;
 const basis={schema:'eeas.command-test-report.v1',scope:{...catalog.observation_scope,generation:c.source_generation},command_ref:c.record_ref,command_generation:c.source_generation,call_id:c.call_id,operation_id:c.operation_id,root_operation_id:c.root_operation_id,report_path:source.path,report_sha256:source.sha256};
 check(object(value)&&Buffer.byteLength(JSON.stringify(value))<=131072&&value.kind==='ORIGINAL_COMMAND_TEST_REPORT'&&value.version===1&&value.request_sha256===hash(request)&&same(value.observation_scope,catalog.observation_scope)&&same(value.basis,basis)&&ref(value.report_ref)&&value.report_ref.id===hash(basis)&&value.report_bytes===source.bytes);flags(value);
 if(options.expectedRef)check(same(value.report_ref,options.expectedRef));
 check(value.writer_attribution==='NOT_PROVEN'&&value.source_scope==='IMMUTABLE_AFTER_COMMAND_SNAPSHOT'&&value.all_dependencies_captured===false&&value.current_workspace_verified===false&&value.target_environment_verified===false&&value.command_status===c.status&&value.command_exit_code===c.exit_code&&value.association===source.association);
 const s=value.summary;check(object(s)&&object(s.counts)&&Object.keys(s.counts).sort().join(',')===[...STATUSES].sort().join(',')&&count(s.total)&&STATUSES.every(k=>count(s.counts[k]))&&STATUSES.reduce((n,k)=>n+s.counts[k],0)===s.total&&duration(s.duration_seconds)&&codes(s.integrity_issues)&&codes(s.metadata_warnings)&&codes(value.reason_codes));
 const derived=s.integrity_issues.length||s.counts.UNKNOWN?'INCONSISTENT':!s.total?'NO_TESTS':s.counts.FAILED||s.counts.ERROR?'FAILED':s.counts.SKIPPED===s.total?'SKIPPED':s.counts.SKIPPED?'PASSED_WITH_SKIPS':'PASSED';check(s.outcome===derived);
 const reasons=[];if(['BEFORE_UNAVAILABLE','UNCHANGED_BYTES'].includes(source.association))reasons.push('NEW_REPORT_GENERATION_NOT_PROVEN');if(c.status!=='EXITED')reasons.push('COMMAND_OUTCOME_UNKNOWN');if(c.exit_code!==0&&c.exit_code!==null&&['PASSED','PASSED_WITH_SKIPS'].includes(s.outcome))reasons.push('COMMAND_NONZERO_WITH_REPORT_PASS');check(same(value.reason_codes,reasons));
 const matching=request.outcome==='ALL'?s.total:request.outcome==='PROBLEMS'?s.counts.FAILED+s.counts.ERROR+s.counts.UNKNOWN:s.counts[request.outcome];
 check(object(value.page)&&value.page.offset===request.offset&&value.page.limit===20&&value.page.outcome===request.outcome&&value.page.matching===matching&&Array.isArray(value.cases)&&value.cases.length===Math.min(20,Math.max(0,matching-request.offset))&&value.page.next_offset===(request.offset+value.cases.length<matching?request.offset+value.cases.length:null));
 const pageCounts=Object.fromEntries(STATUSES.map(k=>[k,0]));let previous=-1;for(const row of value.cases){check(object(row)&&count(row.ordinal)&&row.ordinal<s.total&&row.ordinal>previous&&STATUSES.includes(row.status)&&text(row.suite,160)&&text(row.name,160)&&text(row.classname,160)&&text(row.file,160)&&text(row.message,256)&&text(row.detail,512)&&text(row.type,80)&&typeof row.text_truncated==='boolean'&&duration(row.duration_seconds)&&(row.line===null||typeof row.line==='string'&&/^\d{1,10}$/.test(row.line)));
  pageCounts[row.status]++;check(pageCounts[row.status]<=s.counts[row.status]);
  if(request.outcome==='PROBLEMS')check(['FAILED','ERROR','UNKNOWN'].includes(row.status));else if(request.outcome!=='ALL')check(row.status===request.outcome);previous=row.ordinal;
 }
 return JSON.parse(JSON.stringify(value));
}
const sourceToken=(catalog,source)=>hash({scope:catalog.scope,tool_ref:catalog.tool_ref,catalog_sha256:catalog.catalog_sha256,source});
module.exports={canonical,hash,same,sha,id,count,path,ref,check,FILTERS,validateSources,requestFor,validateReport,sourceToken};
