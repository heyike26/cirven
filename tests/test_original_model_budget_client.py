"""AUTHORED_NOT_RUN: actual typed client, UNIT HTTP boundary; no App/native."""
from pathlib import Path
import json,subprocess
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
import {AgentSessionsApi} from '__SRC__/agent-sessions.ts';
async function main(){
const H='a'.repeat(64),session={project_id:'project',session_ref:{id:'e'.repeat(32),revision:1,sha256:H},generation:3},calls=[];
const original={kind:'ORIGINAL_MODEL_BUDGET',project_id:session.project_id,session_ref:session.session_ref,generation:3,counter:'SUBSCRIPTION_HTTP_RESERVATIONS',scope:'ORIGINAL_OWNER_ALL_NATIVE_DESCENDANTS_ALL_GENERATIONS',status:'AVAILABLE',reservations:2,max_calls:3,read_only:true,metadata_writes:0,model_calls:0,commercial_usage:'NOT_MEASURED',output_token_cap:'NOT_QUALIFIED_NATIVE_CODEX_PLUGIN'};let value=original;
const client=new AgentSessionsApi({projectPath:id=>'/api/projects/'+id,request:async(...args)=>{calls.push(args);return structuredClone(value);}});
function check(v){if(!v)throw Error('Original budget identity mismatch');}async function rejects(fn){let failed=false;try{await fn();}catch{failed=true;}check(failed);}
check((await client.modelBudget(session)).reservations===2);check(calls[0][0].endsWith('/model-budget?session_sha256='+H)&&calls[0][1]===undefined);
for(const change of [{generation:4},{session_ref:{...session.session_ref,sha256:'f'.repeat(64)}},{project_id:'other'},{reservations:4},{reservations:-1},{reservations:true},{model_calls:1},{metadata_writes:1},{scope:'ONE_CHILD'},{counter:'USER_TURNS'}]){value={...original,...change};await rejects(()=>client.modelBudget(session));}
value={...original,status:'UNAVAILABLE',reservations:null};check((await client.modelBudget(session)).reservations===null);check(calls.every(c=>c[1]===undefined));
console.log(JSON.stringify({status:'UNIT_PASS',read_only:true,bad_owner_or_counter_rejected:true}));
}main().catch(e=>{console.error(e);process.exitCode=1;});
'''


def test_actual_budget_client_rejects_wrong_owner_and_impossible_counts(tmp_path):
    entry=tmp_path/'budget.ts';bundle=tmp_path/'budget.cjs';entry.write_text(FIXTURE.replace('__SRC__',str(ROOT/'apps/agent-shell/src')))
    node=ROOT/'.node-tools/node_modules/node/bin/node';builder=tmp_path/'build.cjs'
    builder.write_text('require('+json.dumps(str(ROOT/'apps/agent-shell/node_modules/esbuild'))+').buildSync({entryPoints:['+json.dumps(str(entry))+'],bundle:true,platform:"node",format:"cjs",outfile:'+json.dumps(str(bundle))+'});')
    built=subprocess.run([str(node),str(builder)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
    result=subprocess.run([str(node),str(bundle)],cwd=ROOT,capture_output=True,text=True,timeout=30);assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['read_only'] is True
