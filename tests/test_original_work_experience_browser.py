"""AUTHORED_NOT_RUN: real Solid UI, journal and clients; UNIT fetch only.

No Store, MCP process, model, native Windows or visual qualification. The held
validation response models a network boundary; it is never a real Tool call.
"""
from pathlib import Path
import json,os,socket,subprocess,time,urllib.request
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
import {render} from 'solid-js/web';
import Work from '__SRC__/OriginalWorkExperience.tsx';
import {api} from '__SRC__/api.ts';
const H='a'.repeat(64),S='b'.repeat(64),ref=id=>({id,revision:1,sha256:H}),owner={project_id:'project',session_ref:ref('e'.repeat(32)),generation:1,workspace_ref:{id:H,revision:1,binding_sha256:H}};
const candidate=ref('candidate'),review=ref('review'),calls=[],packets=new Map();let withdrawn=false,release;
const policy=()=>({availability:withdrawn?'WITHDRAWN':'ACTIVE',revision:withdrawn?1:0,forgotten:false});
const cases=['POSITIVE','NEGATIVE'].map(p=>({kind:'MCP_TOOL',id:p,label:p,purpose:p,tool_name:'inspect',input:{},expected_result:{content:[],structuredContent:null,isError:p==='NEGATIVE'}}));
const reviewed={operation_id:'review',request_sha256:S,kind:'REVIEW',record_ref:review,candidate_ref:candidate,request:{cases,statement:'Independent UNIT review',allowed_effects:['REMOTE_MCP_CONNECT'],max_model_calls:0,max_output_tokens:2048,timeout_seconds:60}};
const candidateValue=()=>({kind:'PROPOSAL',record_ref:candidate,root_operation_id:'work',root_sha256:H,source_policy:policy(),...withdrawn?{content_withheld:true}:{request:{title:'Original evidence 😀',observation:'Keep original evidence',proposed_use:'Use a fixed independently reviewed case',limitations:'Only the fixed case',proposal:{kind:'MCP_TOOL',id:'inspect',configuration:{type:'remote',url:'https://fixture.invalid/mcp',enabled:false,timeout:60000}}},configuration:{type:'remote'}}});
const response=v=>new Response(JSON.stringify(v),{headers:{'content-type':'application/json'}});
window.fetch=async(input,init={})=>{const url=new URL(String(input),location.origin),path=url.pathname;calls.push({path,method:init.method??'GET',body:init.body?JSON.parse(init.body):null});
if(path==='/api/bootstrap')return response({csrf_token:'UNIT_CSRF'});
if(path.endsWith('/requests/prepare')){const b=JSON.parse(init.body),request=b.request_json?JSON.parse(b.request_json):b.request;packets.set(request.operation_id,{action:b.action,request});return response({kind:'EXPERIENCE_REQUEST_PREPARATION',version:1,project_id:owner.project_id,session_id:owner.session_ref.id,session_ref:owner.session_ref,action:b.action,request,request_json:JSON.stringify(request),request_sha256:S,read_only:true,metadata_writes:0,model_calls:0,processes_started:0,permission_granted:false,automatic_replay:false,authority_checked_on_actual_command:true});}
if(path.endsWith('/candidates'))return response({schema:'eeas.task-experience-candidate.v1',project_id:owner.project_id,session_id:owner.session_ref.id,entries:[{candidate_ref:candidate,title:'Original evidence 😀',kind:'MCP_TOOL',root_operation_id:'work',root_sha256:H,source_policy:policy()}],offset:0,next_offset:null,total:1,read_only:true,model_calls:0,commands_started:0,configuration_registered:false,automatic_replay:false});
if(path.endsWith('/candidates/candidate'))return response(candidateValue());
if(path.endsWith('/history'))return response({kind:'EXPERIENCE_CANDIDATE_HISTORY',project_id:owner.project_id,session_id:owner.session_ref.id,candidate_ref:candidate,entries:[{kind:'REVIEW',record_ref:review,candidate_ref:candidate,request_sha256:S}],offset:0,next_offset:null,index_sha256:H,legacy_body_reads:0,read_only:true,model_calls:0,metadata_writes:0});
if(path.endsWith('/reviews/review'))return response(reviewed);
if(path.endsWith('/tool-validations')&&init.method==='POST'){const b=JSON.parse(init.body);return new Promise(resolve=>{release=()=>resolve(response({kind:'EXPERIENCE_TOOL_VALIDATION',operation_id:b.operation_id,request_sha256:S,candidate_ref:candidate,review_ref:review,status:'SOURCE_BLOCKED',execution_status:'UNKNOWN',qualified:false,result_sha256:H,content_withheld:true,source_policy:policy()}));});}
if(path.endsWith('/source-policy/candidate'))return response({project_id:owner.project_id,session_id:owner.session_ref.id,candidate_ref:candidate,...policy()});
if(path.endsWith('/source-policy')&&init.method==='POST'){const b=JSON.parse(init.body);withdrawn=true;return response({decision:{operation_id:b.operation_id,request_sha256:S},automatic_replay:false});}
if(path.includes('/source-policy/candidate/operations/')){const id=path.split('/').pop();return response({kind:'EXPERIENCE_SOURCE_DECISION_OBSERVATION',operation_id:id,request_sha256:S,candidate_ref:candidate,decision_ref:ref(id),decision_state:'WITHDRAWN',current:policy(),local_finalization_needed:false,read_only:true});}
throw Error('Unexpected UNIT route '+path);};
Object.assign(api,{transport:window.fetch});
window.qa={calls,release:()=>release(),journal:()=>Object.entries(localStorage).filter(([k])=>k.includes('experience')).map(([k,v])=>JSON.parse(v)),owner};
render(()=><Work context={owner} root="work" rootSha={H} locale="zh-CN" initialCandidate={candidate}/>,document.getElementById('app'));
'''


def test_withdraw_source_remains_available_during_held_validation_and_consent_is_distinct(tmp_path):
    from playwright.sync_api import sync_playwright,expect
    app=ROOT/'apps/agent-shell';node=ROOT/'.node-tools/node_modules/node/bin/node';fixture=tmp_path/'browser';fixture.mkdir()
    (fixture/'index.html').write_text('<!doctype html><html><meta charset="utf-8"><body><div id="app"></div><script type="module" src="/fixture.tsx"></script></body></html>')
    (fixture/'fixture.tsx').write_text(FIXTURE.replace('__SRC__','/@fs/'+str(app/'src')))
    config=fixture/'vite.config.mjs';config.write_text('import solid from '+json.dumps(str(app/'node_modules/vite-plugin-solid/dist/esm/index.mjs'))+';export default '+json.dumps({'root':str(fixture),'cacheDir':str(fixture/'vite-cache'),'resolve':{'dedupe':['solid-js'],'alias':{'solid-js':str(app/'node_modules/solid-js')}},'server':{'hmr':False,'host':'127.0.0.1','strictPort':True,'fs':{'allow':[str(ROOT),str(tmp_path)]}}}).replace('"resolve":','"plugins":[solid()],"resolve":')+';')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,TMPDIR=str(tmp_path),TMP=str(tmp_path),TEMP=str(tmp_path),NODE_COMPILE_CACHE=str(tmp_path/'node-cache'))
    with (tmp_path/'vite.log').open('w') as log:
        process=subprocess.Popen([str(node),str(app/'node_modules/vite/bin/vite.js'),'--config',str(config),'--port',str(port)],cwd=fixture,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+30
            while True:
                if process.poll() is not None:raise RuntimeError((tmp_path/'vite.log').read_text())
                try:
                    with urllib.request.urlopen('http://127.0.0.1:'+str(port),timeout=1):break
                except OSError:
                    if time.monotonic()>=deadline:raise
                    time.sleep(.1)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                try:
                    page=browser.new_page(viewport={'width':520,'height':860});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto('http://127.0.0.1:'+str(port),wait_until='domcontentloaded',timeout=90000)
                    page.get_by_role('heading',name='Original evidence 😀',exact=True).wait_for(timeout=90000)
                    assert page.evaluate("qa.calls.every(c=>c.method==='GET')")
                    page.get_by_text('原审阅、验证与配置记录',exact=True).click();page.get_by_role('button',name='用这份审阅进行验证',exact=True).click()
                    confirm=page.get_by_role('button',name='确认运行固定验证',exact=True);expect(confirm).to_be_disabled()
                    page.get_by_label('确认启动这组固定验证及已审阅的进程或连接。',exact=True).check();confirm.click()
                    page.wait_for_function("qa.calls.some(c=>c.path.endsWith('/tool-validations')&&c.method==='POST')")
                    page.get_by_text('来源控制',exact=True).click();page.get_by_role('button',name='撤回未来使用',exact=True).click()
                    decide=page.get_by_role('button',name='确认记录这次决定',exact=True);expect(decide).to_be_disabled()
                    page.get_by_label('独立核对或来源决定说明',exact=True).fill('撤回来源，保留原人工稿 😀')
                    page.get_by_label('确认这次来源决定。',exact=True).check();decide.click()
                    page.get_by_text('此来源已撤回、忘记或不可用；不再提供其内容用于验证或启用。',exact=True).wait_for()
                    page.evaluate('qa.release()');page.wait_for_function("qa.journal().some(j=>j.attempts?.filter(a=>a.phase==='OBSERVED').length===2)")
                    assert page.evaluate("qa.calls.filter(c=>c.path.endsWith('/tool-validations')&&c.method==='POST').length") == 1
                    assert page.evaluate("qa.calls.filter(c=>c.path.endsWith('/source-policy')&&c.method==='POST').length") == 1
                    assert page.evaluate("qa.journal().some(j=>j.attempts?.some(a=>a.packet.action==='TOOL_VALIDATE'&&a.summary.status==='UNKNOWN'))")
                    assert not errors,errors
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(tmp_path/'withdraw-during-validation.png'),full_page=True)
                finally:browser.close()
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
