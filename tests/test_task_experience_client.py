"""AUTHORED_NOT_RUN: actual TS client with explicit UNIT HTTP boundary only."""
from pathlib import Path
import json,subprocess

ROOT=Path(__file__).resolve().parents[1]


def test_real_client_uses_opaque_prepared_hash_no_positive_replay_and_exact_control_lookup(tmp_path):
    entry=tmp_path/'client.ts';bundle=tmp_path/'client.cjs'
    entry.write_text('import {TaskExperienceApi,assertExperiencePreparation} from '+json.dumps(str(ROOT/'apps/agent-shell/src/task-experience.ts'))+''';
async function main(){
const H='a'.repeat(64),S='b'.repeat(64),O='c'.repeat(64),ref=id=>({id,revision:1,sha256:H}),owner={project_id:'project',session_ref:ref('e'.repeat(32))};
const packets=new Map(),calls=[];let last,unknown=false,tamper=false,page;
function check(v){if(!v)throw Error('Exact experience client protocol mismatch');}
async function rejects(run){let rejected=false;try{await run();}catch{rejected=true;}check(rejected);}
function preparation(action,request){return {kind:'EXPERIENCE_REQUEST_PREPARATION',version:1,project_id:owner.project_id,session_id:owner.session_ref.id,session_ref:owner.session_ref,action,request:structuredClone(request),request_sha256:S,read_only:true,metadata_writes:0,model_calls:0,processes_started:0,permission_granted:false,automatic_replay:false,authority_checked_on_actual_command:true};}
const client=new TaskExperienceApi({projectPath:id=>'/api/projects/'+id,request:async(path,method='GET',body,signal,retry)=>{
 calls.push({path,method,body:structuredClone(body),retry});
 if(path.endsWith('requests/prepare')){last=preparation(body.action,body.request);packets.set(body.request.operation_id,last);return structuredClone(last);}
 if(method==='POST'){
  const p=packets.get(body.operation_id);check(retry===false);if(unknown){unknown=false;throw Error('UNIT response unknown after one dispatch');}
  if(p.action==='SOURCE_DECISION')return {decision:{operation_id:body.operation_id,request_sha256:S},automatic_replay:false};
  if(p.action==='RECOVER_CONFIGURATION')return {operation_id:body.operation_id,request_sha256:O,automatic_replay:false};
  return {kind:'REVIEW',operation_id:body.operation_id,request_sha256:S,record_ref:ref(body.operation_id)};
 }
 if(path.includes('/candidates?'))return structuredClone(page);
 const url=new URL(path,'http://fixture.invalid'),operation=url.pathname.split('/').pop(),hash=url.searchParams.get('request_sha256');
 return {kind:'EXPERIENCE_PENDING_CONTROL_OBSERVATION',operation_id:operation,request_sha256:tamper?H:hash,decision_ref:ref(operation),automatic_replay:false};
}});
const review={operation_id:'review',candidate_ref:ref('candidate'),review_confirmed:true,statement:'Explicit independent review',cases:[{kind:'MCP_TOOL',id:'positive',label:'Positive',purpose:'POSITIVE',tool_name:'inspect',input:{number:1e-7},expected_result:{content:[],structuredContent:{number:1e-7},isError:false}},{kind:'MCP_TOOL',id:'negative',label:'Negative',purpose:'NEGATIVE',tool_name:'inspect',input:{number:-1e-7},expected_result:{content:[],structuredContent:null,isError:true}}],allowed_effects:['PROCESS_START'],max_model_calls:0,max_output_tokens:2048,timeout_seconds:120,process_boundary_acknowledged:true};
const p=await client.prepare(owner,'REVIEW',review);check(p.request_sha256===S);unknown=true;await rejects(()=>client.dispatch(owner,p));
check(calls.filter(c=>c.path.endsWith('/reviews')&&c.method==='POST').length===1);await client.lookup(owner,p);check(calls.filter(c=>c.path.endsWith('/reviews')&&c.method==='POST').length===1);
await rejects(()=>assertExperiencePreparation({...p,permission_granted:true},owner,'REVIEW',review));
await rejects(()=>assertExperiencePreparation({...p,project_id:'other'},owner,'REVIEW',review));
await rejects(()=>assertExperiencePreparation({...p,session_ref:{...owner.session_ref,sha256:O}},owner,'REVIEW',review));
await rejects(()=>assertExperiencePreparation({...p,request:{...review,statement:'Changed'}},owner,'REVIEW',review));
const pause={operation_id:'pause',activation_operation_id:'activate',activation_request_sha256:O,expected_control_revision:0,mode:'PAUSE',expected_current_configuration_ref:null,confirmed:true};
const paused=await client.prepare(owner,'PENDING_CONTROL',pause);const found=await client.dispatch(owner,paused);check(found.operation_id==='pause'&&found.request_sha256===S);
const looked=calls.findLast(c=>c.method==='GET');check(looked.path.includes('/activate/controls/pause?')&&looked.path.includes('mode=PAUSE')&&looked.path.includes('activation_request_sha256='+O));
tamper=true;await rejects(()=>client.lookup(owner,paused));tamper=false;
const source={operation_id:'forget',candidate_ref:ref('candidate'),expected_revision:0,action:'FORGET',confirmed:true,statement:'Do not use this source again'};
const sourcePacket=await client.prepare(owner,'SOURCE_DECISION',source);await client.dispatch(owner,sourcePacket);await client.lookup(owner,sourcePacket);check(calls.at(-1).path.includes('/source-policy/candidate/operations/forget?'));
const recover={operation_id:'activate',request_sha256:O,mode:'FINALIZE_LOCAL_RECORD',recovery_confirmed:true};const rp=await client.prepare(owner,'RECOVER_CONFIGURATION',recover);await client.dispatch(owner,rp);await client.lookup(owner,rp);check(calls.at(-1).path.includes('request_sha256='+O));
const row={candidate_ref:ref('candidate'),title:'😀'.repeat(200),kind:'SKILL',root_operation_id:'work',root_sha256:H,source_policy:{availability:'ACTIVE',revision:0,forgotten:false}};
page={schema:'eeas.task-experience-candidate.v1',project_id:owner.project_id,session_id:owner.session_ref.id,entries:[row],offset:0,next_offset:null,total:1,read_only:true,model_calls:0,commands_started:0,configuration_registered:false,automatic_replay:false};check((await client.candidates(owner)).entries[0].title===row.title);
page={...page,entries:[row,row],total:2};await rejects(()=>client.candidates(owner));page={...page,entries:[{...row,source_policy:{availability:'ACTIVE',revision:1,forgotten:true}}],total:1};await rejects(()=>client.candidates(owner));
console.log(JSON.stringify({opaque_server_hash:true,unknown_positive_dispatches:1,control_lookup:true,source_lookup:true,legacy_original_recovery_hash:true,unicode_title_preserved:true,bad_receipts_rejected:true}));
}main().catch(e=>{console.error(e);process.exitCode=1;});
''')
    node=ROOT/'.node-tools/node_modules/node/bin/node';builder=tmp_path/'build.cjs'
    builder.write_text('require('+json.dumps(str(ROOT/'apps/agent-shell/node_modules/esbuild'))+').buildSync({entryPoints:['+json.dumps(str(entry))+'],bundle:true,platform:"node",format:"cjs",outfile:'+json.dumps(str(bundle))+'});')
    built=subprocess.run([str(node),str(builder)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
    value=subprocess.run([str(node),str(bundle)],cwd=ROOT,capture_output=True,text=True,timeout=30);assert value.returncode==0,value.stderr
    assert json.loads(value.stdout)['unknown_positive_dispatches']==1
