import {api,type ApiClient} from './api.ts';
import type {AgentSession} from './agent-sessions';
import type {NativeRef} from './native-collaboration';
import {originalFileScope,type OriginalFileScope} from './original-session-files.ts';
import type {OriginalCommandObservation} from './original-command-observations';
import {configurationHash as hash,configurationCanonical as canonical} from './conversation-configuration.ts';

export type ReportSource=OriginalCommandObservation['sources'][number];
export type ReportFilter='ALL'|'PROBLEMS'|'SKIPPED'|'PASSED';
export type ReportCase={ordinal:number;suite:string;name:string;classname:string;file:string;line:string|null;status:'PASSED'|'FAILED'|'ERROR'|'SKIPPED'|'UNKNOWN';duration_seconds:string|null;message:string;detail:string;type:string;text_truncated:boolean};
export type CommandTestReport={
 kind:'ORIGINAL_COMMAND_TEST_REPORT';version:1;request_sha256:string;observation_scope:OriginalFileScope;
 basis:{schema:'eeas.command-test-report.v1';scope:OriginalFileScope;command_ref:NativeRef;command_generation:number;call_id:string;operation_id:string;root_operation_id:string;report_path:string;report_sha256:string};
 report_ref:NativeRef;report_bytes:number;
 summary:{outcome:'INCONSISTENT'|'NO_TESTS'|'FAILED'|'SKIPPED'|'PASSED_WITH_SKIPS'|'PASSED';total:number;counts:Record<ReportCase['status'],number>;integrity_issues:string[];metadata_warnings:string[];duration_seconds:string|null};
 command_status:'EXITED'|'UNKNOWN_OUTCOME';command_exit_code:number|null;
 association:'BEFORE_UNAVAILABLE'|'NEW_SNAPSHOT'|'UNCHANGED_BYTES'|'CHANGED_BYTES';reason_codes:string[];cases:ReportCase[];
 page:{offset:number;limit:number;outcome:ReportFilter;matching:number;next_offset:number|null};
 source_scope:'IMMUTABLE_AFTER_COMMAND_SNAPSHOT';writer_attribution:'NOT_PROVEN';all_dependencies_captured:false;current_workspace_verified:false;target_environment_verified:false;whole_task_acceptance:'NOT_ASSESSED';
 read_only:true;model_calls:0;commands_started:0;source_files_written:0;automatic_replay:false;
};
const check=(v:unknown)=>{if(!v)throw Error('COMMAND_TEST_REPORT_UNAVAILABLE');};
const same=(a:unknown,b:unknown)=>canonical(a)===canonical(b);
const sha=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const count=(v:unknown,max=2000)=>Number.isSafeInteger(v)&&Number(v)>=0&&Number(v)<=max;
const text=(v:unknown,max:number)=>typeof v==='string'&&Array.from(v).length<=max&&!/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(v);
const duration=(v:unknown)=>v===null||typeof v==='string'&&v.length<=64&&/^[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?$/.test(v);
export function observedReportSources(entry:OriginalCommandObservation){return entry.sources.filter(source=>/\.xml$/i.test(source.path)&&source.after?.state==='UTF8'&&sha(source.after.sha256));}
export class OriginalTestReportApi{
 constructor(private client:Pick<ApiClient,'request'>=api){}
 async read(session:AgentSession,command:OriginalCommandObservation,source:ReportSource,options:{offset?:number;outcome?:ReportFilter;expectedRef?:NativeRef}={},signal?:AbortSignal){
  const scope=originalFileScope(session),after=source.after;check(scope&&after?.state==='UTF8'&&sha(after.sha256)&&command.source_generation>=1&&command.source_generation<=scope!.generation&&command.sources.some(row=>same(row,source)));
  const request={session_ref:scope!.session_ref,generation:scope!.generation,workspace_ref:scope!.workspace_ref,
   command_ref:command.record_ref,command_generation:command.source_generation,call_id:command.call_id,
   report_path:source.path,report_sha256:after!.sha256,offset:options.offset??0,limit:20,outcome:options.outcome??'ALL'};
  const root='/api/projects/'+encodeURIComponent(scope!.project_id)+'/agent-sessions/'+encodeURIComponent(scope!.session_ref.id)+'/command-test-reports/inspect';
  const value=await this.client.request<CommandTestReport>(root,'POST',request,signal,false);check(value&&new TextEncoder().encode(JSON.stringify(value)).length<=131072);
  const expected={schema:'eeas.command-test-report.v1',scope:{...scope,generation:command.source_generation},command_ref:command.record_ref,command_generation:command.source_generation,call_id:command.call_id,operation_id:command.operation_id,root_operation_id:command.root_operation_id,report_path:source.path,report_sha256:after!.sha256};
  check(value.kind==='ORIGINAL_COMMAND_TEST_REPORT'&&value.version===1&&value.request_sha256===await hash(request)&&same(value.observation_scope,scope)&&same(value.basis,expected)&&value.report_ref?.revision===1&&value.report_ref.id===await hash(expected)&&sha(value.report_ref.sha256)&&value.report_bytes===after!.bytes);
  if(options.expectedRef)check(same(value.report_ref,options.expectedRef));
  check(value.read_only===true&&value.model_calls===0&&value.commands_started===0&&value.source_files_written===0&&value.automatic_replay===false&&value.writer_attribution==='NOT_PROVEN'&&value.source_scope==='IMMUTABLE_AFTER_COMMAND_SNAPSHOT'&&value.all_dependencies_captured===false&&value.current_workspace_verified===false&&value.target_environment_verified===false&&value.whole_task_acceptance==='NOT_ASSESSED');
  const association=source.before===null?'BEFORE_UNAVAILABLE':source.before.state==='ABSENT'?'NEW_SNAPSHOT':source.before.sha256===after!.sha256?'UNCHANGED_BYTES':'CHANGED_BYTES';
  check(value.command_status===command.status&&value.command_exit_code===command.exit_code&&value.association===association);
  const summary=value.summary,statuses:ReportCase['status'][]=['PASSED','FAILED','ERROR','SKIPPED','UNKNOWN'];
  check(summary&&['INCONSISTENT','NO_TESTS','FAILED','SKIPPED','PASSED_WITH_SKIPS','PASSED'].includes(summary.outcome)&&count(summary.total)&&statuses.every(key=>count(summary.counts?.[key]))&&statuses.reduce((total,key)=>total+summary.counts[key],0)===summary.total&&duration(summary.duration_seconds));
  for(const list of [summary.integrity_issues,summary.metadata_warnings,value.reason_codes])check(Array.isArray(list)&&list.length<=32&&list.every(code=>typeof code==='string'&&/^[A-Z_]{1,100}$/.test(code)));
  const derived=summary.integrity_issues.length||summary.counts.UNKNOWN?'INCONSISTENT':!summary.total?'NO_TESTS':summary.counts.FAILED||summary.counts.ERROR?'FAILED':summary.counts.SKIPPED===summary.total?'SKIPPED':summary.counts.SKIPPED?'PASSED_WITH_SKIPS':'PASSED';check(summary.outcome===derived);
  check(value.page?.offset===request.offset&&value.page.limit===20&&value.page.outcome===request.outcome&&count(value.page.matching)&&value.page.matching<=summary.total&&Array.isArray(value.cases)&&value.cases.length===Math.min(20,Math.max(0,value.page.matching-request.offset)));
  check(value.page.next_offset===(request.offset+value.cases.length<value.page.matching?request.offset+value.cases.length:null));
  let previous=-1;for(const row of value.cases){
   check(count(row.ordinal)&&row.ordinal<summary.total&&row.ordinal>previous&&statuses.includes(row.status)&&text(row.suite,160)&&text(row.name,160)&&text(row.classname,160)&&text(row.file,160)&&text(row.message,256)&&text(row.detail,512)&&text(row.type,80)&&typeof row.text_truncated==='boolean'&&duration(row.duration_seconds)&&(row.line===null||typeof row.line==='string'&&/^\d{1,10}$/.test(row.line)));
   if(request.outcome==='PROBLEMS')check(['FAILED','ERROR','UNKNOWN'].includes(row.status));else if(request.outcome!=='ALL')check(row.status===request.outcome);previous=row.ordinal;
  }
  return value;
 }
}
