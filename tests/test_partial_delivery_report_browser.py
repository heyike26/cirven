"""Actual delivery components; isolated action boundary, not Windows opening."""
import asyncio
import json
import os
from legacy_evidence import evidence_folder, chromium_executable
from pathlib import Path
import subprocess
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'
CHROMIUM = chromium_executable()
STYLES = ['native-composer.css', 'styles.css', 'native-collaboration.css', 'agent-studio.css', 'brand-and-spacing.css', 'chat-workbench-layout.css']
COMPILER = r"""
import fs from 'node:fs';import path from 'node:path';import{createRequire}from'node:module';import{createHash}from'node:crypto';
const root=process.env.EEAS_REPO,app=path.join(root,'apps/agent-shell'),out=process.env.EEAS_EVIDENCE_DIR,req=createRequire(path.join(app,'package.json')),ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild'),preset=(await import(path.join(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const program=`import{render}from'solid-js/web';import{createSignal,onCleanup}from'solid-js';import PlanningDeliveryResult from'${app}/src/PlanningDeliveryResult.tsx';import{startUILanguage}from'${app}/src/ui-language.ts';
function Fixture(){onCleanup(startUILanguage());const calls=[],deferred=[];const sha='b'.repeat(64),id='a'.repeat(64),basis={id:'original-plan',revision:5,sha256:'c'.repeat(64)};
 const partial=(project='report-project',operation='original-operation')=>({operation_id:operation,status:'FAILED_OR_PARTIAL',kind:'EXPORT',automatic_model_replay:false,steps:{report:{source_kind:'PROPOSAL',status:'CONFIRMED',proposal_ref:{...basis},artifact:{id,sha256:sha,filename:'完整任务书与原始用户文件名_'+ 'Task-任务-'.repeat(12)+'.docx',byte_length:277597,media_type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',download_url:'/api/projects/'+project+'/planning/proposals/'+basis.id+'/reports/'+id+'/download'},pending_json:{download_url:'/internal-raw-source.json'}}},failures:[{reason:'ManualChangesConflict'}]});
 const [state,setState]=createSignal({project:'report-project',session:'session-a',value:partial()});
 const artifact=()=>({artifactId:state().value.operation_id,type:'planning-delivery',schemaVersion:1,revision:5,scope:{projectId:state().project,workspaceId:'workspace-a',sessionId:state().session,runId:'run-a'},availability:{level:'available'},data:state().value});
 const context={dispatch:(action,target,input)=>{calls.push({action,target:structuredClone(target),input:structuredClone(input)});return new Promise((resolve,reject)=>deferred.push({resolve,reject}));}};
 window.qa={state:()=>({calls:structuredClone(calls),value:structuredClone(state().value)}),settle:(index,error)=>error?deferred[index].reject(new Error(error)):deferred[index].resolve({status:'OPEN_REQUESTED'}),switch:()=>setState({project:'other-project',session:'session-b',value:partial('other-project','other-operation')}),scope:session=>setState(s=>({...s,session})),reset:()=>setState({project:'report-project',session:'session-a',value:partial()}),malformed:()=>setState(s=>({...s,value:{...s.value,steps:{report:{...s.value.steps.report,artifact:{...s.value.steps.report.artifact,download_url:'/other-project/raw.json'}}}}})),complete:()=>setState({project:'report-project',session:'session-a',value:{operation_id:'complete-operation',status:'STAGE_REPORT',session_ref:basis,pending_count:2,blocking_count:1,report:{artifact:{...partial().steps.report.artifact,filename:'stage-report.docx'}},diagrams:[],artifacts:[],project_files:[],file_checks:[],basis_current:true,current_review_status:'CURRENT',execution_authorized:false,historical_receipt_preserved:true}})};
 return <main class="as-app-frame"><div class="as-object" style={{width:'100%', 'max-width':'760px',padding:'16px','margin-inline':'auto'}}><PlanningDeliveryResult value={state().value} artifact={artifact()} context={context}/><label>Unsent draft<textarea id="draft" style={{width:'100%'}} value="用户尚未发送的反馈"/></label></div></main>;
}render(Fixture,document.getElementById('root'));`;
fs.mkdirSync(out,{recursive:true});const entry=path.join(out,'fixture.tsx');fs.writeFileSync(entry,program);
const used=new Set(),pin=p=>{const raw=fs.readFileSync(p);return{path:p,bytes:raw.length,sha256:createHash('sha256').update(raw).digest('hex')};};
const plugin={name:'actual-partial-report-components',setup(b){b.onLoad({filter:/\.tsx$/},({path:p})=>{used.add(p);return{contents:babel.transformSync(ts.transpileModule(fs.readFileSync(p,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:p+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(p)};});}};
try{const result=await esbuild.build({entryPoints:[entry],outfile:path.join(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[path.join(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},alias:{'@opencode-ai/ui/icon':path.join(root,'staging-native-ui/upstream/packages/ui/src/components/icon.tsx')},define:{'process.env.NODE_ENV':'"production"'},metafile:true,logLevel:'silent'});for(const n of Object.keys(result.metafile.inputs))used.add(path.resolve(n));fs.writeFileSync(path.join(out,'COMPILE_INPUTS.json'),JSON.stringify({inputs:[...used].map(pin),bundle:pin(path.join(out,'fixture.js')),boundary:'Actual PlanningDeliveryResult, DeliveryArtifactCard, report guard, localization and CSS; controlled dispatch only. No App/API/Windows/model.'},null,2)+'\n');}finally{esbuild.stop();}
"""
CLIP = r'''n=>{const r=n.getBoundingClientRect();let l=Math.max(0,r.left),t=Math.max(0,r.top),rr=Math.min(innerWidth,r.right),bb=Math.min(innerHeight,r.bottom);for(let p=n.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){l=Math.max(l,b.left);rr=Math.min(rr,b.right);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){t=Math.max(t,b.top);bb=Math.min(bb,b.bottom);}}const hit=document.elementFromPoint((l+rr)/2,(t+bb)/2);return{width:r.width,height:r.height,visibleWidth:rr-l,visibleHeight:bb-t,hit:hit===n||n.contains(hit)};}'''


def test_partial_report_open_scope_and_late_callbacks():
    asyncio.run(run())


async def run():
    out = evidence_folder('partial_delivery_report_browser')
    out.mkdir(parents=True, exist_ok=True)
    compiler = out / 'compile-fixture.mjs'
    compiler.write_text(COMPILER)
    proc = subprocess.run([str(NODE), str(compiler)], cwd=ROOT, env={**os.environ, 'EEAS_EVIDENCE_DIR':str(out), 'EEAS_REPO':str(ROOT), 'TMPDIR':str(out), 'NODE_COMPILE_CACHE':str(out/'node-cache')}, text=True, capture_output=True)
    (out/'compile.stdout').write_text(proc.stdout);(out/'compile.stderr').write_text(proc.stderr)
    assert proc.returncode == 0, proc.stderr
    observations=[]
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,executable_path=str(CHROMIUM))
            try:
                for locale,width,height in [('zh-CN',360,760),('en',780,650)]:
                    context=await browser.new_context(viewport={'width':width,'height':height},locale=locale)
                    page=await context.new_page();page.set_default_timeout(5000)
                    errors=[];outside=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    async def route(r):
                        path=urlparse(r.request.url).path
                        if r.request.url=='http://127.0.0.1:17634/':await r.fulfill(body='<!doctype html><html><body><div id="root"></div></body></html>',content_type='text/html')
                        elif path in ['/web/assets/fonts/manrope-variable.woff2','/web/assets/fonts/noto-sans-sc-variable.woff2']:await r.fulfill(body=(ROOT/'apps'/path.removeprefix('/')).read_bytes(),content_type='font/woff2')
                        else:outside.append(r.request.url);await r.abort()
                    await page.route('**/*',route);await page.goto('http://127.0.0.1:17634/')
                    await page.evaluate('(l)=>localStorage.setItem("eeas.ui-language.v1",l)',locale)
                    for name in STYLES:await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
                    await page.add_script_tag(path=str(out/'fixture.js'));await page.evaluate('()=>document.fonts.ready')
                    async def flush():await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    await flush();button=page.locator('.as-chat-artifact');await button.wait_for(state='visible')
                    assert await button.count()==1 and await page.locator('details[open],pre').count()==0
                    text=await page.locator('.as-delivery-result').inner_text()
                    assert 'ManualChangesConflict' not in text and 'internal-raw-source' not in text and 'a'*64 not in text
                    assert ('报告已生成' if locale=='zh-CN' else 'Report generated') in await button.inner_text()
                    await button.scroll_into_view_if_needed();clip=await button.evaluate(CLIP)
                    assert clip['hit'] and clip['visibleWidth']>=clip['width']-1 and clip['visibleHeight']>=clip['height']-1,clip
                    assert await page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth'), 'horizontal overflow'
                    await page.screenshot(path=str(out/f'{locale}-partial-report.png'))
                    await button.click();await button.evaluate('(n)=>n.click()');assert len((await page.evaluate('()=>qa.state()'))['calls'])==1
                    call=(await page.evaluate('()=>qa.state()'))['calls'][0]
                    assert call['action']=='delivery.report.open' and call['target']['scope']['projectId']=='report-project'
                    assert call['target']['artifactId']=='original-operation' and list(call['input'])==['report']
                    assert call['input']['report']['artifact']['sha256']=='b'*64
                    await page.evaluate('()=>qa.switch()');await flush()
                    observations.append({'phase':'scope_changed_while_open_pending','locale':locale,'new_card_enabled':await button.is_enabled()})
                    assert await button.is_enabled(), 'old pending action disabled the new project report'
                    await button.click();await page.evaluate('()=>qa.settle(0,"late error from original project")');await flush()
                    assert await button.is_disabled() and await page.locator('[role=alert]').count()==0
                    field=page.locator('#draft');await field.fill('用户新反馈保持');await field.focus()
                    await page.evaluate('()=>qa.settle(1,"current report open failed")');await flush()
                    assert await page.locator('[role=alert]').inner_text()=='current report open failed'
                    assert await field.input_value()=='用户新反馈保持' and await field.evaluate('(n)=>document.activeElement===n')
                    await button.click();await page.evaluate('()=>qa.settle(2)');await flush()
                    assert await button.is_enabled() and await page.locator('[role=alert]').count()==0
                    await page.evaluate('()=>qa.reset()');await flush();await button.click()
                    await page.evaluate('()=>qa.scope("session-b")');await flush()
                    assert await button.is_enabled(), 'same-project session switch inherited the original pending action'
                    await button.click();await page.evaluate('()=>qa.settle(3,"late error from original session")');await flush()
                    assert await button.is_disabled() and await page.locator('[role=alert]').count()==0
                    await page.evaluate('()=>qa.settle(4)');await flush()
                    await page.evaluate('()=>qa.malformed()');await flush();assert await button.count()==0
                    await page.evaluate('()=>qa.complete()');await flush();assert await button.count()==1
                    await button.click();call=(await page.evaluate('()=>qa.state()'))['calls'][-1]
                    assert call['action']=='delivery.report.open' and list(call['input'])==['receipt']
                    assert call['input']['receipt']['status']=='STAGE_REPORT'
                    await page.evaluate('()=>qa.settle(5)');await flush()
                    assert not errors and not outside,(errors,outside)
                    state=await page.evaluate('()=>qa.state()')
                    observations.append({'phase':'all_cases_pass','locale':locale,'viewport':[width,height],'clip':clip,'action_count':len(state['calls']),'only_existing_open_action':all(c['action']=='delivery.report.open' for c in state['calls']),'old_callbacks_ignored':True,'retry_explicit':True,'same_draft_and_focus':True,'page_errors':errors,'external_requests':outside})
                    await context.close()
            finally:await browser.close()
    finally:(out/'OBSERVATIONS.json').write_text(json.dumps({'qualification':'ACTUAL_COMPONENT_BROWSER_WITH_CONTROLLED_DISPATCH_NOT_NATIVE_OPEN','observations':observations},ensure_ascii=False,indent=2)+'\n')
