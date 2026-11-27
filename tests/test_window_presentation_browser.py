"""Actual HTML/preload and Solid titlebar; Electron boundary controlled, not Windows."""
import asyncio,json,subprocess,sys
import pytest
from pathlib import Path
from playwright.async_api import async_playwright
R=Path(__file__).resolve().parents[1]
NODE=R/'.node-tools/node_modules/node/bin/node'
WORKER=r'''
const path=require('node:path'),fs=require('node:fs'),vm=require('node:vm'),{createRequire}=require('node:module'),readline=require('node:readline');
const root=process.argv[1],file=path.join(root,'apps/desktop-host/tests/quick-surface.test.cjs'),text=fs.readFileSync(file,'utf8'),end=text.indexOf("test('constructs both durable");
const context=vm.createContext({require:createRequire(file),__dirname:path.dirname(file),console,Buffer,setTimeout,clearTimeout,setImmediate,queueMicrotask});
vm.runInContext(text.slice(0,end)+`let f;globalThis.dispatch=async request=>{
if(request.init){f=fixture({locale:request.init,onSetPinned:async enabled=>f.surface.setPinned(enabled)});await f.surface.openAsk();return {html:quickHtml('popover',request.init),state:f.surface.get()};}
if(request.close){f.surface.dispose();return {closed:true};}if(request.reopen){await f.surface.openAsk({anchor:{x:1380,y:20}});return {state:f.surface.get()};}
const value=await f.send(request);return {value,state:f.surface.get()};};`,context,{filename:file});
let tail=Promise.resolve();readline.createInterface({input:process.stdin}).on('line',raw=>{tail=tail.then(async()=>{try{const value=await context.dispatch(JSON.parse(raw));process.stdout.write(JSON.stringify(value)+'\n');}catch(error){process.stdout.write(JSON.stringify({error:String(error)})+'\n');}});});
'''

BUILD=r'''
const fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module');const root=process.argv[1],app=path.join(root,'apps/agent-shell'),src=path.join(app,'src'),req=createRequire(path.join(app,'package.json')),esbuild=req('esbuild'),babel=req('@babel/core'),solid=createRequire(req.resolve('vite-plugin-solid'))('babel-preset-solid'),ts=req('typescript');
const entry=`import {render} from 'solid-js/web';import {createComponent,createSignal} from 'solid-js';import Titlebar from ${JSON.stringify(path.join(src,'DesktopTitlebar.tsx'))};import {startUILanguage,setUILanguagePreference} from ${JSON.stringify(path.join(src,'ui-language.ts'))};window.mount=async(locale)=>{const stop=startUILanguage();await setUILanguagePreference(locale);const[pinned,setPinned]=createSignal(false),[mode,setMode]=createSignal('companion');window.__calls=0;render(()=>createComponent(Titlebar,{get mode(){return mode()},get pinned(){return pinned()},onPin(){window.__calls++;setPinned(v=>!v)}}),document.querySelector('#fixture'));window.driver={locale:setUILanguagePreference,setMode};};`;
const plugin={name:'actual-solid',setup(b){b.onLoad({filter:/\.tsx$/},a=>{const stripped=ts.transpileModule(fs.readFileSync(a.path,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;return{contents:babel.transformSync(stripped,{filename:a.path,presets:[[solid,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(a.path)}})}};
esbuild.build({stdin:{contents:entry,loader:'js',resolveDir:app},plugins:[plugin],loader:{'.svg':'dataurl'},bundle:true,format:'iife',platform:'browser',write:false,define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent'}).then(r=>process.stdout.write(r.outputFiles[0].text)).catch(e=>{console.error(e);process.exitCode=1});
'''
async def run_browser(D):
 bundle=subprocess.run([str(NODE),'-e',BUILD,str(R)],capture_output=True,text=True,check=True,timeout=35).stdout
 preload=(R/'apps/desktop-host/quick-surface-preload.cjs').read_text()
 out=D/'actual-window-ui';out.mkdir(exist_ok=False);results=[];workers=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True)
  try:
   for locale in ['zh-CN','en']:
    worker=await asyncio.create_subprocess_exec(str(NODE),'-e',WORKER,str(R),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    workers.append(worker);lock=asyncio.Lock()
    async def invoke(payload):
     async with lock:
      worker.stdin.write((json.dumps(payload,ensure_ascii=False)+'\n').encode());await worker.stdin.drain();row=json.loads(await asyncio.wait_for(worker.stdout.readline(),15));assert 'error' not in row,row;return row
    value=await invoke({'init':locale});page=await browser.new_page(viewport={'width':380,'height':200},bypass_csp=True);page.set_default_timeout(8000);errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)));await page.set_content(value['html'])
    await page.expose_binding('__hostInvoke',lambda _source,payload:invoke(payload))
    await page.evaluate('''({preload,value})=>{window.__calls=[];window.__listen={};window.require=()=>({ipcRenderer:{on:(ch,fn)=>__listen[ch]=fn,invoke:async(ch,payload)=>{__calls.push(payload);const response=await __hostInvoke(payload);queueMicrotask(()=>__listen['eeas:quick-surface:state']({},response.state));return response.value??response.state;}}});(0,eval)(preload);window.dispatchEvent(new Event('DOMContentLoaded'));__listen['eeas:quick-surface:state']({},value.state);}''',{'preload':preload,'value':value})
    button=page.locator('#ask-panel [data-command=pin]');editor=page.locator('#comment')
    await editor.fill('未发送的草稿 😀');await editor.evaluate('e=>{window.__originalEditor=e;e.focus()}')
    await button.click();await page.wait_for_function("document.querySelector('[data-command=pin]').getAttribute('aria-pressed')==='true'")
    assert await editor.input_value()=='未发送的草稿 😀';assert await editor.evaluate('e=>e===window.__originalEditor')
    await button.focus();await page.keyboard.press('Enter');await page.wait_for_function("document.querySelector('[data-command=pin]').getAttribute('aria-pressed')==='false'")
    rect=await button.bounding_box();assert rect and rect['x']>=0 and rect['x']+rect['width']<=380 and rect['height']>=28
    current=await invoke({'kind':'state'});assert await button.get_attribute('title')==current['state']['pinLabel']
    if locale=='en':assert await button.get_attribute('title')=='Pin chat window'
    await page.wait_for_timeout(280)
    assert await page.locator('.sheet').evaluate('e=>e.getAnimations().filter(a=>a.playState==="running").length')==0
    await editor.focus();await editor.dispatch_event('compositionstart');await editor.fill('还在组合的汉字 IME')
    await button.click();await page.wait_for_function("document.querySelector('[data-command=pin]').getAttribute('aria-pressed')==='true'")
    assert await editor.input_value()=='还在组合的汉字 IME'
    await editor.dispatch_event('compositionend')
    await page.wait_for_timeout(20)
    await page.keyboard.press('Escape')
    await page.wait_for_function("__calls.some(v=>v.command==='dismiss')")
    await page.wait_for_timeout(220)
    reopened=await invoke({'reopen':True})
    await page.evaluate("v=>__listen['eeas:quick-surface:state']({},v)",reopened['state'])
    await page.wait_for_timeout(280)
    assert await editor.input_value()=='还在组合的汉字 IME'
    assert await page.locator('.sheet').evaluate('e=>e.getAnimations().filter(a=>a.playState==="running").length')==0
    await page.screenshot(path=str(out/f'quick-{locale}.png'))
    calls=await page.evaluate('__calls');assert sum(v.get('command')=='pin' for v in calls)==3
    assert not any(v.get('command') in ['send','expand','workbench'] for v in calls)
    assert not errors,errors;results.append({'surface':'Quick','locale':locale,'pin':rect,'calls':calls})
    await page.close();await invoke({'close':True});worker.stdin.close();await asyncio.wait_for(worker.wait(),10);assert worker.returncode==0
   page=await browser.new_page(viewport={'width':430,'height':300});await page.route('http://cirven.test/**',lambda route:route.fulfill(status=200,content_type='text/html; charset=utf-8',body='<meta charset="utf-8"><div class="as-app-frame"><div id="fixture"></div><textarea id="draft">保留原稿</textarea></div>'));await page.goto('http://cirven.test/')
   await page.add_style_tag(content=(R/'apps/agent-shell/src/styles.css').read_text()+(R/'apps/agent-shell/src/agent-studio.css').read_text()+(R/'apps/agent-shell/src/brand-and-spacing.css').read_text())
   await page.add_script_tag(content=bundle);await page.evaluate("mount('zh-CN')")
   button=page.locator('.as-titleband-pin');await button.wait_for();assert await button.get_attribute('aria-pressed')=='false'
   await button.click();await page.wait_for_function("document.querySelector('.as-titleband-pin').getAttribute('aria-pressed')==='true'")
   assert await button.get_attribute('title')=='取消固定轻量窗口'
   await page.evaluate("driver.locale('en')");assert await button.get_attribute('title')=='Unpin companion window'
   await button.focus();await page.keyboard.press('Enter');assert await button.get_attribute('aria-pressed')=='false'
   assert await page.locator('#draft').input_value()=='保留原稿';rect=await button.bounding_box();(out/'titlebar-geometry.json').write_text(json.dumps(rect));assert rect and rect['height']==28 and rect['x']+rect['width']<=430-140
   await page.screenshot(path=str(out/'titlebar-en.png'));await page.evaluate("driver.setMode('workbench')");assert await button.count()==0
   results.append({'surface':'SolidTitlebar','pin':rect,'clicks':await page.evaluate('__calls'),'sameDraft':True})
   await page.close()
  finally:
   await browser.close()
   for worker in workers:
    if worker.returncode is None:
     worker.stdin.close();await asyncio.wait_for(worker.wait(),10)
 (out/'RESULT.json').write_text(json.dumps({'status':'PASS','qualification':'ACTUAL_CHROMIUM_HTML_PRELOAD_SOLID_WITH_CONTROLLED_ELECTRON_BOUNDARY_NOT_WINDOWS','results':results,'prototype_options':['A-scale','B-clip'],'model_calls':0,'windows':False},ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':'PASS','output':str(out)}))
def test_actual_quick_pin_ime_reopen_finite_reveal_and_solid_titlebar(tmp_path):
 asyncio.run(asyncio.wait_for(run_browser(tmp_path),90))
