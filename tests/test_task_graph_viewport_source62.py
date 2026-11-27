"""Actual TaskGraph/ObjectDraft/Cytoscape/ELK canvas; controlled HTTP persistence.

No App, native Windows, model or backend-authority qualification is implied.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from legacy_evidence import evidence_folder
import subprocess

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'
def graph_output():
    return evidence_folder('task-graph', 'EEAS_GRAPH_EVIDENCE')
BUILD = r'''
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{createRequire}=require('node:module');
const root=process.argv[1],out=process.argv[2],baseline=process.argv[3],app=path.join(root,'apps/agent-shell'),src=path.join(app,'src'),req=createRequire(path.join(app,'package.json'));
const esbuild=req('esbuild'),ts=req('typescript'),babel=req('@babel/core'),solid=createRequire(req.resolve('vite-plugin-solid'))('babel-preset-solid'),inputs={};
const record=p=>{const raw=fs.readFileSync(p);inputs[p]={bytes:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex')};return raw.toString('utf8')};
const entry=`import {render} from 'solid-js/web';import {createComponent,createSignal} from 'solid-js';
import TaskGraph from '${path.join(src,'TaskGraph.tsx')}';import {ObjectDraftBoundary,useDraftSession} from '${path.join(src,'ObjectDraft.tsx')}';
import {startUILanguage,setUILanguagePreference} from '${path.join(src,'ui-language.ts')}';
const NativeWorker=window.Worker;window.Worker=class extends NativeWorker{constructor(...args){super(...args);window.__workers++;}};
window.mountTasks=async(nodes,revision=3)=>{window.__cores=[];window.__layoutCalls=[];window.__workers=0;const [items,setItems]=createSignal(nodes),[selected,setSelected]=createSignal('A');
function Content(){window.__owner=useDraftSession().session;return createComponent(TaskGraph,{get nodes(){return items()},states:[],get selectedId(){return selected()},onSelect:setSelected,stateId:'qa62-tasks'});}
const stopLanguage=startUILanguage();await setUILanguagePreference('zh-CN');const stop=render(()=>createComponent(ObjectDraftBoundary,{artifact:{type:'proposal',artifactId:'qa62-proposal',revision,contentHash:(revision===3?'a':'b').repeat(64),scope:{projectId:'qa62-project'}},get children(){return createComponent(Content,{})}}),document.querySelector('#fixture'));
window.__driver={setItems,locale:setUILanguagePreference,dispose(){stop();stopLanguage()}};};`;
const plugin={name:'actual-taskgraph-fixture',setup(b){b.onResolve({filter:/^cytoscape$/},()=>({path:'cy',namespace:'fixture'}));
b.onResolve({filter:/elk-worker\.min\.js\?url$/},()=>({path:'worker',namespace:'fixture'}));
b.onLoad({filter:/.*/,namespace:'fixture'},a=>a.path==='worker'?({contents:`export default '/assets/elk-worker.min.js'`,loader:'js'}):({contents:`import actual from ${JSON.stringify(req.resolve('cytoscape'))};export default options=>{const c=actual(options);window.__cores.push(c);const layout=c.layout.bind(c);c.layout=o=>{window.__layoutCalls.push(o.name);return layout(o)};return c}`,loader:'js',resolveDir:app}));
b.onLoad({filter:/\.(tsx|ts)$/},a=>{const relative=path.relative(root,a.path),old=baseline?path.join(baseline,relative):null,actual=old&&fs.existsSync(old)?old:a.path,raw=record(actual);if(a.path.endsWith('.ts'))return{contents:raw,loader:'ts',resolveDir:path.dirname(a.path)};const stripped=ts.transpileModule(raw,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;return{contents:babel.transformSync(stripped,{filename:a.path,presets:[[solid,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(a.path)}});
b.onLoad({filter:/task-network\.css$/},a=>({contents:record(baseline?path.join(baseline,path.relative(root,a.path)):a.path),loader:'css'}));}};
fs.mkdirSync(out,{recursive:true});esbuild.build({stdin:{contents:entry,loader:'js',resolveDir:app},plugins:[plugin],bundle:true,format:'iife',platform:'browser',write:false,outdir:out,metafile:true,define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent'}).then(result=>{for(const f of result.outputFiles)fs.writeFileSync(f.path,f.contents);for(const name of Object.keys(result.metafile.inputs)){const p=path.resolve(root,name);if(fs.existsSync(p)&&fs.statSync(p).isFile()&&!inputs[p])record(p);}fs.writeFileSync(path.join(out,'INPUTS.json'),JSON.stringify(inputs,null,2));console.log(JSON.stringify({files:result.outputFiles.map(f=>f.path),inputs:Object.keys(inputs).length}));}).catch(e=>{console.error(e);process.exitCode=1});
'''

NODES = [
    {'node_id': 'A', 'role': '解析当前原创资料', 'dependency_node_ids': [], 'description': '核对来源，不丢人工说明。'},
    {'node_id': 'B', 'role': '形成可核对报告', 'dependency_node_ids': ['A']},
    {'node_id': 'C', 'role': '复核任务与交付依据', 'dependency_node_ids': ['B']},
]
FIELDS = {'graphFeedback': {'qa62': {'comment': '原创未发送批注：先核对中文关系，再交付。', 'tool': 'COMMENT', 'selection': {'graph_id': 'qa62', 'node_ids': ['A'], 'normalized_points': []}}}}
POSITIONS = {'A': {'x': 140, 'y': 80}, 'B': {'x': 140, 'y': 272}, 'C': {'x': 140, 'y': 464}}


@pytest.fixture(scope='module')
def bundle():
    graph_output().mkdir(parents=True, exist_ok=True)
    build = graph_output() / 'bundle'
    result = subprocess.run([str(NODE), '-e', BUILD, str(ROOT), str(build), os.environ.get('EEAS_GRAPH_BASELINE_ROOT', '')], capture_output=True, text=True, timeout=35)
    (graph_output() / 'compile.stdout').write_text(result.stdout)
    (graph_output() / 'compile.stderr').write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    return build


METRICS = r'''()=>{const c=__cores[0],z=c.zoom(),p=c.pan();return {zoom:z,pan:{...p},viewport:{width:c.width(),height:c.height()},center:{x:(c.width()/2-p.x)/z,y:(c.height()/2-p.y)/z},positions:Object.fromEntries(c.nodes().map(n=>[n.id(),{...n.position()}])),selected:c.nodes(':selected').map(n=>n.id()),nodes:c.nodes().map(n=>({id:n.id(),rendered:n.renderedBoundingBox({includeLabels:true}),font:n.numericStyle('font-size')*z,label:n.data('display_label')})),layouts:__layoutCalls.length,workers:__workers,owner:{status:__owner.status,fields:__owner.fields,view:__owner.view},edges:c.edges().map(e=>({id:e.id(),source:e.source().id(),target:e.target().id(),label:e.style('label')}))}}'''


async def start(bundle, saved=None, *, hidden=False, hold=False, width=1300, nodes=None, embedded=False):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'), headless=True)
    page = await browser.new_page(viewport={'width': 1500, 'height': 1108 if embedded else 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    released = asyncio.Event()
    if not hold:
        released.set()
    state = {'status': 'SAVED', 'schema_version': 1, 'state_revision': 1, 'key': None, 'fields': FIELDS, 'view': {'graphs': {'qa62-tasks:readable-v2': saved}} if saved else {}, 'dirty': True, 'sha256': 'd' * 64}
    requests = []

    async def route(r):
        url = r.request.url
        if url.endswith('/assets/elk-worker.min.js'):
            return await r.fulfill(body=(ROOT / 'packages/adapters/diagram_layout_assets/elk-worker.min.js').read_bytes(), content_type='text/javascript')
        if url.endswith('/api/bootstrap'):
            return await r.fulfill(json={'csrf_token': 'qa62-explicit-controlled-port'})
        if url.endswith('/workbench-state/load'):
            requests.append({'method': 'load'})
            await released.wait()
            key = r.request.post_data_json['key']
            if key['revision'] != 3:
                return await r.fulfill(json={**state, 'status': 'EMPTY', 'key': key, 'state_revision': 0, 'fields': {}, 'view': {}})
            state['key'] = key
            return await r.fulfill(json=state)
        if url.endswith('/workbench-state/save'):
            body = r.request.post_data_json
            requests.append({'method': 'save', 'body': body})
            state.update({'key': body['key'], 'state_revision': body['expected_revision'] + 1, 'fields': body['fields'], 'view': body['view'], 'dirty': body['dirty']})
            return await r.fulfill(json={**state, 'operation_id': body['operation_id']})
        if url == 'http://127.0.0.1:17862/':
            return await r.fulfill(body='<html></html>', content_type='text/html')
        raise AssertionError('Unexpected external request ' + url)

    await page.route('**/*', route)
    await page.goto('http://127.0.0.1:17862/')
    css = (bundle / 'stdin.css').read_text()
    host = '<div id="inspector" style="height:1108px;overflow:auto;width:900px"><div style="height:525px"></div><div id="fixture"></div></div>' if embedded else '<div id="fixture"></div>'
    await page.set_content('<style>body{margin:0;background:#fff;font:14px "Noto Sans CJK SC",sans-serif}*{box-sizing:border-box}button{border:0;background:transparent}#fixture{width:' + str(width) + 'px}' + css + '</style>' + host)
    if hidden:
        await page.evaluate("document.querySelector('#fixture').style.display='none'")
    await page.add_script_tag(content=(bundle / 'stdin.js').read_text())
    await page.evaluate('nodes=>mountTasks(nodes)', nodes or NODES)
    return playwright, browser, page, errors, released, state, requests


async def finish(p, browser):
    try:
        await asyncio.wait_for(browser.close(), timeout=8)
    finally:
        await p.stop()


async def ready(page):
    await page.wait_for_function("document.querySelector('.as-task-network')?.dataset.layoutReady==='true'", timeout=10000)
    await page.wait_for_function("()=>{const canvas=document.querySelector('canvas[data-id=\"layer2-node\"]');if(!canvas?.width)return false;return canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data.some((v,i)=>i%4===3&&v>0)}", timeout=5000)


def same_camera_center(before, after):
    return abs(before['center']['x'] - after['center']['x']) < 0.05 and abs(before['center']['y'] - after['center']['y']) < 0.05


async def resize_case(bundle):
    saved = {'zoom': 1, 'pan': {'x': 510, 'y': 30}, 'positions': POSITIONS, 'selected': ['A'], 'mode': 'flow-down', 'search': ''}
    p, b, page, errors, _, _, _ = await start(bundle, saved)
    try:
        await ready(page)
        before = await page.evaluate(METRICS)
        await page.screenshot(path=str(graph_output() / 'resize-before.png'))
        await page.evaluate("document.querySelector('#fixture').style.width='684px'")
        await page.wait_for_timeout(100)
        await ready(page)
        after = await page.evaluate(METRICS)
        await page.screenshot(path=str(graph_output() / 'resize-after.png'))
        (graph_output() / 'RESIZE.json').write_text(json.dumps({'before': before, 'after': after}, ensure_ascii=False, indent=2))
        assert same_camera_center(before, after), 'Actual TaskGraph resize moved original model center: ' + str((before['center'], after['center']))
        assert before['zoom'] == after['zoom'] and before['positions'] == after['positions']
        assert after['nodes'][0]['rendered']['x1'] >= 0 and after['nodes'][0]['rendered']['x2'] <= after['viewport']['width']
        assert before['owner']['fields'] == after['owner']['fields'] == FIELDS
        assert after['layouts'] == before['layouts'] == 0 and not errors, errors
    finally:
        await finish(p, b)


def test_real_taskgraph_old_wide_to_narrow_keeps_model_center_and_painted_target(bundle):
    asyncio.run(asyncio.wait_for(resize_case(bundle), 35))


async def restore_case(bundle):
    saved = {'zoom': 1.15, 'pan': {'x': 488, 'y': 4}, 'positions': POSITIONS, 'selected': ['B'], 'mode': 'flow-down', 'search': '报告', 'viewport': {'width': 1298, 'height': 640}}
    p, b, page, errors, release, state, requests = await start(bundle, saved, hidden=True, hold=True, width=684)
    try:
        await page.wait_for_timeout(100)
        assert await page.evaluate('__cores[0].nodes().length') == 3
        assert await page.locator('.as-task-network').get_attribute('data-layout-ready') == 'false'
        assert not [r for r in requests if r['method'] == 'save']
        release.set()
        await page.wait_for_timeout(80)
        assert await page.locator('.as-task-network').get_attribute('data-layout-ready') == 'false'
        await page.evaluate("document.querySelector('#fixture').style.display='block'")
        await ready(page)
        first = await page.evaluate(METRICS)
        assert abs(first['center']['x'] - (1298/2-488)/1.15) < .05
        assert abs(first['center']['y'] - (640/2-4)/1.15) < .05
        assert first['positions'] == POSITIONS and first['zoom'] == 1.15 and first['layouts'] == 0 and first['workers'] == 0
        assert first['owner']['fields'] == FIELDS
        canvas = page.locator('.as-task-canvas')
        box = await canvas.bounding_box()
        await page.mouse.move(box['x'] + 40, box['y'] + 500)
        await page.mouse.down()
        await page.mouse.move(box['x'] + 81, box['y'] + 519, steps=5)
        await page.mouse.up()
        await page.mouse.move(box['x'] + 85, box['y'] + 400)
        await page.mouse.wheel(0, -100)
        await page.wait_for_timeout(300)
        manual = await page.evaluate(METRICS)
        assert manual['pan'] != first['pan'] and manual['zoom'] > first['zoom'], (first, manual)
        assert manual['positions'] == first['positions'] and manual['owner']['fields'] == FIELDS
        await page.evaluate("document.querySelector('#fixture').style.display='none'")
        await page.wait_for_timeout(50)
        await page.evaluate("document.querySelector('#fixture').style.width='910px'")
        await page.evaluate("document.querySelector('#fixture').style.display='block'")
        await ready(page)
        after = await page.evaluate(METRICS)
        assert same_camera_center(manual, after) and manual['zoom'] == after['zoom'], (manual, after)
        assert after['positions'] == POSITIONS and after['owner']['fields'] == FIELDS and after['layouts'] == 0
        await page.wait_for_function("v=>__owner.status==='saved'&&JSON.stringify(__owner.view.graphs['qa62-tasks:readable-v2'].viewport)===JSON.stringify(v)", arg=after['viewport'], timeout=5000)
        assert state['view']['graphs']['qa62-tasks:readable-v2']['viewport'] == after['viewport']
        assert state['view']['graphs']['qa62-tasks:readable-v2']['pan'] == after['pan']
        await page.screenshot(path=str(graph_output() / 'manual-hidden-visible.png'))
        (graph_output() / 'RESTORE.json').write_text(json.dumps({'first': first, 'manual': manual, 'after': after}, ensure_ascii=False, indent=2))
        # A new proposal basis gets a separate real ObjectDraftSession; no camera or annotation carry.
        original = json.loads(json.dumps(state))
        await page.evaluate('__driver.dispose()')
        await page.evaluate('nodes=>mountTasks(nodes,4)', NODES)
        await ready(page)
        fresh = await page.evaluate(METRICS)
        assert fresh['layouts'] == 0 and fresh['workers'] == 1 and fresh['zoom'] == 1 and fresh['owner']['fields'] == {}
        assert state['view'] == original['view'] and state['fields'] == original['fields']
        assert not errors, errors
    finally:
        await finish(p, b)


def test_hidden_async_exact_view_restores_then_manual_input_and_resize_preserve_drafts_and_new_basis(bundle):
    asyncio.run(asyncio.wait_for(restore_case(bundle), 45))


async def overview_case(bundle):
    nodes = [{'node_id': chr(65+i), 'role': '核对中文资料与关系' + str(i+1), 'dependency_node_ids': [chr(64+i)] if i else []} for i in range(7)]
    p, b, page, errors, _, _, _ = await start(bundle, None, width=684, nodes=nodes)
    try:
        await ready(page)
        initial = await page.evaluate(METRICS)
        assert initial['zoom'] == 1 and initial['nodes'][0]['font'] == 13
        assert all(n['rendered']['x1'] >= 0 and n['rendered']['x2'] <= initial['viewport']['width'] for n in initial['nodes'])
        # Same selected node can be intentionally offscreen; resize must preserve that intention.
        await page.evaluate('__cores[0].pan({x:1400,y:50})')
        await page.wait_for_timeout(60)
        await page.evaluate("__driver.locale('en')")
        await page.locator('.as-task-find').get_by_role('button', name='Readable view', exact=True).click()
        await page.evaluate("__driver.locale('zh-CN')")
        await page.wait_for_timeout(60)
        readable = await page.evaluate(METRICS)
        assert readable['zoom'] == 1
        target = readable['nodes'][0]['rendered']
        assert target['x1'] >= 0 and target['x2'] <= readable['viewport']['width']
        await page.screenshot(path=str(graph_output() / 'readable-chinese.png'))
        await page.get_by_role('button', name='全图', exact=True).click()
        await page.wait_for_timeout(60)
        overview = await page.evaluate(METRICS)
        assert overview['zoom'] < .75 and overview['workers'] == 1
        assert all(n['rendered']['x1'] >= 0 and n['rendered']['x2'] <= overview['viewport']['width'] and n['rendered']['y1'] >= 0 and n['rendered']['y2'] <= overview['viewport']['height'] for n in overview['nodes'])
        assert overview['positions'] == readable['positions']
        assert '依赖关系 → 后续任务' in await page.locator('.as-task-footer').inner_text()
        assert all(edge.get('label') in ('', None) for edge in overview['edges'])
        await page.screenshot(path=str(graph_output() / 'overview-chinese.png'))
        (graph_output() / 'OVERVIEW.json').write_text(json.dumps({'initial': initial, 'readable': readable, 'overview': overview}, ensure_ascii=False, indent=2))
        assert not errors, errors
    finally:
        await finish(p, b)


def test_fresh_readable_view_explicit_target_recovery_and_whole_overview_are_separate_painted_modes(bundle):
    asyncio.run(asyncio.wait_for(overview_case(bundle), 35))


async def embedded_case(bundle):
    # Exact original Root61 camera. No viewport witness is invented for the old record.
    saved = {'zoom': 1, 'pan': {'x': 376, 'y': 10.25}, 'positions': {'A': {'x': 140, 'y': 72}, 'B': {'x': 140, 'y': 256}, 'C': {'x': 140, 'y': 440}}, 'selected': ['A'], 'mode': 'flow-down', 'search': ''}
    p, b, page, errors, _, _, _ = await start(bundle, saved, width=589, embedded=True)
    observation = r'''()=>{const button=document.querySelector('.as-task-find button[type="submit"]'),box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,left:r.left,top:r.top,right:r.right,bottom:r.bottom}},clip=e=>{const b=box(e),v={left:Math.max(0,b.left),top:Math.max(0,b.top),right:Math.min(innerWidth,b.right),bottom:Math.min(innerHeight,b.bottom)};for(let p=e.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),r=p.getBoundingClientRect();if(/(auto|scroll|hidden|clip)/.test(s.overflowX)){v.left=Math.max(v.left,r.left+p.clientLeft);v.right=Math.min(v.right,r.left+p.clientLeft+p.clientWidth)}if(/(auto|scroll|hidden|clip)/.test(s.overflowY)){v.top=Math.max(v.top,r.top+p.clientTop);v.bottom=Math.min(v.bottom,r.top+p.clientTop+p.clientHeight)}}return {...v,width:Math.max(0,v.right-v.left),height:Math.max(0,v.bottom-v.top)}};return {button:{disabled:button.disabled,text:button.textContent,aria:button.getAttribute('aria-label'),box:box(button),clip:clip(button)},footer:{box:box(document.querySelector('.as-task-footer')),clip:clip(document.querySelector('.as-task-footer'))},canvas:{box:box(document.querySelector('.as-task-canvas')),clip:clip(document.querySelector('.as-task-canvas'))},scroll:document.querySelector('#inspector').scrollTop,rootScroll:document.scrollingElement.scrollTop}}'''
    try:
        await ready(page)
        await page.wait_for_timeout(60)
        before = await page.evaluate(METRICS)
        actual = await page.evaluate(observation)
        await page.screenshot(path=str(graph_output() / 'embedded-before.png'))
        (graph_output() / 'EMBEDDED.json').write_text(json.dumps({'before': before, 'visible': actual}, ensure_ascii=False, indent=2))
        assert before['pan'] == saved['pan'] and before['zoom'] == 1
        actual_viewport = await page.locator('.as-task-canvas').evaluate('e=>({width:e.clientWidth,height:e.clientHeight})')
        assert before['viewport'] == actual_viewport
        assert actual_viewport['width'] == 587 and actual_viewport['height'] > 0
        assert before['nodes'][0]['rendered']['x2'] > 587 and actual['footer']['clip']['height'] == 0
        assert not actual['button']['disabled'], 'Original top Locate is disabled while only footer recovery is outside the viewport'
        assert actual['button']['aria'] == '恢复可读视图'
        box, clip = actual['button']['box'], actual['button']['clip']
        assert abs(clip['width'] - box['width']) < 1 and abs(clip['height'] - box['height']) < 1
        # A real coordinate click: no Playwright locator scroll-into-view can conceal the defect.
        await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
        await page.wait_for_timeout(80)
        after = await page.evaluate(METRICS)
        visible_after = await page.evaluate(observation)
        target = after['nodes'][0]['rendered']
        assert 0 <= target['x1'] and target['x2'] <= 587 and after['zoom'] == 1
        assert after['positions'] == before['positions'] and after['owner']['fields'] == before['owner']['fields']
        assert visible_after['scroll'] == actual['scroll'] == 0 and visible_after['rootScroll'] == actual['rootScroll'] == 0
        canvas = visible_after['canvas']
        assert canvas['box']['left'] + target['x1'] >= canvas['clip']['left']
        assert canvas['box']['left'] + target['x2'] <= canvas['clip']['right']
        assert canvas['box']['top'] + target['y1'] >= canvas['clip']['top']
        assert canvas['box']['top'] + target['y2'] <= canvas['clip']['bottom']
        assert await page.locator('.as-task-footer button').count() == 0
        await page.screenshot(path=str(graph_output() / 'embedded-recovered.png'))
        # A nonempty query retains the original search semantics instead of locating A.
        await page.get_by_role('textbox', name='搜索图中节点', exact=True).fill('C')
        await page.locator('.as-task-find button[type="submit"]').click()
        await page.wait_for_timeout(80)
        searched = await page.evaluate(METRICS)
        assert searched['selected'] == ['C'] and searched['zoom'] == 1
        await page.screenshot(path=str(graph_output() / 'embedded-after.png'))
        (graph_output() / 'EMBEDDED.json').write_text(json.dumps({'before': before, 'visible_before': actual, 'after': after, 'visible_after': visible_after, 'query_C': searched}, ensure_ascii=False, indent=2))
        assert not errors, errors
    finally:
        await finish(p, b)


def test_legacy_clipped_task_recovers_from_visible_top_control_without_scrolling_inspector(bundle):
    asyncio.run(asyncio.wait_for(embedded_case(bundle), 35))
