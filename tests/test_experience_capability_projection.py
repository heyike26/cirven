"""AUTHORED_NOT_RUN: actual TS receipt validation/status functions, no UI claim."""
from pathlib import Path
import json,subprocess

ROOT=Path(__file__).resolve().parents[1]


def test_real_client_rejects_positive_contradictions_and_reports_pause_forget_with_legacy_compatibility(tmp_path):
    source=ROOT/'apps/agent-shell/src/native-capabilities.ts';entry=tmp_path/'entry.ts';bundle=tmp_path/'entry.cjs'
    entry.write_text('import {assertAgentCapabilities,capabilityAvailability} from '+json.dumps(str(source))+''';
const base={id:'learned',config_ref:{id:'learned',revision:1,sha256:'a'.repeat(64)},enabled:true,integrity:'CURRENT',loaded_in_original_session:false,qualified:false,native_binding:'DECLARED',title:'Original human title',description:'Preserved description'};
const control={revision:1,state:'PAUSED',effective_state:'PAUSED',record_sha256:'b'.repeat(64),finalized:true,target_native_operation_id:'original_save',operation_id:'pause',model_calls:0,processes_started:0};
function directory(item){return {schema:'eeas.native-capabilities.v1',skills:[item],mcp:[]};}
function check(v){if(!v)throw Error('Source/pause projection mismatch');}
check(assertAgentCapabilities(directory(base)).skills.length===1);check(capabilityAvailability(base)==='已启用');
check(capabilityAvailability({...base,availability_observation_current:false})==='可用状态待核对');
const paused={...base,capability_control:control,effective_enabled:false};assertAgentCapabilities(directory(paused));check(capabilityAvailability(paused)==='已暂停');
const forgotten={...base,experience_source:{availability:'FORGOTTEN',forgotten:true,revision:1},effective_enabled:false};assertAgentCapabilities(directory(forgotten));check(capabilityAvailability(forgotten)==='来源已忘记');
const withdrawn={...base,experience_source:{availability:'SOURCE_WITHDRAWN_OR_UNAVAILABLE',forgotten:false},effective_enabled:false};assertAgentCapabilities(directory(withdrawn));check(capabilityAvailability(withdrawn)==='来源已撤回或不可用');
for(const bad of [{...paused,effective_enabled:true},{...forgotten,effective_enabled:true},{...paused,capability_control:{...control,finalized:false,effective_state:'ACTIVE'}},{...paused,capability_control:{...control,record_sha256:['b'.repeat(64)]}},{...forgotten,experience_source:{...forgotten.experience_source,revision:34}},{...forgotten,effective_enabled:undefined}]){let rejected=false;try{assertAgentCapabilities(directory(bad));}catch{rejected=true;}check(rejected);}
console.log(JSON.stringify({legacy:true,paused:true,forgotten:true,withdrawn:true,contradictions_rejected:6,model_calls:0}));
''')
    node=ROOT/'.node-tools/node_modules/node/bin/node';builder=tmp_path/'build.cjs'
    builder.write_text('require('+json.dumps(str(ROOT/'apps/agent-shell/node_modules/esbuild'))+').buildSync({entryPoints:['+json.dumps(str(entry))+'],bundle:true,platform:"node",format:"cjs",outfile:'+json.dumps(str(bundle))+'});')
    built=subprocess.run([str(node),str(builder)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
    result=subprocess.run([str(node),str(bundle)],cwd=ROOT,capture_output=True,text=True,timeout=30);assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['contradictions_rejected']==6
