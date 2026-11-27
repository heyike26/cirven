"""Actual ProviderConnectionForm/Client feedback, isolated local DOM and adapters."""
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
STYLES = ['styles.css', 'agent-studio.css', 'brand-and-spacing.css', 'provider-layout.css']
COMPILER = r"""
import fs from'node:fs';import path from'node:path';import{createRequire}from'node:module';import{createHash}from'node:crypto';
const root=process.env.EEAS_REPO,app=path.join(root,'apps/agent-shell'),out=process.env.EEAS_EVIDENCE_DIR,baseline=process.env.EEAS_UI_BASELINE,req=createRequire(path.join(app,'package.json')),ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild'),preset=(await import(path.join(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const form=path.join(app,'src/ProviderConnectionForm.tsx'),source=baseline?path.join(baseline,'apps/agent-shell/src/ProviderConnectionForm.tsx'):form;
const program=`import{render}from'solid-js/web';import Form from'${form}';import{PROVIDER_CONNECTION_HISTORY_KEY}from'${app}/src/provider-connection.ts';import{startUILanguage}from'${app}/src/ui-language.ts';
startUILanguage();const ids=['provider-save-original-A','provider-save-original-B','provider-save-original-C','provider-save-original-D','provider-save-original-E'];const raw="Error invoking remote method 'eeas:user-content': KEEP_RAW_70 FileExistsError "+'原始诊断 long-original-detail '.repeat(1000)+' END_OF_RAW_70';const calls=[],bodies=[],outcomes=new Map();let held=false,release,posts=0;
const history=ids.map(operationId=>({operationId,profileId:'qa-provider',expectedRevision:0,providerUrl:'http://127.0.0.1:9/v1',modelId:'qa-model',authMode:'NONE',supportsImages:false,reasoningEfforts:[]}));localStorage.setItem(PROVIDER_CONNECTION_HISTORY_KEY,JSON.stringify(history));
const saved={profile_ref:{id:'qa-provider',revision:1,sha256:'a'.repeat(64)},provider_url:'http://127.0.0.1:9/v1',model_id:'qa-model',auth_kind:'NONE',credential_present:false,supports_images:false,model_access:'NOT_VALIDATED',reasoning_efforts:[],reasoning_qualification:'NOT_DECLARED'};
async function inspect(id){calls.push(id);if(held)await new Promise(r=>release=r);const mode=outcomes.get(id)??(id===ids[0]?'cli':id===ids[1]?'partial':id===ids[2]?'missing':id===ids[3]?'pending':'ipc');if(mode==='missing')throw Object.assign(Error(raw),{status:404,code:'NOT_FOUND'});if(mode==='ipc')throw Error(raw);return{operation_id:id,status:mode==='success'?'COMPLETED':mode==='pending'?'INCOMPLETE':'FAILED_OR_PARTIAL',result:mode==='success'?saved:null,failure:mode==='cli'?{phase:'CLI_INTEGRITY',code:'FILEEXISTSERROR'}:{phase:'PROFILE_COMMIT',code:'ORIGINAL_OTHER_PARTIAL'}};}
window.qa={ids,raw,state:()=>({calls:[...calls],posts,bodies:[...bodies],history:JSON.parse(localStorage.getItem(PROVIDER_CONNECTION_HISTORY_KEY))}),outcome:(id,mode)=>outcomes.set(id,mode),hold:()=>{held=true;},release:()=>{held=false;release?.();}};
render(()=> <div class="as-app-frame"><div class="as-shell"><textarea id="qa-draft" aria-label="QA unsent composer">Original unsent QA draft</textarea><div class="as-object as-provider"><h2>QA model connection</h2><Form storage={localStorage} onSave={async body=>{posts++;bodies.push(body);throw Error('No business save allowed in this fixture');}} onInspect={inspect}/></div></div></div>,document.getElementById('root'));`;
const used=new Set([source]),pin=p=>{const b=fs.readFileSync(p);return{path:p,bytes:b.length,sha256:createHash('sha256').update(b).digest('hex')};},plugin={name:'actual-provider-feedback',setup(b){b.onLoad({filter:/\.tsx$/},({path:p})=>{const actual=p===form?source:p;used.add(actual);return{contents:babel.transformSync(ts.transpileModule(fs.readFileSync(actual,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:p+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(p)};});}};
fs.mkdirSync(out,{recursive:true});const entry=path.join(out,'fixture.tsx');fs.writeFileSync(entry,program);try{const result=await esbuild.build({entryPoints:[entry],outfile:path.join(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[path.join(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'},metafile:true,logLevel:'silent'});for(const n of Object.keys(result.metafile.inputs))used.add(path.resolve(n));if(baseline)used.delete(form);fs.writeFileSync(path.join(out,'COMPILE_INPUTS.json'),JSON.stringify({form:pin(source),inputs:[...used].map(pin),fixture:pin(entry),bundle:pin(path.join(out,'fixture.js')),scope:'Actual Form/Client/Solid/ui-language. Controlled save-operation adapter; no server, CLI, model, fullApp or native.'},null,2)+'\n');}finally{esbuild.stop();}
"""
CLIP = r'''n=>{const r=n.getBoundingClientRect();let l=Math.max(0,r.left),t=Math.max(0,r.top),rr=Math.min(innerWidth,r.right),bb=Math.min(innerHeight,r.bottom),hidden=false;for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);if(p.hidden||p.inert||s.display==='none'||s.visibility==='hidden')hidden=true;if(p!==n){const b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){l=Math.max(l,b.left);rr=Math.min(rr,b.right);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){t=Math.max(t,b.top);bb=Math.min(bb,b.bottom);}}}return{width:r.width,height:r.height,visibleWidth:hidden?0:Math.max(0,rr-l),visibleHeight:hidden?0:Math.max(0,bb-t)};}'''


def test_provider_save_feedback_original_failure_reads_and_draft():
    asyncio.run(run())


async def run():
    out = evidence_folder('provider_save_feedback_source70')
    out.mkdir(parents=True, exist_ok=True)
    compiler = out/'compile-fixture.mjs'
    compiler.write_text(COMPILER)
    result = subprocess.run([str(NODE), str(compiler)], cwd=ROOT, env={**os.environ, 'EEAS_EVIDENCE_DIR':str(out), 'EEAS_REPO':str(ROOT), 'TMPDIR':str(out), 'NODE_COMPILE_CACHE':str(out/'node-cache')}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (out/'compile.stdout').write_bytes(result.stdout)
    (out/'compile.stderr').write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr.decode()
    observations=[]
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            try:
                for locale,width,height in [('zh-CN',360,600),('en',1060,700)]:
                    context=await browser.new_context(viewport={'width':width,'height':height},locale=locale)
                    page=await context.new_page();page.set_default_timeout(6000)
                    errors=[];outside=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    async def route(r):
                        path=urlparse(r.request.url).path
                        if r.request.url=='http://127.0.0.1:17770/':await r.fulfill(body='<!doctype html><html><body><div id="root"></div></body></html>',content_type='text/html')
                        elif path in ['/web/assets/fonts/manrope-variable.woff2','/web/assets/fonts/noto-sans-sc-variable.woff2']:await r.fulfill(body=(ROOT/'apps'/path.removeprefix('/')).read_bytes(),content_type='font/woff2')
                        else:outside.append(r.request.url);await r.abort()
                    await page.route('**/*',route)
                    await page.goto('http://127.0.0.1:17770/')
                    await page.evaluate('(locale)=>localStorage.setItem("eeas.ui-language.v1",locale)',locale)
                    for name in STYLES:await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
                    await page.add_style_tag(content='html,body{margin:0;height:100%;overflow:auto} .as-app-frame,.as-shell{display:block!important;width:100%!important;height:auto!important;min-height:100%;padding:0!important} .as-object.as-provider{box-sizing:border-box;padding:16px;max-width:760px}');await page.add_script_tag(path=str(out/'fixture.js'));await asyncio.wait_for(page.evaluate('()=>document.fonts.ready'),timeout=10)
                    await page.wait_for_function('()=>qa.state().calls.length===5&&!document.querySelector(".as-capability-pending")')
                    ids=await page.evaluate('()=>qa.ids')
                    cards=[page.locator(f'[data-provider-operation="{key}"]') for key in ids]
                    cli,other,missing,pending,ipc=cards
                    assert await cli.get_attribute('data-provider-save-status')=='FAILED_OR_PARTIAL'
                    await cli.scroll_into_view_if_needed()
                    initial=await cli.inner_text()
                    await page.screenshot(path=str(out/f'{locale}-collapsed.png'))
                    assert ids[0] not in initial and 'CLI_INTEGRITY' not in initial and 'FILEEXISTSERROR' not in initial, initial
                    assert ('本地运行组件准备失败' if locale=='zh-CN' else 'local runtime component could not be prepared') in initial
                    assert ('保存未完整完成' if locale=='zh-CN' else 'Saving did not fully complete') in await other.inner_text()
                    assert await missing.get_attribute('data-provider-save-status')=='NO_RECORD'
                    assert await pending.get_attribute('data-provider-save-status')=='INCOMPLETE'
                    assert await ipc.get_attribute('data-provider-save-status')=='INSPECTION_UNAVAILABLE'
                    assert 'Error invoking' not in await ipc.inner_text()
                    # Original operation controls use the real Client and keep the unsent form draft.
                    form=page.locator('.as-provider-connection');field=form.locator('input[aria-label="连接标识"],input[aria-label="Connection ID"]')
                    await field.fill('qa-provider');url=form.locator('input[type=url]');await url.fill('http://127.0.0.1:9/v1')
                    model=form.locator('input').nth(1);await model.fill('Unsent model draft 未保存模型')
                    await model.evaluate('(n)=>{window.originalModel=n;n.focus();n.setSelectionRange(2,12);}')
                    draft=await model.input_value();before_history=await page.evaluate('()=>qa.state().history')
                    await page.evaluate('()=>qa.state().calls.length')
                    for card,key in [(other,ids[1]),(cli,ids[0])]:
                        button=card.locator('.as-capability-actions button')
                        await card.locator('.as-provider-operation-details > summary').click()
                        await button.evaluate('(n)=>{window.originalRead=n;window.originalDetails=n.closest("article").querySelector(".as-provider-operation-details");}')
                        await page.evaluate('()=>qa.hold()')
                        before=await page.evaluate('()=>qa.state().calls.length')
                        await button.click();await page.keyboard.press('Enter')
                        assert await page.evaluate('()=>qa.state().calls.length')==before+1
                        assert await button.get_attribute('aria-disabled')=='true'
                        await page.evaluate('()=>qa.release()')
                        await page.wait_for_function('(count)=>qa.state().calls.length===count+1&&!document.querySelector(".as-capability-pending")',arg=before)
                        identity=await button.evaluate('(n)=>({same_button:n===window.originalRead,focused:document.activeElement===n,same_details:n.closest("article").querySelector(".as-provider-operation-details")===window.originalDetails,details_open:window.originalDetails.open,active:document.activeElement.tagName})')
                        observations.append({'phase':'original_read_identity','id':key,**identity})
                        assert all(identity[k] for k in ['same_button','focused','same_details','details_open']),identity
                        assert await card.get_attribute('data-provider-save-status')=='FAILED_OR_PARTIAL'
                        assert (await page.evaluate('()=>qa.state().calls'))[-1]==key
                    assert await model.evaluate('(n)=>n===window.originalModel&&n.selectionStart===2&&n.selectionEnd===12')
                    assert await model.input_value()==draft
                    # Controlled diagnostic text remains exact and bounded behind details.
                    await ipc.locator('.as-provider-operation-details > summary').click()
                    pre=ipc.locator('pre');assert await page.evaluate('()=>qa.raw') in await pre.inner_text()
                    await pre.evaluate('(n)=>n.scrollTop=n.scrollHeight');await pre.evaluate('(n)=>n.scrollIntoView({block:"center",inline:"nearest",behavior:"instant"})');await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');box=await pre.evaluate(CLIP)
                    assert box['height']<=240.1 and box['visibleHeight']>=box['height']-1 and box['visibleWidth']>=box['width']-1,box
                    terminal=await pre.evaluate('n=>{const t=n.firstChild,start=t.textContent.lastIndexOf("END_OF_RAW_70"),r=document.createRange();r.setStart(t,start);r.setEnd(t,start+13);const a=r.getBoundingClientRect(),b=n.getBoundingClientRect();return{top:a.top,bottom:a.bottom,preTop:b.top,preBottom:b.bottom};}')
                    assert terminal['top']>=terminal['preTop']-1 and terminal['bottom']<=terminal['preBottom']+1,terminal
                    await page.screenshot(path=str(out/f'{locale}-details-end.png'))
                    assert ids[4] in await ipc.inner_text()
                    await ipc.locator('.as-provider-operation-details > summary').click()
                    # Missing/unknown stays uncertain on exact GET. A completed receipt needs its real exact profile.
                    before=await page.evaluate('()=>qa.state().calls.length')
                    await page.evaluate('()=>qa.hold()');await missing.locator('.as-capability-actions button').click()
                    host_draft=page.locator('#qa-draft');await host_draft.evaluate('(n)=>{window.originalHostDraft=n;n.focus();n.setSelectionRange(3,13);}')
                    await host_draft.type('保留 typed');expected_host_draft=await host_draft.input_value()
                    await page.evaluate('()=>qa.release()')
                    await page.wait_for_function('(count)=>qa.state().calls.length===count+1&&!document.querySelector(".as-capability-pending")',arg=before)
                    assert await host_draft.evaluate('(n)=>n===window.originalHostDraft&&document.activeElement===n') and await host_draft.input_value()==expected_host_draft
                    assert await model.evaluate('(n)=>n===window.originalModel&&n.selectionStart===2&&n.selectionEnd===12')
                    assert await missing.get_attribute('data-provider-save-status')=='NO_RECORD'
                    await page.evaluate('(id)=>qa.outcome(id,"success")',ids[0]);before=await page.evaluate('()=>qa.state().calls.length')
                    await cli.locator('.as-capability-actions button').click();await page.wait_for_function('(count)=>qa.state().calls.length===count+1&&!document.querySelector(".as-capability-pending")',arg=before)
                    assert await cli.get_attribute('data-provider-save-status')=='COMPLETED'
                    assert ('尚未验证模型连接' if locale=='zh-CN' else 'not verified') in await cli.inner_text()
                    state=await page.evaluate('()=>qa.state()')
                    assert state['posts']==0 and state['bodies']==[] and state['history']==before_history
                    assert state['calls']==ids+[ids[1],ids[0],ids[2],ids[0]],state
                    assert await model.evaluate('(n)=>n===window.originalModel') and await model.input_value()==draft
                    assert not errors and not outside,(errors,outside)
                    observations.append({'locale':locale,'viewport':[width,height],'default_diagnostics_folded':True,'partial_not_promoted_by_current_files':True,'missing_and_unavailable_remain_uncertain':True,'exact_original_GETs':state['calls'],'business_saves':state['posts'],'same_draft_DOM_selection':True,'late_read_retained_input_focus':True,'same_receipt_details_button_and_query_focus':True,'busy_extra_click_did_not_repeat_GET':True,'raw_detail_verbatim':True,'detail_clip':box,'detail_terminal':terminal,'original_history_unchanged':True,'completed_exact_profile_does_not_validate_model':True})
                    await context.close()
            finally:await browser.close()
    finally:(out/'OBSERVATIONS.json').write_text(json.dumps({'observations':observations,'scope':'Isolated real production Form+Client, controlled operation adapter. No real API/CLI/model/fullApp/native/OS IME qualification.'},ensure_ascii=False,indent=2)+'\n')
