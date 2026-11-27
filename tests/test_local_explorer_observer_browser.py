"""Actual DOM observer regression for the current LocalExplorer functions.

The application API and FileTree renderer are explicit boundaries here; source
functions, source observer options, canOpen/changeLabel and Chromium DOM are
real. Whole built-App/real-filesystem and Windows qualification are separate.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess

import pytest
from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'
EXTRACT = r"""
const fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module'),{createHash}=require('node:crypto');
const root=process.argv[1],file=process.argv[2],ts=createRequire(path.join(root,'apps/agent-shell/package.json'))('typescript');
const source=fs.readFileSync(file,'utf8'),ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),parts=new Map();let options;
function walk(node){
 if(ts.isFunctionDeclaration(node)&&node.name&&['decoration','markRows','scheduleMarks'].includes(node.name.text))parts.set(node.name.text,node.getText(ast));
 if(ts.isCallExpression(node)&&ts.isPropertyAccessExpression(node.expression)&&node.expression.expression.getText(ast)==='mutation'&&node.expression.name.text==='observe')options=node.arguments[1].getText(ast);
 ts.forEachChild(node,walk);
}walk(ast);
if(parts.size!==3||!options)throw Error('LocalExplorer function/observer contract changed');
const explorer=path.join(root,'apps/agent-shell/src/explorer.ts'),text=fs.readFileSync(explorer,'utf8'),tree=ts.createSourceFile(explorer,text,ts.ScriptTarget.Latest,true);
function vars(node){if(ts.isVariableDeclaration(node)&&ts.isIdentifier(node.name)&&['canOpen','changeLabel'].includes(node.name.text))parts.set(node.name.text,'const '+node.getText(tree)+';');ts.forEachChild(node,vars);}vars(tree);
if(parts.size!==5)throw Error('Explorer semantic helpers changed');
const compiled=ts.transpileModule([...parts.values()].join(String.fromCharCode(10)),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext},reportDiagnostics:true});
if(compiled.diagnostics?.some(d=>d.category===ts.DiagnosticCategory.Error))throw Error('Source extraction did not compile cleanly');
const code=compiled.outputText;
console.log(JSON.stringify({sha256:createHash('sha256').update(source).digest('hex'),code,options}));
"""


@pytest.fixture(scope='module')
def source_probe():
    source = Path(os.environ.get('EEAS_EXPLORER_OBSERVER_SOURCE', ROOT/'apps/agent-shell/src/LocalExplorer.tsx'))
    result = subprocess.run([str(NODE), '-e', EXTRACT, str(ROOT), str(source)],
                            capture_output=True, text=True, check=True, timeout=20)
    copy = {}
    for namespace in ['renderer', 'renderer-agent']:
        copy.update(json.loads((ROOT/'packages/i18n'/f'{namespace}.json').read_text())['messages'])
    return {**json.loads(result.stdout), 'copy': copy}


async def run_dom(probe, status, transitions=False):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('<!doctype html><div id="own"></div>')
            result = await asyncio.wait_for(page.evaluate(r"""async ({code,options,copy,status,transitions})=>{
              const host=document.getElementById('own'),root=host.attachShadow({mode:'open'}),node=document.createElement('button'),part=document.createElement('span'),marker=document.createElement('span');
              node.dataset.itemPath='one.docx';part.dataset.itemSection='decoration';part.append(marker);node.append(part);root.append(node);
              let locale='zh-CN',callbacks=0,guarded=false;const mutations=[];
              const entries=new Map([['one.docx',{path:'one.docx',change:status,type:'file',readable:status!=='DELETED',deleted:status==='DELETED',verification:'CONTENT_SHA256'}]]);
              const store={entry:path=>entries.get(path)},tree={getFileTreeContainer:()=>host};
              const uiText=(key,params={})=>(copy[key]?.[locale]??key).replace(/\{([^{}]+)\}/g,(m,key)=>String(params[key]??m));
              const actual=new Function('tree','store','uiText','let markQueued=false;'+code+';return {markRows,scheduleMarks};')(tree,store,uiText);
              const started=performance.now();const observer=new MutationObserver(()=>{
                callbacks++;if(callbacks>=100000||performance.now()-started>500){guarded=true;observer.disconnect();return;}actual.scheduleMarks();
              });observer.observe(root,new Function('return '+options)());
              const audit=new MutationObserver(records=>mutations.push(...records));audit.observe(root,{childList:true,subtree:true,attributes:true});
              const heartbeat=new Promise(resolve=>setTimeout(()=>resolve(performance.now()-started),50));
              actual.markRows();const delay=await heartbeat;
              const initial={text:marker.textContent,disabled:node.getAttribute('aria-disabled'),title:node.title};
              mutations.length=0;for(let i=0;i<20;i++)actual.markRows();await new Promise(resolve=>setTimeout(resolve,20));
              const stableWrites=mutations.length;let changed=null;
              if(transitions&&!guarded){
                locale='en';actual.scheduleMarks();await new Promise(resolve=>setTimeout(resolve,20));
                const english=marker.textContent;
                entries.set('two.docx',{path:'two.docx',change:'ADDED',type:'file',readable:true,deleted:false,verification:'CONTENT_SHA256'});
                node.dataset.itemPath='two.docx';await new Promise(resolve=>setTimeout(resolve,20));
                changed={english,text:marker.textContent,title:node.title,disabled:node.getAttribute('aria-disabled')};
                mutations.length=0;actual.markRows();await new Promise(resolve=>setTimeout(resolve,20));changed.stableWrites=mutations.length;
              }
              observer.disconnect();audit.disconnect();return {status,callbacks,guarded,delay,initial,stableWrites,changed};
            }""", {**probe, 'status': status, 'transitions': transitions}), timeout=8)
            return result
        finally:
            await asyncio.wait_for(browser.close(), timeout=8)


@pytest.mark.parametrize('status', ['UNCHANGED', 'ADDED', 'MODIFIED', 'DELETED', 'UNVERIFIED'])
def test_rows_settle_and_identical_render_performs_no_dom_writes(source_probe, status):
    value = asyncio.run(run_dom(source_probe, status))
    assert not value['guarded'], value
    assert value['callbacks'] <= 2, value
    assert value['delay'] < 400, value
    assert value['stableWrites'] == 0, value
    assert value['initial']['disabled'] == ('true' if status == 'DELETED' else None), value
    expected = {'UNCHANGED': '', 'ADDED': '新增', 'MODIFIED': '修改', 'DELETED': '删除', 'UNVERIFIED': '?'}
    assert value['initial']['text'] == expected[status], value


def test_locale_and_recycled_row_keep_real_status_changes_and_restore_openability(source_probe):
    value = asyncio.run(run_dom(source_probe, 'DELETED', transitions=True))
    assert not value['guarded'], value
    assert value['initial']['disabled'] == 'true', value
    assert value['changed']['english'] == source_probe['copy']['删除']['en'], value
    assert value['changed']['text'] == source_probe['copy']['新增']['en'], value
    assert value['changed']['title'].startswith('two.docx'), value
    assert value['changed']['disabled'] is None, value
    assert value['changed']['stableWrites'] == 0, value
