"""Actual DiscoveryRenderer/Kobalte Button + local product CSS, controlled IO.

No whole-App, API business, model, native or Windows qualification is claimed.
"""
import asyncio,json,os,subprocess
from pathlib import Path
import pytest
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[1]
NODE=ROOT/'.node-tools/node_modules/node/bin/node'
BUILD=r'''
const fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module');const root=process.argv[1],app=path.join(root,'apps/agent-shell'),src=path.join(app,'src'),req=createRequire(path.join(app,'package.json')),esbuild=req('esbuild'),babel=req('@babel/core'),solid=createRequire(req.resolve('vite-plugin-solid'))('babel-preset-solid'),ts=req('typescript');
const entry=`import {render} from 'solid-js/web';import {createComponent} from 'solid-js';import DiscoveryRenderer from ${JSON.stringify(path.join(src,'DiscoveryRenderer.tsx'))};import {ObjectDraftBoundary} from ${JSON.stringify(path.join(src,'ObjectDraft.tsx'))};import {startUILanguage,setUILanguagePreference} from ${JSON.stringify(path.join(src,'ui-language.ts'))};
window.mountPlan=async(initial,locale)=>{const listeners=new Set();window.__calls=[];window.__owner={status:'saved',view:{},fields:{},subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn)},registerDefault(ch,name,value){if(!(name in this[ch]))this[ch][name]=structuredClone(value)},registerRecordDefault(name,key,value){this.fields[name]??={};this.fields[name][key]??=structuredClone(value)},value(ch,name,fallback){return this[ch][name]??fallback},set(ch,name,value){this[ch][name]=structuredClone(value);for(const f of listeners)f()},flush(){return Promise.resolve()},inspect(){return Promise.resolve()}};
const stopLanguage=startUILanguage();await setUILanguagePreference(locale);let release;const artifact={artifactId:initial.id,type:'discovery',scope:{projectId:initial.project_id},revision:initial.revision,contentHash:initial.sha256,data:initial};const context={dispatch:async(id,target,input)=>{window.__calls.push({id,revision:target.revision,input:structuredClone(input)});return new Promise(done=>{release=done})}};const stop=render(()=>createComponent(ObjectDraftBoundary,{artifact,get children(){return createComponent(DiscoveryRenderer,{artifact,context})}}),document.querySelector('#fixture'));window.__driver={locale:setUILanguagePreference,release(){release?.({status:'PROCESSING'})},dispose(){stop();stopLanguage()},state(){return structuredClone({fields:window.__owner.fields,calls:window.__calls})}};};`;
const plugin={name:'controlled-io-only',setup(b){b.onResolve({filter:/^@opencode-ai\/ui\/button$/},()=>({path:path.join(root,'staging-native-ui/upstream/packages/ui/src/components/button.tsx')}));b.onResolve({filter:/^\.\/(SemanticGraph|GraphEditor|WordPreview|TextFeedbackEditor|PlanningDeliveryResult)$/},()=>({path:'unused-child',namespace:'fixture'}));b.onResolve({filter:/^\.\/workbench-state$/},a=>a.importer.endsWith('/ObjectDraft.tsx')?({path:'draft-port',namespace:'fixture'}):undefined);b.onLoad({filter:/.*/,namespace:'fixture'},a=>({contents:a.path==='draft-port'?`export const artifactStateKey=()=> 'fixture';export const objectDraft=()=>window.__owner;`:'export default function UnusedChild(){return null}',loader:'js'}));b.onLoad({filter:/\.tsx$/},a=>{const stripped=ts.transpileModule(fs.readFileSync(a.path,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;return{contents:babel.transformSync(stripped,{filename:a.path,presets:[[solid,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(a.path)}});}};
esbuild.build({stdin:{contents:entry,loader:'js',resolveDir:app},plugins:[plugin],bundle:true,format:'iife',platform:'browser',write:false,define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent'}).then(r=>process.stdout.write(r.outputFiles[0].text)).catch(e=>{console.error(e);process.exitCode=1});
'''

@pytest.fixture(scope='module')
def bundle():
 result=subprocess.run([str(NODE),'-e',BUILD,str(ROOT)],capture_output=True,text=True,timeout=35)
 assert result.returncode==0,result.stderr
 return result.stdout


def original_session():
 return {'id':'plan_fixture','project_id':'project_fixture','revision':4,'sha256':'a'*64,'source_current':True,'answers':[],
 'payload':{'status':'CONFIRMED','accepted_claim_ids':['R01'],'accepted_graph_ids':[],'resolutions':[],'history':[],'source_receipt':{'documents':[]},'candidate':{'title':'用户的原始工作标题','summary':'已确认的原创测试要求，不授权执行。','phase':'PLAN','claims':[{'id':'R01','text':'生成任务书与依赖关系，不执行开发','category':'REQUIREMENT','origin':'USER_STATEMENT','source_anchors':[],'acceptance':['保留人工确认的基线']}],'graphs':[],'next_actions':[],'competitors':[],'questions':[{'id':'Q01','text':'可继续补充的问题','reason':'保持未发送的补充文字','blocking':False,'affects_claim_ids':['R01'],'requested_materials':[],'source_anchors':[]}]}},
 'readiness':{'can_plan':True,'requirements_confirmed':True,'pending_count':1,'blocking_count':0,'execution_authorized':False,'semantic_verification':'NOT_PERFORMED','review_status':'READY'}}

GEOMETRY=r'''()=>{const s=document.querySelector('.as-plan-wait-budget'),b=[...s.parentElement.querySelectorAll('button')].at(-1);function rect(e){const r=e.getBoundingClientRect(),c=getComputedStyle(e);let clip={left:Math.max(0,r.left),right:Math.min(innerWidth,r.right),top:Math.max(0,r.top),bottom:Math.min(innerHeight,r.bottom)};for(let p=e.parentElement;p;p=p.parentElement){const style=getComputedStyle(p),box=p.getBoundingClientRect();if(/hidden|clip|auto|scroll/.test(style.overflowX)){clip.left=Math.max(clip.left,box.left);clip.right=Math.min(clip.right,box.right)}if(/hidden|clip|auto|scroll/.test(style.overflowY)){clip.top=Math.max(clip.top,box.top);clip.bottom=Math.min(clip.bottom,box.bottom)}}return {...r.toJSON(),font:parseFloat(c.fontSize),minHeight:c.minHeight,clip,widthCss:c.width};}const parent=s.parentElement;return{select:rect(s),button:rect(b),parent:rect(parent),scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,value:s.value,options:[...s.options].map(o=>({value:o.value,label:o.textContent})),buttonLabel:b.textContent,...__driver.state()}}'''

async def run(bundle,tmp):
 p=await async_playwright().start();browser=await p.chromium.launch(headless=True);results=[]
 try:
  for width in [320,760]:
   for locale in ['zh-CN','en']:
    page=await browser.new_page(viewport={'width':1000,'height':900});errors=[];network=[];page.on('pageerror',lambda error:errors.append(str(error)))
    async def route(request):
     req=request.request;network.append({'method':req.method,'path':req.url.split('cirven.test')[-1]})
     if req.method!='GET':await request.abort();return
     if req.url=='http://cirven.test/':await request.fulfill(body='<html></html>',content_type='text/html');return
     if req.url.endswith('/api/bootstrap'):body={'csrf_token':'fixture'}
     elif req.url.endswith('/template-profiles'):body={'profiles':[]}
     elif req.url.endswith('/local-workspaces'):body={'workspaces':[]}
     else:await request.abort();return
     await request.fulfill(json=body)
    await page.route('**/*',route);await page.goto('http://cirven.test/')
    await page.set_content(f'<div class="as-app-frame"><div id="fixture" style="width:{width}px;max-width:100%"></div></div>')
    for name in ['styles.css','native-collaboration.css','agent-studio.css','brand-and-spacing.css']:
     await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
    await page.add_script_tag(content=bundle);await page.evaluate('async args=>mountPlan(...args)',[original_session(),locale]);choice=page.locator('.as-plan-wait-budget');await choice.wait_for()
    assert await choice.input_value()=='300'
    assert await choice.locator('option').evaluate_all('items=>items.map(o=>o.value)')==['120','300','600']
    await choice.focus();await choice.press('Home');assert await choice.input_value()=='120';await choice.press('End');assert await choice.input_value()=='600';textarea=page.locator('.as-question-card>textarea');await textarea.fill('保留未发补充 IME 😀');await page.evaluate('window.__originalTextarea=document.querySelector(".as-question-card>textarea")')
    await page.evaluate('locale=>__driver.locale(locale)',locale);assert await textarea.input_value()=='保留未发补充 IME 😀';assert await page.evaluate('__originalTextarea===document.querySelector(".as-question-card>textarea")')
    button=page.locator('.as-plan-confirm button').last;await button.scroll_into_view_if_needed();before=await page.evaluate(GEOMETRY)
    for name in ['select','button']:
     r=before[name];assert r['height']>=44 and r['font']>=15,before
     assert r['clip']['right']-r['clip']['left']>=r['width']-1 and r['clip']['bottom']-r['clip']['top']>=r['height']-1,before
     assert r['width']<=before['parent']['width']+1,before
    assert await choice.evaluate("e=>e.parentElement.classList.contains('as-plan-generation')"),'wait and generation controls stay in the same semantic group'
    a,b=before['select'],before['button'];assert a['right']<=b['left'] or b['right']<=a['left'] or a['bottom']<=b['top'] or b['bottom']<=a['top'],before
    if width==320 and locale=='zh-CN':assert abs(a['y']-b['y'])<4,'Chinese select and generation action fit together at the minimum pane'
    assert before['scrollWidth']<=before['clientWidth'],before
    await page.screenshot(path=str(tmp/f'{width}-{locale}.png'))
    await button.click();assert await choice.is_disabled();assert await button.is_disabled();sent=await page.evaluate('__driver.state()');assert len(sent['calls'])==1 and sent['calls'][0]['input']['timeoutSeconds']==600 and sent['calls'][0]['revision']==4,sent
    opposite='en' if locale=='zh-CN' else 'zh-CN';await page.evaluate('locale=>__driver.locale(locale)',opposite);assert await choice.input_value()=='600';assert await textarea.input_value()=='保留未发补充 IME 😀';assert len((await page.evaluate('__driver.state()'))['calls'])==1
    await page.evaluate('__driver.release()');await page.wait_for_timeout(30);assert len((await page.evaluate('__driver.state()'))['calls'])==1
    assert not errors,errors;assert not [r for r in network if r['method']!='GET'],network
    results.append({'width':width,'locale':locale,'geometry':before,'sent':sent,'network':network,'errors':errors});await page.evaluate('__driver.dispose()');await page.close()
  (tmp/'RESULT.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
 finally:
  try:await asyncio.wait_for(browser.close(),timeout=8)
  finally:await p.stop()


def test_plan_wait_control_320_and_760_zh_en_freezes_click_and_preserves_draft(bundle,tmp_path):asyncio.run(asyncio.wait_for(run(bundle,tmp_path),100))
