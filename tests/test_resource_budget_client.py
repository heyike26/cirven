"""AUTHORED_NOT_RUN: real typed resource client, controlled HTTP DTO boundary."""
import json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
import {AgentSessionsApi,originalResourceOperation,originalResourceNeedsReview,originalResourceBytes} from '__SRC__/agent-sessions.ts';
async function main(){
const H='a'.repeat(64),session={project_id:'project',session_ref:{id:'e'.repeat(32),revision:1,sha256:H},generation:3,active_operation_id:'original'},calls=[];
const attribution={kind:'ORIGINAL_RETAINED_EVENT_ATTRIBUTION',message_locations:1,pending_bytes:0,gap:null,accounting_unconfirmed:false,read_only:true,copied_message_bodies:0,coverage:'NEWLY_BOUND_WORK_ONLY_NOT_RETROACTIVE',physical_disk_bytes:'NOT_MEASURED'};
const usage={kind:'ORIGINAL_WORK_RESOURCE_USAGE',resource_ref:{id:H,revision:0,sha256:H},authority:{project_id:'project'},limits:{source_bytes:64,source_reads:16,model_attempts:3,retained_output_bytes:16},used:{source_bytes:7,source_reads:1,model_attempts:1,retained_output_bytes:18},pending_reads:0,pending_read_bytes:0,accounting_unconfirmed:false,retention_threshold_exceeded:true,usage_coverage:'SINCE_ALLOCATION_NOT_RETROACTIVE',counters_include_unconfirmed_reservations:true,model_reservations_are_provider_usage:false,physical_disk_io:'NOT_MEASURED',read_only:true,metadata_writes:0};
const original={kind:'ORIGINAL_WORK_RESOURCE_BUDGET',project_id:'project',session_ref:session.session_ref,generation:3,operation_id:'original',observed_at:'2026-11-05T00:00:00+00:00',scope:'EXACT_SELECTED_ORIGINAL_WORK',read_only:true,metadata_writes:0,model_calls:0,status:'AVAILABLE',reason:null,usage,retained_event_attribution:attribution};let value=original;
const client=new AgentSessionsApi({projectPath:id=>'/api/projects/'+id,request:async(...args)=>{calls.push(args);return structuredClone(value);}});
function check(v){if(!v)throw Error('Resource original-work receipt requirement failed');}async function rejects(fn){let failed=false;try{await fn();}catch{failed=true;}check(failed);}
const soft=await client.resourceBudget(session,'original');check(soft.usage.used.retained_output_bytes===18&&originalResourceNeedsReview(soft));check(calls[0][0].endsWith('/resource-budget?session_sha256='+H+'&operation_id=original')&&calls[0][1]===undefined);
for(const patch of [{generation:4},{operation_id:'another'},{project_id:'another'},{session_ref:{...session.session_ref,sha256:'f'.repeat(64)}},{read_only:false},{model_calls:1},{metadata_writes:1},{scope:'CURRENT_HEAD'},{status:'ZERO_FALLBACK'},{observed_at:'unknown'},{retained_event_attribution:null}]){value={...original,...patch};await rejects(()=>client.resourceBudget(session,'original'));}
for(const patch of [{used:{...usage.used,source_bytes:65}},{used:{...usage.used,model_attempts:4}},{used:{...usage.used,source_reads:true}},{pending_reads:33},{pending_read_bytes:64},{resource_ref:{...usage.resource_ref,sha256:'bad'}},{limits:{...usage.limits,source_bytes:2**48}},{retention_threshold_exceeded:false},{physical_disk_io:'MEASURED'},{model_reservations_are_provider_usage:true},{metadata_writes:1}]){value={...original,usage:{...usage,...patch}};await rejects(()=>client.resourceBudget(session,'original'));}
for(const patch of [{pending_bytes:-1},{message_locations:4097},{copied_message_bodies:1},{gap:{first_event_seq:-1,reason:'unknown'}},{accounting_unconfirmed:'yes'}]){value={...original,retained_event_attribution:{...attribution,...patch}};await rejects(()=>client.resourceBudget(session,'original'));}
value={...original,status:'NOT_ALLOCATED',reason:'NO_NEW_ALLOCATION_OR_RETROACTIVE_USAGE',usage:null};check((await client.resourceBudget(session,'original')).usage===null);
value={...original,status:'UNAVAILABLE',reason:'ATTRIBUTION_UNAVAILABLE',usage:null,retained_event_attribution:null};check(originalResourceNeedsReview(await client.resourceBudget(session,'original')));
value={...original,status:'NO_WORK_SELECTED',reason:'EXPLICIT_WORK_SELECTION_REQUIRED',operation_id:null,usage:null};check((await client.resourceBudget(session)).operation_id===null);
const before=calls.length;await rejects(()=>client.resourceBudget(session,'../foreign'));check(calls.length===before);
check(originalResourceOperation(session)==='original');check(originalResourceOperation({...session,active_operation_id:null,work_mode_message:{operation_id:'old',generation:2}})===undefined);check(originalResourceOperation({...session,active_operation_id:null,work_mode_message:{operation_id:'same',generation:3}})==='same');
check(originalResourceBytes(18)==='18 B'&&originalResourceBytes(1024)==='1.0 KiB');check(calls.every(c=>c[1]===undefined));
console.log(JSON.stringify({status:'UNIT_PASS',read_only:true,soft_retention_preserves_received:true,missing_history_never_zero:true,wrong_work_and_impossible_counters_rejected:true}));
}main().catch(e=>{console.error(e);process.exitCode=1;});
'''


def test_actual_resource_client_rejects_foreign_work_and_unknown_zero_fallback(tmp_path):
    entry=tmp_path/'resources.ts';bundle=tmp_path/'resources.cjs';entry.write_text(FIXTURE.replace('__SRC__',str(ROOT/'apps/agent-shell/src')))
    node=ROOT/'.node-tools/node_modules/node/bin/node';builder=tmp_path/'build.cjs'
    builder.write_text('require('+json.dumps(str(ROOT/'apps/agent-shell/node_modules/esbuild'))+').buildSync({entryPoints:['+json.dumps(str(entry))+'],bundle:true,platform:"node",format:"cjs",outfile:'+json.dumps(str(bundle))+'});')
    built=subprocess.run([str(node),str(builder)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
    result=subprocess.run([str(node),str(bundle)],cwd=ROOT,capture_output=True,text=True,timeout=30);assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['missing_history_never_zero'] is True
