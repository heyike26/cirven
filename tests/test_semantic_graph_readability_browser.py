"""Real Solid SemanticGraph + ObjectDraft hooks + pinned Cytoscape in Chromium.

Persistence and generic button/source-note boundaries are controlled. No Store,
API, model, native shell or whole-App qualification is claimed.
"""
import asyncio,json,os,subprocess
from pathlib import Path
import pytest
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[1]
NODE=ROOT/'.node-tools/node_modules/node/bin/node'
BUILD=r'''
const fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module');const root=process.argv[1],source=process.argv[2],req=createRequire(path.join(root,'apps/agent-shell/package.json')),esbuild=req('esbuild'),babel=req('@babel/core'),solid=createRequire(req.resolve('vite-plugin-solid'))('babel-preset-solid'),ts=req('typescript');
const src=path.join(root,'apps/agent-shell/src');
const entry=`import {render} from 'solid-js/web';import {createSignal,createComponent} from 'solid-js';import SemanticGraph from ${JSON.stringify(source)};import {ObjectDraftBoundary} from ${JSON.stringify(path.join(src,'ObjectDraft.tsx'))};import {startUILanguage,setUILanguagePreference} from ${JSON.stringify(path.join(src,'ui-language.ts'))};
window.mountGraph=async(graph,saved,fields,loading,locale)=>{window.__cores=[];window.__layoutCalls=[];window.__writes=[];const listeners=new Set();window.__owner={status:loading?'loading':'saved',view:saved?{graphs:{G01:structuredClone(saved)}}:{},fields:structuredClone(fields),subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn)},registerDefault(ch,name,value){if(!(name in this[ch]))this[ch][name]=structuredClone(value)},registerRecordDefault(name,key,value){this.fields[name]??={};this.fields[name][key]??=structuredClone(value)},value(ch,name,fallback){return this[ch][name]??fallback},set(ch,name,value){if(JSON.stringify(this[ch][name])===JSON.stringify(value))return;this[ch][name]=structuredClone(value);window.__writes.push({ch,name});for(const f of listeners)f()},flush(){return Promise.resolve()},inspect(){return Promise.resolve()}};
const [g,setGraph]=createSignal(graph);const stopLanguage=startUILanguage();await setUILanguagePreference(locale);const stop=render(()=>createComponent(ObjectDraftBoundary,{artifact:{},get children(){return createComponent(SemanticGraph,{stateId:'G01',get graph(){return g()},onFeedback:async()=>{throw Error('No feedback/model call is expected')}})}}),document.querySelector('#fixture'));
window.__driver={ready(view=saved){if(view)window.__owner.view.graphs={G01:structuredClone(view)};window.__owner.status='saved';for(const f of listeners)f()},setGraph,locale:setUILanguagePreference,dispose(){stop();stopLanguage()},state(){return structuredClone({view:window.__owner.view,fields:window.__owner.fields,writes:window.__writes})}};};`;
const plugin={name:'actual-component-fixture',setup(b){b.onResolve({filter:/^cytoscape$/},()=>({path:'cy',namespace:'fixture'}));b.onResolve({filter:/^@opencode-ai\/ui\/button$/},()=>({path:'button',namespace:'fixture'}));b.onResolve({filter:/^\.\/SourceNotes$/},()=>({path:'notes',namespace:'fixture'}));b.onResolve({filter:/^\.\/workbench-state$/},a=>a.importer.endsWith('/ObjectDraft.tsx')?({path:'draft-port',namespace:'fixture'}):undefined);
b.onLoad({filter:/.*/,namespace:'fixture'},a=>{const imports=`import {createComponent} from ${JSON.stringify(req.resolve('solid-js/web').replace('/web/dist/server.cjs','/web/dist/web.js'))};`;if(a.path==='cy')return{contents:`import actual from ${JSON.stringify(req.resolve('cytoscape'))};export default options=>{const core=actual(options);window.__cores.push(core);const layout=core.layout.bind(core);core.layout=options=>{window.__layoutCalls.push({name:options.name,boundingBox:options.boundingBox,fit:options.fit});return layout(options)};return core}`,loader:'js',resolveDir:src};if(a.path==='draft-port')return{contents:`export const artifactStateKey=()=> 'fixture';export const objectDraft=()=>window.__owner;`,loader:'js'};if(a.path==='notes')return{contents:'export default function SourceNotes(){return null}',loader:'js'};return{contents:`import {spread,insert} from 'solid-js/web';export function Button(props){const b=document.createElement('button');spread(b,props,false,true);insert(b,()=>props.children);return b}`,loader:'js',resolveDir:path.join(root,'apps/agent-shell')};});
b.onLoad({filter:/\.tsx$/},a=>{const raw=fs.readFileSync(a.path,'utf8'),stripped=ts.transpileModule(raw,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;const code=babel.transformSync(stripped,{filename:a.path,presets:[[solid,{generate:'dom',hydratable:false}]],babelrc:false,configFile:false}).code;return{contents:code,loader:'js',resolveDir:a.path===source?src:path.dirname(a.path)}});}};
esbuild.build({stdin:{contents:entry,loader:'js',resolveDir:path.join(root,'apps/agent-shell')},plugins:[plugin],bundle:true,format:'iife',platform:'browser',write:false,define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent'}).then(r=>process.stdout.write(r.outputFiles[0].text)).catch(e=>{console.error(e);process.exitCode=1});
'''

@pytest.fixture(scope='module')
def bundle():
 source=Path(os.environ.get('EEAS_GRAPH_SOURCE',ROOT/'apps/agent-shell/src/SemanticGraph.tsx'))
 result=subprocess.run([str(NODE),'-e',BUILD,str(ROOT),str(source)],capture_output=True,text=True,timeout=35)
 assert result.returncode==0,result.stderr
 return result.stdout

@pytest.fixture(scope='module')
def data():return json.loads((ROOT/'tests/fixtures/semantic-graphs.json').read_text())

METRICS=r'''()=>{const core=__cores[0];const positions=Object.fromEntries(core.nodes().map(n=>[n.id(),{...n.position()}]));return{destroyed:core.destroyed(),zoom:core.zoom(),pan:{...core.pan()},positions,selected:core.elements(':selected').map(n=>n.id()),layouts:__layoutCalls,rows:core.nodes().map(n=>({id:n.id(),raw:n.data('label'),display:n.data('display_label'),font:n.numericStyle('font-size')*core.zoom(),bbox:n.boundingBox({includeLabels:true}),position:{...n.position()}})),edges:core.edges().map(e=>({id:e.id(),source:e.data('source'),target:e.data('target'),raw:e.data('label'),display:e.data('display_label')})),...__driver.state()}}'''

async def start(bundle,graph,saved=None,fields=None,loading=False,hidden=False,locale='zh-CN'):
 p=await async_playwright().start();browser=await p.chromium.launch(headless=True);page=await browser.new_page(viewport={'width':1100,'height':1050},reduced_motion='reduce');errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 try:
  await page.route('http://cirven.test/',lambda route:route.fulfill(body='<html></html>',content_type='text/html'))
  await page.goto('http://cirven.test/')
  await page.route('**/*',lambda route:route.abort())
  await page.set_content('<style>body{margin:0;font:14px sans-serif}#fixture{width:806px}.as-graph-canvas{height:484px;position:relative}.as-cytoscape-canvas{position:absolute;inset:0;width:100%;height:100%}.as-graph-toolbar>div{display:flex;gap:8px}button,input,select{min-height:32px}.as-annotation-overlay{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.as-graph-feedback textarea{display:block;width:500px;height:70px}</style><div id="fixture"></div>')
  if hidden:await page.evaluate("document.querySelector('#fixture').style.display='none'")
  await page.add_script_tag(content=bundle)
  await page.evaluate('async args=>mountGraph(...args)',[graph,saved,fields or {},loading,locale])
  return p,browser,page,errors
 except BaseException:
  await finish(p,browser);raise

async def finish(p,browser):
 try:await asyncio.wait_for(browser.close(),timeout=8)
 finally:await p.stop()

async def painted(page):
 # Wait for the real node layer after Cytoscape's asynchronous canvas redraw.
 # A state-only test can otherwise capture a transient blank resized canvas.
 await page.wait_for_function("""()=>{const c=document.querySelector('canvas[data-id="layer2-node"]');if(!c||!c.width||!c.height)return false;const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;for(let i=3;i<d.length;i+=4)if(d[i])return true;return false;}""",timeout=5000)

async def fresh_case(bundle,data,name,tmp):
 p,b,page,errors=await start(bundle,data['graphs'][name])
 try:
  await page.wait_for_function("document.querySelector('.as-graph-canvas').getAttribute('aria-busy')==='false'")
  first=await page.evaluate(METRICS);assert first['zoom']>=1 and all(n['font']>=12 for n in first['rows']),first
  assert len(first['layouts'])==1 and first['layouts'][0]['fit'] is False,first
  assert all(len(e['display'].split('\n'))<=2 for e in first['edges']),first
  assert all(len(n['display'].split('\n'))<=3 for n in first['rows']),first
  for i,node in enumerate(first['rows']):
   for other in first['rows'][i+1:]:
    left,right=node['bbox'],other['bbox'];assert left['x2']<=right['x1'] or right['x2']<=left['x1'] or left['y2']<=right['y1'] or right['y2']<=left['y1'],(node,other)
  await painted(page);await page.screenshot(path=str(tmp/f'{name}-fresh.png'))
  longest=max(data['graphs'][name]['edges'],key=lambda edge:len(edge['relation']))
  await page.evaluate("id=>__cores[0].getElementById('edge:'+id).emit('tap')",longest['id'])
  assert await page.locator('.as-diagram-detail strong').inner_text()==longest['relation']
  for e,source in zip(first['edges'],data['graphs'][name]['edges']):assert e['raw']==source['relation'] and e['source']=='node:'+source['source'] and e['target']=='node:'+source['target'],first
  mode=page.get_by_role('combobox',name='图布局',exact=True)
  await page.evaluate('__cores[0].zoom(.15)')
  await mode.select_option('hierarchy');await page.wait_for_timeout(50);a=await page.evaluate(METRICS)
  await page.evaluate('__cores[0].zoom(.3)')
  await mode.select_option('semantic');await page.wait_for_timeout(50);c=await page.evaluate(METRICS)
  assert a['positions']==c['positions'] and a['zoom']==c['zoom'],(a,c)
  await page.get_by_role('button',name='适配视野',exact=True).click();await page.wait_for_timeout(30)
  overview=await page.evaluate(METRICS);assert overview['zoom']<1,overview
  await page.get_by_role('combobox',name='选择图节点',exact=True).select_option(data['graphs'][name]['nodes'][0]['id']);await page.wait_for_timeout(30)
  located=await page.evaluate(METRICS);assert located['zoom']>=1 and located['selected']==['node:'+data['graphs'][name]['nodes'][0]['id']],located
  before=await page.evaluate(METRICS);await page.evaluate("document.querySelector('#fixture').style.width='500px'");await page.wait_for_timeout(80);after=await page.evaluate(METRICS)
  assert before['positions']==after['positions'] and before['zoom']==after['zoom'] and before['pan']==after['pan'],(before,after)
  assert len(before['layouts'])==len(after['layouts']),after
  await painted(page);await page.screenshot(path=str(tmp/f'{name}.png'));(tmp/f'{name}.json').write_text(json.dumps({'first':first,'repeated':c,'overview':overview,'located':located,'resized':after},ensure_ascii=False,indent=2)+'\n');assert not errors,errors
 finally:await finish(p,b)

@pytest.mark.parametrize('name',['original7','proposed9'])
def test_actual_component_fresh_readable_repeated_layout_overview_locate_and_resize(bundle,data,name,tmp_path):asyncio.run(asyncio.wait_for(fresh_case(bundle,data,name,tmp_path),45))

async def restore_case(bundle,data,tmp):
 old=data['old_view'];fields={'graphFeedback':{'G01':{'comment':'保留未发文字','tool':'FREEHAND','selection':{'graph_id':'G01','node_ids':['N03'],'normalized_points':[{'x':.1,'y':.2},{'x':.3,'y':.4}],'viewport':{'width':806,'height':484,'zoom':old['zoom'],'pan':old['pan']}}}}}
 p,b,page,errors=await start(bundle,data['graphs']['original7'],saved=None,fields=fields,loading=True,hidden=True)
 try:
  await page.wait_for_timeout(50);assert await page.evaluate('__layoutCalls.length')==0
  await page.evaluate('view=>__driver.ready(view)',old);await page.wait_for_timeout(50);assert await page.evaluate('__layoutCalls.length')==0
  await page.evaluate("document.querySelector('#fixture').style.display='block'");await page.wait_for_function("document.querySelector('.as-graph-canvas').getAttribute('aria-busy')==='false'")
  restored=await page.evaluate(METRICS);assert restored['positions']==old['positions'] and restored['zoom']==old['zoom'] and restored['pan']==old['pan'] and restored['selected']==old['selected'],restored
  assert restored['fields']==fields and restored['layouts']==[] and not restored['writes'],restored
  assert await page.evaluate("__cores[0].edges().filter(e=>Math.abs(e.source().position('y')-e.target().position('y'))>=1e-5).every(e=>e.numericStyle('text-max-width')===140)"),restored
  assert await page.get_by_role('textbox',name='搜索图节点',exact=True).input_value()==old['search']
  mode=page.get_by_role('combobox',name='图布局',exact=True);await mode.select_option('hierarchy');assert await mode.input_value()==old['mode']
  await page.get_by_role('alert').get_by_text('先发送或清除批注，再调整布局。',exact=True).wait_for();blocked=await page.evaluate(METRICS);assert blocked['fields']==fields and blocked['positions']==old['positions'] and blocked['layouts']==[],blocked
  await page.evaluate("__driver.locale('en')");await page.get_by_role('alert').get_by_text('Send or clear the annotation before changing the layout.',exact=True).wait_for()
  await page.evaluate("document.querySelector('#fixture').style.width='500px'");await page.wait_for_timeout(80);resized=await page.evaluate(METRICS);assert resized['zoom']==old['zoom'] and resized['pan']==old['pan'] and resized['positions']==old['positions'] and resized['fields']==fields,resized
  await page.get_by_role('button',name='Clear drawing',exact=True).click();await page.get_by_role('combobox',name='Graph layout',exact=True).select_option('hierarchy');await page.wait_for_timeout(50);changed=await page.evaluate(METRICS)
  assert changed['zoom']>=1 and changed['fields']['graphFeedback']['G01']['comment']=='保留未发文字' and changed['fields']['graphFeedback']['G01']['selection']['normalized_points']==[],changed
  await painted(page);await page.screenshot(path=str(tmp/'restore.png'));(tmp/'restore.json').write_text(json.dumps({'restored':restored,'blocked':blocked,'resized':resized,'explicit_clear_relayout':changed},ensure_ascii=False,indent=2)+'\n');assert not errors,errors
 finally:await finish(p,b)

def test_async_hidden_restore_preserves_exact_old_view_and_unsent_annotation_until_explicit_clear(bundle,data,tmp_path):asyncio.run(asyncio.wait_for(restore_case(bundle,data,tmp_path),45))

async def disposal_case(bundle,data,tmp):
 p,b,page,errors=await start(bundle,data['graphs']['original7'],loading=True,hidden=True)
 try:
  await page.evaluate('__driver.dispose();__driver.ready()');await page.wait_for_timeout(100);assert await page.evaluate('__cores[0].destroyed()') is True;assert await page.evaluate('__layoutCalls.length')==0;assert not errors,errors
 finally:await finish(p,b)

def test_unmount_before_async_restore_and_visibility_does_not_layout_or_write_late(bundle,data,tmp_path):asyncio.run(asyncio.wait_for(disposal_case(bundle,data,tmp_path),30))
