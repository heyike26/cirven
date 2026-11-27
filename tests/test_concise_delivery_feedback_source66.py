"""Actual App feed/feedback/textarea + real recovery/GET adapter in isolated Chromium."""
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
import fs from'node:fs';import path from'node:path';import{createRequire}from'node:module';import{createHash}from'node:crypto';
const root=process.env.EEAS_REPO,app=path.join(root,'apps/agent-shell'),out=process.env.EEAS_EVIDENCE_DIR,baseline=process.env.EEAS_UI_BASELINE,variant=process.env.EEAS_UI_VARIANT??'after',req=createRequire(path.join(app,'package.json')),ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild'),preset=(await import(path.join(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const sourcePath=path.join(variant==='before'?baseline:root,'apps/agent-shell/src/App.tsx'),source=fs.readFileSync(sourcePath,'utf8'),tree=ts.createSourceFile(sourcePath,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),nodes=[];function walk(n){nodes.push(n);ts.forEachChild(n,walk);}walk(tree);
const fn=name=>nodes.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name)?.getText(tree)??'';
const feed=nodes.find(n=>ts.isJsxElement(n)&&n.openingElement.getText(tree)==='<div class="as-transcript" aria-live="polite">').getText(tree);
const area=nodes.find(n=>ts.isJsxSelfClosingElement(n)&&n.tagName.getText(tree)==='textarea'&&n.getText(tree).includes('描述要完成的工作')).getText(tree);
const program=`import{render}from'solid-js/web';import{createSignal,createMemo,Show,For,onCleanup}from'solid-js';import{Icon}from'${root}/staging-native-ui/upstream/packages/ui/src/components/icon.tsx';import DeliveryHistoryFeed from'${app}/src/DeliveryHistoryFeed.tsx';import{sameNativeReference}from'${app}/src/native-collaboration.ts';import TaskStatus from'${app}/src/TaskStatus.tsx';import DeliveryArtifactCard from'${app}/src/DeliveryArtifactCard.tsx';import NativePageConversation from'${app}/src/NativePageConversation.tsx';import{authoredText}from'${app}/src/authored-ui-copy.ts';import{startUILanguage,t as uiText}from'${app}/src/ui-language.ts';import{ApiError,ApiClient}from'${app}/src/api.ts';import{PlanningDeliveryApi,deliveryComplete}from'${app}/src/planning-delivery.ts';import{unavailableDeliveryItem,DeliveryHistoryRecovery,upsertDeliveryFeed}from'${app}/src/delivery-recovery.ts';
${fn('DeliveryFeedFeedback')}
function Fixture(){startUILanguage();const deliveryDirectories=()=>({}),nativeBridge=undefined,activeSession=()=>undefined;let scopeEpoch=0,activePid='qa-project',posts=0,bootstrapCalls=0,heldResolve;const calls=[],opens=[];let restoreKeys=[];
 const keyA='071c977c-15ed-4f2b-8716-8787380ff305',keyB='b02fe7ec-4d87-4153-b916-a9ecb1aa7880',target={projectId:'qa-project',kind:'EXPORT',basis:{id:'original-proposal',revision:4,sha256:'a'.repeat(64)}},raw='RAW_BACKEND_66: Planning delivery operation does not exist. '+ '中文长诊断 LONG_DETAIL '.repeat(1000)+' END_OF_ORIGINAL_DETAIL_66';
 const savedSource={kind:'local_file',project_id:'qa-project',workspace_id:'qa-workspace',binding_revision:1,binding_sha256:'b'.repeat(64),path:'output/qa66/original-delivery.docx',revision:1,sha256:'c'.repeat(64)},file={path:savedSource.path,sha256:savedSource.sha256,file_source_ref:savedSource};
 const successReceipt=key=>({operation_id:key,status:'EXPORTED_CONFIRMED_PLAN_DERIVATIVES',project_id:'qa-project',proposal_ref:target.basis,report:{artifact:{id:'qa-word',filename:'original-delivery.docx',sha256:savedSource.sha256}},diagrams:[],artifacts:[],project_files:[file],file_checks:[{path:file.path,status:'CURRENT'}],basis_current:true,current_review_status:'CURRENT',execution_authorized:false,historical_receipt_preserved:true,version_directory:'output/qa66',manifest:{path:'output/qa66/manifest.json',sha256:'d'.repeat(64)},taskbook_ref:{taskbook_id:'qa-taskbook',revision:1},harness:{workspace_ref:{id:'qa-workspace',revision:1,binding_sha256:savedSource.binding_sha256},version_directory:'output/qa66',files:[file]}});
 const context={dispatch:async(action,artifact,input)=>{opens.push({action,operation_id:artifact.artifactId,file:input.file?.file_source_ref});return{status:'QA_READONLY_ACTION_OBSERVED_NOT_NATIVE_OPEN'};}};
 const [feed,setFeed]=createSignal([unavailableDeliveryItem(target,keyA,new ApiError(404,'NOT_FOUND',raw)),unavailableDeliveryItem(target,keyB,new ApiError(503,'UNAVAILABLE',raw+' SECOND'))]);
 const feedById=createMemo(()=>new Map(feed().map(item=>[item.id,item])));
 const [prompt,setPrompt]=createSignal('未发送用户原文 Error invoking remote method eeas:keep-me 中文');
 const project=()=>({id:activePid}),navigation={capture:()=>{const captured=scopeEpoch;return()=>captured===scopeEpoch;}},client=new ApiClient(async(url,options)=>{if(String(url)==='/api/bootstrap'){bootstrapCalls++;return new Response(JSON.stringify({csrf_token:'qa-fixture-only'}),{status:200,headers:{'Content-Type':'application/json'}});}calls.push({url:String(url),method:options.method??'GET'});if(options.method&&options.method!=='GET')posts++;if(window.qaHeld)await new Promise(resolve=>heldResolve=resolve);if(window.qaSuccess)return new Response(JSON.stringify(successReceipt(String(url).split('/').at(-1))),{status:200,headers:{'Content-Type':'application/json'}});return new Response(JSON.stringify({error:{code:'NOT_FOUND',message:raw}}),{status:404,headers:{'Content-Type':'application/json'}});}),deliveryRecovery=new DeliveryHistoryRecovery(new PlanningDeliveryApi(client),{getItem:key=>key==='eeas-delivery-history:qa-project:EXPORT:original-proposal'?JSON.stringify(restoreKeys):null},item=>setFeed(items=>upsertDeliveryFeed(items,item)));
 ${fn('inspectDeliveryFeedback')}
 const windowMode=()=>'workbench',composerView=()=>({focused:true,folded:false}),composerPresentation={focus(){}},submitPrompt=async()=>{posts++;};
 const authoredCopyText=(x)=>authoredText(x,uiText),inspect=()=>{},Button=(props)=><button>{props.children}</button>;
 window.qa={state:()=>({calls:[...calls],opens:[...opens],bootstrapCalls,posts,scopeEpoch,activePid,statuses:feed().map(item=>item.status),keys:feed().map(item=>item.deliveryInspection?.operationId),draft:prompt()}),hold:()=>{window.qaHeld=true;},release:()=>{window.qaHeld=false;heldResolve?.();},scope:()=>{scopeEpoch++;activePid='other-project';},raw,keyA,keyB,succeed:()=>{window.qaSuccess=true;},restoreBackground:()=>{restoreKeys=[keyA];return deliveryRecovery.restore([target]);}};
 return <div class="as-app-frame"><div class="as-shell" data-layout="workbench" data-chat-layout="adaptive" data-window-mode="workbench" data-fullscreen="false" data-agent-hidden="false" data-workbench-hidden="true" data-files-hidden="true" data-mobile-view="agent" data-compact={String(window.matchMedia('(max-width:920px)').matches)}><aside class="as-sidebar"/><div class="as-pane-separator" data-kind="files"/><main class="as-workbench"/><div class="as-pane-separator" data-kind="agent"/><aside class="as-agent"><div class="as-agent-content"><header class="as-agent-header"><strong>QA · Conversation</strong></header><main class="as-conversation">${feed}</main><form class="as-composer" onSubmit={e=>{e.preventDefault();posts++;}}><NativePageConversation input={${area}} toolbar={<button type="submit">QA send</button>}/></form></div></aside></div></div>;
}render(Fixture,document.getElementById('root'));`;
const used=new Set([sourcePath]),pin=p=>{const raw=fs.readFileSync(p);return{path:p,bytes:raw.length,sha256:createHash('sha256').update(raw).digest('hex')};};
const pinnedApi=process.env.EEAS_PINNED_API_SOURCE;const plugin={name:'actual-source66-feed',setup(b){if(pinnedApi)b.onLoad({filter:/api\.ts$/},({path:p})=>{if(p!==path.join(app,'src/api.ts'))return;used.add(pinnedApi);return{contents:fs.readFileSync(pinnedApi,'utf8'),loader:'ts',resolveDir:path.dirname(p)};});b.onLoad({filter:/\.tsx$/},({path:p})=>{used.add(p);return{contents:babel.transformSync(ts.transpileModule(fs.readFileSync(p,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:p+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(p)};});if(variant==='before')b.onLoad({filter:/delivery-recovery\.ts$/},({path:p})=>{const original=path.join(baseline,'apps/agent-shell/src/delivery-recovery.ts');used.add(original);return{contents:fs.readFileSync(original,'utf8'),loader:'ts',resolveDir:path.dirname(p)};});}};
fs.mkdirSync(out,{recursive:true});const entry=path.join(out,'fixture.tsx');fs.writeFileSync(entry,program);try{const result=await esbuild.build({entryPoints:[entry],outfile:path.join(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[path.join(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},alias:{'@opencode-ai/ui/icon':path.join(root,'staging-native-ui/upstream/packages/ui/src/components/icon.tsx')},define:{'process.env.NODE_ENV':'"production"'},metafile:true,logLevel:'silent'});for(const n of Object.keys(result.metafile.inputs)){const input=path.resolve(n);used.add(pinnedApi&&input===path.join(app,'src/api.ts')?pinnedApi:input);}fs.writeFileSync(path.join(out,'COMPILE_INPUTS.json'),JSON.stringify({variant,source:pin(sourcePath),inputs:[...used].map(pin),fixture:pin(entry),bundle:pin(path.join(out,'fixture.js')),boundary:'Actual App feed/feedback/textarea + real recovery and PlanningDeliveryApi GET; isolated fixture HTTP responses, no full App/Windows/live API/model/Vite.'},null,2)+'\n');}finally{esbuild.stop();}
"""
CLIP = r'''n=>{const r=n.getBoundingClientRect();let l=Math.max(0,r.left),t=Math.max(0,r.top),rr=Math.min(innerWidth,r.right),bb=Math.min(innerHeight,r.bottom),hidden=false;for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);if(p.hidden||p.inert||s.display==='none'||s.visibility==='hidden')hidden=true;if(p!==n){const b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){l=Math.max(l,b.left);rr=Math.min(rr,b.right);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){t=Math.max(t,b.top);bb=Math.min(bb,b.bottom);}}}const v=!hidden&&rr>l&&bb>t,hit=v?document.elementFromPoint((l+rr)/2,(t+bb)/2):null;return{width:r.width,height:r.height,visibleWidth:v?rr-l:0,visibleHeight:v?bb-t:0,hit:hit===n||n.contains(hit)};}'''


def test_concise_delivery_feedback_original_reads_and_draft():
    asyncio.run(run())


async def run():
    out = evidence_folder('concise_delivery_feedback_source66');out.mkdir(parents=True,exist_ok=True)
    compiler=out/'compile-fixture.mjs';compiler.write_text(COMPILER)
    if os.environ.get('EEAS_REUSE_FROZEN_FIXTURE')=='1':
        import hashlib
        pinned=json.loads((out/'COMPILE_INPUTS.json').read_text())
        assert hashlib.sha256((out/'fixture.js').read_bytes()).hexdigest()==pinned['bundle']['sha256']
        assert pinned['variant']==os.environ.get('EEAS_UI_VARIANT','after')
    else:
        result=subprocess.run([str(NODE),str(compiler)],cwd=ROOT,env={**os.environ, 'EEAS_EVIDENCE_DIR':str(out),'EEAS_REPO':str(ROOT),'TMPDIR':str(out),'NODE_COMPILE_CACHE':str(out/'node-cache')},text=True,capture_output=True)
        (out/'compile.stdout').write_text(result.stdout);(out/'compile.stderr').write_text(result.stderr);assert result.returncode==0,result.stderr
    observations=[];variant=os.environ.get('EEAS_UI_VARIANT','after')
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,executable_path=str(CHROMIUM))
            try:
                for locale,width,height in ([('en',1180,760)] if os.environ.get('EEAS_SUCCESS_ONLY')=='1' else [('zh-CN',780,480),('en',1180,760)]):
                    context=await browser.new_context(viewport={'width':width,'height':height},locale=locale);page=await context.new_page();page.set_default_timeout(5000);errors=[];outside=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    async def route(r):
                        path=urlparse(r.request.url).path
                        if r.request.url=='http://127.0.0.1:17666/':await r.fulfill(body='<!doctype html><html><body><div id="root"></div></body></html>',content_type='text/html')
                        elif path in ['/web/assets/fonts/manrope-variable.woff2','/web/assets/fonts/noto-sans-sc-variable.woff2']:await r.fulfill(body=(ROOT/'apps'/path.removeprefix('/')).read_bytes(),content_type='font/woff2')
                        else:outside.append(r.request.url);await r.abort()
                    await page.route('**/*',route);await page.goto('http://127.0.0.1:17666/');await page.evaluate('(l)=>localStorage.setItem("eeas.ui-language.v1",l)',locale)
                    for name in STYLES:await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
                    await page.add_script_tag(path=str(out/'fixture.js'));await asyncio.wait_for(page.evaluate('()=>document.fonts.ready'),timeout=10);await page.evaluate('()=>Promise.all(document.getAnimations().filter(a=>a.effect?.getComputedTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})))')
                    assert not errors, errors
                    cards=page.locator('.as-feed-item');assert await cards.count()==2
                    collapsed=await page.locator('.as-transcript').inner_text();key_a=await page.evaluate('()=>qa.keyA');key_b=await page.evaluate('()=>qa.keyB')
                    if os.environ.get('EEAS_SUCCESS_ONLY')=='1':
                        field=page.locator('.as-composer textarea');await field.evaluate('(n)=>{window.originalField=n;n.focus();n.setSelectionRange(3,12);}')
                        await page.evaluate('()=>qa.succeed()');button=cards.first.locator('button');await button.click();artifact=cards.first.locator('.as-chat-delivery');await artifact.wait_for(state='visible');await page.wait_for_function('()=>document.activeElement?.classList.contains("as-chat-artifact")')
                        assert await cards.first.locator('button.as-text-action').count()==0
                        assert await artifact.locator('button').first.inner_text() and 'original-delivery.docx' in await artifact.inner_text()
                        await artifact.locator('button').first.click();await page.wait_for_function('()=>qa.state().opens.length===1');opened=await page.evaluate('()=>qa.state().opens[0]');assert opened['action']=='delivery.file.open' and opened['operation_id']==key_a and opened['file']['project_id']=='qa-project' and opened['file']['sha256']=='c'*64,opened
                        await page.evaluate('()=>qa.hold()');second=cards.nth(1).locator('button.as-text-action');await second.click();await page.wait_for_function('()=>qa.state().calls.length===2');await field.focus();await field.evaluate('(n)=>n.setSelectionRange(n.value.length,n.value.length)');await field.type(' keep focus');draft=await field.input_value();await page.evaluate('()=>qa.release()');await cards.nth(1).locator('.as-chat-delivery').wait_for(state='visible');await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                        assert await field.evaluate('(n)=>n===window.originalField&&document.activeElement===n') and await field.input_value()==draft
                        await page.evaluate('()=>qa.restoreBackground()');await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');assert await field.evaluate('(n)=>document.activeElement===n')
                        state=await page.evaluate('()=>qa.state()');assert state['posts']==0 and state['statuses']==['EXPORTED_CONFIRMED_PLAN_DERIVATIVES']*2 and all(x['method']=='GET'for x in state['calls']);assert not errors and not outside,(errors,outside)
                        await page.screenshot(path=str(out/'success-same-card-and-input-focus.png'));observations.append({'phase':'known_success_focus_and_existing_artifact_action','locale':locale,'original_GETs':state['calls'],'focused_artifact_after_explicit_check':True,'existing_action':opened,'late_success_and_background_restore_kept_textarea_focus':True,'same_draft':True,'business_POST_count':0,'file_bytes_or_native_open':'NOT_QUALIFIED_ACTION_PORT_ONLY'});await context.close();continue
                    if variant=='before':
                        observations.append({'locale':locale,'counterexample':{'main_contains_original_id':key_a in collapsed,'main_contains_raw_backend_detail':'RAW_BACKEND_66' in collapsed,'explicit_query_button_count':await cards.locator('button').count(),'concise_oracle_passed':False}})
                        assert key_a in collapsed and 'RAW_BACKEND_66' in collapsed
                        await page.screenshot(path=str(out/f'{locale}-source65-counter.png'));await context.close();continue
                    assert key_a not in collapsed and key_b not in collapsed and 'RAW_BACKEND_66' not in collapsed
                    assert await cards.locator('details[open]').count()==0
                    field=page.locator('.as-composer textarea');await field.evaluate('(n)=>{window.originalField=n;n.focus();n.setSelectionRange(3,12);}')
                    draft=await field.input_value();await page.screenshot(path=str(out/f'{locale}-collapsed.png'))
                    expected='核查原操作' if locale=='zh-CN' else 'Inspect original operation'
                    buttons=cards.locator('button');assert await buttons.count()==2;assert await buttons.first.inner_text()==expected
                    first=cards.first;await first.locator('summary').click();await first.locator('pre').wait_for(state='visible');assert await first.locator('pre').inner_text()==await page.evaluate('()=>qa.raw')
                    long_box=await first.locator('pre').evaluate('(n)=>({height:n.clientHeight,scroll:n.scrollHeight,width:n.clientWidth,overflow:n.scrollWidth})');assert long_box['height']<=min(240,height*.35)+1 and long_box['scroll']>long_box['height'] and long_box['overflow']<=long_box['width']+1,long_box
                    await first.locator('pre').evaluate('(n)=>n.scrollTop=n.scrollHeight');await first.locator('pre').scroll_into_view_if_needed();detail_clip=await first.locator('pre').evaluate(CLIP);assert detail_clip['visibleHeight']>=detail_clip['height']-1,detail_clip
                    terminal=await first.locator('pre').evaluate('(n)=>{const marker="END_OF_ORIGINAL_DETAIL_66",text=n.firstChild,range=document.createRange(),start=text.textContent.lastIndexOf(marker);range.setStart(text,start);range.setEnd(text,start+marker.length);const r=range.getBoundingClientRect(),p=n.getBoundingClientRect();return{top:r.top,bottom:r.bottom,left:r.left,right:r.right,preTop:p.top,preBottom:p.bottom};}');assert terminal['top']>=terminal['preTop']-1 and terminal['bottom']<=terminal['preBottom']+1,terminal
                    observations.append({'phase':'long_detail_terminal_visible','locale':locale,'clip':detail_clip,'terminal':terminal});await page.screenshot(path=str(out/f'{locale}-details-end.png'))
                    await first.locator('summary').click();assert await first.locator('pre').is_hidden()
                    for index,key in [(1,key_b),(0,key_a)]:
                        button=cards.nth(index).locator('button');await button.scroll_into_view_if_needed();box=await button.evaluate(CLIP);assert box['hit'] and box['visibleHeight']>=box['height']-1,box
                        await button.evaluate('(n)=>{window.originalButton=n;}');await button.click();await page.wait_for_function('(count)=>qa.state().calls.length===count',arg=2-index)
                        await page.wait_for_function('()=>!document.querySelector("button[aria-busy=true]")');focus_state=await button.evaluate('(n)=>({same:n===window.originalButton,focused:document.activeElement===n,active:document.activeElement?.outerHTML})');observations.append({'phase':'original_GET_focus','locale':locale,'key':key,'focus':focus_state});assert focus_state['same'] and focus_state['focused'],focus_state
                    state=await page.evaluate('()=>qa.state()');assert [x['url'].split("/")[-1]for x in state['calls']]==[key_b,key_a] and all(x['method']=='GET'for x in state['calls']) and state['posts']==0
                    assert state['statuses']==['NO_RECORD','NO_RECORD']
                    assert await field.evaluate('(n)=>n===window.originalField&&n.value===qa.state().draft&&n.selectionStart===3&&n.selectionEnd===12')
                    # A second activation while the original GET is in flight cannot create another read.
                    await page.evaluate('()=>qa.hold()');button=cards.first.locator('button');await button.click();await page.wait_for_function('()=>qa.state().calls.length===3');await button.evaluate('(n)=>n.click()');assert len((await page.evaluate('()=>qa.state()'))['calls'])==3
                    await field.focus();await field.evaluate('(n)=>n.setSelectionRange(n.value.length,n.value.length)');await field.type(' 追加原文');await page.evaluate('()=>qa.scope()');await page.evaluate('()=>qa.release()');await page.wait_for_function('()=>!document.querySelector("button[aria-busy=true]")')
                    final=await page.evaluate('()=>qa.state()');assert final['draft']==draft+' 追加原文' and final['statuses']==['NO_RECORD','NO_RECORD'] and final['posts']==0
                    assert await field.evaluate('(n)=>n===window.originalField&&document.activeElement===n');field_box=await field.evaluate(CLIP);assert field_box['visibleHeight']>0 and field_box['hit'],field_box
                    assert not errors and not outside,(errors,outside)
                    observations.append({'locale':locale,'viewport':[width,height],'collapsed_has_no_id_or_diagnostic':True,'same_card_button_focus_after_GET':True,'original_GETs':final['calls'],'business_POST_count':0,'same_textarea_and_user_text':True,'details':long_box,'field_clip':field_box,'late_scope_did_not_mutate_cards':True})
                    await context.close()
            finally:await browser.close()
    finally:(out/'OBSERVATIONS.json').write_text(json.dumps({'variant':variant,'observations':observations,'qualification':'ISOLATED_PRODUCTION_FRAGMENT_BROWSER_NOT_FULL_APP_NATIVE_OR_REAL_BACKEND'},ensure_ascii=False,indent=2)+'\n')
