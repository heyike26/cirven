"""Actual Chromium + pinned FileTree/CSS, with a controlled outer-pane boundary.

No API/native/window qualification: Root separately checks the built application.
The fixture loads the actual fonts and late shared rules so a local-looking CSS
reset cannot pass while the production input selector would still override it.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'
EXTRACT = r"""
const fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module');
const root=process.argv[1],source=process.argv[2],req=createRequire(path.join(root,'apps/agent-shell/package.json')),ts=req('typescript'),esbuild=req('esbuild');
const text=fs.readFileSync(source,'utf8'),ast=ts.createSourceFile(source,text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let shadow,options,observe,block,inputHandler;const parts=new Map();
function walk(n){
 if(ts.isVariableDeclaration(n)&&n.name.getText(ast)==='shadowCSS')shadow=n.initializer.text;
 if(ts.isVariableDeclaration(n)&&n.name.getText(ast)==='blockUnavailable')block='const '+n.getText(ast)+';';
 if(ts.isFunctionDeclaration(n)&&n.name&&['decoration','markRows','scheduleMarks'].includes(n.name.text))parts.set(n.name.text,n.getText(ast));
 if(ts.isNewExpression(n)&&n.expression.getText(ast)==='FileTree')options=n.arguments[0].getText(ast);
 if(ts.isCallExpression(n)&&n.expression.getText(ast)==='mutation.observe')observe=n.arguments[1].getText(ast);
 if(ts.isJsxAttribute(n)&&n.name.getText(ast)==='onInput')inputHandler=n.initializer.expression.getText(ast);
 ts.forEachChild(n,walk);
}walk(ast);
const explorer=path.join(root,'apps/agent-shell/src/explorer.ts'),e=fs.readFileSync(explorer,'utf8'),ea=ts.createSourceFile(explorer,e,ts.ScriptTarget.Latest,true);
function helpers(n){if(ts.isVariableDeclaration(n)&&['canOpen','changeLabel','treePath'].includes(n.name.getText(ea)))parts.set(n.name.getText(ea),'const '+n.getText(ea)+';');if(ts.isFunctionDeclaration(n)&&n.name?.text==='statusDecorations')parts.set(n.name.text,n.getText(ea).replace(/^export\s+/,''));ts.forEachChild(n,helpers);}helpers(ea);
if(!shadow||!options||!observe||!block||!inputHandler||parts.size!==7)throw Error('Actual explorer source contract changed');
const sourceCode=`import {FileTree} from ${JSON.stringify(path.join(root,'apps/agent-shell/node_modules/@pierre/trees/dist/index.js'))};
window.mountExplorer=async(copy,initialLocale)=>{
 const host=document.querySelector('.as-explorer-host'),events={opened:[],selected:[],observerCallbacks:0,guarded:false};let locale=initialLocale,markQueued=false,tree,model,suppress=false;
 const rows=[{path:'delivery',type:'directory',change:'ADDED'}, {path:'01-目标与范围.md',change:'UNCHANGED'}, {path:'02-过程与约束.txt',change:'MODIFIED'}, {path:'03-验收与未决事项.md',change:'UNCHANGED'}, {path:'04-用户指定任务书模板.docx',change:'UNVERIFIED'}, {path:'deleted.md',change:'DELETED'}].map(e=>({...e,type:e.type??'file',readable:e.change!=='DELETED',deleted:e.change==='DELETED',verification:'CONTENT_SHA256'}));
 const store={entry:path=>rows.find(e=>e.path===path.replace(/\\/$/,''))};
 const uiText=(key,params={})=>(copy[key]?.[locale]??key).replace(/\\{([^{}]+)\\}/g,(m,k)=>String(params[k]??m));
 const props={onSelection:paths=>events.selected=paths,onOpen:entry=>events.opened.push(entry.path)};
 const directoryPath=path=>path.endsWith('/')?path.slice(0,-1):path;
 ${[...parts.values()].join(String.fromCharCode(10))}
 const shadowCSS=${JSON.stringify(shadow)},options=${options};options.paths=rows.map(treePath);tree=new FileTree(options);tree.render({containerWrapper:host});tree.setGitStatus(statusDecorations(rows));
 const setQuery=value=>events.query=value,onInput=${inputHandler};document.querySelector('.as-explorer-search input').addEventListener('input',onInput);
 ${block}host.addEventListener('click',blockUnavailable,true);host.addEventListener('keydown',blockUnavailable,true);
 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 const root=tree.getFileTreeContainer().shadowRoot;const observer=new MutationObserver(()=>{events.observerCallbacks++;if(events.observerCallbacks>1000){events.guarded=true;observer.disconnect();return;}scheduleMarks();});observer.observe(root,${observe});markRows();
 const setLocale=value=>{locale=value;document.documentElement.lang=value;document.querySelector('.as-open-folder').lastChild.textContent=uiText('打开工作目录');document.querySelector('.as-explorer-search input').placeholder=uiText('搜索已加载文件');scheduleMarks();};setLocale(locale);
 window.ownExplorer={tree,events,root,setLocale,dispose:()=>{observer.disconnect();tree.cleanUp();}};
};`;
const result=esbuild.buildSync({stdin:{contents:sourceCode,loader:'ts',resolveDir:path.join(root,'apps/agent-shell')},bundle:true,format:'iife',platform:'browser',write:false,define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent'});
console.log(JSON.stringify({js:result.outputFiles[0].text}));
"""


@pytest.fixture(scope='module')
def fixture_data():
    before = os.environ.get('EEAS_FILEPANE_BEFORE_DIR')
    override = Path(before) if before else ROOT
    local = override/'apps/agent-shell/src/LocalExplorer.tsx'
    result = subprocess.run([str(NODE), '-e', EXTRACT, str(ROOT), str(local)],
                            capture_output=True, text=True, check=True, timeout=30)
    copy = {}
    for name in ['renderer', 'renderer-agent']:
        copy.update(json.loads((override/'packages/i18n'/f'{name}.json').read_text())['messages'])
    fonts = {}
    styles = []
    for name in ['styles.css', 'native-collaboration.css', 'agent-studio.css', 'brand-and-spacing.css']:
        path = ROOT/'apps/agent-shell/src'/name
        def replace_font(match):
            target = (path.parent/match[1]).resolve()
            if target.suffix not in ('.woff2', '.ttf'):
                return match[0]
            key = f'http://cirven.test/font/{len(fonts)}'
            fonts[key] = target
            return f'url("{key}")'
        styles.append(re.sub(r'url\([\'"]?([^\)\'\"]+)[\'"]?\)', replace_font, path.read_text()))
    if not before:
        styles.append((ROOT/'apps/agent-shell/src/explorer-layout.css').read_text())
    return {**json.loads(result.stdout), 'copy': copy, 'css': '\n'.join(styles), 'fonts': fonts}


METRICS = r"""()=>{
 const root=ownExplorer.root,rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
 const folder=document.querySelector('.as-open-folder'),search=document.querySelector('.as-explorer-search'),input=search.querySelector('input'),host=document.querySelector('.as-explorer-host');
 const range=document.createRange();range.selectNodeContents(folder.lastChild);const labelLines=[...range.getClientRects()].map(r=>({x:r.x,y:r.y,right:r.right,bottom:r.bottom}));
 const canvas=document.createElement('canvas'),context=canvas.getContext('2d');context.font=getComputedStyle(input).font;
 function visibleCharacters(content){let text='';for(const part of content.querySelectorAll('[data-truncate-content=visible]')){const walker=document.createTreeWalker(part,NodeFilter.SHOW_TEXT);let n;while(n=walker.nextNode()){for(let i=0;i<n.length;i++){const r=document.createRange();r.setStart(n,i);r.setEnd(n,i+1);const box=r.getBoundingClientRect();let left=box.left,right=box.right;for(let p=part;p&&p!==content.parentElement;p=p.parentElement){if(['hidden','clip','auto','scroll'].includes(getComputedStyle(p).overflowX)){const bound=p.getBoundingClientRect();left=Math.max(left,bound.left);right=Math.min(right,bound.right);}}if(right-left>=box.width*.9)text+=n.textContent[i];}}}return text;}
 return {folder:rect(folder),labelLines,search:rect(search),input:{...rect(input),padding:getComputedStyle(input).padding,fontSize:getComputedStyle(input).fontSize,placeholder:input.placeholder,placeholderWidth:context.measureText(input.placeholder).width},host:rect(host),events:ownExplorer.events,
 rows:[...root.querySelectorAll('button[data-item-path]')].map(row=>{const content=row.querySelector('[data-item-section=content]'),decoration=row.querySelector('[data-item-section=decoration]'),git=row.querySelector('[data-item-section=git]');let textWidth=0;if(decoration?.textContent){const range=document.createRange();range.selectNodeContents(decoration);textWidth=range.getBoundingClientRect().width;}return {path:row.dataset.itemPath,...rect(row),content:rect(content),visible:visibleCharacters(content),title:row.title,aria:row.getAttribute('aria-label'),disabled:row.getAttribute('aria-disabled'),font:getComputedStyle(row).fontFamily,fontSize:getComputedStyle(row).fontSize,decoration:decoration?{...rect(decoration),text:decoration.textContent,textWidth,display:getComputedStyle(decoration).display}:null,gitDisplay:git?getComputedStyle(git).display:null,color:getComputedStyle(content).color,line:getComputedStyle(content).textDecorationLine};})};
}"""


async def run_fixture(data, width, locale, tmp_path, interactions=False):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1000, 'height': 760})
            async def assets(route):
                path = data['fonts'].get(route.request.url)
                if path:
                    await route.fulfill(path=str(path), content_type='font/woff2' if path.suffix == '.woff2' else 'font/ttf')
                else:
                    await route.abort()
            await page.route('**/*', assets)
            folder = data['copy']['打开工作目录'][locale]
            # Controlled outer-pane markup: actual classes/roles and actual CSS,
            # while the FileTree implementation, source options and handlers are real.
            html = f'''<!doctype html><html lang="{locale}"><head><style>{data['css']}</style></head><body><div class="as-app-frame"><div class="as-shell" data-layout="workbench" style="display:flex"><aside class="as-sidebar" style="width:{width}px;flex:none"><nav class="as-activity-rail"></nav><section class="as-file-pane"><button class="as-open-folder" data-component="button" data-size="normal"><span data-component="icon" aria-hidden="true">□</span>{folder}</button><section class="as-explorer"><header class="as-tree-header"><strong>Files</strong><button aria-label="Refresh">↻</button></header><label class="as-explorer-search"><span data-component="icon" aria-hidden="true">⌕</span><input aria-label="Search the expanded file tree"/></label><div class="as-explorer-host"></div></section></section></aside></div></div></body></html>'''
            await page.set_content(html)
            await page.add_script_tag(content=data['js'])
            await page.evaluate('async ({copy,locale})=>{await document.fonts.ready;await mountExplorer(copy,locale)}', {'copy': data['copy'], 'locale': locale})
            await page.wait_for_timeout(100)
            result = await page.evaluate(METRICS)
            if interactions:
                search = page.get_by_role('textbox', name='Search the expanded file tree', exact=True)
                await search.fill('目标')
                await page.wait_for_timeout(80)
                result['filtered'] = await page.evaluate("[...ownExplorer.root.querySelectorAll('button[data-item-path]')].map(row=>row.dataset.itemPath)")
                await search.fill('')
                await page.wait_for_timeout(80)
                row = page.get_by_role('treeitem', name='01-目标与范围.md', exact=True)
                await row.click()
                await row.press('ArrowDown')
                result['keyboardFocused'] = await page.evaluate('ownExplorer.tree.getFocusedPath()')
                await page.keyboard.press('Enter')
                await page.get_by_role('treeitem', name='deleted.md', exact=True).click(force=True)
                await page.get_by_role('treeitem', name='deleted.md', exact=True).focus()
                await page.keyboard.press('Enter')
                await page.evaluate("ownExplorer.setLocale('en');document.querySelector('.as-sidebar').style.width='320px'")
                await page.wait_for_timeout(80)
                result['wide'] = await page.evaluate(METRICS)
                await page.evaluate("ownExplorer.setLocale('zh-CN');document.querySelector('.as-sidebar').style.width='220px'")
                await page.wait_for_timeout(80)
                result['restored'] = await page.evaluate(METRICS)
            await page.screenshot(path=str(tmp_path/f'filepane-{width}-{locale}.png'))
            (tmp_path/'metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            await page.evaluate('ownExplorer.dispose()')
            return result
        finally:
            await asyncio.wait_for(browser.close(), timeout=8)


@pytest.mark.parametrize('locale', ['zh-CN', 'en'])
@pytest.mark.parametrize('width', [220, 320])
def test_minimum_pane_preserves_readable_names_and_single_layer_controls(fixture_data, tmp_path, width, locale):
    value = asyncio.run(asyncio.wait_for(run_fixture(fixture_data, width, locale, tmp_path), timeout=20))
    assert 43 <= value['folder']['height'] <= 45, value
    assert len(value['labelLines']) == 1, value
    assert value['labelLines'][0]['right'] <= value['folder']['right'], value
    assert 39 <= value['search']['height'] <= 41, value
    assert value['input']['padding'] == '0px', value
    assert value['input']['fontSize'] == '15px', value
    assert value['input']['placeholderWidth'] <= value['input']['width'], value
    names = {row['path']: row for row in value['rows']}
    for path in ['01-目标与范围.md', '03-验收与未决事项.md']:
        row = names[path]
        assert row['content']['width'] >= 105, value
        assert row['aria'] == path and row['title'].startswith(path), value
        assert '目标' in row['visible'] if path.startswith('01') else '验收' in row['visible'], value
    for row in value['rows']:
        assert row['gitDisplay'] in ('none', None), value
        assert 'EEAS Manrope' in row['font'] and row['fontSize'] == '13px', value
        assert row['right'] <= value['host']['right']+1, value
        if row['decoration'] and row['decoration']['text']:
            assert row['decoration']['width'] >= row['decoration']['textWidth']-.1, value
            assert row['decoration']['x'] >= row['content']['right'], value
    assert names['04-用户指定任务书模板.docx']['decoration']['text'] == '?', value
    assert '过程' in names['02-过程与约束.txt']['visible'], value
    assert names['02-过程与约束.txt']['color'] != names['01-目标与范围.md']['color'], value
    assert names['deleted.md']['disabled'] == 'true' and 'line-through' in names['deleted.md']['line'], value
    assert not value['events']['guarded'], value


def test_locale_keyboard_and_wide_narrow_roundtrip_keep_one_status_and_full_identity(fixture_data, tmp_path):
    value = asyncio.run(asyncio.wait_for(run_fixture(fixture_data, 220, 'zh-CN', tmp_path, interactions=True), timeout=25))
    assert value['filtered'] == ['01-目标与范围.md'], value
    assert value['keyboardFocused'] == '02-过程与约束.txt', value
    for state in [value['wide'], value['restored']]:
        assert not state['events']['guarded'], state
        assert 'deleted.md' not in state['events']['opened'], state
        assert '01-目标与范围.md' in state['events']['opened'], state
        assert state['search']['height'] == 40 and len(state['labelLines']) == 1, state
        assert all(row['gitDisplay'] in ('none', None) for row in state['rows']), state
    assert next(row for row in value['wide']['rows'] if row['path']=='delivery/')['decoration']['text'] == fixture_data['copy']['新增']['en']
    assert next(row for row in value['restored']['rows'] if row['path']=='delivery/')['decoration']['text'] == '新增'
    assert next(row for row in value['restored']['rows'] if row['path']=='01-目标与范围.md')['content']['width'] >= 105
