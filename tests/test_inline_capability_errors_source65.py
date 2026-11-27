"""Real App callbacks/composer/popover in isolated Chromium; no App/API/model."""
import asyncio
import hashlib
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
import fs from 'node:fs';import path from 'node:path';import {createRequire}from'node:module';import{createHash}from'node:crypto';
const root=process.env.EEAS_REPO,app=path.join(root,'apps/agent-shell'),out=process.env.EEAS_EVIDENCE_DIR,variant=process.env.EEAS_UI_VARIANT??'after',baseline=process.env.EEAS_UI_BASELINE,req=createRequire(path.join(app,'package.json'));
const ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild'),preset=(await import(path.join(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const sourcePath=variant==='before'?path.join(baseline,'apps/agent-shell/src/App.tsx'):path.join(app,'src/App.tsx'),source=fs.readFileSync(sourcePath,'utf8'),tree=ts.createSourceFile(sourcePath,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),nodes=[];function walk(n){nodes.push(n);ts.forEachChild(n,walk);}walk(tree);
const fn=name=>nodes.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name)?.getText(tree)??'';
const named=(tag,has)=>{const n=nodes.find(n=>ts.isJsxElement(n)&&n.openingElement.tagName.getText(tree)===tag&&has(n));if(!n)throw Error('Missing actual JSX '+tag);return n.getText(tree);};
const mode=named('div',n=>n.openingElement.getText(tree).startsWith('<div class="as-native-review-composer"'));
const banners=[n=>n.openingElement.getText(tree).includes('!panelVisibility().workbench'),n=>n.openingElement.getText(tree)==="<Show when={windowMode()==='companion'&&error()}>",n=>n.openingElement.getText(tree)==="<Show when={windowMode()==='companion'&&notice()}>"].map(p=>named('Show',p)).join('');
const model=named('StudioPopover',n=>n.openingElement.getText(tree).includes('composerMenu()===\'model\''));
const area=nodes.find(n=>ts.isJsxSelfClosingElement(n)&&n.tagName.getText(tree)==='textarea'&&n.getText(tree).includes('描述要完成的工作')).getText(tree);
const title=nodes.find(n=>ts.isVariableDeclaration(n)&&n.name.getText(tree)==='selectedModelTitle');
const added=nodes.find(n=>ts.isJsxSelfClosingElement(n)&&n.tagName.getText(tree)==='ComposerAddMenu');
const toolsHandler=added?.attributes.properties.find(attr=>attr.name?.getText(tree)==='onTools')?.getText(tree);
const addMenu=toolsHandler?'<ComposerAddMenu locale={uiLocale()} busy={false} captureAvailable={false} toolsAvailable={true} '+toolsHandler+' onFile={()=>{}} onPage={()=>{}} onScreenshot={()=>{}}/>':'';
const functions=(title?'const '+title.getText(tree)+';\n':'')+['readPageReview','openPageReview','currentReviewCatalogError','openReviewModelConnections','toggleComposerMenu','closeComposerMenu'].map(fn).join('\n');
const program=`import{render}from'solid-js/web';import{createSignal,Show,For,onCleanup}from'solid-js';import ComposerAddMenu from '${app}/src/ComposerAddMenu.tsx';import NativePageConversation from '${app}/src/NativePageConversation.tsx';import StudioPopover from '${app}/src/StudioPopover.tsx';import{Icon}from'${root}/staging-native-ui/upstream/packages/ui/src/components/icon.tsx';import{authoredCopy,authoredErrorCopy,authoredText,AuthoredUIError}from'${app}/src/authored-ui-copy.ts';import{sameReview,needsReviewProcess,needsReviewRemote}from'${app}/src/native-review.ts';import{sortModelProfiles,modelProfileConnectionHint}from'${app}/src/model-catalog.ts';import{startUILanguage,t as uiText,uiLocale}from'${app}/src/ui-language.ts';
function Fixture(){onCleanup(startUILanguage());const[prompt,setPrompt]=createSignal('原创未发送草稿 Abc😀，不改变当前项目与引用。'),[error,setError]=createSignal(''),[notice,setNotice]=createSignal(''),[mode,setMode]=createSignal('workbench'),[reviewCatalogError,setReviewCatalogError]=createSignal(),[composerMenu,setComposerMenu]=createSignal(),[composerMenuKeyboard,setComposerMenuKeyboard]=createSignal(false),[pageReview,setPageReview]=createSignal(null),[catalog,setReviewCatalog]=createSignal(),[providerId,setProviderId]=createSignal(''),[scopeId,setScopeId]=createSignal('original_scope');let studioDisposed=false,composerMenuFrame,modelMenuButton,optionsMenuButton,reviewReadEpoch=0,readBehavior='missing';const calls=[],pending=[],providers=()=>[];const composerView=()=>({folded:false,focused:true}),composerPresentation={focus(){}},windowMode=()=>mode(),panelVisibility=()=>({workbench:false,agent:true}),uiCopyText=value=>authoredText(value,uiText),conversationId=()=>scopeId(),quickScope=()=>({conversationId:scopeId(),...(providerId()?{providerProfileRef:{id:providerId(),revision:1,sha256:'a'.repeat(64)}}:{})}),linkedFile=()=>undefined;let selectionOpen=false;function setReviewHintSelection(){}function setReviewSelecting(value){selectionOpen=value;}function setCapabilitiesOpen(value){selectionOpen=value;}const nativeBridge={getNativeReviewCatalog:async body=>{calls.push(structuredClone(body));if(readBehavior==='held')return new Promise((resolve,reject)=>pending.push({resolve,reject}));throw Error(readBehavior==='missing'?"Error invoking remote method 'eeas:native-review-catalog': Error: 请先配置模型连接":"Error invoking remote method 'eeas:native-review-catalog': Error: INTERNAL_STACK_SECRET original_operation_unknown");}};const reviewContinue=()=>undefined,reviewUnknown=()=>undefined,ordinaryGuidanceAvailable=()=>false,nativePending=()=>false,ordinaryNewQuestion=()=>false,ordinaryGuidanceParent=()=>undefined,nativeMessages=()=>[],reviewCatalog=()=>catalog(),reviewHintSelection=()=>undefined,pageReviewLabel=()=>'',changePageReview=()=>{throw Error('Unreviewed selection mutation');},newPageQuestion=()=>{throw Error('Unreviewed task mutation');};let submits=0,providerSettings=0;const submitPrompt=()=>submits++,openProviders=()=>providerSettings++,railKey=()=>{};
${functions}
onCleanup(()=>{studioDisposed=true;if(composerMenuFrame!==undefined)cancelAnimationFrame(composerMenuFrame);});
window.qa={banners:()=>{setMode('companion');setError('原创界面错误');setNotice('原创界面提示');},state:()=>({draft:prompt(),menu:composerMenu(),calls:calls.length,selectionOpen,submits,providerSettings,scope:scopeId(),provider:providerId(),error:error(),localError:typeof currentReviewCatalogError==='function'?currentReviewCatalogError():undefined,originalOperation:{id:'retained_original',status:'UNKNOWN_OUTCOME'}}),provider:setProviderId,scope:setScopeId,behavior:value=>readBehavior=value,rejectHeld:()=>pending.shift().reject(Error("Error invoking remote method 'eeas:native-review-catalog': Error: 请先配置模型连接")),setSelection:setPageReview,formatProbe:()=>{const raw=Error("Error invoking remote method 'eeas:native-review-catalog': Error: 请先配置模型连接");raw.status=503;const unknown=Error("Error invoking remote method 'eeas:native-message-submit': Error: PRIVATE_STACK_TOKEN");unknown.status='UNKNOWN_OUTCOME';const literal="Error invoking remote method 'eeas:user-written': Error: original user text";return {raw:raw.message,known:authoredText(authoredErrorCopy(raw),uiText),unchanged:raw.message===\"Error invoking remote method 'eeas:native-review-catalog': Error: 请先配置模型连接\"&&raw.status===503,unknown:authoredText(authoredErrorCopy(unknown),uiText),unknownStatus:unknown.status,literalUnchanged:authoredErrorCopy(literal)===literal,authored:authoredText(authoredErrorCopy(new AuthoredUIError('选择模型')),uiText)};}};
return <div class="as-app-frame"><div class="as-shell" data-layout="workbench" data-chat-layout="adaptive" data-window-mode={windowMode()} data-compact="false" data-fullscreen="false" data-mobile-view="agent" data-files-hidden="true" data-agent-hidden="false" data-workbench-hidden="true" style={{'--as-agent-width':'800px'}}><section class="as-workbench"><div class="as-error-bar"><span>{authoredText(error(),uiText)}</span></div></section><aside class="as-agent"><div class="as-agent-content"><header class="as-agent-header"><strong>Cirven</strong></header>${banners}<main class="as-conversation"><p>原创讨论保留，工作台按需打开。</p></main><form class="as-composer" onSubmit={e=>{e.preventDefault();submitPrompt();}}><NativePageConversation scopeKey="original_scope" mode={${mode}} input={${area}} toolbar={<div class="as-composer-toolbar">${addMenu}<span style={{flex:1}}/>${model}<button type="submit" class="as-composer-send" aria-label="send">↑</button></div>}/></form></div></aside></div></div>;}
render(()=> <Fixture/>,document.getElementById('root'));`;
const used=new Set([sourcePath]),pin=p=>{const raw=fs.readFileSync(p);return{path:p,bytes:raw.length,sha256:createHash('sha256').update(raw).digest('hex')};};
const plugin={name:'actual-app-source65-fragments',setup(b){b.onLoad({filter:/\.tsx$/},({path:p})=>{used.add(p);return{contents:babel.transformSync(ts.transpileModule(fs.readFileSync(p,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:p+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(p)};});if(variant==='before')b.onLoad({filter:/authored-ui-copy\.ts$/},({path:p})=>{const original=path.join(baseline,'apps/agent-shell/src/authored-ui-copy.ts');used.add(original);return{contents:fs.readFileSync(original,'utf8'),loader:'ts',resolveDir:path.dirname(p)};});}};
fs.mkdirSync(out,{recursive:true});const entry=path.join(out,'fixture.tsx');fs.writeFileSync(entry,program);try{const result=await esbuild.build({entryPoints:[entry],outfile:path.join(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[path.join(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},alias:{'@opencode-ai/ui/icon':path.join(root,'staging-native-ui/upstream/packages/ui/src/components/icon.tsx')},define:{'process.env.NODE_ENV':'"production"'},metafile:true,logLevel:'silent'});for(const n of Object.keys(result.metafile.inputs))used.add(path.resolve(n));fs.writeFileSync(path.join(out,'COMPILE_INPUTS.json'),JSON.stringify({variant,source:pin(sourcePath),inputs:[...used].map(pin),fixture:pin(entry),bundle:pin(path.join(out,'fixture.js')),boundary:'Actual App functions + composer JSX + NativePageConversation/StudioPopover/formatter; native callback is fixture, no full App/API/model/Windows/Vite.'},null,2)+'\n');}finally{esbuild.stop();}
"""


def test_inline_capability_errors_preserve_chat_and_original_operation():
    asyncio.run(run())


async def run():
    out = evidence_folder('inline_capability_errors_source65')
    out.mkdir(parents=True, exist_ok=True)
    compiler = out / 'compile-fixture.mjs'
    compiler.write_text(COMPILER)
    env = {**os.environ, 'EEAS_EVIDENCE_DIR':str(out), 'EEAS_REPO': str(ROOT), 'NODE_COMPILE_CACHE': str(out / 'node-cache'), 'TMPDIR': str(out)}
    result = subprocess.run([str(NODE), str(compiler)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    (out / 'compile.stdout').write_text(result.stdout)
    (out / 'compile.stderr').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    variant = os.environ.get('EEAS_UI_VARIANT', 'after')
    observations = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            try:
                for locale, width, height in [('zh-CN', 768, 474), ('en', 1180, 760)]:
                    context = await browser.new_context(viewport={'width': width, 'height': height}, locale=locale)
                    page = await context.new_page()
                    errors, external = [], []
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    fonts = {'/web/assets/fonts/manrope-variable.woff2': ROOT / 'apps/web/assets/fonts/manrope-variable.woff2', '/web/assets/fonts/noto-sans-sc-variable.woff2': ROOT / 'apps/web/assets/fonts/noto-sans-sc-variable.woff2'}

                    async def route(request):
                        name = urlparse(request.request.url).path
                        if request.request.url == 'http://127.0.0.1:17665/':
                            await request.fulfill(body='<!doctype html><html><body><div id="root"></div></body></html>', content_type='text/html')
                        elif name in fonts:
                            await request.fulfill(body=fonts[name].read_bytes(), content_type='font/woff2')
                        else:
                            external.append(request.request.url)
                            await request.abort()

                    await page.route('**/*', route)
                    await page.goto('http://127.0.0.1:17665/')
                    await page.evaluate('(locale)=>localStorage.setItem("eeas.ui-language.v1",locale)', locale)
                    for name in STYLES:
                        await page.add_style_tag(content=(ROOT / 'apps/agent-shell/src' / name).read_text())
                    await page.add_script_tag(path=str(out / 'fixture.js'))
                    await page.evaluate('()=>document.fonts.ready')
                    if os.environ.get('EEAS_UI_BANNER_PROBE') == '1':
                        counts = await banner_counts(page, out, locale)
                        observations.append({'phase': 'companion_global_feedback_count', 'locale': locale, 'measure': counts})
                        assert counts == {'errors': 1, 'notices': 1}, counts
                        await context.close()
                        continue
                    field = page.locator('.as-composer textarea')
                    await field.evaluate('(n)=>{window.originalTextarea=n;n.focus();n.setSelectionRange(3,10);}')
                    before = await page.evaluate('()=>({value:originalTextarea.value,start:originalTextarea.selectionStart,end:originalTextarea.selectionEnd,operation:qa.state().originalOperation})')
                    entry = page.locator('[data-composer-add]') if await page.locator('[data-composer-add]').count() else page.locator('.as-native-review-composer > button').first
                    async def open_tools():
                        if await page.locator('[data-composer-add]').count():
                            await page.locator('[data-composer-add]').click()
                            await page.get_by_role('button',name='Skills and tools' if locale=='en' else '技能与工具',exact=True).click()
                        else:
                            await page.locator('.as-native-review-composer > button').first.click()
                    await open_tools()
                    await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    local = page.locator('[data-native-catalog-error]')
                    old = await page.evaluate('()=>({globalError:qa.state().error,localCount:document.querySelectorAll("[data-native-catalog-error]").length,workbenchHidden:document.querySelector(".as-shell").dataset.workbenchHidden,calls:qa.state().calls})')
                    observations.append({'phase': 'missing_model', 'locale': locale, 'measure': old})
                    await page.screenshot(path=str(out / f'{locale}-missing-model.png'))
                    assert await local.count() == 1, old
                    assert await local.is_visible()
                    assert 'eeas:' not in await local.inner_text() and 'Error invoking' not in await local.inner_text()
                    action = local.locator('button')
                    box = await action.evaluate(CLIP)
                    assert box['visibleWidth'] >= box['width'] - 1 and box['visibleHeight'] >= box['height'] - 1 and box['hit'], box
                    assert await entry.evaluate('(n)=>document.activeElement===n')
                    state = await page.evaluate('()=>({same:document.querySelector(".as-composer textarea")===originalTextarea,value:originalTextarea.value,start:originalTextarea.selectionStart,end:originalTextarea.selectionEnd,state:qa.state(),hidden:document.querySelector(".as-shell").dataset.workbenchHidden})')
                    assert state['same'] and state['value'] == before['value'] and [state['start'], state['end']] == [3, 10]
                    assert state['state']['calls'] == 0 and state['state']['submits'] == 0 and state['hidden'] == 'true'
                    await action.click()
                    await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    action_state = await page.evaluate('()=>({state:qa.state(),popover:document.querySelector("#as-composer-model-options")?.outerHTML,active:document.activeElement?.outerHTML})')
                    action_state['page_errors'] = list(errors)
                    (out / f'{locale}-model-action-observation.json').write_text(json.dumps(action_state, ensure_ascii=False, indent=2) + '\n')
                    popover = page.locator('#as-composer-model-options')
                    await popover.wait_for(state='visible', timeout=3000)
                    assert await page.locator('.as-shell').get_attribute('data-workbench-hidden') == 'true'
                    close = popover.locator('header button')
                    await close.click()
                    await popover.wait_for(state='hidden')
                    assert await page.locator('.as-composer-model-trigger').evaluate('(n)=>document.activeElement===n')
                    assert (await page.evaluate('()=>qa.state().draft')) == before['value']
                    probe = await page.evaluate('()=>qa.formatProbe()')
                    observations.append({'phase': 'formatter_and_existing_model_action', 'locale': locale, 'measure': probe, 'action_box': box})
                    assert probe['unchanged'] and probe['unknownStatus'] == 'UNKNOWN_OUTCOME' and probe['literalUnchanged']
                    assert 'eeas:' not in probe['known'] + probe['unknown'] and 'PRIVATE_STACK_TOKEN' not in probe['unknown']
                    expected_unknown = '暂时无法确认结果。' if locale == 'zh-CN' else 'The result could not be confirmed.'
                    assert probe['unknown'] == expected_unknown
                    await page.evaluate('()=>{qa.provider("original_provider");qa.behavior("unknown");}')
                    await open_tools()
                    await page.wait_for_function('()=>!!qa.state().localError')
                    assert 'INTERNAL_STACK_SECRET' not in await local.inner_text()
                    assert (await local.inner_text()).strip() == expected_unknown
                    unknown = await page.evaluate('()=>qa.state()')
                    assert unknown['calls'] == 1 and unknown['submits'] == 0 and unknown['originalOperation'] == before['operation']
                    await page.screenshot(path=str(out / f'{locale}-unknown-ipc.png'))
                    await page.evaluate('()=>{qa.behavior("held");}')
                    await open_tools()
                    await page.wait_for_function('()=>qa.state().calls===2')
                    await page.evaluate('()=>{qa.scope("new_scope");qa.rejectHeld();}')
                    await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    assert await local.count() == 0
                    assert (await page.evaluate('()=>qa.state()'))['calls'] == 2
                    assert not errors, errors
                    assert not external, external
                    observations.append({'phase': 'late_reject_new_scope_not_shown', 'locale': locale, 'calls': 2, 'same_textarea': await field.evaluate('(n)=>n===window.originalTextarea')})
                    counts = await banner_counts(page, out, locale)
                    observations.append({'phase': 'companion_global_feedback_count', 'locale': locale, 'measure': counts})
                    assert counts == {'errors': 1, 'notices': 1}, counts
                    await context.close()
            finally:
                await browser.close()
    finally:
        (out / 'OBSERVATIONS.json').write_text(json.dumps({'variant': variant, 'observations': observations, 'qualification': 'ISOLATED_PRODUCTION_UI_FRAGMENTS_REAL_POPOVER_HELPERS_CSS_NOT_FULL_APP_OR_NATIVE'}, ensure_ascii=False, indent=2) + '\n')


async def banner_counts(page, out, locale):
    await page.evaluate('()=>qa.banners()')
    await page.evaluate('()=>Promise.all(Array.from(document.querySelectorAll(".as-agent-content > .as-error-bar,.as-agent-content > .as-notice")).flatMap(n=>n.getAnimations().map(a=>a.finished.catch(()=>{}))))')
    counts = {}
    for name, selector in [('errors', '.as-agent-content > .as-error-bar'), ('notices', '.as-agent-content > .as-notice')]:
        boxes = [await node.evaluate(CLIP) for node in await page.locator(selector).all()]
        counts[name] = sum(b['visibleWidth'] > 0 and b['visibleHeight'] > 0 for b in boxes)
    (out / f'{locale}-companion-counts.json').write_text(json.dumps(counts, indent=2) + '\n')
    await page.screenshot(path=str(out / f'{locale}-companion-banners.png'))
    return counts


CLIP = r'''n=>{const r=n.getBoundingClientRect();let l=Math.max(0,r.left),t=Math.max(0,r.top),rr=Math.min(innerWidth,r.right),bb=Math.min(innerHeight,r.bottom),hidden=false;for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);if(p.hidden||p.inert||s.display==='none'||s.visibility==='hidden')hidden=true;if(p!==n){const b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){l=Math.max(l,b.left);rr=Math.min(rr,b.right);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){t=Math.max(t,b.top);bb=Math.min(bb,b.bottom);}}}const v=!hidden&&rr>l&&bb>t,hit=v?document.elementFromPoint((l+rr)/2,(t+bb)/2):null;return{width:r.width,height:r.height,visibleWidth:v?rr-l:0,visibleHeight:v?bb-t:0,hit:hit===n||n.contains(hit),font:getComputedStyle(n).fontFamily,fontSize:getComputedStyle(n).fontSize};}'''
