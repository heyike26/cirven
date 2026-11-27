"""AUTHORED_NOT_RUN: real editor/client/HTTP adapter, controlled UNIT fetch only."""
from pathlib import Path
import json,subprocess
ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
import {ApiClient} from '__API__';
import {TaskExperienceApi} from '__CLIENT__';
import {buildExperienceReviewJSON,reviewFields,experienceDraft,newExperienceCase} from '__EDITOR__';
import {webcrypto} from 'node:crypto';
if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto});
const H='a'.repeat(64),S='b'.repeat(64),ref=id=>({id,revision:1,sha256:H}),context={project_id:'project',session_ref:ref('e'.repeat(32)),generation:1,workspace_ref:{id:H,revision:1,binding_sha256:H}};
const check=v=>{if(!v)throw Error('Exact editor/transport mismatch');};
async function rejects(fn){let error=false;try{await fn();}catch{error=true;}check(error);}
async function main(){
const rows=['POSITIVE','NEGATIVE'].map(p=>({...newExperienceCase('MCP_TOOL',p),label:p,tool_name:'inspect',input_json:'{"integer":9007199254740993,"floating":1.0}',content_json:'[]',structured_json:'{"value":1.0}'}));
const draft=experienceDraft(context,H,'REVIEW',ref('candidate'),null,{...reviewFields('MCP_TOOL'),statement:'Independently reviewed both cases',cases:JSON.stringify(rows)});
const raw=buildExperienceReviewJSON(draft,'review','PROCESS_START',true);check(raw.includes('9007199254740993')&&raw.includes('"floating":1.0'));
const calls=[];let unauthorized=false;let captureStarts=0;globalThis.eeasDesktop={ensureEditorCaptureChannel:async()=>{captureStarts++;}};
const transport=async(path,init)=>{calls.push({path,init});if(path.endsWith('/bootstrap'))return new Response(JSON.stringify({csrf_token:'UNIT_CSRF'}));
if(path.endsWith('/requests/prepare')){const body=JSON.parse(init.body);check(body.request===undefined&&body.request_json===raw);const prefix={kind:'EXPERIENCE_REQUEST_PREPARATION',version:1,project_id:context.project_id,session_id:context.session_ref.id,session_ref:context.session_ref,action:'REVIEW',request_json:raw,request_sha256:S,read_only:true,metadata_writes:0,model_calls:0,processes_started:0,permission_granted:false,automatic_replay:false,authority_checked_on_actual_command:true};return new Response(JSON.stringify(prefix).slice(0,-1)+',"request":'+raw+'}');}
check(init.body===raw&&init.headers['x-eeas-csrf']==='UNIT_CSRF'&&init.credentials==='same-origin');return unauthorized?new Response('{"error":"UNIT_UNAUTHORIZED"}',{status:401}):new Response(JSON.stringify({operation_id:'review',request_sha256:S,record_ref:ref('review')}));};
const api=new ApiClient(transport),client=new TaskExperienceApi(api),packet=await client.prepare(context,'REVIEW',raw);check(packet.request_json===raw&&packet.request_sha256===S);
await client.dispatch(context,packet);check(calls.filter(c=>c.path.endsWith('/reviews')).length===1&&captureStarts===0);
unauthorized=true;await rejects(()=>client.dispatch(context,packet));check(calls.filter(c=>c.path.endsWith('/reviews')).length===2&&calls.filter(c=>c.path.endsWith('/bootstrap')).length===1);
const count=calls.length;await rejects(()=>api.exactExperienceJSON('/api/projects/project/agent-sessions/'+context.session_ref.id+'/messages',raw));check(calls.length===count);
const skillRows=['POSITIVE','NEGATIVE'].map(p=>({...newExperienceCase('SKILL',p),label:p,prompt:'Check exact literals',required_text:'  keep spaces  ',forbidden_text:'禁止😀'}));
const skill=experienceDraft(context,H,'REVIEW',ref('candidate'),null,{...reviewFields('SKILL'),statement:'Independent Skill review',cases:JSON.stringify(skillRows)});
const decoded=JSON.parse(buildExperienceReviewJSON(skill,'skill_review','PROCESS_START',true));check(decoded.cases[0].required_text[0]==='  keep spaces  '&&decoded.cases[0].forbidden_text[0]==='禁止😀');
await rejects(()=>buildExperienceReviewJSON(skill,'review','PROCESS_START',false));
console.log(JSON.stringify({status:'UNIT_PASS',exact_bigint_and_float:true,positive_401_retries:0,editor_capture_starts:captureStarts,literals_preserved:true}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
'''


def test_actual_editor_and_transport_keep_raw_json_no_unauthorized_replay(tmp_path):
    source=HARNESS
    for marker,file in [('__API__','api.ts'),('__CLIENT__','task-experience.ts'),('__EDITOR__','experience-editor.ts')]:source=source.replace(marker,str(ROOT/'apps/agent-shell/src'/file))
    entry=tmp_path/'wire.ts';bundle=tmp_path/'wire.cjs';entry.write_text(source)
    node=ROOT/'.node-tools/node_modules/node/bin/node';builder=tmp_path/'build.cjs'
    builder.write_text('require('+json.dumps(str(ROOT/'apps/agent-shell/node_modules/esbuild'))+').buildSync({entryPoints:['+json.dumps(str(entry))+'],bundle:true,platform:"node",format:"cjs",outfile:'+json.dumps(str(bundle))+'});')
    built=subprocess.run([str(node),str(builder)],cwd=ROOT,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
    result=subprocess.run([str(node),str(bundle)],cwd=ROOT,capture_output=True,text=True,timeout=30);assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['positive_401_retries']==0
